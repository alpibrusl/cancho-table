#!/usr/bin/env python3
"""SPIKE S1 (docs/joins.md section 15): is the accounted size of the build table of section 6.2 a usable bound against resident bytes?

    python3 s1.py --data DIR [--bin s1_build] [--runs 5] [--json out.json]

DIR holds the files of gen.py. For each file: the accounted bytes by the formula, computed here from the file (sum over rows of record bytes + 16, plus
over distinct keys key bytes + 60), against the number the spike's own counter prints (they must agree, or the run stops); then, as the minimum and the
maximum of --runs runs, the build time the program measures (clock_ms around the open, the read and the table) and the peak resident set the kernel reports
(`/usr/bin/time -l`, "maximum resident set size", bytes) of: `scan` (read and split the lines, hold nothing: the floor of the reader), `build` (the table
grown by doubling, as a build that does not know its size), `presize` (the table made with room for exactly the keys, rows and record bytes the file has).
Build with: cancho build s1_build.cho jtab.cho ../../../tools/table/gmap.cho --std -o s1_build"""
import argparse
import json
import os
import pathlib
import platform
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py  # noqa: E402


def formula(path):
    rows = 0
    rec = 0
    keys = {}
    with open(path, "rb") as f:
        f.readline()
        for line in f:
            line = line.rstrip(b"\n")
            k = line.split(b",", 1)[0]
            rows += 1
            rec += len(line)
            keys[k] = 1
    kb = sum(len(k) for k in keys)
    return {"rows": rows, "distinct": len(keys), "record_bytes": rec, "key_bytes": kb, "accounted": rec + 16 * rows + kb + 60 * len(keys), "size": os.path.getsize(path)}


def run(binary, argv):
    p = subprocess.run(["/usr/bin/time", "-l", str(binary)] + argv, capture_output=True, text=True, timeout=600)
    if p.returncode:
        sys.exit(f"{argv}: exit {p.returncode}: {p.stderr[-300:]}")
    out = p.stdout.split()
    rss = int(re.search(r"(\d+)\s+maximum resident set size", p.stderr).group(1))
    foot = int(re.search(r"(\d+)\s+peak memory footprint", p.stderr).group(1))
    real = float(re.search(r"([\d.]+)\s+real", p.stderr).group(1))
    return {"rows": int(out[0]), "distinct": int(out[1]), "accounted": int(out[2]), "build_ms": int(out[3]), "probe_ms": int(out[4]), "found": int(out[5]), "rss": rss, "footprint": foot, "real": real}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--bin", default="s1_build")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", default="")
    ap.add_argument("--files", default="int_100k,int_1m,text_100k,text_1m")
    a = ap.parse_args()
    load0 = os.getloadavg()
    result = {"machine": platform.platform() + " " + platform.machine(), "cores": os.cpu_count(), "runs": a.runs, "load_before": load0, "files": {}}
    print(f"machine {result['machine']}, {os.cpu_count()} cores; load average before {load0}; minimum (and maximum) of {a.runs} runs; one thread\n")
    hdr = f"{'file':10s} {'rows':>8s} {'accounted':>12s} {'scan rss':>10s} {'build rss':>11s} {'ratio':>6s} {'-floor':>7s} {'presize rss':>12s} {'ratio':>6s} {'build ms':>9s} {'presize ms':>10s}"
    print(hdr)
    for name in a.files.split(","):
        path = pathlib.Path(a.data) / (name + ".csv")
        fm = formula(path)
        scans = [run(a.bin, ["scan", str(path)]) for _ in range(a.runs)]
        builds = [run(a.bin, ["build", str(path)]) for _ in range(a.runs)]
        pre = [run(a.bin, ["presize", str(path), str(fm["distinct"]), str(fm["rows"]), str(fm["record_bytes"])]) for _ in range(a.runs)]
        ver = run(a.bin, ["verify", str(path)])
        for r in builds + pre + [ver]:
            if r["accounted"] != fm["accounted"] or r["rows"] != fm["rows"] or r["distinct"] != fm["distinct"]:
                sys.exit(f"{name}: the program's count {r} is not the formula {fm}")
        if ver["found"] != fm["rows"]:
            sys.exit(f"{name}: verify found {ver['found']} of {fm['rows']} rows in their own chains")
        floor = min(r["rss"] for r in scans)
        b_rss = [r["rss"] for r in builds]
        p_rss = [r["rss"] for r in pre]
        e = {"formula": fm, "scan_rss": [r["rss"] for r in scans], "build_rss": b_rss, "build_footprint": [r["footprint"] for r in builds], "presize_rss": p_rss,
             "build_ms": [r["build_ms"] for r in builds], "presize_ms": [r["build_ms"] for r in pre], "scan_ms": [r["build_ms"] for r in scans], "verify_probe_ms": ver["probe_ms"]}
        result["files"][name] = e
        acc = fm["accounted"]
        print(f"{name:10s} {fm['rows']:8d} {acc:12d} {floor:10d} {min(b_rss):11d} {min(b_rss) / acc:6.2f} {(min(b_rss) - floor) / acc:7.2f} {min(p_rss):12d} {min(p_rss) / acc:6.2f} {min(e['build_ms']):9d} {min(e['presize_ms']):10d}")
    result["load_after"] = os.getloadavg()
    print(f"\nload average after {result['load_after']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(result, indent=1) + "\n")


main()
