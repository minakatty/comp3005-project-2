"""Module 1 tests. Section numbers refer to the M1 spec."""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from storage import schema as sch                                           # noqa: E402
from storage.bufferpool import BufferPool                                   # noqa: E402
from storage.cli import run_trace                                           # noqa: E402
from storage.db import Database                                             # noqa: E402
from storage.disk import NullDisk, DiskManager                              # noqa: E402
from storage.errors import (BufferPoolFull, LoadError, PinError, PinnedPageError,   # noqa: E402
                            RecordTooLong, SlotError, StorageError)
from storage.page import DIR_CAPACITY, DirPage, SlottedPage                 # noqa: E402


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class RecordFormat(unittest.TestCase):          # section 2
    def test_spec_example_bytes(self):
        cols = sch.parse_schema("a:INT,b:VARCHAR(5),c:INT")
        rec = sch.encode(cols, (42, "bob", 7))
        self.assertEqual(rec.hex(), "2a000000" "0300626f62" "07000000")
        self.assertEqual(len(rec), 13)
        self.assertEqual(sch.decode(cols, rec), (42, "bob", 7))

    def test_negative_int_and_utf8(self):
        cols = sch.parse_schema("a:INT,b:VARCHAR(10)")
        row = (-5, "héllo")
        self.assertEqual(sch.decode(cols, sch.encode(cols, row)), row)

    def test_varchar_longer_than_declared_is_rejected(self):
        with self.assertRaises(ValueError):
            sch.encode(sch.parse_schema("b:VARCHAR(3)"), ("abcd",))


class SlottedPageSpec(unittest.TestCase):       # section 3
    def test_worked_example_3_2(self):
        p = SlottedPage.init(bytearray(4096), 0)
        self.assertEqual((p.slot_count, p.free_end, p.free_space()), (0, 4096, 4088))
        steps = [(13, (1, 4083, 4071)), (20, (2, 4063, 4047)), (13, (3, 4050, 4030))]
        for length, expect in steps:
            p.insert(b"x" * length)
            self.assertEqual((p.slot_count, p.free_end, p.free_space()), expect)
        self.assertEqual([p.slot(i) for i in range(3)], [(4083, 13), (4063, 20), (4050, 13)])
        p.delete(1)
        self.assertEqual((p.slot_count, p.free_end, p.free_space()), (3, 4050, 4030))   # nothing reclaimed
        self.assertEqual(p.slot(1), (0, 0))

    def test_header_layout_bytes(self):
        p = SlottedPage.init(bytearray(4096), 7)
        p.insert(b"abc")
        self.assertEqual(int.from_bytes(p.data[0:4], "little"), 7)       # page_id
        self.assertEqual(int.from_bytes(p.data[4:6], "little"), 1)       # slot_count
        self.assertEqual(int.from_bytes(p.data[6:8], "little"), 4093)    # free_end
        self.assertEqual(int.from_bytes(p.data[8:10], "little"), 4093)   # slot 0 offset
        self.assertEqual(int.from_bytes(p.data[10:12], "little"), 3)     # slot 0 length

    def test_capacity_13_byte_records_is_240(self):
        p = SlottedPage.init(bytearray(4096), 0)
        n = 0
        while p.insert(b"y" * 13) is not None:
            n += 1
        self.assertEqual(n, 240)
        self.assertEqual(sch.records_per_page(13), 240)

    def test_slot_numbers_are_never_reused(self):
        p = SlottedPage.init(bytearray(4096), 0)
        a = p.insert(b"aa"); p.delete(a)
        b = p.insert(b"bb")
        self.assertNotEqual(a, b)
        self.assertEqual(p.slot_count, 2)

    def test_get_errors(self):
        p = SlottedPage.init(bytearray(4096), 0)
        s = p.insert(b"aa"); p.delete(s)
        with self.assertRaises(SlotError): p.get(s)          # dead
        with self.assertRaises(SlotError): p.get(5)          # out of range
        with self.assertRaises(SlotError): p.delete(s)       # already dead

    def test_directory_page_capacity_is_681(self):
        self.assertEqual(DIR_CAPACITY, 681)
        d = DirPage.init(bytearray(4096), 0)
        for i in range(681):
            d.add_entry(i + 1, 4088)
        with self.assertRaises(SlotError):
            d.add_entry(999, 1)
        self.assertEqual(d.next_dir, 0xFFFFFFFF)


