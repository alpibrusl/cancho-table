"""J7 (docs/joins.md sections 6.2, 11 and 22, slice J7), the pruned build: the build holds, for each row, the record of the `--join` file with the cells of the columns the plan does not name left empty
(the delimiters stay, so the cells keep their places), and the accounted bytes are the formula of section 6.2 over the records held.

The gates, written before the code and each able to fail (on `main`'s binary the build holds the whole record, so the accounting and the memory classes fail there; the identity class passes on both, which is its point):

* A, the accounting: the program's count of the accounted bytes (the refusal one byte below the bound: `detail.held`, `detail.rows_held`) is the formula computed here from the plan and the files by another
  program; the bound is exact (a build of N bytes is accepted at N and refused at N - 1), for inner and left joins over text, `:int` and composite keys, USING, csv and JSON-lines sides, each with the columns
  the plan names; and a build accepted before is accepted now (the unpruned formula is never refused).
* I, identity: for every plan of a generator that varies the form (rows, `--select`, `--group`, `--agg`, `--order-by`, `--top`, `--sort`, `--rename`), `--where` (the split included) and `--join-where`, the
  join type, USING, typed and composite keys, csv or JSON-lines sides, quoted cells and records over several lines, one thread or four, a file or standard input, the answer is the answer of the Python
  reference and, when `MAIN_TABLE` names the binary of `main` (which holds the whole record), the same BYTES as `main`'s: the pair of runs of the design.
* M, memory: on a build of 150 columns of which the plan names two, the peak resident set (wait4 of a small launcher, the minimum of 3) is under half of the same call that names every column, and under half
  of `main`'s when `MAIN_TABLE` names it; the numbers are printed.
"""

import json
import os
import random
import sys
import tempfile
import unittest

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import join_ref as J
import join_ref2 as R
import join_ref3 as R3
import join_ref5 as R5
import join_ref7 as R7
from harness import ROOT, TRAPS, Scratch, binary, run, validate
from test_join_j7 import BIG, Scene, again, describe

MAIN_TABLE = os.environ.get("MAIN_TABLE")
JTYPES = ("inner", "left", "semi", "anti")
BIGPAGE = ["--limit", "1000000", "--max-bytes", "67108864"]


def main_run(argv, stdin=b""):
    if not MAIN_TABLE or not os.path.exists(MAIN_TABLE):
        return None
    return subprocess.run([MAIN_TABLE, *argv], capture_output=True, input=stdin, timeout=120)


def costs(plan, case, pruned):
    """The cost of each held row of the build, in the order they were incurred (docs/joins.md 6.2): (record + 16) and, for a new key, (key + 60)."""
    trace = {"keep": R7.keep_of(plan, case, "build")} if pruned else {"keep": None}
    R3.execute(plan, case.P, case.B, trace)
    return trace.get("build", [])


