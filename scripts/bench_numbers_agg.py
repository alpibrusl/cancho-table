#!/usr/bin/env python3
"""Gates G7 and G10 of docs/numbers.md for the decimal aggregates (stage N2).

    python3 scripts/bench_numbers_agg.py [--bin build/table] [--runs 7] [--file build/num/data.csv] [--threads 16]

The file of scripts/bench_numbers.py (id,status,bytes,price,ratio,path,cents: `price` two decimals, `cents` the same number as an integer of as many
digits: the integer cell the decimal one is compared with). Questions, by `status`:

  sum     sum of the value                          sum:cents            | sum:price:dec(2)
  mean    mean of the value (4 fractional digits)   mean:cents@4         | mean:price:dec(2)@4
  minmax  the minimum and the maximum               min,max              | min,max
  all     count, sum, min, max, mean                everything above

each as the integer cell and as the decimal cell for `table` (G7: dec/int must be at most 1.15), and for DuckDB (DECIMAL(18,2) and DOUBLE),
csvtk and Miller where installed. **Every contender's answer is compared with Python's exact one first**: with `Decimal`, the exact sum, the
exact minimum and maximum, the mean rounded half to even at 4 digits. `table` and DuckDB's DECIMAL must equal it digit for digit; the ones that
compute in doubles (DuckDB DOUBLE, csvtk, Miller) are allowed an error and the largest one is printed, which is the point of the exact types.
DuckDB's AVG of a decimal is a double: its mean is compared to 1e-4.
"""
import argparse
import csv
import io
import pathlib
import shutil
import subprocess
import sys
import time
from decimal import Decimal, ROUND_HALF_EVEN, getcontext

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench_numbers as bn  # noqa: E402

ROOT = bn.ROOT
LIMIT = 1.15


