"""J7 (docs/joins.md sections 6.2, 6.3, 9, 11 and 22, slice J7), the swap repair: a build that passes `--max-state-bytes` is refused with `limit.state-too-large`, and where the call can be run with
the two files exchanged and give the same rows, the refusal says how.

The gates, written before the code and each able to fail (on `main`'s binary the refusal's repair is `none` in every case, so every class below that expects an option fails there):

* S, the repair is mechanical: for inner joins over generated tables (text keys, `:int` keys, USING, two pairs, csv x csv, a JSON-lines probe or build, files with a delimiter of their own) and random
  plans (rows, `--where`, `--select`, `--group`, `--agg`, `--sort`, `--rename`), the call is refused at a bound the other file fits, the refusal has one option, and the option, run as it stands, SUCCEEDS
  and gives the answer of the original call under a larger bound: the same rows with the same columns, in the order of the exchanged definition (the other file's order, then this file's within a key),
  checked against a Python reference of that definition, and byte for byte the same when the answer does not depend on the order (a grouping).
* B, the boundary: the other file's accounted bytes as a build side, N, is accepted (the repair is offered and works) and N - 1 is not (the repair is `none`, and says the other file does not fit either).
* N, nothing is offered when the exchange would not give the same rows, each with its reason: a left, semi or anti join; the probe on standard input; `--join-prefix`; `--join-where`; a column named by
  position; a typed shared-name key; a page of rows; a probe not smaller than the build; a `--query`; a header with a name twice; a csv answer whose delimiter would change.
* The repair is well formed (the schema of the contract, one `choose` option whose `argv` is the call's arguments with the two files, the key pairs and the reader flags exchanged), `--explain` says it
  (`join.build_state.swap`), and no input reaches a panic (320 plans, tags in the catalogue, every offered option accepted by the flags and succeeding).
"""

import json
import os
import random
import re
import sys
import unittest

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import join_ref3 as R3
import join_ref5 as R5
import join_ref7 as R7
import join_ref2 as R
from harness import TRAPS, Scratch, binary, introspect, run, validate

BIG = 1 << 30
SEMI = ("csv", ";")


def refused(r):
    """The error of a refusal by the bound of the build."""
    assert r.status != 0, r.stdout[:300]
    e = r.error()
    assert e["rule"] == "limit.state-too-large", e
    assert e["detail"]["holder"] == "join-build", e
    return e


def option_of(testcase, r):
    """The one option of a refusal that offers the swap; the refusal is valid against the contract's schema."""
    testcase.assertEqual(validate(r), [], r.stdout[:400])
    e = refused(r)
    testcase.assertEqual(e["repair"]["kind"], "choose", e["repair"])
    testcase.assertEqual(len(e["repair"]["options"]), 1, e["repair"])
    opt = e["repair"]["options"][0]
    testcase.assertEqual(sorted(opt), ["argv"], opt)
    testcase.assertTrue(all(isinstance(x, str) for x in opt["argv"]), opt)
    return opt["argv"]


def reason_of(testcase, r):
    testcase.assertEqual(validate(r), [], r.stdout[:400])
    e = refused(r)
    testcase.assertEqual(e["repair"]["kind"], "none", e["repair"])
    return e["repair"]["reason"]


def again(argv, stdin=b""):
    return subprocess.run(argv, capture_output=True, input=stdin, timeout=120)


class Scene:
    """A case written under a scratch root, with the sides' formats."""

    def __init__(self, case, pside=("csv", ","), bside=("csv", ","), seed=1):
        self.case, self.pside, self.bside = case, pside, bside
        self.s = Scratch()
        self.d = self.s.dir
        R7.write_scene(self.d, case, pside, bside, random.Random(seed))

    def argv(self, plan, jtype="inner", fmt="json", bound=None, extra=(), operand=True):
        return R7.argv_of(self.d, self.case, plan, jtype, self.pside, self.bside, fmt, bound, extra, operand)

    def run(self, plan, jtype="inner", fmt="json", bound=None, extra=(), operand=True, stdin=None):
        return run(*self.argv(plan, jtype, fmt, bound, extra, operand), stdin=stdin)

    def cleanup(self):
        self.s.cleanup()


