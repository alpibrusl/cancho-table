#!/usr/bin/env python3
"""The tasks of the matrix, their inputs, their references, the judge of an answer, and the command each contender is asked (docs/matrix-benchmark.md sections 3 and 4).

Nothing here starts a process or reads a clock. `bench_matrix.py` (the driver) imports it.

* **Inputs** (`make_inputs`): NB1's file (scripts/bench_named_gen.py, the SplitMix64 generator, seed 108: the same bytes as the named benchmarks' groupby file of that size),
  its JSON-lines sibling (the same rows, numbers kept as their digits) and a small quoted file whose keys hold a comma, a double quote and a newline. Data is never committed
  and a directory inside the repository is refused (`matrix.inside-repo`).
* **References** (`reference`): computed from the written file in one streaming pass, with exact integers and `Fraction`; never from a contender.
* **The judge** (`compare`): the answer gate of NB2 in a smaller form. Rows are a set unless the task is ordered; an integer column never passes by tolerance; a decimal or a
  float may pass within 1e-9 relative and then the level says "tolerance"; the first difference is reported.
* **Forms** (`build_form`): the argv of a contender for a task, or None where no form was written (which says nothing about whether the tool can do it). Forms for tools that were
  not installed when they were written are *unvalidated* until the gate has judged them: a wrong form is reported as a wrong answer, never repaired quietly.
"""
import csv
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import re
import shlex
import sys
from decimal import Decimal, InvalidOperation
from fractions import Fraction

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import bench_named_gen as gen  # noqa: E402
import bench_named_ref as nref  # noqa: E402

TOLERANCE = Fraction(1, 10**9)
NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class Refusal(Exception):
    """The harness declines to go on: a rule tag (every refusal has one), a message and an exit status."""

    def __init__(self, rule, message, exit=3):
        super().__init__(message)
        self.rule, self.message, self.exit = rule, message, exit

    def line(self):
        return json.dumps({"ok": False, "rule": self.rule, "message": self.message, "exit": self.exit})


# ---------------------------------------------------------------- the tasks (docs/matrix-benchmark.md 3.1)
def _t(name, source, columns, ckinds, ordered=False, probe=False, task_row=""):
    return {"name": name, "input": source, "columns": columns, "ckinds": ckinds, "ordered": ordered, "probe": probe, "row": task_row}


TASKS = {
    "X01": _t("scan: count the rows where v1 >= 4", "csv", ["n"], ["int"], task_row="T01"),
    "X02": _t("group-by, low cardinality: sum v1 by id1", "csv", ["id1", "s"], ["text", "int"], task_row="T03"),
    "X03": _t("group-by, many keys: sum v1 by id3", "csv", ["id3", "s"], ["text", "int"], task_row="T04"),
    "X04": _t("exact decimal sum: sum v3 by id1 (six decimals)", "csv", ["id1", "s"], ["text", "dec6"], task_row="T05"),
    "X05": _t("sort and limit: the first ten id3, ascending", "csv", ["id3"], ["text"], ordered=True, task_row="T07/T08"),
    "X06": _t("distinct: the number of distinct id3", "csv", ["n"], ["int"], task_row="T14"),
    "X07": _t("JSON lines input: sum v1 by id1", "jsonl", ["id1", "s"], ["text", "int"], task_row="T12"),
    "X08": _t("standard input: sum v1 by id1 from a pipe", "stdin", ["id1", "s"], ["text", "int"], task_row="T13"),
    "X09": _t("quoting: sum v by k, keys with a comma, a quote and a newline", "quoted", ["k", "s"], ["text", "int"], task_row="T15"),
    "X10": _t("breadth probe: the median of v3", "csv", ["m"], ["float"], probe=True, task_row="T19"),
    "X11": _t("breadth probe: sum of v1 * v2 by id1 (a computed column)", "csv", ["id1", "s"], ["text", "int"], probe=True, task_row="T19"),
}
DETERMINISM_TASKS = ["X01", "X02", "X03", "X04", "X05", "X06"]
CALLGRIND_TASKS = ["X01", "X02", "X03", "X04", "X06"]
FAMILIES = ["table", "duckdb", "clickhouse", "polars", "pandas", "sqlite", "qsv", "mlr", "csvtk", "datafusion", "awk", "jq"]
THREADS = {"table": [1, 2, 4], "duckdb": [1, 2, 4], "clickhouse": [1, 2, 4], "polars": [1, 2, 4], "mlr": [1, 2, 4], "csvtk": [1, 2, 4],
           "pandas": [None], "sqlite": [None], "qsv": [None], "datafusion": [None], "awk": [None], "jq": [None]}


