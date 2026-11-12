"""Plan tree <-> JSON, and condition unparsing (Project 2, week one, item 4).

JSON is the contract between modules, so every AST node type has a JSON form and
`from_json(to_json(tree))` gives back a tree with the same shape. Conditions are
written as strings ("R.b = S.b") and read back through parser.parse_condition, so
the same parser serves modules 2, 3 and 4.

    Relation -> scan        Select  -> select      Project -> project
    Rename   -> rename      Join    -> join        Times   -> join with condition null
    Union    -> union       Intersect -> intersect Minus   -> except
    Distinct -> distinct    (union/intersect/except carry an "all" flag)
"""
from . import ast_nodes as ast
from .parser import parse_attr, parse_condition

_SYM = {"EQ": "=", "NE": "!=", "LT": "<", "LE": "<=", "GT": ">", "GE": ">="}
_OR, _AND, _NOT = 1, 2, 3


class PlanError(Exception):
    """A plan whose JSON does not match the schema. The message names the node."""


def unparse_condition(c, parent=0):
    if isinstance(c, ast.Num):
        return str(c.value)
    if isinstance(c, ast.Str):
        return "'" + c.value.replace("'", "''") + "'"
    if isinstance(c, ast.Attr):
        return c.qualified()
    if isinstance(c, ast.Compare):
        return f"{unparse_condition(c.left)} {_SYM[c.op]} {unparse_condition(c.right)}"
    if isinstance(c, ast.Not):
        s = f"not {unparse_condition(c.inner, _NOT)}"
        return f"({s})" if parent > _NOT else s
    if isinstance(c, (ast.And, ast.Or)):
        prec, word = (_AND, "and") if isinstance(c, ast.And) else (_OR, "or")
        # left operand may share this level (left associative); the right one may not
        s = f"{unparse_condition(c.left, prec)} {word} {unparse_condition(c.right, prec + 1)}"
        return f"({s})" if parent > prec else s
    raise PlanError(f"cannot unparse {type(c).__name__}")


_BIN_OUT = {ast.Union: "union", ast.Intersect: "intersect", ast.Minus: "except"}
_BIN_IN = {"union": ast.Union, "intersect": ast.Intersect, "except": ast.Minus}


def to_json(n):
    if isinstance(n, ast.Relation):
        return {"op": "scan", "table": n.name}
    if isinstance(n, ast.Select):
        return {"op": "select", "condition": unparse_condition(n.cond), "child": to_json(n.child)}
    if isinstance(n, ast.Project):
        return {"op": "project", "columns": [a.qualified() for a in n.attrs], "child": to_json(n.child)}
    if isinstance(n, ast.Rename):
        return {"op": "rename", "alias": n.new_name, "child": to_json(n.child)}
    if isinstance(n, ast.Join):
        return {"op": "join", "condition": unparse_condition(n.cond),
                "left": to_json(n.left), "right": to_json(n.right)}
    if isinstance(n, ast.Times):
        return {"op": "join", "condition": None, "left": to_json(n.left), "right": to_json(n.right)}
    if isinstance(n, ast.Distinct):
        return {"op": "distinct", "child": to_json(n.child)}
    for cls, name in _BIN_OUT.items():
        if isinstance(n, cls):
            return {"op": name, "all": n.all, "left": to_json(n.left), "right": to_json(n.right)}
    raise PlanError(f"cannot serialise {type(n).__name__}")


def _field(d, op, name, typ, tname):
    if name not in d:
        raise PlanError(f"Node '{op}': missing required field '{name}'.")
    if not isinstance(d[name], typ):
        raise PlanError(f"Node '{op}': field '{name}' must be {tname}.")
    return d[name]


def from_json(d):
    if not isinstance(d, dict):
        raise PlanError("Plan node must be a JSON object.")
    op = d.get("op")
    if not isinstance(op, str):
        raise PlanError("Plan node: missing required field 'op'.")
    if op == "scan":
        return ast.Relation(_field(d, op, "table", str, "a string"))
    if op == "select":
        return ast.Select(parse_condition(_field(d, op, "condition", str, "a string")),
                          from_json(_field(d, op, "child", dict, "an object")))
    if op == "project":
        cols = _field(d, op, "columns", list, "a list")
        if not cols or not all(isinstance(c, str) for c in cols):
            raise PlanError("Node 'project': field 'columns' must be a non-empty list of strings.")
        return ast.Project([parse_attr(c) for c in cols], from_json(_field(d, op, "child", dict, "an object")))
    if op == "rename":
        return ast.Rename(_field(d, op, "alias", str, "a string"),
                          from_json(_field(d, op, "child", dict, "an object")))
    if op == "join":
        left = from_json(_field(d, op, "left", dict, "an object"))
        right = from_json(_field(d, op, "right", dict, "an object"))
        cond = d.get("condition")
        if cond is None:
            return ast.Times(left, right)
        if not isinstance(cond, str):
            raise PlanError("Node 'join': field 'condition' must be a string or null.")
        return ast.Join(parse_condition(cond), left, right)
    if op == "distinct":
        return ast.Distinct(from_json(_field(d, op, "child", dict, "an object")))
    if op in _BIN_IN:
        flag = d.get("all", False)
        if not isinstance(flag, bool):
            raise PlanError(f"Node '{op}': field 'all' must be a boolean.")
        return _BIN_IN[op](from_json(_field(d, op, "left", dict, "an object")),
                           from_json(_field(d, op, "right", dict, "an object")), (0, 0), flag)
    raise PlanError(f"Node '{op}': unknown operator '{op}'.")
