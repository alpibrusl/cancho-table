"""R3 (docs/readers.md sections 7, 8, 10): the rest of the gates of `--format jsonl`: gate 4 (fuzz), the surface (`introspect`, `skill`, `--explain`, the refusals of
flags) and the MCP server. test_jsonl_out.py holds the oracle gates; both are run by `python3 -W ignore -m unittest tests/conformance/test_jsonl_out_more.py`.

* **Fuzz** random and mutated bytes (CSV and JSON lines) x random plans, `--format jsonl`: no trap, a refusal is one catalogued rule on standard error, every line
  of standard output is one valid object written the one way, a file, `--threads` with tiny chunks and standard input agree, and the rows are the rows of the
  tool's own `--format csv` of the plan (a refusal before a row for names that csv accepts excepted).
* **Surface** the flag table, the rules, the skill, `--explain`, `--discover`, `--query`, a bare FILE and `ndjson` for `--format`.
* **Mcp** the server offers `jsonl` and answers a call with it as text only, byte for byte what the CLI writes.
"""

import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jsonl_out_ref as R
from harness import BIN, ROOT, Scratch, TRAPS, binary, introspect, run
from test_jsonl_out import PAR, Failures, Gated, ROUNDS, agg_tail, name_problems, rule_of

PANIC = re.compile(rb"panic|Traceback|SIGSEGV|segmentation|stack overflow|RUST_BACKTRACE|assertion failed|core dumped|Illegal instruction", re.I)


# ---- gate 4: fuzz --------------------------------------------------------------------------------------------------------------------------------

def mutate(data, rng):
    data = bytearray(data)
    for _ in range(rng.choice([1, 1, 2, 4, 8])):
        if not data:
            data.extend(b"a,b\n")
        at = rng.randrange(len(data) + 1)
        r = rng.random()
        if r < 0.2 and data:
            data[min(at, len(data) - 1)] ^= 1 << rng.randrange(8)
        elif r < 0.4:
            data[at:at] = bytes([rng.choice([0, 1, 10, 13, 34, 44, 92, 127, 0x80, 0xC0, 0xC3, 0xE2, 0xED, 0xF4, 0xFF])])
        elif r < 0.5:
            del data[at:at + rng.randint(1, 8)]
        elif r < 0.58:
            del data[at:]
        elif r < 0.7:
            data[at:at] = rng.choice([b'"', b'""', b'\\', b"\x00" * rng.randint(1, 40), b"\r\n", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xc0\xaf", b'"\n"', b",,,", b"\\u0000", b"\xff" * 5])
        elif r < 0.8:
            a = rng.randrange(len(data) + 1)
            data[at:at] = data[a:a + rng.randint(1, 60)]
        else:
            data[at:at] = b"x" * rng.randint(100, 3000)
    return bytes(data)


def random_bytes(rng):
    alphabet = b'a,b\n"\r\x00\xff \xc3\xa9\xe2\x82\xac1-.\t\\:'
    return bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 160)))


CSV_PLANS = [
    ["--rows"], ["--select", "#1"], ["--select", "#1,#2"], ["--select", "#2,#1"], ["--select", "#1,#1"], ["--where", "#1 != zzz"], ["--where", "#1:int > 0", "--select", "#1"],
    ["--where", "#2 contains a", "--select", "#2"], ["--group", "#1"], ["--group", "#1", "--agg", "count,distinct:#2"], ["--agg", "sum:#1,min:#1"], ["--order-by", "#1"],
    ["--order-by", "-#1:int", "--top", "3"], ["--group", "#1:float", "--agg", "mean:#1:float"], ["--select", "#1", "--limit", "3", "--from", "1"],
    ["--group", "#2:dec(2)", "--agg", "sum:#1:dec(2),count"], ["--agg", "count,max:#1"],
]
JSONL_PLANS = [["--rows"], ["--select", "a"], ["--select", "b,a"], ["--select", "a,a"], ["--where", "a != zzz", "--select", "b"], ["--group", "a", "--agg", "count"], ["--order-by", "a"],
               ["--agg", "count,distinct:b"]]


