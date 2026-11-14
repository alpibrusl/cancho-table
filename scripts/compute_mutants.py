#!/usr/bin/env python3
"""Mutation check of the stage `--compute` (docs/computed-columns.md section 7, G-MUT): the reader (`compute.cho`) and its place in `table.cho` broken in one way each, rebuilt and run
against test_compute (the reference of Python integers through the binary, the pages, the rules, the combinations), test_rules and test_describe and test_skill (the rule table and the
help). The arithmetic and the grammar have their own mutants (scripts/cmpv_golden.py --mutants, scripts/cexpr_golden.py --mutants).

    python3 scripts/compute_mutants.py [name-substring ...]
    python3 scripts/compute_mutants.py --check        # every site still matches (no build; CI runs it)

A mutant is killed when a test fails or the build refuses it. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_compute", "test_rules", "test_describe", "test_skill"]

MUTANTS = [
    ("the stage is not dispatched", "table.cho", 'if cli.has(parsed, flag_table(), "compute") {', 'if false {'),
    ("the frame is made without the computed names", "compute.cho", "ncols + nd, mode, delim", "ncols, mode, delim"),
    ("a computed cell starts one byte late", "compute.cho", "contents(cw2)[3 * (ncols + d)] = start;", "contents(cw2)[3 * (ncols + d)] = start + 1;"),
    ("a computed cell is empty", "compute.cho", "contents(cw2)[3 * (ncols + d) + 1] = end;", "contents(cw2)[3 * (ncols + d) + 1] = start;"),
    ("a refused definition is not reported as abort", "compute.cho", "                                    a[engine.k_abort()] = 90;\n", ""),
    ("a refused row goes on reading", "compute.cho", "rline = number;\n                                                going = false;", "rline = number;"),
    ("the refused row of a record on several lines is the wrong one", "compute.cho", "rline = opened;", "rline = 0;"),
    ("the copy keeps the previous row", "compute.cho", "    borrow mut copy2 as &!cw in {\n        buffer.clear(cw);\n    }\n", ""),
    ("the definition's name is not unescaped", "compute.cho", "names = cexpr.name_of(heap, names, text, contents(pr), d);", "names = buffer.append(heap, names, \"x\");"),
    ("standard input is read as the empty file", "table.cho", "rel, full, stdin, delim,", "rel, full, false, delim,"),
    ("JSON lines are allowed", "table.cho", "inp.has_rename || inp.jsonl ||", "inp.has_rename ||"),
    ("a selection is allowed", "table.cho", "if computing && (has_select || has_where ||", "if computing && (has_where ||"),
    ("a reader option is allowed", "table.cho", 'cli.has(parsed, table, "skip-lines") || ', ""),
    ("the rows are not the rows plan", "table.cho", "inp.has_rows || inp.has_join || computing {", "inp.has_rows || inp.has_join {"),
]

# Survivors that are explained (none yet): an entry is (name, file, old, new, reason) and its site must still exist.
EQUIVALENT = []

if __name__ == "__main__":
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS, TESTS))
