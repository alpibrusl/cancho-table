#!/usr/bin/env python3
"""J2 (docs/joins.md section 17): the instructions a join spends per probe row and per build row, before (main) and after (this branch), by the machine's counter.

    python3 scripts/join_perrow.py --old OLD_BINARY --new NEW_BINARY [--dir DIR] [--json out.json]

macOS: `/usr/bin/time -l` reports `instructions retired`, the same counter scripts/gate_regress.py --counter uses (deterministic to a few parts in a thousand, blind to the load of the machine). Linux with perf_event_paranoid
at most 2: `perf stat -e instructions:u`. Where there is no counter (gram: perf_event_paranoid 4) use scripts/join_callgrind.py, which counts by simulation.

A run costs a start-up and a cost per row, so each is run on two sizes and the cost of a row is the difference of the counts over the difference of the rows: the probe's cost on probes of 250,000 and 1,000,000 rows against
one build of 100,000 rows with integer keys (the files of docs/joins.md section 12, seed 7, one in five probe keys in no build row for the left and anti joins), and the build's cost on builds of 100,000 and 400,000 rows
against a probe of one row. The inner join of one pair of text is the path of J1: the ratio new over old of its two costs is what J2 has to keep at 1.01 or less. The other shapes are new and are given as they are.
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
sys.path.insert(0, str(ROOT / "scripts"))


def count(argv):
    """(instructions, return code) of one run."""
    if sys.platform == "darwin":
        p = subprocess.run(["/usr/bin/time", "-l", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        m = re.search(r"(\d+)\s+instructions retired", p.stderr)
        return (int(m.group(1)) if m else 0), p.returncode
    p = subprocess.run(["perf", "stat", "-x,", "-e", "instructions:u", "--", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    n = sum(int(m.group(1)) for m in re.finditer(r"^(\d+),[^,]*,[^,]*instructions", p.stderr, re.M))
    return n, p.returncode


REPS = 3


def slope(binary, make, small, large):
    """The cost of a row: the difference of the counts at two sizes over the difference of the rows. J5: the count of each size is the MINIMUM of REPS runs (the counter of a run varies by a few parts in a thousand,
    page faults of the larger table included, which is a percent of a per-row slope; the work is deterministic and the noise is only ever added)."""
    a = b = 0
    for _ in range(REPS):
        x, ra = count(make(binary, small))
        y, rb = count(make(binary, large))
        if not x or not y or ra or rb:
            sys.exit("no count, or a run failed: %s" % (make(binary, small),))
        a = x if not a else min(a, x)
        b = y if not b else min(b, y)
    return (b - a) / (large - small), a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--dir", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--jsonl", action="store_true", help="J5 (docs/joins.md section 21): also the shapes with a JSON-lines side, new only (the same rows as the csv files, written as JSON lines), beside the csv cell they stand for")
    ap.add_argument("--all", action="store_true", help="compare every shape with the old binary (from J3b on `main` has them all; J2 compared the inner join only)")
    a = ap.parse_args()
    d = pathlib.Path(a.dir) if a.dir else pathlib.Path(tempfile.mkdtemp(prefix="join-perrow-"))
    d.mkdir(parents=True, exist_ok=True)
    out = {"cells": []}
    rnd = random.Random(7)
    # the builds: 100,000, 400,000 rows of unique integer keys in random order; the probes of 250,000 and 1,000,000 rows draw from the first build, one in five from no build row
    keysets = {}
    for n in (100_000, 400_000):
        keys = [str(v) for v in rnd.sample(range(10 * n), n)]
        keysets[n] = keys
        (d / ("build-%d.csv" % n)).write_text("k,name\n" + "".join("%s,n%d\n" % (k, i) for i, k in enumerate(keys)))
    keys = keysets[100_000]
    for n in (250_000, 1_000_000):
        lines = ["id,k,v\n"]
        for i in range(n):
            k = keys[rnd.randrange(100_000)] if rnd.random() >= 0.2 else str(1_000_000 + rnd.randrange(1_000_000))
            lines.append("%d,%s,%d\n" % (i, k, rnd.randrange(100000)))
        (d / ("probe-%d.csv" % n)).write_text("".join(lines))
    (d / "p1.csv").write_text("id,k,v\n1,5,1\n")
    (d / "p1.jsonl").write_text('{"id":"1","k":"5","v":"1"}\n')
    if a.jsonl:
        for n in (100_000, 400_000):
            rows = (d / ("build-%d.csv" % n)).read_text().splitlines()[1:]
            (d / ("build-%d.jsonl" % n)).write_text("".join('{"k":"%s","name":"%s"}\n' % tuple(r.split(",")) for r in rows))
        for n in (250_000, 1_000_000):
            rows = (d / ("probe-%d.csv" % n)).read_text().splitlines()[1:]
            (d / ("probe-%d.jsonl" % n)).write_text("".join('{"id":"%s","k":"%s","v":"%s"}\n' % tuple(r.split(",")) for r in rows))
    shapes = [
        ("inner, one pair of text (J1's path)", True, ["--join-type", "inner", "--join-prefix", "b_", "--on", "k=k"]),
        ("left, one pair of text", False, ["--join-type", "left", "--join-prefix", "b_", "--on", "k=k"]),
        ("semi, one pair of text", False, ["--join-type", "semi", "--on", "k=k"]),
        ("anti, one pair of text", False, ["--join-type", "anti", "--on", "k=k"]),
        ("inner, one pair :int", False, ["--join-type", "inner", "--join-prefix", "b_", "--on", "k=k:int"]),
        ("inner, two pairs of text", False, ["--join-type", "inner", "--join-prefix", "b_", "--on", "k=k,v=k"]),
        ("inner, shared name", False, ["--join-type", "inner", "--on", "k"]),
    ]
    print("%-40s %14s %14s %8s" % ("per probe row (instructions)", "old", "new", "new/old"))
    for name, both, extra in shapes:
        mk = lambda b, n, extra=extra: [b, "--root", str(d), "--join", "build-100000.csv", *extra, "--format", "csv", "--max-state-bytes", "1073741824", "probe-%d.csv" % n]
        new, *_ = slope(a.new, mk, 250_000, 1_000_000)
        if both or a.all:
            old, *_ = slope(a.old, mk, 250_000, 1_000_000)
            print("%-40s %14.2f %14.2f %8.4f" % (name, old, new, new / old))
            out["cells"].append({"cell": "probe row: " + name, "old": old, "new": new, "ratio": new / old})
        else:
            print("%-40s %14s %14.2f %8s" % (name, "-", new, "-"))
            out["cells"].append({"cell": "probe row: " + name, "new": new})
    if a.jsonl:
        # J5: the same inner join of one pair of text with a JSON-lines side, new only, for the record (the csv cell is the first line of the table above)
        jshapes = [
            ("inner, csv probe and csv build (reference)", "probe-%d.csv", "build-%d.csv", []),
            ("inner, jsonl probe, csv build", "probe-%d.jsonl", "build-%d.csv", ["--input-format", "jsonl", "--fields", "id,k,v", "--join-input-format", "csv"]),
            ("inner, csv probe, jsonl build", "probe-%d.csv", "build-%d.jsonl", ["--join-input-format", "jsonl", "--join-fields", "k,name"]),
            ("inner, jsonl probe, jsonl build", "probe-%d.jsonl", "build-%d.jsonl", ["--input-format", "jsonl", "--fields", "id,k,v", "--join-fields", "k,name"]),
        ]
        print("\n%-40s %14s   (JSON lines, new only)" % ("per probe row (instructions)", "new"))
        for name, pf, bf, fl in jshapes:
            mk = lambda b, n, pf=pf, bf=bf, fl=fl: [b, "--root", str(d), "--join", bf % 100_000, "--join-type", "inner", "--join-prefix", "b_", "--on", "k=k", *fl, "--format", "csv", "--max-state-bytes", "1073741824", pf % n]
            new, *_ = slope(a.new, mk, 250_000, 1_000_000)
            print("%-40s %14.2f" % (name, new))
            out["cells"].append({"cell": "probe row: " + name, "new": new})
        print("\n%-40s %14s   (JSON lines, new only)" % ("per build row (instructions)", "new"))
        for name, pf, bf, fl in jshapes:
            mk = lambda b, n, bf=bf, fl=fl, pf=pf: [b, "--root", str(d), "--join", bf % n, "--join-type", "inner", "--join-prefix", "b_", "--on", "k=k", *fl, "--format", "csv", "--max-state-bytes", "1073741824", ("p1.jsonl" if pf.endswith("jsonl") else "p1.csv")]
            new, *_ = slope(a.new, mk, 100_000, 400_000)
            print("%-40s %14.2f" % (name, new))
            out["cells"].append({"cell": "build row: " + name, "new": new})
    print("\n%-40s %14s %14s %8s" % ("per build row (instructions)", "old", "new", "new/old"))
    for name, both, extra in shapes:
        extra2 = [x for x in extra]
        mk = lambda b, n, extra2=extra2: [b, "--root", str(d), "--join", "build-%d.csv" % n, *extra2, "--format", "csv", "--max-state-bytes", "1073741824", "p1.csv"]
        new, *_ = slope(a.new, mk, 100_000, 400_000)
        if both or a.all:
            old, *_ = slope(a.old, mk, 100_000, 400_000)
            print("%-40s %14.2f %14.2f %8.4f" % (name, old, new, new / old))
            out["cells"].append({"cell": "build row: " + name, "old": old, "new": new, "ratio": new / old})
        else:
            print("%-40s %14s %14.2f %8s" % (name, "-", new, "-"))
            out["cells"].append({"cell": "build row: " + name, "new": new})
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1) + "\n")


main()
