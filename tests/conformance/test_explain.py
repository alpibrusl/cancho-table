"""Q2 (docs/query.md sections 9 and 15): `--explain` prints the plan the flags or `--query` resolve to, as one JSON line, and does not run it.

The gates, each of them able to fail (scripts/explain_mutants.py breaks the tool in the ways listed there and every one must be noticed):

* **X1** the plan is not its spelling: a random table and plan written as flags and as a query give the same explain, byte for byte (`scripts/explain_corpus.py` is the same
  gate over the 166 runs of `scripts/corpus.py`).
* **X2** explain never executes: it reads the first chunk of a file for the header and nothing after it (the document's own `bytes_read`, and under strace on Linux the bytes
  the process reads), it does not touch standard input, it writes no result, and it is as fast on a file of 2 GB as on one of 2 KB. The cells and the records after the header are
  not looked at: a file whose rows are all ragged, or whose cells are all unreadable as the types asked, explains.
* **X3** the same refusals as the run: a plan the run refuses before it has read a record (and a header it refuses) is refused by `--explain` with the run's bytes and exit
  status; a plan the run refuses because of the data is explained and the rule it raised is in `refusals.possible`; and a plan the run answers is explained.
* **X4** fuzz: the strings and flag soups of the run, with `--explain`: no trap, only tagged refusals, one line, the same rule as the run's.
* **X5** `output.columns` is the columns of the real answer; **X6** the threads decision is the run's (the document's, and the threads strace counts on Linux).

TABLE_EXPLAIN_CASES (default 150) sets the random plans of X1/X3/X5, TABLE_EXPLAIN_FUZZ (default 1200) the strings of X4, TABLE_EXPLAIN_SEED their seed.
"""

import concurrent.futures
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import query_gen
from test_query import PIECES, SEEDS, comparable, doc, mutate, raw, rule_of, soup
from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate

CASES = int(os.environ.get("TABLE_EXPLAIN_CASES", "150"))
FUZZ = int(os.environ.get("TABLE_EXPLAIN_FUZZ", "1200"))
SEED = int(os.environ.get("TABLE_EXPLAIN_SEED", "21"))
TINY = ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "65536"]
# what only the data can refuse: a run refused with one of these is explained
DATA = re.compile(r"^(value\.|parse\.|limit\.(?!header-too-large)|agg\.float-overflow)")
LINUX_STRACE = sys.platform.startswith("linux") and shutil.which("strace")


def explain_of(result):
    d = doc(result)
    assert d is not None and d.get("ok") is True, result
    return d["data"]["explain"]


def can_fail(result):
    return explain_of(result)["refusals"]["possible"]


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)
        self.cwd = self.s.dir
        self.s.write("f.csv", "a,b,c\n1,x,1.5\n2,y,2.5\n3,x,0.25\n")

    def x(self, *flags, name="f.csv", stdin=None):
        return run("--explain", *flags, *([name] if name else []), cwd=self.cwd, stdin=stdin)

    def r(self, *flags, name="f.csv", stdin=None):
        return run(*flags, *([name] if name else []), cwd=self.cwd, stdin=stdin)


# ---- X1: the plan is not its spelling ------------------------------------------------------------------------------------------