class BufferPoolSpec(unittest.TestCase):        # section 5
    def run_trace_list(self, ids, B, policy):
        bp = BufferPool(NullDisk(), B, policy)
        for p in ids:
            bp.fetch_page(p); bp.unpin_page(p)
        return bp

    def test_worked_example_5_3_lru(self):
        bp = self.run_trace_list([1, 2, 3, 1, 4, 2], 3, "lru")
        s = bp.stats()
        self.assertEqual((s["hits"], s["misses"], s["evictions"]), (1, 5, 2))
        self.assertEqual(sorted(bp.resident()), [1, 2, 4])

    def test_worked_example_5_3_clock(self):
        bp = self.run_trace_list([1, 2, 3, 1, 4, 2], 3, "clock")
        s = bp.stats()
        self.assertEqual((s["hits"], s["misses"], s["evictions"]), (2, 4, 1))
        self.assertEqual(bp.resident(), [4, 2, 3])           # the spec's "[4,2,3]"

    def test_lru_step_by_step(self):
        bp = BufferPool(NullDisk(), 3, "lru")
        for p in (1, 2, 3, 1):
            bp.fetch_page(p); bp.unpin_page(p)
        self.assertEqual(list(bp.order), [2, 3, 1])           # "LRU order 2,3,1"
        bp.fetch_page(4); bp.unpin_page(4)
        self.assertNotIn(2, bp.table)                          # "evict 2"

    def test_first_B_misses_are_not_evictions(self):
        bp = self.run_trace_list([1, 2, 3, 4], 4, "lru")
        self.assertEqual(bp.stats()["evictions"], 0)

    def test_unpin_at_zero_is_an_error(self):
        bp = BufferPool(NullDisk(), 2, "lru")
        bp.fetch_page(1); bp.unpin_page(1)
        with self.assertRaises(PinError): bp.unpin_page(1)
        with self.assertRaises(PinError): bp.unpin_page(99)      # not resident

    def test_evicting_a_pinned_page_fails(self):
        for pol in ("lru", "clock"):
            bp = BufferPool(NullDisk(), 2, pol)
            bp.fetch_page(0)
            with self.assertRaises(PinnedPageError):
                bp.evict_page(0)
            bp.fetch_page(1)
            with self.assertRaises(BufferPoolFull) as e:         # every frame pinned: fetch fails
                bp.fetch_page(2)
            self.assertIn("no unpinned frame available", str(e.exception))
            self.assertIn(0, bp.table)

    def test_pinned_frames_are_skipped_not_chosen(self):
        for pol in ("lru", "clock"):
            bp = BufferPool(NullDisk(), 2, pol)
            bp.fetch_page(0)                                      # stays pinned
            for p in (1, 2, 3, 4):
                bp.fetch_page(p); bp.unpin_page(p)
            self.assertIn(0, bp.table, pol)

    def test_dirty_page_is_written_on_eviction_and_flush(self):
        bp = BufferPool(NullDisk(), 1, "lru")
        bp.fetch_page(0); bp.unpin_page(0, True)
        bp.fetch_page(1)
        self.assertEqual(bp.stats()["writes"], 1)
        bp.unpin_page(1, True)
        bp.flush_all()
        self.assertEqual(bp.stats()["writes"], 2)
        bp.flush_all()
        self.assertEqual(bp.stats()["writes"], 2)               # flags were cleared

    def test_sequential_loop_larger_than_pool_gets_no_hits(self):
        for pol in ("lru", "clock"):
            bp = self.run_trace_list([i for _ in range(5) for i in range(5)], 4, pol)
            self.assertEqual(bp.stats()["hits"], 0, pol)

    def test_trace_command_output_shape(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "t.txt")
        write(f, "1\n2\n3\n1\n4\n2\n")
        out = run_trace(f, 3, "lru")
        for k in ("policy", "frames", "hits", "misses", "evictions", "reads", "writes"):
            self.assertIn(k, out)
        self.assertEqual((out["hits"], out["misses"], out["evictions"], out["reads"], out["writes"]), (1, 5, 2, 5, 0))


