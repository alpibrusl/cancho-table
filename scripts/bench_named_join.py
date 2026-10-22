#!/usr/bin/env python3
"""The join suite of the timing driver (docs/named-benchmarks.md, NB3): db-benchmark's five join questions with the answer gate of NB2.

    python3 scripts/bench_named.py --suite join --rows 1e6 [--runs 5] [--questions q1,q2,q3,q4,q5] [--contenders table,table-sorted,duckdb,csvtk,mlr,pandas] ...

This module holds what is particular to a join: the contenders' forms, the judge of a joined answer, the refusal report, and the step. Everything else (the launch, the min of N, the rotation,
the conditions, `publishable`, the writer of a published table) is `bench_named.py`'s and is used as it is.

A joined answer is wide (13 cells a row, up to the left table's rows), so the answer run writes it to a **file** and the judge reads the file in one pass (`bench_named_join_ref.check_output`): the
fingerprint of the rows, exact. A contender is timed only when its fingerprint is the reference's. The timed run writes the same rows to /dev/null.

The forms ask each engine for the same **canonical row** (x's seven cells, then the right table's other cells, v2 last): table with `--join-prefix`, DuckDB with an explicit select list, csvtk
`join`, Miller `join` with `cut -o`, pandas `merge`. `table-sorted` is `table --join-sorted` over copies of the two tables sorted by the key (its answer is the same rows, in key order).
"""

import json
import os
import pathlib
import sys
import tempfile
import time
import dataclasses
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench_named as bn  # noqa: E402
import bench_named_gen as gen  # noqa: E402
import bench_named_join_gen as jgen  # noqa: E402
import bench_named_join_ref as jref  # noqa: E402

FAMILIES = ("table", "table-sorted", "duckdb", "csvtk", "mlr", "pandas")
JOIN_QUESTIONS = ("q1", "q2", "q3", "q4", "q5")
LEVELS = bn.LEVELS


@dataclasses.dataclass
class JoinContext(bn.Context):
    paths: dict = dataclasses.field(default_factory=dict)       # x, small, medium, big
    reference: dict = dataclasses.field(default_factory=dict)   # qid -> Acc
    tmp: object = None                                          # a directory for the answers and the sorted copies
    sorted_cache: dict = dataclasses.field(default_factory=dict)
    launch_file: object = None                                  # (argv, env=, dest=, timeout=) -> Outcome


