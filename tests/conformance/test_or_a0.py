"""Slice A0 of docs/or-and-columns.md: `a = 1 or a = 2` is refused as it has always been, and the refusal offers `a in (1, 2)` where that is the same question.

The gates of the document that apply to a front-end repair, written before the repair (they fail on `main`, the repair ones, and pass on it, the golden one):

* G-COMPAT: every refusal of the rules `where.*` and `query.*` that this suite can reach (or_a0.py: 2,800 of them, the hand lists, the pinned refusals of query_cases.py and
  seeded soups) answers the same bytes as `main` did, but for the `repair` of the cases the document says are repaired, and for those it is exactly that repair that differs;
* the repair is offered only where it is equivalent: same column written the same, same type suffix, `=` and `in` only, a chain of `or` and nothing else, within the 1,024
  values of an `in`, and the corrected expression parses; mixed columns, mixed types, `!=`, `contains`, `and` beside the `or`, parentheses and a literal the type does not take
  are refused as today with no repair;
* the repaired command, when run, answers the rows that SQLite gives for the disjunction (the oracle judges truth values only: docs/or-and-columns.md section 9) on generated
  tables, for text, `:int`, `:dec(2)` and `:float` columns, in the flag form and in the query form;
* a reference of the rule in Python (or_a0.py, no code shared with the tool) agrees with the tool on generated expressions and queries;
* fuzz: no random string traps, every refusal is a rule `introspect` lists, every repair that is offered is a command that parses and is accepted.
"""

import json
import random
import re
import sqlite3
import unittest
from decimal import Decimal

import or_a0
import query_ref
from harness import Scratch, binary, introspect, run, run_argv, validate

TRAPS = {132, 134, 136, 139, -4, -6, -8, -11}
NONE_REASON = "what the expression was meant to say is not known"
QUERY_NONE_REASON = "the feature has no spelling in this language; the hint says what to write instead"


def doc_of(r):
    return json.loads(r.stdout.decode("utf-8").splitlines()[0])


def repair_of(r):
    d = doc_of(r)
    return d["error"]["repair"] if not d.get("ok") else None


def is_or_refusal(r):
    """The refusal of an `or` in a filter: `where.syntax` at an `or`, or `query.unsupported` for the feature `or` (what slice A0 repairs and nothing else)."""
    d = doc_of(r)
    if d.get("ok"):
        return False
    e = d["error"]
    if e["rule"] == "where.syntax":
        return e["detail"]["expression"][e["detail"]["offset"]:e["detail"]["offset"] + 2] == "or" and e["detail"]["expected"].startswith("and, or the end")
    return e["rule"] == "query.unsupported" and e["detail"]["feature"] == "or"


def options_of(r):
    rep = repair_of(r)
    return [o["argv"] for o in rep["options"]] if rep and rep["kind"] == "choose" else []


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", or_a0.DATA)
        cls.dir = str(cls.s.dir)

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def where(self, expr, *more):
        return run("--root", self.dir, "--where", expr, "--select", "a", *more, "t.csv")

    def query(self, q, *more):
        return run("--root", self.dir, "--query", q, *more)


