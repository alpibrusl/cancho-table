#!/usr/bin/env python3
"""Gates G7 and G10 of docs/numbers.md for the decimal filter and select (stage N1).

    python3 scripts/bench_numbers.py [--bin build/table] [--rows 1000000] [--runs 7] [--file build/num/data.csv] [--threads 16]

A seeded file of id,status,bytes,price,ratio,path (`price` two decimals in 0..99999.99, `bytes` an integer in 0..99999, `ratio` a
double in shortest form). The questions, each asked of `table` as the integer cell and as the decimal cell, and of DuckDB (DECIMAL(18,2)
and DOUBLE), csvtk and Miller (when installed):

  count   how many rows have status=404 and value >= 500          (the comparison, nothing written)
  rows    the rows with status=404 and value >= 500, as id,value  (the comparison and the output, as csv)

Every contender's answer is checked first against Python's: the count, and the ids and values of the rows (as Decimal: a double
column prints `500.5` where a decimal prints `500.50`, which is a different text and the same number). Then the minimum of `runs`
interleaved runs. G7 is the ratio table(dec) / table(int) on the same question, which must be at most 1.15 at one thread and at
`--threads`. The ratios against the others are printed and not gated: they are what the page says.
"""
import argparse
import csv
import io
import os
import pathlib
import random
import shutil
import subprocess
import sys
import time
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIMIT = 1.15


