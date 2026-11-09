"""`--explain` shows the plan as a `--query` statement (docs/query.md section 16, issue #104): `data.explain.sql`, and `sql_flags` for the flags of the call that a query leaves to the call (`--top`).

The gates, written before the code and each able to fail:

* RT, the round trip: for the random plans of the equivalence gate of `--query` (`query_gen`: awkward header names, typed conditions, groups, orders, pages), the statement run with `--query` and `sql_flags` gives
  the same stdout, stderr and exit status as the flags, refusals included, as json and as csv; and no plan has a statement that it cannot back (a mismatch is a statement that says something else).
* N, the cases without a statement each say why with a note whose id is `sql.NAME`, and `sql` is null: the shape, the type report, a join, a rename, a typed group, a comparison of two columns, JSON lines.
* S, what the statement is: pinned examples, as written, for each clause.
* Q, a plan that came from a query shows the query it was given.
"""

import json
import os
import random
import tempfile
import unittest

import query_gen
from harness import Scratch, introspect, run
from test_query import comparable

CASES = int(os.environ.get("TABLE_SQL_CASES", "1000"))
SALES = "host,status,bytes,price\na,200,512,1.50\nb,404,0,2.25\na,200,1024,1.5\nc,500,2048,10.00\nb,200,64,0.75\n"


def explained(*args, cwd=None):
    r = run(*args, "--explain", cwd=cwd)
    return r, (json.loads(r.stdout)["data"]["explain"] if r.status == 0 else None)


