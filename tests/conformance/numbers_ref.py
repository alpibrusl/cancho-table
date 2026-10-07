"""The reference semantics of docs/numbers.md for a decimal cell, in Python, independent of the tool, and the table of
edge cells with the answer the design gives to each (the test spec of stages N1 and N3).

    dec(cell, scale)  ->  ("ok", scaled_integer) | ("refuse", rule)

A cell is a `str` whose characters are bytes (latin-1, as the tests read files). The grammar is ASCII only (Python's
own `Decimal` and `float` accept spaces, underscores and non-ASCII digits, so neither is the oracle for it); the value is
Python's exact `int` arithmetic, and the order of the checks is the design's: the whole cell against the grammar, then
the number of fractional digits against the scale, then the width.

The float half of SPEC is read by stage N3a: `flt(cell)`, `float_text(x)` and `float_key(x)` below.
"""

import math
import re
import struct
from decimal import Decimal
from fractions import Fraction

DEC = re.compile(r"^([+-]?)([0-9]*)(?:\.([0-9]*))?$")
LIMIT = 10 ** 18


def dec(cell, scale):
    m = DEC.match(cell)
    if not m or not ((m.group(2) or "") + (m.group(3) or "")):
        return ("refuse", "value.not-decimal")
    sign, ip, fp = m.group(1), m.group(2) or "", m.group(3) or ""
    if len(fp) > scale:
        return ("refuse", "value.decimal-scale")
    v = int((ip + fp + "0" * (scale - len(fp))) or "0")
    if v >= LIMIT:
        return ("refuse", "value.decimal-too-wide")
    return ("ok", -v if sign == "-" else v)


def text(v, scale):
    """A scaled integer as text at its scale: exactly `scale` fractional digits, `-` only when non-zero."""
    sign = "-" if v < 0 else ""
    d = str(abs(v)).rjust(scale + 1, "0")
    return sign + d[:len(d) - scale] + ("." + d[len(d) - scale:] if scale else "")


