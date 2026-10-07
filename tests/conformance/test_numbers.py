"""`:dec(S)` in `--where` (docs/numbers.md, stage N1): the gates G1 (the reference semantics, cell by cell), G2 (the
generated plans are in test_plan.py), G3 (fuzz, no trap), G4 (every thread count, tiny ranges), and the
grammar of the suffix and of the literals.

The oracle is `numbers_ref.py`: Python's exact `int` arithmetic over the design's grammar, written independently of the
tool. A cell that is not a decimal at the declared scale is refused, naming the row, the line and the column; a decimal
is never rounded, never a float, and compared as the number it is (`1.5` and `1.50` are one value).
"""

import csv
import io
import random
import re
import unittest

import numbers_ref as ref
import numbers_ref
import test_parallel as par
from harness import Scratch, TRAPS, validate

OPS = {"=": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


def enc(rows, eol="\n"):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator=eol)
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("latin-1")


def frac_digits(cell):
    return len(cell.split(".", 1)[1]) if "." in cell else 0


class Numbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    # ---- G1: every edge cell, at three scales, as the reference says ----------------------------------------

    def test_the_reference_agrees_with_its_spec(self):
        for cell, d2, _ in ref.SPEC:
            self.assertEqual(ref.dec(cell, 2), d2, cell[:40])

    def test_every_edge_cell_at_scales_0_2_and_18(self):
        for scale in (0, 2, 18):
            for cell, _, _ in ref.SPEC:
                with self.subTest(scale=scale, cell=cell[:30]):
                    want = ref.dec(cell, scale)
                    got = self.s.table("t.csv", enc([["id", "x"], ["1", cell]]), "--where", "x:dec(%d) >= 0" % scale, "--select", "id")
                    self.assertEqual(validate(got), [], got)
                    if want[0] == "ok":
                        self.assertEqual(got.status, 0, got)
                        self.assertEqual(got.data()["rows"], [["1"]] if want[1] >= 0 else [], got)
                        continue
                    self.assertEqual((got.status, got.first_rule()), (8, want[1]), got)
                    d = got.error()["detail"]
                    self.assertEqual((d["row"], d["line"], d["column"], d["context"]), (1, 2, "x", "where"), got)
                    if want[1] == "value.decimal-scale":
                        self.assertEqual((d["digits"], d["scale"]), (frac_digits(cell), scale), got)
                    if cell.isascii() and len(cell) < 64:
                        self.assertEqual((d["value"], d["value_truncated"]), (cell, False), got)

    def test_the_repair_of_a_scale_refusal_is_the_whole_invocation_with_a_larger_scale(self):
        got = self.s.table("t.csv", "id,x,y\n1,3.999,2\n", "--where", "y:dec(1) >= 1 and x:dec(2) >= 1 and id = 1", "--select", "id")
        self.assertEqual(got.first_rule(), "value.decimal-scale")
        repair = got.error()["repair"]
        self.assertEqual(repair["kind"], "choose", got)
        argv = repair["options"][0]["argv"]
        self.assertIn("y:dec(1) >= 1 and x:dec(3) >= 1 and id = 1", argv, got)       # the second condition, not the first, and the tail kept
        self.assertEqual(got.error()["detail"]["scale"], 2)
        # and that invocation works
        again = self.s.table("t.csv", "id,x,y\n1,3.999,2\n", "--where", "y:dec(1) >= 1 and x:dec(3) >= 1 and id = 1", "--select", "id")
        self.assertEqual(again.data()["rows"], [["1"]])

    # ---- the operators, the values, equality by value ----------------------------------------------------------

    def test_every_operator_against_python_integers(self):
        cells = ["0", "-0.00", "1", "1.5", "1.50", "2.25", "-2.25", "+3", ".5", "5.", "12.34", "-12.34", "0.01", "-0.01", "999999999999999.99", "-999999999999999.99"]
        lits = ["0", "1.5", "1.50", "2.25", "-2.25", "12.34", ".5", "5.", "+3", "-0", "0.01", "999999999999999.99", "-999999999999999.99", "100"]
        data = enc([["id", "x"]] + [[str(i), c] for i, c in enumerate(cells)])
        for op, f in OPS.items():
            for lit in lits:
                with self.subTest(op=op, lit=lit):
                    want = [[str(i)] for i, c in enumerate(cells) if f(ref.dec(c, 2)[1], ref.dec(lit, 2)[1])]
                    got = self.s.table("t.csv", data, "--where", "x:dec(2) %s %s" % (op, lit), "--select", "id")
                    self.assertEqual(got.data()["rows"], want, got)
        for lits in (["1.5", "3"], ["2.25", "-2.25", "0"], ["100"], [".5", "5.", "12.340"][:2]):
            want = [[str(i)] for i, c in enumerate(cells) if ref.dec(c, 2)[1] in {ref.dec(l, 2)[1] for l in lits}]
            got = self.s.table("t.csv", data, "--where", "x:dec(2) in (%s)" % ", ".join(lits), "--select", "id")
            self.assertEqual(got.data()["rows"], want, (lits, got))

    def test_one_five_and_one_fifty_are_one_value_as_a_number_and_two_as_text(self):
        data = "id,x\n1,1.5\n2,1.50\n3,1.500\n4,01.5\n5,2\n"
        got = self.s.table("t.csv", data, "--where", "x:dec(3) = 1.5", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"], ["2"], ["3"], ["4"]])
        got = self.s.table("t.csv", data, "--where", "x:dec(3) != 1.50 and x:dec(3) in (2, 7)", "--select", "id")
        self.assertEqual(got.data()["rows"], [["5"]])
        got = self.s.table("t.csv", data, "--where", "x = 1.5", "--select", "id")        # no suffix: bytes, as before
        self.assertEqual(got.data()["rows"], [["1"]])
        got = self.s.table("t.csv", data, "--where", "x:dec(3) = 1.5", "--select", "id", "--format", "csv")
        self.assertEqual(got.stdout, b"id\n1\n2\n3\n4\n")

    def test_names_quoted_escaped_positional_and_a_header_that_looks_like_a_suffix(self):
        data = "id,my col,a:dec(2)\n1,1.5,9\n2,2.5,1.5\n"
        for expr in ("'my col':dec(2) > 2", "#2:dec(2) > 2", "my\\ col:dec(2) > 2"):
            got = self.s.table("t.csv", data, "--where", expr, "--select", "id")
            self.assertEqual(got.data()["rows"], [["2"]], expr)
        got = self.s.table("t.csv", data, "--where", "'a:dec(2)' = 9", "--select", "id")      # a header that is called that: text
        self.assertEqual(got.data()["rows"], [["1"]])
        got = self.s.table("t.csv", data, "--where", "'a:dec(2)':dec(1) = 1.5", "--select", "id")      # the same header, read as a decimal
        self.assertEqual(got.data()["rows"], [["2"]], got)

    def test_a_typed_condition_after_a_false_one_is_not_reached(self):
        data = "id,x\n1,\n2,3\n3,abc\n"
        got = self.s.table("t.csv", data, "--where", "id != 3 and x != '' and x:dec(2) > 1", "--select", "id")
        self.assertEqual(got.data()["rows"], [["2"]])
        got = self.s.table("t.csv", data, "--where", "x:dec(2) > 1", "--select", "id")      # an empty cell is not a decimal
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"]), (8, "value.not-decimal", 1))
        got = self.s.table("t.csv", data, "--where", "id < 3 and x:dec(2) > 1")
        self.assertEqual((got.status, got.error()["detail"]["row"]), (8, 1))

    def test_a_quoted_cell_is_read_without_its_quotes(self):
        got = self.s.table("t.csv", 'id,x\n1,"1.50"\n2,"-0.5"\n', "--where", "x:dec(2) = 1.5", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"]])
        got = self.s.table("t.csv", 'id,x\n1,"1.50"\n2,"2,5"\n', "--where", "x:dec(2) >= 0", "--select", "id")
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"], got.error()["detail"]["value"]), (8, "value.not-decimal", 2, "2,5"))

    def test_the_digits_and_the_width_are_the_declared_ones(self):
        mx = "9999999999999999.99"
        for scale, cell, ok in ((2, mx, True), (2, "10000000000000000.00", False), (0, "999999999999999999", True), (0, "1000000000000000000", False),
                                (18, "0.999999999999999999", True), (18, "1.000000000000000000", False), (18, "0.000000000000000001", True), (1, "0.01", False),
                                (3, "-999999999999999.999", True), (3, "-1000000000000000.000", False), (0, "5.", True), (0, "5.0", False)):
            got = self.s.table("t.csv", enc([["x"], [cell]]), "--where", "x:dec(%d) != 0" % scale)
            self.assertEqual(got.status == 0, ok, (scale, cell, got))

    # ---- the grammar of the suffix and of the literals --------------------------------------------------------

    def test_literals_and_suffixes_that_are_syntax_errors(self):
        cases = [
            # expression, the byte offset of the error, a word of what was expected
            ("x:dec(2) > 12.505", 11, "fractional digits"),
            ("x:dec(2) > 1e3", 11, "decimal"),
            ("x:dec(2) > abc", 11, "decimal"),
            ("x:dec(2) > ''", 11, "decimal"),
            ("x:dec(2) > 99999999999999999.99", 11, "10^18"),
            ("x:dec(0) > 5.5", 11, "fractional digits"),
            ("x:dec(2) in (1, 2.555)", 16, "fractional digits"),
            ("x:dec(2) contains 5", 9, "contains"),
            ("x:dec > 5", 5, "scale"),
            ("x:dec() > 5", 6, "scale"),
            ("x:dec(a) > 5", 6, "scale"),
            ("x:dec(19) > 5", 6, "0 to 18"),
            ("x:dec(2 > 5", 7, "scale"),
            ("x:dec(-1) > 5", 6, "scale"),
            ("x:dec(2)", 8, "operator"),
            ("x:dec(2) >", 10, "value"),
            ("x:int > 12.5", 8, "integer"),
            ("x:int > 1e3", 8, "integer"),
        ]
        for expr, offset, word in cases:
            with self.subTest(expr):
                got = self.s.table("t.csv", "x\n1\n", "--where", expr)
                self.assertEqual((got.status, got.first_rule()), (2, "where.syntax"), got)
                d = got.error()["detail"]
                self.assertEqual(d["offset"], offset, (expr, d))
                self.assertIn(word, d["expected"], (expr, d))

    def test_literals_that_are_fine(self):
        for expr in ("x:dec(2) > 12", "x:dec(2) > 12.5", "x:dec(2) > 12.50", "x:dec(2) > .5", "x:dec(2) > 5.", "x:dec(2) > -3", "x:dec(2) > +3", "x:dec(18) > 0.000000000000000001",
                     "x:dec(0)=5", "x:dec(2)>=5", "x:dec(2) != '5'", "x:dec(2) in ('1.5', 2)", "x:dec(2) in (1, 2) and x:int > 0 and y = 1"):
            got = self.s.table("t.csv", "x,y\n1,1\n", "--where", expr)
            self.assertNotEqual(got.first_rule(), "where.syntax", (expr, got))

    def test_two_numeric_types_for_one_column_are_refused_before_a_row_is_read(self):
        data = "id,x,y\n1,1.5,2\n"
        for args in (["--where", "x:dec(2) > 1 and x:int > 0"], ["--where", "x:dec(2) > 1 and x:dec(3) > 0"], ["--where", "x:dec(2) > 1", "--group", "id", "--agg", "sum:x"],
                     ["--where", "x:dec(2) > 1", "--group", "id", "--agg", "min:x"], ["--where", "#2:dec(2) > 1 and x:int > 0"],
                     ["--where", "x:dec(2) > 1", "--order-by", "x:int"]):
            got = self.s.table("t.csv", data, *args)
            self.assertEqual((got.status, got.first_rule()), (2, "column.type-conflict"), (args, got))
            d = got.error()["detail"]
            self.assertEqual(d["column"], "x", d)
            self.assertEqual(len(d["types"]), 2, d)
        # not a conflict: a text mention beside a typed one, two columns, the same type twice, a distinct (which reads bytes)
        for args in (["--where", "x = 1.5 and x:dec(2) > 1"], ["--where", "x:dec(2) > 1 and y:int > 0", "--select", "id"], ["--where", "x:dec(2) > 1 and x:dec(2) < 9"],
                     ["--where", "x:dec(2) > 1", "--group", "id", "--agg", "distinct:x"], ["--where", "y:int > 0", "--group", "id", "--agg", "sum:y"]):
            got = self.s.table("t.csv", data, *args)
            self.assertEqual(got.status, 0, (args, got))

    def test_int_columns_still_refuse_a_decimal_cell_and_say_where_the_repair_is(self):
        got = self.s.table("t.csv", "x\n1.5\n", "--where", "x:int > 0")
        self.assertEqual(got.first_rule(), "value.not-integer")
        self.assertIn("dec", got.doc()["errors"][0]["hint"] + got.doc()["errors"][0]["message"])

    # ---- G3: fuzz: no trap, and the reference's verdict on every generated cell -------------------------------

    def test_fuzzed_cells_never_trap_and_agree_with_the_reference(self):
        rng = random.Random(20261007)
        alpha = "0123456789012345678901234567890+-. eE_,x\t'\"\\"
        for i in range(400):
            scale = rng.choice([0, 1, 2, 2, 5, 18])
            cells = []
            for _ in range(rng.randint(1, 12)):
                k = rng.random()
                if k < 0.35:
                    cells.append("".join(rng.choice(alpha) for _ in range(rng.randint(0, 12))))
                elif k < 0.8:
                    ip = rng.randint(0, 10 ** rng.randint(0, 19))
                    fp = rng.randint(0, 10 ** rng.randint(0, 6))
                    cells.append(rng.choice(["", "-", "+"]) + str(ip) + (rng.choice([".", ""]) + str(fp).zfill(rng.randint(0, 6)) if rng.random() < 0.8 else ""))
                else:
                    cells.append(rng.choice(["1.5", "1.50", "-0", ".5", "5.", "", " 1", "1e2", "0.0", "00.10", "9" * rng.randint(1, 22)]))
            lit = rng.choice(["0", "1.5", "-2", "0.1", "5", ".5", "12.25", "-0.5"])
            if ref.dec(lit, scale)[0] != "ok":
                lit = "0"
            op = rng.choice(list(OPS))
            data = enc([["id", "x"]] + [[str(n), c] for n, c in enumerate(cells, 1)])
            got = self.s.table("t.csv", data, "--where", "x:dec(%d) %s %s" % (scale, op, lit), "--select", "id")
            self.assertNotIn(got.status, TRAPS, (scale, cells, got))
            want_rows, refusal = [], None
            for n, c in enumerate(cells, 1):
                r = ref.dec(c, scale)
                if r[0] == "refuse":
                    refusal = (n, r[1])
                    break
                if OPS[op](r[1], ref.dec(lit, scale)[1]):
                    want_rows.append([str(n)])
            if refusal:
                self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"]), (8, refusal[1], refusal[0]), (scale, op, lit, cells, got))
            else:
                self.assertEqual((got.status, got.data()["rows"]), (0, want_rows), (scale, op, lit, cells, got))

    def test_bytes_that_are_not_text_in_a_decimal_column(self):
        for cell in (b"\xff\xfe", b"1.5\x00", b"\x80", b"\xc3\x28"):
            got = self.s.table("t.csv", b"id,x\n1," + cell + b"\n", "--where", "x:dec(2) > 0")
            self.assertEqual((got.status, got.first_rule()), (8, "value.not-decimal"), got)
            self.assertEqual(validate(got), [], got)



# ---- G2: generated tables and plans against the reference (refimpl.py, which reads `:dec(S)` through numbers_ref.py) ----

import test_plan as tp


def dec_cell(rng, scale):
    k = rng.random()
    if k < 0.90:
        ip = rng.choice([0, 1, 7, 12, 250, 99999, rng.randint(0, 10 ** rng.randint(0, 12))])
        fd = rng.randint(0, scale)
        fp = "".join(rng.choice("0123456789") for _ in range(fd))
        sign = rng.choice(["", "", "-", "+"])
        cell = sign + (str(ip) if rng.random() < 0.9 else "") + ("." + fp if fp or rng.random() < 0.2 else "")
        return cell if re.search(r"[0-9]", cell) else "0"
    return rng.choice(["", "1.%s" % ("5" * (scale + 1)), "1e3", " 7", "abc", "-", ".", "1,5", "10000000000000000000", "-0", "0x1"])


def dec_literal(rng, scale):
    cands = ["0", "1", "-1.5", "12.25", "+3", ".5", "5.", "007.1", "-0", "999", "0.1", "0.05", "-0.001", "1.000", "123456.789", "0.000000000000000001"]
    return rng.choice([c for c in cands if ref.dec(c, scale)[0] == "ok"])


def make_dec_table(rng):
    width = rng.randint(2, 5)
    kinds = [rng.choice([("dec", rng.choice([0, 1, 2, 2, 3, 5, 18])), ("int", 0), ("text", 0)]) for _ in range(width)]
    if not any(k[0] == "dec" for k in kinds):
        kinds[rng.randrange(width)] = ("dec", rng.choice([0, 2, 3]))
    names = ["c%d" % i for i in range(width)] if rng.random() < 0.6 else [rng.choice(["id", "price", "col %d" % i, "x,y%d" % i, "q'%d" % i]) + str(i) for i in range(width)]
    rows = []
    for _ in range(rng.randint(0, 25)):
        row = []
        for kind, scale in kinds:
            row.append(dec_cell(rng, scale) if kind == "dec" else str(rng.randint(-300, 300)) if kind == "int" else rng.choice(["a", "b", "", "x y"]))
        if rng.random() < 0.04:
            row = row[:-1]
        rows.append(row)
    return names, kinds, rows


def make_dec_plan(rng, names, kinds, rows):
    plan, conds, typed = {}, [], {}
    for _ in range(rng.choice([0, 1, 1, 2, 3])):
        k = rng.randrange(len(names))
        kind = rng.choice(["cmp", "cmp", "cmp", "in", "contains"])
        cond = {"column": names[k], "kind": kind}
        if kinds[k][0] == "dec" and rng.random() < 0.85:
            if kind == "contains":
                kind = cond["kind"] = "cmp"
            cond["dec"] = kinds[k][1]
            typed[names[k]] = True
            lit = lambda: dec_literal(rng, kinds[k][1])
        elif kinds[k][0] == "int" and rng.random() < 0.7:
            if kind == "contains":
                kind = cond["kind"] = "cmp"
            cond["int"] = True
            lit = lambda: rng.choice(["0", "5", "-1", "42"])
        else:
            lit = lambda: rng.choice(["a", "b", "1.5", "0", "", "x y"])
        if kind == "in":
            cond["lits"] = [lit() for _ in range(rng.randint(1, 3))]
        else:
            cond["lit"] = lit()
            cond["op"] = rng.choice(["=", "!=", "<", "<=", ">", ">="]) if kind == "cmp" else None
        conds.append(cond)
    plan["where"] = conds
    if rng.random() < 0.5:
        if rng.random() < 0.6 or not conds:
            plan["select"] = [rng.choice(names) for _ in range(rng.randint(1, 3))]
        return plan
    groups = rng.sample(names, rng.randint(0, min(2, len(names))))
    aggs = []
    ints = [n for n, kd in zip(names, kinds) if kd[0] == "int"]
    for _ in range(rng.randint(0, 3)):
        f = rng.choice(["count", "sum", "min", "max", "distinct"])
        if f == "count" or (f != "distinct" and not ints):
            aggs.append(("count", None))
        elif f == "distinct":
            aggs.append((f, rng.choice(names)))
        else:
            aggs.append((f, rng.choice(ints)))
    plan["group"] = groups
    plan["aggs"] = aggs or [("count", None)]
    labels = list(groups) + [f if f == "count" else "%s:%s" % (f, c) for f, c in plan["aggs"]]
    if labels and rng.random() < 0.6:
        plan["sort"] = rng.choice(["", "-"]) + rng.choice(labels)
    return plan


class Generated(unittest.TestCase):
    check = tp.Plans.check
    expected = tp.Plans.expected

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_random_decimal_plans(self):
        rng = random.Random(20261007)
        done = refused = 0
        for case in range(1500):
            names, kinds, rows = make_dec_table(rng)
            if len(set(names)) != len(names):
                names = ["c%d" % i for i in range(len(names))]
            data = tp.csv_bytes(names, rows, rng)
            plan = make_dec_plan(rng, names, kinds, rows)
            fmt = "csv" if case % 4 == 3 else "json"
            with self.subTest(case=case):
                if self.check(data, plan, case, fmt):
                    done += 1
                    refused += 1 if self.expected(data, plan)[2] else 0
        self.assertGreater(done, 1200)
        self.assertGreater(refused, 100)      # the refusals are in the sample, not only the answers



# ---- stage N2: typed aggregates ---------------------------------------------------------------------------------------------

import refimpl
from fractions import Fraction


def agg_label(fn, col):
    return "count" if fn == "count" else "%s:%s" % (fn, col)


def make_agg_table(rng):
    """Columns: text keys `k0`, `k1`; decimals `d0..` at scales of their own; integers `i0..`; text `t0`."""
    names, kinds = ["k0", "k1"], [("key", 0), ("key", 0)]
    for j in range(rng.randint(1, 3)):
        names.append("d%d" % j)
        kinds.append(("dec", rng.choice([0, 1, 2, 2, 3, 6, 18])))
    names.append("i0")
    kinds.append(("int", 0))
    names.append("t0")
    kinds.append(("text", 0))
    rows = []
    for _ in range(rng.randint(0, 30)):
        row = []
        for kind, scale in kinds:
            if kind == "key":
                row.append(rng.choice(["a", "b", "c", ""]))
            elif kind == "dec":
                cell = dec_cell(rng, scale)
                while rng.random() > 0.005 and ref.dec(cell, scale)[0] != "ok":
                    cell = dec_cell(rng, scale)
                row.append(cell)
                if rng.random() < 0.15:
                    row[-1] = rng.choice(["0", "-0", "0.5", "-0.5", "9" * (18 - scale) + ("." + "9" * scale if scale else ""), "-" + "9" * (18 - scale) + ("." + "9" * scale if scale else "")])
            elif kind == "int":
                row.append(rng.choice(["0", "1", "-1", "42", "-7", "9223372036854775807", "-9223372036854775808", "5000000000"]))
            else:
                row.append(rng.choice(["x", "y", "", "1.5", "1.50"]))
        if rng.random() < 0.03:
            row = row[:-1]
        rows.append(row)
    return names, kinds, rows


def make_agg_plan(rng, names, kinds):
    plan = {"where": []}
    decs = [(n, k[1]) for n, k in zip(names, kinds) if k[0] == "dec"]
    for _ in range(rng.choice([0, 0, 1, 2])):
        n, sc = rng.choice(decs)
        plan["where"].append({"column": n, "dec": sc, "kind": "cmp", "op": rng.choice(["=", "!=", "<", "<=", ">", ">="]), "lit": dec_literal(rng, sc)})
    if rng.random() < 0.3:
        plan["where"].append({"column": rng.choice(["k0", "k1"]), "kind": "cmp", "op": rng.choice(["=", "!="]), "lit": rng.choice(["a", "b"])})
    plan["group"] = rng.sample(["k0", "k1"], rng.randint(0, 2))
    aggs = []
    for _ in range(rng.randint(1, 5)):
        f = rng.choice(["count", "sum", "min", "max", "mean", "distinct"])
        if f == "count":
            aggs.append(("count", None, None, None))
        elif rng.random() < 0.6:
            n, sc = rng.choice(decs)
            if f == "distinct":
                aggs.append((f, n, ("dec", sc), None))
            elif f == "mean":
                aggs.append((f, n, ("dec", sc), rng.choice([None, None, 0, 1, sc, min(sc + 1, 18), 4, 18])))
            else:
                aggs.append((f, n, ("dec", sc), None))
        elif f == "mean":
            aggs.append((f, "i0", rng.choice([None, "int"]), rng.choice([0, 1, 3, 18])))
        elif f == "distinct":
            aggs.append((f, rng.choice(names), None, None))
        else:
            aggs.append((f, "i0", rng.choice([None, "int"]), None))
    # a column read as two types is refused before a row is read: keep the plan clean of it (the types of a column are one scale and the int)
    plan["aggs"] = aggs
    labels = list(plan["group"]) + [agg_label(f, c) for f, c, t, m in aggs]
    if len(set(labels)) == len(labels) and labels and rng.random() < 0.5:
        plan["sort"] = rng.choice(["", "-"]) + rng.choice(labels)
    return plan


def agg_flags(plan):
    args = []
    if plan["where"]:
        args += ["--where", " and ".join(render_plain_cond(c) for c in plan["where"])]
    args += ["--group", ",".join(plan["group"])] if plan["group"] else []
    items = []
    for f, c, t, m in plan["aggs"]:
        if f == "count":
            items.append("count")
            continue
        item = "%s:%s" % (f, c)
        if t == "int":
            item += ":int"
        elif t:
            item += ":dec(%d)" % t[1]
        if m is not None:
            item += "@%d" % m
        items.append(item)
    args += ["--agg", ",".join(items)]
    if plan.get("sort"):
        args += ["--sort", plan["sort"]]
    return args


def render_plain_cond(c):
    col = c["column"] + (":dec(%d)" % c["dec"] if c.get("dec") is not None else "")
    return "%s %s %s" % (col, c["op"], c["lit"])


def reference_groups(names, kinds, rows, plan):
    """(labels, rows) or ("refuse", rule, detail) for a typed grouping, row by row as the tool reads it."""
    idx = {n: i for i, n in enumerate(names)}
    width = len(names)
    groups = {}
    for n, rec in enumerate(rows, 1):
        if len(rec) != width:
            continue
        ok = True
        for c in plan["where"]:
            cell = rec[idx[c["column"]]]
            v = refimpl.holds(dict(c), cell)
            if v is True:
                continue
            if v is False:
                ok = False
                break
            return ("refuse", v, {"row": n, "column": c["column"], "context": "where"})
        if not ok:
            continue
        key = tuple(rec[idx[g]] for g in plan["group"])
        st = groups.setdefault(key, {"count": 0, "v": [[] for _ in plan["aggs"]], "seen": [set() for _ in plan["aggs"]]})
        st["count"] += 1
        for k, (f, c, t, m) in enumerate(plan["aggs"]):
            if f == "count":
                continue
            cell = rec[idx[c]]
            if t and t != "int":
                r = numbers_ref.dec(cell, t[1])
                if r[0] == "refuse":
                    return ("refuse", r[1], {"row": n, "column": c, "context": f})
                val = r[1]
            elif f == "distinct" and not t:
                st["seen"][k].add(cell)
                continue
            else:
                val, bad = refimpl.to_int(cell)
                if bad:
                    return ("refuse", bad, {"row": n, "column": c, "context": f})
            if f == "distinct":
                st["seen"][k].add(val)
            else:
                st["v"][k].append(val)
    out = []
    for key, st in groups.items():
        row = list(key)
        keys = []
        for k, (f, c, t, m) in enumerate(plan["aggs"]):
            sc = t[1] if t and t != "int" else 0
            vals = st["v"][k]
            if f == "count":
                row.append(str(st["count"]))
                keys.append(st["count"])
            elif f == "distinct":
                row.append(str(len(st["seen"][k])))
                keys.append(len(st["seen"][k]))
            elif f == "sum":
                row.append(numbers_ref.text(sum(vals), sc))
                keys.append(sum(vals))
            elif f == "min":
                row.append(numbers_ref.text(min(vals), sc))
                keys.append(min(vals))
            elif f == "max":
                row.append(numbers_ref.text(max(vals), sc))
                keys.append(max(vals))
            else:
                to = sc if m is None else m
                row.append(numbers_ref.mean_text(vals, sc, to))
                keys.append(Fraction(sum(vals), len(vals)))
        out.append((key, row, keys))
    labels = list(plan["group"]) + [agg_label(f, c) for f, c, t, m in plan["aggs"]]
    out.sort(key=lambda r: r[0])
    if plan.get("sort"):
        desc = plan["sort"].startswith("-")
        at = labels.index(plan["sort"].lstrip("-"))
        if at < len(plan["group"]):
            out.sort(key=lambda r: r[0][at], reverse=desc)
        else:
            out.sort(key=lambda r: r[2][at - len(plan["group"])], reverse=desc)
    return labels, [r[1] for r in out]


class Aggregates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def groups(self, data, *args, fmt=None):
        got = self.s.table("t.csv", data, "--group", "g", *args, *( ["--format", fmt] if fmt else []))
        return got

    def csv_rows(self, got):
        self.assertEqual(got.status, 0, got)
        return list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))

    def test_every_aggregate_of_a_decimal_column_as_python_computes_it(self):
        data = "g,p\na,1.50\na,2.25\nb,-0.05\nb,0.00\na,1.5\nb,10.10\nc,-0.00\n"
        got = self.groups(data, "--agg", "count,sum:p:dec(2),min:p:dec(2),max:p:dec(2),mean:p:dec(2),mean:p:dec(2)@4,mean:p:dec(2)@0,distinct:p:dec(2),distinct:p", "--format", "csv")
        rows = self.csv_rows(got)
        self.assertEqual(rows[0], ["g", "count", "sum:p", "min:p", "max:p", "mean:p", "mean:p", "mean:p", "distinct:p", "distinct:p"])
        self.assertEqual(rows[1:], [["a", "3", "5.25", "1.50", "2.25", "1.75", "1.7500", "2", "2", "3"], ["b", "3", "10.05", "-0.05", "10.10", "3.35", "3.3500", "3", "3", "3"],
                                    ["c", "1", "0.00", "0.00", "0.00", "0.00", "0.0000", "0", "1", "1"]])

    def test_the_mean_is_rounded_half_to_even_from_the_exact_quotient(self):
        # (values at scale, mean scale) -> the mean; every case also against Python's Fraction and Decimal
        cases = [(["0.001", "0.002"], 3, 3, "0.002"), (["0.002", "0.003"], 3, 3, "0.002"), (["0.01", "0.02"], 2, 2, "0.02"), (["0.02", "0.03"], 2, 2, "0.02"),
                 (["-0.01", "-0.02"], 2, 2, "-0.02"), (["-0.02", "-0.03"], 2, 2, "-0.02"), (["-0.05", "0.00"], 2, 2, "-0.02"), (["-0.01", "0.00"], 2, 2, "0.00"),
                 (["1.000", "2.000", "2.000"], 3, 3, "1.667"), (["0.001", "0.001", "0.002"], 3, 3, "0.001"), (["2", "3"], 0, 0, "2"), (["3", "4"], 0, 0, "4"),
                 (["0.001", "0.002"], 3, 4, "0.0015"), (["0.15", "0.25"], 2, 1, "0.2"), (["0.25", "0.35"], 2, 1, "0.3"), (["0.05"], 2, 0, "0"), (["0.5"], 1, 0, "0"),
                 (["1.5"], 1, 0, "2"), (["2.5"], 1, 0, "2"), (["-1.5"], 1, 0, "-2"), (["-2.5"], 1, 0, "-2"), (["0.999999999999999999"] * 3, 18, 18, "0.999999999999999999"),
                 (["0.999999999999999999", "0.000000000000000001"], 18, 17, "0.50000000000000000"), (["999999999999999999"] * 7, 0, 18, "999999999999999999.000000000000000000"),
                 (["999999999999999999", "999999999999999998"], 0, 0, "999999999999999998"), (["-999999999999999999", "-999999999999999998"], 0, 0, "-999999999999999998")]
        for cells, scale, out, want in cases:
            with self.subTest(cells=cells, scale=scale, out=out):
                vals = [numbers_ref.dec(c, scale)[1] for c in cells]
                self.assertEqual(numbers_ref.mean_text(vals, scale, out), want)
                from decimal import Decimal, ROUND_HALF_EVEN, getcontext
                getcontext().prec = 80
                dq = (Decimal(sum(vals)) / Decimal(len(vals)) / Decimal(10) ** scale).quantize(Decimal(1).scaleb(-out), rounding=ROUND_HALF_EVEN)
                self.assertEqual(Decimal(want), dq if dq != 0 else Decimal(0))
                data = "g,p\n" + "".join("a,%s\n" % c for c in cells)
                got = self.groups(data, "--agg", "mean:p:dec(%d)@%d" % (scale, out), "--format", "csv")
                self.assertEqual(self.csv_rows(got)[1], ["a", want], got)

    def test_sums_past_64_bits_at_every_scale_and_at_the_edges_of_the_width(self):
        for scale in (0, 2, 18):
            top = "9" * (18 - scale) + ("." + "9" * scale if scale else "")
            for cells in ([top] * 2, [top] * 1000, ["-" + top] * 1000, [top, "-" + top], [top] * 500 + ["-" + top] * 499, ["0"] * 5, ["1" if scale == 0 else "0.%s1" % ("0" * (scale - 1))] * 3):
                with self.subTest(scale=scale, n=len(cells), first=cells[0]):
                    vals = [numbers_ref.dec(c, scale)[1] for c in cells]
                    data = "g,p\n" + "".join("a,%s\n" % c for c in cells)
                    got = self.groups(data, "--agg", "sum:p:dec(%d),min:p:dec(%d),max:p:dec(%d),mean:p:dec(%d)" % ((scale,) * 4), "--format", "csv")
                    self.assertEqual(self.csv_rows(got)[1], ["a", numbers_ref.sum_text(vals, scale), numbers_ref.text(min(vals), scale), numbers_ref.text(max(vals), scale), numbers_ref.mean_text(vals, scale, scale)], got)

    def test_one_five_and_one_fifty_are_one_distinct_value(self):
        data = "g,p\na,1.5\na,1.50\na,1.500\na,01.5\na,+1.5\na,2\n"
        got = self.groups(data, "--agg", "distinct:p:dec(3),distinct:p,count", "--format", "csv")
        self.assertEqual(self.csv_rows(got)[1], ["a", "2", "6", "6"])

    def test_a_column_that_is_an_integer_is_summed_as_before_and_its_mean_needs_a_scale(self):
        data = "g,n\na,3\na,4\nb,-5\n"
        got = self.groups(data, "--agg", "sum:n,sum:n:int,mean:n@2,mean:n:int@0,min:n,max:n:int", "--format", "csv")
        self.assertEqual(self.csv_rows(got)[1:], [["a", "7", "7", "3.50", "4", "3", "4"], ["b", "-5", "-5", "-5.00", "-5", "-5", "-5"]])
        for spec in ("mean:n", "mean:n:int"):
            got = self.groups(data, "--agg", spec)
            self.assertEqual((got.status, got.first_rule()), (2, "agg.bad-spec"), (spec, got))

    def test_sorting_by_a_sum_a_minimum_and_a_mean_is_by_the_exact_value(self):
        data = "g,p\n" + "".join("%s,%s\n" % (k, v) for k, v in (("a", "1.00"), ("a", "1.00"), ("a", "1.01"), ("b", "1.00"), ("b", "1.00"), ("c", "0.99"), ("c", "2.00"), ("d", "-3.00"), ("d", "0.00")))
        for sort in ("-sum:p", "sum:p", "-mean:p", "mean:p", "-min:p", "max:p", "-count"):
            with self.subTest(sort):
                got = self.groups(data, "--agg", "sum:p:dec(2),mean:p:dec(2)@0,min:p:dec(2),max:p:dec(2),count", "--sort", sort, "--format", "csv")
                want = {}
                for line in data.split("\n")[1:-1]:
                    k, v = line.split(",")
                    want.setdefault(k, []).append(numbers_ref.dec(v, 2)[1])
                keyf = {"sum": sum, "mean": lambda x: Fraction(sum(x), len(x)), "min": min, "max": max, "count": len}[sort.lstrip("-").split(":")[0]]
                order = sorted(sorted(want), key=lambda k: keyf(want[k]), reverse=sort.startswith("-"))
                self.assertEqual([r[0] for r in self.csv_rows(got)[1:]], order, got)

    def test_the_refusals_of_a_decimal_aggregate(self):
        for spec, rows, rule, column, row, extra in (("sum:p:dec(2)", "1.5\n1.234\n", "value.decimal-scale", "p", 2, {"digits": 3, "scale": 2, "context": "sum"}),
                                                     ("min:p:dec(2)", "1.5\nx\n", "value.not-decimal", "p", 2, {"context": "min"}),
                                                     ("max:p:dec(2)", "\n", "value.not-decimal", "p", 1, {"context": "max"}),
                                                     ("mean:p:dec(0)", "1\n1e3\n", "value.not-decimal", "p", 2, {"context": "mean"}),
                                                     ("distinct:p:dec(1)", "1.5\n100000000000000000.0\n", "value.decimal-too-wide", "p", 2, {"context": "distinct", "scale": 1}),
                                                     ("sum:p", "1.5\n", "value.not-integer", "p", 1, {"context": "sum"}),
                                                     ("mean:p@2", "5\n9223372036854775808\n", "value.integer-overflow", "p", 2, {"context": "mean"})):
            with self.subTest(spec):
                got = self.s.table("t.csv", "g,p\n" + "".join("a,%s\n" % r for r in rows.split("\n")[:-1]), "--group", "g", "--agg", spec)
                self.assertEqual((got.status, got.first_rule()), (8, rule), got)
                d = got.error()["detail"]
                self.assertEqual((d["column"], d["row"]), (column, row), got)
                for k, v in extra.items():
                    self.assertEqual(d[k], v, (k, got))

    def test_the_grammar_of_the_items(self):
        data = "g,p,x:dec(2),a:int,n,int,x@2\na,1.5,7,3,4,5,6\n"
        def agg(spec, fmt=True):
            return self.s.table("t.csv", data, "--group", "g", "--agg", spec, *(["--format", "csv"] if fmt else []))
        # a suffix is a suffix only when a column name is left before it
        self.assertEqual(self.csv_rows(agg("sum:int"))[1], ["a", "5"])                    # a column called `int`
        self.assertEqual(self.csv_rows(agg("sum:n:int"))[1], ["a", "4"])
        self.assertEqual(self.csv_rows(agg("sum:#5:int"))[1], ["a", "4"])
        self.assertEqual(self.csv_rows(agg("sum:#2:dec(1)"))[1], ["a", "1.5"])
        self.assertEqual(self.csv_rows(agg("sum:x\\:dec(2)"))[1], ["a", "7"])               # a column really called x:dec(2), as an integer
        self.assertEqual(self.csv_rows(agg("sum:a\\:int"))[1], ["a", "3"])                   # one called a:int
        self.assertEqual(self.csv_rows(agg("mean:x\\@2@3"))[1], ["a", "6.000"])             # one called x@2, mean at scale 3
        got = agg("sum:x:dec(2)", False)                                                   # the same text unescaped is the suffix: column x does not exist
        self.assertEqual(got.first_rule(), "column.unknown")
        for spec in ("count:dec(2)", "count@2", "sum:p:dec(19)", "sum:p:dec(2)@2", "mean:p:dec(2)@19", "mean:p@99", "distinct:p@1", "sum:p:float", "sum:", "mean:p:dec()", "sum:p:dec(2"):
            with self.subTest(spec):
                got = agg(spec, False)
                self.assertIn(got.first_rule(), ("agg.bad-spec", "column.unknown"), (spec, got))
                if spec not in ("sum:p:float", "sum:p:dec(2"):
                    self.assertEqual(got.first_rule(), "agg.bad-spec", (spec, got))

    def test_one_column_read_two_ways_across_where_agg_and_order_by_is_refused(self):
        data = "g,x\na,1.5\n"
        for args in (["--where", "x:dec(2) > 1", "--agg", "sum:x:dec(3)"], ["--where", "x:dec(2) > 1", "--agg", "sum:x"], ["--agg", "sum:x:dec(2),min:x:dec(3)"], ["--agg", "sum:x:dec(2),max:x"],
                     ["--agg", "sum:x:dec(2),distinct:x:int"], ["--agg", "mean:x:dec(2),mean:x@1"], ["--group", "g", "--agg", "sum:x:dec(2)", "--where", "x:int > 0"]):
            got = self.s.table("t.csv", data, *args)
            self.assertEqual((got.status, got.first_rule()), (2, "column.type-conflict"), (args, got))
        for args in (["--agg", "sum:x:dec(2),min:x:dec(2),mean:x:dec(2)@1,distinct:x:dec(2)"], ["--agg", "sum:x:dec(2),distinct:x"], ["--where", "x:dec(2) > 1", "--agg", "sum:x:dec(2),count"]):
            got = self.s.table("t.csv", data, *args)
            self.assertEqual(got.status, 0, (args, got))

    def test_a_group_of_nothing_and_a_grouping_of_everything(self):
        data = "g,p\na,1.5\n"
        got = self.s.table("t.csv", data, "--where", "p:dec(2) > 99", "--agg", "sum:p:dec(2),mean:p:dec(2)", "--format", "csv")
        self.assertEqual(self.csv_rows(got), [["sum:p", "mean:p"]])
        got = self.s.table("t.csv", data, "--agg", "sum:p:dec(2),mean:p:dec(2)@3,min:p:dec(2)", "--format", "csv")
        self.assertEqual(self.csv_rows(got)[1], ["1.50", "1.500", "1.50"])
        got = self.s.table("t.csv", data, "--agg", "sum:p:dec(2)")
        self.assertEqual(got.data()["rows"], [["1.50"]])      # json: a string at the scale

    def test_random_typed_groupings_against_the_reference(self):
        rng = random.Random(20261008)
        answers = refusals = 0
        for case in range(1600):
            names, kinds, rows = make_agg_table(rng)
            data = tp.csv_bytes(names, rows, rng)
            plan = make_agg_plan(rng, names, kinds)
            args = agg_flags(plan)
            fmt = "csv" if case % 3 == 2 else "json"
            with self.subTest(case=case):
                want = reference_groups(names, kinds, [r for r in csv.reader(io.StringIO(data.decode("latin-1"), newline="")) if r][1:], plan)
                got = self.s.table("t.csv", data, *args, *( ["--format", "csv"] if fmt == "csv" else ["--limit", "1000000"]))
                self.assertNotIn(got.status, TRAPS, (args, data, got))
                ragged = any(len(r) != len(names) for r in rows)
                if want[0] == "refuse":
                    refusals += 1
                    self.assertEqual(got.status, 8, (args, data, got))
                    if fmt == "json":
                        self.assertEqual(got.first_rule(), want[1], (args, data, got))
                        d = got.error()["detail"]
                        self.assertEqual((d["row"], d["column"], d["context"]), (want[2]["row"], want[2]["column"], want[2]["context"]), (args, data, got))
                    else:
                        self.assertIn(want[1], got.stderr.decode(), (args, data, got))
                    continue
                answers += 1
                labels, wrows = want
                if fmt == "csv":
                    out = [r for r in csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""))]
                    self.assertEqual(out, [labels] + wrows if wrows or labels else out, (args, data))
                    self.assertEqual(got.status, 8 if ragged else 0, (args, data, got))
                else:
                    d = got.data() if got.status == 0 else None
                    if d is None:
                        self.assertTrue(ragged, (args, data, got))
                        continue
                    self.assertEqual((d["columns"], d["rows"]), (labels, wrows), (args, data, got))
        self.assertGreater(answers, 900)
        self.assertGreater(refusals, 80)

    def test_fuzzed_cells_and_specs_never_trap(self):
        rng = random.Random(77)
        alpha = "0123456789+-. eE_,x:@()dcimnsu\\"
        for i in range(300):
            scale = rng.choice([0, 1, 2, 5, 18])
            cells = ["".join(rng.choice(alpha[:20]) for _ in range(rng.randint(0, 10))) if rng.random() < 0.4 else dec_cell(rng, scale) for _ in range(rng.randint(1, 8))]
            spec = rng.choice(["sum", "min", "max", "mean", "distinct"]) + ":p:dec(%d)" % scale + ("@%d" % rng.randint(0, 20) if rng.random() < 0.3 else "")
            if rng.random() < 0.2:
                spec = "".join(rng.choice(alpha) for _ in range(rng.randint(1, 14)))
            data = enc([["g", "p"]] + [["a", c] for c in cells])
            got = self.s.table("t.csv", data, "--group", "g", "--agg", spec)
            self.assertNotIn(got.status, TRAPS, (spec, cells, got))
            self.assertEqual(validate(got), [], (spec, cells, got))


