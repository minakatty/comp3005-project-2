"""Module 2 tests: the checklist in M2 spec Section 6, plus plan validation."""
import random
import shutil
import tempfile
import unittest

from execution.engine import execute
from execution.errors import ExecError, PlanError
from execution.plan import load_plan_text, validate, walk
from storage.db import Database
from storage.record import parse_schema

JOINS = ("nested_loop_join", "sort_merge_join", "hash_join")


def scan(t, alias=None):
    d = {"op": "seqscan", "table": t}
    if alias:
        d["alias"] = alias
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def table(self, name, schema, rows):
        db = Database(self.dir, 64)
        db.create_table(name, parse_schema(schema))
        h = db.heap(name)
        for r in rows:
            h.insert(r)
        db.catalog.update_counts(name, db.count_data_pages(name), len(rows))
        db.close()

    def run_plan(self, plan, B=8, policy="lru"):
        db = Database(self.dir, B, policy)
        try:
            res = execute(db, plan, B)
        finally:
            db.close()
        # nothing may be left behind (checklist item 8)
        self.assertEqual(res.leftover_pins, 0, "pinned frames left after close")
        self.assertEqual(res.leftover_reserved, 0, "reserved frames left after close")
        self.assertEqual(res.leftover_temps, [], "temporary files left after close")
        self.assertLessEqual(res.peak_frames, B, "held more than B frames")
        return res


class DuplicateKeys(Base):
    def setUp(self):
        super().setUp()
        self.table("R", "a:INT,b:INT", [(1, 1), (2, 2), (3, 2), (4, 3)])
        self.table("S", "b:INT,c:INT", [(2, 10), (2, 20), (4, 30)])

    def test_section_2_8_four_rows_every_join(self):
        for j in JOINS:
            for B in (4, 8):
                res = self.run_plan({"op": j, "condition": "R.b = S.b", "left": scan("R"), "right": scan("S")}, B)
                self.assertEqual(sorted(res.rows), [(2, 2, 2, 10), (2, 2, 2, 20), (3, 2, 2, 10), (3, 2, 2, 20)], j)
                self.assertEqual(res.schema, [["R.a", "INT"], ["R.b", "INT"], ["S.b", "INT"], ["S.c", "INT"]])


