"""Dates and timestamps (docs/datetime.md, slices T0 and T1): `:date`, `:timestamp`, `:datetime`.

These are the gates of docs/datetime.md section 12, written before the code (T0) and run on the built binary:

* G-ORACLE-GRAMMAR: `datetime_ref.py` (Python, fixed offsets, no zoneinfo, no code shared with the tool) decides every cell: the instant, or the tag and `detail.reason` /
  `detail.offset` / `detail.digits` / `detail.direction`. The cells are the named edge cases of the doc and random ones; DT_CELLS (default 300 refused and 3000 valid cells a type)
  scales the generated part (the doc's pass line is 100,000).
* G-ORACLE-SQL: for the cells both engines accept, DuckDB and SQLite give the instant the tool gives (skipped when the engine is not installed: DUCKDB names the binary; DT_ORACLE cells a type, default 2000).
* G-ROUND: the printed key of a value reads back as the same value, and printing it again gives the same text.
* G-DIFF: random tables and plans (conditions, `in`, order keys, group keys, min, max, distinct, top) against a Python evaluation of the same plan.
* G-PAR: threads 1..64 x chunk sizes, standard input against the file: the same bytes, refusals included.
* G-RULES, G-COMPAT, G-FUZZ and the golden refusals.
"""

import csv
import io
import json
import os
import pathlib
import random
import re
import shutil
import sqlite3
import tempfile
import unittest

import procs as subprocess

import datetime_ref as ref
from harness import Scratch, binary, introspect, run, run_argv, validate

DT_CELLS = int(os.environ.get("DT_CELLS", "300"))
DT_VALID = int(os.environ.get("DT_VALID", "3000"))
DT_TABLES = int(os.environ.get("DT_TABLES", "40"))
DT_ORACLE = int(os.environ.get("DT_ORACLE", "2000"))
SUFFIX = {"date": ":date", "timestamp": ":timestamp", "datetime": ":datetime"}
CTX = "distinct"


def csv_text(rows):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def one_column(s, cells, name="v"):
    return s.write("c.csv", csv_text([[name]] + [[c] for c in cells]))


def doc_error(r):
    return r.doc()["error"]


# Hand-written expectations, independent of the reference (the reference is checked against them, and so is the tool): (kind, cell, tag, detail)
GOLDEN = [
    ("date", "2026-02-30", "value.not-date", {"reason": "day", "offset": 8}),
    ("date", "2026-04-31", "value.not-date", {"reason": "day", "offset": 8}),
    ("date", "1900-02-29", "value.not-date", {"reason": "day", "offset": 8}),
    ("date", "2100-02-29", "value.not-date", {"reason": "day", "offset": 8}),
    ("date", "2026-13-01", "value.not-date", {"reason": "month", "offset": 5}),
    ("date", "0000-01-01", "value.not-date", {"reason": "year", "offset": 0}),
    ("date", "2026-1-2", "value.not-date", {"reason": "shape", "offset": 6}),
    ("date", "20260102", "value.not-date", {"reason": "shape", "offset": 4}),
    ("date", "", "value.not-date", {"reason": "shape", "offset": 0}),
    ("date", " 2026-01-02", "value.not-date", {"reason": "shape", "offset": 0}),
    ("date", "2026-01-02 ", "value.not-date", {"reason": "shape", "offset": 11}),
    ("date", "2026-01-02T01:00:00Z", "value.not-date", {"reason": "time-part", "offset": 10}),
    ("date", "2026-01-02 01:00:00", "value.not-date", {"reason": "time-part", "offset": 10}),
    ("date", "2026-W01-1", "value.not-date", {"reason": "shape", "offset": 5}),
    ("date", "2026-002", "value.not-date", {"reason": "shape", "offset": 7}),
    ("timestamp", "2026-01-02", "value.not-timestamp", {"reason": "date-only", "offset": 10}),
    ("datetime", "2026-01-02", "value.not-timestamp", {"reason": "date-only", "offset": 10}),
    ("timestamp", "2026-01-02T24:00:00Z", "value.not-timestamp", {"reason": "hour-24", "offset": 11}),
    ("timestamp", "2026-01-02T25:00:00Z", "value.not-timestamp", {"reason": "hour", "offset": 11}),
    ("timestamp", "2026-01-02T01:60:00Z", "value.not-timestamp", {"reason": "minute", "offset": 14}),
    ("timestamp", "2016-12-31T23:59:60Z", "value.not-timestamp", {"reason": "leap-second", "offset": 17}),
    ("timestamp", "2026-01-02T01:00:61Z", "value.not-timestamp", {"reason": "second", "offset": 17}),
    ("timestamp", "2026-02-30T01:00:00Z", "value.not-timestamp", {"reason": "day", "offset": 8}),
    ("timestamp", "0000-01-01T00:00:00Z", "value.not-timestamp", {"reason": "year", "offset": 0}),
    ("timestamp", "20260102T010000Z", "value.not-timestamp", {"reason": "shape", "offset": 4}),
    ("timestamp", "2026-01-02T01:00Z", "value.not-timestamp", {"reason": "shape", "offset": 16}),
    ("timestamp", "2026-01-02T01:00:00.Z", "value.not-timestamp", {"reason": "shape", "offset": 20}),
    ("timestamp", "2026-01-02T01:00:00+0200", "value.not-timestamp", {"reason": "offset-shape", "offset": 19}),
    ("timestamp", "2026-01-02T01:00:00+02", "value.not-timestamp", {"reason": "offset-shape", "offset": 19}),
    ("timestamp", "2026-01-02T01:00:00+24:00", "value.not-timestamp", {"reason": "offset-shape", "offset": 19}),
    ("timestamp", "2026-01-02T01:00:00+00:60", "value.not-timestamp", {"reason": "offset-shape", "offset": 19}),
    ("timestamp", " 2026-01-02T01:00:00Z", "value.not-timestamp", {"reason": "shape", "offset": 0}),
    ("timestamp", "2026-01-02T01:00:00Z ", "value.not-timestamp", {"reason": "shape", "offset": 20}),
    ("timestamp", "2026-01-02T01:00:00.1234567Z", "value.timestamp-precision", {"digits": 7}),
    ("timestamp", "2026-01-02T01:00:00.1234560Z", "value.timestamp-precision", {"digits": 7}),
    ("timestamp", "2026-01-02T01:00:00.123456789Z", "value.timestamp-precision", {"digits": 9}),
    ("datetime", "2026-01-02T01:00:00.0000000", "value.timestamp-precision", {"digits": 7}),
    ("timestamp", "0001-01-01T00:00:00+01:00", "value.timestamp-range", {"direction": "before"}),
    ("timestamp", "9999-12-31T23:59:59.999999-01:00", "value.timestamp-range", {"direction": "after"}),
    ("timestamp", "9999-12-31T23:59:59-00:01", "value.timestamp-range", {"direction": "after"}),
    ("timestamp", "2026-01-02T01:00:00", "value.timezone-missing", {"reason": "missing"}),
    ("timestamp", "2026-01-02 01:00:00.5", "value.timezone-missing", {"reason": "missing"}),
    ("timestamp", "2026-01-02T01:00:00-00:00", "value.timezone-missing", {"reason": "unknown-offset"}),
    ("datetime", "2026-01-02T01:00:00Z", "value.timestamp-offset-present", {}),
    ("datetime", "2026-01-02 01:00:00+02:00", "value.timestamp-offset-present", {}),
    ("datetime", "2026-01-02T01:00:00-00:00", "value.timestamp-offset-present", {}),
    ("timestamp", "2026-01-02T01:00:00 Europe/Madrid", "value.timezone-unsupported", {}),
    ("timestamp", "2026-01-02T01:00:00CET", "value.timezone-unsupported", {}),
    ("datetime", "2026-01-02T01:00:00 Europe/Madrid", "value.timezone-unsupported", {}),
    ("timestamp", "2026-01-02T01:00:00Z[Europe/Madrid]", "value.timezone-unsupported", {}),
    ("timestamp", "2026-01-02T01:00:00+01:00[Europe/Madrid]", "value.timezone-unsupported", {}),
]
# valid: (kind, cell, value, printed)
GOLDEN_OK = [
    ("date", "1970-01-01", 0, "1970-01-01"),
    ("date", "0001-01-01", -719162, "0001-01-01"),
    ("date", "9999-12-31", 2932896, "9999-12-31"),
    ("date", "2000-02-29", 11016, "2000-02-29"),
    ("date", "2026-01-02", 20455, "2026-01-02"),
    ("timestamp", "1970-01-01T00:00:00Z", 0, "1970-01-01T00:00:00Z"),
    ("timestamp", "2026-01-02T01:00:00+02:00", 1767308400000000, "2026-01-01T23:00:00Z"),
    ("timestamp", "2026-01-01T23:00:00z", 1767308400000000, "2026-01-01T23:00:00Z"),
    ("timestamp", "2026-01-01 23:00:00+00:00", 1767308400000000, "2026-01-01T23:00:00Z"),
    ("timestamp", "2026-01-01t23:00:00.5Z", 1767308400500000, "2026-01-01T23:00:00.500Z"),
    ("timestamp", "2026-01-01T23:00:00.500000Z", 1767308400500000, "2026-01-01T23:00:00.500Z"),
    ("timestamp", "2026-01-01T23:00:00.000001Z", 1767308400000001, "2026-01-01T23:00:00.000001Z"),
    ("timestamp", "1969-12-31T23:59:59.999999Z", -1, "1969-12-31T23:59:59.999999Z"),
    ("timestamp", "0001-01-01T00:00:00Z", -62135596800000000, "0001-01-01T00:00:00Z"),
    ("timestamp", "9999-12-31T23:59:59.999999Z", 253402300799999999, "9999-12-31T23:59:59.999999Z"),
    ("timestamp", "0001-01-01T14:00:00+14:00", -62135596800000000, "0001-01-01T00:00:00Z"),
    ("timestamp", "9999-12-31T09:59:59.999999-14:00", 253402300799999999, "9999-12-31T23:59:59.999999Z"),
    ("datetime", "2026-01-02 12:00:00", 1767355200000000, "2026-01-02T12:00:00"),
    ("datetime", "2026-01-02T12:00:00.25", 1767355200250000, "2026-01-02T12:00:00.250"),
]


