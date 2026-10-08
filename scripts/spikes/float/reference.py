#!/usr/bin/env python3
"""The reference for the float reader (docs/numbers.md section 4.2, docs/gap-float.md): (status, key) of a cell by Python's correctly rounded float(),
and the checksum `fbench C` computes.

    reference.py FILE            print the checksum of the file (one number), as `fbench C 1 < FILE` does
    reference.py --dump FILE     print "status key" per line, as `fbench D 1 < FILE` does

Rules (flt.cho): a cell longer than 1,100 bytes is status 13 (checked first); the grammar [+-]? digits? [. digits?] ([eE] [+-]? digits)? with at least one digit
in the mantissa, ASCII, else 9 (10 for inf/infinity/nan in any case with an optional sign); a mantissa of zeros is key 0, status 0 (so -0 is 0); a value
past the largest double is 11; a non-zero mantissa that reads as zero is 12; else the key is the double's bits, the low 63 flipped when negative."""
import re
import struct
import sys

M = (1 << 64) - 1
GRAMMAR = re.compile(rb"([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?)(\d+))?")
WORD = re.compile(rb"[+-]?(?:inf|infinity|nan)", re.I)


def cell(b):
    """(status, key) of the bytes `b` (a line without its newline)."""
    if len(b) > 1100:
        return 13, 0
    m = GRAMMAR.fullmatch(b)
    if m is None or not b.isascii() or (not m.group(2) and not m.group(3)):
        return (10, 0) if WORD.fullmatch(b) else (9, 0)
    if (m.group(2) or b"").strip(b"0") == b"" and (m.group(3) or b"").strip(b"0") == b"":
        return 0, 0
    x = float(b)
    if x in (float("inf"), float("-inf")):
        return 11, 0
    if x == 0.0:
        return 12, 0
    bits = struct.unpack("<q", struct.pack("<d", x))[0]
    if bits < 0:
        bits ^= 0x7FFFFFFFFFFFFFFF
    return 0, bits


def mix(x):
    x &= M
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & M
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & M
    x ^= x >> 31
    return x


def checksum(lines):
    s = 0
    for b in lines:
        st, key = cell(b)
        s = (s + mix((key & M) ^ mix(st + 1))) & M
    return s


def lines_of(path):
    with open(path, "rb") as f:
        data = f.read()
    ls = data.split(b"\n")
    if ls and ls[-1] == b"":
        ls.pop()
    return ls


if __name__ == "__main__":
    if sys.argv[1] == "--dump":
        for b in lines_of(sys.argv[2]):
            st, key = cell(b)
            print(st, key)
    else:
        s = checksum(lines_of(sys.argv[1]))
        print(s - (1 << 64) if s >> 63 else s)
