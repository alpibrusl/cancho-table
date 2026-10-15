"""A simple reference implementation of `table`'s filter and group semantics, in
Python, over the records Python's csv module reads (docs/filter.md).

It is written to be read, not to be fast: the semantics are the ones the tool
states, and nothing else. A table is the list of records (lists of str, a byte
per character, as the tests read them); the first is the header; a record with
another width than the header is ragged: counted, never tested or grouped.

    rows, ragged, error = run(table, plan)

`plan` is a dict: where (a list of conditions, a conjunction) or where_tree (a tree of `and` and `or`, see `eval_tree`), select (a list of column names
or None), group (a list of names or None), aggs (a list of (function, column)),
sort (a string or None), top (an int or None). The answer is ("rows", header,
rows) or ("groups", labels, rows) with ragged: a count, or an error
(rule, detail) for a cell that has to be an integer and is not.
"""

import re

import numbers_ref

INT_MIN = -(1 << 63)
INT_MAX = (1 << 63) - 1
INTEGER = re.compile(r"^[+-]?[0-9]+$")


def to_int(cell):
    """(value, None), or (None, "value.not-integer" / "value.integer-overflow")."""
    if not INTEGER.match(cell):
        return None, "value.not-integer"
    v = int(cell)
    if v < INT_MIN or v > INT_MAX:
        return None, "value.integer-overflow"
    return v, None


def compare(op, a, b):
    return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def holds(cond, cell):
    """True or False, or an error rule for an :int condition."""
    kind = cond["kind"]
    if cond.get("float"):
        # `:float` (docs/numbers.md, stage N3a): the cell is the nearest double or a refusal; comparisons are of doubles, not of decimal text
        got = numbers_ref.flt(cell)
        if got[0] == "refuse":
            return got[1]
        lits = cond["lits"] if kind == "in" else [cond["lit"]]
        if kind == "in":
            return any(got[1] == numbers_ref.flt(l)[1] for l in lits)
        return compare(cond["op"], got[1], numbers_ref.flt(cond["lit"])[1])
    if cond.get("dec") is not None:
        # `:dec(S)` (docs/numbers.md): the cell is the exact scaled integer or a refusal; the literals are read at the same scale
        scale = cond["dec"]
        got = numbers_ref.dec(cell, scale)
        if got[0] == "refuse":
            return got[1]
        lits = cond["lits"] if kind == "in" else [cond["lit"]]
        if kind == "in":
            return any(got[1] == numbers_ref.dec(l, scale)[1] for l in lits)
        return compare(cond["op"], got[1], numbers_ref.dec(cond["lit"], scale)[1])
    if cond.get("int"):
        v, bad = to_int(cell)
        if bad:
            return bad
        lits = cond["lits"] if kind == "in" else [cond["lit"]]
        if kind == "in":
            return any(v == int(l) for l in lits)
        return compare(cond["op"], v, int(cond["lit"]))
    if kind == "contains":
        return cond["lit"] in cell
    if kind == "in":
        return cell in cond["lits"]
    return compare(cond["op"], cell, cond["lit"])


def leaf_count(node):
    return 1 if node[0] == "leaf" else sum(leaf_count(k) for k in node[1])


def eval_tree(node, cell_of, counter=None):
    """The `--where` of docs/or-and-columns.md as a tree: ("leaf", cond) or ("and" | "or", [nodes]). True or False, or ("refuse", rule, column, the leaf's number, leaves in text order):
    left to right, stopping at the first unit that decides its level (the first false of an `and`, the first true of an `or`); a cell is read as its type only when its leaf is reached,
    so a bad cell that no path reaches is not an error (section 5.1). `cell_of(column)` is the cell of the record."""
    counter = [0] if counter is None else counter
    if node[0] == "leaf":
        n = counter[0]
        counter[0] += 1
        v = holds(node[1], cell_of(node[1]["column"]))
        if v is True or v is False:
            return v
        return ("refuse", v, node[1]["column"], n)
    decides = node[0] == "or"
    for k, kid in enumerate(node[1]):
        v = eval_tree(kid, cell_of, counter)
        if isinstance(v, tuple):
            return v
        if v == decides:
            for rest in node[1][k + 1:]:
                counter[0] += leaf_count(rest)       # the leaves not reached are skipped but still counted, so that a leaf keeps its number
            return v
    return not decides


def run(table, plan):
    header, records = table[0], table[1:]
    width = len(header)
    where = plan.get("where") or []
    group = plan.get("group")
    aggs = plan.get("aggs")
    grouping = group is not None or aggs is not None
    group = group or []
    aggs = aggs or [("count", None)]
    ragged = 0
    rows = []
    state = {}
    # One loop, row by row, as the tool reads: a refusal in a later row's
    # condition does not hide an earlier row's aggregate, and the other way round.
    for n, rec in enumerate(records, 1):
        if len(rec) != width:
            ragged += 1
            continue
        verdict = True
        if plan.get("where_tree") is not None:
            v = eval_tree(plan["where_tree"], lambda column, rec=rec: rec[header.index(column)])
            if isinstance(v, tuple):
                return None, ragged, (v[1], {"row": n, "column": v[2], "context": "where"})
            verdict = v
        for cond in where:
            v = holds(cond, rec[header.index(cond["column"])])
            if v is True:
                continue
            if v is False:
                verdict = False
                break
            return None, ragged, (v, {"row": n, "column": cond["column"], "context": "where"})
        if not verdict:
            continue
        if not grouping:
            select = plan.get("select") or header
            rows.append([rec[header.index(c)] for c in select])
            continue
        key = tuple(rec[header.index(c)] for c in group)
        entry = state.setdefault(key, {"count": 0, "values": [None] * len(aggs), "seen": [set() for _ in aggs]})
        entry["count"] += 1
        for k, (function, column) in enumerate(aggs):
            if function == "count":
                continue
            cell = rec[header.index(column)]
            if function == "distinct":
                entry["seen"][k].add(cell)
                continue
            v, bad = to_int(cell)
            if bad:
                return None, ragged, (bad, {"row": n, "column": column, "context": function})
            cur = entry["values"][k]
            if function == "sum":
                entry["values"][k] = (cur or 0) + v      # exact, whatever the width: a sum is a pair of integers (docs/numbers.md N0p)
            elif cur is None:
                entry["values"][k] = v
            elif function == "min":
                entry["values"][k] = min(cur, v)
            else:
                entry["values"][k] = max(cur, v)
    if not grouping:
        return ("rows", plan.get("select") or header, rows), ragged, None
    labels = list(group) + [f if f == "count" else "%s:%s" % (f, c) for f, c in aggs]
    out = []
    for key, entry in state.items():
        row = list(key)
        for k, (function, column) in enumerate(aggs):
            if function == "count":
                row.append(entry["count"])
            elif function == "distinct":
                row.append(len(entry["seen"][k]))
            else:
                row.append(entry["values"][k])
        out.append(row)
    out.sort(key=lambda r: tuple(r[:len(group)]))
    sort = plan.get("sort")
    if sort:
        descending = sort.startswith("-")
        at = labels.index(sort.lstrip("-") if descending else sort)
        # stable: ties keep the key order
        # reverse=True keeps equal elements in their order, so ties stay in key order
        out.sort(key=lambda r: r[at], reverse=descending)
    if plan.get("top"):
        out = out[:plan["top"]]
    return ("groups", labels, [[str(x) for x in r] for r in out]), ragged, None
