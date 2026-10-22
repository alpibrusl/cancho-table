#!/usr/bin/env python3
"""The matrix harness (docs/matrix-benchmark.md): tasks x contenders, one cell per kind, ready to run on the cloud VM for the kinds a shared machine can settle and on the quiet
Mac or gram for the clock and resident-set kinds.

    python3 scripts/bench_matrix.py --check-tools [--tools-dir DIR] [--table PATH]
    python3 scripts/bench_matrix.py [--rows 20000] [--kinds a,b,c,d,g,h] [--results FILE.json]          # the cloud VM: no clock, no resident set
    python3 scripts/bench_matrix.py --clock --rows 1000000 --taskset gram --results FILE.json            # a quiet machine: adds kinds e (peak resident set) and f (time)

Kinds: a expressible, b correct (the answer gate), c deterministic (distinct outputs across threads and repeats), d behaviour at a limit, e peak resident set, f time,
g instructions (callgrind), h error contract. **The clock kinds (e, f) exist only behind `--clock`**, and `--clock` REFUSES to run (before it generates or launches anything)
unless the machine is quiet (load under a quarter of the cores, no virtual machine, no soak or container), the conditions of docs/named-benchmarks.md 3.1 are complete and the
runs are at least five (`matrix.clock-not-quiet`, `matrix.clock-conditions-missing`, `matrix.clock-min-runs`). A time or a resident set is written only for an answer that passed the gate.

A tool that is not installed is a blank cell with the reason and the exact install command (`scripts/bench_matrix_tools.json`); nothing is estimated and nothing is installed by this
script (`--check-tools` lists what is missing). Data is generated from NB1's seeded generator, outside the repository; no dataset is downloaded.

Every refusal has a rule tag (`matrix.*`) and exit status 3; every process is started through `import procs as subprocess`.
"""
import argparse
import csv
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import platform
import re
import shutil
import sys
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
import bench_matrix_forms as forms  # noqa: E402
import bench_named as bn  # noqa: E402

Refusal = forms.Refusal
SCHEMA = "bench-matrix.v1"
MANIFEST = HERE / "bench_matrix_tools.json"
KINDS = list("abcdefgh")
DEFAULT_KINDS = ["a", "b", "c", "d", "g", "h"]     # what a shared virtual machine can settle: no clock, no resident set
CLOCK_KINDS = ["e", "f"]
MIN_RUNS = bn.MIN_RUNS
FORBIDDEN = ("score", "rank", "total", "winner", "overall", "best")
TIME_KEYS = {"min_s", "median_s", "first_s", "all_s", "seconds"}
RSS_KEYS = {"peak_rss_kb", "cpu_s", "rss_floor_kb"}
CLOCK_TASKS = ["X01", "X02", "X03", "X04", "X05", "X06"]
LADDER_MIB = [64, 256, 1024]
SMOKE_ROWS = bn.SMOKE_ROWS
TAG = re.compile(r"^(?:[A-Za-z_-]+: )?([a-z]+\.[a-z0-9-]+):", re.M)


# ---------------------------------------------------------------- the manifest and the tools
def load_manifest(path=None):
    m = json.loads(pathlib.Path(path or MANIFEST).read_text())
    if m.get("schema") != "bench-matrix-tools.v1":
        raise Refusal("matrix.result-invalid", "the tool manifest has another schema")
    return m


def _command_from(argv, path):
    """The command that starts the tool: its path, and the words of the version call that are not the version flag (`clickhouse local`)."""
    return [path] + [a for a in argv[1:] if not a.startswith("-") and a != "version"]


def default_table():
    t = pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build")) / "table"
    return str(t) if t.exists() else None


def detect_tools(manifest, table=None, which=shutil.which, run=bn._run_text, path=None, python=None):
    """One status per contender of the manifest: found, the command, the version read, the pin and whether they agree, and the install commands. Nothing is installed."""
    python = python or sys.executable
    out = []
    for c in manifest["contenders"]:
        st = {"name": c["name"], "family": c["family"], "found": False, "command": None, "path": None, "version": None, "pin": c["pin"], "pinned": None,
              "pin_note": c["pin_note"], "install": c["install"], "install_verified": c["install_verified"], "role": c["role"]}
        d = c["detect"]
        if d["kind"] == "table":
            if table and pathlib.Path(table).exists():
                v = bn.table_version(str(table))
                st.update(found=True, command=[str(pathlib.Path(table).resolve())], path=str(table), version=v["version"], commit=v["commit"], dirty=v["dirty"])
        elif d["kind"] == "python":
            rc, so, se = run([python, "-I", "-c", "import %s; print(%s.__version__)" % (d["module"], d["module"])], timeout=60)
            if rc == 0 and so.strip():
                st.update(found=True, command=[python], path=python, version=so.strip().splitlines()[-1])
        else:
            for argv in [d["argv"], *d.get("alt", [])]:
                p = which(argv[0], path=path)
                if not p:
                    continue
                rc, so, se = run([p, *argv[1:]], timeout=30)
                m = re.search(d["regex"], (so + se)) if rc == 0 else None
                if m:
                    st.update(found=True, command=_command_from(argv, p), path=p, version=m.group(1))
                    break
        if st["found"] and c["pin"]:
            st["pinned"] = st["version"] == c["pin"]
        out.append(st)
    return out


