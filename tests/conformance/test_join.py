"""J1 (docs/joins.md sections 10 and 11, slice J1): the first working join -- `--join FILE --on PROBE=BUILD`, text keys, one pair, inner, csv and tsv, rows out.

The gates, each able to fail (scripts/join_mutants.py breaks the source in the ways of its list and every one must be noticed):

* O, the oracle: generated tables joined by a Python reference, by SQLite and by DuckDB (when it is installed), compared as the multiset of rows AND in the defined order (the
  probe's, then the build's), as csv and as json, from a file and from standard input, at 1 and 4 threads. The shapes: duplicates, no matches, all matches, big fan-out, empty
  tables, look-alike keys (007 and 7, -0 and 0, 1.5 and 1.50: text keys are bytes), empty keys, quoted keys with commas, quotes and newlines, CRLF and a BOM on either side, keys
  that are not UTF-8, one-column files, `#N`.
* A, the accounting: the program's count of the accounted bytes equals the formula of docs/joins.md 6.2 computed here from the file by another program, on the edges of the growth
  of the arrays (2^20 and 2^20 + 1 rows, a record buffer that doubles at the end of the file, repeated keys); the bound is exact (accepted at the formula, refused one byte below,
  naming the row), at 1, 2, 6 and 16 threads and for a standard input probe.
* The refusals of the flags and of the data, each with its rule and its facts; the build file read first; the repair of a collision is accepted.
* Threads: `--threads N` reads on one core with the same bytes (and `--explain` says so). Standard input as the probe: the same bytes as the file.
* F, fuzz: no trap on anything, every refusal tagged with a rule of the catalogue, every offered repair accepted.
"""

import csv
import io
import itertools
import json
import os
import pathlib
import random
import re
import shlex
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import unittest

import join_ref as J
from harness import ROOT, TRAPS, Scratch, binary, introspect, name_text, run, validate


def argv_of(d, pk, bk, prefix=None, extra=(), p="p.csv", b="b.csv"):
    a = ["--root", d, "--join", b, "--on", "%s=%s" % (pk, bk)]
    if prefix is not None:
        a += ["--join-prefix", prefix]
    return a + [str(x) for x in extra] + ([p] if p is not None else [])


def cells(rows):
    """Rows of json cells as bytes."""
    return [[name_text(c) for c in r] for r in rows]


class Case:
    """A generated case written under a scratch root."""

    def __init__(self, name, pheader, prows, bheader, brows, pk, bk, opts):
        self.name, self.pheader, self.prows, self.bheader, self.brows, self.pk, self.bk, self.opts = name, pheader, prows, bheader, brows, pk, bk, opts
        self.s = Scratch()
        J.write_case(str(self.s.dir), pheader, prows, bheader, brows, opts)
        self.pname, self.bname = self.pheader[pk].decode(), self.bheader[bk].decode()

    def cleanup(self):
        self.s.cleanup()


class Oracle(unittest.TestCase):
    """O: the tool against a reference and two SQL engines, rows and order."""

    def check(self, c, prefix=b""):
        prefix = c.opts.get("prefix", prefix)
        want_header, want = J.ref_join(c.pheader, c.prows, c.bheader, c.brows, c.pk, c.bk, prefix)
        tag = c.name
        lite, _ = J.sqlite_join(c.pheader, c.prows, c.bheader, c.brows, c.pk, c.bk)
        self.assertEqual(lite, want, "%s: the reference and SQLite disagree (the generator or the reference is wrong)" % tag)
        duck, sql = (None, None)
        if all(self.utf8(x) for r in c.prows + c.brows for x in r):
            duck, sql = J.duckdb_join(c.pheader, c.prows, c.bheader, c.brows, c.pk, c.bk)
        if duck is not None:
            self.assertEqual(duck, want, "%s: the reference and DuckDB disagree\n%s" % (tag, sql))
        d = str(c.s.dir)
        base = argv_of(d, c.pname, c.bname, prefix.decode() if prefix else None)
        # csv, the file as the probe
        r = run(*base, "--format", "csv")
        self.assertEqual(r.status, 0, "%s: %r" % (tag, r.stdout[:200] + r.stderr[:300]))
        got = J.read_csv_bytes(r.stdout)
        self.assertEqual(got, [want_header] + want, tag)
        self.assertEqual(r.stdout, J.render_rows([want_header] + want), "%s: the bytes of the csv" % tag)
        # the same from standard input, and at 4 threads (read on one core): the same bytes
        probe = open(os.path.join(d, "p.csv"), "rb").read()
        s = run(*argv_of(d, c.pname, c.bname, prefix.decode() if prefix else None, ["--format", "csv"], p=None), stdin=probe)
        self.assertEqual((s.status, s.stdout), (0, r.stdout), tag + ": standard input")
        t = run(*base, "--format", "csv", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7")
        self.assertEqual((t.status, t.stdout), (0, r.stdout), tag + ": threads")
        # json: the whole page (limit large enough)
        j = run(*base, "--limit", "1000000", "--max-bytes", "67108864")
        self.assertEqual(j.status, 0, tag)
        self.assertEqual(validate(j), [], tag)
        data = j.data()
        self.assertEqual((cells([data["columns"]])[0], cells(data["rows"]), data["row_count"], data["truncated"]), (want_header, want, len(want), False), tag)

    @staticmethod
    def utf8(b):
        try:
            b.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    def test_the_shapes(self):
        for name, ph, pr, bh, br, pk, bk, opts in J.cases():
            with self.subTest(name):
                c = Case(name, ph, pr, bh, br, pk, bk, opts)
                try:
                    self.check(c)
                finally:
                    c.cleanup()

    def test_the_shapes_with_a_prefix(self):
        for name, ph, pr, bh, br, pk, bk, opts in J.cases()[:6]:
            with self.subTest(name):
                c = Case(name, ph, pr, bh, br, pk, bk, opts)
                try:
                    self.check(c, b"d_")
                finally:
                    c.cleanup()

    def test_random_tables(self):
        rng = random.Random(20261015)
        for i in range(60):
            case = J.random_case(rng)
            with self.subTest(i):
                c = Case(*case)
                try:
                    self.check(c)
                finally:
                    c.cleanup()

    def test_tsv_and_semicolon(self):
        s = Scratch()
        try:
            for delim, name in (("\t", "tab"), (";", ";")):
                J.write_csv(str(s.dir / "p.csv"), [b"k", b"v"], [[b"a", b"1"], [b"b", b"2"], [b"a", b"3"]], delim=delim)
                J.write_csv(str(s.dir / "b.csv"), [b"id", b"w"], [[b"a", b"x"], [b"a", b"y"], [b"c", b"z"]], delim=delim)
                r = run("--root", s.dir, "--delimiter", name, "--join", "b.csv", "--on", "k=id", "--format", "csv", "p.csv")
                self.assertEqual(J.read_csv_bytes(r.stdout, delim), [[b"k", b"v", b"id", b"w"], [b"a", b"1", b"a", b"x"], [b"a", b"1", b"a", b"y"], [b"a", b"3", b"a", b"x"], [b"a", b"3", b"a", b"y"]])
        finally:
            s.cleanup()


class Order(unittest.TestCase):
    """The defined order: the probe's rows in the probe's order; for each, the build rows of its key in the build file's order. Not the order of the keys, not the order of the smaller file."""

    def test_probe_order_then_build_order(self):
        s = Scratch()
        try:
            s.write("p.csv", "k,v\nb,p1\na,p2\nb,p3\nz,p4\na,p5\n")
            s.write("b.csv", "id,w\na,b1\nb,b2\na,b3\nb,b4\nq,b5\n")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", "p.csv")
            self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "b,p1,b,b2", "b,p1,b,b4", "a,p2,a,b1", "a,p2,a,b3", "b,p3,b,b2", "b,p3,b,b4", "a,p5,a,b1", "a,p5,a,b3"])
            # the files swapped: the order is the other file's
            r = run("--root", s.dir, "--join", "p.csv", "--on", "id=k", "--format", "csv", "b.csv")
            self.assertEqual(r.stdout.decode().splitlines(), ["id,w,k,v", "a,b1,a,p2", "a,b1,a,p5", "b,b2,b,p1", "b,b2,b,p3", "a,b3,a,p2", "a,b3,a,p5", "b,b4,b,p1", "b,b4,b,p3"])
        finally:
            s.cleanup()

    def test_where_select_limit_from_work_on_the_joined_row(self):
        s = Scratch()
        try:
            s.write("p.csv", "k,v\nb,p1\na,p2\nb,p3\nz,p4\na,p5\n")
            s.write("b.csv", "id,w\na,b1\nb,b2\na,b3\nb,b4\nq,b5\n")
            base = ["--root", s.dir, "--join", "b.csv", "--on", "k=id"]
            r = run(*base, "--where", "w = b3 or w = b4", "p.csv")
            self.assertEqual(r.first_rule(), "where.syntax")
            r = run(*base, "--where", "w != b1 and v != p1", "--select", "v,w", "--format", "csv", "p.csv")
            self.assertEqual(r.stdout.decode().splitlines(), ["v,w", "p2,b3", "p3,b2", "p3,b4", "p5,b3"])
            # a page: --from counts joined rows, and the cursor is the next --from
            r = run(*base, "--limit", "3", "--from", "2", "--select", "v,w", "p.csv")
            d = r.data()
            self.assertEqual((d["rows"], d["truncated"], d["next"]), ([["p2", "b1"], ["p2", "b3"], ["p3", "b2"]], True, {"from": 5}))
            r = run(*base, "--limit", "3", "--from", "5", "--select", "v,w", "p.csv")
            d = r.data()
            self.assertEqual((d["rows"], d["truncated"], d["next"]), ([["p3", "b4"], ["p5", "b1"], ["p5", "b3"]], False, None))
            # a page with --where: next.from is a place in the joined rows, before the filter, as for a file
            r = run(*base, "--where", "w = b3", "--limit", "1", "--select", "v,w", "p.csv")
            self.assertEqual((r.data()["rows"], r.data()["next"]), ([["p2", "b3"]], {"from": 7}))
            # --rows: every column
            r = run(*base, "--rows", "--limit", "1", "p.csv")
            self.assertEqual(r.data()["columns"], ["k", "v", "id", "w"])
            # --format jsonl
            r = run(*base, "--format", "jsonl", "--limit", "2", "--select", "v,w", "p.csv")
            self.assertEqual(r.stdout.decode().splitlines(), ['{"v":"p1","w":"b2"}', '{"v":"p1","w":"b4"}'])
        finally:
            s.cleanup()

    def test_the_answer_does_not_depend_on_which_file_is_larger(self):
        s = Scratch()
        try:
            rng = random.Random(3)
            keys = [b"k%d" % i for i in range(20)]
            J.write_csv(str(s.dir / "p.csv"), [b"k", b"v"], J.table(rng, 2, 400, 0, keys))
            J.write_csv(str(s.dir / "b.csv"), [b"id", b"w"], J.table(rng, 2, 40, 0, keys))
            a = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", "p.csv")
            J.write_csv(str(s.dir / "b.csv"), [b"id", b"w"], J.table(rng, 2, 4000, 0, keys))
            b = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", "p.csv")
            self.assertEqual(a.stdout.splitlines()[0], b.stdout.splitlines()[0])
            self.assertTrue(a.stdout.startswith(b"k,v,id,w\n") and b.stdout.startswith(b"k,v,id,w\n"))      # the probe is the operand, whichever file is larger
        finally:
            s.cleanup()


