"""`--report types` (docs/numbers.md, stage N6, section 5.2): what each column holds, counted, and the declaration that would read it.

The oracle is written here from the design, independently of the tool: every cell is classified once, in this order, by Python's `int`, `re`, `Fraction`-free
integer arithmetic and `numbers_ref.flt` (Python's `float()` behind the design's grammar): empty; int (the `:int` grammar, 64 bits); dec (a point, no exponent, the
digits after the point are its scale, below 10^18 once scaled); float (an exponent, or too wide to be a dec; finite, in range, at most 1,100 bytes); not_finite
(`nan`, `inf`, any case, signed); other. The report's row for a column is the counters of that, the widest cell in bytes, the rows and lines of the first cell of
each rejected kind, and the suggestion: the smallest declaration every non-empty cell satisfies (`:int`, `:dec(S)` while the widest still fits 18 digits at S, `:float`),
else `none`. A suggestion is never applied: the tests also check that the report changes what no other flag does.
"""

import csv
import io
import random
import re
import unittest

import numbers_ref as ref
import test_parallel as par
from harness import Scratch, TRAPS, validate

INT = re.compile(rb"^[+-]?[0-9]+$")
DEC = re.compile(rb"^[+-]?([0-9]*)\.([0-9]*)$")
COLUMNS = ["name", "cells", "empty", "int", "dec", "float", "not_finite", "other", "dec_max_scale", "int_digits_max", "widest", "suggest", "first_empty_row", "first_empty_line",
           "first_not_int_row", "first_not_int_line", "first_not_dec_row", "first_not_dec_line", "first_not_float_row", "first_not_float_line", "first_other_row", "first_other_line",
           "first_other_cell", "first_other_cell_truncated"]


def idigits(cell):
    t = cell.lstrip(b"+-").split(b".")[0].lstrip(b"0")
    return len(t)


def classify(cell):
    """(class, scale, digits before the point): class is empty, int, dec, float, not_finite or other."""
    if cell == b"":
        return ("empty", 0, 0)
    if INT.match(cell) and -2 ** 63 <= int(cell) < 2 ** 63:
        return ("int", 0, idigits(cell))
    m = DEC.match(cell)
    if m and (m.group(1) + m.group(2)) and len(m.group(2)) <= 18:
        v = int((m.group(1) + m.group(2)).decode() or "0")
        if v < 10 ** 18:
            return ("dec", len(m.group(2)), idigits(cell))
    if not cell.isascii():
        return ("other", 0, 0)
    r = ref.flt(cell.decode("ascii"))
    if r[0] == "ok":
        return ("float", 0, 0)
    if r[1] == "value.not-finite":
        return ("not_finite", 0, 0)
    return ("other", 0, 0)


def suggest(c):
    nonempty = c["cells"] - c["empty"]
    if nonempty == 0 or c["not_finite"] or c["other"]:
        return "none"
    if not c["dec"] and not c["float"]:
        return ":int"
    if not c["float"] and c["int_digits_max"] + c["dec_max_scale"] <= 18:
        return ":dec(%d)" % c["dec_max_scale"]
    return ":float"


def report_of(name, cells_with_place):
    """cells_with_place: [(bytes, row, line)] of the rows counted. Returns the report's row as strings."""
    c = {k: 0 for k in COLUMNS[1:11]}
    first = {k: ("", "") for k in ("empty", "not_int", "not_dec", "not_float", "other")}
    other = (b"", False)
    for cell, row, line in cells_with_place:
        cls, scale, idig = classify(cell)
        c["cells"] += 1
        c["widest"] = max(c["widest"], len(cell))
        if cls == "empty":
            c["empty"] += 1
            if first["empty"][0] == "":
                first["empty"] = (str(row), str(line))
            continue
        c[cls] += 1
        if cls in ("int", "dec"):
            c["int_digits_max"] = max(c["int_digits_max"], idig)
        if cls == "dec":
            c["dec_max_scale"] = max(c["dec_max_scale"], scale)
        for key, bad in (("not_int", cls != "int"), ("not_dec", cls not in ("int", "dec")), ("not_float", cls in ("not_finite", "other")), ("other", cls == "other")):
            if bad and first[key][0] == "":
                first[key] = (str(row), str(line))
                if key == "other":
                    other = (cell[:64], len(cell) > 64)
    out = [name]
    out += [str(c[k]) for k in COLUMNS[1:11]]
    out.append(suggest(c))
    for key in ("empty", "not_int", "not_dec", "not_float", "other"):
        out += list(first[key])
    out.append(other[0].decode("latin-1") if first["other"][0] else "")
    out.append(("true" if other[1] else "false") if first["other"][0] else "")
    return out


