"""J2 (docs/joins.md sections 4.2, 5, 8.1 point 3, 11 and 17, slice J2): `left`, `semi`, `anti`; typed keys (`:int`, `:dec(S)`, `:float`); composite keys; USING (`--on NAME`); padding.

The gates, written before the code and each able to fail (on `main`'s binary, which has the inner join of J1 only, every one of the classes below fails):

* O, the oracle: generated tables joined by a Python reference of the definition (join_ref2.py), by SQLite and by DuckDB (explicit CASTs for typed keys, VARCHAR and no
  inference for text), compared as the multiset of rows AND in the defined order, for inner, left, semi and anti; as csv and as json, from a file and from standard input,
  at 1 and 4 threads. The shapes: duplicates, no matches, all matches, empty tables, empty text keys, big fan-out, 007 and 7, -0 and 0, 1.5 and 1.50, composite keys,
  the same probe column twice, eight pairs, USING (also typed, also composite, also with nothing of the build left to show).
* The typed refusals: a cell that is not of the type is refused by the rule the rest of the tool uses, in the build file first (rows in file order, the pairs of a row left to
  right), then in the probe; an empty cell is one; a padded cell of a left join read as a number is refused and says it was padded.
* A, the accounting: the program's count of the accounted bytes equals the formula computed here by another program, for composite keys, typed keys (8 bytes), and the keys-only
  build of semi and anti, on the edges 2^20 and 2^20 + 1 rows; the bound is exact (accepted at N, refused at N - 1) at 1, 2, 6 and 16 threads and for a pipe.
* The flags: `--join-type` takes inner, left, semi, anti; the forms of `--on`; what is refused with its rule.
* ST/TH: the probe from a pipe gives the file's bytes, `--threads` reads on one core with the same bytes and says so in `--explain`.
* F, fuzz: no trap, every refusal tagged with a rule of the catalogue, every repair offered is accepted.
"""

import json
import os
import random
import re
import shutil
import sys
import unittest
from collections import Counter

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import join_ref as J
import join_ref2 as R
from harness import ROOT, TRAPS, Scratch, binary, introspect, name_text, run, validate

JTYPES = ("inner", "left", "semi", "anti")


def argv_for(d, header_p, header_b, pairs, jtype, prefix=None, extra=(), p="p.csv", b="b.csv"):
    a = ["--root", str(d), "--join", b, "--on", R.on_arg(pairs, header_p, header_b), "--join-type", jtype]
    if prefix is not None and jtype in ("inner", "left"):
        a += ["--join-prefix", prefix]
    return a + [str(x) for x in extra] + ([p] if p is not None else [])


def cells(rows):
    return [[name_text(c) for c in r] for r in rows]


class Case:
    def __init__(self, name, pheader, prows, bheader, brows, pairs, opts):
        self.name, self.pheader, self.prows, self.bheader, self.brows, self.pairs, self.opts = name, pheader, prows, bheader, brows, pairs, opts
        self.s = Scratch()
        J.write_case(str(self.s.dir), pheader, prows, bheader, brows, opts)

    def cleanup(self):
        self.s.cleanup()


