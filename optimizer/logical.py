"""Logical plans: output columns, estimates, and the rewrite rules (M4 spec Section 3).

A logical plan is the JSON Module 3 produces. `props(node)` computes, bottom up,
each node's columns and its Est (estimate.py). The rewrite rules are pure
functions plan -> plan; `rewrite(plan, disabled)` runs them to a fixed point.

    1  split conjunctions          select[p and q] -> select[p] over select[q]
    2  push select below join / cross   when it references one side only
    3  cross + select -> join      the select is an equality referencing both sides
    4  push select below rename (subquery alias), union, union_all, and (extension)
       below a project, rewriting column names through it
    5  push select below group_by  only if it references grouping columns alone
    6  push projections down       above each join input, keep only the columns the
                                   ancestors and the join predicates still need
    7  cleanup                     remove a project that keeps every column in order,
                                   collapse adjacent selects into one

A rename directly over a scan is an access path (an aliased seqscan) and is
treated as a leaf: selects stop above it, as in the spec's worked example.
"""
import math

from ralang import ast_nodes as ra
from ralang.condition import and_all, attrs_in, conjuncts, resolve_column
from ralang.parser import parse_condition
from ralang.plan_json import unparse_condition

from .estimate import Est


class OptError(Exception):
    pass


# --------------------------------------------------------------------------
# Columns and estimates
# --------------------------------------------------------------------------

