"""The references and generators of slice A1 of docs/or-and-columns.md: `or` and parentheses in --where and in --query.

test_or_a1.py holds the tool against what is here. Nothing here shares code with the tool; each part is written from the document (the grammar of section 3, the evaluation rule of
section 5, the program of section 4, the repair of section 3.3) and from docs/filter.md:

* `parse`: a parser of the grammar of --where, character by character, that answers the tree or the refusal (`syntax` with the byte offset and the words of expectation, or `mixed` with the
  offset of the connective that changes the level);
* `evaluate`: the tree walked left to right, stopping at the first unit that decides its level, a cell read as its type only when its condition is reached (the rule of section 5.1);
* `program`: the branch program of section 4.1, compiled from the tree the way the appendix model of the document does it;
* `readings`: the two readings a mix is refused with (section 3.3), written independently of the tool;
* the generators: tables (with awkward cells, with empty and non-numeric cells in typed columns, and with valid cells only for the SQL oracle), trees, and their rendering as --where text.
"""

import itertools
import random
import re

import refimpl

WS = " \t\n\r"
DELIM = set(" \t\n\r'(),=!<>")
DEPTH = 8
LEAVES = 64
VALUES = 1024

EXPECTED = {
    1: "a column: a name, 'a quoted name' or #N",
    2: "an operator: = != < <= > >= contains in",
    3: "a value",
    4: "and, or the end of the expression",
    7: "( after in",
    8: ", or ) in a list of values",
    9: "a closing quote",
    10: "a byte after the backslash",
    5: "an integer, because the column is :int",
    6: "= != < <= > >= or in: contains compares text, and this column is :int, :dec or :float",
    11: "fewer conditions or values: the ceiling is 64 conditions and 1024 values",
    12: "an integer that fits 64 bits, because the column is :int",
    13: "a decimal number: digits and at most one point, no exponent, no spaces, because the column is :dec",
    14: "a decimal with no more fractional digits than the column's scale (write :dec(N) with a larger N to compare finer values)",
    15: "a decimal below 10^18 once scaled to the column's scale (a smaller scale, or fewer digits)",
    16: "a scale in parentheses after :dec, as :dec(2)",
    17: "a scale from 0 to 18",
    18: "a number: digits, a point, an exponent (the nearest double), because the column is :float",
    19: "a finite number: inf and nan are not numbers here",
    20: "a number within the range of a double (between 5e-324 and 1.7976931348623157e308 in size), of at most 1,100 bytes",
    21: "fewer nested parentheses: the ceiling is 8",
    22: "and, or, or the ) that closes the group",
}


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The parser

class Refused(Exception):
    def __init__(self, kind, offset, code=0, tree=None):
        super().__init__((kind, offset, code))
        self.kind, self.offset, self.code, self.tree = kind, offset, code, tree


def skip(s, i):
    while i < len(s) and s[i] in WS:
        i += 1
    return i


def word(s, i):
    """(text, end, quoted, status, first_escaped): status 0 a word, 1 none here, 9 a quote never closed, 10 a backslash at the end."""
    if i < len(s) and s[i] == "'":
        out = []
        j = i + 1
        while True:
            k = s.find("'", j)
            if k < 0:
                return "".join(out), len(s), True, 9, False
            out.append(s[j:k])
            if k + 1 < len(s) and s[k + 1] == "'":
                out.append("'")
                j = k + 2
            else:
                return "".join(out), k + 1, True, 0, False
    out = []
    j = i
    first = False
    while j < len(s) and s[j] not in DELIM:
        if s[j] == "\\":
            if j + 1 >= len(s):
                return "".join(out), j, False, 10, False
            if not out:
                first = True
            out.append(s[j + 1])
            j += 2
        else:
            out.append(s[j])
            j += 1
    if not out:
        return "", j, False, 1, False
    return "".join(out), j, False, 0, first


def leaf(s, i, number):
    """The condition at i: (the leaf, where it ends). The columns are bare or quoted words, with :int, :float or :dec(N) after them; a literal is not checked against its type here (the
    generators draw valid ones)."""
    begins = i
    text, j, quoted, status, first = word(s, i)
    if status == 1 and not quoted:
        raise Refused("syntax", i, 1)
    if status != 0:
        raise Refused("syntax", i, status)
    typed = None
    if quoted:
        for suffix, kind in ((":int", "int"), (":float", "float")):
            if s.startswith(suffix, j) and (j + len(suffix) == len(s) or s[j + len(suffix)] in DELIM):
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
        if st == 0 and not q and w == "contains":
            if typed:
                raise Refused("syntax", i, 6)
            kind, i = "contains", k
        elif st == 0 and not q and w == "in":
            kind, i = "in", k
        else:
            raise Refused("syntax", i, 2)
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
    return ("leaf", cond, number, begins, i), i


