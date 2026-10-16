"""Join plans in two spellings, the flags and the query (docs/joins.md sections 2.3, 19 and 20, gate Q-EQ), and the plans the SQLite oracle can judge.

A plan of join_ref3.py (the plans the J1 to J3 tests draw: a join type, pairs of key columns, `--join-where`, `--where`, a select list, a grouping, a sort, an order, a page, a rename) is
rendered here as

* the flags the tool has, with `--join-prefix b.` (the query form always prefixes the right table's columns with its alias and a dot, and never uses the shared-name form), and
* the `--query` that stands for exactly them: `select ... from 'p.csv' as a [jtype] join 'b.csv' as b on a.k = b.k [and ...] where ... group by ... order by ... limit N offset M`,
  every column written alias.column, a column of the right table written for the joined row as `b.` and its own name.

A plan that the query form cannot say is not drawn (`expressible`): a grouping by a typed column (`group by x::int` is `query.unsupported`, as it always was), a `--top` beside a
query that has its own `limit` (the flag and the query would both say the page), the shared-name key. Nothing else is left out.

The plans of the SQLite oracle are drawn in query_join_oracle.py.
"""

import random
import re

import join_ref3 as R3
import join_ref2 as R

ALIAS_P = "a"
ALIAS_B = "b"
PREFIX = ALIAS_B + "."
PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
WORDS = {"select", "from", "where", "group", "order", "limit", "offset", "by", "and", "in", "contains", "asc", "desc", "as", "cast", "join", "inner", "left", "right", "full", "cross",
         "on", "using", "having", "union", "intersect", "except", "like", "ilike", "between", "is", "not", "or", "case", "when", "then", "else", "end", "null", "true", "false", "distinct",
         "over", "with", "all", "any", "exists"}


def ident(name):
    """A column name as the query writes it: bare when it is a plain word that is no keyword, else "quoted" with a doubled quote."""
    if PLAIN.match(name) and name.lower() not in WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def ref_of(side, name):
    return "%s.%s" % (side, ident(name))


def joined_ref(name):
    """A name of the joined row as alias.column."""
    if name.startswith(PREFIX):
        return ref_of(ALIAS_B, name[len(PREFIX):])
    return ref_of(ALIAS_P, name)


def build_ref(name):
    return ref_of(ALIAS_B, name)


def q(word):
    return "'" + word.replace("'", "''") + "'"


def render_q(node, ref, cast=False):
    """The text of an expression of the grammar in the query dialect: columns through `ref`, a typed condition as `col::int` (or `cast(col as int)` when `cast`: the text SQLite reads too)."""
    kind = node[0]
    if kind == "leaf":
        c = node[1]
        col = ref(c["column"])
        if c["int"]:
            col = "cast(%s as int)" % col if cast else col + "::int"
        if c["kind"] == "compare":
            return "%s %s %s" % (col, c["op"], c["lit"] if c["int"] else q(c["lit"]))
        if c["kind"] == "contains":
            return "%s %scontains %s" % (col, "not " if c["neg"] else "", q(c["lit"]))
        return "%s %sin (%s)" % (col, "not " if c["neg"] else "", ", ".join(l if c["int"] else q(l) for l in c["lits"]))
    if kind == "paren":
        return "(" + render_q(node[1][0], ref, cast) + ")"
    if kind == "not":
        return "not (" + render_q(node[1][0], ref, cast) + ")"
    return (" %s " % kind).join(render_q(k, ref, cast) for k in node[1])


def agg_q(fn, name):
    if fn == "count":
        return "count(*)"
    if fn == "distinct":
        return "count(distinct %s)" % joined_ref(name)
    return "%s(%s)" % (fn, joined_ref(name))


def label_of(fn, name):
    return "count" if fn == "count" else "%s:%s" % (fn, name)


def join_word(jt, rng):
    if jt == "inner":
        return rng.choice(["join", "inner join", "JOIN"])
    return jt + " join"


