#!/usr/bin/env python3
"""Mutation check of `--group` by any number of columns (issue 66; tests/conformance/test_group_columns.py): each mutant is one source file of tools/table with one
deliberate defect, rebuilt, and run against the group-column tests (and the named-benchmark gate, whose q10 groups by six columns). A mutant is killed when a test fails or
the build refuses it. The files are restored after every mutant.

    python3 scripts/group_mutants.py [name-substring ...]

Two kinds: the defect itself (the cache look of `agg.add_fast` in checked arithmetic, which trapped from the fifth group column), and a limit of four group columns put
back at each place that walks the group list (the key built by `add` and by `add_fast`, the cache check, the sort of the keys, the header, the explain, the JSON-lines row,
the csv key of a cut group, the parallel partition), which each test of 5 to 64 columns has to see.

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_group_columns", "test_named_groupby"]

FAST_LEN = "            look = wrapping_add(wrapping_mul(look, 31), n);"
FAST_CELL = "                look = wrapping_add(wrapping_mul(wrapping_add(wrapping_mul(look, 31), int_of(record[first])), 31), int_of(record[first + n - 1]));"

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the defect: the look at the cells, folded in checked arithmetic
    ("the look at a cell's length is checked arithmetic", "agg.cho", FAST_LEN, "            look = look * 31 + n;"),
    ("the look at a cell's first and last byte is checked arithmetic", "agg.cho", FAST_CELL,
     "                look = (look * 31 + int_of(record[first])) * 31 + int_of(record[first + n - 1]);"),
    ("the look multiplies in checked arithmetic and adds wrapped", "agg.cho", FAST_LEN, "            look = wrapping_add(look * 31, n);"),
    # a limit of four group columns, put back where the group list is walked
    ("the cache believes a hit on the first four columns only", "agg.cho", "            while j < ng && entry >= 0 {", "            while j < ng && j < 4 && entry >= 0 {"),
    ("the cache believes a hit on the lengths only", "agg.cho", "                if length_at(key, p) != n || !bytes.equal(key[p + 4..p + 4 + n], record[first..first + n]) {", "                if length_at(key, p) != n {"),
    ("the key `add` builds stops at four columns", "agg.cho", "        while j < ng && key_bad == 0 {", "        while j < ng && j < 4 && key_bad == 0 {"),
    ("the key the fast path needs room for is a byte short a column", "agg.cho", "        need = need + 4 + last - first;", "        need = need + 3 + last - first;"),
    ("the keys are sorted by their first four fields", "agg.cho", "            } else if j + 1 < fields {", "            } else if j + 1 < fields && j < 3 {"),
    ("two keys are equal when their first four fields are", "agg.cho", "fn compare_keys[&a, &b](x: &a [byte], y: &b [byte], fields: int) -> [] int {\n    var px = 0;\n    var py = 0;\n    var j = 0;\n    while j < fields {",
     "fn compare_keys[&a, &b](x: &a [byte], y: &b [byte], fields: int) -> [] int {\n    var px = 0;\n    var py = 0;\n    var j = 0;\n    while j < fields && j < 4 {"),
    ("the group of a JSON-lines row stops at four columns", "jout.cho", "        if m < ng {\n            if query.gkind_at(tree, m) != 0 {", "        if m < ng && m < 4 {\n            if query.gkind_at(tree, m) != 0 {"),
    ("the header names four group columns", "frame.cho", "                if m < ng {\n", "                if m < ng && m < 4 {\n"),
    ("the explain lists four group columns", "explain.cho", "    o = buffer.append(heap, o, \"],\\\"group\\\":[\");\n    k = 0;\n    while k < ng {", "    o = buffer.append(heap, o, \"],\\\"group\\\":[\");\n    k = 0;\n    while k < ng && k < 4 {"),
    ("the key of a cut group is written to its fourth column", "finish.cho", "            while j < query.count_of(tree, 2) {\n                if j > 0 {\n                    held", "            while j < query.count_of(tree, 2) && j < 4 {\n                if j > 0 {\n                    held"),
    ("the csv row of a group stops at four columns", "finish.cho", "                                    while j < ng {", "                                    while j < ng && j < 4 {"),
    ("the threads partition the groups by one field less than five", "par.cho", "    let fields = query.count_of(tree, 2);\n    let bound_groups", "    let fields = query.count_of(tree, 2) - query.count_of(tree, 2) / 5;\n    let bound_groups"),
]

# Equivalent in practice: a mutant here survives, and is not in MUTANTS (the reason is beside it).
EQUIVALENT = [
    # `wrapping_mul(look, 31) + n` (the add checked, the multiply wrapped) traps only when the wrapped product is within n of 2^63, about n in 2^64 a cell: it survives every test that can
    # be run and is no defect to find with one; the add is wrapped too, so that it is not a trap with a chance at all.
    ("the look adds in checked arithmetic and multiplies wrapped", "agg.cho", FAST_LEN, "            look = wrapping_mul(look, 31) + n;"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
