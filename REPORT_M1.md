# REPORT_M1

Machine / OS / Python version: ___ (for example `python --version`)

All numbers come from `python tools/m1_measure.py --traces traces_published --out results_m1`. Paste the tables from
`results_m1/REPORT_M1_tables.md`; do not edit numbers by hand.

## 1. Capacity: by hand versus measured
_(paste Measurement 1)_

By hand (show the arithmetic yourself): records per page = floor(4088 / (L + 4)).
- all INT, L = 12: floor(4088 / 16) = ___
- short VARCHAR, L = 4 + (2 + 8) + 4 = 18: floor(4088 / 22) = ___
- VARCHAR(200) holding 150 bytes, L = 4 + 2 + 150 = 156: floor(4088 / 160) = ___

Explain every difference. Things to check in your own table:
1. The "overall" figure divides by all data pages including the last, partly filled page.
2. Bytes left over on a full page are fewer than one more record plus its slot (how many are left in each row?).
3. The "declared max" column shows what you would get by assuming every VARCHAR is full length; why is it lower?
4. Does the measured number on a full page equal the hand number? If not, find the extra bytes.

## 2. Buffer pool policies
_(paste Measurement 2: 5 traces x B = 4, 16, 64, 256, both policies)_

**Trace 3 (80-page file scanned 10 times) at B = 64.** LRU hit rate: ___. Mechanism in two sentences: ___
(Hint: which page does LRU evict just before the scan comes back to it, and what is the pool full of?)
CLOCK on the same trace: ___ . Why: ___
**Which policy would you ship, and why?** ___ (use the hit rates on all five traces, the cost of each policy per access,
and the fact that both lose on a repeated sequential scan larger than the pool.)
Where each policy fails, with a trace as evidence: LRU ___ ; CLOCK ___

## 3. Scan cost
_(paste Measurement 3)_ A scan of a 500-data-page table reads ___ pages at B = 2, 8 and 64. Explain the number
(500 data pages plus the directory page) and why it does not fall as B grows: nothing is read twice in one scan, so there
is nothing for a larger pool to cache. If you see it fall, say what was cached.

## 4. Sources
1. ___ : what it changed in my design: ___
2. ___ : what it changed in my design: ___