class RoundTrip(unittest.TestCase):
    def test_the_statement_of_a_random_plan_gives_the_bytes_of_its_flags(self):
        rnd = random.Random(int(os.environ.get("TABLE_SQL_SEED", "21")))
        seen = {"sql": 0, "none": 0, "why": {}}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.csv")
            for case in range(CASES):
                names, kinds, rows = query_gen.make_table(rnd)
                query_gen.write_csv(path, names, rows)
                plan = query_gen.make_plan(rnd, names, kinds, rows, many=True)
                flags = query_gen.flag_form(plan)
                if rnd.random() < 0.2:
                    flags += ["--top", str(rnd.randrange(1, 6))] if plan["mode"] == "group" and plan["order"] else []
                r, e = explained(*flags, "f.csv", cwd=d)
                if e is None:
                    continue      # the plan is refused before it is explained: the run refuses it too, and test_query holds that
                if e["sql"] is None:
                    seen["none"] += 1
                    why = [n.split(":")[0] for n in e["notes"] if n.startswith("sql.")]
                    self.assertEqual(len(why), 1, (case, flags, e["notes"]))
                    seen["why"][why[0]] = seen["why"].get(why[0], 0) + 1
                    self.assertEqual(e["sql_flags"], [])
                    continue
                seen["sql"] += 1
                self.assertFalse([n for n in e["notes"] if n.startswith("sql.")], e["notes"])
                for fmt in ("json", "csv"):
                    a = run(*flags, "--format", fmt, "f.csv", cwd=d)
                    b = run("--query", e["sql"], *e["sql_flags"], "--format", fmt, cwd=d)
                    self.assertEqual(comparable(a), comparable(b), "case %d\n  flags: %r\n  sql: %r %r\n  table: %r" % (case, flags, e["sql"], e["sql_flags"], names))
        # a gate that never had a statement has gated nothing
        self.assertGreater(seen["sql"], CASES // 2, seen)
        if os.environ.get("TABLE_SQL_REPORT"):
            print("\nSQL-RT: %d plans, %d with a statement, none: %s" % (CASES, seen["sql"], json.dumps(seen["why"], sort_keys=True)))


class Pinned(unittest.TestCase):
    """S: what the statement is, as written."""

    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)
        self.s.write("sales.csv", SALES)
        self.cwd = str(self.s.dir)

    def sql(self, *args, expect_flags=None):
        r, e = explained(*args, "sales.csv", cwd=self.cwd)
        self.assertEqual(r.status, 0, r.stderr[:300])
        if expect_flags is not None:
            self.assertEqual(e["sql_flags"], expect_flags)
        return e["sql"]

    def test_each_clause(self):
        self.assertEqual(self.sql("--select", "host,bytes", "--where", "status = 200 and (host in (a, b) or bytes:int > 100)", "--order-by", "-bytes:int", "--limit", "5"),
                         "select host, bytes from 'sales.csv' where status = '200' and (host in ('a', 'b') or bytes::int > 100) order by bytes::int desc limit 5")
        self.assertEqual(self.sql("--group", "host", "--agg", "count,sum:bytes", "--sort", "-sum:bytes", "--from", "1"),
                         "select host, count(*), sum(bytes) from 'sales.csv' group by host order by sum(bytes) desc offset 1")
        self.assertEqual(self.sql("--where", "host not contains x and not (status = 404)"), "select * from 'sales.csv' where host not contains 'x' and not (status = '404')")
        self.assertEqual(self.sql("--agg", "sum:price:dec(2),mean:bytes@1,mean:price:dec(2),distinct:host"),
                         "select sum(price::dec(2)), mean(bytes, 1), mean(price::dec(2)), count(distinct host) from 'sales.csv'")
        self.assertEqual(self.sql("--group", "status"), "select status, count(*) from 'sales.csv' group by status")
        self.assertEqual(self.sql("--rows"), "select * from 'sales.csv'")
        self.assertEqual(self.sql("--select", "#3,host"), "select #3, host from 'sales.csv'")

    def test_top_is_left_to_the_call_with_the_page_beside_it(self):
        self.assertEqual(self.sql("--group", "host", "--sort", "-count", "--top", "2", "--limit", "1", "--from", "1", expect_flags=["--top", "2", "--limit", "1", "--from", "1"]),
                         "select host, count(*) from 'sales.csv' group by host order by count(*) desc")

    def test_standard_input_and_names_that_need_quotes(self):
        self.s.write("w.csv", 'a b,select,it\'s,x"y\n1,2,3,4\n')
        r, e = explained("--select", "a b,select,it's,x\"y", "--where", "'a b' = 1", "w.csv", cwd=self.cwd)
        self.assertEqual(e["sql"], "select \"a b\", \"select\", \"it's\", \"x\"\"y\" from 'w.csv' where \"a b\" = '1'")
        r = run("--explain", "--select", "host", cwd=self.cwd, stdin=SALES.encode())
        self.assertEqual(json.loads(r.stdout)["data"]["explain"]["sql"], "select host from stdin")

    def test_the_date_types_and_floats_are_written_and_give_the_same_bytes(self):
        self.s.write("t.csv", "d,ts,dt,f,n\n2026-01-02,2026-01-02T00:00:00Z,2026-01-02 12:00:00,1.5,a\n2026-02-03,2026-01-01T23:00:00Z,2026-03-04 01:02:03,2.25,b\n2025-12-31,2026-01-03T00:00:00+02:00,2026-01-01 00:00:00,0.5,a\n")
        plans = [
            ["--where", "d:date >= 2026-01-02 and ts:timestamp < 2026-01-03T00:00:00Z and dt:datetime != '2026-01-01 00:00:00' and f:float > 1.0"],
            ["--where", "d:date in (2026-01-02, 2025-12-31) or f:float <= 0.5"],
            ["--order-by", "-d:date,ts:timestamp,-f:float"],
            ["--group", "n", "--agg", "min:d:date,max:ts:timestamp,distinct:dt:datetime,sum:f:float"],
        ]
        for flags in plans:
            r, e = explained(*flags, "t.csv", cwd=self.cwd)
            self.assertEqual(r.status, 0, (flags, r.stdout[:300]))
            self.assertIsNotNone(e["sql"], flags)
            for fmt in ("json", "csv"):
                a = run(*flags, "--format", fmt, "t.csv", cwd=self.cwd)
                b = run("--query", e["sql"], *e["sql_flags"], "--format", fmt, cwd=self.cwd)
                self.assertEqual(comparable(a), comparable(b), (flags, e["sql"], fmt))
        r, e = explained("--where", "d:date >= 2026-01-02", "--select", "n", "t.csv", cwd=self.cwd)
        self.assertEqual(e["sql"], "select n from 't.csv' where d::date >= '2026-01-02'")

    def test_a_path_with_a_quote_is_quoted(self):
        self.s.write("it's.csv", SALES)
        r, e = explained("--select", "host", "it's.csv", cwd=self.cwd)
        self.assertEqual(e["sql"], "select host from 'it''s.csv'")
        b = run("--query", e["sql"], cwd=self.cwd)
        self.assertEqual(b.status, 0, b.stderr[:300])

    def test_a_query_shows_the_statement_of_its_plan_as_the_flags_do(self):
        # the explain of a query and the explain of its flags are the same bytes (test_explain, Spelling): the statement is the plan's, not the query's text
        query = "SELECT  host ,count(*)  FROM 'sales.csv'\tgroup by host   ORDER BY count(*) DESC limit 3 offset 1"
        r = run("--explain", "--query", query, cwd=self.cwd)
        e = json.loads(r.stdout)["data"]["explain"]
        self.assertEqual((e["sql"], e["sql_flags"]), ("select host, count(*) from 'sales.csv' group by host order by count(*) desc limit 3 offset 1", []))
        self.assertFalse([n for n in e["notes"] if n.startswith("sql.")])
        # a query with an alias is a rename
        r = run("--explain", "--query", "select host as h from 'sales.csv'", cwd=self.cwd)
        e = json.loads(r.stdout)["data"]["explain"]
        self.assertIsNone(e["sql"])
        self.assertTrue([n for n in e["notes"] if n.startswith("sql.rename")])


