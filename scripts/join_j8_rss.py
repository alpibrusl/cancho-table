#!/usr/bin/env python3
"""J8 (docs/joins.md section 24): what the partitioned build holds, measured: the peak resident set of a join over a build file, at `--threads 1` (the sequential build) and at 2, 4 and 8 threads (the partitioned
build, `--parallel-min-bytes 0`), for the same call, with the accounted bytes by the formula of section 6.2 (over the pruned records the plan holds) beside them.

    python3 scripts/join_j8_rss.py [--bin build/table] [--runs 5] [--shapes ROWSxCOLS,...] [--threads 2,4,8] [--json out.json]

For each shape a build file `bk,bg,c0..` of ROWS rows and COLS columns in all (cells of 10 bytes, unique keys in random order, seed 7) and a probe of 5 rows are made; the call is
`table --root D --join b.csv --on pk=bk --select pk,bg,c0 --format csv --max-state-bytes 1073741824 [--threads T --parallel-min-bytes 0] p.csv`. For each thread count:

* the answer is checked to be the bytes of the sequential call (a partitioned build that gave other bytes would be reported and stop the run);
* the build is checked to have been done by the threads and not declined (`strace -f`: one thread for each range and each partition), so that the figure is the partitioned build's;
* the peak resident set the kernel reports for the process (`wait4` of a small launcher, tests/conformance/maxrss.c), the minimum of `--runs` runs, in bytes.

Nothing is claimed of a time. The conditions (machine, cores, load average before and after) are printed with the table.
"""
import argparse
import json
import os
import pathlib
import platform
import random
import re
import sys
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
import join_ref7 as R7  # noqa: E402


def launcher():
    out = os.path.join(tempfile.mkdtemp(prefix="maxrss-"), "maxrss")
    subprocess.run(["cc", "-O1", "-o", out, str(ROOT / "tests" / "conformance" / "maxrss.c")], check=True)
    return out


def peak(helper, binary, args):
    p = subprocess.run([helper, binary, *[str(a) for a in args]], capture_output=True, text=True, check=True)
    rss, status = p.stdout.split()
    return int(rss) * (1 if sys.platform == "darwin" else 1024), int(status)


def make(d, rows, cols):
    rng = random.Random(7)
    names = ["bk", "bg"] + ["c%d" % i for i in range(cols - 2)]
    keys = list(range(rows))
    rng.shuffle(keys)
    total = 0
    seen = set()
    keep = {1, 2} if cols > 2 else {1}            # the columns the plan names (bg, c0): the build holds the others empty (docs/joins.md section 22)
    with open(d / "b.csv", "w") as f:
        f.write(",".join(names) + "\n")
        for k in keys:
            row = ["k%d" % k, rng.choice("xyz")] + ["".join(rng.choice("abcdefghij") for _ in range(10)) for _ in range(cols - 2)]
            f.write(",".join(row) + "\n")
            total += R7.record_len(row, keep) + 16
            if row[0] not in seen:
                seen.add(row[0])
                total += len(row[0]) + 60
    with open(d / "p.csv", "w") as f:
        f.write("pk,pg\n")
        for i in range(5):
            f.write("k%d,%s\n" % (keys[i * 7 % rows], "xyz"[i % 3]))
    return names, total


def call(d, extra):
    bound = [] if "--max-state-bytes" in extra else ["--max-state-bytes", 1073741824]
    return ["--root", d, "--join", "b.csv", "--on", "pk=bk", "--format", "csv", *bound, *extra, "p.csv"]


def threads_started(binary, args):
    p = subprocess.run(["strace", "-f", "-e", "trace=clone,clone3", binary, *[str(a) for a in args]], capture_output=True, input=b"")
    return len(re.findall(r"Process \d+ attached", p.stderr.decode("latin-1")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--shapes", default="100000x12,300000x8,1000000x6")
    ap.add_argument("--threads", default="2,4,8")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    helper = launcher()
    load0 = os.getloadavg()
    cells = []
    bad = False
    for shape in a.shapes.split(","):
        rows, cols = (int(x) for x in shape.split("x"))
        with tempfile.TemporaryDirectory(prefix="j8rss-") as t:
            d = pathlib.Path(t)
            names, accounted = make(d, rows, cols)
            size = os.path.getsize(d / "b.csv")
            sel = ["--select", "pk,bg,c0" if cols > 2 else "pk,bg"]
            base = subprocess.run([a.bin, *[str(x) for x in call(d, sel + ["--threads", 1])]], capture_output=True, input=b"")
            def status_at(bound, extra):
                return subprocess.run([a.bin, *[str(x) for x in call(d, sel + ["--max-state-bytes", bound] + extra)]], capture_output=True, input=b"").returncode

            # the formula is the program's count: accepted at N and refused at N - 1, with one thread and with four (the bound is the same byte)
            holds = [status_at(accounted, ["--threads", t, "--parallel-min-bytes", 0]) == 0 and status_at(accounted - 1, ["--threads", t, "--parallel-min-bytes", 0]) == 8 for t in (1, 4)]
            bad |= not all(holds)
            row = {"shape": shape, "file_bytes": size, "accounted": accounted, "formula_holds_1_and_4_threads": holds, "rss_threads_1": min(peak(helper, a.bin, call(d, sel + ["--threads", 1]))[0] for _ in range(a.runs))}
            for tcount in (int(x) for x in a.threads.split(",")):
                extra = sel + ["--threads", tcount, "--parallel-min-bytes", 0]
                got = subprocess.run([a.bin, *[str(x) for x in call(d, extra)]], capture_output=True, input=b"")
                same = (got.returncode, got.stdout) == (base.returncode, base.stdout)
                data = size - len(",".join(names)) - 1
                rb = min(4194304, -(-data // tcount))
                rb = max(rb, -(-data // 16384))
                want = -(-data // rb) + tcount
                started = threads_started(a.bin, call(d, extra))
                row["rss_threads_%d" % tcount] = min(peak(helper, a.bin, call(d, extra))[0] for _ in range(a.runs))
                row["same_bytes_%d" % tcount] = same
                row["threads_started_%d" % tcount] = started
                row["threads_expected_%d" % tcount] = want
                bad |= not same or started != want
            cells.append(row)
            print(json.dumps(row), flush=True)
    load1 = os.getloadavg()
    cond = {"machine": platform.platform(), "cores": os.cpu_count(), "load_before": load0, "load_after": load1, "runs": a.runs}
    print(json.dumps(cond))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"conditions": cond, "cells": cells}, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
