"""R2 (docs/readers.md section 12): `--input-format jsonl` (and `ndjson`) with `--fields`, `--absent`, `--max-json-nodes` and `--discover`, for a file and for
standard input. These gates were written from the specification before the code.

The oracle is jsonl_ref.py: independent of the tool, built on Python's `json` module (the repairs around it are the specification's: strict constants, exact number
text, duplicate keys on declared paths, lone surrogates, depth 128, node counting). It also generates the inputs.

* **J1** differential. Generated files and plans (the plan subset of test_plan.py, over the oracle's decoded cells) through the binary, compared with refimpl.py:
  the whole `data` of a success; for a refusal the rule, line, row, column, reason, kind and exit status, and nothing else in the document. When the plan itself
  refuses on an earlier record, that refusal is the answer. Also the named edge files (`edge_cases`) and the files that put bytes on the reader's chunk edges.
* **J2** the plans refimpl lacks (`:dec(S)`, `:float`, `--order-by`, `--report types`, `--from/--limit`, `--top`, csv output): the oracle's cells written as a CSV
  through `table` on that CSV must give, byte for byte, the answer `table --input-format jsonl` gives on the JSON lines.
* **J1b** the grammar of `--fields`, the flags' conflicts and bad values, `input.fields-required`, the shape, `--query`, `ndjson`.
* **J3** a file, standard input (named `-` and bare), every thread count and chunk size: the same bytes; the first refusal in file order.
* **J4** fuzz: mutated and random bytes never crash, only tagged refusals, always a valid document, and all configurations agree.
* **D** `--discover`; **E** `--explain`; **F** the limits (`--max-line-bytes`, `--max-rows`, paging).

`TABLE_JSONL_ROUNDS=N` multiplies the loops (default 1: a few minutes). `OracleSelfTest` runs the oracle alone, with no binary.
"""

import contextlib
import csv
import io
import json
import os
import random
import re
import subprocess
import traceback
import unittest

import jsonl_ref as J
import numbers_ref
import refimpl
import test_plan as tp
from harness import Scratch, binary, introspect, normalised as _normalised, run, run_argv, validate
from test_stdin import spawn

ROUNDS = max(1, int(os.environ.get("TABLE_JSONL_ROUNDS", "1")))
PAR_RE = re.compile(rb'"--threads","[0-9]+","--chunk-bytes","[0-9]+","--parallel-min-bytes","0",')
SYNTAX_CODES = {"syntax", "end", "control", "escape", "number", "utf8", "depth", "trailing", "full"}
PANIC = re.compile(rb"panic|Traceback|SIGSEGV|segmentation|stack overflow|goroutine|RUST_BACKTRACE|assertion failed|core dumped|Illegal instruction", re.I)


def par(threads, chunk):
    return ["--threads", threads, "--chunk-bytes", chunk, "--parallel-min-bytes", 0]


def norm(data, name, operand=True):
    """harness.normalised, and the operand in the middle of a repair's argv (`...,"t.jsonl","--absent","empty"]`) as well."""
    data = _normalised(data, name, operand)
    return data.replace(b',%s,' % json.dumps(name).encode(), b',"-",' if operand else b",")


NEAR_RE = re.compile(rb',"near":(?:"(?:[^"\\]|\\.)*"|\{[^}]*\})|,"key":"(?:[^"\\]|\\.)*"')


def strip_par(stdout):
    """The repair's own `--threads` flags go; with TABLE_JSONL_IGNORE_NEAR=1 (a debugging aid, not for the gate) so do `near` and `key`."""
    stdout = PAR_RE.sub(b"", stdout)
    return NEAR_RE.sub(b"", stdout) if os.environ.get("TABLE_JSONL_IGNORE_NEAR") else stdout


class Failures:
    """Run many cases and report the first few that fail, not the first only (and not a thousand of them)."""

    def __init__(self):
        self.items, self.count = [], 0

    @contextlib.contextmanager
    def case(self, label):
        self.count += 1
        try:
            yield
        except Exception as e:  # noqa: BLE001 - a case that raises is a case that fails
            where = "" if isinstance(e, AssertionError) else " at " + " | ".join(traceback.format_exc().splitlines()[-5:-1])
            self.items.append("%s: %s: %s%s" % (label, type(e).__name__, str(e)[:1200], where))

    def check(self, test, what="cases", show=6):
        if self.items:
            test.fail("%d of %d %s failed; the first %d:\n%s" % (len(self.items), self.count, what, min(show, len(self.items)), "\n\n".join(self.items[:show])))


# ---- expectations ------------------------------------------------------------------------------------------------------------------------------

def names_of(fields):
    return [J.item_parse(i)[1] for i in fields]


def kinds_of(names, rows):
    out = []
    for k in range(len(names)):
        col = [r[k] for r in rows]
        ints = sum(1 for c in col if refimpl.INTEGER.match(c))
        out.append("int" if col and ints * 10 >= len(col) * 7 else "text")
    return out


def clean_plan(plan):
    """No NUL in an argument (it cannot be put in an argv)."""
    for cond in plan.get("where") or []:
        if "lit" in cond and cond["lit"] is not None:
            cond["lit"] = cond["lit"].replace("\0", "x")
        if "lits" in cond:
            cond["lits"] = [l.replace("\0", "x") for l in cond["lits"]]
    return plan


def plan_ok(plan):
    """A plan the flags can spell for these names (an aggregate or a sort label with a comma, a backslash or a hash is not a list item the flag can carry)."""
    def bad(text):
        return "," in text or "\\" in text or text.startswith("#") or ":#" in text
    if "group" in plan:
        if any(c and bad(c) for f, c in plan["aggs"]):
            return False
        if plan.get("sort") and bad(plan["sort"]):
            return False
    return True


def plan_for(case, rows, rng):
    names = names_of(case.fields)
    kinds = kinds_of(names, rows)
    for _ in range(8):
        plan = clean_plan(tp.make_plan(rng, names, kinds, rows))
        if plan_ok(plan):
            return plan
    return {"where": []}


def expected(case, plan):
    """What the specification says a plan on this file answers: ("data", answer), ("plan", rule, detail, line) or ("decode", rule, detail)."""
    dec = case.oracle()
    names = names_of(case.fields)
    rows = dec[2] if dec[0] == "ok" else dec[3]
    ans, ragged, err = refimpl.run([names] + rows, plan)
    if err:
        return ("plan", err[0], err[1], J.line_of(case.data, err[1]["row"], case.max_line))
    if dec[0] == "ok":
        return ("data", ans)
    return ("decode", dec[1], dec[2])


class Jsonl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    # a file is read with cwd = the scratch directory and its relative name, so that a repair says the same as a standard input's but for the operand
    def frun(self, data, flags, name="t.jsonl"):
        self.s.write(name, data)
        return run(*flags, name, cwd=str(self.s.dir))

    def srun(self, data, flags, operand=True):
        return run(*flags, *(["-"] if operand else []), stdin=data, cwd=str(self.s.dir))

    def same_everywhere(self, data, flags, got, name="t.jsonl", threads=((3, 7),), label=""):
        """Standard input (named - and bare) and `--threads` give the answer of the file read once, but for the path."""
        want = (got.status, got.stdout, got.stderr)
        for operand in (True, False):
            s = self.srun(data, flags, operand)
            self.assertEqual((s.status, s.stdout, s.stderr), (want[0], norm(want[1], name, operand), norm(want[2], name, operand)),
                             "%s stdin operand=%s flags %r" % (label, operand, flags))
        for t, c in threads:
            p = self.frun(data, flags + par(t, c), name)
            self.assertEqual((p.status, strip_par(p.stdout), p.stderr), want, "%s threads %d chunk %d flags %r" % (label, t, c, flags))
        return want

    # ---- the comparison of an answer with what the specification says ----
    def assert_decode_refusal(self, got, rule, want, label=""):
        self.assertEqual(validate(got), [], label)
        doc = got.doc()
        self.assertEqual(got.status, 8, (label, doc))
        self.assertNotIn("data", doc, label)
        err = doc["error"]
        d = err["detail"]
        if want.get("ambiguous"):
            # the line has two defects whose order is the parser's (N12): either verdict is fine, but the place is the line's
            self.assertIn(err["rule"], (rule, J.SYNTAX, J.NODES), (label, doc))
            self.assertEqual((d.get("line"), d.get("row")), (want["line"], want["row"]), (label, doc))
            return
        self.assertEqual(err["rule"], rule, (label, want, doc))
        if rule == J.LONG:
            self.assertEqual((d["first_line"], d["limit"]), (want["first_line"], want["limit"]), (label, doc))
            self.assertEqual(d["longest"], want["longest"], (label, doc))
            return
        self.assertEqual((d["line"], d["row"]), (want["line"], want["row"]), (label, want, doc))
        self.assertIn("path", d)
        for key in ("column", "kind", "key", "limit"):
            if key in want:
                self.assertEqual(d.get(key), want[key], (label, key, want, doc))
        if rule == J.MISSING:
            self.assertIn(d["reason"], want.get("reason_any") or [want["reason"]], (label, want, doc))
            self.assertIn(d["reason"], ("missing", "null", "not-container"))
        if rule == J.SYNTAX:
            self.assertIn(d["code"], SYNTAX_CODES, doc)
            self.assertTrue(isinstance(d["offset"], int) and d["offset"] >= 0, doc)
            self.assertTrue(isinstance(d["reason"], str) and (isinstance(d["near"], str) or set(d["near"]) == {"b64"}), doc)
        if rule in (J.SYNTAX, J.NOT_OBJECT):
            self.assertEqual(d.get("looks_like"), want.get("looks_like"), (label, want, doc))
        else:
            self.assertNotIn("looks_like", d, label)

    def assert_expected(self, got, exp, label=""):
        kind = exp[0]
        if kind == "decode":
            return self.assert_decode_refusal(got, exp[1], exp[2], label)
        self.assertEqual(validate(got), [], label)
        doc = got.doc()
        if kind == "plan":
            self.assertEqual((got.status, got.first_rule()), (8, exp[1]), (label, exp, doc))
            d = doc["error"]["detail"]
            self.assertEqual((d["row"], d["column"], d["context"]), (exp[2]["row"], exp[2]["column"], exp[2]["context"]), (label, exp, doc))
            self.assertEqual(d["line"], exp[3], (label, "the line of the record, blank lines counted", doc))
            self.assertNotIn("data", doc)
            return
        answer = exp[1]
        self.assertEqual(got.status, 0, (label, doc))
        d = doc["data"]
        self.assertEqual(d["columns"], answer[1], (label, doc))
        self.assertEqual(d["rows"], answer[2], (label, doc))
        self.assertEqual((d["row_count"], d["truncated"]), (len(answer[2]), False), (label, doc))

    def check_case(self, case, plan, rng, label, wide=False):
        """One file, one plan: against the specification; with `wide` also by standard input and with threads."""
        plan_flags = tp.flags_of(plan, rng, "json")
        if not plan_flags:                       # no plan at all: the shape
            got = self.check_shape(case, label)
            if wide:
                self.same_everywhere(case.data, case.flags(), got, label=label)
            return
        exp = expected(case, plan)
        flags = case.flags() + plan_flags + ["--limit", 1000000]
        got = self.frun(case.data, flags)
        self.assert_expected(got, exp, "%s flags %r" % (label, flags))
        if wide:
            self.same_everywhere(case.data, flags, got, label=label)

    def check_shape(self, case, label, flags=()):
        dec = case.oracle()
        got = self.frun(case.data, case.flags() + list(flags))
        if dec[0] == "ok":
            self.assertEqual(validate(got), [], label)
            self.assertEqual(got.status, 0, (label, got))
            self.assertEqual(got.data(), {"headers": dec[1], "column_count": len(dec[1]), "row_count": len(dec[2]), "truncated": False}, label)
        else:
            self.assert_decode_refusal(got, dec[1], dec[2], label)
        return got


# ---- J1 ------------------------------------------------------------------------------------------------------------------------------------

