"""DiskManager: the only code that touches a file. Counts every page read/write."""
import os

PAGE_SIZE = 4096


class DiskManager:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "r+b" if os.path.exists(path) else "w+b")
        self.f.seek(0, 2)
        self._n = self.f.tell() // PAGE_SIZE
        self.reads = 0
        self.writes = 0

    def read_page(self, page_id):
        if not 0 <= page_id < self._n:
            raise IndexError(f"page {page_id} does not exist")
        self.f.seek(page_id * PAGE_SIZE)
        self.reads += 1
        return bytearray(self.f.read(PAGE_SIZE))

    def write_page(self, page_id, data):
        if len(data) != PAGE_SIZE:
            raise ValueError("page must be exactly 4096 bytes")
        if not 0 <= page_id < self._n:
            raise IndexError(f"page {page_id} does not exist")
        self.f.seek(page_id * PAGE_SIZE)
        self.f.write(bytes(data))
        self.writes += 1

    def allocate_page(self):
        # Zero-extends the file. Not counted as a write: the new page is
        # written (and counted) when the buffer pool flushes it.
        pid = self._n
        self._n += 1
        self.f.seek(pid * PAGE_SIZE)
        self.f.write(bytes(PAGE_SIZE))
        return pid

    def num_pages(self):
        return self._n

    def close(self):
        self.f.flush()
        self.f.close()


class NullDisk:
    """In-memory stand-in used by `storage trace`: pages are all zeros, I/O is counted."""

    def __init__(self, n_pages):
        self._n = n_pages
        self.reads = 0
        self.writes = 0

    def read_page(self, page_id):
        self.reads += 1
        return bytearray(PAGE_SIZE)

    def write_page(self, page_id, data):
        self.writes += 1

    def allocate_page(self):
        self._n += 1
        return self._n - 1

    def num_pages(self):
        return self._n
