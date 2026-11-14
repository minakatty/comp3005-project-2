"""Working memory, temporary files and the external sorter (M2 spec 1.2 and 2.5).

The memory rule is enforced by construction. The plan runs over one buffer pool
of exactly B frames. Pages an operator reads are pinned in that pool. Tuples an
operator keeps (a sort run, a hash table, a nested-loop outer block, a partition
buffer) live in frames it has *reserved* from the same pool. A reserved frame is
never a victim, so pinned pages plus reserved frames can never exceed B, and the
pool's peak_held counter is the largest number of frames the plan held at once.

How many tuples fit in k reserved frames is decided by packing them onto pages
exactly as a slotted page would store them: each tuple costs its encoded length
plus a 4-byte slot, and a page holds 4088 bytes of that. So "k frames of tuples"
means the same thing in memory as it does on disk.
"""
import functools

from storage import record
from storage.page import PAGE_SIZE, SLOT, USABLE, SlottedPage

from .errors import ExecError


# --------------------------------------------------------------------------
# Hashing: our own, so that partitioning is identical in every run
# (Python's built-in str hash is salted per process).
# --------------------------------------------------------------------------

def fnv(values, seed=0):
    h = (0xCBF29CE484222325 ^ (seed * 0x9E3779B97F4A7C15)) & 0xFFFFFFFFFFFFFFFF
    for v in values:
        b = str(v).encode("utf-8") if not isinstance(v, int) else v.to_bytes(8, "little", signed=True)
        for byte in b + b"\x1f":
            h ^= byte
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


# --------------------------------------------------------------------------
# Page packing
# --------------------------------------------------------------------------

class PagePacker:
    """Counts how many pages a sequence of records occupies when packed in order."""

    def __init__(self):
        self.pages = 0
        self.used = USABLE          # bytes used on the current page; starts "full"

    def fits_on_current(self, ln):
        return self.used + ln + SLOT <= USABLE

    def add(self, ln):
        if not self.fits_on_current(ln):
            self.pages += 1
            self.used = 0
        self.used += ln + SLOT

    def pages_if_added(self, ln):
        return self.pages + (0 if self.fits_on_current(ln) else 1)


class WorkArea:
    """k reserved frames' worth of tuples."""

    def __init__(self, ctx, cols, frames, what):
        self.ctx, self.cols, self.k, self.what = ctx, cols, frames, what
        self.frames = ctx.reserve(frames, what) if frames > 0 else []
        self.rows = []
        self.packer = PagePacker()

    def try_add(self, row, ln=None):
        if self.k == 0:                     # no frames: hold exactly one tuple
            if self.rows:
                return False
            self.rows.append(row)
            return True
        ln = record.encoded_length(self.cols, row) if ln is None else ln
        if self.packer.pages_if_added(ln) > self.k:
            return False
        self.packer.add(ln)
        self.rows.append(row)
        return True

    def clear(self):
        self.rows = []
        self.packer = PagePacker()

    @property
    def pages(self):
        return self.packer.pages

    def shrink(self):
        """Give back the reserved frames the stored tuples do not occupy."""
        keep = max(1, self.packer.pages) if self.k else 0
        if self.frames and len(self.frames) > keep:
            self.ctx.release(self.frames[keep:])
            self.frames = self.frames[:keep]
            self.k = keep

    def release(self):
        if self.frames is not None:
            self.ctx.release(self.frames)
            self.frames = None


class ListCursor:
    """The TempReader interface (next / position / seek / close) over an in-memory
    sorted run, so sort-merge join can rewind either kind of input the same way."""

    def __init__(self, rows):
        self.rows, self.pos = rows, 0

    def position(self):
        return self.pos

    def seek(self, p):
        self.pos = p

    def next(self):
        if self.pos >= len(self.rows):
            return None
        self.pos += 1
        return self.rows[self.pos - 1]

    def close(self):
        pass


# --------------------------------------------------------------------------
# Temporary files of slotted pages
# --------------------------------------------------------------------------