def scenes(seed=7):
    """(name, builder): builder() -> (case, pside, bside). The refusal and its repair are a JSON document, so every call is `--format json`; the delimiters of the files are how each is read."""
    rng = random.Random(seed)
    int_keys = [k.decode() for k in R.INT_SPELLINGS[:8]]
    return [
        ("csv x csv, text key", lambda: (R7.wide_case(rng, "text"), ("csv", ","), ("csv", ","))),
        ("typed :int key", lambda: (R7.wide_case(rng, "int", keys=int_keys, pairs=[(0, 0, "int", False)]), ("csv", ","), ("csv", ","))),
        ("USING, text key", lambda: (R7.wide_case(rng, "using", shared_key=True, pairs=[(0, 0, None, True)], using=True), ("csv", ","), ("csv", ","))),
        ("two pairs of text", lambda: (R7.wide_case(rng, "two", pairs=[(0, 0, None, False), (1, 1, None, False)]), ("csv", ","), ("csv", ","))),
        ("a JSON-lines probe", lambda: (R7.wide_case(rng, "jp"), ("jsonl", ","), ("csv", ","))),
        ("a JSON-lines build", lambda: (R7.wide_case(rng, "jb"), ("csv", ","), ("jsonl", ","))),
        ("both JSON lines", lambda: (R7.wide_case(rng, "jj"), ("jsonl", ","), ("jsonl", ","))),
        ("both with a semicolon", lambda: (R7.wide_case(rng, "ss"), SEMI, SEMI)),
        ("both with a tab", lambda: (R7.wide_case(rng, "tt"), ("csv", "\t"), ("csv", "\t"))),
        ("a semicolon probe, JSON-lines build", lambda: (R7.wide_case(rng, "sj"), SEMI, ("jsonl", ","))),
        ("a build with a delimiter of its own", lambda: (R7.wide_case(rng, "bd"), ("csv", ","), SEMI)),
        ("a probe with a delimiter of its own", lambda: (R7.wide_case(rng, "pd"), SEMI, ("csv", ","))),
        ("keys in other columns than the first", lambda: (R7.wide_case(rng, "kc", pk_at=2, bk_at=1), ("csv", ","), ("csv", ","))),
    ]


def describe(plan):
    return {k: (R3.render(v) if k == "where" else v) for k, v in plan.items() if k != "pairs"}