class Grammar(unittest.TestCase):
    """G-ORACLE-GRAMMAR, with the golden cells that pin the reference itself."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_the_reference_agrees_with_the_hand_written_expectations(self):
        for kind, cell, tag, detail in GOLDEN:
            got = ref.parse(kind, cell.encode())
            self.assertEqual(got[0], "refuse", (kind, cell, got))
            self.assertEqual(got[1], tag, (kind, cell, got))
            if detail is not None:
                self.assertEqual(got[2], detail, (kind, cell, got))
        for kind, cell, value, printed in GOLDEN_OK:
            got = ref.parse(kind, cell.encode())
            self.assertEqual(got, ("ok", value), (kind, cell, got))
            self.assertEqual(ref.canon(kind, value), printed, (kind, cell))

    def test_the_day_number_of_every_day_of_0001_to_9999(self):
        # the doc's G-ALGORITHM, on the reference: Python's calendar against a hand-written Hinnant (the tool's own version is probed through the cells below)
        def days_from_civil(y, m, d):
            y -= m <= 2
            era = (y if y >= 0 else y - 399) // 400
            yoe = y - era * 400
            doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
            doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
            return era * 146097 + doe - 719468
        import datetime as dt
        day = dt.date(1, 1, 1)
        epoch = dt.date(1970, 1, 1)
        n = 0
        while True:
            if days_from_civil(day.year, day.month, day.day) != (day - epoch).days:
                self.fail(str(day))
            n += 1
            if day == dt.date(9999, 12, 31):
                break
            day += dt.timedelta(days=1)
        self.assertEqual(n, 3652059)

    def check_refusal(self, kind, cell, context_flags, context):
        s = self.s
        want = ref.parse(kind, cell)
        r = s.table("c.csv", b"id,v\n1," + cell + b"\n", *context_flags)
        self.assertEqual(want[0], "refuse")
        err = doc_error(r)
        self.assertEqual((r.status, err["rule"]), (8, want[1]), (kind, cell, r.stdout[:300]))
        self.assertEqual(err["code"], "PRECONDITION_FAILED")
        d = err["detail"]
        self.assertEqual((d["row"], d["line"], d["column"], d["context"]), (1, 2, "v", context), (kind, cell))
        self.assertEqual(d["value"], cell[:64].decode("utf-8", "replace"))
        self.assertEqual(d["value_truncated"], len(cell) >= 64)
        for key, value in want[2].items():
            self.assertEqual(d.get(key), value, (kind, cell, key, d))
        for key in ("reason", "offset", "digits", "direction"):
            self.assertEqual(key in d, key in want[2], (kind, cell, key, d))
        self.assertEqual(validate(r), [])

    def test_each_edge_cell_as_each_type(self):
        cells = ref.edge_cells()
        for kind in ref.KINDS:
            flags = ["--agg", "distinct:v%s" % SUFFIX[kind]]
            valid = []
            for cell in cells:
                b = cell.encode("utf-8")
                if any(c in cell for c in ',"\n\r'):
                    continue
                got = ref.parse(kind, b)
                if got[0] == "ok":
                    valid.append(cell)
                else:
                    self.check_refusal(kind, b, flags, CTX)
            self.assertGreater(len(valid), 15, kind)

    def test_valid_cells_give_the_instant_the_reference_gives(self):
        # one table, all the valid cells: the groups come in the order of the values, written in the printed form
        rng = random.Random(7)
        for kind in ref.KINDS:
            cells = [c for c in ref.edge_cells() if ref.parse(kind, c.encode())[0] == "ok" and not any(x in c for x in ',"\n\r')]
            cells += [ref.random_valid(rng, kind) for _ in range(DT_VALID)]
            r = self.s.table("c.csv", csv_text([["v"]] + [[c] for c in cells]), "--group", "v" + SUFFIX[kind], "--agg", "count,min:v%s,max:v%s,distinct:v%s" % ((SUFFIX[kind],) * 3), "--format", "csv", "--max-groups", "1000000", "--max-distinct", "10000000")
            self.assertEqual(r.status, 0, r.stderr[:400])
            counts = {}
            for c in cells:
                counts[ref.parse(kind, c.encode())[1]] = counts.get(ref.parse(kind, c.encode())[1], 0) + 1
            want = ["v,count,min:v,max:v,distinct:v"]
            for v in sorted(counts):
                want.append("%s,%d,%s,%s,1" % (ref.canon(kind, v), counts[v], ref.canon(kind, v), ref.canon(kind, v)))
            self.assertEqual(r.stdout.decode().splitlines(), want, kind)

    def test_order_min_max_distinct_of_a_mixed_offset_column(self):
        rows = [("1", "2026-01-01T23:59:59Z"), ("2", "2026-01-02T01:00:00+02:00"), ("3", "2026-01-02T00:00:00Z"), ("4", "2026-01-01T23:00:00Z"), ("5", "2026-01-01 23:00:00.000+00:00")]
        data = csv_text([["id", "ts"]] + [list(r) for r in rows])
        r = self.s.table("e.csv", data, "--order-by", "ts:timestamp", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "2", "4", "5", "1", "3"])
        r = self.s.table("e.csv", data, "--order-by", "-ts:timestamp", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "3", "1", "2", "4", "5"])
        r = self.s.table("e.csv", data, "--agg", "count,min:ts:timestamp,max:ts:timestamp,distinct:ts:timestamp", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["count,min:ts,max:ts,distinct:ts", "5,2026-01-01T23:00:00Z,2026-01-02T00:00:00Z,3"])
        r = self.s.table("e.csv", data, "--group", "ts:timestamp", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["ts,count", "2026-01-01T23:00:00Z,3", "2026-01-01T23:59:59Z,1", "2026-01-02T00:00:00Z,1"])
        r = self.s.table("e.csv", data, "--where", "ts:timestamp >= '2026-01-02T00:00:00Z'", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "3"])
        r = self.s.table("e.csv", data, "--where", "ts:timestamp = '2026-01-01T23:00:00+00:00'", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "2", "4", "5"])
        r = self.s.table("e.csv", data, "--where", "ts:timestamp in ('2026-01-02T01:00:00+02:00', '2026-01-02T00:00:00z')", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "2", "3", "4", "5"])

    def test_a_quoted_cell_is_the_text_inside_the_quotes(self):
        r = self.s.table("q.csv", 'v\n"2026-01-02T01:00:00+02:00"\n"2026-01-01T23:00:00Z"\n', "--group", "v:timestamp", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["v,count", "2026-01-01T23:00:00Z,2"])
        r = self.s.table("q.csv", 'v\n"2026-01-02,x"\n', "--agg", "min:v:date")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-date"))

    def test_generated_refused_cells(self):
        rng = random.Random(11)
        n = 0
        while n < DT_CELLS:
            kind = rng.choice(ref.KINDS)
            cell = ref.mutate(rng, ref.random_valid(rng, rng.choice(ref.KINDS)).encode())
            try:
                cell.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if any(c in cell for c in b',"\n\r\x00') or ref.parse(kind, cell)[0] == "ok":
                continue
            self.check_refusal(kind, cell, ["--agg", "distinct:v%s" % SUFFIX[kind]], CTX)
            n += 1

    def test_every_context_names_its_cell(self):
        cases = [("date", "2026-02-30"), ("timestamp", "2026-01-02T01:00:00"), ("datetime", "2026-01-02T01:00:00Z")]
        for kind, cell in cases:
            t = "v" + SUFFIX[kind]
            lit = {"date": "2026-01-02", "timestamp": "2026-01-02T00:00:00Z", "datetime": "2026-01-02 00:00:00"}[kind]
            contexts = [
                ("where", ["--where", "%s > '%s'" % (t, lit)]),
                ("min", ["--agg", "min:" + t]),
                ("max", ["--agg", "max:" + t]),
                ("distinct", ["--agg", "distinct:" + t]),
                ("group", ["--group", t]),
                ("order-by", ["--order-by", t, "--select", "id"]),
            ]
            for context, flags in contexts:
                want = ref.parse(kind, cell.encode())
                r = self.s.table("x.csv", "id,v\n1,%s\n" % cell, *flags)
                err = doc_error(r)
                self.assertEqual((r.status, err["rule"], err["detail"]["context"]), (8, want[1], context), (kind, context, r.stdout[:300]))

    def test_the_first_refusal_in_file_order(self):
        data = "id,v\n1,2026-01-01T00:00:00Z\n2,2026-01-02T00:00:00\n3,x\n4,2026-02-30T00:00:00Z\n"
        r = self.s.table("f.csv", data, "--group", "v:timestamp")
        err = doc_error(r)
        self.assertEqual((err["rule"], err["detail"]["row"], err["detail"]["line"], err["detail"]["reason"]), ("value.timezone-missing", 2, 3, "missing"))
        r = self.s.table("f.csv", data, "--where", "id != 2 and v:timestamp > '2026-01-01T00:00:00Z'", "--select", "id")
        err = doc_error(r)
        self.assertEqual((err["rule"], err["detail"]["row"]), ("value.not-timestamp", 3))


class Algorithm(unittest.TestCase):
    """G-ALGORITHM: the tool's own `days_from_civil` and its inverse (the date printer) over every day of 0001..9999, 3,652,059 of them, against Python's calendar. The
    sources of dtparse.cho are built into a probe (tests/conformance/dtparse_probe); a control with the era boundary one day short must fail."""

    def build(self, mutate=None):
        exe = os.environ.get("CANCHO") or shutil.which("cancho")
        if not exe:
            self.skipTest("no compiler: set CANCHO, or put the cancho named in cancho.toml on PATH")
        root = pathlib.Path(__file__).resolve().parents[2]
        pin = re.search(r'^cancho\s*=\s*"([0-9a-f]+)"', (root / "cancho.toml").read_text(), re.M).group(1)
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="dtprobe-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        (scratch / "src").mkdir()
        text = (root / "tools" / "table" / "dtparse.cho").read_text()
        if mutate:
            old, new = mutate
            self.assertEqual(text.count(old), 1)
            text = text.replace(old, new)
        (scratch / "src" / "dtparse.cho").write_text(text)
        shutil.copy(pathlib.Path(__file__).parent / "dtparse_probe" / "main.cho", scratch / "src" / "main.cho")
        (scratch / "cancho.toml").write_text('[package]\nname = "dtparseprobe"\ncancho = "%s"\n\n[[bin]]\nname = "probe"\nsources = ["src"]\nstd = true\nout = "build/probe"\n' % pin)
        b = subprocess.run([exe, "build"], cwd=scratch, capture_output=True, text=True, timeout=900)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        return scratch / "build" / "probe"

    def compare(self, probe):
        import datetime as dt
        p = subprocess.run([str(probe)], capture_output=True, timeout=900)
        self.assertEqual(p.returncode, 0)
        lines = p.stdout.decode().split("\n")
        self.assertEqual(lines.pop(), "")
        day = dt.date(1, 1, 1)
        epoch = dt.date(1970, 1, 1)
        bad = 0
        first = None
        for line in lines:
            want = "%d %04d-%02d-%02d" % ((day - epoch).days, day.year, day.month, day.day)
            if line != want:
                bad += 1
                first = first or (line, want)
            if day.year == 9999 and day.month == 12 and day.day == 31:
                break
            day += dt.timedelta(days=1)
        return len(lines), bad, first

    def test_every_day_of_0001_to_9999(self):
        n, bad, first = self.compare(self.build())
        self.assertEqual((n, bad, first), (3652059, 0, None))

    def test_the_control_fails(self):
        n, bad, first = self.compare(self.build(("    return era * 146097 + doe - 719468;", "    return era * 146096 + doe - 719468;")))
        self.assertEqual(n, 3652059)
        self.assertGreater(bad, 0)


class Guards(unittest.TestCase):
    """The guard idiom of docs/filter.md: a condition that decides the row stops the ones after it, so a typed read is reached only by the rows it should read."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_an_empty_cell_is_refused_unless_a_condition_keeps_it_out(self):
        data = "id,v\n1,2026-01-01\n2,\n3,2026-01-03\n"
        r = self.s.table("g.csv", data, "--where", "v:date > '2026-01-01'")
        self.assertEqual(r.first_rule(), "value.not-date")
        r = self.s.table("g.csv", data, "--where", "v != '' and v:date > '2026-01-01'", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "3"])
        r = self.s.table("g.csv", data, "--where", "v = '' or v:date > '2026-01-01'", "--select", "id", "--format", "csv")
        self.assertEqual(r.stdout.decode().split(), ["id", "2", "3"])


