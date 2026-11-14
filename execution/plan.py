"""Physical plan JSON: validation, operator construction and the frame budget.

Validation happens in two steps, both before any page is read:
  1. validate(plan)  checks the JSON against the plan schema below and names the
                     offending node: "Node 'sort': missing required field 'keys'."
  2. build(...)      constructs the operator tree, which resolves every table and
                     column and type-checks every condition. An unknown table or
                     column is therefore rejected "at open, before any execution
                     work begins".

Plan schema (fields marked ? are optional):
    seqscan            table, alias?
    filter             condition, child
    project            columns (non-empty), as?, child
    rename             to, child                          (extension, for subqueries)
    distinct           mode? ("sort"|"hash", default sort), child
    sort               keys: [[col, "asc"|"desc"], ...] (a bare "col" means asc), child
    group_by           mode? (default hash), keys: [col...], aggregates: [[fn, col, name?]...], child
    nested_loop_join   condition? (absent or null = cross product), left, right
    sort_merge_join    condition (equality), left, right
    hash_join          condition (equality), left (build side), right
    union_all, union, intersect, except, intersect_all, except_all   left, right
    limit              n (also accepted: count, limit), child

The budget rule (DESIGN_M2.md 2): every seqscan is pipelined and holds one frame;
every memory-using operator gets an equal share of what is left,
    share = floor((B - number_of_seqscans) / number_of_memory_operators)
If that is 0, nested loop joins run tuple-at-a-time (no block frame) and the other
memory operators take one frame each.
"""
import json

from . import operators as ops
from .errors import PlanError

_STR, _OBJ, _LIST, _INT = "a string", "an object", "a list", "an integer"

UNARY = {"filter", "project", "rename", "distinct", "sort", "group_by", "limit"}
BINARY = {"nested_loop_join", "sort_merge_join", "hash_join", "union_all", "union",
          "intersect", "except", "intersect_all", "except_all"}
LEAF = {"seqscan"}
ALL_OPS = UNARY | BINARY | LEAF


def load_plan_text(text, source="plan"):
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanError(f"{source}: malformed JSON at line {e.lineno}, column {e.colno}: {e.msg}")


def _need(d, op, field, kind, path):
    where = f" (at {path})" if path != "root" else ""
    if field not in d:
        raise PlanError(f"Node '{op}'{where}: missing required field '{field}'.")
    v = d[field]
    ok = {_STR: isinstance(v, str), _OBJ: isinstance(v, dict), _LIST: isinstance(v, list),
          _INT: isinstance(v, int) and not isinstance(v, bool)}[kind]
    if not ok:
        raise PlanError(f"Node '{op}'{where}: field '{field}' must be {kind}.")
    return v


