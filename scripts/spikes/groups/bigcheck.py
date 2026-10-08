#!/usr/bin/env python3
"""bigcheck.py --bin PATH --base PATH: full-size plans on build/adv/f2.csv (and build/g4.csv when present), csv and json, at several thread counts with
the default chunk size (so that ranges grow, docs/gap-groups.md 3.2) and with explicit ones; every answer must equal the base's one-thread answer."""
import argparse, hashlib, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BIG = ["--max-groups", "1000000", "--max-state-bytes", "1073741824", "--max-distinct", "10000000"]
PLANS = [["--group", "k1m"], ["--group", "k1m", "--agg", "count,sum:v,min:v,max:v"], ["--group", "k100k,s", "--agg", "distinct:u"], ["--group", "s", "--agg", "distinct:id,sum:v:float"],
         ["--group", "k100k", "--agg", "mean:v@2,sum:v:float"], ["--group", "v:int", "--agg", "count"], ["--where", "v:int>50000", "--group", "k100k", "--agg", "sum:v"],
         ["--agg", "distinct:v"], ["--group", "k1m", "--sort", "-count", "--top", "5"], ["--group", "k1m", "--limit", "10", "--from", "5"], ["--group", "k100k", "--agg", "sum:v", "--sort", "-sum:v", "--limit", "20"],
         ["--group", "s"], ["--report", "types"], ["--order-by", "u:int", "--select", "id,u", "--top", "100"]]
def md(c):
    p = subprocess.run(c, capture_output=True)
    return (p.returncode, hashlib.md5(p.stdout).hexdigest()[:10], hashlib.md5(p.stderr).hexdigest()[:6])
ap = argparse.ArgumentParser(); ap.add_argument("--bin", required=True); ap.add_argument("--base", required=True); a = ap.parse_args()
adv = os.path.join(ROOT, "build", "adv"); bad = 0
for fmt in ([], ["--format", "csv"]):
    for pl in PLANS:
        base = md([a.base, "--root", adv, *pl, *fmt, *BIG, "--threads", "1", "f2.csv"])
        for t, ch in ((1, None), (2, None), (3, None), (6, None), (16, None), (64, None), (2, 4194304), (16, 100000), (7, 300000), (64, 1000000)):
            extra = ["--chunk-bytes", str(ch)] if ch else []
            got = md([a.bin, "--root", adv, *pl, *fmt, *BIG, "--threads", str(t), *extra, "f2.csv"])
            if got != base:
                bad += 1; print("DIFFERS", pl, fmt, t, ch, base, got)
print("bigcheck bad", bad); sys.exit(1 if bad else 0)
