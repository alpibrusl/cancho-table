#!/usr/bin/env python3
"""Mutation check of O1 of docs/reader-options.md (issue #109; tests/conformance/test_preamble.py): `--skip-lines` and `--skip-prefix`.

Each mutant is one defect: the count one short or over, the prefix ignored, matching everything or read with its flag (the = spelling), a blank line among the first N not counted, a line of a header that spans lines
cut by the prefix, the count twice or off by one, no count when none was asked and one when it was, the join file's count (missing, swapped, zero hidden), the same options in the header of a join and of `--explain`, and
the refusal for JSON lines. The tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/preamble_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_preamble", "test_rules"]

BRANCH = "                    } else if !header && !quoted && skip_on && preamble.skips(number, line, skip_n, skip_prefix) {"

MUTANTS = [
    ("the value of the = spelling keeps its flag", "preamble.cho", "    return a[14..len(a)];", "    return a;"),
    ("the spellings are exchanged", "preamble.cho", "    if ref % 2 == 1 {", "    if ref % 2 == 0 {"),
    ("the first lines are one short", "preamble.cho", "    if lines > 0 && number < lines {", "    if lines > 0 && number < lines - 1 {"),
    ("the first lines are one over", "preamble.cho", "    if lines > 0 && number < lines {", "    if lines > 0 && number < lines + 1 {"),
    ("the prefix is ignored", "preamble.cho", "    return len(prefix) > 0 && bytes.starts_with(line, prefix);", "    return false;"),
    ("an empty prefix matches every line", "preamble.cho", "    return len(prefix) > 0 && bytes.starts_with(line, prefix);", "    return bytes.starts_with(line, prefix);"),
    ("the option is not read from the arguments", "inputs.cho", "            skip_lines = cli.nat(args, parsed, table, \"skip-lines\") + 1;", "            skip_lines = 0;"),
    ("the lines are one over in the plan", "inputs.cho", "            skip_lines = cli.nat(args, parsed, table, \"skip-lines\") + 1;", "            skip_lines = cli.nat(args, parsed, table, \"skip-lines\") + 2;"),
    ("the prefix of its own argument is read as the = spelling", "inputs.cho", "            if cli.value_is_whole(parsed, table, \"skip-prefix\") {\n                skip_prefix = skip_prefix + 1;", "            if false {\n                skip_prefix = skip_prefix + 1;"),
    ("the options are given to JSON lines", "table.cho", "        if inp.jsonl || inp.has_join && cli.has(parsed, table, \"join-input-format\")", "        if false || inp.has_join && cli.has(parsed, table, \"join-input-format\")"),
    ("the options are given to a JSON-lines join file", "table.cho", "inp.has_join && cli.has(parsed, table, \"join-input-format\") && bytes.equal(cli.text(args, parsed, table, \"join-input-format\"), \"jsonl\") {", "inp.has_join && false {"),
    ("a blank line among the first lines is not counted", "table.cho", "    if !quoted && len(line) == 0 && (header || number >= skip_n) {", "    if !quoted && len(line) == 0 && (header || true) {"),
    ("the lines are never dropped", "table.cho", BRANCH, BRANCH.replace("skip_on &&", "false &&")),
    ("a line of a header that spans lines is cut by the prefix", "table.cho", BRANCH, BRANCH.replace(" && !quoted", "")),
    ("the lines dropped are counted twice", "table.cho", "                        a[engine.k_skipped()] = a[engine.k_skipped()] + 1;", "                        a[engine.k_skipped()] = a[engine.k_skipped()] + 2;"),
    ("the count starts at zero", "table.cho", "        if skip_on {\n            a[engine.k_skipped()] = 1;", "        if skip_on {\n            a[engine.k_skipped()] = 0;"),
    ("the count is one over in the answer", "table.cho", "skipped: a[engine.k_skipped()] - 1, skipped_join: -1 };", "skipped: a[engine.k_skipped()], skipped_join: -1 };"),
    ("a call with no option gets a count", "table.cho", "    if c.skipped < 0 {\n        return o;", "    if c.skipped < -5 {\n        return o;"),
    ("the join file's count is not said", "table.cho", "    if c.skipped_join >= 0 {", "    if c.skipped_join > 99999 {"),
    ("a zero count of the join file is not said", "table.cho", "    if c.skipped_join >= 0 {", "    if c.skipped_join > 0 {"),
    ("the header of a join counts a blank line among the first", "jnhead.cho", "                if !quoted && len(line) == 0 && number >= skip_n {", "                if !quoted && len(line) == 0 && true {"),
    ("the header of a join keeps its preamble", "jnhead.cho", "                } else if !quoted && skip_on && preamble.skips(number, line, skip_n, skip_prefix) {", "                } else if !quoted && skip_on && false {"),
    ("the header of a join counts twice", "jnhead.cho", "                    skipped = skipped + 1;", "                    skipped = skipped + 2;"),
    ("the join file's count is the file's", "jnrun.cho", "        c = finish.with_skipped(c, skipped_p, skipped_b);", "        c = finish.with_skipped(c, skipped_b, skipped_p);"),
    ("the join file's header is read with no options", "jnrun.cho", "jnhead.head_of(heap, rb, bfile, io, false, cap, bdelim, skip_n, skip_prefix);", "jnhead.head_of(heap, rb, bfile, io, false, cap, bdelim, 0, \"\");"),
    ("the file's header is read with no options in a join", "jnrun.cho", "jnhead.head_of(heap, rp, pfile, io, stdin, cap, delim, skip_n, skip_prefix);", "jnhead.head_of(heap, rp, pfile, io, stdin, cap, delim, 0, \"\");"),
    ("the join file's count is lost", "jnrun.cho", "        skipped_b = sb1;", "        skipped_b = -1;"),
    ("the file's count is lost", "jnrun.cho", "            skipped_p = sp1;", "            skipped_p = -1;"),
    ("explain reads the header with the preamble", "explain.cho", "                } else if !quoted && (skip_n > 0 || len(skip_prefix) > 0) && preamble.skips(number, line, skip_n, skip_prefix) {", "                } else if !quoted && false {"),
    ("explain does not say the lines", "explain.cho", "        if query.skip_of(tree) > 0 {\n            o = buffer.append(heap, o, \",\\\"skip_lines\\\":\");", "        if query.skip_of(tree) > 99999999 {\n            o = buffer.append(heap, o, \",\\\"skip_lines\\\":\");"),
    ("explain does not say the prefix", "explain.cho", "        if query.skipp_of(tree) >= 0 {\n            o = buffer.append(heap, o, \",\\\"skip_prefix\\\":\");", "        if query.skipp_of(tree) > 99999999 {\n            o = buffer.append(heap, o, \",\\\"skip_prefix\\\":\");"),
    ("the count of the file is lost in a join answer", "finish.cho", "skipped: own, skipped_join: joined }", "skipped: joined, skipped_join: own }"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
