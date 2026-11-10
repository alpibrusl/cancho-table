"""`--na`, `--decimal` and `--thousands` (docs/reader-options.md, O2 and O3, issue #109): the row normalizer.

* O, the oracle: a file written in the other syntax (markers for the empty cell, `1.234,5` for `1234.5`), read with the options, answers as the file a script cleaned, for the aggregates, groups, sorted rows, filters,
  csv out, standard input and a parallel-sized file.
* S, the rules: a cell that fits neither syntax is refused as the plain readers refuse it, naming the syntax in force and showing the cell as written; a plain point beside a stated comma is refused; `--thousands` alone
  keeps the point; a marker inside quotes that hold a quote is not one; the options are refused with `--join` and JSON lines.
"""

import csv
import io
import json
import random
import unittest

from harness import Scratch, run

HEADER = ["id", "k", "v", "w"]


def euro(x):
    s = "{:,.2f}".format(x)
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def encode(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(HEADER)
    w.writerows(rows)
    return out.getvalue()


def make(rng, n):
    dirty, clean = [], []
    for i in range(n):
        k = rng.choice(["a", "b", "c", "x,y", 'say "hi"', "?"])
        x = round(rng.uniform(-5e6, 5e6), 2)
        if rng.random() < 0.15:
            dv, cv = rng.choice(["?", "NA"]), ""
        else:
            dv, cv = euro(x), "{:.2f}".format(x)
        wm = rng.choice(["?", "NA", "7", "x"])
        dirty.append([str(i), k, dv, wm])
        clean.append([str(i), "" if k == "?" else k, cv, "" if wm in ("?", "NA") else wm])
    return dirty, clean


OPTS = ["--decimal", ",", "--thousands", ".", "--na", "?,NA"]

FORMS = [
    ("agg", ["--agg", "count,sum:v:float"]),
    ("groups", ["--group", "k", "--agg", "count,sum:v:dec(2)", "--sort", "k"]),
    ("where", ["--select", "id,v", "--where", "v:float > 1000"]),
    ("sorted", ["--order-by", "-v:float,id:int", "--select", "id,k,w", "--limit", "25"]),
    ("shape", []),
    ("rows", ["--select", "id,k,w", "--format", "csv"]),
]


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.root = ["--root", str(self.s.dir)]

    def tearDown(self):
        self.s.cleanup()


class Oracle(Base):
    def check(self, n, extra=(), seed=7):
        rng = random.Random(seed)
        dirty, clean = make(rng, n)
        self.s.write("dirty.csv", encode(dirty))
        self.s.write("clean.csv", encode(clean))
        for label, form in FORMS:
            a = run(*self.root, *OPTS, *extra, *form, "dirty.csv")
            b = run(*self.root, *extra, *form, "clean.csv")
            self.assertEqual(a.status, b.status, (label, a.stdout[:300]))
            if a.status == 0:
                if label == "rows":
                    self.assertEqual(a.stdout, b.stdout, label)
                else:
                    self.assertEqual(a.data(), b.data(), label)

    def test_small(self):
        for seed in range(5):
            self.check(rng_n(seed), seed=seed)

    def test_threads(self):
        self.check(3000, ["--threads", "1"])

    def test_threads_asked(self):
        self.check(20000, ["--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "4096"])

    def test_big(self):
        self.check(30000)


def rng_n(seed):
    return random.Random(seed).randrange(0, 60)


class Rules(Base):
    def write(self, text):
        self.s.write("t.csv", text)

    def test_plain_beside_stated_is_refused_and_says_the_syntax(self):
        self.write("id,v\n1,\"1.234,5\"\n2,12.34\n")
        r = run(*self.root, "--decimal", ",", "--thousands", ".", "--agg", "sum:v:float", "t.csv")
        self.assertNotEqual(r.status, 0)
        d = r.error()
        self.assertEqual(d["rule"], "value.not-float")
        self.assertEqual(d["detail"]["value"], "12.34")
        self.assertEqual(d["detail"]["syntax"], "decimal , thousands .")

    def test_ambiguous_group_is_refused(self):
        self.write("id,v\n1,\"1.23\"\n")
        r = run(*self.root, "--decimal", ",", "--thousands", ".", "--agg", "sum:v:dec(2)", "t.csv")
        self.assertNotEqual(r.status, 0)
        d = r.error()
        self.assertEqual(d["rule"], "value.not-decimal")
        self.assertEqual(d["detail"]["value"], "1.23")

    def test_thousands_alone_is_refused(self):
        self.write("id,v\n1,\"1,234.5\"\n")
        r = run(*self.root, "--thousands", ",", "--agg", "sum:v:float", "t.csv")
        self.assertEqual(r.error()["rule"], "args.required-flag")

    def test_point_and_comma_groups(self):
        self.write("id,v\n1,\"1,234.5\"\n2,2\n")
        r = run(*self.root, "--decimal", ".", "--thousands", ",", "--agg", "sum:v:float", "t.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        self.assertIn(b"1236.5", r.stdout)

    def test_na_makes_the_empty_cell_in_the_output_too(self):
        self.write("id,v\n1,?\n2,3\n")
        r = run(*self.root, "--na", "?", "--format", "csv", "--select", "id,v", "t.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        self.assertIn(b"1,\n", r.stdout)
        self.assertNotIn(b"?", r.stdout)

    def test_refused_with_join_and_jsonl(self):
        self.write("id,v\n1,2\n")
        r = run(*self.root, "--na", "?", "--join", "t.csv", "--on", "id", "t.csv")
        self.assertEqual(r.error()["rule"], "args.conflict")
        self.s.write("j.jsonl", '{"id":1}\n')
        r = run(*self.root, "--decimal", ",", "--input-format", "jsonl", "--fields", "id", "j.jsonl")
        self.assertEqual(r.error()["rule"], "args.conflict")

    def test_explain_says_one_core(self):
        self.write("id,v\n" + "1,2\n" * 50)
        r = run(*self.root, "--explain", "--threads", "4", "--parallel-min-bytes", "1", "--na", "?", "--agg", "count", "t.csv")
        th = r.data()["explain"]["work"]["threads"]
        self.assertEqual((th["effective"], th["reason"]), (1, "--decimal, --thousands and --na are read on one core"))

    def test_stdin(self):
        r = run(*self.root, "--na", "?", "--agg", "count", "-", stdin=b"id,v\n1,?\n2,3\n")
        self.assertEqual(r.status, 0, r.stdout[:300])


# (cell, value) with --decimal , --thousands . ; a value of None is a cell the syntax refuses
GRAMMAR = [
    ("1234,5", 1234.5), ("1.234,5", 1234.5), ("12.345.678,9", 12345678.9), ("-1.234", -1234.0), ("+0,5", 0.5), ("1234", 1234.0),
    ("12,5e3", 12500.0), ("1.234e2", 123400.0), ("1,5e2", 150.0), ("-12,5E-1", -1.25), ("1,234", 1.234), ("123.456", 123456.0),
    ("1.23", None), ("12.34", None), ("1234.567", None), ("1.2345", None), (".234", None), ("1.234,5.6", None), ("1,2,3", None),
    ("1 234", None), ("1.234.56", None), ("1.2,5", None), ("1.23.456", None), ("1234.567,8", None), (".234,5", None), ("1,234.5", None),
    ("12.5", None), ("1.234,5x", None), ("x", None),
]


class Grammar(Base):
    def one(self, cell, *flags, agg="sum:v:float"):
        self.s.write("g.csv", "id,v\n1," + cell + "\n")
        return run(*self.root, *flags, "--agg", agg, "g.csv")

    def test_the_syntax_of_a_cell(self):
        for cell, want in GRAMMAR:
            r = self.one('"' + cell + '"', "--decimal", ",", "--thousands", ".")
            if want is None:
                self.assertNotEqual(r.status, 0, (cell, r.stdout[:200]))
                e = r.error()
                self.assertEqual((e["rule"], e["detail"]["value"], e["detail"]["syntax"]), ("value.not-float", cell, "decimal , thousands ."), cell)
            else:
                self.assertEqual(r.status, 0, (cell, r.stdout[:200]))
                self.assertEqual(float(r.data()["rows"][0][0]), want, cell)

    def test_decimal_alone_refuses_a_plain_point_and_keeps_no_group(self):
        r = self.one("12.34", "--decimal", ",")
        self.assertEqual(r.error()["detail"]["syntax"], "decimal ,")
        r = self.one('"12,34"', "--decimal", ",")
        self.assertEqual(float(r.data()["rows"][0][0]), 12.34)
        r = self.one('"1\x00234,5"', "--decimal", ",")
        self.assertNotEqual(r.status, 0)

    def test_an_integer_column_is_not_rewritten(self):
        r = self.one("1.234", "--decimal", ",", "--thousands", ".", agg="sum:v:int")
        self.assertEqual(r.error()["rule"], "value.not-integer")
        self.assertEqual(r.error()["detail"]["value"], "1.234")

    def test_only_a_typed_column_is_rewritten(self):
        self.s.write("g.csv", 'id,v,w\n1,"1.234,5","1.234,5"\n')
        r = run(*self.root, "--decimal", ",", "--thousands", ".", "--where", "v:float > 0", "--select", "v,w", "--format", "csv", "g.csv")
        self.assertEqual(r.stdout, b'v,w\n1234.5,"1.234,5"\n')

    def test_a_dec_column(self):
        r = self.one('"1.234,56"', "--decimal", ",", "--thousands", ".", agg="sum:v:dec(2)")
        self.assertEqual(r.data()["rows"][0][0], "1234.56")


class Markers(Base):
    def put(self, text):
        self.s.write("m.csv", text)

    def test_the_spellings_and_the_escapes(self):
        self.put('id,v\n1,"a,b"\n2,a\n3,b\n4,"a\\b"\n5,?\n')
        for flags, empty in [(["--na", "a\\,b"], {"1"}), (["--na=a\\,b,a"], {"1", "2"}), (["--na", "a\\\\b"], {"4"}), (["--na=?"], {"5"}), (["--na", "?,b"], {"3", "5"})]:
            r = run(*self.root, *flags, "--select", "id,v", "--format", "csv", "m.csv")
            self.assertEqual(r.status, 0, (flags, r.stdout[:200]))
            rows = [l.split(b",", 1) for l in r.stdout.splitlines()[1:]]
            got = {row[0].decode() for row in rows if len(row) == 2 and row[1] == b""}
            self.assertEqual(got, empty, flags)

    def test_a_list_that_is_not_well_formed_is_refused(self):
        self.put("id,v\n1,a\n")
        for bad in ["a,,b", "", ",a", 'a"b', "a\\x", "a\\"]:
            r = run(*self.root, "--na", bad, "m.csv")
            self.assertNotEqual(r.status, 0, bad)
            self.assertEqual(r.error()["rule"][:5], "args.", bad)

    def test_a_quoted_marker_is_the_empty_cell_unquoted(self):
        self.put('id,v\n1,"?"\n2,x\n')
        r = run(*self.root, "--na", "?", "--select", "id,v", "--format", "csv", "m.csv")
        self.assertEqual(r.stdout, b"id,v\n1,\n2,x\n")

    def test_markers_alone_in_sorted_rows_and_groups(self):
        self.put("id,v\n3,?\n1,x\n2,?\n")
        r = run(*self.root, "--na", "?", "--order-by", "id:int", "--select", "id,v", "--format", "csv", "m.csv")
        self.assertEqual(r.stdout, b"id,v\n1,x\n2,\n3,\n")
        r = run(*self.root, "--na", "?", "--group", "v", "--agg", "count", "--sort", "v", "--format", "csv", "m.csv")
        self.assertEqual(r.stdout, b"v,count\n,2\nx,1\n")
        r = run(*self.root, "--na", "?", "--where", "v = ''", "--select", "id", "--format", "csv", "m.csv")
        self.assertEqual(r.stdout, b"id\n3\n2\n")

    def test_standard_input_is_normalized_too(self):
        dirty, clean = make(random.Random(5), 200)
        for label, form in FORMS:
            a = run(*self.root, *OPTS, *form, stdin=encode(dirty).encode())
            b = run(*self.root, *form, stdin=encode(clean).encode())
            self.assertEqual(a.status, b.status, label)
            if a.status == 0:
                self.assertEqual(a.stdout if label == "rows" else a.data(), b.stdout if label == "rows" else b.data(), label)


if __name__ == "__main__":
    unittest.main()
