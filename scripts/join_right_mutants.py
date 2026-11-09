#!/usr/bin/env python3
"""Mutation check of slice F0b (docs/join-redesign.md section 7; tests/conformance/test_join_right.py): the repair of `--join-type right`, the exchanged call.

Each mutant is one defect in the exchange (a place of the call not changed: the operand, the `--join` file, the pairs of `--on`, the join type; the `=` spelling of a flag), in the cases where the
exchange is not the same question and nothing is offered (standard input, a flag-like operand, a missing flag, a prefix, a condition on the --join file, `--join-sorted`, the reader flags, a position, a typed
shared-name key), and in the wiring of the refusal; the tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/join_right_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_join_right", "test_rules"]


MUTANTS = [
    ("the operand is not given to --join's place", "jnright.cho", "        if i == oi {", "        if i == oi + 1000 {"),
    ("the --join file is not given to the operand's place", "jnright.cho", "        } else if i == ji {", "        } else if i == ji + 1000 {"),
    ("the pairs of --on are not exchanged", "jnright.cho", "        } else if i == oni {", "        } else if i == oni + 1000 {"),
    ("the join type stays right", "jnright.cho", "        } else if i == ti {", "        } else if i == ti + 1000 {"),
    ("the join type becomes inner", "jnright.cho", "                o = json.put_string(heap, o, \"left\");", "                o = json.put_string(heap, o, \"inner\");"),
    ("the join type of the = spelling becomes inner", "jnright.cho", "o = json.put_string(heap, o, \"--join-type=left\");", "o = json.put_string(heap, o, \"--join-type=inner\");"),
    ("the = spelling of the join type is not looked for", "jnright.cho", "        if bytes.equal(a, \"--join-type=right\") {", "        if bytes.equal(a, \"--join-type=rightx\") {"),
    ("the separate spelling of the join type is not looked for", "jnright.cho", "        if bytes.equal(a, \"--join-type\") && i + 1 < arg_count(args) && bytes.equal(arg(args, i + 1), \"right\") {", "        if bytes.equal(a, \"--join-typex\") && i + 1 < arg_count(args) && bytes.equal(arg(args, i + 1), \"right\") {"),
    ("the = spelling of --join drops its flag", "jnright.cho", "                var t = buffer.append(heap, buffer.empty(heap, 32), \"--join=\");", "                var t = buffer.append(heap, buffer.empty(heap, 32), \"\");"),
    ("the = spelling of --on drops its flag", "jnright.cho", "                    var t2 = buffer.append(heap, buffer.empty(heap, 32), \"--on=\");", "                    var t2 = buffer.append(heap, buffer.empty(heap, 32), \"\");"),
    ("a call with another refused flag is offered the repair", "jnright.cho", "    if others {\n        return 10;", "    if others && false {\n        return 10;"),
    ("the repair is offered whatever the case", "jnfail.cho", "    if code == 0 {\n        let (o2, wrote) = jnright.retry(heap, o, args, parsed);", "    if true {\n        let (o2, wrote) = jnright.retry(heap, o, args, parsed);"),
    ("no case is offered", "jnfail.cho", "    if code == 0 {\n        let (o2, wrote) = jnright.retry(heap, o, args, parsed);", "    if code == 99 {\n        let (o2, wrote) = jnright.retry(heap, o, args, parsed);"),
    ("the reason of a case is the one of the next", "jnright.cho", "        return \"--join-prefix names the --join file's columns, and the exchange would give the prefix to the other file's\";", "        return \"--join-where filters the --join file before it is held, and in the exchanged call that file is the probe\";"),
    ("standard input is offered the repair", "jnright.cho", "    if cli.operand_count(parsed) != 1 {\n        return 1;", "    if cli.operand_count(parsed) > 1 {\n        return 1;"),
    ("a flag-like operand is offered the repair", "jnright.cho", "int_of(arg(args, oi)[0]) == '-' {\n        return 2;", "int_of(arg(args, oi)[0]) == 'x' {\n        return 2;"),
    ("a call without --on is offered the repair", "jnright.cho", "    if !cli.has(parsed, table, \"join\") || !cli.has(parsed, table, \"on\") {\n        return 3;", "    if !cli.has(parsed, table, \"join\") && !cli.has(parsed, table, \"on\") {\n        return 3;"),
    ("--join-prefix is offered the repair", "jnright.cho", "    if cli.has(parsed, table, \"join-prefix\") {\n        return 4;", "    if cli.has(parsed, table, \"join-prefixx\") {\n        return 4;"),
    ("--join-where is offered the repair", "jnright.cho", "    if cli.has(parsed, table, \"join-where\") {\n        return 5;", "    if cli.has(parsed, table, \"join-wherex\") {\n        return 5;"),
    ("--join-sorted is offered the repair", "jnright.cho", "    if cli.has(parsed, table, \"join-sorted\") {\n        return 6;", "    if cli.has(parsed, table, \"join-sortedx\") {\n        return 6;"),
    ("--input-format is offered the repair", "jnright.cho", "    if cli.has(parsed, table, \"input-format\") ||", "    if cli.has(parsed, table, \"input-formatx\") ||"),
    ("--join-delimiter is offered the repair", "jnright.cho", "|| cli.has(parsed, table, \"join-delimiter\") {\n        return 7;", "|| cli.has(parsed, table, \"join-delimiterx\") {\n        return 7;"),
    ("a position is offered the repair", "jnright.cho", "        if i != oi && has_hash(arg(args, i)) {", "        if i != oi && has_hash(arg(args, i)) && false {"),
    ("a typed shared-name key is offered the repair", "jnright.cho", "    if typed {\n        return 9;", "    if typed && false {\n        return 9;"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
