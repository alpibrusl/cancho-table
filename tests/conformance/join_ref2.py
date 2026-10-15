"""The reference of slice J2 (docs/joins.md sections 4.2, 5, 8.1 point 3 and 11): left, semi and anti joins, typed keys, composite keys, USING, padding.

The definition the tool is held to (J1's, extended):

    the output is the probe rows in the probe's order; for each probe row, the build rows whose key is equal, in the build file's order;
    * inner: the probe cells then the build cells; a probe row with no build row is not in it.
    * left: the same, and a probe row with no build row once, with every build cell the empty text (there is no null).
    * semi: each probe row that has at least one match, once, the probe cells only.
    * anti: each probe row that has none, once, the probe cells only.
    * a key is a list of pairs. A text pair is equal when the cells are the same bytes. A typed pair (`:int`, `:dec(S)`, `:float`) reads both cells as the type and is equal
      when the VALUES are equal (007 and 7, -0 and 0, 1.5 and 1.50 at :dec(2)); a cell that is not of the type is a refusal, as it is everywhere (an empty cell is not one).
      A composite key is equal when every pair is.
    * USING (`--on NAME`): the pair NAME=NAME, and the build column NAME is left out of the joined row.
    * The refusals come in reading order: the build file first (its rows in file order, the pairs of a row left to right), then the probe's.

Cells are `bytes` (a file is read as latin-1, so every byte is one character). The oracles are SQLite and DuckDB, each given tables with a row-number column and
asked `ORDER BY p.rn, b.rn`; text keys compare VARCHAR as it is (DuckDB `all_varchar=true` and a `nullstr` no cell has, never inference), a typed key an explicit CAST
(computed by SQL, for tables whose cells all are of the type), and the tool is also held to the Python value semantics here.
"""

import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import numbers_ref as N
import join_ref as J

DUCKDB = shutil.which("duckdb")
INT_MIN, INT_MAX = -(1 << 63), (1 << 63) - 1
INT = re.compile(r"^[+-]?[0-9]+$")


class Refusal(Exception):
    """A key cell that is not of its type: where, and which rule."""

    def __init__(self, rule, side, row, line, column, value, direction=None, scale=None):
        super().__init__(rule)
        self.rule, self.side, self.row, self.line, self.column, self.value, self.direction, self.scale = rule, side, row, line, column, value, direction, scale


def typed(cell, kind):
    """The value of `cell` read as `kind` ('int', ('dec', S) or 'float'): ('ok', value) or ('refuse', rule, direction)."""
    s = cell.decode("latin-1")
    if kind == "int":
        if not INT.match(s):
            return ("refuse", "value.not-integer", None)
        v = int(s)
        if v < INT_MIN or v > INT_MAX:
            return ("refuse", "value.integer-overflow", None)
        return ("ok", v)
    if kind == "float":
        r = N.flt(s)
        return ("ok", r[1]) if r[0] == "ok" else ("refuse", r[1], r[2])
    r = N.dec(s, kind[1])
    return ("ok", r[1]) if r[0] == "ok" else ("refuse", r[1], None)


def kind_of(suffix):
    """':int' -> 'int', ':dec(2)' -> ('dec', 2), ':float' -> 'float', None -> None (text)."""
    if suffix is None:
        return None
    if suffix == "int" or suffix == "float":
        return suffix
    return ("dec", int(re.match(r"dec\((\d+)\)$", suffix).group(1)))


def suffix_of(kind):
    if kind is None:
        return ""
    if isinstance(kind, tuple):
        return ":dec(%d)" % kind[1]
    return ":" + kind


def on_arg(pairs, pheader, bheader):
    """The `--on` text for pairs [(pk, bk, kind, using)]: names by header; a USING pair is the bare name."""
    out = []
    for pk, bk, kind, using in pairs:
        if using:
            out.append(pheader[pk].decode() + suffix_of(kind))
        else:
            out.append("%s=%s%s" % (pheader[pk].decode(), bheader[bk].decode(), suffix_of(kind)))
    return ",".join(out)


def key_of(row, cols, kinds, side, number, header, line):
    """The key of one row: a tuple of the values of its pairs, or a Refusal for the first pair (left to right) whose cell is not of its type."""
    out = []
    for c, kind in zip(cols, kinds):
        cell = row[c]
        if kind is None:
            out.append(cell)
        else:
            r = typed(cell, kind)
            if r[0] != "ok":
                raise Refusal(r[1], side, number, line, header[c], cell, r[2], kind[1] if isinstance(kind, tuple) else None)
            out.append(r[1])
    return tuple(out)


