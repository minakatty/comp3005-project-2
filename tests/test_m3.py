"""Module 3 tests: the fifteen required cases (M3 spec Section 7) and the seven
translation examples of Section 5, compared structurally."""
import unittest

from sqlfront.compiler import catalog_from_dict, compile_sql
from sqlfront.errors import SQLError
from sqlfront.lexer import tokenize
from sqlfront.parser import parse_script

TABLES = {
    "Emp": [("eid", "INT", 0), ("name", "VARCHAR", 20), ("age", "INT", 0), ("did", "INT", 0),
            ("salary", "INT", 0), ("mgr", "INT", 0), ("city", "VARCHAR", 20)],
    "Dept": [("did", "INT", 0), ("title", "VARCHAR", 20), ("budget", "INT", 0), ("city", "VARCHAR", 20)],
    "Proj": [("pid", "INT", 0), ("did", "INT", 0), ("pname", "VARCHAR", 20)],
    "Site": [("sid", "INT", 0), ("pid", "INT", 0), ("city", "VARCHAR", 20)],
}

PHYSICAL = {"hash_join", "sort_merge_join", "nested_loop_join", "seqscan", "filter"}


def plan(sql):
    return compile_sql(sql, catalog_from_dict(TABLES))[0]


def ops(p):
    out = [p["op"]]
    for k in ("child", "left", "right"):
        if k in p:
            out += ops(p[k])
    return out


def nodes(p):
    yield p
    for k in ("child", "left", "right"):
        if k in p:
            yield from nodes(p[k])


class Base(unittest.TestCase):
    def fails(self, sql, category, *needles):
        with self.assertRaises(SQLError) as cm:
            compile_sql(sql, catalog_from_dict(TABLES))
        e = cm.exception
        self.assertEqual(e.category, category, e.render())
        text = e.render()
        self.assertTrue(text.startswith(f"error: {category}: "), text)
        for n in needles:
            self.assertIn(n, text)
        return e

    def assertClean(self, p):
        """Section 9 checklist: no physical names, no unqualified source columns."""
        for n in nodes(p):
            self.assertNotIn(n["op"], PHYSICAL)


class Lexer(unittest.TestCase):
    def test_keywords_case_insensitive_identifiers_not(self):
        a = [t.kind + str(t.value) for t in tokenize("SeLeCt emp FROM Emp")]
        self.assertEqual(a[0], "KWSELECT")
        self.assertEqual(a[1], "IDENTemp")
        self.assertEqual(a[3], "IDENTEmp")

    def test_maximal_munch_and_strings(self):
        toks = [t.value for t in tokenize("a<=b<>c>=d 'O''Brien' -- comment\n-5")][:-1]
        self.assertEqual(toks, ["a", "<=", "b", "<>", "c", ">=", "d", "O'Brien", -5])

    def test_unterminated_string(self):
        with self.assertRaises(SQLError) as cm:
            tokenize("SELECT 'abc")
        self.assertEqual((cm.exception.category, cm.exception.line, cm.exception.col), ("lexical", 1, 8))

    def test_several_statements(self):
        self.assertEqual(len(parse_script("SELECT a FROM t; SELECT b FROM u;")), 2)

    def test_syntax_error_position(self):
        with self.assertRaises(SQLError) as cm:
            parse_script("SELECT a\nFROM t WHERE (a = 1")
        self.assertEqual((cm.exception.category, cm.exception.line), ("syntax", 2))


