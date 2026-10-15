"""The reference of the join gates (docs/joins.md section 10, gate O): a join written the plain way in Python, generators of tables and files, and the two SQL oracles.

The definition the tool is held to (J1: text keys, one pair, inner):

    the output is the probe rows in the probe's order; for each probe row, the build rows whose key cell is the same bytes, in the build file's order;
    each row is the probe row's cells and then the build row's cells; a probe row with no build row is not in it.

Cells are `bytes` (a file is read as latin-1 so that every byte is one character: a key that is not UTF-8 is just a key). The oracles are SQLite (Python's `sqlite3`) and
DuckDB (the installed command line, when there is one), each given the same tables with an added row-number column in both and asked `ORDER BY probe_rn, build_rn`:
SQLite compares TEXT with the default BINARY collation, DuckDB is run with `all_varchar=true` and a `nullstr` that cannot occur, **never with type inference** (docs/joins.md
5.3 measured that its default depends on it), and both are written out here so that a reader sees the SQL.
"""

import csv
import io
import os
import random
import shutil
import sqlite3
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import tempfile

DUCKDB = shutil.which("duckdb")


# ---- files ---------------------------------------------------------------------------------------------------------------------------------------------


def write_csv(path, header, rows, delim=",", crlf=False, bom=False, quote_all=False, final_newline=True):
    """A CSV file of `header` and `rows` (lists of bytes), as `csv.writer` writes it (minimal quoting, or every field quoted)."""
    eol = "\r\n" if crlf else "\n"
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=delim, lineterminator=eol, quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL)
    w.writerow([h.decode("latin-1") for h in header])
    for r in rows:
        w.writerow([c.decode("latin-1") for c in r])
    text = buf.getvalue()
    if not final_newline and text.endswith(eol):
        text = text[:-len(eol)]
    data = text.encode("latin-1")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def read_csv_bytes(data, delim=","):
    """The records of CSV text `data` (bytes), each a list of bytes, by Python's csv module (the reference reader of the suite)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("latin-1")
    out = []
    for rec in csv.reader(io.StringIO(text, newline=""), delimiter=delim):
        if rec == []:
            continue                                   # a blank line is no record
        out.append([c.encode("latin-1") for c in rec])
    return out


# ---- the reference -------------------------------------------------------------------------------------------------------------------------------------


def ref_join(pheader, prows, bheader, brows, pk, bk, prefix=b""):
    """The joined header and rows, by the definition."""
    index = {}
    for r in brows:
        index.setdefault(r[bk], []).append(r)
    out = []
    for p in prows:
        for b in index.get(p[pk], ()):
            out.append(list(p) + list(b))
    return list(pheader) + [prefix + h for h in bheader], out


def render_rows(rows, delim=","):
    """Rows as the tool writes csv (minimal quoting; a record of one empty field is `""`)."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=delim, lineterminator="\n")
    for r in rows:
        w.writerow([c.decode("latin-1") for c in r])
    return buf.getvalue().encode("latin-1")


# ---- the oracles ---------------------------------------------------------------------------------------------------------------------------------------


def sqlite_join(pheader, prows, bheader, brows, pk, bk):
    """The rows of `p JOIN b ON p.k = b.k ORDER BY p.rn, b.rn`, by SQLite, TEXT columns and the default (binary) collation."""
    con = sqlite3.connect(":memory:")
    try:
        np_, nb = len(pheader), len(bheader)
        con.execute("CREATE TABLE p (rn INTEGER, %s)" % ", ".join("c%d TEXT" % i for i in range(np_)))
        con.execute("CREATE TABLE b (rn INTEGER, %s)" % ", ".join("c%d TEXT" % i for i in range(nb)))
        con.executemany("INSERT INTO p VALUES (%s)" % ",".join("?" * (np_ + 1)), [(i, *[c.decode("latin-1") for c in r]) for i, r in enumerate(prows)])
        con.executemany("INSERT INTO b VALUES (%s)" % ",".join("?" * (nb + 1)), [(i, *[c.decode("latin-1") for c in r]) for i, r in enumerate(brows)])
        sql = "SELECT %s, %s FROM p JOIN b ON p.c%d = b.c%d ORDER BY p.rn, b.rn" % (
            ", ".join("p.c%d" % i for i in range(np_)), ", ".join("b.c%d" % i for i in range(nb)), pk, bk)
        return [[c.encode("latin-1") for c in row] for row in con.execute(sql)], sql
    finally:
        con.close()


