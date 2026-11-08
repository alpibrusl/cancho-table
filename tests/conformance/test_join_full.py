"""F1a (docs/join-redesign.md section 9, issue #113): `--join-type full`, rows out, `--on PROBE=BUILD` pairs.

The gates, written before the code and each able to fail:

* O, the oracle: random tables joined by a Python definition that shares no code with the tool (a dict of lists, the rows of the left join in probe order, then each build row nothing matched
  in build order, probe cells empty), compared row for row as csv: duplicates, unmatched rows on both sides, empty keys, quoted cells with a comma and a line break, a composite key, typed keys.
* W, the whole `--where` is evaluated on the joined row (no pushdown): a condition on the probe side removes the build-only rows, `id = ''` keeps only them.
* P, a page is a window over the whole answer (`--limit`, `--from`), and the same bytes at 1 and 4 threads and from a pipe.
* B, `--max-join-rows` counts the rows of both phases, to the row.
* R, the refusals: what a full join does not take yet or does not take at all is `args.conflict`; a padded probe cell read as a number is refused and says it was padding.
"""

import csv
import io
import json
import random
import unittest

from harness import TRAPS, Scratch, binary, introspect, run

PH = ["id", "k", "pv"]
BH = ["bk", "v"]


def encode(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


def full_join(prows, brows, pairs):
    """The definition: the left join in probe order, then the build rows no probe row matched, in build order, with the probe cells empty. pairs: (probe column, build column)."""
    np_, nb = len(PH), len(BH)
    index = {}
    for j, b in enumerate(brows):
        index.setdefault(tuple(b[bi] for _, bi in pairs), []).append(j)
    out, hit = [], set()
    for p in prows:
        m = index.get(tuple(p[pi] for pi, _ in pairs))
        if m:
            for j in m:
                out.append(p + brows[j])
                hit.add(j)
        else:
            out.append(p + [""] * nb)
    for j, b in enumerate(brows):
        if j not in hit:
            out.append([""] * np_ + b)
    return out


def parse(stdout):
    return list(csv.reader(io.StringIO(stdout.decode("utf-8")), lineterminator="\n"))


KEYS = ["a", "b", "c", "", "007", "7", "x,y", "q\nr", 'say "hi"', "é"]


def table(rng, header, n, key_cols):
    rows = []
    for i in range(n):
        row = ["r%d_%d" % (rng.randrange(100), i)] * len(header)
        row = ["v%d" % rng.randrange(5) for _ in header]
        for c in key_cols:
            row[c] = rng.choice(KEYS)
        rows.append(row)
    return rows


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def put(self, prows, brows):
        self.s.write("p.csv", encode(PH, prows))
        self.s.write("b.csv", encode(BH, brows))

    def full(self, *extra, on="k=bk", stdin=None, file="p.csv", fmt="csv"):
        a = ["--root", str(self.s.dir), "--join", "b.csv", "--on", on, "--join-type", "full", "--format", fmt, *[str(x) for x in extra]]
        if file is not None:
            a.append(file)
        return run(*a, stdin=stdin)


class Oracle(Base):
    def test_random_tables_equal_the_definition(self):
        rng = random.Random(1301)
        for case in range(120):
            prows = table(rng, PH, rng.randrange(0, 9), [1])
            brows = table(rng, BH, rng.randrange(0, 9), [0])
            self.put(prows, brows)
            r = self.full()
            self.assertEqual(r.status, 0, (case, r.stderr[:200]))
            self.assertEqual(parse(r.stdout), [PH + BH] + full_join(prows, brows, [(1, 0)]), case)

    def test_two_pairs(self):
        rng = random.Random(1302)
        for case in range(40):
            prows = table(rng, PH, rng.randrange(0, 8), [0, 1])
            brows = table(rng, BH, rng.randrange(0, 8), [0, 1])
            self.put(prows, brows)
            r = self.full(on="id=bk,k=v")
            self.assertEqual(r.status, 0, (case, r.stderr[:200]))
            self.assertEqual(parse(r.stdout), [PH + BH] + full_join(prows, brows, [(0, 0), (1, 1)]), case)

    def test_typed_keys_compare_as_numbers(self):
        # as :int, 7 and 007 are one key, and the build row nothing matched still comes back after the probe rows
        self.put([["1", "7", "p"], ["2", "8", "p"], ["3", "9", "p"]], [["007", "b1"], ["8", "b2"], ["10", "b3"]])
        r = self.full(on="k=bk:int")
        self.assertEqual(r.status, 0, r.stderr[:300])
        self.assertEqual(parse(r.stdout), [PH + BH, ["1", "7", "p", "007", "b1"], ["2", "8", "p", "8", "b2"], ["3", "9", "p", "", ""], ["", "", "", "10", "b3"]])
        # as text they are two keys
        r = self.full(on="k=bk")
        self.assertEqual(parse(r.stdout)[1:], full_join([["1", "7", "p"], ["2", "8", "p"], ["3", "9", "p"]], [["007", "b1"], ["8", "b2"], ["10", "b3"]], [(1, 0)]))

    def test_empty_inputs(self):
        self.put([], [["a", "x"], ["b", "y"]])
        self.assertEqual(parse(self.full().stdout), [PH + BH, ["", "", "", "a", "x"], ["", "", "", "b", "y"]])
        self.put([["1", "a", "p"]], [])
        self.assertEqual(parse(self.full().stdout), [PH + BH, ["1", "a", "p", "", ""]])
        self.put([], [])
        self.assertEqual(parse(self.full().stdout), [PH + BH])

    def test_an_empty_key_matches_an_empty_key_on_both_sides(self):
        self.put([["1", "", "p"]], [["", "x"], ["a", "y"]])
        self.assertEqual(parse(self.full().stdout), [PH + BH, ["1", "", "p", "", "x"], ["", "", "", "a", "y"]])


class Where(Base):
    def setUp(self):
        super().setUp()
        self.p = [["1", "a", "p1"], ["2", "z", "p2"], ["3", "a", "p3"]]
        self.b = [["a", "x"], ["q", "w"], ["r", "u"]]
        self.put(self.p, self.b)
        self.all = full_join(self.p, self.b, [(1, 0)])

    def test_a_probe_side_condition_is_evaluated_after_the_join(self):
        # no pushdown: the build rows that matched a removed probe row are still matched, so they do not come back as build-only rows
        r = self.full("--where", "pv = 'p1'")
        self.assertEqual(parse(r.stdout), [PH + BH] + [row for row in self.all if row[2] == "p1"])
        # and the unmatched build rows are the ones the join says, whatever the condition removed
        r = self.full("--where", "id = ''")
        self.assertEqual(parse(r.stdout), [PH + BH] + [row for row in self.all if row[0] == ""])
        self.assertEqual(len(parse(r.stdout)) - 1, 2)

    def test_explain_says_full_and_that_nothing_is_moved_before_the_join(self):
        r = self.full("--explain", "--where", "pv = 'p1'", fmt="json")
        e = json.loads(r.stdout)["data"]["explain"]["join"]
        self.assertEqual(e["type"], "full")
        self.assertEqual((e["where"]["probe_before_join"], e["where"]["build_before_build"]), ([], []))


class Pages(Base):
    def setUp(self):
        super().setUp()
        rng = random.Random(1303)
        self.p = table(rng, PH, 30, [1])
        self.b = table(rng, BH, 30, [0])
        self.put(self.p, self.b)
        self.all = full_join(self.p, self.b, [(1, 0)])

    def test_a_page_is_a_window_over_both_phases(self):
        n = len(self.all)
        for start, size in ((0, 5), (3, 7), (n - 4, 10), (len(self.p) - 2, 6), (n, 3)):
            r = self.full("--from", start, "--limit", size)
            self.assertEqual(r.status, 0, (start, size))
            self.assertEqual(parse(r.stdout)[1:], self.all[start:start + size], (start, size))

    def test_threads_and_a_pipe_give_the_same_bytes(self):
        want = self.full().stdout
        self.assertEqual(self.full("--threads", 4, "--parallel-min-bytes", 0, "--chunk-bytes", 64).stdout, want)
        piped = self.full(stdin=(self.s.dir / "p.csv").read_bytes(), file=None)
        self.assertEqual(piped.stdout, want)

    def test_a_page_that_is_full_inside_the_second_phase_stops_it(self):
        # four build rows nothing matches: a page of one row starting at the first of them reads one more row to know that there is more, and no further one, so a bound that allows
        # exactly that many rows holds (the rows after the page are not produced)
        self.put([["1", "a", "p"]], [["a", "x"], ["q", "w"], ["r", "u"], ["s", "v"], ["t", "z"]])
        first = 1
        r = self.full("--from", first, "--limit", 1, "--max-join-rows", first + 2, fmt="json")
        self.assertEqual(r.status, 0, r.stdout[:300])
        self.assertEqual(json.loads(r.stdout)["data"]["rows"], [["", "", "", "q", "w"]])

    def test_max_join_rows_counts_both_phases_to_the_row(self):
        n = len(self.all)
        self.assertEqual(self.full("--max-join-rows", n).status, 0)
        r = self.full("--max-join-rows", n - 1, fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "limit.too-many-join-rows"))