def check_tools_text(statuses, system=None):
    system = system or platform.system()
    key = "macos" if system == "Darwin" else "linux"
    lines = []
    for s in statuses:
        if s["found"]:
            pin = "pinned" if s["pinned"] else ("DIFFERS from the pin %s" % s["pin"] if s["pin"] else "no pin")
            lines.append("found    %-11s %s (%s)  %s" % (s["name"], s["version"], pin, s.get("path")))
        else:
            lines.append("MISSING  %-11s pin %s" % (s["name"], s["pin"] or "none (%s)" % s["pin_note"]))
            lines.append("           install (%s%s): %s" % (key, "" if s["install_verified"] else ", unverified", s["install"][key]))
    return "\n".join(lines) + "\n"


def require_tools(statuses, names):
    by = {s["name"]: s for s in statuses}
    for n in names:
        if n not in by:
            raise Refusal("matrix.args", "unknown contender %s" % n)
    missing = [n for n in names if not by[n]["found"]]
    if missing:
        raise Refusal("matrix.tool-missing", "required and not installed: %s (run --check-tools for the install commands)" % ", ".join(missing))


def not_installed_reason(tool):
    return "not installed (pin %s). Install, Linux: %s | macOS: %s" % (tool["pin"] or "none", tool["install"]["linux"], tool["install"]["macos"])


# ---------------------------------------------------------------- the decision logic: the clock gate and the cells
def clock_gate(c):
    """The conditions of a clock run, judged: complete, a quiet dedicated machine, at least five runs. Raises a Refusal; nothing is measured when it does."""
    miss = bn.missing_conditions(c)
    if miss:
        raise Refusal("matrix.clock-conditions-missing", "the conditions of a number are incomplete: %s" % ", ".join(miss))
    ok, reasons = bn.evaluate_publishable(c)
    if ok:
        return
    runs = [r for r in reasons if r.startswith("runs:")]
    other = [r for r in reasons if not r.startswith("runs:")]
    if other:
        raise Refusal("matrix.clock-not-quiet", "; ".join(other + runs))
    raise Refusal("matrix.clock-min-runs", "; ".join(runs))


def _walk_keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _walk_keys(v)


def validate_cells(cells):
    """The blank-cell rule, the no-score rule and the rule of the clock: raises a Refusal naming the first cell that breaks one."""
    for c in cells:
        where = "%s/%s/%s" % (c.get("task"), c.get("contender"), c.get("kind"))
        if c.get("kind") not in KINDS:
            raise Refusal("matrix.cell-without-kind", "%s has no known kind" % where)
        for k in list(c) + list(_walk_keys(c.get("value"))):
            if str(k).lower() in FORBIDDEN:
                raise Refusal("matrix.score-field", "%s has a field named %s: the matrix has no score, no rank and no winner" % (where, k))
        v, r = c.get("value"), c.get("reason")
        if v is None:
            if not isinstance(r, str) or not r.strip():
                raise Refusal("matrix.blank-without-reason", "%s is blank and gives no reason" % where)
            continue
        if r:
            raise Refusal("matrix.cell-both", "%s has a value and a reason" % where)
        keys = set(v) if isinstance(v, dict) else set()
        if c["kind"] not in CLOCK_KINDS and keys & (TIME_KEYS | RSS_KEYS - {"rss_floor_kb"}):
            raise Refusal("matrix.time-outside-clock", "%s carries a time or a resident set outside kinds e and f" % where)
        if c["kind"] in CLOCK_KINDS:
            if v.get("answer") != "ok":
                raise Refusal("matrix.time-on-failed", "%s carries a measure for an answer that did not pass the gate" % where)
            if c["kind"] == "f":
                if v.get("runs", 0) < MIN_RUNS:
                    raise Refusal("matrix.clock-min-runs", "%s has %s runs, fewer than %d" % (where, v.get("runs"), MIN_RUNS))
                if "all_s" in v and (v.get("min_s") != min(v["all_s"]) or len(v["all_s"]) != v["runs"]):
                    raise Refusal("matrix.time-invalid", "%s: the minimum is not the minimum of its runs" % where)


