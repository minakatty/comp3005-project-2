# DESIGN_M3 — SQL front end

Code: `sqlfront/`. `lexer.py` and `parser.py` (hand-written, no regular
expressions), `sqlast.py` (the syntax tree and `--explain` printer), `translate.py`
(scope, name resolution, type and aggregate checks, translation), `compiler.py`
(glue against a Module 1 catalog), `cli.py`.

## 1. Where duplicate elimination happens (read this first)

SQL works on bags. My translator eliminates duplicates in exactly four places, and
nowhere else:

| SQL | plan node | why it deduplicates |
|---|---|---|
| `SELECT DISTINCT` | `distinct` directly above the block's `project` | the user asked for a set |
| `UNION` | `union` | SQL's UNION is a set operation; `UNION ALL` emits `union_all`, which does not deduplicate |
| `INTERSECT`, `EXCEPT` | `intersect`, `except` | set semantics in SQL even when the inputs have duplicates |
| `GROUP BY` | `group_by` | one output row per distinct group, by construction |

What I deliberately do **not** do:

- **project never deduplicates**, and no `distinct` is emitted just because a
  projection dropped columns (`test_distinct_above_projection_only_when_written`).
  This is the single biggest change from Bonus 1, where `project` was a set operation.
- **No `distinct` above a `group_by` that makes it redundant.** If every grouping
  column is in the select list, the group_by already produced one row per output
  value, so `SELECT DISTINCT did, COUNT(*) ... GROUP BY did` gets no `distinct`
  node. But `SELECT DISTINCT COUNT(*) FROM Emp GROUP BY did` *keeps* it: two groups
  can have the same count, and dropping the `distinct` would return duplicate
  counts. The spec says "adding DISTINCT above it is redundant, do not emit it"; I
  apply that to the case where it is actually true
  (`test_distinct_over_group_by_is_not_emitted`).

**Why `SELECT DISTINCT` is not free.** Every other node in a block (scan, select,
project) is pipelined and keeps one tuple at a time. `distinct` has to know whether
it has seen a tuple before, which means sorting the whole input or hashing it into
a table that spills to disk when it outgrows memory (Module 2: an external sort, or
a hash table with partitions). For a large result the difference between
`union_all` and `union` is a sort of the whole result.

## 2. Scope and resolution algorithm

`translate.Scope` is built from the FROM clause, one entry per table reference, in
FROM order:

- a base table without alias: key = the table name, columns `Table.col`;
- a base table with alias: key = the alias, columns `alias.col`, and the plan gets a
  `rename` over the `scan`;
- a subquery `( ... ) AS t`: translated on its own first, then its *output* columns
  become `t.<last part of each name>`, and the plan gets a `rename to t`. The outer
  query sees `t.n` and nothing else.

Two entries with the same key is a *name* error: `FROM Emp, Emp` fails,
`FROM Emp e, Emp m` is fine (case 12).

Each column reference is then resolved, in this order:

1. **Qualified `t.c`.** `t` must be a scope key, else "unknown table or alias 't'";
   `c` must be a column of that entry, else "table 't' has no column 'c'". The two
   failures have different messages.
2. **Unqualified `c`.** Collect every scope column whose name is `c`. None: "unknown
   column 'c'". More than one: an *ambiguity* error naming the candidates,
   "column 'did' is ambiguous: it could be Emp.did or Dept.did" (case 3). Exactly one:
   that column.
3. **`SELECT *`** expands to every column of every scope entry, FROM order then
   schema order, each fully qualified, so shared names are not an error (case 2).
4. **ORDER BY** is resolved first against the output names of the select list (an
   `AS` alias, or an aggregate's output name such as `n`), then against the FROM
   scope. A FROM column that is not in the output is allowed (case 9): it is carried
   through an extended projection, the sort uses it, and a final projection drops it
   (`project [name] / sort [Emp.age desc] / project [name, age]`). With
   `SELECT DISTINCT` that would change the meaning of the distinct, so it is an error,
   as in PostgreSQL.
5. **ON conditions** are resolved against the tables joined so far.

After this pass every column in the plan is fully qualified (`Emp.age`, `e.name`).
The only bare names are aggregate outputs (`n`, `count_star`), which the spec's own
example 5.4 shows bare, and select-list `AS` aliases.

**Types.** INT and VARCHAR only. Any comparison of an INT with a VARCHAR, equality
included, is a *type* error (case 10). SUM and AVG need INT; COUNT accepts anything;
MIN and MAX either type. Set operators need the same column count and the same type
position by position, else a *schema* error (case 14). INSERT values are checked
against the table's types and VARCHAR lengths.

## 3. Aggregation

A block is *aggregating* if it has GROUP BY or an aggregate in its select list. Then:

- the select list may hold only grouping columns and aggregates; a bare column that
  is not a grouping column is an *aggregate* error naming it (case 6);
- HAVING may reference only grouping columns and aggregates;
- an aggregate in WHERE (or ON) is always an *aggregate* error, because WHERE runs
  before grouping (case 7);
- `SELECT *` in an aggregating block is an error.

Every distinct aggregate in the select list and in HAVING becomes one entry of the
`group_by` node; an aggregate used twice (once in the select list with `AS n`, once
in HAVING) is computed once, and HAVING refers to it by its output name: example 5.4
gives `select "n > 5"` above the `group_by`.

**Aggregate naming rule.** The `AS` alias if there is one; otherwise
`<function>_star` for `COUNT(*)` and `<function>_<Table>_<column>` with the dot
replaced by `_`: `count_star`, `avg_Emp_salary`, `max_e_name`. This is the same rule
Module 2 applies to a group_by aggregate without a name, so a plan with or without
explicit names produces the same output schema (`test_aggregate_naming_rule`).

`SELECT COUNT(*) FROM Emp` is a `group_by` with no keys; Module 2 returns one row
(0) even when Emp is empty (case 13).

## 4. Translation order

Exactly the canonical order of M3 Section 5, bottom to top: FROM (scans, renames,
then `cross` for commas or `join` for JOIN ... ON, left to right), WHERE (one
`select` with the whole condition), `group_by`, HAVING `select`, `project`,
`distinct`, `sort`, `limit`, then set operations between whole blocks, left
associative. Nothing is moved: the WHERE predicate stays above the join even when it
touches one side only (example 5.2), and a comma join stays `cross` + `select`
(example 5.3). Both are Module 4's job.

Conditions are emitted as strings in the Bonus 1 condition syntax
(`ralang.plan_json.unparse_condition`), so Module 2 parses them with the Bonus 1
parser: `and`/`or`/`not` lower case, `!=` for `<>`, strings in single quotes with
`''` for a quote.

Extra fields beyond the spec's node shapes: `project.as` (select-list aliases), the
third element of each `group_by` aggregate (its output name), `limit.n`, and the
`create_table` / `insert` statement nodes. All seven examples of Section 5 reproduce
with the same shape (`tests/test_m3.py::SpecExamples`), and no plan contains a
physical operator name (`assertClean`).

## 5. Sources

*Write these yourself.* Candidates: Nystrom, *Crafting Interpreters* ("Parsing
Expressions", "Statements and State") for growing a recursive-descent parser to a
larger grammar; the PostgreSQL documentation on SELECT, for ORDER BY on a column not
in the select list and the DISTINCT restriction in Section 2.4; Ramakrishnan & Gehrke
ch. 5 for SQL's bag semantics.
