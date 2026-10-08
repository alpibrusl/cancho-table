#!/usr/bin/env python3
"""A corpus of plans over the benchmark and adversarial files: the md5 of every output, one line per plan.

    python3 scripts/corpus.py > before.md5 ; (change) ; python3 scripts/corpus.py > after.md5 ; diff before.md5 after.md5

The gate of the cell-cost rounds (docs/history.md): a change to how a cell is found or read must leave every line
unchanged. Files are the ones scripts/bench.py and scripts/adversarial.py generate (run them first); plans are
run sequentially and with threads and tiny ranges, so the parallel path and its re-reads are in the corpus.
"""
import hashlib
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE = pathlib.Path(os.environ.get("TABLE", ROOT / "build" / "table"))
F = {
    "f1": ROOT / "build" / "bench" / "data.csv",
    "f2": ROOT / "build" / "adv" / "f2.csv",
    "q": ROOT / "build" / "adv" / "quoted.csv",
    "long": ROOT / "build" / "adv" / "long.csv",
    "wide": ROOT / "build" / "adv" / "wide.csv",
    "num": ROOT / "build" / "num" / "data.csv",     # scripts/bench_numbers.py's file: the 17-digit column `ratio` (stage N3b)
}
BIG = ["--max-groups", "1000000", "--max-state-bytes", "1073741824", "--max-distinct", "10000000"]
PLANS = {
    "f1": [
        ["--select", "status,bytes"], ["--select", "note,id"], ["--select", "path"], ["--where", "id:int>=0"],
        ["--where", "status=404 and bytes:int>50000"], ["--where", "bytes:int>10000"], ["--where", "status!=200"],
        ["--where", "note='a,b 7'"], ["--where", "path contains /p/1"], ["--where", "status in (404,500) and bytes:int<=500"],
        ["--group", "status"], ["--group", "status", "--agg", "sum:bytes"], ["--group", "status", "--agg", "min:bytes,max:bytes"],
        ["--group", "status,note"], ["--group", "status", "--agg", "distinct:path"],["--group","status","--agg","count,sum:bytes"],
        ["--where", "bytes:int>50000", "--group", "status", "--agg", "sum:bytes", "--sort", "-sum:bytes"],
        ["--select", "id,bytes", "--where", "status=200", "--limit", "50", "--from", "100"],
    ],
    "f2": [
        ["--group", "k100k"], ["--group", "k100k", "--agg", "sum:v"], ["--group", "s", "--agg", "distinct:id"] + BIG,
        ["--where", "v:int>50000", "--select", "id,k1m"], ["--group", "k1m"] + BIG,
    ],
    "q": [["--select", "status,note"], ["--where", "status=404 and bytes:int>50000"], ["--group", "status"], ["--group", "status", "--agg", "sum:bytes"]],
    "long": [["--select", "id,g"], ["--group", "g"], ["--select", "text", "--where", "g=g3"]],
    "num": [
        ["--group", "status", "--agg", "min:ratio:float,max:ratio:float"], ["--group", "status", "--agg", "sum:ratio:float,count"], ["--group", "status", "--agg", "mean:ratio:float"],
        ["--where", "status=404 and ratio:float>=500", "--select", "id,ratio"], ["--where", "ratio:float<1", "--select", "id"], ["--group", "status", "--agg", "min:price:float,max:price:float,sum:price:float"],
        ["--group", "status", "--agg", "distinct:ratio:float"] + BIG, ["--order-by", "-ratio:float", "--select", "id,ratio", "--top", "100"],
    ],
    "wide": [["--select", "c5,c100,c199"], ["--where", "c7:int>500", "--select", "c1,c199"], ["--group", "c3"] + BIG],
}
VARIANTS = [["--threads", "1"], ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "65536"], ["--threads", "3", "--parallel-min-bytes", "0", "--chunk-bytes", "65536"]]


def main():
    for key, plans in PLANS.items():
        path = F[key]
        if not path.exists():
            print("missing", path, file=sys.stderr)
            sys.exit(2)
        for plan in plans:
            for fmt in (["--format", "csv"], []):
                for var in VARIANTS:
                    if fmt == [] and var[1] != "1" and "--limit" not in plan:
                        continue
                    extra = [] if "--limit" in plan or fmt == ["--format", "csv"] else ["--limit", "1000"]
                    if fmt == [] and "--limit" not in plan:
                        extra = ["--limit", "1000"]
                    argv = [str(TABLE), "--root", str(path.parent), *plan, *fmt, *extra, *var, path.name]
                    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    h = hashlib.md5(r.stdout + r.stderr + bytes([r.returncode])).hexdigest()
                    print(h, r.returncode, key, " ".join(plan + fmt + extra + var))


main()
