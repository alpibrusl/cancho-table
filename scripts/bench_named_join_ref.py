#!/usr/bin/env python3
"""The reference answers of db-benchmark's JOIN questions q1 to q5 and the checks that hold a contender's joined rows to them (docs/named-benchmarks.md, NB3). **This script times nothing.**

    python3 scripts/bench_named_join_ref.py DIR --rows N --layout      # the files are exactly the documented layout (gate a)
    python3 scripts/bench_named_join_ref.py DIR --rows N --answers     # the reference answers and their chk
    python3 scripts/bench_named_join_ref.py DIR --rows N --cross-check # the same answers by SQLite (a second, independent path)
    python3 scripts/bench_named_join_ref.py DIR --rows N --gate [--table build/table]   # table's q1-q5, flag form and --query form, equal the reference

A join answer is a set of wide rows, so the reference is a **fingerprint of the set**: the owner's `chk` (the row count, `sum(v1)` and `sum(v2)`, exactly: the `v` columns as integer millionths, never a
float) and a **row digest**, the sum over rows, modulo 2^64, of a mixing function of (id1, id2, id3, v1, v2, matched). The digest ties the right side's `v2` to the left row it was joined to, which the
sums alone do not (a wrong pairing with the same totals would pass the sums). It is order-free (a join has no ORDER BY, design 3.3). A contender is never the reference.

The canonical joined row (the column order every contender's form is asked for, so one check reads them all) is **the seven cells of x, then the cells of the right table other than its key, in the
right table's order; the last cell is v2**. A left join's unmatched row has empty right cells (the owner's NULL), counted as a row, with v2 = 0 and matched = 0.

Two paths, which share the digest function and the rounding of a number to millionths and nothing else: a streaming pass over the written files with the right table in a dict, and SQLite (the
tables loaded as they are, the join and the sums in SQL, the digest over the rows SQLite returns).
"""

import argparse
import csv
import json
import os
import pathlib
import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import bench_named_gen as gen  # noqa: E402
import bench_named_join_gen as jgen  # noqa: E402
import bench_named_ref as ref  # noqa: E402

QUESTIONS = ROOT / "scripts" / "bench_named_join_questions.json"
MASK = (1 << 64) - 1
SCALE = 1_000_000
INT = re.compile(r"[0-9]{1,18}$")
FLOAT_INT = re.compile(r"[0-9]{1,18}\.0$")
M1 = 0x9E3779B97F4A7C15
M2 = 0xBF58476D1CE4E5B9

# the five questions as data (the questions file adds the SQL and the table forms)
JOINS = {
    "q1": {"right": "small", "key": "id1", "kind": "inner", "right_cols": ["id4", "v2"]},
    "q2": {"right": "medium", "key": "id2", "kind": "inner", "right_cols": ["id1", "id4", "id5", "v2"]},
    "q3": {"right": "medium", "key": "id2", "kind": "left", "right_cols": ["id1", "id4", "id5", "v2"]},
    "q4": {"right": "medium", "key": "id5", "kind": "inner", "right_cols": ["id1", "id2", "id4", "v2"]},
    "q5": {"right": "big", "key": "id3", "kind": "inner", "right_cols": ["id1", "id2", "id4", "id5", "id6", "v2"]},
}
X_COLUMNS = jgen.HEADERS["x"].split(",")


def load_questions(path=QUESTIONS):
    return json.loads(pathlib.Path(path).read_text())


def joined_columns(qid):
    """The header `table` writes for the flag form: x's columns, then the right table's columns other than the key, each with the prefix `<right>_`."""
    q = JOINS[qid]
    return X_COLUMNS + ["%s_%s" % (q["right"], c) for c in q["right_cols"]]


def to_millionths(text):
    """'97.861311' -> 97861311; '5' -> 5000000; '5e-06' -> 5. A number in [0, 100) with at most six decimals however it is spelled; anything else is ValueError."""
    if len(text) > 40 or text != text.strip() or text == "":
        raise ValueError("not a v of the layout (too long, spaces, or empty): %r" % text[:20])
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise ValueError("not a number: %r" % text)
    if not d.is_finite() or (d != 0 and abs(d.adjusted()) > 40):
        raise ValueError("not a v of the layout: %r" % text)
    m = d * SCALE
    if m != m.to_integral_value() or not 0 <= m < 100 * SCALE:
        raise ValueError("not a v of the layout (six decimals, [0, 100)): %r" % text)
    return int(m)


