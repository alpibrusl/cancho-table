"""J3b (docs/joins.md sections 2.3, 11, 19 and 20, slice J3b): the join in `--query`.

    select a.id, b.city from 'p.csv' as a [inner|left|semi|anti] join 'b.csv' as b on a.id = b.id [and b.n > 1] where ... group by ... order by ... limit N offset M

The gates, written before the code and each able to fail (on `main`'s binary, whose `--query` has no join, every class below that runs the tool on a join query fails):

* Q-EQ, the equivalence: for every join plan the tests of J1 to J3 draw (join_ref3.py: four join types, text and typed keys, one and two pairs, `--join-where`, `--where` with or, not and parentheses,
  select lists, groupings, sorts, orders, pages, renames) that the query form can say, the plan is written as flags and as a query (query_join_gen.py): the same bytes, status and refusals
  (but for the `repair`, which offers the invocation, and for the class `where.syntax` / `query.syntax` of a literal that does not fit its column), as csv, json and json lines, from a file and
  from standard input, at 1 and at 4 threads; and the same `--explain` document.
* O, the oracle: the very text of a query is run by SQLite on the same tables (a semi and an anti join as `exists` and `not exists`): the same rows, in the same order where the order by names
  every column of the answer.
* Q-REF, the pinned cases: every refusal of the grammar of a join, at its byte offset, by the reference front end (query_ref.py) and by the tool; every translation, as the flags it stands for.
* The compatibility: every query that was valid before gives main's bytes (the golden of main, recorded by query_join_golden.py), and the reference front end reads every other query as it did.
* Fuzz: damaged and random join queries: no trap, every refusal tagged, every repair offered accepted.
"""

import csv
import io
import json
import os
import random
import shutil
import sys
import unittest

import join_ref as J
import join_ref3 as R3
import query_cases
import query_join_cases as C
import query_join_gen as G
import query_join_oracle as O
import query_ref
from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate

CASES = int(os.environ.get("TABLE_J3B_CASES", "3"))
FUZZ = int(os.environ.get("TABLE_J3B_FUZZ", "1200"))
JTYPES = ("inner", "left", "semi", "anti")
BIG = ["--limit", "1000000", "--max-bytes", "67108864"]
THREADS = ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7"]


def doc(result):
    try:
        d = json.loads(result.stdout.decode("utf-8"))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def rule_of(result):
    d = doc(result)
    if d and not d.get("ok", True):
        return d["error"]["rule"]
    if result.stderr.startswith(b"table: "):
        return result.stderr.decode("utf-8", "replace").split(":")[1].strip()
    return None


def comparable(result):
    """(status, stdout, stderr) as the equivalence gate compares them: a refusal without its `repair`, and a literal that does not fit its column as one class."""
    out, err = result.stdout, result.stderr
    d = doc(result)
    if d is not None and d.get("ok") is False:
        if d["error"]["rule"] in ("where.syntax", "query.syntax"):
            return result.status, b"SYNTAX", b""
        for e in [d["error"]] + d.get("errors", []):
            e.pop("repair", None)
        out = (json.dumps(d, sort_keys=True) + "\n").encode()
    if err.startswith((b"table: where.syntax:", b"table: query.syntax:")):
        return result.status, out, b"SYNTAX"
    return result.status, out, err


def write_case(d, case):
    ph, pr = case.P
    bh, br = case.B
    J.write_case(str(d), [x.encode() for x in ph], [R3.to_bytes(r) for r in pr], [x.encode() for x in bh], [R3.to_bytes(r) for r in br], {})


def fixtures(s):
    for name, text in C.FIXTURES.items():
        s.write(name, text)


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)
        fixtures(self.s)
        self.cwd = self.s.dir

    def q(self, query, *flags, stdin=None):
        return run("--query", query, *flags, cwd=self.cwd, stdin=stdin)

    def flags(self, *flags, stdin=None):
        return run(*flags, cwd=self.cwd, stdin=stdin)


# ---- Q-REF: the pinned cases ------------------------------------------------------------------------------------------------------------------

