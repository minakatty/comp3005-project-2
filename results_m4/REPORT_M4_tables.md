# Module 4 measurements (B = 4)

## 1. Estimated against actual rows, every node

Nodes: 126. Median q-error **1.00**, worst **400.0**.

<details><summary>q01.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [Emp.name, Emp.age]` | 734 | 702 | 1.05 |
| `filter (Emp.age > 60)` | 734 | 702 | 1.05 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q02.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [Emp.name]` | 400 | 374 | 1.07 |
| `filter (Emp.city = 'c3')` | 400 | 374 | 1.07 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q03.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [Emp.name]` | 41 | 374 | 9.12 |
| `filter (Emp.state = 's3' and Emp.city = 'c3')` | 41 | 374 | 9.12 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q04.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [Emp.eid]` | 204 | 702 | 3.44 |
| `filter (Emp.salary > 60000 and Emp.age > 60)` | 204 | 702 | 3.44 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q05.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `filter (n > 20)` | 17 | 50 | 2.94 |
| `group_by (hash) [Emp.did] [count(*), avg(Emp.salary)]` | 50 | 50 | 1.00 |
| `filter (Emp.age > 30)` | 3134 | 3110 | 1.01 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q06.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `distinct (sort)` | 10 | 10 | 1.00 |
| `project [Emp.city]` | 4000 | 4000 | 1.00 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q07.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `limit 10` | 10 | 10 | 1.00 |
| `sort [Emp.salary desc]` | 4000 | 10 | 400.00 |
| `project [Emp.name, Emp.salary]` | 4000 | 4000 | 1.00 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q08.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name, d.dname]` | 800 | 751 | 1.07 |
| `nested_loop_join (e.did = d.did)` | 800 | 751 | 1.07 |
| `project [d.did, d.dname]` | 10 | 9 | 1.11 |
| `filter (d.budget > 40000)` | 10 | 9 | 1.11 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `project [e.name, e.did]` | 4000 | 4000 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q09.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name, o.amount]` | 300 | 298 | 1.01 |
| `nested_loop_join (e.eid = o.eid)` | 300 | 298 | 1.01 |
| `project [o.eid, o.amount]` | 300 | 298 | 1.01 |
| `filter (o.amount > 900)` | 300 | 298 | 1.01 |
| `seqscan Orders as o` | 3000 | 3000 | 1.00 |
| `project [e.eid, e.name]` | 4000 | 4000 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q10.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name, o.amount]` | 300 | 288 | 1.04 |
| `nested_loop_join (e.eid = o.eid)` | 300 | 288 | 1.04 |
| `project [e.eid, e.name]` | 400 | 400 | 1.00 |
| `filter (e.age < 25)` | 400 | 400 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |
| `project [o.eid, o.amount]` | 3000 | 3000 | 1.00 |
| `seqscan Orders as o` | 3000 | 3000 | 1.00 |

</details>

<details><summary>q11.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `group_by (hash) [d.dname] [count(*)]` | 50 | 50 | 1.00 |
| `nested_loop_join (e.did = d.did)` | 4000 | 4000 | 1.00 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q12.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [p.pname, d.dname]` | 51 | 50 | 1.02 |
| `nested_loop_join (p.did = d.did)` | 51 | 50 | 1.02 |
| `project [d.did, d.dname]` | 50 | 50 | 1.00 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `project [p.did, p.pname]` | 51 | 50 | 1.02 |
| `filter (p.pid < 50)` | 51 | 50 | 1.02 |
| `seqscan Proj as p` | 400 | 400 | 1.00 |

</details>

