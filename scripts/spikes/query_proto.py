"""SPIKE (throwaway, docs/query.md): a prototype of the `--query` front end in Python, to find out whether the grammar of docs/query.md
is unambiguous and implementable, and to be the executable oracle of the examples. NOT the tool's parser (that is cancho, stage Q1);
nothing here is imported by the tool.

    python3 query_proto.py 'select a, count(*) from "x.csv" group by a'      prints the flag form as JSON
    python3 query_proto.py --examples                                        checks every worked example of docs/query.md

A query is TRANSLATED to the argv of flags that the tool already has (`--select`, `--where`, `--group`, `--agg`, `--sort`, `--order-by`,
`--limit`, `--from`, and the operand), never to a second plan: the existing parsers then fill the one `Query`, so the two forms cannot
differ in what they mean, only in how it was written. The gate (query_gate.py) is what checks the translation."""
import json
import re
import sys

MAX_QUERY = 4096

# The one table the lexer and the documentation read (in the tool: a static string table of the module, also printed by `introspect`).
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
KEYWORDS = sorted(RESERVED | FUTURE | set(AGG_FUNCS) | set(TYPES) | set(SOURCE_WORDS))


class QueryError(Exception):
    """kind: syntax | unsupported | group-mismatch | too-long. offset: byte offset in the query."""

    def __init__(self, kind, offset, expected=None, feature=None, hint=None, repair=None):
        super().__init__(f"{kind} at {offset}: {expected or feature}")
        self.kind, self.offset, self.expected, self.feature, self.hint, self.repair = kind, offset, expected, feature, hint, repair


# ---- lexer -------------------------------------------------------------------------------------------------------------------

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def lex(q):
    if len(q.encode()) > MAX_QUERY:
        raise QueryError("too-long", MAX_QUERY, expected=f"a query of at most {MAX_QUERY} bytes")
    toks, i, n = [], 0, len(q)
    while i < n:
        c = q[i]
        if c in " \t\r\n":
            i += 1
        elif c == "'":
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise QueryError("syntax", i, expected="the closing ' of a string")
                if q[j] == "'":
                    if j + 1 < n and q[j + 1] == "'":
                        out.append("'"); j += 2; continue
                    break
                out.append(q[j]); j += 1
            toks.append(("STR", "".join(out), i)); i = j + 1
        elif c == '"':
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise QueryError("syntax", i, expected='the closing " of a name')
                if q[j] == '"':
                    if j + 1 < n and q[j + 1] == '"':
                        out.append('"'); j += 2; continue
                    break
                out.append(q[j]); j += 1
            if not out:
                raise QueryError("unsupported", i, feature="empty-name", hint='a column with an empty name is written by its position, #N')
            toks.append(("QID", "".join(out), i)); i = j + 1
        elif c == "#":
            m = re.compile(r"[0-9]+").match(q, i + 1)
            if not m:
                raise QueryError("syntax", i, expected="digits after #, a column position")
            toks.append(("POS", int(m.group()), i)); i = m.end()
        elif c.isdigit() or (c in "+-." and NUMBER.match(q, i)) :
            m = NUMBER.match(q, i)
            if not m:
                raise QueryError("syntax", i, expected="a number")
            toks.append(("NUM", m.group(), i)); i = m.end()
        elif IDENT.match(q, i):
            m = IDENT.match(q, i)
            toks.append(("ID", m.group(), i)); i = m.end()
        elif q.startswith("!=", i) or q.startswith("<>", i) or q.startswith("<=", i) or q.startswith(">=", i) or q.startswith("::", i):
            toks.append(("SYM", q[i:i + 2], i)); i += 2
        elif c in ",()*=<>;":
            toks.append(("SYM", c, i)); i += 1
        elif c == "|" or c == "+" or c == "/" or c == "%" or c == "-":
            raise QueryError("unsupported", i, feature="expression", hint="no arithmetic or concatenation: a column is a column")
        else:
            raise QueryError("syntax", i, expected="a column, a keyword, a string or a number")
    toks.append(("END", None, n))
    return toks


# ---- parser ------------------------------------------------------------------------------------------------------------------

