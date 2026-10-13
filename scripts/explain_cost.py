#!/usr/bin/env python3
"""What `--explain` costs (docs/query.md section 15.3): the time of one call, the minimum of N and the median, on a file of 2 bytes' worth of header and on files up to 2 GB, beside the run
of the same plan on the file, and the instructions a call retires (macOS `/usr/bin/time -l`) where the machine counts them.

    python3 scripts/explain_cost.py [--table build/table] [--runs 21] [--files F ...] [--counter] [--no-run]

A call is `table --explain --agg count FILE`: the plan of a count of every row, whose run reads every row of the file. The sparse file (--sparse GB) is a header, one row and a hole: reading
it to the end would take seconds and gigabytes of zeros, so a time that does not grow with its size is the evidence that nothing but the header is read; its `bytes_read` is the document's own
count. The load average at the start and at the end is printed, with the machine; the numbers of a quoted run belong with those conditions.
"""
import argparse
import json
import os
import pathlib
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def one(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def instructions(argv):
    if sys.platform == "darwin":
        p = subprocess.run(["/usr/bin/time", "-l", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        m = re.search(r"(\d+)\s+instructions retired", p.stderr)
        return int(m.group(1)) if m else None
    p = subprocess.run(["perf", "stat", "-x,", "-e", "instructions:u", "--", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for line in p.stderr.splitlines():
        m = re.match(r"^(\d+),", line)
        if m:
            return int(m.group(1))
    return None


def cell(table, path, runs, with_run, counter):
    flags = ["--root", str(path.parent), "--agg", "count"]
    explain = [str(table), "--explain", *flags, path.name]
    times = []
    for _ in range(runs):
        t, p = one(explain)
        assert p.returncode == 0, p.stdout[:300]
        times.append(t)
    doc = json.loads(p.stdout)["data"]["explain"]
    row = {"file": path.name, "size": path.stat().st_size, "explain_min_ms": min(times) * 1000, "explain_median_ms": statistics.median(times) * 1000,
           "bytes_read": doc["source"]["bytes_read"], "document_bytes": len(p.stdout)}
    if counter:
        row["explain_instructions"] = min(filter(None, (instructions(explain) for _ in range(3))), default=None)
    if with_run:
        run = [str(table), *flags, "--format", "csv", path.name]
        if row["size"] < (1 << 20):
            # a file this small is the cost of the process: the minimum of as many runs as the explain had
            row["run_min_ms"] = min(one(run)[0] for _ in range(runs)) * 1000
            if counter:
                row["run_instructions"] = min(filter(None, (instructions(run) for _ in range(3))), default=None)
        else:
            t, p = one(run)
            row["run_ms_once"] = t * 1000
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=21)
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--sparse", type=float, default=2.0, help="GB of the sparse file (0: none)")
    ap.add_argument("--counter", action="store_true")
    ap.add_argument("--no-run", action="store_true", help="do not run the plan on the files for comparison")
    a = ap.parse_args()
    table = pathlib.Path(a.table).resolve()
    print("machine: %s %s, %d cores; load average at the start %s" % (platform.system(), platform.machine(), os.cpu_count(), " ".join("%.2f" % x for x in os.getloadavg())))
    rows = []
    with tempfile.TemporaryDirectory() as d:
        tiny = pathlib.Path(d) / "tiny.csv"
        tiny.write_text("id,status\n1,200\n")
        files = [tiny] + [pathlib.Path(f).resolve() for f in a.files]
        if a.sparse:
            big = pathlib.Path(d) / "sparse.csv"
            with open(big, "wb") as f:
                f.write(b"id,status\n1,200\n")
                f.truncate(int(a.sparse * (1 << 30)))
            files.append(big)
        for f in files:
            rows.append(cell(table, f, a.runs, not a.no_run and f.name != "sparse.csv", a.counter))
    for r in rows:
        print(json.dumps(r, sort_keys=True))
    print("load average at the end %s" % " ".join("%.2f" % x for x in os.getloadavg()))


if __name__ == "__main__":
    main()