def props(node, est, memo=None):
    """Est of a logical node. `est` is an Estimator. memo: id(node) -> Est."""
    memo = {} if memo is None else memo
    key = id(node)
    if key in memo:
        return memo[key]
    op = node["op"]
    S = est.stats
    if op == "scan":
        t = node["table"]
        ts = S.table(t)
        cols = [(f"{t}.{n}", ty, ln) for n, ty, ln in S.schema(t)]
        lin = {c[0]: (t, c[0].split(".", 1)[1]) for c in cols}
        e = Est(ts["n_tuples"], ts["width"] or 1, cols, lin)
        e.rows = max(1, ts["n_tuples"])
        e.base_pages = max(1, ts["n_pages"])
        e.fixed_pages = e.base_pages
    elif op == "rename":
        c = props(node["child"], est, memo)
        to = node["to"]
        cols = [(f"{to}.{x[0].rsplit('.', 1)[-1]}",) + tuple(x[1:]) for x in c.cols]
        lin = {n[0]: c.lineage.get(o[0]) for n, o in zip(cols, c.cols)}
        ndv = {n[0]: c.ndv[o[0]] for n, o in zip(cols, c.cols) if o[0] in c.ndv}
        e = Est(c.rows, c.width, cols, lin, ndv)
        if hasattr(c, "base_pages"):
            e.base_pages = c.base_pages
            e.fixed_pages = c.fixed_pages
    elif op == "select":
        c = props(node["child"], est, memo)
        sel = est.selectivity(parse_condition(node["condition"]), c)
        e = Est(c.rows * sel, c.width, c.cols, c.lineage, c.ndv)
    elif op == "project":
        c = props(node["child"], est, memo)
        names = node.get("as") or [None] * len(node["columns"])
        idx = [resolve_column(c.cols, *_split(x)) for x in node["columns"]]
        cols = [((nm or c.cols[i][0]),) + tuple(c.cols[i][1:]) for i, nm in zip(idx, names)]
        lin = {n[0]: c.lineage.get(c.cols[i][0]) for n, i in zip(cols, idx)}
        ndv = {n[0]: c.ndv[c.cols[i][0]] for n, i in zip(cols, idx) if c.cols[i][0] in c.ndv}
        width = est.width_of(c, [c.cols[i][0] for i in idx])
        if getattr(c, "width_scale", None):
            width *= c.width_scale
        e = Est(c.rows, width, cols, lin, ndv)
    elif op in ("join", "cross"):
        l, r = props(node["left"], est, memo), props(node["right"], est, memo)
        both = Est(1, l.width + r.width, l.cols + r.cols, {**l.lineage, **r.lineage}, {**l.ndv, **r.ndv})
        both.rows = l.rows * r.rows
        sel = est.selectivity(parse_condition(node["condition"]), both) if op == "join" else 1.0
        e = Est(l.rows * r.rows * sel, l.width + r.width, both.cols, both.lineage, both.ndv)
    elif op == "group_by":
        c = props(node["child"], est, memo)
        keys = [c.cols[resolve_column(c.cols, *_split(k))] for k in node["keys"]]
        groups = 1
        for k in keys:
            groups *= est.ndv(c, k[0]) or max(1, int(c.rows ** 0.5))
        rows = min(c.rows, groups) if keys else 1
        cols = list(keys)
        for fn, arg, name in node["aggregates"]:
            if fn in ("count", "sum", "avg"):
                cols.append((name, "INT", 0))
            else:
                src = c.cols[resolve_column(c.cols, *_split(arg))]
                cols.append((name,) + tuple(src[1:]))
        lin = {k[0]: c.lineage.get(k[0]) for k in keys}
        width = est.width_of(c, [k[0] for k in keys]) + 8 * len(node["aggregates"])
        ndv = {k[0]: est.ndv(c, k[0]) for k in keys if est.ndv(c, k[0])}
        e = Est(rows, width, cols, lin, ndv)
    elif op == "distinct":
        c = props(node["child"], est, memo)
        prod = 1
        for col in c.cols:
            prod *= est.ndv(c, col[0]) or c.rows
            if prod >= c.rows:
                break
        e = Est(min(c.rows, prod), c.width, c.cols, c.lineage, c.ndv)
    elif op in ("sort",):
        c = props(node["child"], est, memo)
        e = Est(c.rows, c.width, c.cols, c.lineage, c.ndv)
    elif op == "limit":
        c = props(node["child"], est, memo)
        e = Est(min(c.rows, max(1, node["n"])), c.width, c.cols, c.lineage, c.ndv)
    elif op in ("union", "union_all", "intersect", "except"):
        l, r = props(node["left"], est, memo), props(node["right"], est, memo)
        rows = {"union_all": l.rows + r.rows, "union": l.rows + r.rows,
                "intersect": min(l.rows, r.rows), "except": l.rows}[op]
        e = Est(rows, max(l.width, r.width), l.cols, l.lineage, l.ndv)
    else:
        raise OptError(f"logical plan: unknown operator '{op}'")
    memo[key] = e
    return e


def _split(text):
    if "." in text:
        a, b = text.rsplit(".", 1)
        return a, b
    return None, text


def cols_of(node, est):
    return [c[0] for c in props(node, est).cols]


# --------------------------------------------------------------------------
# Condition helpers
# --------------------------------------------------------------------------

def _refs_side(cond_tree, cols):
    """True if every attribute in the condition resolves in `cols`."""
    try:
        for a in attrs_in(cond_tree):
            resolve_column(cols, a.relation, a.name)
        return True
    except Exception:
        return False


def _rename_attrs(tree, mapping):
    """Copy of a condition tree with attributes renamed by `mapping`
    {old qualified name: new name}."""
    if isinstance(tree, ra.Attr):
        new = mapping[tree.qualified()]
        rel, name = _split(new)
        return ra.Attr(rel, name)
    if isinstance(tree, ra.Compare):
        return ra.Compare(tree.op, _rename_attrs(tree.left, mapping), _rename_attrs(tree.right, mapping))
    if isinstance(tree, (ra.And, ra.Or)):
        return type(tree)(_rename_attrs(tree.left, mapping), _rename_attrs(tree.right, mapping))
    if isinstance(tree, ra.Not):
        return ra.Not(_rename_attrs(tree.inner, mapping))
    return tree


