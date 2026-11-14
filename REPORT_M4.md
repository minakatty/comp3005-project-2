# REPORT_M4 — Optimizer measurements

> **Placeholder data.** The spec's measurements are on *the twenty supplied queries
> and the supplied dataset*, which were not available when this was written. Every
> number below comes from a **stand-in** dataset and workload made by
> `tools/make_m4_dataset.py` (Emp 4 000 rows, Orders 3 000, Dept 50, Proj 400; the
> 20 queries are in `workload_m4_standin/`), with two correlations planted on purpose
> (city → state → region one-to-one, salary rising with age). Rerun on the real data:
>
> ```
> for t in <tables>: python opt.py analyze --db <db> --table $t
> python tools/m4_measure.py --db <db> --workload <queries dir> --frames 4 --out results_m4
> ```
>
> then replace the tables and recheck every sentence. The method and the tooling stay.

Machine: *fill in*. Python *3.x.y*. B = 4 throughout (the smallest budget the
executor is graded at, and the one where plan choice matters most). Full per-node
tables: `results_m4/REPORT_M4_tables.md`.

## 1. Estimation quality

126 plan nodes over the 20 queries. q-error = max(est, actual) / min(est, actual),
where for the inner of a nested-loop join "actual" is the rows of one execution (it is
re-executed once per outer block).

**Median q-error 1.00, worst 400.**

| worst nodes | query | est | actual | q-error | why |
|---|---|---|---|---|---|
| `sort [Emp.salary desc]` | q07 | 4000 | 10 | 400 | the `LIMIT 10` above stops pulling after 10 rows; my estimator does not push a limit's cap into its child |
| `filter (region = 'r3' and state = 's3' and city = 'c3')` | q13 | 5 | 374 | 74.8 | independence across three columns that are one column in disguise (§4) |
| `nested_loop_join (e.eid = o.eid)` and its project | q13 | 4 | 252 | 63 | the filter's error, inherited |
| `union` | q17 | 301 | 10 | 30 | union estimated as the sum of its inputs; the inputs are 10 distinct cities repeated |
| `filter (state = 's3' and city = 'c3')` | q03 | 41 | 374 | 9.1 | the same correlation with two columns: (1/10)² instead of 1/10 |
| `filter (salary > 60000 and age > 60)` | q04 | 204 | 702 | 3.4 | the range-correlated pair: a high age implies a high salary |

A median of 1.00 is the scans (exact: the statistics are exact counts), single-column
equality and range predicates (histograms), and key–foreign-key joins, where
n(R)·n(S)/max(ndv) is right because ndv(eid) is the table size. Every large error comes
from one of three assumptions: independence, a set operation's duplicates, and a
limit that the estimator ignores. Errors only grow upward through a plan: q13's join is
off by 63 because its input already was.

## 2. Does optimization pay

The unoptimized plan: joins in the order written, block nested loop for every join, no
pushdown at all (`python opt.py plan --naive`).

| query | optimized I/O | unoptimized I/O | ratio | optimized s | unoptimized s |
|---|---|---|---|---|---|
| q01.sql | 126 | 126 | 1.00 | 0.02 | 0.02 |
| q02.sql | 126 | 126 | 1.00 | 0.02 | 0.03 |
| q03.sql | 126 | 126 | 1.00 | 0.02 | 0.02 |
| q04.sql | 126 | 126 | 1.00 | 0.02 | 0.02 |
| q05.sql | 126 | 1090 | 8.65 | 0.02 | 0.17 |
| q06.sql | 142 | 142 | 1.00 | 0.04 | 0.04 |
| q07.sql | 180 | 180 | 1.00 | 0.05 | 0.05 |
| q08.sql | 128 | 251 | 1.96 | 0.03 | 0.09 |
| q09.sql | 294 | 10710 | 36.43 | 0.34 | 3.77 |
| q10.sql | 294 | 10710 | 36.43 | 0.35 | 7.41 |
| q11.sql | 128 | 2579 | 20.15 | 0.07 | 0.41 |
| q12.sql | 6 | 7 | 1.17 | 0.00 | 0.01 |
| q13.sql | 966 | 10710 | 11.09 | 0.37 | 3.70 |
| q14.sql | 5122 | 24120 | 4.71 | 0.61 | 3.37 |
| q15.sql | 716 | 680124 | 949.89 | 0.40 | 1429.88 |
| q16.sql | 4257 | 396165 | 93.06 | 0.62 | 48.32 |
| q17.sql | 256 | 256 | 1.00 | 0.03 | 0.03 |
| q18.sql | 132 | 132 | 1.00 | 0.02 | 0.02 |
| q19.sql | 128 | 1874 | 14.64 | 0.02 | 0.27 |
| q20.sql | 252 | 8001 | 31.75 | 0.31 | 5.55 |

Ratio range across the workload: 1.00 to 949.89.

Where the ratio is 1.00 there is nothing to choose: one table, a filter and a
projection (q01–q04, q06, q07, q17, q18). The ratio grows with the number of joins and
with how much a pushed-down filter or projection shrinks a join input. q15 (949×) is the
comma join `Emp, Orders, Dept` with all predicates in WHERE: unoptimized, that is a
cross product of Emp and Orders (12 million pairs, 680 124 page I/O, 24 minutes in
Python) filtered afterwards; optimized, the predicates become join conditions, the
`budget > 45000` filter shrinks Dept to a few rows, and the projections leave the
nested-loop outer one or two pages wide.

