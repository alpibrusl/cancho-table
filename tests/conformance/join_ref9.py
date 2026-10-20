"""J9 (docs/joins.md section 23, slice J9): the references of `--join-sorted`, the merge join over two files declared sorted by the key.

* `merge_ref`: a sorted-merge join written as two cursors over the two sorted tables (no dictionary, no hash), the definition the tool is held to: the probe rows in order, for each the build
  rows of its key in build file order. Because the probe is sorted, that order is KEY ORDER (bytewise for text, by value for a typed key, component by component for several pairs).
* the generators of sorted tables for every kind of key (text with empty cells, unicode, awkward cells; integers, decimals, floats and dates with several spellings of one value), with
  duplicate groups of size 1 to N on either side, and of unsorted ones (`break_order`: the first out-of-order row, its line, both keys: what `join.not-sorted` must say).
* `key_cells`, `order_info`: the cells of a key as the refusal shows them (the cell with its quotes undone, at most 64 bytes).

The cells of a table are `str` where each character is one byte (latin-1), as the other join references have them; UTF-8 text is written `"é".encode().decode("latin-1")`.
"""

import datetime
import random

import join_ref2 as R2
import join_ref3 as R3

EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
UNIT = datetime.timedelta(microseconds=1)


def utf8(text):
    """UTF-8 text as the latin-1 string whose characters are its bytes."""
    return text.encode().decode("latin-1")


def value_of(cell, kind):
    """The value the join compares: the cell's bytes (text), or the number or instant it reads as. `kind` is None, 'int', 'float', ('dec', S), 'date', 'timestamp' or 'datetime'."""
    if kind is None:
        return cell.encode("latin-1")
    if kind == "date":
        return datetime.date.fromisoformat(cell).toordinal()
    if kind == "timestamp":
        return (datetime.datetime.fromisoformat(cell) - EPOCH) // UNIT
    if kind == "datetime":
        return (datetime.datetime.fromisoformat(cell).replace(tzinfo=datetime.timezone.utc) - EPOCH) // UNIT
    r = R2.typed(cell.encode("latin-1"), kind)
    assert r[0] == "ok", (cell, kind, r)
    return r[1]


def key_of(row, cols, kinds):
    """The key of a row: the tuple of the values of its pairs (Python's tuple order is the order of the join: bytes bytewise, numbers by value, left to right)."""
    return tuple(value_of(row[c], k) for c, k in zip(cols, kinds))


def sort_rows(rows, cols, kinds):
    """The rows in ascending order of the key; ties keep the order they had."""
    return sorted(rows, key=lambda r: key_of(r, cols, kinds))


def is_sorted(rows, cols, kinds):
    keys = [key_of(r, cols, kinds) for r in rows]
    return all(a <= b for a, b in zip(keys, keys[1:]))


def merge_ref(pheader, prows, bheader, brows, pairs, jtype="inner", prefix=""):
    """(header, rows) of the merge join of two tables that are sorted by their keys. `pairs` is [(pk, bk, kind, using)] with `kind` as `value_of` has it. Two cursors and no index: for each
    probe row the build cursor moves up to the first row that is not below the key, the group of equal build keys is read, and the probe row meets every row of the group in order."""
    pks, bks, kinds = [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs]
    assert is_sorted(prows, pks, kinds) and is_sorted(brows, bks, kinds)
    dropped = {p[1] for p in pairs if p[3]}
    keep = [j for j in range(len(bheader)) if j not in dropped]
    if jtype in ("semi", "anti"):
        header = list(pheader)
    else:
        header = list(pheader) + [prefix + bheader[j] for j in keep]
    pk = [key_of(r, pks, kinds) for r in prows]
    bk = [key_of(r, bks, kinds) for r in brows]
    out = []
    j = 0
    for i, p in enumerate(prows):
        while j < len(brows) and bk[j] < pk[i]:
            j += 1
        g = j
        while g < len(brows) and bk[g] == pk[i]:
            g += 1
        group = brows[j:g]
        if jtype in ("inner", "left"):
            for b in group:
                out.append(list(p) + [b[c] for c in keep])
            if not group and jtype == "left":
                out.append(list(p) + [""] * len(keep))
        elif jtype == "semi":
            if group:
                out.append(list(p))
        elif not group:
            out.append(list(p))
    return header, out


