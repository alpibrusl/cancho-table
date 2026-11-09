"""F0b (docs/join-redesign.md section 7, issue #114): `--join-type right` is refused as `join.type-unsupported`, and where the call allows it the repair is the exchanged call.

The gates, written before the code and each able to fail:

* O, the oracle: for random tables with duplicate, empty and unmatched keys, the rows of the repair's argv, run, equal the right join computed by a Python definition that shares no code with the tool
  (`B left join A`: for each row of B in B's order the matching rows of A in A's order, and a row of B nothing matched once with the cells of A empty), compared by column name, for text and typed keys.
* N, each case where the exchange is not the same question has a refusal with `repair.kind` none and a reason that names it, and the rule tag and exit status are the refusal's.
* F, the `=` spelling of the flags, the pages and the other flags of the call go through to the repair as they were.
"""

import csv
import io
import json
import random
import unittest

from harness import Scratch, introspect, run, run_argv

PH = ["id", "k", "pv"]
BH = ["bk", "v"]
KEYS = ["a", "b", "c", "", "007", "7", "x,y", "q\nr", 'say "hi"', "é"]


def encode(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


def parse(stdout):
    return list(csv.reader(io.StringIO(stdout.decode("utf-8")), lineterminator="\n"))


def right_join(prows, brows, pairs):
    """`A right join B`: the rows of `B left join A`, for each row of B in B's order, with A's cells empty for a row nothing matched. pairs: (A column, B column). Cells of B first, then A's."""
    out = []
    for b in brows:
        m = [p for p in prows if all(p[pi] == b[bi] for pi, bi in pairs)]
        if m:
            for p in m:
                out.append(b + p)
        else:
            out.append(b + [""] * len(PH))
    return out


def table(rng, header, n, key_cols):
    rows = []
    for i in range(n):
        row = ["v%d" % rng.randrange(5) for _ in header]
        for c in key_cols:
            row[c] = rng.choice(KEYS)
        rows.append(row)
    return rows


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.root = ["--root", str(self.s.dir)]

    def tearDown(self):
        self.s.cleanup()

    def put(self, prows, brows):
        self.s.write("p.csv", encode(PH, prows))
        self.s.write("b.csv", encode(BH, brows))

    def refused(self, *extra, on="k=bk", file="p.csv", fmt="json"):
        a = [*self.root, "--join", "b.csv", "--on", on, "--join-type", "right", "--format", fmt, *[str(x) for x in extra]]
        if file is not None:
            a.append(file)
        return run(*a)

    @staticmethod
    def csv_of(argv):
        """The repair with its answer as csv (the refusal was read as JSON, so the repair carries --format json)."""
        out = list(argv)
        out[out.index("--format") + 1] = "csv" if "--format" in out else None
        return out

    def repair(self, r):
        e = r.error()
        self.assertEqual(e["rule"], "join.type-unsupported")
        return e["repair"]


class Oracle(Base):
    def test_the_repair_runs_to_the_right_join(self):
        rng = random.Random(1401)
        for case in range(80):
            prows = table(rng, PH, rng.randrange(0, 9), [1])
            brows = table(rng, BH, rng.randrange(0, 9), [0])
            self.put(prows, brows)
            r = self.refused()
            self.assertEqual(r.status, 2)
            rep = self.repair(r)
            self.assertEqual(rep["kind"], "retry", (case, rep))
            again = run_argv(self.csv_of(rep["argv"]))
            self.assertEqual(again.status, 0, (case, again.stderr[:300]))
            got = parse(again.stdout)
            self.assertEqual(got[0], BH + PH, case)
            self.assertEqual(got[1:], right_join(prows, brows, [(1, 0)]), case)

    def test_two_pairs_and_a_typed_key(self):
        rng = random.Random(1402)
        for case in range(30):
            prows = table(rng, PH, rng.randrange(0, 8), [0, 1])
            brows = table(rng, BH, rng.randrange(0, 8), [0, 1])
            self.put(prows, brows)
            rep = self.repair(self.refused(on="id=bk,k=v"))
            again = run_argv(self.csv_of(rep["argv"]))
            self.assertEqual(again.status, 0, (case, again.stderr[:300]))
            self.assertEqual(parse(again.stdout)[1:], right_join(prows, brows, [(0, 0), (1, 1)]), case)
        self.put([["1", "7", "p"], ["2", "8", "p"]], [["007", "b1"], ["8", "b2"], ["10", "b3"]])
        rep = self.repair(self.refused(on="k=bk:int"))
        again = run_argv(self.csv_of(rep["argv"]))
        self.assertEqual(parse(again.stdout), [BH + PH, ["007", "b1", "1", "7", "p"], ["8", "b2", "2", "8", "p"], ["10", "b3", "", "", ""]])

    def test_a_shared_name_key_is_exchanged_as_it_is(self):
        self.s.write("p.csv", encode(["k", "pv"], [["a", "1"], ["b", "2"]]))
        self.s.write("b.csv", encode(["k", "v"], [["b", "x"], ["d", "y"]]))
        r = run(*self.root, "--join", "b.csv", "--on", "k", "--join-type", "right", "--format", "json", "p.csv")
        again = run_argv(self.csv_of(self.repair(r)["argv"]))
        self.assertEqual(again.status, 0, again.stderr[:300])
        self.assertEqual(parse(again.stdout), [["k", "v", "pv"], ["b", "x", "2"], ["d", "y", ""]])


class Spelling(Base):
    def test_the_equals_spelling_and_the_rest_of_the_call_go_through(self):
        self.put([["1", "a", "p"], ["2", "b", "q"], ["3", "a", "r"]], [["a", "x"], ["a", "y"], ["z", "w"]])
        r = run(*self.root, "--join=b.csv", "--on=k=bk", "--join-type=right", "--limit", "2", "--from=1", "--format=json", "p.csv")
        argv = self.repair(r)["argv"]
        self.assertIn("--join=p.csv", argv)
        self.assertIn("--on=bk=k", argv)
        self.assertIn("--join-type=left", argv)
        self.assertEqual(argv[-1], "b.csv")
        self.assertEqual([argv[argv.index("--limit") + 1], "--from=1" in argv], ["2", True])
        again = run_argv(argv)
        self.assertEqual(again.status, 0, again.stderr[:300])
        self.assertEqual(again.data()["row_count"], 2)

    def test_the_where_and_the_select_keep_naming_their_columns(self):
        self.put([["1", "a", "p"], ["2", "b", "q"]], [["a", "x"], ["z", "w"]])
        r = self.refused("--select", "v,id", "--where", "v != ''")
        again = run_argv(self.csv_of(self.repair(r)["argv"]))
        self.assertEqual(again.status, 0, again.stderr[:300])
        self.assertEqual(parse(again.stdout)[0], ["v", "id"])


class Nothing(Base):
    """N: where the exchanged call is not the same question, or cannot be written from the arguments, the refusal has no repair and says why."""

    def assertNone(self, r, fragment):
        self.assertEqual((r.status, r.first_rule()), (2, "join.type-unsupported"))
        rep = self.repair(r)
        self.assertEqual(rep["kind"], "none", rep)
        self.assertIn(fragment, rep["reason"])

    def test_every_case(self):
        self.put([["1", "a", "p"]], [["a", "x"]])
        self.assertNone(run(*self.root, "--join", "b.csv", "--on", "k=bk", "--join-type", "right", "--format", "json", stdin=b"id,k,pv\n1,a,p\n"), "standard input")
        self.assertNone(self.refused("--join-prefix", "b_"), "--join-prefix")
        self.assertNone(self.refused("--join-where", "v = 'x'"), "--join-where")
        self.assertNone(self.refused("--join-sorted"), "--join-sorted")
        self.assertNone(self.refused("--input-format", "csv"), "reader flag")
        self.assertNone(self.refused("--join-delimiter", "tab"), "reader flag")
        self.assertNone(self.refused("--where", "id = '#1'"), "#")
        self.assertNone(self.refused("--select", "#1"), "#")
        self.s.write("p2.csv", encode(["k", "pv"], [["a", "1"]]))
        self.s.write("b2.csv", encode(["k", "v"], [["a", "x"]]))
        r = run(*self.root, "--join", "b2.csv", "--on", "k:int", "--join-type", "right", "--format", "json", "p2.csv")
        self.assertNone(r, "typed shared-name")
        # only this refusal is told when another flag is refused too (F0a), and the exchanged call would keep that one, so nothing is offered
        self.assertNone(self.refused("--max-rows", "many"), "another flag")
        # a pair list that is empty or malformed is refused when the call runs, and the exchange would keep it
        self.assertNone(self.refused(on=""), "another flag")
        self.assertNone(self.refused(on="k=bk:int:int"), "empty or refused")
        self.assertNone(self.refused(on="k=bk:nope"), "empty or refused")
        # an operand written like a flag (it follows --) cannot be the value of --join
        self.s.write("-p.csv", encode(PH, [["1", "a", "p"]]))
        self.assertNone(run(*self.root, "--join", "b.csv", "--on", "k=bk", "--join-type", "right", "--format", "json", "--", "-p.csv"), "like a flag")

    def test_the_other_values_keep_their_refusal(self):
        self.put([["1", "a", "p"]], [["a", "x"]])
        r = run(*self.root, "--join", "b.csv", "--on", "k=bk", "--join-type", "outer", "--format", "json", "p.csv")
        self.assertEqual(r.first_rule(), "args.bad-value")


if __name__ == "__main__":
    unittest.main()
