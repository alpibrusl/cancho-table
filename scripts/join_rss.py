#!/usr/bin/env python3
"""J1 (docs/joins.md section 16): the peak resident set of the REAL join build against its accounted bytes, on the eight files of the spike of section 15.1.

    python3 scripts/join_rss.py --data DIR [--bin build/table] [--runs 5] [--json out.json]
    python3 scripts/spikes/join/gen.py DIR       # makes the eight files (seed 7)

For each build file: the accounted bytes by the formula of docs/joins.md 6.2, computed here from the file by another program (`tests/conformance/join_ref.py`); the program's own count,
read from its refusal at one byte below (`detail.held` plus the cost of the last row must be the formula, or the run stops); then, as the minimum of `--runs` runs, the peak resident set
the kernel reports (`/usr/bin/time -l` on macOS, bytes; `-v` on Linux, kilobytes) of `table --join FILE` with a probe of one row, at `--max-state-bytes` equal to the formula (the build is
held whole), and of the same call with a build of one row (the floor: the process, the reader's chunks, a table of the smallest size). The ratios are published as they are, with the
conditions printed: nothing is claimed of them. The spike's figures were 1.65 to 2.26 (grown by doubling), 0.99 to 1.29 pre-sized; the real build's are in docs/joins.md section 16.3.
"""
import argparse
import json
import os
import pathlib
import platform
import re
import sys
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
import join_ref as J  # noqa: E402

FILES = ["int_100k", "int_1m", "text_100k", "text_1m", "text_pow2", "text_pow2p1", "wide_pow2", "dup_100k"]


def timed(argv):
    if sys.platform == "darwin":
        cmd = ["/usr/bin/time", "-l"] + argv
    else:
        cmd = ["/usr/bin/time", "-v"] + argv
    p = subprocess.run(cmd, capture_output=True, timeout=1200)
    err = p.stderr.decode()
    if sys.platform == "darwin":
        rss = int(re.search(r"(\d+)\s+maximum resident set size", err).group(1))
    else:
        rss = int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", err).group(1)) * 1024
    return p.returncode, rss, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--bin", default=str(ROOT / "build" / "table"))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", default="")
    ap.add_argument("--files", default=",".join(FILES))
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    load0 = os.getloadavg()
    print("machine %s %s, %s cores; load average before %s; minimum of %d runs; the real build through the tool's reader\n" % (platform.platform(), platform.machine(), os.cpu_count(), load0, a.runs))
    (data / "probe.csv").write_text("k,x\nnomatch-zzz,1\n")
    (data / "one.csv").write_text("k,a,b,c\nu0,1,2,3\n")
    base = [a.bin, "--root", str(data), "--join-prefix", "r_", "--on", "k=k", "--where", "k = nomatch-zzz-2", "--format", "csv"]
    floors = [timed(base + ["--join", "one.csv", "--max-state-bytes", "1073741824", "probe.csv"])[1] for _ in range(a.runs)]
    floor = min(floors)
    print("floor (the call with a build of one row): %d bytes\n" % floor)
    result = {"machine": platform.platform() + " " + platform.machine(), "cores": os.cpu_count(), "runs": a.runs, "load_before": load0, "floor": floor, "files": {}}
    print("%-12s %9s %13s %13s %7s %7s %9s" % ("file", "rows", "accounted", "peak resident", "ratio", "-floor", "ms"))
    import time
    for name in a.files.split(","):
        path = data / (name + ".csv")
        f = J.accounted(str(path), 0)
        # the program's own count: the refusal one byte below names the count before the last row
        r = subprocess.run([a.bin, "--root", str(data), "--join-prefix", "r_", "--on", "k=k", "--where", "k = nomatch-zzz-2", "--join", path.name, "--max-state-bytes", str(f["held"] - 1), "probe.csv"], capture_output=True)
        d = json.loads(r.stdout)["error"]["detail"]
        if d["held"] != f["before_last"] or d["rows_held"] != f["rows"] - 1:
            sys.exit("%s: the program's count %s is not the formula %s" % (name, d, f))
        rss, ms = [], []
        for _ in range(a.runs):
            t = time.perf_counter()
            code, m, _out = timed(base + ["--join", path.name, "--max-state-bytes", str(f["held"]), "probe.csv"])
            ms.append(1000 * (time.perf_counter() - t))
            if code:
                sys.exit("%s: exit %d at the formula" % (name, code))
            rss.append(m)
        e = {"rows": f["rows"], "distinct": f["distinct"], "accounted": f["held"], "size": f["size"], "rss": rss, "ms": ms}
        result["files"][name] = e
        print("%-12s %9d %13d %13d %7.2f %7.2f %9.0f" % (name, f["rows"], f["held"], min(rss), min(rss) / f["held"], (min(rss) - floor) / f["held"], min(ms)))
    result["load_after"] = os.getloadavg()
    print("\nload average after %s" % (result["load_after"],))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(result, indent=1) + "\n")


main()
