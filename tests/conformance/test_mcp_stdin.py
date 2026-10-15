"""The MCP server's `stdin` property (docs/mcp.md section 12): a table given in the call as text, instead of `file`.

What is held here, each written before the code and run against `main`, where it fails:

* the property exists, exactly one of `file` and `stdin` is given (`mcp.input-conflict`, `mcp.input-missing`), an empty string is an input;
* the answer is the CLI's `table --root=R FLAGS -- -` with the same bytes on standard input, byte for byte (a corpus, and seeded random plans over seeded random
  tables), and for a success the same as `file` over the same bytes;
* the bound (`--max-input`) is exact, counted in decoded bytes, and the refusal starts no process;
* what is not text never reaches the tool (the line does not parse), what is text arrives byte for byte (a NUL, a CRLF, a byte order mark, an astral character);
* a tool that exits before it reads, or never reads, ends the call and not the server;
* seeded damage in the property is answered with a rule or ignored, no trap, and the server answers a ping after.

The server of the module is built here, once, and removed here: it does not share the cache of test_mcp.py.
"""

import json
import os
import pathlib
import random
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import time
import unittest

from harness import BIN, ROOT, introspect, run_argv, schema

sys.path.insert(0, str(ROOT / "scripts"))
import bench  # noqa: E402
import mcp  # noqa: E402

import jsonschema  # noqa: E402

BUILT = {}
STATE = {}
MIB = 1 << 20


def server(bin_dir=None):
    key = str(bin_dir or BIN.resolve())
    if key not in BUILT:
        out = pathlib.Path(tempfile.mkdtemp(prefix="mcpstdin-")) / "mcp"
        mcp.build(str(BIN), key, str(out))
        BUILT[key] = out
    return BUILT[key]


def tearDownModule():
    for p in BUILT.values():
        shutil.rmtree(p.parent, ignore_errors=True)
    for d in STATE.values():
        if isinstance(d, pathlib.Path):
            shutil.rmtree(d, ignore_errors=True)
    for f in FAKES.values():
        shutil.rmtree(f.dir, ignore_errors=True)


def root():
    if "root" not in STATE:
        d = pathlib.Path(tempfile.mkdtemp(prefix="mcpstdin-root-")).resolve()
        bench.generate(d / "data.csv", 3000)
        STATE["root"] = d
    return STATE["root"]


def talk(directory, lines, flags=(), timeout=180, bin_dir=None):
    data = b"".join((l if isinstance(l, bytes) else json.dumps(l).encode()) + b"\n" for l in lines)
    p = subprocess.run([str(server(bin_dir)), "--root", str(directory), *flags], input=data, capture_output=True, timeout=timeout)
    return [json.loads(x) for x in p.stdout.split(b"\n") if x], p.returncode


def call(arguments, n=1):
    return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": "table", "arguments": arguments}}


PING = {"jsonrpc": "2.0", "id": "ping", "method": "ping"}


def one(arguments, flags=(), directory=None, bin_dir=None):
    [r], status = talk(directory or root(), [call(arguments)], flags=flags, bin_dir=bin_dir)
    assert status == 0, status
    return r


def flag_argv(flags):
    out = []
    for k, v in flags.items():
        if v is True:
            out.append("--" + k)
        elif v is not False:
            out.append("--%s=%s" % (k, v))
    return out


def cli(flags, text, directory=None):
    """The CLI's answer to the same plan with the text on standard input: the command a `stdin` call stands for."""
    argv = [str(BIN / "table"), "--root=%s" % (directory or root()), *flag_argv(flags), "--", "-"]
    p = subprocess.run(argv, input=text.encode("utf-8"), capture_output=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def rule_of(r):
    return json.loads(r["result"]["content"][0]["text"])["error"]["rule"]


def table_text(rows):
    """A table of `rows` rows of 4 bytes each, after a 4-byte header."""
    return "a,b\n" + "1,2\n" * rows


def bench_text():
    return (root() / "data.csv").read_text()


# ---- a stand-in for the tool: it records what it is given, and behaves as its --select says --------------------------

FAKE_SCRIPT = """#!/bin/sh
# A stand-in for `table`: it records its arguments, and does what its --select value names.
D=$(dirname "$0")/..
mode=none
for a; do case "$a" in --select=*) mode="${a#--select=}" ;; esac; done
for a; do printf '%s\\0' "$a"; done > "$D/record"
echo started >> "$D/starts"
case "$mode" in
  early) echo '{"ok":false}'; exit 2 ;;
  never) echo $$ > "$D/pid"; sleep 30 ;;
  slow) sleep 1; cat > "$D/stdin"; echo '{}' ;;
  sizes) cat > "$D/stdin"; wc -c < "$D/stdin" | tr -d ' ' | sed 's/^/{"n":/; s/$/}/' ;;
  *) cat > "$D/stdin"; echo '{}' ;;
esac
"""


class Fake:
    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="mcpstdin-fake-")).resolve()
        self.root = self.dir / "root"
        self.root.mkdir()
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        (self.bin / "table").write_text(FAKE_SCRIPT)
        (self.bin / "table").chmod(0o755)

    def reset(self):
        for n in ("record", "stdin", "starts", "pid"):
            (self.dir / n).unlink(missing_ok=True)

    def call(self, arguments, flags=(), timeout=120):
        self.reset()
        return one(arguments, flags=flags, directory=self.root, bin_dir=self.bin)

    def argv(self):
        return [g.decode() for g in (self.dir / "record").read_bytes().split(b"\0")[:-1]]

    def started(self):
        return (self.dir / "starts").exists()

    def stdin(self):
        return (self.dir / "stdin").read_bytes()


