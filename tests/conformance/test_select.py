"""`--select`: differential against Python's csv module, paging, bounds.

For every table the reference (csv.reader, strict) gives the records; the
expected answer to `--select NAMES` is those fields of every row, in the order
named, repeated for a repeated name. The json answer must equal it, and the csv
answer must parse back to it with the same csv.reader *and* be the bytes
csv.writer writes (LF, minimal quoting): a field with an embedded delimiter,
quote or newline has to come back exactly.
"""

import csv
import io
import json
import random
import re
import unittest

from harness import Scratch, name_text, run, run_argv, validate
from test_differential import FLAG, reference, write_csv

csv.field_size_limit(1 << 30)


def records(data, delimiter=","):
    """The reference's records: [] when the reader or the table refuses it."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        out = [r for r in csv.reader(io.StringIO(data.decode("latin-1"), newline=""), delimiter=delimiter, strict=True) if r]
    except csv.Error:
        return None
    return out


def spell(name):
    """A header name as --select NAMES writes it."""
    out = name.replace("\\", "\\\\").replace(",", "\\,")
    return "\\" + out if out.startswith("#") else out


def written(rows, delimiter=","):
    out = io.StringIO(newline="")
    w = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("latin-1")


class Select(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def pick(self, data, names, *flags, delimiter=","):
        return self.s.table("t.csv", data, "--select", ",".join(spell(n) for n in names), *FLAG[delimiter], *flags)

    def check(self, data, names, delimiter=",", label=""):
        recs = records(data, delimiter)
        header, rows = recs[0], recs[1:]
        at = [header.index(n) for n in names]
        expected = [[r[i] for i in at] for r in rows]
        got = self.pick(data, names, delimiter=delimiter)
        self.assertEqual(validate(got), [], label)
        d = got.data()
        self.assertEqual(got.status, 0, "%s %r -> %r" % (label, data[:200], got.doc()))
        self.assertEqual([name_text(c) for c in d["columns"]], [n.encode("latin-1") for n in names], label)
        self.assertEqual([[name_text(f) for f in r] for r in d["rows"]], [[f.encode("latin-1") for f in r] for r in expected], label)
        self.assertEqual((d["row_count"], d["truncated"], d["next"]), (len(rows), False, None), label)
        got = self.pick(data, names, "--format", "csv", delimiter=delimiter)
        self.assertEqual((got.status, got.stderr), (0, b""), "%s %r" % (label, got))
        self.assertEqual(got.stdout, written([names] + expected, delimiter), "%s %r" % (label, data[:200]))
        # and it reads back with the reference to the same fields
        again = records(got.stdout, delimiter)
        self.assertEqual(again, [names] + expected, label)

    def test_hand_picked(self):
        cases = {
            "plain": (b"a,b,c\n1,2,3\n4,5,6\n", ["c", "a"]),
            "duplicate": (b"a,b\n1,2\n", ["a", "a", "b", "a"]),
            "reordered": (b"a,b,c\n1,2,3\n", ["c", "b", "a"]),
            "bom crlf": (b"\xef\xbb\xbfa,b\r\n1,2\r\n3,4\r\n", ["b"]),
            "quoted newline": (b'a,b\n1,"x\ny"\n2,3\n', ["b", "a"]),
            "quoted crlf newline": (b'a,b\r\n1,"x\r\ny"\r\n2,3\r\n', ["b"]),
            "embedded delimiter": (b'a,b\n"x,y,z",2\n', ["a"]),
            "embedded quotes": (b'a,b\n"he said ""hi""",2\n', ["a", "b"]),
            "quote only": (b'a\n""""\n', ["a"]),
            "empty fields": (b"a,b,c\n,,\n1,,\n", ["b", "c"]),
            "single empty column": (b'a,b\n,1\n"",2\n', ["a"]),
            "stray quote in unquoted": (b'a,b\n1,x"y\n', ["b"]),
            "quoted that needs no quotes": (b'a,b\n"plain","x y"\n', ["a", "b"]),
            "quoted header": (b'"a,1","b ""q"""\n1,2\n', ["a,1", 'b "q"']),
            "header name with backslash and hash": (b"a\\b,#1,#x\n1,2,3\n", ["a\\b", "#1", "#x"]),
            "header only": (b"a,b\n", ["a"]),
            "blank lines": (b"a,b\n\n1,2\n\n", ["b"]),
            "unicode": ("nom,ville\nJosé,Zürich\n".encode("utf-8"), ["ville"]),
        }
        for label, (data, names) in cases.items():
            with self.subTest(label):
                self.check(data, names, label=label)

    def test_other_delimiters(self):
        data = b'a\tb\tc\n1\t"x\ty"\t3\n"p\nq"\t5\t6\n'
        self.check(data, ["c", "b"], "\t")
        self.check(b"a;b\n1;2\n", ["b"], ";")

    def test_random_tables(self):
        rng = random.Random(77)
        alphabet = ["a", "b", "Z", "0", " ", ",", ";", "\t", '"', '""', "\n", "\r\n", "é", "'", "\\"]
        done = 0
        for case in range(1200):
            delimiter = rng.choice([",", ",", "\t", ";"])
            width = rng.randint(1, 6)
            ascii_only = [c for c in alphabet if ord(c[0]) < 128]
            header = ["".join(rng.choice(ascii_only + ["h", "#"]) for _ in range(rng.randint(1, 4))) for _ in range(width)]
            if len(set(header)) != width or any(h == "" for h in header):
                header = ["h%d" % i for i in range(width)]
            body = [["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6))) for _ in range(width)] for _ in range(rng.randint(0, 8))]
            data = write_csv([header] + body, delimiter, rng.choice(["\n", "\r\n"]), rng.choice([csv.QUOTE_MINIMAL, csv.QUOTE_ALL]), rng.random() < 0.2)
            if isinstance(reference(data, delimiter), str):
                continue
            names = [rng.choice(header) for _ in range(rng.randint(1, 5))]
            with self.subTest(case=case):
                self.check(data, names, delimiter, "case %d" % case)
            done += 1
        self.assertGreater(done, 900)

    def test_ragged_rows_are_not_selected_and_are_reported(self):
        got = self.s.table("r.csv", 'a,b\n1,2\n"x\ny"\n3,4\n5,6,7\n', "--select", "b")
        self.assertEqual(got.error()["rule"], "parse.csv-ragged-row")
        self.assertEqual(got.data()["rows"], [["2"], ["4"]])
        got = self.s.table("r.csv", 'a,b\n1,2\n"x\ny"\n3,4\n', "--select", "b", "--format", "csv")
        self.assertEqual((got.status, got.stdout), (8, b"b\n2\n4\n"))
        self.assertIn(b"parse.csv-ragged-row", got.stderr)

    def test_unknown_and_ambiguous_names(self):
        got = self.s.table("u.csv", "id,Name,x\n1,2,3\n", "--select", "id,name")
        self.assertEqual(validate(got), [])
        self.assertEqual((got.status, got.first_rule()), (3, "select.unknown-column"))
        err = got.error()
        self.assertEqual(err["detail"]["available"], ["id", "Name", "x"])
        self.assertEqual(err["detail"]["name"], "name")
        self.assertNotIn("data", got.doc())
        # The repair is a choice of invocations, each of which works.
        self.assertEqual(err["repair"]["kind"], "choose")
        self.assertEqual(len(err["repair"]["options"]), 1)
        again = run_argv(err["repair"]["options"][0]["argv"])
        self.assertEqual((again.status, again.data()["columns"]), (0, ["id", "Name"]))
        # No near name, no choice.
        got = self.s.table("u.csv", "id,Name,x\n1,2,3\n", "--select", "zzz")
        self.assertEqual(got.error()["repair"]["kind"], "none")
        # An empty file has no columns at all.
        got = self.s.table("u.csv", "", "--select", "a")
        self.assertEqual((got.status, got.error()["detail"]["available"]), (3, []))
        got = self.s.table("u.csv", "a,b,a\n1,2,3\n", "--select", "b,a")
        self.assertEqual((got.status, got.first_rule()), (8, "select.ambiguous-column"))
        self.assertEqual(got.error()["detail"]["name"], "a")
        # by position it can be had
        got = self.s.table("u.csv", "a,b,a\n1,2,3\n", "--select", "#3,#1,b")
        self.assertEqual(got.data()["rows"], [["3", "1", "2"]])

    def test_the_header_list_is_capped_in_the_refusal(self):
        header = ",".join("c%d" % i for i in range(80))
        got = self.s.table("w.csv", header + "\n", "--select", "zzz")
        self.assertEqual(len(got.error()["detail"]["available"]), 50)
        self.assertEqual((got.error()["detail"]["available_truncated"], got.error()["detail"]["columns"]), (True, 80))

    def test_names_escapes_and_positions(self):
        data = "a,b\\c,#2,3,#x,\n1,2,3,4,5,6\n"
        for names, want in (
            ("a", ["1"]), ("b\\\\c", ["2"]), ("#2", ["2"]), ("\\#2", ["3"]), ("#x", ["5"]), ("#6", ["6"]),
            ("a,,a", ["1", "6", "1"]), ("#4,#1", ["4", "1"]),
        ):
            with self.subTest(names):
                got = self.s.table("n.csv", data, "--select", names)
                self.assertEqual(got.data()["rows"], [want], got)
        for bad in ("a\\", "a\\x", "a\\n"):
            with self.subTest(bad):
                got = self.s.table("n.csv", data, "--select", bad)
                self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"))
        for missing in ("#0", "#7", "#9999999999"):
            with self.subTest(missing):
                got = self.s.table("n.csv", data, "--select", missing)
                self.assertEqual((got.status, got.first_rule()), (3, "select.unknown-column"))
        got = self.s.table("n.csv", data, "--select", ",".join("a" for _ in range(4097)))
        self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"))

    def test_paging_equals_slicing(self):
        rows = [[str(i), "v,%d" % i, 'q"%d' % (i % 3), "l\n%d" % i if i % 5 == 0 else "x"] for i in range(237)]
        data = write_csv([["id", "t", "q", "n"]] + rows)
        self.s.write("p.csv", data)
        want = [[r[3], r[0]] for r in rows]
        for limit in (1, 7, 50, 237, 238, 1000):
            got_rows, at, pages = [], 0, 0
            while True:
                flags = ["--select", "n,id", "--limit", limit] + (["--from", at] if at else [])
                got = run("--root", self.s.dir, *flags, "p.csv")
                self.assertEqual(validate(got), [])
                d = got.data()
                self.assertLessEqual(d["row_count"], limit)
                got_rows += d["rows"]
                pages += 1
                if d["next"] is None:
                    self.assertFalse(d["truncated"])
                    break
                self.assertTrue(d["truncated"])
                self.assertEqual(d["next"]["from"], len(got_rows))
                at = d["next"]["from"]
            self.assertEqual(got_rows, want, limit)
            self.assertEqual(pages, -(-237 // limit) if limit < 237 else 1)
        # csv takes the same window
        got = run("--root", self.s.dir, "--select", "id", "--format", "csv", "--from", 230, "p.csv")
        self.assertEqual(got.stdout, b"id\n230\n231\n232\n233\n234\n235\n236\n")
        got = run("--root", self.s.dir, "--select", "id", "--format", "csv", "--from", 3, "--limit", 2, "p.csv")
        self.assertEqual(got.stdout, b"id\n3\n4\n")
        self.assertEqual(got.status, 0)
        # from past the end is an empty page, not an error
        got = run("--root", self.s.dir, "--select", "id", "--from", 5000, "p.csv")
        self.assertEqual((got.data()["rows"], got.data()["truncated"], got.data()["next"]), ([], False, None))

    def test_paging_counts_ragged_rows_as_rows(self):
        data = "a,b\n1,1\n2\n3,3\n4,4\n5,5\n"
        first = self.s.table("g.csv", data, "--select", "a", "--limit", 2)
        self.assertEqual(first.data()["rows"], [["1"], ["3"]])
        self.assertEqual(first.data()["next"], {"from": 3})
        second = self.s.table("g.csv", data, "--select", "a", "--limit", 2, "--from", 3)
        self.assertEqual(second.data()["rows"], [["4"], ["5"]])

    def test_the_budget_ends_a_page(self):
        rows = [[str(i), "x" * 100] for i in range(50)]
        data = write_csv([["id", "t"]] + rows)
        got = self.s.table("b.csv", data, "--select", "id,t", "--max-bytes", 1000)
        d = got.data()
        self.assertTrue(d["truncated"])
        self.assertLess(d["row_count"], 50)
        self.assertEqual(d["next"], {"from": d["row_count"]})
        self.assertLessEqual(len(json.dumps(d["rows"], separators=(",", ":"))), 1000)
        got = self.s.table("b.csv", data, "--select", "id,t", "--max-bytes", 50)
        self.assertEqual((got.status, got.first_rule()), (8, "limit.output-too-large"))
        # The pages of a small budget are the table.
        seen, at = [], 0
        while True:
            got = self.s.table("b.csv", data, "--select", "id,t", "--max-bytes", 700, "--from", at)
            seen += got.data()["rows"]
            if got.data()["next"] is None:
                break
            at = got.data()["next"]["from"]
        self.assertEqual(seen, rows)

    def test_max_rows(self):
        data = "a\n" + "".join("%d\n" % i for i in range(100))
        got = self.s.table("m.csv", data, "--select", "a", "--max-rows", 10)
        self.assertEqual((got.status, got.data()["row_count"], got.data()["truncated"], got.data()["next"]), (0, 10, True, None))
        got = self.s.table("m.csv", data, "--select", "a", "--max-rows", 10, "--format", "csv")
        self.assertEqual((got.status, got.stdout), (8, b"a\n" + b"".join(b"%d\n" % i for i in range(10))))
        self.assertIn(b"limit.too-many-rows", got.stderr)
        got = self.s.table("m.csv", data, "--select", "a", "--max-rows", 100, "--format", "csv")
        self.assertEqual(got.status, 0)

    def test_limits_of_records(self):
        data = b'a,b\n1,"' + b"x\n" * 600 + b'"\n'
        got = self.s.table("l.csv", data, "--select", "a", "--max-line-bytes", 500)
        self.assertEqual((got.status, got.first_rule()), (8, "limit.record-too-large"))
        self.assertEqual(self.s.table("l.csv", data, "--select", "a", "--max-line-bytes", 5000).data()["rows"], [["1"]])
        # the shape does not keep records and has no such limit
        self.assertEqual(self.s.table("l.csv", data, "--max-line-bytes", 500).status, 0)
        got = self.s.table("l.csv", b"a\n" + b"x" * 5000 + b"\n", "--select", "a", "--max-line-bytes", 100)
        self.assertEqual((got.status, got.first_rule()), (8, "limit.line-too-long"))
        got = self.s.table("l.csv", b"a\n" + b"x" * 5000 + b"\n", "--select", "a", "--max-line-bytes", 100, "--format", "csv")
        self.assertEqual((got.status, got.stdout), (8, b"a\n"))
        self.assertIn(b"limit.line-too-long", got.stderr)

    def test_flags(self):
        for args, rule in (
            (["--select", "a", "--limit", 0], "args.bad-value"),
            (["--select", "a", "--limit", 1000001], "args.bad-value"),
            (["--select", "a", "--max-bytes", 0], "args.bad-value"),
            (["--select", "a", "--max-bytes", 67108865], "args.bad-value"),
            (["--limit", 5], "args.required-flag"),
            (["--from", 5], "args.required-flag"),
            (["--format", "csv"], "args.required-flag"),
            (["--select", "a", "--format", "text"], "args.conflict"),
            (["--select", "a", "--select", "a"], "args.duplicate-flag"),
        ):
            with self.subTest(args):
                got = self.s.table("f.csv", "a\n1\n", *args)
                # (--format text and csv answer on standard error, as sentences)
                self.assertIn(rule, got.stdout.decode() + got.stderr.decode())
                self.assertEqual(got.status, 2)
        got = self.s.table("f.csv", "a\n1\n", "--select", "a", "--limit", 1000000)
        self.assertEqual(got.status, 0)

    def test_a_lone_cr_in_a_field_is_quoted_on_the_way_out(self):
        # An unquoted field with a CR in it (the reference ends a record there,
        # so this is not a differential case): written quoted, as a csv writer
        # must, so that no reader ends a record in it.
        got = self.s.table("c.csv", b"a,b\nx\ry,1\n", "--select", "a,b", "--format", "csv")
        self.assertEqual(got.stdout, b'a,b\n"x\ry",1\n')
        got = self.s.table("c.csv", b'a,b\nx"y,1\n', "--select", "a", "--format", "csv")
        self.assertEqual(got.stdout, b'a\n"x""y"\n')

    def test_a_name_of_a_hash_alone(self):
        got = self.s.table("h.csv", "#,b\n1,2\n", "--select", "#")
        self.assertEqual((got.data()["columns"], got.data()["rows"]), (["#"], [["1"]]))

    def test_the_repair_of_a_name_with_a_comma_escapes_it(self):
        got = self.s.table("h.csv", "A,b,c\n1,2,3\n", "--select", "a\\,b")
        self.assertEqual(got.first_rule(), "select.unknown-column")
        got = self.s.table("h.csv", "A\\b,\"A,b\"\n1,2\n", "--select", "a\\,b,A\\\\b")
        options = got.error()["repair"]["options"]
        self.assertEqual(len(options), 1)
        again = run_argv(options[0]["argv"])
        self.assertEqual((again.status, again.data()["rows"]), (0, [["2", "1"]]))

    def test_a_hash_inside_a_name_is_text(self):
        got = self.s.table("h.csv", "a#1,b\n1,2\n", "--select", "a#1")
        self.assertEqual(got.data()["rows"], [["1"]])
        got = self.s.table("h.csv", "a,b\n1,2\n", "--select", "a\\#,#1")
        self.assertEqual(got.first_rule(), "select.unknown-column")
        self.assertEqual(got.error()["detail"]["name"], "a#")

    def test_text_and_binary(self):
        data = b"caf\xe9,b\n\xff\xfe,2\n"
        got = self.s.table("x.csv", data, "--select", "caf\\xe9".replace("\\xe9", "") + ",b")
        self.assertEqual(got.first_rule(), "select.unknown-column")
        got = self.s.table("x.csv", data, "--select", "#1,b")
        self.assertEqual(validate(got), [])
        self.assertEqual(got.data()["columns"][0], {"b64": "Y2Fm6Q=="})
        self.assertEqual(got.data()["rows"], [[{"b64": "//4="}, "2"]])
        got = self.s.table("x.csv", data, "--select", "#1,b", "--format", "csv")
        self.assertEqual(got.stdout, b"caf\xe9,b\n\xff\xfe,2\n")
        # JSON control characters and a quote come out escaped
        got = self.s.table("x.csv", 'a\n"l1\nl2\t""q"""\n', "--select", "a")
        self.assertEqual(got.data()["rows"], [['l1\nl2\t"q"']])

    def test_deterministic_and_never_a_trap(self):
        self.s.write("d.csv", 'a,"b c"\r\n1,"x\r\ny"\r\n2,3\r\n')
        outs = {run("--root", self.s.dir, "--select", "b c,a", "d.csv").stdout for _ in range(4)}
        self.assertEqual(len(outs), 1)
        rng = random.Random(5)
        for i in range(300):
            data = bytes(rng.choice(b'ab,;\t"\r\n\x00\xff #') for _ in range(rng.randint(0, 60)))
            for flags in (["--select", "a,b"], ["--select", "#1,#2,#1", "--format", "csv"], ["--select", "#1", "--delimiter", "tab", "--max-line-bytes", "7", "--max-rows", "2"],
                          ["--select", "a", "--limit", "1", "--from", "1", "--max-bytes", "9"]):
                got = self.s.table("z.csv", data, *flags)
                self.assertNotIn(got.status, (-4, -6, -8, -11, 132, 134, 136, 139), data)
                if "csv" not in flags:
                    self.assertEqual(validate(got), [], (data, flags))


if __name__ == "__main__":
    unittest.main()