# ---- sorted tables ---------------------------------------------------------------------------------------------------------------------------------------------

TEXT = ["", "a", "aa", "ab", "b", "B", "k1", "k10", "k2", "z", "Z", "a b", " lead", "a,b", 'say "hi"', "semi;colon", "tab\there", "back\\slash", "NULL", "007", "7", "-0", "#1", utf8("é"), utf8("日本語"), utf8("Ünï"), utf8("😀")]
INTS = ["-9223372036854775808", "-007", "-3", "-0", "0", "00", "+7", "7", "007", "12", "012", "42", "9223372036854775807"]
DEC2 = ["-1.5", "-0.01", "0", "0.0", "0.00", "00.5", "0.5", "1.5", "1.50", "01.5", "2", "2.00", "10.25", "99999999999.99"]
FLOATS = ["-1e3", "-2.5", "-0", "0", "0.0", "1e-3", "0.25", "1.5", "1.50", "15e-1", "2", "2.0", "1e2", "100", "123.456"]
DATES = ["0001-01-01", "1999-12-31", "2000-02-29", "2024-01-01", "2024-01-02", "2024-12-31", "2026-02-28", "9999-12-31"]
STAMPS = ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00+01:00", "2023-12-31T19:00:00-05:00", "2024-01-01T10:00:00.5Z", "2024-01-01T12:00:00+02:00", "2024-01-01T10:00:00Z", "2024-06-01T00:00:00Z", "2030-01-01T00:00:00Z"]
WALL = ["2024-01-01T00:00:00", "2024-01-01T00:00:00.000001", "2024-01-01T12:30:00", "2024-02-29T23:59:59", "2025-01-01T00:00:00"]

POOLS = {"text": (None, TEXT), "int": ("int", INTS), "dec": (("dec", 2), DEC2), "float": ("float", FLOATS), "date": ("date", DATES), "timestamp": ("timestamp", STAMPS), "datetime": ("datetime", WALL)}
SUFFIX = {None: "", "int": ":int", "float": ":float", ("dec", 2): ":dec(2)", "date": ":date", "timestamp": ":timestamp", "datetime": ":datetime"}

GROUPS = ["x", "y", "z"]


def _groups(rng, n, pool, dup, empty_ok=True):
    """`n` keys drawn from `pool`, in runs: a run is one key repeated 1 to `dup` times (the duplicate groups), the runs in random order (the caller sorts)."""
    out = []
    while len(out) < n:
        k = rng.choice(pool)
        out.extend([k] * rng.randint(1, dup))
    return out[:n]


def _table(rng, names, keys, key_at, tag=""):
    """Rows of `names` with `keys` in the column `key_at`, the other columns small texts and integers (the roles of a name are its last letter, as join_ref3 has them)."""
    rows = []
    for i, k in enumerate(keys):
        row = []
        for j, n in enumerate(names):
            role = n[-1]
            if j == key_at:
                row.append(k)
            elif role == "g":
                row.append(rng.choice(GROUPS))
            elif role == "v":
                row.append(str(rng.randint(0, 9)))
            else:
                row.append("%s%d" % (tag, i))
        rows.append(row)
    return rows


