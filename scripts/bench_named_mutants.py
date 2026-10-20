#!/usr/bin/env python3
"""Mutation check of the timing driver's logic (scripts/bench_named.py, docs/named-benchmarks.md NB2): each mutant is one deliberate defect in the answer comparison and its
tolerance, the classification of a launch, the min-of-N and the rotation of the rounds, the no-time-on-failure rule, the load line and the publishable flag, the writer of a
published table, and the size, launch and disk arithmetic. A mutant is killed when a test of tests/conformance/test_bench_named.py fails (or the driver no longer imports).

    python3 scripts/bench_named_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no run; CI runs it). MUT_SHARD=I/N runs the I-th of N parts of the list. Unlike the mutants of the tool, these
do not rebuild anything and do not edit the working tree: each run copies scripts/ and tests/conformance/ to a directory of its own and mutates the copy, so shards can run at once.
The tests run are the unit classes that need no binary (the program-level mutants name the program tests that must kill them); TOOLBOX_BIN names a built `table` for those.
"""
import os
import pathlib
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
FILE = "bench_named.py"
MOD = "test_bench_named"
UNIT = ["Arithmetic", "Statistics", "Load", "Kinds", "AnswerGate", "Fuzz", "Classify", "Orchestration", "NotExpressible", "Conditions", "Publishable", "RealLaunch", "MachineProbes",
        "MoreAnswerGate", "MoreOrchestration", "MoreConditions"]