class Keys(unittest.TestCase):
    """A key is the bytes of the cell, its quotes undone. There is no null."""

    def join(self, probe, build, extra=()):
        s = Scratch()
        try:
            s.write("p.csv", probe)
            s.write("b.csv", build)
            return run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", *extra, "p.csv")
        finally:
            s.cleanup()

    def test_text_keys_are_bytes_not_numbers(self):
        r = self.join("k,v\n007,a\n7,b\n-0,c\n0,d\n1.5,e\n1.50,f\n+7,g\n", "id,w\n7,x\n0,y\n1.50,z\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "7,b,7,x", "0,d,0,y", "1.50,f,1.50,z"])

    def test_case_and_spaces_matter(self):
        r = self.join("k,v\nA,1\na,2\n a,3\na ,4\n", "id,w\na,x\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "a,2,a,x"])

    def test_an_empty_key_matches_an_empty_key(self):
        r = self.join("k,v\n,p1\na,p2\n,p3\n", "id,w\n,b1\nz,b2\n,b3\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", ",p1,,b1", ",p1,,b3", ",p3,,b1", ",p3,,b3"])

    def test_the_empty_key_of_a_one_column_file_is_a_blank_line_and_no_record(self):
        # a one-column file has no empty cell to write but `""`: a blank line is skipped, as for every other plan
        r = self.join("k\na\n\nb\n", "id\na\n\nb\nb\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,id", "a,a", "b,b", "b,b"])
        r = self.join('k\na\n""\nb\n', 'id\n""\nb\n')
        self.assertEqual(r.stdout.decode().splitlines(), ["k,id", ",", "b,b"])

    def test_a_quoted_key_is_the_text_inside_its_quotes(self):
        r = self.join('k,v\n"a,b",1\n"say ""hi""",2\n"x\ny",3\nplain,4\n', 'id,w\n"a,b",X\n"say ""hi""",Y\n"x\ny",Z\n"plain",W\n')
        self.assertEqual(J.read_csv_bytes(r.stdout), [[b"k", b"v", b"id", b"w"], [b"a,b", b"1", b"a,b", b"X"], [b'say "hi"', b"2", b'say "hi"', b"Y"], [b"x\ny", b"3", b"x\ny", b"Z"], [b"plain", b"4", b"plain", b"W"]])

    def test_the_word_null_is_text(self):
        r = self.join("k,v\nNULL,1\nnull,2\n,3\n", "id,w\nNULL,X\n,Y\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "NULL,1,NULL,X", ",3,,Y"])

    def test_crlf_and_a_bom_are_not_part_of_a_key(self):
        r = self.join("﻿k,v\r\na,1\r\nb,2\r\n", "﻿id,w\r\nb,X\r\na,Y\r\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "a,1,a,Y", "b,2,b,X"])

    def test_keys_by_position_and_names_with_the_flags_escapes(self):
        s = Scratch()
        try:
            s.write("p.csv", 'x,"a,b",#c,d=e,f:g\n1,v,v,v,v\n')
            s.write("b.csv", 'u,"v,w",#z,p=q\nv,v,v,v\n')
            for on in ("#2=#2", "a\\,b=v\\,w", "\\#c=\\#z", "d\\=e=p\\=q", "f\\:g=#1", "#3=#1", "#5=#4"):
                r = run("--root", s.dir, "--join", "b.csv", "--on", on, "--join-prefix", "r_", "--format", "csv", "p.csv")
                self.assertEqual(r.status, 0, (on, r.stderr[:200]))
                self.assertEqual(r.stdout.decode().splitlines(), ["x,\"a,b\",#c,d=e,f:g,r_u,\"r_v,w\",r_#z,r_p=q", "1,v,v,v,v,v,v,v,v"], on)
        finally:
            s.cleanup()

    def test_a_header_with_a_name_twice_in_one_side_is_a_join_all_the_same(self):
        r = self.join("k,v,v\na,1,2\n", "id,w,w\na,x,y\n")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,v,id,w,w", "a,1,2,a,x,y"])
        # but a key that is twice in its own header is ambiguous, and a position is not
        s = Scratch()
        try:
            s.write("p.csv", "k,k\na,b\n")
            s.write("b.csv", "id\nb\n")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "p.csv")
            self.assertEqual((r.status, r.first_rule()), (8, "column.ambiguous"))
            self.assertEqual(r.error()["detail"]["input"], "probe")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "#2=id", "--format", "csv", "p.csv")
            self.assertEqual(r.stdout.decode().splitlines(), ["k,k,id", "a,b,b"])
        finally:
            s.cleanup()


