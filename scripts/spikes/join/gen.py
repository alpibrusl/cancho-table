#!/usr/bin/env python3
"""SPIKE S1/S0 (docs/joins.md section 15): the build files. `k,a,b,c` with unique keys in random order and three text columns of 18 to 22 bytes.

    python3 gen.py DIR            writes DIR/int_100k.csv int_1m.csv text_100k.csv text_1m.csv dup_100k.csv text_pow2.csv text_pow2p1.csv wide_pow2.csv (seed 7)

int_*: the key is a decimal integer (up to 10 times the row count); text_*: the key is `u%08x-%d`, about 14 bytes; dup_100k: 100,000 rows over 10,000 text keys
(chains of 10, to check the chain order of the table); text_pow2 and text_pow2p1: 2^20 and 2^20 + 1 text-key rows, the two edges of the arrays' doubling. The files are not committed; the same seed gives the same bytes."""
import random
import sys
import pathlib


def main():
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(7)
    alpha = "abcdefghijklmnopqrstuvwxyz0123456789"
    pool = ["".join(rnd.choice(alpha) for _ in range(rnd.randint(18, 22))) for _ in range(4096)]

    def cols():
        return ",".join(pool[rnd.randrange(4096)] for _ in range(3))

    for kind, n in (("int", 100_000), ("int", 1_000_000), ("text", 100_000), ("text", 1_000_000)):
        name = out / f"{kind}_{n // 1000 if n < 1_000_000 else 1}{'k' if n < 1_000_000 else 'm'}.csv"
        if kind == "int":
            keys = [str(v) for v in rnd.sample(range(10 * n), n)]
        else:
            keys = [f"u{rnd.getrandbits(32):08x}-{i}" for i in range(n)]
        with open(name, "w", newline="\n") as f:
            f.write("k,a,b,c\n")
            for k in keys:
                f.write(k + "," + cols() + "\n")
        print(name, n)
    # the edges of the doubling: 2^20 rows (the arrays have just filled) and 2^20 + 1 (every array has just doubled and is half empty)
    for n, tag in ((1 << 20, "text_pow2"), ((1 << 20) + 1, "text_pow2p1")):
        with open(out / (tag + ".csv"), "w", newline="\n") as f:
            f.write("k,a,b,c\n")
            for i in range(n):
                f.write(f"u{rnd.getrandbits(32):08x}-{i}," + cols() + "\n")
        print(out / (tag + ".csv"), n)
    # wide_pow2: 100,000 rows of 671 bytes (8,900 of them 672) with unique text keys: the records total 2^26 + 36 bytes, so the record buffer (64 KiB doubled 10 times) has to double a last time at the
    # end of the file, while it holds 64 MiB: the worst moment of the growth of the one array that is nearly all of the table
    with open(out / "wide_pow2.csv", "w", newline="\n") as f:
        f.write("k,pad\n")
        for i in range(100_000):
            key = f"u{rnd.getrandbits(32):08x}-{i}"
            size = 672 if i < 8_900 else 671
            f.write(key + "," + "x" * (size - len(key) - 1) + "\n")
    print(out / "wide_pow2.csv", 100_000)
    name = out / "dup_100k.csv"
    with open(name, "w", newline="\n") as f:
        f.write("k,a,b,c\n")
        for i in range(100_000):
            f.write(f"d{rnd.randrange(10_000):05d}," + cols() + "\n")
    print(name, 100_000)


main()
