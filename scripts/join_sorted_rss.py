#!/usr/bin/env python3
"""J9 (docs/joins.md section 23): what the merge join holds, measured: the accounted bytes and the peak resident set of `--join-sorted` over a build file that the hash join refuses at the bound, against the same
files joined without the flag (the hash join, under a bound large enough for it).

    python3 scripts/join_sorted_rss.py [--bin build/table] [--runs 5] [--shapes ROWSxGROUP,...] [--json out.json]

For each shape a build file `bk,bg,bv,bt` of ROWS rows in groups of GROUP equal keys (keys k%09d in order, cells of 12 bytes) and a probe of 2,000 rows (keys drawn from the build's, in order) are made; then

* the accounted bytes of the merge are the formula of docs/joins.md 6.2 over the largest group a probe row meets (the rows of the group: record + 16 each, and the key + 60), computed here from the files, and checked
  against the program: the call is accepted at `--max-state-bytes` equal to it and refused at one byte below (`limit.state-too-large`, `detail.holder` `join-group`);
* the accounted bytes of the hash join are the same formula over every row of the build (what `--max-state-bytes` would have to be: the call without the flag is refused at one byte below);
* the peak resident set the kernel reports (`wait4` of a small launcher, tests/conformance/maxrss.c), the minimum of `--runs` runs, of the merge at the bound of its accounted bytes and of the hash join at the
  bound of its own.

The plan shows every column of the build, so that the hash join (which holds only the columns a plan names, section 22) and the merge (which holds whole records) hold the same record; and nothing is claimed of a time. The conditions (machine, cores, load average before and after) are printed with the table.
"""
import argparse
import json
import os
import pathlib
import platform
import random
import sys
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "conformance"))


def launcher():
    out = os.path.join(tempfile.mkdtemp(prefix="maxrss-"), "maxrss")
    subprocess.run(["cc", "-O1", "-o", out, str(ROOT / "tests" / "conformance" / "maxrss.c")], check=True)
    return out


def peak(helper, binary, args):
    p = subprocess.run([helper, binary, *[str(a) for a in args]], capture_output=True, text=True, check=True)
    rss, status = p.stdout.split()
    return int(rss) * (1 if sys.platform == "darwin" else 1024), int(status)


def make(d, rows, group):
    rng = random.Random(7)
    keys = ["k%09d" % (i // group) for i in range(rows)]
    recs = []
    with open(d / "b.csv", "w") as f:
        f.write("bk,bg,bv,bt\n")
        for k in keys:
            r = [k, "".join(rng.choice("abcdefghij") for _ in range(12)), "".join(rng.choice("0123456789") for _ in range(12)), "".join(rng.choice("abcdefghij") for _ in range(12))]
            recs.append(r)
            f.write(",".join(r) + "\n")
    ngroups = (rows + group - 1) // group
    picks = sorted(rng.sample(range(ngroups), min(2000, ngroups)))
    with open(d / "p.csv", "w") as f:
        f.write("pk,pt\n")
        for g in picks:
            f.write("k%09d,t\n" % g)
    return recs, picks


def need(recs, group, picks, hash_join):
    """The accounted bytes of docs/joins.md 6.2: of the largest group a probe row meets (the merge), or of the whole build (the hash join)."""
    def row(r):
        return len(",".join(r)) + 16
    if hash_join:
        return sum(row(r) for r in recs) + (len(recs) + group - 1) // group * (len(recs[0][0]) + 60)
    best = 0
    for g in picks:
        rows = recs[g * group:(g + 1) * group]
        best = max(best, sum(row(r) for r in rows) + len(rows[0][0]) + 60)
    return best


def call(d, bound, sorted_):
    a = ["--root", d, "--join", "b.csv", "--on", "pk=bk", "--select", "pk,pt,bk,bg,bv,bt", "--format", "csv", "--max-state-bytes", bound]
    if sorted_:
        a.append("--join-sorted")
    return a + ["p.csv"]


def accepted(binary, d, sorted_, n):
    ok = subprocess.run([binary, *[str(x) for x in call(d, n, sorted_)]], capture_output=True, input=b"").returncode == 0
    low = subprocess.run([binary, *[str(x) for x in call(d, n - 1, sorted_)]], capture_output=True, input=b"").returncode == 8
    return ok and low


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--shapes", default="100000x1,100000x100,300000x1,300000x1000")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    helper = launcher()
    load0 = os.getloadavg()
    cells = []
    for shape in a.shapes.split(","):
        rows, group = (int(x) for x in shape.split("x"))
        with tempfile.TemporaryDirectory(prefix="sorted-") as t:
            d = pathlib.Path(t)
            recs, picks = make(d, rows, group)
            n_merge = need(recs, group, picks, False)
            n_hash = need(recs, group, picks, True)
            row = {"shape": shape, "file_bytes": os.path.getsize(d / "b.csv"), "accounted_merge": n_merge, "accounted_hash": n_hash}
            row["formula_holds_merge"] = accepted(a.bin, d, True, n_merge)
            row["formula_holds_hash"] = accepted(a.bin, d, False, n_hash)
            row["rss_merge"] = min(peak(helper, a.bin, call(d, n_merge, True))[0] for _ in range(a.runs))
            row["rss_hash"] = min(peak(helper, a.bin, call(d, n_hash, False))[0] for _ in range(a.runs))
            # the hash join at the merge's bound is the refusal
            low = subprocess.run([a.bin, *[str(x) for x in call(d, n_merge, False)]], capture_output=True, input=b"")
            row["hash_at_merge_bound_status"] = low.returncode
            cells.append(row)
            print(json.dumps(row), flush=True)
    load1 = os.getloadavg()
    cond = {"machine": platform.platform(), "cores": os.cpu_count(), "load_before": load0, "load_after": load1, "runs": a.runs, "threads": "the join is read on one core"}
    print(json.dumps(cond))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"conditions": cond, "cells": cells}, indent=1))
    return 0 if all(c["formula_holds_merge"] and c["formula_holds_hash"] and c["hash_at_merge_bound_status"] == 8 for c in cells) else 1


if __name__ == "__main__":
    sys.exit(main())
