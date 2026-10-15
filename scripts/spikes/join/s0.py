#!/usr/bin/env python3
"""SPIKE S0 (docs/joins.md section 15): can a worker thread read a table the parent built?

    python3 s0.py [--cancho PATH] [--work DIR] [--runs 5] [--json out.json]

1. builds the programs of s0/ with the compiler (default: the pinned one, cancho.toml's revision, at ../cancho-compiler-e47d647/target/release/cancho or $CANCHO);
2. compiles the negative programs s0/neg/n*.cho and prints, for each, the first compiler error (each must fail, or the run stops: a refusal that went away is a result);
3. runs s0_shared (one `&r Shared` as the whole payload of 1, 2, 4, 8 threads), s0_copy (option (a): a deep copy of the table in each thread's job) and s0_spawn (the cost of a
   spawn and a join), each --runs times as separate processes, with the peak resident set from `/usr/bin/time -l`; every answer is checked inside the program against the parent's own
   sequential probe (the `ok` column must equal the program's repetitions)."""
import argparse
import json
import os
import pathlib
import platform
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py  # noqa: E402

GMAP = HERE.parents[2] / "tools" / "table" / "gmap.cho"
COMMON = [str(HERE / "s0" / "s0common.cho"), str(HERE / "jtab.cho"), str(GMAP)]


def build(cancho, src, out):
    p = subprocess.run([cancho, "build", str(src)] + COMMON + ["--std", "-o", str(out)], capture_output=True, text=True, timeout=600)
    return p


def timed(binary, argv):
    p = subprocess.run(["/usr/bin/time", "-l", str(binary)] + argv, capture_output=True, text=True, timeout=600)
    if p.returncode:
        sys.exit(f"{binary} {argv}: exit {p.returncode}: {p.stderr[-300:]}")
    rss = int(re.search(r"(\d+)\s+maximum resident set size", p.stderr).group(1))
    return [int(x) for x in p.stdout.split()], rss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cancho", default=os.environ.get("CANCHO", str(HERE.parents[2].parent / "cancho-compiler-e47d647" / "target" / "release" / "cancho")))
    ap.add_argument("--work", default="/tmp/joins-s0")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    work = pathlib.Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    ver = subprocess.run([a.cancho, "--version"], capture_output=True, text=True).stdout.strip()
    load0 = os.getloadavg()
    res = {"compiler": ver, "machine": platform.platform() + " " + platform.machine(), "cores": os.cpu_count(), "load_before": load0, "runs": a.runs, "reps": a.reps}
    print(f"compiler {ver}\nmachine {res['machine']}, {os.cpu_count()} cores; load average before {load0}\n")

    print("negative programs (each must fail to compile):")
    res["negative"] = {}
    for src in sorted((HERE / "s0" / "neg").glob("n*.cho")):
        p = build(a.cancho, src, work / "neg_out")
        if p.returncode == 0:
            sys.exit(f"{src.name} compiled: the refusal it records is gone")
        first = next((ln for ln in p.stderr.splitlines() if "error" in ln), "")
        msg = first.split("error: ", 1)[-1]
        res["negative"][src.name] = msg
        print(f"  {src.name:32s} {msg}")

    bins = {}
    for name in ("s0_shared", "s0_copy", "s0_spawn"):
        p = build(a.cancho, HERE / "s0" / (name + ".cho"), work / name)
        if p.returncode:
            sys.exit(f"cannot build {name}: {p.stderr[-600:]}")
        bins[name] = work / name

    n_probe = 4_000_000
    print(f"\nprobe of {n_probe} keys (half in the build, each build key once), the minimum of {a.reps} repetitions inside a process and then of {a.runs} processes; times in ms; RSS in MB (peak, of the process, build included)")
    print(f"{'build rows':>10s} {'threads':>7s} | {'shared: probe ms':>16s} {'speedup':>8s} {'rss':>6s} | {'copy: total ms':>14s} {'speedup':>8s} {'rss':>6s}")
    res["probe"] = {}
    for n_build in (100_000, 1_000_000):
        base_sh = base_cp = None
        for t in (1, 2, 4, 8):
            sh = [timed(bins["s0_shared"], [str(n_build), str(n_probe), str(t), str(a.reps)]) for _ in range(a.runs)]
            cp = [timed(bins["s0_copy"], [str(n_build), str(n_probe), str(t), str(a.reps)]) for _ in range(a.runs)]
            for out, _ in sh + cp:
                if out[-1] != a.reps:
                    sys.exit(f"a worker's answer differed from the sequential probe: {out}")
            sh_ms = min(o[2] for o, _ in sh)
            seq_ms = min(o[1] for o, _ in sh)
            cp_ms = min(o[2] for o, _ in cp)
            build_ms = min(o[0] for o, _ in sh)
            clone_ms = min(o[1] for o, _ in cp)
            if t == 1:
                base_sh, base_cp = sh_ms, cp_ms
            sh_rss = min(r for _, r in sh)
            cp_rss = min(r for _, r in cp)
            res["probe"][f"{n_build}/{t}"] = {"build_ms": build_ms, "seq_probe_ms": seq_ms, "shared_ms": sh_ms, "shared_all": [o[2] for o, _ in sh], "shared_rss": sh_rss, "copy_total_ms": cp_ms, "copy_all": [o[2] for o, _ in cp], "copy_rss": cp_rss, "clone_one_ms": clone_ms, "matches": sh[0][0][3]}
            print(f"{n_build:10d} {t:7d} | {sh_ms:16d} {base_sh / sh_ms:8.2f} {sh_rss / 1e6:6.0f} | {cp_ms:14d} {base_cp / cp_ms:8.2f} {cp_rss / 1e6:6.0f}   (parent's sequential probe {seq_ms} ms, build {build_ms} ms, one clone {clone_ms} ms)")
    sp = [timed(bins["s0_spawn"], ["100000"])[0] for _ in range(a.runs)]
    per = min(o[0] for o in sp) / 100000 * 1000
    res["spawn_join_us"] = per
    print(f"\none spawn and join of a worker that answers one integer: {per:.2f} us (100000 in a row, the minimum of {a.runs} runs)")
    res["load_after"] = os.getloadavg()
    print(f"load average after {res['load_after']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, indent=1) + "\n")


main()
