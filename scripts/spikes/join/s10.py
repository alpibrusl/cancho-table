#!/usr/bin/env python3
"""Spike S10 (docs/join-redesign.md section 3, cancho-table#116): R1, a partitioned join with one job per partition.

    python3 scripts/spikes/join/s10.py [--cancho PATH] [--work DIR] [--runs 3] [--reps 5] [--json FILE]

Builds scripts/spikes/join/s10/partition.cho with the Rust compiler (CANCHO_COMPILER=rust: the reference checks; the default compiler of cancho lets programs through that the
language refuses, section 1 of the design), then for each size and number of partitions runs it three ways: both joins in one process (the timings and the check that R1's answer is
the sequential one), the sequential join alone and R1 alone (the peak resident set of each, from wait4 of a small launcher). Prints the minimum over --runs processes of the minimum over --reps repetitions.
"""
import argparse, json, os, pathlib, platform, shutil, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
SIZES = ((100_000, 400_000), (1_000_000, 4_000_000))
PARTS = (1, 2, 4, 8)


def build(cancho, out):
    env = dict(os.environ, CANCHO_COMPILER="rust")
    p = subprocess.run([cancho, "build", str(HERE / "s10" / "partition.cho"), str(ROOT / "tools" / "table" / "jtab.cho"), str(ROOT / "tools" / "table" / "gmap.cho"), "--std", "-o", str(out)],
                       capture_output=True, text=True, env=env, timeout=900)
    if p.returncode:
        sys.exit("cannot build the spike:\n" + p.stderr[-800:])


def launcher():
    out = os.path.join(tempfile.mkdtemp(prefix="maxrss-"), "maxrss")
    subprocess.run(["cc", "-O1", "-o", out, str(ROOT / "tests" / "conformance" / "maxrss.c")], check=True)
    return out


def run(binary, argv):
    """The numbers the spike prints."""
    p = subprocess.run([str(binary)] + [str(a) for a in argv], capture_output=True, text=True, check=True, timeout=900)
    return [int(x) for x in p.stdout.split()]


def peak_mb(helper, binary, argv):
    """The peak resident set of the program alone, from wait4 of a small launcher (a child forked from Python would start from Python's own mark: tests/conformance/maxrss.c)."""
    p = subprocess.run([helper, str(binary)] + [str(a) for a in argv], capture_output=True, text=True, check=True, timeout=900)
    kb, status = p.stdout.split()
    if int(status):
        sys.exit("the spike failed: %r" % (argv,))
    return int(kb) // 1024 if sys.platform != "darwin" else int(kb) // (1024 * 1024)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cancho", default=shutil.which("cancho") or "cancho")
    ap.add_argument("--work", default="/tmp/joins-s10")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    work = pathlib.Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    binary = work / "partition"
    build(a.cancho, binary)
    helper = launcher()
    ver = subprocess.run([a.cancho, "--version"], capture_output=True, text=True).stdout.strip()
    res = {"compiler": ver, "machine": platform.platform() + " " + platform.machine(), "cores": os.cpu_count(), "load_before": os.getloadavg(), "runs": a.runs, "reps": a.reps, "cells": []}
    print("compiler %s (CANCHO_COMPILER=rust)\nmachine %s, %d cores; load average before %s\n" % (ver, res["machine"], os.cpu_count(), res["load_before"]))
    print("%9s %9s %5s | %9s %9s %9s | %6s %6s %6s %6s | %7s | %8s %8s | %s" % ("build", "probe", "parts", "seq build", "seq probe", "seq total", "route", "par", "merge", "R1 tot", "speedup", "rss seq", "rss R1", "ok"))
    for nb, npr in SIZES:
        for parts in PARTS:
            best = None
            ok = 0
            for _ in range(a.runs):
                v = run(binary, [nb, npr, parts, a.reps, 0])
                ok += v[6]
                best = v if best is None else [min(x, y) for x, y in zip(best, v)]
            sb, sp, rt, par, mg, tot, _ = best
            rss_seq = min(peak_mb(helper, binary, [nb, npr, parts, 1, 1]) for _ in range(a.runs))
            rss_r1 = min(peak_mb(helper, binary, [nb, npr, parts, 1, 2]) for _ in range(a.runs))
            seq = sb + sp
            cell = {"build": nb, "probe": npr, "parts": parts, "seq_build": sb, "seq_probe": sp, "route": rt, "par": par, "merge": mg, "r1_total": tot, "speedup": round(seq / tot, 2) if tot else None,
                    "rss_seq_mb": rss_seq, "rss_r1_mb": rss_r1, "ok_runs": ok, "of": a.runs * a.reps}
            res["cells"].append(cell)
            print("%9d %9d %5d | %9d %9d %9d | %6d %6d %6d %6d | %7.2f | %8d %8d | %d/%d" % (nb, npr, parts, sb, sp, seq, rt, par, mg, tot, seq / tot, rss_seq, rss_r1, ok, a.runs * a.reps))
    res["load_after"] = os.getloadavg()
    print("\nload average after %s" % (res["load_after"],))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