class Spelling(unittest.TestCase):
    def test_flags_and_query_explain_the_same_bytes(self):
        rnd = random.Random(SEED)
        seen = {"ok": 0, "refused": 0}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.csv")
            for case in range(CASES):
                names, kinds, rows = query_gen.make_table(rnd)
                query_gen.write_csv(path, names, rows)
                plan = query_gen.make_plan(rnd, names, kinds, rows)
                flags = query_gen.flag_form(plan)
                from_clause = "'f.csv'" if rnd.random() < 0.5 else None
                query = query_gen.query_form(rnd, plan, from_clause)
                extra = TINY if case % 4 == 0 else []
                for fmt in ("json", "csv"):
                    a = run("--explain", *flags, "--format", fmt, *extra, "f.csv", cwd=d)
                    b = run("--explain", "--query", query, "--format", fmt, *extra, *([] if from_clause else ["f.csv"]), cwd=d)
                    if a.status == 0:
                        seen["ok"] += 1
                        self.assertEqual(a.stdout, b.stdout, "case %d\n  flags: %r\n  query: %r\n  table: %r" % (case, flags, query, names))
                        self.assertEqual(a.stderr, b.stderr)
                        self.assertEqual(b.status, 0)
                    else:
                        seen["refused"] += 1
                        self.assertEqual(comparable(a), comparable(b), "case %d\n  flags: %r\n  query: %r\n  table: %r" % (case, flags, query, names))
        self.assertGreater(seen["ok"], CASES // 2, seen)
        self.assertGreater(seen["refused"], 0, seen)

    def test_the_same_plan_twice_is_the_same_bytes(self):
        s = Scratch()
        self.addCleanup(s.cleanup)
        s.write("f.csv", "a,b\n1,2\n")
        a = run("--explain", "--group", "a", "--agg", "sum:b", "f.csv", cwd=s.dir)
        b = run("--explain", "--group", "a", "--agg", "sum:b", "f.csv", cwd=s.dir)
        self.assertEqual(a.stdout, b.stdout)


# ---- X3, X5: the run's refusals, and the columns of the answer ---------------------------------------------------------------------

class Refusals(unittest.TestCase):
    def test_a_run_and_its_explain_over_random_plans(self):
        rnd = random.Random(SEED + 1)
        seen = {"answered": 0, "static": 0, "data": 0, "columns": 0}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.csv")
            for case in range(CASES):
                names, kinds, rows = query_gen.make_table(rnd)
                query_gen.write_csv(path, names, rows)
                plan = query_gen.make_plan(rnd, names, kinds, rows)
                flags = query_gen.flag_form(plan)
                label = "case %d\n  flags: %r\n  table: %r" % (case, flags, names)
                r = run(*flags, "f.csv", cwd=d)
                x = run("--explain", *flags, "f.csv", cwd=d)
                rule = rule_of(r)
                if rule is None:
                    seen["answered"] += 1
                    self.assertEqual(x.status, 0, label)
                    self.assertEqual(validate(x), [], label)
                    self.assertEqual(explain_of(x)["output"]["columns"], r.data()["columns"], label)
                    seen["columns"] += 1
                elif DATA.match(rule):
                    seen["data"] += 1
                    self.assertEqual(x.status, 0, (label, rule, x.stdout[:300]))
                    self.assertEqual(validate(x), [], label)
                    self.assertIn(rule, can_fail(x), label)
                    # every rule the run raised, in every error it listed
                    for e in r.doc().get("errors", []):
                        self.assertIn(e["rule"], can_fail(x), label)
                else:
                    seen["static"] += 1
                    self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr), label)
        self.assertGreater(seen["answered"], 5, seen)
        self.assertGreater(seen["static"], 3, seen)
        self.assertGreater(seen["data"], 3, seen)