def dec_suffix(s, p, at):
    """The scale of a `:dec(N)` suffix, `p` being where the `(` must be: (("dec", N), where the suffix ends). The offsets are those of main's (docs/numbers.md): the `(` that is missing is
    refused where it should be, a scale with no digits or more than 18 one byte after it, a `)` that is missing where it should be. (Slice A1's reference said the start of the condition
    for all of them, which no generated expression reached; slice A2's soups did.)"""
    if p >= len(s) or s[p] != "(":
        raise Refused("syntax", p, 16)
    k = p + 1
    d = ""
    while k < len(s) and s[k].isdigit() and len(d) < 3:
        d += s[k]
        k += 1
    if not d:
        raise Refused("syntax", p + 1, 16)
    if len(d) > 2 or int(d) > 18:
        raise Refused("syntax", p + 1, 17)
    if k >= len(s) or s[k] != ")":
        raise Refused("syntax", k, 16)
    return ("dec", int(d)), k + 1


def literal_code(cond, value):
    """0 when `value` is a literal the type of the condition takes, else the code of the words of expectation (docs/numbers.md)."""
    if cond.get("int"):
        if not re.fullmatch(r"[+-]?[0-9]+", value):
            return 5
        return 0 if -2**63 <= int(value) <= 2**63 - 1 else 12
    if cond.get("dec") is not None:
        m = re.fullmatch(r"[+-]?([0-9]*)\.?([0-9]*)", value)
        if m is None or not (m.group(1) or m.group(2)):
            return 13
        scale = cond["dec"]
        if len(m.group(2)) > scale:
            return 14
        scaled = int((m.group(1) + m.group(2)) or "0") * 10 ** (scale - len(m.group(2)))
        return 0 if scaled <= 10**18 - 1 else 15
    if cond.get("float"):
        if re.fullmatch(r"[+-]?(inf|infinity|nan)", value, re.I):
            return 19
        if not re.fullmatch(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?", value):
            return 18
        if len(value) > 1100:
            return 20
        try:
            x = float(value)
        except (ValueError, OverflowError):
            return 20
        if x in (float("inf"), float("-inf")) or (x == 0 and re.search(r"[1-9]", value.split("e")[0].split("E")[0])):
            return 20
        return 0
    return 0


def connective(s, i):
    w, k, q, st, _ = word(s, i)
    if st == 0 and not q and w in ("and", "or"):
        return w, k
    return None, i


def parse(s, tolerant=False, legacy=False):
    """The tree of `s`: ("leaf", cond, number, start, end), ("group", tree, start, end), ("and" | "or" | "mix", [units], [connectives before each], start). A level that mixes `and` and `or`
    raises Refused("mixed", the offset of the connective that changes it) at once, as any other error is (the first in the text is the one); `tolerant` reads on and returns the tree, the
    level that mixes being "mix"."""
    state = {"leaves": 0, "mix": None}

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
            if not legacy and i < len(s) and s[i] == "(":
                if depth >= DEPTH:
                    raise Refused("syntax", i, 21)
                opened = i
                inner, i = level(i + 1, depth + 1)
                i = skip(s, i)
                if i < len(s) and s[i] == ")":
                    i += 1
                    unit = ("group", inner, opened, i)
                else:
                    raise Refused("syntax", i, 22)
            else:
                unit, i = leaf(s, i, state["leaves"])
                state["leaves"] += 1
            units.append(unit)
            conns.append(conn)
            i = skip(s, i)
            if i >= len(s) or s[i] == ")":
                break
            c, k = connective(s, i)
            if legacy and c == "or":
                c = None
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


def parse_result(s, legacy=False):
    """("ok", tree) or ("syntax", offset, expected words) or ("mixed", offset). `legacy`: the grammar of `main` before slice A1, a conjunction of conditions."""
    try:
        return ("ok", parse(s, legacy=legacy))
    except Refused as e:
        if e.kind == "mixed":
            return ("mixed", e.offset)
        return ("syntax", e.offset, EXPECTED.get(e.code, "?%d" % e.code))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The meaning

def semantic(tree):
    """The tree reduced to what is evaluated: ("leaf", cond) or ("and" | "or", [kids]); groups and single-unit levels go."""
    kind = tree[0]
    if kind == "leaf":
        return ("leaf", tree[1])
    if kind == "group":
        return semantic(tree[1])
    return (kind, [semantic(u) for u in tree[1]])


def leaves(node):
    if node[0] == "leaf":
        return [node[1]]
    return [x for k in node[1] for x in leaves(k)]


def has_or(node):
    return node[0] == "or" or (node[0] == "and" and any(has_or(k) for k in node[1]))


def evaluate(node, cell_of):
    """The tree walker of refimpl.py: True or False, or ("refuse", rule, column, leaf number)."""
    return refimpl.eval_tree(node, cell_of)


def program(node):
    """The branch program: {leaf number: (next if true, next if false)}, a target a leaf number, "A" accept or "R" reject. The appendix model of the document, on this tree."""
    prog = {}
    number = [0]

    def numbered(n):
        if n[0] == "leaf":
            i = number[0]
            number[0] += 1
            return ("leaf", i)
        return (n[0], [numbered(k) for k in n[1]])

    tree = numbered(node)

    def first(t):
        return t[1] if t[0] == "leaf" else first(t[1][0])

    def emit(t, T, F):
        if t[0] == "leaf":
            prog[t[1]] = (T, F)
            return
        kids = t[1]
        for i, kid in enumerate(kids):
            nxt = first(kids[i + 1]) if i + 1 < len(kids) else None
            if t[0] == "and":
                emit(kid, nxt if nxt is not None else T, F)
            else:
                emit(kid, T, nxt if nxt is not None else F)

    emit(tree, "A", "R")
    return prog, tree


def run_program(prog, statuses):
    """(0 false, 1 true, 2 refused) and the leaves visited, for leaf statuses 0, 1, 2."""
    pc = 0
    seen = []
    while True:
        seen.append(pc)
        if statuses[pc] == 2:
            return 2, seen
        nxt = prog[pc][0 if statuses[pc] == 1 else 1]
        if isinstance(nxt, str):
            return (1 if nxt == "A" else 0), seen
        assert nxt > pc
        pc = nxt


def tree_status(node, statuses):
    """The same walk for leaf statuses: (verdict, leaves visited in order)."""
    seen = []
    counter = [0]

    def go(n):
        if n[0] == "leaf":
            i = counter[0]
            counter[0] += 1
            seen.append(i)
            return statuses[i]
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
# The two readings of a mix (section 3.3)

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


def offered_readings(s):
    """The readings the tool offers: both, each only if the grammar takes it whole, and the second not when it is the first written again."""
    both = readings(s)
    if both is None:
        return []
    out = [both[0]] if within_depth(both[0]) else []
    if both[1] != both[0] and within_depth(both[1]):
        out.append(both[1])
    return out


def within_depth(s):
    try:
        parse(s)
        return True
    except Refused:
        return False


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Generators

SAFE = re.compile(r"^[A-Za-z0-9_.@/-]+$")
KEYWORDS = ("and", "or", "not", "in", "contains")


def quote(w):
    return "'" + w.replace("'", "''") + "'"


def render_word(w, rng):
    if SAFE.match(w) and not w.startswith("#") and rng.random() < 0.85:
        return w
    if rng.random() < 0.6 or not w or any(c in w for c in "\n\r"):
        return quote(w)
    return "".join("\\" + c if (not c.isalnum() and c not in "_.-@/") or (i == 0 and c == "#") else c for i, c in enumerate(w))


def render_leaf(cond, rng):
    col = render_word(cond["column"], rng)
    if cond.get("int"):
        col += ":int"
    elif cond.get("float"):
        col += ":float"
    elif cond.get("dec") is not None:
        col += ":dec(%d)" % cond["dec"]
    if cond["kind"] == "contains":
        return "%s contains %s" % (col, render_word(cond["lit"], rng))
    if cond["kind"] == "in":
        sp = " " if rng.random() < 0.7 else ""
        return "%s in%s(%s)" % (col, sp, ("," + sp).join(render_word(l, rng) for l in cond["lits"]))
    sp = " " if rng.random() < 0.55 else ""
    return "%s%s%s%s%s" % (col, sp, cond["op"], sp, render_word(cond["lit"], rng))


def gap(rng):
    return rng.choice([" ", " ", " ", "  ", "\t", " \n "])


def group(t):
    return "(" + t + ")"


def render(node, rng, extra=0.1, top=True):
    """--where text for the semantic tree `node`: the parentheses the grammar needs (a level inside a level of the other connective), some for a level inside a level of its own
    connective, and now and then redundant ones round a condition or the whole."""
    if node[0] == "leaf":
        text = render_leaf(node[1], rng)
        if rng.random() < extra:
            text = group(text)
            if rng.random() < 0.25:
                text = group(text)
        return text
    parts = []
    for kid in node[1]:
        t = render(kid, rng, extra, False)
        if kid[0] != "leaf" and (kid[0] != node[0] or rng.random() < 0.5):
            t = group(t)
        parts.append(t)
    text = (gap(rng) + node[0] + gap(rng)).join(parts)
    if top and rng.random() < extra / 2:
        text = group(text)
    return text


def gen_tree(rng, n, leaf_of, depth=3, kind=None):
    """A random tree of `n` leaves: levels of 2 to 4 units, mostly alternating between and and or; `leaf_of()` draws a condition."""
    if n == 1:
        return ("leaf", leaf_of())
    kind = kind or rng.choice(["and", "or"])
    k = rng.randint(2, min(n, 4))
    cuts = sorted(rng.sample(range(1, n), k - 1))
    sizes = [b - a for a, b in zip([0] + cuts, cuts + [n])]
    kids = []
    for size in sizes:
        if size == 1 or depth == 0:
            for _ in range(size):
                kids.append(("leaf", leaf_of()))
        else:
            other = "or" if kind == "and" else "and"
            kids.append(gen_tree(rng, size, leaf_of, depth - 1, other if rng.random() < 0.85 else kind))
    return (kind, kids)


def count_leaves(node):
    return len(leaves(node))


def chain(kind, conds):
    return (kind, [("leaf", c) for c in conds])


# ---- tables

HEADER = ["id", "a", "b", "c", "n", "d", "f", "t u", "or", "and", "not"]
TEXTS = ["x", "y", "z", "", "x y", "10", "9", "é", "it's", "a,b", "xx", "Y", "0", "and", "or", "(x)", "x=y", 'q"z', '"']
INT_OK = ["0", "1", "-1", "5", "10", "-10", "007", "+3", "100", "9223372036854775807", "-9223372036854775808", "42"]
DEC_OK = ["0", "1.5", "1.50", "-2", "0.25", "10.00", "-0.5", "3", ".5", "2.25", "100"]
FLOAT_OK = ["0", "1.5", "2e3", "-0.5", "0.1", "1e-3", "10", "3.25", "2.5", "100", "-7"]
BAD_INT = ["", "abc", "1.5", " 7", "-", "1e3", "9223372036854775808"]


def make_valid_table(rng, rows):
    """A table whose typed columns n (:int), d (:dec(2)) and f (:float) hold valid cells only, and the text columns awkward ones: the SQL oracle's."""
    out = []
    for i in range(rows):
        out.append([str(i)] + [rng.choice(TEXTS) for _ in range(3)] + [rng.choice(INT_OK), rng.choice(DEC_OK), rng.choice(FLOAT_OK), rng.choice(TEXTS), rng.choice(["1", "2", "x", ""]), rng.choice(["1", "2", "x", ""]), rng.choice(["1", "2", "x", ""])])
    return out


def make_bad_table(rng, rows):
    """A table whose typed columns hold bad cells (empty, text, a point, too wide) among good ones, the refusal oracle's."""
    out = []
    for i in range(rows):
        def typed(good, p):
            return rng.choice(BAD_INT) if rng.random() < p else rng.choice(good)
        out.append([str(i)] + [rng.choice(TEXTS) for _ in range(3)] + [typed(INT_OK, 0.18), typed(DEC_OK + ["1.555", "x", ""], 0.12), typed(FLOAT_OK + ["inf", "nan", "x", ""], 0.12), rng.choice(TEXTS), rng.choice(["1", "2", "x", ""]), rng.choice(["1", "2", "x", ""]), rng.choice(["1", "2", "x", ""])])
    return out


def draw_leaf(rng, typed_ok=True, pools=None):
    """A random condition over HEADER's columns: the cond dict of refimpl."""
    r = rng.random()
    if typed_ok and r < 0.4:
        col, kind = rng.choice([("n", "int"), ("n", "int"), ("d", "dec"), ("f", "float")])
    else:
        col, kind = rng.choice(["a", "b", "c", "t u", "a", "b", "or", "and", "not", "id"]), None
    cond = {"column": col}
    if kind == "int":
        cond["int"] = True
        pool = INT_OK + ["2", "3", "-2"]
    elif kind == "dec":
        cond["dec"] = 2
        pool = DEC_OK + ["2.5", "0.05"]
    elif kind == "float":
        cond["float"] = True
        pool = FLOAT_OK + ["4", "2.5"]
    else:
        pool = TEXTS + ["1", "2", "q"]
    shape = rng.random()
    if shape < 0.25:
        cond["kind"] = "in"
        cond["lits"] = [rng.choice(pool) for _ in range(rng.randint(1, 3))]
    elif shape < 0.35 and kind is None:
        cond["kind"] = "contains"
        cond["lit"] = rng.choice(["x", "y", "", "1", "é", "a,b", "t"])
        cond["op"] = None
    else:
        cond["kind"] = "cmp"
        cond["op"] = rng.choice(["=", "=", "!=", "<", "<=", ">", ">="])
        cond["lit"] = rng.choice(pool)
    return cond