def ref_join(pheader, prows, bheader, brows, pairs, jtype="inner", prefix=b"", plines=None, blines=None):
    """(header, rows), or raises Refusal. `pairs` is [(pk, bk, kind, using)]. Lines are 2.. by default (a file of single-line records with a header)."""
    pks = [p[0] for p in pairs]
    bks = [p[1] for p in pairs]
    kinds = [p[2] for p in pairs]
    dropped = {p[1] for p in pairs if p[3]}
    index = {}
    for n, r in enumerate(brows, 1):
        k = key_of(r, bks, kinds, "build", n, bheader, blines[n - 1] if blines else n + 1)
        index.setdefault(k, []).append(r)
    keep = [j for j in range(len(bheader)) if j not in dropped]
    if jtype in ("semi", "anti"):
        header = list(pheader)
    else:
        header = list(pheader) + [prefix + bheader[j] for j in keep]
    out = []
    for n, p in enumerate(prows, 1):
        k = key_of(p, pks, kinds, "probe", n, pheader, plines[n - 1] if plines else n + 1)
        ms = index.get(k, ())
        if jtype == "inner" or jtype == "left":
            for b in ms:
                out.append(list(p) + [b[j] for j in keep])
            if not ms and jtype == "left":
                out.append(list(p) + [b""] * len(keep))
        elif jtype == "semi":
            if ms:
                out.append(list(p))
        elif not ms:
            out.append(list(p))
    return header, out


def same_multiset(a, b):
    return Counter(map(tuple, a)) == Counter(map(tuple, b))


# ---- the oracles -----------------------------------------------------------------------------------------------------------------------------------------


def _cast_sqlite(col, kind):
    if kind is None:
        return col
    if kind == "int":
        return "CAST(%s AS INTEGER)" % col
    return "CAST(%s AS REAL)" % col          # :dec(S) and :float: the cells of the tables are small, so a double is exact enough to compare; SQLite has no decimal


def sqlite_join(pheader, prows, bheader, brows, pairs, jtype):
    """The rows by SQLite (TEXT columns, binary collation; typed keys by CAST), or None when a typed key would make SQL guess (a cell that is not of the type)."""
    con = sqlite3.connect(":memory:")
    try:
        np_, nb = len(pheader), len(bheader)
        con.execute("CREATE TABLE p (rn INTEGER, %s)" % ", ".join("c%d TEXT" % i for i in range(np_)))
        con.execute("CREATE TABLE b (rn INTEGER, %s)" % ", ".join("c%d TEXT" % i for i in range(nb)))
        con.executemany("INSERT INTO p VALUES (%s)" % ",".join("?" * (np_ + 1)), [(i, *[c.decode("latin-1") for c in r]) for i, r in enumerate(prows)])
        con.executemany("INSERT INTO b VALUES (%s)" % ",".join("?" * (nb + 1)), [(i, *[c.decode("latin-1") for c in r]) for i, r in enumerate(brows)])
        cond = " AND ".join("%s = %s" % (_cast_sqlite("p.c%d" % pk, kind), _cast_sqlite("b.c%d" % bk, kind)) for pk, bk, kind, _ in pairs)
        pcols = ", ".join("p.c%d" % i for i in range(np_))
        keep = [j for j in range(nb) if j not in {p[1] for p in pairs if p[3]}]
        bcols = ", ".join("b.c%d" % j for j in keep)
        if jtype in ("inner", "left"):
            sql = "SELECT %s%s FROM p %s JOIN b ON %s ORDER BY p.rn, b.rn" % (pcols, (", " + bcols) if bcols else "", "LEFT" if jtype == "left" else "", cond)
        else:
            sql = "SELECT %s FROM p WHERE %sEXISTS (SELECT 1 FROM b WHERE %s) ORDER BY p.rn" % (pcols, "NOT " if jtype == "anti" else "", cond)
        rows = [[(c if c is not None else "").encode("latin-1") for c in row] for row in con.execute(sql)]
        return rows, sql
    finally:
        con.close()


def _cast_duck(col, kind):
    if kind is None:
        return col
    if kind == "int":
        return "CAST(%s AS HUGEINT)" % col
    if kind == "float":
        return "CAST(%s AS DOUBLE)" % col
    return "CAST(%s AS DECIMAL(18,%d))" % (col, kind[1])


