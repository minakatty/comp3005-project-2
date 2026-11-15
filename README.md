Amina Katawazi
101308840

# COMP 3005 Project 2 - a small database system

This is my Project 2: a working DBMS built on top of my Bonus Project 1
relational algebra engine. You type SQL, it gets parsed into a plan, the optimizer picks
how to run it, and the executor reads tuples off 4 KB pages on disk through a buffer pool
I wrote. It's all plain Python, only the standard library (`json`, `csv`, `struct` and so on), plus matplotlib but only for the plots in the reports; you don't need it to run anything.

## How it fits together

```
 SQL text
    |
  M3  sqlfront/    hand-written lexer + parser, name resolution, type checks  -> logical plan (JSON)
    |
  M4  optimizer/   statistics, estimates, rewrite rules, join order + algorithm -> physical plan (JSON)
    |
  M2  execution/   iterator operators (open/next/close) inside a memory budget of B frames
    |
  M1  storage/     buffer pool (LRU / CLOCK) -> heap files -> slotted pages on disk
```

Each module also runs on its own with its own command (that's how each checkpoint gets
graded), and `dbms.py` wires all four together into one prompt.

`ralang/` is my Bonus 1 engine turned into a library. Its condition parser (things like
`R.a > 5 and R.b = 'x'`) gets reused by M2, M3 and M4, so every condition in every plan
is written in the same little language.

## Setup

You just need Python 3.10 or newer (I used 3.13). Nothing to install.

```
cd C:\dev\project2
python -m unittest discover -s tests
```

You should see `Ran 212 tests ... OK`. It takes about 2 minutes; the M4 tests are the slow ones.

## Quick demo (start here)

Make two small tables and load them:

```
Set-Content emp.csv "1,amy,30,10","2,bob,41,20","3,cat,52,10","4,dan,25,30"
Set-Content dept.csv "10,Sales","20,IT","30,HR"
python -m storage create-table --db mydb --name Emp --schema "eid:INT,name:VARCHAR(10),age:INT,did:INT"
python -m storage create-table --db mydb --name Dept --schema "did:INT,title:VARCHAR(10)"
python -m storage load --db mydb --table Emp --file emp.csv
python -m storage load --db mydb --table Dept --file dept.csv
```