def digest(id1, id2, id3, v1m, v2m, matched):
    """A mixing of one joined row, a 64-bit number. The sum of these over the rows is the set's fingerprint."""
    h = 0x243F6A8885A308D3
    for v in (id1, id2, id3, v1m, v2m, matched):
        h = ((h ^ (v & MASK)) * M1 + 0x632BE59BD9B4E019) & MASK
        h ^= h >> 29
        h = (h * M2) & MASK
        h ^= h >> 32
    return h


class Acc:
    """The fingerprint of a set of joined rows: the owner's chk (count, sum v1, sum v2) and the row digest."""

    __slots__ = ("count", "sum_v1", "sum_v2", "digest", "matched")

    def __init__(self):
        self.count = self.sum_v1 = self.sum_v2 = self.digest = self.matched = 0

    def add(self, id1, id2, id3, v1m, v2m, matched):
        self.count += 1
        self.sum_v1 += v1m
        self.sum_v2 += v2m
        self.matched += matched
        self.digest = (self.digest + digest(id1, id2, id3, v1m, v2m, matched)) & MASK

    def as_dict(self):
        return {"count": self.count, "sum_v1": self.sum_v1, "sum_v2": self.sum_v2, "matched": self.matched, "digest": self.digest}

    def chk(self):
        """The owner's check string (make_chk of _helpers): the sums, joined by ';' (exact here, six decimals)."""
        return "%d;%s;%s" % (self.count, ref.fmt6(self.sum_v1), ref.fmt6(self.sum_v2))


def paths_of(directory, n):
    d = pathlib.Path(directory)
    return {t: d / jgen.file_name(t, n) for t in jgen.TABLES}


# ---------------------------------------------------------------- path a: a streaming pass over the written files

def reference(directory, n):
    """{qid: Acc} from one pass over x with each right table in a dict keyed by its (unique) join key."""
    p = paths_of(directory, n)
    right = {}
    for qid, q in JOINS.items():
        t = q["right"]
        if t not in right:
            right[t] = _load_right(p[t])
    accs = {qid: Acc() for qid in JOINS}
    with open(p["x"], "r", encoding="ascii", newline="") as f:
        header = f.readline().rstrip("\n").split(",")
        if header != X_COLUMNS:
            raise ValueError("x header is not the layout's: %r" % header)
        at = {c: i for i, c in enumerate(X_COLUMNS)}
        for line in f:
            cells = line.rstrip("\n").split(",")
            i1, i2, i3 = int(cells[0]), int(cells[1]), int(cells[2])
            v1 = to_millionths(cells[6])
            for qid, q in JOINS.items():
                key = cells[at[q["key"]]]
                v2 = right[q["right"]][q["key"]].get(key)
                if v2 is not None:
                    accs[qid].add(i1, i2, i3, v1, v2, 1)
                elif q["kind"] == "left":
                    accs[qid].add(i1, i2, i3, v1, 0, 0)
    return accs


def _load_right(path):
    """{key column: {key text: v2 in millionths}} for the join keys the questions use on this table; a key must be unique."""
    with open(path, "r", encoding="ascii", newline="") as f:
        header = f.readline().rstrip("\n").split(",")
        keys = sorted({q["key"] for q in JOINS.values() if q["right"] == _table_of(header)})
        at = {c: header.index(c) for c in keys}
        out = {c: {} for c in keys}
        for line in f:
            cells = line.rstrip("\n").split(",")
            v2 = to_millionths(cells[-1])
            for c, i in at.items():
                if cells[i] in out[c]:
                    raise ValueError("%s: key %s=%s is not unique" % (path, c, cells[i]))
                out[c][cells[i]] = v2
    return out


def _table_of(header):
    for t, h in jgen.HEADERS.items():
        if t != "x" and h.split(",") == header:
            return t
    raise ValueError("header is not a right table's: %r" % header)


# ---------------------------------------------------------------- path b: SQLite

