#!/usr/bin/env python3
"""The differential gate of the float reader (docs/gap-float.md section 7): N generated and adversarial cells through `fbench C` (the reader of tools/table/flt.cho) and through
Python's float() (reference.py), the checksums of every file of 1M cells compared; on a difference the first differing cells are printed and the file kept.

    differential.py --bin OUT/fbench [--cells 100000000] [--workers 6] [--seed 1] [--tmp DIR] [--exact]

--exact: run the exact way for every cell (the bin must be built from a flt.cho whose Eisel-Lemire is switched off: see docs/gap-float.md); not needed for the gate.
The mix (share of the cells): f17 20, bits 20, d19 14, exp 10, d25 6, lead 6, money 5, price 5, p10 4, long 5, near 5 (a double's midpoint cut at 17 to 40 digits, a hair above or below),
mid 3 (exact midpoints, up to 767 digits, and the digit string +-1), edge 1 (mantissas at the powers where a rule changes), hard 2 (the 640 cells of tests/conformance/test_float.py, drawn again with other seeds)."""
import argparse
import multiprocessing
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reference  # noqa: E402

MIX = [("f17", 20, 1_000_000), ("bits", 20, 1_000_000), ("d19", 14, 1_000_000), ("exp", 10, 1_000_000), ("d25", 6, 1_000_000), ("lead", 6, 1_000_000),
       ("money", 5, 1_000_000), ("price", 5, 1_000_000), ("p10", 4, 1_000_000), ("long", 5, 200_000), ("near", 5, 300_000), ("mid", 3, 20_000), ("hard", 2, 20_000), ("edge", 1, 1)]


def task(a):
    bin_, tmp, kind, rows, seed = a
    path = pathlib.Path(tmp) / f"{kind}_{seed}.txt"
    with open(path, "w") as f:
        subprocess.run([sys.executable, str(HERE / "gen_cells.py"), kind, str(rows), str(seed)], stdout=f, check=True)
    got = subprocess.run([bin_, "C", "1"], stdin=open(path), capture_output=True, check=True).stdout.decode().strip()
    lines = reference.lines_of(path)
    cells = len(lines)
    want = reference.checksum(lines)
    want = want - (1 << 64) if want >> 63 else want
    ok = int(got) == want
    if ok:
        path.unlink()
    return kind, seed, cells, ok, str(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--cells", type=int, default=100_000_000)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tmp", default="/tmp/float-diff")
    a = ap.parse_args()
    os.makedirs(a.tmp, exist_ok=True)
    total_weight = sum(w for _, w, _ in MIX)
    tasks = []
    for kind, w, rows in MIX:
        want = a.cells * w // total_weight
        # draws of `hard` are 4 cells each
        per = rows * (4 if kind == "hard" else 3 if kind == "mid" else 1)
        n_files = 1 if kind == "edge" else max(1, round(want / per))
        for i in range(n_files):
            tasks.append((a.bin, a.tmp, kind, rows, a.seed * 1000 + i))
    t0 = time.time()
    done = bad = 0
    per_kind = {}
    with multiprocessing.Pool(a.workers) as pool:
        for kind, seed, cells, ok, path in pool.imap_unordered(task, tasks):
            done += cells
            per_kind[kind] = per_kind.get(kind, 0) + cells
            if not ok:
                bad += 1
                print(f"DIFFERENCE {kind} seed {seed}: {path}", flush=True)
            print(f"\r{done:>12,} cells  {time.time() - t0:6.0f} s  {bad} bad files", end="", flush=True)
    print()
    print("cells per shape:", ", ".join(f"{k} {v:,}" for k, v in sorted(per_kind.items())))
    print(f"{done:,} cells, {bad} files with a difference, {time.time() - t0:.0f} s")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