def with_prefix(case):
    """The case as the query form joins it: pairs without the shared-name form, the right table's columns prefixed with `b.`."""
    pairs = [(pi, bi, kind, False) for (pi, bi, kind, _using) in case.pairs]
    c = R3.Case(case.name, case.P, case.B, pairs, prefix=PREFIX)
    c.awkward = case.awkward
    return c


def expressible(plan):
    if any(kind != "text" for _, kind in (plan.get("group") or [])):
        return False
    return True


def on_clause(plan, ph, bh, rng):
    parts = []
    for pi, bi, kind, _using in plan["pairs"]:
        ty = "::" + R.suffix_of(kind)[1:] if kind else ""
        l, r = ref_of(ALIAS_P, ph[pi]) + ty, ref_of(ALIAS_B, bh[bi]) + ty
        parts.append("%s = %s" % ((l, r) if rng is None or rng.random() < 0.7 else (r, l)))
    text = " and ".join(parts)
    jw = plan.get("jw")
    if jw is not None:
        body = render_q(jw, build_ref)
        if jw[0] == "or":
            body = "(" + body + ")"
        text += " and " + body
    return text


def query_text(plan, ph, bh, rng, probe="p.csv", cast=False):
    """The query for `plan`, and the `--top` it needs beside it (or None)."""
    jt = plan["jtype"]
    group, aggs = plan.get("group"), plan.get("aggs")
    rename = plan.get("rename") or {}
    jheader = list(ph) + ([PREFIX + b for b in bh] if jt in ("inner", "left") else [])
    items = []

    def item(text, label):
        return text + (" as " + ident(rename[label]) if label in rename else "")

    if group is not None or aggs is not None:
        for name, _kind in (group or []):
            items.append(item(joined_ref(name), name))
        for fn, name in (aggs or [("count", None)]):
            items.append(item(agg_q(fn, name), label_of(fn, name)))
    elif plan.get("select"):
        items = [item(joined_ref(n), n) for n in plan["select"]]
    elif rename:
        items = [item(joined_ref(n), n) for n in jheader]
    else:
        items = ["*"]
    src = "stdin" if probe is None else "'%s'" % probe
    text = "select %s from %s as %s %s '%s' as %s on %s" % (", ".join(items), src, ALIAS_P, join_word(jt, rng), "b.csv", ALIAS_B, on_clause(plan, ph, bh, rng))
    if plan.get("where") is not None:
        text += " where " + render_q(plan["where"], joined_ref, cast)
    if group:
        text += " group by " + ", ".join(joined_ref(n) for n, _ in group)
    if plan.get("sort"):
        label = plan["sort"].lstrip("-")
        desc = plan["sort"].startswith("-")
        renamed = {v: k for k, v in rename.items()}
        if label in renamed:
            key = ident(label)
        elif label == "count":
            key = "count(*)"
        elif ":" in label and any(label_of(fn, nm) == label for fn, nm in (aggs or [])):
            fn, nm = next((fn, nm) for fn, nm in aggs if label_of(fn, nm) == label)
            key = agg_q(fn, nm)
        else:
            key = joined_ref(label)
        text += " order by %s%s" % (key, " desc" if desc else "")
    elif plan.get("order") is not None:
        keys = []
        for name, desc, typed in plan["order"]:
            col = joined_ref(name)
            if typed:
                col = "cast(%s as int)" % col if cast else col + "::int"
            keys.append(col + (" desc" if desc else ""))
        text += " order by " + ", ".join(keys)
    if plan.get("limit") is not None:
        text += " limit %d" % plan["limit"]
    if plan.get("from"):
        text += " offset %d" % plan["from"]
    top = plan.get("top")
    return text, top


def settle_page(plan):
    """A plan whose page the query can say: not a `--top` beside a `limit` (the query's own page would clash with the flag)."""
    if plan.get("top") and plan.get("limit") is not None:
        p = dict(plan)
        del p["limit"]
        return p
    return plan


def draw(rng, cases, jt):
    """(case, plan) for a join type: a plan of the J3 reference that the query form can say."""
    for _ in range(200):
        case = rng.choice(cases)
        plan = R3.random_plan(rng, case, jt)
        if expressible(plan):
            return case, settle_page(plan)
    raise AssertionError("no expressible plan drawn")
