"""`flt.put_float` from the in-tree sources: scripts/put_float_edge.py (issue #59).

The finite doubles are written as Python's repr in the layout of docs/numbers.md 4.6 (the contract of the differential tests, unchanged); a key that stands for a
double that is not finite is written `inf`, `-inf` or `NaN` (the spelling of `std.fmt.float_into`), not the garbled `i.nf`, `-i.nf`, `N.aN` that splitting the text of `std.fmt.float_into` gave. A leg that
cannot fail proves nothing: the control gives it a wrong reference.
"""

import pathlib
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EDGE = ROOT / "scripts" / "put_float_edge.py"


def edge(*args):
    return subprocess.run([sys.executable, str(EDGE), *args], capture_output=True, text=True, timeout=900)


class PutFloat(unittest.TestCase):
    def test_finite_and_not_finite(self):
        got = edge()
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn(" 0 differences", got.stdout)

    def test_the_control_fails(self):
        got = edge("--control", "reference")
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("DIFFERENT non-finite +inf", got.stdout)


if __name__ == "__main__":
    unittest.main()
