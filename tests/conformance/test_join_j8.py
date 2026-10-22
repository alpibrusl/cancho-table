"""J8 (docs/joins.md sections 7.2 and 24, slice J8): the partitioned build of a hash join.

With `--threads N` (N at least 2) and a `--join` file of at least `--parallel-min-bytes` of data, the build is read by N threads in ranges, each row is cut into one of N partitions by the hash of its key, N threads
build the partitions' tables, and the parent puts them together; the probe is read on one core, as before. The answer is the answer of the same call at `--threads 1`, byte for byte.

The gates are CORRECTNESS gates; no speed is judged or claimed anywhere. Written before the code, each able to fail (on `main`'s binary a join starts no thread and `--explain` has no `partitions`, so `Used` and `Explain`
fail there; the others pass on both, which is their point: they hold the partitioned build to the sequential one, and `Used` is what makes them test the partitioned path):

* A, the differential (`Identity`): for the shapes of J3's generator (the four join types, typed, composite and USING keys, a prefix, duplicate build keys, an empty build, keys in other columns, awkward quoted cells) and random
  plans, csv, json and standard input, 2, 4 and 8 threads with tiny and large ranges: the same status, standard output and standard error as `--threads 1`; and wide builds in every pair of formats (JSON lines and a
  delimiter of its own are read as before).
* B, the edges of the files (`Edges`): an empty build, a build of one row, of fewer rows than partitions, of one key repeated across every range (the chain is in build file order), blank lines, CRLF, a BOM, no final newline,
  quoted keys, empty keys, unicode, a record longer than a range, a quoted line break (the build is read by one thread: same bytes), the key in another column, `--join-where`, `--where` pushed to the build, pruning.
* C, the bound (`Bound`): the least `--max-state-bytes` at which the sequential build is accepted, found by bisection, is accepted by the partitioned build, and one byte less is refused with the same bytes
  (`limit.state-too-large`, its held bytes, its row, its line and its repair), for the four join types, text, `:int`, two pairs, USING and pruned builds; `--max-rows` at the row count and one past it.
* D, the refusals (`Refusals`): a ragged row early, late and in a later range, a typed cell that is not of its type, a cell `--join-where` refuses, a bad quote, an unterminated quote, a line past `--max-line-bytes`, a
  column collision, a missing key column: the same bytes as `--threads 1`.
* E, the threads are used (`Used`): under `strace -f`, an eligible join starts threads; at `--threads 1`, below `--parallel-min-bytes`, with `--join-sorted`, with a JSON-lines build, with a build delimiter of its own and
  with an empty build it starts none.
* F, every thread count (`Threads`): 1, 2, 3, 4, 5, 7, 8, 16 and 64 threads, ranges from 1 byte to the whole file, the same bytes.
* G, the explain (`Explain`): `data.explain.join.work.partitions` for an eligible build and its absence otherwise, valid against the schema; the rule is stated.
* H, a call that does not partition is what it was (`Unchanged`): the 166 plans of `scripts/corpus.py` keep their md5 `fccb01b66dd7518d74611219a6c5aa29`, and with `MAIN_TABLE` naming `main`'s binary every plain join and every
  `--explain` that is not eligible gives its bytes.
* I, no input reaches a panic (`Fuzz`): random plans over the scenes with junk flags and random bounds: no trap, every refusal tagged, and the bytes of `--threads 1`.
"""

import hashlib
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
import join_ref5 as R5
import join_ref7 as R7
from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate
from test_join_j3 import write_case
from test_join_j7 import describe

MAIN_TABLE = os.environ.get("MAIN_TABLE")
JTYPES = ("inner", "left", "semi", "anti")
BIGPAGE = ["--limit", "1000000", "--max-bytes", "67108864"]
STRACE = sys.platform.startswith("linux") and shutil.which("strace")
RULE = "the --join file is csv with the answer's delimiter, --threads is at least 2, and the data after its header is at least --parallel-min-bytes"
DECLINE = "a quoted line break, a ragged row, a refusal or the bound: the build is then read as one thread reads it, with the same bytes"


def part(threads, chunk):
    return ["--threads", str(threads), "--chunk-bytes", str(chunk), "--parallel-min-bytes", "0"]


def main_run(argv, stdin=b""):
    if not MAIN_TABLE or not os.path.exists(MAIN_TABLE):
        return None
    return subprocess.run([MAIN_TABLE, *[str(a) for a in argv]], capture_output=True, input=stdin, timeout=120)


ECHO = re.compile(rb',"--(?:threads|chunk-bytes|parallel-min-bytes)","[0-9]+"')


def triple(r):
    """What a run said, with the flags of the threads taken out of the invocation a refusal repeats (its repairs are the call written again, flags included)."""
    return (r.status, ECHO.sub(b"", r.stdout), ECHO.sub(b"", r.stderr))


def same_as_one_thread(test, argv, tag, variants=((2, 7), (4, 64), (8, 1000)), stdin=None):
    """The call at `--threads 1` and at each (threads, chunk) with `--parallel-min-bytes 0`: the same status, standard output and standard error. Answers the sequential result."""
    base = run(*argv, "--threads", "1", stdin=stdin)
    for t, c in variants:
        got = run(*argv, *part(t, c), stdin=stdin)
        test.assertEqual(triple(got), triple(base), "%s: %d threads, ranges of %d bytes" % (tag, t, c))
    return base