class Header(Base):
    """What needs the header, refused as the run refuses it."""

    PLANS = [["--select", "a"], ["--select", "nosuch"], ["--where", "nosuch = 1"], ["--group", "a"], ["--group", "nosuch"], ["--group", "a", "--agg", "sum:nosuch"], ["--select", "#9"],
             ["--group", "a", "--sort", "-nosuch"], ["--order-by", "nosuch"], ["--report", "types"], ["--where", "a:int > 1 and a:float < 3"], ["--group", "a", "--agg", "sum:a:float", "--where", "a:int > 1"]]

    def same_as_the_run(self, *flags, name="f.csv"):
        r, x = self.r(*flags, name=name), self.x(*flags, name=name)
        if r.status != 0 and not DATA.match(rule_of(r) or ""):
            self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr), (flags, r.stdout[:300]))
            return r, x
        self.assertEqual(x.status, 0, (flags, x.stdout[:300]))
        return r, x

    def test_a_name_the_header_lacks(self):
        for flags in self.PLANS:
            with self.subTest(flags=flags):
                self.same_as_the_run(*flags)
        r, x = self.same_as_the_run("--select", "nosuch")
        self.assertEqual(r.first_rule(), "select.unknown-column")
        self.assertEqual(x.first_rule(), "select.unknown-column")

    def test_the_shape_has_a_header_too(self):
        for text in ("a,b\n", "", "\n\n", "a,b\n1,2\n"):
            self.s.write("g.csv", text)
            r, x = self.r(name="g.csv"), self.x(name="g.csv")
            self.assertEqual((x.status, x.first_rule() if x.status else None), (r.status, r.first_rule() if r.status else None), text)

    def test_empty_and_odd_headers(self):
        cases = {
            "empty": b"", "blank": b"\n\r\n\n", "bom": b"\xef\xbb\xbfa,b\n1,2\n", "crlf": b"a,b\r\n1,2\r\n", "blank first": b"\n\na,b\n1,2\n",
            "quoted newline": b'"a\nb",c\n1,2\n', "bad quote": b'"a"x,b\n1,2\n', "unterminated": b'"a,b\n1,2\n', "no newline": b"a,b", "duplicate": b"a,a\n1,2\n",
            "header only": b"a,b\n",
        }
        for label, data in cases.items():
            self.s.write("h.csv", data)
            for flags in ([], ["--select", "a"], ["--group", "a"], ["--group", "b", "--agg", "count"], ["--report", "types"], ["--rows"], ["--where", "a = 1"]):
                with self.subTest(case=label, flags=flags):
                    self.same_as_the_run(*flags, name="h.csv")

    def test_a_header_past_max_line_bytes(self):
        self.s.write("w.csv", "a," + "b" * 5000 + "\n1,2\n")
        for flags in ([], ["--select", "a"]):
            for extra in (["--max-line-bytes", "1000"], ["--max-line-bytes", "100000"]):
                with self.subTest(flags=flags, extra=extra):
                    self.same_as_the_run(*flags, *extra, name="w.csv")
        r = self.r("--select", "a", "--max-line-bytes", "1000", name="w.csv")
        self.assertIn(r.first_rule(), ("limit.line-too-long", "limit.header-too-large"))
        # a quoted header that spans lines past the bound
        self.s.write("w2.csv", '"' + "x\n" * 600 + '",b\n1,2\n')
        self.same_as_the_run("--select", "b", "--max-line-bytes", "1000", name="w2.csv")

    def test_a_file_that_cannot_be_read(self):
        for flags in ([], ["--select", "a"]):
            r, x = self.r(*flags, name="missing.csv"), self.x(*flags, name="missing.csv")
            self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr))
            self.assertEqual(x.first_rule(), "io.not-found")
        self.s.write("locked.csv", "a\n1\n")
        os.chmod(self.s.dir / "locked.csv", 0)
        try:
            if os.geteuid() != 0:
                r, x = self.r("--select", "a", name="locked.csv"), self.x("--select", "a", name="locked.csv")
                self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr))
                self.assertEqual(x.first_rule(), "io.permission-denied")
        finally:
            os.chmod(self.s.dir / "locked.csv", 0o600)
        for flags in (["--root", str(self.s.dir), "--select", "a", "../f.csv"], ["--root", str(self.s.dir), "--select", "a", "/etc/passwd"]):
            r, x = run(*flags, cwd=self.cwd), run("--explain", *flags, cwd=self.cwd)
            self.assertNotEqual(r.status, 0)
            self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr))

    def test_the_checks_of_the_flags_are_the_runs(self):
        for flags in (["--limit", "5"], ["--sort", "-count"], ["--top", "3"], ["--select", "a", "--group", "b"], ["--group", "a", "--order-by", "b"], ["--delimiter", "x", "--select", "a"],
                      ["--threads", "0", "--select", "a"], ["--select", "a", "--format", "text"], ["--max-groups", "0", "--group", "a"], ["--report", "other"], ["--rows", "--select", "a"],
                      ["--query", "select"], ["--query", "select a", "--select", "a"], ["--query", "select a, count(*)"], ["--query", "select a where b or c"], ["--format", "csv"]):
            with self.subTest(flags=flags):
                r, x = self.r(*flags), self.x(*flags)
                self.assertNotEqual(r.status, 0, flags)
                self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr))

    def test_a_csv_plan_is_explained_as_a_document(self):
        x = self.x("--select", "a", "--format", "csv")
        self.assertEqual(x.status, 0, x)
        self.assertEqual(x.stderr, b"")
        e = explain_of(x)
        self.assertEqual(e["output"]["format"], "csv")
        self.assertEqual(e["output"]["columns"], ["a"])
        # and its refusals are the run's, as sentences on standard error
        r, x = self.r("--select", "nosuch", "--format", "csv"), self.x("--select", "nosuch", "--format", "csv")
        self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr))
        self.assertTrue(x.stderr.startswith(b"table: "), x.stderr)
        # the shape as text is a plan too
        t = self.x("--format", "text")
        self.assertEqual(t.status, 0, t)
        self.assertEqual(explain_of(t)["output"]["format"], "text")


# ---- X2: explain never executes ---------------------------------------------------------------------------------------------------

