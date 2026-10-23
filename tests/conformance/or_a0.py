"""The cases of slice A0 of docs/or-and-columns.md: the repair `a = 1 or a = 2` to `a in (1, 2)`, and the refusals that must not change because of it.

Three things live here, and test_or_a0.py holds the tool against them:

* REPAIRS and NO_REPAIR: the expressions (and queries) that are refused as today, with the corrected one the repair offers, or with none. The table is the specification;
  it was written from docs/or-and-columns.md section 7 before the repair existed, and `reference_repair` below is a second statement of the same rule, in Python, that shares
  no code with the tool (a tokenizer of --where written from docs/filter.md, and query_ref.lex for a query);
* golden_cases(): every refusal of the rules `where.*` and `query.*` that this suite can think of (the hand lists, the pinned refusals of query_cases.py, and seeded soups of
  tokens), run on `main` before the change, whose answers are stored in or_a0_golden.json. After the change every answer must be the same bytes, except the repair of the
  cases of REPAIRS (G-COMPAT, G-OFFSETS of the document);
* the generators of the oracle test (tables and disjunctions) and of the fuzz.

    TOOLBOX_BIN=/path/to/main/build python3 tests/conformance/or_a0.py --write-golden     # only ever run with the binary of `main` the slice starts from
"""

import hashlib
import json
import pathlib
import random
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import query_cases  # noqa: E402
import query_ref  # noqa: E402
import toolbox_pin  # noqa: E402

GOLDEN = HERE / "or_a0_golden.json"
MAIN_GOLDEN = HERE / "or_a1_main_golden.json"       # the answers of main (62d60ad, slice A0 merged) to every case below, refusals and answers: slice A1's compatibility gate

# The file every case is run against: the columns the cases name, and the keywords as columns (a column called `or` is an ordinary column at the start of a condition).
HEADER = ["a", "b", "c", "i", "d", "f", "t", "or", "not", "and", "in", "contains", "a b"]
DATA = "a,b,c,i,d,f,t,or,not,and,in,contains,a b\n1,x,07,5,1.50,1.5,p,1,2,3,4,5,6\n2,y,,6,2.00,2.5,q,2,3,4,5,6,7\n3,x,9,-7,0.25,2e3,\"r,s\",3,4,5,6,7,8\n"

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The repair, as the document states it (section 7): `COND or COND { or COND }`, each COND `COLUMN = VALUE` or `COLUMN in (...)`, the same column written the same with the
# same type suffix, and the result within the 1,024 values of an `in`. (expression, the expression the repair offers)

REPAIRS = [
    ("a = 1 or a = 2", "a in (1, 2)"),
    ("a=1 or a=2", "a in (1, 2)"),
    ("  a = 1   or   a = 2  ", "a in (1, 2)"),
    ("a = 1 or a = 2 or a = 3", "a in (1, 2, 3)"),
    ("a = 1 or a = 1", "a in (1, 1)"),
    ("a in (1, 2) or a = 3", "a in (1, 2, 3)"),
    ("a = 1 or a in (2,3)", "a in (1, 2, 3)"),
    ("a in (1) or a in (2)", "a in (1, 2)"),
    ("a in(1,2) or a in(3)", "a in (1, 2, 3)"),
    ("b = x or b = y", "b in (x, y)"),
    ("b = 'x y' or b = z", "b in ('x y', z)"),
    ("b = '' or b = 'x'", "b in ('', 'x')"),
    ("b = 'it''s' or b = \"q\"", "b in ('it''s', \"q\")"),
    ("'a b' = x or 'a b' = y", "'a b' in (x, y)"),
    ("a\\ b = 1 or a\\ b = 2", "a\\ b in (1, 2)"),
    ("#1 = 1 or #1 = 2", "#1 in (1, 2)"),
    ("or = 1 or or = 2", "or in (1, 2)"),
    ("in = 1 or in = 2", "in in (1, 2)"),
    ("not = 1 or not = 2", "not in (1, 2)"),
    ("and = 1 or and = 2", "and in (1, 2)"),
    ("contains = 1 or contains = 2", "contains in (1, 2)"),
    ("a:int = 1 or a:int = 07", "a:int in (1, 07)"),
    ("a:int = -1 or a:int = +2", "a:int in (-1, +2)"),
    ("a:int in (1, 2) or a:int = 3", "a:int in (1, 2, 3)"),
    ("a:int = 9223372036854775807 or a:int = -9223372036854775808", "a:int in (9223372036854775807, -9223372036854775808)"),
    ("'a':int = 1 or 'a':int = 2", "'a':int in (1, 2)"),
    ("d:dec(2) = 1.50 or d:dec(2) = 2", "d:dec(2) in (1.50, 2)"),
    ("d:dec(2) = .5 or d:dec(2) = -0.25", "d:dec(2) in (.5, -0.25)"),
    ("'d':dec(2) = 1 or 'd':dec(2) = 2", "'d':dec(2) in (1, 2)"),
    ("f:float = 1.5 or f:float = 2e3", "f:float in (1.5, 2e3)"),
    ("f:float = 0.1 or f:float = 1E-3 or f:float = 5", "f:float in (0.1, 1E-3, 5)"),
]

