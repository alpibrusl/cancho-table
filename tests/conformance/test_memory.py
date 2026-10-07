"""M9 -- memory does not grow with the input: one chunk, the longest line and
the header. Peak resident set of the child, from wait4, on files of 2 MB and 37 MB and on one record that spans 200,000 lines."""

import os
import subprocess
import sys
import tempfile
import unittest

from harness import ROOT, Scratch, binary

HELPER = None


def helper():
    """tests/conformance/maxrss.c, built once. A process forked from the test
    runner inherits the runner's resident-set high-water mark (Linux keeps the
    larger across exec), so the measurement is taken by a small launcher."""
    global HELPER
    if HELPER is None:
        out = os.path.join(tempfile.mkdtemp(prefix="maxrss-"), "maxrss")
        subprocess.run(["cc", "-O1", "-o", out, str(ROOT / "tests" / "conformance" / "maxrss.c")], check=True)
        HELPER = out
    return HELPER


def peak_rss(*args):
    """(exit status, peak resident set in bytes) of one run of the tool."""
    p = subprocess.run([helper(), binary(), *[str(a) for a in args]], capture_output=True, text=True, check=True)
    rss, status = p.stdout.split()
    return int(status), int(rss) * (1 if sys.platform == "darwin" else 1024)


def make(path, rows):
    with open(path, "w", newline="") as f:
        f.write("id,status,bytes,path,note\n")
        for i in range(rows):
            f.write('%d,200,%d,/p/%d,"a,b %d"\n' % (i, i % 100000, i, i))


