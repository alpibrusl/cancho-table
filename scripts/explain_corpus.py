#!/usr/bin/env python3
"""Gate X1 over the corpus of `scripts/corpus.py` (docs/query.md section 15): the `--explain` of every plan of the corpus, written as flags and written as `--query`, is the same bytes.

    python3 scripts/explain_corpus.py [--table build/table] [--require f1,f2,q,long,wide,num] [--quiet]

For each of the 166 runs of the corpus (the same files, the same `--format`, `--limit`, `--threads`, `--chunk-bytes` variants) the flag spelling is explained and the query spelling is
explained (the queries are `scripts/query_corpus.py`'s, written by hand), and the exit status, the standard output and the standard error must be byte-identical. Each explanation must
also be an answer (exit 0, one `explain` document) and name the columns the real run's answer has (a json run: `data.columns`), which keeps the gate from passing by two refusals that
agree. Files that are not there are reported and fail the gate when named in --require.

Exit 0 when every pair agreed, 1 when one did not, 2 when a required file is missing or the number of runs is not what the corpus has.
"""
import argparse
import json
import pathlib
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import corpus  # noqa: E402
import query_corpus  # noqa: E402


def run(argv):
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.returncode, r.stdout, r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(corpus.TABLE))
    ap.add_argument("--require", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    table = pathlib.Path(a.table)
    need = set(filter(None, a.require.split(",")))
    ran = differ = skipped = columns = 0
    for key, plan_list in corpus.PLANS.items():
        path = corpus.F[key]
        if not path.exists():
            print("missing %s (%s)" % (path, key), file=sys.stderr)
            if key in need:
                return 2
            skipped += sum(1 for _ in plan_list)
            continue
        for plan in plan_list:
            said, kept = query_corpus.split(plan)
            query = query_corpus.with_source(query_corpus.QUERIES[key][said], path.name)
            for fmt in (["--format", "csv"], []):
                for var in corpus.VARIANTS:
                    if fmt == [] and var[1] != "1" and "--limit" not in plan:
                        continue
                    extra = [] if "--limit" in plan or fmt == ["--format", "csv"] else ["--limit", "1000"]
                    if fmt == [] and "--limit" not in plan:
                        extra = ["--limit", "1000"]
                    base = ["--root", str(path.parent)]
                    flags = [str(table), "--explain", *base, *plan, *fmt, *extra, *var, path.name]
                    qflags = [str(table), "--explain", *base, "--query", query, *kept, *fmt, *extra, *var]
                    x, y = run(flags), run(qflags)
                    ran += 1
                    good = x == y and x[0] == 0 and x[2] == b""
                    if good:
                        d = json.loads(x[1])
                        good = list(d["data"]) == ["explain"] and d["data"]["explain"]["source"]["header_read"]
                    if good:
                        # the columns of the real answer: a json run's data.columns, a csv run's header line (the run is stopped after it)
                        want = d["data"]["explain"]["output"]["columns"]
                        argv = [str(table), *base, *plan, *fmt, *extra, *var, path.name]
                        if fmt == []:
                            real = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            rd = json.loads(real.stdout)
                            if rd.get("ok"):
                                columns += 1
                                good = rd["data"]["columns"] == want
                        else:
                            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            first = proc.stdout.readline().decode().rstrip("\n")
                            proc.kill()
                            proc.wait()
                            columns += 1
                            good = first.split(",") == want
                    if not good:
                        differ += 1
                        print("DIFFERENT %s %s\n  flags: %r\n  query: %r" % (key, plan, x[:2] + (x[2][:200],), y[:2] + (y[2][:200],)), file=sys.stderr)
    if not a.quiet or differ:
        print("explain corpus: %d runs explained as flags and as a query, %d different, %d plans skipped (file missing), %d compared with the columns of the real answer (json runs: data.columns; csv runs: the header line)" % (ran, differ, skipped, columns))
    if not ran:
        return 2
    if need >= set(corpus.PLANS) and ran != 166:
        print("expected 166 runs, ran %d" % ran, file=sys.stderr)
        return 2
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
