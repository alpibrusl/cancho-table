#!/usr/bin/env python3
"""Gate G8/G9/G10 of docs/numbers.md for `:float` (stage N3a): the filter, the rows and min/max of a float column.

    python3 scripts/bench_numbers_float.py [--bin build/table] [--runs 7] [--file build/num/data.csv] [--threads 16]

The file of scripts/bench_numbers.py (id,status,bytes,price,ratio,path,cents). Two float columns, because the two ways of reading a double cost differently:
`price` (two decimals: Clinger's fast path, decided inline) and `ratio` (a double in its shortest form, 15 to 17 digits: the exact reader of std.json, about
a microsecond a cell). Questions, by `status=404` unless said:

  count   rows with value >= 500                              rows   the same as id,value csv           minmax   min and max by status

for `table` (`:float`), DuckDB (DOUBLE), csvtk and Miller where installed, plus the integer cell of the same file as the reference for G8
(`cents:int`: `price:float` must be within 1.25x of it). **Every answer is checked against Python first**: the count; the ids and the values of the rows (as
floats: a double prints `500.5` here and `500.50` in the file, the same number); the minimum and maximum equal as doubles.
"""
import argparse
import csv
import io
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench_numbers as bn  # noqa: E402

ROOT = bn.ROOT
G8 = 1.25