class Golden(Base):
    """G-COMPAT / G-OFFSETS: the refusals of today are the refusals of the branch."""

    def test_every_refusal_of_main_is_the_same_bytes_but_for_the_repair_that_is_new(self):
        golden = json.loads(or_a0.GOLDEN.read_text())["cases"]
        cases = or_a0.golden_cases()
        now = or_a0.refusals(run, self.dir)
        self.assertEqual(sorted(now), sorted(golden), "the set of refusals moved")
        expected = set()
        for kind, argv in cases:
            text = None
            for k, a in enumerate(argv):
                if a == "--where":
                    text = or_a0.reference_repair(argv[k + 1])
                elif a.startswith("--where="):
                    text = or_a0.reference_repair(a[len("--where="):])
                elif a == "--query":
                    text = or_a0.reference_query_repair(argv[k + 1])
                elif a.startswith("--query="):
                    text = or_a0.reference_query_repair(a[len("--query="):])
            if text is not None:
                expected.add(or_a0.case_id(kind, argv))
        # a refusal written as a sentence on standard error (--format csv, --format jsonl) has no repair to show: those stay the same bytes whatever the repair
        expected = {cid for cid in expected if golden.get(cid, [0, 0, 0, 0, None])[4] == "none"}
        changed = set()
        for cid, (status, rule, sha, blank, repair) in now.items():
            g_status, g_rule, g_sha, g_blank, g_repair = golden[cid]
            self.assertEqual((status, rule, blank), (g_status, g_rule, g_blank), "%s: the refusal is not main's apart from its repair" % cid)
            if sha != g_sha:
                changed.add(cid)
                self.assertEqual((g_repair, repair), ("none", "choose"), cid)
            else:
                self.assertEqual(repair, g_repair, cid)
        self.assertEqual(changed, expected & set(golden), "the cases whose repair changed are not the cases the document repairs")
        self.assertGreater(len(changed), 200)
        self.assertGreater(len(golden), 2500)


class FlagForm(Base):
    def test_the_repair_of_each_shape(self):
        for expr, fixed in or_a0.REPAIRS:
            with self.subTest(expr):
                r = self.where(expr)
                self.assertEqual(validate(r), [], expr)
                self.assertEqual((r.status, r.first_rule()), (2, "where.syntax"), (expr, r))
                self.assertEqual(options_of(r), [[binary(), "--root", self.dir, "--where", fixed, "--select", "a", "t.csv"]], (expr, doc_of(r)["error"]["repair"]))
                self.assertEqual(or_a0.reference_repair(expr), fixed)

    def test_no_repair_where_it_is_not_the_same_question(self):
        for expr in or_a0.NO_REPAIR:
            with self.subTest(expr):
                r = self.where(expr)
                self.assertEqual(validate(r), [], expr)
                self.assertEqual(r.status, 2, (expr, r))
                self.assertEqual(r.first_rule(), "where.syntax", (expr, r))
                self.assertEqual(repair_of(r), {"kind": "none", "reason": NONE_REASON}, expr)

    def test_the_ceiling_of_an_in_list(self):
        for kind, n in or_a0.CEILING_OFFERED:
            expr, fixed = or_a0.ceiling_expr(kind, n)
            with self.subTest((kind, n)):
                r = self.where(expr)
                self.assertEqual(options_of(r), [[binary(), "--root", self.dir, "--where", fixed, "--select", "a", "t.csv"]])
                self.assertEqual(run_argv(options_of(r)[0]).status, 0)
        for kind, n in or_a0.CEILING_REFUSED:
            expr, _ = or_a0.ceiling_expr(kind, n)
            with self.subTest((kind, n)):
                r = self.where(expr)
                self.assertEqual((r.first_rule(), repair_of(r)), ("where.syntax", {"kind": "none", "reason": NONE_REASON}))

    def test_the_offset_is_the_or_as_before(self):
        for expr, fixed in or_a0.REPAIRS:
            d = self.where(expr).error()["detail"]
            self.assertEqual(expr[d["offset"]:d["offset"] + 2], "or", expr)
            self.assertEqual((d["expected"], d["expression"]), ("and, or the end of the expression", expr))

    def test_the_equals_form_keeps_its_flag(self):
        r = run("--root", self.dir, "--where=a = 1 or a = 2", "--select", "a", "t.csv")
        self.assertEqual(options_of(r), [[binary(), "--root", self.dir, "--where=a in (1, 2)", "--select", "a", "t.csv"]])
        self.assertEqual(run_argv(options_of(r)[0]).status, 0)

    def test_the_repair_is_the_whole_invocation(self):
        # every other flag and the operand stay, in their places, whatever the form
        for flags in (["--format", "csv"], ["--limit", "1", "--from", "1"], ["--threads", "2"], ["--explain"], ["--order-by", "-a:int"]):
            with self.subTest(flags):
                argv = ["--root", self.dir, *flags, "--where", "a = 1 or a = 2", "--select", "b", "t.csv"]
                r = run(*argv)
                if "--format" in flags:
                    self.assertEqual(r.status, 2)
                    continue
                want = [binary(), *argv]
                want[want.index("a = 1 or a = 2")] = "a in (1, 2)"
                self.assertEqual(options_of(r), [want])
                self.assertEqual(run_argv(want).status, 0)

    def test_explain_is_the_same_refusal_with_the_same_repair(self):
        # docs/or-and-columns.md: A0 changes nothing in the document `--explain` prints (its note, "the conditions at 0 and 1 could be an in", is A1's); the refusal is one
        # refusal, so `--explain` of an `or` is refused as the run is, and the repair it offers is the same command with `--explain` still in it
        r = run("--root", self.dir, "--explain", "--where", "a = 1 or a = 2", "t.csv")
        self.assertEqual((r.status, r.first_rule()), (2, "where.syntax"))
        self.assertEqual(options_of(r), [[binary(), "--root", self.dir, "--explain", "--where", "a in (1, 2)", "t.csv"]])
        ok = run_argv(options_of(r)[0])
        self.assertEqual(ok.status, 0)
        self.assertIn("explain", ok.data())
        # and the document of a conjunction is what it was: it has no `flow`, no `if_true`
        plain = run("--root", self.dir, "--explain", "--where", "a in (1, 2)", "t.csv")
        self.assertNotIn(b"if_true", plain.stdout)
        self.assertNotIn(b"flow", plain.stdout)

    def test_the_rule_says_it_sometimes_repairs(self):
        rule = [x for x in introspect()["rules"] if x["rule"] == "where.syntax"][0]
        self.assertEqual((rule["exit"], rule["repairable"]), (2, "sometimes"))
        self.assertIn("Repair: choose", rule["summary"])

    def test_the_reference_agrees_with_the_tool_on_soups(self):
        rng = random.Random(7)
        offered = 0
        for _ in range(700):
            expr = or_a0.where_soup(rng) if rng.random() < 0.4 else or_a0.where_chain_soup(rng)
            r = self.where(expr)
            self.assertNotIn(r.status, TRAPS)
            want = or_a0.reference_repair(expr)
            got = options_of(r)
            if want is None:
                self.assertEqual(got, [], expr)
            elif is_or_refusal(r):
                offered += 1
                self.assertEqual(got, [[binary(), "--root", self.dir, "--where", want, "--select", "a", "t.csv"]], expr)
            else:
                # the reference says repairable, the tool refused it first for another reason (a column that is not in the file): no repair is claimed
                self.assertEqual(got, [], expr)
                self.assertEqual(r.first_rule(), "column.unknown", (expr, r))
        self.assertGreater(offered, 20)


