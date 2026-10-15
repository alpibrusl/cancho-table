"""The random tables and plans of the equivalence gate of `--query` (docs/query.md section 8), rendered twice by two pieces of code that share nothing
with the tool: `flag_form` (the typed flags, hand-written as the repo's other generators write them) and `query_form` (SQL text, with random keyword case
and spacing). A case is a random table (awkward header names: spaces, commas, quotes, #, colons, at-signs, keywords, a dot, non-ASCII; quoted fields with commas,
quotes and newlines; empty cells; integers, decimals and text) and a random plan. It was the spike scripts/spikes/query_gate.py of the design.
"""
import csv
import io
import re
import json
import os
import random
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile

import query_ref as qp

AWKWARD = ["like", "null", "-dash", "a b", "x,y", "#3", "and", "select", "count", "it's", 'q"d', "a:int", "b@2", "héllo", "k.v", "from", "group", "limit", "Mixed Case", "1st", "x\\y", "in", "contains", "order", "sum:x"]
PLAIN = ["id", "status", "bytes", "name", "price", "city", "n1", "n2", "t"]


def make_table(rnd):
    """-> (header names, kinds, rows). kinds: int | dec | text."""
    critical = ["a:int", "-dash", "x,y", "#3", "b@2", "x\\y", "it's"]       # names whose escape or quote rule differs per flag: two in every table
    names = rnd.sample(critical, 2) + rnd.sample([a for a in AWKWARD if a not in critical], rnd.randint(0, 3)) + rnd.sample(PLAIN, rnd.randint(2, 4))
    rnd.shuffle(names)
    kinds = [rnd.choice(["int", "dec", "text", "text"]) for _ in names]
    rows = []
    for _ in range(rnd.randint(0, 25)):
        row = []
        for k in kinds:
            if rnd.random() < 0.06:
                row.append("")
            elif k == "int":
                row.append(str(rnd.choice([0, 1, 2, 5, 7, 42, 100, 404, 500, -3, 9999999999, rnd.randint(0, 1000)])))
            elif k == "dec":
                row.append(rnd.choice(["1.5", "12.50", "0.25", "3", "-2.75", "100.1", "7.00"]))
            else:
                row.append(rnd.choice(["a", "b", "ab", "Zed", "x y", "o'k", 'q"t', "c,d", "line\nbreak", "é", "", "#1", "10", "9", "200"]))
        rows.append(row)
    return names, kinds, rows


