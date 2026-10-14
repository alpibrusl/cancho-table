"""Typed group keys are JSON numbers in `--format jsonl` (docs/readers.md section 13.3, N32; N18, which made them strings, is superseded).

These gates were written before the change and fail on a build that writes `"1.50"` for a `--group d:dec(2)` key. The oracle is jsonl_out_ref.py (an encoder and a
strict reader of its own, Python's `int`, `Fraction` and `float`); a number is judged as a token by Python's `json` with `parse_int=Num, parse_float=Num` (so `007`,
`.5`, `+1`, `NaN` and `Infinity` are refused, and the token is the text the tool wrote).

* **Golden** the key of every type at the edges: `int` past 2^53 and at 2^63, negative, `007`, `+7`, `-0`; `dec` with trailing zeros, below one, negative, scale 0 and 6;
  `float` with 17 digits, `-0`, exponents either side of the positional range, the extremes of the double.
* **Mixed** an untyped key stays a string, beside typed ones, in any place; `--sort`, `--top`, `--limit`, `--from`; the empty and the invalid cell of a typed key is the
  refusal csv gives, with no row; `--order-by` leaves the cells of a selection strings; `--format json`, `csv` and `--report types` as they were.
* **Differential** generated tables and plans that have at least one typed key: the oracle's numbers, and the tool's own csv.
* **Round trips** `csv -> jsonl -> csv` byte for byte for typed groups; `jsonl -> jsonl` (a key written as a number is read back as its exact text, and groups again).
* **Threads** 1, 2, 3, 7, 16 and 64 threads x chunks give the bytes of one thread; **fuzz** (in test_jsonl_out_more.py, whose plans have typed keys) no trap.
"""

import json
import os
import random
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jsonl_out_ref as R
import numbers_ref as N
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

from harness import TRAPS, binary, introspect, run
from test_jsonl_out import PAR, ROUNDS, Failures, Gated, agg_tail, exit_of, rule_of


def csv_of(rows, header="k,v"):
    return (header + "\n" + "".join("%s,1\n" % r for r in rows)).encode()


def key_lines(stdout, name="k"):
    """The tokens of the key of every line (strictly parsed, each a `Num`), and the lines."""
    out = []
    for pairs in R.read_lines(stdout):
        k, v = pairs[0]
        assert k == name, pairs
        assert type(v) is R.Num, "the key %r is not a JSON number: %r" % (k, v)
        out.append(str(v))
    return out


