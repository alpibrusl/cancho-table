#!/usr/bin/env python3
"""A seeded generator for the layout of db-benchmark's JOIN data (docs/named-benchmarks.md, sections 5.1 and 19). NB3 of the named-benchmarks staging.

    python3 scripts/bench_named_join_gen.py --rows 1e6 [--seed 108] [--out DIR] [--sorted-by q1,q2,...]

**This is NOT the h2oai R generator (`join-datagen.R`): same layout, different bytes, so published numbers are not comparable.** The owner's generator needs N >= 1e7 and R;
this one writes the same tables with the same shape from the SplitMix64 stream of NB1 (scripts/bench_named_gen.py), at any N >= 100 (below 1e7 the sizes are a scaled-down
development size, not a named one).

The tables, for N left rows (the owner's names `J1_<N>_NA_0_0` for x and `J1_<N>_<n>_0_0` for a right table of n rows):

    x       id1 id2 id3 (integers)  id4 id5 id6 (text "id" + id1, id2, id3)  v1 (a number in [0, 100) with at most 6 decimals, trailing zeros dropped)
    small   id1 id4 v2                           n1 = max(N // 10^6, 10) rows
    medium  id1 id2 id4 id5 v2                   n2 = max(N // 10^3, 10) rows
    big     id1 id2 id3 id4 id5 id6 v2           n3 = N rows

(the owner's n are N/1e6, N/1e3 and N; at N = 1e7 they are 10, 10,000 and 10,000,000. The floor of 10 keeps `small` a table with a matching and a non-matching key below 1e7.)

The keys, for each n in (n1, n2, n3): M = n + n // 10 integers 1..M are shuffled (Fisher-Yates); the left key set is the first n of the permutation, the right key set is the n from the
(n // 10)-th on. So 90 percent of the keys are common, n // 10 are left-only and n // 10 right-only. A right table has each of its keys once, in permuted order (its key column is unique).
x draws id1 from the left set 1, id2 from the left set 2 and id3 from the left set 3 **with every key at least once** (the owner's `sample_all`): the n keys first, then uniform draws for the
rest of the N rows, then the column is shuffled. (For id3 n = N, so id3 is a permutation of its keys: unique.) The right tables' other key columns draw uniformly from the right key sets
of the first two (small's id1; medium's id1 from set 1; big's id1 and id2). id4, id5 and id6 are "id" + the integer. v1 and v2 are `draw mod 10^8` millionths.

The stream (SplitMix64, as NB1; a draw is `next()`, `below(m)` is `next() mod m`), in this order, from the seed: the permutation of the small keys, of the medium keys, of the big keys
(Fisher-Yates from i = M - 1 down to 1, j = below(i + 1), swap); then x's id1 column (the n1 keys, then N - n1 draws, then a shuffle), id2 (the same with n2), id3 (n3 = N keys, a shuffle); then x's v1,
one draw a row in row order; then small (v2 a row), medium (id1 then v2 a row) and big (id1, id2, then v2 a row), each in row order.

Files go to DIR (--out, else $NAMED_DATA, else a directory under the system's temporary directory), never inside the repository, with `<x>.meta.json` beside x: the seed, N, the sizes, and for every
table its bytes and sha256. Data is never committed. `sorted_variant` writes a copy of a table sorted bytewise by the text of one column (for `table --join-sorted`).
Memory: the key arrays are 8 bytes a key, about 1.1 N for each of three arrays at the largest.
"""

import argparse
import array
import hashlib
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench_named_gen as gen  # noqa: E402

LABEL = "H2O-join layout, own generator (SplitMix64), seed %d: NOT the h2oai R generator, same layout and different bytes, so published numbers are not comparable"
MASK = gen.MASK
HEADERS = {
    "x": "id1,id2,id3,id4,id5,id6,v1",
    "small": "id1,id4,v2",
    "medium": "id1,id2,id4,id5,v2",
    "big": "id1,id2,id3,id4,id5,id6,v2",
}
TABLES = ("x", "small", "medium", "big")
MIN_ROWS = 100
BLOCK = 50_000


