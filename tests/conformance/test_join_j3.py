"""J3 (docs/joins.md sections 6.3, 8.1, 8.2, 8.4, 11 and 18, slice J3): `--where` split into probe, build and after; `--join-where`; `--group`, `--agg`, `--sort`, `--order-by`, `--top`,
`--limit`, `--from` and `--rename` over the joined rows, with the build and the groups (or the rows to sort) sharing one `--max-state-bytes`; `--explain`; the guard repair of 5.3.

The gates, written before the code and each able to fail (on `main`'s binary, which has the join of J1 and J2 and refuses a group, a sort and a `--join-where` over it, every class below fails):

* O, the oracle: generated tables joined and grouped, sorted, paged by a Python reference of the definition (join_ref3.py), by SQLite and by DuckDB (the join in a derived table, `--join-where` as
  the `ON` clause, `--where` as `WHERE`, the build columns of a left join `COALESCE`d to the empty text because the tool has no null), for inner, left, semi and anti; as csv, as json, from a file, from
  standard input, at 4 threads. The reference itself was checked first against the tool's own non-join `--where`, `--group`, `--order-by` on the joined table written as a file.
* P, the pushdown pairs: for each kind of condition the definition of 8.1 splits, the plan with the condition pushed and the plan with the same condition not pushed (the condition in parentheses,
  which ends the leading run) give the same bytes, except for the one thing the definition says is different (which cells a typed key read can refuse); `--explain` shows the split.
* The pipe: a join written as csv and piped into a second call that groups, sorts and filters gives the bytes of the one call.
* A, the accounting: the program's count of the groups' bytes and the sorted rows' bytes, with the build's, equals the formula computed here by another program; the bound is exact (N accepted,
  N - 1 refused with the holder named) on the edges 2^20 and 2^20 + 1 rows of the build.
* The guard repair of 5.3, `--join-where`'s semantics (the ON of a left join), the flags, the explain document, the padded refusals in every context, `--report`, MCP, fuzz, authority.
"""

import json
import os
import random
import re
import shutil
import sys
import unittest

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import join_ref as J
import join_ref2 as R
import join_ref3 as R3
from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate

JTYPES = ("inner", "left", "semi", "anti")
BIG = ["--limit", "1000000", "--max-bytes", "67108864"]


def bytes_rows(rows):
    return [R3.to_bytes(r) for r in rows]


def write_case(d, case, opts=None):
    ph, pr = case.P
    bh, br = case.B
    J.write_case(str(d), [x.encode() for x in ph], bytes_rows(pr), [x.encode() for x in bh], bytes_rows(br), opts or {})


def argv_for(d, case, plan, fmt="csv", probe="p.csv"):
    ph, bh = case.P[0], case.B[0]
    flags = R3.flags_of(plan, ph, bh, fmt)
    if fmt == "json" and plan.get("limit") is None:
        flags += BIG
    elif fmt == "json" and "--max-bytes" not in flags:
        flags += ["--max-bytes", "67108864"]
    return ["--root", str(d), *R3.join_flags(plan, ph, bh), *flags] + ([probe] if probe else [])


def expected(plan, case):
    try:
        _, labels, rows = R3.execute(plan, case.P, case.B)
        return ("ok", labels, rows)
    except R3.Refused as r:
        return ("refuse", r.info)


def csv_bytes(labels, rows):
    return J.render_rows([[x.encode("latin-1") for x in labels]] + bytes_rows(rows))


def describe(plan):
    return {k: (R3.render(v) if k in ("jw", "where") else v) for k, v in plan.items() if k != "pairs"}


class Oracle(unittest.TestCase):
    """O: the tool against the reference, SQLite and DuckDB, for plans drawn at random over the shapes, for the four join types."""

    def judge(self, d, case, plan, tag, probe_bytes, sql=True):
        want = expected(plan, case)
        ph, bh = case.P[0], case.B[0]
        if want[0] == "ok" and sql and R3.sql_comparable(plan):
            lite, qs = R3.sqlite_answer(plan, case.P, case.B)
            self.assertEqual(R3.page(lite, plan), want[2], "%s: the reference and SQLite disagree\n%s" % (tag, qs))
            duck, dq = R3.duckdb_answer(plan, case.P, case.B)
            if duck is not None:
                self.assertEqual(R3.page(duck, plan), want[2], "%s: the reference and DuckDB disagree\n%s" % (tag, dq))
        j = run(*argv_for(d, case, plan, "json"))
        if want[0] == "ok":
            self.assertEqual(j.status, 0, "%s: %r" % (tag, j.stdout[:400] + j.stderr[:300]))
            self.assertEqual(validate(j), [], tag)
            data = j.data()
            self.assertEqual((data["columns"], data["rows"]), (want[1], want[2]), tag)
            c = run(*argv_for(d, case, plan, "csv"))
            self.assertEqual((c.status, c.stdout), (0, csv_bytes(want[1], want[2])), "%s: the csv bytes" % tag)
            s = run(*argv_for(d, case, plan, "csv", probe=None), stdin=probe_bytes)
            self.assertEqual((s.status, s.stdout), (0, c.stdout), "%s: standard input" % tag)
            t = run(*argv_for(d, case, plan, "csv"), "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7")
            self.assertEqual((t.status, t.stdout), (0, c.stdout), "%s: threads" % tag)
        else:
            info = want[1]
            self.assertEqual(j.status, 8, "%s: wanted a refusal %r, got %r" % (tag, info, j.stdout[:400]))
            e = j.error()
            d_ = e["detail"]
            self.assertEqual((e["rule"], d_["context"], d_["column"], d_["value"], d_.get("padded", False)), (info["rule"], info["context"], info["column"], info["value"], info["padded"]), "%s: %r" % (tag, e))
            self.assertEqual((d_["row"], d_["line"]), (info["row"], info["line"]), tag)
            self.assertEqual(d_["path"], "b.csv" if info["context"] == "join-where" else "p.csv", tag)
            s = run(*argv_for(d, case, plan, "json", probe=None), stdin=probe_bytes)
            self.assertEqual((s.status, s.first_rule()), (8, info["rule"]), "%s: standard input" % tag)

    def test_the_shapes_for_the_four_join_types(self):
        rng = random.Random(20261016)
        for case in R3.shapes():
            s = Scratch()
            try:
                write_case(s.dir, case)
                probe = (s.dir / "p.csv").read_bytes()
                for jt in JTYPES:
                    for i in range(5):
                        plan = R3.random_plan(rng, case, jt)
                        tag = "%s [%s] %d %r" % (case.name, jt, i, describe(plan))
                        with self.subTest(tag):
                            self.judge(s.dir, case, plan, tag, probe)
            finally:
                s.cleanup()

    def test_random_plans_against_the_reference_alone(self):
        rng = random.Random(77)
        cases = R3.shapes(seed=5)
        for i in range(250):
            case = rng.choice(cases)
            jt = rng.choice(JTYPES)
            plan = R3.random_plan(rng, case, jt)
            tag = "%d %s [%s] %r" % (i, case.name, jt, describe(plan))
            with self.subTest(tag):
                s = Scratch()
                try:
                    write_case(s.dir, case)
                    self.judge(s.dir, case, plan, tag, (s.dir / "p.csv").read_bytes(), sql=False)
                finally:
                    s.cleanup()

    def test_the_formats_of_the_files(self):
        # CRLF, a BOM and every field quoted, on either side
        rng = random.Random(3)
        case = R3.shapes()[0]
        for opts in ({"crlf_p": True, "bom_p": True}, {"crlf_b": True, "bom_b": True}, {"quote_all_p": True, "quote_all_b": True}, {"crlf_p": True, "crlf_b": True, "nofinal_p": True}):
            for jt in JTYPES:
                plan = R3.random_plan(rng, case, jt)
                s = Scratch()
                try:
                    write_case(s.dir, case, opts)
                    tag = "%r [%s] %r" % (opts, jt, describe(plan))
                    with self.subTest(tag):
                        self.judge(s.dir, case, plan, tag, (s.dir / "p.csv").read_bytes(), sql=False)
                finally:
                    s.cleanup()


