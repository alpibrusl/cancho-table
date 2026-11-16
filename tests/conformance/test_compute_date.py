"""docs/functions.md section 3.5 (slice K8a): `year`, `month`, `day`, `weekday` and `days_between` over `:date` in `--compute`, through the built binary.

* G-DT-ORACLE: **every date from 0001-01-01 to 9999-12-31** (3,652,059 of them) through the four one-argument functions, against Python's `datetime.date`, which shares no code with the
  tool; `days_between` over random pairs and the two ends; the cells that are not dates are refused with the reader's rule;
* the explain lists `value.not-date` for a definition that reads a date, and nothing it cannot reach.
"""

import csv
import datetime
import io
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
from harness import Scratch, run  # noqa: E402

FIRST = datetime.date(1, 1, 1).toordinal()
LAST = datetime.date(9999, 12, 31).toordinal()


class Dates(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def csv(self, defs, name):
        r = run("--root", self.s.dir, "--compute", defs, "--format", "csv", "--max-bytes", "67108864", name)
        self.assertEqual(r.status, 0, (defs, r.stderr[:300]))
        return r.stdout.decode("utf-8")

    def test_every_date_from_0001_to_9999(self):
        days = [datetime.date.fromordinal(o) for o in range(FIRST, LAST + 1)]
        self.s.write("all.csv", "d\n" + "\n".join(x.isoformat() for x in days) + "\n")
        out = self.csv("y=year(d:date);m=month(d:date);dd=day(d:date);w=weekday(d:date)", "all.csv").split("\n")
        self.assertEqual(out[0], "d,y,m,dd,w")
        self.assertEqual(len(out), len(days) + 2)
        bad = 0
        for x, line in zip(days, out[1:]):
            if line != "%s,%d,%d,%d,%d" % (x.isoformat(), x.year, x.month, x.day, x.isoweekday()):
                bad += 1
                if bad < 5:
                    self.fail((x, line))
        self.assertEqual(bad, 0)

    def test_days_between_over_random_pairs_and_the_ends(self):
        rng = random.Random(5)
        pairs = [(FIRST, LAST), (LAST, FIRST), (FIRST, FIRST), (719163, 719163)]
        pairs += [(rng.randint(FIRST, LAST), rng.randint(FIRST, LAST)) for _ in range(20000)]
        self.s.write("p.csv", "a,b\n" + "\n".join("%s,%s" % (datetime.date.fromordinal(x).isoformat(), datetime.date.fromordinal(y).isoformat()) for x, y in pairs) + "\n")
        out = self.csv("n=days_between(a:date,b:date);m=days_between(a:date,\"1970-01-01\")", "p.csv").split("\n")
        epoch = datetime.date(1970, 1, 1).toordinal()
        for (x, y), line in zip(pairs, out[1:]):
            self.assertEqual(line.split(",")[2:], [str(y - x), str(epoch - x)], (x, y, line))

    def test_a_cell_that_is_not_a_date_is_refused_with_the_readers_rule(self):
        for cell, reason in [("2026-02-30", "day"), ("2026-13-01", "month"), ("x", "shape"), ("", "shape"), ("0000-01-01", "year")]:
            self.s.write("b.csv", "a,b\n1970-01-01,2000-01-01\n1970-01-01,%s\n" % cell)
            r = run("--root", self.s.dir, "--compute", "n=days_between(a:date,b:date)", "b.csv")
            self.assertEqual(r.first_rule(), "value.not-date", cell)
            d = r.error()["detail"]
            self.assertEqual((d["row"], d["column"], d["reason"], d["context"]), (2, "b", reason, "compute"), cell)
            self.assertNotIn(cell.encode() if cell else b"@@", r.stdout.replace(b"1970-01-01", b"").replace(b"2000-01-01", b"") if cell not in ("x",) else b"")

    def test_the_arm_not_taken_does_not_read_its_cell(self):
        self.s.write("l.csv", "a,b\n2026-01-01,x\n")
        rows = list(csv.reader(io.StringIO(self.csv("r=if(year(a:date) > 2000, 1, year(b:date))", "l.csv"))))
        self.assertEqual(rows[1], ["2026-01-01", "x", "1"])

    def test_a_date_beside_the_other_functions(self):
        self.s.write("e.csv", "a,b\n2026-11-16,2000-02-29\n")
        rows = list(csv.reader(io.StringIO(self.csv("r=year(a:date)*100+month(a:date);s=abs(days_between(a:date,b:date))/7@2", "e.csv"))))
        self.assertEqual(rows[1][2:], ["202611", "1393.86"])

    def test_the_compile_refusals(self):
        self.s.write("c.csv", "a,b\n2026-11-16,x\n")
        for defs, rule in [("r=year(a)", "compute.type-mismatch"), ("r=year(a:int)", "compute.type-mismatch"), ("r=year(1)", "compute.type-mismatch"), ("r=year()", "compute.arity"),
                           ("r=days_between(a:date)", "compute.arity"), ("r=year(a:date,a:date)", "compute.arity"), ('r=year("2026-02-30")', "compute.bad-literal"), ("r=Year(a:date)", "compute.unknown-function"),
                           ("r=a:date", "compute.syntax")]:
            r = run("--root", self.s.dir, "--compute", defs, "c.csv")
            self.assertEqual(r.first_rule(), rule, defs)
            self.assertEqual(r.status, 2, defs)

    def test_the_explain_lists_the_rule_of_a_date_cell(self):
        self.s.write("x.csv", "a,b\n2026-11-16,1\n")
        e = run("--root", self.s.dir, "--compute", "r=year(a:date)", "--explain", "x.csv").data()["explain"]
        self.assertIn("value.not-date", [x if isinstance(x, str) else x.get("rule") for x in e["refusals"]["possible"]])
        e = run("--root", self.s.dir, "--compute", "r=1+2", "--explain", "x.csv").data()["explain"]
        self.assertNotIn("value.not-date", [x if isinstance(x, str) else x.get("rule") for x in e["refusals"]["possible"]])
        self.assertEqual([(d["name"], d["scale"]) for d in run("--root", self.s.dir, "--compute", "r=weekday(a:date)", "--explain", "x.csv").data()["explain"]["stage"]["definitions"]], [("r", 0)])


if __name__ == "__main__":
    unittest.main()
