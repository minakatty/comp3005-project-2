"""The five Module 2 measurements (M2 spec Section 5). Writes results_m2/REPORT_M2_tables.md,
CSV files and two plots.

    python tools/m2_measure.py --out results_m2 [--quick]

Every number is a counter from a real run of the executor; nothing is estimated.
"""
import argparse
import csv
import math
import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.engine import execute          # noqa: E402
from storage.db import Database               # noqa: E402
from storage.record import parse_schema       # noqa: E402

PAD = 150          # bytes of padding per tuple: about 25 tuples per page


def make_table(path, name, n, keyfn, rnd):
    db = Database(path, 64)
    db.create_table(name, parse_schema(f"k:INT,id:INT,pad:VARCHAR({PAD})"))
    h = db.heap(name)
    for i in range(n):
        h.insert((keyfn(rnd), i, "p" * PAD))
    db.catalog.update_counts(name, db.count_data_pages(name), n)
    pages = db.catalog.get_table(name)["num_pages"]
    db.close()
    return pages


def run(path, plan, B):
    db = Database(path, B)
    t0 = time.perf_counter()
    res = execute(db, plan, B, collect=False)
    res.wall = time.perf_counter() - t0
    db.close()
    return res


def sort_cost(M, B):
    if M <= B:
        return M
    p = 1 + math.ceil(math.log(math.ceil(M / B)) / math.log(B - 1))
    return 2 * M * (p - 1) + M


def formula(join, M, N, B):
    if join == "nested_loop_join":
        return M + math.ceil(M / (B - 2)) * N
    if join == "hash_join":
        return 3 * (M + N)
    return sort_cost(M, B) + sort_cost(N, B) + M + N


def jplan(join, l="R", r="S"):
    return {"op": join, "condition": f"{l}.k = {r}.k", "left": {"op": "seqscan", "table": l},
            "right": {"op": "seqscan", "table": r}}


JOINS = ("nested_loop_join", "sort_merge_join", "hash_join")