class Joins(Base):
    def setUp(self):
        super().setUp()
        random.seed(7)
        self.table("R", "a:INT,b:INT,pad:VARCHAR(40)", [(i, random.randint(0, 49), "r" * 30) for i in range(1200)])
        self.table("S", "b:INT,c:VARCHAR(40)", [(random.randint(0, 49), "s" * 30) for _ in range(700)])

    def test_three_joins_same_bag_at_every_B(self):
        bags = []
        for j in JOINS:
            for B in (4, 16, 64):
                res = self.run_plan({"op": j, "condition": "R.b = S.b", "left": scan("R"), "right": scan("S")}, B)
                bags.append(sorted(res.rows))
        self.assertTrue(all(b == bags[0] for b in bags))
        self.assertGreater(len(bags[0]), 1200)     # output larger than both inputs (item 6)

    def test_bnl_compares_every_pair(self):
        for B in (4, 16):
            res = self.run_plan({"op": "nested_loop_join", "condition": "R.b = S.b",
                                 "left": scan("R"), "right": scan("S")}, B)
            self.assertEqual(res.root.comparisons, 1200 * 700)

    def test_bnl_page_reads_follow_formula_plus_directory_pages(self):
        db = Database(self.dir, 8)
        M, N = db.catalog.get_table("R")["num_pages"], db.catalog.get_table("S")["num_pages"]
        db.close()
        for B in (4, 8, 16):
            res = self.run_plan({"op": "nested_loop_join", "condition": "R.b = S.b",
                                 "left": scan("R"), "right": scan("S")}, B)
            blocks = -(-M // (B - 2))
            # formula M + ceil(M/(B-2))*N, plus one directory-page read per scan
            self.assertEqual(res.totals["page_reads"], M + blocks * N + 1 + blocks)

    def test_hash_join_partitions_or_stays_in_memory(self):
        plan = {"op": "hash_join", "condition": "R.b = S.b", "left": scan("R"), "right": scan("S")}
        res = self.run_plan(plan, 8)                 # build side bigger than B-2: grace
        info = res.root.info
        self.assertEqual(info["mode"], "grace")
        self.assertEqual(info["overflowed_partitions"], [])
        parts = sum(info["left_partition_pages"]) + sum(info["right_partition_pages"])
        self.assertEqual(res.totals["page_writes"], parts)
        # each partition is written once and read once: reads = scans + partitions
        db = Database(self.dir, 8)
        M, N = db.catalog.get_table("R")["num_pages"], db.catalog.get_table("S")["num_pages"]
        db.close()
        self.assertEqual(res.totals["page_reads"], M + N + 2 + parts)
        res = self.run_plan(plan, 64)                # build side fits: no partitioning
        self.assertEqual(res.root.info["mode"], "in-memory (build side fit)")
        self.assertEqual((res.totals["page_reads"], res.totals["page_writes"]), (M + N + 2, 0))

    def test_inequality_only_on_bnl(self):
        res = self.run_plan({"op": "nested_loop_join", "condition": "R.a < 3 and S.b > R.b",
                             "left": scan("R"), "right": scan("S")}, 4)
        self.assertTrue(all(r[0] < 3 and r[3] > r[1] for r in res.rows))
        with self.assertRaises(PlanError):
            self.run_plan({"op": "hash_join", "condition": "R.a < S.b", "left": scan("R"), "right": scan("S")})

    def test_cross_product(self):
        res = self.run_plan({"op": "nested_loop_join", "left": {"op": "limit", "n": 3, "child": scan("R")},
                             "right": {"op": "limit", "n": 4, "child": scan("S")}}, 4)
        self.assertEqual(len(res.rows), 12)

    def test_deterministic_counters(self):
        plan = {"op": "hash_join", "condition": "R.b = S.b", "left": scan("R"), "right": scan("S")}
        a = self.run_plan(plan, 6).to_json(False)
        b = self.run_plan(plan, 6).to_json(False)
        self.assertEqual(a, b)


class SortTests(Base):
    def setUp(self):
        super().setUp()
        random.seed(3)
        # 13-byte records, 240 per page
        self.rows = [(random.randint(0, 10 ** 6), "abc", i) for i in range(240 * 80)]
        self.table("T", "k:INT,s:VARCHAR(5),i:INT", self.rows)

    def test_sort_20_times_B_at_B4(self):
        res = self.run_plan({"op": "sort", "keys": [["T.k", "asc"], ["T.i", "desc"]], "child": scan("T")}, 4)
        self.assertEqual(res.rows, sorted(self.rows, key=lambda r: (r[0], -r[2])))
        self.assertLessEqual(res.peak_frames, 4)
        info = res.root.info
        self.assertEqual(info["input_pages"], 80)
        # W = B - 1 = 3 frames: ceil(80/3) = 27 runs, merged 2 at a time (one frame for output)
        self.assertEqual(info["runs_after_each_pass"][0], 27)

    def test_pass_count_matches_own_formula(self):
        import math
        for B in (4, 5, 8, 17):
            res = self.run_plan({"op": "sort", "keys": [["T.k", "asc"]], "child": scan("T")}, B)
            W = B - 1
            runs = math.ceil(80 / W)
            F = max(2, W - 1)
            passes, r = 1, runs
            while r > max(2, W):
                r = math.ceil(r / F)
                passes += 1
            self.assertEqual(res.root.info["passes"], passes + 1)

    def test_desc_and_strings(self):
        self.table("U", "n:VARCHAR(10)", [("b",), ("a",), ("é",), ("B",)])
        res = self.run_plan({"op": "sort", "keys": [["U.n", "desc"]], "child": scan("U")}, 4)
        self.assertEqual([r[0] for r in res.rows], ["é", "b", "a", "B"])   # byte-wise UTF-8


class SortExampleN8B3(Base):
    def test_documented_variant_of_section_2_5(self):
        # 8 pages of input at B = 3. With W = B - 1 = 2 frames for runs: 4 runs on
        # pass 0, merged 2-way twice. See DESIGN_M2.md 3 for why W is B - 1.
        self.table("T", "k:INT,s:VARCHAR(5),i:INT", [(240 * 8 - i, "x", i) for i in range(240 * 8)])
        res = self.run_plan({"op": "sort", "keys": ["T.k"], "child": scan("T")}, 3)
        self.assertEqual(res.root.info["runs_after_each_pass"], [4, 2])
        self.assertEqual(res.root.info["passes"], 3)
        sort = res.root
        # pass 0 writes 8; pass 1 reads 8 writes 8; pass 2 reads 8 streaming
        self.assertEqual((sort.page_reads, sort.page_writes), (16, 16))


class Aggregation(Base):
    def setUp(self):
        super().setUp()
        self.table("E", "dept:VARCHAR(5),sal:INT,name:VARCHAR(8)",
                   [("a", 10, "x"), ("a", 20, "y"), ("b", 5, "z"), ("b", 7, "w"), ("c", 1, "v")])

    def test_group_by_both_modes(self):
        for mode in ("sort", "hash"):
            res = self.run_plan({"op": "group_by", "mode": mode, "keys": ["E.dept"],
                                 "aggregates": [["count", "*"], ["avg", "E.sal"], ["max", "E.name"]],
                                 "child": scan("E")}, 4)
            self.assertEqual([c[0] for c in res.schema], ["E.dept", "count_star", "avg_E_sal", "max_E_name"])
            self.assertEqual(sorted(res.rows), [("a", 2, 15.0, "y"), ("b", 2, 6.0, "z"), ("c", 1, 1.0, "v")])

    def test_empty_input_no_keys_one_row(self):
        res = self.run_plan({"op": "group_by", "keys": [], "aggregates": [["count", "*"]],
                             "child": {"op": "filter", "condition": "E.sal > 100", "child": scan("E")}}, 4)
        self.assertEqual(res.rows, [(0,)])

    def test_empty_input_no_keys_avg_is_an_error(self):
        with self.assertRaises(ExecError):
            self.run_plan({"op": "group_by", "keys": [], "aggregates": [["avg", "E.sal"]],
                           "child": {"op": "filter", "condition": "E.sal > 100", "child": scan("E")}}, 4)

    def test_empty_input_with_keys_zero_rows(self):
        res = self.run_plan({"op": "group_by", "keys": ["E.dept"], "aggregates": [["count", "*"]],
                             "child": {"op": "filter", "condition": "E.sal > 100", "child": scan("E")}}, 4)
        self.assertEqual(res.rows, [])

    def test_sum_needs_int(self):
        with self.assertRaises(PlanError):
            self.run_plan({"op": "group_by", "keys": [], "aggregates": [["sum", "E.name"]], "child": scan("E")})

    def test_hash_distinct_spills_and_is_still_right(self):
        random.seed(5)
        rows = [(random.randint(0, 3000), "p" * 40) for _ in range(6000)]
        self.table("D", "x:INT,p:VARCHAR(40)", rows)
        for mode in ("hash", "sort"):
            res = self.run_plan({"op": "distinct", "mode": mode, "child": scan("D")}, 4)
            self.assertEqual(sorted(res.rows), sorted(set(rows)))
        self.assertGreater(res.root.page_writes, 0)


class SetOps(Base):
    def setUp(self):
        super().setUp()
        self.table("A", "x:INT", [(1,), (1,), (2,), (3,)])
        self.table("B", "y:INT", [(1,), (3,), (3,), (4,)])

    def p(self, op):
        return self.run_plan({"op": op, "left": scan("A"), "right": scan("B")}, 4)

    def test_bag_and_set_versions(self):
        self.assertEqual(sorted(self.p("union_all").rows), [(1,), (1,), (1,), (2,), (3,), (3,), (3,), (4,)])
        self.assertEqual(sorted(self.p("union").rows), [(1,), (2,), (3,), (4,)])
        self.assertEqual(sorted(self.p("intersect").rows), [(1,), (3,)])
        self.assertEqual(sorted(self.p("except").rows), [(2,)])
        self.assertEqual(sorted(self.p("intersect_all").rows), [(1,), (3,)])
        self.assertEqual(sorted(self.p("except_all").rows), [(1,), (2,)])
        self.assertEqual(self.p("union").schema, [["A.x", "INT"]])        # left names

    def test_incompatible(self):
        self.table("C", "s:VARCHAR(3)", [("a",)])
        with self.assertRaises(PlanError):
            self.run_plan({"op": "union", "left": scan("A"), "right": scan("C")})


class EmptyInputs(Base):
    def test_every_operator_on_empty_input(self):
        self.table("Z", "a:INT,b:VARCHAR(5)", [])
        self.table("Y", "a:INT,b:VARCHAR(5)", [(1, "x")])
        z, y = scan("Z"), scan("Y")
        plans = [z, {"op": "filter", "condition": "Z.a > 1", "child": z},
                 {"op": "project", "columns": ["Z.b"], "child": z},
                 {"op": "sort", "keys": ["Z.a"], "child": z},
                 {"op": "distinct", "mode": "hash", "child": z}, {"op": "distinct", "mode": "sort", "child": z},
                 {"op": "group_by", "mode": "sort", "keys": ["Z.a"], "aggregates": [["count", "*"]], "child": z},
                 {"op": "group_by", "mode": "hash", "keys": ["Z.a"], "aggregates": [["sum", "Z.a"]], "child": z},
                 {"op": "limit", "n": 3, "child": z}]
        for j in JOINS:
            plans += [{"op": j, "condition": "Z.a = Y.a", "left": z, "right": y},
                      {"op": j, "condition": "Y.a = Z.a", "left": y, "right": z}]
        for s in ("union", "union_all", "intersect", "except", "intersect_all", "except_all"):
            plans += [{"op": s, "left": z, "right": z}]
        for p in plans:
            res = self.run_plan(p, 4)
            self.assertEqual(res.rows, [], p["op"])
        res = self.run_plan({"op": "except", "left": y, "right": z}, 4)
        self.assertEqual(res.rows, [(1, "x")])


class Validation(Base):
    def setUp(self):
        super().setUp()
        self.table("R", "a:INT,b:VARCHAR(5)", [(1, "x")])

    def bad(self, plan, *needles):
        with self.assertRaises(PlanError) as cm:
            validate(plan)
        for n in needles:
            self.assertIn(n, str(cm.exception))

    def test_spec_examples(self):
        self.bad({"op": "sort", "child": scan("R")}, "Node 'sort'", "missing required field 'keys'")
        self.bad({"op": "filter", "condition": 3, "child": scan("R")}, "Node 'filter'", "'condition' must be a string")
        self.bad({"op": "merge_join", "left": scan("R"), "right": scan("R")}, "Node 'merge_join'", "unknown operator")

    def test_nested_offender_is_named(self):
        self.bad({"op": "project", "columns": ["R.a"], "child": {"op": "hash_join", "left": scan("R")}},
                 "Node 'hash_join'", "root.child", "'right'")
        self.bad({"op": "group_by", "keys": [], "aggregates": [["median", "R.a"]], "child": scan("R")},
                 "median")
        self.bad({"op": "limit", "n": -1, "child": scan("R")}, "Node 'limit'")
        self.bad({"op": "sort", "keys": [["R.a", "up"]], "child": scan("R")}, "Node 'sort'")
        self.bad({"table": "R"}, "missing required field 'op'")

    def test_malformed_json_has_location(self):
        with self.assertRaises(PlanError) as cm:
            load_plan_text('{"op": "seqscan",\n "table": }')
        self.assertIn("line 2", str(cm.exception))

    def test_unknown_table_and_column_rejected_before_any_io(self):
        db = Database(self.dir, 4)
        db.pool.reset_stats()
        with self.assertRaises(PlanError) as cm:
            execute(db, {"op": "filter", "condition": "R.zz > 1", "child": scan("R")}, 4)
        self.assertIn("R.zz", str(cm.exception))
        with self.assertRaises(PlanError) as cm:
            execute(db, {"op": "seqscan", "table": "Nope"}, 4)
        self.assertIn("Nope", str(cm.exception))
        self.assertEqual(db.pool.reads, 0)
        db.close()

    def test_type_error_in_condition(self):
        with self.assertRaises(PlanError) as cm:
            self.run_plan({"op": "filter", "condition": "R.a = 'x'", "child": scan("R")})
        self.assertIn("cannot compare", str(cm.exception))

    def test_duplicate_column_names_rejected(self):
        with self.assertRaises(PlanError):
            self.run_plan({"op": "nested_loop_join", "left": scan("R"), "right": scan("R")})
        res = self.run_plan({"op": "nested_loop_join", "left": scan("R"), "right": scan("R", "R2")})
        self.assertEqual(len(res.rows), 1)


class Skew(Base):
    def test_hash_join_overflow_is_handled(self):
        random.seed(11)
        def key():
            return 7 if random.random() < 0.4 else random.randint(0, 999)
        self.table("R", "k:INT,p:VARCHAR(30)", [(key(), "r" * 30) for _ in range(3000)])
        self.table("S", "k:INT,p:VARCHAR(30)", [(key(), "s" * 30) for _ in range(800)])
        res = self.run_plan({"op": "hash_join", "condition": "R.k = S.k", "left": scan("R"), "right": scan("S")}, 8)
        self.assertTrue(res.root.info["overflowed_partitions"])
        ref = self.run_plan({"op": "sort_merge_join", "condition": "R.k = S.k", "left": scan("R"), "right": scan("S")}, 8)
        self.assertEqual(len(res.rows), len(ref.rows))


class Limit(Base):
    def test_limit_stops_pulling(self):
        self.table("R", "a:INT", [(i,) for i in range(5000)])
        res = self.run_plan({"op": "limit", "n": 2, "child": scan("R")}, 4)
        self.assertEqual(res.rows, [(0,), (1,)])
        self.assertEqual([o.tuples_out for o, _ in walk(res.root)], [2, 2])
        self.assertLessEqual(res.totals["page_reads"], 2)


if __name__ == "__main__":
    unittest.main()
