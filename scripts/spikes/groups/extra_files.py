#!/usr/bin/env python3
"""extra_files.py DIR: files of other shapes than the adversarial f2.csv, to see that the numbers are not the shape of one generator.
Each is 1,000,000 rows of `id,k,k2,v` (seeded): uuid (32 hex digits, unique), prefix (`customer-00000123`, unique, shuffled), sorted (`key0000001`
ascending, unique), zipf (200,000 keys, a heavy head), twocol (k 50 values, k2 20,000 values: about 900,000 pairs)."""
import os, random, sys, uuid
d = sys.argv[1]
os.makedirs(d, exist_ok=True)
rng = random.Random(5)
N = 1_000_000
def write(name, keyf):
    with open(os.path.join(d, name + ".csv"), "w") as f:
        f.write("id,k,k2,v\n")
        buf = []
        for i in range(N):
            k, k2 = keyf(i)
            buf.append("%d,%s,%s,%d\n" % (i, k, k2, rng.randrange(100000)))
            if len(buf) >= 50000:
                f.write("".join(buf)); buf = []
        f.write("".join(buf))
perm = list(range(N)); rng.shuffle(perm)
write("uuid", lambda i: ("%032x" % rng.getrandbits(128), "x"))
write("prefix", lambda i: ("customer-%010d" % perm[i], "x"))
write("sorted", lambda i: ("key%07d" % i, "x"))
w = [1.0 / (r + 1) ** 1.1 for r in range(200000)]
cum = []
t = 0
for x in w:
    t += x; cum.append(t)
import bisect
write("zipf", lambda i: ("u%d" % min(bisect.bisect_left(cum, rng.random() * t), 199999), "x"))
write("twocol", lambda i: ("r%d" % rng.randrange(50), "p%d" % rng.randrange(20000)))
