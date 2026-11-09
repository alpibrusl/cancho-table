#!/usr/bin/env python3
"""Mutation check of `--default-limit` (docs/mcp.md section 13, issue #130; tests/conformance/test_default_limit.py): the page a json answer of rows or groups gets when the call gives none.

Each mutant is one defect: the flag not looked up, not applied, applied to a call that gives a page of its own (`--limit`, `--top`), to the shape, to the report, to csv, the value off by one, the range of a bad value
moved. The tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails. The server's half (`--page`, the argument it gives) is in scripts/mcp_mutants.py.

    python3 scripts/default_limit_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_default_limit", "test_rules"]

APPLY = "        if inp.default_limit > 0 && inp.default_limit <= 1000000 && !inp.has_limit && !inp.has_top && mode > 0 && !has_report {\n            limit = inp.default_limit;"

MUTANTS = [
    ("the flag is not looked up", "inputs.cho", "bytes.starts_with(a, \"--overwrite\") || bytes.starts_with(a, \"--default-limit\")", "bytes.starts_with(a, \"--overwrite\") || bytes.starts_with(a, \"--default-limitx\")"),
    ("the flag is looked up and not read", "inputs.cho", "        if cli.has(parsed, table, \"default-limit\") {", "        if cli.has(parsed, table, \"overwrite\") && false {"),
    ("the page is never applied", "table.cho", APPLY, APPLY.replace("inp.default_limit > 0 &&", "inp.default_limit > 99999999 &&")),
    ("the page is applied over --limit", "table.cho", APPLY, APPLY.replace(" && !inp.has_limit", "")),
    ("the page is applied over --top", "table.cho", APPLY, APPLY.replace(" && !inp.has_top", "")),
    ("the page is applied to the shape", "table.cho", APPLY, APPLY.replace("mode > 0", "mode > -1")),
    ("the page is applied to the report", "table.cho", APPLY, APPLY.replace(" && !has_report", "")),
    ("the page is one over", "table.cho", APPLY, APPLY.replace("limit = inp.default_limit;", "limit = inp.default_limit + 1;")),
    ("the page is applied to csv and JSON lines", "table.cho", "    if !as_csv {\n        limit = 1000;\n        // `--default-limit`", "    if true {\n        limit = 1000;\n        // `--default-limit`"),
    ("a bad value is one over", "table.cho", "    if inp.default_limit > 1000000 {\n        e = flags.problem(", "    if inp.default_limit > 1000001 {\n        e = flags.problem("),
    ("a bad value is not refused", "table.cho", "    if inp.default_limit > 1000000 {\n        e = flags.problem(", "    if inp.default_limit > 99999999 {\n        e = flags.problem("),
    ("a bad value names another flag", "table.cho", "\"--default-limit 50\", \"--default-limit\");", "\"--default-limit 50\", \"--limit\");"),
    ("the largest page is refused", "table.cho", "inp.default_limit <= 1000000 && !inp.has_limit", "inp.default_limit < 1000000 && !inp.has_limit"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
