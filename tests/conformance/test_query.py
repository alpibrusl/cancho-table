"""Q1 (docs/query.md sections 1 to 6, 8 and 11): `--query` is a translator from a small SQL subset to the flags the tool already has, and the one plan runs.

The gates, each of them able to fail (scripts/query_mutants.py breaks the tool in the ways listed there and every one must be noticed):

* **Q1-EQ** the equivalence: a random table and a random plan, written as flags (`query_gen.flag_form`, hand-written) and as a query (`query_gen.query_form`, SQL text with
  random keyword case and spacing), run on the same file as JSON and as CSV (and with threads): the same exit status, the same standard output and the same standard error,
  refusals included. The one normalisation is a class: a literal that does not fit its column is `where.syntax` in the flag form and `query.syntax` (at the offset in the query)
  in the query form, and a repair that offers the invocation (a `choose` of argv) is the flags' own. `scripts/query_corpus.py` is the same gate over the 166 plans of
  `scripts/corpus.py` on the benchmark files.
* **Q1-OFFSETS** every place the grammar can fail, pinned (`query_cases.REFUSALS`): the rule, the byte offset, what was expected or the feature, and the reference front end
  (`query_ref.py`, which shares no code with the tool) agrees.
* **Q1-ORACLE / Q1-FUZZ** random and mutated query strings: no trap, only tagged refusals (a rule that `introspect` lists), exit 2 for a query refusal with an offset inside the
  query, and the reference front end agrees on accepted or refused, the kind, the offset and the expectation.
* **Q1-LIMITS** 4,096 bytes accepted and 4,097 refused; 64 conditions and 1,024 values at and past the ceiling. **Q1-CONFLICT** `--query` with each plan flag. **Q1-KEYWORDS**
  every reserved word refused bare and accepted quoted, end to end. **Q1-DOC** the worked examples of docs/query.md.

TABLE_QUERY_CASES (default 150) sets the random plans of Q1-EQ (TABLE_QUERY_THREADS=all runs every one with one thread and with four), TABLE_QUERY_FUZZ (default 1500) the strings of
Q1-FUZZ, TABLE_QUERY_SEED their seed, TABLE_QUERY_REPORT=1 prints what the equivalence saw; the numbers of a run that is quoted are in the pull request.
"""

import concurrent.futures
import json
import os
import pathlib
import random
import re
import shlex
import shutil
import tempfile
import unittest

import query_cases
import query_gen
import query_ref
from test_skill import SALES
from harness import ROOT, TRAPS, Scratch, binary, introspect, run, validate

CASES = int(os.environ.get("TABLE_QUERY_CASES", "150"))
FUZZ = int(os.environ.get("TABLE_QUERY_FUZZ", "1500"))
ALL_THREADS = os.environ.get("TABLE_QUERY_THREADS") == "all"
THREADS = ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "65536"]


def doc(result):
    """The answer as a JSON object, or None (a csv answer is not one, and may be a JSON string or number by chance: a single cell)."""
    try:
        d = json.loads(result.stdout.decode("utf-8"))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def rule_of(result):
    d = doc(result)
    if d and not d.get("ok", True):
        return d["error"]["rule"]
    if result.stderr.startswith(b"table: "):
        return result.stderr.decode("utf-8", "replace").split(":")[1].strip()
    return None


def comparable(result):
    """(status, stdout, stderr) as the equivalence gate compares them: a refusal without its `repair` (an invocation offered back is the flags' own, docs/query.md
    section 1), and a literal that does not fit its column as one class (`where.syntax` in the flag form, `query.syntax` in the query form)."""
    out, err = result.stdout, result.stderr
    d = doc(result)
    if d is not None and d.get("ok") is False:
        if d["error"]["rule"] in ("where.syntax", "query.syntax"):
            return result.status, b"SYNTAX", b""
        for e in [d["error"]] + d.get("errors", []):
            e.pop("repair", None)
        out = (json.dumps(d, sort_keys=True) + "\n").encode()
    if err.startswith((b"table: where.syntax:", b"table: query.syntax:")):
        return result.status, out, b"SYNTAX"
    return result.status, out, err


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)
        for name, text in query_cases.FIXTURES.items():
            self.s.write(name, text)
        self.s.write("f.csv", "a,b,c\n1,x,1.5\n2,y,2.5\n")
        self.s.write("sales.csv", SALES)
        self.cwd = self.s.dir

    def q(self, query, *flags, stdin=None):
        return run("--query", query, *flags, cwd=self.cwd, stdin=stdin)

    def flags(self, *flags, stdin=None):
        return run(*flags, cwd=self.cwd, stdin=stdin)


# ---- Q1-EQ -----------------------------------------------------------------------------------------------------------------------

