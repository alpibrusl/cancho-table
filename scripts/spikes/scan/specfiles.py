#!/usr/bin/env python3
"""Files that make the 'range starts inside quotes' guess wrong, or hard, and a differential run of two binaries over them.

    python3 scripts/spikes/scan/specfiles.py BASE_BIN NEW_BIN [--dir build/adv]

Each file is generated (seeded) under build/adv/spec_*.csv; then for every file, plan, thread count and chunk size the two
binaries must write the same bytes, the same standard error and the same exit status.
"""
import argparse, os, pathlib, random, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
rng = random.Random(5)
SCALE = int(os.environ.get("SCALE", "1"))
PREFIX = os.environ.get("PREFIX", "spec_")


def q(s):
    return '"' + s.replace('"', '""') + '"'


def gen(path, header, rows):
    with open(path, "w", newline="") as f:
        f.write(header + "\n")
        f.write("".join(rows))


def files(d):
    out = {}
    # A: quoted fields that hold lines that look like whole records of the file (3 columns): the guess "outside" is wrong and looks right
    def rec_like():
        return "\n".join("%d,x%d,y%d" % (rng.randrange(1000), rng.randrange(10), rng.randrange(10)) for _ in range(rng.randrange(2, 40)))
    gen(d / (PREFIX + "lookalike.csv"), "id,g,text", ("%d,g%d,%s\n" % (i, i % 7, q(rec_like())) for i in range(30000 * SCALE)))
    # B: the same with CRLF line ends
    with open(d / (PREFIX + "crlf.csv"), "w", newline="") as f:
        f.write("id,g,text\r\n")
        for i in range(30000 * SCALE):
            f.write("%d,g%d,%s\r\n" % (i, i % 7, q(rec_like().replace("\n", "\r\n"))))
    # C: stray quotes in unquoted fields, and quoted newlines: parity of quotes is not the state
    def stray():
        return "".join(rng.choice(['ab"c', 'x', ' y"', 'z']) for _ in range(rng.randrange(1, 4)))
    gen(d / (PREFIX + "stray.csv"), "id,g,text", ("%s,%s,%s\n" % (stray() if i % 5 == 0 else i, "g%d" % (i % 5), q("l1\nl2,%d\nl3" % i) if i % 3 == 0 else stray()) for i in range(40000 * SCALE)))
    # D: a few huge fields (a whole range inside one quoted field) among ordinary rows
    def huge():
        return "\n".join("line %d, with, commas" % k for k in range(rng.randrange(2000, 9000)))
    gen(d / (PREFIX + "huge.csv"), "id,g,text", ("%d,g%d,%s\n" % (i, i % 7, q(huge()) if i % 50 == 0 else "plain %d" % i) for i in range(6000 * SCALE)))
    # E: ordinary file, each 10th row has a short multi-line field
    gen(d / (PREFIX + "few.csv"), "id,g,text", ("%d,g%d,%s\n" % (i, i % 7, q("a\nb") if i % 10 == 0 else "plain %d" % i) for i in range(200000 * SCALE)))
    # F: one column (any line is a record), quoted newlines
    gen(d / (PREFIX + "onecol.csv"), "text", ("%s\n" % (q("l1\nl2\n%d" % i) if i % 2 else "plain %d" % i) for i in range(100000 * SCALE)))
    # G: ragged rows near quoted newlines (the first error in file order is the answer)
    gen(d / (PREFIX + "ragged.csv"), "id,g,text", ("%d,g%d,%s%s\n" % (i, i % 7, q("a\nb\nc,d"), ",extra" if i == 9000 * SCALE or i == 21000 * SCALE else "") for i in range(30000 * SCALE)))
    # H: a bad quote late in the file
    gen(d / (PREFIX + "badq.csv"), "id,g,text", ("%d,g%d,%s\n" % (i, i % 7, q("a\nb") if i != 25000 * SCALE else '"x"y') for i in range(30000 * SCALE)))
    # I: unterminated quote at the end
    with open(d / (PREFIX + "unterm.csv"), "w", newline="") as f:
        f.write("id,g,text\n")
        for i in range(20000 * SCALE):
            f.write("%d,g%d,%s\n" % (i, i % 7, q("a\nb")))
        f.write('1,g,"open\nnever closed\n')
    for p in sorted(d.glob(PREFIX + "*.csv")):
        out[p.stem] = p
    return out


PLANS = [["--select", "id,g"], ["--group", "g"], ["--group", "g", "--agg", "sum:id"], ["--where", "id:int>100", "--select", "g"]]


def run(b, root, plan, f, t, chunk):
    args = [b, "--root", str(root), *plan, "--format", "csv", "--threads", str(t)]
    if chunk:
        args += ["--parallel-min-bytes", "0", "--chunk-bytes", str(chunk)]
    r = subprocess.run(args + [f.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.stdout, r.stderr, r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("new"); ap.add_argument("--dir", default=str(ROOT / "build" / "adv"))
    ap.add_argument("--skip-gen", action="store_true")
    ap.add_argument("--gen-only", action="store_true")
    a = ap.parse_args()
    d = pathlib.Path(a.dir)
    if a.gen_only:
        files(d)
        return
    fs = files(d) if not a.skip_gen else {p.stem: p for p in sorted(d.glob(PREFIX + "*.csv"))}
    bad = 0
    n = 0
    for name, f in fs.items():
        for plan in PLANS:
            if "g" not in " ".join(plan) and name == "spec_onecol":
                continue
            if name == "spec_onecol":
                plan = ["--group", "text"] if plan[0] == "--group" else ["--select", "text"]
                if "--agg" in plan or "--where" in plan:
                    continue
            ref = run(a.base, d, plan, f, 1, 0)
            for t in (2, 4, 8):
                for chunk in (0, 977, 20000, 300000):
                    got = run(a.new, d, plan, f, t, chunk)
                    n += 1
                    if got != ref:
                        bad += 1
                        print("DIFF", name, plan, "t", t, "chunk", chunk, got[2], ref[2], got[1][:80], ref[1][:80])
    print("%d runs, %d differences" % (n, bad))
    sys.exit(1 if bad else 0)


main()
