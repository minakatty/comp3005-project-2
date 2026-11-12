# DESIGN_LOG (dated entries; one short entry per working session)

Replace the dates with your real ones, and add anything that went wrong while you worked. Entries below are facts about
what happened in this project; edit them into your own words.

| date | what I was trying to do | what I did / what broke |
|---|---|---|
| ___ | Turn the Bonus 1 engine into a library (handout 1.3) | Added `parse_condition`, `plan_json` (JSON round trip for every node type), `provider` (operators read rows from a provider), bag semantics (`project` keeps duplicates, new `distinct` operator, `all` flag on set operators). 92 tests pass. |
| ___ | Check the 25 Bonus 1 cases after the change | Only case 23 changed meaning: `project[DID](Employees)` now returns 3 tuples (bags). The test now uses `distinct(project[DID](Employees))`, which still returns 2. The other 24 cases are unchanged. |
| ___ | Build Module 1 | First version was written from the project handout before I had the M1 spec. After reading the spec it differed in the page header, VARCHAR length (1 vs 2 bytes), slot reuse, heap directory pages, one file per table, LRU timing and CLOCK rules, so I rewrote it to the spec. Lesson: build from the module spec, not the overview. |
| ___ | Verify against the spec's own examples | Spec 3.2 (page after inserts of 13, 20, 13 bytes and a delete) and 5.3 (LRU and CLOCK on trace 1 2 3 1 4 2) are unit tests; both reproduce exactly. |
| ___ | Write the unit tests | Two of my first tests failed because of mistakes in the tests, not the engine: wrong records-per-page arithmetic in the directory-chain test (196-byte records give 20 per page, not 19), and a scan test that was not cold (one page was still cached, so reads were one lower). Fixed the tests. |
| ___ | Measurements | Ran `tools/m1_measure.py` on the published traces. (add what you found) |
