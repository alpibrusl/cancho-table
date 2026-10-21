"""NB3 of docs/named-benchmarks.md: the join suite of the timing driver (scripts/bench_named_join.py, run as `scripts/bench_named.py --suite join`).

The gates of NB2 hold for a join as they do for a groupby, and are tested again here with joined rows in place of groups: G-answer (a wrong value, a swap of two rows' v2 that keeps every
total, a missing row, a trailing byte, garbage: no time; every answer judged before a time is printed), G-refusal (refused, crashed, timed out: a status, a reason, no time), G-record (the
conditions carry the four tables), G-publishable (the NB2 rules, unchanged), G-repro (the same seed, the same files), G-forms (every contender's form gives the reference's rows, for the
contenders installed; a missing one is skipped with its reason; `--join-sorted` gives the bytes of the hash join over the same sorted files), the refusal report (what `table` says at a bound,
with its facts and its repair, no clock), and the opt-in of the large steps.

No table above 20,000 rows, no network. The driver is a script, so the tool's authority row is untouched.
"""

import json
import os
import pathlib
import random
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCRIPTS = ROOT / "scripts"
DRIVER = SCRIPTS / "bench_named.py"
sys.path.insert(0, str(SCRIPTS))
import bench_named_gen as gen  # noqa: E402


def mods():
    for name in ("bench_named_join.py", "bench_named_join_gen.py", "bench_named_join_ref.py"):
        if not (SCRIPTS / name).exists():
            raise AssertionError("scripts/%s does not exist: NB3 is not built" % name)
    import bench_named as bn
    import bench_named_join as bj
    import bench_named_join_gen as jgen
    import bench_named_join_ref as jref
    return bn, bj, jgen, jref


TABLE = os.environ.get("TOOLBOX_BIN")
TABLE = str(pathlib.Path(TABLE) / "table") if TABLE else str(ROOT / "build" / "table")


def canonical(paths, jref, qid):
    q = jref.JOINS[qid]
    rt = pathlib.Path(paths[q["right"]]).read_text().splitlines()
    kat = rt[0].split(",").index(q["key"])
    right = {}
    for line in rt[1:]:
        c = line.split(",")
        right[c[kat]] = [v for i, v in enumerate(c) if i != kat]
    xt = pathlib.Path(paths["x"]).read_text().splitlines()
    xat = xt[0].split(",").index(q["key"])
    rows = []
    for line in xt[1:]:
        c = line.split(",")
        m = right.get(c[xat])
        if m is not None:
            rows.append(c + m)
        elif q["kind"] == "left":
            rows.append(c + [""] * len(q["right_cols"]))
    return rows


def csv_of(header, rows):
    return ("\n".join([",".join(header)] + [",".join(r) for r in rows]) + "\n").encode()


class Shared(unittest.TestCase):
    N = 2000

    missing = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.bn, cls.bj, cls.jgen, cls.jref = mods()
        except AssertionError as e:      # each test then fails by itself, with the reason (a missing script)
            cls.missing = e
            return
        cls.tmp = tempfile.TemporaryDirectory(prefix="named-nb3-drv-")
        cls.paths, cls.meta = cls.jgen.write(cls.N, cls.tmp.name)
        cls.ref = cls.jref.reference(cls.tmp.name, cls.N)
        cls.questions = {q["id"]: q for q in cls.jref.load_questions()["questions"]}

    @classmethod
    def tearDownClass(cls):
        if cls.missing is None:
            cls.tmp.cleanup()

    def setUp(self):
        if self.missing is not None:
            raise self.missing