<details><summary>q13.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name, e.pad, o.amount]` | 4 | 252 | 63.00 |
| `nested_loop_join (e.eid = o.eid)` | 4 | 252 | 63.00 |
| `project [e.eid, e.name, e.pad]` | 5 | 374 | 74.80 |
| `filter (e.region = 'r3' and e.state = 's3' and e.city = 'c3')` | 5 | 374 | 74.80 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |
| `project [o.eid, o.amount]` | 3000 | 3000 | 1.00 |
| `seqscan Orders as o` | 3000 | 3000 | 1.00 |

</details>

<details><summary>q14.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name, d.dname, p.pname]` | 320 | 259 | 1.24 |
| `nested_loop_join (e.did = d.did)` | 320 | 259 | 1.24 |
| `nested_loop_join (p.did = d.did)` | 40 | 40 | 1.00 |
| `project [p.did, p.pname]` | 40 | 40 | 1.00 |
| `filter (p.pid < 40)` | 40 | 40 | 1.00 |
| `seqscan Proj as p` | 400 | 400 | 1.00 |
| `project [d.did, d.dname]` | 50 | 50 | 1.00 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `project [e.name, e.did]` | 400 | 313 | 1.28 |
| `filter (e.age > 65)` | 400 | 313 | 1.28 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q15.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name, o.amount, d.dname]` | 300 | 243 | 1.23 |
| `hash_join (e.eid = o.eid)` | 300 | 243 | 1.23 |
| `nested_loop_join (e.did = d.did)` | 400 | 325 | 1.23 |
| `project [d.did, d.dname]` | 5 | 4 | 1.25 |
| `filter (d.budget > 45000)` | 5 | 4 | 1.25 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `project [e.eid, e.name, e.did]` | 4000 | 4000 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |
| `project [o.eid, o.amount]` | 3000 | 3000 | 1.00 |
| `seqscan Orders as o` | 3000 | 3000 | 1.00 |

</details>

<details><summary>q16.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [o.oid, p.pname]` | 13 | 11 | 1.18 |
| `nested_loop_join (p.did = d.did)` | 13 | 11 | 1.18 |
| `nested_loop_join (e.did = d.did)` | 31 | 31 | 1.00 |
| `nested_loop_join (o.eid = e.eid)` | 31 | 31 | 1.00 |
| `project [o.oid, o.eid]` | 31 | 31 | 1.00 |
| `filter (o.amount > 990)` | 31 | 31 | 1.00 |
| `seqscan Orders as o` | 3000 | 3000 | 1.00 |
| `project [e.eid, e.did]` | 4000 | 4000 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |
| `project [d.did]` | 50 | 50 | 1.00 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `project [p.did, p.pname]` | 20 | 20 | 1.00 |
| `filter (p.pid < 20)` | 20 | 20 | 1.00 |
| `seqscan Proj as p` | 400 | 400 | 1.00 |

</details>

<details><summary>q17.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `union` | 301 | 10 | 30.10 |
| `project [Emp.city]` | 200 | 163 | 1.23 |
| `filter (Emp.age < 22)` | 200 | 163 | 1.23 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |
| `project [Emp.city]` | 101 | 80 | 1.26 |
| `filter (Emp.age > 68)` | 101 | 80 | 1.26 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q18.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `intersect` | 30 | 29 | 1.03 |
| `project [Emp.did]` | 338 | 335 | 1.01 |
| `filter (Emp.salary > 70000)` | 338 | 335 | 1.01 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |
| `project [Dept.did]` | 30 | 29 | 1.03 |
| `filter (Dept.budget > 20000)` | 30 | 29 | 1.03 |
| `seqscan Dept` | 50 | 50 | 1.00 |

</details>

<details><summary>q19.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [t.did, t.n]` | 20 | 19 | 1.05 |
| `nested_loop_join (t.did = d.did)` | 20 | 19 | 1.05 |
| `project [d.did]` | 20 | 19 | 1.05 |
| `filter (d.budget > 30000)` | 20 | 19 | 1.05 |
| `seqscan Dept as d` | 50 | 50 | 1.00 |
| `rename to t` | 50 | 50 | 1.00 |
| `group_by (hash) [Emp.did] [count(*)]` | 50 | 50 | 1.00 |
| `seqscan Emp` | 4000 | 4000 | 1.00 |

</details>

<details><summary>q20.sql</summary>

| node | est | actual | q-error |
|---|---|---|---|
| `project [e.name]` | 400 | 302 | 1.32 |
| `nested_loop_join (e.mgr = m.eid)` | 400 | 302 | 1.32 |
| `project [m.eid]` | 400 | 313 | 1.28 |
| `filter (m.age > 65)` | 400 | 313 | 1.28 |
| `seqscan Emp as m` | 4000 | 4000 | 1.00 |
| `project [e.name, e.mgr]` | 4000 | 4000 | 1.00 |
| `seqscan Emp as e` | 4000 | 4000 | 1.00 |

</details>

## 2. Does optimization pay

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

## 3. Which rule mattered

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

