"""docs/functions.md section 3.1 (slice K5a): `--explain` of a `--compute` stage, through the built binary. Each gate can fail (scripts/compute_explain_mutants.py breaks the tool and every
mutant must be noticed).

* **G-EXPLAIN-CERTAIN** a definition that does not compile is refused by `--explain` with the bytes of the run's refusal (the rule, the message, the detail, the exit status);
* **G-EXPLAIN-PLAN** the names and scales in `stage.definitions` are the ones the run writes (the header after the input columns, and the fractional digits of the values), and
  `output.columns` is the header of the real answer;
* **G-EXPLAIN-POSSIBLE** every refusal the run gives on a hostile file is in `refusals.possible`, and a rule that the definitions cannot reach is not;
* **G-EXPLAIN-NOREAD** the explain reads the header chunk and nothing after it: a 3 MB file whose rows are garbage explains, and `bytes_read` is the chunk's;
* the plan of every other `--explain` is the earlier one (test_explain.py, run beside this file), and the stage has no SQL form (`sql: null`, the note `sql.compute`).
"""

import csv
import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import Scratch, run  # noqa: E402

FILE = "price,qty,s\n1.25,2,x\n3.50,4,y\n"

BAD = [
    "t=nope:int", "t=price", "t=price:dec(2)*", "t=price:dec(2)/qty:int", "price=qty:int", "t=price:float", "t=price:dec(30)", "t=qty:int;t=qty:int",
    ";", "t=", "=qty:int", "t=(qty:int", "t=qty:int)", "t=qty:int" + "+qty:int" * 140, ";".join("c%d=qty:int" % i for i in range(9)), "t=u", "t=qty:int*", "t=#9:int",
    "t=qty:int+1.5.5", "t='unterminated:int",
]

VALID = [
    ("t=price:dec(2)*qty:int", [("t", 2)]),
    ("h=price:dec(2)/qty:int@4", [("h", 4)]),
    ("a=qty:int+1;b=a*2", [("a", 0), ("b", 0)]),
    ("x=price:dec(2)*price:dec(2)", [("x", 4)]),
    ("'my col'=qty:int*3;n=-qty:int", [("my col", 0), ("n", 0)]),
    ("t=price:dec(2)*qty:int;h=t/qty:int@6", [("t", 2), ("h", 6)]),
]