class Fake:
    """A fake world for run_join_question: contenders are named programs whose launches return canned outcomes or write canned files; nothing is started and nothing is timed."""

    def __init__(self, test, qid="q1"):
        self.t = test
        self.bn, self.bj, self.jref = test.bn, test.bj, test.jref
        self.qid = qid
        self.modes = {}
        self.calls = []
        self.lines = []
        self.seconds = {}
        self.count = {}
        self.rows = canonical(test.paths, test.jref, qid)

    def contender(self, name, mode="good", profile="double", family="fake"):
        self.modes[name] = mode
        return self.bn.Contender(name=name, family=family, threads="1", profile=profile, version="fake 1",
                                 form=lambda qid, f, name=name: self.bn.Form(["fake", name, qid], ["fake", name, qid, "timed"], {}))

    def payload(self, mode):
        header = self.jref.joined_columns(self.qid)
        rows = [list(r) for r in self.rows]
        if mode == "wrong-value":
            rows[5][-1] = rows[5][-1][:-1] + str((int(rows[5][-1][-1]) + 1) % 10)
        elif mode == "swap":
            i, j = 3, 9
            rows[i][-1], rows[j][-1] = rows[j][-1], rows[i][-1]
        elif mode == "drop-row":
            rows = rows[:-1]
        elif mode == "reorder":
            rows = list(reversed(rows))
        raw = csv_of(header, rows)
        if mode == "trailing":
            raw += b"\n"
        if mode == "garbage":
            raw = bytes(random.Random(7).randrange(256) for _ in range(5000))
        return raw

    def launch_file(self, argv, env=None, dest=None, timeout=120):
        name = argv[1]
        mode = self.modes[name]
        self.calls.append((name, "answer"))
        if mode == "crash":
            return self.bn.Outcome(-11, 0.01, b"", b"", False)
        if mode == "refuse":
            pathlib.Path(dest).write_bytes(b"")
            return self.bn.Outcome(8, 0.01, b"", b"table: limit.state-too-large: the --join file is held in memory\n", False)
        if mode == "hang":
            return self.bn.Outcome(None, timeout, b"", b"", True)
        pathlib.Path(dest).write_bytes(self.payload("good" if mode in ("good", "fails-while-timing") else mode))
        return self.bn.Outcome(0, 0.5, b"", b"", False)

    def launch(self, argv, env=None, capture=True, timeout=120):
        name = argv[1]
        self.calls.append((name, "timed"))
        k = self.count.get(name, 0)
        self.count[name] = k + 1
        if self.modes[name] == "fails-while-timing" and k == 2:
            return self.bn.Outcome(1, 0.01, b"", b"boom", False)
        seq = self.seconds.get(name, [1.0] * 20)
        return self.bn.Outcome(0, seq[k % len(seq)], b"", b"", False)

    def rusage(self, argv, env=None, timeout=120):
        self.calls.append((argv[1], "rss"))
        return {"peak_rss_kb": 1234, "cpu_s": 0.75}

    def context(self, runs=3):
        ctx = self.bj.JoinContext(launch=self.launch, rusage=self.rusage, emit=self.lines.append, runs=runs, timeout=120, file="x", ref={}, exact={}, table=None,
                                  paths={t: str(p) for t, p in self.t.paths.items()}, reference=self.t.ref, tmp=self.t.tmp.name, launch_file=self.launch_file)
        return ctx


