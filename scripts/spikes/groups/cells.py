#!/usr/bin/env python3
"""cells.py: the adversarial cells B1-B4, I1 (and a few phase probes) on several `table` binaries, interleaved, min of N.

    cells.py [-n N] [--cells B1,B3,I1] [--threads 1,16] [--bins label=path,label=path] [--duck] [--csvtk] [--check]

Per cell and thread count it prints, for each binary, min wall, min cpu, peak RSS. With --check the stdout md5 of every binary
is compared with the first one's (the same bytes). DuckDB (with and without ORDER BY) and csvtk run at the same time.
"""
import argparse, hashlib, os, platform, shlex, subprocess, sys, time, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ADV = os.path.join(ROOT, "build", "adv")
BIG = "--max-groups 1000000 --max-state-bytes 1073741824 --max-distinct 10000000".split()
CELLS = {
    "B1": (["--group", "k100k"], "k100k", "count(*)"),
    "B2": (["--group", "k100k", "--agg", "sum:v"], "k100k", "sum(v)"),
    "B3": (["--group", "k1m"], "k1m", "count(*)"),
    "B4": (["--group", "k1m", "--agg", "sum:v"], "k1m", "sum(v)"),
    "I1": (["--group", "s", "--agg", "distinct:id"], "s", "count(DISTINCT id)"),
    "M1": (["--group", "k100k", "--agg", "count,sum:v,min:v,max:v"], "k100k", "count(*), sum(v), min(v), max(v)"),
    "S1": (["--group", "s"], "s", "count(*)"),
    "EK": (["--group", "k"], "k", "count(*)"),
    "EKS": (["--group", "k", "--agg", "sum:v"], "k", "sum(v)"),
    "ETC": (["--group", "k,k2"], "k, k2", "count(*)"),
    "F1": (["--group", "k100k", "--agg", "sum:v:float,mean:v:float,count"], "k100k", "sum(v::DOUBLE), avg(v::DOUBLE), count(*)"),
    "F2": (["--group", "k1m", "--agg", "sum:v:float"], "k1m", "sum(v::DOUBLE)"),
    "D0": (["--agg", "distinct:id"], "", "count(DISTINCT id)"),
}

def run(cmd, rusage=True):
    t = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, st, ru = os.wait4(p.pid, 0)
    w = time.perf_counter() - t
    div = 1024 if platform.system() == "Linux" else 1024 * 1024
    return w, ru.ru_utime + ru.ru_stime, ru.ru_maxrss / div, os.WEXITSTATUS(st)

def md5(cmd):
    return hashlib.md5(subprocess.run(cmd, capture_output=True).stdout).hexdigest()[:10]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--cells", default="B1,B2,B3,B4,I1")
    ap.add_argument("--threads", default="1")
    ap.add_argument("--bins", required=True)
    ap.add_argument("--duck", action="store_true")
    ap.add_argument("--csvtk", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--file", default="f2.csv")
    ap.add_argument("--extra", default="", help="extra table args, shell-quoted")
    ap.add_argument("--adv", default=ADV, help="directory of f2.csv")
    ap.add_argument("--taskset", default="", help="prefix every command with taskset -c LIST")
    ap.add_argument("--duck-threads", type=int, default=0, help="the thread count of the 'default' DuckDB run (0: its default)")
    ap.add_argument("--mlr", action="store_true")
    a = ap.parse_args()
    bins = [b.split("=", 1) for b in a.bins.split(",")]
    threads = [int(t) for t in a.threads.split(",")]
    adv = a.adv
    path = os.path.join(adv, a.file)
    pre_cmd = ["taskset", "-c", a.taskset] if a.taskset else []
    for cid in a.cells.split(","):
        targs, col, agg = CELLS[cid]
        cont = []
        for label, b in bins:
            for t in threads:
                cont.append(("%s -t%d" % (label, t), pre_cmd + [b, "--root", adv, *targs, "--format", "csv", *BIG, "--threads", str(t), *shlex.split(a.extra), a.file]))
        if a.duck and shutil.which("duckdb"):
            q = "SELECT %s, %s FROM read_csv('%s') GROUP BY %s" % (col, agg, path, col)
            dt = a.duck_threads
            for t in [1] + ([] if threads == [1] else [dt if dt else 0]):
                pre = "SET threads=%d; " % t if t else ""
                name = "-t1" if t == 1 else ("-t%d" % t if t else "default")
                cont.append(("duckdb %s unsorted" % name, pre_cmd + ["duckdb", "-c", "%sCOPY (%s) TO '/dev/null' (FORMAT csv)" % (pre, q)]))
                cont.append(("duckdb %s ORDER BY" % name, pre_cmd + ["duckdb", "-c", "%sCOPY (%s ORDER BY %s) TO '/dev/null' (FORMAT csv)" % (pre, q, col)]))
        if a.csvtk and shutil.which("csvtk") and cid in ("B1", "B3", "B2", "B4"):
            if "sum" in agg:
                cont.append(("csvtk -j1", pre_cmd + ["csvtk", "-j", "1", "summary", "-g", col, "-f", "v:sum", path]))
            else:
                cont.append(("csvtk -j1", pre_cmd + ["csvtk", "-j", "1", "freq", "-f", col, path]))
        if a.mlr and shutil.which("mlr") and cid in ("B1", "B3", "B2", "B4", "I1"):
            if "sum" in agg:
                cont.append(("mlr stats1", pre_cmd + ["mlr", "--icsv", "--ocsv", "stats1", "-a", "sum", "-f", "v", "-g", col, path]))
            elif "DISTINCT" in agg:
                cont.append(("mlr count-distinct", pre_cmd + ["mlr", "--icsv", "--ocsv", "count-distinct", "-f", "s,id", "then", "count-similar", "-g", "s", path]))
            else:
                cont.append(("mlr count-distinct", pre_cmd + ["mlr", "--icsv", "--ocsv", "count-distinct", "-f", col, path]))
        res = {l: [] for l, _ in cont}
        for r in range(a.n):
            k = r % len(cont)
            for l, c in cont[k:] + cont[:k]:
                res[l].append(run(c))
        print("%s  %s" % (cid, " ".join(targs)))
        ref = None
        for l, c in cont:
            allv = res[l]
            v = [x for x in allv if x[3] == 0] or allv
            line = "  %-28s wall %.4f  cpu %.4f  rss %6.1f MB  failed %d/%d" % (l, min(x[0] for x in v), min(x[1] for x in v), max(x[2] for x in v), len(allv) - len([x for x in allv if x[3] == 0]), len(allv))
            if a.check and l.startswith(bins[0][0] + " -t1"):
                ref = ref or md5(c)
            if a.check and l.split(" ")[0] in [b[0] for b in bins]:
                m = md5(c)
                line += "  md5 %s%s" % (m, "" if ref is None or m == ref else "  DIFFERENT")
            print(line)
        sys.stdout.flush()

main()