class Accounting(unittest.TestCase):
    """A: the accounted bytes of the build are those of the records it holds, with the columns the plan names."""

    def test_the_bound_is_exact_and_the_held_bytes_are_the_formula(self):
        rng = random.Random(20261019)
        seen = {"plans": 0, "smaller": 0}
        shapes = [("text", R7.wide_case(rng, "a")), ("int", R7.wide_case(rng, "b", keys=[k.decode() for k in R.INT_SPELLINGS[:8]], pairs=[(0, 0, "int", False)])),
                  ("two pairs", R7.wide_case(rng, "c", pairs=[(0, 0, None, False), (1, 1, None, False)])), ("using", R7.wide_case(rng, "d", shared_key=True, pairs=[(0, 0, None, True)], using=True)),
                  ("prefix", R7.wide_case(rng, "e", same=True, prefix="r_"))]
        sides = [(("csv", ","), ("csv", ",")), (("csv", ","), ("jsonl", ",")), (("jsonl", ","), ("csv", ";"))]
        for name, case in shapes:
            for pside, bside in sides:
                sc = Scene(case, pside, bside)
                try:
                    done = 0
                    tries = 0
                    while done < 5 and tries < 80:
                        tries += 1
                        jt = rng.choice(["inner", "inner", "left"])
                        plan = R3.random_plan(rng, case, jt)
                        if R7.grouping(plan) or plan.get("order") is not None:
                            continue
                        plan.pop("rename", None)
                        try:
                            pruned = costs(plan, case, True)
                            whole = costs(plan, case, False)
                        except R3.Refused:
                            continue
                        n, m = sum(pruned), sum(whole)
                        if not pruned or len(pruned) < 2:
                            continue
                        tag = "%s %s/%s %s %r" % (name, pside[0], bside[0], jt, describe(plan))
                        with self.subTest(tag):
                            ok = sc.run(plan, jtype=jt, bound=n)
                            self.assertEqual(ok.status, 0, "%s: a build of %d accounted bytes is accepted at %d: %r" % (tag, n, n, ok.stdout[:300]))
                            r = sc.run(plan, jtype=jt, bound=n - 1)
                            e = r.error()
                            self.assertEqual((r.status, e["rule"], e["detail"]["holder"]), (8, "limit.state-too-large", "join-build"), tag)
                            self.assertEqual((e["detail"]["held"], e["detail"]["limit"]), (n - pruned[-1], n - 1), "%s: the bytes held before the row that did not fit" % tag)
                            self.assertEqual(e["detail"]["rows_held"], len(pruned) - 1, tag)
                            # what was accepted before is accepted now
                            self.assertEqual(sc.run(plan, jtype=jt, bound=m).status, 0, tag)
                        done += 1
                        seen["plans"] += 1
                        seen["smaller"] += n < m
                finally:
                    sc.cleanup()
        print("\nJ7 accounting: %r" % seen, file=sys.stderr)
        self.assertGreaterEqual(seen["plans"], 60)
        self.assertGreaterEqual(seen["smaller"], 30, "the pruning must change the accounted bytes of most plans that name few columns")

    def test_a_plan_that_names_every_column_holds_the_whole_record(self):
        case = R7.wide_case(random.Random(31), "all")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs}
            whole = sum(costs(plan, case, False))
            self.assertEqual(sum(costs(plan, case, True)), whole)
            self.assertEqual(sc.run(plan, bound=whole).status, 0)
            e = sc.run(plan, bound=whole - 1).error()
            self.assertEqual(e["rule"], "limit.state-too-large")
        finally:
            sc.cleanup()

    def test_a_plan_that_leaves_out_only_the_key_holds_the_whole_record(self):
        # pruning is on when some column that is not a key is not named: dropping the key alone would save its bytes and cost the work of a pruned record (docs/joins.md section 22, reading 3)
        case = R7.wide_case(random.Random(34), "keyonly", extras=2)
        sc = Scene(case)
        try:
            names = [h for h in case.B[0] if h != "bk"]
            plan = {"jtype": "inner", "pairs": case.pairs, "select": ["pk"] + names}
            self.assertIsNone(R7.keep_of(plan, case, "build"))
            whole = sum(costs(plan, case, False))
            self.assertEqual(sum(costs(plan, case, True)), whole)
            self.assertEqual(sc.run(plan, bound=whole).status, 0)
            self.assertEqual(sc.run(plan, bound=whole - 1).status, 8)
            # and one column more left out is pruned
            plan2 = dict(plan, select=["pk"] + names[1:])
            self.assertLess(sum(costs(plan2, case, True)), whole)
            self.assertEqual(sc.run(plan2, bound=sum(costs(plan2, case, True))).status, 0)
            self.assertEqual(sc.run(plan2, bound=sum(costs(plan2, case, True)) - 1).status, 8)
        finally:
            sc.cleanup()

    def test_what_the_plan_names_decides_what_is_held(self):
        case = R7.wide_case(random.Random(32), "names")
        sc = Scene(case)
        try:
            base = {"jtype": "inner", "pairs": case.pairs}
            n = lambda plan: sum(costs(plan, case, True))
            held = lambda plan, bound: sc.run(plan, bound=bound)
            one = dict(base, select=["pk", "bg"])
            two = dict(base, select=["pk", "bg", "w5x"])
            where = dict(base, select=["pk"], where=R3.leaf("w3x", "!=", "q"))
            order = dict(base, select=["pk"], order=[("bt", False, False)])
            agg = dict(base, group=[("pg", "text")], aggs=[("distinct", "w1x")])
            key_only = dict(base, select=["pk"])
            sizes = {k: n(p) for k, p in dict(one=one, two=two, where=where, order=order, agg=agg, key_only=key_only, every=base).items()}
            # a column that is read only by --where is held; a column that nothing names is not; the key's own column is held only when named
            self.assertLess(sizes["key_only"], sizes["one"])
            self.assertLess(sizes["one"], sizes["two"])
            self.assertLess(sizes["two"], sizes["every"])
            self.assertLess(sizes["key_only"], sizes["where"])
            for plan in (one, two, key_only):
                self.assertEqual(sc.run(plan, bound=n(plan)).status, 0)
                self.assertEqual(sc.run(plan, bound=n(plan) - 1).status, 8)
            # the groups share the flag: the build then the groups' keys
            self.assertEqual(sc.run(agg, bound=sizes["agg"] + 65536).status, 0)
        finally:
            sc.cleanup()

    def test_quoted_cells_are_held_with_their_quotes_and_counted_so(self):
        # raw records: a kept cell that is quoted costs its quotes, a dropped one costs nothing, a key that is quoted is held unquoted
        recs = [b'"x,1",p,"q""r",s', b'y,"multi\nline",t,"u,v"', b'"say ""hi""",a,b,c', b'y,"",,"z"', b'"x,1","k,k","",d']
        s = Scratch()
        try:
            s.write("b.csv", b"k,a,b,c\n" + b"\n".join(recs) + b"\n")
            s.write("p.csv", b'id,k\n1,"x,1"\n2,y\n3,"say ""hi"""\n')
            for sel, cols in (("id,a", {1}), ("id,b", {2}), ("id,c", {3}), ("id,a,c", {1, 3}), ("id,k", set()), ("id", set())):
                keys, held, costs = set(), 0, []
                for r in recs:
                    key = R7.raw_key(r, 0)
                    cost = R7.raw_pruned_len(r, cols) + 16 + (0 if key in keys else len(key) + 60)
                    keys.add(key)
                    costs.append(cost)
                n = sum(costs)
                a = ["--root", s.dir, "--join", "b.csv", "--on", "k", "--select", sel, "--max-state-bytes", n, "p.csv"]
                self.assertEqual(run(*a).status, 0, (sel, n))
                a[-2] = n - 1
                r = run(*a)
                e = r.error()
                self.assertEqual((e["rule"], e["detail"]["held"], e["detail"]["rows_held"]), ("limit.state-too-large", n - costs[-1], len(recs) - 1), (sel, n, costs))
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k", "--select", "id,c", "p.csv")
            self.assertEqual(r.data()["rows"], [["1", "s"], ["1", "d"], ["2", "u,v"], ["2", "z"], ["3", "c"]])
        finally:
            s.cleanup()

    def test_a_plan_with_a_join_where_does_not_name_the_columns_it_reads_before_the_row_is_held(self):
        case = R7.wide_case(random.Random(33), "jw")
        sc = Scene(case)
        try:
            plan = {"jtype": "inner", "pairs": case.pairs, "select": ["pk", "bg"], "jw": R3.leaf("bt", "!=", "t5")}
            pruned, whole = costs(plan, case, True), costs(plan, case, False)
            self.assertLess(sum(pruned), sum(whole))
            for n in (sum(pruned), sum(pruned) - 1):
                r = sc.run(plan, bound=n)
                self.assertEqual(r.status, 0 if n == sum(pruned) else 8, n)
            # and a --join-where on a column the answer also shows is held
            plan2 = dict(plan, select=["pk", "bt"])
            self.assertEqual(sc.run(plan2, bound=sum(costs(plan2, case, True))).status, 0)
        finally:
            sc.cleanup()


