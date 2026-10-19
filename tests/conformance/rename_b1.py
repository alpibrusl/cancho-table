"""Slice B1 of docs/or-and-columns.md (section 8.2): `--rename TARGET=NEW` and `select a as b`, as a reference that shares no code with the tool.

* `parse_list` reads the text of `--rename`: items `TARGET=NEW` separated by commas; a backslash escapes the next byte, which is a comma, a backslash, an equals sign or a `#`; the first
  unescaped `=` of an item splits it; a target that begins with an unescaped `#` and is followed by 1 to 9 digits is a position, any other target is a label (exactly as `--select` reads a name);
  a new label is at least one byte. It answers the items, or the first fault in the text with its byte offset and the words of the expectation (`rename.syntax`).
* `resolve` applies the items to the labels the answer would have, all against the labels as they are before any rename (`a=b,b=c` renames the column that was `a` and the one that was `b`),
  and answers the new labels or the first refusal: an unknown target, a label two columns carry, a column renamed twice.
* `default_labels` is the header of an answer without a rename, for the plans of `query_gen`; `make_renames` draws renames; `flag_text` and `alias_form` write them as `--rename` and as `as`.
* `sqlite_answer` is the SQLite oracle: the same table loaded as TEXT with `SELECT "a" AS "b"`.
"""
import csv
import io
import json
import re
import sqlite3

MAX_ITEMS = 4096
EXP_ESCAPE = "a comma, a backslash, an equals sign or a # after the backslash"
EXP_ITEM = "TARGET=NEW"
EXP_TARGET = "a target before the ="
EXP_NEW = "a new label of at least one byte after the ="
ESCAPABLE = b",\\=#"


def parse_list(text):
    """-> ("ok", items) | ("syntax", offset, expected) | ("too-many",). An item is a dict: kind ("label" | "position"), position (int or None), target (bytes), new (bytes), tspan (begin, end of the
    target as written), index (0-based)."""
    t = bytes(text)
    n = len(t)
    items = []
    i = 0
    while True:
        start = i
        target = bytearray()
        new = bytearray()
        eq = None
        unescaped_hash_first = False
        cur = target
        while i < n and t[i] != ord(","):
            c = t[i]
            if c == ord("\\"):
                if i + 1 < n and t[i + 1] in ESCAPABLE:
                    cur.append(t[i + 1])
                    i += 2
                    continue
                return ("syntax", i, EXP_ESCAPE)
            if c == ord("=") and eq is None:
                eq = i
                cur = new
                i += 1
                continue
            if c == ord("#") and eq is None and i == start:
                unescaped_hash_first = True
            cur.append(c)
            i += 1
        end = i
        if eq is None:
            return ("syntax", end, EXP_ITEM)
        if eq == start:
            return ("syntax", start, EXP_TARGET)
        if end == eq + 1:
            return ("syntax", eq + 1, EXP_NEW)
        tb = bytes(target)
        # a position: an unescaped `#` first and 1 to 9 digits (all of them written as they are, none escaped: an escape makes the rest a label)
        raw = t[start:eq]
        kind, position = "label", None
        if unescaped_hash_first and re.fullmatch(rb"#[0-9]{1,9}", raw):
            kind, position = "position", int(raw[1:])
        items.append({"kind": kind, "position": position, "target": tb, "new": bytes(new), "tspan": (start, eq), "index": len(items)})
        if i >= n:
            break
        i += 1
    if len(items) > MAX_ITEMS:
        return ("too-many",)
    return ("ok", items)


def resolve(items, labels):
    """-> ("ok", new labels) | (rule, item index, info). `labels` are bytes."""
    renamed = {}
    for it in items:
        if it["kind"] == "position":
            hits = [it["position"] - 1] if 1 <= it["position"] <= len(labels) else []
        else:
            hits = [k for k, l in enumerate(labels) if l == it["target"]]
        if not hits:
            return ("rename.unknown-target", it["index"], None)
        if len(hits) > 1:
            return ("rename.ambiguous-target", it["index"], [h + 1 for h in hits])
        if hits[0] in renamed:
            return ("rename.repeated-target", it["index"], renamed[hits[0]] + 1)
        renamed[hits[0]] = it["index"]
    out = list(labels)
    for it in items:
        k = None
        if it["kind"] == "position":
            k = it["position"] - 1
        else:
            k = [j for j, l in enumerate(labels) if l == it["target"]][0]
        out[k] = it["new"]
    return ("ok", out)


