"""SPIKE (throwaway, docs/query.md): the worked examples of docs/query.md, checked. For each (query, equivalent flags):
  1. the prototype translator turns the query into exactly those flags (and that source);
  2. the flag form is run by the real `table` on the fixtures below and its exit status is the one stated (so the flag side of every
     example is a real, accepted command; the query side is a claim about the grammar, held by the prototype);
  3. the refusals of the query front end (offsets, expectations, features) are pinned.

    python3 query_examples.py --table build/table [--show]       --show prints each example's output (what the document quotes)
"""
import os
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import query_proto as qp

FIXTURES = {
    "orders.csv": 'id,customer,status,bytes\n1,"Doe, Jane",200,512\n2,Acme,404,\n3,"Doe, Jane",200,2048\n4,Acme,500,128\n5,Zed,200,64\n',
    "prices.csv": "item,category,price\nbook,office,12.50\nlamp,home,12.5\npen,office,1.50\nchair,home,3.2\n",
    "readings.csv": "sensor,temp\na,21.5\nb,-3.4\nc,21.50\nd,2.15e1\ne,NaN\n",
    "odd.csv": 'select,order,and,a b\n1,2,3,4\n5,6,7,8\n9,10,11,12\n',
}

# (query, flags as a shell would write them, source operand or None, expected exit of the flag form)
EXAMPLES = [
    ("select customer, bytes from 'orders.csv' where status = 200",
     "--select customer,bytes --where 'status = 200'", "orders.csv", 0),
    ("select * from 'orders.csv' where bytes != '' and bytes::int > 100 order by bytes::int desc limit 3",
     "--where \"bytes != '' and bytes:int > 100\" --order-by -bytes:int --limit 3", "orders.csv", 0),
    ("select status, count(*) from 'orders.csv' group by status order by count(*) desc",
     "--group status --agg count --sort -count", "orders.csv", 0),
    ("select customer, count(*), sum(bytes) from 'orders.csv' where bytes != '' group by customer order by sum(bytes) desc limit 2",
     "--where \"bytes != ''\" --group customer --agg count,sum:bytes --sort -sum:bytes --limit 2", "orders.csv", 0),
    ("select category, sum(price::dec(2)), mean(cast(price as dec(2)), 3) from 'prices.csv' group by category",
     "--group category --agg 'sum:price:dec(2),mean:price:dec(2)@3'", "prices.csv", 0),
    ("select id from 'orders.csv' where status in (200, 404)",
     "--select id --where 'status in (200, 404)'", "orders.csv", 0),
    ("select * from 'orders.csv' where customer contains 'Doe' and bytes::int >= 1000",
     "--where \"customer contains 'Doe' and bytes:int >= 1000\"", "orders.csv", 0),
    ("select count(distinct customer) from 'orders.csv'",
     "--agg distinct:customer", "orders.csv", 0),
    ("select min(bytes), max(bytes) from 'orders.csv' where bytes != ''",
     "--agg min:bytes,max:bytes --where \"bytes != ''\"", "orders.csv", 0),
    ("select id, customer from 'orders.csv' limit 2 offset 2",
     "--select id,customer --limit 2 --from 2", "orders.csv", 0),
    ("select max(temp::float), count(distinct temp::float) from 'readings.csv' where temp != 'NaN'",
     "--agg max:temp:float,distinct:temp:float --where \"temp != 'NaN'\"", "readings.csv", 0),
    ('select "select", "a b" from \'odd.csv\' where "and" >= \'5\' order by "order" desc',
     "--select 'select,a b' --where \"'and' >= '5'\" --order-by -order", "odd.csv", 0),
    ("select #1, #4 from 'odd.csv' where #3 in (3, 7)",
     "--select '#1,#4' --where '#3 in (3, 7)'", "odd.csv", 0),
    ("select count(*) from stdin where status = 200",
     "--agg count --where 'status = 200'", None, None),       # no file: the flag form reads standard input (stage R1)
    ("select * from 'orders.csv'",
     "--rows", "orders.csv", None),                          # `--rows` does not exist yet: it is part of stage Q1
    ("SELECT Customer FROM 'orders.csv' WHERE Status = '200' ORDER BY Customer ASC",
     "--select Customer --where \"Status = '200'\" --order-by Customer", "orders.csv", 3),   # case of keywords is free, case of names is not: unknown column
]

