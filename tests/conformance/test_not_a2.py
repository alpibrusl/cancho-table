"""Slice A2 of docs/or-and-columns.md: `not` in --where (section 17 has the notes of the build). The gates of section 9 that the flag form holds, written against references that share no code
with the tool (not_a2.py, refimpl.py) and written BEFORE the tool was changed: every test here fails on the binary of `main` after A1, which has no `not`.

* G-ORACLE-SQL: the truth values of `not ( ... )`, `COLUMN not in (...)`, `COLUMN not contains V`, with `and`, `or`, parentheses, typed and text conditions, against SQLite (TEXT columns,
  BINARY collation) on generated tables that hold no NULL (SQL's three-valued logic is not the tool's), `--format json` and `csv`, one thread and four with tiny ranges;
* G-ORACLE-REF: the refusals no SQL engine can give (the row, the column and the rule of the first cell a reached condition refuses, in file order) against the tree walker of refimpl.py
  with `not`, on tables with bad cells, and the table of section 5.1 written again for `not`: a `not` never changes which cells are read;
* G-PROGRAM: the branch program the tool compiles (read from --explain) is the document's program with `not` as the swap of the two exits, is forward-only, reaches every condition and gives the
  tree's answer and sequence of leaves on every status of every leaf; a plan with a `not` has a program even when it has no `or`;
* G-PAR: threads 1..64 and chunk sizes give the bytes of one thread, refusals included, with bad cells that only some ranges reach through a `not`;
* G-OFFSETS: the reference parser and the tool agree on accepted, refused with the offset and the words, the mix with its offset and its readings, and the repair of `not COND`;
* G-FUZZ: random bytes and mutated expressions trap nowhere, every refusal is a rule introspect lists, every repair that is offered is accepted;
* the other forms an expression arrives in: groups, aggregates, --order-by with --top, --report types, standard input, JSON lines in and out, a join (the expression is evaluated on the joined
  row), the MCP server; and the columns called `not` and `or` in every place they can stand.
"""

import csv
import io
import json
import os
import random
import re
import unittest

import not_a2 as N
import or_a1 as O
import refimpl
import test_mcp as M
import test_or_a1 as A1
from harness import Scratch, binary, introspect, run, run_argv, validate

THREADS = A1.THREADS
CHUNKS = A1.CHUNKS
pmap = A1.pmap
table_csv = A1.table_csv
lat = A1.lat
doc_of = A1.doc_of
SQL_CASES = int(os.environ.get("NOT_A2_SQL", "3000"))
REF_CASES = int(os.environ.get("NOT_A2_REF", "3000"))
PROGRAM_CASES = int(os.environ.get("NOT_A2_PROGRAM", "10000"))
SOUPS = int(os.environ.get("NOT_A2_SOUPS", "20000"))
FUZZ = int(os.environ.get("NOT_A2_FUZZ", "10000"))


def report(*what):
    """NOT_A2_REPORT=1 prints the figures the pull request quotes: what each generated gate drew."""
    if os.environ.get("NOT_A2_REPORT"):
        print("\nA2:", *what)


def sql_of(node, params):
    """The SQL of a tree with `not` (the tables hold no NULL, so NOT is the two-valued one)."""
    if node[0] == "leaf":
        s = A1.sql_of(node, params)
        return "NOT (%s)" % s if node[1].get("neg") else s
    if node[0] == "not":
        return "NOT (%s)" % sql_of(node[1][0], params)
    glue = " AND " if node[0] == "and" else " OR "
    return "(" + glue.join(sql_of(k, params) for k in node[1]) + ")"


def without_nots(node):
    if node[0] == "leaf":
        return ("leaf", {k: v for k, v in node[1].items() if k != "neg"})
    if node[0] == "not":
        return without_nots(node[1][0])
    return (node[0], [without_nots(k) for k in node[1]])


def drawn(rng, n, leaf_of, depth, p_not=0.3):
    """(tree, text) with a text the reference parser takes (a tree may pass the 8 levels of parentheses once its nots are rendered: such a draw is drawn again)."""
    for _ in range(20):
        tree = N.gen_tree(rng, n, leaf_of, depth=depth, p_not=p_not)
        text = N.render(tree, rng)
        if N.parse_result(text)[0] == "ok":
            return tree, text
    raise AssertionError("no generated expression within the depth")


class Base(A1.Base):
    pass


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-ORACLE-SQL

