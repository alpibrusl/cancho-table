#!/usr/bin/env python3
"""Mutation check of the join suite's logic (scripts/bench_named_join_gen.py, bench_named_join_ref.py, bench_named_join.py; docs/named-benchmarks.md, NB3): each mutant is one deliberate defect in the
generator's key sets and sizes, the fingerprint of a joined answer (the digest, the sums, the unmatched rows, the checks of a row), the forms asked of the contenders, the judge, the order of the
work and the rules of refusal. A mutant is killed when a test of tests/conformance/test_named_join.py or test_bench_named_join.py fails.

    python3 scripts/bench_named_join_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no run; CI runs it). MUT_SHARD=I/N runs the I-th of N parts of the list. Like scripts/bench_named_mutants.py it edits a copy of
scripts/ and of the tests in a temporary directory, never the working tree, so shards run at once. The unit classes need no binary; the mutants that only a program-level test (the driver run as a
process, or a contender run for real) can kill name the tests that must, and need `table` (TOOLBOX_BIN names its `build` directory) and, for the forms of the other engines, DuckDB, csvtk and Miller
on PATH.
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
GEN, REF, JOIN = "bench_named_join_gen.py", "bench_named_join_ref.py", "bench_named_join.py"
UNIT = ["test_named_join.Generator", "test_named_join.Reference", "test_named_join.Output", "test_bench_named_join.Orchestration", "test_bench_named_join.SecondForms", "test_bench_named_join.LaunchToFile"]
FORMS = ["test_bench_named_join.Forms"]
TESTS = ("test_named_join.py", "test_bench_named_join.py", "test_bench_named.py", "procs.py")

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the generator: sizes, key sets, the draws
    ("the small table has one row at 1e6", GEN, 'max(n // 10**6, 10)', 'max(n // 10**6, 1)'),
    ("the medium table has no floor", GEN, 'max(n // 10**3, 10)', 'max(n // 10**3, 1)'),
    ("the generator takes 10 rows", GEN, "MIN_ROWS = 100", "MIN_ROWS = 10"),
    ("a fifth of the keys are not common", GEN, "    d = n // 10\n    return perm[:n], perm[d:d + n]", "    d = n // 5\n    return perm[:n], perm[d:d + n]"),
    ("the right keys are the left keys", GEN, "    return perm[:n], perm[d:d + n]", "    return perm[:n], perm[:n]"),
    ("no key is left-only or right-only", GEN, "    m = n + n // 10", "    m = n"),
    ("sample_all draws without the keys first", GEN, '    col = array.array("q", keys)\n    extra = rows - len(keys)', '    col = array.array("q")\n    extra = rows'),
    ("sample_all does not shuffle", GEN, "    rng.shuffle(col)", "    pass"),
    ("the shuffle draws below i", GEN, "j = self.next() % (i + 1)", "j = self.next() % i"),
    ("two tables of ten rows have one name", GEN, 'if table == "medium" and s["medium"] == s["small"]:', "if False:"),
    ("the sorted copy is in descending order", GEN, "    rows.sort(key=lambda line: line.split(\",\", at + 1)[at])", "    rows.sort(key=lambda line: line.split(\",\", at + 1)[at], reverse=True)"),
    ("the sorted copy sorts by the next column", GEN, "    rows.sort(key=lambda line: line.split(\",\", at + 1)[at])", "    rows.sort(key=lambda line: line.split(\",\", at + 2)[at + 1])"),
    ("the disk is not checked", GEN, "    if free < 3 * need and not force:", "    if False:"),
    ("twice the estimate is enough disk", GEN, "    if free < 3 * need and not force:", "    if free < 2 * need and not force:"),
    ("the estimate leaves out the value", GEN, "per_x = 2 * (d[\"small\"] + d[\"medium\"] + d[\"big\"]) + 6 + 8.9 + 7", "per_x = 2 * (d[\"small\"] + d[\"medium\"] + d[\"big\"]) + 6"),
    # the reference: the fingerprint
    ("the digest is linear", REF, "h = ((h ^ (v & MASK)) * M1 + 0x632BE59BD9B4E019) & MASK", "h = ((h + (v & MASK)) * M1 + 0x632BE59BD9B4E019) & MASK"),
    ("the digest leaves the match out of a row", REF, "for v in (id1, id2, id3, v1m, v2m, matched):", "for v in (id1, id2, id3, v1m, v2m):"),
    ("the sum of v2 is the sum of v1", REF, "        self.sum_v2 += v2m", "        self.sum_v2 += v1m"),
    ("the matched rows are not counted", REF, "        self.matched += matched", "        self.matched += 0"),
    ("a left join drops its unmatched rows", REF, '                elif q["kind"] == "left":', "                elif False:"),
    ("a v of 100 is a v", REF, "not 0 <= m < 100 * SCALE", "not 0 <= m <= 100 * SCALE"),
    ("a v of seven decimals is a v", REF, "if m != m.to_integral_value() or", "if False or"),
    ("a v with spaces around it is a v", REF, 'text != text.strip() or text == ""', 'text == ""'),
    ("the fingerprint ignores the digest", REF, '    for field in ("count", "sum_v1", "sum_v2", "matched", "digest"):', '    for field in ("count", "sum_v1", "sum_v2", "matched"):'),
    ("the fingerprint ignores the sum of v2", REF, '    for field in ("count", "sum_v1", "sum_v2", "matched", "digest"):', '    for field in ("count", "sum_v1", "matched", "digest"):'),
    ("the fingerprint ignores the count", REF, '    for field in ("count", "sum_v1", "sum_v2", "matched", "digest"):', '    for field in ("sum_v1", "sum_v2", "matched", "digest"):'),
    # the check of a contender's joined rows
    ("an output with no final newline is fine", REF, '            if fb.read(1) != b"\\n":', "            if False:"),
    ("a header of the wrong width is fine", REF, "            if len(header) != width:", "            if False:"),
    ("table's header need not be the questions", REF, "            if expect_header is not None and header != expect_header:", "            if False:"),
    ("id4 to id6 need not be the id copies", REF, 'if cells[3] != "id" + cells[0] or cells[4] != "id" + cells[1] or cells[5] != "id" + cells[2]:', "if False:"),
    ("an unmatched row may have right cells", REF, '                        if any(c != "" for c in cells[7:]):', "                        if False:"),
    ("the right id copies are not checked", REF, '        if origin is not None and right[c] != "id" + origin:', "        if False:"),
    ("a float spelling of a right id is wrong", REF, "            origin = origin[:-2]", "            pass"),
    ("a float spelling is taken for any id", REF, "FLOAT_INT = re.compile(r\"[0-9]{1,18}\\.0$\")", "FLOAT_INT = re.compile(r\"[0-9]{1,18}\\.[0-9]+$\")"),
    ("a right key may be twice in its table", REF, "                if cells[i] in out[c]:", "                if False:"),
    # the judge, the order of the work, the rules
    ("a joined answer is not compared", JOIN, "            why = jref.compare(qid, ctx.reference[qid], acc)\n        if why is not None:\n            rec.update(status=\"wrong-answer\"", "            why = None\n        if why is not None:\n            rec.update(status=\"wrong-answer\""),
    ("table's header is not held exact", JOIN, 'c.profile == "exact" and c.family == "table"', "False"),
    ("a timeout is not dropped from the larger steps", JOIN, "                ctx.dropped.add((qid, c.name))", "                pass"),
    ("a dropped pair is launched", JOIN, "        if (qid, c.name) in ctx.dropped:", "        if False:"),
    ("an operator skip is launched", JOIN, "        if skip is not None:", "        if False:"),
    ("the second form is not asked", JOIN, "        if form.alt is not None:", "        if False:"),
    ("the sorted merge may give other bytes", JOIN, 'return None if other.read_bytes() == first.read_bytes() else', "return None if True else"),
    ("the pipe is not checked", JOIN, '        if p.returncode != 0 or p.timed_out or other.read_bytes() != first.read_bytes():', "        if False:"),
    ("the --query form is not fingerprinted", JOIN, '        acc, why = jref.check_output(other, qid)\n        if why is None:\n            why = jref.compare(qid, ctx.reference[qid], acc)', '        acc, why = None, None'),
    ("the launch measures the wrong clock", JOIN, "    t0 = clock()\n    try:\n        with open(dest", "    t0 = 0.0\n    try:\n        with open(dest"),
    ("a launch keeps all of stderr", JOIN, "(p.stderr or b\"\")[:4096]", "(p.stderr or b\"\")"),
    ("a left join is asked as an inner join (DuckDB)", JOIN, 'kind = "LEFT JOIN" if q["kind"] == "left" else "JOIN"', 'kind = "JOIN"'),
    ("the right table's key is selected too (DuckDB)", JOIN, 'for c in q["right_cols"])\n    kind', 'for c in q["right_cols"] + [q["key"]])\n    kind'),
    ("a left join is asked as an inner join (csvtk)", JOIN, '*(["-L"] if q["kind"] == "left" else [])', "*[]"),
    ("the unpaired rows are dropped (Miller)", JOIN, '        argv += ["--ur"]', "        pass"),
    ("the merge flag is not given", JOIN, '        argv = ["--join-sorted", *argv]', "        pass"),
    ("the sorted contender sorts by the wrong column", JOIN, "ctx.sorted_cache[k] = jgen.sorted_variant(ctx.paths[table], q[\"key\"], ctx.tmp)", "ctx.sorted_cache[k] = jgen.sorted_variant(ctx.paths[table], \"id1\", ctx.tmp)"),
    ("a wrong answer does not fail the step", JOIN, '            status = 4\n            emit("GATE FAILED: a wrong answer is in the result")', '            emit("GATE FAILED: a wrong answer is in the result")'),
    ("a large step runs without the opt-in", JOIN, "    if bn.needs_opt_in(rows) and not a.accept_large:", "    if False:"),
    ("a directory inside the repository is used", JOIN, "    if gen.inside_repo(out_dir):", "    if False:"),
    ("a reused set is not checked against its sha256", JOIN, "bn.sha256_of(paths[t]) == meta[\"tables\"][t][\"sha256\"]", "True"),
    ("the disk is not checked before generating", JOIN, "    if not bn.disk_ok(bn._free_bytes(out_dir), need):", "    if False:"),
    ("a question that is not a join is run", JOIN, "        if qid not in jref.JOINS:\n            raise bn.Refusal(\"bench.question\"", "        if False:\n            raise bn.Refusal(\"bench.question\""),
    ("the refusal report calls every call answered", JOIN, '            if doc.get("ok"):', "            if True:"),
    ("the refusal report keeps the tool's bound off", JOIN, '        if bound is not None:\n            trimmed += ["--max-state-bytes", str(bound)]', "        if False:\n            trimmed += []"),
    ("the refusal report keeps the design's bound on", JOIN, 'if a in ("--max-state-bytes", "--format", "--max-join-rows", "--max-rows"):', 'if a in ("--format", "--max-join-rows", "--max-rows"):'),
]

PROGRAM = {
    "the right table's key is selected too (DuckDB)": FORMS,
    "a left join is asked as an inner join (DuckDB)": FORMS,
    "a wrong answer does not fail the step": ["test_bench_named_join.Program.test_g_answer_end_to_end_each_wrong_stub_is_reported_and_not_timed"],
    "a large step runs without the opt-in": ["test_bench_named_join.Program.test_the_large_steps_need_an_explicit_opt_in_and_print_the_estimate_first"],
    "a directory inside the repository is used": ["test_bench_named_join.Program.test_refusals_with_a_tag"],
    "a reused set is not checked against its sha256": ["test_bench_named_join.Program.test_a_reused_set_is_checked_against_its_sha256"],
    "the disk is not checked before generating": ["test_bench_named_join.Program.test_not_enough_disk_is_refused_before_generating"],
    "a question that is not a join is run": ["test_bench_named_join.Program.test_refusals_with_a_tag"],
    "the refusal report calls every call answered": ["test_bench_named_join.Program.test_the_refusal_report_is_in_the_result"],
    "the refusal report keeps the tool's bound off": ["test_bench_named_join.Program.test_the_refusal_report_is_in_the_result"],
    "the refusal report keeps the design's bound on": ["test_bench_named_join.Program.test_the_refusal_report_is_in_the_result", "test_bench_named_join.Program.test_the_default_bounds_answer_at_this_size"],
    "a left join is asked as an inner join (csvtk)": FORMS,
    "the unpaired rows are dropped (Miller)": FORMS,
    "the merge flag is not given": FORMS,
    "the sorted contender sorts by the wrong column": FORMS,
}
# Not here, and why (mutants of the first full run that survived and were proved equivalent, so removed; the others of the seven each got a test and were killed):
#  - "a right table's key need not be unique in the layout" (`len(right) != len(right_col) or len(right) != n_keys` reduced to the second): the number of rows of every table has been
#    checked equal to its key count before this line, so `len(right_col)` is `n_keys` and the two conditions are one.
#  - "an inner join may have an empty v2" (the refusal of an empty v2 in an inner join, deleted): an empty v2 then counts as an unmatched row, which adds to `count` and `sum_v1` and not to
#    `matched`, and an inner join's reference has `matched == count`: the fingerprint refuses the answer by `compare` instead of `check_output`; the verdict is the same, only the message.
#  - "the layout need not have a fifth of the keys on each side" (the left-only and right-only counts, deleted): the left set has n distinct values and so has the right (both checked
#    just above) and the common count is checked equal to n - n // 10, so the two differences are n // 10 each by arithmetic.
# Mutants of a form that the string tests of `Forms` also see are killed without a binary; the ones above need the contender run for real.

# Not here, and why: nothing yet. Survivors of a run are listed in docs/named-benchmarks.md, "NB3 as built", each proved equivalent or killed by a test added after it.


def build_tree(dest):
    """scripts/ and the tests copied to `dest`, so that a mutant never touches the working tree."""
    shutil.copytree(HERE, dest / "scripts", ignore=shutil.ignore_patterns("__pycache__", "demo", "*.html"))
    (dest / "tests" / "conformance").mkdir(parents=True)
    for name in TESTS:
        shutil.copy(ROOT / "tests" / "conformance" / name, dest / "tests" / "conformance" / name)


def run_tests(tree, tests):
    env = dict(os.environ)
    env.setdefault("TOOLBOX_BIN", str(ROOT / "build"))
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-f", *tests], cwd=tree / "tests" / "conformance", capture_output=True, text=True, timeout=3600, env=env)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return p.returncode, ", ".join(failed[:3])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    table = list(MUTANTS)
    problems = mutlib.check([(n, f, o, r) for n, f, o, r in table], HERE)
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
    sources = {f: (HERE / f).read_text() for f in {m[1] for m in table}}
    survivors = []
    with tempfile.TemporaryDirectory(prefix="bench-named-join-mut-") as d:
        tree = pathlib.Path(d)
        build_tree(tree)
        rc, why = run_tests(tree, UNIT)
        print("unmutated:", "pass" if rc == 0 else "FAILED [%s]" % why, flush=True)
        if rc != 0:
            return 1
        for name, file, old, new in chosen:
            (tree / "scripts" / file).write_text(sources[file].replace(old, new, 1))
            rc, why = run_tests(tree, PROGRAM.get(name, UNIT))
            print("%-14s %s  [%s]" % ("killed" if rc else "SURVIVED", name, why), flush=True)
            if rc == 0:
                survivors.append(name)
            (tree / "scripts" / file).write_text(sources[file])
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
