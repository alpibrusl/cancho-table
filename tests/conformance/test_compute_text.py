"""docs/functions.md section 3.3 (slice K6): the text functions of `--compute` through the built binary, against `scripts/ctext_ref.py` (Python's `bytes` methods, no code shared with the tool).

* **G-TX-ORACLE** hand cases for every function and every refusal, and random definitions over random texts (ASCII, 2 to 4 byte characters, quotes, commas, line feeds, CRs, the
  empty text, bytes that are not UTF-8): every cell of the answer is the reference's, and the refusal of a row is the reference's rule at the reference's row;
* **G-TX-WRITE** the answer is CSV that a CSV reader reads back to the same fields, whatever the text holds (a delimiter, a quote, a line feed), and JSON that reads back to the same strings;
* the rules, the explain (`kind: text`, no scale; the refusals a text definition can give) and the limit of a row (`--max-line-bytes`).
"""
import csv
import io
import json
import os
import pathlib
import random
import re
import sys
import unittest

here = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(here))
sys.path.insert(0, str(here.parents[1] / "scripts"))
import ctext_ref as R  # noqa: E402
from harness import Scratch, run  # noqa: E402

CASES = int(os.environ.get("TABLE_TEXT_CASES", "400"))
HEADER = ["a", "b", "c", "d"]


def csv_bytes(header, rows):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([c.decode("utf-8", "surrogateescape") for c in r])
    return buf.getvalue().encode("utf-8", "surrogateescape")


def parse_csv(data):
    text = data.decode("utf-8", "surrogateescape")
    return [[f.encode("utf-8", "surrogateescape") for f in row] for row in csv.reader(io.StringIO(text, newline=""))]