def case_variants(items, labels, index):
    """The labels that differ from the target of item `index` only in ASCII case, at most 5 distinct columns (the options of the repair)."""
    it = items[index]
    if it["kind"] == "position":
        return []
    low = lambda b: bytes(c + 32 if 65 <= c <= 90 else c for c in b)
    out = []
    for l in labels:
        if low(l) == low(it["target"]) and l != it["target"] and l not in out:
            out.append(l)
    return out[:5]


def escape_target(b):
    s = bytearray()
    for k, c in enumerate(b):
        if c in b",\\=" or (c == ord("#") and k == 0):
            s.append(ord("\\"))
        s.append(c)
    return bytes(s)


def escape_new(b, rnd=None):
    s = bytearray()
    for k, c in enumerate(b):
        if c in b",\\" or (c == ord("#") and k == 0) or (c == ord("=") and rnd is not None and rnd.random() < 0.3):
            s.append(ord("\\"))
        s.append(c)
    return bytes(s)


def repaired_text(text, items, index, label):
    """The text of --rename with the target of item `index` written as `label`."""
    a, b = items[index]["tspan"]
    return bytes(text[:a]) + escape_target(label) + bytes(text[b:])


# ---- the labels of an answer ---------------------------------------------------------------------------------------------------------

def col_name(c, names):
    return names[c[1] - 1] if c[0] == "pos" else c[1]


def default_labels(plan, names):
    """The labels (header) an answer without a rename has, for a plan of query_gen, as str."""
    if plan["mode"] == "group":
        out = [col_name(c, names) for c in plan["group"]]
        for a in plan["aggs"]:
            out.append("count" if a["fn"] == "count" else "%s:%s" % (a["fn"], col_name(a["col"], names)))
        return out
    if plan["star"] or not plan["select"]:
        return list(names)
    return [col_name(c, names) for c in plan["select"]]


POOL = ["total", "n", "a b", "x,y", "k=v", "#5", "#", "\\", "é", "count", "sum:bytes", "Q\"uote", "it's", "id", "status", "with\nnewline", " lead", "trail ", "a\\b", ",", "=", "==", "x=y,z",
        "ünï", "日本", "tab\there", "0", "-dash", "like", "select", "as", " "]


def make_renames(rnd, labels, names, by_position_p=0.5, allow_dup=True):
    """-> [(output index, new str)] for a random subset of the outputs (each at most once), new labels drawn from awkward ones, from the header names and from the other outputs'
    (so that a rename can collide with another label)."""
    out = []
    k = len(labels)
    if k == 0:
        return out
    chosen = [i for i in range(k) if rnd.random() < 0.5]
    if not chosen:
        chosen = [rnd.randrange(k)]
    rnd.shuffle(chosen)
    for i in chosen:
        r = rnd.random()
        if r < 0.45:
            new = rnd.choice(POOL)
        elif r < 0.7:
            new = rnd.choice(names)
        elif r < 0.85 and allow_dup:
            new = rnd.choice(labels)
        else:
            new = "r%d" % rnd.randrange(100)
        out.append((i, new))
    return out


def flag_text(rnd, renames, labels):
    """The text of --rename for renames [(output index, new)]: a position, or the label when it is unique among the labels (and mostly the position when it is not)."""
    parts = []
    for i, new in renames:
        lab = labels[i]
        if labels.count(lab) == 1 and rnd.random() < 0.6:
            target = escape_target(lab.encode("utf-8"))
        else:
            target = b"#%d" % (i + 1)
        parts.append(target + b"=" + escape_new(new.encode("utf-8"), rnd))
    return b",".join(parts)


