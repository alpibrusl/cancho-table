"""The MCP server (docs/mcp.md §6): `table` as an MCP tool.

The server is built with `scripts/mcp.py build`, its directory baked to this checkout's `build/`, and driven over
stdio as a client would: one request per line, one answer per line. First what the server promises about the tool
(its schema is `introspect`'s, a call's answer is the CLI's byte for byte, the root cannot be left), then the
limits (the output bound, the deadline), the protocol, hostile input, the server's own description and authority.
Some of these use a stand-in for the tool (a shell script in a directory of its own, built into a second server) to
reach what the real tool does not do on request: record its arguments, be killed by a signal, never finish.
"""

import json
import os
import pathlib
import random
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import jsonschema

from harness import BIN, ROOT, Scratch, introspect, run_argv, schema

sys.path.insert(0, str(ROOT / "scripts"))
import bench  # noqa: E402
import manifest  # noqa: E402
import mcp  # noqa: E402

BUILT = {}
FIXTURES = {}


def server(bin_dir=None):
    """The server binary, built once per directory for the module: by default with this checkout's `build/` baked in."""
    key = str(bin_dir or BIN.resolve())
    if key not in BUILT:
        out = pathlib.Path(tempfile.mkdtemp(prefix="mcp-")) / "mcp"
        mcp.build(str(BIN), key, str(out))
        BUILT[key] = out
    return BUILT[key]


def tearDownModule():
    for p in BUILT.values():
        shutil.rmtree(p.parent, ignore_errors=True)
    for d in FIXTURES.values():
        shutil.rmtree(d, ignore_errors=True)
    for f in FAKES.values():
        shutil.rmtree(f.dir, ignore_errors=True)


def talk(root, lines, flags=(), timeout=120, bin_dir=None):
    """Send `lines` (each a request object, or bytes as they are) and answer `(responses, exit status)`."""
    data = b"".join((l if isinstance(l, bytes) else json.dumps(l).encode()) + b"\n" for l in lines)
    p = subprocess.run([str(server(bin_dir)), "--root", str(root), *flags], input=data, capture_output=True, timeout=timeout)
    responses = [json.loads(x) for x in p.stdout.split(b"\n") if x]
    return responses, p.returncode


def call(arguments, n=1, name="table"):
    return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def one(root, arguments, **kw):
    [r], status = talk(root, [call(arguments)], **kw)
    assert status == 0, status
    return r


def argv_of(arguments):
    """The CLI's argv for a call's arguments, written here and not read from the server: a flag is `--name=value`
    (or `--name` for a true boolean), then `--` and the file."""
    flags = []
    for k, v in arguments.items():
        if k == "file":
            continue
        if v is True:
            flags.append("--" + k)
        elif v is not False:
            flags.append("--%s=%s" % (k, v))
    return flags + ["--", arguments["file"]]


def cli(root, arguments):
    return run_argv([str(BIN / "table"), "--root=%s" % root, *argv_of(arguments)])


def fixtures():
    """The files the corpus reads, made once: the benchmark's generator at 3,000 rows, and the shapes the reader has
    rules for."""
    if "dir" in FIXTURES:
        return FIXTURES["dir"]
    d = pathlib.Path(tempfile.mkdtemp(prefix="mcp-fx-"))
    FIXTURES["dir"] = d
    root = d / "root"
    root.mkdir()
    bench.generate(root / "data.csv", 3000)
    (root / "quoted.csv").write_bytes(
        b'id,name,note\n1,"Smith, John","said ""hi"""\n2,"multi\nline",\n3,caf\xc3\xa9,"\xe2\x82\xac 5"\n4,,x\n')
    (root / "data.tsv").write_bytes(b"id\tname\tn\n1\tann\t10\n2\tbob\t-5\n3\tcy\t7\n")
    (root / "semi.csv").write_bytes(b"id;name\n1;ann\n2;bob\n")
    (root / "nums.csv").write_bytes(b"k,int,dec,flt\na,5,1.25,2.5\nb,-3,0.10,1e3\na,12,3.00,-0.5\nc,9223372036854775807,2.50,7\n")
    (root / "ragged.csv").write_bytes(b"a,b,c\n1,2,3\n4,5\n")
    (root / "badquote.csv").write_bytes(b'a,b\n"x"y,1\n')
    (root / "unterminated.csv").write_bytes(b'a,b\n"x,1\n')
    (root / "empty.csv").write_bytes(b"")
    (root / "header.csv").write_bytes(b"a,b,c\n")
    (root / "dup.csv").write_bytes(b"a,a,b\n1,2,3\n")
    (root / "objhdr.csv").write_bytes(b"{}\n")
    (root / "latin.csv").write_bytes(b"a,b\n\xff\xfe,2\n3,4\n")
    (root / "with space.csv").write_bytes(b"x,y\n1,2\n")
    (root / 'q"uote;$(id).csv').write_bytes(b"x,y\n3,4\n")
    (root / "sub").mkdir()
    (root / "sub" / "in.csv").write_bytes(b"s,t\n1,2\n")
    secret = d / "secret"
    secret.mkdir()
    (secret / "outside.csv").write_bytes(b"leak,more\nTOPSECRET,1\n")
    os.symlink("data.csv", root / "link.csv")
    os.symlink(str(secret / "outside.csv"), root / "out.csv")
    os.symlink("../secret/outside.csv", root / "rel.csv")
    os.symlink("sub", root / "linkdir")
    os.symlink(str(secret), root / "secretdir")
    big = d / "big"
    big.mkdir()
    bench.generate(big / "big.csv", 400000)
    return d


def root():
    return fixtures() / "root"


def structured_text(result):
    return result["content"][0]["text"].encode()


