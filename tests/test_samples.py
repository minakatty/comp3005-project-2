"""Our own sample plans (samples/plans) and sample SQL (samples/sql) run end to end.
The course's sample files are not published; these follow the spec's JSON examples."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from execution.engine import execute
from execution.errors import PlanError
from sqlfront.compiler import catalog_from_db, compile_sql
from storage.db import Database

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Samples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        subprocess.run([sys.executable, os.path.join(ROOT, "samples", "make_sample_db.py"), cls.dir],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_plan(self, plan, B):
        db = Database(self.dir, B)
        try:
            return execute(db, plan, B)
        finally:
            db.close()

    def test_every_good_plan_runs_at_B4_and_B32(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "samples", "plans", "*.json"))):
            name = os.path.basename(path)
            plan = json.load(open(path))
            for B in (4, 32):
                if "_bad_" in name:
                    with self.assertRaises(PlanError, msg=name):
                        self.run_plan(plan, B)
                    continue
                res = self.run_plan(plan, B)
                self.assertEqual(res.leftover_pins, 0, name)
                self.assertLessEqual(res.peak_frames, B, name)

    def test_three_joins_agree_and_duplicate_keys(self):
        load = lambda n: json.load(open(os.path.join(ROOT, "samples", "plans", n)))
        bags = [sorted(self.run_plan(load(n), 8).rows)
                for n in ("09_nested_loop_join.json", "10_sort_merge_join.json", "11_hash_join.json")]
        self.assertTrue(bags[0] == bags[1] == bags[2])
        self.assertEqual(len(self.run_plan(load("18_dup_keys_smj.json"), 4).rows), 4)

    def test_sample_sql_compiles_and_runs(self):
        from optimizer.estimate import Stats
        from optimizer.optimize import optimize
        for path in sorted(glob.glob(os.path.join(ROOT, "samples", "sql", "*.sql"))):
            db = Database(self.dir, 16)
            try:
                lp = compile_sql(open(path).read(), catalog_from_db(db))[0]
                _, root = optimize(lp, Stats(db), 16)
                res = execute(db, root.json(), 16)
            finally:
                db.close()
            self.assertGreaterEqual(len(res.rows), 0, path)


if __name__ == "__main__":
    unittest.main()
