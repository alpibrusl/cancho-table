#!/usr/bin/env python3
"""Gates G8 (for the sum) and G10 of docs/numbers.md for the exact float `sum` and `mean` (stage N4): who is exact, and what it costs.

    python3 scripts/bench_numbers_fsum.py [--bin build/table] [--runs 7] [--file build/num/data.csv] [--threads 16] [--rows 1000000]

The file of scripts/bench_numbers.py (id,status,bytes,price,ratio,path,cents). Two float columns: `price` (two decimals, the number 1,000,000 times over a few
hundred thousand distinct values: the sum is a long run of small terms), `ratio` (a double of 15 to 17 digits). Questions, grouped by `status`:

  sum     the sum                    mean    the mean                   both    sum and mean

for `table` (`:float`: the exact accumulator) at 1, 4 and 16 threads, DuckDB (DOUBLE, `sum` and `avg`, at the same thread counts: its answer depends on the
thread count), csvtk and Miller where installed, and the integer cell of the same file as the reference for G8 (`sum:cents`, the same width: the exact float sum
of `price` must be within 1.25x of it). **Every answer is compared with Python first**: the exact sum rounded once (`Fraction`), `math.fsum` as a cross-check,
the mean rounded once. For each contender and thread count the script prints how many of the groups it got exactly right and the largest error in units in the
last place: **who is exact** is the point of the type.
"""
import argparse
import csv
import io
import math
import pathlib
import shutil
import statistics
import subprocess
import sys
import time
from fractions import Fraction

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench_numbers as bn  # noqa: E402

ROOT = bn.ROOT
G8 = 1.25


