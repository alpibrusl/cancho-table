"""Q0 (docs/query.md section 11): `body` reads the plan through one `Inputs` value.

One thing is held, a property of the sources (the outputs are held by every other gate and by scripts/corpus.py, whose md5 of all the plans is identical to that of `main`):
`body` reads no plan flag with `cli.has`, `cli.text` or `cli.nat`, nor the operands: `inputs.from_flags` (tools/table/inputs.cho) does, once, into `Inputs`. A later `--query` or a stdin source fills the
same value, so that what `body` checks and builds from does not depend on where the values came from.

This is a source gate: able to fail (scripts/inputs_mutants.py reads a flag in `body` again, or breaks a field of `Inputs`, and it must notice).
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "tools" / "table"
PLAN_FLAGS = ("select", "where", "group", "agg", "order-by", "report", "sort", "top", "limit", "from", "max-rows", "root", "query", "rows")


def code(path):
    """The source without its comments (a `//` outside a string; the sources hold none inside one on these lines)."""
    return "\n".join(re.sub(r"^\s*//.*$", "", l) for l in path.read_text().split("\n"))


def function(text, name):
    """The text of a top-level `fn name[...](...) ... {` to its closing brace at column 0."""
    m = re.search(r"^(pub )?fn %s\[" % re.escape(name), text, re.M)
    assert m, "no fn %s" % name
    end = text.index("\n}\n", m.start())
    return text[m.start():end + 3]


class Inputs(unittest.TestCase):
    def setUp(self):
        self.table = code(SRC / "table.cho")
        self.inputs = code(SRC / "inputs.cho")
        # `body` hands the flags' values to `plan_body`, or a query's translation to it through `query_body`: none of the three reads a plan flag itself
        self.body = function(self.table, "body") + function(self.table, "query_body") + function(self.table, "plan_body")

    def test_body_reads_no_plan_flag_itself(self):
        for flag in PLAN_FLAGS:
            for fn in ("has", "text", "nat", "value_index", "value_is_whole"):
                with self.subTest(flag=flag, fn=fn):
                    pat = re.compile(r'cli\.%s\((args, )?parsed, table, "%s"\)' % (fn, re.escape(flag)))
                    self.assertIsNone(pat.search(self.body), "body reads --%s with cli.%s: it comes from Inputs" % (flag, fn))
        self.assertNotIn("cli.operand_", self.body)

    def test_body_reads_the_inputs(self):
        self.assertIn("let inp = inputs.from_flags(args, parsed, table);", self.body)
        for field in ("has_select", "has_where", "has_group", "has_agg", "has_order", "select_text", "where_text", "group_text", "agg_text", "order_text", "top_n", "limit_n", "from_n",
                      "rows_n", "operands", "source_at", "root_text", "sort_text", "query_text", "has_query", "has_rows"):
            with self.subTest(field):
                self.assertIn("inp.%s" % field, self.body)

    def test_the_inputs_are_filled_in_one_function(self):
        fill = function(self.inputs, "from_flags")
        for flag in PLAN_FLAGS:
            with self.subTest(flag):
                self.assertIn('"%s"' % flag, fill)
        struct = self.inputs[self.inputs.index("struct Inputs {"):]
        struct = struct[:struct.index("\n}\n")]
        for field in ("select_text", "where_text", "group_text", "agg_text", "order_text", "top_n", "limit_n", "from_n", "rows_n", "root_text", "operands", "source_at"):
            with self.subTest(field):
                self.assertIn(field + ":", struct)


if __name__ == "__main__":
    unittest.main()
