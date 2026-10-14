"""An independent oracle for `--format jsonl` (docs/readers.md sections 7, 8 (R3) and 10), and the generators that feed it.

The oracle is written from the specification and nothing else. It has four parts:

* the line: `enc_line` writes what one output row must be, byte for byte (`{"k0":v0,...}` and an LF; every cell a JSON string, escaped by the rules of the
  specification and not by Python's `json.dumps`; a cell that is not UTF-8 as `{"b64":...}`; the cells of aggregates as bare numbers), and `parse_line` /
  `re_encode` read a line the tool wrote as strictly as the specification demands (no NaN, no duplicate key, one object, compact) so that a line is checked
  twice: parsed (what it says) and re-encoded (that it is written the one way).
* the table semantics: `evaluate(header, records, plan)` answers what `--select`, `--where`, `--order-by`, `--group`, `--agg`, `--sort`, `--top`, `--limit`
  and `--from` give, on Python's `int`, `Fraction` and `float` (numbers_ref.py and refimpl.py hold the cells' grammar). It is validated against `--format csv` of
  the tool by `OracleSelfCheck` in test_jsonl_out.py: the same cells, the same header.
* the generators: tables of cells that are hard to write (every control byte, quotes, backslashes, NUL, invalid UTF-8 of every kind, 4-byte characters, big
  integers, decimals with trailing zeros, floats), and plans over them, rendered to flags.
* `from_csv_out` turns the stdout of `--format csv` into what `--format jsonl` of the same plan must write (the second oracle: the tool's own csv, for the plans
  this module has no semantics for, a `--report types` or a refusal after rows).

Cells are `str` whose characters are bytes (latin-1), as in refimpl.py; `raw(cell)` is the bytes.
"""

import base64
import csv
import io
import json
import random
import re
from fractions import Fraction

import numbers_ref as N
import refimpl

csv.field_size_limit(1 << 30)


def L(text):
    """A unicode text as the cell of bytes (latin-1 characters) it is in a UTF-8 file."""
    return text.encode("utf-8").decode("latin-1")


def raw(cell):
    return cell.encode("latin-1")


def B(data):
    return data.decode("latin-1")


# ---- the line ----------------------------------------------------------------------------------------------------------------------------------