class Differential(Jsonl):
    def test_j1_generated_files_and_plans_against_the_oracle(self):
        rng = random.Random(20261013)
        fails, outcomes = Failures(), {}
        total = 1500 * ROUNDS
        for i in range(total):
            case = J.random_case(rng, "r%d" % i)
            dec = case.oracle()
            outcomes[dec[0] if dec[0] == "ok" else dec[1]] = outcomes.get(dec[0] if dec[0] == "ok" else dec[1], 0) + 1
            plan = plan_for(case, dec[2] if dec[0] == "ok" else dec[3], rng)
            with fails.case("case %d %r plan %r" % (i, case, plan)):
                self.check_case(case, plan, random.Random(i), "case %d" % i, wide=i % 6 == 0)
            with fails.case("case %d shape %r" % (i, case)):
                if i % 3 == 0:
                    self.check_shape(case, "case %d" % i)
        fails.check(self, "generated cases")
        self.assertGreater(outcomes.get("ok", 0), total // 4, outcomes)
        for rule in (J.SYNTAX, J.MISSING, J.DUPLICATE, J.NOT_OBJECT, J.NOT_SCALAR, J.NODES):
            self.assertGreater(outcomes.get(rule, 0), 10, ("the generator must reach", rule, outcomes))

    def test_j1_named_edge_files(self):
        rng = random.Random(7)
        fails = Failures()
        for i, case in enumerate(J.edge_cases()):
            dec = case.oracle()
            rows = dec[2] if dec[0] == "ok" else dec[3]
            plans = [{"where": [], "select": names_of(case.fields)}, {"where": []}, plan_for(case, rows, rng), plan_for(case, rows, rng)]
            for k, plan in enumerate(plans):
                with fails.case("%s plan %r" % (case, plan)):
                    self.check_case(case, plan, random.Random(i * 10 + k), case.name, wide=k == 0)
            with fails.case("%s shape" % case):
                self.check_shape(case, case.name)
        fails.check(self, "named edge files")

    def test_j1_a_line_that_straddles_a_chunk_edge(self):
        # the reader fills 512 bytes and doubles to 64 KiB for a pipe, and reads 64 KiB for a file: escapes, multi-byte characters, a CR and its LF, a duplicate key
        # and a long line are cut at every byte of those edges
        rng = random.Random(11)
        fails = Failures()
        cases = J.chunk_edge_cases(rng, per_edge=3 * ROUNDS, fraction=min(1.0, 0.25 * ROUNDS))
        names = ["id", "name"]
        for case in cases:
            with fails.case(case.name):
                plan = {"where": [], "select": names}
                exp = expected(case, plan)
                flags = case.flags() + tp.flags_of(plan, random.Random(1), "json") + ["--limit", 1000000]
                got = self.frun(case.data, flags)
                self.assert_expected(got, exp, case.name)
                s = self.srun(case.data, flags)
                self.assertEqual((s.status, s.stdout), (got.status, norm(got.stdout, "t.jsonl", True)), case.name)
        fails.check(self, "chunk-edge files")
        self.assertGreater(len(cases), 200 * ROUNDS)

    def test_j1_stdin_in_pieces_cut_at_the_edges(self):
        # one-byte writes of a small file; and pieces that end one before, at and one after the edges of the reads of a pipe
        for label in ("crlf", "unicode escape", "surrogate pair escape", "multi-byte", "bom mid-file", "duplicate key", "not an object"):
            case = J.straddle_case(label, 512, 3)
            case.data = (J.BOM if label != "bom mid-file" else b"") + case.data[:2400]
            case.data = case.data[:case.data.rfind(b"\n") + 1]
            flags = case.flags() + ["--select", "id,name"]
            want = self.frun(case.data, flags)
            for how in ({"step": 1}, {"cut": [511, 512, 513, 1023, 1024, 1025]}, {"cut": [1, 2, 3, len(case.data) - 1]}):
                status, out, err = spawn([binary(), *[str(a) for a in flags], "-"], case.data, pause=0.0005 if "step" in how else 0.02, **how)
                self.assertEqual((status, out, err), (want.status, norm(want.stdout, "t.jsonl", True), want.stderr), (label, how))

    def test_the_syntax_codes_where_the_name_is_certain(self):
        # N7: offsets are the parser's and not compared; the code names are in 12.5, and for these lines there is one natural name
        certain = {
            "depth": b'{"id":1,"name":"x","d":' + b"[" * 129 + b"]" * 129 + b"}\n",
            "utf8": b'{"id":1,"name":"\xff"}\n',
            "control": b'{"id":1,"name":"a\x01b"}\n',
            "trailing": b'{"id":1,"name":"x"} x\n',
        }
        for code, line in certain.items():
            got = self.frun(line, ["--input-format", "jsonl", "--fields", "id,name"])
            self.assertEqual(got.first_rule(), J.SYNTAX, got)
            self.assertEqual(got.error()["detail"]["code"], code, got)
        got = self.frun(b'{"id":1,"name":"x"\n', ["--input-format", "jsonl", "--fields", "id,name"])
        self.assertIn(got.error()["detail"]["code"], ("end", "syntax"), got)
        self.assertGreaterEqual(got.error()["detail"]["offset"], 17)
        self.assertLessEqual(got.error()["detail"]["offset"], 18)


# ---- J2 ------------------------------------------------------------------------------------------------------------------------------------

def suggest(cells):
    """The type a column's cells can all be read as (numbers_ref is the oracle of the grammars), or '' for text."""
    if not cells:
        return ":int"
    if all(refimpl.INTEGER.match(c) and refimpl.to_int(c)[1] is None for c in cells):
        return ":int"
    scale = max((len(c.split(".", 1)[1]) if "." in c else 0 for c in cells), default=0)
    if scale <= 18 and all(numbers_ref.dec(c, scale)[0] == "ok" for c in cells):
        return ":dec(%d)" % scale
    if all(numbers_ref.flt(c)[0] == "ok" for c in cells):
        return ":float"
    return ""


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.]+$")


def j2_flags(rng, names, rows):
    """Flags for the plans refimpl does not cover, over these names. Every column gets one type for the whole plan (the engine refuses two)."""
    types = {n: (suggest([r[k] for r in rows]) if rng.random() < 0.85 else rng.choice(["", ":int", ":float", ":dec(2)"])) for k, n in enumerate(names)}

    def lit(name):
        t = types[name]
        if t == ":int":
            return rng.choice(["0", "5", "-1", "100", "+7", "007"])
        if t.startswith(":dec"):
            return rng.choice(["0", "1.5", "-2.25", "10", "0.1"])
        if t == ":float":
            return rng.choice(["0", "1e2", "2.5", "-1.5e-3", "1E3"])
        cells = [r[names.index(name)] for r in rows]
        return tp.render_word(rng.choice(cells + tp.TEXT[:5]).replace("\0", "x") if cells and rng.random() < 0.6 else rng.choice(tp.TEXT), rng)

    def cond():
        n = rng.choice(names)
        t = types[n]
        col = tp.render_word(n, rng) + t
        r = rng.random()
        if t == "" and r < 0.2:
            return "%s contains %s" % (col, lit(n))
        if r < 0.35:
            return "%s in (%s)" % (col, ", ".join(lit(n) for _ in range(rng.randint(1, 3))))
        return "%s %s %s" % (col, rng.choice(["=", "!=", "<", "<=", ">", ">="]), lit(n))

    def where():
        return ["--where", " and ".join(cond() for _ in range(rng.randint(1, 2)))] if rng.random() < 0.6 else []

    def page():
        return (["--limit", rng.randint(1, 6)] if rng.random() < 0.5 else []) + (["--from", rng.randint(0, 4)] if rng.random() < 0.4 else [])

    def select():
        return ["--select", tp.render_list(rng.sample(names, rng.randint(1, min(3, len(names)))))] if rng.random() < 0.7 else []

    form = rng.choice(["shape", "rows", "rows", "order", "order", "groups", "groups", "report"])
    if form == "shape":
        return []
    if form == "report":
        return ["--report", "types"] + (["--format", "csv"] if rng.random() < 0.3 else [])
    out = []
    if form == "rows":
        out = where() + select() + page()
    elif form == "order":
        usable = [n for n in names if SAFE_NAME.match(n)]
        if not usable:
            return select()
        keys = ",".join(rng.choice(["", "-"]) + n + types[n] for n in rng.sample(usable, rng.randint(1, min(2, len(usable)))))
        out = where() + ["--order-by", keys] + select() + page()
    else:
        usable = [n for n in names if not (SAFE_NAME.match(n) is None and ("," in n or "\\" in n or n.startswith("#")))]
        group = rng.sample(names, rng.randint(0, min(2, len(names))))
        aggs = ["count"]
        for _ in range(rng.randint(0, 3)):
            f = rng.choice(["sum", "min", "max", "distinct"])
            if usable:
                n = rng.choice(usable)
                aggs.append("%s:%s%s" % (f, n, types[n] if f != "distinct" else ""))
        out = where() + (["--group", tp.render_list(group)] if group else []) + ["--agg", ",".join(aggs)]
        if rng.random() < 0.5:
            out += ["--sort", rng.choice(["", "-"]) + "count"] + (["--top", rng.randint(1, 3)] if rng.random() < 0.5 else [])
        out += page() if rng.random() < 0.4 else []
    if rng.random() < 0.25:
        out += ["--format", "csv"]
    return out


def strip_lines(doc):
    """An answer with what legitimately differs between a CSV and its JSON lines removed: the path, the line (checked apart), the repair's argv."""
    doc = json.loads(json.dumps(doc))
    for e in doc.get("errors", []) + ([doc["error"]] if "error" in doc else []):
        e["detail"].pop("path", None)
        e["detail"].pop("line", None)
        if e.get("repair") and e["repair"].get("argv"):
            e["repair"] = {"kind": e["repair"]["kind"]}
    return doc


class Equivalence(Jsonl):
    def test_j2_the_cells_as_a_csv_give_the_same_answer(self):
        rng = random.Random(20261014)
        fails = Failures()
        tally = {"success": 0, "plan": 0, "decode": 0}
        for i in range(1000 * ROUNDS):
            case = J.random_case(rng, "e%d" % i)
            dec = case.oracle()
            names = names_of(case.fields)
            rows = dec[2] if dec[0] == "ok" else dec[3]
            flags = j2_flags(rng, names, rows)
            if dec[0] != "ok":      # a streaming read stops when its page is full, before a refusal further on: no paging when the file refuses
                for k in ("--limit", "--from"):
                    while k in flags:
                        at = flags.index(k)
                        del flags[at:at + 2]
            if any("\r" in c for r in rows for c in r):
                continue        # the CSV reader's CR handling inside a quoted field is not what is under test
            with fails.case("case %d %r flags %r" % (i, case, flags)):
                self.s.write("c.csv", J.to_csv(names, rows))
                want = run(*flags, "c.csv", cwd=str(self.s.dir))
                got = self.frun(case.data, case.flags() + flags)
                csv_out = "csv" in flags[flags.index("--format") + 1:flags.index("--format") + 2] if "--format" in flags else False
                if dec[0] == "ok":
                    tally["success" if want.status == 0 else "plan"] += 1
                    self.compare_with_csv(got, want, dec, csv_out, flags, case)
                elif want.status != 0:
                    tally["plan"] += 1                 # the plan refuses on an earlier record: the same refusal
                    self.compare_with_csv(got, want, dec, csv_out, flags, case)
                else:
                    tally["decode"] += 1
                    if csv_out:
                        self.assertEqual(got.status, 8, got)
                        self.assertTrue(got.stderr.startswith(b"table: " + dec[1].encode() + b": "), (dec[1], got.stderr))
                    else:
                        self.assert_decode_refusal(got, dec[1], dec[2], case.name)
        fails.check(self, "cases")
        self.assertGreater(tally["success"], 200, tally)
        self.assertGreater(tally["plan"], 20, tally)
        self.assertGreater(tally["decode"], 50, tally)

    def compare_with_csv(self, got, want, dec, csv_out, flags, case):
        self.assertEqual(got.status, want.status, (got, want))
        if csv_out:
            self.assertEqual((got.stdout, got.stderr), (want.stdout, want.stderr))
            return
        gd, wd = got.doc(), want.doc()
        if "--report" in flags:
            return self.compare_report(gd, wd, dec)
        self.assertEqual(strip_lines(gd), strip_lines(wd))
        if "error" in wd and "line" in wd["error"]["detail"]:
            row = wd["error"]["detail"]["row"]
            self.assertEqual(gd["error"]["detail"]["line"], J.line_of(case.data, row, case.max_line))

    def compare_report(self, gd, wd, dec):
        """The type report names the first row and line of each kind of cell: the rows are the CSV's, the lines are the JSON lines'."""
        gc, wc = gd["data"]["columns"], wd["data"]["columns"]
        self.assertEqual(gc, wc)
        keep = [k for k, c in enumerate(wc) if not c.endswith("_line")]
        pick = lambda rows: [[r[k] for k in keep] for r in rows]
        self.assertEqual(pick(gd["data"]["rows"]), pick(wd["data"]["rows"]))
        rowcols = [k for k, c in enumerate(wc) if c.endswith("_row")]
        for g, w in zip(gd["data"]["rows"], wd["data"]["rows"]):
            for k in rowcols:
                if w[k] and dec[0] == "ok":
                    self.assertEqual(g[k + 1], str(dec[3]["line_of_row"][int(w[k]) - 1]))


