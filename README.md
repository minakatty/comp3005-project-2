# COMP3005 Project 2: database system (checkpoint 1: engine refactor + Module 1)

Python 3.9+. No third-party packages.

```
ralang/        relational algebra library (Bonus 1, refactored in week one)
storage/       Module 1: disk manager, slotted pages, heap files with directory pages, buffer pool, catalog, CLI
tools/         m1_measure.py (report tables), make_demo_traces.py, plus the Bonus 1 gen_data/bench/plot
tests/         all tests          docs/   spec checklist, oral-check notes
traces_published/   put the instructor's trace files here
```

## Tests
```
python -m unittest discover -s tests
```

## Module 1 commands (run from the repo root; `--db` is a folder, default `db`)
```
python -m storage create-table --db db --name R --schema "a:INT,b:VARCHAR(20),c:INT"
python -m storage load   --db db --table R --file r.csv          (no header line; "quoted,fields" allowed)
python -m storage scan   --db db --table R --frames 8 --limit 20 --json
python -m storage layout --db db --table R --page 3 [--hex] [--json]
python -m storage trace  --db db --file traces_published\trace1.txt --frames 16 --policy lru --json
```
`--policy lru|clock` (default lru), `--frames` is the buffer pool size B.

## Report tables
```
python tools/m1_measure.py --traces traces_published --out results_m1
```
Writes `results_m1/REPORT_M1_tables.md` (all three required measurements) and `results_m1/trace_results.csv`.
`python tools/make_demo_traces.py traces_demo` makes SYNTHETIC traces for testing the script only.

## Relational algebra engine
```
python ra.py --tree "project[Name](select[Age>30](Employees))"
```
