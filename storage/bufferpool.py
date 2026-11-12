"""Buffer pool with pinning, dirty tracking, and LRU or CLOCK replacement.

LRU:   recency = time of last unpin; victim = least recently unpinned page.
CLOCK: ref bit set on load and on every hit; hand sweeps frames, skipping pinned
       ones, clearing ref bits until it finds an unpinned frame with ref == 0.
Evicting a pinned page is an error (PinnedPageError), never a silent choice.
"""
from collections import OrderedDict
from .disk import PAGE_SIZE


class PinnedPageError(Exception):
    pass


class BufferPoolFull(Exception):
    pass


class Frame:
    __slots__ = ("page_id", "data", "pin", "dirty", "ref")

    def __init__(self, page_id, data):
        self.page_id, self.data = page_id, data
        self.pin, self.dirty, self.ref = 0, False, 1


class BufferPool:
    def __init__(self, disk, frames, policy="lru"):
        if frames < 1:
            raise ValueError("need at least one frame")
        if policy not in ("lru", "clock"):
            raise ValueError("policy must be lru or clock")
        self.disk, self.B, self.policy = disk, frames, policy
        self.table = {}                 # page_id -> slot index
        self.slots = [None] * frames    # slot index -> Frame
        self.lru = OrderedDict()        # unpinned page ids, oldest first
        self.hand = 0
        self.hits = self.misses = self.evictions = 0
        self._r0, self._w0 = disk.reads, disk.writes

    # ---- victim selection -------------------------------------------------
    def _free_slot(self):
        for i, f in enumerate(self.slots):
            if f is None:
                return i
        return None

    def _victim_slot(self):
        i = self._free_slot()
        if i is not None:
            return i
        if self.policy == "lru":
            if not self.lru:
                raise BufferPoolFull("all frames are pinned")
            pid = next(iter(self.lru))
            return self.table[pid]
        for _ in range(2 * self.B + 1):
            f = self.slots[self.hand]
            i = self.hand
            self.hand = (self.hand + 1) % self.B
            if f.pin > 0:
                continue
            if f.ref:
                f.ref = 0
                continue
            return i
        raise BufferPoolFull("all frames are pinned")

    def _evict_slot(self, i):
        f = self.slots[i]
        if f is None:
            return
        if f.pin > 0:
            raise PinnedPageError(f"page {f.page_id} is pinned")
        if f.dirty:
            self.disk.write_page(f.page_id, f.data)
        self.lru.pop(f.page_id, None)
        del self.table[f.page_id]
        self.slots[i] = None
        self.evictions += 1

    def _install(self, frame):
        i = self._victim_slot()
        self._evict_slot(i)
        self.slots[i] = frame
        self.table[frame.page_id] = i

    # ---- public API --------------------------------------------------------
    def fetch_page(self, page_id):
        i = self.table.get(page_id)
        if i is not None:
            f = self.slots[i]
            self.hits += 1
            f.ref = 1
            if f.pin == 0:
                self.lru.pop(page_id, None)
            f.pin += 1
            return f
        self.misses += 1
        f = Frame(page_id, None)
        # choose the victim first so a failure leaves the pool unchanged
        i = self._victim_slot()
        self._evict_slot(i)
        f.data = self.disk.read_page(page_id)
        f.pin = 1
        self.slots[i] = f
        self.table[page_id] = i
        return f

    def unpin_page(self, page_id, is_dirty=False):
        f = self.slots[self.table[page_id]]
        if f.pin <= 0:
            raise ValueError(f"page {page_id} is not pinned")
        f.pin -= 1
        f.dirty = f.dirty or is_dirty
        if f.pin == 0:
            self.lru.pop(page_id, None)
            self.lru[page_id] = True

    def new_page(self):
        i = self._victim_slot()
        self._evict_slot(i)
        pid = self.disk.allocate_page()
        f = Frame(pid, bytearray(PAGE_SIZE))
        f.pin, f.dirty = 1, True
        self.slots[i] = f
        self.table[pid] = i
        return f

    def evict_page(self, page_id):
        """Explicitly evict one page. Raises PinnedPageError if it is pinned."""
        i = self.table.get(page_id)
        if i is not None:
            self._evict_slot(i)

    def flush_all(self):
        for f in self.slots:
            if f is not None and f.dirty:
                self.disk.write_page(f.page_id, f.data)
                f.dirty = False

    def pinned_count(self):
        return sum(1 for f in self.slots if f is not None and f.pin > 0)

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "reads": self.disk.reads - self._r0, "writes": self.disk.writes - self._w0}
