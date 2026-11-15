# DESIGN_M4: Query optimizer

Code: `optimizer/`. `stats.py` (ANALYZE), `estimate.py` (selectivity and
cardinality), `logical.py` (columns, estimates and the rewrite rules), `physical.py`
(cost model, join enumeration, logical → physical), `optimize.py`, `cli.py`.

A logical plan comes in from Module 3; a physical plan goes out in exactly the JSON
Module 2 runs, unedited (`tests/test_m4.py::Live` executes every plan it produces and
compares the result with the unoptimized plan's).

## 1. Statistics and where they live

`opt analyze --table T` scans the table once for the table-level numbers, then sorts
each column with **Module 2's external sort** (a `sort` plan over `project` over
`seqscan`, run under the database's buffer pool, so ANALYZE never holds a column in
memory beyond its frames) and computes, exactly, no sampling:

| statistic | meaning |
|---|---|
| `n_tuples` | live tuples |
| `n_pages` | data pages, directory pages excluded (from the catalog) |
| `width` | average encoded record length in bytes |
| per column `ndv`, `min`, `max` | distinct values, lowest, highest |
| per column `avg_width` | average encoded bytes of that column |
| per column `hist` | equi-depth histogram, k buckets (`--buckets`, default 20), each `{lo, hi, count, ndv}` |

Equi-depth: the sorted column is cut at every ceil(n / k)-th position, so every
bucket holds the same number of tuples (± the remainder), and buckets are narrow
where data is dense (`test_histogram_is_equi_depth`). A value that straddles a cut
appears in two buckets; selectivity adds up its share in each.

**Catalog layout.** The statistics are one JSON object stored in `catalog.db` as a
`kind = "stats"` entry, next to the table's `kind = "table"` entry, chunked over
records exactly like the table entries (DESIGN_M1.md 6). No change to the Module 1
page format was needed. ANALYZE is deterministic, so running it twice writes
byte-identical statistics (`test_analyze_twice_changes_nothing`).

**No statistics.** If a table was never analysed the optimizer still plans, from the
catalog's own `num_tuples` and `num_pages` plus documented defaults: equality 0.1,
range 1/3, other join predicates 0.1, column widths INT 4 / VARCHAR(n) 2 + n/2
(`test_no_statistics_still_plans`).

## 2. Selectivity formulas, and the assumption behind each

| predicate | what I implemented | assumption |
|---|---|---|
| `c = v` | Σ over buckets with lo ≤ v ≤ hi of count(b) / (n × ndv(b)); no histogram: 1/ndv(c); no stats: 0.1 | values uniform *inside* a bucket |
| `c < v`, `c <= v` | whole buckets with hi < v, plus (v − lo)/(hi − lo) of the bucket holding v (strings: half of it); no histogram: (v − min)/(max − min); no stats: 1/3 | continuous, uniform inside a bucket; the mass of v itself is not split out, so `<` and `<=` estimate the same |
| `c > v`, `c >= v` | 1 − sel(c < v) | as above |
| `c <> v` | 1 − sel(c = v) | |
| `p AND q` | sel(p) × sel(q) | **independence** (the one Section 7 breaks) |
| `p OR q` | sel(p) + sel(q) − sel(p) sel(q) | independence |
| `NOT p` | 1 − sel(p) | |
| `R.a = S.b` | 1 / max(ndv(R.a), ndv(S.b)), so rows = n(R) n(S) / max(…) | containment: every value of the smaller side appears in the larger |
| other `R.a op S.b` | 0.1 (stated here, not hidden) | none, it is a guess |

Two refinements over the defaults, both because the default gave a visibly wrong
number: an `ndv` is capped at the row count of the input it is read from (after a
selective filter there cannot be more distinct values than rows), and a value
outside every bucket of a histogram gets 1 / n (at most one row) instead of 0.

Output rows = ceil(input rows × selectivity), never below 1. Output pages =
ceil(rows × width / 4096), with the width of only the columns that survive a
projection, which is how a pushed-down projection makes later joins cheaper. A
base table's pages are its real page count.

The M4 spec's worked example reproduces (`test_section_2_worked_example`):
`did = 7` → 200 rows, `age > 60` → 2000, both → 40, Emp ⋈ Dept on did → 10 000.