def validate_result(result):
    if result.get("schema") != SCHEMA:
        raise Refusal("matrix.result-invalid", "not a %s result" % SCHEMA)
    cells = result.get("cells", [])
    validate_cells(cells)
    if any(c["kind"] in CLOCK_KINDS and c.get("value") for c in cells):
        clock_gate(result.get("conditions") or {})


def blank(task, contender, kind, reason):
    return {"task": task, "contender": contender, "kind": kind, "value": None, "reason": reason}


def cell(task, contender, kind, value):
    return {"task": task, "contender": contender, "kind": kind, "value": value, "reason": None}


def _canonical(raw):
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8", "replace"), newline="")))
    except csv.Error:
        return raw
    return rows


def distinct_outputs(outputs, ordered):
    """How many different outputs there were: byte-wise (`raw`) and, with the rows put in a canonical order unless the question is ordered, after parsing (`canonical`)."""
    if not outputs:
        raise Refusal("matrix.no-outputs", "no output to count")
    raw = {hashlib.sha256(o).hexdigest() for o in outputs}
    canon = set()
    for o in outputs:
        rows = _canonical(o)
        canon.add(repr(rows if (ordered or isinstance(rows, bytes)) else [rows[0]] + sorted(rows[1:])))
    return {"raw": len(raw), "canonical": len(canon)}


def slope(n1, c1, n2, c2):
    """Instructions per row and the start-up, from the counts at two row counts; None when the count did not grow with the rows."""
    if n1 == n2:
        raise Refusal("matrix.slope-rows", "the two runs have the same number of rows")
    per = (c2 - c1) / (n2 - n1)
    if per < 0:
        return None
    return {"per_row": per, "startup": c1 - per * n1}


def collected(stderr_text):
    """(instructions, processes) summed over every `Collected : N` callgrind printed (one per traced process), or None."""
    found = [int(x) for x in re.findall(r"Collected\s*:\s*(\d+)", stderr_text)]
    return (sum(found), len(found)) if found else None


