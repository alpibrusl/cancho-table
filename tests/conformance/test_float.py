"""`:float` (docs/numbers.md, stage N3a): `--where` with `:float`, and `min`, `max`, `count`, `distinct` of a float column.

The oracle is `numbers_ref.py`: Python's `float()`, which is correctly rounded, behind the design's grammar (ASCII only, no
`inf` and no `nan`, no negative zero, a non-zero cell that underflows to zero is refused, a cell past 1,100 bytes is refused) and
Python's `Fraction` for the cells that are exactly halfway between two doubles. A comparison is of the nearest doubles, never of the decimal text.
"""

import csv
import io
import random
import re
import struct
import unittest
from decimal import Decimal, getcontext
from fractions import Fraction

import numbers_ref as ref
import refimpl
import test_parallel as par
import test_plan as tp
from harness import Scratch, TRAPS, validate
from test_numbers import OPS, enc

getcontext().prec = 1300


def exact_text(fr):
    """A Fraction whose denominator is a power of two, as the exact decimal text."""
    return format(Decimal(fr.numerator) / Decimal(fr.denominator), "f")


def next_up(x):
    b = struct.unpack(">q", struct.pack(">d", x))[0]
    return struct.unpack(">d", struct.pack(">q", b + 1))[0]


def hard_cells(rng, n):
    """Cells that are hard to round: the exact midpoint between two doubles, a hair above it and a hair below it, at every exponent."""
    out = []
    for _ in range(n):
        e = rng.choice([rng.randint(-1074, 1022), rng.randint(-1074, -1000), rng.randint(900, 1022), rng.randint(-60, 60)])
        x = float(Fraction(rng.getrandbits(53) | (1 << 52), 1) * Fraction(2) ** (e - 52)) if e > -1022 else float(Fraction(rng.getrandbits(52) + 1) * Fraction(2) ** -1074)
        if not (0 < x < 1.7e308):
            continue
        mid = (Fraction(x) + Fraction(next_up(x))) / 2
        t = exact_text(mid)
        out += [t, t + "1", t[:-1] + "4999", t[:-1] + "5000000001"]
    return out


def lex_float_cells(rng, n):
    cells = []
    for _ in range(n):
        k = rng.random()
        if k < 0.25:
            cells.append(repr(struct.unpack(">d", struct.pack(">Q", rng.getrandbits(64)))[0]))        # 17 digits, any exponent
        elif k < 0.45:
            cells.append("%.2f" % rng.uniform(-1e5, 1e5))
        elif k < 0.6:
            cells.append("%de%d" % (rng.randint(-10 ** 6, 10 ** 6), rng.randint(-30, 30)))
        elif k < 0.7:
            cells.append(str(rng.randint(-2 ** 70, 2 ** 70)))
        elif k < 0.8:
            cells.append("%s.%se%s%d" % (rng.choice(["", "-", "+"]), "".join(rng.choice("0123456789") for _ in range(rng.randint(0, 22))), rng.choice(["", "+", "-"]), rng.randint(0, 400)))
        elif k < 0.9:
            cells.append(repr(rng.uniform(-1, 1) * 10.0 ** rng.randint(-320, 300)))
        else:
            cells.append(rng.choice(["0", "-0", "0.0", "1", "1e0", "0.1", "0.2", "0.3", "5e-324", "2.2250738585072014e-308", "1.7976931348623157e308", "123456789012345678", "9007199254740993"]))
    return [c for c in cells if ref.flt(c)[0] == "ok"]