class Nothing(unittest.TestCase):
    """N: no statement, and the reason is a note with a stable id."""

    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)
        self.s.write("sales.csv", SALES)
        self.s.write("hosts.csv", "host,owner\na,x\n")
        self.s.write("rows.jsonl", '{"a": 1}\n')
        self.cwd = str(self.s.dir)

    def why(self, *args):
        r, e = explained(*args, cwd=self.cwd)
        self.assertEqual(r.status, 0, (args, r.stdout[:300], r.stderr[:300]))
        self.assertIsNone(e["sql"], args)
        self.assertEqual(e["sql_flags"], [])
        ids = [n.split(":")[0] for n in e["notes"] if n.startswith("sql.")]
        self.assertEqual(len(ids), 1, (args, e["notes"]))
        return ids[0]

    def test_every_reason(self):
        self.assertEqual(self.why("sales.csv"), "sql.shape")
        self.assertEqual(self.why("--report", "types", "sales.csv"), "sql.report")
        self.assertEqual(self.why("--join", "hosts.csv", "--on", "host", "sales.csv"), "sql.join")
        self.assertEqual(self.why("--select", "host", "--rename", "host=name", "sales.csv"), "sql.rename")
        self.assertEqual(self.why("--group", "price:dec(2)", "sales.csv"), "sql.typed-group")
        self.assertEqual(self.why("--where", "bytes:int > (status:int)", "sales.csv"), "sql.columns-compared")
        self.assertEqual(self.why("--input-format", "jsonl", "--fields", "a", "--select", "a", "rows.jsonl"), "sql.jsonl")

    def test_the_reasons_are_the_notes_ids_the_docs_list(self):
        import re
        import pathlib
        text = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "query.md").read_text()
        for name in ("shape", "report", "join", "jsonl", "rename", "typed-group", "columns-compared", "mixed", "sort-label", "value"):
            self.assertIn("`sql.%s`" % name, text, name)


if __name__ == "__main__":
    unittest.main()
