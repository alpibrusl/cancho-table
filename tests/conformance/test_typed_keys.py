"""Typed group keys and sort keys (docs/numbers.md, stage N5): `--group x:dec(2)`, `--group x:float`, `--group x:int`, and `--order-by` of the same.

A typed key is the VALUE of the cell: `1.5`, `1.50` and `01.5` are one group at `:dec(2)` or `:float`, `-0` is `0`, and the group is written back at the column's
scale (`1.50`) or as the shortest decimal that reads back (`1.5`), whichever cell came first. The groups come in the numeric order of the key (the order-preserving
bytes of docs/numbers.md 4.5): ascending, negatives first; text columns stay bytewise, field by field. `--order-by` of a typed key sorts rows by value, descending
with `-`, ties in file order. The oracle is `numbers_ref.py` (Python `int`, `Fraction`, `float()`), a stable sort and a dict.
"""

import csv
import functools
import io
import random
import unittest

import numbers_ref as ref
import test_float as tf
import test_parallel as par
from harness import Scratch, TRAPS, validate
from test_numbers import enc


def dec_pool(rng):
    k = rng.random()
    if k < 0.15:
        return rng.choice(["0", "-0", "0.0", "0.00", "-0.00", "+0", ".5", "5.", "1", "1.0", "1.00", "01.5", "1.5", "1.50", "+1.5", "-1.5", "-01.50", "2.25", "-2.25", "10", "10.00", "99999999999999"])
    if k < 0.6:
        return "%.2f" % (rng.randint(-300, 300) / 100)
    if k < 0.85:
        return str(rng.randint(-30, 30))
    return "%d.%d" % (rng.randint(-5, 5), rng.randint(0, 9))


def float_pool(rng):
    k = rng.random()
    if k < 0.3:
        return rng.choice(["0", "-0", "0.0", "-0.0", "0e5", "1", "1.0", "1e0", "10e-1", "0.1", "1e-1", "0.10", "+.1", "1.5", "1.50", "01.5", "15e-1", "-1.5", "2.5", "-2.5", "1e3", "1000", "5e-324", "-5e-324", "0.30000000000000004", "0.3"])
    if k < 0.55:
        return "%.2f" % (rng.randint(-300, 300) / 100)
    if k < 0.8:
        return str(rng.randint(-30, 30))
    return rng.choice(tf.lex_float_cells(rng, 5))


def int_pool(rng):
    k = rng.random()
    if k < 0.3:
        return rng.choice(["0", "-0", "+0", "00", "1", "01", "+1", "-1", "-01", "7", "007"])
    return str(rng.randint(-12, 12))


TEXT = ["a", "b", "", "ab", "B", "a b", 'q"r']


def make_table(rng):
    names = ["k", "d", "f", "i", "v"]
    rows = []
    for _ in range(rng.randint(0, 30)):
        row = [rng.choice(TEXT), dec_pool(rng), float_pool(rng), int_pool(rng), float_pool(rng)]
        if rng.random() < 0.012:
            col = rng.choice([1, 2, 3, 4])
            row[col] = rng.choice(["", "x", "nan", "inf", "1e999", "1.234", "1e3", " 1", "99999999999999999999", "-", "."]) if col != 3 else rng.choice(["", "x", "1.5", "99999999999999999999"])
        if rng.random() < 0.02:
            row = row[:-1]
        rows.append(row)
    return names, rows


TYPES = {"d": ("dec", 2), "f": ("float", None), "i": ("int", None), "v": ("float", None)}


def read(col, cell):
    """("ok", value) | ("refuse", rule, direction) for a typed column; text is its cell."""
    t = TYPES.get(col)
    if t is None:
        return ("ok", cell)
    if t[0] == "dec":
        r = ref.dec(cell, t[1])
        return r if r[0] == "ok" else ("refuse", r[1], None)
    if t[0] == "float":
        r = ref.flt(cell)
        return r
    try:
        if not cell or not all(c in "0123456789+-" for c in cell) or cell in ("+", "-") or "+" in cell[1:] or "-" in cell[1:]:
            return ("refuse", "value.not-integer", None)
        v = int(cell)
    except ValueError:
        return ("refuse", "value.not-integer", None)
    if not -2 ** 63 <= v < 2 ** 63:
        return ("refuse", "value.integer-overflow", None)
    return ("ok", v)


