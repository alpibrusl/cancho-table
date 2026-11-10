"""`--skip-lines` and `--skip-prefix` (docs/reader-options.md, O1, issue #109): the lines above the header that an export puts there.

The gates, written before the code and each able to fail:

* O, the oracle: a file with a preamble, read with the options, answers as the same table without it, for the shape, rows, groups, sorted rows, the report, csv and JSON lines out, standard input, a parallel read and both files of a join;
  and the answer says how many lines were dropped, counted by the test from the preamble it wrote.
* S, the rules: a quote in the preamble opens nothing, a blank line counts among the first N, a prefix is looked at before the header only, a line after the header that starts with it is a row, a byte order mark is the file's,
  a preamble that is the whole file is an empty file, JSON lines refuse the options, no option is no `skipped` in the answer, `--explain` shows the options and honours them.
"""

import csv
import io
import json
import random
import unittest

from harness import Scratch, run

HEADER = ["id", "k", "v"]


def encode(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


def rows_of(rng, n):
    return [[str(i), rng.choice(["a", "b", "c", "", "x,y", 'say "hi"']), str(rng.randrange(100))] for i in range(n)]


PREAMBLE_LINES = ["Quarterly export", "", 'He said "hello', "generated 2026-11-10", "# comment one", "#comment two", "   ", ",,,", "Total,0,0"]


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.root = ["--root", str(self.s.dir)]

    def tearDown(self):
        self.s.cleanup()

    def put(self, name, header, rows, preamble=()):
        self.s.write(name, "".join(l + "\n" for l in preamble) + encode(header, rows))

    def data(self, r):
        self.assertEqual(r.status, 0, (r.stdout[:300], r.stderr[:300]))
        d = r.data()
        return {k: v for k, v in d.items() if k != "skipped"}, d.get("skipped")


FORMS = [
    ("shape", []),
    ("rows", ["--select", "id,k"]),
    ("rows where", ["--select", "id,v", "--where", "v:int > 50"]),
    ("groups", ["--group", "k", "--agg", "count,sum:v:int", "--sort", "-count"]),
    ("sorted", ["--order-by", "-v:int,id:int", "--select", "id,v", "--limit", "30"]),
    ("report", ["--report", "types"]),
    ("page", ["--select", "id", "--limit", "5", "--from", "3"]),
]


class Oracle(Base):
    def test_the_options_read_the_file_as_the_clean_one(self):
        rng = random.Random(1601)
        for case in range(20):
            rows = rows_of(rng, rng.randrange(0, 40))
            pre = [rng.choice(PREAMBLE_LINES) for _ in range(rng.randrange(0, 6))]
            self.put("dirty.csv", HEADER, rows, pre)
            self.put("clean.csv", HEADER, rows)
            for label, form in FORMS:
                if not form and not rows:
                    pass
                lines = len(pre)
                dirty = run(*self.root, "--skip-lines", str(lines), *form, "dirty.csv")
                clean = run(*self.root, *form, "clean.csv")
                self.assertEqual(dirty.status, clean.status, (case, label, dirty.stdout[:200]))
                if dirty.status == 0:
                    d, skipped = self.data(dirty)
                    c, none = self.data(clean)
                    if label == "report":
                        # a line number is the file's, preamble included: the rows say the same and the lines are the clean ones moved down
                        keep = [i for i, n in enumerate(d["columns"]) if not n.endswith("_line")]
                        moved = [i for i, n in enumerate(d["columns"]) if n.endswith("_line")]
                        self.assertEqual([[r[i] for i in keep] for r in d["rows"]], [[r[i] for i in keep] for r in c["rows"]], (case, label))
                        for rd, rc in zip(d["rows"], c["rows"]):
                            for i in moved:
                                self.assertEqual(rd[i], "" if rc[i] == "" else str(int(rc[i]) + lines), (case, label, d["columns"][i]))
                    else:
                        self.assertEqual(d, c, (case, label))
                    self.assertEqual(skipped, {"lines": lines}, (case, label))
                    self.assertIsNone(none)

    def test_a_prefix_drops_the_lines_that_start_with_it_before_the_header(self):
        rows = [["1", "a", "5"], ["2", "#hash", "6"], ["3", "b", "7"]]
        self.put("dirty.csv", HEADER, rows, ["# a", "#b", "# c"])
        self.put("clean.csv", HEADER, rows)
        d, skipped = self.data(run(*self.root, "--skip-prefix", "#", "--select", "id,k", "dirty.csv"))
        c, _ = self.data(run(*self.root, "--select", "id,k", "clean.csv"))
        self.assertEqual((d, skipped), (c, {"lines": 3}))
        self.assertEqual(d["rows"][1], ["2", "#hash"])

    def test_both_options_together_and_the_equals_spelling(self):
        self.put("dirty.csv", HEADER, [["1", "a", "5"]], ["Title", "# note", "# note2"])
        for a in (["--skip-lines", "1", "--skip-prefix", "#"], ["--skip-lines=1", "--skip-prefix=#"], ["--skip-prefix", "#", "--skip-lines", "1"]):
            with self.subTest(a):
                d, skipped = self.data(run(*self.root, *a, "--select", "id", "dirty.csv"))
                self.assertEqual((d["rows"], skipped), ([["1"]], {"lines": 3}))

    def test_csv_and_json_lines_out_carry_no_count_and_the_same_rows(self):
        rows = [["1", "a", "5"], ["2", "b", "6"]]
        self.put("dirty.csv", HEADER, rows, ["x", "y"])
        self.put("clean.csv", HEADER, rows)
        for fmt in ("csv", "jsonl"):
            a = run(*self.root, "--skip-lines", "2", "--select", "id,k", "--format", fmt, "dirty.csv").stdout
            b = run(*self.root, "--select", "id,k", "--format", fmt, "clean.csv").stdout
            self.assertEqual(a, b, fmt)

    def test_standard_input_and_a_parallel_read(self):
        rng = random.Random(7)
        rows = [[str(i), "k%d" % (i % 31), str(rng.randrange(1000))] for i in range(5000)]
        pre = ["Title block", "", "# generated"]
        self.put("dirty.csv", HEADER, rows, pre)
        self.put("clean.csv", HEADER, rows)
        form = ["--group", "k", "--agg", "count,sum:v:int", "--sort", "-count", "--format", "csv"]
        want = run(*self.root, *form, "clean.csv").stdout
        for threads in ("1", "4"):
            got = run(*self.root, "--skip-lines", "3", "--threads", threads, "--parallel-min-bytes", "1", "--chunk-bytes", "4096", *form, "dirty.csv").stdout
            self.assertEqual(got, want, threads)
        text = (self.s.dir / "dirty.csv").read_bytes()
        self.assertEqual(run("--skip-lines", "3", *form, stdin=text).stdout, want)
        self.assertEqual(run(*self.root, "--skip-prefix", "#", "--skip-lines", "2", *form, "dirty.csv").stdout, want)
        rows_form = ["--select", "id,v", "--order-by", "-v:int,id:int", "--format", "csv"]
        want2 = run(*self.root, *rows_form, "clean.csv").stdout
        for threads in ("1", "4"):
            got = run(*self.root, "--skip-lines", "3", "--threads", threads, "--parallel-min-bytes", "1", "--chunk-bytes", "4096", *rows_form, "dirty.csv").stdout
            self.assertEqual(got, want2, threads)

    def test_both_files_of_a_join_and_their_counts(self):
        self.put("p.csv", ["id", "name"], [["1", "ann"], ["2", "bob"], ["3", "cy"]], ["Customers", "# x"])
        self.put("b.csv", ["id", "city"], [["1", "Oslo"], ["3", "Rome"]], ["Cities", "# y", "# z"])
        self.put("pc.csv", ["id", "name"], [["1", "ann"], ["2", "bob"], ["3", "cy"]])
        self.put("bc.csv", ["id", "city"], [["1", "Oslo"], ["3", "Rome"]])
        for jt in ("inner", "left", "full", "semi"):
            with self.subTest(jt):
                a = ["--join-type", jt, "--on", "id"] + ([] if jt == "semi" else ["--join-prefix", "r_"])
                d, skipped = self.data(run(*self.root, "--skip-lines", "1", "--skip-prefix", "#", "--join", "b.csv", *a, "p.csv"))
                c, _ = self.data(run(*self.root, "--join", "bc.csv", *a, "pc.csv"))
                self.assertEqual(d, c)
                self.assertEqual(skipped, {"lines": 2, "join_lines": 3})
        g, skipped = self.data(run(*self.root, "--skip-lines", "1", "--skip-prefix", "#", "--join", "b.csv", "--on", "id", "--join-prefix", "r_", "--group", "r_city", "--agg", "count", "p.csv"))
        self.assertEqual(skipped, {"lines": 2, "join_lines": 3})


class Rules(Base):
    def test_a_quote_in_the_preamble_opens_nothing_and_a_blank_line_counts(self):
        self.s.write("q.csv", 'He said "hello\n\n  \n' + encode(HEADER, [["1", "a", "5"]]))
        d, skipped = self.data(run(*self.root, "--skip-lines", "3", "--select", "id", "q.csv"))
        self.assertEqual((d["rows"], skipped), ([["1"]], {"lines": 3}))
        # without the option the open quote is the file's problem, as it was
        self.assertNotEqual(run(*self.root, "--select", "id", "q.csv").status, 0)

    def test_blank_lines_before_the_header_that_no_option_drops_are_not_counted(self):
        self.s.write("b.csv", "\n\nid,k\n1,a\n")
        d, skipped = self.data(run(*self.root, "--skip-prefix", "#", "--select", "id", "b.csv"))
        self.assertEqual((d["rows"], skipped), ([["1"]], {"lines": 0}))

    def test_a_prefix_line_after_the_header_is_a_row(self):
        self.s.write("r.csv", "id,k\n#1,a\n2,b\n")
        d, skipped = self.data(run(*self.root, "--skip-prefix", "#", "--select", "id", "r.csv"))
        self.assertEqual((d["rows"], skipped), ([["#1"], ["2"]], {"lines": 0}))

    def test_a_header_that_spans_lines_is_not_cut_by_a_prefix(self):
        self.s.write("h.csv", '# note\n"id\n# not a comment",k\n1,a\n')
        d, skipped = self.data(run(*self.root, "--skip-prefix", "#", "--select", "#2", "h.csv"))
        self.assertEqual((d["rows"], skipped), ([["a"]], {"lines": 1}))

    def test_a_join_file_with_no_preamble_counts_zero(self):
        # (the options apply to both files, section 8 of docs/reader-options.md: a prefix is the case where a file without a preamble is untouched)
        self.put("p.csv", ["id", "name"], [["1", "ann"]], ["# T"])
        self.put("b.csv", ["id", "city"], [["1", "Oslo"]])
        d, skipped = self.data(run(*self.root, "--skip-prefix", "#", "--join", "b.csv", "--on", "id", "--join-prefix", "r_", "p.csv"))
        self.assertEqual(skipped, {"lines": 1, "join_lines": 0})
        self.assertEqual(d["row_count"], 1)

    def test_a_number_of_lines_is_dropped_from_both_files_of_a_join(self):
        self.put("p.csv", ["id", "name"], [["1", "ann"]], ["T"])
        self.put("b.csv", ["id", "city"], [["1", "Oslo"]])
        r = run(*self.root, "--skip-lines", "1", "--join", "b.csv", "--on", "id", "--join-prefix", "r_", "p.csv")
        self.assertEqual(r.status, 3)      # the --join file has no title line, so its header went with the first line: that is what "both files alike" means

    def test_a_json_lines_join_file_refuses_the_options(self):
        self.put("p.csv", ["id"], [["1"]], ["T"])
        self.s.write("b.jsonl", '{"id":1}\n')
        r = run(*self.root, "--skip-lines", "1", "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "id", "--on", "id", "p.csv")
        self.assertEqual((r.status, r.error()["rule"]), (2, "args.conflict"))

    def test_a_byte_order_mark_is_the_files_and_the_prefix_sees_past_it(self):
        self.s.write("bom.csv", b"\xef\xbb\xbf# c\nid,k\n1,a\n")
        d, skipped = self.data(run(*self.root, "--skip-prefix", "#", "--select", "id", "bom.csv"))
        self.assertEqual((d["rows"], skipped), ([["1"]], {"lines": 1}))
        self.s.write("bom2.csv", b"\xef\xbb\xbfT\nid,k\n1,a\n")
        d, skipped = self.data(run(*self.root, "--skip-lines", "1", "--select", "id", "bom2.csv"))
        self.assertEqual((d["rows"], skipped), ([["1"]], {"lines": 1}))

    def test_a_preamble_that_is_the_whole_file_is_an_empty_file(self):
        self.s.write("w.csv", "a\nb\nc\n")
        d, skipped = self.data(run(*self.root, "--skip-lines", "10", "w.csv"))
        self.assertEqual((d["headers"], d["row_count"], skipped), ([], 0, {"lines": 3}))
        r = run(*self.root, "--skip-lines", "10", "--select", "a", "w.csv")
        self.assertEqual(r.status, 3)

    def test_json_lines_refuse_the_options(self):
        self.s.write("j.jsonl", '{"a":1}\n')
        for opt in (["--skip-lines", "1"], ["--skip-prefix", "#"]):
            r = run(*self.root, "--input-format", "jsonl", "--fields", "a", *opt, "--select", "a", "j.jsonl")
            self.assertEqual(r.status, 2, opt)
            self.assertEqual(r.error()["rule"], "args.conflict")

    def test_no_option_no_count(self):
        self.put("c.csv", HEADER, [["1", "a", "5"]])
        d = run(*self.root, "--select", "id", "c.csv").data()
        self.assertNotIn("skipped", d)
        d = run(*self.root, "c.csv").data()
        self.assertNotIn("skipped", d)
        d = self.data(run(*self.root, "--skip-lines", "0", "--select", "id", "c.csv"))
        self.assertEqual(d[1], {"lines": 0})

    def test_a_value_that_is_not_a_number_or_is_empty_is_refused(self):
        self.put("c.csv", HEADER, [["1", "a", "5"]])
        self.assertEqual(run(*self.root, "--skip-lines", "x", "c.csv").status, 2)
        self.assertEqual(run(*self.root, "--skip-lines", "-1", "c.csv").status, 2)
        self.assertEqual(run(*self.root, "--skip-prefix", "", "c.csv").status, 2)

    def test_explain_shows_the_options_and_reads_the_header_past_them(self):
        self.put("e.csv", HEADER, [["1", "a", "5"]], ["Title", "# n"])
        r = run(*self.root, "--explain", "--skip-lines", "1", "--skip-prefix", "#", "--select", "id", "e.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        src = r.data()["explain"]["source"]
        self.assertEqual((src["skip_lines"], src["skip_prefix"], src["columns"]), (1, "#", 3))
        none = run(*self.root, "--explain", "--select", "k", "e.csv")
        self.assertNotEqual(none.status, 0)
        plain = run(*self.root, "--explain", "--select", "id", "e.csv")
        self.assertNotIn("skip_lines", plain.data()["explain"]["source"] if plain.status == 0 else {})

    def test_explain_of_a_join_reads_both_headers_past_the_options(self):
        self.put("p.csv", ["id", "name"], [["1", "ann"]], ["T"])
        self.put("b.csv", ["id", "city"], [["1", "Oslo"]], ["T"])
        r = run(*self.root, "--explain", "--skip-lines", "1", "--join", "b.csv", "--on", "id", "--join-prefix", "r_", "p.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])


if __name__ == "__main__":
    unittest.main()
