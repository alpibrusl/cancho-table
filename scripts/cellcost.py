#!/usr/bin/env python3
"""The cell-cost rounds' stopwatch (docs/history.md): `table` only, one thread, minimum of N runs, seconds.

    python3 scripts/cellcost.py [--runs 5] [--cells select,filter,...]

The standard 1M-row file (build/bench/data.csv) for select, filter, group-count and group-sum, and the adversarial
files (build/adv/) for the shapes the round is about. Output goes to /dev/null; the answer is checked by
scripts/corpus.py, not here.
"""
import argparse
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
T = str(ROOT / "build" / "table")
B = ROOT / "build" / "bench"
A = ROOT / "build" / "adv"
BIG = ["--max-groups", "1000000", "--max-state-bytes", "1073741824"]
CELLS = {
    "select": (B, "data.csv", ["--select", "status,bytes"]),
    "filter": (B, "data.csv", ["--where", "status=404 and bytes:int>50000"]),
    "count": (B, "data.csv", ["--group", "status"]),
    "sum": (B, "data.csv", ["--group", "status", "--agg", "sum:bytes"]),
    "count_plain": (A, "f2.csv", ["--group", "s"]),  # no quotes anywhere in the file
    "filter90": (B, "data.csv", ["--where", "bytes:int>10000"]),
    "wide": (A, "wide.csv", ["--select", "c5,c100,c199"]),
    "quoted": (A, "quoted.csv", ["--select", "status,note"]),
    "long": (A, "long.csv", ["--select", "id,g"]),
    "longplain": (A, "longplain.csv", ["--select", "id,g"]),  # long fields without quotes
    "b1": (A, "f2.csv", ["--group", "k100k"]),
    "b3": (A, "f2.csv", ["--group", "k1m"] + BIG),
}


def make_longplain():
    f = A / "longplain.csv"
    if not f.exists():
        import random
        rng = random.Random(11)
        with open(f, "w") as o:
            o.write("id,g,text\n")
            for i in range(20000):
                o.write("%d,g%d,%s\n" % (i, i % 10, "".join(rng.choice("abcdefgh ijklmnop") for _ in range(rng.randrange(1000, 10000)))))


def main():
    make_longplain()
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--cells", default=",".join(CELLS))
    ap.add_argument("--threads", default="1")
    ap.add_argument("--bins", default=T, help="comma separated binaries, run interleaved; the first is the reference")
    a = ap.parse_args()
    bins = a.bins.split(",")
    for name in a.cells.split(","):
        root, f, args = CELLS[name]
        best = {b: 1e9 for b in bins}
        for _ in range(a.runs):
            for b in bins:
                t = time.perf_counter()
                subprocess.run([b, "--root", str(root), *args, "--format", "csv", "--threads", a.threads, f], stdout=subprocess.DEVNULL, check=False)
                best[b] = min(best[b], time.perf_counter() - t)
        mb = (root / f).stat().st_size / 1e6
        ref = best[bins[0]]
        print("%-12s %s" % (name, "  ".join("%7.4f s (%3.0f MB/s, %4.2fx)" % (best[b], mb / best[b], best[b] / ref) for b in bins)))


main()