class P:
    def __init__(self, q):
        self.q, self.t, self.k = q, lex(q), 0

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
    def column(self, what="a column"):
        t = self.peek()
        if t[0] == "ID":
            if t[1].lower() in RESERVED or t[1].lower() in FUTURE:
                raise QueryError("syntax", t[2], expected=what + ' (a name that is a keyword is written "like this")')
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
            if t[0] != "NUM" or not re.fullmatch(r"[0-9]+", t[1]) or int(t[1]) > 18:
                raise QueryError("syntax", t[2], expected="a scale from 0 to 18")
            self.expect_sym(")", ")")
            return ("dec", int(t[1]))
        raise QueryError("syntax", self.peek()[2], expected="a type: int, dec(N) or float")

    # expr := column [ "::" type ] | cast ( column as type )
    def expr(self, what="a column"):
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

    def number(self, what="a non-negative integer"):
        t = self.take()
        if t[0] != "NUM" or not re.fullmatch(r"[0-9]+", t[1]):
            raise QueryError("syntax", t[2], expected=what)
        return int(t[1])

    def literal(self):
        t = self.peek()
        if t[0] == "STR":
            self.k += 1
            return ("str", t[1])
        if t[0] == "NUM":
            self.k += 1
            return ("num", t[1])
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
            scale = self.number("the scale N of mean(x, N), 0 to 18")
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
                e = self.expr("a column, *, or an aggregate: count(*), sum(x), min(x), max(x), mean(x), count(distinct x)")
                if e[1] is not None:
                    raise QueryError("unsupported", at, feature="cast-in-select",
                                     hint="a type is part of how a column is read in a condition, a sort or an aggregate; selecting a column returns its text")
                out.append(("col", e[0], at))
            if self.word() == "as":
                raise QueryError("unsupported", self.peek()[2], feature="alias", hint="a column keeps its header name and an aggregate its name (sum:bytes); there is no renaming")
            if not self.eat_sym(","):
                return out

    def cond(self):
        conds = []
        while True:
            at = self.peek()[2]
            e = self.expr("a column")
            w = self.word()
            t = self.peek()
            if t[0] == "SYM" and t[1] in ("=", "!=", "<>", "<", "<=", ">", ">="):
                self.k += 1
                op = "!=" if t[1] == "<>" else t[1]
                if self.peek()[0] in ("ID", "QID", "POS") and not (self.peek()[0] == "ID" and self.peek()[1].lower() in ("null", "true", "false")):
                    raise QueryError("unsupported", self.peek()[2], feature="column-comparison", hint="a condition compares a column with a value, not with another column")
                conds.append({"col": e, "op": op, "vals": [self.literal()], "at": at})
            elif w == "contains":
                self.k += 1
                if e[1] is not None:
                    raise QueryError("syntax", t[2], expected="a typed column is compared with = != < <= > >= or in, not contains")
                conds.append({"col": e, "op": "contains", "vals": [self.literal()], "at": at})
            elif w == "in":
                self.k += 1
                self.expect_sym("(", "( and a list of values")
                vals = [self.literal()]
                while self.eat_sym(","):
                    vals.append(self.literal())
                self.expect_sym(")", ", or )")
                conds.append({"col": e, "op": "in", "vals": vals, "at": at})
            elif w in ("like", "ilike", "between", "is", "not"):
                feat = {"like": "like", "ilike": "like", "between": "between", "is": "is-null", "not": "not"}[w]
                hints = {"like": "contains 'text' finds a substring; there are no patterns",
                         "between": "write two conditions: x >= a and x <= b",
                         "is-null": "there is no null: an empty cell is the empty text, x = ''",
                         "not": "write the opposite comparison: != for =, in has no negation"}
                raise QueryError("unsupported", t[2], feature=feat, hint=hints[feat])
            else:
                raise QueryError("syntax", t[2], expected="= != <> < <= > >= contains in")
            if self.eat_word("and"):
                continue
            if self.word() == "or":
                raise QueryError("unsupported", self.peek()[2], feature="or", hint="only and; a set of values of one column is col in ('a', 'b')")
            if self.peek()[:2] == ("SYM", "("):
                raise QueryError("unsupported", self.peek()[2], feature="parentheses", hint="conditions joined by and, nothing grouped")
            return conds

    def parse(self):
        self.expect_word("select", "select")
        items = self.items()
        q = {"items": items, "source": None, "conds": [], "group": [], "order": [], "limit": None, "offset": None}
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
            while True:
                at = self.peek()[2]
                e = self.expr()
                if e[1] is not None:
                    raise QueryError("unsupported", at, feature="cast-in-group", hint="grouping by a typed column is not built (docs/numbers.md); group by the text")
                q["group"].append(e[0])
                if not self.eat_sym(","):
                    break
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
            if t[0] == "ID" and t[1].lower() == "or":
                raise QueryError("unsupported", t[2], feature="or", hint="only and")
            raise QueryError("syntax", t[2], expected="the end of the query (one statement)")
        return q


# ---- translation to the flags the tool has ----------------------------------------------------------------------------------

def list_name(c):
    """a column as a word of a --select / --group list: a backslash before a comma, a backslash or a leading #."""
    if c[0] == "pos":
        return f"#{c[1]}"
    s = c[1].replace("\\", "\\\\").replace(",", "\\,")
    return "\\" + s if s.startswith("#") else s


def order_name(c):
    """a column in an --order-by key: a backslash before a comma, a backslash, a colon, and a leading # or - (an at-sign is NOT escapable here)."""
    if c[0] == "pos":
        return f"#{c[1]}"
    s = c[1].replace("\\", "\\\\").replace(",", "\\,").replace(":", "\\:")
    return "\\" + s if s[:1] in ("#", "-") else s


