#!/usr/bin/env python3
"""Gate G6 of docs/numbers.md: no existing integer path slower than +2%.

    python3 scripts/gate_regress.py --base BIN_BEFORE --new BIN_AFTER [--runs 9] [--file build/bench/data.csv] [--limit 1.02]

The standard cells of the 1,000,000-row benchmark file (scripts/bench.py generates it): the filter, `cut`, group-count, group-sum,
group-sum + min/max, a filter with a text condition, at one thread and at four (`--parallel-min-bytes 0`). Each cell is run by both
binaries, interleaved (base, new, new, base, ...), `runs` times; the answers must be byte-identical before anything is timed; the
figure is the minimum. Exit 1 when any cell's new/base ratio is above the limit. Run it on a quiet machine: the ratios, not the
seconds, are the point, and a ratio within noise of 1.0 on repeated runs is what a pass looks like.
"""
import argparse
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CELLS = {
    "filter status=404 and bytes>50000": ["--where", "status=404 and bytes:int>50000", "--format", "csv"],
    "cut status,bytes": ["--select", "status,bytes", "--format", "csv"],
    "group-count status": ["--group", "status", "--format", "csv"],
    "group-sum bytes by status": ["--group", "status", "--agg", "sum:bytes", "--format", "csv"],
    "group count,sum,min,max": ["--group", "status", "--agg", "count,sum:bytes,min:bytes,max:bytes", "--format", "csv"],
    "text filter path contains": ["--where", "path contains /p/1", "--select", "id", "--format", "csv"],
    "sum of everything": ["--agg", "sum:bytes", "--format", "csv"],
}


def argv_of(binary, data, args, threads):
    t = [] if threads == 1 else ["--threads", str(threads), "--parallel-min-bytes", "0"]
    return [str(binary), "--root", str(data.parent), *args, *t, data.name]


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t, p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=9)
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    ap.add_argument("--limit", type=float, default=1.02)
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    worst = 0.0
    print("%-36s %7s %9s %9s %7s" % ("cell", "threads", "base s", "new s", "ratio"))
    for name, args in CELLS.items():
        for threads in (1, 4):
            b, n = argv_of(a.base, data, args, threads), argv_of(a.new, data, args, threads)
            ob = subprocess.run(b, capture_output=True)
            on = subprocess.run(n, capture_output=True)
            if (ob.stdout, ob.stderr, ob.returncode) != (on.stdout, on.stderr, on.returncode):
                print("DIFFERENT OUTPUT:", name, threads)
                return 2
            tb, tn = [], []
            for i in range(a.runs):
                order = (b, n) if i % 2 == 0 else (n, b)
                for argv in order:
                    (tb if argv is b else tn).append(timed(argv)[0])
            r = min(tn) / min(tb)
            worst = max(worst, r)
            print("%-36s %7d %9.4f %9.4f %6.3fx%s" % (name, threads, min(tb), min(tn), r, "  <-- over the limit" if r > a.limit else ""))
    print("worst ratio %.3f (limit %.2f): %s" % (worst, a.limit, "PASS" if worst <= a.limit else "FAIL"))
    return 0 if worst <= a.limit else 1


sys.exit(main())
