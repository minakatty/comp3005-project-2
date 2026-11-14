"""The condition parser and evaluator, exposed on its own (Project 2, week one, item 2).

Modules 2, 3 and 4 all need to take a condition string such as "R.b = S.b and
R.a > 500", turn it into a tree, check it against a schema and evaluate it on a
tuple. This module is that one reused piece. It is built on the Bonus 1 parser
(parser.parse_condition), so the grammar in GRAMMAR.md 1.3 is the grammar of every
condition string in every plan.

A *column list* here is the Project 2 schema form: a list of (name, type[, length]),
where name is a qualified "R.a" or, for computed columns, a bare "count_star", and
type is "INT", "VARCHAR" or "FLOAT" (FLOAT only ever comes out of AVG).

Nothing here prints or reads files.
"""
import operator as _op

from . import ast_nodes as ast
from .errors import NameError_, TypeError_
from .parser import parse_condition
from .plan_json import unparse_condition

_FN = {"EQ": _op.eq, "NE": _op.ne, "LT": _op.lt, "LE": _op.le, "GT": _op.gt, "GE": _op.ge}
_NUMERIC = ("INT", "FLOAT", "LONG")


def type_class(t):
    """INT and FLOAT compare with each other; VARCHAR compares only with VARCHAR."""
    return "num" if t in _NUMERIC else "str"


def resolve_column(columns, relation, name, pos=(None, None)):
    """Index of the column a reference names, by the rules of Bonus 1 schema.resolve:
    a qualified reference must match exactly; an unqualified one must match exactly
    one column's last component. Raises NameError_ otherwise."""
    if relation is not None:
        want = f"{relation}.{name}"
        for i, col in enumerate(columns):
            if col[0] == want:
                return i
        raise NameError_(f"unknown column '{want}'", *pos)
    hits = [i for i, col in enumerate(columns) if col[0] == name or col[0].rsplit(".", 1)[-1] == name]
    if not hits:
        raise NameError_(f"unknown column '{name}'", *pos)
    if len(hits) > 1:
        names = ", ".join(columns[i][0] for i in hits)
        raise NameError_(f"column '{name}' is ambiguous ({names})", *pos)
    return hits[0]


def column_index(columns, text):
    """Resolve a column given as a string, "R.a" or "a"."""
    if "." in text:
        rel, name = text.rsplit(".", 1)
        return resolve_column(columns, rel, name)
    return resolve_column(columns, None, text)


def attrs_in(cond):
    """Every Attr node in a condition tree, left to right."""
    if isinstance(cond, ast.Attr):
        return [cond]
    if isinstance(cond, ast.Compare):
        return attrs_in(cond.left) + attrs_in(cond.right)
    if isinstance(cond, (ast.And, ast.Or)):
        return attrs_in(cond.left) + attrs_in(cond.right)
    if isinstance(cond, ast.Not):
        return attrs_in(cond.inner)
    return []


def conjuncts(cond):
    """Split a tree of ANDs into its list of conjuncts (rewrite rule 1)."""
    if isinstance(cond, ast.And):
        return conjuncts(cond.left) + conjuncts(cond.right)
    return [cond]


def and_all(parts):
    node = parts[0]
    for p in parts[1:]:
        node = ast.And(node, p)
    return node


def _operand(o, columns):
    """Compile one operand into (getter(row), type class)."""
    if isinstance(o, ast.Num):
        v = o.value
        return (lambda row: v), "num"
    if isinstance(o, ast.Str):
        v = o.value
        return (lambda row: v), "str"
    if isinstance(o, ast.Attr):
        i = resolve_column(columns, o.relation, o.name, o.pos)
        return (lambda row: row[i]), type_class(columns[i][1])
    raise AssertionError(type(o).__name__)


def _compile(c, columns):
    if isinstance(c, ast.Compare):
        lf, lt = _operand(c.left, columns)
        rf, rt = _operand(c.right, columns)
        if lt != rt:
            word = {"num": "a number", "str": "a string"}
            raise TypeError_(f"cannot compare {word[lt]} with {word[rt]} in "
                             f"'{unparse_condition(c)}'", *c.pos)
        fn = _FN[c.op]
        return lambda row: fn(lf(row), rf(row))
    if isinstance(c, ast.And):
        a, b = _compile(c.left, columns), _compile(c.right, columns)
        return lambda row: a(row) and b(row)
    if isinstance(c, ast.Or):
        a, b = _compile(c.left, columns), _compile(c.right, columns)
        return lambda row: a(row) or b(row)
    if isinstance(c, ast.Not):
        a = _compile(c.inner, columns)
        return lambda row: not a(row)
    raise AssertionError(type(c).__name__)


class Condition:
    """A parsed, type-checked, compiled condition.

        cond = Condition("R.a > 500 and R.b = 'cs'", columns)
        cond(row) -> bool

    Construction does all the checking (unknown column, ambiguous column,
    number-vs-string) so an error surfaces before any tuple is touched."""

    def __init__(self, text_or_tree, columns):
        self.tree = parse_condition(text_or_tree) if isinstance(text_or_tree, str) else text_or_tree
        self.text = unparse_condition(self.tree)
        self._fn = _compile(self.tree, columns)

    def __call__(self, row):
        return self._fn(row)


def equi_keys(cond_text, left_cols, right_cols):
    """If a join condition is a conjunction of column = column equalities, each with
    one side in `left_cols` and the other in `right_cols`, return the two lists of
    key indexes (left, right). Otherwise return None. Used by sort-merge and hash
    join, which serve equality conditions only."""
    tree = parse_condition(cond_text) if isinstance(cond_text, str) else cond_text
    lk, rk = [], []
    for c in conjuncts(tree):
        if not (isinstance(c, ast.Compare) and c.op == "EQ"
                and isinstance(c.left, ast.Attr) and isinstance(c.right, ast.Attr)):
            return None
        a, b = c.left, c.right
        ia = _try(left_cols, a)
        ib = _try(right_cols, b)
        if ia is None or ib is None:
            ia, ib = _try(left_cols, b), _try(right_cols, a)
            if ia is None or ib is None:
                return None
        if type_class(left_cols[ia][1]) != type_class(right_cols[ib][1]):
            raise TypeError_(f"cannot compare {left_cols[ia][0]} with {right_cols[ib][0]}: "
                             f"different types")
        lk.append(ia)
        rk.append(ib)
    return lk, rk


def _try(columns, attr):
    try:
        return resolve_column(columns, attr.relation, attr.name)
    except NameError_:
        return None
