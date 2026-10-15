#!/usr/bin/env python3
"""The same file and the same question at 1, 2, 4, 8 and 16 threads: `table` against DuckDB, side by side (standard library only).

    python3 scripts/threads_vs_duckdb.py [--bin build/table] [--duckdb duckdb] [--rows 2000000] [--threads 1,2,4,8,16] [--runs 3] [--json docs/assets/threads-vs-duckdb.json]

It writes a seeded file of doubles under a temporary directory (never in the repository; the bytes depend only on --seed and --rows, and their sha256 is
printed), computes the exact sum in Python (`fractions.Fraction`, rounded once to the nearest double), then asks the same question of each tool:

    table --threads N --agg sum:x:float --format csv f.csv
    duckdb:  SET threads=N; SELECT sum(x) ...            x as DOUBLE   (plain double addition)
             SET threads=N; SELECT kahan_sum(x) ...      x as DOUBLE   (DuckDB's compensated sum, also spelled fsum)
             SET threads=N; SELECT sum(x) ...            x as DECIMAL(38,12)   (integer addition; the file's values are first rounded to 12 places)

`--runs` times each (default 3) and prints one row per thread count: the value of table, the values of DuckDB's plain `sum` in each run, and whether each
is equal to the exact sum. **The gate**: the script exits 1 if the value of `table` differs from the exact sum at any thread count in any run. It does
not assert anything about DuckDB: it prints what DuckDB printed, in this run, on this machine. Adding doubles is order-dependent, so a threaded sum in
plain double addition may differ from one run to the next and from one thread count to the next; that is how the addition works, not a defect.

`--json PATH` writes the machine, versions, file and every value (docs/assets/threads-vs-duckdb.json is what scripts/site.py puts on the landing page).
`--mutate table|exact` is the test hook for the gate: it nudges one answer by a unit in the last place, and the script must then exit 1.

For another script (the demo video, a test): `python3 scripts/threads_vs_duckdb.py --rows 20000 --threads 1,2,4 --runs 2` runs in seconds; the exit
status is the gate, the table is on standard output, and without `duckdb` on the path (or with --no-duckdb) it runs `table` alone against the exact
sum. Import it and call `main([...])` for the exit status, or `measure(...)` for the dict that --json writes.
"""
import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import random
import re
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = 20261015
SCALE = 12  # the scale of the DECIMAL contender
AGGS = [("sum", "sum(x), x as DOUBLE", "DOUBLE"), ("kahan_sum", "kahan_sum(x), x as DOUBLE", "DOUBLE"), ("decimal", "sum(x), x as DECIMAL(38,%d)" % SCALE, "DECIMAL(38,%d)" % SCALE)]
SQL = {"sum": "sum(x)", "kahan_sum": "kahan_sum(x)", "decimal": "sum(x)"}
TYPE = {"sum": "DOUBLE", "kahan_sum": "DOUBLE", "decimal": "DECIMAL(38,%d)" % SCALE}


def write_file(path, rows, seed):
    """id,x with x = uniform(-1,1) * 10**randint(-3,6), written with repr: the same bytes for the same seed and rows. Returns (bytes, sha256, values)."""
    rnd = random.Random(seed)
    h = hashlib.sha256()
    n = 0
    xs = []
    with open(path, "wb") as f:
        for chunk in [b"id,x\n"]:
            f.write(chunk)
            h.update(chunk)
            n += len(chunk)
        for i in range(rows):
            x = rnd.uniform(-1, 1) * 10 ** rnd.randint(-3, 6)
            xs.append(x)
            line = ("%d,%r\n" % (i, x)).encode()
            f.write(line)
            h.update(line)
            n += len(line)
    return n, h.hexdigest(), xs


def exact_sum(xs):
    s = Fraction(0)
    for x in xs:
        s += Fraction(x)
    return s


def decimal_expected(xs):
    """The sum of the values each rounded half away from zero to SCALE places, exactly: what an integer-addition DECIMAL(38,SCALE) holds."""
    q = Decimal(1).scaleb(-SCALE)
    return sum((Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP) for x in xs), Decimal(0))


