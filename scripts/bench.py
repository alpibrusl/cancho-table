#!/usr/bin/env python3
"""Benchmark: `table` against `csvtk -j 1` (and `mlr`, if installed) on the file
of lexsys-tools' docs/next-tools.md section 5: 1,000,000 rows, about 31 MB,
columns id,status,bytes,path,note with the last one quoted.

    python3 scripts/bench.py [--rows N] [--runs N] [--bin PATH] [--file PATH]

The generator is seeded, so the file is the same everywhere: status is one of
200 200 200 301 404 500, bytes is 0..99999, path is /p/N, note is "a,b N". Each
tool answers the same question, the file's shape; before any timing the row
count each one reports is checked against the generator's. The rounds are
interleaved (every tool once per round, in a rotating order), the minimum of
the runs is the figure and the median is shown beside it, so one noisy round
does not decide. Output goes to /dev/null, as it is only a count. Peak resident
set comes from `/usr/bin/time` (-l on macOS, -v on Linux), once per tool.

Nothing is tuned: the numbers are what the command line gives. `wc -l` is
printed as a floor, the cost of reading the bytes and finding the newlines.
"""

import argparse
import json
import os
import pathlib
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def generate(path, rows):
    rng = random.Random(1)
    statuses = [200, 200, 200, 301, 404, 500]
    with open(path, "w", newline="") as f:
        f.write("id,status,bytes,path,note\n")
        for i in range(rows):
            f.write('%d,%d,%d,/p/%d,"a,b %d"\n' % (i, rng.choice(statuses), rng.randrange(100000), i, i))


def tools(table, data, rows):
    """name -> (argv, a check of its output against the row count)."""
    out = {}
    out["table"] = ([table, "--root", str(data.parent), data.name],
                    lambda s: json.loads(s)["data"]["rows"] == rows)
    if shutil.which("csvtk"):
        out["csvtk -j 1"] = (["csvtk", "-j", "1", "nrow", str(data)],
                             lambda s: s.split()[-1] == str(rows))
    if shutil.which("mlr"):
        out["mlr"] = (["mlr", "--icsv", "--ojson", "count", str(data)],
                      lambda s: str(rows) in s)
    if shutil.which("wc"):
        out["wc -l (floor)"] = (["wc", "-l", str(data)], lambda s: s.split()[0] == str(rows + 1))
    return out


def once(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t, p.returncode


def rss(argv):
    """Peak resident set in KiB, from /usr/bin/time, or None."""
    exe = "/usr/bin/time"
    if not os.path.exists(exe):
        return None
    flag = "-l" if platform.system() == "Darwin" else "-v"
    p = subprocess.run([exe, flag, *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    m = re.search(r"(\d+)\s+maximum resident set size", p.stderr) or re.search(r"Maximum resident set size \(kbytes\): (\d+)", p.stderr)
    if not m:
        return None
    n = int(m.group(1))
    return n // 1024 if platform.system() == "Darwin" else n


def version(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except (OSError, IndexError):
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    args = ap.parse_args()
    data = pathlib.Path(args.file).resolve()
    if not data.exists() or data.stat().st_size == 0:
        data.parent.mkdir(parents=True, exist_ok=True)
        generate(data, args.rows)
    size = data.stat().st_size
    contenders = tools(os.path.abspath(args.bin), data, args.rows)
    for name, (argv, check) in contenders.items():
        p = subprocess.run(argv, capture_output=True, text=True)
        if p.returncode != 0 or not check(p.stdout):
            sys.exit("%s did not report %d rows: %r %r" % (name, args.rows, p.stdout[:200], p.stderr[:200]))
    names = list(contenders)
    times = {n: [] for n in names}
    for r in range(args.runs):
        order = names[r % len(names):] + names[:r % len(names)]
        for n in order:
            seconds, rc = once(contenders[n][0])
            if rc != 0:
                sys.exit("%s exited %d" % (n, rc))
            times[n].append(seconds)
    peaks = {n: rss(contenders[n][0]) for n in names}
    print("file: %s, %.1f MB, %d rows; %s %s; minimum of %d interleaved runs" % (
        data.name, size / 1e6, args.rows, platform.system(), platform.machine(), args.runs))
    print("versions: csvtk %s; mlr %s" % (version(["csvtk", "version"]) if "csvtk -j 1" in times else "not installed",
                                          version(["mlr", "--version"]) if "mlr" in times else "not installed"))
    print()
    print("%-16s %9s %9s %10s %12s" % ("tool", "min s", "median s", "MB/s", "peak RSS MB"))
    best = min(min(v) for n, v in times.items() if not n.startswith("wc"))
    for n in names:
        lo = min(times[n])
        peak = peaks[n]
        print("%-16s %9.3f %9.3f %10.0f %12s   %s" % (
            n, lo, statistics.median(times[n]), size / 1e6 / lo,
            "%.1f" % (peak / 1024) if peak is not None else "n/a",
            "" if n.startswith("wc") else "%.2fx the fastest" % (lo / best)))
    if "mlr" not in times:
        print("\nmlr is not installed here, so it is not measured.")


if __name__ == "__main__":
    main()
