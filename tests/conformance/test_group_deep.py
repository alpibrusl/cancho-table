"""`--group` on keys that share a very long prefix: the answer, or a tagged refusal, never a signal (issue #79).

The defect this gate is for: `table --group a --agg count --format csv one.csv` died with SIGSEGV (exit 139, no JSON, no rule tag) when two group keys shared a
prefix of about 210 KiB or more, under the usual 8 MiB stack. The default order of the groups (`agg.sort_keys`, docs/gap-groups.md) cuts a key into chunks of six
bytes and sorts again, at the next chunk, each run of groups whose chunks are equal; that second sort was a call of the function itself, one frame for every six
shared bytes, so the depth was the shared prefix divided by six and the stack ran out (measured with gdb: 34,787 frames, all `agg.sort_run`, at the fault).

What is held here:

* the rows of the issue's table that crashed, as the issue gives them (one group column, 400 KiB; the four-key shapes from 210 to 400 KiB; two group columns; `--threads 4`),
  and the rows that did not crash (so a fix cannot trade one for the other);
* the answer equals an independent reference: the groups sorted as docs/gap-groups.md defines the order (the keys compared field by field, each bytewise, a shorter
  field first) with their counts and sums, computed in Python, at every size, as csv, json and JSON lines;
* the same from standard input (named `-` and bare), from JSON lines input, with `--threads` 1, 4 and 16, with a typed key (`:int`, `:timestamp@day`) beside the long text
  one, through `--join`, and with `--sort` and `--top`;
* the sweep: shared prefixes of 1 KiB to 8 MiB (and the line ceiling, 16 MiB) in 1, 2, 3, 5, 6 and 7 group columns; the limits that still apply (`--max-line-bytes`,
  `--max-state-bytes`, `--max-bytes`) are their tagged refusals, never a signal;
* the depth does not depend on the stack: the same answer under `ulimit -s 256`;
* a fuzz of keys with shared prefixes, keys that are prefixes of one another, lengths around multiples of six, empty cells and many rows: the reference's answer, and
  the bytes of main's binary (MAIN_TABLE) when it names one.

The status of every run is judged first: a signal (132, 134, 136, 139 or a negative status) fails the test whatever else it says.
"""

import json
import os
import random
import unittest

import procs as subprocess
from harness import TRAPS, Scratch, binary, run

KIB = 1024
MIB = 1024 * KIB
LINE_MAX = 16 * MIB
MAIN_TABLE = os.environ.get("MAIN_TABLE")

TAILS = [b"a", b"b", b"ab", b"B", b"z", b" ", b"0", b"1", "é".encode(), b"zz", b"a b"]


def names(k):
    return ["g%d" % (i + 1) for i in range(k)]


def csv_cell(b):
    assert b"," not in b and b'"' not in b and b"\n" not in b and b"\r" not in b
    return b


def table_csv(k, rows):
    out = [",".join(names(k) + ["v"]).encode()]
    for key, v in rows:
        out.append(b",".join([csv_cell(c) for c in key] + [str(v).encode()]))
    return b"\n".join(out) + b"\n"


def table_jsonl(k, rows):
    nm = names(k) + ["v"]
    return b"".join(json.dumps(dict(zip(nm, [c.decode("utf-8") for c in key] + [v])), ensure_ascii=False).encode("utf-8") + b"\n" for key, v in rows)


def groups_of(rows):
    """The groups of the rows in the order the tool defines: the keys compared field by field, each bytewise, a shorter field first (a tuple of bytes compares so)."""
    acc = {}
    for key, v in rows:
        c, s = acc.get(key, (0, 0))
        acc[key] = (c + 1, s + v)
    return [(key, c, s) for key, (c, s) in sorted(acc.items())]


def expect_csv(k, rows):
    out = [",".join(names(k) + ["count", "sum:v"]).encode()]
    for key, c, s in groups_of(rows):
        out.append(b",".join(list(key) + [str(c).encode(), str(s).encode()]))
    return b"\n".join(out) + b"\n"


