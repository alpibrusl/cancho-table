#!/usr/bin/env python3
"""Mutation check of the surface of the date types (docs/datetime.md, slice T3; gate G-MUT): `--query` with date, datetime and timestamp, the types as join keys, `--report dates`. Each mutant is one
source file of tools/table with one deliberate defect, rebuilt, and run against tests/conformance/test_datetime_t3.py (with the references `join_rows` and `report_row` of datetime_ref.py) and the
date tests of T1. A mutant is killed when a test fails or the build refuses it. The files are restored after every mutant.

    python3 scripts/datetime_surface_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no build; CI runs it). MUT_SHARD=I/N runs the I-th of N parts (each in a checkout of its own: a run rebuilds tools/table in place).

Grouped: the type words of the query and what the translator writes for them, the type words of a join pair and the key of a typed pair, the refusal of a key cell (its context, its input, its pair, its
reason, the re-reading of the cell that the 4-bit answer of a read asks for), the explain, the classes of a cell of the report, its counters, its suggestion, its merge across threads, its header, its
dispatch, its flags and its bounds. No mutant is equivalent unless a comment says so.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_datetime_t3.QuerySurface", "test_datetime_t3.Joins", "test_datetime_t3.Report", "test_datetime_t3.JsonlOut", "test_datetime_t3.Surface", "test_datetime_t3.QueryEquivalence",
         "test_datetime_t3.Fuzz", "test_datetime.Surface", "test_rules"]

MUTANTS = [
    # ---- the query: the type words and the suffix the translator writes
    ("the word date is a timestamp", "qparse.cho", "    if is_word(q, m, 0, \"date\") {\n        step(m);\n        return 22;", "    if is_word(q, m, 0, \"date\") {\n        step(m);\n        return 23;"),
    ("the word timestamp is a datetime", "qparse.cho", "    if is_word(q, m, 0, \"timestamp\") {\n        step(m);\n        return 23;", "    if is_word(q, m, 0, \"timestamp\") {\n        step(m);\n        return 24;"),
    ("the word datetime is a date", "qparse.cho", "    if is_word(q, m, 0, \"datetime\") {\n        step(m);\n        return 24;", "    if is_word(q, m, 0, \"datetime\") {\n        step(m);\n        return 22;"),
    ("the expected text of a type does not name the new ones", "qtext.cho", "\"a type: int, dec(N), float, date, datetime or timestamp\"", "\"a type: int, dec(N) or float\""),
    ("the type of a refused literal is written text", "qtext.cho", "    if t == 22 {\n        return \"date\";\n    }\n    if t == 23 {\n        return \"timestamp\";", "    if t == 22 {\n        return \"text\";\n    }\n    if t == 23 {\n        return \"timestamp\";"),
    ("the translator writes :datetime for a timestamp", "qtrans.cho", "        o = buffer.append(heap, o, \":timestamp\");", "        o = buffer.append(heap, o, \":datetime\");"),
    ("the translator writes :datetime for a date", "qtrans.cho", "        o = buffer.append(heap, o, \":date\");", "        o = buffer.append(heap, o, \":datetime\");"),
    ("the translator writes :date for a datetime", "qtrans.cho", "        o = buffer.append(heap, o, \":datetime\");\n    } else if t >= 2 {", "        o = buffer.append(heap, o, \":date\");\n    } else if t >= 2 {"),
    # ---- the join: the type of a pair and its key
    ("the word date of a pair is a datetime", "jnflags.cho", "    if bytes.equal(word, \"date\") {\n        return 22;", "    if bytes.equal(word, \"date\") {\n        return 24;"),
    ("the word timestamp of a pair is a datetime", "jnflags.cho", "    if bytes.equal(word, \"timestamp\") {\n        return 23;", "    if bytes.equal(word, \"timestamp\") {\n        return 24;"),
    ("a key of a date type is read as a number", "jnkey.cho", "    if kind >= 22 {\n        return dtparse.parse(data, kind);", "    if kind >= 99 {\n        return dtparse.parse(data, kind);"),
    ("the status of a refused date key is not folded into the answer", "jnkey.cho", "                if bad >= 14 {\n                    status = 15;", "                if bad >= 99 {\n                    status = 15;"),
    ("a refused date key is not read again", "jnread.cho", "    if status == 15 {\n        status = dtjoin.status_of(", "    if status == 99 {\n        status = dtjoin.status_of("),
    ("a date key is refused with the rules of the numbers", "jnfail.cho", "    if status >= 14 {\n        // a :date, :timestamp or :datetime key cell", "    if status >= 99 {\n        // a :date, :timestamp or :datetime key cell"),
    ("the refusal of a date key has the context where", "dtjoin.cho", "    w = fail.detail_str(heap, w, \"context\", \"join\");", "    w = fail.detail_str(heap, w, \"context\", \"where\");"),
    ("the refusal of a date key says the probe whichever file", "dtjoin.cho", "    w = fail.detail_str(heap, w, \"input\", input);", "    w = fail.detail_str(heap, w, \"input\", \"probe\");"),
    ("the refusal of a date key counts the pair from 0", "dtjoin.cho", "    w = fail.detail_int(heap, w, \"pair\", pair);", "    w = fail.detail_int(heap, w, \"pair\", pair - 1);"),
    ("the refusal of a date key names no reason", "dtjoin.cho", "    if len(dtparse.reason_text(reason)) > 0 {\n        w = fail.detail_str(heap, w, \"reason\"", "    if len(dtparse.reason_text(reason)) > 99 {\n        w = fail.detail_str(heap, w, \"reason\""),
    ("the refusal of a date key names no offset", "dtjoin.cho", "    if reason <= 16 {\n        w = fail.detail_int(heap, w, \"offset\", extra);", "    if reason <= 0 {\n        w = fail.detail_int(heap, w, \"offset\", extra);"),
    ("a quoted key cell is read again with its quotes", "dtjoin.cho", "    if quoted == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {", "    if quoted == 99 && index_of_byte(record[first..last], byte_of(34)) >= 0 {"),
    ("the explain names every date type date", "jnexplain.cho", "        d = buffer.append(heap, d, dtparse.type_text(t));", "        d = buffer.append(heap, d, dtparse.type_text(22));"),
    # ---- --report dates: the classes of a cell
    ("a date of ten bytes is not tried", "reportdt.cho", "    if len(data) == 10 {\n        let (v, bad) = dtparse.parse(data, 22);", "    if len(data) == 11 {\n        let (v, bad) = dtparse.parse(data, 22);"),
    ("a datetime of nineteen bytes is not tried", "reportdt.cho", "    if len(data) >= 19 {", "    if len(data) >= 20 {"),
    ("a timestamp is counted a datetime", "reportdt.cho", "        if bad == 0 {\n            return 2;\n        }", "        if bad == 0 {\n            return 3;\n        }"),
    ("the widest cell is not kept", "reportdt.cho", "    if len(data) > get(g, base + 6) {", "    if len(data) > 99999 {"),
    ("the first empty cell is placed by its line and its row", "reportdt.cho", "        first(g, base + 7, row, line);\n        return 0;", "        first(g, base + 7, line, row);\n        return 0;"),
    ("a datetime is not a cell that :timestamp refuses", "reportdt.cho", "    if class != 2 {\n        first(g, base + 11, row, line);", "    if class != 2 && class != 3 {\n        first(g, base + 11, row, line);"),
    ("the first other cell is kept for any non-date", "reportdt.cho", "    if class == 4 && get(g, base + 15) == 0 {", "    if class >= 1 && get(g, base + 15) == 0 {"),
    # ---- the suggestion
    ("a column with any timestamp is suggested :timestamp", "reportdt.cho", "        if get(g, base + 3) == nonempty {", "        if get(g, base + 3) > 0 {"),
    ("the empty cells count against the suggestion", "reportdt.cho", "    let nonempty = get(g, base) - get(g, base + 1);", "    let nonempty = get(g, base);"),
    # ---- the merge across threads
    ("the threads' widest is not combined", "reportdt.cho", "            if widest > get(gw, base + 6) {", "            if widest > 99999 {"),
    ("the threads' first row is not offset by the rows before", "reportdt.cho", "                    put(gw, base + f, their_row + rows_before);", "                    put(gw, base + f, their_row);"),
    ("the threads' first line is not offset by the lines before", "reportdt.cho", "agg.get_i64(blob, from + 8 * (f + 1)) + lines_before);", "agg.get_i64(blob, from + 8 * (f + 1)));"),
    ("the threads' counts of the last class are not added", "reportdt.cho", "            while c < 6 {\n                put(gw, base + c, get(gw, base + c) + agg.get_i64(blob, from + 8 * c));", "            while c < 5 {\n                put(gw, base + c, get(gw, base + c) + agg.get_i64(blob, from + 8 * c));"),
    # ---- the dispatch, the header, the flags
    ("a dates report reads its rows as types", "engine.cho", "        if query.report_kind(tree) == 2 {\n            let (g2, s2, st) = reportdt.row(", "        if query.report_kind(tree) == 3 {\n            let (g2, s2, st) = reportdt.row("),
    ("the counters of a dates report have the width of the types", "report.cho", "    if kind == 2 {\n        return reportdt.width();", "    if kind == 3 {\n        return reportdt.width();"),
    ("a dates report has the header of the types", "report.cho", "    if kind == 2 {\n        return reportdt.head_json(heap);", "    if kind == 3 {\n        return reportdt.head_json(heap);"),
    ("a dates report is merged as the types", "report.cho", "    if kind == 2 {\n        return reportdt.merge(", "    if kind == 3 {\n        return reportdt.merge("),
    ("a dates report is serialised as the types", "report.cho", "    if kind == 2 {\n        return reportdt.serialize(g, out);", "    if kind == 3 {\n        return reportdt.serialize(g, out);"),
    ("the plan keeps the types report whichever was asked", "query.cho", "        vec.set(gw, 1, kind);", "        vec.set(gw, 1, 1);"),
    ("the word dates names the types", "reportflags.cho", "    if bytes.equal(word, \"dates\") {\n        return 2;", "    if bytes.equal(word, \"dates\") {\n        return 1;"),
    ("any word is a report", "reportflags.cho", "    if kind == 0 {\n        e = flags.problem(heap, e, \"args.bad-value\"", "    if kind == 99 {\n        e = flags.problem(heap, e, \"args.bad-value\""),
    ("the refusal of jsonl for dates says types", "reportflags.cho", "    if jsonl_out {\n        if kind == 2 {", "    if jsonl_out {\n        if kind == 99 {"),
    ("a dates report is not told by its explain", "explain.cho", "        o = buffer.append(heap, o, \"date report\");", "        o = buffer.append(heap, o, \"type report\");"),
    ("the bound of a dates report is the types'", "engine.cho", "                a[k_abort()] = 44;", "                a[k_abort()] = 43;"),
]

if __name__ == "__main__":
    shard = os.environ.get("MUT_SHARD")
    chosen = MUTANTS
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        chosen = [m for k, m in enumerate(MUTANTS) if k % n == i]
    sys.exit(mutlib.main(chosen, TESTS))
