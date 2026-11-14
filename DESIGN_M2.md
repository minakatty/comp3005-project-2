# DESIGN_M2: Execution engine

Code: `execution/`. `operators.py` holds every iterator, `memory.py` the budget,
temporary files and the external sort, `plan.py` the plan schema, construction and
frame allocation, `engine.py` the driver.

## 1. The iterator contract

Every operator implements `open()`, `next() -> tuple | None` (None is end of
stream), `close()` and `counters() -> {tuples_out, comparisons, page_reads, page_writes}`.
An operator reaches its input only through its children's `next()`, and the disk
only through Module 1: heap scans, temp files and the buffer pool.

**Counter attribution.** All DiskManagers of a database share one `IOStats`. Each
`open/next/close` of an operator pushes that operator as the stats' *sink* and pops it
afterwards, so every `read_page` / `write_page` increments the counters of the
operator that was running when it happened. A child's reads land on the child, a
sort's run files on the sort. Totals are the sum over all operators.
`comparisons` follow M2 spec 1.4: filter counts one per tuple examined, joins count
every pair or key comparison, sorts count every key comparison in the run sort and
the merge, hash tables count one per probe.

**Validation before work.** `plan.validate` checks the JSON shape and names the
offending node (`Node 'sort' (at root.child): missing required field 'keys'.`).
`plan.build` then constructs the whole operator tree, which resolves every table and
column and type-checks every condition through `ralang.condition.Condition`, the
Bonus 1 condition parser. All of that happens before `open`, so an unknown table or
column is rejected with zero page reads (`test_unknown_table_and_column_rejected_before_any_io`).

**Nothing is left behind.** After `close` every pinned page is unpinned, every
reserved frame released and every temp file deleted. Every M2 test asserts all three
(`tests/test_m2.py::Base.run_plan`).

## 2. The memory budget

**Enforcement by construction.** The plan runs over one buffer pool of exactly B
frames. Pages an operator reads are *pinned* in that pool; tuples an operator keeps
live in frames it has *reserved* from the same pool (`BufferPool.reserve`). A
reserved frame can never be a victim, so pinned + reserved can never exceed B: an
operator that asked for more would get `memory budget exceeded`, never extra memory.
`BufferPool.peak_held` records the largest number of frames held at once, which is
how REPORT_M2.md's budget measurement "knows" the number.

**Tuples are measured in pages.** "k frames of tuples" means: the tuples, packed onto
pages exactly as a slotted page would store them (encoded length + 4-byte slot,
4088 usable bytes per page), occupy at most k pages (`memory.PagePacker`,
`WorkArea.try_add`). A hash table or sort run that would need page k + 1 is full.
So no Python list ever holds more tuples than its reserved frames could.

**The rule for dividing B.** (`plan.allocate`)

```
S = number of seqscan nodes          each is pipelined and pins one frame
K = number of memory-using operators sort, distinct, group_by, the three joins,
                                     union, intersect, except (+ _all forms)
share = floor((B - S) / K)           given to every memory-using operator
```

Pipelined operators other than scans (filter, project, rename, limit, union_all)
pin nothing of their own and get 0. If the share comes out at 0 (many operators at
B = 4), nested-loop joins run tuple-at-a-time (their "block" is the one outer tuple
in hand, no frame) and the other memory operators take one frame each.

For a single join over two scans this gives share = B − 2, which is exactly the
spec's block nested loop outer block. For a sort over a scan it gives B − 1.

## 3. External sort (`memory.ExternalSorter`)

With W = the operator's share:

```
pass 0      fill W frames of tuples, sort them (Python's sort on one run), write the run
merge       while more than W runs remain: merge W-1 runs at a time into one new run,
            with 1 reserved frame as the output buffer
final pass  merge the last <= W runs straight to the parent, writing nothing
```

