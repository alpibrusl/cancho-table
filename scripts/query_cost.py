#!/usr/bin/env python3
"""What translating a query costs (docs/query.md section 14, "Measured"): the front end of tools/table in a loop of its own, scripts/spikes/query/qbench.cho.

    python3 scripts/query_cost.py [--rounds 200000] [--runs 9] [--cpus 0-5]

Builds qbench (the compiler on PATH, or $CANCHO) from the sources of this checkout and runs it as `qbench QUERY ROUNDS`, for several queries, from a few bytes to the largest the
grammar admits (4,096). The cost of one translation is (the time of ROUNDS rounds less the time of 0 rounds) over ROUNDS, each the **minimum of --runs runs** (the work is
deterministic and the noise is only ever added, so the minimum is the estimate with the least variance; the median and the spread are printed beside it). The load of the machine
when it ran is printed with the result, because it is a condition of the number. Nothing here measures a run of `table`: that cost is this plus the existing parsers of the flags it
writes, and the instruction-count gate (scripts/gate_regress.py --counter) is what holds the flags path.

On Linux `--cpus` pins the runs (taskset) and they are run nice. The process start (loading, the heap) is the 0-round run and is subtracted.
"""
import argparse
import os
import pathlib
import platform
import statistics
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

QUERIES = {
    "select a": "select a",
    "5 columns, a condition": "select id, customer, status, bytes, price from 'orders.csv' where status = 200",
    "grouping with a sort (docs/query.md example 4)": "select customer, count(*), sum(bytes) from 'orders.csv' where bytes != '' group by customer order by sum(bytes) desc limit 2",
    "typed, 3 conditions, an in list": "select id from 'orders.csv' where status in (200, 404, 500) and bytes::int >= 1000 and customer contains 'Doe' order by bytes::int desc, id limit 100 offset 20",
}


def longest():
    """A query of 4,096 bytes that is whole: an in list of as many values as fit (at most 1,024), padded with spaces."""
    head = "select id from 'orders.csv' where id in ("
    values = []
    while len(values) < 1024 and len(head) + len(",".join(map(str, values + [len(values)]))) + 1 <= 4096:
        values.append(len(values))
    q = head + ",".join(map(str, values)) + ")"
    return q + " " * (4096 - len(q))


def build(out):
    cancho = os.environ.get("CANCHO", "cancho")
    src = ROOT / "tools" / "table"
    cmd = [cancho, "build", str(ROOT / "scripts" / "spikes" / "query" / "qbench.cho")] + [str(src / f) for f in ("qarena.cho", "qlex.cho", "qparse.cho", "qtrans.cho")]
    cmd += ["--std", "-o", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("cannot build qbench: " + r.stderr[-400:])


def timed(argv, prefix):
    t = time.perf_counter()
    p = subprocess.run(prefix + argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=200000)
    ap.add_argument("--runs", type=int, default=9)
    ap.add_argument("--cpus", default="")
    a = ap.parse_args()
    prefix = []
    if platform.system() == "Linux":
        prefix = ["nice", "-n", "10"] + (["taskset", "-c", a.cpus] if a.cpus else [])
    with tempfile.TemporaryDirectory() as d:
        exe = pathlib.Path(d) / "qbench"
        build(exe)
        print("machine: %s %s, %d cpus; load average before: %s; rounds %d, min of %d runs" % (platform.system(), platform.machine(), os.cpu_count(), " ".join("%.2f" % x for x in os.getloadavg()), a.rounds, a.runs))
        print("%-52s %6s %12s %12s %9s" % ("query", "bytes", "ns/parse min", "ns/parse med", "spread"))
        cases = dict(QUERIES)
        cases["the longest the grammar admits (4,096 bytes)"] = longest()
        for name, q in cases.items():
            checks = set()
            base, full = [], []
            for _ in range(a.runs):
                t0, o0 = timed([str(exe), q, "0"], prefix)
                t1, o1 = timed([str(exe), q, str(a.rounds)], prefix)
                base.append(t0)
                full.append(t1)
                checks.add(o1)
            # the checksum is the same on every run, and not that of a refusal (a refusal is cheaper)
            assert len(checks) == 1, checks
            per = [(f - min(base)) / a.rounds * 1e9 for f in full]
            print("%-52s %6d %12.0f %12.0f %8.1f%%" % (name, len(q.encode()), min(per), statistics.median(per), (max(per) - min(per)) / min(per) * 100))
        print("load average after: %s" % " ".join("%.2f" % x for x in os.getloadavg()))


if __name__ == "__main__":
    main()
