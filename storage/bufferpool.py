"""The buffer pool (M1 spec Section 5), with LRU and CLOCK replacement.

A fixed array of B frames. Each frame holds one page's bytes, a pin count and a
dirty flag. The page table maps a page key to a frame index. Because the system
has one file per table, a page key is (file, page_id); `disk` below is the
DiskManager of that file.

Counting rules (exact, from the spec):
    hit       a fetch of a resident page
    miss      a fetch that needed a disk read
    eviction  a miss that replaced a resident page (the first B misses into an
              empty pool are not evictions)
    read      one call to disk.read_page
    write     one call to disk.write_page, from eviction or flush

Module 2 extension: reserve(k) takes k frames out of circulation as operator
working memory (sort buffers, hash tables, a nested-loop outer block). A reserved
frame is treated exactly like a pinned one: it can never be chosen as a victim.
Because pinned pages and reserved frames come out of the same B frames, a plan can
never hold more than B pages at once; `peak_held` records the most it ever did.
"""
from collections import OrderedDict

from .errors import BufferPoolError
from .page import PAGE_SIZE


class Frame:
    __slots__ = ("index", "disk", "page_id", "data", "pin_count", "dirty", "ref", "reserved")

    def __init__(self, index):
        self.index = index
        self.disk = None
        self.page_id = None
        self.data = bytearray(PAGE_SIZE)
        self.pin_count = 0
        self.dirty = False
        self.ref = 0
        self.reserved = False

    @property
    def occupied(self):
        return self.disk is not None

    def evictable(self):
        return self.pin_count == 0 and not self.reserved


class LRUPolicy:
    """Every fetch, hit or miss, moves the frame to most recently used. The victim
    is the least recently used frame whose pin count is zero."""
    name = "lru"

    def __init__(self, frames):
        self.frames = frames
        self.order = OrderedDict()          # frame index -> None, oldest first

    def on_access(self, frame):
        self.order.pop(frame.index, None)
        self.order[frame.index] = None

    def on_remove(self, frame):
        self.order.pop(frame.index, None)

    def choose_victim(self):
        for idx in self.order:
            if self.frames[idx].evictable():
                return self.frames[idx]
        return None


class ClockPolicy:
    """Frames in a circle, one reference bit each, one hand. Every fetch sets the
    frame's bit to 1; a newly loaded page gets bit 1 and the hand advances past it."""
    name = "clock"

    def __init__(self, frames):
        self.frames = frames
        self.hand = 0

    def on_access(self, frame, loaded=False):
        frame.ref = 1
        if loaded:
            self.hand = (frame.index + 1) % len(self.frames)

    def on_remove(self, frame):
        frame.ref = 0

    def choose_victim(self):
        b = len(self.frames)
        swept = 0
        while True:
            f = self.frames[self.hand]
            if not f.evictable():
                self.hand = (self.hand + 1) % b
                swept += 1
            elif f.ref == 1:
                f.ref = 0
                self.hand = (self.hand + 1) % b
                swept += 1
            else:
                self.hand = (self.hand + 1) % b
                return f
            if swept > 2 * b:
                return None