def clones(argv, stdin=b""):
    """How many threads a run starts (strace -f, Linux): the successful clone calls that share the address space."""
    p = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3", binary(), *[str(a) for a in argv]], capture_output=True, input=stdin, timeout=120)
    return len([ln for ln in p.stderr.decode("latin-1").splitlines() if "CLONE_THREAD" in ln and re.search(r"= \d+$", ln.strip())])


class Files:
    """Raw files under a scratch root, and the argv of a join over them."""

    def __init__(self):
        self.s = Scratch()
        self.d = self.s.dir

    def put(self, name, data):
        (self.d / name).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def argv(self, *flags, probe="p.csv", build="b.csv", on="k", fmt="csv"):
        return ["--root", str(self.d), "--join", build, "--on", on, "--format", fmt, *flags, probe]

    def cleanup(self):
        self.s.cleanup()


def build_rows(n, keys=7, width=6):
    return "".join("k%d,r%d,%s\n" % (i % keys, i, "x" * width) for i in range(n))


# ---- A: the differential -------------------------------------------------------------------------------------------------------------------------------------


class Identity(unittest.TestCase):
    """A: the partitioned build gives the bytes of the sequential build."""

    counts = {"plans": 0, "ok": 0, "refused": 0, "main": 0}

    def judge(self, d, case, plan, tag, pside=("csv", ","), bside=("csv", ","), probe=None):
        ph, bh = case.P[0], case.B[0]
        for fmt in ("csv", "json"):
            a = R7.argv_of(d, case, plan, plan["jtype"], pside, bside, fmt, None, [], True)
            if fmt == "json" and plan.get("limit") is None:
                a += BIGPAGE
            base = same_as_one_thread(self, a, "%s %s" % (tag, fmt))
            self.counts["plans"] += 1
            self.counts["ok" if base.status == 0 else "refused"] += 1
        if probe is not None:
            a = R7.argv_of(d, case, plan, plan["jtype"], pside, bside, "csv", None, [], False)
            same_as_one_thread(self, a, "%s stdin" % tag, variants=((4, 64),), stdin=probe)
        if MAIN_TABLE and os.path.exists(MAIN_TABLE):
            a = R7.argv_of(d, case, plan, plan["jtype"], pside, bside, "csv", None, [], True)
            mine = run(*a, *part(4, 7))
            theirs = main_run([*a, *part(4, 7)])
            self.assertEqual(triple(mine), (theirs.returncode, theirs.stdout, theirs.stderr), "%s: the bytes of main's binary" % tag)
            self.counts["main"] += 1

    def test_the_shapes_for_the_four_join_types(self):
        rng = random.Random(20261022)
        for case in R3.shapes():
            s = Scratch()
            try:
                write_case(s.dir, case)
                probe = (s.dir / "p.csv").read_bytes()
                for jt in JTYPES:
                    for i in range(3):
                        plan = R3.random_plan(rng, case, jt)
                        tag = "%s [%s] %d %r" % (case.name, jt, i, describe(plan))
                        with self.subTest(tag):
                            self.judge(s.dir, case, plan, tag, probe=probe)
            finally:
                s.cleanup()
        print("\nJ8 identity (shapes): %r (MAIN_TABLE %s)" % (self.counts, MAIN_TABLE), file=sys.stderr)
        self.assertGreaterEqual(self.counts["plans"], 300)
        self.assertGreaterEqual(self.counts["ok"], 150)

    def test_wide_builds_in_every_format(self):
        rng = random.Random(20261023)
        formats = [(("csv", ","), ("csv", ",")), (("jsonl", ","), ("csv", ",")), (("csv", ","), ("jsonl", ",")), (("csv", ";"), ("csv", ";")), (("csv", ","), ("csv", ";")), (("csv", "\t"), ("csv", "\t"))]
        int_keys = [k.decode() for k in R.INT_SPELLINGS[:8]]
        done = 0
        for fi, (pside, bside) in enumerate(formats):
            for name, mk in (("text", lambda: R7.wide_case(rng, "t")), ("int", lambda: R7.wide_case(rng, "i", keys=int_keys, pairs=[(0, 0, "int", False)])),
                             ("two", lambda: R7.wide_case(rng, "w", pairs=[(0, 0, None, False), (1, 1, None, False)])), ("using", lambda: R7.wide_case(rng, "u", shared_key=True, pairs=[(0, 0, None, True)], using=True)),
                             ("prefix", lambda: R7.wide_case(rng, "p", same=True, prefix="r_"))):
                case = mk()
                s = Scratch()
                try:
                    R7.write_scene(s.dir, case, pside, bside, random.Random(fi))
                    for jt in JTYPES:
                        plan = R3.random_plan(rng, case, jt)
                        tag = "%s %s/%s [%s] %r" % (name, pside, bside, jt, describe(plan))
                        with self.subTest(tag):
                            self.judge(s.dir, case, plan, tag, pside, bside)
                            done += 1
                finally:
                    s.cleanup()
        self.assertGreaterEqual(done, 100)


# ---- B: the edges of the files ------------------------------------------------------------------------------------------------------------------------------