def spell(col, v):
    t = TYPES.get(col)
    if t is None:
        return v
    if t[0] == "dec":
        return ref.text(v, t[1])
    if t[0] == "float":
        return ref.float_text(v)
    return str(v)


def suffix(col):
    t = TYPES.get(col)
    if t is None:
        return ""
    return {"dec": ":dec(2)", "float": ":float", "int": ":int"}[t[0]]


# ---- group plans -------------------------------------------------------------------------------------------------------

def make_group_plan(rng):
    cols = rng.sample(["k", "d", "f", "i"], rng.randint(1, 3))
    aggs = []
    for _ in range(rng.randint(0, 3)):
        aggs.append(rng.choice([("count", None), ("min", "v"), ("max", "v"), ("sum", "v"), ("mean", "v"), ("distinct", "f"), ("distinct", "k"), ("min", "d"), ("max", "i"), ("sum", "i")]))
    if not aggs:
        aggs = [("count", None)]            # a grouping with no --agg counts
    plan = {"group": cols, "aggs": aggs}
    plan["cond"] = rng.choice([None, None, ("k", "=", rng.choice(["a", "b"])), ("k", "!=", "a")])
    labels = list(cols) + [("count" if f == "count" else "%s:%s" % (f, c)) for f, c in aggs]
    if len(set(labels)) == len(labels) and rng.random() < 0.5:
        plan["sort"] = rng.choice(["", "-"]) + rng.choice(labels)
    return plan


def group_args(plan):
    args = []
    if plan["cond"]:
        c = plan["cond"]
        args += ["--where", "%s %s %s" % c]
    args += ["--group", ",".join(c + suffix(c) for c in plan["group"])]
    items = []
    for f, c in plan["aggs"]:
        if f == "count":
            items.append("count")
        else:
            s = suffix(c)
            if f == "distinct" and c == "k":
                s = ""
            if f == "mean" and TYPES.get(c, ("",))[0] == "dec":
                s += "@4"
            if f == "mean" and TYPES.get(c, ("",))[0] == "int":
                s += "@2"
            items.append("%s:%s%s" % (f, c, s))
    if items:
        args += ["--agg", ",".join(items)]
    if plan.get("sort"):
        args += ["--sort", plan["sort"]]
    return args


def agg_value(f, c, vals):
    if f == "count":
        return len(vals)
    if f == "distinct":
        return len(set(vals))
    if f == "min":
        return min(vals)
    if f == "max":
        return max(vals)
    if TYPES[c][0] == "float":
        r = ref.fsum_ref(vals) if f == "sum" else ref.fmean_ref(vals)
        return r
    return sum(vals) if f == "sum" else None