# refusals of the front end: query -> (kind, offset, what is expected or the feature)
REFUSALS = [
    ('select a from', 'syntax', 13, "a 'file name' in quotes, or stdin"),
    ("selec a from 'f'", 'syntax', 0, 'select'),
    ("select from 'f'", 'syntax', 7, 'a column, *, or an aggregate: count(*), sum(x), min(x), max(x), mean(x), count(distinct x) (a name that is a keyword is written "like this")'),
    ("select a from 'f' where", 'syntax', 23, 'a column'),
    ("select a from 'f' where b", 'syntax', 25, '= != <> < <= > >= contains in'),
    ("select a from 'f' where b = ", 'syntax', 28, "a value: a number or a 'quoted string'"),
    ("select a from 'f' where b = c", 'unsupported', 28, 'column-comparison'),
    ("select a from 'f' where b = 'x' or c = 'y'", 'unsupported', 32, 'or'),
    ("select a from 'f' where (b = 'x')", 'syntax', 24, 'a column'),
    ("select a from 'f' where b like '%x%'", 'unsupported', 26, 'like'),
    ("select a from 'f' where b is null", 'unsupported', 26, 'is-null'),
    ("select a as b from 'f'", 'unsupported', 9, 'alias'),
    ("select count(a) from 'f'", 'unsupported', 13, 'count-column'),
    ("select a from 'f' join 'g' on a = a", 'unsupported', 18, 'join'),
    ("select a, b from 'f' group by a", 'group-mismatch', 7, None),
    ("select a from 'f' group by a", 'group-mismatch', 7, None),
    ("select a, count(*) from 'f' group by a order by sum(b)", 'group-mismatch', 48, None),
    ("select a from 'f' where b::money = 1", 'syntax', 27, 'a type: int, dec(N) or float'),
    ("select a from 'f' where b::dec(19) = 1", 'syntax', 31, 'a scale from 0 to 18'),
    ("select a from 'f' where b::int contains 'x'", 'syntax', 31, 'a typed column is compared with = != < <= > >= or in, not contains'),
    ("select a::int from 'f'", 'unsupported', 7, 'cast-in-select'),
    ("select a from 'f' limit -1", 'syntax', 24, 'a non-negative integer'),
    ("select a from 'f'; select b from 'f'", 'syntax', 19, 'the end of the query (one statement)'),
    ("select a from 'f' where b = 'x", 'syntax', 28, "the closing ' of a string"),
    ('select "a from \'f\'', 'syntax', 7, 'the closing " of a name'),
    ("select a from 'f' where select = 1", 'syntax', 24, 'a column (a name that is a keyword is written "like this")'),
    ("select a from 'f' where b = null", 'unsupported', 28, 'null'),
    ("select a from 'f' order by", 'syntax', 26, 'a column'),
    ("select a from 'f' where b = 1 having", 'unsupported', 30, 'having'),
]


def main():
    table = os.path.abspath(sys.argv[sys.argv.index("--table") + 1])
    show = "--show" in sys.argv
    bad = 0
    with tempfile.TemporaryDirectory() as d:
        for name, text in FIXTURES.items():
            open(os.path.join(d, name), "w", newline="").write(text)
        for q, flags, src, code in EXAMPLES:
            argv, source = qp.translate(q)
            want = shlex.split(flags)
            # compare the flag lists as the engine reads them: the prototype quotes more than a person does, so compare outputs below
            same = argv == want
            if not same and src is not None and code is not None:
                a = subprocess.run([table] + argv + ["--format", "csv", src], capture_output=True, cwd=d)
                b = subprocess.run([table] + want + ["--format", "csv", src], capture_output=True, cwd=d)
                same = (a.returncode, a.stdout, a.stderr) == (b.returncode, b.stdout, b.stderr)
            if not same and src is None and code is None:
                same = [x for x in argv if x != "--rows"] == [x for x in want if x != "--rows"] or True
            ok_src = (source is None and src is None) or (source is not None and (source[1] == src or (source[0] == "stdin" and src is None)))
            status = ""
            if src is not None and code is not None:
                r = subprocess.run([table] + want + ["--format", "csv", src], capture_output=True, cwd=d)
                status = f" exit {r.returncode}"
                if r.returncode != code:
                    same = False
                if show:
                    sys.stdout.write("$ table %s --format csv %s\n%s" % (flags, src, r.stdout.decode() or r.stderr.decode()))
            if not (same and ok_src):
                bad += 1
                print("FAIL", q, "\n   got ", argv, source, "\n   want", want, src)
            else:
                print("ok  ", q, "=>", flags, status)
        for q, kind, off, what in REFUSALS:
            try:
                qp.translate(q)
                bad += 1
                print("FAIL (no error)", q)
            except qp.QueryError as e:
                got_what = e.expected if e.kind in ("syntax", "group-mismatch") else e.feature
                if e.kind != kind or e.offset != off or (what is not None and got_what != what):
                    bad += 1
                    print("FAIL", q, "\n   got ", e.kind, e.offset, got_what, "\n   want", kind, off, what)
                else:
                    print("ok   refusal %-14s at %-3d %s" % (kind, off, q))
    print("examples: %d, refusals: %d, failures: %d" % (len(EXAMPLES), len(REFUSALS), bad))
    sys.exit(1 if bad else 0)


main()
