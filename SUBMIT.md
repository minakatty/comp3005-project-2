# Checkpoint 1: assemble, finish, check, submit

Checkpoint 1 = engine refactor + M1 storage engine (20% of the grade, ~35 hours). The handout says each checkpoint is
**a tagged commit, the module's documents, and a 15-minute oral check**. Things I could not see from the handout
(tag name, repo visibility, how to hand in the link) are marked **CHECK BRIGHTSPACE**.

## Step 1. Assemble the project repo (PowerShell)
Keep git repos out of OneDrive (sync conflicts corrupt `.git` folders). Use `C:\dev`.
```powershell
mkdir C:\dev\project2 ; cd C:\dev\project2
git init

$kit   = "C:\path\where\you\unzipped\proj2"                       # the folder containing storage\, tools\, tests\
$bonus = "C:\Users\minak\OneDrive\comp3005\week1\comp3005-bonus-project-1"

Copy-Item "$kit\*" -Destination . -Recurse                        # storage, tools, tests, docs, README, DESIGN_M1, REPORT_M1...
Copy-Item "$bonus\ralang" -Destination . -Recurse                 # the refactored engine
Copy-Item "$bonus\ra.py", "$bonus\GRAMMAR.md" -Destination .
Copy-Item "$bonus\data" -Destination . -Recurse
Copy-Item "$bonus\tests\*" -Destination .\tests\ -Recurse         # your Bonus 1 tests + test_week1
Copy-Item "$bonus\tools\*" -Destination .\tools\ -Recurse         # your gen_data.py / bench.py

python -m unittest discover -s tests
```
Expected: all tests pass (about 109 = your 92 + 17 new; none skipped, because `storage` is now importable).
If a test fails, stop and paste the output to me.

## Step 2. Get the real traces and make the report tables
1. Download the published traces from Brightspace into `traces_published\` (one page id per line, `.txt`).
   If they look different (extra columns, a header), tell me before going on.
2. Choose four pool sizes. Rule of thumb: relative to each trace's distinct page count, include one size well below,
   one near, and one above it. The default is `4 8 16 32`.
3. Run:
   ```powershell
   python tools\m1_measure.py --traces traces_published --out results_m1 --sizes 4 8 16 32
   ```
   This prints and saves both tables (`results_m1\REPORT_M1_tables.md`, `results_m1\trace_results.csv`).
   The handout wants **all five traces**, but only three are published; the two hidden ones are run by the instructor.
   Report the published ones, and say so in the report.

## Step 3. Write the parts that must be yours
- `REPORT_M1.md`: paste the two tables, do the hand calculations, explain every gap, explain the sequential trace,
  and **say which policy you would ship**. Prompts are in the file.
- `DESIGN_M1.md`: read the AI-written draft against the code, then rewrite section 7 (your reasoning) and section 8 (two sources:
  what you read and one concrete thing it changed).
- `docs\REFACTOR_LOG.md`: add dates and anything that actually went wrong.
- `GRAMMAR.md`: add `distinct "(" Query ")"` to `Primary` and one line about the soft keyword.
- Read `docs\STUDY_NOTES_M1.md` and be able to answer every question aloud.

## Step 4. Checklist against the handout (2.5 and 1.3)
- [ ] Working code behind the five commands: `create-table`, `load`, `scan`, `layout`, `trace` (`--json` on each)
- [ ] `DESIGN_M1.md` with the byte-level diagram of header, slot directory and record, free-space strategy, reasoning
- [ ] `REPORT_M1.md` with the two measurement tables and the explanations
- [ ] Tests, including the test that **evicting a pinned page fails** (`tests/test_m1.py::test_evicting_pinned_page_fails`)
- [ ] Refactor: layers separate and nothing prints; `parse_condition` exposed; qualified names; JSON round trip for every
      node type; operators detached from in-memory data; bags (project keeps duplicates, `distinct` separate);
      the 25 Bonus 1 cases still pass, with the changed one (case 23) explained in `docs\REFACTOR_LOG.md`
- [ ] At least two cited sources, each with what it changed in the design
- [ ] `--frames` is honoured: `storage scan --frames 8` never holds more than 8 pages (the pool enforces it)

## Step 5. Push to GitHub and tag
1. On github.com create a new **empty** repository, for example `comp3005-project-2` (no README, no .gitignore).
   **CHECK BRIGHTSPACE**: whether it must be public or shared with the instructor, and the required repo/tag naming.
2. Then:
   ```powershell
   cd C:\dev\project2
   git add -A
   git commit -m "Checkpoint 1: engine refactor and M1 storage engine"
   git branch -M main
   git remote add origin https://github.com/minakatty/comp3005-project-2.git
   git push -u origin main
   git tag checkpoint-1                      # CHECK BRIGHTSPACE for the exact tag name
   git push origin checkpoint-1
   ```
3. Verify from a clean copy, which is what the instructor will do:
   ```powershell
   cd $env:TEMP ; git clone https://github.com/minakatty/comp3005-project-2.git verify ; cd verify
   python -m unittest discover -s tests
   python -m storage --db db create-table --name R --schema "a:INT,b:VARCHAR(20),c:INT"
   ```
4. Submit the repository link (and tag if asked) on Brightspace. **CHECK BRIGHTSPACE** for where checkpoint 1 is submitted and the deadline.

## Step 6. The oral check
Be ready to run `storage trace` and `storage layout` on a file you have not seen, and to explain the output. Use `docs\STUDY_NOTES_M1.md`.
If you change code after tagging, move the tag: `git tag -f checkpoint-1` then `git push -f origin checkpoint-1`.

## If something fails
| symptom | fix |
|---|---|
| `ModuleNotFoundError: storage` | run commands from the repo root (`C:\dev\project2`) |
| `python` not found | use `py -3` instead of `python` |
| `PinnedPageError` / `BufferPoolFull` | not a crash in the tools; it means more pages are pinned than `--frames` allows. Raise `--frames` |
| `catalog exceeds one page` | too many tables in one database file (known limit; see DESIGN_M1.md section 4) |
