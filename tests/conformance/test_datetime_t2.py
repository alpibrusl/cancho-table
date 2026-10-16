"""`@UNIT` on a group key (docs/datetime.md section 7, slice T2): `--group ts:timestamp@day`, `--group d:date@month`, `--group w:datetime@hour`.

These are the gates of docs/datetime.md section 12 that are about the bucket, written before the code (as T0 wrote T1's) and run on the built binary:

* G-ORACLE-GRAMMAR (the bucket): `datetime_ref.bucket` (Python `datetime.replace` on the calendar date and the clock, fixed offsets, no zoneinfo, no code shared with the tool) decides the key of
  every cell for every unit of every type, against hand-written expectations that pin the reference itself; the edge cells are the instants before, at and after each boundary written
  in many offsets; the refusals of a cell in a bucketed key are those of the same cell in the plain key (tag, reason, offset, row, line, column, context).
* G-ROUND: `parse(print(key)) = key`; the printed key fed back to `--where c:type >= KEY` selects exactly the rows of its bucket; the bucket of a key is the key.
* G-DIFF: random tables and plans (keys with and without a bucket, `min`, `max`, `distinct`, `--where` on the same column, `--sort`, csv/json/jsonl) against a Python evaluation.
* G-PAR: threads 2..64 x chunk sizes, standard input against the file.
* G-RULES, G-COMPAT (near strings: `ts@day`, `:timestamp@`, `@DAY`, `@` twice), G-FUZZ, the same-column rules, the surface (`--agg`, `--order-by`, `--query`, `--explain`, help).
* G-ORACLE-SQL: DuckDB (`date_trunc`, the Python module) and SQLite (`strftime`), for valid cells only; skipped when the engine is not installed.

DT2_VALID (random valid cells a type and unit, default 1500), DT2_TABLES (random plans' tables, default 40) and DT2_REFUSED (refused cells, default 60 a type) scale the generated parts.
"""

import functools
import json
import os
import random
import re
import sqlite3
import unittest

import procs as subprocess

import datetime_ref as ref
from harness import ROOT, Scratch, introspect, run, run_argv, validate
from test_datetime import csv_text, SUFFIX

DT2_VALID = int(os.environ.get("DT2_VALID", "1500"))
DT2_TABLES = int(os.environ.get("DT2_TABLES", "40"))
DT2_REFUSED = int(os.environ.get("DT2_REFUSED", "60"))
BIG = ["--max-groups", "1000000", "--max-state-bytes", "1073741824"]
UNITS = ref.UNITS
KINDS = ref.KINDS


def group_lines(s, kind, cells, unit, *more, name="b.csv"):
    """The csv lines of `--group v:KIND@UNIT --agg count` over `cells`."""
    r = s.table(name, csv_text([["v"]] + [[c] for c in cells]), "--group", "v%s@%s" % (SUFFIX[kind], unit), "--agg", "count", "--format", "csv", *BIG, *more)
    assert r.status == 0, (kind, unit, r.stdout[:300], r.stderr[:300])
    return r.stdout.decode().splitlines()


def expected_lines(kind, cells, unit):
    counts = {}
    for c in cells:
        v = ref.bucket(kind, ref.parse(kind, c.encode())[1], unit)
        counts[v] = counts.get(v, 0) + 1
    return ["v,count"] + ["%s,%d" % (ref.canon(kind, v), counts[v]) for v in sorted(counts)]


def refusal_doc(r):
    return r.doc()["error"]


# (kind, cell, unit, the printed key): written by hand, from the calendar, not from the reference
GOLDEN = [
    ("timestamp", "2026-01-02T01:00:00+02:00", "hour", "2026-01-01T23:00:00Z"),
    ("timestamp", "2026-01-02T01:00:00+02:00", "day", "2026-01-01T00:00:00Z"),      # the UTC day of the instant, not the written day
    ("timestamp", "2026-01-02T01:00:00+02:00", "month", "2026-01-01T00:00:00Z"),
    ("timestamp", "2026-01-02T01:00:00+02:00", "year", "2026-01-01T00:00:00Z"),
    ("timestamp", "2026-01-01T00:30:00+01:00", "day", "2025-12-31T00:00:00Z"),
    ("timestamp", "2026-01-01T00:30:00+01:00", "month", "2025-12-01T00:00:00Z"),
    ("timestamp", "2026-01-01T00:30:00+01:00", "year", "2025-01-01T00:00:00Z"),
    ("timestamp", "2026-12-31T23:30:00-01:00", "day", "2027-01-01T00:00:00Z"),
    ("timestamp", "2026-12-31T23:30:00-01:00", "year", "2027-01-01T00:00:00Z"),
    ("timestamp", "2024-02-29T23:59:59-12:00", "day", "2024-03-01T00:00:00Z"),
    ("timestamp", "2024-02-29T23:59:59-12:00", "month", "2024-03-01T00:00:00Z"),
    ("timestamp", "2100-02-28T23:00:00-01:00", "day", "2100-03-01T00:00:00Z"),       # 2100 is not a leap year
    ("timestamp", "2000-02-29T12:00:00Z", "month", "2000-02-01T00:00:00Z"),
    ("timestamp", "2400-02-29T12:00:00Z", "day", "2400-02-29T00:00:00Z"),
    ("timestamp", "1900-03-01T00:00:00+14:00", "day", "1900-02-28T00:00:00Z"),       # 1900 is not a leap year: the day before 1900-03-01 is the 28th
    ("timestamp", "2026-01-02T01:30:45.123456+05:30", "hour", "2026-01-01T20:00:00Z"),
    ("timestamp", "2026-01-02T01:30:45.123456+05:30", "day", "2026-01-01T00:00:00Z"),
    ("timestamp", "2026-01-02T01:30:45.5+00:00", "hour", "2026-01-02T01:00:00Z"),
    ("timestamp", "1969-12-31T23:59:59.999999Z", "hour", "1969-12-31T23:00:00Z"),    # floor: the bucket of -1 microsecond is before 1970
    ("timestamp", "1969-12-31T23:59:59.999999Z", "day", "1969-12-31T00:00:00Z"),
    ("timestamp", "1969-12-31T23:59:59.999999Z", "month", "1969-12-01T00:00:00Z"),
    ("timestamp", "1969-12-31T23:59:59.999999Z", "year", "1969-01-01T00:00:00Z"),
    ("timestamp", "1970-01-01T00:00:00.000001Z", "day", "1970-01-01T00:00:00Z"),
    ("timestamp", "1970-01-01T00:00:00Z", "year", "1970-01-01T00:00:00Z"),
    ("timestamp", "1960-07-15T13:45:00Z", "month", "1960-07-01T00:00:00Z"),
    ("timestamp", "0001-01-01T00:00:00Z", "hour", "0001-01-01T00:00:00Z"),
    ("timestamp", "0001-01-01T00:00:00Z", "year", "0001-01-01T00:00:00Z"),
    ("timestamp", "0001-01-01T14:00:00+14:00", "day", "0001-01-01T00:00:00Z"),
    ("timestamp", "0001-12-31T23:59:59.999999Z", "month", "0001-12-01T00:00:00Z"),
    ("timestamp", "9999-12-31T23:59:59.999999Z", "hour", "9999-12-31T23:00:00Z"),
    ("timestamp", "9999-12-31T23:59:59.999999Z", "day", "9999-12-31T00:00:00Z"),
    ("timestamp", "9999-12-31T23:59:59.999999Z", "month", "9999-12-01T00:00:00Z"),
    ("timestamp", "9999-12-31T23:59:59.999999Z", "year", "9999-01-01T00:00:00Z"),
    ("timestamp", "9999-12-31T09:59:59.999999-14:00", "day", "9999-12-31T00:00:00Z"),
    ("timestamp", "2026-01-01 23:00:00.5+00:00", "hour", "2026-01-01T23:00:00Z"),
    ("datetime", "2026-01-02 01:00:00.5", "hour", "2026-01-02T01:00:00"),
    ("datetime", "2026-01-02 01:00:00.5", "day", "2026-01-02T00:00:00"),
    ("datetime", "2026-01-02t01:00:00.5", "month", "2026-01-01T00:00:00"),
    ("datetime", "2026-12-31T23:59:59.999999", "year", "2026-01-01T00:00:00"),
    ("datetime", "1969-12-31 23:59:59.999999", "day", "1969-12-31T00:00:00"),
    ("datetime", "1969-12-31 23:59:59.999999", "hour", "1969-12-31T23:00:00"),
    ("datetime", "0001-01-01 00:00:00", "year", "0001-01-01T00:00:00"),
    ("datetime", "9999-12-31 23:59:59.999999", "month", "9999-12-01T00:00:00"),
    ("datetime", "2000-02-29 23:59:59", "day", "2000-02-29T00:00:00"),
    ("date", "2026-02-10", "month", "2026-02-01"),
    ("date", "2026-02-10", "year", "2026-01-01"),
    ("date", "2026-01-01", "month", "2026-01-01"),
    ("date", "2026-12-31", "year", "2026-01-01"),
    ("date", "2000-02-29", "month", "2000-02-01"),
    ("date", "1900-02-28", "month", "1900-02-01"),
    ("date", "1969-12-31", "year", "1969-01-01"),
    ("date", "1969-12-31", "month", "1969-12-01"),
    ("date", "0001-01-01", "year", "0001-01-01"),
    ("date", "0001-12-31", "month", "0001-12-01"),
    ("date", "9999-12-31", "month", "9999-12-01"),
    ("date", "9999-12-31", "year", "9999-01-01"),
]


