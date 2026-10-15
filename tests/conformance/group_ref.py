"""The reference answer of a grouping by any number of columns, independent of the tool: SQLite's `GROUP BY`, driven from Python.

The oracle of tests/conformance/test_group_columns.py (docs/query.md, the grouping by many columns). A table is a header and rows of text cells; a grouping is a list
of key columns (each one a header name or `#N`, with a kind: text, `int`, `dec` with a scale, or `float`, any column in any position and a column more than once),
and a list of aggregates. The key of a row is read by the rules of docs/numbers.md (tests/conformance/numbers_ref.py: Python `int`, exact decimal, the nearest double),
stored in SQLite as TEXT, INTEGER or REAL, and SQLite does the grouping and the order (`ORDER BY` the same keys: the tool lists groups in the order of their keys,
text bytewise, numbers by value). The answer is the output columns and the rows as text, as the tool writes them.
"""

import sqlite3

import numbers_ref as ref

TEXT = "text"
INT = "int"
FLOAT = "float"


def dec(scale):
    return ("dec", scale)


def suffix(kind):
    if kind == TEXT:
        return ""
    if kind == INT:
        return ":int"
    if kind == FLOAT:
        return ":float"
    return ":dec(%d)" % kind[1]


def column_of(header, name):
    """The index of a column named by `name` (a header name or `#N`)."""
    if name.startswith("#"):
        return int(name[1:]) - 1
    return header.index(name)


def read_key(cell, kind):
    """The stored value of a key cell, or None when the cell is not of the kind (the tool refuses; the tests here never generate one)."""
    if kind == TEXT:
        return cell
    if kind == INT:
        return int(cell)
    if kind == FLOAT:
        r = ref.flt(cell)
        assert r[0] == "ok", cell
        return r[1]
    r = ref.dec(cell, kind[1])
    assert r[0] == "ok", cell
    return r[1]


def spell_key(v, kind):
    if kind == TEXT:
        return v
    if kind == INT:
        return str(v)
    if kind == FLOAT:
        return ref.float_text(v)
    return ref.text(v, kind[1])


def group_flag(keys):
    return ",".join(name + suffix(kind) for name, kind in keys)


def labels(header, keys, aggs):
    out = [header[column_of(header, name)] for name, _ in keys]
    out += list(aggs)
    return out


def answer(header, rows, keys, aggs=("count", "sum:v", "min:v", "max:v"), where=None):
    """(columns, rows): the grouping of `rows` by `keys` with `aggs` (count, sum:v, min:v, max:v on the integer column `v`; distinct:w on the text column `w`), in the
    order of the keys. `where` is a function of a row (a list of cells) that says whether it is kept."""
    db = sqlite3.connect(":memory:")
    m = len(keys)
    idx = [column_of(header, name) for name, _ in keys]
    vi = header.index("v") if "v" in header else None
    wi = header.index("w") if "w" in header else None
    db.execute("create table t (%s)" % ", ".join(["g%d" % j for j in range(m)] + ["v", "w"]))
    batch = []
    for row in rows:
        if where is not None and not where(row):
            continue
        batch.append([read_key(row[i], kind) for i, (_, kind) in zip(idx, keys)] + [int(row[vi]) if vi is not None else 0, row[wi] if wi is not None else ""])
    db.executemany("insert into t values (%s)" % ",".join("?" * (m + 2)), batch)
    selects = []
    for a in aggs:
        if a == "count":
            selects.append("count(*)")
        elif a == "distinct:w":
            selects.append("count(distinct w)")
        else:
            f, c = a.split(":")
            assert c == "v"
            selects.append("%s(v)" % f)
    gl = ", ".join("g%d" % j for j in range(m))
    sql = "select %s from t group by %s order by %s" % (", ".join([("g%d" % j) for j in range(m)] + selects), gl, gl)
    if m == 0:
        sql = "select %s from t" % ", ".join(selects)
    out = []
    for r in db.execute(sql):
        out.append([spell_key(r[j], keys[j][1]) for j in range(m)] + [str(x) for x in r[m:]])
    return labels(header, keys, aggs), out
