#!/usr/bin/env python3
"""Mutation check of #107 (tests/conformance/test_empty_guard.py): the repair of an empty typed cell, the call with the guard `COLUMN != ''` in front of `--where`.

Each mutant is one defect in where the repair is offered (a cell that is not empty, a `--query`, a `--join-where`, a join's padded cell), in which refusal gets it (the integer, float and decimal ones) and in the column
it names; the tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/empty_guard_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_empty_guard", "test_rules"]

GUARD = 'w = empty_guard(heap, w, args, parsed, names, hends, kept, c.err_col, c.err_fn, "what the cell was meant to be is not known");'
NONE = 'w = fail.repair_none(heap, w, "what the cell was meant to be is not known");'

MUTANTS = [
    ("the repair is offered for a cell that is not empty", "finish.cho", "    if buffer.size(kept) == 0 && err_fn != 0 - 4", "    if buffer.size(kept) >= 0 && err_fn != 0 - 4"),
    ("the repair is offered for a --join-where", "finish.cho", "err_fn != 0 - 4 && err_fn < 90", "err_fn != 0 - 5 && err_fn < 90"),
    ("the repair is offered for a join's padded cell", "finish.cho", "err_fn != 0 - 4 && err_fn < 90 && col >= 0", "err_fn != 0 - 4 && err_fn < 900 && col >= 0"),
    ("the repair is offered for a --query", "finish.cho", " && col >= 0 && !cli.has(parsed, spec.flag_table(), \"query\") {", " && col >= 0 {"),
    ("the repair names the first column", "finish.cho", "        let key = jnguard.name_for(heap, names, hends, col);", "        let key = jnguard.name_for(heap, names, hends, 0);"),
    ("an empty integer has no repair", "finish.cho", "let context = context_of(c.err_fn);\n        if c.err_fn == -4 {\n            hint = join_where_hint();\n        }\n        var w = fail.open_in(heap, extra(), rule, what, hint);\n        " + GUARD, "let context = context_of(c.err_fn);\n        if c.err_fn == -4 {\n            hint = join_where_hint();\n        }\n        var w = fail.open_in(heap, extra(), rule, what, hint);\n        " + NONE),
    ("an empty float has no repair", "finish.cho", "            hint = \"a number of this length is not a double; compare the column as text\";\n        }\n        if c.err_fn == -4 {\n            hint = join_where_hint();\n        }\n        var w = fail.open_in(heap, extra(), rule, what, hint);\n        " + GUARD, "            hint = \"a number of this length is not a double; compare the column as text\";\n        }\n        if c.err_fn == -4 {\n            hint = join_where_hint();\n        }\n        var w = fail.open_in(heap, extra(), rule, what, hint);\n        " + NONE),
    ("an empty decimal has no repair", "finish.cho", "            w = empty_guard(heap, w, args, parsed, names, hends, kept, c.err_col, c.err_fn, \"what the cell was meant to be is not known (an exponent is a float, which this tool does not read yet)\");", "            w = fail.repair_none(heap, w, \"what the cell was meant to be is not known (an exponent is a float, which this tool does not read yet)\");"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS, sys.argv[1:]))
