"""Shared pieces of the conformance gates, after cancho-tools' harness.

The binary is build/table, built by `cancho build` before the tests run
(TOOLBOX_BIN overrides the directory). Every test runs a real process and
judges it from outside: exit status, the bytes on standard output.
"""

import base64
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build"))
TRAPS = {132, 134, 136, 139, -4, -6, -8, -11}

_schema = None
_introspect = None


def binary():
    return str(BIN / "table")


class Result:
    def __init__(self, argv, status, stdout, stderr):
        self.argv = argv
        self.status = status
        self.stdout = stdout
        self.stderr = stderr

    def doc(self):
        lines = self.stdout.decode("utf-8").splitlines()
        assert len(lines) == 1, "a document tool writes one line: %r" % self.stdout[:400]
        return json.loads(lines[0])

    def data(self):
        return self.doc().get("data")

    def first_rule(self):
        errs = self.doc().get("errors", [])
        return errs[0]["rule"] if errs else None

    def error(self):
        return self.doc()["error"]

    def __repr__(self):
        return "<table %r -> %d %r>" % (self.argv[1:], self.status, self.stdout[:300])


def run(*args, cwd=None, timeout=120, stdin=None):
    """One run of the tool. Standard input is empty unless `stdin` (bytes) is given: a test never inherits the runner's, since a run with no
    operand reads it (docs/readers.md section 3).

    TABLE_STDIN_DIFF=1 makes every run of a regular file under `--root` also a differential: the same bytes through standard input (named `-`, and with no
    operand) must give the same answer but for the file's name where the answer says it (`detail.path` is `-`). Nothing else about a test changes."""
    argv = [binary(), *[str(a) for a in args]]
    p = subprocess.run(argv, capture_output=True, cwd=cwd, timeout=timeout, input=stdin if stdin is not None else b"")
    result = Result(argv, p.returncode, p.stdout, p.stderr)
    if stdin is None and os.environ.get("TABLE_STDIN_DIFF"):
        via_stdin(args, result, cwd, timeout)
    return result


def run_argv(argv, cwd=None, timeout=120):
    p = subprocess.run(argv, capture_output=True, cwd=cwd, input=b"", timeout=timeout)
    result = Result(argv, p.returncode, p.stdout, p.stderr)
    if os.environ.get("TABLE_STDIN_DIFF") and str(argv[0]) == binary():
        via_stdin(argv[1:], result, cwd, timeout)
    return result


STDIN_COMPARED = [0, 0]


def _report_stdin_compared():
    if os.environ.get("TABLE_STDIN_DIFF"):
        import sys
        print("\nTABLE_STDIN_DIFF: %d runs of a file also run through standard input (named -, and with no operand): %d comparisons, all equal" % (STDIN_COMPARED[0], STDIN_COMPARED[1]), file=sys.stderr)


import atexit  # noqa: E402

atexit.register(_report_stdin_compared)


def operand_file(args, cwd):
    """(root, name, index) when `args` is `--root DIR ... NAME` with NAME a plain relative regular file under DIR and nothing else an operand, else None."""
    strs = [str(a) for a in args]
    if "--root" not in strs or not strs:
        return None
    root = strs[strs.index("--root") + 1]
    name = strs[-1]
    if strs[-2] == "--root" or name.startswith("-") or name == "" or name.startswith("/") or ".." in name.split("/"):
        return None
    path = os.path.join(root, name)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    return root, name, len(strs) - 1


def normalised(data, name, operand=True):
    """The answer with the file's name where it says the path (`detail.path`) and where a repair repeats the invocation (its last element): `-` for standard
    input, or nothing when the invocation had no operand."""
    data = data.replace(b'"path":%s' % json.dumps(name).encode(), b'"path":"-"')
    return data.replace(b',%s]' % json.dumps(name).encode(), b',"-"]' if operand else b"]")


def via_stdin(args, result, cwd, timeout):
    found = operand_file(args, cwd)
    if found is None:
        return
    root, name, at = found
    STDIN_COMPARED[0] += 1
    strs = [str(a) for a in args]
    data = open(os.path.join(root, name), "rb").read()
    for form in (strs[:at] + ["-"], strs[:at]):
        operand = form[-1:] == ["-"]
        want = (result.status, normalised(result.stdout, name, operand), normalised(result.stderr, name, operand))
        p = subprocess.run([binary(), *form], capture_output=True, cwd=cwd, timeout=timeout, input=data)
        got = (p.returncode, p.stdout, p.stderr)
        STDIN_COMPARED[1] += 1
        assert got == want, "standard input (%s) answers differently from the file %r:\n  file : %r\n  stdin: %r" % (" ".join(form[-1:] or ["no operand"]), name, want[1][:600], got[1][:600])


def schema():
    global _schema
    if _schema is None:
        _schema = json.loads((ROOT / "schemas" / "table.v2.json").read_text())
    return _schema


def introspect():
    global _introspect
    if _introspect is None:
        p = subprocess.run([binary(), "introspect"], capture_output=True, check=True)
        _introspect = json.loads(p.stdout)
    return _introspect


def validate(result):
    """The output is valid against the schema, the status is in the declared
    table, and nothing reaches standard error in JSON mode. Answers a list of
    problems (empty when it conforms)."""
    problems = []
    if result.status in TRAPS or result.status < 0:
        return ["trapped: status %d" % result.status]
    declared = {c["code"] for c in introspect()["exit_codes"]}
    if result.status not in declared:
        problems.append("status %d not in the declared table %s" % (result.status, sorted(declared)))
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return problems + ["stdout is not UTF-8"]
    if not text.endswith("\n"):
        problems.append("output does not end with a newline")
    lines = text.splitlines()
    if len(lines) != 1:
        problems.append("a document tool wrote %d lines" % len(lines))
    validator = jsonschema.Draft202012Validator(schema())
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError as e:
            problems.append("not JSON: %s" % e)
            continue
        for err in validator.iter_errors(value):
            problems.append("schema: %s at %s" % (err.message[:200], list(err.absolute_path)))
        if value.get("ok") is False and result.status == 0:
            problems.append("ok:false with exit 0")
        if value.get("ok") is True and result.status != 0:
            problems.append("ok:true with exit %d" % result.status)
        if value.get("errors") and value["error"] != value["errors"][0]:
            problems.append("error is not errors[0]")
    if result.stderr and "--format" not in " ".join(result.argv):
        problems.append("stderr is not empty in JSON mode: %r" % result.stderr[:200])
    return problems


def name_text(item):
    """A header name as bytes, from text_or_bytes."""
    if isinstance(item, dict):
        return base64.b64decode(item["b64"])
    return item.encode("utf-8")


def package_catalogue():
    """The rule catalogue of the installed cancho-tools package (toolbox.rules,
    in build/deps): tag -> (exit code, repairable). Empty when not installed."""
    for f in sorted((ROOT / "build" / "deps").glob("*.cho")):
        text = f.read_text()
        if "module toolbox.rules;" not in text:
            continue
        body = re.search(r'pub fn catalogue\(\) -> \[\] &static \[byte\] \{\s*return "(.*?)";', text, re.S).group(1)
        out = {}
        for entry in body.split(";"):
            tag, code, repairable, summary = entry.split("|")
            out[tag] = (int(code), repairable)
        return out
    return {}


class Scratch:
    """A scratch directory that is the --root of a test."""

    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="table-"))

    def write(self, name, data):
        p = self.dir / name
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def table(self, name, data, *flags):
        self.write(name, data)
        return run("--root", self.dir, *flags, name)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
