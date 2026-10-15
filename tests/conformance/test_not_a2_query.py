"""Slice A2 of docs/or-and-columns.md, the query form: `not COND`, `not (...)`, `x not in (...)` and `x not contains ...` in `--query`. Written before the tool was changed.

* the grammar and its refusals against the reference front end (query_ref.py, which shares no code with the tool) on generated where clauses with `not` in every place and on damaged ones:
  accepted or refused, the kind, the byte offset and the words (G-OFFSETS), the ceilings (64 conditions, 1,024 values, 8 levels of which each `not` that is not followed by a parenthesis
  is one, because the translation writes the parenthesis), the mix refused as `query.mixed-and-or` with `not` in a reading;
* the equivalence gate (G-CORPUS-EQ): the same plan as flags and as a query gives the same bytes, for the translation the reference makes, in json and csv, at one thread and at four with tiny
  ranges, and the explain documents are the same bytes;
* what `not` is not: `not like`, `not between`, `is not null`, `not =`, and what stays unsupported; the repair of `x not like '%y%'`; the columns called "not" and "or".
"""

import csv
import io
import json
import os
import random
import unittest
from concurrent.futures import ThreadPoolExecutor

import not_a2 as N
import or_a1 as O
import query_ref
import test_not_a2 as T
import test_or_a1 as A1
import test_or_a1_query as Q1
from harness import Scratch, run, run_argv, validate

SOUPS = int(os.environ.get("NOT_A2_QSOUPS", "10000"))
PLANS = int(os.environ.get("NOT_A2_QPLANS", "1500"))
HEAD = "select id from 'q.csv' where "
doc_of = Q1.doc_of
COLUMNS = Q1.COLUMNS
VALUES = Q1.VALUES
OPS = Q1.OPS


def leaf(rng, typed_ok=True):
    col = rng.choice(COLUMNS if typed_ok else [c for c in COLUMNS if "::" not in c and "cast" not in c])
    typed = "::" in col or "cast(n" in col
    vals = VALUES["n"] if typed else VALUES["a"]
    r = rng.random()
    if r < 0.45:
        return "%s %s %s" % (col, rng.choice(OPS), rng.choice(vals))
    if r < 0.58:
        return "%s in (%s)" % (col, ", ".join(rng.choice(vals) for _ in range(rng.randint(1, 3))))
    if r < 0.72:
        return "%s not in (%s)" % (col, ", ".join(rng.choice(vals) for _ in range(rng.randint(1, 3))))
    if r < 0.8 and not typed:
        return "%s contains %s" % (col, rng.choice(["'x'", "'a'", "''"]))
    if r < 0.9 and not typed:
        return "%s not contains %s" % (col, rng.choice(["'x'", "'a'", "''"]))
    return rng.choice(["%s like 'x%%'" % col, "%s not like '%%x%%'" % col, "%s is null" % col, "%s between 1 and 2" % col, "%s not between 1 and 2" % col, "%s = %s" % (col, rng.choice(COLUMNS)),
                       "%s not = 1" % col, "%s not" % col, "%s not 5" % col, "%s is not null" % col, "%s::int not contains 'x'" % col])


def unit(rng, depth):
    r = rng.random()
    if r < 0.18:
        return "not " + unit(rng, depth + 1)
    if r < 0.28 and depth < 4:
        return "not (" + clause(rng, depth + 1) + ")"
    if r < 0.4 and depth < 4:
        return "(" + clause(rng, depth + 1) + ")"
    return leaf(rng)


def clause(rng, depth=0):
    n = rng.randint(1, 4)
    parts = [unit(rng, depth) for _ in range(n)]
    out = parts[0]
    glue = rng.choice([" and ", " or "])
    for p in parts[1:]:
        out += (glue if rng.random() < 0.8 else rng.choice([" and ", " or ", " AND ", " OR "])) + p
    return out