class Edges(unittest.TestCase):
    """B: shapes of the build file, each equal to the sequential build at every thread count."""

    def setUp(self):
        self.f = Files()
        self.f.put("p.csv", "id,k\n" + "".join("%d,k%d\n" % (i, i % 9) for i in range(40)))

    def tearDown(self):
        self.f.cleanup()

    def check(self, build, tag, *flags, on="k", variants=((2, 1), (2, 7), (3, 13), (4, 64), (8, 1000), (64, 5))):
        self.f.put("b.csv", build)
        for jt in JTYPES:
            a = self.f.argv("--join-type", jt, *flags, on=on)
            base = same_as_one_thread(self, a, "%s [%s]" % (tag, jt), variants)
            with self.subTest(tag + " " + jt):
                self.assertIn(base.status, (0, 2, 3, 8), (tag, jt, base.stderr[:200]))
        return base

    def test_an_empty_build(self):
        self.check("k,r,x\n", "header only")
        self.check("k,r,x", "header only, no newline")
        self.check("k,r,x\n\n\n\n", "blank lines only")

    def test_a_build_of_one_row_and_of_fewer_rows_than_partitions(self):
        for n in (1, 2, 3, 5):
            self.check("k,r,x\n" + build_rows(n), "%d rows" % n)

    def test_one_key_repeated_across_every_range_keeps_the_build_file_order(self):
        base = self.check("k,r,x\n" + "".join("k3,r%d,pad\n" % i for i in range(300)), "one key")
        rows = [ln for ln in base.stdout.decode().splitlines() if ",k3," in ln or ln.endswith(",k3")]
        self.assertTrue(len(rows) >= 1 or base.status == 0)
        a = self.f.argv("--join-type", "inner", "--select", "id,r", "--limit", "5000")
        self.f.put("p.csv", "id,k\n1,k3\n2,k3\n")
        got = run(*a, *part(4, 64))
        self.assertEqual(triple(got), triple(run(*a, "--threads", "1")))
        self.assertEqual(got.stdout.decode().splitlines()[1:301], ["1,r%d" % i for i in range(300)])

    def test_keys_interleaved_over_ranges(self):
        rows = "k,r,x\n" + "".join("k%d,r%d,%s\n" % ((i * 7) % 11, i, "y" * (i % 13)) for i in range(400))
        self.check(rows, "interleaved")

    def test_blank_lines_crlf_bom_and_no_final_newline(self):
        self.check("k,r,x\n" + "k1,a,b\n\nk2,c,d\n\n\nk1,e,f\n", "blank lines")
        self.check("k,r,x\r\n" + "".join("k%d,r%d,z\r\n" % (i % 5, i) for i in range(60)), "crlf")
        self.check("﻿k,r,x\n" + build_rows(60), "bom")
        self.check("k,r,x\n" + build_rows(60).rstrip("\n"), "no final newline")
        self.check("k,r,x\r\n" + "k1,a,b\r\n\r\nk2,c,d\r\n", "crlf with a blank line")

    def test_quoted_cells_on_one_line(self):
        rows = 'k,r,x\n"a,b",1,"he said ""hi"""\n"k1",2,plain\nk2,"",\n"",3,empty key\n' + build_rows(30)
        base = self.check(rows, "quoted")
        self.f.put("p.csv", 'id,k\n1,"a,b"\n2,k1\n3,\n4,k2\n')
        self.check(rows, "quoted keys looked up")
        self.assertEqual(base.status, 0)

    def test_empty_keys_and_unicode(self):
        self.f.put("p.csv", "id,k\n1,\n2,été\n3,日本\n4,k1\n")
        self.check("k,r,x\n,e1,a\nété,e2,b\n日本,e3,c\n,e4,d\nk1,e5,e\n" + build_rows(40), "empty and unicode keys")

    def test_a_record_longer_than_a_range(self):
        rows = "k,r,x\n" + "".join("k%d,%s,%s\n" % (i % 4, "r%d" % i * 40, "w" * 90) for i in range(30))
        self.check(rows, "long records", variants=((2, 1), (4, 7), (8, 50)))

    def test_a_quoted_line_break_makes_the_build_be_read_by_one_thread_with_the_same_bytes(self):
        rows = 'k,r,x\n' + build_rows(20) + 'k1,"two\nlines",x\n' + build_rows(20) + 'k2,"a\n\nb",y\n'
        base = self.check(rows, "quoted line break")
        self.assertEqual(base.status, 0)
        self.check('k,r,x\n"k1\nk2",1,2\n' + build_rows(10), "a key over two lines")
        self.check('k,r,x\n' + build_rows(30) + 'k1,"x\n', "an unterminated quote at the end")

    def test_the_key_in_another_column_and_two_pairs(self):
        self.f.put("p.csv", "id,a,b\n" + "".join("%d,k%d,r%d\n" % (i, i % 5, i % 7) for i in range(30)))
        self.check("x,k,r\n" + "".join("p%d,k%d,r%d\n" % (i, i % 5, i % 9) for i in range(80)), "key in the middle", on="a=k")
        self.check("x,k,r\n" + "".join("p%d,k%d,r%d\n" % (i, i % 5, i % 9) for i in range(80)), "two pairs", on="a=k,b=r")

    def test_typed_keys(self):
        self.f.put("p.csv", "id,k\n" + "".join("%d,%s\n" % (i, v) for i, v in enumerate(["7", "007", "+7", "-0", "0", "12", "13", "x9"])))
        base = self.check("k,r,x\n" + "".join("%s,r%d,z\n" % (v, i) for i, v in enumerate(["7", "007", "+7", "-0", "0", "12"] * 8)), "int keys", on="k:int")
        self.assertEqual(base.status, 8, "the probe's x9 is not an integer")

    def test_join_where_where_pushdown_and_pruning(self):
        rows = "k,r,x\n" + "".join("k%d,r%d,%s\n" % (i % 6, i, "ab" if i % 3 else "cd") for i in range(120))
        self.check(rows, "--join-where", "--join-where", "x = 'ab'")
        self.check(rows, "--where on the build", "--where", "x = 'cd'")
        self.check(rows, "pruned", "--select", "id,r")
        self.check(rows, "pruned to the key", "--select", "id")
        self.check(rows, "grouped", "--group", "x", "--agg", "count")
        self.check(rows, "ordered", "--order-by", "r", "--select", "id,r", "--top", "10")

    def test_prefix_and_using(self):
        rows = "k,id,x\n" + "".join("k%d,b%d,z\n" % (i % 6, i) for i in range(60))
        self.check(rows, "prefix", "--join-prefix", "b_")
        self.f.put("p.csv", "k,id\n" + "".join("k%d,%d\n" % (i % 9, i) for i in range(20)))
        self.check(rows, "using", "--join-prefix", "b_", on="k")

    def test_a_probe_from_standard_input(self):
        self.f.put("b.csv", "k,r,x\n" + build_rows(80))
        probe = (self.f.d / "p.csv").read_bytes()
        for jt in JTYPES:
            a = ["--root", str(self.f.d), "--join", "b.csv", "--on", "k", "--join-type", jt, "--format", "csv"]
            same_as_one_thread(self, a, jt, ((4, 64),), stdin=probe)

    def test_a_page_of_json(self):
        self.f.put("b.csv", "k,r,x\n" + build_rows(80))
        for jt in JTYPES:
            a = self.f.argv("--join-type", jt, "--limit", "7", "--from", "3", fmt="json")
            same_as_one_thread(self, a, jt)


