#!/usr/bin/env python3
"""The comparison of docs/gap-scan.md: table before and after, DuckDB, csvtk and Miller, on the cells of the gaps.

    python3 scripts/spikes/scan/final.py --old build/table.base --new build/table.final --threads 1,4,8,16 [--prefix "taskset -c 0-5"] [--runs 5] [--cells std,G,E]

Minimum of the runs, interleaved, standard output to /dev/null; DuckDB and csvtk get the same thread counts.
"""
import argparse, pathlib, platform, shlex, shutil, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[3]
A, B = ROOT / "build/adv", ROOT / "build/bench"
BIGARGS = ["--max-rows", "1000000000"]


def timeit(cmd, runs):
    best = 1e9
    for _ in range(runs):
        t = time.perf_counter()
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        best = min(best, time.perf_counter() - t)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="build/table.base"); ap.add_argument("--new", default="build/table.final")
    ap.add_argument("--threads", default="1,4,8,16"); ap.add_argument("--prefix", default=""); ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--cells", default="std,G,E"); ap.add_argument("--duckdb", default=shutil.which("duckdb") or str(pathlib.Path.home() / "bin/duckdb"))
    ap.add_argument("--csvtk", default=shutil.which("csvtk") or str(pathlib.Path.home() / "bin/csvtk")); ap.add_argument("--mlr", default=shutil.which("mlr") or str(pathlib.Path.home() / "bin/mlr"))
    a = ap.parse_args()
    pre = shlex.split(a.prefix)
    old, new = str(ROOT / a.old), str(ROOT / a.new)
    have_mlr = pathlib.Path(a.mlr).exists()
    threads = [int(x) for x in a.threads.split(",")]

    def tbl(b, root, f, args, t):
        return pre + [b, "--root", str(root), *args, "--format", "csv", "--threads", str(t), f]

    def duck(path, sql, t):
        return pre + [a.duckdb, "-c", "SET threads=%d; COPY (%s) TO '/dev/null' (FORMAT csv)" % (t, sql.replace("@", "read_csv('%s')" % path))]

    cells = []
    if "std" in a.cells:
        f = str(B / "data.csv")
        cells += [("select status,bytes", B, "data.csv", ["--select", "status,bytes"], [1], "SELECT status,bytes FROM @", f, ["cut", "-f", "status,bytes"], ["cut", "-o", "-f", "status,bytes"]),
                  ("filter 404 & >50000", B, "data.csv", ["--where", "status=404 and bytes:int>50000"], [1], "SELECT * FROM @ WHERE status=404 AND bytes>50000", f, ["filter", "-f", "bytes>50000"], ["filter", "$status==404 && $bytes>50000"]),
                  ("group-count status", B, "data.csv", ["--group", "status"], [1], "SELECT status,count(*) FROM @ GROUP BY status", f, ["freq", "-f", "status"], ["count-distinct", "-f", "status"]),
                  ("group-sum bytes", B, "data.csv", ["--group", "status", "--agg", "sum:bytes"], [1], "SELECT status,sum(bytes) FROM @ GROUP BY status", f, ["summary", "-g", "status", "-f", "bytes:sum"], ["stats1", "-a", "sum", "-f", "bytes", "-g", "status"])]
    if "G" in a.cells:
        f = str(A / "big.csv")
        cells += [("G2 1GB group-count", A, "big.csv", ["--group", "status"] + BIGARGS, threads, "SELECT status,count(*) FROM @ GROUP BY status", f, ["freq", "-f", "status"], ["count-distinct", "-f", "status"]),
                  ("G1 1GB filter", A, "big.csv", ["--where", "status=404 and bytes:int>50000"] + BIGARGS, threads, "SELECT * FROM @ WHERE status=404 AND bytes>50000", f, ["filter", "-f", "bytes>50000"], None)]
    if "E" in a.cells:
        f = str(A / "long.csv")
        cells += [("E1 long fields select", A, "long.csv", ["--select", "id,g"], threads, "SELECT id,g FROM @", f, ["cut", "-f", "id,g"], ["cut", "-o", "-f", "id,g"]),
                  ("E2 long fields count", A, "long.csv", ["--group", "g"], threads, "SELECT g,count(*) FROM @ GROUP BY g", f, ["freq", "-f", "g"], ["count-distinct", "-f", "g"])]
    print("| cell | threads | old | new | new/old | DuckDB | csvtk | Miller |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, root, f, args, ts, sql, path, csvtk_args, mlr_args in cells:
        base_csvtk = None
        for t in ts:
            o = timeit(tbl(old, root, f, args, t), a.runs)
            n = timeit(tbl(new, root, f, args, t), a.runs)
            d = timeit(duck(path, sql, t), max(2, a.runs - 2)) if pathlib.Path(a.duckdb).exists() else float("nan")
            c = float("nan"); m = float("nan")
            if t == ts[0] and pathlib.Path(a.csvtk).exists():
                c = timeit(pre + [a.csvtk, "-j", "1", *csvtk_args, path] if csvtk_args[0] != "filter" else pre + [a.csvtk, "-j", "1", "filter", "-f", "bytes>50000", path], 2 if "1GB" in name else a.runs)
            if t == ts[0] and have_mlr and mlr_args:
                m = timeit(pre + [a.mlr, "--icsv", "--ocsv", *mlr_args, path], 2 if "1GB" in name else a.runs)
            fmt = lambda x: "-" if x != x else "%.3f" % x
            print("| %s | %d | %s | %s | %.2fx | %s | %s | %s |" % (name, t, fmt(o), fmt(n), n / o, fmt(d), fmt(c), fmt(m)), flush=True)

main()
