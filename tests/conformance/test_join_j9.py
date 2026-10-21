"""J9 (docs/joins.md section 23, slice J9): `--join-sorted`, the merge join over two files declared sorted by the key.

The gates, written before the code and each able to fail (on `main`'s binary `--join-sorted` is `args.unknown-flag`, so every class below but `Unchanged` fails there):

* A, the differential (`Merge`, `Plans`): for random SORTED tables (all four join types; text, `:int`, `:dec(2)`, `:float`, `:date`, `:timestamp` and `:datetime` keys with several spellings of one value;
  two pairs; USING; empty keys; unicode, quoted, CRLF and BOM cells; duplicate groups of 1 to N rows on either side; csv, tab, semicolon and JSON-lines sides), the rows of `--join-sorted` are the rows of a
  Python sorted-merge (two cursors, no index) in KEY ORDER, are the BYTES of the same call without the flag (the hash join) on the same data, and for random plans (`--where`, `--select`, `--group`, `--agg`,
  `--sort`, `--order-by`, `--top`, `--limit`, `--from`, `--rename`) are the rows of the hash join's Python reference.
* B, the order (`Unsorted`): a file out of order is refused at the FIRST row below its predecessor, `join.not-sorted`, exit 8, with `detail.input`, `path`, `row`, `line`, `previous_row`, `previous_line`, `key`
  and `previous_key`, for the probe and for the build, for every kind of key, at any thread count, from a pipe, and when the disorder is past the last probe key.
* C, bounded memory (`Bounded`): a build file much larger than `--max-state-bytes` runs when the largest group of equal keys a probe row meets fits (the call the hash join refuses); the state is the formula
  of docs/joins.md 6.2 over that group, exact (accepted at N, refused at N - 1: `limit.state-too-large`, `detail.holder` `join-group`); the peak resident set does not grow with the rows of the build.
* D, the same bytes (`Threads`): at any `--threads` and `--chunk-bytes`, from a file or standard input.
* E, the surface (`Surface`): the flag in the table and the help, `join.not-sorted` in the rules, `--explain`, the refusals of the flags, the MCP definition, and 320 fuzzed calls (no trap, every refusal tagged).
* F, a call without the flag is what it was (`Unchanged`): the 166 plans of `scripts/corpus.py` keep their md5, and every plain join gives the bytes of `main`'s binary when `MAIN_TABLE` names it.
"""

import json
import os
import random
import re
import sys
import unittest

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import join_ref as J
import join_ref2 as R2
import join_ref3 as R3
import join_ref5 as R5
import join_ref7 as R7
import join_ref9 as R9
from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate

MAIN_TABLE = os.environ.get("MAIN_TABLE")
JTYPES = ("inner", "left", "semi", "anti")
BIGPAGE = ["--limit", "1000000", "--max-bytes", "67108864"]
FLAG = "--join-sorted"
CSV = ("csv", ",")
SEMI = ("csv", ";")
TAB = ("csv", "\t")
JSONL = ("jsonl", ",")
DATE_KINDS = ("date", "timestamp", "datetime")


def main_run(argv, stdin=b""):
    if not MAIN_TABLE or not os.path.exists(MAIN_TABLE):
        return None
    return subprocess.run([MAIN_TABLE, *argv], capture_output=True, input=stdin, timeout=120)


class Env:
    """A case written under a scratch root, with the formats of its two sides."""

    def __init__(self, case, pside=CSV, bside=CSV, seed=1, popts=None, bopts=None):
        self.case, self.pside, self.bside = case, pside, bside
        self.s = Scratch()
        self.d = self.s.dir
        R7.write_scene(self.d, case, pside, bside, random.Random(seed), popts, bopts)
        self.delim = pside[1] if pside[0] == "csv" else ","

    def plan(self, jt="inner", **kw):
        p = {"jtype": jt, "pairs": self.case.pairs, "prefix": self.case.prefix}
        p.update(kw)
        return p

    def argv(self, plan, fmt="csv", extra=(), operand=True, sorted_=True):
        a = R7.argv_of(self.d, self.case, plan, plan["jtype"], self.pside, self.bside, fmt, None, [], operand)
        # (the flag goes before the operand, which argv_of puts last)
        tail = [a.pop()] if operand else []
        if sorted_:
            a.append(FLAG)
        a += list(extra)
        return a + tail

    def run(self, plan, fmt="csv", extra=(), operand=True, sorted_=True, stdin=None):
        if fmt == "json" and "--limit" not in extra and plan.get("limit") is None:
            extra = list(extra) + BIGPAGE
        if stdin is None and not operand:
            stdin = (self.d / R7.pname(self.pside)).read_bytes()
        return run(*self.argv(plan, fmt, extra, operand, sorted_), stdin=stdin)

    def cleanup(self):
        self.s.cleanup()


def merge(case, jt):
    ph, pr = case.P
    bh, br = case.B
    pairs = [(p[0], p[1], p[2], p[3]) for p in case.pairs]
    return R9.merge_ref(ph, pr, bh, br, pairs, jt, case.prefix)


def key_values(rows, cols, kinds):
    return [R9.key_of(r, cols, kinds) for r in rows]


# ---- A: the differential ---------------------------------------------------------------------------------------------------------------------------------------


KINDS = ["text", "int", "dec", "float", "date", "timestamp", "datetime"]


def scenes(seed=9):
    """(name, case builder): sorted cases of every kind of key, with duplicate groups of 1 to 6 rows on either side."""
    rng = random.Random(seed)
    out = []
    for kind in KINDS:
        out.append((kind, lambda kind=kind: R9.sorted_case(rng, kind, 30, 40, dup=6)))
    out.append(("text, two pairs", lambda: R9.sorted_case(rng, "text", 30, 40, dup=5, composite=True)))
    out.append(("int, two pairs", lambda: R9.sorted_case(rng, "int", 30, 40, dup=5, composite=True)))
    out.append(("USING, text", lambda: R9.sorted_case(rng, "text", 30, 40, dup=5, using=True)))
    out.append(("USING, int", lambda: R9.sorted_case(rng, "int", 30, 40, dup=5, using=True)))
    out.append(("keys in other columns", lambda: R9.sorted_case(rng, "text", 30, 40, dup=5, pk_at=2, bk_at=1)))
    out.append(("int keys in other columns, two pairs", lambda: R9.sorted_case(rng, "int", 30, 40, dup=5, composite=True, pk_at=3, bk_at=2)))
    out.append(("prefix", lambda: R9.sorted_case(rng, "text", 20, 30, dup=4, prefix="r_", names=("k", "g", "v", "t"), bnames=("k", "g", "v", "t"))))
    out.append(("one group", lambda: R9.sorted_case(rng, "text", 25, 25, dup=1, key_pool=["same"])))
    out.append(("all distinct", lambda: R9.sorted_case(rng, "text", 25, 25, dup=1, key_pool=["k%03d" % i for i in range(200)])))
    return out