class Orchestration(Shared):
    def setUp(self):
        super().setUp()
        self.fake = Fake(self)
        self.q = self.questions["q1"]

    def run_q(self, contenders, runs=3):
        return self.bj.run_join_question(self.fake.context(runs), self.q, contenders)

    def rec(self, records, name):
        (r,) = [r for r in records if r["contender"] == name]
        return r

    def test_a_good_contender_is_timed_and_its_fingerprint_recorded(self):
        self.fake.seconds["good"] = [100.0, 5.0, 3.0, 4.0]
        r = self.rec(self.run_q([self.fake.contender("good")]), "good")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["timing"]["all_s"], [5.0, 3.0, 4.0])
        self.assertEqual(r["timing"]["min_s"], 3.0)
        self.assertEqual(r["answer"]["match"], "exact")
        self.assertEqual(r["answer"]["chk"], self.ref["q1"].chk())
        self.assertEqual(r["answer"]["rows"], self.ref["q1"].count)

    def test_every_answer_is_judged_before_any_time_is_printed(self):
        self.run_q([self.fake.contender("a"), self.fake.contender("bad", "wrong-value"), self.fake.contender("c")])
        sinks = [s for _, s in self.fake.calls]
        self.assertLess(max(i for i, s in enumerate(sinks) if s == "answer"), min(i for i, s in enumerate(sinks) if s == "timed"))
        lines = self.fake.lines
        self.assertLess(max(i for i, l in enumerate(lines) if l.startswith("answer")), min(i for i, l in enumerate(lines) if l.startswith("time")))

    def test_a_wrong_answer_is_reported_and_not_timed(self):
        for mode in ("wrong-value", "swap", "drop-row", "trailing", "garbage"):
            with self.subTest(mode):
                self.fake = Fake(self)
                recs = self.run_q([self.fake.contender("good"), self.fake.contender("bad", mode)])
                r = self.rec(recs, "bad")
                self.assertEqual(r["status"], "wrong-answer", r)
                self.assertTrue(r["reason"])
                for key in ("timing", "peak_rss_kb", "cpu_s", "answer"):
                    self.assertNotIn(key, r)
                self.assertEqual([s for n, s in self.fake.calls if n == "bad"], ["answer"])
                self.assertFalse([l for l in self.fake.lines if l.startswith("time") and "bad" in l])
                self.assertEqual(self.rec(recs, "good")["status"], "ok")

    def test_the_swap_of_two_v2_is_caught_by_the_digest_and_not_by_the_totals(self):
        recs = self.run_q([self.fake.contender("swap", "swap")])
        r = self.rec(recs, "swap")
        self.assertEqual(r["status"], "wrong-answer")
        self.assertIn("digest", r["reason"])

    def test_the_rows_in_another_order_are_the_same_answer(self):
        self.assertEqual(self.rec(self.run_q([self.fake.contender("rev", "reorder")]), "rev")["status"], "ok")

    def test_refused_crashed_and_timed_out_have_no_time_and_a_reason(self):
        recs = self.run_q([self.fake.contender("good"), self.fake.contender("r", "refuse", family="table"), self.fake.contender("r2", "refuse"), self.fake.contender("c", "crash"),
                           self.fake.contender("h", "hang")])
        r = self.rec(recs, "r")
        self.assertEqual((r["status"], r["rule"]), ("refused", "limit.state-too-large"))  # a csv refusal is a line on standard error: its tag is read from there
        self.assertEqual((self.rec(recs, "r2")["status"], self.rec(recs, "r2")["rule"]), ("refused", None))
        self.assertEqual(self.rec(recs, "c")["status"], "crashed")
        h = self.rec(recs, "h")
        self.assertEqual(h["status"], "timeout")
        self.assertIn("over 120 s", h["reason"])
        for name in ("r", "r2", "c", "h"):
            for key in ("timing", "peak_rss_kb", "cpu_s"):
                self.assertNotIn(key, self.rec(recs, name))
            self.assertEqual([s for n, s in self.fake.calls if n == name], ["answer"])

    def test_a_failure_while_timing_loses_the_times(self):
        recs = self.run_q([self.fake.contender("good"), self.fake.contender("flaky", "fails-while-timing")])
        r = self.rec(recs, "flaky")
        self.assertEqual(r["status"], "failed-while-timing")
        for key in ("timing", "peak_rss_kb", "cpu_s"):
            self.assertNotIn(key, r)

    def test_a_timeout_is_dropped_from_the_larger_steps_and_a_skip_is_not_launched(self):
        ctx = self.fake.context()
        ctx.skip = {("q1", "skipme"): "out of memory at 1e5"}
        ctx.dropped = {("q1", "dropme")}
        recs = self.bj.run_join_question(ctx, self.q, [self.fake.contender("skipme"), self.fake.contender("dropme"), self.fake.contender("h", "hang")])
        self.assertIn("out of memory at 1e5", self.rec(recs, "skipme")["reason"])
        self.assertIn("over 120 s", self.rec(recs, "dropme")["reason"])
        self.assertEqual([n for n, _ in self.fake.calls], ["h"])
        self.assertIn(("q1", "h"), ctx.dropped)

    def test_the_header_of_table_is_exact_and_a_double_engines_is_its_own(self):
        names = self.jref.joined_columns("q1")
        bad = list(names)
        bad[7] = "small_id9"
        orig = self.fake.payload
        self.fake.payload = lambda mode: csv_of(bad, self.fake.rows)
        recs = self.run_q([self.fake.contender("t", family="table", profile="exact"), self.fake.contender("d")])
        self.assertEqual(self.rec(recs, "t")["status"], "wrong-answer")
        self.assertEqual(self.rec(recs, "d")["status"], "ok")
        self.fake.payload = orig


