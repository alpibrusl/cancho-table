#!/usr/bin/env python3
"""fuzz.py: random files and random groupings; `--threads N` (tiny ranges, csv output) must equal the one-thread answer of the same binary,
byte for byte (stdout, stderr, status), and the one-thread answer must equal the base binary's.

    fuzz.py --bin PATH --base PATH [--cases 300] [--seed 1]
"""
import argparse, os, random, subprocess, sys, tempfile

def run(argv):
    p = subprocess.run(argv, capture_output=True)
    return p.returncode, p.stdout, p.stderr

def cell(rng, kind):
    if kind == "key":
        r = rng.random()
        if r < 0.5:
            return "k%d" % rng.randrange(rng.choice([3, 20, 200, 2000]))
        if r < 0.6:
            return ""
        if r < 0.7:
            return '"q,%d"' % rng.randrange(30)
        if r < 0.78:
            return '"a ""b"" %d"' % rng.randrange(10)
        if r < 0.84:
            return '"line\nbreak %d"' % rng.randrange(10)
        if r < 0.9:
            return "k%d" % rng.randrange(5) + "é"
        return "z" * rng.randrange(1, 30) + "%d" % rng.randrange(40)
    if kind == "int":
        r = rng.random()
        if r < 0.02:
            return rng.choice(["x", "", "1.5", "99999999999999999999"])
        if r < 0.1:
            return rng.choice(["9223372036854775807", "-9223372036854775808", "4611686018427387904"])
        return str(rng.randrange(-1000, 100000))
    if kind == "text":
        return "t%d" % rng.randrange(rng.choice([2, 50, 5000]))

def make(rng, n, bad):
    lines = ["a,b,c,d"]
    for i in range(n):
        row = [cell(rng, "key"), cell(rng, "key") if rng.random() < 0.5 else "k%d" % rng.randrange(4), cell(rng, "int"), cell(rng, "text")]
        if not bad and row[2] in ("x", "", "1.5", "99999999999999999999"):
            row[2] = str(rng.randrange(1000))
        if bad and rng.random() < 0.01:
            row = row[:2]
        lines.append(",".join(row))
    return ("\n".join(lines) + "\n").encode()

def plan(rng):
    g = rng.choice([["a"], ["b"], ["a", "b"], ["d"], [], ["c:int"], ["a", "c:int"], ["c:dec(2)"]])
    aggs = rng.sample(["count", "sum:c", "min:c", "max:c", "distinct:d", "distinct:a", "mean:c", "distinct:c:int", "sum:c:float", "mean:c:float", "min:c:float"], rng.randrange(0, 4))
    args = []
    if g:
        args += ["--group", ",".join(g)]
    if aggs:
        args += ["--agg", ",".join(aggs)]
    if not g and not aggs:
        args += ["--group", "a"]
    if rng.random() < 0.2:
        args += ["--where", "b != k1"]
    r = rng.random()
    if r < 0.05:
        args += ["--max-groups", str(rng.choice([1, 3, 10, 100]))]
    elif r < 0.1:
        args += ["--max-distinct", str(rng.choice([1, 10, 100]))]
    elif r < 0.15:
        args += ["--max-state-bytes", str(rng.choice([20, 200, 2000]))]
    elif r < 0.18:
        args += ["--max-rows", str(rng.choice([5, 100, 300]))]
    elif r < 0.22:
        args += ["--sort", rng.choice(["-count", "count", "-a"])]
    elif r < 0.25:
        args += ["--top", "3"]
    r = rng.random()
    if r < 0.15:
        args += ["--limit", str(rng.choice([1, 3, 10, 100])), "--from", str(rng.choice([0, 0, 2, 50]))]
    elif r < 0.2:
        args += ["--sort", "-sum:c"] if any(x.startswith("sum:c") for x in aggs) else []
    return args + (["--format", "csv"] if rng.random() < 0.5 else [])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    d = tempfile.mkdtemp()
    bad = 0
    used = 0
    total = 0
    for case in range(a.cases):
        n = rng.choice([0, 1, 5, 40, 300, 3000])
        data = make(rng, n, rng.random() < 0.25)
        with open(os.path.join(d, "t.csv"), "wb") as f:
            f.write(data)
        args = plan(rng)
        base = run([a.base, "--root", d, *args, "--threads", "1", "t.csv"])
        seq = run([a.bin, "--root", d, *args, "--threads", "1", "t.csv"])
        if seq != base:
            print("SEQ DIFFERS from base", args, len(data)); bad += 1; open("/tmp/fuzz_fail_%d.csv" % case, "wb").write(data); continue
        for t in (2, 3, 5, 16):
            for chunk in (1, 64, 1000, 100000):
                got = run([a.bin, "--root", d, *args, "--threads", str(t), "--chunk-bytes", str(chunk), "--parallel-min-bytes", "0", "t.csv"])
                total += 1
                if b"PART\n" in got[1]:
                    used += 1
                    got = (got[0], got[1].replace(b"PART\n", b""), got[2])
                import re
                norm = lambda x: re.sub(rb'"--threads","[0-9]+","--chunk-bytes","[0-9]+","--parallel-min-bytes","0",', b"", x)
                if (got[0], norm(got[1]), got[2]) != (base[0], norm(base[1]), base[2]):
                    print("DIFFERS threads", t, "chunk", chunk, args, "rows", n)
                    print("  want", base[0], base[1][:200], base[2][:200])
                    print("  got ", got[0], got[1][:200], got[2][:200])
                    open("/tmp/fuzz_fail_%d.csv" % case, "wb").write(data); bad += 1
                    break
            else:
                continue
            break
    print("cases", a.cases, "bad", bad, "threaded runs", total, "partitioned", used)
    sys.exit(1 if bad else 0)

main()