class Merge(unittest.TestCase):
    """A: the rows are the sorted-merge reference's, in key order, and the hash join's bytes."""

    counts = {"runs": 0, "rows": 0}

    def check(self, env, jt, tag):
        case = env.case
        labels, rows = merge(case, jt)
        want = R7.csv_bytes(labels, rows, env.delim)
        m = env.run(env.plan(jt), "csv")
        self.assertEqual(m.status, 0, "%s: %r %r" % (tag, m.stdout[:300], m.stderr[:300]))
        self.assertEqual(m.stdout, want, "%s: the rows of the sorted merge, in key order" % tag)
        h = env.run(env.plan(jt), "csv", sorted_=False)
        self.assertEqual(h.status, 0, "%s: the hash join: %r" % (tag, h.stderr[:300]))
        self.assertEqual(m.stdout, h.stdout, "%s: the bytes of the same call without --join-sorted" % tag)
        # the order is the order of the key
        ph, pr = case.P
        np_ = len(ph)
        pcols = [p[0] for p in case.pairs]
        kinds = [p[2] for p in case.pairs]
        if jt in ("semi", "anti") or True:
            out_keys = [R9.key_of(r, pcols, kinds) for r in rows]
            self.assertEqual(out_keys, sorted(out_keys), "%s: key order" % tag)
        Merge.counts["runs"] += 1
        Merge.counts["rows"] += len(rows)
        return m

    def test_every_kind_of_key_and_join_type(self):
        for name, build in scenes():
            case = build()
            env = Env(case)
            try:
                for jt in JTYPES:
                    with self.subTest("%s [%s]" % (name, jt)):
                        self.check(env, jt, "%s [%s]" % (name, jt))
            finally:
                env.cleanup()
        self.assertGreaterEqual(Merge.counts["runs"], 80)
        self.assertGreater(Merge.counts["rows"], 1000)

    def test_files_in_every_format(self):
        rng = random.Random(91)
        sides = [(CSV, JSONL), (JSONL, CSV), (JSONL, JSONL), (SEMI, SEMI), (TAB, TAB), (CSV, SEMI), (SEMI, CSV), (TAB, JSONL)]
        for kind in ("text", "int"):
            for pside, bside in sides:
                case = R9.sorted_case(rng, kind, 24, 30, dup=4)
                env = Env(case, pside, bside, seed=3)
                try:
                    for jt in JTYPES:
                        tag = "%s %s/%s [%s]" % (kind, pside, bside, jt)
                        with self.subTest(tag):
                            self.check(env, jt, tag)
                finally:
                    env.cleanup()

    def test_cells_that_are_hard_to_read(self):
        rng = random.Random(92)
        opts = [{"crlf": True}, {"bom": True}, {"quote_all": True}, {"crlf": True, "bom": True, "quote_all": True}, {"final_newline": False}]
        for o in opts:
            for kind in ("text", "int"):
                case = R9.sorted_case(rng, kind, 20, 20, dup=3)
                env = Env(case, CSV, CSV, popts=o, bopts=o)
                try:
                    for jt in JTYPES:
                        with self.subTest("%r %s %s" % (o, kind, jt)):
                            self.check(env, jt, "%r %s %s" % (o, kind, jt))
                finally:
                    env.cleanup()

    def test_a_key_with_a_line_break_in_a_quoted_cell(self):
        rng = random.Random(93)
        pool = ["a", "b\nb", "c", "d\r\nd", "e"]
        case = R9.sorted_case(rng, "text", 20, 20, dup=3, key_pool=pool)
        env = Env(case)
        try:
            for jt in JTYPES:
                with self.subTest(jt):
                    self.check(env, jt, "line breaks %s" % jt)
        finally:
            env.cleanup()

    def test_the_cross_product_of_a_group_is_in_file_order(self):
        # 3 probe rows and 4 build rows of one key: 12 rows, the probe rows in order and for each the build rows in order
        case = R3.Case("g", (["pk", "pg", "pv", "pt"], [["a", "x", "1", "p1"], ["k", "x", "2", "p2"], ["k", "y", "3", "p3"], ["k", "z", "4", "p4"], ["m", "x", "5", "p5"]]),
                       (["bk", "bg", "bv", "bt"], [["b", "x", "1", "q1"], ["k", "x", "2", "q2"], ["k", "y", "3", "q3"], ["k", "z", "4", "q4"], ["k", "w", "5", "q5"], ["z", "x", "6", "q6"]]), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            r = env.run(env.plan("inner"), "csv")
            self.assertEqual(r.status, 0, r.stderr)
            lines = r.stdout.decode().splitlines()
            self.assertEqual(lines[1:], ["k,x,2,p2,k,x,2,q2", "k,x,2,p2,k,y,3,q3", "k,x,2,p2,k,z,4,q4", "k,x,2,p2,k,w,5,q5", "k,y,3,p3,k,x,2,q2", "k,y,3,p3,k,y,3,q3", "k,y,3,p3,k,z,4,q4",
                                      "k,y,3,p3,k,w,5,q5", "k,z,4,p4,k,x,2,q2", "k,z,4,p4,k,y,3,q3", "k,z,4,p4,k,z,4,q4", "k,z,4,p4,k,w,5,q5"])
            left = env.run(env.plan("left"), "csv")
            self.assertEqual(left.stdout.decode().splitlines()[1], "a,x,1,p1,,,,")
            self.assertEqual(left.stdout.decode().splitlines()[-1], "m,x,5,p5,,,,")
            anti = env.run(env.plan("anti"), "csv")
            self.assertEqual(anti.stdout.decode().splitlines()[1:], ["a,x,1,p1", "m,x,5,p5"])
            semi = env.run(env.plan("semi"), "csv")
            self.assertEqual(semi.stdout.decode().splitlines()[1:], ["k,x,2,p2", "k,y,3,p3", "k,z,4,p4"])
        finally:
            env.cleanup()

    def test_spellings_of_one_value_are_one_key(self):
        # 007, 7 and +7 are the key 7; -0 and 0 are the key 0; 1.5 and 1.50 are one value; the files are sorted by value
        for kind, left, right in (("int", ["-3", "-0", "0", "7", "007", "+7", "12"], ["-0", "0", "00", "+7", "7", "007", "12", "42"]), ("dec", ["-1.5", "0", "0.00", "1.5", "1.50"], ["0.0", "01.5", "1.50", "2"]),
                                  ("float", ["-2.5", "-0", "0", "1.5", "15e-1", "2"], ["0.0", "1.50", "1.5", "2.0"])):
            k, _ = R9.POOLS[kind]
            P = [[x, "g", "1", "p%d" % i] for i, x in enumerate(left)]
            B = [[x, "g", "1", "b%d" % i] for i, x in enumerate(right)]
            case = R3.Case("sp", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, k, False)], "", False)
            env = Env(case)
            try:
                for jt in JTYPES:
                    with self.subTest("%s %s" % (kind, jt)):
                        self.check(env, jt, "%s %s" % (kind, jt))
            finally:
                env.cleanup()

    def test_empty_files_and_empty_keys(self):
        rng = random.Random(94)
        base = R9.sorted_case(rng, "text", 6, 6, dup=2, key_pool=["", "a", "b"])
        env = Env(base)
        try:
            for jt in JTYPES:
                self.check(env, jt, "empty keys %s" % jt)
        finally:
            env.cleanup()
        for np_, nb_ in ((0, 5), (5, 0), (0, 0)):
            case = R9.sorted_case(rng, "text", np_, nb_, dup=2)
            env = Env(case)
            try:
                for jt in JTYPES:
                    with self.subTest("%d x %d %s" % (np_, nb_, jt)):
                        self.check(env, jt, "%d x %d %s" % (np_, nb_, jt))
            finally:
                env.cleanup()

    def test_a_group_of_hundreds_of_rows_on_each_side(self):
        rng = random.Random(95)
        case = R9.sorted_case(rng, "text", 150, 160, dup=1, key_pool=["a", "b"])
        env = Env(case)
        try:
            for jt in ("inner", "left"):
                m = self.check(env, jt, "wide groups %s" % jt)
            self.assertGreater(len(m.stdout.splitlines()), 10000)
        finally:
            env.cleanup()


# ---- plans -------------------------------------------------------------------------------------------------------------------------------------------------------


def as_text(cell):
    """A cell of a reference table (one character a byte) as the JSON answer has it: the text its bytes are."""
    return cell.encode("latin-1").decode("utf-8")


def describe(plan):
    return {k: (R3.render(v) if k == "where" else v) for k, v in plan.items() if k != "pairs"}