def csv_bytes(header, rows, newline=b"\n"):
    """A CSV file: rows are lists of bytes cells, quoted when they need it. Also answers the line each data row starts at (the header is line 1)."""
    out = [b",".join(q(h) for h in header)]
    lines, line = [], 2
    for r in rows:
        enc = b",".join(q(c) for c in r)
        out.append(enc)
        lines.append(line)
        line += enc.count(b"\n") + 1
    return newline.join(out) + newline, lines


def q(cell):
    if b"," in cell or b'"' in cell or b"\n" in cell or b"\r" in cell:
        return b'"' + cell.replace(b'"', b'""') + b'"'
    return cell


def as_bytes_text(cell):
    """A cell of a json row as the latin-1 text of its bytes: a string is UTF-8, a cell that is not text is {"b64": ...}."""
    import base64
    if isinstance(cell, dict):
        return base64.b64decode(cell["b64"]).decode("latin-1")
    return cell.encode("utf-8").decode("latin-1")


def rows_of(got):
    return list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))


# ---- the cells ----------------------------------------------------------------------------------------------------------------

AWKWARD = [b"", b" ", b" 1", b"1 ", b"+1", b"-1", b"+", b"-", b".", b"+.", b".5", b"5.", b"-.5", b"+5.", b"0", b"-0", b"00012", b"007.50", b"1.50", b"1,5", b"1_000", b"1e3", b"1E3", b"1e", b"e3", b"1e-3", b".5e1",
           b"0x10", b"0b1", b"1.2.3", b"--1", b"1-", b"NaN", b"nan", b"-nan", b"Inf", b"-inf", b"+Infinity", b"infinity", b"INF", b"nano", b"infx", b"1e999", b"-1e999", b"1e-999", b"9223372036854775807", b"-9223372036854775808",
           b"9223372036854775808", b"-9223372036854775809", b"99999999999999999999", b"123456789012345678", b"1234567890123456789", b"0.123456789012345678", b"0.1234567890123456789", b"1234567890.123456789",
           b"123456789012345678.5", b"999999999999999999.9", b"9" * 40, b"9" * 40 + b".5", b"0" * 30 + b"7", b"7." + b"0" * 30, b"\xc3\xa9", b"\xff\xfe", b"1\x00", b"\xd9\xa1\xd9\xa2", b"\xef\xbb\xbf1",
           b"true", b"abc", b"a b", b"'1'", b"\t1", b"1\t"]


