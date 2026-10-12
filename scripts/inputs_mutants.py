#!/usr/bin/env python3
"""Mutation check of the Q0 gate (tests/conformance/test_inputs.py): `body` reading the plan through `Inputs`.

Each mutant is a source with the structure undone, a flag read again in `body`, or a field of `Inputs` filled wrongly (which the behaviour gates catch): rebuilt and run against the tests. A mutant is killed when a test fails or the build refuses it.

    python3 scripts/inputs_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_inputs", "test_select", "test_filter", "test_limits", "test_report"]

MUTANTS = [
    ("body reads --select again", "table.cho", "let has_select = inp.has_select;", 'let has_select = cli.has(parsed, table, "select");'),
    ("body reads --where again", "table.cho", "let has_where = inp.has_where;", 'let has_where = cli.has(parsed, table, "where");'),
    ("body reads --group again", "table.cho", "let has_group = inp.has_group;", 'let has_group = cli.has(parsed, table, "group");'),
    ("body reads --agg again", "table.cho", "let has_agg = inp.has_agg;", 'let has_agg = cli.has(parsed, table, "agg");'),
    ("body reads --order-by again", "table.cho", "let has_order = inp.has_order;", 'let has_order = cli.has(parsed, table, "order-by");'),
    ("body reads the operands again", "table.cho", "if inp.operands > 1 {", "if cli.operand_count(parsed) > 1 {"),
    ("body reads --limit again", "table.cho", "limit = inp.limit_n;", 'limit = cli.nat(args, parsed, table, "limit");'),
    ("body reads the root again", "table.cho", "path.root(heap, args, inp.root_text, inp.root_at, e)", 'path.root(heap, args, cli.text(args, parsed, table, "root"), inp.root_at, e)'),
    ("the inputs do not read --top", "inputs.cho", 'top_n: cli.nat(args, parsed, table, "top")', "top_n: 0"),
    ("the inputs read --from as --limit", "inputs.cho", 'from_n: cli.nat(args, parsed, table, "from")', 'from_n: cli.nat(args, parsed, table, "limit")'),
    ("the inputs read the source's index as zero", "inputs.cho", "source_at = cli.operand_index(parsed, 0);", "source_at = 0;"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
