#!/usr/bin/env python3
"""Gate Q1-EQ over the corpus of `scripts/corpus.py` (docs/query.md section 8): every plan of the corpus, written as flags and written as `--query`, gives the same bytes.

    python3 scripts/query_corpus.py [--table build/table] [--require f1,f2,q,long,wide,num] [--quiet]

For each plan of `scripts/corpus.py` (the same files, the same `--format`, `--limit`, `--threads`, `--chunk-bytes` variants, so the parallel path and its re-reads are in it) the flag
spelling is run and the query spelling is run, and the exit status, standard output and standard error must be byte-identical (the md5 of the three is what is compared, and
printed with `--verbose`). The plans are the corpus's own; the query of each is written below by hand (QUERIES: one per plan, in the words of docs/query.md), not made from the flags,
so that a translation that is wrong in the same way as the flags are read cannot agree with itself. The few flags a query has no form for (`--top`, `--max-groups`, `--max-state-bytes`,
`--max-distinct`) stay flags beside it. Files that are not there (the corpus needs the benchmark files: scripts/bench.py, scripts/adversarial.py, scripts/bench_numbers.py) are
reported and fail the gate when named in --require, and are skipped otherwise (CI has the small benchmark file only).

Exit 0 when every run agreed, 1 when one did not, 2 when a required file is missing or the number of plans is not what the corpus has.
"""
import argparse
import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import corpus  # noqa: E402

# plan (as the corpus writes it, the part before the shared flags) -> the query
QUERIES = {
    "f1": {
        ("--select", "status,bytes"): "select status, bytes",
        ("--select", "note,id"): "select note, id",
        ("--select", "path"): "select path",
        ("--where", "id:int>=0"): "select * where id::int >= 0",
        ("--where", "status=404 and bytes:int>50000"): "select * where status = 404 and bytes::int > 50000",
        ("--where", "bytes:int>10000"): "select * where bytes::int > 10000",
        ("--where", "status!=200"): "select * where status != 200",
        ("--where", "note='a,b 7'"): "select * where note = 'a,b 7'",
        ("--where", "path contains /p/1"): "select * where path contains '/p/1'",
        ("--where", "status in (404,500) and bytes:int<=500"): "select * where status in (404, 500) and bytes::int <= 500",
        ("--group", "status"): "select status, count(*) group by status",
        ("--group", "status", "--agg", "sum:bytes"): "select status, sum(bytes) group by status",
        ("--group", "status", "--agg", "min:bytes,max:bytes"): "select status, min(bytes), max(bytes) group by status",
        ("--group", "status,note"): "select status, note, count(*) group by status, note",
        ("--group", "status", "--agg", "distinct:path"): "select status, count(distinct path) group by status",
        ("--group", "status", "--agg", "count,sum:bytes"): "select status, count(*), sum(bytes) group by status",
        ("--where", "bytes:int>50000", "--group", "status", "--agg", "sum:bytes", "--sort", "-sum:bytes"):
            "select status, sum(bytes) where bytes::int > 50000 group by status order by sum(bytes) desc",
        ("--select", "id,bytes", "--where", "status=200", "--limit", "50", "--from", "100"): "select id, bytes where status = 200 limit 50 offset 100",
    },
    "f2": {
        ("--group", "k100k"): "select k100k, count(*) group by k100k",
        ("--group", "k100k", "--agg", "sum:v"): "select k100k, sum(v) group by k100k",
        ("--group", "s", "--agg", "distinct:id"): "select s, count(distinct id) group by s",
        ("--where", "v:int>50000", "--select", "id,k1m"): "select id, k1m where v::int > 50000",
        ("--group", "k1m"): "select k1m, count(*) group by k1m",
    },
    "q": {
        ("--select", "status,note"): "select status, note",
        ("--where", "status=404 and bytes:int>50000"): "select * where status = 404 and bytes::int > 50000",
        ("--group", "status"): "select status, count(*) group by status",
        ("--group", "status", "--agg", "sum:bytes"): "select status, sum(bytes) group by status",
    },
    "long": {
        ("--select", "id,g"): "select id, g",
        ("--group", "g"): "select g, count(*) group by g",
        ("--select", "text", "--where", "g=g3"): "select text where g = 'g3'",
    },
    "num": {
        ("--group", "status", "--agg", "min:ratio:float,max:ratio:float"): "select status, min(ratio::float), max(ratio::float) group by status",
        ("--group", "status", "--agg", "sum:ratio:float,count"): "select status, sum(ratio::float), count(*) group by status",
        ("--group", "status", "--agg", "mean:ratio:float"): "select status, mean(ratio::float) group by status",
        ("--where", "status=404 and ratio:float>=500", "--select", "id,ratio"): "select id, ratio where status = 404 and ratio::float >= 500",
        ("--where", "ratio:float<1", "--select", "id"): "select id where ratio::float < 1",
        ("--group", "status", "--agg", "min:price:float,max:price:float,sum:price:float"): "select status, min(price::float), max(price::float), sum(price::float) group by status",
        ("--group", "status", "--agg", "distinct:ratio:float"): "select status, count(distinct ratio::float) group by status",
        ("--order-by", "-ratio:float", "--select", "id,ratio"): "select id, ratio order by ratio::float desc",
    },
    "wide": {
        ("--select", "c5,c100,c199"): "select c5, c100, c199",
        ("--where", "c7:int>500", "--select", "c1,c199"): "select c1, c199 where c7::int > 500",
        ("--group", "c3"): "select c3, count(*) group by c3",
    },
}
# the flags a query has no form for, kept as flags beside it
KEPT = {"--top", "--max-groups", "--max-state-bytes", "--max-distinct"}