def valid_clause(rng, depth=0):
    """A clause the grammar takes, with no mix: every level of one connective."""
    def u(d):
        r = rng.random()
        if r < 0.2:
            return "not " + u(d + 1) if d < 5 else leaf_ok()
        if r < 0.32 and d < 4:
            return "not (" + valid_clause(rng, d + 1) + ")"
        if r < 0.44 and d < 4:
            return "(" + valid_clause(rng, d + 1) + ")"
        return leaf_ok()

    def leaf_ok():
        while True:
            l = leaf(rng)
            if not any(w in l for w in (" like ", " is ", " between ", " not = ", " not 5", "= #", " not\0")) and not l.endswith(" not") and "::int not contains" not in l:
                try:
                    query_ref.translate(HEAD + l)
                    return l
                except query_ref.QueryError:
                    pass

    glue = rng.choice([" and ", " or "])
    return glue.join(u(depth) for _ in range(rng.randint(1, 4)))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(77)
        cls.s = Scratch()
        header = ["id", "a", "b", "c", "c d", "or", "and", "not", "n"]
        rows = [[str(i)] + [rng.choice(["x", "y", "", "it's", "3", "07", "a"]) for _ in range(7)] + [rng.choice(["1", "5", "-2", "3", "0"])] for i in range(1, 41)]
        out = io.StringIO(newline="")
        w = csv.writer(out, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
        cls.csv = out.getvalue()
        cls.s.write("q.csv", cls.csv)
        cls.dir = str(cls.s.dir)

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def query(self, q, *more):
        return run("--root", self.dir, "--query", q, *more)

    def flags(self, *args):
        return run("--root", self.dir, *args, "q.csv")


class Reference(Base):
    def test_the_reference_front_end_and_the_tool_agree_on_generated_where_clauses(self):
        rng = random.Random(20261110)
        queries = [Q1.damage(rng, HEAD + clause(rng)) for _ in range(SOUPS)]
        seen = {"ok": 0, "mixed": 0, "syntax": 0, "unsupported": 0, "other": 0}
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(self.query, queries))
        nots = 0
        for q, r in zip(queries, results):
            self.assertEqual(validate(r), [], (q, r))
            d = doc_of(r)
            try:
                query_ref.translate(q)
                ref = None
            except query_ref.QueryError as e:
                ref = e
            if d.get("ok"):
                seen["ok"] += 1
                nots += " not " in q
                self.assertIsNone(ref, ("the reference refuses what the tool answers", q))
                continue
            e = d["error"]
            if not e["rule"].startswith("query."):
                seen["other"] += 1
                self.assertIsNone(ref, ("the reference refuses what the engine saw", q, e["rule"]))
                continue
            kind = e["rule"][6:]
            kind = "mixed" if kind == "mixed-and-or" else kind
            if kind == "syntax" and "column" in e["detail"]:
                continue            # a literal that does not fit its column: the engine's checks, not the grammar's
            seen[kind] = seen.get(kind, 0) + 1
            self.assertIsNotNone(ref, ("the tool refuses what the reference accepts", q, e["rule"], e["detail"]))
            self.assertEqual((kind, e["detail"]["offset"]), (ref.kind, ref.offset), q)
            self.assertEqual(e["detail"].get("expected", e["detail"].get("feature")), ref.expected if ref.kind not in ("unsupported", "mixed") else ref.feature, q)
        self.assertGreater(seen["ok"], SOUPS // 20, seen)
        self.assertGreater(nots, SOUPS // 40, seen)
        self.assertGreater(seen["mixed"], SOUPS // 40, seen)
        self.assertGreater(seen["syntax"], SOUPS // 10, seen)
        self.assertGreater(seen["unsupported"], SOUPS // 50, seen)

    def test_what_is_and_is_not_a_not(self):
        def rule(q):
            d = doc_of(self.query("select id from 'q.csv' where " + q))
            if d.get("ok"):
                return "ok"
            e = d["error"]
            return (e["rule"], e["detail"]["offset"] - len("select id from 'q.csv' where "), e["detail"].get("expected", e["detail"].get("feature")))

        for q in ("not a = 'x'", "not (a = 'x')", "not not a = 'x'", "not (not (a = 'x'))", "a not in ('x', 'y')", "a not contains 'x'", "b = 'x' and not a = 'y'", "not a = 'x' or not b = 'y'", "not (a = 'x' or b = 'y') and c = 'z'",
                  "n::int not in (1, 2)", "\"not\" not in (1)", "not \"not\" = 1", "NOT a = 'x'", "a NOT IN ('x')", "a Not Contains 'x'"):
            self.assertEqual(rule(q), "ok", q)
        self.assertEqual(rule("not " * 8 + "a = 'x'"), "ok")
        self.assertEqual(rule("not " * 9 + "a = 'x'"), ("query.syntax", 32, "fewer nested parentheses: the ceiling is 8"))
        self.assertEqual(rule("not (" * 8 + "a = 'x'" + ")" * 8), "ok")
        self.assertEqual(rule("not (" * 9 + "a = 'x'" + ")" * 9), ("query.syntax", 5 * 8 + 4, "fewer nested parentheses: the ceiling is 8"))
        self.assertEqual(rule("(" * 4 + "not (" * 4 + "a = 'x'" + ")" * 8), "ok")
        self.assertEqual(rule("(" * 4 + "not (" * 5 + "a = 'x'" + ")" * 9)[0], "query.syntax")
        self.assertEqual(rule("(" * 7 + "not a = 'x'" + ")" * 7), "ok")                       # 7 groups and the parenthesis the translation writes for the not: 8
        self.assertEqual(rule("(" * 8 + "not a = 'x'" + ")" * 8), ("query.syntax", 8, "fewer nested parentheses: the ceiling is 8"))      # the parenthesis of the translation would be the 9th, and it is refused at the not
        # what stays out, each by name
        self.assertEqual(rule("a like 'x'"), ("query.unsupported", 2, "like"))
        self.assertEqual(rule("a not like 'x'"), ("query.unsupported", 6, "like"))
        self.assertEqual(rule("a not ilike 'x'"), ("query.unsupported", 6, "like"))
        self.assertEqual(rule("a not between 1 and 2"), ("query.unsupported", 6, "between"))
        self.assertEqual(rule("a is null"), ("query.unsupported", 2, "is-null"))
        self.assertEqual(rule("a is not null"), ("query.unsupported", 2, "is-null"))
        self.assertEqual(rule("a not = 'x'"), ("query.syntax", 6, "in or contains after not"))
        self.assertEqual(rule("a not"), ("query.syntax", 5, "in or contains after not"))
        self.assertEqual(rule("a::int not contains 'x'"), ("query.syntax", 11, "a typed column is compared with = != < <= > >= or in, not contains"))
        self.assertEqual(rule("not"), ("query.syntax", 3, "a column"))
        self.assertEqual(rule("not not"), ("query.syntax", 7, "a column"))
        self.assertEqual(rule("not = 1"), ("query.syntax", 4, "a column"))
        self.assertEqual(rule("a = 'x' not (b = 'y')"), ("query.syntax", 8, "the end of the query (one statement)"))
        self.assertEqual(rule("not a = b"), ("query.unsupported", 8, "column-comparison"))
        d = doc_of(self.query("select id from 'q.csv' order by not a"))
        self.assertEqual((d["error"]["rule"], d["error"]["detail"]["offset"]), ("query.syntax", len("select id from 'q.csv' order by ")))

    def test_not_like_with_percent_signs_has_the_repair_not_contains(self):
        d = doc_of(self.query("select id from 'q.csv' where a not like '%x%'"))
        self.assertEqual(d["error"]["rule"], "query.unsupported")
        self.assertEqual(d["error"]["repair"]["kind"], "choose")
        argv = d["error"]["repair"]["options"][0]["argv"]
        self.assertEqual(argv[argv.index("--where") + 1], "a not contains x")
        self.assertTrue(doc_of(run_argv(argv)).get("ok"))
        d = doc_of(self.query("select id from 'q.csv' where a not like 'x%'"))
        self.assertEqual(d["error"]["repair"]["kind"], "none")

    def test_the_boundaries_of_the_depth_are_the_references(self):
        # every mix of groups, `not (` and `not` before a condition, around the ceiling of 8: the tool and the reference agree on accepted, offset and words
        for pre, post in (("(", ")"), ("not (", ")"), ("not ", ""), ("(not (", "))"), ("(not ", ")"), ("not (not ", ")")):
            for k in range(3, 11):
                q = HEAD + pre * k + "a = 'x'" + post * k
                d = doc_of(self.query(q))
                try:
                    query_ref.translate(q)
                    ref = ("ok",)
                except query_ref.QueryError as e:
                    ref = (e.kind, e.offset, e.expected)
                got = ("ok",) if d.get("ok") else (d["error"]["rule"][6:], d["error"]["detail"]["offset"], d["error"]["detail"].get("expected"))
                self.assertEqual(got, ref, q)


class Mixed(Base):
    def test_the_two_readings_with_a_not_inside_are_the_flags_they_stand_for(self):
        rng = random.Random(20261111)
        done = 0
        for _ in range(500):
            q = HEAD + clause(rng)
            try:
                query_ref.translate(q)
                continue
            except query_ref.QueryError as e:
                if e.kind != "mixed":
                    continue
            d = doc_of(self.query(q))
            self.assertEqual(d["error"]["rule"], "query.mixed-and-or", q)
            try:
                argv, source = query_ref.translate_ignoring_mix(q)
            except query_ref.QueryError:
                self.assertEqual(d["error"]["repair"]["kind"], "none", q)
                continue
            where = argv[argv.index("--where") + 1]
            offered = N.offered_readings(where)
            if not offered:
                self.assertEqual(d["error"]["repair"]["kind"], "none", q)
                continue
            got = [o["argv"][o["argv"].index("--where") + 1] for o in d["error"]["repair"]["options"]]
            self.assertEqual(got, offered, q)
            for o in d["error"]["repair"]["options"]:
                again = run(*o["argv"][1:])
                self.assertTrue(again.status in (0, 8), (q, o["argv"], again.stdout[:200]))
            done += 1
        self.assertGreater(done, 40)


class Equivalence(Base):
    def test_the_flags_the_reference_translates_to_give_the_bytes_of_the_query(self):
        rng = random.Random(20261112)
        queries = []
        while len(queries) < PLANS:
            q = HEAD + valid_clause(rng)
            tail = rng.choice(["", "", " order by id desc limit 5", " limit 7 offset 2"])
            queries.append(q + tail)
        queries += ["select a, count(*) from 'q.csv' where %s group by a" % valid_clause(rng) for _ in range(PLANS // 5)]
        queries += ["select a, sum(n::int), count(*) from 'q.csv' where %s group by a order by count(*) desc" % valid_clause(rng) for _ in range(PLANS // 10)]
        modes = [[], ["--format", "csv"], ["--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", "0"], ["--threads", "4", "--chunk-bytes", "100", "--parallel-min-bytes", "0", "--format", "csv"]]

        def one(item):
            k, q = item
            extra = modes[k % 4]
            try:
                argv, source = query_ref.translate(q)
            except query_ref.QueryError as e:
                return q, "refused", e, None
            a = self.query(q, *extra)
            b = run("--root", self.dir, *argv, *extra, source[1])
            return q, a, b, argv

        nots = 0
        for q, a, b, argv in pmap_(one, list(enumerate(queries))):
            if a == "refused":
                self.fail("the reference refuses a generated clause: %s %s" % (q, b))
            nots += "not" in " ".join(argv)
            self.assertEqual((a.status, a.stdout, a.stderr), (b.status, b.stdout, b.stderr), q)
        self.assertGreater(nots, PLANS // 2)

    def test_explain_of_the_query_is_the_explain_of_the_flags(self):
        for where, q in (("not (a = x)", "not a = 'x'"), ("not (a = x)", "not (a = 'x')"), ("not (not (a = x))", "not not a = 'x'"), ("a not in (x, y)", "a not in ('x', 'y')"),
                         ("a not contains x", "a not contains 'x'"), ("not (a = x or b = y) and n:int > 0", "not (a = 'x' or b = 'y') and n::int > 0"), ("a = x and not (b = y)", "a = 'x' and not b = 'y'"),
                         ("not (n:int not in (1))", "not n::int not in (1)")):
            f = self.flags("--explain", "--select", "id", "--where", where)
            qr = self.query("select id from 'q.csv' where " + q, "--explain")
            self.assertEqual(f.stdout, qr.stdout, (where, q))
            self.assertIn(b"if_true", f.stdout)

    def test_stdin_and_the_columns_called_not_and_or(self):
        for q, want in (("select id from stdin where \"not\" not in ('x') and not \"or\" = 'y'", None), ("select id from stdin where not a = 'x' and b not contains 'y'", None)):
            a = run("--query", q, stdin=self.csv.encode())
            argv, source = query_ref.translate(q)
            b = run(*argv, stdin=self.csv.encode())
            self.assertEqual((a.status, a.stdout, a.stderr), (b.status, b.stdout, b.stderr), q)
            self.assertTrue(doc_of(a).get("ok"), q)

    def test_a_value_the_engine_refuses_is_mapped_back_to_the_query(self):
        d = doc_of(self.query("select id from 'q.csv' where not (a = 'x' or n::int not in (1, 'zz'))"))
        self.assertEqual(d["error"]["rule"], "query.syntax")
        self.assertEqual(d["error"]["detail"]["value"], "'zz'")
        self.assertEqual((d["error"]["detail"]["column"], d["error"]["detail"]["type"]), ("n", "int"))


def pmap_(fn, items):
    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(fn, items))


if __name__ == "__main__":
    unittest.main()