class Fixture(unittest.TestCase):
    """Small tables a reader can check by eye."""

    PROBE = "pk,pg,pv\na,x,1\nb,y,2\na,x,3\nc,x,4\nd,q,5\n"
    BUILD = "bk,bg,bv\na,x,10\na,y,20\nb,x,30\nz,x,40\n"

    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def go(self, extra, probe=None, build=None, on="pk=bk", jtype="inner", fmt="csv", pfile="p.csv", stdin=None):
        self.s.write("p.csv", probe if probe is not None else self.PROBE)
        self.s.write("b.csv", build if build is not None else self.BUILD)
        argv = ["--root", self.s.dir, "--join", "b.csv", "--on", on, "--join-type", jtype, *extra]
        if fmt:
            argv += ["--format", fmt]
        return run(*argv, *([] if stdin is not None else [pfile]), stdin=stdin)

    def rows(self, r):
        self.assertEqual(r.status, 0, (r.stdout[:400], r.stderr[:300]))
        return r.stdout.decode().splitlines()

    def explain(self, extra, **kw):
        r = self.go(["--explain", *extra], fmt=None, **kw)
        self.assertEqual(r.status, 0, r.stdout[:400])
        return r.data()["explain"]


INNER = ["pk,pg,pv,bk,bg,bv", "a,x,1,a,x,10", "a,x,1,a,y,20", "b,y,2,b,x,30", "a,x,3,a,x,10", "a,x,3,a,y,20"]