def reference_sqlite(directory, n):
    """The same fingerprints by SQLite: every table loaded as it is, the join in SQL, the digest over the rows it returns."""
    p = paths_of(directory, n)
    con = sqlite3.connect(":memory:")
    for t in jgen.TABLES:
        cols = jgen.HEADERS[t].split(",")
        con.execute("create table %s (%s)" % (t, ", ".join("%s %s" % (c, "real" if c in ("v1", "v2") else "integer" if c in ("id1", "id2", "id3") else "text") for c in cols)))
        with open(p[t], "r", encoding="ascii", newline="") as f:
            r = csv.reader(f)
            next(r)
            con.executemany("insert into %s values (%s)" % (t, ",".join("?" * len(cols))), r)
    for t, c in (("small", "id1"), ("medium", "id2"), ("medium", "id5"), ("big", "id3")):
        con.execute("create index %s_%s on %s (%s)" % (t, c, t, c))
    out = {}
    m = "cast(round(%s * 1000000) as integer)"
    for qid, q in JOINS.items():
        kind = "left join" if q["kind"] == "left" else "join"
        sql = "select x.id1, x.id2, x.id3, %s, %s, y.%s is not null from x %s %s y on x.%s = y.%s" % (m % "x.v1", "coalesce(%s, 0)" % (m % "y.v2"), q["key"], kind, q["right"], q["key"], q["key"])
        acc = Acc()
        for i1, i2, i3, v1, v2, matched in con.execute(sql):
            acc.add(i1, i2, i3, v1, v2, matched)
        out[qid] = acc
    con.close()
    return out


# ---------------------------------------------------------------- a contender's joined rows against the reference

def check_output(path, qid, expect_header=None):
    """Stream the joined rows a contender wrote to `path` and fingerprint them. Returns (Acc or None, problem or None). The checks, row by row: the canonical number of cells (x's seven then the
    right table's other columns), id1 to id3 whole numbers, id4 to id6 equal to "id" + id1 to id3, v1 and v2 numbers of the layout (v2 empty only on an unmatched row of a left join), and the output
    ends with a newline, with no carriage return and no blank line. `expect_header` is the exact header (table's), else only its width is held."""
    q = JOINS[qid]
    width = 7 + len(q["right_cols"])
    acc = Acc()
    try:
        with open(path, "rb") as fb:
            size = os.fstat(fb.fileno()).st_size
            if size == 0:
                return None, "empty output"
            fb.seek(size - 1)
            if fb.read(1) != b"\n":
                return None, "no final newline"
        with open(path, "r", encoding="utf-8", newline="") as f:
            first = f.readline()
            if "\r" in first or "\0" in first or first.startswith("\ufeff"):
                return None, "a carriage return, a NUL or a byte order mark in the header"
            header = next(csv.reader([first.rstrip("\n")], strict=True), [])
            if expect_header is not None and header != expect_header:
                return None, "columns %s, expected %s" % (_clip(header), _clip(expect_header))
            if len(header) != width:
                return None, "%d columns, expected %d" % (len(header), width)
            if any(h == "" or h != h.strip() for h in header):
                return None, "a header cell is empty or has spaces around it"
            rows = 0
            for cells in csv.reader(f, strict=True):
                rows += 1
                if len(cells) != width:
                    return None, "row %d has %d cells, expected %d" % (rows, len(cells), width)
                for j in (0, 1, 2):
                    if not INT.match(cells[j]):
                        return None, "row %d: id%d %s is not a whole number" % (rows, j + 1, _clip(cells[j]))
                if cells[3] != "id" + cells[0] or cells[4] != "id" + cells[1] or cells[5] != "id" + cells[2]:
                    return None, "row %d: id4 to id6 are not 'id' + id1 to id3: %s" % (rows, _clip(",".join(cells[:6])))
                try:
                    v1 = to_millionths(cells[6])
                    if cells[-1] == "":
                        if q["kind"] != "left":
                            return None, "row %d: v2 is empty in an inner join" % rows
                        if any(c != "" for c in cells[7:]):
                            return None, "row %d: v2 is empty but another cell of the right table is not" % rows
                        acc.add(int(cells[0]), int(cells[1]), int(cells[2]), v1, 0, 0)
                        continue
                    v2 = to_millionths(cells[-1])
                except ValueError as e:
                    return None, "row %d: %s" % (rows, e)
                bad = _right_copies(q, cells)
                if bad:
                    return None, "row %d: %s" % (rows, bad)
                acc.add(int(cells[0]), int(cells[1]), int(cells[2]), v1, v2, 1)
    except UnicodeDecodeError:
        return None, "the output is not UTF-8"
    except (csv.Error, ValueError) as e:
        return None, "not CSV: %s" % e
    return acc, None


