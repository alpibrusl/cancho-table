#!/usr/bin/env python3
"""Cell files for the float reader spikes (docs/gap-float.md): one cell per line, seeded.

    gen_cells.py KIND ROWS SEED > file

KIND: f17     repr(uniform(0, 1000)), the `ratio` column of scripts/bench_numbers.py (15 to 17 digits, shortest form)
      price   two decimals, 0..99999.99 (Clinger decides)
      money   a 9 to 17-digit integer part of cents with two decimals (the 16/17-digit money-like column)
      bits    repr of a double of random bits (every exponent, subnormals and huge ones included; no inf/nan)
      exp     d.dddE+-xx with 1 to 17 digits and an exponent in -330..300
      d19     19 random digits with the point at a random place, optional sign and exponent
      d25     25 random digits (more than the 19-digit window), point at a random place
      lead    leading zeros, `.5`, `5.`, `+`, `-` and an exponent with `+`, `0` padded ones, in random mixtures
      near    a double's exact decimal midpoint with its neighbours, printed with 17 to 40 digits (just above and below halfway)
      mid     exact midpoints between consecutive doubles (the full expansion, up to 767 digits) and the digit string +-1 in its last place
      long    1 to 1200 bytes: random digits with runs of zeros and nines, a point anywhere, trailing zeros (so that 20+ digit cells are exact), exponents that bring it to the edge of the range
      edge    every mantissa near a power of two or ten that a rule changes at (2^53, 2^63, 2^64, 10^15..10^20 and +-3 around them) with every exponent -30..30, as `NeX`, `N.0eX` and with the point inside (ROWS and SEED ignored)
      hard    hard_cells of tests/conformance/test_float.py (rng 41, 160 draws: the exact midpoint of two doubles, a hair above and below), 4 cells a draw; ROWS and SEED are the number of draws and the seed
      p10     powers of ten and their neighbours (1e22, 1e23, 9.999999999999999e22, 5e-324, 2.4703282292062327e-324 ...)
"""
import random
import struct
import sys
from fractions import Fraction


def bits_to_double(b):
    return struct.unpack("<d", struct.pack("<Q", b))[0]


def rand_double(r):
    while True:
        x = bits_to_double(r.getrandbits(64))
        if x == x and abs(x) != float("inf"):
            return x


def exact_decimal(fr, maxdigits=800):
    """The exact decimal expansion of a positive Fraction whose denominator is a power of two."""
    num, den = fr.numerator, fr.denominator
    k = den.bit_length() - 1
    assert den == 1 << k
    digits = str(num * 5 ** k)
    return digits, k  # value = digits * 10^-k


def fmt_digits(digits, k):
    if k == 0:
        return digits
    if len(digits) <= k:
        digits = "0" * (k - len(digits) + 1) + digits
    return digits[:-k] + "." + digits[-k:]


def midpoint(x):
    """Exact midpoint between x and the next double up (x finite positive)."""
    b = struct.unpack("<Q", struct.pack("<d", x))[0]
    lo, hi = Fraction(x), Fraction(bits_to_double(b + 1))
    return (lo + hi) / 2


def hard_cells(rng, n):
    """tests/conformance/test_float.py: the exact midpoint between two doubles, a hair above it and a hair below it, at every exponent."""
    out = []
    for _ in range(n):
        e = rng.choice([rng.randint(-1074, 1022), rng.randint(-1074, -1000), rng.randint(900, 1022), rng.randint(-60, 60)])
        x = float(Fraction(rng.getrandbits(53) | (1 << 52), 1) * Fraction(2) ** (e - 52)) if e > -1022 else float(Fraction(rng.getrandbits(52) + 1) * Fraction(2) ** -1074)
        if not (0 < x < 1.7e308):
            continue
        b = struct.unpack(">q", struct.pack(">d", x))[0]
        nxt = struct.unpack(">d", struct.pack(">q", b + 1))[0]
        mid = (Fraction(x) + Fraction(nxt)) / 2
        digits, k = exact_decimal(mid)
        t = fmt_digits(digits, k)
        out += [t, t + "1", t[:-1] + "4999", t[:-1] + "5000000001"]
    return out


