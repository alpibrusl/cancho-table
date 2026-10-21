#!/usr/bin/env python3
"""The timing driver of the named benchmarks (docs/named-benchmarks.md, NB2): the db-benchmark groupby questions, each contender's answer held to the reference of NB1
before any time is printed, and every number recorded with its conditions.

    python3 scripts/bench_named.py [--rows 1e6] [--runs 5] [--questions q1,q2,q3,q4,q5] [--contenders table,duckdb,csvtk,mlr,pandas]
                                   [--threads N] [--out DIR] [--results FILE] [--table build/table] [--timeout 120]
    python3 scripts/bench_named.py --rows 1e8 --estimate                  # the arithmetic of a step, nothing generated, nothing run
    python3 scripts/bench_named.py --rows 1e8 --accept-large --scan-probe-only   # the first thing the 1e8 step reports (design 4.4)
    python3 scripts/bench_named.py --from-result R.json --write-table T.md       # a published table from a result: refused unless the run was publishable

What a step does, in this order, and prints nothing that is a time before the third is done for every contender of the question:

1. reads the conditions (load, uptime, docker, soak, virtual machine) before it generates anything, writes the file of NB1 (or reuses it, checked against its sha256), and holds the
   file to the documented layout, the reference to a second computation (SQLite) and, for `table`, the flag form to the `--query` form;
2. for each question, runs each contender's form once and judges the answer against the reference (`compare_answer`): a wrong answer, a refusal, a crash and a timeout are
   recorded as that, with the reason and **no time** (G-answer, G-refusal);
3. only then, for the contenders whose answer passed: one untimed warm-up each, then N rounds in which every contender runs once and the order rotates, the output going to
   /dev/null; the minimum of the N is the figure, the median and the first run are kept beside it; one more run, measured, gives the peak resident set and the CPU time;
4. writes a JSON result with the conditions of design 3.1 and `publishable`: false, with the reasons, when the one-minute load at the start exceeded a quarter of the cores,
   the machine is a virtual machine (or cannot be told to be a dedicated one), a soak or a container is running, or fewer than 5 runs were made (Q11, Q12). The writer of a
   published table (`--write-table`, `write_published_table`) recomputes that from the recorded conditions and refuses, whatever flag the file carries.

1e7 and 1e8 rows are explicit opt-ins (`--accept-large`) after the estimate (bytes and process launches) is printed; the driver estimates no wall time (nothing was measured).
It starts every process through procs.py, writes no data inside the repository, and times nothing it has not judged. Exit status: 0 ran (a refusal or a timeout of a contender is a
result), 3 refused to run (a rule tag is printed), 4 a gate failed (a wrong answer, a stale classification, a file or a reference that is not what NB1 documents).
"""

import argparse
import csv
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import statistics
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from fractions import Fraction

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
if __name__ == "__main__":
    sys.modules["bench_named"] = sys.modules["__main__"]   # the join suite imports this module by name: one module, one Refusal class, one set of dataclasses
import bench_named_gen as gen  # noqa: E402
import bench_named_ref as ref  # noqa: E402

SCHEMA = "bench-named.v1"
PINS = {"duckdb": "1.5.6", "csvtk": "0.38.0", "mlr": "6.22.0", "pandas": "3.0.3"}  # docs/named-benchmarks.md 3.6
SMOKE_ROWS = 1_000_000          # the largest step that runs without --accept-large
MIN_RUNS = 5                    # the method's minimum of N (3.2): fewer is never published
LOAD_DIVISOR = 4                # loaded = the one-minute load at the start exceeds cores / 4 (3.1)
DISK_FACTOR = 3                 # a step needs three times its file free (3.5)
TOLERANCE = 1e-9                # a double must be within this relative error of the exact value (3.3)
TOLERANCE_FRACTION = Fraction(1, 10**9)
DEFAULT_TIMEOUT = 120.0         # a launch over this is recorded "over 120 s" and dropped from the larger steps (3.5)
FAMILIES = ("table", "duckdb", "csvtk", "mlr", "pandas")
STATUSES = ("ok", "wrong-answer", "refused", "crashed", "timeout", "failed-while-timing", "not-expressible", "class-mismatch", "not-run")
LEVELS = ("exact", "numeric", "tolerance")
NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

REQUIRED_CONDITIONS = [
    "machine.node", "machine.system", "machine.architecture", "machine.cpu_model", "machine.cores", "machine.affinity_cores", "machine.ram_bytes",
    "virtualised.evidence", "load.start", "uptime", "docker.status", "soak.found", "contenders", "file.name", "file.bytes", "file.sha256", "file.path_class",
    "seed", "rows", "k", "step", "runs", "statistic", "sink", "cache", "date", "python",
]
NOT_BUILT = ("join", "1brc", "clickbench", "taxi", "json")


class Refusal(Exception):
    """The driver declines to go on, with a rule tag (every refusal has one) and an exit status."""

    def __init__(self, rule, message, exit=3):
        super().__init__(message)
        self.rule, self.message, self.exit = rule, message, exit

    def line(self):
        return json.dumps({"ok": False, "rule": self.rule, "message": self.message, "exit": self.exit})


class AnswerError(ValueError):
    """A contender's output is not a table of cells."""


# ---------------------------------------------------------------- arithmetic: sizes, launches, disk, load

def parse_rows(text):
    """'1e6' -> 1000000. A positive whole number, or ValueError."""
    try:
        v = Decimal(str(text).strip())
    except InvalidOperation:
        raise ValueError("not a number of rows: %r" % (text,))
    if not v.is_finite() or v != v.to_integral_value() or v <= 0:
        raise ValueError("rows must be a positive whole number: %r" % (text,))
    return int(v)


def launches(n_questions, n_contenders, runs):
    """Process launches of a step: per (question, contender) one warm-up, N runs, one for the peak memory and one for the answer (3.5: 5 x 5 x 8 = 200)."""
    return n_questions * n_contenders * (runs + 3)


def estimate(rows, k, n_questions, n_contenders, runs):
    """What a step costs in bytes and launches: arithmetic only. No wall time: nothing was measured (3.5)."""
    size = gen.estimate_bytes(rows, k)
    return {"rows": rows, "k": k, "bytes": size, "disk_needed": DISK_FACTOR * size, "launches": launches(n_questions, n_contenders, runs)}


def disk_ok(free_bytes, need_bytes):
    return free_bytes >= DISK_FACTOR * need_bytes


def needs_opt_in(rows):
    return rows > SMOKE_ROWS


def gram_cores(soak_found, containers):
    """Q11: cores 0-6 only after confirming that no soak and no container is running, else 0-5."""
    return "0-5" if soak_found or containers else "0-6"


def with_taskset(argv, cores):
    return list(argv) if not cores else ["taskset", "-c", cores, *argv]


def summarise(times):
    """The figure and its companions: the minimum, the median, the first run (listed separately, 3.2) and all runs."""
    if not times:
        raise ValueError("no run to summarise")
    t = [float(x) for x in times]
    return {"runs": len(t), "min_s": min(t), "median_s": statistics.median(t), "first_s": t[0], "all_s": t}


def read_load(getloadavg=os.getloadavg):
    return tuple(float(x) for x in getloadavg())


def is_loaded(load1, cores):
    """3.1: loaded when the one-minute load average at the start exceeds a quarter of the cores (equal is not loaded)."""
    return load1 * LOAD_DIVISOR > cores


# ---------------------------------------------------------------- the probes of the machine, and the conditions

