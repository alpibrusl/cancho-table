"""An independent oracle for `--input-format jsonl` (docs/readers.md section 12), and the generators that feed it.

The oracle is written from the specification and nothing else, on Python's `json` module: it never parses JSON itself. What Python gets wrong for this
tool is repaired around it: constants (`NaN`...) are refused, numbers keep their token text (never a float), keys are kept as a list of pairs so that a
duplicate can be seen, a lone surrogate escape (Python accepts it) is refused, depth is 128, nodes are counted.

    fields_parse(text)                  -> ("ok", items, names, segments) | ("input.bad-fields", item, reason)
    decode(data, fields, absent, ...)   -> ("ok", names, rows, meta) | ("refuse", rule, detail, rows_before)
    discover(data, n_lines, max_fields) -> ("ok", {"fields": ..., ...}) | ("refuse", rule, detail, [])

`detail` of a refusal uses the member names of 12.5 (`line`, `row`, `column`, `reason`, `kind`, `key`, `limit`); `limit.line-too-long` has `first_line`,
`longest`, `limit` (as the CSV reader's). Offsets of `parse.jsonl-syntax` are not computed (N7); `detail["code"]` is a hint when the Python message makes it
certain (`utf8 depth control trailing escape end`) and None otherwise. `detail["ambiguous"]` is true when the line has two defects whose order is the
parser's (N12): the differential then accepts either rule. `detail["reason_any"]` lists the reasons a decision of this oracle leaves open.

The second half of the module generates inputs: `random_case`, `edge_cases`, `chunk_edge_cases`, `big_file` and `planted_case` (a record whose cells the
generator knows, to test the oracle against).
"""

import copy
import json
import random
import re

BOM = b"\xef\xbb\xbf"
MAX_DEPTH = 128
MAX_PATHS = 4096
MAX_SEGMENTS = 8
SYNTAX = "parse.jsonl-syntax"
NODES = "limit.json-too-many-nodes"
LONG = "limit.line-too-long"
NOT_OBJECT = "parse.jsonl-not-object"
MISSING = "parse.jsonl-missing-field"
NOT_SCALAR = "parse.jsonl-not-scalar"
DUPLICATE = "parse.jsonl-duplicate-key"
BAD_FIELDS = "input.bad-fields"


# ---- the parse ---------------------------------------------------------------------------------------------------------------------------------

class Tok:
    """A number as written: the text is the cell."""
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "Tok(%r)" % self.text


class Obj(list):
    """An object as the list of its (key, value) pairs, in file order, duplicates kept."""


def _constant(name):
    raise ValueError("constant " + name)


def parse_line(line, max_nodes=4096):
    """One line (bytes, BOM and CR already gone) to ("ok", value, nodes) or ("refuse", rule, {"code", "ambiguous"})."""
    def refuse(rule, code, ambiguous):
        return ("refuse", rule, {"code": code, "ambiguous": ambiguous})
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as e:
        return refuse(SYNTAX, "utf8", e.start >= max_nodes)
    try:
        value = json.loads(text, object_pairs_hook=Obj, parse_int=Tok, parse_float=Tok, parse_constant=_constant)
    except RecursionError:
        return refuse(SYNTAX, "depth", max_nodes < 1000)
    except json.JSONDecodeError as e:
        code = "control" if e.msg.startswith("Invalid control") else "trailing" if e.msg.startswith("Extra data") else "escape" if "escape" in e.msg else \
            "end" if e.msg.startswith("Unterminated") or e.pos >= len(text.rstrip(" \t\r\n")) else None
        return refuse(SYNTAX, code, e.pos >= max_nodes)
    except ValueError:
        return refuse(SYNTAX, None, len(text) > max_nodes)
    nodes, depth, surrogate = survey(value)
    if depth > MAX_DEPTH:
        return refuse(SYNTAX, "depth", nodes > max_nodes)
    if surrogate:
        return refuse(SYNTAX, "escape", nodes > max_nodes)
    if nodes > max_nodes:
        return refuse(NODES, None, False)
    return ("ok", value, nodes)


def survey(value):
    """(nodes, depth, has_lone_surrogate) of a parsed value, without recursion. A scalar is 1 node, an array 1 + its elements, an object 1 + for each pair a
    node for the key and its value's. The depth is that of the containers (the root object is 1)."""
    nodes = depth = 0
    surrogate = False
    stack = [(value, 1)]
    while stack:
        v, d = stack.pop()
        nodes += 1
        if isinstance(v, Obj):
            depth = max(depth, d)
            for k, child in v:
                nodes += 1
                surrogate = surrogate or has_surrogate(k)
                stack.append((child, d + 1))
        elif isinstance(v, list):
            depth = max(depth, d)
            stack.extend((child, d + 1) for child in v)
        elif isinstance(v, str):
            surrogate = surrogate or has_surrogate(v)
    return nodes, depth, surrogate


def has_surrogate(s):
    return any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def count_nodes(text):
    """The nodes of a JSON text (it must parse)."""
    return survey(json.loads(text, object_pairs_hook=Obj, parse_int=Tok, parse_float=Tok))[0]


def kind_of(value):
    if isinstance(value, Obj):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Tok):
        return "number"
    if value is None:
        return "null"
    return "boolean"


# ---- --fields (12.3) ---------------------------------------------------------------------------------------------------------------------------

ESCAPABLE = ",\\#."


def item_parse(item):
    """An item as written to (segments, name), or (None, reason). Segments are unescaped; the name is the item with `\\,` written as a comma."""
    segments, cur, name = [], [], []
    i = 0
    while i < len(item):
        c = item[i]
        if c == "\\":
            if i + 1 >= len(item):
                return None, "a backslash at the end"
            n = item[i + 1]
            if n not in ESCAPABLE:
                return None, "a backslash before %r" % n
            cur.append(n)
            name.append("," if n == "," else c + n)
            i += 2
            continue
        name.append(c)
        if c == ".":
            segments.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    segments.append("".join(cur))
    if "" in segments:
        return None, "an empty path or segment"
    if len(segments) > MAX_SEGMENTS:
        return None, "more than 8 segments"
    return segments, "".join(name)


def split_items(text):
    """The items of a --fields text, as written ((items, None) or (None, (item index, reason))): the first bad item, left to right."""
    items, cur, seen = [], [], set()
    i = 0
    def close():
        item = "".join(cur)
        segs, why = item_parse(item)
        if segs is None:
            return (len(items), why)
        if item in seen:
            return (len(items), "the same path twice")
        if len(items) >= MAX_PATHS:
            return (len(items), "more than 4096 paths")
        seen.add(item)
        items.append(item)
        cur.clear()
        return None
    while i < len(text):
        c = text[i]
        if c == "\\":
            if i + 1 >= len(text) or text[i + 1] not in ESCAPABLE:
                return None, (len(items), "a backslash at the end" if i + 1 >= len(text) else "a backslash before %r" % text[i + 1])
            cur.append(text[i:i + 2])
            i += 2
        elif c == ",":
            bad = close()
            if bad:
                return None, bad
            i += 1
        else:
            cur.append(c)
            i += 1
    bad = close()
    return (None, bad) if bad else (items, None)


