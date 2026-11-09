"""`--default-limit N` (docs/mcp.md section 13, issue #130): the page a json answer of rows or groups gets when the call gives none.

The gates: without the flag (or with 0, or with 1000) the answers are those of `main`; with it, a call that gives no `--limit`, `--top` or query limit gets the page, says it was cut as ever, and shows it in `--explain`; the forms that take no page (the shape,
the report, csv and JSON lines) and the calls that give one are as they were; a bad value is a refusal with its rule.
"""

import csv
import io
import json
import unittest

from harness import Scratch, run


class DefaultLimit(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["id", "k", "v"])
        for i in range(300):
            w.writerow([i, "k%d" % (i % 7), (i * 31) % 97])
        self.s.write("t.csv", out.getvalue())
        self.root = ["--root", str(self.s.dir)]

    def tearDown(self):
        self.s.cleanup()

    def go(self, *args):
        return run(*self.root, *args)

    def data(self, *args):
        r = self.go(*args)
        self.assertEqual(r.status, 0, (args, r.stdout[:200], r.stderr[:200]))
        return r.data()

    def test_without_the_flag_with_zero_and_with_the_old_default_the_bytes_are_the_same(self):
        for form in (["--select", "id", "t.csv"], ["--group", "k", "--agg", "count", "t.csv"], ["t.csv"], ["--report", "types", "t.csv"], ["--query", "select id from 't.csv'"]):
            base = self.go(*form)
            for value in ("0", "1000"):
                self.assertEqual(self.go("--default-limit", value, *form).stdout, base.stdout, (form, value))

    def test_a_page_is_given_to_rows_groups_and_a_query_and_said_to_be_cut(self):
        d = self.data("--default-limit", "20", "--select", "id", "t.csv")
        self.assertEqual((d["row_count"], d["truncated"], d["next"]), (20, True, {"from": 20}))
        again = self.data("--default-limit", "20", "--select", "id", "--from", "20", "t.csv")
        self.assertEqual(again["rows"][0], ["20"])
        g = self.data("--default-limit", "3", "--group", "k", "--agg", "count", "t.csv")
        self.assertEqual((g["row_count"], g["truncated"], g["group_count"]), (3, True, 7))
        q = self.data("--default-limit", "4", "--query", "select id from 't.csv'")
        self.assertEqual((q["row_count"], q["truncated"]), (4, True))
        j = self.data("--default-limit", "5", "--select", "id", "--where", "id:int < 100", "t.csv")
        self.assertEqual(j["row_count"], 5)

    def test_a_page_of_the_calls_own_is_kept(self):
        self.assertEqual(self.data("--default-limit", "20", "--limit", "50", "--select", "id", "t.csv")["row_count"], 50)
        self.assertEqual(self.data("--default-limit", "20", "--limit", "5", "--select", "id", "t.csv")["row_count"], 5)
        self.assertEqual(self.data("--default-limit", "20", "--top", "60", "--order-by", "id:int", "t.csv")["row_count"], 60)
        self.assertEqual(self.data("--default-limit", "20", "--query", "select id from 't.csv' limit 40")["row_count"], 40)
        self.assertEqual(self.data("--default-limit", "20", "--query", "select id from 't.csv' limit 40 offset 5")["row_count"], 40)

    def test_the_forms_that_take_no_page_are_as_they_were(self):
        self.assertEqual(self.data("--default-limit", "2", "t.csv")["row_count"], 300)
        self.assertEqual(self.data("--default-limit", "1", "--report", "types", "t.csv")["row_count"], 3)
        for form in ("csv", "jsonl"):
            a = self.go("--default-limit", "2", "--select", "id", "--format", form, "t.csv").stdout
            self.assertEqual(a, self.go("--select", "id", "--format", form, "t.csv").stdout, form)
            self.assertEqual(len(a.splitlines()), 301 if form == "csv" else 300)

    def test_explain_shows_the_page(self):
        d = self.data("--default-limit", "20", "--explain", "--select", "id", "t.csv")
        self.assertEqual(d["explain"]["plan"]["limit"] if "plan" in d["explain"] else d["explain"]["limit"], 20)
        base = self.data("--explain", "--select", "id", "t.csv")
        self.assertEqual(base["explain"]["plan"]["limit"] if "plan" in base["explain"] else base["explain"]["limit"], 1000)

    def test_a_bad_value_is_a_refusal_with_its_rule_for_every_form(self):
        for form in (["--select", "id", "t.csv"], ["t.csv"], ["--select", "id", "--limit", "5", "t.csv"], ["--report", "types", "t.csv"]):
            r = self.go("--default-limit", "1000001", *form)
            self.assertEqual(r.status, 2, form)
            e = r.error()
            self.assertEqual((e["rule"], e["detail"]["flags"]), ("args.bad-value", "--default-limit"), form)
        r = self.go("--default-limit", "1000001", "--select", "id", "--format", "csv", "t.csv")
        self.assertEqual(r.status, 2)
        self.assertIn("args.bad-value", r.stderr.decode())
        r = self.go("--default-limit", "x", "--select", "id", "t.csv")
        self.assertEqual(r.status, 2)
        r = self.go("--default-limit", "1000000", "--explain", "--select", "id", "t.csv")
        self.assertEqual(r.status, 0)
        d = r.data()["explain"]
        self.assertEqual(d["plan"]["limit"] if "plan" in d else d["limit"], 1000000)

    def test_it_is_in_introspect_and_works_with_a_join_and_in_the_equals_spelling(self):
        from harness import introspect
        f = {x["name"]: x for x in introspect()["flags"]}["--default-limit"]
        self.assertEqual(f["kind"], "nat")
        self.s.write("b.csv", "k,label\nk1,x\nk2,y\n")
        d = self.data("--default-limit=2", "--join", "b.csv", "--on", "k", "--join-prefix", "r_", "--select", "id,r_label", "t.csv")
        self.assertEqual(d["row_count"], 2)


if __name__ == "__main__":
    unittest.main()
