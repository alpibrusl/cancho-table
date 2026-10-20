"""NB2 of docs/named-benchmarks.md: the timing driver `scripts/bench_named.py` and its gates, written before the driver.

What is held here, and that each can fail:

* G-answer: a contender's time is recorded only if its answer equals the reference of NB1 (scripts/bench_named_ref.py). One value off, one row missing or doubled, one trailing
  byte, a float printed differently, a different header, a different key: no time. (Row order is not part of a groupby answer, design 3.3; an ordered comparison is tested
  too, because the later suites need it.) Every answer is judged before any time is printed.
* G-refusal: a refused, crashed or timed-out contender, and one that fails while it is being timed, records its status and its reason and no time.
* G-record: every recorded number carries its conditions (3.1): machine, cores, load before and after, uptime, docker, soak, versions, file, seed, date, runs, statistic.
* G-publishable (Q11, Q12): a run on a loaded machine (one-minute load over a quarter of the cores), on a virtual machine, with a soak or a container running, or with
  fewer than 5 runs is flagged `publishable: false` with the reasons, and the writer of a published table refuses it, whatever the flag in the file says.
* G-repro: the same seed gives the same bytes through the driver.
* G-forms: every contender's form for every question equals the reference at a small scale, for the contenders installed (a missing one is skipped with its reason).
* the min-of-N, the load reading, the estimate and disk arithmetic, with injected launches, loads and probes, so no test here needs a real timing.
* fuzz: garbage where a contender's answer should be never raises.

No file larger than 20,000 rows, no network. The driver is a script, so the tool's authority row is untouched.
"""

import csv
import hashlib
import io
import json
import os
import pathlib
import random
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import unittest
from decimal import Decimal
from fractions import Fraction

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCRIPTS = ROOT / "scripts"
DRIVER = SCRIPTS / "bench_named.py"
sys.path.insert(0, str(SCRIPTS))

import bench_named_gen as gen  # noqa: E402
import bench_named_ref as ref  # noqa: E402


def driver():
    """The driver module, imported when a test needs it, so that a missing script is each test's own failure (and the reason is stated)."""
    if not DRIVER.exists():
        raise AssertionError("scripts/bench_named.py does not exist: NB2 is not built")
    import bench_named
    return bench_named


TABLE = os.environ.get("TOOLBOX_BIN")
TABLE = str(pathlib.Path(TABLE) / "table") if TABLE else str(ROOT / "build" / "table")
QUESTIONS = ref.load_questions()["questions"]
BY_ID = {q["id"]: q for q in QUESTIONS}


def answer_bytes(columns, rows):
    return ("\n".join([",".join(columns)] + [",".join(r) for r in rows]) + "\n").encode()


