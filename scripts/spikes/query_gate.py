"""SPIKE (throwaway, docs/query.md): the equivalence gate, prototyped against the REAL `table` binary and the Python prototype
translator (query_proto.py). The gate of stage Q1 is this, with the cancho parser in place of the prototype.

    python3 query_gate.py --table build/table [--cases N] [--seed S]       run the gate
    python3 query_gate.py --table build/table --mutants                    apply each mutant of query_proto.py; each must be killed

A case is a random table (awkward header names: spaces, commas, quotes, #, colons, at-signs, keywords, a dot, non-ASCII; quoted fields with
commas, quotes and newlines; empty cells; integers, decimals and text) and a random PLAN. The plan is rendered twice by two pieces of code that
share nothing: `flag_form` (the typed flags, hand-written here as the test generators of this repo write them) and `query_form` (SQL text,
with random keyword case and spacing). The query is translated by the prototype to flags and both argvs are run on the same file. Required:
the same exit status, the same stdout, the same stderr (or, in CSV, the same bytes), for every case, refusals included. A case where the
plan is not expressible in a form (see `expressible`) is not rendered in it, and is counted."""
import csv
import io
import re
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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


def make_plan(rnd, names, kinds, rows=()):
    n = len(names)
    awk = [j for j in range(n) if names[j] in AWKWARD]

    def pick():
        # half of the picks are a column with an awkward name, so that every escape rule is exercised by every role
        return rnd.choice(awk) if awk and rnd.random() < 0.5 else rnd.randrange(n)

    plan = {"conds": [], "group": [], "aggs": [], "order": [], "limit": None, "offset": None, "star": False, "select": []}
    for _ in range(rnd.choice([0, 0, 1, 1, 2, 3])):
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


def expressible_in_flags(plan, names):
    """a plan with no limit and star-only rows has no flag form (`--rows` does not exist yet); positions out of range are fine (refused)."""
    return not (plan["mode"] == "rows" and plan["star"] and not plan["conds"] and not plan["order"])


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
    if plan["conds"]:
        parts = []
        for c in plan["conds"]:
            col = c["col"]
            w = ("#%d" % col[1]) if col[0] == "pos" else "'" + col[1].replace("'", "''") + "'"
            w += tys(c["ty"])
            vs = [v[1] if v[0] == "num" else "'" + v[1].replace("'", "''") + "'" for v in c["vals"]]
            op = "!=" if c["op"] == "<>" else c["op"]
            parts.append("%s in (%s)" % (w, ", ".join(vs)) if op == "in" else "%s %s %s" % (w, op, vs[0]))
        a += ["--where", " and ".join(parts)]
    if plan["order"]:
        if plan["mode"] == "group":
            (kind, v), desc = plan["order"][0]
            if kind == "agg":
                key = "count" if v["fn"] == "count" else "%s:%s" % (v["fn"], v["col"][1] if v["col"][0] == "name" else "#%d" % v["col"][1])
            else:
                key = v[1] if v[0] == "name" else "#%d" % v[1]
            a += ["--sort", ("-" if desc else "") + key]
        else:
            ks = []
            for (kind, col, ty), desc in plan["order"]:
                ks.append(("-" if desc else "") + ordname(col) + tys(ty))
            a += ["--order-by", ",".join(ks)]
    if plan["limit"] is not None:
        a += ["--limit", str(plan["limit"])]
    if plan["offset"] is not None:
        a += ["--from", str(plan["offset"])]
    return a


# ---- the query form: SQL text, with random case and spacing -------------------------------------------------------------------

def kw(rnd, w):
    return rnd.choice([w, w.upper(), w.capitalize()])


def ident(c):
    import query_proto as qp
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


def query_form(rnd, plan, source):
    sp = lambda: rnd.choice([" ", " ", "  ", "\n", "\t"])
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
        p.append((", " if rnd.random() < 0.7 else " , ").join(items))
    elif plan["star"]:
        p.append("*")
    else:
        p.append(", ".join(ident(c) for c in plan["select"]))
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
        p += [kw(rnd, "where"), (" " + kw(rnd, "and") + " ").join(parts)]
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
            p.append(k + (" " + kw(rnd, "desc") if desc else (" " + kw(rnd, "asc") if rnd.random() < 0.3 else "")))
        else:
            p.append(", ".join(tyq(rnd, col, ty) + (" " + kw(rnd, "desc") if desc else (" " + kw(rnd, "asc") if rnd.random() < 0.3 else "")) for (kind, col, ty), desc in plan["order"]))
    if plan["limit"] is not None:
        p += [kw(rnd, "limit"), str(plan["limit"])]
    if plan["offset"] is not None:
        p += [kw(rnd, "offset"), str(plan["offset"])]
    return sp().join(p)


