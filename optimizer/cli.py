"""opt command line tool (M4 spec Section 6).

    python -m optimizer analyze  --db DIR --table R [--buckets 20]
    python -m optimizer plan     --db DIR --logical q.json --frames 32 --json
    python -m optimizer explain  --db DIR --logical q.json --frames 32 [--rewrites]
    python -m optimizer dp-table --db DIR --logical q.json --frames 32

Extras: --sql q.sql instead of --logical (compiled with Module 3 first);
--disable 2,6 turns rewrite rules off; --naive prints the unoptimized plan.
Wrapper with the spec's name:  python opt.py explain ...
"""
import json
import sys

from ralang.errors import RAError
from sqlfront.compiler import catalog_from_db, compile_sql
from sqlfront.errors import SQLError
from storage.cli import as_int, need, parse_args
from storage.db import open_db
from storage.errors import StorageError, read_text

from .estimate import Stats
from .logical import OptError
from .optimize import explain_logical, naive_physical, optimize
from .stats import analyze


def _logical(opts, db):
    if "sql" in opts:
        try:
            text = read_text(opts["sql"])
        except OSError:
            raise StorageError(f"cannot open '{opts['sql']}'")
        plans = [p for p in compile_sql(text, catalog_from_db(db))
                 if p["op"] not in ("create_table", "insert")]
        if not plans:
            raise StorageError("the SQL file holds no query")
        return plans[0]
    path = need(opts, "logical")
    try:
        return json.loads(read_text(path))
    except OSError:
        raise StorageError(f"cannot open '{path}'")
    except json.JSONDecodeError as e:
        raise StorageError(f"{path}: malformed JSON at line {e.lineno}, column {e.colno}")


def _disabled(opts):
    if "disable" not in opts:
        return ()
    return tuple(int(x) for x in opts["disable"].split(",") if x.strip())


def dp_table_lines(log):
    lines = [f"{'subset':<22} {'cost':>10}  plan considered"]
    for S, rows in log:
        name = "{" + ",".join(S) + "}"
        if rows is None:
            lines.append(f"{name:<22} {'-':>10}  no predicate inside this subset, skipped")
            continue
        if not rows:
            lines.append(f"{name:<22} {'-':>10}  no plan")
            continue
        for cost, desc, best in sorted(rows, key=lambda r: (not r[2], r[0])):
            lines.append(f"{name if best else '':<22} {int(round(cost)):>10}  {'* ' if best else '  '}{desc}")
    return lines


def main(argv):
    try:
        cmd, opts = parse_args(argv, flags=("json", "rewrites", "naive"))
        if cmd not in ("analyze", "plan", "explain", "dp-table"):
            raise StorageError(f"unknown command '{cmd}' (analyze, plan, explain, dp-table)")
        B = as_int(opts, "frames", 32, lo=3)
        db = open_db(opts.get("db", "db"), frames=16 if cmd == "analyze" else 8)
        try:
            if cmd == "analyze":
                st = analyze(db, need(opts, "table"), as_int(opts, "buckets", 20, lo=1))
                print(f"analyzed {st['table']}: {st['n_tuples']} tuples, {st['n_pages']} pages, "
                      f"width {st['width']}")
                for c, cs in st["columns"].items():
                    print(f"  {c}: ndv={cs['ndv']} min={cs['min']} max={cs['max']} buckets={len(cs['hist'])}")
                return 0
            logical = _logical(opts, db)
            stats = Stats(db)
            if opts.get("naive"):
                print(json.dumps(naive_physical(logical), indent=2))
                return 0
            log = [] if cmd == "dp-table" else None
            rewritten, root = optimize(logical, stats, B, _disabled(opts), log)
            if cmd == "plan":
                print(json.dumps(root.json(), indent=2 if not opts.get("json") else None))
            elif cmd == "explain":
                if opts.get("rewrites"):
                    print("-- logical plan before rewrites")
                    print("\n".join(explain_logical(logical, stats)))
                    print("-- after rewrites")
                    print("\n".join(explain_logical(rewritten, stats)))
                    print("-- physical plan")
                print("\n".join(root.explain()))
            else:
                print("\n".join(dp_table_lines(log)))
        finally:
            db.close()
        return 0
    except SQLError as e:
        print(e.render(), file=sys.stderr)
        return 1
    except RAError as e:
        print(f"error: {e.kind}: {e.message}", file=sys.stderr)
        return 1
    except (StorageError, OptError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
