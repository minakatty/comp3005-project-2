# Oral check notes for M1 (and the refactor)
The instructor runs your tool on his files and asks you to explain what happened, then asks one reading question.
Be able to answer each of these from the code, out loud, without looking.

**Pages**
- Draw the page. Where do the slot directory and the records start, and which way does each grow?
- How many bytes is the header and a slot? What does `offset == 0` mean? Why can't a record start at 0?
- What happens on delete? On the next insert? When does compaction run, and why do record ids stay valid?
- How many 12-byte records fit on a page, and why that number (formula)?

**Heap file**
- How does insert avoid scanning the file? What is the weakness (map rebuilt on first insert of a session)?
- How does `scan` keep only one page pinned? (copy records, unpin, then yield)
- What is a rid?

**Buffer pool**
- What is pinning and why can't a pinned page be evicted? What do you raise, and when?
- LRU: what exactly is "recently used" in your implementation? (time of last unpin)
- CLOCK: when is the reference bit set, what does the hand skip, and why can CLOCK behave like FIFO?
- Why do both policies get zero hits on a loop over B+1 pages? Which page is evicted right before it is needed?
- What do hits, misses, evictions, reads and writes mean, and which are the same number sometimes?
- Why does `trace` use a dummy disk?

**Catalog and counters**
- Where is the catalog stored and why isn't its I/O counted? What is its size limit?
- Why is `allocate_page` not counted as a write?

**Refactor**
- Why must `project` keep duplicates now, and which of the 25 cases changed (case 23)?
- What is a provider, and why does the evaluator not own its data any more?
- What does `parse_condition` give modules 2, 3 and 4?
- Why is `distinct` a soft keyword? Show that `select[distinct=1](R)` still works.