class Plans(unittest.TestCase):
    """A: the plans of J3 (rows, `--where` and its split, `--join-where`, groups, aggregates, sorts, pages, renames) over sorted files give the hash join's rows."""

    counts = {"plans": 0}

    def judge(self, env, plan, tag):
        try:
            want = ("ok",) + R3.execute(plan, env.case.P, env.case.B)[1:]
        except R3.Refused as r:
            return None
        a = env.argv(plan, "json") + ([] if plan.get("limit") is not None else BIGPAGE)
        j = run(*a)
        self.assertEqual(j.status, 0, "%s: %r %r %r" % (tag, j.stdout[:500], j.stderr[:300], a))
        self.assertEqual(validate(j), [], tag)
        data = j.data()
        self.assertEqual((data["columns"], data["rows"]), ([as_text(x) for x in want[1]], [[as_text(x) for x in r] for r in want[2]]), tag)
        c = env.run(plan, "csv")
        self.assertEqual((c.status, c.stdout), (0, R7.csv_bytes(want[1], want[2], env.delim)), "%s: csv" % tag)
        h = env.run(plan, "csv", sorted_=False)
        self.assertEqual(c.stdout, h.stdout, "%s: the hash join's bytes" % tag)
        s = env.run(plan, "csv", operand=False)
        self.assertEqual((s.status, s.stdout), (0, c.stdout), "%s: standard input" % tag)
        t = env.run(plan, "csv", extra=["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7"])
        self.assertEqual((t.status, t.stdout), (0, c.stdout), "%s: threads" % tag)
        Plans.counts["plans"] += 1
        return j

    def test_random_plans(self):
        rng = random.Random(20261020)
        mk = [lambda: R9.sorted_case(rng, "text", 24, 40, dup=4, extras=2), lambda: R9.sorted_case(rng, "int", 24, 40, dup=4, extras=2),
              lambda: R9.sorted_case(rng, "text", 24, 40, dup=4, composite=True, extras=2), lambda: R9.sorted_case(rng, "text", 24, 40, dup=4, using=True, extras=2),
              lambda: R9.sorted_case(rng, "dec", 24, 40, dup=4, extras=2), lambda: R9.sorted_case(rng, "float", 24, 40, dup=4, extras=2)]
        sides = [(CSV, CSV), (JSONL, CSV), (CSV, JSONL), (SEMI, SEMI)]
        for i, build in enumerate(mk):
            for pside, bside in sides[: (4 if i < 2 else 2)]:
                case = build()
                env = Env(case, pside, bside, seed=i)
                try:
                    for jt in JTYPES:
                        for n in range(3):
                            plan = R3.random_plan(rng, case, jt)
                            tag = "%s %s/%s [%s] %d %r" % (case.name, pside, bside, jt, n, describe(plan))
                            with self.subTest(tag):
                                self.judge(env, plan, tag)
                finally:
                    env.cleanup()
        print("\nJ9 plans: %r" % self.counts, file=sys.stderr)
        self.assertGreaterEqual(self.counts["plans"], 150)

    def test_a_join_where_and_the_split_of_where_are_the_hash_joins(self):
        rng = random.Random(7)
        case = R9.sorted_case(rng, "text", 30, 50, dup=4, extras=1)
        env = Env(case)
        try:
            for jt in JTYPES:
                for plan in (env.plan(jt, jw=R3.leaf("bg", "!=", "x")), env.plan(jt, where=R3.AND(R3.leaf("pg", "=", "x"), R3.leaf("pv", ">", "3", typed=True))),
                             env.plan(jt, where=R3.leaf("pg", "!=", "y"), jw=R3.leaf("bt", "!=", "b3"))):
                    with self.subTest("%s %r" % (jt, describe(plan))):
                        self.judge(env, plan, "%s %r" % (jt, describe(plan)))
        finally:
            env.cleanup()

    def test_the_guard_of_an_empty_typed_probe_key_still_works(self):
        # a leading text condition of --where runs before the key is read, so an empty probe key is not read (the sorted file has its empty cells first)
        P = [["", "g", "1", "p0"], ["1", "g", "1", "p1"], ["2", "g", "1", "p2"]]
        B = [["1", "g", "1", "b1"], ["2", "g", "1", "b2"]]
        case = R3.Case("guard", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, "int", False)], "", False)
        env = Env(case)
        try:
            r = env.run(env.plan("inner"), "csv")
            self.assertEqual(r.status, 8)
            self.assertEqual(json.loads(env.run(env.plan("inner"), "json").stdout)["error"]["rule"], "value.not-integer")
            g = env.run(env.plan("inner", where=R3.leaf("pk", "!=", "")), "csv")
            self.assertEqual(g.stdout.decode().splitlines()[1:], ["1,g,1,p1,1,g,1,b1", "2,g,1,p2,2,g,1,b2"])
        finally:
            env.cleanup()


# ---- B: the order --------------------------------------------------------------------------------------------------------------------------------------------