class QueryForm(Base):
    def test_the_repair_of_each_shape(self):
        for q, fixed in or_a0.QUERY_REPAIRS:
            with self.subTest(q):
                r = self.query(q)
                self.assertEqual(validate(r), [], q)
                self.assertEqual((r.status, r.first_rule()), (2, "query.unsupported"), (q, r))
                d = r.error()["detail"]
                self.assertEqual((d["feature"], q[d["offset"]:d["offset"] + 2].lower()), ("or", "or"), q)
                got = options_of(r)
                self.assertEqual(len(got), 1, (q, repair_of(r)))
                self.assertEqual(or_a0.reference_query_repair(q), fixed)
                # the offered command is what `--query FIXED` translates to: run both, the same bytes
                again = self.query(fixed)
                if "stdin" not in q:
                    self.assertEqual(again.status, 0, again)
                ran = run_argv(got[0])
                self.assertEqual((ran.status, ran.stdout), (again.status, again.stdout), q)
                # and the flags are the reference translator's, in the order of the tool's table, the source last
                flags, source = query_ref.translate(fixed)
                self.assertEqual(got[0][1:3], ["--root", self.dir])
                self.assertEqual(got[0][3:3 + len(flags)], flags, q)
                self.assertEqual(got[0][3 + len(flags):], [source[1]] if source[0] == "file" else [], q)

    def test_no_repair_where_it_is_not_the_same_question(self):
        for q in or_a0.QUERY_NO_REPAIR:
            with self.subTest(q):
                r = self.query(q)
                self.assertEqual(validate(r), [], q)
                self.assertEqual(r.status, 2, (q, r))
                self.assertIn(r.first_rule(), ("query.unsupported", "query.syntax", "query.group-mismatch"), q)
                if is_or_refusal(r):
                    self.assertEqual(repair_of(r), {"kind": "none", "reason": QUERY_NONE_REASON}, q)
                self.assertIsNone(or_a0.reference_query_repair(q), q)

    def test_the_ceiling_of_an_in_list(self):
        for kind, n in or_a0.CEILING_OFFERED:
            q, fixed = or_a0.query_ceiling(kind, n)
            with self.subTest((kind, n)):
                r = self.query(q)
                if len(q) > 4096:
                    self.assertEqual(r.first_rule(), "query.too-long")      # a query is at most 4,096 bytes: the ceiling that comes first
                    continue
                self.assertEqual(len(options_of(r)), 1, doc_of(r))
                self.assertEqual(run_argv(options_of(r)[0]).status, 0)
        for kind, n in or_a0.CEILING_REFUSED:
            q, _ = or_a0.query_ceiling(kind, n)
            with self.subTest((kind, n)):
                r = self.query(q)
                self.assertEqual(options_of(r), [])

    def test_other_flags_of_the_call_stay(self):
        q = "select a from 't.csv' where b = 'x' or b = 'y'"
        for more in (["--format", "csv"], ["--explain"], ["--limit", "1"], ["--threads", "2"]):
            r = self.query(q, *more)
            if "--format" in more:
                self.assertEqual(r.status, 2)
                continue
            got = options_of(r)
            self.assertEqual(len(got), 1, (more, doc_of(r)))
            for flag in more:
                self.assertIn(flag, got[0])
            self.assertEqual(run_argv(got[0]).status, 0)

    def test_the_equals_form_of_the_query_flag(self):
        r = run("--root", self.dir, "--query=select a from 't.csv' where b = 'x' or b = 'y'")
        got = options_of(r)
        self.assertEqual(len(got), 1)
        self.assertNotIn("--query", got[0])
        self.assertEqual(run_argv(got[0]).status, 0)

    def test_a_call_with_an_operand_has_no_repair_that_would_make_two(self):
        r = run("--root", self.dir, "--query", "select a from 't.csv' where b = 'x' or b = 'y'", "t.csv")
        self.assertEqual(options_of(r), [])
        r = run("--root", self.dir, "--query", "select a from stdin where b = 'x' or b = 'y'", "t.csv")
        self.assertNotEqual(r.first_rule(), None)

    def test_the_reference_agrees_with_the_tool_on_soups(self):
        rng = random.Random(11)
        offered = 0
        for _ in range(500):
            q = or_a0.query_soup(rng) if rng.random() < 0.4 else or_a0.query_chain_soup(rng)
            r = self.query(q)
            self.assertNotIn(r.status, TRAPS)
            want = or_a0.reference_query_repair(q)
            got = options_of(r)
            if is_or_refusal(r):
                self.assertEqual(len(got), 0 if want is None else 1, (q, want, got))
                if want is not None:
                    offered += 1
                    again = self.query(want)
                    self.assertEqual(run_argv(got[0]).stdout, again.stdout, q)
            elif want is not None:
                # the reference would repair it, the tool refused it first for another reason, earlier in the text than the `or`: then there is no `or` repair to compare
                self.assertTrue(r.status != 0 and r.first_rule() in ("query.syntax", "query.unsupported", "query.group-mismatch"), (q, r))
        self.assertGreater(offered, 10)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The oracle: SQLite judges which rows a disjunction keeps.