def fields_parse(text):
    items, bad = split_items(text)
    if bad:
        return (BAD_FIELDS, bad[0], bad[1])
    parsed = [item_parse(i) for i in items]
    return ("ok", items, [n for _, n in parsed], [s for s, _ in parsed])


def escape_item(segments):
    """The written form of a path: what `--fields` takes (a dot, comma, backslash or hash in a key escaped)."""
    return ".".join("".join("\\" + c if c in ESCAPABLE else c for c in str(s)) for s in segments)


# ---- cells (12.4) ------------------------------------------------------------------------------------------------------------------------------

INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def lookup(root, segments):
    """("cell", text) | ("absent", reason, loose) | ("container", kind) | ("dup", key). Step by step: an object looks up keys (a key seen twice is a duplicate,
    wherever the second is), an array looks up indices."""
    cur = root
    for seg in segments:
        if isinstance(cur, Obj):
            hits = [v for k, v in cur if k == seg]
            if len(hits) > 1:
                return ("dup", seg)
            if not hits:
                return ("absent", "missing", None)
            cur = hits[0]
        elif isinstance(cur, list):
            if INDEX.match(seg) and len(seg) < 18 and int(seg) < len(cur):
                cur = cur[int(seg)]
            else:
                return ("absent", "missing", None)
        elif cur is None:
            return ("absent", "not-container", ["null", "not-container"])        # a step through a null: the spec names no reason (decision A1)
        else:
            return ("absent", "not-container", None)
    if cur is None:
        return ("absent", "null", None)
    if isinstance(cur, (Obj, list)):
        return ("container", kind_of(cur))
    if isinstance(cur, str):
        return ("cell", cur)
    if isinstance(cur, Tok):
        return ("cell", cur.text)
    return ("cell", "true" if cur else "false")


# ---- the reader and decode (12.2) --------------------------------------------------------------------------------------------------------------

def records(data, max_line=1 << 20, max_rows=None):
    """Yield (line, row, content_bytes) for each record; ("long", line, length) once and stop; ("more", line) once and stop when a record follows the last the
    page may hold (it is not judged: not its length, not its text). The length of a line, for the limit, is as the CSV reader counts it: every byte but the LF
    (a CR and a BOM count), blank or not."""
    pos, line, row = 0, 0, 0
    n = len(data)
    while pos < n:
        end = data.find(b"\n", pos)
        raw = data[pos:] if end < 0 else data[pos:end]
        pos = n if end < 0 else end + 1
        line += 1
        content = raw[3:] if line == 1 and raw.startswith(BOM) else raw
        if end >= 0 and content.endswith(b"\r"):
            content = content[:-1]
        blank = not content.strip(b" \t\r")
        if max_rows is not None and row >= max_rows:
            if not blank:
                yield ("more", line)
                return
            continue
        if len(raw) > max_line:
            yield ("long", line, len(raw))
            return
        if blank:
            continue
        row += 1
        yield (line, row, content)


def looks_like_document(content):
    """A line that starts with `[`, or is a lone `{` (judged without its surrounding blanks)."""
    c = content.strip(b" \t\r")
    return c.startswith(b"[") or c == b"{"


def decode(data, fields, absent="refuse", max_nodes=4096, max_line=1 << 20, max_rows=None):
    """The table `--fields` declares, read as section 12 says. `fields` are items as written. ("ok", names, rows, meta) with meta = records, line_of_row,
    truncated (a record past `max_rows` exists), lines; or ("refuse", rule, detail, rows_before). The first refusal in file order; a record past `max_rows`
    is not judged."""
    parsed = [item_parse(i) for i in fields]
    names = [n for _, n in parsed]
    rows, line_of_row, truncated = [], [], False
    for rec in records(data, max_line, max_rows):
        if rec[0] == "long":
            return ("refuse", LONG, {"first_line": rec[1], "longest": rec[2], "limit": max_line}, rows)
        if rec[0] == "more":
            truncated = True
            break
        line, row, content = rec
        where = {"line": line, "row": row}
        looks = {"looks_like": "json-document"} if row == 1 and (looks_like_document(content)) else {}
        got = parse_line(content, max_nodes)
        if got[0] == "refuse":
            detail = dict(where, **got[2])
            if got[1] == SYNTAX:
                detail.update(looks)
            else:
                detail["limit"] = max_nodes
            return ("refuse", got[1], detail, rows)
        root = got[1]
        if not isinstance(root, Obj):
            return ("refuse", NOT_OBJECT, dict(where, kind=kind_of(root), **looks), rows)
        cells = []
        for (segments, name) in parsed:
            r = lookup(root, segments)
            if r[0] == "cell":
                cells.append(r[1])
            elif r[0] == "dup":
                return ("refuse", DUPLICATE, dict(where, column=name, key=r[1]), rows)
            elif r[0] == "container":
                return ("refuse", NOT_SCALAR, dict(where, column=name, kind=r[1]), rows)
            elif absent == "empty":
                cells.append("")
            else:
                detail = dict(where, column=name, reason=r[1])
                if r[2]:
                    detail["reason_any"] = r[2]
                return ("refuse", MISSING, detail, rows)
        rows.append(cells)
        line_of_row.append(line)
    return ("ok", names, rows, {"records": len(rows), "line_of_row": line_of_row, "truncated": truncated, "lines": data.count(b"\n") + (not data.endswith(b"\n") and bool(data))})


def to_csv(names, rows):
    """The cells as a CSV the table reads (LF line ends)."""
    import csv
    import io
    out = io.StringIO(newline="")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(names)
    w.writerows(rows)
    return out.getvalue().encode("utf-8")


# ---- --discover (12.7) -------------------------------------------------------------------------------------------------------------------------

