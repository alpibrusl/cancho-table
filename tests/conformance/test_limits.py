"""The limits are real: each bound is reached, answered with its own rule or
its own flag, and the answer is still well formed. Also the flags, the text
format, determinism and the schema, over the same fixtures."""

import unittest

from harness import Scratch, name_text, run, run_argv, validate


class Limits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_one_megabyte_field_hits_the_line_limit(self):
        # A 1 MiB field plus the rest of its line is past the default 1,048,576.
        data = b'a,b\n1,"' + b"x" * (1 << 20) + b'"\n2,3\n'
        got = self.s.table("big.csv", data)
        self.assertEqual(validate(got), [])
        self.assertEqual(got.first_rule(), "limit.line-too-long")
        self.assertEqual(got.status, 8)
        self.assertNotIn("data", got.doc())
        detail = got.error()["detail"]
        self.assertEqual((detail["first_line"], detail["longest"], detail["limit"]), (2, (1 << 20) + 4, 1 << 20))
        # The repair raises the cap to the longest line and then the file reads.
        repair = got.error()["repair"]
        self.assertEqual(repair["kind"], "retry")
        again = run_argv(repair["argv"])
        self.assertEqual(again.status, 0)
        self.assertEqual(again.data()["rows"], 2)

    def test_the_ceiling(self):
        got = self.s.table("c.csv", b"a\n1\n", "--max-line-bytes", "16777217")
        self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"))
        got = self.s.table("c.csv", b"a\n1\n", "--max-rows", "1000000001")
        self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"))
        got = self.s.table("c.csv", b"a\n1\n", "--max-line-bytes", "16777216")
        self.assertEqual(got.status, 0)

    def test_the_header_is_bounded_too(self):
        # Each line is short; the header record, one quoted name over many
        # lines, is not allowed to grow without end.
        data = b'"' + b"abcdefgh\n" * 1000 + b'",c\n1,2\n'
        got = self.s.table("h.csv", data, "--max-line-bytes", "1000")
        self.assertEqual(validate(got), [])
        self.assertEqual((got.status, got.first_rule()), (8, "limit.header-too-large"))
        self.assertEqual(got.error()["detail"]["line"], 1)

    def test_data_rows_may_span_any_number_of_lines(self):
        # A quoted field of 200,000 lines of 100 bytes: 20 MB in one record,
        # each line far under the cap, and nothing of it kept.
        data = b'a,b\n1,"' + (b"y" * 99 + b"\n") * 200000 + b'"\n2,3\n'
        got = self.s.table("span.csv", data)
        self.assertEqual(validate(got), [])
        self.assertEqual((got.data()["rows"], got.data()["columns"]), (2, 2))

    def test_max_rows_truncates(self):
        data = "a,b\n" + "".join("%d,x\n" % i for i in range(1000))
        for most, rows, truncated in ((0, 0, True), (1, 1, True), (999, 999, True), (1000, 1000, False), (1001, 1000, False)):
            with self.subTest(most):
                got = self.s.table("m.csv", data, "--max-rows", most)
                self.assertEqual(validate(got), [])
                self.assertEqual(got.status, 0)
                self.assertEqual((got.data()["rows"], got.data()["truncated"]), (rows, truncated))

    def test_max_rows_zero_on_a_header_only_file_is_not_truncated(self):
        got = self.s.table("m.csv", "a,b\n", "--max-rows", 0)
        self.assertEqual((got.data()["rows"], got.data()["truncated"]), (0, False))

    def test_the_read_stops_at_max_rows(self):
        # Beyond the bound nothing is read: a bad quote after it is not seen.
        got = self.s.table("m.csv", 'a,b\n1,2\n"x"y,1\n', "--max-rows", 1)
        self.assertEqual(got.status, 0)
        self.assertTrue(got.data()["truncated"])

    def test_ragged_reports_the_first_and_counts_all(self):
        got = self.s.table("r.csv", 'a,b\n1,2\n"x\ny"\n3,4\n5,6,7\n')
        err = got.error()
        self.assertEqual(err["rule"], "parse.csv-ragged-row")
        self.assertEqual(err["detail"], {"path": "r.csv", "ragged_rows": 2, "first_row": 2, "first_line": 3, "expected": 2, "found": 1})
        self.assertEqual(got.data()["rows"], 4)

    def test_delimiters(self):
        for flag, data in (("tab", "a\tb\n1\t2\n"), ("\t", "a\tb\n1\t2\n"), (";", "a;b\n1;2\n"), (",", "a,b\n1,2\n")):
            with self.subTest(flag):
                got = self.s.table("d.csv", data, "--delimiter", flag)
                self.assertEqual(got.data()["headers"], ["a", "b"])
        got = self.s.table("d.csv", "a;b\n1;2\n")
        self.assertEqual(got.data()["columns"], 1)
        got = self.s.table("d.csv", "a|b\n", "--delimiter", "|")
        self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"))

    def test_a_lone_cr_is_data(self):
        # Python's csv ends a record at a CR; this reader ends it at an LF
        # (a CR before it is dropped), so a CR alone is a byte of a field.
        got = self.s.table("cr.csv", b"a,b\r1,2\r")
        self.assertEqual(validate(got), [])
        self.assertEqual((got.data()["rows"], got.data()["columns"]), (0, 3))
        self.assertEqual([name_text(n) for n in got.data()["headers"]], [b"a", b"b\r1", b"2"])

    def test_text_format(self):
        got = self.s.table("t.csv", 'id,"na,me"\n1,x\n', "--format", "text")
        self.assertEqual(got.status, 0)
        self.assertEqual(got.stdout, b"rows\t1\ncolumns\t2\ntruncated\tfalse\nheader\tid\tna,me\n")
        got = self.s.table("t.csv", "a,b\n1\n", "--format", "text")
        self.assertEqual(got.status, 8)
        self.assertEqual(got.stdout, b"")
        self.assertIn(b"parse.csv-ragged-row", got.stderr)

    def test_deterministic(self):
        self.s.write("det.csv", 'a,"b c"\r\n1,"x\r\ny"\r\n2,3\r\n')
        outs = {run("--root", self.s.dir, "det.csv").stdout for _ in range(5)}
        self.assertEqual(len(outs), 1)

    def test_the_answer_is_a_document_against_its_schema(self):
        cases = [b"", b"a\n", b"a,b\n1,2\n", b'a\n"x', b"a,b\n1\n", b"\xef\xbb\xbfa\n1\n", b"caf\xe9\n1\n"]
        for data in cases:
            with self.subTest(data):
                self.assertEqual(validate(self.s.table("s.csv", data)), [])

    def test_never_a_trap(self):
        import random
        rng = random.Random(3)
        for i in range(300):
            data = bytes(rng.choice(b'ab,;\t"\r\n\x00\xff ') for _ in range(rng.randint(0, 60)))
            got = self.s.table("z.csv", data)
            self.assertEqual(validate(got), [], data)
            got = self.s.table("z.csv", data, "--delimiter", "tab", "--max-line-bytes", "7", "--max-rows", "2")
            self.assertEqual(validate(got), [], data)


if __name__ == "__main__":
    unittest.main()
