# Oral check notes (M1 + refactor). Answer each aloud, from the code, without looking.

**Pages**
- Draw a data page: header fields and offsets (page_id@0, slot_count@4, free_end@6), slot directory from byte 8, records from byte 4095 down.
- Free space formula; why an insert needs L + 4; what a dead slot looks like (0,0); why slot numbers are never reused (rids stay valid).
- After inserts of 13, 20, 13 bytes and a delete of slot 1, what are slot_count, free_end and free space? (3, 4050, 4030) Why did the delete free nothing?
- Records per page for 13-byte records: floor(4088 / 17) = 240. Why does your measured number differ for VARCHAR tables?

**Heap file**
- Page 0 is a directory page: header fields, 6-byte entries, 681 per page. How does insert find a page? What does the in-memory mirror save and cost?
- How does scan keep at most one page pinned? (copy entries / records, unpin, then yield)
- Why does a 500-page scan read 501 pages at every B?

**Buffer pool**
- hit, miss, eviction, read, write: exact definitions. Why are the first B misses not evictions?
- LRU: what moves a frame to most recently used? (every fetch) How is the victim chosen? Walk the 1 2 3 1 4 2 example.
- CLOCK: when is the reference bit set, what does the hand skip, why does it end up at [4,2,3] in the example, when does it give up?
- Why do both policies get 0 hits on a loop over more pages than the pool? Which page is evicted just before it is needed?
- What happens if every frame is pinned? (error "no unpinned frame available") If you unpin a page at pin count 0? (error)

**Catalog and counters**
- Where is the catalog, what is in a record, how is it persisted, why reset the counters after loading it?
- Why does `trace` use a stand-in disk?

**Refactor**
- Why does `project` keep duplicates now; which of the 25 cases changed (case 23) and how did you fix the test?
- What is a provider? What does `parse_condition` give modules 2, 3 and 4? Why is `distinct` a soft keyword?