class Text(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def stage(self, header, rows, defs, *extra, fmt="csv"):
        self.s.write("f.csv", csv_bytes(header, rows))
        return run("--root", self.s.dir, "--compute", defs, "--format", fmt, *extra, "f.csv")

    def rule_of(self, r):
        m = re.match(rb"table: ([a-z.-]+): ", r.raw_stderr)
        return m.group(1).decode() if m else None

    def row_of(self, r):
        m = re.search(rb'"row":(\d+)', r.raw_stderr)
        return int(m.group(1)) if m else None

    def check(self, header, rows, defs, want, label=""):
        r = self.stage(header, rows, defs)
        if isinstance(want, tuple) and want[0] == "C":
            self.assertNotEqual(r.status, 0, (label, defs))
            self.assertEqual(self.rule_of(r), want[1], (label, defs, r.raw_stderr[:200]))
            self.assertEqual(r.stdout, b"", (label, defs))
            return
        got = parse_csv(r.stdout)
        if isinstance(want, tuple) and want[0] == "E":
            self.assertNotEqual(r.status, 0, (label, defs))
            self.assertEqual((self.rule_of(r), self.row_of(r)), (want[1], want[2]), (label, defs, r.raw_stderr[:200]))
            self.assertEqual(len(got) - 1, want[2] - 1, (label, defs))
            return
        self.assertEqual(r.status, 0, (label, defs, r.raw_stderr[:200]))
        self.assertEqual(len(got), len(rows) + 1, (label, defs))
        self.assertEqual(got[0][:len(header)], [h.encode() for h in header])
        for row, out, add in zip(rows, got[1:], want):
            self.assertEqual(out[:len(header)], row, (label, defs))
            self.assertEqual(out[len(header):], add, (label, defs))

    # G-TX-ORACLE: the hand cases
    def test_the_hand_cases(self):
        for entry in R.HAND + [(a, b, c) for a, b, c in R.LEN]:
            rows, defs, want = entry[:3]
            header = entry[3] if len(entry) > 3 else HEADER
            self.check(header, rows, defs, want, "hand")

    # G-TX-ORACLE: random definitions over random texts
    def test_random_definitions(self):
        rng = random.Random(int(os.environ.get("TABLE_TEXT_SEED", "11")))
        refused = 0
        for n in range(CASES):
            defs, trees = [], []
            for d in range(rng.randint(1, 3)):
                for _ in range(30):
                    t = R.gen_call(rng, rng.randint(1, 3))
                    w = R.render(t, rng)
                    if len(w) < 180:
                        break
                trees.append(t)
                defs.append(f"d{d}=" + w)
            rows = R.rows_of(rng, rng.randint(1, 8))
            want, bad = [], None
            for ri, row in enumerate(rows, 1):
                try:
                    want.append([R.ev(t, row) for t in trees])
                except R.RowRefusal as e:
                    bad = (e.rule, ri)
                    break
            text = ";".join(defs)
            if bad:
                refused += 1
                self.check(R.NAMES, rows, text, ("E", bad[0], bad[1]), "random %d" % n)
            else:
                self.check(R.NAMES, rows, text, want, "random %d" % n)
        self.assertGreater(refused, 0)

    # G-TX-WRITE: what a CSV reader and a JSON reader read back
    def test_the_awkward_texts_are_quoted_and_read_back(self):
        awk = [b"a,b", b'q"uote', b"line\nfeed", b"cr\rhere", b"", b" lead", b'"', b'","', b"\xc3\xa9"]
        rows = [[x, b"-", b"", b""] for x in awk]
        r = self.stage(HEADER, rows, 'u=concat(a, b);v=lower(a);w=concat(a, a)')
        self.assertEqual(r.status, 0, r.raw_stderr[:200])
        got = parse_csv(r.stdout)
        self.assertEqual(len(got), len(awk) + 1)
        for x, out in zip(awk, got[1:]):
            self.assertEqual(out[4:], [x + b"-", x.lower(), x + x], x)

    def test_json_reads_back_to_the_same_strings(self):
        rows = [[b"Hello, World", b'say "hi"', b"line\nfeed", b"\xc3\x89t\xc3\xa9"]]
        r = self.stage(HEADER, rows, 'u=upper(a);v=concat(b, c);w=lower(d)', fmt="json")
        self.assertEqual(r.status, 0, r.raw_stderr[:200])
        data = r.data()
        self.assertEqual(data["columns"], HEADER + ["u", "v", "w"])
        # lower() changes the ASCII letters only: the accented capital stays a capital
        self.assertEqual(data["rows"][0][4:], ["HELLO, WORLD", 'say "hi"line\nfeed', "\u00c9t\u00e9"])

    # the limit of a row
    def test_a_result_past_the_line_limit_is_refused_and_one_inside_it_is_not(self):
        rows = [[b"x" * 10, b"", b"", b""]]
        r = self.stage(HEADER, rows, 'u=replace(a, "x", "xxxxxxxxxx")', "--max-line-bytes", "64")
        self.assertEqual((self.rule_of(r), self.row_of(r)), ("compute.result-too-long", 1), r.raw_stderr[:200])
        r = self.stage(HEADER, rows, 'u=lower(a)', "--max-line-bytes", "64")
        self.assertEqual(r.status, 0, r.raw_stderr[:200])

    # a function name stays a column name, and the chain
    def test_a_name_that_is_a_function_is_a_column(self):
        r = self.stage(["lower", "upper", "c", "d"], [[b"A", b"B", b"", b""]], 'u=concat(lower, upper)')
        self.assertEqual(parse_csv(r.stdout)[1][4:], [b"AB"], r.raw_stderr[:200])

    # the explain
    def test_the_explain_of_a_text_stage(self):
        self.s.write("f.csv", csv_bytes(HEADER, [[b"x", b"y", b"z", b"w"]]))
        e = run("--root", self.s.dir, "--compute", "u=upper(a);n=len(a)+1;v=substr(b, 1, 2)", "--explain", "f.csv")
        self.assertEqual(e.status, 0, e)
        x = e.data()["explain"]
        defs = x["stage"]["definitions"]
        self.assertEqual([(d["name"], d.get("kind"), d.get("scale")) for d in defs], [("u", "text", None), ("n", None, 0), ("v", "text", None)])
        possible = x["refusals"]["possible"]
        self.assertIn("compute.result-too-long", possible)
        self.assertIn("compute.not-utf8-boundary", possible)
        e = run("--root", self.s.dir, "--compute", "u=upper(a)", "--explain", "f.csv")
        possible = e.data()["explain"]["refusals"]["possible"]
        self.assertIn("compute.result-too-long", possible)
        self.assertNotIn("compute.not-utf8-boundary", possible)
        e = run("--root", self.s.dir, "--compute", "n=len(a)+1", "--explain", "f.csv")
        possible = e.data()["explain"]["refusals"]["possible"]
        self.assertNotIn("compute.result-too-long", possible)
        self.assertNotIn("compute.not-utf8-boundary", possible)

    def test_the_explain_refuses_what_the_run_refuses(self):
        self.s.write("f.csv", csv_bytes(HEADER, [[b"x", b"y", b"z", b"w"]]))
        for defs in ("u=lower(a:int)", "u=concat(a)", "u=substr(a, 0, 1)", "u=nope(a)", "u=lower(a)+1", "n=len(lower(a))"):
            e = run("--root", self.s.dir, "--compute", defs, "--explain", "f.csv")
            r = run("--root", self.s.dir, "--compute", defs, "f.csv")
            self.assertNotEqual(r.status, 0, defs)
            self.assertEqual((e.status, e.doc().get("error")), (r.status, r.doc().get("error")), defs)

    # the new rules, in the order of the table
    def test_the_new_rules_have_their_exit_status(self):
        r = self.stage(HEADER, [[b"x", b"y", b"z", b"w"]], "u=lower(a:int)")
        self.assertEqual((self.rule_of(r), r.status), ("compute.type-mismatch", 2))
        r = self.stage(HEADER, [[b"\xc3\xa9", b"", b"", b""]], "u=substr(a, 2, 1)")
        self.assertEqual((self.rule_of(r), r.status), ("compute.not-utf8-boundary", 8))
        r = self.stage(HEADER, [[b"x" * 10, b"", b"", b""]], 'u=replace(a, "x", "xxxxxxxxxx")', "--max-line-bytes", "64")
        self.assertEqual((self.rule_of(r), r.status), ("compute.result-too-long", 8))

    def test_json_lines_take_the_text_functions_too(self):
        self.s.write("j.jsonl", '{"a":"Hello","b":"x,y"}\n')
        r = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", "a,b", "--compute", "u=upper(a);v=concat(a, b)", "--format", "csv", "j.jsonl")
        self.assertEqual(r.status, 0, r.raw_stderr[:200])
        self.assertEqual(parse_csv(r.stdout), [[b"a", b"b", b"u", b"v"], [b"Hello", b"x,y", b"HELLO", b"Hellox,y"]])

    def test_standard_input_takes_them_too(self):
        r = run("--compute", "u=upper(a)", "--format", "csv", stdin=csv_bytes(["a"], [[b"hi"]]))
        self.assertEqual(r.status, 0, r.raw_stderr[:200])
        self.assertEqual(parse_csv(r.stdout), [[b"a", b"u"], [b"hi", b"HI"]])


if __name__ == "__main__":
    unittest.main()