class Reference(unittest.TestCase):
    """The reference front end against the pinned cases: no tool in it (it passes on `main`), and it is the other half of every comparison below."""

    def test_every_pinned_refusal_by_the_reference(self):
        for query, kind, anchor, what in C.REFUSALS:
            with self.subTest(query=query):
                at = C.offset_of(query, anchor)
                with self.assertRaises(query_ref.QueryError) as cm:
                    query_ref.translate(query)
                e = cm.exception
                self.assertEqual((e.kind, e.offset, e.expected if e.kind in ("syntax", "group-mismatch") else e.feature), (kind, at, what))

    def test_every_pinned_translation_by_the_reference(self):
        for query, flags, source in C.ACCEPTED + C.ODD:
            with self.subTest(query=query):
                self.assertEqual(query_ref.translate(query), (flags, source))

    def test_the_old_grammar_is_read_as_it_was(self):
        # every pinned refusal and every accepted query of the grammar without a join: the same answer as before the join (the three that named the feature `join` keep their offset)
        for row in query_cases.ALL_REFUSALS:
            query, kind, offset = row[0], row[1], row[2]
            if " join " in query or query.endswith("inner"):
                continue
            with self.subTest(query=query):
                with self.assertRaises(query_ref.QueryError) as cm:
                    query_ref.translate(query)
                self.assertEqual((cm.exception.kind, cm.exception.offset), (kind, offset))

    def test_a_dot_is_read_only_where_a_join_is(self):
        for query in ("select a.b from 'f'", "select a from 'f' where b = 'x' and c.d = 1", "select a. from 'f'"):
            with self.subTest(query=query):
                with self.assertRaises(query_ref.QueryError) as cm:
                    query_ref.translate(query)
                self.assertEqual((cm.exception.kind, cm.exception.expected), ("syntax", C.WHAT_BYTE))