class Floats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def mm(self, cells, extra=()):
        """min and max, distinct, count of one group per cell (a unique key each): one run reads every cell."""
        data = enc([["k", "x"]] + [[str(i), c] for i, c in enumerate(cells)])
        got = self.s.table("t.csv", data, "--group", "k", "--agg", "min:x:float,max:x:float,distinct:x:float,count", "--format", "csv", "--max-groups", "1000000", *extra)
        self.assertEqual(got.status, 0, got)
        return sorted(list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))[1:], key=lambda r: int(r[0]))      # the groups come out in key order, as text

    # ---- G1: the cells ------------------------------------------------------------------------------------------------

    def test_the_reference_agrees_with_its_spec(self):
        for cell, _, f in ref.SPEC:
            self.assertEqual(ref.flt(cell)[:2], f, cell[:40])

    def test_every_edge_cell(self):
        for cell, _, _ in ref.SPEC:
            with self.subTest(cell=cell[:30]):
                want = ref.flt(cell)
                got = self.s.table("t.csv", enc([["id", "x"], ["1", cell]]), "--where", "x:float >= 0", "--select", "id")
                self.assertEqual(validate(got), [], got)
                if want[0] == "ok":
                    self.assertEqual(got.status, 0, got)
                    self.assertEqual(got.data()["rows"], [["1"]] if want[1] >= 0 else [], got)
                    continue
                self.assertEqual((got.status, got.first_rule()), (8, want[1]), got)
                d = got.error()["detail"]
                self.assertEqual((d["row"], d["line"], d["column"], d["context"]), (1, 2, "x", "where"), got)
                if want[2]:
                    self.assertEqual(d["direction"], want[2], got)

    def test_a_cell_of_1100_bytes_is_read_and_one_of_1101_is_refused(self):
        ok = "0" * 1097 + "0.5"                              # 1100 bytes, leading zeros are not digits
        self.assertEqual(len(ok), 1100)
        got = self.s.table("t.csv", enc([["x"], [ok]]), "--agg", "min:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"min:x\n0.5\n", got)
        for bad in ("0" * 1098 + "0.5", "1" * 1101):
            got = self.s.table("t.csv", enc([["x"], [bad]]), "--where", "x:float > 0")
            self.assertEqual((got.status, got.first_rule()), (8, "limit.number-too-long"), got)
        self.assertEqual(self.mm(["1" + "0" * 300 + "e-300"])[0][1:], ["1.0", "1.0", "1", "1"])
        got = self.s.table("t.csv", enc([["x"], ["1" * 1100]]), "--where", "x:float > 0")
        self.assertEqual(got.first_rule(), "value.float-range")

    # ---- the comparison is of doubles --------------------------------------------------------------------------------

    def test_every_operator_against_python_floats(self):
        cells = ["0", "-0", "-0.0", "1e-1", "0.1", "0.10", "0.10000000000000001", "0.10000000000000002", "0.3", "0.30000000000000004", "1", "1.0", "1e0", "2.5", "-2.5", "1e3", "1000", "5e-324", "-5e-324",
                 "1.7976931348623157e308", "-1.7976931348623157e308", "9007199254740992", "9007199254740993", "9007199254740994", "123456789.123456789"]
        lits = ["0", "-0.0", "0.1", "1e-1", "0.30000000000000004", "1", "2.5", "-2.5", "1000", "5e-324", "9007199254740993", "1.7976931348623157e308", "1e308", ".5", "5.", "+3", "-1e-300"]
        data = enc([["id", "x"]] + [[str(i), c] for i, c in enumerate(cells)])
        for op, f in OPS.items():
            for lit in lits:
                with self.subTest(op=op, lit=lit):
                    want = [[str(i)] for i, c in enumerate(cells) if f(ref.flt(c)[1], ref.flt(lit)[1])]
                    got = self.s.table("t.csv", data, "--where", "x:float %s %s" % (op, lit), "--select", "id")
                    self.assertEqual(got.data()["rows"], want, got)
        for lits in (["0.1", "2.5"], ["0", "1e3"], ["9007199254740993", "-5e-324"], ["1e308"]):
            want = [[str(i)] for i, c in enumerate(cells) if ref.flt(c)[1] in {ref.flt(l)[1] for l in lits}]
            got = self.s.table("t.csv", data, "--where", "x:float in (%s)" % ", ".join(lits), "--select", "id")
            self.assertEqual(got.data()["rows"], want, (lits, got))

    def test_equality_is_of_the_double_and_text_is_still_text(self):
        data = "id,x\n1,0.1\n2,0.10\n3,1e-1\n4,0.10000000000000001\n5,0.10000000000000002\n6,1E-1\n7,+.1\n"
        got = self.s.table("t.csv", data, "--where", "x:float = 0.1", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"], ["2"], ["3"], ["4"], ["6"], ["7"]])
        got = self.s.table("t.csv", data, "--where", "x = 0.1", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"]])

    def test_there_is_no_negative_zero(self):
        data = "id,x\n1,-0\n2,0\n3,-0.0\n4,0e5\n5,-0e-9\n6,0.0\n"
        got = self.s.table("t.csv", data, "--where", "x:float = 0", "--select", "id")
        self.assertEqual(len(got.data()["rows"]), 6)
        got = self.s.table("t.csv", data, "--agg", "min:x:float,max:x:float,distinct:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"min:x,max:x,distinct:x\n0.0,0.0,1\n")

    def test_the_cells_that_are_not_numbers(self):
        for cell, rule in (("nan", "value.not-finite"), ("NaN", "value.not-finite"), ("-inf", "value.not-finite"), ("+Infinity", "value.not-finite"), ("INF", "value.not-finite"), ("1e999", "value.float-range"),
                           ("-1e999", "value.float-range"), ("1e-999", "value.float-range"), ("", "value.not-float"), (" 1", "value.not-float"), ("1,5", "value.not-float"), ("0x1p3", "value.not-float"), ("1e", "value.not-float"),
                           ("infx", "value.not-float"), ("nano", "value.not-float"), ("1.5f", "value.not-float"), (".", "value.not-float"), ("-", "value.not-float")):
            got = self.s.table("t.csv", enc([["x"], [cell]]), "--where", "x:float > 0")
            self.assertEqual((got.status, got.first_rule()), (8, rule), (cell, got))
        # the repair of NaN is the idiom: a condition that is false stops the ones after it
        got = self.s.table("t.csv", "id,x\n1,5\n2,NaN\n3,7\n", "--where", "x != 'NaN' and x:float > 6", "--select", "id")
        self.assertEqual(got.data()["rows"], [["3"]])

    # ---- reading: rounding, against Python -----------------------------------------------------------------------------

    def test_hard_cells_are_rounded_as_python_rounds_them(self):
        rng = random.Random(41)
        cells = [c for c in hard_cells(rng, 160) if ref.flt(c)[0] == "ok"]
        self.assertGreater(len(cells), 400)
        rows = self.mm(cells)
        for cell, (k, lo, hi, dist, count) in zip(cells, rows):
            x = ref.flt(cell)[1]
            self.assertTrue(ref.same_text(lo, x) and lo == hi and (dist, count) == ("1", "1"), (cell[:60], lo, ref.float_text(x)))

    def test_random_cells_of_every_shape_are_read_and_written_as_python_does(self):
        rng = random.Random(42)
        cells = lex_float_cells(rng, 6000)
        for cell, (k, lo, hi, dist, count) in zip(cells, self.mm(cells)):
            x = ref.flt(cell)[1]
            self.assertTrue(ref.same_text(lo, x) and lo == hi, (cell[:60], lo, ref.float_text(x)))

    def test_the_printer_lays_out_the_decimal_as_the_design_says(self):
        for cell, text in (("0.1", "0.1"), ("100", "100.0"), ("12345.67", "12345.67"), ("1e21", "1e21"), ("1e20", "100000000000000000000.0"), ("1.5e-7", "1.5e-7"), ("1e-6", "0.000001"), ("1e-7", "1e-7"),
                           ("5e-324", "5e-324"), ("1.7976931348623157e308", "1.7976931348623157e308"), ("-2.5", "-2.5"), ("0.30000000000000004", "0.30000000000000004"), ("0", "0.0"), ("-0", "0.0"),
                           ("123456789012345678901", "123456789012345680000.0"), ("1234.5e17", "123450000000000000000.0"), ("9007199254740993", "9007199254740992.0"), ("1e16", "10000000000000000.0")):
            self.assertEqual(self.mm([cell])[0][1], text, cell)
            self.assertEqual(ref.float_text(ref.flt(cell)[1]), text, cell)

    def test_a_million_cells_agree_on_the_distinct_count_the_extremes_and_the_thresholds(self):
        rng = random.Random(43)
        cells = lex_float_cells(rng, 400000) + hard_cells(rng, 120)
        cells = [c for c in cells if ref.flt(c)[0] == "ok"]
        vals = [ref.flt(c)[1] for c in cells]
        data = enc([["k", "x"]] + [["g", c] for c in cells])
        got = self.s.table("t.csv", data, "--group", "k", "--agg", "min:x:float,max:x:float,distinct:x:float,count", "--format", "csv", "--max-distinct", "10000000", "--max-state-bytes", "1000000000")
        self.assertEqual(self.mm_row(got), [ref.float_text(min(vals)), ref.float_text(max(vals)), str(len(set(vals))), str(len(vals))], got)
        for t in ("0", "1e-300", "-1e300", "0.1", "123456.789", "2.2250738585072014e-308", "1e308"):
            tv = ref.flt(t)[1]
            got = self.s.table("t.csv", data, "--where", "x:float <= %s" % t, "--agg", "count", "--format", "csv", "--max-groups", "10")
            self.assertEqual(got.stdout.decode().split("\n")[1], str(sum(1 for v in vals if v <= tv)), t)

    def mm_row(self, got):
        self.assertEqual(got.status, 0, got)
        return list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))[1][1:]

    # ---- aggregates ----------------------------------------------------------------------------------------------------

    def test_min_max_count_and_distinct_of_a_float_column(self):
        data = "g,x\na,1.5\na,-2.25\nb,1e3\nb,1000\nb,15e-1\na,1.50\nc,-0\n"
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "count,min:x:float,max:x:float,distinct:x:float", "--format", "csv")
        self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode())))[1:], [["a", "3", "-2.25", "1.5", "2"], ["b", "3", "1.5", "1000.0", "2"], ["c", "1", "0.0", "0.0", "1"]])
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "max:x:float", "--sort", "-max:x")
        self.assertEqual([r[0] for r in got.data()["rows"]], ["b", "a", "c"])
        got = self.s.table("t.csv", data, "--agg", "min:x:float,max:x:float")
        self.assertEqual(got.data()["rows"], [["-2.25", "1000.0"]])

    def test_a_float_aggregate_refusal_names_the_function_the_row_and_the_direction(self):
        for spec, rows, rule, extra in (("min:x:float", ["1", "abc"], "value.not-float", {"context": "min"}), ("max:x:float", ["1", "inf"], "value.not-finite", {"context": "max"}),
                                        ("distinct:x:float", ["1", "1e400"], "value.float-range", {"context": "distinct", "direction": "overflow"}),
                                        ("min:x:float", ["1e-400"], "value.float-range", {"direction": "underflow"}), ("max:x:float", ["", "1"], "value.not-float", {"row": 1}),
                                        ("min:x:float", ["1" * 1101], "limit.number-too-long", {"context": "min"})):
            with self.subTest(spec=spec, rows=[r[:10] for r in rows]):
                got = self.s.table("t.csv", "g,x\n" + "".join("a,%s\n" % r for r in rows), "--group", "g", "--agg", spec)
                self.assertEqual((got.status, got.first_rule()), (8, rule), got)
                d = got.error()["detail"]
                self.assertEqual((d["column"], d["row"]), ("x", extra.get("row", len(rows))), got)
                for k, v in extra.items():
                    self.assertEqual(d[k], v, (k, got))

    def test_the_refusal_of_a_slow_path_aggregate_is_the_same(self):
        for key in ('"a""b"', "a"):
            for agg, rows, rule in (("min:x:float,distinct:x", ["1.5", "x"], "value.not-float"), ("max:x:float", ["2", "9" * 1101], "limit.number-too-long")):
                got = self.s.table("t.csv", "g,x\n" + "".join("%s,%s\n" % (key, r) for r in rows), "--group", "g", "--agg", agg)
                self.assertEqual((got.status, got.first_rule()), (8, rule), (key, agg, got))
                self.assertEqual(got.error()["detail"]["row"], 2)

    def test_the_mean_of_a_float_has_no_scale(self):
        for spec in ("mean:x:float@2", "sum:x:float@2"):
            got = self.s.table("t.csv", "g,x\na,1.5\n", "--group", "g", "--agg", spec)
            self.assertEqual((got.status, got.first_rule()), (2, "agg.bad-spec"), (spec, got))
        got = self.s.table("t.csv", "g,x\na,1.5\na,2\n", "--group", "g", "--agg", "sum:x:float,mean:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"g,sum:x,mean:x\na,3.5,1.75\n", got)

    def test_the_grammar_of_the_suffix_and_the_literal(self):
        for expr, offset, word in (("x:float > nan", 10, "finite"), ("x:float > inf", 10, "finite"), ("x:float > 1e999", 10, "range"), ("x:float > abc", 10, "number"), ("x:float > ''", 10, "number"), ("x:float > 1e", 10, "number"),
                                   ("x:float contains 1", 8, "contains"), ("x:float", 7, "operator"), ("x:float >", 9, "value"), ("x:float in (1, nan)", 15, "finite"), ("x:float > 1,5", 11, "and")):
            with self.subTest(expr):
                got = self.s.table("t.csv", "x\n1\n", "--where", expr)
                self.assertEqual((got.status, got.first_rule()), (2, "where.syntax"), got)
                d = got.error()["detail"]
                self.assertEqual(d["offset"], offset, (expr, d))
                self.assertIn(word, d["expected"], (expr, d))
        for expr in ("x:float > 12", "x:float > -.5", "x:float > 5.", "x:float > +1e3", "x:float>=1E-3", "x:float in ('1.5', 2)", "x:float != 0"):
            got = self.s.table("t.csv", "x\n1\n", "--where", expr)
            self.assertEqual(got.status, 0, (expr, got))

    def test_a_float_column_beside_another_numeric_type_is_a_conflict(self):
        data = "g,x\na,1.5\n"
        for args in (["--where", "x:float > 1", "--agg", "min:x"], ["--where", "x:float > 1 and x:dec(2) > 0"], ["--where", "x:float > 1", "--group", "g", "--agg", "max:x:dec(2)"], ["--agg", "min:x:float,max:x:int"],
                     ["--where", "x:float > 1", "--order-by", "x:int"], ["--agg", "min:x:float,distinct:x:dec(1)"]):
            got = self.s.table("t.csv", data, *args)
            self.assertEqual((got.status, got.first_rule()), (2, "column.type-conflict"), (args, got))
            self.assertIn(":float", got.error()["detail"]["types"], got)
        for args in (["--where", "x:float > 1", "--agg", "min:x:float,distinct:x"], ["--where", "x:float > 1 and g = a", "--agg", "count"]):
            self.assertEqual(self.s.table("t.csv", data, *args).status, 0, args)

    def test_a_header_called_x_float_is_written_with_a_backslash_in_where_too(self):
        data = "id,x:float,x\n1,7,1.5\n2,abc,2.5\n"
        got = self.s.table("t.csv", data, "--where", "x\\:float = 7", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"]], got)
        got = self.s.table("t.csv", data, "--where", "'x:float' = 7", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"]], got)
        got = self.s.table("t.csv", data, "--where", "x:float > 2", "--select", "id")
        self.assertEqual(got.data()["rows"], [["2"]], got)       # the column x, read as a float

    def test_a_column_called_x_float_is_written_with_a_backslash(self):
        data = "g,x:float,x\na,7,1.5\n"
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "sum:x\\:float,min:x:float", "--format", "csv")
        self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode())))[1], ["a", "7", "1.5"])

    # ---- G3: fuzz ---------------------------------------------------------------------------------------------------------

    def test_fuzzed_cells_never_trap_and_agree_with_the_reference(self):
        rng = random.Random(44)
        alpha = "0123456789012345678901234567890+-. eE_,x\t'\"\\iInNfFaA"
        for _ in range(500):
            cells = []
            for _ in range(rng.randint(1, 10)):
                k = rng.random()
                cells.append("".join(rng.choice(alpha) for _ in range(rng.randint(0, 12))) if k < 0.4 else rng.choice(lex_float_cells(rng, 4) + ["inf", "nan", "1e999", ""]))
            op = rng.choice(list(OPS))
            lit = rng.choice(["0", "1.5", "-2", "0.1", "1e10", "5e-324"])
            got = self.s.table("t.csv", enc([["id", "x"]] + [[str(n), c] for n, c in enumerate(cells, 1)]), "--where", "x:float %s %s" % (op, lit), "--select", "id")
            self.assertNotIn(got.status, TRAPS, (cells, got))
            want_rows, refusal = [], None
            for n, c in enumerate(cells, 1):
                r = ref.flt(c)
                if r[0] == "refuse":
                    refusal = (n, r[1])
                    break
                if OPS[op](r[1], ref.flt(lit)[1]):
                    want_rows.append([str(n)])
            if refusal:
                self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"]), (8, refusal[1], refusal[0]), (op, lit, cells, got))
            else:
                self.assertEqual((got.status, got.data()["rows"]), (0, want_rows), (op, lit, cells, got))

    def test_bytes_that_are_not_text_in_a_float_column(self):
        for cell in (b"\xff\xfe", b"1.5\x00", b"\x80", b"\xc3\x28"):
            got = self.s.table("t.csv", b"id,x\n1," + cell + b"\n", "--where", "x:float > 0")
            self.assertEqual((got.status, got.first_rule()), (8, "value.not-float"), got)
            self.assertEqual(validate(got), [], got)


