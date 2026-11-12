"""Catalog (stored in page 0), HeapFile, and a Database wrapper tying them together."""
import json, os, csv
from .disk import DiskManager
from .bufferpool import BufferPool
from .page import SlottedPage
from . import schema as sch


class Catalog:
    """Table metadata lives in page 0 of the data file as one JSON record per table.
    Limitation: the whole catalog must fit in one page. Catalog I/O bypasses the pool
    and is not counted, so measurements reflect table data only."""

    def __init__(self, disk):
        self.disk = disk
        self.tables = {}
        if disk.num_pages() == 0:
            disk.allocate_page()
            self.persist()
        else:
            r, w = disk.reads, disk.writes
            page = SlottedPage(disk.read_page(0))
            disk.reads, disk.writes = r, w
            for _, rec in page.records():
                t = json.loads(rec)
                self.tables[t["name"]] = t

    def persist(self):
        page = SlottedPage.init(bytearray(4096))
        for t in self.tables.values():
            if page.insert(json.dumps(t).encode()) is None:
                raise RuntimeError("catalog exceeds one page")
        r, w = self.disk.reads, self.disk.writes
        self.disk.write_page(0, page.data)
        self.disk.reads, self.disk.writes = r, w

    def create_table(self, name, schema_str):
        if name in self.tables:
            raise ValueError(f"table {name} already exists")
        sch.parse_schema(schema_str)  # validate
        self.tables[name] = {"name": name, "schema": schema_str, "first_page": -1,
                             "last_page": -1, "num_pages": 0, "num_tuples": 0}
        self.persist()

    def get_table(self, name):
        if name not in self.tables:
            raise KeyError(f"unknown table {name}")
        t = dict(self.tables[name])
        t["schema"] = sch.parse_schema(t["schema"])
        return t

    def list_tables(self):
        return sorted(self.tables)


class HeapFile:
    """Pages chained by next_page. A free-space directory (page_id -> free bytes) is kept
    in memory and built lazily on the first insert of a session, so inserts do not scan."""

    def __init__(self, pool, catalog, name):
        self.pool, self.catalog, self.name = pool, catalog, name
        self.meta = catalog.tables[name]
        self.cols = sch.parse_schema(self.meta["schema"])
        self.fsm = None

    def _build_fsm(self):
        self.fsm = {}
        pid = self.meta["first_page"]
        while pid != -1:
            f = self.pool.fetch_page(pid)
            p = SlottedPage(f.data)
            self.fsm[pid] = p.usable_space()
            nxt = p.next_page
            self.pool.unpin_page(pid, False)
            pid = nxt

    def _add_page(self):
        f = self.pool.new_page()
        p = SlottedPage.init(f.data)
        pid = f.page_id
        if self.meta["last_page"] == -1:
            self.meta["first_page"] = pid
        else:
            lf = self.pool.fetch_page(self.meta["last_page"])
            SlottedPage(lf.data).next_page = pid
            self.pool.unpin_page(self.meta["last_page"], True)
        self.meta["last_page"] = pid
        self.meta["num_pages"] += 1
        self.fsm[pid] = p.usable_space()
        return f

    def insert(self, row):
        rec = sch.encode(self.cols, row)
        if self.fsm is None:
            self._build_fsm()
        need = len(rec) + 4
        target = self.meta["last_page"]
        if target == -1 or self.fsm.get(target, 0) < need:
            target = next((p for p, free in self.fsm.items() if free >= need), None)
        if target is None:
            f = self._add_page()
        else:
            f = self.pool.fetch_page(target)
        page = SlottedPage(f.data)
        slot = page.insert(rec)
        self.fsm[f.page_id] = page.usable_space()
        self.pool.unpin_page(f.page_id, True)
        self.meta["num_tuples"] += 1
        return (f.page_id, slot)

    def get(self, rid):
        f = self.pool.fetch_page(rid[0])
        try:
            rec = SlottedPage(f.data).get(rid[1])
        finally:
            self.pool.unpin_page(rid[0], False)
        return None if rec is None else sch.decode(self.cols, rec)

    def delete(self, rid):
        f = self.pool.fetch_page(rid[0])
        p = SlottedPage(f.data)
        ok = p.delete(rid[1])
        if self.fsm is not None:
            self.fsm[rid[0]] = p.usable_space()
        self.pool.unpin_page(rid[0], ok)
        if ok:
            self.meta["num_tuples"] -= 1
        return ok

    def scan(self):
        """Pins exactly one page at a time. Records of the current page are copied out
        and the page is unpinned before any tuple is yielded."""
        pid = self.meta["first_page"]
        while pid != -1:
            f = self.pool.fetch_page(pid)
            p = SlottedPage(f.data)
            batch = [(i, r) for i, r in p.records()]
            nxt = p.next_page
            self.pool.unpin_page(pid, False)
            for slot, rec in batch:
                yield (pid, slot), sch.decode(self.cols, rec)
            pid = nxt

    def load_csv(self, path, header=False):
        n = 0
        with open(path, newline="") as fh:
            rd = csv.reader(fh)
            if header:
                next(rd, None)
            for rec in rd:
                if not rec:
                    continue
                row = [int(v) if t == "INT" else v for v, (_, t, _) in zip(rec, self.cols)]
                self.insert(row)
                n += 1
        return n


class Database:
    def __init__(self, path="db", frames=16, policy="lru"):
        os.makedirs(path, exist_ok=True)
        self.disk = DiskManager(os.path.join(path, "data.db"))
        self.catalog = Catalog(self.disk)
        self.pool = BufferPool(self.disk, frames, policy)

    def heap(self, name):
        if name not in self.catalog.tables:
            raise KeyError(f"unknown table {name}")
        return HeapFile(self.pool, self.catalog, name)

    def close(self):
        self.pool.flush_all()
        self.catalog.persist()
        self.disk.close()
