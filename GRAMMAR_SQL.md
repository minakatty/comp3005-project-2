# GRAMMAR_SQL.md

Grammar of the SQL subset accepted by Module 3 (`sqlfront/`), in the style of the
Bonus 1 `GRAMMAR.md`. The parser (`sqlfront/parser.py`) has one method per
non-terminal below, with the same names.

## Notation

| Form | Meaning |
|---|---|
| `"x"` | terminal (punctuation or operator, literal text) |
| `KEYWORD` | keyword token, case insensitive |
| `IDENT`, `INT`, `STRING` | token classes produced by the scanner |
| `lower` | non-terminal |
| `{ X }` | zero or more repetitions |
| `[ X ]` | optional |
| `X \| Y` | alternation |

## 1. Lexical grammar

```ebnf
IDENT    ::= ( Letter | "_" ) { Letter | Digit | "_" }      (case sensitive; not a keyword)
INT      ::= [ "-" ] Digit { Digit }
STRING   ::= "'" { AnyCharExceptQuoteOrNewline | "''" } "'"
compop   ::= "=" | "<>" | "<" | "<=" | ">" | ">="           ("!=" accepted as "<>")
comment  ::= "--" { AnyCharExceptNewline }
```

Keywords (reserved, case insensitive): `SELECT DISTINCT FROM WHERE GROUP BY HAVING
ORDER ASC DESC LIMIT UNION ALL INTERSECT EXCEPT JOIN ON AS AND OR NOT CREATE TABLE
INSERT INTO VALUES`.

Maximal munch: on `<` the scanner looks at the next character and makes `<=` or `<>`
if it can, otherwise `<`; the same for `>`/`>=`. A `-` directly followed by a digit
starts a number; the subset has no arithmetic, so `-` means nothing else.

## 2. Statements

```ebnf
script       ::= statement { ";" statement } [ ";" ]
statement    ::= create | insert | query

create       ::= CREATE TABLE IDENT "(" coldef { "," coldef } ")"
coldef       ::= IDENT type
type         ::= IDENT                       (* must spell INT *)
               | IDENT "(" INT ")"           (* must spell VARCHAR, length 1..255 *)

insert       ::= INSERT INTO IDENT VALUES "(" literal { "," literal } ")"
literal      ::= INT | STRING
```

## 3. Queries

```ebnf
query        ::= select_block { setop select_block }
setop        ::= UNION [ ALL ] | INTERSECT | EXCEPT

select_block ::= SELECT [ DISTINCT ] select_list
                 FROM from_clause
                 [ WHERE condition ]
                 [ GROUP BY column { "," column } [ HAVING condition ] ]
                 [ ORDER BY sort_item { "," sort_item } ]
                 [ LIMIT INT ]
               | "(" query ")"

select_list  ::= "*" | select_item { "," select_item }
select_item  ::= ( aggregate | column ) [ AS IDENT ]
aggregate    ::= aggname "(" ( column | "*" ) ")"
aggname      ::= IDENT                       (* COUNT SUM AVG MIN MAX, followed by "(" *)
sort_item    ::= column [ ASC | DESC ]

from_clause  ::= table_ref { "," table_ref }
               | table_ref JOIN table_ref ON condition { JOIN table_ref ON condition }
table_ref    ::= IDENT [ [ AS ] IDENT ]
               | "(" query ")" AS IDENT

column       ::= IDENT [ "." IDENT ]
```

## 4. Conditions

```ebnf
condition    ::= or_cond
or_cond      ::= and_cond { OR and_cond }
and_cond     ::= not_cond { AND not_cond }
not_cond     ::= NOT not_cond | cond_primary
cond_primary ::= "(" condition ")" | comparison
comparison   ::= operand compop operand
operand      ::= column | INT | STRING | aggregate      (* aggregate: HAVING only, checked later *)
```

## 5. Precedence and associativity

### 5.1 Set operators

