#!/usr/bin/env python3
"""The reference semantics of docs/numbers.md for one cell, in Python, and the table of edge cells with the answer
the design gives to each.  `python3 scripts/spikes/numbers_ref.py` checks the reference against the hand-written
expectations (the test spec) and, with --compare, prints what DuckDB, pandas, csvtk and Python's own Decimal/float
make of the same cells (the table in section 2 of the design is this output).

A cell is bytes; ASCII digits only.  The functions answer ("ok", value) or ("refuse", rule).
  dec(cell, scale): the cell as an integer number of 10^-scale, exactly, no rounding, never a float.
  flt(cell): the cell as the nearest double (Python's float() is correctly rounded: it is the oracle), no
             infinity, no NaN, no negative zero.
"""
import re, sys, math, subprocess, json, struct
from decimal import Decimal, InvalidOperation

DEC = re.compile(rb'^([+-]?)([0-9]*)(?:\.([0-9]*))?$')
FLT = re.compile(rb'^([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?)([0-9]+))?$')
MAX_FLOAT_CELL = 1100        # bytes; a halfway case has at most 767 significant digits (std.json docs/json.md 4.1)

def dec(cell: bytes, scale: int):
    m = DEC.match(cell)
    if not m or not ((m.group(2) or b'') + (m.group(3) or b'')):
        if re.match(rb'^[+-]?[0-9]*\.?[0-9]*[eE][+-]?[0-9]+$', cell) and re.search(rb'[0-9]', cell):
            return ("refuse", "value.not-decimal")        # an exponent: repair says :float
        return ("refuse", "value.not-decimal")
    sign, ip, fp = m.group(1), m.group(2) or b'', m.group(3) or b''
    if len(fp) > scale:
        return ("refuse", "value.decimal-scale")
    v = int((ip + fp + b'0' * (scale - len(fp))) or b'0')
    if v >= 10**18:
        return ("refuse", "value.decimal-too-wide")
    return ("ok", -v if sign == b'-' else v)

def flt(cell: bytes):
    if len(cell) > MAX_FLOAT_CELL:
        return ("refuse", "limit.number-too-long")
    low = cell.lower().lstrip(b'+-')
    if low in (b'inf', b'infinity', b'nan'):
        return ("refuse", "value.not-finite")
    m = FLT.match(cell)
    if not m or not ((m.group(2) or b'') + (m.group(3) or b'')):
        return ("refuse", "value.not-float")
    x = float(cell.decode())
    if math.isinf(x):
        return ("refuse", "value.float-range")
    if x == 0 and re.search(rb'[1-9]', (m.group(2) or b'') + (m.group(3) or b'')):
        return ("refuse", "value.float-range")            # a non-zero cell that rounds to zero
    return ("ok", 0.0 if x == 0 else x)                    # no negative zero