class Equivalence(unittest.TestCase):
    def test_random_plans_flags_and_query_give_the_same_bytes(self):
        rnd = random.Random(int(os.environ.get("TABLE_QUERY_SEED", "11")))
        seen = {"ran": 0, "refusals": 0, "rules": {}}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.csv")
            for case in range(CASES):
                names, kinds, rows = query_gen.make_table(rnd)
                query_gen.write_csv(path, names, rows)
                plan = query_gen.make_plan(rnd, names, kinds, rows, many=True)
                flags = query_gen.flag_form(plan)
                from_clause = "'f.csv'" if rnd.random() < 0.5 else None
                query = query_gen.query_form(rnd, plan, from_clause)
                # one thread, and four threads on tiny ranges for every fifth case (for every case with TABLE_QUERY_THREADS=all)
                extras = [[], THREADS] if ALL_THREADS else [THREADS if case % 5 == 0 else []]
                for extra in extras:
                    for fmt in ("json", "csv"):
                        a = run(*flags, "--format", fmt, *extra, "f.csv", cwd=d)
                        b = run("--query", query, "--format", fmt, *extra, *([] if from_clause else ["f.csv"]), cwd=d)
                        seen["ran"] += 1
                        if a.status != 0:
                            seen["refusals"] += 1
                            seen["rules"][rule_of(a)] = seen["rules"].get(rule_of(a), 0) + 1
                        self.assertEqual(comparable(a), comparable(b), "case %d\n  flags: %r\n  query: %r\n  table: %r" % (case, flags, query, names))
        # a gate that never saw a refusal, or that never ran, has gated nothing
        self.assertEqual(seen["ran"], 2 * CASES * (2 if ALL_THREADS else 1))
        if os.environ.get("TABLE_QUERY_REPORT"):
            print("\nQ1-EQ: %d plans, %d runs, %d refusals: %s" % (CASES, seen["ran"], seen["refusals"], json.dumps(seen["rules"], sort_keys=True)))
        self.assertGreater(seen["refusals"], CASES // 10, seen)


# ---- Q1-OFFSETS ------------------------------------------------------------------------------------------------------------------

def query_ref_readable(query):
    """Whether the rest of the query is whole apart from the mix, so that the refusal offers the two readings (the reference translates it with the mix let through)."""
    try:
        query_ref.translate_ignoring_mix(query)
    except query_ref.QueryError:
        return False
    return True


class Refusals(Base):
    def check(self, row):
        query, kind, offset, what = row[:4]
        r = self.q(query)
        with self.subTest(query=query):
            self.assertEqual(r.status, 2, r)
            d = doc(r)
            self.assertEqual(validate(r), [], r)
            e = d["error"]
            self.assertEqual(e["rule"], "query.mixed-and-or" if kind == "mixed" else "query." + kind, r)
            detail = e["detail"]
            self.assertEqual(detail["offset"], offset, r)
            self.assertEqual(detail["query"], query)
            if kind == "unsupported":
                self.assertEqual(detail["feature"], what)
                self.assertTrue(e["hint"])
            elif kind == "mixed":
                self.assertNotIn("expected", detail)
                self.assertEqual(e["repair"]["kind"], "none" if not query_ref_readable(query) else "choose", e)
            else:
                self.assertEqual(detail["expected"], what)
            if kind == "group-mismatch":
                self.assertEqual((detail["select"], detail["group_by"]), (row[4], row[5]))
            self.assertIn(e["repair"]["kind"], ("choose", "none"))
            if e["repair"]["kind"] == "none":
                self.assertTrue(e["repair"]["reason"])
            # the reference front end says the same
            try:
                query_ref.translate(query)
                self.fail("the reference accepts it")
            except query_ref.QueryError as err:
                self.assertEqual((err.kind, err.offset, err.expected if err.kind in ("syntax", "group-mismatch") else err.feature), (kind, offset, what))

    def test_every_pinned_refusal(self):
        for row in query_cases.REFUSALS:
            self.check(row)

    def test_every_rule_and_kind_is_pinned(self):
        kinds = {row[1] for row in query_cases.REFUSALS}
        self.assertEqual(kinds, {"syntax", "unsupported", "group-mismatch", "mixed"})
        features = {row[3] for row in query_cases.REFUSALS if row[1] == "unsupported"}
        for feature in ("subquery", "join", "alias", "count-column", "cast-in-select", "cast-in-group", "column-comparison", "null", "boolean", "like", "between", "is-null",
                        "having", "union", "expression", "empty-name", "order-by-two-keys", "order-by-position", "order-by-dash-name"):
            self.assertIn(feature, features)

    def test_a_literal_that_does_not_fit_its_column_is_told_at_the_literal(self):
        for query, value, column, type_, expected in query_cases.LITERALS:
            with self.subTest(query=query):
                r = self.q(query)
                self.assertEqual((r.status, rule_of(r)), (2, "query.syntax"), r)
                self.assertEqual(validate(r), [], r)
                detail = doc(r)["error"]["detail"]
                self.assertEqual(detail, {"offset": query.rindex(value), "expected": expected, "query": query, "column": column, "type": type_, "value": value})
                self.assertEqual(doc(r)["error"]["repair"]["kind"], "none")
                # the flags' own refusal of the same literal is --where's, at its own offset in the translated expression: one refusal, written for two front ends
                argv, _ = query_ref.translate(query)
                flags = self.flags(*argv, "f.csv")
                self.assertEqual((flags.status, rule_of(flags)), (2, "where.syntax"))
                self.assertEqual(doc(flags)["error"]["detail"]["expected"], expected)

    def test_a_refusal_reads_nothing(self):
        # the query is refused before the file is opened: a file that does not exist is not what is reported
        r = run("--query", "select a from", "no-such-file.csv", cwd=self.cwd)
        self.assertEqual(rule_of(r), "query.syntax")
        self.assertEqual(r.status, 2)

    def test_csv_refusal_is_a_sentence_on_standard_error(self):
        r = self.q("select a from", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (2, b""))
        self.assertTrue(r.stderr.startswith(b"table: query.syntax: "), r.stderr)

    def test_too_long(self):
        q = "select a from 'f.csv' where b = '%s'" % ("x" * 4100)
        r = self.q(q)
        self.assertEqual((r.status, rule_of(r)), (2, "query.too-long"))
        detail = doc(r)["error"]["detail"]
        self.assertEqual((detail["bytes"], detail["limit"]), (len(q), 4096))
        self.assertEqual(doc(r)["error"]["repair"]["kind"], "none")


class Accepted(Base):
    def test_every_accepted_query_is_the_flags_the_reference_translates_it_to(self):
        for query, flags, source in query_cases.ACCEPTED:
            with self.subTest(query=query):
                operand = [source[1]] if source and source[0] == "file" else []
                for fmt in ("csv", "json"):
                    a = self.flags(*flags, "--format", fmt, *operand)
                    b = self.q(query, "--format", fmt)
                    self.assertEqual(comparable(a), comparable(b), (query, flags))

    def test_the_worked_examples(self):
        for query, flags, src, code in query_cases.EXAMPLES:
            if src is None:
                continue
            with self.subTest(query=query):
                for fmt in ("csv", "json"):
                    a = self.flags(*shlex.split(flags), "--format", fmt, src)
                    b = self.q(query, "--format", fmt)
                    self.assertEqual(comparable(a), comparable(b))
                    if fmt == "csv":
                        self.assertEqual(a.status, code)

    def test_stdin_is_a_source(self):
        data = query_cases.FIXTURES["orders.csv"].encode()
        a = self.flags("--agg", "count", "--where", "status = 200", "--format", "csv", stdin=data)
        b = self.q("select count(*) from stdin where status = 200", "--format", "csv", stdin=data)
        self.assertEqual((a.status, a.stdout, a.stderr), (b.status, b.stdout, b.stderr))
        self.assertEqual(b.stdout, b"count\n3\n")
        # no from at all and no operand: standard input as well
        c = self.q("select count(*)", "--format", "csv", stdin=data)
        self.assertEqual(c.stdout, b"count\n5\n")


# ---- Q1-LIMITS, Q1-CONFLICT, Q1-KEYWORDS -----------------------------------------------------------------------------------------------

class Limits(Base):
    def padded(self, query, size):
        return query + " " * (size - len(query))

    def test_4096_bytes_are_accepted_and_4097_are_not(self):
        base = "select a from 'f.csv'"
        ok = self.q(self.padded(base, 4096), "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n1\n2\n"), ok)
        over = self.q(self.padded(base, 4097), "--format", "csv")
        self.assertEqual((over.status, over.stdout), (2, b""))
        self.assertEqual(rule_of(over), "query.too-long")
        # a multi-byte character counts its bytes: 2,072 characters, 4,112 bytes
        wide = "select a from 'f.csv' where b = '%s'" % ("é" * 2040)
        self.assertLess(len(wide), 4096)
        self.assertGreater(len(wide.encode()), 4096)
        self.assertEqual(rule_of(self.q(wide)), "query.too-long")
        fits = "select a from 'f.csv' where b = '%s'" % ("é" * 2030)
        self.assertLessEqual(len(fits.encode()), 4096)
        ok = self.q(fits, "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n"), ok)

    def conds(self, n):
        return "select a from 'f.csv' where " + " and ".join("b != 'q%d'" % i for i in range(n))

    def test_64_conditions_are_accepted_and_65_are_not(self):
        ok = self.q(self.conds(64), "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n1\n2\n"), ok)
        q65 = self.conds(65)
        r = self.q(q65)
        self.assertEqual((r.status, rule_of(r)), (2, "query.syntax"))
        detail = doc(r)["error"]["detail"]
        self.assertEqual(detail["offset"], q65.index("b != 'q64'"))
        self.assertEqual(detail["expected"], query_ref.WHAT_CEILING)

    def values(self, n):
        return "select a from 'f.csv' where b in (" + ",".join(str(i) for i in range(n)) + ")"

    def test_1024_values_are_accepted_and_1025_are_not(self):
        ok = self.q(self.values(1024), "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n"), ok)
        q = self.values(1025)
        r = self.q(q)
        self.assertEqual((r.status, rule_of(r)), (2, "query.syntax"))
        self.assertEqual(doc(r)["error"]["detail"]["offset"], q.rindex("1024"))
        self.assertEqual(doc(r)["error"]["detail"]["expected"], query_ref.WHAT_CEILING)

    def test_the_ceilings_are_the_references(self):
        for q in (self.conds(64), self.conds(65), self.values(1024), self.values(1025)):
            try:
                query_ref.translate(q)
                ref = None
            except query_ref.QueryError as e:
                ref = (e.kind, e.offset)
            r = self.q(q)
            got = (rule_of(r)[6:], doc(r)["error"]["detail"]["offset"]) if rule_of(r) and rule_of(r).startswith("query.") else None
            self.assertEqual(got, ref)

    def test_a_scale_and_a_page_past_their_digits(self):
        self.assertEqual(rule_of(self.q("select a from 'f.csv' limit 1234567890123456789")), "query.syntax")
        self.assertEqual(rule_of(self.q("select mean(a, 19) from 'f.csv'")), "query.syntax")


