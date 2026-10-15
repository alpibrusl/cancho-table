"""`--group` by any number of columns: the answer, or a tagged refusal, never a signal.

The defect this gate is for: `table --group a,b,c,d,e --agg sum:v g5.csv` exited 132 (SIGILL, a trap, no output) at any size, on a one-row file, where four columns
worked. The cause is not a width of the plan or of the group state (those are vectors): `agg.add_fast` looks at each group cell (its length, first byte and last byte) to
choose a place in a small cache of groups, and it folded them into one integer with `look * 31 + ...` three times a column, in checked arithmetic. The sum passes 2^63 at the
fifth column whatever the cells are (31^14 > 2^69, so one cell of one byte is enough), and at the fourth when the first cell is about 370 bytes long: the compiler traps
an overflow of `*`. So the failure depends on the number of group columns AND on the length of the first cell, and the same plan with four short cells worked.

What is held here, for 1 to 64 group columns (and a fuzz of 1 to 200 columns with random flag soups, in the last class):

* the answer equals SQLite's `GROUP BY` of the same table (tests/conformance/group_ref.py), for the columns named by header name, by `#N`, in any order, with a column
  more than once, with typed keys (`:int`, `:dec(S)`, `:float`) mixed with text ones, with the aggregates count, sum, min, max and distinct;
* the same bytes as json, csv and JSON lines, from a file, from standard input (named `-` and bare), from JSON lines input, with `--threads 1` to 8 and tiny `--chunk-bytes`,
  with `--where`, `--sort`, `--top`, `--limit` and `--from`;
* `--query ... group by` and `--explain` agree with the flags (the same bytes, the same output columns);
* a bound that is reached (`--max-groups`, `--max-state-bytes`, `--max-distinct`) is its tagged refusal at any number of columns;
* the first cell of a group may be as long as the line allows: the length of a cell is not a hazard (the cache key is a hash, not a number that can overflow).

The status of every run here is judged first: a signal (132, 134, 136, 139 or a negative status) fails the test whatever else it says.
"""

import csv
import io
import itertools
import json
import os
import random
import re
import unittest

import group_ref as G
from harness import Scratch, TRAPS, binary, introspect, run, run_argv, validate

TEXT_POOL = ["", "a", "b", "ab", "B", "a b", "x,y", 'q"r', "é", "zz"]
INT_POOL = ["0", "1", "-1", "007", "7", "+5", "12", "-0"]
DEC_POOL = ["0", "1.5", "1.50", "2", "-0.5", "-0.50", "01.25", "0.00", "-0", "3.75"]
FLOAT_POOL = ["0", "-0", "1.5", "1.50", "2", "-0.5", "0.25", "1e2", "100", "3.125"]


def pool_of(kind):
    if kind == G.TEXT:
        return TEXT_POOL
    if kind == G.INT:
        return INT_POOL
    if kind == G.FLOAT:
        return FLOAT_POOL
    return DEC_POOL


def csv_bytes(header, rows):
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode("utf-8")


def jsonl_bytes(header, rows):
    return b"".join(json.dumps(dict(zip(header, r)), ensure_ascii=False).encode("utf-8") + b"\n" for r in rows)


class Table:
    """A generated table: `width` key columns k0..k{width-1} (each of a kind, its cells from that kind's pool), then `v` (an integer) and `w` (text)."""

    def __init__(self, rng, width, kinds=None, nrows=40, prototypes=6):
        self.kinds = list(kinds) if kinds is not None else [G.TEXT] * width
        self.header = ["k%d" % i for i in range(width)] + ["v", "w"]
        protos = [[rng.choice(pool_of(k)) for k in self.kinds] for _ in range(prototypes)]
        self.rows = []
        for _ in range(nrows):
            cells = list(rng.choice(protos))
            if rng.random() < 0.25 and width:
                j = rng.randrange(width)
                cells[j] = rng.choice(pool_of(self.kinds[j]))
            self.rows.append(cells + [str(rng.randint(-50, 50)), rng.choice(["p", "q", "r", ""])])

    def keys(self, picks, refs="name"):
        """The group list for the key columns `picks` (indexes), named by header name, by `#N`, or a mix."""
        out = []
        for n, j in enumerate(picks):
            by_position = refs == "position" or (refs == "mixed" and n % 2)
            out.append(("#%d" % (j + 1) if by_position else self.header[j], self.kinds[j]))
        return out

    def data(self):
        return csv_bytes(self.header, self.rows)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def go(self, data, flags, name="t.csv", stdin=None, label=""):
        """One run, with the signal judged first. A file is read with cwd = the scratch directory."""
        if stdin is None:
            self.s.write(name, data)
            got = run(*flags, name, cwd=str(self.s.dir))
        else:
            got = run(*flags, *(["-"] if stdin == "-" else []), stdin=data, cwd=str(self.s.dir))
        self.assertNotIn(got.status, TRAPS, "a signal (status %d) for %s: %r" % (got.status, label, [str(a) for a in flags][:12]))
        self.assertGreaterEqual(got.status, 0, label)
        return got

    def go_query(self, data, flags, name="t.csv", label=""):
        """A run whose FILE is in the query (`from 't.csv'`): no operand."""
        self.s.write(name, data)
        got = run(*flags, cwd=str(self.s.dir))
        self.assertNotIn(got.status, TRAPS, "a signal (status %d) for %s: %r" % (got.status, label, [str(a) for a in flags][:12]))
        self.assertGreaterEqual(got.status, 0, label)
        return got

    def expect_rows(self, got, columns, rows, label="", flags=None):
        self.assertEqual(got.status, 0, (label, got.stdout[:300]))
        self.assertEqual(validate(got), [], label)
        d = got.data()
        self.assertEqual(d["columns"], columns, label)
        self.assertEqual(d["rows"][:len(rows)], rows[:len(d["rows"])], label)
        self.assertEqual(d["group_count"], len(rows), label)
        return d