def write_csv(path, names, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(names)
        w.writerows(rows)


# ---- the plan --------------------------------------------------------------------------------------------------------------

def col_ref(rnd, names, i):
    """(name or position) of column i."""
    if rnd.random() < 0.12:
        return ("pos", i + 1)
    return ("name", names[i])


def lit_from(rnd, rows, i, ty):
    """a literal that is a cell of column i (so that conditions often keep some rows, and every value of an `in` list can matter)."""
    if not rows or rnd.random() < 0.4:
        return None
    c = rnd.choice(rows)[i]
    if ty == "int":
        return ("num", c) if re.fullmatch(r"-?[0-9]+", c) else None
    if ty == "dec":
        return ("num", c) if re.fullmatch(r"-?[0-9]+(\.[0-9]{1,2})?", c) else None
    return ("str", c)


def lit(rnd, kind, ty):
    if ty == "int":
        return ("num", str(rnd.choice([0, 1, 5, 100, 404, -3, 42])))
    if ty == "dec":
        return ("num", rnd.choice(["1.5", "12.50", "0", "-2.75", "3", "0.125"]))
    if kind == "int" and rnd.random() < 0.5:
        return ("num", str(rnd.choice([0, 1, 100, 404])))
    return ("str", rnd.choice(["a", "b", "x y", "o'k", 'q"t', "c,d", "10", "9", "200", "", "é", "#1", "and", "a\\b"]))


def make_shape(rnd, idxs, kind=None):
    """How the conditions `idxs` (in the order they are written) combine: an index, or ("and" | "or", [parts]) with parts of the other connective (mostly), 2 to 3 of them."""
    if len(idxs) == 1:
        return idxs[0]
    kind = kind or rnd.choice(["and", "or"])
    k = rnd.randint(2, min(3, len(idxs)))
    cuts = sorted(rnd.sample(range(1, len(idxs)), k - 1))
    parts = []
    for a, b in zip([0] + cuts, cuts + [len(idxs)]):
        parts.append(make_shape(rnd, idxs[a:b], ("or" if kind == "and" else "and") if rnd.random() < 0.85 else kind))
    return (kind, parts)


def render_shape(shape, texts, connective):
    """The where text of a shape: `texts[i]` for condition i, `connective(kind)` between the parts of a level, parentheses round a level inside a level of the other connective
    and round some of its own kind."""
    if isinstance(shape, int):
        return texts[shape]
    kind, parts = shape
    out = []
    for part in parts:
        t = render_shape(part, texts, connective)
        if not isinstance(part, int) and (part[0] != kind or len(t) % 2 == 0):
            t = "(" + t + ")"
        out.append(t)
    return connective(kind).join(out)


def make_plan(rnd, names, kinds, rows=(), many=False):
    n = len(names)
    awk = [j for j in range(n) if names[j] in AWKWARD]

    def pick():
        # half of the picks are a column with an awkward name, so that every escape rule is exercised by every role
        return rnd.choice(awk) if awk and rnd.random() < 0.5 else rnd.randrange(n)

    plan = {"conds": [], "group": [], "aggs": [], "order": [], "limit": None, "offset": None, "star": False, "select": []}
    for _ in range(rnd.choice([0, 0, 1, 1, 2, 3, 3, 4, 6] if many else [0, 0, 1, 1, 2, 3])):
        i = pick()
        ty = None
        if kinds[i] == "int" and rnd.random() < 0.6:
            ty = ("int", None)
        elif kinds[i] == "dec" and rnd.random() < 0.6:
            ty = ("dec", rnd.choice([2, 2, 3]))
        op = rnd.choice(["=", "!=", "<", "<=", ">", ">=", "in", "contains", "<>"])
        if ty and op == "contains":
            op = "="
        t0 = ty[0] if ty else None
        mk = lambda: lit_from(rnd, rows, i, t0) or lit(rnd, kinds[i], t0)
        vals = [mk() for _ in range(rnd.randint(2, 3))] if op == "in" else [mk()]
        plan["conds"].append({"col": col_ref(rnd, names, i), "ty": ty, "op": op, "vals": vals})
    plan["shape"] = make_shape(rnd, list(range(len(plan["conds"])))) if len(plan["conds"]) >= 2 and rnd.random() < (0.75 if many else 0.5) else None
    if rnd.random() < 0.45:
        plan["mode"] = "group"
        for i in dict.fromkeys(pick() for _ in range(rnd.choice([0, 1, 1, 2]))):
            plan["group"].append(col_ref(rnd, names, i))
        plan["aggs"].append({"fn": "count"})
        for _ in range(rnd.choice([0, 1, 2])):
            # a header that starts with # cannot be named in --agg: `max:\#3` reads the position (an engine bug the first run of this gate
            # found, docs/query.md section 9): such a column is not aggregated by name here
            ok = [j for j in range(n) if not names[j].startswith("#")]
            i = pick() if not names[pick()].startswith("#") else rnd.choice(ok)
            if names[i].startswith("#"):
                i = rnd.choice(ok)
            fn = rnd.choice(["sum", "min", "max", "mean", "distinct", "distinct"])
            ty = None
            if kinds[i] == "dec":
                ty = ("dec", rnd.choice([2, 2, 3]))
            elif kinds[i] == "int":
                ty = ("int", None) if rnd.random() < 0.5 else None
            a = {"fn": fn, "col": col_ref(rnd, names, i), "ty": ty, "scale": None}
            if fn == "mean":
                if ty is None or ty[0] == "int":
                    a["scale"] = rnd.randint(0, 3)
                elif rnd.random() < 0.5:
                    a["scale"] = rnd.randint(2, 4)
            if fn in ("sum", "min", "max") and ty is None and kinds[i] != "int":
                a["ty"] = None   # untyped sum/min/max read integers: the table refuses non-integers (a refusal case, on purpose)
            plan["aggs"].append(a)
        if rnd.random() < 0.5:
            outs = [("agg", a) for a in plan["aggs"] if a["fn"] == "count" or (a["col"][0] == "name" and not a["col"][1].startswith("-"))]
            outs += [("col", c) for c in plan["group"] if c[0] == "name" and not c[1].startswith("-")]
            if outs:
                plan["order"] = [(rnd.choice(outs), rnd.random() < 0.5)]
    else:
        plan["mode"] = "rows"
        if rnd.random() < 0.3 and (plan["conds"] or True):
            plan["star"] = True
        else:
            for i in [pick() for _ in range(rnd.randint(1, 3))]:
                plan["select"].append(col_ref(rnd, names, i))
        for _ in range(rnd.choice([0, 0, 1, 2])):
            i = pick()
            ty = ("int", None) if kinds[i] == "int" and rnd.random() < 0.5 else None
            plan["order"].append((("col", col_ref(rnd, names, i), ty), rnd.random() < 0.5))
    if rnd.random() < 0.3:
        plan["limit"] = rnd.choice([1, 2, 5, 100])
    if rnd.random() < 0.15:
        plan["offset"] = rnd.choice([0, 1, 3])
    return plan


# ---- the flag form: the typed flags, written by hand --------------------------------------------------------------------------

def lst(c):
    if c[0] == "pos":
        return "#%d" % c[1]
    s = c[1].replace("\\", "\\\\").replace(",", "\\,")
    return "\\" + s if s.startswith("#") else s


def ordname(c):
    if c[0] == "pos":
        return "#%d" % c[1]
    s = c[1].replace("\\", "\\\\").replace(",", "\\,").replace(":", "\\:")
    return "\\" + s if s[:1] in ("#", "-") else s


def aggname(c):
    return lst(c).replace(":", "\\:").replace("@", "\\@")


def tys(ty):
    return "" if not ty else (":int" if ty[0] == "int" else ":dec(%d)" % ty[1])


def flag_form(plan):
    a = []
    if plan["mode"] == "group":
        if plan["group"]:
            a += ["--group", ",".join(lst(c) for c in plan["group"])]
        items = []
        for g in plan["aggs"]:
            if g["fn"] == "count":
                items.append("count")
            else:
                s = "%s:%s%s" % (g["fn"], aggname(g["col"]), tys(g["ty"]))
                if g["scale"] is not None:
                    s += "@%d" % g["scale"]
                items.append(s)
        a += ["--agg", ",".join(items)]
    elif not plan["star"]:
        a += ["--select", ",".join(lst(c) for c in plan["select"])]
    elif not plan["conds"] and not plan["order"]:
        a += ["--rows"]
    if plan["conds"]:
        parts = []
        for c in plan["conds"]:
            col = c["col"]
            w = ("#%d" % col[1]) if col[0] == "pos" else "'" + col[1].replace("'", "''") + "'"
            w += tys(c["ty"])
            vs = [v[1] if v[0] == "num" else "'" + v[1].replace("'", "''") + "'" for v in c["vals"]]
            op = "!=" if c["op"] == "<>" else c["op"]
            parts.append("%s in (%s)" % (w, ", ".join(vs)) if op == "in" else "%s %s %s" % (w, op, vs[0]))
        a += ["--where", " and ".join(parts) if plan.get("shape") is None else render_shape(plan["shape"], parts, lambda k: " %s " % k)]
    if plan["order"]:
        if plan["mode"] == "group":
            (kind, v), desc = plan["order"][0]
            if kind == "agg":
                key = "count" if v["fn"] == "count" else "%s:%s" % (v["fn"], v["col"][1] if v["col"][0] == "name" else "#%d" % v["col"][1])
            else:
                key = v[1] if v[0] == "name" else "#%d" % v[1]
            if plan.get("renames"):
                # --sort names the label after the rename (docs/or-and-columns.md 8.2): the output the key is, renamed
                idx = order_output_index(plan, kind, v)
                if idx in plan["renames"]:
                    key = plan["renames"][idx]
            a += ["--sort", ("-" if desc else "") + key]
        else:
            ks = []
            for (kind, col, ty), desc in plan["order"]:
                ks.append(("-" if desc else "") + ordname(col) + tys(ty))
            a += ["--order-by", ",".join(ks)]
    if plan.get("renames"):
        a += ["--rename", ",".join("#%d=%s" % (i + 1, rename_new(new)) for i, new in sorted(plan["renames"].items()))]
    if plan["limit"] is not None:
        a += ["--limit", str(plan["limit"])]
    if plan["offset"] is not None:
        a += ["--from", str(plan["offset"])]
    return a


def rename_new(new):
    s = new.replace("\\", "\\\\").replace(",", "\\,")
    return "\\" + s if s.startswith("#") else s


def order_output_index(plan, kind, v):
    """The place in the answer of the output a grouping is ordered by (group columns, then the aggregates)."""
    if kind == "agg":
        return len(plan["group"]) + [i for i, g in enumerate(plan["aggs"]) if g is v][0]
    return plan["group"].index(v)


# ---- the query form: SQL text, with random case and spacing -------------------------------------------------------------------

def kw(rnd, w):
    return rnd.choice([w, w.upper(), w.capitalize()])


def ident(c):
    if c[0] == "pos":
        return "#%d" % c[1]
    n = c[1]
    if qp.IDENT.fullmatch(n) and n.lower() not in qp.RESERVED and n.lower() not in qp.FUTURE:
        return n
    return '"' + n.replace('"', '""') + '"'


def tyq(rnd, c, ty):
    s = ident(c)
    if not ty:
        return s
    t = "int" if ty[0] == "int" else "dec(%d)" % ty[1]
    return "cast(%s as %s)" % (s, t) if rnd.random() < 0.5 else "%s::%s" % (s, t)


def litq(v):
    return v[1] if v[0] == "num" else "'" + v[1].replace("'", "''") + "'"


def alias_name(new):
    if qp.IDENT.fullmatch(new) and new.lower() not in qp.RESERVED and new.lower() not in qp.FUTURE:
        return new
    return '"' + new.replace('"', '""') + '"'


def query_form(rnd, plan, source):
    sp = lambda: rnd.choice([" ", " ", "  ", "\n", "\t"])
    renames = plan.get("renames") or {}
    as_ = lambda i: (" " + kw(rnd, "as") + " " + alias_name(renames[i])) if i in renames else ""
    p = [kw(rnd, "select")]
    if plan["mode"] == "group":
        items = [ident(c) for c in plan["group"]]
        for g in plan["aggs"]:
            if g["fn"] == "count":
                items.append("count(*)")
            elif g["fn"] == "distinct":
                items.append("count(distinct %s)" % tyq(rnd, g["col"], g["ty"]))
            elif g["fn"] == "mean" and g["scale"] is not None:
                items.append("mean(%s, %d)" % (tyq(rnd, g["col"], g["ty"]), g["scale"]))
            else:
                items.append("%s(%s)" % (g["fn"], tyq(rnd, g["col"], g["ty"])))
        p.append((", " if rnd.random() < 0.7 else " , ").join(it + as_(i) for i, it in enumerate(items)))
    elif plan["star"]:
        p.append("*")
    else:
        p.append(", ".join(ident(c) + as_(i) for i, c in enumerate(plan["select"])))
    if source:
        p += [kw(rnd, "from"), source]
    if plan["conds"]:
        parts = []
        for c in plan["conds"]:
            w = tyq(rnd, c["col"], c["ty"])
            if c["op"] == "in":
                parts.append("%s %s (%s)" % (w, kw(rnd, "in").lower() if False else kw(rnd, "in"), ", ".join(litq(v) for v in c["vals"])))
            elif c["op"] == "contains":
                parts.append("%s %s %s" % (w, kw(rnd, "contains"), litq(c["vals"][0])))
            else:
                parts.append("%s %s %s" % (w, c["op"], litq(c["vals"][0])))
        p += [kw(rnd, "where"), (" " + kw(rnd, "and") + " ").join(parts) if plan.get("shape") is None else render_shape(plan["shape"], parts, lambda k: " " + kw(rnd, k) + " ")]
    if plan["mode"] == "group" and plan["group"]:
        p += [kw(rnd, "group"), kw(rnd, "by"), ", ".join(ident(c) for c in plan["group"])]
    if plan["order"]:
        p += [kw(rnd, "order"), kw(rnd, "by")]
        if plan["mode"] == "group":
            (kind, v), desc = plan["order"][0]
            if kind == "agg":
                if v["fn"] == "count":
                    k = "count(*)"
                elif v["fn"] == "distinct":
                    k = "count(distinct %s)" % tyq(rnd, v["col"], v["ty"])
                elif v["fn"] == "mean" and v["scale"] is not None:
                    k = "mean(%s, %d)" % (tyq(rnd, v["col"], v["ty"]), v["scale"])
                else:
                    k = "%s(%s)" % (v["fn"], tyq(rnd, v["col"], v["ty"]))
            else:
                k = ident(v)
            if renames and order_output_index(plan, kind, v) in renames and rnd.random() < 0.6:
                k = alias_name(renames[order_output_index(plan, kind, v)])      # an alias is a name `order by` takes (and the one that wins)
            p.append(k + (" " + kw(rnd, "desc") if desc else (" " + kw(rnd, "asc") if rnd.random() < 0.3 else "")))
        else:
            p.append(", ".join(tyq(rnd, col, ty) + (" " + kw(rnd, "desc") if desc else (" " + kw(rnd, "asc") if rnd.random() < 0.3 else "")) for (kind, col, ty), desc in plan["order"]))
    if plan["limit"] is not None:
        p += [kw(rnd, "limit"), str(plan["limit"])]
    if plan["offset"] is not None:
        p += [kw(rnd, "offset"), str(plan["offset"])]
    return sp().join(p)