## 3. Rewrite rules and their preconditions

Applied to the logical plan before costing, in `logical.rewrite`: rule 1 once, then
rules 2–5 and 3 to a fixed point, then 6, then 7. Each can be turned off
(`--disable 2,6`) for the "which rule mattered" measurement.

| # | rule | precondition I check |
|---|---|---|
| 1 | split `select[p and q]` into stacked selects | none |
| 2 | push a select below `join` / `cross` to one side | every column of the predicate resolves in that side's output |
| 3 | `select` (stack) directly over `cross` → `join` | the predicate is a column = column equality with one column from each side |
| 4 | push a select below a subquery `rename`, below `union` / `union_all` (into both branches), and below a `project` | column names are rewritten through the rename / projection / the right branch's names by position |
| 5 | push a select below `group_by` | the predicate references grouping columns only; a predicate on an aggregate never moves |
| 6 | push projections down | above each input of a join, keep only the columns the ancestors and the join's own predicates need; never between two joins |
| 7 | remove a project that keeps every column in order; collapse adjacent selects into one | the project has no `as` aliases |

Selects commute, so a select stuck under another select is tried one level deeper
too (that is what lets `t.did = 3` reach below a `group_by` while `t.n > 2` stops
above it, `test_rule5_...`).

**A rename directly over a scan is an access path**, not a subquery: it becomes a
`seqscan` with an `alias`, and selects stop above it, exactly as in the spec's
worked example. The worked example reproduces node for node
(`RewriteExample.test_section_3_worked_example`):

```
before                                  after
project [e.name]                        project [e.name]
  select (e.did = d.did and               join (e.did = d.did)
          e.age > 60 and                    project [e.name, e.did]
          d.budget > 1000)                    select (e.age > 60)
    cross                                       rename e (scan Emp)
      rename e (scan Emp)                   project [d.did]
      rename d (scan Dept)                    select (d.budget > 1000)
                                                rename d (scan Dept)
```

(`opt explain --rewrites` prints both trees, with estimated rows, for any query.)

## 4. Cost model

Page I/O only (reads + writes), as a function of B. M, N are estimated input pages.
The formulas are Module 2's:

```
seqscan              M
filter, project      0
sort                 2·M·(passes − 1) + M,   passes = 1 + ceil(log(ceil(M/B)) / log(B − 1))
distinct, group_by   sort mode: the sort's cost beyond reading its input
                     hash mode: 0 if the input fits in B − 1 frames, else 2·M
                     (the cheaper is chosen, and named in the plan's "mode")
nested_loop_join     M + ceil(M / (B − 2)) · N
sort_merge_join      sort(M) + sort(N) + M + N
hash_join            3 · (M + N)
limit                0
```

**Accounting inside a join tree**, following the spec's DP example: a base input's M
(or N) term *is* its scan, so its seqscan cost is not added a second time; a
composite outer adds its own cost plus M to read it. The inner of a nested loop is
re-executed once per outer block, so its N is the inner's whole cost (a filtered
scan's base pages, not its output pages).

**Where the model and Module 2 knowingly disagree** (REPORT_M4.md compares
predicted and actual): Module 2 builds a hash join in memory when the build side
fits (cost M + N, not 3(M + N)), and every scan also reads its directory page. I kept the model on the spec's formulas because the
spec's worked examples are stated in them and the DP table must reproduce; the
disagreement is measured rather than hidden.

## 5. Join enumeration

Selinger-style dynamic programming over **left-deep** trees (`Planner.join_block`):

1. A *join block* is a maximal tree of `join`, `cross` and the `select`s inside it.
   Its leaves (base relations with their pushed selects and projections, or planned
   subqueries) are planned on their own, and every join condition and in-block select
   is split into conjuncts, each tagged with the set of leaves it references.
2. `best[{r}]` = the leaf's access path.
3. For k = 2 … n, for every subset S of size k, for every r in S with best[S − {r}]
   known, for each algorithm: cost(best[S − {r}] ⋈ r). The predicates applied are those
   that reference r and lie inside S. Hash and sort-merge are offered only when all of
   them are column = column equalities; nested loop always.