def _run_text(argv, timeout=15):
    """(returncode, stdout, stderr) as text, or (None, "", reason) when it cannot be started."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, errors="replace")
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return None, "", str(e)


CPUINFO = pathlib.Path("/proc/cpuinfo")


class Probes:
    """What the machine says about itself. A test replaces any method, so that no test of the arithmetic needs the machine."""

    def loadavg(self):
        return read_load()

    def cpu_count(self):
        return os.cpu_count() or 1

    def affinity(self):
        return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else self.cpu_count()

    def cpu_model(self):
        try:
            for line in CPUINFO.read_text(errors="replace").splitlines():
                if line.lower().startswith(("model name", "hardware", "cpu model")):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
        rc, out, _ = _run_text(["sysctl", "-n", "machdep.cpu.brand_string"])
        return out.strip() if rc == 0 and out.strip() else (platform.processor() or platform.machine())

    def ram_bytes(self):
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            return 0

    def virtualised(self):
        """(True | False | None, evidence). A hypervisor means other tenants may share the cores: such a machine is never a dedicated one (3.1, Q12)."""
        try:
            text = CPUINFO.read_text(errors="replace")
            flags = [l for l in text.splitlines() if l.lower().startswith("flags")]
            if flags:
                if any(" hypervisor" in " " + l.split(":", 1)[1] for l in flags):
                    return True, "the 'hypervisor' flag is set in /proc/cpuinfo"
                return False, "no 'hypervisor' flag in /proc/cpuinfo"
        except OSError:
            pass
        if platform.system() == "Darwin":
            rc, out, _ = _run_text(["sysctl", "-n", "kern.hv_vmm_present"])
            if rc == 0 and out.strip() in ("0", "1"):
                return out.strip() == "1", "sysctl kern.hv_vmm_present = %s" % out.strip()
        rc, out, _ = _run_text(["systemd-detect-virt"])
        if rc is not None and out.strip():
            return (out.strip() != "none"), "systemd-detect-virt: %s" % out.strip()
        return None, "no way to tell on %s: no cpu flags, no sysctl, no systemd-detect-virt" % platform.system()

    def uptime(self):
        rc, out, err = _run_text(["uptime"])
        return out.strip() if rc == 0 and out.strip() else "uptime is not available: %s" % (err.strip()[:100] or "no output")

    def docker(self):
        if shutil.which("docker") is None:
            return {"status": "not-installed", "containers": [], "detail": "docker is not installed"}
        rc, out, err = _run_text(["docker", "ps", "--format", "{{.Names}}"])
        if rc == 0:
            names = [l.strip() for l in out.splitlines() if l.strip()]
            return {"status": "ok", "containers": names, "detail": "docker ps lists %d container(s)" % len(names)}
        return {"status": "error", "containers": [], "detail": "docker ps failed: %s" % (err.strip().splitlines() or ["no output"])[0][:150]}

    def soak(self):
        rc, out, err = _run_text(["pgrep", "-af", "soak"])
        if rc is None:
            return {"found": False, "lines": [], "detail": "pgrep is not available: %s" % err[:100]}
        lines = [l.strip()[:200] for l in out.splitlines() if l.strip() and "bench_named" not in l and "pgrep" not in l]
        return {"found": bool(lines), "lines": lines, "detail": "pgrep -af soak"}

    def now(self):
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def node(self):
        return platform.node() or "unknown"

    def system(self):
        return platform.system()

    def machine(self):
        return platform.machine()

    def python(self):
        return platform.python_version()


class Snapshot:
    """The probes read once, at the start, before the driver puts any load of its own on the machine; the same methods, with the values read then."""

    NAMES = ("loadavg", "cpu_count", "affinity", "cpu_model", "ram_bytes", "virtualised", "uptime", "docker", "soak", "now", "node", "system", "machine", "python")

    def __init__(self, probes):
        for name in self.NAMES:
            setattr(self, name, (lambda v: lambda: v)(getattr(probes, name)()))


def collect_conditions(probes, info):
    """The conditions of design 3.1 for one step. `info` is what the driver knows: rows, k, seed, runs, step, path_class, taskset, the file's name, bytes and sha256, the contenders."""
    v, evidence = probes.virtualised()
    runs = info["runs"]
    return {
        "machine": {"node": probes.node(), "system": probes.system(), "architecture": probes.machine(), "cpu_model": probes.cpu_model(), "cores": probes.cpu_count(),
                    "affinity_cores": probes.affinity(), "ram_bytes": probes.ram_bytes()},
        "virtualised": {"value": v, "evidence": evidence},
        "load": {"start": [float(x) for x in probes.loadavg()]},
        "uptime": probes.uptime(),
        "docker": probes.docker(),
        "soak": probes.soak(),
        "taskset": info.get("taskset"),
        "threads_many": info.get("threads_many"),
        "contenders": info["contenders"],
        "file": {**info["file"], "path_class": info.get("path_class") or "not stated"},
        **({"tables": info["tables"]} if info.get("tables") else {}),
        "seed": info["seed"], "rows": info["rows"], "k": info["k"], "step": info["step"], "runs": runs,
        "statistic": "minimum of %d runs after one untimed warm-up, the contenders interleaved (each once per round, the order rotating); the median and the first run are kept beside it" % runs,
        "sink": "/dev/null",
        "cache": "warm page cache (the page cache is not dropped; the file is read once before the first timed run)",
        "date": probes.now(),
        "python": probes.python(),
    }


