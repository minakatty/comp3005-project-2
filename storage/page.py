"""Slotted data page and heap directory page, byte for byte as in the M1 spec (little endian).

DATA PAGE
  offset size field
     0     4  page_id     uint32
     4     2  slot_count  uint16   includes dead slots
     6     2  free_end    uint16   lowest byte offset used by record data
     8     4  slot 0      uint16 offset, uint16 length      (offset 0 = dead)
   ...                    slot directory grows forward; record data grows backward from byte 4095
Free space = free_end - (8 + 4 * slot_count). Inserting L bytes needs L + 4.
Slot numbers are never reused or renumbered. Delete only marks the slot; nothing is compacted.

DIRECTORY PAGE
     0     4  page_id        uint32
     4     4  next_dir_page  uint32   0xFFFFFFFF if none
     8     2  entry_count    uint16
    10     6  entry 0        uint32 data_page_id, uint16 free_bytes
  capacity = floor((4096 - 10) / 6) = 681 entries
"""
import struct
from .errors import SlotError

PAGE_SIZE = 4096
HEADER = 8
SLOT = 4
MAX_RECORD = PAGE_SIZE - HEADER - SLOT          # largest record an empty page can take (4084)
NO_PAGE = 0xFFFFFFFF
DIR_HEADER = 10
DIR_ENTRY = 6
DIR_CAPACITY = (PAGE_SIZE - DIR_HEADER) // DIR_ENTRY   # 681

_H = struct.Struct("<IHH")
_S = struct.Struct("<HH")
_D = struct.Struct("<IIH")
_E = struct.Struct("<IH")


class SlottedPage:
    def __init__(self, data):
        self.data = data

    @staticmethod
    def init(data, page_id):
        data[:] = bytes(PAGE_SIZE)
        _H.pack_into(data, 0, page_id, 0, PAGE_SIZE)
        return SlottedPage(data)

    page_id = property(lambda self: _H.unpack_from(self.data, 0)[0])
    slot_count = property(lambda self: _H.unpack_from(self.data, 0)[1])
    free_end = property(lambda self: _H.unpack_from(self.data, 0)[2])

    def free_space(self):
        return self.free_end - (HEADER + SLOT * self.slot_count)

    def slot(self, i):
        return _S.unpack_from(self.data, HEADER + SLOT * i)

    def insert(self, rec):
        """Returns the slot number, or None for NO_SPACE."""
        n, fe = self.slot_count, self.free_end
        length = len(rec)
        if fe - (HEADER + SLOT * n) < length + SLOT:
            return None
        fe -= length
        self.data[fe:fe + length] = rec
        _S.pack_into(self.data, HEADER + SLOT * n, fe, length)
        _H.pack_into(self.data, 0, self.page_id, n + 1, fe)
        return n

    def get(self, i):
        n = self.slot_count
        if not 0 <= i < n:
            raise SlotError(f"slot {i} is out of range (slot_count = {n})")
        off, ln = self.slot(i)
        if off == 0:
            raise SlotError(f"slot {i} is dead (deleted)")
        return bytes(self.data[off:off + ln])

    def delete(self, i):
        n = self.slot_count
        if not 0 <= i < n:
            raise SlotError(f"slot {i} is out of range (slot_count = {n})")
        if self.slot(i)[0] == 0:
            raise SlotError(f"slot {i} is already dead")
        _S.pack_into(self.data, HEADER + SLOT * i, 0, 0)     # space is NOT reclaimed

    def live_records(self):
        for i in range(self.slot_count):
            off, ln = self.slot(i)
            if off:
                yield i, bytes(self.data[off:off + ln])


class DirPage:
    def __init__(self, data):
        self.data = data

    @staticmethod
    def init(data, page_id):
        data[:] = bytes(PAGE_SIZE)
        _D.pack_into(data, 0, page_id, NO_PAGE, 0)
        return DirPage(data)

    page_id = property(lambda self: _D.unpack_from(self.data, 0)[0])
    next_dir = property(lambda self: _D.unpack_from(self.data, 0)[1])
    entry_count = property(lambda self: _D.unpack_from(self.data, 0)[2])

    def set_next(self, pid):
        pg, _, n = _D.unpack_from(self.data, 0)
        _D.pack_into(self.data, 0, pg, pid, n)

    def entry(self, i):
        return _E.unpack_from(self.data, DIR_HEADER + DIR_ENTRY * i)

    def set_entry(self, i, data_pid, free):
        _E.pack_into(self.data, DIR_HEADER + DIR_ENTRY * i, data_pid, free)

    def add_entry(self, data_pid, free):
        n = self.entry_count
        if n >= DIR_CAPACITY:
            raise SlotError("directory page is full")
        self.set_entry(n, data_pid, free)
        pg, nx, _ = _D.unpack_from(self.data, 0)
        _D.pack_into(self.data, 0, pg, nx, n + 1)
        return n