class Pairs(Fixture):
    """P: the definition of 8.1 splits a leading run of text conditions by side. The plan with a condition pushed and the plan with the same condition not pushed (in parentheses, which ends the
    run) give the same bytes; the one difference the definition names is which cells a typed key read can refuse, and it is tested as a pair too."""

    def pair(self, pushed, twin, **kw):
        a = self.go(["--where", pushed], **kw)
        b = self.go(["--where", twin], **kw)
        self.assertEqual((a.status, a.stdout), (b.status, b.stdout), "%r and %r" % (pushed, twin))
        return self.rows(a) if a.status == 0 else None

    def test_a_probe_side_condition_is_pushed_for_every_join_type(self):
        for jt in JTYPES:
            with self.subTest(jt):
                got = self.pair("pg = 'x' and pv:int > 1", "(pg = 'x') and pv:int > 1", jtype=jt)
                x = self.explain(["--where", "pg = 'x' and pv:int > 1"], jtype=jt)["join"]["where"]
                self.assertEqual((x["probe_before_join"], x["build_before_build"], x["after_join"]), (["pg = 'x'"], [], ["pv:int > 1"]), jt)
                y = self.explain(["--where", "(pg = 'x') and pv:int > 1"], jtype=jt)["join"]["where"]
                self.assertEqual((y["probe_before_join"], y["build_before_build"], y["after_join"]), ([], [], ["(pg = 'x')", "pv:int > 1"]), jt)
                if jt == "inner":
                    self.assertEqual(got, ["pk,pg,pv,bk,bg,bv", "a,x,3,a,x,10", "a,x,3,a,y,20"])

    def test_a_build_side_condition_is_pushed_for_an_inner_join_only(self):
        got = self.pair("bg = 'y' and pv:int >= 1", "(bg = 'y') and pv:int >= 1")
        self.assertEqual(got, ["pk,pg,pv,bk,bg,bv", "a,x,1,a,y,20", "a,x,3,a,y,20"])
        x = self.explain(["--where", "bg = 'y' and pv:int >= 1"])["join"]["where"]
        self.assertEqual((x["probe_before_join"], x["build_before_build"], x["after_join"]), ([], ["bg = 'y'"], ["pv:int >= 1"]))
        # a left join keeps a padded row for ON, and WHERE removes it: the condition stays after the join
        x = self.explain(["--where", "bg = 'y' and pv:int >= 1"], jtype="left")["join"]["where"]
        self.assertEqual((x["probe_before_join"], x["build_before_build"], x["after_join"]), ([], [], ["bg = 'y'", "pv:int >= 1"]))
        # a build column does not exist in a semi or an anti join
        for jt in ("semi", "anti"):
            r = self.go(["--where", "bg = 'y'"], jtype=jt, fmt="json")
            self.assertEqual((r.status, r.first_rule()), (3, "column.unknown"), jt)

    def test_the_run_of_a_left_join_pushes_the_probe_side_past_a_build_side_condition(self):
        # text conditions commute: the probe-side one after a build-side one in the run is still pushed
        x = self.explain(["--where", "bg = 'y' and pg = 'x'"], jtype="left")["join"]["where"]
        self.assertEqual((x["probe_before_join"], x["build_before_build"], x["after_join"]), (["pg = 'x'"], [], ["bg = 'y'"]))
        x = self.explain(["--where", "bg = 'y' and pg = 'x'"])["join"]["where"]
        self.assertEqual((x["probe_before_join"], x["build_before_build"], x["after_join"]), (["pg = 'x'"], ["bg = 'y'"], []))

    def test_what_ends_the_run(self):
        cases = [
            ("pv:int > 1 and pg = 'x'", [], [], ["pv:int > 1", "pg = 'x'"]),                                    # a typed condition
            ("pg = 'x' and (pv:int > 1 or bg = 'y')", ["pg = 'x'"], [], ["(pv:int > 1 or bg = 'y')"]),         # a group
            ("pg = 'x' or pv:int > 1", [], [], ["pg = 'x' or pv:int > 1"]),                                     # a top-level or
            ("not (pg = 'x') and bg = 'y'", [], [], ["not (pg = 'x')", "bg = 'y'"]),                           # not (...)
            ("pg not in ('q') and bg = 'y'", ["pg not in ('q')"], ["bg = 'y'"], []),                           # a negated condition is still a bare condition
            ("(pg = 'x' and pk != 'q') and bg = 'y'", [], [], ["(pg = 'x' and pk != 'q')", "bg = 'y'"]),
            ("pg contains 'x' and pg in ('x', 'y') and pk != ''", ["pg contains 'x'", "pg in ('x', 'y')", "pk != ''"], [], []),
            ("pg = 'x'", ["pg = 'x'"], [], []),
            ("pg = 'x' and pv:int > 1 and pk = 'a'", ["pg = 'x'"], [], ["pv:int > 1", "pk = 'a'"]),             # after the first typed one, nothing is pushed
        ]
        for text, probe, build, after in cases:
            with self.subTest(text):
                w = self.explain(["--where", text])["join"]["where"]
                self.assertEqual((w["probe_before_join"], w["build_before_build"], w["after_join"]), (probe, build, after))

    def test_the_pushed_and_the_unpushed_plan_give_the_same_bytes_with_or_and_not(self):
        pairs = [("pg = 'x' and (pv:int > 1 or bg = 'y')", "(pg = 'x') and (pv:int > 1 or bg = 'y')"),
                 ("pg not in ('q', 'y') and bg = 'x' and not (pv:int = 3)", "(pg not in ('q', 'y')) and (bg = 'x') and not (pv:int = 3)"),
                 ("pk != 'z' and pg contains 'x' and bv:int >= 20", "(pk != 'z') and (pg contains 'x') and bv:int >= 20")]
        for jt in JTYPES[:2]:
            for a, b in pairs:
                with self.subTest(a + jt):
                    self.pair(a, b, jtype=jt)

    def test_the_one_difference_is_which_cells_a_typed_key_read_can_refuse_probe_side(self):
        probe = "pk,pg\n1,x\n,q\n2,x\n"
        build = "bk,bg\n1,a\n2,b\n"
        pushed = self.go(["--where", "pg = 'x'"], probe=probe, build=build, on="pk=bk:int")
        self.assertEqual(self.rows(pushed), ["pk,pg,bk,bg", "1,x,1,a", "2,x,2,b"], "the row whose key is empty is removed before its key is read")
        twin = self.go(["--where", "(pg = 'x')"], probe=probe, build=build, on="pk=bk:int", fmt="json")
        self.assertEqual((twin.status, twin.first_rule()), (8, "value.not-integer"))
        d = twin.error()["detail"]
        self.assertEqual((d["context"], d["input"], d["row"], d["line"], d["value"]), ("join", "probe", 2, 3, ""))
        # the pushed plan answers what the unpushed plan would if the file were mended
        mended = self.go(["--where", "(pg = 'x')"], probe="pk,pg\n1,x\n2,x\n", build=build, on="pk=bk:int")
        self.assertEqual(self.rows(mended), self.rows(pushed))

    def test_the_one_difference_is_which_cells_a_typed_key_read_can_refuse_build_side(self):
        probe = "pk,pg\n1,x\n2,y\n"
        build = "bk,bg\n1,x\n,q\n2,y\n"
        pushed = self.go(["--where", "bg != 'q'"], probe=probe, build=build, on="pk=bk:int")
        self.assertEqual(self.rows(pushed), ["pk,pg,bk,bg", "1,x,1,x", "2,y,2,y"], "an inner join: the build row whose key is empty is not held, so its key is not read")
        twin = self.go(["--where", "(bg != 'q')"], probe=probe, build=build, on="pk=bk:int", fmt="json")
        d = twin.error()["detail"]
        self.assertEqual((twin.first_rule(), d["context"], d["input"], d["row"], d["line"]), ("value.not-integer", "join", "build", 2, 3))
        # a left join does not push a build-side condition, so its key is read
        left = self.go(["--where", "bg != 'q'"], probe=probe, build=build, on="pk=bk:int", jtype="left", fmt="json")
        self.assertEqual((left.status, left.error()["detail"]["input"]), (8, "build"))

    def test_the_join_where_runs_before_the_pushed_build_conditions_and_the_key(self):
        probe = "pk,pg\n1,x\n2,y\n"
        build = "bk,bg\n1,x\n,q\n2,y\n"
        ok = self.go(["--join-where", "bg != 'q'", "--where", "(bg != 'zz')"], probe=probe, build=build, on="pk=bk:int")
        self.assertEqual(self.rows(ok), ["pk,pg,bk,bg", "1,x,1,x", "2,y,2,y"])

    def test_from_counts_the_joined_rows_that_the_leading_run_lets_through(self):
        # a narrow reading (docs/joins.md section 18): a probe row the run rejects produces no joined row, so --from and --max-join-rows do not count it
        a = self.rows(self.go(["--where", "pg = 'x'", "--from", "3", "--limit", "5"]))
        self.assertEqual(a, ["pk,pg,pv,bk,bg,bv", "a,x,3,a,y,20"], "the rows are 1, 2, 4, 5 of the join: --from 3 skips three of them")
        b = self.rows(self.go(["--where", "(pg = 'x')", "--from", "3", "--limit", "5"]))
        self.assertEqual(b, ["pk,pg,pv,bk,bg,bv", "a,x,3,a,x,10", "a,x,3,a,y,20"], "unpushed, the join has five rows and --from 3 skips the first three")
        self.assertEqual(self.go(["--where", "pg = 'x'", "--max-join-rows", "4"]).status, 0)
        r = self.go(["--where", "(pg = 'x')", "--max-join-rows", "4"], fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-join-rows"))


class PipeEquivalence(unittest.TestCase):
    """A join written as csv and piped into a second call that filters, groups and sorts it gives the bytes of the one call (the workaround J2 taught, now the same thing)."""

    def test_random_plans(self):
        rng = random.Random(4242)
        cases = R3.shapes(seed=9)
        checked = refused = 0
        for i in range(160):
            case = rng.choice(cases)
            jt = rng.choice(JTYPES)
            plan = R3.random_plan(rng, case, jt)
            grouping = plan.get("group") is not None or plan.get("aggs") is not None
            if plan.get("from") and not grouping and plan.get("order") is None and plan.get("where") is not None and R3.lead_run(plan["where"]):
                continue
            s = Scratch()
            try:
                write_case(s.dir, case)
                ph, bh = case.P[0], case.B[0]
                tag = "%d %s [%s] %r" % (i, case.name, jt, describe(plan))
                one = run(*argv_for(s.dir, case, plan, "csv"))
                stage1 = ["--root", s.dir, *R3.join_flags(plan, ph, bh)] + (["--join-where", R3.render(plan["jw"])] if plan.get("jw") is not None else []) + ["--format", "csv", "p.csv"]
                first = run(*stage1)
                if first.status != 0:
                    # the join itself is refused (a typed cell of `--join-where`): the one call is refused by the same rule, at the same cell
                    self.assertEqual((one.status, one.stderr), (first.status, first.stderr), tag)
                    refused += 1
                    checked += 1
                    continue
                (s.dir / "j.csv").write_bytes(first.stdout)
                rest = {k: v for k, v in plan.items() if k in ("where", "select", "group", "aggs", "sort", "order", "rename", "top", "limit", "from")}
                flags2 = R3.flags_of(rest, 0, 0, "csv")
                if not any(k in rest for k in ("where", "select", "group", "aggs", "order")):
                    flags2 = ["--rows"] + flags2
                second = run("--root", s.dir, *flags2, "j.csv")
                with self.subTest(tag):
                    self.assertEqual((one.status, one.stdout), (second.status, second.stdout), "%s\n%r\n%r" % (tag, one.stderr[:300], second.stderr[:300]))
                checked += 1
                refused += one.status != 0
            finally:
                s.cleanup()
        self.assertGreater(checked, 100)


class JoinWhere(Fixture):
    """`--join-where` is the ON of the join: it filters the build rows before they are held."""

    def test_a_left_join_keeps_the_probe_row_padded_where_where_would_drop_it(self):
        on = self.rows(self.go(["--join-where", "bg = 'y'"], jtype="left"))
        self.assertEqual(on, ["pk,pg,pv,bk,bg,bv", "a,x,1,a,y,20", "b,y,2,,,", "a,x,3,a,y,20", "c,x,4,,,", "d,q,5,,,"])
        wh = self.rows(self.go(["--where", "bg = 'y'"], jtype="left"))
        self.assertEqual(wh, ["pk,pg,pv,bk,bg,bv", "a,x,1,a,y,20", "a,x,3,a,y,20"])

    def test_an_inner_join_is_the_same_either_way(self):
        self.assertEqual(self.go(["--join-where", "bg = 'y'"]).stdout, self.go(["--where", "bg = 'y'"]).stdout)

    def test_semi_and_anti_ask_about_the_filtered_build(self):
        self.assertEqual(self.rows(self.go(["--join-where", "bg = 'y'"], jtype="semi")), ["pk,pg,pv", "a,x,1", "a,x,3"])
        self.assertEqual(self.rows(self.go(["--join-where", "bg = 'y'"], jtype="anti")), ["pk,pg,pv", "b,y,2", "c,x,4", "d,q,5"])

    def test_it_names_the_build_columns_by_their_own_names_not_the_prefix(self):
        r = self.go(["--join-prefix", "r_", "--join-where", "bg = 'y'", "--select", "pk,r_bg"], on="pk=bk")
        self.assertEqual(self.rows(r), ["pk,r_bg", "a,y", "a,y"])
        r = self.go(["--join-prefix", "r_", "--join-where", "r_bg = 'y'"], fmt="json")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["flag"], r.error()["detail"]["input"]), (3, "column.unknown", "--join-where", "build"))
        r = self.go(["--join-where", "#3:int > 10"])
        self.assertEqual(self.rows(r), ["pk,pg,pv,bk,bg,bv", "a,x,1,a,y,20", "b,y,2,b,x,30", "a,x,3,a,y,20"], "#3 is the third column of the build file's own header")

    def test_a_dropped_using_column_can_still_be_named(self):
        r = self.go(["--join-where", "id != 'b'"], probe="id,v\na,1\nb,2\n", build="id,w\na,A\nb,B\n", on="id")
        self.assertEqual(self.rows(r), ["id,v,w", "a,1,A"])

    def test_it_takes_the_whole_grammar(self):
        r = self.go(["--join-where", "bg = 'y' or (bv:int > 25 and not (bk = 'z'))"])
        self.assertEqual(self.rows(r), ["pk,pg,pv,bk,bg,bv", "a,x,1,a,y,20", "b,y,2,b,x,30", "a,x,3,a,y,20"])
        r = self.go(["--join-where", "bv != '' and bv:int > 15"], build="bk,bg,bv\na,x,\na,y,20\n")
        self.assertEqual(self.rows(r), ["pk,pg,pv,bk,bg,bv", "a,x,1,a,y,20", "a,x,3,a,y,20"], "the guard stops the typed read, in the build as it does anywhere")

    def test_a_typed_cell_that_is_not_of_its_type_is_refused_in_build_order(self):
        r = self.go(["--join-where", "bv:int > 15"], build="bk,bg,bv\na,x,10\nb,x,oops\nc,y,bad\n", fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        d = r.error()["detail"]
        self.assertEqual((d["context"], d["path"], d["column"], d["row"], d["line"], d["value"]), ("join-where", "b.csv", "bv", 2, 3, "oops"))
        self.assertNotIn("padded", d)
        # a row the condition rejects before the typed one is never read
        ok = self.go(["--join-where", "(bg = 'x' and bv:int > 5) or bg = 'y'"], build="bk,bg,bv\na,x,10\nb,y,oops\n", fmt="csv")
        self.assertEqual(self.rows(ok), ["pk,pg,pv,bk,bg,bv", "a,x,1,a,x,10", "b,y,2,b,y,oops", "a,x,3,a,x,10"])

    def test_a_row_the_join_where_rejects_is_never_read_for_its_key(self):
        r = self.go(["--join-where", "bg != 'q'"], probe="pk,pg\n1,x\n", build="bk,bg\n1,x\n,q\n", on="pk=bk:int")
        self.assertEqual(self.rows(r), ["pk,pg,bk,bg", "1,x,1,x"])
        r = self.go([], probe="pk,pg\n1,x\n", build="bk,bg\n1,x\n,q\n", on="pk=bk:int", fmt="json")
        self.assertEqual(r.first_rule(), "value.not-integer")
        r = self.go(["--join-where", "bg != 'x'"], probe="pk,pg\n1,x\n", build="bk,bg\n1,x\n,q\n", on="pk=bk:int", fmt="json")
        self.assertEqual((r.first_rule(), r.error()["detail"]["input"], r.error()["detail"]["row"]), ("value.not-integer", "build", 2))

    def test_it_needs_a_join_and_a_grammar(self):
        r = self.s.table("p.csv", self.PROBE, "--join-where", "bg = 'y'")
        self.assertEqual((r.status, r.first_rule()), (2, "args.required-flag"))
        r = self.go(["--join-where", "bg = "], fmt="json")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["flag"]), (2, "where.syntax", "--join-where"))
        r = self.go(["--join-where", "bg = 'y' and bv = '1' or bk = 'a'"], fmt="json")
        self.assertEqual((r.status, r.first_rule()), (2, "where.mixed-and-or"))
        r = self.go(["--join-where", "nope = 'y'"], fmt="json")
        d = r.error()["detail"]
        self.assertEqual((r.status, r.first_rule(), d["flag"], d["input"], d["name"], d["available"]), (3, "column.unknown", "--join-where", "build", "nope", ["bk", "bg", "bv"]))
        r = self.go(["--join-where", "x = 1"], build="bk,x,x\na,1,2\n", fmt="json")
        self.assertEqual(r.first_rule(), "column.ambiguous")

    def test_it_is_in_introspect_and_the_help(self):
        flags = {f["name"]: f for f in introspect()["flags"]}
        self.assertIn("--join-where", flags)
        self.assertIn("ON", flags["--join-where"]["help"])
        self.assertIn("left", flags["--join-where"]["help"])


