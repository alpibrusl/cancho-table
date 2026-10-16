"""The reference front end of `--query` (docs/query.md): the grammar of section 2 made executable, in Python, independent of the cancho parser.

It is the oracle of tests/conformance/test_query.py: for any query it says whether `table --query` must accept it, and if so the flags it translates to
(`translate`), and if not the refusal (`QueryError`: kind, byte offset, what was expected or the feature). It shares no code with `tools/table/qparse.cho`;
two parsers that disagree are the alarm. It was the spike `scripts/spikes/query_proto.py` of the design, kept in step with the decisions recorded in
docs/query.md section 14 (the build's readings of what the design left open).

A query is TRANSLATED to the argv of flags the tool already has (`--select`, `--where`, `--group`, `--agg`, `--sort`, `--order-by`, `--limit`, `--from`,
`--rows`, and the source), never to a second plan. Offsets are bytes of the query: the text is handled as Latin-1 so that one character is one byte.

    python3 query_ref.py 'select a, count(*) from "x.csv" group by a'      prints the translation as JSON
"""
import json
import re
import sys

MAX_QUERY = 4096
MAX_CONDITIONS = 64
MAX_VALUES = 1024
MAX_DIGITS = 18

# The one table the lexer and the documentation read (in the tool: a static string of the module).
CLAUSE_WORDS = ["select", "from", "where", "group", "order", "limit", "offset", "by"]
OPERATOR_WORDS = ["and", "in", "contains", "asc", "desc", "as", "cast"]
RESERVED = set(CLAUSE_WORDS + OPERATOR_WORDS)     # a bare identifier may not be one of these: write it "like this"
# Words of the grammar's future (joins, having, patterns, null, case, set operations): refused as bare identifiers NOW, so that a later
# version can use them without breaking a query that is valid today.
FUTURE = {"join", "inner", "left", "right", "full", "cross", "on", "using", "having", "union", "intersect", "except", "like", "ilike", "between",
          "is", "not", "or", "case", "when", "then", "else", "end", "null", "true", "false", "distinct", "over", "with", "all", "any", "exists"}
AGG_FUNCS = ["count", "sum", "min", "max", "mean"]  # reserved only before "("; `count(distinct x)` has the word distinct inside
TYPES = ["int", "dec", "float"]                     # only after `as` or `::`
SOURCE_WORDS = ["stdin"]                            # `from stdin`; a file is `from 'name'`

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
DIGITS = re.compile(r"[0-9]+")

WHAT_COLUMN = "a column"
WHAT_ITEM = "a column, *, or an aggregate: count(*), sum(x), min(x), max(x), mean(x), count(distinct x)"
WHAT_KEYWORD = ' (a name that is a keyword is written "like this")'
WHAT_CEILING = "fewer conditions or values: the ceiling is 64 conditions and 1024 values"
WHAT_DEPTH = "fewer nested parentheses: the ceiling is 8"
MAX_DEPTH = 8


class QueryError(Exception):
    """kind: syntax | unsupported | group-mismatch | too-long | mixed (and with or at one level, at the connective). offset: byte offset in the query."""

    def __init__(self, kind, offset, expected=None, feature=None, hint=None):
        super().__init__("%s at %s: %s" % (kind, offset, expected or feature))
        self.kind, self.offset, self.expected, self.feature, self.hint = kind, offset, expected, feature, hint


# ---- lexer -------------------------------------------------------------------------------------------------------------------

