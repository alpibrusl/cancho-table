"""SPIKE benchmark: sortpar (scripts/spikes/sort/sortpar.cho) against `table --order-by`, DuckDB, csvtk and sort, on the
standard 1M-row sort file F2 of scripts/adversarial.py.

    bench_sort.py --table build/table --sortpar ./sortpar --file build/adv/f2.csv [--threads 1,2,4,8,16] [--runs 9]
                  [--cells A1,A2,A4,A3,B1] [--taskset 0-5] [--sorts m,r] [--others duckdb,csvtk,sort] [--outs m]

  A1 text key k1m, A2 integer key u, A4 integer key v with about 10 rows to a value, A3 the first 1000 by u descending,
  B1 two keys: s (4 values) then v:int.

--sorts "" leaves sortpar out (only `table`, `table --threads N` with --tthreads, and the others).

Before anything is timed every contender's whole output is compared with `table`'s (the sequential answer, which the conformance
tests check against Python's sort): sortpar byte for byte; DuckDB, csvtk and sort as "the same rows, in the order of the key"
(their tie order is their own). Times are the minimum of --runs, wall and CPU (user+sys of the children); the peak resident set
from `/usr/bin/time`.
"""
import argparse, hashlib, os, platform, re, resource, shutil, subprocess, sys, time

def md5(argv):
    p = subprocess.run(argv, capture_output=True)
    return (hashlib.md5(p.stdout).hexdigest() if p.returncode == 0 else "rc%d" % p.returncode), p.stdout

def once(argv):
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t = time.perf_counter()
    p = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    w = time.perf_counter() - t
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    return w, (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime), p.returncode

