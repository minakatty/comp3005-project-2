"""Cost model, join enumeration and the logical -> physical translation
(M4 spec Sections 4 and 5).

Cost is page I/O (reads + writes) as a function of B. M and N are estimated page
counts. The formulas are exactly the Module 2 ones:

    seqscan            M
    filter, project    0
    sort               2*M*(passes-1) + M,  passes = 1 + ceil(log(ceil(M/B)) / log(B-1))
    distinct/group_by  sort: the sort's passes beyond reading its input; hash: 0 if the
                       input fits in B-1 frames, else 2*M (write and re-read partitions)
    nested_loop_join   M + ceil(M/(B-2)) * N
    sort_merge_join    sort(M) + sort(N) + M + N
    hash_join          3 * (M + N)
    limit              0

Join cost accounting follows the worked example of Section 5.1: a base input's
M (or N) term *is* its scan, so its seqscan cost is not added again; a composite
outer adds its own cost plus M to read it. An inner is re-executed for every outer
block, so N is the inner's full cost (its base pages if it is a filtered scan).

Enumeration: Selinger-style dynamic programming over left-deep trees. The right
input of every join is a leaf of the join block (a base relation with its pushed
selects and projections, or a planned subquery).
"""
import itertools
import math

from ralang import ast_nodes as ra
from ralang.condition import and_all, attrs_in, conjuncts, resolve_column
from ralang.parser import parse_condition
from ralang.plan_json import unparse_condition

from .estimate import Est
from .logical import OptError, _split, is_equi_between, props


def sort_passes(M, B):
    if M <= B:
        return 1
    return 1 + math.ceil(math.log(math.ceil(M / B)) / math.log(B - 1))


def sort_cost(M, B):
    return 2 * M * (sort_passes(M, B) - 1) + M


def bnl_cost(M, N, B):
    return M + math.ceil(M / (B - 2)) * N