# One call per shape of plan, over the files above: what the corpus compares with the CLI.
CORPUS = [
    {"file": "data.csv"},
    {"file": "data.csv", "format": "text"},
    {"file": "data.csv", "select": "id,status", "limit": 5},
    {"file": "data.csv", "select": "bytes,id", "limit": 3, "from": 10},
    {"file": "data.csv", "select": "#1,#3", "limit": 3},
    {"file": "data.csv", "select": "note", "limit": 4},
    {"file": "data.csv", "where": "status=404", "select": "id", "limit": 7},
    {"file": "data.csv", "where": "status!=200 and bytes:int>90000", "select": "id,bytes", "limit": 10},
    {"file": "data.csv", "where": "path contains /p/9", "select": "id,path", "limit": 6},
    {"file": "data.csv", "where": "status in (301, 500) and bytes:int<=500", "select": "id,status,bytes"},
    {"file": "data.csv", "where": "note='a,b 7'", "select": "id,note"},
    {"file": "data.csv", "where": "bytes:int>=0", "limit": 2},
    {"file": "data.csv", "limit": 2, "where": "id:int>2990"},
    {"file": "data.csv", "group": "status"},
    {"file": "data.csv", "group": "status", "agg": "count,sum:bytes", "sort": "-count"},
    {"file": "data.csv", "group": "status", "agg": "min:bytes,max:bytes,distinct:path"},
    {"file": "data.csv", "group": "status,note", "top": 3, "sort": "-count"},
    {"file": "data.csv", "group": "status", "agg": "sum:bytes", "where": "bytes:int>50000", "sort": "-sum:bytes", "top": 2},
    {"file": "data.csv", "agg": "count,sum:bytes"},
    {"file": "data.csv", "order-by": "-bytes:int", "select": "id,bytes", "limit": 5},
    {"file": "data.csv", "order-by": "status,-id:int", "select": "id,status", "top": 8},
    {"file": "data.csv", "select": "id,status", "format": "csv", "limit": 4},
    {"file": "data.csv", "group": "status", "format": "csv", "agg": "sum:bytes"},
    {"file": "data.csv", "select": "id", "where": "status=404", "format": "csv", "limit": 1000},
    {"file": "data.csv", "group": "path", "agg": "count", "sort": "-count", "top": 5, "threads": 3, "parallel-min-bytes": 0, "chunk-bytes": 4096},
    {"file": "data.csv", "select": "id,bytes", "threads": 4, "parallel-min-bytes": 0, "chunk-bytes": 8192, "limit": 9},
    {"file": "data.csv", "select": "id", "max-rows": 10},
    {"file": "data.csv", "select": "id,note", "max-bytes": 200},
    {"file": "quoted.csv"},
    {"file": "quoted.csv", "select": "id,name,note"},
    {"file": "quoted.csv", "where": "name contains John", "select": "id,note"},
    {"file": "quoted.csv", "select": "name,note", "format": "csv"},
    {"file": "quoted.csv", "group": "name"},
    {"file": "data.tsv", "delimiter": "tab"},
    {"file": "data.tsv", "delimiter": "tab", "select": "id,n", "where": "n:int>0"},
    {"file": "data.tsv", "delimiter": "tab", "group": "name", "agg": "sum:n", "sort": "-sum:n"},
    {"file": "semi.csv", "delimiter": ";", "select": "name"},
    {"file": "nums.csv", "select": "k,dec", "where": "dec:dec(2)>1.00"},
    {"file": "nums.csv", "select": "k,flt", "where": "flt:float>1.0"},
    {"file": "nums.csv", "group": "k", "agg": "sum:int,min:int,max:int"},
    {"file": "nums.csv", "group": "k", "agg": "sum:dec"},
    {"file": "empty.csv"},
    {"file": "header.csv"},
    {"file": "header.csv", "select": "a"},
    {"file": "sub/in.csv"},
    {"file": "with space.csv", "select": "y"},
    {"file": 'q"uote;$(id).csv', "select": "x"},
    {"file": "link.csv"},
    # the refusals, with their rules
    {"file": "ragged.csv"},
    {"file": "ragged.csv", "select": "a"},
    {"file": "badquote.csv", "select": "a"},
    {"file": "unterminated.csv", "select": "a"},
    {"file": "dup.csv", "select": "a"},
    {"file": "data.csv", "select": "nope"},
    {"file": "data.csv", "where": "nope=1", "select": "id"},
    {"file": "data.csv", "where": "status ==", "select": "id"},
    {"file": "data.csv", "where": "note:int>1", "select": "id"},
    {"file": "data.csv", "group": "status", "agg": "median:bytes"},
    {"file": "data.csv", "group": "status", "sort": "bogus"},
    {"file": "data.csv", "group": "path", "max-groups": 3},
    {"file": "data.csv", "order-by": "id", "select": "id", "max-sort-rows": 5},
    {"file": "data.csv", "limit": 5},
    {"file": "data.csv", "format": "csv"},
    {"file": "data.csv", "select": "id", "limit": 99999999},
    {"file": "data.csv", "threads": 65, "select": "id"},
    {"file": "data.csv", "select": "id", "delimiter": "ab"},
    {"file": "nope.csv"},
    {"file": "sub"},
    {"file": "data.csv/x"},
    {"file": "nums.csv", "select": "k", "where": "k:int>0"},
    {"file": "latin.csv", "select": "a,b"},
]


def raw_mode(arguments):
    """Is this call's answer on stdout in JSON (the default `--format`)?"""
    return arguments.get("format", "json") == "json"