# (name, the text replaced, its replacement): each `old` occurs exactly once in scripts/bench_named.py.
MUTANTS = [
    # the load line and the flag of a run that is not publishable (Q11, Q12)
    ("loaded: equal to a quarter of the cores is loaded", "return load1 * LOAD_DIVISOR > cores", "return load1 * LOAD_DIVISOR >= cores"),
    ("loaded: a quarter becomes an eighth", "LOAD_DIVISOR = 4 ", "LOAD_DIVISOR = 8 "),
    ("loaded: the five-minute load decides", 'load1 = c["load"]["start"][0]', 'load1 = c["load"]["start"][1]'),
    ("loaded: the usable cores, not the machine's, are counted", 'cores = c["machine"]["cores"]', 'cores = c["machine"]["affinity_cores"]'),
    ("loaded is never a reason", "    if is_loaded(load1, cores):", "    if False:"),
    ("a virtual machine is not a reason", "    if v is True:", "    if v is False:"),
    ("a machine that cannot be told is a dedicated one", "    elif v is None:", "    elif v is False:"),
    ("a docker that fails is not a reason", '    if d["status"] == "error":', '    if d["status"] == "never":'),
    ("a running container is not a reason", '    elif d["containers"]:', "    elif False:"),
    ("a soak is not a reason", '    if c["soak"]["found"]:', "    if False:"),
    ("four runs are enough", '    if c["runs"] < MIN_RUNS:', '    if c["runs"] < MIN_RUNS - 1:'),
    ("exactly five runs are not enough", '    if c["runs"] < MIN_RUNS:', '    if c["runs"] <= MIN_RUNS:'),
    ("the minimum of N is 3", "MIN_RUNS = 5 ", "MIN_RUNS = 3 "),
    ("incomplete conditions are publishable", "    miss = missing_conditions(c)\n    if miss:\n        return False,", "    miss = []\n    if miss:\n        return False,"),
    ("an empty condition is present", 'if not found or value is None or value == "" or', "if not found or value is None or"),
    ("a load of one number is a load", "len(value) == 3 and", "len(value) >= 1 and"),
    ("the published writer believes the flag", '    ok, reasons = evaluate_publishable(c)\n    if not ok:\n        raise Refusal("bench.not-publishable"', '    ok, reasons = evaluate_publishable(c)\n    if False:\n        raise Refusal("bench.not-publishable"'),
    ("the published writer takes a result without conditions", '    miss = missing_conditions(c) if isinstance(c, dict) else ["conditions"]\n    if miss:', '    miss = []\n    if miss:'),
    ("a publishable flag the conditions contradict is accepted", '        if result.get("publishable") is not ok:', "        if False:"),
    ("a failed contender may carry a time", '                if key in r:\n                    problems.append', "                if False:\n                    problems.append"),
    ("the minimum of the result need not be the minimum", 't.get("min_s") != min(all_s) or', "False or"),
    # the arithmetic of a step
    ("a launch costs one less", "n_questions * n_contenders * (runs + 3)", "n_questions * n_contenders * (runs + 2)"),
    ("the disk need is the file", '"disk_needed": DISK_FACTOR * size', '"disk_needed": size'),
    ("exactly three times the file is not enough disk", "return free_bytes >= DISK_FACTOR * need_bytes", "return free_bytes > DISK_FACTOR * need_bytes"),
    ("twice the file is enough disk", "DISK_FACTOR = 3 ", "DISK_FACTOR = 2 "),
    ("1e6 rows needs the opt-in", "return rows > SMOKE_ROWS", "return rows >= SMOKE_ROWS"),
    ("rows may be zero", "v <= 0:", "v < 0:"),
    ("a soak and a container are both needed to drop a core", 'return "0-5" if soak_found or containers else "0-6"', 'return "0-5" if soak_found and containers else "0-6"'),
    ("taskset without -c", '["taskset", "-c", cores, *argv]', '["taskset", cores, *argv]'),
    # the figure
    ("the maximum is the figure", '"min_s": min(t)', '"min_s": max(t)'),
    ("the last run is the first", '"first_s": t[0]', '"first_s": t[-1]'),
    ("the mean is the median", "statistics.median(t)", "statistics.mean(t)"),
    # the answer gate
    ("a trailing byte: no final newline is fine", '    if not text.endswith("\\n"):', "    if False:"),
    ("a carriage return is fine", '    if "\\r" in text:', "    if False:"),
    ("a NUL byte is fine", '    if "\\0" in text:', "    if False:"),
    ("a byte order mark is fine", '    if text.startswith("\\ufeff"):', "    if False:"),
    ("a header with spaces is fine", 'if any(h == "" or h != h.strip() for h in header):', 'if any(h == "" for h in header):'),
    ("table's header need not be the question's", "        if header != columns:", "        if False:"),
    ("a double engine's header need not have the columns", "    elif len(header) != len(columns):", "    elif False:"),
    ("more rows than the reference pass", "    if len(rows) != len(want):", "    if len(rows) < len(want):"),
    ("the order of the rows counts", "    if not ordered:", "    if ordered:"),
    ("the order of the rows never counts", "    if not ordered:", "    if True:"),
    ("a key may be spelled as a number", 'if profile == "exact" or kind == "key":', 'if profile == "exact":'),
    ("the exact profile takes another spelling", 'if profile == "exact" or kind == "key":', 'if kind == "key":'),
    ("an integer may be off by a tolerance", 'if kind == "dec":\n                truth = Fraction(Decimal(w[j]))', 'if kind in ("dec", "int"):\n                truth = Fraction(Decimal(w[j]))'),
    ("the same value in another spelling is exact", "    if dg == dw:\n        return 1", "    if dg == dw:\n        return 0"),
    ("a tolerance of ten billionths", "TOLERANCE_FRACTION = Fraction(1, 10**9)", "TOLERANCE_FRACTION = Fraction(1, 10**8)"),
    ("a tolerance of a hundredth of a billionth", "TOLERANCE_FRACTION = Fraction(1, 10**9)", "TOLERANCE_FRACTION = Fraction(1, 10**11)"),
    ("the tolerance is not relative", "diff <= TOLERANCE_FRACTION * abs(truth)", "diff <= TOLERANCE_FRACTION"),
    ("the level of an answer is its last cell's", "            level = max(level, lvl)", "            level = lvl"),
    ("a mean with no exact value is taken against nothing", '                if kind == "mean" and truth is None and profile != "exact":', "                if False:"),
    ("a decimal sum is compared with a mean's exact value", 'if kind == "dec":\n                truth = Fraction(Decimal(w[j]))', 'if False:\n                truth = Fraction(Decimal(w[j]))'),
    ("the exact mean is divided by the wrong count", "Fraction(s, n * ref.SCALE)", "Fraction(s, n)"),
    ("the exact means are not held to the reference", '    for qid, cols in (("q3", [2]), ("q4", [1, 2, 3])):\n        if qid not in reference:\n            continue\n        by_key', '    for qid, cols in ():\n        if qid not in reference:\n            continue\n        by_key'),
    ("a sum is not a decimal sum", 'elif c == "sum:v3":', 'elif c == "sum:v2":'),
    ("a mean is an integer", 'if c.startswith("mean:"):', 'if c.startswith("meanx:"):'),
    # the classification of a launch
    ("a timeout is ok when the status was 0", "    if timed_out:\n        return {\"status\": \"timeout\"", "    if False:\n        return {\"status\": \"timeout\""),
    ("a signal is a refusal", "returncode is None or returncode < 0 or returncode >= 128", "returncode is None or returncode < -100 or returncode >= 128"),
    ("a trap status is a refusal", "returncode is None or returncode < 0 or returncode >= 128", "returncode is None or returncode < 0 or returncode >= 200"),
    ("a refusal of table loses its rule", "rule = rule_of(stdout)\n        return", "rule = None\n        return"),
    ("a nonzero exit of another tool is ok", '    if returncode == 0:\n        return {"status": "ok"', '    if returncode in (0, 1):\n        return {"status": "ok"'),
    # the order of the work and the no-time-on-failure rule
    ("a wrong answer is timed", '        if not v["ok"]:', "        if False:"),
    ("the forms of table may differ", 'rec["forms_equal"] = other.returncode == 0 and not other.timed_out and other.stdout == out.stdout', 'rec["forms_equal"] = other.returncode == 0 and not other.timed_out'),
    ("a failed second form is not a wrong answer", '            if not rec["forms_equal"]:', "            if False:"),
    ("a timeout is not dropped from the larger steps", "                ctx.dropped.add((qid, c.name))", "                pass"),
    ("a dropped pair is launched", "        if (qid, c.name) in ctx.dropped:", "        if False:"),
    ("an operator skip is launched", "        if skip is not None:", "        if False:"),
    ("N+1 rounds", "    for r in range(ctx.runs):", "    for r in range(ctx.runs + 1):"),
    ("the rounds do not rotate", "for item in (live[r % n:] + live[:r % n]) if n else []:", "for item in live if n else []:"),
    ("no untimed warm-up", "    for item in list(live):                      # one untimed warm-up each", "    for item in []:                              # one untimed warm-up each"),
    ("a run that fails while timing is kept", "        if out.timed_out or out.returncode != 0:", "        if out.timed_out:"),
    ("a measured run that fails is kept", "        if res is None:", "        if False:"),
    ("a probe that works is a confirmed classification", "    if out.timed_out or out.returncode in (0, None) or rule != q[\"probe\"][\"rule\"]:", "    if out.timed_out or rule != q[\"probe\"][\"rule\"]:"),
    ("a probe with another rule is a confirmed classification", "    if out.timed_out or out.returncode in (0, None) or rule != q[\"probe\"][\"rule\"]:", "    if out.timed_out or out.returncode in (0, None):"),
    # the machine
    ("the hypervisor flag is not read", '                if any(" hypervisor" in " " + l.split(":", 1)[1] for l in flags):', "                if False:"),
    ("any word with hypervisor in it is the flag", '" hypervisor" in " " + l.split(":", 1)[1]', '"hypervisor" in l.split(":", 1)[1]'),
    ("a mac in a virtual machine is not one", '                return out.strip() == "1", "sysctl', '                return out.strip() == "0", "sysctl'),
    ("pgrep finds the driver itself", 'and "bench_named" not in l and "pgrep" not in l', 'and "pgrep" not in l'),
    ("a docker with containers lists none", 'names = [l.strip() for l in out.splitlines() if l.strip()]', "names = []"),
    ("the real timing measures the wrong clock", "    t0 = clock()\n    try:\n        p = run(", "    t0 = 0.0\n    try:\n        p = run("),
    ("a timed run keeps its output", "sink = subprocess.PIPE if capture else subprocess.DEVNULL", "sink = subprocess.PIPE"),
    ("a missing program is a crash of the driver", "    except OSError as e:\n        return Outcome(127", "    except KeyError as e:\n        return Outcome(127"),
    ("the snapshot reads the probes late", "setattr(self, name, (lambda v: lambda: v)(getattr(probes, name)()))", "setattr(self, name, (lambda n: lambda: getattr(probes, n)())(name))"),
    # the program: the rules of refusal that need the driver run as a program
    ("the large steps run without the opt-in", "    if needs_opt_in(rows) and not a.accept_large:", "    if False:"),
    ("a directory inside the repository is used", "    if gen.inside_repo(out_dir):", "    if False:"),
    ("a file is reused without its sha256", 'and sha256_of(path) == meta.get("sha256"):', "and True:"),
    ("the disk is not checked", "    if not disk_ok(_free_bytes(out_dir), need):", "    if False:"),
    ("an unbuilt suite is run", '    if a.suite != "groupby":', "    if False:"),
    ("the scan probe counts the header as a row", 'max(nl - 1, 0) if rc == 0 else 0', 'nl if rc == 0 else 0'),
]

