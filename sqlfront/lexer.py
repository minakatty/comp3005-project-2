"""Hand-written SQL scanner (M3 spec Section 1). No regular expressions.

Keywords are case insensitive and come out as KW tokens with an upper-case value.
Identifiers are case sensitive. Aggregate names (COUNT, SUM, AVG, MIN, MAX) and the
type names (INT, VARCHAR) are *not* reserved: they scan as IDENT and the parser
recognises them by position, exactly like the soft keywords of Bonus Project 1
(GRAMMAR_SQL.md 4.2). So a column named `count` still works.
"""
from dataclasses import dataclass

from .errors import SQLError

KEYWORDS = {
    "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER", "ASC", "DESC",
    "LIMIT", "UNION", "ALL", "INTERSECT", "EXCEPT", "JOIN", "ON", "AS", "AND", "OR", "NOT",
    "CREATE", "TABLE", "INSERT", "INTO", "VALUES",
}

PUNCT = {"(": "LPAREN", ")": "RPAREN", ",": "COMMA", ".": "DOT", "*": "STAR", ";": "SEMI"}


@dataclass
class Tok:
    kind: str          # KW IDENT INT STRING OP LPAREN RPAREN COMMA DOT STAR SEMI EOF
    value: object
    text: str
    line: int
    col: int

    def is_kw(self, *words):
        return self.kind == "KW" and self.value in words


def tokenize(src):
    toks, i, line, col, n = [], 0, 1, 1, len(src)

    def err(msg, l, c):
        raise SQLError("lexical", msg, l, c)

    while i < n:
        ch = src[i]
        if ch == "\n":
            i, line, col = i + 1, line + 1, 1
            continue
        if ch in " \t\r":
            i, col = i + 1, col + 1
            continue
        if ch == "-" and i + 1 < n and src[i + 1] == "-":          # comment to end of line
            while i < n and src[i] != "\n":
                i += 1
            continue
        sl, sc = line, col
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            if word.upper() in KEYWORDS:
                toks.append(Tok("KW", word.upper(), word, sl, sc))
            else:
                toks.append(Tok("IDENT", word, word, sl, sc))
            col += j - i
            i = j
            continue
        if ch.isdigit() or (ch == "-" and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and src[j].isdigit():
                j += 1
            if j < n and (src[j].isalpha() or src[j] == "_"):
                err(f"malformed number '{src[i:j + 1]}'", sl, sc)
            if j < n and src[j] == ".":
                err("only integers are supported", sl, sc)
            toks.append(Tok("INT", int(src[i:j]), src[i:j], sl, sc))
            col += j - i
            i = j
            continue
        if ch == "'":
            j, buf = i + 1, []
            while True:
                if j >= n or src[j] == "\n":
                    err("unterminated string literal", sl, sc)
                if src[j] == "'":
                    if j + 1 < n and src[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(src[j])
                j += 1
            toks.append(Tok("STRING", "".join(buf), src[i:j + 1], sl, sc))
            col += j + 1 - i
            i = j + 1
            continue
        if ch in "<>=!":
            nxt = src[i + 1] if i + 1 < n else ""
            if ch == "<" and nxt in "=>":
                op = ch + nxt
            elif ch == ">" and nxt == "=":
                op = ">="
            elif ch == "!" and nxt == "=":
                op = "<>"                       # accepted as a synonym for <>
            elif ch == "!":
                err("unexpected character '!' (not-equal is written <>)", sl, sc)
            else:
                op = ch
            ln = 2 if ch == "!" else len(op)
            toks.append(Tok("OP", op, src[i:i + ln], sl, sc))
            i, col = i + ln, col + ln
            continue
        if ch in PUNCT:
            toks.append(Tok(PUNCT[ch], ch, ch, sl, sc))
            i, col = i + 1, col + 1
            continue
        err(f"unexpected character {ch!r}", sl, sc)
    toks.append(Tok("EOF", None, "", line, col))
    return toks
