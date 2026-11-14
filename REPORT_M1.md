# REPORT_M1: Storage engine measurements

Machines: the tables were produced on Linux (Intel Xeon 2.10 GHz, 2 cores, 7 GB RAM, Python 3.13.18) and reproduced on my Windows PC (8 logical processors, Python 3.13.14). Page counts, hit counts and comparisons are deterministic and identical on both; only wall-clock seconds differ.

Every number below comes from `python tools/m1_measure.py --traces traces --out results_m1`,
which writes `results_m1/REPORT_M1_tables.md`.

## 1. Capacity: computed against measured

Computed from DESIGN_M1.md §3: a record of L bytes needs L + 4 bytes (record + slot),
a page has 4096 − 8 = 4088 usable bytes, so records per page = floor(4088 / (L + 4)).
Measured: load 3000 rows, then read `slot_count` of every full data page.

| schema | columns | record bytes L | computed floor(4088/(L+4)) | measured per full page | data pages for 3000 rows |
|---|---|---|---|---|---|
| all INT | `a:INT,b:INT,c:INT` | 12 | 255 | 255 | 12 |
| short VARCHAR | `a:INT,b:VARCHAR(5),c:INT` | 13 | 240 | 240 | 13 |
| VARCHAR(200) | `a:INT,s:VARCHAR(200)` | 206 | 19 | 19 | 158 |

The short-VARCHAR row is the spec's L = 13 → 240 (M1 spec 3.3).

**The gap is zero, and why.** Records are packed back to back with no alignment and
no record header, and the slot directory costs exactly 4 bytes per record, so the
page holds exactly floor(4088 / (L+4)) records; the remainder (4088 mod (L+4) bytes:
8, 8 and 2 bytes here) is too small for one more record and stays free on every
full page. Two things would open a gap, and neither happens in these loads: variable
lengths (with VARCHAR values of different lengths, L is an average and the
first-fit loader can leave a gap at the end of a page that a later, shorter record
would have fitted), and deletes (dead records keep their bytes until compaction,
DESIGN_M1.md §4). The table count also confirms the directory page is not a data
page: 3000 / 255 = 11.8 → 12 data pages, plus page 0.

## 2. Replacement policies

**Assumption:** the course traces are not published, so the five traces are my own
(`traces/`, written by `tools/make_traces.py`, fixed seed), modelled on the spec.