def fuzz_input(rng, pool):
    r = rng.random()
    if r < 0.18:
        return random_bytes(rng), "csv"
    if r < 0.5:
        return mutate(rng.choice(pool), rng), "csv"
    if r < 0.58:
        return mutate(rng.choice(pool), rng)[:rng.randint(0, 400)] + rng.choice([b'"abc', b'"', b'1,"x\n', b""]), "csv"
    if r < 0.66:
        return b"a,b\n1," + b'"' + bytes(rng.choice(b'x"\n,\\\x00\xff\xc3') for _ in range(rng.randint(20000, 60000))) + b'"\n2,3\n', "csv"
    if r < 0.74:
        return b"a,b\n" + b"\x00" * rng.randint(1, 3000) + b",x\n" + b"1,\x00\x00\n", "csv"
    if r < 0.82:
        return mutate(rng.choice(pool), rng), "csv"
    # JSON lines input
    lines = []
    for _ in range(rng.randint(0, 12)):
        a = rng.choice(['"x"', '"\\u0000"', '"\\ud83d\\ude00"', "1.50", "-0", "true", "null", '"é"', '"a\\"b"', "[1]", "1e999", '"\\ud800"', '"\\n"'])
        b = rng.choice(['"y"', "2", '"\\u001f"', "false", "12345678901234567890", '""', "{}"])
        lines.append('{"a":%s,"b":%s}' % (a, b))
    return mutate(("\n".join(lines) + "\n").encode(), rng) if rng.random() < 0.6 else ("\n".join(lines) + "\n").encode(), "jsonl"


class Fuzz(Gated):
    def test_gate4_no_trap_only_catalogued_refusals_valid_lines_and_all_configurations_agree(self):
        rules = {r["rule"] for r in introspect()["rules"]}
        codes = {c["code"] for c in introspect()["exit_codes"]}
        rng = random.Random(31337)
        pool = [R.make_table(rng, rows=rng.choice([0, 1, 3, 8, 20])).data for _ in range(40)]
        fails = Failures()
        runs = ok_outputs = refusals = 0
        seen_rules = set()
        for i in range(340 * ROUNDS):
            data, kind = fuzz_input(rng, pool)
            if kind == "csv":
                plans = rng.sample(CSV_PLANS, 2)
                decl = []
                name = "t.csv"
            else:
                plans = rng.sample(JSONL_PLANS, 2)
                decl = ["--input-format", "jsonl", "--fields", "a,b", "--absent", rng.choice(["refuse", "empty"])]
                name = "t.jsonl"
            chunk = 7 if len(data) < 6000 else 4096
            par = ["--threads", 3, "--chunk-bytes", chunk, "--parallel-min-bytes", 1]
            for plan in plans:
                flags = decl + plan
                with fails.case("input %d %s %r flags %r" % (i, kind, data[:160], flags)):
                    outs = {}
                    for how in ("file", "threads", "stdin"):
                        runs += 1
                        if how == "file":
                            got = self.frun(data, flags, "jsonl", name)
                        elif how == "threads":
                            got = self.frun(data, flags, "jsonl", name, par)
                        else:
                            got = self.srun(data, flags, "jsonl")
                        self.well_formed(got, rules, codes)
                        outs[how] = got
                    for how in ("threads", "stdin"):
                        self.assertEqual((outs[how].status, outs[how].stdout, rule_of(outs[how].stderr)), (outs["file"].status, outs["file"].stdout, rule_of(outs["file"].stderr)), how)
                    got = outs["file"]
                    seen_rules.add(rule_of(got.stderr))
                    ok_outputs += got.status == 0
                    refusals += got.status != 0
                    self.agrees_with_csv(data, flags, plan, got, name, kind)
        fails.check(self, "fuzz cases")
        self.assertGreater(runs, 1500)
        self.assertGreater(ok_outputs, 100)
        self.assertGreater(refusals, 50)
        self.assertGreater(len(seen_rules), 5, seen_rules)

    def well_formed(self, got, rules, codes):
        self.assertNotIn(got.status, TRAPS, got.stderr[:200])
        self.assertGreaterEqual(got.status, 0)
        self.assertIn(got.status, codes)
        self.assertIsNone(PANIC.search(got.stderr), got.stderr[:300])
        if got.status == 0:
            self.assertEqual(got.stderr, b"")
        else:
            m = re.match(rb"table: ([a-z0-9]+\.[a-z0-9-]+): [^\n]*\n", got.stderr)
            self.assertTrue(m, got.stderr[:300])
            self.assertIn(m.group(1).decode(), rules, got.stderr[:300])
            self.assertEqual(got.status, {r["rule"]: r["exit"] for r in introspect()["rules"]}[m.group(1).decode()], got.stderr[:300])
        R.read_lines(got.stdout)       # every line of a success, and of the prefix of a refusal, is one valid object written the one way
        try:
            got.stdout.decode("utf-8")
        except UnicodeDecodeError:
            self.fail("standard output is not UTF-8")

    def agrees_with_csv(self, data, flags, plan, got, name, kind):
        c = self.frun(data, flags, "csv", name)
        self.assertNotIn(c.status, TRAPS)
        tail = agg_tail(flags)
        try:
            kind_, want = R.from_csv_out(c.stdout, tail)
        except (R.csv.Error, AssertionError):
            return                  # csv wrote something the csv module cannot read back: csv's own business
        if kind_ == "ok":
            self.assertEqual((got.status, rule_of(got.stderr)), (c.status, rule_of(c.stderr)), (got.stderr[:200], c.stderr[:200]))
            self.assertEqual(got.stdout, want)
        else:
            self.assertEqual(got.status, 2, got.stderr[:200])
            self.assertEqual(got.stdout, b"")
            self.assertEqual(rule_of(got.stderr), "output.not-representable" if kind_ == "not-representable" else "output.duplicate-column")