class Differential(unittest.TestCase):
    """A call's answer is the CLI's, byte for byte, and the exit codes agree."""

    def test_corpus(self):
        lines = [call(a, n) for n, a in enumerate(CORPUS)]
        responses, status = talk(root(), lines)
        self.assertEqual(status, 0)
        self.assertEqual(len(responses), len(CORPUS))
        validator = jsonschema.Draft202012Validator(schema())
        seen_rules, seen_zero, seen_text, seen_stderr = set(), 0, 0, 0
        for n, (arguments, r) in enumerate(zip(CORPUS, responses)):
            with self.subTest(arguments=arguments):
                self.assertEqual(r["id"], n)
                self.assertNotIn("error", r, r)
                result = r["result"]
                direct = cli(root(), arguments)
                self.assertNotIn(direct.status, {132, 134, 136, 139}, direct)
                items = result["content"]
                self.assertEqual(items[0]["text"].encode(), direct.stdout.decode("utf-8", "replace").encode())    # byte for byte, bytes that are text
                self.assertEqual(items[0]["_meta"], {"stream": "stdout"})
                if direct.stderr:
                    seen_stderr += 1
                    self.assertEqual(len(items), 2)
                    self.assertEqual(items[1]["text"].encode(), direct.stderr)
                    self.assertEqual(items[1]["_meta"], {"stream": "stderr"})
                else:
                    self.assertEqual(len(items), 1)
                self.assertEqual(result["_meta"]["exit_code"], direct.status)
                self.assertEqual(result["isError"], direct.status != 0)
                if raw_mode(arguments):
                    doc = json.loads(direct.stdout)
                    self.assertEqual(result["structuredContent"], doc)
                    self.assertEqual(list(validator.iter_errors(doc)), [])
                    if doc.get("error"):
                        seen_rules.add(doc["error"]["rule"])
                        self.assertEqual(result["structuredContent"]["error"], doc["error"])
                        self.assertEqual(result["structuredContent"]["errors"][0]["repair"], doc["errors"][0]["repair"])
                else:
                    seen_text += 1
                    self.assertNotIn("structuredContent", result)
                seen_zero += direct.status == 0
        # The corpus reaches both kinds of answer and a spread of rules (a corpus that passes by running nothing is a failure).
        self.assertGreater(seen_zero, 30)
        self.assertGreater(seen_text, 3)
        self.assertGreater(seen_stderr, 0)
        for rule in ("parse.csv-ragged-row", "select.unknown-column", "where.syntax", "agg.bad-spec", "io.not-found", "parse.csv-bad-quote",
                     "limit.too-many-groups", "args.required-flag", "io.is-a-directory", "value.not-integer", "column.unknown"):
            self.assertIn(rule, seen_rules)

    def test_one_call_at_a_time_in_order(self):
        responses, _ = talk(root(), [call(CORPUS[n], 100 + n) for n in (0, 2, 13, 0)])
        self.assertEqual([r["id"] for r in responses], [100, 102, 113, 100])

    def test_a_refusal_keeps_its_rule_and_repair(self):
        direct = cli(root(), {"file": "data.csv", "select": "nope"})
        r = one(root(), {"file": "data.csv", "select": "nope"})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(r["_meta"]["exit_code"], direct.status)
        err = r["structuredContent"]["error"]
        self.assertEqual(err["rule"], "select.unknown-column")
        self.assertEqual(err, json.loads(direct.stdout)["error"])
        self.assertIn("hint", err)
        self.assertIn("repair", err)
        # A repair that is a `retry` is the same argv the tool offered.
        r = one(root(), {"file": "data.csv", "limit": 5})["result"]
        self.assertEqual(r["structuredContent"]["error"]["rule"], "args.required-flag")

    def test_standard_error_is_kept_beside_standard_output(self):
        r = one(root(), {"file": "data.csv", "where": "nope=1", "format": "csv"})["result"]
        self.assertEqual(r["content"][0]["text"], "")
        self.assertEqual(r["content"][1]["text"], "table: column.unknown: the plan names a column the header does not have\n")
        self.assertTrue(r["isError"])
        self.assertEqual(r["_meta"]["exit_code"], 3)

    def test_non_utf8_bytes_in_text_output_do_not_stop_the_server(self):
        # csv on a file whose bytes are not UTF-8: the answer is JSON text, so those bytes cannot be carried as they are
        # (docs/mcp.md §2); the json format carries them as base64 and loses nothing.
        responses, status = talk(root(), [call({"file": "latin.csv", "select": "a,b", "format": "csv"}, 1), call({"file": "latin.csv", "select": "a,b"}, 2),
                                          {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
        self.assertEqual(status, 0)
        self.assertEqual(responses[0]["result"]["content"][0]["text"], "a,b\n��,2\n3,4\n")
        self.assertEqual(responses[1]["result"]["structuredContent"]["data"]["rows"][0][0], {"b64": "//4="})
        self.assertEqual(responses[2]["result"], {})


class Schema(unittest.TestCase):
    """The input schema is `introspect`'s, and follows it."""

    def listed(self):
        [r], _ = talk(root(), [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        [tool] = r["result"]["tools"]
        return tool

    def test_tools_list_is_what_introspect_says(self):
        tool = self.listed()
        d = introspect()
        self.assertEqual(tool["name"], d["tool"])
        self.assertTrue(tool["description"].startswith(d["summary"]))
        s = tool["inputSchema"]
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(s["type"], "object")
        props = s["properties"]
        ceilings = {l["name"]: l["ceiling"] for l in d["limits"]}
        flags = [f for f in d["flags"] if f["role"] != "root"]
        # Every flag but the server's `--root`, by kind, with its help and default; and nothing else but the operand.
        self.assertEqual(set(props), {f["name"][2:] for f in flags} | {"file"})
        self.assertNotIn("root", props)
        kinds_seen = set()
        for f in flags:
            p = props[f["name"][2:]]
            self.assertEqual(p["description"], f["help"])
            if f["default"] is not None:
                self.assertEqual(p["default"], int(f["default"]) if f["kind"] == "nat" else f["default"])
            else:
                self.assertNotIn("default", p)
            kind = f["kind"].split(":")[0]
            kinds_seen.add(kind)
            if kind == "nat":
                self.assertEqual((p["type"], p["minimum"]), ("integer", 0))
                self.assertEqual(p.get("maximum"), ceilings.get(f["name"][2:]), f["name"])
            elif kind == "choice":
                self.assertEqual(p["enum"], f["kind"][len("choice:"):].split("/"))
                self.assertNotIn("type", p)
            elif kind == "text":
                self.assertEqual((p["type"], p["minLength"]), ("string", 1))
            else:
                self.fail("a flag kind this test does not know: %s" % f["kind"])
        self.assertEqual(kinds_seen, {"nat", "text", "choice"})
        # The operand: one FILE, required.
        [operand] = d["operands"]
        self.assertEqual((operand["name"], operand["min"], operand["max"]), ("FILE", 1, 1))
        self.assertEqual(props["file"], {"type": "string", "description": operand["help"]})
        self.assertEqual(s["required"], ["file"])
        # The strings the model has to write are documented from introspect's help, in the schema.
        for name in ("where", "select", "agg", "sort", "order-by", "group"):
            self.assertEqual(props[name]["description"], next(f["help"] for f in flags if f["name"] == "--" + name))
            self.assertGreater(len(props[name]["description"]), 40)
        self.assertIn("COLUMN OP VALUE", props["where"]["description"])
        self.assertIn("count, sum:COL", props["agg"]["description"])
        self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
        self.assertNotIn("outputSchema", tool)

    def test_the_description_says_the_bound_and_what_to_ask_for(self):
        text = self.listed()["description"]
        for word in ("limit", "top", "select", "bounded", "path.dotdot", "rule"):
            self.assertIn(word, text)

    def test_the_committed_module_is_current(self):
        got = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp.py"), "--check", "--build-dir", str(BIN)], capture_output=True, text=True)
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def test_the_schema_follows_the_flag_table_of_the_tool(self):
        """A scratch copy of the sources with the tool's flag table edited -- a flag's kind changed, a flag's help
        changed, a boolean added -- is built, and the server built from its `introspect` lists the edited schema and
        takes what it says."""
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="mcp-mut-"))
        try:
            for name in ("tools", "generated", "schemas", "manifests"):
                shutil.copytree(ROOT / name, scratch / name)
            shutil.copy(ROOT / "cancho.toml", scratch / "cancho.toml")
            if (ROOT / "build" / "deps").is_dir():
                (scratch / "build").mkdir()
                shutil.copytree(ROOT / "build" / "deps", scratch / "build" / "deps")
            src = scratch / "tools" / "table" / "table.cho"
            text = src.read_text()
            edits = [
                ("top||nat|none||", "top||bool|none||"),                                                   # nat -> bool
                (";limit||nat|none||", ";limit||choice:few/many|none||"),                                  # nat -> choice
                ("the 0-based row to start at", "the 0-based row to start at (EDITED HELP)"),               # help follows
                (";format||", ";zzz||bool|none||a flag added by the test;format||"),                       # a new bool flag
            ]
            for old, new in edits:
                self.assertEqual(text.count(old), 1, old)
                text = text.replace(old, new)
            src.write_text(text)
            built = subprocess.run([manifest.compiler(), "build", "--bin", "table"], cwd=scratch, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            out = scratch / "mcp"
            mcp.build(str(scratch / "build"), str(BIN.resolve()), str(out))
            [r] = subprocess.run([str(out), "--root", str(root())], input=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
                                 capture_output=True).stdout.splitlines()
            props = json.loads(r)["result"]["tools"][0]["inputSchema"]["properties"]
            self.assertEqual(props["top"]["type"], "boolean")
            self.assertNotIn("minimum", props["top"])
            self.assertEqual(props["limit"]["enum"], ["few", "many"])
            self.assertIn("(EDITED HELP)", props["from"]["description"])
            self.assertEqual(props["zzz"]["type"], "boolean")
            self.assertEqual(props["zzz"]["description"], "a flag added by the test")
            # And the server takes what the edited schema says: a boolean is `--name` and refuses a number, a choice is a string.
            lines = [call({"file": "data.csv", "zzz": True}, 1), call({"file": "data.csv", "top": 3}, 2), call({"file": "data.csv", "top": "x"}, 3),
                     call({"file": "data.csv", "limit": 3}, 4), call({"file": "data.csv", "limit": "few"}, 5)]
            out = subprocess.run([str(out), "--root", str(root())], input=b"".join(json.dumps(l).encode() + b"\n" for l in lines), capture_output=True).stdout
            r = [json.loads(x) for x in out.splitlines()]
            # The scratch tool knows `--zzz` as a flag of its table (the parser reads that same table) and ignores it.
            self.assertNotIn("error", r[0], r[0])
            self.assertEqual(r[0]["result"]["content"][0]["text"][:6], '{"ok":')
            for n in (1, 2):
                self.assertEqual(r[n]["error"]["data"]["rule"], "mcp.wrong-type")
                self.assertEqual(r[n]["error"]["data"]["problems"][0]["expected"], "a boolean")
            self.assertEqual(r[3]["error"]["data"]["problems"], [{"property": "limit", "expected": "a string", "got": "number"}])
            self.assertNotIn("error", r[4], r[4])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class Confinement(unittest.TestCase):
    """Every path is beneath the served root, and nothing a model sends becomes an argument of its own or a shell word."""

    def secret_free(self, r):
        self.assertNotIn("TOPSECRET", json.dumps(r))

    def test_dotdot_absolute_and_links_are_refused_with_their_rules(self):
        secret = str(fixtures() / "secret" / "outside.csv")
        cases = [
            ("../secret/outside.csv", "path.dotdot"),
            ("sub/../data.csv", "path.dotdot"),
            ("..", "path.dotdot"),
            ("./../root/data.csv", "path.dotdot"),
            (secret, "path.outside-root"),
            ("/etc/passwd", "path.outside-root"),
            ("/", "path.outside-root"),
            ("", "path.empty"),
            ("out.csv", "path.symlink"),
            ("rel.csv", "path.symlink"),
            ("link.csv", "path.symlink"),                 # even a link to a file beneath the root
            ("linkdir/in.csv", "path.symlink"),
            ("secretdir/outside.csv", "path.symlink"),
            ("x" * 5000, "path.too-long"),
        ]
        for path, rule in cases:
            with self.subTest(path=path[:40]):
                arguments = {"file": path, "select": "#1"}      # the first column: of the secret file, it would be the secret
                r = one(root(), arguments)
                self.secret_free(r)
                result = r["result"]
                direct = cli(root(), arguments)
                self.assertEqual(result["_meta"]["exit_code"], direct.status)
                self.assertEqual(result["content"][0]["text"].encode(), direct.stdout)
                if rule:
                    self.assertTrue(result["isError"])
                    self.assertEqual(result["structuredContent"]["error"]["rule"], rule)

    def test_the_absolute_path_under_the_root_is_the_tools_to_judge(self):
        inside = str(root() / "data.csv")
        r = one(root(), {"file": inside})["result"]
        direct = cli(root(), {"file": inside})
        self.assertEqual(r["content"][0]["text"].encode(), direct.stdout)
        self.assertEqual(r["_meta"]["exit_code"], direct.status)

    def test_root_is_the_servers(self):
        # Not a property of the tool.
        r = one(root(), {"file": "data.csv", "root": "/"})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(r["error"]["data"]["rule"], "mcp.unknown-argument")
        self.assertEqual(r["error"]["data"]["property"], "root")
        self.assertIn("select", r["error"]["message"])        # it lists the real properties
        # An operand is an operand, after `--`; a flag's value is a value.
        r = one(root(), {"file": "--root=/"})["result"]
        self.assertEqual(r["structuredContent"]["error"]["rule"], "io.not-found")
        self.assertEqual(r["content"][0]["text"].encode(), cli(root(), {"file": "--root=/"}).stdout)      # the CLI says the same: it is a file name
        r = one(root(), {"file": "data.csv", "select": "--root=/"})["result"]
        self.assertEqual(r["structuredContent"]["error"]["rule"], "select.unknown-column")
        r = one(root(), {"file": "data.csv", "delimiter": "--root"})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(r["structuredContent"]["error"]["rule"], "args.bad-value")
        r = one(root(), {"file": "-x"})["result"]
        self.assertEqual(r["structuredContent"]["error"]["rule"], "io.not-found")

    def test_the_argv_is_fixed_in_form(self):
        fb = fake()
        cases = [
            ({"file": "a.csv"}, ["--root=%ROOT%", "--", "a.csv"]),
            ({"file": "a.csv", "limit": 5, "select": "x,y"}, ["--root=%ROOT%", "--select=x,y", "--limit=5", "--", "a.csv"]),
            ({"select": "x", "file": "a.csv", "limit": 5}, ["--root=%ROOT%", "--select=x", "--limit=5", "--", "a.csv"]),
            ({"file": "a.csv", "where": "a = 'b c' and d:int > 3"}, ["--root=%ROOT%", "--where=a = 'b c' and d:int > 3", "--", "a.csv"]),
            ({"file": "--root=/", "where": "--"}, ["--root=%ROOT%", "--where=--", "--", "--root=/"]),
            ({"file": "-"}, ["--root=%ROOT%", "--", "-"]),
            ({"file": "a b\nc.csv", "select": "$(touch x);`id`|&>"}, ["--root=%ROOT%", "--select=$(touch x);`id`|&>", "--", "a b\nc.csv"]),
            ({"file": "a.csv", "from": 0, "max-bytes": 1048576, "format": "csv"}, ["--root=%ROOT%", "--from=0", "--max-bytes=1048576", "--format=csv", "--", "a.csv"]),
            ({"file": "\u00e9\u20ac\U0001F600.csv", "delimiter": "\t"}, ["--root=%ROOT%", "--delimiter=\t", "--", "\u00e9\u20ac\U0001F600.csv"]),
            ({"file": "a.csv", "limit": 18446744073709}, ["--root=%ROOT%", "--limit=18446744073709", "--", "a.csv"]),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                if fb.record.exists():
                    fb.record.unlink()
                r = fb.call(arguments)
                self.assertFalse(r["result"]["isError"], r)
                self.assertEqual(fb.argv(), [e.replace("%ROOT%", str(fb.root)) for e in expected])
        self.assertFalse((fb.root / "x").exists())

    def test_no_shell_a_metacharacter_is_text(self):
        marker = fixtures() / "root" / "pwned"
        for arguments in [{"file": "data.csv", "select": "id; touch %s" % marker}, {"file": "data.csv", "where": "id=1 && touch %s" % marker},
                          {"file": "data.csv", "select": "$(touch %s)" % marker}, {"file": "`touch %s`" % marker},
                          {"file": "data.csv\ntouch %s" % marker}, {"file": "x; touch %s" % marker}]:
            with self.subTest(arguments=arguments):
                r = one(root(), arguments)["result"]
                self.assertTrue(r["isError"])
                self.assertFalse(marker.exists())
                direct = cli(root(), arguments)
                self.assertEqual(r["content"][0]["text"].encode(), direct.stdout)

    def test_a_nul_cannot_split_an_argument(self):
        for arguments in [{"file": "data.csv\u0000--root=/"}, {"file": "data.csv", "select": "id\u0000--root=/"}, {"file": "data.csv", "where": "\u0000"}]:
            with self.subTest(arguments=arguments):
                r = one(root(), arguments)
                self.assertEqual(r["error"]["code"], -32602)
                self.assertEqual(r["error"]["data"]["rule"], "mcp.nul-in-argument")
                self.assertIn("NUL", r["error"]["message"])

    def test_a_name_that_is_not_the_tool_is_refused(self):
        for name in ["sh", "../table", "/bin/sh", "table/../../bin/sh", "", "TABLE", "table ", "seek", "mcp"]:
            with self.subTest(name=name):
                [r], _ = talk(root(), [call({"file": "data.csv"}, name=name)])
                self.assertEqual(r["error"]["code"], -32602, name)
                self.assertEqual(r["error"]["data"]["rule"], "mcp.unknown-tool")
        [r], _ = talk(root(), [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": {}}}])
        self.assertEqual(r["error"]["data"]["rule"], "mcp.unknown-tool")
        [r], _ = talk(root(), [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": 5}}])
        self.assertEqual(r["error"]["data"]["rule"], "mcp.unknown-tool")


FAKE_SCRIPT = """#!/bin/sh
# A stand-in for `table`: it records its arguments, and does what its last argument (the file) names.
D=$(dirname "$0")/..
for a; do last="$a"; done
for a; do printf '%s\\0' "$a"; done > "$D/record"
case "$last" in
  sleep) echo $$ > "$D/pid"; sleep 30 ;;
  flood) yes x | head -c 50000000 ;;
  errflood) yes x | head -c 5000000 >&2 ;;
  errmore) yes x | head -c 60000 >&2; echo '{}'; exit 3 ;;
  signal) kill -9 $$ ;;
  stdin) if read x; then echo input >&2; fi; echo '{}' ;;
  array) echo '[1,2]' ;;
  number) echo 7 ;;
  garbage) echo 'not json' ;;
  empty) : ;;
  twice) echo '{"a":1}{"b":2}' ;;
  *) echo '{}' ;;
esac
"""


class Fake:
    """A directory with a `table` that is a shell script, and a server baked to it: for what the real tool does not do
    on request (docs/mcp.md §6). Built once for the module."""

    def __init__(self, with_tool=True):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="mcp-fake-")).resolve()
        self.root = self.dir / "root"
        self.root.mkdir()
        self.record = self.dir / "record"
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        if with_tool:
            (self.bin / "table").write_text(FAKE_SCRIPT)
            (self.bin / "table").chmod(0o755)

    def call(self, arguments, flags=(), timeout=60):
        responses, status = talk(self.root, [call(arguments)], flags=flags, bin_dir=self.bin, timeout=timeout)
        assert status == 0, status
        return responses[0]

    def argv(self):
        return [g.decode() for g in self.record.read_bytes().split(b"\0")[:-1]]


FAKES = {}


def fake(with_tool=True):
    if with_tool not in FAKES:
        FAKES[with_tool] = Fake(with_tool)
    return FAKES[with_tool]


class Limits(unittest.TestCase):
    """The output bound and the deadline: each ends a call with its own rule and the server keeps serving."""

    def test_the_output_bound_is_exact_and_refuses_with_what_to_ask_for(self):
        arguments = {"file": "data.csv", "select": "id,note", "limit": 400}
        direct = cli(root(), arguments)
        size = len(direct.stdout)
        self.assertGreater(size, 4000)
        ok = one(root(), arguments, flags=["--max-output", str(size)])["result"]
        self.assertEqual(ok["content"][0]["text"].encode(), direct.stdout)
        self.assertFalse(ok["isError"])
        responses, status = talk(root(), [call(arguments, 1), {"jsonrpc": "2.0", "id": 2, "method": "ping"}, call({"file": "data.csv"}, 3)],
                                 flags=["--max-output", str(size - 1)])
        self.assertEqual(status, 0)
        r = responses[0]["result"]
        self.assertTrue(r["isError"])
        record = json.loads(r["content"][0]["text"])
        self.assertEqual(record["error"]["rule"], "mcp.output-too-large")
        self.assertEqual(record["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(record["error"]["detail"]["limit"], size - 1)
        for word in ("`limit`", "`top`", "`select`", "`max-bytes`"):
            self.assertIn(word, record["error"]["hint"])
        self.assertNotIn("threads", record["error"]["hint"])
        self.assertEqual(record["error"]["repair"], {"kind": "none", "reason": record["error"]["hint"]})
        self.assertNotIn("structuredContent", r)
        self.assertEqual(r["content"][0]["_meta"], {"stream": "server"})
        # The server answers the next requests: the bound ended one call, not the connection, and a small answer is within it.
        self.assertEqual(responses[1]["result"], {})
        self.assertFalse(responses[2]["result"]["isError"])

    def test_the_default_bound_is_two_mebibytes_and_a_default_page_is_within_it(self):
        d = json.loads(subprocess.run([str(server()), "introspect"], capture_output=True).stdout)
        self.assertEqual({l["name"]: l["default"] for l in d["limits"]}["max-output"], 2097152)
        # The tool's own page bound is 1 MiB of rows: its envelope fits in the rest.
        f = fixtures() / "big" / "big.csv"
        r = one(f.parent, {"file": "big.csv", "select": "id,path,note", "limit": 1000000})["result"]
        self.assertFalse(r["isError"], r["content"][0]["text"][:300])
        doc = r["structuredContent"]
        self.assertTrue(doc["data"]["truncated"])
        self.assertLessEqual(len(r["content"][0]["text"].encode()), 2097152)

    def test_a_whole_file_as_csv_is_refused_and_a_bounded_query_of_it_is_not(self):
        big = fixtures() / "big"
        r = one(big, {"file": "big.csv", "select": "id,note", "format": "csv"})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.output-too-large")
        r = one(big, {"file": "big.csv", "select": "id,note", "format": "csv", "limit": 10})["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(r["content"][0]["text"].count("\n"), 11)     # the header and ten rows

    def test_the_deadline_ends_a_call_with_its_own_rule(self):
        big = fixtures() / "big"
        t0 = time.time()
        responses, status = talk(big, [call({"file": "big.csv", "group": "path", "agg": "sum:bytes,distinct:note", "max-distinct": 10000000}, 1),
                                       {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                                       call({"file": "big.csv", "select": "id", "limit": 2}, 3)], flags=["--timeout-ms", "1"])
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(status, 0)
        r = responses[0]["result"]
        self.assertTrue(r["isError"])
        record = json.loads(r["content"][0]["text"])
        self.assertEqual(record["error"]["rule"], "mcp.timeout")
        self.assertEqual(record["error"]["detail"]["limit"], 1)
        for word in ("`where`", "`limit`", "`threads`", "--timeout-ms"):
            self.assertIn(word, record["error"]["hint"])
        self.assertNotIn("max-bytes", record["error"]["hint"])
        self.assertEqual(record["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(responses[1]["result"], {})
        # The server is serving: the small call that follows is answered, by the tool or by the deadline (1 ms can be enough for it).
        self.assertIn("result", responses[2])
        # With the default deadline the same small call is answered.
        r = one(big, {"file": "big.csv", "select": "id", "limit": 2})["result"]
        self.assertFalse(r["isError"])

    def test_the_deadline_kills_the_child(self):
        fb = fake()
        t0 = time.time()
        r = fb.call({"file": "sleep"}, flags=["--timeout-ms", "300"])["result"]
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.timeout")
        pid = int((fb.dir / "pid").read_text())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_a_flood_on_standard_output_or_error_is_bounded(self):
        fb = fake()
        r = fb.call({"file": "flood"}, flags=["--max-output", "10000"])["result"]
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.output-too-large")
        r = fb.call({"file": "errflood"})["result"]
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.output-too-large")
        # standard error up to its bound is returned whole, beside the output
        r = fb.call({"file": "errmore"})["result"]
        self.assertEqual((r["isError"], r["_meta"]["exit_code"], len(r["content"][1]["text"])), (True, 3, 60000))

    def test_a_tool_ended_by_a_signal_and_one_that_cannot_start(self):
        r = fake().call({"file": "signal"})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(json.loads(r["content"][0]["text"])["error"]["rule"], "mcp.signaled")
        r = fake(False).call({"file": "a.csv"})["result"]
        record = json.loads(r["content"][0]["text"])
        self.assertTrue(r["isError"])
        self.assertEqual(record["error"]["rule"], "mcp.spawn")
        self.assertEqual(record["error"]["code"], "GENERAL_ERROR")
        self.assertGreater(record["error"]["detail"]["errno"], 0)

    def test_only_a_document_that_is_one_object_is_structured(self):
        for mode, structured in (("array", False), ("number", False), ("garbage", False), ("empty", False), ("twice", False), ("stdin", True)):
            with self.subTest(mode=mode):
                r = fake().call({"file": mode})["result"]
                self.assertEqual("structuredContent" in r, structured, r)
                self.assertFalse(r["isError"])
        r = fake().call({"file": "array"})["result"]
        self.assertEqual(r["content"][0]["text"], "[1,2]\n")

    def test_the_child_gets_no_standard_input(self):
        r = fake().call({"file": "stdin"})["result"]
        self.assertFalse(r["isError"], r)
        self.assertEqual(len(r["content"]), 1)


class Protocol(unittest.TestCase):
    def test_initialize_and_the_rest(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/whatever", "params": {"a": 1}},
            {"jsonrpc": "2.0", "id": "two", "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "table", "arguments": {"file": "data.csv"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}},
        ]
        responses, status = talk(root(), lines)
        self.assertEqual(status, 0)
        self.assertEqual([r["id"] for r in responses], [1, "two", 3, 4, 5])          # notifications are not answered, not even a call without an id
        init = responses[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(init["serverInfo"]["name"], "cancho-table")
        self.assertEqual(responses[1]["result"], {})
        self.assertEqual([t["name"] for t in responses[2]["result"]["tools"]], ["table"])
        self.assertEqual(responses[3]["error"]["code"], -32601)
        self.assertEqual(responses[3]["error"]["data"]["rule"], "mcp.unknown-method")
        self.assertEqual(responses[4]["result"]["protocolVersion"], "2025-06-18")

    def test_malformed_requests_are_answered_not_fatal(self):
        bad = [b"", b"not json", b"{", b"[]", b"1", b'"x"', b"null", b"true", b"{}", b'{"jsonrpc":"1.0","id":1,"method":"ping"}',
               b'{"jsonrpc":"2.0","id":1}', b'{"jsonrpc":"2.0","id":1,"method":5}', b'{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}', b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[]}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":[]}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":null}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":["a"]}}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"a","select":5,"limit":"3","from":-1,"where":null}}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"a","limit":1.5}}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"a","limit":99999999999999999999}}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"a","format":"yaml"}}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"a","format":5}}}',
               b'\xff\xfe\x00{"jsonrpc"', b'{"jsonrpc":"2.0","id":1,"method":"pi\xffng"}', b"[" * 100000, b"{" * 100000,
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"\\ud800"}}}',
               b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"table","arguments":{"file":"\\u0000"}}}']
        ping = {"jsonrpc": "2.0", "id": "last", "method": "ping"}
        responses, status = talk(root(), [*bad, ping])
        self.assertEqual(status, 0)
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "last", "result": {}})
        for r in responses[:-1]:
            self.assertEqual(r["jsonrpc"], "2.0")
            self.assertTrue(("result" in r) != ("error" in r), r)
            if "error" in r:
                self.assertIn(r["error"]["code"], (-32700, -32600, -32601, -32602), r)
                self.assertTrue(r["error"]["data"]["rule"].startswith("mcp."), r)
        # a few by name
        self.assertEqual(responses[1]["error"]["code"], -32700)
        self.assertEqual(responses[1]["error"]["data"]["rule"], "mcp.parse-error")
        self.assertIsNone(responses[1]["id"])
        self.assertEqual(responses[3]["error"]["data"]["rule"], "mcp.invalid-request")

    def test_every_type_fault_is_told_at_once_and_what_to_send(self):
        r = one(root(), {"file": "data.csv", "limit": "3", "from": "1", "top": 1.5, "select": 5, "max-rows": True})
        self.assertEqual(r["error"]["code"], -32602)
        data = r["error"]["data"]
        self.assertEqual(data["rule"], "mcp.wrong-type")
        self.assertEqual({p["property"]: (p["expected"], p["got"]) for p in data["problems"]},
                         {"limit": ("a non-negative integer", "string"), "from": ("a non-negative integer", "string"), "top": ("a non-negative integer", "number"),
                          "select": ("a string", "number"), "max-rows": ("a non-negative integer", "boolean")})
        for fragment in ("`limit` must be a non-negative integer, not a string; send a JSON number such as 1", "`top`", "negative or fractional", "`select` must be a string, not a number"):
            self.assertIn(fragment, r["error"]["message"])
        r = one(root(), {"file": ["a"], "limit": -1})
        self.assertEqual(sorted(p["property"] for p in r["error"]["data"]["problems"]), ["file", "limit"])
        [r], _ = talk(root(), [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "table", "arguments": []}}])
        self.assertEqual(r["error"]["data"]["rule"], "mcp.arguments-not-object")

    def test_an_oversize_frame_is_refused_and_the_next_line_is_read(self):
        limit = 4194304
        for size in (limit + 1, limit * 2 + 7):
            with self.subTest(size=size):
                big = b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"' + b"a" * size + b'"}'
                responses, status = talk(root(), [big, {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
                self.assertEqual(status, 0)
                self.assertEqual(responses[0]["error"]["code"], -32600)
                self.assertEqual(responses[0]["error"]["data"]["rule"], "mcp.line-too-long")
                self.assertIsNone(responses[0]["id"])
                self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})
        # exactly the bound is read
        pad = limit - len(b'{"jsonrpc":"2.0","id":1,"method":"ping","x":""}')
        line = b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"' + b"a" * pad + b'"}'
        self.assertEqual(len(line), limit)
        responses, _ = talk(root(), [line])
        self.assertEqual(responses[0]["result"], {})
        # one byte more is not
        responses, _ = talk(root(), [line[:-2] + b'a"}', {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
        self.assertEqual(responses[0]["error"]["data"]["rule"], "mcp.line-too-long")
        self.assertEqual(responses[1]["result"], {})
        # nor is a line past the bound that ends with the input and not a newline
        p = subprocess.run([str(server()), "--root", str(root())], input=line + b"aa", capture_output=True, timeout=60)
        self.assertEqual(json.loads(p.stdout)["error"]["data"]["rule"], "mcp.line-too-long")
        self.assertEqual(p.stdout.count(b"\n"), 1)

    def test_end_of_input_ends_the_server_and_an_unterminated_last_line_is_read(self):
        p = subprocess.run([str(server()), "--root", str(root())], input=b'{"jsonrpc":"2.0","id":1,"method":"ping"}', capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout), {"jsonrpc": "2.0", "id": 1, "result": {}})
        p = subprocess.run([str(server()), "--root", str(root())], input=b"", capture_output=True, timeout=30)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def test_an_answer_arrives_while_the_input_is_still_open(self):
        p = subprocess.Popen([str(server()), "--root", str(root())], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for n in range(3):
                p.stdin.write(json.dumps(call({"file": "data.csv", "limit": 1, "select": "id"}, n)).encode() + b"\n")
                p.stdin.flush()
                ready, _, _ = select.select([p.stdout], [], [], 20)
                self.assertTrue(ready, "no answer while the input is open (the output is not flushed)")
                self.assertEqual(json.loads(p.stdout.readline())["id"], n)
        finally:
            p.stdin.close()
            self.assertEqual(p.wait(timeout=20), 0)

    def test_nothing_but_responses_on_standard_output_and_one_per_line(self):
        responses, _ = talk(root(), [call(a, n) for n, a in enumerate(CORPUS[:12])])
        raw = subprocess.run([str(server()), "--root", str(root())], input=b"".join(json.dumps(call(a, n)).encode() + b"\n" for n, a in enumerate(CORPUS[:12])),
                             capture_output=True).stdout
        self.assertEqual(raw.count(b"\n"), 12)
        self.assertTrue(raw.endswith(b"\n"))
        for l in raw.split(b"\n")[:-1]:
            json.loads(l)

    def test_the_servers_own_command_line(self):
        for argv, fragment in [([], "--root DIR is required"), (["--root", "rel"], "absolute"), (["--root"], "takes a value"),
                               (["--root", "/tmp", "--timeout-ms", "0"], "positive"), (["--root", "/tmp", "--timeout-ms", "x"], "positive"),
                               (["--root", "/tmp", "--max-output", "-1"], "positive"), (["--root", "/tmp", "--max-output", "9" * 13], "positive"),
                               (["--root", "/tmp", "--bogus", "1"], "the flags are"), (["introspect", "x"], "the flags are"), (["--root", "/tmp", "introspect"], "takes a value")]:
            with self.subTest(argv=argv):
                p = subprocess.run([str(server()), *argv], input=b"", capture_output=True, timeout=30)
                self.assertEqual(p.returncode, 2, p)
                self.assertEqual(p.stdout, b"")
                self.assertIn(fragment, p.stderr.decode())


class Hostile(unittest.TestCase):
    """Seeded garbage in a valid frame, and a valid frame with seeded damage: every line is answered or ignored, no
    panic, and the server answers the ping after them."""

    def test_three_hundred_seeded_requests(self):
        rng = random.Random(20260718)
        seeds = [call({"file": "data.csv", "select": "id,status", "limit": 3, "where": "status=404"}),
                 call({"file": "nums.csv", "group": "k", "agg": "sum:int"}),
                 {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}]
        junk = [b"\x00", b"\xff", b'"', b"\\", b"{", b"}", b"[", b"]", b",", b":", b"null", b"-0", b"1e999", b"\\u0000", b"\\ud83d", b"\n"[:0], b"\xe2\x82"]
        lines = []
        for _ in range(300):
            kind = rng.randrange(5)
            base = json.dumps(rng.choice(seeds)).encode()
            if kind == 0:
                data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300))).replace(b"\n", b" ")
            elif kind == 1:
                i = rng.randrange(len(base))
                data = base[:i] + rng.choice(junk) + base[i:]
            elif kind == 2:
                i = rng.randrange(len(base))
                data = base[:i]
            elif kind == 3:
                i, j = sorted(rng.randrange(len(base)) for _ in range(2))
                data = base[:i] + base[j:]
            else:
                data = (b"[" * rng.randrange(1, 300)) + base + (b"]" * rng.randrange(0, 300))
            lines.append(data.replace(b"\n", b" "))
        responses, status = talk(root(), [*lines, {"jsonrpc": "2.0", "id": "end", "method": "ping"}], timeout=300)
        self.assertEqual(status, 0)
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "end", "result": {}})
        for r in responses:
            self.assertTrue(("result" in r) != ("error" in r), r)
            if "error" in r:
                self.assertTrue(r["error"]["data"]["rule"].startswith("mcp."), r)


class Own(unittest.TestCase):
    """The server describes itself, and its authority is what the compiler says, within its ceiling."""

    def introspect(self):
        p = subprocess.run([str(server()), "introspect"], capture_output=True, timeout=30)
        self.assertEqual((p.returncode, p.stderr), (0, b""))
        self.assertEqual(p.stdout.count(b"\n"), 1)
        return json.loads(p.stdout)

    def test_introspect(self):
        d = self.introspect()
        self.assertEqual((d["tool"], d["version"], d["tools"]), ("mcp", mcp.VERSION, ["table"]))
        self.assertEqual(d["compiler"], manifest.project()["package"]["cancho"])
        self.assertEqual(d["serves"]["tool"], "table")
        self.assertEqual(d["serves"]["version"], introspect()["version"])
        self.assertEqual({f["name"] for f in d["flags"]}, {"--root", "--timeout-ms", "--max-output"})
        self.assertEqual(d["protocol"]["max_line_bytes"], 4194304)
        self.assertEqual({l["name"]: l["default"] for l in d["limits"]}, {"timeout-ms": 30000, "max-output": 2097152, "stderr": 65536, "line": 4194304})
        self.assertEqual(d["usage"], "mcp --root DIR [--timeout-ms N] [--max-output N] | mcp introspect")

    def test_every_rule_it_lists_is_one_it_says_and_every_one_it_says_is_listed(self):
        listed = {r["rule"] for r in self.introspect()["rules"]}
        said = set()
        # the protocol's
        responses, _ = talk(root(), [b"nope", b"[]", {"jsonrpc": "2.0", "id": 1, "method": "x"}, call({}, name="sh"), call([]),
                                     call({"file": "a", "zz": 1}), call({"file": 5}), call({"file": "a\u0000"}),
                                     b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"' + b"a" * 4194304 + b'"}'])
        for r in responses:
            said.add(r["error"]["data"]["rule"])
        # a call the server ended
        for arguments, flags in (({"file": "data.csv", "select": "id,note", "limit": 400}, ["--max-output", "100"]),):
            said.add(json.loads(one(root(), arguments, flags=flags)["result"]["content"][0]["text"])["error"]["rule"])
        fb = fake()
        for name, flags in (("sleep", ["--timeout-ms", "100"]), ("signal", [])):
            said.add(json.loads(fb.call({"file": name}, flags=flags)["result"]["content"][0]["text"])["error"]["rule"])
        said.add(json.loads(fake(False).call({"file": "a"})["result"]["content"][0]["text"])["error"]["rule"])
        self.assertEqual(said, listed)

    def test_the_authority_is_the_compilers_and_within_its_ceiling(self):
        d = self.introspect()
        record = json.loads((ROOT / "manifests" / "mcp.authority.json").read_text())
        # A server built with another directory says that directory; the rest of its row is the committed one.
        committed = [l for l in record["labels"] if l["name"] != "exec"]
        got = [l for l in d["authority"]["labels"] if l["name"] != "exec"]
        self.assertEqual(got, committed)
        [exec_label] = [l for l in d["authority"]["labels"] if l["name"] == "exec"]
        self.assertEqual(exec_label["argument"], str(BIN.resolve()))
        self.assertEqual(d["authority"]["foreign_symbols"], [])
        self.assertTrue(d["authority"]["bounded"])
        names = {l["name"] for l in d["authority"]["labels"]}
        # No file system, no network, no foreign code: it reads nothing itself.
        self.assertFalse(names & {"ffi", "net_out", "net_in", "fs_read", "fs_write", "file_read", "file_write", "dir_read", "dir_write"})
        self.assertEqual(names, {"args", "child_signal", "clock", "err_write", "exec", "heap", "io_read", "io_write", "pipe_read", "pipe_write", "poll"})
        # and it is the compiler's: derive it again here.
        text = mcp.baked(mcp.SERVER.read_text(), str(BIN.resolve()))
        fresh = mcp.derive_server(text, (ROOT / "generated" / "mcp" / "tools.cho").read_text().replace(mcp.DEFAULT_BIN, str(BIN.resolve())))
        self.assertEqual([l for l in fresh["labels"] if l["name"] != "exec"], committed)

    def test_the_gate_fails_when_the_row_widens(self):
        base = json.loads((ROOT / "manifests" / "mcp.authority.json").read_text())
        self.assertEqual(mcp.gate(base, mcp.DEFAULT_BIN), [])
        for extra in ("net_out", "fs_read", "file_write", "ffi", "clock_extra"):
            widened = json.loads(json.dumps(base))
            widened["labels"].append({"name": extra, "argument": None, "bounded": True})
            problems = mcp.gate(widened, mcp.DEFAULT_BIN)
            self.assertTrue(problems, extra)
            self.assertTrue(any(extra in p for p in problems), problems)
        unbounded = json.loads(json.dumps(base))
        unbounded["bounded"] = False
        self.assertTrue(mcp.gate(unbounded, mcp.DEFAULT_BIN))
        elsewhere = json.loads(json.dumps(base))
        for l in elsewhere["labels"]:
            if l["name"] == "exec":
                l["argument"] = "/bin"
        self.assertTrue(any("exec" in p for p in mcp.gate(elsewhere, mcp.DEFAULT_BIN)))
        # a forbidden label is refused even when the allow list names it
        with tempfile.TemporaryDirectory() as d:
            ceiling = pathlib.Path(d) / "ceiling.toml"
            ceiling.write_text('[mcp]\nallow = ["args", "net_out"]\nforbid = ["net_out"]\n')
            widened = {"bounded": True, "foreign_symbols": [], "labels": [{"name": "net_out", "argument": None, "bounded": True}]}
            saved = mcp.CEILING
            mcp.CEILING = ceiling
            try:
                self.assertEqual(mcp.gate(widened, mcp.DEFAULT_BIN), ["mcp: net_out is forbidden to the server"])
            finally:
                mcp.CEILING = saved
        # an exec label for another directory is refused even when the ceiling names it
        with tempfile.TemporaryDirectory() as d:
            ceiling = pathlib.Path(d) / "ceiling.toml"
            ceiling.write_text('[mcp]\nallow = ["exec(\\"/bin\\")", "exec(\\"%s\\")"]\nforbid = []\n' % mcp.DEFAULT_BIN)
            moved = {"bounded": True, "foreign_symbols": [], "labels": [{"name": "exec", "argument": "/bin", "bounded": True}]}
            saved = mcp.CEILING
            mcp.CEILING = ceiling
            try:
                problems = mcp.gate(moved, mcp.DEFAULT_BIN)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("not the binary directory", problems[0])
            finally:
                mcp.CEILING = saved
        # a ceiling that leaves out a label the server has
        with tempfile.TemporaryDirectory() as d:
            ceiling = pathlib.Path(d) / "ceiling.toml"
            ceiling.write_text('[mcp]\nallow = ["args"]\nforbid = []\n')
            saved = mcp.CEILING
            mcp.CEILING = ceiling
            try:
                self.assertGreater(len(mcp.gate(base, mcp.DEFAULT_BIN)), 5)
            finally:
                mcp.CEILING = saved

    def test_the_generator_refuses_what_would_be_false(self):
        # An embedding that changes the authority is no fixed point.
        calls = []
        real = mcp.derive_server

        def drifting(server_text, tools_text):
            calls.append(1)
            report = json.loads(json.dumps(real(server_text, tools_text)))
            report["functions"] += len(calls)
            return report
        mcp.derive_server = drifting
        try:
            with self.assertRaises(SystemExit) as caught:
                mcp.generated(str(BIN), mcp.DEFAULT_BIN)
            self.assertIn("no fixed point", str(caught.exception))
        finally:
            mcp.derive_server = real
        # The hints name properties the tool has: one that is gone is an error, not a false sentence.
        props = {p: {} for p in ("limit", "top", "select", "where", "threads", "format")}
        self.assertEqual(len(mcp.hints(props)), 2)
        for gone in props:
            with self.assertRaises(SystemExit, msg=gone):
                mcp.hints({p: v for p, v in props.items() if p != gone})
        # A tool that reads standard input, or an operand named like a flag, is not offered silently.
        d = json.loads(json.dumps(introspect()))
        d["authority"]["effects"].append("io_read")
        with self.assertRaises(SystemExit):
            mcp.definition(d)
        d = json.loads(json.dumps(introspect()))
        d["operands"][0]["name"] = "LIMIT"
        with self.assertRaises(SystemExit):
            mcp.definition(d)

    @unittest.skipIf(os.environ.get("MCP_MUTANT_RUN"), "the mutants are being applied: the sources are not the ones their patterns match")
    def test_the_mutants_still_apply(self):
        got = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp_mutants.py"), "--check"], capture_output=True, text=True)
        self.assertEqual((got.returncode, got.stdout.count("!!")), (0, 0), got.stdout)


class Structured(unittest.TestCase):
    def test_structured_content_is_the_document_and_only_when_it_is_json(self):
        for fmt, structured in (("json", True), ("csv", False), ("text", False), (None, True)):
            with self.subTest(format=fmt):
                a = {"file": "data.csv", "select": "id", "limit": 2}
                if fmt:
                    a["format"] = fmt
                if fmt == "text":
                    a = {"file": "data.csv", "format": "text"}
                r = one(root(), a)["result"]
                self.assertFalse(r["isError"], r)
                self.assertEqual("structuredContent" in r, structured)
                if structured:
                    self.assertEqual(r["structuredContent"], json.loads(r["content"][0]["text"]))

    def test_text_that_is_a_json_object_is_still_text(self):
        # A file whose header is `{}`: its csv rows are `{}` and a newline, which parse as one object.
        direct = cli(root(), {"file": "objhdr.csv", "select": "#1", "format": "csv"})
        self.assertEqual(direct.stdout, b"{}\n")
        r = one(root(), {"file": "objhdr.csv", "select": "#1", "format": "csv"})["result"]
        self.assertEqual(r["content"][0]["text"], "{}\n")
        self.assertNotIn("structuredContent", r)

    def test_a_document_with_a_newline_inside_a_cell_is_one_line(self):
        raw = subprocess.run([str(server()), "--root", str(root())], input=json.dumps(call({"file": "quoted.csv", "select": "name"})).encode() + b"\n", capture_output=True).stdout
        self.assertEqual(raw.count(b"\n"), 1)
        r = json.loads(raw)["result"]
        self.assertIn("multi\\nline", r["content"][0]["text"])
        self.assertEqual(r["structuredContent"]["data"]["rows"][1], ["multi\nline"])


if __name__ == "__main__":
    unittest.main()
