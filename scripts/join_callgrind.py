#!/usr/bin/env python3
"""J2 (docs/joins.md section 17): instruction counts per row by callgrind, for a machine with no instruction counter (Linux with perf_event_paranoid 4, as gram has).

    python3 scripts/join_callgrind.py --old OLD_BINARY --new NEW_BINARY [--data build/num/data.csv] [--small 20000] [--large 60000] [--json out.json]

`valgrind --tool=callgrind` counts the instructions a run executes (`Collected : N`), exactly and with no regard to the load of the machine. A run's count is a start-up cost plus a cost per row, so every cell is run
on a prefix of `--small` rows and on one of `--large` rows, and the cost of a row is the DIFFERENCE of the two counts over the difference of the rows: the start-up (the same for both binaries up to the table of
flags, and reading the header) cancels, and what is left is what a row costs. Two binaries are compared by the ratio of those per-row costs (new over old), and the totals beside them. Three groups of cells:

* the non-join cells of scripts/gate_regress.py (G6, the standard ones) on the numeric benchmark file: the shared loops are not edited by a join, so the per-row cost must be the same instruction for instruction;
* the inner join of J1 (one text pair, a probe of N rows against a build of 5,000 rows), the path J2 must not slow: old against new;
* the join shapes J2 adds (left, semi, anti, a typed pair, two pairs, USING), new only: their cost per probe row, for the record.

The bound is the one of the Mac's gate (gate_regress.py COUNTER_LIMIT): a ratio of at most 1.01. Linux only (valgrind); nothing here runs on the Mac.
"""
import argparse
import json
import os
import pathlib
import random
import re
import sys
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
LIMIT = 1.01


