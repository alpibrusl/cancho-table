"""A reference for `:date`, `:timestamp` and `:datetime` (docs/datetime.md sections 5, 6 and 10), sharing no code with the tool.

It parses the grammar by hand, validates the calendar with Python's `datetime` and `calendar` (fixed offsets only: there is no `zoneinfo` here and no zone database),
computes the instant with `datetime` and `timedelta`, and decides the refusal. The order of the checks is the doc's (section 5, "The order of checks"):

    (1) the whole cell against the shape (positions of digits and separators, the form of the offset, a named zone);
    (2) year, month, day against the calendar;                 (3) hour, minute, second; the digits of an offset;
    (4) the number of fractional digits;                       (5) the offset's presence against the type;
    (6) the instant against the range 0001-01-01T00:00:00Z .. 9999-12-31T23:59:59.999999Z (a `:timestamp`).

The tag follows the first failing step. A refusal is ("refuse", tag, detail) with `detail` holding what the tool's `detail` holds besides the position of the cell
(`reason` and `offset` for value.not-date and value.not-timestamp, `digits`, `direction`, `reason`). A value is ("ok", i64): days since 1970-01-01 for a date,
microseconds since 1970-01-01T00:00:00 (UTC for a timestamp, a wall-clock count for a datetime) otherwise.

The narrow readings of the doc's gaps (the offset of a shape fault, what a time part is in a `:date`, the out-of-range digits of an offset) are the ones written in
"T1 as built" in docs/datetime.md; each is a line of this file.
"""

import calendar
import datetime as dt
import random
import re

EPOCH = dt.datetime(1970, 1, 1)
US = dt.timedelta(microseconds=1)
MIN_US = (dt.datetime(1, 1, 1) - EPOCH) // US
MAX_US = (dt.datetime(9999, 12, 31, 23, 59, 59, 999999) - EPOCH) // US
MIN_DAY = (dt.date(1, 1, 1) - EPOCH.date()).days
MAX_DAY = (dt.date(9999, 12, 31) - EPOCH.date()).days

KINDS = ("date", "timestamp", "datetime")
TAGS = ("value.not-date", "value.not-timestamp", "value.timestamp-precision", "value.timestamp-range", "value.timezone-missing", "value.timestamp-offset-present",
        "value.timezone-unsupported")

_DIGIT = set(b"0123456789")
_SEP = set(b"Tt ")
_NAME = re.compile(rb"^ ?[A-Za-z][A-Za-z0-9_/+:\-]*$")
_BRACKET = re.compile(rb"^([+-][0-9]{2}:[0-9]{2}|[Zz])\[")
_NUMOFF = re.compile(rb"^[+-][0-9]{2}:[0-9]{2}$")


def _scan(b):
    """Step (1) for a cell that is not just a date. ("fail", reason, offset) for a fault of shape, ("tag", tag, reason, offset) for a fault with a tag of its own, or
    ("ok", frac_digits, tail_kind, tail_start, tail) with tail_kind one of none, utc, num."""
    n = len(b)
    pat = "dddd-dd-dd"
    for i, ch in enumerate(pat):
        if i >= n:
            return ("fail", "shape", n)
        if (ch == "d") != (b[i] in _DIGIT) or (ch == "-" and b[i] != 0x2D):
            return ("fail", "shape", i)
    if n == 10:
        return ("date-only", 10)
    if b[10] not in _SEP:
        return ("fail", "shape", 10)
    pat = "dd:dd:dd"
    for i, ch in enumerate(pat):
        at = 11 + i
        if at >= n:
            return ("fail", "shape", n)
        if (ch == "d") != (b[at] in _DIGIT) or (ch == ":" and b[at] != 0x3A):
            return ("fail", "shape", at)
    k = 19
    frac = 0
    if k < n and b[k] == 0x2E:
        j = 20
        while j < n and b[j] in _DIGIT:
            j += 1
        if j == 20:
            return ("fail", "shape", 20)
        frac = j - 20
        k = j
    tail = b[k:]
    if tail == b"":
        return ("ok", frac, "none", k, tail)
    if tail in (b"Z", b"z"):
        return ("ok", frac, "utc", k, tail)
    if _NUMOFF.match(tail):
        return ("ok", frac, "num", k, tail)
    if _BRACKET.match(tail) or tail[:1] == b"[" or (_NAME.match(tail) and tail not in (b"Z", b"z")):
        return ("tag", "value.timezone-unsupported", None, k)
    if tail[:1] in (b"+", b"-"):
        return ("fail", "offset-shape", k)
    if tail[:1] in (b"Z", b"z"):
        return ("fail", "shape", k + 1)
    return ("fail", "shape", k)


