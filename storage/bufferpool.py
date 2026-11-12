"""Buffer pool: B frames, pin counts, dirty flags, a page table, and two replacement policies.
Follows the M1 spec section 5 literally.

LRU    Every fetch, hit or miss, makes that frame most recently used. The victim is the least
       recently used frame whose pin count is zero.
CLOCK  Every fetch, hit or miss, sets that frame's reference bit to 1. The hand sweeps:
         pinned frame          -> skip (hand += 1, swept += 1)
         unpinned, ref == 1    -> clear the bit (hand += 1, swept += 1)
         unpinned, ref == 0    -> victim (hand += 1)
       and gives up (returns None) once swept > 2 * B. A newly loaded page gets ref = 1 and the
       hand moves past it.

Counting: hit = fetch of a resident page. miss = fetch that needed a disk read. eviction = a miss
that replaced a resident page (the first B misses on an empty pool are not evictions).
reads / writes = calls to the disk manager since the counters were last reset.
"""
from collections import OrderedDict
from .disk import PAGE_SIZE
from .errors import BufferPoolError, BufferPoolFull, PinError, PinnedPageError  # noqa: F401 (re-exported)


class Frame:
    __slots__ = ("page_id", "data", "pin", "dirty", "ref")

    def __init__(self):
        self.page_id = None
        self.data = None
        self.pin = 0
        self.dirty = False
        self.ref = 0


class BufferPool:
    def __init__(self, disk, frames, policy="lru"):
        if frames < 1:
            raise BufferPoolError("a buffer pool needs at least one frame")
        if policy not in ("lru", "clock"):
            raise BufferPoolError("policy must be 'lru' or 'clock'")
        self.disk, self.B, self.policy = disk, frames, policy
        self.frames = [Frame() for _ in range(frames)]
        self.table = {}                    # page id -> frame index
        self.order = OrderedDict()         # LRU: resident page ids, least recently used first
        self.hand = 0                      # CLOCK hand
        self.hits = self.misses = self.evictions = 0
        self.reset_stats()

    # ---- policy hooks -----------------------------------------------------
    def _on_access(self, idx):
        f = self.frames[idx]
        if self.policy == "lru":
            self.order[f.page_id] = None
            self.order.move_to_end(f.page_id)
        else:
            f.ref = 1

    def _free_frame(self):
        for i, f in enumerate(self.frames):
            if f.page_id is None:
                return i
        return None

    def _choose_victim(self):
        """Index of a frame that may be (re)used, or None if every frame is pinned."""
        i = self._free_frame()
        if i is not None:
            if self.policy == "clock":
                self.hand = (i + 1) % self.B          # the hand moves past a newly loaded page
            return i
        if self.policy == "lru":
            for pid in self.order:                      # oldest first
                idx = self.table[pid]
                if self.frames[idx].pin == 0:
                    return idx
            return None
        swept = 0
        while True:
            idx = self.hand
            f = self.frames[idx]
            if f.pin > 0:
                self.hand = (self.hand + 1) % self.B; swept += 1
            elif f.ref == 1:
                f.ref = 0
                self.hand = (self.hand + 1) % self.B; swept += 1
            else:
                self.hand = (self.hand + 1) % self.B
                return idx
            if swept > 2 * self.B:
                return None

    def _vacate(self, idx):
        """Write back and forget the page in frame idx. Returns True if a page was replaced."""
        f = self.frames[idx]
        if f.page_id is None:
            return False
        if f.pin > 0:
            raise PinnedPageError(f"page {f.page_id} is pinned and cannot be evicted")
        if f.dirty:
            self.disk.write_page(f.page_id, f.data)
        del self.table[f.page_id]
        self.order.pop(f.page_id, None)
        f.page_id, f.data, f.dirty, f.ref = None, None, False, 0
        return True

    # ---- public API ------------------------------------------------------
    def fetch_page(self, page_id):
        idx = self.table.get(page_id)
        if idx is not None:
            f = self.frames[idx]
            f.pin += 1
            self.hits += 1
            self._on_access(idx)
            return f
        idx = self._choose_victim()
        if idx is None:
            raise BufferPoolFull("no unpinned frame available")
        replaced = self._vacate(idx)
        data = self.disk.read_page(page_id)
        f = self.frames[idx]
        f.page_id, f.data, f.pin, f.dirty = page_id, data, 1, False
        self.table[page_id] = idx
        self.misses += 1
        if replaced:
            self.evictions += 1
        self._on_access(idx)
        return f

    def unpin_page(self, page_id, is_dirty=False):
        idx = self.table.get(page_id)
        if idx is None:
            raise PinError(f"cannot unpin page {page_id}: it is not resident")
        f = self.frames[idx]
        if f.pin == 0:
            raise PinError(f"cannot unpin page {page_id}: its pin count is already 0")
        f.dirty = f.dirty or is_dirty
        f.pin -= 1

    def new_page(self, *alloc_args):
        """A zero-filled page, pinned and dirty. `alloc_args` go to disk.allocate_page
        (the file number, when the pool sits on a FileSet)."""
        idx = self._choose_victim()
        if idx is None:
            raise BufferPoolFull("no unpinned frame available")
        self._vacate(idx)
        pid = self.disk.allocate_page(*alloc_args)
        f = self.frames[idx]
        f.page_id, f.data, f.pin, f.dirty = pid, bytearray(PAGE_SIZE), 1, True
        self.table[pid] = idx
        self._on_access(idx)
        return f

    def evict_page(self, page_id):
        """Explicit eviction. Evicting a pinned page is an error, never a silent choice."""
        idx = self.table.get(page_id)
        if idx is not None:
            self._vacate(idx)

    def flush_all(self):
        for f in self.frames:
            if f.page_id is not None and f.dirty:
                self.disk.write_page(f.page_id, f.data)
                f.dirty = False

    def pinned_count(self):
        return sum(1 for f in self.frames if f.page_id is not None and f.pin > 0)

    def resident(self):
        """Page ids by frame index (None for an empty frame). Handy in tests and `layout`."""
        return [f.page_id for f in self.frames]

    def reset_stats(self):
        self.hits = self.misses = self.evictions = 0
        self._r0, self._w0 = self.disk.reads, self.disk.writes

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "reads": self.disk.reads - self._r0, "writes": self.disk.writes - self._w0}
