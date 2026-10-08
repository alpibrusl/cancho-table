"""R0 and Q0 (docs/readers.md section 3, docs/query.md section 11): the read loop is a function of its own, and `body` reads the plan through one `Inputs` value.

Two things are held, each a property of the sources (the outputs are held by every other gate and by scripts/corpus.py, whose md5 of all the plans is identical to that of
`main`):

* the read loop (the calls that take a line, split it into fields and scan it) is in tools/table/readloop.cho and nowhere in table.cho, which calls it once, so that a
  change to the code that parses the flags and renders the answer cannot move the loop's machine code: the instruction count of the loop then does not depend on the
  text of the rest of table.cho (docs/numbers.md, "What was built: R0 and Q0", `scripts/layout_experiment.py` measures it);
* `body` reads no plan flag with `cli.has`, `cli.text` or `cli.nat`, nor the operands: `inputs_from_flags` does, once, into `Inputs`.

These are source gates: able to fail (scripts/readloop_mutants.py puts the loop back, or a flag read back, and they must notice).
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "tools" / "table"
LOOP_CALLS = ("lines.next(", "lines.fill_file(", "reader.scan(", "scan.next_fast(", "reader.split_header(")
PLAN_FLAGS = ("select", "where", "group", "agg", "order-by", "report", "sort", "top", "limit", "from", "max-rows", "root")


def code(path):
    """The source without its comments (a `//` outside a string; the sources hold none inside one on these lines)."""
    return "\n".join(re.sub(r"^\s*//.*$", "", l) for l in path.read_text().split("\n"))


def function(text, name):
    """The text of a top-level `fn name[...](...) ... {` to its closing brace at column 0."""
    m = re.search(r"^(pub )?fn %s\[" % re.escape(name), text, re.M)
    assert m, "no fn %s" % name
    end = text.index("\n}\n", m.start())
    return text[m.start():end + 3]


class ReadLoop(unittest.TestCase):
    def setUp(self):
        self.table = code(SRC / "table.cho")
        self.loop = code(SRC / "readloop.cho")

    def test_the_loop_calls_are_in_the_loop_module(self):
        for call in LOOP_CALLS:
            with self.subTest(call):
                self.assertIn(call, self.loop, "the read loop no longer calls %s" % call)
                self.assertNotIn(call, self.table, "%s is in table.cho: the loop (or a copy of it) is back in the function that parses the flags" % call)

    def test_table_calls_the_loop_once(self):
        self.assertEqual(self.table.count("readloop.run("), 1)
        self.assertIn("import readloop;", self.table)
        self.assertEqual(len(re.findall(r"^pub fn run\[", self.loop, re.M)), 1)

    def test_the_loop_function_does_not_parse_flags(self):
        run = function(self.loop, "run")
        for word in ("cli.", "flag_table", "Args", "fail.", "out.respond"):
            with self.subTest(word):
                self.assertNotIn(word, run, "the loop function must not read flags or render answers (%s)" % word)

    def test_the_loop_module_does_not_import_the_cli(self):
        self.assertNotRegex(self.loop, r"^import (cli|out|fail);", "the loop module is below the cli")


class Inputs(unittest.TestCase):
    def setUp(self):
        self.table = code(SRC / "table.cho")
        self.body = function(self.table, "body")

    def test_body_reads_no_plan_flag_itself(self):
        for flag in PLAN_FLAGS:
            for fn in ("has", "text", "nat", "value_index", "value_is_whole"):
                with self.subTest(flag=flag, fn=fn):
                    pat = re.compile(r'cli\.%s\((args, )?parsed, table, "%s"\)' % (fn, re.escape(flag)))
                    self.assertIsNone(pat.search(self.body), "body reads --%s with cli.%s: it comes from Inputs" % (flag, fn))
        self.assertNotIn("cli.operand_", self.body)

    def test_body_reads_the_inputs(self):
        self.assertIn("let inp = inputs_from_flags(args, parsed);", self.body)
        for field in ("has_select", "has_where", "has_group", "has_agg", "has_order", "select_text", "where_text", "group_text", "agg_text", "order_text", "top_n", "limit_n", "from_n",
                      "rows_n", "operands", "source_at", "root_text"):
            with self.subTest(field):
                self.assertIn("inp.%s" % field, self.body)

    def test_the_inputs_are_filled_in_one_function(self):
        fill = function(self.table, "inputs_from_flags")
        for flag in PLAN_FLAGS:
            with self.subTest(flag):
                self.assertIn('"%s"' % flag, fill)
        struct = self.table[self.table.index("struct Inputs {"):]
        struct = struct[:struct.index("\n}\n")]
        for field in ("select_text", "where_text", "group_text", "agg_text", "order_text", "top_n", "limit_n", "from_n", "rows_n", "root_text", "operands", "source_at"):
            with self.subTest(field):
                self.assertIn(field + ":", struct)


if __name__ == "__main__":
    unittest.main()
