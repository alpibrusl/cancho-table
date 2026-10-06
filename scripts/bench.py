#!/usr/bin/env python3
"""Benchmark: `table` against `csvtk -j 1` (and `mlr`, if installed) on the file
of lexsys-tools' docs/next-tools.md section 5, in three scenarios: the shape
(a row count), `cut -f status,bytes` as csv, and the first 1000 rows of two
columns: 1,000,000 rows, about 31 MB,
columns id,status,bytes,path,note with the last one quoted.

    python3 scripts/bench.py [--rows N] [--runs N] [--bin PATH] [--file PATH]

The generator is seeded, so the file is the same everywhere: status is one of
200 200 200 301 404 500, bytes is 0..99999, path is /p/0..999, note is "a,b N%7"
(31,667,311 bytes for 1,000,000 rows, the design's file). Each
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
    """The design file, exactly: seed 1, and per row the draws in this order."""
    random.seed(1)
    with open(path, "w", newline="") as f:
        f.write("id,status,bytes,path,note\n")
        for i in range(rows):
            f.write(f"{i},{random.choice([200, 200, 200, 301, 404, 500])},{random.randint(0, 99999)},/p/{random.randint(0, 999)},\"a,b {i % 7}\"\n")


def scenarios(table, data, rows):
    """name -> {contender -> (argv, a check of its stdout)}. Each scenario asks
    every contender the same question."""
    root = ["--root", str(data.parent)]
    have = {n: shutil.which(n) for n in ("csvtk", "mlr", "wc")}
    shape = {"table": ([table, *root, data.name], lambda s: json.loads(s)["data"]["row_count"] == rows)}
    cut = {"table": ([table, *root, "--select", "status,bytes", "--format", "csv", data.name], lambda s: s.count("\n") == rows + 1)}
    head = {"table": ([table, *root, "--select", "status,bytes", "--limit", "1000", data.name], lambda s: json.loads(s)["data"]["row_count"] == 1000)}
    if have["csvtk"]:
        shape["csvtk -j 1"] = (["csvtk", "-j", "1", "nrow", str(data)], lambda s: s.split()[-1] == str(rows))
        cut["csvtk -j 1"] = (["csvtk", "-j", "1", "cut", "-f", "status,bytes", str(data)], lambda s: s.count("\n") == rows + 1)
        head["csvtk -j 1"] = (["csvtk", "-j", "1", "head", "-n", "1000", str(data)], lambda s: s.count("\n") == 1001)
    if have["mlr"]:
        shape["mlr"] = (["mlr", "--icsv", "--ojson", "count", str(data)], lambda s: str(rows) in s)
        cut["mlr"] = (["mlr", "--icsv", "--ocsv", "cut", "-f", "status,bytes", str(data)], lambda s: s.count("\n") == rows + 1)
        head["mlr"] = (["mlr", "--icsv", "--ojson", "head", "-n", "1000", "then", "cut", "-f", "status,bytes", str(data)], lambda s: s.count('"status"') == 1000)
    if have["wc"]:
        shape["wc -l (floor)"] = (["wc", "-l", str(data)], lambda s: s.split()[0] == str(rows + 1))
    return {"shape: table FILE / csvtk nrow / mlr count": shape,
            "cut: table --select status,bytes --format csv / csvtk cut / mlr cut": cut,
            "first 1000 rows of two columns: table --select ... --limit 1000 (json) / csvtk head / mlr head": head}


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


def run_scenario(title, contenders, runs, size):
    names = list(contenders)
    times = {n: [] for n in names}
    for r in range(runs):
        order = names[r % len(names):] + names[:r % len(names)]
        for n in order:
            seconds, rc = once(contenders[n][0])
            if rc != 0:
                sys.exit("%s exited %d" % (n, rc))
            times[n].append(seconds)
    peaks = {n: rss(contenders[n][0]) for n in names}
    print(title)
    print("%-16s %9s %9s %10s %12s" % ("tool", "min s", "median s", "MB/s", "peak RSS MB"))
    best = min(min(v) for n, v in times.items() if not n.startswith("wc"))
    ref = min(times["csvtk -j 1"]) if "csvtk -j 1" in times else None
    for n in names:
        lo = min(times[n])
        peak = peaks[n]
        note = ""
        if not n.startswith("wc"):
            note = "%.2fx the fastest" % (lo / best)
            if ref and n != "csvtk -j 1":
                note += ", %.2fx csvtk -j 1" % (lo / ref)
        print("%-16s %9.4f %9.4f %10.0f %12s   %s" % (
            n, lo, statistics.median(times[n]), size / 1e6 / lo,
            "%.1f" % (peak / 1024) if peak is not None else "n/a", note))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    ap.add_argument("--only", default="", help="run only the scenarios whose title starts with this")
    args = ap.parse_args()
    data = pathlib.Path(args.file).resolve()
    if not data.exists() or data.stat().st_size == 0:
        data.parent.mkdir(parents=True, exist_ok=True)
        generate(data, args.rows)
    size = data.stat().st_size
    plan = scenarios(os.path.abspath(args.bin), data, args.rows)
    for title, contenders in plan.items():
        for name, (argv, check) in contenders.items():
            p = subprocess.run(argv, capture_output=True, text=True)
            if p.returncode != 0 or not check(p.stdout):
                sys.exit("%s did not give the answer (%s): %r %r" % (name, title, p.stdout[:200], p.stderr[:200]))
    print("file: %s, %d bytes, %d rows; %s %s; minimum of %d interleaved runs, output to /dev/null" % (
        data.name, size, args.rows, platform.system(), platform.machine(), args.runs))
    print("versions: csvtk %s; mlr %s" % (version(["csvtk", "version"]) if shutil.which("csvtk") else "not installed",
                                          version(["mlr", "--version"]) if shutil.which("mlr") else "not installed"))
    print()
    for title, contenders in plan.items():
        if title.startswith(args.only):
            run_scenario(title, contenders, args.runs, size)
    if not shutil.which("mlr"):
        print("mlr is not installed here, so it is not measured.")


if __name__ == "__main__":
    main()