class Mechanical(unittest.TestCase):
    """S: every offered repair, run as it stands, succeeds and gives the answer of the original call under a larger bound."""

    counts = {"offered": 0, "grouped": 0, "same_bytes": 0}

    def check_plan(self, sc, plan, tag):
        """One plan on one scene: refused at the bound the other file fits, the one option accepted and giving the raised-bound answer. False when the plan is not a case of this class."""
        case = sc.case
        try:
            a_b, a_p = R7.bounds(plan, case)
            want = R3.execute(plan, case.P, case.B)
        except R3.Refused:
            return False
        if len(want[2]) > 900:
            return False                                  # (the default page of --format json is 1000 rows: the answers here are whole)
        grouped = R7.grouping(plan)
        bound = a_p + R7.group_slack(plan, case)
        if bound >= a_b:
            return False
        r = sc.run(plan, bound=bound)
        argv = option_of(self, r)
        ok = sc.run(plan, bound=BIG)
        self.assertEqual(ok.status, 0, "%s: the original call under a larger bound: %r" % (tag, ok.stdout[:300]))
        got = again(argv)
        self.assertEqual(got.returncode, 0, "%s: the offered repair did not succeed: %r %r" % (tag, got.stdout[:400], argv))
        self.assertEqual(got.stderr, b"", tag)
        self.assertEqual(validate(R7.as_result(argv, got)), [], tag)
        if grouped:
            self.assertEqual(got.stdout, ok.stdout, "%s: a grouping is the same bytes under the exchange" % tag)
            self.counts["same_bytes"] += 1
            self.counts["grouped"] += 1
        else:
            exp = R7.swap_expected(plan, case)
            self.assertEqual(exp[0], "ok", tag)
            data = json.loads(got.stdout)["data"]
            self.assertEqual((data["columns"], data["rows"]), (exp[1], exp[2]), "%s: the rows in the order of the exchanged definition" % tag)
            raised = ok.data()
            self.assertEqual(data["columns"], raised["columns"], "%s: the columns of the original call" % tag)
            self.assertEqual(R7.multiset(data["rows"]), R7.multiset(raised["rows"]), "%s: the same rows as the original under a larger bound" % tag)
            if data["rows"] == raised["rows"]:
                self.counts["same_bytes"] += 1
        self.counts["offered"] += 1
        return True

    def test_every_accepted_repair_gives_the_raised_bound_answer(self):
        rng = random.Random(20261017)
        for name, build in scenes():
            case, pside, bside = build()
            sc = Scene(case, pside, bside)
            try:
                got = 0
                tries = 0
                while got < 10 and tries < 400:
                    tries += 1
                    plan = R7.random_plan(rng, case)
                    if plan.get("order") is not None:
                        continue                          # (the sorted rows are held beside the build, in the same budget: the fuzz below has them)
                    tag = "%s %d %r" % (name, tries, describe(plan))
                    with self.subTest(tag):
                        if self.check_plan(sc, plan, tag):
                            got += 1
                self.assertGreaterEqual(got, 10, "%s: too few plans reached a refusal at a bound the other file fits" % name)
            finally:
                sc.cleanup()
        print("\nJ7 swap: %r" % self.counts, file=sys.stderr)
        self.assertGreaterEqual(self.counts["offered"], 120)
        self.assertGreaterEqual(self.counts["grouped"], 20)

    def test_the_option_is_the_call_with_the_two_files_the_pairs_and_the_flags_exchanged(self):
        case = R7.wide_case(random.Random(3), "argv", pairs=[(0, 0, "int", False)], keys=[k.decode() for k in R.INT_SPELLINGS[:6]])
        plan = {"jtype": "inner", "pairs": case.pairs, "select": ["pg", "bg", "w2x"]}
        a_b, a_p = R7.bounds(plan, case)
        pf, bf = ",".join(case.P[0]), ",".join(case.B[0])

        def option(pside, bside, fmt="json"):
            sc = Scene(case, pside, bside)
            try:
                argv = option_of(self, sc.run(plan, fmt=fmt, bound=a_p))
                self.assertEqual(again(argv).returncode, 0, argv)
                return argv, R7.pname(pside), R7.bname(bside)
            finally:
                sc.cleanup()

        def flag(argv, name):
            return argv[argv.index(name) + 1] if name in argv else None

        argv, pn, bn = option(("csv", ","), ("csv", ","))
        self.assertEqual(argv[0], binary())
        self.assertEqual((flag(argv, "--join"), argv[-1], flag(argv, "--on"), flag(argv, "--join-type")), (pn, bn, "bk=pk:int", "inner"))
        self.assertEqual([x for x in argv if x in ("--delimiter", "--join-delimiter", "--input-format", "--fields", "--join-input-format", "--join-fields")], [])
        # the delimiter shared by both files stays the answer's
        argv, pn, bn = option(SEMI, SEMI)
        self.assertEqual((flag(argv, "--delimiter"), flag(argv, "--join-delimiter")), (";", None))
        # different delimiters: the answer is json, so the delimiter is only how each file is read, and it goes with its file
        argv, pn, bn = option(SEMI, ("csv", ","))
        self.assertEqual((flag(argv, "--delimiter"), flag(argv, "--join-delimiter")), (",", ";"))
        # a JSON-lines build becomes the probe, with its declared fields; the csv probe becomes the build, read as csv
        argv, pn, bn = option(("csv", ","), ("jsonl", ","))
        self.assertEqual((flag(argv, "--input-format"), flag(argv, "--fields"), flag(argv, "--join-input-format"), flag(argv, "--join-fields")), ("jsonl", bf, "csv", None))
        # and the other way
        argv, pn, bn = option(("jsonl", ","), ("csv", ","))
        self.assertEqual((flag(argv, "--input-format"), flag(argv, "--fields"), flag(argv, "--join-input-format"), flag(argv, "--join-fields")), (None, None, "jsonl", pf))
        # both JSON lines: each side's fields go with its file
        argv, pn, bn = option(("jsonl", ","), ("jsonl", ","))
        self.assertEqual((flag(argv, "--input-format"), flag(argv, "--fields"), flag(argv, "--join-fields")), ("jsonl", bf, pf))
        # the delimiter of a csv file read beside a JSON-lines one is kept as the answer's
        argv, pn, bn = option(SEMI, ("jsonl", ","))
        self.assertEqual((flag(argv, "--delimiter"), flag(argv, "--input-format"), flag(argv, "--join-delimiter")), (";", "jsonl", None))

    def test_the_forms_with_an_equals_sign_are_written_back_as_they_were(self):
        # `--join=FILE`, `--on=PAIRS` and `--delimiter=;` are one argument each: the exchange replaces or drops the argument and keeps the form it replaces
        case = R7.wide_case(random.Random(5), "eq", pairs=[(0, 0, "int", False)], keys=[k.decode() for k in R.INT_SPELLINGS[:6]])
        plan = {"jtype": "inner", "pairs": case.pairs, "select": ["pg", "bg"]}
        a_b, a_p = R7.bounds(plan, case)
        for pside, bside in ((("csv", ","), ("csv", ",")), (SEMI, SEMI)):
            sc = Scene(case, pside, bside)
            try:
                argv = sc.argv(plan, bound=a_p)
                for flag in ("--join", "--on", "--delimiter"):
                    if flag in argv:
                        at = argv.index(flag)
                        argv[at:at + 2] = ["%s=%s" % (flag, argv[at + 1])]
                self.assertTrue(any(x.startswith("--join=") for x in argv) and any(x.startswith("--on=") for x in argv), argv)
                opt = option_of(self, run(*argv))
                self.assertIn("--join=%s" % R7.pname(pside), opt)
                self.assertIn("--on=bk=pk:int", opt)
                self.assertEqual(opt.count(R7.bname(bside)), 1)
                if pside == SEMI:
                    self.assertEqual([x for x in opt if x.startswith("--delimiter")], ["--delimiter"], opt)
                    self.assertEqual(opt[opt.index("--delimiter") + 1], ";")
                got = again(opt)
                self.assertEqual(got.returncode, 0, got.stdout[:300])
                want = R7.swap_expected(plan, case)
                self.assertEqual(json.loads(got.stdout)["data"]["rows"], want[2][:1000])    # (the default page of --format json)
            finally:
                sc.cleanup()

    def test_names_with_a_comma_a_backslash_a_hash_and_an_equals_sign_are_escaped(self):
        case = R7.wide_case(random.Random(6), "esc")
        case.P[0][:4] = ["p=k", "a,b", "#c", "d\\e"]
        case.B[0][:4] = ["#bk", "b,g", "z=w", "x"]
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            argv = ["--root", str(sc.d), "--join", "b.csv", "--on", "p\\=k=\\#bk", "--max-state-bytes", str(a_p), "p.csv"]
            opt = option_of(self, run(*argv))
            self.assertEqual(opt[opt.index("--on") + 1], "\\#bk=p\\=k")
            self.assertEqual(opt[opt.index("--select") + 1], "p=k,a\\,b,\\#c,d\\\\e,\\#bk,b\\,g,z=w,x," + ",".join(case.B[0][4:]))
            got = again(opt)
            self.assertEqual(got.returncode, 0, got.stdout[:300])
            data = json.loads(got.stdout)["data"]
            self.assertEqual(data["columns"], case.P[0] + case.B[0])
            want = R7.swap_expected(plan, case)
            self.assertEqual(data["rows"], want[2])
        finally:
            sc.cleanup()

    def test_the_header_of_the_original_is_kept_by_a_select_when_the_plan_has_none(self):
        case = R7.wide_case(random.Random(4), "cols")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            argv = option_of(self, sc.run(plan, bound=a_p))
            self.assertEqual(argv.count("--select"), 1)
            self.assertEqual(argv[argv.index("--select") + 1], ",".join(case.P[0] + case.B[0]))
            got = again(argv)
            self.assertEqual(got.returncode, 0)
            self.assertEqual(json.loads(got.stdout)["data"]["columns"], case.P[0] + case.B[0])
            # a plan that selects, or that groups, is not given another --select
            for plan in ({"jtype": "inner", "pairs": case.pairs, "select": ["bg", "pk"]}, {"jtype": "inner", "pairs": case.pairs, "group": [("pg", "text")], "aggs": [("count", None)]}):
                a_b, a_p = R7.bounds(plan, case)
                argv = option_of(self, sc.run(plan, bound=a_p + R7.group_slack(plan, case)))
                self.assertEqual(argv.count("--select"), 1 if plan.get("select") else 0, argv)
        finally:
            sc.cleanup()


