#!/usr/bin/env python3
"""ns per cell of the readers of fbench.cho: (time of 21 rounds - time of 1 round) / 20 / cells, minimum of REPS runs of each, so that reading the file drops out.

    time_cells.py BIN DATADIR [REPS] [KIND ...]      (DATADIR holds c_KIND.txt files; modes l (floor), o (origin/main), n (new))"""
import os
import subprocess
import sys
import time

bin_, data = sys.argv[1], sys.argv[2]
reps = int(sys.argv[3]) if len(sys.argv) > 3 else 3
kinds = sys.argv[4:] or ["price", "f17", "money", "bits", "exp", "d19", "d25", "lead", "p10"]


def best(mode, rounds, f):
    b = 1e9
    for _ in range(reps):
        t = time.perf_counter()
        subprocess.run([bin_, mode, str(rounds)], stdin=open(f), capture_output=True)
        b = min(b, time.perf_counter() - t)
    return b


print(f"load {os.getloadavg()}")
print(f"{'cells':8s} {'rows':>9s} {'line':>8s} {'origin':>8s} {'new':>8s}   (ns/cell, line = the floor included in both)")
for k in kinds:
    f = f"{data}/c_{k}.txt"
    rows = sum(1 for _ in open(f, "rb"))
    out = []
    for m in (("l", "o", "n") if os.environ.get("OLD", "1") == "1" else ("l", "n")):
        a, b = best(m, 1, f), best(m, 11, f)
        out.append(1e9 * (b - a) / 10 / rows)
    if len(out) == 2:
        out = [out[0], float('nan'), out[1]]
    print(f"{k:8s} {rows:9d} {out[0]:8.1f} {out[1]:8.1f} {out[2]:8.1f}")
