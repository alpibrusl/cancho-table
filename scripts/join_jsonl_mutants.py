#!/usr/bin/env python3
"""Mutation check of the join with a JSON-lines side or a build with a delimiter of its own (docs/joins.md section 21, slice J5, gate M; tests/conformance/test_join_j5.py).

Each mutant is one defect in how the formats of the two files are told from the flags (the defaults of the build, the declarations, the flags that name nothing), how a JSON-lines row is read for
a join and handed to it as a csv record (the first line, a blank line, the end of a line, the bound on the rows, the row and line of a refusal, the facts kept for it), how a csv build with its own
delimiter is re-delimited, how the loops hand the rows to the rest of the join, what `--explain` says of the two readers, and how a refusal names its file; the tool is rebuilt from it and run against the
tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/join_jsonl_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py. Real runs: the Mac and gram (niced, cores 0-5, one at a time).

Equivalent mutant, found by the first real run and left out: the tally's `groups` set when the probe ends in abort 71 (`a[engine.k_groups()] = st[jnread.s_produced()]`, carried over from the loops this is copied from):
nothing reads it for that abort, the refusal is made from `err_row`, `err_line`, `err_digits` and `err_cond` (test_join_j5.SharedBudget compares them with the csv probe's, and they are equal with the line removed).

Not here, and why: the reading of a JSON line itself (`jrec.extract`: strictness, escapes, the flat scanner, the tape) is the reader of docs/readers.md section 12, whose mutants are scripts/jsonl_mutants.py and whose
tests are test_jsonl*.py; here the line is given to it and its answer is what is judged. The loops of J1 to J3b (`jnread`, `jn3pass`) are not edited by this slice, and their mutants are scripts/join_mutants.py's.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

# cheap classes first: a killed mutant stops at the first failure (mutlib runs `unittest -f`)
TESTS = ["test_join_j5.Flags", "test_join_j5.Refusals", "test_join_j5.Explain", "test_join_j5.Query", "test_join_j5.SharedBudget", "test_join_j5.Identity", "test_join_j5.Plans", "test_join_j5.Mcp",
         "test_join_j5.ChunkEdges", "test_join_j5.Oracle", "test_join_j5.Accounting", "test_join_j5.Authority", "test_rules", "test_skill"]

MUTANTS = [
    # the formats, from the flags (jnfmt.cho)
    ("the build is csv unless it is told otherwise", "jnfmt.cho", "    var b_jsonl = p_jsonl;", "    var b_jsonl = false;"),
    ("the build has no declaration of the probe's to inherit", "jnfmt.cho", "    var b_fields = p_fields;", "    var b_fields = \"\";"),
    ("ndjson is not a spelling of jsonl", "jnfmt.cho", "    return bytes.equal(form, \"jsonl\") || bytes.equal(form, \"ndjson\");", "    return bytes.equal(form, \"jsonl\");"),
    ("a --join-delimiter that names no delimiter is accepted", "jnfmt.cho", "        } else if f.b_delim < 0 {", "        } else if f.b_delim < 0 - 5 {"),
    ("a --join-delimiter beside a jsonl build is accepted", "jnfmt.cho", "    if f.b_delim_given {\n        if f.b_jsonl {", "    if f.b_delim_given {\n        if false {"),
    ("--join-fields beside a csv build is accepted", "jnfmt.cho", "    if f.b_fields_given && !f.b_jsonl {", "    if false {"),
    ("--fields beside a csv probe is accepted", "jnfmt.cho", "    if f.p_fields_given && !f.p_jsonl {", "    if false {"),
    ("--absent and --max-json-nodes beside two csv files are accepted", "jnfmt.cho", "    if (has_absent || has_nodes) && !f.p_jsonl && !f.b_jsonl {", "    if false {"),
    ("--max-json-nodes of any size is accepted", "jnfmt.cho", "        if n < 1 || n > 1048576 {", "        if n < 0 {"),
    ("--discover goes with a join", "jnfmt.cho", "    if has_discover {", "    if false {"),
    ("a jsonl build with no declaration is accepted", "jnfmt.cho", "        } else if !f.p_jsonl {", "        } else if false {"),
    ("the declaration of the probe is not checked", "jnfmt.cho", "            e = declaration(heap, e, \"--fields\", f.p_fields);", "            e = e;"),
    ("the declaration of the build is not checked", "jnfmt.cho", "            e = declaration(heap, e, \"--join-fields\", f.b_fields);", "            e = e;"),
    ("the delimiter of the build is the probe's", "jnfmt.cho", "        return f.b_delim;", "        return delim;"),
    ("a build with a delimiter of its own is read as the probe's", "jnfmt.cho", "    return f.b_jsonl || f.b_delim_given && f.b_delim != delim;", "    return f.b_jsonl;"),
    ("a build with the probe's delimiter, said again, is foreign", "jnfmt.cho", "    return f.b_jsonl || f.b_delim_given && f.b_delim != delim;", "    return f.b_jsonl || f.b_delim_given;"),
    # the flags of a join that is not one, and the check of the formats
    ("the formats of a join are not checked", "jnflags.cho", "    e = jnfmt.check(heap, args, parsed, inp.jmention, e);", "    e = e;"),
    ("--join-fields without --join is not refused", "jnflags.cho", "|| cli.has(parsed, table, \"join-input-format\") || cli.has(parsed, table, \"join-fields\") || cli.has(parsed, table, \"join-delimiter\") || cli.has(parsed, table, \"join-sorted\") {", "|| cli.has(parsed, table, \"join-sorted\") {"),
    ("the checks of one JSON-lines file run for a join as well", "table.cho", "    if inp.jmention && !inp.has_join {", "    if inp.jmention {"),
    # the reading of a JSON-lines row for a join (jnjson.cho)
    ("a byte order mark is dropped from every line", "jnjson.cho", "    if number == 1 && len(out) >= 3 && int_of(out[0]) == 239", "    if len(out) >= 3 && int_of(out[0]) == 239"),
    ("a byte order mark is not dropped", "jnjson.cho", "    if number == 1 && len(out) >= 3 && int_of(out[0]) == 239", "    if number == 0 && len(out) >= 3 && int_of(out[0]) == 239"),
    ("a carriage return ends no line", "jnjson.cho", "    if len(out) > 0 && int_of(out[len(out) - 1]) == 13 {", "    if false {"),
    ("a blank line of the build is a record", "jnjson.cho", "                                if jrec.blank(line) {\n                                    // A blank line is not a record.\n                                } else if a[engine.k_records()] >= most {", "                                if false {\n                                    // A blank line is not a record.\n                                } else if a[engine.k_records()] >= most {"),
    ("a blank line of the probe is a record", "jnjson.cho", "                                if jrec.blank(line) {\n                                    // A blank line is not a record.\n                                } else if st[jnread.s_probe()] >= most {", "                                if false {\n                                    // A blank line is not a record.\n                                } else if st[jnread.s_probe()] >= most {"),
    ("the row bound of a jsonl build is one past", "jnjson.cho", "                                } else if a[engine.k_records()] >= most {\n                                    a[engine.k_capped()] = 1;\n                                    going = false;\n                                } else {\n                                    var shape = 0;", "                                } else if a[engine.k_records()] > most {\n                                    a[engine.k_capped()] = 1;\n                                    going = false;\n                                } else {\n                                    var shape = 0;"),
    ("the row bound of a jsonl probe is one past", "jnjson.cho", "                                } else if st[jnread.s_probe()] >= most {", "                                } else if st[jnread.s_probe()] > most {"),
    ("the probe does not count its rows", "jnjson.cho", "                                        st[jnread.s_probe()] = st[jnread.s_probe()] + 1;", "                                        st[jnread.s_probe()] = st[jnread.s_probe()];"),
    ("the facts of a refused line of the build are not kept", "jnjson.cho", "                                        kept = jrec.keep_facts(heap, kept, shape, line, tb, sb, dc.nfields, a);", "                                        kept = kept;"),
    ("the facts of a refused line of the probe are not kept", "jnjson.cho", "                                        kept = jrec.keep_facts(heap, kept, code, line, tb, sb, dc.nfields, a);", "                                        kept = kept;"),
    ("the row of a refusal of the probe is the joined rows'", "jnjson.cho", "                                        a[engine.k_err_row()] = st[jnread.s_probe()] + 1;", "                                        a[engine.k_err_row()] = a[engine.k_records()] + 1;"),
    ("the document hint is told for any line of the probe", "jnjson.cho", "                                        if st[jnread.s_probe()] > 0 && (code == 50 || code == 51) {", "                                        if false {"),
    ("the line of a refusal of the probe is not kept", "jnjson.cho", "                                        a[engine.k_abort_line()] = number;\n                                        a[engine.k_err_line()] = number;\n                                        kept = jrec.keep_facts(heap, kept, code,", "                                        kept = jrec.keep_facts(heap, kept, code,"),
    ("the bytes read of a build row that did not fit are not kept", "jnjson.cho", "                                        if engine.count_row(a, nb, number, 0) {\n                                            borrow crec as &crr in {\n                                                borrow dl as &dlr in {\n                                                    let (t2, k2, b2, e2, p2, code2) = hold3(heap, jt, kscr, kbuf, escr, kept, buffer.bytes(crr), cells, number, a, keys, pairs, simple, keys_only, bfast, bkey, limit, jwt, jwcols, jw_on, tree, cls, bcols, push_b, mask, dlr);\n                                                    jt = t2;\n                                                    kscr = k2;\n                                                    kbuf = b2;\n                                                    escr = e2;\n                                                    kept = p2;\n                                                    if code2 == 1 {\n                                                        a[engine.k_abort()] = 70;\n                                                        a[engine.k_abort_line()] = number;\n                                                        consumed = lines.consumed(rr);", "                                        if engine.count_row(a, nb, number, 0) {\n                                            borrow crec as &crr in {\n                                                borrow dl as &dlr in {\n                                                    let (t2, k2, b2, e2, p2, code2) = hold3(heap, jt, kscr, kbuf, escr, kept, buffer.bytes(crr), cells, number, a, keys, pairs, simple, keys_only, bfast, bkey, limit, jwt, jwcols, jw_on, tree, cls, bcols, push_b, mask, dlr);\n                                                    jt = t2;\n                                                    kscr = k2;\n                                                    kbuf = b2;\n                                                    escr = e2;\n                                                    kept = p2;\n                                                    if code2 == 1 {\n                                                        a[engine.k_abort()] = 70;\n                                                        a[engine.k_abort_line()] = number;\n                                                        consumed = 0;"),
    ("a typed key cell of the build that is not of its type does not stop the read", "jnjson.cho", "        kept = jnread.note_typed(heap, kept, record, cells, keys, answer, 1, row, number, a);\n        return (jt, kscr, kbuf, escr, kept, 2);", "        kept = jnread.note_typed(heap, kept, record, cells, keys, answer, 1, row, number, a);\n        return (jt, kscr, kbuf, escr, kept, 0);"),
    ("a refusal of --join-where does not stop the build", "jnjson.cho", "            a[engine.k_err_fn()] = 0 - 4;\n            return (jt, kscr, kbuf, escr, kept, 2);", "            a[engine.k_err_fn()] = 0 - 4;\n            return (jt, kscr, kbuf, escr, kept, 0);"),
    ("a build row that does not fit is held anyway", "jnjson.cho", "    if answer == 1 {\n        return (jt, kscr, kbuf, escr, kept, 1);", "    if answer == 1 {\n        return (jt, kscr, kbuf, escr, kept, 0);"),
    ("a csv build with its own delimiter is held in that delimiter (one line)", "jnjson.cho", "crec = jrec.csv_record(heap, crec, line, cells, nb, delim);", "crec = jrec.csv_record(heap, crec, line, cells, nb, bdelim);"),
    ("a csv build with its own delimiter is held in that delimiter (a record over lines)", "jnjson.cho", "crec = jrec.csv_record(heap, crec, record, cells, nb, delim);", "crec = jrec.csv_record(heap, crec, record, cells, nb, bdelim);"),
    ("a ragged row of a csv build with its own delimiter is held", "jnjson.cho", "} else if n != nb {", "} else if n < 0 {"),
    ("a ragged record over lines of such a build is held", "jnjson.cho", "} else if m != nb {", "} else if m < 0 {"),
    ("a quoted record of such a build is read a line at a time", "jnjson.cho", "} else if open {\n", "} else if open && false {\n"),
    ("a jsonl probe is joined as an inner join of one text pair whatever the type", "jnjson.cho", "    let fast = jtype == 0 && simple && nk == nb;", "    let fast = simple && nk == nb;"),
    ("a jsonl probe's leading conditions are not applied before the join", "jnjson.cho", "                                            if push_p {", "                                            if false {"),
    ("the groups and the sorted rows of a jsonl probe are not finished", "jnjson.cho", "        if grouping && a[engine.k_abort()] == 0 {", "        if false {"),
    ("the sorted rows of a jsonl probe count no bytes", "jnjson.cho", "                a[engine.k_err_digits()] = jn3pass.sorted_held(gr);", "                a[engine.k_err_digits()] = 0;"),
    ("the groups of a jsonl probe count no bytes", "jnjson.cho", "                a[engine.k_err_digits()] = gr.held;", "                a[engine.k_err_digits()] = 0;"),
    # the run: which loop reads which file, and how a refusal names it (jnrun.cho, finish.cho)
    ("the header of a jsonl build is read as csv", "jnrun.cho", "    if fmt.b_jsonl {\n        // JSON lines have no header", "    if false {\n        // JSON lines have no header"),
    ("the header of a jsonl probe is read as csv", "jnrun.cho", "        if fmt.p_jsonl {\n            let (d2, h2)", "        if false {\n            let (d2, h2)"),
    ("a foreign build is read by the loop of csv", "jnrun.cho", "            } else if foreign {\n                // a JSON-lines build", "            } else if false {\n                // a JSON-lines build"),
    ("a jsonl probe is read by the loop of csv", "jnrun.cho", "                                    if push_p || row_grouping || fmt.p_jsonl {", "                                    if push_p || row_grouping {"),
    ("--absent is ignored by a join", "jnrun.cho", "            absent = 1;", "            absent = 0;"),
    ("--max-json-nodes is ignored by a join", "jnrun.cho", "        nodes = cli.nat(args, parsed, table, \"max-json-nodes\");", "        nodes = 4096;"),
    ("a refusal of a line of the build does not say it is the build", "jnrun.cho", "bkr, bc, shown_b, \"build\");", "bkr, bc, shown_b, \"\");"),
    ("a refusal of a line of the probe does not say it is the probe", "jnrun.cho", "vr, c, shown, \"probe\");", "vr, c, shown, \"\");"),
    ("a line of the build that the reader refuses is refused as a csv row", "jnrun.cho", "        } else if bc.abort >= 50 && bc.abort <= 55 {", "        } else if bc.abort >= 56 && bc.abort <= 55 {"),
    ("a line of the probe that the reader refuses is refused as a csv row", "jnrun.cho", "        } else if c.abort >= 50 && c.abort <= 55 {", "        } else if c.abort >= 56 && c.abort <= 55 {"),
    ("the input of a refused line is not told", "finish.cho", "    if len(input) > 0 {\n        w = fail.detail_str(heap, w, \"input\", input);", "    if len(input) > 99 {\n        w = fail.detail_str(heap, w, \"input\", input);"),
    # --explain (jnexplain.cho)
    ("the header of a jsonl probe on standard input is unknown", "jnexplain.cho", "    let known = !stdin || fmt.p_jsonl;", "    let known = !stdin;"),
    ("the bound of a jsonl build is a csv file's", "jnexplain.cho", "    if bjsonl {\n        most = at_most_j(", "    if false {\n        most = at_most_j("),
    ("a jsonl build has one record in four bytes", "jnexplain.cho", "    let rows = size / 3 + 1;", "    let rows = size / 4 + 1;"),
    ("a jsonl record is its line and no more", "jnexplain.cho", "    let body = size + columns * rows;", "    let body = size;"),
    ("the explain says nothing of the reader of a jsonl probe", "jnexplain.cho", "    if fmt.p_jsonl {\n        borrow hp as &hpr in {", "    if false {\n        borrow hp as &hpr in {"),
    ("the explain says nothing of the delimiter of a build", "jnexplain.cho", "    } else if jnfmt.foreign(fmt, delim) {", "    } else if false {"),
    ("the explain says a tab is a comma", "jnexplain.cho", "        if bdelim == 9 {", "        if bdelim == 8 {"),
    ("the explain says --absent empty is refuse", "jnexplain.cho", "    if absent == 1 {\n        out = buffer.append(heap, out, \"empty\");", "    if absent == 2 {\n        out = buffer.append(heap, out, \"empty\");"),
    ("the explain reads the header of a jsonl build as csv", "jnexplain.cho", "                        if fmt.b_jsonl {\n                            let (bd, bh0)", "                        if false {\n                            let (bd, bh0)"),
    ("the explain reads the header of a jsonl probe as csv", "jnexplain.cho", "    if fmt.p_jsonl {\n        let (pd, ph0)", "    if false {\n        let (pd, ph0)"),
    ("the explain names no format for the probe", "jnexplain.cho", "stdin, fmt.p_jsonl, shown, delim", "stdin, false, shown, delim"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