class Columns(unittest.TestCase):
    """The header of a join is the probe's names, then the build's with the prefix. A name in both is a refusal; nothing is renamed on the way."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "id,name,x\n1,a,p\n2,b,q\n")
        self.s.write("b.csv", "id,city,x\n1,Z,r\n3,Y,s\n")

    def tearDown(self):
        self.s.cleanup()

    def test_a_name_in_both_is_a_collision_and_nothing_was_read_but_the_headers(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--format", "csv", "p.csv")
        self.assertEqual(r.status, 2)
        self.assertEqual(r.stdout, b"")
        self.assertTrue(r.stderr.startswith(b"table: join.column-collision: "))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (2, "join.column-collision"))
        e = r.error()
        self.assertEqual((e["detail"]["columns"], e["detail"]["prefix"], e["detail"]["path"], e["detail"]["collisions"]), (["id", "x"], "", "b.csv", 2))
        self.assertEqual(e["repair"]["kind"], "choose")
        self.assertEqual(len(e["repair"]["options"]), 1)
        # the offered repair is the invocation with --join-prefix and the stem of the build file's name, and it is accepted
        argv = e["repair"]["options"][0]["argv"]
        self.assertEqual(argv[-2:], ["--join-prefix", "b_"])
        got = subprocess.run(argv, capture_output=True, input=b"")
        self.assertEqual(got.returncode, 0, got.stdout[:300])
        d = json.loads(got.stdout)["data"]
        self.assertEqual(d["columns"], ["id", "name", "x", "b_id", "b_city", "b_x"])
        self.assertEqual(d["rows"], [["1", "a", "p", "1", "Z", "r"]])

    def test_a_prefix_makes_the_names_and_is_how_they_are_named(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--join-prefix", "r.", "--format", "csv", "--select", "name,r.city,r.x,x", "--where", "r.id = 1", "p.csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["name,r.city,r.x,x", "a,Z,r,p"])

    def test_the_repair_is_not_offered_when_it_would_collide_or_a_prefix_was_given(self):
        self.s.write("c.csv", "id,c_id\n1,2\n")
        self.s.write("p2.csv", "id,c_id\n1,2\n")
        r = run("--root", self.s.dir, "--join", "c.csv", "--on", "id=id", "p2.csv")
        e = r.error()
        self.assertEqual((r.first_rule(), e["repair"]["kind"]), ("join.column-collision", "none"))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--join-prefix", "", "p.csv")
        self.assertEqual((r.first_rule(), r.error()["repair"]["kind"]), ("join.column-collision", "none"))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--join-prefix", "x", "p.csv")
        self.assertEqual(r.status, 0)                                                               # xid, xcity, xx: no collision with id, name, x
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--join-prefix", "n", "p.csv")
        self.assertEqual(r.status, 0)                                                               # nid, ncity, nx
        self.s.write("p3.csv", "id,nid\n1,2\n")
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--join-prefix", "n", "p3.csv")
        self.assertEqual(r.first_rule(), "join.column-collision")                                   # nid is in both

    def test_the_columns_that_collide_are_listed_at_most_32(self):
        self.s.write("w1.csv", ",".join("c%d" % i for i in range(40)) + "\n" + ",".join("1" for _ in range(40)) + "\n")
        r = run("--root", self.s.dir, "--join", "w1.csv", "--on", "c0=c0", "w1.csv")
        d = r.error()["detail"]
        self.assertEqual((len(d["columns"]), d["collisions"]), (32, 40))

    def test_a_key_column_the_header_lacks(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "nope=id", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (3, "column.unknown"))
        d = r.error()["detail"]
        self.assertEqual((d["flag"], d["input"], d["path"], d["name"], d["available"]), ("--on", "probe", "p.csv", "nope", ["id", "name", "x"]))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=nope", "p.csv")
        d = r.error()["detail"]
        self.assertEqual((r.first_rule(), d["input"], d["path"], d["available"]), ("column.unknown", "build", "b.csv", ["id", "city", "x"]))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "#9=#1", "p.csv")
        self.assertEqual(r.first_rule(), "column.unknown")
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "#4=#1", "p.csv")             # p.csv has 3 columns: #4 is the first one past the end
        self.assertEqual((r.first_rule(), r.error()["detail"]["input"]), ("column.unknown", "probe"))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "#1=#4", "p.csv")
        self.assertEqual((r.first_rule(), r.error()["detail"]["input"]), ("column.unknown", "build"))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "#0=#1", "p.csv")
        self.assertEqual(r.first_rule(), "column.unknown")
        # both are wrong: the build file's is first
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "a=b", "p.csv")
        self.assertEqual([e["detail"]["input"] for e in r.doc()["errors"]], ["build", "probe"])

    def test_a_zero_byte_file_has_no_columns(self):
        self.s.write("empty.csv", "")
        for probe, build in (("empty.csv", "b.csv"), ("p.csv", "empty.csv")):
            r = run("--root", self.s.dir, "--join", build, "--on", "id=id", probe)
            self.assertEqual(r.first_rule(), "column.unknown", (probe, build))
            self.assertEqual(r.error()["detail"]["available"], [])

    def test_a_select_of_a_name_the_joined_header_lacks_lists_the_joined_header(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "id=id", "--join-prefix", "r_", "--select", "city", "p.csv")
        self.assertEqual(r.first_rule(), "select.unknown-column")
        self.assertEqual(r.error()["detail"]["available"], ["id", "name", "x", "r_id", "r_city", "r_x"])


class Flags(unittest.TestCase):
    """What is wrong with the flags of a join is refused before any byte is read, with a rule and a hint."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v\na,1\n")
        self.s.write("b.csv", "id,w\na,2\n")

    def tearDown(self):
        self.s.cleanup()

    def rule(self, *argv, file="p.csv"):
        r = run("--root", self.s.dir, *argv, file)
        return r.status, r.first_rule()

    def test_the_flags_that_need_each_other(self):
        self.assertEqual(self.rule("--join", "b.csv"), (2, "args.required-flag"))
        for flag in (["--on", "k=id"], ["--join-prefix", "r_"], ["--max-join-rows", "5"], ["--join-type", "inner"]):
            self.assertEqual(self.rule(*flag), (2, "args.required-flag"), flag)

    def test_the_build_side_cannot_be_standard_input(self):
        r = run("--root", self.s.dir, "--join", "-", "--on", "k=id", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"))
        self.assertEqual(r.error()["detail"]["flags"], "--join")
        # a file called - is ./-
        self.s.write("-", "id,w\na,2\n")
        r = run("--root", self.s.dir, "--join", "./-", "--on", "k=id", "--format", "csv", "p.csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,id,w", "a,1,a,2"])

    def test_the_forms_of_on_that_are_never_a_pair(self):
        # (J2 takes a second pair, a type and a shared name: test_join_j2.py has the grammar of --on whole)
        cases = {"k=": "empty name", "=id": "empty name", "k=id=w": "two equals signs", "k\\x=id": "bad escape", "k=id\\": "trailing backslash"}
        for on, why in cases.items():
            with self.subTest(on):
                r = run("--root", self.s.dir, "--join", "b.csv", "--on", on, "p.csv")
                self.assertEqual((r.status, r.first_rule()), (2, "args.bad-value"), why)
                self.assertEqual(r.error()["detail"]["flags"], "--on")

    def test_a_join_gives_rows_in_this_version(self):
        for extra in (["--group", "k"], ["--agg", "count"], ["--order-by", "k"], ["--sort", "count"], ["--top", "1"], ["--report", "types"]):
            with self.subTest(extra):
                self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", *extra), (2, "args.conflict"))
        self.s.write("j.jsonl", '{"k":"a"}\n')
        self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--input-format", "jsonl", "--fields", "k", file="j.jsonl"), (2, "args.conflict"))
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id", "--group", "k", "p.csv")
        self.assertIn("pipe", r.error()["hint"])

    def test_right_full_and_cross_are_not_join_types(self):
        for t in ("right", "full", "cross", "LEFT"):
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--join-type", t), (2, "args.bad-value"), t)
        for t in ("inner", "left", "semi", "anti"):
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--join-type", t)[0], 0, t)

    def test_max_join_rows_is_1_to_a_billion(self):
        for v in ("0", "1000000001"):
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--max-join-rows", v), (2, "args.bad-value"), v)
        for v in ("1", "1000000000"):
            self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "--max-join-rows", v)[0], 0, v)

    def test_a_query_and_a_join_are_two_plans(self):
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id", "--query", "select * from 'p.csv'")
        self.assertEqual((r.status, r.first_rule()), (2, "args.conflict"))
        self.assertIn("--join", r.error()["detail"]["flags"])
        r = run("--root", self.s.dir, "--query", "select * from 'p.csv' join 'b.csv' on 1 = 1")
        self.assertEqual(r.first_rule(), "query.unsupported")

    def test_two_operands_and_the_build_path_rules(self):
        self.assertEqual(self.rule("--join", "b.csv", "--on", "k=id", "p.csv"), (2, "args.too-many-operands"))
        os.symlink(self.s.dir / "b.csv", self.s.dir / "link.csv")
        os.mkdir(self.s.dir / "sub")
        cases = [("../b.csv", "path.dotdot"), ("sub/../b.csv", "path.dotdot"), ("..", "path.dotdot"), ("/etc/passwd", "path.outside-root"), ("/", "path.outside-root"),
                 (str(self.s.dir / "b.csv"), "path.absolute"), ("link.csv", "path.symlink"), ("", "args.bad-value"), ("x" * 5000, "path.too-long"), ("nope.csv", "io.not-found"),
                 ("sub", "io.is-a-directory"), ("b.csv/x", "io.not-a-directory")]
        for path, rule in cases:
            with self.subTest(path[:30]):
                r = run("--root", self.s.dir, "--join", path, "--on", "k=id", "p.csv")
                self.assertEqual(r.first_rule(), rule)

    def test_the_join_file_may_be_the_operand(self):
        r = run("--root", self.s.dir, "--join", "p.csv", "--on", "k=k", "--join-prefix", "r_", "--format", "csv", "p.csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["k,v,r_k,r_v", "a,1,a,1"])


class DataRefusals(unittest.TestCase):
    """Each file's own rules apply to it: the build file is read first and its refusals come first; the probe's come in the probe's order."""

    def run_join(self, probe, build, *extra):
        s = Scratch()
        try:
            s.write("p.csv", probe)
            s.write("b.csv", build)
            return run("--root", s.dir, "--join", "b.csv", "--on", "k=id", *extra, "p.csv")
        finally:
            s.cleanup()

    def test_a_ragged_row_in_either_file(self):
        r = self.run_join("k,v\na,1\n", "id,w\na,2\nb\n")
        e = r.error()
        self.assertEqual((r.status, e["rule"], e["detail"]["path"], e["detail"]["first_row"], e["detail"]["first_line"], e["detail"]["expected"], e["detail"]["found"]), (8, "parse.csv-ragged-row", "b.csv", 2, 3, 2, 1))
        r = self.run_join("k,v\na,1\nb,2,3\n", "id,w\na,2\n")
        e = r.error()
        self.assertEqual((e["rule"], e["detail"]["path"], e["detail"]["first_row"], e["detail"]["expected"], e["detail"]["found"]), ("parse.csv-ragged-row", "p.csv", 2, 2, 3))
        # the build is read first: its refusal, and only its
        r = self.run_join("k,v\na,1\nb,2,3\n", "id,w\na,2\nb\n")
        self.assertEqual([x["detail"]["path"] for x in r.doc()["errors"]], ["b.csv"])
        # as csv the rows before the probe's ragged row are written, and the exit says it is a prefix
        r = self.run_join("k,v\na,1\nb,2,3\n", "id,w\na,2\n", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (8, b"k,v,id,w\na,1,a,2\n"))
        self.assertIn(b"parse.csv-ragged-row", r.stderr)

    def test_a_bad_quote_in_either_file(self):
        for probe, build, path in (('k,v\na,"x"y\n', "id,w\na,2\n", "p.csv"), ("k,v\na,1\n", 'id,w\na,"x"y\n', "b.csv")):
            r = self.run_join(probe, build)
            self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["path"]), (8, "parse.csv-bad-quote", path))
        r = self.run_join('k,v\na,1\n', 'id,w\na,"open\n')
        self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("parse.csv-unterminated-quote", "b.csv"))
        r = self.run_join('k,v\na,"open\n', 'id,w\na,1\n')
        self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("parse.csv-unterminated-quote", "p.csv"))

    def test_a_long_line_and_a_large_record(self):
        r = self.run_join("k,v\na,1\n", "id,w\na,%s\n" % ("x" * 200), "--max-line-bytes", "100")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["path"]), (8, "limit.line-too-long", "b.csv"))
        r = self.run_join("k,v\na,%s\n" % ("x" * 200), "id,w\na,1\n", "--max-line-bytes", "100")
        self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("limit.line-too-long", "p.csv"))
        r = self.run_join("k,v\na,1\n", 'id,w\na,"%s\n%s\n%s"\n' % ("x" * 80, "y" * 80, "z" * 80), "--max-line-bytes", "100")
        self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("limit.record-too-large", "b.csv"))
        r = self.run_join("k,v\na,1\n", "x" * 200 + ",w\na,1\n", "--max-line-bytes", "100")
        self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("limit.line-too-long", "b.csv"))

    def test_max_rows_bounds_the_probe_and_refuses_a_build(self):
        # the probe: json says truncated, csv refuses (as for a file)
        r = self.run_join("k,v\na,1\na,2\na,3\n", "id,w\na,x\n", "--max-rows", "2")
        d = r.data()
        self.assertEqual((r.status, d["row_count"], d["truncated"], d["next"]), (0, 2, True, None))
        r = self.run_join("k,v\na,1\na,2\na,3\n", "id,w\na,x\n", "--max-rows", "2", "--format", "csv")
        self.assertEqual((r.status, r.stdout, ), (8, b"k,v,id,w\na,1,a,x\na,2,a,x\n"))
        self.assertIn(b"limit.too-many-rows", r.stderr)
        # the build: a truncated build is a wrong answer, so it is always a refusal, also as json
        r = self.run_join("k,v\na,1\n", "id,w\na,x\nb,y\nc,z\n", "--max-rows", "2")
        self.assertEqual((r.status, r.first_rule(), r.error()["detail"]["path"], r.error()["detail"]["limit"]), (8, "limit.too-many-rows", "b.csv", 2))
        r = self.run_join("k,v\na,1\n", "id,w\na,x\nb,y\n", "--max-rows", "2")
        self.assertEqual(r.status, 0)

    def test_header_only_and_empty_tables_give_no_rows(self):
        for probe, build in (("k,v\n", "id,w\na,1\n"), ("k,v\na,1\n", "id,w\n"), ("k,v\n", "id,w\n")):
            r = self.run_join(probe, build, "--format", "csv")
            self.assertEqual((r.status, r.stdout), (0, b"k,v,id,w\n"))
            d = self.run_join(probe, build).data()
            self.assertEqual((d["columns"], d["rows"], d["row_count"], d["truncated"]), (["k", "v", "id", "w"], [], 0, False))

    def test_a_refusal_in_json_is_one_document(self):
        for probe, build in (("k,v\na,1\nb,2,3\n", "id,w\na,2\n"), ("k,v\na,1\n", "id,w\na,2\nb\n"), ("k,v\n", "nope,w\na,2\n")):
            r = self.run_join(probe, build)
            self.assertEqual(validate(r), [], r)
            self.assertFalse(r.doc()["ok"])

    def test_a_where_that_reads_a_typed_cell_names_the_probe_row(self):
        r = self.run_join("k,v\na,1\na,x\n", "id,w\na,2\na,3\n", "--where", "v:int > 0")
        e = r.error()
        self.assertEqual((e["rule"], e["detail"]["context"], e["detail"]["column"], e["detail"]["value"], e["detail"]["row"], e["detail"]["line"]), ("value.not-integer", "where", "v", "x", 2, 3))