def launch_to_file(argv, env=None, dest=None, timeout=bn.DEFAULT_TIMEOUT, run=None, clock=None, stdin_path=None):
    """One launch whose standard output goes to the file `dest` (and whose standard input is the file `stdin_path`, if given); its wall time by `clock`; the group is killed at the deadline."""
    run = run or subprocess.run
    clock = clock or time.perf_counter
    full = None
    if env:
        full = dict(os.environ)
        full.update(env)
    t0 = clock()
    try:
        with open(dest, "wb") as f, (open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb")) as fin:
            p = run(argv, stdout=f, stderr=subprocess.PIPE, stdin=fin, timeout=timeout, env=full)
    except subprocess.TimeoutExpired:
        return bn.Outcome(None, clock() - t0, b"", b"", True)
    except OSError as e:
        return bn.Outcome(127, clock() - t0, b"", str(e).encode(), False)
    return bn.Outcome(p.returncode, clock() - t0, b"", (p.stderr or b"")[:4096], False)


# ---------------------------------------------------------------- the forms

def sorted_copies(ctx, qid):
    """The two tables of the question sorted bytewise by its key (cached): {"x": path, "right": path}."""
    q = jref.JOINS[qid]
    out = {}
    for name, table in (("x", "x"), ("right", q["right"])):
        k = (table, q["key"])
        if k not in ctx.sorted_cache:
            ctx.sorted_cache[k] = jgen.sorted_variant(ctx.paths[table], q["key"], ctx.tmp)
        out[name] = ctx.sorted_cache[k]
    return out


def table_argv(binary, qid, paths, which="flags", sorted_paths=None, merge=False, threads=1):
    """table's argv for a question from the questions file; `sorted_paths` replaces x and the right table by their sorted copies, `merge` adds --join-sorted."""
    spec = next(s for s in jref.load_questions()["questions"] if s["id"] == qid)
    p = dict(paths)
    if sorted_paths:
        p["x"], p[spec["right"]] = sorted_paths["x"], sorted_paths["right"]
    argv = jref.argv_for(spec[which], p)
    if merge:
        argv = ["--join-sorted", *argv]
    return [binary, "--threads", str(threads), *argv]


def duck_sql(qid, paths):
    q = jref.JOINS[qid]
    right = ", ".join("r.%s AS %s_%s" % (c, q["right"], c) for c in q["right_cols"])
    kind = "LEFT JOIN" if q["kind"] == "left" else "JOIN"
    return "SELECT x.*, %s FROM read_csv('%s') x %s read_csv('%s') r ON x.%s = r.%s" % (
        right, str(paths["x"]).replace("'", "''"), kind, str(paths[q["right"]]).replace("'", "''"), q["key"], q["key"])


def pandas_code():
    return ("import sys\nimport pandas as pd\nx = pd.read_csv(sys.argv[1])\nr = pd.read_csv(sys.argv[2])\n"
            "m = x.merge(r, on=sys.argv[3], how=sys.argv[4], suffixes=('', '_r'), sort=False)\nm.to_csv(sys.stdout, index=False, lineterminator='\\n')\n")


def mlr_argv(mlr, qid, paths):
    """Miller: the right table is the -f file (held), x is streamed; the join key comes out first, so `cut -o` puts x's seven columns first; the right table's columns are prefixed (--lp)
    and, for a left join, the unpaired x records are filled (unsparsify -f) so that every record has the same fields."""
    q = jref.JOINS[qid]
    prefixed = ["%s_%s" % (q["right"], c) for c in q["right_cols"]]
    argv = [mlr, "--icsv", "--ocsv", "join", "-j", q["key"], "--lp", q["right"] + "_"]
    if q["kind"] == "left":
        argv += ["--ur"]
    argv += ["-f", str(paths[q["right"]])]
    if q["kind"] == "left":
        argv += ["then", "unsparsify", "-f", ",".join(prefixed)]
    argv += ["then", "cut", "-o", "-f", ",".join(jref.X_COLUMNS + prefixed), str(paths["x"])]
    return argv


def build_contenders(tools, paths, wanted=FAMILIES, taskset=None, externals=()):
    """The join contenders that can run here. Each Contender's `form(qid, _file)` returns the bn.Form for the question over `paths`."""
    out = []
    versions = {}

    def add(name, family, threads, profile, form, path, extra=None):
        base = "duckdb" if family == "duckdb" else "table" if family.startswith("table") else family
        version = versions.setdefault(base, bn.find_version(base, tools))
        info = {"family": family, "version": version, "threads": threads, "path": path, **bn.pin_status(base, version), **(extra or {})}
        out.append(bn.Contender(name, family, threads, profile, version, form, path, info))

    for family in wanted:
        if family == "table" and tools.get("table"):
            extra = {k: v for k, v in bn.table_version(tools["table"]).items() if k != "version"}
            add("table@1", "table", "1", "exact", lambda qid, f, b=tools["table"]: bn.Form(bn.with_taskset(table_argv(b, qid, paths), taskset), bn.with_taskset(table_argv(b, qid, paths), taskset),
                                                                                       {}, bn.with_taskset(table_argv(b, qid, paths, "query"), taskset)),
                tools["table"], {**extra, "note": "the benchmark runs table at --threads 1, where the join is read on one core (docs/joins.md section 7); since J8 a build of at least --parallel-min-bytes is built by threads at --threads 2 or more (section 24), which this benchmark does not use and claims nothing of"})
        elif family == "table-sorted" and tools.get("table"):
            extra = {k: v for k, v in bn.table_version(tools["table"]).items() if k != "version"}
            add("table-sorted@1", "table-sorted", "1", "exact", None, tools["table"], {**extra, "note": "--join-sorted over copies of the two tables sorted bytewise by the key"})
        elif family == "duckdb" and tools.get("duckdb"):
            kind, cli = tools["duckdb"]
            for n in ("1", "default"):
                def dform(qid, f, n=n, kind=kind, cli=cli):
                    pre = [] if n == "default" else ["SET threads=%s" % n]

                    def build(sink):
                        copy = "COPY (%s) TO '%s' (FORMAT csv)" % (duck_sql(qid, paths), sink)
                        if kind == "cli":
                            return bn.with_taskset([cli, "-c", "; ".join([*pre, copy])], taskset)
                        return bn.with_taskset([tools["python"], "-I", "-c", bn._DUCK_PY, *pre, copy], taskset)
                    return bn.Form(build("/dev/stdout"), build("/dev/null"))
                add("duckdb@%s" % n, "duckdb", n, "double", dform, cli, {"variant": kind})
        elif family == "csvtk" and tools.get("csvtk"):
            def cform(qid, f):
                q = jref.JOINS[qid]
                argv = [tools["csvtk"], "-j", "1", "join", "-f", q["key"], *(["-L"] if q["kind"] == "left" else []), str(paths["x"]), str(paths[q["right"]])]
                return bn.Form(bn.with_taskset(argv, taskset), bn.with_taskset(argv, taskset))
            add("csvtk@1", "csvtk", "1", "double", cform, tools["csvtk"], {"note": "csvtk join holds the files in memory"})
        elif family == "mlr" and tools.get("mlr"):
            def mform(qid, f):
                argv = bn.with_taskset(mlr_argv(tools["mlr"], qid, paths), taskset)
                return bn.Form(argv, argv, {"GOMAXPROCS": "1"})
            add("mlr@1", "mlr", "1", "double", mform, tools["mlr"], {"note": "GOMAXPROCS=1; join -f holds the right table; unsorted mode"})
        elif family == "pandas" and tools.get("pandas"):
            def pform(qid, f):
                q = jref.JOINS[qid]
                argv = bn.with_taskset([tools["python"], "-I", "-c", pandas_code(), str(paths["x"]), str(paths[q["right"]]), q["key"], q["kind"]], taskset)
                return bn.Form(argv, argv)
            add("pandas@default", "pandas", "default", "double", pform, tools["python"], {"note": "pd.read_csv x2 then merge, as a process (the import is in the time)"})
    for name, profile, path in externals:
        def eform(qid, f, path=path):
            q = jref.JOINS[qid]
            argv = ([sys.executable] if str(path).endswith(".py") else []) + [str(path), qid, str(paths["x"]), str(paths[q["right"]]), q["key"], q["kind"]]
            return bn.Form(argv, argv)
        out.append(bn.Contender(name, "external", "n/a", profile, "external program, not versioned", eform, path,
                                {"family": "external", "version": "external program, not versioned", "threads": "n/a", "path": path, "pin": None, "pinned": None,
                                 "note": "any program: argv QID X RIGHT KEY KIND, the canonical rows on standard output"}))
    return out


def missing_contenders(tools, wanted):
    have = {"table": tools.get("table"), "table-sorted": tools.get("table"), "duckdb": tools.get("duckdb"), "csvtk": tools.get("csvtk"), "mlr": tools.get("mlr"), "pandas": tools.get("pandas")}
    return [{"contender": f, "reason": bn.why_missing(tools, "table" if f == "table-sorted" else f)} for f in wanted if not have.get(f)]


# ---------------------------------------------------------------- one question

def form_of(ctx, c, qid):
    """The Form of a contender for a question; the sorted contender builds its from the sorted copies, made on first use."""
    if c.family == "table-sorted":
        sp = sorted_copies(ctx, qid)
        merge = table_argv(c.path, qid, ctx.paths, sorted_paths=sp, merge=True)
        plain = table_argv(c.path, qid, ctx.paths, sorted_paths=sp, merge=False)
        return bn.Form(merge, merge, {}, plain)
    return c.form(qid, None)


def run_join_question(ctx, q, contenders):
    """Gate every contender's joined rows against the reference, then time the ones that passed (bn.time_live). One record per contender; no time before every answer is judged."""
    qid = q["id"]
    records, live = [], []
    for c in contenders:
        rec = bn.base_record(q, c)
        records.append(rec)
        if (qid, c.name) in ctx.dropped:
            rec.update(status="not-run", reason="dropped: over %g s on this question at a smaller step" % ctx.timeout)
            ctx.emit(bn._answer_line(q, c, "NOT TIMED: not-run: %s" % rec["reason"]))
            continue
        skip = ctx.skip.get((qid, c.name)) or ctx.skip.get((qid, c.family))
        if skip is not None:
            rec.update(status="not-run", reason="skipped by the operator: %s" % skip)
            ctx.emit(bn._answer_line(q, c, "NOT TIMED: not-run: %s" % rec["reason"]))
            continue
        form = form_of(ctx, c, qid)
        dest = pathlib.Path(ctx.tmp) / ("answer-%s-%s.csv" % (qid, c.name.replace("@", "-")))
        out = ctx.launch_file(form.answer, env=form.env, dest=dest, timeout=ctx.timeout)
        cl = bn.classify(out.returncode, out.stdout, out.stderr, c.family.startswith("table"), out.timed_out)
        if cl["status"] != "ok":
            reason = "over %g s" % ctx.timeout if cl["status"] == "timeout" else cl["reason"]
            rec.update(status=cl["status"], reason=reason, rule=cl["rule"])
            if cl["status"] == "timeout":
                ctx.dropped.add((qid, c.name))
            ctx.emit(bn._answer_line(q, c, "NOT TIMED: %s: %s" % (cl["status"], reason)))
            _drop(dest)
            continue
        acc, why = jref.check_output(dest, qid, expect_header=jref.joined_columns(qid) if c.profile == "exact" and c.family == "table" else None)
        if why is None:
            why = jref.compare(qid, ctx.reference[qid], acc)
        if why is not None:
            rec.update(status="wrong-answer", reason=why)
            ctx.emit(bn._answer_line(q, c, "NOT TIMED: wrong-answer: %s" % why))
            _drop(dest)
            continue
        rec["answer"] = {"match": "exact", "tolerance": None, "check": "fingerprint: count, sum v1, sum v2, row digest", "rows": acc.count, "chk": acc.chk()}
        if form.alt is not None:
            why = _second_form(ctx, c, qid, form, dest)
            rec["forms_equal"] = why is None
            if why is not None:
                rec.pop("answer")
                rec.update(status="wrong-answer", reason=why)
                ctx.emit(bn._answer_line(q, c, "NOT TIMED: wrong-answer: %s" % why))
                _drop(dest)
                continue
        _drop(dest)
        rec["status"] = "ok"
        ctx.emit(bn._answer_line(q, c, "ok (exact fingerprint)"))
        live.append((c, form, rec))
    if live:
        bn.time_live(ctx, q, live)
    return records


def _drop(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _second_form(ctx, c, qid, form, first):
    """table's other spellings of the same call must give the same rows: `table`: the --query form (fingerprint) and the probe on standard input (the same bytes as the file); `table-sorted`: the
    call without --join-sorted over the same sorted copies gives the same bytes. None when they do, else the reason."""
    other = pathlib.Path(ctx.tmp) / ("second-%s-%s.csv" % (qid, c.name.replace("@", "-")))
    try:
        out = ctx.launch_file(form.alt, env=form.env, dest=other, timeout=ctx.timeout)
        if out.returncode != 0 or out.timed_out:
            return "the second form (%s) did not run: exit %s" % ("the call without --join-sorted" if c.family == "table-sorted" else "--query", out.returncode)
        if c.family == "table-sorted":
            return None if other.read_bytes() == first.read_bytes() else "--join-sorted gives other bytes than the hash join over the same sorted files"
        acc, why = jref.check_output(other, qid)
        if why is None:
            why = jref.compare(qid, ctx.reference[qid], acc)
        if why is not None:
            return "the --query form: %s" % why
        flags = table_argv(c.path, qid, ctx.paths)
        piped = [a for a in flags if a != str(ctx.paths["x"])]
        p = ctx.launch_file(piped, env=form.env, dest=other, timeout=ctx.timeout, stdin_path=ctx.paths["x"])
        if p.returncode != 0 or p.timed_out or other.read_bytes() != first.read_bytes():
            return "the probe on standard input gives other bytes than the probe as a file (exit %s)" % p.returncode
        return None
    finally:
        _drop(other)


# ---------------------------------------------------------------- the refusal report: what table says at a bound, without a clock

def refusal_report(table, paths, qids=JOIN_QUESTIONS, timeout=bn.DEFAULT_TIMEOUT, bound=None):
    """For each question, with the default bounds of the tool (no --max-state-bytes): the exit status, and if refused the rule, the facts (limit, held, rows held, the row of the build that did not
    fit) and the repair (its kind, its reason or its option). `--explain` gives the build's accounted bytes at most and whether it may pass the bound, from the file's size alone.
    `bound` puts a --max-state-bytes on the call (to show a refusal at a size where the default does not refuse). Nothing is timed and nothing here is a result of the suite: it says what the answer
    is when `table` refuses."""
    report = {}
    for qid in qids:
        spec = next(s for s in jref.load_questions()["questions"] if s["id"] == qid)
        flags = [a for a in jref.argv_for(spec["flags"], paths)]
        # the same call as the question's flag form, with the tool's own bounds and the JSON answer (a csv answer writes the refusal on standard error without its facts)
        trimmed, skip = [], False
        for a in flags:
            if skip:
                skip = False
                continue
            if a in ("--max-state-bytes", "--format", "--max-join-rows", "--max-rows"):
                skip = True
                continue
            trimmed.append(a)
        if bound is not None:
            trimmed += ["--max-state-bytes", str(bound)]
        row = {"argv": ["table", *[os.path.basename(a) if os.sep in a else a for a in trimmed]]}
        for label, extra in (("run", []), ("explain", ["--explain"])):
            p = subprocess.run([table, *trimmed, *extra], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
            try:
                doc = json.loads(p.stdout.decode("utf-8", "replace").splitlines()[0])
            except (ValueError, IndexError):
                doc = {}
            if label == "explain":
                bs = ((doc.get("data") or {}).get("explain") or {}).get("join", {}).get("build_state", {})
                row["explain"] = {k: bs.get(k) for k in ("file_bytes", "accounted_at_most", "budget", "may_pass_bound")}
                row["explain"]["swap"] = bs.get("swap")
                continue
            row["exit"] = p.returncode
            if doc.get("ok"):
                row["status"] = "answered"
                continue
            err = doc.get("error") or {}
            det = err.get("detail") or {}
            rep = err.get("repair") or {}
            row.update(status="refused", rule=err.get("rule"), facts={k: det.get(k) for k in ("holder", "limit", "held", "rows_held", "build_row", "build_file_bytes") if k in det},
                       repair={"kind": rep.get("kind"), "reason": rep.get("reason"), "options": rep.get("options")})
        report[qid] = row
    return report


# ---------------------------------------------------------------- the step

def parse_externals(specs):
    out = []
    for spec in specs:
        parts = spec.split("=", 2)
        if len(parts) != 3 or parts[1] not in ("exact", "double"):
            raise bn.Refusal("bench.external", "--external NAME=PROFILE=PROGRAM, PROFILE exact or double: %r" % spec)
        out.append((parts[0], parts[1], parts[2]))
    return out


def ensure_files(rows, seed, out_dir):
    """The four tables of NB3 for these parameters in `out_dir`: reused when the sha256 of each is the one in x's metadata, else generated. Returns (paths, meta, seconds spent generating)."""
    out_dir = pathlib.Path(out_dir)
    paths = jref.paths_of(out_dir, rows)
    meta_path = paths["x"].with_name(paths["x"].name + ".meta.json")
    if meta_path.exists() and all(p.exists() for p in paths.values()):
        meta = json.loads(meta_path.read_text())
        if meta.get("seed") == seed and meta.get("rows") == rows and all(
                meta["tables"][t]["bytes"] == paths[t].stat().st_size and bn.sha256_of(paths[t]) == meta["tables"][t]["sha256"] for t in jgen.TABLES):
            return paths, meta, 0.0
    need = jgen.estimate_bytes(rows)
    if not bn.disk_ok(bn._free_bytes(out_dir), need):
        raise bn.Refusal("bench.disk", "%d bytes free near %s, under three times the %d the files are estimated to take" % (bn._free_bytes(out_dir), out_dir, need))
    t0 = time.perf_counter()
    paths, meta = jgen.write(rows, out_dir, seed)
    return paths, meta, time.perf_counter() - t0


def run_join_step(a, emit, probes=None):
    probes = probes or bn.Probes()
    snap = bn.Snapshot(probes)
    rows = bn.parse_rows(a.rows)
    jgen.sizes(rows)
    if a.runs < 1 or a.timeout <= 0:
        raise bn.Refusal("bench.args", "--runs must be at least 1 and --timeout positive")
    wanted_q = bn.split_csv(a.questions)
    for qid in wanted_q:
        if qid not in jref.JOINS:
            raise bn.Refusal("bench.question", "%r is not a join question (q1 to q5)" % qid)
    default = ",".join(bn.FAMILIES)
    families = bn.split_csv(a.contenders) if a.contenders != default else list(FAMILIES)
    families = [("mlr" if f == "miller" else f) for f in families]
    for f in families:
        if f not in FAMILIES:
            raise bn.Refusal("bench.contender", "unknown contender %r (one of %s)" % (f, ", ".join(FAMILIES)))
    tools = bn.find_tools(table=a.table)
    cores = a.taskset
    if cores == "gram":
        cores = bn.gram_cores(snap.soak()["found"], snap.docker()["containers"])
    if cores and bn.shutil.which("taskset") is None:
        raise bn.Refusal("bench.taskset-missing", "--taskset needs the taskset command (Linux)")
    externals = parse_externals(a.external)
    if not tools.get("table"):
        raise bn.Refusal("bench.no-table", "no table binary: %s" % bn.why_missing(tools, "table"))
    out_dir = pathlib.Path(a.out) if a.out else jgen.default_dir()
    if gen.inside_repo(out_dir):
        raise bn.Refusal("bench.inside-repo", "%s is inside the repository: data is never committed, give --out outside it" % out_dir)
    plan = bn.split_csv(a.questions)
    shell_paths = jref.paths_of(out_dir, rows)
    contenders = build_contenders(tools, shell_paths, families, cores, externals)
    est_bytes = jgen.estimate_bytes(rows)
    emit("estimate: %d rows: the four tables are about %d bytes (%d free are needed), %d process launches (%d questions x %d contenders x (1 warm-up + %d runs + 1 for the peak memory + 1 for the answer))" % (
        rows, est_bytes, 3 * est_bytes, bn.launches(len(plan), len(contenders), a.runs), len(plan), len(contenders), a.runs))
    if a.estimate:
        return 0
    if bn.needs_opt_in(rows) and not a.accept_large:
        raise bn.Refusal("bench.large-size", "%d rows is above the smoke size of %d: the maintainer decides these steps; read the estimate above and pass --accept-large" % (rows, bn.SMOKE_ROWS))
    t_start = time.perf_counter()
    paths, meta, gen_s = ensure_files(rows, a.seed, out_dir)
    sizes_real = {t: meta["tables"][t]["bytes"] for t in jgen.TABLES}
    step = {"rows": rows, "estimated_bytes": est_bytes, "file_bytes": sum(sizes_real.values()), "table_bytes": sizes_real, "generate_s": round(gen_s, 3)}
    emit("files: %s; total %d bytes (estimated %d)%s" % (", ".join("%s %d" % (t, b) for t, b in sizes_real.items()), step["file_bytes"], est_bytes, "" if gen_s else " (reused, sha256 checked)"))
    info = {"rows": rows, "k": 0, "seed": a.seed, "runs": a.runs, "step": a.rows, "path_class": a.path_class, "taskset": cores, "threads_many": 1,
            "file": {"name": paths["x"].name, "bytes": sizes_real["x"], "sha256": meta["tables"]["x"]["sha256"]},
            "tables": meta["tables"], "contenders": {c.name: c.info for c in contenders}}
    cond = bn.collect_conditions(snap, info)
    ok, reasons = bn.evaluate_publishable(cond)
    results = {"schema": bn.SCHEMA, "suite": "join", "label": meta["label"], "results": [], "not_run": missing_contenders(tools, families), "dropped": [], "conditions": cond,
               "publishable": ok, "publishable_reasons": reasons, "step": step}
    emit("publishable: %s%s" % (str(ok).lower(), "" if ok else " (reasons: %s)" % "; ".join(reasons)))
    for n in results["not_run"]:
        emit("NOT RUN: %s (%s)" % (n["contender"], n["reason"]))
    skips = {}
    for spec in a.skip:
        target, _, why = spec.partition("=")
        qid, sep, who = target.partition(":")
        if not sep or not who or qid not in jref.JOINS:
            raise bn.Refusal("bench.skip", "--skip QID:CONTENDER[=REASON], e.g. q5:csvtk=out of memory: %r" % spec)
        skips[(qid, who)] = why or "no reason given"
    tmp = tempfile.mkdtemp(prefix="named-join-")
    ctx = JoinContext(launch=bn.real_launch, rusage=bn.real_rusage, emit=emit, runs=a.runs, timeout=a.timeout, file=str(paths["x"]), ref={}, exact={}, table=tools["table"], skip=skips,
                      paths={t: str(p) for t, p in paths.items()}, tmp=tmp, launch_file=launch_to_file)
    status = 0
    try:
        t0 = time.perf_counter()
        problems = jref.validate_layout(out_dir, rows)
        if problems:
            raise bn.Refusal("bench.layout", "the files are not the documented layout: %s" % problems[0], 4)
        ctx.reference = jref.reference(out_dir, rows)
        other = jref.reference_sqlite(out_dir, rows)
        for qid in ctx.reference:
            d = jref.compare(qid, ctx.reference[qid], other[qid])
            if d:
                raise bn.Refusal("bench.reference-disagrees", "the streaming reference and SQLite differ: %s; the files are not used" % d, 4)
        step["reference_s"] = round(time.perf_counter() - t0, 3)
        results["reference"] = {qid: {**acc.as_dict(), "chk": acc.chk()} for qid, acc in ctx.reference.items()}
        emit("reference: layout holds, SQLite agrees on %s" % ", ".join(sorted(ctx.reference)))
        if a.drop_from:
            prior = json.loads(pathlib.Path(a.drop_from).read_text())
            ctx.dropped |= {tuple(x) for x in prior.get("dropped", [])}
        floor = bn.real_rusage(["true"], timeout=30)
        cond["rss_floor_kb"] = floor["peak_rss_kb"] if floor else None
        t1 = time.perf_counter()
        questions = {s["id"]: s for s in jref.load_questions()["questions"]}
        for qid in wanted_q:
            emit("question %s: %s" % (qid, questions[qid]["name"]))
            results["results"] += run_join_question(ctx, questions[qid], contenders)
        step["timing_s"] = round(time.perf_counter() - t1, 3)
        results["refusal_report"] = refusal_report(tools["table"], {t: str(p) for t, p in paths.items()}, tuple(wanted_q), a.timeout, a.refusal_bound)
        for qid, row in results["refusal_report"].items():
            emit("report  %s  at the default bounds: %s%s%s" % (qid, row.get("status"), " (%s)" % row["rule"] if row.get("rule") else "",
                                                               "; repair %s: %s" % (row["repair"]["kind"], row["repair"]["reason"] or row["repair"]["options"]) if row.get("repair") else ""))
        results["dropped"] = sorted([list(x) for x in ctx.dropped])
        if any(r["status"] == "wrong-answer" for r in results["results"]):
            status = 4
            emit("GATE FAILED: a wrong answer is in the result")
    except bn.Refusal as r:
        status = r.exit
        results["refusal"] = {"rule": r.rule, "message": r.message}
        emit(r.line())
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    step["wall_s"] = round(time.perf_counter() - t_start, 3)
    cond["load"]["end"] = [float(x) for x in probes.loadavg()]
    emit("step %s: %d rows, tables %d bytes (estimated %d), generated in %.1f s, wall time of the step %.1f s" % (a.rows, rows, step["file_bytes"], est_bytes, gen_s, step["wall_s"]))
    problems = bn.validate_result(results)
    if problems:
        emit("the result breaks the rules of recording: %s" % "; ".join(problems))
        status = status or 4
    res_path = pathlib.Path(a.results) if a.results else out_dir / ("named-join-%s-%s-%s.json" % (cond["machine"]["node"], cond["date"][:10], a.rows))
    res_path.parent.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(results, indent=1) + "\n")
    emit("result: %s (publishable: %s)" % (res_path, str(results["publishable"]).lower()))
    if a.write_table:
        bn.write_published_table(results, a.write_table)
        emit("published table: %s" % a.write_table)
    return status