def main():
    kind, rows, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    r = random.Random(seed)
    out = sys.stdout
    w = out.write
    if kind == "edge":
        ms = set()
        for base in [2 ** 53, 2 ** 52, 2 ** 63, 2 ** 64, 2 ** 62, 2 ** 54, 2 ** 55, 10 ** 15, 10 ** 16, 10 ** 17, 10 ** 18, 10 ** 19, 10 ** 20, 5 * 10 ** 15, 5 * 10 ** 18, 9 * 10 ** 18]:
            for d in range(-3, 4):
                ms.add(base + d)
        for m in sorted(ms):
            for e in range(-30, 31):
                t = str(m)
                w(f"{t}e{e}\n")
                w(f"{t}.0e{e}\n")
                p = len(t) // 2
                w(f"{t[:p]}.{t[p:]}e{e}\n")
                w(f"-{t}e{e}\n")
        return
    if kind == "hard":
        w("\n".join(hard_cells(r, rows)) + "\n")
        return
    for i in range(rows):
        if kind == "f17":
            w(repr(r.uniform(0, 1000)) + "\n")
        elif kind == "price":
            w(f"{r.randint(0, 9999999) / 100:.2f}\n")
        elif kind == "money":
            n = r.randint(10 ** r.randint(8, 16), 10 ** 17)
            s = str(n)
            w(("-" if r.random() < 0.3 else "") + s[:-2] + "." + s[-2:] + "\n")
        elif kind == "bits":
            w(repr(rand_double(r)) + "\n")
        elif kind == "exp":
            nd = r.randint(1, 17)
            digs = "".join(r.choice("0123456789") for _ in range(nd))
            digs = str(r.randint(1, 9)) + digs[1:]
            mant = digs[0] + ("." + digs[1:] if nd > 1 else "")
            e = r.randint(-330, 300)
            w(("-" if r.random() < 0.3 else "") + mant + r.choice("eE") + (r.choice(["", "+"]) if e >= 0 else "") + str(e) + "\n")
        elif kind in ("d19", "d25"):
            nd = 19 if kind == "d19" else 25
            digs = str(r.randint(1, 9)) + "".join(r.choice("0123456789") for _ in range(nd - 1))
            p = r.randint(0, nd)
            s = digs[:p] + "." + digs[p:] if r.random() < 0.9 else digs
            if r.random() < 0.5:
                s += "e" + str(r.randint(-340, 300))
            w(("-" if r.random() < 0.3 else "") + s + "\n")
        elif kind == "lead":
            nd = r.randint(1, 20)
            digs = "".join(r.choice("0123456789") for _ in range(nd))
            s = "0" * r.randint(0, 5) + digs
            p = r.randint(0, len(s))
            s = s[:p] + "." + s[p:] if r.random() < 0.85 else s
            if s in (".", "") or not any(c.isdigit() for c in s):
                s = "0.5"
            if r.random() < 0.4:
                s += r.choice("eE") + r.choice(["", "+", "-"]) + "0" * r.randint(0, 3) + str(r.randint(0, 320))
            w(r.choice(["", "", "+", "-"]) + s + "\n")
        elif kind in ("near", "mid"):
            x = abs(rand_double(r))
            if r.random() < 0.15:
                x = bits_to_double(r.getrandbits(52))  # a subnormal
            if x > 1e308:
                x = x / 4
            m = midpoint(x)
            digits, k = exact_decimal(m)
            if kind == "mid":
                w(fmt_digits(digits, k) + "\n")
                # exact midpoint with its last digit moved by one (just below / just above)
                for d in (-1, 1):
                    nd = str(int(digits) + d)
                    w(fmt_digits(nd, k) + "\n")
            else:
                # the first 17 to 40 digits of the midpoint, rounded up or down at the cut (a hair above or below)
                n = r.randint(17, 40)
                cut = digits[:n]
                if len(digits) > n:
                    if r.random() < 0.5:
                        cut = str(int(cut) + 1)
                    kk = k - (len(digits) - n)
                    w(fmt_digits(cut, kk) + "\n") if kk >= 0 else w(cut + "e" + str(-kk) + "\n")
                else:
                    w(fmt_digits(digits, k) + "\n")
        elif kind == "long":
            nd = r.choice([r.randint(1, 30), r.randint(1, 400), r.randint(1, 1090)])
            parts = []
            while sum(map(len, parts)) < nd:
                c = r.random()
                if c < 0.5:
                    parts.append("".join(r.choice("0123456789") for _ in range(r.randint(1, 25))))
                elif c < 0.75:
                    parts.append("0" * r.randint(1, 60))
                else:
                    parts.append("9" * r.randint(1, 30))
            digs = "".join(parts)[:nd]
            if r.random() < 0.3:
                digs = digs.rstrip("0") + "0" * r.randint(0, 40)
            if not digs.strip("0"):
                digs = "1" + digs
            p = r.randint(0, len(digs))
            sg = r.choice(["", "", "-", "+"])
            s = digs[:p] + "." + digs[p:] if r.random() < 0.8 else digs
            if r.random() < 0.7:
                # an exponent that puts the value near the top or bottom of the range, or anywhere
                mag = len(digs[:p].lstrip("0")) if p else 0
                e = r.choice([r.randint(-400, 400), 309 - mag + r.randint(-3, 3), -324 - mag + r.randint(-3, 3), r.randint(-100000, 100000)])
                s += r.choice("eE") + (r.choice(["", "+"]) if e >= 0 else "") + str(e)
            w(sg + s + "\n")
        elif kind == "p10":
            e = r.randint(-340, 310)
            m = r.choice([1, 9, 99, 999999999999999, 9999999999999999, 99999999999999999, 4, 5, 2, 24703282292062327, 24703282292062328, 17976931348623157, 17976931348623158])
            w(f"{m}e{e}\n")


main()