# ---- C: the bound ---------------------------------------------------------------------------------------------------------------------------------------------


def least_bound(argv, low=1, high=1 << 26):
    """The least `--max-state-bytes` at which the sequential build is accepted (status 0), by bisection; None when the call is not accepted at `high`."""
    if run(*argv, "--threads", "1", "--max-state-bytes", high).status != 0:
        return None
    while low < high:
        mid = (low + high) // 2
        if run(*argv, "--threads", "1", "--max-state-bytes", mid).status == 0:
            high = mid
        else:
            low = mid + 1
    return low


class Bound(unittest.TestCase):
    """C: the refusal is the sequential one, at the same byte."""

    def test_accepted_at_n_and_refused_with_the_same_bytes_one_below(self):
        rng = random.Random(7)
        f = Files()
        n = 0
        try:
            f.put("p.csv", "id,pk,ps\n" + "".join("%d,k%d,s%d\n" % (i, i % 9, i % 4) for i in range(30)))
            builds = {
                "text": "bk,bk2,r,x\n" + "".join("k%d,s%d,r%d,%s\n" % (i % 11, i % 5, i, "x" * rng.randrange(1, 9)) for i in range(150)),
                "dups": "bk,bk2,r,x\n" + "".join("k%d,s%d,r%d,%s\n" % (i % 3, i % 2, i, "x" * 20) for i in range(100)),
            }
            for name, text in builds.items():
                f.put("b.csv", text)
                for jt in JTYPES:
                    for on, extra in (("pk=bk", []), ("pk=bk,ps=bk2", []), ("pk=bk", ["--select", "id,r"])):
                        if extra and jt in ("semi", "anti"):
                            extra = ["--select", "id"]
                        a = ["--root", str(f.d), "--join", "b.csv", "--on", on, "--join-type", jt, "--format", "csv", *extra, "p.csv"]
                        lo = least_bound(a)
                        self.assertIsNotNone(lo, (name, jt, on))
                        for bound, status in ((lo, 0), (lo - 1, 8)):
                            if bound < 1:
                                continue
                            tag = "%s %s %s %r bound %d" % (name, jt, on, extra, bound)
                            base = run(*a, "--threads", "1", "--max-state-bytes", bound)
                            self.assertEqual(base.status, status, tag)
                            for t, c in ((2, 7), (4, 64), (8, 1000)):
                                got = run(*a, *part(t, c), "--max-state-bytes", bound)
                                self.assertEqual(triple(got), triple(base), "%s: %d threads, ranges of %d" % (tag, t, c))
                            n += 1
            # two pairs and USING and a typed key
            f.put("b.csv", "bk,bk2,r\n" + "".join("k%d,s%d,r%d\n" % (i % 6, i % 3, i) for i in range(90)))
            for on in ("pk=bk,ps=bk2", "pk=bk"):
                a = ["--root", str(f.d), "--join", "b.csv", "--on", on, "--join-prefix", "b_", "--format", "csv", "p.csv"]
                lo = least_bound(a)
                if lo is None:
                    continue
                for bound in (lo, lo - 1):
                    base = run(*a, "--threads", "1", "--max-state-bytes", bound)
                    for t, c in ((2, 7), (4, 64)):
                        self.assertEqual(triple(run(*a, *part(t, c), "--max-state-bytes", bound)), triple(base), (on, bound, t))
                    n += 1
        finally:
            f.cleanup()
        print("\nJ8 bound: %d (call, bound) pairs, each equal to the sequential bytes at 3 thread counts" % n, file=sys.stderr)
        self.assertGreaterEqual(n, 40)

    def test_a_typed_key_build_at_its_bound(self):
        f = Files()
        try:
            f.put("p.csv", "id,k\n" + "".join("%d,%d\n" % (i, i % 12) for i in range(30)))
            f.put("b.csv", "k,r\n" + "".join("%s,r%d\n" % (["7", "007", "+7", "12", "0", "-0"][i % 6], i) for i in range(100)))
            a = ["--root", str(f.d), "--join", "b.csv", "--on", "k:int", "--format", "csv", "p.csv"]
            lo = least_bound(a)
            self.assertIsNotNone(lo)
            for bound in (lo, lo - 1):
                base = run(*a, "--threads", "1", "--max-state-bytes", bound)
                self.assertEqual(base.status, 0 if bound == lo else 8)
                for t, c in ((2, 7), (4, 64), (8, 1000)):
                    self.assertEqual(triple(run(*a, *part(t, c), "--max-state-bytes", bound)), triple(base), (bound, t, c))
        finally:
            f.cleanup()

    def test_the_default_bound_and_a_bound_of_one(self):
        f = Files()
        try:
            f.put("p.csv", "id,k\n1,k1\n")
            f.put("b.csv", "k,r\n" + build_rows(50, width=3).replace(",x", ","))
            for bound in (1, 2, 16, 17, 100):
                a = ["--root", str(f.d), "--join", "b.csv", "--on", "k", "--format", "json", "--max-state-bytes", bound, "p.csv"]
                same_as_one_thread(self, a, "bound %d" % bound)
        finally:
            f.cleanup()

    def test_max_rows_at_the_count_and_one_past_it(self):
        f = Files()
        try:
            f.put("p.csv", "id,k\n1,k1\n")
            for rows in (1, 10, 50):
                f.put("b.csv", "k,r,x\n" + build_rows(rows))
                for most in (rows - 1, rows, rows + 1):
                    if most < 1:
                        continue
                    for jt in JTYPES:
                        a = ["--root", str(f.d), "--join", "b.csv", "--on", "k", "--join-type", jt, "--format", "csv", "--max-rows", most, "p.csv"]
                        base = same_as_one_thread(self, a, "%d rows, --max-rows %d [%s]" % (rows, most, jt))
                        self.assertEqual(base.status, 0 if most >= rows else 8, (rows, most, jt, base.stderr[:200], base.stdout[:200]))
        finally:
            f.cleanup()