def run(argv, timeout=900):
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def table_value(binary, root, name, threads):
    st, out, err = run([binary, "--root", str(root), "--threads", str(threads), "--agg", "sum:x:float", "--format", "csv", name])
    if st != 0:
        raise SystemExit("threads_vs_duckdb: table exited %d at %d threads: %s%s" % (st, threads, out[:300], err[:300]))
    lines = [l for l in out.splitlines() if l.strip()]
    return lines[-1].split(",")[-1]


def duckdb_value(duckdb, path, kind, threads):
    q = "SET threads=%d; SELECT %s FROM read_csv('%s', header=true, columns={'id':'BIGINT','x':'%s'});" % (threads, SQL[kind], path, TYPE[kind])
    st, out, err = run([duckdb, "-csv", "-noheader", "-c", q])
    if st != 0:
        raise SystemExit("threads_vs_duckdb: duckdb exited %d (%s at %d threads): %s%s" % (st, kind, threads, out[:300], err[:300]))
    lines = [l for l in out.splitlines() if l.strip()]
    return lines[-1].strip()


def cpu_name():
    try:
        if sys.platform == "darwin":
            return run(["sysctl", "-n", "machdep.cpu.brand_string"])[1].strip()
        for l in open("/proc/cpuinfo"):
            if l.startswith("model name"):
                return l.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def git_commit():
    try:
        st, out, _ = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], 20)
        c = out.strip() if st == 0 else "unknown"
        st, out, _ = run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], 20)
        return c + (" (modified files)" if st == 0 and out.strip() else "")
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def measure(binary, duckdb, rows, threads, runs, seed, mutate=None, commit=None, keep=None):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="threads_vs_duckdb_", dir=keep))
    try:
        path = tmp / "f.csv"
        size, sha, xs = write_file(path, rows, seed)
        ex = exact_sum(xs)
        exact = float(ex)  # Fraction -> float rounds once, to nearest, ties to even
        if mutate == "exact":
            exact = math.nextafter(exact, math.inf)
        dec_ex = decimal_expected(xs)
        res = {
            "schema": 1,
            "machine": {"os": platform.platform(), "arch": platform.machine(), "cpu": cpu_name(), "cores": os.cpu_count(), "cores_usable": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count(), "load_average_before": [round(v, 2) for v in os.getloadavg()]},
            "versions": {"table_commit": commit or git_commit(), "python": platform.python_version(), "duckdb": None},
            "file": {"rows": rows, "bytes": size, "seed": seed, "sha256": sha, "generator": "x = random.Random(seed).uniform(-1,1) * 10**randint(-3,6), id = 0..rows-1, written with repr"},
            "exact_sum": repr(exact),
            "exact_sum_for_decimal_contender": str(dec_ex),
            "threads": list(threads),
            "runs": runs,
            "table": {},
            "duckdb": {},
        }
        for t in threads:
            vals = [table_value(binary, tmp, "f.csv", t) for _ in range(runs)]
            if mutate == "table" and t == threads[-1]:
                vals[0] = repr(math.nextafter(float(vals[0]), math.inf))
            res["table"][str(t)] = vals
        if duckdb:
            st, out, _ = run([duckdb, "--version"], 30)
            res["versions"]["duckdb"] = out.strip()
            for kind, _, _ in AGGS:
                res["duckdb"][kind] = {}
                for t in threads:
                    res["duckdb"][kind][str(t)] = [duckdb_value(duckdb, path, kind, t) for _ in range(runs)]
        res["machine"]["load_average_after"] = [round(v, 2) for v in os.getloadavg()]
        res["gate"] = {"table_equals_exact_everywhere": all(float(v) == exact for vs in res["table"].values() for v in vs)}
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def equal_exact(res, v):
    return float(v) == float(res["exact_sum"])