def judge_plan(testcase, d, case, plan, tag, probe_bytes, pside=("csv", ","), bside=("csv", ",")):
    """The tool against the reference for one plan: json, csv, 4 threads with tiny ranges, standard input; and the bytes of `main`'s binary when MAIN_TABLE names it."""
    try:
        want = ("ok",) + R3.execute(plan, case.P, case.B)[1:]
    except R3.Refused as r:
        want = ("refuse", r.info)

    def argv(fmt, operand=True):
        a = R7.argv_of(d, case, plan, plan["jtype"], pside, bside, fmt, None, [], operand)
        if fmt == "json" and plan.get("limit") is None:
            a += BIGPAGE
        elif fmt == "json" and "--max-bytes" not in a:
            a += ["--max-bytes", "67108864"]
        return a

    j = run(*argv("json"))
    if want[0] == "ok":
        testcase.assertEqual(j.status, 0, "%s: %r" % (tag, j.stdout[:400] + j.stderr[:300]))
        testcase.assertEqual(validate(j), [], tag)
        data = j.data()
        testcase.assertEqual((data["columns"], data["rows"]), (want[1], want[2]), tag)
        c = run(*argv("csv"))
        delim = pside[1] if pside[0] == "csv" else ","
        testcase.assertEqual((c.status, c.stdout), (0, R7.csv_bytes(want[1], want[2], delim)), "%s: the csv bytes" % tag)
        s = run(*argv("csv", operand=False), stdin=probe_bytes)
        testcase.assertEqual((s.status, s.stdout), (0, c.stdout), "%s: standard input" % tag)
        t = run(*argv("csv"), "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7")
        testcase.assertEqual((t.status, t.stdout), (0, c.stdout), "%s: threads" % tag)
    else:
        info = want[1]
        testcase.assertEqual(j.status, 8, "%s: wanted a refusal %r, got %r" % (tag, info, j.stdout[:400]))
        e = j.error()
        testcase.assertEqual((e["rule"], e["detail"].get("context")), (info["rule"], info["context"]), "%s: %r" % (tag, e))
        testcase.assertEqual(e["detail"].get("value"), info["value"], tag)
    return j, argv


