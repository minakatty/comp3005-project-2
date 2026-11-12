# M1 spec -> code -> test

| spec item | where | test |
|---|---|---|
| 1 one `<table>.db` per table + `catalog.db`, 4096-byte pages | `db.py` Catalog/Database, `disk.py` | `test_one_file_per_table_plus_catalog` |
| 2 record format, VARCHAR 2-byte length, example = 13 bytes | `schema.py` | `RecordFormat.test_spec_example_bytes` |
| 3 page header, slots, free space, delete, no reclaim | `page.py` SlottedPage | `test_worked_example_3_2`, `test_header_layout_bytes` |
| 3.2 worked example reproduces | `page.py` | `test_worked_example_3_2` |
| 3.3 capacity 240 for 13-byte records | `schema.records_per_page` | `test_capacity_13_byte_records_is_240` |
| 3.3 record > 4088 -> error naming table and row | `db.py` HeapFile.insert | `test_record_too_long_names_table_and_row` |
| 4 directory pages, 681 entries, page 0 first | `page.py` DirPage, `db.py` | `test_directory_page_capacity_is_681`, `test_page_zero_is_the_directory` |
| 4.1 insert first fit, get, delete, scan, one page pinned | `db.py` HeapFile | `test_insert_get_delete_scan_roundtrip`, `test_scan_of_table_20x...` |
| 5 hit / miss / eviction / read / write rules | `bufferpool.py` | `test_first_B_misses_are_not_evictions`, trace tests |
| 5.1 LRU, 5.2 CLOCK exactly | `bufferpool.py` | `test_lru_step_by_step`, both worked-example tests |
| 5.3 worked example, both policies | `bufferpool.py` | `test_worked_example_5_3_lru`, `..._clock` |
| 6 catalog in `catalog.db` through the pool, room for stats | `db.py` Catalog | `test_catalog_survives_reopen...`, `test_many_tables_spill...` |
| 6 CSV: quoted commas; bad field -> line and column | `db.py` load_csv | `CsvLoading` |
| 7 five commands, required JSON shapes | `cli.py` | `CommandLine.test_json_shapes` |
| 8 three measurements | `tools/m1_measure.py` | `test_tools.py` (smoke) |
| 9 evicting a pinned page never happens; fetch fails | `bufferpool.py` | `test_evicting_a_pinned_page_fails` |
| 9 unpin at pin count 0 -> error | `bufferpool.py` | `test_unpin_at_zero_is_an_error` |
| 9 dead slot -> error naming the rid | `db.py` | `test_dead_slot_error_names_the_rid` |
| 10 delete everything, scan returns nothing | `db.py` | `test_delete_everything_then_scan_returns_nothing` |
| 10 same command twice, identical counters | all | `test_same_command_twice_gives_identical_counters` |

## Decisions the spec left open (all documented in DESIGN_M1.md)
- Compaction: not implemented (optional in the spec).
- Catalog counters: the pool counters are reset after the catalog is loaded, so `scan`/`load` stats show table access only.
- `new_page` counts no hit, miss or eviction (the spec defines those for fetches).
- Directory search uses an in-memory mirror of the directory (layout on disk identical to a literal walk).
- Delete does not rewrite the directory entry because free space does not change.

## Ask the instructor if unsure
- Do `scan` stats include the page reads of loading the catalog? (Mine do not.)
- What are the names of the five published trace files, and is trace 3 the 80-page loop?
