"""J6 (docs/readers.md section 8 and 12.9) -- memory of the JSON-lines reader does not grow with the input: one 64 KiB chunk, the longest line, the tape (3 ints a node, made
once: 96 KiB for the default 4096 nodes) and the page; with threads, a few ranges. Peak resident set of the child, from wait4 (test_memory.py's launcher), on files of 2 MB and
37 MB, from a file and through standard input, and on one line a megabyte long."""

import os
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import unittest

from harness import Scratch, binary
from test_memory import helper


def peak(*args, stdin_path=None):
    """(status, peak resident set in bytes) of one run; with `stdin_path` the file is the child's standard input."""
    argv = [helper(), binary(), *[str(a) for a in args]]
    with open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb") as fin:
        p = subprocess.run(argv, stdin=fin, capture_output=True, text=True, check=True)
    rss, status = p.stdout.split()
    return int(status), int(rss) * (1 if sys.platform == "darwin" else 1024)


def make(path, rows):
    with open(path, "w", newline="") as f:
        for i in range(rows):
            f.write('{"id":%d,"status":200,"bytes":%d,"path":"/p/%d","note":"a,b %d"}\n' % (i, i % 100000, i, i))


J = ["--input-format", "jsonl"]
F = "id,status,bytes,path,note"


class Memory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        make(cls.s.dir / "small.jsonl", 60_000)       # about 4 MB of lines
        make(cls.s.dir / "large.jsonl", 960_000)      # about 69 MB
        cls.small = os.path.getsize(cls.s.dir / "small.jsonl")
        cls.large = os.path.getsize(cls.s.dir / "large.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def flat(self, flags, ceiling=8 << 20, via_stdin=False):
        root = ["--root", self.s.dir]
        if via_stdin:
            rc1, small = peak(*J, *flags, "-", stdin_path=self.s.dir / "small.jsonl")
            rc2, large = peak(*J, *flags, "-", stdin_path=self.s.dir / "large.jsonl")
        else:
            rc1, small = peak(*root, *J, *flags, "small.jsonl")
            rc2, large = peak(*root, *J, *flags, "large.jsonl")
        self.assertEqual((rc1, rc2), (0, 0), flags)
        print("\npeak RSS jsonl%s %s: %d KB on %d KB, %d KB on %d KB" % (" stdin" if via_stdin else "", " ".join(str(f) for f in flags), small // 1024, self.small // 1024, large // 1024, self.large // 1024), file=sys.stderr)
        self.assertGreater(self.large, 15 * self.small)
        self.assertLess(large - small, 1 << 20, flags)
        self.assertLess(large, ceiling, flags)

    def test_the_shape_rows_and_groups_are_flat(self):
        for flags in (["--fields", F],
                      ["--fields", F, "--select", "status,note", "--format", "csv"],
                      ["--fields", F, "--select", "id,path,bytes", "--limit", "1000"],
                      ["--fields", "id,status,bytes", "--where", "status=200 and bytes:int>50", "--select", "id", "--format", "csv"],
                      ["--fields", "id,status,bytes", "--where", "status=200 and bytes:float>50.5", "--select", "id", "--format", "csv"],
                      ["--fields", "status,bytes", "--group", "status", "--agg", "count,sum:bytes,min:bytes,max:bytes"],
                      ["--fields", "status,bytes", "--group", "status", "--agg", "count,sum:bytes:float,mean:bytes:float"],
                      ["--fields", "status,bytes", "--report", "types", "--format", "csv"],
                      ["--fields", "id,bytes", "--order-by", "-bytes:int,id", "--limit", "1000"]):
            with self.subTest(flags=flags):
                self.flat(flags)

    def test_standard_input_is_flat_too(self):
        for flags in (["--fields", "id,status,bytes", "--where", "status=200 and bytes:int>50", "--select", "id", "--format", "csv"],
                      ["--fields", "status,bytes", "--group", "status", "--agg", "count,sum:bytes"]):
            with self.subTest(flags=flags):
                self.flat(flags, via_stdin=True)

    def test_threads_are_bounded_by_the_ranges_not_the_file(self):
        range_bytes = 1 << 20
        for threads in (2, 4, 8):
            for flags in (["--fields", "id,status,bytes,path", "--where", "status=200 and bytes:int>50", "--select", "id,path", "--format", "csv"],
                          ["--fields", "status,bytes", "--group", "status", "--agg", "count,sum:bytes,distinct:status"],
                          ["--fields", F, "--select", "note", "--limit", "1000", "--max-bytes", "4000000"]):
                common = flags + ["--threads", threads, "--chunk-bytes", range_bytes, "--parallel-min-bytes", 0]
                with self.subTest(common=common):
                    rc1, small = peak("--root", self.s.dir, *J, *common, "small.jsonl")
                    rc2, large = peak("--root", self.s.dir, *J, *common, "large.jsonl")
                    self.assertEqual((rc1, rc2), (0, 0), common)
                    print("\npeak RSS jsonl %s: %d KB on 4 MB, %d KB on 69 MB" % (" ".join(str(c) for c in common), small // 1024, large // 1024), file=sys.stderr)
                    self.assertLess(large, (8 + threads * 4) * range_bytes, common)
                    self.assertLess(large - small, (2 + threads * 3) * range_bytes, common)

    def test_a_line_of_a_megabyte_is_held_and_nothing_more(self):
        # one line of 1 MB (a string of 1,000,000 bytes) and a thousand short ones; the line is the most there is to hold
        with open(self.s.dir / "long.jsonl", "w") as f:
            f.write('{"a":"%s","b":1}\n' % ("x" * 1_000_000))
            for i in range(1000):
                f.write('{"a":"y","b":%d}\n' % i)
        rc, rss = peak("--root", self.s.dir, *J, "--fields", "a,b", "--max-line-bytes", 2_000_000, "--select", "b", "--format", "csv", "long.jsonl")
        self.assertEqual(rc, 0)
        print("\npeak RSS jsonl, one line of 1 MB: %d KB" % (rss // 1024), file=sys.stderr)
        self.assertLess(rss, 16 << 20)

    def test_a_tape_at_the_ceiling_costs_address_space_and_resident_set_only_for_the_nodes_a_line_uses(self):
        # 4096 nodes: 96 KiB; 1,048,576 nodes: 24 MiB of address space, made once, whose pages are resident only as lines touch them (a line of five keys touches the first)
        rc1, base = peak("--root", self.s.dir, *J, "--fields", "id", "small.jsonl")
        rc2, big = peak("--root", self.s.dir, *J, "--fields", "id", "--max-json-nodes", 1048576, "small.jsonl")
        self.assertEqual((rc1, rc2), (0, 0))
        print("\npeak RSS jsonl, tape of 4096 nodes: %d KB; of 1,048,576: %d KB" % (base // 1024, big // 1024), file=sys.stderr)
        self.assertLess(big - base, 1 << 20)
        self.assertLess(base, 8 << 20)
        # a line that uses them: 1,000,000 nodes in one line (an array of ones), declared a.0
        with open(self.s.dir / "nodes.jsonl", "w") as f:
            f.write('{"a":[%s]}\n' % ",".join(["1"] * 1_000_000))
        rc3, used = peak("--root", self.s.dir, *J, "--fields", "a.0", "--max-json-nodes", 1048576, "--max-line-bytes", 4_000_000, "nodes.jsonl")
        self.assertEqual(rc3, 0)
        print("\npeak RSS jsonl, a line of 1,000,000 nodes: %d KB" % (used // 1024), file=sys.stderr)
        self.assertGreater(used - base, 20 << 20)
        self.assertLess(used - base, 40 << 20)


if __name__ == "__main__":
    unittest.main()