- If the whole input fits in W frames nothing is written: one pass, zero I/O.
- Pass 0's frames are released before merging; each merge input pins one page.
- **Carry forward.** In a merge pass, a group left with a single run is carried to
  the next pass untouched, not rewritten (the spec's first strategy).
- **Why W = B − 1 and not B.** During pass 0 the sort's child (a scan) holds the
  page it is reading, so only B − 1 frames are left for the run. This is the one
  place my numbers knowingly differ from the spec's formula, which assumes B frames
  for runs: runs after pass 0 are `ceil(N / (B-1))`, and merges are (B−2)-way.

  Spec example N = 8, B = 3 under my variant: W = 2, so pass 0 makes 4 runs of 2
  pages; one merge pass (2-way) leaves 2 runs; the final pass streams them.
  3 passes like the spec, but 16 reads and 16 writes attributed to the sort (plus 8
  reads by the scan) instead of the spec's 22/14, because of the extra run.
  `tests/test_m2.py::SortExampleN8B3` checks exactly these numbers.

  Passes, my formula: `1 + (merge passes until <= W runs, fan-in W - 1) + 1`; the
  spec's `1 + ceil(log(ceil(N/B)) / log(B-1))` agrees whenever the extra run does
  not cross a power of the fan-in. REPORT_M2.md section 2 compares both.

Page accounting for one sort of N input pages with W working frames, R0 = ceil(N/W):

| pass | reads (by the sort) | writes | frames held |
|---|---|---|---|
| 0 | 0 (the child reads N) | N | W reserved + the child's 1 |
| each merge | N | N | W−1 pinned inputs + 1 reserved output |
| final | N | 0 | up to W pinned inputs |

## 4. Joins

**Block nested loop** (`NestedLoopJoin`). Reads up to `share` pages of outer tuples
into reserved frames, then restarts the inner child and streams it past the block,
testing every pair; repeats per block. Comparisons are exactly |R| × |S| whatever B
or the condition (`test_bnl_compares_every_pair`). Page reads are
`M + ceil(M/(B-2)) × N` plus one directory-page read per scan, because every inner
restart is a new heap scan that starts from directory page 0
(`test_bnl_page_reads_follow_formula_plus_directory_pages`). The only join that
accepts any condition; with no condition it is the cross product.

**Sort-merge join** (`SortMergeJoin`), equality only. The left input is sorted and
its final merge pass is streamed into the join like any sort. The right input must
be rewindable, so after sorting it is either one in-memory run (if it fits in the
frames the left run did not use) or merged into one run file. The merge follows the
spec's pseudocode: for every left tuple in a run of equal keys the right cursor is
rewound to the mark (`TempReader.seek` / `ListCursor.seek`). **A run of equal keys
larger than B** is never held in memory: it stays in the right run file, and each
rewind re-reads its pages through the pool. The spec's duplicate-key example
(R.b = [1,2,2,3], S.b = [2,2,4]) returns four rows (`DuplicateKeys`).

**Grace hash join** (`HashJoin`), equality only, left = build side.

1. *In-memory first.* The build input is read into the working frames. If it all
   fits, the table is built and the probe input streamed past it: no partitioning
   at all, cost M + N. (The spec's formula 3(M + N) assumes partitioning always
   happens.)
2. *Otherwise partition.* P partitions, one reserved output frame each, both inputs
   written by `h1(key) mod P`. P is `share` (B − 2 here) in general, but if the build
   input is a base table its catalog page count M is known and
   P = min(share, ceil(M / share) + 1): enough for each partition to fit in the
   table, without writing hundreds of near-empty partition pages at large B.
3. *Probe.* For each partition pair, build a table on L_i with h2 (my own FNV hash
   with a different seed, bucket lists) and stream R_i past it.

Page accounting when partitioning: reads M + N (scans) + Σ|L_i| + Σ|R_i|, writes
Σ|L_i| + Σ|R_i|; Σ|L_i| is slightly more than M because each partition's last page
is partly full. `test_hash_join_partitions_or_stays_in_memory` checks both cases.

**Partition overflow (my decision).** If some L_i is still larger than the table's
frames, that pair falls back to block nested loop: blocks of L_i the size of the
table, R_i re-read once per block. I chose this over recursive repartitioning
because it always terminates even when one key value fills the partition (repartitioning
cannot split equal keys), and its cost is easy to state. REPORT_M2.md section 4
measures it on 40 % skew.

Hashing uses my own FNV-1a over the key values (`memory.fnv`), never Python's
`hash`, which is salted per process: the same plan at the same B gives the same
partitions and the same counters every run (`test_deterministic_counters`).

## 5. distinct and group_by

Both modes as the plan selects:

- **sort**: an ExternalSorter on the key (all columns for distinct), then one pass
  dropping adjacent duplicates / folding adjacent groups.
- **hash** (`HybridHash`): the table gets T = W − P frames and P = max(1, W // 3)
  partitions. A tuple of a group already in the table is folded in; a new group goes
  into the table while it has room, otherwise to a partition file by hash. At end of
  input the table is emitted, then each partition is processed the same way with a
  new hash seed. Every round finishes at least a table's worth of groups, so it
  terminates even with P = 1.

Aggregates COUNT, SUM, AVG, MIN, MAX; COUNT(*) counts tuples; SUM and AVG need INT;
AVG is a real printed to four decimals. Output names follow the spec
(`count_star`, `avg_R_salary`, `max_R_name`) unless the plan gives a third element
as the name (Module 3 does, for `AS`). With no grouping columns and an empty input
the output is one row with COUNT 0; any other aggregate there is an error, since
there are no NULLs. With grouping columns and an empty input, zero rows.

## 6. Set operators

`union_all` is pipelined concatenation. `union`, `intersect`, `except` and the bag
forms `intersect_all`, `except_all` sort both inputs on every column (each to one
run) and merge the two sorted streams value by value, emitting per value
1 / 1 if on both / 1 if only left / min(l, r) / max(l − r, 0) copies. Both inputs may
exceed memory. Output names come from the left input; inputs must match in column
count and type position by position.

## 7. Extensions to the plan schema

Documented in `execution/plan.py`. All are optional, so every plan the spec shows is
valid as written.

- `seqscan.alias`: scans a table under another name (Module 4 emits this for
  `rename` over `scan`, e.g. self joins).
- `rename {to, child}`: re-qualifies a subquery's columns.
- `project.as`: output names for `SELECT a AS b`.
- `group_by.aggregates` entries may carry a third element, the output name.
- `nested_loop_join.condition` may be absent: the cross product.
- `limit.n` (also accepted as `count` or `limit`).
- `intersect_all`, `except_all`: the bag versions.

## 8. Assumptions (one line each)

- Plan schema: the course's JSON schema and sample plans are not published, so field names are exactly those of the JSON shown in M2 §2, with `limit` carrying its count in `n`; my own sample plans for every operator are in `samples/plans/` (run against `samples/make_sample_db.py`).
- `distinct` without `mode` means sort; `group_by` without `mode` means hash.
- A trace's page ids and a plan's tables refer to files that already exist; nothing is created on the fly.

## 9. Sources

- **Graefe, "Volcano, An Extensible and Parallel Query Evaluation System", IEEE TKDE
  1994.** The open / next / close protocol and the rule that only blocking operators
  (sort, hash build) materialise come from here. It changed where my blocking work
  happens: sort runs pass 0 and every merge except the last inside `open`, and streams
  the last merge through `next`, as Volcano's sort does.
- **Shapiro, "Join Processing in Database Systems with Large Main Memories", ACM TODS
  1986.** Shapiro compares simple, Grace and hybrid hash join and shows that when the
  build relation fits in memory no partitioning is needed. That is the in-memory-first
  build in §4, added after my measurements showed pure Grace partitioning costing more
  at large B than at small B. His partition-count rule (enough partitions that each fits)
  is the P = ceil(M / frames) + 1 choice.