def _dig(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return False, None
        d = d[part]
    return True, d


def missing_conditions(c):
    """The dotted names of the conditions a number must carry that are absent or empty; the contenders are checked one by one for their version and threads."""
    out = []
    for p in REQUIRED_CONDITIONS:
        found, value = _dig(c, p)
        if not found or value is None or value == "" or (p == "contenders" and not value):
            out.append(p)
        elif p == "load.start" and not (isinstance(value, list) and len(value) == 3 and all(isinstance(x, (int, float)) for x in value)):
            out.append(p)
    found, cs = _dig(c, "contenders")
    if found and isinstance(cs, dict):
        for name, info in cs.items():
            for key in ("version", "threads"):
                if not isinstance(info, dict) or info.get(key) in (None, ""):
                    out.append("contenders.%s.%s" % (name, key))
    return out


def evaluate_publishable(c):
    """(publishable, reasons) from the recorded conditions alone (Q11, Q12): the load at the start, the virtual machine, a soak or a container, the runs."""
    miss = missing_conditions(c)
    if miss:
        return False, ["conditions missing: %s" % ", ".join(miss)]
    reasons = []
    cores = c["machine"]["cores"]
    load1 = c["load"]["start"][0]
    if is_loaded(load1, cores):
        reasons.append("loaded: the one-minute load average at the start was %.2f, over a quarter of the %d cores (%.2f)" % (load1, cores, cores / LOAD_DIVISOR))
    v = c["virtualised"]["value"]
    if v is True:
        reasons.append("shared virtual machine: %s" % c["virtualised"]["evidence"])
    elif v is None:
        reasons.append("cannot tell whether this is a shared virtual machine: %s" % c["virtualised"]["evidence"])
    d = c["docker"]
    if d["status"] == "error":
        reasons.append("docker ps failed, so no soak container can be ruled out: %s" % d.get("detail", ""))
    elif d["containers"]:
        reasons.append("docker ps lists %d running container(s): %s" % (len(d["containers"]), ", ".join(d["containers"][:5])))
    if c["soak"]["found"]:
        reasons.append("a soak is running: %s" % "; ".join(c["soak"]["lines"][:3]))
    if c["runs"] < MIN_RUNS:
        reasons.append("runs: %d, fewer than the %d of the method" % (c["runs"], MIN_RUNS))
    return (not reasons), reasons


# ---------------------------------------------------------------- the answer gate

def column_kinds(columns):
    """key | int | dec | mean for each column of a question, from its name: 'mean:*' is an exact mean, 'sum:v3' an exact decimal sum, other sums and count are integers."""
    kinds = []
    for c in columns:
        if c.startswith("mean:"):
            kinds.append("mean")
        elif c == "sum:v3":
            kinds.append("dec")
        elif c.startswith("sum:") or c == "count":
            kinds.append("int")
        else:
            kinds.append("key")
    return kinds


def exact_means(path):
    """The exact rational of every mean of q3 and q4, from a pass over the written file, for the double check of 3.3. {qid: {key tuple: (Fraction, ...)}}."""
    q3, q4 = {}, {}
    with open(path, "r", encoding="ascii", newline="") as f:
        f.readline()
        for line in f:
            id1, id2, id3, id4, id5, id6, v1, v2, v3 = line.rstrip("\n").split(",")
            m = ref.millionths(v3)
            g = q3.get(id3)
            if g is None:
                q3[id3] = [m, 1]
            else:
                g[0] += m
                g[1] += 1
            g = q4.get(id4)
            if g is None:
                q4[id4] = [int(v1), int(v2), m, 1]
            else:
                g[0] += int(v1)
                g[1] += int(v2)
                g[2] += m
                g[3] += 1
    return {"q3": {(k,): (Fraction(s, n * ref.SCALE),) for k, (s, n) in q3.items()},
            "q4": {(k,): (Fraction(a, n), Fraction(b, n), Fraction(c, n * ref.SCALE)) for k, (a, b, c, n) in q4.items()}}


def check_exact_against_reference(exact, reference):
    """The rationals above, rounded half to even at scale 6, must be the text of NB1's reference: the two are one computation seen twice."""
    for qid, cols in (("q3", [2]), ("q4", [1, 2, 3])):
        if qid not in reference:
            continue
        by_key = {(r[0],): r for r in reference[qid]["rows"]}
        if set(by_key) != set(exact[qid]):
            return "%s: the groups of the exact means are not the reference's" % qid
        for key, fracs in exact[qid].items():
            for fr, c in zip(fracs, cols):
                if ref.fmt6(ref.div_half_even(fr.numerator * ref.SCALE, fr.denominator)) != by_key[key][c]:
                    return "%s %r: the exact mean rounds to something else than the reference" % (qid, key)
    return None


def parse_output(raw):
    """A contender's stdout as (header, rows): UTF-8, '\\n' line ends, a final newline, no blank line, a rectangle of cells. AnswerError otherwise, and nothing else."""
    if not raw:
        raise AnswerError("empty output")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AnswerError("the output is not UTF-8")
    if text.startswith("\ufeff"):
        raise AnswerError("the output starts with a byte order mark")
    if "\r" in text:
        raise AnswerError("a carriage return in the output")
    if "\0" in text:
        raise AnswerError("a NUL byte in the output")
    if not text.endswith("\n"):
        raise AnswerError("no final newline")
    lines = text.split("\n")[:-1]   # a blank line is a row of no cells, which the rectangle below refuses
    try:
        rows = list(csv.reader(lines, strict=True))
    except csv.Error as e:
        raise AnswerError("not CSV: %s" % e)
    if not rows:
        raise AnswerError("no header line")
    header = rows[0]
    for i, r in enumerate(rows[1:], 1):
        if len(r) != len(header):
            raise AnswerError("row %d has %d cells, the header %d" % (i, len(r), len(header)))
    return header, rows[1:]


def _clip(s, n=40):
    s = "".join(ch if ch.isprintable() else "?" for ch in str(s))
    return s if len(s) <= n else s[:n] + "..."


def _cell(kind, got, want, profile, truth):
    """The level at which one cell equals the reference: 0 identical text, 1 the same value in another spelling, 2 within the tolerance of a double; None if it does not."""
    if got == want:
        return 0
    if profile == "exact" or kind == "key":
        return None
    if len(got) > 64 or not NUMBER.match(got):
        return None
    try:
        dg, dw = Decimal(got), Decimal(want)
    except InvalidOperation:
        return None
    if not dg.is_finite() or abs(dg.adjusted()) > 40:
        return None
    if dg == dw:
        return 1
    if truth is None:   # an integer column has no truth to be near: it is the same integer or it is wrong
        return None
    diff = abs(Fraction(dg) - truth)
    return 2 if (diff == 0 if truth == 0 else diff <= TOLERANCE_FRACTION * abs(truth)) else None


def compare_answer(question, ref_entry, raw, profile, exact=None, ordered=False):
    """The verdict of one contender's output against NB1's reference entry: {"ok", "level" (exact | numeric | tolerance | None), "problem", "tolerance"}.

    profile 'exact' (table): every cell is the reference's text, the header is the question's columns. profile 'double' (other engines): the keys are the reference's text, an
    integer is the same integer in whatever spelling, a decimal or a mean is the same number or within 1e-9 relative of the exact value, the header only has to have the right
    number of columns. Rows are a set unless `ordered`."""

    def bad(why):
        return {"ok": False, "level": None, "problem": why, "tolerance": None}

    try:
        header, rows = parse_output(raw)
    except AnswerError as e:
        return bad(str(e))
    columns = question["columns"]
    kinds = column_kinds(columns)
    if any(h == "" or h != h.strip() for h in header):
        return bad("a header cell is empty or has spaces around it")
    if profile == "exact":
        if header != columns:
            return bad("columns %s, expected %s" % (_clip(header, 80), _clip(columns, 80)))
    elif len(header) != len(columns):
        return bad("%d columns, expected %d" % (len(header), len(columns)))
    want = ref_entry["rows"]
    if len(rows) != len(want):
        return bad("%d rows, the reference has %d" % (len(rows), len(want)))
    nk = kinds.count("key")
    if not ordered:
        rows = sorted(rows, key=lambda r: tuple(r[:nk]))
    means = (exact.get(question["id"], exact) if exact else None)   # the mapping of exact_means(), or the one question's part of it
    level = 0
    for i, (got, w) in enumerate(zip(rows, want)):
        key = tuple(w[:nk])
        mean_at = 0
        for j, kind in enumerate(kinds):
            truth = None
            if kind == "dec":
                truth = Fraction(Decimal(w[j]))
            elif kind == "mean":
                truth = means[key][mean_at] if means and key in means else None
                mean_at += 1
            lvl = _cell(kind, got[j], w[j], profile, truth)
            if lvl is None:
                where = "row %d (%s), column %s" % (i, _clip(",".join(key)), columns[j])
                if kind == "mean" and truth is None and profile != "exact":
                    return bad("%s: no exact mean to hold %s to" % (where, _clip(got[j])))
                return bad("%s: got %s, the reference has %s" % (where, _clip(got[j]), _clip(w[j])))
            level = max(level, lvl)
    return {"ok": True, "level": LEVELS[level], "problem": None, "tolerance": TOLERANCE if level == 2 else None}


def rule_of(stdout):
    """The first rule tag of a refusal of the tool (its first stdout line is JSON with 'errors'), or None; never raises."""
    try:
        first = stdout.decode("utf-8", "replace").splitlines()[0]
        errs = json.loads(first).get("errors")
        return str(errs[0]["rule"])[:80] if errs else None
    except Exception:
        return None


_STDERR_RULE = re.compile(r"^table: ([a-z]+\.[a-z0-9-]+): ", re.M)


def rule_of_stderr(stderr):
    """A refusal written in a csv answer is one line on standard error, `table: RULE: message`; its rule tag, or None. Never raises."""
    m = _STDERR_RULE.search(stderr.decode("utf-8", "replace")[:2000])
    return m.group(1) if m else None


def _short(raw, n=200):
    for line in raw.decode("utf-8", "replace").splitlines():
        line = "".join(ch for ch in line if ch.isprintable()).strip()
        if line:
            return line[:n]
    return ""


def classify(returncode, stdout, stderr, is_table, timed_out):
    """A launch as a status: ok, timeout, crashed (a signal or a trap status), or refused (any other non-zero exit), with the rule tag of the tool when it is `table` and the reason."""
    if timed_out:
        return {"status": "timeout", "rule": None, "reason": "timeout"}
    if returncode == 0:
        return {"status": "ok", "rule": None, "reason": None}
    if returncode is None or returncode < 0 or returncode >= 128:
        why = "signal %d" % -returncode if returncode and returncode < 0 else "exit %s" % returncode
        return {"status": "crashed", "rule": None, "reason": why}
    if is_table:
        rule = rule_of(stdout) or rule_of_stderr(stderr)
        return {"status": "refused", "rule": rule, "reason": "%s (exit %d)" % (rule or "no rule tag", returncode)}
    return {"status": "refused", "rule": None, "reason": "exit %d: %s" % (returncode, _short(stderr))}


# ---------------------------------------------------------------- launching and measuring

@dataclasses.dataclass
class Outcome:
    returncode: object
    seconds: float
    stdout: bytes
    stderr: bytes
    timed_out: bool


@dataclasses.dataclass
class Form:
    answer: list              # the argv whose stdout is the answer
    timed: list               # the argv that is timed (the same, the output to /dev/null)
    env: dict = dataclasses.field(default_factory=dict)
    alt: object = None        # table only: the --query form, whose bytes must equal the flag form's


@dataclasses.dataclass
class Contender:
    name: str
    family: str
    threads: str
    profile: str
    version: str
    form: object              # (qid, file) -> Form
    path: object = None
    info: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Context:
    launch: object            # (argv, env=, capture=, timeout=) -> Outcome
    rusage: object            # (argv, env=, timeout=) -> {"peak_rss_kb", "cpu_s"} or None
    emit: object              # a line of output
    runs: int
    timeout: float
    file: str
    ref: dict
    exact: dict
    table: object
    dropped: set = dataclasses.field(default_factory=set)
    skip: dict = dataclasses.field(default_factory=dict)       # (question, contender or family) -> the operator's reason


def real_launch(argv, env=None, capture=True, timeout=DEFAULT_TIMEOUT, run=None, clock=None):
    """One launch, its wall time from fork to exit by `clock`; the group is killed at the deadline. `run` and `clock` are injectable."""
    run = run or subprocess.run
    clock = clock or time.perf_counter
    full = None
    if env:
        full = dict(os.environ)
        full.update(env)
    sink = subprocess.PIPE if capture else subprocess.DEVNULL
    t0 = clock()
    try:
        p = run(argv, stdout=sink, stderr=sink, stdin=subprocess.DEVNULL, timeout=timeout, env=full)
    except subprocess.TimeoutExpired:
        return Outcome(None, clock() - t0, b"", b"", True)
    except OSError as e:
        return Outcome(127, clock() - t0, b"", str(e).encode(), False)
    return Outcome(p.returncode, clock() - t0, p.stdout or b"", p.stderr or b"", False)


# A small program that runs one command with its output discarded and reports what wait4 says about that child: the peak resident set and the CPU time.
# (The launcher's own resident set is a floor under the peak, as with /usr/bin/time and tests/conformance/maxrss.c; the floor is measured and recorded.)
_WRAP = (
    "import os, sys\n"
    "out, argv = sys.argv[1], sys.argv[2:]\n"
    "pid = os.fork()\n"
    "if pid == 0:\n"
    "    os.dup2(os.open(os.devnull, os.O_WRONLY), 1)\n"
    "    os.dup2(os.open(os.devnull, os.O_WRONLY), 2)\n"
    "    try:\n"
    "        os.execvp(argv[0], argv)\n"
    "    except OSError:\n"
    "        os._exit(127)\n"
    "_, status, ru = os.wait4(pid, 0)\n"
    "open(out, 'w').write('%d %d %.6f %.6f' % (os.waitstatus_to_exitcode(status), ru.ru_maxrss, ru.ru_utime, ru.ru_stime))\n"
)


def real_rusage(argv, env=None, timeout=DEFAULT_TIMEOUT):
    """{"peak_rss_kb", "cpu_s"} of one run of `argv`, or None if it did not exit 0 or could not be measured."""
    full = None
    if env:
        full = dict(os.environ)
        full.update(env)
    with tempfile.TemporaryDirectory(prefix="bench-named-ru-") as d:
        out = pathlib.Path(d) / "ru"
        try:
            subprocess.run([sys.executable, "-S", "-E", "-c", _WRAP, str(out), *argv], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, env=full)
            code, rss, user, system = out.read_text().split()
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    if int(code) != 0:
        return None
    kb = int(rss) // 1024 if platform.system() == "Darwin" else int(rss)
    return {"peak_rss_kb": kb, "cpu_s": round(float(user) + float(system), 6)}


# ---------------------------------------------------------------- one question

def base_record(q, c):
    return {"question": q["id"], "contender": c.name, "family": c.family, "threads": c.threads, "version": c.version, "status": None, "reason": None, "rule": None}


def _answer_line(q, c, text):
    return "answer  %s  %s  %s" % (q["id"], c.name, text)


def run_question(ctx, q, contenders):
    """Gate every contender's answer, then time the ones that passed. Returns one record per contender. Nothing that is a time is emitted before every answer is judged."""
    qid = q["id"]
    records, live = [], []
    for c in contenders:
        rec = base_record(q, c)
        records.append(rec)
        if (qid, c.name) in ctx.dropped:
            rec.update(status="not-run", reason="dropped: over %g s on this question at a smaller step" % ctx.timeout)
            ctx.emit(_answer_line(q, c, "NOT TIMED: not-run: %s" % rec["reason"]))
            continue
        skip = ctx.skip.get((qid, c.name)) or ctx.skip.get((qid, c.family))
        if skip is not None:
            rec.update(status="not-run", reason="skipped by the operator: %s" % skip)
            ctx.emit(_answer_line(q, c, "NOT TIMED: not-run: %s" % rec["reason"]))
            continue
        form = c.form(qid, ctx.file)
        out = ctx.launch(form.answer, env=form.env, capture=True, timeout=ctx.timeout)
        cl = classify(out.returncode, out.stdout, out.stderr, c.family == "table", out.timed_out)
        if cl["status"] != "ok":
            reason = "over %g s" % ctx.timeout if cl["status"] == "timeout" else cl["reason"]
            rec.update(status=cl["status"], reason=reason, rule=cl["rule"])
            if cl["status"] == "timeout":
                ctx.dropped.add((qid, c.name))
            ctx.emit(_answer_line(q, c, "NOT TIMED: %s: %s" % (cl["status"], reason)))
            continue
        v = compare_answer(q, ctx.ref[qid], out.stdout, c.profile, exact=ctx.exact)
        if not v["ok"]:
            rec.update(status="wrong-answer", reason=v["problem"])
            ctx.emit(_answer_line(q, c, "NOT TIMED: wrong-answer: %s" % v["problem"]))
            continue
        rec["answer"] = {"match": v["level"], "tolerance": v["tolerance"]}
        if form.alt is not None:
            other = ctx.launch(form.alt, env=form.env, capture=True, timeout=ctx.timeout)
            rec["forms_equal"] = other.returncode == 0 and not other.timed_out and other.stdout == out.stdout
            if not rec["forms_equal"]:
                rec.pop("answer")
                rec.update(status="wrong-answer", reason="the flag form and the --query form do not give the same bytes")
                ctx.emit(_answer_line(q, c, "NOT TIMED: wrong-answer: %s" % rec["reason"]))
                continue
        rec["status"] = "ok"
        text = {"exact": "ok (exact)", "numeric": "ok (value-equal: the text differs, the value is the same)",
                "tolerance": "ok (only by tolerance %.0e: a double within a billionth of the exact value)" % TOLERANCE}[v["level"]]
        ctx.emit(_answer_line(q, c, text))
        live.append((c, form, rec))
    if live:
        time_live(ctx, q, live)
    return records


def time_live(ctx, q, live):
    """The timing of the contenders whose answers passed (`live`: (contender, form, record) triples, edited in place): one untimed warm-up each, N rounds with the order rotating, one measured run
    each for the peak resident set and the CPU time. A contender that fails in any of them loses its times. Shared by every suite: the suite judges the answers, this times them."""
    qid = q["id"]

    def fail(item, why):
        c, form, rec = item     # its times are set only after every run, so none is kept
        rec.update(status="failed-while-timing", reason=why)
        ctx.emit("note  %s  %s  failed while timing, no time kept: %s" % (qid, c.name, why))

    def launch_timed(item):
        out = ctx.launch(item[1].timed, env=item[1].env, capture=False, timeout=ctx.timeout)
        if out.timed_out or out.returncode != 0:
            fail(item, "over %g s" % ctx.timeout if out.timed_out else "exit %s" % out.returncode)
            return None
        return out.seconds

    times = {c.name: [] for c, _, _ in live}
    warm = {}
    for item in list(live):                      # one untimed warm-up each
        s = launch_timed(item)
        if s is None:
            live.remove(item)
        else:
            warm[item[0].name] = s
    for r in range(ctx.runs):                    # N rounds, each contender once, the order rotating
        n = len(live)
        for item in (live[r % n:] + live[:r % n]) if n else []:
            s = launch_timed(item)
            if s is None:
                live.remove(item)
            else:
                times[item[0].name].append(s)
    for item in list(live):                      # one more run each, for the peak resident set and the CPU time
        res = ctx.rusage(item[1].timed, env=item[1].env, timeout=ctx.timeout)
        if res is None:
            fail(item, "the measured run did not exit 0")
            live.remove(item)
        else:
            item[2]["peak_rss_kb"], item[2]["cpu_s"] = res["peak_rss_kb"], res["cpu_s"]
    for c, form, rec in live:
        rec["timing"] = {**summarise(times[c.name]), "warmup_s": warm[c.name]}
        t = rec["timing"]
        ctx.emit("time  %s  %s  min %.6f s of %d  median %.6f s  first %.6f s  peak %s KiB  cpu %s s" % (
            qid, c.name, t["min_s"], t["runs"], t["median_s"], t["first_s"], rec["peak_rss_kb"], rec["cpu_s"]))


def probe_not_expressible(ctx, q):
    """A question `table` cannot answer is a result (3.4): its probe must be refused with the stated rule, now, or the classification is stale and the run fails."""
    argv = [ctx.table, *ref.argv_for(q["probe"]["argv"], ctx.file)]
    out = ctx.launch(argv, env=None, capture=True, timeout=ctx.timeout)
    rule = rule_of(out.stdout)
    rec = {"question": q["id"], "contender": "table", "family": "table", "threads": "-", "version": "-", "status": None, "reason": None, "rule": rule,
           "blocker": q["blocker"], "link": q["link"], "probe_exit": out.returncode}
    if out.timed_out or out.returncode in (0, None) or rule != q["probe"]["rule"]:
        rec.update(status="class-mismatch", reason="classified not expressible (%s) but the probe gave exit %s, rule %r: re-classify" % (q["probe"]["rule"], out.returncode, rule))
    else:
        rec.update(status="not-expressible", reason=q["blocker"])
    ctx.emit("answer  %s  table  %s: %s" % (q["id"], rec["status"], rec["reason"]))
    return rec


# ---------------------------------------------------------------- the contenders and their forms

GROUPS = {   # qid -> (key columns, [(column, op)]): what each engine is asked, in the order the question's columns have
    "q1": (["id1"], [("v1", "sum")]),
    "q2": (["id1", "id2"], [("v1", "sum")]),
    "q3": (["id3"], [("v1", "sum"), ("v3", "mean")]),
    "q4": (["id4"], [("v1", "mean"), ("v2", "mean"), ("v3", "mean")]),
    "q5": (["id6"], [("v1", "sum"), ("v2", "sum"), ("v3", "sum")]),
    "q10": (["id1", "id2", "id3", "id4", "id5", "id6"], [("v3", "sum"), ("v3", "count")]),
}

_DUCK_PY = "import duckdb, sys\nc = duckdb.connect()\nfor s in sys.argv[1:]:\n    c.execute(s)\n"
_PANDAS = ("import sys\nimport pandas as pd\nd = pd.read_csv(sys.argv[1])\n"
           "r = d.groupby(%(keys)s, as_index=False, sort=False).agg(%(aggs)s)\nr.to_csv(sys.stdout, index=False, lineterminator='\\n')\n")


def duck_sql(q, file):
    """The owner's SQL of the question, over the file instead of a loaded table (4.5)."""
    sql = q["sql"]
    if sql.count(" FROM x ") != 1:
        raise ValueError("the SQL of %s has no ' FROM x ' to replace" % q["id"])
    return sql.replace(" FROM x ", " FROM read_csv('%s') " % str(file).replace("'", "''"))


def _which(name, path=None):
    return shutil.which(name, path=path)


def _text_of(argv, timeout=60):
    rc, out, err = _run_text(argv, timeout)
    return (out + err).strip() if rc == 0 else None


def find_tools(table=None, path=None, python=None):
    """Where each contender is. {"table": path | None, "duckdb": ("cli", path) | ("python", None) | None, "csvtk": path | None, "mlr": path | None, "pandas": version | None}."""
    python = python or sys.executable
    t = pathlib.Path(table) if table else pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build")) / "table"
    tools = {"table": str(t.resolve()) if t.exists() else None, "python": python}
    cli = _which("duckdb", path)
    if cli:
        tools["duckdb"] = ("cli", cli)
    elif _text_of([python, "-I", "-c", "import duckdb; print(duckdb.__version__)"]):
        tools["duckdb"] = ("python", None)
    else:
        tools["duckdb"] = None
    tools["csvtk"] = _which("csvtk", path)
    tools["mlr"] = _which("mlr", path) or _which("miller", path)
    tools["pandas"] = _text_of([python, "-I", "-c", "import pandas; print(pandas.__version__)"])
    return tools


def why_missing(tools, family):
    if family == "table":
        return "no binary at build/table (build it, or pass --table)"
    if family == "duckdb":
        return "neither a duckdb command on PATH nor the Python module duckdb"
    if family == "pandas":
        return "the Python module pandas is not importable by %s" % tools.get("python")
    return "%s is not on PATH" % ("mlr (Miller)" if family == "mlr" else family)


def pin_status(family, version_text):
    """The version read from the tool, the design's pin (3.6) and whether they are the same."""
    m = re.search(r"\d+\.\d+\.\d+", version_text or "")
    pin = PINS.get(family)
    if pin is None:
        return {"pin": None, "pinned": None, "note": "not a pinned tool"}
    got = m.group(0) if m else None
    return {"pin": pin, "pinned": got == pin, "note": "version %s read, the design pins %s" % (got, pin)}


def table_version(path):
    """The tool's version and compiler from `introspect`, and the commit of the checkout the binary sits in."""
    version = compiler = commit = None
    dirty = None
    try:
        p = subprocess.run([path, "introspect"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        doc = json.loads(p.stdout.decode("utf-8", "replace"))
        version, compiler = doc.get("version"), doc.get("compiler")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    top = pathlib.Path(path).resolve().parent.parent
    rc, out, _ = _run_text(["git", "-C", str(top), "rev-parse", "HEAD"])
    if rc == 0 and out.strip():
        commit = out.strip()
        rc2, out2, _ = _run_text(["git", "-C", str(top), "status", "--porcelain", "--untracked-files=no"])
        dirty = bool(out2.strip()) if rc2 == 0 else None
    return {"version": version or "unknown", "compiler": compiler, "commit": commit, "dirty": dirty}


def _table_form(tools, n, cores):
    def form(qid, file):
        spec = QUESTION_BY_ID[qid]
        flags = [tools["table"], "--threads", str(n), *ref.argv_for(spec["flags"], file)]
        alt = [tools["table"], "--threads", str(n), *ref.argv_for(spec["query"], file)]
        return Form(with_taskset(flags, cores), with_taskset(flags, cores), {}, with_taskset(alt, cores))
    return form


def _duck_form(tools, threads, cores):
    kind, cli = tools["duckdb"]

    def form(qid, file):
        sql = duck_sql(QUESTION_BY_ID[qid], file)
        pre = [] if threads == "default" else ["SET threads=%s" % threads]

        def build(sink):
            copy = "COPY (%s) TO '%s' (FORMAT csv)" % (sql, sink)
            if kind == "cli":
                return with_taskset([cli, "-c", "; ".join([*pre, copy])], cores)
            return with_taskset([tools["python"], "-I", "-c", _DUCK_PY, *pre, copy], cores)
        return Form(build("/dev/stdout"), build("/dev/null"))
    return form


def _csvtk_form(tools, cores):
    def form(qid, file):
        keys, aggs = GROUPS[qid]
        argv = [tools["csvtk"], "-j", "1", "summary", "-w", "12", "-g", ",".join(keys), "-f", ",".join("%s:%s" % a for a in aggs), str(file)]
        return Form(with_taskset(argv, cores), with_taskset(argv, cores))
    return form


def _mlr_form(tools, cores):
    def form(qid, file):
        keys, aggs = GROUPS[qid]
        ops = list(dict.fromkeys(op for _, op in aggs))
        cols = list(dict.fromkeys(col for col, _ in aggs))
        argv = [tools["mlr"], "--icsv", "--ocsv", "stats1", "-a", ",".join(ops), "-f", ",".join(cols), "-g", ",".join(keys)]
        if len(ops) * len(cols) != len(aggs):   # stats1 applies every accumulator to every field: keep the ones asked, in the order asked
            argv += ["then", "cut", "-o", "-f", ",".join(keys + ["%s_%s" % a for a in aggs])]
        argv.append(str(file))
        return Form(with_taskset(argv, cores), with_taskset(argv, cores), {"GOMAXPROCS": "1"})
    return form


def _pandas_form(tools, cores):
    def form(qid, file):
        keys, aggs = GROUPS[qid]
        agg = ", ".join("a%d=(%r, %r)" % (i, col, "size" if op == "count" else op) for i, (col, op) in enumerate(aggs))
        argv = [tools["python"], "-I", "-c", _PANDAS % {"keys": repr(keys), "aggs": agg}, str(file)]
        return Form(with_taskset(argv, cores), with_taskset(argv, cores))
    return form


def _external_form(path):
    def form(qid, file):
        argv = [sys.executable, path, qid, str(file)] if str(path).endswith(".py") else [path, qid, str(file)]
        return Form(argv, argv)
    return form


def find_version(family, tools):
    if family == "table":
        return table_version(tools["table"])["version"]
    if family == "duckdb":
        kind, cli = tools["duckdb"]
        text = _text_of([cli, "--version"]) if kind == "cli" else _text_of([tools["python"], "-I", "-c", "import duckdb; print('python duckdb', duckdb.__version__)"])
        return text or "unknown"
    if family == "csvtk":
        return _text_of([tools["csvtk"], "version"]) or "unknown"
    if family == "mlr":
        return _text_of([tools["mlr"], "--version"]) or "unknown"
    return tools["pandas"] or "unknown"


def build_contenders(tools, threads_many, wanted=FAMILIES, taskset=None, externals=()):
    """The contenders that can run here, in the order of the design (3.6), with the thread settings of 3.2: table at 1 and at the machine's number, DuckDB at 1 and its default,
    csvtk at 1, Miller at 1 (GOMAXPROCS=1), pandas as it is. A family that is missing is not in the list; `missing_contenders` says why."""
    out = []
    versions = {}

    def add(name, family, threads, profile, form, path=None, extra=None):
        version = versions.setdefault(family, find_version(family, tools))
        info = {"family": family, "version": version, "threads": threads, "path": path, **pin_status(family, version), **(extra or {})}
        out.append(Contender(name, family, threads, profile, version, form, path, info))

    for family in wanted:
        if family == "table" and tools.get("table"):
            counts = [1] + ([threads_many] if threads_many and threads_many > 1 else [])
            extra = {k: v for k, v in table_version(tools["table"]).items() if k != "version"}   # once, for every thread count
            for n in counts:
                add("table@%d" % n, "table", str(n), "exact", _table_form(tools, n, taskset), tools["table"], extra)
        elif family == "duckdb" and tools.get("duckdb"):
            kind, cli = tools["duckdb"]
            for n in ("1", "default"):
                add("duckdb@%s" % n, "duckdb", n, "double", _duck_form(tools, n, taskset), cli, {"variant": kind})
        elif family == "csvtk" and tools.get("csvtk"):
            add("csvtk@1", "csvtk", "1", "double", _csvtk_form(tools, taskset), tools["csvtk"])
        elif family == "mlr" and tools.get("mlr"):
            add("mlr@1", "mlr", "1", "double", _mlr_form(tools, taskset), tools["mlr"], {"note": "GOMAXPROCS=1; stats1 applies each accumulator to each field"})
        elif family == "pandas" and tools.get("pandas"):
            add("pandas@default", "pandas", "default", "double", _pandas_form(tools, taskset), tools["python"], {"note": "pd.read_csv, the installed engine; the process wall time includes the import"})
    for name, profile, path in externals:
        out.append(Contender(name, "external", "n/a", profile, "external program, not versioned", _external_form(path), path,
                             {"family": "external", "version": "external program, not versioned", "threads": "n/a", "path": path, "pin": None, "pinned": None, "note": "any program: argv QID FILE"}))
    return out


def missing_contenders(tools, wanted):
    have = {"table": tools.get("table"), "duckdb": tools.get("duckdb"), "csvtk": tools.get("csvtk"), "mlr": tools.get("mlr"), "pandas": tools.get("pandas")}
    return [{"contender": f, "reason": why_missing(tools, f)} for f in wanted if not have.get(f)]


QUESTION_BY_ID = {q["id"]: q for q in ref.load_questions()["questions"]}


# ---------------------------------------------------------------- results

def validate_result(result, check_flag=True):
    """Problems (a list of text) between a result and the rules of recording: every record has a known status; only an ok record has a time, and its time is its runs;
    a number has its conditions; and the publishable flag is what the conditions say."""
    problems = []
    c = result.get("conditions")
    if not isinstance(c, dict):
        return ["no conditions"]
    miss = missing_conditions(c)
    if miss:
        problems.append("conditions missing: %s" % ", ".join(miss))
    runs = c.get("runs")
    for r in result.get("results", []):
        tag = "%s %s" % (r.get("question"), r.get("contender"))
        st = r.get("status")
        if st not in STATUSES:
            problems.append("%s: status %r is not one of %s" % (tag, st, ", ".join(STATUSES)))
            continue
        if st == "ok":
            t = r.get("timing")
            if not isinstance(t, dict):
                problems.append("%s: ok without a timing" % tag)
                continue
            all_s = t.get("all_s")
            if not isinstance(all_s, list) or not all_s or t.get("runs") != len(all_s) or t.get("runs") != runs:
                problems.append("%s: the timing is not the recorded %s runs" % (tag, runs))
            elif t.get("min_s") != min(all_s) or t.get("first_s") != all_s[0]:
                problems.append("%s: min_s or first_s is not the minimum or the first of all_s" % tag)
            if (r.get("answer") or {}).get("match") not in LEVELS:
                problems.append("%s: ok without the level at which its answer matched" % tag)
        else:
            for key in ("timing", "peak_rss_kb", "cpu_s"):
                if key in r:
                    problems.append("%s: status %s but it carries %s: a contender that did not pass is not timed" % (tag, st, key))
    if check_flag and not miss:
        ok, _ = evaluate_publishable(c)
        if result.get("publishable") is not ok:
            problems.append("publishable is %r but the conditions say %r" % (result.get("publishable"), ok))
    return problems


def render_table(result):
    """The published table as Markdown: the conditions, then a row per (question, contender) with its time or its status; no time for what did not pass."""
    c = result["conditions"]
    m = c["machine"]
    out = ["# %s" % result.get("label", "groupby"), "",
           "Conditions: %s, %s %s, %s, %d cores (%d usable), %.1f GiB; load at the start %s; %s; file %s, %d bytes, sha256 %s, seed %s; %s; output to %s; %s; date %s." % (
               m["node"], m["system"], m["architecture"], m["cpu_model"], m["cores"], m["affinity_cores"], m["ram_bytes"] / 2**30, c["load"]["start"], c["virtualised"]["evidence"],
               c["file"]["name"], c["file"]["bytes"], c["file"]["sha256"], c["seed"], c["statistic"], c["sink"], c["cache"], c["date"]),
           "Contenders: " + "; ".join("%s = %s (threads %s)" % (n, i["version"], i["threads"]) for n, i in c["contenders"].items()), "",
           "| question | contender | threads | min s | median s | first s | peak RSS KiB | CPU s | answer |", "|---|---|---|---|---|---|---|---|---|"]
    for r in result["results"]:
        if r["status"] == "ok":
            t = r["timing"]
            out.append("| %s | %s | %s | %.6f | %.6f | %.6f | %s | %s | %s |" % (r["question"], r["contender"], r["threads"], t["min_s"], t["median_s"], t["first_s"],
                                                                                 r.get("peak_rss_kb"), r.get("cpu_s"), r["answer"]["match"]))
        else:
            out.append("| %s | %s | %s | - | - | - | - | - | %s: %s |" % (r["question"], r["contender"], r["threads"], r["status"], str(r.get("reason") or "").replace("|", "/")))
    for n in result.get("not_run", []):
        out.append("| - | %s | - | - | - | - | - | - | NOT RUN: %s |" % (n["contender"], str(n["reason"]).replace("|", "/")))
    return "\n".join(out) + "\n"


def write_published_table(result, path):
    """Write the table of a result, or refuse (a rule tag, exit 3). The publishable flag in the file is not believed: it is recomputed from the recorded conditions."""
    c = result.get("conditions")
    miss = missing_conditions(c) if isinstance(c, dict) else ["conditions"]
    if miss:
        raise Refusal("bench.conditions-missing", "a number without its conditions is not published; missing: %s" % ", ".join(miss))
    ok, reasons = evaluate_publishable(c)
    if not ok:
        raise Refusal("bench.not-publishable", "this run is not publishable: %s" % "; ".join(reasons))
    problems = validate_result(result, check_flag=False)
    if problems:
        raise Refusal("bench.result-invalid", "the result breaks the rules of recording: %s" % "; ".join(problems[:3]))
    pathlib.Path(path).write_text(render_table(result))


# ---------------------------------------------------------------- the scan probe (design 4.4)

def scan_probe(table, file, rows, timeout=DEFAULT_TIMEOUT):
    """Is a plain scan of the file accepted without raising a flag? Two scans, no clock: a count (one row out) and a select of one column (every row out, the CSV rows bounded by
    --max-rows, default 10,000,000). The exit status, the rule tag if refused, and the rows written are recorded."""
    scans = []
    for name, extra in (("count", ["--agg", "count"]), ("select", ["--select", "id1"])):
        argv = [table, *extra, "--format", "csv", str(file)]
        head, nbytes, nl = b"", 0, 0
        t0 = time.monotonic()
        timed_out = False
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, deadline=timeout) as p:
            while True:
                chunk = p.stdout.read(1 << 20)
                if not chunk:
                    break
                if len(head) < 4096:
                    head += chunk[:4096 - len(head)]
                nbytes += len(chunk)
                nl += chunk.count(b"\n")
                if time.monotonic() - t0 > timeout:
                    p.kill()
                    timed_out = True
                    break
            rc = p.wait()
        s = {"name": name, "argv": [os.path.basename(argv[0]), *argv[1:-1], "FILE"], "exit": rc, "rule": rule_of(head) if rc not in (0, None) else None,
             "bytes_out": nbytes, "rows_out": max(nl - 1, 0) if rc == 0 else 0, "timed_out": timed_out}
        if name == "count" and rc == 0:
            s["count_cell"] = head.decode("utf-8", "replace").splitlines()[1:2]
        scans.append(s)
    return {"rows": rows, "scans": scans}


# ---------------------------------------------------------------- the step

def _free_bytes(path):
    override = os.environ.get("BENCH_NAMED_FREE_BYTES")   # a hook for the tests of the disk rule
    if override:
        return int(override)
    p = pathlib.Path(path).resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_file(rows, k, seed, out_dir):
    """The file of NB1 for these parameters in `out_dir`: reused when its sha256 is the one in its metadata, else generated. Returns (path, meta, generated seconds or 0)."""
    out_dir = pathlib.Path(out_dir)
    path = out_dir / gen.name_of(rows, k)
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta.get("seed") == seed and meta.get("rows") == rows and meta.get("k") == k and meta.get("bytes") == path.stat().st_size and sha256_of(path) == meta.get("sha256"):
            return path, meta, 0.0
    need = gen.estimate_bytes(rows, k)
    if not disk_ok(_free_bytes(out_dir), need):
        raise Refusal("bench.disk", "%d bytes free near %s, under three times the %d the file is estimated to take" % (_free_bytes(out_dir), out_dir, need))
    t0 = time.perf_counter()
    path, meta = gen.write(rows, k, out_dir, seed)
    return path, meta, time.perf_counter() - t0


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default="groupby", help="groupby or join (1brc is NB5, clickbench NB6)")
    ap.add_argument("--rows", default="1e6", help="N (default 1e6, the smoke step); above 1e6 needs --accept-large")
    ap.add_argument("--k", default="100")
    ap.add_argument("--seed", type=int, default=gen.DEFAULT_SEED)
    ap.add_argument("--runs", type=int, default=MIN_RUNS, help="N of the minimum of N (default 5; fewer is never publishable)")
    ap.add_argument("--questions", default="q1,q2,q3,q4,q5", help="the questions to run; q6 to q9 are always probed and listed as not expressible; q10 is opt-in (a group a row)")
    ap.add_argument("--contenders", default=",".join(FAMILIES))
    ap.add_argument("--external", action="append", default=[], metavar="NAME=PROFILE=PROGRAM", help="a program run as PROGRAM QID FILE that prints the answer as CSV; PROFILE exact | double")
    ap.add_argument("--threads", type=int, default=None, help="table's thread count besides 1 (default: the usable cores)")
    ap.add_argument("--taskset", default=None, help="cores for every contender, e.g. 0-6, or 'gram' (0-6 if no soak and no container, else 0-5, Q11)")
    ap.add_argument("--table", default=None, help="the table binary (default $TOOLBOX_BIN/table or build/table)")
    ap.add_argument("--out", default=None, help="the data directory (default $NAMED_DATA, else <tmp>/cancho-named-data); never inside the repository")
    ap.add_argument("--results", default=None, help="the JSON result (default: <out>/named-groupby-<machine>-<date>-<rows>.json)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--path-class", default=None, help="what the data directory is (e.g. 'internal SSD'); recorded as given")
    ap.add_argument("--accept-large", action="store_true", help="the maintainer's yes to a step above 1e6 rows, after reading --estimate")
    ap.add_argument("--estimate", action="store_true", help="print the arithmetic of the step (bytes, launches) and stop")
    ap.add_argument("--scan-probe-only", action="store_true", help="generate the file, report whether a plain scan is accepted (4.4), time nothing")
    ap.add_argument("--no-scan-probe", action="store_true")
    ap.add_argument("--skip", action="append", default=[], metavar="QID:CONTENDER[=REASON]", help="do not run this contender (a name such as csvtk@1, or a family) on this question; recorded as not-run with the reason")
    ap.add_argument("--refusal-bound", type=int, default=None, metavar="BYTES", help="join suite: also report what each question says under this --max-state-bytes (default: the tool's own bound)")
    ap.add_argument("--drop-from", default=None, metavar="RESULT.json", help="drop the (question, contender) pairs that timed out in this result (3.5)")
    ap.add_argument("--write-table", default=None, metavar="FILE", help="write the published table of the result, refusing a run that is not publishable")
    ap.add_argument("--from-result", default=None, metavar="RESULT.json", help="with --write-table: use an existing result instead of running")
    return ap.parse_args(argv)


def split_csv(text):
    return [x.strip() for x in text.split(",") if x.strip()]


def run_step(a, emit, probes=None):
    """One step. Returns the exit status."""
    if a.suite == "join":
        import bench_named_join       # the join suite's forms, judge and step; it uses this module for everything else
        return bench_named_join.run_join_step(a, emit, probes)
    if a.suite != "groupby":
        raise Refusal("bench.suite-not-built", "only the groupby and join suites are built (1brc is NB5, clickbench NB6, taxi NB7, json NB8), not %r" % a.suite)
    rows, k = parse_rows(a.rows), parse_rows(a.k)
    gen.spec(rows, k)                     # N a multiple of K, or a ValueError before anything is touched
    if a.runs < 1 or a.timeout <= 0:
        raise Refusal("bench.args", "--runs must be at least 1 and --timeout positive")
    probes = probes or Probes()
    snap = Snapshot(probes)               # before the driver adds any load of its own
    qs = QUESTION_BY_ID
    wanted_q = split_csv(a.questions)
    for qid in wanted_q:
        if qid not in qs or qs[qid]["class"] not in ("E", "B"):
            raise Refusal("bench.question", "%r is not a question that can be run (q1..q5, q10; q6 to q9 are listed as not expressible)" % qid)
    families = [("mlr" if f == "miller" else f) for f in split_csv(a.contenders)]
    for f in families:
        if f not in FAMILIES:
            raise Refusal("bench.contender", "unknown contender %r (one of %s)" % (f, ", ".join(FAMILIES)))
    tools = find_tools(table=a.table)
    threads_many = a.threads or snap.affinity()
    cores = a.taskset
    if cores == "gram":
        cores = gram_cores(snap.soak()["found"], snap.docker()["containers"])
    if cores and shutil.which("taskset") is None:
        raise Refusal("bench.taskset-missing", "--taskset needs the taskset command (Linux)")
    externals = []
    for spec in a.external:
        parts = spec.split("=", 2)
        if len(parts) != 3 or parts[1] not in ("exact", "double"):
            raise Refusal("bench.external", "--external NAME=PROFILE=PROGRAM, PROFILE exact or double: %r" % spec)
        externals.append((parts[0], parts[1], parts[2]))
    if not tools.get("table"):
        raise Refusal("bench.no-table", "no table binary: %s" % why_missing(tools, "table"))
    contenders = build_contenders(tools, threads_many, families, cores, externals)
    est = estimate(rows, k, len(wanted_q), len(contenders), a.runs)
    emit("estimate: %d rows, K %d: the file is about %d bytes (%d free are needed), %d process launches (%d questions x %d contenders x (1 warm-up + %d runs + 1 for the peak memory + 1 for the answer))" % (
        rows, k, est["bytes"], est["disk_needed"], est["launches"], len(wanted_q), len(contenders), a.runs))
    if a.estimate:
        return 0
    if needs_opt_in(rows) and not a.accept_large:
        raise Refusal("bench.large-size", "%d rows is above the smoke size of %d: the maintainer decides these steps; read the estimate above and pass --accept-large" % (rows, SMOKE_ROWS))
    out_dir = pathlib.Path(a.out) if a.out else gen.default_dir()
    if gen.inside_repo(out_dir):
        raise Refusal("bench.inside-repo", "%s is inside the repository: data is never committed, give --out outside it" % out_dir)
    t_start = time.perf_counter()
    path, meta, gen_s = ensure_file(rows, k, a.seed, out_dir)
    step = {"rows": rows, "estimated_bytes": est["bytes"], "file_bytes": meta["bytes"], "generate_s": round(gen_s, 3)}
    emit("file: %s, %d bytes (estimated %d), sha256 %s%s" % (path.name, meta["bytes"], est["bytes"], meta["sha256"], "" if gen_s else " (reused, sha256 checked)"))
    results = {"schema": SCHEMA, "suite": "groupby", "label": meta["label"], "results": [], "not_run": missing_contenders(tools, [f for f in families]), "dropped": []}
    info = {"rows": rows, "k": k, "seed": a.seed, "runs": a.runs, "step": a.rows, "path_class": a.path_class, "taskset": cores, "threads_many": threads_many,
            "file": {"name": path.name, "bytes": meta["bytes"], "sha256": meta["sha256"]},
            "contenders": {c.name: c.info for c in contenders}}
    cond = collect_conditions(snap, info)
    ok, reasons = evaluate_publishable(cond)
    results.update(conditions=cond, publishable=ok, publishable_reasons=reasons, step=step)
    emit("publishable: %s%s" % (str(ok).lower(), "" if ok else " (reasons: %s)" % "; ".join(reasons)))
    for n in results["not_run"]:
        emit("NOT RUN: %s (%s)" % (n["contender"], n["reason"]))
    skips = {}
    for spec in a.skip:
        target, _, why = spec.partition("=")
        qid, sep, who = target.partition(":")
        if not sep or not who or qid not in QUESTION_BY_ID:
            raise Refusal("bench.skip", "--skip QID:CONTENDER[=REASON], e.g. q10:csvtk=out of memory: %r" % spec)
        skips[(qid, who)] = why or "no reason given"
    ctx = Context(launch=real_launch, rusage=real_rusage, emit=emit, runs=a.runs, timeout=a.timeout, file=str(path), ref={}, exact={}, table=tools["table"], skip=skips)
    status = 0
    try:
        if a.scan_probe_only or not a.no_scan_probe:
            t0 = time.perf_counter()
            results["scan_probe"] = scan_probe(tools["table"], path, rows, a.timeout)
            step["scan_probe_s"] = round(time.perf_counter() - t0, 3)
            for s in results["scan_probe"]["scans"]:
                emit("scan  %s: exit %s%s, %d rows out" % (s["name"], s["exit"], " (%s)" % s["rule"] if s["rule"] else "", s["rows_out"]))
        if not a.scan_probe_only:
            status = measure_step(a, ctx, results, path, rows, k, wanted_q, contenders, step, emit)
    except Refusal as r:
        status = r.exit
        results["refusal"] = {"rule": r.rule, "message": r.message}
        emit(r.line())
    step["wall_s"] = round(time.perf_counter() - t_start, 3)
    cond["load"]["end"] = [float(x) for x in probes.loadavg()]
    emit("step %s: %d rows, file %d bytes (estimated %d), generated in %.1f s, wall time of the step %.1f s" % (
        a.rows, rows, meta["bytes"], est["bytes"], gen_s, step["wall_s"]))
    problems = validate_result(results)
    if problems:
        emit("the result breaks the rules of recording: %s" % "; ".join(problems))
        status = status or 4
    res_path = pathlib.Path(a.results) if a.results else out_dir / ("named-groupby-%s-%s-%s.json" % (cond["machine"]["node"], cond["date"][:10], a.rows))
    res_path.parent.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(results, indent=1) + "\n")
    emit("result: %s (publishable: %s)" % (res_path, str(results["publishable"]).lower()))
    if a.write_table:
        write_published_table(results, a.write_table)
        emit("published table: %s" % a.write_table)
    return status


