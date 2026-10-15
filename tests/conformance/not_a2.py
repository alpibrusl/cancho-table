"""The references and generators of slice A2 of docs/or-and-columns.md: `not` in --where and in --query.

test_not_a2.py holds the tool against what is here. Nothing here shares code with the tool; it is written from the document (the grammar of section 3.1, the swap of section 4.1, the
evaluation rule of section 5, the repair of `not COND` of section 3.1) and from or_a1.py, whose pieces it reuses where the grammar of A1 is unchanged (the words, the literals, the
generators of tables and of leaves):

* `parse`: the parser of the grammar of --where with `not`, character by character, answering the tree or the refusal (`syntax` with the byte offset and the words of expectation, or
  `mixed` with the offset of the connective that changes the level). A tree has "leaf" (a condition, with "neg" for `COLUMN not in` and `COLUMN not contains`), "group", "not" (the unit
  of `not ( ... )`) and the levels "and", "or", "mix";
* `semantic`, `evaluate`, `program`, `run_program`, `tree_status`: the meaning, the tree walker of refimpl.py, the branch program of the document (a `not` is the swap of the two exits of
  what it holds) and its walk over leaf statuses;
* `readings`: the two readings a mix is refused with, written independently of the tool, with `not ( ... )` kept as it was written;
* `repair_not`: the rewriting that the refusal of `not COND` offers (`not a < 1` is `a >= 1`), computed by repeating the parser's own refusal until the expression is whole;
* the generators: trees with `not`, and their rendering as --where text.
"""

import itertools
import random

import or_a1 as O
import refimpl
from or_a1 import DEPTH, LEAVES, VALUES, Refused, connective, dec_suffix, gap, group, literal_code, render_word, skip, word

EXPECTED = dict(O.EXPECTED)
EXPECTED[23] = "( after not"
EXPECTED[24] = "in or contains after not"

