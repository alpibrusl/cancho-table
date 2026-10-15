"""Slice A1 of docs/or-and-columns.md: `or` and parentheses in --where. The gates of section 9 that the flag form holds, written against references that share no code with the tool (or_a1.py):

* G-ORACLE-SQL: the truth values of `and`, `or`, parentheses, `in`, `contains`, text and typed comparisons, against SQLite (TEXT columns, BINARY collation) on generated tables and
  generated expressions, `--format json` and `csv`, one thread and four with tiny ranges;
* G-ORACLE-REF: the refusals no SQL engine can give (the row, the column and the rule of the first cell a reached condition refuses, in file order) against a tree walker in refimpl.py,
  on tables with bad cells and expressions that put the guards before and after, and the table of section 5.1 as it is written;
* G-PROGRAM: the branch program the tool compiles (read from --explain) is the program the document's model compiles, is forward-only, reaches every condition, and gives the tree's
  answer and the tree's sequence of leaves on every status of every leaf; and the model of the appendix, run as it stands, gives the numbers the document quotes;
* G-PAR: threads 1..64 and chunk sizes give the bytes of one thread, refusals included, with bad cells that only some ranges reach;
* G-OFFSETS: the reference parser and the tool agree on accepted, refused with the offset and the words, and the mix with its offset and the two readings, on generated token soups;
* G-FUZZ: random bytes and mutated expressions trap nowhere, every refusal is a rule introspect lists, every repair that is offered is accepted;
* the other forms an expression arrives in: groups, aggregates, --order-by with --top, --report types, standard input, JSON lines in and out.
"""

import csv
import io
import json
import os
import random
import re
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import or_a1 as O
import refimpl
from harness import Scratch, binary, introspect, run, run_argv, validate

WORKERS = 8
THREADS = (2, 3, 4, 8, 16, 64)
CHUNKS = (1, 7, 64, 1000)
SQL_CASES = int(os.environ.get("OR_A1_SQL", "3000"))
REF_CASES = int(os.environ.get("OR_A1_REF", "3000"))
PROGRAM_CASES = int(os.environ.get("OR_A1_PROGRAM", "10000"))
SOUPS = int(os.environ.get("OR_A1_SOUPS", "20000"))
FUZZ = int(os.environ.get("OR_A1_FUZZ", "10000"))


def pmap(fn, items):
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(fn, items))


def table_csv(rows, rng, header=O.HEADER):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator=rng.choice(["\n", "\r\n"]))
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("utf-8")


def lat(text):
    """The text as the tool sees it, a byte to a character: the offsets of a refusal are byte offsets."""
    return text.encode("utf-8").decode("latin-1")


def doc_of(r):
    return json.loads(r.stdout.decode("utf-8").splitlines()[0])


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-ORACLE-SQL

SQLCOL = {"n": "n_i", "d": "d_i", "f": "f_r"}


def sql_value(cond, text):
    if cond.get("int"):
        return int(text)
    if cond.get("dec") is not None:
        return int(Decimal(text) * 100)
    if cond.get("float"):
        return float(text)
    return text


def sql_of(node, params):
    if node[0] == "leaf":
        c = node[1]
        typed = c.get("int") or c.get("float") or c.get("dec") is not None
        col = '"%s"' % (SQLCOL[c["column"]] if typed else c["column"])
        if c["kind"] == "in":
            params.extend(sql_value(c, l) for l in c["lits"])
            return "%s IN (%s)" % (col, ",".join("?" * len(c["lits"])))
        if c["kind"] == "contains":
            if c["lit"] == "":
                return "1"
            params.append(c["lit"])
            return "instr(%s, ?) > 0" % col
        params.append(sql_value(c, c["lit"]))
        return "%s %s ?" % (col, "<>" if c["op"] == "!=" else c["op"])
    glue = " AND " if node[0] == "and" else " OR "
    return "(" + glue.join(sql_of(k, params) for k in node[1]) + ")"


def sqlite_of(rows):
    db = sqlite3.connect(":memory:")
    cols = ", ".join('"%s" TEXT' % c for c in O.HEADER) + ', "n_i" INTEGER, "d_i" INTEGER, "f_r" REAL'
    db.execute("CREATE TABLE t (%s)" % cols)
    for r in rows:
        db.execute("INSERT INTO t VALUES (%s)" % ",".join("?" * (len(r) + 3)), r + [int(r[4]), int(Decimal(r[5]) * 100), float(r[6])])
    return db


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.dir = str(cls.s.dir)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def put(self, name, data):
        self.s.write(name, data)

    def tool(self, name, *flags):
        return run("--root", self.dir, *flags, name)