class Groups(Fixture):
    """--group, --agg, --sort, --order-by, --top, --limit and --from take the joined row."""

    def test_groups_of_an_inner_join(self):
        r = self.go(["--group", "bg", "--agg", "count,sum:bv", "--sort", "-count"])
        self.assertEqual(self.rows(r), ["bg,count,sum:bv", "x,3,50", "y,2,40"])
        r = self.go(["--group", "pg,bg", "--agg", "count,min:bv,max:bv,distinct:pk"])
        self.assertEqual(self.rows(r), ["pg,bg,count,min:bv,max:bv,distinct:pk", "x,x,2,10,10,1", "x,y,2,20,20,1", "y,x,1,30,30,1"])
        r = self.go(["--agg", "count,sum:pv"])
        self.assertEqual(self.rows(r), ["count,sum:pv", "5,10"])
        r = self.go(["--group", "bg"])
        self.assertEqual(self.rows(r), ["bg,count", "x,3", "y,2"])

    def test_a_left_join_counts_its_padded_rows(self):
        r = self.go(["--group", "bg", "--agg", "count"], jtype="left")
        self.assertEqual(self.rows(r), ["bg,count", ",2", "x,3", "y,2"])
        r = self.go(["--agg", "count,distinct:bg"], jtype="left")
        self.assertEqual(self.rows(r), ["count,distinct:bg", "7,3"], "count(*) counts the padded rows and the empty text is one value")

    def test_semi_and_anti_group_the_probe_columns(self):
        self.assertEqual(self.rows(self.go(["--group", "pg", "--agg", "count"], jtype="semi")), ["pg,count", "x,2", "y,1"])
        self.assertEqual(self.rows(self.go(["--group", "pg", "--agg", "count"], jtype="anti")), ["pg,count", "q,1", "x,1"])

    def test_a_join_that_gives_no_rows_gives_no_groups(self):
        self.assertEqual(self.rows(self.go(["--group", "bg", "--agg", "count"], build="bk,bg,bv\n")), ["bg,count"])
        self.assertEqual(self.rows(self.go(["--agg", "count"], probe="pk,pg,pv\n")), ["count"])

    def test_order_by_is_stable_in_the_order_of_the_join(self):
        r = self.go(["--order-by", "bg"])
        self.assertEqual(self.rows(r), ["pk,pg,pv,bk,bg,bv", "a,x,1,a,x,10", "b,y,2,b,x,30", "a,x,3,a,x,10", "a,x,1,a,y,20", "a,x,3,a,y,20"])
        r = self.go(["--order-by", "-bv:int", "--top", "2"])
        self.assertEqual(self.rows(r), ["pk,pg,pv,bk,bg,bv", "b,y,2,b,x,30", "a,x,1,a,y,20"])
        r = self.go(["--order-by", "bv:int", "--from", "1", "--limit", "2"])
        self.assertEqual(self.rows(r), ["pk,pg,pv,bk,bg,bv", "a,x,3,a,x,10", "a,x,1,a,y,20"])

    def test_the_page_of_groups_says_where_the_next_begins(self):
        r = self.go(["--group", "bg", "--agg", "count", "--limit", "1"], fmt="json")
        d = r.data()
        self.assertEqual((d["rows"], d["truncated"], d["next"], d["group_count"]), ([["x", "3"]], True, {"from": 1}, 2))
        r = self.go(["--group", "bg", "--agg", "count", "--limit", "1", "--from", "1"], fmt="json")
        self.assertEqual((r.data()["rows"], r.data()["next"]), ([["y", "2"]], None))

    def test_the_names_the_flags_use_are_the_joined_header(self):
        r = self.go(["--join-prefix", "r_", "--group", "r_bg", "--agg", "sum:r_bv", "--rename", "r_bg=who,sum:r_bv=total", "--sort", "-total"], on="pk=bk")
        self.assertEqual(self.rows(r), ["who,total", "x,50", "y,40"])
        r = self.go(["--group", "#5", "--agg", "count"])
        self.assertEqual(self.rows(r), ["bg,count", "x,3", "y,2"], "a position names the column of the joined header")
        r = self.go(["--group", "bg", "--sort", "nope"], fmt="json")
        self.assertEqual(r.first_rule(), "sort.unknown-key")

    def test_a_typed_group_key_is_read_as_its_value(self):
        r = self.go(["--group", "bv:int", "--agg", "count"], build="bk,bg,bv\na,x,007\na,y,7\nb,x,-0\nz,x,0\n")
        self.assertEqual(self.rows(r), ["bv,count", "0,1", "7,4"])

    def test_report_over_a_join_is_still_the_pipe(self):
        r = self.go(["--report", "types"], fmt=None)
        self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"))
        r = self.go(["--select", "pk", "--group", "pg"], fmt=None)
        self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"))
        r = self.go(["--top", "3"], fmt=None)
        self.assertEqual((r.status, r.first_rule()), (2, "args.required-flag"))