FAKES = {}


def fake():
    if "f" not in FAKES:
        FAKES["f"] = Fake()
    return FAKES["f"]


# ---- the plans of the equivalence ---------------------------------------------------------------------------------

PLANS = [
    {},
    {"format": "text"},
    {"select": "id,status", "limit": 5},
    {"select": "bytes,id", "limit": 3, "from": 10},
    {"select": "id", "where": "status=404", "limit": 7},
    {"select": "id,bytes", "where": "status!=200 and bytes:int>90000", "limit": 10},
    {"select": "id,path", "where": "path contains /p/9", "limit": 6},
    {"group": "status"},
    {"group": "status", "agg": "count,sum:bytes", "sort": "-count"},
    {"group": "status", "agg": "min:bytes,max:bytes,distinct:path"},
    {"agg": "count,sum:bytes"},
    {"order-by": "-bytes:int", "select": "id,bytes", "limit": 5},
    {"select": "id,status", "format": "csv", "limit": 4},
    {"group": "status", "format": "csv", "agg": "sum:bytes"},
    {"select": "id", "threads": 4, "parallel-min-bytes": 0, "chunk-bytes": 4096, "limit": 9},
    {"select": "id", "max-rows": 10},
    {"query": "select id, status where status = 404 limit 5"},
    {"query": "select status, count(*), sum(bytes) group by status order by count(*) desc limit 3", "format": "csv"},
    {"rows": True, "limit": 3},
    {"explain": True, "group": "status", "agg": "count"},
    {"report": "types"},
    # refusals: the tool's own, with its rules
    {"select": "nope"},
    {"where": "nope=1", "select": "id"},
    {"where": "status ==", "select": "id"},
    {"group": "status", "agg": "median:bytes"},
    {"limit": 5},
    {"format": "csv"},
    {"select": "id", "limit": 99999999},
    {"select": "id", "delimiter": "ab"},
    {"query": "selct id"},
    {"query": "select id from stdin"},
    {"query": "select id from 'data.csv'"},
    {"query": "select id", "select": "id"},
]

TEXTS = {
    "bench": None,                       # the benchmark's table, 3,000 rows
    "quoted": 'id,name,note\n1,"Smith, John","said ""hi"""\n2,"multi\nline",\n3,café,"€ 5"\n4,,x\n',
    "crlf": "a,b\r\n1,2\r\n3,4\r\n",
    "no final newline": "a,b\n1,2\n3,4",
    "bom": "﻿a,b\n1,2\n",
    "empty": "",
    "header only": "a,b,c\n",
    "newlines only": "\n\n\n",
    "ragged": "a,b,c\n1,2,3\n4,5\n",
    "bad quote": 'a,b\n"x"y,1\n',
    "unterminated": 'a,b\n"x,1\n',
    "dup header": "a,a,b\n1,2,3\n",
    "unicode": "ñ,日本\n😀,\u0085\nU+10FFFF,\U0010ffff\n",
    "nul": "a,b\n1,x\u0000y\n",
    "tsv": "id\tname\tn\n1\tann\t10\n2\tbob\t-5\n",
    "numbers": "k,int,dec,flt\na,5,1.25,2.5\nb,-3,0.10,1e3\na,12,3.00,-0.5\nc,9223372036854775807,2.50,7\n",
}


