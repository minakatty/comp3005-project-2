"""Cardinality and selectivity estimation (M4 spec Section 2).

Est describes one plan node's output: estimated rows, average width, columns and,
for every output column that is still a base-table column, its lineage
(table, column) so that the column's statistics can be found after renames and
projections.

Selectivity formulas (DESIGN_M4.md 2 states each one and its assumption):
    c = v        sum over buckets holding v of count(b) / (n * ndv(b));  no histogram: 1/ndv;
                 no statistics at all: 0.1
    c < v etc.   whole buckets on the correct side plus a linear fraction of the bucket
                 holding v (strings: half of that bucket); no histogram: linear between
                 min and max; no statistics: 1/3
    c <> v       1 - sel(c = v)
    p AND q      sel(p) * sel(q)                (independence)
    p OR q       sel(p) + sel(q) - sel(p)*sel(q)
    NOT p        1 - sel(p)
    a = b        1 / max(ndv(a), ndv(b)), each ndv capped at its input's row count
    other a op b 0.1
Output rows = ceil(input rows * selectivity), never below 1.
Output pages = ceil(rows * width / 4096).
"""
import math

from ralang import ast_nodes as ra
from ralang.condition import resolve_column

PAGE = 4096
DEFAULT_EQ = 0.1
DEFAULT_RANGE = 1 / 3
DEFAULT_JOIN = 0.1


class Est:
    def __init__(self, rows, width, cols, lineage, ndv=None):
        self.rows = max(1, int(math.ceil(rows))) if rows > 0 else 1
        self.width = width
        self.cols = cols                    # [(name, type, len)]
        self.lineage = lineage              # {name: (table, column)}
        self.ndv = ndv or {}                # {name: ndv} for computed columns
        self.fixed_pages = None             # a base table's real page count

    @property
    def pages(self):
        if self.fixed_pages is not None:
            return self.fixed_pages
        return max(1, int(math.ceil(self.rows * self.width / PAGE)))


def default_width(t, ln):
    return 4.0 if t == "INT" else 8.0 if t in ("FLOAT", "LONG") else 2.0 + ln / 2


class Stats:
    """Statistics for the optimizer: from the catalog's ANALYZE results when present,
    otherwise from the catalog's own counts plus documented defaults.
    `override` ({table: stats dict}) lets tests supply statistics directly."""

    def __init__(self, db=None, override=None):
        self.db = db
        self.override = override or {}

    def schema(self, table):
        if table in self.override and "schema" in self.override[table]:
            return [tuple(c) for c in self.override[table]["schema"]]
        return [tuple(c) for c in self.db.catalog.get_table(table)["schema"]]

    def table(self, table):
        if table in self.override:
            return self.override[table]
        st = self.db.catalog.get_stats(table) if self.db else None
        if st is not None:
            return st
        info = self.db.catalog.get_table(table)
        return {"table": table, "n_tuples": info["num_tuples"], "n_pages": info["num_pages"],
                "width": sum(default_width(t, ln) for _, t, ln in info["schema"]), "columns": {},
                "default": True}

    def column(self, lineage):
        if lineage is None:
            return None
        return self.table(lineage[0]).get("columns", {}).get(lineage[1])

    def col_width(self, lineage, t, ln):
        cs = self.column(lineage)
        if cs and "avg_width" in cs:
            return cs["avg_width"]
        return default_width(t, ln)


class Estimator:
    def __init__(self, stats):
        self.stats = stats

    # -- column helpers ----------------------------------------------------------
    def ndv(self, est, name):
        if name in est.ndv:
            return min(est.ndv[name], est.rows)
        cs = self.stats.column(est.lineage.get(name))
        if cs and cs.get("ndv"):
            return max(1, min(cs["ndv"], est.rows))
        return None

    def width_of(self, est, names):
        w = 0.0
        for n in names:
            c = next(c for c in est.cols if c[0] == n)
            w += self.stats.col_width(est.lineage.get(n), c[1], c[2])
        return w

    # -- selectivity --------------------------------------------------------------
    def selectivity(self, cond, est):
        if isinstance(cond, ra.And):
            return self.selectivity(cond.left, est) * self.selectivity(cond.right, est)
        if isinstance(cond, ra.Or):
            p, q = self.selectivity(cond.left, est), self.selectivity(cond.right, est)
            return p + q - p * q
        if isinstance(cond, ra.Not):
            return 1 - self.selectivity(cond.inner, est)
        return self._compare(cond, est)

    def _name(self, attr, est):
        return est.cols[resolve_column(est.cols, attr.relation, attr.name)][0]

    def _compare(self, c, est):
        l, r, op = c.left, c.right, c.op
        la, rattr = isinstance(l, ra.Attr), isinstance(r, ra.Attr)
        if la and rattr:
            a, b = self._name(l, est), self._name(r, est)
            if op == "EQ":
                na, nb = self.ndv(est, a), self.ndv(est, b)
                if na is None and nb is None:
                    return DEFAULT_EQ
                return 1.0 / max(na or 1, nb or 1)
            if op == "NE":
                na, nb = self.ndv(est, a), self.ndv(est, b)
                return 1 - (1.0 / max(na or 1, nb or 1) if (na or nb) else DEFAULT_EQ)
            return DEFAULT_JOIN
        if not la and not rattr:
            from ralang.condition import Condition
            return 1.0 if Condition(c, []).__call__(()) else 0.0
        if not la:                                   # 30 < age  ->  age > 30
            flip = {"LT": "GT", "LE": "GE", "GT": "LT", "GE": "LE", "EQ": "EQ", "NE": "NE"}
            l, r, op = r, l, flip[op]
        name = self._name(l, est)
        v = r.value
        cs = self.stats.column(est.lineage.get(name))
        if op == "EQ":
            return self.sel_eq(cs, v, self.ndv(est, name))
        if op == "NE":
            return 1 - self.sel_eq(cs, v, self.ndv(est, name))
        below = self.sel_below(cs, v)
        if below is None:
            return DEFAULT_RANGE
        return below if op in ("LT", "LE") else 1 - below

    @staticmethod
    def sel_eq(cs, v, ndv):
        if cs and cs.get("hist"):
            n = sum(b["count"] for b in cs["hist"])
            s = sum(b["count"] / (n * b["ndv"]) for b in cs["hist"] if b["lo"] <= v <= b["hi"])
            return s if s > 0 else 1.0 / (n or 1)  # value not in any bucket: at most one row
        if ndv:
            return 1.0 / ndv
        return DEFAULT_EQ

    @staticmethod
    def sel_below(cs, v):
        """Fraction of the column below v (continuous approximation: the mass of v
        itself is not separated out)."""
        if not cs or cs.get("min") is None:
            return None
        if cs.get("hist"):
            n = sum(b["count"] for b in cs["hist"])
            acc = 0.0
            for b in cs["hist"]:
                if b["hi"] < v:
                    acc += b["count"]
                elif b["lo"] <= v:
                    if isinstance(v, (int, float)) and b["hi"] > b["lo"]:
                        acc += b["count"] * (v - b["lo"]) / (b["hi"] - b["lo"])
                    else:
                        acc += b["count"] * 0.5
            return min(1.0, acc / n) if n else None
        lo, hi = cs["min"], cs["max"]
        if not isinstance(v, (int, float)):
            return DEFAULT_RANGE if lo <= v <= hi else (0.0 if v < lo else 1.0)
        if v <= lo:
            return 0.0
        if v >= hi:
            return 1.0
        return (v - lo) / (hi - lo)