# cell, dec(2) expectation, float expectation.  This is the spec the tests of stage N1 and N3 are written from.
SPEC = [
    (b"1",        ("ok", 100),                          ("ok", 1.0)),
    (b"1.5",      ("ok", 150),                          ("ok", 1.5)),
    (b"1.50",     ("ok", 150),                          ("ok", 1.5)),
    (b"1.500",    ("refuse", "value.decimal-scale"),    ("ok", 1.5)),
    (b"1e3",      ("refuse", "value.not-decimal"),      ("ok", 1000.0)),
    (b"",         ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"NaN",      ("refuse", "value.not-decimal"),      ("refuse", "value.not-finite")),
    (b"inf",      ("refuse", "value.not-decimal"),      ("refuse", "value.not-finite")),
    (b"-Infinity", ("refuse", "value.not-decimal"),     ("refuse", "value.not-finite")),
    (b"-0",       ("ok", 0),                            ("ok", 0.0)),
    (b"-0.00",    ("ok", 0),                            ("ok", 0.0)),
    (b" 3 ",      ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"+5",       ("ok", 500),                          ("ok", 5.0)),
    (b"1,000",    ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"1_000",    ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b".5",       ("ok", 50),                           ("ok", 0.5)),
    (b"5.",       ("ok", 500),                          ("ok", 5.0)),
    (b".",        ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"+",        ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"-.5",      ("ok", -50),                          ("ok", -0.5)),
    (b"00012.30", ("ok", 1230),                         ("ok", 12.3)),
    (b"0x10",     ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"1e",       ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"1e+",      ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"e5",       ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"1.5E-3",   ("refuse", "value.not-decimal"),      ("ok", 0.0015)),
    (b"1e999",    ("refuse", "value.not-decimal"),      ("refuse", "value.float-range")),
    (b"1e-999",   ("refuse", "value.not-decimal"),      ("refuse", "value.float-range")),
    (b"0e999",    ("refuse", "value.not-decimal"),      ("ok", 0.0)),
    (b"4.9e-324", ("refuse", "value.not-decimal"),      ("ok", 5e-324)),
    (b"2.4703282292062327e-324", ("refuse", "value.not-decimal"), ("refuse", "value.float-range")),
    (b"1.7976931348623157e308", ("refuse", "value.not-decimal"), ("ok", 1.7976931348623157e308)),
    (b"1.7976931348623159e308", ("refuse", "value.not-decimal"), ("refuse", "value.float-range")),
    (b"9007199254740993", ("ok", 900719925474099300),   ("ok", 9007199254740992.0)),
    (b"9999999999999999.99", ("ok", 999999999999999999), ("ok", 1e16)),
    (b"10000000000000000.00", ("refuse", "value.decimal-too-wide"), ("ok", 1e16)),
    (b"0.1",      ("ok", 10),                           ("ok", 0.1)),
    (b"\xef\xbc\x91", ("refuse", "value.not-decimal"),  ("refuse", "value.not-float")),   # fullwidth 1
    (b"\xd9\xa1", ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),   # Arabic-indic 1
    (b"1\x00",    ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"--1",      ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"1.2.3",    ("refuse", "value.not-decimal"),      ("refuse", "value.not-float")),
    (b"0." + b"0" * 800 + b"1", ("refuse", "value.decimal-scale"), ("refuse", "value.float-range")),
    (b"1" + b"0" * 1200, ("refuse", "value.decimal-too-wide"), ("refuse", "limit.number-too-long")),
]


# ---- aggregates (the reference for stage N2 and N4) ------------------------------------------------------------
def dec_text(v: int, scale: int) -> str:
    """A scaled integer as text at its scale: always `scale` fractional digits, no exponent, `-` only when non-zero."""
    sign = "-" if v < 0 else ""
    d = str(abs(v)).rjust(scale + 1, "0")
    return sign + d[:len(d) - scale] + ("." + d[len(d) - scale:] if scale else "")

def dec_mean(values, scale: int, out_scale: int) -> int:
    """sum / count as an integer of 10^-out_scale, rounded half to even, exactly (no float anywhere)."""
    from fractions import Fraction
    q = Fraction(sum(values), len(values)) * Fraction(10) ** (out_scale - scale)
    f = q.numerator // q.denominator; r = q - f
    if r > Fraction(1, 2) or (r == Fraction(1, 2) and f % 2 == 1): f += 1
    return f

def float_sum(xs) -> float:
    """the exact sum of doubles rounded once to nearest even (what math.fsum computes; an overflow is a refusal)"""
    return math.fsum(xs)

def float_mean(xs) -> float:
    from fractions import Fraction
    return float(sum((Fraction(x) for x in xs), Fraction(0)) / len(xs))

def float_key(x: float) -> bytes:
    """the order-preserving 8 bytes of a double (no NaN, no -0 reach it): bytewise order is numeric order"""
    b = struct.unpack(">Q", struct.pack(">d", 0.0 if x == 0 else x))[0]
    b = b ^ (0xFFFFFFFFFFFFFFFF if b >> 63 else 0x8000000000000000)
    return b.to_bytes(8, "big")