def rss(argv):
    exe = "/usr/bin/time"
    if not os.path.exists(exe):
        return None
    flag = "-l" if platform.system() == "Darwin" else "-v"
    p = subprocess.run([exe, flag] + argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    m = re.search(r"(\d+)\s+maximum resident set size", p.stderr) or re.search(r"Maximum resident set size \(kbytes\): (\d+)", p.stderr)
    if not m:
        return None
    n = int(m.group(1))
    return n / (1024 * 1024) if platform.system() == "Darwin" else n / 1024

# cell: title, table --order-by, sortpar keys, top, DuckDB ORDER BY, csvtk keys, sort command, key columns (for the order check)
CELLS = {
    "A1": ("text key k1m", "k1m", "3:t:a", 0, "k1m", ["-k", "k1m"], "sort -t, -k4,4", [(3, "t")]),
    "A2": ("integer key u", "u:int", "4:i:a", 0, "u", ["-k", "u:n"], "sort -t, -k5,5n", [(4, "i")]),
    "A4": ("integer key v, ties", "v:int", "5:i:a", 0, "v", ["-k", "v:n"], "sort -s -t, -k6,6n", [(5, "i")]),
    "A3": ("first 1000 by u descending", "-u:int", "4:i:d", 1000, "u DESC LIMIT 1000", None, "sort -t, -k5,5nr", [(4, "i")]),
    "B1": ("two keys s, v:int", "s,v:int", "1:t:a,5:i:a", 0, "s, v", ["-k", "s", "-k", "v:n"], "sort -s -t, -k2,2 -k6,6n", [(1, "t"), (5, "i")]),
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--sortpar", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--threads", default="1,2,4,8,16")
    ap.add_argument("--runs", type=int, default=9)
    ap.add_argument("--cells", default="A1,A2,A4")
    ap.add_argument("--taskset", default="")
    ap.add_argument("--tthreads", default="", help="also run `table --threads N` (the psort.cho build of table) for these N")
    ap.add_argument("--sorts", default="m,r")
    ap.add_argument("--outs", default="m")
    ap.add_argument("--others", default="duckdb,csvtk,sort")
    ap.add_argument("--duck-threads", type=int, default=0, help="DuckDB's default ignores a taskset mask (16 threads on 6 cpus): say how many it gets")
    a = ap.parse_args()
    f = os.path.abspath(a.file)
    root, name = os.path.dirname(f), os.path.basename(f)
    ts = ["taskset", "-c", a.taskset] if a.taskset else []
    big = ["--max-state-bytes", "536870912", "--max-sort-rows", "2000000", "--format", "csv"]
    print("%s %s, %s cpus; table %s" % (platform.system(), platform.machine(), os.cpu_count(), a.table))
    for cid in a.cells.split(","):
        title, ob, keys, top, dorder, ckeys, gsort, kcols = CELLS[cid]
        tbl = ts + [a.table, "--root", root] + big + ["--order-by", ob] + (["--top", str(top)] if top else []) + [name]
        want, out = md5(tbl)
        ref = [l for l in out.split(b"\n") if l]
        contenders = {"table -t1": tbl}
        if a.tthreads:
            for t in a.tthreads.split(","):
                contenders["table --threads %s" % t] = tbl[:-1] + ["--threads", t, name]
        for sv in [x for x in a.sorts.split(",") if x]:
            for o in a.outs.split(","):
                for t in a.threads.split(","):
                    label = "sortpar %s%s t=%s" % ({"m": "merge", "r": "radix"}[sv], " reread" if o == "p" else "", t)
                    contenders[label] = ts + [a.sortpar, f, t, "w", sv, keys, str(top), o]
        others = a.others.split(",")
        if "duckdb" in others and shutil.which("duckdb"):
            q = "COPY (SELECT * FROM read_csv('%s') ORDER BY %s) TO '/dev/stdout' (FORMAT csv)" % (f, dorder)
            contenders["duckdb t=1"] = (ts + ["duckdb", "-c", "SET threads=1; " + q.replace("/dev/stdout", "/dev/null")], ts + ["duckdb", "-c", "SET threads=1; " + q])
            pre = "SET threads=%d; " % a.duck_threads if a.duck_threads else ""
            contenders["duckdb default" if not a.duck_threads else "duckdb t=%d" % a.duck_threads] = (ts + ["duckdb", "-c", pre + q.replace("/dev/stdout", "/dev/null")], ts + ["duckdb", "-c", pre + q])
        if "csvtk" in others and shutil.which("csvtk") and ckeys:
            contenders["csvtk sort"] = ts + ["csvtk", "-j", "1", "sort"] + ckeys + [f]
        if "sort" in others:
            gs = "gnusort" if shutil.which("gnusort") else "sort"
            contenders["%s (%s)" % (gs, platform.system())] = ts + ["sh", "-c", "tail -n +2 %s | LC_ALL=C %s" % (f, gsort.replace("sort ", gs + " ", 1)) + (" | head -1000" if top else "")]
        good = {}
        for nme, argv in contenders.items():
            check = argv[1] if isinstance(argv, tuple) else argv
            timed = argv[0] if isinstance(argv, tuple) else argv
            h, o = md5(check)
            same = (h == want)
            if not same and not nme.startswith(("table", "sortpar")) and not h.startswith("rc"):
                lines = [l for l in o.split(b"\n") if l]
                if lines and lines[0] == ref[0]:
                    lines = lines[1:]
                body = ref[1:]
                def key(l):
                    c = l.split(b",")
                    return tuple(c[k] if kd == "t" else int(c[k]) for k, kd in kcols)
                if cid in ("A4", "B1"):
                    same = sorted(lines) == sorted(body) and [key(l) for l in lines] == [key(l) for l in body]
                else:
                    same = lines == body
            if same:
                good[nme] = timed
            else:
                print("  %s: output differs, not timed" % nme)
        print("\n%s %s (%d lines, min of %d; load average at the start %s)" % (cid, title, len(ref), a.runs, " ".join("%.1f" % x for x in os.getloadavg())))
        print("%-26s %8s %8s %9s" % ("contender", "wall s", "cpu s", "RSS MB"))
        names = list(good)
        best = {n: [9e9, 9e9] for n in names}
        for r in range(a.runs):
            for n in names[r % len(names):] + names[:r % len(names)]:
                # a run that exited non-zero is not a time (DuckDB finds /dev/null locked by another DuckDB: docs/gap-sort.md section 1)
                for attempt in range(5):
                    w, c, rc = once(good[n])
                    if rc == 0:
                        break
                    time.sleep(0.2)
                else:
                    sys.exit("%s exited non-zero 5 times in a row" % n)
                if w < best[n][0]:
                    best[n] = [w, c]
        print("(load average at the end of the timing %s)" % " ".join("%.1f" % x for x in os.getloadavg()))
        for n in names:
            m = rss(good[n])
            print("%-26s %8.3f %8.3f %9s" % (n, best[n][0], best[n][1], "%.0f" % m if m else "n/a"))
        sys.stdout.flush()

main()