class SpecExamples(Base):
    def test_5_1_filter_and_projection(self):
        self.assertEqual(plan("SELECT name, age FROM Emp WHERE age > 30;"),
                         {"op": "project", "columns": ["Emp.name", "Emp.age"],
                          "child": {"op": "select", "condition": "Emp.age > 30",
                                    "child": {"op": "scan", "table": "Emp"}}})

    def test_5_2_join_with_aliases(self):
        self.assertEqual(plan("SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did "
                              "WHERE d.budget > 1000;"),
                         {"op": "project", "columns": ["e.name", "d.title"],
                          "child": {"op": "select", "condition": "d.budget > 1000",
                                    "child": {"op": "join", "condition": "e.did = d.did",
                                              "left": {"op": "rename", "to": "e", "child": {"op": "scan", "table": "Emp"}},
                                              "right": {"op": "rename", "to": "d", "child": {"op": "scan", "table": "Dept"}}}}})

    def test_5_3_comma_join(self):
        p = plan("SELECT * FROM Emp e, Dept d WHERE e.did = d.did;")
        self.assertEqual(ops(p), ["project", "select", "cross", "rename", "scan", "rename", "scan"])
        self.assertEqual(p["child"]["condition"], "e.did = d.did")

    def test_5_4_grouping_with_having(self):
        p = plan("SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal FROM Emp WHERE age > 25 "
                 "GROUP BY did HAVING COUNT(*) > 5 ORDER BY n DESC LIMIT 10;")
        self.assertEqual(ops(p), ["limit", "sort", "project", "select", "group_by", "select", "scan"])
        self.assertEqual(p["n"], 10)
        self.assertEqual(p["child"]["keys"], [["n", "desc"]])
        proj = p["child"]["child"]
        self.assertEqual(proj["columns"], ["Emp.did", "n", "avg_sal"])
        self.assertEqual(proj["child"]["condition"], "n > 5")
        gb = proj["child"]["child"]
        self.assertEqual(gb["keys"], ["Emp.did"])
        self.assertEqual(gb["aggregates"], [["count", "*", "n"], ["avg", "Emp.salary", "avg_sal"]])
        self.assertEqual(gb["child"]["condition"], "Emp.age > 25")

    def test_5_5_self_join(self):
        p = plan("SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid;")
        j = p["child"]
        self.assertEqual(j["condition"], "e.mgr = m.eid")
        self.assertEqual((j["left"]["to"], j["right"]["to"]), ("e", "m"))
        self.assertEqual(j["left"]["child"], j["right"]["child"])

    def test_5_6_subquery_in_from(self):
        p = plan("SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t WHERE t.n > 3;")
        self.assertEqual(ops(p), ["project", "select", "rename", "project", "group_by", "scan"])
        self.assertEqual(p["columns"], ["t.did", "t.n"])
        self.assertEqual(p["child"]["child"]["child"]["columns"], ["Emp.did", "n"])

    def test_5_7_the_two_unions(self):
        a = plan("SELECT city FROM Emp UNION ALL SELECT city FROM Dept;")
        b = plan("SELECT city FROM Emp UNION SELECT city FROM Dept;")
        self.assertEqual(a["op"], "union_all")
        self.assertEqual(b["op"], "union")
        self.assertEqual(a["left"], b["left"])
        self.assertEqual(a["right"], b["right"])