def expect_rows(k, rows):
    return [[c.decode("utf-8") for c in key] + [str(n), str(s)] for key, n, s in groups_of(rows)]


def expect_jsonl(k, rows):
    nm = names(k) + ["count", "sum:v"]
    return [dict(zip(nm, [c.decode("utf-8") for c in key] + [n, s])) for key, n, s in groups_of(rows)]


def shared_rows(k, prefix, extra=2, per=1):
    """`extra` distinct keys that share `prefix` bytes (spread over the k columns, the last one differing), `per` rows each."""
    cols = [b"x" * (prefix // k + (1 if i < prefix % k else 0)) for i in range(k)]
    rows = []
    for e in range(extra):
        key = list(cols)
        key[-1] = key[-1] + str(e).encode()
        for r in range(per):
            rows.append((tuple(key), e + 1 + r))
    return rows


def judged(test, res, what=""):
    test.assertNotIn(res.status, TRAPS, "%s: a signal, status %d (%r)" % (what, res.status, res.stderr[:200]))
    test.assertGreaterEqual(res.status, 0, what)


class Deep(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)

    def flags(self, k, *more):
        return ["--group", ",".join(names(k)), "--agg", "count,sum:v", *more]

    def answer_csv(self, k, rows, *more, name="t.csv", data=None, how="file", threads=None, timeout=300):
        """Run the grouping of `rows` (csv input) and demand the reference's csv bytes."""
        data = table_csv(k, rows) if data is None else data
        extra = list(more) + (["--threads", str(threads)] if threads else [])
        args = self.flags(k, "--format", "csv", "--max-line-bytes", str(LINE_MAX), *extra)
        res = self.invoke(name, data, args, how, timeout)
        judged(self, res, "%s k=%d prefix=%d %s" % (how, k, len(rows[0][0][0]) if rows else 0, extra))
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.stdout, expect_csv(k, rows))
        return res

    def invoke(self, name, data, args, how, timeout=300):
        path = self.s.write(name, data)
        if how == "file":
            return run("--root", self.s.dir, *args, name, timeout=timeout)
        if how == "dash":
            return run(*args, "-", stdin=data, timeout=timeout)
        return run(*args, stdin=data, timeout=timeout)

    # ---- the rows of the issue's table ------------------------------------------------------------------------------------------------

    def test_issue_two_keys_400k_one_column(self):
        """The reproduction: two keys sharing 400 KiB, `--group a --agg count --format csv` (exit 139 before the fix)."""
        p = b"x" * (400 * KIB)
        data = b"a,v\n" + p + b"0,1\n" + p + b"1,1\n"
        res = run("--group", "a", "--agg", "count", "--format", "csv", "--root", self.s.dir, self.s.write("one.csv", data).name)
        judged(self, res, "issue repro")
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.stdout, b"a,count\n" + p + b"0,1\n" + p + b"1,1\n")

    def test_issue_default_json_output(self):
        """The same with the default format (json, `--max-bytes` 1 MiB): the page holds both keys."""
        p = b"x" * (400 * KIB)
        data = b"a,v\n" + p + b"0,1\n" + p + b"1,1\n"
        res = run("--group", "a", "--agg", "count", "--root", self.s.dir, self.s.write("one.csv", data).name)
        judged(self, res, "issue repro, json")
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.doc()["data"]["rows"], [[p.decode() + "0", "1"], [p.decode() + "1", "1"]])

    def test_issue_four_keys_210_to_400k(self):
        for kib in (210, 220, 230, 240, 250, 300, 350, 400):
            with self.subTest(kib=kib):
                rows = shared_rows(1, kib * KIB, extra=4)
                self.answer_csv(1, rows)

    def test_issue_threads_4(self):
        rows = shared_rows(1, 400 * KIB)
        self.answer_csv(1, rows, threads=4)

    def test_issue_two_group_columns(self):
        """The issue's `a,b` rows: 8 to 200 KiB passed before, and 400 KiB in the two columns together did not."""
        for kib in (8, 16, 32, 64, 128, 164, 200, 256, 400, 800):
            with self.subTest(kib=kib):
                self.answer_csv(2, shared_rows(2, kib * KIB, extra=4))

    def test_issue_first_column_shared_second_long(self):
        """Every key has the same first cell, and the second cells share 400 KiB: the sort goes to the next field and then runs the second one's chunks."""
        p = b"x" * (400 * KIB)
        rows = [((b"same", p + b"0"), 1), ((b"same", p + b"1"), 2), ((b"same", p + b"1"), 3), ((b"samf", p + b"0"), 4)]
        self.answer_csv(2, rows)

    def test_issue_rows_that_passed(self):
        """4 keys at 164 KiB with 4, 16 and 64 rows each; keys that differ at byte 0; the same long key twice: they passed before and must still."""
        for per in (1, 4, 16, 64):
            with self.subTest(per=per):
                self.answer_csv(1, shared_rows(1, 164 * KIB, extra=4, per=per))
        a = b"a" * (400 * KIB)
        b = b"b" * (400 * KIB)
        self.answer_csv(1, [((a,), 1), ((b,), 2)])
        self.answer_csv(1, [((a,), 1), ((a,), 5)])

    def test_issue_many_rows_one_long_prefix(self):
        """Many keys under one long prefix: each level of the sort holds a long run (the radix, not the insertion, sorts it)."""
        rows = shared_rows(1, 100 * KIB, extra=300, per=2)
        self.answer_csv(1, rows)

    # ---- the variants -----------------------------------------------------------------------------------------------------------------

    def test_standard_input(self):
        rows = shared_rows(2, 600 * KIB, extra=3)
        for how in ("dash", "bare"):
            with self.subTest(how=how):
                self.answer_csv(2, rows, how=how)

    def test_jsonl_input(self):
        for k in (1, 3):
            rows = shared_rows(k, 500 * KIB, extra=3)
            for how in ("file", "dash", "bare"):
                with self.subTest(k=k, how=how):
                    args = self.flags(k, "--input-format", "jsonl", "--fields", ",".join(names(k) + ["v"]), "--format", "csv", "--max-line-bytes", str(LINE_MAX))
                    res = self.invoke("t.jsonl", table_jsonl(k, rows), args, how)
                    judged(self, res)
                    self.assertEqual(res.status, 0, res.stderr[:300])
                    self.assertEqual(res.stdout, expect_csv(k, rows))

    def test_threads_1_4_16(self):
        rows = shared_rows(2, 700 * KIB, extra=4, per=3)
        for t in (1, 4, 16):
            with self.subTest(threads=t):
                self.answer_csv(2, rows, threads=t)

    def test_threads_with_a_parallel_read(self):
        """Enough rows and a small chunk that the threads really split the file: the partial groups are merged and then sorted."""
        rows = shared_rows(1, 64 * KIB, extra=40, per=5)
        for t in (4, 16):
            with self.subTest(threads=t):
                self.answer_csv(1, rows, "--chunk-bytes", "65536", "--parallel-min-bytes", "1", threads=t)

    def test_output_formats(self):
        rows = shared_rows(3, 300 * KIB, extra=3)
        data = table_csv(3, rows)
        base = self.flags(3, "--max-line-bytes", str(LINE_MAX), "--max-bytes", str(64 * MIB))
        res = self.invoke("t.csv", data, base + ["--format", "jsonl"], "file")
        judged(self, res)
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual([json.loads(line) for line in res.stdout.splitlines()], expect_jsonl(3, rows))
        res = self.invoke("t.csv", data, base, "file")
        judged(self, res)
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.doc()["data"]["rows"], expect_rows(3, rows))

    def test_typed_keys_beside_a_long_text_key(self):
        """`--group g1,n:int,ts:timestamp@day`: the key goes on, after the long field, into typed fields."""
        p = b"x" * (500 * KIB)
        data = (b"g1,n,ts,v\n" + p + b",5,2026-01-02T03:04:05Z,1\n" + p + b",5,2026-01-02T23:00:00Z,2\n" + p + b",-3,2026-01-02T03:04:05Z,3\n" + p + b",5,2026-02-01T00:00:00Z,4\n"
                + p + b"2,5,2026-01-02T03:04:05Z,5\n")
        args = ["--group", "g1,n:int,ts:timestamp@day", "--agg", "count,sum:v", "--format", "csv", "--max-line-bytes", str(LINE_MAX)]
        res = self.invoke("t.csv", data, args, "file")
        judged(self, res)
        self.assertEqual(res.status, 0, res.stderr[:300])
        # the order of the typed keys is the tool's (by value); the long text key decides first, then the number, then the instant
        want = (b"g1,n,ts,count,sum:v\n" + p + b",-3,2026-01-02T00:00:00Z,1,3\n" + p + b",5,2026-01-02T00:00:00Z,2,3\n" + p + b",5,2026-02-01T00:00:00Z,1,4\n"
                + p + b"2,5,2026-01-02T00:00:00Z,1,5\n")
        self.assertEqual(res.stdout, want)

    def test_typed_key_alone_does_not_regress(self):
        data = b"n,v\n5,1\n-3,2\n5,3\n10,4\n"
        res = self.invoke("t.csv", data, ["--group", "n:int", "--agg", "count", "--format", "csv"], "file")
        self.assertEqual(res.stdout, b"n,count\n-3,1\n5,2\n10,1\n")

    def test_join_group_key_from_either_side(self):
        """`--join` with the group key on the build side and on the probe side."""
        p = b"x" * (450 * KIB)
        left = b"id,v\n1,1\n2,2\n3,3\n"
        right = b"id,g1\n1," + p + b"0\n2," + p + b"1\n3," + p + b"1\n"
        self.s.write("right.csv", right)
        self.s.write("left.csv", left)
        res = run("--root", self.s.dir, "--join", "right.csv", "--on", "id", "--group", "g1", "--agg", "count,sum:v", "--format", "csv", "--max-line-bytes", str(LINE_MAX), "left.csv")
        judged(self, res, "join, build side")
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.stdout, b"g1,count,sum:v\n" + p + b"0,1,1\n" + p + b"1,2,5\n")
        res = run("--root", self.s.dir, "--join", "left.csv", "--on", "id", "--group", "g1", "--agg", "count,sum:v", "--format", "csv", "--max-line-bytes", str(LINE_MAX), "right.csv")
        judged(self, res, "join, probe side")
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.stdout, b"g1,count,sum:v\n" + p + b"0,1,1\n" + p + b"1,2,5\n")

    def test_sort_and_top_after_the_group(self):
        """`--sort` by an aggregate and `--top`: the other order; the default order still has to be computed for them? No: it must not crash either way."""
        rows = shared_rows(1, 500 * KIB, extra=5, per=2)
        res = self.invoke("t.csv", table_csv(1, rows), self.flags(1, "--sort", "-sum:v", "--top", "2", "--format", "csv", "--max-line-bytes", str(LINE_MAX)), "file")
        judged(self, res)
        self.assertEqual(res.status, 0, res.stderr[:300])
        order = sorted(groups_of(rows), key=lambda g: -g[2])[:2]
        want = b"g1,count,sum:v\n" + b"".join(key[0] + b",%d,%d\n" % (c, s) for key, c, s in order)
        self.assertEqual(res.stdout, want)

    def test_explain_is_not_a_run(self):
        p = b"x" * (500 * KIB)
        self.s.write("e.csv", b"a,v\n" + p + b"0,1\n" + p + b"1,1\n")
        res = run("--root", self.s.dir, "--group", "a", "--agg", "count", "--explain", "e.csv")
        judged(self, res)
        self.assertEqual(res.status, 0, res.stderr[:300])

    # ---- the sweep --------------------------------------------------------------------------------------------------------------------

    def test_sweep_sizes_and_columns(self):
        """Prefixes of 1 KiB to 8 MiB in 1, 2, 3, 5, 6 and 7 group columns (csv in, csv out, one thread)."""
        for size in (1 * KIB, 64 * KIB, 256 * KIB, 1 * MIB, 4 * MIB, 8 * MIB):
            for k in (1, 2, 3, 5, 6, 7):
                with self.subTest(size=size, k=k):
                    self.answer_csv(k, shared_rows(k, size, extra=2))

    def test_sweep_threads_input_and_format(self):
        """At 256 KiB and 1 MiB: threads 1, 4, 16; a file, `-`, bare standard input; csv and JSON lines in; 1, 3, 7 columns."""
        for size in (256 * KIB, 1 * MIB):
            for k in (1, 3, 7):
                rows = shared_rows(k, size, extra=3)
                for t in (1, 4, 16):
                    for how in ("file", "dash", "bare"):
                        for fmt in ("csv", "jsonl"):
                            with self.subTest(size=size, k=k, threads=t, how=how, input=fmt):
                                if fmt == "csv":
                                    self.answer_csv(k, rows, how=how, threads=t)
                                else:
                                    args = self.flags(k, "--input-format", "jsonl", "--fields", ",".join(names(k) + ["v"]), "--format", "csv", "--max-line-bytes", str(LINE_MAX), "--threads", str(t))
                                    res = self.invoke("t.jsonl", table_jsonl(k, rows), args, how)
                                    judged(self, res)
                                    self.assertEqual(res.status, 0, res.stderr[:300])
                                    self.assertEqual(res.stdout, expect_csv(k, rows))

    def test_sweep_8m_threads_stdin_jsonl(self):
        rows = shared_rows(2, 8 * MIB, extra=2)
        self.answer_csv(2, rows, threads=4)
        self.answer_csv(2, rows, how="dash", threads=16)
        args = self.flags(2, "--input-format", "jsonl", "--fields", "g1,g2,v", "--format", "csv", "--max-line-bytes", str(LINE_MAX))
        res = self.invoke("t.jsonl", table_jsonl(2, rows), args, "bare")
        judged(self, res)
        self.assertEqual(res.status, 0, res.stderr[:300])
        self.assertEqual(res.stdout, expect_csv(2, rows))

    def test_the_line_ceiling(self):
        """The longest line the tool holds (`--max-line-bytes` 16 MiB): two keys of about 16 MiB sharing all but their last byte."""
        for k in (1, 2):
            with self.subTest(k=k):
                self.answer_csv(k, shared_rows(k, LINE_MAX - 64, extra=2))

    def test_limits_that_still_apply_are_tagged(self):
        """Past the tool's own limits the answer is a tagged refusal, not a signal."""
        rows = shared_rows(1, 3 * MIB, extra=2)
        data = table_csv(1, rows)
        res = self.invoke("t.csv", data, self.flags(1, "--format", "csv"), "file")  # the default --max-line-bytes is 1 MiB
        judged(self, res)
        self.assertEqual(res.status, 8)
        self.assertIn(b"limit.line-too-long", res.stdout + res.stderr)
        res = self.invoke("t.csv", data, self.flags(1, "--format", "csv", "--max-line-bytes", str(LINE_MAX), "--max-state-bytes", "1000000"), "file")
        judged(self, res)
        self.assertEqual(res.status, 8)
        self.assertIn(b"limit.state-too-large", res.stdout + res.stderr)
        res = self.invoke("t.csv", data, self.flags(1, "--max-line-bytes", str(LINE_MAX), "--max-bytes", "1000"), "file")
        judged(self, res)
        self.assertEqual(res.status, 8)
        self.assertIn(b"limit.output-too-large", res.stdout + res.stderr)
        res = self.invoke("t.csv", data, self.flags(1, "--format", "csv", "--max-line-bytes", str(LINE_MAX), "--max-groups", "1"), "file")
        judged(self, res)
        self.assertEqual(res.status, 8)
        self.assertIn(b"limit.too-many-groups", res.stdout + res.stderr)

    def test_the_depth_does_not_depend_on_the_stack(self):
        """The same answer when the stack is 256 KiB (a recursion of 2 MiB / 6 frames would need 80 MiB)."""
        rows = shared_rows(2, 2 * MIB, extra=3)
        self.s.write("t.csv", table_csv(2, rows))
        argv = ["sh", "-c", 'ulimit -s 256 && exec "$0" "$@"', binary(), "--root", str(self.s.dir), *self.flags(2, "--format", "csv", "--max-line-bytes", str(LINE_MAX)), "t.csv"]
        p = subprocess.run(argv, capture_output=True, input=b"", timeout=300)
        self.assertNotIn(p.returncode, TRAPS, p.stderr[:200])
        self.assertEqual(p.returncode, 0, p.stderr[:300])
        self.assertEqual(p.stdout, expect_csv(2, rows))


