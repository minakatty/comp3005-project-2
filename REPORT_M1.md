# REPORT_M1 — Storage engine measurements

Machine: *fill in (CPU, RAM, OS)*. Language: Python *3.x.y* (`python --version`).
Every number below comes from `python tools/m1_measure.py --traces traces_published
--out results_m1`, which writes `results_m1/REPORT_M1_tables.md`. Page counts are
deterministic, so they are the same on any machine.

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

> **Placeholder.** The table in `results_m1/REPORT_M1_tables.md` must be produced from
> the **five published traces** (`traces_published/`). The numbers that are here now
> came from `tools/make_demo_traces.py`, synthetic traces I made only so the script
> could run; only trace 3's shape (an 80-page file scanned ten times) matches the spec.
> Replace the whole table, and recheck every sentence below against it.

Hit rate = hits / fetches, both policies, B = 4, 16, 64, 256 (twenty rows per policy):
paste `results_m1/REPORT_M1_tables.md` §2 here.

### Trace 3: a repeated sequential scan of 80 pages

Measured: at B = 64, **LRU achieves a hit rate of 0**: 800 fetches, 800 misses, 736
evictions. (With the synthetic trace 3 CLOCK also gets 0; recheck with the published
one.) At B = 256 the file fits and both get 720 / 800 = 0.90, the ten passes minus the
first.

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

**Which policy I would ship.** *Decide from the published table; my reasoning so
far:* CLOCK. On every trace in my runs it is within a few percent of LRU (equal on the
uniform and looping traces, slightly worse on the skewed one), and it costs one bit
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

*Your two sources, and what each changed in the design (handout §8): see
DESIGN_M1.md §10 for candidates.*
