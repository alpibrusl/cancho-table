"""docs/computed-columns.md, the gates of the stage `--compute` through the built binary.

* G-ORACLE: for random files (typed columns, quoted cells with commas and newlines) and random definitions, every row of the answer is the file's cells and then the
  values a Python tree of integers computes (the reference of scripts/cexpr_golden.py, which shares no code with the tool), as CSV and as JSON;
* G-PAGE: `--limit` and `--from` give the slice of the whole answer, with `truncated` and `next`;
* G-REFUSAL: each rule, its exit status, the offset or the row, and no payload (no cell is in a message);
* G-COMBINE: the flags that shape rows or groups, JSON lines, standard input and a reader option are refused with their rule.
"""

import csv
import io
import json
import os
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import cexpr_golden as G  # noqa: E402  (its main() runs under __main__ only)
from harness import Scratch, run  # noqa: E402

HEADER = ["name", "a", "b", "c", "d", "e", "f"]


def make_file(rng, rows):
    kinds = [("int", 0) if rng.random() < 0.4 else ("dec", rng.randint(1, 6)) for _ in range(6)]
    out = [HEADER]
    values = []
    for i in range(rows):
        row = [rng.choice(["x", "y, z", "he said \"hi\"", "two\nlines", "plain"])]
        vals = []
        for kind, s in kinds:
            if kind == "int":
                v = rng.choice([0, 1, -1, rng.randint(-10**12, 10**12)])
                row.append(str(v))
                vals.append((v, 0))
            else:
                v = rng.randint(-10**10, 10**10)
                row.append(G.text(v, s))
                vals.append((v, s))
        out.append(row)
        values.append(vals)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out)
    return kinds, buf.getvalue(), out[1:], values


def tree_for(rng, kinds):
    cols = [(k, s) for k, s in kinds]
    return G.gen(rng, rng.randint(1, 3), cols)


def expected(tree, vals):
    G.static_scale(tree)
    v, s = G.ev(tree, vals)
    return G.text(v, s)