def agg_name(c):
    """a column inside an --agg item: as a list name, and a colon or an at-sign (a type or a scale suffix) is escaped too."""
    s = list_name(c)
    return s.replace(":", "\\:").replace("@", "\\@")


def type_suffix(ty):
    if ty is None:
        return ""
    return {"int": ":int", "float": ":float"}.get(ty[0]) or f":dec({ty[1]})"


def where_word(c):
    """a column in --where: bare when it is a plain identifier that is not one of the grammar's three words, else 'quoted' (never a position or a keyword)."""
    if c[0] == "pos":
        return f"#{c[1]}"
    if IDENT.fullmatch(c[1]) and c[1] not in ("and", "in", "contains"):
        return c[1]
    return "'" + c[1].replace("'", "''") + "'"


def where_value(v):
    return v[1] if v[0] == "num" else "'" + v[1].replace("'", "''") + "'"


def agg_label(a):
    """the output column an aggregate has (and what --sort names)."""
    if a["fn"] == "count":
        return "count"
    return f"{a['fn']}:{a['arg'][0][1] if a['arg'][0][0] == 'name' else '#' + str(a['arg'][0][1])}"


def agg_item(a):
    if a["fn"] == "count":
        return "count"
    col, ty = a["arg"]
    s = f"{a['fn']}:{agg_name(col)}{type_suffix(ty)}"
    if a["fn"] == "mean" and a.get("scale") is not None:
        s += f"@{a['scale']}"
    return s


def same_agg(a, b):
    return a["fn"] == b["fn"] and a.get("arg") == b.get("arg") and a.get("scale") == b.get("scale")


def translate(q):
    """Return (argv, source): argv the flags, source ('file', name) or ('stdin', None) or None."""
    ast = P(q).parse() if isinstance(q, str) else q
    items, argv = ast["items"], []
    aggs = [i[1] for i in items if i[0] == "agg"]
    cols = [i for i in items if i[0] == "col"]
    star = any(i[0] == "star" for i in items)
    grouped = bool(aggs) or bool(ast["group"])
    if grouped:
        gnames = ast["group"]
        # the select list is the group columns, in the group by order, then the aggregates (what the output is)
        if star:
            raise QueryError("group-mismatch", 0, expected="the group columns then the aggregates, not *")
        order_ok = [i[0] for i in items] == ["col"] * len(cols) + ["agg"] * len(aggs)
        if not order_ok or [c[1] for c in cols] != gnames:
            raise QueryError("group-mismatch", cols[0][2] if cols else 7,
                             expected="the select list is the group by columns in the same order, then the aggregates")
        if not aggs:
            raise QueryError("group-mismatch", 7, expected="at least one aggregate, count(*) at least (a grouping always counts something)")
        if gnames:
            argv += ["--group", ",".join(list_name(c) for c in gnames)]
        # --agg: omitted only for exactly [count] (the default), kept otherwise
        argv += ["--agg", ",".join(agg_item(a) for a in aggs)]
    else:
        if not star:
            argv += ["--select", ",".join(list_name(c[1]) for c in cols)]
    if ast["conds"]:
        parts = []
        for c in ast["conds"]:
            col, ty = c["col"]
            w = where_word(col) + type_suffix(ty)
            if c["op"] == "in":
                parts.append(f"{w} in (" + ", ".join(where_value(v) for v in c["vals"]) + ")")
            else:
                parts.append(f"{w} {c['op']} {where_value(c['vals'][0])}")
        argv += ["--where", " and ".join(parts)]
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
                if k[1] is not None or k[0] not in [c for c in ast["group"]]:
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
                if ty is not None and ty[0] != "int":
                    raise QueryError("unsupported", at, feature="order-by-" + ty[0], hint="only int keys are numeric (docs/sort.md); text keys are bytewise")
                keys.append(("-" if desc else "") + order_name(col) + type_suffix(ty))
            argv += ["--order-by", ",".join(keys)]
    if ast["limit"] is not None:
        argv += ["--limit", str(ast["limit"])]
    if ast["offset"] is not None:
        argv += ["--from", str(ast["offset"])]
    if star and not ast["conds"] and not ast["order"] and not grouped:
        argv = ["--rows"] + argv        # needs the new flag (docs/query.md): `select *` alone has no flag form today
    return argv, ast["source"]


EXAMPLES = []   # filled by examples.py's data in docs/query.md; kept in sync by query_examples() below

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--examples":
        try:
            argv, src = translate(sys.argv[1])
            print(json.dumps({"argv": argv, "source": src}))
        except QueryError as e:
            print(json.dumps({"error": e.kind, "offset": e.offset, "expected": e.expected, "feature": e.feature, "hint": e.hint}))
            sys.exit(2)
