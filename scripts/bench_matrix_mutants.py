#!/usr/bin/env python3
"""Mutation check of the matrix harness's decision logic (scripts/bench_matrix.py, scripts/bench_matrix_forms.py; docs/matrix-benchmark.md section 11): each mutant is one deliberate
defect in the clock gate, the blank-cell and no-score rules, the answer gate, the determinism count, the slope of instruction counts, the classification at a limit, the profile of a fault,
the detection of a tool, and the order of the work of the clock kinds. A mutant is killed when a test of tests/conformance/test_bench_matrix.py fails.

    python3 scripts/bench_matrix_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no run; CI runs it). MUT_SHARD=I/N runs the I-th of N parts. Like the mutants of the driver of the named benchmarks, each run
copies scripts/ and the test to a directory of its own and mutates the copy; the working tree is never touched. TOOLBOX_BIN names a built `table` for the tests that start it.
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
M = "bench_matrix.py"
F = "bench_matrix_forms.py"
TESTS = ["test_bench_matrix.py"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the clock gate
    ("gate: incomplete conditions pass", M, '    if miss:\n        raise Refusal("matrix.clock-conditions-missing"', '    if False:\n        raise Refusal("matrix.clock-conditions-missing"'),
    ("gate: a run that is not publishable passes", M, "    if ok:\n        return\n    runs = [", "    if True:\n        return\n    runs = ["),
    ("gate: a machine that is not quiet passes", M, '    if other:\n        raise Refusal("matrix.clock-not-quiet"', '    if False:\n        raise Refusal("matrix.clock-not-quiet"'),
    ("gate: fewer than five runs pass", M, '    raise Refusal("matrix.clock-min-runs", "; ".join(runs))', "    return"),
    ("gate: the result is not gated when it carries a clock cell", M, '        clock_gate(result.get("conditions") or {})', "        pass"),
    ("gate: the switch is not needed for the clock kinds", M, "and not a.clock:\n        raise Refusal(\"matrix.clock-switch\"", "and False:\n        raise Refusal(\"matrix.clock-switch\""),
    ("gate: the provisional gate before anything is written is dropped", M, "        clock_gate(bn.collect_conditions(snap, prov))      # before anything is generated or launched", "        pass"),
    ("gate: the large size needs no opt-in", M, "if a.rows > SMOKE_ROWS and not a.accept_large:", "if False and a.rows > SMOKE_ROWS and not a.accept_large:"),
    ("gate: results may be written inside the repository", M, "if a.results and forms.inside_repo(pathlib.Path(a.results).parent):", "if False and a.results and forms.inside_repo(pathlib.Path(a.results).parent):"),
    # the cells
    ("cells: a kind need not be known", M, '        if c.get("kind") not in KINDS:', "        if False:"),
    ("cells: a score field is fine", M, "            if str(k).lower() in FORBIDDEN:", "            if False:"),
    ("cells: a blank cell may give a blank reason", M, "if not isinstance(r, str) or not r.strip():", "if not isinstance(r, str):"),
    ("cells: a cell may have a value and a reason", M, '        if r:\n            raise Refusal("matrix.cell-both"', '        if False:\n            raise Refusal("matrix.cell-both"'),
    ("cells: a time outside the clock kinds is fine", M, 'if c["kind"] not in CLOCK_KINDS and keys &', 'if False and keys &'),
    ("cells: a time on an answer that failed is fine", M, '            if v.get("answer") != "ok":', "            if False:"),
    ("cells: four runs are enough", M, 'if v.get("runs", 0) < MIN_RUNS:', 'if v.get("runs", 0) < MIN_RUNS - 1:'),
    ("cells: five runs are not enough", M, 'if v.get("runs", 0) < MIN_RUNS:', 'if v.get("runs", 0) <= MIN_RUNS:'),
    ("cells: a minimum that is not the minimum is fine", M, 'v.get("min_s") != min(v["all_s"])', 'v.get("min_s") < min(v["all_s"])'),
    ("cells: the number of runs need not match the times", M, ' or len(v["all_s"]) != v["runs"])', ")"),
    # the arithmetic
    ("distinct: no canonical order", M, "canon.add(repr(rows if (ordered or isinstance(rows, bytes)) else [rows[0]] + sorted(rows[1:])))", "canon.add(repr(rows))"),
    ("distinct: an ordered task is sorted too", M, "canon.add(repr(rows if (ordered or isinstance(rows, bytes))", "canon.add(repr(rows if (isinstance(rows, bytes))"),
    ("distinct: the raw count is by length", M, "raw = {hashlib.sha256(o).hexdigest() for o in outputs}", "raw = {len(o) for o in outputs}"),
    ("distinct: no output counts as one", M, '    if not outputs:\n        raise Refusal("matrix.no-outputs"', '    if False:\n        raise Refusal("matrix.no-outputs"'),
    ("slope: two runs of the same size are fine", M, "    if n1 == n2:", "    if False:"),
    ("slope: a shrinking count is a slope", M, "    if per < 0:", "    if per < -1e18:"),
    ("slope: a flat count is not valid", M, "    if per < 0:", "    if per <= 0:"),
    ("slope: the start-up is from the large run", M, '"startup": c1 - per * n1}', '"startup": c2 - per * n1}'),
    ("slope: the start-up ignores the rows", M, '"startup": c1 - per * n1}', '"startup": c1}'),
    ("callgrind: only the last process counts", M, "return (sum(found), len(found)) if found else None", "return (found[-1], len(found)) if found else None"),
    ("limit: a status over 127 is not a kill", M, "    if rc is None or rc < 0 or rc >= 128:", "    if rc is None or rc < 0:"),
    ("limit: a wrong answer is correct", M, '"answered-correct" if j["status"] == "ok" else "answered-wrong"', '"answered-correct"'),
    ("limit: a timeout is a refusal", M, "    if outcome.timed_out:\n        return {\"class\": \"timeout\"}", "    if False:\n        return {\"class\": \"timeout\"}"),
    ("limit: the repair is not read", M, '        return e.get("rule"), (rep.get("kind") if isinstance(rep, dict) else None)', '        return e.get("rule"), None'),
    ("limit: a limit at the peak puts pressure", M, "    if limit_bytes >= unconstrained_peak_bytes:", "    if limit_bytes > unconstrained_peak_bytes:"),
    ("limit: a limit over the peak puts pressure", M, "    if limit_bytes >= unconstrained_peak_bytes:", "    if False:"),
    ("fault: a success counts as a failure", M, '"exit_nonzero": outcome.returncode != 0', '"exit_nonzero": outcome.returncode == 1'),
    ("fault: stderr is not looked at", M, '"stderr_nonempty": bool(outcome.stderr.strip())', '"stderr_nonempty": False'),
    ("fault: a JSON error is not recognised", M, '"json_error": _json_error(outcome.stdout) is not None', '"json_error": False'),
    # the tools
    ("tools: a version that differs from the pin is pinned", M, '            st["pinned"] = st["version"] == c["pin"]', '            st["pinned"] = True'),
    ("tools: a tool that fails to run is found", M, "if rc == 0 else None\n                if m:", "if True else None\n                if m:"),
    ("tools: a missing tool is not required", M, "    missing = [n for n in names if not by[n][\"found\"]]", "    missing = []"),
    ("tools: the install command is the other system's", M, 'key = "macos" if system == "Darwin" else "linux"', 'key = "linux" if system == "Darwin" else "macos"'),
    ("tools: a found tool lists its install command", M, '        if s["found"]:\n            pin =', '        if False:\n            pin ='),
    # the work of the kinds
    ("work: the command is put in front of the logical name too", M, 'list(tool["command"]) + list(form.argv[1:])', 'list(tool["command"]) + list(form.argv)'),
    ("work: a shell line gets the command", M, "argv = list(form.argv) if form.shell else", "argv = "),
    ("work: a wrong answer is judged right", M, "j = forms.compare(ref_of(ctx, task), o.stdout)", 'j = {"status": "ok", "level": "exact", "detail": ""}'),
    ("work: a refusal is judged", M, '            if st["status"] == "ok":\n                cells.append(cell(task, name, "a", {"outcome": "yes"}))', '            if True:\n                cells.append(cell(task, name, "a", {"outcome": "yes"}))'),
    ("work: a tool that is not installed is run", M, '            if not tool["found"]:\n                why = not_installed_reason(tool)\n                cells += [blank(task, name, "a", why)', '            if False:\n                why = not_installed_reason(tool)\n                cells += [blank(task, name, "a", why)'),
    ("work: the determinism of an ordered task is by canonical order", M, 'd = distinct_outputs(outputs, forms.TASKS[task]["ordered"])', "d = distinct_outputs(outputs, False)"),
    ("work: one repeat only", M, "                for _ in range(ctx.repeats):", "                for _ in range(1):"),
    ("clock: a pair that failed the gate is timed", M, 'c["value"]["status"] == "ok"}', 'c["value"]["status"] != "x"}'),
    ("clock: no rotation", M, "order = live[r % len(live):] + live[:r % len(live)] if live else []", "order = live"),
    ("clock: no warm-up", M, '        for t in live:\n            _launch(ctx, t, forms_of[t["name"]], capture=False)\n        times', "        times"),
    ("clock: one round too many", M, "        for r in range(ctx.runs):", "        for r in range(ctx.runs + 1):"),
    ("clock: the output is kept", M, '    return ctx.launch(argv, env=env, stdin_path=form.stdin_path, timeout=timeout or ctx.timeout, capture=False)', '    return ctx.launch(argv, env=env, stdin_path=form.stdin_path, timeout=timeout or ctx.timeout)'),
    ("clock: the slowest run is the figure", M, '"min_s": s[0],', '"min_s": s[-1],'),
    ("clock: the first run is not the first", M, '"first_s": times[0],', '"first_s": s[0],'),
    ("clock: a failed run keeps its times", M, "                if o.returncode != 0:\n                    times[t[\"name\"]] = None", "                if False:\n                    times[t[\"name\"]] = None"),
    # the answer gate
    ("gate-answer: a tolerance of 1e-3", F, "TOLERANCE = Fraction(1, 10**9)", "TOLERANCE = Fraction(1, 10**3)"),
    ("gate-answer: an integer may be off by a tolerance", F, 'if kind in ("dec6", "float") and want != 0', 'if kind in ("int", "dec6", "float") and want != 0'),
    ("gate-answer: the tolerance is absolute", F, "abs(got - want) <= TOLERANCE * abs(want)", "abs(got - want) <= TOLERANCE"),
    ("gate-answer: a key twice is fine", F, "            if len(set(got_keys)) != len(got_keys):", "            if False:"),
    ("gate-answer: a row more is fine", F, '    if len(body) != len(ref["rows"]):', "    if False:"),
    ("gate-answer: a width is not held", F, "    if len(head) != width:", "    if False:"),
    ("gate-answer: a ragged row is fine", F, "    if any(len(r) != width for r in body):", "    if False:"),
    ("gate-answer: an unordered task is compared in order", F, '    if not ref["ordered"]:', "    if False:"),
    ("gate-answer: an ordered task is compared as a set", F, '    if not ref["ordered"]:', "    if True:"),
    ("gate-answer: a NUL or a BOM is fine", F, '    if "\\x00" in raw or raw.startswith("\\ufeff"):', "    if False:"),
    ("gate-answer: an empty output is fine", F, "    if not raw.strip():", "    if False:"),
    ("gate-answer: a numeric spelling is exact", F, 'return ("exact", "") if text == want_text else ("numeric", "")', 'return ("exact", "")'),
    ("gate-answer: the worst level is not kept", F, "            if _ORDER[lv] > _ORDER[level]:", "            if False:"),
    ("gate-answer: a text key is compared as a number", F, '    if kind == "text":\n        return ("exact", "") if text == want else (None, "key %r, wanted %r" % (text, want))', '    if kind == "text":\n        return ("exact", "")'),
    # the references and the inputs
    ("reference: v1 > 4", F, 's["ge4"] += v1 >= 4', 's["ge4"] += v1 > 4'),
    ("reference: the first nine", F, "ids = sorted(row[2] for row in r)[:10]", "ids = sorted(row[2] for row in r)[:9]"),
    ("reference: the median of an even count is the lower", F, "mid = Fraction(v[n // 2], 10**6) if n % 2 else Fraction(v[n // 2 - 1] + v[n // 2], 2 * 10**6)", "mid = Fraction(v[n // 2], 10**6) if n % 2 else Fraction(v[n // 2 - 1], 10**6)"),
    ("reference: the median of an odd count is the upper", F, "mid = Fraction(v[n // 2], 10**6) if n % 2 else", "mid = Fraction(v[n // 2 + 1], 10**6) if n % 2 else"),
    ("reference: the decimal sum is rounded", F, "rows = [[k_, Fraction(v, 10**6)] for k_, v in s[\"v3_id1\"].items()]", "rows = [[k_, Fraction(v // 10**6)] for k_, v in s[\"v3_id1\"].items()]"),
    ("reference: the product is a sum", F, 's["p_id1"][id1] = s["p_id1"].get(id1, 0) + v1 * v2', 's["p_id1"][id1] = s["p_id1"].get(id1, 0) + v1 + v2'),
    ("inputs: a directory inside the repository is fine", F, "    return p == ROOT or ROOT in p.parents", "    return p == ROOT"),
    ("inputs: the quoted file has no newline in its keys", F, "'%s, \"q%s\"\\nL%s'", "'%s, \"q%s\" L%s'"),
    ("forms: table gets no threads", F, '        argv += ["--threads", str(ns.threads)]', "        pass"),
    ("forms: a machine answer is csv", F, "    if not ns.machine:\n        argv += [\"--format\", \"csv\"]", "    if ns.machine:\n        argv += [\"--format\", \"csv\"]"),
    ("forms: the stdin task names the path", F, "    stdin = ns.file if p is None else None\n    if p is not None:", "    stdin = None\n    if True:"),
    ("forms: the unknown column is never used", F, 'return ns.unknown_column if ns.unknown_column and name == "v1" else name', "return name"),
    ("forms: the missing file is never used", F, "    if ns.missing:\n        return ns.missing", "    if False:\n        return ns.missing"),
    ("forms: table has one thread level", F, 'THREADS = {"table": [1, 2, 4],', 'THREADS = {"table": [1],'),
]


def build_tree(dest):
    shutil.copytree(HERE, dest / "scripts", ignore=shutil.ignore_patterns("__pycache__", "demo", "*.html"))
    (dest / "tests" / "conformance").mkdir(parents=True)
    for name in TESTS + ["procs.py"]:
        shutil.copy(ROOT / "tests" / "conformance" / name, dest / "tests" / "conformance" / name)


def run_tests(tree):
    env = dict(os.environ)
    env.setdefault("TOOLBOX_BIN", str(ROOT / "build"))
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-f", "test_bench_matrix"], cwd=tree / "tests" / "conformance", capture_output=True, text=True, timeout=3600, env=env)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return p.returncode, ", ".join(failed[:3])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    problems = mutlib.check([(n, f, o, r) for n, f, o, r in MUTANTS], HERE)
    for p in problems:
        print("!! " + p)
    if problems:
        print("%d of %d mutants cannot be applied" % (len(problems), len(MUTANTS)))
        return 1
    if "--check" in argv:
        print("%d mutants: every pattern occurs exactly once" % len(MUTANTS))
        return 0
    wanted = [a for a in argv if not a.startswith("--")]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[0] for w in wanted)]
    shard = os.environ.get("MUT_SHARD")
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        chosen = [m for j, m in enumerate(chosen) if j % n == i - 1]
    if not chosen:
        print("!! no mutant matches")
        return 1
    sources = {f: (HERE / f).read_text() for f in {m[1] for m in MUTANTS}}
    survivors = []
    with tempfile.TemporaryDirectory(prefix="bench-matrix-mut-") as d:
        tree = pathlib.Path(d)
        build_tree(tree)
        rc, why = run_tests(tree)
        print("unmutated:", "pass" if rc == 0 else "FAILED [%s]" % why, flush=True)
        if rc != 0:
            return 1
        for name, file, old, new in chosen:
            (tree / "scripts" / file).write_text(sources[file].replace(old, new, 1))
            rc, why = run_tests(tree)
            print("%-8s %s  [%s]" % ("killed" if rc else "SURVIVED", name, why), flush=True)
            if rc == 0:
                survivors.append(name)
            (tree / "scripts" / file).write_text(sources[file])
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
