"""Slice A1 of docs/or-and-columns.md, the query form: `or` and parentheses in `--query`.

* the grammar and its refusals against the reference front end (query_ref.py, which shares no code with the tool) on generated where clauses and on damaged ones: accepted or refused,
  the kind, the byte offset, the words (G-OFFSETS), and the mix refused as `query.mixed-and-or` at the connective, before anything the rest of the statement says;
* the repair of the mix: two options, each the whole invocation with the flags the corrected query stands for, the two readings of --where being the flag form's, and offered only if the
  rest of the query translates;
* the ceilings: 64 conditions, 1,024 values, 8 levels of parentheses, 4,096 bytes;
* the same plan as flags and as a query gives the same bytes (the equivalence gate of test_query.py draws its plans with `or` and parentheses; here the explain documents too);
* what stays unsupported (`not`, `like`, `between`, `is null`) and what a position outside the where clause says of an `or`.
"""

import json
import os
import random
import unittest

import or_a1 as O
import query_ref
from harness import Scratch, run, validate

SOUPS = int(os.environ.get("OR_A1_QSOUPS", "10000"))
HEAD = "select id from 'q.csv' where "

COLUMNS = ["a", "b", "c", "\"c d\"", "\"or\"", "\"and\"", "#1", "id", "n::int", "cast(n as int)"]
VALUES = {"a": ["'x'", "'y'", "''", "'it''s'", "3", "07"], "n": ["1", "5", "-2", "+3"]}
OPS = ["=", "!=", "<>", "<", "<=", ">", ">=", "=", "="]


def doc_of(r):
    return json.loads(r.stdout.decode("utf-8").splitlines()[0])


def leaf(rng):
    col = rng.choice(COLUMNS)
    vals = VALUES["n"] if "n" in col and "::" in col or "cast(n" in col else VALUES["a"]
    r = rng.random()
    if r < 0.6:
        return "%s %s %s" % (col, rng.choice(OPS), rng.choice(vals))
    if r < 0.8:
        return "%s in (%s)" % (col, ", ".join(rng.choice(vals) for _ in range(rng.randint(1, 3))))
    if r < 0.9 and "::" not in col and "cast" not in col:
        return "%s contains %s" % (col, rng.choice(["'x'", "'a'", "''"]))
    return rng.choice(["%s like 'x%%'" % col, "%s not in (1)" % col, "%s is null" % col, "%s between 1 and 2" % col, "%s = %s" % (col, rng.choice(COLUMNS)), "not %s = 1" % col])


def clause(rng, depth=0):
    n = rng.randint(1, 4)
    parts = []
    for _ in range(n):
        if depth < 3 and rng.random() < 0.25:
            parts.append("(" + clause(rng, depth + 1) + ")")
        else:
            parts.append(leaf(rng))
    out = parts[0]
    for p in parts[1:]:
        out += rng.choice([" and ", " or ", " or ", " AND ", " OR "]) + p
    return out