4. A subset whose join graph is not connected is **skipped** (a cross product), unless
   the whole query graph is disconnected, in which case cross products are allowed.
5. Predicates no join used (single-leaf predicates left above the block when rule 2 is
   off, constants) become a filter on top.
6. A new join order also reorders the columns. If nothing above the block sets the order
   (a project or group_by), a project on top puts them back in the logical plan's order,
   so `SELECT *` always lists columns in FROM order.

Join conditions are written in sorted order, so a query written with commas and the
same query written with JOIN ON produce byte-identical physical plans
(`test_commas_and_join_on_give_the_same_physical_plan`).

**What left-deep buys.** The search space: n! orders × algorithms instead of the
(2n − 2)! / (n − 1)! bushy trees, and DP over subsets is O(2ⁿ · n) entries, trivial
for six relations. And pipelining: the outer side of every join is the previous
join's output, streamed tuple by tuple, never materialised; only the inner, a base
relation, is rescanned, and a base relation can always be rescanned from disk.
**What it gives up.** Plans that join two independent pairs first and then join the
two small results (A ⋈ B) ⋈ (C ⋈ D): with two large tables each filtered by a small
one, a bushy tree can keep both intermediate results small, while a left-deep tree has
to carry one big intermediate result into every later join. It also cannot put a
composite result on the inner side, where a hash join would want the smaller input.

### 5.1 The spec's worked example reproduced

`tests/test_m4.py::DPExample` loads exactly the spec's statistics (A 100 pages /
5 000 tuples, B′ 10 / 500, C 400 / 20 000; join outputs 1 000 rows = 20 pages and
2 000 rows = 40 pages) at B = 32 and checks: {A} 100, {B′} 10, {C} 400,
{A,B′} 110 (B′ outer, BNL with A inner, 10 + 1×100), {B′,C} 410, {A,C} skipped,
{A,B′,C} 530 ({A,B′} outer, 110 + 20 + 1×400), chosen plan B′ ⋈ A then ⋈ C.

One cell differs, and the formula is on my side: the spec prints the alternative
"{B′,C} outer (40 pages), BNL with A inner" as 410 + 40 + 100 = 550, but 40 pages do
not fit in one block of B − 2 = 30 frames, so the formula gives
410 + 40 + ceil(40/30) × 100 = **650**. The winner is the same.

### 5.2 The DP table for one four-relation join, in full

Query 16 of my workload (`python opt.py dp-table --db db_m4 --sql workload_m4/q16.sql --frames 16`).

```
SELECT o.oid, p.pname FROM Orders o JOIN Emp e ON o.eid = e.eid
  JOIN Dept d ON e.did = d.did JOIN Proj p ON p.did = d.did
 WHERE o.amount > 990 AND p.pid < 20;     B = 16
```

