"""What `table skill` and `table introspect` say is what the tool does.

A model that has only that text must be able to use the tool, so every claim in it that can be wrong is run:

* `usage` names every flag the flag table has, and no other; each flag a form's line lists is accepted with that
  form, and each flag it leaves out because the form refuses it is refused;
* the conflicts and required flags the summary states are exactly the ones the code raises, over every flag and
  every pair of flags that choose or cut a form;
* the examples in the skill (the file, the commands, what they print) are run, and what they print is compared byte
  for byte (a refusal: its rule, exit status, hint and repair kind);
* the examples in a flag's help are run and accepted, and the claims the help makes about grammar that a mistake
  would turn false (a mean needs its scale, `:float` has an exact sum, `10` is less than `9`, an empty cell under `:int`
  is a refusal) are each checked against the binary.

Nothing here is generated from the code: a change to the code that makes a sentence false fails here, and a change
to the text that no longer says what the test expects fails here, so the two cannot drift apart unseen.
"""

import itertools
import json
import re
import shlex
import subprocess
import unittest

from harness import Scratch, binary, introspect, run

SALES = "host,status,bytes,price\na,200,512,1.50\nb,404,0,2.25\na,200,1024,1.5\nc,500,2048,10.00\nb,200,64,0.75\n"
# Columns for the examples in the help: a path, and a column called `a,b`.
WIDE = 'host,status,bytes,price,path,"a,b"\na,404,1500,1.50,x,1\nb,200,20,2.25,y,2\nc,404,3000,10.00,x z,3\n'

# Every example a flag's help gives, as written there, with what must surround it to be run. The test checks
# the strings occur in the help and that nothing is refused.
HELP_EXAMPLES = {
    "select": [("--select host,bytes", []), ("--select '#3,a\\,b'", [])],
    "where": [("--where 'status = 404 and bytes:int > 1000'", []), ("--where 'host in (a, b) and path contains x'", [])],
    "group": [("--group host", []), ("--group host,status", []), ("--group 'price:dec(2),status'", [])],
    "agg": [("--agg count,sum:bytes,max:bytes", ["--group", "host"]), ("--agg 'sum:price:dec(2),mean:bytes@1'", [])],
    "sort": [("--sort -count", ["--group", "host"]), ("--sort sum:bytes", ["--group", "host", "--agg", "sum:bytes"])],
    "top": [("--top 10", ["--group", "host"]), ("--top 3", ["--order-by", "host"])],
    "limit": [("--limit 100", ["--select", "host"]), ("--limit 20 --from 40", ["--select", "host"])],
    "from": [("--from 1000", ["--select", "host"]), ("--from 2000", ["--select", "host"])],
    "order-by": [("--order-by -bytes:int", []), ("--order-by status,-bytes:int", []), ("--order-by '-price:dec(2)'", [])],
}

# One value per flag, valid for sales.csv, to put a flag into an invocation.
SAMPLE = {
    "--root": ".", "--delimiter": ",", "--max-rows": "100", "--max-line-bytes": "1000", "--select": "host",
    "--where": "host = a", "--order-by": "host", "--group": "host", "--agg": "count", "--sort": "count",
    "--top": "1", "--limit": "1", "--from": "0", "--max-bytes": "100000", "--max-sort-rows": "100",
    "--max-groups": "100", "--max-distinct": "100", "--max-state-bytes": "100000", "--threads": "1",
    "--chunk-bytes": "100000", "--parallel-min-bytes": "100000", "--format": "json",
}
REFUSALS = {"args.conflict", "args.required-flag"}
# What each form's line in the usage lists: the flags the code takes for the form and does something with
# (the ones it takes and ignores, such as --threads with the shape, are not).
COMMON = {"--root", "--delimiter", "--max-rows", "--max-line-bytes", "--format"}
PAGED = {"--top", "--limit", "--from", "--max-bytes", "--max-state-bytes", "--threads", "--chunk-bytes", "--parallel-min-bytes"}
FORM_FLAGS = {
    "shape": COMMON,
    "rows": COMMON | PAGED | {"--select", "--where", "--order-by", "--max-sort-rows"},
    "groups": COMMON | PAGED | {"--group", "--agg", "--where", "--sort", "--max-groups", "--max-distinct"},
}
FORM_BASE = {"shape": [], "rows": ["--select", "host"], "groups": ["--group", "host"]}


def usage_lines():
    """{form: the flags its line lists}, and the whole set the usage names."""
    text = introspect()["usage"]
    forms = {}
    for line in text.splitlines():
        m = re.match(r"(shape|rows|groups): ", line)
        if m:
            forms[m.group(1)] = set(re.findall(r"--[a-z][a-z-]*", line))
    return forms, set(re.findall(r"--[a-z][a-z-]*", text))