# ---- running ---------------------------------------------------------------------------------------------------------------

def run(table, argv, path, fmt):
    p = subprocess.run([table] + argv + ["--format", fmt, path], capture_output=True, cwd=os.path.dirname(path))
    return normalise(p.returncode, p.stdout, p.stderr)


def normalise(code, out, err):
    """A literal that does not fit its column's type is `where.syntax` in the flag form, with the offset and the text of the expression it was
    written in; a query is refused for it with `query.syntax` at the offset in the query. The two are the same refusal written for two front
    ends, so they compare by class only. Everything else, refusals included, compares byte for byte."""
    try:
        e = json.loads(out)["error"]
        if e["rule"] == "where.syntax":
            return code, b"SYNTAX", b""
    except Exception:
        pass
    if err.startswith(b"table: where.syntax:"):
        return code, out, b"SYNTAX"
    return code, out, err


def gate(table, cases, seed, translate=None, verbose=False):
    import query_proto as qp
    translate = translate or qp.translate
    rnd = random.Random(seed)
    stats = dict(cases=0, ran=0, skipped_no_flag_form=0, refusals=0, disagree=0, rules={}, translate_errors=0)
    bad = []
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "f.csv")
        for _ in range(cases):
            names, kinds, rows = make_table(rnd)
            write_csv(path, names, rows)
            plan = make_plan(rnd, names, kinds, rows)
            stats["cases"] += 1
            if not expressible_in_flags(plan, names):
                stats["skipped_no_flag_form"] += 1
                continue
            flags = flag_form(plan)
            q = query_form(rnd, plan, "'f.csv'" if rnd.random() < 0.5 else None)
            try:
                targv, source = translate(q)
            except Exception as e:      # the prototype's QueryError (or, in a mutant, its own copy of it): a valid plan must translate
                stats["translate_errors"] += 1
                bad.append(("translate-error", q, getattr(e, "kind", repr(e)), getattr(e, "offset", None), getattr(e, "expected", None)))
                stats["disagree"] += 1
                continue
            # a query that names no source is given the file as the operand, as `table --query Q FILE` would be
            for fmt in ("json", "csv"):
                a = run(table, flags, path, fmt)
                b = run(table, targv, path, fmt)
                stats["ran"] += 1
                if a[0] != 0:
                    stats["refusals"] += 1
                    try:
                        r = json.loads(a[1])["error"]["rule"] if fmt == "json" else a[2].decode().split(":")[1].strip()
                    except Exception:
                        r = "?"
                    stats["rules"][r] = stats["rules"].get(r, 0) + 1
                if a != b:
                    stats["disagree"] += 1
                    bad.append(("differs", q, flags, targv, a[0], b[0]))
    return stats, bad