Then open the shell and type SQL (`dbms>` is the prompt, you don't type it):

```
python dbms.py --db mydb
dbms> SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.age > 28;
dbms> EXPLAIN ANALYZE SELECT name FROM Emp WHERE age > 28;
dbms> SET frames = 4;
dbms> \q
```

The shell also understands `ANALYZE Emp;`, `LOAD Emp FROM emp.csv;`, `CREATE TABLE` and
`INSERT INTO`. Statements end with `;`.

## Running each module on its own

### M1: storage
```
python -m storage scan   --db mydb --table Emp --frames 4 --json
python -m storage layout --db mydb --table Emp --page 1 --hex     # the page, slot by slot, byte by byte
python -m storage trace  --file traces/trace1.txt --frames 3 --policy lru --json
python -m storage page-demo                                        # reproduces the spec's 3.2 table
```
`traces/trace1.txt` is the spec's 5.3 example (1 2 3 1 4 2). With 3 frames LRU gives
1 hit / 5 misses / 2 evictions and CLOCK gives 2 / 4 / 1, same as the spec.

### M3: SQL front end (`sql.py` is short for `python -m sqlfront`)
```
Set-Content q.sql "SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.age > 28;"
python sql.py parse   --query "SELECT name FROM Emp WHERE age > 3" --explain   # syntax tree, no catalog
python sql.py compile --db mydb --file q.sql                                   # logical plan as a tree
python sql.py compile --db mydb --file q.sql --json > q.json                   # logical plan as JSON
python sql.py check   --db mydb --file q.sql                                   # only errors, exit 0 or 1
```

### M4: optimizer (`opt.py` is short for `python -m optimizer`)
```
python opt.py analyze  --db mydb --table Emp
python opt.py explain  --db mydb --logical q.json --frames 8 --rewrites   # before/after rewrites + chosen plan
python opt.py dp-table --db mydb --logical q.json --frames 8              # the join-order DP table
python opt.py plan     --db mydb --logical q.json --frames 8 --json > plan.json
```
Extras: `--catalog db/` works the same as `--db` in every tool (the handout writes it that way), `--sql q.sql` works instead of `--logical`, `--disable 2,6` turns rewrite rules
off, and `--naive` prints the "no optimizer" plan I compare against in REPORT_M4.

### M2: execution (`exec.py` is short for `python -m execution`)
```
python exec.py run   --db mydb --plan plan.json --frames 8 --json
python exec.py trace --db mydb --plan plan.json --frames 8            # counters for every operator
```
There's a sample plan for every operator in `samples/plans/`, written from the JSON in M2
Section 2. To try them:
```
python samples/make_sample_db.py samples_db
python exec.py run --db samples_db --plan samples/plans/11_hash_join.json --frames 4
```
Plans 20–22 are broken on purpose, to show the error messages (missing field, unknown
operator, unknown column).

## Where the report numbers come from

Every table in the REPORT files is produced by a script in `tools/`; nothing is typed in
by hand. Page counts are deterministic, so reruns give the same numbers (only the seconds
change).

```
python tools/make_traces.py traces
python tools/m1_measure.py --traces traces --out results_m1
python tools/m2_measure.py --out results_m2                                  # ~10 min
python tools/make_m4_dataset.py --db db_m4 --workload workload_m4
python tools/m4_measure.py --db db_m4 --workload workload_m4 --frames 4 --analyze --out results_m4   # ~30 min
```

For the integration demo (the 25 hidden queries), this runs every query through the
whole system and writes the table the handout asks for: rows, page I/O, wall time, and
estimated vs actual rows at the root:

```
python tools/run_workload.py --db db --queries hidden_queries --frames 32 --out results_final.md
```

About data: it was said the course traces, sample plans and dataset won't be posted and to write our own, so `traces/`, `samples/` and `workload_m4/` are mine. Every assumption that comes from that is one line in the "Assumptions" section of the matching DESIGN file.

## What's in the repo

| File / folder | What it is |
|---|---|
| `DESIGN_M1.md` – `DESIGN_M4.md` | how each module works, my decisions, assumptions, sources |
| `REPORT_M1.md`, `REPORT_M2.md`, `REPORT_M4.md` | the required measurements and what they mean |
| `GRAMMAR.md` | Bonus 1 grammar |
| `GRAMMAR_SQL.md` | the SQL grammar for M3, with precedence tables |
| `ARCHITECTURE.md` | the whole system, the interfaces between modules, what I'd change |
| `DESIGN_LOG.md` | dated diary, including twelve times the AI got something wrong (at least two per module) |
| `tests/` | all the tests; the 25 Bonus 1 cases still pass |
| `tools/` | the scripts that make the traces, the M4 dataset and every report table |
| `samples/` | my sample plans (one per operator), sample SQL, and the script that builds their database |
| `traces/`, `workload_m4/` | my buffer pool traces and my 20 M4 queries (the course ones weren't posted) |
| `results_m1/`, `results_m2/`, `results_m4/` | raw output of the measurement scripts |

One Bonus 1 test changed meaning: case 23 (`project[DID](Employees)`) now needs an explicit
`distinct(...)`, because projection keeps duplicates now (SQL works on bags, not sets).

## What works

- INT and VARCHAR(n ≤ 255) columns, no NULLs (as the spec says)
- slotted pages exactly to the spec's byte layout; a page is compacted only when an insert needs the space
- LRU and CLOCK; evicting a pinned page is an error, never a silent choice
- external merge sort, block nested loop, sort-merge join and Grace hash join (with partition overflow handling)
- distinct and group by in both sort and hash versions; COUNT, SUM, AVG, MIN, MAX
- union, union all, intersect, except (plus bag versions)
- SQL: joins with aliases, comma joins, self joins, subqueries in FROM, GROUP BY / HAVING,
  ORDER BY (even on columns not selected), LIMIT, CREATE TABLE, INSERT
- optimizer: ANALYZE with equi-depth histograms, 7 rewrite rules, a page-I/O cost model,
  and dynamic programming over left-deep join orders
- a plan never holds more than B frames at once (checked in the tests)

## Known limitations

- Hash join skips partitioning when the build side fits in memory, so at large B it costs
  less than the spec's 3(M+N). At B = 4, big inputs overflow their partitions and cost a lot
  more. Both are explained in REPORT_M2.
- The optimizer only builds left-deep trees and gets slow past about 10 tables in one join.
- Estimates assume columns are independent, so correlated columns fool it. That's on
  purpose for REPORT_M4 Section 4.
- The estimator ignores LIMIT, so a sort under a LIMIT shows a big q-error.
- No indexes, transactions or concurrency (out of scope for the project).
- The shell opens a fresh buffer pool for each query so I/O numbers are repeatable, which
  means nothing is cached between queries.

## Troubleshooting

- "can't open file ... sql.py": you're not in the project folder. Run `cd C:\dev\project2` first.
- SQL typed straight into PowerShell gives red errors: SQL only works inside
  `python dbms.py` (after the `dbms>` prompt shows up), or saved in a `.sql` file.
- Files saved with `>` in PowerShell: PowerShell saves them as UTF-16. The tools read
  that fine now (this broke things before I fixed it).