class Compute(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.cleanup()

    def test_random_files_and_definitions_equal_the_reference(self):
        rng = random.Random(7)
        checked = 0
        for _ in range(60):
            kinds, text, rows, values = make_file(rng, rng.randint(1, 12))
            trees = [tree_for(rng, kinds) for _ in range(rng.randint(1, 3))]
            try:
                for t in trees:
                    G.static_scale(t)
            except G.BadScale:
                continue
            defs = ";".join(f"r{i}=" + G.render(t, rng) for i, t in enumerate(trees))
            # the file's own header names are a..f: the reference's columns are a..f at positions 1..6 of the file
            header = ["name"] + list("abcdef")
            want_rows, failed = [], None
            for ri, vals in enumerate(values):
                try:
                    want_rows.append(rows[ri] + [expected(t, vals) for t in trees])
                except (G.Overflow, G.DivZero):
                    failed = ri
                    break
            # column positions in the reference: a is column 0 of the six, which is position 2 of the file; the renderer's #N spelling is the six's
            defs = defs  # `#N` in a definition counts the file's columns: the six are at +1
            defs = self._shift_positions(defs)
            self.s.write("t.csv", text)
            r = run("--root", self.s.dir, "--compute", defs, "--format", "csv", "t.csv")
            if failed is not None:
                self.assertEqual(r.status, 8, (defs, r.stderr))
                continue
            self.assertEqual(r.status, 0, (defs, r.stderr, text))
            got = list(csv.reader(io.StringIO(r.stdout.decode())))
            self.assertEqual(got[0], header + [f"r{i}" for i in range(len(trees))], defs)
            self.assertEqual(got[1:], want_rows, defs)
            # JSON says the same
            j = run("--root", self.s.dir, "--compute", defs, "t.csv").data()
            self.assertEqual(j["columns"], header + [f"r{i}" for i in range(len(trees))])
            self.assertEqual(j["rows"], want_rows)
            checked += 1
        self.assertGreater(checked, 25)

    @staticmethod
    def _shift_positions(defs):
        import re
        return re.sub(r"#(\d)", lambda m: "#%d" % (int(m.group(1)) + 1), defs)

    def test_a_page_is_the_slice_of_the_whole_answer(self):
        text = "a,b\n" + "".join(f"{i},{i * 2}\n" for i in range(1, 8))
        self.s.write("t.csv", text)
        whole = run("--root", self.s.dir, "--compute", "s=a:int+b:int", "t.csv").data()["rows"]
        self.assertEqual(len(whole), 7)
        page = run("--root", self.s.dir, "--compute", "s=a:int+b:int", "--limit", "3", "--from", "2", "t.csv").data()
        self.assertEqual(page["rows"], whole[2:5])
        self.assertTrue(page["truncated"])
        self.assertEqual(page["next"], {"from": 5})
        last = run("--root", self.s.dir, "--compute", "s=a:int+b:int", "--limit", "3", "--from", "5", "t.csv").data()
        self.assertEqual(last["rows"], whole[5:])
        self.assertFalse(last["truncated"])

    def test_rules_and_exit_statuses(self):
        self.s.write("t.csv", "a,b,c\n1,2,3\n0,5,x\n")
        cases = [
            ("r=z:int+1", "compute.unknown-column", 3),
            ("a=b:int", "compute.duplicate-name", 2),
            ("r=b+1", "compute.untyped-operand", 2),
            ("r=b:float+1", "compute.float-operand", 2),
            ("r=b:int/a:int", "compute.division-needs-scale", 2),
            ("r=b:int+", "compute.syntax", 2),
            ("r=b:dec(10)*b:dec(10)", "compute.scale-too-large", 2),
            ("r=((((((b:int))))))", "compute.too-long", 2),
            ("r=b:int/a:int@2", "compute.divide-by-zero", 8),
            ("r=c:int+1", "value.not-integer", 8),
        ]
        for defs, rule, status in cases:
            r = run("--root", self.s.dir, "--compute", defs, "t.csv")
            self.assertEqual(r.status, status, (defs, r.stderr))
            self.assertEqual(r.first_rule(), rule, (defs, r.stdout))
        # a refusal of a row names the row, and never the cell
        r = run("--root", self.s.dir, "--compute", "r=c:int+1", "t.csv")
        err = json.dumps(r.doc())
        self.assertIn('"row": 2', err.replace('"row":2', '"row": 2'))
        self.assertNotIn("\"x\"", err.replace('"context"', ""))

    def test_a_refused_row_stops_the_answer_where_it_is(self):
        self.s.write("t.csv", "a,b\n1,2\n3,x\n5,6\n")
        r = run("--root", self.s.dir, "--compute", "s=a:int+b:int", "--format", "csv", "t.csv")
        self.assertEqual((r.status, r.first_rule() if r.stdout.startswith(b"{") else None), (8, None))
        # the rows before the refused one were written, the ones after were never read
        self.assertEqual(r.stdout.decode(), "a,b,s\n1,2,3\n")
        self.assertIn(b"value.not-integer", r.raw_stderr)

    def test_the_row_and_line_of_a_refusal_in_a_record_on_several_lines(self):
        self.s.write("t.csv", 'a,b,c\n1,2,3\n4,"x\ny",6\n')
        r = run("--root", self.s.dir, "--compute", "s=b:int+1", "t.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "value.not-integer"))
        d = r.doc()["errors"][0]["detail"]
        self.assertEqual((d["row"], d["line"], d["column"]), (2, 3, "b"))

    def test_json_lines_input_is_refused(self):
        self.s.write("t.jsonl", '{"a": 1}\n')
        r = run("--root", self.s.dir, "--compute", "s=a:int+1", "--input-format", "jsonl", "--fields", "a", "t.jsonl")
        self.assertEqual((r.status, r.first_rule()), (2, "compute.unsupported-combination"), r.stdout)

    def test_the_overflow_is_a_refusal_never_a_wrap(self):
        big = 2**62
        self.s.write("t.csv", f"a\n{big}\n")
        r = run("--root", self.s.dir, "--compute", "r=a:int*a:int*a:int", "t.csv")
        self.assertEqual((r.status, r.first_rule()), (8, "compute.overflow"))
        ok = run("--root", self.s.dir, "--compute", "r=a:int*a:int", "--format", "csv", "t.csv")
        self.assertEqual(ok.stdout.decode().splitlines()[1], f"{big},{big * big}")

    def test_combinations_are_refused(self):
        self.s.write("t.csv", "a,b\n1,2\n")
        for flags in (["--select", "a"], ["--where", "a:int > 0"], ["--group", "a"], ["--agg", "count"], ["--order-by", "a"], ["--rows"], ["--skip-lines", "1"], ["--rename", "a=z"]):
            r = run("--root", self.s.dir, "--compute", "r=a:int+1", *flags, "t.csv")
            self.assertEqual((r.status, r.first_rule()), (2, "compute.unsupported-combination"), (flags, r.stdout))

    def test_standard_input_is_the_files_answer(self):
        text = 'name,a,b\n"x, y",1.50,2\nz,-0.25,3\n"q\nr",100,4\n'
        self.s.write("t.csv", text)
        for flags in (["--format", "csv"], [], ["--limit", "2"], ["--limit", "1", "--from", "1"]):
            args = ["--compute", "s=a:dec(2)*b:int;d=a:dec(2)/b:int@3", *flags]
            f = run("--root", self.s.dir, *args, "t.csv")
            p = run(*args, stdin=text.encode())
            self.assertEqual(f.status, 0)
            self.assertEqual((p.status, p.stdout), (f.status, f.stdout), flags)
        # a refusal reads the same from a pipe, with the input named `-`
        bad = "a\n1\nx\n"
        self.s.write("bad.csv", bad)
        f = run("--root", self.s.dir, "--compute", "s=a:int+1", "bad.csv")
        p = run("--compute", "s=a:int+1", stdin=bad.encode())
        self.assertEqual((p.status, p.first_rule()), (f.status, f.first_rule()))
        self.assertEqual(p.doc()["errors"][0]["detail"]["path"], "-")

    def test_memory_is_flat_in_the_rows(self):
        """G-FLAT: the peak resident memory of a file and of a pipe does not grow with the rows: 200,000 rows peak less than 1 MB above 20,000 (the noise of the launcher is about 0.5 MB either way; a copy of the rows kept would add about 6 MB). Measured by the small launcher of
        test_memory (a process forked from the runner would report the runner's own high-water mark: tests/conformance/maxrss.c)."""
        import procs as subprocess
        from harness import binary
        from test_memory import helper
        peaks = {}
        for n in (20000, 200000):
            rows = "".join(f"{i},{i % 997}.{i % 100:02d},{i % 50 + 1}\n" for i in range(n))
            path = self.s.write(f"m{n}.csv", "id,price,qty\n" + rows)
            del rows
            plan = ["--compute", "t=price:dec(2)*qty:int;h=price:dec(2)/qty:int@4", "--format", "csv"]
            for mode in ("file", "stdin"):
                argv = [helper(), binary(), *plan] + (["--root", str(self.s.dir), f"m{n}.csv"] if mode == "file" else [])
                with open(path, "rb") as src:
                    p = subprocess.run(argv, capture_output=True, text=True, check=True, stdin=src if mode == "stdin" else subprocess.DEVNULL)
                rss, status = p.stdout.split()
                self.assertEqual(int(status), 0)
                peaks[(n, mode)] = int(rss)
        for mode in ("file", "stdin"):
            self.assertLessEqual(peaks[(200000, mode)] - peaks[(20000, mode)], 1024, peaks)

    def test_the_stage_composes_with_a_second_call(self):
        self.s.write("t.csv", "price,qty\n2.50,4\n10.00,3\n0.99,10\n")
        first = run("--root", self.s.dir, "--compute", "total=price:dec(2)*qty:int", "--format", "csv", "t.csv")
        self.assertEqual(first.status, 0)
        self.s.write("u.csv", first.stdout)
        total = run("--root", self.s.dir, "--agg", "sum:total:dec(2)", "u.csv").data()
        self.assertEqual(total["rows"], [["49.90"]])


if __name__ == "__main__":
    unittest.main()