# ---------------------------------------------------------------- the inputs
def inside_repo(path):
    p = pathlib.Path(path).resolve()
    return p == ROOT or ROOT in p.parents


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_inputs(rows, k, out_dir, seed=gen.DEFAULT_SEED):
    """NB1's groupby file of `rows` rows with `k` groups, and the two files of this suite derived from it, in `out_dir`. Returns the paths, the sha256 of each and the parameters."""
    if inside_repo(out_dir):
        raise Refusal("matrix.inside-repo", "%s is inside the repository: data is never written there" % out_dir)
    if rows < 100:
        raise Refusal("matrix.args", "--rows must be at least 100")
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        path, _meta = gen.write(rows, k, out, seed)
    except ValueError as e:
        raise Refusal("matrix.args", str(e))
    path = pathlib.Path(path)
    jsonl = out / (path.stem + ".jsonl")
    quoted = out / (path.stem + ".quoted.csv")
    with open(path, newline="") as f, open(jsonl, "w", newline="\n") as j, open(quoted, "w", newline="") as q:
        r = csv.reader(f)
        head = next(r)
        w = csv.writer(q, lineterminator="\n")
        w.writerow(["k", "v"])
        text_cols = {"id1", "id2", "id3"}
        for i, row in enumerate(r):
            parts = ['"%s":%s' % (h, json.dumps(c) if h in text_cols else c) for h, c in zip(head, row)]
            j.write("{" + ",".join(parts) + "}\n")
            if i < 2000:
                w.writerow(['%s, "q%s"\nL%s' % (row[0], row[1][-1], row[2][-1]), row[6]])
    files = {"csv": str(path), "jsonl": str(jsonl), "quoted": str(quoted)}
    return {**files, "rows": rows, "k": k, "seed": seed, "sha256": {n: _sha(p) for n, p in files.items()}, "bytes": {n: os.path.getsize(p) for n, p in files.items()}}


# ---------------------------------------------------------------- the references
_CACHE = {}