def _qualify(tree, cols):
    """Rewrite every attribute to the exact column name it resolves to."""
    mapping = {}
    for a in attrs_in(tree):
        mapping[a.qualified()] = cols[resolve_column(cols, a.relation, a.name)][0]
    return _rename_attrs(tree, mapping)


def is_equi_between(tree, lcols, rcols):
    if not (isinstance(tree, ra.Compare) and tree.op == "EQ"
            and isinstance(tree.left, ra.Attr) and isinstance(tree.right, ra.Attr)):
        return False
    a, b = tree.left, tree.right
    return ((_refs_side(a, lcols) and _refs_side(b, rcols)) or
            (_refs_side(a, rcols) and _refs_side(b, lcols)))


def _cols(node, est):
    return props(node, est).cols


# --------------------------------------------------------------------------
# Rewrite rules
# --------------------------------------------------------------------------

def _map(node, fn):
    """Apply fn to every child, return a copy of node."""
    out = dict(node)
    for k in ("child", "left", "right"):
        if k in node:
            out[k] = fn(node[k])
    return out


def rule1_split(node, est):
    node = _map(node, lambda c: rule1_split(c, est))
    if node["op"] == "select":
        parts = conjuncts(parse_condition(node["condition"]))
        if len(parts) > 1:
            child = node["child"]
            for p in reversed(parts):
                child = {"op": "select", "condition": unparse_condition(p), "child": child}
            return child
    return node


def _is_access_path(node):
    return node["op"] == "scan" or (node["op"] == "rename" and node["child"]["op"] == "scan")


def push_selects(node, est, enabled):
    """Rules 2, 4 and 5, applied top-down repeatedly."""
    node = _map(node, lambda c: push_selects(c, est, enabled))
    if node["op"] != "select":
        return node
    tree = parse_condition(node["condition"])
    child = node["child"]
    op = child["op"]
    if op in ("join", "cross") and 2 in enabled:
        for side in ("left", "right"):
            cols = _cols(child[side], est)
            if _refs_side(tree, cols):
                new = dict(child)
                new[side] = push_selects({"op": "select", "condition": node["condition"],
                                          "child": child[side]}, est, enabled)
                return new
    if op == "rename" and not _is_access_path(child) and 4 in enabled:
        inner = _cols(child["child"], est)
        outer = _cols(child, est)
        mapping = {o[0]: i[0] for o, i in zip(outer, inner)}
        q = _qualify(tree, outer)
        moved = unparse_condition(_rename_attrs(q, mapping))
        return dict(child, child=push_selects({"op": "select", "condition": moved,
                                               "child": child["child"]}, est, enabled))
    if op == "project" and 4 in enabled:
        inner = _cols(child["child"], est)
        outer = _cols(child, est)
        src = [inner[resolve_column(inner, *_split(c))][0] for c in child["columns"]]
        mapping = {o[0]: s for o, s in zip(outer, src)}
        try:
            q = _qualify(tree, outer)
            moved = unparse_condition(_rename_attrs(q, mapping))
        except Exception:
            return node
        return dict(child, child=push_selects({"op": "select", "condition": moved,
                                               "child": child["child"]}, est, enabled))
    if op in ("union", "union_all") and 4 in enabled:
        lc, rc = _cols(child["left"], est), _cols(child["right"], est)
        q = _qualify(tree, lc)
        right_cond = unparse_condition(_rename_attrs(q, {a[0]: b[0] for a, b in zip(lc, rc)}))
        return dict(child,
                    left=push_selects({"op": "select", "condition": unparse_condition(q),
                                       "child": child["left"]}, est, enabled),
                    right=push_selects({"op": "select", "condition": right_cond,
                                        "child": child["right"]}, est, enabled))
    if op == "select":
        # selects commute: try to move this one below the select under it
        alone = {"op": "select", "condition": node["condition"], "child": child["child"]}
        moved = push_selects(alone, est, enabled)
        if moved != alone:
            return {"op": "select", "condition": child["condition"], "child": moved}
        return node
    if op == "group_by" and 5 in enabled:
        keys = [c for c in _cols(child, est) if c[0] in child["keys"]]
        if keys and _refs_side(tree, keys):
            return dict(child, child=push_selects({"op": "select", "condition": node["condition"],
                                                   "child": child["child"]}, est, enabled))
    return node


