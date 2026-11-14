"""ANALYZE: exact statistics, stored in the Module 1 catalog (M4 spec Section 1).

Per table:   n_tuples, n_pages (data pages only), width (average record bytes)
Per column:  ndv, min, max, avg_width, and an equi-depth histogram of k buckets,
             each bucket {"lo", "hi", "count", "ndv"}

Every column is sorted with Module 2's external sort (a sort plan over a seqscan,
run under a fixed budget), because ANALYZE on a large table cannot hold a column in
memory. No sampling, so two ANALYZE runs give byte-identical statistics.

Equi-depth: sort the column, cut at every ceil(n/k)-th position. A value that
straddles a cut appears in two buckets; selectivity sums over every bucket that
holds it (DESIGN_M4.md 2).
"""
from execution.engine import execute
from storage import record



def analyze(db, table, buckets=20):
    info = db.catalog.get_table(table)
    cols = info["schema"]
    n_pages = info["num_pages"]
    n, total_len = 0, 0
    for _, row in db.heap(table).scan():
        n += 1
        total_len += record.encoded_length(cols, row)
    stats = {"table": table, "n_tuples": n, "n_pages": n_pages,
             "width": round(total_len / n, 4) if n else 0.0, "buckets": buckets, "columns": {}}
    for name, typ, ln in cols:
        q = f"{table}.{name}"
        plan = {"op": "sort", "keys": [[q, "asc"]],
                "child": {"op": "project", "columns": [q], "child": {"op": "seqscan", "table": table}}}
        res = execute(db, plan, db.pool.B)
        vals = [r[0] for r in res.rows]
        stats["columns"][name] = column_stats(vals, typ, buckets)
    db.catalog.put_stats(table, stats)
    return stats


def column_stats(vals, typ, k):
    """vals must be sorted."""
    n = len(vals)
    out = {"type": typ, "ndv": 0, "min": None, "max": None, "avg_width": 4.0 if typ == "INT" else 2.0, "hist": []}
    if not n:
        return out
    ndv = 1 + sum(1 for i in range(1, n) if vals[i] != vals[i - 1])
    out.update(ndv=ndv, min=vals[0], max=vals[-1])
    if typ == "VARCHAR":
        out["avg_width"] = round(sum(2 + len(v.encode("utf-8")) for v in vals) / n, 4)
    step = -(-n // max(1, k))
    hist = []
    for s in range(0, n, step):
        part = vals[s:s + step]
        bndv = 1 + sum(1 for i in range(1, len(part)) if part[i] != part[i - 1])
        hist.append({"lo": part[0], "hi": part[-1], "count": len(part), "ndv": bndv})
    out["hist"] = hist
    return out