def _scan(inputs):
    key = (inputs["csv"], inputs["sha256"]["csv"])
    if key in _CACHE:
        return _CACHE[key]
    s = {"ge4": 0, "v1_id1": {}, "v1_id3": {}, "v3_id1": {}, "id3": set(), "v3": [], "p_id1": {}, "first": []}
    with open(inputs["csv"], newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            id1, id3, v1, v2, v3 = row[0], row[2], int(row[6]), int(row[7]), nref.millionths(row[8])
            s["ge4"] += v1 >= 4
            s["v1_id1"][id1] = s["v1_id1"].get(id1, 0) + v1
            s["v1_id3"][id3] = s["v1_id3"].get(id3, 0) + v1
            s["v3_id1"][id1] = s["v3_id1"].get(id1, 0) + v3
            s["p_id1"][id1] = s["p_id1"].get(id1, 0) + v1 * v2
            s["id3"].add(id3)
            s["v3"].append(v3)
    _CACHE.clear()
    _CACHE[key] = s
    return s


def _dec_text(fr):
    """The exact decimal text of a Fraction whose denominator divides a power of ten."""
    d = Decimal(fr.numerator) / Decimal(fr.denominator)
    t = format(d.normalize(), "f")
    return t


def _ref(task, columns, rows, texts):
    t = TASKS[task]
    return {"task": task, "columns": list(t["columns"]), "ckinds": list(t["ckinds"]), "ordered": t["ordered"], "rows": rows, "texts": texts}


def reference(task, inputs):
    """The answer of `task` over the inputs, from a pass over the written file. {"columns", "ckinds", "ordered", "rows" (exact values), "texts" (the canonical spelling)}."""
    if task not in TASKS:
        raise Refusal("matrix.args", "unknown task %s" % task)
    if task in ("X09",):
        sums = {}
        with open(inputs["quoted"], newline="") as f:
            r = csv.reader(f)
            next(r)
            for k_, v in r:
                sums[k_] = sums.get(k_, 0) + int(v)
        rows = [[k_, v] for k_, v in sums.items()]
        return _ref(task, None, rows, [[k_, str(v)] for k_, v in rows])
    s = _scan(inputs)
    if task == "X01":
        rows = [[s["ge4"]]]
    elif task in ("X02", "X07", "X08"):
        rows = [[k_, v] for k_, v in s["v1_id1"].items()]
    elif task == "X03":
        rows = [[k_, v] for k_, v in s["v1_id3"].items()]
    elif task == "X04":
        rows = [[k_, Fraction(v, 10**6)] for k_, v in s["v3_id1"].items()]
        return _ref(task, None, rows, [[k_, nref.fmt6(v)] for k_, v in s["v3_id1"].items()])
    elif task == "X05":
        ids = []
        with open(inputs["csv"], newline="") as f:
            r = csv.reader(f)
            next(r)
            ids = sorted(row[2] for row in r)[:10]
        rows = [[i] for i in ids]
    elif task == "X06":
        rows = [[len(s["id3"])]]
    elif task == "X10":
        v = sorted(s["v3"])
        n = len(v)
        mid = Fraction(v[n // 2], 10**6) if n % 2 else Fraction(v[n // 2 - 1] + v[n // 2], 2 * 10**6)
        return _ref(task, None, [[mid]], [[_dec_text(mid)]])
    elif task == "X11":
        rows = [[k_, v] for k_, v in s["p_id1"].items()]
    return _ref(task, None, rows, [[str(c) for c in row] for row in rows])


# ---------------------------------------------------------------- the judge
def _number(text):
    """The exact value of a number's text, or None: no nan, no infinity, no thousands separator, no spaces."""
    if not NUMBER.match(text):
        return None
    try:
        return Fraction(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _cell(kind, text, want_text, want):
    """(level, detail) for one cell: level is exact, numeric, tolerance or None (wrong)."""
    if kind == "text":
        return ("exact", "") if text == want else (None, "key %r, wanted %r" % (text, want))
    got = _number(text)
    if got is None:
        return None, "%r is not a number" % text
    if got == want:
        return ("exact", "") if text == want_text else ("numeric", "")
    if kind in ("dec6", "float") and want != 0 and abs(got - want) <= TOLERANCE * abs(want):
        return "tolerance", "%s within 1e-9 relative of the exact %s" % (text, want_text)
    return None, "%s, wanted %s" % (text, want_text)


_ORDER = {"exact": 0, "numeric": 1, "tolerance": 2}


def compare(ref, raw):
    """Judge an answer (text) against a reference: {"status": "ok"|"wrong", "level": ..., "detail": ...}."""
    def wrong(why):
        return {"status": "wrong", "level": None, "detail": why}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not raw.strip():
        return wrong("empty output")
    if "\x00" in raw or raw.startswith("\ufeff"):
        return wrong("a NUL or a byte order mark in the output")
    try:
        table = list(csv.reader(io.StringIO(raw, newline="")))
    except csv.Error as e:
        return wrong("not csv: %s" % e)
    head, body = table[0], table[1:]
    width = len(ref["ckinds"])
    if len(head) != width:
        return wrong("%d columns, wanted %d" % (len(head), width))
    if any(len(r) != width for r in body):
        return wrong("a row with another width than the header's")
    if len(body) != len(ref["rows"]):
        return wrong("%d rows, wanted %d" % (len(body), len(ref["rows"])))
    kinds = ref["ckinds"]
    wants = list(zip(ref["texts"], ref["rows"]))
    if not ref["ordered"]:
        key_idx = [i for i, k_ in enumerate(kinds) if k_ == "text"]
        if key_idx:
            index = {tuple(t[i] for i in key_idx): (t, v) for t, v in wants}
            got_keys = [tuple(r[i] for i in key_idx) for r in body]
            if len(set(got_keys)) != len(got_keys):
                return wrong("a key twice")
            pairs = []
            for r, gk in zip(body, got_keys):
                if gk not in index:
                    return wrong("key %r is not in the reference" % (gk,))
                pairs.append((r, index[gk]))
        else:
            pairs = [(r, w) for r, w in zip(body, wants)]
    else:
        pairs = list(zip(body, wants))
    level = "exact"
    for r, (t, v) in pairs:
        for i, kind in enumerate(kinds):
            lv, why = _cell(kind, r[i], t[i], v[i] if kind == "text" else Fraction(v[i]))
            if lv is None:
                return wrong(why)
            if _ORDER[lv] > _ORDER[level]:
                level = lv
    return {"status": "ok", "level": level, "detail": ""}


# ---------------------------------------------------------------- the forms
@dataclasses.dataclass
class Ns:
    """What a form needs: the three files, the thread count (None: the tool's own), whether the answer is for a machine (table: its default JSON, not csv), and the two faults."""
    file: str
    jsonl: str = ""
    quoted: str = ""
    threads: object = None
    machine: bool = False
    missing: object = None
    unknown_column: object = None


@dataclasses.dataclass
class FormSpec:
    argv: list
    stdin_path: object = None
    env: dict = dataclasses.field(default_factory=dict)
    shell: bool = False        # the argv is already complete (sh -c ...): the driver does not put the tool's command in front


def _col(ns, name):
    return ns.unknown_column if ns.unknown_column and name == "v1" else name


def _path(ns, task):
    src = TASKS[task]["input"]
    if ns.missing:
        return ns.missing
    return {"csv": ns.file, "jsonl": ns.jsonl, "quoted": ns.quoted, "stdin": None}[src]


# the SQL of the tasks, with {T} the table and {v1} the (possibly unknown) column; each engine adds its own reader and casts
_SQL = {
    "X01": "SELECT count(*) AS n FROM {T} WHERE {v1} >= 4",
    "X02": "SELECT id1, sum({v1}) AS s FROM {T} GROUP BY id1",
    "X03": "SELECT id3, sum({v1}) AS s FROM {T} GROUP BY id3",
    "X04": "SELECT id1, sum(v3) AS s FROM {T} GROUP BY id1",
    "X05": "SELECT id3 FROM {T} ORDER BY id3 LIMIT 10",
    "X06": "SELECT count(DISTINCT id3) AS n FROM {T}",
    "X07": "SELECT id1, sum({v1}) AS s FROM {T} GROUP BY id1",
    "X08": "SELECT id1, sum({v1}) AS s FROM {T} GROUP BY id1",
    "X09": "SELECT k, sum(v) AS s FROM {T} GROUP BY k",
    "X10": "SELECT {MED} AS m FROM {T}",
    "X11": "SELECT id1, sum({v1} * v2) AS s FROM {T} GROUP BY id1",
}


def _sql(task, ns, table, median="median(v3)", cast=None):
    s = _SQL[task]
    v1 = _col(ns, "v1")
    if cast:   # sqlite: every column is text
        s = s.replace("sum({v1} * v2)", "sum(CAST({v1} AS INTEGER) * CAST(v2 AS INTEGER))").replace("sum({v1})", "sum(CAST({v1} AS INTEGER))")
        s = s.replace("{v1} >= 4", "CAST({v1} AS INTEGER) >= 4").replace("sum(v3)", "sum(CAST(v3 AS REAL))").replace("sum(v)", "sum(CAST(v AS INTEGER))")
    return s.format(T=table, v1=v1, MED=median)


def _threads_env(ns, name):
    return {name: str(ns.threads)} if ns.threads else {}


def _table(task, ns):
    v1 = _col(ns, "v1")
    flags = {
        "X01": ["--where", "%s:int >= 4" % v1, "--agg", "count"],
        "X02": ["--group", "id1", "--agg", "sum:%s" % v1],
        "X03": ["--group", "id3", "--agg", "sum:%s" % v1, "--max-groups", "1000000"],
        "X04": ["--group", "id1", "--agg", "sum:v3:dec(6)"],
        "X05": ["--select", "id3", "--order-by", "id3", "--limit", "10"],
        "X06": ["--agg", "distinct:id3"],
        "X07": ["--input-format", "jsonl", "--fields", "id1,%s" % v1, "--group", "id1", "--agg", "sum:%s" % v1],
        "X08": ["--group", "id1", "--agg", "sum:%s" % v1],
        "X09": ["--group", "k", "--agg", "sum:v"],
        "X10": ["--agg", "median:v3"],
        "X11": ["--group", "id1", "--agg", "sum:%s*v2" % v1],
    }[task]
    argv = ["table", *flags]
    if ns.threads:
        argv += ["--threads", str(ns.threads)]
    if not ns.machine:
        argv += ["--format", "csv"]
    p = _path(ns, task)
    stdin = ns.file if p is None else None
    if p is not None:
        argv.append(p)
    return FormSpec(argv, stdin)


def _duckdb(task, ns):
    p = _path(ns, task)
    reader = "read_json_auto('%s')" % p if TASKS[task]["input"] == "jsonl" else "read_csv('%s')" % (p or "/dev/stdin")
    pre = "SET threads=%d; " % ns.threads if ns.threads else ""
    return FormSpec(["duckdb", "-csv", "-c", pre + _sql(task, ns, reader)], ns.file if p is None else None)


def _clickhouse(task, ns):
    p = _path(ns, task)
    src = TASKS[task]["input"]
    if p is None:
        table, extra = "table", ["--input-format", "CSVWithNames"]
    elif src == "jsonl":
        table, extra = "file('%s', 'JSONEachRow')" % p, []
    else:
        table, extra = "file('%s', 'CSVWithNames')" % p, []
    q = _sql(task, ns, table, median="medianExact(v3)") + " FORMAT CSVWithNames"
    th = ["--max_threads", str(ns.threads)] if ns.threads else []
    return FormSpec(["clickhouse-local", *th, *extra, "-q", q], ns.file if p is None else None)


def _sqlite(task, ns):
    if task == "X10" or TASKS[task]["input"] == "jsonl":
        return None
    p = _path(ns, task) or "/dev/stdin"
    return FormSpec(["sqlite3", "-csv", "-header", ":memory:", '.import --csv "%s" x' % p, _sql(task, ns, "x", cast=True)], ns.file if _path(ns, task) is None else None)


def _datafusion(task, ns):
    if TASKS[task]["input"] == "stdin":
        return None
    p = _path(ns, task)
    if TASKS[task]["input"] == "jsonl":
        return FormSpec(["datafusion-cli", "--format", "csv", "-q", "-c", "CREATE EXTERNAL TABLE x STORED AS JSON LOCATION '%s'" % p, "-c", _sql(task, ns, "x") + ";"])
    return FormSpec(["datafusion-cli", "--format", "csv", "-q", "-c", _sql(task, ns, "'%s'" % p) + ";"])


def _qsv(task, ns):
    p = _path(ns, task)
    return FormSpec(["qsv", "sqlp", p or "-", _sql(task, ns, "_t_1")], ns.file if p is None else None)


_PD_BODY = {
    "X01": "r = pd.DataFrame({{'n': [int((d['{v1}'] >= 4).sum())]}})",
    "X02": "r = d.groupby('id1', as_index=False)['{v1}'].sum()",
    "X03": "r = d.groupby('id3', as_index=False)['{v1}'].sum()",
    "X04": "r = d.groupby('id1', as_index=False)['v3'].sum()",
    "X05": "r = d[['id3']].sort_values('id3').head(10)",
    "X06": "r = pd.DataFrame({{'n': [d['id3'].nunique()]}})",
    "X07": "r = d.groupby('id1', as_index=False)['{v1}'].sum()",
    "X08": "r = d.groupby('id1', as_index=False)['{v1}'].sum()",
    "X09": "r = d.groupby('k', as_index=False)['v'].sum()",
    "X10": "r = pd.DataFrame({{'m': [d['v3'].median()]}})",
    "X11": "r = d.assign(p=d['{v1}'] * d['v2']).groupby('id1', as_index=False)['p'].sum()",
}
_PL_BODY = {
    "X01": "r = pl.DataFrame({{'n': [d.filter(pl.col('{v1}') >= 4).height]}})",
    "X02": "r = d.group_by('id1').agg(pl.col('{v1}').sum())",
    "X03": "r = d.group_by('id3').agg(pl.col('{v1}').sum())",
    "X04": "r = d.group_by('id1').agg(pl.col('v3').sum())",
    "X05": "r = d.select('id3').sort('id3').head(10)",
    "X06": "r = pl.DataFrame({{'n': [d['id3'].n_unique()]}})",
    "X07": "r = d.group_by('id1').agg(pl.col('{v1}').sum())",
    "X08": "r = d.group_by('id1').agg(pl.col('{v1}').sum())",
    "X09": "r = d.group_by('k').agg(pl.col('v').sum())",
    "X10": "r = d.select(pl.col('v3').median())",
    "X11": "r = d.with_columns((pl.col('{v1}') * pl.col('v2')).alias('p')).group_by('id1').agg(pl.col('p').sum())",
}


def _pandas(task, ns):
    src = TASKS[task]["input"]
    p = _path(ns, task)
    read = {"csv": "pd.read_csv(sys.argv[1])", "quoted": "pd.read_csv(sys.argv[1])", "jsonl": "pd.read_json(sys.argv[1], lines=True)", "stdin": "pd.read_csv(sys.stdin)"}[src]
    if ns.missing and src == "stdin":
        read = "pd.read_csv(sys.argv[1])"
    script = "import sys\nimport pandas as pd\nd = %s\n%s\nsys.stdout.write(r.to_csv(index=False))\n" % (read, _PD_BODY[task].format(v1=_col(ns, "v1")))
    return FormSpec(["python3", "-I", "-c", script, *([p] if p else [])], ns.file if (p is None) else None)


def _polars(task, ns):
    src = TASKS[task]["input"]
    p = _path(ns, task)
    read = {"csv": "pl.read_csv(sys.argv[1])", "quoted": "pl.read_csv(sys.argv[1])", "jsonl": "pl.read_ndjson(sys.argv[1])", "stdin": "pl.read_csv(sys.stdin.buffer.read())"}[src]
    if ns.missing and src == "stdin":
        read = "pl.read_csv(sys.argv[1])"
    script = "import sys\nimport polars as pl\nd = %s\n%s\nsys.stdout.write(r.write_csv())\n" % (read, _PL_BODY[task].format(v1=_col(ns, "v1")))
    return FormSpec(["python3", "-I", "-c", script, *([p] if p else [])], ns.file if p is None else None, _threads_env(ns, "POLARS_MAX_THREADS"))


def _mlr(task, ns):
    p = _path(ns, task)
    v1 = _col(ns, "v1")
    csv_in = ["--icsv", "--ocsv"]
    body = {
        "X01": [*csv_in, "filter", "$%s >= 4" % v1, "then", "count"],
        "X02": [*csv_in, "stats1", "-a", "sum", "-f", v1, "-g", "id1"],
        "X03": [*csv_in, "stats1", "-a", "sum", "-f", v1, "-g", "id3"],
        "X04": [*csv_in, "stats1", "-a", "sum", "-f", "v3", "-g", "id1"],
        "X05": [*csv_in, "sort", "-f", "id3", "then", "head", "-n", "10", "then", "cut", "-f", "id3"],
        "X06": [*csv_in, "count-distinct", "-f", "id3", "then", "count"],
        "X07": ["--ijsonl", "--ocsv", "stats1", "-a", "sum", "-f", v1, "-g", "id1"],
        "X08": [*csv_in, "stats1", "-a", "sum", "-f", v1, "-g", "id1"],
        "X09": [*csv_in, "stats1", "-a", "sum", "-f", "v", "-g", "k"],
        "X10": [*csv_in, "stats1", "-i", "-a", "p50", "-f", "v3"],
        "X11": [*csv_in, "put", "$p = $%s * $v2" % v1, "then", "stats1", "-a", "sum", "-f", "p", "-g", "id1"],
    }[task]
    return FormSpec(["mlr", *body, *([p] if p else [])], ns.file if p is None else None, _threads_env(ns, "GOMAXPROCS"))


def _csvtk(task, ns):
    p = _path(ns, task)
    f = shlex.quote(p) if p else ""
    j = "-j %d " % ns.threads if ns.threads else ""
    v1 = _col(ns, "v1")
    cmd = {
        "X01": "csvtk {j}filter2 -f '${v1}>=4' {f} | csvtk summary -f {v1}:count",
        "X02": "csvtk {j}summary -g id1 -f {v1}:sum {f}",
        "X03": "csvtk {j}summary -g id3 -f {v1}:sum {f}",
        "X04": "csvtk {j}summary -w 12 -g id1 -f v3:sum {f}",
        "X05": "csvtk {j}sort -k id3 {f} | csvtk cut -f id3 | head -n 11",
        "X06": "csvtk {j}cut -f id3 {f} | csvtk uniq -f id3 | csvtk summary -f id3:count",
        "X07": None,
        "X08": "csvtk {j}summary -g id1 -f {v1}:sum {f}",
        "X09": "csvtk {j}summary -g k -f v:sum {f}",
        "X10": "csvtk {j}summary -w 12 -f v3:median {f}",
        "X11": "csvtk {j}mutate2 -n p -e '${v1} * $v2' {f} | csvtk summary -g id1 -f p:sum",
    }[task]
    if cmd is None:
        return None
    return FormSpec(["sh", "-c", cmd.format(j=j, f=f, v1=v1)], ns.file if p is None else None, shell=True)


def _awk(task, ns):
    p = _path(ns, task)
    f = shlex.quote(p) if p else ""
    sep = "-F,"
    prog = {
        "X01": "NR>1 && $7>=4 {n++} END{print \"n\"; print n+0}",
        "X02": "NR>1{s[$1]+=$7} END{print \"id1,s\"; for(k in s) printf \"%s,%d\\n\", k, s[k]}",
        "X03": "NR>1{s[$3]+=$7} END{print \"id3,s\"; for(k in s) printf \"%s,%d\\n\", k, s[k]}",
        "X04": "NR>1{s[$1]+=$9} END{print \"id1,s\"; for(k in s) printf \"%s,%.6f\\n\", k, s[k]}",
        "X06": "NR>1 && !($3 in s){s[$3]=1; n++} END{print \"n\"; print n}",
        "X08": "NR>1{s[$1]+=$7} END{print \"id1,s\"; for(k in s) printf \"%s,%d\\n\", k, s[k]}",
        "X09": "NR>1{s[$1]+=$NF} END{print \"k,s\"; for(k in s) printf \"%s,%d\\n\", k, s[k]}",
        "X11": "NR>1{s[$1]+=$7*$8} END{print \"id1,s\"; for(k in s) printf \"%s,%d\\n\", k, s[k]}",
    }
    if task == "X05":
        return FormSpec(["sh", "-c", "echo id3; awk -F, 'NR>1{print $3}' %s | LC_ALL=C sort | head -n 10" % f], ns.file if p is None else None, shell=True)
    if task not in prog:
        return None
    return FormSpec(["awk", sep, prog[task], *([p] if p else [])], ns.file if p is None else None)


def _jq(task, ns):
    if task != "X07":
        return None
    v1 = _col(ns, "v1")
    prog = '(["id1","s"]|@csv), (group_by(.id1)[] | [.[0].id1, (map(.%s)|add)] | @csv)' % v1
    return FormSpec(["jq", "-s", "-r", prog, _path(ns, task)])


_BUILD = {"table": _table, "duckdb": _duckdb, "clickhouse": _clickhouse, "polars": _polars, "pandas": _pandas, "sqlite": _sqlite, "qsv": _qsv, "mlr": _mlr, "csvtk": _csvtk,
          "datafusion": _datafusion, "awk": _awk, "jq": _jq}


def build_form(family, task, ns):
    """The form of `family` for `task`, or None where none was written. argv[0] is the tool's logical name; the driver puts the detected command there."""
    if family not in _BUILD:
        raise Refusal("matrix.args", "unknown family %s" % family)
    if task not in TASKS:
        raise Refusal("matrix.args", "unknown task %s" % task)
    return _BUILD[family](task, ns)