class Conflicts(Base):
    PLAN_FLAGS = [["--select", "a"], ["--where", "a = 1"], ["--group", "a"], ["--agg", "count"], ["--sort", "count"], ["--order-by", "a"], ["--rows"], ["--report", "types"]]

    def conflict(self, r, *flags):
        self.assertEqual((r.status, rule_of(r)), (2, "args.conflict"), r)
        self.assertEqual(validate(r), [], r)
        listed = doc(r)["error"]["detail"]["flags"].split()
        for f in flags:
            self.assertIn(f, listed)
        self.assertEqual(listed[0], "--query")

    def test_each_plan_flag_conflicts_with_a_query(self):
        for flag in self.PLAN_FLAGS:
            with self.subTest(flag=flag):
                self.conflict(self.q("select a from 'f.csv'", *flag), flag[0])

    def test_page_flags(self):
        # the page of the flags and the page of the query are one thing, said once
        self.conflict(self.q("select a from 'f.csv' limit 1", "--limit", "2"), "--limit")
        self.conflict(self.q("select a from 'f.csv' offset 1", "--from", "1"), "--from")
        self.conflict(self.q("select a from 'f.csv' limit 1", "--top", "2"), "--top")
        # a flag the query has no form for, or has not used, is the flag's own
        ok = self.q("select a from 'f.csv'", "--limit", "1", "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n1\n"), ok)
        ok = self.q("select a from 'f.csv' limit 1", "--from", "1", "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n2\n"), ok)
        top = self.q("select a, b from 'f.csv' order by a desc", "--top", "1", "--format", "csv")
        self.assertEqual((top.status, top.stdout), (0, b"a,b\n2,y\n"), top)

    def test_from_and_an_operand(self):
        r = self.q("select a from 'f.csv'", "f.csv")
        self.conflict(r)
        r = self.q("select a from stdin", "f.csv")
        self.conflict(r)

    def test_a_query_with_an_operand_and_no_from(self):
        r = self.q("select a", "--format", "csv", "f.csv")
        self.assertEqual((r.status, r.stdout), (0, b"a\n1\n2\n"), r)

    def test_the_flag_itself(self):
        self.assertEqual(rule_of(run("--query", "", "f.csv", cwd=self.cwd)), "args.bad-value")
        self.assertEqual(rule_of(run("--query", "select a", "--query", "select b", "f.csv", cwd=self.cwd)), "args.duplicate-flag")
        self.assertEqual(rule_of(run("--query", cwd=self.cwd)), "args.missing-value")
        r = run("--query=select a from 'f.csv'", "--format", "csv", cwd=self.cwd)
        self.assertEqual((r.status, r.stdout), (0, b"a\n1\n2\n"), r)

    def test_the_other_flags_still_apply(self):
        for flags in (["--format", "text"], ["--delimiter", "z"], ["--max-rows", "0"]):
            with self.subTest(flags=flags):
                a = self.flags("--select", "a", *flags, "f.csv")
                b = self.q("select a from 'f.csv'", *flags)
                self.assertEqual(comparable(a), comparable(b))


