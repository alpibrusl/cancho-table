#!/usr/bin/env python3
"""Gate G10 of docs/numbers.md for the type report (stage N6): `--report types` on the standard 1,000,000-row file against what the others offer for knowing what a column holds.

    python3 scripts/bench_numbers_report.py [--bin build/table] [--runs 5] [--file build/num/data.csv] [--threads 16] [--rows 1000000]

The file of scripts/bench_numbers.py (id,status,bytes,price,ratio,path,cents), and a second file made from it with three late cells made wrong (`n/a` in `price` at 90% of
the rows, `1e3` in `bytes` at 70%, an empty `cents` at 50%): the cells a sample does not see. Contenders:

  table        `--report types` (all rows, exact classes, a suggestion)  | at 1, 4 and 16 threads
  DuckDB       `DESCRIBE SELECT * FROM read_csv(f)` (sniffs a sample of 20,480 rows)  and  `SUMMARIZE` (reads everything, after the sniff)
  Miller       `mlr summary -a field_type` (infers per cell: int, float, string, empty, and their mixtures)
  csvtk        has no per-column type (`csvtk stats` counts rows and columns): not measured

**Every answer is checked against Python first**: the report's counters of each column against an independent classification of every cell. What each contender says of each
column is printed beside the truth (the suggestion the design defines), so the difference is visible: a sampling sniffer says DOUBLE for a column that has one `n/a`
900,000 rows in, and the read then fails (or, with `ignore_errors`, silently turns the cell into NULL); `table` says `none` and names the row.
"""
import argparse
import csv
import pathlib
import random
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
import bench_numbers as bn  # noqa: E402
import test_report as tr  # noqa: E402


def dirty(src, dst):
    rows = list(csv.reader(open(src, newline="")))
    n = len(rows) - 1
    rows[int(n * 0.9)][3] = "n/a"
    rows[int(n * 0.7)][2] = "1e3"
    rows[int(n * 0.5)][6] = ""
    with open(dst, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


def truth(path):
    rows = list(csv.reader(open(path, newline="")))
    header, body = rows[0], rows[1:]
    out = {}
    for ci, name in enumerate(header):
        out[name] = tr.report_of(name, [(r[ci].encode(), ri + 1, ri + 2) for ri, r in enumerate(body)])
    return out


def timed(argv):
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return time.perf_counter() - t, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--file", default=str(ROOT / "build" / "num" / "data.csv"))
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--rows", type=int, default=1_000_000)
    a = ap.parse_args()
    clean = pathlib.Path(a.file)
    if not clean.exists():
        bn.generate(clean, a.rows)
    bad = clean.parent / "dirty.csv"
    if not bad.exists():
        dirty(clean, bad)
    counts = (1, 4, a.threads)
    results = {}
    for label, path in (("clean", clean), ("dirty", bad)):
        want = truth(path)
        root, name = ["--root", str(path.parent)], path.name
        cells = []
        for n in counts:
            t = [] if n == 1 else ["--threads", str(n), "--parallel-min-bytes", "0"]
            cells.append((f"table --report types t={n}", ("table", n), [a.bin, *root, "--report", "types", "--format", "csv", *t, name]))
        d = str(path)
        if shutil.which("duckdb"):
            cells.append(("duckdb DESCRIBE (sniff)", ("duck", "describe"), ["duckdb", "-csv", "-noheader", "-c", f"DESCRIBE SELECT * FROM read_csv('{d}')"]))
            cells.append(("duckdb SUMMARIZE", ("duck", "summarize"), ["duckdb", "-csv", "-noheader", "-c", f"SUMMARIZE SELECT * FROM read_csv('{d}')"]))
        if shutil.which("mlr"):
            cells.append(("mlr summary -a field_type", ("mlr",), ["mlr", "--icsv", "--ocsv", "summary", "-a", "field_type", d]))
        good = []
        verdicts = {}
        for lab, key, argv in cells:
            t, p = timed(argv)
            if p.returncode != 0:
                verdicts[lab] = "FAILED: " + p.stderr.decode()[:90].replace("\n", " ")
                continue
            out = p.stdout.decode()
            if key[0] == "table":
                rows = list(csv.reader(out.splitlines()))[1:]
                if rows != [want[r[0]] for r in rows] or len(rows) != len(want):
                    print("WRONG ANSWER", lab, label)
                    continue
                verdicts[lab] = " ".join("%s=%s" % (r[0], r[11]) for r in rows)
            elif key[0] == "duck":
                rows = [r for r in csv.reader(out.splitlines()) if r]
                verdicts[lab] = " ".join("%s=%s" % (r[0], r[1]) for r in rows[: len(want)])
            else:
                rows = list(csv.reader(out.splitlines()))
                verdicts[lab] = " ".join("%s=%s" % (h, v) for h, v in zip(rows[0][1:], rows[1][1:])) if len(rows) > 1 else out[:90]
            good.append((lab, key, argv))
        times = {l: [] for l, _, _ in good}
        for i in range(a.runs):
            for lab, key, argv in (good if i % 2 == 0 else list(reversed(good))):
                times[lab].append(timed(argv)[0])
        results[label] = ({l: min(v) for l, v in times.items()}, verdicts, {k: want[k][11] for k in want})
    for label in ("clean", "dirty"):
        best, verdicts, suggestions = results[label]
        print("== the %s file ==" % label)
        print("%-34s %9s" % ("cell (minimum of %d)" % a.runs, "seconds"))
        for lab, sec in best.items():
            print("%-34s %9.4f" % (lab, sec))
        print("what each says of the columns (the truth: %s)" % " ".join("%s=%s" % kv for kv in suggestions.items()))
        for lab, v in verdicts.items():
            print("  %-30s %s" % (lab, v))
        for lab in best:
            if lab.startswith("duckdb") or lab.startswith("mlr"):
                for n in counts:
                    t = best.get(f"table --report types t={n}")
                    if t:
                        print("G10 %-30s / table(t=%d) = %.2f" % (lab, n, best[lab] / t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