class LaunchToFile(Shared):
    def test_the_time_is_the_clocks_difference_and_the_output_goes_to_the_file(self):
        bj = self.bj
        d = pathlib.Path(self.tmp.name)
        seen = {}

        def run(argv, **kw):
            seen.update(kw)
            kw["stdout"].write(b"rows")
            return subprocess.CompletedProcess(argv, 0, None, b"err")

        ticks = iter([10.0, 12.5])
        o = bj.launch_to_file(["x"], env={"A": "1"}, dest=d / "o.csv", timeout=7, run=run, clock=lambda: next(ticks))
        self.assertEqual((o.returncode, o.seconds, o.stderr, o.timed_out), (0, 2.5, b"err", False))
        self.assertEqual((d / "o.csv").read_bytes(), b"rows")
        self.assertEqual((seen["timeout"], seen["stderr"], seen["stdin"]), (7, subprocess.PIPE, subprocess.DEVNULL))
        self.assertEqual(seen["env"]["A"], "1")
        self.assertIn("PATH", seen["env"])

    def test_a_timeout_and_a_missing_program_are_outcomes(self):
        bj = self.bj
        d = pathlib.Path(self.tmp.name)

        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])

        ticks = iter([1.0, 4.0])
        o = bj.launch_to_file(["x"], dest=d / "o.csv", run=slow, clock=lambda: next(ticks), timeout=3)
        self.assertEqual((o.timed_out, o.returncode, o.seconds), (True, None, 3.0))

        def missing(argv, **kw):
            raise FileNotFoundError("no such program")

        ticks = iter([1.0, 1.5])
        o = bj.launch_to_file(["x"], dest=d / "o.csv", run=missing, clock=lambda: next(ticks))
        self.assertEqual((o.returncode, o.seconds), (127, 0.5))

    def test_a_large_stderr_is_clipped(self):
        bj = self.bj
        d = pathlib.Path(self.tmp.name)
        o = bj.launch_to_file(["x"], dest=d / "o.csv", run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, None, b"E" * 100000))
        self.assertLessEqual(len(o.stderr), 4096)


