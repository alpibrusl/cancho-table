#!/usr/bin/env python3
"""Mutation check of `add_days` in `--compute` (docs/functions.md section 3.5, slice K8b): the leaf of the text evaluator (`ctext.cho`), the parser of its arguments (`cexpr.cho`), the row's use of a
refused cell (`compute.cho`) and the explain (`computex.cho`), broken against tests/conformance/test_compute_adddays.py (every date a day later and earlier, and random pairs, against Python's `datetime`).

    python3 scripts/cadddays_mutants.py [name-substring ...]
    python3 scripts/cadddays_mutants.py --check        # every site still matches (no build)

A mutant is killed when a test fails or the build refuses it. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_compute_adddays"]

MUTANTS = [
    ("add_days adds one day too few", "ctext.cho", "arena = dtparse.put(heap, arena, days + amount, 22);", "arena = dtparse.put(heap, arena, days + amount - 1, 22);"),
    ("the first date is the last one before it", "ctext.cho", "days + amount < -719162", "days + amount < -719163"),
    ("the first date is refused", "ctext.cho", "days + amount < -719162", "days + amount < -719161"),
    ("the last date is refused", "ctext.cho", "days + amount > 2932896", "days + amount > 2932895"),
    ("the day after the last date is accepted", "ctext.cho", "days + amount > 2932896", "days + amount > 2932897"),
    ("a date out of range is not refused", "ctext.cho", "if status == 0 && (days + amount < -719162 || days + amount > 2932896) {", "if false {"),
    ("a date cell is read as a literal", "ctext.cho", "            if a / 32 % 2 == 0 {\n                let (dv, dst)", "            if a / 32 % 2 == 1 {\n                let (dv, dst)"),
    ("a count cell is read as a literal", "ctext.cho", "if status == 0 && a / 64 % 2 == 0 {", "if status == 0 && a / 64 % 2 == 1 {"),
    ("the status of a date cell has no reason", "ctext.cho", "status = 1000 + dst;", "status = 1000;"),
    ("a count cell that is not an integer is a decimal", "ctext.cho", "status = 100 + nst;", "status = 200 + nst;"),
    ("the column of a refused cell is the first", "ctext.cho", "                    contents(sw)[0] = col;", "                    contents(sw)[0] = 0;"),
    ("the line limit is not checked for a date", "ctext.cho", "                if e0 - s0 > cap {\n                    status = 17;\n                }\n                borrow mut stk as &!sw in {\n                    contents(sw)[2 * sp] = s0;\n                    contents(sw)[2 * sp + 1] = e0;\n                }\n                sp = sp + 1;\n            } else {", "                if e0 - s0 > cap + 1000000000 {\n                    status = 17;\n                }\n                borrow mut stk as &!sw in {\n                    contents(sw)[2 * sp] = s0;\n                    contents(sw)[2 * sp + 1] = e0;\n                }\n                sp = sp + 1;\n            } else {"),
    ("the column of a refused text cell is not taken", "compute.cho", "                    cc = tb;", "                    cc = 0;"),
    ("the sign of a literal count is lost", "cdatep.cho", "        return (le, sign * v, 0, true);", "        return (le, v, 0, true);"),
    ("a literal count is flagged as a column", "cdatep.cho", "            if lit {\n                a = a + 64;\n            }", "            if false {\n                a = a + 64;\n            }"),
    ("a decimal column is a count", "cdatep.cho", "            if tst != 0 || tk != 0 {\n                status = 16;", "            if tst != 0 {\n                status = 16;"),
    ("a number expression takes add_days", "cexpr.cho", "                        // add_days makes a text: no conversion either way\n                        status = 16;", "                        // add_days makes a text: no conversion either way\n                        status = 1;"),
    ("add_days is not a text definition", "cexpr.cho", "    return fid >= 11 && fid <= 18 || fid == 26;", "    return fid >= 11 && fid <= 18;"),
    ("add_days takes one operand", "cdate.cho", "    if id >= 25 {\n        return 2;", "    if id == 25 {\n        return 2;"),
    ("the explain does not list the integer cell of a count", "cexpr.cho", "        if what == 2 && op == ctext.op_add_days() && prog[at + 1] / 64 % 2 == 0 {", "        if what == 2 && op == ctext.op_add_days() && prog[at + 1] / 64 % 2 == 1 {"),
    ("the explain lists the date cell of a literal", "cexpr.cho", "        if what == 5 && op == ctext.op_add_days() && prog[at + 1] / 32 % 2 == 0 {", "        if what == 5 && op == ctext.op_add_days() && prog[at + 1] / 32 % 2 == 1 {"),
    ("the explain does not list the date range", "computex.cho", "        if adds {\n            rules = put_rule(heap, rules, \"compute.date-out-of-range\");", "        if false {\n            rules = put_rule(heap, rules, \"compute.date-out-of-range\");"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS, sys.argv[1:]))
