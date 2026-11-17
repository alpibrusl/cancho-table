#!/usr/bin/env python3
"""G-ORACLE of docs/computed-columns.md for the compiler and evaluator of tools/table/cexpr.cho, against Python's integers (no code shared with the tool).

    python3 scripts/cexpr_golden.py [--cases N] [--seed S]     # random expressions over random typed rows, and the hand-written refusals
    python3 scripts/cexpr_golden.py --check                    # every mutant applies exactly once (no compiler)
    python3 scripts/cexpr_golden.py --mutants                  # every mutant of cexpr.cho must make the golden fail

The probe (tests/conformance/cexpr_probe/main.cho) prints a value as NAME=text at its scale, `C status offset` for definitions refused before a row is read and
`E status column definition` for a row refused. The random cases compare the value text; the refusals compare the status.
"""
import argparse, datetime, pathlib, random, re, shutil, sys, tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import mutlib  # the shared half of the mutation scripts: `check` says every pattern occurs exactly once

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIM = 2**127
HEADER = "a,b,c,d,e,f"


def text(v, scale):
    s = str(abs(v)).rjust(scale + 1, "0")
    return ("-" if v < 0 else "") + (s if scale == 0 else s[:-scale] + "." + s[-scale:])


# ---- the reference: a tree of Python integers ------------------------------------------------------------------------------------------

class Overflow(Exception):
    pass


class DivZero(Exception):
    pass


class BadScale(Exception):
    pass


ROUND_MODES = ("round", "floor", "ceil", "trunc")
FN_ARITY = {"abs": (1, 1), "sign": (1, 1), "greatest": (2, 8), "least": (2, 8), "round": (2, 2), "floor": (2, 2), "ceil": (2, 2), "trunc": (2, 2), "coalesce": (2, 2)}


