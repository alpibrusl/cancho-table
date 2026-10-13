"""R3 (docs/readers.md sections 7, 8 and 10): `--format jsonl`, one JSON object per output row, streamed like `--format csv`. These gates were written from the
specification before the code, and fail on a tool that does not have the format.

The oracle is jsonl_out_ref.py, independent of the tool: the line is written by an encoder of its own (not `json.dumps`) and read back by a strict parser; the
rows come from Python's `int`, `Fraction`, `float` and csv module over the generated table (the same semantics refimpl.py and numbers_ref.py state), and are
validated against the tool's own `--format csv` of the same plans by `OracleSelfCheck`, which passes on a tool that does not have the new format.

* **OracleSelfCheck** the oracle's cells and header are what `--format csv` writes, for hundreds of generated tables and plans (no `--format jsonl` involved).
* **Encoder** the oracle's own writer and reader, against hand-written bytes (no binary).
* **O1 Differential** generated tables (every control byte, quotes, backslashes, NUL, invalid UTF-8 of every kind, 4-byte characters, big integers, decimals,
  floats, empty cells, long fields, rows across the 64 KiB chunk edge) and plans (select, where, order-by, group and every aggregate kind, typed keys, mean with
  @N, distinct, sort, top, limit/from, rows, `--report types`): every line is parsed strictly, is written the one way, and says what the oracle says, keys
  in order. Also the exact bytes of hand-written golden cases (`Golden`).
* **O2/O3 RoundTrip** csv to jsonl to csv byte for byte (newlines, NUL, quotes, backslashes, commas, tabs, CR, unicode), the exception of invalid UTF-8
  (`{"b64":...}` is `parse.jsonl-not-scalar` on the way back), jsonl to jsonl with scalar fields.
* **O4 Refusals before a row** `output.duplicate-column` and `output.not-representable`, exit 2, nothing on stdout.
* **O5 Refusals after rows** a prefix of whole valid lines, equal to csv's, the same exit status, the same rule.
* **Threads** `--threads` x `--chunk-bytes`, standard input: the same bytes as one thread and a file.
* **Fuzz** random and mutated bytes x random plans: no trap, catalogued rules, valid JSON on every line, all configurations agree and csv agrees.
* **Surface** (in test_jsonl_out_more.py with the MCP gates) `introspect`, `skill`, `--explain`, the bad values and conflicts, the MCP schema and call.

Run: `python3 -W ignore -m unittest tests/conformance/test_jsonl_out.py` from the repository root (test_jsonl_out_more.py is the rest).
`TABLE_JSONL_OUT_ROUNDS=N` multiplies the generated loops (default 1: about two minutes).
"""

import base64
import contextlib
import json
import os
import random
import re
import subprocess
import sys
import traceback
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # so that `python3 -m unittest tests/conformance/test_jsonl_out.py` finds its neighbours

import jsonl_out_ref as R
import numbers_ref as N
from harness import Scratch, TRAPS, introspect, run

ROUNDS = max(1, int(os.environ.get("TABLE_JSONL_OUT_ROUNDS", "1")))
PAR = ["--threads", 3, "--chunk-bytes", 7, "--parallel-min-bytes", 1]
RULE_RE = re.compile(rb"^table: ([a-z0-9]+\.[a-z0-9-]+): [^\n]*\n")


class Failures:
    """Run many cases and report the first few that fail, not the first only."""

    def __init__(self):
        self.items, self.count = [], 0

    @contextlib.contextmanager
    def case(self, label):
        self.count += 1
        try:
            yield
        except OSError as e:
            if e.errno != 7:      # E2BIG: an argument of more than 128 KiB cannot be spelled; the case is not run
                self.items.append("%s: OSError: %s" % (label, e))
            else:
                self.count -= 1
        except Exception as e:  # noqa: BLE001 - a case that raises is a case that fails
            where = "" if isinstance(e, AssertionError) else " at " + " | ".join(traceback.format_exc().splitlines()[-5:-1])
            self.items.append("%s: %s: %s%s" % (label, type(e).__name__, str(e)[:1500], where))

    def check(self, test, what="cases", show=5):
        if self.items:
            test.fail("%d of %d %s failed; the first %d:\n%s" % (len(self.items), self.count, what, min(show, len(self.items)), "\n\n".join(self.items[:show])))


def rule_of(stderr):
    """The rule of the first line of a refusal on standard error (`table: RULE: sentence`), or None."""
    m = RULE_RE.match(stderr)
    return m.group(1).decode() if m else None


def agg_tail(flags):
    """How many of the last columns of a plan's answer are aggregates (numbers): the items of --agg, or the count of a --group alone."""
    flags = [str(f) for f in flags]
    if "--agg" in flags:
        return len(flags[flags.index("--agg") + 1].split(","))       # no item of the plans here holds a comma
    return 1 if "--group" in flags else 0


def exit_of(rule):
    return {r["rule"]: r["exit"] for r in introspect()["rules"]}.get(rule)


def known(result, fmt):
    """A tool that has no `--format jsonl` answers args.bad-value for it: say so, rather than let a comparison fail on the empty rows."""
    if fmt == "jsonl" and result.status == 2 and b'"rule":"args.bad-value"' in result.stdout and b'"flag":"--format"' in result.stdout:
        raise AssertionError("the tool does not know `--format jsonl` (args.bad-value for --format): %r" % result.stdout[:240])
    return result


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def frun(self, data, flags, fmt, name="t.csv", extra=()):
        """A file, read with cwd = the scratch directory and its relative name; `fmt` None for the flags as they are."""
        self.s.write(name, data)
        return known(run(*flags, *(["--format", fmt] if fmt else []), *extra, name, cwd=str(self.s.dir)), fmt)

    def srun(self, data, flags, fmt, operand=True, extra=()):
        return known(run(*flags, *(["--format", fmt] if fmt else []), *extra, *(["-"] if operand else []), stdin=data, cwd=str(self.s.dir)), fmt)


# ---- the oracle against the tool's own csv ----------------------------------------------------------------------------------------------------

def cells_equal(got, want):
    return got == want or (isinstance(want, R.FT) and N.same_text(got, want.x))


def check_csv_against_oracle(test, got, exp, label=""):
    """The csv a tool writes is the oracle's header and cells (floats loosely in a tie), or its refusal."""
    if isinstance(exp, R.Refuse):
        test.assertEqual(got.status, exit_of(exp.rule), (label, exp, got.stderr[:300]))
        test.assertEqual(rule_of(got.stderr), exp.rule, (label, got.stderr[:300]))
        return
    test.assertEqual((got.status, got.stderr), (0, b""), label)
    recs = R.csv_records(got.stdout)
    test.assertTrue(recs, (label, "no header"))
    test.assertEqual([R.raw(c) for c in recs[0]], [R.raw(n) for n in exp.names], label)
    rows = recs[1:]
    if len(exp.names) == 1:
        rows = [r if r else [""] for r in rows]
    test.assertEqual(len(rows), len(exp.rows), (label, rows[:3], exp.rows[:3]))
    for i, (g, w) in enumerate(zip(rows, exp.rows)):
        test.assertEqual(len(g), len(w), (label, i, g, w))
        for a, b in zip(g, w):
            test.assertTrue(cells_equal(a, b), (label, "row", i, "got", a[:100], "expected", b[:100]))


class OracleSelfCheck(Base):
    """The oracle against `--format csv` of the tool (which main has): the same header and the same cells, for generated tables and plans."""

    def test_the_oracle_cells_are_the_cells_of_csv(self):
        rng = random.Random(20261013)
        fails = Failures()
        forms = {}
        for case in range(420 * ROUNDS):
            mode = "small"
            t, p, flags = R.random_case(rng, mode=mode)
            forms[p.form] = forms.get(p.form, 0) + 1
            with fails.case("case %d plan %r flags %r header %r" % (case, p.form, flags, t.header)):
                got = self.frun(t.data, flags, "csv")
                exp = R.evaluate(t.header, t.records, p)
                check_csv_against_oracle(self, got, exp, "case %d" % case)
        fails.check(self, "oracle cases")
        for form in ("rows", "select", "where", "order", "group", "agg"):
            self.assertGreater(forms.get(form, 0), 5, forms)


# ---- helpers shared by the gates ------------------------------------------------------------------------------------------------------------------

def name_problems(names):
    """(some name is not UTF-8, two names are the same bytes): what makes `--format jsonl` refuse before a row."""
    keys = [R.raw(n) for n in names]
    bad = False
    for k in keys:
        try:
            k.decode("utf-8")
        except UnicodeDecodeError:
            bad = True
    return bad, len(set(keys)) != len(keys)


def refused_before_a_row(test, got, bad, dup, label=""):
    allowed = set()
    if bad:
        allowed.add("output.not-representable")
    if dup:
        allowed.add("output.duplicate-column")
    test.assertEqual(got.status, 2, (label, got.stdout[:200], got.stderr[:300]))
    test.assertEqual(got.stdout, b"", label)
    test.assertIn(rule_of(got.stderr), allowed, (label, got.stderr[:300]))


