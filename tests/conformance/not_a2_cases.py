"""The cases of the compatibility gate of slice A2 of docs/or-and-columns.md (`not`), and the golden of the binary before the slice.

test_not_a2_compat.py holds the tool against them:

* cases(): the 2,892 cases of or_a0.golden_cases() (the hand lists, the pinned refusals, the soups, the other forms an expression arrives in) and, beside them, cases made for `not`: soups of
  `not (`, `not`, `x not in`, `x not contains` in every place in `--where` and in `--query`, the expressions that today are valid with a column called `not` or `or`, and the other forms an
  expression arrives in (explain, a grouping, the type report, JSON lines out, the equals form, csv, threads, an order);
* not_a2_base_golden.json: the answer of `main` after slice A1 (a3af5ec) to every case, made before a line of A2 was written:
  TOOLBOX_BIN=/path/to/the/build/of/a3af5ec python3 tests/conformance/not_a2_cases.py --write-golden
  Only ever run with the binary of main the slice starts from.
"""

import json
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import or_a0  # noqa: E402

GOLDEN = HERE / "not_a2_base_golden.json"

WHERE_NOT_HAND = [
    "not", "not (", "not ()", "not (a = 1)", "not (a = 1) and b = 2", "not (a = 1 or b = 2)", "not (a = 1 and b = 2) or c = 3", "a = 1 and not (b = 2)", "a = 1 or not (b = 2 and c = 3)",
    "a not in (1)", "a not in (1, 2) and b = 3", "a not in ()", "a not in (1", "a not in 1", "a not contains x", "a not contains", "a:int not in (1, 2)", "a:int not contains 1", "a:dec(2) not contains 1",
    "a:float not in (1.5, inf)", "a not = 1", "a not", "a not not in (1)", "a not  in  (1)", "a NOT in (1)", "a not IN (1)", "a 'not' in (1)", "a \\not in (1)", "a not 'in' (1)",
    "not a = 1", "not a < 1", "not a in (1, 2)", "not a contains x", "not a not in (1)", "not a not contains x", "not a:int > 1", "not a = 1 and not b = 2", "not a = 1 and (b = 2", "not 'a' = 1",
    "not not a = 1", "not not = 1", "not not in (1)", "not not contains x", "not = 1", "not != 1", "not in (1)", "not contains x", "not  contains  x", "not'x' = 1", "not'x'", "not(a = 1)", "not\t(a = 1)",
    "not (not = 1)", "not (not in (1))", "not (not not in (1))", "(not = 1)", "(not) = 1", "'not' (a = 1)", "\\not (a = 1)", "n\\ot (a = 1)", "a = 1 not (b = 2)", "a = 1 and not", "a = 1 and not (",
    "or not in (1)", "or not in (1) or and not in (2)", "or = 1 and not not in (2)", "not = 1 or or = 2", "and not in (1)", "in not in (1)", "contains not contains x", "in = 1 and not (in = 2)",
    "a = 1 or b = 2 and not (c = 3)", "not (a = 1) or b = 2 and c = 3", "(a = 1 or b = 2) and not (c = 3 or d = 4)", "not (a = 1 or b = 2 and c = 3)", "not (a = 1 and b = 2 or c = 3)",
    "a = 1 or not (b = 2) and c not in (3)", "a not in (1) or b not in (2) and c = 3", "((not (a = 1)))", "not ((a = 1))", "not (a = 1) )", "not (a = 1", "not a = 1)",
    "not (" * 8 + "a = 1" + ")" * 8, "not (" * 9 + "a = 1" + ")" * 9, "(" * 7 + "not (a = 1)" + ")" * 7, "(" * 8 + "not (a = 1)" + ")" * 8,
] + [" and ".join("a not in (%d)" % k for k in range(n)) for n in (63, 64, 65)] + [" or ".join("not (a = %d)" % k for k in range(n)) for n in (63, 64, 65)] \
  + ["a not in (%s)" % ", ".join(str(k) for k in range(n)) for n in (1023, 1024, 1025)]