def lex(q):
    if len(q) > MAX_QUERY:
        raise QueryError("too-long", MAX_QUERY, expected="a query of at most %d bytes" % MAX_QUERY)
    toks, i, n = [], 0, len(q)
    while i < n:
        c = q[i]
        if c in " \t\r\n":
            i += 1
        elif q.startswith("--", i) or q.startswith("/*", i):
            raise QueryError("syntax", i, expected="no comments: a query is one statement of words, names and values")
        elif c == "'":
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise QueryError("syntax", i, expected="the closing ' of a string")
                if q[j] == "'":
                    if j + 1 < n and q[j + 1] == "'":
                        out.append("'")
                        j += 2
                        continue
                    break
                out.append(q[j])
                j += 1
            toks.append(("STR", "".join(out), i))
            i = j + 1
        elif c == '"':
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise QueryError("syntax", i, expected='the closing " of a name')
                if q[j] == '"':
                    if j + 1 < n and q[j + 1] == '"':
                        out.append('"')
                        j += 2
                        continue
                    break
                out.append(q[j])
                j += 1
            if not out:
                raise QueryError("unsupported", i, feature="empty-name", hint="a column with an empty name is written by its position, #N")
            toks.append(("QID", "".join(out), i))
            i = j + 1
        elif c == "#":
            m = DIGITS.match(q, i + 1)
            if not m:
                raise QueryError("syntax", i, expected="digits after #, a column position")
            toks.append(("POS", m.group(), i))
            i = m.end()
        elif c in "0123456789" or (c in "+-." and NUMBER.match(q, i)):
            m = NUMBER.match(q, i)
            toks.append(("NUM", m.group(), i))
            i = m.end()
        elif IDENT.match(q, i):
            m = IDENT.match(q, i)
            toks.append(("ID", m.group(), i))
            i = m.end()
        elif q.startswith("!=", i) or q.startswith("<>", i) or q.startswith("<=", i) or q.startswith(">=", i) or q.startswith("::", i):
            toks.append(("SYM", q[i:i + 2], i))
            i += 2
        elif c in ",()*=<>;":
            toks.append(("SYM", c, i))
            i += 1
        elif c in "|+/%-":
            raise QueryError("unsupported", i, feature="expression", hint="no arithmetic or concatenation: a column is a column")
        else:
            raise QueryError("syntax", i, expected="a column, a keyword, a string or a number")
    toks.append(("END", None, n))
    return toks


# ---- parser ------------------------------------------------------------------------------------------------------------------