def m_cost(path, sizes, Bs, md, rows_csv):
    md += ["## 1. Join cost against the formula", "",
           "M, N are data pages. Measured reads include each scan's directory page.", "",
           "| join | M | N | B | measured reads | measured writes | measured total | formula | difference | comparisons |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for n_tuples in sizes:
        rnd = random.Random(n_tuples)
        sub = os.path.join(path, f"size{n_tuples}")
        M = make_table(sub, "R", n_tuples, lambda r: r.randint(0, n_tuples // 4), rnd)
        N = make_table(sub, "S", n_tuples, lambda r: r.randint(0, n_tuples // 4), rnd)
        for join in JOINS:
            for B in Bs:
                res = run(sub, jplan(join), B)
                rd, wr = res.totals["page_reads"], res.totals["page_writes"]
                f = formula(join, M, N, B)
                md.append(f"| {join} | {M} | {N} | {B} | {rd} | {wr} | {rd + wr} | {f} | {rd + wr - f:+d} | "
                          f"{res.totals['comparisons']} |")
                rows_csv.append([join, M, N, B, rd, wr, f, res.totals["comparisons"], round(res.wall, 3)])
    md.append("")


def m_sort(path, md):
    md += ["## 2. External sort passes", "",
           "W = B - 1 working frames (one frame is the input page, DESIGN_M2.md 3). "
           "Own formula: runs0 = ceil(N/W); passes = 1 + (merge passes with fan-in W-1 until <= W runs) + 1.", "",
           "| N pages | B | runs after pass 0 | measured passes | spec formula 1+ceil(log(ceil(N/B))/log(B-1)) | "
           "own formula | reads | writes | peak frames |",
           "|---|---|---|---|---|---|---|---|---|"]
    combos = [(8, 3), (80, 4), (160, 8), (64, 16), (100, 5), (320, 16), (40, 4)]
    for N, B in combos:
        sub = os.path.join(path, f"sort{N}")
        if not os.path.exists(sub):
            db = Database(sub, 64)
            db.create_table("T", parse_schema("k:INT,s:VARCHAR(5),i:INT"))
            h = db.heap("T")
            rnd = random.Random(N)
            for i in range(240 * N):
                h.insert((rnd.randint(0, 10 ** 6), "abc", i))
            db.catalog.update_counts("T", db.count_data_pages("T"), 240 * N)
            db.close()
        res = run(sub, {"op": "sort", "keys": [["T.k", "asc"]], "child": {"op": "seqscan", "table": "T"}}, B)
        info = res.root.info
        spec = 1 if N <= B else 1 + math.ceil(math.log(math.ceil(N / B)) / math.log(B - 1))
        W = B - 1
        r = math.ceil(N / W)
        own = 1
        if r > 1:
            F = max(2, W - 1)
            while r > max(2, W):
                r = math.ceil(r / F)
                own += 1
            own += 1
        md.append(f"| {N} | {B} | {info['runs_after_each_pass'][0] if info['runs_after_each_pass'] else 1} | "
                  f"{info['passes']} | {spec} | {own} | {res.totals['page_reads']} | {res.totals['page_writes']} | "
                  f"{res.peak_frames} |")
    md.append("")


def m_crossover(path, out, md, quick):
    md += ["## 3. Crossover", "", "### 3a. Fixed relations, varying B", ""]
    n = 600 if quick else 1000
    rnd = random.Random(1)
    sub = os.path.join(path, "cross")
    M = make_table(sub, "R", n, lambda r: r.randint(0, 200), rnd)
    N = make_table(sub, "S", n, lambda r: r.randint(0, 200), rnd)
    Bs = [3, 4, 5, 6, 8, 12, 16, 24, 32, 48]
    md += [f"R = {M} pages, S = {N} pages.", "", "| B | " + " | ".join(JOINS) + " | cheapest |",
           "|---|---|---|---|---|"]
    series = {j: [] for j in JOINS}
    for B in Bs:
        row = {}
        for j in JOINS:
            r = run(sub, jplan(j), B)
            row[j] = r.totals["page_reads"] + r.totals["page_writes"]
            series[j].append(row[j])
        md.append(f"| {B} | " + " | ".join(str(row[j]) for j in JOINS) + f" | {min(row, key=row.get)} |")
    plot(Bs, series, "B (frames)", "page I/O", os.path.join(out, "crossover_B.png"), f"R={M}p, S={N}p")
    md += ["", "### 3b. Fixed B = 8, varying size (R and S the same size)", "",
           "| pages each | " + " | ".join(JOINS) + " | cheapest |", "|---|---|---|---|---|"]
    sizes = [50, 150, 400, 900] if quick else [50, 150, 400, 900, 1500]
    series2, xs = {j: [] for j in JOINS}, []
    for s in sizes:
        rnd = random.Random(s)
        sp = os.path.join(path, f"cs{s}")
        pm = make_table(sp, "R", s, lambda r: r.randint(0, s // 4 + 1), rnd)
        make_table(sp, "S", s, lambda r: r.randint(0, s // 4 + 1), rnd)
        xs.append(pm)
        row = {}
        for j in JOINS:
            r = run(sp, jplan(j), 8)
            row[j] = r.totals["page_reads"] + r.totals["page_writes"]
            series2[j].append(row[j])
        md.append(f"| {pm} | " + " | ".join(str(row[j]) for j in JOINS) + f" | {min(row, key=row.get)} |")
    plot(xs, series2, "pages per relation", "page I/O", os.path.join(out, "crossover_size.png"), "B = 8")
    md.append("")


def m_skew(path, md):
    md += ["## 4. Skew", "", "40% of the rows on both sides have key 7; the rest are uniform over 0..999. "
           "Hash join at B = 16.", ""]
    for label, skew in (("uniform", 0.0), ("skewed", 0.4)):
        rnd = random.Random(42)
        kf = (lambda r: 7 if r.random() < skew else r.randint(0, 999))
        sub = os.path.join(path, f"skew_{label}")
        make_table(sub, "R", 1500, kf, rnd)
        make_table(sub, "S", 1500, kf, rnd)
        res = run(sub, jplan("hash_join"), 16)
        info = res.root.info
        md += [f"**{label}**: partitions (left pages) {info['left_partition_pages']}, "
               f"(right pages) {info['right_partition_pages']}, overflowed partitions "
               f"{info['overflowed_partitions']}, reads {res.totals['page_reads']}, writes "
               f"{res.totals['page_writes']}, comparisons {res.totals['comparisons']}, "
               f"output {res.root.tuples_out} rows, {res.wall:.2f} s", ""]


def m_budget(path, md):
    sub = os.path.join(path, "sort80")
    if not os.path.exists(sub):
        return
    res = run(sub, {"op": "sort", "keys": [["T.k", "asc"]], "child": {"op": "seqscan", "table": "T"}}, 4)
    md += ["## 5. The budget", "",
           f"Sort of {res.root.info['input_pages']} pages (20 x B) at B = 4: completed, "
           f"{res.root.info['passes']} passes, {res.totals['page_reads']} reads, {res.totals['page_writes']} writes. "
           f"Largest number of frames held at once (pinned + reserved, BufferPool.peak_held): "
           f"**{res.peak_frames}**.", ""]


def plot(xs, series, xl, yl, path, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(6.4, 4))
    for name, ys in series.items():
        ax.plot(xs, ys, marker="o", label=name)
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    ax.set_yscale("log")
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results_m2")
    ap.add_argument("--quick", action="store_true", help="smaller sizes, for a fast check")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tmp = tempfile.mkdtemp()
    md, rows = ["# Module 2 measurements", ""], []
    try:
        sizes = [250, 750, 2500] if not a.quick else [250, 750]
        m_cost(tmp, sizes, [4, 16, 64, 256], md, rows)
        m_sort(tmp, md)
        m_crossover(tmp, a.out, md, a.quick)
        m_skew(tmp, md)
        m_budget(tmp, md)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(a.out, "join_costs.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["join", "M", "N", "B", "reads", "writes", "formula", "comparisons", "wall_s"])
        w.writerows(rows)
    with open(os.path.join(a.out, "REPORT_M2_tables.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
