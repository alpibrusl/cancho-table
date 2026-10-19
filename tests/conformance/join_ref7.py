"""The reference of slice J7 (docs/joins.md sections 6.2, 6.3, 9, 11 and 22): the swap repair of `limit.state-too-large`, and the build that keeps only the columns the plan names.

Nothing here shares code with the tool. It is written from the definitions:

PRUNING. The build holds, for each row, the record of the `--join` file with the cells of the columns the plan does not name left empty (the delimiters stay, so the cells keep their
places): a column is NAMED when the answer shows it (`--select`, or every column of the joined row when the plan selects nothing and does not group), or when `--where`, `--group`,
`--agg` or `--order-by` read it. The key columns are not named by being keys (the key is held beside the record, in the table of keys), and neither is a column that only
`--join-where` reads (it is read before the row is held). A column that a shared-name key (USING) leaves out of the joined row is named by nothing. The accounted bytes are the formula of
section 6.2 over the held records: `sum over held rows (held record bytes + 16) + sum over distinct keys (key bytes + 60)`, the held record being the pruned one.

THE SWAP. The repair of a build that passes `--max-state-bytes` is the same call with the two files exchanged (and the two key sides of each pair, and the reader flags of the two files),
offered only when the exchange gives the same ROWS: an inner join, the probe a file under the root and smaller on disk than the `--join` file, no `--join-prefix` (the names of the build
columns would change), no `--join-where`, no column named by position, no typed shared-name key (its spelling is the probe's), and an answer that is not a page of rows; and only
when the other file, held as the build side, fits within the same bound. The answer of the repaired call is the answer of the original call under a larger bound with the rows in the
order of the exchanged definition (the other file's order, then this file's order within a key) and the columns in the order the original call gave them (`--select` is added
when the plan has none), and is byte for byte the same when the answer does not depend on the order of the joined rows (a grouping).
"""

import csv
import io
import random

import join_ref as J
import join_ref2 as R
import join_ref3 as R3
import join_ref5 as R5

TOKENS = ["x", "y", "z", "t0", "t1", "t2", "t3"]


# ---- the plan: which columns it names ------------------------------------------------------------------------------------------------------------------------


def grouping(plan):
    return plan.get("group") is not None or plan.get("aggs") is not None


used_names = R3.used_names


def keep_of(plan, case, side="build"):
    """The columns of the `--join` file (side 'build') that the plan names, as indexes into its header; for side 'probe', the columns of FILE that the plan names, when FILE is the
    one that is held (the exchanged call): a shared-name key of the exchanged call is the other file's, so the probe's key column is not named by the plan's use of that name."""
    ph, bh = case.P[0], case.B[0]
    jheader, bkeep, np_ = R3.joined_layout({"jtype": "inner", "pairs": case.pairs, "prefix": case.prefix}, ph, bh)
    used = used_names(plan, jheader)
    if side == "build":
        return {j for j in bkeep if case.prefix + bh[j] in used}
    dropped = {pk for pk, bk, kind, using in case.pairs if using}
    return {i for i in range(len(ph)) if ph[i] in used and i not in dropped}


# ---- the bytes of a held record ----------------------------------------------------------------------------------------------------------------------------------


def quote_min(cell, delim=","):
    """A cell as csv.writer writes it with minimal quoting."""
    if '"' in cell or delim in cell or "\n" in cell or "\r" in cell:
        return '"' + cell.replace('"', '""') + '"'
    return cell


def record_len(row, keep=None, delim=","):
    """The bytes of the record of `row` (str cells) as the build holds it: every cell, or only those of the columns in `keep` with the others empty. A record of one empty cell is two
    quotes (an empty line is no record); a record of one cell that the pruning left empty is empty."""
    if len(row) == 1:
        if keep is not None and 0 not in keep:
            return 0
        return 2 if row[0] == "" else len(quote_min(row[0], delim).encode("latin-1"))
    return len(delim.join(quote_min(c, delim) if keep is None or j in keep else "" for j, c in enumerate(row)).encode("latin-1"))


def build_total(rows, cols, kinds, keep=None, delim=","):
    """The accounted bytes of a build of `rows` (str cells) keyed on `cols` (read as `kinds`): sum of (held record + 16) over the rows and (key + 60) over the distinct keys. Rows are
    all held (no filter): the exchanged call's check is made without the pushdown of `--where`."""
    seen = set()
    total = 0
    for row in rows:
        k = tuple(row[c] if kind is None else R.typed(row[c].encode("latin-1"), kind)[1] for c, kind in zip(cols, kinds))
        total += record_len(row, keep, delim) + 16
        if k not in seen:
            seen.add(k)
            total += len(R.key_bytes([c.encode("latin-1") for c in row], cols, kinds)) + 60
    return total


