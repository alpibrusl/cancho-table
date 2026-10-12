"""R1 (docs/readers.md section 3, stage R1): standard input, `table -` and `table` with no operand.

The gate is a differential. The same bytes through a file and through standard input give the same answer, byte for byte, on standard output and on
standard error and in the exit status, for every plan and every refusal, but for the one place the answer names the input (`detail.path` is the file's
path; for standard input it is `-`). Every way the bytes can arrive is run: all at once, one byte at a time, and cut inside a quoted field, inside a
doubled quote and between the CR and the LF of a line end at the 64 KiB edge of the reader's chunk. Then the rules around it: what is an operand and
what is not, `--threads` on a pipe (the same bytes on one core), a producer that never ends (bounded by `--max-rows` and by the page), memory flat in the
size of the input, no trap on anything that can be put on a descriptor, and the authority row (`io_read`, and exactly that row).

`TABLE_STDIN_DIFF=1 python3 -m unittest discover` re-runs every other test of the suite through standard input too (harness.run): the same bytes as the file
for every case of select, filter, sort, numbers, float, plan, limits and rules, refusals included.

Each of these is able to fail: scripts/stdin_mutants.py breaks the source in the ways below and every one must be noticed.
"""

import csv
import errno
import io
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest

from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate

CHUNK = 65536


def spawn(argv, data, step=None, pause=0.0, cut=None, timeout=120, preexec=None, stdin=None):
    """Run `argv` with `data` on its standard input, delivered through a pipe: all at once, `step` bytes at a time with `pause` seconds between the
    pieces, or cut at the offsets in `cut` with `pause` between the pieces. Answers (status, stdout, stderr)."""
    p = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE if stdin is None else stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=preexec)
    if stdin is not None:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    if step is None and cut is None:
        out, err = p.communicate(data, timeout=timeout)
        return p.returncode, out, err
    pieces = []
    if cut is not None:
        last = 0
        for k in sorted(cut) + [len(data)]:
            pieces.append(data[last:k])
            last = k
    else:
        pieces = [data[i:i + step] for i in range(0, len(data), step)]
    holder = {}

    def collect():
        holder["out"], holder["err"] = p.stdout.read(), p.stderr.read()

    t = threading.Thread(target=collect)
    t.start()
    try:
        for piece in pieces:
            if piece:
                p.stdin.write(piece)
                p.stdin.flush()
                time.sleep(pause)
    except BrokenPipeError:
        pass
    try:
        p.stdin.close()
    except BrokenPipeError:
        pass
    p.wait(timeout=timeout)
    t.join()
    return p.returncode, holder["out"], holder["err"]


def named(data, operand=True):
    """The answer with the path of the one file these tests use (`t.csv`) where it says the path, and where a repair repeats the invocation: standard
    input says `-` (a repair that was given `-` ends with it; one that was given nothing ends without an operand)."""
    data = data.replace(b'"path":"t.csv"', b'"path":"-"')
    return data.replace(b',"t.csv"]', b',"-"]' if operand else b"]")


