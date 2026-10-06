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
        got = self.s.table("t.csv", "id,x\n1,3.999\n", "--where", "x:dec(2) >= 1 and id = 1", "--select", "id")
        self.assertEqual(got.first_rule(), "value.decimal-scale")
        repair = got.error()["repair"]
        self.assertEqual(repair["kind"], "choose", got)
        argv = repair["options"][0]["invocation"] if "invocation" in repair["options"][0] else repair["options"][0]["argv"]
        self.assertIn("x:dec(3) >= 1 and id = 1", argv, got)
        # and that invocation works
        again = self.s.table("t.csv", "id,x\n1,3.999\n", "--where", "x:dec(3) >= 1 and id = 1", "--select", "id")
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
        got = self.s.table("t.csv", data, "--where", "x != '' and x:dec(2) > 1 and id != 3", "--select", "id")
        self.assertEqual(got.data()["rows"], [["2"]])
        got = self.s.table("t.csv", data, "--where", "x:dec(2) > 1", "--select", "id")      # an empty cell is not a decimal
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"]), (8, "value.not-decimal", 1))
        got = self.s.table("t.csv", data, "--where", "id < 3 and x:dec(2) > 1")
        self.assertEqual((got.status, got.error()["detail"]["row"]), (8, 1))

    def test_a_quoted_cell_is_read_without_its_quotes(self):
        data = 'id,x\n1,"1.50"\n2,"2,5"\n'
        got = self.s.table("t.csv", data, "--where", "x:dec(2) = 1.5", "--select", "id")
        self.assertEqual(got.data()["rows"], [["1"]])
        got = self.s.table("t.csv", data, "--where", "x:dec(2) >= 0", "--select", "id")
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"], got.error()["detail"]["value"]), (8, "value.not-decimal", 2, "2,5"))

    def test_the_digits_and_the_width_are_the_declared_ones(self):
        mx = "9999999999999999.99"
        for scale, cell, ok in ((2, mx, True), (2, "10000000000000000.00", False), (0, "999999999999999999", True), (0, "1000000000000000000", False),
                                (18, "0.999999999999999999", True), (18, "1.000000000000000000", False), (18, "0.000000000000000001", True), (1, "0.01", False),
                                (3, "-999999999999999.999", True), (3, "-1000000000000000.000", False), (0, "5.", True), (0, "5.0", False)):
            got = self.s.table("t.csv", enc([["x"], [cell]]), "--where", "x:dec(%d) != 7" % scale)
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
            if len(lit.split(".")[1] if "." in lit else "") > scale:
                lit = "1"
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
    cands = ["0", "1", "-1.5", "12.25", "+3", ".5", "5.", "007.1", "-0", "999", "0.1", "0.05", "-0.001", "1.000", "123456.789"]
    return rng.choice([c for c in cands if "." in c and len(c.split(".")[1]) <= scale] + [c for c in cands if "." not in c])


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

    def test_a_decimal_beside_ragged_rows_and_quoted_newlines(self):
        rows = [[str(i), "%d.5" % i, 'multi\nline "%d"' % i] for i in range(60)]
        data = self.table(rows) + b"61\n" + self.table([["62", "2.5", "z"]]).split(b"\n", 1)[1]
        for args in (["--where", "x:dec(1) > 20", "--select", "t", "--format", "csv"], ["--where", "x:dec(1) > 20"], ["--where", "x:dec(1) > 20", "--group", "t"]):
            self.same(data, args, "ragged and quoted")


if __name__ == "__main__":
    unittest.main()