# ---- cases: a small probe and a wide, large build ---------------------------------------------------------------------------------------------------------------


def wide_case(rng, name, np_rows=24, nb_rows=140, extras=6, keys=None, pairs=None, prefix="", using=False, same=False, shared_key=False, width=12):
    """A Case whose build is wider and longer than its probe: the build's names are bk bg bv bt then `extras` text columns w0x.., the probe's pk pg pv pt (the same names for both with `same`; only the key's with `shared_key`). The role of a name is its last letter (g, t, v, k; x for the extras)."""
    keys = keys or R3.KDOM
    pn = ["k", "g", "v", "t"] if same else ["k", "pg", "pv", "pt"] if shared_key else ["pk", "pg", "pv", "pt"]
    bn = ["k", "g", "v", "t"] if same else ["k", "bg", "bv", "bt"] if shared_key else ["bk", "bg", "bv", "bt"]
    extra_names = ["w%dx" % i for i in range(extras)]
    P = (pn, R3._rows(rng, np_rows, keys, 0))
    brows = R3._rows(rng, nb_rows, keys, 0)
    for r in brows:
        r.extend(["".join(rng.choice("abcdefghij") for _ in range(width)) for _ in range(extras)])
    B = (bn + extra_names, brows)
    return R3.Case(name, P, B, pairs or [(0, 0, None, False)], prefix, using)


def write_scene(d, case, pside=("csv", ","), bside=("csv", ","), rng=None, popts=None, bopts=None):
    """The two files of a case: p.csv / p.jsonl and b.csv / b.jsonl as the sides say (kind, delimiter). Answers the two Sides."""
    rng = rng or random.Random(5)
    ph, pr = case.P
    bh, br = case.B
    P = R5.Side(pside[0], [h.encode() for h in ph], [[c.encode("latin-1") for c in r] for r in pr], pside[1], **(popts or {}))
    B = R5.Side(bside[0], [h.encode() for h in bh], [[c.encode("latin-1") for c in r] for r in br], bside[1], **(bopts or {}))
    P.write(str(d / pname(pside)), rng)
    B.write(str(d / bname(bside)), rng)
    return P, B


def pname(side):
    return "p.jsonl" if side[0] == "jsonl" else "p.csv" if side[1] == "," else "ps.csv"


def bname(side):
    return "b.jsonl" if side[0] == "jsonl" else "b.csv" if side[1] == "," else "bs.csv"


def argv_of(d, case, plan, jtype="inner", pside=("csv", ","), bside=("csv", ","), fmt="json", bound=None, extra=(), operand=True):
    """The flags of one run of a case: `--root D --join B --on ... --join-type T [--join-prefix P]`, the reader flags of the two sides, the plan's flags, the bound, the operand."""
    ph, bh = case.P[0], case.B[0]
    a = ["--root", str(d), "--join", bname(bside), "--on", R.on_arg([(p[0], p[1], p[2], p[3]) for p in case.pairs], [x.encode() for x in ph], [x.encode() for x in bh]), "--join-type", jtype]
    if case.prefix and jtype in ("inner", "left"):
        a += ["--join-prefix", case.prefix]
    if pside[0] == "jsonl":
        a += ["--input-format", "jsonl", "--fields", ",".join(ph)]
    elif pside[1] != ",":
        a += ["--delimiter", R5.delim_word(pside[1])]
    if bside[0] == "jsonl":
        a += ["--join-input-format", "jsonl", "--join-fields", ",".join(bh)]
    elif pside[0] == "jsonl":
        a += ["--join-input-format", "csv"]
        if bside[1] != pside[1]:
            a += ["--join-delimiter", R5.delim_word(bside[1])]
    elif bside[1] != pside[1]:
        a += ["--join-delimiter", R5.delim_word(bside[1])]
    a += R3.flags_of(plan, ph, bh, fmt)
    if bound is not None:
        a += ["--max-state-bytes", str(bound)]
    a += list(extra)
    if operand:
        a.append(pname(pside))
    return a


# ---- the exchanged call ----------------------------------------------------------------------------------------------------------------------------------------


