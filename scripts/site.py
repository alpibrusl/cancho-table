#!/usr/bin/env python3
"""Regenerate the parts of the README and of the project pages that are program
output, from the built binary, so that they cannot drift from it.

    python3 scripts/site.py            # rewrite the generated regions
    python3 scripts/site.py --check    # change nothing; exit 1 if a file differs
    python3 scripts/site.py --bin build/table

A generated region is a pair of comment lines in a file,

    <!-- gen:NAME -->
    ...whatever the script writes...
    <!-- /gen:NAME -->

and nothing outside a pair is touched: **the prose of README.md, docs/index.html
and docs/evidence.html is hand-written, and so are the benchmark numbers** (they
come from machines this script cannot reach; each is copied from the document it
cites, next to its conditions). What is generated:

    readme-demo   README.md      the example commands and their real output
    hero          index.html     a terminal with three of them
    demo          index.html     the five of the README, in a block
    problem       index.html     cut and awk on the same file, then table
    refusals      index.html     two real refusals, as the tool prints them
    authority     index.html     the row of manifests/table.authority.json (and
                                 that it is what the binary says, and has no
                                 net, ffi or clock)
    limits        index.html     the limits `table introspect` lists
    rules         index.html     the rules `table introspect` lists
    counts        evidence.html  the conformance tests and the mutants, counted

The commands run in a temporary directory on a fixed fixture, as `table` found on
PATH (so a repair that names the tool says `table`). Standard output and standard
error are one stream, as in a terminal. Needs the standard library, the built
binary, and `cut` and `awk`; counting the tests imports them (jsonschema).
"""

import argparse
import difflib
import html
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FIXTURE = """id,customer,status,bytes
1,"Doe, Jane",200,512
2,Acme,404,
3,"Doe, Jane",200,2048
4,Acme,500,128
5,Zed,200,64
"""

# What is shown is what is typed; the argv run is shlex.split of it.
DEMOS = {
    "shape": "table orders.csv",
    "select": "table --select customer,bytes --format csv orders.csv",
    "where": """table --where "bytes != '' and bytes:int > 100" --select id,customer --format csv orders.csv""",
    "group": """table --where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --format csv orders.csv""",
    "refuse": "table --where 'bytes:int>100' --select id --format csv orders.csv",
    "unknown": "table --select Status orders.csv",
    "notint": "table --where 'bytes:int>100' orders.csv",
    "sum": "table --agg sum:bytes --format csv orders.csv",
}
README_DEMOS = ["shape", "select", "where", "group", "refuse"]
HERO_DEMOS = ["select", "group", "refuse"]
SHELL = {
    "cut": "cut -d, -f2,4 orders.csv",
    "awk": """awk -F, '{s+=$4} END {print s}' orders.csv""",
}

# Hand-written glosses of authority labels (the labels themselves are the compiler's).
GLOSS = {
    "args": "reads its command line",
    "conc": "threads, for --threads",
    "dir_read": "directory reads, to open the path",
    "err_write": "writes standard error",
    "file_read": "reads the input file",
    'fs_read("")': "a path given at run time",
    "heap": "allocates memory",
    "io_write": "writes standard output",
}
FORBIDDEN = {"ffi", "net_out", "net_in", "clock"}


class Drift(Exception):
    pass


def e(s):
    return html.escape(s, quote=False)


# --- running things ----------------------------------------------------------