AGGS = ["count", "sum:v", "min:v", "max:v"]

REFUSAL = re.compile(r"^table: ([a-z]+(?:[.-][a-z0-9]+)+): ")


def refusal(got):
    """(rule, problems) of a refused run: the tagged JSON line on standard output (json), or `table: RULE: message` on standard error with nothing on standard output
    (csv, jsonl, text; standard output then holds the rows written before it, if any). The problems are what is wrong with it: a rule the tool does not have, an exit status that is not the rule's, a document that is not the schema's."""
    rules = {r["rule"]: r["exit"] for r in introspect()["rules"]}
    if got.stdout.startswith(b"{"):
        problems = validate(got)
        try:
            rule = got.doc()["error"]["rule"]
        except Exception:
            return None, problems + ["no tagged refusal on standard output: %r" % got.stdout[:200]]
    else:
        # (csv and JSON lines may have written the rows before the bound was reached: the refusal is the last line of standard error)
        m = REFUSAL.match(got.stderr.decode("utf-8", "replace").rstrip("\n").split("\n")[-1])
        if not m:
            return None, ["status %d with no tagged refusal: stdout %r stderr %r" % (got.status, got.stdout[:100], got.stderr[:200])]
        rule, problems = m.group(1), []
    if rule not in rules:
        problems.append("rule %s is not in introspect" % rule)
    elif rules[rule] != got.status:
        problems.append("rule %s has exit %d in introspect, the run exited %d" % (rule, rules[rule], got.status))
    return rule, problems


def agg_flag(aggs=AGGS):
    return ",".join(aggs)


class Widths(Base):
    def test_one_to_sixty_four_columns(self):
        """Every number of group columns from 1 to 64, the first columns of a table of that width, text keys: the answer of SQLite."""
        for n in range(1, 65):
            rng = random.Random(1000 + n)
            t = Table(rng, n)
            keys = t.keys(range(n))
            cols, rows = G.answer(t.header, t.rows, keys)
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label="%d columns" % n)
            self.expect_rows(got, cols, rows, "%d columns" % n)

    def test_the_count_without_an_aggregate(self):
        """`--group` alone counts, at the number of columns that used to trap."""
        for n in (4, 5, 6, 7, 8, 12, 33, 64):
            t = Table(random.Random(n), n)
            keys = t.keys(range(n))
            cols, rows = G.answer(t.header, t.rows, keys, ["count"])
            got = self.go(t.data(), ["--group", G.group_flag(keys)], label="count %d" % n)
            self.expect_rows(got, cols, rows, "count %d" % n)

    def test_five_columns_one_row(self):
        """The reproduction of the report: five one-byte cells, one data row (and the same file as the issue wrote it)."""
        got = self.go(b"a,b,c,d,e,v\nx,x,x,x,x,1\n", ["--group", "a,b,c,d,e", "--agg", "sum:v"])
        self.assertEqual((got.status, got.data()["rows"], got.data()["group_count"]), (0, [["x", "x", "x", "x", "x", "1"]], 1))
        got = self.go(b"a,b,c,d,e,f,g,v\nx,x,x,x,x,x,x,1\n", ["--group", "a,b,c,d,e,f", "--agg", "sum:v,count"])
        self.assertEqual((got.status, got.data()["rows"]), (0, [["x", "x", "x", "x", "x", "x", "1", "1"]]))

    def test_no_row_and_empty_cells(self):
        for n in (1, 4, 5, 9):
            header = ["k%d" % i for i in range(n)] + ["v"]
            got = self.go(csv_bytes(header, []), ["--group", ",".join(header[:n])], label="no row")
            self.assertEqual((got.status, got.data()["rows"], got.data()["group_count"]), (0, [], 0))
            got = self.go(csv_bytes(header, [[""] * n + ["1"]] * 3), ["--group", ",".join(header[:n]), "--agg", "sum:v"], label="empty cells")
            self.assertEqual((got.status, got.data()["rows"]), (0, [[""] * n + ["3"]]))