class Forms(Shared):
    """G-forms: every contender's form for every question, run for real at 2,000 rows, gives the reference's rows; the contenders installed (a missing one is skipped with its reason)."""

    def tools(self, path=None):
        return self.bn.find_tools(table=TABLE, path=path)

    def contenders(self, family, path=None):
        tools = self.tools(path)
        found = [c for c in self.bj.build_contenders(tools, {t: str(p) for t, p in self.paths.items()}, [family]) if c.family == family]
        if not found:
            self.skipTest("%s is not installed here (%s): its join forms are not validated by this run" % (family, self.bn.why_missing(tools, family)))
        return found

    def check(self, family, path=None):
        bn, bj, jref = self.bn, self.bj, self.jref
        ctx = Fake(self).context()
        for c in self.contenders(family, path):
            for qid in jref.JOINS:
                with self.subTest(contender=c.name, question=qid):
                    form = bj.form_of(ctx, c, qid)
                    dest = pathlib.Path(self.tmp.name) / ("f-%s-%s.csv" % (c.name.replace("@", "-"), qid))
                    out = bj.launch_to_file(form.answer, env=form.env, dest=dest, timeout=120)
                    cl = bn.classify(out.returncode, out.stdout, out.stderr, family.startswith("table"), out.timed_out)
                    self.assertEqual(cl["status"], "ok", (cl, out.stderr[:300]))
                    acc, why = jref.check_output(dest, qid, expect_header=jref.joined_columns(qid) if family == "table" else None)
                    self.assertIsNone(why)
                    self.assertIsNone(jref.compare(qid, self.ref[qid], acc))

    def test_table(self):
        self.check("table")

    def test_table_sorted(self):
        self.check("table-sorted")

    def test_duckdb(self):
        self.check("duckdb")

    def test_duckdb_python_module_when_there_is_no_command(self):
        tools = self.bn.find_tools(table=TABLE, path="/nonexistent")
        if not tools["duckdb"]:
            self.skipTest("the Python module duckdb is not importable here: the module form is not validated by this run")
        self.assertEqual(tools["duckdb"][0], "python")
        self.check("duckdb", "/nonexistent")

    def test_csvtk(self):
        self.check("csvtk")

    def test_miller(self):
        self.check("mlr")

    def test_pandas(self):
        self.check("pandas")

    def test_the_merge_gives_the_bytes_of_the_hash_join_over_the_same_sorted_files(self):
        bj, jref = self.bj, self.jref
        ctx = Fake(self).context()
        (c,) = self.contenders("table-sorted")
        for qid in jref.JOINS:
            with self.subTest(qid):
                form = bj.form_of(ctx, c, qid)
                self.assertIn("--join-sorted", form.answer)
                self.assertNotIn("--join-sorted", form.alt)
                a = subprocess.run(form.answer, capture_output=True)
                b = subprocess.run(form.alt, capture_output=True)
                self.assertEqual((a.returncode, b.returncode), (0, 0), a.stderr[:200])
                self.assertEqual(a.stdout, b.stdout)
                # the rows are in key order: the key column of x, read as text, never goes down
                q = jref.JOINS[qid]
                at = jref.X_COLUMNS.index(q["key"])
                keys = [line.split(",")[at] for line in a.stdout.decode().splitlines()[1:]]
                self.assertEqual(keys, sorted(keys))

    def test_the_timed_form_is_the_answer_form_with_the_sink_changed(self):
        ctx = Fake(self).context()
        for family in ("table", "table-sorted", "duckdb", "csvtk", "mlr", "pandas"):
            tools = self.tools()
            for c in self.bj.build_contenders(tools, {t: str(p) for t, p in self.paths.items()}, [family]):
                form = self.bj.form_of(ctx, c, "q2")
                if family == "duckdb":
                    self.assertEqual(" ".join(form.answer).replace("/dev/stdout", "/dev/null"), " ".join(form.timed))
                else:
                    self.assertEqual(form.answer, form.timed)

    def test_the_forms_ask_for_the_canonical_row(self):
        bj, jref = self.bj, self.jref
        paths = {t: str(p) for t, p in self.paths.items()}
        sql = bj.duck_sql("q2", paths)
        self.assertIn("x.*", sql)
        self.assertIn("r.id1 AS medium_id1", sql)
        self.assertNotIn("r.id2 AS", sql)            # the key is the left table's, once
        self.assertIn("ON x.id2 = r.id2", sql)
        self.assertIn(" JOIN ", bj.duck_sql("q1", paths))
        self.assertIn("LEFT JOIN", bj.duck_sql("q3", paths))
        self.assertIn("ON x.id5 = r.id5", bj.duck_sql("q4", paths))
        argv = bj.mlr_argv("mlr", "q3", paths)
        self.assertIn("--ur", argv)
        self.assertEqual(argv[argv.index("-o") + 2], ",".join(jref.joined_columns("q3")))
        self.assertNotIn("--ur", bj.mlr_argv("mlr", "q2", paths))
        self.assertNotIn("unsparsify", bj.mlr_argv("mlr", "q2", paths))
        t = bj.table_argv("table", "q3", self.paths)
        self.assertIn("left", t)
        self.assertEqual(t[:3], ["table", "--threads", "1"])
        self.assertNotIn("--join-sorted", t)
        self.assertEqual(bj.table_argv("table", "q1", self.paths, merge=True)[3], "--join-sorted")

    def test_the_pins_of_the_join_contenders_are_recorded(self):
        tools = self.tools()
        cs = self.bj.build_contenders(tools, {t: str(p) for t, p in self.paths.items()}, ["table", "table-sorted", "duckdb"])
        for c in cs:
            self.assertTrue(c.info["version"])
        self.assertTrue([c for c in cs if c.family == "table-sorted"])


