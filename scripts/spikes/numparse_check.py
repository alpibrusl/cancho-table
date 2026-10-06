#!/usr/bin/env python3
"""SPIKE check: the dec and float cell readers of numparse.ls against numbers_ref.py (the design's reference, which
rests on Python's exact int/Decimal arithmetic and its correctly rounded float()) over the edge cells and
generated cells, including grammar fuzz.

    python3 scripts/spikes/numparse_check.py BINARY [--n N] [--seed S]

dec: the status must match (ok / not-decimal / decimal-scale / decimal-too-wide) and the value; float: the scanner
answers 0 (decided by Clinger's path: bits must equal float()), 4 (valid, not decided: float() must accept it and the
cell goes to the exact reader) or 1 (not a number: reference must refuse).
"""
import argparse, random, struct, subprocess, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numbers_ref as ref
TAG = {0: None, 1: "value.not-decimal", 2: "value.decimal-too-wide", 3: "value.decimal-scale"}
def bits(x): return struct.unpack('<q', struct.pack('<d', x))[0]
def cells(r, n):
    c = [x[0] for x in ref.SPEC if b"\n" not in x[0] and b"\x00" not in x[0] and len(x[0]) < 200]
    alpha = b"0123456789+-. eE_,x"
    for _ in range(n):
        k = r.random()
        if k < .35:    # grammar fuzz
            c.append(bytes(r.choice(alpha) for _ in range(r.randint(0, 14))))
        elif k < .6:   # decimals around the 18-digit and scale limits
            ip = r.randint(0, 10**r.randint(0, 20)); fp = r.randint(0, 10**r.randint(0, 5)) if r.random() < .7 else None
            c.append((r.choice([b"", b"-", b"+"]) + str(ip).encode() + (b"." + str(fp).zfill(r.randint(0, 5)).encode() if fp is not None else b"")))
        elif k < .8:   # floats with exponents, at the Clinger limits
            m = r.randint(0, 10**r.randint(1, 19)); e = r.randint(-30, 30)
            c.append(f"{r.choice(['', '-', '+'])}{m}e{e}".encode() if r.random() < .6 else f"{m / 10**r.randint(0, 6)!r}".encode())
        else:          # shortest reprs of random doubles (17 digits: never decided by the fast path)
            x = struct.unpack('<d', struct.pack('<Q', r.getrandbits(64)))[0]
            c.append(repr(x if x == x and abs(x) != float("inf") else 1.5).encode() if r.random() < .5 else repr(r.uniform(-1e5, 1e5)).encode())
    return [x for x in c if b"\n" not in x]
def run(binary, mode, cs, scale=None):
    argv = [binary, mode] + ([str(scale)] if scale is not None else ["1"])
    p = subprocess.run(argv, input=b"\n".join(cs) + b"\n", capture_output=True)
    if p.returncode: print("binary failed", p.returncode, p.stderr[:200]); sys.exit(2)
    return [tuple(map(int, l.split())) for l in p.stdout.decode().split("\n")[:-1]]
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("binary"); ap.add_argument("--n", type=int, default=200000); ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(); r = random.Random(a.seed); cs = cells(r, a.n)
    bad = 0; decided = undecided = refused = 0
    for scale in (0, 2, 5, 18):
        out = run(a.binary, "D", cs, scale)
        assert len(out) == len(cs), (len(out), len(cs))
        for cell, (st, v) in zip(cs, out):
            want = ref.dec(cell, scale)
            got = ("ok", v) if st == 0 else ("refuse", TAG[st])
            if want != got:
                bad += 1
                if bad < 15: print("DEC MISMATCH scale", scale, cell[:50], "got", got, "want", want)
    out = run(a.binary, "F", cs)
    for cell, (st, v) in zip(cs, out):
        want = ref.flt(cell)
        if st == 1:
            refused += 1
            if want[0] != "refuse":
                bad += 1; print("FLOAT: scanner refuses what the reference accepts", cell[:50], want)
        elif st == 4:
            undecided += 1
            if want[0] != "ok" and want[1] not in ("value.float-range",):
                bad += 1; print("FLOAT: undecided but reference refuses", cell[:50], want)
        else:
            decided += 1
            if want[0] != "ok" or bits(want[1]) != v:       # +0.0 is bits 0: there is no negative zero
                bad += 1
                if bad < 15: print("FLOAT MISMATCH", cell[:50], "got bits", v, "want", want)
    print(f"{len(cs)} cells: dec at 4 scales, float ({decided} decided by Clinger, {undecided} undecided, {refused} refused): {bad} mismatches")
    sys.exit(1 if bad else 0)
main()
