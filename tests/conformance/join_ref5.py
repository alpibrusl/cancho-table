"""The reference of slice J5 (docs/joins.md sections 3.2, 11 and 21): JSON lines on the probe side, on the build side, or on both, and a build file with a delimiter of its own.

The definition the tool is held to is the one of J1 to J3b, whatever the format of a side, plus the reader contract of docs/readers.md section 12 for a JSON-lines side:

    * the header of a JSON-lines side is its declaration (`--fields` for the probe, `--join-fields` for the build); a cell is the exact text of the JSON value at the declared
      path: a string decoded, a number as it is written (never through a double), true and false as those words; null, a missing key and a step through a scalar are the
      absent cell (a refusal, or the empty cell with `--absent empty`); an object or an array at the path is a refusal; a key twice at a step of the path is a refusal;
    * the record the join holds and the engine sees is the cells joined by the delimiter and quoted as CSV quotes them (a quote, the delimiter, LF or CR, or the one empty cell
      of a one-column record), so a jsonl build row costs the same accounted bytes as the csv row with the same cells;
    * blank lines (nothing but space, tab and CR) are not records; a trailing CR is not part of a line; a BOM begins the first line and is not part of it.

Everything here reads the generated files again with Python's `json` and `csv`, never with the generator's own memory of what it wrote, so a generator that wrote something
else than it meant is a failure of the test and not a silent agreement. Cells are `bytes`.
"""

import csv
import io
import json
import os
import re

import join_ref as J
import join_ref2 as R

NUMBER = re.compile(rb"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$")


class Num(str):
    """A JSON number as written: its exact text."""


class Refusal(Exception):
    """A JSON-lines refusal: the tool's rule, the 1-based line and row, the column (declared name) when the rule has one."""

    def __init__(self, rule, line, row, column=None):
        super().__init__(rule)
        self.rule, self.line, self.row, self.column = rule, line, row, column


def split_path(name):
    """The segments of a declared path (no escapes in the generated names): split at dots."""
    return name.split(".")


class Pairs(list):
    """A JSON object as the list of its (key, value) pairs, in order, duplicates kept."""


def _no_constant(name):
    raise ValueError("not strict JSON: " + name)


def parse_line(text):
    return json.loads(text, object_pairs_hook=Pairs, parse_int=Num, parse_float=Num, parse_constant=_no_constant)