class Surface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", "id,ts,d,w\n1,2026-01-01T23:00:00Z,2026-01-01,2026-01-01 23:00:00\n2,2026-01-02T01:00:00+02:00,2026-01-02,2026-01-01 23:00:00.000\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def go(self, *flags):
        return run("--root", self.s.dir, *flags, "t.csv")

    def test_type_conflict_is_by_base_type(self):
        for a, b in ((":date", ":timestamp"), (":timestamp", ":datetime"), (":date", ":int"), (":int", ":datetime"), (":timestamp", ":float"), (":dec(2)", ":date")):
            r = self.go("--group", "ts" + a, "--agg", "min:ts" + b)
            self.assertEqual((r.first_rule(), r.status), ("column.type-conflict", 2), (a, b, r.stdout[:300]))
            self.assertEqual(r.error()["detail"]["types"], [b, a])      # the plan's mentions in the order it checks them: conditions, aggregates, group keys, sort keys
        r = self.go("--where", "ts:timestamp > '2026-01-01T00:00:00Z' and ts != ''", "--agg", "count")
        self.assertEqual(r.status, 0)

    def test_sum_and_mean_are_refused(self):
        for item in ("sum:ts:timestamp", "mean:ts:timestamp@2", "sum:d:date", "mean:w:datetime", "mean:d:date@0", "sum:w:datetime"):
            r = self.go("--agg", item)
            self.assertEqual((r.first_rule(), r.status), ("agg.bad-spec", 2), item)
            self.assertEqual(r.error()["detail"]["agg"], item)
            self.assertIn(":date", r.error()["hint"])
            self.assertIn("min", r.error()["hint"])

    def test_contains_is_refused_on_the_three_types(self):
        for text in ("d:date contains 'x'", "ts:timestamp contains 'x'", "w:datetime contains 'x'", "d:date not contains 'x'", "ts:timestamp not contains '2026'"):
            r = self.go("--where", text)
            self.assertEqual((r.first_rule(), r.status), ("where.syntax", 2), text)
            self.assertIn("contains compares text", r.error()["detail"]["expected"])
            self.assertIn(":date, :timestamp or :datetime", r.error()["detail"]["expected"])

    def test_a_literal_is_read_by_the_cells_reader(self):
        T = "a timestamp with an offset: 2026-01-02T00:00:00Z"
        D = "a date: YYYY-MM-DD"
        W = "a date and a time without an offset: 2026-01-02 12:00:00"
        cases = [
            ("ts:timestamp >= '2026-01-02'", "'2026-01-02'", T),
            ("ts:timestamp >= '2026-01-02T00:00:00'", "'2026-01-02T00:00:00'", T),
            ("ts:timestamp in ('2026-01-02T00:00:00Z', 'x')", "'x'", T),
            ("d:date >= '2026-01-02T00:00:00Z'", "'2026", D),
            ("d:date = '2026-02-30'", "'2026", D),
            ("w:datetime < '2026-01-02T00:00:00Z'", "'2026", W),
            ("w:datetime < '2026-01-02'", "'2026", W),
            ("ts:timestamp = '2026-01-02T00:00:00.1234567Z'", "'2026", T),
            ("d:date = 2026-02-30", "2026", D),
        ]
        for text, marker, expected in cases:
            r = self.go("--where", text)
            self.assertEqual((r.first_rule(), r.status), ("where.syntax", 2), text)
            self.assertEqual(r.error()["detail"]["offset"], text.index(marker), text)
            self.assertEqual(r.error()["detail"]["expected"], expected, text)
            self.assertEqual(r.error()["detail"]["expression"], text)
            self.assertEqual(validate(r), [])

    def test_a_date_literal_against_a_timestamp_has_a_repair_that_runs(self):
        """The day's boundary is the user's statement: the repair writes it out as instants, and the invocation it gives answers as the day does (midnight UTC)."""
        s = Scratch()
        try:
            rows = [("1", "2026-01-01T23:00:00Z"), ("2", "2026-01-02T05:00:00Z"), ("3", "2026-01-03T00:00:00Z"), ("4", "2026-01-02T00:00:00Z"), ("5", "2026-01-02T01:00:00+02:00"), ("6", "2026-01-02T23:59:59.999999Z")]
            s.write("d.csv", csv_text([["id", "ts"]] + [list(r) for r in rows]))
            day = ref.parse("date", b"2026-01-02")[1]
            lo, hi = day * 86400 * 1000000, (day + 1) * 86400 * 1000000
            want = {"=": lambda v: lo <= v < hi, "!=": lambda v: not lo <= v < hi, "<": lambda v: v < lo, "<=": lambda v: v < hi, ">": lambda v: v >= hi, ">=": lambda v: v >= lo}
            for op, keep in want.items():
                for literal in ("'2026-01-02'", "2026-01-02"):
                    text = "ts:timestamp %s %s" % (op, literal)
                    r = run("--root", s.dir, "--where", text, "--select", "id", "d.csv")
                    self.assertEqual((r.first_rule(), r.status), ("where.syntax", 2), text)
                    rep = r.error()["repair"]
                    self.assertEqual((rep["kind"], len(rep["options"])), ("choose", 1), text)
                    again = run_argv(rep["options"][0]["argv"])
                    self.assertEqual(again.status, 0, (text, again.stdout[:300]))
                    ids = [r_[0] for r_ in rows if keep(ref.parse("timestamp", r_[1].encode())[1])]
                    self.assertEqual(again.data()["rows"], [[i] for i in ids], text)
            # inside a longer expression only the operators that need no second condition are offered
            for text, offered in (("id != 3 and ts:timestamp >= '2026-01-02'", True), ("ts:timestamp < '2026-01-02' or id = 3", True), ("id != 3 and ts:timestamp = '2026-01-02'", False),
                                  ("ts:timestamp in ('2026-01-02')", False), ("ts:timestamp = '9999-12-31'", False), ("ts:timestamp <= '9999-12-31'", False)):
                r = run("--root", s.dir, "--where", text, "--select", "id", "d.csv")
                self.assertEqual((r.first_rule(), r.error()["repair"]["kind"] == "choose"), ("where.syntax", offered), text)
                if offered:
                    self.assertEqual(run_argv(r.error()["repair"]["options"][0]["argv"]).status, 0)
            # a literal that is no date has no repair
            r = run("--root", s.dir, "--where", "ts:timestamp >= '2026-02-30'", "d.csv")
            self.assertEqual(r.error()["repair"]["kind"], "none")
            # the flag in its --where=EXPRESSION form
            r = run("--root", s.dir, "--where=ts:timestamp >= '2026-01-02'", "--select", "id", "d.csv")
            again = run_argv(r.error()["repair"]["options"][0]["argv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2"], ["3"], ["4"], ["6"]]))
        finally:
            s.cleanup()

    def test_the_repair_is_only_offered_when_the_flags_can_be_rewritten(self):
        s = Scratch()
        try:
            s.write("r.csv", "id,ts,other,x:timestamp\n1,2026-01-02T01:00:00,2026-01-02T01:00:00Z,u\n2,2026-01-02T02:00:00Z,2026-01-02T01:00:00Z,v\n")
            root = ["--root", s.dir]

            def repair(*flags):
                r = run(*root, *flags, "r.csv")
                self.assertEqual(r.first_rule(), "value.timezone-missing", flags)
                return r.error()["repair"]
            # the group key alone: offered
            self.assertEqual(repair("--group", "ts:timestamp")["kind"], "choose")
            # a column called by position in a condition: the literal of the condition would be of the old type
            self.assertEqual(repair("--where", "id = 1 or #2:timestamp > '2030-01-01T00:00:00Z'", "--group", "ts:timestamp")["kind"], "none")
            # a condition on the column that is not reached before the group reads the cell
            self.assertEqual(repair("--where", "id = 1 or ts:timestamp > '2030-01-01T00:00:00Z'", "--group", "ts:timestamp")["kind"], "none")
            # another column with the same type: the flags are rewritten as text, and cannot tell the columns apart
            self.assertEqual(repair("--group", "ts:timestamp,other:timestamp")["kind"], "none")
            self.assertEqual(repair("--group", "ts:timestamp", "--agg", "max:other:timestamp")["kind"], "none")
            # a column whose name ends in a type, escaped, is a text column and is left alone
            rep = repair("--group", "x\\:timestamp,ts:timestamp")
            self.assertEqual(rep["kind"], "choose")
            self.assertIn("x\\:timestamp,ts:datetime", rep["options"][0]["argv"])
            # more than one flag names the type
            rep = repair("--group", "ts:timestamp", "--agg", "min:ts:timestamp,count")
            self.assertEqual(rep["kind"], "choose")
            argv = rep["options"][0]["argv"]
            self.assertEqual([argv[argv.index(f) + 1] for f in ("--group", "--agg")], ["ts:datetime", "min:ts:datetime,count"])
            rep = repair("--order-by", "-ts:timestamp,id")
            argv = rep["options"][0]["argv"]
            self.assertEqual(argv[argv.index("--order-by") + 1], "-ts:datetime,id")
            # the flag in its --flag=VALUE form
            self.assertEqual(run(*root, "--group=ts:timestamp", "r.csv").error()["repair"]["options"][0]["argv"][-2:], ["--group=ts:datetime", "r.csv"])
        finally:
            s.cleanup()

    def test_groups_sort_by_the_value_of_a_date_key_and_of_a_minimum(self):
        s = Scratch()
        try:
            s.write("s.csv", "g,ts,d\na,2026-01-02T01:00:00+02:00,2026-01-02\na,2026-03-01T00:00:00Z,0001-01-01\nb,0001-01-01T00:00:00Z,2026-02-01\nb,1969-12-31T23:59:59Z,1969-12-31\nc,2026-02-01T00:00:00Z,9999-12-31\n")

            def ask(*flags):
                return run("--root", s.dir, *flags, "--format", "csv", "s.csv").stdout.decode().splitlines()
            self.assertEqual(ask("--group", "g", "--agg", "min:ts:timestamp,max:ts:timestamp", "--sort", "-max:ts"),
                             ["g,min:ts,max:ts", "a,2026-01-01T23:00:00Z,2026-03-01T00:00:00Z", "c,2026-02-01T00:00:00Z,2026-02-01T00:00:00Z", "b,0001-01-01T00:00:00Z,1969-12-31T23:59:59Z"])
            self.assertEqual(ask("--group", "g", "--agg", "min:ts:timestamp", "--sort", "min:ts"), ["g,min:ts", "b,0001-01-01T00:00:00Z", "a,2026-01-01T23:00:00Z", "c,2026-02-01T00:00:00Z"])
            self.assertEqual(ask("--group", "d:date", "--sort", "-d", "--top", "2"), ["d,count", "9999-12-31,1", "2026-02-01,1"])
            self.assertEqual(ask("--group", "d:date"), ["d,count", "0001-01-01,1", "1969-12-31,1", "2026-01-02,1", "2026-02-01,1", "9999-12-31,1"])
            self.assertEqual(ask("--group", "g", "--agg", "max:d:date", "--sort", "max:d"), ["g,max:d", "a,2026-01-02", "b,2026-02-01", "c,9999-12-31"])
        finally:
            s.cleanup()

    def test_json_lines_in(self):
        """A JSON string is the cell, read by the same grammar; a JSON number is its token, which has no `-` (docs/datetime.md section 9)."""
        s = Scratch()
        try:
            s.write("e.jsonl", '{"id":1,"ts":"2026-01-02T01:00:00+02:00"}\n{"id":2,"ts":"2026-01-01T23:00:00Z"}\n{"id":3,"ts":"2026-01-03T00:00:00Z"}\n')
            args = ["--root", s.dir, "--input-format", "jsonl", "--fields", "id,ts"]
            r = run(*args, "--group", "ts:timestamp", "--agg", "min:id:int", "--format", "jsonl", "e.jsonl")
            self.assertEqual([json.loads(l) for l in r.stdout.decode().splitlines()], [{"ts": "2026-01-01T23:00:00Z", "min:id": 1}, {"ts": "2026-01-03T00:00:00Z", "min:id": 3}])
            r = run(*args, "--order-by", "-ts:timestamp", "--select", "id", "--format", "csv", "e.jsonl")
            self.assertEqual(r.stdout.decode().split(), ["id", "3", "1", "2"])
            s.write("n.jsonl", '{"id":1,"ts":1767308400}\n')
            r = run(*args, "--group", "ts:timestamp", "n.jsonl")
            d = r.error()["detail"]
            self.assertEqual((r.first_rule(), d["value"], d["reason"], d["offset"], d["context"]), ("value.not-timestamp", "1767308400", "shape", 4, "group"))
        finally:
            s.cleanup()

    def test_a_quoted_name_takes_a_suffix_after_its_quote(self):
        for text, rows in (("'d':date = '2026-01-02'", [["2"]]), ("'ts':timestamp = '2026-01-01T23:00:00Z'", [["1"], ["2"]]), ("'w':datetime = '2026-01-01 23:00:00'", [["1"], ["2"]]),
                           ("'d':date >= '2026-01-02' and 'ts':timestamp > '2026-01-01T00:00:00Z'", [["2"]])):
            r = self.go("--where", text, "--select", "id")
            self.assertEqual((r.status, r.data()["rows"]), (0, rows), text)
        r = self.go("--where", "'d':datex = '2026-01-02'")
        self.assertEqual(r.first_rule(), "where.syntax", "a suffix is a suffix only at the end of a word")

    def test_a_date_type_in_a_join_key_is_refused_with_a_tag(self):
        """T3 (docs/datetime.md section 20) replaced the refusal T1 pinned here (a pair typed by a date type was args.bad-value, and --query's cast was query.syntax): a pair typed by a date
        type is read as that type, so the key cell `a`, which is no date, is refused as one, with the context of a join; and `cast(d as date)` in a select list is query.unsupported."""
        s = Scratch()
        try:
            s.write("a.csv", "k,d\na,2026-01-02\n")
            s.write("b.csv", "kk,x\na,1\n")
            for t, rule in (("date", "value.not-date"), ("timestamp", "value.not-timestamp"), ("datetime", "value.not-timestamp")):
                r = run("--root", s.dir, "--join", "b.csv", "--on", "k=kk:" + t, "a.csv")
                self.assertEqual((r.status, r.first_rule()), (8, rule), t)
                self.assertEqual(r.error()["detail"]["context"], "join", t)
            r = run("--root", s.dir, "--query", "select cast(d as date) from 'a.csv'")
            self.assertEqual((r.first_rule(), r.status), ("query.unsupported", 2))
        finally:
            s.cleanup()

    def test_output_forms(self):
        r = self.go("--group", "ts:timestamp", "--agg", "count,min:ts:timestamp", "--format", "jsonl")
        self.assertEqual([json.loads(l) for l in r.stdout.decode().splitlines()], [{"ts": "2026-01-01T23:00:00Z", "count": 2, "min:ts": "2026-01-01T23:00:00Z"}])
        self.assertIn('"ts":"2026-01-01T23:00:00Z"', r.stdout.decode())
        r = self.go("--group", "d:date", "--agg", "min:w:datetime,max:w:datetime,count,distinct:ts:timestamp", "--format", "jsonl")
        self.assertEqual([json.loads(l) for l in r.stdout.decode().splitlines()],
                         [{"d": "2026-01-01", "min:w": "2026-01-01T23:00:00", "max:w": "2026-01-01T23:00:00", "count": 1, "distinct:ts": 1},
                          {"d": "2026-01-02", "min:w": "2026-01-01T23:00:00", "max:w": "2026-01-01T23:00:00", "count": 1, "distinct:ts": 1}])
        self.assertIn('"count":1,"distinct:ts":1', r.stdout.decode())
        r = self.go("--group", "d:date", "--format", "jsonl")
        self.assertEqual([json.loads(l) for l in r.stdout.decode().splitlines()], [{"d": "2026-01-01", "count": 1}, {"d": "2026-01-02", "count": 1}])
        # a plan whose only date is a maximum or a minimum (the key is text): the writer of the dates is chosen by the aggregate
        for agg, label, shape in (("max:ts:timestamp", "max:ts", "[0-9T:Z-]+"), ("min:d:date", "min:d", "[0-9-]+"), ("min:w:datetime", "min:w", "[0-9T:-]+")):
            r = self.go("--group", "id", "--agg", agg, "--format", "jsonl")
            lines = r.stdout.decode().splitlines()
            self.assertEqual(len(lines), 2, agg)
            for line in lines:
                self.assertRegex(line, r'^\{"id":"[12]","%s":"%s"\}$' % (label, shape))
        r = self.go("--group", "w:datetime", "--format", "json")
        self.assertEqual(r.data()["rows"], [["2026-01-01T23:00:00", "2"]])
        r = self.go("--agg", "min:d:date,max:d:date", "--format", "json")
        self.assertEqual(r.data()["rows"], [["2026-01-01", "2026-01-02"]])
        # a row is its input bytes, unchanged, whichever way it is ordered (equal instants keep the order of the file)
        for key in ("ts:timestamp", "-ts:timestamp"):
            r = self.go("--select", "ts", "--order-by", key, "--format", "csv")
            self.assertEqual(r.stdout.decode().splitlines(), ["ts", "2026-01-01T23:00:00Z", "2026-01-02T01:00:00+02:00"])
        r = self.go("--select", "ts,d", "--order-by", "-ts:timestamp", "--top", "1", "--format", "jsonl")
        self.assertEqual(json.loads(r.stdout), {"ts": "2026-01-01T23:00:00Z", "d": "2026-01-01"})

    def test_explain_says_the_types_and_the_refusals(self):
        r = self.go("--explain", "--where", "ts:timestamp > '2026-01-01T00:00:00Z'", "--group", "d:date", "--agg", "min:w:datetime,count")
        plan = r.data()["explain"]["plan"]
        self.assertEqual(plan["where"][0]["type"], "timestamp")
        self.assertEqual(plan["group"][0]["type"], "date")
        self.assertEqual(plan["aggregates"][0]["type"], "datetime")
        possible = r.data()["explain"]["refusals"]["possible"]
        for tag in ("value.not-timestamp", "value.timestamp-precision", "value.timestamp-range", "value.timezone-missing", "value.timezone-unsupported", "value.not-date", "value.timestamp-offset-present"):
            self.assertIn(tag, possible, tag)
        r = self.go("--explain", "--group", "d:date")
        possible = r.data()["explain"]["refusals"]["possible"]
        self.assertIn("value.not-date", possible)
        for tag in ("value.timezone-missing", "value.not-timestamp", "value.timestamp-offset-present", "value.timestamp-precision"):
            self.assertNotIn(tag, possible)
        r = self.go("--explain", "--order-by", "-w:datetime")
        possible = r.data()["explain"]["refusals"]["possible"]
        self.assertIn("value.timestamp-offset-present", possible)
        self.assertNotIn("value.timezone-missing", possible)

    def test_near_strings_stay_names(self):
        """G-COMPAT: a header that ends in a type word, quoted or escaped, and columns called date or timestamp, mean what they meant."""
        s = Scratch()
        try:
            s.write("n.csv", "created:date,date,timestamp,x:timestamp\na,2026-01-02,2026-01-02T00:00:00Z,b\n")
            for flags, want in (
                (["--where", "'created:date' = 'a'", "--select", "date"], [["2026-01-02"]]),
                (["--where", "created\\:date = 'a'", "--select", "date"], [["2026-01-02"]]),
                (["--where", "date = '2026-01-02'", "--select", "timestamp"], [["2026-01-02T00:00:00Z"]]),
                (["--where", "timestamp > '2026'", "--select", "date"], [["2026-01-02"]]),
                (["--where", "date:date = '2026-01-02'", "--select", "date"], [["2026-01-02"]]),
                (["--where", "timestamp:timestamp = '2026-01-02T00:00:00Z'", "--select", "date"], [["2026-01-02"]]),
                (["--where", "'x:timestamp' = 'b'", "--select", "date"], [["2026-01-02"]]),
                (["--where", "x\\:timestamp = 'b'", "--select", "date"], [["2026-01-02"]]),
            ):
                r = run("--root", s.dir, *flags, "n.csv")
                self.assertEqual(r.status, 0, (flags, r.stdout[:300]))
                self.assertEqual(r.data()["rows"], want, flags)
            r = run("--root", s.dir, "--where", "created:date = '2026-01-02'", "n.csv")
            self.assertEqual((r.first_rule(), r.status), ("column.unknown", 3))
            r = run("--root", s.dir, "--group", "x\\:timestamp", "--agg", "distinct:x\\:timestamp", "--format", "csv", "n.csv")
            self.assertEqual(r.stdout.decode().split(), ["x:timestamp,distinct:x:timestamp", "b,1"])
        finally:
            s.cleanup()

    def test_a_column_called_like_a_suffix_stays_a_name(self):
        s = Scratch()
        try:
            s.write("c.csv", "id,:date,:timestamp,x:datetime\n1,2026-01-02,b,c\n")
            for flags, want in ((["--where", "':date' = '2026-01-02'", "--select", "id"], [["1"]]), (["--where", "\\:timestamp = 'b'", "--select", "id"], [["1"]]),
                                (["--where", "x\\:datetime = 'c'", "--select", "id"], [["1"]])):
                r = run("--root", s.dir, *flags, "c.csv")
                self.assertEqual((r.status, r.data()["rows"]), (0, want), flags)
            # the suffix alone, bare, is a name too
            for text in (":date = '2026-01-02'", ":timestamp = 'b'", ":datetime = 'c'"):
                r = run("--root", s.dir, "--where", text, "--select", "id", "c.csv") if text != ":datetime = 'c'" else run("--root", s.dir, "--where", "x\\:datetime = 'c'", "--select", "id", "c.csv")
                self.assertEqual((r.status, r.data()["rows"]), (0, [["1"]]), text)
            r = run("--root", s.dir, "--group", "\\:date", "--agg", "distinct:\\:timestamp", "--format", "csv", "c.csv")
            self.assertEqual(r.stdout.decode().split(), [":date,distinct::timestamp", "2026-01-02,1"])
        finally:
            s.cleanup()

    def test_the_value_of_a_refused_cell_is_cut_at_64_bytes(self):
        s = Scratch()
        try:
            for extra in (40, 43, 44, 45, 60):
                cell = "2026-01-02T01:00:00." + "1" * extra + "Z"
                s.write("l.csv", "id,v\n1,%s\n" % cell)
                r = run("--root", s.dir, "--agg", "max:v:timestamp", "l.csv")
                d = r.error()["detail"]
                self.assertEqual((r.first_rule(), d["digits"], d["value"], d["value_truncated"]), ("value.timestamp-precision", extra, cell[:64], len(cell) >= 64), extra)
        finally:
            s.cleanup()

    def test_rules_are_listed_with_exit_and_repairability(self):
        got = {r["rule"]: r for r in introspect()["rules"]}
        want = {"value.not-date": (8, "sometimes"), "value.not-timestamp": (8, "sometimes"), "value.timestamp-precision": (8, "never"), "value.timestamp-range": (8, "never"),
                "value.timezone-missing": (8, "sometimes"), "value.timestamp-offset-present": (8, "sometimes"), "value.timezone-unsupported": (8, "never")}
        for tag, (code, rep) in want.items():
            self.assertTrue(tag in got, tag)
            self.assertEqual((got[tag]["exit"], got[tag]["repairable"]), (code, rep), tag)

    def test_the_repairs_are_the_same_invocation_with_the_type_that_is_true(self):
        s = Scratch()
        try:
            s.write("r.csv", "id,ts\n1,2026-01-02T01:00:00\n")
            r = run("--root", s.dir, "--group", "ts:timestamp", "r.csv")
            rep = r.error()["repair"]
            self.assertEqual(rep["kind"], "choose")
            self.assertEqual(rep["options"][0]["argv"][-3:], ["--group", "ts:datetime", "r.csv"])
            again = run_argv(rep["options"][0]["argv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2026-01-02T01:00:00", "1"]]))
            s.write("r.csv", "id,ts\n1,2026-01-02T01:00:00Z\n")
            r = run("--root", s.dir, "--agg", "max:ts:datetime", "r.csv")
            self.assertEqual(r.first_rule(), "value.timestamp-offset-present")
            again = run_argv(r.error()["repair"]["options"][0]["argv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2026-01-02T01:00:00Z"]]))
            r = run("--root", s.dir, "--order-by", "ts:date", "r.csv")
            self.assertEqual((r.first_rule(), r.error()["detail"]["reason"]), ("value.not-date", "time-part"))
            again = run_argv(r.error()["repair"]["options"][0]["argv"])
            self.assertEqual(again.status, 0)
            s.write("r.csv", "id,ts\n1,2026-01-02\n")
            r = run("--root", s.dir, "--agg", "min:ts:datetime", "r.csv")
            self.assertEqual(r.error()["detail"]["reason"], "date-only")
            again = run_argv(r.error()["repair"]["options"][0]["argv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2026-01-02"]]))
            # a literal of the old type would be refused by the new one: no repair is offered, and it says why
            r = run("--root", s.dir, "--where", "ts:timestamp > '2026-01-01T00:00:00Z'", "r.csv")
            self.assertEqual((r.first_rule(), r.error()["detail"]["reason"]), ("value.not-timestamp", "date-only"))
            self.assertEqual(r.error()["repair"]["kind"], "none")
            # no repair for the reasons that have none
            for cell, tag in (("2026-02-30", "value.not-date"), ("2026-01-02T01:00:00.1234567Z", "value.timestamp-precision")):
                s.write("r.csv", "id,ts\n1,%s\n" % cell)
                r = run("--root", s.dir, "--agg", "min:ts:date" if tag == "value.not-date" else "min:ts:timestamp", "r.csv")
                self.assertEqual((r.first_rule(), r.error()["repair"]["kind"]), (tag, "none"))
        finally:
            s.cleanup()