class Surface(unittest.TestCase):
    """The property exists and the pair is checked."""

    def listed(self):
        [r], _ = talk(root(), [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        return r["result"]["tools"][0]

    def test_the_schema_offers_stdin_and_requires_neither_property(self):
        tool = self.listed()
        s = tool["inputSchema"]
        self.assertEqual(s["properties"]["stdin"]["type"], "string")
        self.assertNotIn("minLength", s["properties"]["stdin"])      # an empty string is an input
        self.assertEqual(s["required"], [])
        self.assertFalse(s["additionalProperties"])
        for word in ("file", "exactly one", "from"):
            self.assertIn(word, s["properties"]["stdin"]["description"])
        self.assertIn("stdin", s["properties"]["file"]["description"])
        self.assertNotIn("always a file", s["properties"]["file"]["description"])

    def test_both_neither_and_the_wrong_types(self):
        lines = [call({"file": "data.csv", "stdin": "a\n"}, 1), call({"file": "-", "stdin": "a\n"}, 2), call({}, 3), call({"select": "id"}, 4),
                 call({"stdin": 5}, 5), call({"stdin": None}, 6), call({"stdin": ["a"]}, 7), call({"stdin": True}, 8), call({"stdin": {"a": 1}}, 9),
                 call({"stdin": "a\n", "limit": "3"}, 10), PING]
        responses, status = talk(root(), lines)
        self.assertEqual(status, 0)
        for n in (0, 1):
            e = responses[n]["error"]
            self.assertEqual((e["code"], e["data"]["rule"], e["data"]["properties"]), (-32602, "mcp.input-conflict", ["file", "stdin"]))
            self.assertIn("exactly one", e["message"])
        for n in (2, 3):
            e = responses[n]["error"]
            self.assertEqual((e["code"], e["data"]["rule"], e["data"]["properties"]), (-32602, "mcp.input-missing", ["file", "stdin"]))
        for n in range(4, 9):
            e = responses[n]["error"]
            self.assertEqual((e["code"], e["data"]["rule"]), (-32602, "mcp.wrong-type"), responses[n])
            self.assertEqual(e["data"]["problems"][0]["property"], "stdin")
            self.assertEqual(e["data"]["problems"][0]["expected"], "a string")
        self.assertEqual(responses[9]["error"]["data"]["problems"], [{"property": "limit", "expected": "a non-negative integer", "got": "string"}])
        self.assertEqual(responses[10]["result"], {})

    def test_an_empty_string_is_an_input_and_file_dash_is_still_the_empty_input(self):
        direct = cli({}, "")
        for arguments in ({"stdin": ""}, {"file": "-"}):
            r = one(arguments)["result"]
            self.assertEqual((r["content"][0]["text"].encode(), r["_meta"]["exit_code"]), (direct[1], direct[0]))
            self.assertEqual(r["structuredContent"]["data"]["headers"], [])

    def test_the_argv_has_the_root_first_and_the_operand_dash(self):
        fb = fake()
        fb.call({"stdin": "a,b\n1,2\n", "select": "x", "limit": 3})
        root_dir = str(fb.root)
        self.assertEqual(fb.argv(), ["--root=%s" % root_dir, "--select=x", "--limit=3", "--", "-"])
        # and a call with a file is `main`'s, argv for argv: no dash, and its standard input is empty
        fb.call({"file": "a.csv", "select": "x"})
        self.assertEqual(fb.argv(), ["--root=%s" % root_dir, "--select=x", "--", "a.csv"])
        self.assertEqual(fb.stdin(), b"")

    def test_the_rules_are_listed_and_the_flag_is_described(self):
        p = subprocess.run([str(server()), "introspect"], capture_output=True, timeout=30)
        d = json.loads(p.stdout)
        rules = {r["rule"]: r["answer"] for r in d["rules"]}
        self.assertEqual(rules["mcp.input-conflict"], "jsonrpc -32602")
        self.assertEqual(rules["mcp.input-missing"], "jsonrpc -32602")
        self.assertEqual(rules["mcp.input-too-large"], "isError result")
        self.assertIn("--max-input", {f["name"] for f in d["flags"]})
        self.assertEqual({l["name"]: l["default"] for l in d["limits"]}["max-input"], MIB)
        self.assertIn("[--max-input N]", d["usage"])

    def test_the_flag_takes_a_positive_number(self):
        for bad in ("0", "-1", "x", "1.5", "", "9999999999999"):
            p = subprocess.run([str(server()), "--root", str(root()), "--max-input", bad], input=b"", capture_output=True, timeout=30)
            self.assertNotEqual(p.returncode, 0, bad)
            self.assertIn(b"--max-input", p.stderr)


class Join(unittest.TestCase):
    """`stdin` and `join` (docs/joins.md 3.1): the text is the probe side and a file under the root is the build side; `join` is a second file, not a second input text."""

    def test_the_text_is_the_probe_and_the_file_is_the_build(self):
        d = root()
        (d / "dim.csv").write_bytes(b"id,name\n1,ann\n2,bob\n2,bobby\n")
        text = "id,v\n2,x\n9,y\n1,z\n2,w\n"
        for plan in ({"join": "dim.csv", "on": "id=id", "join-prefix": "d_"}, {"join": "dim.csv", "on": "id=id", "join-prefix": "d_", "format": "csv"},
                     {"join": "dim.csv", "on": "id=id", "join-prefix": "d_", "select": "v,d_name", "limit": 2},
                     {"join": "dim.csv", "on": "id=id"}, {"join": "nope.csv", "on": "id=id"}, {"join": "-", "on": "id=id"}, {"join": "../x.csv", "on": "id=id"},
                     {"join": "dim.csv", "on": "id=id", "join-prefix": "d_", "explain": True}):
            with self.subTest(plan=plan):
                r = one({"stdin": text, **plan})
                code, out, err = cli(plan, text)
                self.assertEqual(r["result"]["content"][0]["text"].encode(), out.decode("utf-8", "replace").encode())
                self.assertEqual(r["result"]["_meta"]["exit_code"], code)
        r = one({"stdin": text, "join": "dim.csv", "on": "id=id", "join-prefix": "d_", "format": "csv"})
        self.assertEqual(r["result"]["content"][0]["text"], "id,v,d_id,d_name\n2,x,2,bob\n2,x,2,bobby\n1,z,1,ann\n2,w,2,bob\n2,w,2,bobby\n")

    def test_the_stdin_text_never_reaches_the_build_side(self):
        d = root()
        (d / "dim.csv").write_bytes(b"id,name\n1,ann\n")
        # the text names a build-looking table: it is still the probe, and the build is the file
        r = one({"stdin": "id,name\n1,FROM-STDIN\n", "join": "dim.csv", "on": "id=id", "join-prefix": "d_", "format": "csv"})
        self.assertEqual(r["result"]["content"][0]["text"], "id,name,d_id,d_name\n1,FROM-STDIN,1,ann\n")
        # a file and stdin together are still refused
        r = one({"file": "data.csv", "stdin": "a\n", "join": "dim.csv", "on": "id=id"})
        self.assertEqual(r["error"]["data"]["rule"], "mcp.input-conflict")


class Equivalence(unittest.TestCase):
    """The same bytes as `table -`, over MCP and over the CLI, for the same input."""

    def check(self, arguments, r, text, flags):
        code, out, err = cli(flags, text)
        self.assertNotIn(code, {132, 134, 136, 139}, (flags, code))
        result = r["result"]
        items = result["content"]
        self.assertEqual(items[0]["text"].encode(), out.decode("utf-8", "replace").encode(), arguments)
        if err:
            self.assertEqual(items[1]["text"].encode(), err.decode("utf-8", "replace").encode())
        else:
            self.assertEqual(len(items), 1)
        self.assertEqual(result["_meta"]["exit_code"], code)
        self.assertEqual(result["isError"], code != 0)
        if flags.get("format", "json") == "json":
            self.assertEqual(result["structuredContent"], json.loads(out))
        else:
            self.assertNotIn("structuredContent", result)
        return code, out

    def test_the_corpus(self):
        cases = [(plan, bench_text(), "bench") for plan in PLANS]
        for name, text in TEXTS.items():
            if name != "bench":
                for plan in ({}, {"select": "a"}, {"select": "id,name"}, {"format": "csv", "select": "a,b"}, {"group": "a"}, {"report": "types"},
                             {"rows": True, "limit": 2}, {"explain": True, "select": "a"}, {"query": "select a"}, {"threads": 4, "select": "a"}):
                    cases.append((plan, text, name))
        responses, status = talk(root(), [call({"stdin": t, **p}, n) for n, (p, t, _) in enumerate(cases)])
        self.assertEqual(status, 0)
        self.assertEqual(len(responses), len(cases))
        validator = jsonschema.Draft202012Validator(schema())
        zero = refusals = 0
        rules = set()
        for n, ((plan, text, name), r) in enumerate(zip(cases, responses)):
            with self.subTest(plan=plan, text=name):
                self.assertEqual(r["id"], n)
                self.assertNotIn("error", r, r)
                code, out = self.check({"stdin": "...", **plan}, r, text, plan)
                zero += code == 0
                refusals += code != 0
                if plan.get("format", "json") == "json":
                    doc = json.loads(out)
                    self.assertEqual(list(validator.iter_errors(doc)), [])
                    if doc.get("error"):
                        rules.add(doc["error"]["rule"])
        self.assertGreater(zero, 80)
        self.assertGreater(refusals, 30)
        self.assertTrue({"select.unknown-column", "where.syntax", "args.conflict", "args.bad-value", "agg.bad-spec", "args.required-flag",
                         "parse.csv-ragged-row", "parse.csv-bad-quote", "query.syntax"} <= rules, rules)

    def test_a_success_is_the_answer_for_the_same_bytes_as_a_file(self):
        # `stdin` and `file` over the same bytes: the same document where the tool does not name the source.
        d = root()
        for name, text in TEXTS.items():
            text = bench_text() if text is None else text
            (d / "same.csv").write_bytes(text.encode("utf-8"))
            for plan in ({}, {"select": "a"}, {"format": "csv", "select": "id"}, {"group": "a"}):
                with self.subTest(text=name, plan=plan):
                    [viaf, vias], status = talk(d, [call({"file": "same.csv", **plan}, 1), call({"stdin": text, **plan}, 2)])
                    self.assertEqual(status, 0)
                    if viaf["result"]["isError"]:
                        continue
                    self.assertEqual(vias["result"]["content"], viaf["result"]["content"])
                    self.assertEqual(vias["result"]["_meta"], viaf["result"]["_meta"])

    def test_seeded_random_plans_over_seeded_random_tables(self):
        rng = random.Random(20261013)
        cells = ["", "0", "-7", "42", "3.14", "1e3", "NaN", "x", "hello world", "a,b", 'say "hi"', "line\nbreak", "é", "日本", "😀", " pad ", "9223372036854775807",
                 "00012", "+5", "0x1F"]

        def cell(c):
            if any(ch in c for ch in ',"\n\r') or c != c.strip() or rng.random() < 0.1:
                return '"' + c.replace('"', '""') + '"'
            return c
        templates = [
            lambda: {}, lambda: {"report": "types"}, lambda: {"select": "c0,c2", "limit": rng.randrange(1, 9)},
            lambda: {"select": "c1", "where": "c0 = %s" % rng.choice(["0", "42", "x"]), "limit": 5},
            lambda: {"select": "c0", "where": "c1:int > %d" % rng.randrange(-5, 50)},
            lambda: {"group": "c%d" % rng.randrange(3), "agg": "count"},
            lambda: {"group": "c0", "agg": "count,distinct:c1", "sort": "-count", "top": rng.randrange(1, 5)},
            lambda: {"order-by": rng.choice(["c0", "-c1", "c2,c0"]), "select": "c0,c1", "limit": 6},
            lambda: {"select": "c0", "format": "csv"},
            lambda: {"query": "select c0, c1 where c2 = 'x' limit 3"},
            lambda: {"query": "select count(*) group by c0"},
            lambda: {"rows": True, "limit": 4},
            lambda: {"select": "c0", "threads": rng.choice([1, 2, 4]), "chunk-bytes": 4096, "parallel-min-bytes": 0},
            lambda: {"explain": True, "select": "c1"},
        ]
        cases = []
        for _ in range(300):
            width = rng.randrange(1, 5)
            rows = rng.randrange(0, 40)
            lines = [",".join("c%d" % i for i in range(width))]
            for _ in range(rows):
                n = width if rng.random() > 0.05 else rng.randrange(1, 6)      # an occasional ragged row
                lines.append(",".join(cell(rng.choice(cells)) for _ in range(n)))
            sep = rng.choice(["\n", "\r\n"])
            text = sep.join(lines) + rng.choice([sep, ""])
            cases.append((rng.choice(templates)(), text))
        responses, status = talk(root(), [call({"stdin": t, **p}, n) for n, (p, t) in enumerate(cases)])
        self.assertEqual(status, 0)
        zero = 0
        for n, ((plan, text), r) in enumerate(zip(cases, responses)):
            with self.subTest(n=n, plan=plan):
                self.assertNotIn("error", r, r)
                code, _ = self.check({"stdin": "...", **plan}, r, text, plan)
                zero += code == 0
        self.assertGreater(zero, 100)


class Bound(unittest.TestCase):
    """`--max-input`: exact, in decoded bytes, and the refusal starts no process."""

    def test_the_bound_is_exact_on_the_real_tool(self):
        n = 5000
        text = table_text(400)[:n].rsplit("\n", 1)[0] + "\n"
        size = len(text.encode())
        direct = cli({"select": "a", "limit": 2}, text)
        ok = one({"stdin": text, "select": "a", "limit": 2}, flags=["--max-input", str(size)])["result"]
        self.assertEqual((ok["isError"], ok["content"][0]["text"].encode()), (False, direct[1]))
        refused = one({"stdin": text, "select": "a", "limit": 2}, flags=["--max-input", str(size - 1)])["result"]
        self.assertTrue(refused["isError"])
        self.assertNotIn("structuredContent", refused)
        record = json.loads(refused["content"][0]["text"])
        e = record["error"]
        self.assertEqual((record["ok"], record["command"], e["rule"], e["code"]), (False, "mcp", "mcp.input-too-large", "PRECONDITION_FAILED"))
        self.assertEqual(e["detail"]["limit"], size - 1)
        self.assertEqual(e["repair"], {"kind": "none", "reason": e["hint"]})
        for word in ("header", "rows", "--max-input"):
            self.assertIn(word, e["hint"])
        self.assertEqual(refused["content"][0]["_meta"], {"stream": "server"})

    def test_it_counts_decoded_bytes_not_characters_and_not_escapes(self):
        fb = fake()
        text = "é" * 100 + "\n" * 5                         # 205 bytes, 105 characters, 10 JSON escapes
        self.assertEqual(len(text.encode()), 205)
        fb.call({"stdin": text}, flags=["--max-input", "205"])
        self.assertEqual(fb.stdin(), text.encode())
        r = fb.call({"stdin": text}, flags=["--max-input", "204"])["result"]
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.input-too-large")
        self.assertFalse(fb.started())

    def test_the_default_is_one_mebibyte(self):
        fb = fake()
        text = table_text(262143)
        self.assertEqual(len(text.encode()), MIB)
        r = fb.call({"stdin": text})["result"]
        self.assertFalse(r["isError"], r)
        self.assertEqual(fb.stdin(), text.encode())
        r = fb.call({"stdin": text + "\n"})["result"]
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.input-too-large")
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["detail"]["limit"], MIB)
        self.assertFalse(fb.started())
        # and the real tool reads a table of exactly that size
        r = one({"stdin": text, "select": "a", "limit": 1})["result"]
        self.assertFalse(r["isError"], r["content"][0]["text"][:200])

    def test_a_bound_raised_is_the_request_lines_to_decide(self):
        # The request line (4 MiB) bounds it from above whatever the flag says: a string of 2 MiB passes a bound of 8 MiB.
        fb = fake()
        text = "a" * (2 * MIB)
        r = fb.call({"stdin": text}, flags=["--max-input", str(8 * MIB)])["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(len(fb.stdin()), 2 * MIB)
        # a string whose line passes 4 MiB is the line's refusal, whatever the flag
        fb.reset()
        [r], _ = talk(fb.root, [call({"stdin": "a" * (4 * MIB)})], flags=["--max-input", str(8 * MIB)], bin_dir=fb.bin)
        self.assertEqual(r["error"]["data"]["rule"], "mcp.line-too-long")
        self.assertFalse(fb.started())

    def test_the_order_of_refusals(self):
        # a type fault, a conflict and a size are told in that order, and none starts the tool
        fb = fake()
        big = "a" * 100
        for arguments, rule in (({"stdin": big, "limit": "3"}, "mcp.wrong-type"), ({"stdin": big, "file": "x"}, "mcp.input-conflict"),
                                ({"stdin": big, "zzz": 1}, "mcp.unknown-argument")):
            r = fb.call(arguments, flags=["--max-input", "10"])
            self.assertEqual(r["error"]["data"]["rule"], rule)
        self.assertFalse(fb.started())


class NotText(unittest.TestCase):
    """A JSON string is Unicode: what is not text does not parse, so no tool starts; what is text arrives as it is."""

    PREFIX = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"stdin":"'
    SUFFIX = b'"}}}'

    def test_bytes_that_are_not_utf8_and_lone_surrogates_are_a_parse_error_and_start_nothing(self):
        fb = fake()
        bad = [b"\xff", b"a\xc0\xafb", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2\x82", b"\x80", b"\xf8\x88\x80\x80\x80",
               b"\\ud800", b"\\udc00", b"\\ud800\\u0041", b"\\udc00\\ud800", b"x\\ud83d", b"\\ud83d\\ud83d"]
        fb.reset()
        lines = [self.PREFIX + b + self.SUFFIX for b in bad] + [PING]
        responses, status = talk(fb.root, lines, bin_dir=fb.bin)
        self.assertEqual(status, 0)
        self.assertEqual(len(responses), len(bad) + 1)
        for b, r in zip(bad, responses):
            with self.subTest(bytes=b):
                self.assertEqual((r["error"]["code"], r["error"]["data"]["rule"], r["id"]), (-32700, "mcp.parse-error", None))
        self.assertEqual(responses[-1]["result"], {})
        self.assertFalse(fb.started())

    def test_text_arrives_byte_for_byte(self):
        fb = fake()
        texts = ["a,b\r\n1,2\r\n", "﻿a\n", "no newline", "\n\n\n", "x\u0000y\u0000", "\u0000", "tab\there", 'q"uote\\slash/', "\U0001f600 and \U0010ffff and �",
                 "é\u0085  ", "".join(chr(c) for c in range(1, 128)), "ñ" * 5000]
        for text in texts:
            with self.subTest(text=text[:20]):
                r = fb.call({"stdin": text})["result"]
                self.assertFalse(r["isError"], r)
                self.assertEqual(fb.stdin(), text.encode("utf-8"))
        # raw (unescaped) multi-byte text in the line is the same text
        fb.reset()
        raw = self.PREFIX + "é日本😀\U0010ffff".encode() + self.SUFFIX
        [r], _ = talk(fb.root, [raw], bin_dir=fb.bin)
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(fb.stdin(), "é日本😀\U0010ffff".encode())
        # the surrogate pair is one character
        fb.reset()
        [r], _ = talk(fb.root, [self.PREFIX + b"\\ud83d\\ude00" + self.SUFFIX], bin_dir=fb.bin)
        self.assertEqual(fb.stdin(), "😀".encode())

    def test_a_nul_in_a_property_is_still_refused_and_in_stdin_it_is_not(self):
        r = one({"file": "a\u0000b"})
        self.assertEqual(r["error"]["data"]["rule"], "mcp.nul-in-argument")
        fb = fake()
        r = fb.call({"stdin": "a\u0000b"})
        self.assertNotIn("error", r)
        self.assertEqual(fb.stdin(), b"a\x00b")


class TheChild(unittest.TestCase):
    """A tool that does not read, or reads late, or ends first, ends a call and never the server."""

    def test_a_tool_that_exits_before_reading_a_large_input_does_not_hang_or_kill_the_server(self):
        fb = fake()
        text = "x" * (900 * 1024)
        lines = [call({"stdin": text, "select": "early"}, n) for n in range(40)] + [PING]
        t0 = time.time()
        responses, status = talk(fb.root, lines, bin_dir=fb.bin)
        self.assertLess(time.time() - t0, 60)
        self.assertEqual(status, 0)
        self.assertEqual(len(responses), 41)
        for r in responses[:-1]:
            self.assertTrue(r["result"]["isError"])
            self.assertEqual(r["result"]["_meta"]["exit_code"], 2)
        self.assertEqual(responses[-1]["result"], {})

    def test_the_real_tool_refusing_a_flag_before_reading_a_large_input(self):
        text = table_text(240000)
        self.assertGreater(len(text), 900000)
        lines = [call({"stdin": text, "select": "a", "limit": 99999999}, n) for n in range(30)] + [call({"stdin": text, "select": "a", "limit": 1}, 99), PING]
        t0 = time.time()
        responses, status = talk(root(), lines)
        self.assertLess(time.time() - t0, 60)
        self.assertEqual(status, 0)
        for r in responses[:30]:
            self.assertEqual(json.loads(r["result"]["content"][0]["text"])["error"]["rule"], "args.bad-value")
        self.assertFalse(responses[30]["result"]["isError"])
        self.assertEqual(responses[31]["result"], {})

    def test_a_tool_that_never_reads_is_ended_by_the_deadline_and_killed(self):
        fb = fake()
        t0 = time.time()
        r = fb.call({"stdin": "x" * (900 * 1024), "select": "never"}, flags=["--timeout-ms", "400"])["result"]
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.timeout")
        pid = int((fb.dir / "pid").read_text())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_a_tool_that_reads_late_gets_all_of_it(self):
        fb = fake()
        text = "ab\n" * 300000
        r = fb.call({"stdin": text, "select": "slow"})["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(fb.stdin(), text.encode())

    def test_the_tools_input_is_the_text_and_never_the_servers_own_stream(self):
        # The lines that follow a call on the server's standard input are the server's, not the child's: a tool that reads to the end of its input
        # sees the text and no more, and the next request is answered.
        fb = fake()
        responses, status = talk(fb.root, [call({"stdin": "one\n", "select": "sizes"}, 1), PING, call({"stdin": "two\n", "select": "sizes"}, 3), PING], bin_dir=fb.bin)
        self.assertEqual(status, 0)
        self.assertEqual([r["id"] for r in responses], [1, "ping", 3, "ping"])
        self.assertEqual(json.loads(responses[0]["result"]["content"][0]["text"]), {"n": 4})
        self.assertEqual(json.loads(responses[2]["result"]["content"][0]["text"]), {"n": 4})

    def test_a_call_with_a_file_gives_the_tool_no_input_as_before(self):
        fb = fake()
        responses, _ = talk(fb.root, [call({"file": "a.csv", "select": "sizes"}, 1), PING], bin_dir=fb.bin)
        self.assertEqual(json.loads(responses[0]["result"]["content"][0]["text"]), {"n": 0})


class Query(unittest.TestCase):
    def test_a_query_without_a_source_reads_the_input_and_one_with_a_source_is_the_tools_conflict(self):
        text = "id,status\n1,200\n2,404\n"
        ok = one({"stdin": text, "query": "select id where status = 404"})["result"]
        self.assertFalse(ok["isError"])
        self.assertEqual(ok["structuredContent"]["data"]["rows"], [["2"]])
        for q in ("select id from stdin", "select id from 'data.csv'"):
            r = one({"stdin": text, "query": q})["result"]
            self.assertTrue(r["isError"])
            self.assertEqual(r["structuredContent"]["error"]["rule"], "args.conflict")
            code, out, _ = cli({"query": q}, text)
            self.assertEqual(r["content"][0]["text"].encode(), out)
            self.assertNotIn("3000", json.dumps(r))        # the file under the root was not read


class Fuzz(unittest.TestCase):
    """Seeded damage in a call with the property: every line is answered with a rule or ignored, no trap, a ping after."""

    def test_seeded_requests_with_a_damaged_stdin(self):
        rng = random.Random(20261013)
        seeds = [call({"stdin": "a,b\n1,2\n3,4\n", "select": "a", "limit": 2}), call({"stdin": 'x,y\n"q,1\n', "group": "x"}), call({"stdin": "", "file": "data.csv"}),
                 call({"stdin": "é\u0000\n" * 40, "format": "csv", "select": "é"}), call({"stdin": "a\n" * 2000, "agg": "count"})]
        junk = [b"\x00", b"\xff", b'"', b"\\", b"\\u0000", b"\\ud83d", b"\\ude00", b"\\udc00\\ud800", b"\xe2\x82", b"\xed\xa0\x80", b"\\u", b"\\x", b"\\n" * 50, b"\\\\"]
        lines = []
        for _ in range(400):
            kind = rng.randrange(7)
            base = json.dumps(rng.choice(seeds)).encode()
            if kind == 0:
                data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
            elif kind == 1:
                i = rng.randrange(len(base))
                data = base[:i] + rng.choice(junk) + base[i:]
            elif kind == 2:
                data = base[:rng.randrange(len(base))]
            elif kind == 3:
                i, j = sorted(rng.randrange(len(base)) for _ in range(2))
                data = base[:i] + base[j:]
            elif kind == 4:
                data = base.replace(b'"stdin": "', b'"stdin": "' + rng.choice(junk) * rng.randrange(1, 40))
            elif kind == 5:
                data = base.replace(b'"stdin": "', b'"stdin": ' + rng.choice([b"[", b"{", b"null", b"1e999", b"-0", b"true", b'"'])).replace(b'"}}', b'}}')
            else:
                data = base.replace(b'"stdin": ', b'"stdin": "' + b"\\u00e9" * rng.randrange(1, 4000) + b'", "x": ')
            lines.append(data.replace(b"\n", b" "))
        responses, status = talk(root(), [*lines, PING], timeout=600)
        self.assertEqual(status, 0)
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "ping", "result": {}})
        for r in responses:
            self.assertTrue(("result" in r) != ("error" in r), r)
            if "error" in r:
                self.assertTrue(r["error"]["data"]["rule"].startswith("mcp."), r)
            elif "content" in r["result"]:
                self.assertNotIn(r["result"]["_meta"]["exit_code"], {132, 134, 136, 139, -4, -6, -8, -11}, r)

    def test_a_flood_of_calls_is_served_in_order_and_the_server_stays_small(self):
        text = table_text(2000)
        lines = [call({"stdin": text, "select": "a", "limit": 1}, n) for n in range(300)]
        responses, status = talk(root(), lines)
        self.assertEqual(status, 0)
        self.assertEqual([r["id"] for r in responses], list(range(300)))
        self.assertTrue(all(not r["result"]["isError"] for r in responses))


if __name__ == "__main__":
    unittest.main()
