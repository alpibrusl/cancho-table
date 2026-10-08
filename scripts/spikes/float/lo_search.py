#!/usr/bin/env python3
"""Evidence for the mutant `lo <= 1` -> `lo <= 0` (docs/gap-float.md section 6): how often `lo` is 1 where the rule is consulted.

    lo_search.py [N]     N random draws per q (default 200000)

For every q in -4..23 and N draws of w (random 1 to 64 bits; and, for q < 0, multiples of 5^-q, the only w whose product can be exactly halfway), runs the model of `eisel_lemire` up to the tie
test and counts (a) how many reach it with lo == 0 and with lo == 1, (b) how many of those with `mant & 3 == 1` are exact halfways (`mant << (upper + 9) == hi`). Prints the totals.
Argument that lo == 1 cannot matter: an exact halfway w*10^q = j*2^e, j odd, has w = 5^-q * j' for q < 0, and T = ceil(2^k / 5^-q) = (2^k + d) / 5^-q with 0 <= d < 5^-q, so the 192-bit product is
j' * 2^k + j' * d, and j' * d < 2^64 does not reach bit 64: hi:lo is exactly the halfway pattern, lo = 0. For q >= 0 (q <= 27) T's low word is 0 and lo is the exact low word of the product: 0 for a halfway."""
import random
import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pow5_table import entry

M = (1 << 64) - 1
n = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
r = random.Random(7)
c = Counter()
for q in range(-4, 24):
    T = entry(q)
    th, tl = T >> 64, T & M
    p5 = 5 ** -q if q < 0 else 1
    for i in range(n):
        if q < 0 and i % 2:
            hi_j = (1 << 64) // p5
            w = p5 * r.randint(1, hi_j - 1)
        else:
            w = r.getrandbits(r.randint(1, 64)) | 1
        if not 0 < w < 1 << 64:
            continue
        lz = 64 - w.bit_length()
        ww = (w << lz) & M
        pr = ww * th
        lo, hi = pr & M, pr >> 64
        if hi & 511 == 511:
            sh = (ww * tl) >> 64
            nl = (lo + sh) & M
            if sh > nl:
                hi = (hi + 1) & M
            lo = nl
        if lo > 1:
            continue
        upper = hi >> 63
        mant = hi >> (upper + 9)
        c[("lo", lo)] += 1
        if mant & 3 == 1:
            c[("lo", lo, "mant&3==1")] += 1
            if (mant << (upper + 9)) & M == hi:
                c[("lo", lo, "exact halfway")] += 1
for k in sorted(c, key=str):
    print(k, c[k])
