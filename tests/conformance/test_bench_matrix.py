"""The matrix harness (docs/matrix-benchmark.md, MX0 to MX5 and the readiness of MX7): `scripts/bench_matrix.py`, `scripts/bench_matrix_forms.py` and the manifest
`scripts/bench_matrix_tools.json`, with their gates, written BEFORE the code they judge. Against a tree without those files every test here fails (the import fails).

What is held, in the order of the document's section 11:
  * the answer gate (`forms.compare`): an answer is judged against the reference, an integer never passes by tolerance, a wrong answer is reported;
  * the reference is computed from the written file, not from a contender;
  * the blank-cell rule and the no-score rule (`validate_cells`): a cell without a value has a reason, no field is a score, a time exists only for a clock kind and only after the gate;
  * the clock switch: the clock kinds (peak resident set, time) refuse to run unless the machine is quiet, the conditions are complete and N >= 5 (`clock_gate`);
  * the tools: a missing tool is `not installed` with its install command and pin, never estimated; `--check-tools` lists them;
  * the arithmetic of the other kinds: distinct outputs (determinism), the slope of instruction counts, the classification at a limit, the profile of a fault.
No test needs a network, a contender other than `table`, or a clock; the ones that start `table` skip when TOOLBOX_BIN has no binary.
"""
import contextlib
import csv
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from fractions import Fraction

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import bench_matrix as bm  # noqa: E402
import bench_matrix_forms as forms  # noqa: E402
import bench_named as bn  # noqa: E402


def table_binary():
    p = pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build")) / "table"
    return str(p) if p.exists() else None


