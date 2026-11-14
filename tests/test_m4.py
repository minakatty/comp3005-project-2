"""Module 4 tests: the checklist of M4 spec Section 8."""
import json
import random
import shutil
import tempfile
import unittest

from execution.engine import execute
from optimizer.estimate import Estimator, Stats
from optimizer.logical import props
from optimizer.optimize import naive_physical, optimize
from optimizer.stats import analyze
from sqlfront.compiler import catalog_from_db, catalog_from_dict, compile_sql
from storage.db import Database
from storage.record import parse_schema


def col(ndv, lo=0, hi=None):
    return {"type": "INT", "ndv": ndv, "min": lo, "max": ndv if hi is None else hi, "avg_width": 4.0, "hist": []}


DP_STATS = {
    "A": {"schema": [("x", "INT", 0), ("pa", "VARCHAR", 37)], "n_tuples": 5000, "n_pages": 100,
          "width": 40.96, "columns": {"x": col(2500)}},
    "Bp": {"schema": [("x", "INT", 0), ("y", "INT", 0), ("pb", "VARCHAR", 33)], "n_tuples": 500,
           "n_pages": 10, "width": 40.96, "columns": {"x": col(500), "y": col(500)}},
    "C": {"schema": [("y", "INT", 0), ("pc", "VARCHAR", 37)], "n_tuples": 20000, "n_pages": 400,
          "width": 40.96, "columns": {"y": col(5000)}},
}

EMP_STATS = {
    "Emp": {"schema": [("eid", "INT", 0), ("name", "VARCHAR", 20), ("age", "INT", 0), ("did", "INT", 0)],
            "n_tuples": 10000, "n_pages": 200, "width": 30,
            "columns": {"did": col(50), "age": col(51, 20, 70)}},
    "Dept": {"schema": [("did", "INT", 0), ("budget", "INT", 0), ("title", "VARCHAR", 10)],
             "n_tuples": 50, "n_pages": 1, "width": 20, "columns": {"did": col(50)}},
}


def compile_with(stats, sql):
    return compile_sql(sql, catalog_from_dict({k: v["schema"] for k, v in stats.items()}))[0]


def ops(p):
    out = [p["op"]]
    for k in ("child", "left", "right"):
        if k in p:
            out += ops(p[k])
    return out


class Selectivity(unittest.TestCase):
    def rows(self, sql):
        lp = compile_with(EMP_STATS, sql)
        return props(lp["child"], Estimator(Stats(override=EMP_STATS))).rows

    def test_section_2_worked_example(self):
        self.assertEqual(self.rows("SELECT * FROM Emp WHERE did = 7"), 200)
        self.assertEqual(self.rows("SELECT * FROM Emp WHERE age > 60"), 2000)
        self.assertEqual(self.rows("SELECT * FROM Emp WHERE did = 7 AND age > 60"), 40)
        self.assertEqual(self.rows("SELECT * FROM Emp JOIN Dept ON Emp.did = Dept.did"), 10000)

    def test_or_not_and_ne(self):
        e = Estimator(Stats(override=EMP_STATS))
        from ralang.parser import parse_condition
        lp = compile_with(EMP_STATS, "SELECT * FROM Emp")["child"]
        base = props(lp, e)
        sel = lambda t: e.selectivity(parse_condition(t), base)
        self.assertAlmostEqual(sel("Emp.did = 7 or Emp.age > 60"), 0.02 + 0.2 - 0.004)
        self.assertAlmostEqual(sel("not Emp.did = 7"), 0.98)
        self.assertAlmostEqual(sel("Emp.did != 7"), 0.98)

    def test_never_below_one_row(self):
        self.assertEqual(self.rows("SELECT * FROM Emp WHERE age > 9999"), 1)


