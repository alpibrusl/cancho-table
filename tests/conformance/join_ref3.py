"""The reference of slice J3 (docs/joins.md sections 6.3, 8.1, 8.2 and 11, and section 18 for the readings): `--where` split into probe, build and after, `--join-where`,
and `--group`, `--agg`, `--sort`, `--order-by`, `--top`, `--limit`, `--from` and `--rename` over the joined rows.

The definition the tool is held to (J2's join, extended):

    1. BUILD, in the build file's order: for each row: `--join-where` (a refusal of a typed cell is the first refusal; a row it rejects is never held); then, for an inner join, the
       build-side conditions of the leading run of `--where` (below); then the key (a typed cell that is not of its type is a refusal); the row is held.
    2. PROBE, in the probe file's order: for each row: the probe-side conditions of the leading run (a row they reject is skipped, its key is never read); then the key; then its joined rows
       (inner, left, semi, anti as J2); each joined row is counted (what `--from` skips in rows mode), then the whole `--where` is evaluated on it (left to right, stopping where the level
       is decided, a typed cell read only when its condition is tried), then it goes to the rows, the groups or the sorter, which read their typed cells (group keys, then aggregates in
       order; the sort keys).
    3. The LEADING RUN: the units of the top-level `and` list (or the one unit) of `--where`, up to the first unit that is not a bare condition or has a type suffix. A unit that is a
       parenthesised group, a `not (...)`, or the whole of an `or` level ends the run (so a plan with a top-level `or` pushes nothing). A condition of the run that names a probe column is
       probe-side; one that names a build column is build-side (pushed for an inner join only; in a left join it stays after the join, as a padded row must keep its probe row for
       `--join-where`'s ON semantics and drop it for `--where`'s WHERE semantics).
    4. After the pass: groups are listed in the order of their keys (text bytewise, `:int` by value), then `--sort`, then `--top` (a window from the start), then `--from` and `--limit` page it.
       `--order-by` is a stable sort of the kept rows (ties in the order of the joined rows), then `--top`, `--from` and `--limit` as for groups.

Cells are `str` here (a byte per character, as `refimpl` reads them). The oracles are SQLite and DuckDB, each given the same tables with a row-number column and asked, in SQL, for the
same answer (`--join-where` is the `ON` clause of the join, `--where` the `WHERE`, build columns of a left join `COALESCE`d to the empty text because `table` has no null).
"""

import os
import re
import shutil
import sqlite3
import tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py

import join_ref as J
import join_ref2 as R
import refimpl as RI

DUCKDB = shutil.which("duckdb")


class Refused(Exception):
    """A refusal the reference predicts: `info` has rule, context, input, column, value, padded, row and line."""

    def __init__(self, **info):
        super().__init__(info.get("rule"))
        self.info = info


# ---- expressions ---------------------------------------------------------------------------------------------------------------------------------------


def leaf(column, op, lit, typed=False, neg=False):
    """A condition `column op lit` ('=', '!=', '<', '<=', '>', '>='); `typed` reads the column as :int."""
    return ("leaf", {"column": column, "kind": "compare", "op": op, "lit": lit, "int": typed, "neg": False})


def leaf_in(column, lits, typed=False, neg=False):
    return ("leaf", {"column": column, "kind": "in", "lits": list(lits), "int": typed, "neg": neg})


def leaf_contains(column, lit, neg=False):
    return ("leaf", {"column": column, "kind": "contains", "lit": lit, "int": False, "neg": neg})


def AND(*kids):
    return ("and", list(kids))


def OR(*kids):
    return ("or", list(kids))


def NOT(kid):
    return ("not", [kid])


def PAREN(kid):
    return ("paren", [kid])


def q(word):
    return "'" + word.replace("'", "''") + "'"


def render(node, top=True):
    """The text of an expression of the grammar of docs/filter.md."""
    kind = node[0]
    if kind == "leaf":
        c = node[1]
        col = c["column"] + (":int" if c["int"] else "")
        if c["kind"] == "compare":
            return "%s %s %s" % (col, c["op"], c["lit"] if c["int"] else q(c["lit"]))
        if c["kind"] == "contains":
            return "%s %scontains %s" % (col, "not " if c["neg"] else "", q(c["lit"]))
        return "%s %sin (%s)" % (col, "not " if c["neg"] else "", ", ".join(l if c["int"] else q(l) for l in c["lits"]))
    if kind == "paren":
        return "(" + render(node[1][0]) + ")"
    if kind == "not":
        return "not (" + render(node[1][0]) + ")"
    return (" %s " % kind).join(render(k) for k in node[1])


def leaves(node):
    if node[0] == "leaf":
        return [node[1]]
    out = []
    for k in node[1]:
        out += leaves(k)
    return out


def eval_node(node, cell_of):
    """refimpl.eval_tree on the tree with parentheses transparent."""
    return RI.eval_tree(strip(node), cell_of)