class Program(unittest.TestCase):
    """The driver run as a program with --suite join, on a 20,000-row set, with the real `table` and stub contenders; no time is read as a result."""

    STUB = r'''
import os, random, sys
qid, x, right, key, kind = sys.argv[1:6]
mode = os.environ.get("STUB_MODE", "good")
if mode == "hang":
    import time
    time.sleep(600)
if mode == "refuse":
    print('{"ok":false,"error":{"rule":"limit.state-too-large"},"errors":[{"rule":"limit.state-too-large"}]}')
    sys.exit(8)
if mode == "crash":
    os.kill(os.getpid(), 11)
if mode == "garbage":
    rng = random.Random(7)
    sys.stdout.buffer.write(bytes(rng.randrange(256) for _ in range(5000)))
    sys.exit(0)
rt = open(right).read().splitlines()
kat = rt[0].split(",").index(key)
table = {}
for line in rt[1:]:
    c = line.split(",")
    table[c[kat]] = [v for i, v in enumerate(c) if i != kat]
xt = open(x).read().splitlines()
xat = xt[0].split(",").index(key)
nright = len(rt[0].split(",")) - 1
out = [xt[0] + "," + ",".join("r%d" % i for i in range(nright))]
rows = []
for line in xt[1:]:
    c = line.split(",")
    m = table.get(c[xat])
    if m is not None:
        rows.append(c + m)
    elif kind == "left":
        rows.append(c + [""] * nright)
if mode == "wrong-value":
    r = rows[3]; r[-1] = r[-1][:-1] + str((int(r[-1][-1]) + 1) % 10)
elif mode == "swap":
    rows[2][-1], rows[7][-1] = rows[7][-1], rows[2][-1]
elif mode == "drop-row":
    rows = rows[:-1]
elif mode == "reorder":
    rows = list(reversed(rows))
text = "\n".join(out + [",".join(r) for r in rows]) + "\n"
if mode == "trailing":
    text += "\n"
sys.stdout.write(text)
'''

    missing = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.bn, cls.bj, cls.jgen, cls.jref = mods()
        except AssertionError as e:
            cls.missing = e
            return
        cls.tmp = tempfile.TemporaryDirectory(prefix="named-nb3-prog-")
        cls.dir = pathlib.Path(cls.tmp.name)
        cls.data = cls.dir / "data"
        cls.stub = cls.dir / "stub.py"
        cls.stub.write_text(cls.STUB)

    @classmethod
    def tearDownClass(cls):
        if cls.missing is None:
            cls.tmp.cleanup()

    def setUp(self):
        if self.missing is not None:
            raise self.missing

    def drive(self, *args, env=None, results=None, timeout=600):
        results = results or (self.dir / ("r%d.json" % len(list(self.dir.glob("r*.json")))))
        e = dict(os.environ)
        e.update(env or {})
        e.pop("NAMED_DATA", None)
        p = subprocess.run([sys.executable, str(DRIVER), "--suite", "join", "--rows", "2e4", "--out", str(self.data), "--results", str(results), "--table", TABLE, *args],
                           capture_output=True, text=True, timeout=timeout, env=e)
        return p, results

    def load(self, results):
        return json.loads(pathlib.Path(results).read_text())

    def test_a_join_run_records_every_condition_and_gates_before_it_times(self):
        p, results = self.drive("--runs", "2", "--contenders", "table,table-sorted", "--questions", "q1,q3,q5")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.load(results)
        bn = self.bn
        self.assertEqual(bn.validate_result(r), [])
        self.assertEqual(bn.missing_conditions(r["conditions"]), [])
        self.assertEqual(r["suite"], "join")
        self.assertEqual(sorted(r["conditions"]["tables"]), ["big", "medium", "small", "x"])
        for t, info in r["conditions"]["tables"].items():
            self.assertEqual(len(info["sha256"]), 64)
            self.assertEqual(info["bytes"], r["step"]["table_bytes"][t])
        self.assertEqual({(x["question"], x["contender"]) for x in r["results"]}, {(q, c) for q in ("q1", "q3", "q5") for c in ("table@1", "table-sorted@1")})
        for x in r["results"]:
            self.assertEqual(x["status"], "ok", x)
            self.assertEqual(x["timing"]["runs"], 2)
            self.assertEqual(x["answer"]["match"], "exact")
            self.assertTrue(x["forms_equal"])
            self.assertEqual(x["answer"]["chk"], r["reference"][x["question"]]["chk"])
        self.assertFalse(r["publishable"])
        self.assertIn("runs", " ".join(r["publishable_reasons"]))
        self.assertEqual(len(r["conditions"]["load"]["end"]), 3)
        lines = p.stdout.splitlines()
        for qid in ("q1", "q3", "q5"):
            answers = [i for i, l in enumerate(lines) if l.startswith("answer  %s  " % qid)]
            times = [i for i, l in enumerate(lines) if l.startswith("time  %s  " % qid)]
            self.assertTrue(answers and times)
            self.assertLess(max(answers), min(times))
        self.assertIn("publishable: false", p.stdout)
        self.assertIn("step 2e4", p.stdout)
        self.assertIn("bytes", p.stdout)

    def test_the_refusal_report_is_in_the_result(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q2,q5", "--refusal-bound", "100000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rep = self.load(results)["refusal_report"]
        self.assertEqual(sorted(rep), ["q2", "q5"])
        q5 = rep["q5"]
        self.assertEqual((q5["status"], q5["rule"], q5["exit"]), ("refused", "limit.state-too-large", 8))
        self.assertEqual(q5["facts"]["holder"], "join-build")
        self.assertEqual(q5["facts"]["limit"], 100000)
        self.assertLessEqual(q5["facts"]["held"], 100000)
        self.assertGreater(q5["facts"]["rows_held"], 0)
        self.assertEqual(q5["repair"]["kind"], "none")
        self.assertIn("--join-prefix", q5["repair"]["reason"])      # the design's spelling names the build's columns with a prefix, so the exchange is not offered
        self.assertIsNotNone(q5["explain"]["accounted_at_most"])
        self.assertEqual(q5["explain"]["budget"], 100000)
        self.assertTrue(q5["explain"]["may_pass_bound"])
        self.assertEqual(rep["q2"]["status"], "answered")             # the 20-row medium table fits
        self.assertIn("report  q5  at the default bounds: refused", p.stdout)

    def test_the_default_bounds_answer_at_this_size(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q5")
        rep = self.load(results)["refusal_report"]["q5"]
        self.assertEqual(rep["status"], "answered")
        self.assertFalse(rep["explain"]["may_pass_bound"])

    def test_g_answer_end_to_end_each_wrong_stub_is_reported_and_not_timed(self):
        for mode in ("wrong-value", "swap", "drop-row", "trailing", "garbage"):
            with self.subTest(mode):
                p, results = self.drive("--runs", "2", "--contenders", "table", "--questions", "q2", "--external", "stub=double=%s" % self.stub, env={"STUB_MODE": mode})
                r = self.load(results)
                (x,) = [x for x in r["results"] if x["contender"] == "stub"]
                self.assertEqual(x["status"], "wrong-answer", x)
                self.assertNotIn("timing", x)
                self.assertNotEqual(p.returncode, 0)
                self.assertNotIn("Traceback", p.stdout + p.stderr)
                self.assertFalse([l for l in p.stdout.splitlines() if l.startswith("time") and "stub" in l])
                (t,) = [x for x in r["results"] if x["contender"] == "table@1"]
                self.assertEqual(t["status"], "ok")

    def test_a_reordered_join_answer_is_the_same_answer(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q3", "--external", "stub=double=%s" % self.stub, env={"STUB_MODE": "reorder"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
        self.assertEqual(x["status"], "ok")

    def test_g_refusal_end_to_end(self):
        for mode, status in (("refuse", "refused"), ("crash", "crashed")):
            with self.subTest(mode):
                p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q1", "--external", "stub=double=%s" % self.stub, env={"STUB_MODE": mode})
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
                self.assertEqual(x["status"], status)
                self.assertNotIn("timing", x)
                self.assertTrue(x["reason"])

    def test_g_refusal_a_timeout(self):
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q1", "--timeout", "2", "--external", "stub=double=%s" % self.stub, env={"STUB_MODE": "hang"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        (x,) = [x for x in self.load(results)["results"] if x["contender"] == "stub"]
        self.assertEqual(x["status"], "timeout")
        self.assertIn("over 2 s", x["reason"])
        self.assertIn(["q1", "stub"], self.load(results)["dropped"])

    def test_g_repro_the_same_seed_gives_the_same_tables(self):
        shas = []
        for seed, sub in (("108", "a"), ("108", "b"), ("109", "c")):
            p, results = self.drive("--seed", seed, "--out", str(self.dir / ("repro-" + sub)), "--runs", "1", "--contenders", "table", "--questions", "q1")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            r = self.load(results)
            shas.append({t: i["sha256"] for t, i in r["conditions"]["tables"].items()})
            self.assertEqual(r["conditions"]["seed"], int(seed))
        self.assertEqual(shas[0], shas[1])
        self.assertNotEqual(shas[0], shas[2])

    def test_the_large_steps_need_an_explicit_opt_in_and_print_the_estimate_first(self):
        data = self.dir / "never"
        for rows in ("1e7", "1e8"):
            p = subprocess.run([sys.executable, str(DRIVER), "--suite", "join", "--rows", rows, "--out", str(data), "--results", str(self.dir / "never.json"), "--table", TABLE],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn("bench.large-size", p.stdout)
            self.assertIn("--accept-large", p.stdout)
            self.assertIn("estimate:", p.stdout)
            self.assertFalse(data.exists())
        p = subprocess.run([sys.executable, str(DRIVER), "--suite", "join", "--rows", "1e8", "--estimate", "--out", str(data), "--table", TABLE], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(str(self.jgen.estimate_bytes(10**8)), p.stdout)
        self.assertIn("launches", p.stdout)
        self.assertFalse(data.exists())

    def test_refusals_with_a_tag(self):
        for args, tag in ((["--questions", "q6"], "bench.question"), (["--questions", "q10"], "bench.question"), (["--contenders", "polars"], "bench.contender"),
                          (["--runs", "0"], "bench.args"), (["--rows", "50"], "bench.args"), (["--skip", "q9"], "bench.skip"), (["--external", "x=fast=/bin/true"], "bench.external"),
                          (["--out", str(ROOT / "build" / "named-nb3-test")], "bench.inside-repo")):
            with self.subTest(args):
                p = subprocess.run([sys.executable, str(DRIVER), "--suite", "join", "--rows", "2e4", "--table", TABLE, "--results", str(self.dir / "x.json"), "--out", str(self.dir / "args"), *args],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
                self.assertIn(tag, p.stdout)
                self.assertNotIn("Traceback", p.stdout + p.stderr)
        self.assertFalse((ROOT / "build" / "named-nb3-test").exists())

    def test_not_enough_disk_is_refused_before_generating(self):
        p = subprocess.run([sys.executable, str(DRIVER), "--suite", "join", "--rows", "2e4", "--out", str(self.dir / "tiny"), "--results", str(self.dir / "x.json"), "--table", TABLE],
                           capture_output=True, text=True, timeout=120, env={**os.environ, "BENCH_NAMED_FREE_BYTES": "1000"})
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("bench.disk", p.stdout)
        self.assertFalse((self.dir / "tiny").exists() and any((self.dir / "tiny").iterdir()))

    def test_a_reused_set_is_checked_against_its_sha256(self):
        data = self.dir / "reuse"
        args = ("--runs", "1", "--contenders", "table", "--questions", "q1", "--out", str(data))
        p, results = self.drive(*args)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("reused", p.stdout)
        shas = {t: i["sha256"] for t, i in self.load(results)["conditions"]["tables"].items()}
        p, results = self.drive(*args)
        self.assertIn("reused, sha256 checked", p.stdout)
        # one byte of the big table changed, the size the same: the set is not the one its metadata describes, so it is written again
        f = data / self.jgen.file_name("big", 20000)
        raw = bytearray(f.read_bytes())
        raw[-2] = ord("0") if raw[-2] != ord("0") else ord("1")
        f.write_bytes(bytes(raw))
        p, results = self.drive(*args)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("reused", p.stdout)
        self.assertEqual({t: i["sha256"] for t, i in self.load(results)["conditions"]["tables"].items()}, shas)

    def test_a_missing_contender_is_not_run_with_its_reason(self):
        p, results = self.drive("--runs", "1", "--contenders", "table,csvtk", "--questions", "q1", env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.load(results)
        self.assertIn("csvtk", {x["contender"] for x in r["not_run"]})
        self.assertFalse([x for x in r["results"] if x["family"] == "csvtk"])
        self.assertIn("NOT RUN", p.stdout)

    def test_the_groupby_suite_and_the_others(self):
        p = subprocess.run([sys.executable, str(DRIVER), "--suite", "1brc"], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 3)
        self.assertIn("bench.suite-not-built", p.stdout)

    def test_the_published_table_of_a_join_run_is_refused_when_it_is_not_publishable(self):
        out = self.dir / "published.md"
        p, results = self.drive("--runs", "1", "--contenders", "table", "--questions", "q1", "--write-table", str(out))
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("bench.not-publishable", p.stdout)
        self.assertFalse(out.exists())
        self.assertTrue(pathlib.Path(results).exists())


class Sources(unittest.TestCase):
    def test_the_scripts_exist_start_children_through_procs_and_are_short(self):
        mods()
        for name in ("bench_named_join.py", "bench_named_join_gen.py", "bench_named_join_ref.py", "bench_named.py"):
            text = (SCRIPTS / name).read_text()
            self.assertLessEqual(len(text.splitlines()), 2000, name)
            if "subprocess" in text.replace("import procs as subprocess", ""):
                self.assertIn("import procs as subprocess", text, name)

    def test_the_mutants_still_apply(self):
        script = SCRIPTS / "bench_named_join_mutants.py"
        if not script.exists():
            raise AssertionError("scripts/bench_named_join_mutants.py does not exist: NB3 is not built")
        got = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
        self.assertEqual((got.returncode, got.stdout.count("!!")), (0, 0), got.stdout)
        self.assertGreaterEqual(int(got.stdout.split()[0]), 25)


if __name__ == "__main__":
    unittest.main()