# ---- J1b -------------------------------------------------------------------------------------------------------------------------------------

BAD_FIELDS = [("", 0), (",", 0), ("a,", 1), (",a", 0), ("a,,b", 1), ("a.", 0), (".a", 0), ("a..b", 0), ("a,b.", 1), ("a\\", 0), ("a\\x", 0), ("a,b\\q", 1), ("a,a", 1), ("a,b,a", 2),
              ("a\\,b,a\\,b", 1), ("a.b,c,a.b", 2), ("1.2.3.4.5.6.7.8.9", 0), ("a,1.2.3.4.5.6.7.8.9", 1), ("a\\\\.", 0), ("\\", 0), ("a, b,", 2)]


class Fields(Jsonl):
    def test_every_bad_fields_is_refused_with_its_item(self):
        for text, item in BAD_FIELDS:
            with self.subTest(fields=text):
                self.assertEqual(J.fields_parse(text)[:2], (J.BAD_FIELDS, item), "the oracle's own table")
                for flags in (["--input-format", "jsonl", "--fields", text], ["--input-format", "ndjson", "--fields", text, "--select", "a"]):
                    got = self.frun(b'{"a":1}\n', flags)
                    self.assertEqual(validate(got), [])
                    if text == "":      # decision to confirm: the implementer's empty flag value is args.bad-value, the spec's empty path is input.bad-fields
                        self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"), got)
                        continue
                    self.assertEqual((got.status, got.first_rule()), (2, J.BAD_FIELDS), got)
                    d = got.error()["detail"]
                    self.assertEqual(d["item"], item, got)
                    self.assertTrue(d["reason"] and isinstance(d["reason"], str), got)
                    self.assertNotIn("data", got.doc())
                    self.assertTrue(got.error()["repair"] is None or got.error()["repair"]["kind"] == "none", got)

    def test_too_many_paths(self):
        ok = ",".join("p%d" % i for i in range(4096))
        got = self.frun(b"", ["--input-format", "jsonl", "--fields", ok])
        self.assertEqual((got.status, got.data()["column_count"]), (0, 4096), got)
        got = self.frun(b"", ["--input-format", "jsonl", "--fields", ok + ",p4096"])
        self.assertEqual((got.status, got.first_rule()), (2, J.BAD_FIELDS), got)
        self.assertEqual(got.error()["detail"]["item"], 4096, "the first path past the 4096th (decision A)")

    def test_eight_segments_are_fine_and_nine_are_not(self):
        got = self.frun(b'{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":7}}}}}}}}\n', ["--input-format", "jsonl", "--fields", "a.b.c.d.e.f.g.h", "--select", "a.b.c.d.e.f.g.h"])
        self.assertEqual(got.data()["rows"], [["7"]], got)

    def test_random_fields_texts_against_the_oracle(self):
        rng = random.Random(5)
        alphabet = ["a", "b", "1", "0", ".", ",", "\\", "#", " ", "é", "a", "b", ".", ",", "\\,", "\\.", "\\\\", "\\#"]
        fails = Failures()
        for i in range(700 * ROUNDS):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 10)))
            exp = J.fields_parse(text)
            with fails.case("fields %r" % text):
                got = self.frun(b"", ["--input-format", "jsonl", "--fields", text])
                self.assertEqual(validate(got), [], got)
                if exp[0] == "ok":
                    self.assertEqual((got.status, got.data()["headers"]), (0, exp[2]), (text, got))
                else:
                    self.assertEqual((got.status, got.first_rule()), (2, J.BAD_FIELDS), (text, got))
                    self.assertEqual(got.error()["detail"]["item"], exp[1], "leftmost bad item (decision): %r" % text)
        fails.check(self, "--fields texts")

    def test_names_are_the_items_as_written_but_for_an_escaped_comma(self):
        fields = ["id", "a\\.b", "k\\,x", "x\\\\y", "\\#h", "user.name", "items.0.sku", "a\\,b.c", "ünï"]
        names = ["id", "a\\.b", "k,x", "x\\\\y", "\\#h", "user.name", "items.0.sku", "a,b.c", "ünï"]
        text = ",".join(fields)
        self.assertEqual(J.fields_parse(text)[2], names)
        doc = ('{"id":1,"a.b":"A","k,x":"K","x\\\\y":"X","#h":"H","user":{"name":"U"},"items":[{"sku":"S"}],"a,b":{"c":"C"},"ünï":"N"}\n').encode()
        got = self.frun(doc, ["--input-format", "jsonl", "--fields", text])
        self.assertEqual(got.data()["headers"], names, got)
        # the header, --select, --where, --group, and the refusals' column name every one of them by the name
        sel = self.frun(doc, ["--input-format", "jsonl", "--fields", text, "--select", tp.render_list(names)])
        self.assertEqual((sel.data()["columns"], sel.data()["rows"]), (names, [["1", "A", "K", "X", "H", "U", "S", "C", "N"]]), sel)
        for k, name in enumerate(names):
            one = self.frun(doc, ["--input-format", "jsonl", "--fields", text, "--select", tp.render_list([name]), "--where", tp.render_word(name, random.Random(1)) + " != ''"])
            self.assertEqual(one.data()["rows"], [[sel.data()["rows"][0][k]]], (name, one))
            grp = self.frun(doc, ["--input-format", "jsonl", "--fields", text, "--group", tp.render_list([name])])
            self.assertEqual(grp.data()["columns"], [name, "count"], (name, grp))
        # position references
        pos = self.frun(doc, ["--input-format", "jsonl", "--fields", text, "--select", "#3,#1"])
        self.assertEqual(pos.data()["rows"], [["K", "1"]], pos)
        bad = self.frun(doc.replace(b'"a,b":{"c":"C"}', b'"a,b":{"c":null}'), ["--input-format", "jsonl", "--fields", text])
        self.assertEqual((bad.first_rule(), bad.error()["detail"]["column"], bad.error()["detail"]["reason"]), (J.MISSING, "a,b.c", "null"), bad)
        bad = self.frun(doc.replace(b'"x\\\\y":"X"', b'"x\\\\y":[1]'), ["--input-format", "jsonl", "--fields", text])
        self.assertEqual((bad.first_rule(), bad.error()["detail"]["column"], bad.error()["detail"]["kind"]), (J.NOT_SCALAR, "x\\\\y", "array"), bad)

    def test_a_valid_random_declaration_round_trips(self):
        rng = random.Random(8)
        keys = ["a", "b", "c d", "x.y", "p,q", "z\\w", "#n", "0", "01", "é", "k"]
        fails = Failures()
        for i in range(200 * ROUNDS):
            paths = []
            for _ in range(rng.randint(1, 5)):
                p = [rng.choice(keys) for _ in range(rng.randint(1, 4))]
                if not any(p[:len(q)] == q or q[:len(p)] == p for q in paths):
                    paths.append(p)
            fields = [J.escape_item(p) for p in paths]
            names = names_of(fields)
            doc = {}
            for n, p in enumerate(paths):
                node = doc
                for s in p[:-1]:
                    node = node.setdefault(s, {})
                node[p[-1]] = "v%d" % n
            data = (json.dumps(doc, ensure_ascii=rng.random() < 0.5) + "\n").encode()
            with fails.case("paths %r fields %r" % (paths, fields)):
                got = self.frun(data, ["--input-format", "jsonl", "--fields", ",".join(fields), "--select", tp.render_list(names)])
                self.assertEqual((got.data()["columns"], got.data()["rows"]), (names, [["v%d" % n for n in range(len(paths))]]), got)
        fails.check(self, "round trips")