def strip(node):
    if node[0] == "paren":
        return strip(node[1][0])
    if node[0] == "leaf":
        return node
    return (node[0], [strip(k) for k in node[1]])


def units(node):
    """The units of the top level: the kids of an `and`, else the node alone."""
    if node[0] == "and":
        return node[1]
    return [node]


def lead_run(node):
    """The leading run: the units that are bare text conditions, from the first, up to the first that is not (a typed condition, a group, a `not (...)`, an `or`)."""
    out = []
    for u in units(node):
        if u[0] == "leaf" and not u[1]["int"]:
            out.append(u[1])
        else:
            break
    return out


# ---- the sides ---------------------------------------------------------------------------------------------------------------------------------------


def to_bytes(row):
    return [c.encode("latin-1") for c in row]


def joined_layout(plan, ph, bh):
    """(header, keep, np): the joined header (the probe's names, then the build's kept ones with the prefix), the kept build columns."""
    jt = plan["jtype"]
    dropped = {p[1] for p in plan["pairs"] if p[3]}
    keep = [j for j in range(len(bh)) if j not in dropped] if jt in ("inner", "left") else []
    return list(ph) + [plan.get("prefix", "") + bh[j] for j in keep], keep, len(ph)


def cond_holds(c, cell):
    r = RI.holds(c, cell)
    if r is True or r is False:
        return (not r) if c.get("neg") else r
    return r


def refuse_key(r, side, n, line, header, col, cell, kind):
    raise Refused(rule=r[1], context="join", input=side, column=header[col], value=cell, row=n, line=line, direction=r[2], padded=False)


def line_numbers(rows):
    """The physical line each record of a file of single- and multi-line records begins on (the header is line 1)."""
    out, line = [], 2
    for r in rows:
        out.append(line)
        line += 1 + sum(c.count("\n") for c in r)
    return out


def key_of(row, cols, kinds, side, n, header, line=None):
    out = []
    for c, kind in zip(cols, kinds):
        cell = row[c]
        if kind is None:
            out.append(cell)
        else:
            r = R.typed(cell.encode("latin-1"), kind)
            if r[0] != "ok":
                refuse_key(r, side, n, n + 1 if line is None else line, header, c, cell, kind)
            out.append(r[1])
    return tuple(out)


def int_of(cell):
    v, bad = RI.to_int(cell)
    return v, bad


def rec_len(row):
    """The bytes of the record of `row` in a file written with minimal quoting (the cells of these tables need none)."""
    return len(",".join(row))


def used_names(plan, jheader):
    """The names of the joined header that the plan reads or shows (docs/joins.md section 22): `--select`'s, or every column when the plan selects nothing and does not group; the names of the whole `--where`
    (the conditions of the leading run are evaluated again on the joined row), `--group`, `--agg` and `--order-by`. `--join-where` is not there: it reads the build row before it is held."""
    used = set()
    if plan.get("where") is not None:
        used |= {leaf_["column"] for leaf_ in leaves(plan["where"])}
    if plan.get("group") is not None or plan.get("aggs") is not None:
        used |= {name for name, _ in (plan.get("group") or [])}
        used |= {name for fn, name in (plan.get("aggs") or []) if fn != "count"}
    else:
        used |= set(plan.get("select") or jheader)
    if plan.get("order") is not None:
        used |= {name for name, _, _ in plan["order"]}
    return used


def named_build_columns(plan, ph, bh):
    """The columns of the build's header (indexes) that the plan names: the build columns the joined row shows, whose joined name is used."""
    jheader, keep, np_ = joined_layout(plan, ph, bh)
    used = used_names(plan, jheader)
    return {j for j in keep if plan.get("prefix", "") + bh[j] in used}


def kept_build_columns(plan, ph, bh):
    """The columns of the build record that are held (docs/joins.md section 22), or None when it is held whole: pruning is on when some column that is not a key (the build side of a pair) is not named."""
    named = named_build_columns(plan, ph, bh)
    keys = {p[1] for p in plan["pairs"]}
    if all(j in named for j in range(len(bh)) if j not in keys):
        return None
    return named


def _held_len(row, trace):
    """The bytes of the build record held for `row` (J7, docs/joins.md section 22): the record with the cells of the columns the plan does not name left empty (`trace["keep"]`, indexes into the
    build's header; None holds the whole record)."""
    keep = trace.get("keep")
    if keep is None:
        return rec_len(row)
    return rec_len([c if j in keep else "" for j, c in enumerate(row)])