class Boundary(unittest.TestCase):
    """B: the other file's accounted bytes as a build side, N, is accepted; N - 1 is not."""

    def test_accepted_at_n_refused_at_n_minus_one(self):
        for name, build in scenes(seed=3):
            case, pside, bside = build()
            sc = Scene(case, pside, bside)
            try:
                pk, pg, bg = case.P[0][0], case.P[0][1], case.B[0][1]
                plans = [{"jtype": "inner", "pairs": case.pairs, "select": [pk, bg]}, {"jtype": "inner", "pairs": case.pairs},
                         {"jtype": "inner", "pairs": case.pairs, "select": [pg, "w1x", pk], "where": R3.leaf(pg, "!=", "x")},
                         {"jtype": "inner", "pairs": case.pairs, "group": [(pg, "text")], "aggs": [("count", None)]}]
                for plan in plans:
                    a_b, a_p = R7.bounds(plan, case)
                    n = a_p + R7.group_slack(plan, case)
                    tag = "%s %r" % (name, describe(plan))
                    with self.subTest(tag):
                        self.assertGreater(a_b, n)
                        argv = option_of(self, sc.run(plan, bound=n))
                        got = again(argv)
                        self.assertEqual(got.returncode, 0, (tag, got.stdout[:300]))
                        if not R7.grouping(plan):
                            # one byte less: the other file does not fit either
                            reason = reason_of(self, sc.run(plan, bound=a_p - 1))
                            self.assertIn("does not fit", reason, reason)
                            self.assertIn("probe", reason, reason)
            finally:
                sc.cleanup()

    def test_the_bound_that_hits_both_sides_offers_nothing(self):
        case = R7.wide_case(random.Random(5), "both")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            for bound in (1, 100, 500):
                reason = reason_of(self, sc.run(plan, bound=bound))
                self.assertIn("does not fit", reason)
        finally:
            sc.cleanup()

    def test_the_repair_of_a_repair_is_not_offered_again(self):
        case = R7.wide_case(random.Random(6), "again")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            argv = option_of(self, sc.run(plan, bound=a_p))
            # a bound the exchanged call refuses: the other file is now the build and the probe is not smaller than it, so nothing is offered (the calls never go back and forth)
            tight = list(argv)
            tight[tight.index("--max-state-bytes") + 1] = str(a_p - 1)
            r = again(tight)
            self.assertEqual(r.returncode, 8, r.stdout[:300])
            e = json.loads(r.stdout)["error"]
            self.assertEqual((e["rule"], e["repair"]["kind"]), ("limit.state-too-large", "none"))
            self.assertIn("smaller", e["repair"]["reason"])
        finally:
            sc.cleanup()


