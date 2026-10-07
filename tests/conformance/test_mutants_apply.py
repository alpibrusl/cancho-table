"""The mutation scripts must still apply: every mutant's text occurs exactly once in the source it mutates.

The scripts went stale once (the engine split moved 21 sites) and, until a mutant was reached, said nothing. Now
`--check` verifies all of them without a build, CI runs it, and this test runs it with the suite and tests the check
itself: a pattern that is missing, one that occurs twice, and a mutant that changes nothing each fail loudly.
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import mutlib  # noqa: E402


class MutantsApply(unittest.TestCase):
    def test_every_script_applies(self):
        for name in ("select", "filter", "parallel", "cellcost", "sort", "numbers", "float", "float_sum", "typed_keys", "report", "mcp", "skill", "readloop"):
            with self.subTest(name):
                got = subprocess.run([sys.executable, str(ROOT / "scripts" / ("%s_mutants.py" % name)), "--check"], capture_output=True, text=True)
                self.assertEqual((got.returncode, got.stdout.count("!!")), (0, 0), got.stdout)

    def test_the_check_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d)
            (src / "a.cho").write_text("one two two\n")
            problems = mutlib.check([("gone", "a.cho", "three", "x"), ("twice", "a.cho", "two", "x"), ("same", "a.cho", "one", "one"),
                                     ("nofile", "b.cho", "one", "x"), ("fine", "a.cho", "one", "x")], src)
            self.assertEqual(len(problems), 4, problems)
            self.assertIn("mutant gone: pattern not found in a.cho", problems)
            self.assertTrue(any(p.startswith("mutant twice: pattern occurs 2 times") for p in problems))
            self.assertTrue(any(p.startswith("mutant same:") for p in problems))
            self.assertTrue(any(p.startswith("mutant nofile:") for p in problems))

    def test_a_script_with_a_stale_mutant_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "a.cho").write_text("one\n")
            mutlib.SRC = pathlib.Path(d)
            try:
                self.assertEqual(mutlib.main([("gone", "a.cho", "three", "x")], [], ["--check"]), 1)
                self.assertEqual(mutlib.main([("fine", "a.cho", "one", "x")], [], ["--check"]), 0)
            finally:
                mutlib.SRC = ROOT / "tools" / "table"


if __name__ == "__main__":
    unittest.main()
