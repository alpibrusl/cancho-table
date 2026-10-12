#!/usr/bin/env python3
"""What standard input costs (docs/readers.md section 3.4, stage R1): the same questions of the benchmark file (scripts/bench.py's, 1,000,000 rows, 31,667,311
bytes) asked of `table` through a file, through a pipe (`cat FILE | table -`) and redirected (`table < FILE`), and of `csvtk` and `duckdb` through a pipe where
they take one.

    python3 scripts/bench_stdin.py [--rows N] [--runs N] [--bin PATH] [--file PATH] [--only SCENARIO-PREFIX] [--floor BIN]

Each contender is a shell pipeline, so that the file is always read by `cat` or by the tool and the pipe is the same pipe. `cat FILE | cat > /dev/null` and
`cat FILE | wc -l` are printed as the floor of the pipe itself. Every answer is checked against the generator's before anything is timed; the rounds are
interleaved in a rotating order; the figure is the minimum of the runs and the median is shown beside it; the machine, its core count and its load average
(before and after) are printed with the table, because a figure without its conditions is not one. A contender that is not installed is skipped and said so.
duckdb reads a pipe through `/dev/stdin` and cannot sniff it, so its columns are declared and the bytes loaded with COPY into a table before the question is asked
(the load is in its time); it is run with `threads=1` as well as with its default.
"""
import argparse
import json
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench  # noqa: E402

ROOT = bench.ROOT


def pipelines(table, data, rows, expected, floor=None):
    ids, count, total = expected
    f = str(data)
    have = {n: shutil.which(n) for n in ("csvtk", "duckdb")}

    def kept(s):
        lines = s.splitlines()
        return lines[0].startswith("id,status") and [l.split(",", 1)[0] for l in lines[1:]] == ids

    def duck(sql, threads=None):
        # DuckDB cannot sniff a pipe (it opens `/dev/stdin` twice and the second open reads nothing), so the columns are declared and the bytes are loaded with COPY, in one pass,
        # into a table; the question is asked of the table. That is the whole of what DuckDB can do with a pipe, and it includes the load.
        pre = "SET threads=%d; " % threads if threads else ""
        return ("cat %s | duckdb -csv -noheader -c \"%sCREATE TABLE t(id BIGINT, status INTEGER, bytes INTEGER, path VARCHAR, note VARCHAR); "
                "COPY t FROM '/dev/stdin' (FORMAT CSV, HEADER true); %s\"") % (f, pre, sql)

    out = {}
    shape = {"table FILE": ("%s --root %s %s" % (table, data.parent, data.name), lambda s: json.loads(s)["data"]["row_count"] == rows),
             "cat | table -": ("cat %s | %s -" % (f, table), lambda s: json.loads(s)["data"]["row_count"] == rows),
             "table < FILE": ("%s < %s" % (table, f), lambda s: json.loads(s)["data"]["row_count"] == rows),
             "cat | cat > /dev/null (floor)": ("cat %s | cat > /dev/null" % f, lambda s: True),
             "cat | wc -l (floor)": ("cat %s | wc -l" % f, lambda s: s.split()[0] == str(rows + 1))}
    if floor:
        # the spike of docs/readers.md section 3.4, built with the pinned compiler: `getchar` into 64 KiB chunks, counting bytes and newlines, keeping nothing
        # `getchar` is the floor the byte-at-a-time reader had; `bulk` (read_bytes, 64 KiB at a time) is the floor of the reader that is built
        for mode in ("getchar", "bulk"):
            shape["stdin_read %s < FILE (the floor)" % mode] = ("%s %s < %s" % (floor, mode, f), lambda s: s.split()[0] == str(data.stat().st_size) and s.split()[1] == str(rows + 1))
    if have["csvtk"]:
        shape["cat | csvtk -j 1 nrow"] = ("cat %s | csvtk -j 1 nrow" % f, lambda s: s.split()[-1] == str(rows))
    if have["duckdb"]:
        shape["cat | duckdb (default threads)"] = (duck("SELECT count(*) FROM t;"), lambda s: s.strip().split()[-1] == str(rows))
        shape["cat | duckdb (threads=1)"] = (duck("SELECT count(*) FROM t;", 1), lambda s: s.strip().split()[-1] == str(rows))
    out["shape: the row count"] = shape
    cut = {"table FILE": ("%s --root %s --select status,bytes --format csv %s" % (table, data.parent, data.name), lambda s: s.count("\n") == rows + 1),
           "cat | table -": ("cat %s | %s --select status,bytes --format csv -" % (f, table), lambda s: s.count("\n") == rows + 1)}
    if have["csvtk"]:
        cut["cat | csvtk -j 1 cut"] = ("cat %s | csvtk -j 1 cut -f status,bytes" % f, lambda s: s.count("\n") == rows + 1)
    out["cut: status,bytes as csv"] = cut
    filt = {"table FILE": ("%s --root %s --where 'status=404 and bytes:int>50000' --format csv %s" % (table, data.parent, data.name), kept),
            "cat | table -": ("cat %s | %s --where 'status=404 and bytes:int>50000' --format csv -" % (f, table), kept)}
    if have["csvtk"]:
        filt["cat | csvtk -j 1 filter2"] = ("cat %s | csvtk -j 1 filter2 -f '$status==404 && $bytes>50000'" % f, kept)
    if have["duckdb"]:
        filt["cat | duckdb (threads=1)"] = (duck("SELECT count(*) FROM t WHERE status=404 AND bytes>50000;", 1), lambda s: s.strip().split()[-1] == str(len(ids)))
    out["filter: status=404 and bytes>50000"] = filt

    def pairs(s):
        return bench.pairs(s, r"^(\d+),(\d+)$")

    freq = {"table FILE": ("%s --root %s --group status --format csv %s" % (table, data.parent, data.name), lambda s: pairs(s) == count),
            "cat | table -": ("cat %s | %s --group status --format csv -" % (f, table), lambda s: pairs(s) == count)}
    if have["csvtk"]:
        freq["cat | csvtk -j 1 freq"] = ("cat %s | csvtk -j 1 freq -f status" % f, lambda s: pairs(s) == count)
    if have["duckdb"]:
        freq["cat | duckdb (threads=1)"] = (duck("SELECT status, count(*) FROM t GROUP BY status ORDER BY status;", 1), lambda s: pairs(s) == count)
    out["group-count by status"] = freq
    return out


