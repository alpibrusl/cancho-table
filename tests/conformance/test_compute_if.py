"""docs/functions.md section 3.4 (slice K7a, the numbers): `if(cond, a, b)` in `--compute`, through the built binary.

* G-IF-ORACLE: random files and random `if` definitions equal the Python reference of scripts/cexpr_golden.py (which shares no code with the tool), as CSV and as JSON;
* G-IF-LAZY: the arm that is not taken is never run: a zero divisor, an overflow or a cell that is not a number in it is not an error, and the same in the arm taken is the same refusal as ever;
* the compile refusals, with an offset and no payload; the explain agrees with the run.
"""

import csv
import io
import json
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import cexpr_golden as G  # noqa: E402
from harness import Scratch, run  # noqa: E402
from test_compute import make_file, expected  # noqa: E402


class Conditions(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.s.write("q.csv", "qty,total,note\n0,10,a\n4,10,b\n-5,7.5,c\n")

    def tearDown(self):
        self.s.cleanup()

    def csv(self, defs, name="q.csv"):
        r = run("--root", self.s.dir, "--compute", defs, "--format", "csv", name)
        self.assertEqual(r.status, 0, (defs, r.stderr))
        return list(csv.reader(io.StringIO(r.stdout.decode("utf-8"))))

    def test_the_guard_of_a_division(self):
        rows = self.csv("r=if(qty:int != 0, total:dec(1)/qty:int@2, 0)")
        self.assertEqual([r[3] for r in rows], ["r", "0.00", "2.50", "-1.50"])

    def test_the_scale_of_the_result_is_the_larger_of_the_arms(self):
        rows = self.csv("r=if(qty:int > 0, 1, 2.5)")
        self.assertEqual([r[3] for r in rows], ["r", "2.5", "1.0", "2.5"])

    def test_and_or_and_the_comparisons(self):
        rows = self.csv("a=if(qty:int > 0 and total:dec(1) >= 10, 1, 0);o=if(qty:int < 0 or qty:int = 0, 1, 0);c=if(total:dec(1) = 10, 1, 0)")
        self.assertEqual([r[3:] for r in rows[1:]], [["0", "1", "1"], ["1", "0", "1"], ["0", "1", "0"]])

    def test_an_earlier_definition_and_an_if_in_an_expression(self):
        rows = self.csv("x=qty:int*2;r=if(x > 0, x, -x) + 1")
        self.assertEqual([r[4] for r in rows[1:]], ["1", "9", "11"])

    # G-IF-LAZY
    def test_a_zero_divisor_in_the_arm_not_taken_is_not_an_error(self):
        self.s.write("l.csv", "a,b\n0,x\n2,3\n")
        rows = self.csv("r=if(a:int != 0, 10/a:int@0, 7)", "l.csv")
        self.assertEqual([r[2] for r in rows[1:]], ["7", "5"])

    def test_a_bad_cell_in_the_arm_not_taken_is_not_an_error(self):
        self.s.write("l.csv", "a,b\n0,x\n0,y\n")
        rows = self.csv("r=if(a:int = 0, 1, b:int)", "l.csv")
        self.assertEqual([r[2] for r in rows[1:]], ["1", "1"])
        r = run("--root", self.s.dir, "--compute", "r=if(a:int = 0, b:int, 1)", "l.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")

    def test_a_refusal_in_the_arm_taken_is_the_refusal_of_that_arm(self):
        r = run("--root", self.s.dir, "--compute", "r=if(qty:int = 0, total:int/qty:int@2, 1)", "q.csv")
        self.assertEqual(r.first_rule(), "compute.divide-by-zero")
        self.assertEqual(r.error()["detail"]["row"], 1)

    def test_the_condition_stops_at_the_first_comparison_that_decides(self):
        rows = self.csv("r=if(qty:int > 0 and total:int/qty:int@0 > 1, 1, 0)")
        self.assertEqual([r[3] for r in rows[1:]], ["0", "1", "0"])

    def test_the_refusals_of_the_compiler(self):
        for defs, rule in [("r=if(qty:int < 1 and qty:int < 2 or qty:int < 3, 1, 2)", "compute.mixed-and-or"),
                           ("r=if(qty:int<1 and qty:int<2 and qty:int<3 and qty:int<4 and qty:int<5, 1, 2)", "compute.too-many-conditions"),
                           ("r=if(qty:int < 1, 1)", "compute.arity"), ("r=if(qty:int < 1, 1, 2, 3)", "compute.arity"),
                           ("r=if(qty:int, 1, 2)", "compute.syntax"), ("r=if(qty:int < 1, 1, 2", "compute.syntax"), ("r=qty:int < 1", "compute.syntax"),
                           ("r=if(qty:int < 1, upper(note), 2)", "compute.type-mismatch")]:
            r = run("--root", self.s.dir, "--compute", defs, "q.csv")
            self.assertEqual(r.status, 2, defs)
            self.assertEqual(r.first_rule(), rule, defs)
            self.assertIn("offset", r.error()["detail"], defs)

    def test_the_explain_agrees_with_the_run(self):
        defs = "r=if(qty:int != 0, total:dec(1)/qty:int@3, 0)"
        e = run("--root", self.s.dir, "--compute", defs, "--explain", "q.csv").data()["explain"]
        self.assertEqual([(d["name"], d["scale"]) for d in e["stage"]["definitions"]], [("r", 3)])
        self.assertIn("compute.divide-by-zero", [x if isinstance(x, str) else x.get("rule") for x in e["refusals"]["possible"]])

    # G-IF-ORACLE
    def test_random_files_and_definitions_equal_the_reference(self):
        rng = random.Random(11)
        checked = 0
        for _ in range(80):
            kinds, text, rows, values = make_file(rng, rng.randint(1, 10))
            cols = [(k, s) for k, s in kinds]
            trees = [G.gen_if(rng, rng.randint(1, 3), cols) for _ in range(rng.randint(1, 2))]
            if any(G.size_of(t) > 100 or G.depth_of(t) > 9 for t in trees):
                continue
            try:
                for t in trees:
                    G.static_scale(t)
            except G.BadScale:
                continue
            defs = ";".join(f"r{i}=" + G.render(t, rng) for i, t in enumerate(trees))
            if G.paren_depth(defs) > 4 or len(defs) > 240:
                continue
            defs = self._shift_positions(defs)
            want_rows, failed = [], None
            for ri, vals in enumerate(values):
                try:
                    want_rows.append(rows[ri] + [expected(t, vals) for t in trees])
                except (G.Overflow, G.DivZero):
                    failed = ri
                    break
            self.s.write("t.csv", text)
            r = run("--root", self.s.dir, "--compute", defs, "--format", "csv", "t.csv")
            if failed is not None:
                self.assertEqual(r.status, 8, (defs, r.stderr))
                continue
            self.assertEqual(r.status, 0, (defs, r.stderr, text))
            self.assertEqual(list(csv.reader(io.StringIO(r.stdout.decode())))[1:], want_rows, defs)
            self.assertEqual(run("--root", self.s.dir, "--compute", defs, "t.csv").data()["rows"], want_rows, defs)
            checked += 1
        self.assertGreater(checked, 25)

    @staticmethod
    def _shift_positions(defs):
        import re
        return re.sub(r"#(\d)", lambda m: "#%d" % (int(m.group(1)) + 1), defs)


if __name__ == "__main__":
    unittest.main()
