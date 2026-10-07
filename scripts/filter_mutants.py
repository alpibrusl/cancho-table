#!/usr/bin/env python3
"""Mutation check of `--where`, `--group` and `--agg` (docs/filter.md): each mutant is
one source file of tools/table with one deliberate defect, rebuilt, and run against the
plan, filter, select, differential, limit and rule tests. A mutant is killed when a test
fails or the build refuses it. The files are restored after every mutant, whatever
happens.

    python3 scripts/filter_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_plan", "test_filter", "test_select", "test_rules", "test_cellcost"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the sum is a pair (docs/numbers.md N0p): the low 32 bits of a cell in one integer, the rest in the second
    ("the low half of a sum loses its top bit", "agg.cho", "a = set_at(a, at + 1 + k, now + (v & 0xffffffff));", "a = set_at(a, at + 1 + k, now + (v & 0x7fffffff));"),
    ("the high half of a sum is shifted by 31", "agg.cho", "a = set_at(a, at + 1 + (stride - 1) / 2 + k, high + (v >> 32));", "a = set_at(a, at + 1 + (stride - 1) / 2 + k, high + (v >> 31));"),
    ("the high half of an in-place sum is not added", "agg.cho", "vec.set(g.acc, high_at, vec.get(g.acc, high_at) + (v >> 32));", "vec.set(g.acc, high_at, vec.get(g.acc, high_at));"),
    ("the low half of an in-place sum is not masked", "agg.cho", "vec.set(g.acc, slot + 1 + k, now + (v & 0xffffffff));", "vec.set(g.acc, slot + 1 + k, now + v);"),
    ("settling a sum forgets the carry", "agg.cho", "vec.set(g.acc, high_at, vec.get(g.acc, high_at) + (low >> 32));", "vec.set(g.acc, high_at, vec.get(g.acc, high_at));"),
    ("settling a sum leaves the low half whole", "agg.cho", "vec.set(g.acc, low_at, low & 0xffffffff);", "vec.set(g.acc, low_at, low);"),
    ("sorting by a sum looks at the low half only", "agg.cho", "        if hx != hy {\n            if descending {", "        if false && hx != hy {\n            if descending {"),
    ("the magnitude of a negative wide sum is one too large", "agg.cho", "            h = 0 - high - 1;\n            l = 4294967296 - low;", "            h = 0 - high;\n            l = 4294967296 - low;"),
    ("a group of nine digits is not padded with zeros", "agg.cho", "while pad > v && pad > 1 {", "while pad > v && pad > 100000000 {"),
    ("a sum of 2^63 and more takes the narrow way", "agg.cho", "if high >= 0 - 2147483648 && high < 2147483648 {", "if high >= 0 - 2147483648 && high < 4294967296 {"),
    # exact integers
    ("2^63 is accepted as a positive integer", "query.cho", "    if acc == int_min() {\n        return (0, 2);", "    if false && acc == int_min() {\n        return (0, 2);"),
    ("a minus sign is a plus", "query.cho", "if len(data) > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {\n        negative = int_of(data[0]) == '-';", "if len(data) > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {\n        negative = int_of(data[0]) == '+';"),
    ("a sign alone is zero", "query.cho", "    if at >= len(data) {\n        return (0, 1);\n    }", "    if false && at >= len(data) {\n        return (0, 1);\n    }"),
    ("an integer past 64 bits wraps", "query.cho", "        if acc < (int_min() + digit) / 10 {\n            over = true;", "        if acc < (int_min() + digit) / 10 && false {\n            over = true;"),
    # the grammar
    ("an escaped :int is still a suffix", "expr.cho", "} else if length >= 4 && escaped_last <= length - 4 {", "} else if length >= 4 && escaped_last <= length {"),
    ("= on an :int column is not equality", "expr.cho", "            if kind == 2 || op == 0 {\n                yes = v == w;", "            if kind == 2 {\n                yes = v == w;"),
    ("<= is < on text", "expr.cho", "            yes = c <= 0;", "            yes = c < 0;"),
    ("<= is < on integers", "expr.cho", "                yes = v <= w;", "                yes = v < w;"),
    ("a condition after a false one is still evaluated", "expr.cho", "        if verdict != 1 {\n            return (verdict, k, scr);", "        if verdict >= 2 {\n            return (verdict, k, scr);"),
    ("64 conditions is not the ceiling", "expr.cho", "        if many >= 64 {", "        if many >= 65 {"),
    ("1024 values is not the ceiling", "expr.cho", "if values > 1024 {", "if values > 2000 {"),
    ("in () with a missing paren is accepted", "expr.cho", "                    } else {\n                        what = 7;", "                    } else if false {\n                        what = 7;"),
    ("contains is case-insensitive for the keyword", "expr.cho", 'is_contains = status == 0 && quoted == 0 && bytes.equal(buffer.bytes(wr), "contains");', 'is_contains = status == 0 && bytes.equal(buffer.bytes(wr), "contains");'),
    # groups
    ("min is max", "agg.cho", "                    } else if function == 2 && v < now {\n                        a = set_at", "                    } else if function == 2 && v > now {\n                        a = set_at"),
    ("min is max, in place", "agg.cho", "            } else if function == 2 && v < now {\n                vec.set", "            } else if function == 2 && v > now {\n                vec.set"),
    ("the first value of a group is not its minimum", "agg.cho", "                    } else if before == 0 {\n                        a = set_at", "                    } else if before == 1 {\n                        a = set_at"),
    ("the first value of a group is not its minimum, in place", "agg.cho", "            } else if before == 0 {\n                vec.set", "            } else if before == 1 {\n                vec.set"),
    ("a value is distinct every time", "agg.cho", "known = map.find(sr, buffer.bytes(dr)) >= 0;", "known = false;"),
    ("one more distinct value is allowed", "agg.cho", "if distinct_pairs >= max_distinct {", "if distinct_pairs > max_distinct {"),
    ("one more group is allowed", "agg.cho", "if size >= max_groups {", "if size > max_groups {"),
    ("the state bound is not kept", "agg.cho", "} else if bytes_held + buffer.size(kr) > max_state {", "} else if bytes_held + buffer.size(kr) > max_state + 100000000 {"),
    ("groups sort descending by key", "agg.cho", "            if c < 0 {\n                return 0 - 1;\n            }\n            return 1;", "            if c < 0 {\n                return 1;\n            }\n            return 0 - 1;"),
    # (Not here: making the merge unstable. The order is total -- ties are broken by the keys, which are
    # all different -- so stability cannot be seen; the mutant is equivalent.)
    ("zero is written -0", "agg.cho", "    if v >= 0 {\n        return buffer.push_nat(heap, out, v);", "    if v > 0 {\n        return buffer.push_nat(heap, out, v);"),
    # --sort
    ("- does not mean descending", "frame.cho", "if int_of(sort[0]) == '-' {", "if int_of(sort[0]) == '+' {"),
    ("count is sorted by the wrong slot", "frame.cho", "            } else if query.agg_at(tree, found - ng, 0) == 0 {\n                sort_slot = 0;", "            } else if query.agg_at(tree, found - ng, 0) == 0 {\n                sort_slot = 1;"),
    # the read
    ("the refusals of a grouping are numbered one low", "engine.cho", "    a[k_abort()] = 12 + status;\n    if status < 4 {", "    a[k_abort()] = 11 + status;\n    if status < 4 {"),
    ("the refusals of a grouping are numbered one low, in place", "engine.cho", "    // The same refusal as `process_groups`.\n    a[k_stop()] = 1;\n    a[k_abort()] = 12 + status;", "    // The same refusal as `process_groups`.\n    a[k_stop()] = 1;\n    a[k_abort()] = 11 + status;"),
    ("a full page holds one row more with --where", "engine.cho", "    if a[k_emitted()] >= limit {\n        // The page is full and this row matches: there is more.", "    if a[k_emitted()] > limit {\n        // The page is full and this row matches: there is more."),
    ("the next of a filtered page is one past", "engine.cho", "        a[k_next()] = a[k_records()] - 1;\n        a[k_stop()] = 1;\n        return (rows, scratch, e2, k2);", "        a[k_next()] = a[k_records()];\n        a[k_stop()] = 1;\n        return (rows, scratch, e2, k2);"),
    ("a page is declared full at a row that does not match", "table.cho", "mode == 1 && !filtering && a[engine.k_records()] >= from", "mode == 1 && a[engine.k_records()] >= from"),
    ("--top is ignored", "table.cho", "            if top > 0 && top < n {", "            if top > n {"),
    ("the byte budget of a json page of groups is not kept", "table.cho", "                        if have + need + 1 > budget {\n                            going = false;", "                        if have + need + 1 > budget + 1000000 {\n                            going = false;"),
    ("the number of groups is not reported", "table.cho", "    a[engine.k_groups()] = n;", "    a[engine.k_groups()] = 0;"),
    ("--select and --group do not conflict", "table.cho", "    if grouped && has_select {", "    if false && grouped && has_select {"),
    ("the row of a refused cell is one low", "engine.cho", "    a[k_err_fn()] = -1;\n    a[k_err_row()] = a[k_records()];", "    a[k_err_fn()] = -1;\n    a[k_err_row()] = a[k_records()] - 1;"),
    ("the row of a refused aggregate cell is one low", "engine.cho", "    a[k_err_row()] = a[k_records()];\n    a[k_err_line()] = opened;\n    return (g3, e3, keep_value", "    a[k_err_row()] = a[k_records()] - 1;\n    a[k_err_line()] = opened;\n    return (g3, e3, keep_value"),
    ("the row of a refused aggregate cell is one low, in place", "engine.cho", "    a[k_err_row()] = a[k_records()];\n    a[k_err_line()] = opened;\n    return (0, e2, keep_value", "    a[k_err_row()] = a[k_records()] - 1;\n    a[k_err_line()] = opened;\n    return (0, e2, keep_value"),
    ("the row of a refused aggregate cell is one low, when recorded after the fast way", "engine.cho", "    a[k_err_row()] = a[k_records()];\n    a[k_err_line()] = opened;\n    return keep_value", "    a[k_err_row()] = a[k_records()] - 1;\n    a[k_err_line()] = opened;\n    return keep_value"),
    ("a refused value is kept whole", "engine.cho", "    if last - first > 64 {", "    if last - first > 6400 {"),
    ("every unknown name is in --select", "table.cho", "            } else if c.bad_name >= ns + nw + ng {", "            } else if c.bad_name >= ns + nw + ng + 5 {"),
    ("a grouping with no --agg counts nothing", "table.cho", "            tree = query.add_agg(heap, tree, 0, -1);", "            tree = query.add_agg(heap, tree, 1, 0);"),
]


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
