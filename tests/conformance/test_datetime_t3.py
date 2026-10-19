"""The surface around the date types (docs/datetime.md section 13, slice T3): `--query` with `date`, `datetime` and `timestamp`, the types as join keys, `--report dates`, JSON lines out.

Gates written before the code, run on the built binary, as T0 and T2 were:

* G-CORPUS-EQ: `cast(ts as timestamp)` and `ts::date` in a condition, a sort and an aggregate give the bytes of the flag form (`--where ts:timestamp ...`), for random plans, at one and four
  threads, as csv and json, and `--explain` is the explain of the flag form; the refusals of a literal and of a type word are `query.syntax` with the column, the type and the value; a typed
  `group by` is `query.unsupported` and its hint names `--group ts:timestamp@day`. The generator is calibrated with the same templates over integers (the control that proves it is not vacuous).
* G-JOIN: `--on k=kk:timestamp` (and `:date`, `:datetime`, `USING`, several pairs): two spellings of one instant match, the rows are those of the Python join on the VALUE of the keys
  (`datetime_ref.join_rows`), for inner, left, semi and anti, at any thread count, from standard input and from JSON lines; a key cell that is not of the type is refused with its own rule
  and `context` `join`; a type on the probe column, two types, and a type word that is not one are `args.bad-value`; DuckDB is an oracle of the matches of valid cells.
* `--report dates`: the counters of `datetime_ref.report_row`, as json and csv, with `--select`, `--where`, `--limit`, at any thread count, from standard input; the refusals of the flags
  it does not go with; `--report types` is the bytes it was.
* JSON lines out for the types, in a join, in a query, in a group; the help, the docs, the MCP definition and the near strings.

DT3_PLANS (random plans, default 60), DT3_ROWS (rows of the generated tables, default 40) scale the generated parts.
"""

import json
import os
import random
import re
import unittest

import procs as subprocess

import datetime_ref as ref
from harness import ROOT, Scratch, introspect, run, run_argv, validate
from test_datetime import csv_text, SUFFIX

DT3_PLANS = int(os.environ.get("DT3_PLANS", "60"))
DT3_ROWS = int(os.environ.get("DT3_ROWS", "40"))
THREADS = ["--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", "0"]
KINDS = ref.KINDS
BIG = ["--max-groups", "1000000", "--max-state-bytes", "1073741824"]
MAIN_TABLE = os.environ.get("MAIN_TABLE")


def err(r):
    return r.doc()["error"]


def rows_of(r):
    return r.stdout.decode().splitlines()


# ---- --query ---------------------------------------------------------------------------------------------------------------------------

KIND_OF = {"t": "timestamp", "d": "date", "w": "datetime"}


def make_table(rng, n, calibrate=False):
    """id, k (text), t (timestamp), d (date), w (datetime); with `calibrate` the three typed columns hold integers (the control: the same templates over `:int`)."""
    if calibrate:
        pool = {c: [str(rng.randint(-5, 30)) for _ in range(8)] for c in KIND_OF}
    else:
        pool = {"t": ref.first_value_cells("timestamp", rng, 10) + ["2026-01-01T23:00:00Z", "2026-01-02T01:00:00+02:00"],
                "d": ref.first_value_cells("date", rng, 8) + ["2026-01-01", "2026-01-02"],
                "w": ref.first_value_cells("datetime", rng, 8) + ["2026-01-01 23:00:00", "2026-01-02T00:00:00.5"]}
    rows = [[str(i + 1), rng.choice("abc")] + [rng.choice(pool[c]) for c in "tdw"] for i in range(n)]
    return ["id", "k", "t", "d", "w"], rows


def spell(col, calibrate):
    return "int" if calibrate else KIND_OF[col]


def value_of(col, cell, calibrate):
    return int(cell) if calibrate else ref.parse(KIND_OF[col], cell.encode())[1]


def lit(rng, col, rows, header, calibrate):
    if rng.random() < 0.7:
        return rows[rng.randrange(len(rows))][header.index(col)]
    return str(rng.randint(-5, 30)) if calibrate else ref.random_valid(rng, KIND_OF[col])


def plan(rng, header, rows, calibrate):
    p = {"conds": [], "form": rng.choice(["rows", "rows", "group"])}
    for _ in range(rng.randint(0, 2)):
        col = rng.choice("tdw")
        op = rng.choice(["=", "!=", "<", "<=", ">", ">=", "in"])
        lits = [lit(rng, col, rows, header, calibrate) for _ in range(rng.randint(1, 3))] if op == "in" else [lit(rng, col, rows, header, calibrate)]
        p["conds"].append((col, op, lits))
    if p["form"] == "rows":
        p["order"] = [(rng.choice("tdw"), rng.random() < 0.4) for _ in range(rng.randint(0, 2))]
        p["limit"] = rng.choice([None, None, 3, 7])
        p["offset"] = rng.choice([None, None, 1]) if p["limit"] else None
    else:
        p["aggs"] = [rng.choice([("count", None), ("min", rng.choice("tdw")), ("max", rng.choice("tdw")), ("distinct", rng.choice("tdw"))]) for _ in range(rng.randint(1, 3))]
        p["sort"] = rng.choice([None, "count"]) if any(a[0] == "count" for a in p["aggs"]) else None
    return p


