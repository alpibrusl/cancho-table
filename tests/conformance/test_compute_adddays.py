"""docs/functions.md section 3.5 (slice K8b): `add_days(d, n)` in `--compute`, a date written as text, through the built binary.

* G-DT-ORACLE: every date from 0001-01-02 to 9999-12-30 one day later and one day earlier, and random `(date, n)` pairs with `n` a column, a literal and negative, against Python's `datetime.date`
  and `timedelta`, which share no code with the tool; the two ends are refused (`compute.date-out-of-range`, the row, exit 8) and never wrapped;
* a date column and an integer column that are not what they say are refused with the reader's rules, no payload;
* the compile refusals, the text functions around it, and the explain.
"""

import csv
import datetime
import io
import json
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
from harness import Scratch, run  # noqa: E402

FIRST = datetime.date(1, 1, 1).toordinal()
LAST = datetime.date(9999, 12, 31).toordinal()
D = datetime.date.fromordinal
C28 = datetime.date(2000, 2, 28).toordinal()


class AddDays(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def out(self, defs, name, *extra):
        r = run("--root", self.s.dir, "--compute", defs, "--format", "csv", "--max-bytes", "67108864", *extra, name)
        self.assertEqual(r.status, 0, (defs, r.stderr[:300]))
        return r.stdout.decode("utf-8")

    def test_every_date_one_day_later_and_earlier(self):
        days = [D(o) for o in range(FIRST + 1, LAST)]
        self.s.write("all.csv", "d\n" + "\n".join(x.isoformat() for x in days) + "\n")
        out = self.out("up=add_days(d:date,1);down=add_days(d:date,-1)", "all.csv").split("\n")
        self.assertEqual(out[0], "d,up,down")
        bad = 0
        for o, line in zip(range(FIRST + 1, LAST), out[1:]):
            if line != "%s,%s,%s" % (D(o).isoformat(), D(o + 1).isoformat(), D(o - 1).isoformat()):
                bad += 1
                if bad < 5:
                    self.fail((o, line))
        self.assertEqual(bad, 0)

    def test_random_pairs_with_a_column_a_literal_and_negative_counts(self):
        rng = random.Random(9)
        rows = []
        while len(rows) < 20000:
            o = rng.randint(FIRST, LAST)
            n = rng.choice([0, 1, -1, 7, -7, 30, 365, -365, rng.randint(-4000000, 4000000)])
            if FIRST <= o + n <= LAST and o + 100 <= LAST and FIRST <= C28 + n <= LAST:
                rows.append((o, n))
        self.s.write("p.csv", "d,n\n" + "\n".join("%s,%d" % (D(o).isoformat(), n) for o, n in rows) + "\n")
        out = self.out('a=add_days(d:date,n:int);b=add_days(d:date,100);c=add_days("2000-02-28",n:int)', "p.csv").split("\n")
        for (o, n), line in zip(rows, out[1:]):
            want = [D(o + n).isoformat(), D(o + 100).isoformat(), D(C28 + n).isoformat()]
            self.assertEqual(line.split(",")[2:], want, (o, n, line))

    def test_the_two_ends_are_refused_and_never_wrapped(self):
        for cell, n, row in [("9999-12-31", 1, 1), ("0001-01-01", -1, 1), ("9999-12-30", 2, 1), ("0001-01-02", -2, 1), ("9999-12-31", 3000000, 1)]:
            self.s.write("e.csv", "d,n\n2000-01-01,0\n%s,%d\n" % (cell, n))
            r = run("--root", self.s.dir, "--compute", "r=add_days(d:date,n:int)", "e.csv")
            self.assertEqual(r.first_rule(), "compute.date-out-of-range", (cell, n))
            self.assertEqual(r.status, 8)
            self.assertEqual(r.error()["detail"]["row"], 2)
            self.assertNotIn(cell, json.dumps(r.error()["detail"]), cell)  # the row, the line and the definition, never the cell
        # the ends themselves are fine
        self.s.write("ok.csv", "d,n\n9999-12-31,0\n0001-01-01,0\n9999-12-30,1\n0001-01-02,-1\n")
        self.assertEqual([l.split(",")[2] for l in self.out("r=add_days(d:date,n:int)", "ok.csv").split("\n")[1:5]], ["9999-12-31", "0001-01-01", "9999-12-31", "0001-01-01"])

    def test_the_text_functions_and_the_conditions_around_it(self):
        self.s.write("t.csv", "d,n\n2026-11-16,30\n")
        rows = list(csv.reader(io.StringIO(self.out('a=concat(add_days(d:date,n:int)," x");b=substr(add_days(d:date,1),1,4);c=upper(add_days("2026-01-01",-1));e=replace(add_days(d:date,0),"-","/")', "t.csv"))))
        self.assertEqual(rows[1][2:], ["2026-12-16 x", "2026", "2025-12-31", "2026/11/16"])

    def test_json_writes_a_string(self):
        self.s.write("j.csv", "d\n2026-02-28\n")
        r = run("--root", self.s.dir, "--compute", "r=add_days(d:date,1)", "j.csv")
        self.assertEqual(r.data()["rows"], [["2026-02-28", "2026-03-01"]])

    def test_a_cell_that_is_not_a_date_or_not_an_integer_is_refused_with_the_readers_rule(self):
        self.s.write("b.csv", "d,n\n2026-01-01,1\n2026-02-30,1\n")
        r = run("--root", self.s.dir, "--compute", "r=add_days(d:date,n:int)", "b.csv")
        d = r.error()["detail"]
        self.assertEqual((r.first_rule(), d["column"], d["row"], d["context"]), ("value.not-date", "d", 2, "compute"))
        self.assertEqual((d["reason"], d["offset"]), ("day", 8))
        self.s.write("c.csv", "d,n\n2026-01-01,1\n2026-01-01,x\n")
        r = run("--root", self.s.dir, "--compute", "r=add_days(d:date,n:int)", "c.csv")
        d = r.error()["detail"]
        self.assertEqual((r.first_rule(), d["column"], d["row"], d["context"]), ("value.not-integer", "n", 2, "compute"))

    def test_the_compile_refusals(self):
        self.s.write("c.csv", "d,n,t\n2026-01-01,1,x\n")
        for defs, rule in [("r=add_days(t,1)", "compute.type-mismatch"), ("r=add_days(d:date)", "compute.arity"), ("r=add_days(d:date,1,2)", "compute.arity"), ("r=add_days()", "compute.arity"),
                           ("r=add_days(d:date,n:dec(2))", "compute.type-mismatch"), ("r=add_days(d:date,t)", "compute.type-mismatch"), ("r=add_days(d:date,1.5)", "compute.bad-literal"),
                           ('r=add_days("2026-02-30",1)', "compute.bad-literal"), ("r=add_days(d:date,n:int)+1", "compute.type-mismatch"), ("r=1+add_days(d:date,1)", "compute.type-mismatch"),
                           ("r=len(add_days(d:date,1))", "compute.type-mismatch"), ("r=add_days(1,1)", "compute.type-mismatch"), ("r=Add_days(d:date,1)", "compute.unknown-function")]:
            r = run("--root", self.s.dir, "--compute", defs, "c.csv")
            self.assertEqual(r.first_rule(), rule, defs)
            self.assertEqual(r.status, 2, defs)

    def test_a_date_longer_than_the_line_limit_is_refused(self):
        self.s.write("l.csv", "x\ny\n")
        r = run("--root", self.s.dir, "--compute", 'r=add_days("2026-01-01",1)', "--max-line-bytes", "8", "l.csv")
        self.assertEqual(r.first_rule(), "compute.result-too-long")
        self.assertEqual(self.out('r=add_days("2026-01-01",1)', "l.csv").split("\n")[1], "y,2026-01-02")
        # an intermediate date past the limit is refused too, as any intermediate text is (docs/functions.md section 3.3), though what is written is 4 bytes
        r = run("--root", self.s.dir, "--compute", 'r=substr(add_days("2026-01-01",1),1,4)', "--max-line-bytes", "8", "l.csv")
        self.assertEqual(r.first_rule(), "compute.result-too-long")

    def test_the_explain(self):
        self.s.write("x.csv", "d,n\n2026-01-01,1\n")
        e = run("--root", self.s.dir, "--compute", "r=add_days(d:date,n:int)", "--explain", "x.csv").data()["explain"]
        possible = [x if isinstance(x, str) else x.get("rule") for x in e["refusals"]["possible"]]
        for rule in ("compute.date-out-of-range", "value.not-date", "value.not-integer", "compute.result-too-long"):
            self.assertIn(rule, possible, rule)
        self.assertEqual(e["stage"]["definitions"][0]["kind"], "text")
        e = run("--root", self.s.dir, "--compute", 'r=add_days("2026-01-01",1)', "--explain", "x.csv").data()["explain"]
        possible = [x if isinstance(x, str) else x.get("rule") for x in e["refusals"]["possible"]]
        self.assertNotIn("value.not-date", possible)
        self.assertNotIn("value.not-integer", possible)
        self.assertIn("compute.date-out-of-range", possible)


if __name__ == "__main__":
    unittest.main()
