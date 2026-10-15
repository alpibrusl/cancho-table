"""scripts/threads_vs_duckdb.py: the gate is that `table` equals the exact sum at every thread count, and it fails when it does not.

A small file (20,000 rows) at 1, 2 and 4 threads. With `duckdb` on the path the script also runs DuckDB (the test checks that its values are printed and
parsed, never that they differ from anything); without it (CI has none) the script runs `table` alone against Fraction. The gate-fails cases use the
script's --mutate hook, which nudges one answer by a unit in the last place: the script must then exit 1.
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction

import procs as subprocess
from harness import binary

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "threads_vs_duckdb.py"
sys.path.insert(0, str(ROOT / "scripts"))
import threads_vs_duckdb as tvd  # noqa: E402

ARGS = ["--bin", binary(), "--rows", "20000", "--threads", "1,2,4", "--runs", "2"]
DUCK = shutil.which("duckdb")


def script(*extra):
    p = subprocess.run([sys.executable, str(SCRIPT), *ARGS, *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=600)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


class Gate(unittest.TestCase):
    def test_table_is_exact_at_every_thread_count(self):
        code, out, err = script("--no-duckdb")
        self.assertEqual(code, 0, out + err)
        self.assertIn("gate: table equals the exact sum at every thread count in every run: PASS", out)

    def test_the_file_and_the_exact_sum_are_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = pathlib.Path(d) / "a.csv", pathlib.Path(d) / "b.csv"
            na, sa, xa = tvd.write_file(a, 3000, 7)
            nb, sb, xb = tvd.write_file(b, 3000, 7)
            self.assertEqual((na, sa, xa), (nb, sb, xb))
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(na, a.stat().st_size)
            self.assertNotEqual(sa, tvd.write_file(b, 3000, 8)[1])
            # the exact sum is one rounding of the real-number sum
            self.assertEqual(float(tvd.exact_sum(xa)), float(sum((Fraction(x) for x in xa), Fraction(0))))

    def test_a_wrong_table_answer_fails_the_gate(self):
        code, out, _ = script("--no-duckdb", "--mutate", "table")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        self.assertIn("NO", out)

    def test_a_wrong_exact_sum_fails_the_gate(self):
        code, out, _ = script("--no-duckdb", "--mutate", "exact")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)

    def test_json_has_every_value(self):
        with tempfile.TemporaryDirectory() as d:
            j = pathlib.Path(d) / "o.json"
            code, out, err = script("--no-duckdb", "--json", str(j))
            self.assertEqual(code, 0, out + err)
            doc = json.loads(j.read_text())
            self.assertEqual(doc["threads"], [1, 2, 4])
            self.assertEqual(doc["file"]["rows"], 20000)
            self.assertEqual(len(doc["file"]["sha256"]), 64)
            for t in ("1", "2", "4"):
                self.assertEqual(len(doc["table"][t]), 2)
                self.assertTrue(all(float(v) == float(doc["exact_sum"]) for v in doc["table"][t]))
            self.assertEqual(doc["duckdb"], {})


@unittest.skipUnless(DUCK, "no `duckdb` on the path (CI has none): the DuckDB columns are not exercised")
class WithDuckDB(unittest.TestCase):
    def test_duckdb_values_are_printed_and_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            j = pathlib.Path(d) / "o.json"
            code, out, err = script("--json", str(j))
            self.assertEqual(code, 0, out + err)
            doc = json.loads(j.read_text())
            self.assertTrue(doc["versions"]["duckdb"].startswith("v"))
            for kind in ("sum", "kahan_sum", "decimal"):
                for t in ("1", "2", "4"):
                    vals = doc["duckdb"][kind][t]
                    self.assertEqual(len(vals), 2)
                    for v in vals:
                        float(v)
                        self.assertIn(v, out)
            self.assertIn("DuckDB equal to exact?", out)

    def test_a_wrong_table_answer_fails_the_gate_with_duckdb_too(self):
        code, out, _ = script("--mutate", "table")
        self.assertEqual(code, 1, out)


if __name__ == "__main__":
    unittest.main()
