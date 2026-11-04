"""docs/column-comparison.md: `COLUMN OP ( COLUMN )` in --where, a condition between two columns of the row. The gates of section 8 that the flag form holds, against a Python reference that shares no
code with the tool:

* G-ORACLE: for random files and random `a OP (b)` of each type (text, :int, :dec(2), :float, :date, :timestamp, :datetime), the rows kept are the rows a Python filter keeps that reads the same
  cells; quoted cells are unquoted on both sides;
* G-REFUSAL: a cell that is not of the type is the refusal of its type whichever side it is on, the left read first; an empty cell is refused; the other side never changes the rule;
* G-PAR: `--threads` and tiny chunks give the bytes of one thread, refusals included;
* G-COMPAT: no expression that was valid is read differently: a bare value that starts with `@` or `(`-less is still a literal, and a `(` where a value goes was a refusal before (the binary
  the pull request started from is not needed: the old refusal is `where.syntax` and so is every malformed use of the new form);
* the combinations: with `and`, `or`, `not (`, a join (the expression runs on the joined row), standard input, --group and --agg, --explain, --join-where (refused).
"""

import csv
import io
import random
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

import not_a2 as N
import not_a2_cases as K
import or_a0
import test_or_a1_compat as C1
from harness import Scratch, binary, run, run_argv

CASES = 400
OPS = ("=", "!=", "<", "<=", ">", ">=")


