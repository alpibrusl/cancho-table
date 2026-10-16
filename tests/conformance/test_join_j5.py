"""J5 (docs/joins.md sections 3.2, 4, 5, 6, 11 and 21, slice J5): JSON lines on the probe side, on the build side or on both, and a build file with a delimiter of its own.

The flags (docs/joins.md 2.1 names them): `--join-input-format csv|jsonl|ndjson`, `--join-fields PATHS`, `--join-delimiter`. Each defaults to the probe's (`--input-format`, `--fields`,
`--delimiter`). `--absent` and `--max-json-nodes` are one flag for both sides. The record the join holds is the cells joined by the delimiter and quoted as CSV quotes them, whatever
the format of the side, so the accounted bytes of a jsonl build row are those of the csv row with the same cells.

The gates, written before the code and each able to fail (on `main`'s binary, where `--join-input-format`, `--join-fields` and `--join-delimiter` are `args.unknown-flag` and a join
with `--input-format jsonl` is `args.conflict`, every class below fails):

* O, the oracle: generated tables written as csv (with the delimiter of the side) or as JSON lines (escapes, unicode, big integers, 17-digit floats, missing keys, nested paths, blank
  lines, CRLF, a BOM, keys in any order, undeclared keys) are READ AGAIN by Python's csv and json modules, and that is what a Python reference, SQLite and DuckDB (when installed)
  join; the tool is compared on the multiset of rows and on the defined order, for inner, left, semi and anti, csv x jsonl, jsonl x csv and jsonl x jsonl, a build with a delimiter
  of its own, from a file, from standard input and at 4 threads with tiny ranges.
* The plans of J3 (`--join-where`, the split of `--where`, groups, sorts, pages) over jsonl sides, against the J3 reference.
* The chunk edges: records that straddle the 64 KiB blocks of the reader, a record longer than a block, and the refusal of a line longer than `--max-line-bytes`.
* The refusals: a malformed, non-object, missing, nested, duplicated or too-large line of the build or of the probe is refused with its rule, the file named in `detail.path`, its line and
  row; the build first; every repair offered is accepted. The flags: each combination that makes no sense is `args.conflict` or `args.bad-value` or `input.*`, before any read.
* A, the accounting: the program's count equals the formula computed here from the file read again by Python, for a jsonl build of 2^20 and 2^20 + 1 rows and for cells that need quotes,
  unicode, empty one-column records and blank lines; accepted at N, refused at N - 1.
* `--explain` shows both readers; the query form with jsonl sides (the format is a flag, not part of the query); the MCP properties.
* F, fuzz: no trap, every refusal tagged with a rule of the catalogue, every repair offered accepted.
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
import join_ref3 as R3
import join_ref5 as R5
import test_join_j3 as T3
from harness import ROOT, TRAPS, Scratch, binary, introspect, name_text, run, validate

JTYPES = ("inner", "left", "semi", "anti")
COMBOS = (("csv", "jsonl"), ("jsonl", "csv"), ("jsonl", "jsonl"))
BIG = ["--limit", "1000000", "--max-bytes", "67108864"]


def utf8(b):
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def utf8_table(header, rows):
    return all(utf8(x) for x in header) and all(utf8(x) for r in rows for x in r)


def rule_status(rule):
    for r in introspect()["rules"]:
        if r["rule"] == rule:
            return r["exit"]
    raise KeyError(rule)


def side_of(kind, header, rows, opts, which, rng, delim=","):
    """A Side for `kind` with the file options the shape asks for (crlf_p, bom_b, quote_all_p, nofinal_b ...) and, for jsonl, the layout noise a JSON-lines file may have."""
    o = {}
    if opts.get("crlf_" + which):
        o["crlf"] = True
    if opts.get("bom_" + which):
        o["bom"] = True
    if opts.get("nofinal_" + which):
        o["final_newline"] = False
    if kind == "jsonl":
        if rng.random() < 0.5:
            o["blanks"] = True
        if rng.random() < 0.4:
            o["extra_keys"] = True
        if rng.random() < 0.5:
            o["shuffle"] = True
        if rng.random() < 0.3:
            o["spaces"] = True
        return R5.Side("jsonl", header, rows, **o)
    if opts.get("quote_all_" + which):
        o["quote_all"] = True
    return R5.Side("csv", header, rows, delim=delim, **o)


class Case5:
    """Two sides in two formats, written under a scratch root as p.csv|p.jsonl and b.csv|b.jsonl, joined on `pairs` (as join_ref2 has them)."""

    def __init__(self, name, probe, build, pairs, prefix=b"", seed=1, absent=None, extra=()):
        self.name, self.p, self.b, self.pairs, self.prefix, self.absent, self.extra = name, probe, build, pairs, prefix, absent, list(extra)
        self.s = Scratch()
        self.pfile = "p." + ("jsonl" if probe.kind == "jsonl" else "csv")
        self.bfile = "b." + ("jsonl" if build.kind == "jsonl" else "csv")
        rng = random.Random(seed)
        probe.write(str(self.s.dir / self.pfile), rng)
        build.write(str(self.s.dir / self.bfile), rng)
        self.pdelim = probe.delim if probe.kind == "csv" else ","

    def cleanup(self):
        self.s.cleanup()

    def tables(self):
        return (self.p.read(str(self.s.dir / self.pfile), self.absent or "refuse"), self.b.read(str(self.s.dir / self.bfile), self.absent or "refuse"))

    def probe_bytes(self):
        return (self.s.dir / self.pfile).read_bytes()

    def argv(self, jtype, ph, bh, *extra, operand=True):
        a = ["--root", str(self.s.dir), "--join", self.bfile, "--on", R.on_arg(self.pairs, ph, bh), "--join-type", jtype]
        if self.prefix and jtype in ("inner", "left"):
            a += ["--join-prefix", self.prefix.decode()]
        a += self.p.flags(True) + self.b.flags(False)
        if self.b.kind == "csv" and self.b.delim != self.pdelim:
            a += ["--join-delimiter", R5.delim_word(self.b.delim)]
        if self.absent:
            a += ["--absent", self.absent]
        a += self.extra + [str(x) for x in extra]
        return a + ([self.pfile] if operand else [])


def expected(c, jtype):
    (ph, pr, pl), (bh, br, bl) = c.tables()
    try:
        header, rows = R.ref_join(ph, pr, bh, br, c.pairs, jtype, c.prefix, plines=pl, blines=bl)
        return ("ok", header, rows, (ph, pr, bh, br))
    except R.Refusal as r:
        return ("refuse", r, None, (ph, pr, bh, br))


class Oracle(unittest.TestCase):
    """O: the tool against a reference, SQLite and DuckDB, over tables that were READ BACK from the files by Python's csv and json modules."""

    def check(self, c, jtype, sql=True):
        tag = "%s [%s] %s x %s" % (c.name, jtype, c.p.kind, c.b.kind)
        want = expected(c, jtype)
        self.assertEqual(want[0], "ok", tag + ": the case itself is refused by the reference: %r" % (want[1].rule if want[0] != "ok" else ""))
        _, header, rows, (ph, pr, bh, br) = want
        if sql:
            lite, qs = R.sqlite_join(ph, pr, bh, br, c.pairs, jtype)
            self.assertEqual(lite, rows, "%s: the reference and SQLite disagree\n%s" % (tag, qs))
            if utf8_table(ph, pr) and utf8_table(bh, br):
                duck, dq = R.duckdb_join(ph, pr, bh, br, c.pairs, jtype)
                if duck is not None:
                    self.assertEqual(duck, rows, "%s: the reference and DuckDB disagree\n%s" % (tag, dq))
        base = c.argv(jtype, ph, bh)
        r = run(*base, "--format", "csv")
        self.assertEqual(r.status, 0, "%s: %r" % (tag, r.stdout[:200] + r.stderr[:300]))
        self.assertEqual(r.stdout, J.render_rows([header] + rows, c.pdelim), tag + ": the bytes of the csv")
        got = J.read_csv_bytes(r.stdout, c.pdelim)
        self.assertEqual(R.same_multiset(got[1:], rows), True, tag + ": the multiset of rows")
        s = run(*c.argv(jtype, ph, bh, "--format", "csv", operand=False), stdin=c.probe_bytes())
        self.assertEqual((s.status, s.stdout), (0, r.stdout), tag + ": standard input")
        t = run(*base, "--format", "csv", "--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "7")
        self.assertEqual((t.status, t.stdout), (0, r.stdout), tag + ": threads")
        j = run(*base, *BIG)
        self.assertEqual(j.status, 0, tag)
        self.assertEqual(validate(j), [], tag)
        data = j.data()
        self.assertEqual(([name_text(x) for x in data["columns"]], [[name_text(x) for x in row] for row in data["rows"]], data["row_count"], data["truncated"]), (header, rows, len(rows), False), tag)

    def sides(self, shape, kinds, rng):
        name, ph, pr, bh, br, pairs, opts = shape
        return (side_of(kinds[0], ph, pr, opts, "p", rng), side_of(kinds[1], bh, br, opts, "b", rng))

    def test_the_shapes_in_three_formats_for_the_four_types(self):
        for shape in R.shapes():
            name, ph, pr, bh, br, pairs, opts = shape
            if not (utf8_table(ph, pr) and utf8_table(bh, br)):
                continue
            for n, kinds in enumerate(COMBOS):
                for jt in JTYPES:
                    with self.subTest("%s [%s] %s x %s" % (name, jt, *kinds)):
                        rng = random.Random(hash((name, jt, n)) & 0xffff)
                        p, b = self.sides(shape, kinds, rng)
                        c = Case5(name, p, b, pairs, opts.get("prefix", b""), seed=n + 1)
                        try:
                            self.check(c, jt, sql=(n == 0))
                        finally:
                            c.cleanup()

    def test_random_tables_in_random_formats(self):
        rng = random.Random(20261016)
        for i in range(70):
            shape = R.random_shape(rng)
            name, ph, pr, bh, br, pairs, opts = shape
            if not (utf8_table(ph, pr) and utf8_table(bh, br)):
                continue
            kinds = rng.choice(COMBOS + (("csv", "csv"),))
            jt = JTYPES[i % 4]
            bd = rng.choice([",", ";", "\t"]) if kinds[1] == "csv" else ","
            pd = rng.choice([",", ";", "\t"]) if kinds[0] == "csv" else ","
            with self.subTest("%d %s %s x %s [%s]" % (i, name, kinds[0], kinds[1], jt)):
                p, b = side_of(kinds[0], ph, pr, opts, "p", rng, pd), side_of(kinds[1], bh, br, opts, "b", rng, bd)
                c = Case5("random", p, b, pairs, b"d_" if i % 3 == 0 else b"", seed=i)
                try:
                    self.check(c, jt, sql=(i % 2 == 0))
                finally:
                    c.cleanup()

    def test_a_build_with_a_delimiter_of_its_own(self):
        shapes = [s for s in R.shapes() if utf8_table(s[1], s[2]) and utf8_table(s[3], s[4])][:12]
        for shape in shapes:
            name, ph, pr, bh, br, pairs, opts = shape
            for pd, bd in ((",", ";"), (";", ","), (",", "\t"), ("\t", ";"), (";", ";")):
                for jt in JTYPES:
                    with self.subTest("%s [%s] %r %r" % (name, jt, pd, bd)):
                        rng = random.Random(7)
                        c = Case5(name, side_of("csv", ph, pr, opts, "p", rng, pd), side_of("csv", bh, br, opts, "b", rng, bd), pairs, opts.get("prefix", b""))
                        try:
                            self.check(c, jt, sql=(pd, bd) == (",", ";"))
                        finally:
                            c.cleanup()

    def test_cells_that_json_escapes_that_are_not_ascii_and_numbers_written_every_way(self):
        pools = {"escapes": R5.ESCAPES, "unicode": R5.UNICODE, "big integers": R5.BIG_INTS, "17-digit floats": R5.FLOATS17, "look-alikes": R5.LOOKALIKE_JSON}
        for name, pool in pools.items():
            rng = random.Random(len(name))
            ph, bh = J.header("p", 3), J.header("b", 2)
            pr = [[k, b"p%d" % i, b"x"] for i, k in enumerate(pool * 2)]
            br = [[k, b"b%d" % i] for i, k in enumerate(reversed(pool))]
            shape = (name, ph, pr, bh, br, [(0, 0, None, False)], {})
            for n, kinds in enumerate(COMBOS):
                for jt in JTYPES:
                    with self.subTest("%s [%s] %s x %s" % (name, jt, *kinds)):
                        c = Case5(name, side_of(kinds[0], ph, pr, {}, "p", rng), side_of(kinds[1], bh, br, {}, "b", rng), shape[5], seed=n)
                        try:
                            self.check(c, jt, sql=(n == 1))
                        finally:
                            c.cleanup()

    def test_typed_keys_over_cells_that_are_json_numbers(self):
        rng = random.Random(5)
        ints = [b"0", b"-0", b"7", b"007", b"+7", b"9223372036854775807", b"-9223372036854775808", b"12", b"-12", b"1"]
        decs = [b"1.5", b"1.50", b"01.5", b"0.30", b"0.3", b"-0.00", b"0", b"12", b"12.0"]
        floats = [b"0.30000000000000004", b"0.3", b"1E5", b"1e+5", b"100000", b"1.0", b"1", b"-0.0", b"0", b"1.7976931348623157e308", b"0.1", b"1e-1"]
        for kind, pool in (("int", ints), ("dec2", decs), ("float", floats)):
            ph, bh = J.header("p", 2), J.header("b", 2)
            pr = [[k, b"p%d" % i] for i, k in enumerate(pool * 2)]
            br = [[k, b"b%d" % i] for i, k in enumerate(reversed(pool))]
            pairs = [(0, 0, {"int": "int", "dec2": ("dec", 2), "float": "float"}[kind], False)]
            for kinds in COMBOS:
                for jt in JTYPES:
                    with self.subTest("%s [%s] %s x %s" % (kind, jt, *kinds)):
                        c = Case5(kind, side_of(kinds[0], ph, pr, {}, "p", rng), side_of(kinds[1], bh, br, {}, "b", rng), pairs)
                        try:
                            self.check(c, jt, sql=False)
                        finally:
                            c.cleanup()

    def test_declared_paths_into_objects_and_arrays(self):
        rng = random.Random(9)
        ph = [b"id", b"u.name", b"items.0.sku"]
        bh = [b"u.id", b"city", b"tags.1"]
        dom = [b"k%d" % i for i in range(6)]
        pr = [[rng.choice(dom), b"n%d" % i, b"s%d" % (i % 4)] for i in range(40)]
        br = [[rng.choice(dom), b"c%d" % i, b"t%d" % i] for i in range(25)]
        pairs = [(0, 0, None, False)]
        for kinds in (("jsonl", "jsonl"), ("csv", "jsonl"), ("jsonl", "csv")):
            for jt in JTYPES:
                with self.subTest("%s x %s [%s]" % (*kinds, jt)):
                    c = Case5("paths", side_of(kinds[0], ph, pr, {}, "p", rng), side_of(kinds[1], bh, br, {}, "b", rng), pairs)
                    try:
                        self.check(c, jt, sql=False)
                    finally:
                        c.cleanup()

    def test_nested_values_not_declared_are_ignored_and_declared_ones_are_refused(self):
        s = Scratch()
        try:
            s.write("p.csv", "k,v\na,1\nb,2\n")
            s.write("b.jsonl", '{"k":"a","w":{"deep":[1,2,{"x":null}]},"z":"q"}\n{"k":"b","w":[],"z":"r"}\n')
            r = run("--root", s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,z", "--on", "k", "--format", "csv", "p.csv")
            self.assertEqual((r.status, r.stdout), (0, b"k,v,z\na,1,q\nb,2,r\n"))
            r = run("--root", s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--on", "k", "p.csv")
            self.assertEqual((r.status, r.first_rule()), (8, "parse.jsonl-not-scalar"))
            d = r.error()["detail"]
            self.assertEqual((d["path"], d["line"], d["row"], d["column"], d["kind"]), ("b.jsonl", 1, 1, "w", "object"))
        finally:
            s.cleanup()


class Plans(unittest.TestCase):
    """The plans of J3 (`--join-where`, the split of `--where`, groups, sorts, pages) over jsonl sides, against the J3 reference."""

    def test_random_plans_over_the_formats(self):
        rng = random.Random(20261017)
        cases = R3.shapes(seed=17)
        done = {"ok": 0, "refuse": 0}
        for i in range(160):
            case = rng.choice(cases)
            jt = rng.choice(JTYPES)
            plan = R3.random_plan(rng, case, jt)
            kinds = rng.choice(COMBOS)
            ph = [x.encode() for x in case.P[0]]
            bh = [x.encode() for x in case.B[0]]
            pr, br = T3.bytes_rows(case.P[1]), T3.bytes_rows(case.B[1])
            tag = "%d %s [%s] %s x %s %r" % (i, case.name, jt, *kinds, T3.describe(plan))
            with self.subTest(tag):
                if not (utf8_table(ph, pr) and utf8_table(bh, br)):
                    continue
                c = Case5(case.name, side_of(kinds[0], ph, pr, {}, "p", rng), side_of(kinds[1], bh, br, {}, "b", rng), case.pairs, case.prefix.encode() if isinstance(case.prefix, str) else case.prefix, seed=i)
                try:
                    want = T3.expected(plan, case)
                    fmt = rng.choice(["csv", "json"])
                    a = T3.argv_for(c.s.dir, case, plan, fmt)
                    a[a.index("b.csv")] = c.bfile
                    a[-1] = c.pfile
                    fl = c.p.flags(True) + c.b.flags(False)
                    if c.b.kind == "csv" and c.b.delim != c.pdelim:
                        fl += ["--join-delimiter", R5.delim_word(c.b.delim)]
                    r = run(*a[:-1], *fl, a[-1])
                    if want[0] == "ok":
                        done["ok"] += 1
                        self.assertEqual(r.status, 0, tag + " %r" % (r.stdout[:300] + r.stderr[:300]))
                        if fmt == "csv":
                            self.assertEqual(r.stdout, T3.csv_bytes(want[1], want[2]), tag)
                        else:
                            data = r.data()
                            self.assertEqual((data["columns"], data["rows"]), (want[1], want[2]), tag)
                        s = run(*a[:-1], *fl, stdin=c.probe_bytes())
                        self.assertEqual((s.status, s.stdout), (0, r.stdout), tag + ": standard input")
                    else:
                        done["refuse"] += 1
                        info = want[1]
                        self.assertEqual((r.status, r.first_rule()), (8, info["rule"]), tag + " %r" % (r.stdout[:300],))
                finally:
                    c.cleanup()
        self.assertGreater(done["ok"], 60, done)


class ChunkEdges(unittest.TestCase):
    """Records that straddle the 64 KiB blocks of the reader, one longer than a block, and the line ceiling."""

    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def join(self, pkind, bkind, rows, pad, jtype="inner", extra=(), stdin=False):
        header = [b"k", b"fill"]
        brows = [[b"k%d" % (i % 37), ("é".encode() if i % 5 == 0 else b"x") * (pad + (i * 7) % 29)] for i in range(rows)]
        prows = [[b"k%d" % (i % 41), b"p%d" % i] for i in range(40)]
        rng = random.Random(pad)
        bh = [b"bk", b"bfill"]
        c = Case5("edge", side_of(pkind, [b"k", b"v"], prows, {}, "p", rng), side_of(bkind, bh, brows, {}, "b", rng), [(0, 0, None, False)], seed=pad)
        c.s.cleanup()
        c.s = self.s
        c.p.write(str(self.s.dir / c.pfile), rng)
        c.b.write(str(self.s.dir / c.bfile), rng)
        return c

    def test_a_sweep_of_record_lengths_moves_every_boundary_across_a_record(self):
        for pad in range(0, 48, 3):
            for jt in ("inner", "left"):
                with self.subTest(pad=pad, jt=jt):
                    c = self.join("csv", "jsonl", 2600, pad)
                    size = (self.s.dir / c.bfile).stat().st_size
                    self.assertGreater(size, 3 * 65536)
                    want = expected(c, jt)
                    self.assertEqual(want[0], "ok")
                    ph, bh = want[3][0], want[3][2]
                    r = run(*c.argv(jt, ph, bh, "--format", "csv"))
                    self.assertEqual((r.status, r.stdout), (0, J.render_rows([want[1]] + want[2])), pad)

    def test_a_jsonl_probe_through_a_pipe_in_blocks(self):
        for pad in (0, 5, 17):
            c = self.join("jsonl", "jsonl", 2500, pad)
            ph, bh = c.tables()[0][0], c.tables()[1][0]
            want = expected(c, "inner")
            f = run(*c.argv("inner", ph, bh, "--format", "csv"))
            p = run(*c.argv("inner", ph, bh, "--format", "csv", operand=False), stdin=c.probe_bytes())
            self.assertEqual((f.status, p.status, f.stdout == p.stdout, f.stdout == J.render_rows([want[1]] + want[2])), (0, 0, True, True), pad)

    def test_a_record_longer_than_a_block_and_longer_than_the_ceiling(self):
        big = b"y" * 200000
        s = self.s
        J.write_csv(str(s.dir / "p.csv"), [b"k", b"v"], [[b"a", b"1"], [b"b", b"2"]])
        with open(s.dir / "b.jsonl", "wb") as f:
            f.write(b'{"k":"a","w":"' + big + b'"}\n{"k":"b","w":"short"}\n')
        base = ["--root", s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--on", "k", "--max-line-bytes", "1000000"]
        r = run(*base, "--format", "csv", "p.csv")
        self.assertEqual(r.status, 0)
        self.assertEqual(r.stdout, b"k,v,w\n" + b"a,1," + big + b"\nb,2,short\n")
        r = run("--root", s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--on", "k", "--max-line-bytes", "100000", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.line-too-long"))
        d = r.error()["detail"]
        self.assertEqual((d["path"], d["line"]), ("b.jsonl", 1))
        # the same line as the probe
        with open(s.dir / "pj.jsonl", "wb") as f:
            f.write(b'{"k":"a","w":"' + big + b'"}\n')
        s.write("bb.csv", "k,x\na,1\n")
        r = run("--root", s.dir, "--join", "bb.csv", "--on", "k", "--input-format", "jsonl", "--fields", "k,w", "--max-line-bytes", "100000", "pj.jsonl")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.line-too-long"))
        self.assertEqual(r.error()["detail"]["path"], "pj.jsonl")


class Refusals(unittest.TestCase):
    """A bad line of the build or of the probe is refused by the rule of the reader, naming the file, the line and the row; the build first."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v\na,1\nb,2\n")
        self.s.write("pj.jsonl", '{"k":"a","v":"1"}\n{"k":"b","v":"2"}\n')

    def tearDown(self):
        self.s.cleanup()

    def build(self, text, fields="k,w", extra=(), probe="p.csv", pextra=()):
        self.s.write("b.jsonl", text)
        return run("--root", self.s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", fields, "--on", "k", *extra, *pextra, probe)

    def probe(self, text, extra=(), fields="k,v"):
        self.s.write("pj2.jsonl", text)
        self.s.write("bb.csv", "k,w\na,x\nb,y\n")
        return run("--root", self.s.dir, "--join", "bb.csv", "--on", "k", "--input-format", "jsonl", "--fields", fields, *extra, "pj2.jsonl")

    def detail(self, r, rule, path, line, row, column=None):
        self.assertEqual((r.status, r.first_rule()), (rule_status(rule), rule), r.stdout[:400])
        d = r.error()["detail"]
        self.assertEqual((d["path"], d["line"], d["row"]), (path, line, row))
        if column is not None:
            self.assertEqual(d["column"], column)
        return d

    def test_every_refusal_of_the_reader_for_the_build(self):
        good = '{"k":"a","w":"x"}\n'
        d = self.detail(self.build(good + '{"k":"b","w":\n'), "parse.jsonl-syntax", "b.jsonl", 2, 2)
        self.assertIn("offset", d)
        self.detail(self.build(good + "\n\n" + '[1,2]\n'), "parse.jsonl-not-object", "b.jsonl", 4, 2)
        self.detail(self.build(good + '{"k":"b"}\n'), "parse.jsonl-missing-field", "b.jsonl", 2, 2, "w")
        self.detail(self.build(good + '{"k":"b","w":null}\n'), "parse.jsonl-missing-field", "b.jsonl", 2, 2, "w")
        self.detail(self.build(good + '{"k":"b","w":{"a":1}}\n'), "parse.jsonl-not-scalar", "b.jsonl", 2, 2, "w")
        self.detail(self.build(good + '{"k":"b","w":[1]}\n'), "parse.jsonl-not-scalar", "b.jsonl", 2, 2, "w")
        self.detail(self.build(good + '{"k":"b","k":"c","w":"x"}\n'), "parse.jsonl-duplicate-key", "b.jsonl", 2, 2, "k")
        self.detail(self.build(good + '{"k":"b","w":"x","a":1,"b":2,"c":3}\n', extra=["--max-json-nodes", "6"]), "limit.json-too-many-nodes", "b.jsonl", 2, 2)

    def test_every_refusal_of_the_reader_for_the_probe(self):
        good = '{"k":"a","v":"1"}\n'
        self.detail(self.probe(good + '{"k":"b","v"\n'), "parse.jsonl-syntax", "pj2.jsonl", 2, 2)
        self.detail(self.probe(good + '"just a string"\n'), "parse.jsonl-not-object", "pj2.jsonl", 2, 2)
        self.detail(self.probe(good + '{"k":"b"}\n'), "parse.jsonl-missing-field", "pj2.jsonl", 2, 2, "v")
        self.detail(self.probe(good + '{"k":"b","v":{}}\n'), "parse.jsonl-not-scalar", "pj2.jsonl", 2, 2, "v")
        self.detail(self.probe(good + '{"k":"b","v":"1","v":"2"}\n'), "parse.jsonl-duplicate-key", "pj2.jsonl", 2, 2, "v")
        # row and line are the probe's own, not the number of joined rows: three probe rows join nothing, then a bad one
        self.detail(self.probe('{"k":"z","v":"1"}\n{"k":"y","v":"1"}\n\n{"k":"x","v":"1"}\n{"k":"b",\n'), "parse.jsonl-syntax", "pj2.jsonl", 5, 4)
        # the probe from standard input names the input as -
        self.s.write("bb.csv", "k,w\na,x\n")
        r = run("--root", self.s.dir, "--join", "bb.csv", "--on", "k", "--input-format", "jsonl", "--fields", "k,v", stdin=b'{"k":"a","v":"1"}\n{"k":\n')
        self.detail(r, "parse.jsonl-syntax", "-", 2, 2)

    def test_a_document_that_is_not_json_lines_is_said_so_on_its_first_line_only(self):
        r = self.build("[\n  {\"k\":\"a\"}\n]\n")
        self.assertEqual(r.first_rule(), "parse.jsonl-syntax")
        self.assertEqual(r.error()["detail"].get("looks_like"), "json-document")
        r = self.build('{"k":"a","w":"x"}\n[\n')
        self.assertEqual(r.first_rule(), "parse.jsonl-syntax")
        self.assertNotIn("looks_like", r.error()["detail"])
        r = self.probe('{"k":"a","v":"1"}\n[\n')
        self.assertNotIn("looks_like", r.error()["detail"])

    def test_the_build_is_read_before_the_probe(self):
        self.s.write("pbad.jsonl", '{"k":"a",\n')
        self.s.write("b.jsonl", '{"k":"a","w":"x"}\n{"k":\n')
        r = run("--root", self.s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--on", "k", "--input-format", "jsonl", "--fields", "k", "pbad.jsonl")
        self.detail(r, "parse.jsonl-syntax", "b.jsonl", 2, 2)

    def test_absent_empty_is_the_repair_of_a_missing_field_and_it_is_accepted(self):
        r = self.build('{"k":"a","w":"x"}\n{"k":"b"}\n')
        e = r.error()
        self.assertEqual(e["repair"]["kind"], "retry")
        self.assertEqual(e["repair"]["argv"][-2:], ["--absent", "empty"])
        again = subprocess.run([binary()] + e["repair"]["argv"][1:], capture_output=True, input=b"", timeout=60)
        self.assertEqual(again.returncode, 0, again.stdout[:300])
        r2 = self.build('{"k":"a","w":"x"}\n{"k":"b"}\n', extra=["--absent", "empty", "--format", "csv"])
        self.assertEqual((r2.status, r2.stdout), (0, b"k,v,w\na,1,x\nb,2,\n"))
        # one flag for both sides
        r3 = self.build('{"k":"a","w":"x"}\n{"k":"b","w":null}\n', extra=["--absent", "empty", "--format", "csv"], probe="pj.jsonl", pextra=["--input-format", "jsonl", "--fields", "k,v"])
        self.assertEqual((r3.status, r3.stdout), (0, b"k,v,w\na,1,x\nb,2,\n"))

    def test_too_many_rows_and_the_ceiling_of_nodes(self):
        r = self.build("".join('{"k":"a%d","w":"x"}\n' % i for i in range(10)), extra=["--max-rows", "5"])
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-rows"))
        self.assertEqual(r.error()["detail"]["path"], "b.jsonl")
        r = self.build('{"k":"a","w":"x"}\n', extra=["--max-json-nodes", "2"])
        self.assertEqual(r.first_rule(), "limit.json-too-many-nodes")

    def test_typed_keys_over_jsonl_cells_and_the_padded_refusal(self):
        self.s.write("b.jsonl", '{"k":1,"w":"x"}\n{"k":1.5,"w":"y"}\n')
        r = run("--root", self.s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--on", "k:int", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        d = r.error()["detail"]
        self.assertEqual((d["input"], d["context"], d["row"], d["line"], d["column"], d["value"]), ("build", "join", 2, 2, "k", "1.5"))
        # a padded cell of a left join with a jsonl build, read as a number
        self.s.write("b.jsonl", '{"k":"a","n":5}\n')
        r = run("--root", self.s.dir, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,n", "--on", "k", "--join-type", "left", "--where", "n:int > 1", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        self.assertTrue(r.error()["detail"].get("padded"))


class Flags(unittest.TestCase):
    """The three flags, what each defaults to, and every combination that makes no sense, before any read."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v\na,1\nb,2\n")
        self.s.write("pj.jsonl", '{"k":"a","v":"1"}\n{"k":"b","v":"2"}\n')
        self.s.write("b.csv", "k,w\na,x\nb,y\n")
        self.s.write("bs.csv", "k;w\na;x\nb;y\n")
        self.s.write("b.jsonl", '{"k":"a","w":"x"}\n{"k":"b","w":"y"}\n')

    def tearDown(self):
        self.s.cleanup()

    def go(self, *a):
        return run("--root", self.s.dir, "--on", "k", "--format", "csv", *a)

    def test_a_jsonl_probe_and_a_csv_build(self):
        r = self.go("--join", "b.csv", "--join-input-format", "csv", "--input-format", "jsonl", "--fields", "k,v", "pj.jsonl")
        self.assertEqual((r.status, r.stdout), (0, b"k,v,w\na,1,x\nb,2,y\n"))

    def test_the_build_defaults_to_the_probes_format_and_fields(self):
        r = self.go("--join", "pj.jsonl", "--join-prefix", "r_", "--input-format", "jsonl", "--fields", "k,v", "pj.jsonl")
        self.assertEqual((r.status, r.stdout), (0, b"k,v,r_k,r_v\na,1,a,1\nb,2,b,2\n"))
        # the probe is jsonl and the build is a csv file read as jsonl: a refusal that names the build
        r = self.go("--join", "b.csv", "--input-format", "jsonl", "--fields", "k,v", "pj.jsonl")
        self.assertEqual((r.status, r.first_rule()), (8, "parse.jsonl-syntax"))
        self.assertEqual(r.error()["detail"]["path"], "b.csv")

    def test_ndjson_is_jsonl(self):
        r = self.go("--join", "b.jsonl", "--join-input-format", "ndjson", "--join-fields", "k,w", "p.csv")
        self.assertEqual((r.status, r.stdout), (0, b"k,v,w\na,1,x\nb,2,y\n"))

    def test_a_delimiter_for_the_build(self):
        r = self.go("--join", "bs.csv", "--join-delimiter", ";", "p.csv")
        self.assertEqual((r.status, r.stdout), (0, b"k,v,w\na,1,x\nb,2,y\n"))
        r = self.go("--join", "bs.csv", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (3, "column.unknown"))
        self.assertEqual(r.error()["detail"]["input"], "build")
        # the output keeps the probe's delimiter, and a build cell that holds it is quoted
        self.s.write("bq.csv", 'k;w\na;"x,y"\n')
        r = self.go("--join", "bq.csv", "--join-delimiter", ";", "p.csv")
        self.assertEqual((r.status, r.stdout), (0, b'k,v,w\na,1,"x,y"\n'))
        r = self.go("--join", "bs.csv", "--join-delimiter", "tab", "p.csv")
        self.assertEqual(r.first_rule(), "column.unknown")

    def test_the_default_delimiter_of_the_build_is_the_probes(self):
        self.s.write("ps.csv", "k;v\na;1\nb;2\n")
        r = self.go("--join", "bs.csv", "--delimiter", ";", "ps.csv")
        self.assertEqual((r.status, r.stdout), (0, b"k;v;w\na;1;x\nb;2;y\n"))
        r = self.go("--join", "b.csv", "--join-delimiter", ",", "--delimiter", ";", "ps.csv")
        self.assertEqual((r.status, r.stdout), (0, b"k;v;w\na;1;x\nb;2;y\n"))

    def test_each_flag_alone_is_refused_when_it_names_nothing(self):
        cases = [
            (["--join", "b.csv", "--join-fields", "k,w", "p.csv"], "args.conflict"),
            (["--join", "b.csv", "--join-input-format", "csv", "--join-fields", "k,w", "p.csv"], "args.conflict"),
            (["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--join-delimiter", ";", "p.csv"], "args.conflict"),
            (["--join", "b.jsonl", "--join-input-format", "jsonl", "p.csv"], "input.fields-required"),
            (["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,,w", "p.csv"], "input.bad-fields"),
            (["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,k", "p.csv"], "input.bad-fields"),
            (["--join", "b.csv", "--join-input-format", "xml", "p.csv"], "args.bad-value"),
            (["--join", "b.csv", "--join-delimiter", "x", "p.csv"], "args.bad-value"),
            (["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--absent", "nope", "p.csv"], "args.bad-value"),
            (["--join", "b.csv", "--absent", "empty", "p.csv"], "args.conflict"),
            (["--join", "b.csv", "--max-json-nodes", "9", "p.csv"], "args.conflict"),
            (["--join", "b.csv", "--fields", "k,v", "p.csv"], "args.conflict"),
            (["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--fields", "k,v", "p.csv"], "args.conflict"),
            (["--join", "b.csv", "--input-format", "jsonl", "--join-input-format", "csv", "pj.jsonl"], "input.fields-required"),
            (["--join", "b.csv", "--input-format", "jsonl", "--discover", "pj.jsonl"], "args.conflict"),
            (["--join", "b.csv", "--join-input-format", "jsonl", "--join-fields", "k,w", "--discover", "p.csv"], "args.conflict"),
            (["--join-fields", "k,w", "p.csv"], "args.required-flag"),
            (["--join-input-format", "jsonl", "p.csv"], "args.required-flag"),
            (["--join-delimiter", ";", "p.csv"], "args.required-flag"),
        ]
        for argv, rule in cases:
            with self.subTest(argv=argv):
                r = run("--root", self.s.dir, *(["--on", "k"] if "--join" in argv else []), *argv)
                self.assertEqual(r.first_rule(), rule, r.stdout[:400])
                self.assertEqual(r.status, rule_status(rule))
                self.assertEqual(validate(r), [])

    def test_the_fields_of_a_jsonl_probe_are_required_and_the_repair_does_not_offer_a_discover_beside_a_join(self):
        r = self.go("--join", "b.csv", "--input-format", "jsonl", "pj.jsonl")
        self.assertEqual(r.first_rule(), "input.fields-required")
        rep = r.error()["repair"]
        self.assertEqual(rep["kind"], "none", "a --discover beside --join would be refused: the join has none to offer")

    def test_join_where_and_the_collision_rule_see_the_declared_names(self):
        r = self.go("--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,v", "p.csv")
        self.assertEqual(r.first_rule(), "join.column-collision")
        self.assertEqual(r.error()["detail"]["columns"], ["k", "v"])
        self.assertEqual(r.error()["repair"]["kind"], "choose")
        again = subprocess.run([binary()] + r.error()["repair"]["options"][0]["argv"][1:], capture_output=True, input=b"", timeout=60)
        self.assertNotEqual(again.returncode, 2, again.stdout[:300])
        r = run("--root", self.s.dir, "--on", "k", "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--join-where", "w = 'y'", "--format", "csv", "p.csv")
        self.assertEqual((r.status, r.stdout), (0, b"k,v,w\nb,2,y\n"))
        r = run("--root", self.s.dir, "--on", "k=u.id", "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "p.csv")
        self.assertEqual((r.status, r.first_rule()), (3, "column.unknown"))
        self.assertEqual(r.error()["detail"]["input"], "build")

    def test_a_stdin_probe_in_jsonl(self):
        r = run("--root", self.s.dir, "--on", "k", "--join", "b.csv", "--input-format", "jsonl", "--fields", "k,v", "--format", "csv", stdin=b'{"v":1,"k":"a"}\n\n{"k":"z","v":2}\r\n{"k":"b","v":"3"}')
        self.assertEqual((r.status, r.stdout), (0, b"k,v,w\na,1,x\nb,3,y\n"))


class Identity(unittest.TestCase):
    """The file, the pipe and the threads give one answer for a jsonl side, down to the refusal."""

    def test_the_same_bytes_from_a_file_a_pipe_and_any_thread_count(self):
        rng = random.Random(3)
        ph, bh = [b"k", b"v"], [b"id", b"w"]
        dom = [b"k%d" % i for i in range(30)]
        pr = [[rng.choice(dom), b"v%d" % i] for i in range(800)]
        br = [[rng.choice(dom), b"w%d" % i] for i in range(120)]
        for jt in JTYPES:
            c = Case5("identity", side_of("jsonl", ph, pr, {}, "p", rng), side_of("jsonl", bh, br, {}, "b", rng), [(0, 0, None, False)])
            try:
                (ph2, _, _), (bh2, _, _) = c.tables()
                base = c.argv(jt, ph2, bh2, "--format", "csv")
                f = run(*base)
                self.assertEqual(f.status, 0)
                self.assertEqual(run(*c.argv(jt, ph2, bh2, "--format", "csv", operand=False), stdin=c.probe_bytes()).stdout, f.stdout)
                for t in (2, 3, 8, 16, 64):
                    for chunk in (1, 7, 64, 1000, 4194304):
                        self.assertEqual(run(*base, "--threads", t, "--chunk-bytes", chunk, "--parallel-min-bytes", 0).stdout, f.stdout, (jt, t, chunk))
            finally:
                c.cleanup()

    def test_the_same_refusal_from_a_file_and_a_pipe(self):
        s = Scratch()
        try:
            s.write("b.csv", "k,w\na,x\n")
            data = b'{"k":"a","v":"1"}\n{"k":"b"}\n'
            s.write("pj.jsonl", data)
            argv = ["--root", s.dir, "--on", "k", "--join", "b.csv", "--input-format", "jsonl", "--fields", "k,v"]
            f = run(*argv, "pj.jsonl")
            p = run(*argv, stdin=data)
            self.assertEqual((f.status, p.status, f.first_rule(), p.first_rule()), (8, 8, "parse.jsonl-missing-field", "parse.jsonl-missing-field"))
            self.assertEqual((f.error()["detail"]["path"], p.error()["detail"]["path"]), ("pj.jsonl", "-"))
            for key in ("line", "row", "column", "reason"):
                self.assertEqual(f.error()["detail"][key], p.error()["detail"][key], key)
        finally:
            s.cleanup()


class Accounting(unittest.TestCase):
    """A: the program's count of the accounted bytes of a jsonl build equals the formula computed here from the file read again by Python (the csv record of the cells, quoted as csv quotes them),
    and the bound is exact: accepted at N, refused at N - 1."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        J.write_csv(str(cls.s.dir / "p.csv"), [b"a", b"b", b"c"], [[b"0", b"0", b"0"]])

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def jsonl(self, name, objects, bom=False, crlf=False, final_newline=True):
        eol = "\r\n" if crlf else "\n"
        data = eol.join(objects).encode("utf-8") + (eol.encode() if final_newline and objects else b"")
        if bom:
            data = b"\xef\xbb\xbf" + data
        (self.s.dir / name).write_bytes(data)

    def exact(self, name, fields, pairs, jtype, threads=(1,), stdin=False):
        path = str(self.s.dir / name)
        data = open(path, "rb").read()
        rows, lines = R5.read_jsonl(data, fields)
        f = R5.accounted_cells(rows, lines, R5.jsonl_positions(data), [p[1] for p in pairs], [p[2] for p in pairs], jtype)
        f["size"] = len(data)
        on = ",".join("%s=%s%s" % ("abc"[min(i, 2)], fields[bk], R.suffix_of(kind)) for i, (pk, bk, kind, _) in enumerate(pairs))
        base = ["--root", self.s.dir, "--join", name, "--join-input-format", "jsonl", "--join-fields", ",".join(fields), "--on", on, "--join-type", jtype] + \
               (["--join-prefix", "r_"] if jtype in ("inner", "left") else []) + ["--where", "a = no-such-key", "--format", "csv"]
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

    def test_the_edges_of_the_growth_of_the_arrays(self):
        rng = random.Random(5)
        for rows in (1 << 20, (1 << 20) + 1):
            name = "c%d.jsonl" % rows
            ids = rng.sample(range(rows * 2), rows)
            self.jsonl(name, ['{"a":%d,"b":"%d.%02d","c":"u%07x"}' % (i % 1000, i // 100, i % 100, i) for i in ids])
            for jtype in ("inner", "semi"):
                pairs = [(0, 0, "int", False), (1, 1, ("dec", 2), False)]
                f = self.exact(name, ["a", "b", "c"], pairs, jtype)
                self.assertEqual((f["rows"], f["distinct"]), (rows, rows))
            os.unlink(self.s.dir / name)

    def test_cells_that_csv_quotes_unicode_numbers_and_bool_words(self):
        objs = []
        for i in range(3000):
            objs.append(json.dumps({"a": "k%d" % (i % 40), "b": ["plain", "a,b", 'say "hi"', "two\nlines", "é日本😀", "tab\t", "back\\slash", "cr\rcr", ""][i % 9], "c": i}, ensure_ascii=(i % 2 == 0)))
        objs.append('{"a":"k1","b":true,"c":1.50}')
        objs.append('{"c":123456789012345678901234567890,"b":false,"a":-0}')
        self.jsonl("q.jsonl", objs)
        for jtype in JTYPES:
            f = self.exact("q.jsonl", ["a", "b", "c"], [(0, 0, None, False)], jtype, threads=(1, 2, 6, 16))
            self.assertEqual(f["distinct"], 41)
        self.exact("q.jsonl", ["a", "b", "c"], [(0, 0, None, False)], "inner", stdin=True)
        self.exact("q.jsonl", ["a", "b", "c"], [(0, 0, None, False)], "anti", stdin=True)

    def test_a_record_of_one_empty_cell_is_two_quotes(self):
        self.jsonl("one.jsonl", ['{"a":""}', '{"a":"x"}', '{"a":""}', '{"a":"y"}'])
        # one-column records: the empty cell is written "" so that it is not a blank line (2 bytes), and the same key twice holds two rows and one key
        for jtype in JTYPES:
            f = self.exact("one.jsonl", ["a"], [(0, 0, None, False)], jtype)
            self.assertEqual((f["rows"], f["distinct"]), (4, 3))

    def test_blank_lines_crlf_a_bom_and_no_final_newline_move_the_line_and_the_bytes_read(self):
        objs = ['{"a":"k%d","b":"v","c":%d}' % (i % 7, i) for i in range(50)]
        objs.insert(10, "")
        objs.insert(20, "   ")
        objs.insert(30, "\t")
        for opts in ({"bom": True}, {"crlf": True}, {"bom": True, "crlf": True, "final_newline": False}, {"final_newline": False}):
            self.jsonl("e.jsonl", objs, **opts)
            for jtype in ("inner", "anti"):
                f = self.exact("e.jsonl", ["a", "b", "c"], [(0, 0, None, False)], jtype)
                self.assertEqual(f["rows"], 50)

    def test_duplicate_keys_hold_many_rows_and_one_key(self):
        self.jsonl("d.jsonl", ['{"a":"dup","b":"%d","c":"%s"}' % (i, "z" * 50) for i in range(5000)])
        for jtype in ("semi", "anti"):
            f = self.exact("d.jsonl", ["a", "b", "c"], [(0, 0, None, False)], jtype)
            self.assertEqual(f["held"], len(b"dup") + 44, "one key, 5000 rows: the rows are not held")
        f = self.exact("d.jsonl", ["a", "b", "c"], [(0, 0, None, False)], "inner", threads=(1, 2, 6, 16))
        self.assertGreater(f["held"], 5000 * 50)

    def test_the_account_of_a_jsonl_row_is_the_csv_rows(self):
        # the same cells as a csv file and as a jsonl file: the same accounted bytes, to the byte
        rng = random.Random(8)
        rows = [[rng.choice([b"k1", b"k2", b"a,b", b'q"q', b"x\ny", b"", "é".encode()]), b"v%d" % i, rng.choice([b"1", b"22", b"3.5", b"true"])] for i in range(500)]
        hdr = [b"a", b"b", b"c"]
        J.write_csv(str(self.s.dir / "same.csv"), hdr, rows)
        R5.write_jsonl(str(self.s.dir / "same.jsonl"), hdr, rows, random.Random(1))
        for jtype in JTYPES:
            held = {}
            for fmt, name in (("csv", "same.csv"), ("jsonl", "same.jsonl")):
                fl = ["--join-input-format", "jsonl", "--join-fields", "a,b,c"] if fmt == "jsonl" else []
                argv = ["--root", self.s.dir, "--join", name, *fl, "--on", "a=a", "--join-type", jtype] + (["--join-prefix", "r_"] if jtype in ("inner", "left") else []) + ["--where", "a = no-such-key", "--format", "csv", "--max-state-bytes", "1", "p.csv"]
                d = run(*argv).error()["detail"]
                held[fmt] = (d["held"], d["limit"])
                # the bound that fits: the count it reached is what the next run needs
            lo, hi = 1, 1 << 24
            for fmt, name in (("csv", "same.csv"), ("jsonl", "same.jsonl")):
                fl = ["--join-input-format", "jsonl", "--join-fields", "a,b,c"] if fmt == "jsonl" else []
                base = ["--root", self.s.dir, "--join", name, *fl, "--on", "a=a", "--join-type", jtype] + (["--join-prefix", "r_"] if jtype in ("inner", "left") else []) + ["--where", "a = no-such-key", "--format", "csv", "p.csv"]
                a, b = lo, hi
                while a < b:
                    m = (a + b) // 2
                    if run(*base[:-1], "--max-state-bytes", m, base[-1]).status == 0:
                        b = m
                    else:
                        a = m + 1
                held[fmt] = a
            self.assertEqual(held["csv"], held["jsonl"], jtype)


class Explain(unittest.TestCase):
    """`--explain` names both readers; a csv x csv join's document has the bytes it had."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "k,v\na,1\n")
        self.s.write("pj.jsonl", '{"k":"a","v":"1"}\n')
        self.s.write("b.csv", "k,w\na,x\n")
        self.s.write("bs.csv", "k;w\na;x\n")
        self.s.write("b.jsonl", '{"k":"a","w":"x"}\n')

    def tearDown(self):
        self.s.cleanup()

    def ex(self, *a, stdin=None):
        return run("--root", self.s.dir, "--explain", "--on", "k", *a, stdin=stdin)

    def test_a_csv_join_has_no_build_reader_member(self):
        r = self.ex("--join", "b.csv", "p.csv")
        self.assertEqual(r.status, 0)
        self.assertNotIn(b"build_reader", r.stdout)

    def test_a_jsonl_build(self):
        r = self.ex("--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "p.csv")
        self.assertEqual(r.status, 0, r.stdout[:300])
        j = r.data()["explain"]["join"]
        self.assertEqual(j["build_reader"], {"format": "jsonl", "fields": ["k", "w"], "absent": "refuse", "max_json_nodes": 4096})
        self.assertEqual(j["build_state"]["header_bytes"], 0)
        self.assertEqual(r.data()["explain"]["source"]["format"], "csv")
        self.assertEqual(validate(r), [])

    def test_a_jsonl_probe_and_a_jsonl_build(self):
        r = self.ex("--join", "b.jsonl", "--input-format", "jsonl", "--fields", "k,v", "--absent", "empty", "--join-fields", "k,w", "pj.jsonl")
        self.assertEqual(r.status, 0, r.stdout[:300])
        e = r.data()["explain"]
        self.assertEqual((e["source"]["format"], e["source"]["fields"], e["source"]["absent"]), ("jsonl", ["k", "v"], "empty"))
        self.assertEqual(e["join"]["build_reader"], {"format": "jsonl", "fields": ["k", "w"], "absent": "empty", "max_json_nodes": 4096})
        self.assertEqual(e["join"]["output_columns"], 3)

    def test_a_pipe_in_jsonl_has_its_columns(self):
        r = self.ex("--join", "b.jsonl", "--input-format", "jsonl", "--fields", "k,v", "--join-fields", "k,w", stdin=b"")
        self.assertEqual(r.status, 0, r.stdout[:300])
        j = r.data()["explain"]["join"]
        self.assertEqual(j["output_columns"], 3)
        self.assertEqual(j["probe"], "-")

    def test_a_build_delimiter(self):
        r = self.ex("--join", "bs.csv", "--join-delimiter", ";", "p.csv")
        self.assertEqual(r.data()["explain"]["join"]["build_reader"], {"format": "csv", "delimiter": ";"})
        r = self.ex("--join", "bs.csv", "--join-delimiter", ";", "--delimiter", ";", "--select", "k", "p.csv")
        self.assertNotIn(b"build_reader", r.stdout)

    def test_the_refusals_before_a_row_are_the_runs(self):
        for argv in (["--join", "b.jsonl", "--join-input-format", "jsonl", "p.csv"], ["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,,", "p.csv"],
                     ["--join", "b.csv", "--join-fields", "k", "p.csv"], ["--join", "bs.csv", "p.csv"], ["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,k", "--on", "k=nope", "p.csv"],
                     ["--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,v", "p.csv"]):
            with self.subTest(argv):
                a = run("--root", self.s.dir, "--on", "k", *argv)
                b = run("--root", self.s.dir, "--on", "k", "--explain", *argv)
                self.assertEqual(a.status, b.status)
                self.assertEqual(a.first_rule(), b.first_rule())
                self.assertNotEqual(a.status, 0)

    def test_the_bound_is_an_upper_bound(self):
        for n in (1, 40, 700):
            self.s.write("bn.jsonl", "".join('{"k":"key%d","w":"%s"}\n' % (i, "\u00e9" * (i % 9)) for i in range(n)))
            data = (self.s.dir / "bn.jsonl").read_bytes()
            rows, lines = R5.read_jsonl(data, ["k", "w"])
            for jt in ("inner", "semi"):
                held = R5.accounted_cells(rows, lines, R5.jsonl_positions(data), [0], [None], jt)["held"]
                r = self.ex("--join", "bn.jsonl", "--join-input-format", "jsonl", "--join-fields", "k,w", "--join-type", jt, *(["--join-prefix", "r_"] if jt == "inner" else []), "p.csv")
                self.assertEqual(r.status, 0, r.stdout[:300])
                at = r.data()["explain"]["join"]["build_state"]["accounted_at_most"]
                self.assertGreaterEqual(at, held, (n, jt))


class Query(unittest.TestCase):
    """The join in `--query` with jsonl sides: the format of a file is a flag, not part of the query. The three format flags are allowed beside a join query and the query is read as the flags it stands for."""

    def setUp(self):
        self.s = Scratch()
        self.s.write("p.csv", "id,v\n1,a\n2,b\n3,c\n")
        self.s.write("pj.jsonl", '{"id":1,"v":"a"}\n{"id":2,"v":"b"}\n{"id":3,"v":"c"}\n')
        self.s.write("b.jsonl", '{"id":"1","city":"x"}\n{"id":"2","city":"y"}\n{"id":"2","city":"yy"}\n')
        self.s.write("b.csv", "id,city\n1,x\n2,y\n2,yy\n")

    def tearDown(self):
        self.s.cleanup()

    def test_the_query_and_the_flags_of_a_jsonl_join_give_the_same_bytes(self):
        cases = [
            ("select a.id, b.city from 'p.csv' as a join 'b.jsonl' as b on a.id = b.id", ["--join-input-format", "jsonl", "--join-fields", "id,city"],
             ["--join", "b.jsonl", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id,b.city", "p.csv"]),
            ("select a.id, b.city from 'pj.jsonl' as a left join 'b.csv' as b on a.id = b.id", ["--input-format", "jsonl", "--fields", "id,v", "--join-input-format", "csv"],
             ["--join", "b.csv", "--on", "id=id", "--join-type", "left", "--join-prefix", "b.", "--select", "id,b.city", "pj.jsonl"]),
            ("select a.id from 'pj.jsonl' as a anti join 'b.jsonl' as b on a.id = b.id", ["--input-format", "jsonl", "--fields", "id,v", "--join-fields", "id,city"],
             ["--join", "b.jsonl", "--on", "id=id", "--join-type", "anti", "--select", "id", "pj.jsonl"]),
        ]
        for query, flags, equiv in cases:
            with self.subTest(query):
                q = run("--root", self.s.dir, "--query", query, "--format", "csv", *flags)
                f = run("--root", self.s.dir, *equiv[:-1], *flags, "--format", "csv", equiv[-1])
                self.assertEqual(q.status, 0, q.stderr[:300])
                self.assertEqual((q.status, q.stdout), (f.status, f.stdout), (q.stderr[:300], f.stderr[:300]))

    def test_the_formats_of_the_files_are_flags_beside_a_query(self):
        q = "select a.id, b.city from 'p.csv' as a join 'b.jsonl' as b on a.id = b.id"
        # without the flags a jsonl file is read as csv: its first line is the header and the query's column is not there
        r = run("--root", self.s.dir, "--query", q, "--format", "csv")
        self.assertNotEqual(r.status, 0)
        # the join flags are still refused beside a query, the format flags are not
        r = run("--root", self.s.dir, "--query", q, "--join", "b.jsonl", "--join-input-format", "jsonl", "--join-fields", "id,city")
        self.assertEqual(r.first_rule(), "args.conflict")
        self.assertIn("--join", r.error()["detail"]["flags"])
        self.assertNotIn("--join-fields", r.error()["detail"]["flags"])
        r = run("--root", self.s.dir, "--query", q, "--join-input-format", "jsonl", "--join-fields", "id,city", "--format", "csv")
        self.assertEqual((r.status, r.stdout), (0, b"id,b.city\n1,x\n2,y\n2,yy\n"))

    def test_the_explain_of_the_query_is_the_explain_of_the_flags(self):
        q = "select a.id, b.city from 'p.csv' as a join 'b.jsonl' as b on a.id = b.id"
        a = run("--root", self.s.dir, "--explain", "--query", q, "--join-input-format", "jsonl", "--join-fields", "id,city")
        b = run("--root", self.s.dir, "--explain", "--join", "b.jsonl", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id,b.city", "--join-input-format", "jsonl", "--join-fields", "id,city", "p.csv")
        self.assertEqual((a.status, a.stdout), (b.status, b.stdout))
        self.assertIn(b"build_reader", a.stdout)


class Mcp(unittest.TestCase):
    """The server's properties: the three flags are properties of the tool, and a call with them gives the CLI's bytes."""

    def test_the_properties_and_a_call(self):
        import test_mcp_stdin as MS
        d = MS.root()
        (d / "j5b.jsonl").write_bytes(b'{"id":"1","name":"ann"}\n{"id":"2","name":"bob"}\n{"id":"2","name":"bobby"}\n')
        (d / "j5b.tsv").write_bytes(b"id\tname\n1\tann\n2\tbob\n")
        try:
            plans = [
                {"file": "j5p.csv", "join": "j5b.jsonl", "on": "id", "join-input-format": "jsonl", "join-fields": "id,name", "format": "csv"},
                {"file": "j5p.csv", "join": "j5b.tsv", "on": "id", "join-delimiter": "tab", "format": "csv"},
                {"file": "j5p.csv", "join": "j5b.jsonl", "on": "id", "join-input-format": "jsonl", "format": "csv"},
                {"file": "j5p.csv", "join": "j5b.jsonl", "on": "id", "join-input-format": "jsonl", "join-fields": "id,name", "explain": True},
                {"file": "j5p.csv", "join": "j5b.jsonl", "on": "id", "join-fields": "id,name", "format": "csv"},
            ]
            (d / "j5p.csv").write_bytes(b"id,v\n1,a\n2,b\n3,c\n")
            tools = MS.talk(d, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])[0][0]["result"]["tools"][0]["inputSchema"]["properties"]
            for name in ("join-input-format", "join-fields", "join-delimiter"):
                self.assertIn(name, tools)
            for plan in plans:
                with self.subTest(plan=plan):
                    r = MS.one(plan, directory=d)
                    argv = [binary(), "--root=%s" % d] + MS.flag_argv({k: v for k, v in plan.items() if k != "file"}) + [plan["file"]]
                    p = subprocess.run(argv, capture_output=True, input=b"", timeout=60)
                    self.assertEqual(r["result"]["content"][0]["text"].encode(), p.stdout.decode("utf-8", "replace").encode())
                    self.assertEqual(r["result"]["_meta"]["exit_code"], p.returncode)
        finally:
            for n in ("j5b.jsonl", "j5b.tsv", "j5p.csv"):
                (d / n).unlink(missing_ok=True)


class Fuzz(unittest.TestCase):
    """F: no trap on any plan of the new alphabet, every refusal is a rule of the catalogue, every repair that is offered is accepted."""

    GOOD = [["--select", "p0"], ["--where", "p0 != 'a'"], ["--limit", "3"], ["--format", "csv"], ["--max-rows", "3"], ["--max-state-bytes", "300"], ["--max-state-bytes", "100"], ["--max-join-rows", "7"],
            ["--max-line-bytes", "30"], ["--max-line-bytes", "80"], ["--threads", "4"], ["--rows"], ["--join-prefix", "x_"], ["--explain"], ["--absent", "empty"], ["--absent", "refuse"],
            ["--max-json-nodes", "3"], ["--max-json-nodes", "40"], ["--group", "p0"], ["--order-by", "b1"], ["--join-where", "b1 != 'a'"], ["--format", "jsonl"]]
    JUNK = [["--discover"], ["--join-delimiter", "x"], ["--join-fields", "a,,b"], ["--join-input-format", "xml"], ["--fields", "p0"], ["--join-delimiter", ";"], ["--join-fields", "b0"],
            ["--delimiter", "tab"], ["--join-input-format", "csv"], ["--join-input-format", "jsonl"], ["--input-format", "jsonl"], ["--query", "select * from 'p.csv'"], ["--report", "types"]]

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
                if not any(x["rule"].startswith("args.") for x in errors):
                    self.assertNotIn(b'"rule":"args.', again.stdout, ("a repair is refused by the flags", argv, opt["argv"]))
                    self.assertNotIn(b"table: args.", again.stderr, ("a repair is refused by the flags", argv, opt["argv"]))

    def test_random_plans_over_random_files_in_random_formats(self):
        rng = random.Random(20261016)
        rules = {r["rule"] if isinstance(r, dict) else r for r in introspect()["rules"]}
        counts = {"accepted": 0, "refused": 0, "repairs": 0}
        for i in range(400):
            ph, bh = J.header("p", 3), J.header("b", 3)
            dom = [b"a", b"b", b"c", b"d"]
            pr, br = J.table(rng, 3, rng.choice([0, 3, 12]), 0, dom), J.table(rng, 3, rng.choice([0, 3, 12]), 0, dom)
            kinds = rng.choice(COMBOS + (("csv", "csv"),))
            pd = rng.choice([",", ";"]) if kinds[0] == "csv" else ","
            bd = rng.choice([",", ";", "\t"]) if kinds[1] == "csv" else ","
            c = Case5("fuzz", side_of(kinds[0], ph, pr, {}, "p", rng, pd), side_of(kinds[1], bh, br, {}, "b", rng, bd), [(0, 0, rng.choice([None, None, "int"]), False)], seed=i)
            try:
                if rng.random() < 0.3:
                    which = rng.choice([c.pfile, c.bfile])
                    data = (c.s.dir / which).read_bytes()
                    cut = rng.randrange(len(data) + 1)
                    data = rng.choice([data[:cut], data + b'{"p0":\n', data + b"[1]\n", data + b'x,"y"z\n', data[:cut] + b'"' + data[cut:], data + b'{"p0":"a"}\n', data + b'{"p0":"a","p0":"b","p1":1,"p2":2,"b0":1,"b1":2,"b2":3}\n', data + b"\xff\xfe\n"])
                    (c.s.dir / which).write_bytes(data)
                jt = rng.choice(JTYPES)
                argv = c.argv(jt, ph, bh, *sum(rng.sample(self.GOOD, rng.randint(0, 3)), []), *(sum(rng.sample(self.JUNK, 1), []) if rng.random() < 0.3 else []), operand=False)
                probe = rng.choice([[c.pfile], [c.pfile], []])
                r = run(*argv, *probe, stdin=c.probe_bytes() if not probe else None)
                self.check(argv + probe, r, rules, counts)
            finally:
                c.cleanup()
        self.assertGreater(counts["accepted"], 80, counts)
        self.assertGreater(counts["refused"], 80, counts)
        self.assertGreater(counts["repairs"], 3, counts)


class Authority(unittest.TestCase):
    def test_manifest_check_passes(self):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "manifest.py"), "--check"], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_row_is_unchanged(self):
        d = json.loads(subprocess.run([binary(), "introspect"], capture_output=True, check=True).stdout)
        self.assertEqual(sorted(d["authority"]["effects"]), ["args", "conc", "dir_read", "err_write", "file_read", "fs_read", "heap", "io_read", "io_write"])

    def test_the_three_flags_are_in_the_table_and_the_defaults_are_the_probes(self):
        d = json.loads(subprocess.run([binary(), "introspect"], capture_output=True, check=True).stdout)
        names = {f["name"] for f in d["flags"]}
        self.assertTrue({"join-input-format", "join-fields", "join-delimiter"} <= names, sorted(names))


class Strace(unittest.TestCase):
    """On Linux, a join with a jsonl side starts no thread, opens no socket and opens no file for writing (the control: a threaded --select of the same file does)."""

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "strace is Linux only")
    def test_no_thread_no_socket_no_write(self):
        s = Scratch()
        try:
            rng = random.Random(1)
            ph, bh = J.header("p", 2), J.header("b", 2)
            pr, br = J.table(rng, 2, 400, 0, [b"a", b"b"]), J.table(rng, 2, 40, 0, [b"a", b"b"])
            c = Case5("strace", side_of("jsonl", ph, pr, {}, "p", rng), side_of("jsonl", bh, br, {}, "b", rng), [(0, 0, None, False)])
            try:
                base = [binary()] + c.argv("inner", ph, bh, "--threads", "4", "--chunk-bytes", "7", "--parallel-min-bytes", "0", "--format", "csv")
                p = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3,socket,openat", "-o", str(s.dir / "t.txt"), *base], capture_output=True, input=b"", timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr[:300])
                text = (s.dir / "t.txt").read_text()
                self.assertNotRegex(text, r"clone3?\(.*CLONE_THREAD")
                self.assertNotIn("socket(", text)
                self.assertNotRegex(text, r"openat\(.*O_(WRONLY|RDWR|CREAT)")
            finally:
                c.cleanup()
        finally:
            s.cleanup()


if __name__ == "__main__":
    unittest.main()