class Shared(unittest.TestCase):
    """A 20,000-row file (K=100) for the class, outside the repository, deleted after, and its reference."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="named-nb2-")
        cls.path, cls.meta = gen.write(20_000, 100, cls.tmp.name)
        cls.ref = ref.reference(cls.path, with_q10=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def good(self, qid):
        entry = self.ref[qid]
        return answer_bytes(entry["columns"], entry["rows"])


# ---------------------------------------------------------------------------------------------------------------- the arithmetic

class Arithmetic(unittest.TestCase):
    def test_parse_rows(self):
        d = driver()
        self.assertEqual(d.parse_rows("1e6"), 1_000_000)
        self.assertEqual(d.parse_rows("1e8"), 100_000_000)
        self.assertEqual(d.parse_rows("10000000"), 10_000_000)
        self.assertEqual(d.parse_rows("2e4"), 20_000)
        for bad in ("0", "-5", "abc", "", "1.5", "1e-3", "nan", "inf"):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    d.parse_rows(bad)

    def test_launches_are_questions_times_contenders_times_eight(self):
        d = driver()
        # design 3.5: 5 questions x 5 contenders x (1 warm-up + 5 runs + 1 for the peak memory + 1 for the answer) = 200
        self.assertEqual(d.launches(5, 5, 5), 200)
        self.assertEqual(d.launches(5, 7, 5), 280)
        self.assertEqual(d.launches(1, 1, 1), 4)
        self.assertEqual(d.launches(0, 7, 5), 0)
        self.assertEqual(d.launches(5, 0, 5), 0)

    def test_estimate_is_the_documented_arithmetic(self):
        d = driver()
        e = d.estimate(1_000_000, 100, 5, 7, 5)
        self.assertEqual(e["bytes"], gen.estimate_bytes(1_000_000, 100))
        self.assertEqual(e["disk_needed"], 3 * e["bytes"])
        self.assertEqual(e["launches"], 280)
        self.assertEqual(e["rows"], 1_000_000)
        big = d.estimate(100_000_000, 100, 5, 5, 5)
        self.assertEqual(big["bytes"], gen.estimate_bytes(100_000_000, 100))
        self.assertEqual(big["launches"], 200)
        # the estimate never claims a wall time: nothing was measured
        self.assertNotIn("seconds", json.dumps(e).lower())

    def test_disk_needs_three_times_the_file(self):
        d = driver()
        self.assertTrue(d.disk_ok(3_000, 1_000))
        self.assertFalse(d.disk_ok(2_999, 1_000))
        self.assertTrue(d.disk_ok(10**12, 1_000))
        self.assertFalse(d.disk_ok(0, 1))

    def test_only_the_smoke_size_runs_without_an_opt_in(self):
        d = driver()
        self.assertFalse(d.needs_opt_in(1_000_000))
        self.assertFalse(d.needs_opt_in(20_000))
        self.assertTrue(d.needs_opt_in(1_000_001))
        self.assertTrue(d.needs_opt_in(10_000_000))
        self.assertTrue(d.needs_opt_in(100_000_000))

    def test_gram_cores(self):
        d = driver()
        # Q11: cores 0-6 only after confirming that no soak and no container is running, else 0-5 as before
        self.assertEqual(d.gram_cores(soak_found=False, containers=[]), "0-6")
        self.assertEqual(d.gram_cores(soak_found=True, containers=[]), "0-5")
        self.assertEqual(d.gram_cores(soak_found=False, containers=["soak"]), "0-5")

    def test_taskset_prefix(self):
        d = driver()
        self.assertEqual(d.with_taskset(["a", "b"], None), ["a", "b"])
        self.assertEqual(d.with_taskset(["a", "b"], "0-6"), ["taskset", "-c", "0-6", "a", "b"])


class Statistics(unittest.TestCase):
    def test_summarise_picks_the_minimum_not_the_first_or_the_last(self):
        d = driver()
        s = d.summarise([5.0, 3.0, 4.0, 9.0, 6.0])
        self.assertEqual((s["runs"], s["min_s"], s["median_s"], s["first_s"]), (5, 3.0, 5.0, 5.0))
        self.assertEqual(s["all_s"], [5.0, 3.0, 4.0, 9.0, 6.0])

    def test_summarise_median_of_an_even_count(self):
        d = driver()
        s = d.summarise([1.0, 4.0, 2.0, 3.0])
        self.assertEqual((s["min_s"], s["median_s"], s["first_s"]), (1.0, 2.5, 1.0))
        s = d.summarise([7.0])
        self.assertEqual((s["runs"], s["min_s"], s["median_s"], s["first_s"]), (1, 7.0, 7.0, 7.0))

    def test_summarise_refuses_nothing_to_summarise(self):
        d = driver()
        with self.assertRaises(ValueError):
            d.summarise([])

    def test_the_first_run_is_listed_separately_from_the_minimum(self):
        d = driver()
        s = d.summarise([9.0, 1.0, 1.5])
        self.assertEqual(s["first_s"], 9.0)
        self.assertEqual(s["min_s"], 1.0)


class Load(unittest.TestCase):
    def test_a_quarter_of_the_cores_is_the_line(self):
        d = driver()
        # "exceeds a quarter of the cores" (3.1): equal is not loaded
        self.assertFalse(d.is_loaded(1.0, 4))
        self.assertTrue(d.is_loaded(1.01, 4))
        self.assertFalse(d.is_loaded(4.0, 16))
        self.assertTrue(d.is_loaded(4.01, 16))
        self.assertTrue(d.is_loaded(12.8, 16))  # the Mac when it was read (2.3)
        self.assertFalse(d.is_loaded(1.0, 16))  # gram when it was read
        self.assertFalse(d.is_loaded(0.0, 4))

    def test_load_is_read_from_the_injected_source(self):
        d = driver()
        self.assertEqual(d.read_load(lambda: (0.5, 0.25, 0.125)), (0.5, 0.25, 0.125))
        with self.assertRaises(OSError):
            d.read_load(lambda: (_ for _ in ()).throw(OSError("no load average")))


# ---------------------------------------------------------------------------------------------------------------- the answer gate

class Kinds(unittest.TestCase):
    def test_the_kind_of_each_column(self):
        d = driver()
        self.assertEqual(d.column_kinds(BY_ID["q1"]["columns"]), ["key", "int"])
        self.assertEqual(d.column_kinds(BY_ID["q2"]["columns"]), ["key", "key", "int"])
        self.assertEqual(d.column_kinds(BY_ID["q3"]["columns"]), ["key", "int", "mean"])
        self.assertEqual(d.column_kinds(BY_ID["q4"]["columns"]), ["key", "mean", "mean", "mean"])
        self.assertEqual(d.column_kinds(BY_ID["q5"]["columns"]), ["key", "int", "int", "dec"])
        self.assertEqual(d.column_kinds(BY_ID["q10"]["columns"]), ["key"] * 6 + ["dec", "int"])


class AnswerGate(Shared):
    """G-answer. The `table` profile is exact: the bytes of the cells are the reference's. The `double` profile is for engines that print doubles."""

    def verdict(self, qid, raw, profile="exact", ordered=False, exact="auto"):
        d = driver()
        if exact == "auto":
            exact = d.exact_means(self.path) if qid in ("q3", "q4") else None
        return d.compare_answer(BY_ID[qid], self.ref[qid], raw, profile, exact=exact, ordered=ordered)

    def test_the_reference_itself_passes_at_every_question(self):
        for qid in ("q1", "q2", "q3", "q4", "q5"):
            for profile in ("exact", "double"):
                with self.subTest(qid=qid, profile=profile):
                    v = self.verdict(qid, self.good(qid), profile)
                    self.assertTrue(v["ok"], v)
                    self.assertEqual(v["level"], "exact")

    def test_exact_means_round_to_the_reference(self):
        d = driver()
        ex = d.exact_means(self.path)
        for qid, cols in (("q3", [2]), ("q4", [1, 2, 3])):
            by_key = {r[0]: r for r in self.ref[qid]["rows"]}
            self.assertEqual(set(ex[qid]), {(k,) for k in by_key})
            for key, fracs in ex[qid].items():
                for frac, c in zip(fracs, cols):
                    self.assertIsInstance(frac, Fraction)
                    self.assertEqual(ref.fmt6(ref.div_half_even(frac.numerator * ref.SCALE, frac.denominator)), by_key[key[0]][c])

    def test_the_rows_in_another_order_are_the_same_set(self):
        rows = list(reversed(self.ref["q1"]["rows"]))
        raw = answer_bytes(self.ref["q1"]["columns"], rows)
        self.assertTrue(self.verdict("q1", raw)["ok"])
        # a groupby answer is a set of rows (3.3); an ordered question is not
        self.assertFalse(self.verdict("q1", raw, ordered=True)["ok"])
        self.assertTrue(self.verdict("q1", self.good("q1"), ordered=True)["ok"])

    def test_one_value_off_by_one(self):
        for qid, row, col in (("q1", 3, 1), ("q2", 7, 2), ("q3", 11, 1), ("q4", 5, 3), ("q5", 2, 3)):
            rows = [list(r) for r in self.ref[qid]["rows"]]
            whole, dot, frac = rows[row][col].partition(".")
            rows[row][col] = str(int(whole) + 1) + dot + frac  # one unit of the integer part: far outside any tolerance
            for profile in ("exact", "double"):
                with self.subTest(qid=qid, profile=profile):
                    v = self.verdict(qid, answer_bytes(self.ref[qid]["columns"], rows), profile)
                    self.assertFalse(v["ok"], v)
                    self.assertIsNone(v["level"])
                    self.assertTrue(v["problem"])

    def test_the_last_digit_off_by_one_is_wrong_for_the_exact_profile(self):
        for qid, row, col in (("q3", 11, 2), ("q4", 5, 3), ("q5", 2, 3)):
            rows = [list(r) for r in self.ref[qid]["rows"]]
            cell = rows[row][col]
            rows[row][col] = cell[:-1] + str((int(cell[-1]) + 1) % 10)
            with self.subTest(qid):
                self.assertFalse(self.verdict(qid, answer_bytes(self.ref[qid]["columns"], rows), "exact")["ok"])

    def test_one_row_missing_or_doubled_or_extra(self):
        cols, rows = self.ref["q2"]["columns"], [list(r) for r in self.ref["q2"]["rows"]]
        for name, variant in (("missing", rows[:-1]), ("first missing", rows[1:]), ("doubled", rows + [rows[4]]), ("extra key", rows + [["id999", "id001", "5"]]),
                              ("one replaced by a copy", rows[:3] + [rows[2]] + rows[4:])):
            with self.subTest(name):
                self.assertFalse(self.verdict("q2", answer_bytes(cols, variant))["ok"])
                self.assertFalse(self.verdict("q2", answer_bytes(cols, variant), "double")["ok"])

    def test_one_trailing_byte(self):
        good = self.good("q1")
        for name, raw in (("newline", good + b"\n"), ("space", good + b" "), ("nul", good + b"\0"), ("letter", good + b"x"), ("no final newline", good[:-1]),
                          ("crlf", good.replace(b"\n", b"\r\n")), ("blank line inside", good.replace(b"\n", b"\n\n", 1)), ("bom", b"\xef\xbb\xbf" + good),
                          ("space in a cell", good.replace(b",", b" ,", 3)), ("leading space", b" " + good)):
            with self.subTest(name):
                self.assertFalse(self.verdict("q1", raw)["ok"])
                self.assertFalse(self.verdict("q1", raw, "double")["ok"])

    def test_the_header_of_table_is_the_questions_and_a_double_engine_needs_the_right_count(self):
        cols = self.ref["q3"]["columns"]
        rows = self.ref["q3"]["rows"]
        renamed = answer_bytes(["id3", "v1", "v3"], rows)  # the owner's names, which DuckDB prints
        self.assertFalse(self.verdict("q3", renamed, "exact")["ok"])
        self.assertTrue(self.verdict("q3", renamed, "double")["ok"])
        self.assertFalse(self.verdict("q3", answer_bytes(["id3", "v1"], [r[:2] for r in rows]), "double")["ok"])
        self.assertFalse(self.verdict("q3", answer_bytes(cols + ["extra"], [r + ("1",) for r in rows]), "double")["ok"])
        self.assertFalse(self.verdict("q3", answer_bytes(cols, [r[:2] for r in rows]), "double")["ok"])  # cells missing in every row

    def test_a_float_printed_differently(self):
        rows = [list(r) for r in self.ref["q4"]["rows"]]
        base = rows[0][1]
        for name, text in (("trailing zero", base + "0"), ("fewer digits", base[:-1]), ("exponent", "%se0" % base), ("leading plus", "+" + base),
                           ("comma decimal", base.replace(".", ","))):
            variant = [list(r) for r in rows]
            variant[0][1] = text
            with self.subTest(name):
                self.assertFalse(self.verdict("q4", answer_bytes(self.ref["q4"]["columns"], variant), "exact")["ok"])
        # an engine that prints doubles may spell the same value differently: the level says so
        variant = [list(r) for r in rows]
        variant[0][1] = base + "000"
        v = self.verdict("q4", answer_bytes(self.ref["q4"]["columns"], variant), "double")
        self.assertTrue(v["ok"])
        self.assertEqual(v["level"], "numeric")
        # ... but not a different value, and not a word
        for bad in ("NaN", "nan", "inf", "-inf", "", "1e999", "0x10", "1_0", "abc", "--1", "4.9.6"):
            variant = [list(r) for r in rows]
            variant[0][1] = bad
            with self.subTest(bad):
                self.assertFalse(self.verdict("q4", answer_bytes(self.ref["q4"]["columns"], variant), "double")["ok"])

    def test_a_double_is_within_a_billionth_and_only_by_tolerance(self):
        d = driver()
        rows = [list(r) for r in self.ref["q3"]["rows"]]
        exact = d.exact_means(self.path)["q3"]
        key = rows[0][0]
        truth = Fraction(exact[(key,)][0])
        for name, factor, ok in (("1e-10", Fraction(1, 10**10), True), ("9e-10", Fraction(9, 10**10), True), ("1.1e-9", Fraction(11, 10**10), False), ("1e-8", Fraction(1, 10**8), False),
                                 ("-1e-10", -Fraction(1, 10**10), True), ("-1.1e-9", -Fraction(11, 10**10), False)):
            variant = [list(r) for r in rows]
            variant[0][2] = "%.17g" % float(truth * (1 + factor))
            with self.subTest(name):
                v = self.verdict("q3", answer_bytes(self.ref["q3"]["columns"], variant), "double")
                self.assertEqual(v["ok"], ok, v)
                if ok:
                    self.assertEqual(v["level"], "tolerance")
                # the exact profile never takes a tolerance
                self.assertFalse(self.verdict("q3", answer_bytes(self.ref["q3"]["columns"], variant), "exact")["ok"])

    def test_integers_are_never_taken_by_tolerance(self):
        rows = [list(r) for r in self.ref["q1"]["rows"]]
        v = int(rows[2][1])
        for text, ok in (("%d.00" % v, True), ("%d.0" % v, True), ("%d.000000000000" % v, True), ("%d.0000001" % v, False), ("%d.5" % v, False), ("%d" % (v + 1), False)):
            variant = [list(r) for r in rows]
            variant[2][1] = text
            with self.subTest(text):
                got = self.verdict("q1", answer_bytes(self.ref["q1"]["columns"], variant), "double")
                self.assertEqual(got["ok"], ok, got)
                if ok:
                    self.assertEqual(got["level"], "numeric")
        # a large sum cannot hide behind a relative tolerance of 1e-9
        variant = [list(r) for r in rows]
        variant[2][1] = str(v * 10**12 + 1)
        self.assertFalse(self.verdict("q1", answer_bytes(self.ref["q1"]["columns"], variant), "double")["ok"])

    def test_keys_are_text_and_exact(self):
        rows = [list(r) for r in self.ref["q1"]["rows"]]
        rows[0][0] = rows[0][0].upper()
        self.assertFalse(self.verdict("q1", answer_bytes(self.ref["q1"]["columns"], rows), "double")["ok"])
        rows = [list(r) for r in self.ref["q1"]["rows"]]
        rows[0][0] = rows[1][0]  # a key twice, another missing
        self.assertFalse(self.verdict("q1", answer_bytes(self.ref["q1"]["columns"], rows), "double")["ok"])

    def test_empty_and_unparseable_outputs(self):
        for name, raw in (("empty", b""), ("only a newline", b"\n"), ("header only", b"id1,sum:v1\n"), ("not utf-8", b"\xff\xfe\x00bad\n"), ("a json refusal", b'{"ok":false}\n'),
                          ("a quote that never ends", b'id1,v1\n"id001,3\n'), ("binary", bytes(range(256)))):
            with self.subTest(name):
                v = self.verdict("q1", raw, "double")
                self.assertFalse(v["ok"])
                self.assertIsInstance(v["problem"], str)

    def test_a_quoted_spelling_of_the_same_cells_is_the_same_answer(self):
        # an engine may quote every text cell; the cells are what is compared
        cols, rows = self.ref["q1"]["columns"], self.ref["q1"]["rows"]
        raw = ("\n".join([",".join(cols)] + ['"%s",%s' % (r[0], r[1]) for r in rows]) + "\n").encode()
        self.assertTrue(self.verdict("q1", raw, "double")["ok"])

    def test_the_first_difference_is_reported(self):
        rows = [list(r) for r in self.ref["q1"]["rows"]]
        rows[5][1] = str(int(rows[5][1]) + 1)
        v = self.verdict("q1", answer_bytes(self.ref["q1"]["columns"], rows))
        self.assertIn(rows[5][0], v["problem"])


class Fuzz(Shared):
    """The parsing of a contender's output must not raise, whatever the output is."""

    def test_garbage_never_raises(self):
        d = driver()
        rng = random.Random(1234)
        exact = d.exact_means(self.path)
        for i in range(400):
            n = rng.choice([0, 1, 2, 5, 30, 300, 3000])
            raw = bytes(rng.randrange(256) for _ in range(n))
            for qid in ("q1", "q3", "q4"):
                v = d.compare_answer(BY_ID[qid], self.ref[qid], raw, rng.choice(["exact", "double"]), exact=exact.get(qid), ordered=rng.random() < 0.5)
                self.assertFalse(v["ok"], (i, qid, raw[:40]))

    def test_mutated_answers_never_raise(self):
        d = driver()
        rng = random.Random(99)
        exact = d.exact_means(self.path)
        for qid in ("q1", "q2", "q3", "q4", "q5"):
            good = self.good(qid)
            for i in range(120):
                raw = bytearray(good)
                for _ in range(rng.randrange(1, 4)):
                    at = rng.randrange(len(raw))
                    op = rng.choice(("flip", "delete", "insert", "dup-line", "cut"))
                    if op == "flip":
                        raw[at] = rng.randrange(256)
                    elif op == "delete":
                        del raw[at]
                    elif op == "insert":
                        raw.insert(at, rng.choice(b'",\n\r 0e.-+x\xff\0'))
                    elif op == "dup-line":
                        lines = bytes(raw).split(b"\n")
                        j = rng.randrange(len(lines))
                        lines.insert(j, lines[j])
                        raw = bytearray(b"\n".join(lines))
                    else:
                        raw = raw[:at]
                    if not raw:
                        raw = bytearray(b"x")
                v = d.compare_answer(BY_ID[qid], self.ref[qid], bytes(raw), rng.choice(["exact", "double"]), exact=exact.get(qid))
                self.assertIn(v["ok"], (True, False))
                if not v["ok"]:
                    self.assertIsInstance(v["problem"], str)

    def test_parse_output_raises_only_its_own_error(self):
        d = driver()
        rng = random.Random(5)
        for _ in range(300):
            raw = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
            try:
                header, rows = d.parse_output(raw)
            except d.AnswerError:
                continue
            self.assertIsInstance(header, list)
            self.assertTrue(all(len(r) == len(header) for r in rows))


