#!/usr/bin/env python3
"""A seeded generator for the layout of db-benchmark's GROUPBY data (docs/named-benchmarks.md, section 4.2). NB1 of the named-benchmarks staging.

    python3 scripts/bench_named_gen.py --rows 1e6 [--k 100] [--seed 108] [--out DIR]

**This is NOT the h2oai R generator: same layout, different bytes, so published numbers are not comparable.** The owner's file is made by R
(`groupby-datagen.R`, `set.seed(108)`, `sample()`), whose draws cannot be reproduced without R and `data.table`; this script writes a file of the same
header, the same columns, the same value sets and the same formats, drawn independently and uniformly with replacement as the owner's script draws,
from a generator specified below. A number measured on this file is labelled "H2O-groupby layout, own generator, seed 108", never "db-benchmark".

The layout (the owner's `G1_<N>_<K>_0_0.csv`, no NAs, random order):

    id1, id2   text  "id%03d" of 1..K        (K values)
    id3        text  "id%010d" of 1..N/K      (N/K values)
    id4, id5   int   1..K
    id6        int   1..N/K
               (all draws are with replacement, so a column of m values holds all m only when there are many draws a value: N/m >= 20 for sure; at K=2 or 10,
               id3 and id6 miss some of their N/K values, as the owner's `sample` does)
    v1         int   1..5
    v2         int   1..15
    v3         number in [0, 100) with at most 6 decimals, written with the trailing zeros dropped (`5`, `5.5`, `0.000001`), as `fwrite` writes them
               (fwrite would write a value under 1e-4 in the form `1e-06`; this script never does, which only matters for 1 row in 10,000)

The generator is specified here, so that the same seed gives the same bytes on every machine and every Python (no `random`, no numpy: their streams are
not promised across versions):

    state = seed (a 64-bit integer); each draw is SplitMix64:
        state = (state + 0x9E3779B97F4A7C15) mod 2^64
        z = state; z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) mod 2^64; z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) mod 2^64; draw = z ^ (z >> 31)
    a row takes nine draws, in the order id1, id2, id3, id4, id5, id6, v1, v2, v3; a column of m values takes `draw mod m` (the bias is under m / 2^64)
    id1, id2, id4, id5: m = K, value = draw mod m + 1; id3, id6: m = N/K; v1: 5; v2: 15; v3: m = 100,000,000, the value is (draw mod m) millionths.

The file is written in blocks (memory is bounded by one block, not by N), to DIR (--out, else the environment variable NAMED_DATA, else a directory under the
system's temporary directory), never inside the repository, with `<name>.meta.json` beside it: seed, N, K, the byte count, the sha256 and the label. Data is never
committed. The file is refused if the disk has less than three times its estimated size free, or if DIR is inside a git work tree of this repository.
"""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

LABEL = "H2O-groupby layout, own generator (SplitMix64), seed %d: NOT the h2oai R generator, same layout and different bytes, so published numbers are not comparable"
HEADER = "id1,id2,id3,id4,id5,id6,v1,v2,v3"
COLUMNS = HEADER.split(",")
DEFAULT_SEED = 108
BLOCK = 50_000  # rows per block written

MASK = (1 << 64) - 1
GAMMA = 0x9E3779B97F4A7C15
M1 = 0xBF58476D1CE4E5B9
M2 = 0x94D049BB133111EB
V3_M = 100_000_000


def spec(n, k):
    """The documented layout as data: per column, its kind, its value count and its range. The tests hold the generator to exactly this."""
    if k < 1 or n < k or n % k:
        raise ValueError("N must be a positive multiple of K (N/K is a count of values): N=%d K=%d" % (n, k))
    big = n // k
    return {
        "id1": {"kind": "text", "format": "id%03d", "count": k, "lo": 1, "hi": k},
        "id2": {"kind": "text", "format": "id%03d", "count": k, "lo": 1, "hi": k},
        "id3": {"kind": "text", "format": "id%010d", "count": big, "lo": 1, "hi": big},
        "id4": {"kind": "int", "count": k, "lo": 1, "hi": k},
        "id5": {"kind": "int", "count": k, "lo": 1, "hi": k},
        "id6": {"kind": "int", "count": big, "lo": 1, "hi": big},
        "v1": {"kind": "int", "count": 5, "lo": 1, "hi": 5},
        "v2": {"kind": "int", "count": 15, "lo": 1, "hi": 15},
        "v3": {"kind": "millionths", "count": V3_M, "lo": 0, "hi": V3_M - 1},
    }


