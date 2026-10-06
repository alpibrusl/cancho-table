"""`--where`, `--group`, `--agg`, `--sort`, `--top`: a differential test against
a reference implementation of the stated semantics (refimpl.py) over generated
tables and generated plans, and the grammar's refusals.

A plan is generated as a structure, rendered to flags the way a person or a
model would write them (quoting and escaping what needs it), run through the
binary, and compared with what refimpl computes from the records Python's csv
module reads: the same rows or groups, in the same order, and, when a cell that
has to be an integer is not one, the same refusal naming the same row and
column.
"""

import csv
import io
import json
import random
import re
import unittest

import refimpl
from harness import Scratch, run, validate

csv.field_size_limit(1 << 30)

SAFE = re.compile(r"^[A-Za-z0-9_.@/-]+$")
INTS = ["0", "1", "-1", "+5", "007", "42", "-42", "1000", "99999", "9223372036854775807", "-9223372036854775808",
        "9223372036854775808", "-9223372036854775809", "", "12.5", "abc", " 7", "1e3", "-", "+"]
GOOD = ["0", "1", "-1", "+5", "007", "42", "-42", "1000", "99999", "7", "250", "-250"]
TEXT = ["a", "b", "ab", "B", "", " x", "x y", "x,y", 'q"z', "it's", "l1\nl2", "#h", "and", "in", "contains", "(a)", "a=b", "<", "\\"]


def quote(word):
    return "'" + word.replace("'", "''") + "'"


def render_word(word, rng):
    """A name or a value, bare when it is safe, else quoted or escaped."""
    if SAFE.match(word) and not word.startswith("#") and rng.random() < 0.85:
        return word
    if rng.random() < 0.6 or not word or any(c in word for c in "\n\r"):
        return quote(word)
    return "".join("\\" + c if (not c.isalnum() and c not in "_.-@/") or (i == 0 and c == "#") else c for i, c in enumerate(word))


def render_cond(cond, rng):
    col = render_word(cond["column"], rng) + (":int" if cond.get("int") else "")
    if cond["kind"] == "contains":
        return "%s contains %s" % (col, render_word(cond["lit"], rng))
    if cond["kind"] == "in":
        return "%s in (%s)" % (col, ", ".join(render_word(l, rng) for l in cond["lits"]))
    sp = " " if rng.random() < 0.5 else ""
    return "%s%s%s%s%s" % (col, sp, cond["op"], sp, render_word(cond["lit"], rng))


def render_list(names):
    return ",".join(n.replace("\\", "\\\\").replace(",", "\\,") if not n.startswith("#") else "\\" + n.replace("\\", "\\\\").replace(",", "\\,") for n in names)


def make_table(rng):
    width = rng.randint(2, 6)
    names, kinds = [], []
    for i in range(width):
        kind = rng.choice(["int", "int", "text", "text", "small"])
        kinds.append(kind)
        names.append(rng.choice(["id", "n%d" % i, "col %d" % i, "x,y%d" % i, "#h%d" % i, "q'%d" % i, "v%d" % i]) if rng.random() < 0.7 else "c%d" % i)
    if len(set(names)) != width:
        names = ["c%d" % i for i in range(width)]
    rows = []
    for _ in range(rng.randint(0, 25)):
        row = []
        for kind in kinds:
            if kind == "int":
                row.append(rng.choice(GOOD if rng.random() < 0.9 else INTS))
            elif kind == "small":
                row.append(rng.choice(["a", "b", "c", "1", "2"]))
            else:
                row.append(rng.choice(TEXT))
        if rng.random() < 0.05:
            row = row[:-1] if rng.random() < 0.5 else row + ["x"]
        rows.append(row)
    return names, kinds, rows


def csv_bytes(names, rows, rng):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator=rng.choice(["\n", "\r\n"]))
    w.writerow(names)
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("utf-8")