def lines_of(data):
    """(number, text) of every non-blank line of a JSON-lines file: the BOM dropped from the first, a trailing CR from each, a line of spaces, tabs and CRs skipped. Lines are
    split at LF only (a JSON string cannot hold a bare LF)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    out = []
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    for n, raw in enumerate(parts, 1):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if raw.strip(b" \t\r") == b"":
            continue
        out.append((n, raw))
    return out


def walk(value, segs):
    """The node at `segs` below `value`: ('ok', node), ('missing', reason) with reason 0 absent, 1 null, 2 through a scalar, or ('twice', segment index)."""
    node = value
    for i, seg in enumerate(segs):
        if isinstance(node, Pairs):
            hits = [v for k, v in node if k == seg]
            if len(hits) > 1:
                return ("twice", i)
            if not hits:
                return ("missing", 0)
            node = hits[0]
        elif isinstance(node, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", seg) or int(seg) >= len(node):
                return ("missing", 0)
            node = node[int(seg)]
        else:
            return ("missing", 2)
    return ("ok", node)


def cell_of(node):
    """The cell of a node, or None for null / ('not-scalar', kind)."""
    if node is None:
        return None
    if isinstance(node, bool):
        return b"true" if node else b"false"
    if isinstance(node, Num):
        return str(node).encode("utf-8")
    if isinstance(node, str):
        return node.encode("utf-8")
    return ("not-scalar", "object" if isinstance(node, Pairs) else "array")


def read_jsonl(data, fields, absent="refuse"):
    """The rows of a JSON-lines file as the tool reads them: a list of rows (lists of cells) and the lines they came from, or raises Refusal for the first line that is refused."""
    rows, lines = [], []
    for row, (n, raw) in enumerate(lines_of(data), 1):
        try:
            value = parse_line(raw.decode("utf-8"))
        except (ValueError, RecursionError, UnicodeDecodeError):
            raise Refusal("parse.jsonl-syntax", n, row)
        if not isinstance(value, Pairs):
            raise Refusal("parse.jsonl-not-object", n, row)
        cells = []
        for f in fields:
            where, node = walk(value, split_path(f))
            if where == "twice":
                raise Refusal("parse.jsonl-duplicate-key", n, row, f)
            if where == "missing":
                if absent == "empty":
                    cells.append(b"")
                    continue
                raise Refusal("parse.jsonl-missing-field", n, row, f)
            c = cell_of(node)
            if c is None:
                if absent == "empty":
                    cells.append(b"")
                    continue
                raise Refusal("parse.jsonl-missing-field", n, row, f)
            if isinstance(c, tuple):
                raise Refusal("parse.jsonl-not-scalar", n, row, f)
            cells.append(c)
        rows.append(cells)
        lines.append(n)
    return rows, lines


def read_side(data, kind, header, delim=b",", absent="refuse"):
    """(header, rows, lines) of one side by its reader: kind 'csv' (a header row, Python's csv module) or 'jsonl' (`header` the declared fields). Raises Refusal."""
    if kind == "jsonl":
        rows, lines = read_jsonl(data, [h.decode("utf-8") for h in header], absent)
        return list(header), rows, lines
    recs = J.read_csv_bytes(data, delim.decode())
    return recs[0], recs[1:], list(range(2, 2 + len(recs) - 1))


# ---- the record the join holds ---------------------------------------------------------------------------------------------------------------------------


def csv_record(cells, delim=b","):
    """The cells as the tool holds them: joined by the delimiter, a cell quoted (quotes doubled) when it holds a quote, the delimiter, LF or CR, and a record of one empty cell as
    two quotes (an empty line is no record)."""
    out = []
    for c in cells:
        if b'"' in c or delim in c or b"\n" in c or b"\r" in c or (len(cells) == 1 and c == b""):
            out.append(b'"' + c.replace(b'"', b'""') + b'"')
        else:
            out.append(c)
    return delim.join(out)


def accounted_cells(rows, lines, positions, bks, kinds, jtype):
    """The accounted bytes of a build whose records are `rows` (lists of cells), read at `lines` (the line each record ended on) and `positions` (the bytes consumed once it was read),
    by the formula of docs/joins.md 6.2 as J2 states it: inner and left, sum of (record bytes + 16) over the rows and (key bytes + 60) over distinct keys; semi and anti the keys
    only at (key bytes + 44). Answers the same dict as join_ref2.accounted (rows, distinct, held, before_fail, fail_row, fail_line, fail_bytes)."""
    seen = set()
    held = 0
    costs = []
    for n, cells in enumerate(rows, 1):
        k = R.key_of(cells, bks, kinds, "build", n, [b"?"] * len(cells), lines[n - 1])
        cost = 0
        if jtype in ("inner", "left"):
            cost = len(csv_record(cells)) + 16
        if k not in seen:
            seen.add(k)
            cost += len(R.key_bytes(cells, bks, kinds)) + (60 if jtype in ("inner", "left") else 44)
        held += cost
        costs.append((cost, n, lines[n - 1], positions[n - 1]))
    last = [c for c in costs if c[0] > 0]
    return {"rows": len(rows), "distinct": len(seen), "held": held, "before_fail": held - last[-1][0] if last else 0,
            "fail_row": last[-1][1] if last else 0, "fail_line": last[-1][2] if last else 0, "fail_bytes": last[-1][3] if last else 0}


def jsonl_positions(data):
    """The bytes consumed once each non-blank record of a JSON-lines file is read: the end of its line (its LF included, the BOM counted)."""
    out = []
    pos = 0
    for n, raw in enumerate(data.split(b"\n")):
        pos += len(raw) + 1
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if n == 0 and raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        if raw.strip(b" \t\r") == b"":
            continue
        out.append(min(pos, len(data)))
    return out


# ---- writers: a table as a JSON-lines file ---------------------------------------------------------------------------------------------------------------


def json_text(cell, rng):
    """JSON text that reads back as exactly `cell` (UTF-8 text): a number as written when the cell is one, true or false as the word, else a string with the escapes of a random
    style (all non-ASCII as \\u escapes, or UTF-8 as it is)."""
    if NUMBER.match(cell) and rng.random() < 0.7:
        return cell.decode("ascii")
    if cell in (b"true", b"false") and rng.random() < 0.6:
        return cell.decode("ascii")
    return json.dumps(cell.decode("utf-8"), ensure_ascii=rng.random() < 0.5)


def object_text(pairs):
    """Nested JSON text for [(path, jsontext)] with the dots of a path as nesting: {"a.b": x} is {"a":{"b":x}}. Shared prefixes share their object."""
    tree = {}
    for path, text in pairs:
        node = tree
        segs = split_path(path)
        for s in segs[:-1]:
            node = node.setdefault(s, {})
        node[segs[-1]] = text

    def render(node):
        if isinstance(node, dict):
            return "{" + ",".join("%s:%s" % (json.dumps(k), render(v)) for k, v in node.items()) + "}"
        return node
    return render(tree)


def write_jsonl(path, header, rows, rng, crlf=False, bom=False, blanks=False, extra_keys=False, shuffle=False, final_newline=True, spaces=False, holes=False):
    """A JSON-lines file of `rows` under the declared `header` (paths): one object per row, the cells as `json_text` renders them, keys in a random order when `shuffle`, an
    undeclared key added when `extra_keys`, a blank line (empty, spaces, a lone CR) between records when `blanks`, an empty cell written as null or left out when `holes` (read back
    as the empty cell under `--absent empty`)."""
    eol = "\r\n" if crlf else "\n"
    out = []
    for r in rows:
        pairs = []
        for h, c in zip(header, r):
            if holes and c == b"" and rng.random() < 0.8:
                # an empty cell as the absent cell: null, or no key at all (read as empty under --absent empty)
                if rng.random() < 0.5:
                    pairs.append((h.decode("utf-8"), "null"))
                continue
            pairs.append((h.decode("utf-8"), json_text(c, rng)))
        if shuffle:
            rng.shuffle(pairs)
        if extra_keys:
            pairs.insert(rng.randint(0, len(pairs)), ("zz_extra", '"x"'))
        text = object_text(pairs)
        if spaces:
            text = " " + text + "  "
        out.append(text)
        if blanks and rng.random() < 0.3:
            out.append(rng.choice(["", "   ", "\t"]))
    body = eol.join(out) + (eol if final_newline and out else "")
    data = body.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def fields_arg(header):
    return ",".join(h.decode("utf-8") for h in header)


# ---- a case: two sides, each a format ---------------------------------------------------------------------------------------------------------------------


def delim_word(d):
    return "tab" if d == "\t" else d


class Side:
    """One side of a case: its table and how it is written. kind 'csv' or 'jsonl'; delim for csv; the file options of write_csv / write_jsonl."""

    def __init__(self, kind, header, rows, delim=",", **opts):
        self.kind, self.header, self.rows, self.delim, self.opts = kind, header, rows, delim, opts

    def write(self, path, rng):
        if self.kind == "jsonl":
            return write_jsonl(path, self.header, self.rows, rng, **self.opts)
        return J.write_csv(path, self.header, self.rows, delim=self.delim, **self.opts)

    def flags(self, probe):
        """The flags that say how this side is read: the probe's `--input-format` and `--fields` (and `--delimiter`), the build's `--join-input-format` and `--join-fields`. A build's
        delimiter is a flag only when it is not the probe's (`--join-delimiter`), which the case knows."""
        if self.kind == "jsonl":
            return (["--input-format", "jsonl", "--fields", fields_arg(self.header)] if probe else ["--join-input-format", "jsonl", "--join-fields", fields_arg(self.header)])
        if probe and self.delim != ",":
            return ["--delimiter", delim_word(self.delim)]
        return []

    def read(self, path, absent="refuse"):
        with open(path, "rb") as f:
            data = f.read()
        return read_side(data, self.kind, self.header, self.delim.encode(), absent)


def table_pair(rng, pn, bn, nrows_p, nrows_b, keys, pk=0, bk=0, pextra=None, bextra=None):
    """Two tables of UTF-8 cells (headers p0.. and b0.., the key columns drawn from `keys`)."""
    ph, bh = J.header("p", pn), J.header("b", bn)
    pr = J.table(rng, pn, nrows_p, pk, keys, pextra)
    br = J.table(rng, bn, nrows_b, bk, keys, bextra)
    return ph, pr, bh, br


# cells that a JSON string must escape, that are not ASCII, that are numbers written every way a JSON number is, and the ones of a text key that look alike
ESCAPES = [b'q"uote', b"back\\slash", b"tab\there", b"new\nline", b"cr\rlf", b"slash/solidus", b"\x01\x02ctrl", b"a,b", b"semi;colon", b"mix \"\\\n\t,;"]
UNICODE = ["é".encode(), "e\u0301".encode(), "日本語".encode(), "😀".encode(), "Ünï".encode(), "ß".encode(), "\u00a0nbsp".encode(), "Ω".encode(), "ǅ".encode()]
BIG_INTS = [b"0", b"-0", b"7", b"9223372036854775807", b"9223372036854775808", b"-9223372036854775808", b"-9223372036854775809", b"123456789012345678901234567890",
            b"18446744073709551616", b"-99999999999999999999999999"]
FLOATS17 = [b"0.30000000000000004", b"0.1", b"1.7976931348623157e308", b"5e-324", b"1.0", b"1E5", b"1e+5", b"-0.0", b"123456789.12345678", b"0.12345678901234567",
            b"1.00000000000000011", b"2.2250738585072014e-308", b"100", b"1e2", b"3.14159265358979323846"]
LOOKALIKE_JSON = [b"true", b"false", b"007", b"+7", b"1.50", b"null-ish", b"", b" 7", b"7 ", b"NaN", b"0x10", b"1e2", b"100"]
