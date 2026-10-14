#!/usr/bin/env python3
"""The four questions of scripts/bench.py on JSON lines (docs/readers.md section 12.9): `table --input-format jsonl` against DuckDB (`read_ndjson`, `read_json_auto`), Miller
(`mlr --ijsonl`, when installed) and `table` on the same rows as CSV, on a narrow file (1,000,000 lines of 72 bytes, `{"id":0,"status":200,"bytes":74606,"path":"/p/867","note":"a,b 0"}`)
and a wide one (24 keys, 330 bytes a line, the two columns read are the last two keys).

    python3 scripts/spikes/jsonl_gen.py DIR 1000000          # writes DIR/bench.csv, bench.jsonl, wide.jsonl, truth.json
    python3 scripts/bench_jsonl.py DIR [--reps N] [--only table,duckdb,mlr] [--wide] [--bin build/table] [--rows N]

Every contender's answer is compared with Python's (from the CSV by the csv module) before it is timed, and a disagreement stops the run. Each contender is run `--reps` times
(default 7) with its output thrown away, in rotation (one run of each, then the next round), and the **minimum** is reported with the load average before the first and after the
last round, and the peak resident set (`/usr/bin/time -l` on macOS, `-v` on Linux) of the slowest-memory run. Nothing here is a claim: it prints the numbers and the conditions
that were. `table` is given only the columns the question reads (`--fields status,bytes`), as a person would, and its threads are the line's (1 here, 4 and 16 apart).
"""
import argparse
import csv
import json
import os
import platform
import re
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAC = platform.system() == "Darwin"
TIME = ["/usr/bin/time", "-l"] if MAC else ["/usr/bin/time", "-v"]


def truth(path, rows):
    count, total, ids = {}, {}, []
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r)
        for n, row in enumerate(r):
            if n >= rows:
                break
            s, b = row[1], int(row[2])
            count[s] = count.get(s, 0) + 1
            total[s] = total.get(s, 0) + b
            if s == "404" and b > 50000:
                ids.append(int(row[0]))
    return {"filter": (len(ids), sum(ids)), "count": sorted(count.items()), "sum": sorted(total.items())}


def run(cmd):
    t = time.perf_counter()
    p = subprocess.run(TIME + ["sh", "-c", cmd], capture_output=True)
    el = time.perf_counter() - t
    err = p.stderr.decode(errors="replace")
    m = re.search(r"(\d+)\s+maximum resident set size", err) if MAC else re.search(r"Maximum resident set size \(kbytes\): (\d+)", err)
    rss = (int(m.group(1)) / 1048576 if MAC else int(m.group(1)) / 1024) if m else 0.0
    return el, rss, p.stdout, p.returncode


def answer_ok(name, out, truth_):
    # (a csv answer of `table` has a header line, which starts with a letter; duckdb and miller write none)
    lines = [l for l in out.decode().split("\n") if l and (l[0].isdigit() or l[0] == '"')]
    if name == "filter":
        if truth_.get("wide"):
            return len(lines) == truth_["filter"][0]      # the wide file has no id: the rows are counted
        return (len(lines), sum(int(x) for x in lines)) == truth_["filter"]
    if name == "cut":
        return len(lines) == truth_["rows"]
    rows = sorted((l.split(",")[0].strip('"'), int(l.split(",")[1].strip('"'))) for l in lines)
    return rows == truth_[name]