# Mutants that only a program-level test can kill, with the tests that must (the others are killed by the unit classes above).
PROGRAM = {
    "the large steps run without the opt-in": ["Program.test_the_large_steps_need_an_explicit_opt_in_and_print_the_estimate_first"],
    "a directory inside the repository is used": ["Program.test_a_data_directory_inside_the_repository_is_refused"],
    "a file is reused without its sha256": ["Program.test_a_reused_file_is_checked_against_its_sha256"],
    "the disk is not checked": ["Program.test_not_enough_disk_is_refused_before_generating"],
    "an unbuilt suite is run": ["Program.test_a_suite_that_is_not_built_is_refused"],
    "the scan probe counts the header as a row": ["Program.test_the_scan_probe_is_reported"],
}

# Not here, and why (mutants of the first full run that survived and were proved equivalent, so removed; the others of the six were each given a test and killed):
#  - "a number may have a huge exponent" (`abs(dg.adjusted()) > 40` raised to 4000): the bound only keeps `Fraction(Decimal(...))` cheap for an exponent like 1e999999999. Every
#    reference value is a text of at most 12 digits, so a cell with an exponent over 40 differs from it by far more than a billionth either way, and both bounds refuse it:
#    the verdict is the same, only the cost differs (a test cannot see a cost without a clock).
#  - "an integer may be off by a tolerance" in its first form (deleting `if kind == "int": return None`): an integer column has no `truth`, and the guard below it
#    (`if truth is None: return None`) refused it first, so the explicit line was dead; it was removed and the mutant now gives integers a truth (killed).
#  - "a blank line is fine" (the explicit blank-line refusal): a blank line is a row of no cells, and the rectangle check refuses it first (the header has at least one cell);
#    the explicit refusal was dead and was removed.
#  - "the times of a contender that failed stay": the times are set only after every run, so the lines that removed them removed nothing; removed.


