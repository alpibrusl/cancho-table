#!/usr/bin/env python3
"""SPIKE: the carry threshold.  Adds one double R times with the all-ones mantissa (every limb piece at its largest)
and compares with R * x exactly.  usage: rep_check.py BINARY [R]"""
import struct, subprocess, sys
from fractions import Fraction
b = sys.argv[1]; R = int(sys.argv[2]) if len(sys.argv) > 2 else 1_200_000_000
def bits(x): return struct.unpack('<q', struct.pack('<d', x))[0]
bad = 0
for e in (32, 500):
    x = struct.unpack('<d', struct.pack('<Q', (e << 52) | ((1 << 52) - 1)))[0]
    out = subprocess.run([b, 'acc', str(R)], input=('[%r]' % x).encode(), capture_output=True)
    want = float(Fraction(x) * R)
    got = int(out.stdout.split()[0]) if out.returncode == 0 and out.stdout else None
    print(f"e={e} R={R}: rc={out.returncode} {'ok' if got == bits(want) else 'MISMATCH/crash'}")
    if got != bits(want): bad += 1
sys.exit(1 if bad else 0)