def holds(op, c):
    return {"=": c == 0, "!=": c != 0, "<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[op]


def cmp(a, b):
    return (a > b) - (a < b)


def cells(kind, rng):
    """Two cell texts of the kind: values from a small pool so that ties are common, spelled in more than one way where the type allows."""
    if kind == "text":
        pool = ["a", "b", "ab", "B", "", "é", "10", "9"]
        return rng.choice(pool), rng.choice(pool)
    if kind == "int":
        pool = ["0", "-0", "7", "-7", "007", "+7", "9223372036854775807", "-9223372036854775808", "12"]
        return rng.choice(pool), rng.choice(pool)
    if kind == "dec":
        pool = ["1", "1.5", "1.50", "-1.5", "0.01", "2", "-0.00", "10.25"]
        return rng.choice(pool), rng.choice(pool)
    if kind == "float":
        pool = ["1", "1.5", "1.50", "-1.5", "1e2", "100", "0", "-0", "2.5e-3"]
        return rng.choice(pool), rng.choice(pool)
    if kind == "date":
        pool = ["2026-01-02", "2026-01-03", "2025-12-31", "2026-02-28"]
        return rng.choice(pool), rng.choice(pool)
    if kind == "timestamp":
        pool = ["2026-01-02T00:00:00Z", "2026-01-02T01:00:00+01:00", "2026-01-02T00:00:01Z"]
        return rng.choice(pool), rng.choice(pool)
    pool = ["2026-01-02 12:00:00", "2026-01-02 12:00:01", "2025-12-31 23:59:59"]
    return rng.choice(pool), rng.choice(pool)


def key(kind, text):
    """The value the tool means by the cell, written again here from the documents (numbers.md, datetime.md)."""
    if kind == "text":
        return text.encode("utf-8")
    if kind == "int":
        return int(text)
    if kind == "dec":
        return Decimal(text)
    if kind == "float":
        return float(text)
    if kind == "date":
        return date.fromisoformat(text).toordinal()
    if kind == "timestamp":
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.fromisoformat(text)


SUFFIX = {"text": "", "int": ":int", "dec": ":dec(2)", "float": ":float", "date": ":date", "timestamp": ":timestamp", "datetime": ":datetime"}
KINDS = list(SUFFIX)


def csv_bytes(rows, header):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("utf-8")


class ColumnComparison(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def tool(self, name, *flags, threads=1, chunk=64):
        extra = [] if threads == 1 else ["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0]
        return run_argv([binary(), "--root", self.s.dir, *[str(f) for f in flags], *[str(e) for e in extra], name])

    def ids(self, r):
        self.assertEqual(r.status, 0, r)
        return [int(x[0]) for x in r.doc()["data"]["rows"]]

    def test_oracle_every_type_and_operator(self):
        rng = random.Random(20260211)
        for kind in KINDS:
            rows = []
            for i in range(CASES):
                a, b = cells(kind, rng)
                rows.append((str(i), a, b))
            self.s.write("t.csv", csv_bytes(rows, ["id", "a", "b"]))
            for op in OPS:
                where = "a%s %s (b%s)" % (SUFFIX[kind], op, SUFFIX[kind])
                r = self.tool("t.csv", "--where", where, "--select", "id", "--limit", 1000)
                want = [i for i, a, b in ((int(r_[0]), r_[1], r_[2]) for r_ in rows) if holds(op, cmp(key(kind, a), key(kind, b)))]
                if kind == "text":
                    want = [i for i, a, b in ((int(r_[0]), r_[1], r_[2]) for r_ in rows) if holds(op, cmp(a.encode(), b.encode()))]
                got = self.ids(r) if r.status == 0 else None
                self.assertEqual(got, want, (kind, op, r))

    def test_threads_give_the_bytes_of_one_thread(self):
        rng = random.Random(7)
        rows = [(str(i),) + cells("int", rng) for i in range(300)]
        self.s.write("t.csv", csv_bytes(rows, ["id", "a", "b"]))
        for where in ("a:int > (b:int)", "a:int = (b:int) or id:int > 290", "not (a:int <= (b:int)) and id:int < 250"):
            one = self.tool("t.csv", "--where", where, "--select", "id", "--limit", 1000)
            for threads in (2, 4, 16):
                for chunk in (1, 7, 64):
                    many = self.tool("t.csv", "--where", where, "--select", "id", "--limit", 1000, threads=threads, chunk=chunk)
                    self.assertEqual(many.status, one.status, (where, threads, chunk))
                    self.assertEqual(many.stdout.replace(b'"--threads","%d","--chunk-bytes","%d","--parallel-min-bytes","0",' % (threads, chunk), b""), one.stdout.replace(b'"--threads","%d","--chunk-bytes","%d","--parallel-min-bytes","0",' % (threads, chunk), b""), (where, threads, chunk))

    def test_a_bad_cell_is_refused_on_either_side(self):
        for left, right, side in (("x", "1", "a"), ("1", "x", "b"), ("", "1", "a"), ("1", "", "b")):
            self.s.write("t.csv", csv_bytes([("1", left, right)], ["id", "a", "b"]))
            r = self.tool("t.csv", "--where", "a:int > (b:int)")
            self.assertEqual(r.status, 8, r)
            self.assertEqual(r.first_rule(), "value.not-integer", r)
            self.assertEqual(r.error()["detail"]["column"], side, r)
        # the other types name their own rule, whichever side holds the cell
        for kind, rule in (("float", "value.not-float"), ("dec(2)", "value.not-decimal"), ("date", "value.not-date")):
            for left, right, side in (("x", "1", "a"), ("1", "x", "b")):
                if kind == "date":
                    left, right = ("x", "2026-01-02") if side == "a" else ("2026-01-02", "x")
                self.s.write("t.csv", csv_bytes([("1", left, right)], ["id", "a", "b"]))
                r = self.tool("t.csv", "--where", "a:%s > (b:%s)" % (kind, kind))
                self.assertEqual(r.first_rule(), rule, (kind, side, r))
                self.assertEqual(r.error()["detail"]["column"], side, (kind, side, r))
        # the left cell is read first: both bad, the left is the one named
        self.s.write("t.csv", csv_bytes([("1", "x", "y")], ["id", "a", "b"]))
        r = self.tool("t.csv", "--where", "a:int > (b:int)")
        self.assertEqual(r.status, 8, r)
        self.assertEqual(r.error()["detail"]["column"], "a", r)

    def test_a_row_decided_before_the_comparison_never_reads_its_cells(self):
        self.s.write("t.csv", csv_bytes([("1", "x", "y"), ("2", "3", "2")], ["id", "a", "b"]))
        r = self.tool("t.csv", "--where", "id:int > 1 and a:int > (b:int)", "--select", "id")
        self.assertEqual(self.ids(r), [2], r)

    def test_quoted_cells_are_unquoted(self):
        self.s.write("t.csv", b'id,a,b\n1,"x,y","x,y"\n2,"a""b",a\n3,"p\nq","p\nq"\n4,"p""q","p""q"\n5,"p""q",p"q\n')
        r = self.tool("t.csv", "--where", "a = (b)", "--select", "id")
        self.assertEqual(self.ids(r), [1, 3, 4, 5], r)

    def test_a_column_against_itself_is_true_for_a_valid_cell(self):
        self.s.write("t.csv", csv_bytes([("1", "5", "x"), ("2", "6", "y")], ["id", "a", "b"]))
        self.assertEqual(self.ids(self.tool("t.csv", "--where", "a:int = (a:int)", "--select", "id")), [1, 2])

    def test_the_two_columns_must_be_written_with_the_same_type(self):
        self.s.write("t.csv", csv_bytes([("1", "5", "5")], ["id", "a", "b"]))
        for where in ("a:int > (b)", "a > (b:int)", "a:int > (b:dec(2))", "a:dec(2) > (b:dec(3))"):
            r = self.tool("t.csv", "--where", where)
            self.assertEqual(r.first_rule(), "where.syntax", (where, r))
            self.assertIn("same type", r.error()["detail"]["expected"], (where, r))

    def test_malformed_forms_are_where_syntax_and_trap_nowhere(self):
        self.s.write("t.csv", csv_bytes([("1", "5", "5")], ["id", "a", "b"]))
        for where in ("a > (", "a > ()", "a > (b", "a > (b c)", "a > ((b))", "a > (b) c", "a contains (b)", "a in (b) (c)", "a > (b) and", "a > (b:foo)", "a > (#9)"):
            r = self.tool("t.csv", "--where", where)
            self.assertNotEqual(r.status, 0, (where, r))
            self.assertIn(r.first_rule(), ("where.syntax", "column.unknown", "column.position"), (where, r))

    def test_a_value_that_starts_with_a_mark_is_still_a_literal(self):
        self.s.write("t.csv", b"id,a,b\n1,@x,@x\n2,(x,y\n3,x,x\n")
        self.assertEqual(self.ids(self.tool("t.csv", "--where", "a = @x", "--select", "id")), [1])
        self.assertEqual(self.ids(self.tool("t.csv", "--where", "a = '(x'", "--select", "id")), [2])
        self.assertEqual(self.ids(self.tool("t.csv", "--where", "a in (@x, x)", "--select", "id")), [1, 3])

    def test_with_not_or_groups_and_aggregates(self):
        self.s.write("t.csv", csv_bytes([("1", "5", "3"), ("2", "3", "3"), ("3", "1", "9"), ("4", "7", "7")], ["id", "a", "b"]))
        self.assertEqual(self.ids(self.tool("t.csv", "--where", "not (a:int > (b:int))", "--select", "id")), [2, 3, 4])
        self.assertEqual(self.ids(self.tool("t.csv", "--where", "a:int < (b:int) or id:int = 1", "--select", "id")), [1, 3])
        r = self.tool("t.csv", "--where", "a:int >= (b:int)", "--agg", "count,sum:a:int")
        self.assertEqual(r.status, 0, r)

    def test_a_join_runs_it_on_the_joined_row(self):
        self.s.write("today.csv", csv_bytes([("1", "5"), ("2", "3"), ("3", "9")], ["k", "bid"]))
        self.s.write("prev.csv", csv_bytes([("1", "4"), ("2", "3"), ("3", "10")], ["k", "t_bid"]))
        for op, want in ((">", [1]), ("<", [3]), ("=", [2])):
            r = run_argv([binary(), "--root", self.s.dir, "--join", "prev.csv", "--on", "k:int", "--where", "bid:int %s (t_bid:int)" % op, "--select", "k", "today.csv"])
            self.assertEqual([int(x[0]) for x in r.doc()["data"]["rows"]], want, (op, r))

    def test_a_join_does_not_push_it_to_a_side(self):
        # a text comparison leads the expression, which is where a condition on one file's column is applied before the join: here the two columns are on the two sides
        self.s.write("today.csv", csv_bytes([("1", "5"), ("2", "3"), ("3", "9")], ["k", "bid"]))
        self.s.write("prev.csv", csv_bytes([("1", "4"), ("2", "3"), ("3", "10")], ["k", "t_bid"]))
        for jt in ("inner", "left"):
            r = run_argv([binary(), "--root", self.s.dir, "--join", "prev.csv", "--on", "k", "--join-type", jt, "--where", "bid = (t_bid)", "--select", "k", "today.csv"])
            self.assertEqual([int(x[0]) for x in r.doc()["data"]["rows"]], [2], (jt, r))

    def test_join_where_refuses_it(self):
        self.s.write("a.csv", csv_bytes([("1", "5", "5")], ["k", "x", "y"]))
        self.s.write("b.csv", csv_bytes([("1", "5", "5")], ["k", "p", "q"]))
        r = run_argv([binary(), "--root", self.s.dir, "--join", "b.csv", "--on", "k", "--join-where", "p:int > (q:int)", "a.csv"])
        self.assertEqual(r.first_rule(), "where.syntax", r)

    def test_stdin_and_explain(self):
        r = run("--where", "a:int > (b:int)", "--format", "csv", "-", stdin=b"a,b\n3,2\n1,2\n")
        self.assertEqual(r.stdout, b"a,b\n3,2\n", r)
        self.s.write("t.csv", csv_bytes([("1", "5", "3")], ["id", "a", "b"]))
        r = self.tool("t.csv", "--where", "a:int > (b:int)", "--explain")
        self.assertEqual(r.status, 0, r)
        w = r.doc()["data"]["explain"]["plan"]["where"][0]
        self.assertEqual(w["against"]["name"], "b", r)
        self.assertEqual(w["op"], ">", r)


if __name__ == "__main__":
    unittest.main()


class Corpus(unittest.TestCase):
    """G-COMPAT over the corpus of the earlier slices (not_a2_cases.py, 5,674 cases): the cases the earlier compat tests divert here (a `(` where a value goes) are answered as the reference of this
    slice reads them; every other case is held to the bytes of main by test_not_a2_compat.py and test_or_a1_compat.py, whose reference for A2 is the same parser with the column form off."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", or_a0.DATA)
        cls.cases = {or_a0.case_id(kind, argv): (kind, argv) for kind, argv in K.cases() if C1.expr_c0(argv)}

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_the_diverted_cases_are_read_as_the_reference_reads_them(self):
        self.assertGreater(len(self.cases), 20)
        for cid, (kind, argv) in self.cases.items():
            expr = C1.expression_of(argv)
            args = [a.replace("{DIR}", str(self.s.dir)) for a in argv]
            doc = None if kind in ("csv", "jsonl", "query-csv") else C1.as_doc(run(*args))
            want = N.parse_result(expr[1].encode("utf-8").decode("latin-1"))
            want = want if want[0] != "ok" else ("ok",)
            got = C1.tool_where(expr[1], doc)
            if got is not None:
                self.assertEqual(got, want, (cid, expr))