class First(Base):
    def test_the_length_of_the_first_cell_is_not_a_hazard(self):
        """The hash of the look at the cells overflowed at 4 columns when the first cell was about 370 bytes long, and at 3 when it was about 11 million: 1 to 8 columns, a first
        cell of every length that matters, the others short; then the last cell long, then every cell long."""
        for n in range(1, 9):
            for width in (0, 1, 100, 369, 371, 372, 400, 1000, 5000, 70000):
                for where in ("first", "last", "all"):
                    cells = [("x" * width if (where == "all" or (where == "first" and j == 0) or (where == "last" and j == n - 1)) else "y") for j in range(n)]
                    header = ["k%d" % i for i in range(n)] + ["v"]
                    rows = [cells + ["2"], cells + ["3"], ["z"] * n + ["4"]]
                    keys = [(h, G.TEXT) for h in header[:n]]
                    cols, want = G.answer(header, rows, keys, ["count", "sum:v"])
                    got = self.go(csv_bytes(header, rows), ["--max-line-bytes", 16777216, "--max-bytes", 67108864, "--group", G.group_flag(keys), "--agg", "count,sum:v"], label="n=%d width=%d %s" % (n, width, where))
                    self.expect_rows(got, cols, want, "n=%d width=%d %s" % (n, width, where))

    def test_a_cell_of_a_few_megabytes_in_the_first_of_three_columns(self):
        """The 3-column form of the same overflow, 31^8 * length > 2^63 at 11 million bytes: a cell of 12 MB (the line ceiling is 16 MB)."""
        big = "x" * 12_000_000
        header = ["a", "b", "c", "v"]
        rows = [[big, "y", "z", "1"], [big, "y", "z", "2"]]
        got = self.go(csv_bytes(header, rows), ["--max-line-bytes", 16777216, "--max-bytes", 67108864, "--group", "a,b,c", "--agg", "sum:v", "--format", "csv"], label="12MB")
        self.assertNotIn(got.status, TRAPS)
        self.assertIn(got.status, (0, 8), got.stdout[:200])
        if got.status == 0:
            self.assertEqual(got.stdout, ("a,b,c,sum:v\n%s,y,z,3\n" % big).encode())
        else:
            self.assertEqual(validate(got), [])


class Spellings(Base):
    def test_names_positions_order_and_repeats(self):
        """Columns named by header name, by #N, mixed; in a shuffled order; a column more than once; from a table of 70 columns, 1 to 64 of them."""
        rng = random.Random(2)
        t = Table(rng, 70, nrows=30)
        for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64):
            for refs in ("name", "position", "mixed"):
                picks = rng.sample(range(70), n)
                keys = t.keys(picks, refs)
                cols, rows = G.answer(t.header, t.rows, keys)
                got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label="%d %s" % (n, refs))
                self.expect_rows(got, cols, rows, "%d %s" % (n, refs))

    def test_a_column_more_than_once(self):
        rng = random.Random(3)
        t = Table(rng, 8, nrows=30)
        for picks in ([0, 0, 1, 2, 3], [1, 1, 1, 1, 1, 1], [0, 1, 2, 3, 0], [4, 3, 2, 1, 0, 4, 3, 2, 1, 0], [5] * 64, [0, 1] * 6, [7, 6, 5, 4, 3, 2, 1, 0, 7]):
            for refs in ("name", "mixed"):
                keys = t.keys(picks, refs)
                cols, rows = G.answer(t.header, t.rows, keys)
                got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label=str(picks))
                self.expect_rows(got, cols, rows, str(picks))

    def test_the_other_columns_are_not_the_keys(self):
        """The key columns are anywhere among the others (v, w and unused ones before, between and after)."""
        rng = random.Random(4)
        t = Table(rng, 12, nrows=40)
        for picks in ([11, 3, 7, 0, 5], [2, 4, 6, 8, 10, 0], [9, 8, 7, 6, 5, 4, 3]):
            keys = t.keys(picks)
            for aggs in (["count"], ["sum:v"], ["distinct:w", "count"], ["min:v", "max:v", "sum:v", "count", "distinct:w"]):
                cols, rows = G.answer(t.header, t.rows, keys, aggs)
                got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(aggs)], label=str((picks, aggs)))
                self.expect_rows(got, cols, rows, str((picks, aggs)))


class Typed(Base):
    def test_typed_and_text_keys_mixed(self):
        """Every kind in the same list (text, :int, :dec(2), :dec(3), :float): groups by value, in the order of the values, 1 to 64 columns."""
        kinds_all = [G.TEXT, G.INT, G.dec(2), G.FLOAT, G.dec(3)]
        for n in (1, 2, 3, 4, 5, 6, 7, 8, 11, 16, 33, 64):
            for seed in range(3):
                rng = random.Random(100 * n + seed)
                kinds = [rng.choice(kinds_all) for _ in range(n)]
                t = Table(rng, n, kinds, nrows=40)
                keys = t.keys(range(n), "mixed")
                cols, rows = G.answer(t.header, t.rows, keys)
                got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label="typed %d/%d" % (n, seed))
                self.expect_rows(got, cols, rows, "typed %d/%d %s" % (n, seed, G.group_flag(keys)))

    def test_each_kind_alone_at_six_columns(self):
        for kind in (G.INT, G.dec(2), G.FLOAT, G.TEXT):
            rng = random.Random(7)
            t = Table(rng, 6, [kind] * 6, nrows=50)
            keys = t.keys(range(6))
            cols, rows = G.answer(t.header, t.rows, keys)
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label=str(kind))
            self.expect_rows(got, cols, rows, str(kind))

    def test_a_cell_that_is_not_of_the_kind_is_the_refusal_of_four_columns(self):
        """At five and at 64 columns a bad typed cell is `value.not-integer` naming the row and the column, as at one."""
        for n in (1, 4, 5, 6, 64):
            rng = random.Random(n)
            t = Table(rng, n, [G.INT] * n, nrows=20)
            t.rows[11][n - 1] = "x"
            keys = t.keys(range(n))
            got = self.go(t.data(), ["--group", G.group_flag(keys)], label="bad int %d" % n)
            self.assertEqual(got.status, 8, got.stdout[:300])
            self.assertEqual(validate(got), [])
            e = got.error()
            self.assertEqual(e["rule"], "value.not-integer")
            self.assertEqual((e["detail"]["row"], e["detail"]["column"]), (12, "k%d" % (n - 1)))

    def test_one_column_with_two_types_is_a_conflict(self):
        got = self.go(csv_bytes(["a", "b", "c", "d", "e", "v"], [list("abcde") + ["1"]]), ["--group", "a:int,b,c,d,e,a:float"])
        self.assertEqual((got.status, got.error()["rule"]), (2, "column.type-conflict"))


