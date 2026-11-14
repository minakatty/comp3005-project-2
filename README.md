Amina Katawazi
101308840

# Project 2: a database system, one module at a time

Text goes in as SQL, a plan comes out, and tuples are read from pages on disk
through a buffer pool. Four modules, each runnable on its own, joined by JSON
contracts, built on top of the Bonus Project 1 relational algebra engine.

```
  SQL text
     |
  [ M3 sqlfront ]   lexer -> parser -> name resolution / types -> translation
     |  logical plan JSON
  [ M4 optimizer ]  rewrites -> statistics -> cost model -> join order -> algorithm
     |  physical plan JSON
  [ M2 execution ]  iterator tree, open / next / close, memory budget B
     |  page requests
  [ M1 storage ]    buffer pool -> heap files -> pages on disk
```

Python 3.10 or later. No third-party packages are needed to run anything
(matplotlib is used only by the report scripts, for plots, if it is installed).

## The command line tools

Each module has the command the spec names. On Windows use `python`, elsewhere
`python3` if that is your interpreter's name.

```
# Module 1: storage
python -m storage create-table --db db --name R --schema "a:INT,b:VARCHAR(20),c:INT"
python -m storage load         --db db --table R --file r.csv
python -m storage scan         --db db --table R --frames 8 --limit 20 --json
python -m storage layout       --db db --table R --page 1 [--hex]
python -m storage trace        --file trace.txt --frames 16 --policy lru --json
python -m storage page-demo                     # spec 3.2 worked example, step by step

# Module 2: execution        (exec.py is the same as python -m execution)
python exec.py run   --db db --plan plan.json --frames 32 --json
python exec.py trace --db db --plan plan.json --frames 32

# Module 3: SQL              (sql.py is the same as python -m sqlfront)
python sql.py parse   --query "SELECT a FROM R WHERE b > 3" --explain
python sql.py compile --db db --file q.sql --json
python sql.py check   --db db --file q.sql

# Module 4: optimizer        (opt.py is the same as python -m optimizer)
python opt.py analyze  --db db --table R --buckets 20
python opt.py plan     --db db --logical q.json --frames 32 --json
python opt.py explain  --db db --logical q.json --frames 32 [--rewrites]
python opt.py dp-table --db db --logical q.json --frames 32
#   --sql q.sql may replace --logical (compiled by M3 first); --disable 2,6 turns rules off;
#   --naive prints the unoptimized plan used as the baseline in REPORT_M4.md

# Integration: one prompt, SQL in, rows out
python dbms.py --db db --frames 32
python dbms.py --db db --file script.sql
#   also: EXPLAIN q; EXPLAIN ANALYZE q; ANALYZE t; SET frames = n; LOAD t FROM file;

# Bonus Project 1, unchanged interface
python ra.py --tree "project[Name](select[Age>30](Employees))"
```

## Tests

```
python -m unittest discover -s tests
```

| file | what it covers |
|---|---|
| `tests/test_tokenizer.py`, `test_parser.py`, `test_operators.py` | the 25 Bonus 1 cases (still passing; case 23 now needs an explicit `distinct`) |
| `tests/test_week1.py` | the week-one refactor: JSON round trip, condition parser alone, providers, bags |
| `tests/test_m1.py` | spec 3.2 and 5.3 worked examples, every Section 9 error, the Section 10 checklist |
| `tests/test_m2.py` | the Section 6 checklist, plan validation messages, skew, empty inputs |
| `tests/test_m3.py` | the 15 required cases and the 7 translation examples, compared structurally |
| `tests/test_m4.py` | the selectivity, rewrite and DP worked examples, the Section 8 checklist |

## Project layout

```
ralang/        Bonus 1 engine as a library: tokenizer, parser, AST, operators,
               condition.py (the reusable condition parser/evaluator), plan_json.py
storage/       M1: record.py, page.py, disk.py, bufferpool.py, heap.py, catalog.py, db.py, cli.py
execution/     M2: operators.py (every iterator), memory.py (budget, temp files, external sort),
               plan.py (validation, construction, frame allocation), engine.py, cli.py
sqlfront/      M3: lexer.py, parser.py, sqlast.py, translate.py (scope, types, translation), cli.py
optimizer/     M4: stats.py (ANALYZE), estimate.py (selectivity), logical.py (rewrite rules),
               physical.py (cost model, DP, physical plans), optimize.py, cli.py
dbms/          the integrated shell
tools/         measurement scripts for every REPORT_M*.md, data and trace generators
tests/         all of the above
```

Measurement output: `results_m1/`, `results_m2/`, `results_m4/` (from the scripts in
`tools/`); `workload_m4_standin/` is the stand-in query set REPORT_M4.md uses until the
published workload is in place. **`TODO_BEFORE_SUBMIT.md` lists what still needs you.**

Documents: `GRAMMAR.md` (Bonus 1), `DESIGN_M1.md` … `DESIGN_M4.md`, `REPORT_M1.md` …
`REPORT_M4.md`, `GRAMMAR_SQL.md`, `ARCHITECTURE.md`, `DESIGN_LOG.md`.

## Known limitations

- Only INT and VARCHAR(n ≤ 255) columns, no NULLs, as the handout requires.
- Statistics are exact (ANALYZE scans and sorts every column); there is no sampling.
- The optimizer enumerates left-deep trees only, and joins blocks of up to about ten
  relations (2^n subsets); the spec asks for six.
- The integrated shell reopens the buffer pool for every query so its page counts
  are reproducible; there is no cache shared between statements.
- There are no indexes, transactions, concurrency or recovery: out of scope.