class NotOffered(unittest.TestCase):
    """N: nothing is offered when the exchange would not give the same rows, and the refusal says why."""

    def base(self, **kw):
        case = R7.wide_case(random.Random(8), "n", **kw)
        return case, Scene(case)

    def test_left_semi_and_anti_are_never_exchanged(self):
        case, sc = self.base()
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            for jt in ("left", "semi", "anti"):
                with self.subTest(jt):
                    reason = reason_of(self, sc.run(plan, jtype=jt, bound=100))
                    self.assertIn(jt, reason)
                    self.assertIn("exchang", reason)
        finally:
            sc.cleanup()

    def test_standard_input_cannot_be_the_build_side(self):
        case, sc = self.base()
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            data = (sc.d / "p.csv").read_bytes()
            self.assertIn("standard input", reason_of(self, sc.run(plan, bound=a_p, operand=False, stdin=data)))
            self.assertIn("standard input", reason_of(self, run(*sc.argv(plan, bound=a_p, operand=False), "-", stdin=data)))
        finally:
            sc.cleanup()

    def test_a_prefix_names_the_build_columns(self):
        case = R7.wide_case(random.Random(9), "pre", same=True, prefix="r_")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs, "prefix": "r_"}
            a_b, a_p = R7.bounds(plan, case)
            self.assertIn("--join-prefix", reason_of(self, sc.run(plan, bound=a_p)))
        finally:
            sc.cleanup()

    def test_join_where_filters_the_build_file(self):
        case, sc = self.base()
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            r = sc.run(plan, bound=a_p, extra=["--join-where", "bg != 'q'"])
            self.assertIn("--join-where", reason_of(self, r))
        finally:
            sc.cleanup()

    def test_a_column_named_by_position_moves_with_the_exchange(self):
        case, sc = self.base()
        try:
            plan = {"jtype": "inner", "pairs": case.pairs, "select": ["pk"]}
            a_b, a_p = R7.bounds(plan, case)
            argv = sc.argv(plan, bound=a_p)
            argv[argv.index("--select") + 1] = "#1,#6"
            self.assertIn("position", reason_of(self, run(*argv)))
            argv = sc.argv({"jtype": "inner", "pairs": case.pairs}, bound=a_p, extra=["--where", "#2 != 'q'"])
            self.assertIn("position", reason_of(self, run(*argv)))
        finally:
            sc.cleanup()

    def test_a_typed_shared_name_key_shows_the_probe_spelling(self):
        case = R7.wide_case(random.Random(10), "tu", shared_key=True, keys=[k.decode() for k in R.INT_SPELLINGS[:6]], pairs=[(0, 0, "int", True)], using=True)
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            self.assertIn("shared-name", reason_of(self, sc.run(plan, bound=a_p)))
        finally:
            sc.cleanup()

    def test_a_page_of_rows_is_a_different_page_after_the_exchange(self):
        case, sc = self.base()
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            for extra in (["--limit", "5"], ["--from", "3"], ["--top", "4", "--order-by", "bg"]):
                with self.subTest(extra):
                    self.assertIn("page", reason_of(self, sc.run(plan, bound=a_p, extra=extra)))
            # the default page of --format json is a cap and not a window the caller asked for: the exchange is offered (docs/joins.md section 22, reading 7)
            option_of(self, sc.run(plan, bound=a_p))
            # a grouping is the same under the exchange, paged or not
            g = {"jtype": "inner", "pairs": case.pairs, "group": [("pg", "text")], "aggs": [("count", None)]}
            for extra in ([], ["--limit", "2"], ["--from", "1"], ["--top", "2", "--sort", "-count"]):
                argv = option_of(self, sc.run(g, bound=R7.bounds(g, case)[1] + R7.group_slack(g, case), extra=extra))
                self.assertEqual(again(argv).returncode, 0)
        finally:
            sc.cleanup()

    def test_a_probe_that_is_not_smaller_than_the_build_is_not_exchanged(self):
        case = R7.wide_case(random.Random(12), "big-probe", np_rows=140, nb_rows=24, extras=0, width=2)
        sc = Scene(case)
        try:
            self.assertGreater(os.path.getsize(sc.d / "p.csv"), os.path.getsize(sc.d / "b.csv"))
            plan = {"jtype": "inner", "pairs": case.pairs}
            self.assertIn("smaller", reason_of(self, sc.run(plan, bound=100)))
        finally:
            sc.cleanup()

    def test_a_probe_that_cannot_be_held_as_a_build_is_not_exchanged(self):
        case, sc = self.base()
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            a_b, a_p = R7.bounds(plan, case)
            good = (sc.d / "p.csv").read_text()
            # a row of the wrong width, and a cell that is not of its type
            (sc.d / "p.csv").write_text(good + "k1,x\n")
            self.assertIn("cannot be held", reason_of(self, sc.run(plan, bound=a_p)))
            (sc.d / "p.csv").write_text(good)
            argv = sc.argv(plan, bound=a_p)
            argv[argv.index("--on") + 1] = "pv=bv:int"
            head, _, rest = good.partition("\n")
            (sc.d / "p.csv").write_text(head + "\nk1,x,zz,t1\n" + rest)
            self.assertIn("cannot be held", reason_of(self, run(*argv)))
        finally:
            sc.cleanup()

    def test_a_header_with_an_empty_name_cannot_be_kept_in_order_by_name(self):
        case, sc = self.base()
        try:
            (sc.d / "p.csv").write_text(",pg,pv,pt\nk1,a,b,c\nk2,d,e,f\n")
            r = run("--root", sc.d, "--join", "b.csv", "--on", "#1=bk", "--max-state-bytes", "300", "p.csv")
            self.assertIn("is empty, so", reason_of(self, r))
        finally:
            sc.cleanup()

    def test_files_of_the_same_size_are_not_exchanged(self):
        s = Scratch()
        try:
            s.write("p.csv", "pk,pg\n" + "".join("k%02d,x\n" % i for i in range(40)))
            s.write("b.csv", "bk,bg\n" + "".join("k%02d,y\n" % i for i in range(40)))
            self.assertEqual(os.path.getsize(s.dir / "p.csv"), os.path.getsize(s.dir / "b.csv"))
            r = run("--root", s.dir, "--join", "b.csv", "--on", "pk=bk", "--max-state-bytes", "300", "p.csv")
            self.assertIn("smaller", reason_of(self, r))
        finally:
            s.cleanup()

    def test_a_query_is_not_rewritten(self):
        case, sc = self.base()
        try:
            r = run("--root", sc.d, "--max-state-bytes", "100", "--query", "select a.pk, b.bg from 'p.csv' as a join 'b.csv' as b on a.pk = b.bk")
            self.assertIn("--query", reason_of(self, r))
        finally:
            sc.cleanup()

    def test_a_header_with_a_name_twice_cannot_be_kept_in_order_by_name(self):
        case, sc = self.base()
        try:
            (sc.d / "p.csv").write_text("pk,pg,pg,pt\nk1,a,b,c\nk2,d,e,f\n")
            r = run("--root", sc.d, "--join", "b.csv", "--on", "pk=bk", "--max-state-bytes", "300", "p.csv")
            self.assertIn("twice", reason_of(self, r))
        finally:
            sc.cleanup()

    def test_a_csv_answer_would_be_written_with_the_other_files_delimiter(self):
        # (a csv refusal is a line on standard error and carries no repair; `--explain` says what the exchange would be)
        case = R7.wide_case(random.Random(17), "dl")
        sc = Scene(case, SEMI, ("csv", ","))
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            r = run(*sc.argv(plan, fmt="csv"), "--explain")
            s = r.data()["explain"]["join"]["build_state"]["swap"]
            self.assertEqual(s["offered"], False)
            self.assertIn("delimiter", s["reason"])
            # as json it is only how each file is read
            self.assertTrue(run(*sc.argv(plan), "--explain").data()["explain"]["join"]["build_state"]["swap"]["offered"])
        finally:
            sc.cleanup()