class Reference(unittest.TestCase):
    """The reference against hand-written expectations (so that it is not the only judge of itself), and the tool against both."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_the_reference_agrees_with_the_hand_written_buckets(self):
        for kind, cell, unit, printed in GOLDEN:
            got = ref.parse(kind, cell.encode())
            self.assertEqual(got[0], "ok", (kind, cell))
            self.assertEqual(ref.canon(kind, ref.bucket(kind, got[1], unit)), printed, (kind, cell, unit))

    def test_the_tool_agrees_with_the_hand_written_buckets(self):
        for kind, cell, unit, printed in GOLDEN:
            r = self.s.table("g.csv", "id,v\n1,%s\n" % cell, "--group", "v%s@%s" % (SUFFIX[kind], unit), "--format", "csv")
            self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["v,count", printed + ",1"]), (kind, cell, unit, r.stdout[:300]))

    def test_units_by_type(self):
        self.assertEqual(ref.UNITS_OF["date"], ("month", "year"))
        for kind in ("timestamp", "datetime"):
            self.assertEqual(ref.UNITS_OF[kind], ("hour", "day", "month", "year"))
        self.assertEqual(ref.bucket_refusal("date", "hour"), "finer-than-type")
        self.assertEqual(ref.bucket_refusal("date", "day"), "finer-than-type")
        self.assertEqual(ref.bucket_refusal("timestamp", "week"), "week-not-built")
        self.assertEqual(ref.bucket_refusal("timestamp", "minute"), "unknown-unit")
        self.assertIsNone(ref.bucket_refusal("timestamp", "year"))

    def test_the_equal_instants_of_two_offsets_are_one_bucket(self):
        rows = "id,ts\n1,2026-01-02T01:00:00+02:00\n2,2026-01-01T23:00:00Z\n3,2026-01-01T23:59:59.999999Z\n4,2026-01-02T00:00:00Z\n5,2025-12-31T14:00:00-10:00\n"
        r = self.s.table("o.csv", rows, "--group", "ts:timestamp@day", "--agg", "count,min:ts:timestamp,max:ts:timestamp", "--format", "csv")
        # 1, 2, 3 are on 2026-01-01 (UTC); 4 on the 2nd; 5 is 2026-01-01T00:00:00Z
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,count,min:ts,max:ts", "2026-01-01T00:00:00Z,4,2026-01-01T00:00:00Z,2026-01-01T23:59:59.999999Z", "2026-01-02T00:00:00Z,1,2026-01-02T00:00:00Z,2026-01-02T00:00:00Z"])
        r = self.s.table("o.csv", rows, "--group", "ts:timestamp@hour", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,count", "2026-01-01T00:00:00Z,1", "2026-01-01T23:00:00Z,3", "2026-01-02T00:00:00Z,1"])

    def test_a_datetime_is_bucketed_by_what_is_written(self):
        rows = "id,w\n1,2026-01-02 01:00:00\n2,2026-01-02T23:59:59.9\n3,2026-01-03 00:00:00\n"
        r = self.s.table("w.csv", rows, "--group", "w:datetime@day", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["w,count", "2026-01-02T00:00:00,2", "2026-01-03T00:00:00,1"])


class Buckets(unittest.TestCase):
    """G-ORACLE-GRAMMAR for the bucket: every unit of every type against the reference, edge cells and random cells."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_the_edge_cells_every_unit_every_type(self):
        rng = random.Random(21)
        for kind in KINDS:
            cells = ref.first_value_cells(kind, rng, 0)
            cells += [c for c in ref.edge_cells() if ref.parse(kind, c.encode())[0] == "ok" and not any(x in c for x in ',"\n\r')]
            self.assertGreater(len(cells), 300, kind)
            for unit in ref.UNITS_OF[kind]:
                self.assertEqual(group_lines(self.s, kind, cells, unit), expected_lines(kind, cells, unit), (kind, unit))

    def test_random_valid_cells_every_unit_every_type(self):
        rng = random.Random(22)
        for kind in KINDS:
            cells = [ref.random_valid(rng, kind) for _ in range(DT2_VALID)]
            for unit in ref.UNITS_OF[kind]:
                self.assertEqual(group_lines(self.s, kind, cells, unit), expected_lines(kind, cells, unit), (kind, unit))

    def test_fractional_digits_one_to_six(self):
        for kind, suffix in (("timestamp", "Z"), ("timestamp", "-05:30"), ("datetime", "")):
            cells = []
            for digits in range(1, 7):
                for frac in ("9" * digits, "0" * (digits - 1) + "1", "5" + "0" * (digits - 1)):
                    cells.append("2026-03-31T23:59:59.%s%s" % (frac, suffix))
                    cells.append("1969-12-31T23:59:59.%s%s" % (frac, suffix))
            for unit in UNITS:
                self.assertEqual(group_lines(self.s, kind, cells, unit), expected_lines(kind, cells, unit), (kind, suffix, unit))

    def test_a_bucket_of_the_ends_of_the_range_is_inside_the_range(self):
        # the first bucket of any unit starts at 0001-01-01 and the last one at or before 9999-12-31: no clamp, no wrap, no refusal
        for kind, first, last in (("timestamp", "0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z"), ("datetime", "0001-01-01 00:00:00", "9999-12-31 23:59:59.999999"), ("date", "0001-01-01", "9999-12-31")):
            for unit in ref.UNITS_OF[kind]:
                lines = group_lines(self.s, kind, [first, last], unit)
                self.assertEqual(lines, expected_lines(kind, [first, last], unit))
                self.assertEqual(len(lines), 3, (kind, unit))
                self.assertTrue(lines[1].startswith("0001-01-01"), lines)
        # a timestamp whose offset takes it past the first instant is refused before it is bucketed, as without a bucket
        r = self.s.table("e.csv", "v\n0001-01-01T00:00:00+01:00\n", "--group", "v:timestamp@year")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["direction"]), (8, "value.timestamp-range", "before"))

    def test_the_refusals_of_a_bucketed_key_are_those_of_the_plain_key(self):
        rng = random.Random(23)
        for kind in KINDS:
            refused = [c for c in ref.edge_cells() if ref.parse(kind, c.encode())[0] == "refuse" and not any(x in c for x in ',"\n\r\x00')]
            rng.shuffle(refused)
            for cell in refused[:DT2_REFUSED]:
                b = cell.encode()
                want = ref.parse(kind, b)
                unit = rng.choice(ref.UNITS_OF[kind])
                r = self.s.table("c.csv", b"id,v\n1," + b + b"\n", "--group", "v%s@%s" % (SUFFIX[kind], unit))
                plain = self.s.table("c.csv", b"id,v\n1," + b + b"\n", "--group", "v" + SUFFIX[kind])
                err = refusal_doc(r)
                self.assertEqual((r.status, err["rule"]), (8, want[1]), (kind, cell, unit, r.stdout[:300]))
                d = err["detail"]
                self.assertEqual((d["row"], d["line"], d["column"], d["context"], d["value"]), (1, 2, "v", "group", cell[:64]), (kind, cell))
                for key, value in want[2].items():
                    self.assertEqual(d.get(key), value, (kind, cell, key))
                for key in ("reason", "offset", "digits", "direction"):
                    self.assertEqual(key in d, key in want[2], (kind, cell, key))
                pd = refusal_doc(plain)["detail"]
                self.assertEqual({k: v for k, v in d.items()}, {k: v for k, v in pd.items()}, (kind, cell))
                self.assertEqual(validate(r), [])

    def test_the_first_refusal_in_file_order_is_the_same_with_a_bucket(self):
        data = "id,v\n1,2026-01-01T00:00:00Z\n2,2026-01-02T00:00:00\n3,x\n4,2026-02-30T00:00:00Z\n"
        r = self.s.table("f.csv", data, "--group", "v:timestamp@month")
        self.assertEqual((r.status, r.first_rule()), (8, "value.timezone-missing"))
        d = refusal_doc(r)["detail"]
        self.assertEqual((d["row"], d["line"], d["reason"], d["context"]), (2, 3, "missing", "group"))