def v3_text(m):
    """Millionths as text, the trailing zeros of the fraction dropped (and the point when nothing is left)."""
    whole, frac = divmod(m, 1_000_000)
    if frac == 0:
        return str(whole)
    return "%d.%s" % (whole, ("%06d" % frac).rstrip("0"))


def blocks(n, k, seed=DEFAULT_SEED, block=BLOCK):
    """The file as blocks of text (the header first), never more than `block` rows alive. The one definition of the bytes."""
    spec(n, k)
    big = n // k
    small = ["id%03d" % i for i in range(1, k + 1)]
    state = seed & MASK
    done = 0
    yield HEADER + "\n"
    while done < n:
        m = min(block, n - done)
        out = []
        add = out.append
        for _ in range(m):
            row = []
            for mod in (k, k, big, k, k, big, 5, 15, V3_M):
                state = (state + GAMMA) & MASK
                z = state
                z = ((z ^ (z >> 30)) * M1) & MASK
                z = ((z ^ (z >> 27)) * M2) & MASK
                row.append((z ^ (z >> 31)) % mod)
            a, b, c, d, e, f, g, h, i = row
            add("%s,%s,id%010d,%d,%d,%d,%d,%d,%s" % (small[a], small[b], c + 1, d + 1, e + 1, f + 1, g + 1, h + 1, v3_text(i)))
        out.append("")
        yield "\n".join(out)
        done += m


def default_dir():
    return pathlib.Path(os.environ.get("NAMED_DATA") or os.path.join(tempfile.gettempdir(), "cancho-named-data"))


def name_of(n, k):
    return "G1_%s_%s_0_0.csv" % (pretty(n), pretty(k))


def pretty(x):
    """1e7 for 10000000, 2e0 for 2, as the owner's file names write them (a digit and a power of ten); any other number as itself."""
    s = str(x)
    if set(s[1:]) <= {"0"}:
        return "%se%d" % (s[0], len(s) - 1)
    return s


def estimate_bytes(n, k):
    """About 51.5 bytes a row at K=100 (docs/named-benchmarks.md 3.5): the sum of the fixed widths, with the digits of the ids counted."""
    return int(n * (5 + 5 + 12 + len(str(k)) + len(str(k)) + len(str(n // k)) + 1 + 1.7 + 8.9 + 9)) + 1


def inside_repo(path):
    p = pathlib.Path(path).resolve()
    return p == ROOT or ROOT in p.parents


def write(n, k, out_dir, seed=DEFAULT_SEED, force=False, block=BLOCK):
    """Write the file and its metadata. Returns (path, meta). Refuses a directory inside the repository and a disk that is nearly full."""
    out_dir = pathlib.Path(out_dir)
    if inside_repo(out_dir):
        raise SystemExit("bench_named_gen: %s is inside the repository: data is never committed, give --out outside it" % out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    need = estimate_bytes(n, k)
    free = shutil.disk_usage(out_dir).free
    if free < 3 * need and not force:
        raise SystemExit("bench_named_gen: %d bytes free in %s, under three times the estimated %d" % (free, out_dir, need))
    path = out_dir / name_of(n, k)
    sha = hashlib.sha256()
    size = 0
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        for text in blocks(n, k, seed, block):
            data = text.encode("ascii")
            sha.update(data)
            size += len(data)
            f.write(data)
    os.replace(tmp, path)
    meta = {"name": path.name, "suite": "groupby", "rows": n, "k": k, "nas": 0, "sorted": 0, "seed": seed, "generator": "bench_named_gen.py (SplitMix64)",
            "bytes": size, "sha256": sha.hexdigest(), "label": LABEL % seed}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return path, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="NOT the h2oai R generator: same layout, different bytes, so published numbers are not comparable.")
    ap.add_argument("--rows", default="1e6", help="N, e.g. 1e6 or 10000000 (default 1e6); N must be a multiple of K")
    ap.add_argument("--k", default="100", help="K (default 100: the report's default case)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", default=None, help="the directory (else $NAMED_DATA, else <tmp>/cancho-named-data); never inside the repository")
    ap.add_argument("--force", action="store_true", help="skip the free-space check")
    a = ap.parse_args(argv)
    n, k = int(float(a.rows)), int(float(a.k))
    out = pathlib.Path(a.out) if a.out else default_dir()
    path, meta = write(n, k, out, a.seed, a.force)
    print("%s  %d rows  %d bytes  sha256 %s" % (path, n, meta["bytes"], meta["sha256"]))
    print(meta["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
