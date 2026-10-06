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
    flow          README.md, index.html   one complete flow: a command, a refusal with
                                          its repair, the repair run
    qs            README.md, index.html   the quick start's commands and output
    hero          index.html              the first example
    agentio       index.html              excerpts of `table introspect`
    skill         index.html              the first lines of `table skill`
    exit-codes, rules, examples   docs/refusals.md   the refusal protocol, from introspect
                                          and from real refusals
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

# A second small file, for the decimal examples
PRICES = """item,price
pen,1.50
book,12.50
lamp,12.5
cup,3.2
"""

# What is shown is what is typed; the argv run is shlex.split of it.
DEMOS = {
    "shape": "table orders.csv",
    "select": "table --select customer,bytes --format csv orders.csv",
    "where": """table --where "bytes != '' and bytes:int > 100" --select id,customer --format csv orders.csv""",
    "group": """table --where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --format csv orders.csv""",
    "page1": "table --select id,customer --limit 2 orders.csv",
    "page2": "table --select id,customer --limit 2 --from 2 orders.csv",
    "csv": """table --where "status = 200" --format csv orders.csv""",
    "dec": """table --where "price:dec(2) >= 12.50" --format csv prices.csv""",
    "dec_bad": """table --where "price:dec(1) >= 12.5" prices.csv""",
    "sort": """table --where "bytes != ''" --order-by -bytes:int,status --limit 3 --select id,customer,bytes --format csv orders.csv""",
    "cores": "table --threads 4 --group status --agg count --format csv orders.csv",
    "notint": """table --where "bytes:int > 100" --select id orders.csv""",
    "flow_bad": """table --where "status = 200" --select Customer,bytes orders.csv""",
    "retry_bad": "table --max-line-bytes 10 orders.csv",
}
# demos shown as the members of the JSON line that matter, since the line is long
SUMMARY = {"dec_bad": ["rule", "hint", "repair"], "flow_bad": ["rule", "hint", "repair"], "notint": ["rule", "hint", "detail"], "retry_bad": ["rule", "hint", "repair"]}
# task name -> the demos shown for it
TASKS = {
    "select": ["select"], "filter": ["where"], "decimal": ["dec", "dec_bad"], "sort": ["sort"], "group": ["group"], "page": ["page1", "page2"],
    "csv": ["csv"], "cores": ["cores"], "refuse": ["notint"],
}
QS = ["shape", "select", "group"]
FORBIDDEN = {"ffi", "net_out", "net_in", "clock"}


class Drift(Exception):
    pass


def e(s):
    return html.escape(s, quote=False)


# --- running things ----------------------------------------------------------