class P:
    def __init__(self, q):
        self.q, self.t, self.k = q, lex(q), 0
        self.mix = None
        self.conds, self.wh = [], []

    def peek(self, d=0):
        return self.t[min(self.k + d, len(self.t) - 1)]

    def word(self, d=0):
        t = self.peek(d)
        return t[1].lower() if t[0] == "ID" else None

    def take(self):
        t = self.t[self.k]
        self.k += 1
        return t

    def eat_word(self, w):
        if self.word() == w:
            self.k += 1
            return True
        return False

    def expect_word(self, w, what=None):
        if not self.eat_word(w):
            raise QueryError("syntax", self.peek()[2], expected=what or w)

    def eat_sym(self, s):
        t = self.peek()
        if t[0] == "SYM" and t[1] == s:
            self.k += 1
            return True
        return False

    def expect_sym(self, s, what=None):
        if not self.eat_sym(s):
            raise QueryError("syntax", self.peek()[2], expected=what or s)

    # column := IDENT | "QID" | #N      (an IDENT may not be a reserved word)
    def column(self, what=WHAT_COLUMN):
        t = self.peek()
        if t[0] == "ID":
            if t[1].lower() in RESERVED or t[1].lower() in FUTURE:
                raise QueryError("syntax", t[2], expected=what + WHAT_KEYWORD)
            self.k += 1
            return ("name", t[1])
        if t[0] == "QID":
            self.k += 1
            return ("name", t[1])
        if t[0] == "POS":
            self.k += 1
            return ("pos", t[1])
        raise QueryError("syntax", t[2], expected=what)

    def typ(self):
        w = self.word()
        if w == "int" or w == "float":
            self.k += 1
            return (w, None)
        if w == "dec":
            self.k += 1
            self.expect_sym("(", "( and the scale of dec(N)")
            t = self.take()
            if t[0] != "NUM" or not DIGITS.fullmatch(t[1]) or len(t[1]) > 3 or int(t[1]) > 18:
                raise QueryError("syntax", t[2], expected="a scale from 0 to 18")
            self.expect_sym(")", ")")
            return ("dec", int(t[1]))
        raise QueryError("syntax", self.peek()[2], expected="a type: int, dec(N) or float")

    # expr := column [ "::" type ] | cast ( column as type )
    def expr(self, what=WHAT_COLUMN):
        if self.word() == "cast" and self.peek(1)[:2] == ("SYM", "("):
            self.k += 2
            c = self.column()
            self.expect_word("as", "as and a type")
            ty = self.typ()
            self.expect_sym(")", ")")
            return (c, ty)
        c = self.column(what)
        if self.eat_sym("::"):
            return (c, self.typ())
        return (c, None)

    def number(self, what="a non-negative integer", limit=None):
        t = self.take()
        if t[0] != "NUM" or not DIGITS.fullmatch(t[1]):
            raise QueryError("syntax", t[2], expected=what)
        if limit is not None and (len(t[1]) > 3 or int(t[1]) > limit):
            raise QueryError("syntax", t[2], expected=what)
        if len(t[1]) > MAX_DIGITS:
            raise QueryError("syntax", t[2], expected="a non-negative integer of at most 18 digits")
        return int(t[1])

    def literal(self):
        t = self.peek()
        if t[0] == "STR":
            self.k += 1
            return ("str", t[1], t[2])
        if t[0] == "NUM":
            self.k += 1
            return ("num", t[1], t[2])
        if t[0] == "ID" and t[1].lower() in ("null", "true", "false"):
            raise QueryError("unsupported", t[2], feature="null" if t[1].lower() == "null" else "boolean",
                             hint="there is no null (an empty cell is the empty text: = '') and no boolean type")
        raise QueryError("syntax", t[2], expected="a value: a number or a 'quoted string'")

    def agg_call(self):
        """count(*) | count(distinct expr) | sum|min|max(expr) | mean(expr [, N]); the current token is the function word."""
        fn = self.word()
        at = self.peek()[2]
        self.k += 2        # the word and (
        if fn == "count":
            if self.eat_sym("*"):
                self.expect_sym(")", ")")
                return {"fn": "count", "at": at}
            if self.eat_word("distinct"):
                e = self.expr()
                self.expect_sym(")", ")")
                return {"fn": "distinct", "arg": e, "at": at}
            raise QueryError("unsupported", self.peek()[2], feature="count-column",
                             hint="count(*) counts rows and count(distinct col) counts values; count(col) would count the empty cells too, so it is not offered")
        e = self.expr()
        scale = None
        if fn == "mean" and self.eat_sym(","):
            scale = self.number("a scale from 0 to 18", 18)
        self.expect_sym(")", ")" if fn != "mean" else ", N or )")
        return {"fn": fn, "arg": e, "scale": scale, "at": at}

    def is_call(self):
        return self.word() in AGG_FUNCS and self.peek(1)[:2] == ("SYM", "(")

    def items(self):
        if self.eat_sym("*"):
            return [("star",)]
        out = []
        while True:
            if self.is_call():
                out.append(("agg", self.agg_call()))
            else:
                at = self.peek()[2]
                e = self.expr(WHAT_ITEM)
                if e[1] is not None:
                    raise QueryError("unsupported", at, feature="cast-in-select",
                                     hint="a type is part of how a column is read in a condition, a sort or an aggregate; selecting a column returns its text")
                out.append(("col", e[0], at))
            if self.word() == "as":
                raise QueryError("unsupported", self.peek()[2], feature="alias", hint="a column keeps its header name and an aggregate its name (sum:bytes); there is no renaming")
            if not self.eat_sym(","):
                return out

    def leaf(self, at):
        """One condition, appended to self.conds (the leaves) and to self.items (the where clause in order)."""
        e = self.expr(WHAT_COLUMN)
        w = self.word()
        t = self.peek()
        if t[0] == "SYM" and t[1] in ("=", "!=", "<>", "<", "<=", ">", ">="):
            self.k += 1
            op = "!=" if t[1] == "<>" else t[1]
            nxt = self.peek()
            if nxt[0] in ("QID", "POS") or (nxt[0] == "ID" and nxt[1].lower() not in ("null", "true", "false")):
                raise QueryError("unsupported", nxt[2], feature="column-comparison", hint="a condition compares a column with a value, not with another column")
            self.conds.append({"col": e, "op": op, "vals": [self.literal()], "at": at})
        elif w == "contains":
            self.k += 1
            if e[1] is not None:
                raise QueryError("syntax", t[2], expected="a typed column is compared with = != < <= > >= or in, not contains")
            self.conds.append({"col": e, "op": "contains", "vals": [self.literal()], "at": at})
        elif w == "in":
            self.k += 1
            self.expect_sym("(", "( and a list of values")
            vals = [self.literal()]
            while self.eat_sym(","):
                if len(vals) >= MAX_VALUES:
                    raise QueryError("syntax", self.peek()[2], expected=WHAT_CEILING)
                vals.append(self.literal())
            self.expect_sym(")", ", or )")
            self.conds.append({"col": e, "op": "in", "vals": vals, "at": at})
        elif w == "not":
            # COLUMN not in (...) and COLUMN not contains V (docs/or-and-columns.md section 3.4); anything else after `not` is a refusal at the word after it
            nt = self.peek(1)
            nw = nt[1].lower() if nt[0] == "ID" else None
            if nw == "in":
                self.k += 2
                self.expect_sym("(", "( and a list of values")
                vals = [self.literal()]
                while self.eat_sym(","):
                    if len(vals) >= MAX_VALUES:
                        raise QueryError("syntax", self.peek()[2], expected=WHAT_CEILING)
                    vals.append(self.literal())
                self.expect_sym(")", ", or )")
                self.conds.append({"col": e, "op": "not in", "vals": vals, "at": at})
            elif nw == "contains":
                self.k += 2
                if e[1] is not None:
                    raise QueryError("syntax", nt[2], expected="a typed column is compared with = != < <= > >= or in, not contains")
                self.conds.append({"col": e, "op": "not contains", "vals": [self.literal()], "at": at})
            elif nw in ("like", "ilike"):
                raise QueryError("unsupported", nt[2], feature="like", hint="contains 'text' finds a substring; there are no patterns")
            elif nw == "between":
                raise QueryError("unsupported", nt[2], feature="between", hint="write two conditions: x >= a and x <= b")
            else:
                raise QueryError("syntax", nt[2], expected="in or contains after not")
        elif w in ("like", "ilike", "between", "is"):
            feat = {"like": "like", "ilike": "like", "between": "between", "is": "is-null"}[w]
            hints = {"like": "contains 'text' finds a substring; there are no patterns",
                     "between": "write two conditions: x >= a and x <= b",
                     "is-null": "there is no null: an empty cell is the empty text, x = ''"}
            raise QueryError("unsupported", t[2], feature=feat, hint=hints[feat])
        else:
            raise QueryError("syntax", t[2], expected="= != <> < <= > >= contains in")
        self.wh.append(self.conds[-1])

    def unit(self, depth):
        at = self.peek()[2]
        if len(self.conds) >= MAX_CONDITIONS:
            raise QueryError("syntax", at, expected=WHAT_CEILING)
        if self.word() == "not":
            # `not UNIT` (section 3.4): `not` binds a condition, a group or another `not`. The flag form has `not (` only, so the translation writes `not (cond)` for a condition, and the parenthesis
            # it adds is a level of the ceiling of 8, like one the query wrote; `not (group)` is written as it stands.
            self.k += 1
            self.wh.append("not")
            if self.peek()[:2] == ("SYM", "("):
                self.unit(depth)
            else:
                if depth >= MAX_DEPTH:
                    raise QueryError("syntax", at, expected=WHAT_DEPTH)
                self.wh.append("(")
                self.unit(depth + 1)
                self.wh.append(")")
        elif self.peek()[:2] == ("SYM", "("):
            if depth >= MAX_DEPTH:
                raise QueryError("syntax", at, expected=WHAT_DEPTH)
            self.k += 1
            self.wh.append("(")
            self.level(depth + 1)
            self.expect_sym(")", ")")
            self.wh.append(")")
        else:
            self.leaf(at)

    def level(self, depth):
        kind = None
        while True:
            self.unit(depth)
            here = self.peek()[2]
            c = self.word()
            if c not in ("and", "or"):
                return
            self.k += 1
            if kind is None:
                kind = c
            elif kind != c and self.mix is None:
                self.mix = here
            self.wh.append(c)

    def cond(self):
        self.conds, self.wh = [], []
        self.level(0)
        return self.conds

    def parse(self):
        try:
            q = self.statement()
        except QueryError:
            if self.mix is not None:
                raise QueryError("mixed", self.mix)
            raise
        if self.mix is not None:
            raise QueryError("mixed", self.mix)
        q["items"] = q["items"]
        q["where"] = self.wh
        return q

    def statement(self):
        self.expect_word("select", "select")
        items_at = self.peek()[2]
        items = self.items()
        items_end = self.peek()[2]
        q = {"items": items, "items_at": items_at, "items_end": items_end, "group_end": None, "source": None, "conds": [], "group": [], "group_at": None, "order": [], "limit": None, "offset": None}
        if self.eat_word("from"):
            t = self.take()
            if t[0] == "STR":
                q["source"] = ("file", t[1])
            elif t[0] == "ID" and t[1].lower() == "stdin":
                q["source"] = ("stdin", None)
            elif t[0] == "ID" and t[1].lower() in ("select", "join", "left", "inner", "cross"):
                raise QueryError("unsupported", t[2], feature="subquery" if t[1].lower() == "select" else "join", hint="one input; there are no joins yet")
            else:
                raise QueryError("syntax", t[2], expected="a 'file name' in quotes, or stdin")
            if self.word() in ("join", "left", "inner", "cross", "full"):
                raise QueryError("unsupported", self.peek()[2], feature="join", hint="one input; there are no joins yet")
        if self.eat_word("where"):
            q["conds"] = self.cond()
        if self.eat_word("group"):
            self.expect_word("by", "by")
            q["group_at"] = self.peek()[2]
            while True:
                at = self.peek()[2]
                e = self.expr()
                if e[1] is not None:
                    raise QueryError("unsupported", at, feature="cast-in-group",
                                     hint="the grammar groups by the text of a column; a grouping by value is the flag --group x:int, x:dec(N) or x:float")
                q["group"].append(e[0])
                if not self.eat_sym(","):
                    break
            q["group_end"] = self.peek()[2]
        if self.word() == "having":
            raise QueryError("unsupported", self.peek()[2], feature="having", hint="there is no having; filter the groups' rows with where, or order and limit the groups")
        if self.eat_word("order"):
            self.expect_word("by", "by")
            while True:
                at = self.peek()[2]
                if self.is_call():
                    k = ("agg", self.agg_call())
                else:
                    k = ("col", self.expr())
                d = False
                if self.eat_word("desc"):
                    d = True
                else:
                    self.eat_word("asc")
                q["order"].append((k, d, at))
                if not self.eat_sym(","):
                    break
        if self.eat_word("limit"):
            q["limit"] = self.number()
            if self.eat_word("offset"):
                q["offset"] = self.number()
        elif self.eat_word("offset"):
            q["offset"] = self.number()
        self.eat_sym(";")
        t = self.peek()
        if t[0] != "END":
            if t[0] == "ID" and t[1].lower() in ("union", "intersect", "except"):
                raise QueryError("unsupported", t[2], feature="union", hint="one statement")
            raise QueryError("syntax", t[2], expected="the end of the query (one statement)")
        return q


