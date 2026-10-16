"""The SQLite oracle of the join query (docs/joins.md gate O of slice J3b): the statement the tool is given is run by SQLite on the same two tables.

SQLite reads a quoted string where a table name goes, so `from 'p.csv' as a join 'b.csv' as b on a.k = b.k` is the very text, and `cast(x as int)` is spelt alike in both. What SQL spells
otherwise is the semi and the anti join, which it says with `exists` and `not exists` (`sql` is that text; `query` is the tool's). The tables are given as TEXT columns, so SQLite compares
the bytes as the tool does; a cell that is NULL in SQLite (the build side of a left join's unmatched row) is the empty text of the tool.

Each spec is `{query, sql, total, tag}`; `total` says the order by names every column of the answer, so the order of the rows is defined and is compared; otherwise the rows are compared as a
multiset. The plans avoid what SQL's NULL would change (a condition or an aggregate on a column of the right table of a left join) and what SQLite casts that the tool refuses (an empty cell
read as a number), which `oracle_ok` keeps out of the tables.
"""

import sqlite3

import query_join_gen as G

TEXT_ROLES = ("g", "t", "k")


def oracle_ok(case):
    """Shapes whose cells SQLite and the tool read alike: no empty cell in an int or a key column (SQLite casts it to 0, the tool refuses it, and an empty key is not NULL for the tool), no quoted record."""
    for rows, header in ((case.P[1], case.P[0]), (case.B[1], case.B[0])):
        for i, n in enumerate(header):
            if n[-1] in ("v", "k") and any(r[i] == "" for r in rows):
                return False
    return not case.awkward


def load(case):
    """An in-memory SQLite with the two tables of a case, named p.csv and b.csv, every column TEXT."""
    db = sqlite3.connect(":memory:")
    for name, (header, rows) in (("p.csv", case.P), ("b.csv", case.B)):
        cols = ", ".join('"%s" text' % h.replace('"', '""') for h in header)
        db.execute('create table "%s" (%s)' % (name, cols))
        db.executemany('insert into "%s" values (%s)' % (name, ", ".join("?" * len(header))), rows)
    return db


def answer(db, sql):
    out = []
    for row in db.execute(sql).fetchall():
        out.append(["" if c is None else (c if isinstance(c, str) else str(c)) for c in row])
    return out


def role_names(header):
    return {r: [n for n in header if n[-1] == r] for r in ("g", "t", "k", "v")}


def text_leaf(rng, ref, dom):
    op = rng.choice(["=", "!=", "<", ">=", "in", "not in"])
    if op in ("in", "not in"):
        vals = rng.sample(dom, min(2, len(dom)))
        return "%s %s (%s)" % (ref, op, ", ".join(G.q(v) for v in vals))
    return "%s %s %s" % (ref, op, G.q(rng.choice(dom)))


def cond(rng, refs_doms, depth=0):
    """A condition over (ref, domain) columns: a leaf, an and, an or in parentheses, a not."""
    r = rng.random()
    if depth >= 2 or r < 0.45:
        ref, dom = rng.choice(refs_doms)
        return text_leaf(rng, ref, dom)
    if r < 0.65:
        return " and ".join(cond(rng, refs_doms, depth + 1) for _ in range(2))
    if r < 0.85:
        # (an operand of an or that is an and, or an or, is parenthesised: a level is all and or all or)
        a, b = (cond(rng, refs_doms, depth + 1) for _ in range(2))
        return "(" + " or ".join("(%s)" % x if " and " in x or " or " in x else x for x in (a, b)) + ")"
    return "not (" + cond(rng, refs_doms, depth + 1) + ")"


DOMS = {"g": ["x", "y", "z", ""], "t": ["t%d" % i for i in range(6)], "k": ["k%d" % i for i in range(6)]}


def dom_of(case, name, rows, header):
    """The values a column has, to draw conditions that sometimes hold: its own cells and a stranger."""
    i = header.index(name)
    cells = sorted({r[i] for r in rows})
    return (cells[:6] or [""]) + ["zz"]


