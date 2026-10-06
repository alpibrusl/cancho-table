"""The grammar of --where and every refusal of the filter and the grouping."""

import random
import unittest

from harness import Scratch, run_argv, validate


class Grammar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.data = "a,b c,i,#3,and\n1,x,10,p,q\n2,y z,20,r,s\n"

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def where(self, expr, *more):
        return self.s.table("w.csv", self.data, "--where", expr, "--select", "a", *more)

    def rows(self, expr):
        got = self.where(expr)
        self.assertEqual(validate(got), [], (expr, got))
        self.assertEqual(got.status, 0, (expr, got.doc()))
        return [r[0] for r in got.data()["rows"]]

    def test_the_language(self):
        for expr, want in (
            ("a=1", ["1"]), ("a = 1", ["1"]), ("a!=1", ["2"]), ("a<2", ["1"]), ("a<=2", ["1", "2"]), ("a>1", ["2"]), ("a>=1", ["1", "2"]),
            ("'b c'='x'", ["1"]), ("b\\ c = 'y z'", ["2"]), ("'b c' contains z", ["2"]), ("'b c' contains ''", ["1", "2"]),
            ("i:int > 9", ["1", "2"]), ("i:int>=20", ["2"]), ("i:int in (10,30)", ["1"]), ("a in (2, 3)", ["2"]), ("a in('1')", ["1"]),
            ("i > 9", []), ("i > 5", []), ("i < 5", ["1", "2"]),   # text: "10" and "20" sort before "5" and "9"
            ("'i':int = 10", ["1"]), ("a=1 and i:int=10", ["1"]), ("a=1 and i:int=20", []), ("  a = 1   and   b\\ c = x  ", ["1"]),
            ("'#3' = p", ["1"]), ("\\#3 = r", ["2"]), ("#4 = r", ["2"]), ("#1 = 2", ["2"]), ("'and' = s", ["2"]), ("and = s", ["2"]),
            ("a = '1'", ["1"]), ("b\\ c = 'x'", ["1"]),
        ):
            with self.subTest(expr):
                self.assertEqual(self.rows(expr), want)

    def test_text_is_bytewise_and_int_is_numeric(self):
        data = "n\n9\n10\n100\n-5\n"
        got = self.s.table("x.csv", data, "--where", "n > 9", "--select", "n")
        self.assertEqual(got.data()["rows"], [])          # "10" < "9" as text
        got = self.s.table("x.csv", data, "--where", "n:int > 9", "--select", "n")
        self.assertEqual(got.data()["rows"], [["10"], ["100"]])
        got = self.s.table("x.csv", data, "--where", "n:int < 0", "--select", "n")
        self.assertEqual(got.data()["rows"], [["-5"]])
        got = self.s.table("x.csv", "n\n\xe9\nz\nZ\n", "--where", "n > Z", "--select", "n")
        self.assertEqual(got.data()["rows"], [["\xe9"], ["z"]])

    def test_syntax_errors_say_where(self):
        for expr, offset in (
            (" ", 1), ("a", 1), ("a =", 3), ("a ! 5", 2), ("a == 5", 3), ("a = 5 b = 6", 6), ("a = 5 and", 9),
            ("a in 5", 5), ("a in (1 2)", 8), ("a in ()", 6), ("a in (1,", 8), ("a:int = x", 8), ("a:int contains 5", 6),
            ("'abc = 5", 0), ("a\\", 0), ("a = 'x", 4), ("= 5", 0), ("a = 5 and and", 13), ("a <> 5", 3), ("(a) = 5", 0),
            ("a:int = 9223372036854775808", 8), ("a:int = 1.5", 8), ("a:int = ''", 8), ("a or b", 2),
        ):
            with self.subTest(expr):
                got = self.where(expr)
                self.assertEqual(validate(got), [], expr)
                self.assertEqual((got.status, got.first_rule()), (2, "where.syntax"), (expr, got))
                d = got.error()["detail"]
                self.assertEqual((d["offset"], d["expression"]), (offset, expr), got.doc())
                self.assertTrue(d["expected"])
        # what was expected is said in words, and a keyword is only a keyword unquoted
        for expr, word in (("a in 5", "( after in"), ("a in (1 2)", ", or )"), ("a", "an operator"), ("a = 5 b", "and"), ("a:int contains 5", "contains compares text"), ("'a = 5", "closing quote")):
            self.assertIn(word, self.where(expr).error()["detail"]["expected"], expr)
        got = self.where("a 'contains' x")
        self.assertEqual((got.first_rule(), got.error()["detail"]["offset"]), ("where.syntax", 2))
        got = self.where("a 'in' (1)")
        self.assertEqual(got.first_rule(), "where.syntax")
        got = self.where("a = 5 'and' b = 6")
        self.assertEqual((got.first_rule(), got.error()["detail"]["offset"]), ("where.syntax", 6))
        got = self.where(" and ".join("a=1" for _ in range(65)))
        self.assertEqual((got.status, got.first_rule()), (2, "where.syntax"))
        got = self.where("a in (" + ",".join("x" for _ in range(1100)) + ")")
        self.assertEqual((got.status, got.first_rule()), (2, "where.syntax"))
        ok = self.where(" and ".join("a=1" for _ in range(64)))
        self.assertEqual(ok.status, 0)

    def test_columns_are_resolved_against_the_header(self):
        got = self.where("nope = 1")
        self.assertEqual((got.status, got.first_rule()), (3, "column.unknown"))
        d = got.error()["detail"]
        self.assertEqual((d["flag"], d["name"], d["available"]), ("--where", "nope", ["a", "b c", "i", "#3", "and"]))
        got = self.s.table("d.csv", "a,a\n1,2\n", "--where", "a = 1")
        self.assertEqual((got.status, got.first_rule()), (8, "column.ambiguous"))
        got = self.s.table("d.csv", "a,a\n1,2\n", "--where", "#2 = 2", "--select", "#1")
        self.assertEqual(got.data()["rows"], [["1"]])
        got = self.where("#9 = 1")
        self.assertEqual(got.first_rule(), "column.unknown")
        got = self.s.table("d.csv", "a\n1\n", "--select", "a", "--where", "zz=1")
        self.assertEqual((got.first_rule(), got.error()["detail"]["flag"]), ("column.unknown", "--where"))
        got = self.s.table("d.csv", "a\n1\n", "--select", "zz", "--where", "a=1")
        self.assertEqual((got.first_rule(), got.error()["detail"]["flag"]), ("select.unknown-column", "--select"))
        # the choose repair of --select still works beside a --where
        got = self.s.table("d.csv", "Aa\n1\n", "--select", "aa", "--where", "Aa=1")
        self.assertEqual(got.error()["repair"]["kind"], "choose")
        again = run_argv(got.error()["repair"]["options"][0]["argv"])
        self.assertEqual(again.data()["rows"], [["1"]])

    def test_integers_are_exact(self):
        ok = ["0", "-0", "+0", "007", "9223372036854775807", "-9223372036854775808", "+9223372036854775807"]
        got = self.s.table("n.csv", "n\n" + "\n".join(ok) + "\n", "--where", "n:int >= -9223372036854775808", "--select", "n")
        self.assertEqual(len(got.data()["rows"]), len(ok))
        for cell, rule in (("", "value.not-integer"), (" 1", "value.not-integer"), ("1 ", "value.not-integer"), ("1.0", "value.not-integer"),
                           ("1e3", "value.not-integer"), ("0x10", "value.not-integer"), ("-", "value.not-integer"), ("+", "value.not-integer"),
                           ("--1", "value.not-integer"), ("1_000", "value.not-integer"), ("9223372036854775808", "value.integer-overflow"),
                           ("-9223372036854775809", "value.integer-overflow"), ("99999999999999999999999", "value.integer-overflow"),
                           ("١", "value.not-integer")):
            with self.subTest(cell):
                got = self.s.table("n.csv", 'k,n\nx,5\ny,"%s"\n' % cell, "--where", "n:int > 0", "--select", "k")
                self.assertEqual((got.status, got.first_rule()), (8, rule), got)
                d = got.error()["detail"]
                self.assertEqual((d["row"], d["line"], d["column"], d["value"], d["context"]), (2, 3, "n", cell, "where"))
                self.assertNotIn("data", got.doc())
        # a long cell is kept only to 64 bytes
        got = self.s.table("n.csv", "n\n" + "9" * 100 + "\n", "--where", "n:int > 0", "--select", "n")
        self.assertEqual((len(got.error()["detail"]["value"]), got.error()["detail"]["value_truncated"]), (64, True))
        # an empty cell is only refused when it is reached: the order of conditions guards it
        got = self.s.table("n.csv", "k,n\nx,\ny,3\n", "--where", "n != '' and n:int > 0", "--select", "k")
        self.assertEqual(got.data()["rows"], [["y"]])

    def test_sums_are_exact_and_never_wrap_or_refuse(self):
        # N0p: a sum is a pair of integers; past 64 bits it is printed in full (Python's int is the oracle)
        mx, mn = "9223372036854775807", "-9223372036854775808"
        for rows in (["9223372036854775806", "1"], [mn.replace("8", "7"), "-1"], [mn, "-1"], ["1", mx], [mx, "1"], [mx, "-1", "1"], [mx, "0"], [mn, "1", "-1"], [mx, mn],
                     [mx] * 3, [mn] * 3, ["5000000000000000000"] * 2, ["5000000000000000000"] * 4 + ["7"], ["-5000000000000000000"] * 4, ["5000000000000000000"] * 200 + ["1"], [mx] * 1000, [mn] * 999 + ["5"], ["4294967296", "4294967295"] * 7, ["-4294967297"] * 5):
            with self.subTest(rows):
                got = self.s.table("s.csv", "v\n" + "\n".join(rows) + "\n", "--agg", "sum:v")
                self.assertEqual(got.status, 0, got)
                self.assertEqual(got.data()["rows"], [[str(sum(int(r) for r in rows))]])
        got = self.s.table("s.csv", "v\n%s\n%s\n" % (mx, mn), "--agg", "min:v,max:v,count")
        self.assertEqual(got.data()["rows"], [[mn, mx, "2"]])
        got = self.s.table("s.csv", "v\n3\nx\n", "--agg", "min:v")
        self.assertEqual((got.first_rule(), got.error()["detail"]["context"], got.error()["detail"]["column"]), ("value.not-integer", "min", "v"))
        got = self.s.table("s.csv", 'v\n3\n""\n4\n', "--agg", "max:v")
        self.assertEqual(got.first_rule(), "value.not-integer")

    def test_the_int_suffix_can_be_escaped(self):
        data = "n:int,m\n5,a\n10,b\n"
        got = self.s.table("e.csv", data, "--where", "n\\:int = 5", "--select", "m")
        self.assertEqual(got.data()["rows"], [["a"]])
        got = self.s.table("e.csv", data, "--where", "'n:int':int > 5", "--select", "m")
        self.assertEqual(got.data()["rows"], [["b"]])
        got = self.s.table("e.csv", data, "--where", "'n:int' > 5", "--select", "m")
        self.assertEqual(got.data()["rows"], [])          # text: "10" and "5" against "5"... only "5" > "5" is false

    def test_agg_and_sort_refusals(self):
        data = "k,v\na,1\nb,2\n"
        for agg in ("avg:v", "sum", "sum:", ":v", "count:v", "count,median:v", "SUM:v", "sum :v"):
            with self.subTest(agg):
                got = self.s.table("a.csv", data, "--group", "k", "--agg", agg)
                self.assertEqual((got.status, got.first_rule()), (2, "agg.bad-spec"), got)
        got = self.s.table("a.csv", data, "--group", "k", "--agg", "count,median:v")
        self.assertEqual(got.error()["detail"]["item"], 1)
        got = self.s.table("a.csv", data, "--group", "k", "--agg", "sum:v", "--sort", "nope")
        self.assertEqual((got.status, got.first_rule()), (2, "sort.unknown-key"))
        self.assertEqual(got.error()["detail"]["available"], ["k", "sum:v"])
        got = self.s.table("a.csv", data, "--group", "k", "--sort", "sum:v")
        self.assertEqual(got.first_rule(), "sort.unknown-key")
        got = self.s.table("a.csv", data, "--group", "k", "--agg", "sum:zz")
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["flag"]), (3, "column.unknown", "--agg"))
        got = self.s.table("a.csv", data, "--group", "zz")
        self.assertEqual((got.first_rule(), got.error()["detail"]["flag"]), ("column.unknown", "--group"))
        got = self.s.table("a.csv", data, "--group", "k", "--agg", "sum:#2,distinct:#1")
        self.assertEqual(got.data()["rows"], [["a", "1", "1"], ["b", "2", "1"]])

    def test_flag_combinations(self):
        data = "k,v\na,1\n"
        for args, rule in (
            (["--group", "k", "--select", "k"], "args.conflict"),
            (["--agg", "count", "--select", "k"], "args.conflict"),
            (["--sort", "count"], "args.required-flag"),
            (["--top", "3"], "args.required-flag"),
            (["--group", "k", "--top", "0"], "args.bad-value"),
            (["--group", "k", "--format", "text"], "args.conflict"),
            (["--where", "k=a", "--format", "text"], "args.conflict"),
            (["--group", "k", "--max-groups", "0"], "args.bad-value"),
            (["--group", "k", "--max-groups", "1000001"], "args.bad-value"),
            (["--group", "k", "--max-distinct", "10000001"], "args.bad-value"),
            (["--group", "k", "--max-state-bytes", "1073741825"], "args.bad-value"),
            (["--group", "k\\"], "args.bad-value"),
            (["--group", "k", "--agg", "count\\"], "args.bad-value"),
            (["--where", "k=a", "--where", "k=b"], "args.duplicate-flag"),
        ):
            with self.subTest(args):
                got = self.s.table("f.csv", data, *args)
                self.assertIn(rule, got.stdout.decode() + got.stderr.decode())
                self.assertEqual(got.status, 2)

    def test_group_bounds(self):
        data = "k,v\n" + "".join("k%d,v%d\n" % (i, i % 5) for i in range(50))
        got = self.s.table("b.csv", data, "--group", "k", "--max-groups", "10")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.too-many-groups"))
        self.assertEqual(got.error()["detail"]["limit"], 10)
        self.assertNotIn("data", got.doc())
        got = self.s.table("b.csv", data, "--group", "k", "--max-groups", "50")
        self.assertEqual(got.data()["group_count"], 50)
        got = self.s.table("b.csv", data, "--group", "k", "--max-groups", "49")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.too-many-groups"))
        got = self.s.table("b.csv", data, "--agg", "distinct:k", "--max-distinct", "49")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.too-many-distinct"))
        got = self.s.table("b.csv", data, "--agg", "distinct:k", "--max-distinct", "50")
        self.assertEqual(got.data()["rows"], [["50"]])
        got = self.s.table("b.csv", data, "--group", "k", "--max-state-bytes", "100")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.state-too-large"))
        got = self.s.table("b.csv", data, "--agg", "distinct:k", "--max-state-bytes", "100")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.state-too-large"))
        # csv: the rows before the refusal stand
        got = self.s.table("b.csv", data, "--group", "k", "--max-groups", "10", "--format", "csv")
        self.assertEqual(got.status, 8)
        self.assertIn(b"limit.too-many-groups", got.stderr)

    def test_group_pages_and_csv(self):
        rows = "".join("k%02d,%d\n" % (i % 23, i) for i in range(230))
        data = "k,v\n" + rows
        full = self.s.table("c.csv", data, "--group", "k", "--agg", "count,sum:v", "--limit", 1000)
        self.assertEqual(full.data()["group_count"], 23)
        seen, at = [], 0
        while True:
            got = self.s.table("c.csv", data, "--group", "k", "--agg", "count,sum:v", "--limit", 5, "--from", at)
            seen += got.data()["rows"]
            if got.data()["next"] is None:
                break
            at = got.data()["next"]["from"]
        self.assertEqual(seen, full.data()["rows"])
        got = self.s.table("c.csv", data, "--group", "k", "--agg", "count,sum:v", "--format", "csv")
        want = "k,count,sum:v\n" + "".join(",".join(r) + "\n" for r in full.data()["rows"])
        self.assertEqual(got.stdout.decode(), want)
        got = self.s.table("c.csv", data, "--group", "k", "--sort", "-count", "--top", "3", "--limit", 2)
        self.assertEqual((len(got.data()["rows"]), got.data()["truncated"], got.data()["next"], got.data()["group_count"]), (2, True, {"from": 2}, 23))
        # a grouping with no rows is no groups
        got = self.s.table("c.csv", data, "--where", "k = nope", "--agg", "count")
        self.assertEqual((got.data()["rows"], got.data()["group_count"]), ([], 0))
        # quoted, multi-line and empty keys survive, in csv and json
        got = self.s.table("c.csv", 'k,v\n"a,b",1\n"l1\nl2",2\n,3\n"a,b",4\n"q""q",5\n', "--group", "k", "--agg", "sum:v", "--format", "csv")
        self.assertEqual(got.stdout, b'k,sum:v\n,3\n"a,b",5\n"l1\nl2",2\n"q""q",5\n')

    def test_from_pages_the_groups_and_skips_no_row(self):
        # (a bug of the first grouping: --from also skipped that many rows before counting)
        data = "k,v\nb,1\nb,1\na,1\na,1\na,1\n"
        got = self.s.table("fr.csv", data, "--group", "k", "--agg", "count,sum:v", "--from", 1)
        self.assertEqual((got.data()["rows"], got.data()["group_count"]), ([["b", "2", "2"]], 2))
        got = self.s.table("fr.csv", data, "--group", "k", "--agg", "count", "--from", 1, "--format", "csv")
        self.assertEqual(got.stdout, b"k,count\nb,2\n")

    def test_ragged_rows_are_not_filtered_or_grouped(self):
        got = self.s.table("r.csv", "a,b\n1,x\n2\n3,y,z\n4,x\n", "--group", "b", "--agg", "count,sum:a")
        self.assertEqual(got.first_rule(), "parse.csv-ragged-row")
        self.assertEqual(got.data()["rows"], [["x", "2", "5"]])

    def test_never_a_trap(self):
        rng = random.Random(11)
        plans = (["--where", "a:int > 1 and b = x"], ["--where", "#1 in (1, 2)", "--format", "csv"], ["--group", "a", "--agg", "count,sum:b,min:b,max:b,distinct:b"],
                 ["--agg", "sum:#1", "--max-groups", "1", "--max-line-bytes", "7"], ["--group", "#1,#2", "--sort", "-count", "--top", "2", "--delimiter", "tab"],
                 ["--where", "a contains '\"'", "--select", "b", "--limit", "1", "--from", "1"])
        for i in range(300):
            data = bytes(rng.choice(b'ab,1,\t"\r\n\x00\xff -9') for _ in range(rng.randint(0, 80)))
            for flags in plans:
                got = self.s.table("z.csv", data, *flags)
                self.assertNotIn(got.status, (-4, -6, -8, -11, 132, 134, 136, 139), (data, flags))
                if "csv" not in flags:
                    self.assertEqual(validate(got), [], (data, flags))


if __name__ == "__main__":
    unittest.main()
