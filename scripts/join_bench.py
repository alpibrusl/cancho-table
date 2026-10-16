#!/usr/bin/env python3
"""J1 to J3 (docs/joins.md sections 12, 16, 17 and 18): the join of `table` against DuckDB and csvtk on the four cases of the baseline.

    python3 scripts/join_bench.py [--dir DIR] [--runs 5] [--bin build/table] [--json out.json] [--cases int-100k,text-100k,int-1m,text-1m] [--type inner|left|anti|group] [--miss 0.2]

`--type group` (J3) is a group-by over the inner join of the baseline files: `table --join build --on k=k --join-prefix b_ --group b_name --agg count,sum:v` against DuckDB's
`SELECT b.name, count(*), sum(a.v) FROM probe a JOIN build b ON a.k=b.k GROUP BY b.name` (one group for each build key that some probe row has: about 100,000 groups for the 100k builds
and about 632,000 for the 1M builds). The answer is compared as a set of rows for every contender, and in the order of the names' bytes for `table`, whose order is defined; csvtk has no
group-by over a join and is not run.

`--type left` and `--type anti` (J2) need probe keys that are not in the build: `--miss` is the share of the probe rows whose key is in no build row (default 0 for inner, which writes the
files J1 measured, byte for byte; 0.2 for left and anti). A left join then has 1,000,000 output rows, the unmatched ones with an empty `name`; an anti join has the unmatched probe rows
(`id,k,v`). DuckDB's SQL is `LEFT JOIN` and `NOT EXISTS`; csvtk has the left join (`join -f k --left-join`) and no anti join.

The files are the baseline's (section 12; `gen` below writes them from seed 7): the probe `id,k,v` of 1,000,000 rows with `k` uniform over the build's keys, the build `k,name` with unique
keys in random order, integer keys or text keys of about 14 bytes (`u%08x-%d`), 100,000 or 1,000,000 build rows. Every probe row matches exactly one build row, so the answer is 1,000,000
rows of `id,k,v,name`. **Before any timing every contender's whole output is checked against a Python join** (the rows sorted, their md5 and count; and for `table`, whose order is
defined, the rows in the probe's order), and the run stops on a difference. Timing: wall clock around the process, output to /dev/null (DuckDB: `COPY (...) TO '/dev/null'`), the
minimum of `--runs` interleaved rounds (the order rotating), the median beside it. Peak resident set once per tool (`/usr/bin/time -l`). DuckDB reads with type detection on (its default; it
does more work than `table` does) at one thread and at its default; csvtk is `csvtk join -f k probe build`. Nothing is tuned. A machine that is loaded by other work is stated, with the
load average before and after.
"""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import random
import re
import shutil
import statistics
import sys
import tempfile
import time
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = {"int-100k": ("int", 100_000), "text-100k": ("text", 100_000), "int-1m": ("int", 1_000_000), "text-1m": ("text", 1_000_000)}


def gen(d, kind, n, probes=1_000_000, miss=0.0):
    """The probe and the build file of a case (seed 7); also the keys of the build in file order. With `miss` a share of the probe rows have a key that is in no build row."""
    rnd = random.Random(7)
    if kind == "int":
        keys = [str(v) for v in rnd.sample(range(10 * n), n)]
    else:
        keys = ["u%08x-%d" % (rnd.getrandbits(32), i) for i in range(n)]
    names = ["n%d" % i for i in range(n)]
    tag = "" if not miss else "-miss%d" % round(miss * 100)
    build = d / ("build-%s-%d.csv" % (kind, n))
    probe = d / ("probe-%s-%d%s.csv" % (kind, n, tag))

    def key_of_row():
        if miss and rnd.random() < miss:
            return ("%d" % (10 * n + rnd.randrange(10 * n))) if kind == "int" else "x%08x-%d" % (rnd.getrandbits(32), rnd.randrange(n))
        return keys[rnd.randrange(n)]

    if not build.exists():
        with open(build, "w", newline="\n") as f:
            f.write("k,name\n")
            f.write("".join("%s,%s\n" % (k, nm) for k, nm in zip(keys, names)))
    if not probe.exists():
        with open(probe, "w", newline="\n") as f:
            f.write("id,k,v\n")
            f.write("".join("%d,%s,%d\n" % (i, key_of_row(), rnd.randrange(100000)) for i in range(probes)))
    return probe, build, keys, names