class Never(Base):
    def big(self, name, rows, ragged=False):
        # about 60 bytes a row; a bad row every so often
        with open(self.s.dir / name, "w") as f:
            f.write("id,status,bytes,note\n")
            for i in range(rows):
                if ragged and i % 7 == 3:
                    f.write("%d,only-two\n" % i)
                else:
                    f.write("%d,%s,%d,note number %d\n" % (i, 200 + i % 5, i, i))

    def test_the_header_is_the_only_thing_read(self):
        self.big("big.csv", 60000)            # about 2 MB
        size = (self.s.dir / "big.csv").stat().st_size
        self.assertGreater(size, 1 << 20)
        for flags in ([], ["--select", "id"], ["--group", "status", "--agg", "sum:bytes"], ["--where", "bytes:int > 5", "--order-by", "-bytes:int"], ["--report", "types"], ["--rows"],
                      ["--select", "id", "--format", "csv"]):
            with self.subTest(flags=flags):
                e = explain_of(self.x(*flags, name="big.csv"))
                self.assertEqual(e["source"]["kind"], "file")
                self.assertTrue(e["source"]["header_read"])
                self.assertEqual(e["source"]["bytes_read"], 65536, e["source"])
                self.assertEqual(e["source"]["size"], size)
        self.s.write("tiny.csv", "a,b\n1,2\n")
        self.assertEqual(explain_of(self.x(name="tiny.csv"))["source"]["bytes_read"], 8)

    def test_a_result_is_not_computed_or_written(self):
        self.big("big.csv", 60000)
        for flags in (["--select", "id"], ["--group", "status"], ["--select", "id", "--format", "csv"], ["--rows", "--format", "csv"]):
            x = self.x(*flags, name="big.csv")
            self.assertEqual(x.status, 0)
            self.assertEqual(len(x.stdout.splitlines()), 1, flags)
            d = x.doc()
            self.assertEqual(list(d["data"]), ["explain"], flags)
            self.assertLess(len(x.stdout), 6000, flags)

    def test_rows_and_cells_after_the_header_are_not_looked_at(self):
        # every row ragged, every cell unreadable as what the plan asks, a quote that never closes: the run refuses, explain describes
        self.s.write("bad.csv", 'a,b,c\n"x"y,2\n,,,,\nzz,"unterminated\n')
        for flags in (["--select", "a"], ["--group", "b", "--agg", "sum:c"], ["--where", "a:int > 1"], ["--order-by", "a:float"], ["--report", "types"], []):
            with self.subTest(flags=flags):
                r = self.r(*flags, name="bad.csv")
                self.assertNotEqual(r.status, 0, flags)
                self.assertTrue(DATA.match(r.first_rule()), r.first_rule())
                x = self.x(*flags, name="bad.csv")
                self.assertEqual(x.status, 0, x.stdout[:300])
                self.assertIn(r.first_rule(), can_fail(x))
        self.big("ragged.csv", 5000, ragged=True)
        x = self.x("--select", "id", "--max-rows", "10", name="ragged.csv")
        self.assertEqual(x.status, 0)

    def test_a_file_of_two_gigabytes_is_explained_in_milliseconds(self):
        path = self.s.dir / "huge.csv"
        with open(path, "wb") as f:
            f.write(b"id,status,bytes\n1,200,3\n")
            f.truncate(2 << 30)                 # a sparse file: reading it would take seconds and megabytes of zeros
        started = time.time()
        x = self.x("--group", "status", "--agg", "sum:bytes", "--threads", "4", name="huge.csv")
        took = time.time() - started
        self.assertEqual(x.status, 0, x.stdout[:300])
        e = explain_of(x)
        self.assertEqual(e["source"]["size"], 2 << 30)
        self.assertEqual(e["source"]["bytes_read"], 65536)
        self.assertEqual(e["work"]["threads"]["effective"], 4)
        self.assertLess(took, 1.0, "explain of a 2 GB file took %.3f s" % took)

    def test_standard_input_is_not_consumed(self):
        data = b"a,b,c\n1,2,3\n4,5,6\n"
        for flags in ([], ["--select", "a"], ["--group", "a"], ["--select", "nosuch"]):
            for form in ("file", "pipe"):
                script = '%s --explain %s > /dev/null; cat' % (binary(), " ".join(flags))
                if form == "file":
                    (self.s.dir / "in.csv").write_bytes(data)
                    p = subprocess.run(["sh", "-c", script], stdin=open(self.s.dir / "in.csv", "rb"), capture_output=True, timeout=30)
                else:
                    p = subprocess.run(["sh", "-c", script], input=data, capture_output=True, timeout=30)
                self.assertEqual(p.stdout, data, (flags, form, "explain consumed standard input"))
        # and says so
        x = self.x("--select", "a", name="-", stdin=data)
        self.assertEqual(x.status, 0, x.stdout)
        e = explain_of(x)
        self.assertEqual(e["source"], {"kind": "stdin", "path": "-", "reader": "blocks", "block_bytes": 65536, "format": "csv", "delimiter": ",", "size": None, "header_read": False,
                                       "columns": None, "bytes_read": 0})
        self.assertIsNone(e["output"]["columns"])
        x = run("--explain", "--select", "a", cwd=self.cwd, stdin=data)
        self.assertEqual(explain_of(x)["source"]["kind"], "stdin")

    def test_stdin_columns_are_not_known_and_the_names_that_may_be_wrong_are_possible(self):
        x = run("--explain", "--select", "nosuch", "--where", "zz = 1", "--format", "json", cwd=self.cwd, stdin=b"a\n1\n")
        self.assertEqual(x.status, 0)
        p = can_fail(x)
        for rule in ("select.unknown-column", "column.unknown", "limit.header-too-large", "parse.csv-ragged-row"):
            self.assertIn(rule, p)
        # the same plan on a file: the header is known, so these are decided and not possible
        y = self.x("--select", "a", "--where", "b = 1")
        for rule in ("select.unknown-column", "column.unknown", "limit.header-too-large"):
            self.assertNotIn(rule, can_fail(y))

    @unittest.skipUnless(LINUX_STRACE, "needs Linux and strace")
    def test_strace_the_bytes_read_from_the_file(self):
        self.big("big.csv", 200000)       # about 6 MB
        for flags in ([], ["--select", "id"], ["--group", "status", "--threads", "4", "--parallel-min-bytes", "0"], ["--order-by", "-bytes:int"]):
            log = str(self.s.dir / "strace.log")
            subprocess.run(["strace", "-f", "-o", log, "-e", "trace=read,pread64,readv,openat,clone,clone3", binary(), "--explain", "--root", str(self.s.dir), *flags, "big.csv"],
                           capture_output=True, input=b"", check=True)
            text = open(log).read()
            fd = None
            read = 0
            for line in text.splitlines():
                m = re.search(r'openat\(.*"big.csv".*\) = (\d+)', line)
                if m:
                    fd = m.group(1)
                m = re.search(r"(?:read|pread64)\((\d+), .*\) = (\d+)$", line)
                if m and m.group(1) == fd:
                    read += int(m.group(2))
            self.assertIsNotNone(fd, text[:2000])
            self.assertLessEqual(read, 65536 + 4096, (flags, read))
            self.assertGreater(read, 0)
            self.assertNotIn("clone", text, (flags, "explain started a thread"))

    @unittest.skipUnless(LINUX_STRACE, "needs Linux and strace")
    def test_strace_standard_input_is_not_read(self):
        log = str(self.s.dir / "strace.log")
        subprocess.run(["strace", "-f", "-o", log, "-e", "trace=read,pread64,readv", binary(), "--explain", "--select", "a"], capture_output=True, input=b"a,b\n1,2\n", check=True)
        self.assertEqual([l for l in open(log).read().splitlines() if re.search(r"read\(0,", l)], [])


