"""#107 (docs/refusals.md): a typed cell that is empty is refused with a repair, the call with the guard `COLUMN != ''` in front of `--where`.

The gates, each able to fail:

* the repair is offered for `value.not-integer`, `value.not-float` and `value.not-decimal` when the refused cell is empty, in a condition, an aggregate, a group and an order key; it is a single `choose`
  option, the whole invocation with `--where` added or changed (an existing expression follows the guard in parentheses, so a top-level `or` is not mixed with the `and`);
* running the repair gives what Python computes from the table with the empty rows left out (the repair does what the hint says, and nothing else);
* it is not offered for a cell that is not empty (nothing says what such a cell was meant to be), for a `--query` (it says its own `where`), or for a `--join-where`;
* a name that is not a plain word is quoted, and a name that two columns share is written by position.
"""

import csv
import io
import json
import unittest
from decimal import Decimal

from harness import Scratch, run

HEADER = ["host", "amount", "n", "x"]
ROWS = [["a", "1.50", "1", "0.5"], ["b", "", "", ""], ["a", "2.25", "3", "1.5"], ["c", "10.00", "4", "2.5"], ["b", "0.25", "", "3.5"]]


def text(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.s.write("t.csv", text(HEADER, ROWS))

    def tearDown(self):
        self.s.cleanup()

    def go(self, *argv):
        return run("--root", str(self.s.dir), *[str(a) for a in argv])

    def repair(self, r):
        """The argv of the one option of the repair, without the program: it already carries --root."""
        rep = r.error()["repair"]
        self.assertEqual(rep["kind"], "choose", rep)
        self.assertEqual(len(rep["options"]), 1)
        return rep["options"][0]["argv"][1:]

    def fixed(self, argv):
        r = run(*argv)
        self.assertEqual(r.status, 0, r.stdout[:300])
        return json.loads(r.stdout)["data"]["rows"]


class Offered(Base):
    def test_a_decimal_sum_over_an_empty_cell(self):
        r = self.go("--agg", "sum:amount:dec(2)", "t.csv")
        self.assertEqual(r.first_rule(), "value.not-decimal")
        want = sum(Decimal(row[1]) for row in ROWS if row[1] != "")
        self.assertEqual(self.fixed(self.repair(r)), [[str(want)]])

    def test_an_integer_condition(self):
        r = self.go("--where", "n:int > 1", "--select", "host,n", "t.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")
        argv = self.repair(r)
        self.assertIn("n != '' and (n:int > 1)", argv)
        got = self.fixed(argv)
        self.assertEqual(got, [[row[0], row[2]] for row in ROWS if row[2] != "" and int(row[2]) > 1])

    def test_an_integer_sum_by_group(self):
        r = self.go("--group", "host", "--agg", "sum:n:int", "t.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")
        got = self.fixed(self.repair(r))
        want = {}
        for row in ROWS:
            if row[2] != "":
                want[row[0]] = want.get(row[0], 0) + int(row[2])
        self.assertEqual(sorted(got), sorted([[k, str(v)] for k, v in want.items()]))

    def test_a_float_condition(self):
        r = self.go("--where", "x:float > 1", "--select", "host", "t.csv")
        self.assertEqual(r.first_rule(), "value.not-float")
        got = self.fixed(self.repair(r))
        self.assertEqual(got, [[row[0]] for row in ROWS if row[3] != "" and float(row[3]) > 1])

    def test_an_order_key(self):
        r = self.go("--order-by", "n:int", "--select", "host,n", "t.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")
        got = self.fixed(self.repair(r))
        kept = [row for row in ROWS if row[2] != ""]
        self.assertEqual(got, [[row[0], row[2]] for row in sorted(kept, key=lambda row: int(row[2]))])

    def test_an_existing_expression_with_an_or_is_put_in_parentheses(self):
        r = self.go("--where", "host = 'a' or n:int > 3", "--select", "host,n", "t.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")
        argv = self.repair(r)
        self.assertIn("n != '' and (host = 'a' or n:int > 3)", argv)
        got = self.fixed(argv)
        self.assertEqual(got, [[row[0], row[2]] for row in ROWS if row[2] != "" and (row[0] == "a" or int(row[2]) > 3)])

    def test_a_where_written_with_an_equals_sign_is_replaced_in_place(self):
        r = self.go("--where=n:int > 1", "--select", "host", "t.csv")
        argv = self.repair(r)
        self.assertIn("--where=n != '' and (n:int > 1)", argv)
        self.assertEqual(run(*argv).status, 0)

    def test_a_name_that_is_not_a_word_is_quoted(self):
        self.s.write("q.csv", text(["host", "my amt"], [["a", "1.5"], ["b", ""]]))
        r = self.go("--agg", "sum:my amt:dec(1)", "q.csv")
        self.assertEqual(r.first_rule(), "value.not-decimal")
        argv = self.repair(r)
        self.assertIn("'my amt' != ''", argv)
        self.assertEqual(self.fixed(argv), [["1.5"]])

    def test_a_name_that_two_columns_share_is_written_by_position(self):
        self.s.write("d.csv", text(["v", "v"], [["1", "2"], ["", "3"]]))
        r = self.go("--agg", "sum:#1:int", "d.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")
        argv = self.repair(r)
        self.assertIn("#1 != ''", argv)
        self.assertEqual(self.fixed(argv), [["1"]])


class NotOffered(Base):
    def reason(self, r):
        rep = r.error()["repair"]
        self.assertEqual(rep["kind"], "none", rep)
        return rep["reason"]

    def test_a_cell_that_is_not_empty_has_no_repair(self):
        self.s.write("w.csv", text(["host", "n"], [["a", "1"], ["b", "abc"]]))
        r = self.go("--agg", "sum:n:int", "w.csv")
        self.assertEqual(r.first_rule(), "value.not-integer")
        self.assertIn("not known", self.reason(r))
        r = self.go("--agg", "sum:n:dec(2)", "w.csv")
        self.assertIn("not known", self.reason(r))

    def test_a_query_has_no_repair(self):
        r = self.go("--query", "select sum(amount::dec(2)) from 't.csv'")
        self.assertEqual(r.first_rule(), "value.not-decimal")
        self.reason(r)

    def test_a_join_padded_cell_has_no_repair(self):
        # the cell is empty because the left join padded it: a guard in --where would turn the join into an inner join on that column, which is for the person to decide
        self.s.write("p.csv", text(["host", "n"], [["a", "1"], ["z", "2"]]))
        self.s.write("b.csv", text(["bk", "m"], [["a", "5"]]))
        r = self.go("--join", "b.csv", "--on", "host=bk", "--join-type", "left", "--agg", "sum:m:int", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        self.assertTrue(r.error()["detail"]["padded"])
        self.assertEqual(r.error()["repair"]["kind"], "none")

    def test_a_join_where_has_no_repair(self):
        self.s.write("b.csv", text(["bk", "m"], [["a", "1"], ["b", ""]]))
        r = self.go("--join", "b.csv", "--on", "host=bk", "--join-where", "m:int > 0", "t.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        self.assertEqual(r.error()["repair"]["kind"], "none")


if __name__ == "__main__":
    unittest.main()
