"""sql command line tool (M3 spec Section 8).

    python -m sqlfront parse   --query "SELECT ..." --explain     indented syntax tree, no catalog
    python -m sqlfront compile --db DIR --file q.sql --json       logical plan JSON
    python -m sqlfront compile --db DIR --file q.sql              logical plan as an indented tree
    python -m sqlfront check   --db DIR --file q.sql              errors only, exit code 0 or 1

Wrapper with the spec's name:  python sql.py compile ...
"""
import json
import sys

from storage.cli import need, parse_args
from storage.db import open_db
from storage.errors import StorageError, read_text

from .compiler import catalog_from_db, compile_sql, explain_plan
from .errors import SQLError
from .parser import parse_script
from .sqlast import explain_ast


def _read(opts):
    if "query" in opts:
        return opts["query"]
    path = need(opts, "file")
    try:
        return read_text(path)
    except OSError:
        raise StorageError(f"cannot open '{path}'")


def main(argv):
    try:
        cmd, opts = parse_args(argv, flags=("json", "explain"))
        src = _read(opts)
        if cmd == "parse":
            for st in parse_script(src):
                print("\n".join(explain_ast(st)))
            return 0
        if cmd not in ("compile", "check"):
            raise StorageError(f"unknown command '{cmd}' (parse, compile or check)")
        db = open_db(opts.get("db", "db"), frames=8)
        try:
            plans = compile_sql(src, catalog_from_db(db))
        finally:
            db.close()
        if cmd == "check":
            return 0
        if opts.get("json"):
            print(json.dumps(plans[0] if len(plans) == 1 else plans, indent=2))
        else:
            for p in plans:
                print("\n".join(explain_plan(p)))
        return 0
    except SQLError as e:
        print(e.render(), file=sys.stderr)
        return 1
    except StorageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