def table_cmds(binary, src, fmt, fields, extra="", pick="id"):
    j = "--input-format jsonl --fields %s " % fields if fmt == "jsonl" else ""
    return {
        "filter": "%s %s%s--where 'status=404 and bytes:int>50000' --select %s --format csv %s" % (binary, j, extra and extra + " ", pick, src),
        "count": "%s %s%s--group status --format csv %s" % (binary, j, extra and extra + " ", src),
        "sum": "%s %s%s--group status --agg sum:bytes --format csv %s" % (binary, j, extra and extra + " ", src),
        "cut": "%s %s%s--select status,bytes --format csv %s" % (binary, j, extra and extra + " ", src),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--only", default="table,duckdb,mlr")
    ap.add_argument("--wide", action="store_true", help="the wide file (24 keys) instead of the narrow one")
    ap.add_argument("--bin", default=os.path.join(ROOT, "build", "table"))
    ap.add_argument("--rows", type=int, default=1000000)
    ap.add_argument("--questions", default="filter,count,sum,cut")
    a = ap.parse_args()
    d = os.path.abspath(a.dir)
    only = a.only.split(",")
    jl = os.path.join(d, "wide.jsonl" if a.wide else "bench.jsonl")
    csvf = os.path.join(d, "bench.csv")
    t = truth(csvf, a.rows)
    t["rows"] = a.rows
    # the contenders: label -> {question: command}
    C = {}
    t["wide"] = a.wide
    pick = "status" if a.wide else "id"
    fields = {"filter": "status,bytes" if a.wide else "id,status,bytes", "count": "status", "sum": "status,bytes", "cut": "status,bytes"}

    def per_question(label, make):
        C[label] = {q: make(q) for q in a.questions.split(",")}

    if "table" in only:
        per_question("table jsonl, file", lambda q: table_cmds(a.bin, jl, "jsonl", fields[q], pick=pick)[q])
        per_question("table jsonl, stdin (cat |)", lambda q: "cat %s | %s" % (jl, table_cmds(a.bin, "-", "jsonl", fields[q], pick=pick)[q]))
        for th in (4, 16):
            per_question("table jsonl, --threads %d" % th, lambda q, th=th: table_cmds(a.bin, jl, "jsonl", fields[q], "--threads %d --parallel-min-bytes 0" % th, pick=pick)[q])
        if not a.wide:
            per_question("table csv (same rows), file", lambda q: table_cmds(a.bin, csvf, "csv", "")[q])
    sql = {
        "filter": "select {pick} from {src} where status = 404 and bytes > 50000",
        "count": "select status, count(*) from {src} group by status order by status",
        "sum": "select status, sum(bytes) from {src} group by status order by status",
        "cut": "select status, bytes from {src}",
    }
    if "duckdb" in only and shutil.which("duckdb"):
        for th in (1, 16):
            for lab, src in (("read_ndjson", "read_ndjson('%s')" % jl), ("read_json_auto", "read_json_auto('%s')" % jl)):
                per_question("duckdb %d thr %s" % (th, lab), lambda q, th=th, src=src: "duckdb -csv -noheader -c \"set threads=%d; %s\"" % (th, sql[q].format(pick=pick, src=src + (" limit %d" % a.rows if a.rows != 1000000 else ""))))
    if "mlr" in only and shutil.which("mlr"):
        m = {
            "filter": "mlr --ijsonl --ocsv --headerless-csv-output filter '$status==404 && $bytes>50000' then cut -f %s %s" % (pick, jl),
            "count": "mlr --ijsonl --ocsv --headerless-csv-output count-distinct -f status then sort -f status %s" % jl,
            "sum": "mlr --ijsonl --ocsv --headerless-csv-output stats1 -a sum -f bytes -g status then sort -f status %s" % jl,
            "cut": "mlr --ijsonl --ocsv --headerless-csv-output cut -o -f status,bytes %s" % jl,
        }
        per_question("miller (default threads)", lambda q: m[q])
    # the answers first
    for label, qs in C.items():
        for q, cmd in qs.items():
            el, rss, out, rc = run(cmd)
            if rc != 0 or not answer_ok(q, out, t):
                print("%-34s %-7s ANSWER DISAGREES with Python (exit %d): stopping" % (label, q, rc))
                sys.exit(2)
    best = {(l, q): [9e9, 0.0] for l, qs in C.items() for q in qs}
    load0 = os.getloadavg()
    for _ in range(a.reps):
        for label, qs in C.items():
            for q, cmd in qs.items():
                el, rss, _, _ = run(cmd + " > /dev/null")
                b = best[(label, q)]
                b[0] = min(b[0], el)
                b[1] = max(b[1], rss)
    load1 = os.getloadavg()
    print("%s on %s %s, file %s (%d lines), reps %d (minimum), load average before %s after %s" % (
        "wide" if a.wide else "narrow", platform.node(), platform.platform(terse=True), os.path.basename(jl), a.rows, a.reps,
        "/".join("%.2f" % x for x in load0), "/".join("%.2f" % x for x in load1)))
    qs_all = a.questions.split(",")
    print("%-34s" % "seconds (peak MB)" + "".join("%18s" % q for q in qs_all))
    for label in C:
        print("%-34s" % label + "".join("%11.4f (%4.0f)" % tuple(best[(label, q)]) if (label, q) in best else "%18s" % "" for q in qs_all))


if __name__ == "__main__":
    main()
