# What is left for you to do before each checkpoint

The code, tests and documents are complete. These items need things only you have
(the published files, your machine, your own words), and the oral check will ask
about them.

## Everywhere
- [ ] Run `python -m unittest discover -s tests` on your machine (expect all OK; M4 tests take ~2 min).
- [ ] Fill in "Machine / Python version" at the top of each REPORT_M*.md.
- [ ] Write the **Sources** sections (two per checkpoint, and what each changed). Candidates are listed in each DESIGN_M*.md; only cite what you actually read.
- [ ] Merge `DESIGN_LOG.md` with your own earlier entries, real dates, your own words.
- [ ] Read every file you will be asked about. In the oral check you must explain any function and any number.

## M1
- [ ] Download the five published traces into `traces_published/`, run
      `python tools/m1_measure.py --traces traces_published --out results_m1`, paste §2 into REPORT_M1.md, and recheck the trace 3 paragraph and the "which policy" choice against the real numbers.
- [ ] Replace the page dump in DESIGN_M1.md §3 with one from your own `db` (commands are there).
- [ ] Ask on the forum whether `scan --json` stats should include the catalog reads (DESIGN_M1.md §6).

## M2
- [ ] Find the published plan JSON schema and sample plans; run each sample with
      `python exec.py run --db <db> --plan <file> --frames 4 --json`. If a sample uses a field name I guessed differently (e.g. `limit`'s count), it is a one-line change in `execution/plan.py`.
- [ ] Optional: rerun `python tools/m2_measure.py --out results_m2` (numbers will be identical; only wall times change).

## M3
- [ ] Compile against the published sample catalog and compare with the published logical plan schema.

## M4
- [ ] Load the published dataset, ANALYZE every table, put the twenty queries in a folder, and run
      `python tools/m4_measure.py --db <db> --workload <dir> --frames 4 --out results_m4`.
      REPORT_M4.md is currently built on a **stand-in** dataset (it says so at the top): replace the tables and redo §4 on the real correlated columns.
- [ ] Re-run the four-table DP table in DESIGN_M4.md §5.2 on a real query.

## Integration
- [ ] For the hidden workload: `python dbms.py --db <db> --frames 32 --file queries.sql`, or EXPLAIN ANALYZE per query for est vs actual at the root.
- [ ] ARCHITECTURE.md "what I would do differently": rewrite in your own words.
- [ ] Tag each checkpoint commit (`git tag m1`, `m2`, … then `git push --tags`).