class Outputs(Base):
    def table5(self, n=6, kinds=None, nrows=40, seed=11):
        t = Table(random.Random(seed), n, kinds, nrows=nrows)
        keys = t.keys(range(n))
        cols, rows = G.answer(t.header, t.rows, keys)
        return t, keys, cols, rows

    def test_csv(self):
        for n, kinds in ((5, None), (6, [G.TEXT, G.INT, G.dec(2), G.FLOAT, G.TEXT, G.INT]), (9, None), (40, None)):
            t, keys, cols, rows = self.table5(n, kinds)
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--format", "csv"], label="csv %d" % n)
            self.assertEqual(got.status, 0)
            self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode("utf-8"), newline=""))), [cols] + rows)

    def test_jsonl(self):
        for n, kinds in ((5, None), (6, [G.TEXT, G.INT, G.dec(2), G.FLOAT, G.TEXT, G.INT]), (9, None), (40, None)):
            t, keys, cols, rows = self.table5(n, kinds)
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--format", "jsonl"], label="jsonl %d" % n)
            self.assertEqual(got.status, 0, got.stdout[:300])
            lines = got.stdout.decode("utf-8").splitlines()
            self.assertEqual(len(lines), len(rows))
            for line, row in zip(lines, rows):
                # a typed key is a JSON number (its text is the number's), an untyped one a string; an aggregate is a number
                text = json.loads(line, object_pairs_hook=lambda p: p, parse_int=str, parse_float=str)
                plain = json.loads(line, object_pairs_hook=lambda p: p)
                self.assertEqual([k for k, _ in text], cols)
                self.assertEqual([v for _, v in text], row)
                self.assertTrue(all(isinstance(v, int) for _, v in plain[n:]))
                for (_, v), kind in zip(plain[:n], t.kinds):
                    self.assertEqual(isinstance(v, str), kind == G.TEXT, (line[:200], kind))

    def test_jsonl_with_a_column_twice_is_the_refusal_not_a_signal(self):
        t = Table(random.Random(1), 6, nrows=10)
        got = self.go(t.data(), ["--group", "k0,k1,k2,k3,k4,k0", "--format", "jsonl"])
        self.assertEqual((got.status, refusal(got)), (2, ("output.duplicate-column", [])))

    def test_threads_and_chunks(self):
        """--threads 1 to 8 with tiny chunks: the one-core bytes, and the oracle's rows."""
        for n in (5, 6, 9):
            t, keys, cols, rows = self.table5(n, nrows=120, seed=n)
            data = t.data()
            base = self.go(data, ["--group", G.group_flag(keys), "--agg", agg_flag()], label="threads base")
            self.expect_rows(base, cols, rows)
            for threads, chunk in itertools.product((1, 2, 3, 4, 5, 6, 7, 8), (1, 7, 64, 500)):
                got = self.go(data, ["--group", G.group_flag(keys), "--agg", agg_flag(), "--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0], label="threads %d chunk %d" % (threads, chunk))
                self.assertEqual((got.status, got.stdout), (base.status, base.stdout), "n=%d threads %d chunk %d" % (n, threads, chunk))

    def test_threads_csv_and_sort(self):
        t, keys, cols, rows = self.table5(7, nrows=150, seed=5)
        for fmt in ("csv", "jsonl", "json"):
            args = ["--group", G.group_flag(keys), "--agg", agg_flag(), "--sort", "-count", "--top", 5, "--format", fmt]
            base = self.go(t.data(), args)
            for threads, chunk in ((2, 33), (4, 1), (8, 100)):
                got = self.go(t.data(), args + ["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0])
                self.assertEqual((got.status, got.stdout), (base.status, base.stdout), (fmt, threads, chunk))

    def test_standard_input(self):
        """The file's answer, byte for byte but for the path, through `-` and through no operand."""
        for n in (5, 6, 12):
            t, keys, cols, rows = self.table5(n, nrows=60, seed=n)
            args = ["--group", G.group_flag(keys), "--agg", agg_flag(), "--format", "csv"]
            want = self.go(t.data(), args)
            for operand in ("-", "bare"):
                got = self.go(t.data(), args, stdin=operand)
                self.assertEqual((got.status, got.stdout), (want.status, want.stdout), (n, operand))
            got = self.go(t.data(), args[:-2], stdin="-")
            self.expect_rows(got, cols, rows)

    def test_json_lines_input(self):
        for n in (5, 6, 10):
            t, keys, cols, rows = self.table5(n, nrows=50, seed=n + 3)
            fields = ",".join(t.header)
            args = ["--input-format", "jsonl", "--fields", fields, "--group", G.group_flag(keys), "--agg", agg_flag()]
            got = self.go(jsonl_bytes(t.header, t.rows), args, name="t.jsonl", label="jsonl in %d" % n)
            self.expect_rows(got, cols, rows, "jsonl in %d" % n)
            via = self.go(jsonl_bytes(t.header, t.rows), args, stdin="-", label="jsonl stdin")
            self.assertEqual(via.data(), got.data())
            thr = self.go(jsonl_bytes(t.header, t.rows), args + ["--threads", 3, "--chunk-bytes", 50, "--parallel-min-bytes", 0], name="t.jsonl")
            self.assertEqual(thr.stdout.replace(b'"--threads","3","--chunk-bytes","50","--parallel-min-bytes","0",', b""), got.stdout)

    def test_tsv_and_where(self):
        t = Table(random.Random(8), 6, nrows=80)
        keys = t.keys(range(6))
        data = t.data().replace(b",", b"\t") if all("," not in c and '"' not in c for r in t.rows for c in r) else None
        cond = lambda r: r[0] != "a" and r[6] != "0"
        cols, rows = G.answer(t.header, t.rows, keys, where=cond)
        got = self.go(t.data(), ["--where", "k0 != a and v != 0", "--group", G.group_flag(keys), "--agg", agg_flag()])
        self.expect_rows(got, cols, rows)
        if data is not None:
            got = self.go(data, ["--delimiter", "tab", "--group", G.group_flag(keys), "--agg", agg_flag()])
            cols, rows = G.answer(t.header, t.rows, keys)
            self.expect_rows(got, cols, rows)


class Pages(Base):
    def test_sort_top_limit_from(self):
        t = Table(random.Random(21), 6, nrows=120, prototypes=30)
        keys = t.keys(range(6))
        cols, rows = G.answer(t.header, t.rows, keys)
        self.assertGreater(len(rows), 12)
        for label in ("count", "-count", "sum:v", "-sum:v", "k0", "-k0"):
            desc = label.startswith("-")
            col = cols.index(label.lstrip("-"))
            numeric = label.lstrip("-") != "k0"
            # ties keep the order of the keys (a stable sort of the groups as the grouping lists them)
            want = sorted(rows, key=lambda r: int(r[col]) if numeric else r[col], reverse=desc)
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--sort", label, "--limit", 1000])
            self.assertEqual(got.status, 0, got.stdout[:300])
            self.assertEqual(got.data()["rows"], want, label)
            top = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--sort", label, "--top", 4])
            self.assertEqual(top.data()["rows"], want[:4], label)
            self.assertEqual(top.data()["group_count"], len(rows))

    def test_limit_and_from_page_through_the_groups(self):
        t = Table(random.Random(22), 5, nrows=100, prototypes=40)
        keys = t.keys(range(5))
        cols, rows = G.answer(t.header, t.rows, keys)
        seen, at = [], 0
        while True:
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--limit", 7, "--from", at])
            self.assertEqual(got.status, 0)
            d = got.data()
            seen += d["rows"]
            if not d["truncated"]:
                break
            at = d["next"]["from"]
        self.assertEqual(seen, rows)

    def test_where_that_keeps_no_row(self):
        t = Table(random.Random(23), 6, nrows=20)
        got = self.go(t.data(), ["--where", "k0 = no-such-value", "--group", "k0,k1,k2,k3,k4,k5"])
        self.assertEqual((got.status, got.data()["rows"], got.data()["group_count"]), (0, [], 0))


class Many(Base):
    """Many groups, so that the tables grow, the threads each hold their own and merge, the groups are partitioned and sorted in parallel."""

    def make(self, n, rows=30000, groups=4000, seed=1, kinds=None):
        rng = random.Random(seed * 1000 + n)
        kinds = kinds or [G.TEXT] * n
        header = ["k%d" % i for i in range(n)] + ["v", "w"]
        pool = [[rng.choice(pool_of(k)) + (str(rng.randint(0, 40)) if k == G.TEXT else "") for k in kinds] for _ in range(groups)]
        body = [list(rng.choice(pool)) + [str(rng.randint(-9, 9)), rng.choice(["p", "q", ""])] for _ in range(rows)]
        t = Table(rng, n, kinds, nrows=0)
        t.header, t.rows = header, body
        return t

    def test_many_groups_threads_and_sorts(self):
        for n in (4, 5, 6, 9, 16, 33):
            t = self.make(n)
            keys = t.keys(range(n))
            cols, rows = G.answer(t.header, t.rows, keys)
            self.assertGreater(len(rows), 1000)
            base = ["--group", G.group_flag(keys), "--agg", agg_flag(), "--max-groups", 100000]
            one = self.go(t.data(), base + ["--format", "csv"], label="many %d" % n)
            self.assertEqual(list(csv.reader(io.StringIO(one.stdout.decode(), newline=""))), [cols] + rows, n)
            for threads in (2, 4, 8):
                got = self.go(t.data(), base + ["--format", "csv", "--threads", threads, "--chunk-bytes", 65536, "--parallel-min-bytes", 0], label="many %d threads %d" % (n, threads))
                self.assertEqual((got.status, got.stdout), (0, one.stdout), (n, threads))
            for label in ("-count", "sum:v", "-k0"):
                col = cols.index(label.lstrip("-"))
                numeric = label.lstrip("-") != "k0"
                want = sorted(rows, key=lambda r: int(r[col]) if numeric else r[col], reverse=label.startswith("-"))[:25]
                for threads in (1, 4):
                    extra = [] if threads == 1 else ["--threads", threads, "--chunk-bytes", 65536, "--parallel-min-bytes", 0]
                    got = self.go(t.data(), base + ["--sort", label, "--top", 25] + extra, label="many sorted %d" % n)
                    self.assertEqual(got.status, 0, got.stdout[:300])
                    self.assertEqual(got.data()["rows"], want, (n, label, threads))
                    self.assertEqual(got.data()["group_count"], len(rows))

    def test_many_typed_groups(self):
        kinds = [G.INT, G.TEXT, G.dec(2), G.FLOAT, G.TEXT, G.INT, G.dec(3)]
        t = self.make(7, rows=20000, groups=3000, seed=3, kinds=kinds)
        keys = t.keys(range(7))
        cols, rows = G.answer(t.header, t.rows, keys)
        for extra in ([], ["--threads", 4, "--chunk-bytes", 65536, "--parallel-min-bytes", 0]):
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--max-groups", 100000, "--format", "csv"] + extra)
            self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode(), newline=""))), [cols] + rows)

    def test_sixty_four_columns_sorted_and_threaded(self):
        t = self.make(64, rows=3000, groups=500, seed=5)
        keys = t.keys(range(64))
        cols, rows = G.answer(t.header, t.rows, keys)
        want = sorted(rows, key=lambda r: -int(r[64]))[:5]
        for extra in ([], ["--threads", 4, "--chunk-bytes", 4096, "--parallel-min-bytes", 0]):
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag(), "--sort", "-count", "--top", 5] + extra)
            self.assertEqual(got.status, 0, got.stdout[:300])
            self.assertEqual(got.data()["rows"], want)