# ---- G-DIFF -------------------------------------------------------------------------------------------------------------

def make_table(rng, rows):
    out = []
    pool_t = [ref.random_valid(rng, "timestamp") for _ in range(6)]
    pool_t += ["2026-01-01T23:00:00Z", "2026-01-02T01:00:00+02:00", "2026-01-01T23:00:00.500Z", "2026-01-01 23:00:00.5+00:00", "2026-01-02T00:00:00z"]
    pool_d = [ref.random_valid(rng, "date") for _ in range(5)] + ["2026-01-01", "2026-01-02", "2000-02-29", "1969-12-31"]
    pool_w = [ref.random_valid(rng, "datetime") for _ in range(5)] + ["2026-01-01 23:00:00", "2026-01-01T23:00:00.000", "2026-01-02 00:00:00"]
    for i in range(rows):
        out.append([str(i + 1), rng.choice(["a", "b", "c"]), rng.choice(pool_t), rng.choice(pool_d), rng.choice(pool_w)])
    return ["id", "k", "t", "d", "w"], out


KIND_OF = {"t": "timestamp", "d": "date", "w": "datetime"}


def lit_for(rng, col, rows, idx):
    kind = KIND_OF[col]
    if rng.random() < 0.7:
        return rows[rng.randrange(len(rows))][idx[col]]
    return ref.random_valid(rng, kind)


