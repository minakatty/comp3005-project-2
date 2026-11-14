"""Project 2 week-one checks: standalone condition parser, JSON round trip, providers, bags."""
import contextlib, io, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ralang import ast_nodes as ast                                   # noqa: E402
from ralang.ast_nodes import tree_signature                           # noqa: E402
from ralang.catalog import parse_catalog                              # noqa: E402
from ralang.errors import SyntaxError_, SchemaError                   # noqa: E402
from ralang.instrument import Counters                                # noqa: E402
from ralang.operators import evaluate                                 # noqa: E402
from ralang.parser import parse_condition, parse_query, parse_attr    # noqa: E402
from ralang.plan_json import to_json, from_json, unparse_condition, PlanError  # noqa: E402
from ralang.provider import DictProvider, StorageProvider, _ProviderBase       # noqa: E402
from ralang.relation import Relation                                  # noqa: E402
from ralang.schema import Attribute, ColType, Schema                  # noqa: E402

DATA = """
R (a, b) = {
  1, 10
  2, 20
  3, 20
}
S (b, c) = {
  10, 100
  20, 200
}
"""


def rows(query, src=DATA):
    return evaluate(parse_query(query), parse_catalog(src), Counters()).rows


class ConditionParser(unittest.TestCase):
    def test_standalone_condition(self):
        self.assertEqual(parse_condition("R.b = S.b").label(), "Eq(Attr(R.b), Attr(S.b))")

    def test_precedence_matches_the_grammar(self):
        self.assertEqual(parse_condition("a=1 and b=2 or c=3").label(),
                         "Or(And(Eq(Attr(a), Num(1)), Eq(Attr(b), Num(2))), Eq(Attr(c), Num(3)))")

    def test_trailing_input_is_a_syntax_error(self):
        with self.assertRaises(SyntaxError_) as e:
            parse_condition("a = 1 b")
        self.assertIn("after a complete condition", e.exception.message)

    def test_incomplete_condition(self):
        with self.assertRaises(SyntaxError_):
            parse_condition("a =")

    def test_parse_attr(self):
        self.assertEqual(parse_attr("Emp.DID").qualified(), "Emp.DID")
        with self.assertRaises(SyntaxError_):
            parse_attr("a b")


class Unparse(unittest.TestCase):
    CONDS = ["R.a > 500", "a=1 and b=2 or c=3", "not (a=1 and b=2) or c>3", "a=1 and (b=2 or c=3)",
             "a=1 or (b=2 or c=3)", "not not a=1", "Name='O''Brien'", "Age>-30", "not=1", "a=1 and and=2",
             "x >= 3.5"]

    def test_reparse_gives_same_tree(self):
        for s in self.CONDS:
            c = parse_condition(s)
            again = parse_condition(unparse_condition(c))
            self.assertEqual(again.label(), c.label(), s)

    def test_condition_strings_match_the_physical_plan_style(self):
        self.assertEqual(unparse_condition(parse_condition("R.b=S.b")), "R.b = S.b")


QUERIES = ["R", "select[R.a > 1 or not R.b = 10](R)", "project[R.a, b](R)", "rename[Q](R)",
           "R join[R.b = S.b] S", "R times S", "distinct(project[b](R))",
           "R union R", "R intersect R", "R minus R"]


class JsonRoundTrip(unittest.TestCase):
    def check(self, tree):
        j = json.loads(json.dumps(to_json(tree)))
        back = from_json(j)
        self.assertEqual(tree_signature(back), tree_signature(tree))
        self.assertEqual(to_json(back), j)
        return j

    def test_every_syntax_form(self):
        for q in QUERIES:
            self.check(parse_query(q))

    def test_every_json_op_is_covered(self):
        seen = set()
        def walk(d):
            seen.add(d["op"])
            for k in ("child", "left", "right"):
                if k in d:
                    walk(d[k])
        trees = [parse_query(q) for q in QUERIES]
        trees += [ast.Union(ast.Relation("R"), ast.Relation("R"), (0, 0), True),
                  ast.Intersect(ast.Relation("R"), ast.Relation("R"), (0, 0), True),
                  ast.Minus(ast.Relation("R"), ast.Relation("R"), (0, 0), True)]
        for t in trees:
            walk(self.check(t))
        self.assertEqual(seen, {"scan", "select", "project", "rename", "join", "distinct",
                                "union", "intersect", "except"})

    def test_all_flag_survives(self):
        t = ast.Minus(ast.Relation("R"), ast.Relation("S"), (0, 0), True)
        self.assertTrue(from_json(to_json(t)).all)
        self.assertEqual(to_json(t)["op"], "except")

    def test_times_is_join_with_null_condition(self):
        j = to_json(parse_query("R times S"))
        self.assertEqual((j["op"], j["condition"]), ("join", None))
        self.assertIsInstance(from_json(j), ast.Times)

    def test_join_condition_text_matches_handout_example(self):
        j = to_json(parse_query("R join[R.b = S.b] S"))
        self.assertEqual(j["condition"], "R.b = S.b")

    def test_plan_validation_messages_name_the_node(self):
        cases = [({"op": "select", "child": {"op": "scan", "table": "R"}},
                  "Node 'select': missing required field 'condition'."),
                 ({"op": "select", "condition": 5, "child": {"op": "scan", "table": "R"}},
                  "Node 'select': field 'condition' must be a string."),
                 ({"op": "merge_join"}, "Node 'merge_join': unknown operator 'merge_join'."),
                 ({"op": "project", "columns": [], "child": {"op": "scan", "table": "R"}},
                  "Node 'project': field 'columns' must be a non-empty list of strings.")]
        for bad, msg in cases:
            with self.assertRaises(PlanError) as e:
                from_json(bad)
            self.assertEqual(str(e.exception), msg)