def duckdb_join(pheader, prows, bheader, brows, pairs, jtype):
    """The same by DuckDB's command line (None when it is not installed): read with `all_varchar=true`, a `nullstr` no cell has and no inference; a typed key by explicit CAST."""
    if not DUCKDB:
        return None, None
    with tempfile.TemporaryDirectory(prefix="duck-") as d:
        pp, bp = os.path.join(d, "p.csv"), os.path.join(d, "b.csv")
        J.write_csv(pp, [b"rn"] + [b"c%d" % i for i in range(len(pheader))], [[b"%d" % i] + list(r) for i, r in enumerate(prows)])
        J.write_csv(bp, [b"rn"] + [b"c%d" % i for i in range(len(bheader))], [[b"%d" % i] + list(r) for i, r in enumerate(brows)])
        keep = [j for j in range(len(bheader)) if j not in {p[1] for p in pairs if p[3]}]
        cols = ", ".join(["p.c%d" % i for i in range(len(pheader))] + (["b.c%d" % j for j in keep] if jtype in ("inner", "left") else []))
        cond = " AND ".join("%s = %s" % (_cast_duck("p.c%d" % pk, kind), _cast_duck("b.c%d" % bk, kind)) for pk, bk, kind, _ in pairs)
        opts = "header=true, all_varchar=true, nullstr='@@no-such-cell@@', quote='\"', escape='\"', auto_detect=false, columns={%s}"
        pcols = ", ".join(["'rn': 'VARCHAR'"] + ["'c%d': 'VARCHAR'" % i for i in range(len(pheader))])
        bcols = ", ".join(["'rn': 'VARCHAR'"] + ["'c%d': 'VARCHAR'" % i for i in range(len(bheader))])
        src_p = "read_csv('%s', %s) p" % (pp, opts % pcols)
        src_b = "read_csv('%s', %s) b" % (bp, opts % bcols)
        if jtype in ("inner", "left"):
            sel = "SELECT %s FROM %s %s JOIN %s ON %s ORDER BY CAST(p.rn AS BIGINT), CAST(b.rn AS BIGINT)" % (cols, src_p, "LEFT" if jtype == "left" else "", src_b, cond)
        else:
            sel = "SELECT %s FROM %s WHERE %sEXISTS (SELECT 1 FROM %s WHERE %s) ORDER BY CAST(p.rn AS BIGINT)" % (cols, src_p, "NOT " if jtype == "anti" else "", src_b, cond)
        sql = "COPY (%s) TO '/dev/stdout' (FORMAT CSV, HEADER false)" % sel
        p = subprocess.run([DUCKDB, "-c", sql], capture_output=True, timeout=300)
        if p.returncode:
            raise AssertionError("duckdb: %s\n%s" % (p.stderr.decode()[:400], sql))
        return J.read_csv_bytes(p.stdout), sql


# ---- the accounted bytes (docs/joins.md 6.2, J2: composite and typed keys, keys only for semi and anti) ------------------------------------------------------


def key_bytes(row, cols, kinds):
    """The bytes of the stored key: one pair is the cell (text) or its 8 bytes (typed); several are each preceded by a 4-byte length."""
    parts = []
    for c, kind in zip(cols, kinds):
        if kind is None:
            parts.append(row[c])
        else:
            parts.append(b"\0" * 8)             # the content does not matter for the size; equality is by value (key_of)
    if len(parts) == 1:
        return parts[0]
    return b"".join(b"\0\0\0\0" + x for x in parts)