# ---- translation to the flags the tool has ---------------------------------------------------------------------------------

def list_name(c):
    """a column as a word of a --select / --group list: a backslash before a comma, a backslash or a leading #."""
    if c[0] == "pos":
        return "#%s" % c[1]
    s = c[1].replace("\\", "\\\\").replace(",", "\\,")
    return "\\" + s if s.startswith("#") else s


def order_name(c):
    """a column in an --order-by key: a backslash before a comma, a backslash, a colon, and a leading # or - (an at-sign is NOT escapable here)."""
    if c[0] == "pos":
        return "#%s" % c[1]
    s = c[1].replace("\\", "\\\\").replace(",", "\\,").replace(":", "\\:")
    return "\\" + s if s[:1] in ("#", "-") else s


def agg_name(c):
    """a column inside an --agg item: as a list name, and a colon or an at-sign (a type or a scale suffix) is escaped too."""
    s = list_name(c)
    return s.replace(":", "\\:").replace("@", "\\@")


def type_suffix(ty):
    if ty is None:
        return ""
    return {"int": ":int", "float": ":float"}.get(ty[0]) or ":dec(%d)" % ty[1]


def where_word(c):
    """a column in --where: bare when it is a plain identifier that is not one of the grammar's three words, else 'quoted' (never a position or a keyword)."""
    if c[0] == "pos":
        return "#%s" % c[1]
    if IDENT.fullmatch(c[1]) and c[1] not in ("and", "in", "contains"):
        return c[1]
    return "'" + c[1].replace("'", "''") + "'"