def flags_and_query(p, calibrate, rng):
    """(the flags, the query) of a plan: the same plan in the two spellings. The query spells a type `c::type` or `cast(c as type)`."""
    ty = lambda c: spell(c, calibrate)
    cast = lambda c: ("%s::%s" % (c, ty(c))) if rng.random() < 0.5 else ("cast(%s as %s)" % (c, ty(c)))
    conds_f, conds_q = [], []
    for col, op, lits in p["conds"]:
        if op == "in":
            conds_f.append("%s:%s in (%s)" % (col, ty(col), ", ".join("'%s'" % x for x in lits)))
            conds_q.append("%s in (%s)" % (cast(col), ", ".join("'%s'" % x for x in lits)))
        else:
            conds_f.append("%s:%s %s '%s'" % (col, ty(col), op, lits[0]))
            conds_q.append("%s %s '%s'" % (cast(col), op, lits[0]))
    flags = []
    q = []
    if p["form"] == "rows":
        flags += ["--select", "id"]
        q.append("select id")
    else:
        items_f = ["count" if fn == "count" else "%s:%s:%s" % (fn, c, ty(c)) for fn, c in p["aggs"]]
        items_q = []
        for fn, c in p["aggs"]:
            if fn == "count":
                items_q.append("count(*)")
            elif fn == "distinct":
                items_q.append("count(distinct %s)" % cast(c))
            else:
                items_q.append("%s(%s)" % (fn, cast(c)))
        flags += ["--group", "k", "--agg", ",".join(items_f)]
        q.append("select k, " + ", ".join(items_q))
    q.append("from 'f.csv'")
    if conds_f:
        flags += ["--where", " and ".join(conds_f)]
        q.append("where " + " and ".join(conds_q))
    if p["form"] == "group":
        q.append("group by k")
        if p["sort"]:
            flags += ["--sort", "-count"]
            q.append("order by count(*) desc")
    elif p["order"]:
        flags += ["--order-by", ",".join(("-" if d else "") + c + ":" + ty(c) for c, d in p["order"])]
        q.append("order by " + ", ".join(cast(c) + (" desc" if d else "") for c, d in p["order"]))
    if p["form"] == "rows" and p["limit"] is not None:
        flags += ["--limit", str(p["limit"])]
        q.append("limit %d" % p["limit"])
        if p["offset"] is not None:
            flags += ["--from", str(p["offset"])]
            q.append("offset %d" % p["offset"])
    return flags, " ".join(q)


def comparable(r):
    return (r.status, r.stdout, r.stderr)


class QueryEquivalence(unittest.TestCase):
    """G-CORPUS-EQ: the flags and the query of a plan give the same bytes."""

    def equivalence(self, calibrate, seed):
        rng = random.Random(seed)
        s = Scratch()
        try:
            ran = refused = 0
            for _ in range(DT3_PLANS):
                header, rows = make_table(rng, rng.randint(1, DT3_ROWS), calibrate)
                s.write("f.csv", csv_text([header] + rows))
                p = plan(rng, header, rows, calibrate)
                flags, query = flags_and_query(p, calibrate, rng)
                for fmt in ("json", "csv"):
                    for extra in ([], THREADS):
                        a = run("--root", s.dir, *flags, "--format", fmt, *extra, *BIG, "f.csv")
                        b = run("--root", s.dir, "--query", query, "--format", fmt, *extra, *BIG)
                        self.assertEqual(comparable(a), comparable(b), (flags, query, fmt, extra, a.stdout[:200], b.stdout[:200]))
                        ran += 1
                        refused += a.status != 0
                a = run("--root", s.dir, *flags, "--explain", "f.csv")
                b = run("--root", s.dir, "--query", query, "--explain")
                self.assertEqual(a.status, 0, a.stdout[:300])
                self.assertEqual(comparable(a), comparable(b), (flags, query))
            self.assertGreater(ran, 4 * DT3_PLANS - 1)
            return ran, refused
        finally:
            s.cleanup()

    def test_the_generator_over_integers_is_calibrated(self):
        ran, refused = self.equivalence(True, 5)
        self.assertEqual(refused, 0)

    def test_the_types_of_the_dates_in_a_condition_a_sort_and_an_aggregate(self):
        ran, refused = self.equivalence(False, 6)
        self.assertGreater(ran, 0)


class QuerySurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("f.csv", "id,k,ts,d,w\n1,a,2026-01-02T01:00:00+02:00,2026-01-02,2026-01-02 01:00:00\n2,b,2026-01-01T23:30:00Z,2026-02-10,2026-01-31 23:59:59\n3,a,2026-03-01T00:00:00Z,2025-12-31,2025-12-31T12:00:00.5\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def q(self, text, *more):
        return run("--root", self.s.dir, "--query", text, "--format", "csv", *more)

    def qj(self, text):
        return run("--root", self.s.dir, "--query", text)

    def test_the_spellings_of_a_type_give_the_flag_plan(self):
        want = run("--root", self.s.dir, "--where", "ts:timestamp >= '2026-01-02T00:00:00Z'", "--select", "id", "--order-by", "-ts:timestamp", "--format", "csv", "f.csv").stdout
        self.assertEqual(want.decode().split(), ["id", "3"])
        for text in ("select id from 'f.csv' where ts::timestamp >= '2026-01-02T00:00:00Z' order by ts::timestamp desc",
                     "select id from 'f.csv' where cast(ts as timestamp) >= '2026-01-02T00:00:00Z' order by cast(ts as timestamp) desc",
                     "SELECT id FROM 'f.csv' WHERE CAST(ts AS timestamp) >= '2026-01-02T00:00:00Z' ORDER BY ts::timestamp DESC"):
            r = self.q(text)
            self.assertEqual((r.status, r.stdout), (0, want), text)
        r = self.q("select id from 'f.csv' where d::date > '2026-01-01' and w::datetime < '2026-02-01 00:00:00' order by d::date")
        self.assertEqual(r.stdout.decode().split(), ["id", "1", "2"])

    def test_aggregates_of_the_three_types(self):
        r = self.q("select k, min(ts::timestamp), max(d::date), count(distinct w::datetime), count(*) from 'f.csv' group by k")
        self.assertEqual(rows_of(r), ["k,min:ts,max:d,distinct:w,count", "a,2026-01-01T23:00:00Z,2026-01-02,2,2", "b,2026-01-01T23:30:00Z,2026-02-10,1,1"])
        for fn in ("sum", "mean"):
            r = run("--root", self.s.dir, "--query", "select %s(ts::timestamp) from 'f.csv'" % fn)
            self.assertEqual((r.status, r.first_rule()), (2, "agg.bad-spec"), fn)

    def test_a_literal_that_is_not_of_the_type_is_query_syntax_with_the_column_the_type_and_the_value(self):
        cases = [("select id from 'f.csv' where ts::timestamp >= '2026-01-02'", "timestamp", "'2026-01-02'"),
                 ("select id from 'f.csv' where d::date = '2026-02-30'", "date", "'2026-02-30'"),
                 ("select id from 'f.csv' where w::datetime < '2026-01-02T00:00:00Z'", "datetime", "'2026-01-02T00:00:00Z'"),
                 ("select id from 'f.csv' where ts::timestamp in ('2026-01-02T00:00:00Z', 'x')", "timestamp", "'x'")]
        for text, ty, value in cases:
            r = self.qj(text)
            e = err(r)
            self.assertEqual((r.status, e["rule"]), (2, "query.syntax"), text)
            d = e["detail"]
            self.assertEqual((d["type"], d["value"], d["query"]), (ty, value, text), text)
            self.assertEqual(text[d["offset"]:d["offset"] + len(value)], value)
            self.assertEqual(validate(r), [])

    def test_contains_on_a_typed_column_is_refused(self):
        r = self.qj("select id from 'f.csv' where ts::timestamp contains 'x'")
        self.assertEqual((r.status, r.first_rule()), (2, "query.syntax"))
        self.assertIn("not contains", err(r)["detail"]["expected"])

    def test_a_cast_in_the_select_list_and_in_a_group_by_stay_unsupported(self):
        r = self.qj("select ts::date from 'f.csv'")
        self.assertEqual((r.status, r.first_rule(), err(r)["detail"]["feature"]), (2, "query.unsupported", "cast-in-select"))
        for text in ("select count(*) from 'f.csv' group by ts::timestamp", "select count(*) from 'f.csv' group by cast(d as date)", "select w, count(*) from 'f.csv' group by w::datetime"):
            r = self.qj(text)
            e = err(r)
            self.assertEqual((r.status, e["rule"], e["detail"]["feature"]), (2, "query.unsupported", "cast-in-group"), text)
            self.assertIn("--group ts:timestamp@day", e["hint"])

    def test_a_type_word_that_is_not_a_type_says_the_three_new_ones(self):
        r = self.qj("select id from 'f.csv' where ts::time > 'x'")
        e = err(r)
        self.assertEqual((r.status, e["rule"]), (2, "query.syntax"))
        for word in ("int", "dec(N)", "float", "date", "datetime", "timestamp"):
            self.assertIn(word, e["detail"]["expected"])
        self.assertEqual(e["detail"]["offset"], len("select id from 'f.csv' where ts::"))

    def test_near_strings_stay_names(self):
        s = Scratch()
        try:
            s.write("n.csv", "date,timestamp,datetime,id\n2026-01-02,x,y,1\n")
            r = run("--root", s.dir, "--query", "select id from 'n.csv' where date = '2026-01-02' and timestamp = 'x' and datetime = 'y'", "--format", "csv")
            self.assertEqual((r.status, r.stdout.decode().split()), (0, ["id", "1"]))
            r = run("--root", s.dir, "--query", "select id from 'n.csv' where date::date = '2026-01-02' and cast(timestamp as int) > 0")
            self.assertEqual(r.first_rule(), "value.not-integer")
            r = run("--root", s.dir, "--query", "select id from 'n.csv' where date::date = '2026-01-02'", "--format", "csv")
            self.assertEqual((r.status, r.stdout.decode().split()), (0, ["id", "1"]))
        finally:
            s.cleanup()


# ---- joins -----------------------------------------------------------------------------------------------------------------------------

class Joins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def tables(self, rng, kind, n=14, m=10):
        pool = ref.first_value_cells(kind, rng, 12) + [ref.random_valid(rng, kind) for _ in range(6)]
        if kind == "timestamp":
            # the same instant in another spelling
            pool += ["2026-01-02T01:00:00+02:00", "2026-01-01T23:00:00Z", "2026-01-01 23:00:00.000+00:00", "2026-01-01T18:00:00-05:00"]
        probe = [[str(i + 1), rng.choice(pool), rng.choice("xy")] for i in range(n)]
        build = [[rng.choice(pool), "n%d" % i] for i in range(m)]
        self.s.write("p.csv", csv_text([["id", "k", "tag"]] + probe))
        self.s.write("b.csv", csv_text([["kk", "name"]] + build))
        self.s.write("bu.csv", csv_text([["k", "name"]] + build))
        return probe, build

    def test_equal_instants_in_two_spellings_match(self):
        self.s.write("p1.csv", "id,k\n1,2026-01-02T01:00:00+02:00\n2,2026-01-02T00:00:00Z\n3,2026-01-01T18:00:00-05:00\n")
        self.s.write("b1.csv", "kk,name\n2026-01-01T23:00:00Z,a\n2026-01-01 23:00:00.000+00:00,b\n2026-01-02T00:00:00z,c\n")
        r = run("--root", self.s.dir, "--join", "b1.csv", "--on", "k=kk:timestamp", "--format", "csv", "p1.csv")
        self.assertEqual(rows_of(r), ["id,k,kk,name", "1,2026-01-02T01:00:00+02:00,2026-01-01T23:00:00Z,a", "1,2026-01-02T01:00:00+02:00,2026-01-01 23:00:00.000+00:00,b",
                                      "2,2026-01-02T00:00:00Z,2026-01-02T00:00:00z,c", "3,2026-01-01T18:00:00-05:00,2026-01-01T23:00:00Z,a",
                                      "3,2026-01-01T18:00:00-05:00,2026-01-01 23:00:00.000+00:00,b"])
        # as text they match nothing
        r = run("--root", self.s.dir, "--join", "b1.csv", "--on", "k=kk", "--format", "csv", "p1.csv")
        self.assertEqual(rows_of(r), ["id,k,kk,name"])

    def test_every_type_every_join_type_against_the_python_join(self):
        rng = random.Random(71)
        for kind in KINDS:
            for _ in range(4):
                probe, build = self.tables(rng, kind)
                for jt in ("inner", "left", "semi", "anti"):
                    want = ref.join_rows(kind, probe, build, jt)
                    header = ["id", "k", "tag"] + ([] if jt in ("semi", "anti") else ["kk", "name"])
                    r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=kk:" + kind, "--join-type", jt, "--max-join-rows", 100000, "--format", "csv", "p.csv")
                    self.assertEqual(r.status, 0, (kind, jt, r.stdout[:300], r.stderr[:300]))
                    self.assertEqual(r.stdout.decode(), csv_text([header] + want), (kind, jt))
                    # the shared-name form: the build key column is not repeated
                    want = ref.join_rows(kind, probe, build, jt, using=True)
                    header = ["id", "k", "tag"] + ([] if jt in ("semi", "anti") else ["name"])
                    r = run("--root", self.s.dir, "--join", "bu.csv", "--on", "k:" + kind, "--join-type", jt, "--max-join-rows", 100000, "--format", "csv", "p.csv")
                    self.assertEqual(r.stdout.decode(), csv_text([header] + want), (kind, jt, "using"))

    def test_threads_standard_input_and_the_explain(self):
        rng = random.Random(72)
        probe, build = self.tables(rng, "timestamp")
        base = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=kk:timestamp", "--format", "csv", "p.csv")
        self.assertEqual(base.status, 0)
        self.assertGreater(len(base.stdout.splitlines()), 3)
        for extra in (THREADS, ["--threads", "16", "--chunk-bytes", "7", "--parallel-min-bytes", "0"]):
            self.assertEqual(run("--root", self.s.dir, "--join", "b.csv", "--on", "k=kk:timestamp", "--format", "csv", *extra, "p.csv").stdout, base.stdout)
        via = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=kk:timestamp", "--format", "csv", stdin=(self.s.dir / "p.csv").read_bytes())
        self.assertEqual(via.stdout, base.stdout)
        r = run("--root", self.s.dir, "--explain", "--join", "b.csv", "--on", "k=kk:timestamp", "p.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        self.assertEqual(r.data()["explain"]["join"]["keys"][0]["type"], "timestamp")
        for ty in ("date", "datetime"):
            r = run("--root", self.s.dir, "--explain", "--join", "b.csv", "--on", "k=kk:" + ty, "p.csv")
            self.assertEqual(r.data()["explain"]["join"]["keys"][0]["type"], ty)

    def test_a_json_lines_probe_and_build(self):
        self.s.write("pj.jsonl", '{"id":1,"k":"2026-01-02T01:00:00+02:00"}\n{"id":2,"k":"2026-01-05T00:00:00Z"}\n')
        self.s.write("bj.jsonl", '{"kk":"2026-01-01T23:00:00Z","name":"a"}\n{"kk":"2026-01-05T00:00:00+00:00","name":"b"}\n')
        r = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", "id,k", "--join", "bj.jsonl", "--join-input-format", "jsonl", "--join-fields", "kk,name", "--on", "k=kk:timestamp", "--format", "csv", "pj.jsonl")
        self.assertEqual(rows_of(r), ["id,k,kk,name", "1,2026-01-02T01:00:00+02:00,2026-01-01T23:00:00Z,a", "2,2026-01-05T00:00:00Z,2026-01-05T00:00:00+00:00,b"])

    def test_several_pairs_of_mixed_types_and_a_text_pair(self):
        self.s.write("p2.csv", "id,a,b,c\n1,2026-01-02,2026-01-02T01:00:00+02:00,x\n2,2026-01-03,2026-01-01T23:00:00Z,x\n3,2026-01-02,2026-01-01T23:00:00Z,y\n")
        self.s.write("b2.csv", "a2,b2,c2,n\n2026-01-02,2026-01-01T23:00:00Z,x,m1\n2026-01-03,2026-01-01T23:00:00Z,x,m2\n")
        r = run("--root", self.s.dir, "--join", "b2.csv", "--on", "a=a2:date,b=b2:timestamp,c=c2", "--format", "csv", "p2.csv")
        self.assertEqual(rows_of(r), ["id,a,b,c,a2,b2,c2,n", "1,2026-01-02,2026-01-02T01:00:00+02:00,x,2026-01-02,2026-01-01T23:00:00Z,x,m1", "2,2026-01-03,2026-01-01T23:00:00Z,x,2026-01-03,2026-01-01T23:00:00Z,x,m2"])

    def test_join_where_and_where_on_the_types_beside_a_typed_key(self):
        self.s.write("p3.csv", "id,k,ts\n1,2026-01-02T01:00:00+02:00,2026-01-01T10:00:00Z\n2,2026-01-05T00:00:00Z,2026-02-01T10:00:00Z\n")
        self.s.write("b3.csv", "kk,name,born\n2026-01-01T23:00:00Z,a,2000-01-01\n2026-01-05T00:00:00Z,b,2010-05-05\n")
        r = run("--root", self.s.dir, "--join", "b3.csv", "--on", "k=kk:timestamp", "--join-where", "born:date > '2005-01-01'", "--where", "ts:timestamp < '2026-03-01T00:00:00Z'", "--format", "csv", "p3.csv")
        self.assertEqual(rows_of(r), ["id,k,ts,kk,name,born", "2,2026-01-05T00:00:00Z,2026-02-01T10:00:00Z,2026-01-05T00:00:00Z,b,2010-05-05"])
        r = run("--root", self.s.dir, "--join", "b3.csv", "--on", "k=kk:timestamp", "--group", "kk:timestamp@month", "--agg", "count,max:born:date", "--format", "csv", "p3.csv")
        self.assertEqual(rows_of(r), ["kk,count,max:born", "2026-01-01T00:00:00Z,2,2010-05-05"])

    def test_the_refusals_of_a_key_cell_are_the_rules_of_the_types(self):
        rng = random.Random(73)
        for kind in KINDS:
            refused = [c for c in ref.edge_cells() if ref.parse(kind, c.encode())[0] == "refuse" and not any(x in c for x in ',"\n\r\x00')]
            rng.shuffle(refused)
            for cell in refused[:25]:
                want = ref.parse(kind, cell.encode())
                for side in ("probe", "build"):
                    good = "2026-01-01" if kind == "date" else "2026-01-01T00:00:00Z" if kind == "timestamp" else "2026-01-01 00:00:00"
                    self.s.write("pr.csv", "id,k\n1,%s\n2,%s\n" % (good, cell if side == "probe" else good))
                    self.s.write("br.csv", "kk,name\n%s,a\n%s,b\n" % (good, cell if side == "build" else good))
                    r = run("--root", self.s.dir, "--join", "br.csv", "--on", "k=kk:" + kind, "pr.csv")
                    e = err(r)
                    self.assertEqual((r.status, e["rule"]), (8, want[1]), (kind, side, cell, r.stdout[:300]))
                    d = e["detail"]
                    self.assertEqual((d["context"], d["input"], d["pair"], d["column"], d["row"], d["line"], d["value"]),
                                     ("join", side, 1, "k" if side == "probe" else "kk", 2, 3, cell[:64]), (kind, side, cell))
                    self.assertEqual(d["path"], "pr.csv" if side == "probe" else "br.csv")
                    for key, value in want[2].items():
                        self.assertEqual(d.get(key), value, (kind, side, cell, key))
                    for key in ("reason", "offset", "digits", "direction"):
                        self.assertEqual(key in d, key in want[2], (kind, side, cell, key))
                    self.assertEqual(validate(r), [])

    def test_a_quoted_key_cell_is_read_without_its_quotes_and_refused_by_its_own_reason(self):
        self.s.write("pq.csv", 'id,k\n1,"2026-01-02T01:00:00"\n')
        self.s.write("bq.csv", 'kk,name\n"2026-01-01T23:00:00Z",a\n')
        r = run("--root", self.s.dir, "--join", "bq.csv", "--on", "k=kk:timestamp", "pq.csv")
        e = err(r)
        self.assertEqual((r.status, e["rule"], e["detail"]["input"], e["detail"]["reason"]), (8, "value.timezone-missing", "probe", "missing"))
        self.s.write("pq.csv", 'id,k\n1,"2026-01-02T01:00:00+02:00"\n')
        self.s.write("bq.csv", 'kk,name\n"2026-01-01T23:00:00",a\n')
        r = run("--root", self.s.dir, "--join", "bq.csv", "--on", "k=kk:timestamp", "pq.csv")
        e = err(r)
        self.assertEqual((r.status, e["rule"], e["detail"]["input"], e["detail"]["reason"]), (8, "value.timezone-missing", "build", "missing"))
        self.s.write("bq.csv", 'kk,name\n"2026-01-01T23:00:00Z",a\n')
        r = run("--root", self.s.dir, "--join", "bq.csv", "--on", "k=kk:timestamp", "--format", "csv", "pq.csv")
        self.assertEqual(rows_of(r), ["id,k,kk,name", "1,2026-01-02T01:00:00+02:00,2026-01-01T23:00:00Z,a"])

    def test_a_date_cell_in_a_timestamp_pair_is_refused_there_is_no_cross_type_join(self):
        self.s.write("pd.csv", "id,k\n1,2026-01-02\n")
        self.s.write("bd.csv", "kk,name\n2026-01-02T00:00:00Z,a\n")
        for ty, side in (("timestamp", "probe"), ("date", "build")):
            r = run("--root", self.s.dir, "--join", "bd.csv", "--on", "k=kk:" + ty, "pd.csv")
            self.assertEqual(r.status, 8, (ty, r.stdout[:300], r.stderr[:200]))
            e = err(r)
            self.assertEqual(e["detail"]["input"], side, ty)
            self.assertIn(e["rule"], ("value.not-timestamp", "value.not-date"))

    def test_the_place_of_the_type_and_a_word_that_is_not_a_type(self):
        self.s.write("pd.csv", "id,k\n1,2026-01-02\n")
        self.s.write("bd.csv", "kk,name\n2026-01-02,a\n")
        for on in ("k:date=kk", "k=kk:date:date", "k=kk:Date", "k=kk:time", "k=kk:timestamp:datetime", "k:timestamp=kk:date", "k=kk:"):
            r = run("--root", self.s.dir, "--join", "bd.csv", "--on", on, "pd.csv")
            self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"), on)
        r = run("--root", self.s.dir, "--join", "bd.csv", "--on", "k=kk:date", "--format", "csv", "pd.csv")
        self.assertEqual(rows_of(r), ["id,k,kk,name", "1,2026-01-02,2026-01-02,a"])
        # a column called kk:date is written with its colon escaped, as one called kk:int is
        self.s.write("be.csv", "kk:date,name\n2026-01-02,a\n")
        r = run("--root", self.s.dir, "--join", "be.csv", "--on", "k=kk\\:date", "--format", "csv", "pd.csv")
        self.assertEqual(rows_of(r), ["id,k,kk:date,name", "1,2026-01-02,2026-01-02,a"])

    def test_duckdb_agrees_on_the_matches_of_valid_cells(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("no duckdb module")
        con = duckdb.connect()
        con.execute("SET TimeZone='UTC'")
        rng = random.Random(74)
        for kind in KINDS:
            cast = {"timestamp": "TIMESTAMPTZ", "date": "DATE", "datetime": "TIMESTAMP"}[kind]
            probe, build = self.tables(rng, kind, n=30, m=20)
            norm = (lambda c: c[:10] + "T" + c[11:]) if kind != "date" else (lambda c: c)
            con.execute("create or replace table p(id int, k varchar)")
            con.execute("create or replace table b(kk varchar, name varchar)")
            con.executemany("insert into p values (?, ?)", [(int(r[0]), norm(r[1])) for r in probe])
            con.executemany("insert into b values (?, ?)", [(norm(r[0]), r[1]) for r in build])
            got = con.execute("select p.id, b.name from p join b on cast(p.k as %s) = cast(b.kk as %s) order by p.id, b.name" % (cast, cast)).fetchall()
            r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=kk:" + kind, "--max-join-rows", 100000, "--format", "csv", "p.csv")
            mine = sorted((int(l.split(",")[0]), l.split(",")[-1]) for l in rows_of(r)[1:])
            self.assertEqual(mine, sorted((int(a), b) for a, b in got), kind)


# ---- --report dates --------------------------------------------------------------------------------------------------------------------

class Report(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        rng = random.Random(81)
        n = 120
        cls.cols = {
            "all_ts": [ref.random_valid(rng, "timestamp") for _ in range(n)],
            "all_d": [ref.random_valid(rng, "date") for _ in range(n)],
            "all_w": [ref.random_valid(rng, "datetime") for _ in range(n)],
            "mixed": [rng.choice([ref.random_valid(rng, k) for k in KINDS] + ["", "x", "2026-02-30", "2026-01-02T24:00:00Z", "12"]) for _ in range(n)],
            "gaps_d": [rng.choice([ref.random_valid(rng, "date"), "", ""]) for _ in range(n)],
            "text": [rng.choice(["a", "b", ""]) for _ in range(n)],
            "none": [""] * n,
            "late_other": [ref.random_valid(rng, "timestamp") for _ in range(n - 3)] + ["2026-01-02T01:00:00", "", "z" * 80],
        }
        cls.names = list(cls.cols)
        rows = [[cls.cols[c][i] for c in cls.names] for i in range(n)]
        cls.s.write("r.csv", csv_text([cls.names] + rows))
        cls.data = (cls.s.dir / "r.csv").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def want(self, names=None, rows=None):
        names = names or self.names
        return [ref.report_row(c, [x.encode() for x in (self.cols[c] if rows is None else [self.cols[c][i] for i in rows])]) for c in names]

    def test_the_counters_of_every_column(self):
        r = run("--root", self.s.dir, "--report", "dates", "r.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        d = r.data()
        self.assertEqual(d["columns"], ref.REPORT_COLUMNS)
        self.assertEqual(d["rows"], self.want())
        self.assertEqual(validate(r), [])
        r = run("--root", self.s.dir, "--report", "dates", "--format", "csv", "r.csv")
        self.assertEqual(r.stdout.decode(), csv_text([ref.REPORT_COLUMNS] + self.want()))

    def test_the_suggestion_is_the_declaration_every_cell_satisfies(self):
        r = run("--root", self.s.dir, "--report", "dates", "r.csv")
        self.assertEqual(r.status, 0, r.stderr[:200])
        got = {row[0]: row[8] for row in r.data()["rows"]}
        self.assertEqual(got, {"all_ts": ":timestamp", "all_d": ":date", "all_w": ":datetime", "mixed": "none", "gaps_d": ":date", "text": "none", "none": "none", "late_other": "none"})

    def test_select_where_limit_and_a_column_twice(self):
        r = run("--root", self.s.dir, "--report", "dates", "--select", "mixed,all_d", "--format", "csv", "r.csv")
        self.assertEqual(r.stdout.decode(), csv_text([ref.REPORT_COLUMNS] + self.want(["mixed", "all_d"])))
        keep = [i for i in range(len(self.cols["text"])) if self.cols["text"][i] == "a"]
        r = run("--root", self.s.dir, "--report", "dates", "--where", "text = 'a'", "--select", "mixed,gaps_d", "--format", "csv", "r.csv")
        # the row and the line of a first cell are those of the file, not of the kept rows
        want = [ref.report_row(c, [self.cols[c][i].encode() for i in keep], [i + 1 for i in keep]) for c in ("mixed", "gaps_d")]
        self.assertEqual(r.stdout.decode(), csv_text([ref.REPORT_COLUMNS] + want))
        r = run("--root", self.s.dir, "--report", "dates", "--limit", "2", "--from", "1", "--format", "csv", "r.csv")
        self.assertEqual(r.stdout.decode(), csv_text([ref.REPORT_COLUMNS] + self.want()[1:3]))

    def test_threads_chunks_and_standard_input(self):
        base = run("--root", self.s.dir, "--report", "dates", "r.csv")
        for threads in (2, 3, 4, 8, 16, 64):
            for chunk in (1, 7, 64, 1000):
                got = run("--root", self.s.dir, "--report", "dates", "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, "r.csv")
                self.assertEqual((got.status, got.stdout), (base.status, base.stdout), (threads, chunk))
        via = run("--report", "dates", stdin=self.data)
        self.assertEqual(via.stdout, base.stdout)

    def test_a_header_only_file_and_empty_quotes(self):
        r = run("--report", "dates", "--format", "csv", stdin=b"a,b\n")
        self.assertEqual(r.status, 0, r.stderr[:200])
        self.assertEqual(rows_of(r)[1:], ["a,0,0,0,0,0,0,0,none,,,,,,,,,,,,", "b,0,0,0,0,0,0,0,none,,,,,,,,,,,,"])
        r = run("--report", "dates", "--format", "csv", stdin=b'a\n""\n2026-01-02\n')
        self.assertEqual(rows_of(r)[1], ",".join(ref.report_row("a", [b"", b"2026-01-02"])))

    def test_a_quoted_cell_is_the_text_inside_the_quotes(self):
        r = run("--report", "dates", "--format", "csv", stdin=b'a\n"2026-01-02T01:00:00+02:00"\n"2026-01-01T23:00:00Z"\n')
        self.assertEqual(r.status, 0, r.stderr[:200])
        self.assertEqual(rows_of(r)[1], ",".join(ref.report_row("a", [b"2026-01-02T01:00:00+02:00", b"2026-01-01T23:00:00Z"])))

    def test_the_flags_it_does_not_go_with(self):
        for flags, rule in ((["--group", "mixed"], "args.conflict"), (["--agg", "count"], "args.conflict"), (["--order-by", "mixed"], "args.conflict"), (["--top", "1"], "args.conflict"),
                            (["--format", "jsonl"], "args.conflict"), (["--rename", "a=b"], "args.conflict"), (["--rows"], "args.conflict"), (["--join", "r.csv", "--on", "mixed"], "args.conflict")):
            r = run("--root", self.s.dir, "--report", "dates", *flags, "r.csv")
            if "jsonl" in flags:
                # the other formats' refusals are one line of text on the standard error
                self.assertEqual(r.status, 2)
                self.assertIn(b"args.conflict", r.stderr)
                self.assertIn(b"dates", r.stderr)
                continue
            self.assertEqual((r.status, r.first_rule()), (2, rule), flags)
            if flags != ["--rows"]:
                self.assertIn("dates", err(r)["message"], flags)
        for word in ("date", "Dates", "all"):
            r = run("--root", self.s.dir, "--report", word, "r.csv")
            self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"), word)
            self.assertIn("dates", err(r)["message"])
            self.assertIn("types", err(r)["message"])
        r = run("--root", self.s.dir, "--report", "", "r.csv")
        self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"))

    def test_the_report_of_types_is_the_bytes_it_was(self):
        r = run("--root", self.s.dir, "--report", "types", "r.csv")
        self.assertEqual(r.data()["columns"][:8], ["name", "cells", "empty", "int", "dec", "float", "not_finite", "other"])
        self.assertEqual(len(r.data()["columns"]), 24)
        if MAIN_TABLE and os.path.exists(MAIN_TABLE):
            from harness import binary
            for extra in ([], ["--format", "csv"], ["--select", "mixed"], ["--threads", "4", "--chunk-bytes", "16", "--parallel-min-bytes", "0"]):
                argv = ["--root", str(self.s.dir), "--report", "types", *extra, "r.csv"]
                mine = subprocess.run([binary(), *argv], capture_output=True, input=b"")
                theirs = subprocess.run([MAIN_TABLE, *argv], capture_output=True, input=b"")
                self.assertEqual((mine.returncode, mine.stdout, mine.stderr), (theirs.returncode, theirs.stdout, theirs.stderr), extra)

    def test_explain_names_the_report(self):
        r = run("--root", self.s.dir, "--explain", "--report", "dates", "r.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        plan = r.data()["explain"]
        self.assertEqual(plan["plan"]["form"] if "form" in plan.get("plan", {}) else plan.get("form"), "report")
        self.assertIn("date report", json.dumps(plan))

    def test_the_bounds_are_the_reports(self):
        r = run("--root", self.s.dir, "--report", "dates", "--max-state-bytes", "1", "r.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.state-too-large"))
        self.assertIn("224 bytes", err(r)["message"])
        r = run("--root", self.s.dir, "--report", "types", "--max-state-bytes", "1", "r.csv")
        self.assertIn("248 bytes", err(r)["message"])
        # two columns and no row: 448 bytes of counters for dates, 496 for types
        r = run("--report", "dates", "--max-state-bytes", "460", stdin=b"a,b\n")
        self.assertEqual(r.status, 0, r.stdout[:200])
        r = run("--report", "types", "--max-state-bytes", "460", stdin=b"a,b\n")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.state-too-large"))
        r = run("--root", self.s.dir, "--report", "dates", "--max-rows", "1", "r.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-rows"))


# ---- JSON lines out --------------------------------------------------------------------------------------------------------------------

class JsonlOut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("f.csv", "id,k,ts,d,w\n1,a,2026-01-02T01:00:00+02:00,2026-01-02,2026-01-02 01:00:00\n2,b,2026-01-01T23:30:00Z,2026-02-10,2026-01-31 23:59:59\n3,a,2026-03-01T00:00:00Z,2025-12-31,2025-12-31T12:00:00.5\n")
        cls.s.write("b.csv", "kk,name\n2026-01-01T23:00:00Z,x\n2026-03-01T00:00:00+00:00,y\n")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def objs(self, *flags):
        r = run("--root", self.s.dir, *flags, "--format", "jsonl")
        self.assertEqual(r.status, 0, (flags, r.stdout[:300]))
        return [json.loads(l) for l in r.stdout.decode().splitlines()], r.stdout.decode()

    def test_a_query_with_dates_writes_strings_and_counts_as_numbers(self):
        got, text = self.objs("--query", "select k, min(ts::timestamp), max(d::date), count(distinct w::datetime), count(*) from 'f.csv' group by k")
        self.assertEqual(got, [{"k": "a", "min:ts": "2026-01-01T23:00:00Z", "max:d": "2026-01-02", "distinct:w": 2, "count": 2}, {"k": "b", "min:ts": "2026-01-01T23:30:00Z", "max:d": "2026-02-10", "distinct:w": 1, "count": 1}])
        self.assertIn('"distinct:w":2,"count":2', text)

    def test_a_join_with_a_typed_key_group_writes_strings(self):
        got, text = self.objs("--join", "b.csv", "--on", "ts=kk:timestamp", "--group", "kk:timestamp", "--agg", "count,min:d:date,max:w:datetime", "f.csv")
        self.assertEqual(got, [{"kk": "2026-01-01T23:00:00Z", "count": 1, "min:d": "2026-01-02", "max:w": "2026-01-02T01:00:00"}, {"kk": "2026-03-01T00:00:00Z", "count": 1, "min:d": "2025-12-31", "max:w": "2025-12-31T12:00:00.500"}])

    def test_a_join_of_rows_is_strings(self):
        got, text = self.objs("--join", "b.csv", "--on", "ts=kk:timestamp", "--select", "id,kk,name", "f.csv")
        self.assertEqual(got, [{"id": "1", "kk": "2026-01-01T23:00:00Z", "name": "x"}, {"id": "3", "kk": "2026-03-01T00:00:00+00:00", "name": "y"}])

    def test_the_report_is_not_written_as_json_lines(self):
        r = run("--root", self.s.dir, "--report", "dates", "--format", "jsonl", "f.csv")
        self.assertEqual(r.status, 2)
        self.assertIn(b"args.conflict", r.stderr)


# ---- the surface -----------------------------------------------------------------------------------------------------------------------

class Surface(unittest.TestCase):
    def test_the_help_says_the_three_things(self):
        flags = {f["name"]: f for f in introspect()["flags"]}
        report = flags["--report"]["help"]
        for word in ("dates", "types", "date", "timestamp", "datetime", "suggest", "first_not_date_row", "first_other_cell"):
            self.assertIn(word, report, word)
        self.assertNotIn("the only value is types", report)
        on = flags["--on"]["help"]
        for word in (":date", ":timestamp", ":datetime"):
            self.assertIn(word, on, word)
        query = flags["--query"]["help"]
        for word in ("::timestamp", "date", "datetime"):
            self.assertIn(word, query, word)

    def test_the_rules_are_the_same_list(self):
        rules = {r["rule"] for r in introspect()["rules"]}
        for tag in ("value.not-date", "value.not-timestamp", "args.bad-value", "args.conflict", "query.syntax", "query.unsupported"):
            self.assertIn(tag, rules)

    def test_the_docs_say_what_exists(self):
        for name, words in (("README.md", ("--report dates", "::timestamp")), ("docs/filter.md", ("--report dates", "=kk:timestamp")), ("docs/query.md", ("::timestamp", "cast(ts as timestamp)")),
                            ("docs/reference.md", ("--report dates",)), ("docs/joins.md", (":timestamp",)), ("docs/refusals.md", ("--report dates",))):
            text = (ROOT / name).read_text()
            for w in words:
                self.assertIn(w, text, (name, w))
        doc = (ROOT / "docs/datetime.md").read_text()
        self.assertIn("## 20. T3 as built", doc)
        self.assertNotIn("slices T0, T1 and T2 are built, T3 is not", doc)
        self.assertNotIn("@@", doc.split("## 20. T3 as built")[1])

    def test_the_mcp_definition_has_the_report(self):
        text = (ROOT / "generated/mcp/tools.cho").read_text()
        self.assertIn("dates", text)


# ---- G-FUZZ and the near strings -------------------------------------------------------------------------------------------------------

class Fuzz(unittest.TestCase):
    def test_random_on_lists_and_report_words_never_trap(self):
        rng = random.Random(91)
        s = Scratch()
        pieces = ["k", "kk", ":date", ":timestamp", ":datetime", ":int", ":dec(2)", "=", ",", "\\", "\\:", "#1", "#", "'", " ", "é", ":", "::", "date", "timestamp", "x"]
        try:
            s.write("p.csv", "id,k\n1,2026-01-02\n")
            s.write("b.csv", "kk,name\n2026-01-02,a\n")
            tags = {r["rule"] for r in introspect()["rules"]}
            for _ in range(400):
                on = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 7)))
                r = run("--root", s.dir, "--join", "b.csv", "--on", on, "p.csv")
                self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11), on)
                self.assertIn(r.status, (0, 2, 3, 8), (on, r.status))
                if r.status:
                    self.assertIn(r.first_rule(), tags, on)
            for _ in range(200):
                word = "".join(rng.choice(["types", "dates", "date", "t", "d", " ", "\\", "é", "S", "ypes"]) for _ in range(rng.randint(0, 4)))
                r = run("--root", s.dir, "--report", word, "p.csv")
                self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11), word)
                self.assertEqual(r.status, 0 if word in ("types", "dates") else 2, word)
            for _ in range(200):
                q = "select id from 'p.csv' where " + "".join(rng.choice(["k", "::", "cast(", " as ", "date", "timestamp", "datetime", ")", " ", "'2026-01-02'", "=", "x", ":", "int", "dec(2)"]) for _ in range(rng.randint(1, 9)))
                r = run("--root", s.dir, "--query", q)
                self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11), q)
        finally:
            s.cleanup()

    def test_report_of_odd_cells_never_traps(self):
        rng = random.Random(92)
        for _ in range(150):
            cells = [ref.fuzz_cell(rng) for _ in range(6)]
            cells = [c for c in cells if not any(x in c for x in b',"\n\r\x00')]
            r = run("--report", "dates", "--format", "csv", stdin=b"a\n" + b"\n".join(cells) + b"\n")
            self.assertNotIn(r.status, (132, 134, 136, 139, -4, -6, -8, -11))
            self.assertEqual(r.status, 0)
            want = ref.report_row("a", [c for c in cells])
            if cells and all(len(c) > 0 for c in cells):
                self.assertEqual(r.stdout.decode(errors="replace").splitlines()[1].split(",")[1:9], want[1:9])


if __name__ == "__main__":
    unittest.main()
