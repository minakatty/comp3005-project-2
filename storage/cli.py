"""storage create-table | load | scan | layout | trace   (all take --json; errors print `error: ...`, exit 1)"""
import argparse
import json
import sys

from .bufferpool import BufferPool
from .db import Database
from .disk import NullDisk
from .errors import StorageError
from .page import DIR_HEADER, DIR_ENTRY, HEADER, SLOT, DirPage, SlottedPage


def run_trace(path, frames, policy):
    """One page id per line. Each is fetched and unpinned immediately as clean. The disk is a
    counting stand-in, so only the replacement policy is measured."""
    pool = BufferPool(NullDisk(), frames, policy)
    refs = 0
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            pid = int(line.split()[0])
            pool.fetch_page(pid)
            pool.unpin_page(pid, False)
            refs += 1
    s = pool.stats()
    out = {"policy": policy, "frames": frames, **s}
    out["hit_rate"] = round(s["hits"] / refs, 6) if refs else 0.0
    return out


def _schema_json(cols):
    return [[n, "INT"] if t == "INT" else [n, "VARCHAR", m] for n, t, m in cols]


def _layout(db, table, page_no):
    h = db.heap(table)
    meta = db.catalog.get_table(table)
    if not 0 <= page_no < meta["file_pages"]:
        raise StorageError(f"table {table} has {meta['file_pages']} pages (0..{meta['file_pages'] - 1}); no page {page_no}")
    dirs = h.directory_pages()
    f = db.pool.fetch_page(h._g(page_no))
    try:
        raw = bytes(f.data)
        if page_no in dirs:
            d = DirPage(f.data)
            out = {"table": table, "page": page_no, "kind": "directory", "page_id": d.page_id,
                   "next_dir_page": d.next_dir if d.next_dir != 0xFFFFFFFF else None,
                   "entry_count": d.entry_count,
                   "entries": [{"entry": i, "byte": DIR_HEADER + DIR_ENTRY * i,
                                "data_page_id": d.entry(i)[0], "free_bytes": d.entry(i)[1]}
                               for i in range(d.entry_count)]}
        else:
            p = SlottedPage(f.data)
            slots = []
            for i in range(p.slot_count):
                off, ln = p.slot(i)
                slots.append({"slot": i, "dir_byte": HEADER + SLOT * i, "offset": off, "length": ln,
                              "last_byte": (off + ln - 1) if off else None, "dead": off == 0})
            out = {"table": table, "page": page_no, "kind": "data", "page_id": p.page_id,
                   "slot_count": p.slot_count, "free_end": p.free_end, "free_space": p.free_space(),
                   "slot_directory_bytes": f"{HEADER}..{HEADER + SLOT * p.slot_count - 1}" if p.slot_count else "empty",
                   "slots": slots}
    finally:
        db.pool.unpin_page(h._g(page_no), False)
    return out, raw


def _print_layout(out, raw, hexdump):
    if out["kind"] == "directory":
        print(f"table {out['table']} page {out['page']}: DIRECTORY page")
        print(f"  page_id={out['page_id']}  next_dir_page={out['next_dir_page']}  entry_count={out['entry_count']}")
        for e in out["entries"]:
            print(f"  entry {e['entry']:>3} @byte {e['byte']:>4}: data_page_id={e['data_page_id']} free_bytes={e['free_bytes']}")
    else:
        print(f"table {out['table']} page {out['page']}: DATA page")
        print(f"  page_id={out['page_id']}  slot_count={out['slot_count']}  free_end={out['free_end']}  free_space={out['free_space']}")
        print(f"  slot directory occupies bytes {out['slot_directory_bytes']}")
        for s in out["slots"]:
            if s["dead"]:
                print(f"  slot {s['slot']:>3} @byte {s['dir_byte']:>4}: DEAD (0,0)")
            else:
                print(f"  slot {s['slot']:>3} @byte {s['dir_byte']:>4}: offset={s['offset']} length={s['length']} (bytes {s['offset']}..{s['last_byte']})")
    if hexdump:
        print("  raw bytes:")
        for i in range(0, 4096, 16):
            chunk = raw[i:i + 16]
            if any(chunk):
                print(f"  {i:04x}  " + " ".join(f"{b:02x}" for b in chunk))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="storage")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, frames=True, db=True):
        if db:
            p.add_argument("--db", default="db", help="database directory (default: db)")
        if frames:
            p.add_argument("--frames", type=int, default=16)
            p.add_argument("--policy", choices=["lru", "clock"], default="lru")
        p.add_argument("--json", action="store_true")

    c = sub.add_parser("create-table"); c.add_argument("--name", required=True); c.add_argument("--schema", required=True); common(c)
    l = sub.add_parser("load"); l.add_argument("--table", required=True); l.add_argument("--file", required=True); common(l)
    s = sub.add_parser("scan"); s.add_argument("--table", required=True); s.add_argument("--limit", type=int); common(s)
    y = sub.add_parser("layout"); y.add_argument("--table", required=True); y.add_argument("--page", type=int, required=True)
    y.add_argument("--hex", action="store_true", help="also dump the non-zero bytes of the page"); common(y)
    t = sub.add_parser("trace"); t.add_argument("--file", required=True); common(t)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "trace":
            out = run_trace(a.file, a.frames, a.policy)
            print(json.dumps(out) if a.json else "\n".join(f"{k}: {v}" for k, v in out.items()))
            return 0
        with Database(a.db, a.frames, a.policy) as db:
            if a.cmd == "create-table":
                db.catalog.create_table(a.name, a.schema)
                print(json.dumps({"created": a.name, "schema": a.schema}) if a.json else f"created table {a.name}")
            elif a.cmd == "load":
                n = db.heap(a.table).load_csv(a.file)
                out = {"table": a.table, "loaded": n, "stats": db.pool.stats()}
                print(json.dumps(out) if a.json else f"loaded {n} rows into {a.table}  {out['stats']}")
            elif a.cmd == "scan":
                h = db.heap(a.table)
                rows = []
                for _, row in h.scan():
                    rows.append(list(row))
                    if a.limit is not None and len(rows) >= a.limit:
                        break
                cols = db.catalog.get_table(a.table)["schema"]
                out = {"table": a.table, "schema": _schema_json(cols), "rows": rows, "stats": db.pool.stats()}
                if a.json:
                    print(json.dumps(out))
                else:
                    print(" | ".join(c[0] for c in cols))
                    for r in rows:
                        print(" | ".join(str(v) for v in r))
                    print(f"({len(rows)} rows)  stats: {out['stats']}")
            elif a.cmd == "layout":
                out, raw = _layout(db, a.table, a.page)
                if a.json:
                    print(json.dumps(out))
                else:
                    _print_layout(out, raw, a.hex)
        return 0
    except (StorageError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