class Unsorted(unittest.TestCase):
    """B: a file out of order is refused at the first row below its predecessor."""

    counts = {"runs": 0}

    def detail(self, r):
        self.assertEqual(r.status, 8, r.stdout[:300] + r.stderr[:200])
        e = r.error()
        self.assertEqual(e["rule"], "join.not-sorted", e)
        return e["detail"]

    def expect(self, env, side, info, plan, tag, extra=(), operand=True):
        r = env.run(plan, "json", extra, operand)
        self.assertEqual(r.status, 8, "%s: %r %r" % (tag, r.stdout[:400], r.stderr[:200]))
        self.assertEqual(validate(r), [], tag)
        e = r.error()
        self.assertEqual(e["rule"], "join.not-sorted", "%s: %r" % (tag, e))
        d = e["detail"]
        fmt = env.pside if side == "probe" else env.bside
        shift = 0 if fmt[0] == "jsonl" else 1             # a JSON-lines file has no header line
        path = R7.pname(env.pside) if side == "probe" else R7.bname(env.bside)
        if side == "probe" and not operand:
            path = "-"
        self.assertEqual(d["input"], side, tag)
        self.assertEqual(d["path"], path, tag)
        self.assertEqual((d["row"], d["line"], d["previous_row"], d["previous_line"]), (info["row"], info["row"] + shift, info["previous_row"], info["previous_row"] + shift), tag)
        self.assertEqual((d["key"], d["previous_key"]), (info["key"], info["previous_key"]), tag)
        self.assertEqual(e["repair"]["kind"] if e["repair"] else None, "none", tag)
        Unsorted.counts["runs"] += 1
        return r

    def test_every_kind_of_key_on_either_side(self):
        rng = random.Random(96)
        for name, build in scenes(seed=15):
            if name in ("one group", "prefix"):
                continue
            for side in ("probe", "build"):
                case = build()
                broken = R9.break_order(rng, case, side)
                if broken is None:
                    continue
                case2, info = broken
                env = Env(case2)
                try:
                    for jt in JTYPES:
                        tag = "%s %s [%s]" % (name, side, jt)
                        with self.subTest(tag):
                            self.expect(env, side, info, env.plan(jt), tag)
                finally:
                    env.cleanup()
        self.assertGreaterEqual(Unsorted.counts["runs"], 80)

    def test_files_in_every_format(self):
        rng = random.Random(97)
        for pside, bside in ((CSV, JSONL), (JSONL, CSV), (SEMI, TAB), (TAB, SEMI), (JSONL, JSONL)):
            for side in ("probe", "build"):
                case = R9.sorted_case(rng, "text", 20, 24, dup=3)
                case2, info = R9.break_order(rng, case, side)
                env = Env(case2, pside, bside, seed=4)
                try:
                    tag = "%s/%s %s" % (pside, bside, side)
                    with self.subTest(tag):
                        self.expect(env, side, info, env.plan("inner"), tag)
                finally:
                    env.cleanup()

    def test_any_thread_count_and_a_pipe_for_the_probe(self):
        rng = random.Random(98)
        for kind in ("text", "int"):
            for side in ("probe", "build"):
                case = R9.sorted_case(rng, kind, 30, 30, dup=3)
                case2, info = R9.break_order(rng, case, side)
                env = Env(case2)
                try:
                    for extra in ([], ["--threads", "4"], ["--threads", "64", "--parallel-min-bytes", "0", "--chunk-bytes", "1"], ["--threads", "3", "--parallel-min-bytes", "0", "--chunk-bytes", "7"]):
                        for operand in (True, False):
                            tag = "%s %s %r %s" % (kind, side, extra, "file" if operand else "pipe")
                            with self.subTest(tag):
                                self.expect(env, side, info, env.plan("left"), tag, extra, operand)
                finally:
                    env.cleanup()

    def test_the_first_disorder_is_the_one_reported(self):
        # three disorders in the probe: the first is named; the rows before it are written (csv)
        P = [["a", "x", "1", "p1"], ["c", "x", "1", "p2"], ["b", "x", "1", "p3"], ["d", "x", "1", "p4"], ["a", "x", "1", "p5"]]
        B = [["a", "x", "1", "q1"], ["b", "x", "1", "q2"], ["c", "x", "1", "q3"]]
        case = R3.Case("first", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            r = env.run(env.plan("inner"), "json")
            d = self.detail(r)
            self.assertEqual((d["input"], d["row"], d["line"], d["key"], d["previous_key"], d["previous_row"]), ("probe", 3, 4, ["b"], ["c"], 2))
            c = env.run(env.plan("inner"), "csv")
            self.assertEqual(c.status, 8)
            self.assertIn(b"join.not-sorted", c.stderr)
            self.assertEqual(c.stdout.decode().splitlines()[:3], ["pk,pg,pv,pt,bk,bg,bv,bt", "a,x,1,p1,a,x,1,q1", "c,x,1,p2,c,x,1,q3"])
        finally:
            env.cleanup()

    def test_a_disorder_past_the_last_probe_key_is_still_found(self):
        P = [["a", "x", "1", "p1"], ["b", "x", "1", "p2"]]
        B = [["a", "x", "1", "q1"], ["b", "x", "1", "q2"], ["z", "x", "1", "q3"], ["m", "x", "1", "q4"]]
        case = R3.Case("tail", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            for jt in JTYPES:
                d = self.detail(env.run(env.plan(jt), "json"))
                self.assertEqual((d["input"], d["row"], d["line"], d["key"], d["previous_key"]), ("build", 4, 5, ["m"], ["z"]), jt)
        finally:
            env.cleanup()

    def test_a_full_page_ends_the_read_and_a_disorder_past_it_is_not_found(self):
        P = [["a", "x", "1", "p1"], ["b", "x", "1", "p2"], ["c", "x", "1", "p3"]]
        B = [["a", "x", "1", "q1"], ["b", "x", "1", "q2"], ["c", "x", "1", "q3"], ["z", "x", "1", "q4"], ["m", "x", "1", "q5"]]
        case = R3.Case("page", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            paged = env.run(env.plan("inner"), "csv", ["--limit", "1"])
            self.assertEqual(paged.status, 0, paged.stderr)
            self.assertEqual(paged.stdout.decode().splitlines()[1:], ["a,x,1,p1,a,x,1,q1"])
            self.assertEqual(env.run(env.plan("inner"), "json").status, 8)
        finally:
            env.cleanup()

    def test_a_probe_past_the_end_of_the_build_is_still_checked(self):
        P = [["a", "x", "1", "p1"], ["z", "x", "1", "p2"], ["y", "x", "1", "p3"]]
        B = [["a", "x", "1", "q1"]]
        case = R3.Case("tailp", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            for jt in JTYPES:
                d = self.detail(env.run(env.plan(jt), "json"))
                self.assertEqual((d["input"], d["row"], d["key"], d["previous_key"]), ("probe", 3, ["y"], ["z"]), jt)
        finally:
            env.cleanup()

    def test_equal_keys_and_spellings_of_one_value_are_in_order(self):
        for kind, cells in (("text", ["a", "a", "a", "b"]), ("int", ["-0", "0", "00", "007", "7", "+7", "8"]), ("dec", ["0", "0.0", "0.00", "1.5", "1.50", "01.5"]), ("float", ["-0", "0", "1.5", "15e-1", "1.50"])):
            k, _ = R9.POOLS[kind]
            P = [[c, "x", "1", "p%d" % i] for i, c in enumerate(cells)]
            case = R3.Case("eq", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], [list(r) for r in P]), [(0, 0, k, False)], "", False)
            env = Env(case)
            try:
                r = env.run(env.plan("inner"), "csv")
                self.assertEqual(r.status, 0, (kind, r.stderr))
            finally:
                env.cleanup()

    def test_a_typed_key_is_ordered_by_its_value_not_by_its_bytes(self):
        # 9 before 10 is sorted as an integer and not as text
        P = [["9", "x", "1", "p1"], ["10", "x", "1", "p2"]]
        case = R3.Case("num", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], [list(r) for r in P]), [(0, 0, "int", False)], "", False)
        env = Env(case)
        try:
            self.assertEqual(env.run(env.plan("inner"), "csv").status, 0)
            text_case = R3.Case("txt", case.P, (["bk", "bg", "bv", "bt"], [["10", "x", "1", "p2"], ["9", "x", "1", "p1"]]), [(0, 0, None, False)], "", False)
            env2 = Env(text_case)
            try:
                d = self.detail(env2.run(env2.plan("inner"), "json"))
                self.assertEqual((d["input"], d["row"], d["key"], d["previous_key"]), ("probe", 2, ["10"], ["9"]))
            finally:
                env2.cleanup()
        finally:
            env.cleanup()

    def test_text_is_ordered_bytewise_and_an_empty_key_is_the_smallest(self):
        order = ["", "B", "a", "aa", "b", utf8("é")]
        P = [[c, "x", "1", "p%d" % i] for i, c in enumerate(order)]
        case = R3.Case("bytes", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], [list(r) for r in P]), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            self.assertEqual(env.run(env.plan("inner"), "csv").status, 0)
        finally:
            env.cleanup()
        P2 = [["a", "x", "1", "p0"], ["B", "x", "1", "p1"]]
        case2 = R3.Case("bytes2", (["pk", "pg", "pv", "pt"], P2), (["bk", "bg", "bv", "bt"], [list(r) for r in P2]), [(0, 0, None, False)], "", False)
        env = Env(case2)
        try:
            r = env.run(env.plan("inner"), "json")
            self.assertEqual(r.error()["rule"], "join.not-sorted")
        finally:
            env.cleanup()

    def test_two_pairs_are_ordered_component_by_component(self):
        # (a, 2) (a, 10) (ab, 1): the second pair is an integer, the first text; "a" < "ab" and 2 < 10
        P = [["a", "2", "1", "p0"], ["a", "10", "1", "p1"], ["ab", "1", "1", "p2"]]
        pairs = [(0, 0, None, False), (1, 1, "int", False)]
        case = R3.Case("two", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], [list(r) for r in P]), pairs, "", False)
        env = Env(case)
        try:
            self.assertEqual(env.run(env.plan("inner"), "csv").status, 0)
        finally:
            env.cleanup()
        P2 = [["ab", "1", "1", "p0"], ["a", "10", "1", "p1"]]
        case2 = R3.Case("two2", (["pk", "pg", "pv", "pt"], P2), (["bk", "bg", "bv", "bt"], [list(r) for r in P]), pairs, "", False)
        env = Env(case2)
        try:
            d = self.detail(env.run(env.plan("inner"), "json"))
            self.assertEqual((d["input"], d["row"], d["key"], d["previous_key"]), ("probe", 2, ["a", "10"], ["ab", "1"]))
        finally:
            env.cleanup()

    def test_a_row_the_join_does_not_read_is_not_checked(self):
        # a --join-where drops the build row that is out of order: its key is never read (the order is the order of the keys the join reads)
        P = [["a", "x", "1", "p1"], ["c", "x", "1", "p2"]]
        B = [["a", "x", "1", "q1"], ["z", "drop", "1", "q2"], ["b", "x", "1", "q3"], ["c", "x", "1", "q4"]]
        case = R3.Case("jw", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            ok = env.run(env.plan("inner", jw=R3.leaf("bg", "!=", "drop")), "csv")
            self.assertEqual(ok.status, 0, ok.stderr)
            self.assertEqual(ok.stdout.decode().splitlines()[1:], ["a,x,1,p1,a,x,1,q1", "c,x,1,p2,c,x,1,q4"])
            # and so does a leading condition of --where that names the --join file, for an inner join (applied before the row is held)
            pushed = env.run(env.plan("inner", where=R3.leaf("bg", "!=", "drop")), "csv")
            self.assertEqual(pushed.status, 0, pushed.stderr)
            self.assertEqual(pushed.stdout, env.run(env.plan("inner", where=R3.leaf("bg", "!=", "drop")), "csv", sorted_=False).stdout)
            left = env.run(env.plan("left", where=R3.leaf("bg", "!=", "drop")), "json")
            self.assertEqual(self.detail(left)["row"], 3)
            bad = env.run(env.plan("inner"), "json")
            d = self.detail(bad)
            self.assertEqual((d["input"], d["row"], d["key"], d["previous_key"]), ("build", 3, ["b"], ["z"]))
        finally:
            env.cleanup()

    def test_a_key_longer_than_the_detail_is_cut(self):
        long_key = "k" * 100
        P = [[long_key + "b", "x", "1", "p0"], [long_key + "a", "x", "1", "p1"]]
        case = R3.Case("long", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], [["a", "x", "1", "q"]]), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            d = self.detail(env.run(env.plan("inner"), "json"))
            self.assertEqual((d["key"], d["previous_key"]), ([long_key[:64]], [long_key[:64]]))
            self.assertTrue(d["key_truncated"])
        finally:
            env.cleanup()