def measure_step(a, ctx, results, path, rows, k, wanted_q, contenders, step, emit):
    t0 = time.perf_counter()
    problems = ref.validate_layout(path, rows, k)
    if problems:
        raise Refusal("bench.layout", "the file is not the documented layout: %s" % problems[0], 4)
    with_q10 = "q10" in wanted_q
    reference = ref.reference(path, with_q10)
    other = ref.reference_sqlite(path, with_q10)
    for qid in reference:
        if other[qid] != reference[qid]:
            raise Refusal("bench.reference-disagrees", "%s: the streaming reference and SQLite differ; the file is not used" % qid, 4)
    ctx.ref = reference
    if any(c.profile == "double" for c in contenders) and any(q in ("q3", "q4") for q in wanted_q):
        ctx.exact = exact_means(path)
        why = check_exact_against_reference(ctx.exact, reference)
        if why:
            raise Refusal("bench.reference-disagrees", why, 4)
    step["reference_s"] = round(time.perf_counter() - t0, 3)
    emit("reference: layout holds, SQLite agrees on %s" % ", ".join(sorted(reference)))
    if a.drop_from:
        prior = json.loads(pathlib.Path(a.drop_from).read_text())
        ctx.dropped |= {tuple(x) for x in prior.get("dropped", [])}
    status = 0
    t1 = time.perf_counter()
    floor = real_rusage(["true"], timeout=30)
    results["conditions"]["rss_floor_kb"] = floor["peak_rss_kb"] if floor else None
    for qid in wanted_q:
        q = QUESTION_BY_ID[qid]
        emit("question %s: %s" % (qid, q["name"]))
        results["results"] += run_question(ctx, q, contenders)
    for q in ref.load_questions()["questions"]:
        if q["class"] == "X":
            rec = probe_not_expressible(ctx, q)
            results["results"].append(rec)
    step["timing_s"] = round(time.perf_counter() - t1, 3)
    results["dropped"] = sorted([list(x) for x in ctx.dropped])
    if any(r["status"] in ("wrong-answer", "class-mismatch") for r in results["results"]):
        status = 4
        emit("GATE FAILED: a wrong answer or a stale classification is in the result")
    return status


def main(argv=None):
    a = parse_args(argv if argv is not None else sys.argv[1:])

    def emit(line):
        print(line, flush=True)

    try:
        if a.from_result:
            if not a.write_table:
                raise Refusal("bench.args", "--from-result is for --write-table")
            result = json.loads(pathlib.Path(a.from_result).read_text())
            write_published_table(result, a.write_table)
            emit("published table: %s" % a.write_table)
            return 0
        return run_step(a, emit)
    except Refusal as r:
        emit(r.line())
        return r.exit
    except ValueError as e:
        emit(Refusal("bench.args", str(e)).line())
        return 3


if __name__ == "__main__":
    sys.exit(main())
