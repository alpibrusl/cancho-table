"""`--order-by` read by several threads (docs/gap-sort.md): the sequential answer, byte for byte, for every thread count and range size.

A whole sort written as csv is sorted by runs (one for each range of the file, sorted by its thread) that the parent merges in partitions (psort.cho). Every
test here runs a plan sequentially and then through many `--threads` and `--chunk-bytes` (a range of 1 byte puts a boundary in every record, so the speculation is
wrong wherever a quoted field holds a newline), and requires the same status, the same standard output and the same standard error, whatever happens: an
answer, a refusal (the same row, line, column and value: the first in file order), a bound passed (`--max-sort-rows`, `--max-state-bytes`, `--max-rows`).
The sequential sort itself is judged against Python's stable sort by test_sort.py; this file does not repeat that.
"""

import csv
import io
import random
import unittest

import test_sort
from harness import Scratch, TRAPS, run

# (threads, range bytes): the first has a boundary in almost every record, the last is the default range size
SETTINGS = ((2, 1), (3, 7), (5, 64), (16, 1000), (64, 3), (4, 4194304))


def csv_bytes(rows):
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue().encode("latin-1")


class PSort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def same(self, data, flags, settings=SETTINGS, label=None):
        """The sequential run, and the same plan with each setting: all the same bytes. Answers the sequential result."""
        self.s.write("t.csv", data)
        base = run("--root", self.s.dir, *flags, "--format", "csv", "t.csv")
        self.assertNotIn(base.status, TRAPS, (flags, data[:200]))
        for threads, chunk in settings:
            got = run("--root", self.s.dir, *flags, "--format", "csv", "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, "t.csv")
            self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr), (label, flags, threads, chunk, data[:200]))
        return base

    def test_plain_sorts(self):
        rng = random.Random(1)
        rows = [[str(rng.randrange(50)), "t%d" % rng.randrange(9), "r%05d" % i] for i in range(3000)]
        data = csv_bytes([["n", "t", "id"]] + rows)
        for keys in ("n:int", "-n:int", "t", "-t", "-n:int,t", "t,-n:int", "n,t,id", "-id", "#3", "-#1:int,-#2"):
            base = self.same(data, ["--order-by", keys])
            self.assertEqual(base.status, 0)
        # with a selection of columns that leaves the keys out, and a condition
        self.same(data, ["--order-by", "-n:int,t", "--select", "id"])
        self.same(data, ["--order-by", "t", "--select", "id,n", "--where", "n:int > 20"])

    def test_every_row_is_a_tie_or_few_values(self):
        rows = [["same", str(i)] for i in range(500)]
        self.same(csv_bytes([["k", "i"]] + rows), ["--order-by", "k"])
        self.same(csv_bytes([["k", "i"]] + rows), ["--order-by", "-k"])
        rows = [[["a", "b"][i % 2], str(i % 5)] for i in range(500)]
        self.same(csv_bytes([["k", "i"]] + rows), ["--order-by", "k"])
        self.same(csv_bytes([["k", "i"]] + rows), ["--order-by", "-k,i:int"])

    def test_text_keys_longer_than_their_words(self):
        # a key that agrees with another on the first 14 bytes (and on 7), or has a NUL, or is empty: the full bytes decide
        rng = random.Random(2)
        stems = ["", "a", "commonp", "commonprefix", "commonprefixxxxx", "commonprefixxxxxx", "commonprefixxxxxxx", "x\x00", "x"]
        keys = [stem + suffix for stem in stems for suffix in ("", "0", "1", "10", "9", "a", "Z", "\xe9")]
        rows = [[rng.choice(keys), rng.choice(keys), str(i)] for i in range(400)]
        data = csv_bytes([["k", "l", "i"]] + rows)
        for flags in ("k", "-k", "k,l", "-k,-l", "l,-k", "k,i:int", "-l,i:int"):
            self.same(data, ["--order-by", flags])

    def test_quoted_newlines_and_doubled_quotes(self):
        rng = random.Random(3)
        cells = ["a", "b\nc", 'q"z', "x,y", "", "l1\nl2\nl3", '"', "it's", "tab\there"]
        rows = [[rng.choice(cells), str(rng.randrange(7)), rng.choice(cells)] for i in range(300)]
        data = csv_bytes([["k", "n", "m"]] + rows)
        for flags in ("k", "-k,n:int", "m,k", "n:int,-m"):
            self.same(data, ["--order-by", flags])
        self.same(data, ["--order-by", "k", "--select", "m,n"])

    def test_integer_edges(self):
        # a range of integers that cannot be offset by its least (past 62 bits) is sorted another way; ranges that can are radix-sorted: both in one file
        edge = ["9223372036854775807", "-9223372036854775808", "0", "-0", "+0", "007", "-1", "1", "4611686018427387904", "-4611686018427387904", "4611686018427387905", "-4611686018427387905",
                "4611686018427387903", "-4611686018427387903", "9223372036854775806", "-9223372036854775807", "3", "3", "3"]
        rng = random.Random(10)
        rows = [[rng.choice(edge), "r%d" % i] for i in range(400)]
        data = csv_bytes([["n", "id"]] + rows)
        for keys in ("n:int", "-n:int", "n:int,id", "-id,n:int"):
            base = self.same(data, ["--order-by", keys])
            self.assertEqual(base.status, 0, (keys, base.stderr))
        small = [[rng.choice(edge[:8]), "r%d" % i] for i in range(200)]
        self.same(csv_bytes([["n", "id"]] + small), ["--order-by", "n:int"])

    def test_typed_keys(self):
        rng = random.Random(4)
        rows = []
        for i in range(300):
            rows.append(["%.2f" % (rng.randint(-300, 300) / 100), rng.choice(["1e3", "0.5", "-2.5e-1", "7", "1.5E2", "0.1"]), str(rng.randint(-9, 9)), "r%d" % i])
        data = csv_bytes([["d", "f", "n", "id"]] + rows)
        for flags in ("d:dec(2)", "-d:dec(2)", "f:float", "-f:float,n:int", "n:int,d:dec(2)", "d:dec(2),f:float,-n:int"):
            base = self.same(data, ["--order-by", flags])
            self.assertEqual(base.status, 0, (flags, base.stderr))

    def test_the_first_refusal_in_file_order(self):
        rng = random.Random(5)
        for case in range(60):
            n = rng.randrange(40, 300)
            rows = [[str(rng.randrange(100)), "t%d" % rng.randrange(9), str(rng.randrange(1000))] for _ in range(n)]
            for _ in range(rng.choice([1, 2, 3])):
                at = rng.randrange(n)
                rows[at][rng.choice([0, 2])] = rng.choice(["", "x", "1.5", " 7", "9223372036854775808", "-9223372036854775809", "1e3"])
            data = csv_bytes([["a", "t", "b"]] + rows)
            for keys in ("a:int", "t,a:int", "-b:int,t", "b:int,a:int"):
                base = self.same(data, ["--order-by", keys], label=case)
                self.assertIn(base.status, (0, 8))
            self.same(data, ["--order-by", "a:int", "--where", "t != 't3'"], label=case)

    def test_a_ragged_row_and_a_bad_quote(self):
        rows = [[str(i % 7), "r%d" % i] for i in range(200)]
        good = csv_bytes([["k", "id"]] + rows)
        lines = good.split(b"\n")
        for at in (3, 60, 150, 199):
            bad = list(lines)
            bad[at] = b"1"
            self.same(b"\n".join(bad), ["--order-by", "k"])
            bad = list(lines)
            bad[at] = b'1,"x'
            self.same(b"\n".join(bad), ["--order-by", "k"])
            bad = list(lines)
            bad[at] = b'1,"x"y'
            self.same(b"\n".join(bad), ["--order-by", "k"])

    def test_the_bounds_are_exactly_where_the_sequential_sort_puts_them(self):
        rng = random.Random(6)
        rows = [[str(rng.randrange(7)), "r%04d" % i, rng.choice(["a", 'q"z', "x,y"])] for i in range(400)]
        data = csv_bytes([["k", "id", "m"]] + rows)
        self.s.write("t.csv", data)
        # --max-sort-rows: 399 passes it, 400 does not
        for most in (1, 99, 100, 399, 400, 401, 2000):
            self.same(data, ["--order-by", "k", "--max-sort-rows", most])
            self.same(data, ["--order-by", "m,k", "--max-sort-rows", most])
        # --max-state-bytes: every value from the first that is enough down to the first that is not, found by the sequential run
        lo, hi = 0, 1 << 20
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            got = run("--root", self.s.dir, "--order-by", "m,k", "--max-state-bytes", mid, "--format", "csv", "t.csv")
            if got.status == 0:
                hi = mid
            else:
                lo = mid
        self.assertEqual(run("--root", self.s.dir, "--order-by", "m,k", "--max-state-bytes", hi, "--format", "csv", "t.csv").status, 0)
        for state in range(max(1, hi - 40), hi + 40, 1):
            self.same(data, ["--order-by", "m,k", "--max-state-bytes", state], settings=((3, 7), (16, 1000)))
        for state in (1, 100, 2000, hi - 1, hi, hi + 1):
            self.same(data, ["--order-by", "k", "--max-state-bytes", state])
        # --max-rows is a bound on the rows read
        for most in (1, 399, 400, 401):
            self.same(data, ["--order-by", "k", "--max-rows", most])
        # a bound and a typed refusal, which is first in file order
        bad = [list(r) for r in rows]
        bad[250][0] = "x"
        for most in (100, 249, 250, 251, 300, 400):
            self.same(csv_bytes([["k", "id", "m"]] + bad), ["--order-by", "k:int", "--max-sort-rows", most])
            self.same(csv_bytes([["k", "id", "m"]] + bad), ["--order-by", "k:int", "--max-state-bytes", 40 * most])

    def test_fewer_rows_than_threads_and_the_empty_cases(self):
        self.same(b"a,b\n", ["--order-by", "a"])
        self.same(b"a,b\n1,2\n", ["--order-by", "a"])
        self.same(b"a,b\n3,2\n1,2\n", ["--order-by", "-a:int"])
        self.same(b"a\n", ["--order-by", "a:int"])
        self.same(b"a,b\n1,2\n3,4", ["--order-by", "a:int"])           # no newline at the end
        self.same(b"a,b\r\n3,2\r\n1,2\r\n", ["--order-by", "a"])        # CRLF
        self.same(b"a,b\n\n3,2\n\n\n1,2\n", ["--order-by", "a"])        # blank lines

    def test_other_delimiters_and_a_bom(self):
        rng = random.Random(11)
        cells = ["a", "b c", 'q"z', "x;y", "", "l1\nl2", "t\tt"]
        rows = [[rng.choice(cells), str(rng.randrange(9)), rng.choice(cells)] for _ in range(200)]
        for name, delim in (("tab", "\t"), (";", ";")):
            out = io.StringIO(newline="")
            csv.writer(out, delimiter=delim, lineterminator="\n").writerows([["k", "n", "m"]] + rows)
            data = out.getvalue().encode("latin-1")
            for flags in ("k", "-n:int,k", "m,-k"):
                self.same(data, ["--delimiter", name, "--order-by", flags])
            self.same(data, ["--delimiter", name, "--order-by", "n:int", "--select", "m,k"])
        # a byte order mark before the header is the file's, not the first name's
        data = b"\xef\xbb\xbf" + csv_bytes([["k", "n"]] + [[str(rng.randrange(30)), str(i)] for i in range(300)])
        self.same(data, ["--order-by", "k:int"])

    def test_a_file_of_many_ranges_and_waves(self):
        rng = random.Random(7)
        rows = [["key%07d" % rng.randrange(10 ** 7), str(rng.randrange(1000)), "x" * rng.randrange(1, 30)] for _ in range(60000)]
        data = csv_bytes([["k", "n", "p"]] + rows)
        self.same(data, ["--order-by", "k"], settings=((4, 100000), (16, 65536), (3, 4194304)))
        self.same(data, ["--order-by", "-n:int,p"], settings=((4, 100000), (8, 300000)))

    def test_a_page_a_top_or_json_is_the_sequential_sort(self):
        rng = random.Random(8)
        rows = [[str(rng.randrange(50)), "r%d" % i] for i in range(500)]
        data = csv_bytes([["n", "id"]] + rows)
        self.s.write("t.csv", data)
        # (the last but one: a `--top` that the bound makes a full sort, which `wanted` does not see, and which the gate must not take)
        for flags in (["--limit", 10], ["--top", 7], ["--from", 3], ["--limit", 4, "--from", 2], ["--top", 20, "--limit", 5, "--from", 18], ["--top", 250, "--max-sort-rows", 500], []):
            for fmt in ([], ["--format", "csv"]):
                base = run("--root", self.s.dir, "--order-by", "n:int", *flags, *fmt, "t.csv")
                for threads, chunk in SETTINGS:
                    got = run("--root", self.s.dir, "--order-by", "n:int", *flags, *fmt, "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, "t.csv")
                    self.assertEqual((got.status, got.stdout), (base.status, base.stdout), (flags, fmt, threads, chunk))


class PSortPlans(test_sort.Sort):
    """The random plans of test_sort.py, each one also through every setting (the sort's own oracle is Python's)."""

    def check(self, recs, plan, label):
        keys, where, select, top, limit, frm = plan["keys"], plan.get("where"), plan.get("select"), plan.get("top", 0), plan.get("limit"), plan.get("from", 0)
        header = recs[0]
        rng = random.Random(label)
        flags = ["--order-by", test_sort.key_flag(keys, rng, header)]
        if where:
            flags += ["--where", where["text"]]
        if select:
            flags += ["--select", ",".join(select)]
        if top:
            flags += ["--top", top]
        if limit is not None:
            flags += ["--limit", limit]
        if frm:
            flags += ["--from", frm]
        self.s.write("t.csv", plan["data"])
        for fmt in (["--format", "csv"], []):
            base = run("--root", self.s.dir, *flags, *fmt, "t.csv")
            self.assertNotIn(base.status, TRAPS, flags)
            for threads, chunk in SETTINGS[:5]:
                got = run("--root", self.s.dir, *flags, *fmt, "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, "t.csv")
                self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr), (flags, fmt, threads, chunk, plan["data"][:300]))
        return True


# of test_sort.py's tests, only the random plans are run here again
for _name in dir(test_sort.Sort):
    if _name.startswith("test_") and _name != "test_random_plans":
        setattr(PSortPlans, _name, None)


if __name__ == "__main__":
    unittest.main()