class Pinned(Base):
    def test_every_pinned_refusal(self):
        for query, kind, anchor, what in C.REFUSALS:
            at = C.offset_of(query, anchor)
            with self.subTest(query=query):
                r = self.q(query)
                self.assertEqual(r.status, 2, r)
                self.assertEqual(validate(r), [], r)
                e = doc(r)["error"]
                self.assertEqual(e["rule"], "query.mixed-and-or" if kind == "mixed" else "query." + kind, r)
                detail = e["detail"]
                self.assertEqual(detail["offset"], at, r)
                self.assertEqual(detail["query"], query)
                if kind == "unsupported":
                    self.assertEqual(detail["feature"], what)
                    self.assertTrue(e["hint"])
                elif kind == "mixed":
                    self.assertNotIn("expected", detail)
                else:
                    self.assertEqual(detail["expected"], what)
                self.assertIn(e["repair"]["kind"], ("choose", "none"))
                if e["repair"]["kind"] == "none":
                    self.assertTrue(e["repair"]["reason"])

    def test_a_mix_inside_the_on_is_told_without_the_two_readings(self):
        # the two readings of a where clause are offered; a mix in the on is not a where clause, and nothing is offered for it
        for q in ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and (b.city = 'x' and b.n = 1 or b.n = 2)",
                  "select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and (b.city = 'x' and b.n = 1 or b.n = 2) where a.v = '10' and a.name = 'x'"):
            r = self.q(q)
            self.assertEqual(rule_of(r), "query.mixed-and-or", r)
            repair = doc(r)["error"]["repair"]
            self.assertEqual(repair["kind"], "none", q)
            self.assertTrue(repair["reason"].startswith("which of the two readings was meant is not known"), repair)
        r = self.q("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where a.v = 1 and a.id = 2 or a.name = 'x'")
        self.assertEqual(doc(r)["error"]["repair"]["kind"], "choose")

    def test_every_pinned_translation_runs_as_its_flags(self):
        for query, flags, source in C.ACCEPTED + C.ODD:
            with self.subTest(query=query):
                operand = [source[1]] if source and source[0] == "file" else []
                stdin = C.FIXTURES["p.csv"].encode() if source and source[0] == "stdin" else None
                for fmt in ("csv", "json"):
                    a = self.flags(*flags, "--format", fmt, *operand, stdin=stdin)
                    b = self.q(query, "--format", fmt, stdin=stdin)
                    self.assertEqual(a.status, 0, a)
                    self.assertEqual(comparable(a), comparable(b), (query, flags))

    def test_a_literal_that_does_not_fit_its_column_is_told_at_the_literal(self):
        for query, value, column, type_, expected in C.LITERALS:
            with self.subTest(query=query):
                r = self.q(query)
                self.assertEqual((r.status, rule_of(r)), (2, "query.syntax"), r)
                self.assertEqual(validate(r), [], r)
                detail = doc(r)["error"]["detail"]
                self.assertEqual(detail, {"offset": query.rindex(value), "expected": expected, "query": query, "column": column, "type": type_, "value": value})
                self.assertEqual(doc(r)["error"]["repair"]["kind"], "none")

    def test_the_odd_names_run_to_their_columns(self):
        r = self.q("select a.\"my col\", b.\"a=b\", b.\"e\\f\" from 'o1.csv' as a join 'o2.csv' as b on a.\"k:1\" = b.\"k=2\"", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (0, b"my col,b.a=b,b.e\\f\nA,R,S\nB,W,X\n"))
        r = self.q("select a.\"#1\", a.\"-d\", b.d from 'o1.csv' as a left join 'o2.csv' as b on a.\"k:1\" = b.\"k=2\" order by a.\"-d\" desc", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (0, b"#1,-d,b.d\nt,u,\nr,s,Y\np,q,T\n"))

    def test_the_answers_a_person_would_expect(self):
        r = self.q("select a.name, b.city from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "--format", "csv")
        self.assertEqual(r.stdout, b"name,b.city\nann,x\nbob,y\nbob,yy\nbo2,y\nbo2,yy\n")
        r = self.q("select a.name from 'p.csv' as a anti join 'b.csv' as b on a.id = b.id", "--format", "csv")
        self.assertEqual(r.stdout, b"name\ncy\neve\n")
        r = self.q("select a.name, b.city from 'p.csv' as a left join 'b.csv' as b on a.id = b.id and b.n > 150 order by a.name", "--format", "csv")
        self.assertEqual(r.stdout, b"name,b.city\nann,\nbo2,y\nbo2,yy\nbob,y\nbob,yy\ncy,\neve,\n")
        r = self.q("select a.id from stdin as a semi join 'b.csv' as b on a.id = b.id", "--format", "csv", stdin=C.FIXTURES["p.csv"].encode())
        self.assertEqual(r.stdout, b"id\n1\n2\n2\n")
        r = self.q("select a.name, count(*) as n, sum(b.n) from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by a.name order by n desc", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (0, b"name,n,sum:b.n\nbo2,2,500\nbob,2,500\nann,1,100\n"))


class Refusals(Base):
    """What the run says of a join is the same for the query and for the flags it stands for: the refusals of the build file, of the keys, of the bounds, of the names."""

    RUNS = [
        ("select a.id from 'p.csv' as a join '-' as b on a.id = b.id", []),
        ("select a.id from 'p.csv' as a join 'nope.csv' as b on a.id = b.id", []),
        ("select a.id from 'p.csv' as a join '../x.csv' as b on a.id = b.id", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.nope = b.id", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.nope", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.name::int = b.city::int", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id::int = b.id::int", []),
        ("select a.id from 'p.csv' as a left join 'b.csv' as b on a.id = b.id where b.n::int > 100", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.nope = 'x'", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.city::int = 1", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", ["--max-state-bytes", "10"]),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", ["--max-join-rows", "1"]),
        ("select a.id, count(*) from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by a.id", ["--max-state-bytes", "300"]),
        ("select a.id from 'pc.csv' as a join 'b.csv' as b on a.id = b.id", []),
        ("select a.id from 'pc.csv' as a semi join 'b.csv' as b on a.id = b.id", []),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", ["--max-rows", "2"]),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", ["--delimiter", "tab"]),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", ["--input-format", "jsonl"]),
        ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", ["--max-join-rows", "0"]),
    ]

    def test_the_same_refusal_in_both_forms(self):
        for query, extra in self.RUNS:
            flags, source = query_ref.translate(query)
            operand = [source[1]] if source and source[0] == "file" else []
            for fmt in ("json", "csv"):
                with self.subTest(query=query, extra=extra, fmt=fmt):
                    a = self.flags(*flags, *extra, "--format", fmt, *operand)
                    b = self.q(query, *extra, "--format", fmt)
                    self.assertEqual(comparable(a), comparable(b), (flags, a.stdout[:300], b.stdout[:300]))

    def test_some_of_them_are_refusals(self):
        rules = set()
        for query, extra in self.RUNS:
            r = self.q(query, *extra)
            rules.add(rule_of(r))
        for rule in ("args.bad-value", "column.unknown", "value.not-integer", "limit.state-too-large", "limit.too-many-join-rows", "join.column-collision", "limit.too-many-rows", "input.fields-required"):
            self.assertIn(rule, rules, sorted(r for r in rules if r))

    def test_the_explain_of_every_pinned_translation_is_the_explain_of_its_flags(self):
        for query, flags, source in C.ACCEPTED + C.ODD:
            with self.subTest(query=query):
                operand = [source[1]] if source and source[0] == "file" else []
                stdin = C.FIXTURES["p.csv"].encode() if source and source[0] == "stdin" else None
                a = self.flags(*flags, "--explain", *operand, stdin=stdin)
                b = self.q(query, "--explain", stdin=stdin)
                self.assertEqual(a.status, 0, a)
                self.assertEqual(comparable(a), comparable(b))

    def test_the_flags_that_say_the_join_too_are_another_plan(self):
        q = "select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id"
        for flag in (["--join", "b.csv"], ["--on", "id=id"], ["--join-type", "left"], ["--join-prefix", "x_"], ["--join-where", "n > 1"]):
            with self.subTest(flag=flag):
                r = self.q(q, *flag)
                self.assertEqual((r.status, rule_of(r)), (2, "args.conflict"), r)
                self.assertIn(flag[0], doc(r)["error"]["detail"]["flags"])
        r = self.q(q, "--max-join-rows", "1")
        self.assertEqual(rule_of(r), "limit.too-many-join-rows")
        r = self.q("select id from 'p.csv'", "--max-join-rows", "1")
        self.assertEqual(rule_of(r), "args.required-flag")

    def test_an_offered_repair_of_a_join_query_is_accepted(self):
        q = "select a.id from 'p.csv' as a left join 'b.csv' as b on a.id = b.id and b.n > 100 where a.v = '10' or a.v = '20' and b.city = 'x'"
        r = self.q(q)
        self.assertEqual(rule_of(r), "query.mixed-and-or")
        e = doc(r)["error"]
        self.assertEqual(e["repair"]["kind"], "choose", e)
        self.assertEqual(len(e["repair"]["options"]), 2)
        answers = []
        for option in e["repair"]["options"]:
            again = run(*option["argv"][1:], "--format", "csv", cwd=self.cwd)
            self.assertEqual(again.status, 0, (option, again))
            answers.append(again.stdout)
        # the two readings are two questions: by precedence (a or (b and c)) and left to right ((a or b) and c)
        by_precedence = self.q("select a.id from 'p.csv' as a left join 'b.csv' as b on a.id = b.id and b.n > 100 where a.v = '10' or (a.v = '20' and b.city = 'x')", "--format", "csv")
        left_to_right = self.q("select a.id from 'p.csv' as a left join 'b.csv' as b on a.id = b.id and b.n > 100 where (a.v = '10' or a.v = '20') and b.city = 'x'", "--format", "csv")
        self.assertEqual(sorted(answers), sorted([by_precedence.stdout, left_to_right.stdout]))
        # a refusal that has a correction (like -> contains) offers the whole join again
        r = self.q("select a.id from 'p.csv' as a semi join 'b.csv' as b on a.id = b.id and b.city = 'y' where a.name like '%o%'")
        e = doc(r)["error"]
        self.assertEqual((e["rule"], e["repair"]["kind"]), ("query.unsupported", "choose"), e)
        again = run(*e["repair"]["options"][0]["argv"][1:], "--format", "csv", cwd=self.cwd)
        self.assertEqual(again.status, 0, again)
        self.assertEqual(again.stdout, self.q("select a.id from 'p.csv' as a semi join 'b.csv' as b on a.id = b.id and b.city = 'y' where a.name contains 'o'", "--format", "csv").stdout)


