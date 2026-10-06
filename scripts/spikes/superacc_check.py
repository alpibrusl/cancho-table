#!/usr/bin/env python3
"""SPIKE check for superacc.ls: exact sum / split-merge sum / mean of generated and adversarial double
lists against Python integer arithmetic (the exact sum as an integer multiple of 2^-1074, rounded once by int
true division, which is correctly rounded) and math.fsum.

    python3 scripts/spikes/superacc_check.py BINARY [--big N] [--seed S]
"""
import json, math, random, struct, subprocess, sys, argparse, time
from fractions import Fraction

DBL_MAX = sys.float_info.max
def bits(x): return struct.unpack('<q', struct.pack('<d', x))[0]
def frombits(b): return struct.unpack('<d', struct.pack('<Q', b & (2**64 - 1)))[0]
def unit(x):                       # x as an integer number of 2^-1074
    fr = Fraction(x); return fr.numerator * ((1 << 1074) // fr.denominator)
def exact(xs): return sum(unit(x) for x in xs)
def rnd_sum(S):
    try: return S / (1 << 1074)
    except OverflowError: return math.inf if S > 0 else -math.inf
def rnd_mean(S, n):
    try: return S / ((1 << 1074) * n)
    except OverflowError: return math.inf if S > 0 else -math.inf

def rand_double(r):
    while True:
        x = frombits(r.getrandbits(64))
        if math.isfinite(x): return x

def cases(r, big):
    c = []
    c += [[0.0], [-0.0], [1.0, -1.0], [0.1] * 10, [1e16, 1.0, -1e16, 1.0], [1.0, 2**-53], [1.0, 2**-53, 2**-200],
          [1.0, 2**-53, -2**-200], [1.0 + 2**-52, 2**-53], [1.0 + 2**-52, 2**-53, 2**-200],
          [DBL_MAX, DBL_MAX, -DBL_MAX], [DBL_MAX, -DBL_MAX], [DBL_MAX], [-DBL_MAX, -DBL_MAX],  # last is +-inf: expected inf
          [5e-324], [5e-324] * 3, [-5e-324, 5e-324 * 2], [2.2250738585072014e-308, -5e-324], [2.2250738585072009e-308] * 7,
          [2**-1074 * 3, 2**-1074 * 5], [1e308, 1e308, -1e308], [1e308, 1e-308, -1e308], [2**1023, 2**1023, -2**1023, 2**-1074],
          [0.1, 0.2, 0.3], [1e100, 1.0, -1e100], [3.0] * 1000, [float(i) for i in range(1, 2001)]]
    # an exact sum just below and above a tie, and a long cancelling run
    c.append([2.0**53, 1.0, 1.0])
    c.append([2.0**53, 1.0])
    c.append([2.0**53, 3.0])
    c.append([2.0**53 + 2, 1.0])
    for n in (1, 2, 3, 5, 10, 100, 1000):
        for _ in range(30):
            c.append([rand_double(r) for _ in range(n)])
    for _ in range(60):                                   # cancellation: x and -x shuffled, plus a residue
        xs = [rand_double(r) * 2.0**-r.randint(0, 80) for _ in range(r.randint(2, 200))]
        ys = xs + [-x for x in xs] + [rand_double(r) * 2.0**-1000 if r.random() < .5 else 0.0]
        r.shuffle(ys); c.append(ys)
    for _ in range(60):                                   # subnormals and tiny mixes
        c.append([frombits(r.getrandbits(52) | (r.getrandbits(1) << 63)) for _ in range(r.randint(1, 300))])
    for _ in range(60):                                   # huge and tiny
        c.append([r.choice([1, -1]) * r.choice([2.0**r.randint(-1074, 1023), 1e308 * r.random(), 1e-300 * r.random()])
                  for _ in range(r.randint(2, 400))])
    for _ in range(40):                                   # decimals like prices, so the sums are not exact in binary
        c.append([round(r.uniform(-1000, 1000), 2) for _ in range(r.randint(1, 5000))])
    for _ in range(40):                                   # near ties: a double plus half an ulp plus a tiny
        b = rand_double(r); u = math.ulp(b) if abs(b) < DBL_MAX / 2 else 1.0
        c.append([b, u / 2, r.choice([-1, 1]) * 2.0**-1074 * r.randint(0, 3)])
    if big: c.append([rand_double(r) * 2.0**-r.randint(0, 900) for _ in range(big)])
    return c

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('binary'); ap.add_argument('--big', type=int, default=1_000_000)
    ap.add_argument('--seed', type=int, default=1); a = ap.parse_args()
    r = random.Random(a.seed); cs = cases(r, a.big)
    doc = json.dumps(cs)
    t = time.time()
    out = subprocess.run([a.binary, 'check'], input=doc.encode(), capture_output=True, timeout=3600)
    if out.returncode: print('binary failed', out.returncode, out.stderr[:500]); sys.exit(2)
    lines = out.stdout.decode().split('\n')[:-1]
    assert len(lines) == len(cs), (len(lines), len(cs))
    bad = 0; infs = 0; fsum_checked = 0; fsum_overflow = 0
    for i, (xs, line) in enumerate(zip(cs, lines)):
        n, w, s3, i5, m = map(int, line.split())
        assert n == len(xs)
        S = exact(xs); want = rnd_sum(S); wantm = rnd_mean(S, len(xs))
        if math.isinf(want): infs += 1
        if not math.isinf(want):
            try:
                fs = math.fsum(xs); fsum_checked += 1
                if bits(fs) != bits(want) and not (fs == 0 and want == 0): print('fsum disagrees with exact int on case', i, fs, want); bad += 1
            except OverflowError:
                fsum_overflow += 1      # fsum refuses an intermediate overflow even when the exact sum is finite
        # an exact zero is +0.0 here, and fsum's choice of sign for zero is not the point
        def same(got, exp): return bits(got) == bits(exp) or (got == 0 and exp == 0)
        for label, got, exp in (('sum', w, want), ('split3', s3, want), ('inter5', i5, want), ('mean', m, wantm)):
            if not same(frombits(got), exp):
                bad += 1
                if bad < 10: print('MISMATCH case', i, label, 'n', len(xs), 'got', frombits(got), 'want', exp, xs[:4])
    print(f'{len(cs)} cases ({infs} overflow-to-inf), {fsum_checked} also against math.fsum ({fsum_overflow} where fsum itself raises OverflowError on a finite exact sum), {bad} mismatches, {time.time()-t:.1f}s')
    sys.exit(1 if bad else 0)
main()
