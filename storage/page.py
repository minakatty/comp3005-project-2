"""Slotted data pages and heap directory pages, to the byte (M1 spec Sections 3 and 4).

Data page:
    offset  size  field
         0     4  page_id          uint32
         4     2  slot_count       uint16   includes dead slots
         6     2  free_end         uint16   lowest byte offset used by record data
         8     4  slot 0           uint16 offset, uint16 length
        12     4  slot 1 ...
    record data grows backward from 4096, newest record at the lowest offset.

    free space = free_end - (8 + 4 * slot_count); an insert of L bytes needs L + 4.
    A slot with offset 0 is dead. Slots are never reused or renumbered.

Directory page:
    offset  size  field
         0     4  page_id          uint32
         4     4  next_dir_page    uint32, 0xFFFFFFFF if none
         8     2  entry_count      uint16
        10     6  entry i          uint32 data_page_id, uint16 free_bytes
    capacity = floor((4096 - 10) / 6) = 681 entries.

Both classes are views over a bytearray (normally a buffer pool frame), so they
change the frame in place and never copy the page.
"""
import struct

from .errors import PageError

PAGE_SIZE = 4096
HEADER = 8
SLOT = 4
USABLE = PAGE_SIZE - HEADER              # 4088
MAX_RECORD = USABLE - SLOT               # 4084: an insert needs L + 4 <= 4088
NO_PAGE = 0xFFFFFFFF
DIR_HEADER = 10
DIR_ENTRY = 6
DIR_CAPACITY = (PAGE_SIZE - DIR_HEADER) // DIR_ENTRY   # 681

_H = struct.Struct("<IHH")       # page_id, slot_count, free_end
_SLOT = struct.Struct("<HH")
_DH = struct.Struct("<IIH")      # page_id, next_dir_page, entry_count
_DE = struct.Struct("<IH")


class SlottedPage:
    def __init__(self, buf):
        self.buf = buf

    # -- header ------------------------------------------------------------
    @staticmethod
    def init(buf, page_id):
        buf[:] = bytes(PAGE_SIZE)
        _H.pack_into(buf, 0, page_id, 0, PAGE_SIZE)
        return SlottedPage(buf)

    @property
    def page_id(self):
        return _H.unpack_from(self.buf, 0)[0]

    @property
    def slot_count(self):
        return _H.unpack_from(self.buf, 0)[1]

    @property
    def free_end(self):
        return _H.unpack_from(self.buf, 0)[2]

    def free_space(self):
        _, n, fe = _H.unpack_from(self.buf, 0)
        return fe - (HEADER + SLOT * n)

    def slot(self, i):
        return _SLOT.unpack_from(self.buf, HEADER + SLOT * i)

    # -- operations (spec 3.1) ----------------------------------------------
    def insert(self, record):
        """Returns the new slot number, or None for NO_SPACE."""
        ln = len(record)
        pid, n, fe = _H.unpack_from(self.buf, 0)
        if fe - (HEADER + SLOT * n) < ln + SLOT:
            return None
        fe -= ln
        self.buf[fe:fe + ln] = record
        _SLOT.pack_into(self.buf, HEADER + SLOT * n, fe, ln)
        _H.pack_into(self.buf, 0, pid, n + 1, fe)
        return n

    def get(self, slot_no):
        n = self.slot_count
        if slot_no < 0 or slot_no >= n:
            raise PageError(f"slot {slot_no} does not exist (page {self.page_id} has {n} slots)")
        off, ln = self.slot(slot_no)
        if off == 0:
            raise PageError(f"rid ({self.page_id}, {slot_no}) refers to a deleted record")
        return bytes(self.buf[off:off + ln])

    def delete(self, slot_no):
        n = self.slot_count
        if slot_no < 0 or slot_no >= n:
            raise PageError(f"slot {slot_no} does not exist (page {self.page_id} has {n} slots)")
        if self.slot(slot_no)[0] == 0:
            raise PageError(f"rid ({self.page_id}, {slot_no}) is already deleted")
        _SLOT.pack_into(self.buf, HEADER + SLOT * slot_no, 0, 0)
        # space is NOT reclaimed here; compact() reclaims it later, only when an insert
        # needs it (DESIGN_M1.md 4)

    def live(self):
        """(slot_no, offset, length) for every live slot, in slot order."""
        out = []
        for i in range(self.slot_count):
            off, ln = self.slot(i)
            if off != 0:
                out.append((i, off, ln))
        return out

    def reclaimable(self):
        """Bytes in the record area that belong to deleted records."""
        used = sum(ln for _, _, ln in self.live())
        return (PAGE_SIZE - self.free_end) - used

    def usable_free(self):
        """Free space an insert could use after compaction (what the directory lists)."""
        return self.free_space() + self.reclaimable()

    def compact(self):
        """Slide every live record to the end of the page, keeping slot numbers (so
        every rid stays valid). Dead slots keep their 4 directory bytes. Runs only
        when an insert does not fit but would fit after compaction (DESIGN_M1.md 4)."""
        live = [(i, bytes(self.buf[off:off + ln])) for i, off, ln in self.live()]
        pid, n, _ = _H.unpack_from(self.buf, 0)
        fe = PAGE_SIZE
        self.buf[HEADER + SLOT * n:PAGE_SIZE] = bytes(PAGE_SIZE - HEADER - SLOT * n)
        for i, rec in live:
            fe -= len(rec)
            self.buf[fe:fe + len(rec)] = rec
            _SLOT.pack_into(self.buf, HEADER + SLOT * i, fe, len(rec))
        _H.pack_into(self.buf, 0, pid, n, fe)

    def layout(self, hexdump=False):
        """The text the `storage layout` command prints."""
        n, fe = self.slot_count, self.free_end
        lines = [
            f"data page {self.page_id}",
            f"  header   [0..7]     page_id={self.page_id} slot_count={n} free_end={fe}",
            f"  slots    [8..{HEADER + SLOT * n - 1}]     {n} slots x 4 bytes",
            f"  free     [{HEADER + SLOT * n}..{fe - 1}]   {self.free_space()} bytes",
            f"  records  [{fe}..4095]  {PAGE_SIZE - fe} bytes ({self.reclaimable()} of them dead)",
        ]
        for i in range(n):
            off, ln = self.slot(i)
            state = "dead" if off == 0 else f"bytes {off}..{off + ln - 1}"
            lines.append(f"    slot {i:<4} @{HEADER + SLOT * i:<5} ({off},{ln})  {state}")
        if hexdump:
            lines.append("  header+slots hex:")
            lines += _hex(self.buf, 0, HEADER + SLOT * n)
            lines.append("  record area hex:")
            lines += _hex(self.buf, fe, PAGE_SIZE)
        return "\n".join(lines)