# ---- G2: generated tables and plans against the reference ------------------------------------------------------------------

def float_cell(rng):
    k = rng.random()
    if k < 0.2:
        return "%.2f" % rng.uniform(-100, 100)
    if k < 0.4:
        return repr(rng.uniform(-1, 1) * 10.0 ** rng.randint(-20, 20))
    if k < 0.55:
        return rng.choice(["0", "-0", "0.0", "1", "1.0", "1e0", "1.50", "1.5", "15e-1", "0.1", "1e-1", ".5", "5.", "+7", "-7", "2.5E3", "1e3", "1000"])
    if k < 0.7:
        return str(rng.randint(-10 ** 6, 10 ** 6))
    if k < 0.85:
        return rng.choice(lex_float_cells(rng, 5))
    return rng.choice(["0.30000000000000004", "0.3", "9007199254740993", "9007199254740992", "1e300", "-1e300", "5e-324"])


def make_float_table(rng):
    names = ["k0", "k1", "f0", "f1", "i0"]
    kinds = ["key", "key", "float", "float", "int"]
    rows = []
    for _ in range(rng.randint(0, 28)):
        row = []
        for kind in kinds:
            row.append(rng.choice(["a", "b", ""]) if kind == "key" else float_cell(rng) if kind == "float" else str(rng.randint(-5, 5)))
            if kind == "float" and rng.random() < 0.006:
                row[-1] = rng.choice(["", "x", "nan", "inf", "-inf", "1e999", "1e-999", "1,5", " 1", "1e"])
        if rng.random() < 0.03:
            row = row[:-1]
        rows.append(row)
    return names, kinds, rows


