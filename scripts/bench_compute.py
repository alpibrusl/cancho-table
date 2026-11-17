#!/usr/bin/env python3
"""G-COST of docs/functions.md section 5: what a function of `--compute` costs a row, next to the arithmetic of the stage and `--select`, on one file, in one run.

    python3 scripts/bench_compute.py [--rows N] [--runs N] [--bin PATH] [--file PATH]

The file is seeded: id, price (a decimal of scale 2), qty (an integer, 0 for one row in twenty), name (short texts with a space, a mixed case), status (paid, open or void) and day (a date from 2000 to
2030). Every plan reads it from the page cache and writes CSV to /dev/null; the rounds are interleaved (every plan once per round, in a rotating order), the minimum of the runs is the figure
and the median is shown beside it. Nothing is tuned and nothing here is a claim about another machine: the load average and the core count are printed with the table, and the ratio to
`--select` and to a product is what to read, not the nanoseconds.
"""

import argparse
import datetime
import os
import pathlib
import platform
import random
import statistics
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

NAMES = ["Alice", " bob ", "Carol Ann", "dave", "ELEANOR", "frank o", "Grace", "heidi"]
STATUS = ["paid", "open", "void"]

# (group, name, the argv after the binary and before the file)
PLANS = [
    ("reference", "--select id,price,qty", ["--select", "id,price,qty"]),
    ("reference", "t=price:dec(2)*qty:int", ["--compute", "t=price:dec(2)*qty:int"]),
    ("reference", "h=price:dec(2)/qty:int@4 (qty 0 skipped by `if`)", ["--compute", "h=if(qty:int != 0, price:dec(2)/qty:int@4, 0)"]),
    ("numbers K5", "abs(price)", ["--compute", "a=abs(price:dec(2))"]),
    ("numbers K5", "sign(qty)", ["--compute", "a=sign(qty:int)"]),
    ("numbers K5", "greatest(price, qty)", ["--compute", "a=greatest(price:dec(2),qty:int)"]),
    ("numbers K5", "round(price*qty, 1)", ["--compute", "a=round(price:dec(2)*qty:int,1)"]),
    ("numbers K5", "floor(price, 0)", ["--compute", "a=floor(price:dec(2),0)"]),
    ("numbers K5", "coalesce(qty, 0)", ["--compute", "a=coalesce(qty:int,0)"]),
    ("texts K6", "upper(name)", ["--compute", "a=upper(name)"]),
    ("texts K6", "lower(name)", ["--compute", "a=lower(name)"]),
    ("texts K6", "trim(name)", ["--compute", "a=trim(name)"]),
    ("texts K6", "substr(name, 1, 3)", ["--compute", "a=substr(name,1,3)"]),
    ("texts K6", "concat(name, \"-\", status)", ["--compute", "a=concat(name,\"-\",status)"]),
    ("texts K6", "replace(name, \"a\", \"b\")", ["--compute", "a=replace(name,\"a\",\"b\")"]),
    ("texts K6", "len(name)", ["--compute", "a=len(name)"]),
    ("texts K6", "upper(trim(name)) (two calls)", ["--compute", "a=upper(trim(name))"]),
    ("conditions K7", "if(qty > 50, price, 0)", ["--compute", "a=if(qty:int > 50, price:dec(2), 0)"]),
    ("conditions K7", "if(status = \"paid\", 1, 0)", ["--compute", "a=if(status = \"paid\", 1, 0)"]),
    ("conditions K7", "if(qty > 50, \"big\", \"small\")", ["--compute", "a=if(qty:int > 50, \"big\", \"small\")"]),
    ("conditions K7", "if(status = \"paid\", name, \"n/a\")", ["--compute", "a=if(status = \"paid\", name, \"n/a\")"]),
    ("dates K8", "year(day)", ["--compute", "a=year(day:date)"]),
    ("dates K8", "weekday(day)", ["--compute", "a=weekday(day:date)"]),
    ("dates K8", "days_between(day, \"2026-01-01\")", ["--compute", "a=days_between(day:date,\"2026-01-01\")"]),
    ("dates K8", "add_days(day, 30)", ["--compute", "a=add_days(day:date,30)"]),
]


def generate(path, rows):
    random.seed(7)
    first = datetime.date(2000, 1, 1).toordinal()
    span = (datetime.date(2030, 12, 31).toordinal() - first)
    with open(path, "w", newline="") as f:
        f.write("id,price,qty,name,status,day\n")
        for i in range(rows):
            qty = 0 if random.random() < 0.05 else random.randint(1, 100)
            f.write("%d,%d.%02d,%d,%s,%s,%s\n" % (i, random.randint(0, 999), random.randint(0, 99), qty, random.choice(NAMES), random.choice(STATUS),
                                                  datetime.date.fromordinal(first + random.randint(0, span)).isoformat()))


def run_once(binary, argv, path):
    with open(os.devnull, "wb") as out:
        t = time.perf_counter()
        r = subprocess.run([binary, *argv, "--format", "csv", "--limit", "1000000", "--max-bytes", "67108864", str(path)], stdout=out, stderr=subprocess.PIPE)
        dt = time.perf_counter() - t
    if r.returncode != 0:
        sys.exit("a plan failed: %s\n%s" % (argv, r.stderr.decode(errors="replace")[:400]))
    return dt


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows", type=int, default=1000000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--file", default=str(ROOT / "build" / "bench_compute.csv"))
    a = ap.parse_args()
    path = pathlib.Path(a.file)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or sum(1 for _ in open(path)) != a.rows + 1:
        generate(path, a.rows)
    for _g, _n, argv in PLANS:           # every plan answers once before any timing (a refusal is an error of this script)
        run_once(a.bin, argv, path)
    times = {i: [] for i in range(len(PLANS))}
    for rnd in range(a.runs):
        order = list(range(len(PLANS)))
        order = order[rnd % len(order):] + order[:rnd % len(order)]
        for i in order:
            times[i].append(run_once(a.bin, PLANS[i][2], path))
    print("%d rows, %d bytes; %s, %d cores, load %s; minimum of %d interleaved runs, the median beside it; stdout /dev/null, --format csv" % (
        a.rows, path.stat().st_size, platform.platform(), os.cpu_count(), " ".join("%.2f" % x for x in os.getloadavg()), a.runs))
    base = min(times[0])
    prod = min(times[1])
    print()
    print("| group | plan | wall (min) | median | per row | vs `--select` | vs the product |")
    print("|---|---|---|---|---|---|---|")
    for i, (g, n, _) in enumerate(PLANS):
        m = min(times[i])
        print("| %s | `%s` | %.3f s | %.3f s | %d ns | %.1fx | %.2fx |" % (g, n, m, statistics.median(times[i]), round(m / a.rows * 1e9), m / base, m / prod))


if __name__ == "__main__":
    main()
