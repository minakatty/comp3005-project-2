# DESIGN_M1: Storage engine and buffer pool

Code: `storage/`. Every byte offset below is the one the M1 spec requires; the
sections marked "my decision" are the places the spec left to me.

## 1. Files and addressing

One file per table, `<table>.db`, plus `catalog.db`, all in the `--db` directory.
A file is a sequence of 4096-byte pages and nothing else. A page id is the page's
zero-based index in its file; a rid is `(page_id, slot)`. All integers are
little-endian. `storage/disk.py` is the only module that opens a file, and every
`read_page` / `write_page` is counted.

Because pages from several files share one buffer pool, the pool's page table is
keyed by `(file, page_id)`. `BufferPool.fetch_page(disk, page_id)` is the
contract's `fetch_page(page_id)` with the file made explicit.

## 2. Record format

Concatenation of the field encodings in schema order. No header, no null bitmap.

```
INT          4 bytes, signed, two's complement
VARCHAR(n)   2-byte unsigned length L, then L bytes of UTF-8 (L <= n <= 255)
```

Spec example, schema `(a INT, b VARCHAR(5), c INT)`, tuple `(42, 'bob', 7)`:
`2A 00 00 00 | 03 00 62 6F 62 | 07 00 00 00` = 13 bytes (`tests/test_m1.py::RecordFormat`).

## 3. Slotted data page

```
offset  size  field
     0     4  page_id       uint32
     4     2  slot_count    uint16   includes dead slots
     6     2  free_end      uint16   lowest byte used by record data (4096 when empty)
     8     4  slot 0        uint16 offset, uint16 length
    12     4  slot 1
   ...
              free space = free_end - (8 + 4 * slot_count)
free_end      record data, newest record at the lowest offset
  4095        end of page
```

A slot `(0, 0)` is dead. Slots are never reused or renumbered, so a rid stays valid
for the life of the record. An insert of L bytes needs `L + 4` free bytes.

### A real page from my own files

Produced by
`python -m storage create-table --db db --name R --schema "a:INT,b:VARCHAR(5),c:INT"`,
`python -m storage load --db db --table R --file data/sample_r.csv`,
`python -m storage layout --db db --table R --page 1 --hex`.
`data/sample_r.csv` holds `42,bob,7`, `43,amy,9` and `44,"o,k",11`. The bytes are the
same on every machine (the format fixes every byte).

```
data page 1
  header   [0..7]     page_id=1 slot_count=3 free_end=4057
  slots    [8..19]     3 slots x 4 bytes
  free     [20..4056]   4037 bytes
  records  [4057..4095]  39 bytes (0 of them dead)
    slot 0    @8     (4083,13)  bytes 4083..4095
    slot 1    @12    (4070,13)  bytes 4070..4082
    slot 2    @16    (4057,13)  bytes 4057..4069
  header+slots hex:
    0000  01 00 00 00 03 00 D9 0F F3 0F 0D 00 E6 0F 0D 00
    0016  D9 0F 0D 00
  record area hex:
    4057  2C 00 00 00 03 00 6F 2C 6B 0B 00 00 00 2B 00 00
    4073  00 03 00 61 6D 79 09 00 00 00 2A 00 00 00 03 00
    4089  62 6F 62 07 00 00 00
```

Reading it byte by byte:

| bytes | hex | meaning |
|---|---|---|
| 0–3 | `01 00 00 00` | page_id = 1 (page 0 is the directory) |
| 4–5 | `03 00` | slot_count = 3 |
| 6–7 | `D9 0F` | free_end = 0x0FD9 = 4057 |
| 8–11 | `F3 0F 0D 00` | slot 0 = (4083, 13) |
| 12–15 | `E6 0F 0D 00` | slot 1 = (4070, 13) |
| 16–19 | `D9 0F 0D 00` | slot 2 = (4057, 13) |
| 4083–4095 | `2A 00 00 00 03 00 62 6F 62 07 00 00 00` | (42, 'bob', 7), the first record, at the end of the page |
| 4070–4082 | `2B 00 00 00 03 00 61 6D 79 09 00 00 00` | (43, 'amy', 9) |
| 4057–4069 | `2C 00 00 00 03 00 6F 2C 6B 0B 00 00 00` | (44, 'o,k', 11): the comma is data, the CSV loader unquoted it |

free space = 4057 − (8 + 4 × 3) = 4037, which is what the directory entry for this
page records.

### Section 3.2 reproduced

`python -m storage page-demo` prints the spec's table, and
`tests/test_m1.py::SlottedPageSpec::test_section_3_2_worked_example` checks every cell:

