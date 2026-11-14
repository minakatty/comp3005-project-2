"""The disk manager: the only code in the system that touches a file (M1 spec 2.1).

One DiskManager per file. Pages are 4096 bytes, addressed by zero-based page id,
with no header outside the pages. Every read_page and write_page call is counted.

Counting goes to an IOStats object shared by every DiskManager of one Database.
Module 2 sets `stats.sink` to the operator currently running, so each read and
write is also attributed to the operator that caused it (M2 spec 1.4).
"""
import os

from .errors import StorageError
from .page import PAGE_SIZE


class IOStats:
    def __init__(self):
        self.reads = 0
        self.writes = 0
        self.sink = None            # object with page_reads / page_writes, or None

    def on_read(self):
        self.reads += 1
        if self.sink is not None:
            self.sink.page_reads += 1

    def on_write(self):
        self.writes += 1
        if self.sink is not None:
            self.sink.page_writes += 1


_next_id = [0]


def _new_file_id():
    _next_id[0] += 1
    return _next_id[0]


class DiskManager:
    def __init__(self, path, stats=None):
        self.path = path
        self.stats = stats or IOStats()
        self.file_id = _new_file_id()
        if not os.path.exists(path):
            open(path, "wb").close()
        self._f = open(path, "r+b")
        size = os.path.getsize(path)
        if size % PAGE_SIZE:
            raise StorageError(f"{path}: size {size} is not a multiple of {PAGE_SIZE}")
        self._n = size // PAGE_SIZE

    def num_pages(self):
        return self._n

    def allocate_page(self):
        """Reserve the next page id. The page's bytes reach the file on its first
        write; until then a read returns zeros. Allocation is not a counted I/O."""
        pid = self._n
        self._n += 1
        return pid

    def read_page(self, page_id):
        if not 0 <= page_id < self._n:
            raise StorageError(f"{os.path.basename(self.path)}: page {page_id} does not exist "
                               f"(file has {self._n} pages)")
        self.stats.on_read()
        self._f.seek(page_id * PAGE_SIZE)
        data = self._f.read(PAGE_SIZE)
        return data + bytes(PAGE_SIZE - len(data))

    def write_page(self, page_id, data):
        if len(data) != PAGE_SIZE:
            raise StorageError(f"write of {len(data)} bytes; pages are {PAGE_SIZE}")
        if not 0 <= page_id < self._n:
            raise StorageError(f"page {page_id} was never allocated")
        self.stats.on_write()
        self._f.seek(page_id * PAGE_SIZE)
        self._f.write(data)

    def close(self):
        if not self._f.closed:
            self._f.flush()
            self._f.close()

    def delete(self):
        self.close()
        if os.path.exists(self.path):
            os.remove(self.path)


class MemoryDisk:
    """A disk with no file behind it, used by the `trace` command. Any page id
    can be read (it reads as zeros); reads and writes are counted the same way."""

    def __init__(self, stats=None):
        self.path = "<memory>"
        self.stats = stats or IOStats()
        self.file_id = _new_file_id()
        self._pages = {}
        self._n = 0

    def num_pages(self):
        return self._n

    def allocate_page(self):
        pid = self._n
        self._n += 1
        return pid

    def read_page(self, page_id):
        self.stats.on_read()
        self._n = max(self._n, page_id + 1)
        return self._pages.get(page_id, bytes(PAGE_SIZE))

    def write_page(self, page_id, data):
        self.stats.on_write()
        self._pages[page_id] = bytes(data)

    def close(self):
        pass

    def delete(self):
        self._pages.clear()
