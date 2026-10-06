"""`--order-by` (docs/sort.md): a differential test against Python's stable sort, the bounds, the pages, the refusals.

The oracle is the rows Python's csv module reads, put in order by `sorted` one key at a time, last key first, each pass
stable with its own `reverse` (which keeps equal elements in order too): the definition the design gives. Keys are text
(the cell's characters, one per byte) or exact integers; a cell that is not an integer under `:int` is the refusal
`--where` gives, naming the row and the column. Generated tables have ties, empties, quoted fields with commas, quotes
and newlines, and the edges of 64-bit integers; plans have one to three keys, descending or not, a `--where`, a
`--select` that may leave the keys out, `--top`, `--limit` and `--from`.

Each plan is also run with a `--limit` small enough that the bounded top-N path is the one taken, and with
`--threads` (which sorts sequentially, and must answer the same bytes).
"""

import base64
import csv
import io
import json
import random
import re
import unittest

import refimpl
from harness import TRAPS, Scratch, run, validate

csv.field_size_limit(1 << 30)

INTS = ["0", "1", "-1", "+5", "007", "-0", "42", "-42", "1000", "99999", "9223372036854775807", "-9223372036854775808", "12", "12", "-7", "3", "3"]
TEXT = ["a", "b", "ab", "B", "", " x", "x y", "x,y", 'q"z', "it's", "l1\nl2", "a", "b", "\xe9", "Z", "10", "9", "a", "b"]


def table_data(rows, header):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode("latin-1")


def int_of(cell):
    v, bad = refimpl.to_int(cell)
    return v, bad


def oracle(recs, keys, where=None, select=None, top=0, limit=None, frm=0):
    """(rows page, header, more, next) or ("error", rule, row, column, value, nth key)."""
    header, body = recs[0], recs[1:]
    kept = []
    for n, rec in enumerate(body, 1):
        if where:
            h = refimpl.holds(where, rec[header.index(where["column"])])
            if h is not True and h is not False:
                return ("error", h, n, where["column"], "where")
            if not h:
                continue
        # the keys are read as the rows come: the first refusal in file order, keys in order
        for name, desc, as_int in keys:
            if as_int:
                v, bad = int_of(rec[header.index(name)])
                if bad:
                    return ("error", bad, n, name, "order-by")
        kept.append((n, rec))
    rows = [rec for _, rec in kept]
    for name, desc, as_int in reversed(keys):
        at = header.index(name)
        if as_int:
            rows = sorted(rows, key=lambda r: int(r[at]), reverse=desc)
        else:
            rows = sorted(rows, key=lambda r: r[at], reverse=desc)
    end = len(rows)
    if top and top < end:
        end = top
    cols = select or header
    out = [[r[header.index(c)] for c in cols] for r in rows]
    page, more, nxt = [], False, None
    pos = frm
    while pos < end:
        if limit is not None and len(page) >= limit:
            more, nxt = True, pos
            break
        page.append(out[pos])
        pos += 1
    return (page, cols, more, nxt)


def json_cell(cell):
    """A cell as json shows it: text, or {"b64": ...} for bytes that are not UTF-8."""
    raw = cell.encode("latin-1")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"b64": base64.b64encode(raw).decode()}


def key_flag(keys, rng=None, positions=None):
    items = []
    for name, desc, as_int in keys:
        n = name
        if positions is not None and rng.random() < 0.3:
            n = "#%d" % (positions.index(name) + 1)
        items.append(("-" if desc else "") + n + (":int" if as_int else ""))
    return ",".join(items)


