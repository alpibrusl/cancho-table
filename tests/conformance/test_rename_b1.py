"""Slice B1 of docs/or-and-columns.md (section 8.2): `--rename TARGET=NEW` and `select a as b`. Written before the tool was changed (they fail on the binary of `main`: it has no such flag).

* **G-B1 / syntax (G-OFFSETS)** the text of `--rename` against the reference reader (rename_b1.py, which shares no code with the tool): accepted or refused, the byte offset and the words, on pinned
  cases and on generated soups; the ceiling of 4,096 items; `--rename=TEXT` as well as `--rename TEXT`.
* **G-B1 / labels** generated tables and plans (query_gen), every mode (rows with `--select`, `--where` alone, `--rows`, a grouping, an aggregate alone), renames by label and by `#N`, awkward and colliding
  new labels: the header of the csv, the `columns` of the json and the keys of the json lines are the reference's, the rows are the answer without the rename, the refusals are the reference's (rule,
  exit, item).
* **G-ORACLE-SQL** SQLite 3.x: `SELECT cI AS "alias"` over the same table as TEXT gives the same names and the same values (rows and a grouping).
* the repairs, `--sort` after the rename, `output.duplicate-column` with a place to point, the conflicts, the join, the JSON-lines input, standard input, MCP.
* **G-CORPUS-EQ** the flag form and the query form (`select a as b`) are one plan: identical stdout, stderr and status in 4 modes (json and csv, one thread and four with tiny ranges), and the
  explain documents too; the query front end against query_ref.py.
* **G-PAR** the same bytes at any thread count and any chunk, **G-EXPLAIN** `output.columns` is the columns of the real answer, **G-FUZZ** no trap, every refusal a rule `introspect` lists, every repair accepted.

RENAME_B1_SOUPS, RENAME_B1_PLANS, RENAME_B1_QPLANS, RENAME_B1_SQL set the sizes; RENAME_B1_REPORT=1 prints what each saw.
"""
import csv
import io
import json
import os
import random
import re
import unittest
from concurrent.futures import ThreadPoolExecutor

import query_gen
import query_ref
import rename_b1 as R
from harness import TRAPS, Scratch, introspect, run, run_argv, validate

SOUPS = int(os.environ.get("RENAME_B1_SOUPS", "1500"))
PLANS = int(os.environ.get("RENAME_B1_PLANS", "300"))
QPLANS = int(os.environ.get("RENAME_B1_QPLANS", "300"))
SQLS = int(os.environ.get("RENAME_B1_SQL", "300"))
REPORT = bool(os.environ.get("RENAME_B1_REPORT"))
SEED = int(os.environ.get("RENAME_B1_SEED", "41"))
THREADS = ["--threads", "4", "--parallel-min-bytes", "0", "--chunk-bytes", "65536"]
HEADER_RULES = {"select.unknown-column", "select.ambiguous-column", "column.unknown", "column.ambiguous", "column.type-conflict", "sort.unknown-key"}
EXIT = {"rename.syntax": 2, "rename.unknown-target": 3, "rename.ambiguous-target": 8, "rename.repeated-target": 2}


def report(*a):
    if REPORT:
        print(*a)


def labels_of(*names):
    return [n.encode() for n in names]


def predicted(text, labels):
    """What the reference says a --rename of `text` does to `labels` (bytes): ("ok", labels) | ("syntax", offset, words) | ("too-many",) | (rule, item index, info)."""
    got = R.parse_list(text)
    if got[0] != "ok":
        return got
    return R.resolve(got[1], labels)


def observe_refusal(res):
    d = res.doc()
    e = d["error"]
    return e["rule"], e.get("detail", {}), e


def check_against_reference(tc, res, text, labels):
    """The tool's answer (json) to --rename `text` over a table whose answer has `labels` equals the reference's."""
    want = predicted(text, labels)
    if want[0] == "ok":
        tc.assertEqual(res.status, 0, (text, res))
        tc.assertEqual(res.data()["columns"], [l.decode() for l in want[1]], text)
        return
    tc.assertNotEqual(res.status, 0, (text, res))
    rule, detail, err = observe_refusal(res)
    tc.assertEqual(validate(res), [], res)
    if want[0] == "syntax":
        tc.assertEqual((rule, res.status, detail["offset"], detail["expected"]), ("rename.syntax", 2, want[1], want[2]), (text, res))
    elif want[0] == "too-many":
        tc.assertEqual((rule, res.status), ("args.bad-value", 2), (text, res))
    else:
        tc.assertEqual((rule, res.status, detail["item"]), (want[0], EXIT[want[0]], want[1] + 1), (text, res))
        if want[0] == "rename.ambiguous-target":
            tc.assertEqual(detail["columns"], want[2], (text, res))
        if want[0] == "rename.repeated-target":
            tc.assertEqual(detail["first"], want[2], (text, res))


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.addCleanup(self.s.cleanup)
        self.s.write("f.csv", "a,b,c\n1,x,1.5\n2,y,2.5\n")
        self.s.write("dup.csv", "a,a,b\n1,2,3\n4,5,6\n")
        self.s.write("sales.csv", "region,units,price\nnorth,3,1.50\nsouth,5,2.25\nnorth,7,1.50\n")
        self.cwd = self.s.dir

    def r(self, *a, stdin=None):
        return run("--root", self.s.dir, *a, stdin=stdin)


# ---- G-B1 / syntax and the reference --------------------------------------------------------------------------------------------------

PINNED = [
    ("a=b", ("ok",)),
    ("a=b,b=c", ("ok",)),
    ("#1=x,#3=y", ("ok",)),
    ("a", ("syntax", 1, R.EXP_ITEM)),
    ("a=b,c", ("syntax", 5, R.EXP_ITEM)),
    (",", ("syntax", 0, R.EXP_ITEM)),
    ("a=b,", ("syntax", 4, R.EXP_ITEM)),
    ("=b", ("syntax", 0, R.EXP_TARGET)),
    ("a=b,=c", ("syntax", 4, R.EXP_TARGET)),
    ("a=", ("syntax", 2, R.EXP_NEW)),
    ("a=,b=c", ("syntax", 2, R.EXP_NEW)),
    ("a=b,c=", ("syntax", 6, R.EXP_NEW)),
    ("a\\", ("syntax", 1, R.EXP_ESCAPE)),
    ("a=b\\", ("syntax", 3, R.EXP_ESCAPE)),
    ("a\\q=b", ("syntax", 1, R.EXP_ESCAPE)),
    ("a=\\q", ("syntax", 2, R.EXP_ESCAPE)),
    ("a\\", ("syntax", 1, R.EXP_ESCAPE)),
    ("a=x=y", ("ok",)),                     # the first unescaped = splits; the rest is the new label
    ("a\\=b=x", ("unknown",)),              # a target that is the label "a=b"
    ("\\#1=z", ("unknown",)),               # a label that is "#1", not a position
    ("#a=z", ("unknown",)),                 # not digits: a label, as in --select
    ("#0=z", ("unknown",)),
    ("#4=z", ("unknown",)),
    ("#1=x,#1=y", ("repeated",)),
    ("a=x,#1=y", ("repeated",)),
    ("a=x,a=y", ("repeated",)),
    ("#1234567890=z", ("unknown",)),
]


