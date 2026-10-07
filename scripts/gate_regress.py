#!/usr/bin/env python3
"""Gate G6 of docs/numbers.md: no existing integer path slower than it was, judged against what two builds of the same sources differ by.

    python3 scripts/gate_regress.py --base BIN_BEFORE --base2 BIN_BEFORE_AGAIN --base3 BIN_BEFORE_A_THIRD_TIME --new BIN_AFTER [--runs 21] [--file build/bench/data.csv]
    python3 scripts/gate_regress.py --base BIN_BEFORE --new BIN_AFTER [--limit 1.02]              (the old fixed bound, for a quiet machine)

Why two more builds (the second revision of the gate). On the Linux box a build of unchanged sources in another directory runs up to 2.3% slower (1 thread) or
5 to 15% (more threads) than the first, by code layout alone (docs/numbers.md, G6). The first definition measured that noise with ONE extra build and it
underestimated it: a second build of the same new sources gave 1.003 to 1.017 where the first gave 1.00 to 1.04. The noise is therefore taken from at least
TWO extra builds of the old sources (`--base2`, `--base3`, other directories) and a cell passes when BOTH hold

    ratio(new / base)  <=  max(1, max_i ratio(base_i / base)) + 0.01        (ratios of the minimum of --runs runs)
    median(new) / median(base)  <=  1 + 0.01                                (the median over the same runs)

with --runs at least 21, the binaries taking turns to go first, two warm-up runs discarded, and the outputs of all of them byte-identical before anything is
timed. What it can show: a regression larger than the layout noise of these builds. What it cannot: none below that noise. Both ratios and the medians are
printed for every cell. Cells: the standard ones of the
1,000,000-row benchmark file (scripts/bench.py generates it) at one thread and at `--threads`. Exit 0 pass, 1 a cell over its bound, 2 outputs differ.
"""
import argparse
import pathlib
import statistics
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


def bound(noise):
    """The ratio a cell may not exceed: what the builds of identical sources differ by at most (never less than 1), plus 1%."""
    return max(1.0, noise) + 0.01


MEDIAN_LIMIT = 1.01


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--base2", default=None, help="the same sources as --base, built again in another directory: measures the build-to-build noise")
    ap.add_argument("--base3", default=None, help="and a third build of them: the noise is the largest of the extra builds, so at least two are needed")
    ap.add_argument("--new", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=21)
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    ap.add_argument("--limit", type=float, default=1.02, help="the fixed bound used when there is no --base2")
    ap.add_argument("--threads", type=int, default=4, help="the second thread count (the first is 1); on a box of three physical cores use 3")
    ap.add_argument("--cell", default=None, help="only the cells whose name contains this text (to look again at one cell with more --runs)")
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    extra = [x for x in (a.base2, a.base3) if x]
    if len(extra) == 1:
        ap.error("--base2 needs --base3: one extra build underestimates the build-to-build noise (docs/numbers.md, G6); give both or neither")
    bins = [a.base, a.new] + extra
    failed = False
    print("%-36s %7s %9s %9s %7s %7s %7s %7s  %s" % ("cell", "threads", "base s", "new s", "new/base", "median", "noise", "bound", ""))
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
                order = order[i % len(order):] + order[:i % len(order)]
                if i % 2:
                    order.reverse()
                for k in order:
                    times[k].append(timed(argvs[k])[0])
            r = min(times[1]) / min(times[0])
            med = statistics.median(times[1]) / statistics.median(times[0])
            noise = max(min(t) for t in times[2:]) / min(times[0]) if extra else None
            limit = bound(noise) if extra else a.limit
            over = r > limit or (bool(extra) and med > MEDIAN_LIMIT)
            failed = failed or over
            print("%-36s %7d %9.4f %9.4f %7.3f %7.3f %7s %7.3f  %s" % (name, threads, min(times[0]), min(times[1]), r, med, "%.3f" % noise if noise else "-", limit, ("<-- over its median limit" if r <= limit else "<-- over its bound") if over else ""))
    print("G6: %s" % ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
