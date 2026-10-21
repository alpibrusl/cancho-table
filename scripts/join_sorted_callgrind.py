#!/usr/bin/env python3
"""J9 (docs/joins.md section 23): instruction counts per row, by callgrind, of the merge join against the hash join on the same sorted files (report only: a count of instructions is not a time, and nothing here is
a claim that one is faster).

    python3 scripts/join_sorted_callgrind.py [--bin build/table] [--small 20000] [--large 60000] [--json out.json]

A probe of N rows and a build of N / 4 rows, both sorted by the key (text, or `:int`), a key repeated 4 times in the build and a probe row in five with a key the build lacks. A run's count is a start-up cost
plus a cost per row, so each call is counted on a prefix of `--small` rows and on one of `--large`, and the cost of a row is the DIFFERENCE of the two counts over the difference of the rows (as
scripts/join_callgrind.py does); the build grows with the probe, so the figure is the cost of a probe row and the build rows that come with it.
"""
import argparse
import json
import pathlib
import random
import re
import sys
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "conformance"))


def ir(argv):
    p = subprocess.run(["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    m = re.search(r"Collected\s*:\s*(\d+)", p.stderr)
    if not m:
        sys.exit("callgrind gave no count: %s" % p.stderr[-400:])
    if p.returncode:
        sys.exit("a run failed: %s" % (argv,))
    return int(m.group(1))


def gen(d, n, kind):
    rng = random.Random(7)
    nb = n // 4
    span = nb // 4 + 1                                   # the distinct keys of the build: a key repeats about 4 times
    key = (lambda k: str(k)) if kind == "int" else (lambda k: "u%08d" % k)   # (zero padded, so the numeric order is the text order too)
    bkeys = sorted(rng.randrange(span) for _ in range(nb))
    (d / ("b-%d.csv" % n)).write_text("k,name\n" + "".join("%s,n%d\n" % (key(k), i) for i, k in enumerate(bkeys)))
    pkeys = sorted(rng.randrange(span) if rng.random() >= 0.2 else span + rng.randrange(nb) for _ in range(n))
    (d / ("p-%d.csv" % n)).write_text("id,k,v\n" + "".join("%d,%s,%d\n" % (i, key(k), rng.randrange(1000)) for i, k in enumerate(pkeys)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--small", type=int, default=20000)
    ap.add_argument("--large", type=int, default=60000)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    out = {"small": a.small, "large": a.large, "cells": []}
    with tempfile.TemporaryDirectory(prefix="cgs-") as t:
        d = pathlib.Path(t)
        print("%-34s %14s %14s %8s" % ("cell", "hash Ir/row", "merge Ir/row", "ratio"))
        for kind in ("text", "int"):
            for n in (a.small, a.large):
                gen(d, n, kind)
            for jt in ("inner", "left", "semi", "anti"):
                row = {}
                for sorted_ in (False, True):
                    counts = []
                    for n in (a.small, a.large):
                        on = "k=k:int" if kind == "int" else "k=k"
                        argv = [a.bin, "--root", str(d), "--join", "b-%d.csv" % n, "--on", on, "--join-type", jt, "--format", "csv", "--max-rows", "1000000", "--max-join-rows", "1000000000"]
                        if jt in ("inner", "left"):
                            argv += ["--join-prefix", "b_"]
                        if sorted_:
                            argv.append("--join-sorted")
                        counts.append(ir(argv + ["p-%d.csv" % n]))
                    row[sorted_] = (counts[1] - counts[0]) / (a.large - a.small)
                name = "%s, %s join" % (kind, jt)
                print("%-34s %14.2f %14.2f %8.3f" % (name, row[False], row[True], row[True] / row[False]))
                out["cells"].append({"cell": name, "hash_per_row": row[False], "merge_per_row": row[True], "ratio": row[True] / row[False]})
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
