#!/usr/bin/env python3
"""Mutation check of the statement `--explain` shows (docs/query.md section 16; tests/conformance/test_sql_explain.py): `data.explain.sql` and `sql_flags`.

Each mutant is one defect in the writing of the statement: the quoting of a name and of a value, the type casts, the operators, the negations, the parentheses, the aggregates, the order, the page and `--top`,
the source, and the reasons for no statement; the tool is rebuilt from it and run against the round trip and the pinned statements. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/sql_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_sql_explain", "test_explain.Spelling"]

MUTANTS = [
    # names
    ("a keyword is written bare", "qsql.cho", "    return !word_in(qparse.reserved(), name) &&", "    return true || !word_in(qparse.reserved(), name) &&"),
    ("a function name is written bare", "qsql.cho", "&& !word_in(qparse.functions(), name) &&", "&& !word_in(qparse.functions(), name) || true &&"),
    ("a quote in a name is not doubled", "qsql.cho", "        if int_of(name[k]) == 34 {", "        if int_of(name[k]) == 35 {"),
    ("a position is written as a name", "qsql.cho", "    if position >= 0 {\n        b = buffer.push(heap, b, byte_of('#'));", "    if position >= 100000 {\n        b = buffer.push(heap, b, byte_of('#'));"),
    # types and values
    ("an integer is cast as a float", "qsql.cho", "    if t == 1 {\n        return buffer.append(heap, b, \"int\");", "    if t == 1 {\n        return buffer.append(heap, b, \"float\");"),
    ("a date is written as a number", "qsql.cho", "let numeric = t == 1 || t >= 2 && t <= 20 || t == 21;", "let numeric = t == 1 || t >= 2 && t <= 20 || t >= 21;"),
    ("a decimal is written without its scale", "qsql.cho", "    b = buffer.append(heap, b, \"dec(\");\n    b = buffer.push_nat(heap, b, t - 2);", "    b = buffer.append(heap, b, \"dec(\");\n    b = buffer.push_nat(heap, b, t - 1);"),
    ("a quote in a value is not doubled", "qsql.cho", "            if int_of(lit[k]) == 39 {\n                b = buffer.push(heap, b, byte_of(39));", "            if int_of(lit[k]) == 38 {\n                b = buffer.push(heap, b, byte_of(39));"),
    ("a leading plus stays on a number", "qsql.cho", "    if len(lit) > 0 && int_of(lit[0]) == '+' {\n        start = 1;", "    if len(lit) > 0 && int_of(lit[0]) == '*' {\n        start = 1;"),
    # conditions
    ("an operator is the one below it", "qsql.cho", "    if op == 3 {\n        return \"<=\";", "    if op == 3 {\n        return \"<\";"),
    ("a negated contains loses its not", "qsql.cho", "    if kind == 1 {\n        if negated {\n            b = buffer.append(heap, b, \" not\");", "    if kind == 1 {\n        if false {\n            b = buffer.append(heap, b, \" not\");"),
    ("a negated in loses its not", "qsql.cho", "    if kind == 2 {\n        if negated {\n            b = buffer.append(heap, b, \" not\");", "    if kind == 2 {\n        if false {\n            b = buffer.append(heap, b, \" not\");"),
    ("the values of an in are joined without a space", "qsql.cho", "            if v > 0 {\n                b = buffer.append(heap, b, \", \");", "            if v > 0 {\n                b = buffer.append(heap, b, \",\");"),
    ("a not of a group loses its parenthesis", "qsql.cho", "        b = buffer.append(heap, b, \"not (\");", "        b = buffer.append(heap, b, \"not  \");"),
    ("a group of another connective loses its parentheses", "qsql.cho", "    if conn == 0 || conn == kind {\n        return put_level(heap, b, plan, tr, node);", "    if true {\n        return put_level(heap, b, plan, tr, node);"),
    ("a group of the same connective keeps its parentheses", "qsql.cho", "    if conn == 0 || conn == kind {", "    if conn == 0 {"),
    # aggregates
    ("a typed aggregate loses its type", "qsql.cho", "    } else if t != 1 {\n        b = put_type(heap, b, t);", "    } else if false {\n        b = put_type(heap, b, t);"),
    ("a distinct count loses its type", "qsql.cho", "    if f == 4 {\n        b = put_type(heap, b, t);", "    if f == 4 {\n        b = b;"),
    ("a mean has the scale of the column as its own", "qsql.cho", "    if mean != 0 && mean != 100 {", "    if mean != 0 {"),
    ("a mean is one digit too many", "qsql.cho", "        b = buffer.push_nat(heap, b, mean - 1);", "        b = buffer.push_nat(heap, b, mean);"),
    ("a min is written as a max", "qsql.cho", "    if f == 2 {\n        return \"min\";", "    if f == 2 {\n        return \"max\";"),
    # clauses
    ("a grouping has no group by", "qsql.cho", "    if mode == 2 && ng > 0 {\n        out = buffer.append(heap, out, \" group by \");", "    if mode == 2 && ng > 100 {\n        out = buffer.append(heap, out, \" group by \");"),
    ("a descending order is ascending", "qsql.cho", "            if query.order_flags(tree, j) % 2 == 1 {", "            if query.order_flags(tree, j) % 2 == 0 {"),
    ("a descending sort is ascending", "qsql.cho", "        if desc {\n            out = buffer.append(heap, out, \" desc\");", "        if false {\n            out = buffer.append(heap, out, \" desc\");"),
    ("an order by of rows is not written", "qsql.cho", "    if mode == 1 && query.count_of(tree, 4) > 0 {", "    if mode == 1 && query.count_of(tree, 4) > 100 {"),
    ("the select list of rows is a star", "qsql.cho", "        if ns == 0 {\n            out = buffer.push(heap, out, byte_of('*'));", "        if ns >= 0 {\n            out = buffer.push(heap, out, byte_of('*'));"),
    # source and page
    ("a quote in a path is not doubled", "qsql.cho", "            if int_of(shown[k]) == 39 {", "            if int_of(shown[k]) == 38 {"),
    ("standard input is named as a file", "qsql.cho", "    if stdin {\n        out = buffer.append(heap, out, \" from stdin\");", "    if false {\n        out = buffer.append(heap, out, \" from stdin\");"),
    ("a limit that is not the default is not written", "qsql.cho", "    if asked && !unbounded {", "    if false && !unbounded {"),
    ("an offset is not written", "qsql.cho", "    if from > 0 {", "    if from > 1000000000 {"),
    ("--top is not left to the call", "qsql.cho", "    if topped {\n        extra = buffer.append(heap, extra, \"\\\"--top\\\",\\\"\");", "    if false {\n        extra = buffer.append(heap, extra, \"\\\"--top\\\",\\\"\");"),
    ("with --top the limit stays in the statement", "qsql.cho", "        if topped {\n            extra = buffer.append(heap, extra, \",\\\"--limit\\\",\\\"\");", "        if false {\n            extra = buffer.append(heap, extra, \",\\\"--limit\\\",\\\"\");"),
    # the reasons
    ("a rename has a statement", "qsql.cho", "    if len(query.rename_text(tree)) > 0 || cli.has(parsed, table, \"rename\") {", "    if false {"),
    ("a typed group has a statement", "qsql.cho", "        if query.gkind_raw_at(tree, j) != 0 {", "        if query.gkind_raw_at(tree, j) < 0 {"),
    ("a join has a statement", "qsql.cho", "    if joining {\n        return (out, extra, 3);", "    if false {\n        return (out, extra, 3);"),
    ("the shape has a statement", "qsql.cho", "    if mode == 0 {\n        return (out, extra, 1);", "    if false {\n        return (out, extra, 1);"),
    ("a report has a statement", "qsql.cho", "    if query.reporting(tree) {\n        return (out, extra, 2);", "    if false {\n        return (out, extra, 2);"),
    ("JSON lines have a statement", "qsql.cho", "    if bytes.equal(input, \"jsonl\") || bytes.equal(input, \"ndjson\") {", "    if false {"),
    ("a plan with no statement says so with a statement", "explain.cho", "    if why != 0 {\n        b = buffer.append(heap, b, \"null\");", "    if why > 100 {\n        b = buffer.append(heap, b, \"null\");"),
    ("a plan with no statement has no note", "explain.cho", "    if sql_why != 0 {\n        var sentence = buffer.append(heap, buffer.empty(heap, 128), \"sql.\");", "    if sql_why > 100 {\n        var sentence = buffer.append(heap, buffer.empty(heap, 128), \"sql.\");"),
    ("the where text of a plan is not kept", "orparse.cho", "    let q = query.with_where_text(heap, q0, src);", "    let q = q0;"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
