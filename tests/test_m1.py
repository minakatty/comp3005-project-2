"""Module 1 tests: every checklist item in M1 spec Section 10 and every error in Section 9."""
import os
import shutil
import tempfile
import unittest

from storage.bufferpool import BufferPool
from storage.cli import run_trace
from storage.db import Database
from storage.disk import MemoryDisk
from storage.errors import BufferPoolError, CatalogError, LoadError, PageError
from storage.page import PAGE_SIZE, SlottedPage
from storage.record import decode, encode, parse_schema


class TmpDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def csv(self, name, lines):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return p


class RecordFormat(unittest.TestCase):
    def test_spec_example_13_bytes(self):
        cols = parse_schema("a:INT,b:VARCHAR(5),c:INT")
        rec = encode(cols, (42, "bob", 7))
        self.assertEqual(rec.hex(" ").upper(), "2A 00 00 00 03 00 62 6F 62 07 00 00 00")
        self.assertEqual(decode(cols, rec), (42, "bob", 7))

    def test_negative_int_twos_complement(self):
        cols = parse_schema("a:INT")
        self.assertEqual(encode(cols, (-1,)), b"\xff\xff\xff\xff")

    def test_varchar_too_long(self):
        with self.assertRaises(PageError):
            encode(parse_schema("b:VARCHAR(2)"), ("abc",))

    def test_bad_schema(self):
        for s in ("a:FLOAT", "a:VARCHAR(0)", "a:VARCHAR(256)", "a:INT,a:INT", "a"):
            with self.assertRaises(CatalogError):
                parse_schema(s)


class SlottedPageSpec(unittest.TestCase):
    def test_section_3_2_worked_example(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE), 0)
        self.assertEqual((p.slot_count, p.free_end, p.free_space()), (0, 4096, 4088))
        self.assertEqual(p.insert(bytes(13)), 0)
        self.assertEqual((p.slot_count, p.free_end, p.free_space(), p.slot(0)), (1, 4083, 4071, (4083, 13)))
        self.assertEqual(p.insert(bytes(20)), 1)
        self.assertEqual((p.slot_count, p.free_end, p.free_space(), p.slot(1)), (2, 4063, 4047, (4063, 20)))
        self.assertEqual(p.insert(bytes(13)), 2)
        self.assertEqual((p.slot_count, p.free_end, p.free_space(), p.slot(2)), (3, 4050, 4030, (4050, 13)))
        p.delete(1)
        self.assertEqual((p.slot_count, p.free_end, p.free_space(), p.slot(1)), (3, 4050, 4030, (0, 0)))

    def test_capacity_formula_L13_is_240(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE), 0)
        n = 0
        while p.insert(bytes(13)) is not None:
            n += 1
        self.assertEqual(n, 4088 // 17)
        self.assertEqual(n, 240)

    def test_dead_slot_read_names_rid(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE), 5)
        p.insert(b"x")
        p.delete(0)
        with self.assertRaises(PageError) as cm:
            p.get(0)
        self.assertIn("(5, 0)", str(cm.exception))
        with self.assertRaises(PageError):
            p.delete(0)

    def test_compaction_keeps_rids_and_reclaims_space(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE), 0)
        a = p.insert(b"A" * 2000)
        b = p.insert(b"B" * 1000)
        p.delete(a)
        self.assertEqual(p.free_space(), 4088 - 3000 - 8)
        self.assertEqual(p.reclaimable(), 2000)
        self.assertIsNone(p.insert(b"C" * 1500))         # does not fit before compaction
        p.compact()
        self.assertEqual(p.get(b), b"B" * 1000)           # rid (page, 1) still valid
        c = p.insert(b"C" * 1500)
        self.assertEqual((c, p.get(c)), (2, b"C" * 1500))
        with self.assertRaises(PageError):
            p.get(a)

    def test_heap_reuses_deleted_space(self):
        d = tempfile.mkdtemp()
        db = Database(d, 8)
        db.create_table("W", parse_schema("a:INT,s:VARCHAR(255)"))
        h = db.heap("W")
        rids = [h.insert((i, "x" * 250)) for i in range(15)]       # one full page
        keep = rids[3]
        for r in rids:
            if r != keep:
                h.delete(r)
        for i in range(14):
            h.insert((100 + i, "y" * 250))
        self.assertEqual(db.count_data_pages("W"), 1)                # no new page needed
        self.assertEqual(h.get(keep), (3, "x" * 250))
        db.close()
        shutil.rmtree(d)

    def test_slot_numbers_never_reused(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE), 0)
        p.insert(b"a")
        p.delete(0)
        self.assertEqual(p.insert(b"b"), 1)


