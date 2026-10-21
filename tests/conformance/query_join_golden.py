"""The golden of `main` for the compatibility gate of slice J3b (docs/joins.md section 20): the answers (status, standard output, standard error) of the binary of `main` to the queries
that were valid before the join, and to the refusals of the grammar before it, as the md5 of each.

    python3 query_join_golden.py record DIR      DIR holds main's `table`; writes query_join_main_golden.json
    python3 query_join_golden.py check DIR       the same cases through the binary in DIR, against the file

The cases: every accepted query and every worked example of query_cases.py, with the fixtures it names, as csv and as json; the seeded random plans of query_gen.py (the same generator the
equivalence gate of docs/query.md uses) as queries over their own tables; a hand list of the words the join makes contextual (`semi`, `anti`, `left`, `join`, `a`, `b`, `as`: as names of
columns, as aliases, quoted and bare, in every clause); and the refusals of the grammar without a join (those that name the feature `join` or `subquery` changed their hint on purpose and
are not here, and neither are the refusals that expect "a type" (slice T3 added three to the types they list), nor those that name `cast-in-group`, whose hint slice T2 extended with the flag of a period: test_datetime_t2.py holds it). A query that was valid is byte for byte the same; a refusal is the same bytes, hint included.
"""

import hashlib
import json
import os
import pathlib
import random
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile

import query_cases
import query_gen
import toolbox_pin

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / "query_join_main_golden.json"
SALES = "host,status,bytes,price\na,200,512,1.50\nb,404,0,2.25\na,200,1024,1.5\nc,500,2048,10.00\nb,200,64,0.75\n"
NEAR = [
    # the words the join reads in a position of its own are columns, aliases and names everywhere else
    "select semi, anti from 'f.csv'",
    "select semi from 'f.csv' where semi = 'x'",
    "select a as semi, b as anti from 'f.csv' order by semi",
    "select a as left from 'f.csv'",
    "select a as semi from 'f.csv' order by semi desc",
    "select \"join\", \"left\", \"inner\", \"on\", \"using\", \"semi\" from 'k.csv'",
    "select \"join\" from 'k.csv' where \"left\" = '1' and \"on\" != 'x' order by \"inner\"",
    "select a, count(*) as anti from 'f.csv' group by a order by anti desc",
    "select a, count(*) as semi from 'f.csv' group by a order by semi",
    "select a from 'f.csv' where a = 'join' or a = 'left' or a in ('semi', 'anti', 'as')",
    "select a from 'f.csv' where b contains 'on' and not c = '1.5'",
    "select a from stdin",
    "select a, b from stdin where a = 1",
    "select \"a.b\", \"x.y.z\" from 'd.csv'",
    "select a from 'f.csv' where a = '.' or b = 'a.b' or b in ('.', '..', 'x.y')",
    "select a from 'f.csv' where c::float > .5 and c::float < 5.",
    "select a from 'f.csv' limit 1 offset 1",
    "select * from 'f.csv'",
]
EXTRA = {"k.csv": "join,left,inner,on,using,semi\n1,2,3,4,5,6\n7,8,9,10,11,12\n", "d.csv": "a.b,x.y.z\n1,2\n3,4\n", "f.csv": "a,b,c\n1,x,1.5\n2,y,2.5\n"}
SEEDS = 80


def files(d):
    for name, text in {**query_cases.FIXTURES, **EXTRA, "sales.csv": SALES}.items():
        (pathlib.Path(d) / name).write_text(text)