def ir(argv, cwd=None):
    """Instructions executed by one run, by callgrind; (count, return code)."""
    p = subprocess.run(["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, cwd=cwd)
    m = re.search(r"Collected\s*:\s*(\d+)", p.stderr)
    if not m:
        sys.exit("callgrind gave no count: %s" % p.stderr[-400:])
    return int(m.group(1)), p.returncode


def prefix(src, dst, rows):
    with open(src, "rb") as f, open(dst, "wb") as o:
        for i, line in enumerate(f):
            if i > rows:
                break
            o.write(line)


def gen_join(d, rows, build_rows, kind="int", miss=0.2):
    """A probe of `rows` rows `id,k,v` (a share `miss` with a key the build lacks) and a build of `build_rows` rows `k,name`, seed 7."""
    rnd = random.Random(7)
    keys = [str(v) for v in rnd.sample(range(10 * build_rows), build_rows)] if kind == "int" else ["u%08x-%d" % (rnd.getrandbits(32), i) for i in range(build_rows)]
    (d / "build.csv").write_text("k,name\n" + "".join("%s,n%d\n" % (k, i) for i, k in enumerate(keys)))
    lines = ["id,k,v,w\n"]
    for i in range(rows):
        k = keys[rnd.randrange(build_rows)] if rnd.random() >= miss else ("%d" % (10 * build_rows + rnd.randrange(10 * build_rows)) if kind == "int" else "x%08x" % rnd.getrandbits(32))
        lines.append("%d,%s,%d,%s\n" % (i, k, rnd.randrange(1000), k))
    (d / ("probe-%d.csv" % rows)).write_text("".join(lines))


def per_row(binary, make_argv, small, large):
    a, ra = ir(make_argv(binary, small))
    b, rb = ir(make_argv(binary, large))
    if ra or rb:
        sys.exit("a run failed: %s" % (make_argv(binary, small),))
    return a, b, (b - a) / (large - small)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--data", default=str(ROOT / "build" / "num" / "data.csv"))
    ap.add_argument("--small", type=int, default=20000)
    ap.add_argument("--large", type=int, default=60000)
    ap.add_argument("--json", default="")
    ap.add_argument("--all", action="store_true", help="also run the old binary on the cells that are new only (from J3b on `main` has them all), and judge them")
    a = ap.parse_args()
    import gate_regress as G
    out = {"limit": LIMIT, "small": a.small, "large": a.large, "cells": []}
    failed = False
    with tempfile.TemporaryDirectory(prefix="cg-") as t:
        d = pathlib.Path(t)
        for n in (a.small, a.large):
            prefix(a.data, d / ("num-%d.csv" % n), n)
            gen_join(d, n, 5000)
        print("%-40s %12s %12s %9s %9s %9s" % ("cell", "old Ir/row", "new Ir/row", "ratio", "old total", "new total"))
        for name, args in G.CELLS.items():
            if "1M keys" in name or "id json page" in name or "distinct id" in name:
                continue                                  # the cells with a million keys need a million rows; the per-row slope of the others is the gate
            mk = lambda b, n, args=args: [b, "--root", str(d), *args, "num-%d.csv" % n]
            o1, o2, orow = per_row(a.old, mk, a.small, a.large)
            n1, n2, nrow = per_row(a.new, mk, a.small, a.large)
            ratio = nrow / orow
            bad = ratio > LIMIT
            failed |= bad
            print("%-40s %12.2f %12.2f %9.4f %9d %9d  %s" % (name, orow, nrow, ratio, o2, n2, "OVER" if bad else ""))
            out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": ratio, "old_total": o2, "new_total": n2})
        # the join of J1 (one text pair, inner) with both binaries
        for name, extra in (("join inner, text pair, select 4 columns", ["--select", "id,k,v,b_name"]), ("join inner, text pair, all columns", [])):
            mk = lambda b, n, extra=extra: [b, "--root", str(d), "--join", "build.csv", "--on", "k=k", "--join-prefix", "b_", *extra, "--format", "csv", "--max-rows", "1000000", "probe-%d.csv" % n]
            o1, o2, orow = per_row(a.old, mk, a.small, a.large)
            n1, n2, nrow = per_row(a.new, mk, a.small, a.large)
            ratio = nrow / orow
            bad = ratio > LIMIT
            failed |= bad
            print("%-40s %12.2f %12.2f %9.4f %9d %9d  %s" % (name, orow, nrow, ratio, o2, n2, "OVER" if bad else ""))
            out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": ratio, "old_total": o2, "new_total": n2})
        # J3: the conditions applied before the join, and the groups and the sort over a join. The first is on a path J2 had (a --where on the probe, which J3 applies before the key is read and again after the join: the ratio is
        # given for the record and is not judged); the others are new
        j3 = {
            "join inner, --where on the probe (pushed)": ["--where", "v != '7'"],
            "join inner, --join-where on the build": ["--join-where", "name != 'n7'"],
            "join inner, --group b_name --agg count,sum:v": ["--group", "b_name", "--agg", "count,sum:v"],
            "join left, --group b_name --agg count,sum:v": ["--join-type", "left", "--group", "b_name", "--agg", "count,sum:v"],
            "join inner, --order-by v:int": ["--order-by", "v:int"],
        }
        for name, extra in j3.items():
            mk = lambda b, n, extra=extra: [b, "--root", str(d), "--join", "build.csv", "--on", "k=k", *(["--join-prefix", "b_"] if "--join-type" not in extra or True else []), *extra, "--format", "csv", "--max-rows", "1000000", "--max-groups", "1000000", "probe-%d.csv" % n]
            n1, n2, nrow = per_row(a.new, mk, a.small, a.large)
            if a.all:
                o1, o2, orow = per_row(a.old, mk, a.small, a.large)
                bad = nrow / orow > LIMIT
                failed |= bad
                print("%-40s %12.2f %12.2f %9.4f %9d %9d  %s" % (name, orow, nrow, nrow / orow, o2, n2, "OVER" if bad else ""))
                out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": nrow / orow, "old_total": o2, "new_total": n2})
            elif name.startswith("join inner, --where"):
                o1, o2, orow = per_row(a.old, mk, a.small, a.large)
                print("%-40s %12.2f %12.2f %9.4f %9d %9d  (for the record)" % (name, orow, nrow, nrow / orow, o2, n2))
                out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": nrow / orow, "old_total": o2, "new_total": n2})
            else:
                print("%-40s %12s %12.2f %9s %9s %9d" % (name, "-", nrow, "-", "-", n2))
                out["cells"].append({"cell": name, "new_per_row": nrow, "new_total": n2})
        # the build: the cost of a build row, from builds of two sizes against a probe of one row (J1's path, old against new; then the build shapes J2 adds, new only)
        rnd = random.Random(7)
        for nb in (a.small // 4, a.large // 4):
            keys = [str(v) for v in rnd.sample(range(10 * nb), nb)]
            (d / ("b%d.csv" % nb)).write_text("k,name\n" + "".join("%s,n%d\n" % (k, i) for i, k in enumerate(keys)))
        (d / "p1.csv").write_text("id,k,v,w\n1,5,1,5\n")
        small_b, large_b = a.small // 4, a.large // 4
        for name, extra in (("build row, inner, text pair", []), ("build row, inner, text pair, USING-free select", ["--select", "id"])):
            mk = lambda b, n, extra=extra: [b, "--root", str(d), "--join", "b%d.csv" % n, "--on", "k=k", "--join-prefix", "b_", *extra, "--format", "csv", "p1.csv"]
            o1, o2, orow = per_row(a.old, mk, small_b, large_b)
            n1, n2, nrow = per_row(a.new, mk, small_b, large_b)
            ratio = nrow / orow
            bad = ratio > LIMIT
            failed |= bad
            print("%-40s %12.2f %12.2f %9.4f %9d %9d  %s" % (name, orow, nrow, ratio, o2, n2, "OVER" if bad else ""))
            out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": ratio, "old_total": o2, "new_total": n2})
        for name, extra in (("build row, semi (keys only)", ["--join-type", "semi"]), ("build row, anti (keys only)", ["--join-type", "anti"]), ("build row, left", ["--join-type", "left", "--join-prefix", "b_"]), ("build row, :int pair", ["--on", "k=k:int", "--join-prefix", "b_"]),
                            ("build row, two text pairs", ["--on", "k=k,w=k", "--join-prefix", "b_"])):
            if "--on" not in extra:
                extra = ["--on", "k=k"] + extra
            mk = lambda b, n, extra=extra: [b, "--root", str(d), "--join", "b%d.csv" % n, *extra, "--format", "csv", "p1.csv"]
            n1, n2, nrow = per_row(a.new, mk, small_b, large_b)
            if a.all:
                o1, o2, orow = per_row(a.old, mk, small_b, large_b)
                bad = nrow / orow > LIMIT
                failed |= bad
                print("%-40s %12.2f %12.2f %9.4f %9d %9d  %s" % (name, orow, nrow, nrow / orow, o2, n2, "OVER" if bad else ""))
                out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": nrow / orow, "old_total": o2, "new_total": n2})
                continue
            print("%-40s %12s %12.2f %9s %9s %9d" % (name, "-", nrow, "-", "-", n2))
            out["cells"].append({"cell": name, "new_per_row": nrow, "new_total": n2})
        # the shapes J2 adds, new only
        shapes = {
            "join left, text pair": ["--join-type", "left", "--join-prefix", "b_"],
            "join semi, text pair": ["--join-type", "semi"],
            "join anti, text pair": ["--join-type", "anti"],
            "join inner, :int pair": ["--on", "k=k:int", "--join-prefix", "b_"],
            "join left, :int pair": ["--on", "k=k:int", "--join-type", "left", "--join-prefix", "b_"],
            "join inner, two text pairs": ["--on", "k=k,w=k", "--join-prefix", "b_"],
            "join inner, USING (one name)": ["--on", "k"],
        }
        for name, extra in shapes.items():
            argv_on = ["--on", "k=k"] if "--on" not in extra else []
            mk = lambda b, n, extra=extra, argv_on=argv_on: [b, "--root", str(d), "--join", "build.csv", *argv_on, *extra, "--format", "csv", "--max-rows", "1000000", "probe-%d.csv" % n]
            n1, n2, nrow = per_row(a.new, mk, a.small, a.large)
            if a.all:
                o1, o2, orow = per_row(a.old, mk, a.small, a.large)
                bad = nrow / orow > LIMIT
                failed |= bad
                print("%-40s %12.2f %12.2f %9.4f %9d %9d  %s" % (name, orow, nrow, nrow / orow, o2, n2, "OVER" if bad else ""))
                out["cells"].append({"cell": name, "old_per_row": orow, "new_per_row": nrow, "ratio": nrow / orow, "old_total": o2, "new_total": n2})
                continue
            print("%-40s %12s %12.2f %9s %9s %9d" % (name, "-", nrow, "-", "-", n2))
            out["cells"].append({"cell": name, "new_per_row": nrow, "new_total": n2})
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1) + "\n")
    print("\nbound %.2f on the ratio of the per-row costs: %s" % (LIMIT, "FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