class Gated(Base):
    """Base with the comparison of a run with the oracle and with the tool's own csv."""

    def against_oracle(self, got, exp, names, label=""):
        bad, dup = name_problems(names)
        if bad or dup:
            if isinstance(exp, R.Refuse) and not (exp.rule.startswith("output.")):
                # the data refuses too: either refusal is the answer, the output one before any row
                self.assertIn(rule_of(got.stderr), {exp.rule, "output.not-representable", "output.duplicate-column"}, (label, got.stderr[:300]))
                self.assertEqual(got.stdout, b"", label)
                return
            refused_before_a_row(self, got, bad, dup, label)
            return
        if isinstance(exp, R.Out):
            self.assertEqual((got.status, got.stderr), (0, b""), (label, got.stdout[:200], got.stderr[:300]))
            self.assertIsNone(R.compare_out(got.stdout, exp), label)
            return
        self.assertEqual(rule_of(got.stderr), exp.rule, (label, got.stderr[:300]))
        self.assertEqual(got.status, exit_of(exp.rule), label)
        if exp.out is None:
            self.assertEqual(got.stdout, b"", label)
        else:
            lines = R.split_lines(got.stdout)
            self.assertLessEqual(len(lines), len(exp.out.rows), label)
            self.assertIsNone(R.compare_out(got.stdout, R.Out(exp.out.names, exp.out.rows[:len(lines)], exp.out.numeric)), label)

    def same_as_csv(self, data, flags, tail, jl, label="", name="t.csv", extra=()):
        """`jl` (a run with `--format jsonl` of `flags`) against the run of the same plan with `--format csv`: the same status, the same rule on standard error,
        and on standard output the rows csv wrote, as jsonl (a header csv wrote and no row is nothing)."""
        c = self.frun(data, flags, "csv", name, extra)
        self.assertNotIn(c.status, TRAPS, c)
        try:
            kind, want = R.from_csv_out(c.stdout, tail)
        except (csv_error, AssertionError) as e:
            self.fail("%s: the csv of the plan cannot be read back: %s %r" % (label, e, c.stdout[:200]))
        if kind != "ok":
            refused_before_a_row(self, jl, kind == "not-representable", kind == "duplicate", label)
            self.assertEqual(c.status, 0, (label, "csv of the same plan"))
            return c
        self.assertEqual((jl.status, rule_of(jl.stderr) if jl.status else None), (c.status, rule_of(c.stderr) if c.status else None), (label, jl.stderr[:300], c.stderr[:300]))
        self.assertEqual(jl.stdout, want, (label, jl.stdout[:300], want[:300]))
        return c


import csv as _csv  # noqa: E402

csv_error = _csv.Error


def cases(seed, n, mode="small", rows=None):
    rng = random.Random(seed)
    for k in range(n):
        yield k, R.random_case(rng, rows=rows, mode=mode)


def label_of(k, t, p, flags):
    return "case %d %s flags %r header %r" % (k, p.form, flags, t.header)


# ---- the oracle's own writer and reader (no binary) ----------------------------------------------------------------------------------------------