def accounted(path, pairs, jtype="inner", delim=","):
    """The accounted bytes of the build `path`, computed here from the file and not by the program: inner and left, the sum over data records of (record bytes + 16) and over
    distinct keys of (key bytes + 60); semi and anti hold the keys only: the sum over distinct keys of (key bytes + 44). The files have single-line records.
    Answers rows, distinct, the size, the total (`held`), the total before the row that would fail a bound one byte lower (`before_fail`), and that row's number and line."""
    data = open(path, "rb").read()
    size = len(data)
    start = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    bks = [p[1] for p in pairs]
    kinds = [p[2] for p in pairs]
    seen = set()
    held = 0
    costs = []                                  # (cost, row, line, bytes consumed once the record is read) for every record
    rows = 0
    import csv as _csv
    import io as _io
    pos = start
    for n, raw in enumerate(data[start:].splitlines(keepends=True)):
        pos += len(raw)
        line = raw.rstrip(b"\n")
        if line.endswith(b"\r"):
            line = line[:-1]
        if n == 0 or line == b"":
            continue
        rows += 1
        rec = next(_csv.reader(_io.StringIO(line.decode("latin-1")), delimiter=delim))
        cells = [c.encode("latin-1") for c in rec]
        k = key_of(cells, bks, kinds, "build", rows, [b"?"] * len(cells), n + 1)
        cost = 0
        if jtype in ("inner", "left"):
            cost = len(line) + 16
        if k not in seen:
            seen.add(k)
            cost += len(key_bytes(cells, bks, kinds)) + (60 if jtype in ("inner", "left") else 44)
        held += cost
        costs.append((cost, rows, n + 1, pos))
    last = [c for c in costs if c[0] > 0]
    before_fail = held - last[-1][0] if last else 0
    return {"rows": rows, "distinct": len(seen), "size": size, "held": held, "before_fail": before_fail,
            "fail_row": last[-1][1] if last else 0, "fail_line": last[-1][2] if last else 0, "fail_bytes": last[-1][3] if last else 0,
            "rows_held_before_fail": (sum(1 for c in costs[:last[-1][1] - 1]) if last and jtype in ("inner", "left") else 0)}


# ---- generators ------------------------------------------------------------------------------------------------------------------------------------------

INT_SPELLINGS = [b"7", b"007", b"+7", b"-0", b"0", b"00", b"-7", b"-007", b"12", b"012", b"9223372036854775807", b"-9223372036854775808", b"1", b"42"]
DEC2_SPELLINGS = [b"1.5", b"1.50", b"01.5", b"+1.5", b"1", b"1.0", b"1.00", b"-0", b"-0.00", b"0", b".5", b"0.50", b"2.25", b"-1.5", b"999999999999999.99"]
FLOAT_SPELLINGS = [b"1.5", b"1.50", b"01.5", b"1.5e0", b"15e-1", b"0.15E1", b"-0", b"0", b"0.0", b"-0.0e5", b"2", b"2.0", b"20e-1", b"1e2", b"100", b"0.1", b"1e-1", b"-3.25"]
POOLS = {None: [b"k%d" % i for i in range(7)] + [b"", b"007", b"7", b"-0", b"0", b"NULL"], "int": INT_SPELLINGS, ("dec", 2): DEC2_SPELLINGS, "float": FLOAT_SPELLINGS}


def hdr(prefix, n):
    return J.header(prefix, n)


def rows_of(rng, n, ncols, keycols, pools, tag):
    """`n` rows of `ncols` cells; the columns in `keycols` drawn from the pool of their pair (a list of pools in the same order), the others short tokens."""
    out = []
    for i in range(n):
        r = []
        for c in range(ncols):
            if c in keycols:
                r.append(rng.choice(pools[keycols.index(c)]))
            else:
                r.append(b"%s%d_%d" % (tag, c, i))
        out.append(r)
    return out


