"""Differential: `table` against Python's csv module.

Python's csv.reader (strict=True, newline='') is the reference for what a
record is. For every fixture the reference gives the records; the expected
answer is the first record as the header, the others as rows, and an error
when a row has another width (ragged) or the reader raises (a closing quote
followed by text, or a quote left open). A blank line is a record of no fields
to Python and is not a record here; the reference drops them.

The fixtures are generated: quoted newlines (LF and CRLF), BOM, CRLF line ends,
empty fields, doubled quotes, delimiters inside quotes, ragged rows, a quote
left open, no rows at all, a header only, and several thousand random tables
written by csv.writer and then damaged.
"""

import csv
import io
import random
import re
import unittest

from harness import Scratch, name_text, run, validate

# The reference has a bound of its own (131072 characters a field); lifted so it
# cannot be what disagrees.
csv.field_size_limit(1 << 30)


def reference(data, delimiter):
    """(headers, rows) from csv.reader, 'ragged' or 'quote' for an error."""
    # Bytes as Latin-1 text: one character per byte, so a file cut in the middle
    # of a UTF-8 sequence is still a file, and a name compares as bytes.
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("latin-1")
    try:
        records = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True) if r]
    except csv.Error as e:
        return "unterminated" if "end of data" in str(e) else "quote"
    if not records:
        return ([], 0, 0)
    header = records[0]
    rows = records[1:]
    if any(len(r) != len(header) for r in rows):
        return "ragged"
    return (header, len(header), len(rows))


FLAG = {",": [], "\t": ["--delimiter", "tab"], ";": ["--delimiter", ";"]}
RULE = {"ragged": "parse.csv-ragged-row", "quote": "parse.csv-bad-quote", "unterminated": "parse.csv-unterminated-quote"}


def write_csv(rows, delimiter=",", lineterminator="\n", quoting=csv.QUOTE_MINIMAL, bom=False):
    out = io.StringIO(newline="")
    w = csv.writer(out, delimiter=delimiter, lineterminator=lineterminator, quoting=quoting)
    for r in rows:
        w.writerow(r)
    data = out.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


