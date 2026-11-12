"""Slotted page.

Byte layout (all little endian), 4096 bytes:
  0..1   num_slots   u16
  2..3   free_end    u16   offset where record data starts (grows downward from 4096)
  4..7   next_page   i32   heap-file chain, -1 = none
  8..    slot directory, 4 bytes per slot: offset u16, length u16
         offset == 0 means the slot is deleted (a live record can never start at 0)
  ...free space...
  free_end..4095  record data, packed from the back
"""
import struct

PAGE_SIZE = 4096
HEADER = 8
SLOT = 4
_H = struct.Struct("<HHi")
_S = struct.Struct("<HH")


class SlottedPage:
    def __init__(self, data):
        self.data = data

    @staticmethod
    def init(data):
        _H.pack_into(data, 0, 0, PAGE_SIZE, -1)
        return SlottedPage(data)

    @property
    def num_slots(self):
        return _H.unpack_from(self.data, 0)[0]

    @property
    def free_end(self):
        return _H.unpack_from(self.data, 0)[1]

    @property
    def next_page(self):
        return _H.unpack_from(self.data, 0)[2]

    @next_page.setter
    def next_page(self, v):
        n, fe, _ = _H.unpack_from(self.data, 0)
        _H.pack_into(self.data, 0, n, fe, v)

    def _set(self, n, fe):
        _, _, nx = _H.unpack_from(self.data, 0)
        _H.pack_into(self.data, 0, n, fe, nx)

    def slot(self, i):
        return _S.unpack_from(self.data, HEADER + SLOT * i)

    def _set_slot(self, i, off, ln):
        _S.pack_into(self.data, HEADER + SLOT * i, off, ln)

    def free_space(self):
        """Contiguous bytes between slot directory and record data."""
        return self.free_end - (HEADER + SLOT * self.num_slots)

    def _dead_slot(self):
        for i in range(self.num_slots):
            if self.slot(i)[0] == 0:
                return i
        return None

    def usable_space(self):
        """Bytes a new record could use after compaction (includes a new slot cost if needed)."""
        live = sum(ln for off, ln in (self.slot(i) for i in range(self.num_slots)) if off)
        total = PAGE_SIZE - HEADER - SLOT * self.num_slots - live
        return total if self._dead_slot() is not None else total - SLOT

    def insert(self, rec):
        if len(rec) == 0:
            raise ValueError("empty record")
        if len(rec) > self.usable_space():
            return None
        dead = self._dead_slot()
        need = len(rec) + (0 if dead is not None else SLOT)
        if need > self.free_space():
            self.compact()
        n, fe = self.num_slots, self.free_end
        off = fe - len(rec)
        self.data[off:fe] = rec
        if dead is None:
            dead = n
            n += 1
        self._set_slot(dead, off, len(rec))
        self._set(n, off)
        return dead

    def get(self, i):
        if not 0 <= i < self.num_slots:
            return None
        off, ln = self.slot(i)
        return None if off == 0 else bytes(self.data[off:off + ln])

    def delete(self, i):
        if not 0 <= i < self.num_slots or self.slot(i)[0] == 0:
            return False
        self._set_slot(i, 0, 0)
        return True

    def compact(self):
        recs = [(i, self.get(i)) for i in range(self.num_slots)]
        fe = PAGE_SIZE
        for i, r in recs:
            if r is None:
                continue
            fe -= len(r)
            self.data[fe:fe + len(r)] = r
            self._set_slot(i, fe, len(r))
        self._set(self.num_slots, fe)

    def records(self):
        for i in range(self.num_slots):
            r = self.get(i)
            if r is not None:
                yield i, r
