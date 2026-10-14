#!/usr/bin/env python3
"""The edge cells of the float reader, one answer per cell, against Python's `float()` (docs/numbers.md, G1; issue #36).

    python3 scripts/float_edge.py [--bin build/table] [--seed 1] [--random 20000] [--list]

`scripts/float_bulk.py` compares aggregates (count, min, max, thresholds, distinct) over millions of cells; this one reads every cell as its own group
(`--group k --agg min:x:float`) and compares the printed double with Python's, bit for bit, so a single wrong cell is named. The cells (all of them
deterministic from `--seed`; the first three families do not use it):

  * subnormal boundaries: n * 2^-1074 for n in 1..2100 and n = 2^j - 1, 2^j, 2^j + 1 for j in 1..53, around the smallest normal 2^-1022 (n = 2^52 +- 4100),
    each as the exact decimal, as the exact decimal midpoint to the next double, a hair above it (a 1 appended after the last digit) and a hair below it
    (the last digit lowered by one and a 9 string after it), the midpoints also in scientific form;
  * the named ties: 5e-324, 2.5e-324, 4.94e-324, 4.9406564584124654e-324, 7.4e-324, 1e-323, 2.2250738585072014e-308 and its neighbours, 2.225073858507201e-308;
  * exponents -330..-300 with 1..30 significant digits (17, 18 and 19 and the 20 to 30 above them at length), random digits;
  * `--random` midpoints n * 2^-1074 + 2^-1075 with n of a random bit length 1..53 (these include the normal cell just above 2^-1022 that docs/gap-float.md
    reports `std.json.to_float` of the old pin misread), in the same four forms.

A cell Python reads as zero (below half of 2^-1074) is not a number to the table (status 12), so those are run one at a time and must be refused with exit 8.
Exit 1 on any difference. `--list` prints the number of cells per family and runs nothing.
"""
import argparse
import os
import random
import struct
import subprocess
import sys
import tempfile
from decimal import Decimal, getcontext
from fractions import Fraction

getcontext().prec = 1400
LOW = Fraction(1, 2 ** 1074)


def exact(fr):
    """the exact decimal of a Fraction whose denominator is a power of two (it terminates), as 'ddd.ddd'"""
    return format(Decimal(fr.numerator) / Decimal(fr.denominator), "f")


def forms(n):
    """the four decimals around n * 2^-1074 and its midpoint to the next double"""
    out = []
    v = exact(n * LOW)
    out.append(v)
    mid = exact((2 * n + 1) * LOW / 2)
    out.append(mid)
    out.append(mid + "1")
    # a hair below: the midpoint minus 10^-(len+3), written out
    frac = mid.split(".")[1]
    digits = int(mid.replace(".", ""))
    below = str(digits - 1).rjust(len(mid.replace(".", "")), "0")
    out.append(below[: len(below) - len(frac)] + "." + below[len(below) - len(frac):] + "9" * 3)
    # scientific form of the midpoint: the digits as an integer, and the exponent
    out.append("%se-%d" % (mid.replace(".", "").lstrip("0") or "0", len(frac)))
    return out


def boundary_cells():
    ns = set(range(1, 2101))
    for j in range(1, 54):
        for d in (-1, 0, 1):
            if 2 ** j + d >= 1:
                ns.add(2 ** j + d)
    for d in range(-4100, 4101):
        ns.add(2 ** 52 + d)
    cells = []
    for n in sorted(ns):
        cells += forms(n)
    return cells


NAMED = ["5e-324", "2.5e-324", "4.94e-324", "4.9406564584124654e-324", "4.9406564584124655e-324", "2.4703282292062328e-324", "2.470328229206233e-324", "7.4e-324",
         "7.410984687618698e-324", "7.41098468761869e-324", "1e-323", "1.5e-323", "9.88e-324", "2.2250738585072014e-308", "2.2250738585072011e-308", "2.225073858507201e-308",
         "2.2250738585072012e-308", "2.2250738585072009e-308", "2.2250738585072013e-308", "2.2250738585072015e-308", "2.2250738585072016e-308", "4.9e-324", "5.0e-324",
         "0.000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005"]


def exponent_cells(rng):
    cells = []
    for e in range(-330, -299):
        for nd in range(1, 31):
            for _ in range(8 if nd < 17 else 24):
                d = rng.randint(1, 9) * 10 ** (nd - 1) + rng.randint(0, 10 ** (nd - 1) - 1) if nd > 1 else rng.randint(1, 9)
                s = str(d)
                cells.append("%s.%se%d" % (s[0], s[1:] or "0", e + 0))
                cells.append("%se%d" % (s, e - nd + 1))
    return cells


def random_cells(rng, count):
    cells = []
    for _ in range(count):
        bits = rng.randint(1, 53)
        n = rng.getrandbits(bits) | (1 << (bits - 1))
        cells += forms(n)
    return cells


def bits_of(x):
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def table(binary, root, *args):
    return subprocess.run([binary, "--root", root, *args, "f.csv"], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default="build/table")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--random", type=int, default=20000)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    families = [("boundaries", boundary_cells()), ("named", NAMED), ("exponents -330..-300", exponent_cells(rng)), ("random midpoints", random_cells(rng, a.random))]
    if a.list:
        for name, cells in families:
            print("%-22s %d" % (name, len(cells)))
        return 0
    ok, refused = [], []
    for name, cells in families:
        for c in cells:
            if len(c) > 1100:
                continue
            v = float(c)
            if v == 0.0:
                refused.append(c)
            else:
                ok.append(c)
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "f.csv"), "w") as f:
        f.write("k,x\n")
        for i, c in enumerate(ok):
            f.write("%d,%s\n" % (i, c))
    p = table(a.bin, d, "--group", "k", "--agg", "min:x:float", "--format", "csv", "--max-groups", "1000000", "--max-state-bytes", "1073741824")
    bad = 0
    if p.returncode != 0:
        print("table failed", p.returncode, p.stderr[:300] or p.stdout[:300])
        return 1
    got = {}
    for line in p.stdout.decode().strip().split("\n")[1:]:
        k, v = line.split(",")
        got[int(k)] = v
    if len(got) != len(ok):
        bad += 1
        print("DIFFERENT number of answers", len(got), len(ok))
    for i, c in enumerate(ok):
        if i not in got or bits_of(float(got[i])) != bits_of(float(c)):
            bad += 1
            if bad <= 20:
                print("DIFFERENT", c[:70], "table", got.get(i), "python", repr(float(c)))
    for c in refused:
        with open(os.path.join(d, "f.csv"), "w") as f:
            f.write("k,x\n1,%s\n" % c)
        q = table(a.bin, d, "--agg", "min:x:float", "--format", "csv")
        if q.returncode != 8:
            bad += 1
            print("DIFFERENT a cell below half of 2^-1074 is not refused", c[:70], q.returncode)
    print("%d cells read (+%d refused), %d differences" % (len(ok), len(refused), bad))
    return 1 if bad else 0


sys.exit(main())