class MaxJoinRows(unittest.TestCase):
    """--max-join-rows bounds the rows produced, before --where; the refusal is at the first row past it, with the exact facts."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v\nz,0\n" + "".join("h,%d\n" % i for i in range(50)))
        self.s.write("b.csv", "id,w\n" + "".join("h,%d\n" % i for i in range(40)) + "z,1\n")

    def tearDown(self):
        self.s.cleanup()

    def test_the_threshold_to_the_row(self):
        total = 1 + 50 * 40
        base = ["--root", self.s.dir, "--join", "b.csv", "--on", "k=id", "--where", "w = nothing"]
        r = run(*base, "--max-join-rows", str(total), "p.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        r = run(*base, "--max-join-rows", str(total - 1), "p.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-join-rows"))
        d = r.error()["detail"]
        self.assertEqual((d["limit"], d["probe_row"], d["probe_line"], d["matches"], d["max_matches_so_far"], d["input"], d["path"]), (total - 1, 51, 52, 40, 40, "probe", "p.csv"))
        for n in (1, 2, 40, 41, 1000):
            r = run(*base, "--max-join-rows", str(n), "p.csv")
            self.assertEqual(r.first_rule(), "limit.too-many-join-rows", n)
            d = r.error()["detail"]
            # the row that would be the n+1st: its probe row is the one the n+1st joined row belongs to
            self.assertEqual(d["probe_row"], 2 + (n - 1) // 40 if n >= 1 else 1, n)
        # a page stops the probe early: a small page of a huge join is not refused
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id", "--max-join-rows", "10", "--limit", "5", "p.csv")
        self.assertEqual((r.status, r.data()["row_count"], r.data()["truncated"]), (0, 5, True))

    def test_the_most_matches_so_far_is_a_maximum(self):
        # a probe row with 40 matches, then one with 1, which is the one that passes the bound: the most is still 40
        self.s.write("p3.csv", "k,v\nh,0\nz,1\n")
        r = run("--root", self.s.dir, "--join", "b.csv", "--on", "k=id", "--max-join-rows", "40", "p3.csv")
        d = r.error()["detail"]
        self.assertEqual((r.first_rule(), d["probe_row"], d["matches"], d["max_matches_so_far"]), ("limit.too-many-join-rows", 2, 1, 40))

    def test_the_default_is_ten_million(self):
        d = introspect()
        lim = {l["name"]: (l["default"], l["ceiling"]) for l in d["limits"]}
        self.assertEqual(lim["max-join-rows"], (10000000, 1000000000))
        # 3162 x 3163 = 10,001,... rows is past it: a hot key on both sides, nothing printed (--where nothing)
        n = 3163
        self.s.write("hp.csv", "k,v\n" + "h,1\n" * n)
        self.s.write("hb.csv", "id,w\n" + "h,1\n" * n)
        r = run("--root", self.s.dir, "--join", "hb.csv", "--on", "k=id", "--where", "w = nothing", "hp.csv")
        self.assertEqual(r.first_rule(), "limit.too-many-join-rows")
        self.assertEqual(r.error()["detail"]["limit"], 10000000)
        self.assertEqual(r.error()["detail"]["probe_row"], 10000000 // n + 1)

    def test_it_counts_before_where_and_after_nothing_else(self):
        # 3 probe rows of the key, 2 build rows of it: 6 joined rows, whatever --where keeps
        self.s.write("p2.csv", "k,v\nh,1\nh,2\nh,3\n")
        self.s.write("b2.csv", "id,w\nh,a\nh,b\n")
        base = ["--root", self.s.dir, "--join", "b2.csv", "--on", "k=id", "--where", "w = a", "p2.csv"]
        self.assertEqual(run("--max-join-rows", "6", *base).status, 0)
        self.assertEqual(run("--max-join-rows", "5", *base).first_rule(), "limit.too-many-join-rows")


class Accounting(unittest.TestCase):
    """A: the accounted bytes of the build equal the formula of docs/joins.md 6.2, and the bound is exact."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def build_file(self, name, header, rows, **kw):
        p = str(self.s.dir / name)
        J.write_csv(p, header, rows, **kw)
        return p

    def check_exact(self, name, keycol=0, probe="p.csv", threads=(1,), stdin=False):
        """Accepted at the formula, refused one below, with the facts of the row that did not fit (independent of the program)."""
        f = J.accounted(str(self.s.dir / name), keycol)
        key = {0: "k", 1: "v"}[keycol]
        for t in threads:
            argv = ["--root", self.s.dir, "--join", name, "--on", "k=%s" % key, "--join-prefix", "r_", "--where", "k = no-such-key", "--threads", t, "--format", "csv"]
            if stdin:
                got = run(*argv, "--max-state-bytes", f["held"], stdin=b"k,v\nzz,1\n")
                low = run(*argv, "--max-state-bytes", f["held"] - 1, stdin=b"k,v\nzz,1\n")
            else:
                got = run(*argv, "--max-state-bytes", f["held"], probe)
                low = run(*argv, "--max-state-bytes", f["held"] - 1, probe)
            self.assertEqual(got.status, 0, "%s: refused at the formula %d: %r" % (name, f["held"], got.stderr[:300]))
            self.assertEqual(low.status, 8, "%s: accepted one byte below the formula %d" % (name, f["held"]))
            self.assertIn(b"limit.state-too-large", low.stderr)
        # the facts, from the json refusal of the same call
        argv = ["--root", self.s.dir, "--join", name, "--on", "k=%s" % key, "--join-prefix", "r_", "--where", "k = no-such-key", "--max-state-bytes", f["held"] - 1, probe]
        r = run(*argv)
        e = r.error()
        d = e["detail"]
        self.assertEqual(e["rule"], "limit.state-too-large")
        self.assertEqual((d["holder"], d["input"], d["path"], d["limit"]), ("join-build", "build", name, f["held"] - 1), name)
        self.assertEqual((d["held"], d["rows_held"], d["build_row"]), (f["before_last"], f["rows"] - 1, f["rows"]), "%s: the program's count before the last row is not the formula's" % name)
        self.assertEqual(d["build_line"], f["rows"] + 1)
        self.assertEqual((d["build_bytes_read"], d["build_file_bytes"]), (f["size"], f["size"]))
        return f

    def probe(self):
        self.build_file("p.csv", [b"k", b"v"], [[b"x", b"1"]])

    def test_the_formula_on_the_edges_of_the_growth_of_the_arrays(self):
        self.probe()
        rng = random.Random(5)
        # 2^20 and 2^20 + 1 rows of unique keys: every row-indexed array has just filled / has just doubled and is half empty
        for rows in (1 << 20, (1 << 20) + 1):
            name = "u%d.csv" % rows
            self.build_file(name, [b"k", b"v"], ([b"u%07x" % i, b"%d" % (i % 10)] for i in rng.sample(range(rows * 2), rows)))
            f = self.check_exact(name)
            self.assertEqual((f["rows"], f["distinct"]), (rows, rows))
            os.unlink(self.s.dir / name)

    def test_a_record_buffer_that_doubles_at_the_end_of_the_file(self):
        self.probe()
        # the file of the spike (docs/joins.md 15.1, `wide_pow2`): 100,000 records of 671 bytes (the first 8,900 of 672), whose sum is 2^26 + 36 bytes, so the buffer of the records, which
        # starts at 64 KiB and doubles, holds exactly 2^26 bytes and has to double a last time at the end of the file
        rng = random.Random(6)
        rows = []
        total = 0
        for i in range(100000):
            key = b"u%08x-%d" % (rng.getrandbits(32), i)
            size = 672 if i < 8900 else 671
            row = [key, b"x" * (size - len(key) - 1)]
            total += len(key) + 1 + len(row[1])
            rows.append(row)
        self.assertEqual(total, (1 << 26) + 36)
        self.build_file("wide.csv", [b"k", b"v"], rows)
        f = self.check_exact("wide.csv")
        self.assertEqual((f["rows"], f["distinct"]), (100000, 100000))
        self.assertEqual(f["held"], total + 16 * 100000 + sum(len(r[0]) for r in rows) + 60 * 100000)

    def test_repeated_keys_chains_of_ten(self):
        self.probe()
        rng = random.Random(7)
        rows = [[b"d%04d" % (i % 10000), b"%d" % i] for i in range(100000)]
        rng.shuffle(rows)
        self.build_file("dup.csv", [b"k", b"v"], rows)
        f = self.check_exact("dup.csv")
        self.assertEqual((f["rows"], f["distinct"]), (100000, 10000))

    def test_quoted_keys_and_crlf_and_a_bom(self):
        self.probe()
        rows = [[b'q"%d' % i, b"a,b"] for i in range(2000)] + [[b"a,b", b"x"]] * 5 + [[b"k%d" % (i % 7), b"y"] for i in range(2000)] + [[b"", b"e"], [b"", b"f"]]
        self.build_file("q.csv", [b"k", b"v"], rows, crlf=True, bom=True)
        self.check_exact("q.csv")
        self.build_file("q2.csv", [b"k", b"v"], rows, quote_all=True)
        self.check_exact("q2.csv")
        self.build_file("q3.csv", [b"k", b"v"], rows, final_newline=False)
        self.check_exact("q3.csv")

    def test_the_key_in_another_column(self):
        self.probe()
        rows = [[b"%d" % i, b"key%d" % (i % 33)] for i in range(3000)]
        self.build_file("kc.csv", [b"k", b"v"], rows)
        self.check_exact("kc.csv", keycol=1)

    def test_the_bound_is_one_number_at_every_thread_count_and_for_a_pipe(self):
        self.probe()
        rng = random.Random(8)
        rows = [[b"s%d" % rng.randrange(500), b"%d" % i] for i in range(5000)]
        self.build_file("m.csv", [b"k", b"v"], rows)
        self.check_exact("m.csv", threads=(1, 2, 6, 16))
        self.check_exact("m.csv", stdin=True)

    def test_a_bound_of_one_byte_and_of_the_formula_of_an_empty_build(self):
        self.probe()
        self.build_file("e.csv", [b"k", b"v"], [])
        r = run("--root", self.s.dir, "--join", "e.csv", "--on", "k=k", "--join-prefix", "r_", "--max-state-bytes", "1", "p.csv")
        self.assertEqual(r.status, 0, "an empty build holds nothing")
        self.build_file("one.csv", [b"k", b"v"], [[b"a", b"b"]])
        f = J.accounted(str(self.s.dir / "one.csv"), 0)
        self.assertEqual(f["held"], len("a,b") + 16 + 1 + 60)
        self.assertEqual(run("--root", self.s.dir, "--join", "one.csv", "--on", "k=k", "--join-prefix", "r_", "--max-state-bytes", str(f["held"]), "p.csv").status, 0)
        self.assertEqual(run("--root", self.s.dir, "--join", "one.csv", "--on", "k=k", "--join-prefix", "r_", "--max-state-bytes", str(f["held"] - 1), "p.csv").first_rule(), "limit.state-too-large")

    def test_the_refusal_says_the_resident_set_may_be_three_times_the_number(self):
        self.probe()
        self.build_file("one.csv", [b"k", b"v"], [[b"a", b"b"]])
        r = run("--root", self.s.dir, "--join", "one.csv", "--on", "k=k", "--join-prefix", "r_", "--max-state-bytes", "10", "p.csv")
        self.assertIn("three times", r.error()["hint"])
        self.assertIn("three times", [f for f in introspect()["flags"] if f["name"] == "--max-state-bytes"][0]["help"])

    def test_the_formula_in_the_help(self):
        helps = {f["name"]: f["help"] for f in introspect()["flags"]}
        self.assertIn("plus 16", helps["--max-state-bytes"])
        self.assertIn("plus 60", helps["--max-state-bytes"])
        self.assertIn("not resident memory", helps["--max-state-bytes"])
        self.assertIn("1.5 to 2.8", helps["--max-state-bytes"])