class Flags(Jsonl):
    def test_args_conflict(self):
        data = b'{"a":1}\n'
        cases = [
            ["--fields", "a"], ["--input-format", "csv", "--fields", "a"], ["--absent", "empty"], ["--input-format", "csv", "--absent", "refuse"], ["--max-json-nodes", "10"],
            ["--discover"], ["--input-format", "csv", "--discover"],
        ]
        base = ["--input-format", "jsonl", "--discover"]
        for extra in (["--select", "a"], ["--where", "a = 1"], ["--group", "a"], ["--agg", "count"], ["--order-by", "a"], ["--report", "types"], ["--query", "select a from 't.jsonl'"],
                      ["--explain"], ["--format", "csv"], ["--format", "text"]):
            cases.append(base + extra)
        for flags in cases:
            with self.subTest(flags=flags):
                got = self.frun(data, flags)
                if got.stdout:
                    self.assertEqual(validate(got), [], got)
                    self.assertEqual((got.status, got.first_rule()), (2, "args.conflict"), (flags, got))
                    self.assertNotIn("data", got.doc())
                else:        # --format csv / text answer on standard error
                    self.assertEqual(got.status, 2, got)
                    self.assertIn(b"args.conflict", got.stderr)
        for flags in (base + ["--limit", "1"], base + ["--from", "1"], base + ["--fields", "a", "--select", "a"]):
            self.assertEqual(self.frun(data, flags).status, 2, flags)
        # --discover-lines and --max-fields need --discover
        for flags in (["--input-format", "jsonl", "--fields", "a", "--discover-lines", "5"], ["--input-format", "jsonl", "--fields", "a", "--max-fields", "5"]):
            got = self.frun(data, flags)
            self.assertEqual(got.status, 2, got)
            self.assertIn(got.first_rule(), ("args.conflict", "args.required-flag"), got)

    def test_bad_values_and_their_ceilings(self):
        data = b'{"a":1}\n'
        J1 = ["--input-format", "jsonl", "--fields", "a"]
        for flags in (["--input-format", "xml"], ["--input-format", "JSONL"], J1 + ["--absent", "maybe"], J1 + ["--max-json-nodes", "0"], J1 + ["--max-json-nodes", "1048577"],
                      J1 + ["--max-json-nodes", "-1"], J1 + ["--max-json-nodes", "x"], ["--input-format", "jsonl", "--discover", "--discover-lines", "0"],
                      ["--input-format", "jsonl", "--discover", "--discover-lines", "1000001"], ["--input-format", "jsonl", "--discover", "--max-fields", "0"],
                      ["--input-format", "jsonl", "--discover", "--max-fields", "4097"]):
            got = self.frun(data, flags)
            self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"), (flags, got))
        for flags in (J1 + ["--max-json-nodes", "3"], J1 + ["--max-json-nodes", "1048576"], ["--input-format", "jsonl", "--discover", "--discover-lines", "1000000"],
                      ["--input-format", "jsonl", "--discover", "--max-fields", "4096"], ["--input-format", "jsonl", "--discover", "--discover-lines", "1", "--max-fields", "1"],
                      J1 + ["--absent", "refuse"], J1 + ["--absent", "empty"], ["--input-format", "csv"]):
            got = self.frun(data if "jsonl" in flags else b"a\n1\n", flags)
            self.assertEqual(got.status, 0, (flags, got))

    def test_the_default_is_csv_and_a_name_is_not_a_hint(self):
        # never guessed from a name or from the content
        got = self.frun(b'{"a":1}\n', [], name="x.jsonl")
        self.assertEqual(got.data()["headers"], ['{"a":1}'], got)
        got = self.frun(b'a,b\n1,2\n', ["--input-format", "jsonl", "--fields", "a"], name="x.csv")
        self.assertEqual(got.first_rule(), J.SYNTAX, got)

    def test_max_json_nodes_is_the_limit_on_the_line(self):
        line = b'{"id":1,"name":"x","a":[1,2]}\n'          # 1 + 2 + 2 + 2 + 2 = 9? the oracle counts
        n = J.count_nodes(line.decode())
        self.assertEqual(n, 1 + 2 * 2 + 1 + 1 + 2)
        for limit, ok in ((n, True), (n - 1, False), (n + 1000, True)):
            got = self.frun(line, ["--input-format", "jsonl", "--fields", "id,name", "--max-json-nodes", limit])
            if ok:
                self.assertEqual(got.status, 0, (limit, got))
            else:
                self.assertEqual((got.status, got.first_rule()), (8, J.NODES), got)
                d = got.error()["detail"]
                self.assertEqual((d["limit"], d["line"], d["row"]), (limit, 1, 1), got)

    def test_absent_refuse_and_empty(self):
        data = b'{"a":"1","b":"x"}\n{"a":"2"}\n{"a":"3","b":null}\n{"a":"4","b":{"c":1}}\n{"b":"y"}\n{"a":5,"b":"z"}\n'
        base = ["--input-format", "jsonl", "--fields", "a,b"]
        got = self.frun(data, base)
        self.assertEqual((got.status, got.first_rule()), (8, J.MISSING), got)
        d = got.error()["detail"]
        self.assertEqual((d["line"], d["row"], d["column"], d["reason"]), (2, 2, "b", "missing"), got)
        self.assertEqual(got.error()["repair"]["kind"], "retry", got)
        self.assertIn("--absent", got.error()["repair"]["argv"], got)
        self.assertIn("empty", got.error()["repair"]["argv"], got)
        # the repair is the same invocation with the one thing changed; applied, it meets the next refusal (a container: --absent says nothing about it)
        again = run_argv(got.error()["repair"]["argv"], cwd=str(self.s.dir))
        self.assertEqual((again.status, again.first_rule()), (8, J.NOT_SCALAR), again)
        self.assertEqual(again.error()["detail"]["kind"], "object")
        clean = data.replace(b'"b":{"c":1}', b'"b":"w"')
        got = self.frun(clean, base + ["--absent", "empty", "--select", "a,b"])
        self.assertEqual(got.data()["rows"], [["1", "x"], ["2", ""], ["3", ""], ["4", "w"], ["", "y"], ["5", "z"]], got)
        # the three reasons
        for line, reason in ((b'{"a":1}', "missing"), (b'{"a":1,"b":null}', "null"), (b'{"a":1,"b":5}', None)):
            fields = ["a", "b"] if reason else ["a", "b.c"]
            got = self.frun(line + b"\n", ["--input-format", "jsonl", "--fields", ",".join(fields)])
            self.assertEqual(got.first_rule(), J.MISSING, got)
            self.assertEqual(got.error()["detail"]["reason"], reason or "not-container", got)
        # the empty cell is what the typed reads refuse
        got = self.frun(b'{"a":1}\n{"a":null}\n', ["--input-format", "jsonl", "--fields", "a", "--absent", "empty", "--where", "a:int > 0"])
        self.assertEqual((got.first_rule(), got.error()["detail"]["row"], got.error()["detail"]["value"]), ("value.not-integer", 2, ""), got)
        got = self.frun(b'{"a":1}\n{"a":null}\n', ["--input-format", "jsonl", "--fields", "a", "--absent", "empty", "--where", "a != '' and a:int > 0"])
        self.assertEqual(got.data()["rows"], [["1"]], got)

    def test_a_number_is_its_token_and_the_typed_reads_read_it_as_a_csv_cell(self):
        data = b'{"v":1E3}\n{"v":1.50}\n{"v":-0}\n{"v":123456789012345678901234567890}\n{"v":0.12345678901234567}\n'
        base = ["--input-format", "jsonl", "--fields", "v"]
        got = self.frun(data, base + ["--select", "v"])
        self.assertEqual(got.data()["rows"], [["1E3"], ["1.50"], ["-0"], ["123456789012345678901234567890"], ["0.12345678901234567"]], got)
        got = self.frun(data, base + ["--where", "v:int > 0"])
        self.assertEqual((got.first_rule(), got.error()["detail"]["row"], got.error()["detail"]["value"]), ("value.not-integer", 1, "1E3"), got)
        lines = data.split(b"\n")
        got = self.frun(b"\n".join(lines[1:]), base + ["--where", "v:dec(2) > 0", "--select", "v"])
        self.assertEqual((got.first_rule(), got.error()["detail"]["row"]), ("value.decimal-too-wide", 3), got)       # 1.50 and -0 are fine, the 30 digits are too wide
        got = self.frun(lines[0] + b"\n", base + ["--where", "v:dec(2) > 0"])
        self.assertEqual((got.first_rule(), got.error()["detail"]["value"]), ("value.not-decimal", "1E3"), got)
        got = self.frun(data, base + ["--where", "v:float > 0", "--select", "v"])
        self.assertEqual(got.status, 0, got)
        self.assertEqual(len(got.data()["rows"]), 4, got)
        got = self.frun(b'{"v":9223372036854775808}\n', base + ["--where", "v:int > 0"])
        self.assertEqual(got.first_rule(), "value.integer-overflow", got)
        got = self.frun(b'{"v":true}\n{"v":false}\n', base + ["--select", "v"])
        self.assertEqual(got.data()["rows"], [["true"], ["false"]], got)
        got = self.frun(b'{"v":true}\n', base + ["--where", "v:int > 0"])
        self.assertEqual(got.first_rule(), "value.not-integer", got)
        got = self.frun(b'{"v":"12"}\n', base + ["--where", "v:int = 12", "--select", "v"])
        self.assertEqual(got.data()["rows"], [["12"]], got)

    def test_a_plan_without_fields_is_input_fields_required(self):
        data = b'{"id":1,"name":"x"}\n{"id":2,"name":"y"}\n'
        for flags in (["--input-format", "jsonl"], ["--input-format", "jsonl", "--select", "id"], ["--input-format", "ndjson", "--where", "id = 1"], ["--input-format", "jsonl", "--group", "id"],
                      ["--input-format", "jsonl", "--report", "types"], ["--input-format", "jsonl", "--max-json-nodes", "9"]):
            got = self.frun(data, flags)
            self.assertEqual(validate(got), [], got)
            self.assertEqual((got.status, got.first_rule()), (2, "input.fields-required"), (flags, got))
            self.assertNotIn("data", got.doc())
            rep = got.error()["repair"]
            self.assertEqual(rep["kind"], "retry", got)
            self.assertIn("--discover", rep["argv"], got)
        got = self.srun(data, ["--input-format", "jsonl", "--query", "select id from stdin"], operand=False)
        self.assertEqual((got.status, got.first_rule()), (2, "input.fields-required"), got)
        # the shape too (N1), with a stdin
        got = self.srun(data, ["--input-format", "jsonl"])
        self.assertEqual((got.status, got.first_rule()), (2, "input.fields-required"), got)
        # the repair of the plain invocation runs, and what it answers is a way to choose the fields
        got = self.frun(data, ["--input-format", "jsonl"])
        again = run_argv(got.error()["repair"]["argv"], cwd=str(self.s.dir))
        self.assertEqual(again.status, 0, again)
        self.assertEqual([f["path"] for f in again.data()["discover"]["fields"]], ["id", "name"], again)
        # the repair of a refusal with a plan keeps working: it is a run that succeeds
        got = self.frun(data, ["--input-format", "jsonl", "--select", "id"])
        again = run_argv(got.error()["repair"]["argv"], cwd=str(self.s.dir))
        self.assertEqual(again.status, 0, (got.error()["repair"], again))

    def test_the_shape_is_the_declaration_and_the_count_of_records(self):
        got = self.frun(b'\xef\xbb\xbf{"a":1}\n\n{"a":2,"b":9}\r\n  \n{"a":3}', ["--input-format", "jsonl", "--fields", "a,b.c,z"] + ["--absent", "empty"])
        self.assertEqual(got.data(), {"headers": ["a", "b.c", "z"], "column_count": 3, "row_count": 3, "truncated": False}, got)
        got = self.frun(b"", ["--input-format", "jsonl", "--fields", "a,b"])
        self.assertEqual(got.data(), {"headers": ["a", "b"], "column_count": 2, "row_count": 0, "truncated": False}, got)
        got = self.srun(b"\n \n", ["--input-format", "jsonl", "--fields", "a,b"])
        self.assertEqual(got.data(), {"headers": ["a", "b"], "column_count": 2, "row_count": 0, "truncated": False}, got)
        # every line is parsed for the shape too
        got = self.frun(b'{"a":1}\n{"a":\n', ["--input-format", "jsonl", "--fields", "a"])
        self.assertEqual((got.first_rule(), got.error()["detail"]["row"]), (J.SYNTAX, 2), got)
        got = self.frun(b'{"a":1}\n{"b":1}\n', ["--input-format", "jsonl", "--fields", "a"])
        self.assertEqual((got.first_rule(), got.error()["detail"]["row"]), (J.MISSING, 2), got)

    def test_ndjson_is_jsonl(self):
        rng = random.Random(3)
        fails = Failures()
        for i in range(120):
            case = J.random_case(rng, "n%d" % i)
            dec = case.oracle()
            plan = plan_for(case, dec[2] if dec[0] == "ok" else dec[3], rng)
            flags = tp.flags_of(plan, random.Random(i), "json") + ["--limit", 1000000]
            with fails.case(repr(case)):
                a = self.frun(case.data, case.flags() + flags)
                b = self.frun(case.data, ["ndjson" if x == "jsonl" else x for x in case.flags()] + flags)
                self.assertEqual((a.status, a.stdout, a.stderr), (b.status, b.stdout.replace(b'"ndjson"', b'"jsonl"'), b.stderr))
        fails.check(self, "ndjson cases")

    def test_csv_output_delimiter(self):
        # --delimiter with JSON lines input only sets the delimiter of CSV output
        data = b'{"a":"x;y","b":"1"}\n{"a":"p","b":"2"}\n'
        for delim in ("tab", ";"):
            got = self.frun(data, ["--input-format", "jsonl", "--fields", "a,b", "--select", "a,b", "--format", "csv", "--delimiter", delim])
            sep = "\t" if delim == "tab" else ";"
            rows = list(csv.reader(io.StringIO(got.stdout.decode()), delimiter=sep))
            self.assertEqual(rows, [["a", "b"], ["x;y", "1"], ["p", "2"]], got)
            self.assertEqual(got.status, 0, got)

    def test_query_on_the_declared_columns(self):
        data = b'{"id":1,"user":{"name":"ann"},"a.b":"p","t":"x"}\n{"id":2,"user":{"name":"bob"},"a.b":"q","t":"y"}\n{"id":3,"user":{"name":"cy"},"a.b":"p","t":"x"}\n'
        fields = ["id", "user.name", "a\\.b", "t"]
        names = names_of(fields)
        rows = [["1", "ann", "p", "x"], ["2", "bob", "q", "y"], ["3", "cy", "p", "x"]]
        self.s.write("q.csv", J.to_csv(names, rows))
        base = ["--input-format", "jsonl", "--fields", ",".join(fields)]
        queries = ["select * from '%s'", "select \"user.name\", id from '%s' where id::int > 1", "select \"user.name\" from '%s' where \"a\\.b\" = 'p' order by id::int desc",
                   "select t, count(*) from '%s' group by t order by count(*) desc", "select \"a\\.b\", sum(id::int) from '%s' group by \"a\\.b\"", "select id from '%s' limit 2 offset 1",
                   "select #2, #1 from '%s' where t = 'x'"]
        for q in queries:
            want = run("--query", q % "q.csv", cwd=str(self.s.dir))
            self.s.write("t.jsonl", data)
            got = run(*base, "--query", q % "t.jsonl", cwd=str(self.s.dir))
            self.assertEqual(want.status, 0, want)
            self.assertEqual((got.status, got.stdout), (want.status, want.stdout), (q, got, want))
        # the source `stdin`, with a pipe
        got = self.srun(data, base + ["--query", "select \"user.name\" from stdin where id::int = 2"], operand=False)
        self.assertEqual(got.data()["rows"], [["bob"]], got)
        # without --fields it is input.fields-required, with a name that is not declared it is the engine's
        got = run(*base, "--query", "select nick from 't.jsonl'", cwd=str(self.s.dir))
        self.assertEqual(got.status, 3, got)
        self.assertEqual(got.first_rule(), "select.unknown-column", got)


# ---- D: --discover ---------------------------------------------------------------------------------------------------------------------------

def discover_doc(got):
    return got.data()["discover"]


def norm_kinds(kinds):
    return {k: v for k, v in kinds.items() if v}