# ---- D: the refusals --------------------------------------------------------------------------------------------------------------------------------------------


class Refusals(unittest.TestCase):
    """D: whatever the sequential build refuses, the partitioned one refuses with the same bytes."""

    def setUp(self):
        self.f = Files()
        self.f.put("p.csv", "id,k\n" + "".join("%d,%s\n" % (i, v) for i, v in enumerate(["7", "12", "k1", "3", "9"] * 6)))

    def tearDown(self):
        self.f.cleanup()

    def refused(self, build, tag, *flags, on="k", rule=None, status=8):
        self.f.put("b.csv", build)
        for fmt in ("csv", "json"):
            a = self.f.argv(*flags, on=on, fmt=fmt)
            base = same_as_one_thread(self, a, "%s %s" % (tag, fmt), ((2, 1), (2, 7), (4, 64), (8, 1000), (3, 33)))
            self.assertEqual(base.status, status, (tag, fmt, base.stdout[:300], base.stderr[:300]))
            if fmt == "json" and rule:
                self.assertEqual(base.first_rule(), rule, tag)

    def test_a_ragged_row_early_late_and_in_a_later_range(self):
        good = build_rows(200)
        self.refused("k,r,x\n" + "k1,short\n" + good, "ragged early")
        self.refused("k,r,x\n" + good + "k1,a,b,c\n", "ragged last")
        self.refused("k,r,x\n" + good[:1000].rsplit("\n", 1)[0] + "\nk1,too,many,cells,here\n" + good[1000:].split("\n", 1)[1], "ragged in the middle")
        self.refused("k,r,x\n" + good + "k1\n", "one cell")

    def test_a_typed_cell_that_is_not_of_its_type(self):
        keys = ["7", "12", "3"] * 30
        for at in (0, 40, 89):
            rows = list(keys)
            rows[at] = "1.5"
            self.refused("k,r\n" + "".join("%s,r%d\n" % (v, i) for i, v in enumerate(rows)), "bad int at %d" % at, on="k:int", rule="value.not-integer")

    def test_a_cell_join_where_refuses(self):
        rows = ["%d" % i for i in range(60)]
        rows[45] = "abc"
        self.refused("k,n\n" + "".join("k%d,%s\n" % (i % 5, v) for i, v in enumerate(rows)), "join-where cell", "--join-where", "n:int > 10")

    def test_bad_quotes(self):
        good = build_rows(80)
        self.refused("k,r,x\n" + good + 'k1,a"b,c\n', "a quote in an unquoted cell", status=0)
        self.refused("k,r,x\n" + good + 'k1,"ab"c,d\n', "text after a closing quote")
        self.refused("k,r,x\n" + good + 'k1,"never closed,d\n' + good, "an unterminated quote with rows after it")

    def test_a_line_past_the_line_bytes(self):
        self.refused("k,r,x\n" + build_rows(40) + "k1," + "z" * 300 + ",x\n" + build_rows(20), "long line", "--max-line-bytes", "200")

    def test_a_column_collision_and_a_missing_key_column(self):
        self.f.put("b.csv", "k,id,x\n" + build_rows(30))
        a = self.f.argv(on="k")
        base = same_as_one_thread(self, a, "collision")
        self.assertEqual(base.status, 2)
        self.f.put("b.csv", "q,r,x\n" + build_rows(30))
        base = same_as_one_thread(self, self.f.argv(on="k"), "missing key")
        self.assertEqual(base.status, 3)