def utf8(text):
    return R9.utf8(text)


# ---- C: bounded memory ------------------------------------------------------------------------------------------------------------------------------------------


def raw_record(row, delim=","):
    return R5.csv_record([c.encode("latin-1") for c in row], delim.encode())


def key_bytes_len(row, pairs, side):
    """The bytes of the key as the tool holds it: the cell for one text pair, 8 for one typed pair, several pairs 4 + the component each."""
    comps = []
    for p in pairs:
        col = p[0] if side == 0 else p[1]
        comps.append(len(row[col].encode("latin-1")) if p[2] is None else 8)
    return comps[0] if len(pairs) == 1 else sum(4 + c for c in comps)


def window_need(case, jt):
    """The accounted bytes of the largest group of equal build keys that some probe row meets (docs/joins.md 6.2: a held row costs its record + 16, a key its bytes + 60, a semi or anti join holds the key at
    bytes + 44), and the group's rows: the number `--max-state-bytes` must reach for the call to run."""
    pairs = case.pairs
    pk = [p[0] for p in pairs]
    bk = [p[1] for p in pairs]
    kinds = [p[2] for p in pairs]
    probe_keys = {R9.key_of(r, pk, kinds) for r in case.P[1]}
    groups = {}
    order = []
    for n, r in enumerate(case.B[1], 1):
        k = R9.key_of(r, bk, kinds)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append((n, r))
    best = (0, 0, None)
    for k in order:
        if k not in probe_keys:
            continue
        rows = groups[k]
        if jt in ("semi", "anti"):
            cost = key_bytes_len(rows[0][1], pairs, 1) + 44
        else:
            cost = sum(len(raw_record(r)) + 16 for n, r in rows) + key_bytes_len(rows[0][1], pairs, 1) + 60
        if cost > best[0]:
            best = (cost, len(rows), k)
    return best