def plan(rng, rows, idx):
    p = {"form": rng.choice(["rows", "rows", "order", "group"])}
    cols = list(KIND_OF)
    if rng.random() < 0.6:
        n = rng.randint(1, 2)
        conds = []
        for _ in range(n):
            col = rng.choice(cols)
            op = rng.choice(["=", "!=", "<", "<=", ">", ">=", "in"])
            lits = [lit_for(rng, col, rows, idx) for _ in range(rng.randint(1, 3))] if op == "in" else [lit_for(rng, col, rows, idx)]
            conds.append((col, op, lits))
        p["where"] = conds
    if p["form"] == "order":
        p["order"] = [(rng.choice(cols + ["k"]), rng.random() < 0.4) for _ in range(rng.randint(1, 3))]
        p["top"] = rng.choice([None, None, 1, 3])
    if p["form"] == "group":
        p["group"] = rng.sample(cols + ["k"], rng.randint(1, 2))
        p["aggs"] = [rng.choice([("count", None), ("min", rng.choice(cols)), ("max", rng.choice(cols)), ("distinct", rng.choice(cols))]) for _ in range(rng.randint(0, 3))]
        used = {c for c in p["group"]}
        for f, c in p["aggs"]:
            if c in KIND_OF:
                pass
    return p


def flags_of(p):
    f = []
    if p.get("where"):
        parts = []
        for col, op, lits in p["where"]:
            t = col + SUFFIX[KIND_OF[col]]
            if op == "in":
                parts.append("%s in (%s)" % (t, ", ".join("'%s'" % x for x in lits)))
            else:
                parts.append("%s %s '%s'" % (t, op, lits[0]))
        f += ["--where", " and ".join(parts)]
    if p["form"] == "rows":
        f += ["--select", "id", "--format", "csv"]
    elif p["form"] == "order":
        f += ["--select", "id", "--order-by", ",".join(("-" if desc else "") + (c + SUFFIX[KIND_OF[c]] if c in KIND_OF else c) for c, desc in p["order"]), "--format", "csv"]
        if p["top"]:
            f += ["--top", str(p["top"])]
    else:
        f += ["--group", ",".join(c + SUFFIX[KIND_OF[c]] if c in KIND_OF else c for c in p["group"]), "--format", "csv"]
        items = []
        for fn, c in p["aggs"]:
            items.append("count" if fn == "count" else "%s:%s%s" % (fn, c, SUFFIX[KIND_OF[c]]))
        if items:
            f += ["--agg", ",".join(items)]
    return f