def exact(data):
    """status -> (count, exact sum, min, max, mean at 4 digits) of price (Decimal) and of cents, by Python."""
    getcontext().prec = 60
    rows = {"price": {}, "cents": {}}
    with open(data, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            rows["price"].setdefault(r[1], []).append(Decimal(r[3]))
            rows["cents"].setdefault(r[1], []).append(Decimal(r[6]))
    out = {}
    for col, groups in rows.items():
        out[col] = {}
        for k, v in groups.items():
            mean = (sum(v) / len(v)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            out[col][k] = {"count": len(v), "sum": sum(v), "min": min(v), "max": max(v), "mean": mean}
    return out


def parse(out, columns, header=True):
    """csv text with a header: status, then the columns in `columns` order -> {status: {name: Decimal}}."""
    res = {}
    rd = csv.reader(io.StringIO(out))
    if header:
        next(rd)
    for r in rd:
        if not r:
            continue
        res[r[0]] = {n: Decimal(v) for n, v in zip(columns, r[1:])}
    return res


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--file", default=str(ROOT / "build" / "num" / "data.csv"))
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--rows", type=int, default=1_000_000)
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    if not data.exists():
        bn.generate(data, a.rows)
    want = exact(data)
    d = str(data)
    root = ["--root", str(data.parent)]
    name = data.name
    QS = {  # question -> (functions in output order)
        "sum": ["sum"], "mean": ["mean"], "minmax": ["min", "max"], "all": ["count", "sum", "min", "max", "mean"]}

    def table_argv(col, typed, q, threads):
        items = []
        for fn in QS[q]:
            if fn == "count":
                items.append("count")
            else:
                spec = f"{fn}:{col}"
                if typed:
                    spec += ":dec(2)"
                if fn == "mean":
                    spec += "@4"
                items.append(spec)
        t = [] if threads == 1 else ["--threads", str(threads), "--parallel-min-bytes", "0"]
        return [a.bin, *root, "--group", "status", "--agg", ",".join(items), "--format", "csv", *t, name]

    def duck_argv(typ, q, threads):
        sel = []
        for fn in QS[q]:
            sel.append({"count": "count(*)", "sum": "sum(price)", "min": "min(price)", "max": "max(price)", "mean": "avg(price)"}[fn])
        rd = f"read_csv('{d}', header=true, columns={{'id':'bigint','status':'int','bytes':'bigint','price':'{typ}','ratio':'double','path':'varchar','cents':'bigint'}})"
        return ["duckdb", "-csv", "-noheader", "-c", f"set threads={threads}; copy (select status, {', '.join(sel)} from {rd} group by status order by 1) to '/dev/stdout' (format csv, header false)"]

    def with_header(argv_fn, cols):
        return argv_fn, cols

    cells = []
    for q in QS:
        for threads in (1, a.threads):
            cells.append((f"table int   {q:6} t={threads}", ("int", q, threads), table_argv("cents", False, q, threads), "cents", "exact", q, True))
            cells.append((f"table dec   {q:6} t={threads}", ("dec", q, threads), table_argv("price", True, q, threads), "price", "exact", q, True))
        if shutil.which("duckdb"):
            for threads in (1, a.threads):
                cells.append((f"duckdb DECIMAL {q:6} t={threads}", ("dd", q, threads), duck_argv("decimal(18,2)", q, threads), "price", "duck", q, False))
                cells.append((f"duckdb DOUBLE  {q:6} t={threads}", ("dbl", q, threads), duck_argv("double", q, threads), "price", "float", q, False))
        if shutil.which("csvtk") and q in ("sum", "all"):
            fs = ",".join(f"price:{f}" for f in QS[q] if f != "count")
            cells.append((f"csvtk -j 1      {q:6}", ("csvtk", q, 1), ["csvtk", "-j", "1", "summary", "-g", "status", "-f", fs, "-w", "4", d], "price", "float", q, False))
        if shutil.which("mlr") and q in ("sum", "all"):
            aggs = ",".join(f for f in QS[q] if f != "count")
            argv = ["mlr", "--icsv", "--ocsv", "stats1", "-a", aggs, "-f", "price", "-g", "status", d]
            cells.append((f"mlr            {q:6}", ("mlr", q, 1), argv, "price", "float", q, False))
    good = []
    worst_err = {}
    for label, grp, argv, col, kind, q, _ in cells:
        t, p = timed(argv)
        if p.returncode != 0:
            print("FAILED", label, p.stderr[:200])
            continue
        fns = [f for f in QS[q]]
        cols = fns
        try:
            if grp[0] in ("csvtk",):
                rd = list(csv.reader(io.StringIO(p.stdout.decode())))
                got = {r[0]: {f: Decimal(v) for f, v in zip([f for f in fns if f != "count"], r[1:])} for r in rd[1:] if r}
            elif grp[0] == "mlr":
                rd = list(csv.reader(io.StringIO(p.stdout.decode())))
                hdr = rd[0]
                got = {r[0]: {f: Decimal(r[hdr.index(f"price_{f}")]) for f in fns if f != "count"} for r in rd[1:] if r}
            else:
                got = parse(p.stdout.decode(), cols, header=grp[0] not in ("dd", "dbl"))
        except Exception as e:
            print("UNPARSEABLE", label, e, p.stdout[:120])
            continue
        ok = True
        err = Decimal(0)
        for k, w in want[col].items():
            g = got.get(k)
            if g is None:
                ok = False
                break
            for f in fns:
                if f not in g:
                    continue
                if f == "count":
                    ok = ok and g[f] == w["count"]
                elif f == "mean" and grp[0] not in ("int", "dec"):
                    ok = ok and abs(g[f] - w[f]) < Decimal("0.0001")        # a double's mean
                elif kind in ("exact", "duck"):
                    ok = ok and g[f] == w[f]
                else:
                    e = abs(g[f] - w[f])
                    err = max(err, e)
                    ok = ok and e < Decimal("0.01") * max(1, abs(w[f])) * Decimal("0.0000001") * 100
        if not ok:
            print("WRONG ANSWER", label)
            continue
        if kind == "float":
            worst_err[label] = err
        good.append((label, grp, argv))
    times = {l: [] for l, _, _ in good}
    for i in range(a.runs):
        order = good if i % 2 == 0 else list(reversed(good))
        for label, grp, argv in order:
            times[label].append(timed(argv)[0])
    best = {l: min(v) for l, v in times.items()}
    print("%-32s %9s" % ("cell (minimum of %d)" % a.runs, "seconds"))
    for label, _, _ in good:
        extra = ""
        if label in worst_err and worst_err[label] != 0:
            extra = "   largest error of a sum, min or max against the exact one: %s" % worst_err[label]
        print("%-32s %9.4f%s" % (label, best[label], extra))
    worst = 0.0
    for q in QS:
        for threads in (1, a.threads):
            i, dd = best.get(f"table int   {q:6} t={threads}"), best.get(f"table dec   {q:6} t={threads}")
            if i and dd:
                r = dd / i
                worst = max(worst, r)
                print("G7 %-6s threads %-3d dec/int = %.3f%s" % (q, threads, r, "   <-- over %.2f" % LIMIT if r > LIMIT else ""))
                for other in ("duckdb DECIMAL", "duckdb DOUBLE "):
                    o = best.get(f"{other} {q:6} t={threads}")
                    if o:
                        print("      %s %-6s t=%-3d  duckdb / table(dec) = %.2f" % (other.strip(), q, threads, o / dd))
        dd = best.get(f"table dec   {q:6} t=1")
        for other in ("csvtk -j 1     ", "mlr           "):
            o = best.get(f"{other} {q:6}")
            if o and dd:
                print("      %s %-6s  / table(dec, 1 thread) = %.2f" % (other.strip(), q, o / dd))
    print("G7: worst dec/int %.3f (limit %.2f): %s" % (worst, LIMIT, "PASS" if worst <= LIMIT else "FAIL"))
    return 0 if worst <= LIMIT else 1


if __name__ == "__main__":
    sys.exit(main())
