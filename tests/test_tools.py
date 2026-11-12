import os, subprocess, sys, tempfile, unittest
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

class ToolsSmoke(unittest.TestCase):
    def test_measure_script_end_to_end(self):
        tr, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        open(os.path.join(tr, "t.txt"), "w").write("\n".join(str(i % 6) for i in range(60)))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "m1_measure.py"), "--traces", tr,
                            "--out", out, "--rows", "400"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = open(os.path.join(out, "REPORT_M1_tables.md"), encoding="utf-8").read()
        self.assertIn("Table 1", text); self.assertIn("Table 2", text)

    def test_cli_layout_lists_slots_with_byte_offsets(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "r.csv"); open(f, "w").write("1,aa\n2,bb\n")
        run = lambda *a: subprocess.run([sys.executable, "-m", "storage", "--db", d, *a], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run("create-table", "--name", "R", "--schema", "a:INT,b:VARCHAR(5)").returncode, 0)
        self.assertEqual(run("load", "--table", "R", "--file", f).returncode, 0)
        out = run("layout", "--table", "R", "--page", "1", "--json").stdout
        import json
        j = json.loads(out)
        self.assertEqual(j["num_slots"], 2)
        self.assertEqual(j["slots"][0]["offset"], 4096 - 7)      # 4 (INT) + 1 (len) + 2 ("aa") = 7 bytes
        self.assertEqual(j["slots"][1]["dir_byte"], 12)

if __name__ == "__main__":
    unittest.main()
