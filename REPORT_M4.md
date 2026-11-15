# REPORT_M4: Optimizer measurements

**Assumption:** the course dataset and twenty-query workload are not published, so both
are my own: `python tools/make_m4_dataset.py --db db_m4 --workload workload_m4` builds Emp
(4 000 rows), Orders (3 000), Dept (50) and Proj (400) and writes the 20 queries. Two
correlations are planted on purpose so Section 4 has something to break: city, state and
region are one-to-one, and salary rises with age.

To reproduce:

```
python tools/make_m4_dataset.py --db db_m4 --workload workload_m4
python tools/m4_measure.py --db db_m4 --workload workload_m4 --frames 4 --analyze --out results_m4
```

Machines: the tables were produced on Linux (Intel Xeon 2.10 GHz, 2 cores, 7 GB RAM, Python 3.13.18). The test suite also passes on my Windows PC (8 logical processors, Python 3.13.14). Page counts and comparisons are deterministic, so they do not depend on the machine; wall-clock seconds do. B = 4 throughout (the smallest budget the
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
| q01.sql | 126 | 126 | 1.00 | 0.01 | 0.01 |
| q02.sql | 126 | 126 | 1.00 | 0.01 | 0.01 |
| q03.sql | 126 | 126 | 1.00 | 0.01 | 0.01 |
| q04.sql | 126 | 126 | 1.00 | 0.01 | 0.01 |
| q05.sql | 126 | 1282 | 10.17 | 0.01 | 0.14 |
| q06.sql | 154 | 154 | 1.00 | 0.03 | 0.03 |
| q07.sql | 203 | 203 | 1.00 | 0.04 | 0.04 |
| q08.sql | 128 | 251 | 1.96 | 0.02 | 0.06 |
| q09.sql | 294 | 10710 | 36.43 | 0.24 | 2.93 |
| q10.sql | 294 | 10710 | 36.43 | 0.24 | 6.19 |
| q11.sql | 128 | 2579 | 20.15 | 0.06 | 0.39 |
| q12.sql | 6 | 7 | 1.17 | 0.00 | 0.01 |
| q13.sql | 966 | 10710 | 11.09 | 0.27 | 3.17 |
| q14.sql | 5122 | 24120 | 4.71 | 0.59 | 2.78 |
| q15.sql | 716 | 680124 | 949.89 | 0.36 | 1201.46 |
| q16.sql | 4257 | 396165 | 93.06 | 0.50 | 38.36 |
| q17.sql | 256 | 256 | 1.00 | 0.03 | 0.03 |
| q18.sql | 132 | 132 | 1.00 | 0.01 | 0.01 |
| q19.sql | 128 | 1626 | 12.70 | 0.01 | 0.19 |
| q20.sql | 252 | 8001 | 31.75 | 0.26 | 4.61 |

Ratio range across the workload: 1.00 to 949.89.

Where the ratio is 1.00 there is nothing to choose: one table, a filter and a
projection (q01–q04, q06, q07, q17, q18). The ratio grows with the number of joins and
with how much a pushed-down filter or projection shrinks a join input. q15 (949×) is the
comma join `Emp, Orders, Dept` with all predicates in WHERE: unoptimized, that is a
cross product of Emp and Orders (12 million pairs, 680 124 page I/O, 20 minutes in
Python) filtered afterwards; optimized, the predicates become join conditions, the
`budget > 45000` filter shrinks Dept to a few rows, and the projections leave the
nested-loop outer one or two pages wide.

## 3. Which rule mattered

Each rewrite rule turned off on its own, whole workload rerun, total actual I/O:

| rule off | total workload I/O | change vs all rules on |
|---|---|---|
| (none) | 13666 | |
| 1 | 28666 | +15000 (+109.8%) |
| 2 | 28380 | +14714 (+107.7%) |
| 3 | 13666 | +0 (+0.0%) |
| 4 | 13666 | +0 (+0.0%) |
| 5 | 13666 | +0 (+0.0%) |
| 6 | 142431 | +128765 (+942.2%) |
| 7 | 13666 | +0 (+0.0%) |

- **Rule 6 (push projections down) matters most: +942 % without it.** At B = 4 a
  nested-loop join's cost is driven by how many outer *pages* there are, and a hash
  join's by how many partitions overflow. Keeping only the columns a join needs turns
  Emp's 125 pages into 8 (eid, did) or a pipelined trickle, so outer blocks and
  partitions shrink by an order of magnitude. Emp and Orders carry wide `pad` columns
  that no query needs, which is exactly when rule 6 pays.
- **Rules 1 and 2: about +110 % each.** Rule 2 pushes single-table predicates below
  joins, and in the comma joins (q10, q14, q15) the WHERE clause mixes join and filter
  predicates in one conjunction, which rule 2 can only push once rule 1 has split it. So
  there, turning off either rule loses the same pushdowns. They differ on three queries:
  - q09 and q20 have a single filter predicate, so rule 1 is not needed: rule 1 off costs
    nothing there (294, 252), and rule 2 off costs more (348, 342).
  - q13 goes the other way. Its three predicates are all on `e`, so rule 2 pushes the whole
    conjunction without rule 1. But with rule 2 off the plan gets **better**: 536 instead
    of 966. Without the pushdown the optimizer never sees the badly underestimated filter
    (§4), plans the join over all of Emp, and picks a hash join. A correct rule turned into a
    bad plan because of a wrong estimate, which is §4's point from the other side.
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
project [o.eid, o.amount]                                    est=3000    actual=3000 x5 reads=0 writes=0
seqscan Orders as o                                          est=3000    actual=3000 x5 reads=840 writes=0
```

Chosen plan: estimated cost 292, actual I/O **966**.

- same plan with the join forced to **hash_join**: actual I/O 348
- same plan with the join forced to **sort_merge_join**: actual I/O 382
- same plan with the join forced to **nested_loop_join**: actual I/O 966

**The estimate.** sel(city = 'c3') = sel(state = 's3') = sel(region = 'r3') = 1/10 from
each column's histogram; the AND rule multiplies them: 4 000 × (1/10)³ ≈ 4–5 rows,
1 page.

**The actual.** 374 rows (10 %), because state and region are functions of city: a
row with city c3 always has state s3 and region r3, so the second and third predicates
remove nothing. After projection that is 10 pages, not 1.

**The assumption that failed:** independence, `sel(p AND q) = sel(p) × sel(q)`
(DESIGN_M4.md §2). It underestimated by 75×.

**What it cost.** Believing the filtered Emp fits in one nested-loop block
(B − 2 = 2 frames), the optimizer chose block nested loop with Emp outer: predicted
125 + 1 × 167 = 292. In reality the outer took 5 blocks, so Orders was scanned five
times: **966** page I/O. The same plan with a hash join instead does **348**, and with
sort-merge 382. The bad plan cost **2.8×** the better one, from one estimate.

**What statistic would have prevented it:** a multi-column statistic on
(city, state, region): either the number of distinct value combinations, ndv = 10, which
gives sel = 1/10 for the conjunction directly, or functional-dependency degrees
(PostgreSQL's `CREATE STATISTICS … (dependencies)`), which record that state and region
are determined by city so their predicates add a factor of 1. A two-dimensional
histogram on (age, salary) would do the same for the range-correlated pair, which q04
exercises (`age > 60 AND salary > 60000`).

## 5. The DP table for one four-relation join, in full

The M4 deliverables ask for this table in full. It is also in DESIGN_M4.md §5.2, where the enumeration is explained. Query 16 of my workload (`python opt.py dp-table --db db_m4 --sql workload_m4/q16.sql --frames 16`).

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

## Sources

- **Leis et al., VLDB 2015** (DESIGN_M4.md §7): errors multiply through joins and come
  mainly from independence on correlated columns; §1's worst genuine error (75×) is exactly
  that, and q13's join inherits it.
- **Selinger et al., SIGMOD 1979**: the plans compared in §2 are produced by its DP over
  left-deep trees; the unoptimized baseline is what that paper's system would do with no
  enumeration at all.