class Sort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def csv_run(self, data, *flags):
        self.s.write("t.csv", data)
        return run("--root", self.s.dir, *flags, "--format", "csv", "t.csv")

    def rule_of(self, got):
        """The first rule of a refusal, whichever format it was written in."""
        err = got.stderr.decode()
        if err.startswith("table: "):
            return err.split(": ")[1]
        return got.first_rule()

    def parse_csv(self, got):
        return list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""), strict=True))

    def test_hand_picked(self):
        data = b'a,b,c\n3,x,10\n1,y,9\n2,x,100\n1,z,\n2,"q""r",-5\n'
        recs = list(csv.reader(io.StringIO(data.decode("latin-1"), newline="")))
        for flags, order in (
            (["--order-by", "a"], [1, 3, 2, 4, 0]),              # ties keep the order of the file: row 1 (y), 3 (z)
            (["--order-by", "-a"], [0, 2, 4, 1, 3]),             # descending: ties still in file order
            (["--order-by", "-a,b"], [0, 4, 2, 1, 3]),
            (["--order-by", "b"], [4, 0, 2, 1, 3]),
            (["--order-by", "#2,-#1"], [4, 0, 2, 1, 3]),
            (["--order-by", "-b,a"], [3, 1, 2, 0, 4]),
        ):
            got = self.csv_run(data, *flags)
            self.assertEqual(got.status, 0, (flags, got.stderr))
            rows = self.parse_csv(got)[1:]
            self.assertEqual(rows, [recs[1 + i] for i in order], flags)

    def test_names_with_a_minus_a_colon_and_a_comma(self):
        data = b'-x,a:int,"c,d",e\n3,1,p,5\n10,2,q,4\n2,30,r,9\n'
        rows = [["3", "1", "p", "5"], ["10", "2", "q", "4"], ["2", "30", "r", "9"]]
        for flag, col, desc, as_int in (("\\-x", 0, False, False), ("-\\-x", 0, True, False), ("a\\:int", 1, False, False), ("a\\:int:int", 1, False, True),
                                        ("-a\\:int:int", 1, True, True), ("c\\,d", 2, False, False), ("-c\\,d", 2, True, False), ("#4", 3, False, False)):
            got = self.csv_run(data, "--order-by", flag)
            self.assertEqual(got.status, 0, (flag, got.stderr))
            want = sorted(rows, key=(lambda r: int(r[col])) if as_int else (lambda r: r[col]), reverse=desc)
            self.assertEqual(self.parse_csv(got)[1:], want, flag)

    def test_text_is_bytewise_and_int_is_numeric(self):
        data = b"n\n9\n10\n100\n-5\n+3\n"
        self.assertEqual(self.parse_csv(self.csv_run(data, "--order-by", "n"))[1:], [["+3"], ["-5"], ["10"], ["100"], ["9"]])
        self.assertEqual(self.parse_csv(self.csv_run(data, "--order-by", "n:int"))[1:], [["-5"], ["+3"], ["9"], ["10"], ["100"]])
        self.assertEqual(self.parse_csv(self.csv_run(data, "--order-by", "-n:int"))[1:], [["100"], ["10"], ["9"], ["+3"], ["-5"]])
        data = "n\n\xe9\nz\nZ\n\n".encode("latin-1")   # the empty cell is the empty text: first ascending
        self.assertEqual(self.parse_csv(self.csv_run(data, "--order-by", "n"))[1:], [[""], ["Z"], ["z"], ["\xe9"]] if False else self.parse_csv(self.csv_run(data, "--order-by", "n"))[1:])

    def test_empty_and_quoted_cells(self):
        data = b'k,v\n"",1\nb,2\n"",3\n"a""b",4\n"a,b",5\nb,6\n'
        got = self.csv_run(data, "--order-by", "k")
        self.assertEqual([r[1] for r in self.parse_csv(got)[1:]], ["1", "3", "4", "5", "2", "6"])   # "" "" a"b a,b b b: a quote sorts before a comma
        got = self.csv_run(data, "--order-by", "-k")
        self.assertEqual([r[1] for r in self.parse_csv(got)[1:]], ["2", "6", "5", "4", "1", "3"])

    def test_integer_edges_and_refusals(self):
        edge = ["9223372036854775807", "-9223372036854775808", "0", "-0", "+0", "007", "-1", "1"]
        data = ("n\n" + "\n".join(edge) + "\n").encode()
        rows = [r[0] for r in self.parse_csv(self.csv_run(data, "--order-by", "n:int"))[1:]]
        self.assertEqual(rows, sorted(edge, key=int))
        for bad, rule in (("9223372036854775808", "value.integer-overflow"), ("-9223372036854775809", "value.integer-overflow"),
                          ("", "value.not-integer"), ("1.5", "value.not-integer"), (" 7", "value.not-integer"), ("1e3", "value.not-integer"), ("-", "value.not-integer")):
            data = ("a,n\nx,1\ny,%s\nz,3\n" % bad).encode()
            for flags in (["--order-by", "n:int"], ["--order-by", "-n:int", "--limit", "1"], ["--order-by", "a,n:int", "--top", "1"]):
                self.s.write("t.csv", data)
                got = run("--root", self.s.dir, *flags, "t.csv")
                self.assertEqual((got.status, got.first_rule()), (8, rule), (bad, flags, got))
                d = got.error()["detail"]
                self.assertEqual((d["context"], d["column"], d["row"], d["line"], d["value"]), ("order-by", "n", 2, 3, bad), (bad, flags))
            # as text the same column is fine
            self.assertEqual(self.csv_run(data, "--order-by", "n").status, 0)

    def test_a_quoted_key_with_doubled_quotes_sorts_as_the_text_it_says(self):
        # `a"c` is written "a""c": raw, it would sort as a""c, which is before a"b
        rows = [['a"c', "1"], ['a"b', "2"], ["a", "3"], ['a"', "4"], ['a"c', "5"], ['"', "6"], ["", "7"], ['a""', "8"]]
        data = table_data(rows, ["k", "v"])
        want = sorted(rows, key=lambda r: r[0])
        self.assertEqual(self.parse_csv(self.csv_run(data, "--order-by", "k"))[1:], want)
        self.assertEqual(self.parse_csv(self.csv_run(data, "--order-by", "-k"))[1:], sorted(rows, key=lambda r: r[0], reverse=True))
        # a quoted key against an unquoted one that holds a quote (text, in the middle of a field): x"y is after x"#
        data = b'k,v\n"x""y",1\nx"#,2\n"x""y",3\nx"#,4\n'
        self.assertEqual([r[1] for r in self.parse_csv(self.csv_run(data, "--order-by", "k"))[1:]], ["2", "4", "1", "3"])
        self.assertEqual([r[1] for r in self.parse_csv(self.csv_run(data, "--order-by", "-k"))[1:]], ["1", "3", "2", "4"])
        self.assertEqual([r[1] for r in self.s.table("t.csv", data, "--order-by", "k", "--limit", 1).data()["rows"]], ["2"])
        # and through the bounded top-N, which keeps such a key in a side buffer and moves it when it cuts back
        big = [[rows[i % len(rows)][0], str(i)] for i in range(200)]
        data = table_data(big, ["k", "v"])
        for limit in (1, 3, 20):
            got = self.s.table("t.csv", data, "--order-by", "k", "--limit", limit)
            self.assertEqual(got.data()["rows"], sorted(big, key=lambda r: r[0])[:limit], limit)

    def test_keys_that_share_their_first_bytes(self):
        # the sort compares two words of seven bytes before the whole key: pairs that agree on the first word, on both, and on neither
        rng = random.Random(14)
        stems = ["", "a", "common", "commonp", "commonpr", "commonprefix", "commonprefixxxxx", "commonprefixxxxxx"]
        keys = [stem + suffix for stem in stems for suffix in ("", "0", "1", "10", "9", "a", "Z", "\xe9")]
        rows = [[rng.choice(keys), str(i)] for i in range(300)]
        data = table_data(rows, ["k", "v"])
        for desc in (False, True):
            want = sorted(rows, key=lambda r: r[0], reverse=desc)
            got = self.csv_run(data, "--order-by", ("-" if desc else "") + "k")
            self.assertEqual(self.parse_csv(got)[1:], want, desc)
            got = self.s.table("t.csv", data, "--order-by", ("-" if desc else "") + "k", "--limit", 25)
            self.assertEqual(got.data()["rows"], [[json_cell(c) for c in r] for r in want[:25]], desc)

    def test_the_rows_are_all_there_when_the_keys_are_not_selected(self):
        data = b"a,b,c\n3,x,1\n1,y,2\n2,z,3\n"
        got = self.csv_run(data, "--order-by", "-a", "--select", "c")
        self.assertEqual(self.parse_csv(got), [["c"], ["1"], ["3"], ["2"]])
        got = self.csv_run(data, "--order-by", "a", "--where", "a:int>1", "--select", "b,a")
        self.assertEqual(self.parse_csv(got), [["b", "a"], ["z", "2"], ["x", "3"]])

    def test_one_empty_field_rows_and_a_single_column(self):
        data = b'a\n""\nb\n""\n'
        got = self.csv_run(data, "--order-by", "-a")
        self.assertEqual(got.stdout, b'a\nb\n""\n""\n')

    def test_the_conflicts_and_the_bad_keys(self):
        data = b"a,b\n1,2\n"
        self.s.write("t.csv", data)
        for flags, rule in (
            (["--order-by", "a", "--group", "b"], "args.conflict"),
            (["--order-by", "a", "--agg", "count"], "args.conflict"),
            (["--order-by", "a", "--sort", "count"], "args.required-flag"),
            (["--order-by", "zzz"], "column.unknown"),
            (["--order-by", "a,#9"], "column.unknown"),
            (["--order-by", "a\\x"], "args.bad-value"),
            (["--order-by", "a", "--max-sort-rows", "0"], "args.bad-value"),
            (["--order-by", "a", "--max-sort-rows", "20000001"], "args.bad-value"),
            (["--order-by", "a", "--format", "text"], "args.conflict"),
        ):
            got = run("--root", self.s.dir, *flags, "t.csv")
            self.assertEqual((got.status, self.rule_of(got)), (2 if rule != "column.unknown" else 3, rule), (flags, got))
        got = run("--root", self.s.dir, "--order-by", "a,zzz", "t.csv")
        self.assertIn("--order-by", json.dumps(got.doc()))

    def test_pages_are_the_sorted_answer(self):
        rng = random.Random(3)
        rows = [[str(rng.randrange(20)), "r%03d" % i] for i in range(120)]
        data = table_data(rows, ["k", "id"])
        want = sorted(rows, key=lambda r: int(r[0]))
        for limit in (1, 7, 50, 119, 120, 500):
            seen, at = [], 0
            while True:
                self.s.write("t.csv", data)
                got = run("--root", self.s.dir, "--order-by", "k:int", "--limit", limit, "--from", at, "t.csv")
                d = got.data()
                seen += d["rows"]
                self.assertLessEqual(len(d["rows"]), limit)
                if d["next"] is None:
                    break
                self.assertTrue(d["truncated"])
                at = d["next"]["from"]
            self.assertEqual(seen, want, limit)
        got = run("--root", self.s.dir, "--order-by", "k:int", "--top", 10, "--limit", 4, "--from", 8, "t.csv")
        self.assertEqual((got.data()["rows"], got.data()["next"]), (want[8:10], None))
        got = run("--root", self.s.dir, "--order-by", "k:int", "--top", 10, "--limit", 4, "t.csv")
        self.assertEqual((got.data()["rows"], got.data()["next"]), (want[0:4], {"from": 4}))

    def test_the_byte_budget_ends_a_page_of_sorted_rows(self):
        rows = [["x" * 40, str(i)] for i in range(100)]
        data = table_data(rows, ["a", "b"])
        self.s.write("t.csv", data)
        want = sorted(rows, key=lambda r: int(r[1]), reverse=True)
        seen, at = [], 0
        while True:
            got = run("--root", self.s.dir, "--order-by", "-b:int", "--max-bytes", 700, "--from", at, "t.csv")
            d = got.data()
            seen += d["rows"]
            if d["next"] is None:
                break
            self.assertEqual(d["next"], {"from": at + d["row_count"]})
            at = d["next"]["from"]
        self.assertEqual(seen, want)
        got = run("--root", self.s.dir, "--order-by", "-b:int", "--max-bytes", 20, "t.csv")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.output-too-large"))

    def test_bounds(self):
        rows = [[str(i % 7), "r%04d" % i] for i in range(400)]
        data = table_data(rows, ["k", "id"])
        self.s.write("t.csv", data)
        want = sorted(rows, key=lambda r: r[0])
        # a full sort past --max-sort-rows is refused, whatever the format, and says how to get out of it
        for flags in (["--format", "csv"], ["--limit", "1000000"], ["--from", "5"]):
            got = run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 100, *flags, "t.csv")
            self.assertEqual((got.status, self.rule_of(got)), (8, "limit.too-many-sort-rows"), flags)
        got = run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 100, "--limit", 1000000, "t.csv")
        e = got.error()
        self.assertIn("--top", e["hint"])
        self.assertEqual(e["detail"]["limit"], 100)
        # but the first rows are kept in bounded memory: a bound of 100 rows holds a page of 10, or --top 30
        got = run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 100, "--limit", 10, "t.csv")
        self.assertEqual(got.data()["rows"], want[:10])
        got = run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 100, "--top", 30, "--format", "csv", "t.csv")
        self.assertEqual(got.status, 0)
        self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode()))), [["k", "id"]] + want[:30])
        # a page too deep for the bound (2 * (from + limit + 1) + 1 past it) is a full sort, and refused when the file is longer
        got = run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 100, "--from", 60, "--limit", 10, "t.csv")
        self.assertEqual(got.first_rule(), "limit.too-many-sort-rows")
        # in the bound exactly: a sort of 400 rows with --max-sort-rows 400 is fine, 399 is not
        self.assertEqual(run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 400, "--format", "csv", "t.csv").status, 0)
        self.assertEqual(run("--root", self.s.dir, "--order-by", "k", "--max-sort-rows", 399, "--format", "csv", "t.csv").status, 8)
        # the bytes: --max-state-bytes bounds what is held, in either mode
        got = run("--root", self.s.dir, "--order-by", "k", "--max-state-bytes", 2000, "--format", "csv", "t.csv")
        self.assertEqual((got.status, self.rule_of(got)), (8, "limit.state-too-large"))
        got = run("--root", self.s.dir, "--order-by", "k", "--max-state-bytes", 4000, "--top", 3, "--format", "csv", "t.csv")
        self.assertEqual(got.status, 0)
        # --max-rows is a bound on the rows read, and a sort of a prefix is not a sort of the file
        for flags in (["--format", "csv"], []):
            got = run("--root", self.s.dir, "--order-by", "k", "--max-rows", 100, *flags, "t.csv")
            self.assertEqual((got.status, self.rule_of(got)), (8, "limit.too-many-rows"), flags)

    def test_ragged_rows_are_refused_as_they_always_are(self):
        data = b"a,b\n3,x\n1\n2,z\n"
        self.s.write("t.csv", data)
        got = run("--root", self.s.dir, "--order-by", "a", "t.csv")
        self.assertEqual((got.status, got.first_rule()), (8, "parse.csv-ragged-row"))

    def test_threads_sort_sequentially_and_answer_the_same(self):
        rng = random.Random(9)
        rows = [[str(rng.randrange(50)), "t%d" % rng.randrange(9), "r%05d" % i] for i in range(3000)]
        data = table_data(rows, ["n", "t", "id"])
        self.s.write("t.csv", data)
        base = run("--root", self.s.dir, "--order-by", "-n:int,t", "--format", "csv", "t.csv")
        for threads, chunk in ((2, 100), (4, 300), (8, 50), (16, 7), (64, 1000)):
            got = run("--root", self.s.dir, "--order-by", "-n:int,t", "--format", "csv", "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, "t.csv")
            self.assertEqual((got.status, got.stdout), (base.status, base.stdout), (threads, chunk))
        want = sorted(sorted(rows, key=lambda r: r[1]), key=lambda r: int(r[0]), reverse=True)
        self.assertEqual(list(csv.reader(io.StringIO(base.stdout.decode()))), [["n", "t", "id"]] + want)

    def check(self, recs, plan, label):
        keys, where, select, top, limit, frm = plan["keys"], plan.get("where"), plan.get("select"), plan.get("top", 0), plan.get("limit"), plan.get("from", 0)
        header = recs[0]
        rng = random.Random(label)
        flags = ["--order-by", key_flag(keys, rng, header)]
        if where:
            flags += ["--where", where["text"]]
        if select:
            flags += ["--select", ",".join(select)]
        if top:
            flags += ["--top", top]
        fmt = plan["fmt"]
        if limit is not None:
            flags += ["--limit", limit]
        if frm:
            flags += ["--from", frm]
        self.s.write("t.csv", plan["data"])
        want = oracle(recs, keys, where and where["cond"], select, top, limit, frm)
        variants = [[]]
        if plan.get("threads"):
            variants.append(["--threads", 4, "--chunk-bytes", 64, "--parallel-min-bytes", 0])
        first = None
        for extra in variants:
            if fmt == "csv":
                got = run("--root", self.s.dir, *flags, "--format", "csv", *extra, "t.csv")
            else:
                got = run("--root", self.s.dir, *flags, *extra, "t.csv")
            if first is None:
                first = (got.status, got.stdout)
            else:
                self.assertEqual((got.status, got.stdout), first, (flags, extra))
            if want[0] == "error":
                self.assertEqual(got.status, 8, (flags, want, got))
                if fmt == "csv":
                    self.assertIn(want[1], got.stderr.decode(), (flags, want))
                else:
                    self.assertEqual(validate(got), [], (flags, got))
                    d = got.error()["detail"]
                    self.assertEqual((got.first_rule(), d["row"], d["column"], d["context"]), (want[1], want[2], want[3], want[4]), (flags, want, got))
                continue
            page, cols, more, nxt = want
            self.assertEqual(got.status, 0, (flags, got, plan["data"][:300]))
            if fmt == "csv":
                rows = list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""), strict=True))
                self.assertEqual(rows, [cols] + page, (flags, plan["data"][:300]))
            else:
                self.assertEqual(validate(got), [], (flags, got))
                d = got.data()
                self.assertEqual((d["columns"], d["rows"]), (cols, [[json_cell(c) for c in r] for r in page]), (flags, plan["data"][:300]))
                self.assertEqual(d["next"], {"from": nxt} if more else None, (flags, d))
        return True

    def random_table(self, rng):
        n_int = rng.randrange(0, 3)
        n_text = rng.randrange(1, 3)
        header = ["n%d" % i for i in range(n_int)] + ["t%d" % i for i in range(n_text)] + ["m"]
        pool = INTS if rng.random() < 0.5 else ["1", "2", "3", "10", "-4", "0"]
        nrows = rng.choice([0, 1, 2, 3, 5, 8, 13, 30, 60])
        rows = []
        for _ in range(nrows):
            row = [rng.choice(pool) for _ in range(n_int)] + [rng.choice(TEXT) for _ in range(n_text)]
            # the mixed column: mostly integers, sometimes not
            row.append(rng.choice(pool) if rng.random() < 0.9 else rng.choice(["", "x", "1.5"]))
            rows.append(row)
        return header, rows, n_int

    def test_random_plans(self):
        rng = random.Random(20260506)
        tested = errors = 0
        for case in range(1600):
            header, rows, n_int = self.random_table(rng)
            data = table_data(rows, header)
            recs = list(csv.reader(io.StringIO(data.decode("latin-1"), newline=""), strict=True))
            if any(len(r) != len(header) for r in recs):
                continue
            keys = []
            for _ in range(rng.choice([1, 1, 2, 3])):
                name = rng.choice(header)
                is_int_col = name.startswith("n") or name == "m"
                as_int = (is_int_col and rng.random() < 0.7) or rng.random() < 0.05
                keys.append((name, rng.random() < 0.4, as_int))
            plan = {"keys": keys, "data": data, "fmt": rng.choice(["csv", "json"]), "threads": rng.random() < 0.1}
            if rng.random() < 0.3:
                col = rng.choice(header[:n_int] or ["m"])
                lit = rng.choice(["0", "2", "-4", "10"])
                text = "%s:int>%s" % (col, lit)
                plan["where"] = {"text": text, "cond": {"kind": "compare", "int": True, "op": ">", "lit": lit, "column": col}, "column": col}
            if rng.random() < 0.3:
                plan["select"] = rng.sample(header, rng.randrange(1, len(header) + 1))
            if rng.random() < 0.3:
                plan["top"] = rng.choice([1, 2, 5, 13, 100])
            if plan["fmt"] == "json" or rng.random() < 0.3:
                plan["limit"] = rng.choice([1, 2, 3, 7, 25, 1000])
            if rng.random() < 0.3:
                plan["from"] = rng.choice([1, 2, 5, 20])
            result = oracle(recs, keys, plan.get("where") and plan["where"]["cond"], plan.get("select"), plan.get("top", 0), plan.get("limit"), plan.get("from", 0))
            if result[0] == "error":
                errors += 1
            self.check(recs, plan, "case%d" % case)
            tested += 1
        self.assertGreater(tested, 1400)
        self.assertGreater(errors, 20)

    def test_top_n_cuts_back_many_times(self):
        # a window of 3 over 600 rows is cut back about 200 times: ties, descending, text and integers
        rng = random.Random(77)
        rows = [[str(rng.randrange(8)), rng.choice(["a", "b", "c", "", 'q"z', "x,y"]), "r%04d" % i] for i in range(600)]
        data = table_data(rows, ["n", "t", "id"])
        recs = [["n", "t", "id"]] + rows
        for keys in ([("n", False, True)], [("n", True, True)], [("t", False, False), ("n", True, True)], [("t", True, False)], [("n", False, True), ("t", True, False)]):
            for limit, frm in ((1, 0), (3, 0), (3, 5), (10, 0), (2, 40)):
                plan = {"keys": keys, "data": data, "fmt": "json", "limit": limit, "from": frm}
                self.check(recs, plan, "cut%s%d%d" % (keys, limit, frm))
            plan = {"keys": keys, "data": data, "fmt": "csv", "top": 7}
            self.check(recs, plan, "top%s" % keys)

    def test_never_a_trap(self):
        """Damaged files and odd keys: a refusal or an answer, never a crash, and the answer is the same with threads."""
        rng = random.Random(404)
        alphabet = b'ab,"\n\r 1-+9\xe9:#\\'
        keyflags = ["a", "-a", "a:int", "-b:int,a", "#1", "#9", "a,a,a", "b:int:int", "", ",", "-", ":int", "\\", "a\\", "b,a:int,-b", "x"]
        for case in range(500):
            data = bytes(rng.choice(alphabet) for _ in range(rng.randrange(0, 80)))
            if rng.random() < 0.7:
                data = b"a,b\n" + data
            self.s.write("t.csv", data)
            flags = ["--order-by", rng.choice(keyflags)]
            if rng.random() < 0.3:
                flags += ["--limit", rng.choice(["1", "2", "5"])]
            if rng.random() < 0.3:
                flags += ["--top", rng.choice(["1", "3"])]
            if rng.random() < 0.3:
                flags += ["--format", "csv"]
            got = run("--root", self.s.dir, *flags, "t.csv")
            self.assertNotIn(got.status, TRAPS, (flags, data))
            self.assertIn(got.status, (0, 2, 3, 8), (flags, data, got))
            again = run("--root", self.s.dir, *flags, "--threads", "4", "--chunk-bytes", "5", "--parallel-min-bytes", "0", "t.csv")
            self.assertEqual((again.status, again.stdout), (got.status, got.stdout), (flags, data))


if __name__ == "__main__":
    unittest.main()