| Level | Operators | Associativity | Enforcing rule |
|---|---|---|---|
| 1 (tightest) | a `select_block`, `( query )` | - | `select_block` |
| 2 | `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT` | left | `query` |

All set operators share one level, as the spec requires, so
`A UNION B EXCEPT C` is `(A UNION B) EXCEPT C`. The loop in `query` folds each new
block into the tree built so far, which is what makes it left associative.
Parentheses override: `A UNION (B EXCEPT C)` (`test_parenthesised_set_operation`).

### 5.2 Conditions

| Level | Operator | Associativity | Enforcing rule |
|---|---|---|---|
| 1 (tightest) | comparison, `( )` | - | `cond_primary` |
| 2 | `NOT` | right (prefix) | `not_cond` |
| 3 | `AND` | left | `and_cond` |
| 4 (loosest) | `OR` | left | `or_cond` |

`NOT a = 1 AND b = 2 OR c = 3` parses as `((NOT a = 1) AND b = 2) OR c = 3`
(`test_not_and_or_precedence`). Precedence comes from the layering (each rule only
descends to the next tighter one); associativity from the shape of the repetition
(`{ op X }` loops fold left; `NOT` is the one right-recursive rule, safe for
recursive descent because the `NOT` token is consumed before the recursive call).
There is no left recursion anywhere, so recursive descent terminates.

### 5.3 Clause order

Clauses are fixed in position by `select_block`: WHERE, then GROUP BY / HAVING, then
ORDER BY, then LIMIT. ORDER BY and LIMIT bind to the select block they follow, not
to a whole set operation; to sort a union, parenthesise it and select from it as a
subquery.

## 6. Where this grammar differs from the sketch in M3 Section 2, and why

1. **`select_block` can be `( query )`.** The sketch has it; I kept it, and it is the
   only way to group set operations differently.
2. **Bare aliases: `FROM Emp e`.** The sketch's `table_ref ::= ident [ AS ident ]`
   requires `AS`, but the spec's own examples (5.2, 5.5) write `FROM Emp e JOIN Dept d`.
   I accept both. This is unambiguous because the next token after a table name is
   either a keyword, `,`, `)`, `;` or end of input, never an identifier.
3. **Aggregates in HAVING conditions.** The sketch's `operand ::= column | int | string`
   cannot express `HAVING COUNT(*) > 5`, which example 5.4 uses. `operand` therefore
   admits `aggregate`; whether it is allowed is a semantic check (aggregate in WHERE
   or ON is an *aggregate* error, `test_07_aggregate_in_where`), not a syntactic one,
   so the error message can say why.
4. **Aggregate and type names are not reserved.** `COUNT`, `SUM`, `AVG`, `MIN`,
   `MAX`, `INT`, `VARCHAR` scan as identifiers and are recognised by position (an
   aggregate is an identifier followed by `(` in an expression; a type is the second
   identifier of a column definition). This is the Bonus 1 soft-keyword decision
   (GRAMMAR.md 5.3) again, and it keeps a column called `count` or `max` usable.
5. **HAVING requires GROUP BY**, as in the sketch. `SELECT COUNT(*) FROM Emp HAVING ...`
   is a syntax error that says so.
6. **Mixing `,` and `JOIN` in one FROM clause** is rejected with a syntax error. The
   sketch's `from_list` has the two forms as alternatives, never mixed; allowing the
   mix would need a precedence rule between comma and JOIN that the spec does not give.
7. **INTERSECT ALL / EXCEPT ALL** are rejected: the sketch has `ALL` only after `UNION`.
8. **`!=`** is accepted as a synonym of `<>`, because Bonus 1 conditions use `!=`.

## 7. Errors

Every error has one form, `error: <category>: <message> at line L, column C`, with
categories lexical, syntax, name, ambiguity, type, aggregate, schema. A syntax error
for an unclosed parenthesis points at the `(` that was opened, not at end of input
(as in Bonus 1 case 16). Panic mode: the first error stops compilation.