class BufferPool:
    def __init__(self, frames, policy="lru"):
        if frames < 1:
            raise BufferPoolError("the buffer pool needs at least one frame")
        self.B = frames
        self.frames = [Frame(i) for i in range(frames)]
        self.page_table = {}                 # (file_id, page_id) -> frame
        if policy == "lru":
            self.policy = LRUPolicy(self.frames)
        elif policy == "clock":
            self.policy = ClockPolicy(self.frames)
        else:
            raise BufferPoolError(f"unknown policy '{policy}' (use lru or clock)")
        self.hits = self.misses = self.evictions = self.reads = self.writes = 0
        self.reserved_count = 0
        self.peak_held = 0

    # -- helpers ------------------------------------------------------------
    def held(self):
        """Frames currently pinned or reserved."""
        return sum(1 for f in self.frames if f.pin_count > 0 or f.reserved)

    def _note_peak(self):
        h = self.held()
        if h > self.peak_held:
            self.peak_held = h

    def _access(self, frame, loaded):
        if isinstance(self.policy, ClockPolicy):
            self.policy.on_access(frame, loaded)
        else:
            self.policy.on_access(frame)

    def _free_frame(self):
        for f in self.frames:
            if not f.occupied and not f.reserved:
                return f
        return None

    def _grab_frame(self):
        """An empty frame if one exists, else a victim (written back if dirty)."""
        frame = self._free_frame()
        if frame is not None:
            return frame, False
        frame = self.policy.choose_victim()
        if frame is None:
            raise BufferPoolError("no unpinned frame available: every frame is pinned or reserved")
        if frame.dirty:
            frame.disk.write_page(frame.page_id, bytes(frame.data))
            self.writes += 1
        del self.page_table[(frame.disk.file_id, frame.page_id)]
        self.policy.on_remove(frame)
        frame.disk = frame.page_id = None
        frame.dirty = False
        return frame, True

    # -- the contract ---------------------------------------------------------
    def fetch_page(self, disk, page_id):
        """Pin and return the frame holding page_id of `disk`. frame.data is the page."""
        key = (disk.file_id, page_id)
        frame = self.page_table.get(key)
        if frame is not None:
            frame.pin_count += 1
            self.hits += 1
            self._access(frame, loaded=False)
            self._note_peak()
            return frame
        frame, evicted = self._grab_frame()
        frame.data[:] = disk.read_page(page_id)
        self.reads += 1
        self.misses += 1
        if evicted:
            self.evictions += 1
        frame.disk, frame.page_id = disk, page_id
        frame.pin_count, frame.dirty = 1, False
        self.page_table[key] = frame
        self._access(frame, loaded=True)
        self._note_peak()
        return frame

    def unpin_page(self, disk, page_id, is_dirty=False):
        frame = self.page_table.get((disk.file_id, page_id))
        if frame is None:
            raise BufferPoolError(f"unpin of page {page_id} of {disk.path}: page is not resident")
        if frame.pin_count == 0:
            raise BufferPoolError(f"unpin of page {page_id} of {disk.path}: pin count is already 0")
        frame.dirty = frame.dirty or is_dirty
        frame.pin_count -= 1

    def new_page(self, disk):
        """Allocate a page in `disk`; returns (page_id, frame), zero filled, pinned, dirty.
        Not a hit or a miss: nothing was fetched."""
        frame, evicted = self._grab_frame()
        if evicted:
            self.evictions += 1
        pid = disk.allocate_page()
        frame.data[:] = bytes(PAGE_SIZE)
        frame.disk, frame.page_id = disk, pid
        frame.pin_count, frame.dirty = 1, True
        self.page_table[(disk.file_id, pid)] = frame
        self._access(frame, loaded=True)
        self._note_peak()
        return pid, frame

    def flush_all(self):
        for f in self.frames:
            if f.occupied and f.dirty:
                f.disk.write_page(f.page_id, bytes(f.data))
                self.writes += 1
                f.dirty = False

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "reads": self.reads, "writes": self.writes}

    def reset_stats(self):
        self.hits = self.misses = self.evictions = self.reads = self.writes = 0

    def pinned_frames(self):
        return [f for f in self.frames if f.pin_count > 0]

    # -- Module 2 extensions ----------------------------------------------------
    def reserve(self, k=1):
        """Take k frames out of circulation for operator working memory. Returns the
        frames (their .data is usable scratch space). Fails rather than overcommit."""
        got = []
        try:
            for _ in range(k):
                frame, evicted = self._grab_frame()
                if evicted:
                    self.evictions += 1
                frame.reserved = True
                got.append(frame)
        except BufferPoolError:
            self.release(got)
            raise BufferPoolError(f"memory budget exceeded: asked for {k} working frames, "
                                  f"only {len(got)} free out of B = {self.B}")
        self.reserved_count += k
        self._note_peak()
        return got

    def release(self, frames):
        for f in frames:
            if f.reserved:
                f.reserved = False
                self.reserved_count -= 1

    def available(self):
        """Frames that could still be pinned or reserved right now."""
        return sum(1 for f in self.frames if f.evictable())

    def write_through(self, disk, page_id, data):
        """Write a page straight to disk from operator memory (a temp-file run page).
        Counts one write. Keeps any resident copy consistent."""
        disk.write_page(page_id, bytes(data))
        self.writes += 1
        f = self.page_table.get((disk.file_id, page_id))
        if f is not None:
            f.data[:] = data
            f.dirty = False

    def drop_file(self, disk):
        """Forget every frame of a file that is about to be deleted, without writing.
        Fails if any of its pages is still pinned."""
        for f in self.frames:
            if f.disk is disk:
                if f.pin_count:
                    raise BufferPoolError(f"page {f.page_id} of {disk.path} is still pinned")
                del self.page_table[(disk.file_id, f.page_id)]
                self.policy.on_remove(f)
                f.disk = f.page_id = None
                f.dirty = False

    def flush_file(self, disk):
        for f in self.frames:
            if f.disk is disk and f.dirty:
                disk.write_page(f.page_id, bytes(f.data))
                self.writes += 1
                f.dirty = False
