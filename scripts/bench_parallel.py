#!/usr/bin/env python3
"""The scaling of `table --threads N`, against DuckDB (`SET threads=N`) and csvtk (`-j N`), on
the 1,000,000-row, 31,667,311-byte file of scripts/bench.py.

    python3 scripts/bench_parallel.py [--runs 5] [--threads 1,2,4,8,16] [--file PATH] [--only NAME]
    python3 scripts/bench_parallel.py --threshold      # where several threads start to pay

Four questions: a filter (`status=404 and bytes>50000`, all columns, as csv), the cut of two
columns, the count by status and the sum of `bytes` by status. Every contender's answer is
checked against one computed here, in Python, before anything is timed. The minimum of the
runs, interleaved (every contender once per round, the order rotating), with output to
/dev/null and the peak resident set of one more run. DuckDB is run the way one runs it
(`read_csv` with its type detection, a `COPY ... TO '/dev/null' (FORMAT csv)`), its time is the whole process's, as
`table`'s is, and `SET threads=N` is its way of saying what `--threads N` says; "default" is
its own choice (one per core).

Nothing is tuned: the numbers are what the command lines give.
"""

import argparse
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

SQL = {
    "filter": "SELECT * FROM read_csv('%s') WHERE status = 404 AND bytes > 50000",
    "cut": "SELECT status, bytes FROM read_csv('%s')",
    "group-count": "SELECT status, count(*) AS count FROM read_csv('%s') GROUP BY status",
    "group-sum": "SELECT status, sum(bytes) AS total FROM read_csv('%s') GROUP BY status",
}


def duck(question, data, threads, sink):
    prefix = "" if threads is None else "SET threads=%d; " % threads
    return ["duckdb", "-c", "%sCOPY (%s) TO '%s' (FORMAT csv)" % (prefix, SQL[question] % data, sink)]


def table_argv(table, root, name, question, threads):
    plans = {
        "filter": ["--where", "status=404 and bytes:int>50000", "--format", "csv"],
        "cut": ["--select", "status,bytes", "--format", "csv"],
        "group-count": ["--group", "status", "--format", "csv"],
        "group-sum": ["--group", "status", "--agg", "sum:bytes", "--format", "csv"],
    }
    return [table, "--root", root, *plans[question], "--threads", str(threads), name]


def csvtk_argv(question, data, jobs):
    j = ["csvtk", "-j", str(jobs)]
    if question == "filter":
        return ["sh", "-c", "csvtk -j %d filter -f 'bytes>50000' %s | csvtk -j %d grep -f status -p 404" % (jobs, data, jobs)]
    return {
        "cut": [*j, "cut", "-f", "status,bytes", data],
        "group-count": [*j, "freq", "-f", "status", data],
        "group-sum": [*j, "summary", "-g", "status", "-f", "bytes:sum", data],
    }[question]