class Identity(unittest.TestCase):
    """I: the answer does not depend on which columns the build holds."""

    counts = {"plans": 0, "ok": 0, "refused": 0, "main": 0}

    def one(self, d, case, plan, tag, probe, pside, bside):
        j, argv = judge_plan(self, d, case, plan, tag, probe, pside, bside)
        self.counts["plans"] += 1
        self.counts["ok" if j.status == 0 else "refused"] += 1
        if MAIN_TABLE and os.path.exists(MAIN_TABLE):
            for fmt, operand in (("json", True), ("csv", True), ("csv", False)):
                a = R7.argv_of(d, case, plan, plan["jtype"], pside, bside, fmt, None, [], operand)
                if fmt == "json" and plan.get("limit") is None:
                    a += BIGPAGE
                for extra in ([], ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7"]):
                    mine = run(*a, *extra, stdin=probe if not operand else None)
                    theirs = main_run([*a, *extra], probe if not operand else b"")
                    self.assertEqual((mine.status, mine.stdout, mine.stderr), (theirs.returncode, theirs.stdout, theirs.stderr), "%s: the bytes of main's binary (%s %s)" % (tag, fmt, extra))
                    self.counts["main"] += 1

    def test_the_shapes_for_the_four_join_types(self):
        rng = random.Random(20261020)
        for case in R3.shapes():
            s = Scratch()
            try:
                from test_join_j3 import write_case
                write_case(s.dir, case)
                probe = (s.dir / "p.csv").read_bytes()
                for jt in JTYPES:
                    for i in range(4):
                        plan = R3.random_plan(rng, case, jt)
                        tag = "%s [%s] %d %r" % (case.name, jt, i, describe(plan))
                        with self.subTest(tag):
                            self.one(s.dir, case, plan, tag, probe, ("csv", ","), ("csv", ","))
            finally:
                s.cleanup()

    def test_wide_builds_in_every_format(self):
        rng = random.Random(20261021)
        formats = [(("csv", ","), ("csv", ",")), (("jsonl", ","), ("csv", ",")), (("csv", ","), ("jsonl", ",")), (("jsonl", ","), ("jsonl", ",")), (("csv", ";"), ("csv", ";")), (("csv", ","), ("csv", ";")), (("csv", "\t"), ("jsonl", ","))]
        int_keys = [k.decode() for k in R.INT_SPELLINGS[:8]]
        for fi, (pside, bside) in enumerate(formats):
            for name, mk in (("text", lambda: R7.wide_case(rng, "t")), ("int", lambda: R7.wide_case(rng, "i", keys=int_keys, pairs=[(0, 0, "int", False)])),
                             ("two", lambda: R7.wide_case(rng, "w", pairs=[(0, 0, None, False), (1, 1, None, False)])), ("using", lambda: R7.wide_case(rng, "u", shared_key=True, pairs=[(0, 0, None, True)], using=True)),
                             ("prefix", lambda: R7.wide_case(rng, "p", same=True, prefix="r_"))):
                case = mk()
                sc = Scene(case, pside, bside, seed=fi)
                try:
                    probe = (sc.d / R7.pname(pside)).read_bytes()
                    for jt in JTYPES:
                        for i in range(2):
                            plan = R3.random_plan(rng, case, jt)
                            tag = "%s %s/%s [%s] %d %r" % (name, pside, bside, jt, i, describe(plan))
                            with self.subTest(tag):
                                self.one(sc.d, case, plan, tag, probe, pside, bside)
                finally:
                    sc.cleanup()
        print("\nJ7 identity: %r (MAIN_TABLE %s)" % (self.counts, MAIN_TABLE), file=sys.stderr)
        self.assertGreaterEqual(self.counts["plans"], 300)
        self.assertGreaterEqual(self.counts["ok"], 150)
        if MAIN_TABLE and os.path.exists(MAIN_TABLE):
            self.assertGreaterEqual(self.counts["main"], 1500)

    def test_quoted_cells_and_records_over_several_lines_in_a_dropped_column(self):
        # the cell of a column the plan does not name may hold a quote, the delimiter, a line break: it is left out of the held record whole
        s = Scratch()
        try:
            s.write("p.csv", "id,k\n1,a\n2,b\n3,c\n")
            s.write("b.csv", 'k,junk,keep,more\na,"x,y","one\ntwo","q""q"\nb,"say ""hi""",plain,"line1\nline2\nline3"\nc,,"",\na,"",z,"t,u"\n')
            for sel in ("id,keep", "id,more", "id,k", "id,junk,more", "keep"):
                a = ["--root", s.dir, "--join", "b.csv", "--on", "k", "--select", sel, "--format", "csv", "p.csv"]
                r = run(*a)
                self.assertEqual(r.status, 0, (sel, r.stderr))
                m = main_run([str(x) for x in a])
                if m is not None:
                    self.assertEqual((r.status, r.stdout), (m.returncode, m.stdout), sel)
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k", "--select", "id,keep,more", "p.csv")
            self.assertEqual(r.data()["rows"], [["1", "one\ntwo", 'q"q'], ["1", "z", "t,u"], ["2", "plain", "line1\nline2\nline3"], ["3", "", ""]])
            # the shortest record the build can hold: nothing named but the key
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k", "--select", "id", "p.csv")
            self.assertEqual(r.data()["rows"], [["1"], ["1"], ["2"], ["3"]])
        finally:
            s.cleanup()

    def test_a_one_column_build_and_an_empty_one(self):
        s = Scratch()
        try:
            s.write("p.csv", "id,k\n1,a\n2,b\n3,\n")
            s.write("b1.csv", "k\na\n\"\"\nc\n")
            s.write("b0.csv", "k,v\n")
            for b in ("b1.csv", "b0.csv"):
                for jt in ("inner", "left"):
                    a = ["--root", s.dir, "--join", b, "--on", "k", "--join-type", jt, "--select", "id", "--format", "csv", "p.csv"]
                    r = run(*a)
                    m = main_run([str(x) for x in a])
                    self.assertEqual(r.status, 0, (b, jt, r.stderr))
                    if m is not None:
                        self.assertEqual((r.status, r.stdout), (m.returncode, m.stdout), (b, jt))
        finally:
            s.cleanup()


def peak_rss(binary_path, *args):
    """(exit status, peak resident set in bytes) of one run, by the launcher of tests/conformance/maxrss.c."""
    from test_memory import helper
    p = subprocess.run([helper(), binary_path, *[str(a) for a in args]], capture_output=True, text=True, check=True)
    rss, status = p.stdout.split()
    return int(status), int(rss) * (1 if sys.platform == "darwin" else 1024)


class Memory(unittest.TestCase):
    """M: a build of 150 columns of which the plan names two."""

    COLS = 150
    ROWS = 8000

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        rng = random.Random(7)
        names = ["bk", "bg"] + ["c%d" % i for i in range(cls.COLS)]
        with open(cls.s.dir / "b.csv", "w") as f:
            f.write(",".join(names) + "\n")
            for i in range(cls.ROWS):
                f.write(",".join(["k%d" % i, rng.choice("xyz")] + ["".join(rng.choice("abcdefghij") for _ in range(10)) for _ in range(cls.COLS)]) + "\n")
        cls.s.write("p.csv", "pk,pg\n" + "".join("k%d,%s\n" % (i * 1597 % cls.ROWS, "xyz"[i % 3]) for i in range(5)))
        cls.names = names

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def call(self, select):
        return ["--root", self.s.dir, "--join", "b.csv", "--on", "pk=bk", "--select", select, "--format", "csv", "--max-state-bytes", "1073741824", "p.csv"]

    def test_the_peak_resident_set_and_the_accounted_bytes(self):
        pruned = self.call("pk,bg")
        whole = self.call("pk,pg," + ",".join(self.names))
        # (the answers differ in their columns, and each is 5 rows: the call that names every column is only the control that holds the whole record)
        self.assertEqual(run(*pruned).status, 0)
        self.assertEqual(run(*whole).status, 0)
        rss = {}
        for label, argv in (("pruned", pruned), ("every column", whole)):
            rss[label] = min(peak_rss(binary(), *argv)[1] for _ in range(3))
            self.assertEqual(peak_rss(binary(), *argv)[0], 0)
        main_rss = None
        if MAIN_TABLE and os.path.exists(MAIN_TABLE):
            main_rss = min(peak_rss(MAIN_TABLE, *pruned)[1] for _ in range(3))
        size = os.path.getsize(self.s.dir / "b.csv")
        print("\nJ7 memory: build %d rows x %d columns, %d bytes; peak RSS, min of 3: pruned %d, every column %d, main (pruned call) %s" % (self.ROWS, self.COLS + 2, size, rss["pruned"], rss["every column"], main_rss), file=sys.stderr)
        self.assertLess(rss["pruned"] * 2, rss["every column"], rss)
        if main_rss:
            self.assertLess(rss["pruned"] * 2, main_rss, (rss, main_rss))
        # the accounted bytes: a bound the whole record passes and the named columns do not
        bound = 3_000_000
        a = self.call("pk,bg")
        a[a.index("--max-state-bytes") + 1] = str(bound)
        self.assertEqual(run(*a).status, 0, "the build of two named columns is held within %d accounted bytes" % bound)
        w = self.call("pk,pg," + ",".join(self.names))
        w[w.index("--max-state-bytes") + 1] = str(bound)
        self.assertEqual(run(*w).status, 8, "the build that names every column passes it")


class Corpus(unittest.TestCase):
    """EQ: the plans of scripts/corpus.py are unchanged (the 166 lines hash to the md5 recorded by every join slice), when the files of the corpus are there (the maintainer's machines')."""

    def test_the_md5_of_the_166_plans(self):
        need = [ROOT / "build" / "bench" / "data.csv", ROOT / "build" / "adv" / "f2.csv", ROOT / "build" / "adv" / "quoted.csv", ROOT / "build" / "adv" / "long.csv", ROOT / "build" / "adv" / "wide.csv", ROOT / "build" / "num" / "data.csv"]
        if not all(p.exists() for p in need):
            self.skipTest("the files of the corpus are not here (scripts/bench.py, scripts/adversarial.py, scripts/bench_numbers.py make them)")
        import hashlib
        env = dict(os.environ, TABLE=binary())
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "corpus.py")], capture_output=True, env=env, timeout=3000)
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        lines = p.stdout.decode().splitlines()
        self.assertEqual(len(lines), 166)
        self.assertEqual(hashlib.md5(p.stdout).hexdigest(), "fccb01b66dd7518d74611219a6c5aa29")


if __name__ == "__main__":
    unittest.main()