INTS = [
    ("9223372036854775807", "9223372036854775807"), ("-9223372036854775808", "-9223372036854775808"), ("9223372036854775806", "9223372036854775806"),
    ("-9223372036854775807", "-9223372036854775807"), ("9007199254740993", "9007199254740993"), ("9007199254740992", "9007199254740992"),
    ("-9007199254740993", "-9007199254740993"), ("007", "7"), ("+7", "7"), ("7", "7"), ("-0", "0"), ("0", "0"), ("+0", "0"), ("000", "0"), ("-007", "-7"),
    ("-5", "-5"), ("1000000", "1000000"), ("4611686018427387904", "4611686018427387904"), ("18014398509481985", "18014398509481985"),
]
DECS2 = [
    ("1.50", "1.50"), ("1.5", "1.50"), ("01.5", "1.50"), ("+1.5", "1.50"), ("0.10", "0.10"), (".5", "0.50"), ("5.", "5.00"), ("-0.05", "-0.05"), ("-0.00", "0.00"),
    ("0", "0.00"), ("10", "10.00"), ("-.5", "-0.50"), ("0.01", "0.01"), ("123456789012345.67", "123456789012345.67"), ("-123456789012345.67", "-123456789012345.67"),
    ("999999999999999.99", "999999999999999.99"), ("007.5", "7.50"), ("+2.25", "2.25"), ("100.00", "100.00"),
]
DECS0 = [("7", "7"), ("-3", "-3"), ("007", "7"), ("+8", "8"), ("-0", "0"), ("123456789012345678", "123456789012345678")]
DECS6 = [("0.000001", "0.000001"), ("-0.000001", "-0.000001"), ("1", "1.000000"), ("0.5", "0.500000"), ("123.456", "123.456000"), ("-0.1", "-0.100000")]
FLOATS = [
    ("0.1", "0.1"), ("1e3", "1000.0"), ("1E3", "1000.0"), ("-0", "0.0"), ("0", "0.0"), ("-0.0", "0.0"), ("0.0", "0.0"), ("0.30000000000000004", "0.30000000000000004"),
    ("0.1234567890123456789", "0.12345678901234568"), ("17.123456789012345678", "17.123456789012344"), ("123456789012345678", "123456789012345680.0"),
    ("9007199254740993", "9007199254740992.0"), ("1e22", "1e22"), ("1e21", "1e21"), ("1e20", "100000000000000000000.0"), ("5e-324", "5e-324"), ("1e-7", "1e-7"),
    ("1e-6", "0.000001"), ("1.5e-7", "1.5e-7"), ("-1.5", "-1.5"), ("-2.5E-3", "-0.0025"), ("2.5", "2.5"), (".5", "0.5"), ("5.", "5.0"), ("+7", "7.0"),
    ("1.7976931348623157e308", "1.7976931348623157e308"), ("-1.7976931348623157e308", "-1.7976931348623157e308"), ("2.2250738585072014e-308", "2.2250738585072014e-308"),
    ("-1e-300", "-1e-300"), ("6.02e23", "6.02e23"), ("4.35", "4.35"), ("100", "100.0"), ("1e0", "1.0"),
]


def numeric_order(pairs, ty):
    """The (cell, token) pairs as the tool lists the groups of a typed key: one per value, by value."""
    seen = {}
    for cell, token in pairs:
        if ty == "float":
            v = float(cell)
            v = 0.0 if v == 0 else v
        else:
            v = Decimal(cell)
        seen.setdefault(v, token)
    return [seen[v] for v in sorted(seen)]