def make_float_plan(rng, names, kinds, funcs=("count", "min", "max", "distinct", "min", "max")):
    plan = {"where": []}
    for _ in range(rng.choice([0, 0, 1, 2, 3])):
        n = rng.choice(["f0", "f1", "f0", "k0"])
        if n == "k0":
            plan["where"].append({"column": n, "kind": "cmp", "op": rng.choice(["=", "!="]), "lit": rng.choice(["a", "b"])})
            continue
        kind = rng.choice(["cmp", "cmp", "cmp", "in"])
        cond = {"column": n, "float": True, "kind": kind}
        lit = lambda: rng.choice(["0", "1.5", "-2", "0.1", "1e-1", "12.25", ".5", "5.", "+3", "-0", "999", "1e300", "5e-324", "0.30000000000000004", "9007199254740993", "-1e-300"])
        if kind == "in":
            cond["lits"] = [lit() for _ in range(rng.randint(1, 3))]
        else:
            cond["lit"] = lit()
            cond["op"] = rng.choice(["=", "!=", "<", "<=", ">", ">="])
        plan["where"].append(cond)
    if rng.random() < 0.4:
        plan["select"] = [rng.choice(names) for _ in range(rng.randint(1, 3))]
        return plan
    plan["group"] = rng.sample(["k0", "k1"], rng.randint(0, 2))
    aggs = []
    for _ in range(rng.randint(1, 4)):
        f = rng.choice(funcs)
        if f == "count":
            aggs.append(("count", None, None))
        else:
            aggs.append((f, rng.choice(["f0", "f1"]), "float"))
    plan["aggs"] = aggs
    labels = list(plan["group"]) + [("count" if f == "count" else "%s:%s" % (f, c)) for f, c, t in aggs]
    if len(set(labels)) == len(labels) and rng.random() < 0.5:
        plan["sort"] = rng.choice(["", "-"]) + rng.choice(labels)
    return plan


