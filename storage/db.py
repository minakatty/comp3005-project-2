"""Catalog (catalog.db), HeapFile (one <table>.db file per table) and the Database that ties them
to one buffer pool. Page ids inside a file are zero-based; the pool addresses a page as
(file_no << 32) | page_id, and file 0 is always catalog.db.
"""
import csv
import json
import os

from .bufferpool import BufferPool
from .disk import DiskManager, FileSet
from .errors import LoadError, RecordTooLong, SlotError, StorageError, UnknownTable
from .page import DIR_CAPACITY, MAX_RECORD, NO_PAGE, DirPage, SlottedPage
from . import schema as sch

CATALOG_FILE_NO = 0


def gid(file_no, page_id):
    return (file_no << 32) | page_id


class Catalog:
    """catalog.db is a sequence of slotted pages (same layout as data pages). Each live record is
    one UTF-8 JSON object describing a table:

      {"name": "R", "schema": "a:INT,b:VARCHAR(20)", "file": "R.db", "file_no": 1,
       "num_pages": <data pages>, "file_pages": <all pages in R.db>, "num_tuples": <live tuples>,
       "stats": {}}                       <- empty now; Module 4 stores ANALYZE results here

    The catalog is read and written through the buffer pool like any other data. persist() rewrites
    every catalog page from scratch, so dead slots never accumulate."""

    def __init__(self, db):
        self.db = db
        self.tables = {}
        pool, fs = db.pool, db.fileset
        for i in range(fs.num_pages(CATALOG_FILE_NO)):
            f = pool.fetch_page(gid(CATALOG_FILE_NO, i))
            for _, rec in SlottedPage(f.data).live_records():
                meta = json.loads(rec.decode("utf-8"))
                self.tables[meta["name"]] = meta
            pool.unpin_page(gid(CATALOG_FILE_NO, i), False)
        for meta in self.tables.values():
            db._open_table_file(meta)

    def persist(self):
        pool, fs = self.db.pool, self.db.fileset
        for meta in self.tables.values():
            meta["file_pages"] = fs.num_pages(meta["file_no"])
        groups, cur, room = [], [], 4096 - 8
        for meta in self.tables.values():
            rec = json.dumps(meta, separators=(",", ":")).encode("utf-8")
            if len(rec) > MAX_RECORD:
                raise StorageError(f"catalog entry for table {meta['name']} is too large")
            if len(rec) + 4 > room:
                groups.append(cur); cur, room = [], 4096 - 8
            cur.append(rec); room -= len(rec) + 4
        groups.append(cur)
        existing = fs.num_pages(CATALOG_FILE_NO)
        for i in range(max(existing, len(groups))):
            if i < existing:
                f = pool.fetch_page(gid(CATALOG_FILE_NO, i))
            else:
                f = pool.new_page(CATALOG_FILE_NO)
            page = SlottedPage.init(f.data, i)
            for rec in (groups[i] if i < len(groups) else []):
                page.insert(rec)
            pool.unpin_page(f.page_id, True)

    def create_table(self, name, schema_str):
        if not name or not (name[0].isalpha() or name[0] == "_") or not name.replace("_", "a").isalnum():
            raise StorageError(f"invalid table name {name!r}")
        if name in self.tables:
            raise StorageError(f"table {name} already exists")
        try:
            sch.parse_schema(schema_str)
        except ValueError as e:
            raise StorageError(f"bad schema: {e}")
        path = os.path.join(self.db.path, f"{name}.db")
        if os.path.exists(path):
            raise StorageError(f"{name}.db already exists but table {name} is not in the catalog")
        file_no = max([CATALOG_FILE_NO] + [m["file_no"] for m in self.tables.values()]) + 1
        meta = {"name": name, "schema": schema_str, "file": f"{name}.db", "file_no": file_no,
                "num_pages": 0, "file_pages": 1, "num_tuples": 0, "stats": {}}
        self.db._open_table_file(meta)
        f = self.db.pool.new_page(file_no)                 # page 0 = first directory page
        DirPage.init(f.data, 0)
        self.db.pool.unpin_page(f.page_id, True)
        self.tables[name] = meta
        self.persist()

    def get_table(self, name):
        if name not in self.tables:
            raise UnknownTable(f"unknown table {name}")
        m = self.tables[name]
        return {"name": name, "schema": sch.parse_schema(m["schema"]), "file": m["file"],
                "first_page": 0, "num_pages": m["num_pages"], "file_pages": m["file_pages"],
                "num_tuples": m["num_tuples"], "stats": m.get("stats", {})}

    def list_tables(self):
        return sorted(self.tables)


