"""The three exact cell parsers (parse_int, parse_dec, pow10) against Python's integer arithmetic: scripts/cells_golden.py.

The binary leg runs the built `table` on a one-cell file for every case it can observe; the source leg builds the module's sources into the probe of
tests/conformance/cells_probe and compares every case, empty cells and pow10 included. The leg through the PUBLISHED store needs a pushed revision
and is run by hand (docs/numeric-package.md). A leg that cannot fail proves nothing: the controls give it a wrong reference, and a wrong source.
"""

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "scripts" / "cells_golden.py"


def golden(*args):
    return subprocess.run([sys.executable, str(GOLDEN), *args], capture_output=True, text=True, timeout=900)


class CellsGolden(unittest.TestCase):
    def test_binary_leg(self):
        got = golden("--leg", "binary")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def test_source_leg(self):
        got = golden("--leg", "source")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def test_the_controls_fail(self):
        for leg, control in (("binary", "reference"), ("binary", "reference-junk"), ("source", "reference"), ("source", "source")):
            with self.subTest(leg=leg, control=control):
                got = golden("--leg", leg, "--control", control)
                self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
                self.assertIn("differ", got.stdout)


if __name__ == "__main__":
    unittest.main()