class HeapFileSpec(unittest.TestCase):          # section 4
    def mk(self, frames=8, schema="a:INT,b:VARCHAR(20),c:INT"):
        self.d = tempfile.mkdtemp()
        db = Database(self.d, frames=frames)
        db.catalog.create_table("R", schema)
        return db, db.heap("R")

    def test_one_file_per_table_plus_catalog(self):
        db, h = self.mk()
        h.insert((1, "a", 2)); db.close()
        self.assertEqual(sorted(os.listdir(self.d)), ["R.db", "catalog.db"])
        for fn in os.listdir(self.d):
            self.assertEqual(os.path.getsize(os.path.join(self.d, fn)) % 4096, 0)

    def test_page_zero_is_the_directory(self):
        db, h = self.mk()
        for i in range(30):
            h.insert((i, "x", i))
        db.close()
        with open(os.path.join(self.d, "R.db"), "rb") as f:
            page0 = f.read(4096)
        self.assertEqual(int.from_bytes(page0[0:4], "little"), 0)                  # page_id
        self.assertEqual(int.from_bytes(page0[4:8], "little"), 0xFFFFFFFF)          # next_dir_page
        self.assertGreaterEqual(int.from_bytes(page0[8:10], "little"), 1)           # entry_count
        self.assertEqual(int.from_bytes(page0[10:14], "little"), 1)                 # entry 0 -> data page 1

    def test_insert_get_delete_scan_roundtrip(self):
        db, h = self.mk()
        rows = [(i, f"n{i}", -i) for i in range(1500)]
        rids = [h.insert(r) for r in rows]
        self.assertEqual(h.get(rids[777]), rows[777])
        self.assertEqual(sorted(r for _, r in h.scan()), sorted(rows))
        h.delete(rids[777])
        self.assertEqual(len([1 for _ in h.scan()]), 1499)
        db.close()

    def test_dead_slot_error_names_the_rid(self):
        db, h = self.mk()
        rid = h.insert((1, "a", 1)); h.delete(rid)
        with self.assertRaises(SlotError) as e:
            h.get(rid)
        self.assertIn(str(tuple(rid)), str(e.exception))
        db.close()

    def test_record_too_long_names_table_and_row(self):
        db, h = self.mk(schema="id:INT,t:VARCHAR(255)")
        h.insert((1, "z" * 255))                                # 4 + 2 + 255 bytes fits
        db.close()
        # force an over-long record by faking a schema wider than a page can hold
        db2 = Database(tempfile.mkdtemp(), frames=4)
        db2.catalog.create_table("W", ",".join(f"c{i}:VARCHAR(255)" for i in range(17)))
        w = db2.heap("W")
        with self.assertRaises(RecordTooLong) as e:
            w.insert(tuple("q" * 255 for _ in range(17)))
        self.assertIn("table W", str(e.exception)); self.assertIn("row", str(e.exception))
        db2.close()

    def test_delete_everything_then_scan_returns_nothing(self):
        db, h = self.mk()
        rids = [h.insert((i, "x", i)) for i in range(600)]
        for r in rids:
            h.delete(r)
        self.assertEqual(list(h.scan()), [])
        db.close()

    def test_scan_of_table_20x_larger_than_pool_never_pins_more_than_two(self):
        db, h = self.mk(frames=4, schema="a:INT,b:VARCHAR(200)")
        for i in range(25 * 90):                                 # about 90 data pages (> 20 x 4)
            h.insert((i, "w" * 150))
        pages = db.catalog.tables["R"]["num_pages"]
        self.assertGreaterEqual(pages, 80)
        db.close()
        db = Database(self.d, frames=4)                          # cold pool, like a real `storage scan`
        h = db.heap("R")
        peak, n = 0, 0
        for _ in h.scan():
            peak = max(peak, db.pool.pinned_count()); n += 1
        self.assertEqual(n, 25 * 90)
        self.assertLessEqual(peak, 2)
        self.assertEqual(db.pool.stats()["reads"], pages + 1)    # every data page + 1 directory page
        db.close()

    def test_directory_chains_across_pages(self):
        db, h = self.mk(frames=6, schema="a:INT,b:VARCHAR(200)")
        n = 20 * 700                                              # 196-byte records: 20 per page, so 700 data pages (> 681 entries)
        for i in range(n):
            h.insert((i, "d" * 190))
        self.assertGreater(len(h.directory_pages()), 1)
        self.assertEqual(sum(1 for _ in h.scan()), n)
        db.close()
        db = Database(self.d, frames=6)
        self.assertEqual(sum(1 for _ in db.heap("R").scan()), n)
        db.close()

    def test_same_command_twice_gives_identical_counters(self):
        db, h = self.mk(frames=3)
        for i in range(800):
            h.insert((i, "x", i))
        db.close()
        runs = []
        for _ in range(2):
            db = Database(self.d, frames=3)
            list(db.heap("R").scan())
            runs.append(db.pool.stats()); db.close()
        self.assertEqual(runs[0], runs[1])

    def test_catalog_survives_reopen_and_holds_counts(self):
        db, h = self.mk()
        for i in range(10):
            h.insert((i, "x", i))
        db.close()
        db = Database(self.d)
        t = db.catalog.get_table("R")
        self.assertEqual((t["num_tuples"], t["first_page"]), (10, 0))
        self.assertEqual(db.catalog.list_tables(), ["R"])
        self.assertEqual(t["schema"][1], ("b", "VARCHAR", 20))
        db.close()

    def test_many_tables_spill_the_catalog_to_more_pages(self):
        d = tempfile.mkdtemp()
        db = Database(d, frames=4)
        for i in range(60):
            db.catalog.create_table(f"T{i}", ",".join(f"col_number_{j}:INT" for j in range(12)))
        db.close()
        db = Database(d, frames=4)
        self.assertEqual(len(db.catalog.list_tables()), 60)
        self.assertGreater(db.fileset.num_pages(0), 1)
        db.close()