def execute(plan, P, B, trace=None):
    """('ok', labels, rows) or raises Refused. `P` and `B` are (header, rows) of str cells. The page is already cut (`from`, `limit`, `top`).
    `trace`, a dict, is filled with the accounted bytes of docs/joins.md section 6.2 and 6.3, each as the list of the costs in the order they were incurred: `build` (what each held
    row and each new key cost), `groups` (what each new group cost: 4 bytes of length and the bytes of each key cell, 12 for a typed one), `sort` (the record and 8 integers for each
    sorted row, 2 + 2 * keys of them)."""
    ph, prows = P
    bh, brows = B
    jt = plan["jtype"]
    pairs = plan["pairs"]
    pks, bks, kinds = [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs]
    if trace is not None and "keep" not in trace:
        trace["keep"] = kept_build_columns(plan, ph, bh)
    jheader, keep, np_ = joined_layout(plan, ph, bh)
    where = plan.get("where")
    jw = plan.get("jw")
    # the leading run, split by side
    probe_conds, build_conds = [], []
    if where is not None:
        for c in lead_run(where):
            if c["column"] in jheader[:np_]:
                probe_conds.append(c)
            elif jt == "inner":
                build_conds.append(c)
    # BUILD
    blines, plines = line_numbers(brows), line_numbers(prows)
    index = {}
    for n, row in enumerate(brows, 1):
        if jw is not None:
            v = RI.eval_tree(strip(jw), lambda col, row=row: row[bh.index(col)])
            if isinstance(v, tuple):
                raise Refused(rule=v[1], context="join-where", input="build", column=v[2], value=row[bh.index(v[2])], row=n, line=blines[n - 1], padded=False)
            if not v:
                continue
        if any(not cond_holds(c, row[bh.index(c["column"][len(plan.get("prefix", "")):])]) for c in build_conds):
            continue
        k = key_of(row, bks, kinds, "build", n, bh, blines[n - 1])
        if trace is not None:
            cost = 0
            if jt in ("inner", "left"):
                cost = _held_len(row, trace) + 16
            if k not in index:
                cost += len(R.key_bytes(to_bytes(row), bks, kinds)) + (60 if jt in ("inner", "left") else 44)
            trace.setdefault("build", []).append(cost)
        index.setdefault(k, []).append(row)
    # PROBE
    ordering = plan.get("order") is not None
    group, aggs = plan.get("group"), plan.get("aggs")
    grouping = group is not None or aggs is not None
    state = {}
    held = []
    produced = 0
    out_rows = []
    frm = plan.get("from", 0)
    done = False
    for n, prow in enumerate(prows, 1):
        if any(not cond_holds(c, prow[ph.index(c["column"])]) for c in probe_conds):
            continue
        k = key_of(prow, pks, kinds, "probe", n, ph, plines[n - 1])
        ms = index.get(k, [])
        rows = []
        if jt in ("inner", "left"):
            rows = [(list(prow) + [b[j] for j in keep], False, rec_len(prow) + 1 + _held_len(b, trace or {})) for b in ms]
            if not ms and jt == "left":
                rows = [(list(prow) + [""] * len(keep), True, rec_len(prow) + len(keep))]
        elif (jt == "semi") == bool(ms):
            rows = [(list(prow), False, rec_len(prow))]
        for cells, padded, reclen in rows:
            produced += 1
            if not grouping and not ordering and produced <= frm:
                continue
            if where is not None:
                v = eval_node(where, lambda col, cells=cells: cells[jheader.index(col)])
                if isinstance(v, tuple):
                    col = v[2]
                    raise Refused(rule=v[1], context="where", input="probe", column=col, value=cells[jheader.index(col)], row=n, line=plines[n - 1], padded=padded and jheader.index(col) >= np_)
                if not v:
                    continue

            def refuse_cell(rule_bad, context, col):
                raise Refused(rule=rule_bad, context=context, input="probe", column=col, value=cells[jheader.index(col)], row=n, line=plines[n - 1], padded=padded and jheader.index(col) >= np_)

            if grouping:
                gk = []
                for name, kind in (group or []):
                    cell = cells[jheader.index(name)]
                    if kind == "int":
                        v, bad = int_of(cell)
                        if bad:
                            refuse_cell(bad, "group", name)
                        gk.append(v)
                    else:
                        gk.append(cell)
                if trace is not None and tuple(gk) not in state:
                    trace.setdefault("groups", []).append(sum(4 + (8 if kind == "int" else len(cells[jheader.index(name)])) for name, kind in (group or [])))
                entry = state.setdefault(tuple(gk), {"count": 0, "values": [None] * len(aggs or []), "seen": [set() for _ in (aggs or [])]})
                entry["count"] += 1
                for i, (fn, name) in enumerate(aggs or []):
                    if fn == "count":
                        continue
                    cell = cells[jheader.index(name)]
                    if fn == "distinct":
                        entry["seen"][i].add(cell)
                        continue
                    v, bad = int_of(cell)
                    if bad:
                        refuse_cell(bad, fn, name)
                    cur = entry["values"][i]
                    entry["values"][i] = (cur or 0) + v if fn == "sum" else (v if cur is None else min(cur, v) if fn == "min" else max(cur, v))
            elif ordering:
                for name, desc, typed in plan["order"]:
                    if typed:
                        v, bad = int_of(cells[jheader.index(name)])
                        if bad:
                            refuse_cell(bad, "order-by", name)
                if trace is not None:
                    trace.setdefault("sort", []).append(reclen + 8 * (2 + 2 * len(plan["order"])))
                held.append(cells)
            else:
                if plan.get("limit") is not None and len(out_rows) >= plan["limit"]:
                    done = True                  # the page is full and this row is wanted: the read stops here, the rows after it are not read (docs/select.md)
                    break
                out_rows.append(cells)
        if done:
            break
    labels = list(jheader)
    if grouping:
        glabels = [name for name, _ in (group or [])]
        alabels = ["count" if fn == "count" else "%s:%s" % (fn, name) for fn, name in (aggs or [("count", None)])]
        labels = glabels + alabels
        rows = []
        for key, e in state.items():
            row = [str(x) for x in key]
            for i, (fn, name) in enumerate(aggs or [("count", None)]):
                if fn == "count":
                    row.append(str(e["count"]))
                elif fn == "distinct":
                    row.append(str(len(e["seen"][i])))
                else:
                    row.append(str(e["values"][i]))
            rows.append((key, row))
        rows.sort(key=lambda kr: kr[0])
        rows = [r for _, r in rows]
        sort = plan.get("sort")
        if sort:
            inv = {new: old for old, new in (plan.get("rename") or {}).items()}
            name = sort.lstrip("-")
            name = inv.get(name, name)
            at = labels.index(name)
            numeric = at >= len(glabels) or group[at][1] == "int"
            rows.sort(key=(lambda r: int(r[at])) if numeric else (lambda r: r[at]), reverse=sort.startswith("-"))
    elif ordering:
        rows = list(held)
        for name, desc, typed in reversed(plan["order"]):
            at = jheader.index(name)
            rows.sort(key=(lambda r, at=at: int(r[at])) if typed else (lambda r, at=at: r[at]), reverse=desc)
    else:
        rows = out_rows
        if plan.get("select"):
            idx = [jheader.index(c) for c in plan["select"]]
            rows = [[r[i] for i in idx] for r in rows]
            labels = list(plan["select"])
    if grouping or ordering:
        if plan.get("top"):
            rows = rows[:plan["top"]]
        rows = rows[frm:]
        if ordering and plan.get("select"):
            idx = [jheader.index(c) for c in plan["select"]]
            rows = [[r[i] for i in idx] for r in rows]
            labels = list(plan["select"])
    if plan.get("limit") is not None:
        rows = rows[:plan["limit"]]
    for old, new in (plan.get("rename") or {}).items():
        labels[labels.index(old)] = new
    return "ok", labels, rows