def truth(data):
    """column -> status -> (exact sum rounded once, exact mean rounded once, math.fsum)."""
    cols = {"price": {}, "ratio": {}}
    with open(data, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            cols["price"].setdefault(r[1], []).append(float(r[3]))
            cols["ratio"].setdefault(r[1], []).append(float(r[4]))
    out = {}
    for col, groups in cols.items():
        out[col] = {}
        for k, xs in groups.items():
            s = sum((Fraction(x) for x in xs), Fraction(0))
            out[col][k] = (float(s), float(s / len(xs)), math.fsum(xs))
    return out


def ulps(got, want):
    if got == want:
        return 0.0
    u = math.ulp(want) if want != 0 else 5e-324
    return abs(got - want) / u


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def parse(text, header):
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    if header:
        rows = rows[1:]
    return {r[0]: [float(x) for x in r[1:]] for r in rows}


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
    want = truth(data)
    d, root, name = str(data), ["--root", str(data.parent)], data.name
    QS = {"sum": ["sum"], "mean": ["mean"], "both": ["sum", "mean"]}
    counts = (1, 4, a.threads)

    def tbl(col, typ, q, threads):
        items = ",".join((f"{fn}:{col}:{typ}" if typ != "int" else f"{fn}:{col}" + ("@4" if fn == "mean" else "")) for fn in QS[q])
        t = [] if threads == 1 else ["--threads", str(threads), "--parallel-min-bytes", "0"]
        return [a.bin, *root, "--group", "status", "--agg", items, "--format", "csv", *t, name]

    def duck(col, q, threads):
        sel = ", ".join({"sum": f"sum({col})", "mean": f"avg({col})"}[fn] for fn in QS[q])
        rd = f"read_csv('{d}', header=true, columns={{'id':'bigint','status':'int','bytes':'bigint','price':'double','ratio':'double','path':'varchar','cents':'bigint'}})"
        return ["duckdb", "-csv", "-noheader", "-c", f"set threads={threads}; copy (select status, {sel} from {rd} group by status order by 1) to '/dev/stdout' (format csv, header false)"]

    cells = []     # (label, key, argv, col, q, header, threads)
    for col in ("price", "ratio"):
        for q in QS:
            for th in counts:
                cells.append((f"table {col}:float {q:4} t={th}", ("table", col, q, th), tbl(col, "float", q, th), col, q, True, th))
                if shutil.which("duckdb"):
                    cells.append((f"duckdb DOUBLE {col} {q:4} t={th}", ("duck", col, q, th), duck(col, q, th), col, q, False, th))
    for q in ("sum", "mean"):
        for th in counts:
            cells.append((f"table cents:int  {q:4} t={th}", ("int", "cents", q, th), tbl("cents", "int", q, th), "cents", q, True, th))
    for col in ("price", "ratio"):
        fn = ["sum", "mean"]
        if shutil.which("csvtk"):
            cells.append((f"csvtk -j 1 {col} both", ("csvtk", col, "both", 1), ["csvtk", "-j", "1", "summary", "-g", "status", "-f", ",".join(f"{col}:{f}" for f in fn), "-w", "17", d], col, "both", True, 1))
        if shutil.which("mlr"):
            cells.append((f"mlr {col} both", ("mlr", col, "both", 1), ["mlr", "--icsv", "--ocsv", "--ofmt", "%.17g", "stats1", "-a", "sum,mean", "-f", col, "-g", "status", "then", "sort", "-f", "status", d], col, "both", True, 1))

    good, accuracy, answers = [], {}, {}
    for label, key, argv, col, q, header, th in cells:
        t, p = timed(argv)
        if p.returncode != 0:
            print("FAILED", label, p.stderr[:200])
            continue
        if col == "cents":
            good.append((label, key, argv))
            continue
        got = parse(p.stdout.decode(), header)
        exact_n = total = 0
        worst = 0.0
        for status, vals in got.items():
            s_ref, m_ref, f_ref = want[col][status]
            refs = [s_ref, m_ref] if q == "both" else [s_ref] if q == "sum" else [m_ref]
            for v, r in zip(vals, refs):
                total += 1
                exact_n += v == r
                worst = max(worst, ulps(v, r))
        if key[0] == "table" and exact_n != total:
            print("WRONG ANSWER", label, exact_n, total, worst)
            continue
        accuracy[key] = (exact_n, total, worst)
        answers[key] = got
        good.append((label, key, argv))
    times = {l: [] for l, _, _ in good}
    for i in range(a.runs):
        for label, key, argv in (good if i % 2 == 0 else list(reversed(good))):
            times[label].append(timed(argv)[0])
    best = {l: min(v) for l, v in times.items()}
    print("%-34s %9s   %s" % ("cell (minimum of %d)" % a.runs, "seconds", "exact groups (largest error in ulps)"))
    for label, key, _ in good:
        acc = accuracy.get(key)
        print("%-34s %9.4f   %s" % (label, best[label], "%d of %d (%.1f ulp)" % acc if acc else ""))
    # a contender whose answer depends on the thread count: the answers (as parsed doubles) of one question at the thread counts, compared bit for bit
    for who in ("table", "duck"):
        for col in ("price", "ratio"):
            for q in QS:
                distinct = {repr(sorted(answers[(who, col, q, th)].items())) for th in counts if (who, col, q, th) in answers}
                if distinct:
                    print("threads: %-5s %-5s %-4s %d distinct answer(s) over %d thread counts" % (who, col, q, len(distinct), len(counts)))
    worst_g8 = 0.0
    for q in ("sum", "mean"):
        for th in counts:
            f, i = best.get(f"table price:float {q:4} t={th}"), best.get(f"table cents:int  {q:4} t={th}")
            if f and i:
                worst_g8 = max(worst_g8, f / i)
                print("G8 %-4s threads %-3d price:float / cents:int = %.3f%s" % (q, th, f / i, "  <-- over %.2f" % G8 if f / i > G8 else ""))
    for col in ("price", "ratio"):
        for q in QS:
            for th in counts:
                t, du = best.get(f"table {col}:float {q:4} t={th}"), best.get(f"duckdb DOUBLE {col} {q:4} t={th}")
                if t and du:
                    print("G10 %-5s %-4s t=%-3d duckdb / table = %.2f" % (col, q, th, du / t))
        for who in ("csvtk -j 1", "mlr"):
            o, t = best.get(f"{who} {col} both"), best.get(f"table {col}:float both t=1")
            if o and t:
                print("G10 %-5s both %s / table(1 thread) = %.2f" % (col, who, o / t))
    print("G8: worst price:float / cents:int %.3f (limit %.2f): %s" % (worst_g8, G8, "PASS" if worst_g8 <= G8 else "FAIL"))
    return 0 if worst_g8 <= G8 else 1


if __name__ == "__main__":
    sys.exit(main())