class Classify(unittest.TestCase):
    def test_statuses(self):
        d = driver()
        refusal = b'{"ok":false,"command":"table","error":{"rule":"limit.too-many-groups"},"errors":[{"rule":"limit.too-many-groups"}]}\n'
        c = d.classify(0, b"x", b"", is_table=True, timed_out=False)
        self.assertEqual(c["status"], "ok")
        c = d.classify(8, refusal, b"", is_table=True, timed_out=False)
        self.assertEqual((c["status"], c["rule"]), ("refused", "limit.too-many-groups"))
        self.assertIn("limit.too-many-groups", c["reason"])
        for rc in (-11, -9, -6, 132, 134, 139):
            self.assertEqual(d.classify(rc, b"", b"", is_table=True, timed_out=False)["status"], "crashed", rc)
            self.assertEqual(d.classify(rc, b"", b"", is_table=False, timed_out=False)["status"], "crashed", rc)
        c = d.classify(1, b"", b"Error: out of memory\n", is_table=False, timed_out=False)
        self.assertEqual(c["status"], "refused")
        self.assertIn("out of memory", c["reason"])
        self.assertIsNone(c["rule"])
        c = d.classify(None, b"", b"", is_table=False, timed_out=True)
        self.assertEqual(c["status"], "timeout")
        c = d.classify(0, b"x", b"", is_table=False, timed_out=True)
        self.assertEqual(c["status"], "timeout")  # a timeout wins over a status read afterwards
        # a refusal of table with no rule tag is still a refusal, and says so
        c = d.classify(3, b"not json\n", b"", is_table=True, timed_out=False)
        self.assertEqual(c["status"], "refused")
        self.assertIsNone(c["rule"])

    def test_the_reason_is_short_and_has_no_control_bytes(self):
        d = driver()
        c = d.classify(1, b"", b"E" * 5000 + b"\x00\x1b[31m\n", is_table=False, timed_out=False)
        self.assertLessEqual(len(c["reason"]), 300)
        self.assertTrue(all(ch.isprintable() for ch in c["reason"]))


# ---------------------------------------------------------------------------------------------------------------- the orchestration, with injected launches

class Fake:
    """A fake world for `run_question`: contenders are named programs whose launches return canned outcomes; nothing is started and nothing is timed."""

    def __init__(self, d, ref_entry, qid="q1", seconds=None):
        self.d = d
        self.qid = qid
        self.entry = ref_entry
        self.calls = []                   # (name, sink) in order: sink is "answer", "timed" or "rss"
        self.lines = []
        self.modes = {}                   # name -> mode
        self.seconds = seconds or {}      # name -> list of times for the timed launches (warm-up first)
        self.fail_on = {}                 # name -> index of the timed launch that fails
        self.count = {}

    def contender(self, name, mode="good", profile="exact", family="fake"):
        self.modes[name] = mode
        return self.d.Contender(name=name, family=family, threads="1", profile=profile, version="fake 1", form=lambda qid, file, name=name: self.d.Form(["fake", name, qid], ["fake", name, qid, "timed"], {}))

    def answer(self, mode):
        cols, rows = self.entry["columns"], [list(r) for r in self.entry["rows"]]
        if mode == "wrong-value":
            rows[3][1] = str(int(rows[3][1]) + 1)
        elif mode == "drop-group":
            rows = rows[:-1]
        elif mode == "tolerance":
            return None
        return answer_bytes(cols, rows)

    def launch(self, argv, env=None, capture=True, timeout=120):
        name = argv[1]
        timed = argv[-1] == "timed"
        mode = self.modes[name]
        sink = "timed" if timed else "answer"
        self.calls.append((name, sink))
        if mode == "crash":
            return self.d.Outcome(returncode=-11, seconds=0.01, stdout=b"", stderr=b"", timed_out=False)
        if mode == "refuse":
            return self.d.Outcome(returncode=8, seconds=0.01, stdout=b'{"ok":false,"error":{"rule":"limit.too-many-groups"},"errors":[{"rule":"limit.too-many-groups"}]}\n', stderr=b"", timed_out=False)
        if mode == "hang":
            return self.d.Outcome(returncode=None, seconds=timeout, stdout=b"", stderr=b"", timed_out=True)
        if timed:
            k = self.count.get(name, 0)
            self.count[name] = k + 1
            if mode == "fails-while-timing" and k == 2:
                return self.d.Outcome(returncode=1, seconds=0.01, stdout=b"", stderr=b"boom", timed_out=False)
            seq = self.seconds.get(name, [1.0] * 20)
            return self.d.Outcome(returncode=0, seconds=seq[k % len(seq)], stdout=b"", stderr=b"", timed_out=False)
        out = self.answer("good" if mode in ("good", "fails-while-timing") else mode)
        return self.d.Outcome(returncode=0, seconds=0.5, stdout=out if out is not None else b"", stderr=b"", timed_out=False)

    def rusage(self, argv, env=None, timeout=120):
        self.calls.append((argv[1], "rss"))
        return {"peak_rss_kb": 1234, "cpu_s": 0.75}

    def context(self, runs=5, file="F.csv"):
        d = self.d
        return d.Context(launch=self.launch, rusage=self.rusage, emit=self.lines.append, runs=runs, timeout=120, file=file, ref={self.qid: self.entry}, exact={}, table=None)


