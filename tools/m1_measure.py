"""Produces the three measurements required by M1 spec section 8. Nothing here is estimated: every
number comes from running the storage engine.

  python tools/m1_measure.py --traces traces_published --out results_m1
  python tools/m1_measure.py --traces traces_demo --out results_demo          (pipeline self-test only)

  1. Capacity: records per page by hand (floor(4088 / (L + 4))) versus measured, three schemas.
  2. Policies: hit rate for every trace, LRU and CLOCK, at B = 4, 16, 64, 256.
  3. Scan cost: page reads for a full scan of a 500 page table at B = 2, 8, 64.
"""
import argparse, csv, glob, os, shutil, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from storage.db import Database
from storage.page import SlottedPage
from storage.cli import run_trace
from storage import schema as sch


def capacity(n_rows):
    cases = [
        ("all INT", "a:INT,b:INT,c:INT", lambda i: (i, i * 7, -i)),
        ("short VARCHAR(10), value length 8", "a:INT,b:VARCHAR(10),c:INT", lambda i: (i, "abcdefgh", i)),
        ("VARCHAR(200), value length 150", "id:INT,title:VARCHAR(200)", lambda i: (i, "t" * 150)),
    ]
    out = []
    for name, ddl, gen in cases:
        d = tempfile.mkdtemp()
        db = Database(d, frames=8)
        db.catalog.create_table("T", ddl)
        cols = sch.parse_schema(ddl)
        h = db.heap("T")
        L = len(sch.encode(cols, gen(0)))
        for i in range(n_rows):
            h.insert(gen(i))
        meta = db.catalog.tables["T"]
        first_data_page = 1
        f = db.pool.fetch_page((meta["file_no"] << 32) | first_data_page)
        p = SlottedPage(f.data)
        full, free = p.slot_count, p.free_space()
        db.pool.unpin_page(f.page_id, False)
        out.append(dict(name=name, ddl=ddl, L=L, hand=sch.records_per_page(L),
                        hand_declared_max=sch.records_per_page(sch.max_record_size(cols)),
                        measured_full_page=full, tuples=meta["num_tuples"], data_pages=meta["num_pages"],
                        overall=round(meta["num_tuples"] / meta["num_pages"], 2), leftover=free))
        db.close()
        shutil.rmtree(d, ignore_errors=True)
    return out


def trace_facts(path):
    ids = [int(l.split()[0]) for l in open(path) if l.strip()]
    seq = sum(1 for a, b in zip(ids, ids[1:]) if b == a + 1)
    return len(ids), len(set(ids)), seq / max(1, len(ids) - 1)


def scan_cost(pages_wanted, sizes):
    per_page = sch.records_per_page(4 + 2 + 150)             # id INT + VARCHAR(200) holding 150 bytes
    d = tempfile.mkdtemp()
    db = Database(d, frames=16)
    db.catalog.create_table("T", "id:INT,title:VARCHAR(200)")
    h = db.heap("T")
    for i in range(per_page * pages_wanted):
        h.insert((i, "t" * 150))
    meta = db.catalog.tables["T"]
    data_pages, file_pages = meta["num_pages"], db.fileset.num_pages(meta["file_no"])
    db.close()
    rows = []
    for B in sizes:
        db = Database(d, frames=B)
        n = sum(1 for _ in db.heap("T").scan())
        rows.append(dict(B=B, rows=n, **db.pool.stats()))
        db.close()
    shutil.rmtree(d, ignore_errors=True)
    return data_pages, file_pages - data_pages, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", required=True, help="folder with the trace .txt files")
    ap.add_argument("--sizes", type=int, nargs="+", default=[4, 16, 64, 256], help="pool sizes for the policy table")
    ap.add_argument("--scan-sizes", type=int, nargs="+", default=[2, 8, 64])
    ap.add_argument("--scan-pages", type=int, default=500)
    ap.add_argument("--rows", type=int, default=3000, help="rows to load for the capacity measurement")
    ap.add_argument("--out", default="results_m1")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(a.traces, "*.txt")))
    if not files:
        sys.exit(f"no .txt trace files found in {a.traces}")

    md = ["## Measurement 1: records per page, by hand versus measured", "",
          "Hand: floor(4088 / (L + 4)) where L is the record length in bytes and 4 is the slot entry.", "",
          "| schema | L (bytes) | by hand | by hand if declared max were used | measured on a full page | tuples / data pages | bytes left on that page |",
          "|---|---|---|---|---|---|---|"]
    for c in capacity(a.rows):
        md.append(f"| {c['name']} (`{c['ddl']}`) | {c['L']} | {c['hand']} | {c['hand_declared_max']} | "
                  f"{c['measured_full_page']} | {c['overall']} ({c['tuples']}/{c['data_pages']}) | {c['leftover']} |")

    md += ["", "## Measurement 2: buffer pool hit rate (hits / references)", "",
           "| trace | refs | distinct pages | seq. fraction | B | LRU | CLOCK |", "|---|---|---|---|---|---|---|"]
    rows_csv = []
    for tf in files:
        refs, distinct, seqf = trace_facts(tf)
        for B in a.sizes:
            r = {p: run_trace(tf, B, p) for p in ("lru", "clock")}
            md.append(f"| {os.path.basename(tf)} | {refs} | {distinct} | {seqf:.2f} | {B} | {r['lru']['hit_rate']:.4f} | {r['clock']['hit_rate']:.4f} |")
            for p in ("lru", "clock"):
                x = r[p]
                rows_csv.append([os.path.basename(tf), p, B, x["hits"], x["misses"], x["evictions"], x["reads"], x["writes"], x["hit_rate"]])

    md += ["", "## Measurement 3: scan cost", ""]
    data_pages, dir_pages, scan_rows = scan_cost(a.scan_pages, a.scan_sizes)
    md += [f"Table scanned: {data_pages} data pages + {dir_pages} directory page(s).", "",
           "| B | rows returned | page reads | hits | misses | evictions |", "|---|---|---|---|---|---|"]
    for r in scan_rows:
        md.append(f"| {r['B']} | {r['rows']} | {r['reads']} | {r['hits']} | {r['misses']} | {r['evictions']} |")

    text = "\n".join(md) + "\n"
    with open(os.path.join(a.out, "REPORT_M1_tables.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(a.out, "trace_results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trace", "policy", "frames", "hits", "misses", "evictions", "reads", "writes", "hit_rate"])
        w.writerows(rows_csv)
    print(text)
    print(f"wrote {a.out}/REPORT_M1_tables.md and {a.out}/trace_results.csv")


if __name__ == "__main__":
    main()
