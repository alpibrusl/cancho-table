#!/usr/bin/env python3
"""Mutation check of JSON lines out (docs/readers.md, R3, section 13; tests/conformance/test_jsonl_out.py): `--format jsonl`.

Each mutant is one defect in how a row, a group or a report row becomes an object, in the keys and their checks, or in how the readers and the threads are told that the rows are JSON
lines; the tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/jsonl_out_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_jsonl_out", "test_jsonl_out_more", "test_jsonl_out_keys", "test_report", "test_rules", "test_authority"]

ROW = "        let to = sel[picked + 2 + j];\n        o = buffer.append(heap, o, tmpl[from..to]);\n        from = to;\n        let at = 3 * sel[j];\n        o = writer.json_cell(heap, o, record, cells[at], cells[at + 1], cells[at + 2]);\n        j = j + 1;\n    }\n    o = buffer.push(heap, o, byte_of(125));\n    return buffer.push(heap, o, byte_of(10));\n}\n"

GK = "            if query.gkind_at(tree, m) != 0 {\n                o = agg.put_key(heap, o, groups, tree, x, m);\n            } else {\n"
QK = "                o = buffer.push(heap, o, byte_of(34));\n                o = agg.put_key(heap, o, groups, tree, x, m);\n                o = buffer.push(heap, o, byte_of(34));\n"

MUTANTS = [
    # a row of a selection
    ("a row's key is not written", "jout.cho", ROW, ROW.replace("tmpl[from..to]", "tmpl[0..0]")),
    ("a row's fragments end one byte early", "jout.cho", ROW, ROW.replace("tmpl[from..to]", "tmpl[from..to - 1]")),
    ("a row does not close its object", "jout.cho", ROW, ROW.replace("    o = buffer.push(heap, o, byte_of(125));\n", "")),
    ("a row has no line feed", "jout.cho", ROW, ROW.replace("    return buffer.push(heap, o, byte_of(10));", "    return o;")),
    ("a row takes the columns in the file's order, not the answer's", "jout.cho", ROW, ROW.replace("let at = 3 * sel[j];", "let at = 3 * j;")),
    ("a quoted cell is written as it was read, its doubled quotes kept", "jout.cho", ROW, ROW.replace("cells[at + 2]);", "0);")),
    ("a row's cell is the next cell's", "jout.cho", ROW, ROW.replace("cells[at], cells[at + 1], cells[at + 2]", "cells[at + 3], cells[at + 4], cells[at + 5]")),
    # a group
    ("a group's aggregate is a string", "jout.cho", "            o = agg.put_value(heap, o, groups, tree, x, m - ng);\n",
     "            o = buffer.push(heap, o, byte_of(34));\n            o = agg.put_value(heap, o, groups, tree, x, m - ng);\n            o = buffer.push(heap, o, byte_of(34));\n"),
    # a typed group key is a number (docs/readers.md N32): the branch of group_row and the writer it calls
    ("a typed group key is a string again (N18)", "jout.cho", GK, GK.replace("                o = agg.put_key(heap, o, groups, tree, x, m);\n", QK)),
    ("a decimal group key is a string", "jout.cho", GK, GK.replace("                o = agg.put_key(heap, o, groups, tree, x, m);\n", "                if query.gkind_at(tree, m) >= 2 && query.gkind_at(tree, m) != 21 {\n                    o = buffer.push(heap, o, byte_of(34));\n                }\n                o = agg.put_key(heap, o, groups, tree, x, m);\n                if query.gkind_at(tree, m) >= 2 && query.gkind_at(tree, m) != 21 {\n                    o = buffer.push(heap, o, byte_of(34));\n                }\n")),
    ("a float group key is a string", "jout.cho", GK, GK.replace("                o = agg.put_key(heap, o, groups, tree, x, m);\n", "                if query.gkind_at(tree, m) == 21 {\n                    o = buffer.push(heap, o, byte_of(34));\n                }\n                o = agg.put_key(heap, o, groups, tree, x, m);\n                if query.gkind_at(tree, m) == 21 {\n                    o = buffer.push(heap, o, byte_of(34));\n                }\n")),
    ("an integer group key is a string", "jout.cho", GK, GK.replace("                o = agg.put_key(heap, o, groups, tree, x, m);\n", "                if query.gkind_at(tree, m) == 1 {\n                    o = buffer.push(heap, o, byte_of(34));\n                }\n                o = agg.put_key(heap, o, groups, tree, x, m);\n                if query.gkind_at(tree, m) == 1 {\n                    o = buffer.push(heap, o, byte_of(34));\n                }\n")),
    ("a typed group key is written as a text key", "jout.cho", GK, GK.replace("!= 0 {", "== 7 {")),
    ("a text group key is written through the typed writer", "jout.cho", GK, GK.replace("!= 0 {", ">= 0 {")),
    ("every group key has the type of the first", "jout.cho", GK, GK.replace("gkind_at(tree, m)", "gkind_at(tree, 0)")),
    ("every group key has the type of the next", "jout.cho", GK, GK.replace("gkind_at(tree, m)", "gkind_at(tree, m + 1)")),
    ("every typed group key is the first key's value", "jout.cho", GK, GK.replace("tree, x, m);", "tree, x, 0);")),
    ("a float group key is written as an integer", "agg.cho", "    if kind == 21 {\n        return flt.put_float(heap, out, v);\n    }\n", "    if kind == 21 {\n        return append_int(heap, out, v);\n    }\n"),
    ("a decimal group key has one decimal too few", "agg.cho", "    return dec.put_scaled(heap, out, v, kind - 2);\n}\n\npub fn put_int", "    return dec.put_scaled(heap, out, v, kind - 3);\n}\n\npub fn put_int"),
    ("a decimal group key has no decimals", "agg.cho", "    return dec.put_scaled(heap, out, v, kind - 2);\n}\n\npub fn put_int", "    return dec.put_scaled(heap, out, v, 0);\n}\n\npub fn put_int"),
    ("the help of --format does not say a typed group key is a number", "spec.cho", " and a group key with a type (--group NAME:int, NAME:dec(S) or NAME:float), which are JSON numbers written as csv writes them (a key is its value: 007 is 7).", ", which are JSON numbers written as csv writes them."),
    ("a text group key is written raw", "jout.cho", "                o = text.append_json(heap, o, agg.key_field(groups, x, m));", "                o = buffer.append(heap, o, agg.key_field(groups, x, m));"),
    ("a group's keys are those of the aggregates", "jout.cho", "        let to = sel[2 + m];", "        let to = sel[2 + m + ng];"),
    # the keys
    ("the first fragment opens no object", "jout.cho", "                frags = buffer.push(heap, frags, byte_of(123));", "                frags = buffer.push(heap, frags, byte_of(44));"),
    ("a key is not followed by its colon", "jout.cho", "            frags = buffer.push(heap, frags, byte_of(58));", "            frags = buffer.push(heap, frags, byte_of(44));"),
    ("a key is written without its escapes", "jout.cho", "            frags = text.append_json(heap, frags, name);", "            frags = buffer.push(heap, buffer.append(heap, buffer.push(heap, frags, byte_of(34)), name), byte_of(34));"),
    ("a name twice is accepted", "jout.cho", "            if found >= 0 {", "            if found >= 1000000000 {"),
    ("a name that is not UTF-8 is accepted", "jout.cho", "        if !text.is_utf8(name) {", "        if false {"),
    ("a name twice is refused as not representable", "jout.cho", "                status = 60;", "                status = 61;"),
    ("the key text is read from the wrong place", "jout.cho", "                        o[picked + 2 + n + k] = int_of(text_bytes[k]);", "                        o[picked + 2 + n + k] = int_of(text_bytes[0]);"),
    ("the ends of the fragments are shifted", "jout.cho", "                                o[picked + 2 + k] = vec.get(er3, k);", "                                o[picked + 2 + k] = vec.get(er3, k) + 1;"),
    # who is told
    ("a JSON line is not counted", "engine.cho", "    a[k_emitted()] = a[k_emitted()] + 1;\n    return (pending, row);\n}\n\n// The last write", "    return (pending, row);\n}\n\n// The last write"),
    ("JSON lines groups are not counted for the page", "finish.cho", "                                a[engine.k_emitted()] = a[engine.k_emitted()] + 1;\n                                var held3 = 0;", "                                var held3 = 0;"),
    # (Not here: the `going = false` after a failed write of the groups' block. It survives and is equivalent: the abort code 9 is set before it and nothing else reads `going` once the groups are written.)
    # the plan
    ("the mark of the header line is written", "jout.cho", "        return data[5..len(data)];", "        return data[0..len(data)];"),
    ("the mark is not dropped from the rows kept", "jout.cho", "    if skip == 0 {", "    if true {"),
    ("the last write keeps the mark", "engine.cho", "        if !jout.is_mark(all) {", "        if false {"),
    ("the threads' answer keeps the mark", "par.cho", "        r = jout.drop_mark(heap, r);", "        r = r;"),
    ("the groups keep the mark", "finish.cho", "        pending = jout.drop_mark(heap, pending);", "        pending = pending;"),
    ("a row does not drop the mark", "engine.cho", "    var pending = jout.drop_mark(heap, rows);", "    var pending = rows;"),
    ("the type report is accepted as JSON lines", "reportflags.cho", '    if jsonl_out {\n        if kind == 2 {', '    if false {\n        if kind == 2 {'),
    ("a sorted run is written with the keys of csv", "psort.cho", "        jsonl_budget = -1;", "        jsonl_budget = 0;"),
    ("a thread is not told the budget of JSON lines", "par.cho", "        if budget < 0 {\n            u[scan.p_budget()] = budget;\n        }", ""),
    ("a row is not keyed", "engine.cho", "    if keyed == 0 {", "    if keyed == 7 {"),
    ("a row is written as csv even when the budget says JSON lines", "engine.cho", "        if budget < 0 {\n            return emit_jsonl", "        if budget < -7 {\n            return emit_jsonl"),
    ("the budget is not set for JSON lines", "table.cho", "        budget = -1;\n", "        budget = budget;\n"),
    ("the partitioned grouping writes csv for JSON lines", "par.cho", "    if !(as_csv && !jout.jsonl(tree) && a[engine.k_sort_field()] < 0", "    if !(as_csv && a[engine.k_sort_field()] < 0"),
    ("a header line is written", "frame.cho", "    if jsonl {\n        // no header line", "    if false {\n        // no header line"),
    ("the plan is not told it is JSON lines", "table.cho", "        tree = jout.set_jsonl(heap, tree);\n", ""),
    ("the explained plan says csv", "explain.cho", '    if jout.jsonl(tree) {\n        o = buffer.append(heap, o, "jsonl");', '    if false {\n        o = buffer.append(heap, o, "jsonl");'),
    ("JSON lines groups are written as csv", "finish.cho", "} else if as_csv && jl {\n                                borrow row", "} else if as_csv && !jl {\n                                borrow row"),
    ("jsonl is not a streamed format", "table.cho", '    let as_csv = bytes.equal(form, "csv") || jsonl_out;\n    let has_select', '    let as_csv = bytes.equal(form, "csv");\n    let has_select'),
    ("a name twice is not refused by the run", "table.cho", "            if c.abort >= 60 && c.abort <= 64 {", "            if c.abort == 61 {"),
    ("a plan with no columns says csv", "table.cho", '        if jsonl_out {\n            e = flags.problem(heap, e, "args.required-flag", "--format jsonl', '        if false {\n            e = flags.problem(heap, e, "args.required-flag", "--format jsonl'),
    ("the explained plan does not refuse a name twice", "explain.cho", "    if abort_now == 60 || abort_now == 61 {", "    if abort_now == 61 {"),
    ("a selection's keys are not made", "frame.cho", "        } else if !query.reporting(tree) {\n            named = picked;", "        } else if false {\n            named = picked;"),
    ("a grouping's keys are made from the selection", "frame.cho", "        if mode == 2 {\n            borrow lends as &er in {\n                named = vec.size(er);", "        if mode == 7 {\n            borrow lends as &er in {\n                named = vec.size(er);"),
    # the refusal's rules (Not here: the `first`/`second` of the detail and the column it names: a refusal of JSON lines is a sentence on standard error, which carries no detail, so no surface shows them)
    ("the rule of a name twice is not in the catalogue", "finish.cho", ";output.duplicate-column|2|never|", ";output.duplicate-colum|2|never|"),
    ("the rule of a name twice exits 8", "finish.cho", ";output.duplicate-column|2|never|", ";output.duplicate-column|8|never|"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
