"""Integration: run a folder (or one file) of SQL queries through the whole system and
write the table the handout asks for (Section 6): for each query the result row
count, page I/O, wall time, and estimated against actual rows at the root.

    python tools/run_workload.py --db db --queries hidden_queries --frames 32 --out results_final.md

--queries can be a folder of .sql files (one query each) or a single .sql file with
several queries separated by ';'.
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbms.shell import Shell, split_statements     # noqa: E402
from storage.errors import read_text               # noqa: E402


def load(path):
    if os.path.isdir(path):
        out = []
        for f in sorted(os.listdir(path)):
            if f.endswith(".sql"):
                for i, q in enumerate(split_statements(read_text(os.path.join(path, f)))):
                    out.append((f if i == 0 else f"{f}#{i + 1}", q.strip()))
        return out
    return [(f"q{i + 1:02d}", q.strip()) for i, q in enumerate(split_statements(read_text(path)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db")
    ap.add_argument("--queries", required=True)
    ap.add_argument("--frames", type=int, default=32)
    ap.add_argument("--out", default="results_final.md")
    a = ap.parse_args()
    sh = Shell(a.db, a.frames, out=io.StringIO())       # rows are not printed, only counted
    lines = [f"# Workload results (B = {a.frames})", "",
             "| query | rows | page reads | page writes | page I/O | wall time (s) | est rows (root) | actual rows (root) | q-error |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, q in load(a.queries):
        info = sh.run(q)
        if not info or info.get("kind") != "query":
            err = sh.out.getvalue().strip().splitlines()[-1:] or ["?"]
            lines.append(f"| {name} | error: {err[0]} | | | | | | | |")
            continue
        est, act = info["est_root"], info["actual_root"]
        qe = max(est, act, 1) / max(min(est, act), 1)
        lines.append(f"| {name} | {info['rows']} | {info['page_reads']} | {info['page_writes']} | {info['io']} | "
                     f"{info['wall']:.3f} | {est} | {act} | {qe:.2f} |")
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