class Rows(Base):
    def test_rows_is_every_row_and_column(self):
        for fmt in ("json", "csv"):
            a = self.flags("--rows", "--format", fmt, "f.csv")
            b = self.q("select * from 'f.csv'", "--format", fmt)
            self.assertEqual((a.status, a.stdout), (0, b.stdout), a)
            self.assertEqual(a.stdout, b.stdout)
        csv = self.flags("--rows", "--format", "csv", "f.csv")
        self.assertEqual(csv.stdout, b"a,b,c\n1,x,1.5\n2,y,2.5\n")
        self.assertEqual(self.flags("--rows", "--limit", "1", "--format", "csv", "f.csv").stdout, b"a,b,c\n1,x,1.5\n")

    def test_rows_with_a_plan_flag(self):
        for flag in (["--select", "a"], ["--where", "a = 1"], ["--group", "a"], ["--agg", "count"], ["--order-by", "a"], ["--report", "types"]):
            with self.subTest(flag=flag):
                r = self.flags("--rows", *flag, "f.csv")
                self.assertEqual((r.status, rule_of(r)), (2, "args.conflict"), r)
                self.assertIn("--rows", doc(r)["error"]["detail"]["flags"])

    def test_rows_is_a_flag_of_the_surface(self):
        names = {f["name"] for f in introspect()["flags"]}
        self.assertIn("--rows", names)
        self.assertIn("--query", names)

    def test_rows_of_a_header_only_file(self):
        self.s.write("h.csv", "a,b\n")
        r = self.flags("--rows", "--format", "csv", "h.csv")
        self.assertEqual((r.status, r.stdout), (0, b"a,b\n"), r)


KEYWORDS = sorted(query_ref.RESERVED | query_ref.FUTURE)
WORDS_THAT_STAY_COLUMNS = sorted(set(query_ref.AGG_FUNCS) | set(query_ref.TYPES) | set(query_ref.SOURCE_WORDS))


class Keywords(Base):
    def table(self, words):
        header = ",".join('"%s"' % w for w in words)
        self.s.write("k.csv", header + "\n" + ",".join(str(i + 1) for i in range(len(words))) + "\n")

    def test_every_reserved_word_is_refused_bare_and_accepted_quoted(self):
        self.table(KEYWORDS)
        for w in KEYWORDS:
            with self.subTest(word=w):
                bare = self.q("select %s from 'k.csv'" % w)
                self.assertEqual((bare.status, rule_of(bare)), (2, "query.syntax"), (w, bare))
                self.assertEqual(doc(bare)["error"]["detail"]["offset"], 7)
                for case in (w.upper(), w.capitalize()):
                    r = self.q("select %s from 'k.csv'" % case)
                    self.assertEqual(rule_of(r), "query.syntax", case)
                quoted = self.q('select "%s" from \'k.csv\'' % w, "--format", "csv")
                flags = self.flags("--select", w, "--format", "csv", "k.csv")
                self.assertEqual((quoted.status, quoted.stdout), (0, flags.stdout), quoted)
                self.assertEqual(quoted.stdout, ('%s\n%d\n' % (w, KEYWORDS.index(w) + 1)).encode())
                # in every clause
                where = self.q('select * from \'k.csv\' where "%s" = %d order by "%s" group by' % (w, KEYWORDS.index(w) + 1, w), "--format", "csv")
                self.assertEqual(rule_of(where), "query.syntax")
                ok = self.q('select "%s", count(*) from \'k.csv\' where "%s" >= 1 group by "%s" order by "%s" desc' % (w, w, w, w), "--format", "csv")
                self.assertEqual((ok.status, ok.stdout), (0, ('%s,count\n%d,1\n' % (w, KEYWORDS.index(w) + 1)).encode()), ok)

    def test_a_function_or_a_type_is_a_word_only_where_it_means_one(self):
        self.table(WORDS_THAT_STAY_COLUMNS)
        for w in WORDS_THAT_STAY_COLUMNS:
            with self.subTest(word=w):
                r = self.q("select %s from 'k.csv' where %s >= 1 order by %s" % (w, w, w), "--format", "csv")
                self.assertEqual((r.status, r.stdout), (0, ("%s\n%d\n" % (w, WORDS_THAT_STAY_COLUMNS.index(w) + 1)).encode()), r)

    def test_a_position_is_not_a_header(self):
        self.s.write("p.csv", "#2,x\n1,2\n")
        by_position = self.q("select #2 from 'p.csv'", "--format", "csv")
        by_name = self.q('select "#2" from \'p.csv\'', "--format", "csv")
        self.assertEqual(by_position.stdout, b"x\n2\n")
        self.assertEqual(by_name.stdout, b"#2\n1\n")