# Refused as today, and no repair: each is the document's "not offered" (operators, types, columns, contains, nesting, the mix, the ceiling) or something that is not
# the shape at all.
NO_REPAIR = [
    "a != 1 or a != 2", "a = 1 or a != 2", "a != 1 or a = 2", "a < 1 or a < 2", "a <= 1 or a >= 2", "a > 1 or a = 2",
    "a contains x or a contains y", "a = x or a contains y", "a contains x or a = y",
    "a = 1 or b = 2", "a = 1 or a = 2 or b = 3", "a = 1 or b = 2 or a = 3", "a = 1 or A = 2", "a = 1 or 'a ' = 2",
    "a = 1 or #1 = 2", "#1 = 1 or a = 2", "a = 1 or 'a' = 2", "'a' = 1 or a = 2", "a = 1 or a\\ = 2",
    "a:int = 1 or a = 2", "a = 1 or a:int = 2", "a:int = 1 or a:float = 2", "a:float = 1 or a:int = 2", "d:dec(2) = 1 or d:dec(3) = 2", "d:dec(2) = 1 or d:dec(2)= 2 or d = 3",
    "a = 1 or a = 2 and b = 3", "a = 1 or a = 2 and a = 3", "a = 1 and a = 2 or a = 3", "b = 1 and a = 1 or a = 2", "a = 1 or a = 2 and",
    "a = 1 or (a = 2)", "(a = 1 or a = 2)", "(a = 1) or (a = 2)", "a = 1 or not a = 2", "a = 1 or not (a = 2)", "not a = 1 or a = 2",
    "a = 1 or", "a = 1 or ", "a = 1 or a", "a = 1 or a =", "a = 1 or a = ", "a = 1 or a in", "a = 1 or a in (", "a = 1 or a in ()", "a = 1 or a in (1", "a = 1 or a in (1,)",
    "a = 1 or or", "a = 1 or or = 2", "a = 1 or a = 2 or", "a = 1 or a = 2 or a", "a = 1 or a = 2 or a !=3",
    "a = 1 OR a = 2", "a = 1 Or a = 2", "a = 1 'or' a = 2", "a = 1 or'a' = 2 or",
    "a:int = 1 or a:int = x", "a:int = 1 or a:int = 1.5", "a:int = 1 or a:int = ''", "a:int = 1 or a:int = 9223372036854775808", "a:int = x or a:int = 2",
    "d:dec(2) = 1 or d:dec(2) = 1.555", "f:float = 1 or f:float = inf", "f:float = nan or f:float = 1", "d:dec(2) = 1 or d:dec(2) = 12345678901234567",
    "a:int contains 1 or a:int = 2",
    "a = 1 or a == 2", "a = 1 or a <> 2", "a = 1 or a = 2 or a <> 3",
    "a = 1 or a in (1 2)", "a = 1 or a in 5", "a = 'x or a = 2", "a = 1 or a = 2 b = 3",
    "a = 1 or a = 2, a = 3", "a = 1 or ,",
    "a = 1 or a = 2 xor a = 3", "a = 1 || a = 2",
]
# a chain of `n` leaves whose values come to exactly `values`: the ceilings
CEILING_OFFERED = [("chain", 64), ("chain", 65), ("chain", 1000), ("chain", 1024), ("lists", 1024)]
CEILING_REFUSED = [("chain", 1025), ("chain", 1100), ("lists", 1025)]


def ceiling_expr(kind, n):
    """(the expression, the expression a repair would offer) for a disjunction of `n` values written as `n` equalities, or as two lists."""
    values = [str(k) for k in range(n)]
    if kind == "chain":
        return " or ".join("a = %s" % v for v in values), "a in (%s)" % ", ".join(values)
    half = n // 2
    return "a in (%s) or a in (%s)" % (", ".join(values[:half]), ", ".join(values[half:])), "a in (%s)" % ", ".join(values)