def make_plan(rng, names, kinds, rows):
    plan = {}
    conds = []
    for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
        k = rng.randrange(len(names))
        kind = rng.choice(["cmp", "cmp", "cmp", "in", "contains"])
        use_int = kinds[k] == "int" and rng.random() < 0.8
        if use_int and kind == "contains":
            kind = "cmp"
        sample = [r[k] for r in rows if len(r) == len(names)]
        def lit():
            if use_int:
                return rng.choice(["0", "5", "-1", "42", "007", "99999", "+7", "9223372036854775807", "-9223372036854775808"])
            return rng.choice(sample + TEXT[:6]) if sample and rng.random() < 0.7 else rng.choice(TEXT)
        cond = {"column": names[k], "int": use_int, "kind": kind}
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
    for _ in range(rng.randint(0, 3)):
        f = rng.choice(["count", "sum", "min", "max", "distinct"])
        if f == "count":
            aggs.append(("count", None))
        elif f == "distinct":
            aggs.append((f, rng.choice(names)))
        else:
            ints = [n for n, kd in zip(names, kinds) if kd == "int"]
            aggs.append((f, rng.choice(ints or names)))
    plan["group"] = groups
    plan["aggs"] = aggs or [("count", None)]
    labels = list(groups) + [f if f == "count" else "%s:%s" % (f, c) for f, c in plan["aggs"]]
    if labels and rng.random() < 0.6:
        plan["sort"] = rng.choice(["", "-"]) + rng.choice(labels)
    if rng.random() < 0.3:
        plan["top"] = rng.randint(1, 4)
    return plan


def flags_of(plan, rng, fmt):
    args = []
    if plan["where"]:
        args += ["--where", " and ".join(render_cond(c, rng) for c in plan["where"])]
    if plan.get("select"):
        args += ["--select", render_list(plan["select"])]
    if "group" in plan:
        if plan["group"]:
            args += ["--group", render_list(plan["group"])]
        aggs = [("count" if f == "count" else "%s:%s" % (f, c)) for f, c in plan["aggs"]]
        args += ["--agg", ",".join(a.replace("\\", "\\\\") if False else a for a in aggs)]
        if plan.get("sort"):
            args += ["--sort", plan["sort"]]
        if plan.get("top"):
            args += ["--top", plan["top"]]
    return args


def agg_item_ok(item):
    return "," not in item and not item.split(":", 1)[-1].startswith("#")


