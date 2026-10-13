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

TESTS = ["test_jsonl_out", "test_jsonl_out_more", "test_report", "test_rules", "test_authority"]

ROW = "        let to = sel[picked + 2 + j];\n        o = buffer.append(heap, o, tmpl[from..to]);\n        from = to;\n        let at = 3 * sel[j];\n        o = writer.json_cell(heap, o, record, cells[at], cells[at + 1], cells[at + 2]);\n        j = j + 1;\n    }\n    o = buffer.push(heap, o, byte_of(125));\n    return buffer.push(heap, o, byte_of(10));\n}\n"

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
    ("a typed group key is not quoted", "jout.cho", "                o = buffer.push(heap, o, byte_of(34));\n                o = agg.put_key(heap, o, groups, tree, x, m);\n                o = buffer.push(heap, o, byte_of(34));\n",
     "                o = agg.put_key(heap, o, groups, tree, x, m);\n"),
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
    ("the readers are not told the rows are JSON lines", "engine.cho", "    a[k_jsonl()] = 1;\n", "    a[k_jsonl()] = 0;\n"),
    ("a row is written as csv even when told", "engine.cho", "        if a[k_jsonl()] != 0 {\n            borrow row", "        if a[k_jsonl()] == 7 {\n            borrow row"),
    ("a JSON line is not counted", "engine.cho", "            a[k_emitted()] = a[k_emitted()] + 1;\n            return (pending, row);\n        }\n        var begun", "            return (pending, row);\n        }\n        var begun"),
    ("the key text is not made for a thread", "par.cho", "            scratch = engine.begin_jsonl(w.heap, scratch, contents(w.sel), contents(w.par)[scan.p_picked()], t);\n", ""),
    ("the key text is not made for a sorted run", "psort.cho", "            scratch = engine.begin_jsonl(heap, scratch, sel, picked, contents(tw));\n", ""),
    ("the partitioned grouping writes csv for JSON lines", "par.cho", "    if !(as_csv && a[engine.k_jsonl()] == 0 && a[engine.k_sort_field()] < 0", "    if !(as_csv && a[engine.k_sort_field()] < 0"),
    ("a header line is written", "finish.cho", "    if budget < 0 {\n        buffer.drop(heap, lead);", "    if budget < -5 {\n        buffer.drop(heap, lead);"),
    ("JSON lines groups are written as csv", "finish.cho", "} else if as_csv && a[engine.k_jsonl()] != 0 {\n                                borrow row", "} else if as_csv && a[engine.k_jsonl()] == 7 {\n                                borrow row"),
    ("JSON lines groups are not counted for the page", "finish.cho", "                                a[engine.k_emitted()] = a[engine.k_emitted()] + 1;\n                                var held3 = 0;", "                                var held3 = 0;"),
    ("the type report is written as csv", "finish.cho", "} else if as_csv && a[engine.k_jsonl()] != 0 {\n                pending = report.put_object", "} else if as_csv && a[engine.k_jsonl()] == 7 {\n                pending = report.put_object"),
    # (Not here: the `going = false` after a failed write of the groups' block. It survives and is equivalent: the abort code 9 is set before it and nothing else reads `going` once the groups are written.)
    # the plan
    ("the budget is not set for JSON lines", "table.cho", "        budget = -1;\n", "        budget = budget;\n"),
    ("jsonl is not a streamed format", "table.cho", '    let as_csv = bytes.equal(form, "csv") || jsonl_out;\n    let has_select', '    let as_csv = bytes.equal(form, "csv");\n    let has_select'),
    ("a name twice is not refused by the run", "table.cho", "            if c.abort == 60 || c.abort == 61 {", "            if c.abort == 61 {"),
    ("a plan with no columns says csv", "table.cho", '        if jsonl_out {\n            e = flags.problem(heap, e, "args.required-flag", "--format jsonl', '        if false {\n            e = flags.problem(heap, e, "args.required-flag", "--format jsonl'),
    ("the explained plan says csv", "explain.cho", '    if jsonl_out {\n        o = buffer.append(heap, o, "jsonl");', '    if false {\n        o = buffer.append(heap, o, "jsonl");'),
    ("the explained plan does not refuse a name twice", "explain.cho", "    if abort_now == 60 || abort_now == 61 {", "    if abort_now == 61 {"),
    ("a selection's keys are not made", "frame.cho", "        } else if !query.reporting(tree) {\n            named = picked;", "        } else if false {\n            named = picked;"),
    ("the type report has keys of a selection", "frame.cho", "        } else if !query.reporting(tree) {\n            named = picked;", "        } else if true {\n            named = picked;"),
    ("a grouping's keys are made from the selection", "frame.cho", "        if mode == 2 {\n            borrow lends as &er in {\n                named = vec.size(er);", "        if mode == 7 {\n            borrow lends as &er in {\n                named = vec.size(er);"),
    ("a type report row has no key", "report.cho", "            o = text.append_json(heap, o, all[key_start..key_end]);\n            o = buffer.push(heap, o, byte_of(':'));", "            o = buffer.push(heap, o, byte_of(':'));"),
    ("a type report row is not closed", "report.cho", "    buffer.drop(heap, page);\n    return buffer.push(heap, o, byte_of('}'));", "    buffer.drop(heap, page);\n    return o;"),
    ("the second value of a type report row is lost", "report.cho", "            if int_of(a[p]) == ',' {\n                p = p + 1;\n            }\n            k = k + 1;", "            if int_of(a[p]) == ',' {\n                p = p + 2;\n            }\n            k = k + 1;"),
    # the refusal's rules (Not here: the `first`/`second` of the detail and the column it names: a refusal of JSON lines is a sentence on standard error, which carries no detail, so no surface shows them)
    ("the rule of a name twice is not in the catalogue", "finish.cho", ";output.duplicate-column|2|never|", ";output.duplicate-colum|2|never|"),
    ("the rule of a name twice exits 8", "finish.cho", ";output.duplicate-column|2|never|", ";output.duplicate-column|8|never|"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
