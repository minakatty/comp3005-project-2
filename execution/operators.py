"""The iterator operators (M2 spec Sections 1 and 2).

Every operator has open(), next() -> tuple | None (None is END), close() and
counters(). An operator reaches its input only through its children's next(),
and the disk only through Module 1 (heap scans, temp files, the buffer pool).

Counter attribution. ExecContext keeps a stack; every open/next/close of an
operator pushes that operator as the I/O "sink" of the shared IOStats, so each
disk read or write lands on the operator that caused it. A child's reads are the
child's, never the parent's. comparisons are incremented by the operator itself.
"""
from storage import record
from storage.errors import BufferPoolError, CatalogError

from ralang.condition import Condition, column_index, equi_keys, type_class

from .errors import ExecError, PlanError
from .memory import ExternalSorter, ListCursor, PagePacker, TempFile, WorkArea, fnv


class ExecContext:
    def __init__(self, db, B):
        self.db = db
        self.B = B
        self._stack = []

    def enter(self, op):
        self._stack.append(self.db.io.sink)
        self.db.io.sink = op

    def exit(self):
        self.db.io.sink = self._stack.pop()

    def reserve(self, k, what):
        try:
            return self.db.pool.reserve(k)
        except BufferPoolError as e:
            raise ExecError(f"{what}: {e}")

    def release(self, frames):
        self.db.pool.release(frames)


def check_unique(cols, node):
    seen = set()
    for c in cols:
        if c[0] in seen:
            raise PlanError(f"Node '{node}': two columns named '{c[0]}' in one schema")
        seen.add(c[0])


class Operator:
    name = "operator"
    memory = False              # True for operators that get a share of B

    def __init__(self, ctx, children=()):
        self.ctx = ctx
        self.children = list(children)
        self.tuples_out = self.comparisons = self.page_reads = self.page_writes = 0
        self.cols = []
        self.share = 0
        self.info = {}
        self._open_flag = False
        self.opens = 0              # > 1 for a nested-loop inner that was rescanned

    # -- public interface --------------------------------------------------
    def open(self):
        self.opens += 1
        self.ctx.enter(self)
        try:
            self._open()
        finally:
            self.ctx.exit()
        self._open_flag = True

    def next(self):
        self.ctx.enter(self)
        try:
            row = self._next()
        finally:
            self.ctx.exit()
        if row is not None:
            self.tuples_out += 1
        return row

    def close(self):
        if self._open_flag:
            self._open_flag = False
            self.ctx.enter(self)
            try:
                self._close()
            finally:
                self.ctx.exit()
        for c in self.children:
            c.close()

    def restart(self):
        """Close and reopen (a nested-loop inner rescan). Counters accumulate."""
        self.close()
        self.open()

    def counters(self):
        return {"tuples_out": self.tuples_out, "comparisons": self.comparisons,
                "page_reads": self.page_reads, "page_writes": self.page_writes}

    def label(self):
        return self.name

    # -- defaults ------------------------------------------------------------
    def _open(self):
        for c in self.children:
            c.open()

    def _close(self):
        pass

    def __iter__(self):
        while True:
            r = self.next()
            if r is None:
                return
            yield r


# ==========================================================================
# Pipelined operators
# ==========================================================================

class SeqScan(Operator):
    name = "seqscan"

    def __init__(self, ctx, table, alias=None):
        super().__init__(ctx)
        try:
            info = ctx.db.catalog.get_table(table)
        except CatalogError:
            raise PlanError(f"Node 'seqscan': unknown table '{table}'")
        self.table, self.alias = table, alias
        q = alias or table
        self.cols = [(f"{q}.{c}", t, n) for c, t, n in info["schema"]]
        self.gen = None

    def label(self):
        return f"seqscan {self.table}" + (f" as {self.alias}" if self.alias else "")

    def _open(self):
        self.gen = self.ctx.db.heap(self.table).scan()

    def _next(self):
        try:
            return next(self.gen)[1]
        except StopIteration:
            return None

    def _close(self):
        if self.gen is not None:
            self.gen.close()
            self.gen = None


class Filter(Operator):
    name = "filter"

    def __init__(self, ctx, child, condition):
        super().__init__(ctx, [child])
        self.cols = child.cols
        self.cond = _condition(condition, child.cols, "filter")

    def label(self):
        return f"filter ({self.cond.text})"

    def _next(self):
        child = self.children[0]
        while True:
            row = child.next()
            if row is None:
                return None
            self.comparisons += 1           # one per tuple examined
            if self.cond(row):
                return row


