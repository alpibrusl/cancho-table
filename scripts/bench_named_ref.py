#!/usr/bin/env python3
"""The reference answers of db-benchmark's groupby questions q1 to q5 (and q10), and the gates that hold `table` to them before anything is timed.
NB1 of docs/named-benchmarks.md. **This script times nothing.**

    python3 scripts/bench_named_ref.py FILE --layout [--rows N --k K]     # the file is exactly the documented layout (gate a)
    python3 scripts/bench_named_ref.py FILE --answers                      # print the reference answers and their chk
    python3 scripts/bench_named_ref.py FILE --cross-check                  # the same answers by SQLite (a second, independent path)
    python3 scripts/bench_named_ref.py FILE --gate [--table build/table]   # table's q1-q5, flag form and --query form, equal the reference (gate b);
                                                                         # q6-q9 refused as classified, q10 still a defect (gate c)

The reference reads the **written file** with the standard library only (no pandas, no numpy), in one streaming pass, and does exact integer arithmetic: the
`v` columns as integers, `v3` as an integer count of millionths (never a float), a mean as an exact rational rounded half to even at the scale asked, which is
what `table` does (docs/numbers.md). A contender is never the reference. The SQLite path (--cross-check, and the tests) groups and sums in SQL over the same
file and must give the same answers: two paths that share no aggregation code.

`chk` is db-benchmark's check string (`_helpers/helpers.py` `make_chk`: the sums of the output columns, a float rounded to 3 decimals, joined by `;`), computed
here from the exact values. The owner's `chk` of a mean is the sum of double averages; ours is the sum of the group means rounded at scale 6, so the two can
differ in the third decimal on a very large number of groups: it is recorded, never compared across engines.

The questions are in scripts/bench_named_questions.json, which the later driver reads too.
"""

import argparse
import csv
import io
import json
import math
import os
import pathlib
import re
import sqlite3
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "scripts" / "bench_named_questions.json"
TRAPS = {132, 134, 136, 139, -4, -6, -8, -11}
SCALE = 1_000_000


def load_questions(path=QUESTIONS):
    return json.loads(pathlib.Path(path).read_text())


def table_bin():
    return str(pathlib.Path(os.environ.get("TOOLBOX_BIN", ROOT / "build")) / "table")


# ---------------------------------------------------------------- numbers, exactly

def millionths(text):
    """'97.861311' -> 97861311; '5' -> 5000000. At most six decimals, no sign, no exponent: anything else is an error (the generator never writes it)."""
    whole, dot, frac = text.partition(".")
    if len(frac) > 6 or not whole.isdigit() or (dot and not frac.isdigit()):
        raise ValueError("not a v3 of the layout: %r" % text)
    return int(whole) * SCALE + int(frac.ljust(6, "0") or "0")


def fmt6(m):
    """Millionths as table writes a scale-6 decimal: 5500000 -> '5.500000'."""
    return "%d.%06d" % divmod(m, SCALE)


def div_half_even(num, den):
    q, r = divmod(num, den)
    twice = 2 * r
    if twice > den or (twice == den and q % 2):
        q += 1
    return q