def _refuse(tag, **detail):
    return ("refuse", tag, detail)


def parse(kind, cell):
    """The reading of `cell` (bytes) as `kind` ('date', 'timestamp' or 'datetime'): ("ok", value) or ("refuse", tag, detail)."""
    b = bytes(cell)
    n = len(b)
    if kind == "date":
        return _parse_date(b, n)
    not_ts = "value.not-timestamp"
    s = _scan(b)
    if s[0] == "date-only":
        return _refuse(not_ts, reason="date-only", offset=10)
    if s[0] == "fail":
        return _refuse(not_ts, reason=s[1], offset=s[2])
    if s[0] == "tag":
        return _refuse(s[1])
    _, frac, tail_kind, k, tail = s
    year, month, day = int(b[0:4]), int(b[5:7]), int(b[8:10])
    hh, mi, ss = int(b[11:13]), int(b[14:16]), int(b[17:19])
    bad = _calendar(year, month, day, not_ts)
    if bad:
        return bad
    if hh == 24:
        return _refuse(not_ts, reason="hour-24", offset=11)
    if hh > 23:
        return _refuse(not_ts, reason="hour", offset=11)
    if mi > 59:
        return _refuse(not_ts, reason="minute", offset=14)
    if ss == 60:
        return _refuse(not_ts, reason="leap-second", offset=17)
    if ss > 60:
        return _refuse(not_ts, reason="second", offset=17)
    offset_us = 0
    if tail_kind == "num":
        oh, om = int(tail[1:3]), int(tail[4:6])
        if oh > 23 or om > 59:
            return _refuse(not_ts, reason="offset-shape", offset=k)
        offset_us = (oh * 3600 + om * 60) * 1000000 * (1 if tail[0] == 0x2B else -1)
    if frac > 6:
        return _refuse("value.timestamp-precision", digits=frac)
    if kind == "timestamp":
        if tail_kind == "none":
            return _refuse("value.timezone-missing", reason="missing")
        if tail == b"-00:00":
            return _refuse("value.timezone-missing", reason="unknown-offset")
    elif tail_kind != "none":
        return _refuse("value.timestamp-offset-present")
    micro = int((b[20:20 + frac] + b"000000")[:6]) if frac else 0
    naive = dt.datetime(year, month, day, hh, mi, ss, micro)
    value = (naive - EPOCH) // US
    if kind == "timestamp":
        value -= offset_us
        if value < MIN_US:
            return _refuse("value.timestamp-range", direction="before")
        if value > MAX_US:
            return _refuse("value.timestamp-range", direction="after")
    return ("ok", value)


def _calendar(year, month, day, tag):
    if year == 0:
        return _refuse(tag, reason="year", offset=0)
    if month < 1 or month > 12:
        return _refuse(tag, reason="month", offset=5)
    if day < 1 or day > calendar.monthrange(year, month)[1]:
        return _refuse(tag, reason="day", offset=8)
    return None