class Explain(unittest.TestCase):
    """`--explain` says what the refusal would do: `join.build_state.swap` is `{offered, reason}`."""

    def swap_of(self, sc, plan, **kw):
        r = run(*sc.argv(plan, **kw), "--explain")
        self.assertEqual(r.status, 0, r.stdout[:400])
        self.assertEqual(validate(r), [], r.stdout[:300])
        return r.data()["explain"]["join"]["build_state"]["swap"]

    def test_the_object(self):
        case = R7.wide_case(random.Random(13), "ex")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            s = self.swap_of(sc, plan)
            self.assertEqual(sorted(s), ["offered", "reason"])
            self.assertEqual(s, {"offered": True, "reason": "inner join and the probe file is smaller on disk"})
            for jt in ("left", "semi", "anti"):
                s = self.swap_of(sc, plan, jtype=jt)
                self.assertEqual((s["offered"], sorted(s)), (False, ["offered", "reason"]))
                self.assertIn(jt, s["reason"])
            self.assertIn("--join-where", self.swap_of(sc, plan, extra=["--join-where", "bg != 'q'"])["reason"])
            self.assertIn("page", self.swap_of(sc, plan, extra=["--limit", "3"])["reason"])
            s = self.swap_of(sc, plan, operand=False)
            self.assertEqual(s["offered"], False)
            self.assertIn("standard input", s["reason"])
        finally:
            sc.cleanup()

    def test_the_old_sentence_is_gone(self):
        case = R7.wide_case(random.Random(14), "ex2")
        sc = Scene(case)
        try:
            r = run(*sc.argv({"jtype": "inner", "pairs": case.pairs}), "--explain")
            self.assertNotIn(b"arrives with a later slice", r.stdout)
        finally:
            sc.cleanup()

    def test_a_probe_not_smaller_says_so(self):
        case = R7.wide_case(random.Random(15), "ex3", np_rows=140, nb_rows=24, extras=0, width=2)
        sc = Scene(case)
        try:
            s = self.swap_of(sc, {"jtype": "inner", "pairs": case.pairs})
            self.assertEqual(s["offered"], False)
            self.assertIn("smaller", s["reason"])
        finally:
            sc.cleanup()

    def test_the_explain_agrees_with_the_refusal(self):
        rng = random.Random(16)
        agreed = 0
        for name, build in scenes(seed=9):
            case, pside, bside = build()
            sc = Scene(case, pside, bside)
            try:
                for i in range(8):
                    plan = R7.random_plan(rng, case)
                    if plan.get("order") is not None:
                        continue
                    try:
                        a_b, a_p = R7.bounds(plan, case)
                    except R3.Refused:
                        continue
                    n = a_p + R7.group_slack(plan, case)
                    if n >= a_b:
                        continue
                    s = self.swap_of(sc, plan, bound=n)
                    # offered by --explain: the static conditions hold; the refusal then offers it because the other file fits
                    self.assertTrue(s["offered"], (name, describe(plan), s))
                    self.assertEqual(refused(sc.run(plan, bound=n))["repair"]["kind"], "choose", (name, describe(plan)))
                    agreed += 1
            finally:
                sc.cleanup()
        self.assertGreaterEqual(agreed, 20)