def group_reference(names, rows, plan):
    """("groups", labels, rows) | ("refuse", rule, detail) for a grouping."""
    idx = {n: i for i, n in enumerate(names)}
    groups = {}
    for n, rec in enumerate(rows, 1):
        if len(rec) != len(names):
            continue
        if plan["cond"]:
            col, op, lit = plan["cond"]
            eq = rec[idx[col]].encode("latin-1") == lit.encode("latin-1")
            if (op == "=") != eq:
                continue
        key = []
        for c in plan["group"]:
            r = read(c, rec[idx[c]])
            if r[0] == "refuse":
                return ("refuse", r[1], {"row": n, "column": c, "context": "group"})
            key.append(r[1])
        st = groups.setdefault(tuple(key), {"count": 0, "v": [[] for _ in plan["aggs"]]})
        st["count"] += 1
        for k, (f, c) in enumerate(plan["aggs"]):
            if f == "count":
                continue
            if f == "distinct" and c == "k":
                st["v"][k].append(rec[idx[c]])
                continue
            r = read(c, rec[idx[c]])
            if r[0] == "refuse":
                return ("refuse", r[1], {"row": n, "column": c, "context": {"sum": "sum", "mean": "mean", "min": "min", "max": "max", "distinct": "distinct"}[f]})
            st["v"][k].append(r[1])
    labels = list(plan["group"]) + [("count" if f == "count" else "%s:%s" % (f, c)) for f, c in plan["aggs"]]
    out = []
    for key, st in groups.items():
        row = [spell(c, v) for c, v in zip(plan["group"], key)]
        sortkeys = []
        for k, (f, c) in enumerate(plan["aggs"]):
            vals = st["v"][k]
            if f == "count":
                row.append(str(st["count"]))
                sortkeys.append(st["count"])
                continue
            if f == "distinct":
                row.append(str(len(set(vals))))
                sortkeys.append(len(set(vals)))
                continue
            t = TYPES[c]
            if f in ("min", "max"):
                x = (min if f == "min" else max)(vals)
                row.append(spell(c, x))
                sortkeys.append(x)
            elif t[0] == "float":
                r = ref.fsum_ref(vals) if f == "sum" else ref.fmean_ref(vals)
                if r[0] == "overflow":
                    return ("refuse", "agg.float-overflow", {"skip": True})
                row.append(ref.float_text(r[1]))
                sortkeys.append(r[1])
            elif f == "sum":
                row.append(str(sum(vals)))
                sortkeys.append(sum(vals))
            else:
                from fractions import Fraction
                q = Fraction(sum(vals), len(vals))
                row.append(None)
                sortkeys.append(q)
        out.append((key, row, sortkeys))
    out.sort(key=lambda r: tuple(r[0]))
    if plan.get("sort"):
        desc = plan["sort"].startswith("-")
        at = labels.index(plan["sort"].lstrip("-"))
        if at < len(plan["group"]):
            out.sort(key=lambda r: r[0][at], reverse=desc)
        else:
            out.sort(key=lambda r: r[2][at - len(plan["group"])], reverse=desc)
    return ("groups", labels, [r[1] for r in out])


def cell_eq(got, want):
    if want is None:
        return True            # a mean of integers: not compared here
    if got == want:
        return True
    try:
        return ref.same_text(got, float(want))
    except ValueError:
        return False


def rows_eq(got, want):
    return len(got) == len(want) and all(len(g) == len(w) and all(cell_eq(a, b) for a, b in zip(g, w)) for g, w in zip(got, want))


# ---- order-by plans ----------------------------------------------------------------------------------------------------

def make_order_plan(rng):
    keys = []
    for c in rng.sample(["k", "d", "f", "i"], rng.randint(1, 3)):
        keys.append((c, rng.random() < 0.4))
    plan = {"keys": keys, "select": rng.sample(["k", "d", "f", "i", "v"], rng.randint(1, 4))}
    plan["cond"] = rng.choice([None, None, ("k", "=", rng.choice(["a", "b"])), ("k", "!=", "a")])
    plan["top"] = rng.choice([0, 0, 0, 1, 3, 7])
    return plan


def order_args(plan):
    args = []
    if plan["cond"]:
        args += ["--where", "%s %s %s" % plan["cond"]]
    args += ["--order-by", ",".join(("-" if d else "") + c + suffix(c) for c, d in plan["keys"])]
    args += ["--select", ",".join(plan["select"])]
    if plan["top"]:
        args += ["--top", str(plan["top"])]
    return args