def utf8(b):
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class Oracle(unittest.TestCase):
    """O: the tool against a reference and two SQL engines, rows and order, for the four join types."""

    def check(self, c, jtype, prefix=b""):
        tag = "%s [%s]" % (c.name, jtype)
        prefix = c.opts.get("prefix", prefix)
        header, want = R.ref_join(c.pheader, c.prows, c.bheader, c.brows, c.pairs, jtype, prefix)
        # the oracles: SQLite always; DuckDB when it is installed and the bytes are UTF-8 (it refuses others)
        lite, sql = R.sqlite_join(c.pheader, c.prows, c.bheader, c.brows, c.pairs, jtype)
        self.assertEqual(R.same_multiset(lite, want), True, "%s: the reference and SQLite disagree as multisets\n%s" % (tag, sql))
        self.assertEqual(lite, want, "%s: the reference and SQLite disagree in order\n%s" % (tag, sql))
        if all(utf8(x) for r in c.prows + c.brows for x in r):
            duck, dsql = R.duckdb_join(c.pheader, c.prows, c.bheader, c.brows, c.pairs, jtype)
            if duck is not None:
                self.assertEqual(R.same_multiset(duck, want), True, "%s: the reference and DuckDB disagree as multisets\n%s" % (tag, dsql))
                self.assertEqual(duck, want, "%s: the reference and DuckDB disagree in order\n%s" % (tag, dsql))
        d = str(c.s.dir)
        base = argv_for(d, c.pheader, c.bheader, c.pairs, jtype, prefix.decode() if prefix else None)
        r = run(*base, "--format", "csv")
        self.assertEqual(r.status, 0, "%s: %r" % (tag, r.stdout[:200] + r.stderr[:300]))
        got = J.read_csv_bytes(r.stdout)
        self.assertEqual(R.same_multiset(got[1:], want), True, tag + ": the multiset of rows")
        self.assertEqual(got, [header] + want, tag)
        self.assertEqual(r.stdout, J.render_rows([header] + want), "%s: the bytes of the csv" % tag)
        probe = open(os.path.join(d, "p.csv"), "rb").read()
        s = run(*argv_for(d, c.pheader, c.bheader, c.pairs, jtype, prefix.decode() if prefix else None, ["--format", "csv"], p=None), stdin=probe)
        self.assertEqual((s.status, s.stdout), (0, r.stdout), tag + ": standard input")
        t = run(*base, "--format", "csv", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7")
        self.assertEqual((t.status, t.stdout), (0, r.stdout), tag + ": threads")
        j = run(*base, "--limit", "1000000", "--max-bytes", "67108864")
        self.assertEqual(j.status, 0, tag)
        self.assertEqual(validate(j), [], tag)
        data = j.data()
        self.assertEqual((cells([data["columns"]])[0], cells(data["rows"]), data["row_count"], data["truncated"]), (header, want, len(want), False), tag)

    def test_the_shapes_all_four_types(self):
        for name, ph, pr, bh, br, pairs, opts in R.shapes():
            for jt in JTYPES:
                with self.subTest("%s [%s]" % (name, jt)):
                    c = Case(name, ph, pr, bh, br, pairs, opts)
                    try:
                        self.check(c, jt)
                    finally:
                        c.cleanup()

    def test_random_tables(self):
        rng = random.Random(20261016)
        for i in range(80):
            case = R.random_shape(rng)
            jt = JTYPES[i % 4]
            with self.subTest("%d %s" % (i, jt)):
                c = Case(*case)
                try:
                    self.check(c, jt, b"d_" if i % 3 == 0 else b"")
                finally:
                    c.cleanup()

    def test_a_prefix_with_left_and_inner(self):
        for name, ph, pr, bh, br, pairs, opts in R.shapes()[:6]:
            for jt in ("inner", "left"):
                with self.subTest(name + jt):
                    c = Case(name, ph, pr, bh, br, pairs, opts)
                    try:
                        self.check(c, jt, b"d_")
                    finally:
                        c.cleanup()


class Semantics(unittest.TestCase):
    """The definitions of the design, one at a time, on small files that a reader can check by eye."""

    def join(self, probe, build, on, jtype="inner", extra=(), stdin=False):
        s = Scratch()
        try:
            s.write("p.csv", probe)
            s.write("b.csv", build)
            return run("--root", s.dir, "--join", "b.csv", "--on", on, "--join-type", jtype, "--format", "csv", *extra, *([] if stdin else ["p.csv"]))
        finally:
            s.cleanup()

    def lines(self, r):
        self.assertEqual(r.status, 0, r.stderr[:300])
        return r.stdout.decode().splitlines()

    def test_left_pads_with_the_empty_text_in_its_place(self):
        out = self.lines(self.join("k,v\na,1\nz,2\nb,3\n", "id,w,x\na,A,1\nb,B,2\na,C,3\n", "k=id", "left"))
        self.assertEqual(out, ["k,v,id,w,x", "a,1,a,A,1", "a,1,a,C,3", "z,2,,,", "b,3,b,B,2"])

    def test_left_with_a_one_column_build_pads_one_cell(self):
        out = self.lines(self.join("k,v\na,1\nz,2\n", "id\na\n", "k=id", "left"))
        self.assertEqual(out, ["k,v,id", "a,1,a", "z,2,"])

    def test_semi_once_and_anti_once_probe_columns_only(self):
        probe = "k,v\na,1\nz,2\na,3\nb,4\ny,5\n"
        build = "id,w\na,x\na,y\nb,z\nb,q\nb,r\n"
        self.assertEqual(self.lines(self.join(probe, build, "k=id", "semi")), ["k,v", "a,1", "a,3", "b,4"])
        self.assertEqual(self.lines(self.join(probe, build, "k=id", "anti")), ["k,v", "z,2", "y,5"])

    def test_semi_and_anti_partition_the_probe(self):
        probe = "k,v\n" + "".join("%s,%d\n" % (k, i) for i, k in enumerate("abcabcxyzab"))
        build = "id\na\nc\nq\n"
        semi, anti = self.lines(self.join(probe, build, "k=id", "semi")), self.lines(self.join(probe, build, "k=id", "anti"))
        self.assertEqual(sorted(semi[1:] + anti[1:], key=lambda l: int(l.split(",")[1])), probe.splitlines()[1:])

    def test_typed_keys_compare_values(self):
        probe = "k,v\n007,a\n7,b\n-0,c\n0,d\n+7,e\n8,f\n"
        build = "id,w\n7,x\n0,y\n"
        self.assertEqual(self.lines(self.join(probe, build, "k=id:int")), ["k,v,id,w", "007,a,7,x", "7,b,7,x", "-0,c,0,y", "0,d,0,y", "+7,e,7,x"])
        self.assertEqual(self.lines(self.join(probe, build, "k=id", "semi")), ["k,v", "7,b", "0,d"], "without a suffix the keys are bytes")
        self.assertEqual(self.lines(self.join(probe, build, "k=id:int", "anti")), ["k,v", "8,f"])

    def test_decimal_and_float_keys(self):
        probe = "k,v\n1.5,a\n1.50,b\n01.5,c\n1.6,d\n"
        build = "id,w\n1.5,x\n"
        self.assertEqual(len(self.lines(self.join(probe, build, "k=id:dec(2)"))), 4)
        self.assertEqual(len(self.lines(self.join(probe, build, "k=id:float"))), 4)
        self.assertEqual(self.lines(self.join(probe, build, "k=id")), ["k,v,id,w", "1.5,a,1.5,x"])
        probe = "k,v\n1e2,a\n100,b\n1.0E2,c\n-0,d\n0.0,e\n"
        build = "id,w\n100,x\n0,y\n"
        self.assertEqual(self.lines(self.join(probe, build, "k=id:float", "semi")), ["k,v", "1e2,a", "100,b", "1.0E2,c", "-0,d", "0.0,e"])

    def test_composite_keys_need_every_pair(self):
        probe = "a,b,v\n1,x,p1\n1,y,p2\n2,x,p3\n01,x,p4\n"
        build = "c,d,w\n1,x,B1\n2,y,B2\n1,x,B3\n"
        self.assertEqual(self.lines(self.join(probe, build, "a=c,b=d")), ["a,b,v,c,d,w", "1,x,p1,1,x,B1", "1,x,p1,1,x,B3"])
        self.assertEqual(self.lines(self.join(probe, build, "a=c:int,b=d")), ["a,b,v,c,d,w", "1,x,p1,1,x,B1", "1,x,p1,1,x,B3", "01,x,p4,1,x,B1", "01,x,p4,1,x,B3"])
        self.assertEqual(self.lines(self.join(probe, build, "a=c:int,b=d", "anti")), ["a,b,v", "1,y,p2", "2,x,p3"])

    def test_the_length_of_a_component_is_part_of_the_key(self):
        probe = "a,b\nab,c\na,bc\n,abc\nabc,\n"
        build = "c,d\na,bc\nabc,\n"
        self.assertEqual(self.lines(self.join(probe, build, "a=c,b=d", "semi")), ["a,b", "a,bc", "abc,"])

    def test_using_leaves_the_build_column_out(self):
        out = self.lines(self.join("id,v\n1,a\n2,b\n3,c\n", "id,w\n1,X\n3,Y\n3,Z\n", "id", "left"))
        self.assertEqual(out, ["id,v,w", "1,a,X", "2,b,", "3,c,Y", "3,c,Z"])
        out = self.lines(self.join("id,v\n007,a\n2,b\n", "id,w\n7,X\n", "id:int"))
        self.assertEqual(out, ["id,v,w", "007,a,X"], "the build's spelling is not shown")
        out = self.lines(self.join("id,v\n007,a\n", "id,w\n7,X\n", "id=id:int", extra=["--join-prefix", "r_"]))
        self.assertEqual(out, ["id,v,r_id,r_w", "007,a,7,X"], "both spellings with the longer form")

    def test_using_with_nothing_left_of_the_build(self):
        out = self.lines(self.join("id,v\n1,a\n2,b\n1,c\n", "id\n1\n1\n", "id"))
        self.assertEqual(out, ["id,v", "1,a", "1,a", "1,c", "1,c"])

    def test_a_name_that_is_not_a_key_still_collides_under_using(self):
        s = Scratch()
        try:
            s.write("p.csv", "id,v\n1,a\n")
            s.write("b.csv", "id,v\n1,A\n")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "id", "p.csv")
        finally:
            s.cleanup()
        self.assertEqual((r.status, r.first_rule()), (2, "join.column-collision"))
        self.assertEqual(r.error()["detail"]["columns"], ["v"])
        out = self.lines(self.join("id,v\n1,a\n", "id,v\n1,A\n", "id", extra=["--join-prefix", "r_"]))
        self.assertEqual(out, ["id,v,r_v", "1,a,A"])

    def test_semi_and_anti_have_no_build_columns_to_collide(self):
        for jt in ("semi", "anti"):
            self.assertEqual(self.lines(self.join("k,v\n1,a\n2,b\n", "k,v\n1,A\n", "k", jt))[0], "k,v")

    def test_select_and_where_name_the_padded_columns(self):
        out = self.lines(self.join("k,v\na,1\nz,2\n", "id,w\na,A\n", "k=id", "left", extra=["--select", "v,w"]))
        self.assertEqual(out, ["v,w", "1,A", "2,"])
        out = self.lines(self.join("k,v\na,1\nz,2\n", "id,w\na,A\n", "k=id", "left", extra=["--where", "w = ''"]))
        self.assertEqual(out, ["k,v,id,w", "z,2,,"], "a text condition cannot tell padding from a real empty cell")
        out = self.lines(self.join("k,v\na,1\nz,2\n", "id,w\na,A\n", "k=id", "left", extra=["--where", "w != ''"]))
        self.assertEqual(out, ["k,v,id,w", "a,1,a,A"], "a condition on a build column removes the padded rows, as WHERE does")

    def test_limit_and_from_count_the_joined_rows_padded_ones_too(self):
        probe = "k\na\nz\nb\nq\n"
        build = "id,w\na,1\nb,2\nb,3\n"
        full = self.lines(self.join(probe, build, "k=id", "left"))
        self.assertEqual(full, ["k,id,w", "a,a,1", "z,,", "b,b,2", "b,b,3", "q,,"])
        self.assertEqual(self.lines(self.join(probe, build, "k=id", "left", extra=["--from", "1", "--limit", "3"])), ["k,id,w"] + full[2:5])

    def test_max_join_rows_counts_the_rows_produced_padded_semi_anti(self):
        probe = "k\na\nz\nq\n"
        build = "id\na\n"
        for jt, produced in (("inner", 1), ("left", 3), ("semi", 1), ("anti", 2)):
            with self.subTest(jt):
                self.assertEqual(self.join(probe, build, "k=id", jt, extra=["--max-join-rows", str(produced)]).status, 0)
                low = self.join(probe, build, "k=id", jt, extra=["--max-join-rows", str(produced - 1)]) if produced > 1 else None
                if low is not None:
                    self.assertIn(b"limit.too-many-join-rows", low.stderr)
                    self.assertEqual(low.status, 8)

    def test_the_facts_of_the_row_bound_for_a_padded_semi_or_anti_row(self):
        # detail.matches is how many build rows the probe row's key has: 0 for a padded or an anti row, and a semi join (which keeps no chain) says 1, the least it can be
        probe = "k\na\nb\nz\n"
        build = "id\na\na\nb\n"
        for jt, want in (("inner", 2), ("left", 2), ("semi", 1), ("anti", 0)):
            for row, bound in ((1, "1"),):
                s = Scratch()
                try:
                    s.write("p.csv", probe)
                    s.write("b.csv", build)
                    r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--join-type", jt, "--max-join-rows", "1", "p.csv")
                    if jt == "anti":
                        s.write("p.csv", "k\nz\nq\n")
                        r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--join-type", jt, "--max-join-rows", "1", "p.csv")
                    self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-join-rows"), jt)
                    d = r.error()["detail"]
                    self.assertEqual(d["matches"], want, jt)
                finally:
                    s.cleanup()
        s = Scratch()
        try:
            s.write("p.csv", "k\nz\nq\n")
            s.write("b.csv", "id\na\n")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--join-type", "left", "--max-join-rows", "1", "p.csv")
            self.assertEqual((r.first_rule(), r.error()["detail"]["matches"], r.error()["detail"]["probe_row"], r.error()["detail"]["probe_line"]), ("limit.too-many-join-rows", 0, 2, 3))
        finally:
            s.cleanup()

    def test_a_probe_on_standard_input(self):
        for jt in JTYPES[1:]:
            r1 = self.join("k,v\na,1\nz,2\n", "id,w\na,A\n", "k=id", jt)
            s = Scratch()
            try:
                s.write("b.csv", "id,w\na,A\n")
                r2 = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--join-type", jt, "--format", "csv", stdin=b"k,v\na,1\nz,2\n")
            finally:
                s.cleanup()
            self.assertEqual((r1.status, r1.stdout), (r2.status, r2.stdout), jt)