class Shared(Base):
    """F1c: a key column that both files name alike (`--on k`) is shown once; for a build-only row it holds the --join file's cell (the coalesce of docs/join-redesign.md section 4)."""

    PH2 = ["id", "k", "pv"]
    BH2 = ["k", "v"]

    def put2(self, prows, brows):
        self.s.write("p.csv", encode(self.PH2, prows))
        self.s.write("b.csv", encode(self.BH2, brows))

    def joined(self, *extra, on="k", fmt="csv"):
        return run("--root", str(self.s.dir), "--join", "b.csv", "--on", on, "--join-type", "full", "--format", fmt, *[str(x) for x in extra], "p.csv")

    @staticmethod
    def definition(prows, brows):
        """The answer's columns are id, k, pv, v: the left join in probe order, then each build row nothing matched, its key in the one key column."""
        out, hit = [], set()
        for p in prows:
            m = [j for j, b in enumerate(brows) if b[0] == p[1]]
            for j in m:
                out.append(p + [brows[j][1]])
                hit.add(j)
            if not m:
                out.append(p + [""])
        for j, b in enumerate(brows):
            if j not in hit:
                out.append(["", b[0], "", b[1]])
        return out

    def test_the_key_is_shown_once_and_a_build_only_row_holds_the_build_cell(self):
        self.put2([["1", "a", "p"], ["2", "b", "q"]], [["a", "x"], ["d", "y"]])
        r = self.joined()
        self.assertEqual(r.status, 0, r.stderr[:300])
        self.assertEqual(parse(r.stdout), [["id", "k", "pv", "v"], ["1", "a", "p", "x"], ["2", "b", "q", ""], ["", "d", "", "y"]])

    def test_random_tables_equal_the_definition(self):
        rng = random.Random(1311)
        for case in range(120):
            prows = table(rng, self.PH2, rng.randrange(0, 9), [1])
            brows = table(rng, self.BH2, rng.randrange(0, 9), [0])
            self.put2(prows, brows)
            r = self.joined()
            self.assertEqual(r.status, 0, (case, r.stderr[:200]))
            self.assertEqual(parse(r.stdout), [["id", "k", "pv", "v"]] + self.definition(prows, brows), case)

    def test_a_typed_key_shows_the_build_spelling_for_a_build_only_row(self):
        self.put2([["1", "7", "p"]], [["007", "b1"], ["10", "b2"]])
        r = self.joined(on="k:int")
        self.assertEqual(r.status, 0, r.stderr[:300])
        self.assertEqual(parse(r.stdout), [["id", "k", "pv", "v"], ["1", "7", "p", "b1"], ["", "10", "", "b2"]])

    def test_a_shared_pair_beside_a_pair_with_two_names(self):
        self.s.write("p.csv", encode(["k", "m", "pv"], [["a", "1", "p"], ["b", "2", "q"]]))
        self.s.write("b.csv", encode(["k", "n", "v"], [["a", "1", "x"], ["a", "9", "y"], ["z", "2", "w"]]))
        r = run("--root", str(self.s.dir), "--join", "b.csv", "--on", "k,m=n", "--join-type", "full", "--format", "csv", "p.csv")
        self.assertEqual(r.status, 0, r.stderr[:300])
        self.assertEqual(parse(r.stdout), [["k", "m", "pv", "n", "v"], ["a", "1", "p", "1", "x"], ["b", "2", "q", "", ""], ["a", "", "", "9", "y"], ["z", "", "", "2", "w"]])

    def test_two_shared_pairs_are_both_coalesced(self):
        self.s.write("p.csv", encode(["k", "j", "pv"], [["a", "1", "p"]]))
        self.s.write("b.csv", encode(["k", "j", "v"], [["a", "1", "x"], ["d", "4", "y"]]))
        r = run("--root", str(self.s.dir), "--join", "b.csv", "--on", "k,j", "--join-type", "full", "--format", "csv", "p.csv")
        self.assertEqual(r.status, 0, r.stderr[:300])
        self.assertEqual(parse(r.stdout), [["k", "j", "pv", "v"], ["a", "1", "p", "x"], ["d", "4", "", "y"]])

    def test_where_sees_the_coalesced_key(self):
        self.put2([["1", "a", "p"]], [["a", "x"], ["d", "y"], ["e", "z"]])
        r = self.joined("--where", "k = d")
        self.assertEqual(parse(r.stdout), [["id", "k", "pv", "v"], ["", "d", "", "y"]])
        # the key cell of a build-only row is not padding: a typed read of it that fails is not said to be
        self.put2([["1", "7", "p"]], [["7", "x"], ["q", "y"]])
        r = self.joined("--where", "k:int > 0", fmt="json")
        self.assertEqual(r.status, 8)
        self.assertFalse(r.error()["detail"].get("padded"), r.stdout[:300])

    def test_pages_and_threads_keep_the_bytes(self):
        rng = random.Random(1312)
        prows = table(rng, self.PH2, 30, [1])
        brows = table(rng, self.BH2, 30, [0])
        self.put2(prows, brows)
        whole = self.joined().stdout
        self.assertEqual(self.joined("--threads", 4, "--parallel-min-bytes", 0).stdout, whole)
        data = parse(whole)[1:]
        page = parse(self.joined("--limit", 7, "--from", len(data) - 6).stdout)
        self.assertEqual(page[1:], data[len(data) - 6:][:7])