def cases(randoms=None):
    """(id, argv, stdin or None, key): `randoms` are the (table, query) pairs of the golden (generated when none are given)."""
    out = []
    for i, (query, flags, source) in enumerate(query_cases.ACCEPTED):
        for fmt in ("csv", "json"):
            out.append(("accepted %d %s" % (i, fmt), ["--query", query, "--format", fmt], None, "fixed"))
    for i, row in enumerate(query_cases.EXAMPLES):
        for fmt in ("csv", "json"):
            out.append(("example %d %s" % (i, fmt), ["--query", row[0], "--format", fmt], None, "fixed"))
    for i, query in enumerate(NEAR):
        stdin = b"a,b\n1,2\n3,4\n" if "stdin" in query else None
        for fmt in ("csv", "json"):
            out.append(("near %d %s" % (i, fmt), ["--query", query, "--format", fmt], stdin, "fixed"))
    for seed, pair in enumerate(randoms if randoms is not None else random_cases()):
        out.append(("random %d" % seed, None, None, tuple(pair)))
    for i, row in enumerate(query_cases.REFUSALS + query_cases.NOT_PINNED + query_cases.B_PINNED):
        query = row[0]
        if row[1] == "unsupported" and row[3] in ("join", "subquery", "cast-in-group"):
            continue
        if row[1] == "syntax" and row[3] == "a type: int, dec(N), float, date, datetime or timestamp":
            continue
        out.append(("refusal %d" % i, ["--query", query], None, "fixed"))
    return out


def digest(binary, p):
    """The md5 of (status, standard output, standard error; the answer read as the tool at the earlier pin wrote it, toolbox_pin.before), the path of the binary (a repair offers the invocation back, argv[0] first) written {BIN}."""
    b = binary.encode()
    return hashlib.md5(repr((p.returncode, toolbox_pin.before(p.stdout.replace(b, b"{BIN}")), p.stderr.replace(b, b"{BIN}"))).encode()).hexdigest()


def random_cases():
    """[(table text, query)] of the seeded plans of query_gen: written into the golden when it is recorded and read back from it, so that what is replayed does not depend on the random streams of the Python that runs it."""
    out = []
    for seed in range(SEEDS):
        rnd = random.Random(seed)
        names, kinds, rows = query_gen.make_table(rnd)
        d = tempfile.mkdtemp(prefix="golden-")
        try:
            query_gen.write_csv(os.path.join(d, "f.csv"), names, rows)
            text = open(os.path.join(d, "f.csv"), encoding="utf-8", newline="").read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
        plan = query_gen.make_plan(rnd, names, kinds, rows, many=True)
        out.append((text, query_gen.query_form(rnd, plan, "'f.csv'")))
    return out


def run_case(binary, case, workdir):
    cid, argv, stdin, key = case
    if key != "fixed":
        text, query = key
        d = tempfile.mkdtemp(prefix="golden-")
        try:
            with open(os.path.join(d, "f.csv"), "w", encoding="utf-8", newline="") as f:
                f.write(text)
            p = subprocess.run([binary, "--query", query, "--format", "csv"], capture_output=True, cwd=d, input=b"", timeout=60)
            q = subprocess.run([binary, "--query", query, "--format", "json"], capture_output=True, cwd=d, input=b"", timeout=60)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return cid, digest(binary, p) + digest(binary, q)
    p = subprocess.run([binary, *argv], capture_output=True, cwd=workdir, input=stdin if stdin is not None else b"", timeout=60)
    return cid, digest(binary, p)


def compute(binary, randoms=None):
    d = tempfile.mkdtemp(prefix="golden-")
    try:
        files(d)
        return dict(run_case(binary, c, d) for c in cases(randoms))
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    mode, where = sys.argv[1], pathlib.Path(sys.argv[2])
    if mode == "record":
        randoms = random_cases()
        got = compute(str(where / "table"), randoms)
        GOLDEN.write_text(json.dumps({"binary": sys.argv[3] if len(sys.argv) > 3 else "main", "random": randoms, "cases": got}, sort_keys=True, indent=0, separators=(",", ":")) + "\n")
        print("recorded", len(got))
    else:
        g = json.loads(GOLDEN.read_text())
        got = compute(str(where / "table"), g["random"])
        want = g["cases"]
        bad = [k for k in want if want[k] != got.get(k)]
        print("checked", len(want), "differing", len(bad), bad[:5])
        sys.exit(1 if bad else 0)
