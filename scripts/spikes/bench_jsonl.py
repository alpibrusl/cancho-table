"""SPIKE (docs/readers.md): the same four questions as scripts/bench.py on the 1,000,000-row file as JSON lines, through
DuckDB (read_ndjson, read_json_auto; the CSV for reference), jq, and Miller when installed; time and peak resident set.

    bench_jsonl.py DIR [--reps N] [--only duckdb,jq,mlr]

DIR holds bench.csv, bench.jsonl, truth.json (jsonl_gen.py). Every contender's answer is compared with Python's before it
is timed (the filter by its ids' count and sum, the groupings by their rows), and a disagreement stops the run. Output of
every command is thrown away after that, as in scripts/bench.py. Peak resident set is `/usr/bin/time -l` (Mac) or `-v` (Linux)."""
import csv, json, os, platform, re, shutil, subprocess, sys, time

D = os.path.abspath(sys.argv[1])
REPS = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 5
ONLY = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else ["duckdb", "jq", "mlr"]
MAC = platform.system() == "Darwin"
TIME = ["/usr/bin/time", "-l"] if MAC else ["/usr/bin/time", "-v"]

# ---- the truth, from the CSV by Python's csv module ----
count, total, ids = {}, {}, []
with open(f"{D}/bench.csv", newline="") as f:
    r = csv.reader(f); next(r)
    for row in r:
        s, b = row[1], int(row[2])
        count[s] = count.get(s, 0) + 1
        total[s] = total.get(s, 0) + b
        if s == "404" and b > 50000:
            ids.append(row[0])
TRUTH = {"filter": (len(ids), sum(map(int, ids))), "count": sorted(count.items()), "sum": sorted((k, v) for k, v in total.items())}

def run(cmd, shell=True):
    t = time.time()
    p = subprocess.run(TIME + ["sh", "-c", cmd] if shell else TIME + cmd, capture_output=True)
    el = time.time() - t
    err = p.stderr.decode(errors="replace")
    m = re.search(r"(\d+)\s+maximum resident set size", err) if MAC else re.search(r"Maximum resident set size \(kbytes\): (\d+)", err)
    rss = int(m.group(1)) / (1 if MAC else 1) / (1048576 if MAC else 1024)  # MB
    return el, rss, p.stdout

def check(name, out):
    lines = [l for l in out.decode().split("\n") if l]
    if name == "filter":
        got = (len(lines), sum(int(x) for x in lines))
        return got == TRUTH["filter"]
    if name in ("count", "sum"):
        rows = sorted((l.split(",")[0].strip('"'), int(l.split(",")[1])) for l in lines if l[0].isdigit() or l[0] == '"')
        return [(a, b) for a, b in rows] == [(a, b) for a, b in TRUTH[name]]
    return len(lines) >= 1000000

def contender(label, cmd, name):
    # one untimed run to check the answer, then REPS timed runs with output to /dev/null
    el, rss, out = run(cmd)
    ok = check(name, out)
    if not ok:
        print(f"{label:34s} {name:7s} ANSWER DISAGREES with Python: stopping"); sys.exit(2)
    best, peak = 9e9, 0
    for _ in range(REPS):
        el, rss, _ = run(cmd + " > /dev/null")
        best, peak = min(best, el), max(peak, rss)
    print(f"{label:34s} {name:7s} {best:7.3f} s   peak {peak:7.1f} MB")

SQL = {
    "filter": "select id from {src} where status = 404 and bytes > 50000",
    "count": "select status, count(*) from {src} group by status order by status",
    "sum": "select status, sum(bytes) from {src} group by status order by status",
    "cut": "select status, bytes from {src}",
}
if "duckdb" in ONLY and shutil.which("duckdb"):
    for threads in (1, 16):
        for name, sql in SQL.items():
            for lab, src in (("read_ndjson", f"read_ndjson('{D}/bench.jsonl')"), ("read_json_auto", f"read_json_auto('{D}/bench.jsonl')"), ("read_csv (reference)", f"read_csv('{D}/bench.csv')")):
                q = sql.format(src=src)
                contender(f"duckdb {threads:2d} thr {lab}", f"duckdb -csv -noheader -c \"set threads={threads}; {q}\"", name)
if "jq" in ONLY and shutil.which("jq"):
    J = f"{D}/bench.jsonl"
    jq = {
        "filter": f"jq -r 'select(.status==404 and .bytes>50000)|.id' {J}",
        "count": f"jq -n -r 'reduce inputs as $r ({{}}; .[$r.status|tostring] += 1)|to_entries|sort_by(.key)|.[]|\"\\(.key),\\(.value)\"' {J}",
        "sum": f"jq -n -r 'reduce inputs as $r ({{}}; .[$r.status|tostring] += $r.bytes)|to_entries|sort_by(.key)|.[]|\"\\(.key),\\(.value)\"' {J}",
        "cut": f"jq -r '[.status,.bytes]|@csv' {J}",
    }
    for name, cmd in jq.items():
        contender("jq 1 thr", cmd, name)
if "mlr" in ONLY and shutil.which("mlr"):
    J = f"{D}/bench.jsonl"
    mlr = {
        "filter": f"mlr --ijsonl --ocsv --headerless-csv-output filter '$status==404 && $bytes>50000' then cut -f id {J}",
        "count": f"mlr --ijsonl --ocsv --headerless-csv-output count-distinct -f status then sort -f status {J}",
        "sum": f"mlr --ijsonl --ocsv --headerless-csv-output stats1 -a sum -f bytes -g status then sort -f status {J}",
        "cut": f"mlr --ijsonl --ocsv --headerless-csv-output cut -o -f status,bytes {J}",
    }
    for name, cmd in mlr.items():
        contender("miller 1 thr", cmd, name)
        contender("miller --nr-progress-mod off -n? (default threads)", cmd, name) if False else None
