"""Produces both tables required by handout section 2.4.

  python tools/m1_measure.py --traces traces_published --out results_m1
  python tools/m1_measure.py --traces traces_demo --out results_demo        (pipeline self-test)

Table 1: records per 4 KB page, by hand versus measured, for three schemas.
Table 2: hit rate for every trace x {LRU, CLOCK} x four pool sizes.
Nothing here is estimated: every number comes from running the storage engine.
"""
import argparse, csv, glob, os, random, shutil, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from storage.db import Database
from storage.page import SlottedPage, HEADER, SLOT
from storage import schema as sch
from storage.cli import run_trace

PAGE = 4096


def per_page(rec_size):
    return (PAGE - HEADER) // (rec_size + SLOT)


def capacity_rows(n_rows=3000):
    rnd = random.Random(1)
    cases = [
        # name, schema, row generator, hand-calc record size (bytes), note
        ("INT only", "a:INT,b:INT,c:INT", lambda i: (i, i * 7, -i), 12, "every record is exactly 12 bytes"),
        ("mixed, VARCHAR(20) lengths 4-16", "a:INT,b:VARCHAR(20),c:INT",
         lambda i: (i, "x" * rnd.randint(4, 16), i), None, "variable lengths; hand calc uses the average (10) and the declared max"),
        ("long VARCHAR(200), length 150", "id:INT,title:VARCHAR(200)",
         lambda i: (i, "t" * 150), 4 + 1 + 150, "one very long column"),
    ]
    out = []
    for name, schema_str, gen, rec, note in cases:
        d = tempfile.mkdtemp()
        db = Database(d, frames=8)
        db.catalog.create_table("T", schema_str)
        h = db.heap("T")
        sizes = []
        cols = sch.parse_schema(schema_str)
        for i in range(n_rows):
            row = gen(i)
            sizes.append(len(sch.encode(cols, row)))
            h.insert(row)
        meta = db.catalog.tables["T"]
        first = db.pool.fetch_page(meta["first_page"])
        p = SlottedPage(first.data)
        full_slots, free = p.num_slots, p.free_space()
        used = sum(p.slot(i)[1] for i in range(p.num_slots))
        db.pool.unpin_page(meta["first_page"], False)
        avg = sum(sizes) / len(sizes)
        by_hand_avg = per_page(round(avg))
        by_hand_max = per_page(sch.max_record_size(cols))
        if rec is not None:
            by_hand_avg = per_page(rec)
        out.append(dict(schema=name, ddl=schema_str, avg_rec=round(avg, 2), max_rec=sch.max_record_size(cols),
                        hand_avg=by_hand_avg, hand_max=by_hand_max, first_page_slots=full_slots,
                        overall=round(meta["num_tuples"] / meta["num_pages"], 2), pages=meta["num_pages"],
                        tuples=meta["num_tuples"], first_page_free=free, first_page_data=used, note=note))
        db.close()
        shutil.rmtree(d, ignore_errors=True)
    return out


def trace_facts(path):
    ids = [int(l.split()[0]) for l in open(path) if l.strip()]
    seq = sum(1 for a, b in zip(ids, ids[1:]) if b == a + 1)
    return len(ids), len(set(ids)), (seq / max(1, len(ids) - 1))


def trace_table(trace_files, sizes):
    rows = []
    for tf in trace_files:
        n, distinct, seqfrac = trace_facts(tf)
        for pol in ("lru", "clock"):
            res = {s: run_trace(tf, s, pol) for s in sizes}
            rows.append(dict(trace=os.path.basename(tf), policy=pol, refs=n, distinct=distinct,
                             seqfrac=round(seqfrac, 2), res=res))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", required=True, help="folder containing the trace .txt files")
    ap.add_argument("--sizes", type=int, nargs=4, default=[4, 8, 16, 32], metavar="B",
                    help="four pool sizes (frames); pick them relative to each trace's distinct page count")
    ap.add_argument("--out", default="results_m1")
    ap.add_argument("--rows", type=int, default=3000)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(a.traces, "*.txt")))
    if not files:
        sys.exit(f"no .txt trace files found in {a.traces}")

    cap = capacity_rows(a.rows)
    tr = trace_table(files, a.sizes)

    md = ["## Table 1: records per page, by hand versus measured", "",
          "Formula: records = floor((4096 - 8) / (record_size + 4)); 8 = page header, 4 = one slot entry.", "",
          "| schema | avg record (B) | max record (B) | by hand (avg) | by hand (declared max) | measured (full first page) | measured overall (tuples/pages) | free bytes left on first page |",
          "|---|---|---|---|---|---|---|---|"]
    for c in cap:
        md.append(f"| {c['schema']} (`{c['ddl']}`) | {c['avg_rec']} | {c['max_rec']} | {c['hand_avg']} | {c['hand_max']} | "
                  f"{c['first_page_slots']} | {c['overall']} ({c['tuples']}/{c['pages']}) | {c['first_page_free']} |")
    md += ["", "## Table 2: buffer pool hit rate (hits / references)", "",
           "| trace | policy | refs | distinct pages | seq. fraction | " + " | ".join(f"B={s}" for s in a.sizes) + " |",
           "|---|---|---|---|---|" + "---|" * len(a.sizes)]
    for r in tr:
        cells = [f"{r['res'][s]['hit_rate']:.3f}" for s in a.sizes]
        md.append(f"| {r['trace']} | {r['policy'].upper()} | {r['refs']} | {r['distinct']} | {r['seqfrac']} | " + " | ".join(cells) + " |")
    text = "\n".join(md) + "\n"
    open(os.path.join(a.out, "REPORT_M1_tables.md"), "w", encoding="utf-8").write(text)

    with open(os.path.join(a.out, "trace_results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trace", "policy", "frames", "hits", "misses", "evictions", "writes", "hit_rate"])
        for r in tr:
            for s in a.sizes:
                x = r["res"][s]
                w.writerow([r["trace"], r["policy"], s, x["hits"], x["misses"], x["evictions"], x["writes"], x["hit_rate"]])
    print(text)
    print(f"wrote {a.out}/REPORT_M1_tables.md and {a.out}/trace_results.csv")


if __name__ == "__main__":
    main()