class TempFile:
    """An append-only file of slotted pages, written one full page at a time from
    an output buffer and read back through the buffer pool. Deleted by drop()."""

    def __init__(self, ctx, cols):
        self.ctx, self.cols = ctx, cols
        self.disk = ctx.db.new_temp()
        self.count = 0
        self.dropped = False

    @property
    def pages(self):
        return self.disk.num_pages()

    def writer(self, buf=None):
        return TempWriter(self, buf)

    def reader(self, start=(0, 0)):
        return TempReader(self, start)

    def drop(self):
        if not self.dropped:
            self.dropped = True
            self.ctx.db.drop_temp(self.disk)


class TempWriter:
    """Packs records into a page buffer (one reserved frame's bytes) and writes the
    page when the next record would not fit. close() writes the last partial page."""

    def __init__(self, tf, buf=None):
        self.tf = tf
        self.buf = buf if buf is not None else bytearray(PAGE_SIZE)
        self.page = None

    def _start(self):
        pid = self.tf.disk.allocate_page()
        self.page = SlottedPage.init(self.buf, pid)

    def write(self, row):
        rec = record.encode(self.tf.cols, row)
        if self.page is None:
            self._start()
        if self.page.insert(rec) is None:
            self._flush()
            self._start()
            if self.page.insert(rec) is None:
                raise ExecError(f"a tuple of {len(rec)} bytes does not fit on a page")
        self.tf.count += 1

    def _flush(self):
        self.tf.ctx.db.pool.write_through(self.tf.disk, self.page.page_id, self.buf)
        self.page = None

    def close(self):
        if self.page is not None and self.page.slot_count:
            self._flush()
        self.page = None


class TempReader:
    """Streams a temp file's tuples with one page pinned at a time. Supports a mark
    (page, slot position) so sort-merge join can rewind its inner input."""

    def __init__(self, tf, start=(0, 0)):
        self.tf = tf
        self.pool = tf.ctx.db.pool
        self.page_no, self.pos = start
        self.frame = None
        self.slots = None

    def position(self):
        return (self.page_no, self.pos)

    def seek(self, position):
        if self.frame is not None and position[0] != self.page_no:
            self._unpin()
        self.page_no, self.pos = position

    def _unpin(self):
        if self.frame is not None:
            self.pool.unpin_page(self.tf.disk, self.page_no, False)
            self.frame = None
            self.slots = None

    def next(self):
        while True:
            if self.page_no >= self.tf.pages:
                self._unpin()
                return None
            if self.frame is None:
                self.frame = self.pool.fetch_page(self.tf.disk, self.page_no)
                self.slots = SlottedPage(self.frame.data).live()
            if self.pos < len(self.slots):
                off = self.slots[self.pos][1]
                self.pos += 1
                return record.decode(self.tf.cols, self.frame.data, off)
            self._unpin()
            self.page_no += 1
            self.pos = 0

    def __iter__(self):
        while True:
            r = self.next()
            if r is None:
                return
            yield r

    def close(self):
        self._unpin()


# --------------------------------------------------------------------------
# The external merge sort
# --------------------------------------------------------------------------

def make_comparator(cols, keys, counter):
    """keys: [(column index, "asc"|"desc")]. Returns cmp(a, b) that counts one
    comparison on `counter` per call (counter is an object with .comparisons)."""
    def cmp(a, b):
        counter.comparisons += 1
        for i, d in keys:
            x, y = a[i], b[i]
            if x != y:
                r = -1 if x < y else 1
                return -r if d == "desc" else r
        return 0
    return cmp