def refusals(scratch, argv):
    """The rules among args.conflict and args.required-flag that `table argv sales.csv` raises (json mode)."""
    r = run("--root", scratch.dir, *argv, "sales.csv")
    try:
        errs = [e["rule"] for e in r.doc().get("errors") or []]
    except (AssertionError, ValueError):
        # csv and text answer on standard error: `table: RULE: message`
        errs = [l.split(": ")[1] for l in r.stderr.decode().splitlines() if l.startswith("table: ")]
    return set(errs) & REFUSALS


def args_of(items):
    """Flags (with the samples) for a set of selector names such as 'select' or 'format=csv'."""
    out = []
    for it in items:
        if "=" in it:
            name, value = it.split("=")
            out += ["--" + name, value]
        else:
            out += ["--" + it, SAMPLE["--" + it]]
    return out


class Usage(unittest.TestCase):
    def test_usage_names_every_flag_and_only_flags(self):
        forms, named = usage_lines()
        table = {f["name"] for f in introspect()["flags"]}
        self.assertEqual(set(forms), {"shape", "rows", "groups"})
        self.assertEqual(named, table, "usage and the flag table disagree: missing %s, extra %s" % (sorted(table - named), sorted(named - table)))

    def test_each_line_lists_what_its_form_takes(self):
        forms, _ = usage_lines()
        for form, want in FORM_FLAGS.items():
            self.assertEqual(forms[form], want, "the %s line: missing %s, extra %s" % (form, sorted(want - forms[form]), sorted(forms[form] - want)))

    def test_every_flag_a_form_lists_is_accepted_with_it(self):
        forms, _ = usage_lines()
        s = Scratch()
        try:
            s.write("sales.csv", SALES)
            for form, flags in forms.items():
                for flag in sorted(flags):
                    base = list(FORM_BASE[form])
                    if flag in base:
                        continue
                    extra = [flag, SAMPLE[flag]]
                    if flag == "--top" and form == "rows":
                        extra = ["--order-by", "host"] + extra
                    if flag == "--sort":
                        extra = [flag, SAMPLE[flag]]
                    if flag == "--format":
                        extra = [flag, "text" if form == "shape" else "csv"]
                    got = refusals(s, base + extra)
                    self.assertEqual(got, set(), "the %s line lists %s but the code refuses it with %s" % (form, flag, sorted(got)))
        finally:
            s.cleanup()

    def test_a_flag_a_form_leaves_out_because_it_refuses_it_is_refused(self):
        forms, _ = usage_lines()
        s = Scratch()
        try:
            s.write("sales.csv", SALES)
            # the rows line has no grouping flag, the groups line no row-picking flag
            for flag in ("--group", "--agg", "--sort"):
                self.assertNotIn(flag, forms["rows"])
                self.assertTrue(refusals(s, FORM_BASE["rows"] + [flag, SAMPLE[flag]]), flag + " is accepted with rows")
            for flag in ("--select", "--order-by"):
                self.assertNotIn(flag, forms["groups"])
                self.assertTrue(refusals(s, FORM_BASE["groups"] + [flag, SAMPLE[flag]]), flag + " is accepted with groups")
            # the shape line has none of the flags that make a form or cut one
            for flag in ("--select", "--where", "--order-by", "--group", "--agg", "--sort", "--top", "--limit", "--from"):
                self.assertNotIn(flag, forms["shape"])
            for flag in ("--limit", "--from", "--top", "--sort"):
                self.assertTrue(refusals(s, [flag, SAMPLE[flag]]), flag + " is accepted with the shape")
            self.assertTrue(refusals(s, ["--format", "csv"]), "--format csv is accepted with the shape")
        finally:
            s.cleanup()