def duckdb_join(pheader, prows, bheader, brows, pk, bk):
    """The same by DuckDB 1.x's command line, or None when it is not installed: the tables are files with a row-number column, read with
    `all_varchar=true` and a `nullstr` that no cell has, so that an empty cell is the empty text and nothing is inferred."""
    if not DUCKDB:
        return None, None
    with tempfile.TemporaryDirectory(prefix="duck-") as d:
        pp, bp = os.path.join(d, "p.csv"), os.path.join(d, "b.csv")
        write_csv(pp, [b"rn"] + [b"c%d" % i for i in range(len(pheader))], [[b"%d" % i] + list(r) for i, r in enumerate(prows)])
        write_csv(bp, [b"rn"] + [b"c%d" % i for i in range(len(bheader))], [[b"%d" % i] + list(r) for i, r in enumerate(brows)])
        cols = ", ".join(["p.c%d" % i for i in range(len(pheader))] + ["b.c%d" % i for i in range(len(bheader))])
        opts = "header=true, all_varchar=true, nullstr='@@no-such-cell@@', quote='\"', escape='\"', auto_detect=false, columns={%s}"
        pcols = ", ".join(["'rn': 'VARCHAR'"] + ["'c%d': 'VARCHAR'" % i for i in range(len(pheader))])
        bcols = ", ".join(["'rn': 'VARCHAR'"] + ["'c%d': 'VARCHAR'" % i for i in range(len(bheader))])
        sql = ("COPY (SELECT %s FROM read_csv('%s', " + opts % pcols + ") p JOIN read_csv('%s', " + opts % bcols +
               ") b ON p.c%d = b.c%d ORDER BY CAST(p.rn AS BIGINT), CAST(b.rn AS BIGINT)) TO '/dev/stdout' (FORMAT CSV, HEADER false)") % (cols, pp, bp, pk, bk)
        p = subprocess.run([DUCKDB, "-c", sql], capture_output=True, timeout=300)
        if p.returncode:
            raise AssertionError("duckdb: %s\n%s" % (p.stderr.decode()[:400], sql))
        return read_csv_bytes(p.stdout), sql


# ---- generators ----------------------------------------------------------------------------------------------------------------------------------------

# keys that look alike: the cells an exact-bytes comparison must keep apart (and a SQL tool that infers types joins)
LOOKALIKES = [b"7", b"007", b"-0", b"0", b"+7", b"1.5", b"1.50", b"01.5", b"1e2", b"100", b" 7", b"7 ", b"NULL", b"null", b"NaN", b"", b"a", b"A", b"0x10", b"16", b"\xc3\xa9", b"e\xcc\x81",
              b"\xff\xfe", b"\x80"]
AWKWARD = [b"a,b", b'say "hi"', b"two\nlines", b"cr\r\nlf", b"semi;colon", b" lead", b"tab\there", b"q'q", b"back\\slash", b"#1", b"=", b"ab", b"a" * 300]


def table(rng, ncols, nrows, keycol, keys, extra=None):
    """`nrows` rows of `ncols` cells: the key column from `keys`, the others short tokens."""
    rows = []
    for i in range(nrows):
        r = []
        for c in range(ncols):
            if c == keycol:
                r.append(rng.choice(keys))
            else:
                r.append(b"%s%d_%d" % (b"v" if extra is None else extra, c, i))
        rows.append(r)
    return rows


def header(prefix, n):
    return [("%s%d" % (prefix, i)).encode() for i in range(n)]


