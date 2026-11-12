# REPORT_M1

Machine / OS / Python version: _(fill in: for example `python --version`, CPU, RAM)_

Everything below must come from running `python tools/m1_measure.py --traces <folder with the published traces> --out results_m1`.
Do not edit numbers by hand. Paste the two tables from `results_m1/REPORT_M1_tables.md`.

## 1. Records per page: by hand versus measured
_(paste Table 1)_

Show your hand calculation for each schema here (do it yourself, then compare with the script):
- INT only: record size = ___ bytes, slot = 4, so floor((4096 - 8) / (___ + 4)) = ___
- mixed schema: ...
- long VARCHAR: ...

**Explain every gap.** Points to check against your own numbers:
1. The hand formula uses the 8-byte header and a 4-byte slot per record; did you include both?
2. For VARCHAR columns the declared maximum and the real length differ. Which did your hand calculation use, and how
   far off is it from the measured value?
3. Free bytes left on a full page are too few for one more record plus its slot. How many are left in your table?
4. The "overall" figure divides by all pages including the last, partly filled one.

## 2. Hit rates for every trace, both policies, four pool sizes
_(paste Table 2)_ Pool sizes used: ___ (say why you chose them relative to each trace).

## 3. The repeated sequential scan
- Which published trace is it, and what does the table show for LRU at each pool size?
- **Why** (explain in terms of which page LRU evicts just before it is needed again): ___
- What does CLOCK do on the same trace in your table? Why?
- **Which policy would you ship, and why?** (Consider hit rates on all five traces, cost per access, and
  that neither policy as implemented fixes sequential flooding. Name one remedy you read about.)
- Where does each policy fail? (one sentence each, with a trace as evidence)

## 4. Sources
1. ___ : what it changed in my design: ___
2. ___ : what it changed in my design: ___
