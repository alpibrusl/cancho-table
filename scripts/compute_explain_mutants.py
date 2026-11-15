#!/usr/bin/env python3
"""Mutation check of `--explain` for a `--compute` stage (docs/functions.md section 3.1, slice K5a): `computex.cho`, its place in `explain.cho`, and the accessors it
uses in `cexpr.cho`, broken against tests/conformance/test_compute_explain.py (the certain refusals, the plan against the run, the possible refusals, the rows not read).

    python3 scripts/compute_explain_mutants.py [name-substring ...]
    python3 scripts/compute_explain_mutants.py --check        # every site still matches (no build)

A mutant is killed when a test fails or the build refuses it. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_compute_explain"]

MUTANTS = [
    ("the scale of a definition is one more", "computex.cho", "stage = buffer.push_nat(heap, stage, cexpr.scale_of(contents(pr), d));", "stage = buffer.push_nat(heap, stage, cexpr.scale_of(contents(pr), d) + 1);"),
    ("the instructions of a definition are none", "computex.cho", "stage = buffer.push_nat(heap, stage, cexpr.size_of(contents(pr), d));", "stage = buffer.push_nat(heap, stage, 0);"),
    ("the names do not follow the input columns", "computex.cho", "                    labels = buffer.push(heap, labels, byte_of(','));\n                    labels = text.append_json(heap, labels, buffer.bytes(nmr));\n", ""),
    ("a division is always possible", "computex.cho", "        if divisions {", "        if true {"),
    ("an integer cell is never refused", "computex.cho", "        if int_cells {", "        if false {"),
    ("a decimal cell is never refused", "computex.cho", "        if dec_cells {", "        if false {"),
    ("an overflow is never possible", "computex.cho", "        if operators {", "        if false {"),
    ("the definitions are not compiled against the header", "explain.cho", 'if cli.has(parsed, table, "compute") && got {', 'if false {'),
    ("a definition that does not compile is explained", "explain.cho", "        if cstatus != 0 {\n            e = compute.refuse(heap, e, cstatus, coffset, 0, 0, 0, shown);", "        if false {\n            e = compute.refuse(heap, e, cstatus, coffset, 0, 0, 0, shown);"),
    ("the stage's rules are not added to the list", "explain.cho", "    o = buffer.append(heap, o, extra_rules);\n", ""),
    ("the stage member is not written", "explain.cho", "    if len(stage) > 0 {\n        o = buffer.append(heap, o, \",\\\"stage\\\":\");", "    if len(stage) > 1000000000 {\n        o = buffer.append(heap, o, \",\\\"stage\\\":\");"),
    ("the answer's columns are the input's only", "explain.cho", "        if has_labels {", "        if false {"),
    ("an operator is not found in a definition", "cexpr.cho", "        if what == 0 && (op == op_add() || op == op_sub() || op == op_mul() || op == op_div()) {", "        if what == 0 && (op == op_mul() || op == op_div()) {"),
    ("a division is found in every definition with an operator", "cexpr.cho", "        if what == 1 && op == op_div() {", "        if what == 1 && (op == op_div() || op == op_mul()) {"),
    ("the stage is said to be read by threads", "explain.cho", '        } else if len(stage) > 0 {\n            why = "the --compute stage runs on one core";', '        } else if len(stage) > 1000000000 {\n            why = "the --compute stage runs on one core";'),
    ("the stage has an SQL form", "qsql.cho", '    if cli.has(parsed, table, "compute") {\n        return (out, extra, 11);\n    }\n', ""),
    ("--explain is refused beside --compute again", "table.cho", 'inp.has_query || inp.has_sort || inp.has_top || inp.has_rename || cli.has(parsed, table, "skip-lines")', 'inp.has_query || inp.has_explain || inp.has_sort || inp.has_top || inp.has_rename || cli.has(parsed, table, "skip-lines")'),
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
