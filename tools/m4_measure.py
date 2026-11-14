"""The four Module 4 measurements (M4 spec Section 7).

    python tools/m4_measure.py --db db_m4 --workload workload_m4 --frames 16 --out results_m4
    (run `python -m optimizer analyze` on every table first, or pass --analyze)

  1. Estimation quality: q-error = max(est, actual) / min(est, actual) at every node.
  2. Does optimization pay: actual I/O of the chosen plan against the unoptimized plan
     (joins in written order, block nested loop everywhere, no pushdown).
  3. Which rule mattered: total workload I/O with each rewrite rule turned off.
  4. Where it breaks: --correlated q13.sql runs the chosen plan, then the same plan
     with the misjudged join forced to each other algorithm, and prints both.
"""
import argparse
import copy
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.engine import execute                       # noqa: E402
from optimizer.estimate import Stats                       # noqa: E402
from optimizer.logical import ALL_RULES                    # noqa: E402
from optimizer.optimize import naive_physical, optimize    # noqa: E402
from optimizer.stats import analyze                        # noqa: E402
from sqlfront.compiler import catalog_from_db, compile_sql  # noqa: E402
from storage.db import Database                            # noqa: E402


def load_queries(d):
    out = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".sql"):
            with open(os.path.join(d, f)) as fh:
                out.append((f, fh.read()))
    return out


def run_phys(db_path, phys, B):
    db = Database(db_path, B)
    try:
        t0 = time.perf_counter()
        res = execute(db, phys, B, collect=False)
        res.wall = time.perf_counter() - t0
    finally:
        db.close()
    return res


def io(res):
    return res.totals["page_reads"] + res.totals["page_writes"]


def plan_for(db_path, sql, B, disabled=()):
    db = Database(db_path, 8)
    try:
        lp = compile_sql(sql, catalog_from_db(db))[0]
        _, root = optimize(lp, Stats(db), B, disabled)
    finally:
        db.close()
    return lp, root


def walk_ops(op):
    yield op
    for c in op.children:
        yield from walk_ops(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db_m4")
    ap.add_argument("--workload", default="workload_m4")
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument("--out", default="results_m4")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--correlated", default="q13.sql")
    ap.add_argument("--skip-naive", action="store_true")
    ap.add_argument("--only-qerror", action="store_true", help="section 1 only")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    B = a.frames
    if a.analyze:
        db = Database(a.db, 16)
        for t in db.catalog.list_tables():
            analyze(db, t)
        db.close()
    queries = load_queries(a.workload)
    md = [f"# Module 4 measurements (B = {B})", ""]

    # 1 and 2 ---------------------------------------------------------------------------
    qerrs, rows2, per_query = [], [], []
    md += ["## 1. Estimated against actual rows, every node", ""]
    for name, sql in queries:
        lp, root = plan_for(a.db, sql, B)
        res = run_phys(a.db, root.json(), B)
        nodes = []
        for p, o in zip(root.walk(), walk_ops(res.root)):
            # a rescanned nested-loop inner is opened once per outer block: compare the
            # estimate with the rows of one execution
            est, act = p.est.rows, o.tuples_out // max(1, o.opens)
            q = max(est, act, 1) / max(min(est, act), 1)
            qerrs.append(q)
            nodes.append((p.label, est, act, q))
        per_query.append((name, nodes))
        if a.only_qerror:
            continue
        if not a.skip_naive:
            nres = run_phys(a.db, naive_physical(lp), B)
            rows2.append((name, io(res), io(nres), io(nres) / max(1, io(res)), res.wall, nres.wall))
        else:
            rows2.append((name, io(res), None, None, res.wall, None))
    md.append(f"Nodes: {len(qerrs)}. Median q-error **{statistics.median(qerrs):.2f}**, "
              f"worst **{max(qerrs):.1f}**.")
    md.append("")
    for name, nodes in per_query:
        md += [f"<details><summary>{name}</summary>", "", "| node | est | actual | q-error |", "|---|---|---|---|"]
        md += [f"| `{l}` | {e} | {r} | {q:.2f} |" for l, e, r, q in nodes]
        md += ["", "</details>", ""]
    if a.only_qerror:
        print("\n".join(md))
        return
    md += ["## 2. Does optimization pay", "",
           "| query | optimized I/O | unoptimized I/O | ratio | optimized s | unoptimized s |",
           "|---|---|---|---|---|---|"]
    for n, o, u, r, w1, w2 in rows2:
        md.append(f"| {n} | {o} | {u if u is not None else '-'} | {f'{r:.2f}' if r else '-'} | {w1:.2f} | "
                  f"{f'{w2:.2f}' if w2 is not None else '-'} |")
    ratios = [r[3] for r in rows2 if r[3]]
    if ratios:
        md.append(f"\nRatio range across the workload: {min(ratios):.2f} to {max(ratios):.2f}.")
    md.append("")

    # 3 ---------------------------------------------------------------------------------
    md += ["## 3. Which rule mattered", "", "| rule off | total workload I/O | change vs all rules on |", "|---|---|---|"]
    total_on = sum(r[1] for r in rows2)
    md.append(f"| (none) | {total_on} | |")
    for rule in ALL_RULES:
        tot = 0
        for name, sql in queries:
            _, root = plan_for(a.db, sql, B, disabled=(rule,))
            tot += io(run_phys(a.db, root.json(), B))
        md.append(f"| {rule} | {tot} | {tot - total_on:+d} ({(tot / total_on - 1) * 100:+.1f}%) |")
    md.append("")

    # 4 ---------------------------------------------------------------------------------
    corr = dict(queries).get(a.correlated)
    if corr:
        md += ["## 4. Where it breaks", "", f"Query `{a.correlated}`: `{corr.strip()}`", ""]
        lp, root = plan_for(a.db, corr, B)
        res = run_phys(a.db, root.json(), B)
        md += ["Chosen plan, with estimated and actual rows:", "", "```"]
        for p, o in zip(root.walk(), walk_ops(res.root)):
            md.append(f"{p.label:<60} est={p.est.rows:<7} actual={o.tuples_out // max(1, o.opens):<7} "
                      f"reads={o.page_reads} writes={o.page_writes}")
        md += ["```", "", f"Chosen plan: estimated cost {int(root.cost)}, actual I/O **{io(res)}**.", ""]
        phys = root.json()
        for alt in ("hash_join", "sort_merge_join", "nested_loop_join"):
            p2 = copy.deepcopy(phys)
            if _force(p2, alt):
                r2 = run_phys(a.db, p2, B)
                md.append(f"- same plan with the join forced to **{alt}**: actual I/O {io(r2)}")
        md.append("")
    with open(os.path.join(a.out, "REPORT_M4_tables.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


def _force(node, alt):
    """Replace the first join in the plan by `alt`. Returns True if one was found."""
    if node["op"].endswith("_join") and node.get("condition"):
        node["op"] = alt
        return True
    for k in ("child", "left", "right"):
        if k in node and _force(node[k], alt):
            return True
    return False


if __name__ == "__main__":
    main()