def float_flags(plan):
    args = []
    if plan["where"]:
        parts = []
        for c in plan["where"]:
            col = c["column"] + (":float" if c.get("float") else "")
            parts.append("%s in (%s)" % (col, ", ".join(c["lits"])) if c["kind"] == "in" else "%s %s %s" % (col, c["op"], c["lit"]))
        args += ["--where", " and ".join(parts)]
    if "select" in plan:
        return args + ["--select", ",".join(plan["select"])]
    if plan["group"]:
        args += ["--group", ",".join(plan["group"])]
    items = ["count" if f == "count" else "%s:%s:float" % (f, c) for f, c, t in plan["aggs"]]
    args += ["--agg", ",".join(items)]
    if plan.get("sort"):
        args += ["--sort", plan["sort"]]
    return args


def float_reference(names, rows, plan):
    """("rows", header, rows) | ("groups", labels, rows) | ("refuse", rule, detail)."""
    idx = {n: i for i, n in enumerate(names)}
    groups, out_rows = {}, []
    for n, rec in enumerate(rows, 1):
        if len(rec) != len(names):
            continue
        ok = True
        for c in plan["where"]:
            v = refimpl.holds(dict(c), rec[idx[c["column"]]])
            if v is True:
                continue
            if v is False:
                ok = False
                break
            return ("refuse", v, {"row": n, "column": c["column"], "context": "where"})
        if not ok:
            continue
        if "select" in plan:
            out_rows.append([rec[idx[c]] for c in plan["select"]])
            continue
        st = groups.setdefault(tuple(rec[idx[g]] for g in plan["group"]), {"count": 0, "v": [[] for _ in plan["aggs"]]})
        st["count"] += 1
        for k, (f, c, t) in enumerate(plan["aggs"]):
            if f == "count":
                continue
            r = ref.flt(rec[idx[c]])
            if r[0] == "refuse":
                return ("refuse", r[1], {"row": n, "column": c, "context": f})
            st["v"][k].append(r[1])
    if "select" in plan:
        return ("rows", plan["select"], out_rows)
    labels = list(plan["group"]) + [("count" if f == "count" else "%s:%s" % (f, c)) for f, c, t in plan["aggs"]]
    out = []
    overflow = []
    for key, st in groups.items():
        row, keys = list(key), []
        for k, (f, c, t) in enumerate(plan["aggs"]):
            v = st["v"][k]
            if f == "count":
                row.append(str(st["count"])); keys.append(st["count"])
            elif f == "distinct":
                row.append(str(len(set(v)))); keys.append(len(set(v)))
            elif f in ("sum", "mean"):
                r = (ref.fsum_ref if f == "sum" else ref.fmean_ref)(v)
                if r[0] == "overflow":
                    overflow.append((key, k, c))
                    row.append(None); keys.append(None)
                else:
                    row.append(ref.float_text(r[1])); keys.append(r[1])
            else:
                x = min(v) if f == "min" else max(v)
                row.append(ref.float_text(x)); keys.append(x)
        out.append((key, row, keys))
    if overflow:
        # no row is to blame: the group is named, the smallest key of the groups that overflow, and its first aggregate that does
        key, k, c = min(overflow, key=lambda o: (o[0], o[1]))
        return ("refuse", "agg.float-overflow", {"group": ",".join(key), "column": c, "context": "sum"})
    out.sort(key=lambda r: r[0])
    if plan.get("sort"):
        desc = plan["sort"].startswith("-")
        at = labels.index(plan["sort"].lstrip("-"))
        if at < len(plan["group"]):
            out.sort(key=lambda r: r[0][at], reverse=desc)
        else:
            out.sort(key=lambda r: r[2][at - len(plan["group"])], reverse=desc)
    return ("groups", labels, [r[1] for r in out])