def order_reference(names, rows, plan):
    idx = {n: i for i, n in enumerate(names)}
    held = []
    for n, rec in enumerate(rows, 1):
        if len(rec) != len(names):
            continue
        if plan["cond"]:
            col, op, lit = plan["cond"]
            if (op == "=") != (rec[idx[col]] == lit):
                continue
        key = []
        for c, d in plan["keys"]:
            r = read(c, rec[idx[c]])
            if r[0] == "refuse":
                return ("refuse", r[1], {"row": n, "column": c, "context": "order-by"})
            key.append(r[1])
        held.append((key, [rec[idx[c]] for c in plan["select"]]))

    def cmp(a, b):
        for (x, y), (c, d) in zip(zip(a[0], b[0]), plan["keys"]):
            if x != y:
                r = -1 if (x.encode("latin-1") < y.encode("latin-1") if isinstance(x, str) else x < y) else 1
                return -r if d else r
        return 0
    held.sort(key=functools.cmp_to_key(cmp))
    if plan["top"]:
        held = held[:plan["top"]]
    return ("rows", plan["select"], [h[1] for h in held])


class Plans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def csv_data(self, names, rows, rng):
        out = io.StringIO(newline="")
        w = csv.writer(out, lineterminator=rng.choice(["\n", "\r\n"]))
        w.writerow(names)
        for r in rows:
            w.writerow(r)
        return out.getvalue().encode("latin-1")

    def check(self, seed, count, make_plan, args_of, reference, min_answers, min_refusals):
        rng = random.Random(seed)
        answers = refusals = 0
        for case in range(count):
            names, rows = make_table(rng)
            data = self.csv_data(names, rows, rng)
            plan = make_plan(rng)
            args = args_of(plan)
            recs = [r for r in csv.reader(io.StringIO(data.decode("latin-1"), newline="")) if r][1:]
            want = reference(names, recs, plan)
            if want[0] == "refuse" and want[2].get("skip"):
                continue
            as_csv = case % 2 == 1
            with self.subTest(case=case):
                got = self.s.table("t.csv", data, *args, *(["--format", "csv"] if as_csv else ["--limit", "1000000"]))
                self.assertNotIn(got.status, TRAPS, (args, data, got))
                ragged = any(len(r) != len(names) for r in rows)
                if want[0] == "refuse":
                    refusals += 1
                    self.assertEqual(got.status, 8, (args, data, got))
                    if not as_csv:
                        self.assertEqual(got.first_rule(), want[1], (args, data, got))
                        d = got.error()["detail"]
                        self.assertEqual((d["row"], d["column"], d["context"]), (want[2]["row"], want[2]["column"], want[2]["context"]), (args, data, got))
                    else:
                        self.assertIn(want[1], got.stderr.decode(), (args, data, got))
                    continue
                answers += 1
                kind, labels, wrows = want
                if as_csv:
                    out = [r for r in csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""))]
                    self.assertTrue(out[:1] == [labels] and rows_eq(out[1:], wrows) or not wrows and out[:1] in ([labels], []), (out, [labels] + wrows, args, data))
                    self.assertEqual(got.status, 8 if ragged else 0, (args, data, got))
                else:
                    if got.status != 0:
                        self.assertTrue(ragged, (args, data, got))
                        continue
                    d = got.data()
                    self.assertEqual(d["columns"], labels, (args, data, got))
                    self.assertTrue(rows_eq(d["rows"], wrows), (d["rows"], wrows, args, data))
        self.assertGreater(answers, min_answers)
        self.assertGreater(refusals, min_refusals)

    def test_random_typed_group_plans(self):
        self.check(20261201, 900, make_group_plan, group_args, group_reference, 600, 20)

    def test_random_typed_order_plans(self):
        self.check(20261202, 900, make_order_plan, order_args, order_reference, 600, 20)


def rows_of(got):
    return list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))