# ---- Q1-DOC ------------------------------------------------------------------------------------------------------------------------

class Documents(Base):
    """What the documents say a query does, run: the table of section 4 of docs/query.md (the query and its flags give the same bytes), and every `--query` in the examples of the
    README, the pages, the refusals page and the help and usage of the tool is accepted by the front end or refused with a rule of the front end."""

    def test_the_table_of_worked_examples_of_the_design(self):
        text = (ROOT / "docs" / "query.md").read_text()
        rows = re.findall(r"^\| *(\d+) *\| `(.+?)` \| `(.+?)`(?: *\(.*\))? *\|$", text.split("## 4. Worked examples", 1)[1].split("What the flag forms print", 1)[0], re.M)
        self.assertGreaterEqual(len(rows), 15)   # the sixteenth is a refusal, shown in words
        for n, query, flags in rows:
            with self.subTest(n=n):
                argv = shlex.split(flags.replace("(standard input, R1)", "").replace("(**the new flag**)", "").strip())
                operand = [argv.pop()] if argv and argv[-1].endswith(".csv") else []
                stdin = query_cases.FIXTURES["orders.csv"].encode()
                a = self.flags(*argv, *operand, "--format", "csv", stdin=stdin)
                b = self.q(query, "--format", "csv", stdin=stdin)
                self.assertEqual((a.status, a.stdout, a.stderr), (b.status, b.stdout, b.stderr), (query, flags))

    def examples_in(self, path):
        found = []
        for line in (ROOT / path).read_text().splitlines():
            line = re.sub(r"<[^>]+>", "", line).replace("&gt;", ">").replace("&lt;", "<").replace("&quot;", '"').replace("&amp;", "&")
            m = re.match(r"\$ table (.*--query .*)$", line.strip())
            if m:
                found.append(m.group(1))
        return found

    def check_example(self, command):
        argv = shlex.split(command)
        # a query with a from names its file; one without is given the sales file of the skill, as the examples of the help are
        operand = [] if re.search(r"from ", command) else ["sales.csv"]
        r = run("--root", self.cwd, *argv, *operand, cwd=self.cwd)
        self.assertEqual(validate(r) if r.stdout.startswith(b"{") else [], [], (command, r))
        rule = rule_of(r)
        self.assertTrue(r.status == 0 or (rule or "").startswith("query."), (command, r))

    def test_the_examples_of_the_readme_the_pages_and_the_refusals(self):
        n = 0
        for path in ("README.md", "docs/index.html", "docs/refusals.md", "docs/mcp.md"):
            for command in self.examples_in(path):
                with self.subTest(path=path, command=command):
                    self.check_example(command)
                    n += 1
        self.assertGreaterEqual(n, 2)

    def test_the_examples_of_the_help_and_the_usage(self):
        d = introspect()
        helps = {f["name"]: f["help"] for f in d["flags"]}
        shown = helps["--query"].split("Examples: ", 1)[1]
        found = re.findall(r'--query "([^"]*)"', shown)
        self.assertGreaterEqual(len(found), 2)
        for text in found:
            self.check_example('--query "%s"' % text)
        for command in re.findall(r"^\$ table (.*--query .*)$", d["usage"], re.M):
            self.check_example(command)

    def test_the_help_says_what_the_grammar_is(self):
        help_text = {f["name"]: f["help"] for f in introspect()["flags"]}["--query"]
        for word in ("select", "from", "where", "group by", "order by", "limit", "offset", "count(*)", "sum", "min", "max", "mean", "count(distinct", "contains", "cast(", "::", "int", "dec(N)", "float", "#3",
                     "4096", "query.unsupported", "query.syntax", "--select", "--where", "--group", "--agg", "--sort", "--order-by", "--rows", "--report", "--top"):
            self.assertIn(word, help_text)
        self.assertNotIn(";", help_text)
        self.assertNotIn("|", help_text)


# ---- the source --------------------------------------------------------------------------------------------------------------------