class RewriteExample(unittest.TestCase):
    def test_section_3_worked_example(self):
        lp = compile_with(EMP_STATS, "SELECT e.name FROM Emp e, Dept d "
                                     "WHERE e.did = d.did AND e.age > 60 AND d.budget > 1000")
        rw, _ = optimize(lp, Stats(override=EMP_STATS), 32)
        self.assertEqual(rw, {
            "op": "project", "columns": ["e.name"],
            "child": {"op": "join", "condition": "e.did = d.did",
                      "left": {"op": "project", "columns": ["e.name", "e.did"],
                               "child": {"op": "select", "condition": "e.age > 60",
                                         "child": {"op": "rename", "to": "e", "child": {"op": "scan", "table": "Emp"}}}},
                      "right": {"op": "project", "columns": ["d.did"],
                                "child": {"op": "select", "condition": "d.budget > 1000",
                                          "child": {"op": "rename", "to": "d", "child": {"op": "scan", "table": "Dept"}}}}}})

    def test_rule5_where_on_group_key_moves_below_group_by_but_having_never(self):
        lp = compile_with(EMP_STATS, "SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t "
                                     "WHERE t.did = 3 AND t.n > 2")
        rw, _ = optimize(lp, Stats(override=EMP_STATS), 32)
        seq = ops(rw)
        gb = seq.index("group_by")
        self.assertIn("select", seq[gb + 1:])        # did = 3 went below the group_by
        self.assertIn("select", seq[:gb])            # n > 2 stayed above it

    def test_disabling_a_rule(self):
        lp = compile_with(EMP_STATS, "SELECT e.name FROM Emp e, Dept d WHERE e.did = d.did AND e.age > 60")
        rw, _ = optimize(lp, Stats(override=EMP_STATS), 32, disabled=(3,))
        self.assertNotIn("join", ops(rw))
        self.assertIn("cross", ops(rw))
        rw, _ = optimize(lp, Stats(override=EMP_STATS), 32, disabled=(2,))
        self.assertEqual(ops(rw)[:2], ["project", "select"])   # e.age > 60 stays on top


class DPExample(unittest.TestCase):
    def test_section_5_1(self):
        lp = compile_with(DP_STATS, "SELECT * FROM A, Bp, C WHERE A.x = Bp.x AND Bp.y = C.y")
        log = []
        _, root = optimize(lp, Stats(override=DP_STATS), 32, dp_log=log)
        table = {tuple(S): rows for S, rows in log}
        best = lambda *S: next(int(c) for c, _, b in table[S] if b)
        self.assertEqual(best("A"), 100)
        self.assertEqual(best("Bp"), 10)
        self.assertEqual(best("C"), 400)
        self.assertEqual(best("A", "Bp"), 110)
        self.assertEqual(best("Bp", "C"), 410)
        self.assertIsNone(table[("A", "C")])               # no predicate: skipped
        self.assertEqual(best("A", "Bp", "C"), 530)
        self.assertEqual(root.cost, 530)
        # the winner joins Bp to A first, Bp as the outer
        j = root.children[0]
        self.assertEqual((root.op, j.op), ("nested_loop_join", "nested_loop_join"))
        self.assertEqual(j.children[0].fields["table"], "Bp")
        alt = [int(c) for c, d, _ in table[("A", "Bp", "C")] if d.startswith("nested_loop_join outer={Bp,C}")]
        # spec prints 550 = 410 + 40 + 100; the formula gives ceil(40/30) = 2 blocks
        self.assertEqual(alt, [410 + 40 + 2 * 100])