class ExternalSorter:
    """External merge sort with W working frames (M2 spec 2.5).

    pass 0      fill W frames with tuples, sort them in memory (the language sort is
                allowed on a single run), write the run to a temp file.
    pass k      merge F = W - 1 runs at a time into a new run, one frame for output.
                A group left with a single run is carried forward untouched.
    final pass  when at most W runs remain (no output frame is needed), they are
                merged straight to the caller without being written.

    If the whole input fits in W frames, nothing is written and the sorted run is
    returned from memory (one pass, zero I/O).

    `owner` is the operator whose counters receive comparisons; reads and writes are
    attributed to whatever operator is running, which is also the owner."""

    def __init__(self, ctx, cols, keys, frames, owner):
        self.ctx, self.cols, self.keys, self.W, self.owner = ctx, cols, keys, max(1, frames), owner
        self.cmp = make_comparator(cols, keys, owner)
        self.area = WorkArea(ctx, cols, self.W, "sort run buffer")
        self.runs = []                  # TempFiles
        self.passes = 0
        self.input_pages = 0
        self.mem_run = None
        self.run_log = []

    def add(self, row):
        ln = record.encoded_length(self.cols, row)
        if not self.area.try_add(row, ln):
            self._spill()
            if not self.area.try_add(row, ln):
                raise ExecError("a tuple is larger than the sort's whole memory")

    def _spill(self):
        rows = sorted(self.area.rows, key=functools.cmp_to_key(self.cmp))
        tf = TempFile(self.ctx, self.cols)
        w = tf.writer(self.area.frames[0].data if self.area.frames else None)
        for r in rows:
            w.write(r)
        w.close()
        self.input_pages += tf.pages
        self.runs.append(tf)
        self.area.clear()

    def finish(self):
        """End of input. Runs pass 0's last run and every merge pass except the last."""
        self.passes = 1
        if not self.runs:
            self.mem_run = sorted(self.area.rows, key=functools.cmp_to_key(self.cmp))
            self.input_pages = self.area.pages
            return
        if self.area.rows:
            self._spill()
        self.area.release()                 # pass 0 memory is no longer needed
        self.run_log.append(len(self.runs))
        F = max(2, self.W - 1)
        while len(self.runs) > max(2, self.W):
            self.runs = self._merge_pass(self.runs, F)
            self.passes += 1
            self.run_log.append(len(self.runs))
        self.passes += 1                    # the final, streaming pass

    def _merge_pass(self, runs, F):
        out = []
        out_frame = self.ctx.reserve(1, "sort merge output")
        try:
            for g in range(0, len(runs), F):
                group = runs[g:g + F]
                if len(group) == 1:
                    out.append(group[0])    # carried forward, not rewritten
                    continue
                tf = TempFile(self.ctx, self.cols)
                w = tf.writer(out_frame[0].data)
                for r in self._merge(group):
                    w.write(r)
                w.close()
                for old in group:
                    old.drop()
                out.append(tf)
        finally:
            self.ctx.release(out_frame)
        return out

    def _merge(self, runs):
        readers = [r.reader() for r in runs]
        try:
            heads = [rd.next() for rd in readers]
            while True:
                best = -1
                for i, h in enumerate(heads):
                    if h is None:
                        continue
                    if best < 0 or self.cmp(h, heads[best]) < 0:
                        best = i
                if best < 0:
                    return
                yield heads[best]
                heads[best] = readers[best].next()
        finally:
            for rd in readers:
                rd.close()

    def __iter__(self):
        """Stream the sorted output (the final pass)."""
        if self.mem_run is not None:
            yield from self.mem_run
            return
        if len(self.runs) == 1:
            rd = self.runs[0].reader()
            try:
                yield from rd
            finally:
                rd.close()
            return
        yield from self._merge(self.runs)

    def materialize(self):
        """Sort to exactly one run file (sort-merge join and the set operators read
        their inputs back from disk). Returns the TempFile."""
        if self.mem_run is not None:
            tf = TempFile(self.ctx, self.cols)
            w = tf.writer(self.area.frames[0].data)
            for r in self.mem_run:
                w.write(r)
            w.close()
            self.area.release()
            self.mem_run = None
            self.runs = [tf]
            return tf
        F = max(2, self.W - 1)
        while len(self.runs) > 1:
            self.runs = self._merge_pass(self.runs, F)
            self.run_log.append(len(self.runs))
        return self.runs[0]

    def close(self):
        self.area.release()
        for r in self.runs:
            r.drop()
        self.runs = []
        self.mem_run = None