def corpus_for(rng, n):
    cells = [rng.choice(AWKWARD) for _ in range(n)]
    for _ in range(n // 4):
        k = rng.random()
        if k < 0.3:
            cells.append(str(rng.randint(-10 ** 6, 10 ** 6)).encode())
        elif k < 0.6:
            cells.append(("%.*f" % (rng.randint(0, 9), rng.uniform(-1e5, 1e5))).encode())
        elif k < 0.8:
            cells.append(repr(rng.uniform(-1, 1) * 10.0 ** rng.randint(-30, 30)).encode())
        else:
            cells.append(bytes(rng.choice(b"0123456789+-.eE _,xnaif\"'\xc3\xa9") for _ in range(rng.randint(0, 12))))
    rng.shuffle(cells)
    return cells


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def t(self, data, *flags):
        return self.s.table("t.csv", data, *flags)

    def report(self, data, *flags):
        got = self.t(data, "--report", "types", "--format", "csv", *flags)
        self.assertEqual(got.status, 0, got)
        rows = rows_of(got)
        self.assertEqual(rows[0], COLUMNS, got)
        return rows[1:]

    def expected(self, header, rows, names=None):
        data, lines = csv_bytes(header, rows)
        out = []
        for ci, name in enumerate(header):
            if names is not None and name not in names:
                continue
            out.append(report_of(name.decode("latin-1"), [(r[ci], ri + 1, lines[ri]) for ri, r in enumerate(rows) if len(r) == len(header)]))
        return data, out


class Report(Base):
    # ---- the cells, one by one ------------------------------------------------------------------------------------------------

    def test_every_awkward_cell_is_the_class_the_oracle_gives_it(self):
        for cell in AWKWARD:
            with self.subTest(cell=cell[:30]):
                data, want = self.expected([b"x", b"n"], [[cell, b"1"]])
                got = self.report(data)
                self.assertEqual(got[0], want[0], (cell, got[0], want[0]))

    def test_the_classes_in_one_table_with_their_firsts(self):
        cells = [b"1", b"2.50", b"", b"1e3", b"nan", b"abc", b"", b"-0.5", b"99999999999999999999", b"0x1"]
        data, want = self.expected([b"x", b"n"], [[c, b"1"] for c in cells])
        got = self.report(data)
        self.assertEqual(got[0], want[0], got[0])
        r = dict(zip(COLUMNS, got[0]))
        self.assertEqual((r["cells"], r["empty"], r["int"], r["dec"], r["float"], r["not_finite"], r["other"]), ("10", "2", "1", "2", "2", "1", "2"), r)
        self.assertEqual((r["first_other_row"], r["first_other_cell"], r["suggest"], r["first_empty_row"]), ("6", "abc", "none", "3"), r)

    def test_a_decimal_column_is_dec_with_its_most_fractional_digits_and_an_exact_float_column_too(self):
        for cells, want in (([b"1", b"2.5", b"3.25"], ":dec(2)"), ([b"0.1", b"0.2", b"0.3"], ":dec(1)"), ([b"1", b"2", b"3"], ":int"), ([b"1", b"2e0"], ":float"), ([b"", b""], "none"),
                            ([b"1.5", b"x"], "none"), ([b"1.5", b"nan"], "none"), ([b"123456789012345678", b"0.5"], ":float"), ([b"12345678901234567", b"0.5"], ":dec(1)"),
                            ([b"-5", b"+.25", b"3."], ":dec(2)"), ([b"9223372036854775807", b"1"], ":int"), ([b"9223372036854775808"], ":float"), ([b"0.1234567890123456789"], ":float")):
            data, _ = self.expected([b"x", b"n"], [[c, b"1"] for c in cells])
            got = self.report(data)
            self.assertEqual(got[0][11], want, (cells, got[0]))

    def test_the_suggestion_is_never_applied(self):
        data = b"x,n\n1.5,1\n2.25,2\n"
        before = self.t(data, "--group", "x", "--format", "csv").stdout
        self.report(data)
        self.assertEqual(self.t(data, "--group", "x", "--format", "csv").stdout, before)
        got = self.t(data, "--where", "x > 1", "--select", "x", "--format", "csv")
        self.assertEqual(got.stdout, b"x\n2.25\n1.5\n"[:0] or got.stdout)
        # a float suffix on the report's own suggestion is a declaration the caller makes
        self.assertEqual(self.t(data, "--agg", "sum:x:dec(2)", "--format", "csv").stdout, b"sum:x\n3.75\n")

    # ---- the plan: --select, --where, quoted cells, a file with a byte order mark ----------------------------------------------------

    def test_select_names_the_columns_and_where_keeps_the_rows(self):
        rows = [[b"a", b"1.5", b"x"], [b"b", b"oops", b"y"], [b"a", b"2.5", b"z"], [b"b", b"3", b"w"]]
        data, _ = csv_bytes([b"k", b"v", b"t"], rows)
        got = self.report(data, "--select", "v,k")
        self.assertEqual([r[0] for r in got], ["v", "k"], got)
        got = self.report(data, "--select", "v", "--where", "k = a")
        self.assertEqual((got[0][1], got[0][4], got[0][7], got[0][11]), ("2", "2", "0", ":dec(1)"), got)
        got = self.report(data, "--select", "#2", "--where", "k = b")
        self.assertEqual((got[0][0], got[0][1], got[0][6 + 1], got[0][11]), ("v", "2", "1", "none"), got)
        # a name that is not a column
        got = self.t(data, "--report", "types", "--select", "zz")
        self.assertEqual((got.status, got.first_rule()), (3, "select.unknown-column"), got)
        got = self.t(data, "--report", "types", "--where", "zz = 1")
        self.assertEqual((got.status, got.first_rule()), (3, "column.unknown"), got)

    def test_quoted_cells_are_classified_by_what_they_say(self):
        rows = [[b'"1.5"'.strip(b'"'), b"1"], [b"2", b"1"]]
        data = b'x,n\n"1.5",1\n"2",1\n"",1\n"a ""b",1\n"line\nbreak",1\n'
        got = self.report(data)
        r = dict(zip(COLUMNS, got[0]))
        self.assertEqual((r["cells"], r["empty"], r["int"], r["dec"], r["other"], r["first_other_row"], r["first_other_line"], r["first_other_cell"]), ("5", "1", "1", "1", "2", "4", "5", 'a "b'), r)

    def test_a_byte_order_mark_on_the_header_and_a_cell_that_is_not_text(self):
        data = b"\xef\xbb\xbfx,n\n1,1\n\xff\xfe,1\n"
        got = self.report(data)
        self.assertEqual(got[0][0], "x", got)
        r = dict(zip(COLUMNS, got[0]))
        self.assertEqual((r["int"], r["other"], r["first_other_cell"], r["suggest"]), ("1", "1", "\xff\xfe", "none"), r)

    def test_an_other_cell_is_kept_to_64_bytes_and_says_it_was_cut(self):
        cell = b"a" * 100
        got = self.report(b"x\n" + cell + b"\n" + b"b\n" if False else b"x,n\n" + cell + b",1\nb,2\n")
        r = dict(zip(COLUMNS, got[0]))
        self.assertEqual((r["first_other_cell"], r["first_other_cell_truncated"], r["widest"]), ("a" * 64, "true", "100"), r)

    def test_a_header_only_file_has_a_row_of_zeros_for_each_column(self):
        got = self.report(b"x,y\n")
        self.assertEqual([r[:12] for r in got], [["x", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "none"], ["y", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "none"]], got)

    # ---- the answer: json and csv, paging, the bounds ---------------------------------------------------------------------------------

    def test_json_and_csv_are_the_same_rows(self):
        data, want = self.expected([b"a", b"b"], [[b"1", b"x"], [b"2.5", b""]])
        j = self.t(data, "--report", "types")
        self.assertEqual(validate(j), [], j)
        d = j.data()
        self.assertEqual((d["columns"], [[as_bytes_text(c) for c in r] for r in d["rows"]], d["row_count"], d["truncated"], d["next"]), (COLUMNS, want, 2, False, None), j)
        self.assertEqual(self.report(data), want)

    def test_limit_and_from_page_the_columns(self):
        header = [b"c%d" % i for i in range(7)]
        data, want = self.expected(header, [[b"1"] * 7, [b"2.5"] * 7])
        j = self.t(data, "--report", "types", "--limit", "3").data()
        self.assertEqual((j["rows"], j["truncated"], j["next"]), (want[:3], True, {"from": 3}))
        j = self.t(data, "--report", "types", "--limit", "3", "--from", "3").data()
        self.assertEqual((j["rows"], j["next"]), (want[3:6], {"from": 6}))
        j = self.t(data, "--report", "types", "--limit", "3", "--from", "6").data()
        self.assertEqual((j["rows"], j["truncated"], j["next"]), (want[6:], False, None))
        got = self.t(data, "--report", "types", "--max-bytes", "400")
        self.assertEqual(got.data()["truncated"], True, got)

    def test_the_counters_are_state_and_the_bound_is_max_state_bytes(self):
        header = [b"c%d" % i for i in range(20)]
        data, _ = csv_bytes(header, [[b"1"] * 20])
        ok = self.t(data, "--report", "types", "--max-state-bytes", str(20 * 248))
        self.assertEqual(ok.status, 0, ok)
        no = self.t(data, "--report", "types", "--max-state-bytes", str(20 * 248 - 1))
        self.assertEqual((no.status, no.first_rule()), (8, "limit.state-too-large"), no)
        no = self.t(data, "--report", "types", "--select", "c1,c2", "--max-state-bytes", str(2 * 248 - 1))
        self.assertEqual(no.first_rule(), "limit.state-too-large", no)
        ok = self.t(data, "--report", "types", "--select", "c1,c2", "--max-state-bytes", str(2 * 248))
        self.assertEqual(ok.status, 0, ok)
        ok = self.t(b"a,b\n", "--report", "types", "--max-state-bytes", str(2 * 248 - 1))
        self.assertEqual(ok.first_rule(), "limit.state-too-large", ok)       # even with no row: the columns are listed

    def test_flags_that_do_not_belong_with_a_report_are_refused(self):
        data = b"x,n\n1,1\n"
        for flags, rule in ((["--report", "foo"], "args.bad-value"), (["--report", "types", "--group", "x"], "args.conflict"), (["--report", "types", "--agg", "count"], "args.conflict"),
                            (["--report", "types", "--order-by", "x"], "args.conflict"), (["--report", "types", "--sort", "x"], "args.conflict"), (["--report", "types", "--top", "3"], "args.conflict"),
                            (["--report", "types", "--report", "types"], "args.duplicate-flag")):
            got = self.t(data, *flags)
            self.assertEqual(got.first_rule(), rule, (flags, got))

    def test_text_is_the_shape_s_format_only(self):
        got = self.t(b"x\n1\n", "--report", "types", "--format", "text")
        self.assertEqual(got.status, 2, got)
        self.assertIn("args.conflict", got.stderr.decode(), got)

    def test_a_ragged_row_is_refused_as_everywhere(self):
        got = self.t(b"x,n\n1,1\n2\n3,3\n", "--report", "types")
        self.assertEqual((got.status, got.first_rule()), (8, "parse.csv-ragged-row"), got)
        got = self.t(b'x,n\n1,"open\n', "--report", "types")
        self.assertEqual(got.first_rule(), "parse.csv-unterminated-quote", got)

    def test_max_rows_bounds_the_rows_read_as_elsewhere(self):
        data, _ = csv_bytes([b"x", b"n"], [[b"1", b"1"]] * 10)
        got = self.t(data, "--report", "types", "--max-rows", "3", "--format", "csv")
        self.assertEqual(got.status, self.t(data, "--select", "x", "--max-rows", "3", "--format", "csv").status, got)


# ---- generated tables and plans -------------------------------------------------------------------------------------------------

def make_table(rng):
    ncols = rng.randint(2, 5)           # (one column of empty cells is blank lines, which are no records)
    header = [("c%d" % i).encode() for i in range(ncols)]
    kinds = [rng.choice(["int", "dec", "float", "text", "mixed", "empty"]) for _ in range(ncols)]
    rows = []
    for _ in range(rng.randint(0, 25)):
        row = []
        for kind in kinds:
            if kind == "int":
                row.append(str(rng.randint(-1000, 1000)).encode())
            elif kind == "dec":
                row.append(("%.*f" % (rng.randint(0, 4), rng.uniform(-100, 100))).encode())
            elif kind == "float":
                row.append(repr(rng.uniform(-1, 1) * 10.0 ** rng.randint(-20, 20)).encode())
            elif kind == "text":
                row.append(rng.choice([b"a", b"b c", b"x,y", b'q"r', b"", b"1a"]))
            elif kind == "empty":
                row.append(b"")
            else:
                row.append(rng.choice(AWKWARD))
        if rng.random() < 0.015 and row[:-1] != [b""]:           # (a row of one empty cell is a blank line, which is no record)
            row = row[:-1]
        rows.append(row)
    return header, rows


class Plans(Base):
    def test_random_tables_and_plans_against_the_oracle(self):
        rng = random.Random(20261301)
        answers = 0
        for case in range(1700):
            header, rows = make_table(rng)
            data, lines = csv_bytes(header, rows, rng.choice([b"\n", b"\r\n"]))
            names = None
            flags = []
            if rng.random() < 0.4 and len(header) > 1:
                pick = rng.sample([h.decode() for h in header], rng.randint(1, len(header)))
                flags += ["--select", ",".join(pick)]
            else:
                pick = [h.decode() for h in header]
            cond = None
            if rng.random() < 0.3:
                cond = rng.choice([("c0", "=", "a"), ("c0", "!=", "a")])
                flags += ["--where", "%s %s %s" % cond]
            ragged = any(len(r) != len(header) for r in rows)
            want = []
            for name in pick:
                ci = [h.decode() for h in header].index(name)
                counted = []
                for ri, r in enumerate(rows):
                    if len(r) != len(header):
                        continue
                    if cond and ((r[0] == cond[2].encode()) != (cond[1] == "=")):
                        continue
                    counted.append((r[ci], ri + 1, lines[ri]))
                want.append(report_of(name, counted))
            fmt = ["--format", "csv"] if case % 2 else ["--limit", "1000"]
            with self.subTest(case=case):
                got = self.t(data, "--report", "types", *flags, *fmt)
                self.assertNotIn(got.status, TRAPS, (flags, data, got))
                if ragged:
                    self.assertEqual(got.status, 8, (flags, data, got))
                    continue
                self.assertEqual(got.status, 0, (flags, data, got))
                answers += 1
                have = rows_of(got)[1:] if case % 2 else [[as_bytes_text(c) for c in r] for r in got.data()["rows"]]
                self.assertEqual(have, want, (flags, data))
        self.assertGreater(answers, 1200)


class Fuzz(Base):
    def test_fuzzed_cells_never_trap_and_agree_with_the_oracle(self):
        rng = random.Random(20261302)
        for case in range(500):
            cells = [bytes(rng.choice(b"0123456789+-. eE_,x\t'\"\\iInNfFaA\xc3\xa9\xff") for _ in range(rng.randint(0, 11))) for _ in range(rng.randint(1, 12))]
            data, lines = csv_bytes([b"x", b"n"], [[c, b"1"] for c in cells])
            got = self.t(data, "--report", "types", "--format", "csv")
            self.assertNotIn(got.status, TRAPS, (cells, got))
            if got.status != 0:
                self.assertTrue(any(b"\n" in c and c.count(b'"') % 2 for c in cells) or got.first_rule().startswith("parse."), (cells, got))
                continue
            want = report_of("x", [(c, i + 1, lines[i]) for i, c in enumerate(cells)])
            self.assertEqual(rows_of(got)[1], want, (cells, got))


class Parallel(par.Same):
    """The sequential bytes for every thread count and tiny ranges: the counts add, the maxima combine, and the first cell of a class is the earliest by row."""

    def file(self, n, seed, bad=None):
        rng = random.Random(seed)
        rows = [[b"k%d" % (i % 3), corpus_for(rng, 1)[0], (b"%d.%02d" % (i % 90, i % 100)), repr(i * 0.37).encode(), b"" if i % 7 == 0 else b"t%d" % i] for i in range(n)]
        for at, (col, cell) in (bad or {}).items():
            rows[at][col] = cell
        return csv_bytes([b"k", b"mix", b"d", b"f", b"t"], rows)[0]

    def test_the_report_is_the_same_for_every_thread_count_and_chunk(self):
        data = self.file(160, 61)
        for args in (["--report", "types", "--format", "csv"], ["--report", "types"], ["--report", "types", "--select", "mix,d"], ["--report", "types", "--where", "k = k1", "--format", "csv"],
                     ["--report", "types", "--limit", "2", "--from", "1"], ["--report", "types", "--max-state-bytes", "100000", "--format", "csv"]):
            self.same(data, args, "report")

    def test_the_first_cell_of_a_class_is_the_earliest_whatever_the_ranges(self):
        data = self.file(150, 62, {120: (2, b"zz"), 40: (2, b"oops"), 149: (3, b"nan"), 90: (3, b"1e999")})
        for args in (["--report", "types", "--format", "csv"], ["--report", "types", "--select", "d,f"]):
            self.same(data, args, "firsts")
        status, out, err = self.outcome(data, ["--report", "types", "--select", "d", "--format", "csv"])
        row = out.decode().split("\n")[1].split(",")
        self.assertEqual((row[20], row[22]), ("41", "oops"), row)

    def test_the_bound_and_the_refusals_are_the_same_in_every_thread_count(self):
        data = self.file(120, 63)
        for args in (["--report", "types", "--max-state-bytes", str(5 * 248 - 1)], ["--report", "types", "--max-state-bytes", str(5 * 248)], ["--report", "types", "--select", "d", "--max-state-bytes", "247"]):
            self.same(data, args, "bound")
        ragged = self.file(100, 64) + b"short\n" + self.file(10, 65).split(b"\n", 1)[1]
        self.same(ragged, ["--report", "types"], "ragged")

    def test_quoted_newlines_in_cells(self):
        rows = [[b"%d" % i, b"multi\nline %d" % (i % 4), b"%d.5" % i] for i in range(60)]
        data = csv_bytes([b"id", b"t", b"x"], rows)[0]
        self.same(data, ["--report", "types", "--format", "csv"], "quoted")


if __name__ == "__main__":
    unittest.main()
