#!/usr/bin/env python3
"""The reference of the text functions of `--compute` (docs/functions.md section 3.3): Python `bytes` methods, no code shared with the tool.

    python3 scripts/ctext_ref.py --selftest     # the hand cases below against the reference

`tests/conformance/test_compute_text.py` runs the built binary on these cases and on random ones and compares the answer with this module's.
A tree is ("col", index) | ("lit", bytes) | ("fn", name, [arguments], extra); `extra` is (start, count) for `substr`.
A row is a list of the cells' bytes. A refusal of a row is an exception that names its rule.
"""
import random
import sys

NAMES = ["a", "b", "c d", "e"]          # the header of the generated files
FUNCTIONS = {"lower": (1, 1), "upper": (1, 1), "trim": (1, 1), "ltrim": (1, 1), "rtrim": (1, 1), "substr": (3, 3), "concat": (2, 8), "replace": (3, 3)}


class RowRefusal(Exception):
    rule = ""


class Boundary(RowRefusal):
    rule = "compute.not-utf8-boundary"


class TooLong(RowRefusal):
    rule = "compute.result-too-long"


def continuation(byte):
    return 0x80 <= byte <= 0xBF


def substr(b, start, count):
    """bytes from `start` (1 is the first), at most `count`, clipped; a cut at a continuation byte is refused"""
    i = start - 1
    if i >= len(b):
        return b""
    e = min(len(b), i + count)
    for p in (i, e):
        if 0 < p < len(b) and continuation(b[p]):
            raise Boundary()
    return b[i:e]


def ev(node, row):
    k = node[0]
    if k == "col":
        return row[node[1]]
    if k == "lit":
        return node[1]
    _, name, args, extra = node
    v = [ev(a, row) for a in args]
    if name == "lower":
        return v[0].lower()          # bytes.lower changes the ASCII letters only
    if name == "upper":
        return v[0].upper()
    if name == "trim":
        return v[0].strip(b" \t")
    if name == "ltrim":
        return v[0].lstrip(b" \t")
    if name == "rtrim":
        return v[0].rstrip(b" \t")
    if name == "substr":
        return substr(v[0], extra[0], extra[1])
    if name == "concat":
        return b"".join(v)
    if name == "replace":
        return v[0].replace(v[1], v[2])
    raise AssertionError(name)


def quoted(b):
    """the bytes the stage appends for a text result: quotes doubled (the cell is flagged as read from quotes)"""
    return b.replace(b'"', b'""')


# ---- the written form ------------------------------------------------------------------------------------------------------------------

def literal(b):
    return '"' + b.decode("utf-8").replace('"', '""') + '"'


def spaces(rng):
    return rng.choice(["", "", " ", "  "])


def column(i, rng):
    n = NAMES[i]
    r = rng.random()
    if r < 0.15:
        return f"#{i + 1}"
    if " " in n or r < 0.3:
        return "'" + n + "'"
    return n


def render(node, rng):
    k = node[0]
    if k == "col":
        return column(node[1], rng)
    if k == "lit":
        return literal(node[1])
    _, name, args, extra = node
    parts = [render(a, rng) for a in args]
    if name == "substr":
        parts += [str(extra[0]), str(extra[1])]
    return f"{name}(" + spaces(rng) + (spaces(rng) + "," + spaces(rng)).join(parts) + spaces(rng) + ")"


# ---- random cases ----------------------------------------------------------------------------------------------------------------------

PIECES = [b"", b"a", b"B", b"Hello", b"WORLD", b"  x  ", b"\t", b" \tmixed\t ", b",", b'"', b'say "hi"', b"a,b", b"line\nbreak", b"cr\rhere", b"\xc3\xa9", b"\xc3\x89t\xc3\xa9",
          b"\xe6\x97\xa5\xe6\x9c\xac", b"\xf0\x9f\x98\x80", b"abc123", b"ABC xyz", b"\x80", b"a\xc3", b"\xff\xfe"]


def text(rng):
    return b"".join(rng.choice(PIECES) for _ in range(rng.randint(0, 3)))


def lit_text(rng):
    return rng.choice([b"", b"x", b" ", b"-", b"AB", b"ab", b'"', b"a,b", b"\xc3\xa9", b"abc", b"aa", b"a"])


def gen(rng, depth):
    if depth == 0 or rng.random() < 0.3:
        if rng.random() < 0.25:
            return ("lit", lit_text(rng))
        return ("col", rng.randrange(len(NAMES)))
    name = rng.choice(list(FUNCTIONS))
    if name in ("lower", "upper", "trim", "ltrim", "rtrim"):
        return ("fn", name, [gen(rng, depth - 1)], None)
    if name == "substr":
        return ("fn", name, [gen(rng, depth - 1)], (rng.randint(1, 6), rng.randint(0, 8)))
    if name == "concat":
        return ("fn", name, [gen(rng, depth - 1) for _ in range(rng.randint(2, 4))], None)
    f = lit_text(rng) or b"x"
    return ("fn", name, [gen(rng, depth - 1), ("lit", f), ("lit", lit_text(rng))], None)


