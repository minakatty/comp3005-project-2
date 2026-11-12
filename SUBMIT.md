# Checkpoint 1: assemble, finish, check, submit

Checkpoint 1 = engine refactor + Module 1. Per the handout each checkpoint is a **tagged commit**, the module's documents and a
**15-minute oral check**. Items I could not see (tag name, repo visibility, where to hand in the link) are marked **CHECK BRIGHTSPACE**.

## Step 1. Replace the old storage code with the new kit (PowerShell)
```powershell
cd C:\dev\project2
Remove-Item -Recurse -Force storage          # the old version no longer matches the M1 spec
$kit = "C:\path\to\the\unzipped\proj2"       # folder containing storage\, tools\, tests\, docs\
Copy-Item "$kit\*" -Destination . -Recurse -Force
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
python -m unittest discover -s tests
```
Expected: about 130 tests, all OK, none skipped (your 92 + 38 new). Your `ralang\`, `ra.py`, `GRAMMAR.md`, `data\` are untouched.

## Step 2. The traces and the report numbers
1. Put the five published trace files from Brightspace in `traces_published\`.
2. `python tools\m1_measure.py --traces traces_published --out results_m1`
3. Open `results_m1\REPORT_M1_tables.md`: it has all three required measurements (capacity, policies at B = 4/16/64/256, 500-page scan at B = 2/8/64).

## Step 3. Write the parts that must be yours
- `REPORT_M1.md`: paste the tables, show the hand calculations, explain every difference, explain trace 3 at B = 64, say which policy you would ship.
- `DESIGN_M1.md`: replace the example page with a dump of a page from YOUR files (`python -m storage layout --db db --table R --page 1 --hex`), write section 8, add two sources and what each changed.
- `DESIGN_LOG.md`: put real dates, add what actually happened.
- `GRAMMAR.md`: add `distinct "(" Query ")"` to `Primary`.
- Read `docs\STUDY_NOTES_M1.md` and `docs\SPEC_CHECKLIST_M1.md`.

## Step 4. Checklist (spec section 10)
- [ ] Spec 3.2 page example reproduces (test `test_worked_example_3_2`)
- [ ] Spec 5.3 buffer example reproduces for both policies (tests)
- [ ] A scan of a table 20x larger than B completes, never pins more than 2 pages (test)
- [ ] Same command twice -> identical counters (test)
- [ ] Delete every record, scan returns nothing, no error (test)
- [ ] `DESIGN_M1.md`: catalog page format, free-space strategy, compaction decision, byte-level diagram of a real page
- [ ] `REPORT_M1.md`: three measurements with explanations
- [ ] `DESIGN_LOG.md` entries, tests, two cited sources
- [ ] Refactor items (handout 1.3) done; case 23 change recorded in the log

## Step 5. Push and tag
Create an **empty** repo on github.com (for example `comp3005-project-2`). **CHECK BRIGHTSPACE** for visibility and naming.
```powershell
cd C:\dev\project2
git add -A
git commit -m "Checkpoint 1: engine refactor and Module 1 storage engine"
git branch -M main
git remote add origin https://github.com/minakatty/comp3005-project-2.git
git push -u origin main
git tag checkpoint-1
git push origin checkpoint-1
```
Verify from a clean copy: `cd $env:TEMP; git clone https://github.com/minakatty/comp3005-project-2.git verify; cd verify; python -m unittest discover -s tests`.
Then submit the link on Brightspace. To move the tag after a late fix: `git tag -f checkpoint-1; git push -f origin checkpoint-1`.

## Oral check
He runs your tool on his files and asks what happened, plus one reading question. Be able to run `storage trace`, `storage layout` and
`storage scan` on a table you have not seen, and explain the numbers. Use `docs\STUDY_NOTES_M1.md`.

## If something fails
| symptom | fix |
|---|---|
| `No module named storage` | run from the repo root |
| `error: ... no unpinned frame available` | more pages pinned than `--frames` allows; raise `--frames` |
| `python` not found | use `py -3` |