class PNode:
    """A physical plan node with its estimate. .json() is what Module 2 runs."""

    def __init__(self, op, fields, children, est, cost, label):
        self.op, self.fields, self.children = op, fields, children
        self.est, self.cost, self.label = est, cost, label

    def json(self):
        d = {"op": self.op}
        d.update(self.fields)
        if self.op in ("nested_loop_join", "sort_merge_join", "hash_join", "union", "union_all",
                       "intersect", "except"):
            d["left"], d["right"] = self.children[0].json(), self.children[1].json()
        elif self.children:
            d["child"] = self.children[0].json()
        return d

    def explain(self, depth=0):
        head = "  " * depth + self.label
        lines = [f"{head:<52} est_rows={self.est.rows:<9} est_cost={int(round(self.cost))}"]
        for c in self.children:
            lines += c.explain(depth + 1)
        return lines

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class Planner:
    def __init__(self, estimator, B, dp_log=None):
        if B < 3:
            raise OptError("the optimizer needs B >= 3")
        self.e = estimator
        self.B = B
        self.dp_log = dp_log            # list to receive DP table rows, or None

    # -- entry ---------------------------------------------------------------------
    def plan(self, node):
        op = node["op"]
        if op in ("join", "cross") or (op == "select" and _block_root(node)):
            return self.join_block(node)
        if op == "scan":
            e = props(node, self.e)
            return PNode("seqscan", {"table": node["table"]}, [], e, e.base_pages,
                         f"seqscan {node['table']}")
        if op == "rename":
            if node["child"]["op"] == "scan":
                e = props(node, self.e)
                t = node["child"]["table"]
                return PNode("seqscan", {"table": t, "alias": node["to"]}, [], e, e.base_pages,
                             f"seqscan {t} as {node['to']}")
            c = self.plan(node["child"])
            return PNode("rename", {"to": node["to"]}, [c], props(node, self.e), c.cost, f"rename to {node['to']}")
        if op == "select":
            c = self.plan(node["child"])
            return PNode("filter", {"condition": node["condition"]}, [c], props(node, self.e), c.cost,
                         f"filter ({node['condition']})")
        if op == "project":
            ch = node["child"]
            if ch["op"] in ("join", "cross") or (ch["op"] == "select" and _block_root(ch)):
                c = self.join_block(ch, keep_order=False)   # this project sets the order
            else:
                c = self.plan(ch)
            f = {"columns": node["columns"]}
            if "as" in node:
                f["as"] = node["as"]
            return PNode("project", f, [c], props(node, self.e), c.cost, f"project [{', '.join(node['columns'])}]")
        if op == "limit":
            c = self.plan(node["child"])
            return PNode("limit", {"n": node["n"]}, [c], props(node, self.e), c.cost, f"limit {node['n']}")
        if op == "sort":
            c = self.plan(node["child"])
            M = c.est.pages
            return PNode("sort", {"keys": node["keys"]}, [c], props(node, self.e),
                         c.cost + sort_cost(M, self.B) - M,
                         "sort [" + ", ".join(f"{k} {d}" for k, d in node["keys"]) + "]")
        if op in ("distinct", "group_by"):
            ch = node["child"]
            if op == "group_by" and (ch["op"] in ("join", "cross") or (ch["op"] == "select" and _block_root(ch))):
                c = self.join_block(ch, keep_order=False)   # group_by output is keys + aggregates
            else:
                c = self.plan(ch)
            M = c.est.pages
            sort_extra = sort_cost(M, self.B) - M
            hash_extra = 0 if M <= self.B - 1 else 2 * M
            mode = "hash" if hash_extra <= sort_extra else "sort"
            extra = min(hash_extra, sort_extra)
            if op == "distinct":
                return PNode("distinct", {"mode": mode}, [c], props(node, self.e), c.cost + extra,
                             f"distinct ({mode})")
            f = {"mode": mode, "keys": node["keys"], "aggregates": node["aggregates"]}
            aggs = ", ".join(f"{a[0]}({a[1]})" for a in node["aggregates"])
            return PNode("group_by", f, [c], props(node, self.e), c.cost + extra,
                         f"group_by ({mode}) [{', '.join(node['keys'])}] [{aggs}]")
        if op in ("union", "union_all", "intersect", "except"):
            l, r = self.plan(node["left"]), self.plan(node["right"])
            extra = 0
            if op != "union_all":
                M, N = l.est.pages, r.est.pages
                # each side is sorted to one run on disk, then both runs are read back
                extra = (sort_cost(M, self.B) - M) + (sort_cost(N, self.B) - N) + M + N + M + N
            return PNode(op, {}, [l, r], props(node, self.e), l.cost + r.cost + extra, op)
        raise OptError(f"cannot plan logical operator '{op}'")

    # -- join blocks ------------------------------------------------------------------
    def join_block(self, node, keep_order=True):
        leaves, preds, top = [], [], []
        _flatten(node, leaves, preds, top)
        L = [self.plan(x) for x in leaves]
        for x, lf in zip(leaves, L):
            lf.leaf_name = leaf_name(x)
        self._names = [lf.leaf_name for lf in L]
        # which leaves each predicate references
        P = []
        for text in preds:
            tree = parse_condition(text)
            refs = frozenset(i for i, lf in enumerate(L) for a in attrs_in(tree) if _has(lf.est.cols, a))
            P.append((text, tree, refs))
        n = len(L)
        best = {}
        for i in range(n):
            best[frozenset([i])] = L[i]
            self._log(frozenset([i]), [(L[i].cost, f"access path for {L[i].leaf_name}: {L[i].label}", True)])
        full = frozenset(range(n))
        edges = [refs for _, _, refs in P if len(refs) > 1]
        avoid_cross = _connected(full, edges)
        for k in range(2, n + 1):
            for S in map(frozenset, itertools.combinations(range(n), k)):
                if avoid_cross and not _connected(S, edges):
                    self._log(S, None)              # no predicate inside S: skipped
                    continue
                cands = []
                connected_any = any(self._connecting(P, S, r) for r in S if S - {r} in best)
                for r in sorted(S):
                    O = S - {r}
                    if O not in best:
                        continue
                    conn = self._connecting(P, S, r)
                    if not conn and connected_any:
                        continue
                    for cand in self._join_candidates(best[O], len(O) == 1, L[r], conn):
                        cands.append(cand)
                if not cands:
                    self._log(S, [])
                    continue
                cands.sort(key=lambda c: (c.cost, c.label))
                best[S] = cands[0]
                self._log(S, [(c.cost, _desc(c, L), c is cands[0]) for c in cands])
        if full not in best:
            raise OptError("join enumeration found no plan")
        plan = best[full]
        # predicates that were never applied by a join (single-leaf predicates left
        # above the block when rule 2 is off) go on top as a filter
        used = set()
        for pn in plan.walk():
            used |= getattr(pn, "applied", set())
        rest = [t for t, _, refs in P if t not in used] + top
        if rest:
            cond = unparse_condition(and_all([parse_condition(t) for t in rest]))
            rows = plan.est.rows * self.e.selectivity(parse_condition(cond), plan.est)
            e = Est(rows, plan.est.width, plan.est.cols, plan.est.lineage, plan.est.ndv)
            plan = PNode("filter", {"condition": cond}, [plan], e, plan.cost, f"filter ({cond})")
        # a new join order also changes the column order; put back the order the
        # logical plan had (SELECT * must list columns in FROM order)
        want = [c[0] for c in props(node, self.e).cols]
        if keep_order and [c[0] for c in plan.est.cols] != want:
            e = props(node, self.e)
            e = Est(plan.est.rows, plan.est.width, e.cols, plan.est.lineage, plan.est.ndv)
            plan = PNode("project", {"columns": want}, [plan], e, plan.cost,
                         f"project [{', '.join(want)}]")
        return plan

    @staticmethod
    def _connecting(P, S, r):
        return [(t, tree) for t, tree, refs in P if r in refs and refs <= S and len(refs) > 1]

    def _join_candidates(self, outer, outer_is_leaf, inner, conn):
        B = self.B
        texts = sorted(t for t, _ in conn)
        cond = " and ".join(texts) if texts else None
        cols = outer.est.cols + inner.est.cols
        lin = {**outer.est.lineage, **inner.est.lineage}
        both = Est(1, outer.est.width + inner.est.width, cols, lin, {**outer.est.ndv, **inner.est.ndv})
        both.rows = outer.est.rows * inner.est.rows
        sel = 1.0
        for _, tree in conn:
            sel *= self.e.selectivity(tree, both)
        out = Est(outer.est.rows * inner.est.rows * sel, both.width, cols, lin, both.ndv)
        M, N = outer.est.pages, inner.est.pages
        outer_part = outer.cost if outer_is_leaf else outer.cost + M
        res = []
        c = outer_part + math.ceil(M / (B - 2)) * inner.cost
        res.append(self._node("nested_loop_join", cond, outer, inner, out, c, texts))
        equi = conn and all(is_equi_between(tree, outer.est.cols, inner.est.cols) for _, tree in conn)
        if equi:
            res.append(self._node("hash_join", cond, outer, inner, out,
                                  outer_part + 2 * M + inner.cost + 2 * N, texts))
            res.append(self._node("sort_merge_join", cond, outer, inner, out,
                                  outer_part + inner.cost + sort_cost(M, B) + sort_cost(N, B), texts))
        return res

    @staticmethod
    def _node(op, cond, outer, inner, est, cost, texts):
        f = {"condition": cond} if cond else {}
        p = PNode(op, f, [outer, inner], est, cost, f"{op} ({cond})" if cond else f"{op} (cross)")
        p.applied = set(texts)
        return p

    def _log(self, S, rows):
        if self.dp_log is not None:
            self.dp_log.append(([self._names[i] for i in sorted(S)], rows))