SHORT = {0x22: '\\"', 0x5C: "\\\\", 0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r"}


class NotRepresentable(Exception):
    """A name that is not UTF-8 cannot be a JSON key (`output.not-representable`)."""


def enc_string(text):
    out = ['"']
    for ch in text:
        o = ord(ch)
        if o in SHORT:
            out.append(SHORT[o])
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def enc_cell(data):
    """A cell's bytes as the JSON value of a string cell: the string, or the object `{"b64": ...}` when the bytes are not UTF-8."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return b'{"b64":"' + base64.b64encode(data) + b'"}'
    return enc_string(text).encode("utf-8")


def enc_key(data):
    try:
        return enc_string(data.decode("utf-8")).encode("utf-8")
    except UnicodeDecodeError:
        raise NotRepresentable(data)


def enc_line(keys, cells, numeric):
    """`keys`, `cells`: bytes; `numeric`: which cells are numbers (their bytes are the token)."""
    parts = []
    for k, c, num in zip(keys, cells, numeric):
        parts.append(enc_key(k) + b":" + (c if num else enc_cell(c)))
    return b"{" + b",".join(parts) + b"}\n"


class Num(str):
    """A JSON number as written: the text is the token."""


class Pairs(list):
    """An object as the list of its (key, value) pairs."""


def _pairs(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise ValueError("duplicate key %r" % k)
        seen.add(k)
    return Pairs(pairs)


def _constant(name):
    raise ValueError("constant " + name)


def parse_line(line):
    """One line (without its LF) as the pairs of its object, strictly."""
    text = line.decode("utf-8")
    if not (text.startswith("{") and text.endswith("}")):
        raise ValueError("not one object")
    value = json.loads(text, object_pairs_hook=_pairs, parse_float=Num, parse_int=Num, parse_constant=_constant)
    if not isinstance(value, Pairs):
        raise ValueError("not an object")
    return value


def re_encode(pairs):
    parts = []
    for k, v in pairs:
        if isinstance(v, Num):
            vs = str(v)
        elif isinstance(v, str):
            vs = enc_string(v)
        elif isinstance(v, Pairs) and len(v) == 1 and v[0][0] == "b64" and type(v[0][1]) is str:
            vs = '{"b64":' + enc_string(v[0][1]) + "}"
        else:
            raise ValueError("a value that is neither a string, a number nor a b64 object: %r" % (v,))
        parts.append(enc_string(k) + ":" + vs)
    return ("{" + ",".join(parts) + "}\n").encode("utf-8")


def split_lines(stdout):
    if not stdout:
        return []
    if not stdout.endswith(b"\n"):
        raise ValueError("the output does not end with a newline: %r" % stdout[-60:])
    lines = stdout.split(b"\n")[:-1]
    for i, ln in enumerate(lines):
        if not ln:
            raise ValueError("line %d is empty" % (i + 1))
        if b"\r" in ln:
            raise ValueError("line %d holds a CR" % (i + 1))
    return lines


def read_lines(stdout):
    """Every line of `stdout`, strictly parsed and checked to be written the one way: the list of pairs. Raises ValueError (with the line) otherwise."""
    out = []
    for i, ln in enumerate(split_lines(stdout)):
        try:
            pairs = parse_line(ln)
            if re_encode(pairs) != ln + b"\n":
                raise ValueError("not the canonical writing: %r, which is %r" % (ln[:200], re_encode(pairs)[:200]))
        except (ValueError, UnicodeError) as e:
            raise ValueError("line %d: %s: %r" % (i + 1, e, ln[:300]))
        out.append(pairs)
    return out


def value_matches(v, cell, numeric):
    """Whether the parsed JSON value `v` is the cell (a latin-1 str, an `FT` for a float written by the shortest-decimal rule, whose digits may differ in a tie)."""
    if numeric:
        if type(v) is not Num:
            return False
        return str(v) == cell or (isinstance(cell, FT) and N.same_text(str(v), cell.x))
    want = raw(cell)
    try:
        text = want.decode("utf-8")
    except UnicodeDecodeError:
        if not (isinstance(v, Pairs) and len(v) == 1 and v[0][0] == "b64" and type(v[0][1]) is str):
            return False
        try:
            return base64.b64decode(v[0][1], validate=True) == want and base64.b64encode(want).decode() == v[0][1]
        except ValueError:
            return False
    if type(v) is not str:
        return False
    return v == text or (isinstance(cell, FT) and N.same_text(v, cell.x))


class FT(str):
    """A cell that is a float written as the shortest decimal that reads back: two shortest strings may be equally close, so the digits are compared loosely."""
    x = 0.0

    def __new__(cls, x):
        s = str.__new__(cls, N.float_text(x))
        s.x = x
        return s


class Out:
    def __init__(self, names, rows, numeric):
        self.names, self.rows, self.numeric = names, rows, numeric

    def __repr__(self):
        return "<Out %r %d rows>" % (self.names, len(self.rows))


class Refuse:
    def __init__(self, rule, out=None, status=8):
        self.rule, self.out, self.status = rule, out, status

    def __repr__(self):
        return "<Refuse %s>" % self.rule


def compare_out(stdout, out):
    """None when `stdout` is exactly the rows of `out` as jsonl; else a sentence."""
    try:
        pairs_list = read_lines(stdout)
    except ValueError as e:
        return str(e)
    if len(pairs_list) != len(out.rows):
        return "%d lines, %d rows expected; first lines %r" % (len(pairs_list), len(out.rows), stdout[:300])
    keys = [raw(n) for n in out.names]
    for i, (pairs, row) in enumerate(zip(pairs_list, out.rows)):
        got = [k.encode("utf-8", "surrogatepass") for k, _ in pairs]
        if got != keys:
            return "line %d: keys %r, expected %r" % (i + 1, got, keys)
        for (k, v), cell, num in zip(pairs, row, out.numeric):
            if not value_matches(v, cell, num):
                return "line %d: the value of %r is %r, expected %s %r" % (i + 1, k, v, "the number" if num else "the cell", cell[:200])
    return None


# ---- the second oracle: the tool's own csv ------------------------------------------------------------------------------------------------------

def csv_records(stdout):
    """The records of the stdout of `--format csv`, cells as latin-1 strs; strict, so a malformed quoting is an error."""
    recs = list(csv.reader(io.StringIO(stdout.decode("latin-1"), newline=""), strict=True))
    return recs


class Tail(int):
    """How many of the last columns of an answer are aggregates (an `int`), and which of the first ones are typed group keys (`typed`: their places), which are
    numbers as the aggregates are (readers.md N32)."""
    typed = ()


def typed_places(group):
    """The places among the items of a `--group` value (`a,b:dec(2),#3:float`) that carry a type."""
    return tuple(i for i, item in enumerate(group.split(",")) if re.search(r":(int|float|dec\(\d+\))$", item))


def from_csv_out(stdout, numeric_tail=0):
    """What `--format jsonl` writes for the plan whose `--format csv` wrote `stdout`: ("ok", bytes), or ("not-representable"|"duplicate", None).
    The first record is the header; the last `numeric_tail` columns are the aggregates, and the places in `numeric_tail.typed` (when it has them) are typed
    group keys, which are numbers too."""
    recs = csv_records(stdout)
    if not recs:
        return ("ok", b"")
    header, rows = recs[0], recs[1:]
    width = len(header)
    if width == 1:
        rows = [r if r else [""] for r in rows]
    keys = [raw(h) for h in header]
    for k in keys:
        try:
            k.decode("utf-8")
        except UnicodeDecodeError:
            return ("not-representable", None)
    if len(set(keys)) != len(keys):
        return ("duplicate", None)
    numeric = [False] * (width - numeric_tail) + [True] * numeric_tail
    for i in getattr(numeric_tail, "typed", ()):
        numeric[i] = True
    out = []
    for r in rows:
        assert len(r) == width, "a csv record of %d cells under a header of %d: %r" % (len(r), width, r[:5])
        out.append(enc_line(keys, [raw(c) for c in r], numeric))
    return ("ok", b"".join(out))


# ---- cells -------------------------------------------------------------------------------------------------------------------------------------

CTL = [bytes([c]) for c in range(0x20)] + [b"\x7f"]
VALID_UNICODE = [
    "é", "中文", "😀", " ", " ", "￿", "￾", "\U0010ffff", "퟿", "", "\u0080", "߿", "ࠀ", "﻿", "�", " ",
    "café", "ñandú", "日本語のテキスト", "á", "\U0001f468‍\U0001f469‍\U0001f467", "\U00010000", "\u0085", "\U000e0001", "ÿ", "Ā",
]
INVALID = [
    b"\x80", b"\xbf", b"\xc2", b"\xc3", b"\xe2\x82", b"\xf0\x9f\x98", b"\xf0\x9f", b"\xc0\xaf", b"\xc1\xbf", b"\xe0\x80\x80", b"\xe0\x9f\xbf",
    b"\xed\xa0\x80", b"\xed\xbf\xbf", b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80", b"\xf8\x88\x80\x80\x80", b"\xfe", b"\xff", b"ok\xff", b"\xffok",
    "é".encode() + b"\xc3", b"\xe2\x82\xac\xe2\x82", b"a\x80b", b"\xf0\x80\x80\x80", b"\xc0\x80", b"\xed\xa0\xbd\xed\xb8\x80", b"\xef\xbf",
]
ESCAPY = ['q"z', '"', '""', "\\", "\\\\", "/", "\\n", "\\u0041", "\\/", "a\\\"b", '"\\', "\\\"", "{\"a\":1}", "[1,2]", "null", "true", "-0", "NaN", "“q”",
          "plain", "a b", " lead", "trail ", "x y z", "a,b", ",", "a;b", "tab\there", "l1\nl2", "cr\rx", "crlf\r\nx", "\n", "\r", "\r\n", "\n\n", ",\n\"", "end\\"]


def rich_bytes(rng):
    """One cell: a few pieces among the escapes, the control bytes, valid and invalid UTF-8."""
    r = rng.random()
    if r < 0.08:
        return b""
    pieces = []
    for _ in range(rng.choice([1, 1, 1, 2, 3, 5])):
        q = rng.random()
        if q < 0.28:
            pieces.append(rng.choice(ESCAPY).encode("utf-8"))
        elif q < 0.5:
            pieces.append(rng.choice(CTL))
        elif q < 0.72:
            pieces.append(rng.choice(VALID_UNICODE).encode("utf-8"))
        elif q < 0.88:
            pieces.append(rng.choice(INVALID))
        else:
            pieces.append(bytes(rng.randrange(256) for _ in range(rng.randint(1, 4))))
    return b"".join(pieces)


def rich(rng):
    return B(rich_bytes(rng))


KEYS = ["a", "b", "c", "", "A", "ab", "a b", "z", L("é"), 'q"', "\x00", "\x01", "\\", "x,y", "l\nl", L(" "), "\xff", "\xc3", L("😀"), "k\x7f"]
INTS = ["0", "1", "-1", "+5", "007", "-0", "42", "-42", "1000", "99999", "250", "-250", "7", "123456789", "-987654321", "+0", "000", "-007"]
BIGS = ["9223372036854775807", "-9223372036854775808", "9007199254740993", "9007199254740992", "-9007199254740993", "4611686018427387904", "0", "-1", "1",
        "9223372036854775806", "-9223372036854775807", "2147483648", "4294967296", "18014398509481985", "1000000000000000000"]
DECS = ["1.5", "1.50", "0.1", ".5", "5.", "-0.00", "+3", "0", "12345.67", "-0.01", "007.10", "1000000000.99", "0.00", "-1.5", "2.25", "10", "-.5", "999999999.99", "0.10",
        "1.0", "100.00", "3.", "+.25", "-7.5"]
FLTS = ["0", "-0", "0.0", "-0.0", "1e3", "1E3", "1.5e-7", "17.123456789012345678", "0.1", "0.30000000000000004", "123456789012345678", "1e22", "5e-324", "2.5",
        "9007199254740993", "-1.5", "1e-5", "100", "3.14159265358979323846", "0.5", "-2.75", "1e21", "123456.789", "87992730773886.125", "4.35", "+7", ".5", "6.02e23",
        "1.7976931348623157e308", "2.2250738585072014e-308", "-1e-300"]


def rand_int64(rng):
    return str(rng.randint(-(1 << 63), (1 << 63) - 1))


NICE = ["id", "name", "k", "n", "amount", "price", "note", "path", "status", "bytes", "host", "ts"]
NASTY = [L('q"uote'), L("back\\slash"), L("tab\there"), L("nl\nname"), L("é"), L("中文"), L("😀"), "\x01ctl", "\x7f", L("ul sep"), "a,b", "#hash", "x:int", "sum:n", "",
         "a b", "a\x00b", "\x1f", "-dash", "/slash", L("�"), "a:dec(2)", "'quoted'", "[", "{", "a\\", "\\n", "A\u0001", "count2", "\xe9", "sp ace "]

KIND_ORDER = ["id", "text", "key", "int", "big", "dec", "flt", "w"]


class Table:
    """A generated table: `header` and `records` (lists of cells), `kinds` (what each column holds), and the CSV bytes."""

    def __init__(self, header, kinds, records, data):
        self.header, self.kinds, self.records, self.data = header, kinds, records, data

    def column(self, i):
        return [r[i] for r in self.records]


def cell_of(kind, i, rng, mode="small"):
    if kind == "id":
        return str(i + 1)
    if kind in ("text", "w"):
        return rich(rng)
    if kind == "key":
        return rng.choice(KEYS)
    if kind == "int":
        return rng.choice(INTS) if rng.random() < 0.7 else str(rng.randint(-10 ** 9, 10 ** 9))
    if kind == "big":
        return rng.choice(BIGS) if rng.random() < 0.7 else rand_int64(rng)
    if kind == "dec":
        return rng.choice(DECS) if rng.random() < 0.75 else "%s%d.%02d" % (rng.choice(["", "-"]), rng.randint(0, 10 ** 9), rng.randint(0, 99))
    if kind == "flt":
        pool = FLTS if rng.random() < 0.97 else FLTS
        c = rng.choice(pool)
        return c
    raise ValueError(kind)


def csv_bytes(header, records, rng, quote_all=0.1):
    """The CSV: a field is quoted when it must be (a comma, a quote, a CR or an LF) and, at a rate, when it need not be. LF or CRLF line ends."""
    eol = rng.choice(["\n", "\n", "\r\n"])
    out = []

    def field(c):
        if any(ch in c for ch in ',"\r\n') or rng.random() < quote_all or c == "":
            if c == "" and rng.random() < 0.5:
                return ""
            return '"' + c.replace('"', '""') + '"'
        return c

    out.append(",".join(field(c) for c in header))
    for r in records:
        out.append(",".join(field(c) for c in r))
    return (eol.join(out) + eol).encode("latin-1")


def inflate(rng, table, long_cells=3, huge=True):
    """Some text cells made long (a few KB, and one of about 100 KB), so that rows cross the 64 KiB edges of the reader and the chunks of the threads."""
    text_cols = [i for i, k in enumerate(table.kinds) if k in ("text", "w")]
    if not text_cols or not table.records:
        return table
    for n in range(long_cells):
        r, c = rng.randrange(len(table.records)), rng.choice(text_cols)
        table.records[r][c] = (rich(rng) + "abc") * rng.randint(200, 1500)
    if huge:
        r, c = rng.randrange(len(table.records)), rng.choice(text_cols)
        table.records[r][c] = (rich(rng) + "x") * rng.randint(20000, 40000)
    table.data = csv_bytes(table.header, table.records, rng)
    return table


def fixed_table(rng, rows, kinds=None):
    """The eight kinds of column under plain names (so that a plan can name them), `rows` rows."""
    kinds = list(kinds or KIND_ORDER)
    names = {"id": "id", "text": "txt", "key": "k", "int": "n", "big": "big", "dec": "d", "flt": "f", "w": "w"}
    header = [names[k] for k in kinds]
    records = [[cell_of(k, i, rng) for k in kinds] for i in range(rows)]
    return Table(header, kinds, records, csv_bytes(header, records, rng))


def make_table(rng, rows=None, mode="small", kinds=None):
    if kinds is None:
        kinds = list(KIND_ORDER)
        if rng.random() < 0.4:
            for k in rng.sample(["key", "big", "flt", "w", "dec"], rng.randint(1, 2)):
                kinds.remove(k)
    names = []
    for k in kinds:
        pool = NASTY if rng.random() < 0.3 else NICE
        for _ in range(20):
            nm = rng.choice(pool)
            if nm not in names:
                break
        else:
            nm = "c%d" % len(names)
        names.append(nm)
    if rows is None:
        rows = rng.choice([0, 1, 1, 2, 3, 5, 8, 13, 20, 40])
    records = [[cell_of(k, i, rng, mode) for k in kinds] for i in range(rows)]
    t = Table(names, kinds, records, csv_bytes(names, records, rng))
    if mode == "big":
        inflate(rng, t, long_cells=rng.randint(1, 4), huge=rng.random() < 0.5)
    return t


# ---- plans -------------------------------------------------------------------------------------------------------------------------------------

NUMERIC_TYPES = {
    "id": [("int",), ("dec", 0), ("dec", 3), ("float",)],
    "int": [("int",), ("dec", 0), ("dec", 4), ("float",)],
    "big": [("int",), ("float",)],
    "dec": [("dec", 2), ("dec", 3), ("dec", 6), ("float",)],
    "flt": [("float",)],
}
SAFE = re.compile(r"^[A-Za-z0-9_.@/]+$")
SAFE_LABEL = re.compile(r"^[A-Za-z0-9_@./ ]+(:[A-Za-z0-9_@./ ]+)?$")


def suffix(ty):
    if ty is None:
        return ""
    return {"int": ":int", "float": ":float"}.get(ty[0]) or ":dec(%d)" % ty[1]


def quote(word):
    """A text literal, quoted, as the argument that carries it: the cell is bytes (latin-1 characters), the argument is the text of those UTF-8 bytes."""
    return "'" + raw(word).decode("utf-8").replace("'", "''") + "'"


def arg_ok(cell):
    """A cell that can be spelled in an argument: no NUL, UTF-8, and not huge."""
    if "\x00" in cell or len(cell) > 300:
        return False
    try:
        raw(cell).decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


class Plan:
    """What a person asks, as a structure. `form` is rows, select, where, order, group or agg."""

    def __init__(self, form):
        self.form = form
        self.where = []        # dicts: col, kind (cmp, in, contains), op, lit, lits, ty
        self.select = None     # column indexes
        self.order = []        # (col, desc, ty)
        self.group = []        # (col, ty)
        self.aggs = []         # (func, col, ty, at)
        self.sort = None       # (output index, desc)
        self.top = self.limit = self.frm = None
        self.bare = {}         # agg index -> True when its int suffix is left off
        self.refs = {}         # column -> True when named by header rather than #N
        self.types = {}


def holds_cond(c):
    ty = c["ty"]
    return {"kind": c["kind"], "op": c.get("op"), "lit": c.get("lit"), "lits": c.get("lits"), "int": ty == ("int",), "dec": ty[1] if ty and ty[0] == "dec" else None,
            "float": ty == ("float",)}


def out_names(header, plan):
    if plan.form in ("rows", "where", "order"):
        return list(header) if plan.select is None else [header[i] for i in plan.select]
    if plan.form == "select":
        return [header[i] for i in plan.select]
    names = [header[c] for c, _ in plan.group]
    for func, col, _, _ in plan.aggs:
        names.append("count" if func == "count" else "%s:%s" % (func, header[col]))
    return names


def evaluate(header, records, plan):
    """Out, or Refuse (with the rows already written when the plan streams)."""
    width = len(header)
    streaming = plan.form in ("rows", "select", "where")
    kept = []
    for n, rec in enumerate(records, 1):
        ok = True
        for c in plan.where:
            v = refimpl.holds(holds_cond(c), rec[c["col"]])
            if v is True:
                continue
            if v is False:
                ok = False
                break
            prefix = None
            if streaming:
                prefix = finish_rows(header, kept, plan)
            return Refuse(v, prefix)
        if ok:
            if streaming:
                # `--from` of a stream is the 0-based row of the file (matching or not), `--limit` the most rows written, and the read stops there
                if n - 1 < (plan.frm or 0):
                    continue
                kept.append(rec)
                if plan.limit and len(kept) >= plan.limit:
                    break
            else:
                kept.append(rec)
    if plan.form in ("group", "agg"):
        return evaluate_groups(header, kept, plan)
    if plan.order:
        keyed = []
        for rec in kept:
            ks = []
            for col, desc, ty in plan.order:
                if ty is None:
                    ks.append(rec[col])
                    continue
                k, bad = typed_value(rec[col], ty)
                if bad:
                    return Refuse(bad)
                ks.append(k)
            keyed.append((ks, rec))
        for at in range(len(plan.order) - 1, -1, -1):
            keyed.sort(key=lambda p: p[0][at], reverse=plan.order[at][1])
        kept = [r for _, r in keyed]
        if plan.top:
            kept = kept[:plan.top]
    return finish_rows(header, kept, plan)


def finish_rows(header, kept, plan):
    if plan.form == "order" and plan.top:
        kept = kept[:plan.top]
    sel = plan.select if plan.select is not None else list(range(len(header)))
    rows = [[r[i] for i in sel] for r in kept]
    if plan.form not in ("rows", "select", "where"):
        start = plan.frm or 0
        rows = rows[start:(start + plan.limit) if plan.limit else None]
    return Out([header[i] for i in sel], rows, [False] * len(sel))


def typed_value(cell, ty):
    """(value, None) or (None, rule): the cell read as the type, the value being what is compared (an int, a scaled int, a float)."""
    if ty[0] == "int":
        v, bad = refimpl.to_int(cell)
        return (None, bad) if bad else (v, None)
    if ty[0] == "dec":
        got = N.dec(cell, ty[1])
        return (None, got[1]) if got[0] == "refuse" else (got[1], None)
    got = N.flt(cell)
    return (None, got[1]) if got[0] == "refuse" else (got[1], None)


def value_text(v, ty):
    if ty[0] == "int":
        return str(v)
    if ty[0] == "dec":
        return N.text(v, ty[1])
    return FT(v)


def evaluate_groups(header, kept, plan):
    groups = {}
    order = []
    for rec in kept:
        key, shown = [], []
        for col, ty in plan.group:
            cell = rec[col]
            if ty is None:
                key.append(cell)
                shown.append(cell)
            else:
                v, bad = typed_value(cell, ty)
                if bad:
                    return Refuse(bad)
                key.append(v)
                shown.append(value_text(v, ty))
        key = tuple(key)
        if key not in groups:
            groups[key] = {"shown": shown, "count": 0, "vals": [[] for _ in plan.aggs]}
            order.append(key)
        g = groups[key]
        g["count"] += 1
        for k, (func, col, ty, at) in enumerate(plan.aggs):
            if func == "count":
                continue
            cell = rec[col]
            if func == "distinct":
                if ty is None:
                    g["vals"][k].append(cell)
                else:
                    v, bad = typed_value(cell, ty)
                    if bad:
                        return Refuse(bad)
                    g["vals"][k].append(v)
                continue
            if ty is None:
                ty = ("int",)
            v, bad = typed_value(cell, ty)
            if bad:
                return Refuse(bad)
            g["vals"][k].append(v)
    keys = sorted(order)
    rows, sortkeys = [], []
    for key in keys:
        g = groups[key]
        row = list(g["shown"])
        sk = list(key)
        for k, (func, col, ty, at) in enumerate(plan.aggs):
            vals = g["vals"][k]
            if func == "count":
                row.append(str(g["count"]))
                sk.append(g["count"])
                continue
            if func == "distinct":
                row.append(str(len(set(vals))))
                sk.append(len(set(vals)))
                continue
            ty = ty or ("int",)
            if ty[0] == "float":
                if func in ("min", "max"):
                    x = (min if func == "min" else max)(vals)
                    x = 0.0 if x == 0 else x
                elif func == "sum":
                    r = N.fsum_ref(vals)
                    if r[0] == "overflow":
                        return Refuse("agg.float-overflow")
                    x = r[1]
                else:
                    r = N.fmean_ref(vals)
                    if r[0] == "overflow":
                        return Refuse("agg.float-overflow")
                    x = r[1]
                row.append(FT(x))
                sk.append(x)
                continue
            scale = ty[1] if ty[0] == "dec" else 0
            if func == "sum":
                row.append(N.text(sum(vals), scale))
                sk.append(sum(vals))
            elif func in ("min", "max"):
                v = (min if func == "min" else max)(vals)
                row.append(N.text(v, scale))
                sk.append(v)
            else:
                out = at if at is not None else scale
                row.append(N.mean_text(vals, scale, out))
                sk.append(Fraction(sum(vals), len(vals)))
        rows.append(row)
        sortkeys.append(sk)
    idx = list(range(len(rows)))
    if plan.sort is not None:
        at, desc = plan.sort
        idx.sort(key=lambda i: sortkeys[i][at], reverse=desc)
    rows = [rows[i] for i in idx]
    if plan.top:
        rows = rows[:plan.top]
    start = plan.frm or 0
    rows = rows[start:(start + plan.limit) if plan.limit else None]
    names = out_names(header, plan)
    # a typed group key is a number (readers.md N32), an untyped one a string; an aggregate is a number
    return Out(names, rows, [ty is not None for _, ty in plan.group] + [True] * len(plan.aggs))


# ---- generating plans ---------------------------------------------------------------------------------------------------------------------------

class Gen:
    def __init__(self, rng, table, big=False):
        self.rng, self.t, self.big = rng, table, big
        self.tm = {}          # column -> its type for the whole plan; False = read as text

    def numeric_cols(self):
        return [i for i, k in enumerate(self.t.kinds) if k in NUMERIC_TYPES]

    def text_cols(self):
        return [i for i, k in enumerate(self.t.kinds) if k in ("text", "key", "w")]

    def num_type(self, i):
        """The plan's type for numeric column i (chosen once), or None when the plan reads it as text."""
        if i in self.tm:
            return self.tm[i] or None
        ty = self.rng.choice(NUMERIC_TYPES[self.t.kinds[i]])
        self.tm[i] = ty
        return ty

    def as_text(self, i):
        if i in self.tm and self.tm[i]:
            return False
        self.tm[i] = False
        return True

    def pick_numeric(self):
        cands = [i for i in self.numeric_cols() if self.tm.get(i, 1) is not False]
        if not cands:
            return None
        return self.rng.choice(cands)

    def pick_text(self, keys_only=False):
        """A column the plan reads as text (a numeric column too, when the plan has not read it as a number)."""
        kinds = self.t.kinds
        cands = [i for i in range(len(kinds)) if i not in self.tm or self.tm[i] is False]
        textish = [i for i in cands if kinds[i] in ("text", "key", "w")]
        if keys_only:
            cands = [i for i in textish if kinds[i] == "key"]
            if not cands:
                return None
        elif textish and self.rng.random() < 0.8:
            cands = textish
        if not cands:
            return None
        i = self.rng.choice(cands)
        self.tm[i] = False
        return i

    def literal(self, i, ty):
        rng = self.rng
        sample = [r[i] for r in self.t.records]
        if ty is None:
            ok = [c for c in sample if arg_ok(c)]
            if ok and rng.random() < 0.8:
                c = rng.choice(ok)
                if rng.random() < 0.3 and len(c) > 1:
                    c = c[:rng.randint(1, len(c))]
                    return c if arg_ok(c) else "a"
                return c
            return rng.choice(["a", "b", "", "plain", "z", "x y"])
        kind = self.t.kinds[i]
        if ty[0] == "float":
            ok = [c for c in sample if N.flt(c)[0] == "ok"]
            return rng.choice(ok + ["0.5", "1e3", "-2", "100"]) if ok and rng.random() < 0.8 else rng.choice(["0.5", "1e3", "-2", "100", "2.5", "0"])
        if ty[0] == "int":
            ok = [c for c in sample if refimpl.to_int(c)[1] is None]
            return rng.choice(ok + ["0", "5", "-1", "42"]) if ok and rng.random() < 0.8 else rng.choice(["0", "5", "-1", "42", "007", "+7"])
        scale = ty[1]
        ok = [c for c in sample if N.dec(c, scale)[0] == "ok"]
        pool = ["0", "1.5", "-2", "100", "0.5"] if scale >= 1 else ["0", "5", "-2"]
        return rng.choice(ok + pool) if ok and rng.random() < 0.8 else rng.choice(pool)

    def cond(self):
        rng = self.rng
        numeric = rng.random() < 0.45
        if numeric:
            i = self.pick_numeric()
            if i is None:
                numeric = False
        if numeric:
            ty = self.num_type(i)
        else:
            i = self.pick_text()
            if i is None:
                return None
            ty = None
        kind = rng.choice(["cmp", "cmp", "cmp", "in", "contains"]) if ty is None else rng.choice(["cmp", "cmp", "in"])
        c = {"col": i, "kind": kind, "ty": ty}
        if kind == "in":
            c["lits"] = [self.literal(i, ty) for _ in range(rng.randint(1, 3))]
        else:
            c["lit"] = self.literal(i, ty)
            c["op"] = rng.choice(["=", "!=", "<", "<=", ">", ">="]) if kind == "cmp" else None
        return c

    def build(self):
        rng, t = self.rng, self.t
        width = len(t.kinds)
        form = rng.choices(["select", "rows", "where", "order", "group", "agg"], [20, 6, 10, 18, 40, 6])[0]
        p = Plan(form)
        nconds = 0 if form == "rows" else (rng.choice([1, 1, 2, 3]) if form == "where" else rng.choice([0, 0, 1, 1, 2, 3]))
        for _ in range(nconds):
            c = self.cond()
            if c:
                p.where.append(c)
        if form == "where" and not p.where:
            form = p.form = "select"
        if form == "select" or (form in ("where", "order") and rng.random() < 0.4):
            p.select = rng.sample(range(width), rng.randint(1, min(4, width)))
        if form == "select" and p.select is None:
            p.select = [0]
        if form == "order":
            for _ in range(rng.randint(1, 3)):
                use_num = rng.random() < 0.6
                i = self.pick_numeric() if use_num else self.pick_text()
                if i is None or any(o[0] == i for o in p.order):
                    continue
                p.order.append((i, rng.random() < 0.4, self.num_type(i) if use_num else None))
            if not p.order:
                i = self.pick_text() or 0
                p.order.append((i, False, None))
            if rng.random() < 0.35:
                p.top = rng.randint(1, 6)
        if form in ("group", "agg"):
            self.build_groups(p)
        if rng.random() < 0.25:
            p.limit = rng.randint(1, 8)
        if rng.random() < 0.2:
            p.frm = rng.randint(0, 6)
        return p

    def build_groups(self, p):
        rng, t = self.rng, self.t
        ngroup = 0 if p.form == "agg" else rng.choice([1, 1, 1, 2, 2])
        used = set()
        for _ in range(ngroup):
            if rng.random() < 0.4 and not self.big:
                i = self.pick_numeric()
                if i is not None and i not in used:
                    p.group.append((i, self.num_type(i)))
                    used.add(i)
                    continue
            i = self.pick_text(keys_only=self.big)
            if i is None or i in used:
                continue
            p.group.append((i, None))
            used.add(i)
        if p.form == "group" and not p.group:
            p.form = "agg"
        seen = set()
        for _ in range(rng.randint(0, 4)):
            func = rng.choice(["count", "sum", "min", "max", "mean", "distinct", "sum", "mean"])
            if func == "count":
                if ("count", None) not in seen:
                    seen.add(("count", None))
                    p.aggs.append(("count", None, None, None))
                continue
            if func == "distinct":
                if rng.random() < 0.5:
                    i = self.pick_numeric()
                    ty = self.num_type(i) if i is not None else None
                else:
                    i = self.pick_text()
                    ty = None
                if i is None or (func, i) in seen:
                    continue
                seen.add((func, i))
                p.aggs.append((func, i, ty, None))
                continue
            i = self.pick_numeric()
            if i is None or (func, i) in seen:
                continue
            ty = self.num_type(i)
            seen.add((func, i))
            at = None
            if func == "mean" and ty[0] != "float":
                at = rng.randint(0, 5) if ty[0] == "int" or rng.random() < 0.6 else None
            p.aggs.append((func, i, ty, at))
            if ty == ("int",) and rng.random() < 0.5:
                p.bare[len(p.aggs) - 1] = True
        if not p.aggs:
            p.aggs.append(("count", None, None, None))
        labels = out_names(t.header, p)
        cands = [k for k, n in enumerate(labels) if SAFE_LABEL.match(n) and not n.startswith("-")]
        if cands and rng.random() < 0.6:
            p.sort = (rng.choice(cands), rng.random() < 0.5)
        if rng.random() < 0.3:
            p.top = rng.randint(1, 5)


def ref_of(plan, i, header, rng):
    """A column named in a flag: by header when that is plain, else `#N`."""
    name = header[i]
    if SAFE.match(name) and not name.startswith("#") and rng.random() < 0.5:
        return name
    return "#%d" % (i + 1)


def render_list(items):
    return ",".join(items)


def flags_of(plan, header, rng):
    """The flags (no format, no file) of a plan."""
    f = []
    if plan.form == "rows":
        f.append("--rows")
    if plan.where:
        conds = []
        for c in plan.where:
            col = ref_of(plan, c["col"], header, rng) + suffix(c["ty"])
            if c["kind"] == "contains":
                conds.append("%s contains %s" % (col, quote(c["lit"])))
            elif c["kind"] == "in":
                conds.append("%s in (%s)" % (col, ", ".join(lit_text(x, c["ty"]) for x in c["lits"])))
            else:
                sp = " " if rng.random() < 0.5 else ""
                conds.append("%s%s%s%s%s" % (col, sp, c["op"], sp, lit_text(c["lit"], c["ty"])))
        f += ["--where", " and ".join(conds)]
    if plan.select is not None and plan.form != "rows":
        f += ["--select", render_list([ref_of(plan, i, header, rng) for i in plan.select])]
    if plan.order:
        f += ["--order-by", render_list([("-" if d else "") + ref_of(plan, i, header, rng) + suffix(ty) for i, d, ty in plan.order])]
    if plan.group:
        f += ["--group", render_list([ref_of(plan, i, header, rng) + suffix(ty) for i, ty in plan.group])]
    if plan.form in ("group", "agg"):
        items = []
        for k, (func, col, ty, at) in enumerate(plan.aggs):
            if func == "count":
                items.append("count")
                continue
            s = "%s:%s" % (func, ref_of(plan, col, header, rng))
            if not (plan.bare.get(k) and ty == ("int",)):
                s += suffix(ty)
            if at is not None:
                s += "@%d" % at
            items.append(s)
        f += ["--agg", ",".join(items)]
        if plan.sort is not None:
            f += ["--sort", ("-" if plan.sort[1] else "") + out_names(header, plan)[plan.sort[0]]]
    if plan.top:
        f += ["--top", plan.top]
    if plan.limit:
        f += ["--limit", plan.limit]
    if plan.frm:
        f += ["--from", plan.frm]
    return f


def lit_text(lit, ty):
    if ty is None:
        return quote(lit)
    return lit


def random_case(rng, rows=None, mode="small"):
    t = make_table(rng, rows=rows, mode=mode)
    p = Gen(rng, t, big=(mode == "big")).build()
    return t, p, flags_of(p, t.header, rng)


def numeric_tail(plan):
    t = Tail(len(plan.aggs) if plan.form in ("group", "agg") else 0)
    t.typed = tuple(i for i, (_, ty) in enumerate(plan.group) if ty is not None)
    return t