def run_words(words, binary, tmp):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    r = subprocess.run(words, executable=str(binary), cwd=tmp, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.stdout.decode("utf-8"), r.returncode


def introspect(binary, tmp):
    r = subprocess.run(["table", "introspect"], executable=str(binary), cwd=tmp,
                       stdout=subprocess.PIPE, check=True)
    return json.loads(r.stdout)


def item(ctx, key):
    """(typed command, what is printed, status, note). A refusal is shown as the
    members of its JSON line that matter."""
    words = shlex.split(DEMOS[key])
    out, status = ctx["run"](words)
    if key in SUMMARY:
        doc = json.loads(out)
        if doc["ok"] or status == 0:
            raise Drift("the demo %s no longer refuses" % key)
        err = doc["error"]
        out = json.dumps({k: err[k] for k in SUMMARY[key]}, ensure_ascii=False)
        return DEMOS[key], out, status, "the %s of the JSON line it prints" % ", ".join(SUMMARY[key])
    return DEMOS[key], out, status, None


def flow_items(ctx):
    """Ask, be refused with a repair, run the repair."""
    first = item(ctx, "flow_bad")
    out, status = ctx["run"](shlex.split(DEMOS["flow_bad"]))
    repair = json.loads(out)["error"]["repair"]
    if repair is None or repair["kind"] != "choose":
        raise Drift("the flow's refusal no longer carries a choose repair")
    argv = repair["options"][0]["argv"]
    out2, status2 = ctx["run"](argv)
    if status2 != 0:
        raise Drift("the repair of the flow does not run")
    return [("# Ask: the customer and bytes of the orders with status 200", None, 0, None),
            first,
            ("# The tool offered the corrected command. Run it:", None, 0, None),
            (shlex.join(argv), out2, status2, None)]


def render(items, html_mode):
    out = []
    for display, text, status, note in items:
        if text is None:
            out.append(('<span class="c">%s</span>' % e(display)) if html_mode else display)
            continue
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


def md_block(items):
    return "```console\n" + render(items, False) + "\n```"


def html_pre(items):
    return '<pre class="code" tabindex="0">' + render(items, True) + "</pre>"


def task_items(ctx, task):
    return [item(ctx, k) for k in TASKS[task]]


def hero(ctx):
    return ('<div class="term" role="img" aria-label="One command on a small file and what it prints">'
            '<div class="bar"><i></i><i></i><i></i></div>\n<pre>' + render([item(ctx, "group")], True) + "</pre></div>")


def qs_items(ctx):
    return [item(ctx, k) for k in QS] + [item(ctx, "flow_bad")]


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


def rules_html(ctx):
    rs = ctx["introspect"]["rules"]
    rows = "".join("<tr><th><code>%s</code></th><td class=\"num\">%d</td><td>%s</td><td>%s</td></tr>"
                   % (e(r["rule"]), r["exit"], e(r["repairable"]), e(r["summary"])) for r in rs)
    return ('<div class="fitwrap"><table class="fit"><thead><tr><th>rule</th><th>exit</th><th>a repair?</th><th>what it refuses</th></tr></thead><tbody>%s</tbody></table></div>'
            % rows)


def nrules(ctx):
    return str(len(ctx["introspect"]["rules"]))


def cut(s, n):
    return s if len(s) <= n else s[: n - 1] + "\u2026"


SAMPLE_RULES = ["args.unknown-flag", "select.unknown-column", "value.not-integer", "limit.line-too-long", "limit.too-many-groups", "limit.too-many-sort-rows"]
SAMPLE_FLAGS = ["--root", "--select", "--where", "--order-by", "--group", "--agg", "--threads"]


def agentio(ctx):
    d = ctx["introspect"]
    flags = {f["name"]: f for f in d["flags"]}
    rules = {r["rule"]: r for r in d["rules"]}
    lim = {l["name"]: l for l in d["limits"]}
    try:
        L = ["$ table introspect          # one JSON document; excerpts, long lines cut", "",
             "tool        %s %s   (compiler %s)" % (d["tool"], d["version"], d["compiler"][:8]),
             "flags       %d, for example" % len(d["flags"])]
        for n in SAMPLE_FLAGS:
            f = flags[n]
            L.append("  %-10s %-5s %s" % (n, f["kind"], cut(f["help"], 62)))
        L.append("limits      %d, for example" % len(d["limits"]))
        for n in ("max-rows", "max-groups", "max-sort-rows", "max-state-bytes", "threads"):
            L.append("  --%-16s default %-12s ceiling %s" % (n, format(lim[n]["default"], ","), format(lim[n]["ceiling"], ",")))
        L.append("rules       %d, for example" % len(d["rules"]))
        for n in SAMPLE_RULES:
            r = rules[n]
            L.append("  %-24s exit %d   a repair: %s" % (n, r["exit"], r["repairable"]))
        g = d["guarantees"]
        L += ["guarantees  deterministic: %s   bounded memory: %s   read-only" % (str(g["deterministic"]).lower(), str(g["bounded_memory"]).lower()),
              "            reads environment: %s   reads clock: %s   integers only: %s"
              % (str(d["reads_environment"]).lower(), str(d["reads_clock"]).lower(), str(d["integers_only"]).lower()),
              "authority   " + ", ".join(l["name"] + ('("%s")' % l["argument"] if l["argument"] is not None else "") for l in d["authority"]["labels"]),
              "            bounded: %s   foreign symbols: %d" % (str(d["authority"]["bounded"]).lower(), len(d["authority"]["foreign_symbols"]))]
    except KeyError as exc:
        raise Drift("`table introspect` no longer lists %s" % exc)
    return '<pre class="code" tabindex="0">' + e("\n".join(L)) + "</pre>"


def skill(ctx):
    out, status = ctx["run"](["table", "skill"])
    if status != 0:
        raise Drift("`table skill` failed")
    head = out.splitlines()[:16]
    return ('<pre class="code" tabindex="0">' + e("$ table skill          # a guide in Markdown; the first lines, long lines cut\n"
            + "\n".join(cut(l, 110) for l in head)) + "</pre>")


def exit_codes(ctx):
    rows = "".join("| %d | `%s` | %s |\n" % (c["code"], c["name"], c["meaning"]) for c in ctx["introspect"]["exit_codes"])
    return "| exit | `code` | meaning |\n|---:|---|---|\n" + rows.rstrip("\n")


def rules_md(ctx):
    rows = "".join("| `%s` | %d | %s | %s |\n" % (r["rule"], r["exit"], r["repairable"], r["summary"]) for r in ctx["introspect"]["rules"])
    return "| `rule` | exit | repair | what it refuses |\n|---|---:|---|---|\n" + rows.rstrip("\n")


def examples(ctx):
    blocks = []
    for key, kind in (("retry_bad", "retry"), ("flow_bad", "choose"), ("notint", "none")):
        out, status = ctx["run"](shlex.split(DEMOS[key]))
        err = json.loads(out)["error"]
        if (err["repair"] or {}).get("kind") != kind:
            raise Drift("the %s example no longer has a %s repair" % (key, kind))
        text = "$ %s          # exit status %d\n%s" % (DEMOS[key], status, json.dumps(err, ensure_ascii=False))
        blocks.append("```console\n" + text + "\n```")
    return "\n\n".join(blocks)


REGIONS = {
    "README.md": dict({"t-" + t: (lambda c, t=t: md_block(task_items(c, t))) for t in TASKS},
                      flow=lambda c: md_block(flow_items(c)), qs=lambda c: md_block(qs_items(c))),
    "docs/index.html": dict({"t-" + t: (lambda c, t=t: html_pre(task_items(c, t))) for t in TASKS},
                            hero=hero, flow=lambda c: html_pre(flow_items(c)), qs=lambda c: html_pre(qs_items(c)),
                            agentio=agentio, skill=skill, authority=authority, limits=limits,
                            rules=rules_html, nrules=nrules),
    "docs/refusals.md": {"exit-codes": exit_codes, "rules": rules_md, "examples": examples},
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
        (Path(tmp) / "prices.csv").write_text(PRICES)
        ctx = {"run": lambda words: run_words(words, binary, tmp), "introspect": introspect(binary, tmp)}
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
