"""Disk managers. The only code in the system that touches a file.

DiskManager: one file of 4096-byte pages, page id = zero-based index in the file.
FileSet:     several DiskManagers behind ONE buffer pool. The pool works on a single integer
             key, so a page of file f with page id p is addressed as (f << 32) | p.
NullDisk:    in-memory stand-in used by `storage trace` (pages are zeros, I/O is counted).
"""
import os
from .errors import StorageError

PAGE_SIZE = 4096
MASK = 0xFFFFFFFF


class DiskManager:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "r+b" if os.path.exists(path) else "w+b")
        self.f.seek(0, 2)
        size = self.f.tell()
        if size % PAGE_SIZE:
            raise StorageError(f"{path}: size {size} is not a multiple of {PAGE_SIZE}")
        self._n = size // PAGE_SIZE
        self.reads = 0
        self.writes = 0

    def read_page(self, page_id):
        if not 0 <= page_id < self._n:
            raise StorageError(f"{os.path.basename(self.path)}: page {page_id} does not exist")
        self.f.seek(page_id * PAGE_SIZE)
        self.reads += 1                      # exactly one count per call
        return bytearray(self.f.read(PAGE_SIZE))

    def write_page(self, page_id, data):
        if len(data) != PAGE_SIZE:
            raise StorageError("a page must be exactly 4096 bytes")
        if not 0 <= page_id < self._n:
            raise StorageError(f"{os.path.basename(self.path)}: page {page_id} does not exist")
        self.f.seek(page_id * PAGE_SIZE)
        self.f.write(bytes(data))
        self.writes += 1                     # exactly one count per call

    def allocate_page(self):
        """Zero-extends the file by one page. Not counted as a write: the page is counted
        when the buffer pool writes it (eviction or flush)."""
        pid = self._n
        self._n += 1
        self.f.seek(pid * PAGE_SIZE)
        self.f.write(bytes(PAGE_SIZE))
        return pid

    def num_pages(self):
        return self._n

    def close(self):
        if not self.f.closed:
            self.f.flush()
            self.f.close()


class FileSet:
    def __init__(self):
        self.files = {}

    def add(self, file_no, disk):
        self.files[file_no] = disk

    @property
    def reads(self):
        return sum(d.reads for d in self.files.values())

    @property
    def writes(self):
        return sum(d.writes for d in self.files.values())

    def read_page(self, gid):
        return self.files[gid >> 32].read_page(gid & MASK)

    def write_page(self, gid, data):
        self.files[gid >> 32].write_page(gid & MASK, data)

    def allocate_page(self, file_no=0):
        return (file_no << 32) | self.files[file_no].allocate_page()

    def num_pages(self, file_no):
        return self.files[file_no].num_pages()

    def close(self):
        for d in self.files.values():
            d.close()


class NullDisk:
    def __init__(self):
        self.reads = 0
        self.writes = 0
        self._n = 0

    def read_page(self, page_id):
        self.reads += 1
        return bytearray(PAGE_SIZE)

    def write_page(self, page_id, data):
        self.writes += 1

    def allocate_page(self, file_no=0):
        self._n += 1
        return self._n - 1

    def num_pages(self, file_no=0):
        return self._n