class Source(Base):
    def test_from_is_the_operand_with_the_same_confinement(self):
        os.makedirs(self.cwd / "sub")
        self.s.write("sub/g.csv", "a\n7\n")
        ok = self.q("select a from 'sub/g.csv'", "--root", self.cwd, "--format", "csv")
        self.assertEqual((ok.status, ok.stdout), (0, b"a\n7\n"), ok)
        again = self.q("select a from './sub//g.csv'", "--root", self.cwd, "--format", "csv")
        self.assertEqual(again.stdout, b"a\n7\n")
        for path in ("../x.csv", "sub/../g.csv", "/etc/passwd", "", "nope.csv"):
            with self.subTest(path=path):
                a = run("--root", self.cwd, "--select", "a", "--format", "csv", path, cwd=self.cwd)
                b = self.q("select a from '%s'" % path, "--root", self.cwd, "--format", "csv")
                self.assertEqual((a.status, rule_of(a)), (b.status, rule_of(b)), (path, a, b))
                self.assertNotEqual(b.status, 0)

    def test_a_symbolic_link_is_refused_as_an_operand_is(self):
        outside = tempfile.mkdtemp(prefix="table-out-")
        self.addCleanup(shutil.rmtree, outside, True)
        pathlib.Path(outside, "secret.csv").write_text("a\n9\n")
        os.symlink(os.path.join(outside, "secret.csv"), self.cwd / "link.csv")
        a = run("--root", self.cwd, "--select", "a", "--format", "csv", "link.csv", cwd=self.cwd)
        b = self.q("select a from 'link.csv'", "--root", self.cwd, "--format", "csv")
        self.assertEqual((a.status, rule_of(a)), (b.status, rule_of(b)))
        self.assertEqual(rule_of(b), "path.symlink")

    def test_a_doubled_quote_in_the_path_is_one_quote(self):
        self.s.write("it's.csv", "a\n42\n")
        r = self.q("select a from 'it''s.csv'", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (0, b"a\n42\n"), r)
        r = self.q("select a from 'it''''s.csv'", "--format", "csv")
        self.assertEqual(rule_of(r), "io.not-found")

    def test_the_path_opens_in_every_thread(self):
        # the threads of --threads open the file again by its whole path: a source that is only right for the first open is found out here
        rows = "".join("%d,%s,%d.5\n" % (i, "x" if i % 3 else "y", i) for i in range(400))
        self.s.write("many.csv", "a,b,c\n" + rows)
        for query, flags in (("select a, b from 'many.csv' where c::float > 100", ["--select", "a,b", "--where", "c:float > 100"]),
                             ("select b, count(*), sum(a) from 'many.csv' group by b", ["--group", "b", "--agg", "count,sum:a"])):
            for threads in (["--threads", "1"], ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "64"]):
                for root in ([], ["--root", self.cwd]):
                    with self.subTest(query=query, threads=threads, root=bool(root)):
                        a = self.flags(*root, *flags, *threads, "--format", "csv", "many.csv")
                        b = self.q(query, *root, *threads, "--format", "csv")
                        self.assertEqual((a.status, a.stdout, a.stderr), (b.status, b.stdout, b.stderr))
                        self.assertEqual(b.status, 0, b)

    def test_the_path_is_data_and_never_a_flag(self):
        # a path that looks like a flag, or like a second statement, is a path
        self.s.write("--select", "a\n5\n")
        r = self.q("select a from '--select'", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (0, b"a\n5\n"), r)
        r = self.q("select a from 'f.csv --where x'", "--format", "csv")
        self.assertEqual(rule_of(r), "io.not-found")


# ---- the repairs -------------------------------------------------------------------------------------------------------------------