| trace | what it is | what it tests |
|---|---|---|
| trace1 | 1 2 3 1 4 2, the M1 §5.3 worked trace | correctness: at B = 3, LRU 1 hit / 5 misses / 2 evictions, CLOCK 2 / 4 / 1 (`python -m storage trace --file traces/trace1.txt --frames 3 --policy lru --json`) |
| trace2 | 5000 fetches, 80 % to 20 hot pages, 20 % to 400 cold ones | ordinary locality |
| trace3 | an 80-page file scanned sequentially ten times (the spec's trace 3) | sequential flooding |
| trace4 | 5000 fetches uniform over 200 pages | no locality at all |
| trace5 | bursts on 10 hot pages, each followed by a one-pass scan of 100 new pages | does a scan flush the hot set? |

Hit rate = hits / fetches, both policies, B = 4, 16, 64, 256:

| trace | policy | B | hits | misses | evictions | hit rate |
|---|---|---|---|---|---|---|
| trace1.txt | lru | 4 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | lru | 16 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | lru | 64 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | lru | 256 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 4 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 16 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 64 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 256 | 2 | 4 | 0 | 0.3333 |
| trace2.txt | lru | 4 | 597 | 4403 | 4399 | 0.1194 |
| trace2.txt | lru | 16 | 2284 | 2716 | 2700 | 0.4568 |
| trace2.txt | lru | 64 | 4070 | 930 | 866 | 0.8140 |
| trace2.txt | lru | 256 | 4474 | 526 | 270 | 0.8948 |
| trace2.txt | clock | 4 | 591 | 4409 | 4405 | 0.1182 |
| trace2.txt | clock | 16 | 2175 | 2825 | 2809 | 0.4350 |
| trace2.txt | clock | 64 | 4038 | 962 | 898 | 0.8076 |
| trace2.txt | clock | 256 | 4476 | 524 | 268 | 0.8952 |
| trace3.txt | lru | 4 | 0 | 800 | 796 | 0.0000 |
| trace3.txt | lru | 16 | 0 | 800 | 784 | 0.0000 |
| trace3.txt | lru | 64 | 0 | 800 | 736 | 0.0000 |
| trace3.txt | lru | 256 | 720 | 80 | 0 | 0.9000 |
| trace3.txt | clock | 4 | 0 | 800 | 796 | 0.0000 |
| trace3.txt | clock | 16 | 0 | 800 | 784 | 0.0000 |
| trace3.txt | clock | 64 | 0 | 800 | 736 | 0.0000 |
| trace3.txt | clock | 256 | 720 | 80 | 0 | 0.9000 |
| trace4.txt | lru | 4 | 97 | 4903 | 4899 | 0.0194 |
| trace4.txt | lru | 16 | 397 | 4603 | 4587 | 0.0794 |
| trace4.txt | lru | 64 | 1578 | 3422 | 3358 | 0.3156 |
| trace4.txt | lru | 256 | 4800 | 200 | 0 | 0.9600 |
| trace4.txt | clock | 4 | 98 | 4902 | 4898 | 0.0196 |
| trace4.txt | clock | 16 | 394 | 4606 | 4590 | 0.0788 |
| trace4.txt | clock | 64 | 1560 | 3440 | 3376 | 0.3120 |
| trace4.txt | clock | 256 | 4800 | 200 | 0 | 0.9600 |
| trace5.txt | lru | 4 | 758 | 2242 | 2238 | 0.2527 |
| trace5.txt | lru | 16 | 1900 | 1100 | 1084 | 0.6333 |
| trace5.txt | lru | 64 | 1900 | 1100 | 1036 | 0.6333 |
| trace5.txt | lru | 256 | 1990 | 1010 | 754 | 0.6633 |
| trace5.txt | clock | 4 | 748 | 2252 | 2248 | 0.2493 |
| trace5.txt | clock | 16 | 1900 | 1100 | 1084 | 0.6333 |
| trace5.txt | clock | 64 | 1900 | 1100 | 1036 | 0.6333 |
| trace5.txt | clock | 256 | 1970 | 1030 | 774 | 0.6567 |

**Reading the table.**
- *trace1* fits in every pool from B = 4 up, so 2 hits (the second 1 and the second 2)
  and no evictions; the B = 3 run above is the spec's check.
- *trace2*: LRU is slightly ahead of CLOCK at every B (0.457 against 0.435 at B = 16),
  because LRU's exact recency keeps the 20 hot pages slightly better than one reference
  bit can; at B = 256 the whole hot set and most cold pages fit and both reach ≈ 0.895.
- *trace4*: with no locality both policies get the hit rate of pure chance, B / 200
  (0.02, 0.08, 0.32), and 0.96 once all 200 pages fit. No policy can do better here.
- *trace5*: both reach 0.633 at B = 16 and 64, which is exactly 1900 / 3000: every
  hot-set fetch except the first ten of each burst is a hit, and every scan page misses.
  The scan *does* flush the hot set: after each 100-page scan the 10 hot pages are gone
  even at B = 64, so each burst starts with 10 misses again. Only at B = 256 do some hot
  pages survive a scan (0.663).

### Trace 3: a repeated sequential scan of 80 pages

Measured: at B = 64, **LRU achieves a hit rate of 0**: 800 fetches, 800 misses, 736
evictions. CLOCK also gets 0. At B = 256 the file fits and both get 720 / 800 = 0.90,
every pass after the first.

**Mechanism, in two sentences.** When the 65th page of a pass is fetched, the least
recently used page is page 0, exactly the page the next pass needs first; LRU evicts
it, and every later fetch in the loop evicts the page that will be wanted soonest, so
each pass misses on every page (sequential flooding). LRU's assumption that recently
used pages will be used again soon is exactly backwards for a loop larger than the
pool: the right victim would be the *most* recently used page, which will not be
needed for another 79 fetches.

CLOCK does not fix this either: it approximates LRU, every frame's reference bit is
set by the time the hand comes round, so the sweep clears them all and evicts in
FIFO order, which on a cyclic trace is the same choice LRU makes.

**Which policy I would ship: CLOCK.** On every trace it is within a few percent of
LRU (equal on the looping and scan traces, equal within noise on the uniform one, about
2 points behind on the hot/cold trace), and it costs one bit
and a hand move per fetch instead of a reordering of a linked list on *every* hit,
which is what a real system pays under concurrency (a hit in LRU writes shared state;
a hit in CLOCK sets one bit). Neither policy survives sequential flooding, so the
real fix is not the policy but scan resistance: give large sequential scans a small
ring of frames of their own (what PostgreSQL does), or use LRU-K / 2Q, which only
promote a page after a second reference and so never let a one-pass scan flush the
hot set (O'Neil et al. 1993).

## 3. Scan cost: a 500-page table at B = 2, 8 and 64

Table Big: 500 data pages + 1 directory page, 120 000 rows of 13 bytes.

| B | page reads | hits | misses | evictions | max frames pinned |
|---|---|---|---|---|---|
| 2 | 501 | 0 | 501 | 501 | 1 |
| 8 | 501 | 0 | 501 | 495 | 1 |
| 64 | 501 | 0 | 501 | 439 | 1 |

Reads are 501 in every case: 500 data pages and the one directory page, each read
exactly once. **They do not fall as B grows** because a single scan never revisits a
page, so nothing is ever a hit, whatever is cached; a bigger pool only means fewer
evictions: the pool starts holding the 2 catalog pages read when the database opened,
so the first B − 2 misses fill empty frames and every other miss evicts, giving
501 − (B − 2) evictions (501, 495, 439). **They do not rise** as B shrinks, because the scan pins one page at a time
(`max frames pinned` = 1, DESIGN_M1.md §7) so even B = 2 never forces a re-read. A scan
that materialised the table would show the opposite: B = 2 could not hold it at all.

## Sources

- **O'Neil, O'Neil and Weikum, "The LRU-K Page Replacement Algorithm for Database Disk
  Buffering", SIGMOD 1993.** Its motivation is the situation of trace 3 and trace 5:
  LRU cannot tell a page referenced once by a scan from a page referenced often, so a
  scan flushes the hot set. This changed what I tested: I added trace5 to measure the
  flushing directly, and it is why my "ship" answer says the real fix is scan
  resistance (LRU-2 / 2Q, or a private ring of frames for scans), not a better
  general-purpose policy.
- **Ramakrishnan and Gehrke, *Database Management Systems*, 3rd ed., ch. 9 (the buffer
  manager and page formats).** Pin counts, dirty bits and "never replace a pinned frame"
  come from its buffer manager section, and so does the definition of cost used in my
  tables: a fetch of a resident page costs no I/O. That is why the scan-cost table
  reports reads and hits separately, and why "reads should be roughly 500 at every B" is
  the right expectation for a single scan that never revisits a page.