# The same, as queries: (query, the corrected query). The columns of the query are the columns of HEADER; a name that is a keyword is written "like this".
QUERY_REPAIRS = [
    ("select a from 't.csv' where b = 'x' or b = 'y'", "select a from 't.csv' where b in ('x', 'y')"),
    ("select a from 't.csv' where b='x' or b='y'", "select a from 't.csv' where b in ('x', 'y')"),
    ("select a from 't.csv' where b = 'x' OR b = 'y'", "select a from 't.csv' where b in ('x', 'y')"),
    ("select a from 't.csv' where a = 1 or a = 2", "select a from 't.csv' where a in (1, 2)"),
    ("select a from 't.csv' where a = 1 or a = 2 or a = 3", "select a from 't.csv' where a in (1, 2, 3)"),
    ("select a from 't.csv' where a in (1, 2) or a = 3", "select a from 't.csv' where a in (1, 2, 3)"),
    ("select a from 't.csv' where a = 1 or a in (2, 3)", "select a from 't.csv' where a in (1, 2, 3)"),
    ("select a from 't.csv' where a in (1) or a in (2)", "select a from 't.csv' where a in (1, 2)"),
    ("select a from 't.csv' where a = 1 or a = 1", "select a from 't.csv' where a in (1, 1)"),
    ("select a from 't.csv' where b = 'it''s' or b = ''", "select a from 't.csv' where b in ('it''s', '')"),
    ("select a from 't.csv' where \"a b\" = 'x' or \"a b\" = 'y'", "select a from 't.csv' where \"a b\" in ('x', 'y')"),
    ("select a from 't.csv' where \"or\" = 1 or \"or\" = 2", "select a from 't.csv' where \"or\" in (1, 2)"),
    ("select a from 't.csv' where #1 = 1 or #1 = 2", "select a from 't.csv' where #1 in (1, 2)"),
    ("select a from 't.csv' where i::int = 1 or i::int = 07", "select a from 't.csv' where i::int in (1, 07)"),
    ("select a from 't.csv' where cast(i as int) = 1 or cast(i as int) = 2", "select a from 't.csv' where cast(i as int) in (1, 2)"),
    ("select a from 't.csv' where d::dec(2) = 1.5 or d::dec(2) = 2", "select a from 't.csv' where d::dec(2) in (1.5, 2)"),
    ("select a from 't.csv' where f::float = 1.5 or f::float = 2e3", "select a from 't.csv' where f::float in (1.5, 2e3)"),
    ("select a from 't.csv' where f::float = -1.5 or f::float = +2", "select a from 't.csv' where f::float in (-1.5, +2)"),
    ("select b, count(*) from 't.csv' where a = 1 or a = 2 group by b", "select b, count(*) from 't.csv' where a in (1, 2) group by b"),
    ("select a from 't.csv' where a = 1 or a = 2 order by b desc limit 5", "select a from 't.csv' where a in (1, 2) order by b desc limit 5"),
    ("select a from 't.csv' where a = 1 or a = 2 limit 5 offset 1", "select a from 't.csv' where a in (1, 2) limit 5 offset 1"),
    ("select a from 't.csv' where a = 1 or a = 2;", "select a from 't.csv' where a in (1, 2);"),
    ("select * from 't.csv' where a = 1 or a = 2", "select * from 't.csv' where a in (1, 2)"),
    ("select count(*) from 't.csv' where a = 1 or a = 2", "select count(*) from 't.csv' where a in (1, 2)"),
    ("select a from stdin where a = 1 or a = 2", "select a from stdin where a in (1, 2)"),
]

QUERY_NO_REPAIR = [
    "select a from 't.csv' where b != 'x' or b != 'y'", "select a from 't.csv' where b <> 'x' or b <> 'y'", "select a from 't.csv' where a < 1 or a < 2",
    "select a from 't.csv' where a = 1 or a != 2", "select a from 't.csv' where a > 1 or a = 2", "select a from 't.csv' where b contains 'x' or b contains 'y'",
    "select a from 't.csv' where b = 'x' or c = 'y'", "select a from 't.csv' where b = 'x' or b = 'y' or c = 'z'", "select a from 't.csv' where b = 'x' or B = 'y'",
    "select a from 't.csv' where b = 'x' or \"b\" = 'y'", "select a from 't.csv' where a = 1 or #1 = 2", "select a from 't.csv' where i::int = 1 or i = 2",
    "select a from 't.csv' where i::int = 1 or cast(i as int) = 2", "select a from 't.csv' where i::int = 1 or i::float = 2", "select a from 't.csv' where d::dec(2) = 1 or d::dec(3) = 2",
    "select a from 't.csv' where b = 'x' or b = 'y' and a = 1", "select a from 't.csv' where b = 'x' and a = 1 or b = 'y'", "select a from 't.csv' where a = 1 and b = 'x' or a = 2",
    "select a from 't.csv' where (b = 'x' or b = 'y')", "select a from 't.csv' where b = 'x' or (b = 'y')", "select a from 't.csv' where b = 'x' or not b = 'y'",
    "select a from 't.csv' where b = 'x' or", "select a from 't.csv' where b = 'x' or b", "select a from 't.csv' where b = 'x' or b =", "select a from 't.csv' where b = 'x' or b in (",
    "select a from 't.csv' where b = 'x' or b in ()", "select a from 't.csv' where b = 'x' or b like 'x%'", "select a from 't.csv' where b = 'x' or b is null",
    "select a from 't.csv' where b = 'x' or b = null", "select a from 't.csv' where b = 'x' or b = c", "select a from 't.csv' where b = 'x' or b = \"y\"", "select a from 't.csv' where b = 'x' or b == 'y'",
    "select a from 't.csv' where b = 'x' or b between 1 and 2", "select a from 't.csv' where b = 'x' or b not in ('y')", "select a from 't.csv' where b = 1 or b = 2 or",
    "select a from 't.csv' where i::int = 1 or i::int = 'x'", "select a from 't.csv' where i::int = 1 or i::int = 1.5", "select a from 't.csv' where d::dec(2) = 1 or d::dec(2) = 1.555",
    "select a from 't.csv' where i::int = 1 or i::int = 9223372036854775808", "select a from 't.csv' where i::int contains 1 or i::int = 2",
    "select a from 't.csv' order by b or a", "select a from 't.csv' where a = 1 order by b or a", "select a from 't.csv' or", "select a from 't.csv' or a = 1",
    "select a from 't.csv' where a = 1 group by b or a", "select a from 't.csv' where a = 1 limit 5 or", "select a from 't.csv' where a = 1 or a = 2 or",
    "select a from 't.csv' where a = 1 or a = 2 having count(*) > 1", "select a from 't.csv' where a = 1 or a = 2 union select b from 't.csv'",
    "select a from 't.csv' where a = 1 or a = 2 foo", "select a from 't.csv' where a = 1 or a = 2 and", "select a from 't.csv' where a = 1 or a = 2 and or",
    "select a, count(*) from 't.csv' where a = 1 or a = 2", "select a from 't.csv' where a = 1 or a = 2 group by b",
    "select a as x from 't.csv' where a = 1 or a = 2",
]


