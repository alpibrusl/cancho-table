#!/usr/bin/env python3
"""Mutation check of `:float` (docs/numbers.md, stage N3a): each mutant is one source file of tools/table with one deliberate defect, rebuilt, and run
against the float, rule and decimal tests. A mutant is killed when a test fails or the build refuses it. The files are restored after every mutant.

    python3 scripts/float_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_float", "test_rules", "test_numbers"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the key and its inverse (flt.cho)
    ("the key of a negative double is not flipped", "flt.cho", "    if bits < 0 {\n        return bits ^ 0x7fffffffffffffff;\n    }\n    return bits;", "    if bits < 0 {\n        return bits;\n    }\n    return bits;"),
    # (Not here: `key_of` answering 0 for a zero. It is only called with a non-zero double: a cell of only zeros is answered as key 0 before it, which is how -0 is 0.)
    ("a cell of only zeros goes the long way", "flt.cho", "    if !nonzero {\n        return (0, 0);\n    }", "    if !nonzero {\n        return (0, 12);\n    }"),
    ("a subnormal is built from the wrong power of two", "flt.cho", "        x = math.ldexp(float_of(f), 0 - 1074);", "        x = math.ldexp(float_of(f), 0 - 1073);"),
    ("a normal double is built without its hidden bit", "flt.cho", "float_of(f | 0x10000000000000), e - 1075);", "float_of(f), e - 1075);"),
    ("a normal double is built with the wrong bias", "flt.cho", "float_of(f | 0x10000000000000), e - 1075);", "float_of(f | 0x10000000000000), e - 1074);"),
    ("the sign of a double is lost on the way back", "flt.cho", "    if bits < 0 {\n        return 0.0 - x;\n    }\n    return x;", "    return x;"),
    # reading
    ("a cell of 1,101 bytes is read", "flt.cho", "    if n > 1100 {", "    if n > 1101 {"),
    ("nan is not finite only in lower case", "flt.cho", "        return word_is(data, at, \"inf\") || word_is(data, at, \"nan\");", "        return word_is(data, at, \"inf\") || word_is(data, at, \"nax\");"),
    ("a capital letter is not folded", "flt.cho", "            c = c + 32;\n        }\n        if c != int_of(word[i]) {", "            c = c + 33;\n        }\n        if c != int_of(word[i]) {"),
    ("the fast path keeps one digit too many", "flt.cho", "    if !dropped && m >= 0 && m <= 9007199254740992 &&", "    if !dropped && m >= 0 && m <= 90071992547409920 &&"),
    ("the fast path reads powers of ten past 22", "flt.cho", "e10 >= 0 - 22 && e10 <= 22 {", "e10 >= 0 - 23 && e10 <= 22 {"),
    ("a negative power of ten multiplies", "flt.cho", "            x = x / pow10_float[0 - e10];", "            x = x * pow10_float[0 - e10];"),
    ("the digits dropped after the point are not counted", "flt.cho", "        fraction = at - point_at - 1;", "        fraction = at - point_at;"),
    ("a number past the largest double is read", "flt.cho", "    if bits >= 9218868437227405312 {\n        return (0, 11);", "    if false && bits >= 9218868437227405312 {\n        return (0, 11);"),
    ("a number that rounds to zero is read as zero", "flt.cho", "    if bits == 0 {\n        return (0, 12);", "    if bits == 0 {\n        return (0, 0);"),
    ("a zero with a huge exponent goes the long way", "flt.cho", "    if !nonzero {\n        return (0, 0);\n    }", "    if false && !nonzero {\n        return (0, 0);\n    }"),
    ("the rounding of a subnormal is dropped (Eisel-Lemire)", "flt.cho", "        mant = mant + (mant & 1);\n        return mant >> 1;", "        return mant >> 1;"),
    ("an exact halfway of the exact tier rounds up always", "flt.cho", "if round_bit == 1 && (low || kept & 1 == 1) {", "if round_bit == 1 {"),
    ("an exact halfway of the exact tier rounds down always", "flt.cho", "if round_bit == 1 && (low || kept & 1 == 1) {", "if round_bit == 1 && low {"),
    ("the remainder of the exact tier's division by a power of ten is forgotten", "flt.cho", "                    if dec.div_small(a, nl, p) != 0 {\n                        sticky = true;\n                    }", "                    dec.div_small(a, nl, p);"),
    ("the window of 19 digits is 20 (the accumulator wraps)", "flt.cho", "            if nd < 19 {", "            if nd < 20 {"),
    ("the bits of a normal double are one too many", "flt.cho", "    return (mant & 4503599627370495) + (power2 << 52);", "    return (mant & 4503599627370495) + (power2 << 52) + 1;"),
    # writing
    ("a number of 1e21 is written positionally", "flt.cho", "        if k >= 21 || k < 0 - 6 {", "        if k >= 22 || k < 0 - 6 {"),
    ("a number below 1e-6 is written positionally", "flt.cho", "        if k >= 21 || k < 0 - 6 {", "        if k >= 21 || k < 0 - 7 {"),
    ("a whole number is written without its point", "flt.cho", "                o = buffer.append(heap, o, \".0\");", "                o = buffer.append(heap, o, \"\");"),
    ("a number below one has a zero too few after the point", "flt.cho", "                while z < 0 - point {", "                while z < 0 - point - 1 {"),
    ("zero is written without its point", "flt.cho", "        return buffer.append(heap, out, \"0.0\");", "        return buffer.append(heap, out, \"0\");"),
    # the condition and the plan
    ("the literal that is not finite is said to be not a number", "expr.cho", "                } else if bad == 10 {\n                    what = 19;", "                } else if bad == 10 {\n                    what = 18;"),
    ("a float verdict takes the code of another", "expr.cho", "            return bad - 2;", "            return bad - 1;"),
    ("a float suffix is a suffix after an escape", "expr.cho", "            if length >= 6 && escaped_last <= length - 6 {", "            if length >= 6 {"),
    ("a float refusal takes the abort code of a decimal's", "engine.cho", "        code = 22 + verdict;", "        code = 21 + verdict;"),
    ("an aggregate's float refusal takes another code", "engine.cho", "        return 28 + status;", "        return 27 + status;"),
    ("the direction of a range refusal is swapped", "table.cho", "            w = fail.detail_str(heap, w, \"direction\", \"overflow\");", "            w = fail.detail_str(heap, w, \"direction\", \"underflow\");"),
    ("a float refusal of --where is read as the status of the next", "table.cho", "        var status = c.abort - 20;", "        var status = c.abort - 21;"),
    ("an aggregate's float suffix is an integer one", "agg.cho", "        kind = 21;\n        e = e - 6;", "        kind = 1;\n        e = e - 6;"),
    ("a float minimum is written as an integer", "agg.cho", "(function == 2 || function == 3) && query.agg_at(tree, k, 2) == 21 {", "(function == 2 || function == 3) && query.agg_at(tree, k, 2) == 22 {"),
    ("a float cell of an aggregate is read as an integer", "query.cho", "    if kind == 21 {\n        // :float (flt.cho)", "    if kind == 22 {\n        // :float (flt.cho)"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
