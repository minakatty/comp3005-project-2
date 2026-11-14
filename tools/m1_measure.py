"""The three Module 1 measurements (M1 spec Section 8), written as markdown tables.

    python tools/m1_measure.py --traces traces_published --out results_m1

Measurement 1 (capacity) and 3 (scan cost) build their own tables in a scratch
database. Measurement 2 reads every *.txt in --traces whose lines are page ids
(other files, such as a README, are skipped with a note).
"""
import argparse
import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.cli import run_trace                    # noqa: E402
from storage.db import Database                      # noqa: E402
from storage.page import USABLE, SLOT                # noqa: E402
from storage.record import max_record_length, parse_schema   # noqa: E402

SCHEMAS = [
    ("all INT", "a:INT,b:INT,c:INT", lambda i: f"{i},{i * 7},{i % 13}"),
    ("short VARCHAR", "a:INT,b:VARCHAR(5),c:INT", lambda i: f"{i},bob,{i % 7}"),
    ("VARCHAR(200)", "a:INT,s:VARCHAR(200)", lambda i: f"{i},{'x' * 200}"),
]


def capacity(tmp):
    rows = []
    for label, schema, gen in SCHEMAS:
        cols = parse_schema(schema)
        db = Database(os.path.join(tmp, label.replace(" ", "_").replace("(", "").replace(")", "")), 16)
        db.create_table("T", cols)
        path = os.path.join(tmp, "cap.csv")
        with open(path, "w") as f:
            f.write("\n".join(gen(i) for i in range(3000)) + "\n")
        db.load_csv("T", path)
        # record length of the rows we loaded (they are all the same length here)
        L = max_record_length(cols) if label != "short VARCHAR" else 4 + 2 + 3 + 4
        computed = USABLE // (L + SLOT)
        per_page = []
        for pid in db.heap("T").data_pages():
            fr = db.pool.fetch_page(db.table_disk("T"), pid)
            from storage.page import SlottedPage
            per_page.append(SlottedPage(fr.data).slot_count)
            db.pool.unpin_page(db.table_disk("T"), pid)
        measured = max(per_page)
        db.close()
        rows.append((label, schema, L, computed, measured, len(per_page)))
    out = ["| schema | columns | record bytes L | computed floor(4088/(L+4)) | measured per full page | data pages for 3000 rows |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r[0]} | `{r[1]}` | {r[2]} | {r[3]} | {r[4]} | {r[5]} |")
    return out, rows


def policies(trace_dir):
    files = sorted(f for f in os.listdir(trace_dir) if f.endswith(".txt"))
    out = ["| trace | policy | B | hits | misses | evictions | hit rate |", "|---|---|---|---|---|---|---|"]
    notes = []
    for f in files:
        ids = []
        ok = True
        for line in open(os.path.join(trace_dir, f)):
            s = line.strip()
            if not s:
                continue
            tok = s.split()[0]
            if not tok.isdigit():
                ok = False
                break
            ids.append(int(tok))
        if not ok or not ids:
            notes.append(f"skipped {f}: not a list of page ids")
            continue
        for policy in ("lru", "clock"):
            for B in (4, 16, 64, 256):
                r = run_trace(ids, B, policy)
                rate = r["hits"] / len(ids)
                out.append(f"| {f} | {policy} | {B} | {r['hits']} | {r['misses']} | {r['evictions']} | {rate:.4f} |")
    return out, notes


def scan_cost(tmp):
    db = Database(os.path.join(tmp, "scan"), 64)
    db.create_table("Big", parse_schema("a:INT,b:VARCHAR(5),c:INT"))
    path = os.path.join(tmp, "big.csv")
    with open(path, "w") as f:
        f.write("\n".join(f"{i},bob,{i % 7}" for i in range(240 * 500)) + "\n")
    db.load_csv("Big", path)
    pages = db.catalog.get_table("Big")["num_pages"]
    db.close()
    out = [f"Table Big: {pages} data pages + 1 directory page, 120000 rows of 13 bytes.", "",
           "| B | page reads | hits | misses | evictions | max frames pinned |", "|---|---|---|---|---|---|"]
    for B in (2, 8, 64):
        d = Database(os.path.join(tmp, "scan"), B)
        d.pool.reset_stats()
        d.pool.peak_held = 0
        n = sum(1 for _ in d.heap("Big").scan())
        s = d.pool.stats()
        out.append(f"| {B} | {s['reads']} | {s['hits']} | {s['misses']} | {s['evictions']} | {d.pool.peak_held} |")
        d.close()
        assert n == 120000
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", default="traces_published")
    ap.add_argument("--out", default="results_m1")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tmp = tempfile.mkdtemp()
    try:
        cap, _ = capacity(tmp)
        pol, notes = policies(a.traces) if os.path.isdir(a.traces) else (["(no trace folder)"], [])
        scan = scan_cost(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = ["# Module 1 measurements", "", "## 1. Capacity", ""] + cap + \
           ["", f"## 2. Replacement policies (traces from `{a.traces}`)", ""] + pol + \
           [""] + [f"- {n}" for n in notes] + ["", "## 3. Scan cost", ""] + scan
    with open(os.path.join(a.out, "REPORT_M1_tables.md"), "w") as f:
        f.write("\n".join(text) + "\n")
    print("\n".join(text))


if __name__ == "__main__":
    main()