COMPLEMENT = {"=": "!=", "!=": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The parser

def leaf(s, i, number):
    """The condition at i: (("leaf", cond, number, start, end, op_span), where it ends). `op_span` is the span of the operator, `not` included for `not in` and `not contains`."""
    begins = i
    text, j, quoted, status, first = word(s, i)
    if status == 1 and not quoted:
        raise Refused("syntax", i, 1)
    if status != 0:
        raise Refused("syntax", i, status)
    typed = None
    if quoted:
        for suffix, kind in ((":int", "int"), (":float", "float")):
            if s.startswith(suffix, j) and (j + len(suffix) == len(s) or s[j + len(suffix)] in O.DELIM):
                typed = kind
                j += len(suffix)
                break
        else:
            if s.startswith(":dec", j):
                typed, j = dec_suffix(s, j + 4, i)
    else:
        if text.endswith(":int"):
            typed, text = "int", text[:-4]
        elif text.endswith(":dec"):
            typed, j = dec_suffix(s, j, i)
            text = text[:-4]
        if text.endswith(":float"):
            typed, text = "float", text[:-6]
    cond = {"column": text, "int": typed == "int", "float": typed == "float", "dec": typed[1] if isinstance(typed, tuple) else None}
    if not cond["float"]:
        cond.pop("float")
    i = skip(s, j)
    if i >= len(s):
        raise Refused("syntax", i, 2)
    c = s[i]
    kind = "cmp"
    op = None
    span_start = i
    if c == "=":
        op, i = "=", i + 1
    elif c == "!":
        if i + 1 < len(s) and s[i + 1] == "=":
            op, i = "!=", i + 2
        else:
            raise Refused("syntax", i, 2)
    elif c in "<>":
        if i + 1 < len(s) and s[i + 1] == "=":
            op, i = c + "=", i + 2
        else:
            op, i = c, i + 1
    else:
        w, k, q, st, _ = word(s, i)
        neg = False
        if st == 0 and not q and w == "not":
            i2 = skip(s, k)
            w2, k2, q2, st2, _ = word(s, i2)
            if st2 == 0 and not q2 and w2 in ("in", "contains"):
                neg = True
                w, k, q, st, i = w2, k2, q2, st2, i2
            else:
                raise Refused("syntax", i2, 24)
        if st == 0 and not q and w == "contains":
            if typed:
                raise Refused("syntax", i, 6)
            kind, i = "contains", k
        elif st == 0 and not q and w == "in":
            kind, i = "in", k
        else:
            raise Refused("syntax", i, 2)
        if neg:
            cond["neg"] = True
    op_span = (span_start, i)
    cond["kind"] = kind
    if kind == "in":
        i = skip(s, i)
        if i >= len(s) or s[i] != "(":
            raise Refused("syntax", i, 7)
        i += 1
        lits = []
        while True:
            i = skip(s, i)
            w, k, q, st, _ = word(s, i)
            if st == 1 and not q:
                raise Refused("syntax", i, 3)
            if st != 0:
                raise Refused("syntax", i, st)
            if literal_code(cond, w):
                raise Refused("syntax", i, literal_code(cond, w))
            lits.append(w)
            i = skip(s, k)
            closing = False
            pending = None
            if i < len(s) and s[i] == ",":
                i += 1
            elif i < len(s) and s[i] == ")":
                i += 1
                closing = True
            else:
                pending = i
            if len(lits) > VALUES:
                raise Refused("syntax", i, 11)
            if pending is not None:
                raise Refused("syntax", pending, 8)
            if closing:
                break
        cond["lits"] = lits
    else:
        i = skip(s, i)
        w, k, q, st, _ = word(s, i)
        if st == 1 and not q:
            raise Refused("syntax", i, 3)
        if st != 0:
            raise Refused("syntax", i, st)
        if literal_code(cond, w):
            raise Refused("syntax", i, literal_code(cond, w))
        cond["lit"] = w
        cond["op"] = op if kind == "cmp" else None
        i = k
    return ("leaf", cond, number, begins, i, op_span), i


def parse(s, tolerant=False):
    """The tree of `s`: ("leaf", cond, number, start, end, op_span), ("group", tree, start, end), ("not", tree, start, end), ("and" | "or" | "mix", [units], [connectives before each], start).
    A level that mixes `and` and `or` raises Refused("mixed", the offset of the connective that changes it) at once, as any other error is (the first in the text is the one); `tolerant`
    reads on and returns the tree, the level that mixes being "mix". A refusal for `not COND` carries `not_at`, where the `not` begins."""
    state = {"leaves": 0, "mix": None}

    def unit_start(i, depth):
        """The unit at i: a group, `not ( ... )`, or a condition. A `not` is a keyword here only when a `(` follows it, or a word that is not an operator: `not = 1`, `not in (1)` and `not contains x`
        are the column of that name, and so is `not not in (1)`."""
        if i < len(s) and s[i] == "(":
            return group_at(i, i, depth, "group")
        w, k, q, st, _ = word(s, i)
        if st == 0 and not q and w == "not":
            j = skip(s, k)
            nxt = s[j] if j < len(s) else ""
            if nxt == "(":
                return group_at(i, j, depth, "not")
            if nxt != "" and nxt not in "=!<>":
                w2, k2, q2, st2, _ = word(s, j)
                if st2 == 0 and (q2 or w2 not in ("in", "contains", "not")):
                    e = Refused("syntax", j, 23)
                    e.not_at = i
                    raise e
        unit, i2 = leaf(s, i, state["leaves"])
        state["leaves"] += 1
        return unit, i2

    def group_at(start, paren, depth, kind):
        if depth >= DEPTH:
            raise Refused("syntax", paren, 21)
        inner, i = level(paren + 1, depth + 1)
        i = skip(s, i)
        if i < len(s) and s[i] == ")":
            return (kind, inner, start, i + 1), i + 1
        raise Refused("syntax", i, 22)

    def level(i, depth):
        units, conns = [], []
        kind = None
        conn = None
        start = skip(s, i)
        i = start
        while True:
            i = skip(s, i)
            if state["leaves"] >= LEAVES:
                raise Refused("syntax", i, 11)
            unit, i = unit_start(i, depth)
            units.append(unit)
            conns.append(conn)
            i = skip(s, i)
            if i >= len(s) or s[i] == ")":
                break
            c, k = connective(s, i)
            if c is None:
                raise Refused("syntax", i, 4 if depth == 0 else 22)
            if kind is None:
                kind = c
            elif kind != c and kind != "mix":
                if state["mix"] is None:
                    state["mix"] = i
                    if not tolerant:
                        raise Refused("mixed", i)
                kind = "mix"
            conn = c
            i = skip(s, k)
        if len(units) == 1:
            return units[0], i
        return (kind, units, conns, start), i

    tree, i = level(0, 0)
    i = skip(s, i)
    if i < len(s):
        raise Refused("syntax", i, 4)
    return tree


def parse_result(s):
    """("ok", tree) or ("syntax", offset, expected words) or ("mixed", offset)."""
    try:
        return ("ok", parse(s))
    except Refused as e:
        if e.kind == "mixed":
            return ("mixed", e.offset)
        return ("syntax", e.offset, EXPECTED.get(e.code, "?%d" % e.code))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The meaning

def semantic(tree):
    """The tree reduced to what is evaluated: ("leaf", cond), ("not", [kid]) or ("and" | "or", [kids]); groups and single-unit levels go."""
    kind = tree[0]
    if kind == "leaf":
        return ("leaf", tree[1])
    if kind == "group":
        return semantic(tree[1])
    if kind == "not":
        return ("not", [semantic(tree[1])])
    return (kind, [semantic(u) for u in tree[1]])


leaves = O.leaves
count_leaves = O.count_leaves


def has_not(node):
    return node[0] == "not" or (node[0] == "leaf" and bool(node[1].get("neg"))) or (node[0] in ("and", "or") and any(has_not(k) for k in node[1]))


def has_or(node):
    return node[0] == "or" or (node[0] in ("and", "not") and any(has_or(k) for k in node[1]))


def has_flow(node):
    """Whether the tool compiles a branch program: an `or` or a `not` anywhere (a conjunction of plain conditions has none)."""
    return has_or(node) or has_not(node)


def evaluate(node, cell_of):
    return refimpl.eval_tree(node, cell_of)


def program(node):
    """The branch program: {leaf number: (next if true, next if false)}, a target a leaf number, "A" accept or "R" reject. `not` swaps the exits of what it holds; a condition that is
    `not in` or `not contains` swaps its own."""
    prog = {}
    number = [0]

    def numbered(n):
        if n[0] == "leaf":
            i = number[0]
            number[0] += 1
            return ("leaf", i, bool(n[1].get("neg")))
        return (n[0], [numbered(k) for k in n[1]])

    tree = numbered(node)

    def first(t):
        return t[1] if t[0] == "leaf" else first(t[1][0])

    def emit(t, T, F):
        if t[0] == "leaf":
            prog[t[1]] = (F, T) if t[2] else (T, F)
            return
        kids = t[1]
        if t[0] == "not":
            emit(kids[0], F, T)
            return
        for i, kid in enumerate(kids):
            nxt = first(kids[i + 1]) if i + 1 < len(kids) else None
            if t[0] == "and":
                emit(kid, nxt if nxt is not None else T, F)
            else:
                emit(kid, T, nxt if nxt is not None else F)

    emit(tree, "A", "R")
    return prog, tree


run_program = O.run_program


def tree_status(node, statuses):
    """The walk of the tree over leaf statuses (0 false, 1 true, 2 a bad cell): (verdict, leaves visited in order). A `not` changes a verdict of 0 or 1 into the other and a 2 stays."""
    seen = []
    counter = [0]

    def go(n):
        if n[0] == "leaf":
            i = counter[0]
            counter[0] += 1
            seen.append(i)
            v = statuses[i]
            if n[1].get("neg") and v != 2:
                v = 1 - v
            return v
        if n[0] == "not":
            v = go(n[1][0])
            return v if v == 2 else 1 - v
        decides = 1 if n[0] == "or" else 0
        for k, kid in enumerate(n[1]):
            v = go(kid)
            if v == 2 or v == decides:
                for rest in n[1][k + 1:]:
                    counter[0] += len(leaves(rest))
                return v
        return 0 if n[0] == "or" else 1

    return go(node), seen


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The two readings of a mix (section 3.3), with `not ( ... )` kept as written

def readings(s):
    """(by precedence, left to right): the text of `s` with the levels that mix written both ways, or None when `s` is not a mix that is otherwise whole."""
    try:
        tree = parse(s, tolerant=True)
    except Refused:
        return None

    def text(t, policy):
        kind = t[0]
        if kind == "leaf":
            return s[t[3]:t[4]]
        if kind == "group":
            return "(" + text(t[1], policy) + ")"
        if kind == "not":
            return "not (" + text(t[1], policy) + ")"
        units = [text(u, policy) for u in t[1]]
        if kind in ("and", "or"):
            return (" %s " % kind).join(units)
        conns = t[2]
        if policy == 0:
            runs = [[units[0]]]
            for u, c in zip(units[1:], conns[1:]):
                if c == "and":
                    runs[-1].append(u)
                else:
                    runs.append([u])
            return " or ".join(r[0] if len(r) == 1 else "(" + " and ".join(r) + ")" for r in runs)
        acc, cur = units[0], None
        for u, c in zip(units[1:], conns[1:]):
            if cur is None:
                cur = c
            elif c != cur:
                acc = "(" + acc + ")"
                cur = c
            acc = "%s %s %s" % (acc, c, u)
        return acc

    return text(tree, 0), text(tree, 1)


def within_depth(s):
    try:
        parse(s)
        return True
    except Refused:
        return False


def offered_readings(s):
    both = readings(s)
    if both is None:
        return []
    out = [both[0]] if within_depth(both[0]) else []
    if both[1] != both[0] and within_depth(both[1]):
        out.append(both[1])
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The repair of `not COND` (section 3.1)

def repair_not(s):
    """The expression the refusal of `not COND` offers, or None: every `not COND` that the grammar refuses is rewritten in turn as the condition's exact complement (`=` and `!=`,
    `<` and `>=`, `>` and `<=`, `in` and `not in`, `contains` and `not contains`), and the result is offered only when the grammar then takes it whole."""
    cur = s
    for _ in range(LEAVES + 2):
        try:
            parse(cur)
            return cur if cur != s else None
        except Refused as e:
            if e.kind != "syntax" or e.code != 23:
                return None
            not_at, j = e.not_at, e.offset
            try:
                lf, end = leaf(cur, j, 0)
            except Refused:
                return None
            cond = lf[1]
            a, b = lf[5]
            if cond.get("neg"):
                new = "in" if cond["kind"] == "in" else "contains"
            elif cond["kind"] == "in":
                new = "not in"
            elif cond["kind"] == "contains":
                new = "not contains"
            else:
                new = COMPLEMENT[cond["op"]]
            cur = cur[:not_at] + cur[j:a] + new + cur[b:end] + cur[end:]
    return None


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Generators

def render_leaf(cond, rng):
    """The condition as --where text: `col not in (...)` and `col not contains x` for a cond with "neg"."""
    col = render_word(cond["column"], rng)
    if cond.get("int"):
        col += ":int"
    elif cond.get("float"):
        col += ":float"
    elif cond.get("dec") is not None:
        col += ":dec(%d)" % cond["dec"]
    sep = rng.choice([" not ", " not ", "  not  ", "\tnot\n"]) if cond.get("neg") else " "
    if cond["kind"] == "contains":
        return "%s%scontains %s" % (col, sep, render_word(cond["lit"], rng))
    if cond["kind"] == "in":
        sp = " " if rng.random() < 0.7 else ""
        return "%s%sin%s(%s)" % (col, sep, sp, ("," + sp).join(render_word(l, rng) for l in cond["lits"]))
    sp = " " if rng.random() < 0.55 else ""
    return "%s%s%s%s%s" % (col, sp, cond["op"], sp, render_word(cond["lit"], rng))


def render_not(inner, rng):
    return "not" + rng.choice(["", " ", " ", "  ", "\t"]) + "(" + inner + ")"


def render(node, rng, extra=0.1, top=True):
    """--where text for the semantic tree `node` (with "not" nodes and "neg" leaves): the parentheses the grammar needs, some for a level inside a level of its own connective, and now and
    then redundant ones round a condition or the whole. A `not` is always `not ( ... )`."""
    if node[0] == "leaf":
        text = render_leaf(node[1], rng)
        if rng.random() < extra:
            text = group(text)
        return text
    if node[0] == "not":
        return render_not(render(node[1][0], rng, extra, True), rng)
    parts = []
    for kid in node[1]:
        t = render(kid, rng, extra, False)
        if kid[0] in ("and", "or") and (kid[0] != node[0] or rng.random() < 0.5):
            t = group(t)
        parts.append(t)
    text = (gap(rng) + node[0] + gap(rng)).join(parts)
    if top and rng.random() < extra / 2:
        text = group(text)
    return text


def add_nots(rng, node, p):
    """`node` with `not` added: a unit is wrapped in a `not` with probability `p`, and a leaf that is an `in` or a `contains` is negated in its own condition with probability `p`."""
    if node[0] == "leaf":
        cond = node[1]
        if cond["kind"] in ("in", "contains") and rng.random() < p:
            node = ("leaf", dict(cond, neg=True))
    else:
        node = (node[0], [add_nots(rng, k, p) for k in node[1]])
    if rng.random() < p:
        node = ("not", [node])
    return node


def gen_tree(rng, n, leaf_of, depth=3, p_not=0.25, kind=None):
    """O.gen_tree with `not` added."""
    return add_nots(rng, O.gen_tree(rng, n, leaf_of, depth=depth, kind=kind), p_not)


def nesting(text):
    """The deepest nesting of parentheses of a text (quotes and escapes ignored: a generated text with such cells is checked by the parser anyway)."""
    d = m = 0
    for c in text:
        if c == "(":
            d += 1
            m = max(m, d)
        elif c == ")":
            d -= 1
    return m