def damage(rng, q):
    r = rng.random()
    if not q or r < 0.45:
        return q
    i = rng.randrange(1, len(q))
    if r < 0.6:
        return q[:i]
    if r < 0.75:
        return q[:i] + q[i + 1:]
    return q[:i] + rng.choice(["(", ")", "'", "\"", " or ", " and ", ",", ";", "not ", "in ("]) + q[i:]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("q.csv", "id,a,b,c,c d,or,and,n\n1,x,y,z,1,2,3,5\n2,y,x,z,1,2,3,-2\n3,x,,z,1,2,3,7\n4,,,,,,,0\n")
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
        rng = random.Random(20261030)
        queries = [damage(rng, HEAD + clause(rng)) for _ in range(SOUPS)]
        seen = {"ok": 0, "mixed": 0, "syntax": 0, "unsupported": 0, "other": 0}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(self.query, queries))
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
        self.assertGreater(seen["mixed"], SOUPS // 20, seen)
        self.assertGreater(seen["syntax"], SOUPS // 10, seen)
        self.assertGreater(seen["unsupported"], SOUPS // 50, seen)


class Mixed(Base):
    def test_the_repair_is_the_two_readings_as_the_flags_they_stand_for(self):
        rng = random.Random(20261031)
        done = 0
        for _ in range(250):
            q = HEAD + clause(rng)
            try:
                query_ref.translate(q)
                continue
            except query_ref.QueryError as e:
                if e.kind != "mixed":
                    continue
            r = self.query(q)
            d = doc_of(r)
            self.assertEqual(d["error"]["rule"], "query.mixed-and-or", q)
            try:
                argv, source = query_ref.translate_ignoring_mix(q)
            except query_ref.QueryError:
                self.assertEqual(d["error"]["repair"]["kind"], "none", q)
                continue
            where = argv[argv.index("--where") + 1]
            offered = O.offered_readings(where)
            if not offered:
                self.assertEqual(d["error"]["repair"]["kind"], "none", q)
                continue
            got = [o["argv"][o["argv"].index("--where") + 1] for o in d["error"]["repair"]["options"]]
            self.assertEqual(got, offered, q)
            for o in d["error"]["repair"]["options"]:
                again = run(*o["argv"][1:])
                self.assertEqual(again.status in (0, 8), True, (q, o["argv"], again.stdout[:200]))
                self.assertTrue(doc_of(again).get("ok") or doc_of(again)["error"]["rule"].startswith("value."), (q, o["argv"]))
            done += 1
        self.assertGreater(done, 40)

    def test_the_mix_comes_before_what_the_rest_of_the_statement_says(self):
        for q, rule in (("select id from 'q.csv' where a = 'x' or b = 'y' and c = 'z' group by", "query.mixed-and-or"),
                        ("select id, count(*) from 'q.csv' where a = 'x' or b = 'y' and c = 'z'", "query.mixed-and-or"),
                        ("select id from 'q.csv' where a = 'x' or b = 'y' and c = 'z' limit x", "query.mixed-and-or"),
                        ("select id from 'q.csv' where a = 'x' or and", "query.syntax")):
            d = doc_of(self.query(q))
            self.assertEqual(d["error"]["rule"], rule, q)
        # a rest that does not translate: the mix is named, with no repair
        d = doc_of(self.query("select id, count(*) from 'q.csv' where a = 'x' or b = 'y' and c = 'z'"))
        self.assertEqual(d["error"]["repair"]["kind"], "none")
        # offered when the rest is whole
        d = doc_of(self.query("select id from 'q.csv' where a = 'x' or b = 'y' and c = 'z' order by id desc limit 2"))
        self.assertEqual([o["argv"][o["argv"].index("--where") + 1] for o in d["error"]["repair"]["options"]], ["a = 'x' or (b = 'y' and c = 'z')", "(a = 'x' or b = 'y') and c = 'z'"])
        for o in d["error"]["repair"]["options"]:
            self.assertIn("--limit", o["argv"])
            self.assertNotIn("--query", o["argv"])

    def test_query_equals_form_and_a_from_operand(self):
        q = "select id from 'q.csv' where a = 'x' or b = 'y' and c = 'z'"
        r = run("--root", self.dir, "--query=" + q)
        d = doc_of(r)
        self.assertEqual(d["error"]["repair"]["kind"], "choose")
        self.assertTrue(all(o["argv"][-1] == "q.csv" and "--query=" + q not in o["argv"] for o in d["error"]["repair"]["options"]))
        # an operand the call already has makes two operands: no repair
        r = run("--root", self.dir, "--query", q, "q.csv")
        self.assertEqual(doc_of(r)["error"]["repair"]["kind"], "none")


class Ceilings(Base):
    def rule(self, q):
        d = doc_of(self.query(q))
        return "ok" if d.get("ok") else (d["error"]["rule"], d["error"]["detail"].get("offset"), d["error"]["detail"].get("expected"))

    def test_conditions(self):
        chain = lambda n, c: "select id from 'q.csv' where " + (" %s " % c).join("a = %d" % (i % 10) for i in range(n))
        self.assertEqual(self.rule(chain(64, "or")), "ok")
        self.assertEqual(self.rule(chain(64, "and")), "ok")
        r = self.rule(chain(65, "or"))
        self.assertEqual((r[0], r[2]), ("query.syntax", "fewer conditions or values: the ceiling is 64 conditions and 1024 values"))
        # the 65th unit is refused where it begins: the group's own parenthesis, not the condition in it
        base = chain(64, "or")
        self.assertEqual(self.rule(base + " or (a = 1)")[:2], ("query.syntax", len(base) + 4))
        self.assertEqual(self.rule(base + " or a = 1")[:2], ("query.syntax", len(base) + 4))
        self.assertEqual(self.rule("select id from 'q.csv' where (" + " or ".join("a = 1" for _ in range(64)) + ") and a = 1")[0], "query.syntax")
        # the flag form gives the same answer for the same leaves
        flags = self.flags("--select", "id", "--where", " or ".join("a = %d" % (i % 10) for i in range(64)))
        self.assertTrue(doc_of(flags).get("ok"))

    def test_values(self):
        lists = lambda n: "select id from 'q.csv' where a in (%s) or a in (1)" % ",".join(["1"] * n)
        self.assertEqual(self.rule(lists(1024)), "ok")
        self.assertEqual(self.rule(lists(1025))[0], "query.syntax")
        self.assertEqual(self.rule("select id from 'q.csv' where a in (%s) or a in (%s)" % (",".join(["1"] * 1024), ",".join(["2"] * 1024)))[0], "query.too-long")

    def test_depth(self):
        deep = lambda n: "select id from 'q.csv' where " + "(" * n + "a = 'x'" + ")" * n
        self.assertEqual(self.rule(deep(8)), "ok")
        r = self.rule(deep(9))
        self.assertEqual((r[0], r[2]), ("query.syntax", "fewer nested parentheses: the ceiling is 8"))
        self.assertEqual(r[1], len("select id from 'q.csv' where ") + 8)
        self.assertEqual(self.rule("select id from 'q.csv' where a = 'x' or " + "(" * 9 + "a = 'y'" + ")" * 9)[0], "query.syntax")


class Same(Base):
    def test_explain_of_the_query_is_the_explain_of_the_flags(self):
        for where, q in (("a = x or b = y", "a = 'x' or b = 'y'"), ("(a = x or b = y) and n:int > 0", "(a = 'x' or b = 'y') and n::int > 0"), ("a = x or (b = y and c = z)", "a = 'x' or (b = 'y' and c = 'z')"),
                         ("n:int > 100 or a in (x, y) or a = z", "n::int > 100 or a in ('x', 'y') or a = 'z'")):
            f = self.flags("--explain", "--select", "id", "--where", where)
            qr = self.query("select id from 'q.csv' where " + q, "--explain")
            self.assertEqual(f.stdout, qr.stdout)
            self.assertIn(b"if_true", f.stdout)

    def test_a_value_the_engine_refuses_is_mapped_back_to_the_query(self):
        d = doc_of(self.query("select id from 'q.csv' where a = 'x' or (n::int = 1 or n::int = 'zz')"))
        self.assertEqual(d["error"]["rule"], "query.syntax")
        self.assertEqual(d["error"]["detail"]["value"], "'zz'")
        self.assertEqual((d["error"]["detail"]["column"], d["error"]["detail"]["type"]), ("n", "int"))

    def test_every_leaf_is_a_leaf(self):
        a = doc_of(self.query("select id from 'q.csv' where (a = 'x' or b = 'x') and (c = 'z' or n::int > 4)"))
        self.assertEqual([r[0] for r in a["data"]["rows"]], ["1", "2", "3"])

    def test_what_stays_unsupported(self):
        for q, feature in (("a not in (1)", "not"), ("a like 'x'", "like"), ("a between 1 and 2", "between"), ("a is null", "is-null"), ("a = null", "null"), ("a = b", "column-comparison")):
            d = doc_of(self.query("select id from 'q.csv' where " + q))
            self.assertEqual((d["error"]["rule"], d["error"]["detail"]["feature"]), ("query.unsupported", feature), q)
        for q in ("select id from 'q.csv' where not a = 'x'", "select id from 'q.csv' where not (a = 'x')"):
            d = doc_of(self.query(q))
            self.assertEqual(d["error"]["rule"], "query.syntax", q)
        d = doc_of(self.query("select id from 'q.csv' order by id or a"))
        self.assertEqual((d["error"]["rule"], d["error"]["detail"]["expected"]), ("query.syntax", "the end of the query (one statement)"))
        d = doc_of(self.query("select id from 'q.csv' where a = 'x' (b = 'y')"))
        self.assertEqual((d["error"]["rule"], d["error"]["detail"]["offset"]), ("query.syntax", len("select id from 'q.csv' where a = 'x' ")))

    def test_group_by_and_order_by_after_an_or(self):
        d = doc_of(self.query("select a, count(*) from 'q.csv' where a = 'x' or (b = 'x' and c = 'z') group by a order by count(*) desc"))
        self.assertEqual(d["data"]["rows"], [["x", "2"], ["y", "1"]])


if __name__ == "__main__":
    unittest.main()
