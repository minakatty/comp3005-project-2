"""Database: one directory, one buffer pool, the catalog and every table's heap file.

This is the object the upper modules hold. It is the only place a DiskManager is
created, so it is the only place a file is opened.

    db = Database("db", frames=8, policy="lru")
    db.create_table("R", parse_schema("a:INT,b:VARCHAR(20)"))
    db.load_csv("R", "r.csv")
    for rid, row in db.heap("R").scan(): ...
    db.close()
"""
import csv
import io
import os

from . import record
from .bufferpool import BufferPool
from .catalog import Catalog
from .disk import DiskManager, IOStats
from .errors import CatalogError, LoadError, StorageError, read_text
from .heap import HeapFile
from .page import MAX_RECORD, NO_PAGE, DirectoryPage


class Database:
    def __init__(self, path, frames=64, policy="lru"):
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.io = IOStats()
        self.pool = BufferPool(frames, policy)
        self._disks = {}
        self.catalog = Catalog(self.pool, self._disk("catalog.db"))
        self._temp_seq = 0
        self._temps = []

    # -- files ----------------------------------------------------------------
    def _disk(self, filename):
        d = self._disks.get(filename)
        if d is None:
            d = DiskManager(os.path.join(self.path, filename), self.io)
            self._disks[filename] = d
        return d

    def heap(self, table):
        info = self.catalog.get_table(table)
        return HeapFile(self.pool, self._disk(info["file"]), info["schema"], table)

    def table_disk(self, table):
        return self._disk(self.catalog.get_table(table)["file"])

    def new_temp(self):
        """A fresh temporary file for Module 2 (sort runs, hash partitions)."""
        self._temp_seq += 1
        name = f"tmp_{os.getpid()}_{self._temp_seq}.tmp"
        d = DiskManager(os.path.join(self.path, name), self.io)
        self._temps.append(d)
        return d

    def drop_temp(self, disk):
        self.pool.drop_file(disk)
        disk.delete()
        if disk in self._temps:
            self._temps.remove(disk)

    def temp_files_left(self):
        return [d.path for d in self._temps]

    # -- tables -----------------------------------------------------------------
    def create_table(self, name, cols):
        if not name or not (name[0].isalpha() or name[0] == "_") or \
                not all(ch.isalnum() or ch == "_" for ch in name):
            raise CatalogError(f"bad table name '{name}'")
        if record.max_record_length(cols) > MAX_RECORD:
            raise CatalogError(f"table {name}: a record could be {record.max_record_length(cols)} "
                               f"bytes, more than a page holds ({MAX_RECORD})")
        if self.catalog.has_table(name):
            raise CatalogError(f"table '{name}' already exists")
        if os.path.exists(os.path.join(self.path, f"{name}.db")):
            os.remove(os.path.join(self.path, f"{name}.db"))
        self.catalog.create_table(name, cols)
        HeapFile(self.pool, self._disk(f"{name}.db"), cols, name).create()

    def drop_table(self, name):
        disk = self.table_disk(name)
        self.pool.drop_file(disk)
        disk.delete()
        del self._disks[f"{name}.db"]
        self.catalog.drop_table(name)

    def insert(self, table, values):
        h = self.heap(table)
        rid = h.insert(values)
        info = self.catalog.get_table(table)
        self.catalog.update_counts(table, self.count_data_pages(table), info["num_tuples"] + 1)
        return rid

    def delete(self, table, rid):
        self.heap(table).delete(rid)
        info = self.catalog.get_table(table)
        self.catalog.update_counts(table, info["num_pages"], info["num_tuples"] - 1)

    def count_data_pages(self, table):
        disk = self.table_disk(table)
        n, dir_pid = 0, 0
        while dir_pid != NO_PAGE:
            fr = self.pool.fetch_page(disk, dir_pid)
            d = DirectoryPage(fr.data)
            n += d.entry_count
            nxt = d.next_dir
            self.pool.unpin_page(disk, dir_pid, False)
            dir_pid = nxt
        return n

    def directory_pages(self, table):
        disk = self.table_disk(table)
        out, dir_pid = [], 0
        while dir_pid != NO_PAGE:
            out.append(dir_pid)
            fr = self.pool.fetch_page(disk, dir_pid)
            nxt = DirectoryPage(fr.data).next_dir
            self.pool.unpin_page(disk, dir_pid, False)
            dir_pid = nxt
        return out

    # -- bulk loader (spec Section 6) --------------------------------------------
    def load_csv(self, table, path):
        """Validate the whole file first, then insert, so a bad line loads nothing.
        No header line; comma separated; a field with a comma is double quoted."""
        cols = self.catalog.get_table(table)["schema"]
        if not os.path.exists(path):
            raise LoadError(f"file not found: {path}")
        rows = list(self._parse_csv(cols, path))
        h = self.heap(table)
        for lineno, row in rows:
            h.insert(row, row_label=lineno)
        info = self.catalog.get_table(table)
        self.catalog.update_counts(table, self.count_data_pages(table), info["num_tuples"] + len(rows))
        return len(rows)

    @staticmethod
    def _parse_csv(cols, path):
        text = read_text(path)
        reader = csv.reader(io.StringIO(text), skipinitialspace=False)
        lineno = 0
        try:
            for fields in reader:
                lineno = reader.line_num
                if not fields or (len(fields) == 1 and fields[0] == ""):
                    continue
                if len(fields) != len(cols):
                    raise LoadError(f"line {lineno}: {len(fields)} fields, table has {len(cols)} columns")
                row = []
                for (name, typ, n), v in zip(cols, fields):
                    if typ == "INT":
                        s = v.strip()
                        body = s[1:] if s[:1] in "+-" else s
                        if not body.isdigit() or not body.isascii():
                            raise LoadError(f"line {lineno}, column {name}: '{v}' is not an INT")
                        iv = int(s)
                        if not record.INT_MIN <= iv <= record.INT_MAX:
                            raise LoadError(f"line {lineno}, column {name}: {v} does not fit in 32 bits")
                        row.append(iv)
                    else:
                        if len(v.encode("utf-8")) > n:
                            raise LoadError(f"line {lineno}, column {name}: value is "
                                            f"{len(v.encode('utf-8'))} bytes, longer than VARCHAR({n})")
                        row.append(v)
                yield lineno, tuple(row)
        except csv.Error as e:
            raise LoadError(f"line {lineno + 1}: {e}")

    # -- shutdown -------------------------------------------------------------------
    def flush(self):
        self.pool.flush_all()

    def close(self):
        for d in list(self._temps):
            self.drop_temp(d)
        self.pool.flush_all()
        for d in self._disks.values():
            d.close()


def open_db(path, frames=64, policy="lru"):
    if not os.path.isdir(path):
        raise StorageError(f"no database directory '{path}' (create a table first)")
    return Database(path, frames, policy)
