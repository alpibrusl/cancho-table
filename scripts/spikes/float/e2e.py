#!/usr/bin/env python3
"""The end-to-end cell of docs/gap-float.md section 8: the 17-digit column `ratio` of the file of scripts/bench_numbers.py through `table` (origin/main's build and the build with
the new reader) and DuckDB, answers checked first, best of N runs, at several thread counts.

    e2e.py --old BASE/build/table --new WT/build/table --file data.csv [--runs 5] [--threads 1,2,4,8,16] [--duckdb duckdb]

Questions (all on `ratio` as a DOUBLE / `:float`):
  agg     group by status: min, max, sum, count            (the whole column is read)
  minmax  group by status: min, max
  sum     group by status: sum
  count   status = 404 and ratio >= 500: count             (the filter reads `ratio` only of the rows with status 404)
Prints seconds (best), and for each thread count the ratio to DuckDB."""
import argparse
import csv
import io
import math
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import bench_numbers as bn  # noqa: E402


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def truth(path):
    g = {}
    cnt = 0
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            v = float(r[4])
            g.setdefault(r[1], []).append(v)
            if r[1] == "404" and v >= 500:
                cnt += 1
    return {k: (min(v), max(v), math.fsum(v), len(v)) for k, v in g.items()}, cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--threads", default="1,2,4,8,16")
    ap.add_argument("--duckdb", default="duckdb")
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    if not data.exists():
        bn.generate(data, 1_000_000)
    ext, count = truth(data)
    root, name = ["--root", str(data.parent)], data.name
    qs = {"agg": "min:ratio:float,max:ratio:float,sum:ratio:float,count", "minmax": "min:ratio:float,max:ratio:float", "sum": "sum:ratio:float"}
    threads = [int(x) for x in a.threads.split(",")]

    def table(binary, q, t):
        th = [] if t == 1 else ["--threads", str(t), "--parallel-min-bytes", "0"]
        if q == "count":
            return [binary, *root, "--where", "status = 404 and ratio:float >= 500", "--agg", "count", "--format", "csv", *th, name]
        return [binary, *root, "--group", "status", "--agg", qs[q], "--format", "csv", *th, name]

    def duck(q, t):
        rd = f"read_csv('{data}', header=true, columns={{'id':'bigint','status':'int','bytes':'bigint','price':'double','ratio':'double','path':'varchar','cents':'bigint'}})"
        sel = {"agg": "min(ratio), max(ratio), sum(ratio), count(*)", "minmax": "min(ratio), max(ratio)", "sum": "sum(ratio)"}
        if q == "count":
            body = f"select count(*) from {rd} where status=404 and ratio>=500"
        else:
            body = f"select status, {sel[q]} from {rd} group by status order by 1"
        return [a.duckdb, "-csv", "-noheader", "-c", f"set threads={t}; copy ({body}) to '/dev/stdout' (format csv, header false)"]

    def ok(q, who, out):
        rows = [r for r in csv.reader(io.StringIO(out)) if r]
        if q == "count":
            return int(rows[-1][-1]) == count
        if who == "table":
            rows = rows[1:]
        got = {r[0]: [float(x) for x in r[1:]] for r in rows}
        for k, (lo, hi, s, n) in ext.items():
            want = {"agg": [lo, hi, s, n], "minmax": [lo, hi], "sum": [s]}[q]
            g = got.get(k)
            if g is None or len(g) != len(want):
                return False
            for x, y, nm in zip(g, want, range(9)):
                # DuckDB's sum is not exact (the point of the type): accept it within 1e-9 relative; the table's must be the fsum exactly
                if who == "table" and x != y:
                    return False
                if who != "table" and abs(x - y) > 1e-9 * abs(y):
                    return False
        return True

    cells = []
    for q in ("agg", "minmax", "sum", "count"):
        for t in threads:
            for who, argv in (("old", table(a.old, q, t)), ("new", table(a.new, q, t)), ("duck", duck(q, t) if shutil.which(a.duckdb) else None)):
                if argv:
                    cells.append((q, t, who, argv))
    for q, t, who, argv in cells:
        _, p = timed(argv)
        if p.returncode != 0 or not ok(q, "table" if who != "duck" else "duck", p.stdout.decode()):
            print("WRONG/FAILED", q, t, who, p.stderr[:200], p.stdout[:200])
            sys.exit(1)
    best = {}
    for i in range(a.runs):
        for q, t, who, argv in (cells if i % 2 == 0 else cells[::-1]):
            best[(q, t, who)] = min(best.get((q, t, who), 9e9), timed(argv)[0])
    print(f"{'question':8s} {'threads':>7s} {'old':>8s} {'new':>8s} {'duckdb':>8s}   new/duckdb  old/new")
    for q in ("agg", "minmax", "sum", "count"):
        for t in threads:
            o, n, d = best[(q, t, "old")], best[(q, t, "new")], best.get((q, t, "duck"))
            print(f"{q:8s} {t:7d} {o:8.3f} {n:8.3f} {d if d else float('nan'):8.3f}   {n / d if d else float('nan'):9.2f}  {o / n:7.2f}")


main()