MUTANTS = [
    # (name, old, new) applied to query_proto.py; each must make the gate fail
    ("<> kept as <>", 'op = "!=" if t[1] == "<>" else t[1]', 'op = t[1]'),
    ("desc ignored in order by", 'argv += ["--order-by", ",".join(keys)]', 'argv += ["--order-by", ",".join(k.lstrip("-") for k in keys)]'),
    ("offset sent as limit", 'argv += ["--from", str(ast["offset"])]', 'argv += ["--limit", str(ast["offset"])]'),
    ("mean scale dropped", 's += f"@{a[\'scale\']}"', 's += ""'),
    ("quote in a value not doubled", """return v[1] if v[0] == "num" else "'" + v[1].replace("'", "''") + "'\"""", """return v[1] if v[0] == "num" else "'" + v[1] + "'\""""),
    ("quote in a name not doubled", """return "'" + c[1].replace("'", "''") + "'\"""", """return "'" + c[1] + "'\""""),
    ("backslash in a list name not escaped", 's = c[1].replace("\\\\", "\\\\\\\\").replace(",", "\\\\,")', 's = c[1].replace(",", "\\\\,")'),
    ("comma in a list name not escaped", '.replace(",", "\\\\,")\n    return "\\\\" + s', '\n    return "\\\\" + s'),
    ("leading # not escaped", 'return "\\\\" + s if s.startswith("#") else s', 'return s'),
    ("position written as a name", 'return f"#{c[1]}"\n    s = c[1].replace', 'return str(c[1])\n    s = c[1].replace'),
    ("colon in an agg name not escaped", 'return s.replace(":", "\\\\:").replace("@", "\\\\@")', 'return s.replace("@", "\\\\@")'),
    ("type dec scale dropped", 'f":dec({ty[1]})"', '":dec(2)"'),
    ("last condition dropped", 'argv += ["--where", " and ".join(parts)]', 'argv += ["--where", " and ".join(parts[:-1] or parts)]'),
    ("in-list drops its last value", '", ".join(where_value(v) for v in c["vals"])', '", ".join(where_value(v) for v in c["vals"][:max(1, len(c["vals"]) - 1)])'),
    ("contains read as =", 'conds.append({"col": e, "op": "contains", "vals": [self.literal()], "at": at})', 'conds.append({"col": e, "op": "=", "vals": [self.literal()], "at": at})'),
    ("colon in an order-by name not escaped", '.replace(",", "\\\\,").replace(":", "\\\\:")\n    return "\\\\" + s if s[:1] in ("#", "-") else s', '.replace(",", "\\\\,")\n    return "\\\\" + s if s[:1] in ("#", "-") else s'),
    ("leading dash of an order-by name not escaped", 'return "\\\\" + s if s[:1] in ("#", "-") else s', 'return "\\\\" + s if s[:1] in ("#",) else s'),
    ("limit and offset swapped", 'argv += ["--limit", str(ast["limit"])]', 'argv += ["--limit", str(ast["offset"] if ast["offset"] is not None else ast["limit"])]'),
    ("limit zero-padded off by one", 'argv += ["--limit", str(ast["limit"])]', 'argv += ["--limit", str(ast["limit"] + (1 if ast["limit"] == 5 else 0))]'),
    ("keywords case sensitive", "return t[1].lower() if t[0] == \"ID\" else None", "return t[1] if t[0] == \"ID\" else None"),
    ("group by order reversed", 'argv += ["--group", ",".join(list_name(c) for c in gnames)]', 'argv += ["--group", ",".join(list_name(c) for c in reversed(gnames))]'),
    ("sort key of an aggregate loses its sign", 'argv += ["--sort", ("-" if desc else "") + key]', 'argv += ["--sort", key]'),
    ("count(distinct) as plain count", 'return {"fn": "distinct", "arg": e, "at": at}', 'return {"fn": "count", "at": at}'),
    ("select list order reversed", 'argv += ["--select", ",".join(list_name(c[1]) for c in cols)]', 'argv += ["--select", ",".join(list_name(c[1]) for c in reversed(cols))]'),
    ("sum read as min", 'return {"fn": fn, "arg": e, "scale": scale, "at": at}', 'return {"fn": "min" if fn == "sum" else fn, "arg": e, "scale": scale, "at": at}'),
    ("int type lost on a where column", 'return {"int": ":int", "float": ":float"}.get(ty[0]) or f":dec({ty[1]})"', 'return ":dec(%d)" % (ty[1] or 0) if ty[0] != "float" else ":float"'),
]


def mutants(table, cases, seed):
    import importlib
    import query_proto as qp
    src = open(os.path.join(HERE, "query_proto.py")).read()
    survivors = []
    for name, old, new in MUTANTS:
        if old not in src:
            print("MUTANT DOES NOT APPLY:", name)
            survivors.append(name)
            continue
        ns = {"__name__": "query_proto_mutant"}
        exec(compile(src.replace(old, new, 1), "query_proto_mutant", "exec"), ns)
        sys.modules["query_proto_mutant"] = type(sys)("query_proto_mutant")
        sys.modules["query_proto_mutant"].__dict__.update(ns)
        # killed when the gate fails for any of four seeds (a mutant is one defect, a seed a few hundred random plans)
        killed, seen = False, 0
        for k in range(4):
            st, bad = gate(table, cases, seed + k, translate=ns["translate"])
            seen += st["ran"]
            if st["disagree"] > 0:
                killed = True
                break
        print(f"{'killed  ' if killed else 'SURVIVED'} {name:46s} (seed {seed + k}: {st['disagree']} of {st['ran']} runs differ)")
        if not killed:
            survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)} of {len(MUTANTS)} killed")
    return survivors


if __name__ == "__main__":
    a = sys.argv
    table = os.path.abspath(a[a.index("--table") + 1])
    cases = int(a[a.index("--cases") + 1]) if "--cases" in a else 300
    seed = int(a[a.index("--seed") + 1]) if "--seed" in a else 1
    if "--mutants" in a:
        sys.exit(1 if mutants(table, cases, seed) else 0)
    st, bad = gate(table, cases, seed)
    print(json.dumps(st))
    for b in bad[:10]:
        print(b)
    sys.exit(1 if st["disagree"] else 0)