class StageExplain(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.s.write("f.csv", FILE)

    def tearDown(self):
        self.s.cleanup()

    def explain(self, defs, name="f.csv", *extra):
        return run("--root", self.s.dir, "--compute", defs, "--explain", *extra, name)

    def run_stage(self, defs, name="f.csv", *extra):
        return run("--root", self.s.dir, "--compute", defs, *extra, name)

    # G-EXPLAIN-CERTAIN
    def test_a_definition_that_does_not_compile_is_refused_as_the_run_refuses_it(self):
        for defs in BAD:
            e = self.explain(defs)
            r = self.run_stage(defs)
            self.assertNotEqual(r.status, 0, defs[:40])
            self.assertEqual(e.status, r.status, defs[:40])
            self.assertEqual(e.doc().get("error"), r.doc().get("error"), defs[:40])
            self.assertEqual(e.doc().get("errors"), r.doc().get("errors"), defs[:40])
            self.assertEqual(e.stderr, r.stderr)

    # G-EXPLAIN-PLAN
    def test_the_definitions_are_the_ones_the_run_writes(self):
        for defs, want in VALID:
            e = self.explain(defs)
            self.assertEqual(e.status, 0, (defs, e))
            x = e.data()["explain"]
            got = [(d["name"], d["scale"]) for d in x["stage"]["definitions"]]
            self.assertEqual(got, want, defs)
            r = self.run_stage(defs, "f.csv", "--format", "csv")
            self.assertEqual(r.status, 0, (defs, r))
            rows = list(csv.reader(io.StringIO(r.stdout.decode("utf-8"))))
            header = rows[0]
            self.assertEqual(header[3:], [n for n, _ in want], defs)
            self.assertEqual(x["output"]["columns"], header, defs)
            for k, (name, scale) in enumerate(want):
                for row in rows[1:]:
                    cell = row[3 + k]
                    digits = len(cell.split(".")[1]) if "." in cell else 0
                    self.assertEqual(digits, scale, (defs, name, cell))
            for d in x["stage"]["definitions"]:
                self.assertGreaterEqual(d["instructions"], 1)
            self.assertEqual(x["stage"]["max_definitions"], 8)

    def test_the_stage_has_no_sql_form(self):
        x = self.explain("t=price:dec(2)*qty:int").data()["explain"]
        self.assertIsNone(x["sql"])
        self.assertTrue(any(n.startswith("sql.compute:") for n in x["notes"]), x["notes"])

    def test_the_header_columns_are_the_input_then_the_definitions_in_a_json_answer(self):
        x = self.explain("t=price:dec(2)*qty:int;u=qty:int+1", "f.csv", "--format", "json").data()["explain"]
        self.assertEqual(x["output"]["columns"], ["price", "qty", "s", "t", "u"])

    # G-EXPLAIN-POSSIBLE
    def possible(self, defs, content):
        self.s.write("h.csv", content)
        e = self.explain(defs, "h.csv")
        self.assertEqual(e.status, 0, e)
        return e.data()["explain"]["refusals"]["possible"], self.run_stage(defs, "h.csv")

    def test_what_the_run_refuses_is_in_the_list(self):
        cases = [
            ("t=qty:int+1", "price,qty,s\n1.25,x,y\n"),
            ("t=qty:int+1", "price,qty,s\n1.25,9223372036854775808,y\n"),
            ("t=price:dec(2)*2", "price,qty,s\nabc,1,y\n"),
            ("t=price:dec(2)*2", "price,qty,s\n1.234,1,y\n"),
            ("t=price:dec(2)*2", "price,qty,s\n12345678901234567890.00,1,y\n"),
            ("t=qty:int*qty:int*qty:int", "price,qty,s\n1.25,9223372036854775807,y\n"),
            ("t=price:dec(2)/qty:int@2", "price,qty,s\n1.25,0,y\n"),
            ("t=qty:int+1", "price,qty,s\n1.25,2,y,extra\n"),
        ]
        for defs, content in cases:
            possible, r = self.possible(defs, content)
            self.assertNotEqual(r.status, 0, (defs, content))
            self.assertIn(r.first_rule(), possible, (defs, content, r.first_rule()))

    def test_a_rule_the_definitions_cannot_reach_is_not_in_the_list(self):
        possible, _ = self.possible("t=qty:int+1", FILE)
        for rule in ("value.not-decimal", "value.decimal-scale", "value.decimal-too-wide", "compute.divide-by-zero"):
            self.assertNotIn(rule, possible)
        for rule in ("value.not-integer", "value.integer-overflow", "compute.overflow"):
            self.assertIn(rule, possible)
        possible, _ = self.possible("t=price:dec(2)*2", FILE)
        for rule in ("value.not-integer", "value.integer-overflow", "compute.divide-by-zero"):
            self.assertNotIn(rule, possible)
        for rule in ("value.not-decimal", "value.decimal-scale", "value.decimal-too-wide", "compute.overflow"):
            self.assertIn(rule, possible)
        possible, _ = self.possible("t=price:dec(2)/qty:int@2", FILE)
        self.assertIn("compute.divide-by-zero", possible)
        possible, _ = self.possible("t=7", FILE)
        for rule in ("value.not-integer", "value.not-decimal", "compute.overflow", "compute.divide-by-zero"):
            self.assertNotIn(rule, possible)

    # G-EXPLAIN-NOREAD
    def test_the_rows_are_not_read(self):
        body = "price,qty,s\n" + "garbage,garbage\n" * 200000
        self.s.write("big.csv", body)
        e = self.explain("t=price:dec(2)*qty:int", "big.csv")
        self.assertEqual(e.status, 0, e)
        src = e.data()["explain"]["source"]
        self.assertLess(src["bytes_read"], 70000)
        self.assertLess(src["bytes_read"], len(body) // 20)

    def test_the_stage_runs_on_one_core_whatever_threads_say(self):
        body = "price,qty,s\n" + "1.25,2,x\n" * 300000
        self.s.write("big.csv", body)
        e = self.explain("t=price:dec(2)*qty:int", "big.csv", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "65536")
        self.assertEqual(e.status, 0, e)
        t = e.data()["explain"]["work"]["threads"]
        self.assertEqual((t["requested"], t["effective"]), (4, 1))
        self.assertEqual(t["reason"], "the --compute stage runs on one core")
        # the plan with no stage on the same file is read by the threads it asked for (the earlier behaviour, unchanged)
        p = run("--root", self.s.dir, "--rows", "--explain", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "65536", "big.csv")
        self.assertEqual(p.data()["explain"]["work"]["threads"]["effective"], 4)

    def test_a_csv_format_still_answers_with_the_document(self):
        e = self.explain("t=price:dec(2)*qty:int", "f.csv", "--format", "csv")
        self.assertEqual(e.status, 0, e)
        self.assertEqual(e.data()["explain"]["stage"]["definitions"][0]["name"], "t")

    def test_standard_input_is_not_read(self):
        r = run("--compute", "t=price:dec(2)*qty:int", "--explain", stdin=b"price,qty,s\n1.25,2,x\n")
        self.assertEqual(r.status, 0, r)
        x = r.data()["explain"]
        self.assertNotIn("stage", x)  # the header of standard input is unknown, so the stage is not resolved
        self.assertTrue(any("standard input is not read" in n for n in x["notes"]))


class Combinations(unittest.TestCase):
    def test_the_other_refusals_of_the_stage_stand(self):
        s = Scratch()
        s.write("f.csv", FILE)
        try:
            for extra in (["--select", "price"], ["--where", "qty:int > 1"], ["--group", "s"], ["--rows"]):
                r = run("--root", s.dir, "--compute", "t=qty:int+1", "--explain", *extra, "f.csv")
                self.assertEqual(r.first_rule(), "compute.unsupported-combination", extra)
        finally:
            s.cleanup()


if __name__ == "__main__":
    unittest.main()
