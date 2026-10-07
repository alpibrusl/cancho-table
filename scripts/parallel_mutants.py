#!/usr/bin/env python3
"""Mutation check of `--threads` (docs/parallel.md): each mutant is one source file of
tools/table with one deliberate defect in the parallel path, rebuilt, and run against
test_parallel (every plan, N threads against the sequential read) and the plan tests. A
mutant is killed when a test fails or the build refuses it. The files are restored after
every mutant, whatever happens.

    python3 scripts/parallel_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_parallel"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # stitching: the parent takes ranges in file order, and only a range whose guess was right
    ("a range whose first line is not where the last record ended is taken", "par.cho", "if agg.get_i64(sl, at) == 0 && first == cur {", "if agg.get_i64(sl, at) == 0 {"),
    ("a range whose thread says it needs the parent is taken", "par.cho", "if agg.get_i64(sl, at) == 0 && first == cur {", "if first == cur {"),
    ("the lines a range read are not counted", "par.cho", "lines_before = lines_before + l_lines(sl, at);", "lines_before = lines_before + 0;"),
    ("the first ragged row of a range is its own number", "par.cho", "a[engine.k_first_row()] = before + agg.get_i64(sl, at + 32 + 8 * engine.k_first_row());", "a[engine.k_first_row()] = agg.get_i64(sl, at + 32 + 8 * engine.k_first_row());"),
    ("the first ragged line of a range is its own number", "par.cho", "a[engine.k_first_line()] = lines_before + agg.get_i64(sl, at + 32 + 8 * engine.k_first_line());", "a[engine.k_first_line()] = agg.get_i64(sl, at + 32 + 8 * engine.k_first_line());"),
    ("a later ragged row can be the first", "par.cho", "if a[engine.k_ragged()] == 0 && g > 0 {", "if g > 0 {"),
    ("the rows a range read are not counted", "par.cho", "a[engine.k_records()] = before + r;", "a[engine.k_records()] = before;"),
    ("a range that passes --max-rows is taken", "par.cho", "var fits = before < most && before + r <= most;", "var fits = true;"),
    ("a range that would fill the page is taken", "par.cho", "if a[engine.k_emitted()] + e > limit {", "if false {"),
    ("the merge of a sum drops the high half of the range", "agg.cho", "a = set_at(a, entry * stride + 1 + na + k, mine_high + get_i64(blob, acc_at + 8 * (1 + na + k)));", "a = set_at(a, entry * stride + 1 + na + k, mine_high);"),
    ("the merge of a sum replaces the total with the range's", "agg.cho", "a = set_at(a, entry * stride + 1 + k, mine + theirs);", "a = set_at(a, entry * stride + 1 + k, theirs);"),
    ("a range that reaches the limit is taken, and the ragged rows after its last row with it", "par.cho", "if !filtering && a[engine.k_emitted()] + e >= limit && r > 0 {", "if false {"),
    ("a range that passes the byte budget is taken", "par.cho", "if have + payload_len + 2 > budget {", "if have + payload_len + 2 > budget + 100000 {"),
    ("json rows of two ranges are not separated", "par.cho", "if !as_csv && a[engine.k_emitted()] > 0 {", "if false {"),
    ("the rows of a range are not added", "par.cho", "rows2 = buffer.append(heap, rows2, body);", "rows2 = buffer.append(heap, rows2, \"\");"),
    ("a reread does not know the lines before it", "par.cho", "                        pp[scan.p_base_line()] = lines_before;\n                        let (x2, l2, st,", "                        pp[scan.p_base_line()] = 0;\n                        let (x2, l2, st,"),
    ("groups that do not fit a slot are trusted", "par.cho", "                    if n < 0 {\n                        clean = 0;", "                    if false {\n                        clean = 0;"),
    # a range
    ("a range reads one record too few", "scan.cho", "} else if !quoted && at >= until {", "} else if !quoted && at + 1 >= until {"),
    ("a range forgets its lines when it stops", "scan.cho", "                    consumed = number - 1;\n                    next_at = at;", "                    consumed = number;\n                    next_at = at;"),
    ("a range of a quoted record is one field short", "scan.cho", "                            let (m, open, bad_quote) = reader.fields(record, delim, cells, a[engine.k_columns()]);\n                            found = m;", "                            let (m, open, bad_quote) = reader.fields(record, delim, cells, a[engine.k_columns()]);\n                            found = m - 1;"),
    # groups across ranges
    ("a bound that would be passed is not noticed", "agg.cho", "if size + new_groups > max_groups || pairs + new_pairs > max_distinct || held + new_bytes > max_state {", "if false {"),
    ("distinct values of a range are not added", "agg.cho", "a = set_at(a, entry * stride + 1 + k, mine + fr[i * na + k]);", "a = set_at(a, entry * stride + 1 + k, mine);"),
    ("a minimum is merged as a maximum", "agg.cho", "} else if function == 2 && theirs < mine {", "} else if function == 2 && theirs > mine {"),
    ("the count of a range is not added", "agg.cho", "a = set_at(a, entry * stride, before + get_i64(blob, acc_at));", "a = set_at(a, entry * stride, before);"),
    ("a negative integer is read as a large one", "agg.cho", "    if top >= 128 {\n        top = top - 256;\n    }", ""),
    ("a new group is kept under an empty key", "agg.cho", "                        m = map.put(heap, m, key, 0);", "                        m = map.put(heap, m, key[0..0], 0);"),
    # handing over
    ("a selection that starts later is read by threads", "table.cho", "threads > 1 && (!ordering || query.reporting(tree)) && (mode == 2 || row_from == 0)", "threads > 1 && (!ordering || query.reporting(tree))"),
]

# Not here, and why: mutants that change nothing a test can see.
#  - a payload of rows that does not fit is trusted: a range that makes `room` bytes of rows has already
#    yielded (status 1, so `clean` is 0) before the copy is reached, and one that has not fits;
#  - a thread that met an error is trusted: scan_range answers status 2 for every abort, stop,
#    full page and row bound, so the extra conditions of `clean` repeat what `s == 0` says;
#  - the ranges are not aligned to lines, or a range reads one record too many: where a range
#    begins and ends is a guess the parent checks by `first == cur`, so a bad guess costs a re-read
#    and not an answer (the output is the same; only the time is not);
#  - a pair already known is added again: `map.put` of a present key replaces its value, which is 0;
#  - one thread is not enough to be sequential: `--threads 1` through the parallel code (one range at
#    a time) answers the same;
#  - a re-read does not write a full block of csv: it keeps the block until the next range is taken
#    instead of writing it, which changes the memory the parent holds and not one byte written.


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
