"""Glue: SQL text -> list of logical plans, against a Module 1 catalog. Prints nothing."""
from storage.errors import StorageError

from .parser import parse_script
from .translate import Catalog, Translator


def catalog_from_db(db):
    def lookup(name):
        if not db.catalog.has_table(name):
            return None
        return [tuple(c) for c in db.catalog.get_table(name)["schema"]]
    return Catalog(lookup)


def catalog_from_dict(tables):
    """tables: {name: [(col, type, len), ...]} -- used by tests and by sql parse."""
    return Catalog(lambda n: tables.get(n))


def compile_sql(src, catalog):
    tr = Translator(catalog)
    return [tr.statement(st) for st in parse_script(src)]


def explain_plan(plan, depth=0):
    """Indented text of a logical plan. A union and a union_all differ visibly."""
    pad = "  " * depth
    op = plan["op"]
    if op == "scan":
        head = f"scan {plan['table']}"
    elif op == "rename":
        head = f"rename to {plan['to']}"
    elif op in ("select", "join"):
        head = f"{op} ({plan['condition']})"
    elif op == "project":
        cols = plan["columns"]
        if "as" in plan:
            cols = [c if a is None else f"{c} AS {a}" for c, a in zip(cols, plan["as"])]
        head = f"project [{', '.join(cols)}]"
    elif op == "group_by":
        aggs = ", ".join(f"{fn}({arg}) AS {nm}" for fn, arg, nm in plan["aggregates"])
        head = f"group_by keys [{', '.join(plan['keys'])}] aggregates [{aggs}]"
    elif op == "sort":
        head = "sort [" + ", ".join(f"{c} {d}" for c, d in plan["keys"]) + "]"
    elif op == "limit":
        head = f"limit {plan['n']}"
    elif op == "create_table":
        return [f"{pad}create_table {plan['table']} {plan['columns']}"]
    elif op == "insert":
        return [f"{pad}insert into {plan['table']} values {plan['values']}"]
    else:
        head = op
    lines = [pad + head]
    for k in ("child", "left", "right"):
        if k in plan:
            lines += explain_plan(plan[k], depth + 1)
    return lines


__all__ = ["compile_sql", "catalog_from_db", "catalog_from_dict", "explain_plan", "StorageError"]