class Forms(Base):
    def query_for(self, keys, header, aggs, table="t.csv"):
        names = [header[G.column_of(header, n)] for n, _ in keys]
        sel = names + [("count(*)" if a == "count" else a.replace(":", "(") + ")") for a in aggs]
        return "select %s from '%s' group by %s" % (", ".join(sel), table, ", ".join(names))

    def test_query_form_is_the_flag_form(self):
        for n in (1, 3, 4, 5, 6, 7, 9, 16, 32):
            t = Table(random.Random(n), n, nrows=40)
            keys = t.keys(range(n))
            cols, rows = G.answer(t.header, t.rows, keys)
            flags = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label="flags %d" % n)
            sql = self.query_for(keys, t.header, AGGS)
            if len(sql) > 4000:
                continue
            q = self.go_query(t.data(), ["--query", sql], label="query %d" % n)
            self.expect_rows(q, cols, rows, "query %d" % n)
            self.assertEqual(q.stdout, flags.stdout, n)

    def test_query_with_an_order_and_a_limit(self):
        t = Table(random.Random(31), 6, nrows=80, prototypes=25)
        keys = t.keys(range(6))
        flags = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", "count,sum:v", "--sort", "-count", "--limit", 5])
        q = self.go_query(t.data(), ["--query", self.query_for(keys, t.header, ["count", "sum:v"], "t.csv") + " order by count(*) desc limit 5"])
        self.assertEqual((q.status, q.stdout), (flags.status, flags.stdout))
        self.assertEqual(flags.status, 0)

    def test_a_typed_key_in_a_query_is_the_refusal_that_names_the_flag(self):
        """Not built (docs/query.md section 15): a cast in `group by` is `query.unsupported`, at any number of columns, never a signal."""
        t = Table(random.Random(32), 6, nrows=10)
        q = self.go_query(t.data(), ["--query", "select k0, k1, k2, k3, k4, k5::int, count(*) from 't.csv' group by k0, k1, k2, k3, k4, k5::int"])
        self.assertEqual((q.status, refusal(q)), (2, ("query.unsupported", [])))

    def test_explain_agrees_with_the_run(self):
        """--explain names the same group columns and output columns as the run produces, from flags and from --query, at 1 to 64 columns."""
        for n in (1, 4, 5, 6, 8, 20, 64):
            t = Table(random.Random(n + 40), n, nrows=10)
            keys = t.keys(range(n), "mixed")
            run_ = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", agg_flag()], label="run")
            exp = self.go(t.data(), ["--explain", "--group", G.group_flag(keys), "--agg", agg_flag()], label="explain")
            self.assertEqual((exp.status, validate(exp)), (0, []), exp.stdout[:300])
            ex = exp.data()["explain"]
            self.assertEqual(len(ex["plan"]["group"]), n)
            self.assertEqual(ex["output"]["columns"], run_.data()["columns"])
            self.assertEqual(ex["plan"]["form"], "groups")
            sql = self.query_for(keys, t.header, AGGS)
            if len(sql) < 4000:
                eq = self.go_query(t.data(), ["--explain", "--query", sql], label="explain query")
                self.assertEqual(eq.status, 0, eq.stdout[:300])
                self.assertEqual(eq.data()["explain"]["output"]["columns"], run_.data()["columns"])
                self.assertEqual(len(eq.data()["explain"]["plan"]["group"]), n)

    def test_a_group_over_a_join_is_a_refusal_not_a_signal(self):
        t = Table(random.Random(5), 6, nrows=10)
        self.s.write("j.csv", t.data())
        got = self.go(t.data(), ["--join", "j.csv", "--on", "k0=k0", "--join-prefix", "r_", "--group", "k0,k1,k2,k3,k4,k5"])
        self.assertNotEqual(got.status, 132)
        self.assertEqual(validate(got), [])


