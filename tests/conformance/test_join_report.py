"""F2 (docs/join-redesign.md section 4, issue #115): `--report` over a join stays `args.conflict`, and its repair is the first of two calls: the join written to `joined.csv` beneath `--root` with `--out`, then the report on that file.

The gates, written before the code and each able to fail:

* O, the oracle: the repair's argv, run, writes the join as csv (the bytes the same call gives with `--format csv`), and the report of that file equals the report of the same csv given on standard input.
* N, each case where the two calls cannot be decided from the arguments has a refusal with `repair.kind` none and a reason that names it (no `--root`, `--out` or `--format` given, a flag of rows, groups or a page, another flag refused too).
* F, the spellings: `--report=types`, `--join=FILE`, a `--` before the file, `--report dates`, a delimiter.
* W, the repair overwrites nothing: run twice, the second is `out.exists`.
"""

import csv
import io
import json
import random
import unittest

from harness import Scratch, run, run_argv

PH = ["id", "k", "pv"]
BH = ["bk", "v"]
KEYS = ["a", "b", "c", "", "007", "7", "x,y", 'say "hi"']


def encode(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.root = ["--root", str(self.s.dir)]
        self.s.write("p.csv", encode(PH, [["1", "a", "x"], ["2", "b", "y"], ["3", "c", ""], ["4", "z", "w"]]))
        self.s.write("b.csv", encode(BH, [["a", "10"], ["b", "20"], ["a", "30"], ["q", "40"]]))

    def tearDown(self):
        self.s.cleanup()

    def refused(self, *extra, report="types", root=True, file="p.csv"):
        a = [*(self.root if root else []), "--join", "b.csv", "--on", "k=bk", "--report", report, *[str(x) for x in extra]]
        if file is not None:
            a.append(file)
        return run(*a)

    def repair(self, r):
        found = [x for x in r.doc()["errors"] if x["rule"] == "args.conflict" and x["detail"].get("flags") == "--join --report"]
        self.assertEqual((r.status, len(found)), (2, 1), r.stdout[:300])
        return found[0]["repair"]


class Oracle(Base):
    def test_the_repair_writes_the_join_and_the_report_of_it_is_the_report_of_the_csv(self):
        rng = random.Random(1501)
        for case in range(25):
            prows = [["%d" % i, rng.choice(KEYS), rng.choice(["", "1", "2.5", "t"])] for i in range(rng.randrange(0, 9))]
            brows = [[rng.choice(KEYS), rng.choice(["", "7", "x"])] for _ in range(rng.randrange(0, 7))]
            self.s.write("p.csv", encode(PH, prows))
            self.s.write("b.csv", encode(BH, brows))
            for report in ("types", "dates"):
                (self.s.dir / "joined.csv").unlink(missing_ok=True)
                rep = self.repair(self.refused(report=report))
                self.assertEqual(rep["kind"], "retry", (case, rep))
                first = run_argv(rep["argv"])
                self.assertEqual(first.status, 0, (case, first.stderr))
                self.assertEqual(first.doc()["data"]["out"]["path"], "joined.csv")
                want = run(*self.root, "--join", "b.csv", "--on", "k=bk", "--format", "csv", "p.csv").stdout
                self.assertEqual((self.s.dir / "joined.csv").read_bytes(), want, case)
                second = run(*self.root, "--report", report, "joined.csv")
                piped = run("--report", report, "-", stdin=want)
                self.assertEqual(second.status, piped.status, (case, report, second.stdout[:200], piped.stdout[:200]))
                if second.status == 0:
                    self.assertEqual(second.data(), piped.data(), (case, report))

    def test_the_hint_names_the_second_call_and_the_file(self):
        e = self.refused().error()
        self.assertIn("joined.csv", e["hint"])
        self.assertIn("--report types joined.csv", e["hint"])
        self.assertIn("--root %s" % self.s.dir, e["hint"])
        d = self.refused(report="dates").error()
        self.assertIn("--report dates joined.csv", d["hint"])
        self.assertIn("--report dates describes", d["message"])

    def test_the_repair_overwrites_nothing(self):
        rep = self.repair(self.refused())
        self.assertEqual(run_argv(rep["argv"]).status, 0)
        before = (self.s.dir / "joined.csv").read_bytes()
        again = run_argv(rep["argv"])
        self.assertEqual(again.status, 2)
        self.assertIn("out.exists", again.stderr.decode())
        self.assertEqual((self.s.dir / "joined.csv").read_bytes(), before)


class Spellings(Base):
    def test_equals_spellings_and_a_terminator(self):
        for a in (
            [*self.root, "--join=b.csv", "--on=k=bk", "--report=types", "p.csv"],
            [*self.root, "--join", "b.csv", "--on", "k=bk", "--report=types", "--", "p.csv"],
            ["--report", "types", *self.root, "--join", "b.csv", "--on", "k=bk", "p.csv"],
        ):
            with self.subTest(a):
                (self.s.dir / "joined.csv").unlink(missing_ok=True)
                rep = self.repair(run(*a))
                argv = rep["argv"]
                self.assertNotIn("--report", argv)
                self.assertFalse([x for x in argv if x.startswith("--report")])
                self.assertEqual(argv[-2:] if "--" not in argv else argv[argv.index("--") - 2:argv.index("--")], ["--format=csv", "--out=joined.csv"])
                self.assertEqual(run_argv(argv).status, 0, argv)
                self.assertTrue((self.s.dir / "joined.csv").exists())

    def test_a_delimiter_goes_to_the_second_call_in_the_hint(self):
        self.s.write("p.tsv", "id\tk\n1\ta\n")
        self.s.write("b.tsv", "bk\tv\na\t5\n")
        r = run(*self.root, "--delimiter", "tab", "--join", "b.tsv", "--on", "k=bk", "--report", "types", "p.tsv")
        e = r.error()
        self.assertIn("--delimiter tab --report types joined.csv", e["hint"])
        self.assertEqual(run_argv(e["repair"]["argv"]).status, 0)
        self.assertEqual(run(*self.root, "--delimiter", "tab", "--report", "types", "joined.csv").status, 0)


class Nothing(Base):
    def none(self, r, word):
        rep = self.repair(r)
        self.assertEqual(rep["kind"], "none")
        self.assertIn(word, rep["reason"])

    def test_no_root(self):
        self.none(run("--join", "b.csv", "--on", "k=bk", "--report", "types", str(self.s.dir / "p.csv")), "--root")

    def test_out_or_format_given(self):
        self.none(self.refused("--out", "x.csv"), "--out or --format")
        self.none(self.refused("--format", "json"), "--out or --format")

    def test_a_flag_of_rows_groups_or_a_page(self):
        for extra in (["--select", "id"], ["--where", "id != ''"], ["--limit", "5"], ["--from", "1"], ["--max-bytes", "9999"], ["--group", "k"], ["--agg", "count"], ["--order-by", "id"], ["--top", "3"], ["--rename", "id=x"]):
            with self.subTest(extra):
                self.none(self.refused(*extra), "rows, groups or a page")

    def test_another_flag_refused_too(self):
        self.none(self.refused("--max-join-rows", "0"), "refused too")

    def test_the_call_without_a_join_is_as_it_was(self):
        r = run(*self.root, "--report", "types", "p.csv")
        self.assertEqual(r.status, 0)


if __name__ == "__main__":
    unittest.main()
