#!/usr/bin/env python3
"""Mutation check of the comparison of two columns (docs/column-comparison.md; tools/table/expr.cho, orparse.cho, engine.cho, jnflags.cho, jnwhere.cho, explain.cho): each mutant is one
deliberate defect in the reader of `COLUMN OP ( COLUMN )`, in the comparison, in the cell a refusal names, or in what keeps the condition out of a loop that does not know it, rebuilt, and
run against tests/conformance/test_column_comparison.py. A mutant is killed when a test fails or the build refuses it.

    python3 scripts/column_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no build; CI runs it). See mutlib.py. MUT_SHARD=I/N runs the I-th of N parts of the list (each in a checkout of its own: a run
rebuilds tools/table in place).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_column_comparison"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the comparison
    ("< is <=", "expr.cho", "    if op == 2 {\n        return c < 0;", "    if op == 2 {\n        return c <= 0;"),
    ("> is >=", "expr.cho", "    if op == 4 {\n        return c > 0;", "    if op == 4 {\n        return c >= 0;"),
    ("!= is =", "expr.cho", "    if op == 1 {\n        return c != 0;", "    if op == 1 {\n        return c == 0;"),
    ("= is !=", "expr.cho", "    if op == 0 {\n        return c == 0;", "    if op == 0 {\n        return c != 0;"),
    ("text is compared as a number's key", "expr.cho", "    if typed == 0 {\n        return (bool_int(ordered(op, bytes.compare(a, b))), 0);", "    if typed == 99 {\n        return (bool_int(ordered(op, bytes.compare(a, b))), 0);"),
    ("the right cell is compared with itself", "expr.cho", "    var c = 0;\n    if x < y {", "    var c = 0;\n    if x < x {"),
    # the type of a cell
    ("a float cell's verdict is shifted", "expr.cho", "            return (0, bad - 2);\n        }\n        return (key, 0);", "            return (0, bad - 1);\n        }\n        return (key, 0);"),
    ("a date is read as a decimal", "expr.cho", "    if typed >= 22 {\n        let (x, bad) = dtparse.parse(cell, typed);\n        return (x, bad);", "    if typed >= 99 {\n        let (x, bad) = dtparse.parse(cell, typed);\n        return (x, bad);"),
    # the cell a refusal names
    ("a bad right cell is named as the left", "expr.cho", "        return (bad_b, 1);", "        return (bad_b, 0);"),
    ("a bad left cell is not refused", "expr.cho", "    if bad_a != 0 {\n        return (bad_a, 0);\n    }", "    if bad_a != 0 {\n        return (1, 0);\n    }"),
    ("the program does not say which side was refused", "expr.cho", "                if side == 1 {\n                    return (v3, 0 - 1 - k, scr);\n                }", "                if side == 99 {\n                    return (v3, 0 - 1 - k, scr);\n                }"),
    ("a refusal names the left column whichever side", "engine.cho", "        cond = 0 - 1 - which;\n        field = 4;", "        cond = 0 - 1 - which;\n        field = 3;"),
    ("a refusal of the right cell names the wrong condition", "engine.cho", "        cond = 0 - 1 - which;", "        cond = 0 - which;"),
    # the cells
    ("the right column is the left", "expr.cho", "    let right = cols[query.cond_at(plan, k, 4)];", "    let right = cols[query.cond_at(plan, k, 3)];"),
    ("a quoted right cell is not unquoted", "expr.cho", "    if cells[3 * right + 2] == 1 &&", "    if cells[3 * right + 2] == 99 &&"),
    # the reader
    ("the types of the two columns may differ", "expr.cho", "            if what == 0 && typed2 != typed {", "            if what == 0 && typed2 == 99 {"),
    ("the closing parenthesis is not asked for", "expr.cho", "                if i < len(src) && int_of(src[i]) == ')' {\n                    i = i + 1;\n                    between = true;", "                if i < len(src) {\n                    i = i + 1;\n                    between = true;"),
    ("the column is recorded as the left one's name", "expr.cho", "query.add_cond(heap, plan, 3, typed, op, index, index2, 0, begins)", "query.add_cond(heap, plan, 3, typed, op, index, index, 0, begins)"),
    # what keeps it out of the loops that do not know it
    ("the program is not asked for", "orparse.cho", "                    if query.last_cond_kind(pr) == 3 {", "                    if query.last_cond_kind(pr) == 99 {"),
    ("a plan with a column comparison runs the program that does not know it", "expr.cho", "        if query.has_columns(plan) {", "        if false {"),
    ("the program of a column comparison is told by the wrong parity", "query.cho", "    return vec.size(q.flow) % 2 == 1;", "    return vec.size(q.flow) % 2 == 0;"),
    ("the program does not meet a column comparison", "expr.cho", "        if query.cond_at(plan, k, 0) == 3 {\n            // two columns", "        if query.cond_at(plan, k, 0) == 99 {\n            // two columns"),
    ("--join-where takes it", "jnflags.cho", "                if what == 0 && query.has_between(tr) {\n                    what = 32;", "                if what == 0 && query.has_between(tr) {\n                    what = 0;"),
    ("the join pushes it to a side", "jnwhere.cho", "if kind == 1 || query.cond_at(tree, leaf, 1) != 0 || query.cond_at(tree, leaf, 0) == 3 {", "if kind == 1 || query.cond_at(tree, leaf, 1) != 0 {"),
    ("--explain does not say the column", "explain.cho", "            o = buffer.append(heap, o, \"\\\",\\\"against\\\":\");", "            o = buffer.append(heap, o, \"\\\",\\\"agai\\\":\");"),
]

if __name__ == "__main__":
    shard = os.environ.get("MUT_SHARD")
    chosen = MUTANTS
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        chosen = [m for k, m in enumerate(MUTANTS) if k % n == i]
    sys.exit(mutlib.main(chosen, TESTS))