class RoundTrip(unittest.TestCase):
    """G-ROUND with buckets: the printed key is a value of the type; fed back it selects the bucket's rows; its own bucket is itself."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_parse_of_print_is_the_key_and_the_key_is_its_own_bucket(self):
        rng = random.Random(31)
        for kind in KINDS:
            cells = [ref.random_valid(rng, kind) for _ in range(DT2_VALID)] + ref.first_value_cells(kind, rng, 800)
            for unit in ref.UNITS_OF[kind]:
                keys = [l.rsplit(",", 1)[0] for l in group_lines(self.s, kind, cells, unit)[1:]]
                for k in keys:
                    v = ref.parse(kind, k.encode())
                    self.assertEqual(v[0], "ok", (kind, unit, k))
                    self.assertEqual(ref.canon(kind, v[1]), k)
                    self.assertEqual(ref.bucket(kind, v[1], unit), v[1], (kind, unit, k))
                # grouping the keys again by the same unit gives them back, one row each
                again = group_lines(self.s, kind, keys, unit)
                self.assertEqual(again, ["v,count"] + [k + ",1" for k in keys], (kind, unit))
                # and a coarser unit of the keys is the coarser unit of the cells
                for coarser in ref.UNITS_OF[kind][ref.UNITS_OF[kind].index(unit) + 1:]:
                    self.assertEqual([l.rsplit(",", 1)[0] for l in group_lines(self.s, kind, keys, coarser)[1:]],
                                     [l.rsplit(",", 1)[0] for l in group_lines(self.s, kind, cells, coarser)[1:]], (kind, unit, coarser))

    def test_the_printed_key_selects_exactly_the_rows_of_its_bucket(self):
        rng = random.Random(32)
        for kind in KINDS:
            cells = [ref.random_valid(rng, kind) for _ in range(200)] + ref.first_value_cells(kind, rng, 150)
            for unit in ref.UNITS_OF[kind]:
                self.s.write("rt.csv", csv_text([["v"]] + [[c] for c in cells]))
                lines = group_lines(self.s, kind, cells, unit)[1:]
                keys = [l.rsplit(",", 1)[0] for l in lines]
                counts = [int(l.rsplit(",", 1)[1]) for l in lines]
                t = "v" + SUFFIX[kind]
                for i, k in enumerate(keys):
                    cond = "%s >= '%s'" % (t, k)
                    if i + 1 < len(keys):
                        cond += " and %s < '%s'" % (t, keys[i + 1])
                    r = run("--root", self.s.dir, "--where", cond, "--agg", "count", "--format", "csv", "rt.csv")
                    self.assertEqual(r.stdout.decode().split(), ["count", str(counts[i])], (kind, unit, k))
                    # the rows whose value is the key itself are a subset: the first value of the bucket is a value of the type
                    r = run("--root", self.s.dir, "--where", "%s = '%s'" % (t, k), "--agg", "count", "--format", "csv", "rt.csv")
                    shown = r.stdout.decode().split()
                    self.assertEqual(int(shown[1]) if len(shown) > 1 else 0, len([c for c in cells if ref.parse(kind, c.encode())[1] == ref.parse(kind, k.encode())[1]]), (kind, unit, k))

    def test_where_on_the_grouped_column_and_group_with_a_bucket(self):
        data = csv_text([["id", "ts"], ["1", "2026-01-01T23:00:00Z"], ["2", "2026-01-02T01:00:00+02:00"], ["3", "2026-01-02T00:00:00Z"], ["4", "2026-01-03T10:00:00Z"], ["5", "2025-12-31T23:59:59Z"]])
        r = self.s.table("w.csv", data, "--where", "ts:timestamp >= '2026-01-01T23:00:00Z'", "--group", "ts:timestamp@day", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,count", "2026-01-01T00:00:00Z,2", "2026-01-02T00:00:00Z,1", "2026-01-03T00:00:00Z,1"])
        r = self.s.table("w.csv", data, "--where", "ts:timestamp < '2026-01-02T00:00:00Z'", "--group", "ts:timestamp@month", "--agg", "count,distinct:ts:timestamp,min:ts:timestamp", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,count,distinct:ts,min:ts", "2025-12-01T00:00:00Z,1,1,2025-12-31T23:59:59Z", "2026-01-01T00:00:00Z,2,1,2026-01-01T23:00:00Z"])


# ---- the refusals of the spelling ---------------------------------------------------------------------------------------------------

class Spelling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", "id,ts,d,w,s,ts@day,x:timestamp@day,a@b\n1,2026-01-02T01:00:00+02:00,2026-01-02,2026-01-02 01:00:00,a,p,q,r\n2,2026-01-01T23:30:00Z,2026-02-10,2026-01-31 23:59:59,b,p,q,r\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def go(self, *flags):
        return run("--root", self.s.dir, *flags, "t.csv")

    def test_the_built_units_are_accepted(self):
        for kind, col in (("timestamp", "ts"), ("date", "d"), ("datetime", "w")):
            for unit in ref.UNITS_OF[kind]:
                r = self.go("--group", "%s%s@%s" % (col, SUFFIX[kind], unit), "--format", "csv")
                self.assertEqual(r.status, 0, (col, unit, r.stdout[:300]))

    def test_each_refused_spelling_has_a_tag_a_reason_and_a_hint(self):
        cases = []
        for kind, col in (("timestamp", "ts"), ("date", "d"), ("datetime", "w")):
            for unit in ("week", "minute", "second", "hour", "day", "DAY", "Day", "days", "months", "MONTH", "weeks", "quarter", "decade", "century", "ms", "us", "", " day", "day ", "1", "0", "-day", "d", "y", "dayx", "day@month", "day@", "@day", "hour,day"):
                if "," in unit:
                    continue
                if ref.bucket_refusal(kind, unit) is not None:
                    cases.append((col + SUFFIX[kind] + "@" + unit, kind, unit))
        self.assertGreater(len(cases), 60)
        for item, kind, unit in cases:
            r = self.go("--group", item)
            err = refusal_doc(r)
            want = ref.bucket_refusal(kind, unit)
            self.assertEqual((r.status, err["code"], err["rule"]), (2, "INVALID_ARGS", "args.bad-value"), (item, r.stdout[:300]))
            d = err["detail"]
            self.assertEqual((d["flags"], d["item"], d["unit"], d["reason"]), ("--group", item, unit, want), (item, d))
            self.assertIsNone(err["repair"], item)
            self.assertEqual(validate(r), [], item)
            if want == "week-not-built":
                self.assertIn("week is not built", err["hint"])
            if want == "finer-than-type":
                self.assertIn("finer", err["hint"])
            for word in ("hour", "day", "month", "year"):
                self.assertIn(word, err["hint"], item)
        # the same word in a list: the item that is wrong is the one named, the others are read
        r = self.go("--group", "s,ts:timestamp@week,d:date@month")
        self.assertEqual((r.first_rule(), r.error()["detail"]["item"]), ("args.bad-value", "ts:timestamp@week"))

    def test_the_hint_for_week_and_for_a_finer_unit_say_what_to_write(self):
        err = refusal_doc(self.go("--group", "ts:timestamp@week"))
        self.assertIn("week is not built", err["hint"])
        self.assertIn("@day", err["hint"])
        err = refusal_doc(self.go("--group", "d:date@hour"))
        self.assertIn(":date has no hour", err["hint"])
        self.assertIn("@month", err["hint"])

    def test_a_refused_unit_is_refused_before_a_row_is_read(self):
        r = run("--root", self.s.dir, "--group", "ts:timestamp@week", "--where", "id:int > 0", "no-such-file.csv")
        self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"))

    def test_a_bucket_in_agg_is_refused_as_a_bad_spec(self):
        for item in ("min:ts:timestamp@day", "max:d:date@month", "distinct:w:datetime@hour", "min:ts:timestamp@week", "sum:ts:timestamp@day", "mean:d:date@month", "min:ts:timestamp@", "min:ts:timestamp@DAY"):
            r = self.go("--agg", "count," + item)
            err = refusal_doc(r)
            self.assertEqual((r.status, err["rule"]), (2, "agg.bad-spec"), (item, r.stdout[:300]))
            self.assertEqual(err["detail"]["agg"], "count," + item)
            self.assertEqual(err["detail"]["item"], 1)
            self.assertIn("--group ts:timestamp@day", err["hint"])
            self.assertIn("group key", err["hint"])
        # without a date type the item is a column name, as it was
        r = self.go("--agg", "min:zz@day")
        self.assertEqual((r.first_rule(), r.status), ("column.unknown", 3))
        # (and a column that really is called ts@day is the integer minimum of it, as it was)
        r = self.go("--agg", "min:ts@day")
        self.assertEqual((r.first_rule(), r.status), ("value.not-integer", 8))

    def test_a_bucket_in_order_by_is_not_read(self):
        # --order-by is not a group key: the item is a column name, as it was (and there is none of that name)
        r = self.go("--order-by", "ts:timestamp@day", "--select", "id")
        self.assertEqual((r.first_rule(), r.status, r.error()["detail"]["name"]), ("column.unknown", 3, "ts:timestamp@day"))
        r = self.go("--where", "ts:timestamp@day > 'x'")
        self.assertNotEqual(r.status, 0)

    def test_the_query_has_no_bucket_and_its_hint_names_the_flag(self):
        r = self.go("--query", "select id, count(*) from 't.csv' group by id::dec(2)")
        err = refusal_doc(r)
        self.assertEqual((r.status, err["rule"], err["detail"]["feature"]), (2, "query.unsupported", "cast-in-group"))
        self.assertIn("--group ts:timestamp@day", err["hint"])
        self.assertIn("--group x:int, x:dec(N) or x:float", err["hint"])
        # a date type in the query is not built (T3): the refusal has not changed
        r = self.go("--query", "select count(*) from 't.csv' group by cast(ts as timestamp)")
        self.assertEqual((r.status, r.first_rule()), (2, "query.syntax"))

    def test_near_strings_stay_names(self):
        # a header that ends in @day, with no date type before it, is a name
        r = self.go("--group", "ts@day", "--format", "csv")
        self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["ts@day,count", "p,2"]))
        r = self.go("--group", "a@b", "--format", "csv")
        self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["a@b,count", "r,2"]))
        # a header that really ends in :timestamp@day is written with its colon escaped, as one that ends in :int is
        r = self.go("--group", "x\\:timestamp@day", "--format", "csv")
        self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["x:timestamp@day,count", "q,2"]))
        # an integer, a decimal or a float is not bucketed: the item is a name, and there is none of that name
        for item in ("n:int@day", "n:dec(2)@day", "n:float@day", "ts:int@day"):
            r = self.go("--group", item)
            self.assertEqual((r.first_rule(), r.status, r.error()["detail"]["name"]), ("column.unknown", 3, item), item)
        # a name that has an @ before the type: the bucket is the first @ after a date type
        s = Scratch()
        try:
            s.write("a.csv", "a@b,c\n2026-01-02T00:00:00Z,x\n2026-01-02T05:00:00Z,y\n")
            r = run("--root", s.dir, "--group", "a@b:timestamp@day", "--format", "csv", "a.csv")
            self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["a@b,count", "2026-01-02T00:00:00Z,2"]))
            r = run("--root", s.dir, "--group", "a@b:timestamp@day@month", "a.csv")
            self.assertEqual((r.first_rule(), r.error()["detail"]["unit"]), ("args.bad-value", "day@month"))
        finally:
            s.cleanup()
        # the suffix alone, and a bucket on nothing, stay names
        for item in (":timestamp@day", ":date@month", "@day", "@"):
            r = self.go("--group", item)
            self.assertEqual((r.first_rule(), r.status, r.error()["detail"]["name"]), ("column.unknown", 3, item), item)

    def test_a_bucket_on_a_text_or_position_key(self):
        r = self.go("--group", "#2:timestamp@day", "--format", "csv")
        self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["ts,count", "2026-01-01T00:00:00Z,2"]))
        r = self.go("--group", "s@day")
        self.assertEqual((r.first_rule(), r.status), ("column.unknown", 3))


class SameColumn(unittest.TestCase):
    """The conflict check compares the base type and ignores the unit; two keys on one column are independent keys."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", "id,ts,d\n1,2026-01-02T01:00:00+02:00,2026-01-02\n2,2026-01-01T23:30:00Z,2026-02-10\n3,2026-02-01T00:00:00Z,2026-02-11\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def go(self, *flags):
        return run("--root", self.s.dir, *flags, "t.csv")

    def test_a_filter_and_a_bucket_on_one_column_are_one_type(self):
        r = self.go("--where", "ts:timestamp >= '2026-01-01T00:00:00Z' and ts:timestamp < '2026-02-01T00:00:00Z'", "--group", "ts:timestamp@day", "--agg", "min:ts:timestamp,distinct:ts:timestamp", "--format", "csv")
        self.assertEqual(r.status, 0)
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,min:ts,distinct:ts", "2026-01-01T00:00:00Z,2026-01-01T23:00:00Z,2"])

    def test_base_type_conflicts_are_unchanged_by_the_unit(self):
        for a, b in (("ts:timestamp@day", "ts:date"), ("ts:timestamp@month", "ts:datetime"), ("ts:timestamp@day", "ts:int"), ("ts:datetime@year", "ts:timestamp"), ("ts:date@month", "ts:dec(2)")):
            r = self.go("--group", a + "," + b)
            self.assertEqual((r.first_rule(), r.status), ("column.type-conflict", 2), (a, b, r.stdout[:300]))
            r = self.go("--group", a, "--agg", "min:" + b)
            self.assertEqual((r.first_rule(), r.status), ("column.type-conflict", 2), (a, b))
        r = self.go("--where", "ts:date > '2026-01-01'", "--group", "ts:timestamp@day")
        self.assertEqual((r.first_rule(), r.status), ("column.type-conflict", 2))
        # types in the error are the base types, in the order of the mentions (no unit in them)
        r = self.go("--group", "ts:timestamp@day", "--agg", "min:ts:date")
        self.assertEqual(r.error()["detail"]["types"], [":date", ":timestamp"])

    def test_two_units_on_one_column_are_two_keys(self):
        r = self.go("--group", "ts:timestamp@month,ts:timestamp@day", "--format", "csv")
        self.assertEqual((r.status, r.stdout.decode().splitlines()), (0, ["ts,ts,count", "2026-01-01T00:00:00Z,2026-01-01T00:00:00Z,2", "2026-02-01T00:00:00Z,2026-02-01T00:00:00Z,1"]))
        r = self.go("--group", "ts:timestamp@day,ts:timestamp@day", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,ts,count", "2026-01-01T00:00:00Z,2026-01-01T00:00:00Z,2", "2026-02-01T00:00:00Z,2026-02-01T00:00:00Z,1"])
        r = self.go("--group", "ts:timestamp@hour,ts:timestamp@year", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,ts,count", "2026-01-01T23:00:00Z,2026-01-01T00:00:00Z,2", "2026-02-01T00:00:00Z,2026-01-01T00:00:00Z,1"])

    def test_a_bucket_with_and_without_the_bucket_or_as_text_are_independent_keys(self):
        r = self.go("--group", "ts:timestamp@year,ts:timestamp", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,ts,count", "2026-01-01T00:00:00Z,2026-01-01T23:00:00Z,1", "2026-01-01T00:00:00Z,2026-01-01T23:30:00Z,1", "2026-01-01T00:00:00Z,2026-02-01T00:00:00Z,1"])
        r = self.go("--group", "ts:timestamp@day,ts", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["ts,ts,count", "2026-01-01T00:00:00Z,2026-01-01T23:30:00Z,1", "2026-01-01T00:00:00Z,2026-01-02T01:00:00+02:00,1", "2026-02-01T00:00:00Z,2026-02-01T00:00:00Z,1"])

    def test_sort_and_top_on_the_aggregates_of_a_bucketed_group(self):
        data = csv_text([["id", "ts", "n"]] + [[str(i), "2026-01-%02dT%02d:00:00Z" % (1 + i % 3, i % 24), str(i)] for i in range(1, 40)])
        r = self.s.table("sd.csv", data, "--group", "ts:timestamp@day", "--agg", "count,max:ts:timestamp,min:n:int", "--sort", "-count", "--format", "csv")
        self.assertEqual(r.status, 0)
        lines = r.stdout.decode().splitlines()
        counts = [int(l.split(",")[1]) for l in lines[1:]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        r = self.s.table("sd.csv", data, "--group", "ts:timestamp@day", "--agg", "count,max:ts:timestamp", "--sort", "-max:ts", "--top", "2", "--format", "csv")
        self.assertEqual([l.split(",")[0] for l in r.stdout.decode().splitlines()], ["ts", "2026-01-03T00:00:00Z", "2026-01-02T00:00:00Z"])


# ---- G-DIFF ---------------------------------------------------------------------------------------------------------------------------

KIND_OF = {"t": "timestamp", "d": "date", "w": "datetime"}


def make_table(rng, rows):
    pool_t = ref.first_value_cells("timestamp", rng, 14) + [ref.random_valid(rng, "timestamp") for _ in range(4)] + ["2026-01-01T23:00:00Z", "2026-01-02T01:00:00+02:00", "2026-01-31T23:59:59.999999-12:00"]
    pool_d = ref.first_value_cells("date", rng, 8) + [ref.random_valid(rng, "date") for _ in range(3)] + ["2026-01-01", "2026-01-31", "2000-02-29"]
    pool_w = ref.first_value_cells("datetime", rng, 10) + [ref.random_valid(rng, "datetime") for _ in range(3)] + ["2026-01-01 23:00:00", "2026-01-31T23:59:59.5"]
    out = [[str(i + 1), rng.choice("abc"), rng.choice(pool_t), rng.choice(pool_d), rng.choice(pool_w)] for i in range(rows)]
    return ["id", "k", "t", "d", "w"], out


def plan(rng, rows, idx):
    cols = list(KIND_OF)
    keys = []
    for _ in range(rng.randint(1, 3)):
        c = rng.choice(cols + ["k"])
        if c == "k":
            keys.append(("k", None, False))
        else:
            r = rng.random()
            if r < 0.7:
                keys.append((c, rng.choice(ref.UNITS_OF[KIND_OF[c]]), True))
            elif r < 0.85:
                keys.append((c, None, True))
            else:
                keys.append((c, None, False))
    p = {"keys": keys, "aggs": [rng.choice([("count", None), ("min", rng.choice(cols)), ("max", rng.choice(cols)), ("distinct", rng.choice(cols))]) for _ in range(rng.randint(0, 3))]}
    if rng.random() < 0.5:
        col = rng.choice(cols)
        op = rng.choice(["=", "!=", "<", "<=", ">", ">="])
        lit = rows[rng.randrange(len(rows))][idx[col]] if rng.random() < 0.8 else ref.random_valid(rng, KIND_OF[col])
        p["where"] = (col, op, lit)
    p["sort"] = rng.choice([None, None, "-count", "count"]) if any(fn == "count" for fn, _ in p["aggs"]) or not p["aggs"] else None
    return p


def item_of(key):
    c, unit, typed = key
    if not typed:
        return c
    return c + SUFFIX[KIND_OF[c]] + ("@" + unit if unit else "")


def flags_of(p, fmt="csv"):
    f = []
    if p.get("where"):
        col, op, lit = p["where"]
        f += ["--where", "%s%s %s '%s'" % (col, SUFFIX[KIND_OF[col]], op, lit)]
    f += ["--group", ",".join(item_of(k) for k in p["keys"])]
    items = ["count" if fn == "count" else "%s:%s%s" % (fn, c, SUFFIX[KIND_OF[c]]) for fn, c in p["aggs"]]
    if items:
        f += ["--agg", ",".join(items)]
    if p["sort"]:
        f += ["--sort", p["sort"]]
    return f + ["--format", fmt]


def expected(p, header, rows):
    """(labels, list of rows of strings) of the plan, evaluated in Python."""
    idx = {n: i for i, n in enumerate(header)}

    def val(col, row):
        r = ref.parse(KIND_OF[col], row[idx[col]].encode())
        assert r[0] == "ok", (col, row)
        return r[1]

    kept = rows
    if p.get("where"):
        col, op, lit = p["where"]
        w = ref.parse(KIND_OF[col], lit.encode())[1]
        test = {"=": lambda v: v == w, "!=": lambda v: v != w, "<": lambda v: v < w, "<=": lambda v: v <= w, ">": lambda v: v > w, ">=": lambda v: v >= w}[op]
        kept = [r for r in rows if test(val(col, r))]
    groups = {}
    for r in kept:
        key = []
        for c, unit, typed in p["keys"]:
            if c == "k":
                key.append(r[idx[c]])
            elif typed:
                v = val(c, r)
                key.append(ref.bucket(KIND_OF[c], v, unit) if unit else v)
            else:
                key.append(r[idx[c]])
        g = groups.setdefault(tuple(key), {"n": 0, "rows": []})
        g["n"] += 1
        g["rows"].append(r)
    labels = [k[0] for k in p["keys"]] + [("count" if fn == "count" else "%s:%s" % (fn, c)) for fn, c in p["aggs"]]
    if not p["aggs"]:
        labels.append("count")
    out = []
    for key, g in groups.items():
        cells = []
        for (c, unit, typed), x in zip(p["keys"], key):
            cells.append(ref.canon(KIND_OF[c], x) if c != "k" and typed else x)
        if not p["aggs"]:
            cells.append(str(g["n"]))
        for fn, c in p["aggs"]:
            if fn == "count":
                cells.append(str(g["n"]))
            else:
                vs = [val(c, r) for r in g["rows"]]
                cells.append(str(len(set(vs))) if fn == "distinct" else ref.canon(KIND_OF[c], min(vs) if fn == "min" else max(vs)))
        out.append((key, g["n"], cells))
    out.sort(key=lambda e: tuple((x if isinstance(x, int) else x.encode()) for x in e[0]))
    if p["sort"]:
        # ties keep the order of the keys
        ci = labels.index("count") if "count" in labels else len(labels) - 1
        out.sort(key=lambda e: -e[1] if p["sort"] == "-count" else e[1])
    return labels, [e[2] for e in out]


class Diff(unittest.TestCase):
    """G-DIFF: random tables and plans, one and four threads, csv, json and jsonl, against a Python evaluation."""

    def test_random_tables_and_plans(self):
        rng = random.Random(2027)
        s = Scratch()
        try:
            for n in range(DT2_TABLES):
                header, rows = make_table(rng, rng.randint(1, 60))
                s.write("p.csv", csv_text([header] + rows))
                idx = {c: i for i, c in enumerate(header)}
                for _ in range(8):
                    p = plan(rng, rows, idx)
                    labels, want = expected(p, header, rows)
                    want_csv = [",".join(labels)] + [",".join(r) for r in want]
                    for extra in ([], ["--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", "0"]):
                        r = run("--root", s.dir, *flags_of(p), *extra, *BIG, "p.csv")
                        self.assertEqual(r.status, 0, (flags_of(p), r.stdout[:300], r.stderr[:300]))
                        self.assertEqual(r.stdout.decode().splitlines(), want_csv, (flags_of(p), extra))
                    if len(set(labels)) == len(labels):
                        r = run("--root", s.dir, *flags_of(p, "jsonl"), *BIG, "p.csv")
                        got = [json.loads(l) for l in r.stdout.decode().splitlines()]
                        self.assertEqual(len(got), len(want), flags_of(p))
                        for obj, row in zip(got, want):
                            self.assertEqual(list(obj), labels)
                            for lab, v in zip(labels, row):
                                self.assertEqual(str(obj[lab]), v, (flags_of(p), lab))
                            # a key of a date type, a minimum and a maximum are JSON strings; a count and a distinct are numbers
                            for lab in labels:
                                if lab == "count" or lab.startswith("distinct:"):
                                    self.assertIsInstance(obj[lab], int)
                                elif lab in KIND_OF or lab.startswith(("min:", "max:")):
                                    if not (lab == "k"):
                                        self.assertIsInstance(obj[lab], str, (lab, obj))
                    r = run("--root", s.dir, *flags_of(p, "json"), *BIG, "p.csv")
                    self.assertEqual(r.data()["rows"], want, flags_of(p))
        finally:
            s.cleanup()


# ---- G-PAR ----------------------------------------------------------------------------------------------------------------------------

class Parallel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        rng = random.Random(41)
        pool = ref.first_value_cells("timestamp", rng, 60) + [ref.random_valid(rng, "timestamp") for _ in range(40)]
        poold = ref.first_value_cells("date", rng, 30)
        poolw = ref.first_value_cells("datetime", rng, 30)
        rows = [[str(i), rng.choice(pool), rng.choice(poold), rng.choice(poolw)] for i in range(1, 801)]
        cls.s.write("big.csv", csv_text([["id", "t", "d", "w"]] + rows))
        bad = [list(r) for r in rows]
        bad[300][1] = "2026-01-02T01:00:00"
        bad[500][1] = "2026-02-30T01:00:00Z"
        bad[700][2] = "x"
        cls.s.write("bad.csv", csv_text([["id", "t", "d", "w"]] + bad))
        cls.plans = [
            ["--group", "t:timestamp@day", "--format", "csv", *BIG],
            ["--group", "t:timestamp@hour", "--agg", "count,min:t:timestamp,max:t:timestamp,distinct:w:datetime", "--format", "csv", *BIG],
            ["--group", "d:date@month,t:timestamp@year", "--agg", "min:w:datetime,count", "--sort", "-count", "--format", "csv", *BIG],
            ["--group", "w:datetime@day", "--agg", "distinct:t:timestamp", "--format", "jsonl", *BIG],
            ["--where", "t:timestamp >= '2000-01-01T00:00:00Z'", "--group", "t:timestamp@month", "--format", "csv", *BIG],
            ["--group", "d:date@year,d:date@month", "--format", "json", *BIG],
        ]

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def same(self, name, plan):
        base = run("--root", self.s.dir, *plan, name)
        for threads in (2, 3, 4, 8, 16, 64):
            for chunk in (1, 7, 64, 1000):
                got = run("--root", self.s.dir, *plan, "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, name)
                shown = re.sub(rb'"--threads","[0-9]+","--chunk-bytes","[0-9]+","--parallel-min-bytes","0",', b"", got.stdout)
                self.assertEqual((got.status, shown, got.stderr), (base.status, base.stdout, base.stderr), (plan, threads, chunk))
        data = (self.s.dir / name).read_bytes()
        via = run("--root", self.s.dir, *plan, stdin=data)
        self.assertEqual(via.status, base.status)
        if base.status == 0:
            self.assertEqual(via.stdout, base.stdout)
        else:
            a, b = base.error(), via.error()
            keys = ("context", "column", "row", "line", "value", "reason")
            self.assertEqual((a["rule"],) + tuple(a["detail"].get(k) for k in keys), (b["rule"],) + tuple(b["detail"].get(k) for k in keys))
        return base

    def test_the_same_bytes_at_any_thread_count(self):
        for plan in self.plans:
            base = self.same("big.csv", plan)
            self.assertEqual(base.status, 0, (plan, base.stdout[:200], base.stderr[:200]))

    def test_the_first_refusal_in_file_order_at_any_thread_count(self):
        for plan in (["--group", "t:timestamp@day"], ["--group", "d:date@month,t:timestamp@year"], ["--group", "w:datetime@hour,d:date@year"], ["--group", "d:date@month", "--agg", "min:t:timestamp"]):
            base = self.same("bad.csv", plan)
            self.assertEqual(base.status, 8, plan)
            d = base.error()["detail"]
            self.assertEqual(d["row"], 301 if "t:timestamp" in " ".join(plan) else 701, plan)

    def test_a_bucket_and_a_plain_key_agree_on_the_rows_counted(self):
        a = run("--root", self.s.dir, "--group", "t:timestamp@year", "--agg", "count", "--format", "csv", *BIG, "big.csv").stdout.decode().splitlines()[1:]
        self.assertEqual(sum(int(l.rsplit(",", 1)[1]) for l in a), 800)


# ---- --explain, help, introspect ------------------------------------------------------------------------------------------------------

class Surface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", "id,ts,d,w\n1,2026-01-01T23:00:00Z,2026-01-01,2026-01-01 23:00:00\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def go(self, *flags):
        return run("--root", self.s.dir, *flags, "t.csv")

    def test_explain_prints_the_bucket_of_a_key(self):
        r = self.go("--explain", "--group", "ts:timestamp@day,d:date@month,w:datetime,id", "--agg", "count")
        self.assertEqual(r.status, 0, r.stdout[:300])
        group = r.data()["explain"]["plan"]["group"]
        self.assertEqual([g.get("bucket") for g in group], ["day", "month", None, None])
        self.assertEqual([g["type"] for g in group], ["timestamp", "date", "datetime", "text"])
        self.assertEqual([g["column"]["name"] for g in group], ["ts", "d", "w", "id"])
        possible = r.data()["explain"]["refusals"]["possible"]
        for tag in ("value.not-timestamp", "value.timezone-missing", "value.not-date", "value.timestamp-offset-present"):
            self.assertIn(tag, possible)

    def test_explain_without_a_bucket_has_no_bucket_key(self):
        for flags in (["--group", "ts:timestamp"], ["--group", "d:date,w:datetime"], ["--group", "id"], ["--group", "ts:timestamp", "--where", "d:date > '2020-01-01'"]):
            r = self.go("--explain", *flags)
            self.assertNotIn(b"bucket", r.stdout, flags)

    def test_the_help_and_introspect_say_the_bucket(self):
        flags = {f["name"]: f for f in introspect()["flags"]}
        help_text = flags["--group"]["help"]
        for word in ("@day", "@month", "@hour", "@year", "UTC", "first"):
            self.assertIn(word, help_text, word)
        self.assertNotIn("@week", help_text)
        self.assertIn("@UNIT", help_text)

    def test_the_docs_and_the_readme_say_the_bucket(self):
        for name in ("README.md", "docs/filter.md", "docs/refusals.md", "docs/datetime.md"):
            text = (ROOT / name).read_text()
            self.assertIn("@day", text, name)
        doc = (ROOT / "docs/datetime.md").read_text()
        self.assertIn("## 19. T2 as built", doc)
        self.assertNotIn("T2 and T3 are not built", doc)


# ---- G-COMPAT: a plan without an @ means what it meant ---------------------------------------------------------------------------------

class Compat(unittest.TestCase):
    """MAIN_TABLE names the binary of `main` before this slice (a build of origin/main); the explain, the answer and the refusal of every plan without an `@` are then compared with it,
    byte for byte. Without it the class is skipped: the md5 of the 166 plans of scripts/corpus.py is `test_rename_b1_compat`'s, and the pinned hint of the query is the query tests'."""

    PLANS = [
        ["--group", "ts:timestamp"], ["--group", "d:date,w:datetime"], ["--group", "id"], ["--group", "ts:timestamp,id", "--agg", "count,min:ts:timestamp,distinct:d:date"],
        ["--group", "n:int", "--agg", "sum:n"], ["--group", "p:dec(2)", "--agg", "mean:p:dec(2)@4"], ["--group", "x:float"], ["--group", "ts@day"], ["--group", "x\\:timestamp@day"],
        ["--where", "ts:timestamp >= '2026-01-01T00:00:00Z'", "--group", "ts:timestamp"], ["--where", "d:date > '2026-01-01' or w:datetime < '2026-03-01 00:00:00'", "--group", "id"],
        ["--agg", "min:ts:timestamp,max:ts:timestamp,count"], ["--order-by", "-ts:timestamp", "--select", "id"], ["--select", "id,ts"], ["--select", "ts"],
        ["--group", "ts:timestamp", "--sort", "-count", "--top", "1"], ["--group", "d:date", "--format", "jsonl"], ["--group", "d:date", "--format", "json"], ["--group", "ts:date"],
        ["--group", "ts:timestamp", "--agg", "sum:ts:timestamp"], ["--group", "nosuch:timestamp"], ["--group", "ts:timestamp,ts:date"], ["--group", "ts:dec(19)"], ["--query", "select count(*) from 't.csv' group by id"],
    ]

    @classmethod
    def setUpClass(cls):
        cls.main = os.environ.get("MAIN_TABLE")
        cls.s = Scratch()
        cls.s.write("t.csv", "id,ts,d,w,n,p,x,ts@day,x:timestamp@day\n1,2026-01-02T01:00:00+02:00,2026-01-02,2026-01-02 01:00:00,5,1.50,1.5e3,a,b\n2,2026-01-01T23:30:00Z,2026-02-10,2026-01-31 23:59:59,6,2.25,2,c,d\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_plans_without_a_bucket_give_main_s_bytes(self):
        if not self.main or not os.path.exists(self.main):
            self.skipTest("MAIN_TABLE names no binary")
        from harness import binary
        for plan in self.PLANS:
            for extra in ([], ["--explain"], ["--threads", "4", "--chunk-bytes", "8", "--parallel-min-bytes", "0"]):
                argv = ["--root", str(self.s.dir), *plan, *extra, "t.csv"]
                mine = subprocess.run([binary(), *argv], capture_output=True, input=b"")
                theirs = subprocess.run([self.main, *argv], capture_output=True, input=b"")
                self.assertEqual((mine.returncode, mine.stdout, mine.stderr), (theirs.returncode, theirs.stdout, theirs.stderr), (plan, extra))


# ---- G-FUZZ ---------------------------------------------------------------------------------------------------------------------------

class Fuzz(unittest.TestCase):
    """No trap, no hang: group items made of the pieces of the grammar and of the near strings; every refusal has a rule tag."""

    def test_random_group_items_never_trap(self):
        rng = random.Random(51)
        s = Scratch()
        pieces = ["ts", "d", "w", "id", ":timestamp", ":date", ":datetime", ":int", ":dec(2)", ":float", "@", "@day", "@hour", "@month", "@year", "@week", "@DAY", "@x", "\\", "\\:", "\\@", ",", "#2", "#", "'", "\"",
                  " ", "é", ":", "@@", "@day@day", "()", "-", "0", "99", "\x01"]
        try:
            s.write("z.csv", "id,ts,d,w\n1,2026-01-01T23:00:00Z,2026-01-01,2026-01-01 23:00:00\n2,2026-02-01T00:00:00+01:00,2026-02-01,2026-02-01 00:00:00\n")
            tags = {r["rule"] for r in introspect()["rules"]}
            n = 0
            for _ in range(600):
                item = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 7)))
                if "\x00" in item:
                    continue
                r = run("--root", s.dir, "--group", item, "z.csv")
                self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11), item)
                self.assertIn(r.status, (0, 2, 3, 8), (item, r.status, r.stdout[:200]))
                if r.status != 0:
                    self.assertIn(r.error()["rule"], tags, item)
                    self.assertIsNotNone(r.error().get("hint"), item)
                n += 1
            self.assertGreater(n, 500)
        finally:
            s.cleanup()

    def test_bucket_with_odd_cells_and_a_long_unit(self):
        s = Scratch()
        try:
            for cell in ("", "x", "2026-01-02T01:00:00." + "1" * 3000 + "Z", "\xc3\xa9" * 40):
                s.write("o.csv", 'id,v\n1,"%s"\n' % cell)
                for kind in KINDS:
                    r = run("--root", s.dir, "--group", "v%s@month" % SUFFIX[kind], "o.csv")
                    self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11))
                    self.assertEqual(r.status, 8)
            r = run("--root", s.dir, "--group", "v:timestamp@" + "x" * 5000, "o.csv")
            self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"))
        finally:
            s.cleanup()