def round_to(v, s, n, mode):
    """v at scale s as a value at scale n: more digits are only zeros; fewer are decided by the mode (round: ties to even; floor, ceil, trunc)"""
    if n >= s:
        return v * 10 ** (n - s)
    d = 10 ** (s - n)
    if mode == "round":
        q, r = divmod(abs(v), d)
        if 2 * r > d or (2 * r == d and q % 2 == 1):
            q += 1
        return -q if v < 0 else q
    if mode == "floor":
        return v // d
    if mode == "ceil":
        return -((-v) // d)
    return -(abs(v) // d) if v < 0 else abs(v) // d


def ev(node, row):
    """(value, scale) of a node; raises Overflow, DivZero, BadScale"""
    k = node[0]
    if k == "col":
        _, c, kind, s = node
        return row[c][0] * 10 ** (s - row[c][1]), s
    if k == "coal":
        # coalesce(column, literal): an empty cell (None) is the literal at the column's scale, anything else is the column
        _, col, lv, ls = node
        if row[col[1]] is None:
            return lv * 10 ** (col[3] - ls), col[3]
        return ev(col, row)
    if k == "fn":
        _, name, args, extra = node
        vs = [ev(a, row) for a in args]
        if name == "abs":
            r, sc = abs(vs[0][0]), vs[0][1]
        elif name == "sign":
            r, sc = (vs[0][0] > 0) - (vs[0][0] < 0), 0
        elif name in ("greatest", "least"):
            sc = max(sv for _, sv in vs)
            xs = [v * 10 ** (sc - sv) for v, sv in vs]
            if any(abs(x) >= LIM for x in xs):
                raise Overflow()
            r = max(xs) if name == "greatest" else min(xs)
        else:
            r, sc = round_to(vs[0][0], vs[0][1], extra, name), extra
        if abs(r) >= LIM:
            raise Overflow()
        return r, sc
    if k == "lit":
        return node[1], node[2]
    if k == "dt":
        return dt_ev(node, row), 0
    if k == "if":
        # docs/functions.md section 3.4: only the chosen branch is evaluated, and the comparisons are evaluated left to right and stop at the first that decides
        _, conn, conds, a_node, b_node = node
        taken = cond_holds(conn, conds, row)
        v, sv = ev(a_node if taken else b_node, row)
        target = static_scale(node)
        r = v * 10 ** (target - sv)
        if abs(r) >= LIM:
            raise Overflow()
        return r, target
    if k == "ref":
        return ev(node[1], row)
    if k == "neg":
        v, s = ev(node[1], row)
        return -v, s
    (a, sa), (b, sb) = ev(node[1], row), ev(node[2], row)
    if k in "+-":
        sc = max(sa, sb)
        x, y = a * 10 ** (sc - sa), b * 10 ** (sc - sb)
        r = x + y if k == "+" else x - y
    elif k == "*":
        if sa + sb > 18:
            raise BadScale()
        sc, r = sa + sb, a * b
    else:
        n = node[3]
        if b == 0:
            raise DivZero()
        e = n + sb - sa
        num, den = (abs(a) * 10 ** e, abs(b)) if e >= 0 else (abs(a), abs(b) * 10 ** -e)
        q, rem = divmod(num, den)
        if 2 * rem > den or (2 * rem == den and q % 2 == 1):
            q += 1
        r, sc = (-q if (a < 0) != (b < 0) else q), n
    if abs(r) >= LIM:
        raise Overflow()
    return r, sc


CMP_OPS = ("=", "!=", "<", "<=", ">", ">=")


def compare(op, x, sx, y, sy):
    """exact: both at the larger scale (never an overflow: the comparison is not an operand)"""
    sc = max(sx, sy)
    x, y = x * 10 ** (sc - sx), y * 10 ** (sc - sy)
    return {"=": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def text_of(node, row):
    """the bytes of a text operand of a condition (docs/functions.md section 3.4, K7b1): a cell as written without quotes, or a literal"""
    if node[0] == "tcol":
        v, sc = row[node[1]]
        return text(v, sc).encode()
    return node[1].encode()


def cond_holds(conn, conds, row):
    for i, (op, left, right) in enumerate(conds):
        if left[0] in ("tcol", "tlit"):
            x, y = text_of(left, row), text_of(right, row)
            t = {"=": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
        else:
            (x, sx), (y, sy) = ev(left, row), ev(right, row)
            t = compare(op, x, sx, y, sy)
        if conn == "or" and t:
            return True
        if conn != "or" and not t:
            return False
    return conn != "or"


class BadCell(Exception):
    """a cell that is not a date, in a column read as :date: the column"""


def dt_ev(node, row):
    """docs/functions.md section 3.5: the date functions over Python's datetime.date (no code shared with the tool)"""
    _, name, args = node
    vals = []
    for a in args:
        if a[0] == "dcol":
            if row[a[1]] is None:
                raise BadCell(a[1])
            vals.append(row[a[1]])
        else:
            vals.append(a[1])
    if name == "year":
        return vals[0].year
    if name == "month":
        return vals[0].month
    if name == "day":
        return vals[0].day
    if name == "weekday":
        return vals[0].isoweekday()
    return (vals[1] - vals[0]).days


def static_scale(node):
    k = node[0]
    if k in ("dt", "tcol", "tlit"):
        return 0
    if k == "tif":
        for _, left, right in node[2]:
            static_scale(left)
            static_scale(right)
        return 0
    if k == "if":
        for _, left, right in node[2]:
            static_scale(left)
            static_scale(right)
        return max(static_scale(node[3]), static_scale(node[4]))
    if k == "col":
        return node[3]
    if k == "coal":
        return node[1][3]
    if k == "fn":
        name, args, extra = node[1], node[2], node[3]
        scales = [static_scale(a) for a in args]
        if name == "abs":
            return scales[0]
        if name == "sign":
            return 0
        if name in ("greatest", "least"):
            return max(scales)
        return extra
    if k == "lit":
        return node[2]
    if k == "ref":
        return static_scale(node[1])
    if k == "neg":
        return static_scale(node[1])
    a, b = static_scale(node[1]), static_scale(node[2])
    if k == "*":
        if a + b > 18:
            raise BadScale()
        return a + b
    return node[3] if k == "/" else max(a, b)


PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def sp(rng):
    return rng.choice(["", " ", "  "])


def atom(node):
    return node[0] in ("col", "lit", "ref", "fn", "coal", "if", "dt", "tif")


def render_date(a, rng):
    if a[0] == "dcol":
        return ("#%d:date" % (a[1] + 1)) if rng.random() < 0.15 else "%s:date" % "abcdef"[a[1]]
    return '"%s"' % a[1].isoformat()


def render(node, rng):
    k = node[0]
    if k == "tcol":
        return "abcdef"[node[1]]
    if k == "tlit":
        return '"' + node[1].replace('"', '""') + '"'
    if k == "dt":
        return node[1] + "(" + sp(rng) + (sp(rng) + "," + sp(rng)).join(render_date(a, rng) for a in node[2]) + sp(rng) + ")"
    if k in ("if", "tif"):
        _, conn, conds, a_node, b_node = node
        # a comparison of texts has a space before its operator: `<`, `>` and `!` do not end a bare word
        parts = [f"{render(l, rng)}{' ' if l[0] in ('tcol', 'tlit') else sp(rng)}{op}{sp(rng)}{render(r, rng)}" for op, l, r in conds]
        glue = " " + (conn or "and") + " "
        return "if(" + sp(rng) + glue.join(parts) + sp(rng) + "," + sp(rng) + render(a_node, rng) + sp(rng) + "," + sp(rng) + render(b_node, rng) + sp(rng) + ")"
    if k == "coal":
        _, col, lv, ls = node
        return f"coalesce({sp(rng)}{render(col, rng)}{sp(rng)},{sp(rng)}{text(lv, ls)}{sp(rng)})"
    if k == "fn":
        _, name, args, extra = node
        parts = [render(a, rng) for a in args] + ([str(extra)] if extra is not None else [])
        return f"{name}(" + sp(rng) + (sp(rng) + "," + sp(rng)).join(parts) + sp(rng) + ")"
    if k == "col":
        s = f"{'abcdef'[node[1]]}:int" if node[2] == "int" else f"{'abcdef'[node[1]]}:dec({node[3]})"
        return f"#{node[1] + 1}" + s[1:] if rng.random() < 0.15 else s
    if k == "lit":
        return text(node[1], node[2])
    if k == "ref":
        return node[2]
    if k == "neg":
        inner = render(node[1], rng)
        return "-" + sp(rng) + (inner if atom(node[1]) or node[1][0] == "neg" else f"({inner})")
    l, r = render(node[1], rng), render(node[2], rng)
    lc, rc = node[1], node[2]
    if k == "/":
        r = r if atom(rc) or rc[0] == "neg" else f"({r})"
        l = f"({l})" if lc[0] in "+-" else l
        return f"{l}{sp(rng)}/{sp(rng)}{r}{sp(rng)}@{node[3]}"
    l = f"({l})" if lc[0] in "+-*/" and PREC[lc[0]] < PREC[k] else l
    r = f"({r})" if rc[0] in "+-*/" and PREC[rc[0]] <= PREC[k] else r
    return f"{l}{sp(rng)}{k}{sp(rng)}{r}"


def paren_depth(s):
    """the deepest nesting of grouping parentheses and call parentheses; the parentheses of a type (`dec(2)`) are not a level"""
    s = re.sub(r"dec\(\d+\)", "dec", s)
    d = m = 0
    for ch in s:
        d += ch == "("
        d -= ch == ")"
        m = max(m, d)
    return m


class Longest:
    """a stand-in for the random generator that makes `render` as long as it can be: the widest spacing, no `#N` position"""

    def random(self):
        return 0.99

    def choice(self, seq):
        return max(seq, key=len)


def gen_fn(rng, depth, cols):
    """a call of one of the numeric functions of docs/functions.md section 3.2 (not coalesce: its cell must be empty, see lines_coalesce)"""
    name = rng.choice(["abs", "sign", "greatest", "least", "round", "floor", "ceil", "trunc", "round", "trunc"])
    if name in ("abs", "sign"):
        return ("fn", name, [gen(rng, depth - 1, cols, True)], None)
    if name in ("greatest", "least"):
        return ("fn", name, [gen(rng, depth - 1, cols, True) for _ in range(rng.randint(2, 4))], None)
    return ("fn", name, [gen(rng, depth - 1, cols, True)], rng.randint(0, 18))


def gen(rng, depth, cols, fns=True):
    if depth > 0 and fns and rng.random() < 0.3:
        return gen_fn(rng, depth, cols)
    if depth == 0 or rng.random() < 0.25:
        if rng.random() < 0.2:
            sc = rng.randint(0, 4)
            return ("lit", rng.randint(0, 10 ** rng.randint(1, 6)), sc)
        c = rng.randrange(6)
        kind, s = cols[c]
        return ("col", c, kind, 0 if kind == "int" else s)
    k = rng.choice(["+", "-", "*", "*", "/", "neg"])
    if k == "neg":
        return ("neg", gen(rng, depth - 1, cols, fns))
    if k == "/":
        return ("/", gen(rng, depth - 1, cols, fns), gen(rng, depth - 1, cols, fns), rng.randint(0, 18))
    return (k, gen(rng, depth - 1, cols, fns), gen(rng, depth - 1, cols, fns))


TEXT_POOL = ["0", "1", "10", "1.5", "-1", "123", "é", 'a"b', "", " ", "2", "a,b"]


def gen_text_operand(rng, texts):
    if rng.random() < 0.55:
        return ("tcol", rng.randrange(6))
    pool = TEXT_POOL + (texts or [])
    t = rng.choice(pool)
    if texts and rng.random() < 0.3:
        t = rng.choice(texts)[:rng.randint(0, 3)]   # a prefix of a cell
    return ("tlit", t)


def gen_if(rng, depth, cols, texts=None):
    """an `if` of docs/functions.md section 3.4: one to three comparisons joined by one connective; half of the time the guard of a division by the column it tests"""
    conn = rng.choice(["and", "or"])
    n = rng.choice([1, 1, 2, 3])
    conds = []
    for _ in range(n):
        if rng.random() < 0.3:
            left = gen_text_operand(rng, texts)
            right = gen_text_operand(rng, texts)
            if left[0] == "tlit" and right[0] == "tlit" and rng.random() < 0.7:
                right = ("tcol", rng.randrange(6))
            conds.append((rng.choice(CMP_OPS), left, right))
        else:
            conds.append((rng.choice(CMP_OPS), gen(rng, max(depth - 1, 0), cols, True), gen(rng, max(depth - 1, 0), cols, True)))
    if rng.random() < 0.5:
        c = rng.randrange(6)
        kind, sc = cols[c]
        col = ("col", c, kind, 0 if kind == "int" else sc)
        conds[0] = (rng.choice(["!=", "=", ">", "<"]), col, ("lit", 0, 0))
        div = ("/", gen(rng, 1, cols, False), col, rng.randint(0, 6))
        a_node, b_node = (div, ("lit", 0, 0)) if conds[0][0] in ("!=", ">", "<") else (("lit", 0, 0), div)
    else:
        arm = (lambda: gen_if(rng, depth - 1, cols, texts)) if depth > 1 and rng.random() < 0.3 else (lambda: gen(rng, max(depth - 1, 0), cols, True))
        a_node, b_node = arm(), arm()
    return ("if", conn if n > 1 else None, conds, a_node, b_node)


def tev(node, row):
    """the bytes of a text `if` (docs/functions.md section 3.4, K7b2): the leaf of the arm taken; the condition is lazy as in `ev`"""
    if node[0] in ("tcol", "tlit"):
        return text_of(node, row)
    _, conn, conds, a_node, b_node = node
    return tev(a_node if cond_holds(conn, conds, row) else b_node, row)


def gen_tif(rng, depth, cols, texts=None):
    """a text `if`: one to three comparisons (numbers or texts) joined by one connective, leaf arms or another text `if`"""
    conn = rng.choice(["and", "or"])
    n = rng.choice([1, 1, 2, 3])
    conds = []
    for _ in range(n):
        if rng.random() < 0.4:
            left, right = gen_text_operand(rng, texts), gen_text_operand(rng, texts)
            if left[0] == "tlit" and right[0] == "tlit":
                right = ("tcol", rng.randrange(6))
            conds.append((rng.choice(CMP_OPS), left, right))
        else:
            conds.append((rng.choice(CMP_OPS), gen(rng, max(depth - 1, 0), cols, True), gen(rng, max(depth - 1, 0), cols, True)))
    if rng.random() < 0.4:
        # the guard of a division in the condition
        c = rng.randrange(6)
        kind, sc = cols[c]
        col = ("col", c, kind, 0 if kind == "int" else sc)
        conds = [("!=", col, ("lit", 0, 0)), (">", ("/", gen(rng, 1, cols, False), col, rng.randint(0, 6)), ("lit", 0, 0))]
        conn = "and"

    def arm():
        if depth > 1 and rng.random() < 0.3:
            return gen_tif(rng, depth - 1, cols, texts)
        return gen_text_operand(rng, texts)
    return ("tif", conn if len(conds) > 1 else None, conds, arm(), arm())


def lines_tif(rng, n):
    """G-IFT-ORACLE for K7b2: definitions that are text `if`s, with number definitions beside them; the first refusal decides, as in lines_of"""
    out = []
    for _ in range(n):
        cols, cells, row = make_row(rng)
        texts = [text(v, sc) for v, sc in row]
        defs, exp = [], []
        for d in range(rng.randint(1, 3)):
            if rng.random() < 0.7:
                tree = gen_tif(rng, rng.randint(1, 3), cols, texts)
                for _try in range(30):
                    if size_of(tree) <= 100 and depth_of(tree) <= 9 and len(render(tree, Longest())) < 245 and paren_depth(render(tree, Longest())) <= 4:
                        break
                    tree = gen_tif(rng, rng.randint(1, 2), cols, texts)
            else:
                tree = gen(rng, rng.randint(1, 2), cols)
            try:
                static_scale(tree)
                if tree[0] == "tif":
                    exp.append(("ok", f"r{d}=" + tev(tree, row).decode()))
                else:
                    v, sc = ev(tree, row)
                    exp.append(("ok", f"r{d}={text(v, sc)}"))
            except BadScale:
                exp.append(("C", 7))
            except Overflow:
                exp.append(("E", 12))
            except DivZero:
                exp.append(("E", 11))
            rendered = render(tree, rng)
            if paren_depth(rendered) > 4:
                exp[-1] = ("C", 9)
            defs.append(f"r{d}=" + rendered)
        if any(e[0] == "C" for e in exp):
            want = "C %d" % [e[1] for e in exp if e[0] == "C"][0]
        else:
            bad = [i for i, e in enumerate(exp) if e[0] == "E"]
            want = f"E {exp[bad[0]][1]} 0 {bad[0]}" if bad else ";".join(e[1] for e in exp)
        out.append((HEADER, cells, ";".join(defs), want))
    return out


def lines_if(rng, n):
    """G-IF-ORACLE: definitions that are an `if` (alone or in an expression), against rows with zeros and wide values; the first refusal decides, as in lines_of"""
    out = []
    for _ in range(n):
        cols, cells, row = make_row(rng)
        defs, exp = [], []
        for d in range(rng.randint(1, 3)):
            tree = None
            for _try in range(30):
                t = gen_if(rng, rng.randint(1, 3), cols, [text(v, sc) for v, sc in row])
                if rng.random() < 0.3:
                    t = (rng.choice(["+", "-", "*"]), t, gen(rng, 1, cols, False))
                if size_of(t) <= 100 and depth_of(t) <= 9 and len(render(t, Longest())) < 245:
                    tree = t
                    break
            if tree is None:
                tree = gen(rng, 1, cols)
            try:
                static_scale(tree)
                v, sc = ev(tree, row)
                exp.append(("ok", f"r{d}={text(v, sc)}"))
            except BadScale:
                exp.append(("C", 7))
            except Overflow:
                exp.append(("E", 12))
            except DivZero:
                exp.append(("E", 11))
            rendered = render(tree, rng)
            if paren_depth(rendered) > 4:
                exp[-1] = ("C", 9)
            defs.append(f"r{d}=" + rendered)
        if any(e[0] == "C" for e in exp):
            want = "C %d" % [e[1] for e in exp if e[0] == "C"][0]
        else:
            bad = [i for i, e in enumerate(exp) if e[0] == "E"]
            want = f"E {exp[bad[0]][1]} 0 {bad[0]}" if bad else ";".join(e[1] for e in exp)
        out.append((HEADER, cells, ";".join(defs), want))
    return out


IF_HAND = [
    # cells are 10,20,30,40,50,60 (a to f)
    ("r=if(a:int < b:int, 1, 2)", "r=1"),
    ("r=if(a:int > b:int, 1, 2)", "r=2"),
    ("r=if(a:int = 10, 1, 2)", "r=1"),
    ("r=if(a:int != 10, 1, 2)", "r=2"),
    ("r=if(a:int <= 10, 1, 2)", "r=1"),
    ("r=if(a:int >= 11, 1, 2)", "r=2"),
    ("r=if(a:int<b:int,1,2)", "r=1"),
    # the scale of the result is the larger of the arms, the other brought to it exactly
    ("r=if(a:int < b:int, 1, 2.50)", "r=1.00"),
    ("r=if(a:int > b:int, 1, 2.50)", "r=2.50"),
    ("r=if(a:int < b:int, 1.5, 2)", "r=1.5"),
    # exact comparison across scales
    ("r=if(a:dec(1) = 10, 1, 0)", "r=1"),
    ("r=if(1.50 = 1.5, 1, 0)", "r=1"),
    ("r=if(0.1 + 0.2 = 0.3, 1, 0)", "r=1"),
    # the guard: the arm that divides by zero is not taken
    ("r=if(a:int != 0, 100 / a:int @2, 0)", "r=10.00"),
    ("r=if(a:int - 10 != 0, 100 / (a:int - 10) @2, -1)", "r=-1.00"),
    ("r=if(a:int - 10 = 0, -1, 100 / (a:int - 10) @2)", "r=-1.00"),
    # the same refusal in the taken arm is refused
    ("r=if(a:int - 10 = 0, 100 / (a:int - 10) @2, -1)", "E 11 0 0"),
    # and, or, and the stop at the first that decides
    ("r=if(a:int < b:int and b:int < c:int and c:int < d:int, 1, 0)", "r=1"),
    ("r=if(a:int < b:int and b:int > c:int, 1, 0)", "r=0"),
    ("r=if(a:int > b:int or b:int < c:int, 1, 0)", "r=1"),
    ("r=if(a:int > b:int or b:int > c:int or c:int > d:int, 1, 0)", "r=0"),
    # an unreached comparison is never evaluated: its division by zero is not a refusal
    ("r=if(a:int > b:int and 1 / (a:int - 10) @0 > 0, 1, 0)", "r=0"),
    ("r=if(a:int < b:int or 1 / (a:int - 10) @0 > 0, 1, 0)", "r=1"),
    ("r=if(a:int < b:int and 1 / (a:int - 10) @0 > 0, 1, 0)", "E 11 0 0"),
    # nesting, and an if inside an expression
    ("r=if(a:int < b:int, if(c:int > d:int, 1, 2), 3)", "r=2"),
    ("r=if(a:int < b:int, 1, if(c:int > d:int, 2, 3))", "r=1"),
    ("r=1 + if(a:int < b:int, 10, 20) * 2", "r=21"),
    ("r=if(if(a:int < b:int, 1, 0) = 1, 7, 8)", "r=7"),
    ("r=abs(if(a:int < b:int, -5, 5))", "r=5"),
    ("r=greatest(if(a:int < b:int, 1, 2), 3)", "r=3"),
    # a function in the condition, in an arm, a reference to an earlier definition
    ("r=if(abs(a:int - 20) = 10, 1, 0)", "r=1"),
    ("x=a:int + 1;r=if(x > 10, x, 0)", "x=11;r=11"),
    # a zero divisor or overflow in the arm that is not taken is not an error; in the taken one it is
    ("r=if(a:int > 0, 1, 999999999999999999 * 999999999999999999 * 999999999999999999)", "r=1"),
    ("r=if(a:int < 0, 1, 999999999999999999 * 999999999999999999 * 999999999999999999)", "E 12 0 0"),
    # a cell that is not a number, in an arm not reached
    ("r=if(a:int > 0, 1, z:int)", "C 2"),
    # refusals of the compiler
    ("r=if(a:int < b:int, 1)", "C 14"),
    ("r=if(a:int < b:int, 1, 2, 3)", "C 14"),
    ("r=if(a:int, 1, 2)", "C 1"),
    ("r=if(a:int < b:int and b:int < c:int or c:int < d:int, 1, 2)", "C 19"),
    ("r=if(a:int < b:int or b:int < c:int and c:int < d:int, 1, 2)", "C 19"),
    ("r=if(a:int<1 and a:int<2 and a:int<3 and a:int<4, 1, 2)", "r=2"),
    ("r=if(a:int<1 and a:int<2 and a:int<3 and a:int<4 and a:int<5, 1, 2)", "C 20"),
    ("r=if(a:int<1 or a:int<2 or a:int<3 or a:int<4 or a:int<5, 1, 2)", "C 20"),
    ("r=if(a:int < b:int, 1, 2", "C 1"),
    ("r=if(a:int < , 1, 2)", "C 1"),
    ("r=if(< 1, 1, 2)", "C 2"),
    ("r=if(a:int < b:int < c:int, 1, 2)", "C 1"),
    ("r=if(a:int < b:int, , 2)", "C 1"),
    ("r=if(a:int < 1 and, 1, 2)", "C 1"),
    ("r=if()", "C 1"),
    ("r=if(a:int and b:int < 1, 1, 2)", "C 1"),
    ("r=if(a:int < 1 or and b:int < 1, 1, 2)", "C 2"),
    ("r=if(a:int < 1 xor b:int < 1, 1, 2)", "C 1"),
    ("r=a:int < 1", "C 1"),
    ("r=(a:int < 1)", "C 1"),
    ("r=abs(a:int < 1)", "C 1"),
    ("r=if(a:int < b:int, 1, 2) < 3", "C 1"),
    ("r=if (a:int < b:int, 1, 2)", "C 2"),
    # a product past scale 18 in an arm that is not taken is the compiler's refusal
    ("r=if(a:int > 0, 1, 0.1234567890 * 0.1234567890)", "C 7"),
    # four levels of parentheses: an if is one
    ("r=if(a:int < b:int, if(a:int < b:int, if(a:int < b:int, if(a:int < b:int, 1, 2), 2), 2), 2)", "r=1"),
    ("r=if(a:int < b:int, if(a:int < b:int, if(a:int < b:int, if(a:int < b:int, if(a:int < b:int, 1, 2), 2), 2), 2), 2)", "C 9"),
    # a text function in an arm: no conversion
    ('r=if(a:int < b:int, upper("x"), 2)', "C 16"),
]


IF_TEXT = [
    # a..f are the cells 10,20,30,40,50,60 read as texts (docs/functions.md section 3.4, K7b1): bytewise, no collation, no number
    ('r=if(a = "10", 1, 0)', "r=1"), ('r=if(a = "1", 1, 0)', "r=0"), ('r=if(a != "1", 1, 0)', "r=1"), ('r=if(a < "2", 1, 0)', "r=1"),
    ('r=if(a > "2", 1, 0)', "r=0"), ('r=if(a <= "10", 1, 0)', "r=1"), ('r=if(a >= "10", 1, 0)', "r=1"), ('r=if(a > "1", 1, 0)', "r=1"), ('r=if(a < "100", 1, 0)', "r=1"),
    ('r=if("10" = a, 1, 0)', "r=1"), ('r=if("9" > a, 1, 0)', "r=1"), ('r=if(a < b, 1, 0)', "r=1"), ('r=if(a = a, 1, 0)', "r=1"), ('r=if(b >= a and b < c, 1, 0)', "r=1"),
    ('r=if(a = "", 1, 0)', "r=0"), ('r=if("x" = "x", 1, 2)', "r=1"), ('r=if("x" < "y", 1, 2)', "r=1"), ('r=if("" < "a", 1, 2)', "r=1"),
    ('r=if(a="10", 1, 0)', "r=1"), ('r=if(a ="10", 1, 0)', "r=1"), ('r=if( a  =  "10" , 1, 0)', "r=1"),
    # mixed with a comparison of numbers, joined by one connective, lazily
    ('r=if(a = "10" and b:int > 5, 1, 0)', "r=1"), ('r=if(a = "11" or b:int > 5, 1, 0)', "r=1"), ('r=if(b:int > 5 and a = "11", 1, 0)', "r=0"),
    ('r=if(a = "11" and 1 / (a:int - 10) @0 > 0, 1, 0)', "r=0"), ('r=if(a = "10" or 1 / (a:int - 10) @0 > 0, 1, 0)', "r=1"), ('r=if(a = "10" and 1 / (a:int - 10) @0 > 0, 1, 0)', "E 11 0 0"),
    ('r=if(a = "10", 1.5, 2)', "r=1.5"), ('r=if(a != "10", 1, 2.50)', "r=2.50"), ('r=1 + if(a = "10", 10, 20) * 2', "r=21"), ('x=a:int*2;r=if(a = "10", x, 0)', "x=20;r=20"),
    # nesting
    ('r=if(a = "10", if(b = "20", 1, 2), 3)', "r=1"), ('r=if(a = "11", 1, if(b = "20", 2, 3))', "r=2"),
    # a text against a number, a call as an operand, a column that is not there, no operator, a second operator
    ("r=if(a = 1, 1, 0)", "C 16"), ('r=if(a:int = "x", 1, 0)', "C 16"), ("r=if(a = b:int, 1, 0)", "C 16"), ('r=if(1 = "1", 1, 0)', "C 16"), ('r=if("1" = 1, 1, 0)', "C 16"),
    ('r=if(a = lower(b), 1, 0)', "C 16"), ('r=if(lower(a) = "x", 1, 0)', "C 16"), ('r=if(z = "x", 1, 0)', "C 2"), ('r=if(a, 1, 2)', "C 1"), ('r=if("x", 1, 2)', "C 1"),
    ('r=if(a = , 1, 2)', "C 1"), ('r=if(a = "x" "y", 1, 2)', "C 1"), ('r=if(a == "x", 1, 2)', "C 1"), ('r=if(a ! "x", 1, 2)', "C 1"), ('r=if(a = "x, 1, 2)', "C 15"),
    ('r=if(a="x" and a="y" or a="z", 1, 2)', "C 19"), ('r=if(a="1" and a="2" and a="3" and a="4" and a="5", 1, 2)', "C 20"),
    # a comparison operator after an untyped column with no space is a word: the limit the design names
    ('r=if(a<"2", 1, 0)', "C 2"), ('r=if(a>"2", 1, 0)', "C 2"),
    # a text outside a condition is not a number
    ('r=a = "10"', "C 5"), ('r=if(a = "10", a, 1)', "C 16"), ('r=if(a = "10", "x", 1)', "C 16"),
]
# cells that are quoted, with doubled quotes, commas, empty, UTF-8
IF_TEXT_CELLS = [
    ('"a""b",x,3,4,5,6', 'r=if(a = "a""b", 1, 0)', "r=1"), ('"a""b",x,3,4,5,6', 'r=if(a = "a\"b", 1, 0)', "C 1"), ('"a""b",x,3,4,5,6', 'r=if(a = "ab", 1, 0)', "r=0"),
    ('"a""b",x,3,4,5,6', 'r=if(a < "a#", 1, 0)', "r=1"), ('"a""b",x,3,4,5,6', 'r=if(a > "a!", 1, 0)', "r=1"),
    ('"a,b",x,3,4,5,6', 'r=if(a = "a,b", 1, 0)', "r=1"), ('"a,b",x,3,4,5,6', 'r=if(a > "a", 1, 0)', "r=1"),
    (',x,3,4,5,6', 'r=if(a = "", 1, 0)', "r=1"), (',x,3,4,5,6', 'r=if(a < b, 1, 0)', "r=1"), ('"",x,3,4,5,6', 'r=if(a = "", 1, 0)', "r=1"),
    ('é,e,3,4,5,6', 'r=if(a = "é", 1, 0)', "r=1"), ('é,e,3,4,5,6', 'r=if(a > b, 1, 0)', "r=1"), ('é,e,3,4,5,6', 'r=if(a > "z", 1, 0)', "r=1"),
    ('é,e,3,4,5,6', 'r=if("é" = "é", 1, 0)', "r=1"), ('"é",e,3,4,5,6', 'r=if(a = "é", 1, 0)', "r=1"),
    ('10,x,3,4,5,6', 'r=if(a = "10" and b:int > 1, 1, 0)', "E 101 1 0"), ('10,x,3,4,5,6', 'r=if(a = "11" and b:int > 1, 1, 0)', "r=0"),
    ('10,x,3,4,5,6', 'r=if(a = "11" or b:int > 1, 1, 0)', "E 101 1 0"), ('10,x,3,4,5,6', 'r=if(a = "10" or b:int > 1, 1, 0)', "r=1"),
]


IF_TIF = [
    # a text `if` (docs/functions.md section 3.4, K7b2): the leaf of the arm taken; a..f are 10,20,30,40,50,60
    ('r=if(a:int > 5, "big", "small")', "r=big"), ('r=if(a:int < 5, "big", "small")', "r=small"), ('r=if(a = "10", "ten", "other")', "r=ten"), ('r=if(a != "10", "ten", "other")', "r=other"),
    ('r=if(a = "10", b, c)', "r=20"), ('r=if(a = "11", b, c)', "r=30"), ('r=if(a:int > 5, "", "x")', "r="), ('r=if(a:int > 5, "a""b", "z")', 'r=a"b'),
    ('r=if(a:int > 100, "x", if(a:int > 5, "mid", "low"))', "r=mid"), ('r=if(a:int > 5, if(b:int > 5, "bb", "ba"), "n")', "r=bb"), ('r=if(a:int > 5, if(b:int > 50, "bb", "ba"), "n")', "r=ba"),
    ('r=if(a:int > 5 and b:int > 5, "both", "no")', "r=both"), ('r=if(a:int > 50 or b:int > 5, "some", "no")', "r=some"), ('r=if(a:int > 50 or b:int > 50, "some", "no")', "r=no"),
    ('r=if(a:int > 5 and a = "10", "y", "n")', "r=y"), ('r=if(a = "11" and 1 / (a:int - 10) @0 > 0, "y", "n")', "r=n"), ('r=if(a = "10" and 1 / (a:int - 10) @0 > 0, "y", "n")', "E 11 0 0"),
    ('r=if(a:int != 0 and 100 / a:int @0 > 5, "y", "n")', "r=y"), ('r=if("x" = "x", "yes", "no")', "r=yes"), ('r=if(a = a, a, b)', "r=10"),
    ('n=a:int+1;r=if(n > 5, "yes", "no")', "n=11;r=yes"), ('r=if(a:int > 5, "yes", "no");n=a:int+1', "r=yes;n=11"),
    # a text and a number in one `if`, a text `if` where a number or a text function is, an operator after a leaf, a call as an arm
    ('r=if(a:int > 5, "x", 1)', "C 16"), ('r=if(a:int > 5, 1, "x")', "C 16"), ('r=if(a:int > 5, a, 1)', "C 16"), ('r=if(a:int > 5, 1, b)', "C 16"),
    ('r=1 + if(a:int > 5, "x", "y")', "C 16"), ('r=if(a:int > 5, "x", "y") + 1', "C 16"), ('r=abs(if(a:int > 5, "x", "y"))', "C 16"), ('r=-if(a:int > 5, "x", "y")', "C 16"),
    ('r=if(if(a:int > 5, "x", "y") = "x", 1, 2)', "C 16"), ('r=if(1 = if(a:int > 5, "x", "y"), 1, 2)', "C 16"), ('r=greatest(if(a:int > 5, "x", "y"), 1)', "C 16"),
    ('r=if(a:int > 5, "x" + 1, "y")', "C 16"), ('r=if(a:int > 5, a * 2, "y")', "C 16"), ('r=if(a:int > 5, upper(a), "y")', "C 16"), ('r=if(a:int > 5, "x", len(a))', "C 16"),
    ('r=if(a:int > 5, "x", "y");s=r+1', "C 16"), ('r=if(a:int > 5, "x", "y");u=upper(r)', "C 16"), ('r=if(a:int > 5, "x", "y");u=if(r = "x", 1, 2)', "C 16"),
    ('r=if(a:int > 5, "x")', "C 14"), ('r=if(a:int > 5, "x", "y", "z")', "C 14"), ('r=if(a:int > 5, , "y")', "C 1"), ('r=if(a:int > 5, "x", "y"', "C 1"), ('r=if(a:int > 5, "x", z)', "C 2"),
    ('r=if(a:int > 5, "x, "y")', "C 1"),
]
IF_TIF_CELLS = [
    # quoted cells, empty cells and UTF-8 as leaves: the doubled quotes of a quoted cell are undone
    ('"x""y",2,3,4,5,6', 'r=if(b:int > 1, a, "z")', 'r=x"y'), ('"x""y",2,3,4,5,6', 'r=if(b:int > 5, a, "z")', "r=z"), ('"x,y",2,3,4,5,6', 'r=if(b:int > 1, a, "z")', "r=x,y"),
    (',2,3,4,5,6', 'r=if(b:int > 1, a, "z")', "r="), ('"",2,3,4,5,6', 'r=if(b:int > 1, a, "z")', "r="), ('é,2,3,4,5,6', 'r=if(b:int > 1, a, "z")', "r=é"), ('"é",2,3,4,5,6', 'r=if(a = "é", "same", "diff")', "r=same"),
    ('0,2,3,4,5,6', 'r=if(a:int != 0 and 100 / a:int @0 > 5, "y", "n")', "r=n"), ('x,2,3,4,5,6', 'r=if(b:int > 1, "ok", a:int)', "C 16"), ('x,2,3,4,5,6', 'r=if(b:int > 1, "ok", "no")', "r=ok"),
    # a number read in the condition that is not one is refused as ever; one not reached is not read
    ('1,x,3,4,5,6', 'r=if(a:int > 5 and b:int > 1, "y", "n")', "r=n"), ('9,x,3,4,5,6', 'r=if(a:int > 5 and b:int > 1, "y", "n")', "E 101 1 0"),
]


IF_CELLS = [
    # a cell that is not a number, in the arm that is taken and in the one that is not
    ("10,x,3,4,5,6", "r=if(a:int > 0, 1, b:int)", "r=1"),
    ("10,x,3,4,5,6", "r=if(a:int > 0, b:int, 1)", "E 101 1 0"),
    ("10,x,3,4,5,6", "r=if(a:int < 0, 1, b:int)", "E 101 1 0"),
    ("10,x,3,4,5,6", "r=if(a:int < 0 and b:int > 1, 1, 2)", "r=2"),
    ("10,x,3,4,5,6", "r=if(a:int > 0 and b:int > 1, 1, 2)", "E 101 1 0"),
    ("10,x,3,4,5,6", "r=if(a:int > 0 or b:int > 1, 1, 2)", "r=1"),
    ("10,x,3,4,5,6", "r=if(b:int > 1 or a:int > 0, 1, 2)", "E 101 1 0"),
    ("10,,3,4,5,6", "r=if(a:int > 0, coalesce(b:int, 7), 0)", "r=7"),
]


def if_hand_cases():
    return [(HEADER, "10,20,30,40,50,60", defs, want) for defs, want in IF_TIF] + [(HEADER, cells, defs, want) for cells, defs, want in IF_TIF_CELLS] + [(HEADER, "10,20,30,40,50,60", defs, want) for defs, want in IF_TEXT] + [(HEADER, cells, defs, want) for cells, defs, want in IF_TEXT_CELLS] + [(HEADER, "10,20,30,40,50,60", defs, want) for defs, want in IF_HAND] + [(HEADER, cells, defs, want) for cells, defs, want in IF_CELLS]


# ---- dates (docs/functions.md section 3.5): G-DT-ORACLE ----------------------------------------------------------------------------------

DT_SPECIAL = [datetime.date(1, 1, 1), datetime.date(9999, 12, 31), datetime.date(1969, 12, 31), datetime.date(1970, 1, 1), datetime.date(2000, 2, 29), datetime.date(1900, 2, 28),
              datetime.date(1900, 3, 1), datetime.date(2100, 3, 1), datetime.date(2024, 2, 29), datetime.date(1600, 2, 29), datetime.date(1, 3, 1), datetime.date(4, 2, 29)]
DT_BAD = ["2026-02-30", "2026-13-01", "", "x", "2026-1-01", "0000-01-01", "10000-01-01", "2026-02-29", "1900-02-29", "2026/01/01", " 2026-01-01", "2026-01-01T00:00:00Z"]


def rand_date(rng):
    if rng.random() < 0.3:
        return rng.choice(DT_SPECIAL)
    return datetime.date.fromordinal(rng.randint(1, 3652059))


def make_dt_row(rng, p_bad=0.08):
    cells, row = [], []
    for _ in range(6):
        if rng.random() < p_bad:
            cells.append(rng.choice(DT_BAD))
            row.append(None)
        else:
            d = rand_date(rng)
            cells.append(d.isoformat())
            row.append(d)
    return ",".join(cells), row


def gen_dt_leaf(rng):
    name = rng.choice(["year", "month", "day", "weekday", "days_between", "days_between"])

    def operand():
        return ("dcol", rng.randrange(6)) if rng.random() < 0.7 else ("dlit", rand_date(rng))
    return ("dt", name, [operand() for _ in range(2 if name == "days_between" else 1)])


def gen_dt(rng, depth):
    """a number built from the date functions: a leaf, an operator, a function of K5, or an `if` on two of them"""
    if depth == 0 or rng.random() < 0.35:
        return gen_dt_leaf(rng) if rng.random() < 0.85 else ("lit", rng.randint(0, 400), 0)
    k = rng.choice(["+", "-", "*", "/", "if", "if", "fn"])
    if k == "if":
        conds = [(rng.choice(CMP_OPS), gen_dt(rng, depth - 1), gen_dt(rng, depth - 1))]
        return ("if", None, conds, gen_dt(rng, depth - 1), gen_dt(rng, depth - 1))
    if k == "fn":
        return ("fn", "abs", [gen_dt(rng, depth - 1)], None)
    if k == "/":
        return ("/", gen_dt(rng, depth - 1), gen_dt(rng, depth - 1), rng.randint(0, 4))
    return (k, gen_dt(rng, depth - 1), gen_dt(rng, depth - 1))


def lines_dt(rng, n):
    out = []
    for _ in range(n):
        cells, row = make_dt_row(rng)
        defs, exp = [], []
        for d in range(rng.randint(1, 3)):
            tree = gen_dt(rng, rng.randint(0, 3))
            for _try in range(30):
                if size_of(tree) <= 60 and depth_of(tree) <= 8 and len(render(tree, Longest())) < 245 and paren_depth(render(tree, Longest())) <= 4:
                    break
                tree = gen_dt(rng, rng.randint(0, 2))
            try:
                v, sc = ev(tree, row)
                exp.append(("ok", f"r{d}={text(v, sc)}"))
            except BadCell as e:
                exp.append(("D", e.args[0]))
            except Overflow:
                exp.append(("E", 12))
            except DivZero:
                exp.append(("E", 11))
            defs.append(f"r{d}=" + render(tree, rng))
        bad = [i for i, e in enumerate(exp) if e[0] in "ED"]
        if not bad:
            want = ";".join(e[1] for e in exp)
        elif exp[bad[0]][0] == "D":
            want = f"D {exp[bad[0]][1]} {bad[0]}"
        else:
            want = f"E {exp[bad[0]][1]} 0 {bad[0]}"
        out.append((HEADER, cells, ";".join(defs), want))
    return out


DT_CELLS = "2026-11-16,1970-01-01,2000-02-29,1900-02-28,0001-01-01,9999-12-31"
DT_HAND = [
    # a..f: 2026-11-16 (a Monday), 1970-01-01 (a Thursday), 2000-02-29 (a Tuesday), 1900-02-28, 0001-01-01 (a Monday), 9999-12-31 (a Friday)
    ("r=year(a:date)", "r=2026"), ("r=month(a:date)", "r=11"), ("r=day(a:date)", "r=16"), ("r=weekday(a:date)", "r=1"),
    ("r=weekday(b:date)", "r=4"), ("r=weekday(c:date)", "r=2"), ("r=weekday(e:date)", "r=1"), ("r=weekday(f:date)", "r=5"),
    ("r=year(e:date);m=month(e:date);dd=day(e:date)", "r=1;m=1;dd=1"),
    ("r=year(f:date);m=month(f:date);dd=day(f:date)", "r=9999;m=12;dd=31"),
    ("r=day(c:date)", "r=29"), ("r=day(d:date)", "r=28"),
    ("r=days_between(b:date, c:date)", "r=11016"), ("r=days_between(c:date, b:date)", "r=-11016"), ("r=days_between(a:date, a:date)", "r=0"),
    ("r=days_between(e:date, f:date)", "r=3652058"),
    ('r=days_between("2000-01-01", "2000-03-01")', "r=60"), ('r=days_between(c:date, "2000-03-01")', "r=1"), ('r=days_between("1999-12-31", "2000-01-01")', "r=1"),
    ('r=days_between("1900-02-28", "1900-03-01")', "r=1"), ('r=days_between("2100-02-28", "2100-03-01")', "r=1"), ('r=days_between("2024-02-28", "2024-03-01")', "r=2"),
    ('r=year("2026-11-16")', "r=2026"), ('r=weekday("1970-01-01")', "r=4"),
    ("r=year(a:date)*100+month(a:date)", "r=202611"),
    ("r=if(days_between(b:date, a:date) > 1000, 1, 0)", "r=1"), ("r=if(weekday(a:date) = 1, year(a:date), 0)", "r=2026"),
    ("r=year(a:date);s=r+1", "r=2026;s=2027"),
    ("r=year( a:date )", "r=2026"), ("r=days_between( a:date , b:date )", "r=-20773"), ("r=year(#1:date)", "r=2026"),
    # refusals of the compiler
    ('r=year("2026-02-30")', "C 15"), ('r=year("2026-1-01")', "C 15"), ('r=year("2026-11-16)', "C 15"), ("r=year(a:int)", "C 16"), ("r=year(a)", "C 16"), ("r=year(a:dec(2))", "C 16"),
    ("r=year(1)", "C 16"), ("r=year()", "C 14"), ("r=year(a:date, b:date)", "C 14"), ("r=days_between(a:date)", "C 14"), ("r=days_between(a:date, b:date, c:date)", "C 14"),
    ("r=year(z:date)", "C 2"), ("r=year(a:date", "C 1"), ("r=year (a:date)", "C 2"), ("r=a:date", "C 1"), ("r=a:date + 1", "C 1"),
    ("r=Year(a:date)", "C 13"), ("r=1+add_days(a:date, 1)", "C 16"), ("r=add_days(a:date, 1)+1", "C 16"), ("r=year(a:date);s=year(r)", "C 16"),
]
# a cell that is not a date (the status is the reader's, from 1000: the column and the definition are the tool's)
DT_CELL_CASES = [
    ("x,1970-01-01,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=year(a:date)", "D 0 0"),
    ("2026-11-16,x,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=days_between(a:date, b:date)", "D 1 0"),
    ("2026-11-16,2026-02-30,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=year(a:date);s=day(b:date)", "D 1 1"),
    # the bad cell is in the arm that is not taken, or in a comparison that is not reached
    ("2026-11-16,x,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=if(year(a:date) > 2000, 1, year(b:date))", "r=1"),
    ("2026-11-16,x,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=if(year(a:date) < 2000, 1, year(b:date))", "D 1 0"),
    ("2026-11-16,x,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=if(year(a:date) < 2000 and year(b:date) > 1, 1, 2)", "r=2"),
    ("2026-11-16,,2000-02-29,1900-02-28,0001-01-01,9999-12-31", "r=year(b:date)", "D 1 0"),
    ('"2026-11-16",1970-01-01,2000-02-29,1900-02-28,0001-01-01,9999-12-31', "r=year(a:date)", "r=2026"),
]


def dt_hand_cases():
    return [(HEADER, DT_CELLS, defs, want) for defs, want in DT_HAND] + [(HEADER, cells, defs, want) for cells, defs, want in DT_CELL_CASES]


def make_row(rng):
    cols, cells, row = [], [], []
    for _ in range(6):
        if rng.random() < 0.4:
            v = rng.choice([0, 1, -1, rng.randint(-10**rng.randint(1, 18) + 1, 10**rng.randint(1, 18) - 1), 2**62])
            cols.append(("int", 0))
            cells.append(str(v))
            row.append((v, 0))
        else:
            s = rng.randint(1, 8)
            digits = rng.randint(1, 17)
            v = rng.randint(-10**digits + 1, 10**digits - 1)
            cols.append(("dec", s))
            cells.append(text(v, s))
            row.append((v, s))
    return cols, ",".join(cells), row


def lines_of(rng, n):
    out = []
    for _ in range(n):
        cols, cells, row = make_row(rng)
        defs, exp = [], []
        for d in range(rng.randint(1, 3)):
            tree = gen(rng, rng.randint(1, 4), cols)
            for _ in range(30):
                # the limits (256 bytes, 128 instructions, 10 pending operands) have their own hand cases: a generated tree stays inside them
                if size_of(tree) <= 100 and depth_of(tree) <= 9 and len(render(tree, Longest())) < 245:
                    break
                tree = gen(rng, rng.randint(1, 3), cols)
            try:
                static_scale(tree)
                v, s = ev(tree, row)
                exp.append(("ok", f"r{d}={text(v, s)}"))
            except BadScale:
                exp.append(("C", 7))
            except Overflow:
                exp.append(("E", 12))
            except DivZero:
                exp.append(("E", 11))
            rendered = render(tree, rng)
            if paren_depth(rendered) > 4:
                exp[-1] = ("C", 9)
            defs.append(f"r{d}=" + rendered)
        # the first definition that is refused decides; a compile refusal comes before any row error
        if any(e[0] == "C" for e in exp):
            want = "C %d" % [e[1] for e in exp if e[0] == "C"][0]
        else:
            bad = [i for i, e in enumerate(exp) if e[0] == "E"]
            want = f"E {exp[bad[0]][1]} 0 {bad[0]}" if bad else ";".join(e[1] for e in exp)
        # the order the tool evaluates in is the definition order: values of earlier definitions are not printed on a failure
        out.append((HEADER, cells, ";".join(defs), want))
    return out


def size_of(node):
    k = node[0]
    if k in ("dt", "tcol", "tlit"):
        return 1
    if k in ("if", "tif"):
        return sum(size_of(l) + size_of(r) + 2 for _, l, r in node[2]) + size_of(node[3]) + size_of(node[4]) + 3
    if k in ("col", "lit"):
        return 1
    if k == "coal":
        return 2
    if k == "fn":
        return sum(size_of(a) for a in node[2]) + 1
    if k == "ref":
        return size_of(node[1])
    if k == "neg":
        return size_of(node[1]) + 1
    return size_of(node[1]) + size_of(node[2]) + 1


def depth_of(node):
    """most operands pending at once in the postfix program of the expanded tree"""
    k = node[0]
    if k in ("tcol", "tlit"):
        return 0
    if k in ("col", "lit", "dt"):
        return 1
    if k == "coal":
        return 1
    if k in ("if", "tif"):
        return max([max(depth_of(l), 1 + depth_of(r)) for _, l, r in node[2]] + [depth_of(node[3]), depth_of(node[4])])
    if k == "fn":
        return max(i + depth_of(a) for i, a in enumerate(node[2]))
    if k == "ref":
        return depth_of(node[1])
    if k == "neg":
        return depth_of(node[1])
    return max(depth_of(node[1]), 1 + depth_of(node[2]))


def gen_chain(rng, depth, cols, refs):
    """like gen, with leaves that may be an earlier definition: ("ref", its tree, its name)"""
    if depth == 0 or rng.random() < 0.25:
        if refs and rng.random() < 0.45:
            tree, name = rng.choice(refs)
            return ("ref", tree, name)
        return gen(rng, 0, cols)
    k = rng.choice(["+", "-", "*", "*", "/", "neg"])
    if k == "neg":
        return ("neg", gen_chain(rng, depth - 1, cols, refs))
    if k == "/":
        return ("/", gen_chain(rng, depth - 1, cols, refs), gen_chain(rng, depth - 1, cols, refs), rng.randint(0, 18))
    return (k, gen_chain(rng, depth - 1, cols, refs), gen_chain(rng, depth - 1, cols, refs))


def chain_lines(rng, n):
    """G-CHAIN-ORACLE: two to four definitions, each of which may name the ones before it; the first refusal decides, as in lines_of"""
    out = []
    for _ in range(n):
        cols, cells, row = make_row(rng)
        defs, exp, refs = [], [], []
        for d in range(rng.randint(2, 4)):
            tree = None
            for _try in range(12):
                t = gen_chain(rng, rng.randint(1, 3), cols, refs)
                if size_of(t) <= 40 and depth_of(t) <= 8 and paren_depth(render(t, random.Random(0))) <= 4:
                    tree = t
                    break
            if tree is None:
                tree = gen(rng, 1, cols)
            name = f"r{d}"
            try:
                static_scale(tree)
                v, s = ev(tree, row)
                exp.append(("ok", f"{name}={text(v, s)}"))
            except BadScale:
                exp.append(("C", 7))
            except Overflow:
                exp.append(("E", 12))
            except DivZero:
                exp.append(("E", 11))
            defs.append(f"{name}=" + render(tree, rng))
            refs.append((tree, name))
        if any(e[0] == "C" for e in exp):
            want = "C %d" % [e[1] for e in exp if e[0] == "C"][0]
        else:
            bad = [i for i, e in enumerate(exp) if e[0] == "E"]
            want = f"E {exp[bad[0]][1]} 0 {bad[0]}" if bad else ";".join(e[1] for e in exp)
        out.append((HEADER, cells, ";".join(defs), want))
    return out


CHAIN_HAND = [
    # cells are 10,20,30,40,50,60
    ("r0=a:int+b:int;r1=r0*c:int", "r0=30;r1=900"),
    ("p=a:dec(1)*b:dec(1);q=p+1", "p=200.00;q=201.00"),
    ("x=a:int+b:int;y=x/c:int@3", "x=30;y=1.000"),
    ("x=a:int;y=-x*(x+1)", "x=10;y=-110"),
    ("x=a:int;y=x+1;z=y*y", "x=10;y=11;z=121"),
    ("x=a:int;y=x+1;z=x*y", "x=10;y=11;z=110"),
    ("'my col'=a:int;y='my col'+1", "my col=10;y=11"),
    ("x=y+1;y=a:int", "C 2"),
    ("x=a:int;y=x:int+1", "C 1"),
    ("x=a:int;y=x : int+1", "C 1"),
    ("x=a:int;x=x+1", "C 4"),
    ("a=b:int;y=a+1", "C 4"),
    ("x=a:int;y=#7+1", "C 2"),
    ("r0=a:int+a:int;r1=r0+r0;r2=r1+r1;r3=r2+r2;r4=r3+r3;r5=r4+r4", "r0=20;r1=40;r2=80;r3=160;r4=320;r5=640"),
    ("r0=a:int+a:int;r1=r0+r0;r2=r1+r1;r3=r2+r2;r4=r3+r3;r5=r4+r4;r6=r5+r5", "C 9"),
]


def chain_hand_cases():
    return [(HEADER, "10,20,30,40,50,60", defs, want) for defs, want in CHAIN_HAND]


HAND = [
    ("a,b,c,d,e,f", "r=z:int+1", "C 2"),
    ("a,b,c,d,e,f", "z:int+1", "C 1"),
    ("a,a,c,d,e,f", "r=a:int+1", "C 3"),
    ("a,b,c,d,e,f", "a=b:int+1", "C 4"),
    ("a,b,c,d,e,f", "r=b:int+1;r=c:int", "C 4"),
    ("a,b,c,d,e,f", "r=a+1", "C 5"),
    ("a,b,c,d,e,f", "r=a:float+1", "C 6"),
    ("a,b,c,d,e,f", "r=a:int/b:int", "C 8"),
    ("a,b,c,d,e,f", "r=a:int/b:int@19", "C 1"),
    ("a,b,c,d,e,f", "r=a:dec(10)*b:dec(10)", "C 7"),
    ("a,b,c,d,e,f", "r=a:int+", "C 1"),
    ("a,b,c,d,e,f", "r=(a:int", "C 1"),
    ("a,b,c,d,e,f", "r=a:int)", "C 1"),
    ("a,b,c,d,e,f", "r=a:int b:int", "C 1"),
    ("a,b,c,d,e,f", "", "C 1"),
    ("a,b,c,d,e,f", "r", "C 1"),
    ("a,b,c,d,e,f", "r=1234567890123456789", "C 1"),
    ("a,b,c,d,e,f", "r=((((((a:int))))))", "C 9"),
    ("a,b,c,d,e,f", "r=a:int/b:int@2*c:int", "r=15.00"),
    ("a,b,c,d,e,f", "r=a:int@2", "C 1"),
    ("a,b,c,d,e,f", "r=#7:int", "C 2"),
    ("a,b,c,d,e,f", "r=#0:int", "C 2"),
]


def function_hand_cases():
    """docs/functions.md section 3.2, one line each: the values the table says, and the refusals with their statuses"""
    cells = "10,20,30,40,50,60"
    big = "4611686018427387904,2,3,4,5,6"  # 2^62
    rows = [
        (cells, "r=abs(-a:int)", "r=10"), (cells, "r=abs(a:int)", "r=10"), (cells, "r=abs(0-5)", "r=5"), (cells, "r=abs(-1.50)", "r=1.50"),
        (cells, "r=sign(-a:int)", "r=-1"), (cells, "r=sign(a:int)", "r=1"), (cells, "r=sign(0)", "r=0"), (cells, "r=sign(-0.5)", "r=-1"),
        (cells, "r=greatest(a:int, b:int, c:int)", "r=30"), (cells, "r=least(a:int,b:int)", "r=10"), (cells, "r=greatest(1.5, 2)", "r=2.0"),
        (cells, "r=least(1.50, 1.5)", "r=1.50"), (cells, "r=greatest(-1, -2, -3)", "r=-1"), (cells, "r=least(1.25, 1, 3.125)", "r=1.000"),
        (cells, "r=round(2.5, 0)", "r=2"), (cells, "r=round(3.5, 0)", "r=4"), (cells, "r=round(-2.5, 0)", "r=-2"), (cells, "r=round(-3.5, 0)", "r=-4"),
        (cells, "r=round(2.675, 2)", "r=2.68"), (cells, "r=round(1.005, 2)", "r=1.00"), (cells, "r=round(0.5, 0)", "r=0"), (cells, "r=round(7, 2)", "r=7.00"),
        (cells, "r=round(1/3@6, 2)", "r=0.33"), (cells, "r=round(a:int/c:int@6, 3)", "r=0.333"),
        (cells, "r=floor(2.5, 0)", "r=2"), (cells, "r=floor(-2.5, 0)", "r=-3"), (cells, "r=floor(-0.1, 0)", "r=-1"), (cells, "r=floor(2.999, 2)", "r=2.99"),
        (cells, "r=ceil(2.1, 0)", "r=3"), (cells, "r=ceil(-2.5, 0)", "r=-2"), (cells, "r=ceil(0.1, 0)", "r=1"), (cells, "r=ceil(-0.001, 2)", "r=0.00"),
        (cells, "r=trunc(2.9, 0)", "r=2"), (cells, "r=trunc(-2.9, 0)", "r=-2"), (cells, "r=trunc(2.5, 0)", "r=2"), (cells, "r=trunc(12.3456, 1)", "r=12.3"),
        (cells, "r=greatest(-3, 5)", "r=5"), (cells, "r=least(-3, 5)", "r=-3"), (cells, "r=greatest(3, -5)", "r=3"), (cells, "r=least(3, -5)", "r=-5"),
        (cells, "r=greatest(-0.5, 0)", "r=0.0"), (cells, "r=least(-0.5, 0)", "r=-0.5"), (cells, "r=floor(-5.5, 0)", "r=-6"), (cells, "r=ceil(5.5, 0)", "r=6"),
        (cells, "r=round(1.5, 18)", "r=1.500000000000000000"), (cells, "r=abs(round(-2.5,0))+1", "r=3"), (cells, "r=greatest(abs(-2), round(1.5,0))", "r=2"),
        (cells, "r=round( a:int , 2 )", "r=10.00"), (cells, "r = round ( 1.5 , 0 )".replace("round (", "round("), "r=2"),
        (cells, "x=abs(-1);y=sign(x)", "x=1;y=1"),
        # the refusals
        (cells, "r=nope(1)", "C 13"), (cells, "r=ABS(1)", "C 13"), (cells, "r=abs()", "C 1"), (cells, "r=abs(1,2)", "C 14"), (cells, "r=greatest(1)", "C 14"),
        (cells, "r=greatest(1,2,3,4,5,6,7,8,9)", "C 14"), (cells, "r=round(1)", "C 14"), (cells, "r=round(1,2,3)", "C 14"),
        (cells, "r=round(1,19)", "C 15"), (cells, "r=round(1,-1)", "C 15"), (cells, "r=round(1,a:int)", "C 15"), (cells, "r=round(1,1.5)", "C 15"),
        (cells, "r=floor(1,)", "C 15"), (cells, "r=abs(1", "C 1"), (cells, "r=abs(1))", "C 1"), (cells, "r=1,2", "C 1"), (cells, "r=(1,2)", "C 1"),
        (cells, "r=abs (1)", "C 2"), (cells, "r=abs(,1)", "C 1"), (cells, "r=greatest(1,,2)", "C 1"), (cells, "r=greatest(1,2,)", "C 1"),
        (cells, "r=abs(abs(abs(abs(abs(1)))))", "C 9"), (cells, "r=abs(abs(abs(abs(1))))", "r=1"),
        (cells, "r=abs(a:float)", "C 6"), (cells, "r=abs(nope:int)", "C 2"), (cells, "r=abs(a)", "C 5"),
        # overflow and the cells
        (big, "r=round(a:int*a:int, 18)", "E 12 0 0"), (big, "r=greatest(a:int*a:int, 1.5)", "E 12 0 0"), (big, "r=abs(a:int*a:int)", "r=21267647932558653966460912964485513216"),
        ("x,2,3,4,5,6", "r=abs(a:int)", "E 101 0 0"), ("1.234,2,3,4,5,6", "r=round(a:dec(2), 0)", "E 203 0 0"),
        # coalesce
        (",2,3,4,5,6", "r=coalesce(a:int, 7)", "r=7"), ('"",2,3,4,5,6', "r=coalesce(a:int, 7)", "r=7"), ("10,2,3,4,5,6", "r=coalesce(a:int, 7)", "r=10"),
        (",2,3,4,5,6", "r=coalesce(a:dec(2), 1.5)", "r=1.50"), (",2,3,4,5,6", "r=coalesce(a:dec(2), 2)", "r=2.00"), (",2,3,4,5,6", "r=coalesce(a:int, -3)", "r=-3"),
        (",2,3,4,5,6", "r=coalesce(a:int, 7)+1", "r=8"), (",2,3,4,5,6", "r=round(coalesce(a:dec(3), 0.125), 2)", "r=0.12"),
        ("x,2,3,4,5,6", "r=coalesce(a:int, 7)", "E 101 0 0"), (" ,2,3,4,5,6", "r=coalesce(a:int, 7)", "E 101 0 0"),
        (",2,3,4,5,6", "r=coalesce(a:int, 1.5)", "C 15"), (",2,3,4,5,6", "r=coalesce(a:int*2, 0)", "C 1"), (",2,3,4,5,6", "r=coalesce(a:int)", "C 14"),
        (",2,3,4,5,6", "r=coalesce(a, 7)", "C 5"), (",2,3,4,5,6", "r=coalesce(a:int, b:int)", "C 15"), (",2,3,4,5,6", "r=coalesce(7, a:int)", "C 1"),
        (",2,3,4,5,6", "r=coalesce(a:int, )", "C 15"), (",2,3,4,5,6", "r=1+coalesce(a:int, 7)*2", "r=15"),
        (",2,3,4,5,6", "r=a:int+1", "E 101 0 0"),
    ]
    return [(HEADER, c, d, w) for c, d, w in rows]


def lines_coalesce(rng, n):
    """coalesce(cell, literal) with the cell empty or not, alone and inside an expression whose other leaves are other columns"""
    out = []
    for _ in range(n):
        cols, cells, row = make_row(rng)
        parts = cells.split(",")
        c = rng.randrange(6)
        kind, scale = cols[c]
        col = ("col", c, kind, scale)
        empty = rng.random() < 0.5
        if empty:
            parts[c] = '""' if rng.random() < 0.2 else ""
            row[c] = None
        ls = rng.randint(0, scale) if rng.random() < 0.85 else scale + rng.randint(1, 3)
        lv = rng.randint(-10 ** rng.randint(1, 8), 10 ** rng.randint(1, 8))
        tree = ("coal", col, lv, ls)
        if rng.random() < 0.5:
            o = (c + 1) % 6
            ok, osc = cols[o]
            other = ("col", o, ok, osc)
            tree = rng.choice([("+", tree, other), ("*", other, tree), ("-", tree, other)])
        rendered = render(tree, rng)
        if ls > scale:
            want = "C 15"
        else:
            try:
                static_scale(tree)
                v, sc = ev(tree, row)
                want = f"r0={text(v, sc)}"
            except BadScale:
                want = "C 7"
            except Overflow:
                want = "E 12 0 0"
        out.append((HEADER, ",".join(parts), "r0=" + rendered, want))
    return out


def selftest(rng, n=200000):
    """the rounding family of the reference against Python's `decimal`, which shares no code with it"""
    import decimal
    ctx = decimal.Context(prec=80)
    modes = {"round": decimal.ROUND_HALF_EVEN, "floor": decimal.ROUND_FLOOR, "ceil": decimal.ROUND_CEILING, "trunc": decimal.ROUND_DOWN}
    for i in range(n):
        sc = rng.randint(0, 18)
        v = rng.choice([0, 5, -5, 25, -25, 15, 35, 10 ** 30, rng.randint(-10 ** rng.randint(1, 36), 10 ** rng.randint(1, 36))])
        # ties on purpose: a value ending in 5 followed by zeros at the digit being dropped
        n_to = rng.randint(0, 18)
        if i % 3 == 0 and sc > n_to:
            v = (v // 10 ** (sc - n_to) * 10 + rng.choice([5, -5])) * 10 ** (sc - n_to - 1) if sc - n_to >= 1 else v
        d = decimal.Decimal(v).scaleb(-sc, context=ctx)
        for mode, dm in modes.items():
            q = d.quantize(decimal.Decimal(1).scaleb(-n_to), rounding=dm, context=ctx)
            want = int(q.scaleb(n_to, context=ctx))
            got = round_to(v, sc, n_to, mode)
            assert got == want, (mode, v, sc, n_to, got, want)
    return n


def hand_cases():
    out = []
    for header, defs, want in HAND:
        cells = "10,20,30,40,50,60"
        out.append((header, cells, defs, want))
    out += [
        (HEADER, "10,0,3,4,5,6", "r=a:int/b:int@2", "E 11 0 0"),
        (HEADER, "x,2,3,4,5,6", "r=a:int+1", "E 101 0 0"),
        (HEADER, "1.5,2,3,4,5,6", "r=a:dec(1)+1", "r=2.5"),
        (HEADER, "1.55,2,3,4,5,6", "r=a:dec(1)+1", "E 203 0 0"),
        (HEADER, "9223372036854775808,2,3,4,5,6", "r=a:int+1", "E 102 0 0"),
        (HEADER, "1,2,3,4,5,6", "r=1+2*3", "r=7"),
        (HEADER, "1,2,3,4,5,6", "r=(1+2)*3", "r=9"),
        (HEADER, "1,2,3,4,5,6", "r=10-2-3", "r=5"),
        (HEADER, "1,2,3,4,5,6", "r=-a:int*-b:int", "r=2"),
        (HEADER, "1,2,3,4,5,6", "r=-(a:int+b:int)", "r=-3"),
        (HEADER, "1,2,3,4,5,6", "r=7/2@0", "r=4"),
        (HEADER, "1,2,3,4,5,6", "r=5/2@0", "r=2"),
        (HEADER, "1,2,3,4,5,6", "r=1/3@4", "r=0.3333"),
        (HEADER, "1,2,3,4,5,6", "r=1.25*2", "r=2.50"),
        (HEADER, "1,2,3,4,5,6", "x=a:int+1;y=b:int*2", "x=2;y=4"),
        (HEADER, '"1",2,3,4,5,6', "r=a:int+1", "r=2"),
        ("a b,b,c,d,e,f", "7,2,3,4,5,6", "r='a b':int+1", "r=8"),
        ("a b,b,c,d,e,f", "7,2,3,4,5,6", "'out one'=\\a\\ b:int+1", "out one=8"),
        ("a,b,c,d,e,f", "7,2,3,4,5,6", "r=#1:int+#2:int", "r=9"),
    ]
    out += function_hand_cases()
    return out


# ---- the probe -------------------------------------------------------------------------------------------------------------------------

def closure(module):
    seen, todo = [], [module]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.append(m)
        src = (ROOT / "tools" / "table" / (m + ".cho")).read_text()
        todo += [i for i in re.findall(r"^import\s+([\w.]+)\s*;", src, re.M) if not i.startswith("std.")]
    return seen


def build(work, mutant=None):
    proj = pathlib.Path(work)
    (proj / "src").mkdir()
    for m in closure("cexpr") + ["cmpv"]:
        p = ROOT / "tools" / "table" / (m + ".cho")
        t = p.read_text()
        if mutant and mutant[0] == m:
            t = t.replace(mutant[1], mutant[2])
        (proj / "src" / (m + ".cho")).write_text(t)
    (proj / "src/main.cho").write_text((ROOT / "tests/conformance/cexpr_probe/main.cho").read_text().replace("@MODULE@", "cexpr"))
    (proj / "cancho.toml").write_text('[package]\nname = "cexprprobe"\ncancho = "119bcfe9b985ef5ebd9dafc3ff3b2bbd05f22653"\n\n'
                                      '[[bin]]\nname = "probe"\nsources = ["src"]\nstd = true\nout = "build/probe"\n')
    r = subprocess.run(["cancho", "build", "--ignore-compiler-rev"], cwd=proj, capture_output=True, text=True)
    return (proj / "build/probe", "") if r.returncode == 0 else (None, r.stdout + r.stderr)


def run(probe, cs):
    got = []
    for i in range(0, len(cs), 100):
        chunk = cs[i:i + 100]
        argv = [str(probe)] + [x for c in chunk for x in c[:3]]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 0:
            return None, f"rc {r.returncode}: {r.stderr[:200]}"
        got += r.stdout.split("\n")[:len(chunk)]
    return got, ""


def same(want, got):
    if want.startswith("D "):
        _, c, dd = want.split()
        m = re.fullmatch(r"E (\d{4,}) %s %s" % (c, dd), got)
        return m is not None and int(m.group(1)) >= 1015  # 1000 and the reader's status: 14 and the reason, so never less
    if want.startswith("C "):
        return got.startswith(want)  # the status; the offset is the tool's
    return got == want


def check(cs, probe):
    got, err = run(probe, cs)
    if got is None:
        return [("probe failed", err)]
    return [(c, g) for c, g in zip(cs, got) if not same(c[3], g)]


MUTANTS = [
    ("a product binds as a sum", "    if code == 3 || code == 4 {\n        return 2;", "    if code == 3 || code == 4 {\n        return 1;"),
    ("a negation binds loosely", "    if code == 5 {\n        return 3;", "    if code == 5 {\n        return 0;"),
    ("a right-associative subtraction", "    while status == 0 && work[64] > 0 && prec(work[work[64] - 1]) >= prec(code) {", "    while status == 0 && work[64] > 0 && prec(work[work[64] - 1]) > prec(code) {"),
    ("the scale of a division is ignored", "        return clex.emit(prog, at, op_div(), n, 0, 0);", "        return clex.emit(prog, at, op_div(), 0, 0, 0);"),
    ("a division without a scale is accepted", "        if n < 0 {\n            return -1;\n        }", "        if n < -5 {\n            return -1;\n        }"),
    ("a decimal read at scale 0", "            return (from + 1 + k + 1, n, 1, 0);", "            return (from + 1 + k + 1, 0, 1, 0);", "clit"),
    ("a float is read as an int", "        return (from + 6, 0, 0, 6);", "        return (from + 6, 0, 0, 0);", "clit"),
    ("no type is accepted", "        return (from, 0, 0, 5);", "        return (from, 0, 0, 0);", "clit"),
    ("a position is one off", "                return (n - 1, 0);", "                return (n, 0);", "clex"),
    ("an ambiguous name is accepted", "    if count > 1 {\n        return (0, 3);", "    if count > 100 {\n        return (0, 3);", "clex"),
    ("a duplicate of a column is accepted", "            if cs == 0 {\n                status = 4;", "            if cs == 100 {\n                status = 4;"),
    ("a duplicate of a definition is accepted", "            if same {\n                status = 4;", "            if same && false {\n                status = 4;"),
    ("a product past scale 18 is accepted", "                if scale > 18 {\n                    return (0, 0, s_scale());", "                if scale > 99 {\n                    return (0, 0, s_scale());"),
    ("a zero divisor is a value", "                if st == 1 {\n                    return (11, 0, 0, 0, 0, 0);\n                }", "                if st == 11 {\n                    return (11, 0, 0, 0, 0, 0);\n                }"),
    ("an overflow is a value", "            if !ok {\n                return (12, 0, 0, 0, 0, 0);", "            if false {\n                return (12, 0, 0, 0, 0, 0);"),
    ("the literal's scale is dropped", "            cmpv.set_int(w, sp, prog[at + 1], prog[at + 2]);", "            cmpv.set_int(w, sp, prog[at + 1], 0);"),
    ("negation does nothing", "            cmpv.negate(w, sp - 1);", "            sp = sp;"),
    ("parentheses are unlimited", "                if work[65] > 4 || work[64] > 28 {", "                if work[65] > 400 || work[64] > 28 {"),
    ("chain: a reference is never found", "                    if rs == 2 {\n                        borrow cb as &cr in {", "                    if rs == 99 {\n                        borrow cb as &cr in {"),
    ("chain: one instruction too few copied", "                        while q < n_ints {", "                        while q < n_ints - 4 {"),
    ("chain: one instruction too many copied", "let n_ints = prog[1 + 5 * dk + 1] - from_at;", "let n_ints = prog[1 + 5 * dk + 1] - from_at + 4;"),
    ("chain: the loop's size check is off", "        } else if cur - first > 4 * 128 {\n            status = 9;", "        } else if cur - first > 4 * 12800 {\n            status = 9;"),
    ("chain: always the first definition", "let from_at = prog[1 + 5 * dk];\n                        let n_ints", "let from_at = prog[1 + 5 * 0];\n                        let n_ints"),
    ("chain: the operand is not closed", "                        cur = cur + n_ints;\n                        i = e2;\n                        expect = false;", "                        cur = cur + n_ints;\n                        i = e2;"),
    ("an empty expression is accepted", "    if status == 0 && (expect || work[65] != 0) {", "    if status == 0 && (work[65] != 0) {"),
    # the numeric functions (docs/functions.md section 3.2)
    ("fn: abs keeps the sign", "        cmpv.abs(w, base);\n        return 0;", "        return 0;"),
    ("fn: the sign of a negative is 1", "        } else if cmpv.negative(w, base) {\n            v = -1;", "        } else if cmpv.negative(w, base) {\n            v = 1;"),
    ("fn: greatest and least are swapped", "            if id == 3 && c > 0 || id == 4 && c < 0 {", "            if id == 3 && c < 0 || id == 4 && c > 0 {"),
    ("fn: the operands are not brought to one scale", "            if !cmpv.rescale(w, base + k, widest) {\n                return 12;\n            }", "            if false {\n                return 12;\n            }"),
    ("fn: floor steps the wrong way", "        if id == 6 {\n            down = c > 0;", "        if id == 6 {\n            down = c < 0;"),
    ("fn: ceil steps the wrong way", "        } else if id == 7 {\n            up = c < 0;", "        } else if id == 7 {\n            up = c > 0;"),
    ("fn: trunc treats a negative as a positive", "        } else if cmpv.negative(w, base) {\n            up = c < 0;", "        } else if false {\n            up = c < 0;"),
    ("fn: floor, ceil and trunc never step", "        if down || up {", "        if false {"),
    ("fn: an overflow of the rounding is not reported", "    if cmpv.div(w, 11, base, 10, extra) != 0 {\n        return 12;", "    if cmpv.div(w, 11, base, 10, extra) == 99 {\n        return 12;"),
    ("fn: the scale of a rounding call is the operand's", "            } else if id >= 5 {\n                fscale = prog[at + 3];", "            } else if id >= 99 {\n                fscale = prog[at + 3];"),
    ("fn: the scale of sign is the operand's", "            if id == 2 {\n                fscale = 0;", "            if id == 99 {\n                fscale = 0;"),
    ("fn: greatest takes the scale of its first operand", "                    if work[72 + sp - n + k] > fscale {", "                    if work[72 + sp - n + k] > 99 {"),
    ("fn: an unknown function is not refused", "                    if fid == 0 {\n                        status = 13;\n                    } else if fid >= 11 && fid <= 18 {", "                    if fid == 99 {\n                        status = 13;\n                    } else if fid >= 11 && fid <= 18 {"),
    ("fn: the arity is not checked", "                            if nargs < arity_min(fid) || nargs > arity_max(fid) {", "                            if false {"),
    ("fn: the scale literal may be 19", "    if digits == 0 || n > 18 {\n        return (p, 0, 15);", "    if digits == 0 || n > 19 {\n        return (p, 0, 15);", "clit"),
    ("fn: a call's parenthesis is not a level", "                        if work[64] > 28 || work[65] > 4 {\n                            status = 9;\n                        } else {\n                            let top = work[64];\n                            work[top] = 7;\n                            work[32 + top] = fid * 16;\n                            work[160 + top] = cur;", "                        if work[64] > 28 || work[65] > 400 {\n                            status = 9;\n                        } else {\n                            let top = work[64];\n                            work[top] = 7;\n                            work[32 + top] = fid * 16;\n                            work[160 + top] = cur;"),
    ("fn: the scale of a rounding call is taken as an operand", "cur = clex.emit(prog, cur, op_fn(), fid, 1, work[224 + top]);", "cur = clex.emit(prog, cur, op_fn(), fid, 2, work[224 + top]);"),
    ("fn: coalesce takes the default of a cell that is not empty", "            if kind >= 2 && cells[3 * prog[at + 1]] == cells[3 * prog[at + 1] + 1] {", "            if kind >= 2 {"),
    ("fn: the default is not brought to the column's scale", "                cmpv.mul_pow10(w, sp, prog[at + 2] - prog[at + 6]);", "                cmpv.mul_pow10(w, sp, 0);"),
    ("fn: the default is also an operand", "            if kind >= 2 {\n                at = at + 4;\n            }\n            sp = sp + 1;", "            sp = sp + 1;"),
    ("fn: a default with more digits than the column is accepted", "                            if ls != 0 || sc > prog[start + 2] {", "                            if ls != 0 {"),
    ("fn: coalesce takes an expression", "                        if commas != 0 || cur - start != 4 || prog[start] != op_cell() || prog[start + 3] >= 2 {", "                        if commas != 0 {"),
    ("fn: a comma outside a call is accepted", "                if status == 0 && (work[64] == 0 || work[work[64] - 1] != 7 && work[work[64] - 1] != 8) {\n                    status = 1;", "                if status == 0 && (work[64] == 0) {\n                    status = 1;"),
    ("fn: the shape does not skip a default", "            if op == op_cell() && prog[at + 3] >= 2 {\n                // a coalesce: the constant that follows is the default, not an operand\n                at = at + 4;\n            }", ""),
    # conditions (docs/functions.md section 3.4): G-IF-MUT
    ("if: a false condition does not skip the first arm", "        } else if op == cif.op_jf() {\n            if !verdict {", "        } else if op == cif.op_jf() {\n            if false {"),
    ("if: a true verdict does not skip at a jt", "        } else if op == cif.op_jt() {\n            if verdict {", "        } else if op == cif.op_jt() {\n            if !verdict {"),
    ("if: the jump over the second arm does not skip", "        } else if op == cif.op_jmp() {\n            at = at + 4 * prog[at + 1];", "        } else if op == cif.op_jmp() {\n            at = at + 0;"),
    ("if: the result is not brought to the scale of the arms", "            if prog[at + 1] >= 0 && !cmpv.rescale(w, sp - 1, prog[at + 1]) {\n                return (12, 0, 0, 0, 0, 0);", "            if false {\n                return (12, 0, 0, 0, 0, 0);"),
    ("if: the scale of the result is the first arm's", "    if sb > sa {\n        target = sb;\n    }", ""),
    ("if: a comparison consumes one operand", "            sp = sp - 2;\n            verdict = cif.compare", "            sp = sp - 1;\n            verdict = cif.compare"),
    ("if: the shape does not return to the first arm's depth", "            // the arm that follows pushes its own value where this one's was\n            sp = sp - 1;", "            sp = sp;"),
    ("if: the shape ignores the scale of an arm", "            work[72 + sp - 1] = prog[at + 1];\n        } else if op == cif.op_jmp() {", "        } else if op == cif.op_jmp() {"),
    ("if: an or is an and", "    if is_or {\n        at = clex.emit(prog, at, cif.op_jt(), -1, 0, 0);", "    if false {\n        at = clex.emit(prog, at, cif.op_jt(), -1, 0, 0);"),
    ("if: the jumps of an or land one instruction late", "            if prog[q] == cif.op_jt() && prog[q + 1] == -1 {\n                prog[q + 1] = cif.skip(q, at);", "            if prog[q] == cif.op_jt() && prog[q + 1] == -1 {\n                prog[q + 1] = cif.skip(q, at + 4);"),
    ("if: the jumps of the condition land one instruction late", "            if prog[q] == cif.op_jf() && prog[q + 1] == -1 {\n                prog[q + 1] = cif.skip(q, at);", "            if prog[q] == cif.op_jf() && prog[q + 1] == -1 {\n                prog[q + 1] = cif.skip(q, at + 4);"),
    ("if: a skip is counted from the jump, not after it", "    return (to - (from + 4)) / 4;", "    return (to - from) / 4;", "cif"),
    ("if: the mix of and and or is accepted", "    if conn != 0 && conn != want {\n        return (cur, 19);", "    if false {\n        return (cur, 19);"),
    ("if: a fifth comparison is accepted", "    if ncmp + 1 >= cif.max_comparisons() {", "    if ncmp + 1 > cif.max_comparisons() {"),
    ("if: the limit of comparisons is five", "pub fn max_comparisons() -> [] int {\n    return 4;", "pub fn max_comparisons() -> [] int {\n    return 5;", "cif"),
    ("if: a condition with no comparison is accepted", "        if pending == 0 {\n            return (cur, 1);\n        }\n        var at = cur;", "        var at = cur;"),
    ("if: a connective with no comparison before it is accepted", "    if pending == 0 || st / 256 != 0 {\n        return (cur, 1);", "    if st / 256 != 0 {\n        return (cur, 1);"),
    ("if: two arguments are accepted", "    if work[32 + top] / 256 != 2 {\n        return (cur, 14);", "    if work[32 + top] / 256 < 1 {\n        return (cur, 14);"),
    ("if: a third comma is accepted", "        work[32 + top] = st + 256;\n        return (at, 0);\n    }\n    return (cur, 14);", "        work[32 + top] = st + 256;\n        return (at, 0);\n    }\n    return (cur, 0);"),
    ("if: five levels of parentheses are accepted for an if", "if work[64] > 28 || work[65] > 4 {\n                            status = 9;\n                        } else {\n                            let top = work[64];\n                            work[top] = 8;", "if work[64] > 28 || work[65] > 5 {\n                            status = 9;\n                        } else {\n                            let top = work[64];\n                            work[top] = 8;"),
    ("if: the parser reads <= as <", "                    if c == '<' {\n                        code = 4;", "                    if c == '<' {\n                        code = 3;"),
    ("if: the parser reads >= as >", "                    } else if c == '>' {\n                        code = 6;", "                    } else if c == '>' {\n                        code = 5;"),
    ("if: the parser reads != as =", "    if c == '!' {\n                    code = 2;", "    if c == '!' {\n                    code = 1;"),
    ("if: < is <=", "    if op == 3 {\n        return c < 0;", "    if op == 3 {\n        return c <= 0;", "cif"),
    ("if: <= is <", "    if op == 4 {\n        return c <= 0;", "    if op == 4 {\n        return c < 0;", "cif"),
    ("if: > is >=", "    if op == 5 {\n        return c > 0;", "    if op == 5 {\n        return c >= 0;", "cif"),
    ("if: >= is >", "    return c >= 0;\n}\n\n// The skip count", "    return c > 0;\n}\n\n// The skip count", "cif"),
    ("if: = is !=", "    if op == 1 {\n        return c == 0;", "    if op == 1 {\n        return c != 0;", "cif"),
    ("if: the left operand is not brought to the scale of the right", "        cmpv.mul_pow10(w, x, sy - sx);\n        cmpv.set_scale(w, x, sy);", "        cmpv.set_scale(w, x, sy);", "cif"),
    ("if: the right operand is not brought to the scale of the left", "        cmpv.mul_pow10(w, y, sx - sy);\n        cmpv.set_scale(w, y, sx);", "        cmpv.set_scale(w, y, sx);", "cif"),
    ("if: an if is not a marker the operators stop at", "                    while work[64] > 0 && work[work[64] - 1] != 6 && work[work[64] - 1] != 7 && work[work[64] - 1] != 8 && status == 0 {", "                    while work[64] > 0 && work[work[64] - 1] != 6 && work[work[64] - 1] != 7 && status == 0 {"),
    # dates (docs/functions.md section 3.5): G-DT-MUT
    ("dt: the calendar starts a day late", "dtparse.civil_from_shifted(x + 719468)", "dtparse.civil_from_shifted(x + 719469)", "cdate"),
    ("dt: the week starts on a different day", "        var m = (x + 3) % 7;", "        var m = (x + 4) % 7;", "cdate"),
    ("dt: the weekday of Monday is 0", "        v = m + 1;", "        v = m;", "cdate"),
    ("dt: a date before 1970 has a weekday of a negative modulus", "        if m < 0 {\n            m = m + 7;\n        }\n", "", "cdate"),
    ("dt: days_between has the opposite sign", "        v = y - x;", "        v = x - y;", "cdate"),
    ("dt: the second operand is flagged as the first", "operand_days(a, 64, prog[at + 3], record, cells)", "operand_days(a, 32, prog[at + 3], record, cells)", "cdate"),
    ("dt: a literal is read as a column", "    if prog_a / flag % 2 == 1 {", "    if prog_a / flag % 2 == 7 {", "cdate"),
    ("dt: year is the month", "        if id == 21 {\n            v = yy;", "        if id == 21 {\n            v = mm;", "cdate"),
    ("dt: a literal that is not a date is accepted", "    if st != 0 {\n        return (from, 0, 15);\n    }\n    return (from + 12, v, 0);", "    return (from + 12, v, 0);", "cdate"),
    ("dt: any type is a date", "    if from + 5 <= len(t) && bytes.equal(t[from..from + 5], \":date\") {", "    if from + 5 <= len(t) {", "cdate"),
    ("dt: the status of a cell has no reason", "        return (0, s_cell_date() + st, v);", "        return (0, s_cell_date(), v);", "cdate"),
    ("dt: the instruction does not push", "            let (dst, dcol) = cdate.eval(prog, at, record, cells, w, sp);\n            if dst != 0 {\n                return (dst, dcol, 0, 0, 0, 0);\n            }\n            sp = sp + 1;", "            let (dst, dcol) = cdate.eval(prog, at, record, cells, w, sp);\n            if dst != 0 {\n                return (dst, dcol, 0, 0, 0, 0);\n            }"),
    ("dt: the shape gives the leaf a scale", "        } else if op == cdate.op_dt() {\n            work[72 + sp] = 0;", "        } else if op == cdate.op_dt() {\n            work[72 + sp] = 1;"),
    ("dt: a second argument is accepted for a one-argument function", "        if int_of(t[i]) == ')' || k >= want {", "        if int_of(t[i]) == ')' || k > want {", "cdatep"),
    ("dt: one argument is accepted for days_between", "            if k < want {\n                return (i, cur, 14);\n            }\n            var op = cdate.op_dt();", "            var op = cdate.op_dt();", "cdatep"),
    ("dt: a number is a column name", "            return (i, cur, 16);\n        } else {\n            var cb = buffer.empty(heap, 32);\n            let (b2, e2, q2, s2) = clex.word_at(heap, cb, t, i);\n            cb = b2;\n            var status = 0;\n            if s2 != 0 {\n                status = 1;\n            } else {\n                var col = 0;", "            return (i, cur, 2);\n        } else {\n            var cb = buffer.empty(heap, 32);\n            let (b2, e2, q2, s2) = clex.word_at(heap, cb, t, i);\n            cb = b2;\n            var status = 0;\n            if s2 != 0 {\n                status = 1;\n            } else {\n                var col = 0;", "cdatep"),
    ("dt: an earlier definition is not a type mismatch", "                    status = 2;\n                    if dk >= 0 {\n                        status = 16;", "                    status = 2;\n                    if dk >= 0 {\n                        status = 2;", "cdatep"),
    ("dt: the second literal is flagged as the first", "            a = a + 32 + 32 * k;", "            a = a + 32;", "cdatep"),
    # a comparison of texts (docs/functions.md section 3.4, slice K7b1): G-IFT-MUT
    ("ift: a text that is the start of the other is equal to it", "        if i >= xl {\n            return -1;\n        }", "        if i >= xl {\n            return 0;\n        }", "ctext"),
    ("ift: the shorter text is the greater", "        if i >= xl {\n            return -1;\n        }\n        if j >= yl {\n            return 1;\n        }", "        if i >= xl {\n            return 1;\n        }\n        if j >= yl {\n            return -1;\n        }", "ctext"),
    ("ift: the first byte that differs decides the other way", "            if bx < by {\n                return -1;\n            }\n            return 1;", "            if bx < by {\n                return 1;\n            }\n            return -1;", "ctext"),
    ("ift: two equal texts are the greater", "        if i >= xl && j >= yl {\n            return 0;", "        if i >= xl && j >= yl {\n            return 1;", "ctext"),
    ("ift: the doubled quotes of the left are not undone", "if xd == 1 && bx == 34", "if xd == 7 && bx == 34", "ctext"),
    ("ift: the doubled quotes of the right are not undone", "if yd == 1 && by == 34", "if yd == 7 && by == 34", "ctext"),
    ("ift: != is =", "        op = 2;\n        width = 2;", "        op = 1;\n        width = 2;", "ctcmp"),
    ("ift: <= is <", "            op = 4;\n            width = 2;", "            op = 3;\n            width = 2;", "ctcmp"),
    ("ift: >= is >", "            op = 6;\n            width = 2;", "            op = 5;\n            width = 2;", "ctcmp"),
    ("ift: a right operand that is not a text is not refused as one", "    var no = 16;", "    var no = 100;", "ctcmp"),
    ("ift: a typed column starts a comparison of texts", "} else if e2 < len(t) && (int_of(t[e2]) == ':' || int_of(t[e2]) == '(') {\n        status = no;", "} else if e2 < len(t) && int_of(t[e2]) == '(' {\n        status = no;", "ctcmp"),
    ("ift: both operands are loaded into the right register", "    var at = clex.emit(prog, cur, ctext.op_tld(), 2 * k1, a1, b1);", "    var at = clex.emit(prog, cur, ctext.op_tld(), 1 + 2 * k1, a1, b1);", "ctcmp"),
    ("ift: a literal is read as a column", "            if prog[at + 1] / 2 == 1 {", "            if prog[at + 1] / 2 == 7 {"),
    ("ift: a cell has no doubled quotes", "                dbl = cells[3 * prog[at + 2] + 2];", "                dbl = 0;"),
    ("ift: a cell is compared with the text of the definitions", "                sign = ctext.compare(record, lf, lt, ld, t, rf, rt, rd);", "                sign = ctext.compare(record, lf, lt, ld, record, rf, rt, rd);"),
    ("ift: a text comparison is followed by the comparison of numbers", "    if pending != 7 {\n        at = clex.emit(prog, at, cif.op_cmp(), pending, 0, 0);\n    }\n    if is_or {", "    if pending != 99 {\n        at = clex.emit(prog, at, cif.op_cmp(), pending, 0, 0);\n    }\n    if is_or {"),
    ("ift: the last text comparison is followed by the comparison of numbers", "        if pending != 7 {\n            at = clex.emit(prog, at, cif.op_cmp(), pending, 0, 0);\n        }\n        at = clex.emit(prog, at, cif.op_jf(), -1, 0, 0);", "        if pending != 99 {\n            at = clex.emit(prog, at, cif.op_cmp(), pending, 0, 0);\n        }\n        at = clex.emit(prog, at, cif.op_jf(), -1, 0, 0);"),
    ("ift: a text comparison is read in an arm", "work[32 + work[64] - 1] / 256 == 0 && work[32 + work[64] - 1] % 8 == 0 && cur - first <= 4 * 128", "work[32 + work[64] - 1] / 256 >= 0 && work[32 + work[64] - 1] % 8 == 0 && cur - first <= 4 * 128"),
    ("ift: a string literal in a number is not a type mismatch", "            if c == 34 {\n                // a string literal in a number: no conversion either way\n                status = 16;", "            if c == 34 {\n                // a string literal in a number: no conversion either way\n                status = 2;"),
    ("ift: the shape counts the loads as operands", "        } else if op == ctext.op_tld() || op == ctext.op_tcmp() {\n            sp = sp;", "        } else if op == ctext.op_tld() || op == ctext.op_tcmp() {\n            sp = sp + 1;"),
    ("ift: a text operand is compared as the left whatever its register", "            if prog[at + 1] % 2 == 0 {\n                lsrc = src;", "            if prog[at + 1] % 2 == 7 {\n                lsrc = src;"),
    # a text `if` (docs/functions.md section 3.4, slice K7b2): G-IFT2-MUT
    ("ift2: a literal leaf is read as a column", "            if prog[at + 1] == 1 {\n                selsrc = 1;", "            if prog[at + 1] == 7 {\n                selsrc = 1;"),
    ("ift2: a cell leaf has no doubled quotes", "                seldbl = cells[3 * prog[at + 2] + 2];", "                seldbl = 0;"),
    ("ift2: a literal leaf has no doubled quotes", "                selto = prog[at + 3];\n                seldbl = 1;", "                selto = prog[at + 3];\n                seldbl = 0;"),
    ("ift2: the leaf selected is not the value", "        return (0, 0, selsrc, selfrom, selto, seldbl);", "        return (0, 0, 0, 0, 0, 0);"),
    ("ift2: a text arm and a number arm are accepted", "    if sa < 0 != sb < 0 {", "    if false {"),
    ("ift2: a text is an operand of an operator", "            if right < 0 || left < 0 {", "            if false {"),
    ("ift2: a text is negated", "        } else if op == op_neg() {\n            if work[72 + sp - 1] < 0 {", "        } else if op == op_neg() {\n            if false {"),
    ("ift2: a text is compared as a number", "            if work[72 + sp - 1] < 0 || work[72 + sp - 2] < 0 {", "            if false {"),
    ("ift2: a text is the argument of a function", "                if work[72 + sp - n + kk] < 0 {", "                if false {"),
    ("ift2: a text `if` is copied into a text definition", "                        } else if prog[1 + 5 * dk + 2] != -1 {", "                        } else if prog[1 + 5 * dk + 2] >= 0 {"),
    ("ift2: the arms of a text `if` are brought to a scale", "            if prog[at + 1] >= 0 && !cmpv.rescale(w, sp - 1, prog[at + 1]) {", "            if !cmpv.rescale(w, sp - 1, prog[at + 1]) {"),
    ("ift2: the shape gives a leaf a scale", "        if op == ctext.op_tsel() {\n            work[72 + sp] = -1;", "        if op == ctext.op_tsel() {\n            work[72 + sp] = 0;"),
    # the exact core: what the functions added to it
    ("cmpv: abs keeps the sign", "pub fn abs[&w](w: &!w [int], s: int) -> [] int {\n    w[s * 12] = 0;", "pub fn abs[&w](w: &!w [int], s: int) -> [] int {\n    w[s * 12] = w[s * 12];", "cmpv"),
    ("cmpv: a comparison of a negative and a positive is by magnitude", "    if na && !nb {\n        return -1;\n    }\n    if nb && !na {\n        return 1;\n    }\n    let c = mag_cmp(w, a, b);", "    let c = mag_cmp(w, a, b);", "cmpv"),
    ("cmpv: two negatives compare as two positives", "    if na {\n        return -c;\n    }\n    return c;", "    return c;", "cmpv"),
    ("cmpv: a rescale does not set the scale", "    w[s * 12 + 1] = to;\n    return fits(w, s);", "    return fits(w, s);", "cmpv"),
    ("cmpv: a rescale is never too wide", "    return fits(w, s);\n}\n\n// A slot from an `int`", "    return true;\n}\n\n// A slot from an `int`", "cmpv"),
]


def fuzz_cases(rng, n):
    """G-NOPANIC: damaged and random definitions; whatever the answer, it is an answer (no trap), a listed status, and an offset inside the text."""
    base = [d for _, _, d, _ in lines_of(rng, 200)]
    out = []
    alphabet = "abcdef#:()+-*/@;=' \\.0123456789\"intdec,"
    for _ in range(n):
        d = rng.choice(base)
        k = rng.random()
        if k < 0.4 and d:
            i = rng.randrange(len(d))
            d = d[:i] + d[i + rng.randint(1, 4):]
        elif k < 0.7:
            i = rng.randrange(len(d) + 1)
            d = d[:i] + "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 4))) + d[i:]
        elif k < 0.85:
            d = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        else:
            d = d * rng.randint(2, 40)
        out.append((HEADER, "1,2.5,3,4,5,6", d, ""))
    return out


def fuzz_ok(cs, probe):
    got, err = run(probe, cs)
    if got is None:
        return [("probe failed", err)]
    shape = re.compile(r"^(C (1\d|20|[1-9]) (\d+)|E (\d{4,}|1\d\d|2\d\d|11|12) \d+ \d+|[^=]*=.*)$")
    bad = []
    for c, g in zip(cs, got):
        m = shape.match(g)
        if not m or (g.startswith("C ") and int(g.split()[2]) > len(c[2])):
            bad.append((c, g))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="the reference's rounding against Python's decimal module (no compiler)")
    a = ap.parse_args()
    if a.selftest:
        print(f"{selftest(random.Random(a.seed))} rounding cases agree with decimal")
        sys.exit(0)
    problems = mutlib.check([(m[0], (m[3] if len(m) > 3 else "cexpr") + ".cho", m[1], m[2]) for m in MUTANTS])
    if problems or a.check:
        print("\n".join(problems) or f"{len(MUTANTS)} mutants: every pattern occurs exactly once")
        sys.exit(1 if problems else 0)
    rng = random.Random(a.seed)
    cs = hand_cases() + if_hand_cases() + dt_hand_cases() + lines_dt(rng, 1200 if not a.mutants else 600) + lines_if(rng, 1200 if not a.mutants else 600) + lines_tif(rng, 800 if not a.mutants else 400) + lines_of(rng, a.cases if not a.mutants else 1500) + lines_coalesce(rng, 600 if not a.mutants else 300) + chain_hand_cases() + chain_lines(rng, 1500 if not a.mutants else 800)
    if not a.mutants:
        with tempfile.TemporaryDirectory() as w:
            probe, err = build(w)
            if not probe:
                sys.exit("build failed:\n" + err)
            bad = check(cs, probe)
            fz = fuzz_cases(rng, 3000)
            fbad = fuzz_ok(fz, probe)
        print(f"{len(fz)} fuzz cases, {len(fbad)} traps or malformed answers")
        for b in fbad[:5]:
            print(" ", b)
        bad += fbad
        print(f"{len(cs)} cases, {len(bad)} differences")
        for b in bad[:10]:
            print(" ", b)
        sys.exit(1 if bad else 0)
    survivors = 0
    for name, old, new, *rest in MUTANTS:
        with tempfile.TemporaryDirectory() as w:
            probe, err = build(w, (rest[0] if rest else "cexpr", old, new))
            if not probe:
                print(f"SKIP (does not build) {name}: {err[:200]}")
                survivors += 1
                continue
            bad = check(cs, probe)
        print(("killed   " if bad else "SURVIVED ") + name)
        survivors += 0 if bad else 1
    print(f"{len(MUTANTS) - survivors}/{len(MUTANTS)} killed")
    sys.exit(1 if survivors else 0)


if __name__ == "__main__":
    main()