def validate(node, path="root"):
    """Structural check of a whole plan. Raises PlanError naming the node."""
    if not isinstance(node, dict):
        raise PlanError(f"Plan node at {path} must be a JSON object, got {type(node).__name__}.")
    if "op" not in node:
        raise PlanError(f"Plan node at {path}: missing required field 'op'.")
    op = node["op"]
    if not isinstance(op, str):
        raise PlanError(f"Plan node at {path}: field 'op' must be a string.")
    where = f" (at {path})" if path != "root" else ""
    if op not in ALL_OPS:
        raise PlanError(f"Node '{op}'{where}: unknown operator '{op}'.")
    if op == "seqscan":
        _need(node, op, "table", _STR, path)
        if "alias" in node and not isinstance(node["alias"], str):
            raise PlanError(f"Node 'seqscan'{where}: field 'alias' must be a string.")
        return
    if op in UNARY:
        validate(_need(node, op, "child", _OBJ, path), path + ".child")
    else:
        validate(_need(node, op, "left", _OBJ, path), path + ".left")
        validate(_need(node, op, "right", _OBJ, path), path + ".right")
    if op == "filter":
        _need(node, op, "condition", _STR, path)
    elif op == "project":
        cols = _need(node, op, "columns", _LIST, path)
        if not cols or not all(isinstance(c, str) for c in cols):
            raise PlanError(f"Node 'project'{where}: field 'columns' must be a non-empty list of strings.")
        if "as" in node:
            a = node["as"]
            if not isinstance(a, list) or len(a) != len(cols) or \
                    not all(x is None or isinstance(x, str) for x in a):
                raise PlanError(f"Node 'project'{where}: field 'as' must be a list of strings or nulls, "
                                f"one per column.")
    elif op == "rename":
        _need(node, op, "to", _STR, path)
    elif op in ("distinct", "group_by") and "mode" in node:
        if node["mode"] not in ("sort", "hash"):
            raise PlanError(f"Node '{op}'{where}: field 'mode' must be \"sort\" or \"hash\".")
    if op == "sort":
        keys = _need(node, op, "keys", _LIST, path)
        if not keys:
            raise PlanError(f"Node 'sort'{where}: field 'keys' must not be empty.")
        for k in keys:
            if isinstance(k, str):
                continue
            if not (isinstance(k, list) and len(k) == 2 and isinstance(k[0], str)
                    and k[1] in ("asc", "desc")):
                raise PlanError(f"Node 'sort'{where}: each key must be [column, \"asc\"|\"desc\"], got {k!r}.")
    elif op == "group_by":
        keys = _need(node, op, "keys", _LIST, path)
        if not all(isinstance(k, str) for k in keys):
            raise PlanError(f"Node 'group_by'{where}: field 'keys' must be a list of strings.")
        aggs = _need(node, op, "aggregates", _LIST, path)
        for a in aggs:
            if not (isinstance(a, list) and len(a) in (2, 3) and all(isinstance(x, str) for x in a)):
                raise PlanError(f"Node 'group_by'{where}: each aggregate must be [function, column] "
                                f"or [function, column, name], got {a!r}.")
            if a[0].lower() not in ops.AGG_FUNCS:
                raise PlanError(f"Node 'group_by'{where}: unknown aggregate function '{a[0]}'.")
        if not keys and not aggs:
            raise PlanError(f"Node 'group_by'{where}: needs at least one key or aggregate.")
    elif op in ("sort_merge_join", "hash_join"):
        _need(node, op, "condition", _STR, path)
    elif op == "nested_loop_join":
        c = node.get("condition")
        if c is not None and not isinstance(c, str):
            raise PlanError(f"Node 'nested_loop_join'{where}: field 'condition' must be a string or null.")
    elif op == "limit":
        key = next((k for k in ("n", "count", "limit") if k in node), None)
        if key is None:
            raise PlanError(f"Node 'limit'{where}: missing required field 'n'.")
        v = node[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise PlanError(f"Node 'limit'{where}: field '{key}' must be a non-negative integer.")


def build(ctx, node):
    """JSON node -> operator tree (unopened)."""
    op = node["op"]
    if op == "seqscan":
        return ops.SeqScan(ctx, node["table"], node.get("alias"))
    if op in UNARY:
        child = build(ctx, node["child"])
        if op == "filter":
            return ops.Filter(ctx, child, node["condition"])
        if op == "project":
            return ops.Project(ctx, child, node["columns"], node.get("as"))
        if op == "rename":
            return ops.Rename(ctx, child, node["to"])
        if op == "distinct":
            return ops.Distinct(ctx, child, node.get("mode", "sort"))
        if op == "sort":
            keys = [(k, "asc") if isinstance(k, str) else (k[0], k[1]) for k in node["keys"]]
            return ops.Sort(ctx, child, keys)
        if op == "group_by":
            return ops.GroupBy(ctx, child, node["keys"], node["aggregates"], node.get("mode", "hash"))
        if op == "limit":
            return ops.Limit(ctx, child, next(node[k] for k in ("n", "count", "limit") if k in node))
    left, right = build(ctx, node["left"]), build(ctx, node["right"])
    cls = {"nested_loop_join": ops.NestedLoopJoin, "sort_merge_join": ops.SortMergeJoin,
           "hash_join": ops.HashJoin, "union_all": ops.UnionAll, "union": ops.Union,
           "intersect": ops.Intersect, "except": ops.Except, "intersect_all": ops.IntersectAll,
           "except_all": ops.ExceptAll}[op]
    if op.endswith("_join"):
        return cls(ctx, left, right, node.get("condition"))
    return cls(ctx, left, right)


def walk(op, depth=0):
    yield op, depth
    for c in op.children:
        yield from walk(c, depth + 1)


def allocate(root, B):
    nodes = [o for o, _ in walk(root)]
    scans = sum(1 for o in nodes if isinstance(o, ops.SeqScan))
    mem = [o for o in nodes if o.memory]
    if not mem:
        return 0
    share = (B - scans) // len(mem)
    for o in mem:
        # Below one frame each, a nested loop join degrades to tuple-at-a-time (its
        # "block" is the one outer tuple in hand) and every other memory operator
        # gets the single frame it cannot work without.
        o.share = share if share >= 1 else (0 if isinstance(o, ops.NestedLoopJoin) else 1)
    return share