def apply(labels, renames):
    out = list(labels)
    for i, new in renames:
        out[i] = new
    return out


def alias_name(new):
    """A rename as the name after `as`: bare when it is a plain identifier that is not a keyword of the grammar, else "quoted" (the tool's grammar has no empty name)."""
    import query_ref
    if query_ref.IDENT.fullmatch(new) and new.lower() not in query_ref.RESERVED and new.lower() not in query_ref.FUTURE:
        return new
    return '"' + new.replace('"', '""') + '"'


# ---- how a table answers: csv, json, jsonl ----------------------------------------------------------------------------------------------

def csv_header(stdout):
    rows = list(csv.reader(io.StringIO(stdout.decode("utf-8", "surrogateescape"), newline="")))
    return rows[0] if rows else None


def json_columns(result):
    return result.doc()["data"]["columns"]


def jsonl_keys(stdout):
    first = stdout.split(b"\n", 1)[0]
    return list(json.loads(first.decode("utf-8"), object_pairs_hook=lambda p: [k for k, _ in p])) if first else None


# ---- the SQLite oracle ------------------------------------------------------------------------------------------------------------------

def qi(name):
    return '"' + name.replace('"', '""') + '"'


def sqlite_answer(names, rows, picks, aliases, where=None, order_cols=None, group=None):
    """Load the table as TEXT columns c0..cN and answer `SELECT "ci" AS "alias" ...` : (labels, rows) with labels the names SQLite gives the result columns. `picks` are column indexes,
    `aliases` the AS name of each or None."""
    con = sqlite3.connect(":memory:")
    cols = ", ".join("c%d TEXT" % i for i in range(len(names)))
    con.execute("CREATE TABLE t (__row INTEGER, %s)" % cols)
    for k, r in enumerate(rows):
        con.execute("INSERT INTO t VALUES (%d, %s)" % (k, ", ".join("?" for _ in names)), r)
    items = []
    for c, a in zip(picks, aliases):
        items.append("c%d" % c + (" AS %s" % qi(a) if a is not None else ""))
    cur = con.execute("SELECT %s FROM t ORDER BY t.__row" % ", ".join(items))
    labels = [d[0] for d in cur.description]
    return labels, [list(r) for r in cur.fetchall()]


# ---- the cases whose reading slice B1 moved ----------------------------------------------------------------------------------------------------

def reading_of(text, module):
    try:
        module.translate(text)
        return ("ok",)
    except module.QueryError as e:
        if e.kind == "mixed":
            return ("mixed", e.offset)
        if e.kind == "too-long":
            return ("too-long",)
        return (e.kind, e.offset, e.expected if e.kind in ("syntax", "group-mismatch") else e.feature)


def b1_changed(text):
    """Whether the reference front end of slice B1 (query_ref.py) reads this query differently from the one of slice A2 (query_ref_a2.py, frozen): it has an `as`, and the tool follows B1. The goldens of
    the earlier slices hold their other cases to main's bytes, and test_rename_b1_compat.py holds these to the reference of B1."""
    import query_ref
    import query_ref_a2
    lat = text.encode("utf-8").decode("latin-1")
    return reading_of(lat, query_ref_a2) != reading_of(lat, query_ref)


def j3b_hint_moved(text):
    """Whether the query is refused today as `query.unsupported` for the feature `join` or `subquery`: slice J3b (docs/joins.md section 20) changed the hint of those two, which said there were no joins,
    and nothing else of the answer; or for `cast-in-group`, whose hint slice T2 (docs/datetime.md section 19) extended with the flag of a period (`--group ts:timestamp@day`), and nothing else. The goldens of the
    earlier slices hold every other case to main's bytes; for these they hold the status and the rule, and test_join_j3b.py (the first two) and test_datetime_t2.py (the third) hold the rest."""
    import query_ref
    lat = text.encode("utf-8").decode("latin-1")
    r = reading_of(lat, query_ref)
    return len(r) == 3 and r[0] == "unsupported" and r[2] in ("join", "subquery", "cast-in-group")