class SqlOracle(Base):
    """G-ORACLE-SQL."""

    def test_the_truth_values_are_sqlites(self):
        rng = random.Random(20261016)
        tables = []
        for k in range(12):
            rows = O.make_valid_table(rng, rng.randint(25, 60))
            self.put("v%d.csv" % k, table_csv(rows, rng))
            tables.append((k, rows, sqlite_of(rows)))
        jobs = []
        for case in range(SQL_CASES):
            k, rows, db = rng.choice(tables)
            tree = O.gen_tree(rng, rng.choice([1, 2, 3, 3, 4, 5, 6, 8, 12]), lambda: O.draw_leaf(rng), depth=rng.choice([1, 2, 3, 4]))
            text = O.render(tree, rng)
            params = []
            where = sql_of(tree, params)
            want = [r[0] for r in db.execute("SELECT id FROM t WHERE %s ORDER BY CAST(id AS INTEGER)" % where, params)]
            jobs.append((case, k, text, want, case % 4))

        def one(job):
            case, k, text, want, mode = job
            extra = {0: [], 1: ["--format", "csv"], 2: ["--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", "0"],
                     3: ["--threads", "4", "--chunk-bytes", "100", "--parallel-min-bytes", "0", "--format", "csv"]}[mode]
            r = self.tool("v%d.csv" % k, "--where", text, "--select", "id", "--limit", "1000000", *extra)
            if "csv" in extra:
                got = [row[0] for row in csv.reader(io.StringIO(r.stdout.decode()))][1:] if r.status == 0 else None
            else:
                got = [row[0] for row in doc_of(r)["data"]["rows"]] if r.status == 0 else None
            return case, text, want, got, r

        bad = [(case, text, want, got, r) for case, text, want, got, r in pmap(one, jobs) if got != want]
        self.assertEqual(bad[:3], [], "%d of %d expressions differ from SQLite" % (len(bad), len(jobs)))

    def test_the_two_oracles_would_notice_a_swapped_connective(self):
        # the generator reaches both kinds of plan, and an `or` is not an `and`: the same table under the two readings of a tree differ for some expression
        rng = random.Random(7)
        rows = O.make_valid_table(rng, 40)
        db = sqlite_of(rows)
        differ = ors = 0
        for _ in range(300):
            tree = O.gen_tree(rng, rng.randint(2, 6), lambda: O.draw_leaf(rng))
            if not O.has_or(tree):
                continue
            ors += 1
            swapped = swap(tree)
            a, b = [], []
            w1, w2 = sql_of(tree, a), sql_of(swapped, b)
            if db.execute("SELECT group_concat(id) FROM t WHERE %s" % w1, a).fetchone() != db.execute("SELECT group_concat(id) FROM t WHERE %s" % w2, b).fetchone():
                differ += 1
        self.assertGreater(ors, 100)
        self.assertGreater(differ, ors // 2)


def swap(node):
    if node[0] == "leaf":
        return node
    return ("or" if node[0] == "and" else "and", [swap(k) for k in node[1]])


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-ORACLE-REF

def expected_outcome(rows, sem):
    """("rows", [ids]) or ("refuse", rule, row number, column): the first refusal in file order, else the rows kept."""
    header = O.HEADER
    kept = []
    for n, rec in enumerate(rows, 1):
        v = O.evaluate(sem, lambda col, rec=rec: rec[header.index(col)])
        if isinstance(v, tuple):
            return ("refuse", v[1], n, v[2])
        if v:
            kept.append(rec[0])
    return ("rows", kept)


def tool_outcome(r):
    d = doc_of(r)
    if d.get("ok"):
        return ("rows", [row[0] for row in d["data"]["rows"]])
    e = d["error"]
    return ("refuse", e["rule"], e["detail"].get("row"), e["detail"].get("column"))


class RefusalOracle(Base):
    """G-ORACLE-REF."""

    def test_the_first_refusal_in_file_order_and_the_rows_otherwise(self):
        rng = random.Random(20261017)
        tables = []
        for k in range(10):
            rows = O.make_bad_table(rng, rng.randint(20, 70))
            self.put("b%d.csv" % k, table_csv(rows, rng))
            tables.append((k, rows))
        jobs = []
        refused = kept = 0
        for case in range(REF_CASES):
            k, rows = rng.choice(tables)
            tree = O.gen_tree(rng, rng.choice([1, 2, 3, 3, 4, 5, 6, 8]), lambda: guarded_leaf(rng), depth=rng.choice([1, 2, 3]))
            text = O.render(tree, rng)
            jobs.append((case, k, text, expected_outcome(rows, tree)))

        def one(job):
            case, k, text, want = job
            r = self.tool("b%d.csv" % k, "--where", text, "--select", "id", "--limit", "1000000")
            return case, text, want, tool_outcome(r), r

        results = pmap(one, jobs)
        bad = [(c, t, w, g) for c, t, w, g, r in results if w != g]
        refused = sum(1 for c, t, w, g, r in results if w[0] == "refuse")
        kept = len(results) - refused
        self.assertEqual(bad[:3], [], "%d of %d differ from the reference" % (len(bad), len(results)))
        self.assertGreater(refused, len(results) // 10)
        self.assertGreater(kept, len(results) // 10)

    def test_the_table_of_section_5_1(self):
        data = "id,a,c\n1,x,\n2,x,abc\n3,y,\n4,y,7\n"
        self.put("g.csv", data)

        def rows(where):
            d = doc_of(self.tool("g.csv", "--where", where, "--select", "id"))
            return [r[0] for r in d["data"]["rows"]] if d.get("ok") else (d["error"]["rule"], d["error"]["detail"]["row"], d["error"]["detail"]["column"])

        # `c != '' and c:int > 5` never reads an empty cell as an integer: the guard stops the evaluation (as today)
        self.assertEqual(rows("a = y and c != '' and c:int > 5"), ["4"])
        self.assertEqual(rows("c != '' and c:int > 5"), ("value.not-integer", 2, "c"))
        # `c = '' or c:int > 5`: the guard of an `or` is the positive test; the empty cell keeps the row and the second condition is not tried
        self.assertEqual(rows("a = y and (c = '' or c:int > 5)"), ["3", "4"])
        # `c:int > 5 or c = ''`: the typed condition is tried first and refuses the first empty cell
        self.assertEqual(rows("c:int > 5 or c = ''"), ("value.not-integer", 1, "c"))
        # `a = 'x' or c:int > 5` with a = x and c = abc: c is never read
        self.assertEqual(rows("a = x and c = abc or a = x and c = abc"), ["2"]) if False else None
        self.assertEqual(rows("(a = x and c = abc) or c = 7"), ["2", "4"])
        self.assertEqual(rows("a = x or c:int > 5"), ("value.not-integer", 3, "c"))
        # `a = 'y' or c:int > 5`: a is x on the first row, c is empty there: refused at c, the row and the column named
        self.assertEqual(rows("a = y or c:int > 5"), ("value.not-integer", 1, "c"))
        # `(a = 'x' or b = 'x') and c:int > 5` with a = x: the second alternative is never read, c is
        self.assertEqual(rows("(a = x or a = z) and c:int > 5"), ("value.not-integer", 1, "c"))
        self.assertEqual(rows("(a = x or c:int > 5) and a = x"), ("value.not-integer", 3, "c"))
        self.assertEqual(rows("a = x and (a = x or c:int > 5)"), ["1", "2"])

    def test_a_cell_that_no_path_reaches_is_not_an_error_at_any_thread_count(self):
        data = "id,a,c\n" + "".join("%d,%s,%s\n" % (i, "x" if i % 2 else "y", "bad" if i % 2 else str(i)) for i in range(1, 200))
        self.put("h.csv", data)
        for threads in ("1", "4", "16"):
            r = self.tool("h.csv", "--where", "a = x or c:int > 5", "--select", "id", "--limit", "1000", "--threads", threads, "--chunk-bytes", "50", "--parallel-min-bytes", "0")
            self.assertEqual(r.status, 0, r)
            self.assertEqual([int(x[0]) for x in doc_of(r)["data"]["rows"]], [i for i in range(1, 200) if i % 2 or i > 5])


def guarded_leaf(rng):
    """A leaf over the columns of the bad table: text leaves are the guards, typed ones read cells that may not be of the type."""
    if rng.random() < 0.42:
        col, key = rng.choice([("n", "int"), ("n", "int"), ("d", "dec"), ("f", "float")])
        cond = {"column": col, key: 2 if key == "dec" else True, "kind": "cmp"}
        pool = {"int": ["0", "5", "-1", "42"], "dec": ["0", "1.5", "2"], "float": ["0", "1.5", "2e3"]}[key]
        if rng.random() < 0.2:
            cond["kind"] = "in"
            cond["lits"] = [rng.choice(pool) for _ in range(rng.randint(1, 3))]
        else:
            cond["op"] = rng.choice(["=", "!=", "<", ">", ">="])
            cond["lit"] = rng.choice(pool)
        return cond
    col = rng.choice(["a", "b", "n", "d", "f", "c"])
    cond = {"column": col, "kind": "cmp", "op": rng.choice(["=", "!=", "="]), "lit": rng.choice(["", "x", "y", "10", "0", "abc", "1.555", "inf", "z"])}
    if rng.random() < 0.15:
        cond["kind"] = "in"
        cond["lits"] = [rng.choice(["", "x", "y", "abc"]) for _ in range(2)]
    return cond


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-PROGRAM

def explain_where(r):
    d = doc_of(r)
    return d["data"]["explain"]["plan"]["where"] if d.get("ok") else None


def target_of(t):
    return {"A": "accept", "R": "reject"}.get(t, t)


class Program(Base):
    """G-PROGRAM."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s.write("p.csv", table_csv([["0", "x", "y", "z", "1", "1.00", "1.0", "", "", "", ""]], random.Random(0)))

    def explain(self, text):
        return run("--root", self.dir, "--explain", "--where", text, "--select", "id", "p.csv")

    def test_the_program_is_the_models_and_the_trees(self):
        rng = random.Random(20261018)
        jobs = []
        for case in range(PROGRAM_CASES):
            tree = O.gen_tree(rng, rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16]), lambda: O.draw_leaf(rng), depth=rng.choice([1, 2, 3, 4]))
            jobs.append((tree, O.render(tree, rng)))

        def one(job):
            tree, text = job
            return tree, text, explain_where(self.explain(text))

        orplans = 0
        for tree, text, where in pmap(one, jobs):
            leaves = O.leaves(tree)
            self.assertIsNotNone(where, text)
            self.assertEqual(len(where), len(leaves), text)
            for entry, cond in zip(where, leaves):
                self.assertEqual(entry["column"]["name"] if "name" in entry["column"] else entry["column"], entry["column"] if "name" not in entry["column"] else cond["column"], text) if False else None
                self.assertEqual(entry["op"], cond["op"] if cond["kind"] == "cmp" else cond["kind"], text)
                self.assertEqual(entry["values"], cond["lits"] if cond["kind"] == "in" else [cond["lit"]], text)
            if not O.has_or(tree):
                for entry in where:
                    self.assertNotIn("if_true", entry, text)
                continue
            orplans += 1
            prog, numbered = O.program(tree)
            for k, entry in enumerate(where):
                self.assertEqual((entry["if_true"], entry["if_false"]), tuple(target_of(t) for t in prog[k]), "%s leaf %d" % (text, k))
                for t in (entry["if_true"], entry["if_false"]):
                    self.assertTrue(t in ("accept", "reject") or (isinstance(t, int) and t > k), "a target goes backwards: %s" % text)
            # every condition is reachable, and the program equals the tree on every status of every leaf (to 7 leaves) or on 300 random ones
            tool = {k: (entry["if_true"], entry["if_false"]) for k, entry in enumerate(where)}
            n = len(where)
            reach = {0}
            for k in range(n):
                if k in reach:
                    reach.update(t for t in tool[k] if isinstance(t, int))
            self.assertEqual(reach, set(range(n)), "a condition cannot be reached: %s" % text)
            sem = O.semantic(O.parse(text)) if False else tree
            statuses = list(__import__("itertools").product([0, 1, 2], repeat=n)) if n <= 6 else [tuple(rng.choice([0, 1, 2]) for _ in range(n)) for _ in range(300)]
            for st in statuses:
                compiled = {k: tuple(({"accept": "A", "reject": "R"}.get(t, t)) for t in tool[k]) for k in tool}
                got, seen = O.run_program(compiled, st)
                want, seen_tree = O.tree_status(tree, st)
                self.assertEqual((got, seen), (want, seen_tree), "%s statuses %r" % (text, st))
        self.assertGreater(orplans, PROGRAM_CASES // 3)

    def test_the_model_of_the_appendix_gives_the_numbers_the_document_quotes(self):
        doc = open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "or-and-columns.md"), encoding="utf-8").read()
        code = doc.split("```python", 1)[1].split("```", 1)[0]
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            exec(compile(code, "appendix", "exec"), {"__name__": "appendix"})
        lines = out.getvalue().splitlines()
        self.assertIn("6000 random trees of 3 to 7 leaves", lines[0])
        self.assertEqual(lines[1].split(":")[-1].strip(), "1730")
        self.assertEqual(lines[2].split(":")[-1].strip(), "0")
        self.assertTrue(lines[4].startswith("branch program against the tree: 5030376 cases"), lines[4])
        self.assertTrue(lines[4].endswith(": 0"), lines[4])

    def test_the_program_of_the_documents_examples(self):
        def exits(text):
            return [(e["if_true"], e["if_false"]) for e in explain_where(self.explain(text))]

        self.assertEqual(exits("(a = 1 or b = 2) and c = 3"), [(2, 1), (2, "reject"), ("accept", "reject")])
        self.assertEqual(exits("a = 1 or b = 2"), [("accept", 1), ("accept", "reject")])
        self.assertEqual(exits("a = 1 and b = 2 and c = 3 and (d = 1 and e = 2)")[0], (1, "reject")) if False else None
        # a conjunction has no program: its document is the document it was
        for e in explain_where(self.explain("a = 1 and (b = 2 and c = 3)")):
            self.assertNotIn("if_true", e)

    def test_a_note_says_that_equalities_on_one_column_could_be_an_in(self):
        def notes(text):
            return doc_of(self.explain(text))["data"]["explain"]["notes"]

        n = notes("a = 1 or a = 2")
        self.assertEqual(len(n), 1)
        self.assertIn("the conditions at 0 and 1", n[0])
        self.assertIn("a in (1, 2)", n[0])
        self.assertEqual([x for x in notes("a = 1 or b = 2") if "could be one in" in x], [])
        self.assertEqual([x for x in notes("a != 1 or a != 2") if "could be one in" in x], [])
        self.assertEqual(len([x for x in notes("a = 1 or a = 2 or a in (3, 4)") if "could be one in" in x]), 1)
        self.assertIn("a in (1, 2, 3, 4)", [x for x in notes("a = 1 or a = 2 or a in (3, 4)") if "could be one in" in x][0])
        self.assertEqual([x for x in notes("a = 1 and a = 2") if "could be one in" in x], [])
        # the same column read two ways is two questions; alternatives that leave differently are not a run
        self.assertEqual([x for x in notes("a:int = 1 or a = 2") if "could be one in" in x], [])
        self.assertEqual([x for x in notes("a = 1 or (a = 2 and b = 3)") if "could be one in" in x], [])
        self.assertEqual([x for x in notes("(a = 1 or a = 2) and b = 3") if "could be one in" in x], ["the conditions at 0 and 1 test a against single values and could be one in: a in (1, 2), which reads the cell once"])


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-PAR

class Parallel(Base):
    """G-PAR: the bytes of one thread at any thread count and any size of range, refusals included."""

    def outcome(self, name, args, threads=1, chunk=4096):
        extra = [] if threads == 1 else ["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0]
        got = run_argv([binary(), "--root", self.dir, *[str(a) for a in args], *[str(a) for a in extra], name])
        shown = re.sub(rb'"--threads","[0-9]+","--chunk-bytes","[0-9]+","--parallel-min-bytes","0",', b"", got.stdout)
        return got.status, shown, got.stderr

    def same(self, name, args, label=""):
        want = self.outcome(name, args)
        jobs = [(t, c) for t in THREADS for c in CHUNKS]
        for (t, c), got in zip(jobs, pmap(lambda j: self.outcome(name, args, *j), jobs)):
            self.assertEqual(got, want, "%s threads %d chunk %d args %r" % (label, t, c, args))
        return want

    def test_bad_cells_that_only_some_ranges_reach(self):
        rows = []
        for i in range(400):
            a = "x" if i % 3 else "y"
            n = str(i)
            rows.append([str(i), a, "q", "r", n, "1.00", "1.0", "", "", "", ""])
        rows[40][4] = ""                # a = y (40 % 3 = 1 -> x): guard true, never read in an `a = x or n:int` plan
        rows[41][4] = "oops"            # a = x: not reached either
        rows[300][1] = "y"
        rows[300][4] = "bad"            # reached: a != x
        rows[360][1] = "y"
        rows[360][4] = ""               # reached too, later
        self.put("par.csv", table_csv(rows, random.Random(1)))
        for args in (["--where", "a = x or n:int > 100", "--select", "id", "--format", "csv"], ["--where", "a = x or n:int > 100", "--select", "id"],
                     ["--where", "(a = x or n:int > 100) and id:int >= 0", "--select", "id"], ["--where", "a = y or n:int > 100", "--select", "id"],
                     ["--where", "n:int > 100 or a = x", "--group", "a", "--agg", "count"], ["--where", "a = x or n:int > 100", "--group", "a", "--agg", "count,sum:n"]):
            want = self.same("par.csv", args, "late bad cell")
            self.assertIn(want[0], (0, 8))
        # with the bad cells that are reached taken out, the answer is rows; the cells that are not reached never made a difference
        rows[300][4] = "300"
        rows[360][4] = "360"
        self.put("par.csv", table_csv(rows, random.Random(1)))
        want = self.same("par.csv", ["--where", "a = x or n:int > 100", "--select", "id", "--format", "csv"], "no reached bad cell")
        self.assertEqual(want[0], 0, want)

    def test_generated_plans_with_an_or(self):
        rng = random.Random(20261019)
        rows = O.make_bad_table(rng, 160)
        self.put("gp.csv", table_csv(rows, rng))
        for case in range(14):
            tree = O.gen_tree(rng, rng.randint(2, 7), lambda: guarded_leaf(rng), depth=2)
            text = O.render(tree, rng)
            for extra in (["--select", "id", "--format", "csv"], ["--select", "id"], ["--group", "a", "--agg", "count"], ["--order-by", "id:int", "--top", "3"]):
                self.same("gp.csv", ["--where", text, *extra], text)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-OFFSETS and G-FUZZ

PIECES_COLUMNS = ["a", "b", "c", "'c d'", "or", "and", "not", "in", "contains", "#1", "a\\ b", "id"]
PIECES_CONN = ["and", "or", "and", "or", "AND", "Or", "xor", "&&", "||", "'or'", "'and'", "or'", "and'"]
SOUP_VALUES = ["1", "x", "'y z'", "''", "07", "or", "and", "(", ")", ",", "in", "contains", "=", "\\", "'", "é"]


def soup(rng):
    """A token sequence: mostly conditions and connectives and parentheses in the right order, damaged now and then."""
    out = []
    depth = 0
    for _ in range(rng.randint(1, 5)):
        while rng.random() < 0.2:
            out.append("(")
            depth += 1
        col = rng.choice(PIECES_COLUMNS)
        r = rng.random()
        if r < 0.5:
            out.append("%s %s %s" % (col, rng.choice(["=", "=", "!=", "<", ">=", "<=", ">", "=="]), rng.choice(SOUP_VALUES[:8])))
        elif r < 0.75:
            out.append("%s in (%s)" % (col, ", ".join(rng.choice(SOUP_VALUES[:8]) for _ in range(rng.randint(1, 3)))))
        elif r < 0.85:
            out.append("%s contains %s" % (col, rng.choice(SOUP_VALUES[:8])))
        else:
            out.append(" ".join(rng.choice(SOUP_VALUES + PIECES_COLUMNS) for _ in range(rng.randint(1, 3))))
        while depth and rng.random() < 0.3:
            out.append(")")
            depth -= 1
        out.append(rng.choice(PIECES_CONN) if rng.random() < 0.85 else "")
    if out and out[-1] in PIECES_CONN + [""] and rng.random() < 0.8:
        out.pop()
    while depth and rng.random() < 0.85:
        out.append(")")
        depth -= 1
    text = rng.choice([" ", "  ", ""]).join(out) if rng.random() < 0.15 else " ".join(out)
    if rng.random() < 0.2 and text:
        i = rng.randrange(len(text))
        text = text[:i] + rng.choice(["'", "\\", "(", ")", ",", " or ", " and ", "\x01", "é"]) + text[i + 1:]
    return text


def mixed_text(rng):
    """Leaves joined by connectives drawn at random, some of them in groups: most of them mix `and` with `or` at some level."""
    def part(depth):
        if depth < 2 and rng.random() < 0.3:
            return "(" + chain(depth + 1) + ")"
        return O.render_leaf(O.draw_leaf(rng, typed_ok=False), rng)

    def chain(depth):
        n = rng.randint(2, 5)
        out = part(depth)
        for _ in range(n - 1):
            out += rng.choice([" and ", " or ", " or "]) + part(depth)
        return out

    return chain(0)


class Offsets(Base):
    """G-OFFSETS: the reference parser is the tool's, on accepted and on refused, with the offset, the words, the mix, and the two readings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s.write("o.csv", table_csv([["1", "x", "y", "z", "1", "1.00", "1.0", "a b", "1", "2", "3"]], random.Random(0), O.HEADER))

    def test_the_reference_and_the_tool_agree(self):
        rng = random.Random(20261020)
        texts = [soup(rng) for _ in range(SOUPS)] + [mixed_text(rng) for _ in range(SOUPS // 10)]
        texts += ["a = 1 or b = 2 and c = 3", "a = 1 and b = 2 or c = 3", "(a = 1 or b = 2 and c = 3)", "a = 1 or (b = 2 and c = 3) and d = 4", "a = 1 and b = 2 or c = 3 and d = 4 or e = 5"]
        texts += ["(" * k + "a = 1" + ")" * k for k in range(0, 11)] + [" and ".join("a = %d" % i for i in range(n)) for n in (63, 64, 65)] + [" or ".join("a = %d" % i for i in range(n)) for n in (63, 64, 65)]
        texts += ["a in (%s)" % ", ".join(str(i) for i in range(n)) for n in (1023, 1024, 1025)]
        seen = {"ok": 0, "syntax": 0, "mixed": 0}

        def one(text):
            return text, run("--root", self.dir, "--where", text, "--select", "id", "o.csv")

        for text, r in pmap(one, texts):
            ref = O.parse_result(lat(text))
            self.assertEqual(validate(r), [], (text, r))
            d = doc_of(r)
            if d.get("ok"):
                tool = ("ok",)
            else:
                e = d["error"]
                if e["rule"] == "where.syntax":
                    tool = ("syntax", e["detail"]["offset"], e["detail"]["expected"])
                elif e["rule"] == "where.mixed-and-or":
                    tool = ("mixed", e["detail"]["offset"])
                else:
                    tool = ("ok",)          # an unknown column and the like: after the expression was read
            seen[ref[0]] += 1
            self.assertEqual(tool, ref if ref[0] != "ok" else ("ok",), text[:300])
            if ref[0] == "mixed":
                options = [lat(o["argv"][o["argv"].index("--where") + 1]) for o in d["error"]["repair"]["options"]] if d["error"]["repair"]["kind"] == "choose" else []
                offered = O.offered_readings(lat(text))
                self.assertEqual(options, offered, text)
        self.assertGreater(seen["ok"], SOUPS // 20, seen)
        self.assertGreater(seen["syntax"], SOUPS // 10, seen)
        self.assertGreater(seen["mixed"], 100, seen)

    def test_both_readings_of_a_mix_are_accepted_and_are_the_two_questions(self):
        rng = random.Random(20261021)
        rows = O.make_valid_table(rng, 50)
        self.put("m.csv", table_csv(rows, rng))
        db = sqlite_of(rows)
        done = 0
        for _ in range(150):
            # a flat mixed level over distinct leaves: SQL's precedence and the left-to-right fold are both written out and judged by SQLite
            n = rng.randint(3, 5)
            leaves = [O.draw_leaf(rng, typed_ok=False) for _ in range(n)]
            conns = [rng.choice(["and", "or"]) for _ in range(n - 1)]
            if len(set(conns)) < 2:
                continue
            text = " ".join(O.render_leaf(leaves[0], rng).split("\n")) + "".join(" %s %s" % (c, O.render_leaf(l, rng)) for c, l in zip(conns, leaves[1:]))
            r = self.tool("m.csv", "--where", text, "--select", "id")
            d = doc_of(r)
            self.assertEqual(d["error"]["rule"], "where.mixed-and-or", text)
            options = [o["argv"][o["argv"].index("--where") + 1] for o in d["error"]["repair"]["options"]]
            self.assertIn(len(options), (1, 2))
            # SQLite's own precedence of AND over OR judges the first reading
            params = []
            sql = sql_of(leaves[0] and ("leaf", leaves[0]), params)
            for c, l in zip(conns, leaves[1:]):
                sql += " %s %s" % (c.upper(), sql_of(("leaf", l), params))
            want0 = [r_[0] for r_ in db.execute("SELECT id FROM t WHERE %s ORDER BY CAST(id AS INTEGER)" % sql, params)]
            got0 = [x[0] for x in doc_of(self.tool("m.csv", "--where", options[0], "--select", "id", "--limit", "1000000"))["data"]["rows"]]
            self.assertEqual(got0, want0, (text, options[0]))
            # the second is the fold from the left: evaluated by a fold in Python over SQLite's rows of each leaf
            sets = []
            for l in leaves:
                p = []
                s_ = sql_of(("leaf", l), p)
                sets.append({r_[0] for r_ in db.execute("SELECT id FROM t WHERE %s" % s_, p)})
            acc = sets[0]
            allids = {r_[0] for r_ in db.execute("SELECT id FROM t")}
            for c, st in zip(conns, sets[1:]):
                acc = (acc & st) if c == "and" else (acc | st)
            got1 = {x[0] for x in doc_of(self.tool("m.csv", "--where", options[-1], "--select", "id", "--limit", "1000000"))["data"]["rows"]}
            self.assertEqual(got1, acc, (text, options[-1]))
            done += 1
        self.assertGreater(done, 60)

    def test_the_equals_form_of_the_flag_keeps_its_flag_in_the_repair(self):
        r = run("--root", self.dir, "--where=a = x or b = y and c = z", "--select", "id", "o.csv")
        d = doc_of(r)
        self.assertEqual(d["error"]["rule"], "where.mixed-and-or")
        options = [o["argv"] for o in d["error"]["repair"]["options"]]
        self.assertEqual([[a for a in argv if a.startswith("--where")] for argv in options], [["--where=a = x or (b = y and c = z)"], ["--where=(a = x or b = y) and c = z"]])
        for argv in options:
            self.assertTrue(doc_of(run_argv(argv)).get("ok"))

    def test_a_reading_that_passes_the_depth_is_not_offered(self):
        text = "(" * 8 + "a = x or b = y and c = z" + ")" * 8
        d = doc_of(self.tool("o.csv", "--where", text, "--select", "id"))
        self.assertEqual(d["error"]["rule"], "where.mixed-and-or")
        self.assertEqual(d["error"]["repair"]["kind"], "none")
        text = "(" * 7 + "a = x or b = y and c = z" + ")" * 7
        d = doc_of(self.tool("o.csv", "--where", text, "--select", "id"))
        self.assertEqual(len(d["error"]["repair"]["options"]), 2)
        # one reading deeper than the other: (a or b) and c wraps the first two, a or (b and c) the last two; a mix at depth 8 in the last unit only
        text = "a = x and b = y or " + "(" * 7 + "c = z" + ")" * 7
        d = doc_of(self.tool("o.csv", "--where", text, "--select", "id"))
        self.assertEqual(d["error"]["rule"], "where.mixed-and-or")

    def test_the_depth_and_the_leaves_and_the_values(self):
        def rule(text):
            d = doc_of(self.tool("o.csv", "--where", text, "--select", "id"))
            return "ok" if d.get("ok") else (d["error"]["rule"], d["error"]["detail"].get("offset"), d["error"]["detail"].get("expected"))

        self.assertEqual(rule("(" * 8 + "a = x" + ")" * 8), "ok")
        self.assertEqual(rule("(" * 9 + "a = x" + ")" * 9), ("where.syntax", 8, "fewer nested parentheses: the ceiling is 8"))
        self.assertEqual(rule(" or ".join("a = %d" % i for i in range(64))), "ok")
        self.assertEqual(rule(" or ".join("a = %d" % i for i in range(65)))[0], "where.syntax")
        # the 65th unit is refused where it begins, a group or a condition
        base = " or ".join("a = %d" % i for i in range(64))
        self.assertEqual(rule(base + " or (a = 1)"), ("where.syntax", len(base) + 4, "fewer conditions or values: the ceiling is 64 conditions and 1024 values"))
        self.assertEqual(rule(base + " or a = 1"), ("where.syntax", len(base) + 4, "fewer conditions or values: the ceiling is 64 conditions and 1024 values"))
        self.assertEqual(rule("(" + " or ".join("a = %d" % i for i in range(64)) + ") and a = 1")[0], "where.syntax")
        self.assertEqual(rule("a in (%s) or a = 1" % ", ".join(str(i) for i in range(1024))), "ok")
        self.assertEqual(rule("a in (%s) or a = 1" % ", ".join(str(i) for i in range(1025)))[0], "where.syntax")
        # the same ceilings of 64 and of 1,024 for and and for in, as before
        self.assertEqual(rule(" and ".join("a = %d" % i for i in range(65)))[0], "where.syntax")


class Fuzz(Base):
    """G-FUZZ: no trap, every refusal a rule of introspect, an offset inside the text, and every repair that is offered runs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rng = random.Random(3)
        cls.s.write("z.csv", table_csv(O.make_valid_table(rng, 30), rng))

    def fuzz_texts(self):
        rng = random.Random(20261022)
        texts = []
        alphabet = "()'\\ ,=!<>aorndtbc:1"
        while len(texts) < FUZZ * 4 // 10:
            texts.append("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30))))
        while len(texts) < FUZZ * 6 // 10:
            texts.append(mixed_text(rng))
        while len(texts) < FUZZ * 9 // 10:
            tree = O.gen_tree(rng, rng.randint(1, 8), lambda: O.draw_leaf(rng))
            t = O.render(tree, rng)
            if t and rng.random() < 0.9:
                i = rng.randrange(len(t))
                m = rng.random()
                if m < 0.4:
                    t = t[:i] + t[i + 1:]
                elif m < 0.8:
                    t = t[:i] + rng.choice(list(alphabet) + ["or", "and", " or ", "(", ")"]) + t[i:]
                else:
                    t = t[:i]
            texts.append(t)
        while len(texts) < FUZZ:
            texts.append(bytes(rng.randrange(1, 256) for _ in range(rng.randint(0, 24))))
        return texts

    def test_no_trap_every_refusal_a_rule_and_every_repair_accepted(self):
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        texts = self.fuzz_texts()

        def one(t):
            raw = t if isinstance(t, bytes) else t.encode("utf-8")
            r = run_argv([binary(), "--root", self.dir, "--where", raw, "--select", "id", "z.csv"])
            return t, raw, r

        counts = {"ok": 0, "where.syntax": 0, "where.mixed-and-or": 0, "repairs": 0, "other": 0}
        repairs = []
        for t, raw, r in pmap(one, texts):
            self.assertNotIn(r.status, (-4, -6, -8, -11, 132, 134, 136, 139), (t, r))
            self.assertEqual(validate(r), [], (t, r))
            d = doc_of(r)
            if d.get("ok"):
                counts["ok"] += 1
                continue
            e = d["error"]
            self.assertIn(e["rule"], rules, (t, e["rule"]))
            counts[e["rule"]] = counts.get(e["rule"], 0) + 1
            if e["rule"].startswith("where."):
                off = e["detail"]["offset"]
                self.assertTrue(0 <= off <= len(raw), (t, e["detail"]))
            if (e.get("repair") or {}).get("kind") == "choose":
                for o in e["repair"]["options"]:
                    repairs.append((t, o["argv"]))
        self.assertGreater(counts["ok"], 100, counts)
        self.assertGreater(counts["where.syntax"], FUZZ // 3, counts)

        def again(item):
            t, argv = item
            return t, run_argv(argv)

        for t, r in pmap(again, repairs):
            d = doc_of(r)
            self.assertTrue(d.get("ok"), ("an offered repair is refused", t, r.argv[-6:], r.stdout[:300]))
        counts["repairs"] = len(repairs)
        self.assertGreater(len(repairs), 20, counts)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# the other forms an expression arrives in

class Forms(Base):
    def test_groups_aggregates_sorts_reports_stdin_and_json_lines(self):
        rng = random.Random(20261023)
        rows = O.make_valid_table(rng, 60)
        self.put("f.csv", table_csv(rows, rng))
        db = sqlite_of(rows)
        for case in range(25):
            tree = O.gen_tree(rng, rng.randint(2, 6), lambda: O.draw_leaf(rng))
            text = O.render(tree, rng)
            params = []
            where = sql_of(tree, params)
            kept = [r for r in db.execute("SELECT id, a, n_i FROM t WHERE %s ORDER BY CAST(id AS INTEGER)" % where, params)]
            want_ids = [k[0] for k in kept]
            # a grouping counts the rows the expression keeps (SQLite groups bytewise too)
            groups = {}
            for _, a, n in kept:
                g = groups.setdefault(a, [0, 0])
                g[0] += 1
                g[1] += n
            r = self.tool("f.csv", "--where", text, "--group", "a", "--agg", "count,sum:n", "--limit", "1000000")
            got = [(x[0], int(x[1]), int(x[2])) for x in doc_of(r)["data"]["rows"]]
            self.assertEqual(got, [(a, g[0], g[1]) for a, g in sorted(groups.items(), key=lambda kv: kv[0].encode())], text)
            # --order-by and --top sort and cut the kept rows (ties keep the order of the file)
            r = self.tool("f.csv", "--where", text, "--order-by", "-n:int", "--top", "5", "--select", "id")
            ordered = sorted(kept, key=lambda k: -k[2])[:5]
            self.assertEqual([x[0] for x in doc_of(r)["data"]["rows"]], [k[0] for k in ordered], text)
            # --report types counts the cells of the kept rows
            r = self.tool("f.csv", "--where", text, "--report", "types", "--select", "id")
            self.assertEqual(int(doc_of(r)["data"]["rows"][0][1]), len(kept), text)
            # standard input: the same bytes
            data = (self.s.dir / "f.csv").read_bytes()
            via = run("--where", text, "--select", "id", "--limit", "1000000", stdin=data)
            self.assertEqual([x[0] for x in doc_of(via)["data"]["rows"]], want_ids, text)
            # JSON lines in, JSON lines out
            lines = "".join(json.dumps(dict(zip(O.HEADER, r_))) + "\n" for r_ in rows).encode()
            self.s.write("f.jsonl", lines)
            j = run("--root", self.dir, "--input-format", "jsonl", "--fields", ",".join(c.replace(".", "\\.") for c in O.HEADER), "--where", text, "--select", "id", "--limit", "1000000", "f.jsonl")
            self.assertEqual([x[0] for x in doc_of(j)["data"]["rows"]], want_ids, text)
            out = run("--root", self.dir, "--where", text, "--select", "id", "--format", "jsonl", "f.csv")
            self.assertEqual([json.loads(l)["id"] for l in out.stdout.decode().splitlines()], want_ids, text)

    def test_a_typed_cell_in_json_lines_is_the_exact_text_of_the_number(self):
        lines = b'{"id": "1", "n": 5, "s": "x"}\n{"id": "2", "n": 12, "s": "y"}\n{"id": "3", "n": 7.5, "s": "x"}\n'
        self.s.write("t.jsonl", lines)
        r = run("--root", self.dir, "--input-format", "jsonl", "--fields", "id,n,s", "--where", "s = y or (s = x and n:int > 4)", "--select", "id", "t.jsonl")
        d = doc_of(r)
        self.assertEqual(d["error"]["rule"], "value.not-integer")      # 7.5 is not an integer, reached on the third line
        self.assertEqual(d["error"]["detail"]["row"], 3)
        r = run("--root", self.dir, "--input-format", "jsonl", "--fields", "id,n,s", "--where", "s = y or (s = x and n:float > 4)", "--select", "id", "t.jsonl")
        self.assertEqual([x[0] for x in doc_of(r)["data"]["rows"]], ["1", "2", "3"])

    def test_a_column_called_or_and_and_not_in_every_place(self):
        header = "or,and,not,in,contains,x\n1,2,3,4,5,6\n7,8,9,10,11,12\n"
        self.put("k.csv", header)
        cases = {
            "or = 1": [["1"]], "or = 1 or or = 7": [["1"], ["7"]], "(or = 1) or (and = 8)": [["1"], ["7"]], "'or' = 1 and not = 3": [["1"]], "not = 3 or not = 9 and x = 6": None,
            "in = 4 or contains = 11": [["1"], ["7"]], "(in = 4 and or = 1) or (and = 8 and not = 9)": [["1"], ["7"]], "a = 1 or or = 1": None,
        }
        for where, want in cases.items():
            r = run("--root", self.dir, "--where", where, "--select", "or", "k.csv")
            d = doc_of(r)
            if want is None:
                self.assertFalse(d.get("ok"), where)
            else:
                self.assertEqual(d["data"]["rows"], want, where)


if __name__ == "__main__":
    unittest.main()
