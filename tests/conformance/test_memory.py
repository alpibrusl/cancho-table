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