QUERY_NOT_HAND = [
    "select a from 't.csv' where not a = 1", "select a from 't.csv' where not (a = 1)", "select a from 't.csv' where not (a = 1 or b = 'x')", "select a from 't.csv' where not not a = 1",
    "select a from 't.csv' where b not in ('x')", "select a from 't.csv' where b not in ('x', 'y') and not a = 1", "select a from 't.csv' where b not contains 'x'", "select a from 't.csv' where i::int not in (5, 6)",
    "select a from 't.csv' where i::int not contains 5", "select a from 't.csv' where b not in ()", "select a from 't.csv' where b not in (1", "select a from 't.csv' where b not", "select a from 't.csv' where b not = 1",
    "select a from 't.csv' where b not like 'x'", "select a from 't.csv' where b not like '%x%'", "select a from 't.csv' where b not ilike 'x'", "select a from 't.csv' where b not between 1 and 2",
    "select a from 't.csv' where b is not null", "select a from 't.csv' where not", "select a from 't.csv' where not not", "select a from 't.csv' where not = 1", "select a from 't.csv' where not (",
    "select a from 't.csv' where not ()", "select a from 't.csv' where \"not\" = 1", "select a from 't.csv' where \"not\" not in (1)", "select a from 't.csv' where not \"not\" = 1",
    "select a from 't.csv' where \"or\" not contains 'x' or not \"or\" = 1", "select a from 't.csv' where a = 1 or not b = 2 and c = 3", "select a from 't.csv' where not a = 1 or b = 2 and c = 3",
    "select a from 't.csv' where not (a = 1) and (b = 2 or not c = 3)", "select a from 't.csv' where a = 1 not (b = 2)", "select a from 't.csv' where not a = b", "select a from 't.csv' where not a = null",
    "select a from 't.csv' order by not a", "select a from 't.csv' where a = 1 order by a or not b", "select a, count(*) from 't.csv' where not a = 1 group by a",
    "select a from 't.csv' where " + "not " * 8 + "a = 1", "select a from 't.csv' where " + "not " * 9 + "a = 1", "select a from 't.csv' where " + "not (" * 8 + "a = 1" + ")" * 8,
    "select a from 't.csv' where " + "not (" * 9 + "a = 1" + ")" * 9,
] + ["select a from 't.csv' where " + " and ".join("a not in (%d)" % k for k in range(n)) for n in (64, 65)]

# the expressions that are valid today and mean something with a column called `not` or `or`, to be answered as they are
NEAR_VALID = ["not = 2", "not != 2", "not in (2)", "not contains 2", "not = 2 and or = 1", "or = 1 or not = 3", "(not = 2)", "(not = 2) and (or = 1)", "or in (1) and not in (3)", "not in (2, 3) or or in (1)",
              "'not' = 2", "'or' = 1 and not = 2", "and = 3 and not contains 3", "not:int = 2", "not:int in (2)", "not:dec(2) = 2", "not:float > 1"]


def not_soup(rng):
    import test_not_a2 as T
    r = rng.random()
    if r < 0.45:
        return T.soup_not(rng)
    if r < 0.8:
        return T.damaged(rng)
    return T.unwrapped(rng)


Q_PIECES_COLUMNS = ["a", "b", "c", "\"a b\"", "#1", "i::int", "cast(i as int)", "d::dec(2)", "f::float", "\"or\"", "\"not\"", "t"]


def query_not_soup(rng):
    def leaf():
        col = rng.choice(Q_PIECES_COLUMNS)
        typed = "::" in col or "cast" in col
        vals = ["1", "5", "-2", "07"] if typed else ["'x'", "'y'", "''", "1", "'it''s'"]
        r = rng.random()
        if r < 0.3:
            return "%s %s %s" % (col, rng.choice(["=", "!=", "<>", "<", ">="]), rng.choice(vals))
        if r < 0.5:
            return "%s %s (%s)" % (col, rng.choice(["in", "not in", "not in", "NOT IN"]), ", ".join(rng.choice(vals) for _ in range(rng.randint(1, 3))))
        if r < 0.6 and not typed:
            return "%s %s %s" % (col, rng.choice(["contains", "not contains", "NOT CONTAINS"]), rng.choice(vals))
        return rng.choice(["%s not like 'x'" % col, "%s not = 1" % col, "%s not" % col, "%s is not null" % col, "%s not between 1 and 2" % col, "%s = %s" % (col, rng.choice(Q_PIECES_COLUMNS))])

    def unit(depth):
        r = rng.random()
        if r < 0.2 and depth < 5:
            return "not " + unit(depth + 1)
        if r < 0.32 and depth < 4:
            return "not (" + clause(depth + 1) + ")"
        if r < 0.42 and depth < 4:
            return "(" + clause(depth + 1) + ")"
        return leaf()

    def clause(depth):
        glue = rng.choice([" and ", " or "])
        return glue.join(unit(depth) for _ in range(rng.randint(1, 4))) if rng.random() < 0.85 else (" %s " % rng.choice(["and", "or"])).join(unit(depth) for _ in range(rng.randint(2, 4)))

    q = "select a from 't.csv' where " + clause(0) + rng.choice(["", "", "", " group by b", " order by a", " order by a desc limit 5", " limit 3 offset 1", ";", " foo", " or", " and a = 1"])
    r = rng.random()
    if r < 0.12:
        q = q[:rng.randint(0, len(q))]
    elif r < 0.22:
        i = rng.randint(0, len(q) - 1)
        q = q[:i] + rng.choice(["'", "\"", "(", ")", ",", " or ", ";", " not ", "not ("]) + q[i + 1:]
    return q


