"""SPIKE (docs/readers.md): the per-record cost of each mode of jsonl_scan, by the difference of ROUNDS (11) rounds and 1 round
(the load of the file through `getchar` is the same in both and drops out), best of REPS (3) each; and the load alone.

    S=DIR_WITH_w_and_data [BIN=jsonl_scan_x] jsonl_time.py MODE:FILE ... [--threads N]      (env REPS, ROUNDS)
"""
import os, subprocess, sys, time
S = os.environ["S"]
REPS = int(os.environ.get('REPS', '3'))
ROUNDS = int(os.environ.get('ROUNDS', '11'))
def run(mode, rounds, f, threads=1):
    best = 9e9
    for _ in range(REPS):
        t = time.time()
        r = subprocess.run([S + "/w/" + os.environ.get("BIN","jsonl_scan"), mode, str(rounds)] + ([str(threads)] if threads > 1 else []), stdin=open(S + "/data/" + f), capture_output=True)
        best = min(best, time.time() - t)
        out = r.stdout.decode().strip()
    return best, out
threads = 1
args = sys.argv[1:]
if "--threads" in args:
    i = args.index("--threads"); threads = int(args[i + 1]); del args[i:i + 2]
for a in args:
    mode, f = a.split(":")
    lines = sum(1 for _ in open(S + "/data/" + f, "rb"))
    size = os.path.getsize(S + "/data/" + f)
    if mode == "ingest":
        t, out = run("ingest", 1, f)
        print(f"{mode:9s} {f:14s} {t:.3f} s for {size/1e6:.1f} MB: {size/1e6/t:.0f} MB/s")
        continue
    a1, o1 = run(mode, 1, f, threads)
    a2, o2 = run(mode, ROUNDS, f, threads)
    per = (a2 - a1) / (ROUNDS - 1)
    print(f"{mode:9s} {f:14s} threads {threads}: {per*1000:7.1f} ms per round of {lines} lines = {per/lines*1e9:6.1f} ns/line, {size/1e6/per:7.0f} MB/s   (answer {o1})")