def where_value(v):
    return v[1] if v[0] == "num" else "'" + v[1].replace("'", "''") + "'"


def agg_label(a):
    """the output column an aggregate has (and what --sort names)."""
    if a["fn"] == "count":
        return "count"
    return "%s:%s" % (a["fn"], a["arg"][0][1] if a["arg"][0][0] == "name" else "#" + a["arg"][0][1])


def agg_item(a):
    if a["fn"] == "count":
        return "count"
    col, ty = a["arg"]
    s = "%s:%s%s" % (a["fn"], agg_name(col), type_suffix(ty))
    if a["fn"] == "mean" and a.get("scale") is not None:
        s += "@%d" % a["scale"]
    return s


def same_agg(a, b):
    return a["fn"] == b["fn"] and a.get("arg") == b.get("arg") and a.get("scale") == b.get("scale")


def translate(q):
    """Return (argv, source): argv the flags, source ('file', name) or ('stdin', None) or None. `q` is str (Latin-1 view of the bytes) or bytes."""
    if isinstance(q, bytes):
        q = q.decode("latin-1")
    ast = P(q).parse()
    try:
        return _translate(ast)
    except QueryError as e:
        if e.kind == "group-mismatch":
            e.select = q[ast["items_at"]:ast["items_end"]].strip()
            e.group_by = q[ast["group_at"]:ast["group_end"]].strip() if ast["group_at"] is not None else ""
        raise