# ---- X6: the threads decision -----------------------------------------------------------------------------------------------------

class Threads(Base):
    CASES = [
        # flags, effective, a word of the reason ("" is none)
        (["--group", "b", "--threads", "4", "--parallel-min-bytes", "0"], 4, ""),
        (["--group", "b", "--threads", "4"], 1, "parallel-min-bytes"),
        (["--group", "b", "--threads", "1", "--parallel-min-bytes", "0"], 1, ""),
        (["--threads", "4", "--parallel-min-bytes", "0"], 1, "shape"),
        (["--order-by", "a:int", "--threads", "4", "--parallel-min-bytes", "0"], 1, "--order-by"),
        (["--select", "a", "--from", "1", "--threads", "4", "--parallel-min-bytes", "0"], 1, "--from"),
        (["--group", "b", "--from", "1", "--threads", "4", "--parallel-min-bytes", "0"], 4, ""),
        (["--select", "a", "--threads", "3", "--parallel-min-bytes", "0"], 3, ""),
        (["--report", "types", "--threads", "4", "--parallel-min-bytes", "0"], 4, ""),
        (["--order-by", "a:int", "--format", "csv", "--threads", "4", "--parallel-min-bytes", "0"], 4, ""),
        (["--order-by", "a:int", "--format", "csv", "--top", "2", "--threads", "4", "--parallel-min-bytes", "0"], 1, "--order-by"),
        (["--order-by", "a:int", "--format", "csv", "--limit", "2", "--threads", "4", "--parallel-min-bytes", "0"], 1, "--order-by"),
    ]

    def test_the_decision_and_its_reason(self):
        for flags, effective, word in self.CASES:
            with self.subTest(flags=flags):
                t = explain_of(self.x(*flags))["work"]["threads"]
                self.assertEqual(t["requested"], int(flags[flags.index("--threads") + 1]) if "--threads" in flags else 1)
                self.assertEqual(t["effective"], effective, t)
                if word:
                    self.assertIn(word, t["reason"])
                else:
                    self.assertIsNone(t["reason"])

    def test_standard_input_is_one_core(self):
        x = run("--explain", "--group", "a", "--threads", "4", "--parallel-min-bytes", "0", cwd=self.cwd, stdin=b"a\n1\n")
        t = explain_of(x)["work"]["threads"]
        self.assertEqual((t["requested"], t["effective"]), (4, 1))
        self.assertIn("standard input", t["reason"])
        self.assertTrue(any("--threads" in n and "standard input" in n for n in explain_of(x)["notes"]), explain_of(x)["notes"])

    def test_the_file_must_have_more_than_the_header_to_split(self):
        self.s.write("h.csv", "a,b\n")
        t = explain_of(self.x("--group", "a", "--threads", "4", "--parallel-min-bytes", "0", name="h.csv"))["work"]["threads"]
        self.assertEqual(t["effective"], 1)

    @unittest.skipUnless(LINUX_STRACE, "needs Linux and strace")
    def test_strace_the_run_starts_threads_exactly_when_explain_says_it_does(self):
        with open(self.s.dir / "t.csv", "w") as f:
            f.write("a,b,c\n")
            for i in range(30000):
                f.write("%d,%d,%d\n" % (i, i % 7, i % 3))
        for flags, effective, word in self.CASES:
            with self.subTest(flags=flags):
                want = explain_of(run("--explain", "--root", str(self.s.dir), *flags, "t.csv", cwd=self.cwd))["work"]["threads"]["effective"]
                self.assertEqual(want, effective)
                log = str(self.s.dir / "strace.log")
                p = subprocess.run(["strace", "-f", "-o", log, "-e", "trace=clone,clone3", binary(), "--root", str(self.s.dir), *flags, "t.csv"], capture_output=True, input=b"")
                self.assertEqual(p.returncode, 0, p.stdout[:300])
                clones = len([l for l in open(log).read().splitlines() if "clone" in l and "resumed" not in l and "= -1" not in l])
                self.assertEqual(clones > 0, effective > 1, (flags, clones, effective))