class Forms(unittest.TestCase):
    """The summary states the conflicts and the required flags; the code raises exactly those."""

    CONFLICT = "args.conflict: --select or --order-by with --group or --agg, and --format text with rows or groups."
    REQUIRED = ("args.required-flag: --limit, --from or --format csv with the shape, --top with neither --order-by, "
                "--group nor --agg, and --sort without --group or --agg.")

    @staticmethod
    def predicted(items):
        has = set(i.split("=")[0] for i in items)
        groups = bool(has & {"group", "agg"})
        rows = not groups and bool(has & {"select", "where", "order-by"})
        shape = not groups and not rows
        out = set()
        if ("select" in has or "order-by" in has) and groups or "format=text" in items and (groups or rows):
            out.add("args.conflict")
        if shape and (has & {"limit", "from"} or "format=csv" in items) \
                or "top" in has and not has & {"order-by", "group", "agg"} \
                or "sort" in has and not groups:
            out.add("args.required-flag")
        return out

    def test_the_summary_says_what_the_code_refuses(self):
        summary = introspect()["summary"]
        self.assertIn(self.CONFLICT, summary)
        self.assertIn(self.REQUIRED, summary)
        s = Scratch()
        try:
            s.write("sales.csv", SALES)
            names = ["select", "where", "order-by", "group", "agg", "sort", "top", "limit", "from", "format=csv", "format=text"]
            wrong = []
            for k in (1, 2):
                for combo in itertools.combinations(names, k):
                    got = refusals(s, args_of(combo))
                    want = self.predicted(combo)
                    if got != want:
                        wrong.append("%s: the code raises %s, the summary %s" % (" ".join(combo), sorted(got), sorted(want)))
            self.assertEqual(wrong, [])
        finally:
            s.cleanup()

    def test_each_form_runs_and_the_summary_names_it(self):
        summary = introspect()["summary"]
        for phrase in ("the shape (FILE alone", "rows (--select, --where or --order-by", "groups (--group or --agg"):
            self.assertIn(phrase, summary)
        s = Scratch()
        try:
            s.write("sales.csv", SALES)
            for argv in ([], ["--select", "host"], ["--where", "host = a"], ["--order-by", "host"], ["--group", "host"], ["--agg", "count"]):
                r = run("--root", s.dir, *argv, "sales.csv")
                self.assertEqual(r.status, 0, argv)
            # one group of all the rows, said under the groups line
            self.assertEqual(run("--root", s.dir, "--agg", "count", "sales.csv").data()["group_count"], 1)
        finally:
            s.cleanup()


class Examples(unittest.TestCase):
    def examples(self):
        """(the file, [(command, what it prints)]) from the skill's usage."""
        text = introspect()["usage"]
        after = text.split("Examples, on the file sales.csv", 1)[1].split("\n", 1)[1]
        file_part, rest = after.split("\n\n", 1)
        pairs = []
        lines = rest.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("$ table "):
                pairs.append((line[len("$ table "):], lines[i + 1]))
        return file_part + "\n", pairs

    def test_the_file_in_the_skill_is_the_one_the_tests_use(self):
        file, _ = self.examples()
        self.assertEqual(file, SALES)

    def test_between_three_and_five_examples(self):
        _, pairs = self.examples()
        self.assertTrue(3 <= len(pairs) <= 5, len(pairs))

    def test_every_example_prints_what_the_skill_says(self):
        file, pairs = self.examples()
        s = Scratch()
        try:
            s.write("sales.csv", file)
            skill = subprocess.run([binary(), "skill"], capture_output=True, check=True).stdout.decode()
            for command, printed in pairs:
                self.assertIn("$ table " + command + "\n" + printed, skill)
                p = subprocess.run([binary(), *shlex.split(command)], capture_output=True, cwd=str(s.dir))
                out = p.stdout.decode().rstrip("\n")
                m = re.match(r'refused: exit (\d+), rule (\S+), hint "(.*)", repair (\w+)$', printed)
                if m:
                    e = json.loads(out)["error"]
                    got = (p.returncode, e["rule"], e["hint"], e["repair"]["kind"] if e["repair"] else "none")
                    self.assertEqual(got, (int(m.group(1)), m.group(2), m.group(3), m.group(4)), command)
                else:
                    self.assertEqual((p.returncode, out), (0, printed), command)
            kinds = [bool(re.match(r"refused: ", printed)) for _, printed in pairs]
            self.assertIn(True, kinds, "no refusal among the examples")
            self.assertIn(False, kinds)
        finally:
            s.cleanup()


class HelpExamples(unittest.TestCase):
    def test_every_flag_that_takes_a_string_has_two_examples_and_they_run(self):
        helps = {f["name"][2:]: f["help"] for f in introspect()["flags"]}
        s = Scratch()
        try:
            s.write("wide.csv", WIDE)
            for flag, examples in HELP_EXAMPLES.items():
                self.assertIn("Examples: ", helps[flag], flag)
                self.assertGreaterEqual(len(examples), 2, flag)
                shown = helps[flag].split("Examples: ", 1)[1]
                for text, extra in examples:
                    self.assertIn(text, shown, "%s: the help does not show %r" % (flag, text))
                    r = run("--root", s.dir, *extra, *shlex.split(text), "wide.csv")
                    self.assertEqual((r.status, r.first_rule()), (0, None), "%s: %r is refused: %r" % (flag, text, r.stdout[:300]))
        finally:
            s.cleanup()

    def test_no_help_breaks_the_flag_table(self):
        for f in introspect()["flags"]:
            self.assertNotIn(";", f["help"], f["name"])
            self.assertNotIn("|", f["help"], f["name"])

    def test_the_operand_says_what_a_file_is(self):
        (op,) = introspect()["operands"]
        self.assertEqual((op["name"], op["min"], op["max"]), ("FILE", 1, 1))
        for word in ("--root", "header", "relative"):
            self.assertIn(word, op["help"])