class CsvLoading(unittest.TestCase):            # section 6
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = Database(self.d, frames=4)
        self.db.catalog.create_table("R", "a:INT,b:VARCHAR(5)")

    def tearDown(self):
        self.db.close()

    def load(self, text):
        f = os.path.join(self.d, "in.csv"); write(f, text)
        return self.db.heap("R").load_csv(f)

    def test_quoted_field_with_comma(self):
        self.assertEqual(self.load('1,"a,b"\n2,xy\n'), 2)
        self.assertEqual([r for _, r in self.db.heap("R").scan()], [(1, "a,b"), (2, "xy")])

    def test_bad_int_names_line_and_column_and_stops(self):
        with self.assertRaises(LoadError) as e:
            self.load("1,aa\nx,bb\n3,cc\n")
        msg = str(e.exception)
        self.assertIn("line 2", msg); self.assertIn("column 1", msg)
        self.assertEqual(len(list(self.db.heap("R").scan())), 1)       # the load stopped

    def test_varchar_too_long_names_line_and_column(self):
        with self.assertRaises(LoadError) as e:
            self.load("1,abcdef\n")
        self.assertIn("line 1", str(e.exception)); self.assertIn("column 2", str(e.exception))

    def test_wrong_field_count(self):
        with self.assertRaises(LoadError) as e:
            self.load("1,aa,zz\n")
        self.assertIn("line 1", str(e.exception))


class CommandLine(unittest.TestCase):           # section 7
    def run_cli(self, *a):
        return subprocess.run([sys.executable, "-m", "storage", *a], cwd=ROOT, capture_output=True, text=True)

    def test_json_shapes(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "r.csv"); write(f, "42,bob,7\n43,amy,9\n")
        self.assertEqual(self.run_cli("create-table", "--db", d, "--name", "R", "--schema", "a:INT,b:VARCHAR(20),c:INT").returncode, 0)
        self.assertEqual(self.run_cli("load", "--db", d, "--table", "R", "--file", f).returncode, 0)
        out = json.loads(self.run_cli("scan", "--db", d, "--table", "R", "--frames", "8", "--json").stdout)
        self.assertEqual(out["table"], "R")
        self.assertEqual(out["schema"], [["a", "INT"], ["b", "VARCHAR", 20], ["c", "INT"]])
        self.assertEqual(out["rows"], [[42, "bob", 7], [43, "amy", 9]])
        for k in ("hits", "misses", "evictions", "reads", "writes"):
            self.assertIn(k, out["stats"])
        lim = json.loads(self.run_cli("scan", "--db", d, "--table", "R", "--limit", "1", "--json").stdout)
        self.assertEqual(len(lim["rows"]), 1)
        t = os.path.join(d, "t.txt"); write(t, "1\n2\n3\n1\n4\n2\n")
        tr = json.loads(self.run_cli("trace", "--db", d, "--file", t, "--frames", "3", "--policy", "clock", "--json").stdout)
        self.assertEqual((tr["policy"], tr["frames"], tr["hits"], tr["misses"], tr["evictions"], tr["writes"]),
                         ("clock", 3, 2, 4, 1, 0))

    def test_layout_text_and_json(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "r.csv"); write(f, "1,aa\n2,bb\n")
        self.run_cli("create-table", "--db", d, "--name", "R", "--schema", "a:INT,b:VARCHAR(5)")
        self.run_cli("load", "--db", d, "--table", "R", "--file", f)
        j = json.loads(self.run_cli("layout", "--db", d, "--table", "R", "--page", "1", "--json").stdout)
        self.assertEqual((j["kind"], j["slot_count"], j["free_end"]), ("data", 2, 4096 - 2 * 8))   # 4+2+2 = 8 bytes each
        self.assertEqual(j["slots"][0]["offset"], 4088)
        txt = self.run_cli("layout", "--db", d, "--table", "R", "--page", "0")
        self.assertEqual(txt.returncode, 0); self.assertIn("DIRECTORY", txt.stdout)

    def test_errors_are_messages_not_stack_traces(self):
        d = tempfile.mkdtemp()
        r = self.run_cli("scan", "--db", d, "--table", "Nope")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("error:"))
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
