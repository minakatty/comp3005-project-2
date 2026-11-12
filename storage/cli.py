"""storage create-table | load | scan | layout | trace"""
import argparse, json, sys
from .db import Database
from .page import SlottedPage, HEADER, SLOT
from .bufferpool import BufferPool
from .disk import NullDisk
from . import schema as sch


def run_trace(path, frames, policy):
    """Trace line: `<page_id>` or `<page_id> w` (w = page is dirtied on unpin)."""
    ops = []
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if parts:
                ops.append((int(parts[0]), len(parts) > 1 and parts[1].lower() in ("w", "write")))
    disk = NullDisk(max((p for p, _ in ops), default=-1) + 1)
    pool = BufferPool(disk, frames, policy)
    for pid, w in ops:
        pool.fetch_page(pid)
        pool.unpin_page(pid, w)
    pool.flush_all()
    s = pool.stats()
    s["hit_rate"] = round(s["hits"] / len(ops), 6) if ops else 0.0
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="storage")
    ap.add_argument("--db", default="db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-table"); c.add_argument("--name", required=True); c.add_argument("--schema", required=True)
    l = sub.add_parser("load"); l.add_argument("--table", required=True); l.add_argument("--file", required=True)
    l.add_argument("--header", action="store_true")
    s = sub.add_parser("scan"); s.add_argument("--table", required=True); s.add_argument("--limit", type=int)
    y = sub.add_parser("layout"); y.add_argument("--table", required=True); y.add_argument("--page", type=int, required=True)
    t = sub.add_parser("trace"); t.add_argument("--file", required=True)
    for p in (c, l, s, y, t):
        p.add_argument("--frames", type=int, default=16)
        p.add_argument("--policy", choices=["lru", "clock"], default="lru")
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "trace":
        out = run_trace(a.file, a.frames, a.policy)
        print(json.dumps(out) if a.json else out)
        return 0

    db = Database(a.db, a.frames, a.policy)
    try:
        out = {}
        if a.cmd == "create-table":
            db.catalog.create_table(a.name, a.schema)
            out = {"created": a.name}
        elif a.cmd == "load":
            n = db.heap(a.table).load_csv(a.file, a.header)
            out = {"loaded": n}
        elif a.cmd == "scan":
            h = db.heap(a.table)
            rows = []
            for _, row in h.scan():
                rows.append(row)
                if a.limit and len(rows) >= a.limit:
                    break
            out = {"rows": rows if a.json else None}
            if not a.json:
                for r in rows:
                    print(r)
        elif a.cmd == "layout":
            f = db.pool.fetch_page(a.page)
            p = SlottedPage(f.data)
            lay = {"page": a.page, "num_slots": p.num_slots, "free_end": p.free_end,
                   "next_page": p.next_page, "slot_dir_bytes": f"{HEADER}..{HEADER + SLOT * p.num_slots - 1}",
                   "free_space": p.free_space(),
                   "header_bytes": "0..7 (num_slots u16, free_end u16, next_page i32)", "slots": []}
            for i in range(p.num_slots):
                off, ln = p.slot(i)
                lay["slots"].append({"slot": i, "dir_byte": HEADER + SLOT * i, "offset": off, "length": ln,
                                     "end": (off + ln - 1) if off else None, "deleted": off == 0})
            db.pool.unpin_page(a.page, False)
            if not a.json:
                print({k: v for k, v in lay.items() if k != "slots"})
                for s_ in lay["slots"]:
                    print(s_)
            out = lay if a.json else {}
        out["stats"] = db.pool.stats()
        out["table"] = None
        if a.json:
            out.pop("table")
            print(json.dumps(out))
        else:
            print("stats:", out["stats"])
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