TEXTS = ["", "x", "y", "x y", "it's", "a,b", "X", "10", "9", "1", "é", "xx"]
INTS = ["0", "1", "2", "-1", "7", "3", "9223372036854775807", "-9223372036854775808", "99"]
DECS = ["1.50", "2", "-0.5", "0.25", "0", "1.5", "10.00", "-0.25", "100"]
FLOATS = ["1.5", "2e3", "0.1", "-2.25", "1e-3", "7", "100", "2000", "0.001", "3"]

LITS = {
    "t": ["x", "y", "'x y'", "''", "'it''s'", "'a,b'", "X", "10", "9", "1", "é", "'xx'", "'x'", "zz", "'1'"],
    "i": ["1", "2", "07", "+3", "-1", "7", "9223372036854775807", "-9223372036854775808", "99", "0", "-0", "'7'"],
    "d": ["1.50", "2", "1.5", ".5", "-0.5", "0.25", "0", "-0.25", "10", "100.00", "-.25"],
    "f": ["1.5", "2e3", "0.1", "-2.25", "7", "1E-3", "2000", "100", "3", "+3.0", "0.001"],
}
POOLS = {"t": TEXTS, "i": INTS, "d": DECS, "f": FLOATS}


def literal_value(col, lit):
    """What the literal compares as: the text, the integer, the integer scaled by 100, or the double."""
    if col == "t":
        return lit[1:-1].replace("''", "'") if lit.startswith("'") else lit
    if lit.startswith("'"):
        lit = lit[1:-1]
    if col == "i":
        return int(lit)
    if col == "d":
        return int(Decimal(lit) * 100)
    return float(lit)


