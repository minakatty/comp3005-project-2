# DESIGN_LOG: Project 2

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

## 2026-11-11: First Module 1 kit and the measurement script

- **Trying to:** get the M1 measurement script running before the traces arrived.
- **AI was wrong (1): the measurement script crashed with `invalid literal for int(): 'Put'`.**
  The AI-generated kit had put a placeholder `README.txt` into `traces_published/`, and
  its own script then read every `.txt` in that folder as a trace, including the README.
  Found the first time I ran it; deleted the placeholder. The current script skips any
  file that is not a list of page ids, with a note.

## 2026-11-12: Module 1 rebuilt to the spec

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

## 2026-11-12: Module 2: memory budget and the operators

- **Trying to:** every operator under one budget of B frames.
- **Decision:** enforce the budget by construction: tuples an operator keeps live in
  frames reserved from the same buffer pool, so pinned + reserved can never exceed B
  (DESIGN_M2.md §2).
- **AI was wrong (2): hash distinct returned 200 rows instead of 100.** The hybrid
  hash table tested membership with `table.get(k) is not None`; for distinct the
  stored state *is* `None`, so every tuple looked like a new group once the table
  spilled. Found by running the same distinct in sort mode and hash mode on the same
  input and getting different counts; fixed to `k in table`.
- **AI was wrong (3): hash join got *more* expensive as B grew.** The cost-vs-formula
  table showed hash join at 563 page I/O for two 32-page inputs at B = 256, against a
  formula of 192. With P = B − 2 = 254 partitions each of the 64 input pages landed in
  a different, almost empty partition page. Fixed by building in memory when the build
  side fits (hybrid hashing); when it does not fit there are more pages than partitions,
  so the near-empty partitions mostly disappear.
- **AI was wrong (4): sort-merge join crashed at B = 16 and then got slower with more
  memory.** It kept the sorted left input in memory, which at B = 16 left the right
  sort one frame, fewer than a merge needs: `no unpinned frame available`, caught by
  `test_three_joins_same_bag_at_every_B`. After that fix the crossover table showed
  sort-merge costing 410 at B = 48 against 338 at B = 32: the left run fitted, so the
  right sort was squeezed into the 3 frames left over. Now the left run stays in
  memory only if the right input's page count also fits in what is left.
- **Broke:** the unoptimized plan for a three-table query at B = 4 needed 5 frames
  (three scans and two nested-loop blocks). Changed the allocation rule: when the
  share is 0, nested loop joins run tuple-at-a-time.

## 2026-11-12: Module 3: SQL front end

- **Trying to:** the seven translation examples exactly, node for node.
- **Decision:** bare table aliases (`FROM Emp e`) even though the grammar sketch
  requires `AS`, because the spec's own examples use them. Aggregates are allowed as
  operands syntactically and rejected in WHERE semantically, so the error can say
  *why* (GRAMMAR_SQL.md §6).
- **Decision:** `SELECT DISTINCT` over `GROUP BY` is dropped only when every grouping
  column is in the select list; `SELECT DISTINCT COUNT(*) ... GROUP BY d` keeps it,
  because two groups can have the same count (DESIGN_M3.md §1).

## 2026-11-12: Module 4: optimizer

- **AI was wrong (5): rule 5 never fired through a subquery.** For
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

## 2026-11-13: Running everything on Windows, and the missing course files

- **Trying to:** run the whole walkthrough in PowerShell on my own machine.
- **Broke:** I was still in the old project folder (130 tests, old `storage/schema.py`),
  so `sql.py`, `opt.py`, `exec.py` did not exist. Renamed it `project2_old`, unzipped the
  new code, copied `.git` across: 207 tests, OK, in 145 s (211 now, with the tests added below).
- **AI was wrong (6): plans saved with PowerShell's `>` could not be read.** The
  walkthrough said `python sql.py compile ... --json > q.json`, but Windows PowerShell's
  `>` writes UTF-16, and the tools opened files as UTF-8. Found when preparing the
  Windows instructions; fixed with one `read_text` helper (`storage/errors.py`) that
  every tool now uses, which also accepts a UTF-8 BOM. Checked by reading a UTF-16 plan
  and a BOM-prefixed SQL file.
- **Broke:** `git push` failed: the remote was the walkthrough's placeholder URL. Fixed with
  `git remote set-url origin` and pushed, with the `m1` tag.
- **Decision:** we were told the course traces, plan schema, sample plans and dataset won't be posted, so write our own and state each assumption in one line.
  Wrote `tools/make_traces.py` (five traces, trace1 is the §5.3 trace, trace3 the 80-page
  repeated scan, trace5 a hot set broken by scans), `samples/plans/` (one plan per
  operator in the spec's §2 JSON, plus three invalid ones), `samples/make_sample_db.py`
  and `samples/sql/`, and a test that runs every sample plan at B = 4 and B = 32
  (`tests/test_samples.py`). Re-ran the M1 measurements on my traces. One-line
  assumptions are in each DESIGN_M*.md.

## 2026-11-13 (evening): Counters aligned with the spec's formulas

- **Trying to:** get a second review of the whole repository before submitting.
- **AI was wrong (7): my sort and hash join counters did not match the spec.** The
  review pointed out that the sort used B − 1 frames for runs (the scan's page counted
  separately), so the spec's own example (N = 8, B = 3) gave 16 reads / 16 writes for
  the sort instead of the spec's 22 / 14, and the hash join used B − 2 partitions instead
  of B − 1. The professor grades counters against what the named algorithm should cost,
  so this mattered. Fixed: the scan's pinned page is the sort's B-th run frame, and
  joins of two tables use the frame of the scan that is idle. Now the §2.5 example gives
  exactly 22 reads (+1 directory page) and 14 writes, and the measured pass count equals
  the spec's formula in every case. Reran REPORT_M2 and REPORT_M4.
- **Broke:** the change made sort-merge keep the right input in memory while the left
  input's final merge also needed a frame per run, which overflowed B = 8
  (`test_hash_join_overflow_is_handled` runs sort-merge as its reference). Now the right
  run is written out whenever the left merge needs those frames.
- **Added:** a test that a query of the actual workload changes join algorithm with B
  (q15: hash join at B = 4, nested loop at B = 32), as the M4 checklist asks.