# ---- the surface --------------------------------------------------------------------------------------------------------------------------------

class Surface(Gated):
    def test_the_flag_table_offers_jsonl_for_format_and_still_ndjson_for_the_input(self):
        flags = {f["name"]: f for f in introspect()["flags"]}
        kind = flags["--format"]["kind"]
        self.assertTrue(kind.startswith("choice:"), kind)
        choices = kind[len("choice:"):].split("/")
        self.assertIn("jsonl", choices)
        self.assertNotIn("ndjson", choices)
        for old in ("json", "text", "csv"):
            self.assertIn(old, choices)
        self.assertEqual(len(set(choices)), len(choices))
        self.assertIn("ndjson", flags["--input-format"]["kind"])
        self.assertIn("jsonl", flags["--format"]["help"])

    def test_the_rules_are_listed_with_exit_2_and_no_repair(self):
        rules = {r["rule"]: r for r in introspect()["rules"]}
        for rule in ("output.duplicate-column", "output.not-representable"):
            self.assertIn(rule, rules)
            self.assertEqual(rules[rule]["exit"], 2, rule)
            self.assertEqual(rules[rule]["repairable"], "never", rule)
            self.assertTrue(rules[rule]["summary"], rule)
        self.assertEqual(sum(1 for r in introspect()["rules"] if r["rule"] == "output.duplicate-column"), 1)

    def test_the_skill_mentions_the_format(self):
        text = subprocess.run([binary(), "skill"], capture_output=True, check=True).stdout.decode()
        self.assertIn("--format jsonl", text)

    def test_ndjson_is_not_a_value_of_format(self):
        for spelling in ("ndjson", "JSONL", "json-lines", "jsonl ", "jsonlines", "Jsonl", ""):
            got = run("--rows", "--format", spelling, "-", stdin=b"a\n1\n")
            self.assertEqual(got.status, 2, spelling)
            self.assertEqual(got.first_rule(), "args.bad-value", (spelling, got.stdout[:200]))
            d = got.error()["detail"]
            self.assertEqual(d["flag"], "--format")
            self.assertIn("jsonl", d["expected"])
        self.s.write("t.csv", b"a\n1\n")
        got = run("--rows", "--format", "ndjson", "t.csv", cwd=str(self.s.dir))
        self.assertEqual((got.status, got.first_rule()), (2, "args.bad-value"))
        # the input format keeps both spellings
        got = run("--input-format", "ndjson", "--fields", "a", "--rows", "--format", "jsonl", "-", stdin=b'{"a":1}\n')
        self.assertEqual((got.status, got.stdout), (0, b'{"a":"1"}\n'))

    def test_a_file_alone_is_required_flag_as_for_csv(self):
        for fmt in ("csv", "jsonl"):
            got = self.frun(b"a\n1\n", [], fmt)
            self.assertEqual((got.status, got.stdout, rule_of(got.stderr)), (2, b"", "args.required-flag"), fmt)
        j = self.frun(b"a\n1\n", [], "jsonl")
        c = self.frun(b"a\n1\n", [], "csv")
        self.assertEqual(j.stderr.replace(b"jsonl", b"csv"), c.stderr)
        got = run("--format", "jsonl", "-", stdin=b"a\n1\n")
        self.assertEqual((got.status, rule_of(got.stderr)), (2, "args.required-flag"))
        got = run("--format", "jsonl", "--limit", "5", "-", stdin=b"a\n1\n")
        self.assertEqual((got.status, rule_of(got.stderr)), (2, "args.required-flag"))

    def test_discover_with_jsonl_is_a_conflict_like_csv_and_text(self):
        self.s.write("j.jsonl", b'{"a":1}\n')
        results = {}
        for fmt in ("csv", "text", "jsonl"):
            got = run("--input-format", "jsonl", "--discover", "--format", fmt, "j.jsonl", cwd=str(self.s.dir))
            results[fmt] = got
            self.assertEqual(got.status, 2, fmt)
            self.assertEqual(got.stdout, b"", fmt)
            self.assertRegex(got.stderr, rb"^table: args\.conflict: ", fmt)
        self.assertEqual(results["jsonl"].stderr.splitlines()[0], results["csv"].stderr.splitlines()[0])

    def test_explain_says_jsonl_and_the_columns(self):
        self.s.write("t.csv", b"a,b,c\n1,2,3\n")
        for flags, cols in ((["--rows"], ["a", "b", "c"]), (["--select", "c,a"], ["c", "a"]), (["--group", "a", "--agg", "count,sum:b"], ["a", "count", "sum:b"]),
                            (["--order-by", "b", "--select", "b"], ["b"])):
            got = run("--explain", *flags, "--format", "jsonl", "t.csv", cwd=str(self.s.dir))
            self.assertEqual(got.status, 0, got.stdout[:300])
            out = got.data()["explain"]["output"]
            self.assertEqual(out["format"], "jsonl", flags)
            c = run("--explain", *flags, "--format", "csv", "t.csv", cwd=str(self.s.dir))
            self.assertEqual(out["columns"], c.data()["explain"]["output"]["columns"], flags)
            if cols:
                self.assertEqual(out["columns"], cols)
            # the rest of the explanation is csv's
            a, b = got.data()["explain"], c.data()["explain"]
            a["output"]["format"] = b["output"]["format"]
            self.assertEqual(a, b)
        got = run("--explain", "--select", "a", "--format", "jsonl", "-", stdin=b"")
        self.assertEqual((got.status, got.data()["explain"]["output"]["format"], got.data()["explain"]["output"]["columns"]), (0, "jsonl", None))

    def test_explain_of_a_plan_the_run_refuses_before_a_row(self):
        self.s.write("t.csv", b"a,b,c\n1,2,3\n")
        got = run("--explain", "--select", "a,a", "--format", "jsonl", "t.csv", cwd=str(self.s.dir))
        # the plan the run refuses before a row is refused by --explain as the run refuses it: a sentence on standard error, as for csv, and nothing on standard output
        self.assertEqual((got.status, got.stdout), (2, b""))
        self.assertEqual(rule_of(got.stderr), "output.duplicate-column")
        got = run("--explain", "--select", "a,b", "--format", "jsonl", "t.csv", cwd=str(self.s.dir))
        self.assertEqual(got.status, 0)

    def test_a_plan_with_no_columns_names_the_format_it_was_asked_for(self):
        self.s.write("t.csv", b"a,b,c\n1,2,3\n")
        for fmt in ("jsonl", "csv"):
            got = run("--format", fmt, "t.csv", cwd=str(self.s.dir))
            self.assertEqual((got.status, rule_of(got.stderr)), (2, "args.required-flag"))
            self.assertIn("--format " + fmt, got.stderr.decode())

    def test_the_query_form_and_the_other_flags_work_with_jsonl_as_with_csv(self):
        self.s.write("t.csv", b"k,n\na,1\nb,22\nc,3\n")
        for flags in (["--query", "select k, n where k != 'a'"], ["--query", "select k from t.csv where n::int > 2 order by n desc limit 1"], ["--query", "select count(*) group by k"],
                      ["--select", "k", "--limit", "2", "--from", "1"], ["--order-by", "-n:int", "--top", "2", "--select", "k"], ["--group", "k", "--agg", "sum:n", "--sort", "-sum:n", "--top", "2"],
                      ["--delimiter", ";", "--select", "k,n"], ["--max-rows", "2", "--select", "k"], ["--max-line-bytes", "5", "--select", "k"]):
            j = run(*flags, "--format", "jsonl", "t.csv", cwd=str(self.s.dir))
            c = run(*flags, "--format", "csv", "t.csv", cwd=str(self.s.dir))
            self.assertEqual((j.status, rule_of(j.stderr) if j.status else None), (c.status, rule_of(c.stderr) if c.status else None), (flags, j.stderr[:200]))
            tail = 1 if flags[:2] == ["--query", "select count(*) group by k"] else agg_tail(flags)
            self.assertEqual(j.stdout, R.from_csv_out(c.stdout, tail)[1], flags)

    def test_the_json_and_text_formats_are_unchanged(self):
        self.s.write("t.csv", b"a,b\n1,2\n")
        j = run("--select", "a", "t.csv", cwd=str(self.s.dir))
        self.assertEqual(j.data()["rows"], [["1"]])
        t = run("--format", "text", "t.csv", cwd=str(self.s.dir))
        self.assertEqual(t.status, 0)
        c = run("--select", "a", "--format", "csv", "t.csv", cwd=str(self.s.dir))
        self.assertEqual(c.stdout, b"a\n1\n")

    def test_limits_that_apply_to_json_pages_do_not_apply_to_the_stream(self):
        # --max-bytes bounds a json page and --limit defaults to 1000 there; a stream has neither
        n = 5000
        data = b"a\n" + b"".join(b"%d\n" % i for i in range(n))
        got = self.frun(data, ["--select", "a", "--max-bytes", "100"], "jsonl")
        self.assertEqual((got.status, got.stdout.count(b"\n")), (0, n))
        got = self.frun(data, ["--select", "a", "--limit", "1000000"], "jsonl")
        self.assertEqual(got.stdout.count(b"\n"), n)
        got = self.frun(data, ["--select", "a", "--max-rows", "10"], "jsonl")
        c = self.frun(data, ["--select", "a", "--max-rows", "10"], "csv")
        self.assertEqual((got.status, got.stdout, rule_of(got.stderr) if got.status else None), (c.status, R.from_csv_out(c.stdout)[1], rule_of(c.stderr) if c.status else None))

    def test_the_max_state_limits_refuse_like_csv(self):
        data = b"k\n" + b"".join(b"key%d\n" % i for i in range(300))
        for flags in (["--group", "k", "--max-groups", "5"], ["--order-by", "k", "--max-sort-rows", "5"], ["--agg", "distinct:k", "--max-distinct", "5"], ["--group", "k", "--max-state-bytes", "100"]):
            j = self.frun(data, flags, "jsonl")
            c = self.frun(data, flags, "csv")
            self.assertEqual((j.status, rule_of(j.stderr)), (c.status, rule_of(c.stderr)), flags)
            self.assertEqual(j.stdout, b"", flags)
            self.assertNotEqual(j.status, 0)