# ---- the document ------------------------------------------------------------------------------------------------------------------

class Document(Base):
    def test_the_example_of_the_design(self):
        self.s.write("orders.csv", "id,customer,city,bytes\n1,ann,x,150\n2,bob,y,50\n3,ann,x,300\n")
        flags = ["--where", "bytes:int > 100", "--group", "customer", "--agg", "count,sum:bytes", "--sort", "-sum:bytes", "--limit", "2", "--threads", "4"]
        x = self.x(*flags, name="orders.csv")
        self.assertEqual(x.status, 0, x.stdout)
        self.assertEqual(validate(x), [], x.stdout)
        e = explain_of(x)
        self.assertEqual(list(e), ["source", "plan", "output", "work", "refusals", "notes"])
        self.assertEqual(e["source"], {"kind": "file", "path": "orders.csv", "reader": "file", "block_bytes": 65536, "format": "csv", "delimiter": ",", "size": 58, "header_read": True,
                                       "columns": 4, "bytes_read": 58})
        p = e["plan"]
        self.assertEqual(p["form"], "groups")
        self.assertEqual(p["select"], [])
        self.assertEqual(p["where"], [{"column": {"ref": "bytes", "position": 4, "name": "bytes"}, "type": "int", "op": ">", "values": ["100"]}])
        self.assertEqual(p["group"], [{"column": {"ref": "customer", "position": 2, "name": "customer"}, "type": "text"}])
        self.assertEqual([(a["function"], a["column"] and a["column"]["ref"], a["type"]) for a in p["aggregates"]], [("count", None, None), ("sum", "bytes", "int")])
        self.assertEqual((p["sort"], p["top"], p["limit"], p["from"]), ("-sum:bytes", None, 2, 0))
        self.assertEqual(e["output"], {"format": "json", "columns": ["customer", "count", "sum:bytes"]})
        w = e["work"]
        self.assertEqual(w["class"], "grouped single pass")
        self.assertEqual(w["reads"], "all rows")
        self.assertEqual(w["bounds"], {"max_rows": 10000000, "max_line_bytes": 1048576, "max_bytes": 1048576, "max_groups": 100000, "max_state_bytes": 67108864})
        self.assertEqual(w["threads"]["requested"], 4)
        self.assertEqual(w["threads"]["effective"], 1)
        r = self.r(*flags, name="orders.csv")
        self.assertEqual(r.data()["columns"], e["output"]["columns"])
        for rule in ("value.not-integer", "limit.too-many-groups", "parse.csv-ragged-row"):
            self.assertIn(rule, e["refusals"]["possible"])
        self.assertNotIn("limit.too-many-sort-rows", e["refusals"]["possible"])

    def test_the_forms(self):
        forms = {"shape": [], "rows": ["--select", "a"], "groups": ["--group", "b"], "report": ["--report", "types"]}
        for form, flags in forms.items():
            with self.subTest(form=form):
                e = explain_of(self.x(*flags))
                self.assertEqual(e["plan"]["form"], form)
        self.assertEqual(explain_of(self.x("--rows"))["plan"]["select"], [])
        self.assertEqual(explain_of(self.x("--agg", "sum:a"))["plan"]["form"], "groups")
        c = explain_of(self.x())["output"]["columns"]
        self.assertEqual(c, ["a", "b", "c"])

    def test_classes_and_reads(self):
        cls = lambda *f: explain_of(self.x(*f))["work"]
        self.assertEqual(cls()["class"], "shape")
        self.assertEqual(cls()["reads"], "all rows")
        self.assertEqual(cls("--select", "a")["class"], "stream rows")
        self.assertEqual(cls("--select", "a")["reads"], "until the page is full")
        self.assertEqual(cls("--select", "a", "--format", "csv")["reads"], "all rows")
        self.assertEqual(cls("--select", "a", "--format", "csv", "--limit", "2")["reads"], "until the page is full")
        self.assertEqual(cls("--order-by", "a:int")["class"], "sorted rows")
        self.assertEqual(cls("--report", "types")["class"], "type report")
        self.assertEqual(cls("--group", "a")["class"], "grouped single pass")
        self.assertEqual(cls("--group", "a", "--agg", "distinct:b")["bounds"]["max_distinct"], 100000)
        sorted_ = cls("--order-by", "a:int", "--limit", "10", "--from", "5")
        self.assertEqual(sorted_["rows_held"], 16)
        self.assertEqual(cls("--order-by", "a:int", "--top", "3")["rows_held"], 3)
        self.assertIsNone(cls("--order-by", "a:int", "--format", "csv")["rows_held"])
        self.assertEqual(cls("--order-by", "a:int")["bounds"]["max_sort_rows"], 1000000)

    def test_types_are_what_the_plan_reads(self):
        e = explain_of(self.x("--where", "a:dec(2) > 1 and b = x and c:float < 3", "--group", "a:dec(2)", "--agg", "sum:a:dec(2),mean:c@3,min:c:float,distinct:b,mean:a@2"))
        p = e["plan"]
        self.assertEqual([(w["column"]["ref"], w["type"], w["op"]) for w in p["where"]], [("a", "dec(2)", ">"), ("b", "text", "="), ("c", "float", "<")])
        self.assertEqual(p["group"][0]["type"], "dec(2)")
        got = [(a["function"], a["column"] and a["column"]["ref"], a["type"], a.get("scale")) for a in p["aggregates"]]
        self.assertIn(("mean", "c", "int", 3), got)

    def test_columns_by_position_and_by_awkward_name(self):
        self.s.write("p.csv", '#3,x y,"a,b"\n1,2,3\n')
        e = explain_of(self.x("--select", "#2,\\#3,a\\,b", name="p.csv"))
        sel = e["plan"]["select"]
        self.assertEqual(sel, [{"ref": "#2", "position": 2, "name": "x y"}, {"ref": "\\#3", "position": 1, "name": "#3"}, {"ref": "a,b", "position": 3, "name": "a,b"}])
        self.assertEqual(e["output"]["columns"], ["x y", "#3", "a,b"])

    def test_a_name_that_is_not_utf8_is_text_or_bytes(self):
        self.s.write("n.csv", b"a,\xff\xfe\n1,2\n")
        x = self.x("--select", "#2", name="n.csv")
        self.assertEqual(x.status, 0)
        self.assertEqual(validate(x), [], x.stdout)
        self.assertEqual(explain_of(x)["output"]["columns"], [{"b64": "//4="}])

    def test_notes(self):
        notes = lambda *f: explain_of(self.x(*f))["notes"]
        self.assertTrue(any("compared as text" in n for n in notes("--where", "a > 100")), notes("--where", "a > 100"))
        self.assertEqual([n for n in notes("--where", "a:int > 100") if "as text" in n], [])
        self.assertEqual([n for n in notes("--where", "b > x") if "as text" in n], [])
        self.assertEqual([n for n in notes("--where", "a = 100") if "as text" in n], [])
        self.assertTrue(any("holds all" in n and "--order-by" in n for n in notes("--order-by", "a:int", "--format", "csv")))
        self.assertEqual([n for n in notes("--order-by", "a:int", "--top", "5") if "holds all" in n], [])
        self.assertEqual([n for n in notes("--order-by", "a:int") if "holds all" in n], [])

    def test_the_flag_is_in_the_surface(self):
        flags = {f["name"]: f for f in introspect()["flags"]}
        self.assertIn("explain", flags)
        self.assertEqual(flags["explain"]["kind"], "bool")

    def test_it_is_a_report_and_exits_zero(self):
        x = self.x("--select", "a")
        self.assertEqual(x.status, 0)
        self.assertEqual(x.stderr, b"")
        self.assertTrue(x.doc()["ok"])

    def test_a_query_is_explained_without_its_text(self):
        x = self.x("--query", "select b, count(*) group by b order by count(*) desc limit 2")
        self.assertEqual(x.status, 0, x.stdout)
        p = explain_of(x)["plan"]
        self.assertEqual((p["form"], p["sort"], p["limit"]), ("groups", "-count", 2))
        self.assertNotIn(b"select b", x.stdout)
        y = self.x("--group", "b", "--sort", "-count", "--limit", "2")
        self.assertEqual(x.stdout, y.stdout)


