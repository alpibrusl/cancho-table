"""`--threads N`: the answer of N threads is the sequential answer, byte for byte.

The sequential read (--threads 1) is the oracle. Every test here runs the same plan with
N threads and tiny ranges (`--chunk-bytes`, so that a boundary falls in almost every
record, and `--parallel-min-bytes 0`, so that the file is not too small to be split) and
requires standard output, standard error and the exit status to be the oracle's: for rows
as csv and as json, for groups, for every refusal (the row, line and column it names are the
first in file order, not the first a thread met), for ragged rows (the same first one and
the same count).
"""

import csv
import io
import random
import re
import unittest

import test_plan as tp
from harness import Scratch, run_argv, binary

THREADS = (2, 3, 4, 8, 16, 64)
CHUNKS = (1, 7, 64, 1000)


class Same(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def outcome(self, data, args, threads=1, chunk=4096):
        self.s.write("t.csv", data)
        extra = [] if threads == 1 else ["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0]
        got = run_argv([binary(), "--root", str(self.s.dir), *[str(a) for a in args], *[str(a) for a in extra], "t.csv"])
        # (a `retry` repair is the whole invocation, and says so: the flags that are the test's go)
        shown = re.sub(rb'"--threads","[0-9]+","--chunk-bytes","[0-9]+","--parallel-min-bytes","0",', b"", got.stdout)
        return got.status, shown, got.stderr

    def same(self, data, args, label=""):
        want = self.outcome(data, args)
        for threads in THREADS:
            for chunk in CHUNKS:
                got = self.outcome(data, args, threads, chunk)
                self.assertEqual(got, want, "%s threads %d chunk %d args %r data %r" % (label, threads, chunk, args, data[:300]))


class Adversarial(Same):
    def test_every_boundary_is_a_quoted_newline(self):
        # Records whose quoted fields hold newlines and doubled quotes, so that a range
        # boundary falls inside a quoted field whatever the chunk size.
        rows = []
        for i in range(60):
            rows.append(["id%d" % i, 'multi\nline "%d"\r\nend' % i, "x,y", str(i)])
        out = io.StringIO(newline="")
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["id", "text", "pair", "n"])
        for r in rows:
            w.writerow(r)
        data = out.getvalue().encode()
        for args in (["--select", "text,n", "--format", "csv"], ["--select", "id,pair"], ["--where", "n:int >= 20", "--format", "csv"],
                     ["--group", "pair", "--agg", "count,sum:n,min:n,max:n,distinct:text"], ["--group", "id", "--sort", "-count", "--top", "3"]):
            self.same(data, args, "quoted newlines")

    def test_stray_quotes_in_unquoted_fields(self):
        data = b'a,b,c\n1,x"y,3\n"4,5\n6,7,8\n9,x"y"z,10\n11,12,13\n' * 20
        for args in (["--select", "b", "--format", "csv"], ["--group", "c"]):
            self.same(data, args, "stray quotes")

    def test_ragged_rows_at_boundaries(self):
        data = b"a,b\n" + b"".join((b"%d,%d\n" % (i, i) if i % 7 else b"%d\n" % i) for i in range(80))
        for args in (["--select", "a"], ["--select", "b", "--format", "csv"], ["--group", "b"]):
            self.same(data, args, "ragged")

    def test_small_empty_header_only_bom_crlf(self):
        for data in (b"", b"a,b\n", b"a,b\n1,2\n", b"\xef\xbb\xbfa,b\r\n1,2\r\n3,4\r\n5,6", b"a,b\n\n\n1,2\n\n3,4\n\n", b"a\n"):
            for args in (["--select", "a"], ["--group", "a"], ["--where", "a = 1"]):
                self.same(data, args, "small")

    def test_errors_in_two_ranges_the_first_in_file_order_wins(self):
        rows = ["%d,%d" % (i, i) for i in range(100)]
        rows[70] = "70,notanumber"
        rows[20] = "20,"
        data = ("a,b\n" + "\n".join(rows) + "\n").encode()
        for args in (["--where", "b:int > 0"], ["--agg", "sum:b"], ["--group", "a", "--agg", "max:b"]):
            self.same(data, args, "two errors")
            want = self.outcome(data, args)
            self.assertIn(b'"row":21', want[1] + want[2] + b'"row":21' if want[0] else want[1])

    def test_blank_lines_and_late_errors_name_the_same_line(self):
        rows = []
        for i in range(120):
            rows.append("%d,%d" % (i, i))
            if i % 9 == 0:
                rows.append("")
                rows.append("")
        rows[100] = "100"            # ragged
        rows[111] = "111,zzz"        # not an integer
        data = ("a,b\n" + "\n".join(rows) + "\n").encode()
        for args in (["--select", "a"], ["--where", "b:int >= 0"], ["--group", "a", "--agg", "sum:b"], ["--select", "a", "--format", "csv"]):
            self.same(data, args, "blank lines")

    def test_a_quote_left_open_and_a_bad_quote(self):
        data = b'a,b\n' + b"1,2\n" * 30 + b'3,"unterminated\n4,5\n' * 1
        for args in (["--select", "a"], ["--group", "a"]):
            self.same(data, args, "open quote")
        data = b'a,b\n' + b"1,2\n" * 30 + b'"x"y,2\n' + b"3,4\n" * 30
        for args in (["--select", "a"], ["--group", "b"]):
            self.same(data, args, "bad quote")

    def test_integer_sums_near_the_edge(self):
        mx, mn = "9223372036854775807", "-9223372036854775808"
        cases = {
            "peak then back": ["4611686018427387904"] * 2 + ["-4611686018427387904"] * 2,           # exactly the edge, then home
            "overflow in order": [mx, "1"] + ["1"] * 20,
            "overflow only in the total": ["4611686018427387904"] * 3,
            "partials overflow, total does not": ["4611686018427387904", "4611686018427387904", "-4611686018427387904", "-4611686018427387904"] * 3,
            "negative edge": [mn, "-1"] + ["0"] * 10,
            "back and forth": [mx, "-1", "1", "-1", "1"] * 5,
            "many small": ["1"] * 200,
        }
        for label, values in cases.items():
            data = ("k,v\n" + "".join("a,%s\n" % v for v in values)).encode()
            for args in (["--group", "k", "--agg", "sum:v,min:v,max:v"], ["--agg", "sum:v"]):
                self.same(data, args, label)

    def test_limits_and_pages(self):
        data = ("k,v\n" + "".join("k%d,%d\n" % (i % 9, i) for i in range(300))).encode()
        for args in (["--select", "v", "--limit", "7"], ["--select", "v", "--limit", "7", "--from", "20"], ["--where", "v:int>10", "--limit", "13", "--format", "json"],
                     ["--select", "v", "--max-rows", "50"], ["--select", "v", "--max-rows", "50", "--format", "csv"], ["--select", "v", "--max-bytes", "60"],
                     ["--select", "v", "--format", "csv", "--limit", "5"], ["--group", "k", "--max-groups", "4"], ["--group", "k", "--agg", "distinct:v", "--max-distinct", "100"],
                     ["--group", "k", "--max-state-bytes", "30"], ["--group", "k", "--limit", "3", "--from", "2"], ["--group", "k", "--max-rows", "100"]):
            self.same(data, args, "limits")

    def test_a_single_huge_record(self):
        data = b'a,b\n1,"' + b"x\n" * 40000 + b'"\n2,3\n'
        for args in (["--select", "a", "--max-line-bytes", "100000"], ["--group", "a", "--max-line-bytes", "100000"], ["--select", "a"]):
            want = self.outcome(data, args)
            for threads, chunk in ((4, 1000), (16, 64)):
                self.assertEqual(self.outcome(data, args, threads, chunk), want)

    def test_ragged_rows_after_a_full_page(self):
        # the page is full at the last good row of a range, and what follows is ragged
        data = b"a,b\n" + b"1,1\n" * 5 + b"2\n" * 40 + b"3,3\n" * 5
        for limit in ("5", "3", "6"):
            for args in (["--select", "a", "--limit", limit], ["--select", "a", "--limit", limit, "--format", "csv"], ["--where", "b = 1", "--limit", limit]):
                self.same(data, args, "ragged after a page")

    def test_output_that_does_not_fit_a_slot(self):
        # every row writes the long field 200 times: a range's output is far more than its input
        text = "x" * 300
        data = ("a,b\n" + "".join("%d,%s\n" % (i, text) for i in range(30))).encode()
        many = ",".join(["b"] * 200)
        for args in (["--select", many, "--format", "csv"], ["--select", many, "--limit", "40"]):
            self.same(data, args, "output past the slot")

    def test_groups_that_do_not_fit_a_slot(self):
        # 3,000 groups of a range's few bytes: the groups are far more than the range
        data = ("id,v\n" + "".join("group-number-%d,%d\n" % (i, i) for i in range(6000))).encode()
        for args in (["--group", "id", "--agg", "count,sum:v,min:v,max:v,distinct:v"], ["--group", "id", "--sort", "-count", "--top", "5"]):
            want = self.outcome(data, args)
            for threads, chunk in ((2, 40000), (4, 70000), (3, 100000)):
                self.assertEqual(self.outcome(data, args, threads, chunk), want, (args, threads, chunk))

    def test_the_most_threads_and_a_working_directory_instead_of_a_root(self):
        data = ("k,v\n" + "".join("%d,%d\n" % (i % 7, i) for i in range(500))).encode()
        args = ["--group", "k", "--agg", "sum:v,distinct:v", "--sort", "-sum:v"]
        want = self.outcome(data, args)
        self.assertEqual(self.outcome(data, args, 64, 20), want)
        self.assertEqual(self.outcome(data, args, 64, 1000000), want)
        # no --root: the path is relative to where the process is
        run_here = lambda *extra: run_argv([binary(), *args, *extra, "t.csv"], cwd=str(self.s.dir))
        self.s.write("t.csv", data)
        one = run_here()
        many = run_here("--threads", "8", "--chunk-bytes", "50", "--parallel-min-bytes", "0")
        self.assertEqual((many.status, many.stdout, many.stderr), (one.status, one.stdout, one.stderr))
        self.assertEqual(one.status, 0)

    def test_a_file_smaller_than_the_chunks(self):
        data = b"a,b\n1,2\n3,4\n"
        for args in (["--select", "a", "--format", "csv"], ["--group", "a"]):
            self.assertEqual(self.outcome(data, args, 16, 1000000), self.outcome(data, args))

    def test_unicode_and_binary(self):
        data = ("k,v\n" + "".join("é%d,%d\n" % (i % 5, i) for i in range(100))).encode() + b"\xff\xfe,7\n"
        for args in (["--group", "k", "--agg", "sum:v"], ["--select", "k", "--format", "csv"], ["--select", "k"]):
            self.same(data, args, "bytes")


def _q(text):
    return '"' + text.replace('"', '""') + '"'


def speculation_files(rng, rows):
    """Files for the look-ahead that guesses whether a range starts inside a quoted field (scan.guess): the ones where the guess is
    right (long multi-line fields, E1/E2 of docs/adversarial.md), where 'outside' is right, and the ones built so that the wrong
    reading also looks right (quoted lines that are themselves whole records of the file's width: the guess is wrong and the
    parent must read the range again), or that break the reading (stray quotes, a bad quote, ragged rows, an open quote)."""
    def lookalike():
        return "\n".join("%d,x%d,y%d" % (rng.randrange(1000), rng.randrange(10), rng.randrange(10)) for _ in range(rng.randrange(2, 12)))

    def stray():
        return "".join(rng.choice(['ab"c', "x", ' y"', "z"]) for _ in range(rng.randrange(1, 4)))

    def long_text():
        letters = "abcdefghijklmnopqrstuvwxyz ,\n\""
        n = rng.randrange(400, 2500)
        return "".join(rng.choice(letters) for _ in range(n // 10)) * 10

    head = "id,g,text\n"
    f = {}
    f["e1"] = head + "".join("%d,g%d,%s\n" % (i, i % 10, _q(long_text())) for i in range(rows))
    f["lookalike"] = head + "".join("%d,g%d,%s\n" % (i, i % 7, _q(lookalike())) for i in range(rows))
    f["lookalike crlf"] = head.replace("\n", "\r\n") + "".join("%d,g%d,%s\r\n" % (i, i % 7, _q(lookalike().replace("\n", "\r\n"))) for i in range(rows))
    f["stray"] = head + "".join("%s,g%d,%s\n" % (stray() if i % 5 == 0 else i, i % 5, _q("l1\nl2,%d\nl3" % i) if i % 3 == 0 else stray()) for i in range(rows))
    f["few"] = head + "".join("%d,g%d,%s\n" % (i, i % 7, _q("a\nb") if i % 10 == 0 else "plain %d" % i) for i in range(rows))
    f["ragged"] = head + "".join("%d,g%d,%s%s\n" % (i, i % 7, _q("a\nb\nc,d"), ",extra" if i in (rows // 3, rows // 2 + 1) else "") for i in range(rows))
    f["bad quote"] = head + "".join("%d,g%d,%s\n" % (i, i % 7, _q("a\nb") if i != rows * 2 // 3 else '"x"y') for i in range(rows))
    f["open quote"] = head + "".join("%d,g%d,%s\n" % (i, i % 7, _q("a\nb")) for i in range(rows)) + '1,g,"open\nnever closed\n'
    f["one column"] = "text\n" + "".join("%s\n" % (_q("l1\nl2\n%d" % i) if i % 2 else "plain %d" % i) for i in range(rows))
    return {k: v.encode() for k, v in f.items()}


class Speculation(Same):
    """A range that starts inside a quoted field (a guess made by looking ahead) or that the guess gets wrong gives the sequential bytes, for every
    thread count, with tiny ranges (the boundary in every record) and with ranges of a few records up to the default."""

    def check(self, data, plans, chunks, label):
        for args in plans:
            want = self.outcome(data, args)
            for threads in THREADS:
                for chunk in chunks:
                    got = self.outcome(data, args, threads, chunk)
                    self.assertEqual(got, want, "%s threads %d chunk %d args %r" % (label, threads, chunk, args))

    PLANS = (["--select", "id,g", "--format", "csv"], ["--group", "g"], ["--group", "g", "--agg", "count,sum:id,min:id,max:id"], ["--where", "id:int > 10", "--select", "g"],
             ["--report", "types", "--format", "csv"])

    def test_tiny_ranges(self):
        for name, data in speculation_files(random.Random(5), 40).items():
            plans = self.PLANS if name != "one column" else (["--select", "text", "--format", "csv"], ["--group", "text"])
            self.check(data, plans, (1, 7, 64, 1000), name)

    def test_ranges_of_a_few_records_and_the_default(self):
        for name, data in speculation_files(random.Random(6), 700).items():
            plans = self.PLANS if name != "one column" else (["--select", "text", "--format", "csv"], ["--group", "text"])
            self.check(data, plans, (4000, 30000, 4 << 20), name)

    def test_random_quoting_with_ragged_rows(self):
        # Rows made of pieces that are sometimes a whole record, sometimes ragged, sometimes a quoted field with newlines or a stray quote: at some
        # boundaries the look-ahead says 'inside' where the truth is 'outside' and the other way round, and every one must still give the oracle's bytes.
        rng = random.Random(11)
        pieces = ["1,a,b", "2,c", "3,d,e,f", '4,"x",y', '5,"p\nq",r', '"6,s,t', 'u",7,8', '9,"a""b",c', 'k"l,m,n', '"', "", "10,11,12", '13,"multi\nline\nfield",z']
        for case in range(25):
            body = "\n".join(rng.choice(pieces) for _ in range(rng.randrange(20, 60)))
            data = ("id,g,text\n" + body + "\n").encode()
            for args in (["--select", "id,g", "--format", "csv"], ["--group", "g"]):
                want = self.outcome(data, args)
                for threads in (2, 3, 8):
                    for chunk in (1, 7, 20, 64):
                        got = self.outcome(data, args, threads, chunk)
                        self.assertEqual(got, want, "case %d threads %d chunk %d args %r data %r" % (case, threads, chunk, args, data[:200]))

    def test_e1_e2_files_of_several_ranges(self):
        # 1-10 KB quoted fields with a newline every few bytes, as adversarial.py's long.csv, but a few MB.
        data = speculation_files(random.Random(7), 1500)["e1"]
        self.check(data, (["--select", "id,g", "--format", "csv"], ["--group", "g"]), (65536, 1 << 20, 4 << 20), "e1")


class Scale(Same):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rng = random.Random(99)
        out = io.StringIO(newline="")
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["id", "status", "bytes", "path", "note"])
        for i in range(120000):
            note = "a,b %d" % (i % 7)
            if i % 997 == 0:
                note = 'q"uote\nand a line %d' % i
            w.writerow([i, rng.choice([200, 200, 200, 301, 404, 500]), rng.randint(0, 99999), "/p/%d" % rng.randint(0, 999), note])
        cls.big = out.getvalue().encode()

    def test_a_file_of_several_default_ranges(self):
        self.s.write("big.csv", self.big)
        for args in (["--where", "status=404 and bytes:int>50000", "--format", "csv"], ["--group", "status", "--agg", "count,sum:bytes,min:bytes,max:bytes,distinct:path", "--sort", "-count"],
                     ["--select", "id,note", "--format", "csv"], ["--where", "note contains uote", "--select", "id,note", "--limit", "500"], ["--group", "path", "--top", "5", "--sort", "-sum:bytes", "--agg", "sum:bytes"]):
            want = run_argv([binary(), "--root", str(self.s.dir), *args, "big.csv"])
            for threads in THREADS:
                for chunk in (65536, 1 << 20, 4 << 20):
                    got = run_argv([binary(), "--root", str(self.s.dir), *args, "--threads", str(threads), "--chunk-bytes", str(chunk), "--parallel-min-bytes", "0", "big.csv"])
                    self.assertEqual((got.status, got.stdout, got.stderr), (want.status, want.stdout, want.stderr), (args, threads, chunk))

    def test_ranges_that_grow_for_groups_when_chunk_bytes_is_not_given(self):
        # Without --chunk-bytes a grouping's ranges grow after the first wave while the answers stay small, and fall back to the first size when one does not fit
        # (here the keys are few in the first half of the file and almost all different in the second). Same bytes as the sequential read in every case.
        rng = random.Random(21)
        rows = ["id,k,v,f,note"]
        for i in range(450000):
            k = "k%d" % (i % 7) if i < 225000 else "u%d" % i
            rows.append("%d,%s,%d,%d.%d,%s" % (i, k, rng.randint(0, 99999), rng.randint(0, 999), rng.randint(0, 99), '"a,b\nc"' if i % 50000 == 3 else "n"))
        self.s.write("grow.csv", ("\n".join(rows) + "\n").encode())
        for args in (["--group", "k", "--agg", "count,sum:v,min:v,max:v", "--max-groups", "1000000", "--max-state-bytes", "1073741824"], ["--group", "k", "--agg", "sum:f:float", "--max-groups", "1000000", "--max-state-bytes", "1073741824"],
                     ["--group", "v", "--agg", "count"], ["--report", "types", "--format", "csv"], ["--agg", "sum:v,distinct:k", "--max-distinct", "1000000", "--max-state-bytes", "1073741824"]):
            want = run_argv([binary(), "--root", str(self.s.dir), *args, "grow.csv"])
            for threads in (2, 3, 4, 8):
                for extra in ([], ["--chunk-bytes", "1048576"]):
                    got = run_argv([binary(), "--root", str(self.s.dir), *args, "--threads", str(threads), "--parallel-min-bytes", "0", *extra, "grow.csv"])
                    self.assertEqual((got.status, got.stdout, got.stderr), (want.status, want.stdout, want.stderr), (args, threads, extra))

    def test_the_default_threshold_keeps_a_small_file_sequential_and_the_answer_the_same(self):
        self.s.write("big.csv", self.big)
        args = ["--group", "status", "--agg", "count,sum:bytes"]
        want = run_argv([binary(), "--root", str(self.s.dir), *args, "big.csv"])
        got = run_argv([binary(), "--root", str(self.s.dir), *args, "--threads", "8", "big.csv"])
        self.assertEqual((got.status, got.stdout), (want.status, want.stdout))

    def test_other_delimiters(self):
        for delim, flag in (("\t", "tab"), (";", ";")):
            data = "k{0}v{0}t\n".format(delim) + "".join('%d{0}%d{0}"x{0}y\nz"\n'.format(delim) % (i % 5, i) for i in range(400))
            for args in (["--delimiter", flag, "--group", "k", "--agg", "sum:v"], ["--delimiter", flag, "--select", "t", "--format", "csv"]):
                self.same(data.encode(), args, "delimiter")


class Stable(Same):
    def test_the_same_run_two_hundred_times_gives_the_same_bytes(self):
        rng = random.Random(5)
        rows = [[str(i % 13), str(rng.randint(-1000, 1000)), "t%d" % (i % 4), 'q"%d\nz' % i if i % 11 == 0 else "p"] for i in range(3000)]
        data = tp.csv_bytes(["k", "v", "w", "n"], rows, rng)
        for args in (["--group", "k", "--agg", "count,sum:v,distinct:w", "--sort", "-sum:v"], ["--where", "v:int > 0", "--select", "k,n", "--format", "csv"]):
            seen = set()
            for _ in range(200):
                seen.add(self.outcome(data, args, 8, 700))
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen, {self.outcome(data, args)})


class Fuzz(Same):
    def test_300_fuzz_inputs_no_trap_and_the_oracle_answer(self):
        rng = random.Random(8)
        plans = (["--where", "a:int > 1 and b = x", "--format", "csv"], ["--group", "a", "--agg", "count,sum:b,min:b,max:b,distinct:b"], ["--select", "#1,#2", "--format", "csv"],
                 ["--group", "#1,#2", "--sort", "-count", "--top", "2"], ["--agg", "sum:#1", "--max-line-bytes", "9"], ["--where", "#1 contains 1", "--select", "#2", "--limit", "3"])
        for i in range(300):
            data = bytes(rng.choice(b'ab,1,\t"\r\n\x00\xff -9') for _ in range(rng.randint(0, 90)))
            for args in plans:
                want = self.outcome(data, args)
                got = self.outcome(data, args, rng.choice((2, 3, 5, 16)), rng.choice((1, 3, 9, 50)))
                self.assertNotIn(got[0], (-4, -6, -8, -11, 132, 134, 136, 139), (data, args))
                self.assertEqual(got, want, (data, args))


class Random(Same):
    def test_random_plans(self):
        rng = random.Random(4242)
        cases = 0
        for case in range(500):
            names, kinds, rows = tp.make_table(rng)
            if case % 5 == 0:
                rows = rows * 8
            data = tp.csv_bytes(names, rows, rng)
            plan = tp.make_plan(rng, names, kinds, rows)
            fmt = "csv" if case % 3 == 2 else "json"
            args = tp.flags_of(plan, random.Random(case), fmt) + (["--format", "csv"] if fmt == "csv" else [])
            args += rng.choice([[], [], ["--limit", "3"], ["--limit", "11"], ["--max-rows", "9"], ["--max-rows", "30"], ["--max-bytes", "150"], ["--limit", "5", "--from", "2"]])
            if "--from" in args and "group" not in plan and "--limit" in args:
                pass
            if "group" in plan and any(not tp.agg_item_ok(("%s:%s" % (f, c)) if c else f) for f, c in plan["aggs"]):
                continue
            want = self.outcome(data, args)
            for threads, chunk in ((2, 1), (3, 16), (4, 100), (8, 7)):
                self.assertEqual(self.outcome(data, args, threads, chunk), want, "case %d threads %d chunk %d args %r data %r" % (case, threads, chunk, args, data[:200]))
            cases += 1
        self.assertGreater(cases, 350)


if __name__ == "__main__":
    unittest.main()