def render(ref, texts=None):
    """The csv a correct contender prints for a reference."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(ref["columns"])
    for row in (texts or ref["texts"]):
        w.writerow(row)
    return out.getvalue()


class Tiny(unittest.TestCase):
    """A 400-row file of NB1's layout, its jsonl and quoted siblings, and the references."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="matrix-test-")
        cls.inputs = forms.make_inputs(400, 10, cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Manifest(unittest.TestCase):
    def setUp(self):
        self.m = bm.load_manifest()

    def test_every_contender_has_what_a_reader_and_an_installer_need(self):
        names = [c["name"] for c in self.m["contenders"]]
        self.assertEqual(len(names), len(set(names)))
        for c in self.m["contenders"]:
            for key in ("name", "family", "role", "why", "sources", "pin", "pin_note", "detect", "install", "install_verified"):
                self.assertIn(key, c, "%s lacks %s" % (c["name"], key))
            self.assertTrue(c["why"].strip(), c["name"])
            self.assertTrue(all(s.startswith("https://") or s.startswith("repo:") for s in c["sources"]), c["name"])
            self.assertIn("linux", c["install"])
            self.assertIn("macos", c["install"])

    def test_the_pinned_ones_have_a_pin_and_the_unpinned_say_why(self):
        for c in self.m["contenders"]:
            if c["pin"] is None:
                self.assertTrue(c["pin_note"].strip(), "%s has no pin and no reason" % c["name"])

    def test_the_wired_ones_keep_the_pins_of_the_named_benchmarks(self):
        pins = {c["name"]: c["pin"] for c in self.m["contenders"]}
        for name, pin in bn.PINS.items():
            self.assertEqual(pins[name], pin, name)

    def test_the_list_holds_the_contenders_of_the_document(self):
        names = {c["name"] for c in self.m["contenders"]}
        for want in ("table", "duckdb", "clickhouse", "polars", "pandas", "sqlite", "qsv", "mlr", "csvtk", "datafusion", "awk", "jq"):
            self.assertIn(want, names)
        self.assertNotIn("xsv", names)   # archived 2025-04-24 (docs/matrix-benchmark.md X2): not a contender

    def test_an_install_command_is_never_called_verified_here(self):
        # nothing was installed by the session that wrote the manifest
        for c in self.m["contenders"]:
            if c["name"] not in ("table",):
                self.assertIs(c["install_verified"], False, c["name"])

    def test_every_family_has_a_form_writer(self):
        for c in self.m["contenders"]:
            self.assertIn(c["family"], forms.FAMILIES)


class AnswerGate(Tiny):
    def ref(self, task):
        return forms.reference(task, self.inputs)

    def test_the_reference_answer_passes_exactly(self):
        for t in forms.TASKS:
            r = self.ref(t)
            got = forms.compare(r, render(r))
            self.assertEqual((got["status"], got["level"]), ("ok", "exact"), (t, got))

    def test_an_integer_in_another_spelling_is_numeric_not_exact(self):
        r = self.ref("X02")
        texts = [[k, "%d.00" % int(v)] for k, v in r["texts"]]
        got = forms.compare(r, render(r, texts))
        self.assertEqual((got["status"], got["level"]), ("ok", "numeric"))

    def test_an_integer_is_never_taken_by_tolerance(self):
        r = self.ref("X02")
        texts = [list(row) for row in r["texts"]]
        texts[0][1] = str(int(texts[0][1]) + 1)
        got = forms.compare(r, render(r, texts))
        self.assertEqual(got["status"], "wrong")

    def test_a_decimal_sum_by_a_double_passes_only_by_tolerance_and_says_so(self):
        r = self.ref("X04")
        texts = [[k, repr(float(v))] for k, v in r["texts"]]
        got = forms.compare(r, render(r, texts))
        self.assertEqual(got["status"], "ok")
        self.assertIn(got["level"], ("numeric", "tolerance"))
        far = [[k, repr(float(v) * 1.001)] for k, v in r["texts"]]
        self.assertEqual(forms.compare(r, render(r, far))["status"], "wrong")

    def test_a_missing_row_an_extra_row_and_a_wrong_key_are_wrong(self):
        r = self.ref("X02")
        for texts in (r["texts"][1:], r["texts"] + [["zzz", "1"]], [["bad"] + row[1:] for row in r["texts"][:1]] + r["texts"][1:]):
            self.assertEqual(forms.compare(r, render(r, texts))["status"], "wrong")

    def test_rows_are_a_set_unless_the_question_is_ordered(self):
        r = self.ref("X02")
        self.assertEqual(forms.compare(r, render(r, list(reversed(r["texts"]))))["status"], "ok")
        o = self.ref("X05")
        self.assertTrue(o["ordered"])
        self.assertEqual(forms.compare(o, render(o, list(reversed(o["texts"]))))["status"], "wrong")

    def test_a_wrong_width_a_nan_and_an_empty_output_are_wrong(self):
        r = self.ref("X02")
        self.assertEqual(forms.compare(r, "")["status"], "wrong")
        self.assertEqual(forms.compare(r, "a,b,c\n1,2,3\n")["status"], "wrong")
        texts = [list(row) for row in r["texts"]]
        texts[0][1] = "nan"
        self.assertEqual(forms.compare(r, render(r, texts))["status"], "wrong")

    def test_a_missing_header_is_wrong(self):
        r = self.ref("X02")
        body = "".join("%s,%s\n" % tuple(row) for row in r["texts"])
        self.assertEqual(forms.compare(r, body)["status"], "wrong")


class Reference(Tiny):
    def rows(self):
        with open(self.inputs["csv"], newline="") as f:
            r = csv.DictReader(f)
            return list(r)

    def test_group_sum_and_count_distinct_are_computed_from_the_file(self):
        rows = self.rows()
        want = {}
        for x in rows:
            want[x["id1"]] = want.get(x["id1"], 0) + int(x["v1"])
        got = {k: int(v) for k, v in forms.reference("X02", self.inputs)["texts"]}
        self.assertEqual(got, want)
        self.assertEqual(forms.reference("X06", self.inputs)["texts"], [[str(len({x["id3"] for x in rows}))]])
        self.assertEqual(forms.reference("X01", self.inputs)["texts"], [[str(sum(1 for x in rows if int(x["v1"]) >= 4))]])

    def test_the_decimal_sum_is_exact_in_millionths(self):
        rows = self.rows()
        tot = {}
        for x in rows:
            tot[x["id1"]] = tot.get(x["id1"], Fraction(0)) + Fraction(x["v3"])
        ref = forms.reference("X04", self.inputs)
        for k, text in ref["texts"]:
            self.assertEqual(Fraction(text), tot[k])
            self.assertEqual(len(text.split(".")[1]), 6)

    def test_the_sort_limit_and_the_computed_column_and_the_median(self):
        rows = self.rows()
        self.assertEqual([r[0] for r in forms.reference("X05", self.inputs)["texts"]], sorted(x["id3"] for x in rows)[:10])
        want = {}
        for x in rows:
            want[x["id1"]] = want.get(x["id1"], 0) + int(x["v1"]) * int(x["v2"])
        self.assertEqual({k: int(v) for k, v in forms.reference("X11", self.inputs)["texts"]}, want)
        vs = sorted(Fraction(x["v3"]) for x in rows)
        mid = (vs[len(vs) // 2 - 1] + vs[len(vs) // 2]) / 2 if len(vs) % 2 == 0 else vs[len(vs) // 2]
        self.assertEqual(forms.reference("X10", self.inputs)["rows"], [[mid]])

    def test_the_quoted_file_is_read_by_a_csv_parser_and_groups_by_its_odd_keys(self):
        with open(self.inputs["quoted"], newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(any("\n" in r["k"] for r in rows) and any(',' in r["k"] for r in rows) and any('"' in r["k"] for r in rows))
        want = {}
        for r in rows:
            want[r["k"]] = want.get(r["k"], 0) + int(r["v"])
        self.assertEqual({k: int(v) for k, v in forms.reference("X09", self.inputs)["texts"]}, want)

    def test_the_jsonl_file_has_the_rows_of_the_csv(self):
        lines = pathlib.Path(self.inputs["jsonl"]).read_text().splitlines()
        self.assertEqual(len(lines), len(self.rows()))
        first = json.loads(lines[0])
        self.assertEqual(sorted(first), sorted(self.rows()[0]))
        self.assertEqual(forms.reference("X07", self.inputs)["texts"], forms.reference("X02", self.inputs)["texts"])


class Inputs(unittest.TestCase):
    def test_the_same_seed_gives_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            x, y = forms.make_inputs(300, 10, a), forms.make_inputs(300, 10, b)
            self.assertEqual(x["sha256"], y["sha256"])
            z = forms.make_inputs(300, 10, a + "/s", seed=7)
            self.assertNotEqual(x["sha256"]["csv"], z["sha256"]["csv"])

    def test_a_directory_inside_the_repository_is_refused_with_a_tag(self):
        with self.assertRaises(forms.Refusal) as e:
            forms.make_inputs(300, 10, str(ROOT / "build" / "matrix-test-dir"))
        self.assertEqual(e.exception.rule, "matrix.inside-repo")

    def test_the_generator_is_the_one_of_the_named_benchmarks(self):
        with tempfile.TemporaryDirectory() as d:
            x = forms.make_inputs(300, 10, d)
            import bench_named_gen as gen
            path, meta = gen.write(300, 10, d + "/ref", 108)
            self.assertEqual(hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest(), x["sha256"]["csv"])


class Forms(Tiny):
    def ns(self, **kw):
        base = dict(file=self.inputs["csv"], jsonl=self.inputs["jsonl"], quoted=self.inputs["quoted"], threads=1, machine=False, missing=None, unknown_column=None)
        base.update(kw)
        return forms.Ns(**base)

    def test_a_form_is_an_argv_of_strings_or_nothing(self):
        for fam in forms.FAMILIES:
            for t in forms.TASKS:
                f = forms.build_form(fam, t, self.ns())
                if f is not None:
                    self.assertTrue(f.argv and all(isinstance(a, str) for a in f.argv), (fam, t))

    def test_table_has_a_form_for_every_task_and_threads_in_the_flags(self):
        for t in forms.TASKS:
            f = forms.build_form("table", t, self.ns(threads=4))
            self.assertIsNotNone(f, t)
            self.assertIn("--threads", f.argv)
            self.assertEqual(f.argv[f.argv.index("--threads") + 1], "4")

    def test_the_stdin_task_feeds_the_file_and_names_no_path(self):
        f = forms.build_form("table", "X08", self.ns())
        self.assertEqual(f.stdin_path, self.inputs["csv"])
        self.assertNotIn(self.inputs["csv"], f.argv)

    def test_the_jsonl_task_names_the_jsonl_file(self):
        f = forms.build_form("table", "X07", self.ns())
        self.assertIn(self.inputs["jsonl"], f.argv)
        self.assertIn("jsonl", f.argv)

    def test_a_missing_file_and_an_unknown_column_reach_the_argv(self):
        f = forms.build_form("table", "X02", self.ns(missing="/no/such/file.csv"))
        self.assertIn("/no/such/file.csv", f.argv)
        g = forms.build_form("duckdb", "X02", self.ns(unknown_column="nope_col"))
        self.assertIn("nope_col", " ".join(g.argv))

    def test_a_machine_readable_table_form_has_no_csv_format(self):
        f = forms.build_form("table", "X02", self.ns(machine=True))
        self.assertNotIn("csv", f.argv)

    def test_the_breadth_probes_are_marked_and_table_is_asked_them(self):
        probes = [t for t, v in forms.TASKS.items() if v["probe"]]
        self.assertEqual(sorted(probes), ["X10", "X11"])
        for t in probes:
            self.assertIsNotNone(forms.build_form("table", t, self.ns()))

    def test_jq_is_a_jsonl_baseline_only_and_the_floors_are_not_csv_readers(self):
        for t in forms.TASKS:
            f = forms.build_form("jq", t, self.ns())
            self.assertEqual(f is not None, t == "X07", t)
        self.assertIsNotNone(forms.build_form("awk", "X09", self.ns()))   # asked on the quoted file: the gate says what it gives

    def test_threads_only_where_the_tool_has_a_setting(self):
        self.assertEqual(forms.THREADS["table"], [1, 2, 4])
        self.assertEqual(forms.THREADS["awk"], [None])
        self.assertEqual(forms.THREADS["pandas"], [None])
        for fam in forms.FAMILIES:
            self.assertIn(fam, forms.THREADS)


@dataclasses.dataclass
class FakeProbes:
    load: tuple = (0.1, 0.1, 0.1)
    cores: int = 16
    virt: object = False
    docker_status: str = "ok"
    containers: tuple = ()
    soak_lines: tuple = ()

    def loadavg(self): return self.load
    def cpu_count(self): return self.cores
    def affinity(self): return self.cores
    def cpu_model(self): return "Fake CPU"
    def ram_bytes(self): return 64 << 30
    def virtualised(self): return self.virt, "fake evidence"
    def uptime(self): return "up 1 day"
    def docker(self): return {"status": self.docker_status, "containers": list(self.containers), "detail": ""}
    def soak(self): return {"found": bool(self.soak_lines), "lines": list(self.soak_lines)}
    def now(self): return "2026-10-22T00:00:00Z"
    def node(self): return "fake"
    def system(self): return "Linux"
    def machine(self): return "x86_64"
    def python(self): return "3.13"


def good_conditions(**kw):
    probes = FakeProbes(**kw)
    info = {"runs": 5, "rows": 1000000, "k": 100, "seed": 108, "step": "1e6", "contenders": {"table": {"version": "0.3.0", "threads": "1"}},
            "file": {"name": "f.csv", "bytes": 10, "sha256": "ab" * 32, "path_class": "internal SSD"}}
    return probes, info


class ClockGate(unittest.TestCase):
    def conditions(self, runs=5, **kw):
        probes, info = good_conditions(**kw)
        info["runs"] = runs
        return bn.collect_conditions(probes, info)

    def rule(self, c):
        with self.assertRaises(bm.Refusal) as e:
            bm.clock_gate(c)
        return e.exception.rule

    def test_a_quiet_dedicated_machine_with_five_runs_passes(self):
        bm.clock_gate(self.conditions())

    def test_a_loaded_machine_is_refused(self):
        self.assertEqual(self.rule(self.conditions(load=(5.0, 1.0, 1.0))), "matrix.clock-not-quiet")

    def test_exactly_a_quarter_of_the_cores_is_not_loaded(self):
        bm.clock_gate(self.conditions(load=(4.0, 1.0, 1.0)))

    def test_a_shared_virtual_machine_and_one_that_cannot_be_told_are_refused(self):
        self.assertEqual(self.rule(self.conditions(virt=True)), "matrix.clock-not-quiet")
        self.assertEqual(self.rule(self.conditions(virt=None)), "matrix.clock-not-quiet")

    def test_a_soak_and_a_container_and_an_unreadable_docker_are_refused(self):
        self.assertEqual(self.rule(self.conditions(soak_lines=("python soak.py",))), "matrix.clock-not-quiet")
        self.assertEqual(self.rule(self.conditions(containers=("c1",))), "matrix.clock-not-quiet")
        self.assertEqual(self.rule(self.conditions(docker_status="error")), "matrix.clock-not-quiet")

    def test_fewer_than_five_runs_is_refused_with_its_own_tag(self):
        self.assertEqual(self.rule(self.conditions(runs=4)), "matrix.clock-min-runs")

    def test_incomplete_conditions_are_refused_with_their_own_tag(self):
        c = self.conditions()
        del c["machine"]["cpu_model"]
        self.assertEqual(self.rule(c), "matrix.clock-conditions-missing")
        c = self.conditions()
        c["contenders"]["table"]["version"] = ""
        self.assertEqual(self.rule(c), "matrix.clock-conditions-missing")

    def test_the_reasons_are_all_listed(self):
        with self.assertRaises(bm.Refusal) as e:
            bm.clock_gate(self.conditions(load=(9.0, 9.0, 9.0), virt=True))
        self.assertIn("loaded", e.exception.message)
        self.assertIn("virtual", e.exception.message)


class Cells(unittest.TestCase):
    def cell(self, **kw):
        c = {"task": "X02", "contender": "table", "kind": "b", "value": {"status": "ok", "level": "exact"}, "reason": None}
        c.update(kw)
        return c

    def check(self, cells):
        with self.assertRaises(bm.Refusal) as e:
            bm.validate_cells(cells)
        return e.exception.rule

    def test_a_good_cell_and_a_blank_one_with_a_reason_pass(self):
        bm.validate_cells([self.cell(), self.cell(value=None, reason="not installed")])

    def test_a_cell_with_neither_a_value_nor_a_reason_is_refused(self):
        self.assertEqual(self.check([self.cell(value=None, reason=None)]), "matrix.blank-without-reason")
        self.assertEqual(self.check([self.cell(value=None, reason="  ")]), "matrix.blank-without-reason")

    def test_a_cell_with_a_value_and_a_reason_is_refused(self):
        self.assertEqual(self.check([self.cell(reason="because")]), "matrix.cell-both")

    def test_a_cell_without_a_known_kind_is_refused(self):
        self.assertEqual(self.check([self.cell(kind=None)]), "matrix.cell-without-kind")
        self.assertEqual(self.check([self.cell(kind="z")]), "matrix.cell-without-kind")

    def test_no_field_may_be_a_score_a_rank_or_a_winner(self):
        for name in ("score", "rank", "total", "winner", "overall", "best"):
            self.assertEqual(self.check([self.cell(**{name: 1})]), "matrix.score-field", name)
            self.assertEqual(self.check([self.cell(value={"status": "ok", name: 1})]), "matrix.score-field", name)

    def test_a_time_exists_only_in_a_clock_kind_after_the_gate(self):
        self.assertEqual(self.check([self.cell(kind="b", value={"status": "ok", "min_s": 0.1})]), "matrix.time-outside-clock")
        t = self.cell(kind="f", value={"min_s": 0.1, "median_s": 0.1, "first_s": 0.1, "runs": 5, "answer": "ok"})
        bm.validate_cells([t])
        self.assertEqual(self.check([self.cell(kind="f", value={"min_s": 0.1, "answer": "wrong"})]), "matrix.time-on-failed")
        self.assertEqual(self.check([self.cell(kind="f", value={"min_s": 0.1})]), "matrix.time-on-failed")

    def test_the_clock_cells_need_the_conditions_in_the_result(self):
        t = self.cell(kind="f", value={"min_s": 0.1, "median_s": 0.1, "first_s": 0.1, "runs": 5, "answer": "ok"})
        with self.assertRaises(bm.Refusal) as e:
            bm.validate_result({"schema": bm.SCHEMA, "cells": [t], "conditions": {}, "tools": []})
        self.assertEqual(e.exception.rule, "matrix.clock-conditions-missing")

    def test_the_minimum_must_be_the_minimum_and_fewer_than_five_runs_never_pass(self):
        bad = self.cell(kind="f", value={"min_s": 0.2, "median_s": 0.2, "first_s": 0.1, "runs": 5, "answer": "ok", "all_s": [0.1, 0.2, 0.2, 0.3, 0.3]})
        self.assertEqual(self.check([bad]), "matrix.time-invalid")
        few = self.cell(kind="f", value={"min_s": 0.1, "median_s": 0.1, "first_s": 0.1, "runs": 4, "answer": "ok"})
        self.assertEqual(self.check([few]), "matrix.clock-min-runs")


class Arithmetic(unittest.TestCase):
    def test_distinct_outputs(self):
        same = [b"a,b\n1,2\n"] * 5
        self.assertEqual(bm.distinct_outputs(same, ordered=False), {"raw": 1, "canonical": 1})
        self.assertEqual(bm.distinct_outputs(same + [b"a,b\n1,3\n"], ordered=False), {"raw": 2, "canonical": 2})

    def test_a_reordering_is_a_raw_difference_not_a_canonical_one_unless_ordered(self):
        a, b = b"k,v\nx,1\ny,2\n", b"k,v\ny,2\nx,1\n"
        self.assertEqual(bm.distinct_outputs([a, b], ordered=False), {"raw": 2, "canonical": 1})
        self.assertEqual(bm.distinct_outputs([a, b], ordered=True), {"raw": 2, "canonical": 2})

    def test_no_output_is_not_one_distinct_output(self):
        with self.assertRaises(bm.Refusal) as e:
            bm.distinct_outputs([], ordered=False)
        self.assertEqual(e.exception.rule, "matrix.no-outputs")

    def test_the_slope_is_the_instructions_per_row_and_the_intercept_the_start_up(self):
        self.assertEqual(bm.slope(1000, 5000, 3000, 9000), {"per_row": 2.0, "startup": 3000.0})
        self.assertEqual(bm.slope(1000, 5000, 3000, 5000)["per_row"], 0.0)

    def test_a_slope_over_no_rows_or_backwards_is_refused_or_invalid(self):
        with self.assertRaises(bm.Refusal) as e:
            bm.slope(1000, 5, 1000, 9)
        self.assertEqual(e.exception.rule, "matrix.slope-rows")
        self.assertIsNone(bm.slope(1000, 9000, 3000, 5000))

    def test_callgrind_counts_are_summed_over_the_processes_it_traced(self):
        text = "==1== Collected : 100\n==2== Collected : 250\nother\n"
        self.assertEqual(bm.collected(text), (350, 2))
        self.assertIsNone(bm.collected("nothing"))


class Limits(unittest.TestCase):
    def out(self, rc=0, so=b"", se=b"", to=False):
        return bn.Outcome(rc, 0.1, so, se, to)

    def test_classes(self):
        c = bm.classify_limit
        self.assertEqual(c(self.out(rc=-9), None, "table")["class"], "killed")
        self.assertEqual(c(self.out(rc=137), None, "table")["class"], "killed")
        self.assertEqual(c(self.out(to=True, rc=None), None, "table")["class"], "timeout")
        self.assertEqual(c(self.out(rc=1, se=b"out of memory"), None, "mlr")["class"], "refused-message")
        self.assertEqual(c(self.out(rc=8, se=b"table: limit.too-many-groups: more groups"), None, "table")["class"], "refused-tag")
        j = json.dumps({"ok": False, "error": {"rule": "limit.state-too-large", "repair": {"kind": "retry", "argv": ["table"]}}}).encode()
        r = c(self.out(rc=8, so=j), None, "table")
        self.assertEqual((r["class"], r["rule"], r["repair"]), ("refused-tag", "limit.state-too-large", "retry"))

    def test_an_exit_zero_is_an_answer_judged_by_the_gate(self):
        r = c = bm.classify_limit(self.out(rc=0, so=b"a\n1\n"), {"columns": ["a"], "texts": [["1"]], "rows": [[1]], "ckinds": ["int"], "ordered": False}, "mlr")
        self.assertEqual(r["class"], "answered-correct")
        w = bm.classify_limit(self.out(rc=0, so=b"a\n2\n"), {"columns": ["a"], "texts": [["1"]], "rows": [[1]], "ckinds": ["int"], "ordered": False}, "mlr")
        self.assertEqual(w["class"], "answered-wrong")

    def test_a_limit_that_puts_no_pressure_is_refused(self):
        with self.assertRaises(bm.Refusal) as e:
            bm.check_pressure(limit_bytes=2 << 30, unconstrained_peak_bytes=1 << 30)
        self.assertEqual(e.exception.rule, "matrix.limit-no-pressure")
        bm.check_pressure(limit_bytes=1 << 29, unconstrained_peak_bytes=1 << 30)


class Faults(unittest.TestCase):
    def test_profile_of_a_machine_readable_refusal(self):
        j = json.dumps({"ok": False, "error": {"code": "NOT_FOUND", "rule": "io.not-found", "repair": {"kind": "choose", "options": []}}}).encode()
        p = bm.fault_profile(bn.Outcome(3, 0.1, j, b"", False))
        self.assertEqual(p, {"exit_nonzero": True, "stdout_empty": False, "stderr_nonempty": False, "json_error": True, "rule_tag": "io.not-found", "repair": "choose"})

    def test_profile_of_a_message_on_stderr(self):
        p = bm.fault_profile(bn.Outcome(1, 0.1, b"", b"Error: no such file\n", False))
        self.assertEqual((p["exit_nonzero"], p["stdout_empty"], p["stderr_nonempty"], p["json_error"], p["rule_tag"], p["repair"]), (True, True, True, False, None, None))

    def test_profile_of_a_success_on_a_fault(self):
        p = bm.fault_profile(bn.Outcome(0, 0.1, b"x\n", b"", False))
        self.assertFalse(p["exit_nonzero"])

    def test_a_table_style_line_on_stderr_is_a_tag(self):
        p = bm.fault_profile(bn.Outcome(8, 0.1, b"", b"table: limit.too-many-groups: more groups\n", False))
        self.assertEqual(p["rule_tag"], "limit.too-many-groups")


def fake_status(name, family=None, found=True, version="1.0.0", pin="1.0.0", command=None):
    return {"name": name, "family": family or name, "found": found, "command": command or [name], "version": version if found else None, "pin": pin,
            "pinned": (version == pin) if found else None, "install": {"linux": "install-%s-linux" % name, "macos": "install-%s-mac" % name}, "install_verified": False,
            "pin_note": ""}


class Tools(unittest.TestCase):
    def test_detection_with_nothing_installed_gives_not_found_with_the_install_command(self):
        m = bm.load_manifest()
        st = bm.detect_tools(m, table=None, which=lambda n, path=None: None, run=lambda argv, timeout=30: (None, "", "no such program"))
        by = {s["name"]: s for s in st}
        self.assertFalse(by["qsv"]["found"])
        self.assertTrue(by["qsv"]["install"]["linux"])
        self.assertFalse(by["table"]["found"])

    def test_a_found_tool_with_another_version_is_not_pinned(self):
        m = bm.load_manifest()

        def which(n, path=None):
            return "/bin/" + n if n == "mlr" else None

        def run(argv, timeout=30):
            return 0, "mlr 6.99.0\n", ""
        by = {s["name"]: s for s in bm.detect_tools(m, table=None, which=which, run=run)}
        self.assertTrue(by["mlr"]["found"])
        self.assertEqual(by["mlr"]["version"], "6.99.0")
        self.assertIs(by["mlr"]["pinned"], False)
        run2 = lambda argv, timeout=30: (0, "mlr 6.22.0\n", "")
        by2 = {s["name"]: s for s in bm.detect_tools(m, table=None, which=which, run=run2)}
        self.assertIs(by2["mlr"]["pinned"], True)

    def test_a_tool_that_is_on_the_path_but_does_not_run_is_not_found(self):
        m = bm.load_manifest()
        by = {s["name"]: s for s in bm.detect_tools(m, table=None, which=lambda n, path=None: "/bin/" + n if n == "csvtk" else None, run=lambda argv, timeout=30: (127, "", "bad"))}
        self.assertFalse(by["csvtk"]["found"])

    def test_the_report_names_the_install_command_of_every_missing_tool_for_this_system(self):
        text = bm.check_tools_text([fake_status("a"), fake_status("b", found=False)], system="Linux")
        self.assertIn("install-b-linux", text)
        self.assertNotIn("install-a-linux", text)
        mac = bm.check_tools_text([fake_status("b", found=False)], system="Darwin")
        self.assertIn("install-b-mac", mac)

    def test_requiring_a_missing_tool_is_a_refusal_with_a_tag(self):
        with self.assertRaises(bm.Refusal) as e:
            bm.require_tools([fake_status("a"), fake_status("b", found=False)], ["a", "b"])
        self.assertEqual(e.exception.rule, "matrix.tool-missing")
        bm.require_tools([fake_status("a")], ["a"])
        with self.assertRaises(bm.Refusal) as e:
            bm.require_tools([fake_status("a")], ["nope"])
        self.assertEqual(e.exception.rule, "matrix.args")


class Scripted:
    """A launch that answers each argv from a table: the answers a correct and a wrong contender give."""

    def __init__(self, inputs, wrong=(), refuse=(), vary=False):
        self.inputs, self.wrong, self.refuse, self.vary, self.calls, self.n = inputs, set(wrong), set(refuse), vary, [], 0

    def __call__(self, argv, env=None, stdin_path=None, timeout=60):
        self.calls.append(list(argv))
        self.n += 1
        task = argv[-1] if argv[:1] == ["FAKE"] else None
        if task is None:
            return bn.Outcome(127, 0.0, b"", b"unknown", False)
        if task in self.refuse:
            return bn.Outcome(8, 0.01, b"", b"table: agg.bad-spec: no\n", False)
        ref = forms.reference(task, self.inputs)
        texts = ref["texts"]
        if task in self.wrong:
            texts = texts[1:]
        if self.vary and self.n % 2:
            texts = list(reversed(texts))
        return bn.Outcome(0, 0.01, render(ref, texts).encode(), b"", False)


class Orchestration(Tiny):
    def ctx(self, launch, tools=None, **kw):
        tools = tools if tools is not None else [fake_status("table", command=["FAKE"])]
        c = bm.Context(inputs=self.inputs, tools=tools, launch=launch, emit=lambda s: None, repeats=kw.get("repeats", 2), tasks=kw.get("tasks", ["X02", "X06"]),
                       form_builder=lambda fam, task, ns: forms.FormSpec(["FAKE", task], None, {}))
        return c

    def by(self, cells, kind, task, contender="table"):
        got = [c for c in cells if c["kind"] == kind and c["task"] == task and c["contender"] == contender]
        self.assertEqual(len(got), 1, (kind, task, contender, len(got)))
        return got[0]

    def test_a_correct_contender_is_expressible_and_correct(self):
        cells = bm.cells_ab(self.ctx(Scripted(self.inputs)))
        self.assertEqual(self.by(cells, "a", "X02")["value"]["outcome"], "yes")
        self.assertEqual(self.by(cells, "b", "X02")["value"]["status"], "ok")
        bm.validate_cells(cells)

    def test_a_wrong_contender_is_reported_wrong_with_its_difference(self):
        cells = bm.cells_ab(self.ctx(Scripted(self.inputs, wrong=["X02"])))
        b = self.by(cells, "b", "X02")
        self.assertEqual(b["value"]["status"], "wrong")
        self.assertTrue(b["value"]["detail"])
        self.assertEqual(self.by(cells, "b", "X06")["value"]["status"], "ok")

    def test_a_refusal_is_a_result_with_its_tag_and_nothing_to_judge(self):
        cells = bm.cells_ab(self.ctx(Scripted(self.inputs, refuse=["X06"])))
        a = self.by(cells, "a", "X06")
        self.assertEqual((a["value"]["outcome"], a["value"]["rule"]), ("refused", "agg.bad-spec"))
        b = self.by(cells, "b", "X06")
        self.assertIsNone(b["value"])
        self.assertIn("refused", b["reason"])

    def test_a_tool_that_is_not_installed_is_blank_for_every_kind_with_the_install_command(self):
        tools = [fake_status("qsv", found=False)]
        ctx = self.ctx(Scripted(self.inputs), tools=tools)
        cells = bm.cells_ab(ctx) + bm.cells_c(ctx) + bm.cells_clock_blank(ctx)
        self.assertTrue(cells)
        for c in cells:
            self.assertIsNone(c["value"], c)
            self.assertIn("not installed", c["reason"])
            self.assertIn("install-qsv-linux", c["reason"])
        bm.validate_cells(cells)

    def test_a_form_that_does_not_exist_is_no_form_not_not_expressible(self):
        ctx = self.ctx(Scripted(self.inputs))
        ctx.form_builder = lambda fam, task, ns: None
        a = self.by(bm.cells_ab(ctx), "a", "X02")
        self.assertEqual(a["value"]["outcome"], "no-form")
        self.assertIn("no form written", a["reason"] or a["value"].get("note", ""))

    def test_determinism_counts_distinct_outputs_and_a_stable_contender_gives_one(self):
        cells = bm.cells_c(self.ctx(Scripted(self.inputs)))
        c = self.by(cells, "c", "X02")
        self.assertEqual(c["value"]["canonical"], 1)
        self.assertEqual(c["value"]["raw"], 1)

    def test_determinism_sees_a_contender_whose_output_order_varies(self):
        cells = bm.cells_c(self.ctx(Scripted(self.inputs, vary=True)))
        c = self.by(cells, "c", "X02")
        self.assertEqual(c["value"]["canonical"], 1)
        self.assertEqual(c["value"]["raw"], 2)

    def test_the_clock_kinds_are_blank_without_the_switch_with_the_reason(self):
        cells = bm.cells_clock_blank(self.ctx(Scripted(self.inputs)))
        self.assertTrue(cells)
        for c in cells:
            self.assertIn(c["kind"], ("e", "f"))
            self.assertIsNone(c["value"])
            self.assertIn("NOT RUN", c["reason"])


class Program(unittest.TestCase):
    def run_main(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bm.main(argv, **kw)
        return rc, out.getvalue(), err.getvalue()

    def test_check_tools_prints_every_contender_and_exits_zero(self):
        rc, out, err = self.run_main(["--check-tools", "--table", "/nonexistent/table"])
        self.assertEqual(rc, 0, err)
        for name in ("table", "duckdb", "clickhouse", "polars", "pandas", "sqlite", "qsv", "mlr", "csvtk", "datafusion", "awk", "jq"):
            self.assertIn(name, out)

    def test_clock_kinds_without_the_switch_are_refused_with_a_tag(self):
        rc, out, err = self.run_main(["--kinds", "f"])
        self.assertEqual(rc, 3)
        self.assertIn("matrix.clock-switch", out + err)

    def test_the_switch_on_a_machine_that_is_not_quiet_is_refused_before_anything_runs(self):
        rc, out, err = self.run_main(["--clock", "--kinds", "f"], probes=FakeProbes(virt=True))
        self.assertEqual(rc, 3)
        self.assertIn("matrix.clock-not-quiet", out + err)

    def test_an_unknown_kind_and_an_unknown_task_are_refused(self):
        for argv in (["--kinds", "z"], ["--tasks", "X99"], ["--rows", "-5"]):
            rc, out, err = self.run_main(argv)
            self.assertEqual(rc, 3, argv)
            self.assertIn("matrix.args", out + err)

    def test_a_results_path_inside_the_repository_is_refused(self):
        rc, out, err = self.run_main(["--kinds", "a", "--results", str(ROOT / "docs" / "x.json")])
        self.assertEqual(rc, 3)
        self.assertIn("matrix.inside-repo", out + err)
        self.assertFalse((ROOT / "docs" / "x.json").exists())

    def test_the_default_kinds_are_the_ones_a_shared_vm_can_settle(self):
        self.assertEqual(bm.DEFAULT_KINDS, ["a", "b", "c", "d", "g", "h"])
        self.assertEqual(bm.CLOCK_KINDS, ["e", "f"])


@unittest.skipUnless(table_binary(), "no built table (TOOLBOX_BIN)")
class RealTable(unittest.TestCase):
    """The harness against the real `table`, at 400 rows: every task expressible and correct but the two probes, which are refused with a rule tag."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="matrix-real-")
        cls.inputs = forms.make_inputs(400, 10, cls.tmp.name)
        m = bm.load_manifest()
        cls.tools = [s for s in bm.detect_tools(m, table=table_binary()) if s["name"] == "table"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_cells_of_a_and_b_for_table(self):
        ctx = bm.Context(inputs=self.inputs, tools=self.tools, launch=bm.real_launch, emit=lambda s: None, repeats=2, tasks=list(forms.TASKS))
        cells = bm.cells_ab(ctx)
        bm.validate_cells(cells)
        for c in cells:
            if c["kind"] == "b" and not forms.TASKS[c["task"]]["probe"]:
                self.assertEqual(c["value"]["status"], "ok", c)
        for t in ("X10", "X11"):
            a = [c for c in cells if c["kind"] == "a" and c["task"] == t][0]
            self.assertEqual(a["value"]["outcome"], "refused", a)
            self.assertTrue(a["value"]["rule"], a)

    def test_the_own_bound_probe_refuses_with_a_tag_and_says_its_repair(self):
        ctx = bm.Context(inputs=self.inputs, tools=self.tools, launch=bm.real_launch, emit=lambda s: None, repeats=2, tasks=["X03"])
        cells = bm.cells_d(ctx)
        own = [c for c in cells if c["contender"] == "table" and c["value"] and c["value"].get("probe") == "own-bound-groups"]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["value"]["rule"], "limit.too-many-groups")


if __name__ == "__main__":
    unittest.main()