def discover(data, n_lines=1000, max_fields=256, max_nodes=4096, max_line=1 << 20):
    """The `data.discover` member, with `complete` a list of the values the text allows (both when the input has exactly N records: decision A8).
    ("ok", doc) or ("refuse", rule, detail, [])."""
    fields = {}                      # path -> {"kinds": {}, "first_line": n, "records": set()}
    state = {"arrays": [], "undeclarable": 0, "truncated": False}
    examined, more = 0, False

    def walk(obj, prefix, line, row):
        for key, value in obj:
            if key == "":
                state["undeclarable"] += 1
                continue
            segs = prefix + [key]
            if isinstance(value, Obj) and len(value) and len(segs) < MAX_SEGMENTS:
                walk(value, segs, line, row)
                continue
            kind = ("object" if isinstance(value, Obj) else "array" if isinstance(value, list) else "string" if isinstance(value, str) else
                    "null" if value is None else "bool" if isinstance(value, bool) else ("number" if re.search(r"[.eE]", value.text) else "int"))
            path = unhash(segs)
            f = fields.get(path)
            if f is None:
                if len(fields) >= max_fields:
                    state["truncated"] = True
                    continue
                f = fields[path] = {"kinds": {}, "first_line": line, "records": set()}
            f["kinds"][kind] = f["kinds"].get(kind, 0) + 1
            f["records"].add(row)
            if kind == "array" and path not in state["arrays"]:
                state["arrays"].append(path)

    for rec in records(data, max_line):
        if examined >= n_lines:
            more = True
            break
        if rec[0] == "long":
            return ("refuse", LONG, {"first_line": rec[1], "longest": rec[2], "limit": max_line}, [])
        line, row, content = rec
        examined += 1
        got = parse_line(content, max_nodes)
        where = {"line": line, "row": row}
        looks = {"looks_like": "json-document"} if row == 1 and (looks_like_document(content)) else {}
        if got[0] == "refuse":
            detail = dict(where, **got[2])
            if got[1] == SYNTAX:
                detail.update(looks)
            else:
                detail["limit"] = max_nodes
            return ("refuse", got[1], detail, [])
        if not isinstance(got[1], Obj):
            return ("refuse", NOT_OBJECT, dict(where, kind=kind_of(got[1]), **looks), [])
        walk(got[1], [], line, row)
    out = []
    for path, f in fields.items():
        kinds = dict(f["kinds"])
        missing = examined - len(f["records"])
        if missing:
            kinds["missing"] = missing
        out.append({"path": path, "kinds": kinds, "first_line": f["first_line"]})
    complete = [not more]            # no further non-blank line after the N examined (the implementer's reading: exactly N records and EOF is complete)
    return ("ok", {"fields": out, "lines_examined": examined, "complete": complete, "paths_truncated": state["truncated"], "undeclarable": state["undeclarable"], "arrays": state["arrays"]})


def unhash(segs):
    """A discovered path as `--fields` takes it: a dot, comma or backslash in a key escaped; a `#` is not (a key that begins with one is a name only a plan reads)."""
    return ".".join("".join("\\" + c if c in ",\\." else c for c in s) for s in segs)


# ---- generators: the writer of JSON text -------------------------------------------------------------------------------------------------------

class Raw(str):
    """Text emitted as it is (a number token, or a deliberately broken one)."""


class P(list):
    """An object as pairs [key, value], emitted in order; duplicates are possible."""


class Style:
    def __init__(self, rng, ascii_only=None, plain=False):
        self.rng = rng
        self.ascii = rng.random() < 0.3 if ascii_only is None else ascii_only
        self.slash = rng.random() < 0.3
        self.esc_p = 0.0 if plain else rng.choice([0, 0, 0.02, 0.2])
        self.upper = rng.random() < 0.3
        self.sep = rng.choice([",", ",", ", ", " , "]) if not plain else ","
        self.colon = rng.choice([":", ":", ": ", " : "]) if not plain else ":"
        self.pad = 0 if plain else rng.choice([0, 0, 0, 0.1])

    def ws(self):
        return self.rng.choice([" ", "\t", "  ", " \t"]) if self.pad and self.rng.random() < self.pad else ""


