#!/usr/bin/env python3
"""Benchmark: `table` against `csvtk -j 1` (and `mlr`, if installed) on the file
of cancho-tools' docs/next-tools.md section 5, in three scenarios: the shape
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


def truth(data):
    """What every contender must answer, computed here, independently: the ids of
    the rows the filter keeps, and the count and the sum of `bytes` by status."""
    import csv
    ids, count, total = [], {}, {}
    with open(data, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            status, size = int(row[1]), int(row[2])
            count[status] = count.get(status, 0) + 1
            total[status] = total.get(status, 0) + size
            if status == 404 and size > 50000:
                ids.append(row[0])
    return ids, count, total


def pairs(stdout, pattern):
    out = {}
    for line in stdout.splitlines():
        m = re.match(pattern, line)
        if m:
            out[int(m.group(1))] = int(m.group(2))
    return out


def scenarios(table, data, rows, expected):
    """name -> {contender -> (argv, a check of its stdout)}. Each scenario asks
    every contender the same question."""
    ids, count, total = expected
    root = ["--root", str(data.parent)]
    have = {n: shutil.which(n) for n in ("csvtk", "mlr", "wc", "awk", "sh")}
    f = str(data)
    shape = {"table": ([table, *root, data.name], lambda s: json.loads(s)["data"]["row_count"] == rows)}
    cut = {"table": ([table, *root, "--select", "status,bytes", "--format", "csv", data.name], lambda s: s.count("\n") == rows + 1)}
    head = {"table": ([table, *root, "--select", "status,bytes", "--limit", "1000", data.name], lambda s: json.loads(s)["data"]["row_count"] == 1000)}
    def kept(s):
        lines = s.splitlines()
        return lines[0].startswith("id,status") and [l.split(",", 1)[0] for l in lines[1:]] == ids
    filt = {"table": ([table, *root, "--where", "status=404 and bytes:int>50000", "--format", "csv", data.name], kept)}
    freq = {"table": ([table, *root, "--group", "status", "--format", "csv", data.name], lambda s: pairs(s, r"^(\d+),(\d+)$") == count)}
    summ = {"table": ([table, *root, "--group", "status", "--agg", "sum:bytes", "--format", "csv", data.name], lambda s: pairs(s, r"^(\d+),(\d+)(?:\.0+)?$") == total)}
    if have["csvtk"]:
        shape["csvtk -j 1"] = (["csvtk", "-j", "1", "nrow", f], lambda s: s.split()[-1] == str(rows))
        cut["csvtk -j 1"] = (["csvtk", "-j", "1", "cut", "-f", "status,bytes", f], lambda s: s.count("\n") == rows + 1)
        head["csvtk -j 1"] = (["csvtk", "-j", "1", "head", "-n", "1000", f], lambda s: s.count("\n") == 1001)
        filt["csvtk -j 1 filter2"] = (["csvtk", "-j", "1", "filter2", "-f", "$status==404 && $bytes>50000", f], kept)
        filt["csvtk -j 1 filter|grep"] = (["sh", "-c", "csvtk -j 1 filter -f 'bytes>50000' %s | csvtk -j 1 grep -f status -p 404" % f], kept)
        freq["csvtk -j 1 freq"] = (["csvtk", "-j", "1", "freq", "-f", "status", f], lambda s: pairs(s, r"^(\d+),(\d+)$") == count)
        summ["csvtk -j 1 summary"] = (["csvtk", "-j", "1", "summary", "-g", "status", "-f", "bytes:sum", f], lambda s: pairs(s, r"^(\d+),(\d+)(?:\.0+)?$") == total)
    if have["mlr"]:
        shape["mlr"] = (["mlr", "--icsv", "--ojson", "count", f], lambda s: str(rows) in s)
        cut["mlr"] = (["mlr", "--icsv", "--ocsv", "cut", "-f", "status,bytes", f], lambda s: s.count("\n") == rows + 1)
        head["mlr"] = (["mlr", "--icsv", "--ojson", "head", "-n", "1000", "then", "cut", "-f", "status,bytes", f], lambda s: s.count('"status"') == 1000)
        filt["mlr filter"] = (["mlr", "--icsv", "--ocsv", "filter", "$status==404 && $bytes>50000", f], kept)
        freq["mlr count-distinct"] = (["mlr", "--icsv", "--ocsv", "count-distinct", "-f", "status", f], lambda s: pairs(s, r"^(\d+),(\d+)$") == count)
        summ["mlr stats1"] = (["mlr", "--icsv", "--ocsv", "stats1", "-a", "sum", "-f", "bytes", "-g", "status", f], lambda s: pairs(s, r"^(\d+),(\d+)(?:\.0+)?$") == total)
    if have["awk"] and have["sh"]:
        # A reference floor where CSV quoting does not matter: it is not equivalent
        # on the quoted column (awk -F, would split `"a,b 1"`), which no condition here reads.
        filt["awk -F, (floor, not CSV)"] = (["awk", "-F,", "NR==1{print;next} $2==404 && $3>50000", f], kept)
        freq["cut|sort|uniq -c (floor, not CSV)"] = (["sh", "-c", "cut -d, -f2 %s | LC_ALL=C sort | uniq -c" % f], lambda s: pairs(s, r"^\s*(\d+) (\d+)$") == {k: v for k, v in count.items()} or {v: k for k, v in pairs(s, r"^\s*(\d+) (\d+)$").items()} == count)
    if have["wc"]:
        shape["wc -l (floor)"] = (["wc", "-l", f], lambda s: s.split()[0] == str(rows + 1))
    return {"shape: table FILE / csvtk nrow / mlr count": shape,
            "cut: table --select status,bytes --format csv / csvtk cut / mlr cut": cut,
            "first 1000 rows of two columns: table --select ... --limit 1000 (json) / csvtk head / mlr head": head,
            "filter: status=404 and bytes>50000, all columns as csv": filt,
            "group-count by status": freq,
            "group sum of bytes by status": summ}


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
    best = min(min(v) for n, v in times.items() if not n.startswith(("wc", "awk", "cut|")))
    ref = min(min(times[n]) for n in times if n.startswith("csvtk -j 1")) if any(n.startswith("csvtk -j 1") for n in times) else None
    for n in names:
        lo = min(times[n])
        peak = peaks[n]
        note = ""
        if not n.startswith(("wc", "awk", "cut|")):
            note = "%.2fx the fastest" % (lo / best)
            if ref and n.startswith("table"):
                note += ", %.2fx the best csvtk -j 1" % (lo / ref)
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
    plan = scenarios(os.path.abspath(args.bin), data, args.rows, truth(data))
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
