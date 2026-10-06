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
and docs/benchmarks.html is hand-written, and so are the benchmark numbers** (they
come from machines this script cannot reach; each is copied from the document it
cites, next to its conditions). What is generated:

    t-NAME        README.md, index.html   the example for one task: the command and
                                          what the built binary prints for it
    hero          index.html              the first example
    authority     index.html              the row of manifests/table.authority.json
                                          (checked against `table introspect`, and
                                          for net, ffi and clock)
    limits        index.html              the limits `table introspect` lists
    rules         index.html              the rules `table introspect` lists

The commands run in a temporary directory on a fixed fixture, as `table` found on
PATH (so a repair that names the tool says `table`). Standard output and standard
error are one stream, as in a terminal. Needs the standard library and the built
binary.
"""

import argparse
import difflib
import html
import json
import os
import shlex
import subprocess
import sys
import tempfile
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
    "select": "table --select customer,bytes --format csv orders.csv",
    "where": """table --where "bytes != '' and bytes:int > 100" --select id,customer --format csv orders.csv""",
    "group": """table --where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --format csv orders.csv""",
    "page1": "table --select id,customer --limit 2 orders.csv",
    "page2": "table --select id,customer --limit 2 --from 2 orders.csv",
    "csv": """table --where "status = 200" --format csv orders.csv""",
    "cores": "table --threads 4 --group status --agg count --format csv orders.csv",
    "unknown": "table --select Status orders.csv",
}
# task name -> the demos shown for it
TASKS = {
    "select": ["select"], "filter": ["where"], "group": ["group"], "page": ["page1", "page2"],
    "csv": ["csv"], "cores": ["cores"], "refuse": ["unknown"],
}
FORBIDDEN = {"ffi", "net_out", "net_in", "clock"}


class Drift(Exception):
    pass


def e(s):
    return html.escape(s, quote=False)


# --- running things ----------------------------------------------------------


def run(display, binary, tmp):
    """Run a typed command in the fixture directory, `table` being the binary
    (named `table` in argv). Returns (output, exit status)."""
    words = shlex.split(display)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    r = subprocess.run(words, executable=str(binary), cwd=tmp, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.stdout.decode("utf-8"), r.returncode


def introspect(binary, tmp):
    r = subprocess.run(["table", "introspect"], executable=str(binary), cwd=tmp,
                       stdout=subprocess.PIPE, check=True)
    return json.loads(r.stdout)


def shown(ctx, key):
    """(typed command, what is printed, status). The refusal is shown as the three
    members of the JSON line that matter, since the line is long."""
    out, status = ctx["run"](DEMOS[key])
    if key == "unknown":
        doc = json.loads(out)
        err = doc["error"]
        if doc["ok"] or status == 0:
            raise Drift("the demo %s no longer refuses" % key)
        out = json.dumps({"rule": err["rule"], "hint": err["hint"], "repair": err["repair"]}, ensure_ascii=False)
        return DEMOS[key], out, status, "the rule, the hint and the repair from the JSON line it prints"
    return DEMOS[key], out, status, None


def lines(ctx, task, html_mode):
    out = []
    for key in TASKS[task]:
        display, text, status, note = shown(ctx, key)
        if html_mode:
            out.append('<span class="c">$</span> ' + e(display))
            out.append(e(text.rstrip("\n")))
            if status != 0:
                out.append('<span class="r"># exit status %d%s</span>' % (status, "; " + e(note) if note else ""))
        else:
            out.append("$ " + display)
            out.append(text.rstrip("\n"))
            if status != 0:
                out.append("# exit status %d%s" % (status, "; " + note if note else ""))
    return "\n".join(out)


def task_md(task):
    return lambda ctx: "```console\n" + lines(ctx, task, False) + "\n```"


def task_html(task):
    return lambda ctx: '<pre class="code" tabindex="0">' + lines(ctx, task, True) + "</pre>"


def hero(ctx):
    display, text, status, _ = shown(ctx, "group")
    return ('<div class="term" role="img" aria-label="One command on a small file and what it prints">'
            '<div class="bar"><i></i><i></i><i></i></div>\n<pre>' + lines(ctx, "group", True) + "</pre></div>")


def authority(ctx):
    manifest = json.loads((ROOT / "manifests" / "table.authority.json").read_text())
    if manifest != ctx["introspect"]["authority"]:
        raise Drift("manifests/table.authority.json is not what `table introspect` says: run scripts/manifest.py")
    names = []
    for lab in manifest["labels"]:
        names.append(lab["name"] + ('("%s")' % lab["argument"] if lab["argument"] is not None else ""))
    effects = {lab["name"] for lab in manifest["labels"]}
    if not manifest["bounded"] or manifest["foreign_symbols"] or effects & FORBIDDEN:
        raise Drift("the authority has a forbidden label, a foreign symbol, or is unbounded")
    return ('<pre class="code" tabindex="0"><span class="c"># manifests/table.authority.json</span>\n'
            'table    %s\n<span class="c"># bounded: %s; foreign symbols: none; net_out, net_in, ffi, clock: absent</span></pre>'
            % (e(", ".join(names)), str(manifest["bounded"]).lower()))


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
    return ('<div class="fitwrap"><table class="fit"><thead><tr><th>rule</th><th>exit</th><th>a repair?</th><th>what it refuses</th></tr></thead><tbody>%s</tbody></table></div>'
            % rows), len(rs)


def rules_region(ctx):
    return rules(ctx)[0]


def rules_count(ctx):
    return str(rules(ctx)[1])


REGIONS = {
    "README.md": {"t-" + t: task_md(t) for t in TASKS},
    "docs/index.html": dict({"t-" + t: task_html(t) for t in TASKS},
                            hero=hero, authority=authority, limits=limits, rules=rules_region, nrules=rules_count),
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