def cases(seed=1):
    """(name, pheader, prows, bheader, brows, pk, bk, options) for the shapes of docs/joins.md section 10 that a text join has. `options`: crlf/bom/quote_all for each side."""
    rng = random.Random(seed)
    out = []

    def add(name, pn, pr, bn, br, pk=0, bk=0, **opts):
        out.append((name, header("p", pn), pr, header("b", bn), br, pk, bk, opts))

    def add_named(name, ph, pr, bh, br, pk=0, bk=0, **opts):
        out.append((name, ph, pr, bh, br, pk, bk, opts))

    dom = [b"k%d" % i for i in range(8)]
    add("duplicates on both sides", 3, table(rng, 3, 60, 0, dom), 2, table(rng, 2, 40, 0, dom))
    add("key not in the first column", 4, table(rng, 4, 50, 2, dom), 3, table(rng, 3, 30, 1, dom), 2, 1)
    add("no matches", 2, table(rng, 2, 30, 0, [b"x%d" % i for i in range(5)]), 2, table(rng, 2, 30, 0, [b"y%d" % i for i in range(5)]))
    add("all match one key (fan-out)", 2, table(rng, 2, 25, 0, [b"same"]), 2, table(rng, 2, 25, 0, [b"same"]))
    add("big fan-out", 2, table(rng, 2, 300, 0, [b"hot"]), 2, table(rng, 2, 300, 0, [b"hot"]))
    add("unique keys both sides", 2, [[b"u%d" % i, b"p"] for i in range(100)], 2, [[b"u%d" % i, b"b"] for i in reversed(range(150))])
    add("probe header only", 2, [], 2, table(rng, 2, 10, 0, dom))
    add("build header only", 2, table(rng, 2, 10, 0, dom), 2, [])
    add("both header only", 2, [], 2, [])
    add("look-alike text keys", 2, [[k, b"p%d" % i] for i, k in enumerate(LOOKALIKES * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(LOOKALIKES)])
    add("empty keys match empty keys", 3, [[b"", b"p0", b"x"], [b"a", b"p1", b"y"], [b"", b"p2", b"z"]], 2, [[b"", b"b0"], [b"a", b"b1"], [b"", b"b2"], [b"zz", b"b3"]])
    add("awkward quoted keys", 2, [[k, b"p%d" % i] for i, k in enumerate(AWKWARD * 2)], 2, [[k, b"b%d" % i] for i, k in enumerate(reversed(AWKWARD))])
    add("quoted everything", 2, table(rng, 2, 20, 0, dom), 2, table(rng, 2, 20, 0, dom), quote_all_p=True, quote_all_b=True)
    add("CRLF and a BOM on each side", 3, table(rng, 3, 20, 0, dom), 2, table(rng, 2, 20, 0, dom), crlf_p=True, bom_p=True, crlf_b=True, bom_b=True)
    add("CRLF probe, LF build, no final newline", 2, table(rng, 2, 20, 0, dom), 2, table(rng, 2, 20, 0, dom), crlf_p=True, nofinal_p=True, nofinal_b=True)
    add("one-column files", 1, [[k] for k in dom * 3], 1, [[k] for k in dom[:5]])
    add("one-column probe, two-column build", 1, [[k] for k in dom * 2], 2, table(rng, 2, 12, 0, dom))
    add("wide rows", 12, table(rng, 12, 20, 7, dom), 9, table(rng, 9, 20, 4, dom), 7, 4)
    add("non-UTF-8 keys", 2, [[k, b"p%d" % i] for i, k in enumerate([b"\xff", b"\xfe\xff", b"\x80\x81", b"ok"] * 3)], 2, [[k, b"b%d" % i] for i, k in enumerate([b"\xff", b"\x80\x81", b"ok", b"\xff"])])
    add("probe is a subset of the build", 2, [[b"k1", b"p"], [b"k3", b"p"]], 2, table(rng, 2, 30, 0, dom))
    add("build is a subset of the probe", 2, table(rng, 2, 30, 0, dom), 2, [[b"k1", b"b"], [b"k3", b"b"]])
    # the key columns have the same name in both files: the case the prefix is for (and USING, a later slice, would be)
    add_named("same-named key columns, with a prefix", [b"id", b"v"], [[k, b"p%d" % i] for i, k in enumerate(dom * 3)], [b"id", b"w"], [[k, b"b%d" % i] for i, k in enumerate(dom[:5] * 2)], prefix=b"r_")
    add_named("same-named columns all round, with a prefix", [b"id", b"name", b"x"], [[k, b"n%d" % i, b"x"] for i, k in enumerate(dom)], [b"id", b"name", b"x"], [[k, b"m%d" % i, b"y"] for i, k in enumerate(dom)], prefix=b"b.")
    big = [b"id%05d" % i for i in range(1500)]
    add("larger tables", 3, table(rng, 3, 4000, 1, big), 3, table(rng, 3, 2500, 0, big), 1, 0)
    return out


def random_case(rng):
    """One random table pair: sizes, key domain size and layout drawn; keys drawn from the look-alikes, the awkward and plain ones."""
    pn, bn = rng.randint(1, 5), rng.randint(1, 5)
    pk, bk = rng.randrange(pn), rng.randrange(bn)
    pool = rng.choice([[b"k%d" % i for i in range(rng.randint(1, 12))], LOOKALIKES, AWKWARD, LOOKALIKES + AWKWARD + [b"k%d" % i for i in range(5)]])
    pr = table(rng, pn, rng.choice([0, 1, 5, 30, 120]), pk, pool)
    br = table(rng, bn, rng.choice([0, 1, 5, 30, 120]), bk, pool)
    opts = {k: rng.random() < 0.2 for k in ("crlf_p", "bom_p", "crlf_b", "bom_b", "quote_all_p", "quote_all_b")}
    return ("random", header("p", pn), pr, header("b", bn), br, pk, bk, opts)


def write_case(d, pheader, prows, bheader, brows, opts):
    """The two files of a case under `d`, as p.csv and b.csv; they are written as the case says (CRLF, BOM, every field quoted, no final newline)."""
    write_csv(os.path.join(d, "p.csv"), pheader, prows, crlf=opts.get("crlf_p", False), bom=opts.get("bom_p", False), quote_all=opts.get("quote_all_p", False), final_newline=not opts.get("nofinal_p", False))
    write_csv(os.path.join(d, "b.csv"), bheader, brows, crlf=opts.get("crlf_b", False), bom=opts.get("bom_b", False), quote_all=opts.get("quote_all_b", False), final_newline=not opts.get("nofinal_b", False))


def effective_rows(rows, ncols):
    """The rows the tool sees: in a one-column file a row that is an empty cell is written `""` by csv.writer, which is a record, so nothing is dropped here."""
    return rows


# ---- the formula of docs/joins.md section 6.2 -----------------------------------------------------------------------------------------------------------


def accounted(path, keycol, delim=b","):
    """The accounted bytes of the build `path`, computed here from the file and not by the program: the sum over data records of (record bytes + 16), and over distinct
    keys of (key bytes + 60). The files have single-line records (a quoted field with a newline is not in these); the record is the line without its LF (and CR), the key
    is the cell with its quotes undone. Answers a dict: rows, distinct, the cost of the last row, the size of the file, the total (`held`) and the total before the last row."""
    rows = 0
    held = 0
    keys = set()
    last = 0
    with open(path, "rb") as f:
        data = f.read()
    size = len(data)
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    for n, line in enumerate(lines):
        if line.endswith(b"\r"):
            line = line[:-1]
        if n == 0 or line == b"":
            continue
        if b'"' in line:
            key = next(csv.reader(io.StringIO(line.decode("latin-1")), delimiter=delim.decode()))[keycol].encode("latin-1")
        else:
            key = line.split(delim)[keycol]
        cost = len(line) + 16
        if key not in keys:
            keys.add(key)
            cost += len(key) + 60
        held += cost
        last = cost
        rows += 1
    return {"rows": rows, "distinct": len(keys), "last": last, "size": size, "held": held, "before_last": held - last}