class Padded(Fixture):
    """There is no null: a padded cell is the empty text, and every typed read of it is a refusal that says it was padded."""

    def refusal(self, extra, probe=None, build=None, jtype="left"):
        r = self.go(extra, probe=probe, build=build, jtype=jtype, fmt="json")
        self.assertEqual(r.status, 8, r.stdout[:300])
        return r.first_rule(), r.error()["detail"]

    def test_an_aggregate_of_a_padded_column(self):
        for fn, ctx in (("sum", "sum"), ("min", "min"), ("max", "max")):
            rule, d = self.refusal(["--group", "pg", "--agg", "%s:bv" % fn])
            self.assertEqual((rule, d["context"], d["column"], d["value"], d.get("padded"), d["row"], d["line"], d["path"]), ("value.not-integer", ctx, "bv", "", True, 4, 5, "p.csv"), fn)

    def test_a_group_key_and_a_sort_key_that_are_padded(self):
        rule, d = self.refusal(["--group", "bv:int"])
        self.assertEqual((rule, d["context"], d["column"], d.get("padded")), ("value.not-integer", "group", "bv", True))
        rule, d = self.refusal(["--order-by", "bv:int"])
        self.assertEqual((rule, d["context"], d["column"], d.get("padded")), ("value.not-integer", "order-by", "bv", True))
        rule, d = self.refusal(["--agg", "sum:bv:dec(2)"])
        self.assertEqual((rule, d["context"], d.get("padded")), ("value.not-decimal", "sum", True))
        rule, d = self.refusal(["--agg", "sum:bv:float"])
        self.assertEqual((rule, d["context"], d.get("padded")), ("value.not-float", "sum", True))

    def test_the_cells_that_are_not_padding_do_not_say_so(self):
        rule, d = self.refusal(["--agg", "sum:bv"], build="bk,bg,bv\na,x,\nb,y,5\n", jtype="inner")
        self.assertEqual((rule, d["context"], d["row"]), ("value.not-integer", "sum", 1))
        self.assertNotIn("padded", d, "an empty cell of a matched row is a real empty cell")
        rule, d = self.refusal(["--agg", "sum:pv"], probe="pk,pg,pv\nz,x,\n", build="bk,bg,bv\na,x,1\n", jtype="left")
        self.assertNotIn("padded", d, "a cell of the probe is never padding")

    def test_the_guard_a_person_is_taught(self):
        r = self.go(["--where", "bv != ''", "--group", "pg", "--agg", "sum:bv"], jtype="left")
        self.assertEqual(self.rows(r), ["pg,sum:bv", "x,60", "y,30"])

    def test_an_aggregate_of_a_text_column_reads_padding_as_text(self):
        self.assertEqual(self.rows(self.go(["--group", "pg", "--agg", "count,distinct:bg"], jtype="left")), ["pg,count,distinct:bg", "q,1,1", "x,5,3", "y,1,1"])