def sizes(n):
    """The row counts: n1 (small), n2 (medium), n3 (big, N rows)."""
    if n < MIN_ROWS:
        raise ValueError("the join generator needs N >= %d rows: N=%d" % (MIN_ROWS, n))
    return {"small": max(n // 10**6, 10), "medium": max(n // 10**3, 10), "big": n}


class Rng:
    """SplitMix64 as NB1 specifies it."""

    def __init__(self, seed):
        self.state = seed & MASK

    def next(self):
        self.state = (self.state + gen.GAMMA) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * gen.M1) & MASK
        z = ((z ^ (z >> 27)) * gen.M2) & MASK
        return z ^ (z >> 31)

    def below(self, m):
        return self.next() % m

    def shuffle(self, a):
        for i in range(len(a) - 1, 0, -1):
            j = self.next() % (i + 1)
            a[i], a[j] = a[j], a[i]


def key_sets(rng, n):
    """(left set, right set) of n keys each, in the order of the permutation: the left is its first n, the right the n from index n // 10."""
    m = n + n // 10
    perm = array.array("q", range(1, m + 1))
    rng.shuffle(perm)
    d = n // 10
    return perm[:n], perm[d:d + n]


def sample_all(rng, keys, rows):
    """`rows` values drawn from `keys`, each key at least once (the owner's sample_all): the keys, then uniform draws, then a shuffle."""
    col = array.array("q", keys)
    extra = rows - len(keys)
    for _ in range(extra):
        col.append(keys[rng.next() % len(keys)])
    rng.shuffle(col)
    return col


def spec(n):
    """The documented layout as data, for the tests: per table its rows, header and the key sets' sizes."""
    s = sizes(n)
    return {"x": {"rows": n, "header": HEADERS["x"]}, **{t: {"rows": s[t], "header": HEADERS[t]} for t in ("small", "medium", "big")}}


def tables(n, seed=gen.DEFAULT_SEED, block=BLOCK):
    """Yield (table name, iterator of text blocks, header first), the whole data set as a stream, one table at a time (x, small, medium, big). The one definition of the bytes."""
    s = sizes(n)
    rng = Rng(seed)
    ls, rs = {}, {}
    for name in ("small", "medium", "big"):
        ls[name], rs[name] = key_sets(rng, s[name])
    c1 = sample_all(rng, ls["small"], n)
    c2 = sample_all(rng, ls["medium"], n)
    c3 = sample_all(rng, ls["big"], n)
    v3 = gen.v3_text

    def x_blocks():
        yield HEADERS["x"] + "\n"
        for lo in range(0, n, block):
            out = []
            for i in range(lo, min(lo + block, n)):
                a, b, c = c1[i], c2[i], c3[i]
                out.append("%d,%d,%d,id%d,id%d,id%d,%s" % (a, b, c, a, b, c, v3(rng.next() % gen.V3_M)))
            out.append("")
            yield "\n".join(out)

    yield "x", x_blocks()

    def right_blocks(name):
        yield HEADERS[name] + "\n"
        keys = rs[name]
        r1, r2 = rs["small"], rs["medium"]
        total = len(keys)
        for lo in range(0, total, block):
            out = []
            for i in range(lo, min(lo + block, total)):
                if name == "small":
                    a = keys[i]
                    out.append("%d,id%d,%s" % (a, a, v3(rng.next() % gen.V3_M)))
                elif name == "medium":
                    a = r1[rng.next() % len(r1)]
                    b = keys[i]
                    out.append("%d,%d,id%d,id%d,%s" % (a, b, a, b, v3(rng.next() % gen.V3_M)))
                else:
                    a = r1[rng.next() % len(r1)]
                    b = r2[rng.next() % len(r2)]
                    c = keys[i]
                    out.append("%d,%d,%d,id%d,id%d,id%d,%s" % (a, b, c, a, b, c, v3(rng.next() % gen.V3_M)))
            out.append("")
            yield "\n".join(out)

    for name in ("small", "medium", "big"):
        yield name, right_blocks(name)


def file_name(table, n):
    """The owner's names: x is J1_<N>_NA_0_0, a right table J1_<N>_<rows>_0_0."""
    if table == "x":
        return "J1_%s_NA_0_0.csv" % gen.pretty(n)
    s = sizes(n)
    if table == "medium" and s["medium"] == s["small"]:   # below N = 2e4 both have 10 rows: the owner's names would be one file
        return "J1_%s_%s_0_0_medium.csv" % (gen.pretty(n), gen.pretty(s["medium"]))
    return "J1_%s_%s_0_0.csv" % (gen.pretty(n), gen.pretty(s[table]))


def estimate_bytes(n):
    """Computed, not measured: the bytes of the four files from the widths of their cells, taking each key to have the digits of the largest of its set (so a little over). A row of x is the
    digits of id1, id2 and id3 twice (the ids and their "id" copies), six for the "id"s, a v1 of about 8.9, six commas and a newline."""
    s = sizes(n)
    d = {t: len(str(s[t] + s[t] // 10)) for t in ("small", "medium", "big")}
    per_x = 2 * (d["small"] + d["medium"] + d["big"]) + 6 + 8.9 + 7
    per_small = 2 * d["small"] + 2 + 8.9 + 3
    per_medium = 2 * (d["small"] + d["medium"]) + 4 + 8.9 + 5
    return int(n * per_x + s["small"] * per_small + s["medium"] * per_medium + n * per_x) + 1


def default_dir():
    return gen.default_dir()


def write(n, out_dir, seed=gen.DEFAULT_SEED, force=False, block=BLOCK):
    """Write the four files and x's metadata. Returns (dict name -> path, meta). Refuses a directory inside the repository and a disk that is nearly full."""
    out_dir = pathlib.Path(out_dir)
    if gen.inside_repo(out_dir):
        raise SystemExit("bench_named_join_gen: %s is inside the repository: data is never committed, give --out outside it" % out_dir)
    sizes(n)
    out_dir.mkdir(parents=True, exist_ok=True)
    need = estimate_bytes(n)
    free = shutil.disk_usage(out_dir).free
    if free < 3 * need and not force:
        raise SystemExit("bench_named_join_gen: %d bytes free in %s, under three times the estimated %d" % (free, out_dir, need))
    paths, info = {}, {}
    for name, blocks_ in tables(n, seed, block):
        path = out_dir / file_name(name, n)
        sha = hashlib.sha256()
        size = 0
        tmp = path.with_name(path.name + ".part")
        with open(tmp, "wb") as f:
            for text in blocks_:
                data = text.encode("ascii")
                sha.update(data)
                size += len(data)
                f.write(data)
        os.replace(tmp, path)
        paths[name] = path
        info[name] = {"file": path.name, "rows": sizes(n)[name] if name != "x" else n, "bytes": size, "sha256": sha.hexdigest()}
    meta = {"suite": "join", "rows": n, "seed": seed, "generator": "bench_named_join_gen.py (SplitMix64)", "sizes": sizes(n), "tables": info, "label": LABEL % seed}
    meta_path = paths["x"].with_name(paths["x"].name + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=1) + "\n")
    return paths, meta


def sorted_variant(path, column, out_dir=None):
    """A copy of the csv `path` sorted bytewise by the text of `column`, rows with equal keys in their file order (stable). Returns the new path. In memory: for development sizes."""
    path = pathlib.Path(path)
    out = pathlib.Path(out_dir) if out_dir else path.parent
    with open(path, "r", encoding="ascii", newline="") as f:
        header = f.readline()
        names = header.rstrip("\n").split(",")
        at = names.index(column)
        rows = f.readlines()
    rows.sort(key=lambda line: line.split(",", at + 1)[at])
    dest = out / ("%s.sorted-by-%s.csv" % (path.name[:-4], column))
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "w", encoding="ascii", newline="") as f:
        f.write(header)
        f.writelines(rows)
    os.replace(tmp, dest)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="NOT the h2oai R generator: same layout, different bytes, so published numbers are not comparable.")
    ap.add_argument("--rows", default="1e6", help="N, at least 100 (default 1e6; the owner's named sizes are 1e7 and 1e8)")
    ap.add_argument("--seed", type=int, default=gen.DEFAULT_SEED)
    ap.add_argument("--out", default=None, help="the directory (else $NAMED_DATA, else <tmp>/cancho-named-data); never inside the repository")
    ap.add_argument("--force", action="store_true", help="skip the free-space check")
    a = ap.parse_args(argv)
    n = int(float(a.rows))
    paths, meta = write(n, pathlib.Path(a.out) if a.out else default_dir(), a.seed, a.force)
    for name in TABLES:
        t = meta["tables"][name]
        print("%-6s %s  %d rows  %d bytes  sha256 %s" % (name, paths[name], t["rows"], t["bytes"], t["sha256"]))
    print(meta["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