# ---- the fuzz -----------------------------------------------------------------------------------------------------------------------

def random_rows(rng):
    k = rng.choice((1, 1, 2, 3, 4))
    pools = []
    for _ in range(k):
        n = rng.choice((1, 2, 3, 5))
        pool = []
        for _ in range(n):
            base = rng.choice((0, 1, 5, 6, 7, 11, 12, 13, 18, 36, 100, 600, 3000))
            pool.append(b"y" * base)
        pools.append(pool)
    count = rng.choice((2, 3, 10, 33, 34, 80))
    rows = []
    for _ in range(count):
        key = []
        for pool in pools:
            cell = rng.choice(pool)
            for _ in range(rng.choice((0, 0, 1, 1, 2, 4))):
                cell += rng.choice(TAILS)
            if rng.random() < 0.05:
                cell = b""
            key.append(cell)
        rows.append((tuple(key), rng.randint(-5, 50)))
    return k, rows


class Fuzz(unittest.TestCase):
    CASES = int(os.environ.get("GROUP_DEEP_CASES", "250"))

    def test_random_keys_against_the_reference(self):
        rng = random.Random(7902)
        s = Scratch()
        self.addCleanup(s.cleanup)
        compared = 0
        for case in range(self.CASES):
            k, rows = random_rows(rng)
            data = table_csv(k, rows)
            s.write("f.csv", data)
            args = ["--group", ",".join(names(k)), "--agg", "count,sum:v", "--format", "csv", "--root", s.dir]
            if case % 5 == 0:
                args += ["--threads", "4", "--chunk-bytes", "1024", "--parallel-min-bytes", "1"]
            res = run(*args, "f.csv")
            self.assertNotIn(res.status, TRAPS, "case %d: signal %d" % (case, res.status))
            self.assertEqual(res.status, 0, "case %d: %r" % (case, res.stderr[:200]))
            self.assertEqual(res.stdout, expect_csv(k, rows), "case %d" % case)
            if MAIN_TABLE and os.path.exists(MAIN_TABLE):
                theirs = subprocess.run([MAIN_TABLE, *[str(a) for a in args], "f.csv"], capture_output=True, input=b"")
                self.assertEqual((theirs.returncode, theirs.stdout), (res.status, res.stdout), "case %d: main's binary answers differently" % case)
                compared += 1
        if MAIN_TABLE:
            print("\ngroup-deep fuzz: %d cases, %d also run by main's binary, all equal" % (self.CASES, compared))


if __name__ == "__main__":
    unittest.main()
