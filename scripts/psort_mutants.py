#!/usr/bin/env python3
"""Mutation check of `--order-by` read by several threads (docs/gap-sort.md): each mutant is one source file of tools/table with one
deliberate defect in the runs, their merge, the bounds that cut a read short, or the gate that lets a sort be parallel, rebuilt and run against
test_psort (every setting of threads and range size must give the sequential bytes). A mutant is killed when a test fails or the build refuses it.

    python3 scripts/psort_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_psort.PSort"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the merge: order and ties
    ("a tie goes to the later run", "psort.cho", "        if c != 0 {\n            return c < 0;\n        }\n    }\n    return a < b;", "        if c != 0 {\n            return c < 0;\n        }\n    }\n    return a > b;"),
    ("a descending first key is merged ascending", "psort.cho", "    var c = cmp_pre(hh1[a], hh2[a], hh1[b], hh2[b], is_desc(ks, 0));", "    var c = cmp_pre(hh1[a], hh2[a], hh1[b], hh2[b], 0);"),
    ("a first key longer than its words is not compared by its bytes", "psort.cho", "    if ks[0] > 1 || (hh4[a] | hh4[b]) & 1 == 1 {", "    if ks[0] > 1 {"),
    ("a later key longer than its word is not compared by its bytes", "psort.cho", "        if c == 0 && !is_num(ks, j) && (fa | fb) >> j & 1 == 1 {", "        if c == 0 && !is_num(ks, j) && false {"),
    ("a later key's descending flag is dropped", "psort.cho", "            c = cmp_one(agg.get_i64(blob, pa + 32 + 8 * j), agg.get_i64(blob, pb + 32 + 8 * j), is_desc(ks, j));", "            c = cmp_one(agg.get_i64(blob, pa + 32 + 8 * j), agg.get_i64(blob, pb + 32 + 8 * j), 0);"),
    ("the keys after the first are not looked at", "psort.cho", "    if ks[0] > 1 || (hh4[a] | hh4[b]) & 1 == 1 {\n        c = cmp_rest(inp, cpos[a], cpos[b], hh4[a], hh4[b], ks);", "    if false {\n        c = cmp_rest(inp, cpos[a], cpos[b], hh4[a], hh4[b], ks);"),
    # the run
    ("the words of a later text key are 14 bytes where 7 are kept", "psort.cho", "                        var cap = 14;\n                        if j > 0 {\n                            cap = 7;\n                        }", "                        var cap = 14;"),
    ("a key with a NUL is not flagged", "psort.cho", "                        if len(kb) > cap || bytes.count_byte(kb, 0) > 0 {", "                        if len(kb) > cap {"),
    ("the length of the keys' text is not written", "psort.cho", "                    agg.put_i64(out, at + 8, tail);", "                    agg.put_i64(out, at + 8, 0);"),
    ("the flags of the rows are not gathered", "psort.cho", "    agg.put_i64(out, 32, flags_or);", "    agg.put_i64(out, 32, 0);"),
    # the partitions
    ("the second key's word cuts the partitions though a key may be longer than its word", "psort.cho", "    if nk >= 2 && any == 0 {", "    if nk >= 2 {"),
    ("a partition's output is placed where the next one's input begins", "psort.cho", "                    contents(oa)[q] = acc;\n                    var r = 0;", "                    contents(oa)[q] = 0;\n                    var r = 0;"),
    # the bounds
    ("--max-sort-rows allows one more in a taken run", "psort.cho", "    if total > max_rows {", "    if total > max_rows + 1 {"),
    ("the unquoted keys of the last row are not looked at in a taken run", "psort.cho", "    return runs.size + open_size + (size - last_side) + 8 * width * total <= max_state;", "    return runs.size + open_size + (size - last_side - 1) + 8 * width * total <= max_state;"),
    ("the places in the rows are not counted in a taken run", "psort.cho", "    return runs.size + open_size + (size - last_side) + 8 * width * total <= max_state;", "    return runs.size + open_size + (size - last_side) <= max_state;"),
    ("the sorter that goes on has all the rows left", "psort.cho", "    return max_rows - runs.rows;", "    return max_rows;"),
    ("the sorter that goes on has all the bytes left less the records", "psort.cho", "    return max_state - state_bytes(runs.size, runs.rows, width);", "    return max_state - runs.size;"),
    ("a run's bytes are not added to the sort's", "psort.cho", "size: size + sz, flags: flags | fl };", "size: size, flags: flags | fl };"),
    ("a run's rows are not added to the sort's", "psort.cho", "rows: rows + n, size: size + sz", "rows: n, size: size + sz"),
    ("an open sorter is never made a run", "psort.cho", "    if n == 0 {\n        return (runs, open);\n    }\n    var cap =", "    if n >= 0 {\n        return (runs, open);\n    }\n    var cap ="),
    # the read
    ("a run is taken whatever the bounds", "par.cho", "                            if !okfit {\n                                fits = false;", "                            if false {\n                                fits = false;"),
    ("the bounds of the sorter that goes on are not lowered", "par.cho", "                                    borrow mut groups2 as &!gw in {\n                                        sorter.set_bounds(gw, lr, ls);\n                                    }", "                                    borrow mut groups2 as &!gw in {\n                                        sorter.set_bounds(gw, sort_rows, sort_state);\n                                    }"),
    ("the bounds of the next read of a range are not lowered", "par.cho", "                                    pp[scan.p_max_groups()] = lr;\n                                    pp[scan.p_max_state()] = ls;", "                                    pp[scan.p_max_groups()] = sort_rows;\n                                    pp[scan.p_max_state()] = sort_state;"),
    # the gate
    ("a page is sorted by threads and not cut", "table.cho", "as_csv && wanted == 0 && top == 0 && from == 0 && limit >= 100000000;", "as_csv && wanted == 0 && from == 0 && limit >= 100000000;"),
    ("a page that begins late is sorted by threads and not cut", "table.cho", "as_csv && wanted == 0 && top == 0 && from == 0 && limit >= 100000000;", "as_csv && wanted == 0 && top == 0 && limit >= 100000000;"),
    ("a limit is sorted by threads and not cut", "table.cho", "as_csv && wanted == 0 && top == 0 && from == 0 && limit >= 100000000;", "as_csv && top == 0 && from == 0;"),
]

# Not here, and why: mutants that change nothing a test can see.
#  - the second key's word missing from a sparse sample, a splitter is a threshold wherever it lies and a sample that reads too early only starts a scan sooner;
#  - the rows not counted as emitted: the count is for a page of json;
#  - `a[engine.k_psorted()]` not set: the sequential writer then runs over a sorter that holds nothing and writes nothing;
#  - a payload slot too small (`payload_cap`): a range whose run does not fit is read again, slower and the same;
#  - a run taken only when the bounds hold with room to spare (`>=` for `>`, a byte more): the range is read again and the same bounds hold;
#  - the splitters' binary search `< 0` for `<= 0`: rows equal to a splitter go to the partition on its left in every run, which is as good;
#  - the number of partitions: any number of partitions gives the same rows, the answer does not depend on it.


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