class BufferPoolSpec(unittest.TestCase):
    TRACE = [1, 2, 3, 1, 4, 2]

    def test_section_5_3_lru(self):
        r = run_trace(self.TRACE, 3, "lru")
        self.assertEqual((r["hits"], r["misses"], r["evictions"], r["reads"], r["writes"]), (1, 5, 2, 5, 0))

    def test_section_5_3_clock(self):
        r = run_trace(self.TRACE, 3, "clock")
        self.assertEqual((r["hits"], r["misses"], r["evictions"], r["reads"], r["writes"]), (2, 4, 1, 4, 0))

    def test_lru_pool_contents(self):
        pool, d = BufferPool(3, "lru"), MemoryDisk()
        for pid in self.TRACE:
            pool.fetch_page(d, pid)
            pool.unpin_page(d, pid)
        self.assertEqual(sorted(k[1] for k in pool.page_table), [1, 2, 4])

    def test_clock_pool_contents(self):
        pool, d = BufferPool(3, "clock"), MemoryDisk()
        for pid in self.TRACE:
            pool.fetch_page(d, pid)
            pool.unpin_page(d, pid)
        self.assertEqual([f.page_id for f in pool.frames], [4, 2, 3])

    def test_evicting_pinned_page_fails(self):
        for policy in ("lru", "clock"):
            pool, d = BufferPool(2, policy), MemoryDisk()
            pool.fetch_page(d, 1)
            pool.fetch_page(d, 2)
            with self.assertRaises(BufferPoolError):
                pool.fetch_page(d, 3)
            # the pinned pages are still there and still pinned
            self.assertEqual(sorted(f.pin_count for f in pool.frames), [1, 1])

    def test_unpin_at_zero_fails(self):
        pool, d = BufferPool(2), MemoryDisk()
        pool.fetch_page(d, 1)
        pool.unpin_page(d, 1)
        with self.assertRaises(BufferPoolError):
            pool.unpin_page(d, 1)
        with self.assertRaises(BufferPoolError):
            pool.unpin_page(d, 9)

    def test_dirty_eviction_counts_a_write(self):
        pool, d = BufferPool(1), MemoryDisk()
        pool.fetch_page(d, 1)
        pool.unpin_page(d, 1, True)
        pool.fetch_page(d, 2)
        self.assertEqual(pool.stats()["writes"], 1)

    def test_first_B_misses_are_not_evictions(self):
        r = run_trace([1, 2, 3, 4], 4, "lru")
        self.assertEqual(r["evictions"], 0)

    def test_reserved_frames_never_evicted(self):
        pool, d = BufferPool(3), MemoryDisk()
        pool.reserve(2)
        pool.fetch_page(d, 1)
        pool.unpin_page(d, 1)
        pool.fetch_page(d, 2)
        with self.assertRaises(BufferPoolError):
            pool.reserve(1)

    def test_repeated_scan_floods_lru(self):
        trace = list(range(80)) * 10
        lru = run_trace(trace, 64, "lru")
        self.assertEqual(lru["hits"], 0)          # sequential flooding
        clock = run_trace(trace, 64, "clock")
        self.assertEqual(clock["hits"], 0)        # CLOCK floods too on a pure loop