class Golden(Gated):
    def test_every_value_of_every_type_is_written_as_its_value_in_a_bare_number(self):
        for ty, pairs in (("int", INTS), ("dec(2)", DECS2), ("dec(0)", DECS0), ("dec(6)", DECS6), ("float", FLOATS)):
            got = self.frun(csv_of([c for c, _ in pairs]), ["--group", "k:" + ty, "--agg", "count"], "jsonl")
            self.assertEqual((got.status, got.stderr), (0, b""), (ty, got.stderr[:300]))
            toks = key_lines(got.stdout)
            kind = "float" if ty == "float" else "dec"
            self.assertEqual(toks, numeric_order(pairs, kind), ty)
            # every line is exactly one number, then the count (a number as well)
            for line, tok in zip(got.stdout.split(b"\n"), toks):
                self.assertTrue(line.startswith(b'{"k":' + tok.encode() + b',"count":'), (ty, line))
            # csv writes the same token (csv's cell is the number's text)
            c = self.frun(csv_of([c for c, _ in pairs]), ["--group", "k:" + ty, "--agg", "count"], "csv")
            self.assertEqual([r[0] for r in R.csv_records(c.stdout)[1:]], toks, ty)

    def test_the_text_of_a_float_is_the_shortest_that_reads_back_and_is_a_json_number(self):
        got = self.frun(csv_of([c for c, _ in FLOATS]), ["--group", "k:float"], "jsonl")
        for tok in key_lines(got.stdout):
            self.assertEqual(float(tok), float(json.loads(tok)))
        want = sorted({(0.0 if float(c) == 0 else float(c)) for c, _ in FLOATS})
        self.assertEqual([float(t) for t in key_lines(got.stdout)], want)
        for t, x in zip(key_lines(got.stdout), want):
            self.assertTrue(N.same_text(t, x), (t, x))

    def test_the_literal_lines(self):
        data = b"k,n,d,f\na,5,1.50,2.5\na,-3,0.1,0.5\nb,007,10,0\nc,+7,1.5,-0\n"
        for flags, want in (
                (["--group", "d:dec(2)"], b'{"d":0.10,"count":1}\n{"d":1.50,"count":2}\n{"d":10.00,"count":1}\n'),
                (["--group", "n:int", "--agg", "count,sum:n"], b'{"n":-3,"count":1,"sum:n":-3}\n{"n":5,"count":1,"sum:n":5}\n{"n":7,"count":2,"sum:n":14}\n'),
                (["--group", "f:float", "--agg", "count"], b'{"f":0.0,"count":2}\n{"f":0.5,"count":1}\n{"f":2.5,"count":1}\n'),
                (["--group", "k,d:dec(2)", "--agg", "count"], b'{"k":"a","d":0.10,"count":1}\n{"k":"a","d":1.50,"count":1}\n{"k":"b","d":10.00,"count":1}\n{"k":"c","d":1.50,"count":1}\n'),
                (["--group", "n:int,k", "--agg", "count"], b'{"n":-3,"k":"a","count":1}\n{"n":5,"k":"a","count":1}\n{"n":7,"k":"b","count":1}\n{"n":7,"k":"c","count":1}\n'),
                (["--group", "n:int,d:dec(2),f:float", "--agg", "count"], None),
                (["--group", "d:dec(2)", "--agg", "count", "--sort", "-count", "--top", "1"], b'{"d":1.50,"count":2}\n'),
                (["--group", "n:int", "--agg", "count", "--sort", "n", "--limit", "2", "--from", "1"], b'{"n":5,"count":1}\n{"n":7,"count":2}\n')):
            got = self.frun(data, flags, "jsonl")
            if want is None:
                self.assertEqual(got.status, 0, got.stderr)
                key_lines(got.stdout, "n")
                continue
            self.assertEqual((got.status, got.stderr, got.stdout), (0, b"", want), flags)

    def test_leading_zeros_plus_signs_and_negative_zero_in_the_source_are_the_value(self):
        for ty, cells, want in (("int", ["007", "+7", "7", "-0", "0", "-007"], [b"-7", b"0", b"7"]), ("dec(2)", ["007.5", "-0.00", "0", "+7.50"], [b"0.00", b"7.50"]),
                                ("float", ["-0", "0", "-0.0", "00.5e0", "0.5"], [b"0.0", b"0.5"])):
            got = self.frun(csv_of(cells), ["--group", "k:" + ty], "jsonl")
            self.assertEqual(got.status, 0, got.stderr)
            self.assertEqual([l.split(b",")[0][5:] for l in got.stdout.split(b"\n")[:-1]], want, ty)

    def test_no_line_holds_a_quote_around_a_typed_key_or_a_non_json_number(self):
        for ty, pairs in (("int", INTS), ("dec(2)", DECS2), ("float", FLOATS)):
            got = self.frun(csv_of([c for c, _ in pairs]), ["--group", "k:" + ty], "jsonl")
            self.assertNotIn(b'"k":"', got.stdout)
            for line in got.stdout.split(b"\n")[:-1]:
                tok = line[5:line.index(b",")]
                self.assertRegex(tok.decode(), r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
            self.assertNotIn(b"NaN", got.stdout)
            self.assertNotIn(b"Infinity", got.stdout)


class Mixed(Gated):
    DATA = b"k,n,d,f,t\na,5,1.50,2.5,x\na,-3,0.1,0.5,y\nb,007,10,0,x\nc,7,1.5,2.50,z\n"

    def test_an_untyped_key_beside_typed_ones_is_a_string_in_any_place(self):
        for group in ("t,n:int", "n:int,t", "t,d:dec(2),k", "d:dec(2),t,f:float", "k,t,n:int,d:dec(2),f:float"):
            flags = ["--group", group, "--agg", "count,min:n"]
            got = self.frun(self.DATA, flags, "jsonl")
            self.assertEqual((got.status, got.stderr), (0, b""), flags)
            typed = R.typed_places(group)
            for pairs in R.read_lines(got.stdout):
                for i, item in enumerate(group.split(",")):
                    self.assertIs(type(pairs[i][1]) is R.Num, i in typed, (group, pairs))
            c = self.frun(self.DATA, flags, "csv")
            self.assertEqual(got.stdout, R.from_csv_out(c.stdout, agg_tail(flags))[1], flags)

    def test_the_sorts_pages_and_limits_of_typed_groups(self):
        t = R.fixed_table(random.Random(5), 300)
        for flags in (["--group", "n:int", "--agg", "count", "--sort", "-count", "--top", "7"], ["--group", "d:dec(2)", "--agg", "count", "--sort", "d", "--limit", "9", "--from", "3"],
                      ["--group", "f:float,k", "--agg", "sum:n,min:f:float", "--sort", "-f", "--top", "11"], ["--group", "big:int", "--agg", "count", "--limit", "5"],
                      ["--group", "d:dec(3)", "--agg", "max:big", "--sort", "-max:big"], ["--where", "n:int > 0", "--group", "n:int,f:float", "--agg", "count"]):
            got = self.frun(t.data, flags, "jsonl")
            c = self.frun(t.data, flags, "csv")
            self.assertEqual(got.status, c.status, (flags, got.stderr[:200]))
            if got.status == 0:
                self.assertEqual(got.stdout, R.from_csv_out(c.stdout, agg_tail(flags))[1], flags)
                self.assertTrue(got.stdout, flags)
                R.read_lines(got.stdout)

    def test_an_empty_cell_cannot_be_a_typed_key_and_nothing_is_written(self):
        for ty, rule in (("int", "value.not-integer"), ("dec(2)", "value.not-decimal"), ("float", "value.not-float")):
            for rows in (["1", ""], ["", "1"], ["1", "2", "", "3"], [""]):
                data = csv_of(rows)
                c = self.frun(data, ["--group", "k:" + ty], "csv")
                for extra in ((), PAR):
                    got = self.frun(data, ["--group", "k:" + ty], "jsonl", extra=extra)
                    self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (8, b"", rule), (ty, rows, got.stderr[:200]))
                    self.assertEqual((got.status, rule_of(got.stderr)), (c.status, rule_of(c.stderr)))
                got = self.srun(data, ["--group", "k:" + ty], "jsonl")
                self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (8, b"", rule))
            # an empty cell in a column that is not the key is not this refusal
            got = self.frun(b"k,v\n1,\n2,\n", ["--group", "k:" + ty], "jsonl")
            self.assertEqual(got.status, 0, got.stderr)

    def test_a_cell_that_is_not_the_type_or_is_not_finite_is_the_refusal_csv_gives(self):
        for ty, cell, rule in (("int", "1.5", "value.not-integer"), ("int", "9223372036854775808", "value.integer-overflow"), ("dec(2)", "1e3", "value.not-decimal"),
                               ("dec(2)", "12345678901234567.89", "value.decimal-too-wide"), ("float", "inf", "value.not-finite"), ("float", "-NaN", "value.not-finite"),
                               ("float", "1e999", "value.float-range"), ("float", "1e-999", "value.float-range"), ("float", "abc", "value.not-float"), ("float", "0x10", "value.not-float")):
            data = csv_of(["1", cell, "2"])
            c = self.frun(data, ["--group", "k:" + ty], "csv")
            got = self.frun(data, ["--group", "k:" + ty], "jsonl")
            self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (8, b"", rule), (ty, cell, got.stderr[:200]))
            self.assertEqual((c.status, rule_of(c.stderr)), (got.status, rule_of(got.stderr)))
            self.assertEqual(exit_of(rule), 8)
            self.assertEqual(got.stderr.count(b"\n"), 1)

    def test_an_absent_field_of_a_jsonl_input_is_an_empty_cell_and_so_refused_as_a_typed_key(self):
        data = b'{"k":1}\n{"j":2}\n{"k":3}\n'
        got = run("--input-format", "jsonl", "--fields", "k", "--absent", "empty", "--group", "k:int", "--format", "jsonl", "-", stdin=data)
        self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (8, b"", "value.not-integer"), got.stderr)
        got = run("--input-format", "jsonl", "--fields", "k", "--absent", "refuse", "--group", "k:int", "--format", "jsonl", "-", stdin=data)
        self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (8, b"", "parse.jsonl-missing-field"), got.stderr)

    def test_order_by_types_the_sort_and_not_the_cells_of_a_selection(self):
        data = b"n,d,f\n007,1.50,1e3\n-3,0.1,-0\n5,10,2.5\n"
        for flags, want in ((["--order-by", "n:int", "--select", "n,d,f"], b'{"n":"-3","d":"0.1","f":"-0"}\n{"n":"5","d":"10","f":"2.5"}\n{"n":"007","d":"1.50","f":"1e3"}\n'),
                            (["--order-by", "-d:dec(2)", "--select", "d,n"], b'{"d":"10","n":"5"}\n{"d":"1.50","n":"007"}\n{"d":"0.1","n":"-3"}\n'),
                            (["--order-by", "f:float", "--select", "f"], b'{"f":"-0"}\n{"f":"2.5"}\n{"f":"1e3"}\n')):
            got = self.frun(data, flags, "jsonl")
            self.assertEqual((got.status, got.stderr, got.stdout), (0, b"", want), flags)

    def test_json_csv_and_the_report_are_as_they_were(self):
        data = b"k,n\n1.50,007\n1.5,7\n2,8\n"
        c = self.frun(data, ["--group", "k:dec(2)", "--agg", "count"], "csv")
        self.assertEqual(c.stdout, b"k,count\n1.50,2\n2.00,1\n")
        j = self.frun(data, ["--group", "k:dec(2)", "--agg", "count"], "json")
        self.assertEqual(j.data()["rows"], [["1.50", "2"], ["2.00", "1"]])
        r = self.frun(data, ["--report", "types"], "jsonl")
        self.assertEqual((r.status, r.stdout, rule_of(r.stderr)), (2, b"", "args.conflict"))
        r = self.frun(data, ["--report", "types"], "json")
        self.assertEqual(r.status, 0)

    def test_the_explain_of_a_typed_grouping_is_csv_s_but_for_the_format(self):
        self.s.write("t.csv", self.DATA)
        flags = ["--explain", "--group", "d:dec(2),k", "--agg", "count"]
        e = run(*flags, "--format", "jsonl", "t.csv", cwd=str(self.s.dir))
        c = run(*flags, "--format", "csv", "t.csv", cwd=str(self.s.dir))
        self.assertEqual((e.status, c.status), (0, 0), e.stderr)
        a, b = e.data()["explain"], c.data()["explain"]
        self.assertEqual(a["output"]["format"], "jsonl")
        a["output"]["format"] = b["output"]["format"]
        self.assertEqual(a, b)


