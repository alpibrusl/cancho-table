"""The timing loop of scripts/adversarial.py counts only runs that succeeded.

Found in the sort round (docs/gap-sort.md section 1): `COPY (...) TO '/dev/null'` of DuckDB fails in 50 ms with "Could not set lock on file
/dev/null" when another DuckDB on the machine holds the lock, and the loop, which did not look at the status, took the minimum: a sort of 1M rows
"in 0.059 s". `timed_run` runs again a run that exited non-zero and stops the script after five in a row.
"""
import importlib.util
import pathlib
import shutil
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("adversarial", ROOT / "scripts" / "adversarial.py")
adversarial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adversarial)


class TimedRun(unittest.TestCase):
    def script(self, results):
        calls = []

        def once(argv):
            calls.append(argv)
            return results[len(calls) - 1]
        return once, calls

    def test_a_failed_run_is_not_a_time(self):
        once, calls = self.script([(0.05, 1), (0.05, 1), (0.52, 0)])
        self.assertEqual(adversarial.timed_run("x", ["x"], once=once, pause=0), 0.52)
        self.assertEqual(len(calls), 3)

    def test_a_run_that_succeeds_is_its_time(self):
        once, calls = self.script([(0.31, 0)])
        self.assertEqual(adversarial.timed_run("x", ["x"], once=once, pause=0), 0.31)

    def test_five_failures_in_a_row_stop_the_script(self):
        once, calls = self.script([(0.05, 1)] * 5)
        with self.assertRaises(SystemExit) as e:
            adversarial.timed_run("x", ["x"], once=once, pause=0)
        self.assertIn("exited non-zero", str(e.exception))
        self.assertEqual(len(calls), 5)

    @unittest.skipUnless(shutil.which("duckdb"), "needs duckdb")
    def test_duckdb_against_a_duckdb_that_holds_dev_null(self):
        holder = subprocess.Popen(["duckdb", "-c", "COPY (SELECT range FROM range(400000000) ORDER BY hash(range)) TO '/dev/null' (FORMAT csv)"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            argv = ["duckdb", "-c", "COPY (SELECT 1) TO '/dev/null' (FORMAT csv)"]
            deadline = time.time() + 20
            rc = 0
            while rc == 0 and time.time() < deadline and holder.poll() is None:
                time.sleep(0.2)
                rc = adversarial.bench.once(argv)[1]
            if holder.poll() is not None:
                self.skipTest("the holder ended before it was found to hold the lock")
            self.assertNotEqual(rc, 0, "a DuckDB that finds /dev/null locked exits non-zero (this is what the old loop timed)")
            with self.assertRaises(SystemExit):
                adversarial.timed_run("duckdb", argv, tries=2, pause=0.1)
        finally:
            holder.kill()
            holder.wait()


if __name__ == "__main__":
    unittest.main()
