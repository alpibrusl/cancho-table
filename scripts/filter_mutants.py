#!/usr/bin/env python3
"""Mutation check of `--where`, `--group` and `--agg` (docs/filter.md): each mutant is
one source file of tools/table with one deliberate defect, rebuilt, and run against the
plan, filter, select, differential, limit and rule tests. A mutant is killed when a test
fails or the build refuses it. The files are restored after every mutant, whatever
happens.

    python3 scripts/filter_mutants.py [name-substring ...]

Run where a compiler is (`lex-sys` on PATH, or LEX_SYS; LEX_SYS_ARGS="--ignore-compiler-rev"
for a compiler of another revision). Exit status 1 if one survives. Do not edit
tools/table while it runs.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "table"
COMPILER = os.environ.get("LEX_SYS", "lex-sys")
EXTRA = os.environ.get("LEX_SYS_ARGS", "").split()
TESTS = ["test_plan", "test_filter", "test_select", "test_rules"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # exact integers
    ("2^63 is accepted as a positive integer", "query.ls", "    if acc == int_min() {\n        return (0, 2);", "    if false && acc == int_min() {\n        return (0, 2);"),
    ("a minus sign is a plus", "query.ls", "negative = int_of(data[0]) == '-';", "negative = int_of(data[0]) == '+';"),
    ("a sign alone is zero", "query.ls", "    if at >= len(data) {\n        return (0, 1);\n    }", "    if false && at >= len(data) {\n        return (0, 1);\n    }"),
    ("a sum that reaches the maximum is refused", "query.ls", "if b > 0 && a > int_max() - b {", "if b > 0 && a >= int_max() - b {"),
    ("a sum that reaches the minimum is refused", "query.ls", "if b < 0 && a < int_min() - b {", "if b < 0 && a <= int_min() - b {"),
    ("an integer past 64 bits wraps", "query.ls", "        if acc < (int_min() + digit) / 10 {\n            over = true;", "        if acc < (int_min() + digit) / 10 && false {\n            over = true;"),
    # the grammar
    ("an escaped :int is still a suffix", "expr.ls", "} else if length >= 4 && escaped_last <= length - 4 {", "} else if length >= 4 && escaped_last <= length {"),
    ("= on an :int column is not equality", "expr.ls", "            if kind == 2 || op == 0 {\n                yes = v == w;", "            if kind == 2 {\n                yes = v == w;"),
    ("<= is < on text", "expr.ls", "            yes = c <= 0;", "            yes = c < 0;"),
    ("<= is < on integers", "expr.ls", "                yes = v <= w;", "                yes = v < w;"),
    ("a condition after a false one is still evaluated", "expr.ls", "        if verdict != 1 {\n            return (verdict, k, scr);", "        if verdict >= 2 {\n            return (verdict, k, scr);"),
    ("64 conditions is not the ceiling", "expr.ls", "        if many >= 64 {", "        if many >= 65 {"),
    ("1024 values is not the ceiling", "expr.ls", "if values > 1024 {", "if values > 2000 {"),
    ("in () with a missing paren is accepted", "expr.ls", "                    } else {\n                        what = 7;", "                    } else if false {\n                        what = 7;"),
    ("contains is case-insensitive for the keyword", "expr.ls", 'is_contains = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), "contains");', 'is_contains = status == 0 && bytes.equal(buffer.bytes(wr), "contains");'),
    # groups
    ("min is max", "agg.ls", "} else if function == 2 && v < now {", "} else if function == 2 && v > now {"),
    ("the first value of a group is not its minimum", "agg.ls", "} else if before == 0 {", "} else if before == 1 {"),
    ("a value is distinct every time", "agg.ls", "known = map.find(sr, buffer.bytes(dr)) >= 0;", "known = false;"),
    ("one more distinct value is allowed", "agg.ls", "if distinct_pairs >= max_distinct {", "if distinct_pairs > max_distinct {"),
    ("one more group is allowed", "agg.ls", "if size >= max_groups {", "if size > max_groups {"),
    ("the state bound is not kept", "agg.ls", "} else if bytes_held + buffer.size(kr) > max_state {", "} else if bytes_held + buffer.size(kr) > max_state + 100000000 {"),
    ("groups sort descending by key", "agg.ls", "            if c < 0 {\n                return 0 - 1;\n            }\n            return 1;", "            if c < 0 {\n                return 1;\n            }\n            return 0 - 1;"),
    # (Not here: making the merge unstable. The order is total -- ties are broken by the keys, which are
    # all different -- so stability cannot be seen; the mutant is equivalent.)
    ("zero is written -0", "agg.ls", "    if v >= 0 {\n        return buffer.push_nat(heap, out, v);", "    if v > 0 {\n        return buffer.push_nat(heap, out, v);"),
    ("a sum that overflows is not noticed", "agg.ls", "                        if fits {\n                            a = set_at(a, at + 1 + k, sum);", "                        if true {\n                            a = set_at(a, at + 1 + k, sum);"),
    # --sort
    ("- does not mean descending", "frame.ls", "if int_of(sort[0]) == '-' {", "if int_of(sort[0]) == '+' {"),
    ("count is sorted by the wrong slot", "frame.ls", "            } else if query.agg_at(tree, found - ng, 0) == 0 {\n                sort_slot = 0;", "            } else if query.agg_at(tree, found - ng, 0) == 0 {\n                sort_slot = 1;"),
    # the read
    ("the refusals of a grouping are numbered one low", "table.ls", "a[k_abort()] = 12 + status;", "a[k_abort()] = 11 + status;"),
    ("a full page holds one row more with --where", "table.ls", "    if a[k_emitted()] >= limit {\n        // The page is full and this row matches: there is more.", "    if a[k_emitted()] > limit {\n        // The page is full and this row matches: there is more."),
    ("the next of a filtered page is one past", "table.ls", "        a[k_next()] = a[k_records()] - 1;\n        a[k_stop()] = 1;\n        return (rows, scratch, e2, k2);", "        a[k_next()] = a[k_records()];\n        a[k_stop()] = 1;\n        return (rows, scratch, e2, k2);"),
    ("a page is declared full at a row that does not match", "table.ls", "mode == 1 && !filtering && a[k_records()] >= from", "mode == 1 && a[k_records()] >= from"),
    ("--top is ignored", "table.ls", "            if top > 0 && top < n {", "            if top > n {"),
    ("the number of groups is not reported", "table.ls", "    a[k_groups()] = n;", "    a[k_groups()] = 0;"),
    ("--select and --group do not conflict", "table.ls", "    if grouped && has_select {", "    if false && grouped && has_select {"),
    ("the row of a refused cell is one low", "table.ls", "    a[k_err_row()] = a[k_records()];\n    a[k_err_line()] = opened;\n    a[k_stop()] = 1;", "    a[k_err_row()] = a[k_records()] - 1;\n    a[k_err_line()] = opened;\n    a[k_stop()] = 1;"),
    ("a refused value is kept whole", "table.ls", "    if last - first > 64 {", "    if last - first > 6400 {"),
    ("every unknown name is in --select", "table.ls", "            if c.bad_name >= ns + nw + ng {", "            if c.bad_name >= ns + nw + ng + 5 {"),
    ("a grouping with no --agg counts nothing", "table.ls", "            tree = query.add_agg(heap, tree, 0, -1);", "            tree = query.add_agg(heap, tree, 1, 0);"),
]

FILES = {n: (SRC / n).read_text() for n in {m[1] for m in MUTANTS}}


def restore(*_):
    for n, text in FILES.items():
        (SRC / n).write_text(text)


def build_and_test():
    b = subprocess.run([COMPILER, "build", "--bin", "table", *EXTRA], cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "does not build", b.stderr.strip()[-140:]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", *TESTS], cwd=ROOT / "tests" / "conformance",
                       capture_output=True, text=True, timeout=1800)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main():
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    wanted = sys.argv[1:]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[0] for w in wanted)]
    verdict, _ = build_and_test()
    print("unmutated:", "pass" if verdict == "SURVIVED" else verdict, flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for name, file, old, new in chosen:
        if FILES[file].count(old) != 1:
            print("!! %s: the site occurs %d times in %s" % (name, FILES[file].count(old), file))
            return 1
        try:
            (SRC / file).write_text(FILES[file].replace(old, new, 1))
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-14s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict == "SURVIVED":
            survivors.append(name)
    for n, text in FILES.items():
        assert (SRC / n).read_text() == text
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