def cell_eq(got, want):
    if got == want:
        return True
    try:
        return ref.same_text(got, float(want))
    except ValueError:
        return False


def rows_eq(got, want):
    return len(got) == len(want) and all(len(g) == len(w) and all(cell_eq(a, b) for a, b in zip(g, w)) for g, w in zip(got, want))


class FloatPlans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def check_plans(self, seed, funcs, cases, table, answers_min, refusals_min):
        rng = random.Random(seed)
        answers = refusals = 0
        for case in range(cases):
            names, kinds, rows = table(rng)
            data = tp.csv_bytes(names, rows, rng)
            plan = make_float_plan(rng, names, kinds, funcs)
            args = float_flags(plan)
            recs = [r for r in csv.reader(io.StringIO(data.decode("latin-1"), newline="")) if r][1:]
            want = float_reference(names, recs, plan)
            fmt = "csv" if case % 3 == 2 else "json"
            with self.subTest(case=case):
                got = self.s.table("t.csv", data, *args, *(["--format", "csv"] if fmt == "csv" else ["--limit", "1000000"]))
                self.assertNotIn(got.status, TRAPS, (args, data, got))
                ragged = any(len(r) != len(names) for r in rows)
                if want[0] == "refuse":
                    refusals += 1
                    self.assertEqual(got.status, 8, (args, data, got))
                    if fmt == "json":
                        self.assertEqual(got.first_rule(), want[1], (args, data, got))
                        d = got.error()["detail"]
                        if want[1] == "agg.float-overflow":
                            self.assertEqual((d["group"], d["column"], d["context"]), (want[2]["group"], want[2]["column"], want[2]["context"]), (args, data, got))
                            continue
                        self.assertEqual((d["row"], d["column"], d["context"]), (want[2]["row"], want[2]["column"], want[2]["context"]), (args, data, got))
                        if want[1] == "value.float-range":
                            self.assertIn(d["direction"], ("overflow", "underflow"))
                    else:
                        self.assertIn(want[1], got.stderr.decode(), (args, data, got))
                    continue
                answers += 1
                kind, labels, wrows = want
                if fmt == "csv":
                    out = [r for r in csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""))]
                    self.assertTrue(out[:1] == [labels] and rows_eq(out[1:], wrows) or not (wrows or kind == "groups"), (out, [labels] + wrows, args, data))
                    self.assertEqual(got.status, 8 if ragged else 0, (args, data, got))
                else:
                    d = got.data() if got.status == 0 else None
                    if d is None:
                        self.assertTrue(ragged, (args, data, got))
                        continue
                    self.assertEqual(d["columns"], labels, (args, data, got))
                    self.assertTrue(rows_eq(d["rows"], wrows), (d["rows"], wrows, args, data))
        self.assertGreater(answers, answers_min)
        self.assertGreater(refusals, refusals_min)

    def test_random_float_plans(self):
        self.check_plans(20261012, ("count", "min", "max", "distinct", "min", "max"), 1600, make_float_table, 900, 80)