def build_tree(dest):
    """scripts/ and tests/conformance/ copied to `dest`, so that a mutant never touches the working tree."""
    shutil.copytree(HERE, dest / "scripts", ignore=shutil.ignore_patterns("__pycache__", "demo", "*.html"))
    (dest / "tests" / "conformance").mkdir(parents=True)
    for name in ("procs.py", "test_bench_named.py"):
        shutil.copy(ROOT / "tests" / "conformance" / name, dest / "tests" / "conformance" / name)


def run_tests(tree, tests):
    env = dict(os.environ)
    env.setdefault("TOOLBOX_BIN", str(ROOT / "build"))
    names = ["%s.%s" % (MOD, t) for t in tests]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-f", *names], cwd=tree / "tests" / "conformance", capture_output=True, text=True, timeout=3600, env=env)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return p.returncode, ", ".join(failed[:3])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    table = [(n, FILE, o, r) for n, o, r in MUTANTS]
    problems = mutlib.check(table, HERE)
    for p in problems:
        print("!! " + p)
    if problems:
        print("%d of %d mutants cannot be applied" % (len(problems), len(table)))
        return 1
    if "--check" in argv:
        print("%d mutants: every pattern occurs exactly once" % len(table))
        return 0
    wanted = [a for a in argv if not a.startswith("--")]
    chosen = [m for m in table if not wanted or any(w in m[0] for w in wanted)]
    shard = os.environ.get("MUT_SHARD")
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        chosen = [m for j, m in enumerate(chosen) if j % n == i - 1]
    if not chosen:
        print("!! no mutant matches")
        return 1
    source = (HERE / FILE).read_text()
    survivors = []
    with tempfile.TemporaryDirectory(prefix="bench-named-mut-") as d:
        tree = pathlib.Path(d)
        build_tree(tree)
        rc, why = run_tests(tree, UNIT)
        print("unmutated:", "pass" if rc == 0 else "FAILED [%s]" % why, flush=True)
        if rc != 0:
            return 1
        for name, file, old, new in chosen:
            (tree / "scripts" / FILE).write_text(source.replace(old, new, 1))
            rc, why = run_tests(tree, PROGRAM.get(name, UNIT))
            print("%-14s %s  [%s]" % ("killed" if rc else "SURVIVED", name, why), flush=True)
            if rc == 0:
                survivors.append(name)
        (tree / "scripts" / FILE).write_text(source)
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