def translate_ignoring_mix(q):
    """The flags of a query that is whole but for `and` mixed with `or` at one level (the clause is written as it stands): what the repair of `query.mixed-and-or` starts from."""
    if isinstance(q, bytes):
        q = q.decode("latin-1")
    p = P(q)
    ast = p.statement()
    ast["where"] = p.wh
    return _translate(ast)


def _translate(ast):
    items, argv = ast["items"], []
    aggs = [i[1] for i in items if i[0] == "agg"]
    cols = [i for i in items if i[0] == "col"]
    star = any(i[0] == "star" for i in items)
    grouped = bool(aggs) or bool(ast["group"])
    if grouped:
        gnames = ast["group"]
        # the select list is the group columns, in the group by order, then the aggregates (what the output is)
        if star:
            raise QueryError("group-mismatch", ast["items_at"], expected="the group columns then the aggregates, not *")
        order_ok = [i[0] for i in items] == ["col"] * len(cols) + ["agg"] * len(aggs)
        if not order_ok or [c[1] for c in cols] != gnames:
            raise QueryError("group-mismatch", cols[0][2] if cols else ast["items_at"],
                             expected="the select list is the group by columns in the same order, then the aggregates")
        if not aggs:
            raise QueryError("group-mismatch", ast["items_at"], expected="at least one aggregate, count(*) at least (a grouping always counts something)")
        if gnames:
            argv += ["--group", ",".join(list_name(c) for c in gnames)]
        argv += ["--agg", ",".join(agg_item(a) for a in aggs)]
    else:
        if not star:
            argv += ["--select", ",".join(list_name(c[1]) for c in cols)]
    if ast["conds"]:
        text = ""
        for c in ast["where"]:
            if c in ("(", ")"):
                text += c
            elif c == "not":
                text += "not "
            elif isinstance(c, str):
                text += " %s " % c
            else:
                col, ty = c["col"]
                w = where_word(col) + type_suffix(ty)
                if c["op"] in ("in", "not in"):
                    text += w + " %s (" % c["op"] + ", ".join(where_value(v) for v in c["vals"]) + ")"
                else:
                    text += "%s %s %s" % (w, c["op"], where_value(c["vals"][0]))
        argv += ["--where", text]
    if ast["order"]:
        if grouped:
            if len(ast["order"]) != 1:
                raise QueryError("unsupported", ast["order"][1][2], feature="order-by-two-keys", hint="a grouping is ordered by one output column")
            (kind, k), desc, at = ast["order"][0][0], ast["order"][0][1], ast["order"][0][2]
            if kind == "agg":
                if not any(same_agg(k, a) for a in aggs):
                    raise QueryError("group-mismatch", at, expected="an aggregate that is in the select list")
                if k.get("arg") and k["arg"][0][0] == "pos":
                    raise QueryError("unsupported", at, feature="order-by-position", hint="--sort names output columns by their header name: name the column instead of its position")
                key = agg_label(k)
            else:
                if k[1] is not None or k[0] not in ast["group"]:
                    raise QueryError("group-mismatch", at, expected="a group by column or an aggregate of the select list")
                if k[0][0] == "pos":
                    raise QueryError("unsupported", at, feature="order-by-position", hint="--sort names output columns by their header name: name the column instead of its position")
                key = k[0][1]
            if key.startswith("-"):
                raise QueryError("unsupported", at, feature="order-by-dash-name", hint="--sort has no escape for a name that starts with -")
            argv += ["--sort", ("-" if desc else "") + key]
        else:
            keys = []
            for (kind, k), desc, at in ast["order"]:
                if kind == "agg":
                    raise QueryError("group-mismatch", at, expected="a column (an aggregate needs a group by or an aggregate select list)")
                col, ty = k
                keys.append(("-" if desc else "") + order_name(col) + type_suffix(ty))
            argv += ["--order-by", ",".join(keys)]
    if ast["limit"] is not None:
        argv += ["--limit", str(ast["limit"])]
    if ast["offset"] is not None:
        argv += ["--from", str(ast["offset"])]
    if star and not ast["conds"] and not ast["order"] and not grouped:
        argv = ["--rows"] + argv        # `select *` alone has no other flag form
    return argv, ast["source"]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            argv, src = translate(sys.argv[1])
            print(json.dumps({"argv": argv, "source": src}))
        except QueryError as e:
            print(json.dumps({"error": e.kind, "offset": e.offset, "expected": e.expected, "feature": e.feature, "hint": e.hint}))
            sys.exit(2)