def mean6(total, count, scale=1):
    """The exact mean of `total` (in units of 1/scale) over `count`, in millionths, rounded half to even."""
    return div_half_even(total * (SCALE // scale), count)


def chk_text(values):
    """db-benchmark's make_chk: an int as itself, a float rounded to 3 decimals, joined by ';'."""
    parts = []
    for v in values:
        parts.append(str(round(v, 3)) if isinstance(v, float) else str(v))
    return ";".join(parts)


# ---------------------------------------------------------------- the reference (streaming, exact)

def reference(path, with_q10=False):
    """{qid: {"columns": [...], "rows": sorted list of tuples of text, "chk": text}} from one pass over the file. q10 holds a group per row: opt in."""
    q1, q2, q3, q4, q5, q10 = {}, {}, {}, {}, {}, {}
    with open(path, "r", encoding="ascii", newline="") as f:
        header = f.readline().rstrip("\n").split(",")
        if header != ["id1", "id2", "id3", "id4", "id5", "id6", "v1", "v2", "v3"]:
            raise ValueError("header is not the layout's: %r" % header)
        for line in f:
            id1, id2, id3, id4, id5, id6, v1, v2, v3 = line.rstrip("\n").split(",")
            a = int(v1)
            b = int(v2)
            c = millionths(v3)
            q1[id1] = q1.get(id1, 0) + a
            k = (id1, id2)
            q2[k] = q2.get(k, 0) + a
            g = q3.get(id3)
            if g is None:
                q3[id3] = [a, c, 1]
            else:
                g[0] += a
                g[1] += c
                g[2] += 1
            g = q4.get(id4)
            if g is None:
                q4[id4] = [a, b, c, 1]
            else:
                g[0] += a
                g[1] += b
                g[2] += c
                g[3] += 1
            g = q5.get(id6)
            if g is None:
                q5[id6] = [a, b, c]
            else:
                g[0] += a
                g[1] += b
                g[2] += c
            if with_q10:
                k = (id1, id2, id3, id4, id5, id6)
                g = q10.get(k)
                if g is None:
                    q10[k] = [c, 1]
                else:
                    g[0] += c
                    g[1] += 1
    return _answers(q1, q2, q3, q4, q5, q10 if with_q10 else None)


def _answers(q1, q2, q3, q4, q5, q10):
    """The five (six) answers from the group tables: the same function for the streaming path and the SQLite path, which differ only in how the tables are made."""
    out = {}
    rows = [(k, str(v)) for k, v in q1.items()]
    out["q1"] = _q(["id1", "sum:v1"], [(k, v) for k, v in rows], [sum(q1.values())])
    rows = [(k[0], k[1], str(v)) for k, v in q2.items()]
    out["q2"] = _q(["id1", "id2", "sum:v1"], rows, [sum(q2.values())])
    rows, m_total = [], 0
    for k, (s, tm, n) in q3.items():
        m = mean6(tm, n, SCALE)
        m_total += m
        rows.append((k, str(s), fmt6(m)))
    out["q3"] = _q(["id3", "sum:v1", "mean:v3"], rows, [sum(g[0] for g in q3.values()), m_total / SCALE])
    rows, t1, t2, t3 = [], 0, 0, 0
    for k, (s1, s2, s3, n) in q4.items():
        m1, m2, m3 = mean6(s1, n, 1), mean6(s2, n, 1), mean6(s3, n, SCALE)
        t1, t2, t3 = t1 + m1, t2 + m2, t3 + m3
        rows.append((k, fmt6(m1), fmt6(m2), fmt6(m3)))
    out["q4"] = _q(["id4", "mean:v1", "mean:v2", "mean:v3"], rows, [t1 / SCALE, t2 / SCALE, t3 / SCALE])
    rows = [(k, str(a), str(b), fmt6(c)) for k, (a, b, c) in q5.items()]
    out["q5"] = _q(["id6", "sum:v1", "sum:v2", "sum:v3"], rows,
                   [sum(g[0] for g in q5.values()), sum(g[1] for g in q5.values()), sum(g[2] for g in q5.values()) / SCALE])
    if q10 is not None:
        rows = [(*k, fmt6(c), str(n)) for k, (c, n) in q10.items()]
        out["q10"] = _q(["id1", "id2", "id3", "id4", "id5", "id6", "sum:v3", "count"], rows,
                        [sum(g[0] for g in q10.values()) / SCALE, sum(g[1] for g in q10.values())])
    return out


def _q(columns, rows, chk):
    return {"columns": columns, "rows": sorted(tuple(r) for r in rows), "chk": chk_text(chk)}


def reference_sqlite(path, with_q10=False):
    """The same answers by SQLite: the file loaded as it is (text keys, integer v1 v2, v3 as a REAL turned into integer millionths in SQL), grouped and summed in SQL.
    Shares with `reference` only the final rounding of a mean (`_answers`) and not one line of parsing or aggregating."""
    con = sqlite3.connect(":memory:")
    con.execute("create table x (id1 text, id2 text, id3 text, id4 integer, id5 integer, id6 integer, v1 integer, v2 integer, v3 real)")
    with open(path, "r", encoding="ascii", newline="") as f:
        r = csv.reader(f)
        next(r)
        con.executemany("insert into x values (?,?,?,?,?,?,?,?,?)", r)
    m = "cast(round(v3 * 1000000) as integer)"
    q1 = dict(con.execute("select id1, sum(v1) from x group by id1"))
    q2 = {(a, b): s for a, b, s in con.execute("select id1, id2, sum(v1) from x group by id1, id2")}
    q3 = {k: [s, t, n] for k, s, t, n in con.execute("select id3, sum(v1), sum(%s), count(*) from x group by id3" % m)}
    q4 = {str(k): [a, b, c, n] for k, a, b, c, n in con.execute("select id4, sum(v1), sum(v2), sum(%s), count(*) from x group by id4" % m)}
    q5 = {str(k): [a, b, c] for k, a, b, c in con.execute("select id6, sum(v1), sum(v2), sum(%s) from x group by id6" % m)}
    q10 = None
    if with_q10:
        q10 = {tuple(str(v) for v in row[:6]): [row[6], row[7]]
               for row in con.execute("select id1, id2, id3, id4, id5, id6, sum(%s), count(*) from x group by id1, id2, id3, id4, id5, id6" % m)}
    con.close()
    return _answers(q1, q2, q3, q4, q5, q10)


# ---------------------------------------------------------------- gate (a): the file is the documented layout

def validate_layout(path, n, k, spec=None):
    """Problems (a list of text) between the file and the documented layout of scripts/bench_named_gen.py: header, row count, per column the format, the range and the exact
    number of distinct values (a column of m values has all m once there are 20 draws a value or more; a draw with replacement misses a value with probability e^-(N/m), which the check allows below that, as the owner's `sample` does)."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import bench_named_gen as gen
    spec = spec or gen.spec(n, k)
    problems = []
    seen = {c: set() for c in gen.COLUMNS if c != "v3"}
    pats = {
        "id1": re.compile(r"id\d{3}$"), "id2": re.compile(r"id\d{3}$"), "id3": re.compile(r"id\d{10}$"),
        "id4": re.compile(r"[1-9]\d*$"), "id5": re.compile(r"[1-9]\d*$"), "id6": re.compile(r"[1-9]\d*$"),
        "v1": re.compile(r"[1-9]\d*$"), "v2": re.compile(r"[1-9]\d*$"),
        "v3": re.compile(r"(0|[1-9]\d?)(\.\d{0,5}[1-9])?$"),
    }
    rows = 0
    v3min, v3max = None, None
    with open(path, "r", encoding="ascii", newline="") as f:
        head = f.readline()
        if head != gen.HEADER + "\n":
            problems.append("header %r, documented %r" % (head, gen.HEADER))
        for line in f:
            rows += 1
            cells = line.rstrip("\n").split(",")
            if len(cells) != 9 or not line.endswith("\n"):
                problems.append("row %d: %d cells, or no final newline" % (rows, len(cells)))
                break
            for name, cell in zip(gen.COLUMNS, cells):
                s = spec[name]
                if not pats[name].match(cell) or (name == "v3" and cell.endswith(".")):
                    problems.append("row %d, %s: %r does not have the documented form" % (rows, name, cell))
                    return problems
                if name == "v3":
                    m = millionths(cell)
                    v3min = m if v3min is None else min(v3min, m)
                    v3max = m if v3max is None else max(v3max, m)
                    if not s["lo"] <= m <= s["hi"]:
                        problems.append("row %d, v3: %r is outside [0, 100)" % (rows, cell))
                        return problems
                    continue
                v = int(cell[2:]) if s["kind"] == "text" else int(cell)
                if not s["lo"] <= v <= s["hi"]:
                    problems.append("row %d, %s: %r is outside %d..%d" % (rows, name, cell, s["lo"], s["hi"]))
                    return problems
                seen[name].add(cell)
    if rows != n:
        problems.append("%d rows, documented %d" % (rows, n))
    for name, vals in seen.items():
        m, got = spec[name]["count"], len(vals)
        draws_per_value = n / m
        if draws_per_value >= 20:  # a value is never drawn with probability e^-20 (2e-9): every one of the m values is in the file
            if got != m:
                problems.append("%s has %d distinct values, documented %d" % (name, got, m))
        else:  # few draws a value (K = 2 or 10 for id3 and id6): the count is what the draws leave, m (1 - e^-(N/m)), within 2 percent of m
            expect = m * (1 - math.exp(-draws_per_value))
            if got > m or abs(got - expect) > 0.02 * m + 10:
                problems.append("%s has %d distinct values, documented up to %d (expected about %d)" % (name, got, m, expect))
    return problems


# ---------------------------------------------------------------- gates (b) and (c): table against the reference

def argv_for(template, path):
    return [a.replace("{FILE}", str(path)) for a in template]


def run_table(argv, binary=None, timeout=600):
    p = subprocess.run([binary or table_bin(), *argv], capture_output=True, input=b"", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def parse_csv(stdout):
    rows = list(csv.reader(io.StringIO(stdout.decode("utf-8"), newline="")))
    return (rows[0], sorted(tuple(r) for r in rows[1:])) if rows else ([], [])


def first_rule(stdout):
    try:
        errs = json.loads(stdout.decode("utf-8").splitlines()[0]).get("errors", [])
        return errs[0]["rule"] if errs else None
    except (ValueError, IndexError, KeyError):
        return None


def compare(qid, ref, status, stdout, stderr, form):
    """Problems of one answer against one reference: the first difference, not a count."""
    tag = "%s %s form" % (qid, form)
    if status != 0:
        return ["%s: table refused or failed: status %d, stderr %r" % (tag, status, stderr[:200])]
    cols, rows = parse_csv(stdout)
    if cols != ref["columns"]:
        return ["%s: columns %r, reference %r" % (tag, cols, ref["columns"])]
    if len(rows) != len(ref["rows"]):
        return ["%s: %d groups, reference %d" % (tag, len(rows), len(ref["rows"]))]
    for got, want in zip(rows, ref["rows"]):
        if got != want:
            return ["%s: first difference: table %r, reference %r" % (tag, got, want)]
    return []


def gate(path, ref, questions=None, binary=None, with_q10_defect=True):
    """The answer gate. Nothing in it prints or records a time; the driver (NB2) must call it and print no time unless it returns []. Checks, for every question:
    E/B: the flag form and the --query form give the same bytes, and the rows and columns are the reference's; X: the probe is refused with the stated rule;
    D: the command still fails with a trap status (a fix fails the gate: the entry must be re-classified)."""
    questions = questions or load_questions()
    problems = []
    for q in questions["questions"]:
        qid, cls = q["id"], q["class"]
        if cls in ("E", "B"):
            if qid not in ref:
                problems.append("%s: no reference answer" % qid)
                continue
            a = run_table(argv_for(q["flags"], path), binary)
            b = run_table(argv_for(q["query"], path), binary)
            problems += compare(qid, ref[qid], *a, "flag")
            problems += compare(qid, ref[qid], *b, "query")
            if a[0] == 0 and b[0] == 0 and a[1] != b[1]:
                problems.append("%s: the flag form and the --query form give different bytes" % qid)
            if ref[qid]["chk"] is None:
                problems.append("%s: no chk" % qid)
        elif cls == "X":
            st, out, err = run_table(argv_for(q["probe"]["argv"], path), binary)
            rule = first_rule(out)
            if st == 0 or rule != q["probe"]["rule"]:
                problems.append("%s: classified not expressible (%s) but the probe gave status %d, rule %r: re-classify" % (qid, q["probe"]["rule"], st, rule))
        elif cls == "D":
            for argv in q["defect"]["argvs"] if with_q10_defect else []:
                st, out, err = run_table(argv_for(argv, path), binary)
                if st not in q["defect"]["statuses"] or out:
                    problems.append("%s: classified a defect (status %s, no output) but %r gave status %d: if fixed, re-classify" % (qid, q["defect"]["statuses"], argv[:4], st))
        else:
            problems.append("%s: unknown class %r" % (qid, cls))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--layout", action="store_true")
    ap.add_argument("--answers", action="store_true")
    ap.add_argument("--cross-check", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--rows", default=None)
    ap.add_argument("--k", default=None)
    ap.add_argument("--table", default=None, help="the binary (else $TOOLBOX_BIN/table, else build/table)")
    ap.add_argument("--questions", default=None, help="a questions file (else scripts/bench_named_questions.json): to try a mutated one")
    ap.add_argument("--q10", action="store_true", help="include q10 in the reference (a group per row: memory)")
    a = ap.parse_args(argv)
    path = pathlib.Path(a.file)
    bad = []
    ref = None
    if a.layout:
        meta = json.loads(path.with_name(path.name + ".meta.json").read_text()) if path.with_name(path.name + ".meta.json").exists() else {}
        n = int(float(a.rows)) if a.rows else meta.get("rows")
        k = int(float(a.k)) if a.k else meta.get("k", 100)
        if not n:
            sys.exit("--layout needs --rows (or the file's .meta.json)")
        p = validate_layout(path, n, k)
        print("layout: %s" % ("matches the documented spec" if not p else "DIFFERS"))
        bad += p
    if a.answers or a.cross_check or a.gate:
        ref = reference(path, a.q10)
        if a.answers:
            for qid, r in ref.items():
                print("%s  %d groups  chk %s" % (qid, len(r["rows"]), r["chk"]))
    if a.cross_check:
        other = reference_sqlite(path, a.q10)
        for qid in ref:
            if other[qid] != ref[qid]:
                bad.append("%s: the streaming reference and SQLite differ" % qid)
        print("cross-check: %s" % ("SQLite gives the same answers" if not any("SQLite" in b for b in bad) else "DIFFERS"))
    if a.gate:
        p = gate(path, ref, questions=load_questions(a.questions or QUESTIONS), binary=a.table)
        print("gate: %s" % ("every answer equals the reference, no time printed" if not p else "FAILED"))
        bad += p
    for b in bad:
        print("  " + b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
