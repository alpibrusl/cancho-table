#!/usr/bin/env python3
"""Gate G6 of docs/numbers.md (third revision): no existing path slower than it was, judged build-averaged.

    python3 scripts/gate_regress.py --old OLD1 OLD2 OLD3 --new NEW1 NEW2 NEW3 [--runs 21] [--file build/num/data.csv] [--threads 4]

OLD1..: the sources of the previous stage built at least three times, each in its own directory; NEW1..: the sources of this stage built at least three
times, each in its own directory. For every cell (the standard ones of the benchmark file at 1 thread and at `--threads`) all the binaries are run
interleaved, `--runs` times (at least 21), the order rotating and every other round reversed, two warm-up runs of each dropped, the outputs of ALL the
binaries byte-identical before anything is timed. Per binary the **minimum** of its runs; per side the **mean over builds** of those minima;

    pass  <=>  mean(new minima) / mean(old minima)  <=  1.02

and the spread across builds (max - min over the mean, per side) is printed, so that one outlier build is visible. Exit 0 pass, 1 a cell over, 2 outputs differ.

Why this form (revision three; the first two judged ONE new build against one or two builds of the old sources). A build of unchanged sources runs up to 2 to 5%
slower or faster than another by code layout alone; a single new build is one random draw of that layout, and a gate that compares one draw with a bound
fails identical sources as often as it fails a change (the control of docs/numbers.md, G6 for N3a: 2, 2, 3, 6 and 0 of 14 cells over for identical sources, 3, 3, 0, 5, 5 for N3a). Averaging
builds on both sides shrinks the draw by the square root of the number of builds, and a change that is real moves the mean while layout does not. The minimum
(not the median) per build: the work of a cell is deterministic, the noise is only ever added, so the minimum has the smallest variance; the medians of 21 runs of a
40 ms cell scattered 0.92 to 1.04 in the controls where the minima stayed within 1.00 to 1.03. What it still cannot show: a regression smaller than the
averaged draw (about 1%); and a cell much shorter than a few milliseconds. The runs are the cost, the builds are cheap, so add builds before adding runs.
"""
import argparse
import pathlib
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIMIT = 1.02
SPREAD_WARN = 0.04
CELLS = {
    "filter status=404 and bytes>50000": ["--where", "status=404 and bytes:int>50000", "--format", "csv"],
    "cut status,bytes": ["--select", "status,bytes", "--format", "csv"],
    "group-count status": ["--group", "status", "--format", "csv"],
    "group-sum bytes by status": ["--group", "status", "--agg", "sum:bytes", "--format", "csv"],
    "group count,sum,min,max": ["--group", "status", "--agg", "count,sum:bytes,min:bytes,max:bytes", "--format", "csv"],
    "text filter path contains": ["--where", "path contains /p/1", "--select", "id", "--format", "csv"],
    "sum of everything": ["--agg", "sum:bytes", "--format", "csv"],
    # the typed paths of the stages before (they need the columns of scripts/bench_numbers.py's file)
    "dec filter price:dec(2)>=500": ["--where", "status = 404 and price:dec(2) >= 500", "--select", "id", "--format", "csv"],
    "dec group-sum cents": ["--group", "status", "--agg", "sum:cents:dec(2),min:price:dec(2),max:price:dec(2)", "--format", "csv"],
    "float filter price:float>=500": ["--where", "status = 404 and price:float >= 500", "--select", "id", "--format", "csv"],
    "float min/max by status": ["--group", "status", "--agg", "min:ratio:float,max:ratio:float", "--format", "csv"],
}


def argv_of(binary, data, args, threads):
    t = [] if threads == 1 else ["--threads", str(threads), "--parallel-min-bytes", "0"]
    return [str(binary), "--root", str(data.parent), *args, *t, data.name]


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t, p.returncode


def judge(old, new):
    """old, new: per build, the minimum time of the runs. Returns (ratio of the means, spread old, spread new, medians ratio for information)."""
    mo, mn = statistics.mean(old), statistics.mean(new)
    so = (max(old) - min(old)) / mo
    sn = (max(new) - min(new)) / mn
    return mn / mo, so, sn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", nargs="+", required=True, help="the previous stage built at least 3 times, one binary per directory")
    ap.add_argument("--new", nargs="+", required=True, help="this stage built at least 3 times, one binary per directory")
    ap.add_argument("--runs", type=int, default=21)
    ap.add_argument("--file", default=str(ROOT / "build" / "num" / "data.csv"))
    ap.add_argument("--threads", type=int, default=4, help="the second thread count (the first is 1); on a box of three physical cores use 3")
    ap.add_argument("--cell", default=None, help="only the cells whose name contains this text")
    ap.add_argument("--limit", type=float, default=LIMIT)
    a = ap.parse_args()
    if len(a.old) < 3 or len(a.new) < 3:
        ap.error("at least three builds of each side are needed (--old and --new): one build is one random draw of code layout (docs/numbers.md, G6, third revision)")
    if a.runs < 21:
        ap.error("--runs must be at least 21")
    data = pathlib.Path(a.file)
    if not data.exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        import bench_numbers
        bench_numbers.generate(data, 1_000_000)
    bins = list(a.old) + list(a.new)
    no = len(a.old)
    failed = False
    print("%-34s %3s %9s %9s %7s %7s %7s  %s" % ("cell", "thr", "old s", "new s", "ratio", "spr old", "spr new", ""))
    for name, args in CELLS.items():
        if a.cell and a.cell not in name:
            continue
        for threads in (1, a.threads):
            argvs = [argv_of(b, data, args, threads) for b in bins]
            outs = [subprocess.run(x, capture_output=True) for x in argvs]
            if any((o.stdout, o.stderr, o.returncode) != (outs[0].stdout, outs[0].stderr, outs[0].returncode) for o in outs):
                print("DIFFERENT OUTPUT:", name, threads)
                return 2
            for x in argvs * 2:
                timed(x)                       # warm-up, discarded
            times = [[] for _ in bins]
            for i in range(a.runs):
                order = list(range(len(bins)))
                k0 = i % len(order)
                order = order[k0:] + order[:k0]
                if i % 2:
                    order.reverse()
                for k in order:
                    times[k].append(timed(argvs[k])[0])
            mins = [min(t) for t in times]
            ratio, so, sn = judge(mins[:no], mins[no:])
            over = ratio > a.limit
            failed = failed or over
            flag = ("<-- over %.2f" % a.limit) if over else ""
            if max(so, sn) > SPREAD_WARN:
                flag += "  (a build is off: spread over %d%%)" % round(SPREAD_WARN * 100)
            print("%-34s %3d %9.4f %9.4f %7.3f %6.1f%% %6.1f%%  %s" % (name, threads, statistics.mean(mins[:no]), statistics.mean(mins[no:]), ratio, so * 100, sn * 100, flag))
    print("G6: %s" % ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