def truth(probe, build, kind="inner"):
    """The join by Python, in the probe's order: the rows as bytes, and their sorted md5. `kind`: inner, left (an unmatched row ends in an empty `name`) or anti (the unmatched probe rows)."""
    index = {}
    with open(build, "rb") as f:
        f.readline()
        for line in f:
            k, name = line.rstrip(b"\n").split(b",")
            index.setdefault(k, []).append(name)
    rows = []
    with open(probe, "rb") as f:
        f.readline()
        for line in f:
            i, k, v = line.rstrip(b"\n").split(b",")
            if kind == "anti":
                if k not in index:
                    rows.append(b",".join((i, k, v)))
                continue
            for name in index.get(k, ()):
                rows.append(b",".join((i, k, v, name)))
            if kind == "left" and k not in index:
                rows.append(b",".join((i, k, v, b"")))
    return rows


def truth_group(probe, build):
    """The groups of the inner join by build `name`: `name,count,sum(v)` as bytes, in the order of the names' bytes (what `table` writes)."""
    names = {}
    with open(build, "rb") as f:
        f.readline()
        for line in f:
            k, name = line.rstrip(b"\n").split(b",")
            names.setdefault(k, []).append(name)
    acc = {}
    with open(probe, "rb") as f:
        f.readline()
        for line in f:
            i, k, v = line.rstrip(b"\n").split(b",")
            for name in names.get(k, ()):
                c = acc.get(name)
                if c is None:
                    acc[name] = [1, int(v)]
                else:
                    c[0] += 1
                    c[1] += int(v)
    return [b"%s,%d,%d" % (name, c[0], c[1]) for name, c in sorted(acc.items())]


def digest(rows):
    return hashlib.md5(b"\n".join(sorted(rows))).hexdigest(), len(rows)


def contenders(a, d, probe, build):
    out = {}
    kind = a.type
    if kind == "group":
        out["table"] = [a.bin, "--root", str(d), "--join", build.name, "--on", "k=k", "--join-prefix", "b_", "--group", "b_name", "--agg", "count,sum:v", "--format", "csv", "--max-groups", "1000000", "--max-state-bytes", "1073741824", probe.name]
        if shutil.which("duckdb"):
            body = "SELECT b.name, count(*), sum(a.v) FROM read_csv('%s') a JOIN read_csv('%s') b ON a.k=b.k GROUP BY b.name" % (probe, build)
            sql = "COPY (%s) TO '%%s' (FORMAT CSV, HEADER)" % body
            out["duckdb-1"] = ["duckdb", "-c", "SET threads=1; " + sql]
            out["duckdb"] = ["duckdb", "-c", sql]
        return out
    if kind == "anti":
        out["table"] = [a.bin, "--root", str(d), "--join", build.name, "--on", "k=k", "--join-type", "anti", "--select", "id,k,v", "--format", "csv", "--max-state-bytes", "1073741824", probe.name]
    else:
        out["table"] = [a.bin, "--root", str(d), "--join", build.name, "--on", "k=k", "--join-type", kind, "--join-prefix", "b_", "--select", "id,k,v,b_name", "--format", "csv", "--max-state-bytes", "1073741824", probe.name]
    if shutil.which("duckdb"):
        if kind == "anti":
            body = "SELECT a.id,a.k,a.v FROM read_csv('%s') a WHERE NOT EXISTS (SELECT 1 FROM read_csv('%s') b WHERE b.k = a.k)" % (probe, build)
        else:
            body = "SELECT a.id,a.k,a.v,b.name FROM read_csv('%s') a %s JOIN read_csv('%s') b ON a.k=b.k" % (probe, "LEFT" if kind == "left" else "", build)
        sql = "COPY (%s) TO '%%s' (FORMAT CSV, HEADER)" % body
        out["duckdb-1"] = ["duckdb", "-c", "SET threads=1; " + sql]
        out["duckdb"] = ["duckdb", "-c", sql]
    if shutil.which("csvtk") and kind != "anti":
        out["csvtk"] = ["csvtk", "join", "-f", "k"] + (["--left-join"] if kind == "left" else []) + [str(probe), str(build)]
    return out


def run_to(argv, sink):
    """Run, standard output to `sink` (a path, or /dev/null); for duckdb the COPY target is the sink."""
    argv = [x % sink if x.startswith(("COPY", "SET threads")) and "%s" in x else x for x in argv]
    return argv


def wall(argv, sink):
    argv = run_to(argv, sink)
    t = time.perf_counter()
    if argv[0] == "duckdb":
        p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        with open(sink, "wb") as o:
            p = subprocess.run(argv, stdout=o, stderr=subprocess.PIPE)
    dt = time.perf_counter() - t
    if p.returncode:
        sys.exit("%s: exit %d: %s" % (argv[0], p.returncode, p.stderr[-300:]))
    return dt