# ---- X4: fuzz ---------------------------------------------------------------------------------------------------------------------

FLAG_POOL = [["--select", v] for v in ("a", "a,b", "#1", "nosuch", "", "a,", "\\,", "#0", "#99999999999")] + [["--where", v] for v in (
    "a=1", "a:int>1", "a:dec(2)>1.5", "b contains x", "a in (1,2)", "a:int in (1,x)", "zz=1", "a", "=", "a:float<1e999", "a:dec(19)>1", "a=1 or b=2", "a!=", "b='x", "a:int>9223372036854775808")] + [
    ["--group", v] for v in ("a", "b", "a,b", "a:int", "a:dec(2)", "nosuch", "#2", "a,a")] + [["--agg", v] for v in (
    "count", "sum:a", "sum:b", "mean:a@2", "mean:a", "min:a:dec(2)", "distinct:b", "bogus", "sum:", "max:#3", "count,count", "sum:a:float")] + [["--sort", v] for v in ("-count", "count", "-sum:a", "zz", "-")] + [
    ["--order-by", v] for v in ("a", "-a:int", "b,a:int", "a:float", "zz", "")] + [["--top", v] for v in ("1", "0", "x")] + [["--limit", v] for v in ("1", "0", "5")] + [
    ["--from", "1"], ["--rows"], ["--report", "types"], ["--format", "csv"], ["--format", "text"], ["--threads", "4", "--parallel-min-bytes", "0"], ["--max-rows", "2"], ["--max-groups", "1"],
    ["--delimiter", "tab"], ["--max-line-bytes", "3"], ["--root", "."], ["--explain"], ["--query", "select a"], ["--query", "select b, count(*) group by b"]]