def generate(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    r = random.Random(1)
    with open(path, "w", newline="") as f:
        f.write("id,status,bytes,price,ratio,path\n")
        for i in range(rows):
            f.write(f"{i},{r.choice([200, 200, 200, 301, 404, 500])},{r.randint(0, 99999)},{r.randint(0, 9999999) / 100:.2f},{r.uniform(0, 1000)!r},/p/{r.randint(0, 999)}\n")


def truth(path):
    ints, decs = [], []
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if row[1] == "404":
                if int(row[2]) >= 500:
                    ints.append((row[0], Decimal(row[2])))
                if Decimal(row[3]) >= 500:
                    decs.append((row[0], Decimal(row[3])))
    return ints, decs


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def rows_of(text):
    out = []
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].lstrip("-").isdigit():
            out.append((row[0], Decimal(row[1])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--file", default=str(ROOT / "build" / "num" / "data.csv"))
    ap.add_argument("--threads", type=int, default=16)
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    if not data.exists():
        generate(data, a.rows)
    ints, decs = truth(data)
    d = str(data)
    root = ["--root", str(data.parent)]
    name = data.name

    def table(where, select, threads, count):
        t = [] if threads == 1 else ["--threads", str(threads), "--parallel-min-bytes", "0"]
        if count:
            return [a.bin, *root, "--where", where, "--agg", "count", "--format", "csv", *t, name]
        return [a.bin, *root, "--where", where, "--select", select, "--format", "csv", *t, name]

    def duck(col, typ, count, threads):
        rd = f"read_csv('{d}', header=true, columns={{'id':'bigint','status':'int','bytes':'bigint','price':'{typ}','ratio':'double','path':'varchar'}})"
        sel = "count(*)" if count else f"id, {col}"
        fmt = "(header false)" if count else "(format csv, header false)"
        return ["duckdb", "-csv", "-noheader", "-c", f"set threads={threads}; copy (select {sel} from {rd} where status=404 and {col}>=500) to '/dev/stdout' {fmt}"]

    cells = []     # (label, group, argv, expected, count)
    for count in (True, False):
        q = "count" if count else "rows"
        want_i, want_d = ints, decs
        chk = (lambda w: (lambda o: int(o.strip().split("\n")[-1].split(",")[-1]) == len(w))) if count else (lambda w: (lambda o: rows_of(o) == w))
        for threads in (1, a.threads):
            cells.append((f"table int   {q} t={threads}", (q, threads, "int"), table("status = 404 and bytes:int >= 500", "id,bytes", threads, count), chk(want_i)))
            cells.append((f"table dec   {q} t={threads}", (q, threads, "dec"), table("status = 404 and price:dec(2) >= 500.00", "id,price", threads, count), chk(want_d)))
        for threads in (1, a.threads) if shutil.which("duckdb") else ():
            cells.append((f"duckdb DECIMAL {q} t={threads}", (q, threads, "duck-dec"), duck("price", "decimal(18,2)", count, threads), chk(want_d)))
            cells.append((f"duckdb DOUBLE  {q} t={threads}", (q, threads, "duck-dbl"), duck("price", "double", count, threads), chk(want_d)))
        if shutil.which("csvtk") and not count:
            cells.append((f"csvtk -j 1      {q}", (q, 1, "csvtk"), ["csvtk", "-j", "1", "filter2", "-f", "$status==404 && $price>=500", d], lambda o: [(r[0], Decimal(r[3])) for r in csv.reader(io.StringIO(o)) if r and r[0].isdigit()] == decs))
        if shutil.which("csvtk") and count:
            cells.append((f"csvtk -j 1      {q}", (q, 1, "csvtk"), ["csvtk", "-j", "1", "filter2", "-f", "$status==404 && $price>=500", d], lambda o: len(o.strip().split("\n")) - 1 == len(decs)))
        if shutil.which("mlr"):
            expr = "$status==404 && $price>=500"
            argv = ["mlr", "--icsv", "--ocsv", "filter", expr, d] if not count else ["mlr", "--icsv", "--ocsv", "count-similar", "-g", "status", "then", "nothing", d]
            if not count:
                cells.append((f"mlr            {q}", (q, 1, "mlr"), argv, lambda o: [(r[0], Decimal(r[3])) for r in csv.reader(io.StringIO(o)) if r and r[0].isdigit()] == decs))
    good = []
    for label, grp, argv, check in cells:
        t, p = timed(argv)
        out = p.stdout.decode()
        if p.returncode != 0 or not check(out):
            print("WRONG ANSWER or failure:", label, p.returncode, p.stderr[:200], out[:200])
            continue
        good.append((label, grp, argv))
    times = {label: [] for label, _, _ in good}
    for i in range(a.runs):
        order = good if i % 2 == 0 else list(reversed(good))
        for label, grp, argv in order:
            times[label].append(timed(argv)[0])
    best = {l: min(v) for l, v in times.items()}
    print("%-30s %9s" % ("cell (minimum of %d)" % a.runs, "seconds"))
    for label, _, _ in good:
        print("%-30s %9.4f" % (label, best[label]))
    worst = 0.0
    for q in ("count", "rows"):
        for threads in (1, a.threads):
            i, dd = best.get(f"table int   {q} t={threads}"), best.get(f"table dec   {q} t={threads}")
            if i and dd:
                r = dd / i
                worst = max(worst, r)
                print("G7 %-6s threads %-3d  dec/int = %.3f%s" % (q, threads, r, "   <-- over %.2f" % LIMIT if r > LIMIT else ""))
                for other in ("duckdb DECIMAL", "duckdb DOUBLE "):
                    o = best.get(f"{other} {q} t={threads}")
                    if o:
                        print("      %s %-6s t=%-3d  duckdb / table(dec) = %.2f" % (other.strip(), q, threads, o / dd))
        c = best.get(f"csvtk -j 1      {q}")
        dd = best.get(f"table dec   {q} t=1")
        if c and dd:
            print("      csvtk -j 1 %-6s  csvtk / table(dec, 1 thread) = %.2f" % (q, c / dd))
        m = best.get(f"mlr            {q}")
        if m and dd:
            print("      mlr %-6s  mlr / table(dec, 1 thread) = %.2f" % (q, m / dd))
    print("G7: worst dec/int %.3f (limit %.2f): %s" % (worst, LIMIT, "PASS" if worst <= LIMIT else "FAIL"))
    return 0 if worst <= LIMIT else 1


sys.exit(main())
