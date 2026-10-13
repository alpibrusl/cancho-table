#!/usr/bin/env python3
"""Mutation check of the Q2 gates (tests/conformance/test_explain.py): `--explain`, the plan said and not run (docs/query.md section 15).

Each mutant is the explain with one defect: the header read past the header, a bound mis-reported, a work class mis-assigned, a threads reason wrong, a rule missing from `refusals.possible`,
a static refusal missed, standard input read, `--format csv` not a document, a flag not found: rebuilt and run against the tests. A mutant is killed when a test fails or the build refuses it.

    python3 scripts/explain_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py. A real run builds each mutant into build/: run it in a checkout of its own.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_explain"]
E = "explain.cho"

MUTANTS = [
    # the header, and nothing but the header
    ("the read goes on past the header", E, "                        got = true;\n                        going = false;", "                        got = true;"),
    ("standard input is read like a file", E, "    } else if !stdin {\n        let Head {", "    } else if true {\n        let Head {"),
    ("a header past the bound is one byte later", E, "if held + 1 > cap {", "if held + 2 > cap {"),
    ("a header past the bound is a line past it", E, "                            abort = 3;", "                            abort = 4;"),
    ("a line past the bound is a header past it", E, "        abort = 4;\n            borrow r as &rr in {", "        abort = 3;\n            borrow r as &rr in {"),
    ("no header is not an unknown column", E, "if mode != 0 && !got && abort == 0 {", "if mode == 0 && !got && abort == 0 {"),
    ("an open quote at the end is refused after a missing header", E, "    if quoted && abort == 0 {", "    if quoted {"),
    ("the byte order mark is not dropped", E, "if number == 1 && len(line) >= 3", "if number == 2 && len(line) >= 3"),
    ("the carriage return stays in the header", E, "                if len(line) > 0 && int_of(line[len(line) - 1]) == 13 {", "                if false && len(line) > 0 && int_of(line[len(line) - 1]) == 13 {"),
    ("a blank line before the header is the header", E, "                if !quoted && len(line) == 0 {", "                if false && len(line) == 0 {"),
    ("the bound of the line is --max-bytes", E, '        let cap = cli.nat(args, parsed, table, "max-line-bytes");', '        let cap = cli.nat(args, parsed, table, "max-bytes");'),
    ("the file size is not asked for", E, "            head = read_head(heap, handle, cap, delim, mode);\n            match file_size(handle) {\n                Done::Ok(n) => {\n                    size = n;", "            head = read_head(heap, handle, cap, delim, mode);\n            match file_size(handle) {\n                Done::Ok(n) => {\n                    size = -1;"),
    ("the delimiter is not given to the reader", E, "head = read_head(heap, handle, cap, delim, mode);", "head = read_head(heap, handle, cap, 44, mode);"),
    # what the header makes of the plan
    ("the plan is not resolved", E, "    if got && mode != 0 {\n        frame.drop(heap, made);", "    if false && got && mode != 0 {\n        frame.drop(heap, made);"),
    ("a refusal of the plan is missed", E, "                abort_now = mr.fabort;", "                abort_now = abort_now;"),
    ("the unknown name is not told", E, "                bad = mr.fbad;", "                bad = 0;"),
    ("the line of the refusal is lost", E, "abort_line: abort_line, long_length: long_length, emitted: 0", "abort_line: 0, long_length: long_length, emitted: 0"),
    ("a refused plan is explained", E, "    if !refused {\n        var reporting = false;", "    if true {\n        var reporting = false;"),
    ("the sort label of the flag is not given to the frame", E, '    var flagged = "";\n    if cli.has(parsed, table, "sort") {\n        flagged = cli.text(args, parsed, table, "sort");\n    }\n    var made', '    var flagged = "";\n    var made'),
    # the plan
    ("a type is written one scale off", E, "        b = buffer.push_nat(heap, b, t - 2);", "        b = buffer.push_nat(heap, b, t - 1);"),
    ("float is int", E, "    } else if t == 21 {\n        b = buffer.append(heap, b, \"\\\"float\\\"\");", "    } else if t == 22 {\n        b = buffer.append(heap, b, \"\\\"float\\\"\");"),
    ("less than is greater than", E, '    if op == 2 {\n        return "<";', '    if op == 2 {\n        return ">";'),
    ("a position is its index", E, "        b = buffer.push_nat(heap, b, cols[i] + 1);", "        b = buffer.push_nat(heap, b, cols[i]);"),
    ("a position named is a name", E, "    if position >= 0 {\n        s = buffer.push(heap, s, byte_of('#'));", "    if position > 0 - 5 && position < 0 {\n        s = buffer.push(heap, s, byte_of('#'));"),
    ("a name that begins with # is not escaped", E, "        if len(given) > 0 && int_of(given[0]) == '#' {", "        if false && len(given) > 0 && int_of(given[0]) == '#' {"),
    ("contains is in", E, '            o = buffer.append(heap, o, "contains");', '            o = buffer.append(heap, o, "in");'),
    ("the values begin at the first literal of the plan", E, "text.append_json(heap, o, query.lit_bytes(tree, first + l))", "text.append_json(heap, o, query.lit_bytes(tree, l))"),
    ("a group column is a selected one", E, "put_column(heap, o, tree, ns + nw + k, resolved, cols, names, ends)", "put_column(heap, o, tree, ns + k, resolved, cols, names, ends)"),
    ("a mean of the column's own scale has one", E, "if mean == 0 || mean == 100 {", "if mean == 0 {"),
    ("the scale of a mean is one off", E, "o = buffer.push_nat(heap, o, mean - 1);", "o = buffer.push_nat(heap, o, mean);"),
    ("an ascending key is descending", E, "o = put_bool(heap, o, flags % 2 == 1);", "o = put_bool(heap, o, flags % 2 == 0);"),
    ("the type of a key is its flags", E, "o = put_type(heap, o, flags / 2);", "o = put_type(heap, o, flags);"),
    ("the report has the key of the sort it is read by", E, "    if reporting {\n        nk = 0;\n    }", "    if false {\n        nk = 0;\n    }"),
    ("the sort label of the flag is not shown", E, "    if len(flagged) > 0 {\n        sort = flagged;\n    }\n    var form", "    var form"),
    ("a limit is shown for the shape", E, "    if mode == 0 || unbounded {\n        o = buffer.append(heap, o, \"null\");", "    if unbounded {\n        o = buffer.append(heap, o, \"null\");"),
    ("a csv plan is a json plan", E, '    if as_csv {\n        o = buffer.append(heap, o, "csv");', '    if false {\n        o = buffer.append(heap, o, "csv");'),
    ("the columns of standard input are known", E, "    if stdin && !as_json {\n        o = buffer.append(heap, o, \"null\");\n    } else if mode == 0 {", "    if false {\n        o = buffer.append(heap, o, \"null\");\n    } else if mode == 0 {"),
    # the work
    ("a grouping is a sort", E, "    } else if mode == 2 {\n        class = 2;", "    } else if ordering && mode == 2 {\n        class = 2;"),
    ("the report is a selection", E, "    if reporting {\n        class = 1;", "    if false {\n        class = 1;"),
    ("a selection reads all rows", E, "if class == 4 && !unbounded {", "if false && class == 4 && !unbounded {"),
    ("a csv selection with no limit stops at a page", E, "if class == 4 && !unbounded {", "if class == 4 {"),
    ("a window holds one row too few", E, "            wanted = from + limit + 1;", "            wanted = from + limit;"),
    ("--top does not bound the window", E, "if top > 0 && (wanted == 0 || top < wanted) {", "if false && top > 0 && (wanted == 0 || top < wanted) {"),
    ("a window past half the bound is held", E, "if wanted > 0 && 2 * wanted + 1 > max_sort {", "if wanted > 0 && 2 * wanted + 1 > max_sort + max_sort {"),
    ("the window bound is a page", E, "        if limit < 100000000 {\n            wanted", "        if limit < 100 {\n            wanted"),
    ("a bound is not told: --max-bytes", E, "    if mode != 0 && !as_csv {\n        o = buffer.append(heap, o, \",\\\"max_bytes\\\":\");", "    if false {\n        o = buffer.append(heap, o, \",\\\"max_bytes\\\":\");"),
    ("a bound is told for csv: --max-bytes", E, "    if mode != 0 && !as_csv {\n        o = buffer.append(heap, o, \",\\\"max_bytes\\\":\");", "    if mode != 0 {\n        o = buffer.append(heap, o, \",\\\"max_bytes\\\":\");"),
    ("a bound is not told: --max-distinct", E, "        if distinct {\n            o = buffer.append(heap, o, \",\\\"max_distinct\\\":\");", "        if false {\n            o = buffer.append(heap, o, \",\\\"max_distinct\\\":\");"),
    ("a bound is not told: --max-groups", E, "    if class == 2 {\n        o = buffer.append(heap, o, \",\\\"max_groups\\\":\");", "    if false {\n        o = buffer.append(heap, o, \",\\\"max_groups\\\":\");"),
    ("a bound is wrong: --max-sort-rows is --max-rows", E, "        o = buffer.push_nat(heap, o, max_sort);\n    }\n    if class == 2 || class == 3 || class == 1 {", "        o = buffer.push_nat(heap, o, max_rows);\n    }\n    if class == 2 || class == 3 || class == 1 {"),
    ("a bound is not told: --max-state-bytes of the report", E, "    if class == 2 || class == 3 || class == 1 {\n        o = buffer.append(heap, o, \",\\\"max_state_bytes\\\":\");", "    if class == 2 || class == 3 {\n        o = buffer.append(heap, o, \",\\\"max_state_bytes\\\":\");"),
    # the threads
    ("one thread has a reason", E, "    if requested > 1 {\n        let psortable", "    if requested >= 1 {\n        let psortable"),
    ("standard input is split", E, "        if stdin {\n            why = \"standard input is read on one core\";", "        if false {\n            why = \"standard input is read on one core\";"),
    ("the shape is split", E, "        } else if mode == 0 {\n            why = \"the shape", "        } else if false {\n            why = \"the shape"),
    ("a sort is split", E, "        } else if ordering && !reporting && !psortable {", "        } else if false {"),
    ("the report is not split", E, "        } else if ordering && !reporting && !psortable {", "        } else if ordering && !psortable {"),
    ("a whole csv sort is not split", E, "let psortable = ordering && !reporting && as_csv && wanted == 0 && top == 0 && from == 0 && unbounded;", "let psortable = false;"),
    ("a csv sort with --top is split", E, "let psortable = ordering && !reporting && as_csv && wanted == 0 && top == 0 && from == 0 && unbounded;", "let psortable = ordering && !reporting && as_csv && wanted == 0 && from == 0 && unbounded;"),
    ("a csv sort with --from is split", E, "let psortable = ordering && !reporting && as_csv && wanted == 0 && top == 0 && from == 0 && unbounded;", "let psortable = ordering && !reporting && as_csv && wanted == 0 && top == 0 && unbounded;"),
    ("a csv sort with a limit is split", E, "let psortable = ordering && !reporting && as_csv && wanted == 0 && top == 0 && from == 0 && unbounded;", "let psortable = ordering && !reporting && as_csv && wanted == 0 && top == 0 && from == 0;"),
    ("--from on a selection is split", E, "        } else if mode != 2 && row_from != 0 {", "        } else if false {"),
    ("--from on a sort is a skip", E, "        if mode == 2 || ordering {\n            row_from = 0;", "        if mode == 2 {\n            row_from = 0;"),
    ("a file of exactly the minimum is not split", E, "} else if size - consumed < min_bytes ||", "} else if size - consumed <= min_bytes ||"),
    ("a file of nothing after the header is split", E, "|| size - consumed <= 0 {", "{"),
    ("the minimum is counted from the start of the file", E, "} else if size - consumed < min_bytes ||", "} else if size < min_bytes ||"),
    ("the threads are one", E, "        } else {\n            effective = requested;", "        } else {\n            effective = 1;"),
    # the refusals the data could still raise
    ("no ragged row is possible", E, '    o = rule(heap, o, "parse.csv-ragged-row");\n', ""),
    ("a record too large is possible for the shape", E, "        if mode != 0 {\n            o = rule(heap, o, \"limit.record-too-large\");", "        if true {\n            o = rule(heap, o, \"limit.record-too-large\");"),
    ("a header is possible after it was read", E, "    if !got {\n        o = rule(heap, o, \"limit.header-too-large\");", "    if true {\n        o = rule(heap, o, \"limit.header-too-large\");"),
    ("an unknown column is not possible for standard input", E, "        if nw + ng + na + nk > 0 {", "        if false {"),
    ("an unknown select is not possible for standard input", E, "        if ns > 0 {\n            o = rule(heap, o, \"select.unknown-column\");", "        if false {\n            o = rule(heap, o, \"select.unknown-column\");"),
    ("a type conflict is not possible for standard input", E, "        if typed > 1 {", "        if false {"),
    ("an unknown sort key is not possible for standard input", E, "        if len(sort) > 0 {\n            o = rule(heap, o, \"sort.unknown-key\");", "        if false {\n            o = rule(heap, o, \"sort.unknown-key\");"),
    ("a condition's int is not read", E, "        let t = query.cond_at(tree, k, 1);\n        if t == 1 {\n            has_int = true;", "        let t = query.cond_at(tree, k, 1);\n        if t == 99 {\n            has_int = true;"),
    ("an aggregate's int is not read", E, "        if f != 0 {\n            if t == 1 {\n                has_int = true;", "        if f != 0 {\n            if t == 99 {\n                has_int = true;"),
    ("a group's decimal is not read", E, "        let t = query.gkind_at(tree, k);\n        if t == 1 {\n            has_int = true;\n        } else if t == 21 {\n            has_float = true;\n        } else if t >= 2 {\n            has_dec = true;", "        let t = query.gkind_at(tree, k);\n        if t == 1 {\n            has_int = true;\n        } else if t == 21 {\n            has_float = true;\n        } else if t >= 99 {\n            has_dec = true;"),
    ("an order key's float is not read", E, "        let t = query.order_flags(tree, k) / 2;\n        if t == 1 {\n            has_int = true;\n        } else if t == 21 {\n            has_float = true;", "        let t = query.order_flags(tree, k) / 2;\n        if t == 1 {\n            has_int = true;\n        } else if t == 99 {\n            has_float = true;"),
    ("a float sum cannot overflow", E, "                if f == 1 {\n                    float_sum = true;", "                if f == 99 {\n                    float_sum = true;"),
    ("a csv answer cannot be too many rows", E, "    if as_csv || ordering {\n        o = rule(heap, o, \"limit.too-many-rows\");", "    if ordering {\n        o = rule(heap, o, \"limit.too-many-rows\");"),
    ("a report cannot be too many rows", E, "    if as_csv || ordering {\n        o = rule(heap, o, \"limit.too-many-rows\");", "    if as_csv || ordering && !reporting {\n        o = rule(heap, o, \"limit.too-many-rows\");"),
    ("a csv page can be too large", E, "    if mode != 0 && !as_csv {\n        o = rule(heap, o, \"limit.output-too-large\");", "    if mode != 0 {\n        o = rule(heap, o, \"limit.output-too-large\");"),
    ("too many distinct values for any grouping", E, "        if distinct {\n            o = rule(heap, o, \"limit.too-many-distinct\");", "        if true {\n            o = rule(heap, o, \"limit.too-many-distinct\");"),
    ("the state is not bounded for a sort", E, "    if class == 2 || class == 3 || class == 1 {\n        o = rule(heap, o, \"limit.state-too-large\");", "    if class == 2 || class == 1 {\n        o = rule(heap, o, \"limit.state-too-large\");"),
    # the notes
    ("an equality is compared as text, with a note", E, "query.cond_at(tree, k, 2) >= 2 && query.cond_at(tree, k, 5) == 1", "query.cond_at(tree, k, 2) >= 0 && query.cond_at(tree, k, 5) == 1"),
    ("a typed comparison is told to be text", E, "query.cond_at(tree, k, 1) == 0 && query.cond_at(tree, k, 2) >= 2", "query.cond_at(tree, k, 1) >= 0 && query.cond_at(tree, k, 2) >= 2"),
    ("a word is a number", E, "    return digits > 0 && points <= 1;", "    return points <= 1;"),
    ("no note for standard input", E, "a name the header lacks is not reported\");\n        first_note = false;\n        if requested > 1 {\n            o = note(", "a name the header lacks is not reported\");\n        first_note = false;\n        if false {\n            o = note("),
    ("a window is told to be all", E, "    if class == 3 && wanted == 0 {\n        var sentence", "    if class == 3 {\n        var sentence"),
    # the call
    ("the flag is not looked for when it is the only one", "inputs.cho", '} else if bytes.starts_with(a, "--explain") {', "} else if false {"),
    ("the flag is looked for only when a query is", "inputs.cho", "has_explain: explaining && cli.has", "has_explain: mentioned && cli.has"),
    ("an explained plan is run", "table.cho", "    if explaining {\n        // `--explain`", "    if false && explaining {\n        // `--explain`"),
    ("an explained csv plan is not a document", "table.cho", "if (streamed || as_csv) && !explained {", "if (streamed || as_csv) {"),
    ("an explained text plan is text", "table.cho", "er, text_mode && !explained,", "er, text_mode,"),
    ("a plan is explained when it is refused", "table.cho", "            explained = fail.count(er) == 0;", "            explained = true;"),
    ("explain is only for the flags", "inputs.cho", "has_explain: inp.has_explain, src_file", "has_explain: false, src_file"),
]

# mutants proved to change nothing a call can see (not run; their sites are checked, so that a stale entry is not a lie about what was proved)
EQUIVALENT = [
    ("a json sort is split", E, "let psortable = ordering && !reporting && as_csv && wanted == 0", "let psortable = ordering && !reporting && wanted == 0",
     "a whole sort is a sort with no window, no cut, no page and no limit (`unbounded`: a limit of 1e15), and the only answer with no limit is csv: a json answer has the default limit of 1,000 or the one asked for, so `as_csv` is implied by `unbounded`"),
    ("a count is read as an integer", E, "        if f != 0 {\n            if t == 1 {", "        if f != 99 {\n            if t == 1 {",
     "a count is added to the plan with the type 0 (`agg.cho` and `plan_body` both write `add_agg(.., 0, -1, 0, 0)`), so reading its type reads nothing"),
    ("--from on a grouping is not split", E, "        } else if mode != 2 && row_from != 0 {", "        } else if row_from != 0 {",
     "`row_from` is zero for a grouping (the groups are paged after all the rows are counted), so the `mode != 2` before it cannot make a difference"),
]

if __name__ == "__main__":
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS, TESTS))
