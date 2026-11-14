# ARCHITECTURE

## The whole system

```
  SQL text
     |
     v
  [ M3 sqlfront ]  lexer -> parser -> scope / resolution / types / aggregates -> translation
     |
     |  logical plan JSON   (scan rename select project join cross group_by distinct
     |                       sort limit union union_all intersect except)
     v
  [ M4 optimizer ] rewrites 1-7 -> statistics -> selectivity -> cost model
     |                         -> DP over left-deep join orders -> algorithm per join
     |
     |  physical plan JSON  (seqscan filter project rename distinct sort group_by
     |                       nested_loop_join sort_merge_join hash_join set ops limit)
     v
  [ M2 execution ] validate -> build operator tree (resolves names, types) -> allocate B
     |              -> open / next / close, counters per operator
     |
     |  page requests: fetch_page / unpin_page / new_page / reserve / write_through
     v
  [ M1 storage ]   buffer pool (B frames, LRU | CLOCK) -> heap files -> 4 KB pages
                    ^
                    |  catalog.db: table entries (M1) and statistics entries (M4)
                    +---- read by M3 (schemas) and M4 (statistics)

  [ ralang ]  the Bonus 1 engine as a library: the condition parser and evaluator
              (ralang/condition.py) is used by M2, M3 and M4, and plan_json's
              unparse_condition writes every condition string in every plan.
```

`dbms.py` wires the four together and adds EXPLAIN, EXPLAIN ANALYZE, ANALYZE,
SET frames and LOAD. It contains no planning or execution logic of its own: it calls
`compile_sql`, `optimize` and `execute`, the same functions the module tools call.

## The interface between each pair of modules

| from → to | interface | format | what crosses it |
|---|---|---|---|
| M3 → M4 | logical plan | JSON (M3 spec §5, plus `project.as`, named aggregates, `limit.n`) | what to compute; every column fully qualified; no physical names; predicates where SQL put them |
| M4 → M2 | physical plan | JSON (M2 plan schema, `execution/plan.py`) | how to compute it; algorithm per join, mode per distinct/group_by, aliases on seqscans |
| M2 → M1 | page access API | Python calls (`BufferPool`, `HeapFile.scan`, `Database.new_temp`) | page bytes; every read and write counted and attributed to the calling operator |
| M3 → M1 | catalog | `Catalog.get_table` | table schemas for resolution and type checking |
| M4 → M1 | catalog | `Catalog.get_stats` / `put_stats` | statistics; table counts when a table was never analysed |
| M2, M3, M4 → ralang | condition library | `parse_condition`, `Condition`, `unparse_condition` | condition strings ⇄ trees, type checks, evaluation |

Two properties of these contracts made the modules independent:

1. **Conditions are strings in one language.** M3 writes them, M4 rewrites them, M2
   evaluates them, all through the Bonus 1 parser. Nothing passes a Python object
   across a module boundary except through JSON.
2. **Every plan node carries enough to resolve names without the producer.** Columns
   are qualified (`e.name`) and renames are explicit, so M2 can check a plan from any
   source (mine, the professor's sample plans) before reading a page.

## What I would do differently if I started again

- **A typed plan object instead of raw dicts between M3 and M4.** Rewrites over
  nested dictionaries made every rule re-derive column lists from scratch
  (`logical.props` is called again and again). A small class per logical node, with
  its output schema computed once, would have made the rules shorter and faster, and
  JSON would remain only the external contract.
- **Memory accounting in the buffer pool from day one.** The decision that working
  memory is *reserved frames of the same pool* is what made the budget provable; it
  came after M1 was written. Designing M1's pool with reservations in mind would have
  avoided the `write_through` and `reserve` additions.
- **The optimizer's cost model should be the executor's.** M4 uses the spec's
  formulas so the worked examples reproduce, while M2 does better in places (hash
  join in memory, sort with B − 1 run frames). One shared module of cost functions,
  used by both and tested against measured counters, would remove that whole class of
  predicted-vs-actual gap.
- **Bushy trees or at least a greedy fallback beyond ten relations**, and
  multi-column statistics for the correlated columns the workload is designed to
  break (REPORT_M4.md §4).