class Source(unittest.TestCase):
    """A scratch directory with the file `t.csv`, and the three ways to give the same bytes."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def file(self, data, plan):
        self.s.write("t.csv", data)
        p = subprocess.run([binary(), *[str(a) for a in plan], "t.csv"], capture_output=True, input=b"", cwd=str(self.s.dir))
        return p.returncode, p.stdout, p.stderr

    def stdin(self, data, plan, operand=True, **how):
        argv = [binary(), *[str(a) for a in plan]] + (["-"] if operand else [])
        return spawn(argv, data, **how)

    def same(self, data, plan, label="", forms=("pipe", "bare", "redirect")):
        raw = self.file(data, plan)
        want = (raw[0], named(raw[1]), named(raw[2]))
        bare = (raw[0], named(raw[1], False), named(raw[2], False))
        shown = "%s plan %r data %r" % (label, plan, data[:200])
        if "pipe" in forms:
            self.assertEqual(self.stdin(data, plan), want, "through a pipe, named -: " + shown)
        if "bare" in forms:
            self.assertEqual(self.stdin(data, plan, operand=False), bare, "through a pipe, no operand: " + shown)
        if "redirect" in forms:
            with open(self.s.dir / "t.csv", "rb") as f:
                self.assertEqual(self.stdin(data, plan, stdin=f), want, "redirected from a file: " + shown)
        return want


PLANS = [
    [],                                                                    # the shape
    ["--format", "text"],
    ["--select", "a,c"],
    ["--select", "#2,a", "--format", "csv"],
    ["--where", "b = x or"],                                              # a refusal of the plan itself
    ["--where", "a:int >= 2", "--select", "a,b", "--format", "csv"],
    ["--where", "b contains q", "--select", "c"],
    ["--group", "b", "--agg", "count,sum:a,max:a", "--sort", "-count"],
    ["--agg", "count,distinct:b", "--format", "csv"],
    ["--order-by", "-a:int", "--select", "a,b", "--format", "csv"],
    ["--order-by", "b,a:int", "--select", "a", "--limit", "2", "--from", "1"],
    ["--report", "types"],
    ["--select", "a", "--limit", "1", "--from", "1"],
    ["--select", "a", "--max-rows", "2"],
    ["--select", "a", "--max-rows", "2", "--format", "csv"],
    ["--select", "a", "--max-line-bytes", "20"],
    ["--delimiter", "tab"],
]

TEXTS = {
    "plain": "a,b,c\n1,x,2\n3,y,4\n5,x,6\n",
    "crlf": "a,b,c\r\n1,x,2\r\n3,y,4\r\n5,x,6\r\n",
    "no final newline": "a,b,c\n1,x,2\n3,y,4",
    "quoted newline": 'a,b,c\n1,"x\ny",2\n3,"q\r\nq",4\n',
    "doubled quotes": 'a,b,c\n1,"he said ""q""",2\n3,"""",4\n',
    "bom": "﻿a,b,c\n1,x,2\n",
    "blank lines": "a,b,c\n\n1,x,2\n\r\n3,y,4\n\n",
    "empty": "",
    "header only": "a,b,c\n",
    "just a newline": "\n",
    "ragged": "a,b,c\n1,x\n3,y,4\n",
    "bad quote": 'a,b,c\n1,"x"y,2\n',
    "unterminated quote": 'a,b,c\n1,"x\n3,y,4\n',
    "long line": "a,b,c\n1," + "z" * 300 + ",2\n",
    "not an integer": "a,b,c\n1,x,2\nq,y,4\n",
    "nul": "a,b,c\n1,x\0,2\n",
    "tab": "a\tb\tc\n1\tx\t2\n",
    "utf8": "a,b,c\n1,éü中,2\n",
    "invalid utf8": b"a,b,c\n1,\xff\xfe,2\n",
}


class Differential(Source):
    def test_every_plan_on_every_text(self):
        for label, text in TEXTS.items():
            data = text if isinstance(text, bytes) else text.encode()
            for plan in PLANS:
                self.same(data, plan, label, forms=("pipe",))

    def test_bare_and_redirected_forms_too(self):
        for label in ("plain", "crlf", "quoted newline", "ragged", "unterminated quote", "empty"):
            for plan in PLANS[:12:2]:
                self.same(TEXTS[label].encode(), plan, label)

    def test_a_refusal_names_the_input_as_dash(self):
        got = self.stdin(b"a,b\n1\n", [])
        doc = json.loads(got[1])
        self.assertEqual((got[0], doc["error"]["rule"], doc["error"]["detail"]["path"]), (8, "parse.csv-ragged-row", "-"))
        got = self.stdin(b"a,b\n1,2\n", ["--max-line-bytes", "2"])
        doc = json.loads(got[1])
        self.assertEqual((got[0], doc["error"]["rule"], doc["error"]["detail"]["path"]), (8, "limit.line-too-long", "-"))
        # the repair is the same invocation with the one thing changed, and it still reads standard input
        self.assertEqual(doc["error"]["repair"]["argv"][-1], "-")

    def test_the_empty_input_is_the_empty_file(self):
        want = self.file(b"", [])
        self.assertEqual(want[0], 0)
        self.assertEqual(json.loads(want[1])["data"], {"headers": [], "column_count": 0, "row_count": 0, "truncated": False})
        self.assertEqual(self.stdin(b"", []), want)
        self.assertEqual(self.stdin(b"", [], operand=False), want)
        self.assertEqual(spawn([binary()], b"", stdin=subprocess.DEVNULL), want)


class OneByteAtATime(Source):
    def test_a_slow_pipe_gives_the_same_bytes(self):
        # one byte per write, a pause between them so that the reader sees one at a time
        for label in ("plain", "crlf", "quoted newline", "doubled quotes", "ragged", "unterminated quote", "bom"):
            data = TEXTS[label].encode()
            for plan in (PLANS[0], PLANS[2], PLANS[7], PLANS[9]):
                want = self.file(data, plan)
                want = (want[0], named(want[1]), named(want[2]))
                got = self.stdin(data, plan, step=1, pause=0.0004)
                self.assertEqual(got, want, "%s %r" % (label, plan))

    def test_a_pause_between_pieces_changes_nothing(self):
        data = TEXTS["quoted newline"].encode()
        want = self.file(data, PLANS[2])
        for cut in ([3], [6, 7], [14, 15, 16], [len(data) - 1]):
            got = self.stdin(data, PLANS[2], cut=cut, pause=0.05)
            self.assertEqual(got, (want[0], named(want[1]), named(want[2])), cut)


def edge_case(feature, inside, edge, rows_after=3):
    """CSV text in which `feature` (whole rows) starts `inside` bytes before the byte offset `edge`, so that the reads of the reader (bulk.ask) cut it there."""
    head = b"a,b,c\n"
    start = edge - inside
    pad = start - len(head)
    filler = b"0," + b"x" * (pad - len(b"0,,y\n")) + b",y\n"
    assert len(head) + len(filler) == start
    tail = b"".join(b"%d,t,%d\n" % (i, i) for i in range(rows_after))
    return head + filler + feature + tail


FEATURES = {
    "CRLF line end": b"1,2,3\r\n",
    "doubled quote": b'1,"a""b",3\n',
    "quoted CRLF": b'1,"line1\r\nline2",3\n',
    "closing quote": b'1,"x",3\n',
    "quote then newline": b'1,"x\n",3\n',
    "unterminated at the end": b'1,"x\n',
    "bad quote": b'1,"x"y,3\n',
}


class ChunkEdge(Source):
    def test_every_cut_of_every_feature_at_every_edge(self):
        # the reads of a pipe are 512, 512, 1,024, 2,048 ... 65,536 bytes and then 65,536 each (bulk.ask): the edges are the sums of those
        for name, feature in FEATURES.items():
            for edge in (512, 1024, 4096, 32768, CHUNK, 2 * CHUNK, 3 * CHUNK):
                for inside in range(1, len(feature)):
                    data = edge_case(feature, inside, edge)
                    for plan in (PLANS[0], PLANS[3], PLANS[7]):
                        self.same(data, plan, "%s cut %d bytes in at %d" % (name, inside, edge), forms=("pipe",))

    def test_the_cut_is_what_a_reader_sees(self):
        # sanity of the construction: the file has the quoted field where the test says, and Python's csv agrees on the rows
        data = edge_case(FEATURES["quoted CRLF"], 8, CHUNK)
        self.assertEqual(data[CHUNK - 8:CHUNK + 4], b'1,"line1\r\nline2'[:12])
        rows = list(csv.reader(io.StringIO(data.decode(), newline="")))
        self.assertEqual(len(rows) - 1, int(json.loads(self.file(data, [])[1])["data"]["row_count"]))

    def test_pipe_pieces_cut_at_the_edges_too(self):
        # the pipe delivers in pieces that end exactly at, one before and one after the chunk edge, with a pause so that each is a separate read
        for name in ("CRLF line end", "doubled quote", "quoted CRLF"):
            feature = FEATURES[name]
            data = edge_case(feature, 3, CHUNK)
            want = self.file(data, PLANS[3])
            for cut in ([CHUNK - 1], [CHUNK], [CHUNK + 1], [CHUNK - 1, CHUNK, CHUNK + 1]):
                got = self.stdin(data, PLANS[3], cut=cut, pause=0.03)
                self.assertEqual(got, (want[0], named(want[1]), named(want[2])), "%s %r" % (name, cut))

    def test_a_forty_thousand_line_record_and_an_unterminated_quote_at_the_end(self):
        # S7: one record that spans 40,000 lines is a record; with the quote left open at the end it is parse.csv-unterminated-quote
        lines = b"".join(b"line %d\n" % i for i in range(40000))
        closed = b'a,b\n1,"' + lines + b'"\n2,z\n'
        opened = b'a,b\n1,"' + lines
        for data, rule in ((closed, None), (opened, "parse.csv-unterminated-quote")):
            for plan in ([], ["--select", "b"], ["--group", "b"]):
                want = self.same(data, plan, "40,000 lines", forms=("pipe",))
                if rule:
                    self.assertEqual(json.loads(want[1])["error"]["rule"], rule)
        self.assertEqual(json.loads(self.file(closed, [])[1])["data"]["row_count"], 2)


class Threads(Source):
    def test_threads_on_a_pipe_are_one_core_and_the_same_bytes(self):
        rows = ["a,b,c"] + ["%d,%s,%d" % (i, "xyz"[i % 3], i * 7) for i in range(30000)]
        data = ("\n".join(rows) + "\n").encode()
        for plan in (["--select", "a,c", "--format", "csv"], ["--group", "b", "--agg", "sum:c,count"], ["--where", "b = x", "--select", "a"], ["--order-by", "-c:int", "--format", "csv"], ["--report", "types"]):
            want = self.stdin(data, plan)
            self.assertEqual(want[0], 0, plan)
            for threads in (1, 2, 3, 4, 16, 64):
                for chunk in (1, 7, 4096):
                    got = self.stdin(data, plan + ["--threads", str(threads), "--chunk-bytes", str(chunk), "--parallel-min-bytes", "0"])
                    self.assertEqual(got, want, "threads %d chunk %d %r" % (threads, chunk, plan))
            # and the file, with threads, is the pipe's answer too (the path aside)
            self.s.write("t.csv", data)
            f = subprocess.run([binary(), *plan, "--threads", "4", "--chunk-bytes", "4096", "--parallel-min-bytes", "0", "t.csv"], capture_output=True, input=b"", cwd=str(self.s.dir))
            self.assertEqual((f.returncode, named(f.stdout), named(f.stderr)), (want[0], named(want[1]), named(want[2])))

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "needs Linux and strace")
    def test_no_thread_is_started_for_a_pipe(self):
        data = b"a,b\n" + b"".join(b"%d,x\n" % i for i in range(5000))
        log = os.path.join(self.s.dir, "clone")
        p = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3", "-o", log, binary(), "--select", "a", "--format", "csv", "--threads", "8", "--chunk-bytes", "1", "--parallel-min-bytes", "0", "-"], input=data, capture_output=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual([l for l in open(log).read().splitlines() if "clone" in l], [])
        # control: the same plan on a file does start them
        self.s.write("t.csv", data)
        subprocess.run(["strace", "-f", "-e", "trace=clone,clone3", "-o", log, binary(), "--root", str(self.s.dir), "--select", "a", "--format", "csv", "--threads", "8", "--chunk-bytes", "1", "--parallel-min-bytes", "0", "t.csv"], capture_output=True, input=b"")
        self.assertTrue([l for l in open(log).read().splitlines() if "clone" in l], "the control did not start a thread: the test proves nothing")


class Operands(Source):
    def test_dash_and_nothing_are_standard_input_and_a_file_called_dash_is_dot_slash_dash(self):
        data = TEXTS["plain"].encode()
        a = spawn([binary()], data)
        b = spawn([binary(), "-"], data)
        self.assertEqual(a, b)
        self.assertEqual(a[0], 0)
        self.assertEqual(json.loads(a[1])["data"]["row_count"], 3)
        # a file called `-` is `./-`, and then it is a file: the standard input is not read and is not consulted
        self.s.write("-", b"p,q\n1,2\n")
        got = spawn([binary(), "--root", str(self.s.dir), "./-"], data)
        self.assertEqual(json.loads(got[1])["data"]["headers"], ["p", "q"])
        got = spawn([binary(), "./-"], data, preexec=lambda: os.chdir(self.s.dir))
        self.assertEqual(json.loads(got[1])["data"]["headers"], ["p", "q"])

    def test_after_the_fence_a_dash_is_standard_input_and_a_name_that_starts_with_one_is_a_file(self):
        data = TEXTS["plain"].encode()
        self.s.write("-x.csv", b"p,q\n1,2\n")
        got = spawn([binary(), "--root", str(self.s.dir), "--", "-x.csv"], data)
        self.assertEqual(json.loads(got[1])["data"]["headers"], ["p", "q"])
        got = spawn([binary(), "--", "-"], data)
        self.assertEqual(json.loads(got[1])["data"]["headers"], ["a", "b", "c"])
        got = spawn([binary(), "--root", str(self.s.dir), "-x.csv"], data)
        self.assertEqual(json.loads(got[1])["error"]["rule"], "args.unknown-flag")

    def test_two_inputs_are_refused_with_their_rule(self):
        data = TEXTS["plain"].encode()
        self.s.write("t.csv", data)
        for argv in (["-", "-"], ["-", "t.csv"], ["t.csv", "-"], ["t.csv", "t.csv"]):
            got = spawn([binary(), "--root", str(self.s.dir), *argv], data)
            doc = json.loads(got[1])
            self.assertEqual((got[0], doc["error"]["rule"]), (2, "args.too-many-operands"), argv)
            self.assertEqual(validate(type("R", (), {"status": got[0], "stdout": got[1], "stderr": got[2], "argv": ["table"]})), [])

    def test_a_file_that_is_named_is_never_confused_with_standard_input(self):
        # the file path does not read standard input at all: a bad descriptor on 0 changes nothing
        self.s.write("t.csv", TEXTS["plain"].encode())
        a = spawn([binary(), "--root", str(self.s.dir), "t.csv"], b"", preexec=lambda: os.close(0))
        b = spawn([binary(), "--root", str(self.s.dir), "t.csv"], b"different,bytes\n")
        self.assertEqual(a, b)
        self.assertEqual(json.loads(a[1])["data"]["row_count"], 3)

    def test_root_is_ignored_for_standard_input(self):
        data = TEXTS["plain"].encode()
        want = spawn([binary(), "--select", "a"], data)
        self.assertEqual(spawn([binary(), "--root", str(self.s.dir), "--select", "a", "-"], data), want)
        self.assertEqual(spawn([binary(), "--root", str(self.s.dir), "--select", "a"], data), want)

    def test_the_description_says_so(self):
        d = introspect()
        [operand] = d["operands"]
        self.assertEqual((operand["name"], operand["min"], operand["max"]), ("FILE", 0, 1))
        self.assertIn("standard input", operand["help"])
        self.assertIn("-", operand["help"])
        self.assertIn("standard input", d["summary"])
        self.assertIn("table [", d["usage"])
        skill = subprocess.run([binary(), "skill"], capture_output=True, check=True, input=b"").stdout.decode()
        self.assertIn("standard input", skill)
        self.assertNotIn("args.missing-operand", [r["rule"] for r in d["rules"]], "no operand is standard input now: the rule cannot be emitted")

    def test_the_reader_of_a_pipe_says_what_it_costs(self):
        usage = introspect()["usage"] + introspect()["summary"]
        self.assertIn("one core", usage + json.dumps(introspect()["flags"]))


class Bounded(Source):
    def test_a_producer_that_never_ends_is_bounded_by_max_rows_and_by_the_page(self):
        for plan, check in ((["--select", "a", "--max-rows", "50"], lambda d: (d["data"]["truncated"], d["data"]["row_count"]) == (True, 50)),
                            (["--select", "a", "--limit", "10"], lambda d: (d["data"]["truncated"], d["data"]["row_count"]) == (True, 10)),
                            (["--agg", "count", "--max-rows", "1000"], lambda d: d["ok"])):
            p = subprocess.Popen([binary(), *plan], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            def produce():
                try:
                    p.stdin.write(b"a,b\n")
                    block = b"".join(b"%d,x\n" % i for i in range(2000))
                    while True:
                        p.stdin.write(block)
                except (BrokenPipeError, ValueError, OSError):
                    pass

            t = threading.Thread(target=produce, daemon=True)
            t.start()
            try:
                out = p.stdout.read()
                p.wait(timeout=30)
            finally:
                p.kill()
                try:
                    p.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            self.assertEqual(p.returncode, 0, plan)
            self.assertTrue(check(json.loads(out)), (plan, out[:300]))

    def test_a_producer_with_no_newline_is_bounded_by_the_line(self):
        # `lines` answers a line that is too long at its end, and this one has none: past the largest cap (16 MiB) the read stops and names the length so far
        for cap in (["--max-line-bytes", "1000"], []):
            p = subprocess.Popen([binary(), *cap], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            def produce():
                try:
                    while True:
                        p.stdin.write(b"x" * 4096)
                except (BrokenPipeError, ValueError, OSError):
                    pass

            threading.Thread(target=produce, daemon=True).start()
            try:
                out = p.stdout.read()
                p.wait(timeout=60)
            finally:
                p.kill()
                try:
                    p.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            doc = json.loads(out)
            self.assertEqual((p.returncode, doc["error"]["rule"], doc["error"]["detail"]["path"], doc["error"]["detail"]["first_line"]), (8, "limit.line-too-long", "-", 1), cap)
            self.assertGreater(doc["error"]["detail"]["longest"], 16777216)
            self.assertLessEqual(doc["error"]["detail"]["longest"], 16777216 + 65536)

    def test_a_line_past_the_ceiling_is_the_one_difference_from_a_file(self):
        # the file's line has an end, and `longest` is its whole length; the pipe's is the length when the read stopped, the end of a 64 KiB chunk past 16 MiB.
        # (A line of 16 MiB or less is measured whole on both: the differential tests above.)
        data = b"a,b\n1," + b"z" * (20 << 20) + b"\n"
        f = self.file(data, [])
        s = self.stdin(data, [])
        fd, sd = json.loads(f[1])["error"], json.loads(s[1])["error"]
        self.assertEqual((f[0], s[0], fd["rule"], sd["rule"]), (8, 8, "limit.line-too-long", "limit.line-too-long"))
        self.assertEqual(fd["detail"]["longest"], len(b"1," + b"z" * (20 << 20)) + 1 - 1)
        self.assertEqual((sd["detail"]["longest"] + len(b"a,b\n")) % 65536, 0)
        self.assertGreater(sd["detail"]["longest"], 16777216)
        # up to the ceiling there is no difference at all
        data = b"a,b\n1," + b"z" * (15 << 20) + b"\n"
        want = self.file(data, ["--max-line-bytes", "1000"])
        got = self.stdin(data, ["--max-line-bytes", "1000"])
        self.assertEqual(got, (want[0], named(want[1]), named(want[2])))

    def test_memory_does_not_grow_with_the_input(self):
        from test_memory import make, helper
        make(self.s.dir / "small.csv", 60_000)
        make(self.s.dir / "large.csv", 960_000)
        peaks = {}
        for name in ("small", "large"):
            with open(self.s.dir / (name + ".csv"), "rb") as f:
                p = subprocess.run([helper(), binary(), "--select", "id,note", "--format", "csv"], stdin=f, capture_output=True, text=True, check=True)
            rss, status = p.stdout.split()
            peaks[name] = int(rss) * (1 if sys.platform == "darwin" else 1024)
            self.assertEqual(int(status), 0)
        print("\nstdin peak RSS: %d KB on 2 MB, %d KB on 37 MB" % (peaks["small"] // 1024, peaks["large"] // 1024), file=sys.stderr)
        self.assertLess(peaks["large"] - peaks["small"], 1 << 20)
        self.assertLess(peaks["large"], 8 << 20)


class NoTrap(Source):
    def test_nothing_that_can_be_on_descriptor_zero_reaches_a_trap(self):
        d = introspect()
        declared = {c["code"] for c in d["exit_codes"]}
        self.s.write("somefile", b"a,b\n1,2\n")
        os.makedirs(self.s.dir / "adir", exist_ok=True)
        cases = {
            "closed": lambda: os.close(0),
            "directory": lambda: (os.dup2(os.open(str(self.s.dir / "adir"), os.O_RDONLY), 0)),
            "/dev/null": lambda: os.dup2(os.open("/dev/null", os.O_RDONLY), 0),
            "/dev/zero with a bound": lambda: os.dup2(os.open("/dev/zero", os.O_RDONLY), 0),
            "a file": lambda: os.dup2(os.open(str(self.s.dir / "somefile"), os.O_RDONLY), 0),
            "write only": lambda: os.dup2(os.open(str(self.s.dir / "wo"), os.O_WRONLY | os.O_CREAT), 0),
        }
        for label, pre in cases.items():
            for plan in ([], ["--max-line-bytes", "5000"], ["--select", "a", "--max-rows", "3"]):
                if label == "/dev/zero with a bound":
                    plan = plan + ["--max-line-bytes", "100000"]
                p = subprocess.run([binary(), *plan], stdin=None, preexec_fn=pre, capture_output=True, timeout=60)
                self.assertNotIn(p.returncode, TRAPS, (label, plan))
                self.assertIn(p.returncode, declared, (label, plan, p.stdout[:200]))

    def test_an_empty_file_that_cannot_be_opened_is_a_refusal_and_not_a_trap(self):
        # The empty file `read_file` is typed with (/dev/null) is opened before standard input is read. Where it cannot be, the input is refused with a rule that names it
        # as `-`, as a file that cannot be opened is. (Made to fail with the system's own means: the sandbox on macOS, strace's error injection on Linux.)
        declared = {c["code"] for c in introspect()["exit_codes"]}
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            wrap = ["sandbox-exec", "-p", '(version 1)(allow default)(deny file-read* (literal "/dev/null"))']
        elif sys.platform.startswith("linux") and shutil.which("strace"):
            wrap = ["strace", "-f", "-o", "/dev/null", "-P", "/dev/null", "-e", "trace=openat", "-e", "inject=openat:error=EACCES"]
        else:
            self.skipTest("needs sandbox-exec (macOS) or strace (Linux)")
        for argv in ([binary()], [binary(), "-"], [binary(), "--select", "a", "--format", "csv"]):
            p = subprocess.run([*wrap, *argv], input=b"a\n1\n", capture_output=True, timeout=60)
            self.assertNotIn(p.returncode, TRAPS, argv)
            self.assertIn(p.returncode, declared, argv)
            self.assertNotEqual(p.returncode, 0, (argv, p.stdout[:200], p.stderr[:200]))
            if "--format" in argv:
                self.assertIn(b"table: io.permission-denied", p.stderr)
            else:
                doc = json.loads(p.stdout)
                self.assertEqual((doc["ok"], doc["error"]["rule"], doc["error"]["detail"]["path"]), (False, "io.permission-denied", "-"))
        # control: the same wrapper does not stop a file, which does not open /dev/null
        self.s.write("t.csv", b"a\n1\n")
        p = subprocess.run([*wrap, binary(), "--root", str(self.s.dir), "t.csv"], capture_output=True, input=b"", timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout[:300] + p.stderr[:300])

    def test_random_bytes(self):
        rng = random.Random(20261012)
        alphabet = [b"a", b"b", b",", b'"', b'"', b"\n", b"\r", b"\0", b"1", b"-", b"\xc3\xa9", b"\xff", b" ", b"\t", b'""', b"\r\n", b",,"]
        plans = [[], ["--select", "#1"], ["--group", "#1", "--agg", "count"], ["--where", "#1 = a", "--select", "#1"], ["--report", "types"], ["--order-by", "#1", "--format", "csv"], ["--agg", "sum:#1:int"]]
        declared = {c["code"] for c in introspect()["exit_codes"]}
        for n in range(150):
            data = b"".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 200)))
            if n % 3 == 0:
                data = b"a,b\n" + data
            for plan in plans:
                want = self.file(data, plan)
                got = self.stdin(data, plan)
                self.assertNotIn(got[0], TRAPS, (data, plan))
                self.assertIn(got[0], declared, (data, plan))
                self.assertEqual((got[0], got[1], got[2]), (want[0], named(want[1]), named(want[2])), (data, plan))
                if "--format" not in plan:
                    self.assertEqual(validate(type("R", (), {"status": got[0], "stdout": got[1], "stderr": got[2], "argv": ["table"]})), [], (data, plan))


class Authority(unittest.TestCase):
    MAIN = {"args", "conc", "dir_read", "err_write", "file_read", "fs_read", "heap", "io_write"}

    def test_the_row_grew_by_io_read_and_nothing_else(self):
        got = {l["name"] for l in introspect()["authority"]["labels"]}
        self.assertEqual(got - self.MAIN, {"io_read"})
        self.assertEqual(self.MAIN - got, set())
        committed = json.loads((ROOT / "manifests" / "table.authority.json").read_text())
        self.assertEqual({l["name"] for l in committed["labels"]} - self.MAIN, {"io_read"})
        self.assertEqual(set(committed["effects"]) - self.MAIN, {"io_read"})
        self.assertTrue(committed["bounded"])
        ceiling = tomllib.loads((ROOT / "tools.toml").read_text())["table"]["allow"]
        self.assertEqual({c.split("(")[0] for c in ceiling} - self.MAIN, {"io_read"})

    def test_the_ceiling_refuses_the_source_without_it(self):
        # the mutant: tools.toml without `io_read`; scripts/manifest.py --check must fail and name it
        text = (ROOT / "tools.toml").read_text()
        self.assertIn('"io_read", ', text)
        d = tempfile.mkdtemp(prefix="ceiling-")
        try:
            mutant = pathlib.Path(d) / "tools.toml"
            mutant.write_text(text.replace('"io_read", ', "", 1))
            env = dict(os.environ)
            p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check", "--ceiling", str(mutant)], capture_output=True, text=True, env=env)
            self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("io_read is not within the ceiling", p.stdout + p.stderr)
            control = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check", "--ceiling", str(ROOT / "tools.toml")], capture_output=True, text=True, env=env)
            self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "needs Linux and strace")
    def test_strace_a_pipe_reads_descriptor_zero_and_a_file_does_not(self):
        d = tempfile.mkdtemp(prefix="table-")
        try:
            with open(os.path.join(d, "f.csv"), "w") as f:
                f.write('a,b\n1,"x\ny"\n')
            log = os.path.join(d, "trace")
            p = subprocess.run(["strace", "-f", "-o", log, binary()], input=b'a,b\n1,"x\ny"\n', capture_output=True)
            self.assertEqual(p.returncode, 0)
            text = open(log).read()
            self.assertRegex(text, r"read\(0, ")
            for call in ("socket(", "connect(", "bind(", "sendto(", "clock_gettime(CLOCK_REALTIME", "unlink", "rename", "mkdir", "ftruncate"):
                self.assertNotIn(call, text, call)
            for line in text.splitlines():
                if line.startswith(("openat", "open(")) or " openat(" in line:
                    self.assertNotRegex(line, r"O_(WRONLY|RDWR|CREAT)", line)
            log2 = os.path.join(d, "trace2")
            subprocess.run(["strace", "-f", "-o", log2, binary(), "--root", d, "f.csv"], input=b"unread\n", capture_output=True, check=True)
            self.assertNotRegex(open(log2).read(), r"read\(0, ")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Bulk(Source):
    """Standard input is read in bulk (docs/readers.md 11.7; cancho PR #363's `read_bytes`), not a byte at a time, and a read that fails is a refusal and not an end.

    Written before the code that makes them pass (they fail on `getchar`): a pipe costs about what a file does in instructions, the reads on descriptor 0 are few and
    large, and a closed descriptor, a directory or a descriptor that cannot be read from are refused with the rule of their errno and the input named as `-`."""

    ROWS = 150_000

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from test_memory import make
        make(cls.s.dir / "bulk.csv", cls.ROWS)
        cls.size = (cls.s.dir / "bulk.csv").stat().st_size

    @staticmethod
    def instructions(argv, stdin=None, cwd=None):
        """User-space instructions retired by one run (the count does not depend on the load), or None where this machine cannot count them: macOS `/usr/bin/time -l`,
        Linux `perf stat -e instructions:u` (when perf_event_paranoid allows it)."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import gate_regress
        if sys.platform == "darwin":
            p = subprocess.run(["/usr/bin/time", "-l", *argv], stdin=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, cwd=cwd)
            m = re.search(r"(\d+)\s+instructions retired", p.stderr)
            return int(m.group(1)) if m else None
        if shutil.which("perf"):
            p = subprocess.run(["perf", "stat", "-x,", "-e", "instructions:u", "--", *argv], stdin=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, cwd=cwd)
            return gate_regress.parse_perf(p.stderr) or None
        return None

    def test_a_pipe_costs_about_what_a_file_costs_in_instructions(self):
        plan = ["--select", "status,bytes", "--format", "csv"]
        d = str(self.s.dir)
        # a file by name and the same bytes redirected into standard input: the work on the rows is the same, the reading is what differs
        by_name = self.instructions([binary(), "--root", d, *plan, "bulk.csv"])
        with open(self.s.dir / "bulk.csv", "rb") as f:
            through_stdin = self.instructions([binary(), *plan], stdin=f)
        if by_name is None or through_stdin is None:
            self.skipTest("no instruction counter here")
        ratio = through_stdin / by_name
        print("\nstdin instructions: file %.1f M, standard input %.1f M, ratio %.3f (%d bytes)" % (by_name / 1e6, through_stdin / 1e6, ratio, self.size), file=sys.stderr)
        # a byte at a time (getchar) costs 2.2 times the file on x86-64 and 1.5 on arm64; the bound is a little over what bulk reads cost
        self.assertLess(ratio, 1.15, "standard input costs %.2f times the instructions of the same file" % ratio)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "needs Linux and strace")
    def test_strace_the_reads_of_descriptor_zero_are_few_and_large(self):
        d = tempfile.mkdtemp(prefix="table-")
        try:
            log = os.path.join(d, "trace")
            with open(self.s.dir / "bulk.csv", "rb") as f:
                p = subprocess.run(["strace", "-f", "-e", "trace=read", "-o", log, binary(), "--select", "status", "--format", "csv"], stdin=f, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr[:300])
            sizes = [int(m.group(1)) for m in re.finditer(r"read\(0, .*?, (\d+)\) = ", open(log).read())]
            self.assertTrue(sizes, "no read of descriptor 0")
            print("\nstdin reads of descriptor 0 over %d bytes: %d, largest request %d" % (self.size, len(sizes), max(sizes)), file=sys.stderr)
            # getchar asks for 4096 at a time (stdio's buffer): one read per 4 KiB of input
            self.assertLess(len(sizes), self.size // 4096 // 4, "reads of descriptor 0: %d for %d bytes" % (len(sizes), self.size))
            self.assertGreaterEqual(max(sizes), CHUNK)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_page_is_answered_soon_after_its_last_row_from_a_producer_that_trickles(self):
        # `read_bytes` waits until its buffer is full or the input ends, so a buffer of 64 KiB makes a page of three rows from a producer at 8 KB/s wait for
        # the producer to have written 64 KiB (8 seconds), or for it to end. The reads start small and double (bulk.ask), so the answer waits for about twice the
        # bytes the page needs. The producer here stops after 6 seconds: a reader that waits for 64 KiB answers when the producer ends.
        p = subprocess.Popen([binary(), "--select", "a", "--limit", "3", "--format", "csv"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = lambda i: ("%d,%s,%d\n" % (i, "x" * 30, i)).encode()
        p.stdin.write(b"a,b,c\n")
        p.stdin.flush()
        wrote = None
        t0 = time.perf_counter()
        try:
            for i in range(1200):
                p.stdin.write(line(i))
                p.stdin.flush()
                if i == 4:
                    wrote = time.perf_counter()        # the header, three rows and the one past the page are written
                time.sleep(0.005)
                if p.poll() is not None:
                    break
        except BrokenPipeError:
            pass
        answered = p.poll() is not None
        waited = time.perf_counter() - wrote
        try:
            p.stdin.close()
        except BrokenPipeError:
            pass
        out = p.stdout.read()
        p.wait()
        self.assertEqual(out, b"a\n0\n1\n2\n")
        print("\nstdin trickle: a page of 3 rows answered %.2f s after its last row was written" % waited, file=sys.stderr)
        self.assertTrue(answered, "not answered while the producer was still writing")
        self.assertLess(waited, 2.0)

    def refusal(self, pre, plan=(), keep=()):
        p = subprocess.run([binary(), *plan], stdin=None, preexec_fn=pre, capture_output=True, timeout=60, pass_fds=keep)
        self.assertNotIn(p.returncode, TRAPS)
        return p

    def test_a_descriptor_that_cannot_be_read_is_a_refusal_with_its_errno(self):
        os.makedirs(self.s.dir / "adir", exist_ok=True)
        codes = {c["code"] for c in introspect()["exit_codes"]}
        pipe_r, pipe_w = os.pipe()
        os.set_blocking(pipe_r, False)
        full_r, full_w = os.pipe()
        os.set_blocking(full_r, False)
        os.write(full_w, b"a,b\n1,2\n")
        try:
            cases = {
                "write only": (lambda: os.dup2(os.open(str(self.s.dir / "wo"), os.O_WRONLY | os.O_CREAT), 0), "io.read-failed", 9, (), False),
                "directory": (lambda: os.dup2(os.open(str(self.s.dir / "adir"), os.O_RDONLY), 0), "io.is-a-directory", 21, (), False),
                # nothing there yet on a pipe that does not wait: the read fails with EAGAIN (EWOULDBLOCK), which getchar could not tell from the end
                "a pipe that does not wait, empty": (lambda: os.dup2(pipe_r, 0), "io.read-failed", errno.EAGAIN, (pipe_r,), False),
                # the rows that arrived before the failure were read, and the failure is the answer
                "a pipe that does not wait, with a record and no end": (lambda: os.dup2(full_r, 0), "io.read-failed", errno.EAGAIN, (full_r,), True),
            }
            for label, (pre, rule, number, keep, rows_first) in cases.items():
                p = self.refusal(pre, keep=keep)
                self.assertNotEqual(p.returncode, 0, (label, p.stdout[:200]))
                self.assertIn(p.returncode, codes, label)
                doc = json.loads(p.stdout)
                self.assertEqual((doc["ok"], doc["error"]["rule"], doc["error"]["detail"]["path"], doc["error"]["detail"]["errno"]), (False, rule, "-", number), label)
                if rows_first:
                    self.assertEqual(doc["data"]["headers"], ["a", "b"], label)
                # csv: the refusal is on standard error, as a file's is
                q = self.refusal(pre, ["--select", "a", "--format", "csv"], keep=keep)
                self.assertNotEqual(q.returncode, 0, label)
                self.assertIn(b"table: " + rule.encode(), q.stderr, label)
        finally:
            for fd in (pipe_r, pipe_w, full_r, full_w):
                os.close(fd)

    def test_nothing_to_read_is_not_a_failure_to_read(self):
        for label, pre in (("/dev/null", lambda: os.dup2(os.open("/dev/null", os.O_RDONLY), 0)),
                           ("a closed descriptor", lambda: os.close(0))):
            # A closed descriptor 0 is the empty input, as before the bulk read and not as `read_bytes` alone would say (`Failed(9)`): the empty file that
            # `read_file` is typed with is opened before standard input is read, and an open takes the lowest free descriptor, which is 0. The program reads
            # /dev/null and cannot know. (docs/readers.md 11.7; the fix is upstream, a `File` for descriptor 0.)
            p = self.refusal(pre)
            self.assertEqual(p.returncode, 0, label)
            self.assertEqual(json.loads(p.stdout)["data"]["column_count"], 0, label)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "needs Linux and strace")
    def test_strace_a_closed_descriptor_is_the_stand_in_that_is_read(self):
        d = tempfile.mkdtemp(prefix="table-")
        try:
            log = os.path.join(d, "trace")
            subprocess.run(["strace", "-o", log, "-e", "trace=openat,read", binary()], stdin=None, preexec_fn=lambda: os.close(0), capture_output=True, timeout=60)
            text = open(log).read()
            self.assertRegex(text, r'openat\(AT_FDCWD, "/dev/null", O_RDONLY[^)]*\) = 0')
        finally:
            shutil.rmtree(d, ignore_errors=True)


class StreamLoop(unittest.TestCase):
    """`read_stream` is `read_file` made mechanically (scripts/stream.py): the loop of a file is not edited to serve a pipe, and the loop of a pipe is not edited by hand."""

    SCRIPT = [sys.executable, str(ROOT / "scripts" / "stream.py")]

    def test_the_committed_copy_is_what_the_file_loop_makes(self):
        got = subprocess.run([*self.SCRIPT, "--check"], capture_output=True, text=True)
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def test_an_edit_of_either_loop_alone_is_noticed(self):
        text = (ROOT / "tools" / "table" / "table.cho").read_text()
        d = tempfile.mkdtemp(prefix="stream-")
        try:
            for what, old, new in (("the copy", "a[engine.k_records()] >= most {\n                        // A record past the bound: not read, so not judged.\n                        a[engine.k_capped()] = 1;\n                        going = false;\n                    } else if header && !quoted && mode == 1 && !filtering && a[engine.k_records()] >= from",
                                    "a[engine.k_records()] > most {\n                        // A record past the bound: not read, so not judged.\n                        a[engine.k_capped()] = 1;\n                        going = false;\n                    } else if header && !quoted && mode == 1 && !filtering && a[engine.k_records()] >= from"),):
                a = text.index("// BEGIN read_stream")
                self.assertGreater(text.count(old), 1, "the pattern is in both loops")
                # edit the copy only
                edited = text[:a] + text[a:].replace(old, new, 1)
                path = os.path.join(d, "table.cho")
                open(path, "w").write(edited)
                got = subprocess.run([*self.SCRIPT, "--check", "--file", path], capture_output=True, text=True)
                self.assertEqual(got.returncode, 1, what + ": " + got.stdout)
                # edit the file loop only (the first occurrence): the copy is no longer what it makes
                edited = text.replace(old, new, 1)
                open(path, "w").write(edited)
                got = subprocess.run([*self.SCRIPT, "--check", "--file", path], capture_output=True, text=True)
                self.assertEqual(got.returncode, 1, "the file loop: " + got.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_copy_has_no_partitioned_read_and_reads_standard_input(self):
        text = (ROOT / "tools" / "table" / "table.cho").read_text()
        a = text.index("// BEGIN read_stream")
        region = text[a:text.index("// END read_stream")]
        self.assertNotIn("par.run", region)
        self.assertNotIn("file_size", region)
        self.assertIn("bulk.fill_stdin", region)
        self.assertNotIn("bulk.fill_stdin", text[:a])
        self.assertIn("par.run", text[:a])


if __name__ == "__main__":
    unittest.main()