class Repairs(Base):
    def options(self, r):
        self.assertEqual(r.status, 2, r)
        repair = doc(r)["error"]["repair"]
        self.assertEqual(repair["kind"], "choose", doc(r)["error"])
        return [o["argv"][1:] for o in repair["options"]]

    def test_a_keyword_typo(self):
        for query, fixed in (("selct a from 'f.csv'", ["--select", "a", "f.csv"]),
                             ("select a form 'f.csv'", ["--select", "a", "f.csv"]),
                             ("select a from 'f.csv' wher b = 'x'", ["--select", "a", "--where", "b = 'x'", "f.csv"]),
                             ("select a from 'f.csv' limt 1", ["--select", "a", "--limit", "1", "f.csv"]),
                             ("select a from 'f.csv' ordr by a", ["--select", "a", "--order-by", "a", "f.csv"])):
            with self.subTest(query=query):
                self.assertEqual(self.options(self.q(query)), [fixed])

    def test_the_options_run(self):
        r = self.q("select a form 'f.csv'", "--format", "csv")
        option = doc(run("--query", "select a form 'f.csv'", cwd=self.cwd))["error"]["repair"]["options"][0]["argv"]
        again = run(*option[1:], "--format", "csv", cwd=self.cwd)
        self.assertEqual((again.status, again.stdout), (0, b"a\n1\n2\n"), again)

    def test_the_rest_of_the_invocation_is_kept_in_place(self):
        r = run("--format", "csv", "--query", "selct a from 'f.csv'", "--threads", "1", cwd=self.cwd)
        self.assertEqual(r.status, 2)
        argv = doc(r)["error"]["repair"]["options"][0]["argv"] if doc(r) else None
        # csv has no document: the repair is for json
        r = run("--root", self.cwd, "--query", "selct a from 'f.csv'", "--threads", "1", cwd=self.cwd)
        argv = doc(r)["error"]["repair"]["options"][0]["argv"]
        self.assertEqual(argv[1:], ["--root", str(self.cwd), "--select", "a", "--threads", "1", "f.csv"])

    def test_type_names(self):
        for query, fixed in (("select a from 'f.csv' where a::integer = 1", ["--select", "a", "--where", "a:int = 1", "f.csv"]),
                             ("select a from 'f.csv' where c::decimal(2) = 1.5", ["--select", "a", "--where", "c:dec(2) = 1.5", "f.csv"]),
                             ("select a from 'f.csv' where c::double > 1", ["--select", "a", "--where", "c:float > 1", "f.csv"]),
                             ("select a from 'f.csv' where cast(a as integer) = 1", ["--select", "a", "--where", "a:int = 1", "f.csv"])):
            with self.subTest(query=query):
                self.assertEqual(self.options(self.q(query)), [fixed])

    def test_a_double_equals_and_a_double_quoted_value(self):
        self.assertEqual(self.options(self.q("select a from 'f.csv' where b == 'x'")), [["--select", "a", "--where", "b = 'x'", "f.csv"]])
        r = self.q('select a from \'f.csv\' where b = "x"')
        self.assertEqual(rule_of(r), "query.unsupported")
        self.assertEqual(self.options(r), [["--select", "a", "--where", "b = 'x'", "f.csv"]])

    def test_a_typo_of_three_edits_is_not_a_repair(self):
        r = self.q("select a from 'f.csv' ordering by a")
        self.assertEqual((rule_of(r), doc(r)["error"]["repair"]["kind"]), ("query.syntax", "none"))
        r = self.q("select a from 'f.csv' orderr by a")
        self.assertEqual(self.options(r), [["--select", "a", "--order-by", "a", "f.csv"]])

    def test_a_repair_that_would_give_the_call_two_files_is_not_offered(self):
        # the corrected query has a from, and the call has an operand already: running it would be an args.conflict
        r = run("--query", "select a form 'f.csv'", "f.csv", cwd=self.cwd)
        self.assertEqual((rule_of(r), doc(r)["error"]["repair"]["kind"]), ("query.syntax", "none"))
        # a from-less correction takes the operand it was given
        r = run("--query", "selct a", "f.csv", cwd=self.cwd)
        self.assertEqual([o["argv"][1:] for o in doc(r)["error"]["repair"]["options"]], [["--select", "a", "f.csv"]])

    def test_only_a_pattern_that_is_contains_is_repaired(self):
        for pattern in ("%a%b%", "%x_y%", "%xy", "x%", "%", "%%", "_x_"):
            with self.subTest(pattern=pattern):
                r = self.q("select a from 'f.csv' where b like '%s'" % pattern)
                self.assertEqual((rule_of(r), doc(r)["error"]["repair"]["kind"]), ("query.unsupported", "none"), pattern)
        r = self.q("select a from 'f.csv' where b like '%it''s%'")
        self.assertEqual(self.options(r), [["--select", "a", "--where", "b contains 'it''s'", "f.csv"]])

    def test_like(self):
        r = self.q("select a from 'f.csv' where b like '%x%'")
        self.assertEqual(self.options(r), [["--select", "a", "--where", "b contains 'x'", "f.csv"]])
        # a pattern that is not "contains" has none
        r = self.q("select a from 'f.csv' where b like 'x%'")
        self.assertEqual((rule_of(r), doc(r)["error"]["repair"]["kind"]), ("query.unsupported", "none"))

    def test_group_mismatch(self):
        r = self.q("select a from 'f.csv' group by a")
        self.assertEqual(self.options(r), [["--group", "a", "--agg", "count", "f.csv"]])
        r = self.q("select a, b from 'f.csv' group by a")
        self.assertEqual(self.options(r), [["--group", "a", "--agg", "count", "f.csv"]])
        r = self.q("select count(*), a from 'f.csv' group by a")
        self.assertEqual(self.options(r), [["--group", "a", "--agg", "count", "f.csv"]])
        r = self.q("select a, count(*) from 'f.csv' group by a order by sum(b)")
        self.assertEqual(self.options(r), [["--group", "a", "--agg", "count,sum:b", "--sort", "sum:b", "f.csv"]])

    def test_no_repair_says_why(self):
        for query in ("select a from", "select a, count(*) from 'f.csv' where b = 'x' or c = 'y' and d = 1", "select a,", "select a from 'f.csv' limit -1", "select a, count(*) from 'f.csv'"):
            with self.subTest(query=query):
                e = doc(self.q(query))["error"]
                self.assertEqual(e["repair"]["kind"], "none", e)
                self.assertGreater(len(e["repair"]["reason"]), 10)


# ---- Q1-ORACLE / Q1-FUZZ -------------------------------------------------------------------------------------------------------------

SEEDS = [q for q, *_ in query_cases.ACCEPTED] + [q for q, *_ in query_cases.REFUSALS] + [q for q, *_ in query_cases.EXAMPLES] + [
    "select a, count(*), sum(b::int), mean(c::dec(2), 3) from 'f.csv' where a in (1, 2, 3) and b contains 'x' group by a order by sum(b::int) desc limit 5 offset 2",
    'select "x y", #2 from \'f.csv\' where "a ""b""" != \'it\'\'s\' and #3::float >= -1.5e3 order by #1::int desc, "x y"',
]
PIECES = ["select", "from", "where", "group", "by", "order", "limit", "offset", "and", "in", "contains", "asc", "desc", "as", "cast", "count", "sum", "min", "max", "mean", "distinct",
          "int", "float", "dec", "(", ")", ",", "*", "=", "!=", "<>", "<", "<=", ">", ">=", "::", ";", "'x'", "'", '"', '"y"', "#", "#2", "1", "-1", "1.5", "1e3", "a", "b", "c", "stdin", "'f.csv'",
          "or", "like", "null", "join", "having", "union", "--", "/*", "+", "||", "é", "\\", "\t", "\n", "  "]


