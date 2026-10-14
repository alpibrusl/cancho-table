#!/usr/bin/env python3
"""Typed group keys as JSON numbers (docs/readers.md N32): the cost of `--format jsonl` of a grouping with a typed key, this build against another (`main`'s), on 1,000,000 rows.

    python3 scripts/bench_jsonl_keys.py DIR --old OLD_BIN --new NEW_BIN [--reps N] [--rows N] [--cpus 0-5] [--check]

DIR gets keys.csv (1,000,000 rows by default: `id` unique, `grp` 1,000 integer values, `price` 2-decimal values with 5,000 distinct, `ratio` 17-digit floats with 20,000 distinct, `status`
text, `bytes`). The cells are the ones that write many groups (a write of 1,000,000 groups is where a branch in the group writer would show) and the ones that write few (where it cannot):

    id:int (1M groups), price:dec(2) (5,000), ratio:float (20,000), grp:int (1,000), status (text key, 6 groups), and the same plans as `--format csv`.

Each cell is run with both binaries in rotation (every other round reversed), `--reps` times, output to /dev/null; the minimum wall time of each is printed with the load average before and
after, and the ratio new/old. With `--cpus` the processes are pinned (`taskset -c`, Linux; ignored where there is no taskset). `--check` first compares the two outputs of every `csv` cell
(byte-identical, the path that must not change) and of the untyped-key jsonl cell, and parses the new binary's jsonl cells strictly (typed keys are numbers; the old binary's are strings).
Nothing here is a claim: it prints the numbers and the conditions.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time


def make(path, rows):
    rng = random.Random(7)
    prices = ["%d.%02d" % (rng.randint(0, 9999), rng.randint(0, 99)) for _ in range(5000)]
    ratios = ["%.16g" % (rng.random() * 10 ** rng.randint(-3, 9)) for _ in range(20000)]
    with open(path, "w", newline="") as f:
        f.write("id,grp,price,ratio,status,bytes\n")
        for i in range(rows):
            f.write("%d,%d,%s,%s,%d,%d\n" % (i, rng.randint(0, 999), rng.choice(prices), rng.choice(ratios), rng.choice([200, 200, 200, 301, 404, 500]), rng.randint(0, 99999)))


BIG = ["--max-groups", "1000000", "--max-state-bytes", "1073741824"]
CELLS = [
    ("id:int, 1M groups", ["--group", "id:int", "--agg", "count,sum:bytes"] + BIG),
    ("price:dec(2), 5,000 groups", ["--group", "price:dec(2)", "--agg", "count,sum:bytes"]),
    ("ratio:float, 20,000 groups", ["--group", "ratio:float", "--agg", "count"]),
    ("grp:int, 1,000 groups", ["--group", "grp:int", "--agg", "count,sum:bytes"]),
    ("id (text key), 1M groups", ["--group", "id", "--agg", "count,sum:bytes"] + BIG),
    ("status (text key), 6 groups", ["--group", "status", "--agg", "count,sum:bytes"]),
]


def argv(binary, path, args, fmt, cpus):
    a = [binary, "--root", os.path.dirname(path), *args, "--format", fmt, os.path.basename(path)]
    if cpus and shutil.which("taskset"):
        return ["taskset", "-c", cpus] + a
    return a


def timed(a):
    t = time.perf_counter()
    subprocess.run(a, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return time.perf_counter() - t


def out(a):
    return subprocess.run(a, capture_output=True, check=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--reps", type=int, default=9)
    ap.add_argument("--rows", type=int, default=1000000)
    ap.add_argument("--cpus", default="")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    path = os.path.abspath(os.path.join(a.dir, "keys.csv"))
    if not os.path.exists(path) or sum(1 for _ in open(path, "rb")) != a.rows + 1:
        make(path, a.rows)
    old, new = os.path.abspath(a.old), os.path.abspath(a.new)
    if a.check:
        for name, args in CELLS:
            o, n = out(argv(old, path, args, "csv", a.cpus)), out(argv(new, path, args, "csv", a.cpus))
            if o != n:
                sys.exit("the csv of %s differs between the builds" % name)
            typed = any(":" in x for x in args[1:2])
            on, nn = out(argv(old, path, args, "jsonl", a.cpus)), out(argv(new, path, args, "jsonl", a.cpus))
            if not typed and on != nn:
                sys.exit("the jsonl of the untyped cell %s differs between the builds" % name)
            lines = nn.split(b"\n")[:-1]
            key = args[1].split(",")[0].split(":")[0]
            for line in lines:
                json.loads(line, parse_constant=lambda c: sys.exit("constant " + c))
            first = lines[0]
            quoted = first.startswith(b'{"%s":"' % key.encode())
            if typed and quoted:
                sys.exit("the new binary writes a string for the typed key of %s: %r" % (name, first[:80]))
            if not typed and not quoted:
                sys.exit("the new binary does not write a string for the text key of %s: %r" % (name, first[:80]))
            print("checked", name, len(lines), "lines; csv equal", "; jsonl equal" if not typed else "; typed keys are numbers")
    rows = []
    for fmt in ("jsonl", "csv"):
        for name, args in CELLS:
            ts = {"old": [], "new": []}
            for b, _ in ((old, "old"), (new, "new")):
                timed(argv(b, path, args, fmt, a.cpus))      # a warm-up of each
            for r in range(a.reps):
                order = [("old", old), ("new", new)]
                if r % 2:
                    order.reverse()
                for tag, b in order:
                    ts[tag].append(timed(argv(b, path, args, fmt, a.cpus)))
            rows.append((fmt, name, min(ts["old"]), min(ts["new"]), sorted(ts["old"])[len(ts["old"]) // 2], sorted(ts["new"])[len(ts["new"]) // 2]))
    la = os.getloadavg()
    print("machine %s, rows %d, reps %d, cpus %r, load average at the end %.2f %.2f %.2f" % (os.uname().sysname + " " + os.uname().machine, a.rows, a.reps, a.cpus, *la))
    print("%-6s %-30s %9s %9s %7s %9s %9s %7s" % ("format", "cell", "old min", "new min", "ratio", "old med", "new med", "ratio"))
    for fmt, name, o, n, om, nm in rows:
        print("%-6s %-30s %9.4f %9.4f %7.3f %9.4f %9.4f %7.3f" % (fmt, name, o, n, n / o, om, nm, nm / om))


if __name__ == "__main__":
    main()