def dec_key(v: int) -> bytes:
    return ((v + (1 << 63)) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")

def selfcheck_aggregates():
    import random
    from decimal import ROUND_HALF_EVEN, getcontext
    getcontext().prec = 60
    r = random.Random(2); bad = 0
    for _ in range(3000):
        S = r.randint(0, 6); vals = [r.randint(-10**r.randint(1, 12), 10**r.randint(1, 12)) for _ in range(r.randint(1, 9))]
        o = r.randint(0, 8)
        want = (Decimal(sum(vals)) / Decimal(len(vals)) / Decimal(10) ** (S - 0)).quantize(Decimal(1).scaleb(-o), rounding=ROUND_HALF_EVEN)
        got = dec_mean(vals, S, o)
        if Decimal(got).scaleb(-o) != want: bad += 1; print("MEAN", vals, S, o, got, want)
    xs = sorted(r.uniform(-1e6, 1e6) for _ in range(2000)) + [0.0, 5e-324, -5e-324, 1.5, -1.5]
    ks = sorted(xs, key=float_key)
    if ks != sorted(xs): bad += 1; print("float_key order")
    vs = [r.randint(-2**63, 2**63 - 1) for _ in range(2000)]
    if sorted(vs, key=dec_key) != sorted(vs): bad += 1; print("dec_key order")
    print(f"aggregate references: {bad} problems")
    return bad

def check():
    bad = 0
    for cell, d, f in SPEC:
        gd, gf = dec(cell, 2), flt(cell)
        if gd != d or gf != f:
            bad += 1
            print("SPEC FAILS", cell[:40], "dec", gd, "want", d, "| float", gf, "want", f)
    print(f"{len(SPEC)} edge cells, {bad} reference/spec disagreements")
    return bad

def compare():
    import tempfile, os, csv
    import pandas as pd
    cells = [c for c, _, _ in SPEC if len(c) < 40 and b"\x00" not in c and b"\n" not in c]
    def duck(cell):
        lit = cell.decode("utf-8", "replace").replace("'", "''")
        q = f"select coalesce(try_cast('{lit}' as decimal(18,2))::varchar,'NULL') || ' | ' || coalesce(try_cast('{lit}' as double)::varchar,'NULL')"
        r = subprocess.run(["duckdb", "-noheader", "-list", "-c", q], capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()[:30]
    def csvtk(cell):
        p = subprocess.run(["csvtk", "summary", "-f", "x:sum", "-w", "17"], input=b"x\n" + cell + b"\n", capture_output=True)
        o = p.stdout.decode().strip().split("\n")[-1] if p.returncode == 0 else "ERROR " + p.stderr.decode().split("]")[-1].strip()[:28]
        return o
    def py(cell):
        s = cell.decode("utf-8", "replace")
        try: a = repr(float(s))
        except ValueError: a = "ValueError"
        try: b = str(Decimal(s))
        except InvalidOperation: b = "InvalidOperation"
        return f"float()={a}  Decimal()={b}"
    def pdv(cell):
        df = pd.read_csv(__import__("io").BytesIO(b"id,x\n1,x\n2," + (b'"' + cell + b'"' if b"," in cell else cell) + b"\n"), dtype=str, keep_default_na=False)
        try: return repr(float(df.x[1]))
        except Exception: return "float() error"
    print(f"{'cell':26} | {'design dec(2)':26} | {'design float':28} | duckdb dec(18,2) | double | csvtk sum | python")
    for cell, d, f in SPEC:
        if cell not in cells: continue
        print(f"{cell.decode('utf-8','replace')!r:26} | {str(d[1]):26} | {str(f[1]):28} | {duck(cell)} | {csvtk(cell)} | {py(cell)}")

if __name__ == "__main__":
    rc = check() + selfcheck_aggregates()
    if "--compare" in sys.argv: compare()
    sys.exit(1 if rc else 0)