def once(cmd):
    t = time.perf_counter()
    p = subprocess.run(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t, p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    ap.add_argument("--only", default="")
    ap.add_argument("--floor", default="", help="the built scripts/spikes/stdin_read.cho (cancho build --std scripts/spikes/stdin_read.cho -o PATH): the getchar floor, and gate S5 of docs/readers.md")
    a = ap.parse_args()
    data = pathlib.Path(a.file).resolve()
    rows = a.rows
    if not data.exists() or data.stat().st_size == 0:
        data.parent.mkdir(parents=True, exist_ok=True)
        bench.generate(data, rows)
    size = data.stat().st_size
    plan = pipelines(os.path.abspath(a.bin), data, rows, bench.truth(data), os.path.abspath(a.floor) if a.floor else None)
    for title, contenders in plan.items():
        for name, (cmd, check) in contenders.items():
            p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
            if p.returncode != 0 or not check(p.stdout):
                sys.exit("%s did not give the answer (%s): %r %r" % (name, title, p.stdout[:200], p.stderr[:200]))
    load0 = os.getloadavg()
    print("file %s, %d bytes, %d rows; %s %s, %d cores; minimum of %d interleaved runs, output to /dev/null; load average before %s" % (
        data.name, size, rows, platform.system(), platform.machine(), os.cpu_count(), a.runs, "/".join("%.2f" % x for x in load0)))
    for tool in ("csvtk version", "duckdb --version"):
        p = subprocess.run(["sh", "-c", tool], capture_output=True, text=True)
        print("  " + ((p.stdout or p.stderr).strip().splitlines() or ["%s: not installed" % tool.split()[0]])[0])
    print()
    for title, contenders in plan.items():
        if a.only and not title.startswith(a.only):
            continue
        names = list(contenders)
        times = {n: [] for n in names}
        for r in range(a.runs):
            for n in names[r % len(names):] + names[:r % len(names)]:
                seconds, rc = once(contenders[n][0])
                if rc != 0:
                    sys.exit("%s exited %d" % (n, rc))
                times[n].append(seconds)
        print(title)
        print("%-34s %9s %9s %9s" % ("", "min s", "median s", "MB/s"))
        for n in names:
            lo = min(times[n])
            print("%-34s %9.4f %9.4f %9.0f" % (n, lo, statistics.median(times[n]), size / 1e6 / lo))
        for mode in ("getchar", "bulk"):
            floor_name = "stdin_read %s < FILE (the floor)" % mode
            if floor_name in times:
                print("S5 (bound 1.10): table < FILE / the %s floor = %.3f (minimum of %d); the file path, table FILE / the floor = %.3f" % (
                    mode, min(times["table < FILE"]) / min(times[floor_name]), a.runs, min(times["table FILE"]) / min(times[floor_name])))
        print()
    print("load average after %s" % "/".join("%.2f" % x for x in os.getloadavg()))


if __name__ == "__main__":
    main()
