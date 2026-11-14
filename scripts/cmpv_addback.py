#!/usr/bin/env python3
"""The operands of ADD_BACK in scripts/cmpv_golden.py: pairs (a, b) for which Knuth's algorithm D (TAOCP 4.3.1) in base 2^30 must add back.

    python3 scripts/cmpv_addback.py --check     # fail if ADD_BACK is not what the search below finds
    python3 scripts/cmpv_addback.py             # print it

A model of the algorithm as tools/table/cmpv.cho writes it (`divmod_mag`) is run on operands whose limbs are drawn from 0, 1, 2, 3, 2^28, 2^29 - 1, 2^29, 2^29 + 1,
2^30 - 3, 2^30 - 2 and 2^30 - 1 (divisors of 2 to 4 limbs, dividends of as many or up to 2 more, both below 2^127); a pair is kept when the model's subtraction goes
negative once and the quotient limb has to be put back. Random operands reach that about once in 2^29 digits. The first 40 pairs of a fixed seed are the list.
"""
import ast, pathlib, random, re, sys

B = 1 << 30
M = B - 1


def limbs(x):
    out = []
    while x:
        out.append(x & M)
        x >>= 30
    return out or [0]


def adds_back(a, b):
    v, u = limbs(b), limbs(a)
    nd, nn = len(v), len(u)
    if nn < nd or nd < 2:
        return False
    shift, top = 0, v[-1]
    while top < (1 << 29):
        top *= 2
        shift += 1
    vn = limbs(b << shift) + [0] * nd
    vn = vn[:nd]
    un = (limbs(a << shift) + [0] * (nn + 1))[:nn + 1]
    hit = False
    for j in range(nn - nd, -1, -1):
        num = un[j + nd] * B + un[j + nd - 1]
        qhat = num // vn[nd - 1]
        rhat = num - qhat * vn[nd - 1]
        while qhat >= B or qhat * vn[nd - 2] > rhat * B + un[j + nd - 2]:
            qhat -= 1
            rhat += vn[nd - 1]
            if rhat >= B:
                break
        borrow = mc = 0
        for i in range(nd):
            p = qhat * vn[i] + mc
            mc = p >> 30
            t = un[j + i] - (p & M) - borrow
            borrow = 1 if t < 0 else 0
            un[j + i] = t + B if t < 0 else t
        t2 = un[j + nd] - mc - borrow
        if t2 < 0:
            hit = True
            t2 += B
            c = 0
            for i in range(nd):
                s = un[j + i] + vn[i] + c
                un[j + i] = s & M
                c = s >> 30
            un[j + nd] = (t2 + c) & M
        else:
            un[j + nd] = t2
    return hit


def find(count=40, seed=5):
    rng = random.Random(seed)
    pool = [0, 1, 2, M, M - 1, B // 2, B // 2 - 1, B // 2 + 1, B // 4, 3, M - 2]
    out = []
    while len(out) < count:
        nd = rng.choice([2, 3, 4])
        nn = nd + rng.choice([0, 1, 2])
        v = [rng.choice(pool) for _ in range(nd)]
        if v[-1] == 0:
            v[-1] = rng.choice([1, B // 2, M])
        u = [rng.choice(pool) for _ in range(nn)]
        if u[-1] == 0:
            u[-1] = rng.choice([1, B // 2, M])
        a = sum(x << (30 * i) for i, x in enumerate(u))
        b = sum(x << (30 * i) for i, x in enumerate(v))
        if a < (1 << 127) and 0 < b < (1 << 127) and adds_back(a, b):
            out.append((a, b))
    return out


if __name__ == "__main__":
    found = find()
    if "--check" in sys.argv:
        src = (pathlib.Path(__file__).resolve().parent / "cmpv_golden.py").read_text()
        listed = ast.literal_eval(re.search(r"^ADD_BACK = (\[.*\])$", src, re.M).group(1))
        if [tuple(x) for x in listed] != found:
            sys.exit("ADD_BACK in cmpv_golden.py is not what scripts/cmpv_addback.py finds")
        print(f"ADD_BACK: the {len(found)} pairs are what the search finds")
    else:
        print(found)