def run(display, binary, tmp):
    """Run a typed command in the fixture directory. `table` is the binary,
    named `table` in argv. Returns (output, exit status)."""
    words = shlex.split(display)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    if words[0] == "table":
        r = subprocess.run(words, executable=str(binary), cwd=tmp, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run(words, cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.stdout.decode("utf-8"), r.returncode


def introspect(binary, tmp):
    r = subprocess.run(["table", "introspect"], executable=str(binary), cwd=tmp,
                       stdout=subprocess.PIPE, check=True)
    return json.loads(r.stdout)


# --- the regions ---------------------------------------------------------------


def transcript(items):
    """[(display, output, status)] as lines of a terminal."""
    out = []
    for display, text, status in items:
        out.append("$ " + display)
        out.append(text.rstrip("\n"))
        if status != 0:
            out.append("# exit status %d" % status)
    return "\n".join(l for l in out if l is not None)


def readme_demo(ctx):
    items = [(DEMOS[k],) + ctx["run"](DEMOS[k]) for k in README_DEMOS]
    return "```console\n" + transcript(items) + "\n```"


def term_html(items):
    lines = []
    for display, text, status in items:
        lines.append('<span class="c">$</span> ' + e(display))
        lines.append(e(text.rstrip("\n")))
        if status != 0:
            lines.append('<span class="r"># exit status %d</span>' % status)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def hero(ctx):
    items = [(DEMOS[k],) + ctx["run"](DEMOS[k]) for k in HERO_DEMOS]
    return ('<div class="term" role="img" aria-label="Three example commands on a small file and what they print">'
            '<div class="bar"><i></i><i></i><i></i></div>\n<pre>' + term_html(items) + "</pre></div>")


def demo(ctx):
    items = [(DEMOS[k],) + ctx["run"](DEMOS[k]) for k in README_DEMOS]
    return '<pre class="code" tabindex="0">' + term_html(items) + "</pre>"


def problem(ctx):
    items = [(SHELL["cut"],) + ctx["run"](SHELL["cut"]),
             (SHELL["awk"],) + ctx["run"](SHELL["awk"])]
    tbl = [(DEMOS["select"],) + ctx["run"](DEMOS["select"]),
           (DEMOS["sum"],) + ctx["run"](DEMOS["sum"])]
    return ('<div class="two">\n<div class="figure"><h3>cut and awk, on the file</h3>\n<pre class="code" tabindex="0">'
            + term_html(items) + '</pre></div>\n<div class="figure"><h3>table, on the same file</h3>\n<pre class="code" tabindex="0">'
            + term_html(tbl) + "</pre></div>\n</div>")


def refusals(ctx):
    parts = []
    for key, title in (("unknown", "A name that is not in the header"), ("notint", "A cell that is not an integer")):
        out, status = ctx["run"](DEMOS[key])
        doc = json.loads(out)
        if doc["ok"] or status == 0:
            raise Drift("the demo %s no longer refuses" % key)
        body = json.dumps(doc["error"], indent=2, ensure_ascii=False)
        parts.append('<figure class="figure"><h3>%s</h3>\n<pre class="code" tabindex="0"><span class="c">$</span> %s\n'
                     '<span class="c"># exit status %d; the "error" member of the one line printed, reformatted</span>\n%s</pre></figure>'
                     % (e(title), e(DEMOS[key]), status, e(body)))
    return '<div class="two">\n' + "\n".join(parts) + "\n</div>"


def authority(ctx):
    manifest = json.loads((ROOT / "manifests" / "table.authority.json").read_text())
    said = ctx["introspect"]["authority"]
    if manifest != said:
        raise Drift("manifests/table.authority.json is not what `table introspect` says: run scripts/manifest.py")
    names = []
    for lab in manifest["labels"]:
        names.append(lab["name"] + ('("%s")' % lab["argument"] if lab["argument"] is not None else ""))
    effects = {lab["name"] for lab in manifest["labels"]}
    if not manifest["bounded"] or manifest["foreign_symbols"] or effects & FORBIDDEN:
        raise Drift("the authority has a forbidden label, a foreign symbol, or is unbounded")
    rows = "".join("<tr><th><code>%s</code></th><td>%s</td></tr>" % (e(n), e(GLOSS.get(n, "(no gloss written)")))
                   for n in names)
    return ('<pre class="code" tabindex="0"><span class="c"># manifests/table.authority.json, as `python3 scripts/manifest.py --check` prints it</span>\n'
            'table    %s\n<span class="c"># bounded: %s; foreign symbols: %s; net_out, net_in, ffi, clock: absent</span></pre>\n'
            '<div class="fitwrap"><table class="fit auth"><thead><tr><th>label</th><th>what it lets the program do <span class="note">(the gloss is hand-written)</span></th></tr></thead><tbody>%s</tbody></table></div>'
            % (e(", ".join(names)), str(manifest["bounded"]).lower(), "none" if not manifest["foreign_symbols"] else "some", rows))


def limits(ctx):
    rows = "".join("<tr><th><code>--%s</code></th><td class=\"num\">%s</td><td class=\"num\">%s</td></tr>"
                   % (e(l["name"]), format(l["default"], ","), format(l["ceiling"], ","))
                   for l in ctx["introspect"]["limits"])
    return ('<div class="fitwrap"><table class="fit"><thead><tr><th>limit</th><th>default</th><th>ceiling</th></tr></thead><tbody>'
            + rows + "</tbody></table></div>")


def rules(ctx):
    rs = ctx["introspect"]["rules"]
    rows = "".join("<tr><th><code>%s</code></th><td class=\"num\">%d</td><td>%s</td><td>%s</td></tr>"
                   % (e(r["rule"]), r["exit"], e(r["repairable"]), e(r["summary"])) for r in rs)
    return ('<details class="rules"><summary>All %d rules, as <code>table introspect</code> lists them</summary>'
            '<div class="fitwrap"><table class="fit"><thead><tr><th>rule</th><th>exit</th><th>a repair?</th><th>what it refuses</th></tr></thead><tbody>%s</tbody></table></div></details>'
            % (len(rs), rows))


def load_mutants(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return len(mod.MUTANTS)


def counts(ctx):
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests" / "conformance"))
    tests = suite.countTestCases()
    muts = [(n, load_mutants(n)) for n in ("select_mutants", "filter_mutants", "parallel_mutants", "cellcost_mutants")]
    rows = "".join('<tr><th><code>scripts/%s.py</code></th><td class="num">%d</td></tr>' % (n, c) for n, c in muts)
    return ('<div class="stats"><div><b>%d</b><span>conformance tests (<code>tests/conformance</code>, counted by the unittest loader)</span></div>'
            '<div><b>%d</b><span>mutants listed across the four scripts</span></div></div>\n'
            '<div class="fitwrap"><table class="fit"><thead><tr><th>mutant list</th><th>mutants</th></tr></thead><tbody>%s</tbody></table></div>'
            % (tests, sum(c for _, c in muts), rows))


REGIONS = {
    "README.md": {"readme-demo": readme_demo},
    "docs/index.html": {"hero": hero, "demo": demo, "problem": problem, "refusals": refusals, "authority": authority,
                        "limits": limits, "rules": rules},
    "docs/evidence.html": {"counts": counts},
}


def splice(text, name, body):
    start = "<!-- gen:%s -->\n" % name
    end = "<!-- /gen:%s -->" % name
    i = text.find(start)
    j = text.find(end, i + 1)
    if i < 0 or j < 0:
        raise Drift("no <!-- gen:%s --> region in the file" % name)
    return text[:i] + start + body + "\n" + text[j:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 if a file differs")
    args = ap.parse_args()
    binary = Path(args.bin).resolve()
    if not binary.exists():
        sys.exit("site.py: %s does not exist: run `lex-sys build` first" % binary)
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "orders.csv").write_text(FIXTURE)
        ctx = {"run": lambda display: run(display, binary, tmp), "introspect": introspect(binary, tmp)}
        for rel, regions in REGIONS.items():
            path = ROOT / rel
            old = path.read_text()
            new = old
            for name, fn in regions.items():
                new = splice(new, name, fn(ctx))
            if new == old:
                continue
            if args.check:
                bad += 1
                sys.stderr.write("site.py: %s differs from what the binary produces; run `python3 scripts/site.py`\n" % rel)
                for k, line in enumerate(difflib.unified_diff(old.splitlines(), new.splitlines(), "committed", "generated", lineterm="", n=0)):
                    if k >= 30:
                        sys.stderr.write("...\n")
                        break
                    sys.stderr.write(line[:200] + "\n")
            else:
                path.write_text(new)
                print("wrote", rel)
    if args.check and not bad:
        print("site.py: generated regions are current")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Drift as exc:
        sys.exit("site.py: %s" % exc)