# ---- flags -------------------------------------------------------------------------------------------------------------------------------------------


def group_flag(group):
    return ",".join(name + (":int" if kind == "int" else "") for name, kind in group)


def agg_flag(aggs):
    return ",".join("count" if fn == "count" else "%s:%s" % (fn, name) for fn, name in aggs)


def order_flag(order):
    return ",".join(("-" if desc else "") + name + (":int" if typed else "") for name, desc, typed in order)


def flags_of(plan, ph, bh, fmt="csv"):
    """The flags after the join's: `--join-where`, `--where`, the form, the page."""
    a = []
    if plan.get("jw") is not None:
        a += ["--join-where", render(plan["jw"])]
    if plan.get("where") is not None:
        a += ["--where", render(plan["where"])]
    if plan.get("select"):
        a += ["--select", ",".join(plan["select"])]
    if plan.get("group") is not None:
        a += ["--group", group_flag(plan["group"])]
    if plan.get("aggs") is not None:
        a += ["--agg", agg_flag(plan["aggs"])]
    if plan.get("sort"):
        a += ["--sort", plan["sort"]]
    if plan.get("order") is not None:
        a += ["--order-by", order_flag(plan["order"])]
    if plan.get("rename"):
        a += ["--rename", ",".join("%s=%s" % kv for kv in plan["rename"].items())]
    if plan.get("top"):
        a += ["--top", str(plan["top"])]
    if plan.get("limit") is not None:
        a += ["--limit", str(plan["limit"])]
    if plan.get("from"):
        a += ["--from", str(plan["from"])]
    return a + ["--format", fmt]


def join_flags(plan, ph, bh, jtype=None):
    """`--join b.csv --on ... --join-type ... [--join-prefix ...]`."""
    jt = jtype or plan["jtype"]
    a = ["--join", "b.csv", "--on", R.on_arg([(p[0], p[1], p[2], p[3]) for p in plan["pairs"]], [x.encode() for x in ph], [x.encode() for x in bh]), "--join-type", jt]
    if plan.get("prefix") and jt in ("inner", "left"):
        a += ["--join-prefix", plan["prefix"]]
    return a


def pipe_flags(plan):
    """The second stage of the pipe: the plan after the join, on the joined file (names are the joined header's)."""
    p = dict(plan)
    p["jw"] = None
    return p


# ---- SQL ---------------------------------------------------------------------------------------------------------------------------------------------


def _cast(col, typed, engine):
    if not typed:
        return col
    return "TRY_CAST(%s AS HUGEINT)" % col if engine == "duck" else "CAST(%s AS INTEGER)" % col


