import os, sys, tempfile, unittest, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.disk import DiskManager, NullDisk, PAGE_SIZE
from storage.page import SlottedPage
from storage.bufferpool import BufferPool, PinnedPageError, BufferPoolFull
from storage.db import Database
from storage import schema as sch
from storage.cli import run_trace


class PageTests(unittest.TestCase):
    def test_insert_get_delete_reuse_compact(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE))
        s0 = p.insert(b"hello"); s1 = p.insert(b"world!!")
        self.assertEqual(p.get(s0), b"hello"); self.assertEqual(p.get(s1), b"world!!")
        self.assertTrue(p.delete(s0)); self.assertIsNone(p.get(s0))
        s2 = p.insert(b"abc")
        self.assertEqual(s2, s0)  # slot reuse
        self.assertEqual(p.get(s1), b"world!!")

    def test_fill_exactly(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE))
        n = 0
        while p.insert(b"x" * 12) is not None:
            n += 1
        self.assertEqual(n, (4096 - 8) // (12 + 4))

    def test_compaction_reclaims_space(self):
        p = SlottedPage.init(bytearray(PAGE_SIZE))
        slots = [p.insert(b"y" * 100) for _ in range(30)]
        for s in slots[::2]:
            p.delete(s)
        self.assertIsNotNone(p.insert(b"z" * 1000))
        for s in slots[1::2]:
            self.assertEqual(p.get(s), b"y" * 100)


class PoolTests(unittest.TestCase):
    def mk(self, frames, policy="lru", n=20):
        return BufferPool(NullDisk(n), frames, policy)

    def test_evicting_pinned_page_fails(self):
        for pol in ("lru", "clock"):
            bp = self.mk(2, pol)
            bp.fetch_page(0)  # stays pinned
            with self.assertRaises(PinnedPageError):
                bp.evict_page(0)
            bp.fetch_page(1)
            with self.assertRaises(BufferPoolFull):
                bp.fetch_page(2)   # all frames pinned: refuses rather than evicting
            self.assertIn(0, bp.table)

    def test_dirty_written_on_evict(self):
        bp = self.mk(1)
        bp.fetch_page(0); bp.unpin_page(0, True)
        bp.fetch_page(1)
        self.assertEqual(bp.stats()["writes"], 1)

    def test_lru_sequential_flooding(self):
        bp = self.mk(4, "lru", 5)
        for _ in range(10):
            for pid in range(5):
                bp.fetch_page(pid); bp.unpin_page(pid)
        self.assertEqual(bp.stats()["hits"], 0)  # LRU never hits on a loop of B+1 pages

    def test_clock_also_floods_on_pure_loop(self):
        # With every ref bit set, CLOCK degenerates to FIFO and also gets 0 hits on a
        # loop over B+1 pages. Both policies need a scan-resistant variant to survive this.
        bp = self.mk(4, "clock", 5)
        for _ in range(10):
            for pid in range(5):
                bp.fetch_page(pid); bp.unpin_page(pid)
        self.assertEqual(bp.stats()["hits"], 0)

    def test_hot_page_protected_under_both_policies(self):
        for pol in ("lru", "clock"):
            bp = self.mk(3, pol, 50)
            hits_hot = 0
            for i in range(1, 40):
                bp.fetch_page(0); bp.unpin_page(0)       # hot page touched every round
                bp.fetch_page(i); bp.unpin_page(i)       # cold one-shot pages
            self.assertIn(0, bp.table, pol)

    def test_lru_order(self):
        bp = self.mk(2)
        for pid in (0, 1, 0, 2):  # 1 is LRU, so 2 evicts 1
            bp.fetch_page(pid); bp.unpin_page(pid)
        self.assertIn(0, bp.table); self.assertNotIn(1, bp.table)

    def test_trace_runner(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(str(i % 3) for i in range(30)))
        r = run_trace(f.name, 3, "lru")
        self.assertEqual((r["hits"], r["misses"]), (27, 3))


class HeapTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_roundtrip_and_persistence(self):
        db = Database(self.d, frames=4)
        db.catalog.create_table("R", "a:INT,b:VARCHAR(20),c:INT")
        h = db.heap("R")
        rows = [(i, f"name{i}", -i) for i in range(2000)]
        rids = [h.insert(r) for r in rows]
        self.assertEqual(h.get(rids[1234]), rows[1234])
        db.close()
        db = Database(self.d, frames=4)
        h = db.heap("R")
        self.assertEqual([r for _, r in h.scan()], rows)
        self.assertEqual(db.catalog.get_table("R")["num_tuples"], 2000)
        db.close()

    def test_scan_pins_at_most_one_page(self):
        db = Database(self.d, frames=3)
        db.catalog.create_table("R", "a:INT")
        h = db.heap("R")
        for i in range(5000):
            h.insert((i,))
        pages = db.catalog.tables["R"]["num_pages"]
        self.assertGreater(pages, 3)
        before = db.pool.stats()["reads"]
        n = 0
        for _ in h.scan():
            self.assertLessEqual(db.pool.pinned_count(), 1)
            n += 1
        self.assertEqual(n, 5000)
        self.assertEqual(db.pool.stats()["reads"] - before, pages)  # one read per page
        db.close()

    def test_delete(self):
        db = Database(self.d, frames=4)
        db.catalog.create_table("R", "a:INT")
        h = db.heap("R")
        rids = [h.insert((i,)) for i in range(10)]
        h.delete(rids[3])
        self.assertIsNone(h.get(rids[3]))
        self.assertEqual(len([1 for _ in h.scan()]), 9)
        db.close()

    def test_capacity_matches_hand_calc(self):
        cols = sch.parse_schema("a:INT,b:INT,c:INT")
        db = Database(self.d, frames=4)
        db.catalog.create_table("T", "a:INT,b:INT,c:INT")
        h = db.heap("T")
        for i in range(sch.theoretical_per_page(cols) * 3):
            h.insert((i, i, i))
        self.assertEqual(db.catalog.tables["T"]["num_pages"], 3)
        db.close()

    def test_varchar_too_long_rejected(self):
        with self.assertRaises(ValueError):
            sch.encode(sch.parse_schema("b:VARCHAR(3)"), ("abcd",))


if __name__ == "__main__":
    unittest.main()