def _desc(c, L):
    o, i = c.children
    return f"{c.op} outer={_names(o, L)} ({o.est.pages}p) inner={_names(i, L)}"


def _names(p, L):
    if p in L:
        return p.leaf_name
    return "{" + ",".join(_names(c, L) for c in p.children) + "}"


def leaf_name(n):
    """Short name of a join-block leaf: its alias, table, or 'subquery'."""
    while n["op"] in ("project", "select"):
        n = n["child"]
    if n["op"] == "rename":
        return n["to"]
    return n.get("table", n["op"])


def _connected(S, edges):
    """Is the join graph restricted to S connected? (edges are sets of leaves)"""
    S = set(S)
    if len(S) <= 1:
        return True
    start = next(iter(S))
    seen, todo = {start}, [start]
    while todo:
        x = todo.pop()
        for e in edges:
            if x in e and e <= S:
                for y in e:
                    if y not in seen:
                        seen.add(y)
                        todo.append(y)
    return seen == S


def _has(cols, attr):
    try:
        resolve_column(cols, attr.relation, attr.name)
        return True
    except Exception:
        return False


def _block_root(node):
    while node["op"] == "select":
        node = node["child"]
    return node["op"] in ("join", "cross")


def _flatten(node, leaves, preds, top):
    """Leaves and predicates of a join block. Join conditions and selects inside
    the block become join predicates (split into conjuncts)."""
    op = node["op"]
    if op == "select":
        for c in conjuncts(parse_condition(node["condition"])):
            preds.append(unparse_condition(c))
        _flatten(node["child"], leaves, preds, top)
    elif op in ("join", "cross"):
        _flatten(node["left"], leaves, preds, top)
        _flatten(node["right"], leaves, preds, top)
        if op == "join":
            for c in conjuncts(parse_condition(node["condition"])):
                preds.append(unparse_condition(c))
    else:
        leaves.append(node)


# --------------------------------------------------------------------------
# The unoptimized baseline: joins in written order, one algorithm, no pushdown
# --------------------------------------------------------------------------

def naive_physical(node):
    op = node["op"]
    if op == "scan":
        return {"op": "seqscan", "table": node["table"]}
    if op == "rename" and node["child"]["op"] == "scan":
        return {"op": "seqscan", "table": node["child"]["table"], "alias": node["to"]}
    if op == "rename":
        return {"op": "rename", "to": node["to"], "child": naive_physical(node["child"])}
    if op == "select":
        return {"op": "filter", "condition": node["condition"], "child": naive_physical(node["child"])}
    if op in ("join", "cross"):
        d = {"op": "nested_loop_join", "left": naive_physical(node["left"]), "right": naive_physical(node["right"])}
        if op == "join":
            d["condition"] = node["condition"]
        return d
    if op in ("distinct", "group_by"):
        d = {k: v for k, v in node.items() if k != "child"}
        d["mode"] = "sort"
        d["child"] = naive_physical(node["child"])
        return d
    d = {k: v for k, v in node.items() if k not in ("child", "left", "right")}
    for k in ("child", "left", "right"):
        if k in node:
            d[k] = naive_physical(node[k])
    return d