def mutate(rnd, q):
    q = bytearray(q.encode("utf-8"))
    for _ in range(rnd.choice([1, 1, 1, 1, 2, 3])):
        kind = rnd.randrange(8)
        at = rnd.randrange(len(q) + 1) if q else 0
        if kind == 0 and q:
            del q[min(at, len(q) - 1)]
        elif kind == 1:
            q[at:at] = bytes([rnd.randrange(1, 256)])
        elif kind == 2 and q:
            q[min(at, len(q) - 1)] = rnd.randrange(1, 256)
        elif kind == 3:
            q[at:at] = (" " + rnd.choice(PIECES) + " ").encode()
        elif kind == 4 and q:
            end = min(len(q), at + rnd.randrange(1, 12))
            del q[at:end]
        elif kind == 5 and q:
            end = min(len(q), at + rnd.randrange(1, 20))
            q[at:at] = q[at:end]
        elif kind == 6:
            q = q[:at]
        elif kind == 7 and q:
            i = rnd.randrange(len(q))
            q[i] = ord(rnd.choice("'\"(),=<>!*#:;-+ "))
    return bytes(q).replace(b"\x00", b"") or b"select"


def soup(rnd):
    return " ".join(rnd.choice(PIECES) for _ in range(rnd.randrange(1, 14))).encode()


def raw(rnd):
    return bytes(rnd.randrange(1, 256) for _ in range(rnd.randrange(1, 60)))


class Fuzz(Base):
    def test_random_and_mutated_queries(self):
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        rnd = random.Random(int(os.environ.get("TABLE_QUERY_SEED", "5")))
        cases = []
        names, kinds = ["a", "b", "c"], ["int", "text", "dec"]
        for i in range(FUZZ):
            k = i % 6
            if k < 2:
                cases.append(mutate(rnd, rnd.choice(SEEDS)))
            elif k == 2:
                cases.append(soup(rnd))
            elif k == 3:
                cases.append(raw(rnd))
            else:
                # a query the generator of the equivalence gate wrote (it is valid, whatever the table), as it is or with one mutation
                plan = query_gen.make_plan(rnd, names, kinds, [["1", "x", "1.5"]])
                text = query_gen.query_form(rnd, plan, "'f.csv'" if rnd.random() < 0.5 else None)
                cases.append(text.encode() if k == 4 else mutate(rnd, text))
        results = {}

        # the binary is given the bytes as the operating system holds them
        def runbytes(b):
            import procs as subprocess
            p = subprocess.run([binary(), "--query", b, "--format", "json", "f.csv"], capture_output=True, cwd=self.cwd, input=b"", timeout=60)
            return p.returncode, p.stdout, p.stderr

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            outs = list(pool.map(runbytes, cases))
        counts = {"accepted": 0, "refused-by-query": 0, "refused-by-engine": 0}
        for b, (status, out, err) in zip(cases, outs):
            with self.subTest(query=b):
                self.assertNotIn(status, TRAPS, (b, out, err))
                self.assertGreaterEqual(status, 0)
                self.assertEqual(err, b"", (b, err))
                lines = out.decode("utf-8", "replace").splitlines()
                self.assertEqual(len(lines), 1, (b, out[:200]))
                d = json.loads(lines[0])
                text = b.decode("latin-1")
                try:
                    flags, source = query_ref.translate(text)
                    ref = None
                except query_ref.QueryError as e:
                    flags, source, ref = None, None, e
                if d.get("ok"):
                    counts["accepted"] += 1
                    self.assertEqual(status, 0)
                    self.assertIsNone(ref, ("the reference refuses what the tool answers", b, ref and (ref.kind, ref.offset)))
                    continue
                e = d["error"]
                self.assertIn(e["rule"], rules, (b, e["rule"]))
                self.assertTrue(e["repair"] is None or e["repair"]["kind"] in ("none", "choose", "retry"), e)
                if e["rule"].startswith("query."):
                    counts["refused-by-query"] += 1
                    self.assertEqual(status, 2, (b, e["rule"]))
                    kind = e["rule"][6:]
                    if kind == "mixed-and-or":
                        kind = "mixed"
                    if kind == "too-long":
                        self.assertGreater(len(b), 4096)
                        continue
                    self.assertTrue(0 <= e["detail"]["offset"] <= len(b), (b, e["detail"]))
                    if ref is None and e["rule"] == "query.syntax" and "column" in e["detail"]:
                        continue            # a literal that does not fit its column: the engine's literal checks, not the grammar's
                    self.assertIsNotNone(ref, ("the tool refuses what the reference accepts", b, e["rule"], e["detail"]))
                    self.assertEqual((kind, e["detail"]["offset"]), (ref.kind, ref.offset), (b, e["detail"]))
                    self.assertEqual(e["detail"].get("expected", e["detail"].get("feature")), ref.expected if ref.kind != "unsupported" else ref.feature, b)
                else:
                    counts["refused-by-engine"] += 1
                    self.assertIsNone(ref, ("the reference refuses what the engine saw", b, ref and (ref.kind, ref.offset), e["rule"]))
        if os.environ.get("TABLE_QUERY_REPORT"):
            print("\nQ1-FUZZ: %d strings, %s" % (FUZZ, json.dumps(counts, sort_keys=True)))
        # a fuzz that reached only one kind of answer has fuzzed one path
        self.assertGreater(counts["refused-by-query"], FUZZ // 10, counts)
        self.assertGreater(counts["accepted"] + counts["refused-by-engine"], FUZZ // 12, counts)


if __name__ == "__main__":
    unittest.main()
