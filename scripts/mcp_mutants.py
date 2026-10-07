#!/usr/bin/env python3
"""Mutation check of the MCP server (docs/mcp.md §6): each mutant is one deliberate defect in server/mcp.cho (the
server's logic) or in scripts/mcp.py (how the schema and the authority gate are derived), the server rebuilt from it
and run against tests/conformance/test_mcp.py. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/mcp_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). A run needs the tool built
(`cancho build`) and a compiler, and does not touch tools/table. See mutlib.py.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

ROOT = mutlib.ROOT
SERVER = ROOT / "server"
TESTS = ["test_mcp"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file. Files are relative to server/.
MUTANTS = [
    # the wire
    ("an answer is not flushed", "mcp.cho", "    var status = 0;\n    match flush_out(io) {\n        Done::Ok(n) => {\n        }\n        Done::Failed(e) => {\n            status = e;",
     "    var status = 0;\n    match Done::Ok(0) {\n        Done::Ok(n) => {\n        }\n        Done::Failed(e) => {\n            status = e;"),
    ("a notification is answered", "mcp.cho", "            if id >= 0 {\n                if json.string_equals(src, t, method, \"initialize\") {", "            if true {\n                if json.string_equals(src, t, method, \"initialize\") {"),
    ("a string id is answered as null", "mcp.cho", "    if json.is_string(tape, id) {\n        let text = decoded(heap, src, tape, id);", "    if false {\n        let text = decoded(heap, src, tape, id);"),
    ("a line one byte past the bound is read", "mcp.cho", "            if n >= longest_line() {", "            if n > longest_line() {"),
    ("the bound of a line is eight times", "mcp.cho", "fn longest_line() -> [] int {\n    return tools.longest_line();", "fn longest_line() -> [] int {\n    return 8 * tools.longest_line();"),
    ("a line past the bound is taken for a request", "mcp.cho", "            if over {\n                return (line, 2);\n            }\n            return (line, 0);\n        }\n        if c == 10 {", "            if over {\n                return (line, 0);\n            }\n            return (line, 0);\n        }\n        if c == 10 {"),
    ("a parse error has the code of an invalid request", "mcp.cho", "0 - 1, 0 - 32700, \"mcp.parse-error\"", "0 - 1, 0 - 32600, \"mcp.parse-error\""),
    ("a refusal carries the wrong rule", "mcp.cho", "    w = json.put_key(heap, w, \"rule\");\n    w = json.put_string(heap, w, rule);\n    w = json.end_object(heap, w);\n    w = json.end_object(heap, w);\n    w = json.end_object(heap, w);\n    return send(heap, io, w);\n}\n\n// `refuse`, with",
     "    w = json.put_key(heap, w, \"rule\");\n    w = json.put_string(heap, w, \"mcp.error\");\n    w = json.end_object(heap, w);\n    w = json.end_object(heap, w);\n    w = json.end_object(heap, w);\n    return send(heap, io, w);\n}\n\n// `refuse`, with"),
    ("the server claims a list that changes", "mcp.cho", "{\\\"tools\\\":{\\\"listChanged\\\":false}}", "{\\\"tools\\\":{\\\"listChanged\\\":true}}"),
    ("ping answers an array", "mcp.cho", "w = json.put_fragment(heap, w, \"{}\");", "w = json.put_fragment(heap, w, \"[]\");"),
    ("tools/list lists nothing", "mcp.cho", "w = json.put_fragment(heap, w, tools.list_result());", "w = json.put_fragment(heap, w, \"{\\\"tools\\\":[]}\");"),
    # which call, and what it becomes
    ("any tool name is the tool", "mcp.cho", "        if json.string_equals(src, tape, asked, tools.name(k)) {\n            tool = k;", "        if true {\n            tool = k;"),
    ("a property the tool does not have is passed", "mcp.cho", "            if !known(src, tape, tool, j) {\n                return (entries, Problem { code: 2,", "            if false && !known(src, tape, tool, j) {\n                return (entries, Problem { code: 2,"),
    ("the root is the whole file system", "mcp.cho", "    with_root = buffer.append(heap, with_root, root);", "    with_root = buffer.append(heap, with_root, \"/\");"),
    ("operands are not fenced", "mcp.cho", "    let (fenced, refused) = process.add(heap, entries, \"--\");", "    let (fenced, refused) = process.add(heap, entries, \"-q\");"),
    ("a flag's value is a flag of its own", "mcp.cho", "                var prefix = buffer.empty(heap, len(flag) + 1);\n                prefix = buffer.append(heap, prefix, flag);\n                prefix = buffer.push(heap, prefix, byte_of('='));",
     "                var prefix = buffer.empty(heap, len(flag) + 1);\n                prefix = buffer.append(heap, prefix, flag);\n                prefix = buffer.push(heap, prefix, byte_of(' '));"),
    ("a NUL in a flag's value is let through", "mcp.cho", "                buffer.drop(heap, prefix);\n                if refused != 0 {", "                buffer.drop(heap, prefix);\n                if false {"),
    ("a NUL in an operand is let through", "mcp.cho", "        if refused != 0 {\n            result = 4;", "        if false {\n            result = 4;"),
    ("type faults are not told together", "mcp.cho", "    if given >= 0 && json.is_object(tape, given) {\n        var j = given + 1;\n        var left = json.count(tape, given);\n        while left > 0 {\n            let kind = kind_of(", "    if false && given >= 0 && json.is_object(tape, given) {\n        var j = given + 1;\n        var left = json.count(tape, given);\n        while left > 0 {\n            let kind = kind_of("),
    ("a number is a string", "mcp.cho", "    if json.is_string(tape, v) {\n        return 0 - 1;\n    }\n    return v;\n}\n\n// Every property", "    return 0 - 1;\n}\n\n// Every property"),
    ("a count may be negative", "mcp.cho", "        if json.is_int(tape, v) && json.fits_int(src, tape, v) && json.to_int(src, tape, v) >= 0 {", "        if json.is_int(tape, v) && json.fits_int(src, tape, v) {"),
    ("a boolean is not checked", "mcp.cho", "        if json.is_bool(tape, v) {\n            return 0 - 1;\n        }\n        return v;", "        return 0 - 1;"),
    # what comes back
    ("every exit but zero is a success", "mcp.cho", "    w = json.put_bool(heap, w, code != 0);", "    w = json.put_bool(heap, w, false);"),
    ("exit 8 is a success", "mcp.cho", "    w = json.put_bool(heap, w, code != 0);", "    w = json.put_bool(heap, w, code != 0 && code != 8);"),
    ("the exit code is not the tool's", "mcp.cho", "    w = json.put_key(heap, w, \"exit_code\");\n    w = json.put_int(heap, w, code);", "    w = json.put_key(heap, w, \"exit_code\");\n    w = json.put_int(heap, w, code + 1);"),
    ("standard error is dropped", "mcp.cho", "    if len(errors) > 0 {\n        w = text_item(heap, w, \"stderr\", errors);", "    if false {\n        w = text_item(heap, w, \"stderr\", errors);"),
    ("csv is structured content", "mcp.cho", "    return v < 0 || json.string_equals(src, tape, v, \"json\");", "    return true;"),
    ("only csv is structured content", "mcp.cho", "    return v < 0 || json.string_equals(src, tape, v, \"json\");", "    return v >= 0;"),
    ("an array is structured content", "mcp.cho", "        whole = nodes > 0 && json.is_object(contents(tw), 0);", "        whole = nodes > 0;"),
    ("the fragment keeps its newline", "mcp.cho", "        while end > 0 && (out[end - 1] == byte_of(10) ||", "        while false && (out[end - 1] == byte_of(10) ||"),
    # the limits
    ("the output bound is one byte more", "mcp.cho", "contents(ir), config.most, tools.most_errors(), config.timeout);", "contents(ir), config.most + 1, tools.most_errors(), config.timeout);"),
    ("the output bound is not the flag's", "mcp.cho", "contents(ir), config.most, tools.most_errors(), config.timeout);", "contents(ir), tools.default_output(), tools.most_errors(), config.timeout);"),
    ("there is no bound on standard error", "mcp.cho", "contents(ir), config.most, tools.most_errors(), config.timeout);", "contents(ir), config.most, 1000000000, config.timeout);"),
    ("standard error may not be written at all", "mcp.cho", "contents(ir), config.most, tools.most_errors(), config.timeout);", "contents(ir), config.most, 0, config.timeout);"),
    ("the deadline is a thousand times longer", "mcp.cho", "contents(ir), config.most, tools.most_errors(), config.timeout);", "contents(ir), config.most, tools.most_errors(), config.timeout * 1000);"),
    ("a deadline is the output rule", "mcp.cho", "\"PRECONDITION_FAILED\", \"mcp.timeout\",", "\"PRECONDITION_FAILED\", \"mcp.output-too-large\","),
    ("an output bound is the deadline rule", "mcp.cho", "\"PRECONDITION_FAILED\", \"mcp.output-too-large\", \"the answer is larger", "\"PRECONDITION_FAILED\", \"mcp.timeout\", \"the answer is larger"),
    ("a deadline says what to do about size", "mcp.cho", "tools.hint_timeout(), 0, config.timeout);", "tools.hint_too_large(), 0, config.timeout);"),
    ("a size refusal says what to do about time", "mcp.cho", "tools.hint_too_large(), 0, config.most);", "tools.hint_timeout(), 0, config.most);"),
    ("a deadline names no limit", "mcp.cho", "tools.hint_timeout(), 0, config.timeout);", "tools.hint_timeout(), 0, 0);"),
    ("a signal is a spawn failure", "mcp.cho", "\"GENERAL_ERROR\", \"mcp.signaled\",", "\"GENERAL_ERROR\", \"mcp.spawn\","),
    ("a refusal by the server is a success", "mcp.cho", "    w = json.put_key(heap, w, \"isError\");\n    w = json.put_bool(heap, w, true);\n    w = json.end_object(heap, w);\n    w = json.end_object(heap, w);\n    return send(heap, io, w);\n}\n\nfn call", "    w = json.put_key(heap, w, \"isError\");\n    w = json.put_bool(heap, w, false);\n    w = json.end_object(heap, w);\n    w = json.end_object(heap, w);\n    return send(heap, io, w);\n}\n\nfn call"),
    ("the default deadline is one millisecond", "mcp.cho", "    var timeout = tools.default_timeout();", "    var timeout = 1;"),
    # the command line and the description
    ("a relative root is accepted", "mcp.cho", "        if len(value) == 0 || value[0] != byte_of('/') {", "        if len(value) == 0 {"),
    ("a deadline of zero is accepted", "mcp.cho", "            timeout = nat_of(value);\n            if timeout <= 0 {", "            timeout = nat_of(value);\n            if timeout < 0 {"),
    ("introspect is not asked for", "mcp.cho", "if arg_count(g) == 2 && bytes.equal(arg(g, 1), \"introspect\") {", "if arg_count(g) == 3 && bytes.equal(arg(g, 1), \"introspect\") {"),
    # the generator: the schema and the gate
    ("the ceiling of a count is not in the schema", "../scripts/mcp.py", "            s[\"maximum\"] = ceiling[name]", "            pass"),
    ("a count may be negative in the schema", "../scripts/mcp.py", "        s = {\"type\": \"integer\", \"minimum\": 0}", "        s = {\"type\": \"integer\"}"),
    ("a choice offers only its first word", "../scripts/mcp.py", "        s = {\"enum\": kind[len(\"choice:\"):].split(\"/\")}", "        s = {\"enum\": kind[len(\"choice:\"):].split(\"/\")[:1]}"),
    ("a text may be empty in the schema", "../scripts/mcp.py", "            s[\"minLength\"] = 1", "            s[\"minLength\"] = 0"),
    ("a default is not in the schema", "../scripts/mcp.py", "    if flag[\"default\"] is not None:\n        s[\"default\"]", "    if False:\n        s[\"default\"]"),
    ("the help is not in the schema", "../scripts/mcp.py", "    s[\"description\"] = flag[\"help\"]", "    s[\"description\"] = \"\""),
    ("the root is offered to the model", "../scripts/mcp.py", "WITHHELD_ROLES = {\"root\"}", "WITHHELD_ROLES = set()"),
    ("a count is a text to the server", "../scripts/mcp.py", "\"bool\" if f[\"kind\"] == \"bool\" else \"nat\" if f[\"kind\"] == \"nat\" else \"text\"", "\"bool\" if f[\"kind\"] == \"bool\" else \"text\""),
    ("the operand is not required", "../scripts/mcp.py", "        if o[\"min\"] > 0:\n            required.append(prop)", "        if False:\n            required.append(prop)"),
    ("the operand is an array", "../scripts/mcp.py", "        if o[\"max\"] == 1:\n            s = {\"type\": \"string\"", "        if False:\n            s = {\"type\": \"string\""),
    ("the format does not choose the structure", "../scripts/mcp.py", "        selector = \"format\"", "        selector = \"\""),
    ("the tool is not read-only", "../scripts/mcp.py", "    read_only = not (effects & {", "    read_only = False and not (effects & {"),
    ("the default bound is one mebibyte", "../scripts/mcp.py", "DEFAULT_MAX_OUTPUT = 2097152 ", "DEFAULT_MAX_OUTPUT = 1048576 "),
    ("the gate allows any label", "../scripts/mcp.py", "        if text not in allowed:", "        if False:"),
    ("the gate forbids nothing", "../scripts/mcp.py", "        if label[\"name\"] in ceiling[\"forbid\"]:", "        if False:"),
    ("the gate lets exec be anywhere", "../scripts/mcp.py", "        if label[\"name\"] == \"exec\" and label[\"argument\"] != bin_dir:", "        if False:"),
    ("the gate lets the row be unbounded", "../scripts/mcp.py", "    if not authority.get(\"bounded\", False):", "    if False:"),
    ("a drifting authority is a fixed point", "../scripts/mcp.py", "    if second != first:", "    if False:"),
    ("a hint may name a flag that is gone", "../scripts/mcp.py", "        if p not in props:", "        if False:"),
]


def build_and_test(tests, compiler, extra):
    """No `cancho build` of the tool: the tool is built. A mutant is built as a server (the baked directory is this
    checkout's build/), and refused if it does not build; the tests then build it again as they run."""
    with tempfile.TemporaryDirectory() as d:
        b = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp.py"), "build", "--bin", str((ROOT / "build").resolve()), "--out", str(pathlib.Path(d) / "mcp")],
                           cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "does not build", (b.stderr or b.stdout).strip()[-140:]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "-f", *tests], cwd=ROOT / "tests" / "conformance",
                       capture_output=True, text=True, timeout=3600, env={**os.environ, "MCP_MUTANT_RUN": "1"})
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


if __name__ == "__main__":
    mutlib.SRC = SERVER
    mutlib.build_and_test = build_and_test
    sys.exit(mutlib.main(MUTANTS, TESTS))