def truth(data):
    rows = {"price": [], "ratio": []}
    ext = {"price": {}, "ratio": {}}
    with open(data, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            for col, i in (("price", 3), ("ratio", 4)):
                v = float(r[i])
                if r[1] == "404" and v >= 500:
                    rows[col].append((r[0], v))
                e = ext[col].setdefault(r[1], [v, v])
                e[0] = min(e[0], v)
                e[1] = max(e[1], v)
    return rows, ext


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--file", default=str(ROOT / "build" / "num" / "data.csv"))
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--rows", type=int, default=1_000_000)
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    if not data.exists():
        bn.generate(data, a.rows)
    rows, ext = truth(data)
    d, root, name = str(data), ["--root", str(data.parent)], data.name
    cells = []

    def tbl(col, typ, q, threads):
        t = [] if threads == 1 else ["--threads", str(threads), "--parallel-min-bytes", "0"]
        if q == "count":
            return [a.bin, *root, "--where", f"status = 404 and {col}:{typ} >= 500", "--agg", "count", "--format", "csv", *t, name]
        if q == "rows":
            return [a.bin, *root, "--where", f"status = 404 and {col}:{typ} >= 500", "--select", f"id,{col}", "--format", "csv", *t, name]
        return [a.bin, *root, "--group", "status", "--agg", f"min:{col}:{typ},max:{col}:{typ}", "--format", "csv", *t, name]

    def duck(col, q, threads):
        rd = f"read_csv('{d}', header=true, columns={{'id':'bigint','status':'int','bytes':'bigint','price':'double','ratio':'double','path':'varchar','cents':'bigint'}})"
        if q == "count":
            body = f"select count(*) from {rd} where status=404 and {col}>=500"
        elif q == "rows":
            body = f"select id, {col} from {rd} where status=404 and {col}>=500"
        else:
            body = f"select status, min({col}), max({col}) from {rd} group by status order by 1"
        return ["duckdb", "-csv", "-noheader", "-c", f"set threads={threads}; copy ({body}) to '/dev/stdout' (format csv, header false)"]

    def check(q, col, out, header):
        r = list(csv.reader(io.StringIO(out)))
        if header:
            r = r[1:]
        r = [x for x in r if x]
        if q == "count":
            return int(r[-1][-1]) == len(rows[col])
        if q == "rows":
            return sorted((x[0], float(x[1])) for x in r) == sorted(rows[col], key=lambda t: t[0]) or sorted((x[0], float(x[1])) for x in r) == sorted(rows[col])
        return {x[0]: (float(x[1]), float(x[2])) for x in r} == {k: tuple(v) for k, v in ext[col].items()}

    for col in ("price", "ratio"):
        for q in ("count", "rows", "minmax"):
            for threads in (1, a.threads):
                cells.append((f"table {col}:float {q:6} t={threads}", (col, "table", q, threads), tbl(col, "float", q, threads), q, col, True))
                if shutil.which("duckdb"):
                    cells.append((f"duckdb DOUBLE {col} {q:6} t={threads}", (col, "duck", q, threads), duck(col, q, threads), q, col, False))
    for q in ("count", "rows"):
        for threads in (1, a.threads):
            c = tbl("cents", "int", q, threads)
            cells.append((f"table cents:int   {q:6} t={threads}", ("cents", "int", q, threads), c, q, "cents", True))
    cents_truth = None
    good = []
    for label, grp, argv, q, col, hdr in cells:
        t, p = timed(argv)
        if p.returncode != 0:
            print("FAILED", label, p.stderr[:200])
            continue
        if col == "cents":
            good.append((label, grp, argv))
            continue
        if not check(q, col, p.stdout.decode(), hdr and q != "count"):
            print("WRONG ANSWER", label, p.stdout[:120])
            continue
        good.append((label, grp, argv))
    # csvtk and Miller on the filter (their expression engines) of each column
    for col in ("price", "ratio"):
        if shutil.which("csvtk"):
            cells2 = (f"csvtk -j 1 {col} rows", ("csvtk", col), ["csvtk", "-j", "1", "filter2", "-f", f"$status==404 && ${col}>=500", d])
            t, p = timed(cells2[2])
            if p.returncode == 0 and sorted((x[0], float(x[3 if col == "price" else 4])) for x in csv.reader(io.StringIO(p.stdout.decode())) if x and x[0].isdigit()) == sorted(rows[col]):
                good.append(cells2)
            else:
                print("WRONG ANSWER/FAILED", cells2[0])
        if shutil.which("mlr"):
            argv = ["mlr", "--icsv", "--ocsv", "filter", f"$status==404 && ${col}>=500", d]
            t, p = timed(argv)
            if p.returncode == 0 and sorted((x[0], float(x[3 if col == "price" else 4])) for x in csv.reader(io.StringIO(p.stdout.decode())) if x and x[0].isdigit()) == sorted(rows[col]):
                good.append((f"mlr {col} rows", ("mlr", col), argv))
            else:
                print("WRONG ANSWER/FAILED mlr", col)
    times = {l: [] for l, _, _ in good}
    for i in range(a.runs):
        for label, grp, argv in (good if i % 2 == 0 else list(reversed(good))):
            times[label].append(timed(argv)[0])
    best = {l: min(v) for l, v in times.items()}
    print("%-34s %9s" % ("cell (minimum of %d)" % a.runs, "seconds"))
    for label, _, _ in good:
        print("%-34s %9.4f" % (label, best[label]))
    worst = 0.0
    for q in ("count", "rows"):
        for threads in (1, a.threads):
            f, i = best.get(f"table price:float {q:6} t={threads}"), best.get(f"table cents:int   {q:6} t={threads}")
            if f and i:
                worst = max(worst, f / i)
                print("G8 %-6s threads %-3d price:float / cents:int = %.3f%s" % (q, threads, f / i, "  <-- over %.2f" % G8 if f / i > G8 else ""))
    for col in ("price", "ratio"):
        for q in ("count", "rows", "minmax"):
            for threads in (1, a.threads):
                t = best.get(f"table {col}:float {q:6} t={threads}")
                du = best.get(f"duckdb DOUBLE {col} {q:6} t={threads}")
                if t and du:
                    print("G9/G10 %-6s %-6s t=%-3d duckdb / table = %.2f" % (col, q, threads, du / t))
        for who in ("csvtk -j 1", "mlr"):
            o = best.get(f"{who} {col} rows")
            t = best.get(f"table {col}:float rows   t=1")
            if o and t:
                print("G10 %-6s rows   %s / table(1 thread) = %.2f" % (col, who, o / t))
    print("G8: worst price:float / cents:int %.3f (limit %.2f): %s" % (worst, G8, "PASS" if worst <= G8 else "FAIL"))
    return 0 if worst <= G8 else 1


if __name__ == "__main__":
    sys.exit(main())