def big_case(rng, nkeys=600, per=5, big=40, np_=80):
    """A build of nkeys keys of `per` rows each but one of `big` rows (the largest group), 11 columns wide; a probe of np_ rows whose keys are build keys, among them the big one."""
    keys = ["k%05d" % i for i in range(nkeys)]
    brows = []
    for i, k in enumerate(keys):
        for j in range(big if i == nkeys // 2 else per):
            brows.append([k, rng.choice("xyz"), str(rng.randint(0, 9)), "b%d" % len(brows)] + ["".join(rng.choice("abcdefgh") for _ in range(10)) for _ in range(3)])
    pkeys = sorted(rng.sample(keys, np_ - 1) + [keys[nkeys // 2]])
    prows = [[k, rng.choice("xyz"), "1", "p%d" % i] for i, k in enumerate(pkeys)]
    return R3.Case("big", (["pk", "pg", "pv", "pt"], prows), (["bk", "bg", "bv", "bt", "w0x", "w1x", "w2x"], brows), [(0, 0, None, False)], "", False)


class Bounded(unittest.TestCase):
    """C: the state is one group of equal build keys."""

    def test_a_build_much_larger_than_the_bound_runs_where_the_hash_join_is_refused(self):
        rng = random.Random(99)
        case = big_case(rng)
        env = Env(case)
        try:
            need, rows, key = window_need(case, "inner")
            self.assertEqual(rows, 40)
            size = os.path.getsize(env.d / "b.csv")
            self.assertGreater(size, 15 * need, "the build file is much larger than the bound")
            h = env.run(env.plan("inner"), "json", ["--max-state-bytes", str(need)], sorted_=False)
            self.assertEqual(h.status, 8)
            self.assertEqual(h.error()["rule"], "limit.state-too-large")
            self.assertEqual(h.error()["detail"]["holder"], "join-build")
            for jt in JTYPES:
                m = env.run(env.plan(jt), "csv", ["--max-state-bytes", str(window_need(case, jt)[0])])
                self.assertEqual(m.status, 0, "%s: %r %r" % (jt, m.stdout[:200], m.stderr[:300]))
                labels, want = merge(case, jt)
                self.assertEqual(m.stdout, R7.csv_bytes(labels, want, env.delim), jt)
        finally:
            env.cleanup()

    def test_the_state_is_the_largest_group_a_probe_row_meets_and_exact(self):
        rng = random.Random(100)
        shapes = [R9.sorted_case(rng, "text", 40, 80, dup=7, extras=2), R9.sorted_case(rng, "int", 40, 80, dup=7, extras=1), R9.sorted_case(rng, "text", 40, 80, dup=6, composite=True),
                  R9.sorted_case(rng, "dec", 40, 80, dup=5), R9.sorted_case(rng, "text", 40, 80, dup=6, using=True)]
        for case in shapes:
            env = Env(case)
            try:
                for jt in JTYPES:
                    need, rows, key = window_need(case, jt)
                    if need == 0:
                        continue
                    tag = "%s [%s] need %d" % (case.name, jt, need)
                    ok = env.run(env.plan(jt), "csv", ["--max-state-bytes", str(need)])
                    self.assertEqual(ok.status, 0, "%s: %r" % (tag, ok.stderr[:300]))
                    low = env.run(env.plan(jt), "json", ["--max-state-bytes", str(need - 1)])
                    self.assertEqual(low.status, 8, tag)
                    e = low.error()
                    self.assertEqual((e["rule"], e["detail"]["holder"], e["detail"]["limit"]), ("limit.state-too-large", "join-group", need - 1), tag)
            finally:
                env.cleanup()

    def test_the_refusal_says_which_row_did_not_fit(self):
        # the key "k" has three build rows; the third does not fit
        P = [["j", "x", "1", "p"], ["k", "x", "1", "p"]]
        B = [["j", "x", "1", "q"], ["k", "x", "1", "q1"], ["k", "x", "1", "q2"], ["k", "x", "1", "q3"]]
        case = R3.Case("fit", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            rec = len(raw_record(B[1]))
            two = 2 * (rec + 16) + 1 + 60
            r = env.run(env.plan("inner"), "json", ["--max-state-bytes", str(two)])
            self.assertEqual(r.status, 8, r.stdout[:300])
            d = r.error()["detail"]
            self.assertEqual((d["holder"], d["input"], d["path"], d["limit"], d["held"], d["rows_held"], d["build_row"], d["build_line"], d["group_key"]), ("join-group", "build", "b.csv", two, two, 2, 4, 5, ["k"]))
            ok = env.run(env.plan("inner"), "csv", ["--max-state-bytes", str(two + rec + 16)])
            self.assertEqual(ok.status, 0)
            # a group no probe row has costs nothing: with the probe row of "k" gone, the same bound runs
            case2 = R3.Case("fit2", (["pk", "pg", "pv", "pt"], P[:1]), case.B, case.pairs, "", False)
            env2 = Env(case2)
            try:
                self.assertEqual(env2.run(env2.plan("inner"), "csv", ["--max-state-bytes", "200"]).status, 0)
            finally:
                env2.cleanup()
        finally:
            env.cleanup()

    def test_groups_and_sorted_rows_share_the_flag_with_the_window(self):
        rng = random.Random(101)
        case = R9.sorted_case(rng, "text", 40, 80, dup=6, extras=1)
        env = Env(case)
        try:
            need, rows, key = window_need(case, "inner")
            plan = env.plan("inner", group=[("pg", "text")], aggs=[("count", None)])
            # the window is given the flag less half of it (a choice, docs/joins.md 23): 2 * need is the least that runs, 2 * need - 2 does not
            ok = env.run(plan, "csv", ["--max-state-bytes", str(2 * need)])
            self.assertEqual(ok.status, 0, ok.stderr[:300])
            low = env.run(plan, "json", ["--max-state-bytes", str(2 * need - 2)])
            self.assertEqual(low.status, 8)
            self.assertEqual(low.error()["detail"]["holder"], "join-group")
        finally:
            env.cleanup()

    def test_a_group_that_does_not_fit_beside_the_window_says_what_the_window_has(self):
        keys = ["k%03d" % i + "x" * 60 for i in range(40)]
        P = [[k, "g", "1", "p%d" % i] for i, k in enumerate(keys)]
        B = [[k, "g", "1", "b%d" % i] for i, k in enumerate(keys)]
        case = R3.Case("share", (["pk", "pg", "pv", "pt"], P), (["bk", "bg", "bv", "bt"], B), [(0, 0, None, False)], "", False)
        env = Env(case)
        try:
            need, rows, key = window_need(case, "inner")
            plan = env.plan("inner", group=[("pk", "text")], aggs=[("count", None)])
            # the window has the flag less its half, and the groups the half: 40 group keys of 64 bytes do not fit in the half of 2 * need
            low = env.run(plan, "json", ["--max-state-bytes", str(2 * need)])
            self.assertEqual(low.status, 8, low.stdout[:300])
            e = low.error()
            d = e["detail"]
            self.assertEqual((e["rule"], d["holder"], d["build_bytes"], d["limit"]), ("limit.state-too-large", "groups", need, 2 * need))
            ok = env.run(plan, "csv", ["--max-state-bytes", str(40 * need)])
            self.assertEqual(ok.status, 0, ok.stderr[:300])
        finally:
            env.cleanup()

    def test_the_peak_resident_set_does_not_grow_with_the_build(self):
        from test_join_j7_prune import peak_rss
        peaks = {}
        for rows in (20000, 160000):
            s = Scratch()
            try:
                keys = ["k%07d" % i for i in range(rows)]
                with open(s.dir / "b.csv", "w") as f:
                    f.write("bk,bg,bv\n")
                    for k in keys:
                        f.write("%s,g,%s\n" % (k, "v" * 20))
                with open(s.dir / "p.csv", "w") as f:
                    f.write("pk,pt\n")
                    for k in keys[:: max(rows // 500, 1)]:
                        f.write("%s,t\n" % k)
                common = ["--root", s.dir, "--join", "b.csv", "--on", "pk=bk", "--format", "csv", "--max-state-bytes", "1073741824", "p.csv"]
                status, rss = peak_rss(binary(), *common, FLAG)
                self.assertEqual(status, 0)
                hstatus, hrss = peak_rss(binary(), *common)
                self.assertEqual(hstatus, 0)
                peaks[rows] = (rss, hrss, os.path.getsize(s.dir / "b.csv"))
            finally:
                s.cleanup()
        print("\nJ9 memory: rows -> (merge peak, hash join peak, build file bytes): %r" % peaks, file=sys.stderr)
        small, large = peaks[20000], peaks[160000]
        self.assertLess(large[0], small[0] * 1.5 + 2 * 1024 * 1024, "the peak does not grow with the build")
        self.assertLess(large[0] * 2, large[1], "and is under half of the hash join's on the same files")


# ---- D: threads ------------------------------------------------------------------------------------------------------------------------------------------------


def canon(e):
    """A refusal without the flag in the arguments of its repairs (a retry repeats the call it repairs)."""
    text = json.dumps(e, sort_keys=True).replace('"--join-sorted", ', "").replace(', "--join-sorted"', "")
    return json.loads(text)


class Refusals(unittest.TestCase):
    """B: a file that the reader or the plan refuses is refused as the call without the flag refuses it: the same rule, row, line and facts, whichever file it is."""

    P = "k,pv\n" + "".join("%s,%d\n" % (k, i) for i, k in enumerate("aabbccddeeff"))
    B = "k,bv\n" + "".join("%s,%d\n" % (k, i) for i, k in enumerate("aabcdefg"))

    def both(self, p=None, b=None, flags=(), fmt="json", stdin=False):
        s = Scratch()
        try:
            s.write("p.csv", self.P if p is None else p)
            s.write("b.csv", self.B if b is None else b)
            base = ["--root", s.dir, "--join", "b.csv", "--on", "k=k", "--join-prefix", "r_", "--format", fmt, *flags]
            m = run(*base, FLAG, "p.csv")
            h = run(*base, "p.csv")
            return m, h
        finally:
            s.cleanup()

    def same(self, tag, **kw):
        m, h = self.both(**kw)
        self.assertEqual(h.status, 8 if "status" not in kw else kw["status"], "%s: the hash join: %r" % (tag, h.stdout[:300]))
        self.assertEqual(m.status, h.status, "%s: %r %r" % (tag, m.stdout[:400], m.stderr[:200]))
        self.assertEqual(canon(m.error()), canon(h.error()), tag)
        return m.error()

    def test_a_cell_that_is_not_of_its_type_in_either_file(self):
        for side in ("p", "b"):
            for pos in (2, 6):
                p = "k,pv\n" + "".join("%d,%d\n" % (i // 2 + 1, i) for i in range(12))
                b = "k,bv\n" + "".join("%d,%d\n" % (i // 2 + 1, i) for i in range(8))
                lines = (p if side == "p" else b).split("\n")
                lines[pos] = "x" + lines[pos]
                kw = {"p": "\n".join(lines), "b": b} if side == "p" else {"p": p, "b": "\n".join(lines)}
                s = Scratch()
                try:
                    s.write("p.csv", kw["p"])
                    s.write("b.csv", kw["b"])
                    a = ["--root", s.dir, "--join", "b.csv", "--on", "k=k:int", "--join-prefix", "r_", "--format", "json"]
                    m = run(*a, FLAG, "p.csv")
                    h = run(*a, "p.csv")
                    self.assertEqual((m.status, h.status), (8, 8), (side, pos, m.stdout[:300]))
                    self.assertEqual(canon(m.error()), canon(h.error()), (side, pos))
                    self.assertEqual(m.error()["rule"], "value.not-integer")
                finally:
                    s.cleanup()

    def test_ragged_rows_bad_quotes_and_long_lines_in_either_file(self):
        cases = []
        for side in ("p", "b"):
            text = self.P if side == "p" else self.B
            lines = text.split("\n")
            for name, edit in (("ragged", lambda l: l + ",extra"), ("bad quote", lambda l: l.replace(",", ',"x"y', 1)), ("unterminated", lambda l: l.replace(",", ',"x', 1)),
                               ("long", lambda l: l + "," + "y" * 80)):
                for pos in (3, 7):
                    ls = list(lines)
                    ls[pos] = edit(ls[pos])
                    cases.append((side, name, pos, "\n".join(ls)))
        for side, name, pos, text in cases:
            kw = {"p": text} if side == "p" else {"b": text}
            tag = (side, name, pos)
            m, h = self.both(flags=["--max-line-bytes", "60"], **kw)
            self.assertEqual(h.status, 8, (tag, h.stdout[:300]))
            self.assertEqual((m.status, canon(m.error())), (h.status, canon(h.error())), tag)

    def test_more_rows_than_max_rows_in_either_file(self):
        # the build has 8 rows and FILE 12: past 5 the build is refused, past 9 FILE is cut and says so
        m, h = self.both(flags=["--max-rows", "5"])
        self.assertEqual((m.status, h.status), (8, 8))
        self.assertEqual(m.error()["rule"], "limit.too-many-rows")
        self.assertEqual(canon(m.error()), canon(h.error()))
        m, h = self.both(flags=["--max-rows", "9"])
        self.assertEqual((m.status, h.status), (0, 0))
        self.assertEqual(m.data(), h.data())
        self.assertIs(m.data()["truncated"], True)

    def test_a_join_where_that_refuses_a_cell_of_the_build(self):
        b = "k,bv\na,1\na,x\nb,3\n"
        m, h = self.both(b=b, flags=["--join-where", "bv:int > 0"])
        self.assertEqual((m.status, h.status), (8, 8))
        self.assertEqual(canon(m.error()), canon(h.error()))
        self.assertEqual(m.error()["rule"], "value.not-integer")

    def test_json_lines_files_that_the_reader_refuses(self):
        s = Scratch()
        try:
            s.write("p.jsonl", '{"k":"a","pv":"1"}\n{"k":"b","pv":"2"}\n{"k":"c"}\n')
            s.write("b.jsonl", '{"k":"a","bv":"1"}\n{"k":"b","bv":"2"}\n{"k":"c","bv":3}\n')
            s.write("bad.jsonl", '{"k":"a","bv":"1"}\n{"k":"b","bv":\n')
            a = ["--root", s.dir, "--join", "b.jsonl", "--on", "k=k", "--join-prefix", "r_", "--format", "json", "--input-format", "jsonl", "--fields", "k,pv", "--join-input-format", "jsonl", "--join-fields", "k,bv"]
            m = run(*a, FLAG, "p.jsonl")
            h = run(*a, "p.jsonl")
            self.assertEqual((m.status, h.status), (8, 8), m.stdout[:300])
            self.assertEqual(canon(m.error()), canon(h.error()))
            self.assertEqual(m.error()["rule"], "parse.jsonl-missing-field")
            a[a.index("b.jsonl")] = "bad.jsonl"
            s.write("p.jsonl", '{"k":"a","pv":"1"}\n')
            m = run(*a, FLAG, "p.jsonl")
            h = run(*a, "p.jsonl")
            self.assertEqual((m.status, h.status), (8, 8), m.stdout[:300])
            self.assertEqual(canon(m.error()), canon(h.error()))
            self.assertEqual(m.error()["rule"], "parse.jsonl-syntax")
        finally:
            s.cleanup()

    def test_more_rows_than_max_rows_in_json_lines_files(self):
        s = Scratch()
        try:
            s.write("p.jsonl", "".join('{"k":"%s","pv":"%d"}\n' % (k, i) for i, k in enumerate("aabbccddeeff")))
            s.write("b.jsonl", "".join('{"k":"%s","bv":"%d"}\n' % (k, i) for i, k in enumerate("aabcdefg")))
            a = ["--root", s.dir, "--join", "b.jsonl", "--on", "k=k", "--join-prefix", "r_", "--format", "json", "--input-format", "jsonl", "--fields", "k,pv", "--join-input-format", "jsonl", "--join-fields", "k,bv"]
            for most, status in (("5", 8), ("9", 0), ("12", 0)):
                m = run(*a, "--max-rows", most, FLAG, "p.jsonl")
                h = run(*a, "--max-rows", most, "p.jsonl")
                self.assertEqual((m.status, h.status), (status, status), most)
                if status:
                    self.assertEqual(canon(m.error()), canon(h.error()), most)
                else:
                    self.assertEqual(m.data(), h.data(), most)
        finally:
            s.cleanup()

    def test_a_join_where_refusal_ends_the_merge_before_any_row_is_written(self):
        # the refusal is met when the first probe row asks for its group: nothing but the header of the csv has been written
        b = "k,bv\na,1\na,x\nb,3\n"
        s = Scratch()
        try:
            s.write("p.csv", self.P)
            s.write("b.csv", b)
            a = ["--root", s.dir, "--join", "b.csv", "--on", "k=k", "--join-prefix", "r_", "--format", "csv", "--join-where", "bv:int > 0"]
            m = run(*a, FLAG, "p.csv")
            self.assertEqual(m.status, 8)
            self.assertEqual(m.stdout.decode().splitlines(), ["k,pv,r_k,r_bv"])
            self.assertIn(b"value.not-integer", m.stderr)
        finally:
            s.cleanup()

    def test_a_filtered_probe_row_out_of_order_is_not_read(self):
        # the leading text condition of --where drops the row before its key is read: the order is the order of the keys the join reads
        p = "k,pv\na,1\nz,drop\nb,2\nc,3\n"
        s = Scratch()
        try:
            s.write("p.csv", p)
            s.write("b.csv", self.B)
            a = ["--root", s.dir, "--join", "b.csv", "--on", "k=k", "--join-prefix", "r_", "--format", "csv"]
            ok = run(*a, "--where", "pv != 'drop'", FLAG, "p.csv")
            self.assertEqual(ok.status, 0, ok.stderr)
            self.assertEqual(ok.stdout, run(*a, "--where", "pv != 'drop'", "p.csv").stdout)
            self.assertEqual(run(*a, FLAG, "p.csv").status, 8)
        finally:
            s.cleanup()

    def test_a_grouped_plan_over_a_file_the_reader_refuses(self):
        m, h = self.both(p=self.P.replace("c,4", "c,4,5"), flags=["--group", "k", "--agg", "count"])
        self.assertEqual((m.status, h.status), (8, 8))
        self.assertEqual(canon(m.error()), canon(h.error()))


class Threads(unittest.TestCase):
    """D: the same bytes at any thread count and chunk size, from a file or standard input."""

    def test_the_same_bytes(self):
        rng = random.Random(103)
        for kind in ("text", "int"):
            case = R9.sorted_case(rng, kind, 40, 50, dup=4, extras=1)
            env = Env(case)
            try:
                for jt in JTYPES:
                    base = env.run(env.plan(jt), "csv")
                    self.assertEqual(base.status, 0, base.stderr)
                    jbase = env.run(env.plan(jt), "json")
                    self.assertEqual(jbase.status, 0)
                    for threads in ("1", "2", "3", "4", "8", "16", "64"):
                        for chunk in ("1", "7", "64", "1000", "4096"):
                            extra = ["--threads", threads, "--parallel-min-bytes", "0", "--chunk-bytes", chunk]
                            t = env.run(env.plan(jt), "csv", extra)
                            self.assertEqual((t.status, t.stdout), (0, base.stdout), "%s %s %s %s" % (kind, jt, threads, chunk))
                    for fmt, ref in (("csv", base), ("json", jbase)):
                        s = env.run(env.plan(jt), fmt, operand=False)
                        self.assertEqual((s.status, s.stdout), (0, ref.stdout), "%s %s %s stdin" % (kind, jt, fmt))
                        s4 = env.run(env.plan(jt), fmt, ["--threads", "4"], operand=False)
                        self.assertEqual((s4.status, s4.stdout), (0, ref.stdout), "%s %s %s stdin threads" % (kind, jt, fmt))
            finally:
                env.cleanup()

    def test_a_page_stops_the_read_and_is_the_hash_joins_page(self):
        rng = random.Random(104)
        case = R9.sorted_case(rng, "text", 40, 60, dup=5, extras=1)
        env = Env(case)
        try:
            for jt in ("inner", "left"):
                for extra in (["--limit", "7"], ["--limit", "5", "--from", "3"], ["--max-join-rows", "50", "--limit", "10"]):
                    m = env.run(env.plan(jt), "csv", extra)
                    h = env.run(env.plan(jt), "csv", extra, sorted_=False)
                    self.assertEqual((m.status, m.stdout), (h.status, h.stdout), "%s %r" % (jt, extra))
        finally:
            env.cleanup()

    def test_max_join_rows_and_max_rows_refuse_as_the_hash_join_does(self):
        rng = random.Random(105)
        case = R9.sorted_case(rng, "text", 30, 40, dup=1, key_pool=["a", "b"])
        env = Env(case)
        try:
            m = env.run(env.plan("inner"), "json", ["--max-join-rows", "100"])
            h = env.run(env.plan("inner"), "json", ["--max-join-rows", "100"], sorted_=False)
            self.assertEqual(m.status, 8)
            self.assertEqual(m.error()["rule"], "limit.too-many-join-rows")
            for k in ("limit", "probe_row", "probe_line", "matches"):
                self.assertEqual(m.error()["detail"][k], h.error()["detail"][k], k)
            m = env.run(env.plan("inner"), "json", ["--max-rows", "10"])
            h = env.run(env.plan("inner"), "json", ["--max-rows", "10"], sorted_=False)
            self.assertEqual((m.status, m.error()["rule"]), (h.status, h.error()["rule"]))
        finally:
            env.cleanup()


# ---- E: the surface --------------------------------------------------------------------------------------------------------------------------------------------


class Surface(unittest.TestCase):
    """E: the flag, its rule, the help, `--explain`, the refusals of the flags, MCP, and fuzz."""

    def small(self):
        case = R3.Case("s", (["pk", "pg", "pv", "pt"], [["a", "x", "1", "p1"], ["b", "x", "2", "p2"]]), (["bk", "bg", "bv", "bt"], [["a", "x", "1", "q1"], ["b", "y", "2", "q2"]]), [(0, 0, None, False)], "", False)
        return Env(case)

    def test_the_flag_and_the_rule_are_in_the_tables(self):
        i = introspect()
        flags = {f["name"]: f for f in i["flags"]}
        self.assertIn("--join-sorted", flags)
        self.assertEqual(flags["--join-sorted"]["kind"], "bool")
        self.assertIn("ascending", flags["--join-sorted"]["help"])
        self.assertIn("join.not-sorted", flags["--join-sorted"]["help"])
        self.assertIn("--join-sorted", i["usage"])
        rules = {r["rule"]: r for r in i["rules"]}
        self.assertIn("join.not-sorted", rules)
        self.assertEqual((rules["join.not-sorted"]["exit"], rules["join.not-sorted"]["repairable"]), (8, "never"))
        self.assertIn("sorted", rules["join.not-sorted"]["summary"])
        self.assertIn("join-group", rules["limit.state-too-large"]["summary"])

    def test_the_flag_needs_a_join_and_does_not_go_with_a_query(self):
        env = self.small()
        try:
            a = ["--root", env.d, FLAG, "p.csv"]
            r = run(*a)
            self.assertEqual((r.status, r.first_rule()), (2, "args.required-flag"))
            r = run("--root", env.d, FLAG, "--query", "select a.pk from 'p.csv' as a join 'b.csv' as b on a.pk = b.bk", "--format", "json")
            self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"))
            r = run("--root", env.d, FLAG, "--join", "b.csv", "--on", "pk=bk", "--report", "types", "p.csv")
            self.assertEqual(r.status, 2)
        finally:
            env.cleanup()

    def test_explain_says_what_the_run_would_do(self):
        env = self.small()
        try:
            r = run(*env.argv(env.plan("left"), "json"), "--explain")
            self.assertEqual(r.status, 0, r.stdout[:400])
            self.assertEqual(validate(r), [], r.stdout[:300])
            j = r.data()["explain"]["join"]
            self.assertIs(j["sorted"], True)
            self.assertIn("key order", j["order"])
            self.assertIn("merge", j["work"]["class"])
            self.assertIn("group", j["work"]["first_output_after"])
            self.assertIn("merge", r.data()["explain"]["work"]["class"])
            self.assertEqual(j["work"]["threads"]["effective"], 1)
            self.assertIn("join.not-sorted", [x["rule"] if isinstance(x, dict) else x for x in r.data()["explain"]["refusals"]["possible"]])
            plain = run(*env.argv(env.plan("left"), "json", sorted_=False), "--explain")
            self.assertNotIn("sorted", plain.data()["explain"]["join"])
            self.assertIn("probe order", plain.data()["explain"]["join"]["order"])
            q = run(*env.argv(env.plan("left"), "json"), "--explain", "--threads", "8")
            self.assertEqual(q.data()["explain"]["join"]["work"]["threads"], {"requested": 8, "effective": 1, "reason": "the join is read on one core"})
        finally:
            env.cleanup()

    def test_the_mcp_definition_has_the_flag(self):
        text = (ROOT / "generated" / "mcp" / "tools.cho").read_text()
        self.assertIn("join-sorted", text)

    def test_a_sorted_join_of_a_semi_and_anti_with_a_prefix_is_still_refused_as_before(self):
        env = self.small()
        try:
            r = run(*env.argv(env.plan("semi"), "json"), "--join-prefix", "r_")
            self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"))
        finally:
            env.cleanup()

    def test_three_hundred_twenty_calls_no_trap_every_refusal_tagged(self):
        rng = random.Random(20261021)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        counts = {"runs": 0, "refused": 0, "not-sorted": 0, "ok": 0}
        junk = [[], [], [], ["--limit", "5"], ["--threads", "4"], ["--max-rows", "10"], ["--max-join-rows", "20"], ["--max-state-bytes", "300"], ["--max-state-bytes", "100"], ["--join-where", "bg != 'q'"],
                ["--max-line-bytes", "40"], ["--join-prefix", "r_"], ["--select", "#1"], ["--where", "pg = 'x'"], ["--explain"]]
        envs = []
        for name, build in scenes(seed=21):
            case = build()
            envs.append((name, Env(case)))
            for side in ("probe", "build"):
                b = R9.break_order(rng, build(), side)
                if b is not None:
                    envs.append((name + " " + side + " unsorted", Env(b[0])))
        try:
            while counts["runs"] < 330:
                name, env = rng.choice(envs)
                jt = rng.choice(JTYPES)
                plan = R3.random_plan(rng, env.case, jt) if rng.random() < 0.5 else env.plan(jt)
                extra = list(rng.choice(junk))
                fmt = rng.choice(["csv", "json"])
                try:
                    a = env.argv(plan, fmt, extra, operand=rng.random() < 0.8)
                except Exception:
                    continue
                r = run(*a, stdin=(env.d / R7.pname(env.pside)).read_bytes())
                counts["runs"] += 1
                self.assertNotIn(r.status, TRAPS, "%s %r" % (name, a))
                self.assertIn(r.status, (0, 2, 3, 8), "%s %r %r" % (name, a, r.stdout[:200]))
                if r.status == 0:
                    counts["ok"] += 1
                    continue
                counts["refused"] += 1
                doc = None
                try:
                    doc = json.loads(r.stdout.decode().splitlines()[0]) if r.stdout else None
                except ValueError:
                    doc = None
                if doc is not None and "error" in doc:
                    rule = doc["error"]["rule"]
                    self.assertIn(rule, rules, "%s %r" % (name, rule))
                    if rule == "join.not-sorted":
                        counts["not-sorted"] += 1
                else:
                    self.assertRegex(r.stderr.decode(), r"table: [a-z-]+\.[a-z-]+: ", "%s %r" % (name, a))
                    if "join.not-sorted" in r.stderr.decode():
                        counts["not-sorted"] += 1
        finally:
            for n, e in envs:
                e.cleanup()
        print("\nJ9 fuzz: %r" % counts, file=sys.stderr)
        self.assertGreaterEqual(counts["not-sorted"], 20)
        self.assertGreaterEqual(counts["ok"], 60)


# ---- F: a plain join is what it was ---------------------------------------------------------------------------------------------------------------------


class Unchanged(unittest.TestCase):
    """F: a call without `--join-sorted` is what it was."""

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

    def test_plain_joins_give_the_bytes_of_mains_binary(self):
        if not MAIN_TABLE or not os.path.exists(MAIN_TABLE):
            self.skipTest("MAIN_TABLE does not name the binary of main")
        rng = random.Random(106)
        n = 0
        for name, build in scenes(seed=33):
            case = build()
            env = Env(case)
            try:
                for jt in JTYPES:
                    plan = R3.random_plan(rng, case, jt) if name in ("text", "int") else env.plan(jt)
                    for fmt in ("csv", "json"):
                        for extra in ([], ["--explain"], ["--threads", "4"]):
                            a = env.argv(plan, fmt, extra, sorted_=False)
                            mine = run(*a)
                            theirs = main_run(a)
                            self.assertEqual((mine.status, mine.stdout, mine.stderr), (theirs.returncode, theirs.stdout, theirs.stderr), "%s %s %s %r" % (name, jt, fmt, extra))
                            n += 1
            finally:
                env.cleanup()
        print("\nJ9 unchanged: %d calls equal main's" % n, file=sys.stderr)
        self.assertGreaterEqual(n, 300)

    def test_a_flag_that_looks_like_it_is_still_unknown_without_the_dash_dash(self):
        r = run("--join-sorted-x", "a.csv")
        self.assertEqual((r.status, r.first_rule()), (2, "args.unknown-flag"))


if __name__ == "__main__":
    unittest.main()