class Live(unittest.TestCase):
    """Against a real database: plans run unedited in Module 2."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        random.seed(2)
        db = Database(cls.dir, 64)
        db.create_table("Emp", parse_schema("eid:INT,name:VARCHAR(12),age:INT,did:INT,salary:INT"))
        db.create_table("Dept", parse_schema("did:INT,title:VARCHAR(12),budget:INT"))
        db.create_table("Proj", parse_schema("pid:INT,did:INT,pname:VARCHAR(12)"))
        h = db.heap("Emp")
        for i in range(3000):
            h.insert((i, f"n{i % 400}", 20 + i % 50, i % 40, 1000 + (i * 37) % 5000))
        db.catalog.update_counts("Emp", db.count_data_pages("Emp"), 3000)
        h = db.heap("Dept")
        for d in range(40):
            h.insert((d, f"t{d}", d * 100))
        db.catalog.update_counts("Dept", db.count_data_pages("Dept"), 40)
        h = db.heap("Proj")
        for p in range(300):
            h.insert((p, p % 40, f"p{p}"))
        db.catalog.update_counts("Proj", db.count_data_pages("Proj"), 300)
        cls.stats_before = None
        for t in ("Emp", "Dept"):
            analyze(db, t)
        db.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_sql(self, sql, B=16, disabled=()):
        db = Database(self.dir, B)
        try:
            lp = compile_sql(sql, catalog_from_db(db))[0]
            _, root = optimize(lp, Stats(db), B, disabled)
            phys = root.json()
            json.loads(json.dumps(phys))          # it is plain JSON
            opt = execute(db, phys, B)
            naive = execute(db, naive_physical(lp), B)
        finally:
            db.close()
        self.assertEqual(sorted(opt.rows), sorted(naive.rows), sql)
        return root, opt, naive

    QUERIES = [
        "SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did WHERE d.budget > 1000 AND e.age > 60",
        "SELECT e.name, p.pname FROM Emp e, Dept d, Proj p WHERE e.did = d.did AND d.did = p.did AND e.salary > 5500",
        "SELECT did, COUNT(*) AS n, AVG(salary) AS a FROM Emp WHERE age > 30 GROUP BY did HAVING COUNT(*) > 10",
        "SELECT DISTINCT did FROM Emp WHERE age < 25",
        "SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t JOIN Dept d ON t.did = d.did "
        "WHERE t.did < 10",
        "SELECT name FROM Emp WHERE age = 33 UNION SELECT title FROM Dept",
        "SELECT name FROM Emp ORDER BY salary DESC LIMIT 5",
        "SELECT a.name, b.name FROM Emp a JOIN Emp b ON a.eid = b.salary WHERE a.age > 65",
    ]

    def test_every_plan_runs_and_matches_naive(self):
        for q in self.QUERIES:
            for B in (4, 16):
                self.run_sql(q, B)

    def test_optimized_beats_naive_on_a_join(self):
        _, opt, naive = self.run_sql(self.QUERIES[1], 16)
        io = lambda r: r.totals["page_reads"] + r.totals["page_writes"]
        self.assertLess(io(opt), io(naive))

    def test_commas_and_join_on_give_the_same_physical_plan(self):
        db = Database(self.dir, 16)
        a = compile_sql("SELECT e.name FROM Emp e, Dept d, Proj p WHERE e.did = d.did AND d.did = p.did",
                        catalog_from_db(db))[0]
        b = compile_sql("SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did JOIN Proj p ON d.did = p.did",
                        catalog_from_db(db))[0]
        pa = optimize(a, Stats(db), 16)[1].json()
        pb = optimize(b, Stats(db), 16)[1].json()
        db.close()
        self.assertEqual(pa, pb)

    def test_changing_B_changes_the_join_algorithm(self):
        db = Database(self.dir, 16)
        lp = compile_sql("SELECT a.name FROM Emp a JOIN Emp b ON a.eid = b.salary", catalog_from_db(db))[0]
        algos = set()
        for B in (3, 4, 8, 32, 128):
            algos.add(tuple(n.op for n in optimize(lp, Stats(db), B)[1].walk() if n.op.endswith("join")))
        db.close()
        self.assertGreater(len(algos), 1)

    def test_analyze_twice_changes_nothing(self):
        db = Database(self.dir, 16)
        a = json.dumps(db.catalog.get_stats("Emp"), sort_keys=True)
        analyze(db, "Emp")
        b = json.dumps(db.catalog.get_stats("Emp"), sort_keys=True)
        db.close()
        self.assertEqual(a, b)

    def test_histogram_is_equi_depth(self):
        db = Database(self.dir, 16)
        st = db.catalog.get_stats("Emp")
        db.close()
        counts = [b["count"] for b in st["columns"]["age"]["hist"]]
        self.assertEqual(len(counts), 20)
        self.assertLessEqual(max(counts) - min(counts), 1)
        self.assertEqual(st["columns"]["did"]["ndv"], 40)

    def test_no_statistics_still_plans(self):
        root, _, _ = self.run_sql("SELECT p.pname FROM Proj p JOIN Dept d ON p.did = d.did WHERE p.pid > 100")
        self.assertTrue(root.est.rows >= 1)


if __name__ == "__main__":
    unittest.main()
