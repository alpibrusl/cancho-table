#!/usr/bin/env python3
"""The cost of a row by the number of group columns (issue 66): instructions retired per row of `--group k0,...,k{n-1} --agg sum:v`, for each binary given.

    python3 scripts/bench_group_columns.py --table OLD NEW [--rows 1000000] [--cols 1,2,3,4,5,6,8] [--threads 1] [--file F]

The file is made here (seeded, no network): `rows` rows of eight key columns and `v`, each row one of 1000 prototypes, so that the groups are 10 at one column, 100 at two and
1000 from three on, and the cache of `agg.add_fast` hits (the path the change touches); the cells are 4 to 7 bytes. The count is the user-space instructions the process
retired (macOS `/usr/bin/time -l`, Linux `perf stat -e instructions:u`, as scripts/gate_regress.py --counter does): deterministic to a few parts in a thousand, so the load of the
machine does not enter. A binary that traps on a plan (status other than 0) is reported as such, not timed. The outputs of the binaries that answer are compared byte for byte
before anything is reported.
"""
import argparse
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_regress as g  # noqa: E402
import procs as subprocess  # noqa: E402,F401  (every child in a group that is reaped: tests/conformance/procs.py)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def make(path, rows, seed=66):
    rng = random.Random(seed)
    protos = []
    for p in range(1000):
        cells = []
        for i in range(8):
            d = (p // (10 ** i)) % 10 if i < 3 else rng.randrange(10)
            cells.append("k%dv%d%s" % (i, d, "x" * (d % 3)))
        protos.append(cells)
    with open(path, "w") as f:
        f.write(",".join("k%d" % i for i in range(8)) + ",v\n")
        for _ in range(rows):
            f.write(",".join(protos[rng.randrange(1000)]) + ",%d\n" % rng.randint(1, 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", nargs="+", required=True)
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--cols", default="1,2,3,4,5,6,8")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--file", default=str(ROOT / "build" / "bench" / "group_columns.csv"))
    a = ap.parse_args()
    path = pathlib.Path(a.file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        make(path, a.rows)
    rows = sum(1 for _ in open(path, "rb")) - 1
    print("%d rows; instructions per row (the minimum of %d runs), --threads %d" % (rows, a.runs, a.threads))
    print("%-4s %-8s" % ("cols", "groups") + "".join("%16s" % pathlib.Path(t).parent.name for t in a.table))
    for n in [int(x) for x in a.cols.split(",")]:
        args = ["--group", ",".join("k%d" % i for i in range(n)), "--agg", "sum:v", "--format", "csv"]
        t = [] if a.threads == 1 else ["--threads", str(a.threads), "--parallel-min-bytes", "0"]
        argvs = [[b, "--root", str(path.parent), *args, *t, path.name] for b in a.table]
        outs = [subprocess.run(x, capture_output=True) for x in argvs]
        answered = [o for o in outs if o.returncode == 0]
        if any(o.stdout != answered[0].stdout for o in answered):
            print("DIFFERENT OUTPUT at %d columns" % n)
            return 2
        groups = answered[0].stdout.count(b"\n") - 1 if answered else "-"
        cells = []
        for x, o in zip(argvs, outs):
            if o.returncode != 0:
                cells.append("trap(%d)" % o.returncode if o.returncode < 0 or o.returncode > 128 else "refused(%d)" % o.returncode)
                continue
            best = min(g.counted(x)[0] for _ in range(a.runs))
            cells.append("%.1f" % (best / rows))
        print("%-4d %-8s" % (n, groups) + "".join("%16s" % c for c in cells), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
