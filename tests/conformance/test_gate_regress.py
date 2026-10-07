"""scripts/gate_regress.py: the regression gate judges a build against the noise between two builds of identical sources, and can fail."""
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

    def run_gate(self, times, outputs=None):
        d = tempfile.mkdtemp()
        data = pathlib.Path(d) / "data.csv"
        data.write_text("a\n1\n")
        paths = {}
        for name in times:
            out = (outputs or {}).get(name, "same")
            p = pathlib.Path(d) / name
            p.write_text("#!/bin/sh\necho %s\n" % out)
            p.chmod(0o755)
            paths[str(p)] = times[name]
        old, buf = gate_regress.timed, io.StringIO()
        gate_regress.timed = lambda argv: (paths[argv[0]], 0)
        argv = ["gate_regress", "--base", str(pathlib.Path(d) / "base"), "--base2", str(pathlib.Path(d) / "base2"), "--base3", str(pathlib.Path(d) / "base3"), "--new", str(pathlib.Path(d) / "new"), "--runs", "3", "--file", str(data)]
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(buf):
                code = gate_regress.main()
        finally:
            gate_regress.timed = old
        return code, buf.getvalue()

    def test_the_bound_is_the_noise_plus_one_percent_and_never_below_one(self):
        self.assertAlmostEqual(gate_regress.bound(1.023), 1.033)
        self.assertAlmostEqual(gate_regress.bound(0.95), 1.01)
        self.assertAlmostEqual(gate_regress.bound(1.15), 1.16)

    def test_a_build_within_the_noise_passes_and_one_beyond_it_fails(self):
        code, out = self.run_gate({"base": 1.0, "base2": 1.0, "base3": 1.0, "new": 1.0})
        self.assertEqual(code, 0, out)
        self.assertIn("noise", out)
        code, out = self.run_gate({"base": 1.0, "base2": 1.0, "base3": 1.0, "new": 1.02})       # 2% slower, identical builds agree: over 1 + 1%
        self.assertEqual(code, 1, out)
        self.assertIn("over its bound", out)
        code, out = self.run_gate({"base": 1.0, "base2": 1.04, "base3": 1.0, "new": 1.005})     # noisy builds around: a half percent is within the bound and the median
        self.assertEqual(code, 0, out)
        code, out = self.run_gate({"base": 1.0, "base2": 1.04, "base3": 1.0, "new": 1.04})      # inside the noise bound but the median is 4% over: the median criterion fails it
        self.assertEqual(code, 1, out)
        self.assertIn("median limit", out)
        code, out = self.run_gate({"base": 1.0, "base2": 1.04, "base3": 1.0, "new": 1.06})      # more than the noise plus 1%
        self.assertEqual(code, 1, out)
        code, out = self.run_gate({"base": 1.0, "base2": 1.0, "base3": 1.0, "new": 0.8})        # faster is not a failure
        self.assertEqual(code, 0, out)

    def test_the_noise_is_the_largest_of_the_extra_builds(self):
        code, out = self.run_gate({"base": 1.0, "base2": 1.0, "base3": 1.04, "new": 1.0})   # the second extra build is the unlucky one: it is the one reported
        self.assertEqual(code, 0, out)
        self.assertIn("1.040   1.050", out)
        code, out = self.run_gate({"base": 1.0, "base2": 1.04, "base3": 1.0, "new": 1.06})
        self.assertEqual(code, 1, out)
        self.assertIn("over its bound", out)

    def test_the_median_criterion_can_fail_alone(self):
        # the minimum of the new build sits inside the noise bound, the median does not: 3 runs, the clock is a table, so make the median differ via the sequence
        calls = {"n": 0}
        d = tempfile.mkdtemp()
        data = pathlib.Path(d) / "data.csv"
        data.write_text("a\n1\n")
        paths = {}
        for name in ("base", "base2", "base3", "new"):
            p = pathlib.Path(d) / name
            p.write_text("#!/bin/sh\necho same\n")
            p.chmod(0o755)
            paths[str(p)] = name
        seq = {"base": [1.0], "base2": [1.0], "base3": [1.05], "new": [1.0, 1.04, 1.04]}
        count = {}

        def clock(argv):
            n = paths[argv[0]]
            count[n] = count.get(n, 0) + 1
            v = seq[n]
            return v[min(count[n] - 1, len(v) - 1)] if n == "new" else v[0], 0
        old, buf = gate_regress.timed, io.StringIO()
        gate_regress.timed = clock
        sys.argv = ["g", "--base", str(pathlib.Path(d) / "base"), "--base2", str(pathlib.Path(d) / "base2"), "--base3", str(pathlib.Path(d) / "base3"), "--new", str(pathlib.Path(d) / "new"), "--runs", "3", "--file", str(data)]
        try:
            with contextlib.redirect_stdout(buf):
                code = gate_regress.main()
        finally:
            gate_regress.timed = old
        self.assertEqual(code, 1, buf.getvalue())
        self.assertIn("median", buf.getvalue())

    def test_a_single_extra_build_is_refused(self):
        sys.argv = ["g", "--base", "a", "--base2", "b", "--new", "c"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate_regress.main()

    def test_different_outputs_are_refused_before_timing(self):
        code, out = self.run_gate({"base": 1.0, "base2": 1.0, "base3": 1.0, "new": 1.0}, {"new": "different"})
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