class Guard(Fixture):
    """The repair of 5.3, now that the filter that runs before the key is read exists: an empty typed key cell is answered with the guard."""

    def repair_argv(self, r):
        rep = r.error()["repair"]
        self.assertEqual(rep["kind"], "choose", r.stdout[:400])
        self.assertEqual(len(rep["options"]), 1)
        return rep["options"][0]["argv"]

    def after(self, argv, flag):
        i = argv.index(flag)
        return argv[i + 1]

    def test_a_build_key_that_is_empty(self):
        r = self.go([], probe="pk,pg\n1,x\n", build="bk,bg\n1,a\n,b\n", on="pk=bk:int", fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        argv = self.repair_argv(r)
        self.assertEqual(self.after(argv, "--join-where"), "bk != ''")
        again = subprocess.run(argv, capture_output=True, input=b"")
        self.assertEqual(again.returncode, 0, again.stdout[:300])
        self.assertEqual(json.loads(again.stdout)["data"]["rows"], [["1", "x", "1", "a"]])

    def test_a_build_key_that_is_empty_beside_a_join_where_of_its_own(self):
        r = self.go(["--join-where", "bg != 'zz' or bg = 'q'"], probe="pk,pg\n1,x\n", build="bk,bg\n1,a\n,b\n", on="pk=bk:int", fmt="json")
        argv = self.repair_argv(r)
        self.assertEqual(self.after(argv, "--join-where"), "bk != '' and (bg != 'zz' or bg = 'q')")
        self.assertEqual(subprocess.run(argv, capture_output=True, input=b"").returncode, 0)

    def test_a_probe_key_that_is_empty(self):
        r = self.go([], probe="pk,pg\n1,x\n,y\n", build="bk,bg\n1,a\n", on="pk=bk:int", fmt="json")
        self.assertEqual((r.first_rule(), r.error()["detail"]["input"]), ("value.not-integer", "probe"))
        argv = self.repair_argv(r)
        self.assertEqual(self.after(argv, "--where"), "pk != ''")
        again = subprocess.run(argv, capture_output=True, input=b"")
        self.assertEqual(json.loads(again.stdout)["data"]["rows"], [["1", "x", "1", "a"]])

    def test_a_probe_key_that_is_empty_beside_a_where_of_its_own(self):
        r = self.go(["--where", "pg = 'x' or pg = 'y'"], probe="pk,pg\n1,x\n,y\n", build="bk,bg\n1,a\n", on="pk=bk:int", fmt="json")
        argv = self.repair_argv(r)
        self.assertEqual(self.after(argv, "--where"), "pk != '' and (pg = 'x' or pg = 'y')")
        self.assertEqual(subprocess.run(argv, capture_output=True, input=b"").returncode, 0)

    def test_the_name_is_written_as_the_grammar_reads_it(self):
        r = self.go([], probe="my key,pg\n1,x\n,y\n", build="bk,bg\n1,a\n", on="my key=bk:int", fmt="json")
        argv = self.repair_argv(r)
        self.assertEqual(self.after(argv, "--where"), "'my key' != ''")
        self.assertEqual(subprocess.run(argv, capture_output=True, input=b"").returncode, 0)
        # a name the header has twice is written as its position
        r = self.go([], probe="k,k\n1,x\n,y\n", build="bk,bg\n1,a\n", on="#1=bk:int", fmt="json")
        argv = self.repair_argv(r)
        self.assertEqual(self.after(argv, "--where"), "#1 != ''")

    def test_a_cell_that_is_not_empty_has_no_guard(self):
        for side, probe, build in (("probe", "pk,pg\noops,x\n", "bk,bg\n1,a\n"), ("build", "pk,pg\n1,x\n", "bk,bg\noops,b\n")):
            r = self.go([], probe=probe, build=build, on="pk=bk:int", fmt="json")
            self.assertEqual(r.first_rule(), "value.not-integer", side)
            rep = r.error()["repair"]
            self.assertTrue(rep is None or rep["kind"] == "none", side)
            self.assertIn("text", r.error()["hint"])

    def test_the_hint_no_longer_says_there_is_no_filter(self):
        r = self.go([], probe="pk,pg\n1,x\n", build="bk,bg\n,b\n", on="pk=bk:int", fmt="json")
        h = r.error()["hint"]
        self.assertNotIn("this version has no filter", h)
        self.assertIn("--join-where", h)
        self.assertIn("--where", h)

    def test_the_guard_of_a_float_and_a_decimal_key(self):
        for typ in ("float", "dec(2)"):
            r = self.go([], probe="pk,pg\n1,x\n", build="bk,bg\n1,a\n,b\n", on="pk=bk:" + typ, fmt="json")
            argv = self.repair_argv(r)
            self.assertEqual(self.after(argv, "--join-where"), "bk != ''")
            self.assertEqual(subprocess.run(argv, capture_output=True, input=b"").returncode, 0, typ)


class Accounting(unittest.TestCase):
    """A: the build, the groups and the sorted rows share one --max-state-bytes. The program's count of each equals the formula computed here by another program (the reference's trace), and the
    bound is exact: accepted at the sum, refused one byte below it with the holder named, the number the build holds in the refusal, and the holder's own count before the item that did not fit."""

    def exact(self, case, plan, tag, threads=(1,), stdin=False):
        s = Scratch()
        try:
            write_case(s.dir, case)
            trace = {}
            try:
                R3.execute(plan, case.P, case.B, trace)
            except R3.Refused:
                return None
            build = sum(trace.get("build", []))
            holder, items = ("groups", trace.get("groups")) if plan.get("group") is not None or plan.get("aggs") is not None else ("sort", trace.get("sort"))
            if not items or not build:
                return None
            total = build + sum(items)
            probe = (s.dir / "p.csv").read_bytes()
            for t in threads:
                base = argv_for(s.dir, case, plan, "csv", probe=None if stdin else "p.csv") + ["--threads", str(t)]
                got = run(*base, "--max-state-bytes", total, stdin=probe if stdin else None)
                self.assertEqual(got.status, 0, "%s: refused at the formula %d (build %d + %s %d): %r" % (tag, total, build, holder, sum(items), got.stderr[:300]))
                low = run(*(argv_for(s.dir, case, plan, "json", probe=None if stdin else "p.csv")), "--threads", str(t), "--max-state-bytes", total - 1, stdin=probe if stdin else None)
                self.assertEqual((low.status, low.first_rule()), (8, "limit.state-too-large"), "%s: accepted one byte below the formula %d" % (tag, total))
                d = low.error()["detail"]
                self.assertEqual((d["holder"], d["build_bytes"], d["limit"]), (holder, build, total - 1), tag)
                self.assertEqual(d["held"], sum(items) - items[-1], "%s: the %s's count before the item that did not fit is not the formula's" % (tag, holder))
            under = run(*argv_for(s.dir, case, plan, "json"), "--max-state-bytes", build - 1)
            d = under.error()["detail"]
            self.assertEqual((under.first_rule(), d["holder"], d["held"]), ("limit.state-too-large", "join-build", build - [c for c in trace["build"] if c][-1]), "%s: the build alone" % tag)
            return build, items
        finally:
            s.cleanup()

    def test_the_references_trace_is_j2s_formula(self):
        s = Scratch()
        try:
            for case in R3.shapes()[:4]:
                write_case(s.dir, case)
                for jt in JTYPES:
                    plan = {"jtype": jt, "pairs": case.pairs, "prefix": case.prefix}
                    t = {}
                    R3.execute(plan, case.P, case.B, t)
                    f = R.accounted(str(s.dir / "b.csv"), case.pairs, jt)
                    self.assertEqual(sum(t["build"]), f["held"], (case.name, jt))
        finally:
            s.cleanup()

    def test_groups_share_the_budget_with_the_build(self):
        rng = random.Random(8)
        done = 0
        for case in R3.shapes():
            if case.awkward:
                continue
            for jt in JTYPES:
                for variant in range(3):
                    plan = {"jtype": jt, "pairs": case.pairs, "prefix": case.prefix}
                    roles, jh = R3.case_layout(case, jt)
                    texts = [n for n in jh if roles[n] in ("g", "t")]
                    ints = [n for n in jh if roles[n] == "v"]
                    if variant == 0:
                        plan["group"] = [(rng.choice(texts), "text")]
                    elif variant == 1:
                        plan["group"] = [(texts[0], "text"), (texts[-1], "text")]
                        plan["aggs"] = [("count", None)]
                    else:
                        plan["group"] = [(rng.choice(texts), "text")]
                        if rng.random() < 0.5:
                            plan["jw"] = R3.leaf(case.B[0][1], "!=", "x")
                        plan["where"] = R3.leaf(texts[0], "!=", "t3")
                    tag = "%s [%s] %d" % (case.name, jt, variant)
                    with self.subTest(tag):
                        if self.exact(case, plan, tag):
                            done += 1
        self.assertGreater(done, 60)

    def test_a_typed_group_key_costs_twelve(self):
        case = R3.shapes()[0]
        for jt in JTYPES:
            plan = {"jtype": jt, "pairs": case.pairs, "prefix": "", "group": [("pv", "int")]}
            with self.subTest(jt):
                got = self.exact(case, plan, "typed " + jt)
                if jt in ("inner", "left"):
                    self.assertIsNotNone(got)

    def test_sorted_rows_share_it_too(self):
        done = 0
        for case in R3.shapes():
            if case.awkward:
                continue
            for jt in JTYPES:
                roles, jh = R3.case_layout(case, jt)
                plan = {"jtype": jt, "pairs": case.pairs, "prefix": case.prefix, "order": [(jh[1], False, False)]}
                if len(jh) > 5:
                    plan["order"].append((jh[-1], True, False))
                tag = "%s [%s]" % (case.name, jt)
                with self.subTest(tag):
                    if self.exact(case, plan, tag):
                        done += 1
        self.assertGreater(done, 30)

    def test_a_using_record_is_held_whole_and_a_padded_one_is_the_probe_and_its_delimiters(self):
        case = R3.shapes()[13]                                   # using with a prefix
        for jt in ("inner", "left"):
            plan = {"jtype": jt, "pairs": case.pairs, "prefix": case.prefix, "order": [("g", False, False)]}
            self.assertIsNotNone(self.exact(case, plan, "using " + jt))

    def test_the_bound_is_exact_at_every_thread_count_and_for_a_pipe(self):
        case = R3.shapes()[0]
        plan = {"jtype": "inner", "pairs": case.pairs, "prefix": "", "group": [("pg", "text")]}
        self.exact(case, plan, "threads", threads=(1, 2, 6, 16))
        self.exact(case, plan, "stdin", stdin=True)

    def test_the_edges_of_the_growth_of_the_build_with_a_group_over_it(self):
        s = Scratch()
        try:
            rng = random.Random(6)
            J.write_csv(str(s.dir / "p.csv"), [b"pk", b"pv"], [[b"u%07x" % 5, b"1"], [b"u%07x" % 6, b"2"], [b"u%07x" % 7, b"3"]])
            for rows in (1 << 20, (1 << 20) + 1):
                brows = ([b"u%07x" % i, [b"x", b"y", b"z"][i % 3], b"%d" % (i % 100)] for i in range(rows))
                J.write_csv(str(s.dir / "b.csv"), [b"bk", b"bg", b"bv"], brows)
                pairs = [(0, 0, None, False)]
                f = R.accounted(str(s.dir / "b.csv"), pairs, "inner")
                self.assertEqual((f["rows"], f["distinct"]), (rows, rows))
                # the groups: bg is x for u...5? i % 3: 5 -> z, 6 -> x, 7 -> y: three groups of one byte, 5 bytes each
                total = f["held"] + 15
                argv = ["--root", s.dir, "--join", "b.csv", "--on", "pk=bk", "--group", "bg", "--agg", "count,sum:bv", "--format", "csv", "p.csv"]
                ok = run(*argv, "--max-state-bytes", total)
                self.assertEqual((ok.status, ok.stdout.decode().splitlines()), (0, ["bg,count,sum:bv", "x,1,6", "y,1,7", "z,1,5"]), rows)
                low = run(*argv[:-3], "--format", "json", "p.csv", "--max-state-bytes", total - 1)
                d = low.error()["detail"]
                self.assertEqual((low.first_rule(), d["holder"], d["build_bytes"], d["held"]), ("limit.state-too-large", "groups", f["held"], 10), rows)
                self.assertEqual(d["limit"], total - 1)
                os.unlink(s.dir / "b.csv")
        finally:
            s.cleanup()

    def test_the_help_says_one_budget(self):
        h = {f["name"]: f["help"] for f in introspect()["flags"]}["--max-state-bytes"]
        for word in ("join", "groups", "sorted rows", "shared"):
            self.assertIn(word, h)


class Explain(Fixture):
    """--explain says the split, the join-where, the shared budget and the plan, and refuses what the run refuses before a row."""

    def test_the_join_where_and_the_split(self):
        x = self.explain(["--join-where", "bg = 'y' and bv:int > 5", "--where", "pg = 'x' and pv:int > 1"])["join"]
        self.assertEqual(x["join_where"], ["bg = 'y'", "bv:int > 5"])
        self.assertEqual(x["where"], {"probe_before_join": ["pg = 'x'"], "build_before_build": [], "after_join": ["pv:int > 1"]})
        self.assertEqual(self.explain([])["join"]["join_where"], [])
        self.assertEqual(self.explain([])["join"]["where"], {"probe_before_join": [], "build_before_build": [], "after_join": []})
        x = self.explain(["--join-where", "(bg = 'y' or bg = 'x')"])["join"]
        self.assertEqual(x["join_where"], ["(bg = 'y' or bg = 'x')"])

    def test_the_plan_is_the_plan_of_the_groups_over_the_joined_header(self):
        d = self.explain(["--group", "bg", "--agg", "count,sum:bv", "--sort", "-count", "--top", "3"])
        self.assertEqual(d["plan"]["form"], "groups")
        self.assertEqual(d["output"]["columns"], ["bg", "count", "sum:bv"])
        self.assertEqual([c["column"]["name"] for c in d["plan"]["group"]], ["bg"])
        self.assertEqual(d["join"]["build_state"]["shared_with"], "groups")
        self.assertIn("every row", d["work"]["reads"])
        d = self.explain(["--order-by", "-bv:int"])
        self.assertEqual((d["plan"]["form"], d["join"]["build_state"]["shared_with"]), ("rows", "sorted rows"))
        d = self.explain(["--select", "pk"])
        self.assertIsNone(d["join"]["build_state"]["shared_with"])

    def test_the_threads_are_read_on_one_core_with_a_note(self):
        d = self.explain(["--group", "bg", "--threads", "4"])
        self.assertEqual(d["work"]["threads"]["requested"], 4)
        self.assertEqual(d["join"]["work"]["threads"], {"requested": 4, "effective": 1, "reason": "the join is read on one core"})
        self.assertIn("the join is read on one core", json.dumps(d))

    def test_the_notes(self):
        notes = " ".join(self.explain(["--where", "bg = 'y'"], jtype="left")["notes"])
        self.assertIn("removes the padded rows", notes)
        self.assertIn("--join-where", notes)
        notes = " ".join(self.explain(["--where", "pg = 'x' and bv:int > 1"])["notes"])
        self.assertIn("before the join", notes)
        notes = " ".join(self.explain(["--group", "bg"])["notes"])
        self.assertIn("--max-state-bytes", notes)

    def test_it_refuses_what_the_run_refuses_before_a_row(self):
        for extra in (["--join-where", "nope = 1"], ["--join-where", "bg = "], ["--join-where", "bg = 'y' and bv = '1' or bk = 'a'"], ["--group", "nope"], ["--where", "nope = 1"], ["--agg", "sum"]):
            with self.subTest(extra):
                run_ = self.go(extra, fmt="json")
                ex = self.go(["--explain", *extra], fmt=None)
                self.assertEqual((ex.status, ex.first_rule()), (run_.status, run_.first_rule()))
                a, b = ex.error(), run_.error()
                self.assertEqual((a["message"], a["hint"], a["detail"]), (b["message"], b["hint"], b["detail"]))


class Combinations(Fixture):
    """The flags among themselves."""

    def test_rename_and_prefix(self):
        r = self.go(["--join-prefix", "r_", "--rename", "r_bg=who", "--select", "pk,r_bg", "--where", "r_bg = 'y'"], on="pk=bk")
        self.assertEqual(self.rows(r), ["pk,who", "a,y", "a,y"])

    def test_the_same_names_on_both_sides(self):
        s = "k,g,v\na,x,1\nb,y,2\nc,x,3\n"
        r = self.go(["--join-prefix", "r_", "--group", "g,r_g", "--agg", "sum:v,sum:r_v"], probe=s, build="k,g,v\na,x,10\nb,x,20\nc,y,30\n", on="k")
        self.assertEqual(self.rows(r), ["g,r_g,sum:v,sum:r_v", "x,x,1,10", "x,y,3,30", "y,x,2,20"])
        r = self.go(["--group", "g"], probe=s, build="k,g,v\na,x,10\n", on="k", fmt="json")
        self.assertEqual(r.first_rule(), "join.column-collision")

    def test_from_standard_input(self):
        r = self.go(["--join-where", "bg = 'y'", "--group", "pg", "--agg", "count", "--where", "pg = 'x'"], stdin=self.PROBE.encode())
        self.assertEqual(self.rows(r), ["pg,count", "x,2"])

    def test_the_threads_do_not_change_the_bytes(self):
        a = self.go(["--group", "bg", "--agg", "count"])
        for t in ("2", "4", "16"):
            b = self.go(["--group", "bg", "--agg", "count", "--threads", t, "--parallel-min-bytes", "0", "--chunk-bytes", "5"])
            self.assertEqual(a.stdout, b.stdout, t)

    def test_max_join_rows_bounds_the_rows_before_the_groups(self):
        r = self.go(["--group", "bg", "--max-join-rows", "4"], fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-join-rows"))
        self.assertEqual(self.go(["--group", "bg", "--max-join-rows", "5"]).status, 0)

    def test_max_groups_and_max_sort_rows_still_apply(self):
        r = self.go(["--group", "pg,bg", "--max-groups", "2"], fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-groups"))
        r = self.go(["--order-by", "bg", "--max-sort-rows", "3"], fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-sort-rows"))


class Fuzz(unittest.TestCase):
    """F: no trap on any plan of the new alphabet, every refusal is a rule of the catalogue, every repair that is offered is accepted."""

    EXTRA = [["--join-where", "bk != 'a'"], ["--join-where", "bv:int > 3"], ["--join-where", "nope = 1"], ["--join-where", "bg = "], ["--join-where", "(bg = 'x' or bg = 'y') and bv != ''"], ["--join-where", "not (bg = 'x')"],
             ["--where", "pg = 'x' and bv:int > 0"], ["--where", "(pg = 'x') or bg = 'y'"], ["--where", "pg = 'x' and pv:int > 1 or pk = 'a'"], ["--join-where", "bg = 'x' and bk = 'a' or bv = '1'"], ["--where", "pv:int > 2 and pg != 'q'"], ["--where", "bg not in ('x')"],
             ["--group", "pg"], ["--group", "pg,bg", "--agg", "count,sum:bv"], ["--group", "bv:int"], ["--agg", "count"], ["--agg", "sum:bv,min:pv,max:pv,distinct:pt"], ["--group", "pg", "--sort", "-count"],
             ["--order-by", "bv:int"], ["--order-by", "-pv:int,bg", "--top", "3"], ["--top", "2"], ["--sort", "count"], ["--from", "1"], ["--limit", "2"], ["--rename", "pg=z"],
             ["--max-state-bytes", "50"], ["--max-state-bytes", "300"], ["--max-state-bytes", "2000"], ["--max-groups", "1"], ["--max-sort-rows", "2"], ["--max-join-rows", "5"], ["--select", "bg,pk"],
             ["--format", "jsonl"], ["--threads", "4"], ["--explain"], ["--report", "types"], ["--max-bytes", "60"]]

    def check(self, argv, r, rules, counts):
        self.assertNotIn(r.status, TRAPS, argv)
        self.assertGreaterEqual(r.status, 0, argv)
        if r.status == 0:
            counts["accepted"] += 1
            return
        counts["refused"] += 1
        try:
            errors = r.doc().get("errors") or []
        except (AssertionError, ValueError):
            tags = re.findall(rb"^table: ([a-z]+\.[a-z0-9-]+): ", r.stderr, re.M)
            self.assertTrue(tags, (argv, r.stdout[:300], r.stderr[:300]))
            for t in tags:
                self.assertIn(t.decode(), rules, argv)
            return
        self.assertTrue(errors, (argv, r.stdout[:300]))
        for e in errors:
            self.assertIn(e["rule"], rules, argv)
            repair = e.get("repair")
            if not repair:
                continue
            options = repair["options"] if repair["kind"] == "choose" else [repair] if repair["kind"] == "retry" else []
            for opt in options:
                counts["repairs"] += 1
                again = subprocess.run(opt["argv"], capture_output=True, input=b"", timeout=60)
                self.assertNotIn(again.returncode, TRAPS, opt["argv"])
                # (a repair mends one refusal: a call that had a refusal of its flags besides keeps it)
                if not any(x["rule"].startswith("args.") for x in errors):
                    self.assertNotIn(b'"rule":"args.', again.stdout, ("a repair is refused by the flags", argv, opt["argv"]))
                    self.assertNotIn(b"table: args.", again.stderr, ("a repair is refused by the flags", argv, opt["argv"]))

    def test_random_plans_over_random_files(self):
        rng = random.Random(20261016)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        counts = {"accepted": 0, "refused": 0, "repairs": 0}
        cases = R3.shapes(seed=21)
        s = Scratch()
        try:
            for i in range(500):
                case = rng.choice(cases)
                write_case(s.dir, case)
                if rng.random() < 0.15:
                    which = rng.choice(["p.csv", "b.csv"])
                    data = (s.dir / which).read_bytes()
                    cut = rng.randrange(len(data) + 1)
                    data = rng.choice([data[:cut], data + b'x,"y"z\n', data + b"1,2,3,4,5,6,7\n", data[:cut] + b'"' + data[cut:], data + b",,,\n", data + b",x,1,t\n", data + b",x,1,t\n"])
                    (s.dir / which).write_bytes(data)
                ph, bh = case.P[0], case.B[0]
                pairs = [(a, b, k, u) for a, b, k, u in case.pairs]
                jt = rng.choice(JTYPES)
                typed = rng.random() < 0.2
                on = R.on_arg([(a, b, "int" if typed else k, False) for a, b, k, u in pairs], [x.encode() for x in ph], [x.encode() for x in bh])
                extra = sum(rng.sample(self.EXTRA, rng.randint(1, 4)), [])
                if jt in ("semi", "anti"):
                    extra = [x for x in extra if x not in ("bg,pk",)]
                argv = ["--root", str(s.dir), "--join", "b.csv", "--on", on, "--join-type", jt] + extra
                probe = rng.choice([["p.csv"], ["p.csv"], []])
                r = run(*argv, *probe, stdin=b"pk,pg,pv,pt\nk1,x,1,t\n" if not probe else None)
                self.check(argv + probe, r, rules, counts)
        finally:
            s.cleanup()
        self.assertGreater(counts["accepted"], 100, counts)
        self.assertGreater(counts["refused"], 100, counts)
        self.assertGreater(counts["repairs"], 3, counts)


class Authority(unittest.TestCase):
    def test_manifest_check_passes(self):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check"], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_row_is_unchanged(self):
        d = json.loads(subprocess.run([binary(), "introspect"], capture_output=True, check=True).stdout)
        self.assertEqual(sorted(d["authority"]["effects"]), ["args", "conc", "dir_read", "err_write", "file_read", "fs_read", "heap", "io_read", "io_write"])


class Strace(unittest.TestCase):
    """On Linux, a join with groups starts no thread, opens no socket and opens no file for writing (the control: a threaded --select of the same file does)."""

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "strace is Linux only")
    def test_no_thread_no_socket_no_write(self):
        s = Scratch()
        try:
            case = R3.shapes()[0]
            write_case(s.dir, case)
            base = [binary(), "--root", str(s.dir), "--join", "b.csv", "--on", "pk=bk", "--group", "bg", "--agg", "count,sum:bv", "--threads", "4", "--chunk-bytes", "7", "--parallel-min-bytes", "0", "--format", "csv", "p.csv"]
            p = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3,socket,openat", *base], capture_output=True, input=b"")
            err = p.stderr.decode("latin-1")
            self.assertNotIn("CLONE_THREAD", err)
            self.assertNotIn("socket(", err)
            self.assertNotRegex(err, r"O_WRONLY|O_RDWR|O_CREAT")
            ctl = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3", binary(), "--root", str(s.dir), "--select", "pk", "--threads", "4", "--chunk-bytes", "7", "--parallel-min-bytes", "0", "--format", "csv", "p.csv"], capture_output=True, input=b"")
            self.assertIn("CLONE_THREAD", ctl.stderr.decode("latin-1"))
        finally:
            s.cleanup()


if __name__ == "__main__":
    unittest.main()
