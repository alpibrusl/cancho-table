#!/usr/bin/env python3
"""A bulk check of the float reader (docs/numbers.md, stage N3a pass line: millions of cells against Python's `float()`).

    python3 scripts/float_bulk.py [--cells 10000000] [--bin build/table] [--seed 1]

Writes `--cells` cells of every shape a column holds (two decimals, shortest repr of random doubles at any exponent, integers past 2^53, exponent forms, 17-digit
decimals, subnormals, the exact midpoints between two doubles), reads them with `table`, and compares with Python (correctly rounded) on: the count, the minimum and
the maximum (as doubles), and the number of rows at or below each of 12 thresholds (a cell read one ulp wrong moves a row across a threshold only by luck, so the thresholds
are the values of 12 cells and their neighbours); and, on the first 2,000,000, the number of distinct doubles (a misrounded cell makes two doubles one or one two).
Exit 1 on any difference.
"""
import argparse, os, random, struct, subprocess, sys, tempfile, time
from fractions import Fraction
from decimal import Decimal, getcontext
getcontext().prec = 1300

def cell(r):
    k = r.random()
    if k < 0.2: return "%.2f" % r.uniform(-1e5, 1e5)
    if k < 0.45: return repr(struct.unpack(">d", struct.pack(">Q", r.getrandbits(64)))[0])
    if k < 0.55: return str(r.randint(-2 ** 70, 2 ** 70))
    if k < 0.7: return "%de%d" % (r.randint(-10 ** 6, 10 ** 6), r.randint(-30, 30))
    if k < 0.85: return repr(r.uniform(-1, 1) * 10.0 ** r.randint(-320, 300))
    if k < 0.93: return "%.17g" % r.uniform(0, 1)
    if k < 0.97:
        x = struct.unpack(">d", struct.pack(">Q", r.getrandbits(52) | (r.getrandbits(1) << 63)))[0]   # subnormal
        return repr(x)
    x = struct.unpack(">d", struct.pack(">Q", r.getrandbits(62)))[0]
    if not (0 < x < 1.7e308): return "0.5"
    nxt = struct.unpack(">d", struct.pack(">q", struct.unpack(">q", struct.pack(">d", x))[0] + 1))[0]
    mid = (Fraction(x) + Fraction(nxt)) / 2
    return format(Decimal(mid.numerator) / Decimal(mid.denominator), "f") + r.choice(["", "1", ""])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", type=int, default=10_000_000)
    ap.add_argument("--bin", default="build/table")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    r = random.Random(a.seed)
    d = tempfile.mkdtemp()
    path = os.path.join(d, "f.csv")
    t = time.time()
    cells = []
    with open(path, "w") as f:
        f.write("k,x\n")
        for i in range(a.cells):
            c = cell(r)
            try:
                v = float(c)
            except ValueError:
                c, v = "0.5", 0.5
            if v != v or v in (float("inf"), float("-inf")) or (v == 0 and any(ch in "123456789" for ch in c.split("e")[0])):
                c, v = "0.5", 0.5
            f.write("g,%s\n" % c)
            cells.append(v)
    print("generated %d cells in %.0fs" % (len(cells), time.time() - t), flush=True)
    bad = 0
    def run(*args):
        p = subprocess.run([a.bin, "--root", d, *args, "f.csv"], capture_output=True)
        assert p.returncode == 0, p.stderr[:300] or p.stdout[:300]
        return p.stdout.decode().strip().split("\n")
    out = run("--agg", "count,min:x:float,max:x:float", "--format", "csv", "--max-rows", "20000000")
    cnt, lo, hi = out[1].split(",")
    want = (str(len(cells)), min(cells), max(cells))
    if (int(cnt), float(lo), float(hi)) != (len(cells), want[1], want[2]):
        bad += 1; print("DIFFERENT count/min/max", out)
    pick = sorted(r.sample(cells, 12))
    for v in pick:
        for t in (v, struct.unpack(">d", struct.pack(">q", struct.unpack(">q", struct.pack(">d", v))[0] + (1 if v > 0 else -1)))[0]):
            got = int(run("--where", "x:float <= %r" % t, "--agg", "count", "--format", "csv", "--max-rows", "20000000")[1])
            w = sum(1 for c in cells if c <= t)
            if got != w:
                bad += 1; print("DIFFERENT threshold", repr(t), got, w)
    n = min(len(cells), 2_000_000)
    out = run("--where", "k = g", "--agg", "distinct:x:float", "--format", "csv", "--max-rows", "20000000", "--max-distinct", "10000000", "--max-state-bytes", "1000000000") if len(cells) <= n else None
    if out:
        if int(out[1]) != len(set(cells)):
            bad += 1; print("DIFFERENT distinct", out[1], len(set(cells)))
    print("%d cells, %d differences" % (len(cells), bad))
    os.remove(path)
    return 1 if bad else 0

sys.exit(main())