class Bounds(Base):
    def test_max_groups(self):
        for n in (1, 4, 5, 6, 8, 30):
            t = Table(random.Random(n), n, nrows=60, prototypes=20)
            keys = t.keys(range(n))
            cols, rows = G.answer(t.header, t.rows, keys, ["count"])
            self.assertGreater(len(rows), 3)
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--max-groups", len(rows)], label="max-groups = groups")
            self.assertEqual(got.status, 0)
            for threads in (1, 4):
                extra = [] if threads == 1 else ["--threads", threads, "--chunk-bytes", 40, "--parallel-min-bytes", 0]
                got = self.go(t.data(), ["--group", G.group_flag(keys), "--max-groups", len(rows) - 1] + extra, label="max-groups below")
                self.assertEqual((got.status, validate(got), got.error()["rule"]), (8, [], "limit.too-many-groups"), (n, threads, got.stdout[:300]))
                self.assertEqual(got.error()["detail"]["limit"], len(rows) - 1)

    def test_state_and_distinct_bounds(self):
        for n in (4, 5, 6, 12):
            t = Table(random.Random(n + 9), n, nrows=60, prototypes=20)
            keys = t.keys(range(n))
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--max-state-bytes", 40], label="state")
            self.assertEqual((got.status, validate(got), got.error()["rule"]), (8, [], "limit.state-too-large"), (n, got.stdout[:300]))
            got = self.go(t.data(), ["--group", G.group_flag(keys), "--agg", "distinct:w", "--max-distinct", 1], label="distinct")
            self.assertEqual((got.status, validate(got), got.error()["rule"]), (8, [], "limit.too-many-distinct"), (n, got.stdout[:300]))

    def test_row_and_line_bounds(self):
        t = Table(random.Random(1), 6, nrows=30)
        for n in (4, 6):   # whatever --max-rows does to a grouping, it does at six columns what it does at four
            got = self.go(t.data(), ["--group", ",".join("k%d" % i for i in range(n)), "--max-rows", 5])
            if n == 4:
                four = (got.status, got.doc().get("error", {}).get("rule"))
            else:
                self.assertEqual((got.status, got.doc().get("error", {}).get("rule")), four)
        got = self.go(t.data(), ["--group", "k0,k1,k2,k3,k4,k5", "--max-line-bytes", 20])
        self.assertEqual(got.status, 8)
        self.assertEqual(refusal(got)[1], [])

    def test_unknown_and_ragged(self):
        t = Table(random.Random(2), 6, nrows=10)
        got = self.go(t.data(), ["--group", "k0,k1,k2,k3,k4,nope"])
        self.assertEqual((got.status, got.error()["rule"]), (3, "column.unknown"))
        got = self.go(t.data(), ["--group", "k0,k1,k2,k3,k4,#99"])
        self.assertEqual(validate(got), [])
        got = self.go(t.data() + b"a,b\n", ["--group", "k0,k1,k2,k3,k4,k5"])
        self.assertEqual((got.status, got.error()["rule"]), (8, "parse.csv-ragged-row"))

    def test_the_group_list_has_no_ceiling_of_its_own(self):
        """The wide file of the corpus (200 columns): all of them are keys, and 200 of 200 with a count."""
        n = 200
        header = ["c%d" % i for i in range(1, n + 1)]
        rng = random.Random(9)
        rows = [[rng.choice(["a", "b", "c"]) for _ in range(n)] for _ in range(30)] * 2
        keys = [(h, G.TEXT) for h in header]
        header_v = header + ["v"]
        rows_v = [r + ["1"] for r in rows]
        cols, want = G.answer(header_v, rows_v, keys, ["count"])
        got = self.go(csv_bytes(header_v, rows_v), ["--group", G.group_flag(keys)], label="200 columns")
        self.assertEqual(got.status, 0, got.stdout[:300])
        self.assertEqual(got.data()["rows"], want)