def render(res):
    out = []
    f, m, v = res["file"], res["machine"], res["versions"]
    out.append("exact sum (Fraction, rounded once): %s" % res["exact_sum"])
    out.append("file: %d rows, %d bytes, seed %d, sha256 %s" % (f["rows"], f["bytes"], f["seed"], f["sha256"]))
    out.append("machine: %s, %s, %s cores (%s usable by this process), load average before %s after %s" % (m["os"], m["cpu"], m["cores"], m["cores_usable"], m["load_average_before"], m.get("load_average_after")))
    out.append("table commit: %s; duckdb: %s; python %s; %d runs each" % (v["table_commit"], v["duckdb"] or "not run", v["python"], res["runs"]))
    out.append("")
    n = res["runs"]
    have = bool(res["duckdb"])
    head = ["threads", "table"] + (["DuckDB sum run %d" % (i + 1) for i in range(n)] + ["DuckDB equal to exact?"] if have else []) + ["table equal to exact?"]
    rows = [head]
    for t in res["threads"]:
        tv = res["table"][str(t)]
        line = [str(t), tv[0] if len(set(tv)) == 1 else "/".join(tv)]
        if have:
            dv = res["duckdb"]["sum"][str(t)]
            line += dv + ["%d of %d" % (sum(equal_exact(res, x) for x in dv), n)]
        line += ["yes" if all(equal_exact(res, x) for x in tv) else "NO"]
        rows.append(line)
    out += grid(rows)
    if have:
        out.append("")
        out.append("DuckDB's other aggregates, same file, same thread counts (values per run, then how many equal the exact sum):")
        rows = [["threads", "kahan_sum (DOUBLE)", "equal to exact?", "sum of DECIMAL(38,%d)" % SCALE, "same in all runs?"]]
        for t in res["threads"]:
            k = res["duckdb"]["kahan_sum"][str(t)]
            d = res["duckdb"]["decimal"][str(t)]
            rows.append([str(t), " ".join(k), "%d of %d" % (sum(equal_exact(res, x) for x in k), n), " ".join(d), "yes" if len(set(d)) == 1 else "no"])
        out += grid(rows)
        alld = {x for t in res["threads"] for x in res["duckdb"]["decimal"][str(t)]}
        out.append("the DECIMAL contender reads each value rounded to %d places; the exact sum of the rounded values is %s, DuckDB printed %s" % (
            SCALE, res["exact_sum_for_decimal_contender"], ", ".join(sorted(alld)) if len(alld) < 4 else "%d different values" % len(alld)))
        ks = {x for t in res["threads"] for x in res["duckdb"]["kahan_sum"][str(t)]}
        ds = {x for t in res["threads"] for x in res["duckdb"]["sum"][str(t)]}
        out.append("distinct values in this run: sum %d, kahan_sum %d, DECIMAL %d (table: %d)" % (len(ds), len(ks), len(alld), len({x for vs in res["table"].values() for x in vs})))
    out.append("")
    out.append("gate: table equals the exact sum at every thread count in every run: %s" % ("PASS" if res["gate"]["table_equals_exact_everywhere"] else "FAIL"))
    return out


def grid(rows):
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--duckdb", default="duckdb")
    ap.add_argument("--no-duckdb", action="store_true", help="run table alone against the exact sum")
    ap.add_argument("--rows", type=int, default=2_000_000)
    ap.add_argument("--threads", default="1,2,4,8,16")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--json", help="write the machine, versions, file and every value to this path")
    ap.add_argument("--commit", help="the table commit to record (default: git rev-parse HEAD)")
    ap.add_argument("--mutate", choices=["table", "exact"], help="test hook for the gate: nudge one answer by one unit in the last place")
    args = ap.parse_args(argv)
    threads = [int(x) for x in args.threads.split(",")]
    duck = None if args.no_duckdb else shutil.which(args.duckdb)
    if not args.no_duckdb and not duck:
        sys.stderr.write("threads_vs_duckdb: no `%s` on the path: running table alone against the exact sum\n" % args.duckdb)
    res = measure(args.bin, duck, args.rows, threads, args.runs, args.seed, args.mutate, args.commit)
    print("\n".join(render(res)))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(res, indent=1) + "\n")
        print("wrote", args.json)
    return 0 if res["gate"]["table_equals_exact_everywhere"] else 1


if __name__ == "__main__":
    sys.exit(main())