# cell, expectation at :dec(2), then at :float (N3).
SPEC = [
    ("1", ("ok", 100), ("ok", 1.0)),
    ("1.5", ("ok", 150), ("ok", 1.5)),
    ("1.50", ("ok", 150), ("ok", 1.5)),
    ("1.500", ("refuse", "value.decimal-scale"), ("ok", 1.5)),
    ("1e3", ("refuse", "value.not-decimal"), ("ok", 1000.0)),
    ("", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("NaN", ("refuse", "value.not-decimal"), ("refuse", "value.not-finite")),
    ("inf", ("refuse", "value.not-decimal"), ("refuse", "value.not-finite")),
    ("-Infinity", ("refuse", "value.not-decimal"), ("refuse", "value.not-finite")),
    ("-0", ("ok", 0), ("ok", 0.0)),
    ("-0.00", ("ok", 0), ("ok", 0.0)),
    (" 3 ", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("+5", ("ok", 500), ("ok", 5.0)),
    ("1,000", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("1_000", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    (".5", ("ok", 50), ("ok", 0.5)),
    ("5.", ("ok", 500), ("ok", 5.0)),
    (".", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("+", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("-.5", ("ok", -50), ("ok", -0.5)),
    ("00012.30", ("ok", 1230), ("ok", 12.3)),
    ("0x10", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("1e", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("1e+", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("e5", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("1.5E-3", ("refuse", "value.not-decimal"), ("ok", 0.0015)),
    ("1e999", ("refuse", "value.not-decimal"), ("refuse", "value.float-range")),
    ("1e-999", ("refuse", "value.not-decimal"), ("refuse", "value.float-range")),
    ("0e999", ("refuse", "value.not-decimal"), ("ok", 0.0)),
    ("9007199254740993", ("ok", 900719925474099300), ("ok", 9007199254740992.0)),
    ("9999999999999999.99", ("ok", 999999999999999999), ("ok", 1e16)),
    ("10000000000000000.00", ("refuse", "value.decimal-too-wide"), ("ok", 1e16)),
    ("0.1", ("ok", 10), ("ok", 0.1)),
    ("\xef\xbc\x91", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),   # fullwidth 1, as UTF-8 bytes
    ("\xd9\xa1", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),       # Arabic-indic 1
    ("--1", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("1.2.3", ("refuse", "value.not-decimal"), ("refuse", "value.not-float")),
    ("0." + "0" * 800 + "1", ("refuse", "value.decimal-scale"), ("refuse", "value.float-range")),
    ("1" + "0" * 1200, ("refuse", "value.decimal-too-wide"), ("refuse", "limit.number-too-long")),
    ("0" * 40 + "7.25", ("ok", 725), ("ok", 7.25)),
    ("99999999999999999999.5", ("refuse", "value.decimal-too-wide"), ("ok", 1e20)),
    ("-9999999999999999.99", ("ok", -999999999999999999), ("ok", -1e16)),
]


# ---- aggregates (stage N2) ----------------------------------------------------------------------------------------

def mean_scaled(values, scale, out):
    """The exact mean of scaled integers (at `scale`) as an integer of 10^-out, rounded half to even: nothing is a float."""
    q = Fraction(sum(values), len(values)) * Fraction(10) ** (out - scale)
    f = q.numerator // q.denominator
    r = q - f
    if r > Fraction(1, 2) or (r == Fraction(1, 2) and f % 2 == 1):
        f += 1
    return f


def mean_text(values, scale, out):
    """The mean as the tool writes it: at `out` fractional digits, `-` only for a value that is not zero."""
    v = mean_scaled(values, scale, out)
    return text(v, out)


def sum_text(values, scale):
    return text(sum(values), scale)


def mean_key(values, count):
    return Fraction(sum(values), count)


# ---- floats (stage N3a) --------------------------------------------------------------------------------------------------

FLT = re.compile(r"^([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?)([0-9]+))?$")
MAX_FLOAT_CELL = 1100


def flt(cell):
    """("ok", x) | ("refuse", rule, direction): the nearest double (Python's float() is correctly rounded), no infinity, no NaN, no negative zero.
    The order of the checks is the design's: the length, then the grammar (a word `inf`, `infinity`, `nan` is `not-finite`), then the range."""
    if len(cell) > MAX_FLOAT_CELL:
        return ("refuse", "limit.number-too-long", None)
    m = FLT.match(cell)
    if not m or not ((m.group(2) or "") + (m.group(3) or "")):
        if cell.lower().lstrip("+-") in ("inf", "infinity", "nan"):
            return ("refuse", "value.not-finite", None)
        return ("refuse", "value.not-float", None)
    x = float(cell)
    if math.isinf(x):
        return ("refuse", "value.float-range", "overflow")
    if x == 0 and re.search(r"[1-9]", (m.group(2) or "") + (m.group(3) or "")):
        return ("refuse", "value.float-range", "underflow")
    return ("ok", 0.0 if x == 0 else x)


def float_key(x):
    """The signed integer whose order is the numeric order of the double (-0 is 0): what min, max and the comparisons keep."""
    b = struct.unpack(">q", struct.pack(">d", 0.0 if x == 0 else x))[0]
    return b ^ 0x7FFFFFFFFFFFFFFF if b < 0 else b


def float_text(x):
    """The shortest decimal that reads back to the same double, positional from 1e-6 to 1e21 and always with a point (`3.0`), scientific outside
    (`1e21`, `1.5e-7`): docs/numbers.md 4.6. Digits from Python's repr (also shortest round trip), laid out by the rule."""
    if x == 0:
        return "0.0"
    sign = "-" if x < 0 else ""
    t = Decimal(repr(abs(x))).as_tuple()
    digits = "".join(map(str, t.digits)).rstrip("0") or "0"
    k = len(t.digits) + t.exponent - 1             # the decimal exponent of the first digit
    if k >= 21 or k < -6:
        return sign + digits[0] + ("." + digits[1:] if len(digits) > 1 else "") + "e" + str(k)
    pos = k + 1                                     # digits before the point
    if pos <= 0:
        return sign + "0." + "0" * (-pos) + digits
    if pos >= len(digits):
        return sign + digits + "0" * (pos - len(digits)) + ".0"
    return sign + digits[:pos] + "." + digits[pos:]


def same_text(tool, x):
    """Whether `tool` is a legitimate printing of the double `x`: the layout of `float_text`, or the same layout of the other shortest digit string when two
    shortest strings are equally close to the double (`87992730773886.125` is `...86.12` in Python and `...86.13` in the tool: both read back to it; the
    rule of 4.6 is "shortest that reads back", and a tie between two is not specified)."""
    want = float_text(x)
    if tool == want:
        return True
    if x == 0 or len(tool) != len(want) or float(tool) != x:
        return False
    return sum(a != b for a, b in zip(tool, want)) <= 1          # the same layout, and one digit apart: the tie