def cases():
    out = list(or_a0.golden_cases())
    rng = random.Random(20261110)
    for e in WHERE_NOT_HAND + NEAR_VALID:
        out.append(("where", ["--root", "{DIR}", "--where", e, "--select", "a", "t.csv"]))
    for _ in range(1500):
        out.append(("where", ["--root", "{DIR}", "--where", not_soup(rng), "--select", "a", "t.csv"]))
    for q in QUERY_NOT_HAND:
        out.append(("query", ["--root", "{DIR}", "--query", q]))
    for _ in range(1000):
        out.append(("query", ["--root", "{DIR}", "--query", query_not_soup(rng)]))
    for e in ("not (a = 1)", "a not in (1, 2)", "a not contains x", "not (a = 1 or b = 2)", "x not in (1)", "not = 1", "not in (1)", "not a = 1", "(not = 1) or or = 2"):
        out.append(("explain", ["--root", "{DIR}", "--explain", "--where", e, "--select", "a", "t.csv"]))
        out.append(("group", ["--root", "{DIR}", "--where", e, "--group", "b", "--agg", "count", "t.csv"]))
        out.append(("report", ["--root", "{DIR}", "--report", "types", "--where", e, "t.csv"]))
        out.append(("jsonl", ["--root", "{DIR}", "--format", "jsonl", "--where", e, "--select", "a", "t.csv"]))
        out.append(("equals", ["--root", "{DIR}", "--where=" + e, "--select", "a", "t.csv"]))
        out.append(("csv", ["--root", "{DIR}", "--format", "csv", "--where", e, "--select", "a", "t.csv"]))
        out.append(("threads", ["--root", "{DIR}", "--threads", "4", "--parallel-min-bytes", "0", "--where", e, "--select", "a", "t.csv"]))
        out.append(("order", ["--root", "{DIR}", "--where", e, "--order-by", "-a:int", "--select", "a", "t.csv"]))
    for q in ("select a from 't.csv' where not b = 'x'", "select a from 't.csv' where b not in ('x', 'y')", "select a from 't.csv' where not (b = 'x' or c = 'y')"):
        out.append(("explain-query", ["--root", "{DIR}", "--explain", "--query", q]))
        out.append(("query-equals", ["--root", "{DIR}", "--query=" + q]))
        out.append(("query-csv", ["--root", "{DIR}", "--format", "csv", "--query", q]))
        out.append(("query-limit", ["--root", "{DIR}", "--limit", "2", "--query", q]))
    return out


def write_golden(run, directory):
    rows = or_a0.run_cases(cases(), directory, run)
    out = {row[0]: [row[1], row[2], row[3], row[4], row[5]] for row in rows}
    GOLDEN.write_text(json.dumps({"binary": "main a3af5ec (slice A1 merged)", "count": len(out), "cases": out}, sort_keys=True, separators=(",", ":")) + "\n")
    print("%d answers written to %s (%d refusals)" % (len(out), GOLDEN, sum(1 for v in out.values() if v[0] != 0)))


if __name__ == "__main__":
    if "--write-golden" not in sys.argv:
        print(__doc__)
        sys.exit(2)
    import harness
    sc = harness.Scratch()
    sc.write("t.csv", or_a0.DATA)
    write_golden(harness.run, sc.dir)
    sc.cleanup()