class Discover(Jsonl):
    def compare(self, got, exp, label):
        if exp[0] == "refuse":
            self.assert_decode_refusal(got, exp[1], exp[2], label)
            return
        self.assertEqual(validate(got), [], (label, got))
        self.assertEqual(got.status, 0, (label, got))
        d = discover_doc(got)
        want = exp[1]
        self.assertEqual([f["path"] for f in d["fields"]], [f["path"] for f in want["fields"]], (label, got))
        for g, w in zip(d["fields"], want["fields"]):
            self.assertEqual((norm_kinds(g["kinds"]), g["first_line"]), (norm_kinds(w["kinds"]), w["first_line"]), (label, g["path"], got))
        for key in ("lines_examined", "paths_truncated", "undeclarable", "arrays"):
            self.assertEqual(d[key], want[key], (label, key, got))
        self.assertIn(d["complete"], want["complete"], (label, got))
        self.assertEqual(set(d), {"fields", "lines_examined", "complete", "paths_truncated", "undeclarable", "arrays"}, d)

    def test_the_example_of_the_specification(self):
        lines = [b'{"id":%d,"tags":["a"],"user":{"name":"n%d"}}' % (i, i) for i in range(1, 2501)]
        got = self.frun(b"\n".join(lines) + b"\n", ["--input-format", "jsonl", "--discover"])
        d = discover_doc(got)
        self.assertEqual(d["fields"][0], {"path": "id", "kinds": {"int": 1000}, "first_line": 1}, got)
        self.assertEqual((d["lines_examined"], d["complete"], d["paths_truncated"], d["undeclarable"], d["arrays"]), (1000, False, False, 0, ["tags"]), got)
        self.assertEqual([f["path"] for f in d["fields"]], ["id", "tags", "user.name"], got)

    def test_generated_files_against_the_oracle(self):
        rng = random.Random(77)
        fails, compared = Failures(), 0
        for i in range(500 * ROUNDS):
            case = J.random_case(rng, "d%d" % i)
            if J.has_duplicates(case.data):
                continue            # `missing` of a path seen twice in a record is not specified (decision A6)
            n = rng.choice([1, 2, 3, 5, 1000])
            m = rng.choice([1, 2, 3, 256])
            exp = J.discover(case.data, n, m, case.max_nodes, case.max_line)
            if exp[0] == "ok" and exp[1]["complete"] != [False] and exp[1]["lines_examined"] == n:
                pass
            flags = ["--input-format", "jsonl", "--discover", "--discover-lines", n, "--max-fields", m]
            if case.max_nodes != 4096:
                flags += ["--max-json-nodes", case.max_nodes]
            compared += 1
            with fails.case("case %d %r n=%d m=%d" % (i, case, n, m)):
                got = self.frun(case.data, flags)
                self.compare(got, exp, case.name)
                if i % 5 == 0:
                    self.same_everywhere(case.data, flags, got, label=case.name)
        fails.check(self, "discover cases")
        self.assertGreater(compared, 150)

    def test_the_corners_of_the_report(self):
        lines = [
            b'{"a":1,"b":2.5,"c":"s","d":true,"e":null,"f":{},"g":[1,2],"h":{"i":1,"j":{"k":[]}},"1e3":1e3,"n":-0,"m":1E3,"o":12345678901234567890}',
            b'{"a":2,"x.y":1,"p,q":2,"b\\\\s":3,"#h":4,"":5,"deep":{"a":{"b":{"c":{"d":{"e":{"f":{"g":1}}}}}}}}',
            b'{"a":"3","h":{"i":null,"z":1}}', b'{}', b'', b'   ',
            b'{"deep":{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":1}}}}}}}}}',
        ]
        data = b"\r\n".join(lines) + b"\r\n"
        for n, m in ((1000, 256), (2, 256), (1, 3), (1000, 5), (1000, 1), (3, 100)):
            exp = J.discover(data, n, m)
            got = self.frun(data, ["--input-format", "jsonl", "--discover", "--discover-lines", n, "--max-fields", m])
            self.compare(got, exp, "n=%d m=%d" % (n, m))
        got = self.frun(data, ["--input-format", "jsonl", "--discover"])
        d = discover_doc(got)
        paths = [f["path"] for f in d["fields"]]
        # a dot, comma or backslash in a key is escaped, as --fields takes it; a key that begins with # is not; an empty key is not listed
        for p in ("x\\.y", "p\\,q", "b\\\\s", "#h"):
            self.assertIn(p, paths, got)
        self.assertGreaterEqual(d["undeclarable"], 1, got)
        for must_not in ("", "e.x", "g.0", "h", "f"):
            if must_not in ("f",):
                continue                    # an empty object is a path of kind object
            self.assertNotIn(must_not, paths, got)
        by = {f["path"]: f for f in d["fields"]}
        self.assertEqual(norm_kinds(by["f"]["kinds"]).get("object"), 1, got)
        self.assertEqual(norm_kinds(by["a"]["kinds"]), {"int": 2, "string": 1, "missing": 2}, got)
        self.assertEqual(norm_kinds(by["b"]["kinds"]).get("number"), 1)
        self.assertEqual(norm_kinds(by["1e3"]["kinds"]).get("number"), 1)
        self.assertEqual(norm_kinds(by["n"]["kinds"]).get("int"), 1)
        self.assertEqual(norm_kinds(by["o"]["kinds"]).get("int"), 1)
        self.assertEqual(norm_kinds(by["d"]["kinds"]).get("bool"), 1)
        self.assertEqual(norm_kinds(by["e"]["kinds"]).get("null"), 1)
        self.assertEqual(d["arrays"], ["g", "h.j.k"], got)
        self.assertEqual(by["h.i"]["kinds"].get("int"), 1, got)
        self.assertEqual(by["h.i"]["kinds"].get("null"), 1, got)
        self.assertIn("deep.a.b.c.d.e.f.g", paths, got)

    def test_what_discover_writes_is_what_fields_takes(self):
        data = b'{"a":1,"x.y":"s","p,q":2.5,"b\\\\s":true,"#h":"z","k":{"m.n":{"o,p":7}}}\n{"a":2,"x.y":"t","p,q":1,"b\\\\s":false,"#h":"w","k":{"m.n":{"o,p":8}}}\n'
        got = self.frun(data, ["--input-format", "jsonl", "--discover"])
        paths = [f["path"] for f in discover_doc(got)["fields"]]
        self.assertEqual(len(paths), 6, got)
        again = self.frun(data, ["--input-format", "jsonl", "--fields", ",".join(paths)])
        self.assertEqual((again.status, again.data()["column_count"], again.data()["row_count"]), (0, 6, 2), again)
        rows = self.frun(data, ["--input-format", "jsonl", "--fields", ",".join(paths), "--select", tp.render_list(names_of(paths))])
        self.assertEqual(rows.data()["rows"], [["1", "s", "2.5", "true", "z", "7"], ["2", "t", "1", "false", "w", "8"]], rows)

    def test_complete_and_the_lines_examined(self):
        data = b"".join(b'{"a":%d}\n' % i for i in range(5)) + b"\n\n"
        for n, complete in ((6, True), (100, True), (1, False), (4, False)):
            got = self.frun(data, ["--input-format", "jsonl", "--discover", "--discover-lines", n])
            d = discover_doc(got)
            self.assertEqual((d["lines_examined"], d["complete"]), (min(n, 5), complete), (n, got))
        got = self.frun(data, ["--input-format", "jsonl", "--discover", "--discover-lines", 5])
        d = discover_doc(got)
        self.assertEqual(d["lines_examined"], 5)                   # `complete` at exactly N records is not specified (decision A8)
        got = self.frun(b"", ["--input-format", "jsonl", "--discover"])
        self.assertEqual(discover_doc(got), {"fields": [], "lines_examined": 0, "complete": True, "paths_truncated": False, "undeclarable": 0, "arrays": []}, got)
        # a malformed line past the N examined is not read
        got = self.frun(b'{"a":1}\n{"a":2}\n{"a":\n', ["--input-format", "jsonl", "--discover", "--discover-lines", 2])
        self.assertEqual((got.status, discover_doc(got)["lines_examined"]), (0, 2), got)
        got = self.frun(b'{"a":1}\n{"a":2}\n{"a":\n', ["--input-format", "jsonl", "--discover", "--discover-lines", 3])
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["line"]), (8, J.SYNTAX, 3), got)
        got = self.frun(b'{"a":1}\n[1]\n', ["--input-format", "jsonl", "--discover"])
        self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["kind"]), (8, J.NOT_OBJECT, "array"), got)
        got = self.frun(b'{"a":1,"a":2}\n', ["--input-format", "jsonl", "--discover"])
        self.assertEqual(got.status, 0, "duplicates are counted as occurrences, not refused")
        self.assertEqual(discover_doc(got)["fields"][0]["kinds"].get("int"), 2, got)

    def test_discover_does_not_need_fields_and_stdin_is_the_file(self):
        data = b'\xef\xbb\xbf{"a":1}\r\n\r\n{"a":"x","b":[1]}\n'
        a = self.frun(data, ["--input-format", "jsonl", "--discover"])
        self.same_everywhere(data, ["--input-format", "jsonl", "--discover"], a, label="discover")
        d = discover_doc(a)
        self.assertEqual([(f["path"], f["first_line"]) for f in d["fields"]], [("a", 1), ("b", 3)], a)
        self.assertEqual(d["lines_examined"], 2)


# ---- E: --explain ---------------------------------------------------------------------------------------------------------------------------

class Explain(Jsonl):
    FIELDS = ["id", "user.name", "a\\.b"]

    def explain(self, data, extra, stdin=False, operand=True):
        flags = ["--input-format", "jsonl", "--fields", ",".join(self.FIELDS)] + extra + ["--explain"]
        return self.srun(data, flags, operand) if stdin else self.frun(data, flags)

    def test_explain_names_the_format_the_fields_and_the_columns_for_a_file_and_for_stdin(self):
        data = b'{"id":1,"user":{"name":"n"},"a.b":"p"}\n'
        names = names_of(self.FIELDS)
        for how in ("file", "stdin", "bare"):
            for extra, cols in (([], names), (["--select", "id,user.name"], ["id", "user.name"]), (["--where", "id:int > 0", "--select", "#3"], ["a\\.b"]),
                                (["--group", "user.name", "--agg", "count"], ["user.name", "count"])):
                got = self.explain(data, extra, stdin=how != "file", operand=how == "stdin")
                self.assertEqual(validate(got), [], (how, extra, got))
                self.assertEqual(got.status, 0, got)
                e = got.data()["explain"]
                # TODO(member names): the lead's draft names source.format, source.fields and output.columns; the rest is checked by its words
                self.assertEqual(e["source"]["format"], "jsonl", got)
                self.assertEqual(e["source"]["fields"], names, got)
                self.assertEqual(e["output"]["columns"], cols, (how, extra, got))
                self.assertEqual(e["source"]["kind"], "file" if how == "file" else "stdin", got)
                text = json.dumps(e)
                for word in ("refuse", "4096"):
                    self.assertIn(word, text, "the defaults of --absent and --max-json-nodes are explained")
                if how != "file":
                    # N14: the header is the declaration, so a pipe's columns are resolved
                    self.assertEqual(e["source"].get("columns"), len(names), got)
                    for item in e["plan"].get("select", []):
                        self.assertIsNotNone(item["position"], got)

    def test_the_flags_the_read_was_given_are_explained(self):
        got = self.explain(b"", ["--absent", "empty", "--max-json-nodes", "77", "--threads", "4", "--chunk-bytes", "100", "--parallel-min-bytes", "0"])
        text = json.dumps(got.data()["explain"])
        for word in ("empty", "77"):
            self.assertIn(word, text, got)
        # a file is read by ranges (the work says threads), a pipe on one core
        self.assertEqual(got.data()["explain"]["work"]["threads"]["requested"], 4, got)
        got = self.explain(b"", ["--threads", "4", "--select", "id"], stdin=True)
        self.assertEqual(got.data()["explain"]["work"]["threads"]["effective"], 1, got)

    def test_explain_reads_nothing_and_refuses_what_the_run_refuses_before_reading(self):
        garbage = b"\xff\xfe not json at all \x00\n{{{\n"
        got = self.explain(garbage, ["--select", "id"])
        self.assertEqual((got.status, validate(got)), (0, []), got)
        for flags in (["--input-format", "jsonl", "--select", "id"], ["--input-format", "jsonl", "--fields", "a,a"], ["--input-format", "jsonl", "--fields", "a", "--select", "zz"]):
            run_ = self.frun(garbage, flags)
            exp = self.frun(garbage, flags + ["--explain"])
            self.assertEqual(exp.first_rule(), run_.first_rule(), (flags, exp, run_))
            self.assertEqual(exp.status, run_.status)


# ---- F: limits and paging --------------------------------------------------------------------------------------------------------------------