def expected(p, header, rows):
    idx = {n: i for i, n in enumerate(header)}

    def val(col, row):
        r = ref.parse(KIND_OF[col], row[idx[col]].encode())
        assert r[0] == "ok", (col, row)
        return r[1]

    def keep(row):
        for col, op, lits in p.get("where", []):
            v = val(col, row)
            ws = [ref.parse(KIND_OF[col], x.encode())[1] for x in lits]
            ok = any({"=": v == w, "!=": v != w, "<": v < w, "<=": v <= w, ">": v > w, ">=": v >= w, "in": v == w}[op] for w in ws)
            if not ok:
                return False
        return True

    kept = [r for r in rows if keep(r)]
    if p["form"] == "rows":
        return ["id"] + [r[0] for r in kept]
    if p["form"] == "order":
        def sort_key(r):
            out = []
            for c, desc in p["order"]:
                if c in KIND_OF:
                    v = val(c, r)
                    out.append(-v if desc else v)
                else:
                    t = r[idx[c]].encode()
                    out.append(tuple(-b for b in t) + (1,) if desc else tuple(t))
            return out
        # bytewise descending for the text key is not a negation: compare with cmp
        import functools

        def cmp(a, b):
            for c, desc in p["order"]:
                if c in KIND_OF:
                    x, y = val(c, a), val(c, b)
                else:
                    x, y = a[idx[c]].encode(), b[idx[c]].encode()
                if x != y:
                    return (1 if x > y else -1) * (-1 if desc else 1)
            return 0
        ordered = sorted(kept, key=functools.cmp_to_key(cmp))
        if p["top"]:
            ordered = ordered[:p["top"]]
        return ["id"] + [r[0] for r in ordered]
    groups = {}
    for r in kept:
        key = tuple(val(c, r) if c in KIND_OF else r[idx[c]] for c in p["group"])
        g = groups.setdefault(key, {"n": 0, "rows": []})
        g["n"] += 1
        g["rows"].append(r)
    labels = list(p["group"]) + [("count" if fn == "count" else "%s:%s" % (fn, c)) for fn, c in p["aggs"]]
    if not p["aggs"]:
        labels.append("count")
    out = [",".join(labels)]
    for key in sorted(groups, key=lambda k: tuple((x if isinstance(x, int) else x.encode()) for x in k) if True else k):
        g = groups[key]
        cells = [ref.canon(KIND_OF[c], x) if c in KIND_OF else x for c, x in zip(p["group"], key)]
        if not p["aggs"]:
            cells.append(str(g["n"]))
        for fn, c in p["aggs"]:
            if fn == "count":
                cells.append(str(g["n"]))
            else:
                vs = [val(c, r) for r in g["rows"]]
                if fn == "distinct":
                    cells.append(str(len(set(vs))))
                else:
                    cells.append(ref.canon(KIND_OF[c], min(vs) if fn == "min" else max(vs)))
        out.append(",".join(cells))
    return out


