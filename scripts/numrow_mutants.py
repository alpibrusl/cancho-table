#!/usr/bin/env python3
"""Mutation check of O2 and O3 of docs/reader-options.md (issue #109; tests/conformance/test_numrow.py): `--na`, `--decimal` and `--thousands`, the row normalizer (tools/table/numrow.cho).

Each mutant is one defect: a marker list read wrongly (an escape, a split), a marker not compared whole, a quoted cell with a quote taken for one, the cells not repointed or the record not rebuilt (the sorter keeps the record), the
delimiter or quotes lost in the rebuild, a group of other than three digits taken for one, the first group's width, the decimal byte not written as a point, a plain point beside the stated one let through, the sentinel left
visible in a refusal or not added, the syntax missing from the refusal, and the loops for the options not given the normalizer. The tool is rebuilt from it and run against the tests.

    python3 scripts/numrow_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_numrow", "test_preamble"]

MUTANTS = [
    ("a backslash does not escape a comma", "numrow.cho", "if i + 1 < len(text) && (int_of(text[i + 1]) == ',' || int_of(text[i + 1]) == 92) {", "if false {"),
    ("an empty marker is well formed", "numrow.cho", "            if size == start {\n                good = false;", "            if false {\n                good = false;"),
    ("a marker with a quote is well formed", "numrow.cho", "            if c == 34 || c == 10 || c == 13 {", "            if c == 10 || c == 13 {"),
    ("the = spelling is cut at the wrong byte", "numrow.cho", "        text = a[5..len(a)];", "        text = a[4..len(a)];"),
    ("a marker is compared by its start", "numrow.cho", "if to - from == len(text) && bytes.equal(", "if to - from <= len(text) && bytes.equal("),
    ("the last marker is not looked at", "numrow.cho", "    while k < vec.size(naends) {\n        let to = vec.get(naends, k);", "    while k + 1 < vec.size(naends) {\n        let to = vec.get(naends, k);"),
    ("an empty cell is a marker", "numrow.cho", "if last > first && is_marker(", "if is_marker("),
    ("a quoted cell holding a quote is a marker", "numrow.cho", " && !(cells[3 * column + 2] != 0 && has_quote(record[first..last])) {", " {"),
    ("a marker cell is still quoted", "numrow.cho", "                cells[3 * column + 1] = first;\n                cells[3 * column + 2] = 0;\n                touched = true;", "                cells[3 * column + 1] = first;\n                touched = true;"),
    ("a marker is not said to change the row", "numrow.cho", "                cells[3 * column + 2] = 0;\n                touched = true;", "                cells[3 * column + 2] = 0;"),
    ("the record is not rebuilt with its delimiters", "numrow.cho", "            out = buffer.push(heap, out, byte_of(delim));", "            out = out;"),
    ("a quoted cell loses its quotes in the rebuild", "numrow.cho", "        if quoted && !rewrite {\n            out = buffer.push(heap, out, byte_of(34));\n        }\n        var start = 0;", "        var start = 0;"),
    ("a quoted cell does not close in the rebuild", "numrow.cho", "        if quoted && !rewrite {\n            out = buffer.push(heap, out, byte_of(34));\n        }\n        cells[3 * column] = start;", "        cells[3 * column] = start;"),
    ("the cells are not repointed at the rebuilt record", "numrow.cho", "        cells[3 * column] = start;\n        cells[3 * column + 1] = stop;", "        cells[3 * column] = cells[3 * column];\n        cells[3 * column + 1] = stop;"),
    ("an untyped column is rewritten", "numrow.cho", "if numbers && contents(norm.kinds)[column] == 1 && needs(", "if numbers && needs("),
    ("a typed column is not looked at", "numrow.cho", "            if contents(norm.kinds)[column] == 1 && needs(record[cells[3 * column]..cells[3 * column + 1]], norm.dec, norm.thou) {\n                numbers = true;", "            if false {\n                numbers = true;"),
    ("a column of another type is typed", "numrow.cho", "if t >= 2 && t <= 21 {", "if t >= 1 && t <= 21 {"),
    ("a float or decimal column is not typed", "numrow.cho", "if t >= 2 && t <= 21 {", "if t >= 9 && t <= 21 {"),
    ("the thousands byte is the decimal one", "numrow.cho", "        thou = syn % 256;\n        if thou == 0 {", "        thou = dec;\n        if thou == 0 {"),
    ("no thousands byte is a byte", "numrow.cho", "        if thou == 0 {\n            thou = 0 - 1;", "        if thou == 99999 {\n            thou = 0 - 1;"),
    ("a plain point beside the stated one is let through", "numrow.cho", "(c == 46 && dec != 46) {\n            return true;", "(c == 46 && dec != 46 && false) {\n            return true;"),
    ("the decimal byte is not written as a point", "numrow.cho", "            plain = buffer.push(heap, plain, byte_of(46));", "            plain = buffer.push(heap, plain, byte_of(dec));"),
    ("a group is not checked for its three digits", "numrow.cho", "            } else if digits_in_group != 3 {\n                good = false;\n            }\n            seen_group = true;", "            }\n            seen_group = true;"),
    ("the first group may have four digits", "numrow.cho", "if digits_in_group < 1 || digits_in_group > 3 {", "if digits_in_group < 1 || digits_in_group > 4 {"),
    ("the first group may be empty", "numrow.cho", "if digits_in_group < 1 || digits_in_group > 3 {", "if digits_in_group > 3 {"),
    ("the last group may be short before the point", "numrow.cho", "            if seen_group && digits_in_group != 3 {\n                good = false;\n            }\n            plain", "            plain"),
    ("the last group may be short without a point", "numrow.cho", "    if good && seen_group && !fraction && digits_in_group != 3 {", "    if good && seen_group && !fraction && digits_in_group != 3 && false {"),
    ("a group after the point is a group", "numrow.cho", "} else if c == thou && !fraction {", "} else if c == thou {"),
    ("the exponent is not copied", "numrow.cho", "        o = buffer.append(heap, o, text[end..len(text)]);\n", ""),
    ("the exponent is looked for from the start", "numrow.cho", "    var k = i;\n    while k < len(text) {", "    var k = 0;\n    while k < len(text) {"),
    ("the sign is dropped", "numrow.cho", "        plain = buffer.push(heap, plain, text[i]);\n        i = i + 1;\n    }\n    // the exponent", "        i = i + 1;\n    }\n    // the exponent"),
    ("a refused cell is not marked", "numrow.cho", "        o = buffer.push(heap, o, byte_of(sentinel()));", "        o = o;"),
    ("a refused cell is marked with a digit", "numrow.cho", "pub fn sentinel() -> [] int {\n    return 31;", "pub fn sentinel() -> [] int {\n    return 48;"),
    ("the mark is shown in a refusal", "numrow.cho", "        return v[0..len(v) - 1];", "        return v;"),
    ("a value ending in a mark byte that was not put is cut", "numrow.cho", "pub fn plain_value[&k](v: &k [byte]) -> [] &k [byte] {\n    if len(v) > 0 &&", "pub fn plain_value[&k](v: &k [byte]) -> [] &k [byte] {\n    if len(v) > 1 &&"),
    ("the syntax text has no thousands", "numrow.cho", "    if syn % 256 != 0 {", "    if syn % 256 != 0 && false {"),
    ("the options are not made into a normalizer", "numrow.cho", "    if syn == 0 && nam < 0 {\n        return none(heap);", "    if true {\n        return none(heap);"),
    ("the normalizer is not asked of the row", "table.cho#fileopt", "                            if norm_on {\n                                borrow norm as &nmr in {", "                            if false {\n                                borrow norm as &nmr in {"),
    ("the stream loop does not ask the normalizer", "table.cho#streamopt", "                            if norm_on {\n                                borrow norm as &nmr in {", "                            if false {\n                                borrow norm as &nmr in {"),
    ("the rewritten row is not the one read", "table.cho#fileopt", "                                var text = buffer.bytes(nrr);\n                                if !fixed {", "                                var text = buffer.bytes(nrr);\n                                if true {"),
]


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