class Bags(unittest.TestCase):
    def test_project_keeps_duplicates(self):
        self.assertEqual(sorted(rows("project[b](R)")), [(10,), (20,), (20,)])

    def test_distinct_is_separate(self):
        self.assertEqual(sorted(rows("distinct(project[b](R))")), [(10,), (20,)])

    def test_distinct_of_a_set_changes_nothing(self):
        self.assertEqual(sorted(rows("distinct(R)")), sorted(rows("R")))

    def test_set_versions_still_dedup_even_for_bag_inputs(self):
        a, b = "project[b](R)", "project[b](R)"
        self.assertEqual(sorted(rows(f"{a} union {b}")), [(10,), (20,)])
        self.assertEqual(sorted(rows(f"{a} intersect {b}")), [(10,), (20,)])
        self.assertEqual(sorted(rows(f"{a} minus project[b](S)")), [])

    def _all(self, cls, left, right):
        cat = parse_catalog(DATA)
        n = cls(parse_query(left), parse_query(right), (0, 0), True)
        return sorted(evaluate(n, cat, Counters()).rows)

    def test_bag_versions(self):
        l, r = "project[b](R)", "project[b](S)"       # {10,20,20} and {10,20}
        self.assertEqual(self._all(ast.Union, l, r), [(10,), (10,), (20,), (20,), (20,)])
        self.assertEqual(self._all(ast.Intersect, l, r), [(10,), (20,)])
        self.assertEqual(self._all(ast.Minus, l, r), [(20,)])
        self.assertEqual(self._all(ast.Minus, r, l), [])

    def test_times_and_join_are_bags(self):
        self.assertEqual(len(rows("project[S.c](R join[R.b = S.b] S)")), 3)


class Providers(unittest.TestCase):
    def test_dict_and_provider_agree(self):
        cat = parse_catalog(DATA)
        q = parse_query("R join[R.b = S.b] S")
        self.assertEqual(sorted(evaluate(q, cat, Counters()).rows),
                         sorted(evaluate(q, DictProvider(cat), Counters()).rows))

    def test_rows_really_come_from_scan(self):
        class P(_ProviderBase):
            def __init__(self): self.scans = []
            def schema(self, name):
                if name != "T":
                    raise KeyError(name)
                return Schema([Attribute("T", "x")], [ColType.NUMBER])
            def scan(self, name):
                self.scans.append(name)
                yield (7,)
                yield (8,)
        p = P()
        out = evaluate(parse_query("select[x > 7](T)"), p, Counters())
        self.assertEqual(out.rows, [(8,)])
        self.assertEqual(p.scans, ["T"])

    def test_unknown_table_through_a_provider(self):
        from ralang.errors import NameError_
        with self.assertRaises(NameError_):
            evaluate(parse_query("Nope"), DictProvider(parse_catalog(DATA)), Counters())

    def test_storage_provider_over_the_m1_engine(self):
        try:
            from storage.db import Database
        except ImportError:
            self.skipTest("M1 storage package not on the path")
        import tempfile
        db = Database(tempfile.mkdtemp(), frames=4)
        from storage.record import parse_schema
        db.create_table("R", parse_schema("a:INT,b:VARCHAR(10)"))
        h = db.heap("R")
        for i in range(5):
            h.insert((i, "x" if i % 2 else "y"))
        p = StorageProvider(db)
        self.assertEqual(p.schema("R").types, [ColType.NUMBER, ColType.STRING])
        out = evaluate(parse_query("project[b](select[a >= 1](R))"), p, Counters())
        self.assertEqual(sorted(out.rows), [("x",), ("x",), ("y",), ("y",)])   # bag: 4 rows, 2 distinct
        with self.assertRaises(Exception):
            evaluate(parse_query("select[b > 3](R)"), p, Counters())           # string vs number: type error
        db.close()


class SoftKeywordDistinct(unittest.TestCase):
    def test_distinct_as_attribute_and_as_relation(self):
        self.assertEqual(parse_condition("distinct=1").label(), "Eq(Attr(distinct), Num(1))")
        self.assertEqual(tree_signature(parse_query("distinct union B")),
                         "Union[Relation(distinct), Relation(B)]")
        self.assertEqual(tree_signature(parse_query("distinct(A)")), "Distinct[Relation(A)]")


class Hygiene(unittest.TestCase):
    def test_library_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            for q in QUERIES:
                try:
                    evaluate(parse_query(q), parse_catalog(DATA), Counters())
                except SchemaError:
                    pass                     # e.g. R minus R is fine; R times S / self joins may collide
                to_json(parse_query(q))
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
