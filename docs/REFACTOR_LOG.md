# Refactor log (engine -> library), part of the checkpoint 1 grade
Handout 1.3 item 7: say which of the 25 Bonus 1 cases changed meaning under bag semantics, and why.

| date | what I did | result |
|---|---|---|
| ___ | Applied the refactor patch to the Bonus 1 code (provider, plan_json, distinct, parse_condition) | 92 tests pass |
| ___ | Re-ran the 25 required cases | only case 23 changed |

**Cases that changed meaning:** case 23 (`project[DID](Employees)`). Under sets it returned 2 tuples; under bags
`project` keeps duplicates and returns 3. The test now asks for `distinct(project[DID](Employees))`, which still
returns 2. No other of the 25 cases changed. (Relation bodies loaded from definition files are still sets,
per Bonus 1 section 4.1; tables from the storage engine can hold duplicates.)

**Item by item** (handout 1.3): 1 layers separated, nothing in `ralang/` prints; 2 `parse_condition`, `parse_attr`;
3 qualified names carried by `Schema`; 4 `plan_json.to_json/from_json` with a round-trip test for every node
type; 5 operators read through a provider (`DictProvider`, `StorageProvider`); 6 bags: `project` keeps
duplicates, new `distinct`, `all` flag on union/intersect/minus; 7 test suite kept.

_Add your own dated entries for anything that went wrong while you did this._