class Differential(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def agree(self, data, delimiter=",", label=""):
        expected = reference(data, delimiter)
        got = self.scratch.table("f.csv", data, *FLAG[delimiter])
        self.assertEqual(validate(got), [], "%s %r" % (label, data[:200]))
        doc = got.doc()
        if isinstance(expected, str):
            self.assertEqual(got.first_rule(), RULE[expected], "%s %r -> %r" % (label, data[:200], doc))
            if expected == "ragged":
                # The read ran to the end, so the shape is still answered.
                self.assertIn("data", doc)
            else:
                self.assertNotIn("data", doc)
            return
        header, columns, rows = expected
        self.assertEqual(got.status, 0, "%s %r -> %r" % (label, data[:200], doc))
        d = doc["data"]
        self.assertEqual([name_text(n) for n in d["headers"]], [h.encode("latin-1") for h in header], label)
        self.assertEqual((d["columns"], d["rows"], d["truncated"]), (columns, rows, False), label)

    def test_hand_picked(self):
        cases = {
            "plain": b"a,b,c\n1,2,3\n4,5,6\n",
            "no final newline": b"a,b\n1,2",
            "crlf": b"a,b\r\n1,2\r\n3,4\r\n",
            "bom": b"\xef\xbb\xbfa,b\n1,2\n",
            "bom crlf quoted header": b"\xef\xbb\xbf\"a\",\"b c\"\r\n1,2\r\n",
            "quoted newline lf": b'a,b\n1,"x\ny"\n2,3\n',
            "quoted newline crlf": b'a,b\r\n1,"x\r\ny"\r\n2,3\r\n',
            "many quoted newlines": b'a,b\n1,"' + b"line\n" * 500 + b'"\n2,3\n',
            "quoted header newline": b'"a\nb",c\n1,2\n',
            "doubled quotes": b'a,b\n"he said ""hi""",2\n',
            "doubled quotes only": b'a\n""""\n',
            "empty quoted": b'a,b\n"",""\n',
            "empty fields": b"a,b,c\n,,\n1,,\n",
            "only commas": b",,,\n,,,\n",
            "delimiter in quotes": b'a,b\n"x,y,z",2\n',
            "quote in unquoted field": b'a,b\n1,x"y\n',
            "header only": b"a,b,c\n",
            "header only no newline": b"a,b,c",
            "empty file": b"",
            "only a newline": b"\n",
            "only a bom": b"\xef\xbb\xbf",
            "blank lines between": b"a,b\n\n1,2\n\n\n3,4\n\n",
            "crlf blank lines": b"a,b\r\n\r\n1,2\r\n",
            "one column": b"a\n1\n2\n",
            "one column empty value": b'a\n""\n"x"\n',
            "unicode": "nom,ville\nJosé,Zürich\n名,東京\n".encode("utf-8"),
            "ragged short": b"a,b,c\n1,2\n",
            "ragged long": b"a,b\n1,2,3\n",
            "ragged among good": b"a,b\n1,2\n3\n4,5\n6,7,8\n",
            "ragged after quoted newline": b'a,b\n"x\ny",1\nz\n',
            "unterminated": b'a,b\n1,"x\n',
            "unterminated at header": b'"a,b\n1,2\n',
            "unterminated empty tail": b'a,b\n1,"',
            "text after closing quote": b'a,b\n"x"y,1\n',
            "space after closing quote": b'a,b\n"x" ,1\n',
            "closing quote then newline then more": b'a,b\n"x"\n1,2\n',
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.agree(data, label=label)

    def test_other_delimiters(self):
        for delimiter in ("\t", ";"):
            for label, data in {
                "plain": b"a,b;c\n1,2;3\n",
                "quoted": b'a,b;c\n"x;y",2;3\n"p\nq";z\n',
                "ragged": b"a;b\n1\n",
            }.items():
                with self.subTest((delimiter, label)):
                    self.agree(data.replace(b";", delimiter.encode()) if delimiter == "\t" else data, delimiter, label)

    def test_header_names_survive(self):
        names = ["id", "na,me", 'q"x', "multi\nline", "multi\r\nline", " spaced ", "", "é名"]
        for terminator in ("\n", "\r\n"):
            data = write_csv([names, ["1"] * len(names)], lineterminator=terminator, quoting=csv.QUOTE_ALL)
            with self.subTest(terminator):
                got = self.scratch.table("n.csv", data)
                self.assertEqual([name_text(n) for n in got.data()["headers"]], [n.encode("utf-8") for n in names])
                data = write_csv([names, ["1"] * len(names)], lineterminator=terminator)
                got = self.scratch.table("n.csv", data)
                self.assertEqual([name_text(n) for n in got.data()["headers"]], [n.encode("utf-8") for n in names])

    def test_names_that_are_not_utf8_come_back_as_bytes(self):
        got = self.scratch.table("l.csv", b"caf\xe9,b\n1,2\n")
        self.assertEqual(validate(got), [])
        self.assertEqual(got.data()["headers"][0], {"b64": "Y2Fm6Q=="})
        self.assertEqual(got.data()["headers"][1], "b")

    def test_random_tables(self):
        rng = random.Random(20260604)
        alphabet = ['a', 'b', 'Z', '0', ' ', ',', ';', '\t', '"', '""', '\n', '\r\n', 'é', '東', "'", '\\']
        for case in range(1500):
            delimiter = rng.choice([",", ",", "\t", ";"])
            width = rng.randint(1, 6)
            rows = []
            for _ in range(rng.randint(0, 8)):
                rows.append(["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6))) for _ in range(width)])
            if rows and rng.random() < 0.5:
                # a header of plain names
                rows[0] = ["h%d" % i for i in range(width)]
            data = write_csv(rows, delimiter, rng.choice(["\n", "\r\n"]), rng.choice([csv.QUOTE_MINIMAL, csv.QUOTE_ALL, csv.QUOTE_NONNUMERIC]), rng.random() < 0.2)
            damage = rng.random()
            if data and damage < 0.12:
                data = data[:rng.randrange(len(data))]                       # cut anywhere: often a quote left open
            elif data and damage < 0.24:
                at = rng.randrange(len(data))
                data = data[:at] + b'"' + data[at:]                           # a stray quote
            elif rows and damage < 0.36:
                r = rng.randrange(len(rows))
                rows[r] = rows[r][:-1] if rng.random() < 0.5 else rows[r] + ["x"]
                data = write_csv(rows, delimiter)                              # a ragged row
            if damage < 0.36 and re.search(rb"\r(?!\n)", data):
                # Python ends a record at a lone CR; this reader does not (see
                # tools/table/table.ls). They can differ only on damaged input.
                continue
            with self.subTest(case=case):
                self.agree(data, delimiter, "case %d" % case)

    def test_long_records_cross_chunk_boundaries(self):
        # The reader works in 64 KiB chunks: records that straddle them.
        rng = random.Random(7)
        for size in (65535, 65536, 65537, 65536 * 2 - 3, 200000):
            field = "".join(rng.choice("abc,\n\"") for _ in range(size))
            data = write_csv([["a", "b"], ["1", field], ["2", "3"]])
            with self.subTest(size):
                self.agree(data, label="field of %d" % size)

    def test_many_rows(self):
        rows = [["id", "text"]] + [[str(i), "v,%d" % i] for i in range(50000)]
        self.agree(write_csv(rows), label="50000 rows")


if __name__ == "__main__":
    unittest.main()