def split(plan):
    """(the part of the plan the query says, the flags that stay flags) -- the plan is a flat list; a kept flag takes the value after it."""
    said, kept, i = [], [], 0
    while i < len(plan):
        if plan[i] in KEPT:
            kept += plan[i:i + 2]
            i += 2
        else:
            said.append(plan[i])
            i += 1
    return tuple(said), kept


def run(argv):
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.returncode, r.stdout, r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(corpus.TABLE))
    ap.add_argument("--require", default="", help="comma-separated keys of the corpus's files that must be there (f1,f2,q,long,wide,num)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    table = pathlib.Path(a.table)
    need = set(filter(None, a.require.split(",")))
    plans = ran = differ = skipped = 0
    for key, plan_list in corpus.PLANS.items():
        path = corpus.F[key]
        if not path.exists():
            print("missing %s (%s)" % (path, key), file=sys.stderr)
            if key in need:
                return 2
            skipped += sum(1 for _ in plan_list)
            continue
        for plan in plan_list:
            said, kept = split(plan)
            query = QUERIES[key].get(said)
            if query is None:
                print("no query written for %s %s" % (key, plan), file=sys.stderr)
                return 2
            query = with_source(query, path.name)
            for fmt in (["--format", "csv"], []):
                for var in corpus.VARIANTS:
                    if fmt == [] and var[1] != "1" and "--limit" not in plan:
                        continue
                    extra = [] if "--limit" in plan or fmt == ["--format", "csv"] else ["--limit", "1000"]
                    if fmt == [] and "--limit" not in plan:
                        extra = ["--limit", "1000"]
                    base = ["--root", str(path.parent)]
                    flags = [str(table), *base, *plan, *fmt, *extra, *var, path.name]
                    # the query carries its own page when the plan does; the extra --limit is a flag beside a query that has none
                    qflags = [str(table), *base, "--query", query, *kept, *fmt, *extra, *var]
                    x, y = run(flags), run(qflags)
                    ran += 1
                    ok = x == y
                    if a.verbose or not ok:
                        h = lambda r: hashlib.md5(r[1] + r[2] + bytes([r[0]])).hexdigest()
                        print("%s %s %s %s %s" % ("same" if ok else "DIFFERENT", h(x), h(y), key, " ".join(plan + fmt + extra + var)))
                    if not ok:
                        differ += 1
                        print("   query: %s" % query, file=sys.stderr)
                        print("   flags: %r\n   query: %r" % (x[1][:200] + x[2][:200], y[1][:200] + y[2][:200]), file=sys.stderr)
            plans += 1
    print("query_corpus: %d plans, %d runs (flags and query), %d different, %d plans skipped for a missing file" % (plans, ran, differ, skipped))
    if not plans:
        return 2
    return 1 if differ else 0


def with_source(q, name):
    """The query with its source clause: `from 'name'` goes after the select list, before where, group by, order by and limit."""
    clause = " from '%s'" % name
    for word in (" where ", " group by ", " order by ", " limit "):
        at = q.find(word)
        if at >= 0:
            return q[:at] + clause + q[at:]
    return q + clause


if __name__ == "__main__":
    sys.exit(main())