class TypedRefusals(unittest.TestCase):
    """A cell that is not of the key's type is refused by the rule of the rest of the tool; nothing is guessed."""

    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def go(self, probe, build, on, jtype="inner", extra=(), fmt=None):
        self.s.write("p.csv", probe)
        self.s.write("b.csv", build)
        return run("--root", self.s.dir, "--join", "b.csv", "--on", on, "--join-type", jtype, *(["--format", fmt] if fmt else []), *extra, "p.csv")

    def test_every_cell_of_the_table_of_edge_cells_as_a_key_on_either_side(self):
        # docs/numbers.md's table of cells, at the three types: the tool refuses with the rule the table says, in the probe and in the build, for every join type
        import numbers_ref as N
        for cell, dec2, flt in N.SPEC:
            raw = cell.encode("latin-1")
            if b"\n" in raw or b"\r" in raw or len(raw) > 1100:
                continue
            for kind, want in (("dec(2)", dec2), ("float", flt)):
                for side in ("probe", "build"):
                    for jt in ("inner", "anti"):
                        with self.subTest(cell=cell[:30], kind=kind, side=side, jt=jt):
                            good = "1"
                            probe = [[raw if side == "probe" else good.encode(), b"p"]]
                            build = [[raw if side == "build" else good.encode(), b"b"]]
                            J.write_csv(str(self.s.dir / "p.csv"), [b"k", b"v"], probe)
                            J.write_csv(str(self.s.dir / "b.csv"), [b"id", b"w"], build)
                            r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id:" + kind, "--join-type", jt, "p.csv")
                            if want[0] == "ok":
                                self.assertEqual(r.status, 0, (cell, r.stdout[:300]))
                            else:
                                self.assertEqual((r.status, r.first_rule()), (8, want[1]), (cell, kind, r.stdout[:300]))
                                d = r.error()["detail"]
                                self.assertEqual((d["context"], d["input"], d["row"], d["line"], d["column"]), ("join", side, 1, 2, "k" if side == "probe" else "id"))

    def test_integers_at_the_edges(self):
        for cell, rule in (("", "value.not-integer"), (" 7", "value.not-integer"), ("7.0", "value.not-integer"), ("1e2", "value.not-integer"), ("0x10", "value.not-integer"), ("+", "value.not-integer"),
                           ("9223372036854775808", "value.integer-overflow"), ("-9223372036854775809", "value.integer-overflow"), ("9223372036854775807", None), ("-9223372036854775808", None), ("-0", None), ("+0007", None)):
            with self.subTest(cell):
                r = self.go("k,v\n%s,a\n" % cell if cell != "" else "k,v\n,a\n", "id,w\n1,x\n", "k=id:int")
                if rule is None:
                    self.assertEqual(r.status, 0)
                else:
                    self.assertEqual((r.status, r.first_rule()), (8, rule))

    def test_an_empty_cell_is_a_refusal_for_a_typed_key_and_a_key_for_text(self):
        r = self.go("k,v\n,a\nb,c\n", "id,w\n,x\nb,y\n", "k=id", "inner", fmt="csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", ",a,,x", "b,c,b,y"])
        for side, probe, build in (("probe", "k,v\n,a\n", "id,w\n1,x\n"), ("build", "k,v\n1,a\n", "id,w\n,x\n")):
            r = self.go(probe, build, "k=id:int")
            self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"), side)
            self.assertEqual(r.error()["detail"]["input"], side)
            self.assertEqual(r.error()["detail"]["value"], "")

    def test_the_build_is_read_first_then_the_probe_in_file_order(self):
        r = self.go("k,v\nbad1,a\nbad2,b\n", "id,w\n1,x\nbad3,y\nbad4,z\n", "k=id:int")
        d = r.error()["detail"]
        self.assertEqual((d["input"], d["row"], d["line"], d["value"]), ("build", 2, 3, "bad3"))
        r = self.go("k,v\n1,a\nbad1,b\nbad2,b\n", "id,w\n1,x\n", "k=id:int")
        d = r.error()["detail"]
        self.assertEqual((d["input"], d["row"], d["line"], d["value"], d["path"], d["column"], d["context"]), ("probe", 2, 3, "bad1", "p.csv", "k", "join"))

    def test_the_pairs_of_a_row_are_read_left_to_right_and_the_failing_pair_is_named(self):
        r = self.go("a,b,v\n1,x,p\n", "c,d,w\n1,y,q\n", "a=c:int,b=d:int")
        d = r.error()["detail"]
        self.assertEqual((r.first_rule(), d["input"], d["column"], d["value"], d["pair"]), ("value.not-integer", "build", "d", "y", 2))
        r = self.go("a,b,v\nx,1,p\n", "c,d,w\n1,1,q\n", "a=c:int,b=d:int")
        d = r.error()["detail"]
        self.assertEqual((d["input"], d["column"], d["pair"]), ("probe", "a", 1))
        r = self.go("a,b,v\nx,y,p\n", "c,d,w\n1,1,q\n", "a=c:int,b=d:int")
        self.assertEqual(r.error()["detail"]["column"], "a", "the first pair of the row, not the last")

    def test_decimal_scale_and_float_range_say_their_facts(self):
        r = self.go("k\n1.234\n", "id\n1\n", "k=id:dec(2)")
        d = r.error()["detail"]
        self.assertEqual((r.first_rule(), d["scale"], d["digits"]), ("value.decimal-scale", 2, 3))
        r = self.go("k\n1e999\n", "id\n1\n", "k=id:float")
        self.assertEqual((r.first_rule(), r.error()["detail"]["direction"]), ("value.float-range", "overflow"))
        r = self.go("k\nNaN\n", "id\n1\n", "k=id:float")
        self.assertEqual(r.first_rule(), "value.not-finite")

    def test_a_text_join_never_reads_a_number(self):
        r = self.go("k\nbad\n", "id\nworse\n", "k=id", "inner", fmt="csv")
        self.assertEqual(r.status, 0)

    def test_a_padded_cell_read_as_a_number_is_refused_and_says_it_was_padded(self):
        probe = "k,v\na,1\nz,2\n"
        build = "id,w\na,5\n"
        r = self.go(probe, build, "k=id", "left", extra=["--where", "w:int > 0"])
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        d = r.error()["detail"]
        self.assertEqual((d["context"], d["column"], d["value"], d.get("padded"), d["row"], d["line"]), ("where", "w", "", True, 2, 3))
        # the guard the refusal teaches
        r = self.go(probe, build, "k=id", "left", extra=["--where", "w != '' and w:int > 0"], fmt="csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "a,1,a,5"])
        for typed_where, rule in (("w:dec(2) > 0", "value.not-decimal"), ("w:float > 0", "value.not-float")):
            r = self.go(probe, build, "k=id", "left", extra=["--where", typed_where])
            self.assertEqual((r.status, r.first_rule(), r.error()["detail"].get("padded"), r.error()["detail"]["value"]), (8, rule, True, ""), typed_where)
        # a real empty cell of a matched row is not padding
        r = self.go("k\na\n", "id,w\na,\n", "k=id", "left", extra=["--where", "w:int > 0"])
        self.assertEqual(r.first_rule(), "value.not-integer")
        self.assertNotIn("padded", r.error()["detail"])
        # a cell of the probe side is not padding either
        r = self.go("k,v\nz,\n", "id,w\na,5\n", "k=id", "left", extra=["--where", "v:int > 0"])
        self.assertNotIn("padded", r.error()["detail"])
        # an inner join has no padding
        r = self.go("k\na\n", "id,w\na,\n", "k=id", "inner", extra=["--where", "w:int > 0"])
        self.assertNotIn("padded", r.error()["detail"])

    def test_count_of_a_padded_column_is_not_in_a_join(self):
        # --group/--agg over a join are J3; the padded count(*) of 8.1 is J3's. The refusal says so and the pipe is the way
        r = self.go("k\na\n", "id,w\na,\n", "k=id", "left", extra=["--group", "k"])
        self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"))


class Flags(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v,n\na,1,5\n")
        self.s.write("b.csv", "id,w,m\na,2,5\n")

    def tearDown(self):
        self.s.cleanup()

    def rule(self, *argv, file="p.csv"):
        r = run("--root", self.s.dir, *argv, file)
        return r.status, r.first_rule()

    def test_join_type_takes_four_words(self):
        for t in JTYPES:
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--join-type", t)[0], 0, t)
        for t in ("right", "full", "cross", "LEFT", "outer", "left outer", ""):
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--join-type", t), (2, "args.bad-value"), t)

    def test_join_type_is_in_introspect(self):
        f = {x["name"]: x for x in introspect()["flags"]}
        self.assertEqual(f["--join-type"]["kind"], "choice:inner/left/semi/anti")
        self.assertEqual(f["--join-type"]["default"], "inner")

    def test_a_prefix_has_nothing_to_name_in_a_semi_or_anti_join(self):
        for t in ("semi", "anti"):
            r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id", "--join-type", t, "--join-prefix", "r_", "p.csv")
            self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"), t)
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--join-type", t)[0], 0)

    def test_forms_of_on_that_are_taken(self):
        ok = ["k=id", "k=id:int", "k=id:dec(2)", "k=id:dec(0)", "k=id:dec(18)", "k=id:float", "k=id,v=w", "k=id,v=w,n=m", "k=id:int,v=w:float,n=m:dec(3)", "#1=#1", "#1=#1:int", "#3=#3:int", "k=id,k=id", "k=id,k=m"]
        for on in ok:
            with self.subTest(on):
                r = run("--root", self.s.dir, "--join", "b.csv", "--on", on, "--join-prefix", "r_", "--format", "csv", "p.csv")
                self.assertIn(r.status, (0, 8), (on, r.stderr[:300]))          # 8: a value of the type (the files hold text)
                self.assertNotIn(b"args.bad-value", r.stderr)

    def test_forms_of_on_that_are_refused(self):
        bad = {"": "empty", "k": "a name that is only one side and not in the other header is column.unknown, see below", "k=": "empty", "=id": "empty", "k=id=w": "two equals",
               "k:int=id": "a type on the left side", "k:int=id:int": "a type on both", "k=id:": "empty type", "k=id:integer": "unknown type", "k=id:int:int": "two types",
               "k=id:dec": "no scale", "k=id:dec()": "no scale", "k=id:dec(19)": "scale", "k=id:dec(-1)": "scale", "k=id:dec(x)": "scale", "k=id:DEC(2)": "case", "k=id:INT": "case", "k=id:Float": "case",
               "k\\x=id": "bad escape", "k=id\\": "trailing backslash", "k=id,": "empty pair", ",k=id": "empty pair", "k=id,,v=w": "empty pair", "#1": "bare position", "#1:int": "bare position typed",
               ",".join("a%d=b%d" % (i, i) for i in range(9)): "nine pairs"}
        for on, why in bad.items():
            if on == "k":
                continue
            with self.subTest(on):
                r = run("--root", self.s.dir, "--join", "b.csv", "--on", on, "--join-prefix", "r_", "p.csv")
                self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"), why)
                if on:
                    self.assertEqual(r.error()["detail"]["flags"], "--on")

    def test_a_bare_name_is_using_and_needs_the_name_on_both_sides(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (3, "column.unknown"))
        self.assertEqual(r.error()["detail"]["input"], "build")
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (3, "column.unknown"))
        self.assertEqual(r.error()["detail"]["input"], "probe")

    def test_a_second_pair_that_names_nothing_is_the_column_error_with_its_side(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id,v=nope", "p.csv")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["input"], r.error()["detail"]["name"]), (3, "column.unknown", "build", "nope"))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id,nope=w", "p.csv")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["input"]), (3, "column.unknown", "probe"))

    def test_escapes_in_a_pair_and_a_colon_in_a_name(self):
        s = Scratch()
        try:
            s.write("p.csv", "a:b,c=d,x\n1,2,3\n")
            s.write("b.csv", "e:f,g,h\n1,2,3\n")
            for on in ("a\\:b=e\\:f", "a\\:b=e\\:f:int", "c\\=d=g:int,a\\:b=#1", "#1=#1,#2=#2:int"):
                r = run("--root", s.dir, "--join", "b.csv", "--on", on, "--join-prefix", "r_", "--format", "csv", "p.csv")
                self.assertEqual(r.status, 0, (on, r.stderr[:300]))
                self.assertEqual(r.stdout.decode().splitlines()[1], "1,2,3,1,2,3", on)
        finally:
            s.cleanup()

    def test_using_with_a_name_twice_in_a_header_is_ambiguous(self):
        s = Scratch()
        try:
            s.write("p.csv", "id,id\n1,2\n")
            s.write("b.csv", "id\n1\n")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "id", "p.csv")
            self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["input"]), (8, "column.ambiguous", "probe"))
            r = run("--root", s.dir, "--join", "b.csv", "--on", "#1=id", "--join-prefix", "r_", "--format", "csv", "p.csv")
            self.assertEqual(r.status, 0)
        finally:
            s.cleanup()

    def test_join_where_and_the_forms_of_j3_are_still_refused(self):
        for extra in (["--group", "k"], ["--order-by", "k"], ["--top", "1"]):
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--join-type", "left", *extra), (2, "args.conflict"))