class Claims(unittest.TestCase):
    """Sentences of the help that a change of the grammar would make false."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("sales.csv", SALES)

    def tearDown(self):
        self.s.cleanup()

    def r(self, *argv, file="sales.csv"):
        return run("--root", self.s.dir, *argv, file)

    def test_agg_grammar(self):
        help_ = {f["name"]: f["help"] for f in introspect()["flags"]}["--agg"]
        # a mean of an integer column needs its scale, a decimal column has its own
        self.assertIn("mean:bytes@2", help_)
        self.assertIn("@N may be left out only for a :dec(S) column", help_)
        self.assertEqual(self.r("--agg", "mean:bytes").first_rule(), "agg.bad-spec")
        self.assertEqual(self.r("--agg", "mean:bytes@2").data()["rows"], [["729.60"]])
        self.assertEqual(self.r("--agg", "mean:price:dec(2)").first_rule(), None)
        # the suffixes are not in the output column
        self.assertEqual(self.r("--agg", "sum:price:dec(2)").data()["columns"], ["sum:price"])
        # :float has min, max, distinct and, from N4, the exact sum and mean (a mean has no scale)
        self.assertIn("and the exact sum and mean", help_)
        self.assertIn("mean:x:float@2 is agg.bad-spec", help_)
        for item in ("min:price:float", "max:price:float", "distinct:price:float", "sum:price:float", "mean:price:float"):
            self.assertEqual(self.r("--agg", item).first_rule(), None, item)
        self.assertEqual(self.r("--agg", "mean:price:float@2").first_rule(), "agg.bad-spec")
        # :dec(S) is 0 to 18
        self.assertEqual(self.r("--agg", "sum:price:dec(19)").first_rule(), "agg.bad-spec")
        # the escape of a column really called `x:dec(2)`
        self.s.write("odd.csv", "x:dec(2),k\n1,a\n2,a\n")
        r = self.r("--agg", "sum:x\\:dec(2)", file="odd.csv")
        self.assertEqual(r.data()["rows"], [["3"]])
        self.assertEqual(r.data()["columns"], ["sum:x:dec(2)"])
        self.assertIn("sum:x\\:dec(2)", help_)

    def test_typed_group_and_order_keys(self):
        flags = {f["name"]: f["help"] for f in introspect()["flags"]}
        group, order, sort = flags["--group"], flags["--order-by"], flags["--sort"]
        # the help says what the tool does: a typed key is the value, written at its scale, in numeric order
        self.assertIn("to group by the value of the cell and not its text", group)
        self.assertIn("1.5 and 1.50 are one group", group)
        d = self.r("--group", "price:dec(2)", "--agg", "count").data()
        self.assertEqual(d["rows"], [["0.75", "1"], ["1.50", "2"], ["2.25", "1"], ["10.00", "1"]])        # one group for 1.50 and 1.5, at its scale, numeric order
        self.assertIn("and the groups come in numeric order, negatives first", group)
        d = self.r("--group", "price:float", "--agg", "count", "--sort", "-count").data()
        self.assertEqual(d["rows"][0], ["1.5", "2"])
        self.assertIn("(numerically for a typed --group column)", sort)
        # a cell that is not of the type refuses the query, context group or order-by, as the help says
        self.assertIn("(value.not-decimal and the like, context group)", group)
        self.assertIn("context order-by)", order)
        for flag, ctx in (("--group", "group"), ("--order-by", "order-by")):
            got = self.r(flag, "host:dec(2)")
            self.assertEqual((got.first_rule(), got.error()["detail"]["context"]), ("value.not-decimal", ctx), got)
        self.assertIn(":int, :dec(S) or :float after it to compare by value", order)
        rows = lambda *a: [r[0] for r in self.r("--order-by", *a, "--select", "host").data()["rows"]]
        self.assertEqual(rows("price:float"), ["b", "a", "a", "b", "c"])                                  # 0.75, 1.50, 1.5 (a tie, in file order), 2.25, 10.00
        self.assertEqual(rows("-price:float"), ["c", "b", "a", "a", "b"])
        self.assertIn("column.type-conflict", group)

    def test_where_grammar(self):
        self.s.write("nums.csv", "v,w\n10,a\n9,b\n,c\n")
        rows = lambda *a: [r[0] for r in self.r("--where", *a, file="nums.csv").data()["rows"]]
        help_ = {f["name"]: f["help"] for f in introspect()["flags"]}["--where"]
        for sentence in ("so 10 is less than 9", "no or, no parentheses", "keep such rows out first: bytes != '' and bytes:int > 5"):
            self.assertIn(sentence, help_)
        # text compares bytewise: 10 is less than 9, and an empty cell is the empty text
        self.assertEqual(rows("v < 9"), ["10", ""])
        # an empty cell under :int refuses, unless a condition before it is false
        self.assertEqual(self.r("--where", "v:int > 5", file="nums.csv").first_rule(), "value.not-integer")
        self.assertEqual(rows("v != '' and v:int > 5"), ["10", "9"])
        # no `or`, no parentheses around conditions
        self.assertEqual(self.r("--where", "v = 1 or v = 2", file="nums.csv").first_rule(), "where.syntax")
        self.assertEqual(self.r("--where", "(v = 1)", file="nums.csv").first_rule(), "where.syntax")
        # the grammar is in the help, which is what the hint says
        self.assertIn("help of --where", self.r("--where", "v =", file="nums.csv").error()["hint"])

    def test_order_by_grammar(self):
        self.s.write("nums.csv", "v,w\n10,a\n9,b\n,c\n9,a\n")
        rows = lambda *a: [r[1] for r in self.r("--order-by", *a, file="nums.csv").data()["rows"]]
        # bytewise: the empty cell first, 10 before 9; descending puts the empty cell last
        self.assertEqual(rows("v"), ["c", "a", "b", "a"])
        self.assertEqual(rows("-v"), ["b", "a", "a", "c"])
        # ties keep the order of the file, and a later key breaks them
        self.assertEqual(rows("v,w"), ["c", "a", "a", "b"])
        # :int refuses an empty cell
        self.assertEqual(self.r("--order-by", "v:int", file="nums.csv").first_rule(), "value.not-integer")
        # and with --group it is a conflict
        self.assertEqual(self.r("--order-by", "v", "--group", "w", file="nums.csv").first_rule(), "args.conflict")

    def test_sort_and_top(self):
        d = self.r("--group", "host", "--agg", "count,sum:bytes", "--sort", "-count").data()
        self.assertEqual([x[0] for x in d["rows"]], ["a", "b", "c"])
        d = self.r("--group", "host", "--agg", "sum:bytes", "--sort", "sum:bytes").data()
        self.assertEqual([x[0] for x in d["rows"]], ["b", "a", "c"])
        # without --sort the groups come in byte order
        d = self.r("--group", "status").data()
        self.assertEqual([x[0] for x in d["rows"]], ["200", "404", "500"])
        # a cut answer says where the next page starts
        d = self.r("--select", "host", "--limit", "2").data()
        self.assertEqual((d["truncated"], d["next"]), (True, {"from": 2}))
        self.assertEqual(self.r("--select", "host", "--limit", "2", "--from", "2").data()["rows"], [["a"], ["c"]])

    def test_select_grammar(self):
        self.s.write("commas.csv", 'x,"a,b",c\n1,2,3\n')
        self.assertEqual(self.r("--select", "#3,a\\,b", file="commas.csv").data()["rows"], [["3", "2"]])
        self.assertEqual(self.r("--select", "x,x", file="commas.csv").data()["rows"], [["1", "1"]])

    def test_csv_is_not_a_document(self):
        help_ = {f["name"]: f["help"] for f in introspect()["flags"]}["--format"]
        self.assertIn("not a json document", help_)
        self.s.write("late.csv", "a\n1\nx\n")
        p = subprocess.run([binary(), "--root", str(self.s.dir), "--where", "a:int > 0", "--format", "csv", "late.csv"], capture_output=True)
        # the rows before the refusal stand, the verdict is one line on standard error, and the status says it
        self.assertEqual(p.stdout.decode(), "a\n1\n")
        self.assertTrue(p.stderr.decode().startswith("table: value.not-integer: "), p.stderr)
        self.assertEqual(p.returncode, 8)
        self.assertIn("a refusal is one line on standard error", help_)


if __name__ == "__main__":
    unittest.main()