class Fuzz(unittest.TestCase):
    """No input reaches a panic: random plans at random bounds, with junk flags, over the scenes: no trap, every refusal tagged with a rule of the catalogue, every offered option accepted by the flags and
    succeeding."""

    JUNK = [[], [], [], ["--join-prefix", "r_"], ["--join-where", "bg != 'q'"], ["--limit", "5"], ["--threads", "4"], ["--select", "#1"], ["--max-rows", "10"], ["--max-join-rows", "20"],
            ["--absent", "empty"], ["--join-delimiter", ";"], ["--max-line-bytes", "40"]]

    def test_three_hundred_plans(self):
        rng = random.Random(20261018)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        counts = {"runs": 0, "refused": 0, "offered": 0, "none": 0, "ok": 0}
        scs = [(n, Scene(*b())) for n, b in scenes(seed=21)]
        try:
            while counts["runs"] < 320:
                name, sc = rng.choice(scs)
                case = sc.case
                plan = R7.random_plan(rng, case, rng.choice(["inner", "inner", "inner", "left", "semi", "anti"]))
                try:
                    a_b, a_p = R7.bounds(plan, case)
                except R3.Refused:
                    a_b, a_p = 2000, 500
                bound = rng.choice([1, 50, a_p - 1, a_p, a_p + 1, a_p + R7.group_slack(plan, case), (a_p + a_b) // 2, a_b - 1, a_b, a_b + 10, BIG])
                bound = max(1, min(bound, BIG))
                junk = rng.choice(self.JUNK)
                fmt = rng.choice(["json", "json", "csv"])
                argv = sc.argv(plan, jtype=plan["jtype"], fmt=fmt, bound=bound, extra=junk)
                r = run(*argv)
                counts["runs"] += 1
                tag = repr(argv[1:])
                self.assertNotIn(r.status, TRAPS, tag)
                if fmt == "json":
                    self.assertEqual(validate(r), [], tag)
                if r.status == 0:
                    counts["ok"] += 1
                    continue
                counts["refused"] += 1
                if fmt == "json":
                    errs = r.doc().get("errors") or []
                else:
                    tags = re.findall(rb"^table: ([a-z]+\.[a-z0-9-]+): ", r.stderr, re.M)
                    self.assertTrue(tags, (tag, r.stderr[:300]))
                    for t in tags:
                        self.assertIn(t.decode(), rules, tag)
                    errs = []
                for e in errs:
                    self.assertIn(e["rule"], rules, tag)
                    rep = e.get("repair")
                    if not rep:
                        continue
                    opts = rep["options"] if rep["kind"] == "choose" else [rep] if rep["kind"] == "retry" else []
                    for opt in opts:
                        got = again(opt["argv"])
                        self.assertNotIn(got.returncode, TRAPS, (tag, opt["argv"]))
                        self.assertNotIn(b'"rule":"args.', got.stdout, ("a repair is refused by the flags", tag, opt["argv"]))
                        self.assertNotIn(b"table: args.", got.stderr, ("a repair is refused by the flags", tag, opt["argv"]))
                        if e["rule"] == "limit.state-too-large" and e["detail"].get("holder") == "join-build":
                            counts["offered"] += 1
                            # an offered swap succeeds. The only other answers: what the call would have been refused for with a larger bound (a typed cell, --max-join-rows, a page that does not exist...), or the
                            # refusal of the groups or of the sorted rows, which share the flag with the build
                            if got.returncode != 0:
                                big = run(*sc.argv(plan, jtype=plan["jtype"], fmt=fmt, bound=BIG, extra=junk))
                                if big.status == 0:
                                    holder = json.loads(got.stdout)["error"]["detail"].get("holder")
                                    self.assertIn(holder, ("groups", "sort"), ("an offered swap must succeed", tag, got.stdout[:300], opt["argv"]))
                    if e["rule"] == "limit.state-too-large" and rep["kind"] == "none":
                        counts["none"] += 1
        finally:
            for _, sc in scs:
                sc.cleanup()
        print("\nJ7 fuzz: %r" % counts, file=sys.stderr)
        self.assertGreaterEqual(counts["offered"], 15)
        self.assertGreaterEqual(counts["none"], 15)
        self.assertGreaterEqual(counts["ok"], 15)


if __name__ == "__main__":
    unittest.main()
