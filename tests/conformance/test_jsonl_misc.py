"""R2 -- small facts of the JSON-lines flags and refusals that the generated differentials do not pin: the bytes a syntax error quotes, the key a duplicate names, and the flags that
do not go together (docs/readers.md 12.5 and N15)."""

import unittest

from harness import Scratch, run


class Misc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def jl(self, data, *flags):
        self.s.write("t.jsonl", data)
        return run("--root", self.s.dir, "--input-format", "jsonl", *flags, "t.jsonl")

    def test_near_is_the_bytes_around_the_offset(self):
        got = self.jl(b'{"a":1,}\n', "--fields", "a")
        d = got.error()["detail"]
        self.assertEqual((got.first_rule(), d["offset"], d["near"]), ("parse.jsonl-syntax", 7, '{"a":1,}'))
        line = b'{"a":"' + b"x" * 100 + b'",}' + b"y" * 40 + b"\n"
        d = self.jl(line, "--fields", "a").error()["detail"]
        self.assertEqual(len(d["near"]), 32)
        self.assertEqual(d["near"], line[d["offset"] - 16:d["offset"] + 16].decode())

    def test_a_duplicate_names_its_key(self):
        d = self.jl(b'{"user":{"n":1,"n":2}}\n', "--fields", "user.n").error()["detail"]
        self.assertEqual((d["key"], d["column"], d["row"], d["line"]), ("n", "user.n", 1, 1))
        d = self.jl(b'{"a":1,"a":2}\n', "--fields", "a").error()["detail"]
        self.assertEqual(d["key"], "a")

    def test_discover_takes_no_fields_and_no_plan(self):
        for flags in (["--discover", "--fields", "a"], ["--discover", "--select", "a"], ["--discover", "--format", "csv"], ["--discover", "--explain"]):
            got = self.jl(b'{"a":1}\n', *flags)
            self.assertEqual(got.status, 2, (flags, got))
        self.assertEqual(self.jl(b'{"a":1}\n', "--discover").status, 0)

    def test_the_sizes_of_a_discovery_need_it_and_are_bounded(self):
        for flags in (["--discover-lines", "5", "--fields", "a"], ["--max-fields", "5", "--fields", "a"]):
            self.assertEqual(self.jl(b'{"a":1}\n', *flags).status, 2, flags)
        for flags in (["--discover", "--discover-lines", "0"], ["--discover", "--discover-lines", "1000001"], ["--discover", "--max-fields", "0"], ["--discover", "--max-fields", "4097"]):
            got = self.jl(b'{"a":1}\n', *flags)
            self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"), flags)
        for flags in (["--discover", "--discover-lines", "1000000"], ["--discover", "--max-fields", "4096"]):
            self.assertEqual(self.jl(b'{"a":1}\n', *flags).status, 0, flags)

    def test_the_node_limit_is_bounded(self):
        for n, ok in (("0", False), ("1", True), ("1048576", True), ("1048577", False)):
            got = self.jl(b'{}\n', "--fields", "a", "--absent", "empty", "--max-json-nodes", n)
            self.assertEqual(got.status == 0, ok, (n, got))
        for flags in (["--fields", "a"], ["--absent", "empty"], ["--max-json-nodes", "9"], ["--discover"]):
            self.s.write("c.csv", "a\n1\n")
            got = run("--root", self.s.dir, *flags, "c.csv")
            self.assertEqual((got.status, got.first_rule()), (2, "args.conflict"), flags)


if __name__ == "__main__":
    unittest.main()