class Memory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_flat_in_the_size_of_the_file(self):
        make(self.s.dir / "small.csv", 60_000)       # about 2 MB
        make(self.s.dir / "large.csv", 960_000)      # about 30 MB
        small_size = os.path.getsize(self.s.dir / "small.csv")
        large_size = os.path.getsize(self.s.dir / "large.csv")
        self.assertGreater(large_size, 15 * small_size)
        rc1, small = peak_rss("--root", self.s.dir, "small.csv")
        rc2, large = peak_rss("--root", self.s.dir, "large.csv")
        self.assertEqual((rc1, rc2), (0, 0))
        print("\npeak RSS: %d KB on %d KB of input, %d KB on %d KB" % (small // 1024, small_size // 1024, large // 1024, large_size // 1024), file=sys.stderr)
        self.assertLess(large - small, 1 << 20, "RSS grew by more than 1 MiB on 15x the input")
        self.assertLess(large, 24 << 20)

    def test_select_is_flat_too(self):
        # csv streams every row; json holds the page (1000 rows) and nothing else.
        make(self.s.dir / "small.csv", 60_000)
        make(self.s.dir / "large.csv", 960_000)
        for flags in (["--select", "status,note", "--format", "csv"], ["--select", "id,path,bytes"], ["--select", "note", "--limit", "1000", "--from", "900000"]):
            rc1, small = peak_rss("--root", self.s.dir, *flags, "small.csv")
            rc2, large = peak_rss("--root", self.s.dir, *flags, "large.csv")
            self.assertEqual((rc1, rc2), (0, 0), flags)
            print("\npeak RSS %s: %d KB on 2 MB, %d KB on 37 MB" % (" ".join(flags), small // 1024, large // 1024), file=sys.stderr)
            self.assertLess(large - small, 1 << 20, flags)
            self.assertLess(large, 8 << 20, flags)

    def test_filter_and_group_are_flat_too(self):
        make(self.s.dir / "small.csv", 60_000)
        make(self.s.dir / "large.csv", 960_000)
        for flags in (["--where", "status=200 and bytes:int>50", "--select", "id", "--format", "csv"],
                      ["--where", "bytes:int>50", "--limit", "1000"],
                      ["--where", "status=200 and bytes:dec(2)>50.5", "--select", "id", "--format", "csv"],   # docs/numbers.md N1
                      ["--where", "bytes:dec(0) in (7, 8, 99999)", "--limit", "1000"],
                      ["--group", "status", "--agg", "count,sum:bytes:dec(2),min:bytes:dec(2),max:bytes:dec(2),mean:bytes:dec(2)@4,distinct:bytes:dec(0)"],   # docs/numbers.md N2
                      ["--group", "status", "--agg", "count,sum:bytes,min:bytes,max:bytes"],
                      ["--group", "status", "--agg", "count,distinct:status", "--sort", "-count"]):
            rc1, small = peak_rss("--root", self.s.dir, *flags, "small.csv")
            rc2, large = peak_rss("--root", self.s.dir, *flags, "large.csv")
            self.assertEqual((rc1, rc2), (0, 0), flags)
            print("\npeak RSS %s: %d KB on 2 MB, %d KB on 37 MB" % (" ".join(flags), small // 1024, large // 1024), file=sys.stderr)
            self.assertLess(large - small, 1 << 20, flags)
            self.assertLess(large, 8 << 20, flags)

    def test_threads_are_bounded_by_the_ranges_not_the_file(self):
        # O(threads x range): the same ranges on a file 18 times as large use the same memory,
        # and the memory is a few times (threads x range), never the file.
        make(self.s.dir / "small.csv", 60_000)
        make(self.s.dir / "large.csv", 960_000)
        range_bytes = 1 << 20
        for threads in (2, 4, 8):
            for flags in (["--where", "status=200 and bytes:int>50", "--select", "id,path", "--format", "csv"],
                          ["--group", "status", "--agg", "count,sum:bytes,distinct:status"],
                          ["--select", "note", "--limit", "1000", "--max-bytes", "4000000"]):
                common = flags + ["--threads", threads, "--chunk-bytes", range_bytes, "--parallel-min-bytes", 0]
                rc1, small = peak_rss("--root", self.s.dir, *common, "small.csv")
                rc2, large = peak_rss("--root", self.s.dir, *common, "large.csv")
                self.assertEqual((rc1, rc2), (0, 0), common)
                print("\npeak RSS %s: %d KB on 2 MB, %d KB on 37 MB" % (" ".join(str(c) for c in common), small // 1024, large // 1024), file=sys.stderr)
                self.assertLess(large, (8 + threads * 4 * 1) * range_bytes, common)
                self.assertLess(large - small, (2 + threads * 3) * range_bytes, common)

    def test_the_first_rows_of_a_sort_are_flat_in_the_size_of_the_file(self):
        # Top-N is bounded memory: 2 * (page + 1) + 1 rows held, whatever the file holds.
        make(self.s.dir / "small.csv", 60_000)
        make(self.s.dir / "large.csv", 960_000)
        for flags in (["--order-by", "-bytes:int,note", "--limit", "1000"], ["--order-by", "note", "--top", "500", "--format", "csv"],
                      ["--order-by", "-id:int", "--select", "id,path", "--limit", "100", "--from", "1000"]):
            rc1, small = peak_rss("--root", self.s.dir, *flags, "small.csv")
            rc2, large = peak_rss("--root", self.s.dir, *flags, "large.csv")
            self.assertEqual((rc1, rc2), (0, 0), flags)
            print("\npeak RSS %s: %d KB on 2 MB, %d KB on 37 MB" % (" ".join(flags), small // 1024, large // 1024), file=sys.stderr)
            self.assertLess(large - small, 1 << 20, flags)
            self.assertLess(large, 8 << 20, flags)

    def test_a_full_sort_is_within_its_bound_and_refused_at_it(self):
        make(self.s.dir / "large.csv", 960_000)
        make(self.s.dir / "small.csv", 60_000)
        # within the bound the rows are held: about what they weigh, and never more than --max-state-bytes plus the sort's scratch
        rc, peak = peak_rss("--root", self.s.dir, "--order-by", "-bytes:int", "--format", "csv", "small.csv")
        self.assertEqual(rc, 0)
        self.assertLess(peak, 24 << 20)
        # at the bound: the refusal comes when the bytes held pass it, so the memory is about the bound, not the file
        bound = 8 << 20
        rc, peak = peak_rss("--root", self.s.dir, "--order-by", "-bytes:int", "--format", "csv", "--max-state-bytes", bound, "large.csv")
        self.assertEqual(rc, 8)
        print("\npeak RSS of a sort refused at 8 MiB of state: %d KB" % (peak // 1024), file=sys.stderr)
        self.assertLess(peak, bound * 4)   # the buffers double as they grow: old and new are live together, about 3x
        rc, peak = peak_rss("--root", self.s.dir, "--order-by", "-bytes:int", "--format", "csv", "--max-sort-rows", 100000, "large.csv")
        self.assertEqual(rc, 8)
        self.assertLess(peak, 64 << 20)

    def test_a_grouping_is_bounded_by_its_limits(self):
        # Every id is a group: the default 100,000 groups stop it, and memory is
        # what 100,000 short keys take, not what a million rows would.
        make(self.s.dir / "ids.csv", 960_000)
        rc, peak = peak_rss("--root", self.s.dir, "--group", "id", "ids.csv")
        self.assertEqual(rc, 8)
        print("\npeak RSS at --max-groups 100000: %d KB" % (peak // 1024), file=sys.stderr)
        self.assertLess(peak, 48 << 20)
        rc, peak = peak_rss("--root", self.s.dir, "--group", "id", "--agg", "distinct:path", "--max-groups", "1000000", "--max-state-bytes", "4000000", "ids.csv")
        self.assertEqual(rc, 8)
        self.assertLess(peak, 48 << 20)

    def test_a_selected_record_of_many_lines_is_bounded(self):
        # One selected field of 20 MB over 200,000 lines is a record past the
        # limit, refused, not held.
        with open(self.s.dir / "span2.csv", "wb") as f:
            f.write(b'a,b\n1,"')
            f.write((b"y" * 99 + b"\n") * 200000)
            f.write(b'"\n')
        rc, peak = peak_rss("--root", self.s.dir, "--select", "b", "span2.csv")
        self.assertEqual(rc, 8)
        self.assertLess(peak, 8 << 20)

    def test_flat_in_the_length_of_a_record(self):
        # 20 MB in one quoted field, over 200,000 lines.
        with open(self.s.dir / "span.csv", "wb") as f:
            f.write(b'a,b\n1,"')
            f.write((b"y" * 99 + b"\n") * 200000)
            f.write(b'"\n')
        rc, peak = peak_rss("--root", self.s.dir, "span.csv")
        self.assertEqual(rc, 0)
        self.assertLess(peak, 24 << 20)


if __name__ == "__main__":
    unittest.main()
