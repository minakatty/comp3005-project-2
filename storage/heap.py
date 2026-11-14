"""Heap files (M1 spec Section 4).

A heap file is a linked list of directory pages, each listing data pages and their
free bytes. Page 0 of every table file is the first directory page. Every page
access goes through the buffer pool; this module never touches the disk directly.

rid = (page_id, slot_no).
"""
from . import record
from .errors import PageError
from .page import (DIR_CAPACITY, MAX_RECORD, NO_PAGE, SLOT, DirectoryPage, SlottedPage)


class HeapFile:
    def __init__(self, pool, disk, cols, table="?"):
        self.pool = pool
        self.disk = disk
        self.cols = cols
        self.table = table

    # -- creation -----------------------------------------------------------
    def create(self):
        """Write the first directory page (page 0) of an empty file."""
        pid, frame = self.pool.new_page(self.disk)
        assert pid == 0, "a heap file's first page must be its directory"
        DirectoryPage.init(frame.data, 0)
        self.pool.unpin_page(self.disk, 0, True)

    # -- insert ---------------------------------------------------------------
    def insert(self, values, row_label=None):
        rec = record.encode(self.cols, values)
        return self.insert_bytes(rec, row_label)

    def insert_bytes(self, rec, row_label=None):
        need = len(rec) + SLOT
        if len(rec) > MAX_RECORD:
            where = f", row {row_label}" if row_label is not None else ""
            raise PageError(f"table {self.table}{where}: record of {len(rec)} bytes is longer "
                            f"than a page can hold ({MAX_RECORD} bytes)")
        dir_pid = 0
        while True:
            dframe = self.pool.fetch_page(self.disk, dir_pid)
            dpage = DirectoryPage(dframe.data)
            for i, (data_pid, free) in enumerate(dpage.entries()):     # first fit
                if free >= need:
                    fr = self.pool.fetch_page(self.disk, data_pid)
                    page = SlottedPage(fr.data)
                    slot = page.insert(rec)
                    if slot is None:                   # fits only after compaction
                        page.compact()
                        slot = page.insert(rec)
                    dpage.set_free(i, page.usable_free())
                    self.pool.unpin_page(self.disk, data_pid, True)
                    self.pool.unpin_page(self.disk, dir_pid, True)
                    return (data_pid, slot)
            nxt = dpage.next_dir
            if nxt != NO_PAGE:
                self.pool.unpin_page(self.disk, dir_pid, False)
                dir_pid = nxt
                continue
            # no page has room: add a data page to this directory, or chain a new one
            if dpage.entry_count >= DIR_CAPACITY:
                new_dir, nf = self.pool.new_page(self.disk)
                DirectoryPage.init(nf.data, new_dir)
                dpage.next_dir = new_dir
                self.pool.unpin_page(self.disk, dir_pid, True)
                dir_pid, dpage = new_dir, DirectoryPage(nf.data)
            data_pid, fr = self.pool.new_page(self.disk)
            page = SlottedPage.init(fr.data, data_pid)
            slot = page.insert(rec)
            dpage.add(data_pid, page.usable_free())
            self.pool.unpin_page(self.disk, data_pid, True)
            self.pool.unpin_page(self.disk, dir_pid, True)
            return (data_pid, slot)

    # -- get / delete -----------------------------------------------------------
    def get(self, rid):
        pid, slot = rid
        self._check_data_page(pid)
        fr = self.pool.fetch_page(self.disk, pid)
        try:
            return record.decode(self.cols, SlottedPage(fr.data).get(slot))
        finally:
            self.pool.unpin_page(self.disk, pid, False)

    def delete(self, rid):
        pid, slot = rid
        self._check_data_page(pid)
        fr = self.pool.fetch_page(self.disk, pid)
        try:
            page = SlottedPage(fr.data)
            page.delete(slot)
            free = page.usable_free()
        except PageError:
            self.pool.unpin_page(self.disk, pid, False)
            raise
        self.pool.unpin_page(self.disk, pid, True)
        # the directory lists usable free space (free + reclaimable), so a later
        # insert can find this page and compact it
        dir_pid = 0
        while dir_pid != NO_PAGE:
            dframe = self.pool.fetch_page(self.disk, dir_pid)
            dpage = DirectoryPage(dframe.data)
            for i, (data_pid, _) in enumerate(dpage.entries()):
                if data_pid == pid:
                    dpage.set_free(i, free)
                    self.pool.unpin_page(self.disk, dir_pid, True)
                    return
            nxt = dpage.next_dir
            self.pool.unpin_page(self.disk, dir_pid, False)
            dir_pid = nxt

    def _check_data_page(self, pid):
        if not 0 < pid < self.disk.num_pages():
            raise PageError(f"table {self.table}: rid page {pid} is not a data page")

    # -- scan -------------------------------------------------------------------
    def data_pages(self):
        """Data page ids in directory order. Reads each directory page once."""
        out, dir_pid = [], 0
        while dir_pid != NO_PAGE:
            dframe = self.pool.fetch_page(self.disk, dir_pid)
            dpage = DirectoryPage(dframe.data)
            out += [pid for pid, _ in dpage.entries()]
            nxt = dpage.next_dir
            self.pool.unpin_page(self.disk, dir_pid, False)
            dir_pid = nxt
        return out

    def scan(self):
        """Yield (rid, tuple) for every live record, in heap order.

        At most one directory page and one data page are pinned at any instant;
        in fact the directory page is unpinned before its data pages are fetched,
        so a scan holds exactly one frame while it is between yields. If the caller
        stops early, closing the generator unpins the current page (finally)."""
        dir_pid = 0
        while dir_pid != NO_PAGE:
            dframe = self.pool.fetch_page(self.disk, dir_pid)
            dpage = DirectoryPage(dframe.data)
            entries = [pid for pid, _ in dpage.entries()]
            nxt = dpage.next_dir
            self.pool.unpin_page(self.disk, dir_pid, False)
            for pid in entries:
                fr = self.pool.fetch_page(self.disk, pid)
                try:
                    page = SlottedPage(fr.data)
                    for slot, off, _ in page.live():
                        yield (pid, slot), record.decode(self.cols, fr.data, off)
                finally:
                    self.pool.unpin_page(self.disk, pid, False)
            dir_pid = nxt
