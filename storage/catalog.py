"""The catalog, itself stored in pages (M1 spec Section 6).

catalog.db is an ordinary heap file (directory page 0, slotted data pages) read and
written through the buffer pool like any other table. Its records have the fixed
schema

    kind  VARCHAR(16)    "table" now; Module 4 adds "stats"
    name  VARCHAR(64)    table name
    seq   INT            chunk number, 0, 1, 2 ...
    chunk VARCHAR(200)   a slice of the entry's JSON text

One catalog entry is a JSON object split across as many records as it needs, so an
entry can grow (Module 4 histograms) without changing the page format. A table
entry is

    {"name": "R", "columns": [["a","INT",0],["b","VARCHAR",20]],
     "file": "R.db", "first_page": 0, "num_pages": 3, "num_tuples": 500}

The whole catalog is read into a dictionary once when the database opens; every
change rewrites that entry's records (delete old chunks, insert new ones).
"""
import json

from .errors import CatalogError
from .heap import HeapFile

CATALOG_COLS = [("kind", "VARCHAR", 16), ("name", "VARCHAR", 64),
                ("seq", "INT", 0), ("chunk", "VARCHAR", 200)]
CHUNK = 200


class Catalog:
    def __init__(self, pool, disk):
        self.pool = pool
        self.heap = HeapFile(pool, disk, CATALOG_COLS, "catalog")
        if disk.num_pages() == 0:
            self.heap.create()
        self._entries = {}           # (kind, name) -> dict
        self._rids = {}              # (kind, name) -> [rid, ...]
        self._load()

    def _load(self):
        parts = {}
        for rid, (kind, name, seq, chunk) in self.heap.scan():
            parts.setdefault((kind, name), []).append((seq, chunk, rid))
        for key, chunks in parts.items():
            chunks.sort()
            self._entries[key] = json.loads("".join(c for _, c, _ in chunks))
            self._rids[key] = [rid for _, _, rid in chunks]

    def _put(self, kind, name, obj):
        for rid in self._rids.pop((kind, name), []):
            self.heap.delete(rid)
        text = json.dumps(obj, separators=(",", ":"))
        rids = []
        # split by bytes of UTF-8 would be safer for non-ASCII names; table and
        # column names are identifiers, so characters are bytes here
        for i in range(0, max(len(text), 1), CHUNK):
            rids.append(self.heap.insert((kind, name, i // CHUNK, text[i:i + CHUNK])))
        self._rids[(kind, name)] = rids
        self._entries[(kind, name)] = obj

    def _drop(self, kind, name):
        for rid in self._rids.pop((kind, name), []):
            self.heap.delete(rid)
        self._entries.pop((kind, name), None)

    # -- the contract -------------------------------------------------------
    def create_table(self, name, cols):
        if ("table", name) in self._entries:
            raise CatalogError(f"table '{name}' already exists")
        self._put("table", name, {"name": name, "columns": [list(c) for c in cols],
                                  "file": f"{name}.db", "first_page": 0,
                                  "num_pages": 0, "num_tuples": 0})

    def get_table(self, name):
        e = self._entries.get(("table", name))
        if e is None:
            raise CatalogError(f"unknown table '{name}'")
        return {"name": name, "schema": [tuple(c) for c in e["columns"]],
                "file": e["file"], "first_page": e["first_page"],
                "num_pages": e["num_pages"], "num_tuples": e["num_tuples"]}

    def has_table(self, name):
        return ("table", name) in self._entries

    def list_tables(self):
        return sorted(n for k, n in self._entries if k == "table")

    def update_counts(self, name, num_pages, num_tuples):
        e = dict(self._entries[("table", name)])
        if e["num_pages"] == num_pages and e["num_tuples"] == num_tuples:
            return
        e["num_pages"], e["num_tuples"] = num_pages, num_tuples
        self._put("table", name, e)

    def drop_table(self, name):
        self._drop("table", name)
        self._drop("stats", name)

    # -- Module 4: statistics live in the same structure ---------------------
    def put_stats(self, name, stats):
        self._put("stats", name, stats)

    def get_stats(self, name):
        return self._entries.get(("stats", name))