# ---- the fuzz ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

class Fuzz(Base):
    """Column counts 1 to 200 and random flag soups with --group: no signal, ever; a refusal is a tagged one; an answer is the oracle's when the soup has a plain
    form. GROUP_FUZZ_RUNS (default 300) is the number of soups, GROUP_FUZZ_SEED the seed."""

    FORMATS = ["json", "csv", "jsonl"]

    def soup(self, rng, t, n_header):
        n = len(t.kinds)
        picks = [rng.randrange(n) for _ in range(rng.randint(1, min(n, 12)))] if rng.random() < 0.3 else rng.sample(range(n), min(n, rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 33, 64, 100, 200])))
        keys = t.keys(picks, rng.choice(["name", "position", "mixed"]))
        flags = ["--group", G.group_flag(keys)]
        if rng.random() < 0.8:
            flags += ["--agg", agg_flag(rng.sample(["count", "sum:v", "min:v", "max:v", "distinct:w", "mean:v@2"], rng.randint(1, 4)))]
        pool = [
            ["--format", rng.choice(self.FORMATS)], ["--threads", str(rng.randint(1, 8)), "--chunk-bytes", str(rng.choice([1, 5, 64, 4096])), "--parallel-min-bytes", "0"],
            ["--where", rng.choice(["v != 0", "k0 = a", "k0 != a and v:int > -10", "v:int > 0 or v:int < -40"])], ["--sort", rng.choice(["count", "-count", "sum:v", "-sum:v", "nope"])], ["--top", str(rng.randint(0, 10))],
            ["--limit", str(rng.randint(0, 10))], ["--from", str(rng.randint(0, 10))], ["--max-groups", str(rng.choice([0, 1, 2, 5, 100000]))], ["--max-distinct", str(rng.choice([0, 1, 100]))],
            ["--max-state-bytes", str(rng.choice([0, 10, 100, 100000]))], ["--max-rows", str(rng.choice([0, 3, 100]))], ["--max-line-bytes", str(rng.choice([1, 20, 1000000]))],
            ["--max-bytes", str(rng.choice([1, 100, 1000000]))], ["--delimiter", rng.choice(["tab", ";", ","])], ["--explain"], ["--rows"], ["--select", "k0"], ["--order-by", "k0"],
            ["--report", "types"], ["--agg", "count"], ["--format", "text"], ["--group", "k0"],
        ]
        for extra in rng.sample(pool, rng.randint(0, 4)):
            flags += extra
        return keys, flags

    def test_soups(self):
        runs = int(os.environ.get("GROUP_FUZZ_RUNS", "300"))
        rng = random.Random(int(os.environ.get("GROUP_FUZZ_SEED", "20261015")))
        sizes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 33, 64, 100, 150, 200]
        bad = []
        counts = {"answer": 0, "refusal": 0}
        for i in range(runs):
            width = sizes[i % len(sizes)] if i < 3 * len(sizes) else rng.randint(1, 200)
            # a plan with a typed key never takes the cache that overflowed (every row takes `add`): most soups are text keys
            kinds = [G.TEXT] * width if rng.random() < 0.7 else [rng.choice([G.TEXT, G.TEXT, G.TEXT, G.INT, G.dec(2), G.FLOAT]) for _ in range(width)]
            t = Table(rng, width, kinds, nrows=rng.choice([0, 1, 2, 10, 40]))
            keys, flags = self.soup(rng, t, width)
            fmt = flags[flags.index("--format") + 1] if "--format" in flags else "json"
            input_format = rng.random() < 0.15
            if input_format:
                data, name = jsonl_bytes(t.header, t.rows), "t.jsonl"
                flags = ["--input-format", "jsonl", "--fields", ",".join(t.header)] + flags
            else:
                data, name = t.data(), "t.csv"
            self.s.write(name, data)
            stdin = rng.random() < 0.1
            got = run(*flags, *([] if stdin else [name]), cwd=str(self.s.dir), stdin=data if stdin else None)
            tag = "run %d width %d: %s" % (i, width, " ".join(str(f) for f in flags)[:400])
            if got.status in TRAPS or got.status < 0:
                bad.append("%s -> status %d" % (tag, got.status))
                continue
            if got.status != 0:
                rule, problems = refusal(got)
                if problems:
                    bad.append("%s -> status %d %s" % (tag, got.status, problems))
                counts["refusal"] += 1
                continue
            counts["answer"] += 1
            if fmt == "json" or "--explain" in flags:
                problems = validate(got)
                if problems:
                    bad.append("%s -> %s" % (tag, problems))
        self.assertEqual(bad, [], "\n".join(bad[:20]))
        self.assertGreater(counts["answer"], runs // 5, counts)

    def test_the_corpus_of_widths(self):
        """Every number of columns from 1 to 200, as the whole header and as keys, three kinds of cell: no signal."""
        rng = random.Random(77)
        for n in range(1, 201):
            width = rng.choice(["", "x", "x" * 40])
            header = ["c%d" % i for i in range(n)]
            rows = [[width + rng.choice("ab") for _ in range(n)] for _ in range(4)]
            rows += rows[:2]
            got = self.go(csv_bytes(header, rows), ["--group", ",".join(header)], label="width %d" % n)
            self.assertIn(got.status, (0, 8), (n, got.stdout[:200]))
            if got.status == 0:
                self.assertLessEqual(got.data()["group_count"], 4)
                self.assertEqual(got.data()["group_count"], len({tuple(r) for r in rows}), n)


if __name__ == "__main__":
    unittest.main()