# ---- repairs --------------------------------------------------------------------------------------------------------------------------

class Repairs(unittest.TestCase):
    """A refusal of a cell in a bucketed key offers the same invocation with the type that is true and the same unit, when the unit is one of that type."""

    def test_repairs_keep_the_unit_when_the_new_type_has_it(self):
        s = Scratch()
        try:
            s.write("r.csv", "id,ts\n1,2026-01-02T01:00:00\n")
            r = run("--root", s.dir, "--group", "ts:timestamp@day", "r.csv")
            rep = r.error()["repair"]
            self.assertEqual(rep["kind"], "choose")
            self.assertEqual(rep["options"][0]["argv"][-3:], ["--group", "ts:datetime@day", "r.csv"])
            again = run_argv(rep["options"][0]["argv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2026-01-02T00:00:00", "1"]]))
            s.write("r.csv", "id,d\n1,2026-01-02\n")
            r = run("--root", s.dir, "--group", "d:timestamp@month", "r.csv")
            self.assertEqual(r.error()["detail"]["reason"], "date-only")
            again = run_argv(r.error()["repair"]["options"][0]["argv"])
            self.assertEqual(r.error()["repair"]["options"][0]["argv"][-3:], ["--group", "d:date@month", "r.csv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2026-01-01", "1"]]))
            # the unit of the old type is not one of the new: no repair, and it says why
            r = run("--root", s.dir, "--group", "d:timestamp@day", "r.csv")
            self.assertEqual((r.error()["detail"]["reason"], r.error()["repair"]["kind"]), ("date-only", "none"))
            s.write("r.csv", "id,d\n1,2026-01-02T00:00:00Z\n")
            r = run("--root", s.dir, "--group", "d:date@month", "r.csv")
            self.assertEqual(r.error()["detail"]["reason"], "time-part")
            self.assertEqual(r.error()["repair"]["options"][0]["argv"][-3:], ["--group", "d:timestamp@month", "r.csv"])
            again = run_argv(r.error()["repair"]["options"][0]["argv"])
            self.assertEqual((again.status, again.data()["rows"]), (0, [["2026-01-01T00:00:00Z", "1"]]))
            # an item with a bucket and another with the same old type: one repair rewrites both, or none is offered
            s.write("r.csv", "id,ts,u\n1,2026-01-02T01:00:00,2026-01-02T01:00:00\n")
            r = run("--root", s.dir, "--group", "ts:timestamp@day", "--agg", "min:u:timestamp", "r.csv")
            self.assertEqual(r.error()["repair"]["kind"], "none")
        finally:
            s.cleanup()


