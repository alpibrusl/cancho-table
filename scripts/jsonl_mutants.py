#!/usr/bin/env python3
"""Mutation check of the JSON-lines reader (docs/readers.md section 12, stage R2/R2b; tests/conformance/test_jsonl.py, test_jsonl_flat.py, test_jsonl_memory.py, test_rules.py).

Each mutant is one defect in how a line is read, how the columns are declared, what a cell is, how a range is cut, or what `--discover` reports; the tool is rebuilt from it and run
against the tests. A mutant is killed when the build refuses it or a test fails. A mutant that survives is either a test to write or an equivalent mutant, and the second is said here, with
the reason, and not left to be found again.

    python3 scripts/jsonl_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py. Real runs: gram, niced, cores 0-5 (the suite is long).

Not here, and why: std.json's own strictness (a leading zero, `NaN`, a lone surrogate, depth 129) is the compiler's `std/json.cho`, whose tests are cancho's; the tests here feed those
lines and require the refusal. The scanner of jflat.cho is judged by `test_jsonl_flat.py` against the tape on the same lines, and its mutants below are the ways it could answer wrongly.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_jsonl_misc", "test_jsonl_csv_out", "test_jsonl", "test_jsonl_flat", "test_rules", "test_describe"]

MUTANTS = [
    # --fields: the grammar (jfields.cho)
    ("a dot escape is no escape", "jfields.cho", "} else if d == '\\\\' || d == '#' || d == '.' {", "} else if d == '\\\\' || d == '#' {"),
    ("a comma escape keeps its backslash in the name", "jfields.cho", "if d == ',' {\n                    names = buffer.push(heap, names, byte_of(','));", "if d == ',' {\n                    names = buffer.push(heap, names, byte_of('\\\\'));\n                    names = buffer.push(heap, names, byte_of(','));"),
    ("the same path twice is accepted", "jfields.cho", "if same(buffer.bytes(nr)[from..to], mine) {", "if false {"),
    ("nine segments are a path", "jfields.cho", "if item_segs > most_segments() {", "if item_segs > most_segments() + 1 {"),
    ("an empty segment is a path", "jfields.cho", "if send == sbegin {", "if false {"),
    ("4096 paths are too many", "jfields.cho", "if item > most_fields() {", "if item >= most_fields() {"),
    ("01 is an index", "jfields.cho", "    if int_of(seg[0]) == '0' {\n        if n == 1 {\n            return 0;\n        }\n        return 0 - 1;\n    }", "    if int_of(seg[0]) == '0' {\n        if n == 1 {\n            return 0;\n        }\n    }"),
    ("an index of 19 digits is read", "jfields.cho", "if n == 0 || n > 18 {", "if n == 0 {"),
    # a record: the cells (jrec.cho)
    ("a missing key is the empty cell under refuse", "jrec.cho", "            if absent == 1 {\n                cells[3 * i] = 0;", "            if true {\n                cells[3 * i] = 0;"),
    ("--absent empty is ignored", "jrec.cho", "            if absent == 1 {\n                cells[3 * i] = 0;", "            if absent == 7 {\n                cells[3 * i] = 0;"),
    ("null is a missing key", "jrec.cho", "            } else if k == 0 {\n                gone = 1;", "            } else if k == 0 {\n                gone = 0;"),
    ("null is the empty cell under refuse", "jrec.cho", "            } else if k == 0 {\n                gone = 1;", "            } else if k == 0 {\n                cells[3 * i] = 0;\n                cells[3 * i + 1] = 0;\n                cells[3 * i + 2] = 0;"),
    ("true is written as 1", "jrec.cho", "                    rec = buffer.append(heap, rec, \"true\");\n                    cells[3 * i + 1] = at + 4;\n                } else {\n                    rec = buffer.append(heap, rec, \"false\");\n                    cells[3 * i + 1] = at + 5;\n                }\n                cells[3 * i] = at;\n                cells[3 * i + 2] = 0;\n            } else if k == 0 {", "                    rec = buffer.append(heap, rec, \"1\");\n                    cells[3 * i + 1] = at + 1;\n                } else {\n                    rec = buffer.append(heap, rec, \"false\");\n                    cells[3 * i + 1] = at + 5;\n                }\n                cells[3 * i] = at;\n                cells[3 * i + 2] = 0;\n            } else if k == 0 {"),
    ("false is written as true", "jrec.cho", "                    rec = buffer.append(heap, rec, \"false\");\n                    cells[3 * i + 1] = at + 5;\n                }\n                cells[3 * i] = at;\n                cells[3 * i + 2] = 0;\n            } else if k == 0 {", "                    rec = buffer.append(heap, rec, \"fals\");\n                    cells[3 * i + 1] = at + 4;\n                }\n                cells[3 * i] = at;\n                cells[3 * i + 2] = 0;\n            } else if k == 0 {"),
    ("an escaped string is not decoded", "jrec.cho", "                if tape[3 * node] >> 3 == 0 {\n                    cells[3 * i] = tape[3 * node + 1];", "                if true {\n                    cells[3 * i] = tape[3 * node + 1];"),
    ("a number is cut one short", "jrec.cho", "            } else if k == 3 || k == 4 {\n                cells[3 * i] = tape[3 * node + 1];\n                cells[3 * i + 1] = tape[3 * node + 2];", "            } else if k == 3 || k == 4 {\n                cells[3 * i] = tape[3 * node + 1];\n                cells[3 * i + 1] = tape[3 * node + 2] - 1;"),
    ("an object in a cell is not refused", "jrec.cho", "            } else {\n                a[k_err_col()] = i;\n                a[k_err_fn()] = 0;", "            } else if k == 99 {\n                a[k_err_col()] = i;\n                a[k_err_fn()] = 0;"),
    ("an array in a cell is called an object", "jrec.cho", "                if k == 6 {\n                    a[k_err_fn()] = 1;\n                }", "                if k == 99 {\n                    a[k_err_fn()] = 1;\n                }"),
    ("an array index one past the end is read", "jrec.cho", "                        if ix < 0 || ix >= tape[3 * node + 1] {", "                        if ix < 0 || ix > tape[3 * node + 1] {"),
    ("a step through a scalar is a missing key", "jrec.cho", "                    } else {\n                        gone = 2;\n                    }", "                    } else {\n                        gone = 1;\n                    }"),
    ("the second duplicate wins (general path)", "jrec.cho", "                                if found >= 0 {\n                                    twice = step;\n                                } else {\n                                    found = j + 1;\n                                }", "                                if found >= 0 {\n                                    found = j + 1;\n                                } else {\n                                    found = j + 1;\n                                }"),
    ("the second duplicate wins (one key per path)", "jrec.cho", "                            if cells[3 * f] == 0 - 1 {\n                                cells[3 * f] = j + 1;\n                            } else {\n                                cells[3 * f] = 0 - 3;\n                                cells[3 * f + 1] = 0;\n                            }", "                            cells[3 * f] = j + 1;"),
    ("a key with an escape is compared as written", "jrec.cho", "                    if table[sg + 1] - table[sg] == kl || w0 >> 3 != 0 {", "                    if table[sg + 1] - table[sg] == kl {"),
    ("the row of a refusal is one low", "jrec.cho", "    a[k_err_row()] = a[k_records()] + 1;", "    a[k_err_row()] = a[k_records()];"),
    ("the offset of a syntax error is one off", "jrec.cho", "            a[k_err_digits()] = json.error_position(n);\n            if looks_like_document(line) && a[k_records()] == 0 {\n                a[k_err_cond()] = 1;\n            } else {\n                a[k_err_cond()] = 0;\n            }\n            return (fail_with(a, a_syntax()), rec);", "            a[k_err_digits()] = json.error_position(n) + 1;\n            if looks_like_document(line) && a[k_records()] == 0 {\n                a[k_err_cond()] = 1;\n            } else {\n                a[k_err_cond()] = 0;\n            }\n            return (fail_with(a, a_syntax()), rec);"),
    ("the node limit is a syntax error", "jrec.cho", "            if code == 8 {\n                return (fail_with(a, a_nodes()), rec);\n            }", "            if code == 99 {\n                return (fail_with(a, a_nodes()), rec);\n            }"),
    ("a document is not looked for on row 1", "jrec.cho", "fn looks_like_document[&l](line: &l [byte]) -> [] bool {\n    var i = 0;", "fn looks_like_document[&l](line: &l [byte]) -> [] bool {\n    if true {\n        return false;\n    }\n    var i = 0;"),
    ("a blank line holds only a space", "jrec.cho", "        if c != ' ' && c != 9 && c != 13 {", "        if c != ' ' {"),
    ("a field with a comma is not quoted for csv", "jrec.cho", "if qdelim >= 0 && index_of_byte(line[tape[3 * node + 1]..tape[3 * node + 2]], byte_of(qdelim)) >= 0 {", "if false && index_of_byte(line[tape[3 * node + 1]..tape[3 * node + 2]], byte_of(qdelim)) >= 0 {"),
    ("a decoded field with a newline is not quoted for csv", "jrec.cho", "odd = index_of_byte(text, byte_of(qdelim)) >= 0 || index_of_byte(text, byte_of(10)) >= 0;", "odd = index_of_byte(text, byte_of(qdelim)) >= 0;"),
    ("a quote in a quoted cell is not doubled", "jrec.cho", "            out = buffer.push(heap, out, byte_of(34));\n            p = p + found + 1;", "            p = p + found + 1;"),
    ("the quoted cell is flag 0", "jrec.cho", "    cells[3 * i + 2] = 1;\n    return out;", "    cells[3 * i + 2] = 0;\n    return out;"),
    # the scanner (jflat.cho)
    ("the scanner takes a leading zero", "jflat.cho", "    if int_of(line[p]) == '0' {\n        p = p + 1;\n    } else if", "    if int_of(line[p]) == '0' {\n        p = digits(line, p);\n    } else if"),
    ("the scanner takes a bare point", "jflat.cho", "        let q = digits(line, p + 1);\n        if q == p + 1 {\n            return 0 - 1;\n        }", "        let q = digits(line, p + 1);"),
    ("the scanner takes an empty exponent", "jflat.cho", "        let r = digits(line, q);\n        if r == q {\n            return 0 - 1;\n        }", "        let r = digits(line, q);"),
    ("the scanner takes a plus sign", "jflat.cho", "        } else if c == '-' || c >= '0' && c <= '9' {", "        } else if c == '-' || c == '+' || c >= '0' && c <= '9' {"),
    ("the scanner takes a backslash in a string", "jflat.cho", "        if c < 32 || c == '\\\\' || c >= 128 {", "        if c < 32 || c >= 128 {"),
    ("the scanner takes a control byte in a string", "jflat.cho", "        if c < 32 || c == '\\\\' || c >= 128 {", "        if c == '\\\\' || c >= 128 {"),
    ("the scanner takes a byte past ASCII in a string", "jflat.cho", "        if c < 32 || c == '\\\\' || c >= 128 {", "        if c < 32 || c == '\\\\' {"),
    ("the scanner takes a key twice", "jflat.cho", "                if found[3 * f] != 0 - 1 {\n                    // a key twice: a refusal, which std.json's path says\n                    return 1;\n                }", ""),
    ("the scanner takes a trailing comma", "jflat.cho", "        if int_of(line[p]) == ',' {\n            p = blanks(line, p + 1);\n        } else if", "        if int_of(line[p]) == ',' {\n            p = blanks(line, p + 1);\n            if p < n && int_of(line[p]) == '}' {\n                return 0;\n            }\n        } else if"),
    ("the scanner takes data after the object", "jflat.cho", "            if blanks(line, p + 1) != n {\n                return 1;\n            }\n            going = false;", "            going = false;"),
    ("the scanner takes a nested value", "jflat.cho", "        } else {\n            return 1;\n        }\n        // the key against the declared names", "        } else if c == '[' && word_at(line, p, \"[]\") {\n            kind = 4;\n            p = p + 2;\n            v1 = p;\n        } else {\n            return 1;\n        }\n        // the key against the declared names"),
    ("the scanner ignores the node limit", "jflat.cho", "        if 1 + 2 * pairs > nodes {\n            return 1;\n        }", ""),
    ("the scanner takes a LF as blank", "jflat.cho", "(int_of(line[p]) == ' ' || int_of(line[p]) == 9 || int_of(line[p]) == 13)", "(int_of(line[p]) == ' ' || int_of(line[p]) == 9 || int_of(line[p]) == 13 || int_of(line[p]) == 11)"),
    ("the scanner finds a key by its prefix", "jflat.cho", "if s1 - s0 == klen && bytes.equal(line[k0..ke], sbytes[s0..s1]) {", "if s1 - s0 <= klen && bytes.equal(line[k0..k0 + (s1 - s0)], sbytes[s0..s1]) {"),
    ("the scanner reports a string one byte long", "jrec.cho", "            cells[3 * i] = v0;\n            cells[3 * i + 1] = v1;", "            cells[3 * i] = v0;\n            cells[3 * i + 1] = v1 - 1;"),
    # the loops (jsonl.cho, jscan.cho)
    ("the byte order mark is kept", "jsonl.cho", "if number == 1 && len(line) >= 3 && int_of(line[0]) == 239", "if number == 2 && len(line) >= 3 && int_of(line[0]) == 239"),
    ("a CR is kept", "jsonl.cho", "                                    if len(line) > 0 && int_of(line[len(line) - 1]) == 13 {", "                                    if len(line) > 0 && int_of(line[len(line) - 1]) == 14 {"),
    ("a blank line is a record", "jsonl.cho", "                                    if jrec.blank(line) {", "                                    if len(line) == 0 {"),
    ("--max-rows is not honoured", "jsonl.cho", "} else if a[engine.k_records()] >= most {\n                                        a[engine.k_capped()] = 1;", "} else if a[engine.k_records()] >= most + 1 {\n                                        a[engine.k_capped()] = 1;"),
    ("a full page is not noticed", "jsonl.cho", "a[engine.k_records()] >= from && a[engine.k_emitted()] >= limit {\n                                        a[engine.k_more()] = 1;", "a[engine.k_records()] >= from && a[engine.k_emitted()] > limit {\n                                        a[engine.k_more()] = 1;"),
    ("a refusal does not stop the read", "jsonl.cho", "                                        if shape >= 50 {\n                                            a[engine.k_abort_line()] = number;\n                                            a[engine.k_err_line()] = number;\n                                            kept = jrec.keep_facts(heap, kept, shape, line, contents(tbr), buffer.bytes(sbr), nfields, a);\n                                            going = false;", "                                        if shape >= 50 {\n                                            a[engine.k_abort_line()] = number;\n                                            a[engine.k_err_line()] = number;\n                                            kept = jrec.keep_facts(heap, kept, shape, line, contents(tbr), buffer.bytes(sbr), nfields, a);"),
    ("the line of a refusal is the row", "jsonl.cho", "                                            a[engine.k_abort_line()] = number;\n                                            a[engine.k_err_line()] = number;\n                                            kept", "                                            a[engine.k_abort_line()] = a[engine.k_records()] + 1;\n                                            a[engine.k_err_line()] = number;\n                                            kept"),
    ("--from does not skip records", "jsonl.cho", "} else if engine.count_row(a, nfields, number, row_from) && mode != 0 {", "} else if engine.count_row(a, nfields, number, 0) && mode != 0 {"),
    ("standard input is read as a file", "jsonl.cho", "                                if stdin {\n                                    r = bulk.fill_stdin(r, io);", "                                if false {\n                                    r = bulk.fill_stdin(r, io);"),
    ("the tape is one node too big", "jsonl.cho", "    var tape = box_slice(heap, 3 * nodes, 0);", "    var tape = box_slice(heap, 3 * nodes + 3, 0);"),
    ("the tape is one node too big for a worker", "jscan.cho", "    var tape = box_slice(heap, 3 * nodes, 0);", "    var tape = box_slice(heap, 3 * nodes + 3, 0);"),
    ("the shape is not read by threads' ranges only for files", "jsonl.cho", "if going && mode != 0 && !stdin && threads > 1", "if going && mode != 0 && threads > 1"),
    ("a range does not end at the next range's first line", "jscan.cho", "                if at >= until {\n                    // The next range's, whatever it holds: where this one ends.", "                if at > until {\n                    // The next range's, whatever it holds: where this one ends."),
    ("a range reads a blank line before it looks at where it is", "jscan.cho", "                    if at == 0 && len(line) >= 3 && int_of(line[0]) == 239", "                    if at == 1 && len(line) >= 3 && int_of(line[0]) == 239"),
    ("a range does not stop at the row bound", "jscan.cho", "                    } else if a[engine.k_records()] >= most {\n                        a[engine.k_capped()] = 1;", "                    } else if a[engine.k_records()] > most {\n                        a[engine.k_capped()] = 1;"),
    ("a range counts the lines of the next one", "jscan.cho", "                    consumed = number - 1;\n                    next_at = at;", "                    consumed = number;\n                    next_at = at;"),
    ("the declaration is not handed to the workers", "jscan.cho", "    u[at + 3] = len(table);", "    u[at + 3] = 0;"),
    ("the segment bytes are not handed to the workers", "jscan.cho", "        u[at + 4 + len(table) + i] = int_of(sbytes[i]);", "        u[at + 4 + len(table) + i] = 0;"),
    ("a worker finds its declaration at the parent's offset", "par.cho", "            u[jscan.p_format()] = j_size();", "            u[jscan.p_format()] = scan.p_count();"),
    ("the dispatcher always reads csv", "dispatch.cho", "    if par[jscan.p_format()] != 0 {", "    if false {"),
    ("a worker guesses how its range starts", "par.cho", "if contents(w.par)[j_spec()] == 1 && contents(w.par)[jscan.p_format()] == 0 {", "if contents(w.par)[j_spec()] == 1 {"),
    ("the ranges are not cut at a line start", "par.cho", "        // Where the ranges begin: `cur`, then the first line start at or after each chunk's worth.\n        borrow mut plan as &!pw in {\n            let pl = contents(pw);\n            pl[0] = cur;\n            var j = 1;\n            while j <= n {\n                var edge = align(heap, file, cur + j * chunk, size);", "        // Where the ranges begin: `cur`, then the first line start at or after each chunk's worth.\n        borrow mut plan as &!pw in {\n            let pl = contents(pw);\n            pl[0] = cur;\n            var j = 1;\n            while j <= n {\n                var edge = cur + j * chunk;"),
    ("a refusal in a worker is taken as it is", "par.cho", "                        wave_bad = true;\n                    }\n                    if agg.get_i64(sl, at) == 0 && first == cur {", "                        wave_bad = true;\n                    }\n                    if first == cur {"),
    # the flags (jflags.cho)
    ("a plan on JSON lines needs no --fields", "jflags.cho", "    if !has_fields {\n        return fields_required(heap, e, args, parsed);", "    if false {\n        return fields_required(heap, e, args, parsed);"),
    ("--fields is allowed with csv", "jflags.cho", "if has_fields || has_absent || has_nodes || has_discover || has_lines || has_paths {", "if has_absent || has_nodes || has_discover || has_lines || has_paths {"),
    ("--absent is allowed with csv", "jflags.cho", "if has_fields || has_absent || has_nodes || has_discover || has_lines || has_paths {", "if has_fields || has_nodes || has_discover || has_lines || has_paths {"),
    ("--max-json-nodes is not bounded", "jflags.cho", "if n < 1 || n > most_nodes() {", "if n < 1 {"),
    ("--max-json-nodes of 0 is accepted", "jflags.cho", "if n < 1 || n > most_nodes() {", "if n > most_nodes() {"),
    ("--discover takes --fields", "jflags.cho", "if has_fields || plan || csv_out {", "if plan || csv_out {"),
    ("--discover takes a plan", "jflags.cho", "if has_fields || plan || csv_out {", "if has_fields || csv_out {"),
    ("--discover-lines is not bounded", "jflags.cho", "if n < 1 || n > most_lines() {", "if n < 1 {"),
    ("--max-fields is not bounded", "jflags.cho", "if n < 1 || n > 4096 {", "if n < 1 {"),
    ("--discover-lines without --discover is allowed", "jflags.cho", "    if has_lines || has_paths {\n        e = flags.problem(", "    if false {\n        e = flags.problem("),
    ("the repair of the missing --fields keeps the plan", "jflags.cho", "    o = json.put_string(heap, o, \"--discover\");", "    o = json.put_string(heap, o, \"--discover\");\n    o = json.put_string(heap, o, \"--limit\");"),
    # the refusals (finish.cho)
    ("a missing field offers no repair", "finish.cho", "    if c.abort == 52 {\n        let at = cli.value_index(parsed, table, \"absent\");", "    if c.abort == 99 {\n        let at = cli.value_index(parsed, table, \"absent\");"),
    ("a refusal does not name the column", "finish.cho", "    if column {\n        w = fail.detail_text(heap, w, \"column\", name_of(names, hends, c.err_col));\n    }", "    if false {\n        w = fail.detail_text(heap, w, \"column\", name_of(names, hends, c.err_col));\n    }"),
    ("the near bytes are not quoted", "finish.cho", "        w = fail.detail_text(heap, w, \"near\", buffer.bytes(kept));", "        w = fail.detail_text(heap, w, \"near\", \"\");"),
    ("looks_like is said for every line", "finish.cho", "    if (c.abort == 50 || c.abort == 51) && c.err_cond == 1 {", "    if (c.abort == 50 || c.abort == 51) {"),
    ("a null is called missing", "finish.cho", "        if c.err_fn == 1 {\n            reason = \"null\";", "        if c.err_fn == 9 {\n            reason = \"null\";"),
    ("the json code names are shifted", "finish.cho", "    if code == 3 {\n        return \"control\";", "    if code == 3 {\n        return \"escape\";"),
    # discover (jdisc.cho)
    ("an object at the eighth segment is expanded", "jdisc.cho", "tape[3 * node + 1] > 0 && depth < 8 {", "tape[3 * node + 1] > 0 && depth < 9 {"),
    ("a dot in a key is not escaped", "jdisc.cho", "        if c == ',' || c == '\\\\' || c == '.' {", "        if c == ',' || c == '\\\\' {"),
    ("a comma in a key is not escaped", "jdisc.cho", "        if c == ',' || c == '\\\\' || c == '.' {", "        if c == '\\\\' || c == '.' {"),
    ("an empty key is a path", "jdisc.cho", "    if n <= 0 {\n        return 0 - 1;", "    if n < 0 {\n        return 0 - 1;"),
    ("an empty object is expanded", "jdisc.cho", "tape[3 * node + 1] > 0 && depth < 8 {", "depth < 8 {"),
    ("the report is complete at the examined bound", "jdisc.cho", "                                    } else if examined >= wanted {\n                                        // another record after the last one examined: the report is not of the whole input\n                                        going = false;", "                                    } else if examined >= wanted {\n                                        complete = true;\n                                        going = false;"),
    ("the report is never complete", "jdisc.cho", "            } else if status == lines.done() {\n                                complete = true;", "            } else if status == lines.done() {\n                                complete = false;"),
    ("paths are never truncated", "jdisc.cho", "        if n >= room {\n            cut = true;", "        if n >= room {\n            cut = false;"),
    ("a path past the room is listed", "jdisc.cho", "        if n >= room {\n            cut = true;", "        if n >= room + 1 {\n            cut = true;"),
    ("an array is listed twice", "jdisc.cho", "if kind == 6 && vec.get(cw, id * kinds() + kind) == 1 {", "if kind == 6 {"),
    ("missing is not said", "jdisc.cho", "                    if examined > seen {", "                    if false {"),
    ("an int is a number", "jdisc.cho", "    if k == 3 {\n        return 1;\n    }\n    if k == 4 {\n        return 2;\n    }", "    if k == 3 || k == 4 {\n        return 1;\n    }"),
    ("the first line of a path is the last", "jdisc.cho", "            f = vec.push(heap, f, line);", "            f = vec.push(heap, f, line + 1);"),
    # explain (explain.cho)
    ("an explained JSON-lines plan says csv", "explain.cho", "\\\"format\\\":\\\"jsonl\\\",\\\"delimiter\\\":null", "\\\"format\\\":\\\"csv\\\",\\\"delimiter\\\":null"),
    ("an explained JSON-lines plan does not list its fields", "explain.cho", "        k = 0;\n        while k < columns {\n            if k > 0 {\n                o = buffer.push(heap, o, byte_of(','));\n            }\n            o = text.append_json(heap, o, finish.name_of(names, ends, k));\n            k = k + 1;\n        }\n        o = buffer.append(heap, o, \"],\\\"absent\\\":\\\"\");", "        o = buffer.append(heap, o, \"\\\"x\\\"],\\\"absent\\\":\\\"\");"),
    ("an explained JSON-lines plan on standard input has no columns", "explain.cho", "    if stdin && !as_json {\n        o = buffer.append(heap, o, \"null\");", "    if stdin {\n        o = buffer.append(heap, o, \"null\");"),
    ("an explained JSON-lines plan may refuse a csv quote", "explain.cho", "        o = rule(heap, o, \"parse.jsonl-syntax\");", "        o = rule(heap, o, \"parse.csv-bad-quote\");"),
    ("an explained JSON-lines plan on standard input is read", "explain.cho", "        head = Head { names: fnames, ends: fends, got: true, abort: 0,", "        head = Head { names: fnames, ends: fends, got: false, abort: 0,"),
    # the plan's inputs (inputs.cho, table.cho)
    ("ndjson is not jsonl", "inputs.cho", "jsonl = bytes.equal(form, \"jsonl\") || bytes.equal(form, \"ndjson\");", "jsonl = bytes.equal(form, \"jsonl\");"),
    ("jsonl is not ndjson", "inputs.cho", "jsonl = bytes.equal(form, \"jsonl\") || bytes.equal(form, \"ndjson\");", "jsonl = bytes.equal(form, \"ndjson\");"),
    ("a JSON-lines read is not chosen", "table.cho", "    if as_json {\n        return jsonl.read(", "    if false {\n        return jsonl.read("),
    ("--discover is not looked for", "table.cho", "    if as_json && cli.has(parsed, flag_table(), \"discover\") {", "    if false && cli.has(parsed, flag_table(), \"discover\") {"),
    ("a flag of the family is not looked for", "inputs.cho", "|| bytes.starts_with(a, \"--max-fields\") {\n            jmention = true;", "{\n            jmention = true;"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