class RequiredCases(Base):
    def test_01_self_join_two_renames(self):
        p = plan("SELECT e.name FROM Emp e JOIN Emp m ON e.mgr = m.eid")
        self.assertEqual(ops(p).count("rename"), 2)
        self.assertEqual(ops(p).count("scan"), 2)

    def test_02_star_over_three_tables_sharing_a_column(self):
        p = plan("SELECT * FROM Emp, Dept, Proj")
        cols = p["columns"]
        self.assertIn("Emp.did", cols)
        self.assertIn("Dept.did", cols)
        self.assertIn("Proj.did", cols)
        self.assertEqual(len(cols), 7 + 4 + 3)
        self.assertEqual(cols[:2], ["Emp.eid", "Emp.name"])   # FROM order, then schema order

    def test_03_ambiguous_unqualified(self):
        self.fails("SELECT did FROM Emp, Dept", "ambiguity", "Emp.did", "Dept.did", "line 1, column 8")

    def test_04_union_vs_union_all(self):
        a = plan("SELECT name FROM Emp UNION SELECT title FROM Dept")
        b = plan("SELECT name FROM Emp UNION ALL SELECT title FROM Dept")
        self.assertNotEqual(a, b)
        self.assertEqual((a["op"], b["op"]), ("union", "union_all"))

    def test_05_where_and_having_on_opposite_sides(self):
        p = plan("SELECT did, COUNT(*) FROM Emp WHERE age > 30 GROUP BY did HAVING COUNT(*) > 2")
        seq = ops(p)
        self.assertEqual(seq, ["project", "select", "group_by", "select", "scan"])
        self.assertEqual(p["child"]["condition"], "count_star > 2")
        self.assertEqual(p["child"]["child"]["child"]["condition"], "Emp.age > 30")

    def test_06_non_grouped_column(self):
        self.fails("SELECT name, COUNT(*) FROM Emp GROUP BY did", "aggregate", "Emp.name")

    def test_07_aggregate_in_where(self):
        self.fails("SELECT did FROM Emp WHERE COUNT(*) > 3 GROUP BY did", "aggregate", "WHERE")

    def test_08_subquery_joined_to_base_table(self):
        p = plan("SELECT d.title, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t "
                 "JOIN Dept d ON t.did = d.did")
        j = p["child"]
        self.assertEqual(j["op"], "join")
        self.assertEqual(j["condition"], "t.did = d.did")
        self.assertEqual(j["left"]["op"], "rename")
        self.assertEqual(j["left"]["child"]["op"], "project")

    def test_09_order_by_column_not_in_select_list(self):
        p = plan("SELECT name FROM Emp ORDER BY age DESC")
        self.assertEqual(ops(p), ["project", "sort", "project", "scan"])
        self.assertEqual(p["columns"], ["Emp.name"])
        self.assertEqual(p["child"]["keys"], [["Emp.age", "desc"]])

    def test_10_type_error(self):
        self.fails("SELECT name FROM Emp WHERE age > '30'", "type", "INT", "VARCHAR")

    def test_11_unknown_table_and_unknown_column_differ(self):
        a = self.fails("SELECT name FROM Nope", "name", "unknown table 'Nope'")
        b = self.fails("SELECT nope FROM Emp", "name", "unknown column 'nope'")
        self.assertNotEqual(a.message, b.message)
        self.fails("SELECT Emp.nope FROM Emp", "name", "no column 'nope'")
        self.fails("SELECT x.name FROM Emp", "name", "unknown table or alias 'x'")

    def test_12_duplicate_alias(self):
        self.fails("SELECT * FROM Emp, Emp", "name", "duplicate")
        plan("SELECT * FROM Emp e, Emp m")

    def test_13_count_star_without_group_by(self):
        p = plan("SELECT COUNT(*) FROM Emp")
        self.assertEqual(ops(p), ["project", "group_by", "scan"])
        self.assertEqual(p["child"]["keys"], [])
        self.assertEqual(p["columns"], ["count_star"])

    def test_14_union_type_mismatch(self):
        self.fails("SELECT name FROM Emp UNION SELECT did FROM Dept", "schema", "column 1")
        self.fails("SELECT name, age FROM Emp UNION SELECT title FROM Dept", "schema", "same number")

    def test_15_four_table_chain_commas_vs_join_on(self):
        a = plan("SELECT e.name, s.city FROM Emp e, Dept d, Proj p, Site s "
                 "WHERE e.did = d.did AND d.did = p.did AND p.pid = s.pid")
        b = plan("SELECT e.name, s.city FROM Emp e JOIN Dept d ON e.did = d.did "
                 "JOIN Proj p ON d.did = p.did JOIN Site s ON p.pid = s.pid")
        self.assertEqual(ops(a), ["project", "select", "cross", "cross", "cross",
                                  "rename", "scan", "rename", "scan", "rename", "scan", "rename", "scan"])
        self.assertEqual(ops(b), ["project", "join", "join", "join",
                                  "rename", "scan", "rename", "scan", "rename", "scan", "rename", "scan"])
        # the leaves are the same, only where the predicates sit differs
        leaves = lambda p: [n for n in nodes(p) if n["op"] == "rename"]
        self.assertEqual(leaves(a), leaves(b))