def _right_copies(q, cells):
    """The right table's id4, id5 and id6 are "id" + its id1, id2 and id3; the key is the left row's. A matched row whose cells disagree was joined wrongly or written in the wrong columns."""
    right = dict(zip(q["right_cols"], cells[7:]))
    xkey = cells[X_COLUMNS.index(q["key"])]
    for n in (4, 5, 6):
        c = "id%d" % n
        if c not in right:
            continue
        src = "id%d" % (n - 3)
        origin = right.get(src, xkey if src == q["key"] else None)
        if origin is not None and FLOAT_INT.match(origin):
            origin = origin[:-2]     # an engine that gives a column NULLs (pandas, in a left join) writes its integers as 5.0
        if origin is not None and right[c] != "id" + origin:
            return "the right table's %s is %s, not 'id' + its %s (%s)" % (c, _clip(right[c]), src, _clip(origin))
    return None


def _clip(s, n=60):
    s = "".join(ch if ch.isprintable() else "?" for ch in str(s))
    return s if len(s) <= n else s[:n] + "..."


def compare(qid, want, got):
    """None when the fingerprints are equal, else the first thing that differs."""
    for field in ("count", "sum_v1", "sum_v2", "matched", "digest"):
        if getattr(want, field) != getattr(got, field):
            return "%s: %s differs: got %s, the reference has %s" % (qid, field, getattr(got, field), getattr(want, field))
    return None


# ---------------------------------------------------------------- the layout of the files

