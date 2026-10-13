"""docs/readers.md section 12, N17 -- a JSON string may hold the delimiter, an LF, a CR or a quote, which an unquoted CSV field may not. Whatever the string holds, `--format csv` writes a
field Python's csv module reads back as that string, with every delimiter, and `--where` and `--order-by` compare it as the text it is."""

import csv
import io
import json
import unittest

from harness import Scratch, run

TEXTS = ["plain", "a,b", "l1\nl2", "q\"z", 'a,"b', "x;y", "tab\there", "cr\rlf", "\n", ",", "\"", "end,", "é,ü", "", " ", "a\r\nb", ",\n\"", "\"\"", "x\\y,z"]


def jsonl(rows, escape):
    out = []
    for r in rows:
        out.append(json.dumps({"k": r[0], "v": r[1], "n": 1}, ensure_ascii=escape, separators=(",", ":")))
    return ("\n".join(out) + "\n").encode("utf-8")


class CsvOut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def csv_rows(self, out, delim):
        return list(csv.reader(io.StringIO(out.decode("utf-8"), newline=""), delimiter=delim, strict=True))

    def test_every_text_survives_csv_out_with_every_delimiter(self):
        rows = [(str(i), t) for i, t in enumerate(TEXTS)]
        for escape in (False, True):
            for delim_flag, delim in ((",", ","), (";", ";"), ("tab", "\t")):
                for fields in ("k,v", "v,k", "k,v,n"):
                    # json.dumps writes a newline as \n (a decoded string), a quote as \" and non-ASCII as itself or as \uXXXX
                    self.s.write("t.jsonl", jsonl(rows, escape))
                    names = fields.split(",")
                    got = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", fields, "--delimiter", delim_flag, "--select", ",".join(names), "--format", "csv", "t.jsonl")
                    with self.subTest(escape=escape, delim=delim_flag, fields=fields):
                        self.assertEqual(got.status, 0, got)
                        want = [names] + [[{"k": r[0], "v": r[1], "n": "1"}[n] for n in names] for r in rows]
                        self.assertEqual(self.csv_rows(got.stdout, delim), want)

    def test_a_condition_and_a_sort_compare_the_text_not_the_quoting(self):
        rows = [(str(i), t) for i, t in enumerate(TEXTS)]
        self.s.write("t.jsonl", jsonl(rows, False))
        for t in TEXTS:
            lit = "'" + t.replace("'", "''") + "'"
            for fmt in ([], ["--format", "csv"]):
                got = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", "k,v", "--where", "v = " + lit, "--select", "k", *fmt, "t.jsonl")
                with self.subTest(text=t, fmt=fmt):
                    self.assertEqual(got.status, 0, got)
                    want = [[r[0]] for r in rows if r[1] == t]
                    if fmt:
                        self.assertEqual(self.csv_rows(got.stdout, ",")[1:], want)
                    else:
                        self.assertEqual(got.data()["rows"], want)
        for fmt in ([], ["--format", "csv"]):
            got = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", "k,v", "--order-by", "v,k", "--select", "v,k", *fmt, "t.jsonl")
            want = sorted([[r[1], r[0]] for r in rows], key=lambda x: (x[0].encode("utf-8"), x[1].encode("utf-8")))
            with self.subTest(order=fmt):
                self.assertEqual(got.status, 0, got)
                self.assertEqual(self.csv_rows(got.stdout, ",")[1:] if fmt else got.data()["rows"], want)
        # a group key and a distinct count
        got = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", "k,v", "--group", "v", "--format", "csv", "t.jsonl")
        self.assertEqual(sorted(r[0] for r in self.csv_rows(got.stdout, ",")[1:]), sorted(set(TEXTS)))


if __name__ == "__main__":
    unittest.main()