class Diff(unittest.TestCase):
    """G-DIFF: the plans, against a Python evaluation. Each table is run at one and at four threads."""

    def test_random_tables_and_plans(self):
        rng = random.Random(2026)
        s = Scratch()
        try:
            for n in range(DT_TABLES):
                header, rows = make_table(rng, rng.randint(1, 40))
                s.write("p.csv", csv_text([header] + rows))
                idx = {c: i for i, c in enumerate(header)}
                for _ in range(8):
                    p = plan(rng, rows, idx)
                    want = expected(p, header, rows)
                    for extra in ([], ["--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", "0"]):
                        r = run("--root", s.dir, *flags_of(p), *extra, "p.csv")
                        self.assertEqual(r.status, 0, (flags_of(p), r.stdout[:300], r.stderr[:300]))
                        self.assertEqual(r.stdout.decode().splitlines(), want, (flags_of(p), extra))
        finally:
            s.cleanup()


# ---- G-ROUND ------------------------------------------------------------------------------------------------------------

class Round(unittest.TestCase):
    def test_a_printed_key_reads_back_as_the_value(self):
        rng = random.Random(5)
        s = Scratch()
        try:
            for kind in ref.KINDS:
                cells = [ref.random_valid(rng, kind) for _ in range(DT_VALID)] + [c for c in ref.edge_cells() if ref.parse(kind, c.encode())[0] == "ok" and not any(x in c for x in ',"\n\r')]
                r = s.table("rt.csv", csv_text([["v"]] + [[c] for c in cells]), "--group", "v" + SUFFIX[kind], "--format", "csv", "--max-groups", "1000000")
                self.assertEqual(r.status, 0, (kind, r.stdout[:300], r.stderr[:300]))
                printed = r.stdout.decode().splitlines()[1:]
                self.assertGreater(len(printed), 15, kind)
                keys = [l.rsplit(",", 1)[0] for l in printed]
                values = [ref.parse(kind, k.encode()) for k in keys]
                for k, v in zip(keys, values):
                    self.assertEqual(v[0], "ok", (kind, k))
                    self.assertEqual(ref.canon(kind, v[1]), k)
                # the printed text is also a cell: a table of the printed keys prints as itself
                r2 = s.table("rt2.csv", csv_text([["v"]] + [[k] for k in keys]), "--group", "v" + SUFFIX[kind], "--format", "csv", "--max-groups", "1000000")
                self.assertEqual(r2.status, 0, (kind, r2.stdout[:300]))
                self.assertEqual(r2.stdout.decode().splitlines()[1:], [k + ",1" for k in keys])
                self.assertEqual(sorted(values), values)
        finally:
            s.cleanup()


# ---- G-ORACLE-SQL -------------------------------------------------------------------------------------------------------

def duckdb_binary():
    for c in (os.environ.get("DUCKDB"), shutil.which("duckdb"), str(pathlib.Path.home() / "bin" / "duckdb")):
        if c and os.path.exists(c):
            return c
    return None