class Limits(Jsonl):
    def test_a_line_over_max_line_bytes(self):
        base = ["--input-format", "jsonl", "--fields", "id,name"]
        line = b'{"id":1,"name":"' + b"x" * 300 + b'"}\n'
        data = J.rec(0) + b"\n" + line + J.rec(2) + b"\n"
        got = self.frun(data, base + ["--max-line-bytes", "200"])
        self.assertEqual(validate(got), [], got)
        self.assertEqual((got.status, got.first_rule()), (8, J.LONG), got)
        d = got.error()["detail"]
        self.assertEqual((d["first_line"], d["longest"], d["limit"], d["path"]), (2, len(line) - 1, 200, "t.jsonl"), got)
        want = self.same_everywhere(data, base + ["--max-line-bytes", "200"], got)
        self.assertEqual(want[0], 8)
        rep = got.error()["repair"]
        self.assertEqual(rep["kind"], "retry", got)
        again = run_argv(rep["argv"], cwd=str(self.s.dir))
        self.assertEqual((again.status, again.data()["row_count"]), (0, 3), again)
        # the limit is on the line, not on the file; a CR and a BOM are counted, as the CSV reader counts them
        self.assertEqual(self.frun(J.rec(1) + b"\r\n", base + ["--max-line-bytes", str(len(J.rec(1)) + 1)]).status, 0)
        self.assertEqual(self.frun(J.rec(1) + b"\r\n", base + ["--max-line-bytes", str(len(J.rec(1)))]).first_rule(), J.LONG)
        # a line past the limit that is also malformed: the limit first (the reader's)
        got = self.frun(b'{"id":' + b"9" * 300 + b"\n", base + ["--max-line-bytes", "100"])
        self.assertEqual(got.first_rule(), J.LONG, got)

    def test_max_rows(self):
        names = ["id", "name"]
        rows = [[str(i), "n%d" % i] for i in range(1, 11)]
        data = b"".join(J.rec(i, "n%d" % i) + b"\n" for i in range(1, 11))
        base = ["--input-format", "jsonl", "--fields", "id,name"]
        self.s.write("m.csv", J.to_csv(names, rows))
        for k in (0, 1, 5, 9, 10, 11):
            for extra in ([], ["--select", "name"], ["--where", "id:int > 3", "--select", "id"]):
                got = self.frun(data, base + extra + ["--max-rows", k])
                want = run(*extra, "--max-rows", k, "m.csv", cwd=str(self.s.dir))
                self.assertEqual((got.status, got.stdout), (want.status, want.stdout), (k, extra, got, want))
                gotc = self.frun(data, base + extra + ["--max-rows", k, "--format", "csv"])
                wantc = run(*extra, "--max-rows", k, "--format", "csv", "m.csv", cwd=str(self.s.dir))
                self.assertEqual((gotc.status, gotc.stdout, gotc.stderr), (wantc.status, wantc.stdout, wantc.stderr), (k, extra))
        got = self.frun(data, base + ["--select", "id", "--max-rows", 3])
        self.assertEqual((got.data()["row_count"], got.data()["truncated"]), (3, True), got)
        gotc = self.frun(data, base + ["--select", "id", "--max-rows", 3, "--format", "csv"])
        self.assertEqual(gotc.status, 8, gotc)
        self.assertIn(b"limit.too-many-rows", gotc.stderr)
        # blank lines after the last record are not a record: not truncated
        got = self.frun(data + b"\n  \n\r\n", base + ["--select", "id", "--max-rows", 10])
        self.assertEqual((got.data()["row_count"], got.data()["truncated"]), (10, False), got)

    def test_the_read_stops_at_max_rows_and_a_page_stops_at_its_end(self):
        base = ["--input-format", "jsonl", "--fields", "id,name"]
        data = J.rec(1) + b"\n" + J.rec(2) + b"\n" + b'{"id":\n' + J.rec(4) + b"\n"
        got = self.frun(data, base + ["--select", "id", "--max-rows", 2])
        self.assertEqual((got.status, got.data()["truncated"], got.data()["rows"]), (0, True, [["1"], ["2"]]), "the 3rd record is not judged")
        got = self.frun(data, base + ["--select", "id", "--max-rows", 3])
        self.assertEqual((got.status, got.first_rule()), (8, J.SYNTAX), got)
        got = self.frun(data, base + ["--select", "id", "--limit", 2])
        self.assertEqual((got.status, got.data()["next"]), (0, {"from": 2}), "a streaming read stops when the page is full")
        # sorting and grouping read everything: the refusal is found
        for extra in (["--order-by", "id:int", "--select", "id"], ["--group", "id"], ["--report", "types"]):
            got = self.frun(data, base + extra + ["--limit", 2])
            self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["line"]), (8, J.SYNTAX, 3), (extra, got))

    def test_paging_pages_are_slices_of_the_whole(self):
        rng = random.Random(41)
        for trial in range(25):
            n = rng.randint(0, 40)
            lines = []
            for i in range(n):
                lines.append(J.rec(i, rng.choice(["a", "b", "c"])))
                if rng.random() < 0.2:
                    lines.append(rng.choice([b"", b"  "]))
            data = b"\n".join(lines) + (b"\n" if lines else b"")
            base = ["--input-format", "jsonl", "--fields", "id,name"]
            for plan in (["--select", "id"], ["--where", "name = a", "--select", "id,name"], ["--order-by", "-id:int", "--select", "id"]):
                whole = self.frun(data, base + plan + ["--limit", 100000]).data()["rows"]
                size = rng.randint(1, 7)
                seen, at, pages = [], 0, 0
                while True:
                    got = self.frun(data, base + plan + ["--limit", size, "--from", at])
                    d = got.data()
                    self.assertLessEqual(d["row_count"], size)
                    seen += d["rows"]
                    pages += 1
                    if d["next"] is None:
                        self.assertFalse(d["truncated"] and d["next"])
                        break
                    at = d["next"]["from"]
                    self.assertLess(pages, 200)
                self.assertEqual(seen, whole, (trial, plan, size))
                if "--where" in plan:
                    continue        # --from of a filter counts input records, not matches
                for start in (0, 1, len(whole), len(whole) + 3):
                    got = self.frun(data, base + plan + ["--limit", 5, "--from", start])
                    self.assertEqual(got.data()["rows"], whole[start:start + 5], (trial, plan, start))

    def test_a_record_before_from_is_read_like_the_rest(self):
        # N6: an error in a skipped record is the file's
        data = J.rec(1) + b"\n" + b'{"id":2}\n' + b"".join(J.rec(i) + b"\n" for i in range(3, 20))
        base = ["--input-format", "jsonl", "--fields", "id,name"]
        for plan in (["--select", "id", "--from", 10, "--limit", 3], ["--order-by", "id:int", "--from", 10, "--limit", 3], ["--group", "id", "--from", 10]):
            got = self.frun(data, base + plan)
            self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"]), (8, J.MISSING, 2), (plan, got))
        # (a --where is not evaluated on the records before --from, as for CSV: only the reading is)
        data = b'{"id":"x","name":"a"}\n' + b"".join(J.rec(i) + b"\n" for i in range(3, 20))
        got = self.frun(data, base + ["--where", "id:int > 0", "--select", "id", "--from", 10, "--limit", 3])
        self.assertEqual(got.status, 0, got)


# ---- J3 ----------------------------------------------------------------------------------------------------------------------------------------

J3_PLANS = [
    ("shape", []),
    ("csv", ["--select", "id,n", "--format", "csv"]),
    ("group", ["--group", "k", "--agg", "count,sum:n,max:n,min:n,distinct:name"]),
    ("page", ["--where", "n:int > 500", "--select", "id", "--limit", 7, "--from", 3]),
    ("order", ["--order-by", "-n:int,id:int", "--select", "id,n", "--format", "csv", "--limit", 50]),
    ("report", ["--report", "types"]),
]


class Parallel(Jsonl):
    """A file read by ranges, a pipe read on one core, and every thread count: the same bytes, and the first refusal in file order."""

    def files(self):
        """(label, case, size): small files for chunks of 1 to 64 bytes, 3000-line files for chunks of 1,000 and 64 KiB, 40,000-line ones for a few."""
        rng = random.Random(2026)
        out = []
        for size, lines in (("small", 300), ("big", 3000)):
            out.append(("plain %d" % lines, J.big_file(rng, lines, name="plain"), size))
            out.append(("crlf bom blanks %d" % lines, J.big_file(rng, lines, blanks=0.1, crlf=0.5, bom=True, escapes=0.5, name="odd"), size))
            for defect in J.BIG_DEFECTS:
                out.append(("%s %d" % (defect, lines), J.big_file(rng, lines, defect, name=defect), size))
            late = J.big_file(rng, lines, "syntax", at=lines - 100, name="late")
            out.append(("two defects: syntax late, missing early", J.Case("two", self.replace_line(late.data, 40, b'{"id":41,"k":"b","name":"early"}'), late.fields), size))
            out.append(("defect on the first line", J.big_file(rng, lines, "dup", at=0, name="first"), size))
            out.append(("defect on the last line", J.big_file(rng, lines, "not-scalar", at=lines - 1, name="last"), size))
            out.append(("no final newline", J.Case("nofinal", J.big_file(rng, lines).data.rstrip(b"\n\r"), list(J.BIG_FIELDS)), size))
        out.append(("plain 40000", J.big_file(rng, 40000, name="huge"), "huge"))
        out.append(("defect at 39000 of 40000", J.big_file(rng, 40000, "missing", at=39000, crlf=0.2, name="huge2"), "huge"))
        out.append(("two defects in 40000: dup at 30000, not-object at 12000",
                    J.Case("huge3", self.replace_line(J.big_file(rng, 40000, "dup", at=30000, name="h3").data, 12000, b"[1,2]"), list(J.BIG_FIELDS)), "huge"))
        return out

    @staticmethod
    def replace_line(data, index, new):
        lines = data.split(b"\n")
        k = 0
        for at, ln in enumerate(lines):
            if ln.strip(b" \t\r"):
                if k == index:
                    lines[at] = new
                    break
                k += 1
        return b"\n".join(lines)

    def test_j3_every_configuration_answers_the_same_bytes(self):
        fails = Failures()
        grid = {"small": ([(t, c) for t in (2, 3, 7, 16, 64) for c in (1, 7, 64)], [(3, 7), (64, 1)]),
                "big": ([(t, c) for t in (2, 3, 7, 16, 64) for c in (1000, 65536)], [(3, 1000), (64, 65536)]),
                "huge": ([(t, c) for t in (2, 7, 64) for c in (1000, 65536)], [(7, 1000)])}
        for label, case, size in self.files():
            for pname, plan in J3_PLANS:
                if size == "huge" and pname in ("page", "order", "report"):
                    continue
                flags = case.flags() + plan
                base = self.frun(case.data, flags)
                with fails.case("%s %s sequential against the oracle" % (label, pname)):
                    self.against_oracle(case, pname, plan, base)
                full, few = grid[size]
                for t, c in (full if pname in ("csv", "group", "shape") or ROUNDS > 1 else few):
                    with fails.case("%s %s threads %d chunk %d" % (label, pname, t, c)):
                        got = self.frun(case.data, flags + par(t, c))
                        self.assertEqual((got.status, strip_par(got.stdout), got.stderr), (base.status, strip_par(base.stdout), base.stderr))
                with fails.case("%s %s stdin" % (label, pname)):
                    for operand in (True, False):
                        s = self.srun(case.data, flags, operand)
                        self.assertEqual((s.status, strip_par(s.stdout), s.stderr), (base.status, strip_par(norm(base.stdout, "t.jsonl", operand)), norm(base.stderr, "t.jsonl", operand)), operand)
                    s = self.srun(case.data, flags + par(7, 64))
                    self.assertEqual((s.status, strip_par(s.stdout), s.stderr), (base.status, strip_par(norm(base.stdout, "t.jsonl", True)), norm(base.stderr, "t.jsonl", True)))
        fails.check(self, "configurations")

    def against_oracle(self, case, pname, plan, got):
        dec = case.oracle()
        names = names_of(case.fields)
        rows = dec[2] if dec[0] == "ok" else dec[3]
        if pname == "shape":
            if dec[0] == "ok":
                self.assertEqual(got.data(), {"headers": names, "column_count": 4, "row_count": len(rows), "truncated": False})
            else:
                self.assert_decode_refusal(got, dec[1], dec[2])
        elif pname == "group":
            p = {"where": [], "group": ["k"], "aggs": [("count", None), ("sum", "n"), ("max", "n"), ("min", "n"), ("distinct", "name")]}
            self.assert_expected(got, expected(case, p), pname)
        elif pname == "csv":
            self.assertEqual(got.status, 0 if dec[0] == "ok" else 8)
            if dec[0] == "ok":
                self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode()))), [["id", "n"]] + [[r[0], r[2]] for r in rows])
            else:
                self.assertIn(dec[1].encode(), got.stderr)
        else:
            # a page, a sort and a report: when the file is clean the answer is a success; when it is not, a streaming page that fills before the refusal succeeds,
            # and the others (which read everything) refuse with the oracle's rule
            if dec[0] == "ok":
                self.assertIn(got.status, (0, 8), got)          # 8: a cell the plan reads as an integer is not one (the `float` defect)
                if got.status == 8:
                    self.assertTrue(b"value.not-integer" in got.stderr + got.stdout, got)
            elif pname in ("order", "report"):
                self.assertEqual(got.status, 8)
                if pname == "report":
                    self.assertEqual(got.first_rule(), dec[1], got)
                else:
                    self.assertIn(dec[1].encode(), got.stderr)