def cell_value(col, cell):
    if col == "t":
        return cell
    if col == "i":
        return int(cell)
    if col == "d":
        return int(Decimal(cell) * 100)
    return float(cell)


def make_table(rng, rows=40):
    cells = []
    for k in range(rows):
        cells.append([str(k + 1)] + [rng.choice(POOLS[c]) for c in "tidf"])
    return cells


def csv_of(cells):
    def q(v):
        return '"%s"' % v.replace('"', '""') if any(c in v for c in ',"\n') else v
    return "id,t,i,d,f\n" + "".join(",".join(q(v) for v in row) + "\n" for row in cells)


def sqlite_of(cells):
    db = sqlite3.connect(":memory:")
    db.execute("create table t (id integer, t text collate binary, i integer, d integer, f real)")
    for row in cells:
        db.execute("insert into t values (?,?,?,?,?)", [int(row[0])] + [cell_value(c, v) for c, v in zip("tidf", row[1:])])
    return db


def disjunction(rng, col, leaves):
    """[(operator, [literals])]: the leaves of one disjunction on one column."""
    out = []
    for _ in range(leaves):
        if rng.random() < 0.55:
            out.append(("=", [rng.choice(LITS[col])]))
        else:
            out.append(("in", [rng.choice(LITS[col]) for _ in range(rng.randint(1, 3))]))
    return out


def sql_of(col, leaves):
    parts, args = [], []
    for op, lits in leaves:
        vals = [literal_value(col, v) for v in lits]
        if op == "=":
            parts.append("%s = ?" % col)
        else:
            parts.append("%s IN (%s)" % (col, ", ".join("?" * len(vals))))
        args += vals
    return "SELECT id FROM t WHERE " + " OR ".join(parts) + " ORDER BY id", args


FLAG_COL = {"t": "t", "i": "i:int", "d": "d:dec(2)", "f": "f:float"}
QUERY_COL = {"t": "t", "i": "i::int", "d": "d::dec(2)", "f": "f::float"}


def flag_text(col, leaves):
    return " or ".join("%s = %s" % (FLAG_COL[col], lits[0]) if op == "=" else "%s in (%s)" % (FLAG_COL[col], ", ".join(lits)) for op, lits in leaves)


def query_literal(col, lit):
    """A literal of the flag form as a query writes it: a bare word is a column there, so a text value that is not a number is quoted."""
    if col == "t" and not lit.startswith("'") and not re.fullmatch(r"[+-]?[0-9]+", lit):
        return "'%s'" % lit.replace("'", "''")
    return lit