class Surface(Gated):
    def test_the_help_of_the_format_says_a_typed_group_key_is_a_number(self):
        help_ = {f["name"]: f for f in introspect()["flags"]}["--format"]["help"]
        self.assertIn("a group key with a type (--group NAME:int, NAME:dec(S) or NAME:float)", help_)
        self.assertIn("JSON numbers", help_)
        text = subprocess.run([binary(), "skill"], capture_output=True, check=True).stdout.decode()
        self.assertIn("--format jsonl", text)

    def test_the_note_of_the_decision_is_in_the_docs(self):
        text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "readers.md"), encoding="utf-8").read()
        self.assertIn("**N32 (revises N18", text)
        self.assertIn("**N18 (superseded by N32", text)
        self.assertEqual(text.count("## 13. R3: JSON lines out"), 1)
        self.assertEqual(text.count("### 13.4 The gates"), 1)


class Differential(Gated):
    """Generated tables and plans that have at least one typed group key."""

    def typed_cases(self, seed, want, mode="small", rows=None):
        rng = random.Random(seed)
        n = 0
        tries = 0
        while n < want and tries < want * 60:
            tries += 1
            t, p, flags = R.random_case(rng, rows=rows, mode=mode)
            if p.form == "group" and any(ty is not None for _, ty in p.group):
                n += 1
                yield n, (t, p, flags)
        self.assertGreaterEqual(n, want, "the generator did not make %d plans with a typed key" % want)

    def test_generated_plans_with_typed_keys_against_the_oracle_and_csv(self):
        fails = Failures()
        for k, (t, p, flags) in self.typed_cases(1801, 140 * ROUNDS):
            lab = "case %d %r header %r" % (k, flags, t.header)
            with fails.case(lab):
                exp = R.evaluate(t.header, t.records, p)
                got = self.frun(t.data, flags, "jsonl")
                self.assertNotIn(got.status, TRAPS, got)
                self.against_oracle(got, exp, R.out_names(t.header, p), lab)
                self.same_as_csv(t.data, flags, R.numeric_tail(p), got, lab)
                if isinstance(exp, R.Out) and exp.rows:
                    self.assertTrue(any(exp.numeric[:len(p.group)]), lab)
                if k % 3 == 0:
                    other = self.frun(t.data, flags, "jsonl", extra=PAR)
                    self.assertEqual((other.status, other.stdout, other.stderr), (got.status, got.stdout, got.stderr), lab)
                    other = self.srun(t.data, flags, "jsonl")
                    self.assertEqual((other.status, other.stdout, other.stderr), (got.status, got.stdout, got.stderr), lab)
        fails.check(self, "generated plans with typed keys")

    def test_a_table_of_hundreds_of_rows_with_the_hard_values(self):
        rng = random.Random(77)
        t = R.fixed_table(rng, 600)
        fails = Failures()
        for flags in (["--group", "big:int", "--agg", "count"], ["--group", "n:int,k", "--agg", "count,sum:big"], ["--group", "d:dec(2)", "--agg", "count,min:f:float"],
                      ["--group", "d:dec(6),n:dec(3)", "--agg", "count"], ["--group", "f:float", "--agg", "count,max:big"], ["--group", "id:int", "--agg", "count", "--top", "50", "--sort", "-id"],
                      ["--group", "f:float,big:int,d:dec(3)", "--agg", "count"]):
            with fails.case(repr(flags)):
                got = self.frun(t.data, flags, "jsonl")
                c = self.frun(t.data, flags, "csv")
                self.assertEqual(got.status, c.status, got.stderr[:200])
                if got.status == 0:
                    self.assertEqual(got.stdout, R.from_csv_out(c.stdout, agg_tail(flags))[1])
                    self.assertTrue(got.stdout)
                    typed = R.typed_places(flags[1])
                    for pairs in R.read_lines(got.stdout):
                        for i in range(len(flags[1].split(","))):
                            self.assertIs(type(pairs[i][1]) is R.Num, i in typed, pairs)
        fails.check(self, "hard values")