def _json_error(stdout):
    try:
        doc = json.loads(stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and (doc.get("ok") is False or "error" in doc) else None


def _rule_and_repair(outcome):
    doc = _json_error(outcome.stdout)
    if doc and isinstance(doc.get("error"), dict):
        e = doc["error"]
        rep = e.get("repair")
        return e.get("rule"), (rep.get("kind") if isinstance(rep, dict) else None)
    m = TAG.search(outcome.stderr.decode("utf-8", "replace"))
    return (m.group(1) if m else None), None


def classify_limit(outcome, ref, family):
    """What happened at a limit: answered-correct / answered-wrong (judged by the gate), killed (a signal or a trap status), timeout, refused-tag (with the rule and the repair kind)
    or refused-message."""
    if outcome.timed_out:
        return {"class": "timeout"}
    rc = outcome.returncode
    if rc is None or rc < 0 or rc >= 128:
        return {"class": "killed", "exit": rc}
    if rc == 0:
        if ref is None:
            return {"class": "answered"}
        j = forms.compare(ref, outcome.stdout)
        return {"class": "answered-correct" if j["status"] == "ok" else "answered-wrong", "level": j["level"], "detail": j["detail"]}
    rule, repair = _rule_and_repair(outcome)
    if rule:
        return {"class": "refused-tag", "rule": rule, "repair": repair, "exit": rc}
    return {"class": "refused-message", "exit": rc, "message": outcome.stderr.decode("utf-8", "replace").strip()[:160]}


def check_pressure(limit_bytes, unconstrained_peak_bytes):
    """A limit at or above what the unconstrained run needed puts no pressure on it: such a probe says nothing and is refused."""
    if limit_bytes >= unconstrained_peak_bytes:
        raise Refusal("matrix.limit-no-pressure", "a limit of %d bytes is not under the %d the run needs" % (limit_bytes, unconstrained_peak_bytes))


def fault_profile(outcome):
    """How a tool fails: exit status, where the words go, whether there is a machine-readable error with a tag and a repair."""
    rule, repair = _rule_and_repair(outcome)
    return {"exit_nonzero": outcome.returncode != 0, "stdout_empty": not outcome.stdout.strip(), "stderr_nonempty": bool(outcome.stderr.strip()),
            "json_error": _json_error(outcome.stdout) is not None, "rule_tag": rule, "repair": repair}


# ---------------------------------------------------------------- launching
def real_launch(argv, env=None, stdin_path=None, timeout=60, capture=True):
    full = None
    if env:
        full = dict(os.environ)
        full.update(env)
    sink = subprocess.PIPE if capture else subprocess.DEVNULL
    f = open(stdin_path, "rb") if stdin_path else None
    try:
        import time
        t0 = time.perf_counter()
        try:
            p = subprocess.run(argv, stdout=sink, stderr=sink, stdin=f or subprocess.DEVNULL, timeout=timeout, env=full)
        except subprocess.TimeoutExpired:
            return bn.Outcome(None, time.perf_counter() - t0, b"", b"", True)
        except OSError as e:
            return bn.Outcome(127, time.perf_counter() - t0, b"", str(e).encode(), False)
        return bn.Outcome(p.returncode, time.perf_counter() - t0, p.stdout or b"", p.stderr or b"", False)
    finally:
        if f:
            f.close()


@dataclasses.dataclass
class Context:
    inputs: dict
    tools: list
    launch: object
    emit: object
    repeats: int = 5
    tasks: list = dataclasses.field(default_factory=lambda: list(forms.TASKS))
    form_builder: object = forms.build_form
    timeout: float = 120.0
    tools_dir: object = None
    wrap: object = None                     # argv -> argv (taskset)
    rusage: object = None
    valgrind: object = None
    cg_rows: tuple = (2000, 6000)
    cg_timeout: float = 900.0
    runs: int = MIN_RUNS
    clock_threads: int = 4
    refs: dict = dataclasses.field(default_factory=dict)
    scratch: object = None


def ref_of(ctx, task):
    if task not in ctx.refs:
        ctx.refs[task] = forms.reference(task, ctx.inputs)
    return ctx.refs[task]


def ns_for(ctx, family, threads=None, file=None, **kw):
    return forms.Ns(file=file or ctx.inputs["csv"], jsonl=ctx.inputs["jsonl"], quoted=ctx.inputs["quoted"], threads=threads, **kw)


def full_launch(ctx, tool, form):
    """(argv, env) of a form: the detected command in front of the form's words (or the form's own shell line), the tool's directory on PATH."""
    argv = list(form.argv) if form.shell else list(tool["command"]) + list(form.argv[1:])
    if ctx.wrap:
        argv = ctx.wrap(argv)
    env = dict(form.env)
    dirs = [ctx.tools_dir] if ctx.tools_dir else []
    if tool.get("path") and os.sep in str(tool["path"]):
        dirs.append(os.path.dirname(str(tool["path"])))
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    return argv, env


def _first_threads(family):
    return forms.THREADS[family][0]


def _form(ctx, tool, task, threads, **kw):
    return ctx.form_builder(tool["family"], task, ns_for(ctx, tool["family"], threads=threads, **kw))


def _launch(ctx, tool, form, timeout=None, capture=True):
    argv, env = full_launch(ctx, tool, form)
    if capture:
        return ctx.launch(argv, env=env, stdin_path=form.stdin_path, timeout=timeout or ctx.timeout)
    return ctx.launch(argv, env=env, stdin_path=form.stdin_path, timeout=timeout or ctx.timeout, capture=False)


def _judged(tool, o):
    st = bn.classify(o.returncode, o.stdout, o.stderr, tool["family"] == "table", o.timed_out)
    return st


# ---------------------------------------------------------------- kinds a and b
def cells_ab(ctx):
    cells = []
    for tool in ctx.tools:
        for task in ctx.tasks:
            name = tool["name"]
            if not tool["found"]:
                why = not_installed_reason(tool)
                cells += [blank(task, name, "a", why), blank(task, name, "b", why)]
                continue
            form = _form(ctx, tool, task, _first_threads(tool["family"]))
            if form is None:
                cells += [cell(task, name, "a", {"outcome": "no-form", "note": "no form written for this tool and task (this says nothing about whether the tool can do it)"}),
                          blank(task, name, "b", "no answer to judge: no form written")]
                continue
            o = _launch(ctx, tool, form)
            st = _judged(tool, o)
            if st["status"] == "ok":
                cells.append(cell(task, name, "a", {"outcome": "yes"}))
                j = forms.compare(ref_of(ctx, task), o.stdout)
                cells.append(cell(task, name, "b", {"status": j["status"], "level": j["level"], "detail": j["detail"]}))
                ctx.emit("answer  %s  %s  %s%s" % (task, name, j["status"], " (" + j["level"] + ")" if j["level"] else " " + j["detail"]))
            else:
                v = {"outcome": {"refused": "refused", "crashed": "crashed", "timeout": "timeout"}[st["status"]], "reason": st["reason"]}
                if st.get("rule"):
                    v["rule"] = st["rule"]
                cells.append(cell(task, name, "a", v))
                cells.append(blank(task, name, "b", "no answer to judge: %s: %s" % (st["status"], st["reason"])))
                ctx.emit("answer  %s  %s  %s" % (task, name, st["reason"]))
    return cells


# ---------------------------------------------------------------- kind c
def cells_c(ctx):
    cells = []
    for tool in ctx.tools:
        for task in [t for t in ctx.tasks if t in forms.DETERMINISM_TASKS]:
            name = tool["name"]
            if not tool["found"]:
                cells.append(blank(task, name, "c", not_installed_reason(tool)))
                continue
            outputs, settings, why = [], [], None
            for level in forms.THREADS[tool["family"]]:
                form = _form(ctx, tool, task, level)
                if form is None:
                    why = "no form written"
                    break
                for _ in range(ctx.repeats):
                    o = _launch(ctx, tool, form)
                    st = _judged(tool, o)
                    if st["status"] != "ok":
                        why = "a run did not answer: %s" % st["reason"]
                        break
                    outputs.append(o.stdout)
                settings.append(level)
                if why:
                    break
            if why:
                cells.append(blank(task, name, "c", "not determined: " + why))
                continue
            d = distinct_outputs(outputs, forms.TASKS[task]["ordered"])
            cells.append(cell(task, name, "c", {**d, "runs": len(outputs), "threads": settings, "repeats": ctx.repeats}))
            ctx.emit("determinism  %s  %s  raw %d canonical %d over %d runs" % (task, name, d["raw"], d["canonical"], len(outputs)))
    return cells


# ---------------------------------------------------------------- kind d
def _limited(argv, mib):
    return ["sh", "-c", 'ulimit -v "$0" && exec "$@"', str(mib * 1024), *argv]


def cells_d(ctx):
    cells = []
    for tool in ctx.tools:
        name = tool["name"]
        if not tool["found"]:
            cells.append(blank("X03", name, "d", not_installed_reason(tool)))
            continue
        if tool["family"] == "table":
            probes = []
            if "X03" in ctx.tasks:
                probes.append(("own-bound-groups", "X03", ["table", "--group", "id3", "--agg", "sum:v1", "--max-groups", "10", ctx.inputs["csv"]]))
            if "X05" in ctx.tasks:
                probes.append(("own-bound-sort", "X05", ["table", "--select", "id3", "--order-by", "id3", "--max-sort-rows", "10", ctx.inputs["csv"]]))
            for probe, task, argv in probes:
                o = ctx.launch(list(tool["command"]) + argv[1:], env=None, stdin_path=None, timeout=ctx.timeout)
                r = classify_limit(o, None, "table")
                cells.append(cell(task, name, "d", {"probe": probe, **r, "rule": r.get("rule"), "note": "the tool's own bound set to 10, below the number of keys or rows of the input, answered in its default JSON"}))
                ctx.emit("limit  %s  %s  %s" % (probe, name, r["class"] + (" " + str(r.get("rule")) if r.get("rule") else "")))
        if "X03" not in ctx.tasks:
            continue
        form = _form(ctx, tool, "X03", _first_threads(tool["family"]))
        if form is None:
            cells.append(blank("X03", name, "d", "no form written"))
            continue
        peak = ctx.rusage(*full_launch(ctx, tool, form)) if ctx.rusage and not form.stdin_path else None
        rungs = []
        for mib in LADDER_MIB:
            argv, env = full_launch(ctx, tool, form)
            o = ctx.launch(_limited(argv, mib), env=env, stdin_path=form.stdin_path, timeout=ctx.timeout)
            r = classify_limit(o, ref_of(ctx, "X03"), tool["family"])
            pressured = (mib * 1024 < peak["peak_rss_kb"]) if peak else None
            rungs.append({"limit_mib": mib, **r, "under_the_unconstrained_peak": pressured})
        cells.append(cell("X03", name, "d", {"probe": "address-space-limit", "rungs": rungs, "unconstrained_peak_rss_kb": peak["peak_rss_kb"] if peak else None,
                                             "note": "ulimit -v is the virtual address space, not the resident set; a rung above the process's virtual size puts no pressure on it"}))
        ctx.emit("limit  address-space  %s  %s" % (name, ", ".join("%d MiB %s" % (x["limit_mib"], x["class"]) for x in rungs)))
    return cells


# ---------------------------------------------------------------- kind g
def _prefix(src, dst, rows):
    with open(src, "rb") as f, open(dst, "wb") as o:
        for i, line in enumerate(f):
            if i > rows:
                break
            o.write(line)


def cells_g(ctx):
    cells = []
    scratch = pathlib.Path(ctx.scratch or tempfile.mkdtemp(prefix="matrix-cg-"))
    small, large = ctx.cg_rows
    files = {}
    for n in (small, large):
        files[n] = str(scratch / ("prefix-%d.csv" % n))
        _prefix(ctx.inputs["csv"], files[n], n)
    for tool in ctx.tools:
        for task in [t for t in ctx.tasks if t in forms.CALLGRIND_TASKS]:
            name = tool["name"]
            if not tool["found"]:
                cells.append(blank(task, name, "g", not_installed_reason(tool)))
                continue
            if not ctx.valgrind:
                cells.append(blank(task, name, "g", "valgrind is not installed here"))
                continue
            counts, why = {}, None
            for n in (small, large):
                form = _form(ctx, tool, task, _first_threads(tool["family"]) and 1, file=files[n])
                if form is None:
                    why = "no form written"
                    break
                argv, env = full_launch(ctx, tool, form)
                o = ctx.launch([ctx.valgrind, "--tool=callgrind", "--callgrind-out-file=/dev/null", "--trace-children=yes", *argv], env=env, stdin_path=form.stdin_path, timeout=ctx.cg_timeout)
                got = collected(o.stderr.decode("utf-8", "replace")) if o.returncode == 0 else None
                if got is None:
                    tail = o.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
                    why = "does not run under callgrind: %s" % ("timeout" if o.timed_out else "exit %s: %s" % (o.returncode, tail[0][:120]))
                    break
                counts[n] = got
            if why:
                cells.append(blank(task, name, "g", why))
                continue
            sl = slope(small, counts[small][0], large, counts[large][0])
            if sl is None:
                cells.append(blank(task, name, "g", "not valid: the count did not grow with the rows (%d at %d rows, %d at %d)" % (counts[small][0], small, counts[large][0], large)))
                continue
            cells.append(cell(task, name, "g", {"instructions_small": counts[small][0], "instructions_large": counts[large][0], "rows_small": small, "rows_large": large,
                                                 "per_row": sl["per_row"], "startup": sl["startup"], "processes": counts[large][1], "unit": "instructions retired (callgrind), not a time"}))
            ctx.emit("instructions  %s  %s  %.1f per row" % (task, name, sl["per_row"]))
    return cells


# ---------------------------------------------------------------- kind h
FAULTS = [("missing-file", {"missing": "/nonexistent/matrix-missing.csv"}), ("unknown-column", {"unknown_column": "nope_col"})]


def cells_h(ctx):
    cells = []
    for tool in ctx.tools:
        name = tool["name"]
        if not tool["found"]:
            cells.append(blank("X02", name, "h", not_installed_reason(tool)))
            continue
        for fault, kw in FAULTS:
            form = _form(ctx, tool, "X02", _first_threads(tool["family"]) and 1, machine=True, **kw)
            if form is None:
                cells.append(blank("X02", name, "h", "no form written"))
                continue
            o = _launch(ctx, tool, form)
            p = fault_profile(o)
            cells.append(cell("X02", name, "h", {"fault": fault, **p}))
            ctx.emit("fault  %s  %s  %s" % (fault, name, json.dumps(p)))
    return cells


# ---------------------------------------------------------------- kinds e and f
NOT_CLOCK = "NOT RUN: the clock kinds run only behind --clock, on a quiet machine (load under a quarter of the cores, no virtual machine, no soak or container, at least %d runs)" % MIN_RUNS


def cells_clock_blank(ctx):
    cells = []
    for tool in ctx.tools:
        for task in [t for t in ctx.tasks if t in CLOCK_TASKS]:
            for kind in CLOCK_KINDS:
                cells.append(blank(task, tool["name"], kind, (not_installed_reason(tool) + ". ") * (not tool["found"]) + NOT_CLOCK))
    return cells


def summarise(times):
    s = sorted(times)
    n = len(s)
    return {"min_s": s[0], "median_s": s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, "first_s": times[0], "all_s": list(times), "runs": n}


def cells_clock(ctx, ab_cells):
    """Time and peak resident set, for the pairs whose answer passed the gate: one untimed warm-up, `ctx.runs` rounds with the contenders interleaved and the order rotating,
    output to /dev/null; then one more run for the resident set. Called only after clock_gate."""
    ok = {(c["task"], c["contender"]) for c in ab_cells if c["kind"] == "b" and c["value"] and c["value"]["status"] == "ok"}
    cells = []
    floor = (ctx.rusage(["true"], {}) or {}).get("peak_rss_kb") if ctx.rusage else None
    for task in [t for t in ctx.tasks if t in CLOCK_TASKS]:
        live = []
        for tool in ctx.tools:
            if not tool["found"]:
                why = not_installed_reason(tool)
                cells += [blank(task, tool["name"], "e", why), blank(task, tool["name"], "f", why)]
            elif (task, tool["name"]) not in ok:
                why = "no measure: the answer did not pass the gate (see kinds a and b)"
                cells += [blank(task, tool["name"], "e", why), blank(task, tool["name"], "f", why)]
            else:
                live.append(tool)
        forms_of = {t["name"]: _form(ctx, t, task, _first_threads(t["family"]) and ctx.clock_threads) for t in live}
        for t in live:
            _launch(ctx, t, forms_of[t["name"]], capture=False)
        times = {t["name"]: [] for t in live}
        for r in range(ctx.runs):
            order = live[r % len(live):] + live[:r % len(live)] if live else []
            for t in order:
                o = _launch(ctx, t, forms_of[t["name"]], capture=False)
                if o.returncode != 0:
                    times[t["name"]] = None
                if times[t["name"]] is not None:
                    times[t["name"]].append(o.seconds)
        for t in live:
            ts = times[t["name"]]
            if not ts:
                cells += [blank(task, t["name"], "e", "a run failed while timing"), blank(task, t["name"], "f", "a run failed while timing")]
                continue
            cells.append(cell(task, t["name"], "f", {**summarise(ts), "answer": "ok", "threads": ctx.clock_threads if forms.THREADS[t["family"]][0] else "tool default"}))
            ru = ctx.rusage(*full_launch(ctx, t, forms_of[t["name"]])) if ctx.rusage else None
            if ru:
                cells.append(cell(task, t["name"], "e", {**ru, "rss_floor_kb": floor, "answer": "ok"}))
            else:
                cells.append(blank(task, t["name"], "e", "the resident set could not be measured"))
            ctx.emit("time  %s  %s  min %.4f s of %d" % (task, t["name"], min(ts), len(ts)))
    return cells


# ---------------------------------------------------------------- the program
def split(text):
    return [x for x in text.split(",") if x]


def parse_args(argv):
    p = argparse.ArgumentParser(prog="bench_matrix.py", add_help=True, exit_on_error=False)
    p.add_argument("--rows", type=int, default=20000)
    p.add_argument("--k", type=int, default=100)
    p.add_argument("--seed", type=int, default=108)
    p.add_argument("--out", default=None)
    p.add_argument("--results", default=None)
    p.add_argument("--kinds", default=None)
    p.add_argument("--tasks", default=None)
    p.add_argument("--contenders", default=None)
    p.add_argument("--clock", action="store_true")
    p.add_argument("--runs", type=int, default=MIN_RUNS)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--table", default=None)
    p.add_argument("--tools-dir", default=None)
    p.add_argument("--check-tools", action="store_true")
    p.add_argument("--require", default=None)
    p.add_argument("--taskset", default=None)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--accept-large", action="store_true")
    p.add_argument("--cg-rows", default="2000,6000")
    p.add_argument("--path-class", default=None)
    try:
        return p.parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as e:
        raise Refusal("matrix.args", "bad arguments: %s" % e)


def main(argv=None, probes=None, launch=None, rusage=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        return run(argv, probes, launch, rusage)
    except Refusal as r:
        print(r.line())
        print("bench_matrix: %s: %s" % (r.rule, r.message), file=sys.stderr)
        return r.exit


def run(argv, probes, launch, rusage):
    a = parse_args(argv)
    kinds = split(a.kinds) if a.kinds else None
    if a.clock and not kinds:
        kinds = DEFAULT_KINDS + CLOCK_KINDS
    kinds = kinds or list(DEFAULT_KINDS)
    if any(k not in KINDS for k in kinds):
        raise Refusal("matrix.args", "unknown kind in %s" % ",".join(kinds))
    tasks = split(a.tasks) if a.tasks else list(forms.TASKS)
    if any(t not in forms.TASKS for t in tasks):
        raise Refusal("matrix.args", "unknown task in %s" % ",".join(tasks))
    if a.rows < 100 or a.runs < 1 or a.repeats < 1 or a.threads < 1:
        raise Refusal("matrix.args", "--rows must be at least 100; --runs and --repeats at least 1")
    if any(k in CLOCK_KINDS for k in kinds) and not a.clock:
        raise Refusal("matrix.clock-switch", "kinds e and f (peak resident set, time) run only with --clock, on a quiet machine")
    if a.rows > SMOKE_ROWS and not a.accept_large:
        raise Refusal("matrix.large-size", "%d rows is over the %d of a smoke step: pass --accept-large (the data directory needs room)" % (a.rows, SMOKE_ROWS))
    if a.results and forms.inside_repo(pathlib.Path(a.results).parent):
        raise Refusal("matrix.inside-repo", "%s is inside the repository: results are written outside it, then committed deliberately" % a.results)
    manifest = load_manifest()
    tools = detect_tools(manifest, table=a.table or default_table(), path=(a.tools_dir + os.pathsep + os.environ.get("PATH", "")) if a.tools_dir else None)
    if a.check_tools:
        sys.stdout.write(check_tools_text(tools))
        return 0
    if a.require:
        require_tools(tools, split(a.require))
    if a.contenders:
        want = split(a.contenders)
        require_names = {t["name"] for t in tools}
        if any(w not in require_names for w in want):
            raise Refusal("matrix.args", "unknown contender in %s" % a.contenders)
        tools = [t for t in tools if t["name"] in want]
    probes = probes or bn.Probes()
    snap = bn.Snapshot(probes)
    live = {t["name"]: {"version": t["version"] or "", "threads": "1..4" if forms.THREADS[t["family"]][0] else "tool default"} for t in tools if t["found"]}
    prov = {"runs": a.runs, "rows": a.rows, "k": a.k, "seed": a.seed, "step": "%de" % a.rows, "contenders": live or {"table": {"version": "pending", "threads": "1"}},
            "file": {"name": "pending", "bytes": 1, "sha256": "pending", "path_class": a.path_class or "not stated"}}
    if a.clock:
        clock_gate(bn.collect_conditions(snap, prov))      # before anything is generated or launched
    out_dir = a.out or os.path.join(tempfile.gettempdir(), "cancho-matrix-data")
    inputs = forms.make_inputs(a.rows, a.k, out_dir, a.seed)
    info = {**prov, "file": {"name": pathlib.Path(inputs["csv"]).name, "bytes": inputs["bytes"]["csv"], "sha256": inputs["sha256"]["csv"], "path_class": a.path_class or "not stated"},
            "taskset": a.taskset, "tables": {n: {"bytes": inputs["bytes"][n], "sha256": inputs["sha256"][n]} for n in ("jsonl", "quoted")}}
    conditions = bn.collect_conditions(snap, info)
    if a.clock:
        clock_gate(conditions)
    wrap = None
    if a.taskset:
        cores = bn.gram_cores(conditions["soak"]["found"], bool(conditions["docker"]["containers"])) if a.taskset == "gram" else a.taskset
        conditions["taskset"] = cores
        wrap = lambda argv_, c=cores: bn.with_taskset(argv_, c)  # noqa: E731
    cg = tuple(int(x) for x in a.cg_rows.split(","))
    ctx = Context(inputs=inputs, tools=tools, launch=launch or real_launch, emit=lambda s: print(s, flush=True), repeats=a.repeats, tasks=tasks, timeout=a.timeout, tools_dir=a.tools_dir,
                  wrap=wrap, rusage=rusage or (bn.real_rusage if a.clock or "d" in kinds else None), valgrind=shutil.which("valgrind"), cg_rows=cg, runs=a.runs, clock_threads=a.threads)
    cells = []
    ab = []
    if any(k in kinds for k in ("a", "b", "e", "f")):
        ab = cells_ab(ctx)
        cells += [c for c in ab if c["kind"] in kinds]
    if "c" in kinds:
        cells += cells_c(ctx)
    if "d" in kinds:
        cells += cells_d(ctx)
    if "g" in kinds:
        cells += cells_g(ctx)
    if "h" in kinds:
        cells += cells_h(ctx)
    if a.clock:
        cells += cells_clock(ctx, ab)
    else:
        cells += cells_clock_blank(ctx)
    result = {"schema": SCHEMA, "conditions": conditions, "kinds_run": kinds, "clock": bool(a.clock), "tools": tools, "inputs": {k: inputs[k] for k in ("rows", "k", "seed", "sha256", "bytes")},
              "cells": cells}
    validate_result(result)
    if a.results:
        pathlib.Path(a.results).write_text(json.dumps(result, indent=1, default=str))
    counts = {}
    for c in cells:
        counts[(c["kind"], "value" if c["value"] else "blank")] = counts.get((c["kind"], "value" if c["value"] else "blank"), 0) + 1
    print("cells: " + ", ".join("%s %s %d" % (k, s, n) for (k, s), n in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