class Parallel(par.Same):
    """G4: the sequential answer is every thread count's, with a boundary in nearly every record, for float conditions, min, max, distinct and the refusals."""

    def table(self, rows, bad=None):
        rows = [list(r) for r in rows]
        for at, cell in (bad or {}).items():
            rows[at][1] = cell
        return enc([["id", "x", "t"]] + rows)

    def rows(self, n, seed):
        rng = random.Random(seed)
        return [[str(i), float_cell(rng), rng.choice(["a", "b,c", 'q"r'])] for i in range(n)]

    def test_float_filters_and_aggregates_every_way(self):
        data = self.table(self.rows(160, 10))
        for args in (["--where", "x:float >= 10.5", "--select", "id"], ["--where", "x:float > -3 and x:float < 3", "--format", "csv"], ["--where", "x:float in (0, 1.5, 0.1)"],
                     ["--group", "t", "--agg", "count,min:x:float,max:x:float,distinct:x:float"], ["--agg", "min:x:float,max:x:float", "--where", "x:float != 0"],
                     ["--group", "t", "--agg", "max:x:float", "--sort", "-max:x", "--top", "2"], ["--group", "t,id", "--agg", "min:x:float", "--max-groups", "1000"]):
            self.same(data, args, "float aggregates")

    def test_the_first_refusal_in_file_order_wins(self):
        rows = self.rows(150, 11)
        for bad in ({120: "1.5x", 40: "abc"}, {40: "", 120: "nan"}, {120: "1e999", 121: "x", 149: "1" * 1101}, {149: "-"}, {0: " 1"}, {70: "1e-999", 20: "inf"}):
            data = self.table(rows, bad)
            for args in (["--where", "x:float > 1"], ["--where", "x:float > 1", "--format", "csv"], ["--group", "t", "--agg", "min:x:float"], ["--group", "t", "--agg", "distinct:x:float"],
                         ["--agg", "max:x:float", "--format", "csv"]):
                self.same(data, args, "refusals %r" % (bad,))

    def test_a_float_beside_ragged_rows_and_quoted_newlines(self):
        rows = [[str(i), "%d.5e0" % i, 'multi\nline "%d"' % i] for i in range(60)]
        data = self.table(rows) + b"61\n" + self.table([["62", "2.5", "z"]]).split(b"\n", 1)[1]
        for args in (["--where", "x:float > 20", "--select", "t", "--format", "csv"], ["--where", "x:float > 20"], ["--where", "x:float > 20", "--group", "t", "--agg", "max:x:float"]):
            self.same(data, args, "ragged and quoted")


if __name__ == "__main__":
    unittest.main()
