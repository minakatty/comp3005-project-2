"""Recursive descent parser for the SQL subset. One method per non-terminal of
GRAMMAR_SQL.md; read the two side by side.

    script       ::= statement { ";" statement } [ ";" ]
    statement    ::= create | insert | query
    query        ::= select_block { setop select_block }        left associative, one level
    setop        ::= UNION [ ALL ] | INTERSECT | EXCEPT
    select_block ::= SELECT [DISTINCT] select_list FROM from_clause
                     [WHERE condition] [GROUP BY col_list [HAVING condition]]
                     [ORDER BY sort_list] [LIMIT int]
                   | "(" query ")"
    condition    ::= or_cond
    or_cond      ::= and_cond { OR and_cond }
    and_cond     ::= not_cond { AND not_cond }
    not_cond     ::= NOT not_cond | cond_primary
    cond_primary ::= "(" condition ")" | comparison
    comparison   ::= operand compop operand
    operand      ::= column | INT | STRING | aggregate        (aggregate: HAVING only)
"""
from . import sqlast as A
from .errors import SQLError
from .lexer import tokenize

AGGS = ("count", "sum", "avg", "min", "max")
COMPOPS = ("=", "<>", "<", "<=", ">", ">=")


class Parser:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.i = 0

    # -- helpers --------------------------------------------------------------
    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def advance(self):
        t = self.toks[self.i]
        if t.kind != "EOF":
            self.i += 1
        return t

    def at(self, kind):
        return self.peek().kind == kind

    def at_kw(self, *w):
        return self.peek().is_kw(*w)

    def err(self, msg, tok=None):
        t = tok or self.peek()
        return SQLError("syntax", msg, t.line, t.col)

    @staticmethod
    def show(t):
        return "end of input" if t.kind == "EOF" else f"'{t.text}'"

    def expect(self, kind, what):
        if not self.at(kind):
            raise self.err(f"expected {what}, found {self.show(self.peek())}")
        return self.advance()

    def expect_kw(self, word):
        if not self.at_kw(word):
            raise self.err(f"expected {word}, found {self.show(self.peek())}")
        return self.advance()

    def close_paren(self, opener):
        if not self.at("RPAREN"):
            raise SQLError("syntax", f"'(' opened here is never closed (found {self.show(self.peek())})",
                           opener.line, opener.col)
        return self.advance()

    def ident(self, what="an identifier"):
        t = self.peek()
        if t.kind != "IDENT":
            hint = f" ({t.value} is a reserved word)" if t.kind == "KW" else ""
            raise self.err(f"expected {what}, found {self.show(t)}{hint}")
        return self.advance()

    # -- statements ---------------------------------------------------------------
    def script(self):
        stmts = []
        while not self.at("EOF"):
            if self.at("SEMI"):
                self.advance()
                continue
            stmts.append(self.statement())
            if not self.at("EOF") and not self.at("SEMI"):
                raise self.err(f"expected ';' or end of input, found {self.show(self.peek())}")
        if not stmts:
            raise self.err("empty input: no statement")
        return stmts

    def statement(self):
        if self.at_kw("CREATE"):
            return self.create()
        if self.at_kw("INSERT"):
            return self.insert()
        return self.query()

    def create(self):
        kw = self.advance()
        self.expect_kw("TABLE")
        name = self.ident("a table name").value
        lp = self.expect("LPAREN", "'('")
        cols = [self.coldef()]
        while self.at("COMMA"):
            self.advance()
            cols.append(self.coldef())
        self.close_paren(lp)
        seen = set()
        for c in cols:
            if c[0] in seen:
                raise SQLError("schema", f"column '{c[0]}' is declared twice", kw.line, kw.col)
            seen.add(c[0])
        return A.CreateTable(name, cols, (kw.line, kw.col))

    def coldef(self):
        name = self.ident("a column name").value
        t = self.ident("a type (INT or VARCHAR(n))")
        up = t.value.upper()
        if up == "INT":
            return (name, "INT", 0)
        if up == "VARCHAR":
            lp = self.expect("LPAREN", "'(' after VARCHAR")
            n = self.expect("INT", "a length")
            self.close_paren(lp)
            if not 1 <= n.value <= 255:
                raise SQLError("type", f"VARCHAR length must be 1..255, got {n.value}", n.line, n.col)
            return (name, "VARCHAR", n.value)
        raise SQLError("type", f"unknown type '{t.value}' (INT or VARCHAR(n))", t.line, t.col)

    def insert(self):
        kw = self.advance()
        self.expect_kw("INTO")
        name = self.ident("a table name").value
        self.expect_kw("VALUES")
        lp = self.expect("LPAREN", "'('")
        vals = [self.literal()]
        while self.at("COMMA"):
            self.advance()
            vals.append(self.literal())
        self.close_paren(lp)
        return A.Insert(name, vals, (kw.line, kw.col))

    def literal(self):
        t = self.peek()
        if t.kind in ("INT", "STRING"):
            self.advance()
            return A.Literal(t.value, (t.line, t.col))
        raise self.err(f"expected a number or a string, found {self.show(t)}")

    # -- queries --------------------------------------------------------------------
    def query(self):
        node = self.select_block()
        while self.at_kw("UNION", "INTERSECT", "EXCEPT"):
            t = self.advance()
            op = t.value.lower()
            if op == "union" and self.at_kw("ALL"):
                self.advance()
                op = "union_all"
            elif self.at_kw("ALL"):
                raise self.err(f"{t.value} ALL is not part of this SQL subset")
            # folding into `node` makes the set operators left associative
            node = A.SetOp(op, node, self.select_block(), (t.line, t.col))
        return node

    def select_block(self):
        if self.at("LPAREN"):
            lp = self.advance()
            q = self.query()
            self.close_paren(lp)
            return q
        kw = self.peek()
        if not self.at_kw("SELECT"):
            raise self.err(f"expected SELECT, found {self.show(kw)}")
        self.advance()
        distinct = False
        if self.at_kw("DISTINCT"):
            self.advance()
            distinct = True
        items = self.select_list()
        self.expect_kw("FROM")
        from_ = self.from_clause()
        blk = A.SelectBlock(distinct, items, from_, pos=(kw.line, kw.col))
        if self.at_kw("WHERE"):
            self.advance()
            blk.where = self.condition()
        if self.at_kw("GROUP"):
            self.advance()
            self.expect_kw("BY")
            blk.group_by = [self.column()]
            while self.at("COMMA"):
                self.advance()
                blk.group_by.append(self.column())
            if self.at_kw("HAVING"):
                self.advance()
                blk.having = self.condition()
        elif self.at_kw("HAVING"):
            raise self.err("HAVING needs a GROUP BY clause in this subset")
        if self.at_kw("ORDER"):
            self.advance()
            self.expect_kw("BY")
            blk.order_by = [self.sort_item()]
            while self.at("COMMA"):
                self.advance()
                blk.order_by.append(self.sort_item())
        if self.at_kw("LIMIT"):
            self.advance()
            t = self.expect("INT", "a row count after LIMIT")
            if t.value < 0:
                raise SQLError("syntax", "LIMIT must not be negative", t.line, t.col)
            blk.limit = t.value
        return blk

    def sort_item(self):
        c = self.column()
        d = "asc"
        if self.at_kw("ASC", "DESC"):
            d = self.advance().value.lower()
        return (c, d)

    def select_list(self):
        if self.at("STAR"):
            t = self.advance()
            return [A.SelectItem(A.Star((t.line, t.col)), None, (t.line, t.col))]
        items = [self.select_item()]
        while self.at("COMMA"):
            self.advance()
            items.append(self.select_item())
        return items

    def select_item(self):
        t = self.peek()
        expr = self.aggregate() if self._at_aggregate() else self.column()
        alias = None
        if self.at_kw("AS"):
            self.advance()
            alias = self.ident("an alias after AS").value
        return A.SelectItem(expr, alias, (t.line, t.col))

    def _at_aggregate(self):
        t = self.peek()
        return t.kind == "IDENT" and t.value.lower() in AGGS and self.peek(1).kind == "LPAREN"

    def aggregate(self):
        t = self.advance()
        lp = self.expect("LPAREN", "'('")
        if self.at("STAR"):
            self.advance()
            arg = "*"
            if t.value.lower() != "count":
                raise SQLError("aggregate", f"{t.value.upper()}(*) is not allowed; only COUNT(*)", t.line, t.col)
        else:
            arg = self.column()
        self.close_paren(lp)
        return A.Aggregate(t.value.lower(), arg, (t.line, t.col))

    def column(self):
        first = self.ident("a column name")
        if self.at("DOT"):
            self.advance()
            second = self.ident("a column name after '.'")
            return A.Column(first.value, second.value, (first.line, first.col))
        return A.Column(None, first.value, (first.line, first.col))

    def from_clause(self):
        first = self.table_ref()
        if self.at("COMMA"):
            refs = [first]
            while self.at("COMMA"):
                self.advance()
                refs.append(self.table_ref())
            if self.at_kw("JOIN"):
                raise self.err("mixing ',' and JOIN in one FROM clause is not part of this subset")
            return A.FromClause("comma", refs)
        if self.at_kw("JOIN"):
            refs, conds = [first], []
            while self.at_kw("JOIN"):
                self.advance()
                refs.append(self.table_ref())
                self.expect_kw("ON")
                conds.append(self.condition())
            if self.at("COMMA"):
                raise self.err("mixing ',' and JOIN in one FROM clause is not part of this subset")
            return A.FromClause("join", refs, conds)
        return A.FromClause("comma", [first])

    def table_ref(self):
        t = self.peek()
        if self.at("LPAREN"):
            lp = self.advance()
            q = self.query()
            self.close_paren(lp)
            if not self.at_kw("AS"):
                raise self.err("a subquery in FROM must have an alias: ( ... ) AS name")
            self.advance()
            alias = self.ident("an alias for the subquery").value
            return A.SubqueryRef(q, alias, (t.line, t.col))
        name = self.ident("a table name").value
        alias = None
        if self.at_kw("AS"):
            self.advance()
            alias = self.ident("an alias after AS").value
        elif self.at("IDENT"):
            alias = self.advance().value             # FROM Emp e
        return A.TableRef(name, alias, (t.line, t.col))

    # -- conditions -------------------------------------------------------------------
    def condition(self):
        return self.or_cond()

    def or_cond(self):
        node = self.and_cond()
        while self.at_kw("OR"):
            t = self.advance()
            node = A.BoolOp("OR", node, self.and_cond(), (t.line, t.col))
        return node

    def and_cond(self):
        node = self.not_cond()
        while self.at_kw("AND"):
            t = self.advance()
            node = A.BoolOp("AND", node, self.not_cond(), (t.line, t.col))
        return node

    def not_cond(self):
        if self.at_kw("NOT"):
            t = self.advance()
            return A.Not(self.not_cond(), (t.line, t.col))
        return self.cond_primary()

    def cond_primary(self):
        if self.at("LPAREN"):
            # '(' either groups a condition or (never, in this subset) starts an operand
            lp = self.advance()
            c = self.condition()
            self.close_paren(lp)
            return c
        return self.comparison()

    def comparison(self):
        left = self.operand()
        t = self.peek()
        if t.kind != "OP":
            raise self.err(f"expected a comparison operator (=, <>, <, <=, >, >=), found {self.show(t)}")
        self.advance()
        right = self.operand()
        return A.Compare(t.value, left, right, (t.line, t.col))

    def operand(self):
        t = self.peek()
        if t.kind in ("INT", "STRING"):
            self.advance()
            return A.Literal(t.value, (t.line, t.col))
        if self._at_aggregate():
            return self.aggregate()
        if t.kind == "IDENT":
            return self.column()
        raise self.err(f"expected a column, a number or a string, found {self.show(t)}")


def parse_script(src):
    return Parser(src).script()


def parse_one(src):
    stmts = parse_script(src)
    if len(stmts) != 1:
        raise SQLError("syntax", f"expected one statement, found {len(stmts)}")
    return stmts[0]
