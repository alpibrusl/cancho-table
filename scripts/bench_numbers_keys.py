#!/usr/bin/env python3
"""Gate G10 of docs/numbers.md for the typed group keys and sort keys (stage N5): group by a float or decimal column, and order by a decimal or float column.

    python3 scripts/bench_numbers_keys.py [--bin build/table] [--runs 5] [--file build/num/keys.csv] [--threads 16] [--rows 1000000]

A file of id,status,level,price,ratio: `level` a number with two decimals in 0 to 200 (about 20,000 distinct values) written in two spellings (`12.5` and `12.50`: the
same number), `price` two decimals up to 99,999.99 (nearly unique), `ratio` a double of 15 to 17 digits (unique). Questions:

  group    count of the rows by `level`                  table `--group level:float` (and `:dec(2)`) | DuckDB DOUBLE | csvtk and Miller (by TEXT: they cannot do otherwise)
  top      the first 1,000 ids ordered by price descending   table `--order-by -price:dec(2) --top 1000` | DuckDB | csvtk | Miller
  sort     all the ids ordered by ratio ascending             table `--order-by ratio:float` | DuckDB | csvtk | Miller

**Every answer is checked against Python first**: the groups by value (key as a float, count), the order of the key values (ties are not ordered by every contender; `table`
keeps file order for ties, and its ids are compared exactly with Python's stable sort). csvtk and Miller group by the text of the cell, so `12.5` and `12.50` are two groups
for them: the script says how many groups each got against the true number.
"""
import argparse
import csv
import io
import pathlib
import random
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def generate(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    r = random.Random(5)
    with open(path, "w", newline="") as f:
        f.write("id,status,level,price,ratio\n")
        for i in range(rows):
            level = r.randint(0, 20000)
            text = "%d.%02d" % divmod(level, 100)
            if r.random() < 0.3 and text.endswith("0"):
                text = text[:-1]
            f.write(f"{i},{r.choice([200, 200, 301, 404, 500])},{text},{r.randint(0, 9999999) / 100:.2f},{r.uniform(0, 1000)!r}\n")


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--file", default=str(ROOT / "build" / "num" / "keys.csv"))
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--rows", type=int, default=1_000_000)
    a = ap.parse_args()
    data = pathlib.Path(a.file)
    if not data.exists():
        generate(data, a.rows)
    rows = list(csv.reader(open(data, newline="")))[1:]
    true_groups = {}
    for r in rows:
        true_groups[float(r[2])] = true_groups.get(float(r[2]), 0) + 1
    by_price = sorted(range(len(rows)), key=lambda i: (-float(rows[i][3]), i))
    by_ratio = sorted(range(len(rows)), key=lambda i: (float(rows[i][4]), i))
    d, root, name = str(data), ["--root", str(data.parent)], data.name
    counts = (1, 4, a.threads)

    def th(n):
        return [] if n == 1 else ["--threads", str(n), "--parallel-min-bytes", "0"]

    rd = f"read_csv('{d}', header=true, columns={{'id':'bigint','status':'int','level':'double','price':'double','ratio':'double'}})"

    def duck(sql, n):
        return ["duckdb", "-csv", "-noheader", "-c", f"set threads={n}; copy ({sql}) to '/dev/stdout' (format csv, header false)"]

    cells = []
    for n in counts:
        for typ in ("float", "dec(2)"):
            cells.append((f"table group level:{typ} t={n}", ("table", "group", typ, n), [a.bin, *root, "--group", f"level:{typ}", "--agg", "count", "--format", "csv", "--max-groups", "100000", *th(n), name], "group"))
        cells.append((f"table top price:dec(2) t={n}", ("table", "top", "dec", n), [a.bin, *root, "--order-by", "-price:dec(2)", "--select", "id", "--top", "1000", "--format", "csv", *th(n), name], "top"))
        cells.append((f"table top price:float t={n}", ("table", "top", "float", n), [a.bin, *root, "--order-by", "-price:float", "--select", "id", "--top", "1000", "--format", "csv", *th(n), name], "top"))
        cells.append((f"table sort ratio:float t={n}", ("table", "sort", "float", n), [a.bin, *root, "--order-by", "ratio:float", "--select", "id", "--format", "csv", "--max-sort-rows", "2000000", "--max-state-bytes", "1000000000", *th(n), name], "sort"))
        if shutil.which("duckdb"):
            cells.append((f"duckdb group level t={n}", ("duck", "group", "", n), duck(f"select level, count(*) from {rd} group by level", n), "group"))
            cells.append((f"duckdb top price t={n}", ("duck", "top", "", n), duck(f"select id from {rd} order by price desc limit 1000", n), "top"))
            cells.append((f"duckdb sort ratio t={n}", ("duck", "sort", "", n), duck(f"select id from {rd} order by ratio", n), "sort"))
    if shutil.which("csvtk"):
        cells.append(("csvtk -j 1 group level (text)", ("csvtk", "group", "", 1), ["csvtk", "-j", "1", "summary", "-g", "level", "-f", "id:count", d], "group"))
        cells.append(("csvtk -j 1 sort price desc", ("csvtk", "top", "", 1), ["csvtk", "-j", "1", "sort", "-k", "price:nr", d], "top-all"))
        cells.append(("csvtk -j 1 sort ratio", ("csvtk", "sort", "", 1), ["csvtk", "-j", "1", "sort", "-k", "ratio:n", d], "sort-all"))
    if shutil.which("mlr"):
        cells.append(("mlr group level (text)", ("mlr", "group", "", 1), ["mlr", "--icsv", "--ocsv", "count-distinct", "-f", "level", d], "group"))
        cells.append(("mlr sort price desc", ("mlr", "top", "", 1), ["mlr", "--icsv", "--ocsv", "sort", "-nr", "price", d], "top-all"))
        cells.append(("mlr sort ratio", ("mlr", "sort", "", 1), ["mlr", "--icsv", "--ocsv", "sort", "-nf", "ratio", d], "sort-all"))

    def check(label, key, out, kind):
        r = [x for x in csv.reader(io.StringIO(out)) if x]
        if kind == "group":
            if key[0] in ("csvtk", "mlr"):
                hdr = r[0] if r and not r[0][0].replace(".", "").isdigit() else None
                got = len(r) - (1 if hdr else 0)
                return got == len(true_groups), "%d groups (true %d)" % (got, len(true_groups))
            m = {}
            for x in r:
                if x[0].replace(".", "").replace("-", "").isdigit() or x[0] == "level":
                    try:
                        m[float(x[0])] = int(x[1])
                    except ValueError:
                        pass
            return m == true_groups, ""
        if key[0] == "table":
            ids = [int(x[0]) for x in r[1:]]
            want = by_price[:1000] if kind == "top" else by_ratio
            return ids == want, ""
        if kind in ("top-all", "sort-all"):
            r = r[1:]
            col = 3 if kind == "top-all" else 4
            keys = [float(x[col]) for x in r]
            want = [float(rows[i][col]) for i in (by_price if kind == "top-all" else by_ratio)]
            return keys == want, ""
        ids = [int(x[0]) for x in r]
        col = 3 if kind == "top" else 4
        want = [float(rows[i][col]) for i in (by_price[:1000] if kind == "top" else by_ratio)]
        return [float(rows[i][col]) for i in ids] == want, ""

    good, notes = [], {}
    for label, key, argv, kind in cells:
        t, p = timed(argv)
        if p.returncode != 0:
            print("FAILED", label, p.stderr[:200])
            continue
        ok, note = check(label, key, p.stdout.decode(), kind)
        if not ok and key[0] == "table":
            print("WRONG ANSWER", label)
            continue
        notes[label] = ("exact" if ok else "DIFFERENT") + (" " + note if note else "")
        good.append((label, key, argv))
    times = {l: [] for l, _, _ in good}
    for i in range(a.runs):
        for label, key, argv in (good if i % 2 == 0 else list(reversed(good))):
            times[label].append(timed(argv)[0])
    best = {l: min(v) for l, v in times.items()}
    print("%-34s %9s   %s" % ("cell (minimum of %d)" % a.runs, "seconds", "answer"))
    for label, _, _ in good:
        print("%-34s %9.4f   %s" % (label, best[label], notes[label]))
    for kind in ("group level", "top price", "sort ratio"):
        for n in counts:
            for typ in (" level:float", " level:dec(2)", " price:dec(2)", " price:float", " ratio:float"):
                t = best.get(f"table {kind.split()[0]}{typ} t={n}")
                du = best.get(f"duckdb {kind} t={n}")
                if t and du:
                    print("G10 %-10s %-13s t=%-3d duckdb / table = %.2f" % (kind.split()[0], typ.strip(), n, du / t))
    for who, lab in (("csvtk -j 1", "csvtk -j 1 "), ("mlr", "mlr ")):
        for kind, ours in (("group level (text)", "table group level:float t=1"), ("sort price desc", "table top price:dec(2) t=1"), ("sort ratio", "table sort ratio:float t=1")):
            o, t = best.get(f"{lab}{kind}"), best.get(ours)
            if o and t:
                print("G10 %-18s %s / table(1 thread) = %.2f" % (kind, who, o / t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