class Project(Operator):
    name = "project"

    def __init__(self, ctx, child, columns, names=None):
        super().__init__(ctx, [child])
        self.idx = [_col(child.cols, c, "project") for c in columns]
        names = names or [None] * len(columns)
        self.cols = [((nm or child.cols[i][0]),) + tuple(child.cols[i][1:]) for i, nm in zip(self.idx, names)]
        check_unique(self.cols, "project")

    def label(self):
        return "project [" + ", ".join(c[0] for c in self.cols) + "]"

    def _next(self):
        row = self.children[0].next()
        return None if row is None else tuple(row[i] for i in self.idx)


class Rename(Operator):
    """Extension node: re-qualifies every column (a subquery's alias)."""
    name = "rename"

    def __init__(self, ctx, child, to):
        super().__init__(ctx, [child])
        self.to = to
        self.cols = [(f"{to}.{c[0].rsplit('.', 1)[-1]}",) + tuple(c[1:]) for c in child.cols]
        check_unique(self.cols, "rename")

    def label(self):
        return f"rename to {self.to}"

    def _next(self):
        return self.children[0].next()


class Limit(Operator):
    name = "limit"

    def __init__(self, ctx, child, n):
        super().__init__(ctx, [child])
        self.n = n
        self.cols = child.cols
        self.seen = 0

    def label(self):
        return f"limit {self.n}"

    def _open(self):
        self.seen = 0
        super()._open()

    def _next(self):
        if self.seen >= self.n:
            return None                     # stop pulling: the child is not asked again
        row = self.children[0].next()
        if row is not None:
            self.seen += 1
        return row


class UnionAll(Operator):
    name = "union_all"

    def __init__(self, ctx, left, right):
        super().__init__(ctx, [left, right])
        self.cols = _set_compatible(left, right, "union_all")
        self.side = 0

    def _open(self):
        self.side = 0
        super()._open()

    def _next(self):
        while self.side < 2:
            row = self.children[self.side].next()
            if row is not None:
                return row
            self.side += 1
        return None


# ==========================================================================
# Sort, distinct, group_by
# ==========================================================================

class Sort(Operator):
    name = "sort"
    memory = True

    def __init__(self, ctx, child, keys):
        super().__init__(ctx, [child])
        self.cols = child.cols
        self.keys = [(_col(child.cols, c, "sort"), d) for c, d in keys]
        self.key_text = keys
        self.sorter = self.it = None

    def label(self):
        return "sort [" + ", ".join(f"{c} {d}" for c, d in self.key_text) + "]"

    def _open(self):
        child = self.children[0]
        child.open()
        self.sorter = ExternalSorter(self.ctx, self.cols, self.keys, self.share, self,
                                     input_frame=isinstance(child, SeqScan))
        for row in child:
            self.sorter.add(row)
        child.close()                       # its frames are free for the merge
        self.sorter.finish()
        self.info = {"frames": self.share, "input_pages": self.sorter.input_pages,
                     "runs_after_each_pass": self.sorter.run_log, "passes": self.sorter.passes}
        self.it = iter(self.sorter)

    def _next(self):
        return next(self.it, None)

    def _close(self):
        if self.it is not None:
            self.it.close()
        if self.sorter is not None:
            self.sorter.close()
        self.it = self.sorter = None


