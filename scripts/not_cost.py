#!/usr/bin/env python3
"""What a `not` costs a row (docs/or-and-columns.md section 17): instructions a row for plans that do the same work and differ only in how the conditions are combined.

    python3 scripts/not_cost.py [--table build/table] [--file build/bench/data.csv] [--rows 1000000] [--runs 3] [--callgrind]

Each plan is run on one thread over the benchmark file and answers no row (so the work is the evaluation and nothing else): the count is the **instructions retired** of the least of
`--runs` runs (macOS `/usr/bin/time -l`, Linux `perf stat`, through scripts/gate_regress.py's `counted`), or with `--callgrind` the `Collected` total of `valgrind --tool=callgrind`
(the counter of a Linux box where `perf_event_paranoid` is 4); the **cut** (the plan that tries one text condition and keeps nothing) is the base that
the process, the read and the split cost, so the per-row cost of the evaluation is the difference over the rows. A pair of plans answers the same rows; the first of each group is the
conjunction that the code has always run, the others are the program. Run it for the binary of `main` and for the binary of the branch, one after the other, on the same file.
"""
import argparse
import pathlib
import re
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gate_regress  # noqa: E402

# every plan keeps nothing: the last condition tried is false for every row, or the not of it is
PLANS = [
    ("cut: one text condition, false", "one", ["--where", "id = -1", "--select", "id", "--format", "csv"]),
    ("2 text conditions, a conjunction (true, then false)", "and2", ["--where", "status != 999 and id = -1", "--select", "id", "--format", "csv"]),
    ("2 text conditions, not (an or) (false, then true)", "notor2", ["--where", "not (status = 999 or id != -1)", "--select", "id", "--format", "csv"]),
    ("2 text conditions, a not in (true) and a condition (false)", "notin2", ["--where", "status not in (999) and id = -1", "--select", "id", "--format", "csv"]),
    ("4 text conditions, a conjunction (3 true, then false)", "and4", ["--where", "status != 999 and path != zz and note != zz and id = -1", "--select", "id", "--format", "csv"]),
    ("4 text conditions, an or (4 false)", "or4", ["--where", "status = 999 or path = zz or note = zz or id = -1", "--select", "id", "--format", "csv"]),
    ("4 text conditions, not (an or) (3 false, then true)", "notor4", ["--where", "not (status = 999 or path = zz or note = zz or id != -1)", "--select", "id", "--format", "csv"]),
    ("4 text conditions, not (an and) (4 true)", "notand4", ["--where", "not (status != 999 and path != zz and note != zz and id != -1)", "--select", "id", "--format", "csv"]),
    ("4 text conditions, a conjunction with a not in as its third", "andnotin4", ["--where", "status != 999 and path != zz and note not in (zz) and id = -1", "--select", "id", "--format", "csv"]),
    ("4 text conditions, a conjunction with a not contains as its third", "andnotcontains4", ["--where", "status != 999 and path != zz and note not contains zz and id = -1", "--select", "id", "--format", "csv"]),
    ("1 text condition, not (it) (true inside)", "not1", ["--where", "not (id != -1)", "--select", "id", "--format", "csv"]),
    ("4 :int conditions, a conjunction (3 true, then false)", "andint4", ["--where", "id:int >= 0 and bytes:int >= 0 and status:int >= 0 and id:int = -1", "--select", "id", "--format", "csv"]),
    ("4 :int conditions, not (an or) (3 false, then true)", "notorint4", ["--where", "not (id:int < 0 or bytes:int < 0 or status:int < 0 or id:int != -1)", "--select", "id", "--format", "csv"]),
    ("4 :int conditions, an or (4 false)", "orint4", ["--where", "id:int < 0 or bytes:int < 0 or status:int < 0 or id:int = -1", "--select", "id", "--format", "csv"]),
]


def count(argv, callgrind):
    if callgrind:
        p = subprocess.run(["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null", *argv], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        m = re.search(r"Collected\s*:\s*(\d+)", p.stderr)
        return int(m.group(1)) if m else 0
    return gate_regress.counted(argv)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(ROOT / "build" / "table"))
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "data.csv"))
    ap.add_argument("--rows", type=int, default=1_000_000, help="the rows of the file, for the cost of a row")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--callgrind", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    runs = 1 if a.callgrind else a.runs
    results = {}
    for name, key, args in PLANS:
        argv = [a.table, "--root", str(data.parent), *args, data.name]
        best = min(count(argv, a.callgrind) for _ in range(runs))
        results[key] = best
    base = results["one"]
    print("%-62s %14s %12s %12s" % ("plan (the same rows kept: none)", "instructions", "a row", "a row less the cut"))
    for name, key, args in PLANS:
        n = results[key]
        print("%-62s %14d %12.1f %12.1f" % (name, n, n / a.rows, (n - base) / a.rows))
    print()
    for label, a_key, b_key in (("2 text: not (or) against the conjunction", "notor2", "and2"), ("2 text: a not in against the same as a !=", "notin2", "and2"), ("1: not (it) against the cut", "not1", "one"),
                                ("4 text: or against the conjunction (A1)", "or4", "and4"), ("4 text: not (or) against the conjunction", "notor4", "and4"), ("4 text: not (and) against the conjunction", "notand4", "and4"),
                                ("4 text: a not in in a conjunction against the conjunction", "andnotin4", "and4"), ("4 text: a not contains in a conjunction against the conjunction", "andnotcontains4", "and4"),
                                ("4 text: not (or) against the or", "notor4", "or4"), ("4 :int: not (or) against the conjunction", "notorint4", "andint4"), ("4 :int: or against the conjunction (A1)", "orint4", "andint4"),
                                ("4 :int: not (or) against the or", "notorint4", "orint4")):
        print("%-62s %+8.1f instructions a row" % (label, (results[a_key] - results[b_key]) / a.rows))

if __name__ == "__main__":
    main()
