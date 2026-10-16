#!/usr/bin/env python3
"""The cost of reading one cell as a date, a timestamp or a datetime (docs/datetime.md section 11 estimated it; G-NEW-SPEED reports it), in the manner of scripts/cellcost.py: `table` only,
one thread, the same plan asked with the column as text and as the type, the difference over the rows being the cost of the typed read.

    python3 scripts/bench_datetime.py [--bin build/table] [--rows 1000000] [--runs 5] [--file build/dt/data.csv]

A seeded file of id,status,bytes,price,ts,ts2,d,w: `ts` an instant in UTC written `2024-03-05T01:02:03Z`, `ts2` an instant with a mixed offset and a fraction `2024-03-05T01:02:03.250000+02:00`,
`d` a day, `w` a wall-clock time `2024-03-05 01:02:03`; `bytes` an integer and `price` a decimal as scripts/bench_numbers.py has them. Every cell is valid, and the file is the same on every machine.

For each question the output is the minimum over the runs of the time (seconds, `time.perf_counter`) and, where the machine can count them (macOS `/usr/bin/time -l`, Linux `perf stat -e
instructions:u`), the instructions retired, per row, and the **difference between the typed question and the text question** per row: that difference is the figure the doc estimates ("about 10 to
30 ns above the line scan, 100 to 400 more instructions than a tried :int condition"). The instruction count does not depend on the load of the machine; the time does, so a run on a loaded machine
says so (the load average at the start and the end) and is not a figure to publish.
"""
import argparse
import os
import pathlib
import random
import re
import shutil
import sys
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

QUESTIONS = [
    # (label, flags with the column as text, flags with the column as the type)
    ("int    bytes >= 50000", ["--where", "bytes >= '50000'", "--agg", "count"], ["--where", "bytes:int >= 50000", "--agg", "count"]),
    ("dec(2) price >= 500", ["--where", "price >= '500.00'", "--agg", "count"], ["--where", "price:dec(2) >= 500", "--agg", "count"]),
    ("timestamp (UTC, whole seconds) >= a literal", ["--where", "ts >= '2024-06-01T00:00:00Z'", "--agg", "count"], ["--where", "ts:timestamp >= '2024-06-01T00:00:00Z'", "--agg", "count"]),
    ("timestamp (+02:00, 6 digits) >= a literal", ["--where", "ts2 >= '2024-06-01T00:00:00Z'", "--agg", "count"], ["--where", "ts2:timestamp >= '2024-06-01T00:00:00Z'", "--agg", "count"]),
    ("date >= a literal", ["--where", "d >= '2024-06-01'", "--agg", "count"], ["--where", "d:date >= '2024-06-01'", "--agg", "count"]),
    ("datetime >= a literal", ["--where", "w >= '2024-06-01 00:00:00'", "--agg", "count"], ["--where", "w:datetime >= '2024-06-01 00:00:00'", "--agg", "count"]),
    ("max of a timestamp", ["--agg", "distinct:ts"], ["--agg", "max:ts:timestamp"]),
    ("group by a date (about 1,500 groups)", ["--group", "d"], ["--group", "d:date"]),
    ("order by a timestamp, top 100", ["--order-by", "ts", "--select", "id", "--top", "100"], ["--order-by", "ts:timestamp", "--select", "id", "--top", "100"]),
]


def generate(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    r = random.Random(1)
    base = 1704067200  # 2024-01-01T00:00:00Z
    import datetime as dt
    with open(path, "w", newline="") as f:
        f.write("id,status,bytes,price,ts,ts2,d,w\n")
        for i in range(rows):
            sec = base + r.randrange(0, 400 * 86400)
            t = dt.datetime.fromtimestamp(sec, dt.timezone.utc)
            off = r.choice([2, 1, 0, -5, 9])
            local = t + dt.timedelta(hours=off)
            frac = r.randrange(0, 1000000)
            f.write("%d,%d,%d,%d.%02d,%s,%s.%06d%s%02d:00,%s,%s\n" % (
                i, r.choice([200, 200, 200, 301, 404, 500]), r.randrange(0, 100000), r.randrange(0, 100000), r.randrange(0, 100),
                t.strftime("%Y-%m-%dT%H:%M:%SZ"), local.strftime("%Y-%m-%dT%H:%M:%S"), frac, "+" if off >= 0 else "-", abs(off),
                t.strftime("%Y-%m-%d"), t.strftime("%Y-%m-%d %H:%M:%S")))


def counted(argv):
    """(instructions retired, return code), 0 when the machine cannot count them."""
    if sys.platform == "darwin":
        p = subprocess.run(["/usr/bin/time", "-l", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        m = re.search(r"(\d+)\s+instructions retired", p.stderr)
        return (int(m.group(1)) if m else 0), p.returncode
    if shutil.which("perf"):
        p = subprocess.run(["perf", "stat", "-x,", "-e", "instructions:u", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        total = 0
        for line in p.stderr.splitlines():
            fields = line.split(",")
            if len(fields) > 2 and fields[0].isdigit():
                total += int(fields[0])
        return total, p.returncode
    return 0, 0


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t, p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--rows", type=int, default=1000000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--file", default=str(ROOT / "build" / "dt" / "data.csv"))
    ap.add_argument("--no-count", action="store_true", help="time only")
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    if not data.exists():
        generate(data, a.rows)
    rows = sum(1 for _ in open(data, "rb")) - 1
    big = ["--max-groups", "1000000", "--max-state-bytes", "1073741824"]
    load0 = os.getloadavg()[0]
    print("file %s, %d rows, %d runs, minimum; load average %.1f at the start" % (data, rows, a.runs, load0))
    print("%-48s %10s %10s %9s %9s %12s" % ("question (text, then typed)", "text s", "typed s", "ns/row +", "instr/row", "instr/row +"))
    for label, text_flags, typed_flags in QUESTIONS:
        result = []
        for flags in (text_flags, typed_flags):
            argv = [a.bin, "--root", str(data.parent), *flags, *big, "--format", "csv", data.name]
            best = 1e9
            for _ in range(a.runs):
                s, rc = timed(argv)
                if rc != 0:
                    print("!! %s exited %d" % (label, rc))
                    return 1
                best = min(best, s)
            n = 0 if a.no_count else counted(argv)[0]
            result.append((best, n))
        (t0, n0), (t1, n1) = result
        print("%-48s %10.4f %10.4f %9.1f %9.1f %12.1f" % (label, t0, t1, (t1 - t0) / rows * 1e9, n1 / rows if n1 else 0, (n1 - n0) / rows if n1 else 0))
    print("load average %.1f at the end" % os.getloadavg()[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