def swapped(plan, case):
    """(plan', P', B') of the exchanged call: the pairs read the other way, and the joined header of the original as `--select` when the plan selects nothing and does not group."""
    ph, prows = case.P
    bh, brows = case.B
    p2 = dict(plan)
    p2["pairs"] = [(bk, pk, kind, using) for pk, bk, kind, using in plan["pairs"]]
    jheader = R3.joined_layout({"jtype": "inner", "pairs": case.pairs, "prefix": case.prefix}, ph, bh)[0]
    if not grouping(plan) and not plan.get("select"):
        p2["select"] = jheader
    return p2, (bh, brows), (ph, prows)


def swap_expected(plan, case):
    """('ok', labels, rows) of the exchanged call, or ('refuse', info)."""
    p2, P2, B2 = swapped(plan, case)
    try:
        return R3.execute(p2, P2, B2)
    except R3.Refused as r:
        return ("refuse", r.info)


def bounds(plan, case):
    """(a_b, a_p): a_b is the accounted bytes the call as written holds when it is run under a bound large enough (the build's rows kept by `--where`'s pushdown, the columns the plan names:
    what the refusal is about); a_p is what the OTHER file needs as the build side of the exchanged call, all its rows (the check at the refusal does not push `--where` down) and the columns the
    plan names. Raises R3.Refused when the plan refuses (a typed key cell)."""
    trace = {"keep": keep_of(plan, case, "build")}
    R3.execute(plan, case.P, case.B, trace)
    a_b = sum(trace.get("build", []))
    pcols = [p[0] for p in case.pairs]
    kinds = [p[2] for p in case.pairs]
    return a_b, build_total(case.P[1], pcols, kinds, keep_of(plan, case, "probe"))


def group_slack(plan, case):
    """The accounted bytes the groups of a grouping plan need beside the build (the groups share the flag with it): the keys of the groups, by the formula of docs/joins.md 6.3, and a margin for the
    values `distinct` keeps. 0 for a plan that does not group."""
    if not grouping(plan):
        return 0
    trace = {"keep": keep_of(plan, case, "build")}
    R3.execute(plan, case.P, case.B, trace)
    return sum(trace.get("groups", [])) + 2048


def csv_bytes(labels, rows, delim=","):
    return J.render_rows([[x.encode("latin-1") for x in labels]] + [[c.encode("latin-1") for c in r] for r in rows], delim)


def parse_csv(data):
    """Rows of csv bytes as lists of str (latin-1)."""
    return [[c.decode("latin-1") for c in r] for r in J.read_csv_bytes(data)]


def multiset(rows):
    from collections import Counter
    return Counter(tuple(r) for r in rows)


def random_plan(rng, case, jtype="inner"):
    """A plan of J3's generator without `--join-where`, and a page only when it groups (a swap is offered for an answer that is not a page of rows)."""
    plan = R3.random_plan(rng, case, jtype)
    plan.pop("jw", None)
    if not grouping(plan):
        for k in ("limit", "from", "top"):
            plan.pop(k, None)
    return plan


def as_result(argv, p):
    """A finished subprocess as the harness's Result (to validate it against the schema)."""
    from harness import Result
    return Result(argv, p.returncode, p.stdout, p.stderr)


def raw_fields(rec, delim=b","):
    """The raw fields (start, end, quoted) of one csv record `rec` (bytes), a quoted field with its quotes."""
    out, n, start, i, inq = [], len(rec), 0, 0, False
    while i < n:
        c = rec[i:i + 1]
        if inq:
            if c == b'"':
                if rec[i + 1:i + 2] == b'"':
                    i += 2
                    continue
                inq = False
        elif c == b'"' and i == start:
            inq = True
        elif c == delim:
            out.append((start, i))
            start = i + 1
        i += 1
    out.append((start, n))
    return out


def raw_pruned_len(rec, keep, delim=b","):
    """The bytes of the held record of the raw record `rec` when the columns in `keep` are kept: their raw fields (quotes included) and a delimiter between every two columns."""
    spans = raw_fields(rec, delim)
    return sum(e - s for j, (s, e) in enumerate(spans) if j in keep) + len(spans) - 1


def raw_key(rec, col, delim=b","):
    """The key of a raw record: its field `col` with the quotes undone."""
    s, e = raw_fields(rec, delim)[col]
    f = rec[s:e]
    if f[:1] == b'"':
        f = f[1:-1].replace(b'""', b'"')
    return f