class Refusals(Base):
    def setUp(self):
        super().setUp()
        self.put([["1", "a", "p"]], [["a", "x"]])

    def rule(self, *extra, on="k=bk"):
        r = self.full(*extra, on=on, fmt="json")
        return r.status, r.first_rule()

    def test_what_a_full_join_does_not_take_is_a_conflict(self):
        self.assertEqual(self.rule("--join-where", "v = 'x'"), (2, "args.conflict"))
        self.assertEqual(self.rule("--join-sorted"), (2, "args.conflict"))
        self.assertEqual(self.rule("--group", "id"), (2, "args.conflict"))
        self.assertEqual(self.rule("--agg", "count"), (2, "args.conflict"))
        self.assertEqual(self.rule("--order-by", "id"), (2, "args.conflict"))
        self.assertEqual(self.rule("--input-format", "jsonl", "--fields", "k"), (2, "args.conflict"))

    def test_nothing_is_given_for_the_build_rows_after_a_refusal(self):
        # a probe row of another width is a refusal of the whole answer: the build rows nothing matched are not given as if the probe had been read
        self.s.write("rag.csv", "id,k,pv\n1,a,p\n2,b\n")
        self.s.write("b.csv", "bk,v\na,x\nq,w\n")
        r = self.full(file="rag.csv")
        self.assertEqual(r.status, 8)
        self.assertNotIn(b"q,w", r.stdout)
        # the same when the probe is cut by --max-rows
        self.put([["1", "a", "p"], ["2", "b", "p"], ["3", "c", "p"]], [["a", "x"], ["q", "w"]])
        r = self.full("--max-rows", 2)
        self.assertEqual(r.status, 8)
        self.assertNotIn(b"q,w", r.stdout)

    def test_a_padded_probe_cell_read_as_a_number_is_refused_and_says_so(self):
        self.put([["1", "a", "p"]], [["a", "x"], ["q", "w"]])
        r = self.full("--where", "id:int > 0", fmt="json")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        self.assertTrue(r.error()["detail"]["padded"])
        # the guard the refusal teaches, and the build-only row it removes
        r = self.full("--where", "id != '' and id:int > 0")
        self.assertEqual(parse(r.stdout)[1:], [["1", "a", "p", "a", "x"]])

    def test_the_kind_of_the_flag_names_full(self):
        f = {x["name"]: x for x in introspect()["flags"]}
        self.assertEqual(f["--join-type"]["kind"], "choice:inner/left/semi/anti/full")


if __name__ == "__main__":
    unittest.main()