def _keycast(col, kind, engine):
    if kind is None:
        return col
    return "CAST(%s AS %s)" % (col, "HUGEINT" if engine == "duck" else "INTEGER")


def sql_node(node, ref, engine):
    kind = node[0]
    if kind == "leaf":
        c = node[1]
        col = _cast(ref(c["column"]), c["int"], engine)
        if c["kind"] == "compare":
            lit = c["lit"] if c["int"] else "'" + c["lit"].replace("'", "''") + "'"
            s = "%s %s %s" % (col, "<>" if c["op"] == "!=" else c["op"], lit)
        elif c["kind"] == "contains":
            s = "instr(%s, '%s') > 0" % (col, c["lit"].replace("'", "''")) if engine == "lite" else "contains(%s, '%s')" % (col, c["lit"].replace("'", "''"))
            if c["lit"] == "":
                s = "1 = 1"
            if c["neg"]:
                s = "NOT (%s)" % s
            return "(%s)" % s
        else:
            lits = ", ".join(l if c["int"] else "'" + l.replace("'", "''") + "'" for l in c["lits"])
            s = "%s %sIN (%s)" % (col, "NOT " if c["neg"] else "", lits)
        return "(%s)" % s
    if kind == "paren":
        return sql_node(node[1][0], ref, engine)
    if kind == "not":
        return "(NOT %s)" % sql_node(node[1][0], ref, engine)
    return "(" + (" %s " % kind.upper()).join(sql_node(k, ref, engine) for k in node[1]) + ")"


def sql_text(plan, P, B, engine):
    """The query that answers the plan in SQL: the join in a derived table (ordered by the row numbers), then WHERE, GROUP BY, ORDER BY. Typed cells are cast by the engine."""
    ph, _ = P
    bh, _ = B
    jt = plan["jtype"]
    pairs = plan["pairs"]
    jheader, keep, np_ = joined_layout(plan, ph, bh)
    cond = " AND ".join("%s = %s" % (_keycast("p.c%d" % pk, kind, engine), _keycast("b.c%d" % bk, kind, engine)) for pk, bk, kind, _ in pairs)
    jwsql = ""
    if plan.get("jw") is not None:
        jwsql = " AND " + sql_node(plan["jw"], lambda name: "b.c%d" % bh.index(name), engine)
    pcols = ["p.c%d AS j%d" % (i, i) for i in range(np_)]
    if jt in ("inner", "left"):
        bcols = ["COALESCE(b.c%d, '') AS j%d" % (j, np_ + i) for i, j in enumerate(keep)]
        inner = "SELECT p.rn AS prn, COALESCE(b.rn, -1) AS brn, %s FROM p %sJOIN b ON %s%s" % (", ".join(pcols + bcols), "LEFT " if jt == "left" else "", cond, jwsql)
    else:
        inner = "SELECT p.rn AS prn, 0 AS brn, %s FROM p WHERE %sEXISTS (SELECT 1 FROM b WHERE %s%s)" % (", ".join(pcols), "NOT " if jt == "anti" else "", cond, jwsql)
    ref = lambda name: "j.j%d" % jheader.index(name)
    wsql = ""
    if plan.get("where") is not None:
        wsql = " WHERE " + sql_node(plan["where"], ref, engine)
    grouping = plan.get("group") is not None or plan.get("aggs") is not None
    if grouping:
        gcols = [ref(name) if kind != "int" else "CAST(%s AS %s)" % (ref(name), "HUGEINT" if engine == "duck" else "INTEGER") for name, kind in (plan.get("group") or [])]
        sel = list(gcols)
        for fn, name in plan.get("aggs") or [("count", None)]:
            if fn == "count":
                sel.append("count(*)")
            elif fn == "distinct":
                sel.append("count(DISTINCT %s)" % ref(name))
            else:
                sel.append("%s(CAST(%s AS %s))" % (fn, ref(name), "HUGEINT" if engine == "duck" else "INTEGER"))
        sql = "SELECT %s FROM (%s) j%s" % (", ".join(sel), inner, wsql)
        if gcols:
            sql += " GROUP BY " + ", ".join(gcols)
        else:
            sql += " HAVING count(*) > 0"
        order = []
        if plan.get("sort"):
            labels = [name for name, _ in (plan.get("group") or [])] + [("count" if fn == "count" else "%s:%s" % (fn, name)) for fn, name in (plan.get("aggs") or [("count", None)])]
            inv = {new: old for old, new in (plan.get("rename") or {}).items()}
            name = plan["sort"].lstrip("-")
            order.append("%d %s" % (1 + labels.index(inv.get(name, name)), "DESC" if plan["sort"].startswith("-") else "ASC"))
        order += [str(i + 1) for i in range(len(gcols))]
        if order:
            sql += " ORDER BY " + ", ".join(order)
        return sql
    cols = [ref(name) for name in (plan.get("select") or jheader)]
    sql = "SELECT %s FROM (%s) j%s" % (", ".join(cols), inner, wsql)
    order = []
    for name, desc, typed in plan.get("order") or []:
        order.append("%s %s" % (_cast(ref(name), typed, engine) if typed else ref(name), "DESC" if desc else "ASC"))
    order += ["j.prn", "j.brn"]
    return sql + " ORDER BY " + ", ".join(order)


