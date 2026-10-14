#!/usr/bin/env python3
"""JSON lines out (docs/readers.md section 13): `table --format jsonl` against its own `--format csv` (gate O6: at most 1.3x) and against DuckDB writing JSON lines
(`COPY (...) TO ... (FORMAT JSON)`), on 1,000,000 rows, narrow (scripts/bench.py's file, 5 columns) and wide (24 columns, the two read last).

    python3 scripts/bench_jsonl_out.py DIR [--reps N] [--rows N] [--bin build/table] [--wide] [--only table,duckdb]

DIR gets bench.csv (narrow) or wide.csv. The answers are checked first: every line of `table`'s output is parsed by Python's json and compared with the cells Python's csv
module reads (strings, as the design says), and DuckDB's lines (read with all_varchar, so that its values are strings too) are compared the same way; a disagreement stops the run.
Each contender is then run --reps times (default 7), in rotation, output to /dev/null, and the minimum wall time is printed with the load average before and after. DuckDB is given
one thread (`set threads=1`) and its default. Nothing here is a claim: it prints numbers and the conditions that were.
"""
import argparse
import csv
import json
import os
import random
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make(path, rows, wide):
    random.seed(1)
    with open(path, "w", newline="") as f:
        if wide:
            f.write(",".join(["c%02d" % k for k in range(22)] + ["status", "bytes"]) + "\n")
            for i in range(rows):
                f.write(",".join([str(k * i) for k in range(22)] + [str(random.choice([200, 200, 200, 301, 404, 500])), str(random.randint(0, 99999))]) + "\n")
        else:
            f.write("id,status,bytes,path,note\n")
            for i in range(rows):
                f.write('%d,%d,%d,/p/%d,"a,b %d"\n' % (i, random.choice([200, 200, 200, 301, 404, 500]), random.randint(0, 99999), random.randint(0, 999), i % 7))


def expected(path, rows, cols, status=None):
    out = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        head = next(r)
        ix = [head.index(c) for c in cols]
        si = head.index("status")
        for n, row in enumerate(r):
            if n >= rows:
                break
            if status is None or row[si] == status:
                out.append({head[i]: row[i] for i in ix})
    return out


def parsed(data):
    return [json.loads(l) for l in data.decode().split("\n") if l]


def timed(cmd):
    t = time.perf_counter()
    p = subprocess.run(["sh", "-c", cmd + " > /dev/null"], capture_output=True)
    return time.perf_counter() - t, p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--rows", type=int, default=1000000)
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "table"))
    ap.add_argument("--wide", action="store_true")
    ap.add_argument("--only", default="table,duckdb")
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    src = os.path.abspath(os.path.join(a.dir, "wide.csv" if a.wide else "bench.csv"))
    if not os.path.exists(src):
        make(src, a.rows, a.wide)
    allcols = ["c%02d" % k for k in range(22)] + ["status", "bytes"] if a.wide else ["id", "status", "bytes", "path", "note"]
    # question: (columns, table flags, SQL where)
    Q = {
        "cut2": (["status", "bytes"], "--select status,bytes", ""),
        "filter": (["status", "bytes"], "--where status=200 --select status,bytes", " where status = '200'"),
        "all": (allcols, "--rows", ""),
    }
    lim = " limit %d" % a.rows
    C = {}
    for q, (cols, flags, where) in Q.items():
        if "table" in a.only:
            C[("table jsonl", q)] = "%s %s --format jsonl %s" % (a.bin, flags, src)
            C[("table csv", q)] = "%s %s --format csv %s" % (a.bin, flags, src)
            C[("table jsonl --threads 4", q)] = "%s %s --format jsonl --threads 4 --parallel-min-bytes 0 %s" % (a.bin, flags, src)
        if "duckdb" in a.only and shutil.which("duckdb"):
            sel = ", ".join(cols)
            for th in (1, 0):
                lab = "duckdb %s, strings" % ("1 thread" if th == 1 else "default threads")
                setth = "set threads=%d; " % th if th else ""
                C[(lab, q)] = "duckdb -c \"%scopy (select %s from read_csv('%s', all_varchar=true)%s%s) to '/dev/stdout' (format json)\"" % (setth, sel, src, where, lim)
    # the answers
    for (lab, q), cmd in C.items():
        cols = Q[q][0]
        status = "200" if q == "filter" else None
        p = subprocess.run(["sh", "-c", cmd], capture_output=True)
        if p.returncode:
            print("%s / %s: exit %d: %s" % (lab, q, p.returncode, p.stderr[:200]))
            sys.exit(2)
        if lab.startswith("table csv"):
            continue
        got = parsed(p.stdout)
        if got != expected(src, a.rows, cols, status):
            print("%s / %s: ANSWER DISAGREES with Python (%d lines): stopping" % (lab, q, len(got)))
            sys.exit(2)
        if lab.startswith("table jsonl") and any(l and not l.startswith(b"{") for l in p.stdout.split(b"\n")):
            print("%s / %s: a line is not an object" % (lab, q))
            sys.exit(2)
    best = {k: 9e9 for k in C}
    load0 = os.getloadavg()
    for _ in range(a.reps):
        for k, cmd in C.items():
            el, rc = timed(cmd)
            best[k] = min(best[k], el)
    load1 = os.getloadavg()
    print("file %s, %d rows, %s; minimum of %d runs, seconds, output to /dev/null; load average %.2f before, %.2f after" % (src, a.rows, "wide" if a.wide else "narrow", a.reps, load0[0], load1[0]))
    for q in Q:
        print("\n%s" % q)
        for (lab, qq), v in best.items():
            if qq == q:
                print("  %-34s %8.4f" % (lab, v))
        if ("table jsonl", q) in best and ("table csv", q) in best:
            print("  jsonl / csv (gate O6, at most 1.3): %.3f" % (best[("table jsonl", q)] / best[("table csv", q)]))


if __name__ == "__main__":
    main()