class Stdin(unittest.TestCase):
    """ST: the probe from standard input is the probe from the file, in every way the bytes can arrive."""

    def test_the_same_bytes_whatever_the_pipe_does(self):
        rng = random.Random(9)
        keys = [b"k%d" % i for i in range(30)]
        s = Scratch()
        try:
            J.write_csv(str(s.dir / "b.csv"), [b"id", b"w"], J.table(rng, 2, 80, 0, keys))
            J.write_csv(str(s.dir / "p.csv"), [b"k", b"v"], J.table(rng, 2, 3000, 0, keys + [b"none"]))
            probe = (s.dir / "p.csv").read_bytes()
            for extra in ([], ["--threads", "4"], ["--format", "csv"], ["--limit", "10"], ["--from", "7", "--limit", "10"], ["--max-rows", "100", "--format", "csv"]):
                f = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", *extra, "p.csv")
                for operand in (["-"], []):
                    g = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", *extra, *operand, stdin=probe)
                    self.assertEqual((g.status, g.stdout, g.stderr), (f.status, f.stdout, f.stderr), (extra, operand))
        finally:
            s.cleanup()

    def test_a_pipe_dripped_a_byte_at_a_time_and_cut_inside_a_quoted_field(self):
        s = Scratch()
        try:
            J.write_csv(str(s.dir / "b.csv"), [b"id", b"w"], [[b"a,b", b"1"], [b'q"q', b"2"], [b"n\nl", b"3"]])
            probe = b'k,v\n"a,b",x\n"q""q",y\n"n\nl",z\nnone,w\n'
            want = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", stdin=probe)
            self.assertEqual(want.status, 0)
            p = subprocess.Popen([binary(), "--root", str(s.dir), "--join", "b.csv", "--on", "k=id", "--format", "csv"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            import time
            for i in range(len(probe)):
                p.stdin.write(probe[i:i + 1])
                p.stdin.flush()
                if i % 9 == 0:
                    time.sleep(0.002)
            p.stdin.close()
            out = p.stdout.read()
            p.wait()
            self.assertEqual((p.returncode, out), (0, want.stdout))
        finally:
            s.cleanup()

    def test_the_build_is_refused_before_the_rows_of_the_pipe_are_read(self):
        # the header of the probe is read first (a collision is refused before any row of either file); its rows are not, until the build is whole: an endless producer
        # of rows would never end if they were
        s = Scratch()
        try:
            s.write("b.csv", "id,w\na,1\nb\n")
            producer = subprocess.Popen(["yes", "a"], stdout=subprocess.PIPE)
            p = subprocess.Popen([binary(), "--root", str(s.dir), "--join", "b.csv", "--on", "a=id", "--format", "csv"], stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            producer.stdout.close()
            out, err = p.communicate(timeout=30)
            producer.kill()
            producer.wait()
            self.assertEqual(p.returncode, 8)
            self.assertIn(b"parse.csv-ragged-row", err)
        finally:
            s.cleanup()

    def test_a_refusal_of_the_probe_names_the_dash(self):
        s = Scratch()
        try:
            s.write("b.csv", "id,w\na,1\n")
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", stdin=b"k,v\na,1\nb,2,3\n")
            self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("parse.csv-ragged-row", "-"))
            r = run("--root", s.dir, "--join", "b.csv", "--on", "nope=id", stdin=b"k,v\na,1\n")
            self.assertEqual((r.first_rule(), r.error()["detail"]["path"], r.error()["detail"]["input"]), ("column.unknown", "-", "probe"))
            r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", stdin=b"")
            self.assertEqual((r.first_rule(), r.error()["detail"]["path"]), ("column.unknown", "-"))
        finally:
            s.cleanup()

    def test_the_join_file_is_found_beneath_the_root_for_a_pipe_too(self):
        s = Scratch()
        try:
            r = run("--root", s.dir, "--join", "../x.csv", "--on", "k=id", stdin=b"k\na\n")
            self.assertEqual(r.first_rule(), "path.dotdot")
            r = run("--root", s.dir, "--join", "nope.csv", "--on", "k=id", stdin=b"k\na\n")
            self.assertEqual(r.first_rule(), "io.not-found")
        finally:
            s.cleanup()

    def test_no_thread_is_started_for_a_join(self):
        # --threads is accepted and the join is read on one core with the same bytes; the explain says why
        s = Scratch()
        try:
            s.write("p.csv", "k,v\n" + "a,1\n" * 20000)
            s.write("b.csv", "id,w\na,2\n")
            one = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", "p.csv")
            for t in (2, 3, 4, 8, 16, 64):
                r = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", "--threads", t, "--parallel-min-bytes", "0", "--chunk-bytes", "1000", "p.csv")
                self.assertEqual((r.status, r.stdout), (0, one.stdout), t)
            x = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--threads", "4", "--explain", "p.csv").data()["explain"]
            self.assertEqual(x["work"]["threads"], {"requested": 4, "effective": 1, "reason": "the join is read on one core"})
            self.assertEqual(x["join"]["work"]["threads"], {"requested": 4, "effective": 1, "reason": "the join is read on one core"})
            self.assertTrue(any("--threads has no effect on a join" in n for n in x["notes"]))
            y = run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--explain", "p.csv").data()["explain"]
            self.assertFalse(any("--threads" in n for n in y["notes"]))
        finally:
            s.cleanup()


class Explain(unittest.TestCase):
    """--explain says the join and refuses what the run refuses before a row."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v\na,1\nb,2\n")
        self.s.write("b.csv", "id,w\na,2\n")

    def tearDown(self):
        self.s.cleanup()

    def ex(self, *argv, stdin=None):
        return run("--root", self.s.dir, *argv, "--explain", *([] if stdin is not None else ["p.csv"]), stdin=stdin)

    def test_the_join_member(self):
        r = self.ex("--join", "b.csv", "--on", "k=id", "--join-prefix", "r_", "--threads", "3")
        self.assertEqual(validate(r), [])
        x = r.data()["explain"]
        j = x["join"]
        self.assertEqual((j["type"], j["probe"], j["build"], j["prefix"], j["using"], j["output_columns"], j["order"], j["max_join_rows"]), ("inner", "p.csv", "b.csv", "r_", [], 4, "probe order, then build order", 10000000))
        self.assertEqual(j["keys"], [{"probe": "k", "build": "id", "type": "text", "probe_position": 1, "build_position": 1}])
        bs = j["build_state"]
        size = os.path.getsize(self.s.dir / "b.csv")
        self.assertEqual((bs["file_bytes"], bs["header_bytes"], bs["budget"], bs["may_pass_bound"]), (size, len("id,w\n"), 67108864, False))
        self.assertGreaterEqual(bs["accounted_at_most"], J.accounted(str(self.s.dir / "b.csv"), 0)["held"])
        self.assertEqual(j["work"], {"class": "join: build then stream", "first_output_after": "the whole build", "threads": {"requested": 3, "effective": 1, "reason": "the join is read on one core"}})
        self.assertEqual(x["output"]["columns"], ["k", "v", "r_id", "r_w"])
        self.assertEqual(x["work"]["class"], "join: build then stream")
        self.assertEqual(x["work"]["reads"], "the whole build file, then the probe until the page is full")
        for rule in ("limit.state-too-large", "limit.too-many-join-rows", "limit.too-many-rows", "join.column-collision", "column.unknown", "parse.csv-ragged-row"):
            self.assertIn(rule, x["refusals"]["possible"])
        self.assertTrue(any("an empty key matches an empty key" in n for n in x["notes"]))
        self.assertEqual(x["plan"]["form"], "rows")

    def test_may_pass_the_bound_is_said_only_when_the_upper_bound_is_over_it(self):
        size = os.path.getsize(self.s.dir / "b.csv")
        at_most = self.ex("--join", "b.csv", "--on", "k=id", "--join-prefix", "r_").data()["explain"]["join"]["build_state"]["accounted_at_most"]
        for budget, want in ((at_most, False), (at_most - 1, True), (1, True)):
            bs = self.ex("--join", "b.csv", "--on", "k=id", "--join-prefix", "r_", "--max-state-bytes", budget).data()["explain"]["join"]["build_state"]
            self.assertEqual((bs["budget"], bs["may_pass_bound"]), (budget, want))

    def test_the_upper_bound_holds_for_files_of_every_shape(self):
        # the bound from the size and the header alone is never below what the formula says, also for one-column files with the smallest records (the bound is `2 * (size - header) + 76 * rows`, which is provable: the records and the keys inside them are both counted)
        rng = random.Random(11)
        for ncols in (1, 2, 5):
            for rows in (0, 1, 7, 300):
                for width in (1, 4, 40):
                    recs = [[b"%d" % i + b"x" * (width - 1) if c == 0 else b"y" * rng.randint(0, 3) for c in range(ncols)] for i in range(rows)]
                    recs = [r for r in recs if any(r)] if ncols == 1 else recs
                    J.write_csv(str(self.s.dir / "e.csv"), [b"h%d" % i for i in range(ncols)], recs)
                    x = run("--root", self.s.dir, "--join", "e.csv", "--on", "h0=h0", "--join-prefix", "r_", "--explain", "p2.csv" if False else "p.csv")
                    # p.csv has k,v: key h0 is not there; use the build file as its own probe
                    x = run("--root", self.s.dir, "--join", "e.csv", "--on", "h0=h0", "--join-prefix", "r_", "--explain", "e.csv").data()["explain"]["join"]["build_state"]
                    f = J.accounted(str(self.s.dir / "e.csv"), 0)
                    self.assertGreaterEqual(x["accounted_at_most"], f["held"], (ncols, rows, width))

    def test_it_reads_nothing_after_the_headers_and_names_a_pipe(self):
        r = self.ex("--join", "b.csv", "--on", "k=id", stdin=b"")
        j = r.data()["explain"]["join"]
        self.assertEqual((j["probe"], j["output_columns"], j["keys"][0]["probe_position"]), ("-", None, None))
        self.assertEqual(r.data()["explain"]["output"]["columns"], None)
        self.assertTrue(any("standard input is not read" in n for n in r.data()["explain"]["notes"]))

    def test_it_refuses_what_the_run_refuses_before_a_row(self):
        for argv, rule in ((["--join", "b.csv", "--on", "id=id"], None), (["--join", "b.csv", "--on", "k=nope"], "column.unknown"), (["--join", "nope.csv", "--on", "k=id"], "io.not-found"),
                           (["--join", "b.csv", "--on", "k=id", "--select", "zz"], "select.unknown-column"), (["--join", "b.csv", "--on", "k=id", "--where", "zz = 1"], "column.unknown"),
                           (["--join", "../b.csv", "--on", "k=id"], "path.dotdot"), (["--join", "b.csv"], "args.required-flag")):
            with self.subTest(argv):
                got = self.ex(*argv)
                ran = run("--root", self.s.dir, *argv, "p.csv")
                if rule is None:
                    rule = "join.column-collision" if False else None
                    self.assertEqual((got.first_rule(), got.status), (ran.first_rule(), ran.status))
                    continue
                self.assertEqual((got.first_rule(), got.status), (rule, ran.status))
                self.assertEqual(got.doc()["errors"], ran.doc()["errors"][:len(got.doc()["errors"])])

    def test_the_collision_is_refused_by_explain_with_the_run_error(self):
        self.s.write("c.csv", "k,w\na,2\n")
        got = self.ex("--join", "c.csv", "--on", "k=k")
        ran = run("--root", self.s.dir, "--join", "c.csv", "--on", "k=k", "p.csv")
        self.assertEqual(got.first_rule(), "join.column-collision")
        self.assertEqual(got.error()["detail"], ran.error()["detail"])


class Introspect(unittest.TestCase):
    def test_the_flags_and_limits_and_rules_are_in_introspect(self):
        d = introspect()
        names = {f["name"]: f for f in d["flags"]}
        for flag, kind in (("--join", "path"), ("--on", "text"), ("--join-type", "choice:inner/left/semi/anti"), ("--join-prefix", "any"), ("--max-join-rows", "nat")):
            self.assertEqual(names[flag]["kind"], kind, flag)
        self.assertEqual(names["--join"]["role"], "path-read")
        self.assertEqual(names["--join-type"]["default"], "inner")
        rules = {r["rule"] if isinstance(r, dict) else r for r in d["rules"]}
        for rule in ("join.column-collision", "limit.too-many-join-rows"):
            self.assertIn(rule, rules)
        self.assertIn("join:", d["usage"])

    def test_the_new_rules_have_their_exit_codes(self):
        d = introspect()
        rows = {r["rule"]: r for r in d["rules"] if isinstance(r, dict)}
        if rows:
            self.assertEqual(rows["join.column-collision"]["exit"], 2)
            self.assertEqual(rows["limit.too-many-join-rows"]["exit"], 8)


class Authority(unittest.TestCase):
    """AU: the join only reads files. The authority row is the one of main."""

    def test_the_row_is_unchanged(self):
        d = json.loads(subprocess.run([binary(), "introspect"], capture_output=True, check=True).stdout)
        self.assertEqual(sorted(d["authority"]["effects"]), ["args", "conc", "dir_read", "err_write", "file_read", "fs_read", "heap", "io_read", "io_write"])
        for forbidden in ("file_write", "dir_write", "net_out", "net_in", "exec", "ffi", "clock"):
            self.assertNotIn(forbidden, d["authority"]["effects"])

    def test_manifest_check_passes(self):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check"], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_join_writes_nothing(self):
        s = Scratch()
        try:
            s.write("p.csv", "k\na\n")
            s.write("b.csv", "id\na\n")
            before = sorted(os.listdir(s.dir))
            run("--root", s.dir, "--join", "b.csv", "--on", "k=id", "--format", "csv", "p.csv")
            self.assertEqual(sorted(os.listdir(s.dir)), before)
        finally:
            s.cleanup()


class Fuzz(unittest.TestCase):
    """F: no trap on any plan, every refusal is a rule of the catalogue, every repair that is offered is accepted."""

    GOOD = [["--select", "p0"], ["--select", "b1,p0"], ["--where", "p0 != 'a'"], ["--where", "b0 = k1"], ["--limit", "3"], ["--limit", "2", "--from", "2"], ["--format", "csv"], ["--format", "jsonl"],
            ["--max-rows", "3"], ["--max-rows", "50"], ["--max-state-bytes", "300"], ["--max-state-bytes", "100"], ["--max-state-bytes", "4000"], ["--max-join-rows", "7"], ["--max-join-rows", "40"],
            ["--max-line-bytes", "30"], ["--max-line-bytes", "80"], ["--threads", "4"], ["--rows"], ["--join-prefix", "x_"], ["--join-prefix", "p"], ["--join-prefix", "b"], ["--explain"],
            ["--where", "p0:int > 0"], ["--max-bytes", "60"]]
    JUNK = [["--delimiter", "tab"], ["--group", "p0"], ["--order-by", "b0"], ["--top", "2"], ["--query", "select * from 'p.csv'"], ["--join-type", "left"], ["--report", "types"],
            ["--input-format", "jsonl"], ["--max-join-rows", "0"], ["--select", "nope"], ["--where", "p0 ="], ["--from", "1"]]

    def check(self, argv, r, rules, counts):
        self.assertNotIn(r.status, TRAPS, argv)
        self.assertGreaterEqual(r.status, 0, argv)
        if r.status == 0:
            counts["accepted"] += 1
            return
        counts["refused"] += 1
        try:
            doc = r.doc()
            errors = doc.get("errors") or []
        except (AssertionError, ValueError):
            # csv and jsonl answer on standard error: `table: RULE: message`, after the rows that were written
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
        rng = random.Random(77)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        counts = {"accepted": 0, "refused": 0, "repairs": 0}
        s = Scratch()
        try:
            for i in range(400):
                _, ph, pr, bh, br, pk, bk, opts = J.random_case(rng)
                # the files often share a column name, so that the collision and its repair are reached
                if rng.random() < 0.4:
                    bh = [ph[min(j, len(ph) - 1)] if j == bk else bh[j] for j in range(len(bh))]
                J.write_case(str(s.dir), ph, pr, bh, br, opts)
                if rng.random() < 0.15:
                    # damage a file: a cut in the middle, a ragged row, a bad quote
                    which = rng.choice(["p.csv", "b.csv"])
                    data = (s.dir / which).read_bytes()
                    cut = rng.randrange(len(data) + 1)
                    data = rng.choice([data[:cut], data + b'x,"y"z\n', data + b"1,2,3,4,5,6,7\n", data[:cut] + b'"' + data[cut:]])
                    (s.dir / which).write_bytes(data)
                good = rng.random() < 0.75
                on = "%s=%s" % (ph[pk].decode(), bh[bk].decode()) if good else rng.choice(["p0=b0", "#1=#1", "p0=nope", "k", "%s=%s:int" % (ph[pk].decode(), bh[bk].decode()), "a,b", "=", "\\"])
                join = "b.csv" if good else rng.choice(["b.csv", "p.csv", "nope.csv", "-", "../b.csv", ""])
                pool = self.GOOD if good else self.GOOD + self.JUNK
                extra = sum(rng.sample(pool, rng.randint(0, 3)), [])
                argv = ["--root", str(s.dir), "--join", join, "--on", on] + extra
                probe = rng.choice([["p.csv"], ["p.csv"], []])
                r = run(*argv, *probe, stdin=b"p0\na\n" if not probe else None)
                self.check(argv + probe, r, rules, counts)
        finally:
            s.cleanup()
        self.assertGreater(counts["accepted"], 100, counts)
        self.assertGreater(counts["refused"], 100, counts)
        self.assertGreater(counts["repairs"], 5, counts)


if __name__ == "__main__":
    unittest.main()