class Orchestration(Shared):
    def setUp(self):
        self.d = driver()
        self.q = BY_ID["q1"]
        self.fake = Fake(self.d, self.ref["q1"])

    def run_q(self, contenders, runs=5):
        return self.d.run_question(self.fake.context(runs=runs), self.q, contenders)

    def rec(self, records, name):
        (r,) = [r for r in records if r["contender"] == name]
        return r

    def test_a_good_contender_is_timed_and_the_minimum_is_selected(self):
        self.fake.seconds["good"] = [100.0, 5.0, 3.0, 4.0, 9.0, 6.0]  # the first launch is the warm-up
        recs = self.run_q([self.fake.contender("good")])
        r = self.rec(recs, "good")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["timing"]["min_s"], 3.0)
        self.assertEqual(r["timing"]["all_s"], [5.0, 3.0, 4.0, 9.0, 6.0])  # the warm-up (100.0) is not one of the runs
        self.assertEqual(r["timing"]["runs"], 5)
        self.assertEqual(r["timing"]["first_s"], 5.0)
        self.assertEqual(r["timing"]["median_s"], 5.0)
        self.assertEqual((r["peak_rss_kb"], r["cpu_s"]), (1234, 0.75))
        self.assertEqual(r["answer"]["match"], "exact")

    def test_one_warm_up_then_n_interleaved_rounds(self):
        names = ["a", "b", "c"]
        recs = self.run_q([self.fake.contender(n) for n in names], runs=4)
        timed = [n for n, sink in self.fake.calls if sink == "timed"]
        self.assertEqual(timed[:3], ["a", "b", "c"])  # the warm-ups, each contender once
        rounds = [timed[3 + 3 * i: 6 + 3 * i] for i in range(4)]
        self.assertEqual(rounds, [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"], ["a", "b", "c"]])  # the order rotates
        self.assertEqual(len(timed), 3 + 12)
        for r in recs:
            self.assertEqual(r["timing"]["runs"], 4)

    def test_every_answer_is_judged_before_any_time_is_printed(self):
        recs = self.run_q([self.fake.contender("a"), self.fake.contender("bad", "wrong-value"), self.fake.contender("c")])
        sinks = [sink for _, sink in self.fake.calls]
        last_answer = max(i for i, s in enumerate(sinks) if s == "answer")
        first_timed = min(i for i, s in enumerate(sinks) if s == "timed")
        self.assertLess(last_answer, first_timed)
        lines = self.fake.lines
        last_gate = max(i for i, l in enumerate(lines) if l.startswith("answer"))
        first_time_line = min(i for i, l in enumerate(lines) if l.startswith("time"))
        self.assertLess(last_gate, first_time_line)
        self.assertEqual(len([r for r in recs if r["status"] == "ok"]), 2)

    def test_a_wrong_answer_is_reported_and_not_timed(self):
        for mode in ("wrong-value", "drop-group"):
            with self.subTest(mode):
                self.fake = Fake(self.d, self.ref["q1"])
                recs = self.run_q([self.fake.contender("good"), self.fake.contender("bad", mode)])
                r = self.rec(recs, "bad")
                self.assertEqual(r["status"], "wrong-answer")
                self.assertTrue(r["reason"])
                for key in ("timing", "peak_rss_kb", "cpu_s"):
                    self.assertNotIn(key, r)
                self.assertEqual([sink for n, sink in self.fake.calls if n == "bad"], ["answer"])  # one launch: the answer, no warm-up, no run, no rss
                self.assertFalse([l for l in self.fake.lines if l.startswith("time") and "bad" in l])
                self.assertTrue([l for l in self.fake.lines if l.startswith("answer") and "bad" in l and "NOT TIMED" in l])
                self.assertEqual(self.rec(recs, "good")["status"], "ok")

    def test_a_refusal_a_crash_and_a_timeout_have_no_time_and_a_reason(self):
        recs = self.run_q([self.fake.contender("good"), self.fake.contender("r", "refuse"), self.fake.contender("c", "crash"), self.fake.contender("h", "hang")])
        r = self.rec(recs, "r")
        self.assertEqual((r["status"], r["rule"]), ("refused", None))  # a fake is not table: no tag is read
        self.assertIn("exit 8", r["reason"])
        self.assertEqual(self.rec(recs, "c")["status"], "crashed")
        h = self.rec(recs, "h")
        self.assertEqual(h["status"], "timeout")
        self.assertIn("over 120 s", h["reason"])
        for name in ("r", "c", "h"):
            for key in ("timing", "peak_rss_kb", "cpu_s"):
                self.assertNotIn(key, self.rec(recs, name))
            self.assertEqual([sink for n, sink in self.fake.calls if n == name], ["answer"])
            self.assertFalse([l for l in self.fake.lines if l.startswith("time") and (" %s " % name) in l])

    def test_table_refusal_keeps_its_rule_tag(self):
        c = self.fake.contender("t", "refuse", family="table")
        recs = self.run_q([c])
        r = self.rec(recs, "t")
        self.assertEqual((r["status"], r["rule"]), ("refused", "limit.too-many-groups"))
        self.assertIn("limit.too-many-groups", r["reason"])

    def test_a_contender_that_fails_while_it_is_timed_loses_its_times(self):
        recs = self.run_q([self.fake.contender("good"), self.fake.contender("flaky", "fails-while-timing")])
        r = self.rec(recs, "flaky")
        self.assertEqual(r["status"], "failed-while-timing")
        for key in ("timing", "peak_rss_kb", "cpu_s"):
            self.assertNotIn(key, r)
        self.assertFalse([l for l in self.fake.lines if l.startswith("time") and "flaky" in l])
        self.assertEqual(self.rec(recs, "good")["timing"]["runs"], 5)

    def test_tolerance_is_marked_and_printed(self):
        entry = {"columns": ["id1", "sum:v1"], "rows": [("id001", "10"), ("id002", "20")], "chk": "30"}
        self.fake = Fake(self.d, entry)
        self.fake.answer = lambda mode: b"id1,v1\nid001,10.00\nid002,20.0\n"
        recs = self.d.run_question(self.fake.context(), self.q, [self.fake.contender("duck", profile="double")])
        r = recs[0]
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["answer"]["match"], "numeric")
        self.assertTrue([l for l in self.fake.lines if l.startswith("answer") and "value-equal" in l])
        # only by tolerance
        entry = {"columns": ["id3", "sum:v1", "mean:v3"], "rows": [("id1", "10", "50.000000")], "chk": "x"}
        exact = {"q3": {("id1",): (Fraction(50),)}}
        self.fake = Fake(self.d, entry, qid="q3")
        self.fake.answer = lambda mode: b"id3,v1,v3\nid1,10,50.00000001\n"
        ctx = self.fake.context()
        ctx.exact = exact
        recs = self.d.run_question(ctx, BY_ID["q3"], [self.fake.contender("duck", profile="double")])
        self.assertEqual(recs[0]["status"], "ok")
        self.assertEqual(recs[0]["answer"]["match"], "tolerance")
        self.assertEqual(recs[0]["answer"]["tolerance"], 1e-9)
        self.assertTrue([l for l in self.fake.lines if l.startswith("answer") and "only by tolerance" in l])

    def test_no_contender_no_calls(self):
        self.assertEqual(self.run_q([]), [])
        self.assertEqual(self.fake.calls, [])

    def test_a_dropped_contender_is_not_launched(self):
        ctx = self.fake.context()
        ctx.dropped = {("q1", "slow")}
        recs = self.d.run_question(ctx, self.q, [self.fake.contender("slow"), self.fake.contender("fast")])
        r = self.rec(recs, "slow")
        self.assertEqual(r["status"], "not-run")
        self.assertIn("over 120 s", r["reason"])
        self.assertNotIn("slow", [n for n, _ in self.fake.calls])

    def test_a_timeout_adds_to_the_dropped(self):
        ctx = self.fake.context()
        ctx.dropped = set()
        self.d.run_question(ctx, self.q, [self.fake.contender("h", "hang")])
        self.assertIn(("q1", "h"), ctx.dropped)


class NotExpressible(unittest.TestCase):
    def ctx(self, outcome):
        d = driver()
        calls = []

        def launch(argv, env=None, capture=True, timeout=120):
            calls.append(argv)
            return outcome

        return d, calls, d.Context(launch=launch, rusage=lambda *a, **k: None, emit=lambda s: None, runs=1, timeout=120, file="F.csv", ref={}, exact={}, table="/bin/table")

    def test_a_refused_probe_is_the_classification_confirmed(self):
        d, calls, ctx = self.ctx(None)
        out = json.dumps({"ok": False, "error": {"rule": "agg.bad-spec"}, "errors": [{"rule": "agg.bad-spec"}]}).encode() + b"\n"
        ctx.launch = lambda argv, env=None, capture=True, timeout=120: d.Outcome(returncode=3, seconds=0.0, stdout=out, stderr=b"", timed_out=False)
        r = d.probe_not_expressible(ctx, BY_ID["q6"])
        self.assertEqual(r["status"], "not-expressible")
        self.assertIn("median", r["reason"])
        self.assertEqual(r["rule"], "agg.bad-spec")
        self.assertNotIn("timing", r)

    def test_an_exit_zero_is_never_a_refusal_even_with_the_right_rule(self):
        d, calls, ctx = self.ctx(None)
        out = json.dumps({"ok": False, "error": {"rule": "agg.bad-spec"}, "errors": [{"rule": "agg.bad-spec"}]}).encode() + b"\n"
        ctx.launch = lambda argv, env=None, capture=True, timeout=120: d.Outcome(returncode=0, seconds=0.0, stdout=out, stderr=b"", timed_out=False)
        self.assertEqual(d.probe_not_expressible(ctx, BY_ID["q6"])["status"], "class-mismatch")
        ctx.launch = lambda argv, env=None, capture=True, timeout=120: d.Outcome(returncode=None, seconds=1.0, stdout=out, stderr=b"", timed_out=True)
        self.assertEqual(d.probe_not_expressible(ctx, BY_ID["q6"])["status"], "class-mismatch")

    def test_a_probe_that_now_works_or_changes_its_rule_is_a_stale_classification(self):
        d, calls, ctx = self.ctx(None)
        ctx.launch = lambda argv, env=None, capture=True, timeout=120: d.Outcome(returncode=0, seconds=0.0, stdout=b"id4\n", stderr=b"", timed_out=False)
        self.assertEqual(d.probe_not_expressible(ctx, BY_ID["q6"])["status"], "class-mismatch")
        out = json.dumps({"ok": False, "error": {"rule": "something.else"}, "errors": [{"rule": "something.else"}]}).encode() + b"\n"
        ctx.launch = lambda argv, env=None, capture=True, timeout=120: d.Outcome(returncode=3, seconds=0.0, stdout=out, stderr=b"", timed_out=False)
        r = d.probe_not_expressible(ctx, BY_ID["q6"])
        self.assertEqual(r["status"], "class-mismatch")
        self.assertIn("something.else", r["reason"])


# ---------------------------------------------------------------------------------------------------------------- the conditions and the publishable flag

class FakeProbes:
    """An idle, dedicated, 16-core machine: everything that makes a run publishable. A test breaks one thing."""

    def __init__(self, **over):
        self.over = over

    def get(self, name, default):
        return self.over.get(name, default)

    def loadavg(self):
        return self.get("loadavg", (0.3, 0.2, 0.1))

    def cpu_count(self):
        return self.get("cpu_count", 16)

    def affinity(self):
        return self.get("affinity", 16)

    def cpu_model(self):
        return self.get("cpu_model", "12th Gen Intel(R) Core(TM) i7-1260P")

    def ram_bytes(self):
        return self.get("ram_bytes", 31 * 2**30)

    def virtualised(self):
        return self.get("virtualised", (False, "no hypervisor flag in /proc/cpuinfo"))

    def uptime(self):
        return self.get("uptime", " 10:00:00 up 3 days,  1 user,  load average: 0.30, 0.20, 0.10")

    def docker(self):
        return self.get("docker", {"status": "ok", "containers": [], "detail": "docker ps: no container"})

    def soak(self):
        return self.get("soak", {"found": False, "lines": []})

    def now(self):
        return self.get("now", "2026-10-20T12:00:00Z")

    def node(self):
        return "gram"

    def system(self):
        return "Linux"

    def machine(self):
        return "x86_64"

    def python(self):
        return "3.14.4"


def run_info(**over):
    info = {"rows": 1_000_000, "k": 100, "seed": 108, "runs": 5, "step": "1e6", "path_class": "internal SSD", "taskset": None, "threads_many": 6,
            "file": {"name": "G1_1e6_1e2_0_0.csv", "bytes": 49_916_813, "sha256": "16f80eb8ced039012aa9bbfdde73ecf1468e57ae28c6e40611d9736d79a73d6a"},
            "contenders": {"table@1": {"family": "table", "version": "0.3.0", "pin": None, "threads": "1", "path": "/x/table", "commit": "abc"},
                           "duckdb@1": {"family": "duckdb", "version": "v1.5.6", "pin": "1.5.6", "threads": "1", "path": "/x/duckdb"}}}
    info.update(over)
    return info


class Conditions(unittest.TestCase):
    def conditions(self, **over):
        d = driver()
        run = over.pop("run", {})
        return d.collect_conditions(FakeProbes(**over), run_info(**run))

    def test_a_quiet_dedicated_machine_is_publishable(self):
        d = driver()
        c = self.conditions()
        self.assertEqual(d.missing_conditions(c), [])
        ok, reasons = d.evaluate_publishable(c)
        self.assertTrue(ok, reasons)
        self.assertEqual(reasons, [])

    def test_every_recorded_condition(self):
        c = self.conditions()
        self.assertEqual(c["machine"]["cores"], 16)
        self.assertEqual(c["machine"]["affinity_cores"], 16)
        self.assertEqual(c["machine"]["cpu_model"], "12th Gen Intel(R) Core(TM) i7-1260P")
        self.assertEqual(c["machine"]["ram_bytes"], 31 * 2**30)
        self.assertEqual(c["load"]["start"], [0.3, 0.2, 0.1])
        self.assertIn("load average", c["uptime"])
        self.assertEqual(c["docker"]["status"], "ok")
        self.assertEqual(c["soak"]["found"], False)
        self.assertEqual(c["runs"], 5)
        self.assertEqual(c["seed"], 108)
        self.assertEqual(c["file"]["sha256"], "16f80eb8ced039012aa9bbfdde73ecf1468e57ae28c6e40611d9736d79a73d6a")
        self.assertEqual(c["file"]["bytes"], 49_916_813)
        self.assertEqual(c["date"], "2026-10-20T12:00:00Z")
        self.assertEqual(c["contenders"]["table@1"]["version"], "0.3.0")
        self.assertEqual(c["contenders"]["duckdb@1"]["threads"], "1")
        self.assertIn("minimum of 5", c["statistic"])
        self.assertIn("warm", c["cache"])
        self.assertEqual(c["sink"], "/dev/null")

    def test_each_break_has_its_reason(self):
        d = driver()
        cases = {
            "loaded": (dict(loadavg=(4.5, 3.0, 2.0)), "loaded"),
            "virtual": (dict(virtualised=(True, "hypervisor flag in /proc/cpuinfo")), "virtual machine"),
            "unknown vm": (dict(virtualised=(None, "no way to tell")), "cannot tell"),
            "container": (dict(docker={"status": "ok", "containers": ["soak-1"], "detail": "x"}), "container"),
            "docker broken": (dict(docker={"status": "error", "containers": [], "detail": "cannot connect to the daemon"}), "docker"),
            "soak": (dict(soak={"found": True, "lines": ["123 soak.sh"]}), "soak"),
        }
        for name, (over, word) in cases.items():
            with self.subTest(name):
                c = self.conditions(**over)
                ok, reasons = d.evaluate_publishable(c)
                self.assertFalse(ok)
                self.assertEqual(len(reasons), 1, reasons)
                self.assertIn(word, reasons[0].lower())
        c = self.conditions(run={"runs": 4})
        ok, reasons = d.evaluate_publishable(c)
        self.assertFalse(ok)
        self.assertIn("runs", reasons[0])
        c = self.conditions(docker={"status": "not-installed", "containers": [], "detail": "docker is not installed"})
        self.assertTrue(d.evaluate_publishable(c)[0])  # no docker, nothing to run in it

    def test_the_load_line_is_a_quarter_of_the_cores_of_the_machine(self):
        d = driver()
        self.assertTrue(d.evaluate_publishable(self.conditions(loadavg=(4.0, 4.0, 4.0)))[0])
        self.assertFalse(d.evaluate_publishable(self.conditions(loadavg=(4.01, 0.0, 0.0)))[0])
        self.assertTrue(d.evaluate_publishable(self.conditions(cpu_count=4, affinity=4, loadavg=(1.0, 0.0, 0.0)))[0])
        self.assertFalse(d.evaluate_publishable(self.conditions(cpu_count=4, affinity=4, loadavg=(1.2, 0.0, 0.0)))[0])
        # the load after the run is recorded and is not what decides (the run's own work is in it)
        c = self.conditions()
        c["load"]["end"] = [9.0, 5.0, 2.0]
        self.assertTrue(d.evaluate_publishable(c)[0])

    def test_all_reasons_are_listed(self):
        d = driver()
        c = self.conditions(loadavg=(9.0, 9.0, 9.0), virtualised=(True, "x"), soak={"found": True, "lines": ["1 soak"]})
        ok, reasons = d.evaluate_publishable(c)
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 3, reasons)

    def test_a_missing_condition_is_named(self):
        d = driver()
        full = self.conditions()
        paths = ["machine.cores", "machine.cpu_model", "machine.ram_bytes", "machine.affinity_cores", "virtualised.evidence", "load.start", "uptime", "docker.status", "soak.found",
                 "contenders", "file.sha256", "file.bytes", "file.name", "seed", "date", "runs", "statistic", "sink", "cache", "python", "step"]
        self.assertEqual(sorted(d.REQUIRED_CONDITIONS), sorted(set(d.REQUIRED_CONDITIONS)))
        for p in paths:
            self.assertIn(p, d.REQUIRED_CONDITIONS)
            c = json.loads(json.dumps(full))
            node = c
            *head, last = p.split(".")
            for part in head:
                node = node[part]
            del node[last]
            with self.subTest(p):
                self.assertEqual(d.missing_conditions(c), [p])
            c = json.loads(json.dumps(full))
            node = c
            for part in head:
                node = node[part]
            node[last] = "" if isinstance(node[last], str) else None
            with self.subTest(p + " empty"):
                self.assertEqual(d.missing_conditions(c), [p])

    def test_a_contender_without_its_version_or_threads_is_missing(self):
        d = driver()
        c = self.conditions()
        del c["contenders"]["duckdb@1"]["version"]
        self.assertEqual(d.missing_conditions(c), ["contenders.duckdb@1.version"])
        c = self.conditions()
        c["contenders"]["duckdb@1"]["threads"] = ""
        self.assertEqual(d.missing_conditions(c), ["contenders.duckdb@1.threads"])

    def test_the_real_probes_fill_every_condition(self):
        d = driver()
        c = d.collect_conditions(d.Probes(), run_info(file={"name": "f", "bytes": 1, "sha256": "0" * 64}))
        self.assertEqual(d.missing_conditions(c), [])
        self.assertGreaterEqual(c["machine"]["cores"], 1)
        self.assertEqual(len(c["load"]["start"]), 3)
        ok, reasons = d.evaluate_publishable(c)
        self.assertEqual(ok, reasons == [])
        v, evidence = d.Probes().virtualised()
        self.assertIn(v, (True, False, None))
        self.assertTrue(evidence)