```
after            slot_count  free_end  free space  slot directory
empty page                0      4096        4088  empty
insert 13 bytes           1      4083        4071  0:(4083, 13)
insert 20 bytes           2      4063        4047  1:(4063, 20)
insert 13 bytes           3      4050        4030  2:(4050, 13)
delete slot 1             3      4050        4030  1:(0, 0)
```

### Largest record

The spec says a record longer than 4088 bytes cannot be stored. An insert needs
`L + 4 <= 4088`, so the real limit is **4084** bytes; my error fires above 4084 and
names the table and the row (`HeapFile.insert_bytes`). With VARCHAR capped at 255 a
schema would need more than fifteen VARCHAR(255) columns to reach it, so
`create_table` rejects such a schema up front.

## 4. Free space strategy and compaction (my decision)

**Free space directory.** The heap file is the spec's linked list of directory pages
(page 0 first, 681 entries of `(data_page_id uint32, free_bytes uint16)` each, then
`next_dir_page`). Insert is first fit over the directory entries: it reads directory
pages, never data pages, until it finds an entry with `free_bytes >= L + 4`, so an
insert into a table of P data pages touches `ceil(P / 681)` directory pages plus
exactly one data page. When no entry has room it allocates a new data page at the
end of the file, and when the last directory page is full it chains a new one.

**What `free_bytes` means.** The directory stores a page's *usable* free space:
its contiguous free space plus the bytes of deleted records. That is why delete
updates the directory, as the spec's delete does.

**Compaction runs only when an insert needs it.** Delete never moves data (so the
3.2 example is unchanged: deleting the 20-byte record frees nothing at once). When
an insert picks a page whose usable free space is enough but whose contiguous free
space is not, `SlottedPage.compact()` slides the live records to the end of the
page, keeps every slot number, keeps the dead slots' 4 directory bytes, and the
insert then succeeds. Rids stay valid because slots are not renumbered.

Why then and not at delete: compaction rewrites up to a whole page; doing it on
every delete would pay that for space nobody may ever need, and the spec's 3.2
example shows delete as reclaiming nothing. Doing it lazily costs nothing extra in
I/O, because the page is already pinned and dirty for the insert.

Before and after (`tests/test_m1.py::test_compaction_keeps_rids_and_reclaims_space`
does the same with larger records):

```
before: three inserts of 13, 20, 13 bytes, delete slot 1
  header   page_id=1 slot_count=3 free_end=4050
  free     [20..4049]   4030 bytes
  records  [4050..4095]  46 bytes (20 of them dead)
    slot 0  (4083,13)
    slot 1  (0,0)  dead
    slot 2  (4050,13)
after compact()
  header   page_id=1 slot_count=3 free_end=4070
  free     [20..4069]   4050 bytes
  records  [4070..4095]  26 bytes (0 of them dead)
    slot 0  (4083,13)
    slot 1  (0,0)  dead
    slot 2  (4070,13)        <- moved 20 bytes up, same slot number
```

## 5. Buffer pool

`storage/bufferpool.py`. B frames, each with the page bytes, a pin count and a
dirty flag; a page table `(file, page_id) -> frame`.

- `fetch_page`: a resident page is a **hit** (pin count + 1). Otherwise a free frame
  is used if there is one, else the policy chooses a victim among frames with pin
  count 0; a dirty victim is written first. The read is a **miss**, and an
  **eviction** only if a resident page was replaced, so the first B misses into an
  empty pool are not evictions.
- No unpinned frame: `BufferPoolError("no unpinned frame available ...")`. A pinned
  page is never evicted (`test_evicting_pinned_page_fails`).
- `unpin_page` at pin count 0, or of a page that is not resident: error
  (`test_unpin_at_zero_fails`).
- `new_page`: allocates a page id in the file, zero filled, pinned, dirty.
- `flush_all`: writes every dirty frame, counted as writes.

**LRU** is an `OrderedDict` of frame indexes; every fetch, hit or miss, moves the
frame to the most-recently-used end, and the victim is the first unpinned frame
from the least-recently-used end.

**CLOCK** keeps a reference bit per frame and a hand. Every fetch sets the bit;
loading a page into a frame sets its bit and moves the hand just past it.
`choose_victim` is the spec's loop exactly: skip pinned frames, clear a set bit and
move on, take the first unpinned frame with bit 0, give up after `2 × B` steps.

Section 5.3 (B = 3, trace 1 2 3 1 4 2) reproduces for both:
LRU 1 hit / 5 misses / 2 evictions, CLOCK 2 hits / 4 misses / 1 eviction, and the
final pools are [1,4,2] and [4,2,3] (`tests/test_m1.py::BufferPoolSpec`).