def _parse_date(b, n):
    tag = "value.not-date"
    pat = "dddd-dd-dd"
    for i, ch in enumerate(pat):
        if i >= n:
            return _refuse(tag, reason="shape", offset=n)
        if (ch == "d") != (b[i] in _DIGIT) or (ch == "-" and b[i] != 0x2D):
            return _refuse(tag, reason="shape", offset=i)
    if n > 10:
        # a time part: the cell is a valid :timestamp (it has an offset) or a valid :datetime (it has none); a repair that reads the cell as one of them is then accepted
        if parse("timestamp", b)[0] == "ok" or parse("datetime", b)[0] == "ok":
            return _refuse(tag, reason="time-part", offset=10)
        s = _scan(b)
        if s[0] == "fail":
            return _refuse(tag, reason="shape", offset=s[2])
        if s[0] == "tag":
            return _refuse(tag, reason="shape", offset=s[3])
        return _refuse(tag, reason="shape", offset=10)
    bad = _calendar(int(b[0:4]), int(b[5:7]), int(b[8:10]), tag)
    if bad:
        return bad
    return ("ok", (dt.date(int(b[0:4]), int(b[5:7]), int(b[8:10])) - EPOCH.date()).days)


def canon(kind, value):
    """The normalised form of a value (section 8): YYYY-MM-DD; YYYY-MM-DDThh:mm:ss[.fff[fff]]Z in UTC; the datetime without the Z."""
    if kind == "date":
        d = EPOCH.date() + dt.timedelta(days=value)
        return "%04d-%02d-%02d" % (d.year, d.month, d.day)
    days, rest = divmod(value, 86400 * 1000000)
    d = EPOCH.date() + dt.timedelta(days=days)
    secs, micro = divmod(rest, 1000000)
    text = "%04d-%02d-%02dT%02d:%02d:%02d" % (d.year, d.month, d.day, secs // 3600, secs // 60 % 60, secs % 60)
    if micro % 1000000 == 0:
        pass
    elif micro % 1000 == 0:
        text += ".%03d" % (micro // 1000)
    else:
        text += ".%06d" % micro
    return text + ("Z" if kind == "timestamp" else "")


def literal_ok(kind, text):
    """A literal of a condition is read by the cell's reader (section 4.2): it is a value or it is not."""
    return parse(kind, text.encode("utf-8"))[0] == "ok"


# ---- the cells ---------------------------------------------------------------------------------------------------------

LEAP = [1900, 2000, 2100, 2400, 2024, 2026, 1600, 1700, 1800, 2023, 2004, 1, 9999, 4, 100, 400, 1996, 2096, 2200, 2300]
OFFSETS = ["Z", "z", "+00:00", "-00:00", "+14:00", "-12:00", "+23:59", "-23:59", "+01:00", "-01:00", "+05:30", "-03:30", "+02:00", "+09:45", "+24:00", "+00:60", "-00:01"]
SEPS = ["T", "t", " "]


def month_ends():
    out = []
    for y in LEAP:
        for m in range(1, 13):
            last = calendar.monthrange(y, m)[1] if 1 <= y <= 9999 else 28
            out += ["%04d-%02d-%02d" % (y, m, last), "%04d-%02d-%02d" % (y, m, last + 1), "%04d-%02d-01" % (y, m)]
    return out


def edge_cells():
    """The named cases of the doc's G-ORACLE-GRAMMAR: leap days, month ends, offsets, 24:00, :60, the year ends and their offset overflows, 0 to 9 fractional digits,
    whitespace, lowercase, a space for the T, the basic, week and ordinal forms, empty, a lone sign, multibyte digits, named zones."""
    cells = ["", " ", "-", "+", "Z", "T", "2026", "2026-01", "2026-1-2", "2026-01-2", "20260102", "20260102T010000Z", "2026-W01-1", "2026-002", "infinity", "epoch", "-infinity",
             "0000-01-01", "0000-01-01T00:00:00Z", "10000-01-01", "10000-01-01T00:00:00Z", "+2026-01-02", "-2026-01-02", "٢٠٢٦-٠١-٠٢", "２０２６-０１-０２", "2026-01-02 ", "2026‑01‑02"]
    cells += month_ends()
    # the same dates with a time, a zone and a fraction: every fault of the calendar reached through the reader of a timestamp and of a datetime
    for y in (1900, 2000, 2024, 2026, 2100):
        for m in list(range(0, 14)):
            for d in (0, 1, 28, 29, 30, 31, 32):
                day = "%04d-%02d-%02d" % (y, m, d)
                cells += [day + "T00:00:00Z", day + " 01:02:03.5"]
    cells += ["2026-02-30", "2026-04-31", "1900-02-29", "2000-02-29", "2100-02-29", "2400-02-29", "2024-02-29", "2026-02-29", "2026-13-01", "2026-00-10", "2026-01-00", "2026-01-32",
              "0001-01-01", "9999-12-31", "0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z", "0001-01-01T00:00:00+01:00", "0001-01-01T00:00:00+00:00", "0001-01-01T00:00:00-01:00",
              "9999-12-31T23:59:59.999999-01:00", "9999-12-31T23:59:59.999999+01:00", "9999-12-31T23:59:59Z", "9999-12-31T23:59:59+14:00", "9999-12-31T23:59:59-12:00",
              "0001-01-01T14:00:00+14:00", "0001-01-01T13:59:59+14:00", "0001-01-01T12:00:00-12:00", "0001-01-01T00:00:00.000000+00:00", "9999-12-31T09:59:59.999999-14:00",
              "9999-12-31T10:00:00-14:00", "0001-01-02T00:00:00+24:00", "0001-01-01T23:59:59+23:59"]
    # the ends of the range at offsets of hours and minutes: the first instant, the one before it, the last, the one after it, written in each offset (when the local year exists)
    for off in ("+00:30", "-00:30", "+05:30", "-05:30", "+09:45", "-03:30", "+14:00", "-12:00", "+23:59", "-23:59", "+00:01", "-00:01", "+01:01", "-01:01"):
        sign = 1 if off[0] == "+" else -1
        shift = sign * (int(off[1:3]) * 3600 + int(off[4:6]) * 60) * 1000000
        for instant in (MIN_US - 1, MIN_US, MAX_US, MAX_US + 1):
            try:
                local = EPOCH + dt.timedelta(microseconds=instant + shift)
            except OverflowError:
                continue
            if 1 <= local.year <= 9999:
                cells.append("%04d-%02d-%02dT%02d:%02d:%02d.%06d%s" % (local.year, local.month, local.day, local.hour, local.minute, local.second, local.microsecond, off))
    for sep in SEPS:
        for off in OFFSETS + [""]:
            cells.append("2026-01-02%s01:02:03%s" % (sep, off))
            cells.append("2026-01-02%s24:00:00%s" % (sep, off))
            cells.append("2026-01-02%s23:59:60%s" % (sep, off))
            cells.append("2026-01-02%s23:59:59.999999%s" % (sep, off))
    for digits in range(0, 11):
        frac = "." + "".join(str((i + 1) % 10) for i in range(digits)) if digits else ""
        cells.append("2026-01-02T01:00:00%sZ" % frac)
        cells.append("2026-01-02T01:00:00%s" % frac)
        cells.append("2026-01-02 01:00:00%s+02:00" % frac)
        cells.append("2026-01-02T01:00:00.%sZ" % ("0" * digits if digits else ""))
    cells += ["2026-01-02T01:00:00.Z", "2026-01-02T01:00:00.", "2026-01-02T01:00:00,5Z", "2026-01-02T01:00:00.5", "2026-01-02T01:00:00.50Z", "2026-01-02T01:00:00.500000Z",
              "2026-01-02T01:00:00.500Z", "2026-01-02T01:00:00.123456Z", "2026-01-02T01:00:00.1234560Z", "2026-01-02T01:00:00.123456789Z", "2026-01-02T01:00:00.0000000Z",
              "2026-01-02T01:00:00.0000001Z", "2026-01-02T01:00:00.000000Z"]
    cells += [" 2026-01-02", "2026-01-02 ", " 2026-01-02T01:00:00Z", "2026-01-02T01:00:00Z ", "2026-01-02 T01:00:00Z", "2026-01-02T 01:00:00Z", "2026-01-02T01 :00:00Z",
              "2026-01-02\t01:00:00Z", "2026-01-02T01:00:00Z\n", "2026-01-02T01:00:00 Z", "2026-01-02T01:00:00 +02:00", "\t2026-01-02", "2026-01-02\r"]
    cells += ["2026-01-02T01:00:00+0200", "2026-01-02T01:00:00+02", "2026-01-02T01:00:00-0200", "2026-01-02T01:00:00+2:00", "2026-01-02T01:00:00+02:0", "2026-01-02T01:00:00+02:00:00",
              "2026-01-02T01:00:00+ 02:00", "2026-01-02T01:00:00+aa:bb", "2026-01-02T01:00:00ZZ", "2026-01-02T01:00:00Zx", "2026-01-02T01:00:00z1", "2026-01-02T01:00:00+",
              "2026-01-02T01:00:00-", "2026-01-02T01:00:00+02:00x", "2026-01-02T01:00:00+14:01", "2026-01-02T01:00:00-12:30", "2026-01-02T01:00:00+23:59", "2026-01-02T01:00:00+24:00"]
    cells += ["2026-01-02T01:00:00 Europe/Madrid", "2026-01-02T01:00:00Europe/Madrid", "2026-01-02T01:00:00CET", "2026-01-02T01:00:00 UTC", "2026-01-02T01:00:00UTC", "2026-01-02T01:00:00GMT",
              "2026-01-02T01:00:00Z[Europe/Madrid]", "2026-01-02T01:00:00+01:00[Europe/Madrid]", "2026-01-02T01:00:00[Europe/Madrid]", "2026-01-02T01:00:00 America/New_York",
              "2026-01-02T01:00:00 EST5EDT", "2026-01-02 01:00:00 PST"]
    cells += ["2026-01-02T", "2026-01-02T01", "2026-01-02T01:", "2026-01-02T01:00", "2026-01-02T01:00:", "2026-01-02T01:00:0", "2026-01-02T1:00:00Z", "2026-01-02T01:0:00Z",
              "2026-01-02T01-00-00Z", "2026/01/02T01:00:00Z", "2026-01-02T01:00:00Z2", "26-01-02T01:00:00Z", "2026-01-02-01:00:00Z", "2026-01-02Z", "2026-01-02+01:00", "2026-01-02Z01:00:00",
              "2026-01-02T01:00:60Z", "2026-01-02T01:00:61Z", "2026-01-02T01:60:00Z", "2026-01-02T25:00:00Z", "2026-01-02T24:30:00Z", "2026-01-02T99:99:99Z", "2026-01-02T00:00:00Z",
              "2026-01-02T23:59:59Z", "2026-01-02 00:00:00", "2026-01-02t00:00:00z", "2026-01-02t00:00:00Z", "2026-01-02T00:00:00z", "1970-01-01T00:00:00Z", "1969-12-31T23:59:59.999999Z",
              "1969-12-31T23:59:59Z", "1970-01-01", "1969-12-31", "1969-12-31 23:59:59.5", "1969-12-31T23:59:59.5Z", "1969-12-31T23:59:59.000001Z", "2026-01-02T01:00:00+02:00",
              "2026-01-01T23:00:00Z", "2026-01-01T23:00:00+00:00", "2026-01-01T23:00:00z", "2026-01-02T01:00:00.50+02:00", "2026-01-01T23:00:00.500000Z", "2026-01-02 01:00:00",
              "2026-01-02 01:00:00.000", "2026-01-02 01:00:00.5", "2026-01-02 01:00:00.1234567", "2026-01-02T00:00:00-00:00", "2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00-00:01"]
    # year 0 without an offset, the delimiters of a date that are not dashes
    cells += ["0000-01-01T00:00:00", "0000-12-31 23:59:59", "0000-02-29 00:00:00", "0000-01-01", "2026/01/02", "2026.01.02", "2026-01/02", "2026/01-02", "2026 01 02", "2026_01_02", "2026-01 02", "2026:01:02",
              "2026-01-02T01.00.00Z", "2026-01-02T01:00-00Z", "2026-01-02T01:00:00Z"]
    # two faults at once: the order of the checks of section 5 decides which is named (a sample of the product of the faults, always the same)
    rng = random.Random(5)
    pieces = [["2026", "0000"], ["01", "13", "00", "02"], ["02", "32", "00", "30", "29"], ["01", "24", "25", "00"], ["00", "60"], ["00", "60", "61", "59"], ["", ".5", ".1234567", ".", ".000000"],
              ["Z", "+24:00", "-00:00", "+01:00", "", " Europe/Madrid", "+0100", "+01:60", "z", "[x]"]]
    for _ in range(400):
        y, mo, d, h, mi, ss, frac, off = (rng.choice(p) for p in pieces)
        cells.append("%s-%s-%s%s%s:%s:%s%s%s" % (y, mo, d, rng.choice(SEPS), h, mi, ss, frac, off))
    seen = set()
    out = []
    for c in cells:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def random_valid(rng, kind):
    """A valid cell of `kind` in a random spelling (a timestamp near the year 1 or 9999 whose offset takes it out of the range is drawn again)."""
    while True:
        cell = _random_cell(rng, kind)
        if parse(kind, cell.encode())[0] == "ok":
            return cell


def _random_cell(rng, kind):
    y = rng.choice([rng.randint(1, 9999), rng.randint(1900, 2100), rng.choice(LEAP)])
    m = rng.randint(1, 12)
    d = rng.randint(1, calendar.monthrange(y, m)[1])
    date = "%04d-%02d-%02d" % (y, m, d)
    if kind == "date":
        return date
    time = "%02d:%02d:%02d" % (rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
    r = rng.random()
    if r < 0.4:
        time += "." + "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 6)))
    elif r < 0.5:
        time += "." + rng.choice(["5", "50", "500", "123", "000", "000000", "999999"])
    s = date + rng.choice(SEPS) + time
    if kind == "datetime":
        return s
    off = rng.choice(["Z", "z", "+00:00", "+01:00", "-05:00", "+05:30", "+14:00", "-12:00", "+09:45", "-03:30", "+23:59", "-23:59"])
    return s + off


def mutate(rng, cell):
    """A byte-level mutation of a cell (the fuzz of G-FUZZ): insert, delete, replace one byte from the alphabet of the grammar, truncate, duplicate, swap."""
    alphabet = b"0123456789-:TtZz+. ,\x00\xc3\xa9[]/\n\"abcXYU"
    b = bytearray(cell)
    for _ in range(rng.randint(1, 3)):
        op = rng.randint(0, 5)
        if op == 0 or not b:
            b.insert(rng.randint(0, len(b)), rng.choice(alphabet))
        elif op == 1:
            del b[rng.randrange(len(b))]
        elif op == 2:
            b[rng.randrange(len(b))] = rng.choice(alphabet)
        elif op == 3:
            del b[rng.randint(0, len(b)):]
        elif op == 4:
            at = rng.randint(0, len(b))
            b[at:at] = b[:rng.randint(0, len(b))]
        else:
            i, j = rng.randrange(len(b)), rng.randrange(len(b))
            b[i], b[j] = b[j], b[i]
    return bytes(b)


def fuzz_cell(rng):
    r = rng.random()
    if r < 0.15:
        return bytes(rng.choice(b"0123456789-:TtZz+. ,") for _ in range(rng.randint(0, 40)))
    base = random_valid(rng, rng.choice(KINDS)).encode()
    return mutate(rng, base) if r < 0.9 else base + bytes(rng.choice(b"0123456789.") for _ in range(rng.randint(40, 80)))


# ---- the bucket of a group key (docs/datetime.md section 7, slice T2) ------------------------------------------------------------------
#
# `--group ts:timestamp@day`: the key of a row is the FIRST VALUE OF THE BUCKET its value falls in, a value of the same type. The bucket of a :timestamp is a function of the INSTANT (the
# UTC day, hour, month or year), of a :datetime the wall-clock reading, of a :date the written day. Written with `datetime.replace` on the value read back as a calendar date and
# a clock, never with a division, so it shares nothing with the tool (which divides, with the value shifted to the year 1 so that no operand is negative).

UNITS = ("hour", "day", "month", "year")
UNITS_OF = {"date": ("month", "year"), "timestamp": UNITS, "datetime": UNITS}
# the units the grammar knows but T2 does not build, and the ones that are no unit at all
NOT_BUILT = ("week",)
FINER = {"date": ("hour", "day")}      # a unit that exists but is finer than the type: `:date@hour`, `:date@day`


def bucket(kind, value, unit):
    """The first value of the bucket of `value` (days for a date, microseconds otherwise)."""
    if unit not in UNITS_OF[kind]:
        raise ValueError((kind, unit))
    if kind == "date":
        d = EPOCH.date() + dt.timedelta(days=value)
        d = d.replace(day=1) if unit == "month" else d.replace(month=1, day=1)
        return (d - EPOCH.date()).days
    t = EPOCH + dt.timedelta(microseconds=value)
    if unit == "hour":
        t = t.replace(minute=0, second=0, microsecond=0)
    elif unit == "day":
        t = t.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "month":
        t = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        t = t.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return (t - EPOCH) // US


def bucket_refusal(kind, unit):
    """What `--group c:KIND@UNIT` is: None when it is built, else (reason, word) where reason is the `detail.reason` of the `args.bad-value` refusal (T2 as built, docs/datetime.md
    section 19): `week-not-built`, `finer-than-type` or `unknown-unit`."""
    if unit in UNITS_OF[kind]:
        return None
    if unit in NOT_BUILT:
        return "week-not-built"
    if unit in FINER.get(kind, ()):
        return "finer-than-type"
    return "unknown-unit"


def first_value_cells(kind, rng, n):
    """Valid cells near the edges of every unit: the instant before, at and after a boundary, written in several offsets and spellings."""
    boundaries = []
    for y in (1, 2, 4, 100, 400, 1600, 1899, 1900, 1969, 1970, 1971, 1999, 2000, 2023, 2024, 2025, 2026, 2099, 2100, 2399, 2400, 9998, 9999):
        boundaries += [dt.datetime(y, 1, 1), dt.datetime(y, 12, 31, 23)]
        for m in (2, 3, 12):
            boundaries.append(dt.datetime(y, m, 1))
            if m == 2:
                boundaries.append(dt.datetime(y, 2, calendar.monthrange(y, 2)[1]))
    boundaries += [dt.datetime(1969, 12, 31, 23, 59, 59), dt.datetime(1970, 1, 1), dt.datetime(9999, 12, 31, 23, 59, 59)]
    cells = []
    for b in boundaries:
        for delta in (-1, 0, 1, 3600 * 1000000 - 1, 3600 * 1000000, 86400 * 1000000 - 1, 86400 * 1000000):
            try:
                t = b + dt.timedelta(microseconds=delta)
            except OverflowError:
                continue
            if not 1 <= t.year <= 9999:
                continue
            micro = (t - EPOCH) // US
            if kind == "date":
                cells.append("%04d-%02d-%02d" % (t.year, t.month, t.day))
                continue
            if kind == "datetime":
                cells.append("%04d-%02d-%02d%s%02d:%02d:%02d.%06d" % (t.year, t.month, t.day, rng.choice(SEPS), t.hour, t.minute, t.second, t.microsecond))
                cells.append("%04d-%02d-%02d %02d:%02d:%02d" % (t.year, t.month, t.day, t.hour, t.minute, t.second))
                continue
            for off in ("Z", "+00:00", "+14:00", "-12:00", "+23:59", "-23:59", "+05:30", "-03:30", "+01:00", "-01:00", "z", "+09:45"):
                sign = 1 if off[0] != "-" else -1
                shift = 0 if off in ("Z", "z") else sign * (int(off[1:3]) * 3600 + int(off[4:6]) * 60) * 1000000
                try:
                    local = EPOCH + dt.timedelta(microseconds=micro + shift)
                except OverflowError:
                    continue
                if not 1 <= local.year <= 9999:
                    continue
                cell = "%04d-%02d-%02d%s%02d:%02d:%02d.%06d%s" % (local.year, local.month, local.day, rng.choice(SEPS), local.hour, local.minute, local.second, local.microsecond, off)
                if parse("timestamp", cell.encode())[0] == "ok":
                    cells.append(cell)
    seen = []
    done = set()
    for c in cells:
        if c not in done:
            done.add(c)
            seen.append(c)
    rng.shuffle(seen)
    return seen[:n] if n else seen


# ---- slice T3: the report of dates, the join of the types ----------------------------------------------------------------------------------
#
# `--report dates` (docs/datetime.md section 9, open question 8; T3 as built, section 20): one row per column of counters. A non-empty cell is exactly one of `date`, `timestamp`,
# `datetime` (the three grammars exclude one another: a date has ten bytes, a timestamp has an offset, a datetime has none) or `other`; an empty cell is `empty` (no bytes, or a quoted
# pair of quotes). `suggest` is the declaration every non-empty cell satisfies, when they all satisfy the same one, else `none`.

REPORT_COLUMNS = ["name", "cells", "empty", "date", "timestamp", "datetime", "other", "widest", "suggest", "first_empty_row", "first_empty_line", "first_not_date_row", "first_not_date_line",
                  "first_not_timestamp_row", "first_not_timestamp_line", "first_not_datetime_row", "first_not_datetime_line", "first_other_row", "first_other_line", "first_other_cell",
                  "first_other_cell_truncated"]


def classify(cell):
    """'empty', 'date', 'timestamp', 'datetime' or 'other' for the bytes of one cell (already unquoted)."""
    if len(cell) == 0:
        return "empty"
    for kind in KINDS:
        if parse(kind, cell)[0] == "ok":
            return kind
    return "other"


def report_row(name, cells, numbers=None):
    """The row of `--report dates` for the column `name` whose cells (bytes, in file order; row 1 is on line 2) are given; with `numbers` the 1-based row of each cell in the file
    (a `--where` keeps some rows, and a first cell is placed by its place in the file)."""
    count = {"empty": 0, "date": 0, "timestamp": 0, "datetime": 0, "other": 0}
    first = {}      # key -> (row, line)
    widest = 0
    other_cell = None
    for i, cell in enumerate(cells):
        row = i + 1 if numbers is None else numbers[i]
        line = row + 1
        widest = max(widest, len(cell))
        c = classify(cell)
        count[c] += 1
        if c == "empty":
            first.setdefault("empty", (row, line))
            continue
        for kind in KINDS:
            if c != kind:
                first.setdefault("not_" + kind, (row, line))
        if c == "other":
            first.setdefault("other", (row, line))
            if other_cell is None:
                other_cell = cell
    nonempty = len(cells) - count["empty"]
    suggest = "none"
    for kind in KINDS:
        if nonempty > 0 and count[kind] == nonempty:
            suggest = ":" + kind
    out = [name, str(len(cells)), str(count["empty"]), str(count["date"]), str(count["timestamp"]), str(count["datetime"]), str(count["other"]), str(widest), suggest]
    for key in ("empty", "not_date", "not_timestamp", "not_datetime", "other"):
        row, line = first.get(key, ("", ""))
        out += [str(row), str(line)]
    out.append(other_cell[:64].decode("utf-8", "replace") if other_cell is not None else "")
    out.append("" if other_cell is None else ("true" if len(other_cell) > 64 else "false"))
    return out


def join_rows(kind, probe, build, jtype, using=False):
    """The rows of a join of `probe` (rows [id, key, ...]) with `build` (rows [key, name]) on the VALUE of the key cells read as `kind`: for each probe row in file order the build rows
    with an equal value in file order (inner), the probe row with empty build cells when there is none (left), the probe row alone once (semi) or when there is none (anti). With
    `using` the build key column is not repeated."""
    def value(cell):
        r = parse(kind, cell.encode())
        assert r[0] == "ok", (kind, cell)
        return r[1]
    out = []
    for p in probe:
        hits = [b for b in build if value(b[0]) == value(p[1])]
        if jtype == "semi":
            if hits:
                out.append(list(p))
        elif jtype == "anti":
            if not hits:
                out.append(list(p))
        elif hits:
            for b in hits:
                out.append(list(p) + (list(b[1:]) if using else list(b)))
        elif jtype == "left":
            out.append(list(p) + [""] * (len(build[0]) - 1 if using else len(build[0])))
    return out
