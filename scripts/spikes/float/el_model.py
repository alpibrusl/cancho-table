"""Python model of eisel_lemire() of tools/table/flt.cho, for debugging (same steps, unsigned 64-bit words)."""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pow5_table import entry
M = (1 << 64) - 1


def el(q, w):
    if q < -342:
        return 0
    if q > 308:
        return 0x7FF0000000000000
    lz = 64 - w.bit_length()
    w = (w << lz) & M
    T = entry(q)
    th, tl = T >> 64, T & M
    p = w * th
    lo, hi = p & M, p >> 64
    if hi & 511 == 511:
        s = w * tl
        sh = s >> 64
        nl = (lo + sh) & M
        if sh > nl:
            hi = (hi + 1) & M
        lo = nl
    upper = hi >> 63
    mant = hi >> (upper + 9)
    power2 = ((217706 * q) >> 16) + 63 + upper - lz + 1023
    if power2 <= 0:
        if 1 - power2 >= 64:
            return 0
        mant >>= 1 - power2
        mant += mant & 1
        return mant >> 1
    if lo <= 1 and -4 <= q <= 23 and mant & 3 == 1:
        if (mant << (upper + 9)) & M == hi:
            mant -= 1
    mant += mant & 1
    mant >>= 1
    if mant >= 1 << 53:
        mant = 1 << 52
        power2 += 1
    if power2 >= 2047:
        return 0x7FF0000000000000
    return (mant & ((1 << 52) - 1)) + (power2 << 52)


if __name__ == "__main__":
    import struct
    for s in sys.argv[1:]:
        d, _, e = s.partition("e")
        e = int(e or 0)
        if "." in d:
            a, b = d.split(".")
            w, q = int(a + b), e - len(b)
        else:
            w, q = int(d), e
        got = el(q, w)
        ref = struct.unpack("<Q", struct.pack("<d", float(s)))[0]
        print(s, hex(got), hex(ref), "OK" if got == ref else "MISMATCH")