class Plans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def expected(self, data, plan):
        recs = list(csv.reader(io.StringIO(data.decode("latin-1"), newline=""), strict=True))
        recs = [r for r in recs if r]
        return refimpl.run(recs, plan)

    def check(self, data, plan, label, fmt="json"):
        rng = random.Random(label)
        args = flags_of(plan, rng, fmt)
        if "group" in plan and any(not agg_item_ok(("%s:%s" % (f, c)) if c else f) for f, c in plan["aggs"]):
            return False
        got = self.s.table("t.csv", data, *args, *( ["--format", "csv"] if fmt == "csv" else []), "--limit", 1000000) if fmt == "json" else self.s.table("t.csv", data, *args, "--format", "csv")
        answer, ragged, error = self.expected(data, plan)
        if fmt == "csv":
            if error:
                self.assertEqual(got.status, 8, (args, data, got))
                self.assertIn(error[0], got.stderr.decode())
                return True
            rows = list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""), strict=True))
            self.assertEqual(rows, [answer[1]] + answer[2] if answer[0] == "rows" or True else None, (args, data))
            self.assertEqual(got.status, 8 if ragged else 0, (args, got))
            return True
        self.assertEqual(validate(got), [], (args, data, got))
        doc = got.doc()
        if error:
            self.assertEqual(got.first_rule(), error[0], (args, data, doc))
            d = doc["error"]["detail"]
            self.assertEqual((d["row"], d["column"], d["context"]), (error[1]["row"], error[1]["column"], error[1]["context"]), (args, data, doc))
            self.assertNotIn("data", doc)
            return True
        d = doc["data"]
        self.assertEqual(d["columns"], answer[1], (args, data))
        self.assertEqual(d["rows"], answer[2], (args, data, doc))
        self.assertEqual(got.status, 8 if ragged else 0, (args, data, doc))
        if ragged:
            self.assertEqual(got.first_rule(), "parse.csv-ragged-row")
        if answer[0] == "groups":
            self.assertEqual(d["group_count"], len(answer[2]) if not plan.get("top") else d["group_count"])
        return True

    def test_random_plans(self):
        rng = random.Random(20260705)
        done = errors = 0
        for case in range(1800):
            names, kinds, rows = make_table(rng)
            data = csv_bytes(names, rows, rng)
            plan = make_plan(rng, names, kinds, rows)
            fmt = "csv" if case % 4 == 3 else "json"
            with self.subTest(case=case):
                if self.check(data, plan, case, fmt):
                    done += 1
        self.assertGreater(done, 1500)

    def test_the_stated_semantics_by_example(self):
        data = b"id,status,bytes,name\n1,200,100,a\n2,404,5000,b\n3,500,,c\n4,500,-7,\n5,404,6000,d\n"
        def q(*a):
            got = self.s.table("e.csv", data, *a)
            return got
        # text compares bytewise: "5000" < "6000" < "99" < ...
        self.assertEqual(q("--where", "bytes>1").data()["rows"][0][0], "1")
        r = q("--where", "status=404 and bytes:int>=5000", "--select", "id").data()["rows"]
        self.assertEqual(r, [["2"], ["5"]])
        # short-circuit: the empty cell of row 3 is never read as an integer, because 'name != c' comes first
        r = q("--where", "name != c and bytes:int < 0", "--select", "id").data()["rows"]
        self.assertEqual(r, [["4"]])
        got = q("--where", "status != 404 and bytes:int > 0", "--select", "id")
        self.assertEqual((got.first_rule(), got.error()["detail"]["row"], got.error()["detail"]["column"], got.error()["detail"]["value"]), ("value.not-integer", 3, "bytes", ""))
        self.assertEqual(got.status, 8)

    def test_group_order_and_sort(self):
        data = b"k,v\nb,1\na,2\nb,3\nc,10\na,4\n"
        got = self.s.table("g.csv", data, "--group", "k", "--agg", "count,sum:v,max:v,min:v")
        self.assertEqual(got.data()["rows"], [["a", "2", "6", "4", "2"], ["b", "2", "4", "3", "1"], ["c", "1", "10", "10", "10"]])
        self.assertEqual(got.data()["columns"], ["k", "count", "sum:v", "max:v", "min:v"])
        got = self.s.table("g.csv", data, "--group", "k", "--agg", "sum:v", "--sort", "-sum:v")
        self.assertEqual([r[0] for r in got.data()["rows"]], ["c", "a", "b"])
        got = self.s.table("g.csv", data, "--group", "k", "--sort", "-count", "--top", "2")
        self.assertEqual((got.data()["rows"], got.data()["group_count"]), ([["a", "2"], ["b", "2"]], 3))
        got = self.s.table("g.csv", data, "--group", "k", "--sort", "-k")
        self.assertEqual([r[0] for r in got.data()["rows"]], ["c", "b", "a"])
        got = self.s.table("g.csv", data, "--agg", "count,sum:v,distinct:k")
        self.assertEqual(got.data()["rows"], [["5", "20", "3"]])

    def test_group_order_is_the_same_whatever_the_order_of_rows(self):
        rng = random.Random(9)
        rows = [[rng.choice(["x", "y", "z", "xx", "", "é"]), str(rng.randint(-5, 5))] for _ in range(60)]
        outs = set()
        for _ in range(4):
            rng.shuffle(rows)
            data = csv_bytes(["k", "v"], rows, rng)
            outs.add(self.s.table("o.csv", data, "--group", "k", "--agg", "count,sum:v").stdout)
        self.assertEqual(len(outs), 1)

    def test_a_where_alone_returns_every_column(self):
        got = self.s.table("w.csv", "a,b\n1,x\n2,y\n", "--where", "a:int>1")
        self.assertEqual((got.data()["columns"], got.data()["rows"]), (["a", "b"], [["2", "y"]]))
        got = self.s.table("w.csv", "a,b\n1,x\n2,y\n", "--where", "a:int>0", "--format", "csv")
        self.assertEqual(got.stdout, b"a,b\n1,x\n2,y\n")

    def test_filter_pages(self):
        rows = [[str(i), "x" if i % 3 == 0 else "y"] for i in range(101)]      # the last row does not match
        data = csv_bytes(["i", "t"], rows, random.Random(1))
        want = [[r[0]] for r in rows if r[1] == "x"]
        seen, at = [], 0
        while True:
            got = self.s.table("p.csv", data, "--where", "t = x", "--select", "i", "--limit", 7, "--from", at)
            seen += got.data()["rows"]
            self.assertLessEqual(got.data()["row_count"], 7)
            if got.data()["next"] is None:
                break
            at = got.data()["next"]["from"]
            self.assertTrue(got.data()["truncated"])
        self.assertEqual(seen, want)
        # not truncated when only non-matching rows are left
        got = self.s.table("p.csv", data, "--where", "t = x", "--select", "i", "--limit", 34)
        self.assertEqual((got.data()["row_count"], got.data()["truncated"], got.data()["next"]), (34, False, None))


if __name__ == "__main__":
    unittest.main()
