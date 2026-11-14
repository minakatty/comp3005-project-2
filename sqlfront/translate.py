"""Semantic analysis and translation to a logical plan (M3 spec Sections 3 to 5).

One pass per SELECT block, in the canonical order of Section 5:

    1 FROM      scans, renames for aliases, cross / join left to right
    2 WHERE     one select node with the whole condition unchanged
    3 GROUP BY  group_by node
    4 HAVING    select node above the group_by
    5 SELECT    project node, then distinct if DISTINCT was written
    6 ORDER BY  sort node
    7 LIMIT     limit node
    8 set ops   between whole blocks, left associative

No optimisation happens here: predicates stay exactly where the SQL put them.

Every plan node is returned together with its output columns, a list of
(qualified name, type, length). That list is what the scope of an outer query, a
set operation's compatibility check and the next node's resolution read.

Where duplicate elimination happens (DESIGN_M3.md 3): only at a distinct node
(SELECT DISTINCT), a union node (UNION), intersect / except (always set
semantics), and implicitly at group_by. Never at project.
"""
from ralang import ast_nodes as ra
from ralang.plan_json import unparse_condition

from . import sqlast as A
from .errors import SQLError

_OPS = {"=": "EQ", "<>": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE"}


def _err(cat, msg, pos):
    return SQLError(cat, msg, *(pos if pos else (None, None)))


def type_class(t):
    return "num" if t in ("INT", "LONG", "FLOAT") else "str"


TYPE_WORD = {"num": "INT", "str": "VARCHAR"}


def agg_name(fn, arg):
    """Output name of an aggregate with no AS alias: count_star, avg_Emp_salary.
    The same rule Module 2 applies when a group_by aggregate carries no name."""
    return f"{fn}_star" if arg == "*" else f"{fn}_{arg.replace('.', '_')}"


class Scope:
    """FROM-clause scope: alias (or table name) -> output columns of that entry."""

    def __init__(self):
        self.entries = []           # [(key, [(qualified, type, len)])]

    def add(self, key, cols, pos):
        if any(k == key for k, _ in self.entries):
            raise _err("name", f"duplicate table name or alias '{key}' in FROM; "
                               f"give each occurrence its own alias", pos)
        self.entries.append((key, cols))

    def all_columns(self):
        return [c for _, cols in self.entries for c in cols]

    def resolve(self, col):
        """Column -> its (qualified, type, len) entry, by the rules of Section 3."""
        if col.table is not None:
            for key, cols in self.entries:
                if key == col.table:
                    for c in cols:
                        if c[0].rsplit(".", 1)[-1] == col.name:
                            return c
                    raise _err("name", f"table '{col.table}' has no column '{col.name}'", col.pos)
            raise _err("name", f"unknown table or alias '{col.table}' in '{col.text()}'", col.pos)
        hits = [c for _, cols in self.entries for c in cols if c[0].rsplit(".", 1)[-1] == col.name]
        if not hits:
            raise _err("name", f"unknown column '{col.name}'", col.pos)
        if len(hits) > 1:
            raise _err("ambiguity", f"column '{col.name}' is ambiguous: it could be "
                                    f"{' or '.join(h[0] for h in hits)}", col.pos)
        return hits[0]


class Catalog:
    """What the translator needs from Module 1: schema(name) -> [(col, type, len)]
    or None. `extra` holds tables created earlier in the same SQL file."""

    def __init__(self, lookup):
        self.lookup = lookup
        self.extra = {}

    def schema(self, name):
        if name in self.extra:
            return self.extra[name]
        return self.lookup(name)


class Translator:
    def __init__(self, catalog):
        self.cat = catalog

    # -- statements ------------------------------------------------------------
    def statement(self, st):
        if isinstance(st, A.CreateTable):
            if self.cat.schema(st.name) is not None:
                raise _err("schema", f"table '{st.name}' already exists", st.pos)
            self.cat.extra[st.name] = list(st.columns)
            return {"op": "create_table", "table": st.name,
                    "columns": [[n, t] if t == "INT" else [n, t, ln] for n, t, ln in st.columns]}
        if isinstance(st, A.Insert):
            cols = self.cat.schema(st.table)
            if cols is None:
                raise _err("name", f"unknown table '{st.table}'", st.pos)
            if len(cols) != len(st.values):
                raise _err("schema", f"table {st.table} has {len(cols)} columns, "
                                     f"INSERT gives {len(st.values)} values", st.pos)
            for (n, t, ln), v in zip(cols, st.values):
                if (t == "INT") != isinstance(v.value, int):
                    raise _err("type", f"column {n} is {t}, value {v.text()} is not", v.pos)
                if t == "VARCHAR" and len(v.value.encode("utf-8")) > ln:
                    raise _err("type", f"value {v.text()} is longer than VARCHAR({ln}) for column {n}", v.pos)
            return {"op": "insert", "table": st.table, "values": [v.value for v in st.values]}
        plan, _ = self.query(st)
        return plan

    # -- queries -----------------------------------------------------------------
    def query(self, q):
        if isinstance(q, A.SetOp):
            lp, lc = self.query(q.left)
            rp, rc = self.query(q.right)
            if len(lc) != len(rc):
                raise _err("schema", f"{q.op.upper().replace('_', ' ')} needs the same number of columns "
                                     f"on both sides ({len(lc)} vs {len(rc)})", q.pos)
            for i, (a, b) in enumerate(zip(lc, rc)):
                if type_class(a[1]) != type_class(b[1]):
                    raise _err("schema", f"{q.op.upper().replace('_', ' ')} column {i + 1} is "
                                         f"{TYPE_WORD[type_class(a[1])]} on the left and "
                                         f"{TYPE_WORD[type_class(b[1])]} on the right", q.pos)
            return {"op": q.op, "left": lp, "right": rp}, lc
        return self.block(q)

    def from_clause(self, fc):
        scope = Scope()
        plan = None
        for k, ref in enumerate(fc.refs):
            node, cols, key = self.table_ref(ref)
            scope.add(key, cols, ref.pos)
            if plan is None:
                plan = node
            elif fc.kind == "comma":
                plan = {"op": "cross", "left": plan, "right": node}
            else:
                cond = self.condition(fc.conds[k - 1], scope, allow_agg=False, where="ON")
                plan = {"op": "join", "condition": cond, "left": plan, "right": node}
        return plan, scope

    def table_ref(self, ref):
        if isinstance(ref, A.SubqueryRef):
            sub, sub_cols = self.query(ref.query)
            cols = [(f"{ref.alias}.{c[0].rsplit('.', 1)[-1]}",) + tuple(c[1:]) for c in sub_cols]
            seen = set()
            for c in cols:
                if c[0] in seen:
                    raise _err("schema", f"subquery {ref.alias} has two columns named "
                                         f"'{c[0].rsplit('.', 1)[-1]}'; alias one of them", ref.pos)
                seen.add(c[0])
            return {"op": "rename", "to": ref.alias, "child": sub}, cols, ref.alias
        sch = self.cat.schema(ref.name)
        if sch is None:
            raise _err("name", f"unknown table '{ref.name}'", ref.pos)
        key = ref.alias or ref.name
        cols = [(f"{key}.{n}", t, ln) for n, t, ln in sch]
        node = {"op": "scan", "table": ref.name}
        if ref.alias:
            node = {"op": "rename", "to": ref.alias, "child": node}
        return node, cols, key

    def block(self, b):
        plan, scope = self.from_clause(b.from_)
        cols = scope.all_columns()

        # 2  WHERE
        if b.where is not None:
            plan = {"op": "select", "condition": self.condition(b.where, scope, False, "WHERE"), "child": plan}

        aggs_in_select = [i.expr for i in b.items if isinstance(i.expr, A.Aggregate)]
        aggregating = bool(b.group_by) or bool(aggs_in_select)

        # 3, 4  GROUP BY and HAVING
        agg_out = {}            # (fn, arg qualified) -> output name
        agg_list = []
        keys = []
        if aggregating:
            for g in b.group_by:
                c = scope.resolve(g)
                if c[0] not in [k[0] for k in keys]:
                    keys.append(c)
            # aliases first, so COUNT(*) AS n names the aggregate n everywhere
            for it in b.items:
                if isinstance(it.expr, A.Aggregate):
                    self._register_agg(it.expr, scope, agg_out, agg_list, it.alias)
            having = None
            if b.having is not None:
                for a in _aggs_in(b.having):
                    self._register_agg(a, scope, agg_out, agg_list, None)
            plan = {"op": "group_by", "keys": [k[0] for k in keys],
                    "aggregates": [[fn, arg, name] for fn, arg, name, _ in agg_list], "child": plan}
            group_cols = list(keys) + [(name, t, ln) for _, _, name, (t, ln) in agg_list]
            if b.having is not None:
                having = self.condition(b.having, scope, True, "HAVING", agg_out, keys)
                plan = {"op": "select", "condition": having, "child": plan}
            cols = group_cols

        # 5  SELECT list
        out_cols, proj, names, any_alias = [], [], [], False
        for it in b.items:
            if isinstance(it.expr, A.Star):
                if aggregating:
                    raise _err("aggregate", "SELECT * is not allowed in an aggregating query", it.pos)
                for c in scope.all_columns():
                    proj.append(c[0])
                    names.append(None)
                    out_cols.append(c)
                continue
            if isinstance(it.expr, A.Aggregate):
                name = agg_out[self._agg_key(it.expr, scope)]
                c = next(gc for gc in cols if gc[0] == name)
                proj.append(name)
                if it.alias and it.alias != name:
                    names.append(it.alias)
                    any_alias = True
                    c = (it.alias,) + tuple(c[1:])
                else:
                    names.append(None)
                out_cols.append(c)
                continue
            c = scope.resolve(it.expr)
            if aggregating and c[0] not in [k[0] for k in keys]:
                raise _err("aggregate", f"column '{c[0]}' must appear in GROUP BY or be used in an "
                                        f"aggregate", it.expr.pos)
            proj.append(c[0])
            if it.alias:
                names.append(it.alias)
                any_alias = True
                c = (it.alias,) + tuple(c[1:])
            else:
                names.append(None)
            out_cols.append(c)
        seen = set()
        for c in out_cols:
            if c[0] in seen:
                raise _err("schema", f"the select list produces two columns named '{c[0]}'; "
                                     f"use AS to rename one", b.pos)
            seen.add(c[0])

        # 6  ORDER BY: resolve against output names first, then the FROM scope
        sort_keys, hidden = [], []
        for col, d in b.order_by:
            name = self._order_key(col, out_cols, names, proj, scope, cols, aggregating, keys)
            if name not in [c[0] for c in out_cols]:
                if b.distinct:
                    raise _err("schema", f"with SELECT DISTINCT, ORDER BY column '{col.text()}' must "
                                         f"appear in the select list", col.pos)
                if name not in hidden:
                    hidden.append(name)
            sort_keys.append([name, d])

        proj_node = {"op": "project", "columns": proj + hidden, "child": plan}
        if any_alias:
            proj_node["as"] = names + [None] * len(hidden)
        plan = proj_node
        if b.distinct and not self._distinct_redundant(aggregating, keys, proj):
            plan = {"op": "distinct", "child": plan}
        if sort_keys:
            plan = {"op": "sort", "keys": sort_keys, "child": plan}
        if hidden:
            # the ORDER BY column was carried through the projection only to sort on it
            plan = {"op": "project", "columns": [c[0] for c in out_cols], "child": plan}
        if b.limit is not None:
            plan = {"op": "limit", "n": b.limit, "child": plan}
        return plan, out_cols

    @staticmethod
    def _distinct_redundant(aggregating, keys, proj):
        """GROUP BY already gives one row per group. DISTINCT above it adds nothing
        exactly when every grouping column is in the output; then it is not emitted.
        (SELECT DISTINCT COUNT(*) ... GROUP BY d can repeat counts, so it is kept.)"""
        return aggregating and all(k[0] in proj for k in keys)

    def _order_key(self, col, out_cols, names, proj, scope, cols, aggregating, keys):
        if col.table is None:
            for c, alias in zip(out_cols, names):
                if alias is not None and alias == col.name:
                    return c[0]
            for c in out_cols:
                if c[0] == col.name:              # an aggregate output name such as n
                    return c[0]
        try:
            c = scope.resolve(col)
        except SQLError:
            if aggregating and col.table is None:
                for gc in cols:
                    if gc[0] == col.name:
                        return gc[0]
            raise
        if aggregating and c[0] not in [k[0] for k in keys]:
            raise _err("aggregate", f"ORDER BY column '{c[0]}' must be a grouping column in an "
                                    f"aggregating query", col.pos)
        # an output column that is this source column, possibly under an alias
        for oc, src in zip(out_cols, proj):
            if src == c[0]:
                return oc[0]
        return c[0]

    # -- aggregates -------------------------------------------------------------------
    def _agg_key(self, a, scope):
        return (a.fn, "*" if a.arg == "*" else scope.resolve(a.arg)[0])

    def _register_agg(self, a, scope, agg_out, agg_list, alias):
        key = self._agg_key(a, scope)
        if key in agg_out:
            return agg_out[key]
        if a.arg == "*":
            t, ln = "INT", 0
        else:
            c = scope.resolve(a.arg)
            if a.fn in ("sum", "avg") and c[1] != "INT":
                raise _err("type", f"{a.fn.upper()} needs an INT column; {c[0]} is {c[1]}", a.pos)
            t, ln = ("INT", 0) if a.fn in ("count", "sum", "avg") else (c[1], c[2])
        name = alias or agg_name(a.fn, key[1])
        agg_out[key] = name
        agg_list.append((a.fn, key[1], name, (t, ln)))
        return name

    # -- conditions ---------------------------------------------------------------------
    def condition(self, c, scope, allow_agg, where, agg_out=None, keys=None):
        tree = self._cond(c, scope, allow_agg, where, agg_out, keys)
        return unparse_condition(tree)

    def _cond(self, c, scope, allow_agg, where, agg_out, keys):
        if isinstance(c, A.BoolOp):
            cls = ra.And if c.op == "AND" else ra.Or
            return cls(self._cond(c.left, scope, allow_agg, where, agg_out, keys),
                       self._cond(c.right, scope, allow_agg, where, agg_out, keys))
        if isinstance(c, A.Not):
            return ra.Not(self._cond(c.inner, scope, allow_agg, where, agg_out, keys))
        lt, l = self._operand(c.left, scope, allow_agg, where, agg_out, keys)
        rt, r = self._operand(c.right, scope, allow_agg, where, agg_out, keys)
        if lt != rt:
            raise _err("type", f"cannot compare {TYPE_WORD[lt]} with {TYPE_WORD[rt]}: "
                               f"{_txt(c.left)} {c.op} {_txt(c.right)}", c.pos)
        return ra.Compare(_OPS[c.op], l, r)

    def _operand(self, o, scope, allow_agg, where, agg_out, keys):
        if isinstance(o, A.Literal):
            if isinstance(o.value, int):
                return "num", ra.Num(o.value)
            return "str", ra.Str(o.value)
        if isinstance(o, A.Aggregate):
            if not allow_agg:
                raise _err("aggregate", f"aggregate {o.text()} is not allowed in {where}"
                                        + (" (WHERE runs before grouping; use HAVING)" if where == "WHERE" else ""),
                           o.pos)
            name = agg_out[self._agg_key(o, scope)]
            t = "num" if o.fn in ("count", "sum", "avg") else type_class(scope.resolve(o.arg)[1])
            return t, ra.Attr(None, name)
        c = scope.resolve(o)
        if keys is not None and c[0] not in [k[0] for k in keys]:
            raise _err("aggregate", f"HAVING may use only grouping columns and aggregates; "
                                    f"'{c[0]}' is neither", o.pos)
        rel, name = c[0].rsplit(".", 1)
        return type_class(c[1]), ra.Attr(rel, name)


def _aggs_in(c):
    if isinstance(c, A.BoolOp):
        return _aggs_in(c.left) + _aggs_in(c.right)
    if isinstance(c, A.Not):
        return _aggs_in(c.inner)
    return [x for x in (c.left, c.right) if isinstance(x, A.Aggregate)]


def _txt(o):
    return o.text()
