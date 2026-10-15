#!/usr/bin/env python3
"""J1 and J2 (docs/joins.md sections 16 and 17): the peak resident set of the REAL join build against its accounted bytes, on the eight files of the spike of section 15.1.

    python3 scripts/join_rss.py --data DIR [--bin build/table] [--runs 5] [--json out.json] [--shape text|int|composite|semi|left|group]

`--shape` (J2) is the join measured: `text` (the default, J1's: one pair of text, inner), `int` (one pair typed :int, on the files whose key is an integer), `composite` (two pairs of text, `k` and `a`), `semi` (one pair of text, the keys alone are held:
44 accounted bytes beyond the key) and `left` (the rows are held as for inner) and, J3, `group` (docs/joins.md section 18: the build is joined with a probe of its own keys, one row for each, and the answer is `--group k --agg count`, so the call holds the build and one group for each distinct key: the accounted bytes
are the build's by the formula of 6.2 and the groups' by `4 + the key's bytes` for each, both computed here, and the program's count of the groups is read from its refusal at one byte below).
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
import join_ref2 as R  # noqa: E402

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
    ap.add_argument("--shape", default="text", choices=["text", "int", "composite", "semi", "left", "group"])
    a = ap.parse_args()
    # the --on, the pairs of the reference, the join type, and the probe's header and row for the shape
    on, pairs, jtype, probe_head, probe_row = {
        "text": ("k=k", [(0, 0, None, False)], "inner", "k,x", "nomatch-zzz,1"),
        "int": ("k=k:int", [(0, 0, "int", False)], "inner", "k,x", "0,1"),
        "composite": ("k=k,x=a", [(0, 0, None, False), (1, 1, None, False)], "inner", "k,x", "nomatch-zzz,1"),
        "semi": ("k=k", [(0, 0, None, False)], "semi", "k,x", "nomatch-zzz,1"),
        "left": ("k=k", [(0, 0, None, False)], "left", "k,x", "nomatch-zzz,1"),
        "group": ("k=k", [(0, 0, None, False)], "inner", "k,x", "nomatch-zzz,1"),
    }[a.shape]
    if a.shape == "int" and a.files == ",".join(FILES):
        a.files = "int_100k,int_1m"
    if a.shape == "group" and a.files == ",".join(FILES):
        a.files = "int_100k,int_1m,text_100k,text_1m,wide_pow2,dup_100k"        # (the files of 2^20 rows have more keys than --max-groups can be)
    data = pathlib.Path(a.data)
    load0 = os.getloadavg()
    print("machine %s %s, %s cores; load average before %s; minimum of %d runs; the real build through the tool's reader\n" % (platform.platform(), platform.machine(), os.cpu_count(), load0, a.runs))
    (data / "probe.csv").write_text("%s\n%s\n" % (probe_head, probe_row))
    (data / "one.csv").write_text("k,a,b,c\n%s,1,2,3\n" % ("5" if a.shape == "int" else "u0"))
    base = [a.bin, "--root", str(data), "--on", on, "--join-type", jtype, "--where", "k = nomatch-zzz-2", "--format", "csv"] + (["--join-prefix", "r_"] if jtype in ("inner", "left") else [])
    if a.shape == "group":
        base = [a.bin, "--root", str(data), "--on", on, "--join-type", jtype, "--group", "k", "--agg", "count", "--max-groups", "1000000", "--format", "csv"] + ["--join-prefix", "r_"]
    floors = [timed(base + ["--join", "one.csv", "--max-state-bytes", "1073741824", "probe.csv"])[1] for _ in range(a.runs)]
    floor = min(floors)
    print("floor (the call with a build of one row): %d bytes\n" % floor)
    result = {"machine": platform.platform() + " " + platform.machine(), "cores": os.cpu_count(), "runs": a.runs, "load_before": load0, "floor": floor, "files": {}}
    print("%-12s %9s %13s %13s %7s %7s %9s" % ("file", "rows", "accounted", "peak resident", "ratio", "-floor", "ms"))
    import time
    for name in a.files.split(","):
        path = data / (name + ".csv")
        if a.shape == "composite" and name in ("wide_pow2",):
            continue                                  # that file has the columns k and pad only
        f = R.accounted(str(path), pairs, jtype)
        probe_name = "probe.csv"
        extra = 0
        if a.shape == "group":
            # a probe of the build's own keys, one row each; the groups are the distinct keys, each 4 bytes of length and the key
            import csv as _csv
            keys = []
            with open(path, newline="") as fh:
                rd = _csv.reader(fh)
                next(rd)
                keys = [row[0] for row in rd]
            probe_name = "probe-" + name + ".csv"
            with open(data / probe_name, "w", newline="") as fh:
                fh.write("k,x\n")
                fh.write("".join(("%s,1\n" % k) if ("," not in k and '"' not in k) else '"%s",1\n' % k.replace('"', '""') for k in keys))
            distinct = []
            seen = set()
            for k in keys:
                if k not in seen:
                    seen.add(k)
                    distinct.append(k)
            costs = [4 + len(k.encode("latin-1")) for k in distinct]
            extra = sum(costs)
            f = dict(f, held=f["held"] + extra)
            low = subprocess.run([x for x in base if x not in ("--format", "csv")] + ["--join", path.name, "--max-state-bytes", str(f["held"] - 1), probe_name], capture_output=True)
            d = json.loads(low.stdout)["error"]["detail"]
            first_seen_order_last = costs[-1]
            if d["holder"] != "groups" or d["build_bytes"] != f["held"] - extra or d["held"] != extra - first_seen_order_last:
                sys.exit("%s: the program's count %s is not the formula (build %d, groups %d, last group %d)" % (name, d, f["held"] - extra, extra, first_seen_order_last))
            f = dict(f, before_fail=None, rows_held_before_fail=None)
        # the program's own count: the refusal one byte below names the count before the row that did not fit
        if a.shape != "group":
            r = subprocess.run([x for x in base if x not in ("--format", "csv")] + ["--join", path.name, "--max-state-bytes", str(f["held"] - 1), "probe.csv"], capture_output=True)
            d = json.loads(r.stdout)["error"]["detail"]
            if d["held"] != f["before_fail"] or d["rows_held"] != f["rows_held_before_fail"]:
                sys.exit("%s: the program's count %s is not the formula %s" % (name, d, f))
        rss, ms = [], []
        for _ in range(a.runs):
            t = time.perf_counter()
            code, m, _out = timed(base + ["--join", path.name, "--max-state-bytes", str(f["held"]), probe_name])
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