def enc_str(s, st):
    out = ['"']
    hexf = "\\u%04X" if st.upper else "\\u%04x"
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif o < 0x20:
            short = {8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r"}.get(o)
            out.append(short if short and st.rng.random() < 0.7 else hexf % o)
        elif ch == "/" and st.slash and st.rng.random() < 0.5:
            out.append("\\/")
        elif 0xD800 <= o <= 0xDFFF:
            out.append(hexf % o)
        elif o > 0x7F and (st.ascii or st.rng.random() < st.esc_p):
            if o > 0xFFFF:
                o -= 0x10000
                out.append(hexf % (0xD800 + (o >> 10)) + hexf % (0xDC00 + (o & 0x3FF)))
            else:
                out.append(hexf % o)
        elif o < 0x7F and st.esc_p and st.rng.random() < st.esc_p / 4:
            out.append(hexf % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def emit(v, st):
    if isinstance(v, Raw):
        return str(v)
    if isinstance(v, P):
        return "{" + st.ws() + (st.sep + st.ws()).join(enc_str(k, st) + st.colon + st.ws() + emit(x, st) for k, x in v) + st.ws() + "}"
    if isinstance(v, list):
        return "[" + st.ws() + (st.sep + st.ws()).join(emit(x, st) for x in v) + st.ws() + "]"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    return enc_str(v, st)


# ---- generators: values ------------------------------------------------------------------------------------------------------------------------

TEXTS = ["", "plain", "a b", 'q"z', "back\\slash", "tab\there", "nl\nx", "cr\rx", "crlf\r\nx", "é", "caf\u00e9", "中文", "😀", "€", "\u0000", "a\u0000b", "\u00a0",
         "\ufeffbom", "\U0010ffff", "\u007f", "/path/x", "12", "007", " 7", "-0", "1e3", "true", "null", "#h", "a,b", "'q'", "x" * 40, "\u0001\u001f", "\b\f", "ünï", "ß", "{}", "[1]",
         "abc\\n", "\\u0041", "q\"\\\"", "  ", "A", "b", "c"]
INT_TOKENS = ["0", "1", "2", "5", "42", "-1", "-7", "250", "1000", "99999", "-0", "9223372036854775807", "-9223372036854775808", "9223372036854775808", "-9223372036854775809",
              "1" + "0" * 39, "123456789012345678901234567890"]
DEC_TOKENS = ["0.5", "1.50", "2.5", "10.25", "-3.14", "0.1", "12", "7.0", "-0.0", "100.00", "0.12345678901234567", "12345678901234567.0"]
FLOAT_TOKENS = ["1E3", "1e-7", "1.5E+10", "2e2", "1e999", "0e0", "-1.5e-3", "3.14159265358979323846", "1E+2", "5e-324", "1.7976931348623157e308"]


def leaf_value(profile, rng):
    """A scalar for a leaf of the template. Mostly the profile's kind, sometimes another."""
    r = rng.random()
    if profile == "int":
        if r < 0.78:
            return Raw(rng.choice(INT_TOKENS[:12]))
        profile = rng.choice(["text", "dec", "bool", "float", "bigint"])
    if profile == "dec" and r < 0.8:
        return Raw(rng.choice(DEC_TOKENS + INT_TOKENS[:6]))
    if profile == "bigint":
        return Raw(rng.choice(INT_TOKENS[10:]))
    if profile == "float":
        return Raw(rng.choice(FLOAT_TOKENS))
    if profile == "bool":
        return rng.choice([True, False, "true", "false"])
    if profile == "dec":
        profile = "text"
    if profile == "text" and r < 0.1:
        return Raw(rng.choice(INT_TOKENS + DEC_TOKENS + FLOAT_TOKENS))
    if profile == "text" and r < 0.14:
        return rng.choice([True, False])
    return rng.choice(TEXTS)


# (path as segments (int = array index), profile)
LEAVES = [(["id"], "int"), (["name"], "text"), (["qty"], "int"), (["price"], "dec"), (["ok"], "bool"), (["note"], "text"), (["v"], "float"),
          (["user", "name"], "text"), (["user", "age"], "int"), (["user", "addr", "zip"], "text"), (["user", "addr", "city"], "text"),
          (["items", 0, "sku"], "text"), (["items", 1, "qty"], "int"), (["tags", 0], "text"), (["tags", 1], "text"),
          (["a.b"], "text"), (["k,x"], "int"), (["x\\y"], "text"), (["#h"], "text"), (["01"], "int"), (["0"], "int"), (["ünï"], "text"), (["sp ace"], "text"),
          (["a", "b", "c", "d", "e", "f", "g", "h"], "int"), (["m", "n.o"], "text"), (["emoji😀"], "text"), (["big"], "bigint")]
ABSENT_PATHS = [["user", "nick"], ["items", 5, "sku"], ["tags", 9], ["id", "x"], ["name", 0], ["nope"], ["items", "sku"], ["user", "name", "first"], ["tags", "01"]]


def item_of(segs):
    return escape_item(segs)


def build_doc(leaves, rng):
    """The full document of a record: a tree of dicts (int keys are array indices) frozen to P and list."""
    root = {}
    for segs, profile in leaves:
        node = root
        for s in segs[:-1]:
            node = node.setdefault(s, {})
        node[segs[-1]] = leaf_value(profile, rng)
    return freeze(root, rng)


def freeze(node, rng):
    if not isinstance(node, dict):
        return node
    if all(isinstance(k, int) for k in node):
        size = max(node) + 1
        return [freeze(node[i], rng) if i in node else rng.choice([1, "pad", None, True]) for i in range(size)]
    pairs = [[str(k), freeze(v, rng)] for k, v in node.items()]
    if rng.random() < 0.3:
        rng.shuffle(pairs)
    return P(pairs)


EXTRAS = [("extra", "x"), ("extra2", 5), ("junk", None), ("n", Raw("1.5e3")), ("meta", P([["a", [1, 2, P([["y", None]])]], ["b", P()]])), ("empty", P()), ("list", []), ("t", True)]


def add_extras(doc, rng):
    if not isinstance(doc, P):
        return
    for _ in range(rng.randint(1, 3)):
        k, v = rng.choice(EXTRAS)
        doc.insert(rng.randint(0, len(doc)), [k + rng.choice(["", "1", "_"]), copy.deepcopy(v)])
    for pair in doc:
        if isinstance(pair[1], P) and rng.random() < 0.3:
            pair[1].insert(rng.randint(0, len(pair[1])), ["nick", rng.choice(["z", 3, None])])


def walk_to(doc, segs):
    """(container, key_or_index) of the last step of `segs` in `doc`, or None when the path does not exist."""
    node = doc
    for i, s in enumerate(segs):
        last = i == len(segs) - 1
        if isinstance(node, P):
            idx = next((j for j, (k, _) in enumerate(node) if k == str(s)), None)
            if idx is None:
                return None
            if last:
                return node, idx
            node = node[idx][1]
        elif isinstance(node, list) and isinstance(s, int) and s < len(node):
            if last:
                return node, s
            node = node[s]
        else:
            return None
    return None


def set_at(doc, segs, value):
    got = walk_to(doc, segs)
    if got is None:
        return False
    node, idx = got
    if isinstance(node, P):
        node[idx][1] = value
    else:
        node[idx] = value
    return True


def delete_at(doc, segs):
    got = walk_to(doc, segs)
    if got is None:
        return False
    node, idx = got
    del node[idx]
    return True


def duplicate_at(doc, segs, rng):
    """Add a second pair for the key at `segs` (or at one of its prefixes): a duplicate on the declared path."""
    for cut in rng.sample(range(1, len(segs) + 1), len(segs)):
        got = walk_to(doc, segs[:cut])
        if got and isinstance(got[0], P):
            node, idx = got
            node.insert(rng.randint(0, len(node)), [node[idx][0], rng.choice([1, "dup", None, P([["z", 1]]), [1], Raw("2.50")])])
            return True
    return False


# the refusals a document-level anomaly makes (whatever `--absent`), and the ones that are only refusals with `--absent refuse`
BAD_TOKENS = ["01", "-01", "1.", ".5", "+1", "0x10", "1e", "NaN", "Infinity", "-Infinity", "1_000", "--1", "1.2.3", "tru", "True", "nul", "'s'", "\"unterminated", "1,2", "[1,", "{", "}", "٣", "1 2",
              "\"a\" \"b\"", "-", "e5", "0b1", "undefined", "\"\\x41\"", "\"\\u12\"", "\"\\ud800\"", "\"\\udc00\"", "\"\\ud83d\\u0041\"", "\"\\ude00\\ud83d\"", "\"a\x01b\"", "\"a\tb\"", "\"a\x1fb\"", "\"\x00\""]
NON_OBJECTS = ["[1,2]", "\"str\"", "12", "true", "null", "[]", "-0", "1e3", "\"\"", "[{\"id\":1}]", "false", "[[]]", "1.5", "\"{}\""]


def deep(depth):
    return Raw("[" * depth + "]" * depth)


def anomaly_doc(doc, segs_list, rng, max_nodes):
    """A document-level defect that every `--absent` refuses (kind, new document or text)."""
    kind = rng.choice(["container", "dup", "dup", "token", "token", "surrogate", "nonobject", "bigarray", "deep", "keyesc"])
    segs = rng.choice(segs_list) if segs_list else ["id"]
    if kind == "container":
        set_at(doc, segs, rng.choice([P(), [], [1], P([["z", 1]]), [P([["a", 1]])]])) or doc.append(["id", P()])
    elif kind == "dup":
        duplicate_at(doc, segs, rng) or doc.append(["id", 1]) or doc.append(["id", 2])
    elif kind == "token":
        set_at(doc, segs, Raw(rng.choice(BAD_TOKENS))) or doc.append(["zzz", Raw(rng.choice(BAD_TOKENS))])
    elif kind == "surrogate":
        set_at(doc, segs, rng.choice(["\ud800", "\udc00", "a\ud83d", "\ud83dx", "\ude00\ud83d"])) or doc.append(["s", "\ud800"])
    elif kind == "nonobject":
        return Raw(rng.choice(NON_OBJECTS))
    elif kind == "bigarray":
        doc.append(["big_array", [1] * (max_nodes + rng.choice([0, 1, 5]))])
    elif kind == "deep":
        doc.append(["deep", deep(rng.choice([128, 129, 130, 200, 1500]))])
    else:
        duplicate_at(doc, segs, rng)
    return doc


def anomaly_soft(doc, segs_list, rng):
    """A defect only `--absent refuse` refuses: a missing key, a null, a step through a scalar, an index past the end."""
    segs = rng.choice(segs_list) if segs_list else ["id"]
    kind = rng.choice(["missing", "null", "through"])
    if kind == "missing" or len(segs) == 1 and kind == "through":
        delete_at(doc, segs) or doc.clear()
    elif kind == "null":
        set_at(doc, segs, None)
    else:
        set_at(doc, segs[:-1], rng.choice(["s", 5, True, Raw("1.5")]))
    return doc


def anomaly_line(line, rng):
    """A defect of the text of a line (bytes) that is a refusal whatever it lands on, but for the ones the parser forgives by luck: the oracle judges."""
    kind = rng.choice(["truncate", "comma", "garbage", "quotes", "comment", "nul", "utf8", "ctl", "empty-key", "nolf"])
    if kind == "truncate" and len(line) > 2:
        return line[:rng.randint(1, len(line) - 1)]
    if kind == "comma":
        return line.rstrip()[:-1] + b",}" if line.rstrip().endswith(b"}") else line + b","
    if kind == "garbage":
        return line + rng.choice([b" x", b"}", b",", b"{}", b" //c", b"\x00"])
    if kind == "quotes":
        return line.replace(b'"', b"'", rng.randint(1, 4))
    if kind == "comment":
        return rng.choice([b"/*c*/", b"//c "]) + line if rng.random() < 0.5 else line.replace(b",", b",/*c*/", 1)
    if kind == "nul":
        at = rng.randint(0, len(line))
        return line[:at] + b"\x00" + line[at:]
    if kind == "utf8":
        at = rng.randint(0, len(line))
        return line[:at] + rng.choice([b"\xff", b"\xc3", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2\x82", b"\x80", b"\xfe"]) + line[at:]
    if kind == "ctl":
        at = rng.randint(0, len(line))
        return line[:at] + bytes([rng.choice([1, 8, 11, 12, 31])]) + line[at:]
    if kind == "empty-key":
        return line.replace(b"{", b'{"":1,', 1)
    return line


class Case:
    """An input and the declaration to read it by."""

    def __init__(self, name, data, fields, absent="refuse", max_nodes=4096, max_line=1 << 20):
        self.name, self.data, self.fields = name, data, fields
        self.absent, self.max_nodes, self.max_line = absent, max_nodes, max_line

    def oracle(self, max_rows=None):
        return decode(self.data, self.fields, self.absent, self.max_nodes, self.max_line, max_rows)

    def flags(self):
        """The flags that declare the read (not the plan)."""
        out = ["--input-format", "jsonl", "--fields", ",".join(self.fields)]
        if self.absent != "refuse":
            out += ["--absent", self.absent]
        if self.max_nodes != 4096:
            out += ["--max-json-nodes", self.max_nodes]
        if self.max_line != 1 << 20:
            out += ["--max-line-bytes", self.max_line]
        return out

    def __repr__(self):
        return "<Case %s fields=%r %r absent=%s nodes=%d>" % (self.name, self.fields, self.data[:160], self.absent, self.max_nodes)


def random_case(rng, name="random"):
    """A random file: records of a random template, some benign variety (extra keys, key order, escapes, CRLF, blanks, BOM, no final LF), and, in 40% of the
    files, one or two defects."""
    pool = rng.sample(LEAVES, rng.randint(1, 6))
    segs_list = [leaf[0] for leaf in pool]
    declared = [list(s) for s in segs_list]
    if rng.random() < 0.15:
        declared.append(list(rng.choice(ABSENT_PATHS)))
    rng.shuffle(declared)
    seen, fields = set(), []
    for s in declared:
        item = item_of(s)
        if item not in seen:
            seen.add(item)
            fields.append(item)
    absent = "empty" if rng.random() < 0.3 else "refuse"
    max_nodes = rng.choice([4096] * 40 + [12, 24, 40, 80, 300])
    n = rng.choice([0, 1, 1, 2, 3, 5, 8, 15, 30])
    defective = rng.random() < 0.4
    bad = set(rng.sample(range(n), min(n, rng.choice([1, 1, 2])))) if defective else set()
    st = Style(rng)
    lines = []
    for i in range(n):
        doc = build_doc(pool, rng)
        if rng.random() < 0.4:
            add_extras(doc, rng)
        text = None
        soft = rng.random() < 0.08 and absent == "empty" or i in bad and rng.random() < 0.3
        if soft:
            doc = anomaly_soft(doc, segs_list, rng)
        if i in bad and not soft:
            if rng.random() < 0.3:
                text = anomaly_line(emit(doc, st).encode("utf-8", "surrogatepass"), rng)
            else:
                doc = anomaly_doc(doc, segs_list, rng, max_nodes)
        if rng.random() < 0.05 and isinstance(doc, P):
            doc = P(doc + [["extra_dup", 1], ["extra_dup", 2]])
        if text is None:
            text = emit(doc, st).encode("utf-8", "surrogatepass")
        if st.pad == 0 and rng.random() < 0.1:
            text = rng.choice([b" ", b"\t", b"  \t"]) + text + rng.choice([b"", b" ", b"\t"])
        lines.append(text)
        if rng.random() < 0.08:
            lines.append(rng.choice([b"", b"  ", b"\t", b" \t ", b"\r"]))
    eol = rng.choice([b"\n", b"\n", b"\r\n", None])
    out = b""
    for ln in lines:
        e = eol if eol is not None else rng.choice([b"\n", b"\r\n"])
        out += ln + e
    if lines and rng.random() < 0.2:
        out = out.rstrip(b"\r\n") if rng.random() < 0.5 else out[:-1]
    if rng.random() < 0.12:
        out = BOM + out
    if rng.random() < 0.05:
        out = b"\n" * rng.randint(1, 3) + out
    return Case(name, out, fields, absent, max_nodes)


def planted_cell(v):
    if isinstance(v, Raw):
        return str(v)
    if v is True:
        return "true"
    if v is False:
        return "false"
    return v


def planted_case(rng):
    """(Case, rows): clean records whose cells the generator knows, for testing the oracle against."""
    pool = rng.sample(LEAVES, rng.randint(1, 5))
    st = Style(rng)
    fields = [item_of(s) for s, _ in pool]
    rows, lines = [], []
    for _ in range(rng.randint(1, 12)):
        doc = build_doc(pool, rng)
        add_extras(doc, rng)
        row = []
        for segs, _ in pool:
            node = doc
            for s in segs:
                node = next(v for k, v in node if k == str(s)) if isinstance(node, P) else node[s]
            row.append(planted_cell(node))
        rows.append(row)
        lines.append(emit(doc, st).encode("utf-8"))
    return Case("planted", b"\n".join(lines) + b"\n", fields), rows


def has_duplicates(data):
    """Whether any object of any line of `data` repeats a key (to leave such files out of the checks that count occurrences)."""
    for raw in data.removeprefix(BOM).split(b"\n"):
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=Obj, parse_int=Tok, parse_float=Tok, parse_constant=_constant)
        except (ValueError, RecursionError):
            continue
        stack = [value]
        while stack:
            v = stack.pop()
            if isinstance(v, Obj):
                if len({k for k, _ in v}) != len(v):
                    return True
                stack.extend(x for _, x in v)
            elif isinstance(v, list):
                stack.extend(v)
    return False


def line_of(data, row, max_line=1 << 20):
    """The physical line of the record `row` (1-based)."""
    for rec in records(data, max_line):
        if rec[0] in ("long", "more"):
            return None
        if rec[1] == row:
            return rec[0]
    return None


# ---- generators: hand-made files -----------------------------------------------------------------------------------------------------------------

def rec(i, name="n"):
    return b'{"id":%d,"name":"%s"}' % (i, name.encode())


def edge_cases():
    """Named files for each thing the specification says. Fields are `id,name` unless a case says otherwise."""
    cs = []
    F = ["id", "name"]

    def add(name, data, fields=None, **kw):
        cs.append(Case(name, data if isinstance(data, bytes) else data.encode("utf-8", "surrogatepass"), fields or F, **kw))
    r1, r2, r3 = rec(1), rec(2, "b"), rec(3, "c")
    L = b"\n"
    add("empty file", b"")
    add("a newline", b"\n")
    add("blank lines only", b"\n\n  \n\t\n\r\n \r\n")
    add("one record", r1 + L)
    add("no final newline", r1 + L + r2)
    add("crlf", r1 + b"\r\n" + r2 + b"\r\n")
    add("crlf, no final", r1 + b"\r\n" + r2 + b"\r")
    add("mixed eols", r1 + b"\r\n" + r2 + L + r3 + b"\r\n")
    add("bom", BOM + r1 + L + r2 + L)
    add("bom only", BOM)
    add("bom and blank", BOM + b"\n" + r1 + L)
    add("bom twice", BOM + BOM + r1 + L)
    add("bom on line two", r1 + L + BOM + r2 + L)
    add("blank lines between", r1 + L + L + b"  \t " + L + r2 + b"\r\n\r\n" + r3 + L + L)
    add("blank first", L + L + r1 + L)
    add("whitespace around records", b"  " + r1 + b" \t\n\t" + r2 + b"  \n")
    add("a lone CR inside a line", b'{"id":1,\r"name":"x"}\n')
    add("a form feed line", b"\x0c\n" + r1 + L)
    add("escapes", rb'{"id":1,"name":"a\nb\tc\"d\\e\/f\bg\fh\ri\u0041\u00e9 \u4e2d"}' + L)
    add("escape NUL", rb'{"id":1,"name":"a\u0000b"}' + L)
    add("surrogate pair", rb'{"id":1,"name":"\ud83d\ude00 \uD83D\uDE00"}' + L)
    add("pair, lowercase and upper", rb'{"id":1,"name":"\ud83d\ude00"}' + L + rb'{"id":2,"name":"\uD83D\uDE00"}' + L)
    add("lone high surrogate", rb'{"id":1,"name":"\ud83d"}' + L)
    add("lone low surrogate", rb'{"id":1,"name":"\ude00"}' + L)
    add("reversed pair", rb'{"id":1,"name":"\ude00\ud83d"}' + L)
    add("high then other escape", rb'{"id":1,"name":"\ud83d\u0041"}' + L)
    add("lone surrogate in an undeclared key", rb'{"id":1,"name":"x","\ud800":1}' + L)
    add("lone surrogate in an undeclared value", rb'{"id":1,"name":"x","z":"\ud800"}' + L)
    add("bad escape", rb'{"id":1,"name":"\x41"}' + L)
    add("short unicode escape", rb'{"id":1,"name":"\u12"}' + L)
    add("escaped key equals declared", rb'{"\u0069d":1,"na\u006de":"x"}' + L)
    add("escaped duplicate of a declared key", rb'{"id":1,"\u0069d":2,"name":"x"}' + L)
    add("non-ascii", '{"id":1,"name":"café 中文 😀 €"}\n')
    add("non-ascii key", '{"ünï":1,"name":"x"}\n', ["ünï", "name"])
    add("invalid utf8: ff in a value", b'{"id":1,"name":"a\xffb"}' + L)
    add("invalid utf8: overlong", b'{"id":1,"name":"\xc0\x80"}' + L)
    add("invalid utf8: encoded surrogate", b'{"id":1,"name":"\xed\xa0\x80"}' + L)
    add("invalid utf8: beyond 10ffff", b'{"id":1,"name":"\xf4\x90\x80\x80"}' + L)
    add("invalid utf8: truncated", b'{"id":1,"name":"\xe2\x82"}' + L)
    add("invalid utf8: lone continuation outside", b'{"id":1,\x80"name":"x"}' + L)
    add("invalid utf8: after the end", r1 + b"\xff" + L)
    add("invalid utf8 in an undeclared key", b'{"id":1,"name":"x","\xff":1}' + L)
    add("raw NUL in a string", b'{"id":1,"name":"a\x00b"}' + L)
    add("raw NUL outside", b'{"id":1,"name":"x"}\x00' + L)
    add("raw control byte in a string", b'{"id":1,"name":"a\x01b"}' + L)
    add("raw tab in a string", b'{"id":1,"name":"a\tb"}' + L)
    add("raw DEL in a string", b'{"id":1,"name":"a\x7fb"}' + L)
    nums = {"big ints": ["9223372036854775807", "9223372036854775808", "-9223372036854775809", "1" + "0" * 39, "123456789012345678901234567890"],
            "floats": ["0.12345678901234567", "12345678901234567.0", "3.14159265358979323846", "0.1", "1.50", "-0.0"],
            "exponents": ["1E3", "1e-7", "1.5E+10", "1e999", "0e0", "5e-324", "1E+2", "1e0"],
            "zeros": ["0", "-0", "0.0", "-0.0", "0e5", "-0e-5"]}
    for label, toks in nums.items():
        add("numbers: " + label, b"".join(b'{"id":%s,"name":"x"}\n' % t.encode() for t in toks))
    add("numbers: 1.50 in a name column", b'{"id":1,"name":1.50}\n' + b'{"id":2,"name":-0}\n' + b'{"id":3,"name":1E3}\n')
    for tok in ["01", "-01", "1.", ".5", "+1", "0x10", "1e", "NaN", "Infinity", "-Infinity", "-", "1_0", "--1", "1.2.3", "٣", "1 2", "00", "-00", "1e+", "0.", "- 1"]:
        add("bad number " + tok, b'{"id":%s,"name":"x"}\n' % tok.encode("utf-8"))
    add("literals", b'{"id":1,"name":true}\n{"id":2,"name":false}\n{"id":3,"name":null}\n', absent="empty")
    add("null refused", b'{"id":1,"name":null}\n')
    for tok in ["True", "FALSE", "nul", "tru", "None", "undefined"]:
        add("bad literal " + tok, b'{"id":1,"name":%s}\n' % tok.encode())
    # paths
    doc = b'{"a":{"b":"ab","c":{"d":"abcd"},"n":null,"e":{}},"items":[{"sku":"s0","q":1},{"sku":"s1"}],"tags":["t0","t1"],"a.b":"dotted","01":"zero-one","0":"zero",' \
          b'"k,x":"comma","x\\\\y":"back","#h":"hash","s":"str","z":null,"arr":[],"obj":{"w":1}}\n'
    for item in ["a.b", "a\\.b", "a.c.d", "a.n", "a.e", "a.x", "items.0.sku", "items.1.sku", "items.1.q", "items.2.sku", "items.01.sku", "items.sku", "items.-1.sku", "items.+1.sku",
                 "items.0.sku.x", "items.99999999999999999999.sku", "tags.0", "tags.1", "tags.2", "tags.01", "tags.1.0", "01", "0", "k\\,x", "x\\\\y", "\\#h", "#h", "s.x", "z", "z.x", "arr", "obj",
                 "arr.0", "nope", "a.b.x", "items", "a"]:
        add("path " + item, doc, [item])
        add("path %s, empty" % item, doc, [item], absent="empty")
    add("all paths of the doc", doc, ["a.b", "a\\.b", "a.c.d", "items.0.sku", "tags.1", "01", "0", "k\\,x", "x\\\\y", "\\#h"])
    add("missing and extra keys", rec(1) + L + b'{"id":2}\n' + b'{"name":"x","id":3,"extra":[1,{"a":2}]}\n')
    add("ragged key order", b'{"name":"a","id":1}\n{"id":2,"name":"b"}\n{"x":0,"name":"c","y":0,"id":3}\n')
    add("empty object", b"{}\n")
    add("empty object, absent empty", b"{}\n", absent="empty")
    # duplicates
    dupe = {"dup first step": b'{"a":{"b":1},"a":{"b":2}}\n', "dup final step": b'{"a":{"b":1,"b":2}}\n', "dup undeclared": b'{"a":{"b":1},"z":1,"z":2}\n',
            "dup in an undeclared nested object": b'{"a":{"b":1},"u":{"p":1,"p":2}}\n', "dup far apart": b'{"a":{"b":1},"x":1,"y":2,"a":{"b":1}}\n',
            "dup on the root, declared by index": b'{"items":[1],"items":[2]}\n', "dup under an array": b'{"a":{"b":1},"arr":[{"k":1,"k":2}]}\n',
            "dup with a container first": b'{"a":{"b":1},"a":3}\n', "dup of a sibling of the declared": b'{"a":{"b":1,"c":2,"c":3}}\n',
            "dup escaped": b'{"a":{"b":1},"\\u0061":{"b":2}}\n', "dup inside a skipped branch": b'{"q":{"b":1,"b":2},"a":{"b":1}}\n'}
    for label, d in dupe.items():
        add(label, d, ["a.b"])
        add(label + " (declared a.b and arr.0.k)", d, ["a.b", "arr.0.k"])
    add("dup in the second field only", b'{"a":1,"b":2,"b":3}\n', ["a", "b"])
    add("dup on both fields: the first declared", b'{"a":1,"a":3,"b":2,"b":3}\n', ["b", "a"])
    # not-objects and malformed
    for i, t in enumerate(NON_OBJECTS + ["{", "[", " {", "[{", "]", "}", "{}{}", "\"a\"x"]):
        add("line 1 %s" % t, t.encode() + L)
        add("line 3 %s" % t, r1 + L + L + t.encode() + L)
        add("row 2 line 3 %s" % t, r1 + L + t.encode() + L)
    for label, d in {"truncated": b'{"id":1,"na', "truncated after colon": b'{"id":1,"name":', "trailing comma": b'{"id":1,"name":"x",}', "array trailing comma": b'{"id":1,"name":[1,]}',
                     "trailing garbage": b'{"id":1,"name":"x"} x', "two values": b'{"id":1,"name":"x"}{"id":2}', "single quotes": b"{'id':1,'name':'x'}", "line comment": b'{"id":1,"name":"x"} // c',
                     "block comment": b'{"id":1,/*c*/"name":"x"}', "unquoted key": b'{id:1,"name":"x"}', "missing colon": b'{"id" 1,"name":"x"}', "missing comma": b'{"id":1 "name":"x"}',
                     "leading comma": b'{,"id":1}', "double comma": b'{"id":1,,"name":"x"}', "key not a string": b'{1:2}', "unterminated string": b'{"id":1,"name":"x}',
                     "unclosed": b'{"id":1,"name":"x"', "extra close": b'{"id":1,"name":"x"}}', "just a comma": b",", "colon only": b":", "empty key object": b'{"":1,"id":2,"name":"x"}'}.items():
        add(label, r1 + L + d + L + r3 + L)
        add(label + " (first)", d + L)
    add("a pretty-printed document", b'{\n  "id": 1,\n  "name": "x"\n}\n')
    add("an array of objects", b'[\n  {"id": 1, "name": "x"},\n  {"id": 2, "name": "y"}\n]\n')
    add("an array of objects on one line", b'[{"id":1,"name":"x"},{"id":2,"name":"y"}]\n')
    add("a lone brace with CR", b"{\r\n  \"id\": 1\r\n}\r\n")
    add("blank lines then a document", b"\n\n[\n1\n]\n")
    # depth
    for d in (2, 3, 127, 128, 129, 130, 255, 1000, 100000):
        add("depth %d under an undeclared key" % d, b'{"id":1,"name":"x","d":' + b"[" * (d - 1) + b"]" * (d - 1) + b"}\n")
        add("depth %d of objects" % d, b'{"id":1,"name":"x","d":' + b'{"a":' * (d - 1) + b"1" + b"}" * (d - 1) + b"}\n")
        add("depth %d, root array" % d, b"[" * d + b"]" * d + b"\n")
        add("depth %d unclosed" % d, b'{"id":1,"name":"x","d":' + b"[" * d + b"\n")
    # nodes: `{"id":1,"name":"x"}` has 5
    add("nodes exactly", r1 + L, max_nodes=5)
    add("nodes one over", r1 + L, max_nodes=4)
    add("nodes: 3 records, the second one over", r1 + L + b'{"id":2,"name":"x","e":1}\n' + r3 + L, max_nodes=5)
    add("nodes: exactly 4096", b'{"id":1,"name":"x","a":[' + b",".join([b"1"] * (4096 - 7)) + b"]}\n")
    add("nodes: 4097", b'{"id":1,"name":"x","a":[' + b",".join([b"1"] * (4097 - 7)) + b"]}\n")
    add("nodes: keys count", b'{"id":1,"name":"x","a":{"b":{"c":{}}}}\n', max_nodes=11)
    add("nodes: keys count, one under", b'{"id":1,"name":"x","a":{"b":{"c":{}}}}\n', max_nodes=10)
    add("nodes: root array over the limit", b"[" + b",".join([b"1"] * 20) + b"]\n", max_nodes=10)
    add("nodes: root scalar", b"1\n", max_nodes=1)
    add("nodes: empty object", b"{}\n", fields=["id"], absent="empty", max_nodes=1)
    # line length (the CSV reader's count: every byte but the LF)
    pad = lambda n, tail=b"": b'{"id":1,"name":"' + b"x" * (n - len(b'{"id":1,"name":""}') - len(tail)) + b'"}' + tail
    for lim in (64, 200):
        add("line of exactly %d" % lim, pad(lim) + L, max_line=lim)
        add("line of %d+1" % lim, pad(lim + 1) + L, max_line=lim)
        add("line of %d, no final LF" % lim, pad(lim), max_line=lim)
        add("line of %d+1, no final LF" % lim, pad(lim + 1), max_line=lim)
        add("second line over %d" % lim, r1 + L + pad(lim + 1) + L + r3 + L, max_line=lim)
        add("crlf line of %d" % lim, pad(lim - 1) + b"\r\n", max_line=lim)
        add("a blank line past the limit is long too", r1 + L + b" " * (lim + 1) + L, max_line=lim)
        add("long line after a bad line", r1 + L + b'{"id":' + L + pad(lim + 1) + L, max_line=lim)
        add("bad line after a long line", r1 + L + pad(lim + 1) + L + b'{"id":' + L, max_line=lim)
    add("a very long line", b'{"id":1,"name":"' + b"y" * 500000 + b'"}\n')
    add("a line past the default limit", b'{"id":1,"name":"' + b"y" * (1 << 20) + b'"}\n')
    return cs


# ---- generators: the edges of the reader's chunks ------------------------------------------------------------------------------------------------

PIPE_EDGES = (512, 1024, 2048, 4096, 32768, 65536, 131072, 196608)


def filler(total, first_id=1):
    """Exactly `total` bytes of records for the fields `id,name` (or one whitespace line when that is too few)."""
    if total <= 0:
        return b""
    head = lambda i: b'{"id":%d,"name":"' % i
    tail = b'"}\n'
    least = len(head(first_id + 999)) + len(tail)
    if total < least:
        return b" " * (total - 1) + b"\n"
    k = max(1, total // 120)
    while total // k < least:
        k -= 1
    base, extra = divmod(total, k)
    out = []
    for i in range(k):
        n = base + (1 if i < extra else 0)
        h = head(first_id + i)
        out.append(h + b"p" * (n - len(h) - len(tail)) + tail)
    data = b"".join(out)
    assert len(data) == total, (len(data), total)
    return data


STRADDLERS = {
    "unicode escape": rb'{"id":7,"name":"caf\u00e9 x"}' + b"\n",
    "surrogate pair escape": rb'{"id":7,"name":"a\ud83d\ude00b"}' + b"\n",
    "multi-byte": '{"id":7,"name":"é€😀中"}\n'.encode(),
    "crlf": b'{"id":7,"name":"x"}\r\n',
    "short escapes": rb'{"id":7,"name":"a\nb\"c\\d\/e"}' + b"\n",
    "duplicate key": b'{"id":7,"name":"a","name":"b"}\n',
    "lone surrogate": rb'{"id":7,"name":"\ud83d x"}' + b"\n",
    "truncated utf8": b'{"id":7,"name":"\xe2\x82"}\n',
    "blank": b"  \t \r\n",
    "bom mid-file": BOM + b'{"id":7,"name":"x"}\n',
    "number": b'{"id":123456789012345678901234567890,"name":1.50}\n',
    "long line": b'{"id":7,"name":"' + b"w" * 70000 + b'"}\n',
    "key with escapes": rb'{"\u0069d":7,"na\u006de":"z"}' + b"\n",
    "not an object": b"[1,2]\n",
}


def straddle_case(label, edge, inside):
    """A file in which byte `inside` of the line `label` is the first byte of the chunk that begins at offset `edge`."""
    special = STRADDLERS[label]
    start = edge - inside
    assert start >= 0
    data = filler(start) + special + b"".join(rec(100 + i, "t") + b"\n" for i in range(3))
    return Case("%s cut %d bytes in at %d" % (label, inside, edge), data, ["id", "name"])


def chunk_edge_cases(rng, per_edge=4, every_cut_at=65536, fraction=1.0):
    """Every straddler at every edge: cut at every byte for the edge of 64 KiB (a `fraction` of them) and at `per_edge` for the others."""
    out = []
    for label, special in STRADDLERS.items():
        top = min(len(special), 100)
        for edge in PIPE_EDGES:
            if len(special) > 100 and edge < 65536:
                continue
            if edge == every_cut_at:
                cuts = [c for c in range(0, top + 1) if rng.random() < fraction]
            else:
                cuts = rng.sample(range(0, top + 1), min(per_edge, top + 1))
            if len(special) > 100:
                cuts = [c for c in (0, 1, 100, 35000, 65535) if c <= edge]
            out.extend(straddle_case(label, edge, c) for c in cuts if edge - c >= 0)
    return out


# ---- generators: big files ---------------------------------------------------------------------------------------------------------------------

BIG_FIELDS = ["id", "k", "n", "name"]
BIG_DEFECTS = ["syntax", "missing", "not-scalar", "dup", "nodes", "not-object", "utf8", "null", "float"]


def big_file(rng, lines, defect=None, at=None, blanks=0.01, crlf=0.0, bom=False, escapes=0.05, name="big"):
    """`lines` records of `id,k,n,name` (id a counter; k one of seven keys; n an integer; name text, with escapes and non-ASCII at times) made fast; with `defect`
    (one of BIG_DEFECTS) placed at record `at` (random when None). Blank lines and CRLF at the given rates."""
    at = rng.randrange(lines) if defect and at is None else at
    keys = ["a", "b", "c", "d", "e", "f", "g"]
    names = ["plain", "x y", 'q\\"z', "caf\\u00e9", "\u00e9\u4e2d", "line\\nbreak", "\\ud83d\\ude00", "\U0001f600", "tab\\t", "\\u0000", "sl\\/ash"]
    out = []
    for i in range(lines):
        nm = rng.choice(names) if rng.random() < escapes else "r%d" % (i % 97)
        n = rng.randint(-1000, 100000)
        raw = ('{"id":%d,"k":"%s","n":%d,"name":"%s"}' % (i + 1, keys[i % 7], n, nm)).encode("utf-8")
        if i == at:
            i1 = i + 1
            raw = {"syntax": b'{"id":%d,"k":"a","n":1,"name":"x"' % i1, "missing": b'{"id":%d,"k":"a","name":"x"}' % i1, "null": b'{"id":%d,"k":"a","n":null,"name":"x"}' % i1,
                   "not-scalar": b'{"id":%d,"k":"a","n":[1],"name":"x"}' % i1, "dup": b'{"id":%d,"k":"a","n":1,"k":"b","name":"x"}' % i1,
                   "nodes": b'{"id":%d,"k":"a","n":1,"name":"x","z":[%s]}' % (i1, b",".join([b"1"] * 4100)), "not-object": b"[1,2]",
                   "utf8": b'{"id":%d,"k":"a","n":1,"name":"\xff"}' % i1, "float": b'{"id":%d,"k":"a","n":1.5,"name":"x"}' % i1}[defect]
        out.append(raw + (b"\r\n" if rng.random() < crlf else b"\n"))
        if rng.random() < blanks:
            out.append(rng.choice([b"\n", b"  \n", b"\r\n", b"\t\n"]))
    data = b"".join(out)
    return Case(name, BOM + data if bom else data, list(BIG_FIELDS))
