"""The storyboard of the demo video: the one place the commands are written.

Three things read it and nothing else says what the video shows:

* demo.py plays it (simulated typing, real runs) under `asciinema rec`, see record.sh;
* tests/conformance/test_demo.py runs every command against the current build and compares the transcript with expected.txt, so the video cannot drift from the tool;
* `python3 scripts/demo/storyboard.py --write` rewrites expected.txt from the built binary (review the diff: it is the new truth the video will show).

A command is a line of bash run with `set -o pipefail` in a scratch directory that holds the files `make_files` writes and a `table` on PATH. `exit` is the status the
whole line must end with (with pipefail: the first non-zero of the pipeline), so a refusal that the tool must give is part of the contract and a refusal that is not
expected fails the gate.
"""

import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

ORDERS = """id,customer,status,bytes
1,"Doe, Jane",200,512
2,Acme,404,
3,"Doe, Jane",200,2048
4,Acme,500,128
5,Zed,200,64
"""
CUSTOMERS = "name,tier\nAcme,silver\nZed,bronze\n"
PRICES = "item,price\ntea,0.1\ncup,0.2\nbook,0.3\n"
EVENTS = """{"user":{"name":"ana"},"action":"login","ms":120}
{"user":{"name":"bo"},"action":"view","ms":45}
{"user":{"name":"ana"},"action":"view","ms":80}
"""
NAMES = ["Acme", "Zed", "Initech", "Globex", "Umbra", "Doe, Jane"]


def sales(rows=6000):
    """A few thousand rows from a fixed linear congruential generator: the same bytes on every machine."""
    x = 12345
    out = ["id,customer,status,bytes"]
    for i in range(1, rows + 1):
        x = (x * 1103515245 + 12345) % 2147483648
        name = NAMES[(x >> 8) % len(NAMES)]
        status = (200, 200, 200, 404, 500)[(x >> 16) % 5]
        size = (x >> 4) % 100000
        out.append('%d,%s,%d,%d' % (i, '"%s"' % name if "," in name else name, status, size))
    return "\n".join(out) + "\n"


def make_files(d):
    d = pathlib.Path(d)
    (d / "orders.csv").write_text(ORDERS)
    (d / "customers.csv").write_text(CUSTOMERS)
    (d / "prices.csv").write_text(PRICES)
    (d / "events.jsonl").write_text(EVENTS)
    (d / "sales.csv").write_text(sales())


def sh(cmd, exit=0, hold=None):
    """One command. `hold` is the seconds the output stays on screen (default: demo.py's); a long command is typed on purpose with backslash continuations at flag boundaries."""
    return {"cmd": cmd, "exit": exit, "hold": hold}


COLS = 80  # the terminal's width: no line of the video (caption, command or output) is longer than COLS - 1, so nothing wraps
ROWS = 22

THREADS = "--parallel-min-bytes 0 --chunk-bytes 16384"
HASH = "  --group customer --agg count,sum:bytes --format csv sales.csv \\\n  | shasum -a 256"

# (scene, [caption lines], [commands]); a scene starts on a cleared screen
SCENES = [
    ("question", ["one question, one exact answer: who sent the most bytes?"], [
        sh("cat orders.csv"),
        sh("""table --where "bytes != ''" --group customer --agg count,sum:bytes \\
  --sort -sum:bytes --format csv orders.csv""", hold=4.0),
    ]),
    ("refusal", ["a wrong column name is refused (exit 3); printed: rule, hint, repair"], [
        sh("""table --where "status = 200" --select Customer,bytes orders.csv \\
  | jq -r '.error | .rule, .hint, (.repair.options[0].argv | join(" "))'""", 3, hold=4.5),
        sh("""table --where "status = 200" --select customer,bytes \\
  --format csv orders.csv""", hold=3.5),
    ]),
    ("decimals", ["exact decimals: 0.1 + 0.2 + 0.3 as dec(2), then as plain doubles"], [
        sh("cat prices.csv"),
        sh("""table --agg "sum:price:dec(2)" --format csv prices.csv"""),
        sh("""python3 -c "print(0.1 + 0.2 + 0.3)" """.strip(), hold=4.5),
    ]),
    ("explain", ["--explain: the plan, not the run; here only the size and bytes read", "(the file is larger than the one 64 KiB block read for the header)"], [
        sh("""table --explain --group customer --agg count,sum:bytes sales.csv \\
  | jq -c '.data.explain | {size: .source.size, read: .source.bytes_read}'""", hold=4.0),
    ]),
    ("jsonl", ["JSON lines in and out: --fields names the columns"], [
        sh("cat events.jsonl"),
        sh("""table --input-format jsonl --fields user.name,action,ms \\
  --where "ms:int > 50" --format jsonl events.jsonl"""),
    ]),
    ("join", ["a join: which orders have no customer on file? (anti join)"], [
        sh("cat customers.csv"),
        sh("""table --join customers.csv --on customer=name --join-type anti \\
  --select id,customer --format csv orders.csv"""),
    ]),
    ("query", ["the same question in a small SQL: --query means exactly the flags"], [
        sh("""table --query "select customer, sum(bytes) from 'orders.csv' \\
  where bytes != '' group by customer order by sum(bytes) desc" \\
  --format csv"""),
    ]),
    ("threads", ["the same bytes on 1 core and on 4: the hash of each answer", "(6000 rows; the size threshold is lowered so that 4 threads run)"], [
        sh("table --threads 1 %s \\\n%s" % (THREADS, HASH)),
        sh("table --threads 4 %s \\\n%s" % (THREADS, HASH), hold=4.5),
    ]),
    ("pipe", ["and it reads a pipe"], [
        sh("""cat orders.csv \\
  | table --where "status = 200" --select id,customer --format csv"""),
    ]),
]
# the closing card: (text, style), one line each; "" is a blank line
CLOSING = [("table", "title"), ("", ""), ("exact answers, or a refusal", "tag"), ("that tells you how to fix the question", "tag"), ("", ""),
           ("https://github.com/alpibrusl/cancho-table", "url")]


def commands():
    for _, _, cmds in SCENES:
        for c in cmds:
            yield c


def environment(table_bin, scratch):
    """The environment of every command: `table` is the built binary on PATH, found by name as a user would."""
    scratch = pathlib.Path(scratch)
    bindir = scratch / "bin"
    bindir.mkdir(exist_ok=True)
    link = bindir / "table"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(pathlib.Path(table_bin).resolve())
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    return env


def line_of(cmd):
    return ["bash", "-c", "set -o pipefail; " + cmd]


def run_all(table_bin):
    """Run every command for real; return [(cmd, expected_exit, status, output)]."""
    with tempfile.TemporaryDirectory() as d:
        make_files(d)
        env = environment(table_bin, d)
        out = []
        for c in commands():
            r = subprocess.run(line_of(c["cmd"]), cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            out.append((c["cmd"], c["exit"], r.returncode, r.stdout.decode("utf-8")))
        return out


def transcript(results):
    parts = []
    for cmd, _, status, output in results:
        parts.append("$ %s\n%s[exit %d]\n" % (cmd, output, status))
    return "\n".join(parts)


if __name__ == "__main__":
    table = os.environ.get("TABLE_BIN") or str(ROOT / "build" / "table")
    res = run_all(table)
    for cmd, want, got, _ in res:
        if want != got:
            sys.exit("refused unexpectedly: %r exited %d, the storyboard says %d" % (cmd, got, want))
    text = transcript(res)
    if "--write" in sys.argv:
        (HERE / "expected.txt").write_text(text)
        print("wrote", HERE / "expected.txt")
    else:
        sys.stdout.write(text)
