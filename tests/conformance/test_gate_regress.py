"""scripts/gate_regress.py (third revision): build-averaged; it passes identical sources, fails a real slowdown, and shows an outlier build."""
import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gate_regress  # noqa: E402


class Gate(unittest.TestCase):
    """The decision, with the clock replaced by a table of times per binary (a real clock would make the test as noisy as the gate's subject)."""

    def run_gate(self, old, new, outputs=None, extra=()):
        d = tempfile.mkdtemp()
        data = pathlib.Path(d) / "data.csv"
        data.write_text("a\n1\n")
        paths = {}
        names = []
        for side, times in (("o", old), ("n", new)):
            row = []
            for i, t in enumerate(times):
                name = "%s%d" % (side, i)
                p = pathlib.Path(d) / name
                p.write_text("#!/bin/sh\necho %s\n" % (outputs or {}).get(name, "same"))
                p.chmod(0o755)
                paths[str(p)] = t
                row.append(str(p))
            names.append(row)
        old_timed, buf = gate_regress.timed, io.StringIO()
        gate_regress.timed = lambda argv: (paths[argv[0]], 0)
        sys.argv = ["gate_regress", "--old", *names[0], "--new", *names[1], "--runs", "21", "--file", str(data), "--cell", "cut", *extra]
        try:
            with contextlib.redirect_stdout(buf):
                code = gate_regress.main()
        finally:
            gate_regress.timed = old_timed
        return code, buf.getvalue()

    def test_identical_sources_with_layout_spread_pass(self):
        # three builds each side, each build 3% off in a different direction: the means agree
        code, out = self.run_gate([1.00, 1.03, 0.97], [1.03, 0.97, 1.00])
        self.assertEqual(code, 0, out)
        self.assertIn("G6: PASS", out)

    def test_a_single_lucky_or_unlucky_new_build_does_not_decide(self):
        code, out = self.run_gate([1.0, 1.0, 1.0], [1.0, 1.0, 1.05])   # one new build 5% slow: the mean is 1.017 <= 1.02
        self.assertEqual(code, 0, out)
        self.assertIn("a build is off", out)                          # but it is visible

    def test_a_real_slowdown_fails(self):
        code, out = self.run_gate([1.0, 1.0, 1.0], [1.03, 1.03, 1.03])
        self.assertEqual(code, 1, out)
        self.assertIn("over 1.02", out)
        code, out = self.run_gate([1.0, 1.01, 0.99], [1.04, 1.03, 1.05])
        self.assertEqual(code, 1, out)

    def test_faster_is_not_a_failure(self):
        code, out = self.run_gate([1.0, 1.0, 1.0], [0.7, 0.7, 0.7])
        self.assertEqual(code, 0, out)

    def test_one_build_a_side_is_refused(self):
        sys.argv = ["g", "--old", "a", "--new", "b"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate_regress.main()
        sys.argv = ["g", "--old", "a", "b", "c", "--new", "d", "e"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate_regress.main()

    def test_fewer_than_21_runs_are_refused(self):
        sys.argv = ["g", "--old", "a", "b", "c", "--new", "d", "e", "f", "--runs", "5"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate_regress.main()

    def test_different_outputs_are_refused_before_timing(self):
        code, out = self.run_gate([1.0, 1.0, 1.0], [1.0, 1.0, 1.0], {"n1": "different"})
        self.assertEqual(code, 2, out)

    def test_the_judgement_is_the_ratio_of_the_means_and_the_spread(self):
        r, so, sn = gate_regress.judge([1.0, 1.0, 1.0], [1.0, 1.0, 1.06])
        self.assertAlmostEqual(r, 1.02)
        self.assertAlmostEqual(so, 0.0)
        self.assertAlmostEqual(sn, 0.06 / (3.06 / 3))


if __name__ == "__main__":
    unittest.main()