# ---- the MCP server -------------------------------------------------------------------------------------------------------------------------

class Mcp(unittest.TestCase):
    """`format` is one of the tool's flags, so the server's schema offers jsonl; a call with it is the CLI's answer as text (not structured content)."""

    @classmethod
    def setUpClass(cls):
        try:
            import test_mcp as M
            import bench
            cls.M = M
            cls.dir = pathlib.Path(tempfile.mkdtemp(prefix="mcp-jl-"))
            root = cls.dir / "root"
            root.mkdir()
            bench.generate(root / "data.csv", 3000)
            (root / "quoted.csv").write_bytes(b'id,name,note\n1,"Smith, John","said ""hi"""\n2,"multi\nline",\n3,caf\xc3\xa9,"\xe2\x82\xac 5"\n4,,x\n')
            (root / "latin.csv").write_bytes(b"a,b\n\xff\xfe,2\n3,4\n")
            (root / "nums.csv").write_bytes(b"k,int,dec,flt\na,5,1.25,2.5\nb,-3,0.10,1e3\na,12,3.00,-0.5\nc,9223372036854775807,2.50,7\n")
            (root / "ragged.csv").write_bytes(b"a,b,c\n1,2,3\n4,5\n")
            (root / "dup.csv").write_bytes(b"a,a,b\n1,2,3\n")
            (root / "one.csv").write_bytes(b"a,b\n1,2\n")
            (root / "bad.csv").write_bytes(b"ok,\xff\n1,2\n")
            big = cls.dir / "big"
            big.mkdir()
            bench.generate(big / "big.csv", 30000)
            cls.root, cls.big = root, big
            M.server()
        except Exception as e:      # noqa: BLE001 - the MCP server cannot be built here (as test_mcp, this is skipped where it is not available)
            raise unittest.SkipTest("the MCP server is not available: %r" % (e,))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def listed(self):
        [r], _ = self.M.talk(self.root, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        [tool] = r["result"]["tools"]
        return tool

    def test_the_schema_offers_jsonl(self):
        tool = self.listed()
        prop = tool["inputSchema"]["properties"]["format"]
        flag = [f for f in introspect()["flags"] if f["name"] == "--format"][0]
        self.assertEqual(prop["enum"], flag["kind"][len("choice:"):].split("/"))
        self.assertIn("jsonl", prop["enum"])
        self.assertNotIn("ndjson", prop["enum"])
        self.assertEqual(prop["description"], flag["help"])

    def test_a_call_with_jsonl_is_the_cli_byte_for_byte_and_text_only(self):
        M = self.M
        calls = [
            {"file": "data.csv", "select": "id,status", "limit": 5},
            {"file": "data.csv", "select": "#1,#3", "limit": 3, "from": 10},
            {"file": "data.csv", "where": "status=404", "select": "id", "limit": 7},
            {"file": "data.csv", "where": "status!=200 and bytes:int>90000", "select": "id,bytes", "limit": 10},
            {"file": "data.csv", "where": "path contains /p/9", "select": "id,path", "limit": 6},
            {"file": "data.csv", "group": "status", "agg": "count,sum:bytes", "sort": "-count"},
            {"file": "data.csv", "group": "status", "agg": "min:bytes,max:bytes,distinct:path"},
            {"file": "data.csv", "group": "status,note", "top": 3, "sort": "-count"},
            {"file": "data.csv", "agg": "count,sum:bytes"},
            {"file": "data.csv", "order-by": "-bytes:int", "select": "id,bytes", "limit": 5},
            {"file": "data.csv", "order-by": "status,-id:int", "select": "id,status", "top": 8},
            {"file": "data.csv", "group": "path", "agg": "count", "sort": "-count", "top": 5, "threads": 3, "parallel-min-bytes": 0, "chunk-bytes": 4096},
            {"file": "data.csv", "select": "id,bytes", "threads": 4, "parallel-min-bytes": 0, "chunk-bytes": 8192, "limit": 9},
            {"file": "data.csv", "report": "types", "select": "id,status"},
            {"file": "quoted.csv", "select": "id,name,note"},
            {"file": "quoted.csv", "select": "name,note"},
            {"file": "quoted.csv", "where": "name contains John", "select": "id,note"},
            {"file": "quoted.csv", "group": "name"},
            {"file": "nums.csv", "select": "k,dec", "where": "dec:dec(2)>1.00"},
            {"file": "nums.csv", "group": "k", "agg": "sum:int,min:int,max:int,mean:dec"},
            {"file": "nums.csv", "group": "k", "agg": "sum:dec:dec(2),mean:flt:float,max:flt:float"},
            {"file": "latin.csv", "select": "a,b"},
            {"file": "ragged.csv", "select": "a"},
            {"file": "ragged.csv", "rows": True},
            {"file": "dup.csv", "select": "#1,#2"},
            {"file": "dup.csv", "rows": True},
            {"file": "nums.csv", "select": "k,k"},
            {"file": "nums.csv", "group": "k", "agg": "count,count"},
            {"file": "bad.csv", "select": "#2"},
            {"file": "bad.csv", "select": "ok"},
            {"file": "data.csv", "select": "nope"},
            {"file": "data.csv", "where": "note:int>1", "select": "id"},
            {"file": "data.csv", "limit": 5},
            {"file": "data.csv"},
            {"file": "one.csv", "rows": True},
            {"file": "one.csv", "select": "b"},
        ]
        lines = [M.call(dict(a, format="jsonl"), n) for n, a in enumerate(calls)]
        responses, status = M.talk(self.root, lines)
        self.assertEqual(status, 0)
        self.assertEqual(len(responses), len(calls))
        seen = {"zero": 0, "refused": 0, "rows": 0, "stderr": 0}
        for n, (a, r) in enumerate(zip(calls, responses)):
            with self.subTest(arguments=a):
                self.assertNotIn("error", r, r)
                result = r["result"]
                direct = M.cli(self.root, dict(a, format="jsonl"))
                self.assertNotIn(direct.status, TRAPS, direct)
                items = result["content"]
                self.assertEqual(items[0]["text"].encode(), direct.stdout.decode("utf-8", "replace").encode())
                self.assertEqual(items[0]["_meta"], {"stream": "stdout"})
                if direct.stderr:
                    seen["stderr"] += 1
                    self.assertEqual(len(items), 2)
                    self.assertEqual(items[1]["text"].encode(), direct.stderr)
                    self.assertEqual(items[1]["_meta"], {"stream": "stderr"})
                else:
                    self.assertEqual(len(items), 1)
                self.assertEqual(result["_meta"]["exit_code"], direct.status)
                self.assertEqual(result["isError"], direct.status != 0)
                self.assertNotIn("structuredContent", result)
                if direct.status == 0:
                    seen["zero"] += 1
                    seen["rows"] += bool(direct.stdout)
                    # a success is rows: every line is an object and the server did not alter a byte of a valid UTF-8 output
                    self.assertEqual(direct.stdout.decode("utf-8"), items[0]["text"])
                    R.read_lines(direct.stdout)
                else:
                    seen["refused"] += 1
        self.assertGreater(seen["zero"], 20)
        self.assertGreater(seen["rows"], 20)
        self.assertGreater(seen["refused"], 4)
        self.assertGreater(seen["stderr"], 4)

    def test_a_single_row_is_one_json_object_and_still_only_text(self):
        M = self.M
        direct = M.cli(self.root, {"file": "one.csv", "select": "b", "format": "jsonl"})
        self.assertEqual(direct.stdout, b'{"b":"2"}\n')
        r = M.one(self.root, {"file": "one.csv", "select": "b", "format": "jsonl"})["result"]
        self.assertEqual(r["content"][0]["text"], '{"b":"2"}\n')
        self.assertNotIn("structuredContent", r)
        self.assertFalse(r["isError"])
        # and the default format of the same call is still the document, structured
        r = M.one(self.root, {"file": "one.csv", "select": "b"})["result"]
        self.assertIn("structuredContent", r)

    def test_bytes_that_are_not_text_arrive_intact_as_jsonl(self):
        # csv of latin.csv loses the bytes (U+FFFD); jsonl writes {"b64"} and the text is exact
        M = self.M
        responses, status = M.talk(self.root, [M.call({"file": "latin.csv", "select": "a,b", "format": "jsonl"}, 1), M.call({"file": "latin.csv", "select": "a,b", "format": "csv"}, 2),
                                               {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
        self.assertEqual(status, 0)
        self.assertEqual(responses[0]["result"]["content"][0]["text"], '{"a":{"b64":"//4="},"b":"2"}\n{"a":"3","b":"4"}\n')
        self.assertEqual(responses[1]["result"]["content"][0]["text"], "a,b\n��,2\n3,4\n")
        self.assertEqual(responses[2]["result"], {})

    def test_a_cell_with_a_newline_is_one_line_of_the_text(self):
        M = self.M
        r = M.one(self.root, {"file": "quoted.csv", "select": "name", "format": "jsonl"})["result"]
        text = r["content"][0]["text"]
        self.assertEqual(text, '{"name":"Smith, John"}\n{"name":"multi\\nline"}\n{"name":"café"}\n{"name":""}\n')
        raw = subprocess.run([str(M.server()), "--root", str(self.root)], input=json.dumps(M.call({"file": "quoted.csv", "select": "name", "format": "jsonl"})).encode() + b"\n", capture_output=True).stdout
        self.assertEqual(raw.count(b"\n"), 1)         # the response is one line

    def test_refusals_are_the_cli_refusals_as_text(self):
        M = self.M
        r = M.one(self.root, {"file": "nums.csv", "select": "k,k", "format": "jsonl"})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(r["_meta"]["exit_code"], 2)
        self.assertEqual(r["content"][0]["text"], "")
        self.assertRegex(r["content"][1]["text"], r"^table: output\.duplicate-column: [^\n]+\n$")
        self.assertNotIn("structuredContent", r)
        r = M.one(self.root, {"file": "ragged.csv", "select": "a", "format": "jsonl"})["result"]
        self.assertEqual((r["isError"], r["_meta"]["exit_code"]), (True, 8))
        self.assertEqual(r["content"][0]["text"], '{"a":"1"}\n')
        self.assertIn("parse.csv-ragged-row", r["content"][1]["text"])

    def test_the_output_bound_applies_to_the_stream(self):
        M = self.M
        r = M.one(self.big, {"file": "big.csv", "rows": True, "format": "jsonl"})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.output-too-large")
        r = M.one(self.big, {"file": "big.csv", "rows": True, "format": "jsonl", "limit": 10})["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(r["content"][0]["text"].count("\n"), 10)
        for line in r["content"][0]["text"].splitlines():
            json.loads(line)

    def test_jsonl_needs_a_plan_as_csv_does(self):
        M = self.M
        j = M.one(self.root, {"file": "data.csv", "format": "jsonl"})["result"]
        c = M.one(self.root, {"file": "data.csv", "format": "csv"})["result"]
        self.assertEqual((j["isError"], j["_meta"]["exit_code"]), (c["isError"], c["_meta"]["exit_code"]))
        self.assertIn("args.required-flag", j["content"][-1]["text"])
        self.assertNotIn("structuredContent", j)

    def test_ndjson_is_refused_by_the_schema_or_the_tool(self):
        M = self.M
        r = M.one(self.root, {"file": "one.csv", "select": "b", "format": "ndjson"})
        result = r.get("result")
        if result is None:
            self.assertIn("error", r)         # refused by the server's validation of the arguments
        else:
            self.assertTrue(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"])["error"]["rule"], "args.bad-value")      # the tool's document, as text: a format was chosen
