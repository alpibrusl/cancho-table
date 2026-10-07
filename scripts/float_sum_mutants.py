#!/usr/bin/env python3
"""Mutation check of the exact float `sum` and `mean` (docs/numbers.md, stage N4): each mutant is one source file of tools/table with one deliberate defect,
rebuilt, and run against the float-sum, float and rule tests. A mutant is killed when a test fails or the build refuses it. The files are restored after every mutant.

    python3 scripts/float_sum_mutants.py [name-substring ...]
    python3 scripts/float_sum_mutants.py --parallel-only      the plain-f64 mutants against test_float_sum.Parallel alone (they die at N >= 2 by themselves)
    python3 scripts/float_sum_mutants.py --lowered            builds with the carry threshold lowered to 3 and to 1 addition: they must PASS every test (the rep_check of the spike)

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.

Equivalent mutants (they survive, and must): see EQUIVALENT below, each with the reason.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_float_sum", "test_float", "test_rules"]

PLAIN_FAST = ("facc.add_bits(g.acc, facc_at, bits_of_key(v));",
              "{ let (cur, over) = facc.finalize_key(g.acc, facc_at); let s = flt.float_of_key(cur) + flt.float_of_key(v); var z = 0; while z < 73 { vec.set(g.acc, facc_at + z, 0); z = z + 1; } if s != 0.0 { facc.add_bits(g.acc, facc_at, bits_of_key(flt.key_of(s))); } }")
PLAIN_SLOW = ("        facc.add_bits(w, at, bits_of_key(key));",
              "        let (cur, over) = facc.finalize_key(w, at);\n        let s = flt.float_of_key(cur) + flt.float_of_key(key);\n        var z = 0;\n        while z < 73 {\n            vec.set(w, at + z, 0);\n            z = z + 1;\n        }\n        if s != 0.0 {\n            facc.add_bits(w, at, bits_of_key(flt.key_of(s)));\n        }")

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the plain double sum: the accumulator holds one double and rounds after every addition (the thread-dependent answer the tool exists to avoid)
    ("plain f64 addition on the fast path", "agg.cho", *PLAIN_FAST),
    ("plain f64 addition on the slow path", "agg.cho", *PLAIN_SLOW),
    # adding a double (facc.cho)
    ("the hidden bit of a normal double is not added", "facc.cho", "        m = m | 0x10000000000000;", "        m = m | 0;"),
    ("a normal double's exponent is off by one", "facc.cho", "        p = e - 1;", "        p = e;"),
    ("a negative double's top piece is added, not subtracted", "facc.cho", "        vec.set(acc, i + 2, vec.get(acc, i + 2) - c2);", "        vec.set(acc, i + 2, vec.get(acc, i + 2) + c2);"),
    ("a negative double's low piece is added, not subtracted", "facc.cho", "        vec.set(acc, i, vec.get(acc, i) - c0);", "        vec.set(acc, i, vec.get(acc, i) + c0);"),
    ("the middle piece forgets the high mantissa", "facc.cho", "    let c1 = (a >> 32) + (b & 0xffffffff);", "    let c1 = (a >> 32);"),
    ("the top piece is the mantissa shifted by 31", "facc.cho", "    let c2 = b >> 32;", "    let c2 = b >> 31;"),
    ("the shift inside the limb is mod 30", "facc.cho", "    let s = p & 31;", "    let s = p & 30;"),
    ("the limb is one too high", "facc.cho", "    let i = at + (p >> 5);", "    let i = at + (p >> 5) + 1;"),
    ("the low piece is not masked", "facc.cho", "    let c0 = a & 0xffffffff;", "    let c0 = a;"),
    ("the exponent field is 10 bits", "facc.cho", "    let e = bits >> 52 & 0x7ff;", "    let e = bits >> 52 & 0x3ff;"),
    # the carry
    ("the carry stops at limb 69", "facc.cho", "    while i < 71 {\n        let x = vec.get(acc, at + i) + carry;", "    while i < 69 {\n        let x = vec.get(acc, at + i) + carry;"),
    ("the carry is of 31 bits", "facc.cho", "        carry = x >> 32;\n        i = i + 1;\n    }\n    vec.set(acc, at + 71, vec.get(acc, at + 71) + carry);", "        carry = x >> 31;\n        i = i + 1;\n    }\n    vec.set(acc, at + 71, vec.get(acc, at + 71) + carry);"),
    ("the carry is lost at the top", "facc.cho", "    vec.set(acc, at + 71, vec.get(acc, at + 71) + carry);", "    vec.set(acc, at + 71, vec.get(acc, at + 71));"),
    ("a limb is masked to 31 bits", "facc.cho", "        vec.set(acc, at + i, x & 0xffffffff);", "        vec.set(acc, at + i, x & 0x7fffffff);"),
    # the rounding
    ("the top bit is found one too low", "facc.cho", "    let top = 32 * h + bit_length(t[h]) - 1;", "    let top = 32 * h + bit_length(t[h]) - 2;"),
    ("53 bits are kept, then one more", "facc.cho", "    var lsb = top - 52;", "    var lsb = top - 51;"),
    ("a subnormal keeps its 53 bits", "facc.cho", "    if lsb < unit {\n        lsb = unit;\n    }\n    var m = 0;", "    var m = 0;"),
    ("a tie goes up always", "facc.cho", "    if guard == 1 && (rest || m & 1 == 1) {", "    if guard == 1 {"),
    ("a tie goes down always", "facc.cho", "    if guard == 1 && (rest || m & 1 == 1) {", "    if guard == 1 && rest {"),
    ("a tie goes to odd", "facc.cho", "    if guard == 1 && (rest || m & 1 == 1) {", "    if guard == 1 && (rest || m & 1 == 0) {"),
    ("the sticky bit is ignored", "facc.cho", "    if guard == 1 && (rest || m & 1 == 1) {", "    if guard == 1 && m & 1 == 1 {"),
    ("the sticky scan skips a bit", "facc.cho", "        var j = lsb - 2;", "        var j = lsb - 3;"),
    ("the guard bit is the lowest kept", "facc.cho", "        guard = bit_at(t, lsb - 1);", "        guard = bit_at(t, lsb);"),
    ("the smallest bit of the limbs is dropped", "facc.cho", "    if pos < 0 {\n        return 0;\n    }\n    return t[pos >> 5]", "    if pos < 1 {\n        return 0;\n    }\n    return t[pos >> 5]"),
    ("a result of zero is a negative zero", "facc.cho", "    if m == 0 {\n        return (0, 0);\n    }\n    if m >= 4503599627370496 {", "    if m >= 4503599627370496 {"),
    ("a sum of nothing is not zero", "facc.cho", "    if h < 0 {\n        return (0, 0);\n    }", "    if h < 0 {\n        return (1, 0);\n    }"),
    ("the biased exponent is off by one", "facc.cho", "        let biased = q + 1075;", "        let biased = q + 1074;"),
    ("the largest double plus half an ulp is not an overflow", "facc.cho", "        if biased >= 2047 {", "        if biased >= 2048 {"),
    ("an overflow is one exponent early", "facc.cho", "        if biased >= 2047 {", "        if biased >= 2046 {"),
    ("an overflow is not told", "facc.cho", "            return (0, 1);\n        }\n        bits = biased", "            return (0, 0);\n        }\n        bits = biased"),
    ("the key of a negative sum is not flipped", "facc.cho", "        return (bits + (0 - 9223372036854775807 - 1) ^ 0x7fffffffffffffff, 0);", "        return (bits + (0 - 9223372036854775807 - 1), 0);"),
    ("the sign of a sum is lost", "facc.cho", "    if negative {\n        return (bits + (0 - 9223372036854775807 - 1) ^", "    if false {\n        return (bits + (0 - 9223372036854775807 - 1) ^"),
    ("a negative sum is not negated limb by limb with a borrow", "facc.cho", "            x = carry - x;\n            carry = x >> 32;", "            x = 0 - x;\n            carry = 0;"),
    ("a sum is negative when its top limb is zero", "facc.cho", "    let negative = vec.get(acc, at + 71) < 0;", "    let negative = vec.get(acc, at + 71) <= 0;"),
    ("the accumulator is not carried before it is read", "facc.cho", "    normalize(acc, at);\n    var key = 0;\n    var over = 0;", "    var key = 0;\n    var over = 0;"),
    # the mean
    ("the mean is of a sum shifted by 63 bits", "facc.cho", "        let negative = magnitude(acc, at, t, 2);", "        let negative = magnitude(acc, at, t, 1);"),
    ("the mean's unit is 63 fractional bits", "facc.cho", "        let (k2, o2) = round_key(t, 73, 64, rem != 0, negative);", "        let (k2, o2) = round_key(t, 73, 63, rem != 0, negative);"),
    ("the mean divides the high 16 bits by 15", "facc.cho", "            let cur_hi = rem * 65536 + (limb >> 16);", "            let cur_hi = rem * 65536 + (limb >> 15);"),
    ("the mean divides the low 16 bits masked to 15", "facc.cho", "            let cur_lo = rem * 65536 + (limb & 0xffff);", "            let cur_lo = rem * 65536 + (limb & 0x7fff);"),
    ("the mean's remainder is not carried to the next digit", "facc.cho", "            rem = cur_hi - q_hi * n;", "            rem = 0;"),
    ("the mean starts from the lowest limb", "facc.cho", "        var j = 72;\n        while j >= 0 {", "        var j = 71;\n        while j >= 0 {"),
    # the plan: where each accumulator is, how it is counted, rounded, merged and named
    ("an accumulator is 72 integers", "facc.cho", "pub fn width() -> [] int {\n    return 73;", "pub fn width() -> [] int {\n    return 72;"),
    ("a float sum is not counted in the row", "agg.cho", "    return 1 + 2 * query.agg_count(tree) + facc.width() * floats_in(tree);", "    return 1 + 2 * query.agg_count(tree);"),
    ("a float sum is a sum of an integer column", "agg.cho", "    return query.agg_at(tree, k, 0) == 1 && query.agg_at(tree, k, 2) == 21;", "    return query.agg_at(tree, k, 0) == 1 && query.agg_at(tree, k, 2) == 22;"),
    ("the state of a float sum is half what it is", "agg.cho", "    return 8 * facc.width() * floats_in(tree);", "    return 4 * facc.width() * floats_in(tree);"),
    ("the state of a float sum is not counted for a new group", "agg.cho", "            } else if bytes_held + buffer.size(kr) + float_state(tree) > max_state {\n                status = 3;\n            } else {\n                bytes_held = bytes_held + buffer.size(kr) + float_state(tree);", "            } else if bytes_held + buffer.size(kr) > max_state {\n                status = 3;\n            } else {\n                bytes_held = bytes_held + buffer.size(kr);"),
    ("the state of a float sum is not held in the held bytes", "agg.cho", "                bytes_held = bytes_held + buffer.size(kr) + float_state(tree);", "                bytes_held = bytes_held + buffer.size(kr);"),
    ("the key of a negative cell is not turned back into bits", "agg.cho", "    if key < 0 {\n        return key ^ 0x7fffffffffffffff;\n    }\n    return key;\n}\n\nfn float_add", "    return key;\n}\n\nfn float_add"),
    ("the accumulators start on the slots of the sums' halves", "agg.cho", "        var facc_at = at + 1 + 2 * na;", "        var facc_at = at + 1 + na;"),
    ("the fast path's accumulators start on the sums' halves", "agg.cho", "    var facc_at = slot + 1 + 2 * na;", "    var facc_at = slot + 1 + na;"),
    ("the second float sum shares the first one's accumulator on the slow path", "agg.cho", "            if is_float_sum(tree, k) {\n                facc_at = facc_at + facc.width();\n            }\n            k = k + 1;\n        }\n    }\n    return (Groups", "            k = k + 1;\n        }\n    }\n    return (Groups"),
    ("the second float sum shares the first one's accumulator on the fast path", "agg.cho", "                facc.add_bits(g.acc, facc_at, bits_of_key(v));\n                facc_at = facc_at + facc.width();", "                facc.add_bits(g.acc, facc_at, bits_of_key(v));"),
    ("a float sum is also added as an integer pair on the fast path", "agg.cho", "                facc.add_bits(g.acc, facc_at, bits_of_key(v));\n                facc_at = facc_at + facc.width();\n            } else if function == 1 {", "                facc.add_bits(g.acc, facc_at, bits_of_key(v));\n                facc_at = facc_at + facc.width();\n                vec.set(g.acc, slot + 1 + k, now + 1);\n            } else if function == 1 {"),
    ("a mean is settled as a sum", "agg.cho", "                if query.agg_at(tree, k, 3) != 0 {\n                    key = facc.mean_key(", "                if query.agg_at(tree, k, 3) == 0 {\n                    key = facc.mean_key("),
    ("a mean is divided by the first group's rows", "agg.cho", "                    key = facc.mean_key(g.acc, facc_at, vec.get(g.acc, x * g.stride));", "                    key = facc.mean_key(g.acc, facc_at, vec.get(g.acc, 0));"),
    ("a mean is divided by one more", "agg.cho", "                    key = facc.mean_key(g.acc, facc_at, vec.get(g.acc, x * g.stride));", "                    key = facc.mean_key(g.acc, facc_at, vec.get(g.acc, x * g.stride) + 1);"),
    ("the overflow named is of the largest key", "agg.cho", "compare_keys(map.key_at(g.index, x), map.key_at(g.index, worst), query.count_of(tree, 2)) < 0) {", "compare_keys(map.key_at(g.index, x), map.key_at(g.index, worst), query.count_of(tree, 2)) > 0) {"),
    ("the overflow is named by its first group found", "agg.cho", "if over != 0 && (worst < 0 || compare_keys(", "if over != 0 && (worst < 0 || false && compare_keys("),
    ("the overflow of the aggregate after the first in a group is the one named", "agg.cho", "                    worst = x;\n                    worst_k = k;", "                    worst = x;\n                    worst_k = na - 1;"),
    ("an overflow is not looked for", "agg.cho", "                    over = o2;\n                }\n                vec.set(g.acc, x * g.stride + 1 + k, key);", "                    over = 0;\n                }\n                vec.set(g.acc, x * g.stride + 1 + k, key);"),
    ("the settled sum is kept in the slot of the next aggregate", "agg.cho", "                vec.set(g.acc, x * g.stride + 1 + k, key);\n                facc_at = facc_at + facc.width();", "                vec.set(g.acc, x * g.stride + 2 + k, key);\n                facc_at = facc_at + facc.width();"),
    ("the second float aggregate settles the first's accumulator", "agg.cho", "                vec.set(g.acc, x * g.stride + 1 + k, key);\n                facc_at = facc_at + facc.width();", "                vec.set(g.acc, x * g.stride + 1 + k, key);"),
    ("a float sum is written as an integer", "agg.cho", "    if function == 1 && query.agg_at(tree, k, 2) == 21 {\n        return flt.put_float(heap, out, vec.get(g.acc, x * g.stride + 1 + k));", "    if function == 1 && query.agg_at(tree, k, 2) == 22 {\n        return flt.put_float(heap, out, vec.get(g.acc, x * g.stride + 1 + k));"),
    ("a float sum is written from the next aggregate's slot", "agg.cho", "        return flt.put_float(heap, out, vec.get(g.acc, x * g.stride + 1 + k));", "        return flt.put_float(heap, out, vec.get(g.acc, x * g.stride + k));"),
    ("a float mean takes a scale", "agg.cho", "                    if mean != 0 {\n                        bad = i;\n                    } else {\n                        mean_final = 100;", "                    if false {\n                        bad = i;\n                    } else {\n                        mean_final = mean;"),
    ("a float mean is a sum in the plan", "agg.cho", "                    } else {\n                        mean_final = 100;\n                    }\n                } else if is_mean {", "                    } else {\n                        mean_final = 0;\n                    }\n                } else if is_mean {"),
    # merging the ranges of threads
    ("a range's float limbs are merged without carrying the group's first", "agg.cho", "                                    facc.normalize(aw, entry * stride + facc_at);\n                                    var l = 0;", "                                    var l = 0;"),
    ("a range's float limbs are merged without carrying after", "agg.cho", "                                    }\n                                    facc.normalize(aw, entry * stride + facc_at);\n                                }", "                                    }\n                                }"),
    ("a range's top limb is not merged", "agg.cho", "                                    while l < 72 {\n                                        vec.set(aw, entry", "                                    while l < 71 {\n                                        vec.set(aw, entry"),
    ("a range's limbs are merged at the wrong offset", "agg.cho", "get_i64(blob, acc_at + 8 * (facc_at + l)));", "get_i64(blob, acc_at + 8 * (facc_at + l + 1)));"),
    ("a range's second float accumulator is merged into the first", "agg.cho", "                                    facc.normalize(aw, entry * stride + facc_at);\n                                }\n                                facc_at = facc_at + facc.width();", "                                    facc.normalize(aw, entry * stride + facc_at);\n                                }"),
    ("a range's float sum is not merged", "agg.cho", "                            if function == 1 && query.agg_at(tree, k, 2) == 21 {\n                                // an exact float sum", "                            if false && function == 1 && query.agg_at(tree, k, 2) == 21 {\n                                // an exact float sum"),
    ("a new group's float state is not counted in a merge", "agg.cho", "                    new_bytes = new_bytes + klen + float_state(tree);", "                    new_bytes = new_bytes + klen;"),
    # the plan's words, the refusal, the order
    ("the overflow is the exit code of a limit", "table.cho", "                a[engine.k_abort()] = 42;", "                a[engine.k_abort()] = 15;"),
    ("the groups are written after an overflow", "table.cho", "            if a[engine.k_abort()] == 0 {\n                borrow groups as &gr in {\n                    let (r2, s2) = finish_groups", "            if true {\n                borrow groups as &gr in {\n                    let (r2, s2) = finish_groups"),
    ("the overflow names the first group column only as its key's bytes", "table.cho", "                            kept = buffer.push(heap, kept, byte_of(','));\n                        }\n                        kept = buffer.append(heap, kept, agg.key_field(gr, x, j));", "                            kept = buffer.push(heap, kept, byte_of(';'));\n                        }\n                        kept = buffer.append(heap, kept, agg.key_field(gr, x, j));"),
    ("the overflow names the wrong group", "table.cho", "                        kept = buffer.append(heap, kept, agg.key_field(gr, x, j));", "                        kept = buffer.append(heap, kept, agg.key_field(gr, 0, j));"),
    ("the overflow names the wrong column", "table.cho", "                    a[engine.k_err_col()] = contents(kr)[query.agg_at(tree, k, 1)];", "                    a[engine.k_err_col()] = 0;"),
    ("a float mean is sorted by its exact fraction", "frame.cho", "                if query.agg_at(tree, found - ng, 3) != 0 && query.agg_at(tree, found - ng, 2) != 21 {", "                if query.agg_at(tree, found - ng, 3) != 0 {"),
    ("the high half of a sort key is read from the wrong place", "agg.cho", "        let hx = vec.get(acc, x * stride + slot + na);", "        let hx = vec.get(acc, x * stride + slot + na + 1);"),
]

# They survive, and must (the reasons are in docs/numbers.md, N4):
EQUIVALENT = [
    # (a mutant that is not in MUTANTS because it cannot be told apart by any input, with why)
    ("the carry threshold set to 2^62 additions", "a limb is at most 2^27 * 2^33 = 2^60 before a carry; with no carry at all it would reach 2^63 only after 2^30 additions of the largest pieces, and `--max-rows` is at most 10^9 < 2^30: equivalent within the ceiling (scripts/spikes/rep_check.py runs the 2.4 billion version outside the tool)"),
    ("a carry of every addition", "slower, never different: the carry is exact"),
    ("the counter is not reset by a carry", "an earlier carry only: exact"),
    ("the renormalisation of a mantissa that rounds up to 2^53", "the bits of 2^53 * 2^q are the bits of 2^52 * 2^(q+1): the exponent field absorbs it"),
    ("the mean without the sticky bit", "a remainder that is not zero cannot sit under an exact tie (a tie differs from the quotient by at least 1/(2n) of a unit, 2^-41 at least, and 2^-64 is the bit being dropped)"),
    ("the room for 64 groups' accumulators in a worker's answer", "performance only: a range whose groups do not fit is read by the parent, the same bytes"),
]

# Builds that are NOT defective and must pass every test: the carry threshold lowered (the test-only constant of the stage table's `rep_check`). With the
# threshold at 3 additions, or 1, the carry runs between nearly every addition, so the carry, the sign limb and the counter are tested by the whole suite;
# at 2^27 they are only reached by 134 million additions of one group (scripts/spikes/rep_check.py does that outside the tool).
LOWERED = [
    ("carry every 3 additions", "facc.cho", "    if n >= 134217728 {", "    if n >= 3 {"),
    ("carry after every addition", "facc.cho", "    if n >= 134217728 {", "    if n >= 1 {"),
]


def lowered(argv):
    import os
    import signal
    problems = mutlib.check(LOWERED)
    for p in problems:
        print("!! " + p)
    if problems or "--check" in argv:
        return 1 if problems else 0
    compiler = os.environ.get("CANCHO", "cancho")
    extra = os.environ.get("CANCHO_ARGS", "").split()
    text = (mutlib.SRC / "facc.cho").read_text()
    signal.signal(signal.SIGTERM, lambda *a: ((mutlib.SRC / "facc.cho").write_text(text), sys.exit(143)))
    bad = 0
    try:
        for name, file, old, new in LOWERED:
            (mutlib.SRC / file).write_text(text.replace(old, new, 1))
            verdict, why = mutlib.build_and_test(TESTS, compiler, extra)
            print("%-32s %s" % (name, "pass" if verdict == "SURVIVED" else "%s [%s]" % (verdict, why)), flush=True)
            bad += verdict != "SURVIVED"
    finally:
        (mutlib.SRC / "facc.cho").write_text(text)
    return 1 if bad else 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--lowered" in argv:
        sys.exit(lowered([a for a in argv if a != "--lowered"]))
    tests = TESTS
    mutants = MUTANTS
    if "--parallel-only" in argv:
        argv.remove("--parallel-only")
        tests = ["test_float_sum.Parallel"]
        mutants = [m for m in MUTANTS if m[0].startswith("plain f64")]
    if "--check" in argv and mutlib.check(LOWERED):
        print("!! a lowered-threshold build cannot be applied")
        sys.exit(1)
    sys.exit(mutlib.main(mutants, tests, argv))
