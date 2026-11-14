# DESIGN_LOG — Project 2

> **Before submitting:** merge these entries with your own earlier Project 2 log
> entries (the M1 sessions), keep the real dates of your own sessions, and rewrite each
> entry in your own words. Every entry below describes something that actually
> happened while this code was built with AI assistance on 2026-11-12; the "AI was
> wrong" items are real and each one names the test or measurement that exposed it.

## Week one: engine refactor (handout §1.3)

- **What changed.** `ralang/` is now a library: tokenizer, parser, AST and operators
  import independently and print nothing (`tests/test_week1.py::Hygiene`). The
  condition parser is exposed on its own (`ralang/parser.parse_condition`, and
  `ralang/condition.py`, which every later module uses to type-check and evaluate a
  condition against a schema). Plans round-trip through JSON (`ralang/plan_json.py`).
  Operators read from a provider instead of an in-memory dict.
- **Sets to bags.** `project` keeps duplicates; duplicate removal is a separate
  `distinct` node (soft keyword `distinct(`). **Tests that changed meaning:** case 23,
  `project[DID](Employees)`, now returns three tuples; the test is now
  `distinct(project[DID](Employees))`, which returns the required two. All other 24
  Bonus 1 cases pass unchanged.
- **Tokenizer.** Added `<>` as a second spelling of not-equal, so condition strings
  written by Module 3 parse with the Bonus 1 parser unchanged.

## 2026-11-12 — Module 1 rebuilt to the spec

- **Trying to:** storage engine exactly to M1 spec: slotted pages, directory heap
  file, LRU and CLOCK, catalog in pages, CLI with the required JSON shapes.
- **Tried:** wrote the page, heap, pool and catalog, then checked the two worked
  examples first (3.2 page table, 5.3 trace) before anything else.
- **Broke:** `tests/test_week1.py::test_storage_provider_over_the_m1_engine` was written
  against an earlier M1 API (`catalog.create_table(name, "a:INT,...")` and a heap with
  no directory page). Updated the test to the public API
  (`db.create_table(name, parse_schema(...))`).
- **Decision:** compaction on insert only, when the page's dead space would make the
  record fit (DESIGN_M1.md §4). Without it, every catalog update left a dead 150-byte
  record behind and the catalog grew without bound, which I noticed in the
  catalog page dump (`slot 0 (0,0) dead`).

## 2026-11-12 — Module 2: memory budget and the operators

- **Trying to:** every operator under one budget of B frames.
- **Decision:** enforce the budget by construction: tuples an operator keeps live in
  frames reserved from the same buffer pool, so pinned + reserved can never exceed B
  (DESIGN_M2.md §2).
- **AI was wrong (1): hash distinct returned 200 rows instead of 100.** The hybrid
  hash table tested membership with `table.get(k) is not None`; for distinct the
  stored state *is* `None`, so every tuple looked like a new group once the table
  spilled. Found by running the same distinct in sort mode and hash mode on the same
  input and getting different counts; fixed to `k in table`.
- **AI was wrong (2): hash join got *more* expensive as B grew.** The cost-vs-formula
  table showed hash join at 563 page I/O for two 32-page inputs at B = 256, against a
  formula of 192. With P = B − 2 = 254 partitions each of the 64 input pages landed in
  a different, almost empty partition page. Fixed two ways: build in memory when the
  build side fits, and otherwise choose P from the build table's catalog page count.
- **AI was wrong (3): sort-merge join crashed at B = 16 and then got slower with more
  memory.** It kept the sorted left input in memory, which at B = 16 left the right
  sort one frame, fewer than a merge needs: `no unpinned frame available`, caught by
  `test_three_joins_same_bag_at_every_B`. After that fix the crossover table showed
  sort-merge costing 410 at B = 48 against 338 at B = 32: the left run fitted, so the
  right sort was squeezed into the 3 frames left over. Now the left run stays in
  memory only if the right input's page count also fits in what is left.
- **Broke:** the unoptimized plan for a three-table query at B = 4 needed 5 frames
  (three scans and two nested-loop blocks). Changed the allocation rule: when the
  share is 0, nested loop joins run tuple-at-a-time.

## 2026-11-12 — Module 3: SQL front end

- **Trying to:** the seven translation examples exactly, node for node.
- **Decision:** bare table aliases (`FROM Emp e`) even though the grammar sketch
  requires `AS`, because the spec's own examples use them. Aggregates are allowed as
  operands syntactically and rejected in WHERE semantically, so the error can say
  *why* (GRAMMAR_SQL.md §6).
- **Decision:** `SELECT DISTINCT` over `GROUP BY` is dropped only when every grouping
  column is in the select list; `SELECT DISTINCT COUNT(*) ... GROUP BY d` keeps it,
  because two groups can have the same count (DESIGN_M3.md §1).

## 2026-11-12 — Module 4: optimizer

- **AI was wrong (4): rule 5 never fired through a subquery.** For
  `... FROM (SELECT did, COUNT(*) AS n ... GROUP BY did) AS t WHERE t.did = 3 AND t.n > 2`
  rule 1 split the predicate into two stacked selects; the one on `t.n` stopped above
  the group_by (correct) and the one on `t.did` then sat on top of it and could not
  move, because the push only looked at its direct child. Found by
  `test_rule5_where_on_group_key_moves_below_group_by_but_having_never`. Fixed by
  letting a select move below another select (selects commute).
- **The spec's DP example has an arithmetic slip.** The alternative "{B′,C} outer
  (40 pages), BNL with A inner" is printed as 410 + 40 + 100 = 550, but 40 pages need
  ceil(40/30) = 2 blocks at B = 32, so the formula gives 650. Every other cell of
  the table reproduces; the chosen plan is the same. Documented in DESIGN_M4.md §5.1
  and asserted in `DPExample`.
- **Broke:** "changing B changes the join algorithm" was false on Emp ⋈ Proj (Proj is
  so small that nested loop always wins); the test now uses a self join of Emp, where
  hash join wins at B = 4 and nested loop at B = 32.
