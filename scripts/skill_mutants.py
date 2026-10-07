#!/usr/bin/env python3
"""Mutation check of what `table skill` and `table introspect` say about the tool (tests/conformance/test_skill.py,
and the hint and repair kind each rule's summary states, in test_rules.py): each mutant is tools/table/table.cho with
one sentence made false, a flag missing from the usage, an example that no longer prints what it says, a conflict that
the code does not raise, rebuilt and run against those tests. A mutant is killed when a test fails or the build refuses
it. The code that raises the conflicts has its own mutants (filter_mutants.py, sort_mutants.py); these are the words.

    python3 scripts/skill_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_skill", "test_rules", "test_describe"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file. The source holds
# the strings escaped (a quote is \", a newline \n), and the patterns are written the same way.
Q = '\\"'
MUTANTS = [
    # usage: the flags of each form
    ("the rows line forgets --order-by", "table.cho", "[--max-line-bytes N] [--select NAMES] [--where EXPR] [--order-by KEYS] [--top N]", "[--max-line-bytes N] [--select NAMES] [--where EXPR] [--top N]"),
    ("the rows line forgets --threads", "table.cho", "[--max-sort-rows N] [--max-state-bytes N] [--threads N]", "[--max-sort-rows N] [--max-state-bytes N]"),
    ("the groups line lists --select", "table.cho", "--group NAMES [--agg ITEMS]", "--group NAMES [--select NAMES] [--agg ITEMS]"),
    ("the rows line lists --sort", "table.cho", "[--max-line-bytes N] [--select NAMES] [--where EXPR] [--order-by KEYS] [--top N]", "[--max-line-bytes N] [--select NAMES] [--where EXPR] [--order-by KEYS] [--sort KEY] [--top N]"),
    ("the shape line lists --limit", "table.cho", "[--max-line-bytes N] [--format json|text] FILE", "[--max-line-bytes N] [--limit N] [--format json|text] FILE"),
    ("a flag that is no flag is in the usage", "table.cho", "[--max-rows N] [--max-line-bytes N] --group NAMES", "[--max-rows N] [--max-line-bytes N] [--max-widgets N] --group NAMES"),
    # the summary: what the code refuses
    ("a conflict is not stated: --order-by with groups", "table.cho", "args.conflict: --select or --order-by with", "args.conflict: --select with"),
    ("a requirement is not stated: --sort needs groups", "table.cho", "and --sort without --group or --agg.", "and --sort without --group."),
    ("a requirement is not stated: --format csv needs a form", "table.cho", "--limit, --from or --format csv with the shape", "--limit or --from with the shape"),
    ("a conflict is stated that the code does not raise", "table.cho", "and --format text with rows or groups.", "and --format csv with rows or groups."),
    ("a form is not named", "table.cho", "rows (--select, --where or --order-by, then", "rows (--select or --where, then"),
    # the examples
    ("an example prints another next", "table.cho", "truncated" + Q + ":true," + Q + "next" + Q + ":{" + Q + "from" + Q + ":2}", "truncated" + Q + ":true," + Q + "next" + Q + ":{" + Q + "from" + Q + ":3}"),
    ("an example's command is changed", "table.cho", "--sort -sum:bytes --top 2 sales.csv", "--sort -sum:bytes --top 3 sales.csv"),
    ("the file of the examples is changed", "table.cho", "a,200,512,1.50", "a,200,513,1.50"),
    ("the refusal's hint is another", "table.cho", "hint " + Q + "drop --select" + Q, "hint " + Q + "drop --group" + Q),
    ("the refusal's exit status is another", "table.cho", "refused: exit 2, rule args.conflict", "refused: exit 3, rule args.conflict"),
    # the help of the flags
    ("an example in the help is refused", "table.cho", "--sort -count and --sort sum:bytes", "--sort -total and --sort sum:bytes"),
    ("an example of --where is not a condition", "table.cho", "--where 'status = 404 and bytes:int > 1000'", "--where 'status = 404 or bytes:int > 1000'"),
    ("the help says a float has a sum", "table.cho", "(min, max and distinct only)", "(min, max, sum and distinct)"),
    ("the help says text compares the other way", "table.cho", "so 10 is less than 9", "so 9 is less than 10"),
    ("the help says csv is a document", "table.cho", "csv and text are not a json document", "csv and text are a json document"),
    ("the help says a refusal in csv is on standard output", "table.cho", "a refusal is one line on standard error", "a refusal is one line on standard output"),
    ("the help of --agg forgets the scale of a mean", "table.cho", "as mean:bytes@2, and @N may be left out", "as mean:bytes@2, and @N may be left out of a sum"),
    ("the escape of a column called x:dec(2) is not the one", "table.cho", "sum:x\\\\:dec(2)", "sum:x\\\\\\\\:dec(2)"),
    ("the operand's help forgets the header", "table.cho", "whose first row is the header that --select", "whose first row is data that --select"),
    # the rules
    ("a summary says a refusal has a repair when it has none", "table.cho", "select.unknown-column|3|sometimes|a name or position in --select that is not a column of the header. Hint: pick from detail.available. Repair: choose.", "select.unknown-column|3|sometimes|a name or position in --select that is not a column of the header. Hint: pick from detail.available. Repair: none."),
    ("a summary names another kind of repair", "table.cho", "the column holds. Repair: choose.", "the column holds. Repair: retry."),
    ("a summary hints something the refusal does not", "table.cho", "select.ambiguous-column|8|never|a name in --select that is the name of more than one column. Hint: name it by position, as #3 for the third column.", "select.ambiguous-column|8|never|a name in --select that is the name of more than one column. Hint: name it by position, as #3 for the fourth column."),
    ("the hint of where.syntax names a file again", "table.cho", "\"the grammar is in the help of --where: COLUMN OP VALUE joined by and\");", "\"see docs/filter.md for the grammar: COLUMN OP VALUE joined by and\");"),
    ("io.write-failed is not declared", "table.cho", "io.read-failed;io.write-failed;", "io.read-failed;"),
]

# Not here, and why: mutants that change nothing a test can see.
#  - a flag a form accepts but its usage line leaves out (`[--format json|text]` dropped from the shape line, while the
#    rows and groups lines still name --format): the usage understates a form, which is not false; the union of the lines
#    is what is held equal to the flag table;
#  - the wording of the prose around the examples (a word of "Examples, on the file sales.csv ..." other than the file's
#    name, which the test splits on).


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