class Typed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def t(self, data, *flags):
        return self.s.table("t.csv", data, *flags)

    def test_the_key_is_the_value_one_group_for_1_5_and_1_50_and_the_scale_is_the_columns(self):
        data = "x,n\n1.5,a\n1.50,b\n01.5,c\n+1.5,d\n-0,e\n0.00,f\n.5,g\n0.5,h\n"
        got = self.t(data, "--group", "x:dec(2)", "--format", "csv")
        self.assertEqual(got.stdout, b"x,count\n0.00,2\n0.50,2\n1.50,4\n", got)
        got = self.t(data, "--group", "x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"x,count\n0.0,2\n0.5,2\n1.5,4\n", got)
        got = self.t("x\n1\n01\n+1\n-0\n0\n-5\n", "--group", "x:int", "--format", "csv")
        self.assertEqual(got.stdout, b"x,count\n-5,1\n0,2\n1,3\n", got)
        # untyped is text, as it always was: three spellings are three groups
        got = self.t(data, "--group", "x", "--format", "csv")
        self.assertEqual(len(rows_of(got)) - 1, 8, got)

    def test_the_output_does_not_depend_on_which_cell_came_first_or_the_order_of_the_rows(self):
        cells = ["1.5", "1.50", "01.5", "+1.5", "2.5", "2.50", "-0", "0", "0.0"]
        outs = set()
        rng = random.Random(7)
        for _ in range(12):
            rng.shuffle(cells)
            outs.add(self.t("x\n" + "\n".join(cells) + "\n", "--group", "x:dec(2)", "--format", "csv").stdout)
        self.assertEqual(outs, {b"x,count\n0.00,3\n1.50,4\n2.50,2\n"})

    def test_the_groups_come_in_numeric_order_negatives_first_and_text_stays_bytewise(self):
        data = "x,t\n10,b\n9,b\n-10,b\n-9,a\n100,a\n-0.5,a\n"
        got = self.t(data, "--group", "x:dec(1)", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], ["-10.0", "-9.0", "-0.5", "9.0", "10.0", "100.0"], got)
        got = self.t(data, "--group", "x", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], ["-0.5", "-10", "-9", "10", "100", "9"], got)         # as text: 10 is before 9
        got = self.t(data, "--group", "t,x:float", "--format", "csv")
        self.assertEqual([tuple(r[:2]) for r in rows_of(got)[1:]], [("a", "-9.0"), ("a", "-0.5"), ("a", "100.0"), ("b", "-10.0"), ("b", "9.0"), ("b", "10.0")], got)

    def test_sort_by_a_typed_group_column_and_by_aggregates_beside_it(self):
        data = "x,v\n3,1.5\n-1,2.5\n3,0.5\n10,9\n"
        got = self.t(data, "--group", "x:int", "--agg", "sum:v:float", "--sort", "-x", "--format", "csv")
        self.assertEqual(got.stdout, b"x,sum:v\n10,9.0\n3,2.0\n-1,2.5\n", got)
        got = self.t(data, "--group", "x:int", "--agg", "sum:v:float", "--sort", "sum:v", "--format", "csv")
        self.assertEqual(got.stdout, b"x,sum:v\n3,2.0\n-1,2.5\n10,9.0\n", got)

    def test_distinct_stays_by_value_and_composes_with_a_typed_group(self):
        data = "g,x\na,1.5\na,1.50\nb,2\nb,2.0\nb,3\n"
        got = self.t(data, "--group", "g", "--agg", "distinct:x:dec(2),distinct:x", "--format", "csv")
        self.assertEqual(got.stdout, b"g,distinct:x,distinct:x\na,1,2\nb,2,3\n", got)
        got = self.t(data, "--group", "x:float", "--agg", "distinct:g", "--format", "csv")
        self.assertEqual(got.stdout, b"x,distinct:g\n1.5,1\n2.0,1\n3.0,1\n", got)

    def test_json_and_csv_say_the_same(self):
        data = "x\n1.5\n1.50\n-2\n"
        j = self.t(data, "--group", "x:dec(2)").data()
        self.assertEqual((j["columns"], j["rows"]), (["x", "count"], [["-2.00", "1"], ["1.50", "2"]]))

    # ---- --order-by ---------------------------------------------------------------------------------------------------

    def test_order_by_a_decimal_a_float_and_an_integer_by_value_descending_and_stable(self):
        data = "id,x\na,10\nb,9\nc,-1\nd,9.0\ne,1e1\nf,0.5\ng,-1.0\n"
        got = self.t(data, "--order-by", "x:float", "--select", "id", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], list("cgfbdae"), got)             # 9 and 9.0 tie, and keep their order; 10 and 1e1 too
        got = self.t(data, "--order-by", "-x:float", "--select", "id", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], list("aebdfcg"), got)             # descending: ties still in file order
        got = self.t("id,x\na,10\nb,9\nc,-1\nd,09\n", "--order-by", "x:int", "--select", "id", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], list("cbda"), got)
        got = self.t("id,x\na,10.5\nb,9.25\nc,-1\nd,9.250\n", "--order-by", "x:dec(3),id", "--select", "id", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], list("cbda"), got)

    def test_two_keys_one_typed_one_text_each_its_own_direction(self):
        data = "t,x\nb,1\na,2\nb,2\na,1\na,2\n"
        got = self.t(data, "--order-by", "t,-x:float", "--select", "t,x", "--format", "csv")
        self.assertEqual(rows_of(got)[1:], [["a", "2"], ["a", "2"], ["a", "1"], ["b", "2"], ["b", "1"]], got)
        got = self.t(data, "--order-by", "-t,x:int", "--select", "t,x", "--format", "csv")
        self.assertEqual(rows_of(got)[1:], [["b", "1"], ["b", "2"], ["a", "1"], ["a", "2"], ["a", "2"]], got)

    def test_order_by_with_top_keeps_the_first_rows_and_a_quoted_cell_takes_the_slow_way(self):
        rng = random.Random(9)
        xs = ["%.2f" % rng.uniform(-50, 50) for _ in range(300)]
        quoted = ['"%s"' % x if i % 5 == 0 else x for i, x in enumerate(xs)]
        data = "id,x\n" + "".join("%d,%s\n" % (i, q) for i, q in enumerate(quoted))
        want = [str(i) for i, x in sorted(enumerate(xs), key=lambda p: (float(p[1]), p[0]))]
        for top in (0, 1, 7, 100):
            got = self.t(data, "--order-by", "x:dec(2)", "--select", "id", *(["--top", str(top)] if top else []), "--format", "csv")
            self.assertEqual([r[0] for r in rows_of(got)[1:]], want[:top] if top else want, top)
        got = self.t(data, "--order-by", "-x:float", "--select", "id", "--top", "5", "--format", "csv")
        self.assertEqual([r[0] for r in rows_of(got)[1:]], [str(i) for i, x in sorted(enumerate(xs), key=lambda p: (-float(p[1]), p[0]))][:5])

    # ---- the refusals -------------------------------------------------------------------------------------------------

    def test_a_cell_that_is_not_the_type_is_refused_naming_the_context_the_row_and_the_column(self):
        for flags, rows, rule, ctx, row in (
                (["--group", "x:dec(2)"], ["1", "1.234"], "value.decimal-scale", "group", 2),
                (["--group", "x:float"], ["1", "abc"], "value.not-float", "group", 2),
                (["--group", "x:float"], ["nan"], "value.not-finite", "group", 1),
                (["--group", "x:int"], ["1", ""], "value.not-integer", "group", 2),
                (["--group", "x:int"], ["1", "99999999999999999999"], "value.integer-overflow", "group", 2),
                (["--order-by", "x:dec(2)"], ["1", "", "3"], "value.not-decimal", "order-by", 2),
                (["--order-by", "-x:float"], ["1", "2", "1e999"], "value.float-range", "order-by", 3),
                (["--order-by", "x:float", "--top", "1"], ["1", "2", "inf"], "value.not-finite", "order-by", 3),
                (["--order-by", "x:int"], ["1", "1.5"], "value.not-integer", "order-by", 2)):
            with self.subTest(flags=flags, rows=rows):
                got = self.t("x,n\n" + "".join(r + ",1\n" for r in rows), *flags)
                self.assertEqual((got.status, got.first_rule()), (8, rule), got)
                d = got.error()["detail"]
                self.assertEqual((d["context"], d["column"], d["row"], d["line"]), (ctx, "x", row, row + 1), got)
                self.assertEqual(validate(got), [], got)
        got = self.t("x,n\n1.234,1\n", "--order-by", "x:dec(2)")
        self.assertEqual((got.error()["detail"]["scale"], got.error()["detail"]["digits"]), (2, 3), got)
        got = self.t("x\n1e999\n", "--group", "x:float")
        self.assertEqual(got.error()["detail"]["direction"], "overflow", got)

    def test_the_first_refusal_in_file_order_is_the_key_before_the_aggregate_of_the_same_row(self):
        got = self.t("k,v\na,1\nb,zz\n", "--group", "k:int", "--agg", "sum:v:float")
        self.assertEqual((got.first_rule(), got.error()["detail"]["context"], got.error()["detail"]["row"]), ("value.not-integer", "group", 1), got)
        got = self.t("k,v\n1,2\n2,zz\nq,1\n", "--group", "k:int", "--agg", "sum:v:float")
        self.assertEqual((got.first_rule(), got.error()["detail"]["context"], got.error()["detail"]["row"]), ("value.not-float", "sum", 2), got)

    def test_a_scale_past_18_is_an_argument_error_and_select_takes_no_type(self):
        for flags in (["--group", "x:dec(19)"], ["--order-by", "x:dec(19)"]):
            got = self.t("x\n1\n", *flags)
            self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"), (flags, got))
        got = self.t("x\n1\n", "--select", "x:float")
        self.assertEqual(got.first_rule(), "select.unknown-column", got)

    def test_a_header_that_really_ends_in_a_type_is_escaped(self):
        data = "x:float,x\n3,1\n3.0,2\n4,3\n"
        got = self.t(data, "--group", "x\\:float", "--format", "csv")
        self.assertEqual(got.stdout, b"x:float,count\n3,1\n3.0,1\n4,1\n", got)                    # the column called x:float, as text
        got = self.t(data, "--group", "x\\:float:float", "--format", "csv")
        self.assertEqual(got.stdout, b"x:float,count\n3.0,2\n4.0,1\n", got)                      # ... read as a float
        got = self.t(data, "--order-by", "-x\\:float:float", "--select", "x", "--format", "csv")
        self.assertEqual(rows_of(got)[1:], [["3"], ["1"], ["2"]], got)                          # 4, then the tie of 3 and 3.0 in file order

    # ---- column.type-conflict across every place a column is named -------------------------------------------------------

    def test_one_column_one_type_across_group_where_agg_and_order_by(self):
        data = "x,n\n1,2\n"
        pairs = (["--group", "x:float", "--where", "x:int > 0"], ["--group", "x:dec(2)", "--agg", "sum:x:float"], ["--group", "x:int", "--agg", "min:x:dec(1)"],
                 ["--order-by", "x:float", "--where", "x:dec(2) > 0"], ["--order-by", "x:float,x:int"], ["--group", "x:dec(2)", "--where", "x:dec(3) > 0"])
        for flags in pairs:
            got = self.t(data, *flags)
            self.assertEqual((got.status, got.first_rule()), (2, "column.type-conflict"), (flags, got))
        for flags in (["--group", "x:float", "--where", "x:float > 0", "--agg", "min:x:float"], ["--group", "x:float", "--where", "x = 1"], ["--group", "x:int,n:float", "--agg", "sum:n:float"], ["--order-by", "x:dec(2),x:dec(2)"]):
            got = self.t(data, *flags)
            self.assertEqual(got.status, 0, (flags, got))

    # ---- the limits ---------------------------------------------------------------------------------------------------

    def test_the_group_ceilings_count_a_typed_key_as_its_12_bytes(self):
        n = 40
        data = "x\n" + "".join("%d.5\n" % i for i in range(n))
        got = self.t(data, "--group", "x:float", "--max-groups", str(n), "--max-state-bytes", str(n * 12), "--format", "csv")
        self.assertEqual(got.status, 0, got)
        got = self.t(data, "--group", "x:float", "--max-groups", str(n - 1))
        self.assertEqual(got.first_rule(), "limit.too-many-groups", got)
        got = self.t(data, "--group", "x:float", "--max-state-bytes", str(n * 12 - 1))
        self.assertEqual(got.first_rule(), "limit.state-too-large", got)
        # equal values are one group whatever the text: the same count of keys for any spelling
        data2 = "x\n" + "".join(("%d.5\n" if i % 2 else "%d.50\n") % (i // 2) for i in range(n))
        got = self.t(data2, "--group", "x:float", "--max-groups", str(n // 2), "--format", "csv")
        self.assertEqual(got.status, 0, got)


class Parallel(par.Same):
    def table(self, n, seed):
        rng = random.Random(seed)
        rows = [["k%d" % rng.randint(0, 3), dec_pool(rng), float_pool(rng), int_pool(rng), "%.2f" % rng.uniform(-9, 9)] for _ in range(n)]
        return enc([["k", "d", "f", "i", "v"]] + rows)

    def test_typed_groups_and_orders_are_the_sequential_bytes_for_every_thread_count_and_chunk(self):
        data = self.table(160, 21)
        for args in (["--group", "d:dec(2)", "--agg", "count,sum:v:float,min:v:float"], ["--group", "f:float", "--agg", "count,max:v:float", "--sort", "-count"],
                     ["--group", "i:int,k", "--agg", "sum:v:float,mean:v:float"], ["--group", "k,d:dec(2)", "--agg", "distinct:f:float", "--format", "csv"],
                     ["--order-by", "d:dec(2),-f:float", "--select", "k,d,f", "--format", "csv"], ["--order-by", "-i:int,f:float", "--select", "i,f,v", "--top", "9"],
                     ["--order-by", "f:float", "--select", "f", "--where", "k = k1", "--format", "csv"], ["--group", "f:float", "--max-groups", "1000", "--max-state-bytes", "100000"]):
            self.same(data, args, "typed keys")

    def test_the_first_refusal_in_file_order_wins_with_typed_keys(self):
        rows = [["k%d" % (i % 3), "%d.5" % i, "%d.25" % i, str(i), "1"] for i in range(90)]
        for bad in ({60: (2, "x"), 20: (1, "1.234")}, {40: (2, ""), 70: (3, "1.5")}, {89: (2, "1e999")}, {0: (3, "")}):
            r2 = [list(r) for r in rows]
            for at, (col, cell) in bad.items():
                r2[at][col] = cell
            data = enc([["k", "d", "f", "i", "v"]] + r2)
            for args in (["--group", "d:dec(2),f:float"], ["--group", "i:int"], ["--order-by", "f:float", "--select", "k"], ["--order-by", "-d:dec(2)", "--select", "k", "--top", "3"], ["--order-by", "i:int", "--select", "k", "--format", "csv"]):
                self.same(data, args, "refusals %r" % (bad,))

    def test_quoted_newlines_and_ragged_rows_beside_typed_keys(self):
        rows = [[str(i), "%d.5" % (i % 7), 'multi\nline "%d"' % (i % 3)] for i in range(60)]
        data = enc([["id", "x", "t"]] + rows) + b"61\n" + enc([["62", "2.5", "z"]])
        for args in (["--group", "x:dec(1)", "--agg", "count,distinct:t"], ["--order-by", "-x:float", "--select", "id", "--format", "csv"]):
            self.same(data, args, "ragged and quoted")


if __name__ == "__main__":
    unittest.main()
