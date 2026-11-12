# DESIGN_M1: storage engine and buffer pool

> DRAFT written with AI help from the code. Read each statement against `storage/*.py`, run `storage layout --hex`
> on your own files to replace the example in section 2, and rewrite section 8 (reasoning) in your own words.
> Delete this note when done.

## 1. Files and addressing
One file per table (`<table>.db`) plus `catalog.db`, all sequences of 4096-byte pages with no header outside the
pages. A page id is the zero-based index inside its file; a rid is `(page_id, slot)`. Integers are little endian.
The buffer pool is shared by all files, so it addresses a page by one integer, `(file_no << 32) | page_id`
(file 0 is `catalog.db`). Only `storage/disk.py` touches files.

## 2. Data page (byte level)
```
offset size field
   0    4   page_id      uint32
   4    2   slot_count   uint16   includes dead slots
   6    2   free_end     uint16   lowest byte offset used by record data
   8    4   slot 0       uint16 offset, uint16 length      (offset 0 = dead)
  12    4   slot 1
  ...        <-- free space -->
free_end    record data, newest at the lowest offset
 4095       end of page
```
Free space = `free_end - (8 + 4 * slot_count)`; an insert of L bytes needs `L + 4`. Slot numbers are never reused or
renumbered. Delete only marks the slot `(0,0)`; space is **not** reclaimed.

**Compaction decision:** not implemented. The spec makes it optional; skipping it keeps rids stable, keeps free space
equal to the spec's formula after every operation, and makes the worked example in spec 3.2 reproduce exactly.

**A real page from my own files** (replace with the output of `storage layout --db DB --table R --page 1 --hex`;
this example is a table `R(a INT, b VARCHAR(20), c INT)` holding (42,bob,7), (43,amy,9), (44,"x,y",1)):
```
0000  01 00 00 00 | 03 00 | d9 0f | f3 0f 0d 00 | e6 0f 0d 00 | d9 0f 0d 00
      page_id=1     slots=3 free_end=0x0fd9=4057
                                    slot0 off=0x0ff3 (4083) len=13
                                                  slot1 off=4070 len=13
                                                                slot2 off=4057 len=13
0fd9  2c 00 00 00 03 00 78 2c 79 01 00 00 00      record 2: a=44, b="x,y" (len 3), c=1
0fe6  2b 00 00 00 03 00 61 6d 79 09 00 00 00      record 1: a=43, b="amy", c=9
0ff3  2a 00 00 00 03 00 62 6f 62 07 00 00 00      record 0: a=42, b="bob", c=7
```

## 3. Record format
`INT` = 4 bytes signed two's complement. `VARCHAR(n)` = 2-byte unsigned length L (L <= n <= 255) then L bytes of
UTF-8. Fields concatenated in schema order; no record header, no null bitmap. Record length is variable whenever a
VARCHAR is present, and nothing assumes a fixed length. Example `(42,'bob',7)` = `2A000000 0300 626F62 07000000`
= 13 bytes (a test checks this).
A record that cannot fit on an empty page (more than 4084 bytes, i.e. 4088 minus its own slot) is rejected with an error
naming the table and the row.

## 4. Heap file: directory pages and the free-space strategy
Page 0 of every table file is the first directory page; data pages and any further directory pages follow in
allocation order.
```
offset size field
   0    4   page_id        uint32
   4    4   next_dir_page  uint32     0xFFFFFFFF if none
   8    2   entry_count    uint16
  10    6   entry 0        uint32 data_page_id, uint16 free_bytes
  16    6   entry 1 ...                      capacity = floor((4096 - 10) / 6) = 681 entries
```
`free_bytes` is the page's free space as defined in section 2.
- **insert:** first fit over the directory entries (`free_bytes >= len + 4`); otherwise allocate a new data page and add an
  entry to the last directory page, chaining a new directory page when the last one holds 681 entries.
  *Implementation note:* the first-fit search runs over an in-memory mirror of the directory, built by one walk of the
  directory chain on the first insert of a session and kept in step with every change. The data page and directory page
  are still read and written through the buffer pool. Reason: walking up to 681 entries per insert in Python made bulk
  loads slow; the layout on disk is identical to a literal walk. Cost: the first insert of a session reads the directory
  pages once.
- **get:** fetch the data page, read the slot, unpin. A dead or out-of-range slot is an error naming the rid.
- **delete:** mark the slot dead. A delete does not change free space, so the directory entry stays correct and is not
  rewritten (the spec says to update it; the update would write the same number).
- **scan:** for each directory page: copy its entries, unpin it; for each entry: fetch the data page, copy its live
  records, unpin it, then yield. At most one page is ever pinned (the spec allows two). A table 20x larger than the pool
  scans fine (tested).

## 5. Catalog
`catalog.db` is a sequence of slotted pages (same layout as section 2). Each live record is one UTF-8 JSON object:
`{"name","schema","file","file_no","num_pages","file_pages","num_tuples","stats":{}}`. `num_pages` counts data pages,
`file_pages` all pages in the table file, `stats` is empty and is where Module 4 will store ANALYZE results. The catalog is
read at start-up and written at close through the buffer pool. `persist()` rewrites every catalog page, so dead slots
never accumulate, and spills to further pages when full (tested with 60 tables). The pool counters are reset right after
the catalog is loaded, so reported counters describe table access only.

## 6. Buffer pool
B frames, each with page bytes, pin count, dirty flag (and a reference bit for CLOCK), plus a page table.
- **fetch_page:** resident -> pin++ and a *hit*. Otherwise choose a victim among unpinned frames (none -> error
  "no unpinned frame available"), write it back if dirty (a *write*), read the page (a *read*), and count a *miss*; it is
  also an *eviction* only if a resident page was replaced, so the first B misses are not evictions.
- **unpin_page:** error if the page is not resident or its pin count is already 0.
- **new_page:** zero-filled, pinned, dirty. **flush_all:** writes every dirty frame and clears the flags.
- **LRU:** every fetch (hit or miss) moves the frame to most recently used; the victim is the least recently used frame whose
  pin count is 0.
- **CLOCK:** every fetch sets the reference bit; the hand skips pinned frames, clears set bits, evicts the first unpinned
  frame with the bit clear, advances past the victim, and gives up after sweeping more than 2B frames. A page loaded into an
  empty frame also moves the hand past it.
- Both policies reproduce the spec 5.3 example exactly (LRU 1/5/2, CLOCK 2/4/1, final pool [4,2,3]); tests check it.

## 7. Command line
`create-table`, `load`, `scan`, `layout [--hex]`, `trace`, each with `--json` and `--db DIR`. `scan --json` returns
`table, schema, rows, stats`; `trace --json` returns `policy, frames, hits, misses, evictions, reads, writes` (plus
`hit_rate`). `trace` runs on a counting stand-in disk, because all pages are clean, so only the policy matters. Errors print
`error: ...` on stderr with exit code 1 and never a stack trace.

## 8. Choices and reasoning (write this yourself)
- Why slotted pages with a backward-growing record area, and why slot numbers are never reused.
- Why a directory of pages rather than a linked list of data pages.
- What the in-memory directory mirror costs and why it is safe.
- Where each policy fails (see REPORT_M1.md) and what you would change (LRU-K / 2Q, MRU for scans, a persisted mirror).

## 9. Sources (at least two; for each, one concrete thing it changed)
1. ___ : changed ___
2. ___ : changed ___