def sqlite_answer(plan, P, B):
    """The rows by SQLite (before the page): [[str]] ."""
    ph, prows = P
    bh, brows = B
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE TABLE p (rn INTEGER, %s)" % ", ".join("c%d TEXT" % i for i in range(len(ph))))
        con.execute("CREATE TABLE b (rn INTEGER, %s)" % ", ".join("c%d TEXT" % i for i in range(len(bh))))
        con.executemany("INSERT INTO p VALUES (%s)" % ",".join("?" * (len(ph) + 1)), [(i, *r) for i, r in enumerate(prows)])
        con.executemany("INSERT INTO b VALUES (%s)" % ",".join("?" * (len(bh) + 1)), [(i, *r) for i, r in enumerate(brows)])
        sql = sql_text(plan, P, B, "lite")
        return [[("" if c is None else str(c)) for c in row] for row in con.execute(sql)], sql
    finally:
        con.close()


def duckdb_answer(plan, P, B):
    if not DUCKDB:
        return None, None
    ph, prows = P
    bh, brows = B
    with tempfile.TemporaryDirectory(prefix="duck3-") as d:
        pp, bp = os.path.join(d, "p.csv"), os.path.join(d, "b.csv")
        J.write_csv(pp, [b"rn"] + [b"c%d" % i for i in range(len(ph))], [[b"%d" % i] + to_bytes(r) for i, r in enumerate(prows)])
        J.write_csv(bp, [b"rn"] + [b"c%d" % i for i in range(len(bh))], [[b"%d" % i] + to_bytes(r) for i, r in enumerate(brows)])
        opts = "header=true, all_varchar=true, nullstr='@@no-such-cell@@', quote='\"', escape='\"', auto_detect=false, columns={%s}"
        pcols = ", ".join(["'rn': 'BIGINT'"] + ["'c%d': 'VARCHAR'" % i for i in range(len(ph))])
        bcols = ", ".join(["'rn': 'BIGINT'"] + ["'c%d': 'VARCHAR'" % i for i in range(len(bh))])
        sql = sql_text(plan, P, B, "duck")
        pre = "CREATE TABLE p AS SELECT * FROM read_csv('%s', %s); CREATE TABLE b AS SELECT * FROM read_csv('%s', %s); " % (pp, opts % pcols, bp, opts % bcols)
        full = pre + "COPY (%s) TO '/dev/stdout' (FORMAT CSV, HEADER false);" % sql
        p = subprocess.run([DUCKDB, "-c", full], capture_output=True, timeout=300)
        if p.returncode:
            raise AssertionError("duckdb: %s\n%s" % (p.stderr.decode()[:400], sql))
        return [[c.decode("latin-1") for c in r] for r in J.read_csv_bytes(p.stdout)], sql


def page(rows, plan):
    """The page of the SQL answer: top, from, limit (rows mode counts `from` before the filter, so the comparison with SQL is for plans where that is the same)."""
    if plan.get("top"):
        rows = rows[:plan["top"]]
    rows = rows[plan.get("from", 0):]
    if plan.get("limit") is not None:
        rows = rows[:plan["limit"]]
    return rows


def sql_comparable(plan):
    """Whether the SQL answer, paged here, is the answer the tool gives: in rows mode `--from` skips joined rows before `--where`, and the leading run is cut before the join's rows are counted, so SQL
    (which filters first) is the oracle for plans with no `--from`, or no `--where`."""
    grouping = plan.get("group") is not None or plan.get("aggs") is not None
    if plan.get("from") and not grouping and plan.get("order") is None and plan.get("where") is not None:
        return False
    return True


# ---- generators ------------------------------------------------------------------------------------------------------------------------------------------

GDOM = ["x", "y", "z"]
KDOM = ["k%d" % i for i in range(6)]
INTSP = ["7", "007", "+7", "-0", "0", "12", "-3", "5"]


def _v(rng):
    n = rng.randint(-5, 20)
    return "%03d" % n if n >= 0 and rng.random() < 0.2 else str(n)


def _rows(rng, n, keys, key_at, gempty=0.0, vempty=0.0):
    """`n` rows of 4 cells, the key in column `key_at` and (g, v, t) in the other three, in order."""
    out = []
    for _ in range(n):
        g = rng.choice(GDOM + [""]) if rng.random() < gempty else rng.choice(GDOM)
        v = "" if rng.random() < vempty else _v(rng)
        rest = [g, v, "t%d" % rng.randint(0, 5)]
        row = rest[:key_at] + [rng.choice(keys)] + rest[key_at:]
        out.append(row)
    return out