def shapes(seed=3):
    """(name, pheader, prows, bheader, brows, pairs, opts): the shapes of docs/joins.md section 10 that J2 adds (and the old ones, run with the new types)."""
    import random
    rng = random.Random(seed)
    out = []

    def add(name, pn, pr, bn, br, pairs, **opts):
        ph, bh = hdr("p", pn), hdr("b", bn)
        for pk, bk, _, using in pairs:
            if using:
                bh[bk] = ph[pk]                # USING needs the one name on both sides
        out.append((name, ph, pr, bh, br, pairs, opts))

    dom = [b"k%d" % i for i in range(8)]
    add("text: duplicates on both sides", 3, J.table(rng, 3, 60, 0, dom), 2, J.table(rng, 2, 40, 0, dom), [(0, 0, None, False)])
    add("text: no matches", 2, J.table(rng, 2, 30, 0, [b"x%d" % i for i in range(5)]), 2, J.table(rng, 2, 30, 0, [b"y%d" % i for i in range(5)]), [(0, 0, None, False)])
    add("text: every row matches", 2, J.table(rng, 2, 30, 0, dom), 2, [[k, b"b"] for k in dom], [(0, 0, None, False)])
    add("text: big fan-out", 2, J.table(rng, 2, 300, 0, [b"hot"]), 2, J.table(rng, 2, 300, 0, [b"hot"]), [(0, 0, None, False)])
    add("text: header only on the probe", 2, [], 2, J.table(rng, 2, 10, 0, dom), [(0, 0, None, False)])
    add("text: header only on the build", 2, J.table(rng, 2, 10, 0, dom), 2, [], [(0, 0, None, False)])
    add("text: both header only", 2, [], 2, [], [(0, 0, None, False)])
    add("text: empty keys match empty keys", 3, [[b"", b"p0", b"x"], [b"a", b"p1", b"y"], [b"", b"p2", b"z"], [b"q", b"p3", b"w"]], 2, [[b"", b"b0"], [b"a", b"b1"], [b"", b"b2"], [b"zz", b"b3"]], [(0, 0, None, False)])
    add("text: look-alike keys are bytes", 2, [[k, b"p%d" % i] for i, k in enumerate(J.LOOKALIKES * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(J.LOOKALIKES)], [(0, 0, None, False)])
    add("text: awkward quoted keys", 2, [[k, b"p%d" % i] for i, k in enumerate(J.AWKWARD * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(reversed(J.AWKWARD))], [(0, 0, None, False)])
    add("text: CRLF and a BOM on each side", 3, J.table(rng, 3, 20, 0, dom), 2, J.table(rng, 2, 20, 0, dom), [(0, 0, None, False)], crlf_p=True, bom_p=True, crlf_b=True, bom_b=True)
    add("text: one-column files", 1, [[k] for k in dom * 3], 1, [[k] for k in dom[:5]], [(0, 0, None, False)])
    add("text: one-column build, two-column probe, left (padding is one empty cell)", 2, J.table(rng, 2, 12, 0, dom), 1, [[k] for k in dom[:4]], [(0, 0, None, False)])
    add("text: non-UTF-8 keys", 2, [[k, b"p%d" % i] for i, k in enumerate([b"\xff", b"\xfe\xff", b"\x80\x81", b"ok"] * 3)], 2, [[k, b"b%d" % i] for i, k in enumerate([b"\xff", b"\x80\x81", b"ok", b"\xff"])], [(0, 0, None, False)])
    # typed keys: the spellings of one value are one key
    add("int: 007 and 7, -0 and 0, +7", 2, [[k, b"p%d" % i] for i, k in enumerate(INT_SPELLINGS * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(INT_SPELLINGS)], [(0, 0, "int", False)])
    add("int: duplicates, build is a subset", 3, rows_of(rng, 80, 3, [1], [INT_SPELLINGS], b"p"), 2, rows_of(rng, 10, 2, [0], [INT_SPELLINGS[:6]], b"b"), [(1, 0, "int", False)])
    add("dec(2): 1.5 and 1.50 and 01.5", 2, [[k, b"p%d" % i] for i, k in enumerate(DEC2_SPELLINGS * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(DEC2_SPELLINGS)], [(0, 0, ("dec", 2), False)])
    add("float: one double, many spellings", 2, [[k, b"p%d" % i] for i, k in enumerate(FLOAT_SPELLINGS * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(FLOAT_SPELLINGS)], [(0, 0, "float", False)])
    add("int: the key of a typed pair in another column on each side", 4, rows_of(rng, 50, 4, [2], [INT_SPELLINGS], b"p"), 3, rows_of(rng, 30, 3, [1], [INT_SPELLINGS], b"b"), [(2, 1, "int", False)])
    add("int: big fan-out", 2, [[b"007", b"p%d" % i] for i in range(200)], 2, [[b"7", b"b%d" % i] for i in range(200)], [(0, 0, "int", False)])
    # composite keys
    add("composite text: two pairs", 3, rows_of(rng, 80, 3, [0, 1], [dom[:4], dom[:3]], b"p"), 3, rows_of(rng, 50, 3, [0, 1], [dom[:4], dom[:3]], b"b"), [(0, 0, None, False), (1, 1, None, False)])
    add("composite text: (ab,c) is not (a,bc)", 3, [[b"ab", b"c", b"p0"], [b"a", b"bc", b"p1"], [b"", b"abc", b"p2"], [b"abc", b"", b"p3"]], 3, [[b"a", b"bc", b"b0"], [b"ab", b"c", b"b1"], [b"abc", b"", b"b2"]], [(0, 0, None, False), (1, 1, None, False)])
    add("composite mixed: text, int", 3, rows_of(rng, 80, 3, [0, 1], [dom[:3], INT_SPELLINGS[:7]], b"p"), 3, rows_of(rng, 50, 3, [0, 1], [dom[:3], INT_SPELLINGS[:7]], b"b"), [(0, 0, None, False), (1, 1, "int", False)])
    add("composite typed: int, dec(2), float in different columns", 5, rows_of(rng, 80, 5, [1, 3, 4], [INT_SPELLINGS[:4], DEC2_SPELLINGS[:7], FLOAT_SPELLINGS[:7]], b"p"), 4, rows_of(rng, 60, 4, [0, 2, 3], [INT_SPELLINGS[:4], DEC2_SPELLINGS[:7], FLOAT_SPELLINGS[:7]], b"b"),
        [(1, 0, "int", False), (3, 2, ("dec", 2), False), (4, 3, "float", False)])
    add("composite: eight pairs", 8, rows_of(rng, 60, 8, list(range(8)), [dom[:2]] * 8, b"p"), 9, rows_of(rng, 40, 9, list(range(8)), [dom[:2]] * 8, b"b"), [(i, i, None, False) for i in range(8)])
    add("composite: the same probe column twice", 3, rows_of(rng, 40, 3, [0, 1], [dom[:3], dom[:3]], b"p"), 3, rows_of(rng, 40, 3, [0, 1], [dom[:3], dom[:3]], b"b"), [(0, 0, None, False), (0, 1, None, False)])
    # USING
    add("using: id on both sides, text", 3, [[k, b"p%d" % i, b"q"] for i, k in enumerate(dom * 3)], 3, [[k, b"b%d" % i, b"r"] for i, k in enumerate(dom[:5] * 2)], [(0, 0, None, True)], prefix=b"r_")
    add("using: typed int, spellings differ", 2, [[k, b"p%d" % i] for i, k in enumerate(INT_SPELLINGS)], 2, [[k, b"b%d" % i] for i, k in enumerate(INT_SPELLINGS[::-1])], [(0, 0, "int", True)])
    add("using: composite, one pair a using and one not", 4, rows_of(rng, 40, 4, [0, 2], [dom[:3], dom[:3]], b"p"), 4, rows_of(rng, 30, 4, [0, 3], [dom[:3], dom[:3]], b"b"), [(0, 0, None, True), (2, 3, None, False)], prefix=b"x_")
    add("using: every build column is a key (nothing of the build is left to show)", 2, [[k, b"p%d" % i] for i, k in enumerate(dom * 2)], 1, [[k] for k in dom[:4]], [(0, 0, None, True)])
    add("using: the key is not the first column of the build", 3, [[k, b"p%d" % i, b"c"] for i, k in enumerate(dom * 2)], 3, [[b"u", k, b"b%d" % i] for i, k in enumerate(dom[:5] * 2)], [(0, 1, None, False)])
    big = [b"id%05d" % i for i in range(1500)]
    add("text: larger tables", 3, J.table(rng, 3, 4000, 1, big), 3, J.table(rng, 3, 2500, 0, big), [(1, 0, None, False)])
    return out


def random_shape(rng):
    """A random table pair with a random key: 1 to 3 pairs, each text or typed, drawn from the pool of its type."""
    pn, bn = rng.randint(1, 5), rng.randint(1, 5)
    npairs = rng.randint(1, min(3, pn, bn))
    pks = rng.sample(range(pn), npairs)
    bks = rng.sample(range(bn), npairs)
    kinds = [rng.choice([None, None, "int", ("dec", 2), "float"]) for _ in range(npairs)]
    pools = [POOLS[k][:rng.randint(2, len(POOLS[k]))] for k in kinds]
    pr = rows_of(rng, rng.choice([0, 1, 5, 30, 100]), pn, pks, pools, b"p")
    br = rows_of(rng, rng.choice([0, 1, 5, 30, 100]), bn, bks, pools, b"b")
    using = rng.random() < 0.2 and npairs == 1
    pheader, bheader = hdr("p", pn), hdr("b", bn)
    pairs = [(a, b, k, False) for a, b, k in zip(pks, bks, kinds)]
    if using:
        bheader = list(bheader)
        bheader[bks[0]] = pheader[pks[0]]
        pairs[0] = (pks[0], bks[0], kinds[0], True)
    opts = {k: rng.random() < 0.15 for k in ("crlf_p", "bom_p", "crlf_b", "bom_b", "quote_all_p", "quote_all_b")}
    return ("random", pheader, pr, bheader, br, pairs, opts)
