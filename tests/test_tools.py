import os, subprocess, sys, tempfile, unittest
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class ToolsSmoke(unittest.TestCase):
    def test_measure_script_end_to_end(self):
        tr, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        with open(os.path.join(tr, "t.txt"), "w") as f:
            f.write("\n".join(str(i % 6) for i in range(60)))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "m1_measure.py"), "--traces", tr, "--out", out,
                            "--rows", "400", "--scan-pages", "20", "--sizes", "2", "4"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(out, "REPORT_M1_tables.md"), encoding="utf-8") as f:
            text = f.read()
        for h in ("Measurement 1", "Measurement 2", "Measurement 3"):
            self.assertIn(h, text)


if __name__ == "__main__":
    unittest.main()