# ---- E: the threads are used ------------------------------------------------------------------------------------------------------------------------------------


@unittest.skipUnless(STRACE, "needs Linux and strace")
class Used(unittest.TestCase):
    """E: an eligible join starts threads and an ineligible one does not."""

    def setUp(self):
        self.f = Files()
        self.f.put("p.csv", "id,k\n" + "".join("%d,k%d\n" % (i, i % 9) for i in range(40)))
        self.f.put("b.csv", "k,r,x\n" + build_rows(400))

    def tearDown(self):
        self.f.cleanup()

    def test_an_eligible_join_starts_threads_for_every_join_type(self):
        for jt in JTYPES:
            n = clones(self.f.argv("--join-type", jt, *part(4, 64)))
            self.assertGreaterEqual(n, 4, jt)

    def test_two_threads_start_threads_too(self):
        self.assertGreaterEqual(clones(self.f.argv(*part(2, 100))), 2)

    def test_one_thread_starts_none(self):
        self.assertEqual(clones(self.f.argv("--threads", "1", "--chunk-bytes", "64", "--parallel-min-bytes", "0")), 0)

    def test_a_build_below_parallel_min_bytes_starts_none(self):
        self.assertEqual(clones(self.f.argv("--threads", "4", "--chunk-bytes", "64")), 0)
        data = len((self.f.d / "b.csv").read_bytes()) - len("k,r,x\n")
        self.assertEqual(clones(self.f.argv("--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", data + 1)), 0)
        self.assertGreaterEqual(clones(self.f.argv("--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", data)), 4)

    def test_join_sorted_and_foreign_builds_start_none(self):
        self.f.put("p.csv", "id,k\n" + "".join("%d,k%d\n" % (i, i // 5) for i in range(40)))
        self.f.put("b.csv", "k,r,x\n" + "".join("k%d,r%d,x\n" % (i // 3, i) for i in range(200)))
        self.assertEqual(clones(self.f.argv("--join-sorted", *part(4, 64))), 0)
        self.f.put("b.jsonl", "".join(json.dumps({"k": "k%d" % (i // 3), "r": "r%d" % i, "x": "x"}) + "\n" for i in range(200)))
        self.assertEqual(clones(self.f.argv("--join-input-format", "jsonl", "--join-fields", "k,r,x", *part(4, 64), build="b.jsonl")), 0)
        self.f.put("bs.csv", "k;r;x\n" + "".join("k%d;r%d;x\n" % (i // 3, i) for i in range(200)))
        self.assertEqual(clones(self.f.argv("--join-delimiter", ";", *part(4, 64), build="bs.csv")), 0)

    def test_an_empty_build_starts_none(self):
        self.f.put("b.csv", "k,r,x\n")
        self.assertEqual(clones(self.f.argv(*part(4, 64))), 0)

    def test_a_declined_build_still_gives_the_sequential_bytes(self):
        self.f.put("b.csv", 'k,r,x\n' + build_rows(100) + 'k1,"a\nb",x\n')
        a = self.f.argv(*part(4, 64))
        self.assertGreaterEqual(clones(a), 1)
        self.assertEqual(triple(run(*a)), triple(run(*self.f.argv("--threads", "1"))))

    def test_the_probe_is_read_on_one_core(self):
        # a probe much larger than the build: the threads started are the build's (at most the ranges and the partitions), not one for each probe range
        self.f.put("b.csv", "k,r,x\n" + build_rows(30))
        self.f.put("p.csv", "id,k\n" + "".join("%d,k%d\n" % (i, i % 9) for i in range(3000)))
        small = clones(self.f.argv("--threads", "4", "--chunk-bytes", "100000", "--parallel-min-bytes", "0"))
        big = clones(self.f.argv("--threads", "4", "--chunk-bytes", "100", "--parallel-min-bytes", "0"))
        self.assertGreaterEqual(small, 1)
        self.assertLessEqual(big, small + 4 * 40, "the chunk size is the build's")


# ---- F: every thread count --------------------------------------------------------------------------------------------------------------------------------------


class Threads(unittest.TestCase):
    """F: 1 to 64 threads and ranges from one byte to the whole file."""

    def test_every_thread_count_and_range_size(self):
        f = Files()
        try:
            f.put("p.csv", "id,k\n" + "".join("%d,k%d\n" % (i, i % 9) for i in range(30)))
            f.put("b.csv", "k,r,x\n" + "".join("k%d,r%d,%s\n" % ((i * 5) % 8, i, "q" * (i % 5)) for i in range(120)))
            size = len((f.d / "b.csv").read_bytes())
            n = 0
            for jt in JTYPES:
                a = f.argv("--join-type", jt)
                base = run(*a, "--threads", "1")
                self.assertEqual(base.status, 0)
                for t in (1, 2, 3, 4, 5, 7, 8, 16, 64):
                    for c in (1, 7, 64, 500, size, 1 << 22):
                        got = run(*a, *part(t, c))
                        self.assertEqual(triple(got), triple(base), "%s %d threads, ranges of %d" % (jt, t, c))
                        n += 1
            self.assertEqual(n, 4 * 9 * 6)
        finally:
            f.cleanup()


# ---- G: the explain -----------------------------------------------------------------------------------------------------------------------------------------------


class Explain(unittest.TestCase):
    """G: `--explain` names the partitions of the build when the build would be partitioned, and nothing otherwise."""

    def setUp(self):
        self.f = Files()
        self.f.put("p.csv", "id,k\n" + "".join("%d,k%d\n" % (i, i % 9) for i in range(40)))
        self.f.put("b.csv", "k,r,x\n" + build_rows(400))
        self.data = len((self.f.d / "b.csv").read_bytes()) - len("k,r,x\n")

    def tearDown(self):
        self.f.cleanup()

    def explain(self, *flags, **kw):
        r = run(*self.f.argv(*flags, "--explain", **kw))
        self.assertEqual(r.status, 0, r.stderr[:300])
        self.assertEqual(validate(r), [])
        return r.data()["explain"]

    def test_the_object(self):
        x = self.explain("--threads", "4", "--chunk-bytes", "1000", "--parallel-min-bytes", "0")
        w = x["join"]["work"]
        rng = 1000
        self.assertEqual(w["partitions"], {"count": 4, "build_data_bytes": self.data, "range_bytes": rng, "ranges": -(-self.data // rng), "rule": RULE, "declines": DECLINE})
        self.assertEqual(w["threads"], {"requested": 4, "effective": 1, "reason": "the probe is read on one core; the build is partitioned (work.partitions)"})
        self.assertEqual(x["work"]["threads"], {"requested": 4, "effective": 1, "reason": "the probe is read on one core; the build is partitioned (join.work.partitions)"})
        self.assertTrue(any("--threads partitions the build of a join" in n for n in x["notes"]))
        self.assertFalse(any("has no effect on a join" in n for n in x["notes"]))

    def test_the_range_is_a_threads_share_when_the_chunk_is_larger(self):
        x = self.explain("--threads", "4", "--parallel-min-bytes", "0")
        p = x["join"]["work"]["partitions"]
        self.assertEqual((p["count"], p["range_bytes"], p["ranges"]), (4, -(-self.data // 4), 4))

    def test_absent_at_one_thread_below_the_minimum_and_for_other_builds(self):
        for flags in (["--threads", "1", "--parallel-min-bytes", "0"], ["--threads", "4"], ["--threads", "4", "--parallel-min-bytes", str(self.data + 1)], ["--parallel-min-bytes", "0"]):
            x = self.explain(*flags)
            self.assertNotIn("partitions", x["join"]["work"], flags)
            if "4" not in flags:
                self.assertEqual(x["join"]["work"]["threads"]["effective"], 1)
        x = self.explain("--threads", "4", "--parallel-min-bytes", "0", "--join-sorted")
        self.assertNotIn("partitions", x["join"]["work"])
        self.f.put("b.jsonl", "".join(json.dumps({"k": "k1", "r": "a", "x": "b"}) + "\n" for i in range(50)))
        x = self.explain("--threads", "4", "--parallel-min-bytes", "0", "--join-input-format", "jsonl", "--join-fields", "k,r,x", build="b.jsonl")
        self.assertNotIn("partitions", x["join"]["work"])
        self.f.put("bs.csv", "k;r;x\n" + "k1;a;b\n" * 50)
        x = self.explain("--threads", "4", "--parallel-min-bytes", "0", "--join-delimiter", ";", build="bs.csv")
        self.assertNotIn("partitions", x["join"]["work"])

    def test_the_boundary_of_the_minimum(self):
        self.assertIn("partitions", self.explain("--threads", "2", "--parallel-min-bytes", str(self.data))["join"]["work"])
        self.assertNotIn("partitions", self.explain("--threads", "2", "--parallel-min-bytes", str(self.data + 1))["join"]["work"])

    def test_an_empty_build_has_no_partitions(self):
        self.f.put("b.csv", "k,r,x\n")
        self.assertNotIn("partitions", self.explain("--threads", "4", "--parallel-min-bytes", "0")["join"]["work"])

    def test_the_explain_does_not_read_the_build(self):
        # the plan is said and not run: the same document whatever the rows hold (a ragged row, a quoted line break)
        a = self.explain("--threads", "4", "--chunk-bytes", "1000", "--parallel-min-bytes", "0")["join"]["work"]["partitions"]
        self.f.put("b.csv", "k,r,x\n" + build_rows(200) + 'k1,"a\nb",x\n' + "k1,short\n" + build_rows(10))
        size = len((self.f.d / "b.csv").read_bytes()) - len("k,r,x\n")
        b = self.explain("--threads", "4", "--chunk-bytes", "1000", "--parallel-min-bytes", "0")["join"]["work"]["partitions"]
        self.assertEqual((b["count"], b["build_data_bytes"]), (4, size))
        self.assertEqual({k: v for k, v in a.items() if k in ("rule", "declines")}, {k: v for k, v in b.items() if k in ("rule", "declines")})


# ---- H: a call that does not partition is what it was ----------------------------------------------------------------------------------------------------------


class Unchanged(unittest.TestCase):
    """H: nothing else moves."""

    def test_the_md5_of_the_166_plans(self):
        need = [ROOT / "build" / "bench" / "data.csv", ROOT / "build" / "adv" / "f2.csv", ROOT / "build" / "adv" / "quoted.csv", ROOT / "build" / "adv" / "long.csv", ROOT / "build" / "adv" / "wide.csv", ROOT / "build" / "num" / "data.csv"]
        if not all(p.exists() for p in need):
            self.skipTest("the files of the corpus are not here (scripts/bench.py, scripts/adversarial.py, scripts/bench_numbers.py make them)")
        env = dict(os.environ, TABLE=binary())
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "corpus.py")], capture_output=True, env=env, timeout=3000)
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        lines = p.stdout.decode().splitlines()
        self.assertEqual(len(lines), 166)
        self.assertEqual(hashlib.md5(p.stdout).hexdigest(), "fccb01b66dd7518d74611219a6c5aa29")

    def test_plain_joins_and_ineligible_explains_give_the_bytes_of_mains_binary(self):
        if not MAIN_TABLE or not os.path.exists(MAIN_TABLE):
            self.skipTest("MAIN_TABLE does not name the binary of main")
        rng = random.Random(108)
        n = 0
        for case in R3.shapes():
            s = Scratch()
            try:
                write_case(s.dir, case)
                for jt in JTYPES:
                    plan = R3.random_plan(rng, case, jt)
                    for fmt in ("csv", "json"):
                        a = R7.argv_of(s.dir, case, plan, jt, ("csv", ","), ("csv", ","), fmt, None, [], True)
                        if fmt == "json" and plan.get("limit") is None:
                            a += BIGPAGE
                        for extra in ([], ["--explain"], ["--threads", "4"], ["--threads", "4", "--explain"], ["--threads", "1", "--parallel-min-bytes", "0", "--explain"]):
                            mine = run(*a, *extra)
                            theirs = main_run([*a, *extra])
                            self.assertEqual(triple(mine), (theirs.returncode, theirs.stdout, theirs.stderr), "%s %s %s %r" % (case.name, jt, fmt, extra))
                            n += 1
            finally:
                s.cleanup()
        print("\nJ8 unchanged: %d calls equal main's" % n, file=sys.stderr)
        self.assertGreaterEqual(n, 500)


# ---- I: no input reaches a panic ----------------------------------------------------------------------------------------------------------------------------------


class Fuzz(unittest.TestCase):
    """I: junk flags and bounds over the scenes, at the bytes of one thread."""

    def test_three_hundred_calls(self):
        rng = random.Random(20261024)
        rules = {r["rule"] for r in introspect()["rules"]} if "rules" in introspect() else None
        counts = {"refused": 0, "answered": 0}
        junk = [[], ["--join-prefix", "b_"], ["--limit", "3"], ["--select", "#1"], ["--max-rows", "40"], ["--max-join-rows", "50"], ["--max-line-bytes", "30"], ["--join-where", "x != 'x'"], ["--absent", "empty"],
                ["--max-state-bytes", "300"], ["--max-state-bytes", "3000"], ["--where", "r != 'r1'"]]
        for case in R3.shapes():
            s = Scratch()
            try:
                write_case(s.dir, case)
                for i in range(20 if case.name.startswith(("dup", "typed", "two", "same", "using")) else 8):
                    jt = rng.choice(JTYPES)
                    plan = R3.random_plan(rng, case, jt)
                    a = R7.argv_of(s.dir, case, plan, jt, ("csv", ","), ("csv", ","), rng.choice(["csv", "json"]), None, [], True)
                    for flag in rng.sample(junk, 2):
                        a += flag
                    t, c = rng.choice([(2, 1), (3, 7), (4, 64), (8, 1000), (64, 9)])
                    base = run(*a, "--threads", "1")
                    got = run(*a, *part(t, c))
                    tag = "%s [%s] %r" % (case.name, jt, a[-8:])
                    self.assertNotIn(got.status, TRAPS, tag)
                    self.assertEqual(triple(got), triple(base), tag)
                    if got.status == 0:
                        counts["answered"] += 1
                    else:
                        counts["refused"] += 1
                        if "--format" in a and a[a.index("--format") + 1] == "json":
                            self.assertIn(got.first_rule() or "x", rules or {got.first_rule() or "x"}, tag)
            finally:
                s.cleanup()
        print("\nJ8 fuzz: %r" % counts, file=sys.stderr)
        self.assertGreaterEqual(counts["refused"] + counts["answered"], 150)


if __name__ == "__main__":
    unittest.main()