def validate_layout(directory, n):
    """Problems (a list of text) between the files and the documented layout of scripts/bench_named_join_gen.py: headers, row counts, formats, the sets of keys (distinct counts, uniqueness of a
    right table's key, 90 percent common) and the 'id' + integer copies."""
    p = paths_of(directory, n)
    s = jgen.sizes(n)
    problems = []
    keys = {}
    for t in jgen.TABLES:
        want_rows = n if t == "x" else s[t]
        names = jgen.HEADERS[t].split(",")
        cols = {c: [] for c in names}
        try:
            with open(p[t], "r", encoding="ascii", newline="") as f:
                if f.readline() != jgen.HEADERS[t] + "\n":
                    problems.append("%s: header is not %s" % (t, jgen.HEADERS[t]))
                    continue
                rows = 0
                for line in f:
                    rows += 1
                    cells = line.rstrip("\n").split(",")
                    if len(cells) != len(names) or not line.endswith("\n"):
                        problems.append("%s row %d: %d cells or no newline" % (t, rows, len(cells)))
                        break
                    for c, v in zip(names, cells):
                        cols[c].append(v)
        except OSError as e:
            problems.append("%s: %s" % (t, e))
            continue
        if len(cols[names[0]]) != want_rows:
            problems.append("%s has %d rows, documented %d" % (t, len(cols[names[0]]), want_rows))
        for c in names:
            v = cols[c]
            if c in ("id1", "id2", "id3") and not all(INT.match(x) for x in v):
                problems.append("%s.%s is not whole numbers" % (t, c))
            if c in ("id4", "id5", "id6") and any(a != "id" + b for a, b in zip(v, cols["id" + str(int(c[2]) - 3)])):
                problems.append("%s.%s is not 'id' + %s" % (t, c, "id" + str(int(c[2]) - 3)))
            if c in ("v1", "v2"):
                try:
                    for x in v:
                        to_millionths(x)
                        if "." in x and x.endswith("0"):
                            raise ValueError("trailing zero %r" % x)
                except ValueError as e:
                    problems.append("%s.%s: %s" % (t, c, e))
        keys[t] = cols
    if problems:
        return problems
    # the key sets
    for col, small_t in (("id1", "small"), ("id2", "medium"), ("id3", "big")):
        n_keys = s[small_t]
        left = set(keys["x"][col])
        if len(left) != n_keys:
            problems.append("x.%s has %d distinct values, documented %d" % (col, len(left), n_keys))
        right_col = keys[small_t][col]
        right = set(right_col)
        if len(right) != len(right_col) or len(right) != n_keys:
            problems.append("%s.%s is not a unique key of %d values" % (small_t, col, n_keys))
        common = len(left & right)
        if common != n_keys - n_keys // 10:
            problems.append("%s: %d keys in common, documented %d (90 percent)" % (small_t, common, n_keys - n_keys // 10))
        if len(left - right) != n_keys // 10 or len(right - left) != n_keys // 10:
            problems.append("%s: left-only %d and right-only %d, documented %d each" % (small_t, len(left - right), len(right - left), n_keys // 10))
    # the other right-table key columns draw from the right sets of small (id1) and medium (id2)
    r1, r2 = set(keys["small"]["id1"]), set(keys["medium"]["id2"])
    for t, col, rs in (("medium", "id1", r1), ("big", "id1", r1), ("big", "id2", r2)):
        if not set(keys[t][col]) <= rs:
            problems.append("%s.%s has a key that is not in the right key set of its table" % (t, col))
    return problems


# ---------------------------------------------------------------- the gate of table's forms against the reference

def run_table(argv, binary, cwd=None, stdin=None, timeout=600):
    import procs as subprocess
    p = subprocess.run([binary, *argv], capture_output=True, input=stdin if stdin is not None else b"", timeout=timeout, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def table_bin():
    return ref.table_bin()


def argv_for(template, paths):
    """Fill {X}, {SMALL}, {MEDIUM}, {BIG} (and {RIGHT}, the question's right table) in a questions-file argv with the files' paths."""
    return [a.replace("{X}", str(paths["x"])).replace("{SMALL}", str(paths["small"])).replace("{MEDIUM}", str(paths["medium"])).replace("{BIG}", str(paths["big"])) for a in template]


def gate(directory, n, reference_accs, questions=None, binary=None, workdir=None):
    """The answer gate for `table`. For every question the flag form and the --query form each write their joined rows to a file; both are fingerprinted against the reference; the flag form is also
    run with the probe on standard input and must give the same bytes as with the probe as a file. Returns the problems; nothing here prints or records a time."""
    import tempfile
    questions = questions or load_questions()
    binary = binary or table_bin()
    p = paths_of(directory, n)
    problems = []
    with tempfile.TemporaryDirectory(prefix="named-join-gate-") as tmp:
        for q in questions["questions"]:
            qid = q["id"]
            if qid not in reference_accs:
                problems.append("%s: no reference answer" % qid)
                continue
            outs = {}
            for form in ("flags", "query"):
                rc, out, err = run_table(argv_for(q[form], p), binary)
                dest = pathlib.Path(tmp) / ("%s-%s.csv" % (qid, form))
                dest.write_bytes(out)
                if rc != 0:
                    problems.append("%s %s form: table refused or failed: status %d, stderr %r" % (qid, form, rc, err[:200]))
                    continue
                acc, why = check_output(dest, qid, expect_header=joined_columns(qid) if form == "flags" else None)
                if why:
                    problems.append("%s %s form: %s" % (qid, form, why))
                    continue
                d = compare(qid, reference_accs[qid], acc)
                if d:
                    problems.append("%s %s form: %s" % (qid, form, d))
                outs[form] = out
            if "flags" in outs:
                no_operand = [a for a in argv_for(q["flags"], p) if a != str(p["x"])]
                rc, piped, err = run_table(no_operand, binary, stdin=p["x"].read_bytes())
                if rc != 0 or piped != outs["flags"]:
                    problems.append("%s: the probe on standard input gives other bytes than the probe as a file (status %d)" % (qid, rc))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--rows", required=True)
    ap.add_argument("--layout", action="store_true")
    ap.add_argument("--answers", action="store_true")
    ap.add_argument("--cross-check", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--table", default=None)
    a = ap.parse_args(argv)
    n = int(float(a.rows))
    bad = []
    if a.layout:
        p = validate_layout(a.dir, n)
        print("layout: %s" % ("matches the documented spec" if not p else "DIFFERS"))
        bad += p
    accs = None
    if a.answers or a.cross_check or a.gate:
        accs = reference(a.dir, n)
        if a.answers:
            for qid, acc in accs.items():
                print("%s  chk %s  matched %d  digest %016x" % (qid, acc.chk(), acc.matched, acc.digest))
    if a.cross_check:
        other = reference_sqlite(a.dir, n)
        for qid in accs:
            d = compare(qid, accs[qid], other[qid])
            if d:
                bad.append("%s: the streaming reference and SQLite differ: %s" % (qid, d))
        print("cross-check: %s" % ("SQLite gives the same answers" if not any("SQLite" in b for b in bad) else "DIFFERS"))
    if a.gate:
        p = gate(a.dir, n, accs, binary=a.table)
        print("gate: %s" % ("every answer equals the reference, no time printed" if not p else "FAILED"))
        bad += p
    for b in bad:
        print("  " + b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
