# DESIGN_M1: storage engine and buffer pool

> DRAFT written with AI help from the code as it exists. Read every line, check it against
> `storage/*.py`, and rewrite the "why" parts in your own words. You will be asked about all of it.

## 1. Page layout (4096 bytes, little endian)

```
byte   0    2    4         8                       8+4n          free_end              4096
       +----+----+---------+-----------------------+-------------+---------------------+
       | ns | fe | next_pg | slot 0 | slot 1 | ... |  free space | ... rec 1 | rec 0   |
       +----+----+---------+-----------------------+-------------+---------------------+
        u16  u16    i32      4 bytes each            (grows ->)     (<- grows downward)
```
| bytes | field | meaning |
|---|---|---|
| 0-1 | `num_slots` (u16) | slot directory entries in use, deleted ones included |
| 2-3 | `free_end` (u16) | offset of the lowest record byte; starts at 4096 |
| 4-7 | `next_page` (i32) | next page of the heap file, -1 at the end |
| 8 + 4i .. 11 + 4i | slot i | `offset` (u16), `length` (u16) |

- The slot directory grows forward from byte 8, record data grows backward from byte 4096.
- A slot with `offset == 0` is deleted. A real record can never start at 0 because the header lives there.
- Free space between the two = `free_end - (8 + 4 * num_slots)`.
- Delete only marks the slot. A later insert reuses the first deleted slot number. When a record fits only
  after reclaiming dead bytes, the page is compacted (records slide to the back; slot numbers do not change,
  so record ids stay valid).
- Capacity by hand: `records = floor((4096 - 8) / (record_size + 4))`.

## 2. Record format
`INT` = 4 bytes signed. `VARCHAR(n)` = 1 length byte then the UTF-8 bytes (n <= 255). Columns in schema order,
no padding, no NULLs. A record's size depends on its actual string lengths, not on the declared `n`.

## 3. Heap file and free-space strategy
- A table is a singly linked chain of pages (`next_page`). The catalog stores `first_page`, `last_page`,
  `num_pages`, `num_tuples`.
- **Free-space directory:** an in-memory map `page_id -> usable bytes`. An insert tries the last page first,
  then any page in the map with room, and only then allocates a new page. It never scans the file.
- **Known cost (be ready to say this):** the map is rebuilt lazily, so the first insert into an existing table
  in a new session walks the page chain once (one read per page). It is not persisted.
- `scan()` fetches one page, copies its records out, unpins it, and only then yields them, so at most one page is
  pinned at any time. The tests assert this.
- `rid = (page_id, slot)`. `delete` marks the slot; `get` on a deleted rid returns `None`.

## 4. Catalog
Page 0 of the data file holds one JSON record per table (name, schema string, first/last page, counts), stored
with the same slotted-page code. Limits: the whole catalog must fit in one page, and catalog reads/writes go
straight to the disk manager so they are **not** counted in the buffer-pool counters (the measurements then
reflect table pages only).

## 5. Buffer pool
- `B` frames, a table `page_id -> frame`, per-frame `pin`, `dirty`, `ref`.
- `fetch_page` hit: pin++ and count a hit. Miss: choose a victim (a free frame first), write it back if dirty,
  read the page, count a miss; counting an eviction only when a resident page was replaced.
- `new_page` allocates a page and pins it dirty, without a read.
- **Pinned pages are never victims.** `evict_page(pid)` on a pinned page raises `PinnedPageError`; a fetch when
  every frame is pinned raises `BufferPoolFull`. There is a test for both.
- **LRU:** recency is the time of the last unpin; unpinned pages sit in an ordered dict, oldest first; the
  victim is the first entry.
- **CLOCK:** the reference bit is set when a page is loaded and on every hit. The hand sweeps the frames,
  skipping pinned ones, clearing set bits, and evicts the first unpinned frame whose bit is clear.
- `stats()` returns hits, misses, evictions, and the disk reads and writes since the pool was created.
- `storage trace` replays a trace on an in-memory dummy disk (all-zero pages) so only the policy is measured; a line
  `<id> w` marks the page dirty on unpin, which produces writes.

## 6. Disk manager
One file of 4096-byte pages. Counts every `read_page` and `write_page`. `allocate_page` zero-extends the file
without counting a write (the page is counted when the pool flushes it).

## 7. Choices and alternatives (write your own reasoning here)
- Why slotted pages instead of fixed-size slots: variable-length VARCHARs; record ids survive compaction.
- Why `offset == 0` as the deleted marker instead of a flag byte: smaller slots (4 bytes) and no extra state.
- Why a chain instead of a directory page: simplest thing that supports scans; cost is walking the chain to
  rebuild the free-space map.
- What you would change with more time: persist the free-space map, spill the catalog across pages, a
  scan-resistant policy (LRU-K, 2Q, or CLOCK-Pro).

## 8. Sources (at least two; say what each changed)
Fill in after reading, for example Ramakrishnan and Gehrke ch. 9, O'Neil et al. (LRU-K), Petrov (Database
Internals). For each one write: what you read, and **one concrete thing in this design that it changed**.
