"""SPIKE check: scripts/spikes/sort/sortpar.cho against Python's stable sort on random files, for every thread count.

    sort_check.py SORTPAR [--cases N] [--seed S]     (SORTPAR: the built scripts/spikes/sort/sortpar.cho)

Files: header + rows `id,t,i,u` (id the row number, t a text key, i an integer key, u another text key), no quotes.
The text keys are drawn from awkward pools (empty, one byte, a shared 20-byte prefix, 14/15/16-byte keys that differ only at the
end, keys with a NUL, bytes above 127, heavy duplicates), the integer keys from small ranges, negatives, and values near +-2^62.
Every (keys: one or two, either direction, text or integer; top 0, 1, 3 or 100; threads in {1,2,3,5,8,16}; sort variant m/r; rows in the run or
read back by offset) gives the same bytes as Python's stable sorts, last key first, each `sorted(..., reverse=desc)` (which keeps ties in order).
"""
import random, subprocess, sys, tempfile, os, argparse

POOL = [b"", b"a", b"b", b"ab", b"abc", b"a\x00", b"a\x00\x00", b"\xff", b"\xc3\xa9", b"A", b"Z", b"0", b"10", b"9",
        b"commonprefix-0000-a", b"commonprefix-0000-b", b"commonprefix-0000", b"commonprefix-0001",
        b"0123456789abcd", b"0123456789abce", b"0123456789abcde", b"0123456789abcdf", b"0123456789abcdef", b"0123456789abcdeg",
        b"zzzzzzzzzzzzzz", b"zzzzzzzzzzzzzzz", b"zzzzzzzzzzzzzzzz"]
INTS = [0, 1, -1, 2, 10, 9, 100, -100, 4611686018427387903, -4611686018427387903, 4611686018427387900, 123456789012345678, -123456789012345678, 9223372036854775807, -9223372036854775807]


def make(rng, n, shape):
    rows = []
    for k in range(n):
        if shape == 0:
            t = rng.choice(POOL)
            u = rng.choice(POOL)
            i = rng.choice(INTS)
        elif shape == 1:
            t = bytes(rng.choice(b"abc") for _ in range(rng.randrange(0, 20)))
            u = bytes(rng.choice(b"01") for _ in range(rng.randrange(0, 5)))
            i = rng.randrange(-5, 5)
        elif shape == 2:
            t = b"key%07d" % rng.randrange(10 ** 7)
            u = b"k%d" % rng.randrange(50)
            i = rng.randrange(-10 ** 9, 10 ** 9)
        else:
            # keys that share a long prefix, and a few that do not (below it, above it, shorter than it, equal to it, with a NUL after it)
            pre = b"https://www.example.com/products/item-"
            r = rng.random()
            if r < 0.9:
                t = pre + b"%d" % rng.randrange(10 ** rng.choice([2, 7, 30]))
            elif r < 0.93:
                t = rng.choice([b"", b"a", b"https", b"https://www.example.com/products", b"https://www.example.com/products/item", b"https://www.example.com/products/item-"])
            elif r < 0.96:
                t = rng.choice([b"zzz", b"https://www.example.com/q", b"https://www.example.com/products/item.", b"https://www.example.com/products/itemz"])
            else:
                t = pre + b"7\x00" + b"%d" % rng.randrange(5)
            u = b"k%d" % rng.randrange(5)
            i = rng.randrange(-3, 3)
        t = t.replace(b",", b"_").replace(b"\n", b"_").replace(b"\r", b"_").replace(b'"', b"_")
        u = u.replace(b",", b"_").replace(b"\n", b"_").replace(b"\r", b"_").replace(b'"', b"_")
        rows.append(b"%d,%s,%d,%s\n" % (k, t, i, u))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("--cases", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    bad = 0
    runs = 0
    specs = [[(1, "t", 0)], [(1, "t", 1)], [(3, "t", 0)], [(2, "i", 0)], [(2, "i", 1)],
             [(1, "t", 0), (2, "i", 1)], [(2, "i", 0), (3, "t", 1)], [(3, "t", 0), (1, "t", 0)], [(1, "t", 1), (3, "t", 1)]]
    for case in range(a.cases):
        n = rng.choice([0, 1, 2, 3, 7, 64, 65, 129, 1000, 5000, 20000, 20000])
        shape = rng.randrange(4)
        rows = make(rng, n, shape)
        header = b"id,t,i,u\n"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
            f.write(header + b"".join(rows))
            path = f.name
        for spec in specs:
            want_rows = list(rows)
            for col, kind, desc in reversed(spec):
                def key(r, col=col, kind=kind):
                    cells = r.rstrip(b"\n").split(b",")
                    return cells[col] if kind == "t" else int(cells[col])
                want_rows = sorted(want_rows, key=key, reverse=bool(desc))
            keys = ",".join("%d:%s:%s" % (c, k, "d" if d else "a") for c, k, d in spec)
            for top in (0, 1, 3, 100):
                want = header + b"".join(want_rows[:top] if top else want_rows)
                for t in (1, 2, 3, 5, 8, 16):
                    for sv in ("m", "r"):
                        outs = ["m"]
                        if all(k == "i" for _, k, _ in spec):
                            outs.append("p")
                        for out in outs:
                            p = subprocess.run([a.bin, path, str(t), "w", sv, keys, str(top), out], capture_output=True)
                            runs += 1
                            if p.stdout != want:
                                bad += 1
                                print("DIFF case %d n=%d shape=%d keys=%s top=%d threads=%d sort=%s out=%s (rc %d)" % (case, n, shape, keys, top, t, sv, out, p.returncode))
                                if bad < 4:
                                    open("/tmp/sortcheck_%d.csv" % bad, "wb").write(header + b"".join(rows))
        os.unlink(path)
    print("%d runs, %d differences" % (runs, bad))
    sys.exit(1 if bad else 0)


main()
