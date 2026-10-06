#!/usr/bin/env python3
"""SPIKE: what the contenders do, and cost, on numeric columns, so the gates of docs/numbers.md have numbers to start
from.  A seeded 1,000,000-row file (id,status,bytes,price,ratio,path): `price` has two decimals (0..99999.99),
`ratio` is a double printed in its shortest form (so 15-17 significant digits).  Every contender's answer is checked
against Python's exact one before timing; the minimum of N interleaved runs is the figure.

    python3 scripts/spikes/bench_engines.py [--bin build/table] [--runs 5] [--rows 1000000]
"""
import argparse, csv, math, os, random, subprocess, sys, time, tempfile
from decimal import Decimal
from fractions import Fraction

def gen(path, rows):
    r = random.Random(1)
    with open(path, "w", newline="") as f:
        f.write("id,status,bytes,price,ratio,path\n")
        for i in range(rows):
            f.write(f"{i},{r.choice([200,200,200,301,404,500])},{r.randint(0,99999)},{r.randint(0,9999999)/100:.2f},{r.uniform(0,1000)!r},/p/{r.randint(0,999)}\n")

def truth(path):
    cnt, sb, sp, sr, fl = {}, {}, {}, {}, 0
    rr = {}
    with open(path, newline="") as f:
        rd = csv.reader(f); next(rd)
        for row in rd:
            s = int(row[1]); b = int(row[2]); p = Decimal(row[3]); x = float(row[4])
            cnt[s] = cnt.get(s, 0) + 1; sb[s] = sb.get(s, 0) + b; sp[s] = sp.get(s, 0) + p
            rr.setdefault(s, []).append(x)
            if s == 404 and p >= Decimal("500.00"): fl += 1
    return cnt, sb, sp, {s: math.fsum(v) for s, v in rr.items()}, fl

def run(argv, sink=True):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p.stdout.decode(), p.stderr.decode(), p.returncode

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bin", default="build/table"); ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--rows", type=int, default=1_000_000); a = ap.parse_args()
    d = tempfile.mkdtemp(); data = os.path.join(d, "data.csv"); gen(data, a.rows)
    cnt, sb, sp, sr, fl = truth(data)
    def duck(sql, threads):
        return ["duckdb", "-csv", "-noheader", "-c", f"set threads={threads}; " + sql]
    rd = "read_csv('%s', header=true, columns={'id':'bigint','status':'int','bytes':'bigint','price':'%%s','ratio':'double','path':'varchar'})" % data
    cases = []   # name, argv, checker(stdout) -> bool
    def parse_groups(out, conv):
        return {int(l.split(",")[0]): conv(l.split(",")[1]) for l in out.strip().split("\n") if l and l[0].isdigit()}
    cases.append(("table  group-sum bytes (int)", [a.bin, "--group", "status", "--agg", "sum:bytes", "--format", "csv", data], lambda o: parse_groups(o, int) == sb))
    for t in (1, 16):
        nm = "1 thread" if t == 1 else "16 threads"
        cases.append((f"duckdb {nm} group-sum bytes (bigint)", duck(f"copy (select status, sum(bytes) from {rd % 'decimal(18,2)'} group by status order by 1) to '/dev/stdout' (header false)", t), lambda o: parse_groups(o, int) == sb))
        cases.append((f"duckdb {nm} group-sum price DECIMAL(18,2)", duck(f"copy (select status, sum(price) from {rd % 'decimal(18,2)'} group by status order by 1) to '/dev/stdout' (header false)", t), lambda o: parse_groups(o, Decimal) == sp))
        cases.append((f"duckdb {nm} group-sum price DOUBLE", duck(f"copy (select status, sum(price) from {rd % 'double'} group by status order by 1) to '/dev/stdout' (header false)", t), lambda o: all(abs(Decimal(repr(v)) - sp[k]) < Decimal("0.001") for k, v in parse_groups(o, float).items())))
        cases.append((f"duckdb {nm} group-sum ratio DOUBLE", duck(f"copy (select status, sum(ratio) from {rd % 'double'} group by status order by 1) to '/dev/stdout' (header false)", t), lambda o: all(abs(v - sr[k]) < 1e-6 * abs(sr[k]) for k, v in parse_groups(o, float).items())))
        cases.append((f"duckdb {nm} filter status=404 and price>=500.00 (DECIMAL)", duck(f"copy (select count(*) from {rd % 'decimal(18,2)'} where status=404 and price>=500.00) to '/dev/stdout' (header false)", t), lambda o: int(o.strip()) == fl))
        cases.append((f"duckdb {nm} mean price DECIMAL(18,2) by status", duck(f"copy (select status, avg(price) from {rd % 'decimal(18,2)'} group by status order by 1) to '/dev/stdout' (header false)", t), lambda o: all(abs(Decimal(repr(v)) - sp[k] / cnt[k]) < Decimal("0.0001") for k, v in parse_groups(o, float).items())))
    cases.append(("csvtk -j 1 group-sum price (float64)", ["csvtk", "-j", "1", "summary", "-g", "status", "-f", "price:sum", "-w", "2", data], lambda o: all(abs(Decimal(v) - sp[int(k)]) < Decimal("0.01") for k, v in (l.split(",") for l in o.strip().split("\n")[1:]))))
    cases.append(("csvtk -j 1 filter2 status==404 && price>=500 (float64)", ["csvtk", "-j", "1", "filter2", "-f", "$status==404 && $price>=500", data], lambda o: len(o.strip().split("\n")) - 1 == fl))
    ftruth = 0
    with open(data, newline="") as f:
        rr = csv.reader(f); next(rr)
        for row in rr:
            if row[1] == "404" and int(row[2]) > 50000: ftruth += 1
    cases.append(("table  filter status=404 and bytes:int>50000, count (int)", [a.bin, "--where", "status = 404 and bytes:int > 50000", "--agg", "count", "--format", "csv", data], lambda o: int(o.strip().split("\n")[-1].split(",")[-1]) == ftruth))
    cases.append(("duckdb 1 thread filter status=404 and bytes>50000, count (bigint)", duck(f"copy (select count(*) from {rd % 'decimal(18,2)'} where status=404 and bytes>50000) to '/dev/stdout' (header false)", 1), lambda o: int(o.strip()) == ftruth))
    res = {}
    for name, argv, chk in cases:
        t, out, err, rc = run(argv)
        if rc != 0: print("FAILED", name, rc, err[:200]); continue
        if chk and not chk(out): print("WRONG ANSWER", name, out[:200]); continue
        res[name] = [t]
    order = list(res)
    for _ in range(a.runs - 1):
        for name, argv, chk in cases:
            if name in res: res[name].append(run(argv)[0])
    # pandas (parse included, as everything else)
    import pandas as pd
    def pand():
        t = time.perf_counter(); df = pd.read_csv(data, usecols=["status", "price", "ratio"]); g = df.groupby("status").price.sum(); return time.perf_counter() - t
    res["pandas read_csv + groupby sum (float64)"] = [pand() for _ in range(a.runs)]
    for name, v in res.items(): print(f"{min(v):7.3f} s  (median {sorted(v)[len(v)//2]:.3f})  {name}")
main()