# ---- J4 ----------------------------------------------------------------------------------------------------------------------------------------

def mutate(data, rng):
    data = bytearray(data)
    for _ in range(rng.choice([1, 1, 2, 4])):
        if not data:
            data.extend(b"{")
        at = rng.randrange(len(data) + 1)
        r = rng.random()
        if r < 0.25 and data:
            data[min(at, len(data) - 1)] ^= 1 << rng.randrange(8)
        elif r < 0.45:
            data[at:at] = bytes([rng.choice([0, 1, 10, 13, 34, 44, 58, 91, 92, 93, 123, 125, 0xC0, 0xE2, 0xED, 0xF4, 0xFF])])
        elif r < 0.6:
            del data[at:at + rng.randint(1, 6)]
        elif r < 0.7:
            del data[at:]
        elif r < 0.85:
            data[at:at] = rng.choice([b"\\u", b"\\ud800", b"\\", b'"', b",", b"{", b"\\u0000", b"1e999", b"-", b"\xef\xbb\xbf", b"\r\n", b"[[[[", b'":', b"null"])
        else:
            a = rng.randrange(len(data) + 1)
            data[at:at] = data[a:a + rng.randint(1, 30)]
    return bytes(data)


def random_bytes(rng):
    alphabet = b'{}[]",:\\ 0123456789truefalsn.eE+-\r\n\t\x00\xff\xc3\xa9\xe2\x82\xac'
    return bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))


class Fuzz(Jsonl):
    def test_j4_no_crash_only_catalogued_refusals_and_all_configurations_agree(self):
        rules = {r["rule"] for r in introspect()["rules"]}
        codes = {c["code"] for c in introspect()["exit_codes"]}
        rng = random.Random(4242)
        pool = [c for c in J.edge_cases() if len(c.data) < 5000]
        fails = Failures()
        plans = [([], "shape"), (["--select", "{f0}"], "json"), (["--group", "{f0}", "--agg", "count"], "json"), (["--select", "{f0}", "--format", "csv"], "csv"),
                 (["--where", "{f0}:int > 0", "--select", "{f0}"], "json")]
        runs = 0
        for i in range(260 * ROUNDS):
            r = rng.random()
            if r < 0.55:
                case = rng.choice(pool) if rng.random() < 0.4 else J.random_case(rng)
                data, fields, absent, nodes = mutate(case.data, rng), case.fields, case.absent, case.max_nodes
            elif r < 0.75:
                case = J.random_case(rng)
                data, fields, absent, nodes = case.data, case.fields, case.absent, case.max_nodes
            else:
                data, fields, absent, nodes = random_bytes(rng), rng.choice([["id", "name"], ["a"], ["a.b", "c"], ["a.0", "b\\.c"]]), rng.choice(["refuse", "empty"]), 4096
            f0 = tp.render_list([names_of(fields)[0]])
            for plan, kind in rng.sample(plans, 2):
                flags = ["--input-format", "jsonl", "--fields", ",".join(fields), "--absent", absent, "--max-json-nodes", nodes] + [x.replace("{f0}", f0) for x in plan]
                if kind == "csv" and "--select" not in plan:
                    continue
                with fails.case("input %d %r flags %r" % (i, data[:200], flags)):
                    outs = []
                    for how in ("file", "threads", "stdin"):
                        runs += 1
                        if how == "file":
                            got = self.frun(data, flags)
                        elif how == "threads":
                            got = self.frun(data, flags + par(4, 7))
                        else:
                            got = self.srun(data, flags)
                        self.well_formed(got, kind, rules, codes)
                        outs.append((got.status, strip_par(got.stdout), got.stderr) if how != "stdin" else (got.status, strip_par(norm(got.stdout, "-", True)), norm(got.stderr, "-", True)))
                    self.assertEqual(outs[0], outs[1], "threads 4 differ from the sequential read")
                    self.assertEqual((outs[0][0], norm(outs[0][1], "t.jsonl", True), norm(outs[0][2], "t.jsonl", True)), outs[2], "standard input differs from the file")
        fails.check(self, "fuzz inputs")
        self.assertGreater(runs, 500)

    def test_j4_mutated_files_against_the_oracle_on_the_shape(self):
        # the verdict, the rule, the line and the row of the first refusal are the parser's: Python's json is the oracle (offsets are not compared, N7)
        rng = random.Random(99)
        pool = [c for c in J.edge_cases() if len(c.data) < 5000]
        fails, n = Failures(), 0
        for i in range(300 * ROUNDS):
            base = rng.choice(pool) if rng.random() < 0.5 else J.random_case(rng)
            case = J.Case("m%d" % i, mutate(base.data, rng), base.fields, base.absent, base.max_nodes)
            with fails.case("mutated %r" % case):
                n += 1
                self.check_shape(case, case.name)
        fails.check(self, "mutated files")

    def well_formed(self, got, kind, rules, codes):
        self.assertNotIn(got.status, (-4, -6, -8, -11, 132, 134, 136, 139), "trapped: %r" % got)
        self.assertGreaterEqual(got.status, 0, got)
        self.assertIsNone(PANIC.search(got.stderr + got.stdout), (got.stdout[:300], got.stderr[:300]))
        self.assertIn(got.status, codes, got)
        if kind == "csv":
            if got.status != 0:
                m = re.match(rb"table: ([a-z]+\.[a-z0-9-]+): ", got.stderr)
                self.assertTrue(m and m.group(1).decode() in rules, got.stderr[:200])
            return
        self.assertEqual(validate(got), [], got)
        self.assertTrue(got.stdout, "an empty answer")
        doc = got.doc()
        if got.status != 0:
            self.assertIn(doc["error"]["rule"], rules, doc)
        else:
            self.assertTrue(doc["ok"])


# ---- the oracle, alone ------------------------------------------------------------------------------------------------------------------------