def _names(base, key_at):
    """The 4 names `base` (key first, then g, v, t) with the key moved to position `key_at`."""
    return base[1:][:key_at] + [base[0]] + base[1:][key_at:]


class Case:
    """Two tables and the way they are joined (the names of a column end in its role: k key, g group-like text, v int, t text)."""

    def __init__(self, name, P, B, pairs, prefix="", using=False):
        self.name, self.P, self.B, self.pairs, self.prefix, self.using = name, P, B, pairs, prefix, using
        self.awkward = False


def shapes(seed=11):
    rng = random.Random(seed)
    out = []

    def tables(np_, nb, kp=KDOM, kb=KDOM, pk_at=0, bk_at=0, same=False, popts=None, bopts=None):
        pn = ["k", "g", "v", "t"] if same else ["pk", "pg", "pv", "pt"]
        bn = ["k", "g", "v", "t"] if same else ["bk", "bg", "bv", "bt"]
        return ((_names(pn, pk_at), _rows(rng, np_, kp, pk_at, **(popts or {}))), (_names(bn, bk_at), _rows(rng, nb, kb, bk_at, **(bopts or {}))))

    def add(name, PB, pairs, prefix="", using=False):
        out.append(Case(name, PB[0], PB[1], pairs, prefix, using))

    add("duplicates on both sides", tables(60, 40), [(0, 0, None, False)])
    add("no matches", tables(40, 30, KDOM, ["k%d" % i for i in range(6, 10)]), [(0, 0, None, False)])
    add("a left join pads", tables(50, 30, KDOM, KDOM[:3]), [(0, 0, None, False)])
    add("empty probe", tables(0, 20), [(0, 0, None, False)])
    add("empty build", tables(20, 0), [(0, 0, None, False)])
    add("both empty", tables(0, 0), [(0, 0, None, False)])
    add("fan-out", tables(60, 60, ["hot", "hot", "hot", "k1"], ["hot", "hot", "k1", "k2"]), [(0, 0, None, False)])
    add("the key is not in the first column", tables(40, 30, pk_at=2, bk_at=1), [(2, 1, None, False)])
    add("empty group cells", tables(40, 30, popts={"gempty": 0.3}, bopts={"gempty": 0.3}), [(0, 0, None, False)])
    add("empty cells in the int column", tables(40, 30, popts={"vempty": 0.15}, bopts={"vempty": 0.15}), [(0, 0, None, False)])
    add("typed int key with spellings", tables(50, 30, INTSP, INTSP[:6]), [(0, 0, "int", False)])
    add("two pairs of text", tables(60, 40), [(0, 0, None, False), (1, 1, None, False)])
    add("same names with a prefix", tables(50, 40, same=True), [(0, 0, None, False)], prefix="r_")
    add("using with a prefix", tables(50, 40, same=True), [(0, 0, None, True)], prefix="r_", using=True)
    add("big fan-out", tables(300, 300, ["a", "a", "a", "b"], ["a", "a", "b", "c"]), [(0, 0, None, False)])
    # cells with a comma, a quote and a line break in the group and the text columns: quoted records, one of them over two lines
    P, B = tables(40, 30)
    odd = ["a,b", 'say "hi"', "two\nlines", "x", "y", ""]
    for rows in (P[1], B[1]):
        for r in rows:
            r[1] = rng.choice(odd)
            r[3] = rng.choice(["q\"q", "multi\nline", "t1", "t2"])
    case = Case("awkward cells in quoted records", P, B, [(0, 0, None, False)])
    case.awkward = True
    out.append(case)
    return out


def case_layout(case, jt):
    """(roles, jheader): the role of each joined name is its last letter."""
    ph, _ = case.P
    bh, _ = case.B
    plan = {"jtype": jt, "pairs": case.pairs, "prefix": case.prefix}
    jheader, keep, np_ = joined_layout(plan, ph, bh)
    return {n: n[-1] for n in jheader}, jheader


def _text_leaf(rng, name, role):
    dom = {"g": GDOM + [""], "t": ["t%d" % i for i in range(6)], "k": KDOM + [""]}[role]
    kind = rng.choice(["=", "=", "!=", "<", ">=", "in", "contains", "notin", "notcontains"])
    if kind in ("=", "!=", "<", ">="):
        return leaf(name, kind, rng.choice(dom))
    if kind == "in":
        return leaf_in(name, rng.sample(dom, 2))
    if kind == "notin":
        return leaf_in(name, rng.sample(dom, 2), neg=True)
    return leaf_contains(name, rng.choice(["x", "t", "1", "k"]), neg=kind == "notcontains")


def _int_leaf(rng, name):
    if rng.random() < 0.2:
        return leaf_in(name, [str(rng.randint(-5, 20)) for _ in range(2)], typed=True)
    return leaf(name, rng.choice(["=", "!=", "<", "<=", ">", ">="]), str(rng.randint(-5, 20)), typed=True)