```
subset                       cost  plan considered
{o}                           167  * access path for o: project [o.oid, o.eid]
{e}                           125  * access path for e: project [e.eid, e.did]
{d}                             1  * access path for d: project [d.did]
{p}                             3  * access path for p: project [p.did, p.pname]
{o,e}                         292  * nested_loop_join outer=e (8p) inner=o
                              292    nested_loop_join outer=o (1p) inner=e
                              301    sort_merge_join outer=e (8p) inner=o
                              301    sort_merge_join outer=o (1p) inner=e
                              310    hash_join outer=e (8p) inner=o
                              310    hash_join outer=o (1p) inner=e
{o,d}                          -  no predicate inside this subset, skipped
{o,p}                          -  no predicate inside this subset, skipped
{e,d}                         126  * nested_loop_join outer=d (1p) inner=e
                              126    nested_loop_join outer=e (8p) inner=d
                              135    sort_merge_join outer=d (1p) inner=e
                              135    sort_merge_join outer=e (8p) inner=d
                              144    hash_join outer=d (1p) inner=e
                              144    hash_join outer=e (8p) inner=d
{e,p}                          -  no predicate inside this subset, skipped
{d,p}                           4  * nested_loop_join outer=p (1p) inner=d
                                4    nested_loop_join outer=d (1p) inner=p
                                6    sort_merge_join outer=p (1p) inner=d
                                6    sort_merge_join outer=d (1p) inner=p
                                8    hash_join outer=p (1p) inner=d
                                8    hash_join outer=d (1p) inner=p
{o,e,d}                       294  * nested_loop_join outer={e,o} (1p) inner=d
                              296    sort_merge_join outer={e,o} (1p) inner=d
                              298    hash_join outer={e,o} (1p) inner=d
                              305    nested_loop_join outer={d,e} (12p) inner=o
                              318    sort_merge_join outer={d,e} (12p) inner=o
                              331    hash_join outer={d,e} (12p) inner=o
{o,e,p}                        -  no predicate inside this subset, skipped
{o,d,p}                        -  no predicate inside this subset, skipped
{e,d,p}                       130  * nested_loop_join outer={p,d} (1p) inner=e
                              139    sort_merge_join outer={p,d} (1p) inner=e
                              141    nested_loop_join outer={d,e} (12p) inner=p
                              148    hash_join outer={p,d} (1p) inner=e
                              154    sort_merge_join outer={d,e} (12p) inner=p
                              167    hash_join outer={d,e} (12p) inner=p
{o,e,d,p}                     298  * nested_loop_join outer={{e,o},d} (1p) inner=p
                              300    sort_merge_join outer={{e,o},d} (1p) inner=p
                              302    hash_join outer={{e,o},d} (1p) inner=p
                              307    nested_loop_join outer={{p,d},e} (10p) inner=o
                              318    sort_merge_join outer={{p,d},e} (10p) inner=o
                              329    hash_join outer={{p,d},e} (10p) inner=o
```

The rows marked `*` are best[S]. Reading it: the cheapest pair is d–p (both tiny);
but the cheapest *complete* plan starts from {o,e} because the filter on o (31
estimated rows, 1 page) makes o ⋈ e produce one page, after which d and p each cost a
single scan. Subsets with no join predicate, such as {o,d}, are skipped. The chosen
plan:

```
project [o.oid, p.pname]                             est_rows=13        est_cost=298
  nested_loop_join (p.did = d.did)                   est_rows=13        est_cost=298
    nested_loop_join (e.did = d.did)                 est_rows=31        est_cost=294
      nested_loop_join (o.eid = e.eid)               est_rows=31        est_cost=292
        project [e.eid, e.did]                       est_rows=4000      est_cost=125
          seqscan Emp as e                           est_rows=4000      est_cost=125
        project [o.oid, o.eid]                       est_rows=31        est_cost=167
          filter (o.amount > 990)                    est_rows=31        est_cost=167
            seqscan Orders as o                      est_rows=3000      est_cost=167
      project [d.did]                                est_rows=50        est_cost=1
        seqscan Dept as d                            est_rows=50        est_cost=1
    project [p.did, p.pname]                         est_rows=20        est_cost=3
      filter (p.pid < 20)                            est_rows=20        est_cost=3
        seqscan Proj as p                            est_rows=400       est_cost=3
```

## 6. Assumptions (one line each)

- Dataset and workload: not published, so they are my own (`tools/make_m4_dataset.py`: Emp, Dept, Orders, Proj, 20 queries in `workload_m4/`), with two correlations planted on purpose (city → state → region one-to-one; salary rising with age).
- A predicate between two columns that is not an equality has selectivity 0.1.

## 7. Sources

- **Selinger, Astrahan, Chamberlin, Lorie and Price, "Access Path Selection in a
  Relational Database Management System", SIGMOD 1979.** The DP over subsets, keeping
  one best plan per subset, restricting to left-deep trees, and postponing cross
  products until no joined alternative exists are all from this paper. It changed my
  enumerator: my first version considered cross products for every subset, which is why
  {A, C} appeared in my DP table before I added the connectivity check.
- **Leis, Gubichev, Mirchev, Boncz, Kemper and Neumann, "How Good Are Query
  Optimizers, Really?", VLDB 2015.** Their finding that estimation errors grow
  multiplicatively with each join and come mostly from independence on correlated
  columns is what I designed REPORT_M4 §4 around: I planted a three-column
  functional dependency so independence would be wrong by a known factor (10× per extra
  predicate) and measured the plan it produced.
