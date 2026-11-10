#!/usr/bin/env python3
"""Mutation check of F2 (docs/join-redesign.md section 4, issue #115; tests/conformance/test_join_report.py): the repair of `--report` over a join, the first of two calls.

Each mutant is one defect in the arguments of the first call (the report kept, its value kept, the file's name, `--format` or `--out` not added or added after a `--`), in the cases where nothing is offered (no `--root`, `--out` or
`--format` given, a flag of rows, groups or a page, another flag refused too), in the hint that names the second call, and in the wiring of the refusal; the tool is rebuilt from it and run against the tests.

    python3 scripts/join_report_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_join_report", "test_rules"]

MUTANTS = [
    ("the report is kept", "jnreport.cho", "        var skipped = i == vi;", "        var skipped = false;"),
    ("the value of a split report is kept", "jnreport.cho", "        if two && i + 1 == vi {\n            skipped = true;", "        if false && i + 1 == vi {\n            skipped = true;"),
    ("the value of the report is dropped with the next argument", "jnreport.cho", "        var skipped = i == vi;", "        var skipped = i == vi || i == vi + 1;"),
    ("the format is not added", "jnreport.cho", "            o = json.put_string(heap, o, \"--format=csv\");\n            o = json.put_string(heap, o, \"--out=joined.csv\");\n        }\n        var skipped", "            o = json.put_string(heap, o, \"--out=joined.csv\");\n        }\n        var skipped"),
    ("the file is not named", "jnreport.cho", "            o = json.put_string(heap, o, \"--format=csv\");\n            o = json.put_string(heap, o, \"--out=joined.csv\");\n        }\n        var skipped", "            o = json.put_string(heap, o, \"--format=csv\");\n        }\n        var skipped"),
    ("the flags are added after the terminator", "jnreport.cho", "    var stop = arg_count(args);", "    var stop = arg_count(args) + 5;"),
    ("the file has another name in the argv", "jnreport.cho", "            o = json.put_string(heap, o, \"--out=joined.csv\");\n        }\n        var skipped", "            o = json.put_string(heap, o, \"--out=joined.tsv\");\n        }\n        var skipped"),
    ("no root is offered the repair", "jnreport.cho", "    if !cli.has(parsed, table, \"root\") {\n        return 1;", "    if !cli.has(parsed, table, \"rootx\") && false {\n        return 1;"),
    ("an --out is offered the repair", "jnreport.cho", "    if cli.has(parsed, table, \"out\") || cli.has(parsed, table, \"format\") {", "    if cli.has(parsed, table, \"format\") {"),
    ("a --format is offered the repair", "jnreport.cho", "    if cli.has(parsed, table, \"out\") || cli.has(parsed, table, \"format\") {", "    if cli.has(parsed, table, \"out\") {"),
    ("--select is offered the repair", "jnreport.cho", "    if cli.has(parsed, table, \"select\") ||", "    if cli.has(parsed, table, \"selectx\") ||"),
    ("--where is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"where\") ||", "cli.has(parsed, table, \"wherex\") ||"),
    ("--limit is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"limit\") ||", "cli.has(parsed, table, \"limitx\") ||"),
    ("--from is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"from\") ||", "cli.has(parsed, table, \"fromx\") ||"),
    ("--max-bytes is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"max-bytes\") {", "cli.has(parsed, table, \"max-bytesx\") {"),
    ("--group is offered the repair", "jnreport.cho", "    if cli.has(parsed, table, \"group\") ||", "    if cli.has(parsed, table, \"groupx\") ||"),
    ("--agg is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"agg\") ||", "cli.has(parsed, table, \"aggx\") ||"),
    ("--order-by is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"order-by\") ||", "cli.has(parsed, table, \"order-byx\") ||"),
    ("--top is offered the repair", "jnreport.cho", "cli.has(parsed, table, \"top\") {", "cli.has(parsed, table, \"topx\") {"),
    ("--rename is offered the repair", "jnreport.cho", "    if cli.has(parsed, table, \"rename\") ||", "    if cli.has(parsed, table, \"renamex\") ||"),
    ("another refusal is offered the repair", "jnreport.cho", "    if others {\n        return 4;", "    if others && false {\n        return 4;"),
    ("the hint does not name the file", "jnreport.cho", "        hint = buffer.push(heap, hint, byte_of(' '));\n        hint = buffer.append(heap, hint, file_name());", "        hint = buffer.push(heap, hint, byte_of(' '));"),
    ("the hint forgets the delimiter", "jnreport.cho", "        if cli.has(parsed, table, \"delimiter\") {\n            hint = buffer.append(heap, hint, \" --delimiter \");", "        if cli.has(parsed, table, \"delimiterx\") {\n            hint = buffer.append(heap, hint, \" --delimiter \");"),
    ("the hint names another report", "jnreport.cho", "        hint = buffer.append(heap, hint, \" --report \");\n        hint = buffer.append(heap, hint, kind);", "        hint = buffer.append(heap, hint, \" --report \");\n        hint = buffer.append(heap, hint, \"types\");"),
    ("the message names another report", "jnreport.cho", "    message = buffer.append(heap, message, kind);", "    message = buffer.append(heap, message, \"types\");"),
    ("the refusal is not made", "jnflags.cho", "    if inp.has_report {\n        var others = false;", "    if inp.has_report && false {\n        var others = false;"),
    ("the earlier refusals are not counted", "jnflags.cho", "            others = fail.count(er) > 0;\n        }\n        e = jnreport.refusal(", "            others = false;\n        }\n        e = jnreport.refusal("),
    ("the detail names no flags", "jnreport.cho", "    o = fail.detail_str(heap, o, \"flags\", \"--join --report\");", "    o = fail.detail_str(heap, o, \"flags\", \"--join\");"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
