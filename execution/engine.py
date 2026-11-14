"""Run a physical plan end to end. Used by the exec CLI, the optimizer's measurements
and the integration shell. Prints nothing."""
from storage.db import open_db
from storage.errors import BufferPoolError

from .errors import ExecError, PlanError
from .operators import ExecContext
from .plan import allocate, build, validate, walk


class Result:
    def __init__(self, root, rows, pool, ctx):
        self.root = root
        self.rows = rows
        self.schema = [[c[0], c[1]] if c[1] != "VARCHAR" else [c[0], c[1], c[2]] for c in root.cols]
        nodes = [o for o, _ in walk(root)]
        self.totals = {k: sum(o.counters()[k] for o in nodes)
                       for k in ("tuples_out", "comparisons", "page_reads", "page_writes")}
        self.totals["tuples_out"] = root.tuples_out
        self.peak_frames = pool.peak_held
        self.B = ctx.B
        self.share = root_share(root)
        self.leftover_pins = len(pool.pinned_frames())
        self.leftover_reserved = pool.reserved_count
        self.leftover_temps = ctx.db.temp_files_left()

    def trace_lines(self):
        lines = []
        for op, d in walk(self.root):
            c = op.counters()
            label = "  " * d + op.label()
            line = (f"{label:<46} tuples_out={c['tuples_out']:<8} comparisons={c['comparisons']:<10} "
                    f"reads={c['page_reads']:<7} writes={c['page_writes']}")
            if op.info:
                line += "   " + " ".join(f"{k}={v}" for k, v in op.info.items())
            lines.append(line)
        return lines

    def to_json(self, include_rows=True):
        out = {"schema": self.schema}
        if include_rows:
            out["rows"] = [list(r) for r in self.rows]
        out["totals"] = self.totals
        out["frames"] = self.B
        out["peak_frames_held"] = self.peak_frames
        out["operators"] = [dict(op=op.label(), depth=d, **op.counters(), **({"info": op.info} if op.info else {}))
                            for op, d in walk(self.root)]
        return out


def root_share(root):
    return max((o.share for o, _ in walk(root)), default=0)


def execute(db, plan, B, collect=True, limit_rows=None):
    """Validate, build, allocate, open, drain, close. `db` must have been opened with
    a pool of exactly B frames. Returns a Result."""
    validate(plan)
    ctx = ExecContext(db, B)
    root = build(ctx, plan)               # unknown table / column fails here, before any I/O
    allocate(root, B)
    db.pool.peak_held = db.pool.held()
    rows = []
    try:
        root.open()
        while True:
            r = root.next()
            if r is None:
                break
            if collect and (limit_rows is None or len(rows) < limit_rows):
                rows.append(r)
    except BufferPoolError as e:
        raise ExecError(f"the plan needs more than B = {B} frames: {e}")
    finally:
        root.close()
    return Result(root, rows, db.pool, ctx)


def run_plan(db_dir, plan, B, policy="lru", collect=True):
    db = open_db(db_dir, frames=B, policy=policy)
    try:
        return execute(db, plan, B, collect)
    finally:
        db.close()


__all__ = ["execute", "run_plan", "Result", "PlanError"]
