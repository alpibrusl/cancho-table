#!/usr/bin/env python3
"""Mutation check of `:dec(S)` in `--where` (docs/numbers.md, stage N1): each mutant is one source file of tools/table with one
deliberate defect, rebuilt, and run against the numbers, plan, filter, rule and parallel tests. A mutant is killed when a test
fails or the build refuses it. The files are restored after every mutant, whatever happens.

    python3 scripts/numbers_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_numbers", "test_plan", "test_rules", "test_filter"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the cell reader (query.parse_dec)
    ("a cell with too many fractional digits is accepted", "query.ls", "    if frac > scale {\n        return (0, 3);\n    }", "    if false && frac > scale {\n        return (0, 3);\n    }"),
    ("the width limit is 10^18 * 10", "query.ls", "            if v >= 100000000000000000 {\n                wide = true;", "            if v >= 1000000000000000000 {\n                wide = true;"),
    ("the padding to the scale is not checked for width", "query.ls", "        if v > 999999999999999999 / pow10(pad) {", "        if false && v > 999999999999999999 / pow10(pad) {"),
    ("a short cell is not padded to the scale", "query.ls", "        v = v * pow10(pad);", "        v = v + 0 * pow10(pad);"),
    ("the padding is one digit short", "query.ls", "        let pad = scale - frac;", "        let pad = scale - frac - 1;"),
    ("a width error outranks a grammar error", "query.ls", "                wide = true;\n            } else {", "                return (0, 2);\n            } else {"),
    ("a sign or a point alone is zero", "query.ls", "    if digits == 0 {\n        return (0, 1);\n    }\n    if frac > scale {", "    if false && digits == 0 {\n        return (0, 1);\n    }\n    if frac > scale {"),
    ("a second point is accepted", "query.ls", "        } else if c == '.' && !seen_point {", "        } else if c == '.' {"),
    ("a minus sign is a plus, in a decimal", "query.ls", "        negative = int_of(data[0]) == '-';\n        at = 1;\n    }\n    var v = 0;", "        negative = int_of(data[0]) == '+';\n        at = 1;\n    }\n    var v = 0;"),
    ("the digits after the point are not counted", "query.ls", "            if seen_point {\n                frac = frac + 1;\n            }", "            if seen_point {\n                frac = frac + 0;\n            }"),
    # the grammar of the suffix and of literals (expr.ls)
    ("a decimal literal is read at scale 0", "expr.ls", "let (v, bad) = query.parse_dec(buffer.bytes(wr), typed - 2);", "let (v, bad) = query.parse_dec(buffer.bytes(wr), 0);"),
    ("a literal finer than the scale is accepted", "expr.ls", "                } else if bad == 3 {\n                    what = 14;", "                } else if false {\n                    what = 14;"),
    ("a literal that is too wide is accepted", "expr.ls", "                } else if bad == 2 {\n                    what = 15;", "                } else if false {\n                    what = 15;"),
    ("a scale of 19 is accepted", "expr.ls", "    if digits > 2 || value > 18 {", "    if digits > 2 || value > 19 {"),
    ("a scale is read one too high", "expr.ls", "    return (2 + value, i + 1, 0, 0);", "    return (3 + value, i + 1, 0, 0);"),
    ("the closing parenthesis of the scale is not required", "expr.ls", "    if i >= len(src) || int_of(src[i]) != ')' {\n        return (0, p, 16, i);\n    }", "    if false && (i >= len(src) || int_of(src[i]) != ')') {\n        return (0, p, 16, i);\n    }"),
    ("contains is allowed on a :dec column", "expr.ls", "    if is_contains && typed != 0 {", "    if is_contains && typed == 1 {"),
    # the comparison and the verdicts
    ("<= is < on decimals", "expr.ls", "\n            yes = v <= w;", "\n            yes = v < w;"),
    # (Not here: `if kind == 2 || op == 0` written `if op == 0`. An `in` is parsed with op 0, so the two are the same: equivalent.)
    ("an in-list of decimals is not an equality test", "expr.ls", "\n        if kind == 2 || op == 0 {\n            yes = v == w;", "\n        if op == 0 && kind != 2 {\n            yes = v == w;"),
    ("a decimal is compared as text", "expr.ls", "    if typed >= 2 {\n        return holds_dec(", "    if typed >= 3 {\n        return holds_dec("),
    ("a cell too fine for the scale is said not to be a decimal", "expr.ls", "    if bad == 3 {\n        return 5;", "    if bad == 3 {\n        return 4;"),
    ("a cell too wide is said to be too fine", "expr.ls", "    if bad == 2 {\n        return 6;", "    if bad == 2 {\n        return 5;"),
    # the type conflict
    ("a sum does not count as an :int reading", "query.ls", "        if (f == 1 || f == 2 || f == 3) && cols[agg_at(q, k, 1)] == column {", "        if (f == 2 || f == 3) && cols[agg_at(q, k, 1)] == column {"),
    ("two scales of one column are not a conflict", "query.ls", "                } else if other != first {", "                } else if (other == 1) != (first == 1) {"),
    ("an --order-by key does not count as an :int reading", "query.ls", "        if order_flags(q, k) / 2 % 2 == 1 && cols[order_name(q, k)] == column {", "        if false && order_flags(q, k) / 2 % 2 == 1 && cols[order_name(q, k)] == column {"),
    ("a type conflict is not reported", "frame.ls", "    } else if conflict >= 0 {", "    } else if false && conflict >= 0 {"),
    # what a refusal says
    ("a decimal refusal takes the code of a limit", "engine.ls", "        code = 18 + verdict;", "        code = 17 + verdict;"),
    ("the digits of a too-fine cell are one too many", "engine.ls", "        a[k_err_digits()] = n;", "        a[k_err_digits()] = n + 1;"),
    ("the repair rewrites the first condition, not the one that refused", "table.ls", "query.cond_at(tree, c.err_cond, 6), digits);", "0, digits);"),
    ("the repair drops what follows the scale", "table.ls", "    return buffer.append(heap, out, given[end..len(given)]);", "    return out;"),
    ("the scale of a refusal is the type code", "table.ls", "        let scale = query.cond_at(tree, c.err_cond, 1) - 2;", "        let scale = query.cond_at(tree, c.err_cond, 1) - 1;"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