class Encoder(unittest.TestCase):
    def test_the_bytes_of_a_line(self):
        cases_ = [
            ([b"a"], [b'q"z'], [False], b'{"a":"q\\"z"}\n'),
            ([b"a"], [b"back\\slash"], [False], b'{"a":"back\\\\slash"}\n'),
            ([b"a"], [b"a/b"], [False], b'{"a":"a/b"}\n'),
            ([b"a"], [b"\x08\t\n\x0c\r"], [False], b'{"a":"\\b\\t\\n\\f\\r"}\n'),
            ([b"a"], [bytes(range(0, 8)) + b"\x0b\x0e\x0f" + bytes(range(0x10, 0x20))], [False],
             b'{"a":"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\u000b\\u000e\\u000f\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019\\u001a\\u001b\\u001c\\u001d\\u001e\\u001f"}\n'),
            ([b"a"], [b"\x7f"], [False], b'{"a":"\x7f"}\n'),
            ([b"a"], ["é中😀\u2028\u2029\uffff".encode()], [False], '{"a":"é中😀\u2028\u2029\uffff"}\n'.encode()),
            ([b"a"], [b""], [False], b'{"a":""}\n'),
            ([b"a"], [b"\xff"], [False], b'{"a":{"b64":"/w=="}}\n'),
            ([b"a"], [b"\xc0\xaf"], [False], b'{"a":{"b64":"wK8="}}\n'),
            ([b"a"], [b"\xed\xa0\x80"], [False], b'{"a":{"b64":"7aCA"}}\n'),
            ([b"a"], [b"\xf4\x90\x80\x80"], [False], b'{"a":{"b64":"9JCAgA=="}}\n'),
            ([b"a"], [b"\xe2\x82"], [False], b'{"a":{"b64":"4oI="}}\n'),
            ([b"k", b"count"], [b"x", b"12"], [False, True], b'{"k":"x","count":12}\n'),
            ([b'k"\n'], [b"v"], [False], b'{"k\\"\\n":"v"}\n'),
            ([b"a", b"b"], [b"1", b"2"], [False, False], b'{"a":"1","b":"2"}\n'),
        ]
        for keys, cells, num, want in cases_:
            self.assertEqual(R.enc_line(keys, cells, num), want)

    def test_a_key_that_is_not_utf8_cannot_be_written(self):
        with self.assertRaises(R.NotRepresentable):
            R.enc_line([b"\xff"], [b"x"], [False])

    def test_the_strict_reader(self):
        good = [b'{"a":"x"}', b'{"a":{"b64":"/w=="},"b":1.50,"c":-5,"d":1e22}', b'{}']
        for line in good:
            R.parse_line(line)
        bad = [b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1,"a":2}', b'[1]', b'"s"', b' {"a":1}', b'{"a":1} ', b'{"a":1}x', b'{"a":', b'{"a":"\xff"}', b'{"a":01}']
        for line in bad:
            with self.assertRaises((ValueError, UnicodeError), msg=line):
                R.parse_line(line)

    def test_the_canonical_form(self):
        for line in (b'{"a":"x"}', b'{"a":{"b64":"/w=="},"n":1.50}', b'{"a":"\\u001f\\n\\"\\\\"}', "{\"a\":\"é\u2028\"}".encode()):
            self.assertEqual(R.re_encode(R.parse_line(line)), line + b"\n")
        for line in (b'{"a": "x"}', b'{"a":"x" ,"b":"y"}', b'{"a":"\\u001F"}', b'{"a":"\\/"}', b'{"a":"\\u00e9"}', b'{"a":"\\u007f"}', b'{"a":"x","b":true}', b'{"a":null}'):
            try:
                again = R.re_encode(R.parse_line(line))
            except ValueError:
                continue
            self.assertNotEqual(again, line + b"\n", line)

    def test_read_lines_refuses_what_is_not_one_object_per_line(self):
        R.read_lines(b'{"a":"x"}\n{"a":"y"}\n')
        for bad in (b'{"a":"x"}', b'{"a":"x"}\n\n', b'{"a":"x"}\r\n', b'{"a": "x"}\n', b'{"a":"x"}{"a":"y"}\n', b'{"a":"x"}\n{"a":"x}\n'):
            with self.assertRaises(ValueError, msg=bad):
                R.read_lines(bad)

    def test_the_value_comparison(self):
        self.assertTrue(R.value_matches("é", "\xc3\xa9", False))
        self.assertFalse(R.value_matches("\xc3\xa9", "\xc3\xa9", False))
        self.assertTrue(R.value_matches(R.Pairs([("b64", "/w==")]), "\xff", False))
        self.assertFalse(R.value_matches("\ufffd", "\xff", False))
        self.assertFalse(R.value_matches(R.Num("1"), "1", False))
        self.assertFalse(R.value_matches("1", "1", True))
        self.assertTrue(R.value_matches(R.Num("1.50"), "1.50", True))

    def test_the_oracle_encoder_agrees_with_python_where_python_is_canonical(self):
        # json.dumps(ensure_ascii=False) writes the same escapes but for DEL and the separators; it is a cross-check of the table of escapes, not the oracle
        rng = random.Random(5)
        for _ in range(500):
            b = R.rich_bytes(rng)
            try:
                text = b.decode("utf-8")
            except UnicodeDecodeError:
                continue
            self.assertEqual(R.enc_string(text), json.dumps(text, ensure_ascii=False))

    def test_the_generator_reaches_what_it_claims(self):
        seen = {k: 0 for k in ("b64", "ctl", "nonascii", "number", "float", "zero", "dup", "bad", "empty", "u2028", "typed", "mean", "sortdesc", "top", "limit")}
        forms = set()
        for k, (t, p, flags) in cases(7, 500):
            exp = R.evaluate(t.header, t.records, p)
            forms.add(p.form)
            if isinstance(exp, R.Refuse):
                continue
            bad, dup = name_problems(exp.names)
            seen["bad"] += bad
            seen["dup"] += dup
            seen["zero"] += not exp.rows
            if bad or dup:
                continue
            body = b"".join(R.enc_line([R.raw(n) for n in exp.names], [R.raw(c) for c in r], exp.numeric) for r in exp.rows)
            seen["b64"] += b'{"b64"' in body
            seen["ctl"] += b"\\u00" in body
            seen["nonascii"] += any(c >= 0x80 for c in body)
            seen["u2028"] += "\u2028".encode() in body
            seen["number"] += any(exp.numeric)
            seen["float"] += any(isinstance(c, R.FT) for r in exp.rows for c in r)
            seen["empty"] += b'""' in body
            seen["typed"] += any(g[1] for g in p.group)
            seen["mean"] += any(a[0] == "mean" for a in p.aggs)
            seen["sortdesc"] += bool(p.sort and p.sort[1])
            seen["top"] += bool(p.top)
            seen["limit"] += bool(p.limit)
        for k, v in seen.items():
            if k != "dup":          # a duplicate output name needs a header that collides with an aggregate's label: O4 makes them by hand
                self.assertGreater(v, 5, (k, seen))
        self.assertEqual(forms, {"rows", "select", "where", "order", "group", "agg"})


# ---- O1: the oracle differential -----------------------------------------------------------------------------------------------------------------

class Differential(Gated):
    def run_set(self, label, gen, same_everywhere_every=3):
        fails = Failures()
        for k, (t, p, flags) in gen:
            lab = label + " " + label_of(k, t, p, flags)
            with fails.case(lab):
                exp = R.evaluate(t.header, t.records, p)
                got = self.frun(t.data, flags, "jsonl")
                self.assertNotIn(got.status, TRAPS, got)
                self.against_oracle(got, exp, R.out_names(t.header, p), lab)
                # the tool's own csv is the same rows (exactly, down to a float's digits)
                self.same_as_csv(t.data, flags, R.numeric_tail(p), got, lab)
                if k % same_everywhere_every == 0:
                    for how, other in (("threads", self.frun(t.data, flags, "jsonl", extra=PAR)), ("stdin", self.srun(t.data, flags, "jsonl")), ("bare stdin", self.srun(t.data, flags, "jsonl", False))):
                        self.assertEqual((other.status, other.stdout, other.stderr), (got.status, got.stdout, got.stderr), (lab, how))
        fails.check(self, "generated plans")

    def test_o1_generated_tables_and_plans(self):
        self.run_set("small", cases(1013, 520 * ROUNDS))

    def test_o1_tables_of_a_few_hundred_rows_with_long_fields(self):
        self.run_set("mid", cases(77, 40 * ROUNDS, "big", rows=300), 2)

    def test_o1_tables_that_cross_the_chunk_and_block_edges(self):
        self.run_set("big", cases(8128, 6 * ROUNDS, "big", rows=2500), 1)

    def test_o1_zero_rows_write_nothing_not_even_a_line(self):
        empty = b"a,b\n"
        rows = b"a,b\nx,1\ny,2\n"
        for data, flags in ((empty, ["--rows"]), (empty, ["--select", "a"]), (empty, ["--where", "a = nothing"]), (empty, ["--order-by", "a"]), (empty, ["--group", "a"]),
                            (empty, ["--group", "a", "--agg", "count,sum:b"]), (empty, ["--agg", "count,sum:b"]), (rows, ["--where", "a = nothing"]),
                            (rows, ["--where", "a = nothing", "--select", "b,a"]), (rows, ["--agg", "count,sum:b", "--where", "a = nothing"]), (rows, ["--select", "a", "--from", "5"]),
                            (rows, ["--order-by", "a", "--select", "a", "--from", "5"]), (rows, ["--group", "a", "--from", "5"]), (rows, ["--where", "a = nothing", "--group", "a"])):
            got = self.frun(data, flags, "jsonl")
            self.assertEqual((got.status, got.stdout, got.stderr), (0, b"", b""), flags)
            c = self.frun(data, flags, "csv")
            self.assertEqual(c.status, 0)

    def test_o1_a_row_across_the_64k_edge(self):
        """A cell with a 4-byte character, a quote, a newline, an invalid byte and a control byte begins at offsets around every edge of the reader's blocks (64 KiB)
        and of the chunks of the threads, in a quoted field and a bare one."""
        payload = "😀\"\n\\\x01é\u2028€".encode("utf-8") + b"\xff\xc3" + "😀".encode()
        fails = Failures()
        for edge in (65536, 131072):
            for delta in range(-9, 10):
                head = b"id,t\n1,"
                k = edge + delta - len(head) - 1 - 3
                filler = (b"xyz\xc3\xa9" * (k // 5 + 1))[:k]
                body = b'2,"' + payload.replace(b'"', b'""') + b'"\n3,tail\n'
                data = head + filler + b"\n" + body
                rows = [["id", "t"], ["1", R.B(filler)], ["2", R.B(payload)], ["3", "tail"]]
                exp = R.Out(["id", "t"], rows[1:], [False, False])
                for flags, extra in (([], ()), ([], ("--threads", 3, "--chunk-bytes", 65536, "--parallel-min-bytes", 1)), ([], ("--threads", 7, "--chunk-bytes", 1000, "--parallel-min-bytes", 1)),
                                     ([], ("--threads", 2, "--chunk-bytes", edge // 2 + 1, "--parallel-min-bytes", 1))):
                    with fails.case("edge %d delta %d extra %r" % (edge, delta, extra)):
                        got = self.frun(data, ["--rows", *flags], "jsonl", extra=extra)
                        self.assertEqual((got.status, got.stderr), (0, b""))
                        self.assertIsNone(R.compare_out(got.stdout, exp))
                        got = self.srun(data, ["--rows", *flags], "jsonl", extra=extra)
                        self.assertIsNone(R.compare_out(got.stdout, exp))
        fails.check(self, "edge files")

    def test_o1_a_cell_of_100k_with_everything_in_it(self):
        rng = random.Random(100)
        cell = b"".join(R.rich_bytes(rng) + b"ab" for _ in range(9000))[:100_000]
        data = b"id,t,u\n1,x,y\n" + b'2,"' + cell.replace(b'"', b'""') + b'",z\n3,,\n'
        exp = R.Out(["id", "t", "u"], [["1", "x", "y"], ["2", R.B(cell), "z"], ["3", "", ""]], [False] * 3)
        for flags in (["--rows"], ["--select", "id,t,u"], ["--where", "id:int > 0"], ["--order-by", "id:int"], ["--order-by", "-id:int", "--top", "3"]):
            got = self.frun(data, flags, "jsonl")
            if "--top" in flags:
                e = R.Out(exp.names, list(reversed(exp.rows)), exp.numeric)
            else:
                e = exp
            self.assertEqual((got.status, got.stderr), (0, b""), flags)
            self.assertIsNone(R.compare_out(got.stdout, e), flags)
            self.same_as_csv(data, flags, 0, got, str(flags))

    def test_o1_every_byte_value_as_a_cell(self):
        rows = [[R.B(bytes([b]))] for b in range(256)] + [[R.B(bytes([b]) + b"a")] for b in range(0x80, 0x100)] + [[R.B(b"a" + bytes([b]))] for b in range(0x80, 0x100)]
        data = R.csv_bytes(["c"], [r for r in rows], random.Random(1), 0.0)
        exp = R.Out(["c"], rows, [False])
        for extra in ((), PAR):
            got = self.frun(data, ["--rows"], "jsonl", extra=extra)
            self.assertEqual((got.status, got.stderr), (0, b""), got.stderr[:200])
            self.assertIsNone(R.compare_out(got.stdout, exp))
        got = self.frun(data, ["--select", "c"], "jsonl")
        self.assertIsNone(R.compare_out(got.stdout, exp))

    def test_o1_every_multibyte_boundary_codepoint(self):
        # the first and last scalar of each UTF-8 length, the neighbours that are not (surrogates, past U+10FFFF), and each as the lead of a longer cell
        scalars = [0x7F, 0x80, 0x7FF, 0x800, 0xD7FF, 0xE000, 0xFFFD, 0xFFFE, 0xFFFF, 0x10000, 0x10FFFF, 0x2028, 0x2029, 0x85, 0xA0, 0x1F600]
        cells = [chr(c).encode("utf-8") for c in scalars]
        cells += [b"\xed\xa0\x80", b"\xed\xaf\xbf", b"\xed\xb0\x80", b"\xed\xbf\xbf", b"\xf4\x90\x80\x80", b"\xf7\xbf\xbf\xbf", b"\xc0\x80", b"\xc1\xbf", b"\xe0\x80\x80", b"\xe0\x9f\xbf",
                  b"\xf0\x80\x80\x80", b"\xf0\x8f\xbf\xbf", b"\xf8\x80\x80\x80\x80", b"\xfc\x80\x80\x80\x80\x80", b"\x80", b"\xbf", b"\xc2", b"\xe0\xa0", b"\xf0\x90\x80", b"\xf4\x8f\xbf"]
        cells += [c + b"z" for c in cells] + [b"z" + c for c in cells]
        rows = [[R.B(c)] for c in cells]
        data = R.csv_bytes(["c"], rows, random.Random(2), 0.0)
        got = self.frun(data, ["--rows"], "jsonl")
        self.assertIsNone(R.compare_out(got.stdout, R.Out(["c"], rows, [False])))
        self.assertEqual((got.status, got.stderr), (0, b""))

    def test_o1_report_types_is_strings_and_the_csv_of_the_plan(self):
        fails = Failures()
        for k, (t, p, flags) in cases(5150, 60 * ROUNDS):
            sel = []
            if rng_pick(k):
                sel = ["--select", "#1,#%d" % len(t.header)]
            if len(t.header) < 2 and sel:
                sel = []
            lab = "report %d header %r" % (k, t.header)
            with fails.case(lab):
                hdr = None
                got = self.frun(t.data, ["--report", "types", *sel], "jsonl")
                self.assertNotIn(got.status, TRAPS)
                c = self.frun(t.data, ["--report", "types", *sel], "csv")
                recs = R.csv_records(c.stdout)
                names = recs[0]
                bad, dup = name_problems(names)
                self.assertFalse(bad or dup)
                if c.status == 0:
                    self.assertEqual(got.status, 0, got)
                    lines = R.read_lines(got.stdout)
                    self.assertEqual(len(lines), len(recs) - 1)
                    for pairs, rec in zip(lines, recs[1:]):
                        self.assertEqual([k_.encode() for k_, _ in pairs], [R.raw(n) for n in names])
                        for (key, v), cell in zip(pairs, rec):
                            self.assertTrue(R.value_matches(v, cell, False), (key, v, cell))      # every value, the counts too, is a string
                    self.assertEqual(got.stdout, R.from_csv_out(c.stdout, 0)[1])
                else:
                    self.assertEqual((got.status, rule_of(got.stderr)), (c.status, rule_of(c.stderr)))
        fails.check(self, "reports")


def rng_pick(k):
    return k % 2 == 0


# ---- golden bytes ----------------------------------------------------------------------------------------------------------------------------------

class Golden(Gated):
    """The writing of a line, byte by byte, on hand-written inputs."""

    def rows(self, cells_rows, header=b"a,b,c"):
        data = header + b"\n"
        for cells in cells_rows:
            data += b",".join((b'"' + c.replace(b'"', b'""') + b'"') if any(ch in c for ch in b',"\r\n') else c for c in cells) + b"\n"
        return data

    def test_escapes_and_bytes(self):
        data = self.rows([
            [b'q"z', b"back\\slash", b"/"],
            [b"\x00\x01\x1f", b"\x08\x09\x0a\x0c\x0d", b"\x7f"],
            ["é中文😀".encode(), "\u2028\u2029".encode(), "\uffff".encode()],
            [b"\xff", b"\xc0\xaf", b"\xed\xa0\x80"],
            [b"", b"\x0b\x0e\x1a\x1b\x1c\x1d\x1e\x1f", b"\\u00e9\\n"],
            [b"\xf4\x90\x80\x80", b"\xe2\x82", b"x\xffy"],
        ])
        lines = [b'{"a":"q\\"z","b":"back\\\\slash","c":"/"}\n',
                 b'{"a":"\\u0000\\u0001\\u001f","b":"\\b\\t\\n\\f\\r","c":"\x7f"}\n',
                 '{"a":"é中文😀","b":"\u2028\u2029","c":"\uffff"}\n'.encode(),
                 b'{"a":{"b64":"/w=="},"b":{"b64":"wK8="},"c":{"b64":"7aCA"}}\n',
                 b'{"a":"","b":"\\u000b\\u000e\\u001a\\u001b\\u001c\\u001d\\u001e\\u001f","c":"\\\\u00e9\\\\n"}\n',
                 b'{"a":{"b64":"9JCAgA=="},"b":{"b64":"4oI="},"c":{"b64":"eP95"}}\n']
        keys = [b'q"z', b"\x00\x01\x1f", "é中文😀".encode(), b"\xff", b"", b"\xf4\x90\x80\x80"]
        ascending = [l for _, l in sorted(zip(keys, lines))]
        for flags, want in ((["--rows"], lines), (["--select", "a,b,c"], lines), (["--select", "#1,#2,#3"], lines), (["--where", "a != zzz"], lines), (["--where", "b != zzz"], lines),
                            (["--order-by", "a"], ascending), (["--order-by", "-a,c", "--top", "9"], ascending[::-1]), (["--order-by", "a", "--top", "2", "--from", "1"], ascending[1:2])):
            for how in ("file", "stdin", "threads"):
                got = (self.frun(data, flags, "jsonl") if how == "file" else self.srun(data, flags, "jsonl") if how == "stdin" else self.frun(data, flags, "jsonl", extra=PAR))
                self.assertEqual((got.status, got.stderr, got.stdout), (0, b"", b"".join(want)), (flags, how))

    def test_the_order_of_keys_is_the_order_of_the_columns_asked(self):
        data = b"a,b,c\n1,2,3\n"
        for flags, want in ((["--select", "c,a"], b'{"c":"3","a":"1"}\n'), (["--select", "#3,#1,#2"], b'{"c":"3","a":"1","b":"2"}\n'),
                            (["--select", "b"], b'{"b":"2"}\n'), (["--rows"], b'{"a":"1","b":"2","c":"3"}\n')):
            got = self.frun(data, flags, "jsonl")
            self.assertEqual((got.status, got.stdout), (0, want), flags)

    def test_a_header_is_a_key_with_the_same_escaping(self):
        header = 'q"uote,back\\slash,tab\there,é中,\u2028,x\x01y,'.encode() + b"\xe2\x82\xac"
        data = header + b"\n1,2,3,4,5,6,7\n"
        got = self.frun(data, ["--rows"], "jsonl")
        self.assertEqual(got.stdout, '{"q\\"uote":"1","back\\\\slash":"2","tab\\there":"3","é中":"4","\u2028":"5","x\\u0001y":"6","€":"7"}\n'.encode())

    def test_the_empty_header_name_is_the_empty_key(self):
        got = self.frun(b"a,,c\n1,2,3\n", ["--rows"], "jsonl")
        self.assertEqual((got.status, got.stdout), (0, b'{"a":"1","":"2","c":"3"}\n'))

    def test_aggregates_are_numbers_and_group_keys_are_strings(self):
        data = b"k,n,d,f\na,5,1.50,2.5\na,-3,0.1,0.5\nb,007,10,0\n"
        got = self.frun(data, ["--group", "k", "--agg", "count,sum:n,min:n,max:n,mean:n@2,distinct:d,sum:d:dec(2),min:d:dec(2),max:d:dec(2),mean:d:dec(2),sum:f:float,min:f:float,max:f:float,mean:f:float"], "jsonl")
        want = (b'{"k":"a","count":2,"sum:n":2,"min:n":-3,"max:n":5,"mean:n":1.00,"distinct:d":2,"sum:d":1.60,"min:d":0.10,"max:d":1.50,"mean:d":0.80,"sum:f":3.0,"min:f":0.5,"max:f":2.5,"mean:f":1.5}\n'
                b'{"k":"b","count":1,"sum:n":7,"min:n":7,"max:n":7,"mean:n":7.00,"distinct:d":1,"sum:d":10.00,"min:d":10.00,"max:d":10.00,"mean:d":10.00,"sum:f":0.0,"min:f":0.0,"max:f":0.0,"mean:f":0.0}\n')
        self.assertEqual((got.status, got.stderr, got.stdout), (0, b"", want))
        got = self.frun(data, ["--agg", "count,sum:n"], "jsonl")
        self.assertEqual(got.stdout, b'{"count":3,"sum:n":9}\n')
        got = self.frun(data, ["--agg", "count,sum:n", "--where", "k = zz"], "jsonl")
        self.assertEqual((got.status, got.stdout), (0, b""))
        # csv's cell is the number's token
        c = self.frun(data, ["--group", "k", "--agg", "count,sum:n,mean:n@2"], "csv")
        self.assertEqual(c.stdout, b"k,count,sum:n,mean:n\na,2,2,1.00\nb,1,7,7.00\n")

    def test_typed_group_keys_are_strings(self):
        data = b"k,n,d,f\na,5,1.50,2.5\na,-3,0.1,0.5\nb,007,10,0\nc,7,1.5,2.50\n"
        for flags, want in ((["--group", "d:dec(2)", "--agg", "count"], b'{"d":"0.10","count":1}\n{"d":"1.50","count":2}\n{"d":"10.00","count":1}\n'),
                            (["--group", "n:int"], b'{"n":"-3","count":1}\n{"n":"5","count":1}\n{"n":"7","count":2}\n'),
                            (["--group", "f:float", "--agg", "count"], b'{"f":"0.0","count":1}\n{"f":"0.5","count":1}\n{"f":"2.5","count":2}\n')):
            got = self.frun(data, flags, "jsonl")
            self.assertEqual((got.status, got.stderr, got.stdout), (0, b"", want), flags)

    def test_report_types_values_are_all_strings(self):
        data = b"a,b\n1,x\n2,\n"
        got = self.frun(data, ["--report", "types"], "jsonl")
        self.assertEqual(got.status, 0, got)
        lines = R.read_lines(got.stdout)
        self.assertEqual(len(lines), 2)
        c = self.frun(data, ["--report", "types"], "csv")
        names = R.csv_records(c.stdout)[0]
        for pairs in lines:
            self.assertEqual([k for k, _ in pairs], names)
            for k, v in pairs:
                self.assertIs(type(v), str, (k, v))
        self.assertEqual(dict(lines[0])["name"], "a")
        self.assertEqual(dict(lines[0])["cells"], "2")
        self.assertEqual(dict(lines[1])["empty"], "1")

    def test_no_header_no_envelope_no_page(self):
        data = b"a\n1\n2\n3\n"
        got = self.frun(data, ["--select", "a", "--limit", "2"], "jsonl")
        self.assertEqual(got.stdout, b'{"a":"1"}\n{"a":"2"}\n')
        got = self.frun(data, ["--select", "a", "--from", "1", "--limit", "1"], "jsonl")
        self.assertEqual(got.stdout, b'{"a":"2"}\n')
        got = self.frun(data, ["--order-by", "-a:int", "--top", "2"], "jsonl")
        self.assertEqual(got.stdout, b'{"a":"3"}\n{"a":"2"}\n')
        for text in (got.stdout,):
            self.assertNotIn(b"truncated", text)
            self.assertNotIn(b"next", text)

    def test_a_default_has_no_limit(self):
        n = 3000
        data = b"a\n" + b"".join(b"%d\n" % i for i in range(n))
        got = self.frun(data, ["--select", "a"], "jsonl")
        self.assertEqual(got.stdout.count(b"\n"), n)
        self.assertEqual(got.stdout, b"".join(b'{"a":"%d"}\n' % i for i in range(n)))


# ---- O2, O3: round trips ----------------------------------------------------------------------------------------------------------------------

def utf8_cell(rng):
    """A rich cell that is valid UTF-8 (the round trip through JSON text keeps what is text)."""
    while True:
        b = R.rich_bytes(rng)
        try:
            b.decode("utf-8")
            return R.B(b)
        except UnicodeDecodeError:
            continue


class RoundTrip(Gated):
    FIELDS = "c0,c1,c2,c3"

    def table(self, rng, rows):
        header = ["c0", "c1", "c2", "c3"]
        recs = [[utf8_cell(rng) for _ in header] for _ in range(rows)]
        return header, recs, R.csv_bytes(header, recs, rng)

    def pipe(self, data, flags=("--rows",), fields=None, extra=()):
        """csv -> jsonl (the tool) -> csv (the tool's JSON-lines reader on standard input); the first stage's result and the second's."""
        a = self.frun(data, list(flags), "jsonl", extra=extra)
        b = run("--input-format", "jsonl", "--fields", fields or self.FIELDS, "--rows", "--format", "csv", *extra, "-", stdin=a.stdout, cwd=str(self.s.dir))
        return a, b

    def test_o2_csv_to_jsonl_to_csv_is_the_normal_form(self):
        fails = Failures()
        rng = random.Random(2026)
        for k in range(40 * ROUNDS):
            header, recs, data = self.table(rng, rng.choice([1, 2, 5, 20, 60]))
            with fails.case("table %d" % k):
                normal = self.frun(data, ["--rows"], "csv")
                self.assertEqual(normal.status, 0)
                for flags, fields in ((("--rows",), None), (("--select", "c3,c0"), "c3,c0"), (("--where", "c1 != zzz", "--select", "c2,c1"), "c2,c1")):
                    want = self.frun(data, list(flags), "csv")
                    for extra in ((), PAR):
                        a, b = self.pipe(data, flags, fields, extra)
                        self.assertEqual((a.status, a.stderr), (0, b""), flags)
                        self.assertEqual((b.status, b.stderr), (0, b""), (flags, b.stderr[:300]))
                        # the normal form is csv of the same rows, with the same header as the keys give
                        self.assertEqual(b.stdout, want.stdout, (flags, extra))
        fails.check(self, "round trips")

    def test_o3_a_newline_a_nul_a_quote_a_backslash_survive_csv_to_jsonl_to_csv(self):
        cells = ["line1\nline2", "a\x00b", 'say "hi"', "back\\slash", "\x00", "\n", '"', "\\", "\\n", "\\u0000", "x\r\ny", "tab\tend", "\r", "a,b;c", "\x1f\x7f", "€😀 "]
        header = ["c0", "c1", "c2", "c3"]
        recs = [[cells[(i + j) % len(cells)] for j in range(4)] for i in range(len(cells))]
        recs.append(["", "", "", ""])
        data = R.csv_bytes(header, [[R.L(c) for c in r] for r in recs], random.Random(3), 0.0)
        want = self.frun(data, ["--rows"], "csv")
        a, b = self.pipe(data)
        self.assertEqual((a.status, a.stderr), (0, b""))
        self.assertEqual((b.status, b.stderr), (0, b""), b.stderr[:300])
        self.assertEqual(b.stdout, want.stdout)
        # and the cells that come out of the csv read back are the cells that went in
        back = R.csv_records(b.stdout)
        self.assertEqual(back[1:], [[R.L(c) for c in r] for r in recs])
        # the jsonl in the middle is what it must be, line by line
        exp = R.Out(header, [[R.L(c) for c in r] for r in recs], [False] * 4)
        self.assertIsNone(R.compare_out(a.stdout, exp))
        # also with the reader given the file and not a pipe
        self.s.write("mid.jsonl", a.stdout)
        c = run("--root", self.s.dir, "--input-format", "jsonl", "--fields", self.FIELDS, "--rows", "--format", "csv", "mid.jsonl")
        self.assertEqual(c.stdout, want.stdout)

    def test_o2_a_cell_that_is_not_utf8_comes_back_as_the_object_the_reader_refuses(self):
        for bad in (b"\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"ok\xe2\x82", b"\xf4\x90\x80\x80"):
            header = ["c0", "c1"]
            data = R.csv_bytes(header, [["1", "x"], ["2", R.B(bad)], ["3", "y"]], random.Random(1), 0.0)
            a = self.frun(data, ["--rows"], "jsonl")
            self.assertEqual((a.status, a.stderr), (0, b""))
            lines = a.stdout.split(b"\n")
            self.assertEqual(lines[1], b'{"c0":"2","c1":{"b64":"' + base64.b64encode(bad) + b'"}}')
            b = run("--input-format", "jsonl", "--fields", "c0,c1", "--rows", "--format", "csv", "-", stdin=a.stdout)
            self.assertEqual(b.status, 8, b)
            self.assertEqual(rule_of(b.stderr), "parse.jsonl-not-scalar", b.stderr)
            self.assertEqual(b.stdout, b"c0,c1\n1,x\n")       # the rows before the line that is refused stand
            j = run("--input-format", "jsonl", "--fields", "c0,c1", "--rows", "--format", "json", "-", stdin=a.stdout)
            self.assertEqual(j.first_rule(), "parse.jsonl-not-scalar")
            self.assertEqual(j.error()["detail"]["column"], "c1")
            self.assertEqual(j.error()["detail"]["line"], 2)

    def test_o2_jsonl_to_jsonl_every_value_comes_back_as_its_text(self):
        rng = random.Random(77)
        keys = ["s", "num", "flag", "nul", "txt", "uni"]
        tokens = ["1.50", "1E3", "-0", "12345678901234567890", "0.1e-5", "7", "0", "1e+22", "-1.25e-7", "100000000000000000000000000000.5", "0.0", "2"]
        fails = Failures()
        for k in range(60 * ROUNDS):
            order = rng.sample(keys, rng.randint(1, len(keys)))
            lines, want = [], []
            for _ in range(rng.choice([1, 2, 6, 20])):
                vals, cells = {}, {}
                s = utf8_cell(rng)
                st = R.raw(s).decode("utf-8")
                vals["s"], cells["s"] = json.dumps(st, ensure_ascii=rng.random() < 0.5), s
                tok = rng.choice(tokens)
                vals["num"], cells["num"] = tok, tok
                fl = rng.random() < 0.5
                vals["flag"], cells["flag"] = ("true" if fl else "false"), ("true" if fl else "false")
                vals["nul"], cells["nul"] = "null", ""
                t2 = rng.choice(["", "plain", "a\"b", "\\", "é", "\U0001F600", " ", "\x00", "\x1f", "\x7f"])
                vals["txt"], cells["txt"] = json.dumps(t2, ensure_ascii=rng.random() < 0.5), R.L(t2)
                vals["uni"], cells["uni"] = '"\\ud83d\\ude00 \\u00e9 \\u0000 \\/ \\b\\f\\n\\r\\t \\u001f"', R.L("\U0001F600 é \x00 / \b\f\n\r\t \x1f")
                items = list(vals.items())
                rng.shuffle(items)       # the order of the keys in the line is not the order of the fields
                lines.append("{" + ",".join(json.dumps(a) + ":" + b for a, b in items) + "}")
                want.append([cells[f] for f in order])
            eol = rng.choice(["\n", "\r\n"])
            data = (eol.join(lines) + eol).encode("utf-8")
            flags = ["--input-format", "jsonl", "--fields", ",".join(order), "--absent", "empty", "--rows"]
            exp = R.Out(order, want, [False] * len(order))
            with fails.case("jsonl %d fields %r" % (k, order)):
                got = self.frun(data, flags, "jsonl", "t.jsonl")
                self.assertEqual((got.status, got.stderr), (0, b""), got.stderr[:300])
                self.assertIsNone(R.compare_out(got.stdout, exp))
                for how in (self.srun(data, flags, "jsonl"), self.srun(data, flags, "jsonl", False), self.frun(data, flags, "jsonl", "t.jsonl", PAR)):
                    self.assertEqual((how.status, how.stdout), (0, got.stdout))
        fails.check(self, "jsonl to jsonl")

    def test_o2_a_number_is_a_string_in_the_output_unless_it_is_an_aggregate(self):
        data = b'{"a":1.50,"b":true,"c":-0,"d":"x","e":null}\n'
        got = run("--input-format", "jsonl", "--fields", "a,b,c,d,e", "--absent", "empty", "--rows", "--format", "jsonl", "-", stdin=data)
        self.assertEqual(got.stdout, b'{"a":"1.50","b":"true","c":"-0","d":"x","e":""}\n')
        got = run("--input-format", "jsonl", "--fields", "a,d", "--group", "d", "--agg", "count,sum:a:dec(2)", "--format", "jsonl", "-", stdin=data)
        self.assertEqual(got.stdout, b'{"d":"x","count":1,"sum:a":1.50}\n')

    def test_the_jsonl_reader_against_the_oracle_with_jsonl_out(self):
        import jsonl_ref as J
        rng = random.Random(1234)
        fails = Failures()
        n = 0
        for k in range(160 * ROUNDS):
            case = J.random_case(rng)
            dec = case.oracle()
            flags = case.flags() + ["--rows"]
            with fails.case("jsonl input %d %r" % (k, case)):
                got = self.frun(case.data, flags, "jsonl", "t.jsonl")
                self.assertNotIn(got.status, TRAPS)
                c = self.frun(case.data, flags, "csv", "t.jsonl")
                if dec[0] == "ok":
                    names = [R.L(J.item_parse(i)[1]) for i in case.fields]
                    rows = [[R.L(x) for x in r] for r in dec[2]]
                    exp = R.Out(names, rows, [False] * len(names))
                    self.against_oracle(got, exp, names, "k")
                    n += 1
                bad, dup = name_problems([R.L(J.item_parse(i)[1]) for i in case.fields])
                kind, want = R.from_csv_out(c.stdout, 0)
                if kind == "ok":
                    self.assertEqual((got.status, rule_of(got.stderr) if got.status else None), (c.status, rule_of(c.stderr) if c.status else None))
                    self.assertEqual(got.stdout, want)
                for other in (self.srun(case.data, flags, "jsonl"), self.frun(case.data, flags, "jsonl", "t.jsonl", PAR)):
                    self.assertEqual((other.status, other.stdout), (got.status, got.stdout))
        fails.check(self, "jsonl inputs")
        self.assertGreater(n, 40)


# ---- O4: refused before a row ----------------------------------------------------------------------------------------------------------------

class Refusals(Gated):
    DUPS = [
        (["--select", "a,a"], 0),
        (["--select", "a,#1"], 0),
        (["--select", "#1,#1"], 0),
        (["--select", "b,a,b"], 0),
        (["--select", "a,b,a,b"], 0),
        (["--select", "#2,b"], 0),
        (["--where", "a != zzz", "--select", "x,x"], 0),
        (["--order-by", "a", "--select", "a,a"], 0),
        (["--order-by", "-x:int", "--select", "x,#4"], 0),
        (["--group", "a", "--agg", "count,count"], 2),
        (["--agg", "sum:x,sum:x"], 2),
        (["--agg", "count,count"], 1),
        (["--group", "count", "--agg", "count"], 2),
        (["--group", "a", "--agg", "min:x,min:#4"], 2),
        (["--group", "a,a"], 1),
        (["--group", "a,#1", "--agg", "count"], 2),
    ]

    def data(self, rows=6):
        recs = [[["p", "q", "r"][i % 3], "u%d" % (i % 2), str(i % 4), str(i)] for i in range(rows)]
        return R.csv_bytes(["a", "b", "count", "x"], recs, random.Random(1), 0.0)

    def test_o4_duplicate_output_names_are_refused_before_any_row(self):
        fails = Failures()
        big = self.data(4000)
        for flags, tail in self.DUPS:
            for label, data in (("small", self.data()), ("big", big)):
                for how in ("file", "threads", "stdin", "bare stdin"):
                    with fails.case("%r %s %s" % (flags, label, how)):
                        if how == "file":
                            got = self.frun(data, flags, "jsonl")
                        elif how == "threads":
                            got = self.frun(data, flags, "jsonl", extra=["--threads", 3, "--chunk-bytes", 4096, "--parallel-min-bytes", 1])
                        else:
                            got = self.srun(data, flags, "jsonl", how == "stdin")
                        self.assertEqual(got.status, 2, (got.stdout[:100], got.stderr))
                        self.assertEqual(got.stdout, b"")
                        self.assertEqual(rule_of(got.stderr), "output.duplicate-column", got.stderr)
                        self.assertEqual(got.stderr.count(b"\n"), 1)
        fails.check(self, "duplicate names")

    def test_o4_the_same_plans_are_fine_as_csv(self):
        for flags, tail in self.DUPS:
            c = self.frun(self.data(), flags, "csv")
            self.assertEqual((c.status, c.stderr), (0, b""), flags)
            self.assertTrue(c.stdout.startswith(b"a,a") or c.stdout.count(b"\n") >= 1)

    def test_o4_the_duplicate_in_the_file_header_is_a_duplicate_in_the_output(self):
        data = b"a,a,b\n1,2,3\n4,5,6\n"
        for flags in (["--rows"], ["--select", "#1,#2"], ["--where", "b != zzz"], ["--order-by", "b"]):
            got = self.frun(data, flags, "jsonl")
            self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (2, b"", "output.duplicate-column"), flags)
            c = self.frun(data, flags, "csv")
            self.assertEqual(c.status, 0, flags)
        got = self.frun(data, ["--select", "#1,#3"], "jsonl")
        self.assertEqual((got.status, got.stdout), (0, b'{"a":"1","b":"3"}\n{"a":"4","b":"6"}\n'))
        got = self.frun(data, ["--select", "#2"], "jsonl")
        self.assertEqual((got.status, got.stdout), (0, b'{"a":"2"}\n{"a":"5"}\n'))

    def test_o4_a_name_that_is_not_utf8_is_not_representable(self):
        bads = [b"\xff", b"a\xc3", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xf0\x9f\x98", b"x\x80y", b"\xe2\x82"]
        fails = Failures()
        for bad in bads:
            data = b"ok," + bad + b",n\n1,2,3\n4,5,6\n"
            for flags, success in ((["--select", "#2"], False), (["--select", "ok,#2"], False), (["--rows"], False), (["--where", "ok != zzz"], False), (["--group", "#2"], False),
                                   (["--order-by", "ok"], False), (["--agg", "count,sum:#2"], False), (["--group", "ok", "--agg", "sum:#2"], False), (["--group", "ok", "--agg", "min:#2:dec(0)"], False),
                                   (["--agg", "distinct:#2"], False), (["--select", "ok,n"], True), (["--group", "ok", "--agg", "sum:n"], True), (["--agg", "count", "--where", "#2 != zzz"], True),
                                   (["--select", "n"], True)):
                for how in ("file", "threads", "stdin"):
                    with fails.case("%r %r %s" % (bad, flags, how)):
                        if how == "file":
                            got = self.frun(data, flags, "jsonl")
                        elif how == "threads":
                            got = self.frun(data, flags, "jsonl", extra=PAR)
                        else:
                            got = self.srun(data, flags, "jsonl")
                        c = self.frun(data, flags, "csv")
                        self.assertEqual(c.status, 0, (flags, c.stderr))
                        if success:
                            self.assertEqual((got.status, got.stderr), (0, b""))
                            self.assertEqual(got.stdout, R.from_csv_out(c.stdout, 1 if "--agg" in flags else 0)[1])
                        else:
                            self.assertEqual(got.status, 2)
                            self.assertEqual(got.stdout, b"")
                            self.assertEqual(rule_of(got.stderr), "output.not-representable", got.stderr)
        fails.check(self, "names that are not UTF-8")

    def test_o4_not_representable_is_the_name_and_not_the_cell(self):
        # a cell that is not UTF-8 is a {"b64"} value; the same bytes as a header are a refusal
        data = b"a,b\n\xff,\xc0\xaf\n"
        got = self.frun(data, ["--rows"], "jsonl")
        self.assertEqual((got.status, got.stdout), (0, b'{"a":{"b64":"/w=="},"b":{"b64":"wK8="}}\n'))
        data = b"\xff,b\n1,2\n"
        got = self.frun(data, ["--rows"], "jsonl")
        self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (2, b"", "output.not-representable"))

    def test_o4_the_report_names_its_own_columns(self):
        data = b"ok,\xff\n1,2\n"
        got = self.frun(data, ["--report", "types"], "jsonl")
        self.assertEqual(got.status, 0, got.stderr)
        lines = R.read_lines(got.stdout)
        self.assertEqual(len(lines), 2)
        self.assertEqual(dict(lines[1])["name"], R.Pairs([("b64", "/w==")]))
        c = self.frun(data, ["--report", "types"], "csv")
        self.assertEqual([k for k, _ in lines[0]], R.csv_records(c.stdout)[0])

    def test_o4_the_refusal_is_one_sentence_and_the_exit_is_the_cataloged_one(self):
        for rule in ("output.duplicate-column", "output.not-representable"):
            entry = [r for r in introspect()["rules"] if r["rule"] == rule]
            self.assertEqual(len(entry), 1, rule)
            self.assertEqual((entry[0]["exit"], entry[0]["repairable"]), (2, "never"), rule)
        got = self.frun(self.data(), ["--select", "a,a"], "jsonl")
        self.assertRegex(got.stderr, rb"^table: output\.duplicate-column: [^\n]+\n$")
        self.assertEqual(got.status, 2)
        self.assertIn(2, {c["code"] for c in introspect()["exit_codes"]})
        self.assertIn(b"a", got.stderr)      # it says which

    def test_o4_a_dup_is_refused_before_the_data_is_judged(self):
        # the plan refuses before a row is read; so a bad cell later in the file does not change the answer
        data = b"a,b\n1,2\nx,3\n"
        got = self.frun(data, ["--where", "a:int > 0", "--select", "b,b"], "jsonl")
        self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (2, b"", "output.duplicate-column"))
        # and a plan that would be refused for its arguments is refused for them
        got = self.frun(data, ["--select", "nope,nope"], "jsonl")
        self.assertEqual(got.stdout, b"")
        self.assertNotEqual(got.status, 0)
        self.assertIn(rule_of(got.stderr), ("select.unknown-column", "output.duplicate-column", "column.unknown"))

    def test_o4_nothing_but_the_refusal_when_it_is_the_json_input_reader(self):
        data = b'{"a":"1","b":"2"}\n'
        got = run("--input-format", "jsonl", "--fields", "a,b", "--select", "a,a", "--format", "jsonl", "-", stdin=data)
        self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (2, b"", "output.duplicate-column"))


# ---- O5: refused after rows -------------------------------------------------------------------------------------------------------------------

KEYS7 = [b"a", b"b", b"c", b"d", b"e", b"f", b"g"]


def numbered(n, at=None, line=None, tail=b"", keys=KEYS7):
    """`n` rows of `id,x,k,name` (x = 1..1000) of about 70 bytes; the row `at` replaced by `line`; `tail` appended. -> (bytes, the good rows as [id, x] cells in file order)."""
    out = [b"id,x,k,name\n"]
    good = []
    for i in range(n):
        x = i % 1000 + 1
        if i == at:
            out.append(line)
            continue
        out.append(b"%d,%d,%s,name-%d-padpadpadpadpadpadpadpadpadpadpadpadpad\n" % (i, x, keys[i % len(keys)], i))
        good.append((i, x))
    return b"".join(out) + tail, good


class AfterRows(Gated):
    N = 30000
    CONFIGS = [(3, 65536), (7, 1000), (16, 4096)]

    def cases(self):
        n, at = self.N, 24000
        yield "not an integer", numbered(n, at, b"%d,abc,a,nm\n" % at), ["--where", "x:int > 0", "--select", "id,x"], 0, "value.not-integer", True
        yield "an integer past int64", numbered(n, at, b"%d,9223372036854775808,a,nm\n" % at), ["--where", "x:int > 0", "--select", "id,x"], 0, "value.integer-overflow", True
        yield "a decimal with too many digits", numbered(n, at, b"%d,1.234,a,nm\n" % at), ["--where", "x:dec(2) > 0", "--select", "id,x"], 0, "value.decimal-scale", True
        yield "a float out of range", numbered(n, at, b"%d,1e999,a,nm\n" % at), ["--where", "x:float > 0", "--select", "id,x"], 0, "value.float-range", True
        yield "an empty cell read as an integer", numbered(n, at, b"%d,,a,nm\n" % at), ["--where", "x:int > 0", "--select", "id,x"], 0, "value.not-integer", True
        yield "a ragged row", numbered(n, at, b"%d,5\n" % at), ["--where", "x:int > 0", "--select", "id,x"], 0, "parse.csv-ragged-row", True
        yield "a ragged row (rows)", numbered(n, 20000, b"20000,5,a\n"), ["--rows"], 0, "parse.csv-ragged-row", False
        yield "an unterminated quote at the end", numbered(n, None, b"", b'%d,"abc,a,nm' % n), ["--where", "x:int > 0", "--select", "id,x"], 0, "parse.csv-unterminated-quote", True
        yield "a quote closed by the wrong byte", numbered(n, at, b'%d,"a"b,a,nm\n' % at), ["--where", "x:int > 0", "--select", "id,x"], 0, "parse.csv-bad-quote", True
        yield "a line past --max-line-bytes", numbered(n, at, b'%d,5,a,%s\n' % (at, b"z" * (1 << 21))), ["--where", "x:int > 0", "--select", "id,x"], 0, "limit.line-too-long", True
        yield "a sum read from a bad cell (groups are written at the end)", numbered(n, at, b"%d,abc,a,nm\n" % at), ["--group", "k", "--agg", "count,sum:x"], 2, "value.not-integer", False
        yield "a float sum that overflows", numbered(n, 100, b"100,1.7976931348623157e308,a,nm\n", b"200,1.7976931348623157e308,a,nm\n"), ["--group", "k", "--agg", "sum:x:float"], 1, "agg.float-overflow", False
        yield "a sort with a bad cell", numbered(n, at, b"%d,abc,a,nm\n" % at), ["--order-by", "x:int", "--select", "id,x"], 0, "value.not-integer", False
        yield "a bad cell behind a text condition that is true for it", numbered(n, 24002, b"24002,abc,c,nm\n"), ["--where", "k = c and x:int > 0", "--select", "id,x,k"], 0, "value.not-integer", True

    def expect_prefix(self, good, flags, lines, label):
        """The lines written are the first rows the plan selects of the good rows, in order."""
        if "--where" not in flags:
            return
        cells = []
        for i, x in good:
            if "k = c" in flags[1]:
                if KEYS7[i % 7] != b"c":
                    continue
                cells.append([str(i), str(x), "c"])
            else:
                cells.append([str(i), str(x)])
        names = ["id", "x", "k"] if "k = c" in flags[1] else ["id", "x"]
        k = len(lines)
        self.assertGreater(k, 0, label)
        self.assertLessEqual(k, len(cells), label)
        self.assertIsNone(R.compare_out(b"".join(lines), R.Out(names, cells[:k], [False] * len(names))), label)

    def test_o5_a_prefix_of_whole_lines_the_exit_and_the_rule_of_csv(self):
        fails = Failures()
        for name, (data, good), flags, tail, rule, streaming in self.cases():
            with fails.case(name):
                csvr = self.frun(data, flags, "csv")
                self.assertEqual(rule_of(csvr.stderr), rule, (name, csvr.stderr[:200]))
                got = self.frun(data, flags, "jsonl")
                self.assertEqual(rule_of(got.stderr), rule, (name, got.stderr[:300]))
                self.assertEqual(got.status, csvr.status, name)
                self.assertEqual(got.status, exit_of(rule), name)
                self.assertEqual(got.stderr.count(b"\n"), 1)
                self.assertTrue(got.stderr.startswith(b"table: " + rule.encode() + b": "))
                R.read_lines(got.stdout)            # whole lines, each one valid JSON, written the one way
                self.assertEqual(got.stdout, R.from_csv_out(csvr.stdout, tail)[1], name)
                lines = R.split_lines(got.stdout)
                if streaming:
                    self.expect_prefix(good, flags, [l + b"\n" for l in lines], name)
                else:
                    if name.startswith("a ragged row (rows)"):
                        self.assertEqual(len(lines), len(good))
                    else:
                        self.assertEqual(lines, [], name)       # a group or a sort is written once it is complete
        fails.check(self, "refusals after rows")

    def test_o5_the_same_through_threads_and_standard_input(self):
        fails = Failures()
        for name, (data, good), flags, tail, rule, streaming in self.cases():
            with fails.case(name):
                base = self.frun(data, flags, "jsonl")
                for t, c in self.CONFIGS:
                    got = self.frun(data, flags, "jsonl", extra=["--threads", t, "--chunk-bytes", c, "--parallel-min-bytes", 1])
                    self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr), (name, t, c))
                for operand in (True, False):
                    got = self.srun(data, flags, "jsonl", operand)
                    self.assertEqual((got.status, got.stdout), (base.status, base.stdout), (name, "stdin", operand))
                    self.assertEqual(rule_of(got.stderr), rule_of(base.stderr))
                got = self.srun(data, flags, "jsonl", True, extra=["--threads", 3, "--chunk-bytes", 4096, "--parallel-min-bytes", 1])
                self.assertEqual((got.status, got.stdout), (base.status, base.stdout), (name, "stdin threads"))
        fails.check(self, "refusals in other configurations")

    def test_o5_a_late_refusal_leaves_the_rows_before_it_in_every_configuration(self):
        data, good = numbered(30000, 29000, b"29000,abc,a,nm\n")
        flags = ["--where", "x:int > 500", "--select", "id,x"]
        got = self.frun(data, flags, "jsonl")
        want = [[str(i), str(x)] for i, x in good if x > 500 and i < 29000]
        self.assertEqual(rule_of(got.stderr), "value.not-integer")
        lines = R.split_lines(got.stdout)
        self.assertGreater(len(lines), 10000)
        self.assertIsNone(R.compare_out(got.stdout, R.Out(["id", "x"], want[:len(lines)], [False, False])))
        for extra in (["--threads", 7, "--chunk-bytes", 65536, "--parallel-min-bytes", 1], ["--threads", 64, "--chunk-bytes", 1000, "--parallel-min-bytes", 1]):
            other = self.frun(data, flags, "jsonl", extra=extra)
            self.assertEqual((other.status, other.stdout, other.stderr), (got.status, got.stdout, got.stderr), extra)

    def test_o5_the_first_refusal_in_file_order_whichever_thread_finds_it(self):
        n = 20000
        data, good = numbered(n, 15000, b"15000,abc,a,nm\n")
        lines = data.split(b"\n")
        lines[19000 + 1] = b"19000,zzz,b,nm"         # a later defect: the earlier one is the answer
        data = b"\n".join(lines)
        flags = ["--where", "x:int > 0", "--select", "id"]
        c = self.frun(data, flags, "csv")
        got = self.frun(data, flags, "jsonl")
        self.assertEqual((got.status, rule_of(got.stderr)), (c.status, "value.not-integer"))
        for extra in (["--threads", 16, "--chunk-bytes", 4096, "--parallel-min-bytes", 1], ["--threads", 3, "--chunk-bytes", 65536, "--parallel-min-bytes", 1]):
            other = self.frun(data, flags, "jsonl", extra=extra)
            self.assertEqual((other.status, other.stdout, other.stderr), (got.status, got.stdout, got.stderr), extra)
        self.assertEqual(R.split_lines(got.stdout)[-1], b'{"id":"14999"}')


# ---- threads, chunks, standard input -------------------------------------------------------------------------------------------------------

PLANS = [
    ["--rows"],
    ["--select", "txt,id"],
    ["--select", "#1,#3,#4,#5,#6,#7", "--where", "n:int > 0 and k != c"],
    ["--where", "txt contains a"],
    ["--order-by", "-n:int,id", "--select", "id,n,k"],
    ["--order-by", "d:dec(2),f:float,txt", "--top", "40"],
    ["--group", "k", "--agg", "count,sum:n,min:big,max:big,mean:n@2,distinct:txt,sum:d:dec(2),mean:d:dec(3),min:f:float,max:f:float"],
    ["--group", "d:dec(2),k", "--agg", "count,distinct:n:int", "--sort", "-count", "--top", "9"],
    ["--agg", "count,sum:n,sum:big,distinct:w"],
    ["--report", "types"],
    ["--select", "id,txt,k", "--from", "17", "--limit", "23"],
    ["--where", "id:int >= 5", "--group", "n:int", "--agg", "count", "--sort", "n", "--limit", "5", "--from", "2"],
]


class Threads(Gated):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        rng = random.Random(404)
        cls.small = R.fixed_table(rng, 24)
        cls.mid = R.inflate(rng, R.fixed_table(rng, 260), long_cells=3, huge=False)
        cls.big = R.inflate(rng, R.fixed_table(rng, 2500), long_cells=4, huge=True)

    def baseline(self, t, flags):
        g = self.frun(t.data, flags, "jsonl")
        self.assertNotIn(g.status, TRAPS)
        return g

    def matrix(self, label, t, threads, chunks, plans=PLANS):
        fails = Failures()
        for flags in plans:
            base = self.baseline(t, flags)
            c = self.frun(t.data, flags, "csv")
            with fails.case("%s %r baseline against csv" % (label, flags)):
                self.assertEqual(base.status, c.status, (base.stderr[:200], c.stderr[:200]))
                kind, want = R.from_csv_out(c.stdout, agg_tail(flags))
                self.assertEqual(kind, "ok")
                self.assertEqual(base.stdout, want)
                R.read_lines(base.stdout)
            for t_ in threads:
                for ch in chunks:
                    with fails.case("%s %r threads %d chunk %d" % (label, flags, t_, ch)):
                        got = self.frun(t.data, flags, "jsonl", extra=["--threads", t_, "--chunk-bytes", ch, "--parallel-min-bytes", 1])
                        self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr))
        fails.check(self, label)

    def test_gate3_a_small_table_every_thread_count_and_tiny_chunks(self):
        self.matrix("small", self.small, (1, 2, 3, 7, 16, 64), (1, 7, 64, 1000, 65536))

    def test_gate3_a_table_of_hundreds_of_rows(self):
        self.matrix("mid", self.mid, (2, 3, 7, 16, 64), (7, 64, 1000, 65536))

    def test_gate3_a_table_of_hundreds_of_kilobytes(self):
        self.matrix("big", self.big, (2, 7, 64), (1000, 65536, 4194304), [p for p in PLANS if p[0] != "--report"] + [PLANS[9]])

    def test_gate3_the_default_thresholds(self):
        # the default --parallel-min-bytes (1 MiB) keeps a small file sequential, and --threads without it changes nothing
        for flags in PLANS[:8]:
            base = self.baseline(self.mid, flags)
            got = self.frun(self.mid.data, flags, "jsonl", extra=["--threads", 8])
            self.assertEqual((got.status, got.stdout), (base.status, base.stdout), flags)
            got = self.frun(self.big.data, flags, "jsonl", extra=["--threads", 8, "--chunk-bytes", 65536])
            base = self.baseline(self.big, flags)
            self.assertEqual((got.status, got.stdout), (base.status, base.stdout), flags)

    def test_gate3_standard_input_is_the_file(self):
        fails = Failures()
        for label, t in (("small", self.small), ("mid", self.mid), ("big", self.big)):
            for flags in PLANS:
                if label == "big" and flags[0] in ("--report",):
                    continue
                base = self.baseline(t, flags)
                for how in ("-", "bare", "- threads", "bare threads"):
                    with fails.case("%s %r %s" % (label, flags, how)):
                        extra = ["--threads", 5, "--chunk-bytes", 777, "--parallel-min-bytes", 1] if "threads" in how else []
                        got = self.srun(t.data, flags, "jsonl", how.startswith("-"), extra=extra)
                        self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr))
        fails.check(self, "standard input")

    def test_gate3_standard_input_in_pieces(self):
        # a pipe delivers a few bytes at a time; the output does not depend on where the reads fall
        import subprocess as sp
        import time
        t = self.mid
        flags = ["--select", "txt,id,w"]
        base = self.baseline(t, flags)
        p = sp.Popen([str(__import__("harness").binary()), *flags, "--format", "jsonl"], stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE)
        data = t.data
        cuts = [1, 3, 7, 100, 4093, 65535, 65536, 65537, 70000]
        at = 0
        for cut in cuts:
            if cut > len(data):
                break
            p.stdin.write(data[at:cut])
            p.stdin.flush()
            at = cut
            time.sleep(0.01)
        p.stdin.write(data[at:])
        p.stdin.close()
        out = p.stdout.read()
        err = p.stderr.read()
        p.wait()
        self.assertEqual((p.returncode, out, err), (base.status, base.stdout, base.stderr))

    def test_gate3_threads_one_to_sixty_four_are_accepted_and_sixty_five_is_not(self):
        t = self.small
        for n in (1, 64):
            got = self.frun(t.data, ["--rows"], "jsonl", extra=["--threads", n])
            self.assertEqual(got.status, 0)
        got = self.frun(t.data, ["--rows"], "jsonl", extra=["--threads", 65])
        self.assertEqual(got.status, 2)
        got = self.frun(t.data, ["--rows"], "csv", extra=["--threads", 65])
        self.assertEqual(got.status, 2)