def rss_of(argv, sink):
    argv = run_to(argv, sink)
    cmd = ["/usr/bin/time", "-l"] if sys.platform == "darwin" else ["/usr/bin/time", "-v"]
    with open(sink if argv[0] != "duckdb" else os.devnull, "wb") as o:
        p = subprocess.run(cmd + argv, stdout=o, stderr=subprocess.PIPE)
    err = p.stderr.decode()
    if sys.platform == "darwin":
        return int(re.search(r"(\d+)\s+maximum resident set size", err).group(1))
    return int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", err).group(1)) * 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--json", default="")
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--type", default="inner", choices=["inner", "left", "anti", "group"])
    ap.add_argument("--miss", type=float, default=None, help="the share of probe rows whose key is in no build row (default 0 for inner, 0.2 for left and anti)")
    a = ap.parse_args()
    if a.miss is None:
        a.miss = 0.0 if a.type in ("inner", "group") else 0.2
    d = pathlib.Path(a.dir) if a.dir else pathlib.Path(tempfile.mkdtemp(prefix="join-bench-"))
    d.mkdir(parents=True, exist_ok=True)
    load0 = os.getloadavg()
    print("machine %s %s, %s cores; load average before %s; %s; minimum of %d interleaved rounds (median in brackets)" % (platform.platform(), platform.machine(), os.cpu_count(), load0, a.bin, a.runs))
    for tool in ("duckdb", "csvtk"):
        if shutil.which(tool):
            print(tool, subprocess.run([tool, "--version" if tool == "duckdb" else "version"], capture_output=True, text=True).stdout.strip().splitlines()[0])
    result = {"machine": platform.platform() + " " + platform.machine(), "cores": os.cpu_count(), "load_before": load0, "runs": a.runs, "cases": {}}
    sink = str(d / "out.csv")
    for name in a.cases.split(","):
        kind, n = CASES[name]
        probe, build, keys, names = gen(d, kind, n, miss=a.miss)
        want = truth_group(probe, build) if a.type == "group" else truth(probe, build, a.type)
        wd = digest(want)
        cs = contenders(a, d, probe, build)
        # correctness first: every contender's whole output
        for tool, argv in cs.items():
            argv2 = run_to(argv, sink)
            with open(sink, "wb") as o:
                p = subprocess.run(argv2, stdout=o if argv2[0] != "duckdb" else subprocess.DEVNULL, stderr=subprocess.PIPE)
            if p.returncode:
                sys.exit("%s %s: exit %d %s" % (name, tool, p.returncode, p.stderr[-300:]))
            lines = pathlib.Path(sink).read_bytes().split(b"\n")
            if lines and lines[-1] == b"":
                lines.pop()
            rows = [l.replace(b'"', b"") for l in lines[1:]]
            if a.type == "left" and tool == "csvtk":
                rows = [l if l.count(b",") >= 3 else l + b"," for l in rows]
            if tool == "table" and a.type == "group":
                # the groups are listed in the order of the bytes of the name
                pass
            if digest(rows) != wd:
                sys.exit("%s %s: the output is not the Python join (%s against %s)" % (name, tool, digest(rows), wd))
            if tool == "table" and rows != want:
                sys.exit("%s table: the order is not the probe's" % name)
        times = {t: [] for t in cs}
        for r in range(a.runs):
            order = list(cs)
            k = r % len(order)
            order = order[k:] + order[:k]
            if r % 2:
                order.reverse()
            for t in order:
                times[t].append(wall(cs[t], sink if cs[t][0] != "duckdb" else os.devnull))
        rss = {t: rss_of(cs[t], sink if cs[t][0] != "duckdb" else os.devnull) for t in cs}
        print("\n%s (%s, miss %.2f): %d probe rows x %d build rows, %d output rows, probe %.1f MB, build %.1f MB" % (name, a.type, a.miss, 1_000_000, n, len(want), probe.stat().st_size / 1e6, build.stat().st_size / 1e6))
        for t in cs:
            print("  %-9s %7.3f s (median %.3f)   peak resident %5.0f MB" % (t, min(times[t]), statistics.median(times[t]), rss[t] / 1e6))
        result["cases"][name] = {"rows": len(want), "times": times, "rss": rss, "probe_bytes": probe.stat().st_size, "build_bytes": build.stat().st_size}
    result["load_after"] = os.getloadavg()
    print("\nload average after %s" % (result["load_after"],))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(result, indent=1) + "\n")
    if not a.dir:
        shutil.rmtree(d, ignore_errors=True)


main()