class Fuzz(Base):
    def check(self, label, run_argv, explain_argv):
        r = run(*run_argv, cwd=self.cwd)
        x = run(*explain_argv, cwd=self.cwd)
        for res in (r, x):
            self.assertNotIn(res.status, TRAPS, (label, res.stdout[:200]))
            self.assertGreaterEqual(res.status, 0)
        rule = rule_of(r)
        if x.status == 0:
            lines = x.stdout.decode("utf-8").splitlines()
            self.assertEqual(len(lines), 1, label)
            self.assertEqual(list(json.loads(lines[0])["data"]), ["explain"], label)
            self.assertTrue(rule is None or DATA.match(rule), (label, "explained, but the run refuses before the data", rule))
            if rule is not None:
                self.assertIn(rule, json.loads(lines[0])["data"]["explain"]["refusals"]["possible"], label)
            return "explained"
        self.assertFalse(rule is None or DATA.match(rule), (label, "refused, but the run does not", x.stdout[:300], r.stdout[:200]))
        self.assertEqual((x.status, x.stdout, x.stderr), (r.status, r.stdout, r.stderr), label)
        return "refused"

    def test_queries(self):
        rnd = random.Random(SEED + 2)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        cases = []
        names, kinds = ["a", "b", "c"], ["int", "text", "dec"]
        for i in range(FUZZ):
            k = i % 6
            if k < 2:
                cases.append(mutate(rnd, rnd.choice(SEEDS)))
            elif k == 2:
                cases.append(soup(rnd))
            elif k == 3:
                cases.append(raw(rnd))
            else:
                plan = query_gen.make_plan(rnd, names, kinds, [["1", "x", "1.5"]])
                text = query_gen.query_form(rnd, plan, "'f.csv'" if rnd.random() < 0.5 else None)
                cases.append(text.encode() if k == 4 else mutate(rnd, text))
        self.s.write("f.csv", "a,b,c\n1,x,1.5\n2,y,2.5\n")

        def one(b):
            r = subprocess.run([binary(), "--query", b, "f.csv"], capture_output=True, cwd=self.cwd, input=b"", timeout=60)
            x = subprocess.run([binary(), "--explain", "--query", b, "f.csv"], capture_output=True, cwd=self.cwd, input=b"", timeout=60)
            return b, r, x

        counts = {"explained": 0, "refused": 0}
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            for b, r, x in pool.map(one, cases):
                with self.subTest(query=b):
                    for p in (r, x):
                        self.assertNotIn(p.returncode, TRAPS)
                        self.assertEqual(p.stderr, b"")
                        self.assertEqual(len(p.stdout.decode("utf-8", "replace").splitlines()), 1, (b, p.stdout[:200]))
                    rd, xd = json.loads(r.stdout), json.loads(x.stdout)
                    rule = None if rd.get("ok") else rd["error"]["rule"]
                    if xd.get("ok"):
                        counts["explained"] += 1
                        self.assertTrue(rule is None or DATA.match(rule), (b, rule))
                        self.assertEqual(list(xd["data"]), ["explain"])
                    else:
                        counts["refused"] += 1
                        self.assertIn(xd["error"]["rule"], rules)
                        self.assertEqual((x.returncode, x.stdout), (r.returncode, r.stdout), b)
        if os.environ.get("TABLE_EXPLAIN_REPORT"):
            print("\nX4 queries: %d strings, %s" % (FUZZ, json.dumps(counts, sort_keys=True)))
        self.assertGreater(counts["explained"], FUZZ // 12, counts)
        self.assertGreater(counts["refused"], FUZZ // 10, counts)

    def test_flag_soups(self):
        rnd = random.Random(SEED + 3)
        self.s.write("f.csv", "a,b,c\n1,x,1.5\n2,y,2.5\n")
        counts = {"explained": 0, "refused": 0}
        for i in range(max(200, FUZZ // 2)):
            flags = []
            for _ in range(rnd.randint(1, 6)):
                flags += rnd.choice(FLAG_POOL)
            argv_r = [f for f in flags if f != "--explain"] + ["f.csv"]
            argv_x = ["--explain"] + [f for f in flags if f != "--explain"] + ["f.csv"]
            counts[self.check(flags, argv_r, argv_x)] += 1
        if os.environ.get("TABLE_EXPLAIN_REPORT"):
            print("\nX4 flags: %s" % json.dumps(counts, sort_keys=True))
        self.assertGreater(counts["explained"], 20, counts)
        self.assertGreater(counts["refused"], 20, counts)


if __name__ == "__main__":
    unittest.main()