def random_unit(rng, names_roles):
    """One bare condition: text on a g, t or k column, or :int on a v column; sometimes the guard pair `c != '' and c:int ...` (answered as a list of two units)."""
    pool = [(n, r) for n, r in names_roles if r in ("g", "t", "k")]
    ints = [n for n, r in names_roles if r == "v"]
    if ints and (rng.random() < 0.4 or not pool):
        n = rng.choice(ints)
        if rng.random() < 0.25:
            return [leaf(n, "!=", ""), _int_leaf(rng, n)]
        return [_int_leaf(rng, n)]
    n, r = rng.choice(pool)
    return [_text_leaf(rng, n, r)]


def _one(rng, names_roles):
    u = random_unit(rng, names_roles)
    return u[0] if len(u) == 1 else PAREN(AND(*u))


def random_expr(rng, names_roles):
    t = rng.random()
    if t < 0.22:
        u = random_unit(rng, names_roles)
        return AND(*u) if len(u) > 1 else u[0]
    if t < 0.5:
        kids = []
        for _ in range(rng.randint(2, 3)):
            kids += random_unit(rng, names_roles)
        return AND(*kids)
    if t < 0.62:
        return OR(_one(rng, names_roles), _one(rng, names_roles))
    if t < 0.72:
        return AND(PAREN(OR(_one(rng, names_roles), _one(rng, names_roles))), _one(rng, names_roles))
    if t < 0.8:
        return AND(_one(rng, names_roles), PAREN(OR(_one(rng, names_roles), _one(rng, names_roles))))
    if t < 0.88:
        return AND(_one(rng, names_roles), NOT(AND(_one(rng, names_roles), _one(rng, names_roles))))
    if t < 0.94:
        return AND(PAREN(_one(rng, names_roles)), _one(rng, names_roles))
    return NOT(OR(_one(rng, names_roles), _one(rng, names_roles)))


def labels_of(plan, jheader):
    if plan.get("group") is not None or plan.get("aggs") is not None:
        return [n for n, _ in (plan.get("group") or [])] + [("count" if fn == "count" else "%s:%s" % (fn, c)) for fn, c in (plan.get("aggs") or [("count", None)])]
    return list(plan.get("select") or jheader)


def random_plan(rng, case, jt):
    roles, jheader = case_layout(case, jt)
    names_roles = [(n, roles[n]) for n in jheader]
    build_roles = [(n, n[-1]) for n in case.B[0]]
    plan = {"jtype": jt, "pairs": case.pairs, "prefix": case.prefix}
    if rng.random() < 0.35:
        plan["jw"] = random_expr(rng, build_roles)
    if rng.random() < 0.7:
        plan["where"] = random_expr(rng, names_roles)
    form = rng.random()
    ints = [n for n, r in names_roles if r == "v"]
    texts = [n for n, r in names_roles if r in ("g", "t", "k")]
    if form < 0.45:
        group = [(n, "text") for n in rng.sample(texts, min(rng.choice([0, 1, 1, 2]), len(texts)))]
        if rng.random() < 0.15 and ints:
            group.append((rng.choice(ints), "int"))
        aggs = []
        for _ in range(rng.choice([0, 1, 2, 3])):
            fn = rng.choice(["count", "sum", "min", "max", "distinct"])
            a = ("count", None) if fn == "count" else ("distinct", rng.choice(texts)) if fn == "distinct" else (fn, rng.choice(ints)) if ints else ("count", None)
            if a not in aggs:
                aggs.append(a)
        if not group and not aggs:
            aggs = [("count", None)]
        if group:
            plan["group"] = group
        if aggs:
            plan["aggs"] = aggs
        labels = labels_of(plan, jheader)
        if rng.random() < 0.4:
            plan["sort"] = ("-" if rng.random() < 0.5 else "") + rng.choice(labels)
        if rng.random() < 0.35:
            plan["top"] = rng.randint(1, 4)
    elif form < 0.7:
        keys = []
        for name, role in rng.sample(names_roles, rng.randint(1, 2)):
            keys.append((name, rng.random() < 0.4, role == "v" and rng.random() < 0.8))
        plan["order"] = keys
        if rng.random() < 0.4:
            plan["top"] = rng.randint(1, 6)
        if rng.random() < 0.3:
            plan["select"] = rng.sample(jheader, rng.randint(1, min(3, len(jheader))))
    else:
        if rng.random() < 0.5:
            plan["select"] = rng.sample(jheader, rng.randint(1, min(4, len(jheader))))
    if rng.random() < 0.3:
        plan["limit"] = rng.randint(1, 8)
    if rng.random() < 0.25:
        plan["from"] = rng.randint(1, 6)
    if rng.random() < 0.15:
        labels = labels_of(plan, jheader)
        old = rng.choice(labels)
        if len(set(labels)) == len(labels):
            plan["rename"] = {old: "z_" + old.replace(":", "_")}
            if plan.get("sort") and plan["sort"].lstrip("-") == old:
                plan["sort"] = ("-" if plan["sort"].startswith("-") else "") + plan["rename"][old]
    return plan


import random  # noqa: E402  (the generators take the rng they are given)
