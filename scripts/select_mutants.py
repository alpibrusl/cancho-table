#!/usr/bin/env python3
"""Mutation check of `--select` (docs/select.md): each mutant is one source file of
tools/table with one deliberate defect, rebuilt, and run against the select,
differential, limit and rule tests. A mutant is killed when a test fails or the
build refuses it. The files are restored after every mutant, whatever happens.

    python3 scripts/select_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_select", "test_differential", "test_limits", "test_rules"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
# Not here, and why: "a page holds one row more" (table.cho and scan.cho stop a read without `--where` when the
# page is full and another record is there, `emitted >= limit` made `emitted > limit`): the row that then
# reaches `engine.process_rows_buf` is stopped by its own `emitted >= limit` and gives the same `more` and the same
# `next`; the early test only saves the work of splitting the record. Equivalent.
MUTANTS = [
    # reader.fields: where the fields of a record are
    ("a field is never quoted", "reader.cho", "if p < end && int_of(line[p]) == 34 {", "if p < end && int_of(line[p]) == 35 {"),
    ("a doubled quote closes a quoted field", "reader.cho", "} else if at + 1 < end && int_of(line[at + 1]) == 34 {\n                    q = at + 2;", "} else if false && at + 1 < end && int_of(line[at + 1]) == 34 {\n                    q = at + 2;"),
    ("a delimiter after a closing quote is bad", "reader.cho", "} else if int_of(line[at + 1]) == delim {\n                        next = at + 2;", "} else if int_of(line[at + 1]) == 0 - 5 {\n                        next = at + 2;"),
    ("a quoted field is marked unquoted", "reader.cho", "                    quoted = 1;", "                    quoted = 0;"),
    ("an unquoted field swallows the next delimiter", "reader.cho", "                next = q + 1;", "                next = q + 2;"),
    ("the last stored field is dropped", "reader.cho", "            if n < room {", "            if n + 1 < room {"),
    # plan: NAMES
    ("a # cannot be escaped", "plan.cho", " || int_of(given[i + 1]) == '#') {", ") {"),
    ("a position needs one character, not two", "plan.cho", "if hash && len(name) >= 2 && len(name) <= 10 {", "if hash && len(name) >= 1 && len(name) <= 10 {"),
    ("a repeated header names one column", "plan.cho", "map.set_value_at(w, found, 0 - 2);", "map.set_value_at(w, found, 0 - 1);"),
    ("a position past the last column is a column", "plan.cho", "position >= 1 && position <= width", "position >= 1 && position <= width + 1"),
    ("a position counts from 0", "plan.cho", "column = position - 1;", "column = position;"),
    ("case is ignored in a name's repair", "plan.cho", "if bytes.to_lower(int_of(a[i])) != bytes.to_lower(int_of(b[i])) {", "if int_of(a[i]) != int_of(b[i]) {"),
    ("a comma in a repaired name is not escaped", "plan.cho", "if c == ',' || c == '\\\\' || c == '#' && j == 0 {", "if c == '\\\\' || c == '#' && j == 0 {"),
    # writer
    ("an unquoted field with a CR is not quoted", "writer.cho", "        if !must_quote(data, delim, 0) {", "        if !has(data, 34) {"),
    ("a quoted field with a delimiter loses its quotes", "writer.cho", "    if !must_quote(data, delim, 1) {\n        return buffer.append(heap, out, data);\n    }\n    var o = buffer.push(heap, out, byte_of(34));\n    o = buffer.append(heap, o, data);", "    if !must_quote(data, delim, 0) {\n        return buffer.append(heap, out, data);\n    }\n    var o = buffer.push(heap, out, byte_of(34));\n    o = buffer.append(heap, o, data);"),
    ("a doubled quote is read as two in json", "writer.cho", "            p = p + found + 2;", "            p = p + found + 1;"),
    # table: paging, bounds, refusals
    ("a page starts one row late", "engine.cho", "    return index >= from;", "    return index > from;"),
    ("next is one past", "table.cho", "                        a[engine.k_next()] = a[engine.k_records()];", "                        a[engine.k_next()] = a[engine.k_records()] + 1;"),
    ("the budget is not kept", "engine.cho", "    if have + need + 1 > budget {\n        a[k_stop()] = 1;", "    if have + need + 1 > budget + 1000000 {\n        a[k_stop()] = 1;"),
    ("the next of a budget stop is one past", "engine.cho", "            a[k_next()] = a[k_records()] - 1;\n        }\n        return (pending, row);", "            a[k_next()] = a[k_records()];\n        }\n        return (pending, row);"),
    ("a row of one empty field is a blank line", "engine.cho", "        if picked == 1 && held == begun {", "        if false && picked == 1 && held == begun {"),
    ("--max-rows allows one more", "table.cho", "header && !quoted && a[engine.k_records()] >= most {", "header && !quoted && a[engine.k_records()] > most {"),
    ("a record is not bounded", "table.cho", "                            if held + 1 > cap {", "                            if held + 1 > cap + 100000000 {"),
    ("csv hides that --max-rows stopped it", "table.cho", "    } else if (as_csv || query.count_of(tree, 4) > 0) && c.capped {", "    } else if false && (as_csv || query.count_of(tree, 4) > 0) && c.capped {"),
    ("a record of several lines is one field short", "table.cho", "                                    var found = n + 1;", "                                    var found = n;"),
    ("a record of several lines is not kept for select", "table.cho", "                        } else if inside {\n                            quoted = true;\n                            opened = number;\n                            borrow mut rec as &!rw in {\n                                buffer.clear(rw);\n                            }\n                            if mode != 0 {", "                        } else if inside {\n                            quoted = true;\n                            opened = number;\n                            borrow mut rec as &!rw in {\n                                buffer.clear(rw);\n                            }\n                            if false {"),
    ("the number of names is not capped", "table.cho", "    } else if named > plan.most_names() {\n        e = flag_problem(heap, e, \"args.bad-value\", \"a list names more columns than the ceiling\", \"4096 is the most one flag names\", flag);", "    } else if named > 99999999 {\n        e = flag_problem(heap, e, \"args.bad-value\", \"a list names more columns than the ceiling\", \"4096 is the most one flag names\", flag);"),
    ("the header is kept as a name list of the wrong width", "table.cho", "                                    picked = fpicked;", "                                    picked = fpicked - 1;"),
]


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