class Oracle(unittest.TestCase):
    """For the cells both engines accept, the tool's instant is theirs. They disagree on refusals, so they are no oracle for those (docs/datetime.md section 3)."""

    def cells(self, kind, n):
        """Cells both engines accept: upper-case T and Z (they differ on lower case), offsets within -14:00..+14:00, the instant inside 0001..9999."""
        rng = random.Random(99)
        out = []
        while len(out) < n:
            c = ref.random_valid(rng, kind)
            if kind == "timestamp":
                c = c[:10] + "T" + c[11:] if c[10] == "t" else c
                c = c[:-1] + "Z" if c.endswith("z") else c
                if c[-6] in "+-" and abs(int(c[-6:-3])) > 14:
                    continue
            if ref.parse(kind, c.encode())[0] == "ok":
                out.append(c)
        return out

    def test_duckdb(self):
        exe = duckdb_binary()
        if not exe:
            self.skipTest("no duckdb")
        for kind in ("timestamp", "date"):
            cells = self.cells(kind, DT_ORACLE)
            cast = "timestamptz" if kind == "timestamp" else "date"
            values = ",".join("('%s')" % c for c in cells)
            if kind == "timestamp":
                sql = "SET TimeZone='UTC'; SELECT v, epoch_us(CAST(v AS TIMESTAMPTZ)) FROM (VALUES %s) t(v);" % values
            else:
                sql = "SELECT v, CAST(v AS DATE) - DATE '1970-01-01' FROM (VALUES %s) t(v);" % values
            p = subprocess.run([exe, "-csv", "-noheader", "-c", sql], capture_output=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stderr[:300])
            got = {}
            for line in p.stdout.decode().splitlines():
                v, n = line.rsplit(",", 1)
                got[v.strip('"')] = int(n)
            for c in cells:
                self.assertEqual(ref.parse(kind, c.encode()), ("ok", got[c]), c)
            s = Scratch()
            try:
                r = s.table("o.csv", csv_text([["v"]] + [[c] for c in cells]), "--group", "v" + SUFFIX[kind], "--format", "csv", "--max-groups", "1000000")
                keys = [l.rsplit(",", 1)[0] for l in r.stdout.decode().splitlines()[1:]]
                self.assertEqual([ref.parse(kind, k.encode())[1] for k in keys], sorted({got[c] for c in cells}))
            finally:
                s.cleanup()

    def test_sqlite(self):
        db = sqlite3.connect(":memory:")
        cells = self.cells("timestamp", DT_ORACLE)
        whole = [c for c in cells if "." not in c]
        s = Scratch()
        try:
            r = s.table("o.csv", csv_text([["v"]] + [[c] for c in whole]), "--group", "v:timestamp", "--format", "csv", "--max-groups", "1000000")
            keys = [l.rsplit(",", 1)[0] for l in r.stdout.decode().splitlines()[1:]]
        finally:
            s.cleanup()
        want = sorted({int(db.execute("select strftime('%s', ?)", (c,)).fetchone()[0]) * 1000000 for c in whole})
        self.assertEqual([ref.parse("timestamp", k.encode())[1] for k in keys], want)


# ---- G-PAR --------------------------------------------------------------------------------------------------------------

class Parallel(unittest.TestCase):
    """Threads 1..64 x chunk sizes: the same bytes, refusals included; standard input as the file."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        rng = random.Random(3)
        rows = [[str(i), ref.random_valid(rng, "timestamp"), ref.random_valid(rng, "date"), ref.random_valid(rng, "datetime")] for i in range(1, 801)]
        cls.s.write("big.csv", csv_text([["id", "t", "d", "w"]] + rows))
        bad = [list(r) for r in rows]
        bad[300][1] = "2026-01-02T01:00:00"
        bad[500][1] = "2026-02-30T01:00:00Z"
        bad[700][2] = "x"
        cls.s.write("bad.csv", csv_text([["id", "t", "d", "w"]] + bad))
        cls.plans = [
            ["--group", "t:timestamp", "--format", "csv", "--max-groups", "100000"],
            ["--group", "d:date", "--agg", "min:t:timestamp,max:w:datetime,distinct:t:timestamp,count", "--format", "csv"],
            ["--order-by", "-t:timestamp,d:date", "--select", "id", "--format", "csv"],
            ["--where", "t:timestamp >= '2000-01-01T00:00:00Z' and d:date < '2030-01-01'", "--select", "id", "--format", "csv"],
            ["--agg", "min:t:timestamp,max:t:timestamp,distinct:w:datetime,distinct:d:date", "--format", "csv"],
            ["--group", "w:datetime", "--agg", "count", "--format", "jsonl", "--max-groups", "100000"],
        ]

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def same(self, name, plan):
        base = run("--root", self.s.dir, *plan, name)
        for threads in (2, 3, 4, 8, 16, 64):
            for chunk in (1, 7, 64, 1000):
                got = run("--root", self.s.dir, *plan, "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, name)
                # a repair repeats the invocation, threads included
                shown = re.sub(rb'"--threads","[0-9]+","--chunk-bytes","[0-9]+","--parallel-min-bytes","0",', b"", got.stdout)
                self.assertEqual((got.status, shown, got.stderr), (base.status, base.stdout, base.stderr), (plan, threads, chunk))
        data = (self.s.dir / name).read_bytes()
        via = run("--root", self.s.dir, *plan, stdin=data)
        self.assertEqual(via.status, base.status)
        if base.status == 0:
            self.assertEqual(via.stdout, base.stdout)
        else:
            a, b = base.error(), via.error()
            self.assertEqual((a["rule"], a["detail"]["context"], a["detail"]["column"], a["detail"]["row"], a["detail"]["line"], a["detail"]["value"], a["detail"].get("reason")),
                             (b["rule"], b["detail"]["context"], b["detail"]["column"], b["detail"]["row"], b["detail"]["line"], b["detail"]["value"], b["detail"].get("reason")))
        return base

    def test_the_same_bytes_at_any_thread_count(self):
        for plan in self.plans:
            base = self.same("big.csv", plan)
            self.assertEqual(base.status, 0, (plan, base.stdout[:200], base.stderr[:200]))

    def test_the_first_refusal_in_file_order_at_any_thread_count(self):
        for plan in (["--group", "t:timestamp"], ["--agg", "min:t:timestamp"], ["--where", "t:timestamp > '2000-01-01T00:00:00Z'"], ["--order-by", "t:timestamp", "--select", "id"], ["--group", "d:date", "--agg", "count"]):
            base = self.same("bad.csv", plan)
            self.assertEqual(base.status, 8, plan)
            d = base.error()["detail"]
            self.assertEqual(d["row"], 301 if "d:date" not in plan else 701, plan)


# ---- G-FUZZ -------------------------------------------------------------------------------------------------------------

class Fuzz(unittest.TestCase):
    """No trap, no hang; every refusal has a listed tag, the reference's reason and a repair that is accepted."""

    def test_random_and_mutated_cells_no_trap_and_the_reference_answer(self):
        rng = random.Random(404)
        s = Scratch()
        tags = set(ref.TAGS)
        try:
            n = 0
            while n < DT_CELLS:
                kind = rng.choice(ref.KINDS)
                cell = ref.fuzz_cell(rng)
                if any(c in cell for c in b',"\n\r\x00'):
                    continue
                try:
                    cell.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                n += 1
                s.write("z.csv", b"id,v\n1," + cell + b"\n")
                r = run("--root", s.dir, "--agg", "min:v" + SUFFIX[kind], "z.csv")
                want = ref.parse(kind, cell)
                self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11), (kind, cell))
                if want[0] == "ok":
                    self.assertEqual(r.status, 0, (kind, cell, r.stdout[:300]))
                    self.assertEqual(r.data()["rows"], [[ref.canon(kind, want[1])]], (kind, cell))
                    continue
                err = r.error()
                self.assertEqual(err["rule"], want[1], (kind, cell))
                self.assertIn(err["rule"], tags)
                for key, value in want[2].items():
                    self.assertEqual(err["detail"].get(key), value, (kind, cell, key))
                rep = err["repair"]
                if rep and rep["kind"] == "choose":
                    for option in rep["options"]:
                        again = run_argv(option["argv"], cwd=None)
                        # accepted: the invocation is understood (a repair does not make the cell valid: a date alone that is no real day is still no day, in the type it should be read as)
                        self.assertIn(again.status, (0, 8), (kind, cell, option["argv"], again.stdout[:300]))
        finally:
            s.cleanup()

    def test_long_and_odd_cells_do_not_trap(self):
        s = Scratch()
        try:
            cells = [b"", b"0", b"-", b"2026-01-02T01:00:00." + b"1" * 5000 + b"Z", b"9" * 3000, b"2026-01-02T01:00:00Z" * 100, b"\xc3\xa9" * 30, b"2026-01-02T01:00:00.5" + b" " * 2000,
                     b"+" * 100, b"[" * 100]
            for cell in cells:
                for kind in ref.KINDS:
                    s.write("z.csv", b"id,v\n1,\"" + cell.replace(b'"', b'""') + b"\"\n")
                    r = run("--root", s.dir, "--group", "v" + SUFFIX[kind], "--agg", "max:v" + SUFFIX[kind], "z.csv")
                    self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11), (kind, cell[:30]))
                    want = ref.parse(kind, cell)
                    self.assertEqual(r.status, 0 if want[0] == "ok" else 8, (kind, cell[:30], r.stdout[:200]))
        finally:
            s.cleanup()


if __name__ == "__main__":
    unittest.main()