class HeapFile:
    """A table file: page 0 is the first directory page; data pages and further directory pages
    follow in allocation order. Directory entries are (data_page_id, free_bytes).

    insert  first fit over the directory entries (free_bytes >= len + 4). The search runs over an
            in-memory mirror of the directory that is built by one walk of the directory chain on
            the first insert of a session and kept in step with every change; the data page and
            the directory page are still read and written through the buffer pool.
    scan    for each directory page: copy its entries and unpin it; for each entry: fetch the data
            page, copy its live records, unpin it, then yield. At most one page is ever pinned.
    delete  marks the slot dead. Free space is unchanged by a delete (nothing is compacted), so the
            directory entry stays correct and is not rewritten."""

    def __init__(self, db, name):
        self.db, self.pool, self.name = db, db.pool, name
        self.meta = db.catalog.tables[name]
        self.cols = sch.parse_schema(self.meta["schema"])
        self.file_no = self.meta["file_no"]
        self.min_need = sch.min_record_size(self.cols) + 4
        self._ents = None
        self._dirs = None
        self._start = 0

    def _g(self, pid):
        return gid(self.file_no, pid)

    # -- directory mirror ---------------------------------------------------
    def _ensure_mirror(self):
        if self._ents is not None:
            return
        ents, dirs, pid = [], [], 0
        while pid != NO_PAGE:
            f = self.pool.fetch_page(self._g(pid))
            d = DirPage(f.data)
            n, nxt = d.entry_count, d.next_dir
            for i in range(n):
                data_pid, free = d.entry(i)
                ents.append([pid, i, data_pid, free])
            self.pool.unpin_page(self._g(pid), False)
            dirs.append([pid, n])
            pid = nxt
        self._ents, self._dirs, self._start = ents, dirs, 0

    def _set_dir_entry(self, dir_pid, idx, data_pid, free):
        f = self.pool.fetch_page(self._g(dir_pid))
        DirPage(f.data).set_entry(idx, data_pid, free)
        self.pool.unpin_page(self._g(dir_pid), True)

    # -- operations ---------------------------------------------------------
    def insert(self, row):
        rec = sch.encode(self.cols, row)
        if len(rec) > MAX_RECORD:
            raise RecordTooLong(f"table {self.name}: row {tuple(row)!r} is {len(rec)} bytes; "
                                f"a page holds at most {MAX_RECORD} bytes per record (limit 4088 minus its 4-byte slot)")
        self._ensure_mirror()
        need = len(rec) + 4
        ents = self._ents
        while self._start < len(ents) and ents[self._start][3] < self.min_need:
            self._start += 1                       # pages that can never take another record
        j = self._start
        while j < len(ents) and ents[j][3] < need:
            j += 1
        if j < len(ents):
            dir_pid, idx, data_pid, _ = ents[j]
            f = self.pool.fetch_page(self._g(data_pid))
            page = SlottedPage(f.data)
            slot = page.insert(rec)
            free = page.free_space()
            self.pool.unpin_page(self._g(data_pid), True)
            self._set_dir_entry(dir_pid, idx, data_pid, free)
            ents[j][3] = free
        else:
            f = self.pool.new_page(self.file_no)
            data_pid = f.page_id & 0xFFFFFFFF
            page = SlottedPage.init(f.data, data_pid)
            slot = page.insert(rec)
            free = page.free_space()
            self.pool.unpin_page(f.page_id, True)
            last = self._dirs[-1]
            if last[1] >= DIR_CAPACITY:            # directory page full: chain a new one
                nf = self.pool.new_page(self.file_no)
                new_pid = nf.page_id & 0xFFFFFFFF
                DirPage.init(nf.data, new_pid)
                self.pool.unpin_page(nf.page_id, True)
                pf = self.pool.fetch_page(self._g(last[0]))
                DirPage(pf.data).set_next(new_pid)
                self.pool.unpin_page(self._g(last[0]), True)
                self._dirs.append([new_pid, 0])
                last = self._dirs[-1]
            df = self.pool.fetch_page(self._g(last[0]))
            DirPage(df.data).add_entry(data_pid, free)
            self.pool.unpin_page(self._g(last[0]), True)
            ents.append([last[0], last[1], data_pid, free])
            last[1] += 1
            self.meta["num_pages"] += 1
        self.meta["num_tuples"] += 1
        return (data_pid, slot)

    def get(self, rid):
        f = self.pool.fetch_page(self._g(rid[0]))
        try:
            rec = SlottedPage(f.data).get(rid[1])
        except SlotError as e:
            raise SlotError(f"rid {tuple(rid)} in table {self.name}: {e}") from None
        finally:
            self.pool.unpin_page(self._g(rid[0]), False)
        return sch.decode(self.cols, rec)

    def delete(self, rid):
        f = self.pool.fetch_page(self._g(rid[0]))
        ok = False
        try:
            SlottedPage(f.data).delete(rid[1])
            ok = True
        except SlotError as e:
            raise SlotError(f"rid {tuple(rid)} in table {self.name}: {e}") from None
        finally:
            self.pool.unpin_page(self._g(rid[0]), ok)
        self.meta["num_tuples"] -= 1

    def scan(self):
        pid = 0
        while pid != NO_PAGE:
            f = self.pool.fetch_page(self._g(pid))
            d = DirPage(f.data)
            entries = [d.entry(i) for i in range(d.entry_count)]
            nxt = d.next_dir
            self.pool.unpin_page(self._g(pid), False)
            for data_pid, _ in entries:
                f = self.pool.fetch_page(self._g(data_pid))
                batch = list(SlottedPage(f.data).live_records())
                self.pool.unpin_page(self._g(data_pid), False)
                for slot, rec in batch:
                    yield (data_pid, slot), sch.decode(self.cols, rec)
            pid = nxt

    def directory_pages(self):
        out, pid = [], 0
        while pid != NO_PAGE:
            out.append(pid)
            f = self.pool.fetch_page(self._g(pid))
            nxt = DirPage(f.data).next_dir
            self.pool.unpin_page(self._g(pid), False)
            pid = nxt
        return out

    def load_csv(self, path):
        """No header line. Fields containing commas are wrapped in double quotes. The first bad
        line stops the load with an error naming the line number and the column."""
        n = 0
        with open(path, newline="", encoding="utf-8") as fh:
            rd = csv.reader(fh)
            for rec in rd:
                line = rd.line_num
                if not rec:
                    continue
                if len(rec) != len(self.cols):
                    raise LoadError(f"line {line}: expected {len(self.cols)} fields, found {len(rec)}")
                row = []
                for k, (value, (cname, ctype, clen)) in enumerate(zip(rec, self.cols), 1):
                    if ctype == "INT":
                        try:
                            v = int(value)
                        except ValueError:
                            raise LoadError(f"line {line}, column {k} ({cname}): {value!r} is not an INT")
                        if not -2**31 <= v < 2**31:
                            raise LoadError(f"line {line}, column {k} ({cname}): {value} does not fit in 4 bytes")
                        row.append(v)
                    else:
                        if len(value.encode("utf-8")) > clen:
                            raise LoadError(f"line {line}, column {k} ({cname}): value is longer than VARCHAR({clen})")
                        row.append(value)
                try:
                    self.insert(row)
                except RecordTooLong as e:
                    raise LoadError(f"line {line}: {e}")
                n += 1
        return n


class Database:
    def __init__(self, path="db", frames=16, policy="lru"):
        os.makedirs(path, exist_ok=True)
        self.path = path
        self.fileset = FileSet()
        self.fileset.add(CATALOG_FILE_NO, DiskManager(os.path.join(path, "catalog.db")))
        self.pool = BufferPool(self.fileset, frames, policy)
        self._heaps = {}
        self.catalog = Catalog(self)
        self.pool.reset_stats()                   # counters describe table access, not catalog loading

    def _open_table_file(self, meta):
        self.fileset.add(meta["file_no"], DiskManager(os.path.join(self.path, meta["file"])))

    def heap(self, name):
        if name not in self.catalog.tables:
            raise UnknownTable(f"unknown table {name}")
        if name not in self._heaps:
            self._heaps[name] = HeapFile(self, name)
        return self._heaps[name]

    def close(self):
        self.catalog.persist()
        self.pool.flush_all()
        self.fileset.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
