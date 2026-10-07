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
    ("two scales of one column are not a conflict", "query.ls", "                } else if other != first {", "                } else if (other == 1) != (first == 1) {"),
    ("an --order-by key does not count as an :int reading", "query.ls", "        if order_flags(q, k) / 2 % 2 == 1 && cols[order_name(q, k)] == column {", "        if false && order_flags(q, k) / 2 % 2 == 1 && cols[order_name(q, k)] == column {"),
    ("a type conflict is not reported", "frame.ls", "    } else if conflict >= 0 {", "    } else if false && conflict >= 0 {"),
    # what a refusal says
    ("a decimal refusal takes the code of a limit", "engine.ls", "        code = 18 + verdict;", "        code = 17 + verdict;"),
    ("the digits of a too-fine cell are one too many", "engine.ls", "        a[k_err_digits()] = n;", "        a[k_err_digits()] = n + 1;"),
    ("the repair rewrites the first condition, not the one that refused", "table.ls", "query.cond_at(tree, c.err_cond, 6), digits);", "0, digits);"),
    ("the repair drops what follows the scale", "table.ls", "    return buffer.append(heap, out, given[end..len(given)]);", "    return out;"),
    ("the scale of a refusal is the type code", "table.ls", "            scale = query.cond_at(tree, c.err_cond, 1) - 2;", "            scale = query.cond_at(tree, c.err_cond, 1) - 1;"),
    # stage N2: the aggregates of a decimal column (dec.ls, agg.ls)
    ("a mean rounds half up", "dec.ls", "            if inexact || limbs[0] & 1 == 1 {", "            if true {"),
    ("an exact half rounds up only when something was left over", "dec.ls", "            if inexact || limbs[0] & 1 == 1 {", "            if inexact {"),
    ("the half bit of a mean is not looked at", "dec.ls", "        let bit = limbs[0] & 1;", "        let bit = 0;"),
    ("a mean at a larger scale is not multiplied", "dec.ls", "            mul_small(limbs, 8, p);", "            mul_small(limbs, 8, 1);"),
    ("a remainder of the division by the scale is forgotten", "dec.ls", "            if div_small(limbs, 8, p) != 0 {\n                inexact = true;", "            if false && div_small(limbs, 8, p) != 0 {\n                inexact = true;"),
    ("a remainder of the division by the count is forgotten", "dec.ls", "        var inexact = div_small(limbs, 8, count) != 0;", "        var inexact = div_small(limbs, 8, count) < 0;"),
    ("the sign of a negative mean is lost", "dec.ls", "        o = emit(heap, o, d, n, scale, high < 0 && nonzero);", "        o = emit(heap, o, d, n, scale, false);"),
    # (Not here: a sum written `-` when it is zero. A negative high half is a value of at most -1, so there is no zero to write -0.00: put_sum has no test for it.)
    ("a mean that rounds to zero from below is written -0.00", "dec.ls", "        o = emit(heap, o, d, n, scale, high < 0 && nonzero);", "        o = emit(heap, o, d, n, scale, high < 0);"),
    ("the point is one digit off", "dec.ls", "        if i == scale && scale > 0 {", "        if i == scale + 1 && scale > 0 {"),
    ("a value smaller than one is not padded", "dec.ls", "    while count < scale + 1 {", "    while count < scale {"),
    ("the leading zeros of a group of nine are kept", "dec.ls", "    while count > scale + 1 && int_of(d[count - 1]) == '0' {", "    while count > scale + 1 && false && int_of(d[count - 1]) == '0' {"),
    ("the magnitude of a negative sum is one too large", "dec.ls", "            // the magnitude: 0 - (high * 2^32 + low)\n            if low == 0 {\n                h = 0 - high;\n            } else {\n                h = 0 - high - 1;", "            // the magnitude: 0 - (high * 2^32 + low)\n            if low == 0 {\n                h = 0 - high;\n            } else {\n                h = 0 - high;"),
    ("a mean is compared as a sum", "dec.ls", "magnitude_times(hx, lx, cy);", "magnitude_times(hx, lx, 1);"),
    ("the order of two negative means is not reversed", "dec.ls", "    if sx < 0 {\n        return 0 - out;", "    if false {\n        return 0 - out;"),
    ("distinct counts the text of a decimal", "agg.ls", "                    if query.agg_at(tree, k, 2) != 0 {\n                        // by value", "                    if false {\n                        // by value"),
    ("an aggregate reads a decimal cell as an integer, in place", "agg.ls", "let (v2, bad2) = query.parse_typed(record[cells[3 * column]..cells[3 * column + 1]], query.agg_at(tree, k, 2));", "let (v2, bad2) = query.parse_typed(record[cells[3 * column]..cells[3 * column + 1]], 1);"),
    ("the cell of the first aggregate is used for all the others", "agg.ls", "            if column != seen_column {\n                seen_column = column;", "            if seen_column < 0 {\n                seen_column = column;"),
    ("an integer cell in place is read as a decimal of scale 0", "agg.ls", "                    let (v2, bad2) = query.parse_int(record[cells[3 * column]..cells[3 * column + 1]]);\n                    v = v2;\n                    bad = 0;", "                    let (v2, bad2) = query.parse_int(record[cells[3 * column]..cells[3 * column + 1]]);\n                    v = v2 + 1;\n                    bad = 0;"),
    ("an aggregate reads a decimal cell as an integer, by value", "agg.ls", "typed_cell(heap, scr, record, first, last, quoted, query.agg_at(tree, k, 2));\n                    scr = s3;", "typed_cell(heap, scr, record, first, last, quoted, 1);\n                    scr = s3;"),
    ("too many fractional digits is said to be not a decimal, in an aggregate", "query.ls", "        if bad == 3 {\n            return (0, 7);", "        if bad == 3 {\n            return (0, 6);"),
    ("a mean of a decimal defaults to scale 0", "agg.ls", "        var to = scale;", "        var to = 0;"),
    ("@N is one too high", "agg.ls", "            to = mean - 1;", "            to = mean;"),
    ("a minimum is written without its scale", "agg.ls", "    if (function == 2 || function == 3) && query.agg_at(tree, k, 2) >= 2 {", "    if (function == 2 || function == 3) && false {"),
    ("a sum is written without its scale", "agg.ls", "            return dec.put_sum(heap, out, high, low, scale);", "            return dec.put_sum(heap, out, high, low, 0);"),
    ("@N takes one digit only", "agg.ls", "    if digits >= 1 && digits <= 2 && k >= 1 && int_of(item[k - 1]) == '@'", "    if digits >= 1 && digits <= 1 && k >= 1 && int_of(item[k - 1]) == '@'"),
    ("the mean of an integer column needs no scale", "agg.ls", "                    if kind < 2 && mean == 0 {\n                        bad = i;", "                    if false && kind < 2 && mean == 0 {\n                        bad = i;"),
    ("a suffix is taken with no column before it", "agg.ls", " && index_of_byte(item[0..e - 4], byte_of(':')) > 0 {\n        kind = 1;", " && index_of_byte(item[0..e - 4], byte_of(':')) > -2 {\n        kind = 1;"),
    ("a scale of 19 is taken", "agg.ls", "            if v > 18 {\n                kind = 99;", "            if v > 19 {\n                kind = 99;"),
    ("an escaped colon is still a suffix", "agg.ls", "bytes.equal(item[e - 4..e], \":int\") && !escaped_at(item, e - 4) &&", "bytes.equal(item[e - 4..e], \":int\") &&"),
    ("a mean sorts as a sum", "frame.ls", "                    sort_slot = sort_slot + 1000000;", "                    sort_slot = sort_slot + 0;"),
    ("a mean is labelled sum", "frame.ls", "                            labels = buffer.append(heap, labels, \"mean\");", "                            labels = buffer.append(heap, labels, \"sum\");"),
    ("an implicit :int does not count as a reading of the column", "query.ls", "        if agg_at(q, k, 0) != 0 && agg_at(q, k, 2) != 0 && cols[agg_at(q, k, 1)] == column {", "        if agg_at(q, k, 0) != 0 && agg_at(q, k, 2) >= 2 && cols[agg_at(q, k, 1)] == column {"),
    ("a decimal refusal of an aggregate takes the code of another", "engine.ls", "        return 20 + status;", "        return 19 + status;"),
    ("the refusal an in-place aggregate answered is decoded in eights", "engine.ls", "    let status = (answer - 16) % 16;", "    let status = (answer - 16) % 8;"),
    ("the digits are counted for the wrong refusal", "engine.ls", "    if status == 7 {\n        a[k_err_digits()]", "    if status == 6 {\n        a[k_err_digits()]"),
    ("a mean refusal says sum", "engine.ls", "        function = 5;", "        function = 1;"),
    ("the scale of an aggregate refusal is the type code", "table.ls", "            scale = query.agg_at(tree, c.err_cond, 2) - 2;", "            scale = query.agg_at(tree, c.err_cond, 2) - 1;"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