class Accounting(unittest.TestCase):
    """A: the accounted bytes equal the formula (join_ref2.accounted, computed here) for composite keys, typed keys and the keys-only build of semi and anti, and the bound is exact."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        # a probe of one row of zeros: every typed cell parses, and the --where that follows keeps it out of the answer
        J.write_csv(str(cls.s.dir / "p.csv"), [b"a", b"b", b"c"], [[b"0", b"0", b"0"]])

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def file(self, name, header, rows, **kw):
        J.write_csv(str(self.s.dir / name), header, rows, **kw)

    def exact(self, name, pairs, jtype, threads=(1,), stdin=False):
        path = str(self.s.dir / name)
        f = R.accounted(path, pairs, jtype)
        on = ",".join("%s=%s%s" % ("abc"[min(i, 2)], "abc"[bk], R.suffix_of(kind)) for i, (pk, bk, kind, _) in enumerate(pairs))
        base = ["--root", self.s.dir, "--join", name, "--on", on, "--join-type", jtype] + (["--join-prefix", "r_"] if jtype in ("inner", "left") else []) + ["--where", "a = no-such-key", "--format", "csv"]
        for t in threads:
            argv = base + ["--threads", t]
            if stdin:
                got = run(*argv, "--max-state-bytes", f["held"], stdin=b"a,b,c\n0,0,0\n")
                low = run(*argv, "--max-state-bytes", f["held"] - 1, stdin=b"a,b,c\n0,0,0\n")
            else:
                got = run(*argv, "--max-state-bytes", f["held"], "p.csv")
                low = run(*argv, "--max-state-bytes", f["held"] - 1, "p.csv")
            self.assertEqual(got.status, 0, "%s/%s: refused at the formula %d: %r" % (name, jtype, f["held"], got.stderr[:300]))
            self.assertEqual(low.status, 8, "%s/%s: accepted one byte below the formula %d" % (name, jtype, f["held"]))
            self.assertIn(b"limit.state-too-large", low.stderr)
        r = run(*base[:-2], "--max-state-bytes", f["held"] - 1, "p.csv")
        e = r.error()
        d = e["detail"]
        self.assertEqual(e["rule"], "limit.state-too-large")
        self.assertEqual((d["holder"], d["input"], d["path"], d["limit"]), ("join-build", "build", name, f["held"] - 1), name)
        self.assertEqual(d["held"], f["before_fail"], "%s/%s: the program's count before the row that did not fit is not the formula's" % (name, jtype))
        self.assertEqual((d["build_row"], d["build_line"]), (f["fail_row"], f["fail_line"]))
        self.assertEqual((d["build_bytes_read"], d["build_file_bytes"]), (f["fail_bytes"], f["size"]))
        return f

    def test_composite_and_typed_keys_on_the_edges_of_the_growth_of_the_arrays(self):
        rng = random.Random(5)
        for rows in (1 << 20, (1 << 20) + 1):
            name = "c%d.csv" % rows
            self.file(name, [b"a", b"b", b"c"], ([b"%d" % (i % 1000), b"%d.%02d" % (i // 100, i % 100), b"u%07x" % i] for i in rng.sample(range(rows * 2), rows)))
            for jtype in ("inner", "semi"):
                pairs = [(0, 0, "int", False), (1, 1, ("dec", 2), False)]
                f = self.exact(name, pairs, jtype)
                self.assertEqual((f["rows"], f["distinct"]), (rows, rows))
            os.unlink(self.s.dir / name)

    def test_one_typed_key_is_eight_bytes_and_spellings_are_one_key(self):
        rows = [[b"%d" % (i % 700), b"x" * (i % 5), b"c"] for i in range(5000)] + [[b"007", b"y", b"c"], [b"7", b"z", b"c"], [b"+7", b"", b"c"], [b"-0", b"", b"c"], [b"0", b"", b"c"]]
        self.file("t.csv", [b"a", b"b", b"c"], rows)
        for jtype in JTYPES:
            f = self.exact("t.csv", [(0, 0, "int", False)], jtype, threads=(1, 2, 6, 16))
            self.assertEqual(f["distinct"], 700, "007, 7 and +7 are one key; -0 and 0 are one")
        self.exact("t.csv", [(0, 0, "int", False)], "inner", stdin=True)
        self.exact("t.csv", [(0, 0, "int", False)], "anti", stdin=True)

    def test_float_and_decimal_keys(self):
        rows = [[b"%d.%d" % (i % 90, i % 7), b"1.50" if i % 2 else b"1.5", b"e"] for i in range(3000)]
        self.file("f.csv", [b"a", b"b", b"c"], rows)
        for jtype in ("inner", "anti"):
            self.exact("f.csv", [(0, 0, "float", False)], jtype)
            self.exact("f.csv", [(1, 1, ("dec", 2), False)], jtype)

    def test_composite_text_keys_carry_four_bytes_of_length_each(self):
        rows = [[b"ab%d" % (i % 40), b"c", b"z" * (i % 9)] for i in range(4000)] + [[b"", b"", b""], [b"", b"", b"q"]]
        self.file("x.csv", [b"a", b"b", b"c"], rows)
        for jtype in JTYPES:
            self.exact("x.csv", [(0, 0, None, False), (1, 1, None, False)], jtype, threads=(1, 2, 6, 16))
        one = R.accounted(str(self.s.dir / "x.csv"), [(0, 0, None, False), (1, 1, None, False)], "inner")
        self.assertEqual(one["distinct"], 41)

    def test_semi_and_anti_hold_keys_only_so_duplicates_cost_nothing(self):
        rows = [[b"dup", b"%d" % i, b"c" * 50] for i in range(5000)]
        self.file("d.csv", [b"a", b"b", b"c"], rows)
        for jtype in ("semi", "anti"):
            f = self.exact("d.csv", [(0, 0, None, False)], jtype)
            self.assertEqual(f["held"], len(b"dup") + 44, "one key, 5000 rows: the rows are not held")
        f = self.exact("d.csv", [(0, 0, None, False)], "inner")
        self.assertGreater(f["held"], 5000 * 50)

    def test_using_holds_the_whole_record(self):
        # the dropped column is left out of the answer, not out of the table: the formula is the same as without USING (docs/joins.md 17)
        rows = [[b"k%d" % (i % 90), b"v%d" % i, b"w"] for i in range(2000)]
        self.file("u.csv", [b"a", b"b", b"c"], rows)
        f1 = self.exact("u.csv", [(0, 0, None, False)], "inner")
        path = str(self.s.dir / "u.csv")
        on = "a"
        r = run("--root", self.s.dir, "--join", "u.csv", "--on", on, "--join-prefix", "r_", "--where", "a = zzz", "--max-state-bytes", f1["held"] - 1, "p.csv")
        self.assertEqual(r.first_rule(), "limit.state-too-large")
        ok = run("--root", self.s.dir, "--join", "u.csv", "--on", on, "--join-prefix", "r_", "--where", "a = zzz", "--max-state-bytes", f1["held"], "p.csv")
        self.assertEqual(ok.status, 0)

    def test_the_bound_is_the_same_when_the_join_is_empty_or_tiny(self):
        self.file("e.csv", [b"a", b"b", b"c"], [])
        for jtype in JTYPES:
            r = run("--root", self.s.dir, "--join", "e.csv", "--on", "a=a:int", "--join-type", jtype, "--max-state-bytes", "1", *(["--join-prefix", "r_"] if jtype in ("inner", "left") else []), "p.csv")
            self.assertEqual(r.status, 0, jtype)

    def test_the_formula_is_in_the_help(self):
        helps = {f["name"]: f["help"] for f in introspect()["flags"]}
        h = helps["--max-state-bytes"]
        for word in ("plus 16", "plus 60", "plus 44", "keys only", "8 bytes", "four bytes", "not resident memory"):
            self.assertIn(word, h)


class Threads(unittest.TestCase):
    """--threads reads a join of any type on one core, with the same bytes, and says so."""

    def test_the_same_bytes_at_every_thread_count_and_chunk_size(self):
        rng = random.Random(31)
        s = Scratch()
        try:
            dom = [b"k%d" % i for i in range(20)]
            J.write_csv(str(s.dir / "p.csv"), [b"k", b"n", b"v"], R.rows_of(rng, 2500, 3, [0, 1], [dom, R.INT_SPELLINGS], b"p"))
            J.write_csv(str(s.dir / "b.csv"), [b"id", b"m", b"w"], R.rows_of(rng, 200, 3, [0, 1], [dom, R.INT_SPELLINGS], b"b"))
            for jt in JTYPES:
                base = ["--root", s.dir, "--join", "b.csv", "--on", "k=id,n=m:int", "--join-type", jt, "--format", "csv"]
                want = run(*base, "p.csv")
                self.assertEqual(want.status, 0, jt)
                self.assertGreater(len(want.stdout), 100, jt)
                for t, chunk in ((2, 1), (3, 7), (4, 64), (8, 1000), (16, 1 << 22), (64, 7)):
                    got = run(*base, "--threads", t, "--chunk-bytes", chunk, "--parallel-min-bytes", 0, "p.csv")
                    self.assertEqual((got.status, got.stdout), (0, want.stdout), (jt, t, chunk))
                x = run(*base, "--threads", 4, "--explain", "p.csv").data()["explain"]["join"]
                self.assertEqual(x["type"], jt)
                self.assertEqual(x["work"]["threads"], {"requested": 4, "effective": 1, "reason": "the join is read on one core"})
        finally:
            s.cleanup()


class Strace(unittest.TestCase):
    """ST and AU on Linux (`strace`, skipped where there is none): a join of any type starts no thread, opens no socket and opens no file for writing, from a file and from a pipe."""

    @unittest.skipUnless(shutil.which("strace") and sys.platform.startswith("linux"), "strace is Linux only")
    def test_no_thread_no_socket_no_write(self):
        s = Scratch()
        try:
            rng = random.Random(3)
            dom = [b"k%d" % i for i in range(10)]
            J.write_csv(str(s.dir / "p.csv"), [b"k", b"n", b"v"], R.rows_of(rng, 3000, 3, [0, 1], [dom, R.INT_SPELLINGS], b"p"))
            J.write_csv(str(s.dir / "b.csv"), [b"id", b"m", b"w"], R.rows_of(rng, 100, 3, [0, 1], [dom, R.INT_SPELLINGS], b"b"))
            probe = (s.dir / "p.csv").read_bytes()
            # the control: the same trace of a plain threaded read of the same file does show threads, so that an empty answer below means something
            log = str(s.dir / "control.txt")
            subprocess.run(["strace", "-f", "-o", log, "-e", "trace=clone,clone3", binary(), "--root", str(s.dir), "--select", "k", "--format", "csv", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "100", "p.csv"],
                           capture_output=True, input=b"", timeout=120)
            self.assertTrue([l for l in open(log).read().splitlines() if "CLONE_THREAD" in l], "the control: a threaded read starts threads")
            for jt in JTYPES:
                for piped in (False, True):
                    log = str(s.dir / "trace.txt")
                    argv = ["strace", "-f", "-o", log, "-e", "trace=clone,clone3,socket,connect,openat,open,creat,unlink,rename,mkdir", binary(), "--root", str(s.dir), "--join", "b.csv", "--on", "k=id,n=m:int",
                            "--join-type", jt, "--format", "csv", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "100"] + ([] if piped else ["p.csv"])
                    p = subprocess.run(argv, capture_output=True, input=probe if piped else b"", timeout=120)
                    self.assertEqual(p.returncode, 0, (jt, p.stderr[:300]))
                    trace = open(log).read()
                    self.assertIn("b.csv", trace, "the control: strace saw the build file opened")
                    clones = [l for l in trace.splitlines() if re.search(r"\bclone3?\(", l) and "CLONE_THREAD" in l]
                    self.assertEqual(clones, [], (jt, piped, "a thread was started"))
                    self.assertEqual(re.findall(r"\bsocket\(|\bconnect\(", trace), [], (jt, piped, "a socket"))
                    writes = [l for l in trace.splitlines() if re.search(r"O_WRONLY|O_RDWR|O_CREAT|\bcreat\(|unlink|rename|mkdir", l) and "ENOENT" not in l]
                    self.assertEqual(writes, [], (jt, piped, "a file opened for writing"))
        finally:
            s.cleanup()


class Explain(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,n,v\na,1,x\n")
        self.s.write("b.csv", "id,m,w\na,1,y\n")

    def tearDown(self):
        self.s.cleanup()

    def ex(self, *argv):
        return run("--root", self.s.dir, *argv, "--explain", "p.csv")

    def test_the_type_the_keys_and_the_using_columns_are_said(self):
        r = self.ex("--join", "b.csv", "--on", "k=id,n=m:int", "--join-type", "left", "--join-prefix", "r_")
        self.assertEqual(validate(r), [])
        j = r.data()["explain"]["join"]
        self.assertEqual(j["type"], "left")
        self.assertEqual(j["keys"], [{"probe": "k", "build": "id", "type": "text", "probe_position": 1, "build_position": 1},
                                     {"probe": "n", "build": "m", "type": "int", "probe_position": 2, "build_position": 2}])
        self.assertEqual((j["using"], j["output_columns"], j["prefix"]), ([], 6, "r_"))
        self.assertTrue(any("padded" in n or "empty" in n for n in r.data()["explain"]["notes"]))
        r = self.ex("--join", "b.csv", "--on", "k=id:dec(2),n:float", "--join-type", "inner", "--join-prefix", "r_")
        self.assertEqual(r.first_rule(), "column.unknown")          # `n` is a bare name: USING, and b.csv has no `n`
        r = self.ex("--join", "b.csv", "--on", "k=id,m=m:dec(3)", "--join-prefix", "r_")
        self.assertEqual(r.first_rule(), "column.unknown")

    def test_using_semi_and_anti_columns(self):
        self.s.write("b2.csv", "k,m,w\na,1,y\n")
        j = run("--root", self.s.dir, "--join", "b2.csv", "--on", "k:int,n=m:float", "--join-prefix", "r_", "--explain", "p.csv").data()["explain"]["join"]
        self.assertEqual((j["using"], [k["type"] for k in j["keys"]]), (["k"], ["int", "float"]))
        j = run("--root", self.s.dir, "--join", "b2.csv", "--on", "k", "--explain", "p.csv").data()["explain"]
        self.assertEqual((j["join"]["using"], j["join"]["output_columns"], j["output"]["columns"]), (["k"], 5, ["k", "n", "v", "m", "w"]))
        for t in ("semi", "anti"):
            j = run("--root", self.s.dir, "--join", "b2.csv", "--on", "k", "--join-type", t, "--explain", "p.csv").data()["explain"]
            self.assertEqual((j["join"]["type"], j["join"]["output_columns"], j["output"]["columns"], j["join"]["using"]), (t, 3, ["k", "n", "v"], []))

    def test_the_upper_bound_holds_for_typed_and_composite_keys_and_keys_only_builds(self):
        # a typed key is 8 bytes whatever the cell and several pairs carry 4 bytes of length each: the bound from the size has to allow for both, for every join type
        rng = random.Random(12)
        for ncols in (1, 2, 5):
            for rows in (0, 1, 7, 300):
                for width in (1, 4, 40):
                    recs = [[b"%d" % (i % 9) + b"x" * (width - 1) if False else b"%d" % (i % 9) for c in range(ncols)] for i in range(rows)]
                    J.write_csv(str(self.s.dir / "e.csv"), [b"h%d" % i for i in range(ncols)], recs)
                    for on, pairs in (("h0=h0:int", [(0, 0, "int", False)]), ("h0=h0:float", [(0, 0, "float", False)]), ("h0=h0", [(0, 0, None, False)])) + ((("h0=h0:int,h1=h1:int", [(0, 0, "int", False), (1, 1, "int", False)]),) if ncols > 1 else ()):
                        for jt in JTYPES:
                            x = run("--root", self.s.dir, "--join", "e.csv", "--on", on, "--join-type", jt, "--explain", *(["--join-prefix", "r_"] if jt in ("inner", "left") else []), "e.csv")
                            if x.status:
                                continue
                            at_most = x.data()["explain"]["join"]["build_state"]["accounted_at_most"]
                            f = R.accounted(str(self.s.dir / "e.csv"), pairs, jt)
                            self.assertGreaterEqual(at_most, f["held"], (ncols, rows, width, on, jt))

    def test_the_bound_for_the_keys_only_build_is_lower(self):
        inner = self.ex("--join", "b.csv", "--on", "k=id", "--join-prefix", "r_").data()["explain"]["join"]["build_state"]["accounted_at_most"]
        semi = self.ex("--join", "b.csv", "--on", "k=id", "--join-type", "semi").data()["explain"]["join"]["build_state"]["accounted_at_most"]
        self.assertLess(semi, inner)

    def test_it_refuses_what_the_run_refuses_before_a_row(self):
        for argv in (["--join", "b.csv", "--on", "k=id:int,n=nope"], ["--join", "b.csv", "--on", "k=id,k=id,x=y"], ["--join", "b.csv", "--on", "k=id", "--join-type", "left"], ["--join", "b.csv", "--on", "k=id:integer"],
                     ["--join", "b.csv", "--on", "k=id", "--join-type", "semi", "--join-prefix", "r_"]):
            with self.subTest(argv):
                got = self.ex(*argv)
                ran = run("--root", self.s.dir, *argv, "p.csv")
                self.assertEqual((got.status, got.first_rule()), (ran.status, ran.first_rule()))
                if ran.status:
                    self.assertEqual(got.doc()["errors"], ran.doc()["errors"][:len(got.doc()["errors"])])


class Fuzz(unittest.TestCase):
    """F: no trap on any plan of the new alphabet, every refusal is a rule of the catalogue, every repair that is offered is accepted."""

    TYPES = ["", ":int", ":dec(2)", ":float", ":dec(0)", ":bogus"]
    GOOD = [["--select", "p0"], ["--select", "b1,p0"], ["--where", "p0 != 'a'"], ["--where", "b1 = ''"], ["--where", "b1:int > 0"], ["--where", "p0:float < 3"], ["--limit", "3"], ["--limit", "2", "--from", "2"], ["--format", "csv"],
            ["--format", "jsonl"], ["--max-rows", "3"], ["--max-state-bytes", "300"], ["--max-state-bytes", "100"], ["--max-state-bytes", "4000"], ["--max-join-rows", "7"], ["--max-join-rows", "40"], ["--max-line-bytes", "30"],
            ["--threads", "4"], ["--rows"], ["--join-prefix", "x_"], ["--join-prefix", "p"], ["--explain"], ["--max-bytes", "60"]]

    def check(self, argv, r, rules, counts):
        self.assertNotIn(r.status, TRAPS, argv)
        self.assertGreaterEqual(r.status, 0, argv)
        if r.status == 0:
            counts["accepted"] += 1
            return
        counts["refused"] += 1
        try:
            errors = r.doc().get("errors") or []
        except (AssertionError, ValueError):
            tags = re.findall(rb"^table: ([a-z]+\.[a-z0-9-]+): ", r.stderr, re.M)
            self.assertTrue(tags, (argv, r.stdout[:300], r.stderr[:300]))
            for t in tags:
                self.assertIn(t.decode(), rules, argv)
            return
        self.assertTrue(errors, (argv, r.stdout[:300]))
        for e in errors:
            self.assertIn(e["rule"], rules, argv)
            repair = e.get("repair")
            if not repair:
                continue
            options = repair["options"] if repair["kind"] == "choose" else [repair] if repair["kind"] == "retry" else []
            for opt in options:
                counts["repairs"] += 1
                again = subprocess.run(opt["argv"], capture_output=True, input=b"", timeout=60)
                self.assertNotIn(again.returncode, TRAPS, opt["argv"])
                self.assertNotIn(b'"rule":"args.', again.stdout, ("a repair is refused by the flags", argv, opt["argv"]))
                self.assertNotIn(b"table: args.", again.stderr, ("a repair is refused by the flags", argv, opt["argv"]))
                if e["rule"] == "join.column-collision":
                    self.assertNotIn(b'"rule":"join.column-collision"', again.stdout, opt["argv"])
                    self.assertNotIn(b"table: join.column-collision", again.stderr, opt["argv"])

    def test_random_plans_over_random_files(self):
        rng = random.Random(1015)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        counts = {"accepted": 0, "refused": 0, "repairs": 0}
        s = Scratch()
        try:
            for i in range(500):
                _, ph, pr, bh, br, pairs, opts = R.random_shape(rng)
                if rng.random() < 0.4:
                    bh = list(bh)
                    k = rng.randrange(len(bh))
                    bh[k] = ph[min(k, len(ph) - 1)]
                J.write_case(str(s.dir), ph, pr, bh, br, opts)
                if rng.random() < 0.15:
                    which = rng.choice(["p.csv", "b.csv"])
                    data = (s.dir / which).read_bytes()
                    cut = rng.randrange(len(data) + 1)
                    data = rng.choice([data[:cut], data + b'x,"y"z\n', data + b"1,2,3,4,5,6,7\n", data[:cut] + b'"' + data[cut:], data + b",,,\n"])
                    (s.dir / which).write_bytes(data)
                # an --on text: the drawn pairs, or a junk one
                good = rng.random() < 0.8
                if good:
                    on = ",".join(("%s%s" % (ph[a].decode(), R.suffix_of(k)) if (u or rng.random() < 0.1 and ph[a] == bh[b]) else "%s=%s%s" % (ph[a].decode(), bh[b].decode(), R.suffix_of(k))) for a, b, k, u in pairs)
                else:
                    on = rng.choice(["p0=b0:bogus", "p0:int=b0", "#1", "p0=b0,", ",", "p0", "p0=b0,p0=b0,p0=b0,p0=b0,p0=b0,p0=b0,p0=b0,p0=b0,p0=b0", "p0=b0:dec(99)", "\\", "p0=nope:int", "#9=#1"])
                jt = rng.choice(list(JTYPES) + ["right"] * (not good))
                extra = sum(rng.sample(self.GOOD, rng.randint(0, 3)), [])
                if jt in ("semi", "anti") and rng.random() < 0.8:
                    extra = [x for x in extra if x != "x_" and x != "p"]
                    while "--join-prefix" in extra:
                        k = extra.index("--join-prefix")
                        del extra[k:k + 2]
                argv = ["--root", str(s.dir), "--join", "b.csv", "--on", on, "--join-type", jt] + extra
                probe = rng.choice([["p.csv"], ["p.csv"], []])
                r = run(*argv, *probe, stdin=b"p0\na\n" if not probe else None)
                self.check(argv + probe, r, rules, counts)
        finally:
            s.cleanup()
        self.assertGreater(counts["accepted"], 100, counts)
        self.assertGreater(counts["refused"], 100, counts)
        self.assertGreater(counts["repairs"], 5, counts)


class Authority(unittest.TestCase):
    def test_manifest_check_passes(self):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check"], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_row_is_unchanged(self):
        d = json.loads(subprocess.run([binary(), "introspect"], capture_output=True, check=True).stdout)
        self.assertEqual(sorted(d["authority"]["effects"]), ["args", "conc", "dir_read", "err_write", "file_read", "fs_read", "heap", "io_read", "io_write"])


if __name__ == "__main__":
    unittest.main()