class RoundTrip(Gated):
    def pipe(self, data, flags, fields, extra=()):
        """csv -> jsonl (the tool) -> csv (the tool's JSON-lines reader, from standard input)."""
        a = self.frun(data, flags, "jsonl", extra=extra)
        b = run("--input-format", "jsonl", "--fields", fields, "--rows", "--format", "csv", *extra, "-", stdin=a.stdout, cwd=str(self.s.dir))
        return a, b

    def test_csv_to_jsonl_to_csv_is_the_csv_of_the_plan_for_typed_groups(self):
        t = R.fixed_table(random.Random(31), 400, kinds=["id", "int", "big", "dec", "flt"])
        fails = Failures()
        for flags, fields in ((["--group", "n:int", "--agg", "count"], "n,count"), (["--group", "d:dec(2)", "--agg", "count"], "d,count"),
                              (["--group", "f:float", "--agg", "count"], "f,count"), (["--group", "big:int,f:float", "--agg", "count"], "big,f,count"),
                              (["--group", "id,d:dec(3),n:int", "--agg", "count"], "id,d,n,count"), (["--group", "d:dec(6)", "--agg", "count,min:f:float"], "d,count,min:f")):
            for extra in ((), PAR):
                with fails.case("%r %r" % (flags, extra)):
                    want = self.frun(t.data, flags, "csv")
                    a, b = self.pipe(t.data, flags, fields, extra)
                    self.assertEqual((a.status, a.stderr), (0, b""), a.stderr[:200])
                    self.assertEqual((b.status, b.stderr), (0, b""), b.stderr[:300])
                    self.assertEqual(b.stdout, want.stdout)
                    typed = R.typed_places(flags[1])
                    for pairs in R.read_lines(a.stdout):
                        for i in range(len(flags[1].split(","))):
                            self.assertIs(type(pairs[i][1]) is R.Num, i in typed, pairs)
        fails.check(self, "round trips")

    def test_generated_typed_groups_csv_to_jsonl_to_csv(self):
        fails = Failures()
        for k, (t, p, flags) in Differential.typed_cases(self, 4242, 60 * ROUNDS):
            names = R.out_names(t.header, p)
            if any(any(c in n for c in ',"\\:\n\x00') or n == "" for n in names) or len(set(names)) != len(names):
                continue
            with fails.case("case %d %r" % (k, flags)):
                want = self.frun(t.data, flags, "csv")
                if want.status != 0:
                    continue
                a = self.frun(t.data, flags, "jsonl")
                self.assertEqual(a.status, 0)
                b = run("--input-format", "jsonl", "--fields", ",".join(n.encode("latin-1").decode("utf-8", "replace") for n in names), "--rows", "--format", "csv", "-",
                        stdin=a.stdout, cwd=str(self.s.dir))
                if b.status != 0 and rule_of(b.stderr) in ("parse.jsonl-not-scalar", "input.fields-bad", "args.bad-value"):
                    continue         # a name or cell the reader cannot take back (invalid UTF-8, a path with a dot): the plain csv->jsonl->csv gate is test_jsonl_out.py
                self.assertEqual((b.status, b.stderr), (0, b""), b.stderr[:300])
                self.assertEqual(R.csv_records(b.stdout), R.csv_records(want.stdout))
        fails.check(self, "generated round trips")

    def test_jsonl_to_jsonl_a_number_key_is_read_back_as_its_text_and_grouped_again(self):
        data = b'{"d":1.50,"n":7,"f":1e3,"s":"x"}\n{"d":1.5,"n":007x,"f":0.1,"s":"y"}\n'.replace(b"007x", b"7")
        data += b'{"d":10,"n":-3,"f":-0,"s":"x"}\n{"d":0.10,"n":9223372036854775807,"f":5e-324,"s":"z"}\n'
        for flags, key in ((["--group", "d:dec(2)"], "d"), (["--group", "n:int"], "n"), (["--group", "f:float"], "f"), (["--group", "s,n:int"], "n")):
            a = run("--input-format", "jsonl", "--fields", "d,n,f,s", *flags, "--format", "jsonl", "-", stdin=data)
            self.assertEqual((a.status, a.stderr), (0, b""), a.stderr)
            lines = R.read_lines(a.stdout)
            typed = [str(dict(p)[key]) for p in lines]
            self.assertTrue(all(type(dict(p)[key]) is R.Num for p in lines))
            # the first answer is the input of a second grouping of the same key: its keys come back as they went, and the counts are summed
            b = run("--input-format", "jsonl", "--fields", ",".join(k for k, _ in lines[0]), "--group", flags[1].replace("s,", ""), "--agg", "sum:count:int", "--format", "jsonl", "-", stdin=a.stdout)
            self.assertEqual((b.status, b.stderr), (0, b""), b.stderr)
            again = [str(dict(p)[key]) for p in R.read_lines(b.stdout)]
            self.assertEqual(sorted(set(again)), sorted(set(typed)))
            self.assertEqual(sum(int(str(dict(p)["sum:count"])) for p in R.read_lines(b.stdout)), 4)
        # the exact text a JSON number has is the key: a decimal with a 17-digit value, and an integer past 2^53, are not through a double
        data = b'{"d":123456789012345.67,"n":9007199254740993}\n'
        a = run("--input-format", "jsonl", "--fields", "d,n", "--group", "d:dec(2),n:int", "--format", "jsonl", "-", stdin=data)
        self.assertEqual(a.stdout, b'{"d":123456789012345.67,"n":9007199254740993,"count":1}\n')