def query_ceiling(kind, n):
    """(the query, the corrected one) for `n` values: a chain of equalities (4,096 bytes of query come first at about 450 of them), or two lists of one-digit values written
    without spaces, which is how 1,024 and 1,025 values fit a query."""
    if kind == "chain":
        values = [str(k) for k in range(n)]
        return "select a from 't.csv' where " + " or ".join("a = %s" % v for v in values), "select a from 't.csv' where a in (%s)" % ", ".join(values)
    values = [str(k % 10) for k in range(n)]
    half = n // 2
    return ("select a from 't.csv' where a in (%s) or a in (%s)" % (",".join(values[:half]), ",".join(values[half:])), "select a from 't.csv' where a in (%s)" % ", ".join(values))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# A reference for the rule, written from docs/or-and-columns.md section 7 and docs/filter.md, sharing nothing with the tool.

_BARE = r"(?:\\.|[^\s'(),=!<>\\])"
_COLUMN = re.compile(r"(?:'(?:[^']|'')*'(?::int|:float|:dec\(\d{1,2}\))?|" + _BARE + r"*:dec\(\d{1,2}\)|" + _BARE + r"+)")
_VALUE = re.compile(r"(?:'(?:[^']|'')*'|" + _BARE + r"+)")
_WS = re.compile(r"[ \t\r\n]*")
INT_MAX = 2**63 - 1


def _type_of(column):
    """None for text, else 'int', 'float' or ('dec', scale): the suffix the column is written with."""
    if column.startswith("'"):
        tail = column[column.rindex("'") + 1:]
    else:
        tail = column
    if tail.endswith(":int"):
        return "int"
    if tail.endswith(":float"):
        return "float"
    m = re.search(r":dec\((\d+)\)$", tail)
    if m:
        return ("dec", int(m.group(1)))
    return None


def _unquote(v):
    return v[1:-1].replace("''", "'") if v.startswith("'") else re.sub(r"\\(.)", r"\1", v)


