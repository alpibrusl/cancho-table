#!/usr/bin/env python3
"""What a rename costs (docs/or-and-columns.md section 18): instructions for plans that do the same work and differ only in a `--rename`.

    python3 scripts/rename_cost.py [--table build/table] [--file build/bench/data.csv] [--rows 1000000] [--runs 3] [--callgrind]

Each pair is run on one thread over the file: the plan without the rename, then with it, and the difference is printed with the rows. The rename is made once, where the labels are written
(`frame.prepare`), before the first row, so the difference should not depend on the number of rows: **run it on two files of different sizes** (the same generator, 20,000 and 200,000 rows)
and a cost of a row is the difference of the two differences over the difference of the rows, which is 0 if the rename is not in the loop. The count is the **instructions retired** of the least
of `--runs` runs (macOS `/usr/bin/time -l`, Linux `perf stat`, through scripts/gate_regress.py's `counted`), or with `--callgrind` the `Collected` total of `valgrind --tool=callgrind`
(the counter of a Linux box where `perf_event_paranoid` is 4).
"""
import argparse
import pathlib
import re
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gate_regress  # noqa: E402

PAIRS = [
    ("rows: --select status,bytes", ["--select", "status,bytes", "--format", "csv"], ["--rename", "status=s,bytes=b"]),
    ("rows: --where alone (all five columns)", ["--where", "status = 404", "--format", "csv"], ["--rename", "id=key,note=memo"]),
    ("rows: --select, json", ["--select", "status,bytes", "--limit", "1000000", "--max-bytes", "67108864"], ["--rename", "status=s,bytes=b"]),
    ("rows: --select, json lines", ["--select", "status,bytes", "--format", "jsonl"], ["--rename", "status=s,bytes=b"]),
    ("groups: --group status --agg sum:bytes", ["--group", "status", "--agg", "sum:bytes", "--format", "csv"], ["--rename", "status=s,sum:bytes=total"]),
    ("groups: --group path (1000 keys), sorted", ["--group", "path", "--sort", "-count", "--format", "csv"], ["--rename", "count=n"]),
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
    ap.add_argument("--rows", type=int, default=1_000_000, help="the rows of the file")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--callgrind", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    runs = 1 if a.callgrind else a.runs
    print("%-46s %14s %14s %12s %12s" % ("rows: %d" % a.rows, "without", "with", "difference", "a row"))
    for name, plan, rename in PAIRS:
        # the sort of the group by id keeps the same flags with the label the rename gives it
        with_rename = list(plan)
        if "--sort" in with_rename and "count=n" in rename:
            with_rename[with_rename.index("--sort") + 1] = "-n"
        base = [a.table, "--root", str(data.parent), *plan, data.name]
        renamed = [a.table, "--root", str(data.parent), *with_rename, *rename, data.name]
        x = min(count(base, a.callgrind) for _ in range(runs))
        y = min(count(renamed, a.callgrind) for _ in range(runs))
        print("%-46s %14d %14d %12d %12.4f" % (name, x, y, y - x, (y - x) / a.rows))


if __name__ == "__main__":
    main()