class OracleSelfTest(unittest.TestCase):
    """The oracle against hand-written answers (written from the specification) and against the generator's planted cells. No binary."""

    def dec(self, data, fields=("a",), **kw):
        return J.decode(data if isinstance(data, bytes) else data.encode("utf-8", "surrogatepass"), list(fields), **kw)

    def test_fields_grammar(self):
        self.assertEqual(J.fields_parse("a,b.c,d\\,e\\.f"), ("ok", ["a", "b.c", "d\\,e\\.f"], ["a", "b.c", "d,e\\.f"], [["a"], ["b", "c"], ["d,e.f"]]))
        self.assertEqual(J.fields_parse("#x,\\#x,a\\\\b"), ("ok", ["#x", "\\#x", "a\\\\b"], ["#x", "\\#x", "a\\\\b"], [["#x"], ["#x"], ["a\\b"]]))
        for text, item in BAD_FIELDS:
            self.assertEqual(J.fields_parse(text)[:2], (J.BAD_FIELDS, item), text)
        self.assertEqual(J.fields_parse(",".join("p%d" % i for i in range(4096)))[0], "ok")
        self.assertEqual(J.fields_parse(",".join("p%d" % i for i in range(4097)))[:2], (J.BAD_FIELDS, 4096))
        self.assertEqual(J.fields_parse("1.2.3.4.5.6.7.8")[0], "ok")
        self.assertEqual(J.escape_item(["a.b", "c,d", "e\\f", "#g"]), "a\\.b.c\\,d.e\\\\f.\\#g")
        self.assertEqual(J.fields_parse(J.escape_item(["a.b", "c,d", "e\\f", "#g"]))[3], [["a.b", "c,d", "e\\f", "#g"]])

    def test_cells(self):
        line = '{"s":"a\\nb\\u0000\\ud83d\\ude00é","i":123456789012345678901234567890,"f":0.12345678901234567,"e":1E3,"z":-0,"d":1.50,"t":true,"u":false}\n'
        got = self.dec(line, ["s", "i", "f", "e", "z", "d", "t", "u"])
        self.assertEqual(got[:2], ("ok", ["s", "i", "f", "e", "z", "d", "t", "u"]))
        self.assertEqual(got[2], [["a\nb\x00\U0001f600é", "123456789012345678901234567890", "0.12345678901234567", "1E3", "-0", "1.50", "true", "false"]])
        self.assertEqual(got[3]["line_of_row"], [1])

    def test_paths_and_absent_reasons(self):
        doc = '{"a":{"b":1,"n":null,"o":{},"s":"x"},"l":[{"k":7},9],"0":"zero","01":"zo","a.b":"dot","m":[],"nul":null}\n'
        def one(path, **kw):
            r = self.dec(doc, [path], **kw)
            return r[2][0][0] if r[0] == "ok" else (r[1], r[2].get("reason") or r[2].get("kind") or r[2].get("key"))
        self.assertEqual(one("a.b"), "1")
        self.assertEqual(one("a\\.b"), "dot")
        self.assertEqual(one("l.0.k"), "7")
        self.assertEqual(one("l.1"), "9")
        self.assertEqual(one("l.2"), (J.MISSING, "missing"))
        self.assertEqual(one("l.01"), (J.MISSING, "missing"))
        self.assertEqual(one("l.k"), (J.MISSING, "missing"))
        self.assertEqual(one("l.-1"), (J.MISSING, "missing"))
        self.assertEqual(one("l.99999999999999999999999999"), (J.MISSING, "missing"))
        self.assertEqual(one("0"), "zero")
        self.assertEqual(one("01"), "zo")
        self.assertEqual(one("a.x"), (J.MISSING, "missing"))
        self.assertEqual(one("a.n"), (J.MISSING, "null"))
        self.assertEqual(one("nul"), (J.MISSING, "null"))
        self.assertEqual(one("a.s.t"), (J.MISSING, "not-container"))
        self.assertEqual(one("a.o"), (J.NOT_SCALAR, "object"))
        self.assertEqual(one("m"), (J.NOT_SCALAR, "array"))
        self.assertEqual(one("l"), (J.NOT_SCALAR, "array"))
        self.assertEqual(one("a.x", absent="empty"), "")
        self.assertEqual(one("a.n", absent="empty"), "")
        self.assertEqual(one("a.o", absent="empty"), (J.NOT_SCALAR, "object"))
        self.assertEqual(one("a.s.t", absent="empty"), "")

    def test_duplicates_only_on_the_walked_keys(self):
        self.assertEqual(self.dec('{"a":1,"a":2}', ["a"])[:2], ("refuse", J.DUPLICATE))
        self.assertEqual(self.dec('{"a":1,"a":2}', ["a"])[2]["key"], "a")
        self.assertEqual(self.dec('{"a":{"b":1,"b":2}}', ["a.b"])[1], J.DUPLICATE)
        self.assertEqual(self.dec('{"a":{"b":1},"z":1,"z":2}', ["a.b"])[0], "ok")
        self.assertEqual(self.dec('{"a":{"b":1,"c":1,"c":2}}', ["a.b"])[0], "ok")
        self.assertEqual(self.dec('{"a":{"b":1},"u":{"p":1,"p":2}}', ["a.b"])[0], "ok")
        self.assertEqual(self.dec('{"a":1,"\\u0061":2}', ["a"])[1], J.DUPLICATE)
        self.assertEqual(self.dec('{"a":1,"a":2}', ["b"], absent="empty")[0], "ok")
        self.assertEqual(self.dec('{"a":1,"a":2,"b":null}', ["b", "a"])[1], J.MISSING)         # the first declared field decides
        self.assertEqual(self.dec('{"a":1,"a":2,"b":null}', ["a", "b"])[1], J.DUPLICATE)
        self.assertEqual(self.dec('{"l":[{"k":1,"k":2}]}', ["l.0.k"])[1], J.DUPLICATE)
        self.assertEqual(self.dec('{"l":[{"k":1,"k":2}]}', ["l.1"])[0:2], ("refuse", J.MISSING))

    def test_lines_blank_bom_crlf(self):
        got = self.dec(b'\xef\xbb\xbf{"a":"1"}\r\n\r\n \t\n{"a":"2"}  \r\n{"a":"3"}', ["a"])
        self.assertEqual((got[0], got[2], got[3]["line_of_row"], got[3]["records"]), ("ok", [["1"], ["2"], ["3"]], [1, 4, 5], 3))
        got = self.dec(b'{"a":"1"}\n\n{"a":\n', ["a"])
        self.assertEqual(got[:2], ("refuse", J.SYNTAX))
        self.assertEqual((got[2]["line"], got[2]["row"]), (3, 2))
        self.assertEqual(self.dec(b"", ["a"])[2:3], ([],))
        self.assertEqual(self.dec(b"\n  \n\t\r\n", ["a"])[2], [])
        self.assertEqual(self.dec(b'\xef\xbb\xbf\xef\xbb\xbf{"a":1}', ["a"])[1], J.SYNTAX)
        self.assertEqual(self.dec(b'{"a":1}\n\xef\xbb\xbf{"a":1}', ["a"])[1], J.SYNTAX)
        self.assertEqual(self.dec(b'\x0c\n', ["a"])[1], J.SYNTAX)                              # a form feed is not blank
        self.assertEqual(self.dec(b'{"a":"x"}\r', ["a"])[2], [["x"]])

    def test_syntax_and_what_python_gets_wrong(self):
        for bad in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', '{"a":01}', '{"a":1.}', '{"a":.5}', '{"a":+1}', "{'a':1}", '{"a":1,}', '{"a":1} x', '{"a":1}{}', '{"a":"\\x"}', '{"a":"\\u12"}',
                    '{"a":"\\ud800"}', '{"a":"\\udc00"}', '{"a":"\\ud83dx"}', '{"a":"\\ud83d\\u0041"}', '{"a":"\\ude00\\ud83d"}', '{"a":"x","\\ud800":1}', '{"a":"\t"}', '{"a":"\x01"}', '{"a":1 // c\n}',
                    '{"a":tru}', '{"a":True}', '{"a"}', '{a:1}', '{', '{"a":1', '{"a":"x}'):
            got = self.dec(bad, ["a"], absent="empty")
            self.assertEqual(got[:2], ("refuse", J.SYNTAX), bad)
        for good in ('{"a":1e999}', '{"a":-0}', '{"a":"\\ud83d\\ude00"}', '{"a":"\\/"}', '{"a":"\x7f"}', ' \t{ "a" : 1 }\t', '{"a":"\ufeff"}'):
            self.assertEqual(self.dec(good, ["a"])[0], "ok", good)
        for bad in (b'{"a":"\xff"}', b'{"a":"\xc0\x80"}', b'{"a":"\xed\xa0\x80"}', b'{"a":"\xf4\x90\x80\x80"}', b'{"a":"\xe2\x82"}', b'{"a":1}\x00', b'{"a":"\x00"}', b'\xff{"a":1}'):
            self.assertEqual(self.dec(bad, ["a"])[:2], ("refuse", J.SYNTAX), bad)
        self.assertEqual(self.dec(b'{"a":"\\u0000"}', ["a"])[2], [["\x00"]])

    def test_not_objects_and_looks_like(self):
        for text, kind in (("[1]", "array"), ('"s"', "string"), ("12", "number"), ("-0", "number"), ("1e3", "number"), ("true", "boolean"), ("null", "null"), ("[]", "array")):
            got = self.dec(text, ["a"])
            self.assertEqual((got[1], got[2]["kind"]), (J.NOT_OBJECT, kind), text)
        self.assertEqual(self.dec("[1,2]", ["a"])[2].get("looks_like"), "json-document")
        self.assertEqual(self.dec('{"a":1}\n[1]', ["a"])[2].get("looks_like"), None)
        self.assertEqual(self.dec("{\n", ["a"])[2].get("looks_like"), "json-document")
        self.assertEqual(self.dec("\n\n{\r\n", ["a"])[2]["looks_like"], "json-document")
        self.assertEqual(self.dec('{"a":', ["a"])[2].get("looks_like"), None)
        self.assertEqual(self.dec(" {", ["a"])[2].get("looks_like"), "json-document")
        self.assertEqual(self.dec(" [1", ["a"])[2].get("looks_like"), "json-document")          # a lone brace is judged trimmed (the implementation's reading)
        self.assertEqual(self.dec("]", ["a"])[2].get("looks_like"), None)
        self.assertEqual(self.dec("[1,", ["a"])[2].get("looks_like"), "json-document")

    def test_depth_and_nodes(self):
        for depth, ok in ((127, True), (128, True), (129, False), (130, False)):
            text = '{"a":1,"d":' + "[" * (depth - 1) + "]" * (depth - 1) + "}"
            self.assertEqual(self.dec(text, ["a"])[0] == "ok", ok, depth)
            text = "[" * depth + "]" * depth
            self.assertEqual(self.dec(text, ["a"])[1], J.NOT_OBJECT if ok else J.SYNTAX, depth)
        for depth in (1000, 100000):
            self.assertEqual(self.dec("[" * depth, ["a"])[:2], ("refuse", J.SYNTAX), depth)            # no RecursionError escapes
            self.assertEqual(self.dec('{"a":' * depth, ["a"])[:2], ("refuse", J.SYNTAX), depth)
        self.assertEqual(self.dec('{"a":' + "[" * 5000 + "]" * 5000 + "}", ["a"])[1], J.SYNTAX)
        self.assertEqual(self.dec('{"a":1}', ["a"], max_nodes=3)[0], "ok")
        self.assertEqual(self.dec('{"a":1}', ["a"], max_nodes=2)[1], J.NODES)
        self.assertEqual(self.dec('{"a":1}', ["a"], max_nodes=2)[2]["limit"], 2)
        self.assertEqual(J.count_nodes('{"a":[1,{"b":null}],"c":{}}'), 9)
        self.assertEqual(J.count_nodes("[]"), 1)
        self.assertEqual(J.count_nodes("[1,2]"), 3)
        self.assertEqual(J.count_nodes('{"a":1}'), 3)
        self.assertEqual(J.count_nodes('{"a":{"b":{"c":{}}}}'), 7)
        self.assertEqual(self.dec("[1,2,3,4,5,6]", ["a"], max_nodes=3)[1], J.NODES)          # the limit before the not-object (decision A3)
        got = self.dec('{"a":1,"a":2,"b":[1,2,3,4,5,6]}', ["a"], max_nodes=4)
        self.assertEqual(got[1], J.NODES)
        got = self.dec('{"a":NaN,"b":[1,2,3,4,5,6]}', ["a"], max_nodes=4)
        self.assertEqual((got[1], got[2]["ambiguous"]), (J.SYNTAX, True))                      # two defects: the parser's order (N12)

    def test_line_length_and_max_rows(self):
        line = b'{"a":"' + b"x" * 10 + b'"}'
        self.assertEqual(self.dec(line + b"\n", ["a"], max_line=len(line))[0], "ok")
        got = self.dec(line + b"\n", ["a"], max_line=len(line) - 1)
        self.assertEqual((got[1], got[2]), (J.LONG, {"first_line": 1, "longest": len(line), "limit": len(line) - 1}))
        self.assertEqual(self.dec(line + b"\r\n", ["a"], max_line=len(line))[1], J.LONG)           # a CR counts, as the CSV reader's
        self.assertEqual(self.dec(J.BOM + line, ["a"], max_line=len(line))[1], J.LONG)             # and a BOM
        got = self.dec(b'{"a":1}\n{"a":2}\n{"a":\n{"a":4}\n', ["a"], max_rows=2)
        self.assertEqual((got[0], got[2], got[3]["truncated"]), ("ok", [["1"], ["2"]], True))
        got = self.dec(b'{"a":1}\n{"a":2}\n\n  \n', ["a"], max_rows=2)
        self.assertEqual((got[0], got[3]["truncated"]), ("ok", False))
        self.assertEqual(self.dec(b'{"a":1}\n{"a":\n', ["a"], max_rows=2)[1], J.SYNTAX)
        self.assertEqual(self.dec(b'{"a":1}\n{"a":\n', ["a"], max_rows=0)[3]["truncated"], True)

    def test_the_planted_cells_are_what_the_oracle_reads(self):
        rng = random.Random(31)
        for _ in range(400):
            case, rows = J.planted_case(rng)
            got = case.oracle()
            self.assertEqual(got[0], "ok", case)
            self.assertEqual(got[2], rows, case)
            self.assertEqual(got[1], names_of(case.fields))

    def test_the_generator_reaches_every_rule_and_the_edge_files_are_valid_cases(self):
        rng = random.Random(1)
        seen = {}
        for i in range(1500):
            r = J.random_case(rng).oracle()
            key = "ok" if r[0] == "ok" else r[1]
            seen[key] = seen.get(key, 0) + 1
        for rule in ("ok", J.SYNTAX, J.MISSING, J.DUPLICATE, J.NOT_OBJECT, J.NOT_SCALAR, J.NODES):
            self.assertGreater(seen.get(rule, 0), 10, (rule, seen))
        edge = {}
        for case in J.edge_cases():
            r = case.oracle()
            key = "ok" if r[0] == "ok" else r[1]
            edge[key] = edge.get(key, 0) + 1
        for rule in ("ok", J.SYNTAX, J.MISSING, J.DUPLICATE, J.NOT_OBJECT, J.NOT_SCALAR, J.NODES, J.LONG):
            self.assertGreater(edge.get(rule, 0), 3, (rule, edge))
        self.assertGreater(len(J.edge_cases()), 300)

    def test_the_layout_puts_the_special_line_on_the_edge(self):
        for label in J.STRADDLERS:
            for edge in (512, 65536, 131072):
                for inside in (0, 1, 3, 20):
                    case = J.straddle_case(label, edge, inside)
                    at = case.data.find(J.STRADDLERS[label])
                    self.assertEqual(at, edge - inside, (label, edge, inside))
        self.assertEqual(len(J.filler(1000)), 1000)
        self.assertEqual(len(J.filler(7)), 7)
        self.assertEqual(len(J.filler(65536 * 2 - 1)), 65536 * 2 - 1)
        data = J.big_file(random.Random(3), 500, "dup", at=250, crlf=0.3, blanks=0.1)
        self.assertEqual(data.oracle()[:2], ("refuse", J.DUPLICATE))
        self.assertEqual(data.oracle()[2]["row"], 251)

    def test_discover(self):
        data = b'{"a":1,"b":{"c":"x","d":[1]},"e":2.5,"":3}\n\n{"a":"s","b":{"c":null},"#k":true,"x.y":{},"deep":{"a":{"b":{"c":{"d":{"e":{"f":{"g":9}}}}}}}}\n'
        got = J.discover(data, 1000, 256)
        self.assertEqual(got[0], "ok")
        d = got[1]
        self.assertEqual([(f["path"], f["kinds"], f["first_line"]) for f in d["fields"]], [
            ("a", {"int": 1, "string": 1}, 1), ("b.c", {"string": 1, "null": 1}, 1), ("b.d", {"array": 1, "missing": 1}, 1), ("e", {"number": 1, "missing": 1}, 1),
            ("#k", {"bool": 1, "missing": 1}, 3), ("x\\.y", {"object": 1, "missing": 1}, 3), ("deep.a.b.c.d.e.f.g", {"int": 1, "missing": 1}, 3)])
        self.assertEqual((d["lines_examined"], d["complete"], d["paths_truncated"], d["undeclarable"], d["arrays"]), (2, [True], False, 1, ["b.d"]))
        d = J.discover(data, 1, 256)[1]
        self.assertEqual((d["lines_examined"], d["complete"]), (1, [False]))
        d = J.discover(data, 2, 2)[1]
        self.assertEqual((len(d["fields"]), d["paths_truncated"], d["complete"]), (2, True, [True]))
        deep = b'{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":{"i":1}}}}}}}}}\n'
        d = J.discover(deep)[1]
        self.assertEqual(([f["path"] for f in d["fields"]], d["fields"][0]["kinds"], d["undeclarable"]), (["a.b.c.d.e.f.g.h"], {"object": 1}, 0))
        self.assertEqual(J.discover(b"[1]\n")[:2], ("refuse", J.NOT_OBJECT))
        self.assertEqual(J.discover(b'{"a":1}\n{"a":\n', 1)[0], "ok")
        self.assertEqual(J.discover(b'{"a":1}\n{"a":\n', 2)[1], J.SYNTAX)
        self.assertEqual(J.discover(b'{"a":1}\n', 5)[1]["complete"], [True])


if __name__ == "__main__":
    unittest.main()