def result_of(d, conditions, records):
    ok, reasons = d.evaluate_publishable(conditions)
    return {"schema": "bench-named.v1", "suite": "groupby", "publishable": ok, "publishable_reasons": reasons, "conditions": conditions, "results": records, "not_run": []}


def ok_record(qid="q1", name="table@1", **over):
    r = {"question": qid, "contender": name, "family": "table", "threads": "1", "version": "0.3.0", "status": "ok", "reason": None, "rule": None,
         "answer": {"match": "exact", "tolerance": None},
         "timing": {"runs": 5, "min_s": 0.5, "median_s": 0.6, "first_s": 0.9, "all_s": [0.9, 0.6, 0.5, 0.6, 0.7], "warmup_s": 1.0},
         "peak_rss_kb": 2048, "cpu_s": 0.4}
    r.update(over)
    return r


def bad_record(status="wrong-answer", name="duckdb@1", qid="q1"):
    return {"question": qid, "contender": name, "family": "duckdb", "threads": "1", "version": "v1.5.6", "status": status, "reason": "first difference: id003", "rule": None}


class Publishable(unittest.TestCase):
    def setUp(self):
        self.d = driver()
        self.tmp = tempfile.TemporaryDirectory(prefix="named-nb2-pub-")
        self.out = pathlib.Path(self.tmp.name) / "table.md"

    def tearDown(self):
        self.tmp.cleanup()

    def conditions(self, **over):
        return self.d.collect_conditions(FakeProbes(**over), run_info())

    def test_validate_result(self):
        d = self.d
        r = result_of(d, self.conditions(), [ok_record(), bad_record()])
        self.assertEqual(d.validate_result(r), [])
        timed_bad = bad_record()
        timed_bad["timing"] = {"runs": 5, "min_s": 0.1, "median_s": 0.1, "first_s": 0.1, "all_s": [0.1] * 5, "warmup_s": 0.1}
        self.assertTrue(d.validate_result(result_of(d, self.conditions(), [timed_bad])), "a failed contender with a time must be a problem")
        no_time = ok_record()
        del no_time["timing"]
        self.assertTrue(d.validate_result(result_of(d, self.conditions(), [no_time])))
        with_rss = bad_record("refused")
        with_rss["peak_rss_kb"] = 1
        self.assertTrue(d.validate_result(result_of(d, self.conditions(), [with_rss])))
        not_n = ok_record()
        not_n["timing"]["all_s"] = [0.5]
        self.assertTrue(d.validate_result(result_of(d, self.conditions(), [not_n])))
        wrong_min = ok_record()
        wrong_min["timing"]["min_s"] = 0.9
        self.assertTrue(d.validate_result(result_of(d, self.conditions(), [wrong_min])))
        c = self.conditions()
        del c["machine"]["cores"]
        self.assertTrue(d.validate_result(result_of(d, c, [ok_record()])), "a number without its conditions must be a problem")
        r = result_of(d, self.conditions(), [ok_record()])
        r["publishable"] = True
        r2 = result_of(d, self.conditions(loadavg=(9.0, 9.0, 9.0)), [ok_record()])
        r2["publishable"] = True
        self.assertTrue(d.validate_result(r2), "a publishable flag that the conditions contradict is a problem")
        r3 = result_of(d, self.conditions(), [{**ok_record(), "status": "mystery"}])
        self.assertTrue(d.validate_result(r3))

    def test_a_quiet_dedicated_run_is_written(self):
        d = self.d
        r = result_of(d, self.conditions(), [ok_record(), bad_record(), bad_record("refused", "csvtk@1")])
        d.write_published_table(r, self.out)
        text = self.out.read_text()
        self.assertIn("0.5", text)
        self.assertIn("minimum of 5", text)
        self.assertIn("i7-1260P", text)  # the conditions are next to the table
        self.assertIn("wrong-answer", text)  # a failed contender is listed with its status
        self.assertIn("refused", text)

    def test_a_loaded_run_is_refused_and_writes_nothing(self):
        d = self.d
        r = result_of(d, self.conditions(loadavg=(12.8, 16.4, 17.7)), [ok_record()])
        self.assertFalse(r["publishable"])
        with self.assertRaises(d.Refusal) as cm:
            d.write_published_table(r, self.out)
        self.assertEqual(cm.exception.rule, "bench.not-publishable")
        self.assertIn("loaded", cm.exception.message)
        self.assertFalse(self.out.exists())

    def test_a_virtual_machine_is_refused_whatever_the_flag_says(self):
        d = self.d
        r = result_of(d, self.conditions(virtualised=(True, "hypervisor flag")), [ok_record()])
        r["publishable"] = True  # a doctored file
        r["publishable_reasons"] = []
        with self.assertRaises(d.Refusal) as cm:
            d.write_published_table(r, self.out)
        self.assertEqual(cm.exception.rule, "bench.not-publishable")
        self.assertFalse(self.out.exists())

    def test_a_result_without_its_conditions_is_refused(self):
        d = self.d
        c = self.conditions()
        del c["file"]["sha256"]
        r = result_of(d, c, [ok_record()])
        r["publishable"] = True
        with self.assertRaises(d.Refusal) as cm:
            d.write_published_table(r, self.out)
        self.assertEqual(cm.exception.rule, "bench.conditions-missing")
        self.assertFalse(self.out.exists())

    def test_a_result_with_a_time_on_a_failed_contender_is_refused(self):
        d = self.d
        timed_bad = bad_record()
        timed_bad["timing"] = {"runs": 5, "min_s": 0.1, "median_s": 0.1, "first_s": 0.1, "all_s": [0.1] * 5, "warmup_s": 0.1}
        r = result_of(d, self.conditions(), [timed_bad])
        with self.assertRaises(d.Refusal) as cm:
            d.write_published_table(r, self.out)
        self.assertEqual(cm.exception.rule, "bench.result-invalid")
        self.assertFalse(self.out.exists())

    def test_fewer_than_five_runs_is_not_a_published_table(self):
        d = self.d
        r = result_of(d, d.collect_conditions(FakeProbes(), run_info(runs=3)), [ok_record()])
        with self.assertRaises(d.Refusal):
            d.write_published_table(r, self.out)


# ---------------------------------------------------------------------------------------------------------------- the driver as a program

STUB = r'''
import os, random, sys
sys.path.insert(0, %(scripts)r)
import bench_named_ref as ref
qid, path = sys.argv[1], sys.argv[2]
mode = os.environ.get("STUB_MODE", "good")
if mode == "hang":
    import time
    time.sleep(600)
if mode == "refuse":
    print('{"ok":false,"error":{"rule":"limit.too-many-groups"},"errors":[{"rule":"limit.too-many-groups"}]}')
    sys.exit(8)
if mode == "crash":
    os.kill(os.getpid(), 11)
if mode == "garbage":
    rng = random.Random(7)
    sys.stdout.buffer.write(bytes(rng.randrange(256) for _ in range(5000)))
    sys.exit(0)
entry = ref.reference(path)[qid]
rows = [list(r) for r in entry["rows"]]
if mode == "wrong-value":
    c = rows[2][-1]
    rows[2][-1] = c[:-1] + chr((ord(c[-1]) - 47) %% 10 + 48)
elif mode == "drop-row":
    rows = rows[:-1]
elif mode == "reorder":
    rows = list(reversed(rows))
elif mode == "float":
    rows[0][-1] = rows[0][-1] + "0"
out = "\n".join([",".join(entry["columns"])] + [",".join(r) for r in rows]) + "\n"
if mode == "trailing":
    out += "\n"
sys.stdout.write(out)
'''