def query_text(col, leaves):
    where = " or ".join("%s = %s" % (QUERY_COL[col], query_literal(col, lits[0])) if op == "=" else "%s in (%s)" % (QUERY_COL[col], ", ".join(query_literal(col, v) for v in lits))
                        for op, lits in leaves)
    return "select id from 't.csv' where " + where


class Oracle(Base):
    """G-ORACLE-SQL, for the repair: the repaired command keeps the rows SQLite keeps for the disjunction."""

    def check_tables(self, rng, n_tables, n_exprs, form):
        checked = 0
        for _ in range(n_tables):
            cells = make_table(rng)
            self.s.write("t.csv", csv_of(cells))
            db = sqlite_of(cells)
            for _ in range(n_exprs):
                col = rng.choice("tidf")
                leaves = disjunction(rng, col, rng.randint(2, 6))
                sql, args = sql_of(col, leaves)
                want = [[str(r[0])] for r in db.execute(sql, args)]
                if form == "flag":
                    text = flag_text(col, leaves)
                    r = run("--root", self.dir, "--where", text, "--select", "id", "--format", "json", "t.csv")
                else:
                    text = query_text(col, leaves)
                    r = self.query(text)
                self.assertEqual(r.status, 2, (text, r))
                got = options_of(r)
                self.assertEqual(len(got), 1, (text, doc_of(r)["error"]["repair"], r.first_rule()))
                ran = run_argv(got[0])
                self.assertEqual(ran.status, 0, (text, got, ran))
                self.assertEqual(validate(ran), [], text)
                self.assertEqual(ran.data()["rows"], want, (text, got))
                # the same, on several threads with tiny ranges: the repaired command is an ordinary command
                if checked % 7 == 0:
                    par = run_argv(got[0][:1] + ["--threads", "3", "--parallel-min-bytes", "0", "--chunk-bytes", "65536"] + got[0][1:])
                    self.assertEqual(par.stdout, ran.stdout, text)
                checked += 1
        return checked

    def test_flag_form(self):
        self.assertEqual(self.check_tables(random.Random(1), 12, 40, "flag"), 480)

    def test_query_form(self):
        self.assertEqual(self.check_tables(random.Random(2), 12, 40, "query"), 480)

    def test_the_literal_forms(self):
        # `a:int = 1 or a:int = 07` and its cousins, each with the rows SQLite keeps
        cells = [[str(k + 1), "x", c, d, f] for k, (c, d, f) in enumerate(zip(["1", "7", "-7", "0", "2", "9223372036854775807"], ["1.50", "2", "0.5", "-0.25", "10", "0"], ["1.5", "2000", "0.1", "-2.25", "7", "3"]))]
        self.s.write("t.csv", csv_of(cells))
        db = sqlite_of(cells)
        for expr, col, leaves in (
            ("i:int = 1 or i:int = 07", "i", [("=", ["1"]), ("=", ["07"])]),
            ("i:int = -7 or i:int = +2 or i:int = -0", "i", [("=", ["-7"]), ("=", ["+2"]), ("=", ["-0"])]),
            ("i:int in (9223372036854775807) or i:int = 1", "i", [("in", ["9223372036854775807"]), ("=", ["1"])]),
            ("d:dec(2) = 1.50 or d:dec(2) = 2", "d", [("=", ["1.50"]), ("=", ["2"])]),
            ("d:dec(2) = 1.5 or d:dec(2) = .5 or d:dec(2) = -.25", "d", [("=", ["1.5"]), ("=", [".5"]), ("=", ["-.25"])]),
            ("f:float = 1.5 or f:float = 2e3", "f", [("=", ["1.5"]), ("=", ["2e3"])]),
            ("f:float = 2000 or f:float = 0.10 or f:float = -2.25", "f", [("=", ["2000"]), ("=", ["0.10"]), ("=", ["-2.25"])]),
        ):
            with self.subTest(expr):
                sql, args = sql_of(col, leaves)
                want = [[str(r[0])] for r in db.execute(sql, args)]
                r = run("--root", self.dir, "--where", expr, "--select", "id", "t.csv")
                got = options_of(r)
                self.assertEqual(len(got), 1, doc_of(r))
                self.assertEqual(run_argv(got[0]).data()["rows"], want)
                self.assertTrue(want, "the case selects nothing, which proves nothing")

    def test_a_bad_cell_is_refused_by_the_repaired_command_as_the_first_leaf_would(self):
        # the document's equivalence for a reached bad cell: refused, the row and the column named (the refusal's repair text differs: it shows the expression)
        self.s.write("t.csv", "id,i\n1,1\n2,x\n3,2\n")
        r = run("--root", self.dir, "--where", "i:int = 1 or i:int = 2", "--select", "id", "t.csv")
        got = run_argv(options_of(r)[0])
        self.assertEqual((got.status, got.first_rule()), (8, "value.not-integer"))
        d = got.error()["detail"]
        self.assertEqual((d["row"], d["line"], d["column"], d["value"]), (2, 3, "i", "x"))
        self.s.write("t.csv", or_a0.DATA)

    def test_mixed_columns_are_not_merged(self):
        # the repair that merged `a = 1 or b = 2` into one `in` would answer the wrong rows; there is none, and nothing to run
        self.s.write("t.csv", "id,a,b\n1,1,0\n2,0,2\n3,0,0\n")
        r = run("--root", self.dir, "--where", "a = 1 or b = 2", "--select", "id", "t.csv")
        self.assertEqual((r.first_rule(), options_of(r)), ("where.syntax", []))
        self.s.write("t.csv", or_a0.DATA)