def checker(question, rows, expected):
    ids, count, total = expected
    if question == "filter":
        def check(s):
            lines = s.splitlines()
            return lines[0].startswith("id,status") and [l.split(",", 1)[0] for l in lines[1:]] == ids
        return check
    if question == "cut":
        return lambda s: s.count("\n") == rows + 1
    pattern = r"^(\d+),(\d+)(?:\.0+)?$"
    want = count if question == "group-count" else total
    return lambda s: bench.pairs(s, pattern) == want


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--threads", default="1,2,4,8,16")
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    ap.add_argument("--only", default="")
    ap.add_argument("--threshold", action="store_true")
    args = ap.parse_args()
    data = pathlib.Path(args.file).resolve()
    if not data.exists() or data.stat().st_size == 0:
        data.parent.mkdir(parents=True, exist_ok=True)
        bench.generate(data, 1_000_000)
    table = os.path.abspath(args.bin)
    if args.threshold:
        return threshold(table, args)
    counts = [int(x) for x in args.threads.split(",")]
    expected = bench.truth(data)
    have_duck = shutil.which("duckdb") is not None
    have_csvtk = shutil.which("csvtk") is not None
    print("file: %s, %d bytes, 1000000 rows; %s %s, %s cores; minimum of %d interleaved runs, output to /dev/null" % (
        data.name, data.stat().st_size, platform.system(), platform.machine(), os.cpu_count(), args.runs))
    print("duckdb: %s; csvtk: %s" % (bench.version(["duckdb", "--version"]) if have_duck else "not installed", bench.version(["csvtk", "version"]) if have_csvtk else "not installed"))
    print()
    for question in SQL:
        if not question.startswith(args.only):
            continue
        contenders = {}
        for n in counts:
            contenders["table -t%d" % n] = (table_argv(table, str(data.parent), data.name, question, n), None)
        if have_duck:
            for n in counts:
                contenders["duckdb -t%d" % n] = (duck(question, str(data), n, "/dev/null"), duck(question, str(data), n, "/dev/stdout"))
            contenders["duckdb default"] = (duck(question, str(data), None, "/dev/null"), duck(question, str(data), None, "/dev/stdout"))
        if have_csvtk:
            for n in counts:
                contenders["csvtk -j%d" % n] = (csvtk_argv(question, str(data), n), None)
        check = checker(question, 1_000_000, expected)
        for name, (argv, check_argv) in contenders.items():
            p = subprocess.run(check_argv or argv, capture_output=True, text=True)
            if p.returncode != 0 or not check(p.stdout):
                sys.exit("%s did not give the answer (%s): %r %r" % (name, question, p.stdout[:200], p.stderr[:200]))
        names = list(contenders)
        times = {n: [] for n in names}
        for r in range(args.runs):
            order = names[r % len(names):] + names[:r % len(names)]
            for n in order:
                seconds, rc = bench.once(contenders[n][0])
                if rc != 0:
                    sys.exit("%s exited %d" % (n, rc))
                times[n].append(seconds)
        peaks = {n: bench.rss(contenders[n][0]) for n in names}
        print(question)
        print("%-16s %9s %9s %8s %12s" % ("contender", "min s", "median s", "vs 1", "peak RSS MB"))
        for n in names:
            lo = min(times[n])
            family = n.split(" ")[0]
            base = min(times[family + (" -t1" if family != "csvtk" else " -j1")]) if (family + (" -t1" if family != "csvtk" else " -j1")) in times else None
            print("%-16s %9.4f %9.4f %7s %12s" % (n, lo, statistics.median(times[n]), "%.2fx" % (base / lo) if base else "", "%.1f" % (peaks[n] / 1024) if peaks[n] else "n/a"))
        print()


def threshold(table, args):
    """Where does a thread start to pay? The same file cut to sizes from 256 KiB to 32 MiB, `table` with one
    thread and with several (`--parallel-min-bytes 0`, so that the size is not what decides)."""
    rows_per_mb = 31_667_311 / 1_000_000
    print("size MB   seq s   t2 s    t4 s    t8 s    (min of %d, interleaved)" % args.runs)
    with tempfile.TemporaryDirectory() as d:
        source = pathlib.Path(args.file)
        text = source.read_bytes()
        for mb in (0.25, 0.5, 1, 2, 4, 8, 16):
            cut = int(mb * 1024 * 1024)
            body = text[:cut]
            body = body[:body.rfind(b"\n") + 1]
            (pathlib.Path(d) / "t.csv").write_bytes(body)
            variants = {"seq": [], "t2": ["--threads", "2"], "t4": ["--threads", "4"], "t8": ["--threads", "8"]}
            times = {k: [] for k in variants}
            for r in range(args.runs * 3):
                names = list(variants)
                for k in names[r % 4:] + names[:r % 4]:
                    argv = [table, "--root", d, "--group", "status", "--agg", "sum:bytes", *variants[k], "--chunk-bytes", str(max(65536, cut // 8)) if k != "seq" else "4194304", "--parallel-min-bytes", "0", "t.csv"]
                    seconds, rc = bench.once(argv)
                    times[k].append(seconds)
            print("%7.2f  %7.4f %7.4f %7.4f %7.4f" % (mb, *[min(times[k]) for k in variants]))


if __name__ == "__main__":
    main()
