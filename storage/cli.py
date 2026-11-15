"""storage command line tool (M1 spec Section 7).

    python -m storage create-table --db DIR --name R --schema "a:INT,b:VARCHAR(20),c:INT"
    python -m storage load         --db DIR --table R --file r.csv
    python -m storage scan         --db DIR --table R --frames 8 [--limit N] [--policy lru|clock] --json
    python -m storage layout       --db DIR --table R --page 3 [--hex]
    python -m storage trace        [--db DIR] --file trace.txt --frames 16 --policy lru|clock --json
    python -m storage tables       --db DIR
    python -m storage page-demo                          (reproduces spec Section 3.2)

Errors print one line, `error: ...`, and exit 1. Never a stack trace.
"""
import json
import sys

from .bufferpool import BufferPool
from .db import Database, open_db
from .disk import MemoryDisk
from .errors import StorageError, read_text
from .page import DirectoryPage, PAGE_SIZE, SlottedPage
from .record import parse_schema, schema_json


def parse_args(argv, flags=("json", "hex")):
    if not argv:
        raise StorageError("missing command")
    cmd, opts, i = argv[0], {}, 1
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--"):
            raise StorageError(f"unexpected argument '{a}'")
        key = a[2:]
        if key in flags:
            opts[key] = True
            i += 1
        else:
            if i + 1 >= len(argv):
                raise StorageError(f"--{key} needs a value")
            opts[key] = argv[i + 1]
            i += 2
    # the handout writes the database folder as --catalog db/ for sql and opt
    if "catalog" in opts and "db" not in opts:
        opts["db"] = opts.pop("catalog")
    return cmd, opts


def need(opts, key):
    if key not in opts:
        raise StorageError(f"missing --{key}")
    return opts[key]


def as_int(opts, key, default=None, lo=None):
    if key not in opts:
        if default is None:
            raise StorageError(f"missing --{key}")
        return default
    v = opts[key]
    if not v.lstrip("-").isdigit():
        raise StorageError(f"--{key} must be an integer, got '{v}'")
    v = int(v)
    if lo is not None and v < lo:
        raise StorageError(f"--{key} must be at least {lo}")
    return v


def cmd_create(opts):
    db = Database(opts.get("db", "db"))
    try:
        cols = parse_schema(need(opts, "schema"))
        db.create_table(need(opts, "name"), cols)
    finally:
        db.close()
    print(f"created table {opts['name']}")


def cmd_load(opts):
    db = open_db(opts.get("db", "db"))
    try:
        n = db.load_csv(need(opts, "table"), need(opts, "file"))
        info = db.catalog.get_table(opts["table"])
    finally:
        db.close()
    print(f"loaded {n} rows into {opts['table']} ({info['num_tuples']} tuples, {info['num_pages']} data pages)")


def cmd_scan(opts):
    frames = as_int(opts, "frames", 8, lo=2)
    limit = as_int(opts, "limit", -1)
    db = open_db(opts.get("db", "db"), frames, opts.get("policy", "lru"))
    try:
        table = need(opts, "table")
        info = db.catalog.get_table(table)
        # Opening the database read the catalog through the pool. Those reads are
        # not part of the scan, so the counters start here. (DESIGN_M1.md 6.)
        catalog_reads = db.pool.reads
        db.pool.reset_stats()
        rows = []
        gen = db.heap(table).scan()
        for _, row in gen:
            if limit >= 0 and len(rows) >= limit:
                break
            rows.append(list(row))
        gen.close()
        stats = db.pool.stats()
        stats["max_pinned"] = db.pool.peak_held
        stats["catalog_reads"] = catalog_reads
    finally:
        db.close()
    if opts.get("json"):
        print(json.dumps({"table": table, "schema": schema_json(info["schema"]),
                          "rows": rows, "stats": stats}))
    else:
        print(" | ".join(c[0] for c in info["schema"]))
        for r in rows:
            print(" | ".join(str(v) for v in r))
        print(f"({len(rows)} rows) {stats}")


def cmd_layout(opts):
    db = open_db(opts.get("db", "db"))
    try:
        table = need(opts, "table")
        pid = as_int(opts, "page", lo=0)
        disk = db.table_disk(table)
        if pid >= disk.num_pages():
            raise StorageError(f"table {table} has pages 0..{disk.num_pages() - 1}, no page {pid}")
        dirs = db.directory_pages(table)
        fr = db.pool.fetch_page(disk, pid)
        try:
            if pid in dirs:
                print(DirectoryPage(fr.data).layout())
            else:
                print(SlottedPage(fr.data).layout(opts.get("hex", False)))
        finally:
            db.pool.unpin_page(disk, pid, False)
    finally:
        db.close()


def run_trace(page_ids, frames, policy):
    """The graded buffer-pool experiment: fetch each id, unpin it immediately, clean."""
    pool = BufferPool(frames, policy)
    disk = MemoryDisk()
    for pid in page_ids:
        pool.fetch_page(disk, pid)
        pool.unpin_page(disk, pid, False)
    out = {"policy": policy, "frames": frames}
    out.update(pool.stats())
    return out


def read_trace(path):
    ids = []
    try:
        text = read_text(path)
    except OSError:
        raise StorageError(f"cannot open trace file '{path}'")
    if True:
        for lineno, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            tok = s.split()[0]
            if not tok.isdigit():
                raise StorageError(f"{path} line {lineno}: '{tok}' is not a page id")
            ids.append(int(tok))
    return ids


def cmd_trace(opts):
    frames = as_int(opts, "frames", lo=1)
    policy = opts.get("policy", "lru")
    if policy not in ("lru", "clock"):
        raise StorageError("--policy must be lru or clock")
    out = run_trace(read_trace(need(opts, "file")), frames, policy)
    if opts.get("json"):
        print(json.dumps(out))
    else:
        total = out["hits"] + out["misses"]
        rate = out["hits"] / total if total else 0.0
        print(f"{policy} B={frames}: {out['hits']} hits, {out['misses']} misses, "
              f"{out['evictions']} evictions, hit rate {rate:.4f}")


def cmd_tables(opts):
    db = open_db(opts.get("db", "db"))
    try:
        for t in db.catalog.list_tables():
            i = db.catalog.get_table(t)
            print(f"{t}  {i['num_tuples']} tuples  {i['num_pages']} data pages  {i['schema']}")
    finally:
        db.close()


def cmd_page_demo(opts):
    """Spec 3.2, step by step, on a page in memory."""
    page = SlottedPage.init(bytearray(PAGE_SIZE), 0)
    print(f"{'after':<16}{'slot_count':>11}{'free_end':>10}{'free space':>12}  slot directory")

    def row(label, last):
        print(f"{label:<16}{page.slot_count:>11}{page.free_end:>10}{page.free_space():>12}  {last}")
    row("empty page", "empty")
    for n in (13, 20, 13):
        s = page.insert(bytes(n))
        row(f"insert {n} bytes", f"{s}:{page.slot(s)}")
    page.delete(1)
    row("delete slot 1", f"1:{page.slot(1)}")


COMMANDS = {"create-table": cmd_create, "load": cmd_load, "scan": cmd_scan,
            "layout": cmd_layout, "trace": cmd_trace, "tables": cmd_tables,
            "page-demo": cmd_page_demo}


def main(argv):
    try:
        cmd, opts = parse_args(argv)
        if cmd not in COMMANDS:
            raise StorageError(f"unknown command '{cmd}' (one of: {', '.join(COMMANDS)})")
        COMMANDS[cmd](opts)
        return 0
    except StorageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
