"""SQL syntax tree. Every node carries (line, col) of the token that started it."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Column:
    table: Optional[str]
    name: str
    pos: tuple = (0, 0)

    def text(self):
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class Literal:
    value: object          # int or str
    pos: tuple = (0, 0)

    def text(self):
        return str(self.value) if isinstance(self.value, int) else "'" + self.value.replace("'", "''") + "'"


@dataclass
class Aggregate:
    fn: str                # count sum avg min max (lower case)
    arg: object            # Column or "*"
    pos: tuple = (0, 0)

    def text(self):
        return f"{self.fn.upper()}({'*' if self.arg == '*' else self.arg.text()})"


@dataclass
class Compare:
    op: str                # = <> < <= > >=
    left: object
    right: object
    pos: tuple = (0, 0)


@dataclass
class BoolOp:
    op: str                # AND / OR
    left: object
    right: object
    pos: tuple = (0, 0)


@dataclass
class Not:
    inner: object
    pos: tuple = (0, 0)


@dataclass
class Star:
    pos: tuple = (0, 0)


@dataclass
class SelectItem:
    expr: object           # Column | Aggregate | Star
    alias: Optional[str] = None
    pos: tuple = (0, 0)


@dataclass
class TableRef:
    name: str
    alias: Optional[str] = None
    pos: tuple = (0, 0)


@dataclass
class SubqueryRef:
    query: object
    alias: str
    pos: tuple = (0, 0)


@dataclass
class FromClause:
    kind: str              # "comma" or "join"
    refs: list             # table refs in order
    conds: list = field(default_factory=list)    # for "join": one ON condition per JOIN


@dataclass
class SelectBlock:
    distinct: bool
    items: list            # [SelectItem]
    from_: FromClause
    where: object = None
    group_by: list = field(default_factory=list)     # [Column]
    having: object = None
    order_by: list = field(default_factory=list)     # [(Column, "asc"|"desc")]
    limit: Optional[int] = None
    pos: tuple = (0, 0)


@dataclass
class SetOp:
    op: str                # union, union_all, intersect, except
    left: object
    right: object
    pos: tuple = (0, 0)


@dataclass
class CreateTable:
    name: str
    columns: list          # [(name, "INT", 0) | (name, "VARCHAR", n)]
    pos: tuple = (0, 0)


@dataclass
class Insert:
    table: str
    values: list           # [Literal]
    pos: tuple = (0, 0)


def explain_ast(node, depth=0):
    """The indented tree `sql parse --explain` prints. No catalog is needed."""
    pad = "  " * depth
    out = []
    if isinstance(node, SetOp):
        out.append(f"{pad}{node.op.upper().replace('_', ' ')}")
        out += explain_ast(node.left, depth + 1)
        out += explain_ast(node.right, depth + 1)
    elif isinstance(node, SelectBlock):
        out.append(f"{pad}SELECT{' DISTINCT' if node.distinct else ''}")
        items = ", ".join(_item(i) for i in node.items)
        out.append(f"{pad}  list: {items}")
        out.append(f"{pad}  from ({node.from_.kind}):")
        for k, r in enumerate(node.from_.refs):
            if isinstance(r, TableRef):
                out.append(f"{pad}    table {r.name}" + (f" AS {r.alias}" if r.alias else ""))
            else:
                out.append(f"{pad}    subquery AS {r.alias}")
                out += explain_ast(r.query, depth + 3)
            if node.from_.kind == "join" and k > 0:
                out.append(f"{pad}      ON {cond_text(node.from_.conds[k - 1])}")
        if node.where is not None:
            out.append(f"{pad}  where: {cond_text(node.where)}")
        if node.group_by:
            out.append(f"{pad}  group by: {', '.join(c.text() for c in node.group_by)}")
        if node.having is not None:
            out.append(f"{pad}  having: {cond_text(node.having)}")
        if node.order_by:
            out.append(f"{pad}  order by: {', '.join(f'{c.text()} {d.upper()}' for c, d in node.order_by)}")
        if node.limit is not None:
            out.append(f"{pad}  limit: {node.limit}")
    elif isinstance(node, CreateTable):
        cols = ", ".join(f"{n} {t}" + (f"({ln})" if t == "VARCHAR" else "") for n, t, ln in node.columns)
        out.append(f"{pad}CREATE TABLE {node.name} ({cols})")
    elif isinstance(node, Insert):
        out.append(f"{pad}INSERT INTO {node.table} VALUES ({', '.join(v.text() for v in node.values)})")
    return out


def _item(i):
    e = "*" if isinstance(i.expr, Star) else i.expr.text()
    return e + (f" AS {i.alias}" if i.alias else "")


def cond_text(c, parent=0):
    """SQL-ish text of a condition, fully parenthesised where precedence needs it."""
    if isinstance(c, Compare):
        return f"{c.left.text()} {c.op} {c.right.text()}"
    if isinstance(c, Not):
        s = f"NOT {cond_text(c.inner, 3)}"
        return f"({s})" if parent > 3 else s
    prec = 2 if c.op == "AND" else 1
    s = f"{cond_text(c.left, prec)} {c.op} {cond_text(c.right, prec + 1)}"
    return f"({s})" if parent > prec else s