class Parallel(par.Same):
    """G4: the sequential answer is every thread count's, with a boundary in nearly every record, for decimal conditions and for
    the refusals, which name the first bad cell in file order."""

    def table(self, rows, bad=None):
        rows = [list(r) for r in rows]
        for at, cell in (bad or {}).items():
            rows[at][1] = cell
        return enc([["id", "x", "t"]] + rows)

    def test_decimal_filters_every_way(self):
        rng = random.Random(5)
        rows = [[str(i), rng.choice(["%d.%02d" % (rng.randint(-50, 50), rng.randint(0, 99)), "%d.5" % rng.randint(0, 9), "7", "-0", ".25"]), rng.choice(["a", "b,c", 'q"r'])] for i in range(150)]
        data = self.table(rows)
        for args in (["--where", "x:dec(2) >= 10.50", "--select", "id"], ["--where", "x:dec(2) > -3 and x:dec(2) < 3", "--format", "csv"], ["--where", "t = a and x:dec(2) in (7, 0.25, 1.5)"],
                     ["--where", "x:dec(2) != 7", "--select", "id,t", "--limit", "9"], ["--where", "x:dec(2) = 7", "--group", "t", "--agg", "count,distinct:x"],
                     ["--where", "x:dec(2) <= 0", "--group", "t", "--agg", "min:id,max:id,sum:id"]):
            self.same(data, args, "decimal filter")

    def test_the_first_refusal_in_file_order_wins(self):
        rng = random.Random(6)
        rows = [[str(i), "%d.%02d" % (rng.randint(0, 9), rng.randint(0, 99)), "t"] for i in range(140)]
        for bad in ({100: "1.234", 30: "abc"}, {30: "", 100: "1.234"}, {100: "1e3", 101: "x", 139: "99999999999999999999.9"}, {139: "-"}, {0: " 1"}, {70: "1.234", 20: "10000000000000000.00"}):
            data = self.table(rows, bad)
            for args in (["--where", "x:dec(2) > 1"], ["--where", "x:dec(2) > 1", "--format", "csv"], ["--where", "id != 5 and x:dec(2) > 1", "--select", "t"], ["--where", "x:dec(2) > 1", "--group", "t"]):
                self.same(data, args, "refusals %r" % (bad,))
            self.assertIn(b"row", self.outcome(data, ["--where", "x:dec(2) > 1"])[1])

    def test_decimal_aggregates_every_way(self):
        rng = random.Random(8)
        rows = [[str(i), rng.choice(["%d.%02d" % (rng.randint(-50, 50), rng.randint(0, 99)), "%d.5" % rng.randint(0, 9), "7", "-0", ".25", "99999999999999.99", "-99999999999999.99"]), rng.choice(["a", "b", "c"])] for i in range(160)]
        data = self.table(rows)
        for args in (["--group", "t", "--agg", "count,sum:x:dec(2),min:x:dec(2),max:x:dec(2),mean:x:dec(2),mean:x:dec(2)@5,mean:x:dec(2)@0,distinct:x:dec(2),distinct:x"],
                     ["--agg", "sum:x:dec(2),mean:x:dec(2)@3,min:x:dec(2)", "--where", "x:dec(2) > -20"],
                     ["--group", "t", "--agg", "sum:x:dec(2),mean:x:dec(2)", "--sort", "-mean:x", "--top", "2"], ["--group", "t", "--agg", "sum:x:dec(2)", "--sort", "sum:x", "--format", "csv"],
                     ["--group", "t,id", "--agg", "mean:id@1,sum:x:dec(2)", "--max-groups", "1000"]):
            self.same(data, args, "decimal aggregates")

    def test_sums_that_need_the_carry_every_way(self):
        rows = [[str(i), "999999999999999.99" if i % 3 else "-999999999999999.99", "k%d" % (i % 4)] for i in range(150)]
        data = self.table(rows)
        for args in (["--group", "t", "--agg", "sum:x:dec(2),mean:x:dec(2)@4,min:x:dec(2),max:x:dec(2)"], ["--agg", "sum:x:dec(2),sum:id"]):
            self.same(data, args, "carry")

    def test_the_first_refusal_of_an_aggregate_in_file_order_wins(self):
        rng = random.Random(9)
        rows = [[str(i), "%d.%02d" % (rng.randint(0, 9), rng.randint(0, 99)), "t%d" % (i % 3)] for i in range(150)]
        for bad in ({120: "1.234", 40: "abc"}, {40: "", 120: "1.234"}, {120: "1e3", 121: "x", 149: "99999999999999999999.9"}, {149: "-"}, {0: " 1"}):
            data = self.table(rows, bad)
            for args in (["--group", "t", "--agg", "sum:x:dec(2)"], ["--agg", "min:x:dec(2),max:x:dec(2)"], ["--group", "t", "--agg", "mean:x:dec(2)"], ["--group", "t", "--agg", "distinct:x:dec(2)"],
                         ["--group", "t", "--agg", "sum:x:dec(2)", "--format", "csv"]):
                self.same(data, args, "refusals %r" % (bad,))

    def test_a_decimal_beside_ragged_rows_and_quoted_newlines(self):
        rows = [[str(i), "%d.5" % i, 'multi\nline "%d"' % i] for i in range(60)]
        data = self.table(rows) + b"61\n" + self.table([["62", "2.5", "z"]]).split(b"\n", 1)[1]
        for args in (["--where", "x:dec(1) > 20", "--select", "t", "--format", "csv"], ["--where", "x:dec(1) > 20"], ["--where", "x:dec(1) > 20", "--group", "t"]):
            self.same(data, args, "ragged and quoted")


if __name__ == "__main__":
    unittest.main()
