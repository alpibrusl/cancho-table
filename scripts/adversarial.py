#!/usr/bin/env python3
"""The adversarial round (docs/adversarial.md): shapes of file and question that are not kind to `table`,
against csvtk, Miller (where installed), DuckDB (where installed) and the shell.

    python3 scripts/adversarial.py [--cells B1,B3] [--runs 5] [--threads 8] [--gen-only]

Files are generated under build/adv/ (seeded, never committed). Before any timing every contender's output is
parsed as CSV, normalised (integral numbers compared as integers; rows sorted where the question defines no
order) and compared with the answer computed here in Python; a contender that disagrees is reported and not
timed. Minimum of the runs, interleaved, output to /dev/null; peak resident set of one more run.
"""

import argparse
import csv
import io
import os
import pathlib
import platform
import random
import shutil
import statistics
import subprocess
import sys
from decimal import Decimal, InvalidOperation

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADV = ROOT / "build" / "adv"
BIG = "--max-groups 1000000 --max-state-bytes 1073741824 --max-distinct 10000000"


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        f.write(header + "\n")
        buf = []
        for r in rows:
            buf.append(r)
            if len(buf) >= 50000:
                f.write("".join(buf))
                buf = []
        f.write("".join(buf))


def generate(only=None):
    ADV.mkdir(parents=True, exist_ok=True)
    f1 = ROOT / "build" / "bench" / "data.csv"
    if not f1.exists():
        f1.parent.mkdir(parents=True, exist_ok=True)
        bench.generate(f1, 1_000_000)
    rng = random.Random(7)
    if not (ADV / "f2.csv").exists():
        perm = list(range(1_000_000))
        rng.shuffle(perm)
        keys = list(range(1_000_000))
        rng.shuffle(keys)
        write(ADV / "f2.csv", "id,s,k100k,k1m,u,v", ("%d,%s,k%d,key%07d,%d,%d\n" % (i, "wxyz"[rng.randrange(4)], rng.randrange(100000), keys[i], perm[i], rng.randrange(100000)) for i in range(1_000_000)))
    if not (ADV / "wide.csv").exists():
        header = ",".join("c%d" % i for i in range(200))
        write(ADV / "wide.csv", header, (",".join(str(rng.randrange(10000)) for _ in range(200)) + "\n" for _ in range(200_000)))
    if not (ADV / "quoted.csv").exists():
        stat = [200, 200, 200, 301, 404, 500]
        write(ADV / "quoted.csv", '"id","status","bytes","path","note"', ('"%d","%d","%d","/p/%d","a ""x"", b %d"\n' % (i, stat[rng.randrange(6)], rng.randrange(100000), rng.randrange(1000), i % 7) for i in range(1_000_000)))
    if not (ADV / "long.csv").exists():
        letters = "abcdefghijklmnopqrstuvwxyz ,\n\""
        def blob():
            n = rng.randrange(1000, 10000)
            return "".join(rng.choice(letters) for _ in range(n // 10)) * 10
        def esc(s):
            return '"' + s.replace('"', '""') + '"'
        write(ADV / "long.csv", "id,g,text", ("%d,g%d,%s\n" % (i, i % 10, esc(blob())) for i in range(20_000)))
    if not (ADV / "big.csv").exists():
        body = f1.read_bytes()
        nl = body.index(b"\n") + 1
        header, rest = body[:nl], body[nl:]
        with open(ADV / "big.csv", "wb") as out:
            out.write(header)
            for _ in range(34):
                out.write(rest)


# ---------------------------------------------------------------------------------------- canonical answers

def norm(cell):
    try:
        d = Decimal(cell)
    except InvalidOperation:
        return cell
    if d == d.to_integral_value() and "e" not in cell.lower() and cell.strip() == cell and cell != "":
        return str(int(d))
    return cell


def canon(text, ordered=False, drop_header=True):
    rows = [tuple(norm(c) for c in r) for r in csv.reader(io.StringIO(text, newline=""))]
    if drop_header and rows:
        rows = rows[1:]
    return rows if ordered else sorted(rows)


_cache = {}


def python_rows(path):
    if path not in _cache:
        with open(path, newline="") as f:
            r = csv.reader(f)
            header = next(r)
            _cache[path] = (header, list(r))
    return _cache[path]


def counts(path, col, pick=None):
    header, rows = python_rows(path)
    i = header.index(col)
    out = {}
    for r in rows:
        out[r[i]] = out.get(r[i], 0) + 1
    return out


# ------------------------------------------------------------------------------------------------ cells

def duck(path, sql, threads=None):
    pre = "" if threads is None else "SET threads=%d; " % threads
    return ["duckdb", "-c", "%sCOPY (%s) TO '/dev/null' (FORMAT csv)" % (pre, sql.replace("@", "read_csv('%s')" % path))], \
           ["duckdb", "-c", "%sCOPY (%s) TO '/dev/stdout' (FORMAT csv)" % (pre, sql.replace("@", "read_csv('%s')" % path))]


def sh(cmd):
    return ["sh", "-c", cmd]


def cells(table, nthreads):
    """cell id -> (title, file, expected(canon), contenders: name -> (timing argv, check argv or None)), ordered?"""
    out = {}
    d = str(ADV)
    big = BIG.split()

    def tbl(path, *args, t=1):
        return [table, "--root", str(pathlib.Path(path).parent), *args, "--threads", str(t), pathlib.Path(path).name]

    def common(path, name, a, extra):
        c = {}
        c["table -t1"] = (tbl(path, *a, t=1), None)
        c["table -t%d" % nthreads] = (tbl(path, *a, t=nthreads), None)
        c.update(extra)
        return c

    f2 = d + "/f2.csv"
    # A1: sort by text. table: the proxy (a group by the unique key, in key order)
    def a1():
        header, rows = python_rows(f2)
        k = header.index("k1m")
        keys = sorted(r[k] for r in rows)
        return keys
    A1 = {"table -t1 (proxy: --group k1m)": (tbl(f2, "--group", "k1m", "--format", "csv", *big), None),
          "csvtk sort": (["csvtk", "-j", "1", "sort", "-k", "k1m", f2], None),
          "sh sort": (sh("tail -n +2 %s | LC_ALL=C sort -t, -k4,4" % f2), None)}
    if shutil.which("mlr"):
        A1["mlr sort"] = (["mlr", "--icsv", "--ocsv", "sort", "-f", "k1m", f2], None)
    if shutil.which("duckdb"):
        A1["duckdb -t1"] = duck(f2, "SELECT * FROM @ ORDER BY k1m", 1)
        A1["duckdb default"] = duck(f2, "SELECT * FROM @ ORDER BY k1m")
    out["A1"] = ("sort 1M rows by a text column (table: proxy only, see docs)", f2, a1, A1, "keys")
    A2 = {"csvtk sort": (["csvtk", "-j", "1", "sort", "-k", "u:n", f2], None)}
    if shutil.which("mlr"):
        A2["mlr sort"] = (["mlr", "--icsv", "--ocsv", "sort", "-nf", "u", f2], None)
    if shutil.which("duckdb"):
        A2["duckdb -t1"] = duck(f2, "SELECT * FROM @ ORDER BY u", 1)
        A2["duckdb default"] = duck(f2, "SELECT * FROM @ ORDER BY u")
    out["A2"] = ("sort 1M rows by an integer column (table has no row sort and no proxy)", f2, None, A2, "sortint")

    def group_cell(cid, title, path, col, agg, valcol=None, kind="count", extra_args=()):
        ex = {}
        a = ["--group", col, "--format", "csv", *big]
        if agg:
            a = ["--group", col, "--agg", agg, "--format", "csv", *big]
        if kind == "count":
            ex["csvtk -j1"] = (["csvtk", "-j", "1", "freq", "-f", col, path], None)
            idx = python_rows(path)[0].index(col) + 1
            ex["sh cut|sort|uniq -c"] = (sh("tail -n +2 %s | cut -d, -f%d | LC_ALL=C sort | uniq -c" % (path, idx)), None)
            if shutil.which("mlr"):
                ex["mlr count-distinct"] = (["mlr", "--icsv", "--ocsv", "count-distinct", "-f", col, path], None)
            if shutil.which("duckdb"):
                ex["duckdb -t1"] = duck(path, "SELECT %s, count(*) FROM @ GROUP BY %s" % (col, col), 1)
                ex["duckdb default"] = duck(path, "SELECT %s, count(*) FROM @ GROUP BY %s" % (col, col))
        else:
            ex["csvtk -j1"] = (["csvtk", "-j", "1", "summary", "-g", col, "-f", "%s:sum" % valcol, path], None)
            if shutil.which("mlr"):
                ex["mlr stats1"] = (["mlr", "--icsv", "--ocsv", "stats1", "-a", "sum", "-f", valcol, "-g", col, path], None)
            if shutil.which("duckdb"):
                ex["duckdb -t1"] = duck(path, "SELECT %s, sum(%s) FROM @ GROUP BY %s" % (col, valcol, col), 1)
                ex["duckdb default"] = duck(path, "SELECT %s, sum(%s) FROM @ GROUP BY %s" % (col, valcol, col))
        def expected():
            header, rows = python_rows(path)
            i = header.index(col)
            if kind == "count":
                c = {}
                for r in rows:
                    c[r[i]] = c.get(r[i], 0) + 1
            else:
                j = header.index(valcol)
                c = {}
                for r in rows:
                    c[r[i]] = c.get(r[i], 0) + int(r[j])
            return sorted((k, str(v)) for k, v in c.items())
        out[cid] = (title, path, expected, common(path, cid, a, ex), "pairs")

    group_cell("B1", "group-count, 100,000 distinct keys", f2, "k100k", None)
    group_cell("B2", "group-sum of v, 100,000 keys", f2, "k100k", "sum:v", "v", "sum")
    group_cell("B3", "group-count, 1,000,000 distinct keys", f2, "k1m", None)
    group_cell("B4", "group-sum of v, 1,000,000 keys", f2, "k1m", "sum:v", "v", "sum")

    # I1
    def i1():
        header, rows = python_rows(f2)
        s, i = header.index("s"), header.index("id")
        sets = {}
        for r in rows:
            sets.setdefault(r[s], set()).add(r[i])
        return sorted((k, str(len(v))) for k, v in sets.items())
    ex = {"csvtk -j1": (sh("csvtk -j 1 uniq -f s,id %s | csvtk -j 1 freq -f s" % f2), None),
          "sh cut|sort -u|uniq -c": (sh("tail -n +2 %s | cut -d, -f1,2 | LC_ALL=C sort -u | cut -d, -f2 | LC_ALL=C sort | uniq -c" % f2), None)}
    if shutil.which("mlr"):
        ex["mlr"] = (["mlr", "--icsv", "--ocsv", "count-distinct", "-f", "s,id", "then", "count", "-g", "s", f2], None)
    if shutil.which("duckdb"):
        ex["duckdb -t1"] = duck(f2, "SELECT s, count(DISTINCT id) FROM @ GROUP BY s", 1)
        ex["duckdb default"] = duck(f2, "SELECT s, count(DISTINCT id) FROM @ GROUP BY s")
    out["I1"] = ("distinct:id (1M distinct) per s", f2, i1, common(f2, "I1", ["--group", "s", "--agg", "distinct:id", "--format", "csv", *big], ex), "pairs")

    # C1
    wide = d + "/wide.csv"
    ex = {"csvtk -j1": (["csvtk", "-j", "1", "cut", "-f", "c5,c100,c199", wide], None)}
    if shutil.which("mlr"):
        ex["mlr cut"] = (["mlr", "--icsv", "--ocsv", "cut", "-o", "-f", "c5,c100,c199", wide], None)
    if shutil.which("duckdb"):
        ex["duckdb -t1"] = duck(wide, "SELECT c5, c100, c199 FROM @", 1)
        ex["duckdb default"] = duck(wide, "SELECT c5, c100, c199 FROM @")
    def c1():
        header, rows = python_rows(wide)
        return sorted((r[5], r[100], r[199]) for r in rows)
    out["C1"] = ("select 3 of 200 columns (200,000 rows)", wide, c1, common(wide, "C1", ["--select", "c5,c100,c199", "--format", "csv"], ex), "rows")

    # D
    q = d + "/quoted.csv"
    def dcut():
        header, rows = python_rows(q)
        return sorted((r[1], r[4]) for r in rows)
    ex = {"csvtk -j1": (["csvtk", "-j", "1", "cut", "-f", "status,note", q], None)}
    if shutil.which("mlr"):
        ex["mlr cut"] = (["mlr", "--icsv", "--ocsv", "cut", "-o", "-f", "status,note", q], None)
    if shutil.which("duckdb"):
        ex["duckdb -t1"] = duck(q, "SELECT status, note FROM @", 1)
        ex["duckdb default"] = duck(q, "SELECT status, note FROM @")
    out["D1"] = ("cut 2 columns, all fields quoted", q, dcut, common(q, "D1", ["--select", "status,note", "--format", "csv"], ex), "rows")
    def dfilt():
        header, rows = python_rows(q)
        return sorted(tuple(r) for r in rows if r[1] == "404" and int(r[2]) > 50000)
    ex = {"csvtk -j1 filter|grep": (sh("csvtk -j 1 filter -f 'bytes>50000' %s | csvtk -j 1 grep -f status -p 404" % q), None)}
    if shutil.which("mlr"):
        ex["mlr filter"] = (["mlr", "--icsv", "--ocsv", "filter", "$status==404 && $bytes>50000", q], None)
    if shutil.which("duckdb"):
        ex["duckdb -t1"] = duck(q, "SELECT * FROM @ WHERE status = 404 AND bytes > 50000", 1)
        ex["duckdb default"] = duck(q, "SELECT * FROM @ WHERE status = 404 AND bytes > 50000")
    out["D2"] = ("filter status=404 and bytes>50000, all quoted", q, dfilt, common(q, "D2", ["--where", "status=404 and bytes:int>50000", "--format", "csv"], ex), "rows")
    group_cell("D3", "group-count by status, all quoted", q, "status", None)

    # E
    lg = d + "/long.csv"
    def e1():
        header, rows = python_rows(lg)
        return sorted((r[0], r[1]) for r in rows)
    ex = {"csvtk -j1": (["csvtk", "-j", "1", "cut", "-f", "id,g", lg], None)}
    if shutil.which("mlr"):
        ex["mlr cut"] = (["mlr", "--icsv", "--ocsv", "cut", "-o", "-f", "id,g", lg], None)
    if shutil.which("duckdb"):
        ex["duckdb -t1"] = duck(lg, "SELECT id, g FROM @", 1)
        ex["duckdb default"] = duck(lg, "SELECT id, g FROM @")
    out["E1"] = ("select 2 columns, 1-10 KB fields", lg, e1, common(lg, "E1", ["--select", "id,g", "--format", "csv"], ex), "rows")
    group_cell("E2", "group-count by g, 1-10 KB fields", lg, "g", None)

    # H1
    f1 = str(ROOT / "build" / "bench" / "data.csv")
    def h1():
        header, rows = python_rows(f1)
        return sorted(tuple(r) for r in rows if int(r[2]) > 10000)
    ex = {"csvtk -j1": (["csvtk", "-j", "1", "filter", "-f", "bytes>10000", f1], None)}
    if shutil.which("mlr"):
        ex["mlr filter"] = (["mlr", "--icsv", "--ocsv", "filter", "$bytes>10000", f1], None)
    if shutil.which("duckdb"):
        ex["duckdb -t1"] = duck(f1, "SELECT * FROM @ WHERE bytes > 10000", 1)
        ex["duckdb default"] = duck(f1, "SELECT * FROM @ WHERE bytes > 10000")
    out["H1"] = ("filter keeping ~90% of 1M rows (output-bound)", f1, h1, common(f1, "H1", ["--where", "bytes:int>10000", "--format", "csv"], ex), "rows")

    # G (1 GB): the expected answers are the 1M file's, times 34
    big_path = d + "/big.csv"
    def g_count():
        c = counts(f1, "status")
        return sorted((k, str(v * 34)) for k, v in c.items())
    ex = {"csvtk -j1 freq": (["csvtk", "-j", "1", "freq", "-f", "status", big_path], None)}
    if shutil.which("duckdb"):
        ex["duckdb default"] = duck(big_path, "SELECT status, count(*) FROM @ GROUP BY status")
    c = {"table -t1": (tbl(big_path, "--group", "status", "--format", "csv", "--max-rows", "1000000000", t=1), None)}
    for n in (4, nthreads):
        c["table -t%d" % n] = (tbl(big_path, "--group", "status", "--format", "csv", "--max-rows", "1000000000", t=n), None)
    c.update(ex)
    out["G2"] = ("1 GB file: group-count by status", big_path, g_count, c, "pairs")
    def g_filter_count():
        header, rows = python_rows(f1)
        return len([1 for r in rows if r[1] == "404" and int(r[2]) > 50000]) * 34
    ex = {"csvtk -j1 filter|grep": (sh("csvtk -j 1 filter -f 'bytes>50000' %s | csvtk -j 1 grep -f status -p 404" % big_path), None)}
    if shutil.which("duckdb"):
        ex["duckdb default"] = duck(big_path, "SELECT * FROM @ WHERE status = 404 AND bytes > 50000")
    c = {"table -t1": (tbl(big_path, "--where", "status=404 and bytes:int>50000", "--format", "csv", "--max-rows", "1000000000", t=1), None)}
    for n in (4, nthreads):
        c["table -t%d" % n] = (tbl(big_path, "--where", "status=404 and bytes:int>50000", "--format", "csv", "--max-rows", "1000000000", t=n), None)
    c.update(ex)
    out["G1"] = ("1 GB file: filter status=404 and bytes>50000", big_path, g_filter_count, c, "linecount")
    return out


def pairs_of(text):
    """(key, number) pairs from csv with a header, or from `uniq -c` lines, sorted."""
    import re
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+) (\S+)$", line)
        if m and "," not in line:
            out.append((m.group(2), m.group(1)))
            continue
        row = next(csv.reader([line]))
        if len(row) == 2 and re.fullmatch(r"-?\d+(\.0+)?", row[1]):
            out.append((row[0], norm(row[1])))
    return sorted(out)


def check(kind, expected, text):
    if kind == "keys":      # the keys of k1m in order: from full rows (column 4) or from (key, count) pairs
        rows = [r for r in csv.reader(io.StringIO(text, newline="")) if r]
        col = 3 if rows and len(rows[0]) > 2 else 0
        keys = [r[col] for r in rows if r[col] != "k1m"]
        return keys == expected()
    if kind == "sortint":
        rows = [r for r in csv.reader(io.StringIO(text, newline="")) if r and r[4] != "u"]
        u = [int(r[4]) for r in rows]
        return u == sorted(u) and len(u) == 1_000_000
    if kind == "linecount":
        return len(text.splitlines()) - 1 == expected()
    if kind == "pairs":
        return pairs_of(text) == expected()
    return canon(text) == expected()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--base", default="", help="another table binary (the one before a change): every table contender is run with it too, as `base ...`")
    ap.add_argument("--gen-only", action="store_true")
    args = ap.parse_args()
    generate()
    if args.gen_only:
        return
    table = os.path.abspath(args.bin)
    chosen = [c for c in args.cells.split(",") if c]
    plan = cells(table, args.threads)
    if args.base:
        base = os.path.abspath(args.base)
        for _, (_, _, _, contenders, _) in plan.items():
            for name, (argv, chk) in list(contenders.items()):
                if argv and argv[0] == table:
                    contenders["base" + name[5:]] = ([base] + argv[1:], chk)
    print("%s %s, %s cores; table %s; csvtk %s; mlr %s; duckdb %s" % (
        platform.system(), platform.machine(), os.cpu_count(), table, bench.version(["csvtk", "version"]) if shutil.which("csvtk") else "-",
        bench.version(["mlr", "--version"]) if shutil.which("mlr") else "not installed", bench.version(["duckdb", "--version"]) if shutil.which("duckdb") else "not installed"))
    for cid, (title, path, expected, contenders, kind) in plan.items():
        if chosen and cid not in chosen:
            continue
        runs = 3 if cid.startswith("G") else args.runs
        good = {}
        for name, (argv, check_argv) in contenders.items():
            p = subprocess.run(check_argv or argv, capture_output=True, text=True, errors="replace")
            ok = p.returncode == 0
            if ok and expected is not None:
                try:
                    ok = check(kind, expected, p.stdout)
                except Exception as e:  # noqa
                    ok = False
                    print("   (%s check raised %r)" % (name, e))
            elif ok and kind == "sortint":
                ok = check(kind, None, p.stdout)
            if ok:
                good[name] = argv
            else:
                print("  %s: output differs or failed (rc %d), not timed: %r" % (name, p.returncode, p.stderr[:120]))
        names = list(good)
        times = {n: [] for n in names}
        for r in range(runs):
            for n in names[r % len(names):] + names[:r % len(names)]:
                s, rc = bench.once(good[n])
                times[n].append(s)
        peaks = {n: bench.rss(good[n]) for n in names}
        size = os.path.getsize(path) / 1e6
        print("\n%s  %s   (%.0f MB, min of %d)" % (cid, title, size, runs))
        print("%-34s %9s %12s" % ("contender", "min s", "peak RSS MB"))
        for n in names:
            print("%-34s %9.4f %12s" % (n, min(times[n]), "%.1f" % (peaks[n] / 1024) if peaks[n] else "n/a"))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