class SqlOracle(Base):
    def test_the_truth_values_are_sqlites(self):
        rng = random.Random(20261101)
        tables = []
        for k in range(12):
            rows = O.make_valid_table(rng, rng.randint(25, 60))
            self.put("v%d.csv" % k, table_csv(rows, rng))
            tables.append((k, rows, A1.sqlite_of(rows)))
        jobs = []
        for case in range(SQL_CASES):
            k, rows, db = rng.choice(tables)
            tree, text = drawn(rng, rng.choice([1, 2, 3, 3, 4, 5, 6, 8, 12]), lambda: O.draw_leaf(rng), rng.choice([1, 2, 3, 4]))
            self.assertEqual(N.count_leaves(N.semantic(N.parse(text))), N.count_leaves(tree), text)
            params = []
            where = sql_of(tree, params)
            want = [r[0] for r in db.execute("SELECT id FROM t WHERE %s ORDER BY CAST(id AS INTEGER)" % where, params)]
            jobs.append((case, k, text, want, case % 4))
        self.assertGreater(sum(1 for j in jobs if "not" in j[2]), SQL_CASES // 3)

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
        report("G-ORACLE-SQL: %d expressions, %d with a not, %d differ from SQLite" % (len(jobs), sum(1 for j in jobs if "not" in j[2]), len(bad)))
        self.assertEqual(bad[:3], [], "%d of %d expressions differ from SQLite" % (len(bad), len(jobs)))

    def test_the_oracle_would_notice_a_dropped_or_doubled_not(self):
        # the generator reaches `not`, and a `not` changes the rows: the same table under a tree and under the tree without its nots differ for most expressions that have one
        rng = random.Random(8)
        rows = O.make_valid_table(rng, 40)
        db = A1.sqlite_of(rows)
        differ = nots = 0
        for _ in range(300):
            tree = N.gen_tree(rng, rng.randint(1, 6), lambda: O.draw_leaf(rng), p_not=0.3)
            if not N.has_not(tree):
                continue
            nots += 1
            a, b = [], []
            w1, w2 = sql_of(tree, a), sql_of(without_nots(tree), b)
            if db.execute("SELECT group_concat(id) FROM t WHERE %s" % w1, a).fetchone() != db.execute("SELECT group_concat(id) FROM t WHERE %s" % w2, b).fetchone():
                differ += 1
        self.assertGreater(nots, 150)
        self.assertGreater(differ, nots // 2)

    def test_not_in_is_the_complement_of_in_and_not_contains_of_contains(self):
        rng = random.Random(9)
        rows = O.make_valid_table(rng, 80)
        self.put("c.csv", table_csv(rows, rng))
        everything = [r[0] for r in rows]
        for col in ("a", "b", "c", "t u"):
            for lits in (["x"], ["x", "y", ""], ["it's", "a,b"]):
                q = ", ".join(("'%s'" % l.replace("'", "''")) for l in lits)
                kept = doc_of(self.tool("c.csv", "--where", "'%s' in (%s)" % (col, q), "--select", "id", "--limit", "1000"))["data"]["rows"]
                gone = doc_of(self.tool("c.csv", "--where", "'%s' not in (%s)" % (col, q), "--select", "id", "--limit", "1000"))["data"]["rows"]
                self.assertEqual(sorted([x[0] for x in kept] + [x[0] for x in gone]), sorted(everything), (col, lits))
                self.assertEqual(set(x[0] for x in kept) & set(x[0] for x in gone), set())
            for needle in ("x", "", "y z", "é"):
                kept = doc_of(self.tool("c.csv", "--where", "'%s' contains '%s'" % (col, needle), "--select", "id", "--limit", "1000"))["data"]["rows"]
                gone = doc_of(self.tool("c.csv", "--where", "'%s' not contains '%s'" % (col, needle), "--select", "id", "--limit", "1000"))["data"]["rows"]
                self.assertEqual(sorted([x[0] for x in kept] + [x[0] for x in gone]), sorted(everything), (col, needle))
        # an empty needle is in every cell, so its complement keeps none
        self.assertEqual(doc_of(self.tool("c.csv", "--where", "a not contains ''", "--select", "id"))["data"]["rows"], [])


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-ORACLE-REF

class RefusalOracle(Base):
    def test_the_first_refusal_in_file_order_and_the_rows_otherwise(self):
        rng = random.Random(20261102)
        tables = []
        for k in range(10):
            rows = O.make_bad_table(rng, rng.randint(20, 70))
            self.put("b%d.csv" % k, table_csv(rows, rng))
            tables.append((k, rows))
        jobs = []
        for case in range(REF_CASES):
            k, rows = rng.choice(tables)
            tree, text = drawn(rng, rng.choice([1, 2, 3, 3, 4, 5, 6, 8]), lambda: A1.guarded_leaf(rng), rng.choice([1, 2, 3]), p_not=0.35)
            jobs.append((case, k, text, A1.expected_outcome(rows, tree)))

        def one(job):
            case, k, text, want = job
            r = self.tool("b%d.csv" % k, "--where", text, "--select", "id", "--limit", "1000000")
            return case, text, want, A1.tool_outcome(r), r

        results = pmap(one, jobs)
        bad = [(c, t, w, g) for c, t, w, g, r in results if w != g]
        refused = sum(1 for c, t, w, g, r in results if w[0] == "refuse")
        report("G-ORACLE-REF: %d expressions, %d refused (the first refusal in file order), %d kept rows, %d differ from the reference" % (len(results), refused, len(results) - refused, len(bad)))
        self.assertEqual(bad[:3], [], "%d of %d differ from the reference" % (len(bad), len(results)))
        self.assertGreater(refused, len(results) // 10)
        self.assertGreater(len(results) - refused, len(results) // 10)

    def test_the_table_of_section_5_1_with_not(self):
        self.put("g.csv", "id,a,c\n1,x,\n2,x,abc\n3,y,\n4,y,7\n")

        def rows(where):
            d = doc_of(self.tool("g.csv", "--where", where, "--select", "id"))
            return [r[0] for r in d["data"]["rows"]] if d.get("ok") else (d["error"]["rule"], d["error"]["detail"]["row"], d["error"]["detail"]["column"])

        bad = "value.not-integer"
        # `not` does not change which cells are read: the read is made before the comparison, and the refusal is the same either way
        self.assertEqual(rows("not (c:int > 5)"), (bad, 1, "c"))
        self.assertEqual(rows("c:int <= 5"), (bad, 1, "c"))
        self.assertEqual(rows("not (a = x) and c:int > 5"), (bad, 3, "c"))          # a = x on the first two rows: the `not` is false, the typed condition is not tried
        # the guard of an `or` under a `not` is the positive test, as it is without one
        self.assertEqual(rows("a = y and not (c = '' or c:int > 6)"), [])
        self.assertEqual(rows("a = y and not (c = '' or c:int > 7)"), ["4"])
        self.assertEqual(rows("a = y and not (c = '' or c:int > 5)"), [])
        self.assertEqual(rows("a = y and not (c:int > 5 or c = '')"), (bad, 3, "c"))
        # `not in` and `not contains` are the condition's own, swapped: a typed one reads its cell once
        self.assertEqual(rows("c not in ('') and c:int > 5"), (bad, 2, "c"))
        self.assertEqual(rows("a not in (y) or c:int > 5"), (bad, 3, "c"))
        self.assertEqual(rows("a not in (x) or c:int > 5"), (bad, 1, "c"))
        self.assertEqual(rows("not (a = y or c:int > 5)"), (bad, 1, "c"))
        self.assertEqual(rows("not (a = x or c:int > 5)"), (bad, 3, "c"))
        self.assertEqual(rows("c:int not in (7)"), (bad, 1, "c"))
        self.assertEqual(rows("a = y and c:int not in (7)"), (bad, 3, "c"))
        self.assertEqual(rows("a = y and c != '' and c:int not in (7)"), [])
        self.assertEqual(rows("a = y and c != '' and c:int not in (8)"), ["4"])
        # and the same at every thread count, with a range of one record
        for threads in ("1", "4", "16"):
            r = self.tool("g.csv", "--where", "a = y and not (c = '' or c:int > 7)", "--select", "id", "--threads", threads, "--chunk-bytes", "8", "--parallel-min-bytes", "0")
            self.assertEqual([x[0] for x in doc_of(r)["data"]["rows"]], ["4"])

    def test_the_scale_repair_rewrites_the_condition_it_names_inside_a_not(self):
        # value.decimal-scale offers the same command with the column's scale made larger, at the offset of the condition (finish.cho, err_cond): a condition in a `not` or a `not in` is one
        self.put("d.csv", "id,d\n1,1.55\n2,2.00\n")
        for where, fixed in (("id != 9 and d:dec(1) not in (1.5)", "id != 9 and d:dec(2) not in (1.5)"), ("not (d:dec(1) = 1.5 and id != 9)", "not (d:dec(2) = 1.5 and id != 9)"),
                             ("not (id = 9 or d:dec(1) not in (1.5))", "not (id = 9 or d:dec(2) not in (1.5))")):
            d = doc_of(self.tool("d.csv", "--where", where))
            self.assertEqual(d["error"]["rule"], "value.decimal-scale", where)
            argv = d["error"]["repair"]["options"][0]["argv"]
            self.assertEqual(argv[argv.index("--where") + 1], fixed)
            self.assertTrue(doc_of(run_argv(argv)).get("ok"), fixed)

    def test_a_typed_condition_cannot_be_contains_or_not_contains(self):
        self.put("t.csv", "id,n\n1,5\n")
        for text, offset in (("n:int contains 5", 6), ("n:int not contains 5", 10), ("n:dec(2) not  contains 5", 14)):
            d = doc_of(self.tool("t.csv", "--where", text, "--select", "id"))
            self.assertEqual((d["error"]["rule"], d["error"]["detail"]["offset"]), ("where.syntax", offset), text)
            self.assertEqual(d["error"]["detail"]["expected"], "= != < <= > >= or in: contains compares text, and this column is :int, :dec or :float")


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-PROGRAM

def explain(self, text):
    return run("--root", self.dir, "--explain", "--where", text, "--select", "id", "p.csv")


class Program(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s.write("p.csv", table_csv([["0", "x", "y", "z", "1", "1.00", "1.0", "", "", "", ""]], random.Random(0)))

    def exits(self, text):
        return [(e["if_true"], e["if_false"]) for e in A1.explain_where(explain(self, text))]

    def test_the_program_is_the_models_and_the_trees(self):
        rng = random.Random(20261103)
        jobs = [drawn(rng, rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16]), lambda: O.draw_leaf(rng), rng.choice([1, 2, 3, 4])) for _ in range(PROGRAM_CASES)]

        def one(job):
            tree, text = job
            return tree, text, A1.explain_where(explain(self, text))

        flowed = 0
        for tree, text, where in pmap(one, jobs):
            lv = N.leaves(tree)
            self.assertIsNotNone(where, text)
            self.assertEqual(len(where), len(lv), text)
            for entry, cond in zip(where, lv):
                self.assertEqual(entry["op"], cond["op"] if cond["kind"] == "cmp" else cond["kind"], text)
                self.assertEqual(entry["values"], cond["lits"] if cond["kind"] == "in" else [cond["lit"]], text)
            if not N.has_flow(tree):
                for entry in where:
                    self.assertNotIn("if_true", entry, text)
                continue
            flowed += 1
            prog, numbered = N.program(tree)
            for k, entry in enumerate(where):
                self.assertEqual((entry["if_true"], entry["if_false"]), tuple(A1.target_of(t) for t in prog[k]), "%s leaf %d" % (text, k))
                for t in (entry["if_true"], entry["if_false"]):
                    self.assertTrue(t in ("accept", "reject") or (isinstance(t, int) and t > k), "a target goes backwards: %s" % text)
            tool = {k: (entry["if_true"], entry["if_false"]) for k, entry in enumerate(where)}
            n = len(where)
            reach = {0}
            for k in range(n):
                if k in reach:
                    reach.update(t for t in tool[k] if isinstance(t, int))
            self.assertEqual(reach, set(range(n)), "a condition cannot be reached: %s" % text)
            statuses = list(__import__("itertools").product([0, 1, 2], repeat=n)) if n <= 6 else [tuple(rng.choice([0, 1, 2]) for _ in range(n)) for _ in range(300)]
            for st in statuses:
                compiled = {k: tuple(({"accept": "A", "reject": "R"}.get(t, t)) for t in tool[k]) for k in tool}
                got, seen = N.run_program(compiled, st)
                want, seen_tree = N.tree_status(tree, st)
                self.assertEqual((got, seen), (want, seen_tree), "%s statuses %r" % (text, st))
        report("G-PROGRAM: %d expressions explained, %d with a program" % (len(jobs), flowed))
        self.assertGreater(flowed, PROGRAM_CASES // 2)

    def test_the_program_of_the_documents_examples(self):
        # section 4.1's table: `not` is a swap of the exits
        self.assertEqual(self.exits("not (a = 1 or b = 2)"), [("reject", 1), ("reject", "accept")])
        self.assertEqual(self.exits("a not in (1, 2) and b = 3"), [("reject", 1), ("accept", "reject")])
        self.assertEqual(self.exits("(a = 1 or b = 2) and c = 3"), [(2, 1), (2, "reject"), ("accept", "reject")])
        # a plan with a `not` has a program though it has no `or`
        self.assertEqual(self.exits("a not in (1)"), [("reject", "accept")])
        self.assertEqual(self.exits("a not contains x"), [("reject", "accept")])
        self.assertEqual(self.exits("not (a = 1)"), [("reject", "accept")])
        self.assertEqual(self.exits("not (not (a = 1))"), [("accept", "reject")])
        self.assertEqual(self.exits("a not in (1) and b not contains 2 and c = 3"), [("reject", 1), ("reject", 2), ("accept", "reject")])
        self.assertEqual(self.exits("not (a = 1 and b = 2)"), [(1, "accept"), ("reject", "accept")])
        # a conjunction of plain conditions has none: its document is the document it was
        for e in A1.explain_where(explain(self, "a = 1 and (b = 2 and c = 3)")):
            self.assertNotIn("if_true", e)

    def test_the_document_of_a_plan_without_not_or_or_is_the_document_it_was(self):
        for text in ("a = 1", "a = 1 and b in (1, 2) and c contains x", "(a = 1 and b = 2) and c != 3", "a:int < 5 and b:dec(2) >= 1.5"):
            for e in A1.explain_where(explain(self, text)):
                self.assertEqual(sorted(e), ["column", "op", "type", "values"], text)

    def test_the_explain_of_a_not_names_the_swap_in_its_exits(self):
        d = doc_of(explain(self, "a not in (1, 2)"))
        w = d["data"]["explain"]["plan"]["where"]
        self.assertEqual([(e["op"], e["values"], e["if_true"], e["if_false"]) for e in w], [("in", ["1", "2"], "reject", "accept")])


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-PAR

class Parallel(Base):
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

    def test_bad_cells_that_only_some_ranges_reach_through_a_not(self):
        rows = []
        for i in range(400):
            rows.append([str(i), "x" if i % 3 else "y", "q", "r", str(i), "1.00", "1.0", "", "", "", ""])
        rows[40][4] = ""                # a = x: `not (a = y)` is true, n is never read
        rows[41][4] = "oops"            # not reached either
        rows[300][1] = "y"
        rows[300][4] = "bad"            # a = y: reached
        rows[360][1] = "y"
        rows[360][4] = ""               # reached too, later
        self.put("par.csv", table_csv(rows, random.Random(1)))
        outcomes = set()
        for where in ("not (a = y) or id:int > 100", "not (a = y) or n:int > 100", "a not in (y) or n:int > 100", "not (a in (y) and n:int < 100)", "not (a = y and n:int <= 100)", "a not in (y) or (n:int > 100 and a not contains q)",
                      "not (not (a = x) or n:int < 100)"):
            for extra in (["--select", "id", "--format", "csv"], ["--select", "id"], ["--group", "a", "--agg", "count"], ["--group", "a", "--agg", "count,sum:n"]):
                want = self.same("par.csv", ["--where", where, *extra], where)
                self.assertIn(want[0], (0, 8), where)
                outcomes.add(want[0])
        self.assertEqual(outcomes, {0, 8})
        rows[300][4] = "300"
        rows[360][4] = "360"
        self.put("par.csv", table_csv(rows, random.Random(1)))
        want = self.same("par.csv", ["--where", "not (a = y) or n:int > 100", "--select", "id", "--format", "csv"], "no reached bad cell")
        self.assertEqual(want[0], 0, want)

    def test_generated_plans_with_a_not(self):
        rng = random.Random(20261104)
        rows = O.make_bad_table(rng, 160)
        self.put("gp.csv", table_csv(rows, rng))
        for case in range(14):
            tree, text = drawn(rng, rng.randint(2, 7), lambda: A1.guarded_leaf(rng), 2, p_not=0.4)
            for extra in (["--select", "id", "--format", "csv"], ["--select", "id"], ["--group", "a", "--agg", "count"], ["--order-by", "id:int", "--top", "3"]):
                self.same("gp.csv", ["--where", text, *extra], text)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# G-OFFSETS and G-FUZZ

SOUP_NOT = ["not (", "not", "not(", " not ", "not not", ")", "(", "not in (1)", "not contains x", "not = 1", "not in", "not (a", "'not'", "\\not"]


def soup_not(rng):
    """A token sequence with `not` in the places it can and cannot stand, damaged now and then."""
    out = []
    depth = 0
    for _ in range(rng.randint(1, 5)):
        while rng.random() < 0.25:
            out.append(rng.choice(["(", "not (", "not(", "not  ("]))
            depth += 1
        col = rng.choice(A1.PIECES_COLUMNS + ["not", "not", "'not'"])
        r = rng.random()
        if r < 0.3:
            out.append("%s %s %s" % (col, rng.choice(["=", "=", "!=", "<", ">=", "<=", ">", "=="]), rng.choice(A1.SOUP_VALUES[:8])))
        elif r < 0.45:
            out.append("%s in (%s)" % (col, ", ".join(rng.choice(A1.SOUP_VALUES[:8]) for _ in range(rng.randint(1, 3)))))
        elif r < 0.6:
            out.append("%s not in (%s)" % (col, ", ".join(rng.choice(A1.SOUP_VALUES[:8]) for _ in range(rng.randint(1, 3)))))
        elif r < 0.7:
            out.append("%s %s %s" % (col, rng.choice(["contains", "not contains", "not  contains", "NOT contains"]), rng.choice(A1.SOUP_VALUES[:8])))
        elif r < 0.82:
            out.append("not %s %s %s" % (col, rng.choice(["=", "!=", "<", ">=", "in", "contains", "not in", "not contains"]), rng.choice(["1", "x", "(1, 2)", "'y z'"])))
        elif r < 0.92:
            out.append("%s %s" % (col, " ".join(rng.choice(SOUP_NOT + A1.SOUP_VALUES + A1.PIECES_COLUMNS) for _ in range(rng.randint(1, 3)))))
        else:
            out.append(" ".join(rng.choice(SOUP_NOT + A1.SOUP_VALUES + A1.PIECES_COLUMNS) for _ in range(rng.randint(1, 3))))
        while depth and rng.random() < 0.3:
            out.append(")")
            depth -= 1
        out.append(rng.choice(A1.PIECES_CONN) if rng.random() < 0.85 else "")
    if out and out[-1] in A1.PIECES_CONN + [""] and rng.random() < 0.8:
        out.pop()
    while depth and rng.random() < 0.85:
        out.append(")")
        depth -= 1
    text = rng.choice([" ", "  ", ""]).join(out) if rng.random() < 0.15 else " ".join(out)
    if rng.random() < 0.2 and text:
        i = rng.randrange(len(text))
        text = text[:i] + rng.choice(["'", "\\", "(", ")", ",", " or ", " and ", " not ", "not (", "\x01", "é"]) + text[i + 1:]
    return text


def damaged(rng):
    tree = N.gen_tree(rng, rng.randint(1, 6), lambda: O.draw_leaf(rng, typed_ok=rng.random() < 0.5), depth=2, p_not=0.4)
    t = N.render(tree, rng)
    if t and rng.random() < 0.8:
        i = rng.randrange(len(t))
        m = rng.random()
        if m < 0.35:
            t = t[:i] + t[i + 1:]
        elif m < 0.7:
            t = t[:i] + rng.choice([" not ", "not", "not (", "(", ")", " or ", " and ", "not in", "'"]) + t[i:]
        else:
            t = t[:i]
    return t


def unwrapped(rng):
    """`not COND` the way SQL spells it: a not before a bare condition, in an otherwise good expression (the refusal that has a repair)."""
    def cond():
        return O.render_leaf(O.draw_leaf(rng, typed_ok=rng.random() < 0.4), rng)

    parts = []
    for _ in range(rng.randint(1, 4)):
        c = cond()
        parts.append(("not " + c) if rng.random() < 0.6 else (("(" + c + ")") if rng.random() < 0.2 else c))
    glue = rng.choice([" and ", " or "])
    text = glue.join(parts)
    return "(" + text + ")" if rng.random() < 0.2 else text


class Offsets(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s.write("o.csv", table_csv([["1", "x", "y", "z", "1", "1.00", "1.0", "a b", "1", "2", "3"]], random.Random(0), O.HEADER))

    def where_outcome(self, text, r):
        d = doc_of(r)
        if d.get("ok"):
            return ("ok",), d
        e = d["error"]
        if e["rule"] == "where.syntax":
            return ("syntax", e["detail"]["offset"], e["detail"]["expected"]), d
        if e["rule"] == "where.mixed-and-or":
            return ("mixed", e["detail"]["offset"]), d
        return ("ok",), d

    def test_the_reference_and_the_tool_agree(self):
        rng = random.Random(20261105)
        texts = [soup_not(rng) for _ in range(SOUPS)] + [damaged(rng) for _ in range(SOUPS // 2)] + [unwrapped(rng) for _ in range(SOUPS // 4)]
        texts += [A1.soup(rng) for _ in range(SOUPS // 4)] + [A1.mixed_text(rng) for _ in range(SOUPS // 10)]
        texts += ["a = 1 or not (b = 2) and c = 3", "not (a = 1) and b = 2 or c = 3", "(not (a = 1 or b = 2 and c = 3))", "not (a = 1 or b = 2 and c = 3)", "a not in (1) or b = 2 and c not contains x"]
        texts += ["not (" * k + "a = 1" + ")" * k for k in range(0, 11)] + ["(" * j + "not (" * k + "a = 1" + ")" * (j + k) for j in (1, 4, 7) for k in (1, 2, 5)]
        texts += [" and ".join("a not in (%d)" % i for i in range(n)) for n in (63, 64, 65)] + [" or ".join("not (a = %d)" % i for i in range(n)) for n in (63, 64, 65)]
        texts += ["a not in (%s)" % ", ".join(str(i) for i in range(n)) for n in (1023, 1024, 1025)]
        texts += ["not (a = 1) " + c + " not" for c in ("and", "or")] + ["not", "not ()", "not (", "not )", "a not", "a not in", "a not in (", "a not contains", "a not = 1", "not 'a' = 1", "not a", "not not = 1", "not not in (1)",
                                                                       "not  not  in  (1)", "'not' (a = 1)", "not'(a = 1)", "(not = 1)", "not = 1 or not in (2)", "not contains x and not not contains y"]
        texts = [t for t in texts if t != ""]        # an empty --where is no --where
        seen = {"ok": 0, "syntax": 0, "mixed": 0, "repair": 0, "norepair": 0}

        def one(text):
            return text, run("--root", self.dir, "--where", text, "--select", "id", "o.csv")

        for text, r in pmap(one, texts):
            ref = N.parse_result(lat(text))
            self.assertEqual(validate(r), [], (text, r))
            tool, d = self.where_outcome(text, r)
            seen[ref[0]] += 1
            self.assertEqual(tool, ref if ref[0] != "ok" else ("ok",), text[:300])
            if ref[0] == "mixed":
                options = [lat(o["argv"][o["argv"].index("--where") + 1]) for o in d["error"]["repair"]["options"]] if d["error"]["repair"]["kind"] == "choose" else []
                self.assertEqual(options, N.offered_readings(lat(text)), text)
            elif ref[0] == "syntax":
                fixed = N.repair_not(lat(text)) if ref[2] == N.EXPECTED[23] else None
                rep = d["error"]["repair"]
                if fixed is None:
                    self.assertEqual(rep["kind"], "none", text)
                    seen["norepair"] += 1
                else:
                    self.assertEqual(rep["kind"], "choose", text)
                    self.assertEqual([lat(o["argv"][o["argv"].index("--where") + 1]) for o in rep["options"]], [fixed], text)
                    seen["repair"] += 1
        report("G-OFFSETS: %d texts, %s" % (len(texts), seen))
        self.assertGreater(seen["ok"], SOUPS // 10, seen)
        self.assertGreater(seen["syntax"], SOUPS // 5, seen)
        self.assertGreater(seen["mixed"], 100, seen)
        self.assertGreater(seen["repair"], SOUPS // 8, seen)

    def test_the_repair_of_not_cond_is_the_same_question(self):
        # `not COND` is refused with the exact complement; the rewritten expression answers what `not COND` means (SQLite judges the meaning)
        rng = random.Random(20261106)
        rows = O.make_valid_table(rng, 50)
        self.put("r.csv", table_csv(rows, rng))
        db = A1.sqlite_of(rows)
        done = 0
        for _ in range(400):
            leaves = [O.draw_leaf(rng, typed_ok=rng.random() < 0.4) for _ in range(rng.randint(1, 4))]
            if any(l["column"] == "not" for l in leaves):
                continue            # `not not = 1` is the column called not, and `not not in (1)` is valid: a different question (tested elsewhere)
            nots = [rng.random() < 0.6 for _ in leaves]
            if not any(nots):
                continue
            glue = rng.choice(["and", "or"])
            text = (" %s " % glue).join(("not " if n else "") + O.render_leaf(l, rng) for l, n in zip(leaves, nots))
            tree = (glue, [("not", [("leaf", l)]) if n else ("leaf", l) for l, n in zip(leaves, nots)]) if len(leaves) > 1 else ("not", [("leaf", leaves[0])])
            d = doc_of(self.tool("r.csv", "--where", text, "--select", "id"))
            self.assertEqual(d["error"]["rule"], "where.syntax", text)
            self.assertEqual(d["error"]["detail"]["expected"], "( after not", text)
            rep = d["error"]["repair"]
            self.assertEqual(rep["kind"], "choose", text)
            fixed = rep["options"][0]["argv"]
            where = fixed[fixed.index("--where") + 1]
            params = []
            want = [r[0] for r in db.execute("SELECT id FROM t WHERE %s ORDER BY CAST(id AS INTEGER)" % sql_of(tree, params), params)]
            got = doc_of(run_argv(fixed))
            self.assertTrue(got.get("ok"), (text, where))
            self.assertEqual(sorted(r[0] for r in got["data"]["rows"]), sorted(want), (text, where))
            done += 1
        self.assertGreater(done, 200)

    def test_the_repair_keeps_its_flag_in_the_equals_form_and_offers_nothing_for_what_is_not_exact(self):
        r = run("--root", self.dir, "--where=not a < 1 and b = 2", "--select", "id", "o.csv")
        d = doc_of(r)
        self.assertEqual(d["error"]["rule"], "where.syntax")
        self.assertEqual([[a for a in o["argv"] if a.startswith("--where")] for o in d["error"]["repair"]["options"]], [["--where=a >= 1 and b = 2"]])
        # a second fault after the not: the grammar still refuses the rewritten expression, so none is offered
        d = doc_of(self.tool("o.csv", "--where", "not a < 1 and (b = 2", "--select", "id"))
        self.assertEqual(d["error"]["repair"]["kind"], "none")
        # `not a not in (...)` has an exact complement too, and `not not in (1)` is a column called not
        d = doc_of(self.tool("o.csv", "--where", "not a not in (1, 2)", "--select", "id"))
        self.assertEqual([o["argv"][o["argv"].index("--where") + 1] for o in d["error"]["repair"]["options"]], ["a in (1, 2)"])
        self.assertTrue(doc_of(self.tool("o.csv", "--where", "not in (1)", "--select", "id")).get("ok"))
        self.assertTrue(doc_of(self.tool("o.csv", "--where", "not not in (1)", "--select", "id")).get("ok"))
        self.assertTrue(doc_of(self.tool("o.csv", "--where", "not contains x", "--select", "id")).get("ok"))

    def test_the_depth_and_the_leaves_and_the_values_with_not(self):
        def rule(text):
            d = doc_of(self.tool("o.csv", "--where", text, "--select", "id"))
            return "ok" if d.get("ok") else (d["error"]["rule"], d["error"]["detail"].get("offset"), d["error"]["detail"].get("expected"))

        # the parenthesis of a `not (` is a level of the ceiling of 8, shared with the groups
        self.assertEqual(rule("not (" * 8 + "a = x" + ")" * 8), "ok")
        self.assertEqual(rule("not (" * 9 + "a = x" + ")" * 9), ("where.syntax", 5 * 8 + 4, "fewer nested parentheses: the ceiling is 8"))
        self.assertEqual(rule("(" * 4 + "not (" * 4 + "a = x" + ")" * 8), "ok")
        self.assertEqual(rule("(" * 4 + "not (" * 5 + "a = x" + ")" * 9)[0], "where.syntax")
        self.assertEqual(rule(" and ".join("a not in (%d)" % i for i in range(64))), "ok")
        self.assertEqual(rule(" and ".join("a not in (%d)" % i for i in range(65)))[0], "where.syntax")
        base = " and ".join("a not in (%d)" % i for i in range(64))
        self.assertEqual(rule(base + " and not (a = 1)"), ("where.syntax", len(base) + 5, "fewer conditions or values: the ceiling is 64 conditions and 1024 values"))
        self.assertEqual(rule("a not in (%s) or a not in (1)" % ", ".join(str(i) for i in range(1024))), "ok")
        self.assertEqual(rule("a not in (%s) or a = 1" % ", ".join(str(i) for i in range(1025)))[0], "where.syntax")


class Fuzz(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rng = random.Random(3)
        cls.s.write("z.csv", table_csv(O.make_valid_table(rng, 30), rng))

    def fuzz_texts(self):
        rng = random.Random(20261107)
        texts = []
        alphabet = "()'\\ ,=!<>aorndtbc:1"
        while len(texts) < FUZZ * 3 // 10:
            texts.append("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30))))
        while len(texts) < FUZZ * 5 // 10:
            texts.append(soup_not(rng))
        while len(texts) < FUZZ * 6 // 10:
            texts.append(unwrapped(rng))
        while len(texts) < FUZZ * 9 // 10:
            texts.append(damaged(rng))
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

        counts = {"ok": 0}
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
                self.assertTrue(0 <= e["detail"]["offset"] <= len(raw), (t, e["detail"]))
            if (e.get("repair") or {}).get("kind") == "choose":
                for o in e["repair"]["options"]:
                    repairs.append((t, o["argv"]))
        report("G-FUZZ: %d texts, %s, %d repairs offered" % (len(texts), counts, len(repairs)))
        self.assertGreater(counts["ok"], 100, counts)
        self.assertGreater(counts["where.syntax"], FUZZ // 3, counts)

        def again(item):
            t, argv = item
            return t, run_argv(argv)

        for t, r in pmap(again, repairs):
            d = doc_of(r)
            # an offered repair is accepted by the grammar: it may still name a column the table does not have, or a cell that is not a number, which are other rules
            self.assertTrue(d.get("ok") or not d["error"]["rule"].startswith("where."), ("an offered repair is refused by the grammar", t, r.argv[-6:], r.stdout[:300]))
        self.assertGreater(len(repairs), FUZZ // 60, counts)
        self.assertGreater(len([1 for t, a in repairs if "not" in str(a[-4:])]), 0)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# the other forms an expression arrives in

class Forms(Base):
    def test_groups_aggregates_sorts_reports_stdin_and_json_lines(self):
        rng = random.Random(20261108)
        rows = O.make_valid_table(rng, 60)
        self.put("f.csv", table_csv(rows, rng))
        db = A1.sqlite_of(rows)
        for case in range(25):
            tree, text = drawn(rng, rng.randint(2, 6), lambda: O.draw_leaf(rng), 3, p_not=0.4)
            params = []
            where = sql_of(tree, params)
            kept = [r for r in db.execute("SELECT id, a, n_i FROM t WHERE %s ORDER BY CAST(id AS INTEGER)" % where, params)]
            want_ids = [k[0] for k in kept]
            groups = {}
            for _, a, n in kept:
                g = groups.setdefault(a, [0, 0])
                g[0] += 1
                g[1] += n
            r = self.tool("f.csv", "--where", text, "--group", "a", "--agg", "count,sum:n", "--limit", "1000000")
            got = [(x[0], int(x[1]), int(x[2])) for x in doc_of(r)["data"]["rows"]]
            self.assertEqual(got, [(a, g[0], g[1]) for a, g in sorted(groups.items(), key=lambda kv: kv[0].encode())], text)
            r = self.tool("f.csv", "--where", text, "--order-by", "-n:int", "--top", "5", "--select", "id")
            ordered = sorted(kept, key=lambda k: -k[2])[:5]
            self.assertEqual([x[0] for x in doc_of(r)["data"]["rows"]], [k[0] for k in ordered], text)
            r = self.tool("f.csv", "--where", text, "--report", "types", "--select", "id")
            self.assertEqual(int(doc_of(r)["data"]["rows"][0][1]), len(kept), text)
            data = (self.s.dir / "f.csv").read_bytes()
            via = run("--where", text, "--select", "id", "--limit", "1000000", stdin=data)
            self.assertEqual([x[0] for x in doc_of(via)["data"]["rows"]], want_ids, text)
            lines = "".join(json.dumps(dict(zip(O.HEADER, r_))) + "\n" for r_ in rows).encode()
            self.s.write("f.jsonl", lines)
            j = run("--root", self.dir, "--input-format", "jsonl", "--fields", ",".join(c.replace(".", "\\.") for c in O.HEADER), "--where", text, "--select", "id", "--limit", "1000000", "f.jsonl")
            self.assertEqual([x[0] for x in doc_of(j)["data"]["rows"]], want_ids, text)
            out = run("--root", self.dir, "--where", text, "--select", "id", "--format", "jsonl", "f.csv")
            self.assertEqual([json.loads(l)["id"] for l in out.stdout.decode().splitlines()], want_ids, text)

    def test_the_expression_is_evaluated_on_the_joined_row(self):
        rng = random.Random(20261109)
        probe = [[str(i), rng.choice(["x", "y", "z", ""]), "k%d" % rng.randrange(6), rng.choice(["1", "2", "7", "-3"])] for i in range(60)]
        build = [["k%d" % rng.randrange(7), rng.choice(["p", "q", "", "pq"]), rng.choice(["5", "9", "x", ""])] for _ in range(14)]
        self.put("pj.csv", table_csv(probe, rng, ["pid", "a", "k", "n"]))
        self.put("bj.csv", table_csv(build, rng, ["bk", "b", "m"]))
        joined = [p + b for p in probe for b in build if p[2] == b[0]]
        header = ["pid", "a", "k", "n", "bk", "b", "m"]
        self.assertGreater(len(joined), 40)
        cases = [
            ("not (a = x and b = p)", ("not", [("and", [("leaf", {"column": "a", "kind": "cmp", "op": "=", "lit": "x"}), ("leaf", {"column": "b", "kind": "cmp", "op": "=", "lit": "p"})])])),
            ("a not in (x, y) or b not contains p", ("or", [("leaf", {"column": "a", "kind": "in", "lits": ["x", "y"], "neg": True}), ("leaf", {"column": "b", "kind": "contains", "lit": "p", "neg": True})])),
            ("not (m = '' or n:int > 1) and bk not in (k1)", ("and", [("not", [("or", [("leaf", {"column": "m", "kind": "cmp", "op": "=", "lit": ""}), ("leaf", {"column": "n", "int": True, "kind": "cmp", "op": ">", "lit": "1"})])]),
                                                                         ("leaf", {"column": "bk", "kind": "in", "lits": ["k1"], "neg": True})])),
        ]
        for where, tree in cases:
            want = [r[0] + "/" + r[5] for r in joined if refimpl.eval_tree(tree, lambda c, r=r: r[header.index(c)]) is True]
            for extra in ([], ["--threads", "4", "--chunk-bytes", "64", "--parallel-min-bytes", "0"]):
                r = run("--root", self.dir, "--join", "bj.csv", "--on", "k=bk", "--where", where, "--select", "pid,b", "--limit", "100000", "--format", "csv", *extra, "pj.csv")
                self.assertEqual(r.status, 0, (where, r))
                got = [row[0] + "/" + row[1] for row in csv.reader(io.StringIO(r.stdout.decode()))][1:]
                self.assertEqual(got, want, where)

    def test_a_typed_cell_in_json_lines_is_the_exact_text_of_the_number(self):
        self.s.write("t.jsonl", b'{"id": "1", "n": 5, "s": "x"}\n{"id": "2", "n": 12, "s": "y"}\n{"id": "3", "n": 7.5, "s": "x"}\n')
        r = run("--root", self.dir, "--input-format", "jsonl", "--fields", "id,n,s", "--where", "not (s = y or (s = x and n:int > 4))", "--select", "id", "t.jsonl")
        d = doc_of(r)
        self.assertEqual((d["error"]["rule"], d["error"]["detail"]["row"]), ("value.not-integer", 3))
        r = run("--root", self.dir, "--input-format", "jsonl", "--fields", "id,n,s", "--where", "s not in (y) and n:float not in (5)", "--select", "id", "t.jsonl")
        self.assertEqual([x[0] for x in doc_of(r)["data"]["rows"]], ["3"])

    def test_a_column_called_not_or_and_and_and_in_every_place(self):
        self.put("k.csv", "or,and,not,in,contains,x\n1,2,3,4,5,6\n7,8,9,10,11,12\n")
        cases = {
            "not = 3": [["1"]], "not != 3": [["7"]], "not in (3)": [["1"]], "not not in (3)": [["7"]], "not contains 3": [["1"]], "not not contains 3": [["7"]],
            "not (not = 3)": [["7"]], "not (not in (3, 9))": [], "not (not not in (3, 9))": [["1"], ["7"]], "not (or = 1) and not = 9": [["7"]],
            "(not = 3) or (not = 9)": [["1"], ["7"]], "or = 1 and not (and = 8)": [["1"]], "or = 1 or not (and = 8)": [["1"]], "not not in (9) and or not in (7)": [["1"]],
            "'not' = 3": [["1"]], "'not' not in (3)": [["7"]], "not = 3 and not not in (9)": [["1"]], "in not in (4)": [["7"]], "contains not in (5, 11)": [], "contains not contains 5": [["7"]],
            "not :int = 3": None, "not (": None, "not a = 1": None, "not 'x' = 1": None, "not not = 1": None, "(not = 1)": [], "not(and = 8)": [["1"]],
        }
        for where, want in cases.items():
            d = doc_of(run("--root", self.dir, "--where", where, "--select", "or", "k.csv"))
            if want is None:
                self.assertFalse(d.get("ok"), where)
            else:
                self.assertEqual(d["data"]["rows"], want, where)


class Mcp(unittest.TestCase):
    def setUp(self):
        M.BUILT.clear()           # test_mcp's tearDownModule has removed the server it built when it ran first: build it again

    def tearDown(self):
        M.tearDownModule()

    def test_a_not_is_the_same_through_the_server_as_on_the_command_line(self):
        wheres = ["not (status = 404)", "status not in (200, 404)", "path not contains /p/1", "not (status = 404 or bytes:int > 90000) and id:int < 40", "not (note:int > 1)", "status not in (200) or not (id:int < 5000)",
                  "not a = 1", "bytes:int not contains 5"]
        calls = [M.call({"file": "data.csv", "select": "id,status", "where": w, "limit": 8}, n) for n, w in enumerate(wheres)]
        responses, status = M.talk(M.root(), calls)
        self.assertEqual(status, 0)
        kinds = set()
        for w, r in zip(wheres, responses):
            direct = M.cli(M.root(), {"file": "data.csv", "select": "id,status", "where": w, "limit": 8})
            res = r["result"]
            self.assertEqual(res["content"][0]["text"].encode(), direct.stdout, w)
            self.assertEqual(res["_meta"]["exit_code"], direct.status, w)
            kinds.add(direct.status)
        self.assertEqual(kinds, {0, 2, 8})
        # the description of the tool says `not`, the schema is introspect's
        tools = M.talk(M.root(), [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])[0][0]["result"]["tools"]
        where = [t for t in tools if t["name"] == "table"][0]["inputSchema"]["properties"]["where"]["description"]
        self.assertIn("not", where)


if __name__ == "__main__":
    unittest.main()