def gen_call(rng, depth):
    """a tree whose top is a call (a text definition is a call)"""
    while True:
        t = gen(rng, depth)
        if t[0] == "fn":
            return t


def rows_of(rng, n):
    return [[text(rng) for _ in NAMES] for _ in range(n)]


# ---- hand cases: (rows, definitions, what the answer adds) --------------------------------------------------------------------------------
# what is a list of results per definition for each row, ("E", rule, row) for the refusal of a row (the rows before it are answered), or ("C", rule) for a definition refused at compile time

R1 = [[b"Hello World", b"  pad  ", b"x,y", b'say "hi"']]
HAND = [
    (R1, 'u=lower(a);v=upper(a)', [[b"hello world", b"HELLO WORLD"]]),
    (R1, 'u=trim(b);v=ltrim(b);w=rtrim(b)', [[b"pad", b"pad  ", b"  pad"]]),
    ([[b"\t a \t", b"", b"", b""]], 'u=trim(a);v=ltrim(a);w=rtrim(a)', [[b"a", b"a \t", b"\t a"]]),
    (R1, 'u=substr(a, 1, 5);v=substr(a, 7, 100);w=substr(a, 20, 3);z=substr(a, 3, 0)', [[b"Hello", b"World", b"", b""]]),
    (R1, 'u=concat(a, "-", b);v=concat(c, d)', [[b"Hello World-  pad  ", b'x,ysay "hi"']]),
    (R1, 'u=replace(a, "o", "0");v=replace(a, "l", "");w=replace("aaa", "aa", "b")', [[b"Hell0 W0rld", b"Heo Word", b"ba"]]),
    (R1, 'u=upper(trim(b));v=concat(lower(a), "!", upper(c))', [[b"PAD", b"hello world!X,Y"]]),
    (R1, 'u=concat(c, d)', [[b'x,ysay "hi"']]),
    ([[b"\xc3\xa9T\xc3\x89", b"", b"", b""]], 'u=lower(a);v=upper(a)', [[b"\xc3\xa9t\xc3\x89", b"\xc3\xa9T\xc3\x89"]]),
    ([[b"\xc3\xa9abc", b"", b"", b""]], 'u=substr(a, 1, 2);v=substr(a, 3, 3)', [[b"\xc3\xa9", b"abc"]]),
    ([[b"\xc3\xa9abc", b"", b"", b""]], 'u=substr(a, 2, 3)', ("E", "compute.not-utf8-boundary", 1)),
    ([[b"\xc3\xa9abc", b"", b"", b""]], 'u=substr(a, 1, 1)', ("E", "compute.not-utf8-boundary", 1)),
    ([[b"abc", b"", b"", b""], [b"\xf0\x9f\x98\x80z", b"", b"", b""]], 'u=substr(a, 1, 2)', ("E", "compute.not-utf8-boundary", 2)),
    ([[b"\x80\x80", b"", b"", b""]], 'u=substr(a, 1, 1)', [[b"\x80"]]),
    ([[b"", b"", b"", b""]], 'u=lower(a);v=trim(a);w=concat(a, b)', [[b"", b"", b""]]),
    # a text with a comma, a quote, a line feed and a CR is a cell the writers quote
    ([[b"a,b", b'q"', b"l\nf", b"c\rr"]], 'u=concat(a, "")', [[b"a,b"]]),
    ([[b"a,b", b'q"', b"l\nf", b"c\rr"]], 'u=concat(b, c, d)', [[b'q"l\nfc\rr']]),
    ([[b"x", b"y", b"z", b"w"]], 'u=concat(a, ",", b);v=concat(a, "\n", b);w=concat(a, "\r", b)', [[b"x,y", b"x\ny", b"x\ry"]]),
    ([[b"a", b"b", b"c", b"d"]], 'u=concat(a, """", b)', [[b'a"b']]),
    ([[b"a", b"b", b"c", b"d"]], "u=upper('c d')", [[b"C"]], ["a", "b", "c d", "d"]),
    ([[b"a", b"b", b"c", b"d"]], "u=upper(#3)", [[b"C"]]),
    ([[b"1", b"2", b"3", b"4"]], 'lower=upper(a)', [[b"1"]]),
    # chaining
    (R1, 'u=trim(b);v=upper(u)', [[b"pad", b"PAD"]]),
    # refusals at compile time
    (R1, 'u=lower()', ("C", "compute.syntax")),
    (R1, 'u=lower(a, b)', ("C", "compute.arity")),
    (R1, 'u=concat(a)', ("C", "compute.arity")),
    (R1, 'u=concat(a,b,a,b,a,b,a,b,a)', ("C", "compute.arity")),
    (R1, 'u=substr(a, 1)', ("C", "compute.arity")),
    (R1, 'u=replace(a, "x")', ("C", "compute.arity")),
    (R1, 'u=substr(a, 0, 3)', ("C", "compute.bad-literal")),
    (R1, 'u=substr(a, -1, 3)', ("C", "compute.bad-literal")),
    (R1, 'u=substr(a, 1, -3)', ("C", "compute.bad-literal")),
    (R1, 'u=substr(a, 1, 1.5)', ("C", "compute.bad-literal")),
    (R1, 'u=substr(a, b, 3)', ("C", "compute.bad-literal")),
    (R1, 'u=substr(a, 1, 9999999999)', ("C", "compute.bad-literal")),
    (R1, 'u=replace(a, "", "x")', ("C", "compute.bad-literal")),
    (R1, 'u=replace(a, b, "x")', ("C", "compute.bad-literal")),
    (R1, 'u=lower("abc', ("C", "compute.bad-literal")),
    (R1, 'u=concat(a, 1)', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(a:int)', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(a:dec(2))', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(1)', ("C", "compute.type-mismatch")),
    (R1, 'u=1+lower(a)', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(a)+1', ("C", "compute.type-mismatch")),
    (R1, 'n=a:int+len(a);u=upper(n)', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(a);n=u:int+1', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(a);n=u+1', ("C", "compute.type-mismatch")),
    (R1, 'u=lower(zz)', ("C", "compute.unknown-column")),
    (R1, 'u=nope(a)', ("C", "compute.unknown-function")),
    (R1, 'u=LOWER(a)', ("C", "compute.unknown-function")),
    (R1, 'u=lower(a))', ("C", "compute.syntax")),
    (R1, 'u=lower(a,)', ("C", "compute.syntax")),
    (R1, 'u=concat(a,,b)', ("C", "compute.syntax")),
    (R1, 'u=lower(len(a))', ("C", "compute.type-mismatch")),
    (R1, 'n=len(lower(a))', ("C", "compute.type-mismatch")),
    (R1, 'n=len(a:int)', ("C", "compute.type-mismatch")),
    (R1, 'n=len()', ("C", "compute.syntax")),
    (R1, 'n=len(a, b)', ("C", "compute.arity")),
    (R1, 'u=lower(lower(lower(lower(lower(a)))))', ("C", "compute.too-long")),
    (R1, 'u=lower(lower(lower(lower(a))))', [[b"hello world"]]),
    (R1, 'a=lower(a)', ("C", "compute.duplicate-name")),
]
# the number-valued len, with its answers
LEN = [
    ([[b"Hello", b"\xc3\xa9", b"", b""]], 'n=len(a);m=len(b);k=len(c);j=len("xyz")', [[b"5", b"2", b"0", b"3"]]),
    ([[b"Hello", b"\xc3\xa9", b"", b""]], 'n=len(a)+len(b)*2;j=len("")', [[b"9", b"0"]]),
    ([[b"Hello", b"\xc3\xa9", b"", b""]], 'n=len(a);u=upper(a);m=n+1', [[b"5", b"HELLO", b"6"]]),
]


def results(row, defs):
    """what the definitions add to a row: a list of bytes (numbers as their text), or raises RowRefusal"""
    out = []
    for d in defs:
        kind, tree = d
        if kind == "text":
            out.append(ev(tree, row))
        else:
            out.append(str(tree(row)).encode())
    return out


def selftest():
    n = 0
    assert ev(("fn", "lower", [("lit", b"AbC\xc3\x89")], None), []) == b"abc\xc3\x89"
    assert ev(("fn", "upper", [("lit", b"aBc\xc3\xa9")], None), []) == b"ABC\xc3\xa9"
    assert ev(("fn", "trim", [("lit", b" \t a b \t ")], None), []) == b"a b"
    assert ev(("fn", "ltrim", [("lit", b" \t a b \t ")], None), []) == b"a b \t "
    assert ev(("fn", "rtrim", [("lit", b" \t a b \t ")], None), []) == b" \t a b"
    assert ev(("fn", "replace", [("lit", b"aaa"), ("lit", b"aa"), ("lit", b"b")], None), []) == b"ba"
    assert ev(("fn", "concat", [("lit", b"a"), ("lit", b"b"), ("lit", b"c")], None), []) == b"abc"
    assert substr(b"Hello World", 7, 100) == b"World" and substr(b"abc", 4, 2) == b"" and substr(b"abc", 2, 0) == b""
    assert substr("éabc".encode(), 1, 2) == "é".encode()
    for args, name in (((2, 3), "mid"), ((1, 1), "cut")):
        try:
            substr("éabc".encode(), *args)
            raise AssertionError(name)
        except Boundary:
            pass
    assert quoted(b'a"b') == b'a""b'
    rng = random.Random(1)
    for _ in range(20000):
        t = gen_call(rng, 3)
        row = [text(rng) for _ in NAMES]
        try:
            ev(t, row)
        except Boundary:
            pass
        n += 1
    return n


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print(f"the reference holds on {selftest()} random trees and its fixed cases")