def specs(rng, case, jt, n=6):
    """`n` specs for a case (a case of `query_join_gen.with_prefix`) and a join type."""
    ph, pr = case.P
    bh, br = case.B
    P, B = role_names(ph), role_names(bh)
    out = []
    pairs = case.pairs
    for i in range(n):
        form = rng.choice(["rows", "rows", "group", "group"])
        # the keys
        eq_q, eq_s = [], []
        for pi, bi, kind, _u in pairs:
            ty = (lambda c: "cast(%s as int)" % c) if kind == "int" else (lambda c: c)
            eq_q.append("%s = %s" % (ty(G.ref_of("a", ph[pi])), ty(G.ref_of("b", bh[bi]))))
        on = " and ".join(eq_q)
        # a condition on the right table in the on (the ON of a left join), sometimes
        bdoms = [(G.ref_of("b", c), dom_of(case, c, br, bh)) for r in TEXT_ROLES for c in B[r] if c not in [bh[p[1]] for p in pairs]]
        pdoms = [(G.ref_of("a", c), dom_of(case, c, pr, ph)) for r in TEXT_ROLES for c in P[r]]
        on_cond = cond(rng, bdoms) if bdoms and rng.random() < 0.35 else None
        # (a condition is an and of units, an or only inside parentheses: it goes after the keys with an and, in the query and in SQL alike)
        on_q = on_s = on + (" and " + on_cond if on_cond else "")
        # the where: probe columns always, right-table columns for an inner join
        where_doms = pdoms + (bdoms if jt == "inner" else [])
        where = cond(rng, where_doms) if where_doms and rng.random() < 0.6 else None
        join_kw = {"inner": rng.choice(["join", "inner join"]), "left": "left join", "semi": "semi join", "anti": "anti join"}[jt]
        right_cols = jt in ("inner", "left")
        if form == "rows":
            cols = [G.ref_of("a", c) for c in rng.sample(ph, rng.randint(1, min(3, len(ph))))]
            if right_cols:
                cols += [G.ref_of("b", c) for c in rng.sample(bh, rng.randint(0, min(2, len(bh))))]
            keys = ["%s%s" % (c, rng.choice(["", " desc"])) for c in cols]
            tail = " order by " + ", ".join(keys)
            page = ""
            if rng.random() < 0.4:
                page = " limit %d" % rng.randint(1, 9) + (" offset %d" % rng.randint(0, 4) if rng.random() < 0.5 else "")
            sel = ", ".join(cols)
            query = "select %s from 'p.csv' as a %s 'b.csv' as b on %s%s%s%s" % (sel, join_kw, on_q, "" if not where else " where " + where, tail, page)
            if jt in ("inner", "left"):
                sql = "select %s from 'p.csv' as a %s 'b.csv' as b on %s%s%s%s" % (sel, "left join" if jt == "left" else "join", on_s, "" if not where else " where " + where, tail, page)
            else:
                ex = "%sexists (select 1 from 'b.csv' as b where %s)" % ("not " if jt == "anti" else "", on_s)
                sql = "select %s from 'p.csv' as a where %s%s%s%s" % (sel, ex, "" if not where else " and (" + where + ")", tail, page)
            out.append({"query": query, "sql": sql, "total": True, "tag": "rows %s" % jt})
        else:
            gcols = [G.ref_of("a", c) for c in rng.sample(P["g"] + P["t"] + P["k"], 1)]
            if right_cols and jt == "inner" and rng.random() < 0.5:
                gcols = [G.ref_of("b", rng.choice(B["g"] + B["t"] + B["k"]))]
            aggs = ["count(*)"]
            if P["v"] and rng.random() < 0.7:
                v = G.ref_of("a", P["v"][0])
                aggs.append(rng.choice(["sum(cast(%s as int))", "min(cast(%s as int))", "max(cast(%s as int))"]) % v)
            if jt == "inner" and B["v"] and rng.random() < 0.5:
                aggs.append("sum(cast(%s as int))" % G.ref_of("b", B["v"][0]))
            if rng.random() < 0.3:
                aggs.append("count(distinct %s)" % G.ref_of("a", rng.choice(P["t"] + P["g"])))
            sel = ", ".join(gcols + aggs)
            tail = " group by %s order by %s" % (", ".join(gcols), ", ".join(gcols))
            if jt in ("inner", "left"):
                query = "select %s from 'p.csv' as a %s 'b.csv' as b on %s%s%s" % (sel, join_kw, on_q, "" if not where else " where " + where, tail)
                sql = "select %s from 'p.csv' as a %s 'b.csv' as b on %s%s%s" % (sel, "left join" if jt == "left" else "join", on_s, "" if not where else " where " + where, tail)
            else:
                query = "select %s from 'p.csv' as a %s 'b.csv' as b on %s%s%s" % (sel, join_kw, on_q, "" if not where else " where " + where, tail)
                ex = "%sexists (select 1 from 'b.csv' as b where %s)" % ("not " if jt == "anti" else "", on_s)
                sql = "select %s from 'p.csv' as a where %s%s%s" % (sel, ex, "" if not where else " and (" + where + ")", tail)
            out.append({"query": query, "sql": sql, "total": True, "tag": "group %s" % jt})
    return out