class Syntax(Base):
    def run_text(self, text, *extra, table="f.csv"):
        return self.r("--select", "a,b,c", "--rename", text, *extra, table)

    def test_pinned_cases_are_the_references_and_the_tools(self):
        for text, expect in PINNED:
            with self.subTest(text=text):
                want = predicted(text.encode(), labels_of("a", "b", "c"))
                kind = {"ok": "ok", "syntax": "syntax", "unknown": "rename.unknown-target", "repeated": "rename.repeated-target"}[expect[0]]
                self.assertEqual(want[0], kind, want)
                if expect[0] == "syntax":
                    self.assertEqual(want[1:], expect[1:])
                check_against_reference(self, self.run_text(text), text.encode(), labels_of("a", "b", "c"))

    def test_the_equals_form_of_the_flag_is_the_same(self):
        for text in ("a=b", "a", "#1=x,#2=y", "#9=z", "a=x,a=y"):
            a = self.r("--select", "a,b,c", "--rename", text, "f.csv")
            b = self.r("--select", "a,b,c", "--rename=" + text, "f.csv")
            self.assertEqual((a.status, a.stdout), (b.status, b.stdout), text)

    def test_a_syntax_error_is_found_before_anything_is_read(self):
        # the file does not exist: the list is refused first, as --where's is
        r = self.r("--select", "a", "--rename", "a", "nope.csv")
        self.assertEqual(r.first_rule(), "rename.syntax", r)

    def test_generated_soups_against_the_reference(self):
        rnd = random.Random(SEED)
        alphabet = ["a", "b", "c", "x", "A", "#", "1", "2", "9", "0", ",", "=", "\\", " ", "é", ":", "-", "a=b", "#1=", "\\,", "\\=", "\\#", "\\\\"]
        seen = {}
        valid = ["a=b", "#1=x", "b=y,c=z", "#2=q,a=w", "a\\=b=c", "a=x\\,y"]
        for k in range(SOUPS):
            if rnd.random() < 0.3:
                parts = list(rnd.choice(valid))
                for _ in range(rnd.randint(0, 3)):
                    at = rnd.randrange(len(parts) + 1)
                    r = rnd.random()
                    if r < 0.4 and parts:
                        parts.pop(min(at, len(parts) - 1))
                    else:
                        parts.insert(at, rnd.choice(alphabet))
                text = "".join(parts)
            else:
                text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
            if not text:
                continue          # (an empty value is args.bad-value, the cli's, before the list is read)
            with self.subTest(text=text):
                want = predicted(text.encode(), labels_of("a", "b", "c"))
                seen[want[0]] = seen.get(want[0], 0) + 1
                check_against_reference(self, self.run_text(text), text.encode(), labels_of("a", "b", "c"))
        report("syntax soups:", SOUPS, json.dumps(seen, sort_keys=True))
        for kind in ("ok", "syntax", "rename.unknown-target"):
            self.assertGreater(seen.get(kind, 0), SOUPS // 50, seen)

    def test_the_ceiling_is_4096_items(self):
        ok = ",".join("#%d=x" % (k + 1) for k in range(4096))
        over = ok + ",#4097=x"
        r = self.r("--select", "a,b,c", "--rename", ok, "f.csv")
        self.assertEqual((r.first_rule(), r.status), ("rename.unknown-target", 3), r)
        self.assertEqual(r.error()["detail"]["item"], 4)
        r = self.r("--select", "a,b,c", "--rename", over, "f.csv")
        self.assertEqual((r.first_rule(), r.status), ("args.bad-value", 2), r)


# ---- G-B1 / the labels ---------------------------------------------------------------------------------------------------------------

def parse_csv(stdout):
    return list(csv.reader(io.StringIO(stdout.decode("utf-8"), newline="")))


def jsonl_pairs(stdout):
    out = []
    for line in stdout.split(b"\n"):
        if line:
            out.append(json.loads(line.decode("utf-8"), object_pairs_hook=lambda p: p))
    return out


def one_case(tc, rnd, d, stats):
    names, kinds, rows = query_gen.make_table(rnd)
    path = os.path.join(d, "f.csv")
    query_gen.write_csv(path, names, rows)
    plan = query_gen.make_plan(rnd, names, kinds, rows)
    plan["order"] = []                         # --sort after a rename has its own test
    plan["limit"] = plan["offset"] = None
    flags = query_gen.flag_form(plan)
    labels = R.default_labels(plan, names)
    renames = R.make_renames(rnd, labels, names)
    text = R.flag_text(rnd, renames, labels)
    # now and then a label is the target though two columns carry it
    if len(set(labels)) < len(labels) and rnd.random() < 0.5:
        dup = [l for l in labels if labels.count(l) > 1][0]
        text = R.escape_target(dup.encode()) + b"=zz," + text
    want = predicted(text, [l.encode() for l in labels])
    stats[want[0]] = stats.get(want[0], 0) + 1
    for fmt in ("json", "csv", "jsonl"):
        fa = [] if fmt == "json" else ["--format", fmt]
        plain = run(*flags, *fa, "f.csv", cwd=d)
        got = run(*flags, "--rename", text.decode(), *fa, "f.csv", cwd=d)
        ctx = (fmt, names, flags, text, want[0])
        if want[0] != "ok":
            if plain.status != 0 and fmt == "json" and plain.first_rule() in HEADER_RULES:
                # the plan is refused by the header before a rename is looked at (a column the header lacks, a column read as two types): the same refusal, rename or none
                tc.assertEqual((got.status, got.first_rule()), (plain.status, plain.first_rule()), ctx)
                continue
            if plain.status != 0 and fmt != "json":
                continue
            tc.assertEqual(got.status, EXIT[want[0]] if want[0] in EXIT else 2, ctx + (got,))
            if fmt == "json":
                check_against_reference(tc, got, text, [l.encode() for l in labels])
            continue
        new = [l.decode() for l in want[1]]
        if plain.status != 0:
            # a refusal of the data: the run reads the same cells, so the same rule (the csv header may already be out); but two equal labels in json lines are refused before any row is read
            if fmt == "jsonl" and len(set(new)) < len(new) and plain.status == 8:
                tc.assertEqual(got.status, 2, ctx)
            elif fmt == "jsonl" and plain.status == 2 and len(set(new)) == len(new):
                pass                      # the rename removed the duplicate that json lines refused: the answer is the data's
            else:
                tc.assertEqual(got.status, plain.status, ctx)
            continue
        if fmt == "json":
            tc.assertEqual(got.status, 0, ctx + (got,))
            a, b = plain.doc(), got.doc()
            tc.assertEqual(a["data"]["columns"], labels, ctx)
            tc.assertEqual(b["data"]["columns"], new, ctx)
            a["data"]["columns"] = b["data"]["columns"] = None
            tc.assertEqual(a, b, ctx)
        elif fmt == "csv":
            tc.assertEqual(got.status, 0, ctx + (got,))
            pa, pb = parse_csv(plain.stdout), parse_csv(got.stdout)
            tc.assertEqual(pa[0], labels, ctx)
            tc.assertEqual(pb[0], new, ctx)
            tc.assertEqual(pa[1:], pb[1:], ctx)
        else:
            if len(set(new)) < len(new):
                tc.assertEqual((got.status, got.stderr.startswith(b"table: output.duplicate-column")), (2, True), ctx + (got,))
                continue
            tc.assertEqual(got.status, 0, ctx + (got,))
            pa, pb = jsonl_pairs(plain.stdout), jsonl_pairs(got.stdout)
            tc.assertEqual(len(pa), len(pb), ctx)
            for x, y in zip(pa, pb):
                tc.assertEqual([k for k, _ in y], new, ctx)
                tc.assertEqual([v for _, v in x], [v for _, v in y], ctx)


class Labels(unittest.TestCase):
    def test_generated_plans_every_mode_and_format(self):
        rnd = random.Random(SEED + 1)
        stats = {}
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for case in range(PLANS):
                one_case(self, rnd, d, stats)
        report("labels:", PLANS, json.dumps(stats, sort_keys=True))
        self.assertGreater(stats.get("ok", 0), PLANS // 3, stats)
        self.assertGreater(sum(v for k, v in stats.items() if k != "ok"), PLANS // 50, stats)


class Sqlite(unittest.TestCase):
    """G-ORACLE-SQL: SELECT cI AS "alias" gives the names and the values."""

    def test_rows_and_groupings_against_sqlite(self):
        rnd = random.Random(SEED + 2)
        import tempfile
        ran = 0
        with tempfile.TemporaryDirectory() as d:
            for case in range(SQLS):
                names, kinds, rows = query_gen.make_table(rnd)
                query_gen.write_csv(os.path.join(d, "f.csv"), names, rows)
                n = len(names)
                if rnd.random() < 0.6:
                    picks = [rnd.randrange(n) for _ in range(rnd.randint(1, 4))]
                    aliases = [rnd.choice(R.POOL + names) if rnd.random() < 0.6 else None for _ in picks]
                    sel = ",".join(query_gen.lst(("name", names[c])) for c in picks)
                    if all(a is None for a in aliases):
                        aliases[0] = rnd.choice(R.POOL)
                    renames = [(i, a) for i, a in enumerate(aliases) if a is not None]
                    argv = ["--select", sel]
                    sql_labels = [a if a is not None else names[c] for c, a in zip(picks, aliases)]
                    got_labels, got_rows = R.sqlite_answer(names, rows, picks, [a if a is not None else names[c] for c, a in zip(picks, aliases)])
                else:
                    k = rnd.randrange(n)
                    alias_k, alias_n = rnd.choice(R.POOL), rnd.choice(R.POOL + ["count"])
                    argv = ["--group", query_gen.lst(("name", names[k])).replace(":", "\\:"), "--agg", "count"]
                    renames = [(0, alias_k), (1, alias_n)]
                    con = __import__("sqlite3").connect(":memory:")
                    con.execute("CREATE TABLE t (c TEXT)")
                    con.executemany("INSERT INTO t VALUES (?)", [(r[k],) for r in rows])
                    cur = con.execute("SELECT c AS %s, COUNT(*) AS %s FROM t GROUP BY c ORDER BY c" % (R.qi(alias_k), R.qi(alias_n)))
                    got_labels = [x[0] for x in cur.description]
                    got_rows = [[a, str(b)] for a, b in cur.fetchall()]
                    sql_labels = got_labels
                text = b",".join(b"#%d=" % (i + 1) + R.escape_new(a.encode()) for i, a in renames)
                r = run(*argv, "--rename", text.decode(), "--format", "csv", "f.csv", cwd=d)
                ran += 1
                self.assertEqual(r.status, 0, (argv, text, r))
                out = parse_csv(r.stdout)
                self.assertEqual(out[0], got_labels, (names, argv, text))
                self.assertEqual(out[1:], [[str(c) for c in row] for row in got_rows], (names, argv, text))
        report("sqlite:", ran)
        self.assertEqual(ran, SQLS)


# ---- the refusals, the repairs, the neighbours ---------------------------------------------------------------------------------------------

class Refusals(Base):
    def test_unknown_target_lists_the_labels_and_repairs_a_case_variant(self):
        r = self.r("--select", "a,c", "--rename", "A=x,c=y", "f.csv")
        self.assertEqual((r.first_rule(), r.status), ("rename.unknown-target", 3), r)
        e = r.error()
        self.assertEqual(e["detail"]["item"], 1)
        self.assertEqual(e["detail"]["target"], "A")
        self.assertEqual(e["detail"]["available"], ["a", "c"])
        self.assertEqual(e["repair"]["kind"], "choose")
        opts = e["repair"]["options"]
        self.assertEqual(len(opts), 1)
        again = run_argv(opts[0]["argv"])
        self.assertEqual(again.status, 0, again)
        self.assertEqual(again.data()["columns"], ["x", "y"])
        self.assertEqual(validate(r), [])

    def test_the_repair_keeps_the_rest_of_the_list_as_written(self):
        r = self.r("--select", "a,b,c", "--rename", "B=p\\,q,c=z", "f.csv")
        opt = r.error()["repair"]["options"][0]["argv"]
        self.assertIn("b=p\\,q,c=z", opt)

    def test_a_column_that_is_not_selected_is_unknown(self):
        r = self.r("--select", "a", "--rename", "b=x", "f.csv")
        self.assertEqual((r.first_rule(), r.status), ("rename.unknown-target", 3), r)
        self.assertEqual(r.error()["detail"]["available"], ["a"])
        self.assertEqual(r.error()["repair"]["kind"], "none")

    def test_the_source_name_of_an_aggregate_is_an_unknown_target_after_the_rename_only_in_the_sort(self):
        r = self.r("--group", "region", "--agg", "sum:units", "--rename", "sum:units=total", "--format", "csv", "sales.csv")
        self.assertEqual(r.status, 0, r)
        self.assertEqual(r.stdout.decode().splitlines()[0], "region,total")
        r = self.r("--group", "region", "--agg", "sum:units", "--rename", "total=x", "sales.csv")
        self.assertEqual((r.first_rule(), r.status), ("rename.unknown-target", 3), r)
        self.assertEqual(r.error()["detail"]["available"], ["region", "sum:units"])

    def test_ambiguous_target_is_exit_8_and_a_position_resolves_it(self):
        r = self.r("--select", "a,a", "--rename", "a=x", "f.csv")
        self.assertEqual((r.first_rule(), r.status), ("rename.ambiguous-target", 8), r)
        self.assertEqual(r.error()["detail"]["columns"], [1, 2])
        self.assertEqual(r.error()["repair"]["kind"], "none")
        r = self.r("--select", "a,a", "--rename", "#2=x", "f.csv")
        self.assertEqual(r.data()["columns"], ["a", "x"], r)

    def test_repeated_target(self):
        for text in ("a=x,a=y", "a=x,#1=y", "#1=x,#1=y"):
            r = self.r("--select", "a,b", "--rename", text, "f.csv")
            self.assertEqual((r.first_rule(), r.status), ("rename.repeated-target", 2), (text, r))
            self.assertEqual(r.error()["detail"]["item"], 2)
            self.assertEqual(r.error()["detail"]["first"], 1)

    def test_targets_are_the_labels_before_any_rename(self):
        r = self.r("--select", "a,b", "--rename", "a=b,b=a", "f.csv")
        self.assertEqual(r.data()["columns"], ["b", "a"])
        r = self.r("--select", "a,b", "--rename", "a=b,b=c", "f.csv")
        self.assertEqual(r.data()["columns"], ["b", "c"])

    def test_a_new_label_may_equal_another_label_in_csv_and_json(self):
        r = self.r("--select", "a,b", "--rename", "b=a", "--format", "csv", "f.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], "a,a", r)
        r = self.r("--select", "a,b", "--rename", "b=a", "f.csv")
        self.assertEqual(r.data()["columns"], ["a", "a"])

    def test_json_lines_refuses_two_equal_labels_with_a_place_to_point(self):
        r = self.r("--select", "a,b", "--rename", "b=a", "--format", "jsonl", "f.csv")
        self.assertEqual(r.status, 2)
        self.assertTrue(r.stderr.startswith(b"table: output.duplicate-column"), r)
        # the sentence carries no hint; the hint is in the rule table, and it points at the way out
        rule = [x for x in introspect()["rules"] if x["rule"] == "output.duplicate-column"][0]
        self.assertIn("or rename one with --rename", rule["summary"])
        self.assertIn("Repair: none.", rule["summary"])

    def test_the_repair_offers_at_most_five_case_variants_in_both_directions(self):
        self.s.write("v.csv", "ABC,ABc,AbC,Abc,aBC,aBc,abC\n1,2,3,4,5,6,7\n")
        r = self.r("--select", "ABC,ABc,AbC,Abc,aBC,aBc,abC", "--rename", "abc=x", "v.csv")
        opts = r.error()["repair"]["options"]
        self.assertEqual(len(opts), 5, r)
        self.assertEqual(len({tuple(o["argv"]) for o in opts}), 5)
        for o in opts:
            again = run_argv(o["argv"])
            self.assertEqual(again.status, 0, again)
        # an upper-case label for a lower-case target, and the other way round
        for label, target in (("B", "b"), ("b", "B")):
            self.s.write("c.csv", label + "\n1\n")
            r = self.r("--select", label, "--rename", target + "=x", "c.csv")
            self.assertEqual(r.error()["repair"]["kind"], "choose", (label, target, r))

    def test_a_label_with_an_equals_sign_or_a_comma_is_repaired_escaped(self):
        self.s.write("e.csv", "K=V,a#b\n1,2\n")
        r = self.r("--select", "K=V", "--rename", "k\\=v=x", "e.csv")
        self.assertEqual(r.first_rule(), "rename.unknown-target", r)
        again = run_argv(r.error()["repair"]["options"][0]["argv"])
        self.assertEqual(again.status, 0, again)
        self.assertEqual(again.data()["columns"], ["x"])

    def test_no_repair_is_offered_for_the_equals_form_of_the_flag(self):
        r = self.r("--select", "a", "--rename=A=x", "f.csv")
        self.assertEqual((r.first_rule(), r.error()["repair"]["kind"]), ("rename.unknown-target", "none"), r)

    def test_a_wide_answer_lists_fifty_labels(self):
        names = ["c%d" % i for i in range(60)]
        self.s.write("w.csv", ",".join(names) + "\n" + ",".join("1" for _ in names) + "\n")
        r = self.r("--rows", "--rename", "zz=x", "w.csv")
        d = r.error()["detail"]
        self.assertEqual((len(d["available"]), d["available_truncated"], d["columns"]), (50, True, 60), r)
        r = self.r("--select", ",".join(names[:50]), "--rename", "zz=x", "w.csv")
        d = r.error()["detail"]
        self.assertEqual((len(d["available"]), d["available_truncated"]), (50, False), r)

    def test_the_repair_of_a_grouping_keeps_the_alias_of_an_aggregate(self):
        r = self.r("--query", "select region, count(*) as n from 'sales.csv' group by region order by sum(units)")
        self.assertEqual(r.first_rule(), "query.group-mismatch", r)
        argv = r.error()["repair"]["options"][0]["argv"]
        self.assertIn("--rename", argv)
        self.assertEqual(argv[argv.index("--rename") + 1], "#2=n", argv)
        again = run_argv(argv + ["--format", "csv"])
        self.assertEqual(again.status, 0, again)
        self.assertEqual(again.stdout.decode().splitlines()[0], "region,n,sum:units", again)

    def test_the_repair_of_a_mix_keeps_the_aliases(self):
        r = self.r("--query", "select region as r, units as u from 'sales.csv' where region = 'north' or units = '3' and price = '1.50'")
        self.assertEqual(r.first_rule(), "query.mixed-and-or", r)
        for option in r.error()["repair"]["options"]:
            argv = option["argv"]
            self.assertEqual(argv[argv.index("--rename") + 1], "#1=r,#2=u", argv)
            self.assertEqual(run_argv(argv + ["--format", "csv"]).stdout.decode().splitlines()[0], "r,u")

    def test_fifty_one_labels_are_truncated(self):
        names = ["c%d" % i for i in range(51)]
        self.s.write("w51.csv", ",".join(names) + "\n" + ",".join("1" for _ in names) + "\n")
        r = self.r("--rows", "--rename", "zz=x", "w51.csv")
        d = r.error()["detail"]
        self.assertEqual((len(d["available"]), d["available_truncated"], d["columns"]), (50, True, 51), r)

    def test_discover_takes_no_rename(self):
        r = self.r("--input-format", "jsonl", "--discover", "--rename", "a=b", stdin=b'{"a":1}\n')
        self.assertEqual((r.first_rule(), r.status), ("args.conflict", 2), r)
        self.assertTrue(r.error()["detail"]["flags"].startswith("--discover"), r)          # --discover's conflict, not only the shape's

    def test_the_syntax_error_reports_the_list_and_the_offset(self):
        r = self.r("--select", "a,b", "--rename", "a=x,b", "f.csv")
        self.assertEqual(r.error()["detail"], {"offset": 5, "expected": R.EXP_ITEM, "list": "a=x,b"}, r)
        self.assertEqual(r.error()["repair"], None, r)
        self.assertIn("TARGET=NEW", r.error()["hint"])

    def test_ten_digits_after_a_hash_are_a_label_and_nine_are_a_position(self):
        self.s.write("h.csv", "#1234567890,#123456789\n1,2\n")
        r = self.r("--select", "\\#1234567890", "--rename", "#1234567890=x", "h.csv")
        self.assertEqual(r.data()["columns"], ["x"], r)
        r = self.r("--select", "\\#123456789", "--rename", "#123456789=x", "h.csv")
        self.assertEqual((r.first_rule(), r.status), ("rename.unknown-target", 3), r)
        r = self.r("--select", "\\#123456789", "--rename", "\\#123456789=x", "h.csv")
        self.assertEqual(r.data()["columns"], ["x"], r)
        # a `#` and something that is not digits is a label (a position is `#` and digits and nothing else), as in --select
        self.s.write("k.csv", "#abc,#1a,#\n1,2,3\n")
        r = self.r("--select", "\\#abc,\\#1a,\\#", "--rename", "#abc=x,#1a=y,#=z", "k.csv")
        self.assertEqual(r.data()["columns"], ["x", "y", "z"], r)

    def test_the_rename_that_repairs_the_duplicate_is_accepted(self):
        r = self.r("--select", "a,a", "--rename", "#2=a2", "--format", "jsonl", "f.csv")
        self.assertEqual(r.status, 0, r)
        self.assertEqual(r.stdout.decode().splitlines()[0], '{"a":"1","a2":"1"}')

    def test_conflicts(self):
        for argv in (["--report", "types", "--rename", "a=x", "f.csv"], ["--rename", "a=x", "f.csv"], ["--query", "select a from 'f.csv'", "--rename", "#1=x"]):
            r = self.r(*argv)
            self.assertEqual((r.first_rule(), r.status), ("args.conflict", 2), (argv, r))

    def test_sort_names_the_label_after_the_rename(self):
        base = ["--group", "region", "--agg", "sum:units", "sales.csv"]
        r = self.r("--rename", "sum:units=total", "--sort", "-total", *base)
        self.assertEqual(r.status, 0, r)
        self.assertEqual(r.data()["columns"], ["region", "total"])
        self.assertEqual([row[0] for row in r.data()["rows"]], ["north", "south"])
        r = self.r("--rename", "sum:units=total", "--sort", "-sum:units", *base)
        self.assertEqual((r.first_rule(), r.status), ("sort.unknown-key", 2), r)
        self.assertEqual(r.error()["detail"]["available"], ["region", "total"])

    def test_order_by_and_where_and_group_and_agg_name_header_columns(self):
        r = self.r("--select", "units", "--rename", "units=region", "--where", "region = north", "--order-by", "-units:int", "--format", "csv", "sales.csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["region", "7", "3"], r)
        r = self.r("--group", "region", "--rename", "region=units", "--agg", "sum:units", "--format", "csv", "sales.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], "units,sum:units", r)

    def test_a_rename_of_a_where_only_answer_is_the_header(self):
        r = self.r("--where", "region = north", "--rename", "units=n", "--format", "csv", "sales.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], "region,n,price", r)

    def test_a_rename_with_rows(self):
        r = self.r("--rows", "--rename", "#1=first", "--format", "csv", "sales.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], "first,units,price", r)

    def test_standard_input(self):
        r = self.r("--select", "a,b", "--rename", "a=n", "--format", "csv", stdin=b"a,b\n1,2\n")
        self.assertEqual(r.stdout, b"n,b\n1,2\n", r)

    def test_json_lines_input(self):
        r = self.r("--input-format", "jsonl", "--fields", "id,user.name", "--select", "user.name", "--rename", "user.name=who", "--format", "csv", stdin=b'{"id":1,"user":{"name":"ann"}}\n')
        self.assertEqual(r.stdout, b"who\nann\n", r)

    def test_a_join(self):
        self.s.write("l.csv", "id,v\n1,a\n2,b\n")
        self.s.write("r.csv", "id,w\n1,X\n3,Y\n")
        r = self.r("--join", "r.csv", "--on", "id", "--join-prefix", "r_", "--rename", "r_w=who,v=val", "--select", "id,v,r_w", "--format", "csv", "l.csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["id,val,who", "1,a,X"], r)
        # a collision is still the collision: nothing is renamed on the way (docs/joins.md)
        r = self.r("--join", "r.csv", "--on", "id=id", "--rename", "id=x", "l.csv")
        self.assertEqual(r.first_rule(), "join.column-collision", r)

    def test_the_awkward_labels_of_csv(self):
        r = self.r("--select", "a,b", "--rename", "a=x\\,y,b=q\"t", "--format", "csv", "f.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], '"x,y","q""t"', r)
        r = self.r("--select", "a", "--rename", "a=\\#1", "--format", "csv", "f.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], "#1", r)

    def test_the_label_of_the_rename_is_utf8_in_json_and_jsonl(self):
        r = self.r("--select", "a", "--rename", "a=日本", "--format", "jsonl", "f.csv")
        self.assertEqual(r.stdout.decode().splitlines()[0], '{"日本":"1"}')

    def test_explain_shows_the_renamed_columns_of_the_real_answer(self):
        for argv in (["--select", "a,c", "--rename", "a=x"], ["--group", "b", "--agg", "count,sum:a", "--rename", "sum:a=total,b=k"], ["--where", "a = 1", "--rename", "#3=z"]):
            real = self.r(*argv, "f.csv")
            ex = self.r(*argv, "--explain", "f.csv")
            self.assertEqual(ex.status, 0, ex)
            self.assertEqual(ex.data()["explain"]["output"]["columns"], real.data()["columns"], argv)
            self.assertEqual(validate(ex), [], ex)
        # and a refusal that the run makes is the explain's too
        for argv in (["--select", "a", "--rename", "zz=x"], ["--select", "a,a", "--rename", "a=x"], ["--select", "a", "--rename", "a=x,#1=y"], ["--select", "a", "--rename", "a"]):
            real = self.r(*argv, "f.csv")
            ex = self.r(*argv, "--explain", "f.csv")
            self.assertEqual((ex.status, ex.first_rule()), (real.status, real.first_rule()), argv)

    def test_the_rename_is_in_the_help_and_the_introspection(self):
        flags = {f["name"] for f in introspect()["flags"]}
        self.assertIn("--rename", flags)
        rules = {r["rule"] for r in introspect()["rules"]}
        self.assertTrue({"rename.syntax", "rename.unknown-target", "rename.ambiguous-target", "rename.repeated-target"} <= rules)


# ---- G-CORPUS-EQ: the flag form and the query form ------------------------------------------------------------------------------------------

def comparable(res):
    out, err = res.stdout, res.stderr
    try:
        d = json.loads(out.decode("utf-8"))
    except ValueError:
        d = None
    if isinstance(d, dict) and d.get("ok") is False:
        if d["error"]["rule"] in ("where.syntax", "query.syntax"):
            return res.status, b"SYNTAX", b""
        for e in [d["error"]] + d.get("errors", []):
            e.pop("repair", None)
        out = (json.dumps(d, sort_keys=True) + "\n").encode()
    if err.startswith((b"table: where.syntax:", b"table: query.syntax:")):
        return res.status, out, b"SYNTAX"
    return res.status, out, err


class Equivalence(unittest.TestCase):
    def test_random_plans_with_aliases_flags_query_and_the_reference_translation(self):
        rnd = random.Random(SEED + 3)
        import tempfile
        seen = {"runs": 0, "refusals": 0, "aliased": 0, "refused-by-the-reference": 0, "hand": 0}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.csv")
            for case in range(QPLANS):
                names, kinds, rows = query_gen.make_table(rnd)
                query_gen.write_csv(path, names, rows)
                plan = query_gen.make_plan(rnd, names, kinds, rows, many=True)
                labels = R.default_labels(plan, names)
                plan["renames"] = {}
                for i, new in R.make_renames(rnd, labels, names, allow_dup=True):
                    if rnd.random() < 0.7:
                        plan["renames"][i] = new
                if plan["renames"]:
                    seen["aliased"] += 1
                query = query_gen.query_form(rnd, plan, None)
                hand = query_gen.flag_form(plan)
                try:
                    ref_argv, _ = query_ref.translate(query)
                except query_ref.QueryError as e:
                    ref_argv = None
                    kind = e.kind
                shadowed = False
                if ref_argv is not None:
                    # the hand-written flags and the translation say the same unless an alias shadowed a header name in order by, or a label repeats (then the flags the query writes differ on purpose)
                    sw = lambda argv: sorted(argv[i:i + 2] for i in range(0, len(argv), 2)) if "--rows" not in argv else sorted(argv)
                    shadowed = sw(hand) != sw(ref_argv)
                    if not shadowed:
                        seen["hand"] += 1
                extras = [[], THREADS] if case % 4 == 0 else [[]]
                for extra in extras:
                    for fmt in ("json", "csv"):
                        q = run("--query", query, "--format", fmt, *extra, "f.csv", cwd=d)
                        seen["runs"] += 1
                        if ref_argv is None:
                            seen["refused-by-the-reference"] += 1
                            self.assertEqual(q.status, 2, (query, q))
                            rule = q.first_rule() if fmt == "json" else q.stderr.decode().split(":")[1].strip()
                            self.assertEqual(rule, {"mixed": "query.mixed-and-or"}.get(kind, "query." + kind), (query, q))
                            continue
                        f = run(*ref_argv, "--format", fmt, *extra, "f.csv", cwd=d)
                        self.assertEqual(comparable(q), comparable(f), "case %d\n  query: %r\n  ref flags: %r\n  table %r" % (case, query, ref_argv, names))
                        if not shadowed:
                            h = run(*hand, "--format", fmt, *extra, "f.csv", cwd=d)
                            self.assertEqual(comparable(q), comparable(h), "case %d\n  query: %r\n  hand flags: %r" % (case, query, hand))
                        if q.status != 0:
                            seen["refusals"] += 1
                # the explain documents are the same bytes
                if ref_argv is not None and case % 3 == 0:
                    qe = run("--query", query, "--explain", "f.csv", cwd=d)
                    fe = run(*ref_argv, "--explain", "f.csv", cwd=d)
                    self.assertEqual(comparable(qe), comparable(fe), query)
        report("query equivalence:", json.dumps(seen, sort_keys=True))
        self.assertGreater(seen["aliased"], QPLANS // 3, seen)
        self.assertGreater(seen["hand"], QPLANS // 4, seen)

    def test_the_front_end_against_the_reference_on_generated_selects(self):
        rnd = random.Random(SEED + 4)
        cols = ["a", "b", '"x y"', "#2", '"select"', "c"]
        aliases = ["x", "n", '"a b"', '"x,y"', '"#1"', "count", "sum", "id", '"select"', "select", "like", "5", "#3", "'q'", '""', '"é"', '"q""t"']
        import tempfile
        checked = {"accepted": 0, "syntax": 0, "other": 0}
        if True:
            self_ = Scratch()
            try:
                self_.write("f.csv", 'a,b,x y,select,c\n1,2,3,4,5\n6,7,8,9,10\n')
                for k in range(QPLANS):
                    items = []
                    for _ in range(rnd.randint(1, 3)):
                        it = rnd.choice(cols)
                        if rnd.random() < 0.7:
                            it += rnd.choice([" as ", " AS ", "  as  "]) + rnd.choice(aliases)
                        elif rnd.random() < 0.1:
                            it += " " + rnd.choice(aliases)
                        items.append(it)
                    q = "select " + ", ".join(items) + " from 'f.csv'"
                    if rnd.random() < 0.3:
                        q += " order by " + rnd.choice(cols + [a for a in aliases if a[0] != "'"])
                    try:
                        argv, src = query_ref.translate(q.encode("utf-8").decode("latin-1"))       # (the reference reads a byte as a character)
                        want = ("ok", [a.encode("latin-1").decode("utf-8") for a in argv])
                    except query_ref.QueryError as e:
                        want = (e.kind, e.offset, e.expected if e.kind in ("syntax", "group-mismatch") else e.feature)
                    r = run("--root", self_.dir, "--query", q)
                    if want[0] == "ok":
                        checked["accepted"] += 1
                        f = run("--root", self_.dir, *want[1], "f.csv")
                        self.assertEqual(comparable(r), comparable(f), q)
                    else:
                        checked["syntax" if want[0] == "syntax" else "other"] += 1
                        self.assertEqual(r.status, 2, (q, r))
                        e = r.error()
                        self.assertEqual(e["rule"], {"mixed": "query.mixed-and-or"}.get(want[0], "query." + want[0]), (q, r))
                        self.assertEqual(e["detail"]["offset"], want[1], (q, r))
                        if want[0] != "mixed":
                            self.assertEqual(e["detail"].get("expected", e["detail"].get("feature")), want[2], (q, r))
            finally:
                self_.cleanup()
        report("query front end:", json.dumps(checked))
        self.assertGreater(checked["accepted"], QPLANS // 5, checked)
        self.assertGreater(checked["syntax"], QPLANS // 20, checked)


class QueryPinned(Base):
    def test_the_worked_examples(self):
        cases = [
            ("select region as r, sum(units) as total from 'sales.csv' group by region order by total desc", ["r", "total"], ["north", "south"]),
            ("select region as r, count(*) as n from 'sales.csv' group by region order by region desc", ["r", "n"], ["south", "north"]),
            ("select units as u from 'sales.csv' where region = 'north' order by u::int desc", ["u"], ["7", "3"]),
            ("select units as region, region as units from 'sales.csv' order by region::int", ["region", "units"], ["3", "5", "7"]),
        ]
        for q, header, first in cases:
            r = self.r("--query", q, "--format", "csv")
            self.assertEqual(r.status, 0, (q, r))
            rows = parse_csv(r.stdout)
            self.assertEqual(rows[0], header, q)
            self.assertEqual([row[0] for row in rows[1:]], first, q)

    def test_awkward_aliases_round_trip_through_the_rename_the_query_writes(self):
        for alias in ("\\", "a\\b", "#5", "#", ",", "x,y", "x=y", "=", "'", "é", "a b", "\\,", "#\\"):
            name = '"' + alias.replace('"', '""') + '"'
            q = self.r("--query", "select units as %s, region from 'sales.csv' limit 1" % name, "--format", "csv")
            self.assertEqual(q.status, 0, (alias, q))
            rows = parse_csv(q.stdout)
            self.assertEqual(rows[0], [alias, "region"], (alias, q))
            self.assertEqual(rows[1], ["3", "north"], (alias, q))

    def test_two_aggregates_of_one_label_and_no_alias_are_main_s_first_match(self):
        # the golden of A1 holds this to main's bytes; here the answer: --sort finds the first column of the label (sum:units, the typed one), as it always did
        q = self.r("--query", "select region, sum(units::int), sum(units) from 'sales.csv' group by region order by sum(units)", "--format", "csv")
        f = self.r("--group", "region", "--agg", "sum:units:int,sum:units", "--sort", "sum:units", "--format", "csv", "sales.csv")
        self.assertEqual((q.status, q.stdout), (0, f.stdout), q)
        # with an alias on either it is ambiguous, and refused
        q = self.r("--query", "select region, sum(units::int) as t, sum(units) as t from 'sales.csv' group by region order by t")
        self.assertEqual(q.first_rule(), "query.group-mismatch", q)
        text = "select region, sum(units::int), sum(units) as \"sum:units\" from 'sales.csv' group by region order by sum(units)"
        q = self.r("--query", text)
        with self.assertRaises(query_ref.QueryError) as caught:
            query_ref.translate(text)
        self.assertEqual((q.first_rule(), q.error()["detail"]["offset"]), ("query.group-mismatch", caught.exception.offset), q)
        self.assertEqual(caught.exception.expected, query_ref.WHAT_SORT_AMBIGUOUS)

    def test_an_alias_is_not_seen_by_where_or_group_by(self):
        r = self.r("--query", "select units as u from 'sales.csv' where u = 3")
        self.assertEqual((r.first_rule(), r.status), ("column.unknown", 3), r)
        r = self.r("--query", "select region as r, count(*) from 'sales.csv' group by r")
        self.assertEqual(r.first_rule(), "query.group-mismatch", r)

    def test_as_is_required_and_select_star_takes_no_alias(self):
        for q in ("select units u from 'sales.csv'", "select * as x from 'sales.csv'"):
            r = self.r("--query", q)
            self.assertEqual((r.first_rule(), r.status), ("query.syntax", 2), (q, r))

    def test_the_alias_of_the_query_is_the_rename_of_the_flags_in_explain(self):
        q = self.r("--query", "select units as u from 'sales.csv'", "--explain")
        f = self.r("--select", "units", "--rename", "#1=u", "--explain", "sales.csv")
        self.assertEqual(q.status, 0, q)
        self.assertEqual(q.data()["explain"]["output"], f.data()["explain"]["output"])
        self.assertEqual(q.data()["explain"]["output"]["columns"], ["u"])

    def test_mixed_with_a_page_and_a_duplicate_alias(self):
        r = self.r("--query", "select units as u, units as u from 'sales.csv' limit 2", "--format", "csv")
        self.assertEqual(r.stdout.decode().splitlines(), ["u,u", "3,3", "5,5"], r)
        r = self.r("--query", "select units as u, units as u from 'sales.csv'", "--format", "jsonl")
        self.assertEqual((r.status, r.stderr.startswith(b"table: output.duplicate-column")), (2, True), r)


# ---- G-PAR ---------------------------------------------------------------------------------------------------------------------------

class Parallel(unittest.TestCase):
    def test_threads_by_chunks_give_the_bytes_of_one_thread(self):
        rnd = random.Random(SEED + 5)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rows = [[str(i), rnd.choice(["n", "s", "e", "w"]), str(rnd.randint(0, 999)), "c,%d" % (i % 7)] for i in range(400)]
            query_gen.write_csv(os.path.join(d, "f.csv"), ["id", "dir", "n", "note"], rows)
            plans = [
                ["--select", "id,note,n", "--rename", "id=key,note=memo"],
                ["--where", "dir = n", "--rename", "#1=k,#4=m"],
                ["--group", "dir", "--agg", "count,sum:n", "--rename", "sum:n=total,dir=d"],
                ["--group", "dir", "--agg", "count,sum:n", "--rename", "count=how many", "--sort", "-how many"],
                ["--select", "id,n", "--rename", "n=amount", "--order-by", "-n:int", "--top", "50"],
            ]
            for plan in plans:
                for fmt in ("json", "csv", "jsonl"):
                    base = run(*plan, "--format", fmt, "--limit", "100000", "--threads", "1", "f.csv", cwd=d)
                    self.assertEqual(base.status, 0, (plan, base))
                    for n in (2, 3, 4, 8, 16, 64):
                        for chunk in (1, 7, 64, 1000):
                            got = run(*plan, "--format", fmt, "--limit", "100000", "--threads", str(n), "--parallel-min-bytes", "0", "--chunk-bytes", str(chunk), "f.csv", cwd=d)
                            self.assertEqual((got.status, got.stdout, got.stderr), (base.status, base.stdout, base.stderr), (plan, fmt, n, chunk))


# ---- MCP -----------------------------------------------------------------------------------------------------------------------------

class Mcp(unittest.TestCase):
    def test_a_call_with_rename_is_the_cli_byte_for_byte(self):
        import shutil
        import tempfile
        import test_mcp
        root = tempfile.mkdtemp(prefix="mcp-rn-")
        try:
            with open(os.path.join(root, "data.csv"), "w") as f:
                f.write("id,note,n\n1,a,5\n2,b,6\n3,c,7\n4,d,8\n")
            test_mcp.BUILT.clear()              # (test_mcp's teardown removed the server it built, and this builds its own)
            arguments = {"file": "data.csv", "select": "id,note", "rename": "id=key", "limit": 3}
            direct = test_mcp.cli(root, arguments)
            self.assertEqual(direct.status, 0, direct)
            got = test_mcp.one(root, arguments)["result"]
            self.assertEqual(got["content"][0]["text"].encode(), direct.stdout)
            self.assertEqual(json.loads(direct.stdout)["data"]["columns"], ["key", "note"])
            # a refusal is the CLI's too
            bad = {"file": "data.csv", "select": "id", "rename": "zz=x"}
            direct = test_mcp.cli(root, bad)
            self.assertEqual(direct.first_rule(), "rename.unknown-target", direct)
            got = test_mcp.one(root, bad)["result"]
            self.assertEqual(got["content"][0]["text"].encode(), direct.stdout)
            self.assertTrue(got["isError"])
        finally:
            for built in test_mcp.BUILT.values():
                shutil.rmtree(built.parent, ignore_errors=True)
            test_mcp.BUILT.clear()
            shutil.rmtree(root, ignore_errors=True)


# ---- G-FUZZ ---------------------------------------------------------------------------------------------------------------------------

class Fuzz(Base):
    def test_no_trap_every_refusal_tagged_every_repair_accepted(self):
        rnd = random.Random(SEED + 6)
        rules = {r["rule"] for r in introspect()["rules"]}
        alphabet = [b"a", b"b", b"c", b"A", b"#", b"1", b"=", b",", b"\\", b" ", "é".encode(), b"\xff", b"\x00", b":", b"-", b"'", b'"', b"=,", b"#1=", b"\\\\", b"\\,", b"\\="]
        counts = {"ok": 0, "refused": 0, "repaired": 0}
        for k in range(SOUPS):
            raw = b"".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 14)))
            raw = raw.replace(b"\x00", b"0")
            for fmt in ([], ["--format", "csv"]):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")
                r = self.r("--select", "a,b,c", "--rename", text, *fmt, "f.csv")
                self.assertNotIn(r.status, TRAPS, (raw, r))
                if r.status == 0:
                    counts["ok"] += 1
                    continue
                counts["refused"] += 1
                if fmt:
                    self.assertTrue(r.stderr.startswith(b"table: "), r)
                    self.assertIn(r.stderr.decode().split(":")[1].strip(), rules, r)
                    continue
                self.assertEqual(validate(r), [], (raw, r))
                e = r.error()
                self.assertIn(e["rule"], rules, r)
                if e["rule"] == "rename.syntax":
                    self.assertTrue(0 <= e["detail"]["offset"] <= len(text.encode("utf-8", "surrogateescape")), (raw, r))
                if e["repair"] and e["repair"]["kind"] == "choose":
                    for opt in e["repair"]["options"]:
                        counts["repaired"] += 1
                        again = run_argv(opt["argv"])
                        self.assertEqual(again.status, 0, (raw, opt, again))
        report("fuzz:", json.dumps(counts))
        self.assertGreater(counts["refused"], SOUPS // 4, counts)
        self.assertGreater(counts["ok"], 0, counts)

    def test_query_soups_with_as(self):
        rnd = random.Random(SEED + 7)
        rules = {r["rule"] for r in introspect()["rules"]}
        words = ["select", "a", "b", "as", "x", '"x y"', ",", "from 'f.csv'", "count(*)", "group by a", "order by", "x", "desc", "(", ")", "5", "#1", "'s'", "where a = 1", "sum(a)", '"', "é", "\\"]
        seeds = ["select a as x, count(*) as n from 'f.csv' group by a order by n desc", "select a as x, b as y from 'f.csv' where a = '1' order by y desc limit 2",
                 "select b, a as b, c as \"c c\" from 'f.csv' order by b", "select a as \"x,y\", sum(a::int) as t from 'f.csv' group by a order by t", "select count(*) as n, min(c) as m from 'f.csv'",
                 "select a as a, b as b from 'f.csv' where a in ('1', '2') or b = 'y' order by a desc, b"]
        refused = ok = 0
        for k in range(SOUPS):
            if rnd.random() < 0.6:
                toks = rnd.choice(seeds).split(" ")
                for _ in range(rnd.randint(0, 3)):
                    i = rnd.randrange(len(toks))
                    r = rnd.random()
                    if r < 0.3 and len(toks) > 1:
                        toks.pop(i)
                    elif r < 0.6:
                        toks[i] = rnd.choice(words)
                    elif r < 0.8:
                        toks.insert(i, rnd.choice(words))
                    else:
                        j = rnd.randrange(len(toks))
                        toks[i], toks[j] = toks[j], toks[i]
                q = " ".join(toks)
            else:
                q = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 12)))
            r = self.r("--query", q)
            self.assertNotIn(r.status, TRAPS, (q, r))
            try:
                d = r.doc()
            except AssertionError:
                continue
            if r.status == 0:
                ok += 1
                continue
            refused += 1
            self.assertIn(d["error"]["rule"], rules, (q, r))
            off = d["error"].get("detail", {}).get("offset")
            if d["error"]["rule"].startswith("query.") and off is not None:
                self.assertLessEqual(off, len(q.encode()), (q, r))
            rep = d["error"].get("repair")
            if rep and rep["kind"] == "choose":
                for opt in rep["options"]:
                    again = run_argv(opt["argv"], cwd=self.cwd)
                    self.assertNotIn(again.status, TRAPS, (q, opt, again))
                    self.assertFalse((again.first_rule() or "").startswith("query.") if again.status and again.stdout.startswith(b"{") else False, (q, opt, again))
        report("fuzz queries:", ok, refused)
        self.assertGreater(refused, SOUPS // 4)
        self.assertGreater(ok, SOUPS // 20)


if __name__ == "__main__":
    unittest.main()