class DirectoryPage:
    def __init__(self, buf):
        self.buf = buf

    @staticmethod
    def init(buf, page_id):
        buf[:] = bytes(PAGE_SIZE)
        _DH.pack_into(buf, 0, page_id, NO_PAGE, 0)
        return DirectoryPage(buf)

    @property
    def page_id(self):
        return _DH.unpack_from(self.buf, 0)[0]

    @property
    def next_dir(self):
        return _DH.unpack_from(self.buf, 0)[1]

    @next_dir.setter
    def next_dir(self, v):
        pid, _, cnt = _DH.unpack_from(self.buf, 0)
        _DH.pack_into(self.buf, 0, pid, v, cnt)

    @property
    def entry_count(self):
        return _DH.unpack_from(self.buf, 0)[2]

    def entries(self):
        cnt = self.entry_count
        return [e for e in _DE.iter_unpack(bytes(self.buf[DIR_HEADER:DIR_HEADER + DIR_ENTRY * cnt]))]

    def add(self, data_pid, free):
        pid, nxt, cnt = _DH.unpack_from(self.buf, 0)
        if cnt >= DIR_CAPACITY:
            return False
        _DE.pack_into(self.buf, DIR_HEADER + DIR_ENTRY * cnt, data_pid, free)
        _DH.pack_into(self.buf, 0, pid, nxt, cnt + 1)
        return True

    def set_free(self, index, free):
        pid = _DE.unpack_from(self.buf, DIR_HEADER + DIR_ENTRY * index)[0]
        _DE.pack_into(self.buf, DIR_HEADER + DIR_ENTRY * index, pid, free)

    def layout(self):
        lines = [f"directory page {self.page_id}",
                 f"  header [0..9]  page_id={self.page_id} next_dir_page="
                 f"{'none' if self.next_dir == NO_PAGE else self.next_dir} entry_count={self.entry_count}"]
        for i, (pid, free) in enumerate(self.entries()):
            lines.append(f"    entry {i:<4} @{DIR_HEADER + DIR_ENTRY * i:<5} data_page={pid} free_bytes={free}")
        return "\n".join(lines)


def _hex(buf, start, end):
    out = []
    for a in range(start, end, 16):
        chunk = bytes(buf[a:min(a + 16, end)])
        out.append(f"    {a:04d}  " + " ".join(f"{b:02X}" for b in chunk))
    return out