class Threads(Gated):
    PLANS = [
        ["--group", "n:int", "--agg", "count,sum:big"], ["--group", "d:dec(2),k", "--agg", "count,distinct:n:int", "--sort", "-count", "--top", "9"],
        ["--group", "f:float", "--agg", "count,mean:f:float"], ["--group", "big:int,f:float", "--agg", "count"], ["--group", "id:int", "--agg", "count", "--top", "20"],
        ["--where", "id:int >= 5", "--group", "d:dec(6),n:dec(3)", "--agg", "count", "--sort", "d", "--limit", "5", "--from", "2"],
    ]

    def test_one_to_sixty_four_threads_and_chunks_give_the_bytes_of_one_thread(self):
        rng = random.Random(404)
        t = R.inflate(rng, R.fixed_table(rng, 700), long_cells=3, huge=False)
        fails = Failures()
        for flags in self.PLANS:
            base = self.frun(t.data, flags, "jsonl")
            self.assertNotIn(base.status, TRAPS)
            c = self.frun(t.data, flags, "csv")
            with fails.case("%r baseline" % (flags,)):
                self.assertEqual((base.status, rule_of(base.stderr) if base.status else None), (c.status, rule_of(c.stderr) if c.status else None))
                kind, want = R.from_csv_out(c.stdout, agg_tail(flags))
                self.assertEqual((kind, base.stdout), ("ok", want))
                self.assertTrue(base.stdout)
            for threads in (1, 2, 3, 7, 16, 64):
                for chunk in (1000, 4096, 65536):
                    with fails.case("%r threads %d chunk %d" % (flags, threads, chunk)):
                        got = self.frun(t.data, flags, "jsonl", extra=["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 1])
                        self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr))
            with fails.case("%r stdin" % (flags,)):
                for s in (self.srun(t.data, flags, "jsonl"), self.srun(t.data, flags, "jsonl", False)):
                    self.assertEqual((s.status, s.stdout, s.stderr), (base.status, base.stdout, base.stderr))
        fails.check(self, "thread and chunk configurations")

    def test_threads_one_to_sixty_four_with_tiny_chunks_on_a_small_table(self):
        t = R.fixed_table(random.Random(8), 40)
        fails = Failures()
        for flags in self.PLANS[:4]:
            base = self.frun(t.data, flags, "jsonl")
            for threads in (1, 2, 3, 7, 16, 64):
                for chunk in (1, 7, 64):
                    with fails.case("%r threads %d chunk %d" % (flags, threads, chunk)):
                        got = self.frun(t.data, flags, "jsonl", extra=["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 1])
                        self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr))
        fails.check(self, "tiny chunks")


if __name__ == "__main__":
    unittest.main()