# ---- Q-EQ -------------------------------------------------------------------------------------------------------------------------------------

class Equivalence(unittest.TestCase):
    """Q-EQ: a plan as flags and as a query give the same bytes."""

    def pair(self, d, case, plan, fmt, probe_from_stdin, extra, rng):
        ph, bh = case.P[0], case.B[0]
        flags = ["--root", str(d), *R3.join_flags(plan, ph, bh), *R3.flags_of(plan, ph, bh, fmt)]
        text, top = G.query_text(plan, ph, bh, rng, None if probe_from_stdin else "p.csv")
        query = ["--root", str(d), "--query", text, "--format", fmt]
        if top:
            query += ["--top", str(top)]
        if plan.get("limit") is None and fmt == "json":
            flags += BIG
            query += BIG
        elif fmt == "json" and "--max-bytes" not in flags:
            flags += ["--max-bytes", "67108864"]
            query += ["--max-bytes", "67108864"]
        stdin = (d / "p.csv").read_bytes() if probe_from_stdin else None
        a = run(*flags, *extra, *([] if probe_from_stdin else ["p.csv"]), stdin=stdin)
        b = run(*query, *extra, stdin=stdin)
        return a, b, text, flags

    def test_every_shape_every_join_type(self):
        rng = random.Random(20261016)
        cases = [G.with_prefix(c) for c in R3.shapes()]
        seen = {"runs": 0, "refusals": 0, "ok": 0}
        for case in cases:
            s = Scratch()
            try:
                write_case(s.dir, case)
                for jt in JTYPES:
                    for i in range(CASES):
                        c, plan = G.draw(rng, [case], jt)
                        for fmt, stdin, extra in (("csv", False, []), ("json", False, []), ("jsonl", False, []), ("csv", True, []), ("json", True, THREADS), ("csv", False, THREADS)):
                            a, b, text, flags = self.pair(s.dir, c, plan, fmt, stdin, extra, rng)
                            seen["runs"] += 1
                            seen["ok" if a.status == 0 else "refusals"] += 1
                            self.assertEqual(comparable(a), comparable(b), "%s [%s] %s\n  query: %s\n  flags: %r\n  flags -> %r\n  query -> %r" % (case.name, jt, fmt, text, flags, a.stdout[:300] + a.stderr[:200], b.stdout[:300] + b.stderr[:200]))
            finally:
                s.cleanup()
        if os.environ.get("TABLE_J3B_REPORT"):
            print("\nJ3b Q-EQ: %d pairs of runs over %d plans, %d answers and %d refusals" % (seen["runs"], seen["runs"] // 6, seen["ok"], seen["refusals"]))
        # a gate that never ran, or that never saw a refusal or an answer, has gated nothing
        self.assertGreater(seen["runs"], 6 * 4 * CASES * 10)
        self.assertGreater(seen["ok"], seen["runs"] // 2, seen)
        self.assertGreater(seen["refusals"], 5, seen)

    def test_the_shapes_of_j2_and_random_keys(self):
        """The shapes of the J2 tests (text and typed keys in every spelling, composite keys, one-column files, empty sides, CRLF and a BOM, quoted keys, bytes that are not UTF-8, eight pairs) and random pairs of
        tables: `select *` of every join type, as the flags and as the query."""
        import join_ref2 as R2
        rng = random.Random(31)
        cases = R2.shapes() + [R2.random_shape(rng) for _ in range(12)]
        seen = {"pairs": 0, "ok": 0, "refusals": 0}
        for name, ph, pr, bh, br, pairs, opts in cases:
            s = Scratch()
            try:
                J.write_case(str(s.dir), ph, pr, bh, br, opts)
                plain = [(pk, bk, kind, False) for pk, bk, kind, _using in pairs]
                on_flag = R2.on_arg(plain, ph, bh)
                eqs = []
                for pk, bk, kind, _using in plain:
                    ty = "::" + R2.suffix_of(kind)[1:] if kind else ""
                    eqs.append("a.%s%s = b.%s%s" % (G.ident(ph[pk].decode("latin-1")), ty, G.ident(bh[bk].decode("latin-1")), ty))
                for jt in JTYPES:
                    flags = ["--root", str(s.dir), "--join", "b.csv", "--on", on_flag, "--join-type", jt] + (["--join-prefix", "b."] if jt in ("inner", "left") else [])
                    text = "select * from 'p.csv' as a %s join 'b.csv' as b on %s" % (jt, " and ".join(eqs))
                    for fmt in ("csv", "json"):
                        a = run(*flags, "--format", fmt, "p.csv")
                        b = run("--root", str(s.dir), "--query", text, "--format", fmt)
                        seen["pairs"] += 1
                        seen["ok" if a.status == 0 else "refusals"] += 1
                        self.assertEqual(comparable(a), comparable(b), "%s [%s] %s\n  query: %s\n  flags: %r\n  flags -> %r\n  query -> %r" % (name, jt, fmt, text, flags, a.stdout[:300] + a.stderr[:200], b.stdout[:300] + b.stderr[:200]))
            finally:
                s.cleanup()
        self.assertGreater(seen["pairs"], 300)
        self.assertGreater(seen["ok"], 100, seen)
        if os.environ.get("TABLE_J3B_REPORT"):
            print("\nJ3b Q-EQ over the shapes of J2: %d pairs of runs over %d table pairs, %d answers and %d refusals" % (seen["pairs"], len(cases), seen["ok"], seen["refusals"]))

    def test_the_explain_documents_are_the_same(self):
        rng = random.Random(77)
        cases = [G.with_prefix(c) for c in R3.shapes()]
        for case in cases[:8]:
            s = Scratch()
            try:
                write_case(s.dir, case)
                for jt in JTYPES:
                    c, plan = G.draw(rng, [case], jt)
                    a, b, text, flags = self.pair(s.dir, c, plan, "json", False, ["--explain"], rng)
                    self.assertEqual(a.status, 0, a)
                    with self.subTest(query=text):
                        self.assertEqual(comparable(a), comparable(b))
                        self.assertEqual(doc(a)["data"]["explain"]["join"]["prefix"], "b." if jt in ("inner", "left") else "")
            finally:
                s.cleanup()

    def test_the_threads_and_the_sources(self):
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        q = "select a.name, b.city from 'p.csv' as a left join 'b.csv' as b on a.id = b.id order by a.name, b.city"
        base = run("--query", q, "--format", "csv", "--root", s.dir)
        self.assertEqual(base.status, 0, base)
        for extra in (THREADS, ["--threads", "64", "--chunk-bytes", "1", "--parallel-min-bytes", "0"]):
            self.assertEqual(run("--query", q, "--format", "csv", "--root", s.dir, *extra).stdout, base.stdout)
        piped = run("--query", q.replace("'p.csv'", "stdin"), "--format", "csv", "--root", s.dir, stdin=C.FIXTURES["p.csv"].encode())
        self.assertEqual(piped.stdout, base.stdout)


# ---- O: SQLite ---------------------------------------------------------------------------------------------------------------------------------

class Oracle(unittest.TestCase):
    def run_one(self, d, db, spec):
        sql_rows = O.answer(db, spec["sql"])
        r = run("--root", str(d), "--query", spec["query"], "--format", "csv")
        self.assertEqual(r.status, 0, "%s\n%s\n%r" % (spec["query"], spec["sql"], r.stdout[:300] + r.stderr[:300]))
        rows = list(csv.reader(io.StringIO(r.stdout.decode("utf-8"), newline="")))[1:]
        if spec["total"]:
            self.assertEqual(rows, sql_rows, "%s\n%s" % (spec["query"], spec["sql"]))
        else:
            self.assertEqual(sorted(rows), sorted(sql_rows), spec["query"])
        return len(rows)

    def test_the_tool_and_sqlite_agree(self):
        rng = random.Random(5)
        seen = {"specs": 0, "rows": 0, "empty": 0}
        for case in [G.with_prefix(c) for c in R3.shapes(seed=3)]:
            if not O.oracle_ok(case):
                continue
            s = Scratch()
            try:
                write_case(s.dir, case)
                db = O.load(case)
                for jt in JTYPES:
                    for spec in O.specs(rng, case, jt, 5 * CASES):
                        with self.subTest(case=case.name, jt=jt, query=spec["query"]):
                            n = self.run_one(s.dir, db, spec)
                            seen["specs"] += 1
                            seen["rows"] += n
                            seen["empty"] += n == 0
            finally:
                s.cleanup()
        if os.environ.get("TABLE_J3B_REPORT"):
            print("\nJ3b oracle: %d statements run by the tool and by SQLite, %d rows compared, %d with no row" % (seen["specs"], seen["rows"], seen["empty"]))
        self.assertGreater(seen["specs"], 100, seen)
        self.assertGreater(seen["rows"], 300, seen)
        self.assertLess(seen["empty"], seen["specs"] * 0.6, seen)


class Strace(unittest.TestCase):
    """On Linux, a join query, from a file and from a pipe, starts no thread, opens no socket and opens no file for writing (the control: a threaded --select of the same file does)."""

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "strace is Linux only")
    def test_no_thread_no_socket_no_write(self):
        import procs as subprocess
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        threaded = ["--threads", "4", "--chunk-bytes", "7", "--parallel-min-bytes", "0", "--format", "csv"]
        for query, stdin in (("select a.name, count(*) from 'p.csv' as a left join 'b.csv' as b on a.id = b.id group by a.name", None),
                             ("select a.name from stdin as a anti join 'b.csv' as b on a.id = b.id", C.FIXTURES["p.csv"].encode())):
            p = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3,socket,openat", binary(), "--root", str(s.dir), "--query", query, *threaded], capture_output=True, input=stdin or b"")
            err = p.stderr.decode("latin-1")
            self.assertEqual(p.returncode, 0, err[-300:])
            self.assertIn("b.csv", err, "the control: strace saw the build file opened")
            self.assertNotIn("CLONE_THREAD", err)
            self.assertNotIn("socket(", err)
            self.assertNotRegex(err, r"O_WRONLY|O_RDWR|O_CREAT")
        ctl = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3", binary(), "--root", str(s.dir), "--select", "id", *threaded, "p.csv"], capture_output=True, input=b"")
        self.assertIn("CLONE_THREAD", ctl.stderr.decode("latin-1"))


# ---- the compatibility --------------------------------------------------------------------------------------------------------------------

class Compat(unittest.TestCase):
    def test_what_was_valid_gives_the_bytes_of_main(self):
        import query_join_golden as K
        golden = json.loads(K.GOLDEN.read_text())
        want = golden["cases"]
        got = K.compute(binary(), golden["random"])
        bad = sorted(k for k in want if want[k] != got.get(k))
        self.assertEqual(bad, [], "answers that are not main's (query_join_main_golden.json)")
        self.assertGreater(len(want), 350)

    @unittest.skipUnless(os.environ.get("J3B_BASE"), "J3B_BASE names the directory of main's build")
    def test_against_the_binary_of_main(self):
        import query_join_golden as K
        randoms = json.loads(K.GOLDEN.read_text())["random"]
        base = K.compute(os.path.join(os.environ["J3B_BASE"], "table"), randoms)
        here = K.compute(binary(), randoms)
        self.assertEqual(sorted(k for k in base if base[k] != here[k]), [])

    def test_the_hints_of_the_two_features_that_said_there_were_no_joins_say_what_there_is(self):
        # the only answers of a refusal that are not main's bytes (test_or_a1_compat.hint_moved holds the status and the rule of the rest)
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        r = run("--query", "select a from 'f' join 'g' on a = a", cwd=s.dir)
        e = doc(r)["error"]
        self.assertEqual((e["rule"], e["detail"]["feature"], e["detail"]["offset"]), ("query.unsupported", "join", 18))
        self.assertEqual(e["hint"], "a join names both tables and their key: from 'a.csv' as a join 'b.csv' as b on a.k = b.k")
        r = run("--query", "select a from select", cwd=s.dir)
        e = doc(r)["error"]
        self.assertEqual((e["detail"]["feature"], e["hint"]), ("subquery", "one input; there are no subqueries"))

    @unittest.skipUnless(os.environ.get("J3B_BASE"), "J3B_BASE names the directory of main's build")
    def test_the_goldens_of_the_earlier_slices_recomputed_on_the_binary_of_main(self):
        """The 5,585 cases of the goldens of A0, A1 and A2 (the hand lists, the pinned refusals, the seeded soups, the other forms an expression arrives in), run through the binary of `main` and through this
        one: the same status, rule and bytes, but for the hint of the two features that said there were no joins (the status and the rule hold)."""
        import not_a2_cases as K
        import or_a0
        import rename_b1
        from harness import run_argv
        base = os.path.join(os.environ["J3B_BASE"], "table")
        cases = list(or_a0.golden_cases()) + list(K.cases())
        s = Scratch()
        self.addCleanup(s.cleanup)
        s.write("t.csv", or_a0.DATA)
        old = {row[0]: row for row in or_a0.run_cases(cases, str(s.dir), lambda *a: run_argv([base, *a]))}
        new = {row[0]: row for row in or_a0.run_cases(cases, str(s.dir), run)}
        self.assertEqual(sorted(old), sorted(new))
        moved = []
        for cid, row in new.items():
            if row[1:4] != old[cid][1:4]:
                moved.append(cid)
        by_id = {or_a0.case_id(k, a): (k, a) for k, a in cases}
        import test_or_a1_compat as C1
        for cid in moved:
            self.assertTrue(C1.hint_moved(by_id[cid][1]), (cid, by_id[cid], old[cid][:3], new[cid][:3]))
            self.assertEqual(new[cid][1:3], old[cid][1:3], cid)
        self.assertGreater(len(old), 5000)
        print("\nJ3b: %d cases of the goldens of A0, A1 and A2 through main and through this binary: %d differ, all in the hint of join or subquery" % (len(old), len(moved)))

    def test_the_flags_of_a_join_are_unchanged(self):
        # the flag form of a join is not touched by the query form: the fixtures' joins by flags, by the tool and by the reference of the tests of J3
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        r = run("--root", s.dir, "--join", "b.csv", "--on", "id", "--join-type", "left", "--format", "csv", "p.csv")
        self.assertEqual(r.stdout, b"id,name,v,odd,city,n\n1,ann,10,a,x,100\n2,bob,20,b,y,200\n2,bob,20,b,yy,300\n2,bo2,25,c,y,200\n2,bo2,25,c,yy,300\n3,cy,30,d,,\n5,eve,7,e,,\n")


# ---- the introspection, the skill and the MCP tool -----------------------------------------------------------------------------------------

class Surface(unittest.TestCase):
    def test_the_help_of_query_says_the_join(self):
        flags = {f["name"]: f for f in introspect()["flags"]}
        for word in ("join", "as a", "alias.column"):
            self.assertIn(word, flags["--query"]["help"])
        usage = introspect()["usage"]
        self.assertIn("a join is from 'a.csv' as a", usage)

    def test_the_features_of_a_join_are_told_in_the_help_of_join(self):
        # (the sentences are in the help of --join, a flag late in the table: every lookup of a flag reads the table up to it, so a long help early costs every call)
        flags = {f["name"]: f for f in introspect()["flags"]}
        for feature in ("right", "full", "cross", "left outer join", "second join", "alias.column", "--join-where", "[inner, left, semi or anti]"):
            self.assertIn(feature, flags["--join"]["help"])


class Mcp(unittest.TestCase):
    """The server maps `file` and `stdin` to the operand, so a query that names its source with `from` has two (docs/joins.md section 20): a join query is that query, and the server's way to a join is
    the properties `join`, `on`, `join-type`, `join-prefix` and `join-where` (slice J4)."""

    def test_a_join_query_over_the_server_is_a_query_that_names_its_source(self):
        import test_mcp_stdin as MS
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        answers = []
        for args in ({"query": "select id from 'p.csv'", "file": "p.csv"},
                     {"query": "select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "file": "p.csv"},
                     {"query": "select a.id from stdin as a join 'b.csv' as b on a.id = b.id", "stdin": "id\n1\n"},
                     {"query": "select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id"}):
            [r], _ = MS.talk(s.dir, [MS.call(args)])
            answers.append(r["result"]["content"][0]["text"] if "result" in r else r["error"]["data"]["rule"])
        rules = [json.loads(a)["error"]["rule"] if a.startswith("{") else a for a in answers]
        self.assertEqual(rules, ["args.conflict", "args.conflict", "args.conflict", "mcp.input-missing"])

    def test_the_join_properties_do_what_a_query_does(self):
        import test_mcp_stdin as MS
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        [r], _ = MS.talk(s.dir, [MS.call({"file": "p.csv", "join": "b.csv", "on": "id=id", "join-type": "left", "join-prefix": "b.", "select": "name,b.city", "order-by": "name", "format": "csv"})])
        q = run("--root", s.dir, "--query", "select a.name, b.city from 'p.csv' as a left join 'b.csv' as b on a.id = b.id order by a.name", "--format", "csv")
        self.assertEqual(r["result"]["content"][0]["text"].encode(), q.stdout)


# ---- fuzz ---------------------------------------------------------------------------------------------------------------------------------------

JOIN_PIECES = ["select", "from", "where", "group", "by", "order", "limit", "offset", "and", "or", "not", "in", "contains", "as", "join", "inner", "left", "right", "full", "cross", "semi", "anti",
               "outer", "on", "using", "a", "b", "c", "a.id", "b.city", "a.", ".", "..", "b.n", "'p.csv'", "'b.csv'", "stdin", "=", "!=", "<", "(", ")", ",", "*", "::", "::int", "cast", "count", "sum",
               "distinct", "desc", "asc", "#1", "a.#1", "'x'", "1", "1.5", "id", "\"id\"", "\"a b\"", "a.\"a b\"", ";", "'", "\"", "é", "\t", "\n"]


def join_soup(rnd):
    return " ".join(rnd.choice(JOIN_PIECES) for _ in range(rnd.randrange(1, 18))).encode()


def mutate(rnd, q):
    import test_query as TQ
    return TQ.mutate(rnd, q)


class Fuzz(unittest.TestCase):
    def test_damaged_and_random_join_queries(self):
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        rnd = random.Random(int(os.environ.get("TABLE_J3B_SEED", "9")))
        seeds = [row[0] for row in C.REFUSALS] + [q for q, _, _ in C.ACCEPTED + C.ODD]
        cases = []
        for i in range(FUZZ):
            k = i % 4
            if k < 2:
                cases.append(mutate(rnd, rnd.choice(seeds)))
            elif k == 2:
                cases.append(join_soup(rnd))
            else:
                cases.append(rnd.choice(seeds).encode())
        s = Scratch()
        self.addCleanup(s.cleanup)
        fixtures(s)
        counts = {"accepted": 0, "refused-by-query": 0, "refused-by-engine": 0, "repairs": 0}
        import concurrent.futures
        import procs as subprocess

        def runbytes(b):
            p = subprocess.run([binary(), "--query", b, "--format", "json", "--root", str(s.dir)], capture_output=True, cwd=s.dir, input=b"id\n1\n", timeout=60)
            return p.returncode, p.stdout, p.stderr

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            outs = list(pool.map(runbytes, cases))
        for b, (status, out, err) in zip(cases, outs):
            with self.subTest(query=b):
                self.assertNotIn(status, TRAPS, (b, out, err))
                self.assertGreaterEqual(status, 0)
                self.assertEqual(err, b"", (b, err))
                lines = out.decode("utf-8", "replace").splitlines()
                self.assertEqual(len(lines), 1, (b, out[:200]))
                d = json.loads(lines[0])
                text = b.decode("latin-1")
                try:
                    flags, source = query_ref.translate(text)
                    ref = None
                except query_ref.QueryError as e:
                    flags, source, ref = None, None, e
                if d.get("ok"):
                    counts["accepted"] += 1
                    self.assertEqual(status, 0)
                    self.assertIsNone(ref, ("the reference refuses what the tool answers", b, ref and (ref.kind, ref.offset)))
                    continue
                e = d["error"]
                self.assertIn(e["rule"], rules, (b, e["rule"]))
                self.assertTrue(e["repair"] is None or e["repair"]["kind"] in ("none", "choose", "retry"), e)
                if e["rule"].startswith("query."):
                    counts["refused-by-query"] += 1
                    self.assertEqual(status, 2, (b, e["rule"]))
                    kind = e["rule"][6:]
                    if kind == "mixed-and-or":
                        kind = "mixed"
                    if kind == "too-long":
                        continue
                    self.assertTrue(0 <= e["detail"]["offset"] <= len(b), (b, e["detail"]))
                    if ref is None and e["rule"] == "query.syntax" and "column" in e["detail"]:
                        continue            # a literal that does not fit its column: the engine's literal checks, not the grammar's
                    self.assertIsNotNone(ref, ("the tool refuses what the reference accepts", b, e["rule"], e["detail"]))
                    self.assertEqual((kind, e["detail"]["offset"]), (ref.kind, ref.offset), (b, e["detail"]))
                    self.assertEqual(e["detail"].get("expected", e["detail"].get("feature")), ref.expected if ref.kind != "unsupported" else ref.feature, b)
                    if e["repair"] and e["repair"]["kind"] == "choose":
                        counts["repairs"] += 1
                        for option in e["repair"]["options"]:
                            again = run(*option["argv"][1:], cwd=s.dir, stdin=b"id\n1\n")
                            self.assertNotEqual((again.status, rule_of(again)), (2, e["rule"]), ("an offered repair is refused as the query was", b, option))
                else:
                    counts["refused-by-engine"] += 1
                    self.assertIsNone(ref, ("the reference refuses what the engine saw", b, ref and (ref.kind, ref.offset), e["rule"]))
        if os.environ.get("TABLE_J3B_REPORT"):
            print("\nJ3b fuzz: %d strings, %s" % (FUZZ, json.dumps(counts, sort_keys=True)))
        self.assertGreater(counts["refused-by-query"], FUZZ // 10, counts)
        self.assertGreater(counts["accepted"] + counts["refused-by-engine"], FUZZ // 20, counts)


if __name__ == "__main__":
    unittest.main()