def _literal_ok(ty, v):
    """Whether the literal fits the type (docs/numbers.md): the cases the corpus draws, which are the cases the document names."""
    w = _unquote(v)
    if ty is None:
        return True
    if ty == "int":
        return re.fullmatch(r"[+-]?[0-9]+", w) is not None and -2**63 <= int(w) <= INT_MAX
    if ty == "float":
        if re.fullmatch(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", w) is None:
            return False
        try:
            x = float(w)
        except ValueError:
            return False
        return x == x and abs(x) != float("inf")
    scale = ty[1]
    m = re.fullmatch(r"[+-]?([0-9]*)\.?([0-9]*)", w)
    if m is None or (not m.group(1) and not m.group(2)):
        return False
    if len(m.group(2)) > scale:
        return False
    digits = (m.group(1) + m.group(2).ljust(scale, "0")).lstrip("0")
    return len(digits) <= 17


def reference_repair(expr):
    """The expression the repair offers for `expr`, or None. `COND or COND { or COND }`, a COND `COLUMN = VALUE` or `COLUMN in (V { , V })`, the column written the same each
    time, at most 1,024 values in all, and every literal one the type takes."""
    i = _WS.match(expr).end()
    column = None
    values = []
    leaves = 0
    while True:
        m = _COLUMN.match(expr, i)
        if not m:
            return None
        text = m.group(0)
        if column is None:
            column = text
        elif text != column:
            return None
        i = _WS.match(expr, m.end()).end()
        if expr.startswith("=", i) and not expr.startswith("==", i):
            i = _WS.match(expr, i + 1).end()
            v = _VALUE.match(expr, i)
            if not v:
                return None
            values.append(v.group(0))
            i = v.end()
        elif re.match(r"in(?![^\s'(),=!<>])", expr[i:]):
            i = _WS.match(expr, i + 2).end()
            if not expr.startswith("(", i):
                return None
            i += 1
            while True:
                i = _WS.match(expr, i).end()
                v = _VALUE.match(expr, i)
                if not v:
                    return None
                values.append(v.group(0))
                i = _WS.match(expr, v.end()).end()
                if expr.startswith(",", i):
                    i += 1
                elif expr.startswith(")", i):
                    i += 1
                    break
                else:
                    return None
        else:
            return None
        leaves += 1
        i = _WS.match(expr, i).end()
        if i == len(expr):
            break
        if re.match(r"or(?![^\s'(),=!<>])", expr[i:]):
            i = _WS.match(expr, i + 2).end()
            continue
        return None
    ty = _type_of(column)
    if leaves < 2 or len(values) > 1024 or not all(_literal_ok(ty, v) for v in values):
        return None
    return "%s in (%s)" % (column, ", ".join(values))


_QTOKEN = re.compile(r"""\s+|'(?:[^']|'')*'|"(?:[^"]|"")+"|\#\d+|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|!=|<>|<=|>=|::|[,()*=<>;]""")


class _Tok:
    def __init__(self, text, start, end):
        self.text, self.start, self.end = text, start, end
        c = text[0]
        self.kind = "str" if c == "'" else "qid" if c == '"' else "pos" if c == "#" else "num" if (c.isdigit() or (c in "+-." and len(text) > 1)) else "id" if (c.isalpha() or c == "_") else "sym"

    def word(self):
        return self.text.lower() if self.kind == "id" else None


def _qlex(q):
    toks, i = [], 0
    while i < len(q):
        m = _QTOKEN.match(q, i)
        if not m:
            return None
        if not m.group(0).isspace():
            toks.append(_Tok(m.group(0), i, m.end()))
        i = m.end()
    return toks


def reference_query_repair(q):
    """The corrected query for a query whose where clause is `COND or COND { or COND }` of one column written the same, or None. The tokens are this file's own."""
    toks = _qlex(q)
    if toks is None:
        return None
    n = len(toks)
    k = 0
    while k < n and toks[k].word() != "where":
        k += 1
    if k >= n:
        return None
    k += 1
    start = k
    column = None
    values = []
    leaves = 0
    while True:
        a = k
        if k + 1 < n and toks[k].word() == "cast" and toks[k + 1].text == "(":
            k += 1
            depth = 0
            while k < n:
                depth += (toks[k].text == "(") - (toks[k].text == ")")
                k += 1
                if depth == 0:
                    break
        elif k < n and toks[k].kind in ("id", "qid", "pos"):
            k += 1
            if k < n and toks[k].text == "::":
                k += 1
                if k < n and toks[k].kind == "id":
                    typ = toks[k].word()
                    k += 1
                    if typ == "dec":
                        depth = 0
                        while k < n:
                            depth += (toks[k].text == "(") - (toks[k].text == ")")
                            k += 1
                            if depth == 0:
                                break
        else:
            return None
        if k <= a:
            return None
        text = q[toks[a].start:toks[k - 1].end]
        if column is None:
            column = text
        elif text != column:
            return None
        if k < n and toks[k].text == "=":
            k += 1
            if k < n and toks[k].kind in ("str", "num"):
                values.append(toks[k].text)
                k += 1
            else:
                return None
        elif k < n and toks[k].word() == "in":
            k += 1
            if not (k < n and toks[k].text == "("):
                return None
            k += 1
            while True:
                if k < n and toks[k].kind in ("str", "num"):
                    values.append(toks[k].text)
                    k += 1
                else:
                    return None
                if k < n and toks[k].text == ",":
                    k += 1
                elif k < n and toks[k].text == ")":
                    k += 1
                    break
                else:
                    return None
        else:
            return None
        leaves += 1
        if k < n and toks[k].word() == "or":
            k += 1
            continue
        break
    if leaves < 2 or len(values) > 1024 or (k < n and toks[k].word() == "and"):
        return None
    m = re.search(r"(?:::|\bas\s+)(int|float|dec\s*\(\s*(\d+)\s*\))\s*\)?$", column, re.I)
    ty = None if not m else "int" if m.group(1).lower() == "int" else "float" if m.group(1).lower() == "float" else ("dec", int(m.group(2)))
    if not all(_literal_ok(ty, v) for v in values):
        return None
    candidate = q[:toks[start].start] + "%s in (%s)" % (column, ", ".join(values)) + q[toks[k - 1].end:]
    try:
        query_ref.translate(candidate)       # offered only when it translates: the reference translator, which shares no code with the tool
    except query_ref.QueryError:
        return None
    return candidate


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The soups: seeded random expressions and queries made of the pieces of the grammar, then damaged, so that most are near misses.

COLUMNS = ["a", "b", "c", "'a b'", "#1", "a:int", "i:int", "d:dec(2)", "f:float", "t", "or", "not", "and", "in", "contains", "'a'", "A", "zz", "a\\ b"]
OPS = ["=", "!=", "<", "<=", ">", ">=", "=", "=", "contains", "in", "in", "not in", "not contains", "=="]
VALUES = ["1", "2", "3", "x", "y", "'y z'", "07", "1.5", "''", "-1", "+2", "2e3", ".5", "99999999999999999999", "inf", "'it''s'", "q"]
LISTS = ["(1, 2)", "(x,y)", "(1)", "()", "(1", "(1 2)", "(1,)", "('a', 'b')", "(07, 1.5)", "5"]
CONNECTIVES = ["or", "or", "or", "and", "OR", "xor", "||", "", ",", ")", "(", "not", "or not", "and not"]


def where_soup(rng):
    parts = []
    for _ in range(rng.randint(1, 5)):
        col = rng.choice(COLUMNS)
        op = rng.choice(OPS)
        val = rng.choice(LISTS) if op.endswith("in") else rng.choice(VALUES)
        if rng.random() < 0.55 and parts:
            col = parts[0].split()[0]       # the same column again: the shape the repair wants
        parts.append("%s %s %s" % (col, op, val) if rng.random() < 0.9 else "%s%s%s" % (col, op.strip(), val))
    out = parts[0]
    for p in parts[1:]:
        out += " %s %s" % (rng.choice(CONNECTIVES) if rng.random() < 0.6 else "or", p)
    r = rng.random()
    if r < 0.2:
        out = "(" + out + ")"
    elif r < 0.3:
        out = out[:rng.randint(0, len(out))]
    elif r < 0.4:
        i = rng.randint(0, max(0, len(out) - 1))
        out = out[:i] + rng.choice(["'", "\\", "(", ")", ",", " or ", " and ", "\x01"]) + out[i + 1:]
    return out


def where_chain_soup(rng):
    """A chain of `or` on mostly one column, `=` and `in` mostly, with now and then a leaf that breaks the shape: the near misses of the repair."""
    col = rng.choice(["a", "b", "t", "'a b'", "#1", "a:int", "i:int", "d:dec(2)", "f:float", "or", "in", "not"])
    kind = "i" if ":int" in col else "d" if ":dec" in col else "f" if ":float" in col else "t"
    pool = {"i": ["1", "2", "07", "-3", "+4", "'5'", "x", "1.5"], "d": ["1.50", "2", ".5", "-0.25", "1.555", "x"], "f": ["1.5", "2e3", "0.1", "inf", "x"],
            "t": ["x", "y", "'y z'", "''", "1", "07", "'it''s'", "q"]}[kind]
    out = []
    for _ in range(rng.randint(2, 5)):
        c = col if rng.random() < 0.9 else rng.choice(COLUMNS)
        r = rng.random()
        if r < 0.55:
            out.append("%s = %s" % (c, rng.choice(pool)))
        elif r < 0.85:
            out.append("%s in (%s)" % (c, ", ".join(rng.choice(pool) for _ in range(rng.randint(1, 3)))))
        elif r < 0.9:
            out.append("%s != %s" % (c, rng.choice(pool)))
        elif r < 0.95:
            out.append("%s contains %s" % (c, rng.choice(pool)))
        else:
            out.append("%s<%s" % (c, rng.choice(pool)))
    text = out[0]
    for p in out[1:]:
        text += rng.choice([" or "] * 18 + [" and ", " OR ", " or ( "]) + p
    if rng.random() < 0.08:
        text = text[:rng.randint(0, len(text))]
    return text


Q_COLUMNS = ["a", "b", "c", "\"a b\"", "#1", "a::int", "i::int", "cast(i as int)", "d::dec(2)", "f::float", "\"or\"", "\"not\"", "t", "A", "zz"]
Q_OPS = ["=", "=", "=", "!=", "<>", "<", ">=", "in", "in", "contains", "like", "is", "not in", "=="]
Q_VALUES = ["1", "2", "3", "'x'", "'y'", "'y z'", "07", "1.5", "''", "-1", "2e3", "'it''s'", "null", "c", "\"y\"", "9223372036854775808"]
Q_LISTS = ["(1, 2)", "('x','y')", "(1)", "()", "(1", "(1 2)", "(1,)", "(07, 1.5)"]
Q_TAILS = ["", "", "", " group by b", " order by a", " order by a desc limit 5", " limit 3 offset 1", ";", " having count(*) > 1", " foo", " or", " and a = 1", " order by a or b"]


def query_soup(rng):
    parts = []
    for _ in range(rng.randint(1, 5)):
        col = rng.choice(Q_COLUMNS)
        if rng.random() < 0.55 and parts:
            col = parts[0].split(" ")[0] if " " not in parts[0].split(" ")[0] else col
        op = rng.choice(Q_OPS)
        val = rng.choice(Q_LISTS) if op.endswith("in") else rng.choice(Q_VALUES)
        parts.append("%s %s %s" % (col, op, val))
    out = parts[0]
    for p in parts[1:]:
        out += " %s %s" % (rng.choice(["or", "or", "or", "and", "OR", "xor"]), p)
    q = "select a from 't.csv' where " + out + rng.choice(Q_TAILS)
    r = rng.random()
    if r < 0.15:
        q = q[:rng.randint(0, len(q))]
    elif r < 0.25:
        i = rng.randint(0, len(q) - 1)
        q = q[:i] + rng.choice(["'", "\"", "(", ")", ",", " or ", ";"]) + q[i + 1:]
    return q


def query_chain_soup(rng):
    col = rng.choice(["a", "b", "t", "\"a b\"", "#1", "i::int", "cast(i as int)", "d::dec(2)", "f::float", "\"or\"", "\"in\""])
    kind = "i" if ("int" in col) else "d" if "dec" in col else "f" if "float" in col else "t"
    pool = {"i": ["1", "2", "07", "-3", "+4", "'5'", "'x'", "1.5"], "d": ["1.50", "2", ".5", "-0.25", "1.555", "'x'"], "f": ["1.5", "2e3", "0.1", "-2", "'x'"],
            "t": ["'x'", "'y'", "'y z'", "''", "1", "07", "'it''s'", "q", "null"]}[kind]
    out = []
    for _ in range(rng.randint(2, 5)):
        c = col if rng.random() < 0.9 else rng.choice(Q_COLUMNS)
        r = rng.random()
        if r < 0.55:
            out.append("%s = %s" % (c, rng.choice(pool)))
        elif r < 0.85:
            out.append("%s in (%s)" % (c, ", ".join(rng.choice(pool) for _ in range(rng.randint(1, 3)))))
        elif r < 0.9:
            out.append("%s <> %s" % (c, rng.choice(pool)))
        elif r < 0.95:
            out.append("%s contains %s" % (c, rng.choice(pool)))
        else:
            out.append("%s < %s" % (c, rng.choice(pool)))
    text = out[0]
    for p in out[1:]:
        text += rng.choice([" or "] * 18 + [" and ", " OR ", " or ("]) + p
    q = "select a from 't.csv' where " + text + rng.choice(["", "", "", " group by b", " order by a", " limit 3", ";", " foo"])
    if rng.random() < 0.06:
        q = q[:rng.randint(0, len(q))]
    return q


# a hand list of expressions for the golden: one for each place the grammar of --where can fail, the ceilings, and `or` near every other word
WHERE_HAND = [
    "", " ", "a", "a =", "a ! 5", "a == 5", "a = 5 b = 6", "a = 5 and", "a in 5", "a in (1 2)", "a in ()", "a in (1,", "a:int = x", "a:int contains 5", "'abc = 5", "a\\", "a = 'x",
    "= 5", "a = 5 and and", "a <> 5", "(a) = 5", "a:int = 9223372036854775808", "a:int = 1.5", "a:int = ''", "a or b", "a = 1 or", "a = 1 or or", "or", "or or", "or = 1 or", "or = 1 and or",
    "not (a = 1)", "a not in (1)", "a not contains x", "not a = 1", "a = 1 and not b = 2", "(a = 1 or b = 2) and c = 3", "a = 1 or b = 2 and c = 3", "a = 1 and b = 2 or c = 3",
    "a:dec = 1", "a:dec( = 1", "a:dec(x) = 1", "a:dec(19) = 1", "a:dec(2 = 1", "d:dec(2) = 1.555", "d:dec(2) = 1e5", "f:float = inf", "f:float = nan", "f:float = 1e999", "f:float = x",
    "a:foo = 1", "a:int:int = 1", "a:int = 1 and a:float = 1", "a = 1 or a:int = 2", "a:int = 1 or a = 2", "a = 'x' or 'a' = 'y'", "#9 = 1 or #9 = 2", "zz = 1 or zz = 2", "a = 1 or zz = 2",
    "a = 1 or a = 2 or a = 3 or a = 4 or a = 5", "a = 1 or a = 2 and b = 3 or a = 4", "a=1 or a=2 or b=3", "a in (1, 2) or a in (3, 4) or b in (5)", "a != 1 or a != 2", "a < 1 or a > 2",
    "a contains x or a contains y", "a contains x or a = 1", "a = 1 or a contains x", "a=1or a=2", "a=1 or'a'=2", "a = 1 or a = 2)", "a = 1 or a = (2)", "a = 1 or (a = 2", "a = 1 ) or a = 2",
    "a = 1 OR a = 2", "a = 1 or A = 2", "a = 1 or a = 2 ;", "a = 1 or a = 2 --", "a = 1 or a = 2 or", "a=1 or a=2 or a",
] + ["a = %s" % " and a = ".join(str(k) for k in range(n)) for n in (1, 63, 64, 65)] \
  + [" and ".join("a = %d" % k for k in range(n)) for n in (63, 64, 65)] \
  + ["a in (%s)" % ", ".join(str(k) for k in range(n)) for n in (1023, 1024, 1025)] \
  + [ceiling_expr(kind, n)[0] for kind, n in CEILING_OFFERED + CEILING_REFUSED] \
  + [" or ".join(["a = 1"] * n) for n in (2, 3, 10)] \
  + [" and ".join(["a = 1"] * 64) + " or a = 2", " and ".join(["a = 1"] * 64) + " and a = 2 or a = 3"]
WHERE_HAND += [e for e, _ in REPAIRS] + NO_REPAIR

QUERY_HAND = [q[0] for q in query_cases.PINNED] + [q for q, _ in QUERY_REPAIRS] + QUERY_NO_REPAIR \
    + [query_ceiling(kind, n)[0] for kind, n in CEILING_OFFERED + CEILING_REFUSED] \
    + ["select a from 't.csv' where " + " and ".join("a = %d" % k for k in range(n)) + " or a = 1" for n in (63, 64, 65)] \
    + ["select a from 't.csv' where " + " and ".join("a = %d" % k for k in range(n)) for n in (64, 65)]


def golden_cases():
    """The cases, as (id, kind, argv template). `{DIR}` is the scratch directory that holds t.csv. Every kind is an argv after the program name."""
    cases = []
    for e in WHERE_HAND:
        cases.append(("where", ["--root", "{DIR}", "--where", e, "--select", "a", "t.csv"]))
    rng = random.Random(20261015)
    for _ in range(900):
        cases.append(("where", ["--root", "{DIR}", "--where", where_soup(rng), "--select", "a", "t.csv"]))
    for _ in range(500):
        cases.append(("where", ["--root", "{DIR}", "--where", where_chain_soup(rng), "--select", "a", "t.csv"]))
    for q in QUERY_HAND:
        cases.append(("query", ["--root", "{DIR}", "--query", q]))
    for _ in range(500):
        cases.append(("query", ["--root", "{DIR}", "--query", query_soup(rng)]))
    for _ in range(500):
        cases.append(("query", ["--root", "{DIR}", "--query", query_chain_soup(rng)]))
    # the other forms an expression can arrive in: explain, a grouping, the type report, standard-input-free JSON lines out, the equals form of the flag
    for e in ("a = 1 or a = 2", "a = 1 or b = 2", "a:int = 1 or a:int = 07", "a != 1 or a != 2", "a = 1 or a = 2 and b = 3", "or = 1 or or = 2", "a in (1) or a in (2)"):
        cases.append(("explain", ["--root", "{DIR}", "--explain", "--where", e, "--select", "a", "t.csv"]))
        cases.append(("group", ["--root", "{DIR}", "--where", e, "--group", "b", "--agg", "count", "t.csv"]))
        cases.append(("report", ["--root", "{DIR}", "--report", "types", "--where", e, "t.csv"]))
        cases.append(("jsonl", ["--root", "{DIR}", "--format", "jsonl", "--where", e, "--select", "a", "t.csv"]))
        cases.append(("equals", ["--root", "{DIR}", "--where=" + e, "--select", "a", "t.csv"]))
        cases.append(("csv", ["--root", "{DIR}", "--format", "csv", "--where", e, "--select", "a", "t.csv"]))
        cases.append(("threads", ["--root", "{DIR}", "--threads", "4", "--parallel-min-bytes", "0", "--where", e, "--select", "a", "t.csv"]))
        cases.append(("order", ["--root", "{DIR}", "--where", e, "--order-by", "-a:int", "--select", "a", "t.csv"]))
    for q in ("select a from 't.csv' where b = 'x' or b = 'y'", "select a from 't.csv' where b = 'x' or c = 'y'"):
        cases.append(("explain-query", ["--root", "{DIR}", "--explain", "--query", q]))
        cases.append(("query-equals", ["--root", "{DIR}", "--query=" + q]))
        cases.append(("query-csv", ["--root", "{DIR}", "--format", "csv", "--query", q]))
        cases.append(("query-limit", ["--root", "{DIR}", "--limit", "2", "--query", q]))
    return cases


def case_id(kind, argv):
    return "%s-%s" % (kind, hashlib.sha256(json.dumps(argv).encode()).hexdigest()[:12])


REPAIR_JSON = re.compile(r'"repair":\{.*?\}(?=,"detail":)')


def normalise(stdout, stderr, directory, binary):
    """The answer with the scratch directory and the program's path replaced, as text."""
    text = (stdout + b"\n--stderr--\n" + stderr).decode("utf-8", "replace")
    return text.replace(str(directory), "{DIR}").replace(str(binary), "{BIN}")


def blank_repair(text):
    return REPAIR_JSON.sub('"repair":null', text)


def digest(text):
    """The sha of an answer, read as the tool at the earlier pin wrote it (toolbox_pin.before: seven hints that were null then and are sentences now)."""
    return hashlib.sha256(toolbox_pin.before(text).encode()).hexdigest()[:20]


def run_cases(cases, directory, run):
    """[(id, status, rule, sha of the whole answer, sha with the repair blanked, repair kind)] for each case that is a refusal of the rules `where.*` or `query.*` (plus the
    ones the other rules of the same invocations give, which are kept too: they must not move either)."""
    out = []
    for kind, argv in cases:
        args = [a.replace("{DIR}", str(directory)) for a in argv]
        r = run(*args)
        text = normalise(r.stdout, r.stderr, directory, r.argv[0])
        rule = None
        repair = None
        try:
            doc = json.loads(r.stdout.decode("utf-8").splitlines()[0])
            if not doc.get("ok"):
                rule = doc["error"]["rule"]
                repair = (doc["error"]["repair"] or {"kind": None})["kind"]
        except (ValueError, IndexError, KeyError):
            pass
        if rule is None and r.status != 0:
            rule = "csv:" + (r.stderr.decode("utf-8", "replace").split(":")[1].strip() if b":" in r.stderr else "?")
        out.append((case_id(kind, argv), r.status, rule, digest(text), digest(blank_repair(text)), repair))
    return out


def refusals(run, directory):
    """{case id: [status, rule, sha of the answer, sha with the repair blanked, repair kind]} for the cases whose answer is a refusal (an answer is not part of the golden: a soup
    that happens to be a valid expression is dropped, and if one becomes a refusal or the reverse the ids no longer match)."""
    cases = golden_cases()
    rows = run_cases(cases, directory, run)
    return {row[0]: [row[1], row[2], row[3], row[4], row[5]] for row in rows if row[1] != 0}


def write_golden(run, directory):
    seen = refusals(run, directory)
    GOLDEN.write_text(json.dumps({"binary": "main", "count": len(seen), "cases": seen}, sort_keys=True, separators=(",", ":")) + "\n")
    rules = {}
    for v in seen.values():
        rules[v[1]] = rules.get(v[1], 0) + 1
    print("%d refusals written to %s" % (len(seen), GOLDEN))
    for k in sorted(rules, key=str):
        print("  %-24s %d" % (k, rules[k]))


def write_main_golden(run, directory):
    """Every case of `golden_cases()` as main answers it: {case id: [status, rule or null, sha of the whole answer]}. Only ever run with the binary of main the slice starts from."""
    rows = run_cases(golden_cases(), directory, run)
    out = {row[0]: [row[1], row[2], row[3]] for row in rows}
    MAIN_GOLDEN.write_text(json.dumps({"binary": "main 62d60ad", "count": len(out), "cases": out}, sort_keys=True, separators=(",", ":")) + "\n")
    print("%d answers written to %s (%d refusals)" % (len(out), MAIN_GOLDEN, sum(1 for v in out.values() if v[0] != 0)))


if __name__ == "__main__":
    if "--write-main-golden" in sys.argv:
        import harness
        sc = harness.Scratch()
        sc.write("t.csv", DATA)
        write_main_golden(harness.run, sc.dir)
        sc.cleanup()
        sys.exit(0)
    if "--write-golden" not in sys.argv:
        print(__doc__)
        sys.exit(2)
    import harness
    sc = harness.Scratch()
    sc.write("t.csv", DATA)
    write_golden(harness.run, sc.dir)
    sc.cleanup()