# ---- G-ORACLE-SQL ---------------------------------------------------------------------------------------------------------------------

class Oracle(unittest.TestCase):
    """DuckDB (`date_trunc`) and SQLite (`strftime`) agree with the tool on the bucket of valid cells. Neither is an oracle for refusals (docs/datetime.md section 3)."""

    def cells(self, kind, n, seed):
        rng = random.Random(seed)
        out = []
        for c in ref.first_value_cells(kind, rng, n // 2) + [ref.random_valid(rng, kind) for _ in range(n // 2)]:
            if kind != "date":
                c = c[:10] + "T" + c[11:]
            if kind == "timestamp":
                c = c[:-1] + "Z" if c.endswith("z") else c
                if c[-6] in "+-" and abs(int(c[-6:-3])) > 14:
                    continue
                if ref.parse(kind, c.encode())[0] != "ok":
                    continue
            out.append(c)
        return out

    def test_duckdb(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("no duckdb module")
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        s = Scratch()
        try:
            for kind in KINDS:
                cells = self.cells(kind, 3000, 61)
                cast = {"timestamp": "TIMESTAMPTZ", "date": "DATE", "datetime": "TIMESTAMP"}[kind]
                for unit in ref.UNITS_OF[kind]:
                    if kind == "timestamp":
                        sql = "SELECT v, epoch_us(date_trunc('%s', CAST(v AS TIMESTAMPTZ))) FROM (VALUES %s) t(v)" % (unit, ",".join("('%s')" % c for c in cells))
                    elif kind == "datetime":
                        sql = "SELECT v, epoch_us(date_trunc('%s', CAST(v AS TIMESTAMP))) FROM (VALUES %s) t(v)" % (unit, ",".join("('%s')" % c for c in cells))
                    else:
                        sql = "SELECT v, CAST(date_trunc('%s', CAST(v AS DATE)) AS DATE) - DATE '1970-01-01' FROM (VALUES %s) t(v)" % (unit, ",".join("('%s')" % c for c in cells))
                    got = {v: int(n) for v, n in con.execute(sql).fetchall()}
                    for c in cells:
                        self.assertEqual(ref.bucket(kind, ref.parse(kind, c.encode())[1], unit), got[c], (kind, unit, c))
                    lines = group_lines(s, kind, cells, unit)[1:]
                    keys = [ref.parse(kind, l.rsplit(",", 1)[0].encode())[1] for l in lines]
                    self.assertEqual(keys, sorted(set(got.values())), (kind, unit))
        finally:
            s.cleanup()

    def test_sqlite(self):
        db = sqlite3.connect(":memory:")
        s = Scratch()
        try:
            cells = [c for c in self.cells("timestamp", 3000, 62) if "." not in c]
            fmt = {"hour": "%Y-%m-%dT%H:00:00Z", "day": "%Y-%m-%dT00:00:00Z", "month": "%Y-%m-01T00:00:00Z", "year": "%Y-01-01T00:00:00Z"}
            for unit in UNITS:
                want = sorted({db.execute("select strftime(?, ?)", (fmt[unit], c)).fetchone()[0] for c in cells})
                keys = [l.rsplit(",", 1)[0] for l in group_lines(s, "timestamp", cells, unit)[1:]]
                self.assertEqual(keys, want, unit)
        finally:
            s.cleanup()


if __name__ == "__main__":
    unittest.main()
