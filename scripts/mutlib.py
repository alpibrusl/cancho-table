"""The shared half of the mutation scripts (select_mutants, filter_mutants, parallel_mutants, cellcost_mutants).

A mutant is (name, file, the text replaced, its replacement) against a source file of tools/table. Two things this
module is for, after the scripts had gone stale without anyone being told:

* `--check` (no compiler, no build, no tests: it is cheap enough for CI) verifies that every mutant's text occurs
  exactly once in its file, and exits 1, naming each one that does not ("mutant X: pattern not found in F"),
  otherwise;
* a run does that check first, for every mutant, before building anything: a mutant that cannot apply is a failure,
  never a skip, and the same goes for a named subset (`NAME ...` selects among mutants that all apply).

    python3 scripts/X_mutants.py --check
    python3 scripts/X_mutants.py [name-substring ...]

A run needs a compiler (`lex-sys` on PATH, or LEX_SYS; LEX_SYS_ARGS="--ignore-compiler-rev" for a compiler of
another revision), restores the sources after every mutant whatever happens, stops a mutant's tests at the first
failure (killed), and exits 1 if one survives. The binary left in build/ is the last mutant's: rebuild after a run.
Do not edit tools/table while it runs.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "table"


def check(mutants, src=None):
    """Problems (a list of strings): a pattern missing, occurring more than once, or a mutant that changes nothing."""
    src = SRC if src is None else src
    texts = {}
    problems = []
    names = set()
    for name, file, old, new in mutants:
        if name in names:
            problems.append("mutant %s: named twice" % name)
        names.add(name)
        if file not in texts:
            path = src / file
            texts[file] = path.read_text() if path.exists() else None
        text = texts[file]
        if text is None:
            problems.append("mutant %s: no such file %s" % (name, file))
            continue
        n = text.count(old)
        if n == 0:
            problems.append("mutant %s: pattern not found in %s" % (name, file))
        elif n > 1:
            problems.append("mutant %s: pattern occurs %d times in %s (it must be unique)" % (name, n, file))
        if old == new:
            problems.append("mutant %s: replacement equals the pattern" % name)
    return problems


def build_and_test(tests, compiler, extra):
    b = subprocess.run([compiler, "build", "--bin", "table", *extra], cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "does not build", b.stderr.strip()[-140:]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-f", *tests], cwd=ROOT / "tests" / "conformance",
                       capture_output=True, text=True, timeout=3600)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main(mutants, tests, argv=None):
    argv = sys.argv[1:] if argv is None else argv
    problems = check(mutants)
    for p in problems:
        print("!! " + p)
    if problems:
        print("%d of %d mutants cannot be applied" % (len(problems), len(mutants)))
        return 1
    if "--check" in argv:
        print("%d mutants: every pattern occurs exactly once" % len(mutants))
        return 0
    wanted = [a for a in argv if not a.startswith("--")]
    chosen = [m for m in mutants if not wanted or any(w in m[0] for w in wanted)]
    if wanted and not chosen:
        print("!! no mutant matches %r" % wanted)
        return 1
    compiler = os.environ.get("LEX_SYS", "lex-sys")
    extra = os.environ.get("LEX_SYS_ARGS", "").split()
    files = {n: (SRC / n).read_text() for n in {m[1] for m in mutants}}

    def restore(*_):
        for n, text in files.items():
            (SRC / n).write_text(text)

    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    verdict, why = build_and_test(tests, compiler, extra)
    print("unmutated:", "pass" if verdict == "SURVIVED" else "%s [%s]" % (verdict, why), flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for name, file, old, new in chosen:
        try:
            (SRC / file).write_text(files[file].replace(old, new, 1))
            verdict, why = build_and_test(tests, compiler, extra)
        finally:
            restore()
        print("%-14s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict == "SURVIVED":
            survivors.append(name)
    for n, text in files.items():
        assert (SRC / n).read_text() == text
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0