## 3. Which rule mattered

Each rewrite rule turned off on its own, whole workload rerun, total actual I/O:

| rule off | total workload I/O | change vs all rules on |
|---|---|---|
| (none) | 13631 | |
| 1 | 28756 | +15125 (+111.0%) |
| 2 | 28758 | +15127 (+111.0%) |
| 3 | 13631 | +0 (+0.0%) |
| 4 | 13631 | +0 (+0.0%) |
| 5 | 13631 | +0 (+0.0%) |
| 6 | 148764 | +135133 (+991.4%) |
| 7 | 13631 | +0 (+0.0%) |

- **Rule 6 (push projections down) matters most: +991 % without it.** At B = 4 a
  nested-loop join's cost is driven by how many outer *pages* there are, and a hash
  join's by how many partitions overflow. Keeping only the columns a join needs turns
  Emp's 125 pages into 8 (eid, did) or a pipelined trickle, so outer blocks and
  partitions shrink by an order of magnitude. Emp and Orders carry wide `pad` columns
  that no query needs, which is exactly when rule 6 pays.
- **Rules 1 and 2: +111 % each, and the same number.** Rule 2 pushes single-table
  predicates below joins, but a conjunction `a AND b` can only be pushed once rule 1 has
  split it, so turning off either one disables the same pushdowns.
- **Rule 3 (cross + select → join): 0 %.** Not because cross products are fine, but
  because my join enumerator (DESIGN_M4.md §5) takes every select inside a join block
  as a join predicate and applies it at the first join where both sides are present, so
  it re-derives what rule 3 would have done. What rule 3 still buys is a logical plan in
  which a comma join and a JOIN ON look the same.
- **Rules 4, 5 (push through rename / union / group_by): 0 % on this workload.** Only
  q19 has a subquery and its WHERE is on the base table, not on the subquery's columns;
  no query filters above a union. These rules matter on queries this workload lacks:
  `SELECT … FROM (… GROUP BY did) AS t WHERE t.did = 3` reads one group's tuples
  instead of every tuple (`test_rule5_...`).
- **Rule 7 (cleanup): 0 %**, as expected: removing a projection that drops nothing,
  or merging two adjacent selects, changes no page count. It makes `explain` readable.

## 4. Where it breaks

Query `q13.sql`: `SELECT e.name, e.pad, o.amount FROM Emp e JOIN Orders o ON e.eid = o.eid WHERE e.city = 'c3' AND e.state = 's3' AND e.region = 'r3';`

Chosen plan, with estimated and actual rows:

```
project [e.name, e.pad, o.amount]                            est=4       actual=252     reads=0 writes=0
nested_loop_join (e.eid = o.eid)                             est=4       actual=252     reads=0 writes=0
project [e.eid, e.name, e.pad]                               est=5       actual=374     reads=0 writes=0
filter (e.region = 'r3' and e.state = 's3' and e.city = 'c3') est=5       actual=374     reads=0 writes=0
seqscan Emp as e                                             est=4000    actual=4000    reads=126 writes=0
project [o.eid, o.amount]                                    est=3000    actual=3000 x5   reads=0 writes=0
seqscan Orders as o                                          est=3000    actual=3000 x5   reads=840 writes=0
```

Chosen plan: estimated cost 292, actual I/O **966**.

- same plan with the join forced to **hash_join**: actual I/O 360
- same plan with the join forced to **sort_merge_join**: actual I/O 414
- same plan with the join forced to **nested_loop_join**: actual I/O 966

**The estimate.** sel(city = 'c3') = sel(state = 's3') = sel(region = 'r3') = 1/10 from
each column's histogram; the AND rule multiplies them: 4 000 × (1/10)³ ≈ 4–5 rows,
1 page.

**The actual.** 374 rows (10 %), because state and region are functions of city: a
row with city c3 always has state s3 and region r3, so the second and third predicates
remove nothing. After projection that is about 11 pages, not 1.

**The assumption that failed:** independence, `sel(p AND q) = sel(p) × sel(q)`
(DESIGN_M4.md §2). It underestimated by 75×.

**What it cost.** Believing the filtered Emp fits in one nested-loop block
(B − 2 = 2 frames), the optimizer chose block nested loop with Emp outer: predicted
125 + 1 × 167 = 292. In reality the outer took 5 blocks, so Orders was scanned five
times: **966** page I/O. The same plan with a hash join instead does **360**, and with
sort-merge 414. The bad plan cost **2.7×** the better one, from one estimate.

**What statistic would have prevented it:** a multi-column statistic on
(city, state, region): either the number of distinct value combinations, ndv = 10, which
gives sel = 1/10 for the conjunction directly, or functional-dependency degrees
(PostgreSQL's `CREATE STATISTICS … (dependencies)`), which record that state and region
are determined by city so their predicates add a factor of 1. A two-dimensional
histogram on (age, salary) would do the same for the range-correlated pair, which q04
exercises (`age > 60 AND salary > 60000`).
