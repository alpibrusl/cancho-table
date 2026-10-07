"""The reference semantics of docs/numbers.md for a decimal cell, in Python, independent of the tool, and the table of
edge cells with the answer the design gives to each (the test spec of stages N1 and N3).

    dec(cell, scale)  ->  ("ok", scaled_integer) | ("refuse", rule)

A cell is a `str` whose characters are bytes (latin-1, as the tests read files). The grammar is ASCII only (Python's
own `Decimal` and `float` accept spaces, underscores and non-ASCII digits, so neither is the oracle for it); the value is
Python's exact `int` arithmetic, and the order of the checks is the design's: the whole cell against the grammar, then
the number of fractional digits against the scale, then the width.

The float half of SPEC is kept for stage N3 (`:float` is not built); the tests of N1 read only the decimal half.
"""

import re
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