class HeapAndCatalog(TmpDB):
    def make(self, n, frames=8, schema="a:INT,b:VARCHAR(5),c:INT"):
        db = Database(self.dir, frames)
        db.create_table("R", parse_schema(schema))
        lines = [f"{i},bob,{i % 7}" for i in range(n)]
        db.load_csv("R", self.csv("r.csv", lines))
        return db

    def test_measured_capacity_matches_formula(self):
        db = self.make(1000)
        info = db.catalog.get_table("R")
        self.assertEqual(info["num_tuples"], 1000)
        self.assertEqual(info["num_pages"], 5)     # ceil(1000 / 240)
        db.close()

    def test_scan_20x_B_pins_at_most_two(self):
        db = self.make(240 * 40, frames=2)         # 40 pages, B = 2: twenty times B
        db.pool.reset_stats()
        n = sum(1 for _ in db.heap("R").scan())
        self.assertEqual(n, 9600)
        self.assertLessEqual(db.pool.peak_held, 2)
        self.assertEqual(db.pool.reads, 41)        # 40 data pages + 1 directory page
        self.assertEqual(db.pool.pinned_frames(), [])
        db.close()

    def test_scan_reads_do_not_fall_with_small_B(self):
        db = self.make(240 * 10, frames=64)
        db.close()
        reads = []
        for b in (2, 8, 64):
            d = Database(self.dir, b)
            d.pool.reset_stats()
            for _ in d.heap("R").scan():
                pass
            reads.append(d.pool.reads)
            d.close()
        self.assertEqual(reads, [11, 11, 11])

    def test_same_command_twice_same_counters(self):
        db = self.make(2000)
        db.close()
        stats = []
        for _ in range(2):
            d = Database(self.dir, 4, "clock")
            d.pool.reset_stats()
            list(d.heap("R").scan())
            stats.append(d.pool.stats())
            d.close()
        self.assertEqual(stats[0], stats[1])

    def test_delete_everything_then_scan_is_empty(self):
        db = self.make(500)
        rids = [rid for rid, _ in db.heap("R").scan()]
        for rid in rids:
            db.delete("R", rid)
        self.assertEqual(list(db.heap("R").scan()), [])
        with self.assertRaises(PageError):
            db.heap("R").get(rids[0])
        db.close()

    def test_get_and_rid(self):
        db = self.make(10)
        rid = db.insert("R", (99, "amy", 9))
        self.assertEqual(db.heap("R").get(rid), (99, "amy", 9))
        db.close()

    def test_catalog_persists_in_pages(self):
        db = self.make(300)
        db.close()
        db = Database(self.dir, 4)
        info = db.catalog.get_table("R")
        self.assertEqual((info["num_tuples"], info["num_pages"]), (300, 2))
        self.assertEqual(info["schema"], [("a", "INT", 0), ("b", "VARCHAR", 5), ("c", "INT", 0)])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "catalog.db")))
        db.close()

    def test_more_than_681_data_pages_chains_directories(self):
        db = Database(self.dir, 16)
        db.create_table("W", parse_schema("a:INT,b:VARCHAR(255)"))
        h = db.heap("W")
        big = "x" * 255
        for i in range(700 * 15):                  # 15 x 265-byte records per page
            h.insert((i, big))
        self.assertGreater(len(db.directory_pages("W")), 1)
        self.assertEqual(sum(1 for _ in h.scan()), 700 * 15)
        db.close()

    def test_bad_csv_int_names_line_and_column(self):
        db = Database(self.dir)
        db.create_table("R", parse_schema("a:INT,b:VARCHAR(5)"))
        with self.assertRaises(LoadError) as cm:
            db.load_csv("R", self.csv("bad.csv", ["1,ok", "2,ok", "x3,ok"]))
        self.assertIn("line 3", str(cm.exception))
        self.assertIn("column a", str(cm.exception))
        self.assertEqual(db.catalog.get_table("R")["num_tuples"], 0)   # the load stopped
        db.close()

    def test_bad_csv_varchar_too_long(self):
        db = Database(self.dir)
        db.create_table("R", parse_schema("a:INT,b:VARCHAR(3)"))
        with self.assertRaises(LoadError) as cm:
            db.load_csv("R", self.csv("bad.csv", ["1,abcd"]))
        self.assertIn("line 1", str(cm.exception))
        db.close()

    def test_quoted_comma_field(self):
        db = Database(self.dir)
        db.create_table("R", parse_schema("a:INT,b:VARCHAR(10)"))
        db.load_csv("R", self.csv("q.csv", ['1,"a,b"']))
        self.assertEqual([r for _, r in db.heap("R").scan()], [(1, "a,b")])
        db.close()

    def test_record_too_long_names_table_and_row(self):
        db = Database(self.dir)
        db.create_table("R", parse_schema("a:INT"))
        with self.assertRaises(PageError) as cm:
            db.heap("R").insert_bytes(bytes(4090), row_label=7)
        self.assertIn("table R", str(cm.exception))
        self.assertIn("row 7", str(cm.exception))
        db.close()

    def test_unknown_table(self):
        db = Database(self.dir)
        with self.assertRaises(CatalogError):
            db.heap("nope")
        db.close()


if __name__ == "__main__":
    unittest.main()