def sorted_case(rng, kind, np_rows=30, nb_rows=40, dup=4, using=False, composite=False, extras=0, prefix="", names=("pk", "pg", "pv", "pt"), bnames=("bk", "bg", "bv", "bt"), key_pool=None, pk_at=0, bk_at=0):
    """An R3.Case whose two tables are sorted by the key. `kind` is a key of POOLS. The probe's key is the column `pk`, the build's `bk` (at the places `pk_at` and `bk_at`, which may differ); with
    `composite` a second pair joins `pg` to `bg` (text), and the tables are sorted by the two keys. With `using` the key is the shared name `k`. Pairs are [(pk, bk, kind, using)] in the form join_ref2 has them
    ('int', ('dec', 2), 'float' and, for the date types, the same words); `extras` adds wide text columns to the build."""
    k, pool = POOLS[kind]
    if key_pool is not None:
        pool = key_pool
    pn, bn = list(names), list(bnames)
    if using:
        pn[0], bn[0] = "k", "k"
    pn = R3._names(pn, pk_at)
    bn = R3._names(bn, bk_at)
    pkeys = _groups(rng, np_rows, pool, dup)
    bkeys = _groups(rng, nb_rows, pool, dup)
    P = _table(rng, pn, pkeys, pk_at, tag="p")
    B = _table(rng, bn, bkeys, bk_at, tag="b")
    for r in B:
        r.extend(["w%d%s" % (i, rng.choice("abcdefgh")) for i in range(extras)])
    bn = bn + ["w%dx" % i for i in range(extras)]
    pairs = [(pk_at, bk_at, k, using)]
    if composite:
        pairs.append((pn.index("pg"), bn.index("bg"), None, False))
    pks = [p[0] for p in pairs]
    bks = [p[1] for p in pairs]
    kinds = [p[2] for p in pairs]
    P = sort_rows(P, pks, kinds)
    B = sort_rows(B, bks, kinds)
    return R3.Case("sorted-%s" % kind, (pn, P), (bn, B), pairs, prefix, using)


def re_sorted(case):
    """The same case after its tables were sorted again by their keys (after an edit of the rows)."""
    pks = [p[0] for p in case.pairs]
    kinds = [p[2] for p in case.pairs]
    case.P = (case.P[0], sort_rows(case.P[1], pks, kinds))
    case.B = (case.B[0], sort_rows(case.B[1], [p[1] for p in case.pairs], kinds))
    return case


def key_cells(row, cols):
    """The cells of the key as the refusal shows them: the bytes of each key cell, at most 64 of them."""
    return [row[c].encode("latin-1")[:64].decode("utf-8") for c in cols]


def break_order(rng, case, side):
    """A copy of the case in which `side` ('probe' or 'build') is not sorted: two neighbouring rows with different keys are swapped, so the first out-of-order row is known. Answers (case2, info)
    with `info` = {row, line, previous_row, previous_line, key, previous_key} of the first row whose key is below its predecessor's (rows 1-based, line 1 is the header). None when the table has
    no two neighbours with different keys."""
    pairs = case.pairs
    cols = [p[0] for p in pairs] if side == "probe" else [p[1] for p in pairs]
    kinds = [p[2] for p in pairs]
    header, rows = case.P if side == "probe" else case.B
    rows = [list(r) for r in rows]
    spots = [i for i in range(len(rows) - 1) if key_of(rows[i], cols, kinds) < key_of(rows[i + 1], cols, kinds)]
    if not spots:
        return None
    i = rng.choice(spots)
    rows[i], rows[i + 1] = rows[i + 1], rows[i]
    row = i + 2                               # the swapped-up row, 1-based
    info = {"row": row, "line": row + 1, "previous_row": row - 1, "previous_line": row, "key": key_cells(rows[row - 1], cols), "previous_key": key_cells(rows[row - 2], cols)}
    c2 = R3.Case(case.name + "-unsorted", (header, rows) if side == "probe" else case.P, (header, rows) if side == "build" else case.B, case.pairs, case.prefix, case.using)
    return c2, info


def first_disorder(rows, cols, kinds):
    """(row, previous_row) of the first row, 1-based, whose key is below the one before it; None when the rows are sorted."""
    keys = [key_of(r, cols, kinds) for r in rows]
    for i in range(1, len(keys)):
        if keys[i] < keys[i - 1]:
            return i + 1, i
    return None