class HybridHash:
    """Hash table for distinct and group_by that partitions to disk when it is full.

    The table gets T frames. A tuple whose group is already in the table is folded
    into it. A tuple of a new group goes into the table while it has room; once the
    table is full, new groups are written to P partition files by hash instead.
    When input ends the table is emitted, then each partition is processed the same
    way (with a different hash seed). Every round finishes at least one table's
    worth of groups, so it always terminates."""

    def __init__(self, owner, in_cols, key_idx, agg, W):
        self.owner, self.ctx = owner, owner.ctx
        self.in_cols, self.key_idx, self.agg = in_cols, key_idx, agg
        self.P = max(1, W // 3) if W >= 3 else 1
        self.T = max(1, W - self.P)
        self.area = owner.ctx.reserve(self.T, f"{owner.name} hash table")
        self.rounds = 0
        self.spilled_tuples = 0

    def run(self, source, depth=0):
        self.rounds += 1
        table, packer, parts = {}, PagePacker(), None
        for row in source:
            k = tuple(row[i] for i in self.key_idx)
            self.owner.comparisons += 1            # one hash-table probe per tuple
            if k in table:
                self.agg.update(table[k], row)
                continue
            ln = self.agg.state_len(k)
            if parts is None and packer.pages_if_added(ln) <= self.T:
                packer.add(ln)
                table[k] = self.agg.init(row)
                continue
            if parts is None:
                frames = self.ctx.reserve(self.P, f"{self.owner.name} partition buffers")
                parts = [(TempFile(self.ctx, self.in_cols), f) for f in frames]
                writers = [tf.writer(f.data) for tf, f in parts]
            p = fnv(k, depth + 1) % self.P
            writers[p].write(row)
            self.spilled_tuples += 1
        for k, st in table.items():
            yield self.agg.final(k, st)
        table.clear()
        if parts is None:
            return
        for w in writers:
            w.close()
        self.ctx.release([f for _, f in parts])
        for tf, _ in parts:
            if tf.count:
                rd = tf.reader()
                try:
                    yield from self.run(rd, depth + 1)
                finally:
                    rd.close()
            tf.drop()

    def close(self):
        if self.area is not None:
            self.ctx.release(self.area)
            self.area = None


class _DistinctAgg:
    def state_len(self, k):
        return self.ln(k)

    def init(self, row):
        return None

    def update(self, st, row):
        pass

    def final(self, k, st):
        return k


class Distinct(Operator):
    name = "distinct"
    memory = True

    def __init__(self, ctx, child, mode="sort"):
        super().__init__(ctx, [child])
        if mode not in ("sort", "hash"):
            raise PlanError(f"Node 'distinct': field 'mode' must be \"sort\" or \"hash\", got {mode!r}")
        self.mode = mode
        self.cols = child.cols
        self.gen = None

    def label(self):
        return f"distinct ({self.mode})"

    def _open(self):
        child = self.children[0]
        child.open()
        all_cols = list(range(len(self.cols)))
        if self.mode == "sort":
            self.sorter = ExternalSorter(self.ctx, self.cols, [(i, "asc") for i in all_cols], self.share, self,
                                         input_frame=isinstance(child, SeqScan))
            for row in child:
                self.sorter.add(row)
            child.close()
            self.sorter.finish()
            self.info = {"frames": self.share, "passes": self.sorter.passes,
                         "input_pages": self.sorter.input_pages}
            self.gen = self._sorted_unique()
        else:
            agg = _DistinctAgg()
            agg.ln = lambda k: record.encoded_length(self.cols, k)
            self.hh = HybridHash(self, self.cols, all_cols, agg, self.share)
            self.gen = self._hashed(child)

    def _sorted_unique(self):
        prev = None
        for row in self.sorter:
            if prev is not None:
                self.comparisons += 1
                if row == prev:
                    continue
            prev = row
            yield row

    def _hashed(self, child):
        # The first round consumes the child as it goes; the child is closed when
        # it is exhausted so later rounds can use its frame.
        def src():
            for r in child:
                yield r
            child.close()
        yield from self.hh.run(src())
        self.info = {"frames": self.share, "table_frames": self.hh.T, "partitions": self.hh.P,
                     "rounds": self.hh.rounds, "spilled_tuples": self.hh.spilled_tuples}

    def _next(self):
        return next(self.gen, None)

    def _close(self):
        if self.gen is not None:
            self.gen.close()
        if getattr(self, "sorter", None) is not None:
            self.sorter.close()
        if getattr(self, "hh", None) is not None:
            self.hh.close()


AGG_FUNCS = ("count", "sum", "avg", "min", "max")


class _GroupAgg:
    """Aggregate state: one slot per aggregate. count -> n, sum -> s, avg -> [s, n],
    min/max -> value."""

    def __init__(self, specs, key_cols):
        self.specs = specs                  # [(fn, column index or None)]
        self.key_cols = key_cols

    def state_len(self, k):
        return record.encoded_length(self.key_cols, k) + 8 * len(self.specs)

    def init(self, row):
        st = []
        for fn, i in self.specs:
            if fn == "count":
                st.append(1)
            elif fn == "avg":
                st.append([row[i], 1])
            else:
                st.append(row[i])
        return st

    def update(self, st, row):
        for j, (fn, i) in enumerate(self.specs):
            if fn == "count":
                st[j] += 1
            elif fn == "sum":
                st[j] += row[i]
            elif fn == "avg":
                st[j][0] += row[i]
                st[j][1] += 1
            elif fn == "min":
                if row[i] < st[j]:
                    st[j] = row[i]
            elif fn == "max":
                if row[i] > st[j]:
                    st[j] = row[i]

    def final(self, k, st):
        out = list(k)
        for (fn, _), v in zip(self.specs, st):
            out.append(round(v[0] / v[1], 4) if fn == "avg" else v)
        return tuple(out)


class GroupBy(Operator):
    name = "group_by"
    memory = True

    def __init__(self, ctx, child, keys, aggregates, mode="hash"):
        super().__init__(ctx, [child])
        if mode not in ("sort", "hash"):
            raise PlanError(f"Node 'group_by': field 'mode' must be \"sort\" or \"hash\", got {mode!r}")
        self.mode = mode
        self.key_idx = [_col(child.cols, k, "group_by") for k in keys]
        key_cols = [child.cols[i] for i in self.key_idx]
        specs, out = [], list(key_cols)
        for a in aggregates:
            fn, col = a[0].lower(), a[1]
            if fn not in AGG_FUNCS:
                raise PlanError(f"Node 'group_by': unknown aggregate '{a[0]}'")
            if col == "*":
                if fn != "count":
                    raise PlanError(f"Node 'group_by': {fn}(*) is not allowed, only count(*)")
                i, ctype = None, None
            else:
                i = _col(child.cols, col, "group_by")
                ctype = child.cols[i]
                if fn in ("sum", "avg") and type_class(ctype[1]) != "num":
                    raise PlanError(f"Node 'group_by': {fn} needs an INT column, '{col}' is {ctype[1]}")
            name = a[2] if len(a) > 2 else (f"{fn}_star" if col == "*" else f"{fn}_{col.replace('.', '_')}")
            if fn == "count":
                out.append((name, "INT", 0))
            elif fn == "sum":
                out.append((name, "LONG", 0))
            elif fn == "avg":
                out.append((name, "FLOAT", 0))
            else:
                out.append((name,) + tuple(ctype[1:]))
            specs.append((fn, i))
        self.cols = out
        check_unique(self.cols, "group_by")
        self.agg = _GroupAgg(specs, key_cols)
        self.agg_text = aggregates
        self.keys_text = keys
        self.gen = None

    def label(self):
        aggs = ", ".join(f"{a[0]}({a[1]})" for a in self.agg_text)
        return f"group_by ({self.mode}) keys [{', '.join(self.keys_text)}] aggs [{aggs}]"

    def _open(self):
        child = self.children[0]
        child.open()
        if self.mode == "sort":
            self.sorter = ExternalSorter(self.ctx, child.cols, [(i, "asc") for i in self.key_idx],
                                         self.share, self, input_frame=isinstance(child, SeqScan))
            for row in child:
                self.sorter.add(row)
            child.close()
            self.sorter.finish()
            self.info = {"frames": self.share, "passes": self.sorter.passes,
                         "input_pages": self.sorter.input_pages}
            src = self._sorted_groups()
        else:
            self.hh = HybridHash(self, child.cols, self.key_idx, self.agg, self.share)

            def feed():
                for r in child:
                    yield r
                child.close()
            src = self.hh.run(feed())
        self.gen = self._empty_rule(src)

    def _sorted_groups(self):
        cur_k, st = None, None
        for row in self.sorter:
            k = tuple(row[i] for i in self.key_idx)
            if st is not None:
                self.comparisons += 1
                if k == cur_k:
                    self.agg.update(st, row)
                    continue
                yield self.agg.final(cur_k, st)
            cur_k, st = k, self.agg.init(row)
        if st is not None:
            yield self.agg.final(cur_k, st)

    def _empty_rule(self, src):
        """No grouping columns and an empty input: exactly one row, COUNT 0."""
        any_row = False
        for r in src:
            any_row = True
            yield r
        if not any_row and not self.key_idx:
            if any(fn != "count" for fn, _ in self.agg.specs):
                bad = [fn for fn, _ in self.agg.specs if fn != "count"]
                raise ExecError(f"{bad[0].upper()} over an empty input is undefined "
                                f"(no NULLs in this system)")
            yield tuple(0 for _ in self.agg.specs)

    def _next(self):
        return next(self.gen, None)

    def _close(self):
        if self.gen is not None:
            self.gen.close()
        if getattr(self, "sorter", None) is not None:
            self.sorter.close()
        if getattr(self, "hh", None) is not None:
            self.hh.close()
            if self.mode == "hash":
                self.info = {"frames": self.share, "table_frames": self.hh.T, "partitions": self.hh.P,
                             "rounds": self.hh.rounds, "spilled_tuples": self.hh.spilled_tuples}


# ==========================================================================
# Joins
# ==========================================================================

class _Join(Operator):
    memory = True

    def __init__(self, ctx, left, right, condition):
        super().__init__(ctx, [left, right])
        self.cols = left.cols + right.cols
        check_unique(self.cols, self.name)
        self.cond_text = condition
        self.gen = None

    def label(self):
        return f"{self.name} ({self.cond_text})" if self.cond_text else f"{self.name} (cross)"

    def _next(self):
        return next(self.gen, None)

    def _close(self):
        if self.gen is not None:
            self.gen.close()
            self.gen = None


class NestedLoopJoin(_Join):
    """Block nested loop: B-2 outer pages in memory, the inner streamed past each block."""
    name = "nested_loop_join"

    def __init__(self, ctx, left, right, condition=None):
        super().__init__(ctx, left, right, condition)
        self.cond = _condition(condition, self.cols, self.name) if condition else None

    def _open(self):
        self.children[0].open()
        self.area = WorkArea(self.ctx, self.children[0].cols, self.share, "nested loop outer block")
        self.blocks = 0
        self.gen = self._run()

    def _run(self):
        outer, inner = self.children
        pending, done, inner_open = None, False, False
        try:
            while not done:
                self.area.clear()
                if pending is not None:
                    self.area.try_add(pending)
                    pending = None
                while True:
                    r = outer.next()
                    if r is None:
                        done = True
                        break
                    if not self.area.try_add(r):
                        pending = r
                        break
                if not self.area.rows:
                    return
                self.blocks += 1
                if inner_open:
                    inner.restart()
                else:
                    inner.open()
                    inner_open = True
                block = self.area.rows
                cond = self.cond
                while True:
                    s = inner.next()
                    if s is None:
                        break
                    for r in block:
                        self.comparisons += 1
                        row = r + s
                        if cond is None or cond(row):
                            yield row
        finally:
            self.info = {"frames": self.share, "outer_blocks": self.blocks}

    def _close(self):
        super()._close()
        if getattr(self, "area", None) is not None:
            self.area.release()
            self.area = None


class _EquiJoin(_Join):
    def __init__(self, ctx, left, right, condition):
        super().__init__(ctx, left, right, condition)
        if not isinstance(condition, str):
            raise PlanError(f"Node '{self.name}': field 'condition' must be a string")
        try:
            keys = equi_keys(condition, left.cols, right.cols)
        except Exception as e:
            raise PlanError(f"Node '{self.name}': {getattr(e, 'message', e)}")
        if keys is None:
            # resolve the columns first so an unknown column is reported as such
            _condition(condition, self.cols, self.name)
            raise PlanError(f"Node '{self.name}': condition '{condition}' is not an equality "
                            f"between one column of each input; use nested_loop_join")
        self.lk, self.rk = keys


class SortMergeJoin(_EquiJoin):
    """Sort both inputs on the key with the external sort, then merge with two
    cursors.

    The left input is sorted first and its final merge pass is streamed straight
    into the join, like any sort. The right input must be rewindable (for each left
    tuple of a run of equal keys the right cursor goes back to the mark), so it is
    either kept as one in-memory run, when it fits in the frames the left run did
    not use, or merged into a single run file on disk. A run of equal keys larger
    than B therefore costs only re-reads of the right run file, never memory."""
    name = "sort_merge_join"

    def _open(self):
        left, right = self.children
        # As in hash join: the right scan's frame is idle while the left input is
        # sorted, and the left scan's while the right one is, so one more frame is free.
        spare = 1 if (input_pages_hint(self.ctx, left) is not None
                      and input_pages_hint(self.ctx, right) is not None) else 0
        left.open()
        self.ls = ExternalSorter(self.ctx, left.cols, [(i, "asc") for i in self.lk], self.share + spare, self,
                                 input_frame=isinstance(left, SeqScan))
        for row in left:
            self.ls.add(row)
        left.close()
        self.ls.finish()
        used = 0
        if self.ls.mem_run is not None:
            self.ls.area.shrink()
            used = self.ls.area.k
            right_pages = input_pages_hint(self.ctx, right)
            if self.share + spare - used < 3 or right_pages is None or right_pages > self.share + spare - used:
                # keeping the left run in memory would leave the right sort too few
                # frames (fewer than a merge needs, or fewer than the right input's
                # pages): write the left run out as one run file instead
                self.ls.materialize()
                used = 0
        right.open()
        self.rs = ExternalSorter(self.ctx, right.cols, [(i, "asc") for i in self.rk],
                                 max(1, self.share + spare - used), self, input_frame=isinstance(right, SeqScan))
        for row in right:
            self.rs.add(row)
        right.close()
        self.rs.finish()
        self.tr = None
        if self.rs.mem_run is not None and len(self.ls.runs) > self.ctx.db.pool.available():
            # the left input's final merge needs one frame per run, and keeping the
            # right run in memory would leave too few: write the right run out instead
            self.rs.materialize()
        if self.rs.mem_run is not None:
            cursor = ListCursor(self.rs.mem_run)
        else:
            self.tr = self.rs.materialize()
            self.rs.runs = []
            cursor = self.tr.reader()
        self.info = {"frames": self.share, "left_input_pages": self.ls.input_pages,
                     "left_sort_passes": self.ls.passes, "right_input_pages": self.rs.input_pages,
                     "right_sort_passes": self.rs.passes,
                     "right_run": "memory" if self.tr is None else f"disk ({self.tr.pages} pages)"}
        self.gen = self._merge(iter(self.ls), cursor)

    def _cmp(self, r, s):
        self.comparisons += 1
        for i, j in zip(self.lk, self.rk):
            if r[i] != s[j]:
                return -1 if r[i] < s[j] else 1
        return 0

    def _merge(self, left_iter, rr):
        rl = _IterCursor(left_iter)
        try:
            r = rl.next()
            s_pos = rr.position()           # position *of* s, the mark if s starts a run
            s = rr.next()
            while r is not None and s is not None:
                c = self._cmp(r, s)
                if c < 0:
                    r = rl.next()
                elif c > 0:
                    s_pos = rr.position()
                    s = rr.next()
                else:
                    key = tuple(r[i] for i in self.lk)
                    mark = s_pos
                    while r is not None:
                        self.comparisons += 1
                        if tuple(r[i] for i in self.lk) != key:
                            break
                        rr.seek(mark)       # rewind the inner cursor for every r in the run
                        p2 = rr.position()
                        s2 = rr.next()
                        while s2 is not None:
                            self.comparisons += 1
                            if tuple(s2[j] for j in self.rk) != key:
                                break
                            yield r + s2
                            p2 = rr.position()
                            s2 = rr.next()
                        r = rl.next()
                    s, s_pos = s2, p2       # s is now the first right tuple past the run
        finally:
            rl.close()
            rr.close()

    def _close(self):
        super()._close()
        for srt in (getattr(self, "ls", None), getattr(self, "rs", None)):
            if srt is not None:
                srt.close()
        if getattr(self, "tr", None) is not None:
            self.tr.drop()
        self.ls = self.rs = self.tr = None


class _IterCursor:
    def __init__(self, it):
        self.it = it

    def next(self):
        return next(self.it, None)

    def close(self):
        close = getattr(self.it, "close", None)
        if close:
            close()


class HashJoin(_EquiJoin):
    """Grace hash join. Left is the build side.
    If the whole build input fits in the working frames, the table is built in memory
    and the probe input streamed past it: no partitioning, cost M + N.
    Otherwise: partition both inputs to disk by h1(key) mod P, one output buffer per
    partition; then for each partition pair build an in-memory table on L_i (h2) and
    stream R_i past it. A partition L_i still larger than the table's frames
    overflows; that pair falls back to block nested loop (DESIGN_M2.md 5)."""
    name = "hash_join"

    def _spare_frame(self):
        """1 if both inputs are base-table pipelines. The allocator charged each of
        their scans one frame for the whole plan, but the right scan is not open while
        the left input is partitioned, and both are closed during the probe, so one
        more frame is genuinely free in those phases."""
        l, r = self.children
        return 1 if input_pages_hint(self.ctx, l) is not None and input_pages_hint(self.ctx, r) is not None else 0

    def _open(self):
        left, right = self.children
        left.open()
        # Try to build the whole table in memory first. If the build side fits in
        # the working frames, no partitioning is needed at all.
        area = WorkArea(self.ctx, left.cols, max(1, self.share), "hash join build table")
        extra = None
        for row in left:
            if not area.try_add(row):
                extra = row
                break
        if extra is None:
            left.close()
            self.area = area
            self.lp, self.rp = [], []
            self.info = {"frames": self.share, "partitions": 0, "mode": "in-memory (build side fit)"}
            self.gen = self._probe_memory()
            return
        buffered = area.rows
        area.release()                     # its tuples are flushed into the partition buffers
        P = max(1, self.share + self._spare_frame())        # B - 1 for a join of two scans
        frames = self.ctx.reserve(P, "hash join partition buffers")
        try:
            self.lp = self._partition(left, self.lk, frames, buffered + [extra])
            self.rp = self._partition(right, self.rk, frames, [])
        finally:
            self.ctx.release(frames)
        self.info = {"frames": self.share, "partitions": P, "mode": "grace",
                     "left_partition_pages": [t.pages for t in self.lp],
                     "right_partition_pages": [t.pages for t in self.rp],
                     "overflowed_partitions": []}
        self.gen = self._probe()

    def _partition(self, child, keys, frames, first):
        if not first:
            child.open()
        parts = [TempFile(self.ctx, child.cols) for _ in frames]
        writers = [tf.writer(f.data) for tf, f in zip(parts, frames)]
        P = len(parts)
        for row in first:
            writers[fnv(tuple(row[i] for i in keys), 1) % P].write(row)
        for row in child:
            writers[fnv(tuple(row[i] for i in keys), 1) % P].write(row)
        for w in writers:
            w.close()
        child.close()
        return parts

    def _buckets(self, rows):
        nb = max(1, len(rows))
        buckets = [[] for _ in range(nb)]
        for r in rows:
            buckets[fnv(tuple(r[k] for k in self.lk), 2) % nb].append(r)
        return buckets, nb

    def _probe_memory(self):
        right = self.children[1]
        buckets, nb = self._buckets(self.area.rows)
        right.open()
        for s in right:
            sk = tuple(s[k] for k in self.rk)
            for r in buckets[fnv(sk, 2) % nb]:
                self.comparisons += 1
                if tuple(r[k] for k in self.lk) == sk:
                    yield r + s
        right.close()

    def _probe(self):
        left, right = self.children
        # both inputs are closed now: B - 1 frames for the table, 1 to read partitions
        area = WorkArea(self.ctx, left.cols, max(1, self.share + self._spare_frame()), "hash join build table")
        try:
            for i, (lt, rt) in enumerate(zip(self.lp, self.rp)):
                if lt.count == 0 or rt.count == 0:
                    continue
                area.clear()
                lr = lt.reader()
                overflow = False
                for row in lr:
                    if not area.try_add(row):
                        overflow = True
                        break
                lr.close()
                if overflow:
                    self.info["overflowed_partitions"].append(i)
                    # the fallback reads L_i and R_i at the same time, so it needs one
                    # more reader frame: give one table frame back while it runs
                    if len(area.frames) > 1:
                        self.ctx.release([area.frames.pop()])
                        area.k -= 1
                        yield from self._bnl_partition(lt, rt, area)
                        area.frames += self.ctx.reserve(1, "hash join build table")
                        area.k += 1
                    else:
                        yield from self._bnl_partition(lt, rt, area)
                    continue
                buckets, nb = self._buckets(area.rows)
                rd = rt.reader()
                try:
                    for s in rd:
                        sk = tuple(s[k] for k in self.rk)
                        for r in buckets[fnv(sk, 2) % nb]:
                            self.comparisons += 1
                            if tuple(r[k] for k in self.lk) == sk:
                                yield r + s
                finally:
                    rd.close()
        finally:
            area.release()

    def _bnl_partition(self, lt, rt, area):
        """Overflow fallback: block nested loop over one partition pair, the block
        being the build table's own frames."""
        lr = lt.reader()
        try:
            pending = None
            while True:
                area.clear()
                if pending is not None:
                    area.try_add(pending)
                    pending = None
                for row in lr:
                    if not area.try_add(row):
                        pending = row
                        break
                if not area.rows:
                    return
                rd = rt.reader()
                try:
                    for s in rd:
                        sk = tuple(s[k] for k in self.rk)
                        for r in area.rows:
                            self.comparisons += 1
                            if tuple(r[k] for k in self.lk) == sk:
                                yield r + s
                finally:
                    rd.close()
                if pending is None:
                    return
        finally:
            lr.close()

    def _close(self):
        super()._close()
        for tf in getattr(self, "lp", []) + getattr(self, "rp", []):
            tf.drop()
        self.lp = self.rp = []
        if getattr(self, "area", None) is not None:
            self.area.release()
            self.area = None


# ==========================================================================
# Set operators
# ==========================================================================

class _SortedSetOp(Operator):
    """union (set), intersect, except and their bag forms, by sorting both inputs on
    every column and merging the two sorted streams value group by value group."""
    memory = True

    def __init__(self, ctx, left, right):
        super().__init__(ctx, [left, right])
        self.cols = _set_compatible(left, right, self.name)
        self.gen = None

    def _sorted(self, child):
        child.open()
        s = ExternalSorter(self.ctx, child.cols, [(i, "asc") for i in range(len(child.cols))],
                           self.share, self, input_frame=isinstance(child, SeqScan))
        for row in child:
            s.add(row)
        child.close()
        s.finish()
        tf = s.materialize()
        s.runs = []
        s.close()
        return tf

    def _open(self):
        self.tl = self._sorted(self.children[0])
        self.tr = self._sorted(self.children[1])
        self.gen = self._merge()

    def _groups(self, rd):
        """(value, count) for each run of equal tuples in a sorted reader."""
        cur, n = None, 0
        for row in rd:
            if n:
                self.comparisons += 1
                if row == cur:
                    n += 1
                    continue
                yield cur, n
            cur, n = row, 1
        if n:
            yield cur, n

    def _merge(self):
        rl, rr = self.tl.reader(), self.tr.reader()
        gl, gr = self._groups(rl), self._groups(rr)
        try:
            a, b = next(gl, None), next(gr, None)
            while a is not None or b is not None:
                if b is None or (a is not None and self._lt(a[0], b[0])):
                    k, cl, cr = a[0], a[1], 0
                    a = next(gl, None)
                elif a is None or self._lt(b[0], a[0]):
                    k, cl, cr = b[0], 0, b[1]
                    b = next(gr, None)
                else:
                    k, cl, cr = a[0], a[1], b[1]
                    a, b = next(gl, None), next(gr, None)
                for _ in range(self.emit(cl, cr)):
                    yield k
        finally:
            gl.close()
            gr.close()
            rl.close()
            rr.close()

    def _lt(self, x, y):
        self.comparisons += 1
        return x < y

    def _next(self):
        return next(self.gen, None)

    def _close(self):
        if self.gen is not None:
            self.gen.close()
        for tf in (getattr(self, "tl", None), getattr(self, "tr", None)):
            if tf is not None:
                tf.drop()
        self.tl = self.tr = None


class Union(_SortedSetOp):
    name = "union"
    emit = staticmethod(lambda cl, cr: 1)


class Intersect(_SortedSetOp):
    name = "intersect"
    emit = staticmethod(lambda cl, cr: 1 if cl and cr else 0)


class Except(_SortedSetOp):
    name = "except"
    emit = staticmethod(lambda cl, cr: 1 if cl and not cr else 0)


class IntersectAll(_SortedSetOp):
    name = "intersect_all"
    emit = staticmethod(lambda cl, cr: min(cl, cr))


class ExceptAll(_SortedSetOp):
    name = "except_all"
    emit = staticmethod(lambda cl, cr: max(cl - cr, 0))


# ==========================================================================
# helpers
# ==========================================================================

def input_pages_hint(ctx, op):
    """Upper bound on an input's pages when it is a base table seen through
    filter / project / rename / limit (its catalog page count); None otherwise."""
    while isinstance(op, (Filter, Project, Rename, Limit)):
        op = op.children[0]
    if isinstance(op, SeqScan):
        return ctx.db.catalog.get_table(op.table)["num_pages"]
    return None


def _col(cols, text, node):
    try:
        return column_index(cols, text)
    except Exception as e:
        raise PlanError(f"Node '{node}': {getattr(e, 'message', e)}")


def _condition(text, cols, node):
    try:
        return Condition(text, cols)
    except Exception as e:
        raise PlanError(f"Node '{node}': {getattr(e, 'message', e)} in condition '{text}'")


def _set_compatible(left, right, node):
    if len(left.cols) != len(right.cols):
        raise PlanError(f"Node '{node}': inputs have {len(left.cols)} and {len(right.cols)} columns")
    for i, (a, b) in enumerate(zip(left.cols, right.cols)):
        if type_class(a[1]) != type_class(b[1]):
            raise PlanError(f"Node '{node}': column {i + 1} is {a[1]} on the left and {b[1]} on the right")
    return left.cols
