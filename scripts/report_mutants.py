#!/usr/bin/env python3
"""Mutation check of the type report (docs/numbers.md, stage N6): each mutant is one source file of tools/table with one deliberate defect, rebuilt, and run
against the report, skill and float tests. A mutant is killed when a test fails or the build refuses it. The files are restored after every mutant.

    python3 scripts/report_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_report", "test_skill", "test_float"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the classes (report.cho: classify)
    ("an empty cell is an int", "report.cho", "    if len(data) == 0 {\n        return (0, 0, 0);\n    }\n    let (v, bad) = query.parse_int(data);", "    if len(data) == 0 {\n        return (1, 0, 0);\n    }\n    let (v, bad) = query.parse_int(data);"),
    ("an int that does not fit 64 bits is an int", "report.cho", "    let (v, bad) = query.parse_int(data);\n    if bad == 0 {", "    let (v, bad) = query.parse_int(data);\n    if bad != 1 {"),
    ("an int is a dec", "report.cho", "        return (1, 0, digits_before_point(data));\n    }\n    // the decimal grammar", "        return (2, 0, digits_before_point(data));\n    }\n    // the decimal grammar"),
    ("a decimal may have 19 fractional digits", "report.cho", "    if clean && point && digits > 0 && frac <= 18 {", "    if clean && point && digits > 0 && frac <= 19 {"),
    ("a decimal's scale is not counted", "report.cho", "            if point {\n                frac = frac + 1;\n            }", "            if false {\n                frac = frac + 1;\n            }"),
    ("a sign is not skipped before the decimal's digits", "report.cho", "    if int_of(data[0]) == '-' || int_of(data[0]) == '+' {\n        at = 1;\n    }\n    var digits = 0;", "    if int_of(data[0]) == '-' {\n        at = 1;\n    }\n    var digits = 0;"),
    ("a float is a dec", "report.cho", "    if status == 0 {\n        return (3, 0, 0);", "    if status == 0 {\n        return (2, 0, 0);"),
    ("a number that is not finite is other", "report.cho", "    if status == 10 {\n        return (4, 0, 0);", "    if status == 11 {\n        return (4, 0, 0);"),
    ("other is not finite", "report.cho", "    return (5, 0, 0);\n}\n\nfn get", "    return (4, 0, 0);\n}\n\nfn get"),
    ("the digits before the point count the leading zeros", "report.cho", "    while at < len(data) && int_of(data[at]) == '0' {\n        at = at + 1;\n    }", "    while at < 0 && int_of(data[at]) == '0' {\n        at = at + 1;\n    }"),
    ("the digits before the point count the sign", "report.cho", "    var at = 0;\n    if len(data) > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {\n        at = 1;\n    }\n    while at < len(data) && int_of(data[at]) == '0'", "    var at = 0;\n    if len(data) > 0 && (int_of(data[0]) == '-' || int_of(data[0]) == '+') {\n        at = 0;\n    }\n    while at < len(data) && int_of(data[at]) == '0'"),
    # the counters (account)
    ("the cells are not counted", "report.cho", "    put(g, base, get(g, base) + 1);\n    if len(data) > get(g, base + 9) {", "    if len(data) > get(g, base + 9) {"),
    ("the widest is the last", "report.cho", "    if len(data) > get(g, base + 9) {\n        put(g, base + 9, len(data));", "    if len(data) > 0 {\n        put(g, base + 9, len(data));"),
    ("an empty cell is not counted as empty", "report.cho", "        put(g, base + 1, get(g, base + 1) + 1);\n        if get(g, base + 10) == 0 {", "        put(g, base + 1, get(g, base + 1) + 0);\n        if get(g, base + 10) == 0 {"),
    ("the first empty is the last", "report.cho", "        if get(g, base + 10) == 0 {\n            put(g, base + 10, row);", "        if true {\n            put(g, base + 10, row);"),
    ("the empty cell's line is its row", "report.cho", "            put(g, base + 11, line);\n        }\n        return 0;", "            put(g, base + 11, row);\n        }\n        return 0;"),
    ("a class is counted in the next counter", "report.cho", "    put(g, base + 1 + class, get(g, base + 1 + class) + 1);", "    put(g, base + 2 + class, get(g, base + 2 + class) + 1);"),
    ("an int's digits are not counted", "report.cho", "    if class <= 2 {\n        if idig > get(g, base + 8) {", "    if class == 2 {\n        if idig > get(g, base + 8) {"),
    ("a dec's scale is the last", "report.cho", "    if class == 2 && scale > get(g, base + 7) {", "    if class == 2 {"),
    ("an int is the first not-int", "report.cho", "    if class != 1 && get(g, base + 12) == 0 {", "    if class != 7 && get(g, base + 12) == 0 {"),
    ("a dec is the first not-dec", "report.cho", "    if class != 1 && class != 2 && get(g, base + 14) == 0 {", "    if class != 1 && get(g, base + 14) == 0 {"),
    ("a not-finite cell is not a not-float", "report.cho", "    if class >= 4 && get(g, base + 16) == 0 {", "    if class >= 5 && get(g, base + 16) == 0 {"),
    ("a dec is a not-float", "report.cho", "    if class >= 4 && get(g, base + 16) == 0 {", "    if class >= 2 && get(g, base + 16) == 0 {"),
    ("the first other is the last", "report.cho", "    if class == 5 && get(g, base + 18) == 0 {", "    if class == 5 {"),
    ("the length of the other cell is not kept", "report.cho", "        put(g, base + 20, len(data));\n", "        put(g, base + 20, 0);\n"),
    ("the other cell is packed 8 to an integer", "report.cho", "            let slot = base + 21 + i / 7;\n            put(g, slot, get(g, slot) + (int_of(data[i]) << 8 * (i % 7)));", "            let slot = base + 21 + i / 8;\n            put(g, slot, get(g, slot) + (int_of(data[i]) << 8 * (i % 7)));"),
    # the suggestion and the answer
    ("a column of nothing is an int", "report.cho", "    if cells == empty || bad > 0 {\n        return buffer.append(heap, out, \"none\");", "    if bad > 0 {\n        return buffer.append(heap, out, \"none\");"),
    ("a not-finite cell does not make none", "report.cho", "    let bad = get(g, base + 5) + get(g, base + 6);", "    let bad = get(g, base + 6);"),
    ("an other cell does not make none", "report.cho", "    let bad = get(g, base + 5) + get(g, base + 6);", "    let bad = get(g, base + 5);"),
    ("a column of decimals is an int", "report.cho", "    if decs == 0 && floats == 0 {\n        return buffer.append(heap, out, \":int\");", "    if floats == 0 {\n        return buffer.append(heap, out, \":int\");"),
    ("the width is not checked for a dec", "report.cho", "    if floats == 0 && get(g, base + 8) + get(g, base + 7) <= 18 {", "    if floats == 0 {"),
    ("the width check allows 19 digits", "report.cho", "    if floats == 0 && get(g, base + 8) + get(g, base + 7) <= 18 {", "    if floats == 0 && get(g, base + 8) + get(g, base + 7) <= 19 {"),
    ("the dec's scale is the digits", "report.cho", "        o = buffer.push_nat(heap, o, get(g, base + 7));\n        return buffer.push(heap, o, byte_of(')'));", "        o = buffer.push_nat(heap, o, get(g, base + 8));\n        return buffer.push(heap, o, byte_of(')'));"),
    ("a float column is a dec", "report.cho", "    return buffer.append(heap, out, \":float\");\n}", "    return buffer.append(heap, out, \":dec(0)\");\n}"),
    ("an absent first is 0", "report.cho", "    if v == 0 {\n        if as_csv {\n            return out;\n        }\n        return buffer.append(heap, out, \"\\\"\\\"\");\n    }", "    if v == 0 {\n        return put_number(heap, out, 0, as_csv);\n    }"),
    ("the truncated flag is always false", "report.cho", "    } else if get(g, base + 20) > 64 {\n        o = field(heap, o, \"true\", as_csv, delim);", "    } else if get(g, base + 20) > 640 {\n        o = field(heap, o, \"true\", as_csv, delim);"),
    ("the cell is not decoded from its second integer", "report.cho", "        o = buffer.push(heap, o, byte_of(get(g, base + 21 + i / 7) >> 8 * (i % 7) & 255));", "        o = buffer.push(heap, o, byte_of(get(g, base + 21) >> 8 * (i % 7) & 255));"),
    ("the csv header is the json one", "report.cho", "            if start > 0 {\n                o = buffer.push(heap, o, byte_of(delim));", "            if start > 0 {\n                o = buffer.push(heap, o, byte_of(';'));"),
    # the first row of a report, the state, the merge
    ("the counters are made for one column fewer", "report.cho", "        g = agg.extend(heap, g, width() * ncols);\n    }\n    let ns", "        g = agg.extend(heap, g, width() * ncols - 31);\n    }\n    let ns"),
    ("the state bound is 8 bytes a counter fewer", "report.cho", "        if 8 * width() * ncols > max_state {\n            return (g, scr, 1);", "        if 4 * width() * ncols > max_state {\n            return (g, scr, 1);"),
    ("a selected column is the position", "report.cho", "        if ns > 0 {\n            column = cols[m];\n        }", "        if ns > 0 {\n            column = m;\n        }"),
    ("a quoted cell is not unquoted", "report.cho", "        if cells[3 * column + 2] == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {", "        if cells[3 * column + 2] == 7 && index_of_byte(record[first..last], byte_of(34)) >= 0 {"),
    ("the merge adds the maxima", "report.cho", "            while c < 10 {\n                let theirs = agg.get_i64(blob, from + 8 * c);\n                if theirs > get(gw, base + c) {\n                    put(gw, base + c, theirs);\n                }", "            while c < 10 {\n                let theirs = agg.get_i64(blob, from + 8 * c);\n                if true {\n                    put(gw, base + c, get(gw, base + c) + theirs);\n                }"),
    ("the merge takes the later first", "report.cho", "                if their_row > 0 && get(gw, base + f) == 0 {", "                if their_row > 0 {"),
    ("the merge does not offset the rows", "report.cho", "                    put(gw, base + f, their_row + rows_before);", "                    put(gw, base + f, their_row);"),
    ("the merge does not offset the lines", "report.cho", "+ lines_before);\n                    if f == 18 {", ");\n                    if f == 18 {"),
    ("the merge forgets the other cell", "report.cho", "                    if f == 18 {\n                        c = 20;", "                    if f == 99 {\n                        c = 20;"),
    ("the merge counts only six counters", "report.cho", "            var c = 0;\n            while c < 7 {", "            var c = 0;\n            while c < 6 {"),
    # the plan and the engine
    ("the report is not told from a sort", "query.cho", "    return vec.get(q.gkinds, 1) != 0;", "    return false;"),
    ("a report row is held as a sort row", "engine.cho", "    if query.reporting(tree) {\n        // `--report types`", "    if false {\n        // `--report types`"),
    ("a report's refusal has the code of a bound of the groups", "engine.cho", "            a[k_abort()] = 43;", "            a[k_abort()] = 15;"),
    ("a report counts the wrong row", "engine.cho", "report.columns_of(tree, a[k_columns()]), a[k_records()], opened, max_state);", "report.columns_of(tree, a[k_columns()]), a[k_records()] + 1, opened, max_state);"),
    ("the worker's counters are not sent", "par.cho", "                    if query.reporting(w.tree) {\n                        n = report.serialize", "                    if false {\n                        n = report.serialize"),
    ("the parent merges them as groups", "par.cho", "                            if query.reporting(tree) {\n                                let (g2, r2) = report.merge", "                            if false {\n                                let (g2, r2) = report.merge"),
    ("the merge's offsets are the next range's", "par.cho", "sl[at + header_bytes()..at + header_bytes() + payload_len], before, lines_before, pp[scan.p_max_state()]);", "sl[at + header_bytes()..at + header_bytes() + payload_len], before + r, lines_before, pp[scan.p_max_state()]);"),
    ("--report is any value", "table.cho", "        if !bytes.equal(inp.report_text, \"types\") {", "        if false {"),
    ("--report with a group is not a conflict", "table.cho", "        if grouped || has_order || inp.has_sort || inp.has_top {", "        if has_order || inp.has_sort || inp.has_top {"),
    ("--report with --top is not a conflict", "table.cho", "        if grouped || has_order || inp.has_sort || inp.has_top {", "        if grouped || has_order || inp.has_sort {"),
    ("the report's header is the selection's", "readloop.cho", "    if reporting {\n        buffer.drop(heap, head);\n        return report.head_json(heap);\n    }", "    if false {\n        buffer.drop(heap, head);\n        return report.head_json(heap);\n    }"),
    ("the report's csv header is the selection's", "readloop.cho", "    if reporting {\n        buffer.drop(heap, lead);\n        return report.head_csv(heap, delim);\n    }", "    if false {\n        buffer.drop(heap, lead);\n        return report.head_csv(heap, delim);\n    }"),
    ("the report is read by one thread", "readloop.cho", "threads > 1 && (!ordering || query.reporting(tree)) && (mode == 2 || row_from == 0) {", "threads > 1 && !ordering && (mode == 2 || row_from == 0) {"),
    ("the report's workers read rows", "readloop.cho", "                                            if query.reporting(tree) {\n                                                u[scan.p_mode()] = 2;", "                                            if false {\n                                                u[scan.p_mode()] = 2;"),
    ("the report's columns are not paged from --from", "table.cho", "    var pos = from;\n    var going = true;\n    borrow groups as &gr in {", "    var pos = 0;\n    var going = true;\n    borrow groups as &gr in {"),
    ("a report with no rows is not listed", "table.cho", "        groups = agg.extend(heap, groups, report.width() * ncols);\n    }\n    a[engine.k_groups()] = ncols;", "    }\n    a[engine.k_groups()] = ncols;"),
    ("the report's state refusal is another rule", "table.cho", "the type report keeps 248 bytes for each column", "the type report keeps 8 bytes for each column"),
    # the 8-byte word of :float (found by this report's fuzz)
    ("the word `infinity` is read from a number built from its bytes", "flt.cho", "    return n == 8 && word_is(data, at, \"infinity\");", "    return n == 8 && word_is(data, at, \"infinitz\");"),
    ("the word check does not fold capitals", "flt.cho", "        if c >= 'A' && c <= 'Z' {\n            c = c + 32;\n        }\n        if c != int_of(word[i]) {", "        if c != int_of(word[i]) {"),
]

# Equivalent: mutants that survive, and must, with the reason (each was found by the first run; none of them can change a byte of the answer).
EQUIVALENT = [
    ("a decimal needs no point", "without a point the cell is digits (an int, taken first), or a number of 19 digits or more, which `parse_dec` refuses as too wide (10^18): the same float"),
    ("a decimal needs no digit", "a cell with a point and no digit has `parse_dec` status 1: the same other"),
    ("a decimal too wide for 64 bits is a dec", "`parse_dec` at the cell's own scale answers 0 or 2 only (the grammar was checked, the scale is the cell's): `!= 2` is `== 0`"),
    ("a second point is clean", "`parse_dec` refuses the second point (status 1): the same float or other"),
    ("a float's digits are counted for the suggestion", "`classify` answers 0 digits for a float, so the maximum does not move"),
    ("the other cell is kept to 65 bytes", "the 65th byte is stored and never read: 64 are written out, and `truncated` is told from the length"),
    ("the merge of nothing refuses", "the parent reads that range itself (a worker with no row has nothing to add): the same bytes, only slower"),
    ("the report is read by one thread (on a machine without strace)", "the bytes are the same either way; `test_report.Threads` counts the threads started under strace on Linux, which kills it there"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