class Program(unittest.TestCase):
    """The driver run as a program, on a 20,000-row file, with the real `table` and stub contenders; no time is read as a result."""

    def setUp(self):
        driver()  # the script must exist for any of these tests to mean anything

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="named-nb2-prog-")
        cls.dir = pathlib.Path(cls.tmp.name)
        cls.data = cls.dir / "data"
        cls.stub = cls.dir / "stub.py"
        cls.stub.write_text(STUB % {"scripts": str(SCRIPTS)})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, *args, env=None, results=None, timeout=300):
        results = results or (self.dir / ("r%d.json" % len(list(self.dir.glob("r*.json")))))
        e = dict(os.environ)
        e.update(env or {})
        e.pop("NAMED_DATA", None)
        p = subprocess.run([sys.executable, str(DRIVER), "--rows", "2e4", "--out", str(self.data), "--results", str(results), "--table", TABLE, *args],
                           capture_output=True, text=True, timeout=timeout, env=e)
        return p, results

    def load(self, results):
        return json.loads(pathlib.Path(results).read_text())

    def test_the_script_exists_and_only_starts_children_through_procs(self):
        driver()
        text = DRIVER.read_text()
        self.assertIn("import procs as subprocess", text)
        self.assertLessEqual(len(text.splitlines()), 2000)

    def test_a_run_with_table_records_every_condition_and_gates_before_it_times(self):
        p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q1,q3", "--threads", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.load(results)
        d = driver()
        self.assertEqual(d.validate_result(r), [])
        self.assertEqual(d.missing_conditions(r["conditions"]), [])
        recs = [x for x in r["results"] if x["question"] in ("q1", "q3")]
        self.assertEqual({(x["question"], x["contender"]) for x in recs}, {(q, c) for q in ("q1", "q3") for c in ("table@1", "table@2")})
        for x in recs:
            self.assertEqual(x["status"], "ok", x)
            self.assertEqual(x["timing"]["runs"], 2)
            self.assertEqual(len(x["timing"]["all_s"]), 2)
            self.assertEqual(x["answer"]["match"], "exact")
            self.assertTrue(x["peak_rss_kb"] > 0)
        # q6 to q9 are listed as not expressible, with their probe confirmed
        listed = {x["question"]: x for x in r["results"] if x["status"] == "not-expressible"}
        self.assertEqual(sorted(listed), ["q6", "q7", "q8", "q9"])
        self.assertEqual(r["conditions"]["runs"], 2)
        self.assertEqual(r["publishable"], r["publishable_reasons"] == [])
        self.assertFalse(r["publishable"], "two runs are fewer than the five of the method")
        self.assertIn("runs", " ".join(r["publishable_reasons"]))
        self.assertIn("load", r["conditions"])
        self.assertEqual(len(r["conditions"]["load"]["end"]), 3)
        # for each question, the printed times come after every answer of the question
        lines = p.stdout.splitlines()
        for qid in ("q1", "q3"):
            answers = [i for i, l in enumerate(lines) if l.startswith("answer  %s  " % qid)]
            times = [i for i, l in enumerate(lines) if l.startswith("time  %s  " % qid)]
            self.assertTrue(answers and times, qid)
            self.assertLess(max(answers), min(times))
        self.assertIn("publishable: false", p.stdout)

    def test_the_form_of_the_query_is_the_form_of_the_flags(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q1,q4,q5")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for x in self.load(results)["results"]:
            if x["family"] == "table" and x["status"] == "ok":
                self.assertTrue(x["forms_equal"], x)

    def test_g_answer_end_to_end_each_wrong_stub_is_reported_and_not_timed(self):
        for mode in ("wrong-value", "drop-row", "trailing", "float", "garbage"):
            with self.subTest(mode):
                p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q4", "--threads", "1", "--external", "stub=exact=%s" % self.stub, env={"STUB_MODE": mode})
                r = self.load(results)
                (x,) = [x for x in r["results"] if x["contender"] == "stub" and x["question"] == "q4"]
                self.assertEqual(x["status"], "wrong-answer", x)
                self.assertNotIn("timing", x)
                self.assertNotEqual(p.returncode, 0, "a wrong answer fails the run")
                self.assertNotIn("Traceback", p.stdout + p.stderr)
                self.assertFalse([l for l in p.stdout.splitlines() if l.startswith("time") and "stub" in l])
                (t,) = [x for x in r["results"] if x["contender"] == "table@1" and x["question"] == "q4"]
                self.assertEqual(t["status"], "ok")

    def test_a_reordered_groupby_answer_is_the_same_answer(self):
        p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q4", "--threads", "1", "--external", "stub=exact=%s" % self.stub, env={"STUB_MODE": "reorder"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
        self.assertEqual(x["status"], "ok")

    def test_g_refusal_end_to_end(self):
        for mode, status in (("refuse", "refused"), ("crash", "crashed")):
            with self.subTest(mode):
                p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q1", "--threads", "1", "--external", "stub=exact=%s" % self.stub, env={"STUB_MODE": mode})
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)  # a refusal is a result, not a failure of the run
                (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
                self.assertEqual(x["status"], status)
                self.assertNotIn("timing", x)
                self.assertTrue(x["reason"])

    def test_g_refusal_a_timeout_is_recorded_without_a_time(self):
        p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q1", "--threads", "1", "--timeout", "2", "--external", "stub=exact=%s" % self.stub, env={"STUB_MODE": "hang"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
        self.assertEqual(x["status"], "timeout")
        self.assertIn("over 2 s", x["reason"])
        self.assertNotIn("timing", x)

    def test_a_reused_file_is_checked_against_its_sha256(self):
        data = self.dir / "reuse"
        args = ["--runs", "1", "--contenders", "table", "--questions", "q1", "--threads", "1", "--no-scan-probe"]
        p, results = self.drive(*args, "--out", str(data))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("reused", p.stdout)
        sha = self.load(results)["conditions"]["file"]["sha256"]
        p, results = self.drive(*args, "--out", str(data))
        self.assertIn("reused, sha256 checked", p.stdout)
        self.assertEqual(self.load(results)["conditions"]["file"]["sha256"], sha)
        # one byte changed, the size the same: the file is not the one its metadata describes, so it is written again
        f = data / gen.name_of(20_000, 100)
        raw = bytearray(f.read_bytes())
        raw[-2] = ord("0") if raw[-2] != ord("0") else ord("1")
        f.write_bytes(bytes(raw))
        p, results = self.drive(*args, "--out", str(data))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("reused", p.stdout)
        self.assertEqual(self.load(results)["conditions"]["file"]["sha256"], sha)
        self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), sha)

    def test_a_skipped_contender_is_not_launched_and_says_why(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q4", "--threads", "1", "--external", "stub=exact=%s" % self.stub, "--skip", "q4:stub=out of memory at 1e5",
                                env={"STUB_MODE": "crash"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
        self.assertEqual(x["status"], "not-run")
        self.assertIn("out of memory at 1e5", x["reason"])
        self.assertNotIn("timing", x)
        p, _ = self.drive("--skip", "q4")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("bench.skip", p.stdout)

    def test_a_pair_that_timed_out_at_a_smaller_step_is_dropped_from_the_next(self):
        prior = self.dir / "prior.json"
        prior.write_text(json.dumps({"dropped": [["q4", "stub"]]}))
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q4", "--threads", "1", "--external", "stub=exact=%s" % self.stub, "--drop-from", str(prior),
                                "--timeout", "2", env={"STUB_MODE": "hang"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
        self.assertEqual(x["status"], "not-run")
        self.assertIn("over 2 s", x["reason"])
        # and a timeout in this run is written down for the next step
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q4", "--threads", "1", "--external", "stub=exact=%s" % self.stub, "--timeout", "2", env={"STUB_MODE": "hang"})
        self.assertIn(["q4", "stub"], self.load(results)["dropped"])

    def test_g_repro_the_same_seed_gives_the_same_file(self):
        shas = []
        for seed, sub in (("108", "a"), ("108", "b"), ("109", "c")):
            data = self.dir / ("repro-" + sub)
            results = self.dir / ("repro-%s.json" % sub)
            p = subprocess.run([sys.executable, str(DRIVER), "--rows", "2e4", "--seed", seed, "--out", str(data), "--results", str(results), "--table", TABLE, "--runs", "1",
                                "--contenders", "table", "--questions", "q1", "--threads", "1"], capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            r = self.load(results)
            shas.append(r["conditions"]["file"]["sha256"])
            self.assertEqual(r["conditions"]["seed"], int(seed))
        self.assertEqual(shas[0], shas[1])
        self.assertNotEqual(shas[0], shas[2])

    def test_the_large_steps_need_an_explicit_opt_in_and_print_the_estimate_first(self):
        data = self.dir / "never"
        for rows in ("1e7", "1e8"):
            p = subprocess.run([sys.executable, str(DRIVER), "--rows", rows, "--out", str(data), "--results", str(self.dir / "never.json"), "--table", TABLE], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn("bench.large-size", p.stdout + p.stderr)
            self.assertIn("--accept-large", p.stdout + p.stderr)
            self.assertFalse(data.exists(), "nothing is generated before the opt-in")
        p = subprocess.run([sys.executable, str(DRIVER), "--rows", "1e8", "--estimate", "--out", str(data), "--table", TABLE], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(str(gen.estimate_bytes(100_000_000, 100)), p.stdout)
        self.assertIn("launches", p.stdout)
        self.assertFalse(data.exists())

    def test_a_data_directory_inside_the_repository_is_refused(self):
        p = subprocess.run([sys.executable, str(DRIVER), "--rows", "2e4", "--out", str(ROOT / "build" / "named-nb2-test"), "--results", str(self.dir / "x.json"), "--table", TABLE],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("bench.inside-repo", p.stdout + p.stderr)
        self.assertFalse((ROOT / "build" / "named-nb2-test").exists())

    def test_not_enough_disk_is_refused_before_generating(self):
        p = subprocess.run([sys.executable, str(DRIVER), "--rows", "2e4", "--out", str(self.dir / "tiny"), "--results", str(self.dir / "x.json"), "--table", TABLE],
                           capture_output=True, text=True, timeout=60, env={**os.environ, "BENCH_NAMED_FREE_BYTES": "1000"})
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("bench.disk", p.stdout + p.stderr)
        self.assertFalse((self.dir / "tiny").exists() and any((self.dir / "tiny").iterdir()))

    def test_arguments_that_make_no_step_are_refused_with_a_tag(self):
        for args in (["--runs", "0"], ["--timeout", "0"], ["--k", "7"], ["--rows", "abc"], ["--rows", "-1"], ["--contenders", "polars"], ["--questions", "q6"], ["--questions", "q99"],
                     ["--external", "x=fast=/bin/true"], ["--taskset", "0", "--table", "/nonexistent/table"]):
            with self.subTest(args):
                p = subprocess.run([sys.executable, str(DRIVER), "--out", str(self.dir / "args"), "--results", str(self.dir / "args.json"), "--table", TABLE, *args], capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
                self.assertIn('"rule": "bench.', p.stdout)
                self.assertNotIn("Traceback", p.stdout + p.stderr)

    def test_a_suite_that_is_not_built_is_refused(self):
        for suite in ("join", "1brc", "clickbench"):
            p = subprocess.run([sys.executable, str(DRIVER), "--suite", suite], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn("bench.suite-not-built", p.stdout + p.stderr)

    def test_the_writer_of_the_published_table_refuses_this_run_if_it_is_not_publishable(self):
        out = self.dir / "published.md"
        p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q1", "--threads", "1", "--write-table", str(out))
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)  # two runs: never publishable
        self.assertIn("bench.not-publishable", p.stdout + p.stderr)
        self.assertFalse(out.exists())
        self.assertTrue(pathlib.Path(results).exists(), "the result is still recorded")
        again = subprocess.run([sys.executable, str(DRIVER), "--from-result", str(results), "--write-table", str(out)], capture_output=True, text=True, timeout=60)
        self.assertEqual(again.returncode, 3, again.stdout + again.stderr)
        self.assertFalse(out.exists())

    def test_the_scan_probe_is_reported(self):
        p, results = self.drive("--scan-probe-only")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.load(results)
        probe = r["scan_probe"]
        self.assertEqual(probe["rows"], 20000)
        self.assertEqual([x["name"] for x in probe["scans"]], ["count", "select"])
        for s in probe["scans"]:
            self.assertEqual(s["exit"], 0, s)
        self.assertEqual(probe["scans"][1]["rows_out"], 20000)
        self.assertIn("scan", p.stdout)
        self.assertFalse([x for x in r.get("results", [])], "the probe times nothing")

    def test_the_step_reports_real_sizes_and_its_wall_time(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q1", "--threads", "1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.load(results)
        step = r["step"]
        self.assertEqual(step["file_bytes"], r["conditions"]["file"]["bytes"])
        self.assertEqual(step["estimated_bytes"], gen.estimate_bytes(20_000, 100))
        self.assertGreater(step["wall_s"], 0)
        self.assertIn("bytes", p.stdout)
        self.assertIn("step", p.stdout)

    def test_a_missing_contender_is_not_run_with_its_reason_and_never_faked(self):
        p, results = self.drive("--runs", "1", "--contenders", "table,csvtk", "--questions", "q1", "--threads", "1", env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.load(results)
        nr = {x["contender"]: x for x in r["not_run"]}
        self.assertIn("csvtk", nr)
        self.assertTrue(nr["csvtk"]["reason"])
        self.assertFalse([x for x in r["results"] if x["family"] == "csvtk"])
        self.assertIn("NOT RUN", p.stdout)


# ---------------------------------------------------------------------------------------------------------------- G-forms

class Forms(Shared):
    """Every contender's form for every question, run for real at 20,000 rows and held to the reference: the contenders installed; a missing one is skipped with its reason."""

    QUESTIONS = ("q1", "q2", "q3", "q4", "q5")

    def setUp(self):
        self.d = driver()
        self.exact = self.d.exact_means(self.path)
        self.ref10 = ref.reference(self.path, with_q10=True)

    def contenders(self, family):
        d = self.d
        tools = d.find_tools(table=TABLE)
        found = [c for c in d.build_contenders(tools, threads_many=4, wanted=[family]) if c.family == family]
        if not found:
            self.skipTest("%s is not installed here (%s): its forms are not validated by this run" % (family, d.why_missing(tools, family)))
        return found

    def check(self, family):
        d = self.d
        for c in self.contenders(family):
            for qid in self.QUESTIONS + ("q10",):
                with self.subTest(contender=c.name, question=qid):
                    form = c.form(qid, str(self.path))
                    out = d.real_launch(form.answer, env=form.env, capture=True, timeout=120)
                    cls = d.classify(out.returncode, out.stdout, out.stderr, is_table=(family == "table"), timed_out=out.timed_out)
                    self.assertEqual(cls["status"], "ok", (cls, out.stderr[:300]))
                    entry = self.ref10[qid]
                    v = d.compare_answer(BY_ID[qid], entry, out.stdout, c.profile, exact=self.exact.get(qid))
                    self.assertTrue(v["ok"], v)

    def test_table(self):
        self.check("table")

    def test_duckdb(self):
        self.check("duckdb")

    def test_csvtk(self):
        self.check("csvtk")

    def test_miller(self):
        self.check("mlr")

    def test_pandas(self):
        self.check("pandas")

    def test_the_timed_form_of_every_contender_writes_the_same_answer_to_the_sink(self):
        # the timed run goes to /dev/null: its argv must be the answer's argv with the sink changed, nothing else (DuckDB's COPY ... TO '/dev/null')
        for family in ("table", "duckdb", "csvtk", "mlr", "pandas"):
            tools = self.d.find_tools(table=TABLE)
            for c in self.d.build_contenders(tools, threads_many=4, wanted=[family]):
                if c.family != family:
                    continue
                form = c.form("q1", str(self.path))
                if family == "duckdb":
                    self.assertEqual(" ".join(form.answer).replace("/dev/stdout", "/dev/null"), " ".join(form.timed))
                else:
                    self.assertEqual(form.answer, form.timed)

    def test_duckdb_python_module_when_there_is_no_command(self):
        d = self.d
        tools = d.find_tools(table=TABLE, path="/nonexistent")
        if not tools["duckdb"]:
            self.skipTest("the Python module duckdb is not importable here: the module form is not validated by this run")
        self.assertEqual(tools["duckdb"][0], "python")
        for c in d.build_contenders(tools, threads_many=4, wanted=["duckdb"]):
            self.assertIn("python duckdb", c.version)
            for qid in self.QUESTIONS + ("q10",):
                with self.subTest(contender=c.name, question=qid):
                    form = c.form(qid, str(self.path))
                    out = d.real_launch(form.answer, env=form.env, capture=True, timeout=120)
                    self.assertEqual(out.returncode, 0, out.stderr[:300])
                    self.assertTrue(d.compare_answer(BY_ID[qid], self.ref10[qid], out.stdout, c.profile, exact=self.exact.get(qid))["ok"])

    def test_the_pins_are_recorded(self):
        d = self.d
        self.assertEqual(d.PINS["duckdb"], "1.5.6")
        self.assertEqual(d.PINS["csvtk"], "0.38.0")
        self.assertEqual(d.PINS["mlr"], "6.22.0")
        self.assertEqual(d.PINS["pandas"], "3.0.3")
        self.assertEqual(d.pin_status("duckdb", "v1.5.6 (Variegata) 069cc9f9b5")["pinned"], True)
        self.assertEqual(d.pin_status("mlr", "mlr 6.22.0")["pinned"], True)
        self.assertEqual(d.pin_status("csvtk", "csvtk v0.38.0")["pinned"], True)
        s = d.pin_status("pandas", "3.0.5")
        self.assertEqual((s["pinned"], s["pin"]), (False, "3.0.3"))
        self.assertIn("3.0.5", s["note"])

    def test_the_resources_of_a_run_are_measured(self):
        d = self.d
        r = d.real_rusage([sys.executable, "-c", "x = b'a' * (60 * 1024 * 1024); print(len(x))"], env=None, timeout=60)
        self.assertGreater(r["peak_rss_kb"], 50 * 1024)
        self.assertGreaterEqual(r["cpu_s"], 0.0)
        self.assertIsNone(d.real_rusage([sys.executable, "-c", "import sys; sys.exit(3)"], env=None, timeout=60))


class RealLaunch(unittest.TestCase):
    """real_launch with an injected `run` and `clock`: the time is the clock's difference around the run, whatever the run does."""

    def setUp(self):
        self.d = driver()

    def clock(self, *values):
        it = iter(values)
        return lambda: next(it)

    def test_the_time_is_the_clocks_difference(self):
        d = self.d
        seen = {}

        def run(argv, **kw):
            seen.update(kw, argv=argv)
            return subprocess.CompletedProcess(argv, 0, b"out", b"err")

        o = d.real_launch(["x", "y"], env=None, capture=True, timeout=7, run=run, clock=self.clock(10.0, 12.5))
        self.assertEqual((o.returncode, o.seconds, o.stdout, o.stderr, o.timed_out), (0, 2.5, b"out", b"err", False))
        self.assertEqual((seen["timeout"], seen["stdout"], seen["stderr"], seen["stdin"]), (7, subprocess.PIPE, subprocess.PIPE, subprocess.DEVNULL))
        self.assertIsNone(seen["env"])

    def test_a_timed_run_discards_its_output(self):
        d = self.d
        seen = {}

        def run(argv, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(argv, 0, None, None)

        o = d.real_launch(["x"], env={"A": "1"}, capture=False, timeout=7, run=run, clock=self.clock(0.0, 1.0))
        self.assertEqual((seen["stdout"], seen["stderr"]), (subprocess.DEVNULL, subprocess.DEVNULL))
        self.assertEqual(seen["env"]["A"], "1")
        self.assertIn("PATH", seen["env"])  # the environment is the process's, plus the form's
        self.assertEqual((o.stdout, o.stderr), (b"", b""))

    def test_a_timeout_and_a_missing_program_are_outcomes_not_exceptions(self):
        d = self.d

        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])

        o = d.real_launch(["x"], run=slow, clock=self.clock(1.0, 4.0), timeout=3)
        self.assertEqual((o.timed_out, o.returncode, o.seconds), (True, None, 3.0))

        def missing(argv, **kw):
            raise FileNotFoundError("no such program")

        o = d.real_launch(["x"], run=missing, clock=self.clock(1.0, 1.5))
        self.assertEqual((o.timed_out, o.returncode, o.seconds), (False, 127, 0.5))
        self.assertIn(b"no such program", o.stderr)
        self.assertEqual(d.classify(o.returncode, o.stdout, o.stderr, False, o.timed_out)["status"], "refused")


class FakeRun:
    """A stand-in for the module's _run_text: commands map to (returncode, stdout, stderr)."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, argv, timeout=15):
        self.calls.append(argv)
        return self.table.get(argv[0], (None, "", "not found"))


class MachineProbes(unittest.TestCase):
    """The probes that decide publishable, against what the machine would say."""

    def setUp(self):
        self.d = driver()
        self.tmp = tempfile.TemporaryDirectory(prefix="named-nb2-probe-")
        self.saved = (self.d.CPUINFO, self.d._run_text, self.d.shutil.which, self.d.platform.system)

    def tearDown(self):
        self.d.CPUINFO, self.d._run_text, self.d.shutil.which, self.d.platform.system = self.saved
        self.tmp.cleanup()

    def cpuinfo(self, flags_line):
        p = pathlib.Path(self.tmp.name) / "cpuinfo"
        p.write_text("processor\t: 0\nmodel name\t: Some CPU @ 3.00GHz\n%s\n" % flags_line)
        self.d.CPUINFO = p

    def test_the_hypervisor_flag_makes_it_a_virtual_machine(self):
        self.d._run_text = FakeRun({})
        self.cpuinfo("flags\t\t: fpu vme sse2 hypervisor lahf_lm")
        v, why = self.d.Probes().virtualised()
        self.assertIs(v, True)
        self.assertIn("hypervisor", why)
        self.cpuinfo("flags\t\t: fpu vme sse2 lahf_lm")
        v, why = self.d.Probes().virtualised()
        self.assertIs(v, False)
        self.cpuinfo("flags\t\t: fpu vme nothypervisorflag")  # a word that merely contains it is not the flag
        self.assertIs(self.d.Probes().virtualised()[0], False)

    def test_without_flags_the_other_witnesses_are_asked_and_then_it_is_unknown(self):
        self.d.CPUINFO = pathlib.Path(self.tmp.name) / "no-such-file"
        self.d.platform.system = lambda: "Darwin"
        self.d._run_text = FakeRun({"sysctl": (0, "1\n", "")})
        self.assertEqual(self.d.Probes().virtualised()[0], True)
        self.d._run_text = FakeRun({"sysctl": (0, "0\n", "")})
        self.assertEqual(self.d.Probes().virtualised()[0], False)
        self.d.platform.system = lambda: "Linux"
        self.d._run_text = FakeRun({"systemd-detect-virt": (0, "kvm\n", "")})
        self.assertEqual(self.d.Probes().virtualised()[0], True)
        self.d._run_text = FakeRun({"systemd-detect-virt": (1, "none\n", "")})
        self.assertEqual(self.d.Probes().virtualised()[0], False)
        self.d._run_text = FakeRun({})
        v, why = self.d.Probes().virtualised()
        self.assertIsNone(v)
        self.assertIn("no way to tell", why)

    def test_docker(self):
        d = self.d
        d.shutil.which = lambda name, **kw: None
        self.assertEqual(d.Probes().docker()["status"], "not-installed")
        d.shutil.which = lambda name, **kw: "/usr/bin/docker"
        d._run_text = FakeRun({"docker": (0, "soak-1\nweb\n", "")})
        r = d.Probes().docker()
        self.assertEqual((r["status"], r["containers"]), ("ok", ["soak-1", "web"]))
        d._run_text = FakeRun({"docker": (0, "", "")})
        self.assertEqual(d.Probes().docker()["containers"], [])
        d._run_text = FakeRun({"docker": (1, "", "Cannot connect to the Docker daemon\nsecond line")})
        r = d.Probes().docker()
        self.assertEqual((r["status"], r["containers"]), ("error", []))
        self.assertIn("Cannot connect", r["detail"])
        self.assertNotIn("second line", r["detail"])

    def test_soak_is_pgrep_without_the_driver_and_pgrep_itself(self):
        d = self.d
        d._run_text = FakeRun({"pgrep": (0, "101 /bin/sh soak.sh\n202 python3 scripts/bench_named.py --note soak\n303 pgrep -af soak\n", "")})
        r = d.Probes().soak()
        self.assertEqual(r["found"], True)
        self.assertEqual(r["lines"], ["101 /bin/sh soak.sh"])
        d._run_text = FakeRun({"pgrep": (1, "", "")})
        self.assertEqual(d.Probes().soak()["found"], False)
        d._run_text = FakeRun({})
        r = d.Probes().soak()
        self.assertEqual(r["found"], False)
        self.assertIn("not available", r["detail"])

    def test_uptime_is_recorded_as_it_is_said(self):
        d = self.d
        d._run_text = FakeRun({"uptime": (0, " 12:10:57 up 5:16,  0 user,  load average: 0.34, 0.25, 0.10\n", "")})
        self.assertEqual(d.Probes().uptime(), "12:10:57 up 5:16,  0 user,  load average: 0.34, 0.25, 0.10")
        d._run_text = FakeRun({})
        self.assertIn("not available", d.Probes().uptime())


class MoreAnswerGate(Shared):
    """Cases the first mutant runs showed were not held."""

    def test_a_numeric_key_must_be_the_same_text(self):
        d = driver()
        rows = [list(r) for r in self.ref["q4"]["rows"]]
        rows[0][0] = rows[0][0] + ".0"  # the key 1 as 1.0
        v = d.compare_answer(BY_ID["q4"], self.ref["q4"], answer_bytes(self.ref["q4"]["columns"], rows), "double", exact=d.exact_means(self.path)["q4"])
        self.assertFalse(v["ok"])

    def test_a_mean_without_its_exact_value_is_not_taken_on_trust(self):
        d = driver()
        rows = [list(r) for r in self.ref["q4"]["rows"]]
        rows[0][1] = rows[0][1][:-1] + str((int(rows[0][1][-1]) + 1) % 10)  # a different value: only the exact mean could say if it is within a billionth
        v = d.compare_answer(BY_ID["q4"], self.ref["q4"], answer_bytes(self.ref["q4"]["columns"], rows), "double", exact=None)
        self.assertFalse(v["ok"])
        self.assertIn("exact mean", v["problem"])

    def test_a_huge_exponent_is_refused_not_computed(self):
        d = driver()
        rows = [list(r) for r in self.ref["q1"]["rows"]]
        rows[0][1] = "1e999999999"
        self.assertFalse(d.compare_answer(BY_ID["q1"], self.ref["q1"], answer_bytes(self.ref["q1"]["columns"], rows), "double")["ok"])
        rows[0][1] = "9" * 70
        self.assertFalse(d.compare_answer(BY_ID["q1"], self.ref["q1"], answer_bytes(self.ref["q1"]["columns"], rows), "double")["ok"])

    def test_a_decimal_sum_is_held_to_its_exact_value_by_tolerance(self):
        d = driver()
        rows = [list(r) for r in self.ref["q5"]["rows"]]
        truth = Fraction(Decimal(rows[3][3]))
        for factor, ok in ((Fraction(1, 10**10), True), (Fraction(5, 10**9), False)):
            variant = [list(r) for r in rows]
            variant[3][3] = "%.17g" % float(truth * (1 + factor))
            v = d.compare_answer(BY_ID["q5"], self.ref["q5"], answer_bytes(self.ref["q5"]["columns"], variant), "double")
            self.assertEqual((v["ok"], v["level"]), (ok, "tolerance" if ok else None), v)

    def test_a_nul_byte_in_a_header_a_double_engine_prints_is_refused(self):
        d = driver()
        raw = self.good_q1().replace(b"id1,", b"id1\x00,", 1)
        self.assertFalse(d.compare_answer(BY_ID["q1"], self.ref["q1"], raw, "double")["ok"])
        self.assertFalse(d.compare_answer(BY_ID["q1"], self.ref["q1"], self.good_q1().replace(b"\n", b"\n\n", 1), "double")["ok"])

    def good_q1(self):
        return answer_bytes(self.ref["q1"]["columns"], self.ref["q1"]["rows"])

    def test_the_exact_means_are_checked_against_the_reference(self):
        d = driver()
        ex = d.exact_means(self.path)
        self.assertIsNone(d.check_exact_against_reference(ex, self.ref))
        bad = {k: dict(v) for k, v in ex.items()}
        key = next(iter(bad["q3"]))
        bad["q3"][key] = (bad["q3"][key][0] + Fraction(1, 10**6),)
        self.assertIn("q3", d.check_exact_against_reference(bad, self.ref))
        short = {k: dict(v) for k, v in ex.items()}
        del short["q4"][next(iter(short["q4"]))]
        self.assertIn("q4", d.check_exact_against_reference(short, self.ref))

    def test_a_mean_is_taken_from_the_one_questions_part_or_from_the_whole(self):
        d = driver()
        ex = d.exact_means(self.path)
        rows = [list(r) for r in self.ref["q3"]["rows"]]
        key = (rows[0][0],)
        rows[0][2] = "%.17g" % float(ex["q3"][key][0])
        raw = answer_bytes(self.ref["q3"]["columns"], rows)
        self.assertTrue(d.compare_answer(BY_ID["q3"], self.ref["q3"], raw, "double", exact=ex)["ok"])
        self.assertTrue(d.compare_answer(BY_ID["q3"], self.ref["q3"], raw, "double", exact=ex["q3"])["ok"])


class MoreOrchestration(Shared):
    def setUp(self):
        self.d = driver()
        self.fake = Fake(self.d, self.ref["q1"])

    def test_the_two_forms_of_table_must_give_the_same_bytes(self):
        d = self.d
        calls = []
        entry = self.ref["q1"]
        good = answer_bytes(entry["columns"], entry["rows"])
        other = answer_bytes(entry["columns"], list(reversed(entry["rows"])))

        def launch(argv, env=None, capture=True, timeout=120):
            calls.append(argv[-1])
            return d.Outcome(0, 0.1, other if argv[-1] == "query" else good, b"", False)

        def form(qid, file):
            return d.Form(["t", "flags"], ["t", "flags", "timed"], {}, ["t", "query"])

        c = d.Contender(name="table@1", family="table", threads="1", profile="exact", version="0.3.0", form=form)
        ctx = d.Context(launch=launch, rusage=lambda *a, **k: {"peak_rss_kb": 1, "cpu_s": 0.1}, emit=self.fake.lines.append, runs=2, timeout=120, file="F", ref={"q1": entry}, exact={}, table=None)
        (r,) = d.run_question(ctx, BY_ID["q1"], [c])
        self.assertEqual(r["status"], "wrong-answer")
        self.assertIs(r["forms_equal"], False)
        self.assertNotIn("timing", r)
        self.assertNotIn("answer", r)
        self.assertNotIn("timed", calls)
        # the same bytes: timed, and recorded
        other = good
        calls.clear()
        ctx.emit = self.fake.lines.append
        (r,) = d.run_question(ctx, BY_ID["q1"], [c])
        self.assertEqual(r["status"], "ok")
        self.assertIs(r["forms_equal"], True)

    def test_an_operator_skip_by_name_or_family(self):
        d = self.d
        ctx = self.fake.context()
        ctx.skip = {("q1", "slow"): "out of memory at 1e5", ("q1", "fake"): "all of the family"}
        recs = d.run_question(ctx, BY_ID["q1"], [self.fake.contender("slow"), self.fake.contender("other")])
        for r in recs:
            self.assertEqual(r["status"], "not-run")
            self.assertIn("skipped by the operator", r["reason"])
        self.assertIn("out of memory at 1e5", recs[0]["reason"])
        self.assertIn("all of the family", recs[1]["reason"])
        self.assertEqual(self.fake.calls, [])

    def test_a_failure_in_the_measured_run_loses_the_times(self):
        d = self.d
        ctx = self.fake.context()
        ctx.rusage = lambda argv, env=None, timeout=120: None
        recs = d.run_question(ctx, BY_ID["q1"], [self.fake.contender("good")])
        self.assertEqual(recs[0]["status"], "failed-while-timing")
        for key in ("timing", "peak_rss_kb", "cpu_s"):
            self.assertNotIn(key, recs[0])

    def test_a_timeout_while_timing_is_a_failure_with_the_limit(self):
        d = self.d
        base = self.fake.launch

        def launch(argv, env=None, capture=True, timeout=120):
            if argv[-1] == "timed":
                return d.Outcome(None, timeout, b"", b"", True)
            return base(argv, env=env, capture=capture, timeout=timeout)

        ctx = self.fake.context()
        ctx.launch = launch
        recs = d.run_question(ctx, BY_ID["q1"], [self.fake.contender("good")])
        self.assertEqual(recs[0]["status"], "failed-while-timing")
        self.assertIn("over 120 s", recs[0]["reason"])


class MoreConditions(unittest.TestCase):
    def test_the_load_line_is_a_quarter_of_the_machines_cores_not_the_usable_ones(self):
        d = driver()
        c = d.collect_conditions(FakeProbes(cpu_count=16, affinity=4, loadavg=(2.0, 0.0, 0.0)), run_info())
        self.assertTrue(d.evaluate_publishable(c)[0])
        c = d.collect_conditions(FakeProbes(cpu_count=16, affinity=4, loadavg=(4.5, 0.0, 0.0)), run_info())
        self.assertFalse(d.evaluate_publishable(c)[0])

    def test_incomplete_conditions_are_never_publishable(self):
        d = driver()
        c = d.collect_conditions(FakeProbes(), run_info())
        del c["file"]["sha256"]
        ok, reasons = d.evaluate_publishable(c)
        self.assertFalse(ok)
        self.assertIn("file.sha256", reasons[0])

    def test_a_load_that_is_not_three_numbers_is_missing(self):
        d = driver()
        for bad in ([0.1], [0.1, 0.2], "0.1,0.2,0.3", [0.1, "x", 0.3], []):
            c = d.collect_conditions(FakeProbes(), run_info())
            c["load"]["start"] = bad
            with self.subTest(bad):
                self.assertEqual(d.missing_conditions(c), ["load.start"])

    def test_the_snapshot_holds_the_values_of_the_start(self):
        d = driver()
        p = FakeProbes(loadavg=(0.5, 0.5, 0.5))
        snap = d.Snapshot(p)
        p.over["loadavg"] = (9.0, 9.0, 9.0)
        self.assertEqual(snap.loadavg(), (0.5, 0.5, 0.5))
        self.assertEqual(snap.cpu_count(), 16)


class MutantsScript(unittest.TestCase):
    def test_the_mutants_still_apply(self):
        script = SCRIPTS / "bench_named_mutants.py"
        if not script.exists():
            raise AssertionError("scripts/bench_named_mutants.py does not exist: NB2 is not built")
        got = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
        self.assertEqual((got.returncode, got.stdout.count("!!")), (0, 0), got.stdout)
        count = int(got.stdout.split()[0])
        self.assertGreaterEqual(count, 25)


if __name__ == "__main__":
    unittest.main()
