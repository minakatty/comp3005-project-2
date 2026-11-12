# COMP3005 Project 2: database system (checkpoint 1: engine refactor + M1 storage)

Python 3.9 or newer. No third-party packages needed (matplotlib only if you plot later).

## Layout
```
ralang/        relational algebra library (Bonus 1, refactored in week one)
storage/       M1: disk manager, slotted pages, heap file, buffer pool (LRU, CLOCK), catalog, CLI
tools/         m1_measure.py (report tables), make_demo_traces.py, plus Bonus 1 gen_data/bench
tests/         all tests
traces_published/   the instructor's trace files (put them here)
DESIGN_M1.md  REPORT_M1.md  GRAMMAR.md  docs/
```

## Run the tests
```
python -m unittest discover -s tests
```

## M1 commands (run from the repo root)
```
python -m storage --db db create-table --name R --schema "a:INT,b:VARCHAR(20),c:INT"
python -m storage --db db load   --table R --file r.csv --frames 8          (add --header if the CSV has a header row)
python -m storage --db db scan   --table R --limit 20 --frames 8 --json
python -m storage --db db layout --table R --page 3 --json
python -m storage trace --file traces_published\trace1.txt --frames 16 --policy lru --json
```
`--db` names the folder that holds the data file and goes before the subcommand. `--policy lru|clock`.

## Produce the report tables
```
python tools/m1_measure.py --traces traces_published --out results_m1
```
Writes `results_m1/REPORT_M1_tables.md` and `results_m1/trace_results.csv`. Use `--sizes 4 8 16 32` to choose the
four pool sizes. `python tools/make_demo_traces.py traces_demo` makes *synthetic* traces for testing only.

## Run the relational algebra engine
```
python ra.py --tree "project[Name](select[Age>30](Employees))"
python ra.py --run "project[DID](Employees)" --data data/employees.ra
```