class Semantics(Base):
    def test_no_unqualified_source_column_survives(self):
        p = plan("SELECT name FROM Emp WHERE age > 3 AND city = 'x' ORDER BY did")
        text = str(p)
        for bare in ("'name'", "'age >", "'did'"):
            self.assertNotIn(bare, text)

    def test_distinct_above_projection_only_when_written(self):
        self.assertEqual(ops(plan("SELECT DISTINCT did FROM Emp"))[:2], ["distinct", "project"])
        self.assertNotIn("distinct", ops(plan("SELECT did FROM Emp")))

    def test_distinct_over_group_by_is_not_emitted(self):
        self.assertNotIn("distinct", ops(plan("SELECT DISTINCT did, COUNT(*) FROM Emp GROUP BY did")))
        # but it is kept when the grouping column is not in the output
        self.assertIn("distinct", ops(plan("SELECT DISTINCT COUNT(*) FROM Emp GROUP BY did")))

    def test_intersect_except_and_left_associativity(self):
        p = plan("SELECT city FROM Emp UNION SELECT city FROM Dept EXCEPT SELECT city FROM Site")
        self.assertEqual(p["op"], "except")
        self.assertEqual(p["left"]["op"], "union")

    def test_parenthesised_set_operation(self):
        p = plan("SELECT city FROM Emp UNION (SELECT city FROM Dept EXCEPT SELECT city FROM Site)")
        self.assertEqual((p["op"], p["right"]["op"]), ("union", "except"))

    def test_aggregate_naming_rule(self):
        p = plan("SELECT did, MAX(salary), MIN(name) FROM Emp GROUP BY did")
        self.assertEqual(p["columns"], ["Emp.did", "max_Emp_salary", "min_Emp_name"])

    def test_sum_avg_need_int(self):
        self.fails("SELECT AVG(name) FROM Emp", "type", "AVG")

    def test_having_non_group_column(self):
        self.fails("SELECT did FROM Emp GROUP BY did HAVING age > 3", "aggregate")

    def test_order_by_output_alias(self):
        p = plan("SELECT name AS who FROM Emp ORDER BY who")
        self.assertEqual(p["keys"], [["who", "asc"]])
        self.assertEqual(p["child"]["as"], ["who"])

    def test_not_and_or_precedence(self):
        p = plan("SELECT name FROM Emp WHERE NOT age = 1 AND did = 2 OR city = 'x'")
        self.assertEqual(p["child"]["condition"],
                         "not Emp.age = 1 and Emp.did = 2 or Emp.city = 'x'")

    def test_create_and_insert(self):
        ps = compile_sql("CREATE TABLE T (a INT, b VARCHAR(5)); INSERT INTO T VALUES (1, 'x'); "
                         "SELECT b FROM T", catalog_from_dict(TABLES))
        self.assertEqual(ps[0], {"op": "create_table", "table": "T", "columns": [["a", "INT"], ["b", "VARCHAR", 5]]})
        self.assertEqual(ps[1], {"op": "insert", "table": "T", "values": [1, "x"]})
        self.assertEqual(ps[2]["columns"], ["T.b"])
        self.fails("INSERT INTO Dept VALUES ('x', 'y', 1, 'z')", "type")

    def test_logical_plans_are_clean(self):
        for q in ("SELECT * FROM Emp e JOIN Dept d ON e.did = d.did", "SELECT COUNT(*) FROM Emp"):
            self.assertClean(plan(q))


if __name__ == "__main__":
    unittest.main()