**Module 2 extension: reserved frames.** `reserve(k)` takes k frames out of
circulation for an operator's working memory and `release` gives them back. A
reserved frame is never a victim. Because pinned pages and reserved frames come
from the same B frames, a query plan can never hold more than B pages, and
`peak_held` records the maximum it did. DESIGN_M2.md builds on this.

## 6. Catalog page format (my decision)

`catalog.db` is an ordinary heap file (directory page 0, slotted data pages), read
and written through the buffer pool. Its records have a fixed schema:

```
kind   VARCHAR(16)    "table"; Module 4 adds "stats"
name   VARCHAR(64)    the table name
seq    INT            chunk number 0, 1, 2 ...
chunk  VARCHAR(200)   a slice of the entry's JSON text
```

One entry is a JSON object cut into as many 200-character chunks as it needs, so an
entry can grow (a 20-bucket histogram per column) without changing the page format.
A table entry:

```
{"name":"R","columns":[["a","INT",0],["b","VARCHAR",5],["c","INT",0]],
 "file":"R.db","first_page":0,"num_pages":1,"num_tuples":3}
```

It holds everything the spec lists: name, ordered columns with type and length, data
file, number of (data) pages, number of live tuples. Module 4 statistics are stored
as `kind = "stats"` entries in the same file ("leave room": done without any change).
The catalog is read into a dictionary once when the database opens; an update
deletes the entry's old records and inserts new ones, and compaction (Section 4)
reuses the space.

**Scan counters and the catalog.** Opening the database reads the catalog through
the pool (2 page reads for a fresh catalog). `scan --json` resets the counters after
that, so its stats describe the scan only; the catalog reads are reported separately
as the extra field `catalog_reads`.
**Assumption:** `scan --json` stats exclude the catalog reads made when the database opens.

## 7. Scan

`HeapFile.scan()` reads a directory page, copies its entry list, **unpins it**, and
then fetches each data page in turn, yielding its live records while it is pinned
and unpinning it before the next. So a scan holds one frame between yields (at most
one directory page and one data page at any instant, as the spec requires), and
closing the generator early unpins the current page (`finally`).
`test_scan_20x_B_pins_at_most_two` scans 40 pages with B = 2: peak frames held 1,
reads 41 (40 data pages + 1 directory page).

## 8. Loader

`Database.load_csv` uses Python's `csv` module (allowed: it is not a lexer for the
query language) with no header line, double-quoted fields for embedded commas. The
whole file is validated first and only then inserted, so a bad line loads nothing.
Errors name the line and the column: `line 3, column a: 'x3' is not an INT`,
`line 1, column b: value is 4 bytes, longer than VARCHAR(3)`.

## 9. Errors (spec Section 9)

| case | where | test |
|---|---|---|
| evicting a pinned page | `BufferPool._grab_frame` | `test_evicting_pinned_page_fails` |
| unpin at pin count 0 | `BufferPool.unpin_page` | `test_unpin_at_zero_fails` |
| reading a dead slot, naming the rid | `SlottedPage.get` | `test_dead_slot_read_names_rid` |
| record too long, naming table and row | `HeapFile.insert_bytes` | `test_record_too_long_names_table_and_row` |
| bad CSV field, naming line and column | `Database._parse_csv` | `test_bad_csv_int_names_line_and_column` |

The CLI catches `StorageError` once and prints `error: <message>`; there is never a
stack trace.

## 10. Assumptions (one line each)

- Traces: the course traces are not published, so I use my own five (`traces/`, REPORT_M1.md §2); trace1 is the spec's §5.3 trace.
- Scan stats exclude catalog reads (§6).
- The largest storable record is 4084 bytes, the most an insert needing L + 4 ≤ 4088 can hold (§3).

## 11. Sources

- **Ramakrishnan and Gehrke, ch. 9 (heap files and page formats).** Two heap-file
  organisations are compared there: a linked list of data pages, and a directory of
  pages whose entries hold each page's free space. The second is the spec's layout, and
  R&G's argument for it (an insert reads directory pages, not data pages, to find room)
  is §4's first-fit strategy. Their point that a slotted page can move a record inside
  the page without changing its rid, because the rid names the slot, is what makes
  compaction with stable slot numbers safe.
- **Petrov, *Database Internals*, ch. 3 (slotted pages).** Petrov describes slotted
  pages that leave deleted records in place and defragment the page later, when space is
  actually needed. That is the policy in §4: compact on the insert that needs the
  space, never on delete, which also keeps the spec's §3.2 example exact.