class Fuzz(Base):
    """G-FUZZ for the front end: no trap, every refusal tagged, every repair that is offered parses and is accepted."""

    def known(self):
        return {x["rule"] for x in introspect()["rules"]}

    def check(self, r, text):
        self.assertNotIn(r.status, TRAPS, (text, r))
        self.assertEqual(validate(r), [], (text, r))
        if r.status != 0:
            rule = r.first_rule()
            self.assertIn(rule, self.known(), (text, r))
            for argv in options_of(r):
                again = run_argv(argv)
                self.assertNotIn(again.status, TRAPS, (text, argv))
                self.assertEqual(validate(again), [], (text, argv))
                if is_or_refusal(r):
                    # the repair this slice adds parses and is accepted: it is not refused for its syntax, whatever else the file says about it
                    if again.status != 0:
                        self.assertNotIn(again.first_rule(), ("where.syntax", "query.syntax", "query.unsupported", "query.group-mismatch", "query.too-long"), (text, argv, again))

    def test_random_expressions(self):
        rng = random.Random(31)
        for _ in range(900):
            e = or_a0.where_soup(rng) if rng.random() < 0.5 else or_a0.where_chain_soup(rng)
            self.check(self.where(e), e)

    def test_random_queries(self):
        rng = random.Random(32)
        for _ in range(600):
            q = or_a0.query_soup(rng) if rng.random() < 0.5 else or_a0.query_chain_soup(rng)
            self.check(self.query(q), q)

    def test_random_bytes(self):
        rng = random.Random(33)
        alphabet = [chr(c) for c in range(1, 128)] + ["\udc80", "\udcff", "é", "or ", " or ", "a = 1 or a = 2", " in (", "'"]
        for _ in range(300):
            e = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            if rng.random() < 0.5:
                e = "a = 1 or " + e
            self.check(self.where(e), e)
            self.check(self.query("select a from 't.csv' where " + e), e)

    def test_every_offered_repair_is_a_fixed_point(self):
        # the corrected expression has no `or` left and is not refused again for the same reason: it is the expression the repair would offer again, none
        for expr, fixed in or_a0.REPAIRS:
            r = self.where(fixed)
            self.assertEqual(r.status, 0, (expr, fixed, r))
            self.assertEqual(options_of(r), [])


if __name__ == "__main__":
    unittest.main()