def rule3_cross_to_join(node, est):
    """A stack of selects directly over a cross: the first equality that references
    both sides becomes the join condition; the other selects stay where they were."""
    node = _map(node, lambda c: rule3_cross_to_join(c, est))
    if node["op"] != "select":
        return node
    stack, cur = [], node
    while cur["op"] == "select":
        stack.append(cur["condition"])
        cur = cur["child"]
    if cur["op"] != "cross":
        return node
    lc, rc = _cols(cur["left"], est), _cols(cur["right"], est)
    for i, c in enumerate(stack):
        if is_equi_between(parse_condition(c), lc, rc):
            out = {"op": "join", "condition": c, "left": cur["left"], "right": cur["right"]}
            for c2 in reversed(stack[:i] + stack[i + 1:]):
                out = {"op": "select", "condition": c2, "child": out}
            return out
    return node


def _in_block(node):
    while node["op"] == "select":
        node = node["child"]
    return node["op"] in ("join", "cross")


def _cond_cols(node, est):
    tree = _qualify(parse_condition(node["condition"]), _cols(node, est))
    return {a.qualified() for a in attrs_in(tree)}


def rule6_project_down(node, est, needed=None):
    """Insert a projection above each join input keeping only the columns its
    ancestors need plus those the join and select predicates above it need.
    Never between two joins, so a join block stays one block for enumeration."""
    op = node["op"]
    if needed is None:
        needed = {c[0] for c in _cols(node, est)}
    if op == "select":
        return dict(node, child=rule6_project_down(node["child"], est, needed | _cond_cols(node, est)))
    if op in ("join", "cross"):
        need = needed | (_cond_cols(node, est) if op == "join" else set())
        out = dict(node)
        for side in ("left", "right"):
            child = node[side]
            ccols = [c[0] for c in _cols(child, est)]
            keep = [c for c in ccols if c in need]
            sub = rule6_project_down(child, est, set(keep))
            if not _in_block(child) and keep and len(keep) < len(ccols):
                sub = {"op": "project", "columns": keep, "child": sub}
            out[side] = sub
        return out
    if op == "project":
        ccols = _cols(node["child"], est)
        need = {ccols[resolve_column(ccols, *_split(c))][0] for c in node["columns"]}
        return dict(node, child=rule6_project_down(node["child"], est, need))
    return _map(node, lambda c: rule6_project_down(c, est, None))


def rule7_cleanup(node, est):
    node = _map(node, lambda c: rule7_cleanup(c, est))
    if node["op"] == "project" and "as" not in node:
        ccols = [c[0] for c in _cols(node["child"], est)]
        if node["columns"] == ccols:
            return node["child"]
    if node["op"] == "select" and node["child"]["op"] == "select":
        a = parse_condition(node["condition"])
        b = parse_condition(node["child"]["condition"])
        return {"op": "select", "condition": unparse_condition(and_all(conjuncts(b) + conjuncts(a))),
                "child": node["child"]["child"]}
    return node


ALL_RULES = (1, 2, 3, 4, 5, 6, 7)


def rewrite(plan, est, disabled=()):
    enabled = set(ALL_RULES) - set(disabled)
    p = plan
    if 1 in enabled:
        p = rule1_split(p, est)
    for _ in range(8):
        before = p
        p = push_selects(p, est, enabled)
        if 3 in enabled:
            p = rule3_cross_to_join(p, est)
        if p == before:
            break
    if 6 in enabled:
        p = rule6_project_down(p, est)
    if 7 in enabled:
        p = rule7_cleanup(p, est)
    return p
