#!/usr/bin/env python3
"""J7 (docs/joins.md section 22): what the pruned build saves, measured: the accounted bytes and the peak resident set of a join whose build file is wide, for a plan that names few of its columns, against the same call
on `main`'s binary (which holds the whole record) and against the same call naming every column.

    python3 scripts/join_prune_rss.py [--bin build/table] [--main PATH] [--runs 5] [--shapes ROWSxCOLS,...] [--json out.json]

For each shape a build file `bk,bg,c0..` of ROWS rows and COLS columns in all (cells of 10 bytes, unique keys in random order, seed 7) and a probe of 5 rows are made; then, for the plans "names 2 columns", "names half"
and "names every column":

* the accounted bytes by the formula of docs/joins.md 6.2 over the records held, computed here from the file by another program (`tests/conformance/join_ref7.py`), and checked against the program: the call is accepted
  at `--max-state-bytes` equal to it and refused at one byte below (the program's own count; `main`'s binary is checked against the whole-record formula the same way);
* the peak resident set the kernel reports for the process (`wait4` of a small launcher, tests/conformance/maxrss.c: the launcher is small, so the child's peak is its own), the minimum of `--runs` runs, in bytes.

Nothing is claimed of a time. The conditions (machine, cores, load average before and after) are printed with the table.
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
    recs = []
    with open(d / "b.csv", "w") as f:
        f.write(",".join(names) + "\n")
        for k in keys:
            row = ["k%d" % k, rng.choice("xyz")] + ["".join(rng.choice("abcdefghij") for _ in range(10)) for _ in range(cols - 2)]
            recs.append(row)
            f.write(",".join(row) + "\n")
    with open(d / "p.csv", "w") as f:
        f.write("pk,pg\n")
        for i in range(5):
            f.write("k%d,%s\n" % (keys[i * 7 % rows], "xyz"[i % 3]))
    return names, recs


def total(recs, keep):
    seen = set()
    n = 0
    for r in recs:
        n += R7.record_len(r, keep) + 16
        if r[0] not in seen:
            seen.add(r[0])
            n += len(r[0].encode()) + 60
    return n


def call(d, select, bound):
    return ["--root", d, "--join", "b.csv", "--on", "pk=bk", "--select", select, "--format", "csv", "--max-state-bytes", bound, "p.csv"]


def accepted(binary, d, select, n):
    ok = subprocess.run([binary, *[str(x) for x in call(d, select, n)]], capture_output=True, input=b"").returncode == 0
    low = subprocess.run([binary, *[str(x) for x in call(d, select, n - 1)]], capture_output=True, input=b"").returncode == 8
    return ok and low


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--main", default="")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--shapes", default="8000x152,100000x22,300000x8")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    helper = launcher()
    load0 = os.getloadavg()
    cells = []
    for shape in a.shapes.split(","):
        rows, cols = (int(x) for x in shape.split("x"))
        with tempfile.TemporaryDirectory(prefix="prune-") as t:
            d = pathlib.Path(t)
            names, recs = make(d, rows, cols)
            size = os.path.getsize(d / "b.csv")
            plans = [("names 2 columns (pk,bg)", "pk,bg", {1}), ("names half", "pk,bg," + ",".join(names[2:2 + (cols - 2) // 2]), set(range(1, 2 + (cols - 2) // 2))),
                     ("names every column", "pk," + ",".join(names), set(range(cols)))]
            for label, select, keep in plans:
                n_new = total(recs, keep)
                n_whole = total(recs, None)
                row = {"shape": shape, "file_bytes": size, "plan": label, "accounted_pruned": n_new, "accounted_whole": n_whole}
                row["formula_holds_new"] = accepted(a.bin, d, select, n_new)
                row["rss_new"] = min(peak(helper, a.bin, call(d, select, 1073741824))[0] for _ in range(a.runs))
                if a.main:
                    row["formula_holds_main"] = accepted(a.main, d, select, n_whole)
                    row["rss_main"] = min(peak(helper, a.main, call(d, select, 1073741824))[0] for _ in range(a.runs))
                cells.append(row)
                print(json.dumps(row), flush=True)
    load1 = os.getloadavg()
    cond = {"machine": platform.platform(), "cores": os.cpu_count(), "load_before": load0, "load_after": load1, "runs": a.runs, "threads": "the join is read on one core"}
    print(json.dumps(cond))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"conditions": cond, "cells": cells}, indent=1))
    return 0 if all(c["formula_holds_new"] and c.get("formula_holds_main", True) for c in cells) else 1


if __name__ == "__main__":
    sys.exit(main())
