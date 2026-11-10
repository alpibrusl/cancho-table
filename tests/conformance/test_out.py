"""`--out FILE` (docs/out.md, issue #108): the answer is written to a file, whole, and put in place by a rename.

The gates of docs/out.md section 8, each able to fail:

* B, bytes: the file is the bytes standard output would have had, for every `--format` and every form (rows, groups, sorted, joined, report, shape, JSON-lines input, standard input), and the receipt says how many.
* L, litter: a refused run, a name that is taken and an answer past the bound leave the directory as it was.
* C, confinement: no `--root`, `..`, an absolute path, a link in the way, a directory that is not there.
* H, the bound: the held answer is counted in `--max-state-bytes`, to the byte.
* R, the race: two runs with one name and no `--overwrite`, exactly one wins.
* A, the authority: the ceiling gained `dir_write` and `file_write` and nothing else.
"""

import csv
import io
import json
import os
import random
import sys
import threading
import unittest

import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
from harness import BIN, ROOT, Scratch, introspect, run, run_argv


def encode(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


def listing(d):
    return sorted(p.name for p in d.iterdir())


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.dir = self.s.dir
        self.root = ["--root", str(self.dir)]
        rng = random.Random(7)
        rows = [[str(i), "n%d" % (i % 13), str(rng.randrange(1000)), rng.choice(["x,y", 'q"r', "", "plain", "é"])] for i in range(400)]
        self.s.write("t.csv", encode(["id", "name", "v", "note"], rows))
        self.s.write("b.csv", encode(["name", "label"], [["n%d" % i, "L%d" % i] for i in range(0, 13, 2)]))
        self.s.write("j.jsonl", "".join(json.dumps({"id": i, "u": {"n": "n%d" % (i % 4)}}) + "\n" for i in range(50)))

    def tearDown(self):
        self.s.cleanup()

    def plain(self, args, stdin=None):
        return run(*self.root, *args, stdin=stdin)

    def out(self, name, args, *extra, stdin=None):
        return run(*self.root, *args, "--out", name, *extra, stdin=stdin)

    def read(self, name):
        return (self.dir / name).read_bytes()


CASES = [
    ("rows csv", ["--select", "id,name", "--where", "v:int > 500", "--format", "csv", "t.csv"]),
    ("rows json", ["--select", "id,name", "--where", "v:int > 500", "--limit", "7", "t.csv"]),
    ("rows jsonl", ["--select", "id,name", "--format", "jsonl", "t.csv"]),
    ("rows quoted cells", ["--select", "note,id", "--format", "csv", "t.csv"]),
    ("rows page", ["--select", "id", "--limit", "10", "--from", "20", "--format", "csv", "t.csv"]),
    ("all rows", ["--rows", "--format", "csv", "t.csv"]),
    ("sorted csv", ["--order-by", "-v:int,id:int", "--format", "csv", "t.csv"]),
    ("sorted top", ["--order-by", "-v:int", "--top", "5", "--format", "csv", "t.csv"]),
    ("groups csv", ["--group", "name", "--agg", "count,sum:v,distinct:note", "--sort", "-count", "--format", "csv", "t.csv"]),
    ("groups json", ["--group", "name", "--agg", "count,sum:v", "t.csv"]),
    ("groups jsonl", ["--group", "name", "--agg", "count", "--format", "jsonl", "t.csv"]),
    ("one group", ["--agg", "count,sum:v", "--format", "csv", "t.csv"]),
    ("shape json", ["t.csv"]),
    ("shape text", ["--format", "text", "t.csv"]),
    ("rows text is a shape", ["--format", "text", "t.csv"]),
    ("report", ["--report", "types", "--format", "csv", "t.csv"]),
    ("report json", ["--report", "types", "t.csv"]),
    ("join csv", ["--join", "b.csv", "--on", "name", "--join-prefix", "r_", "--select", "id,name,r_label", "--format", "csv", "t.csv"]),
    ("join left grouped", ["--join", "b.csv", "--on", "name", "--join-prefix", "r_", "--join-type", "left", "--group", "r_label", "--agg", "count", "--format", "csv", "t.csv"]),
    ("join full", ["--join", "b.csv", "--on", "name", "--join-prefix", "r_", "--join-type", "full", "--format", "csv", "--select", "id,name,r_label", "t.csv"]),
    ("jsonl input", ["--input-format", "jsonl", "--fields", "id,u.n", "--select", "id", "--where", "id:int > 40", "--format", "csv", "j.jsonl"]),
    ("jsonl input threads", ["--input-format", "jsonl", "--fields", "id,u.n", "--select", "id,u.n", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "512", "--format", "csv", "j.jsonl"]),
    ("jsonl input grouped", ["--input-format", "jsonl", "--fields", "id,u.n", "--group", "u.n", "--agg", "count", "--format", "csv", "j.jsonl"]),
    ("join left", ["--join", "b.csv", "--on", "name", "--join-prefix", "r_", "--join-type", "left", "--select", "id,name,r_label", "--format", "csv", "t.csv"]),
    ("join sorted by key", ["--join", "b.csv", "--on", "name", "--join-prefix", "r_", "--order-by", "r_label,id:int", "--select", "id,name,r_label", "--format", "csv", "t.csv"]),
    ("join semi", ["--join", "b.csv", "--on", "name", "--join-type", "semi", "--format", "csv", "t.csv"]),
    ("query", ["--query", "select name, count(*) from 't.csv' group by name order by count(*) desc limit 3", "--format", "csv"]),
    ("nothing selected", ["--select", "id", "--where", "v:int > 100000", "--format", "csv", "t.csv"]),
    ("nothing selected jsonl", ["--select", "id", "--where", "v:int > 100000", "--format", "jsonl", "t.csv"]),
    ("nothing selected json", ["--select", "id", "--where", "v:int > 100000", "t.csv"]),
    ("threads asked", ["--select", "id,name", "--threads", "4", "--parallel-min-bytes", "1", "--format", "csv", "t.csv"]),
]


class Bytes(Base):
    def test_the_file_is_what_standard_output_would_have_been(self):
        for i, (label, args) in enumerate(CASES):
            with self.subTest(label):
                name = "o%d.out" % i
                want = self.plain(args)
                self.assertEqual(want.status, 0, (label, want.stdout[:200], want.stderr[:200]))
                got = self.out(name, args)
                self.assertEqual(got.status, 0, (label, got.stdout[:300], got.stderr[:300]))
                self.assertEqual(self.read(name), want.stdout, label)
                receipt = got.doc()
                self.assertTrue(receipt["ok"])
                self.assertEqual(receipt["schema"], "table.v2")
                self.assertEqual(receipt["data"]["out"]["path"], name)
                self.assertEqual(receipt["data"]["out"]["bytes"], len(want.stdout))
                self.assertEqual(got.stdout.count(b"\n"), 1, "one line")
                self.assertEqual(got.stderr, b"")

    def test_the_rows_of_the_receipt_are_the_rows_of_the_answer(self):
        for label, args in [
            ("rows", ["--select", "id", "--where", "v:int > 500", "--limit", "1000", "t.csv"]),
            ("groups", ["--group", "name", "--agg", "count", "--limit", "1000", "t.csv"]),
        ]:
            with self.subTest(label):
                want = self.plain(args).data()["row_count"]
                got = self.out("r_" + label + ".json", args)
                self.assertEqual(got.doc()["data"]["out"]["rows"], want)
                got_csv = self.out("r_" + label + ".csv", [*args, "--format", "csv"])
                self.assertEqual(got_csv.doc()["data"]["out"]["rows"], want)
        empty = self.out("r_empty.csv", ["--select", "id", "--where", "v:int > 100000", "--format", "csv", "t.csv"])
        self.assertEqual(empty.doc()["data"]["out"]["rows"], 0)

    def test_standard_input_is_read_and_the_file_written(self):
        data = self.read("t.csv")
        args = ["--select", "id,name", "--where", "id:int < 30", "--format", "csv"]
        want = self.plain(args, stdin=data)
        got = self.out("stdin.csv", args, stdin=data)
        self.assertEqual(got.status, 0, got.stderr)
        self.assertEqual(self.read("stdin.csv"), want.stdout)

    def test_a_file_in_a_directory_beneath_the_root(self):
        (self.dir / "sub").mkdir()
        got = self.out("sub/x.csv", ["--select", "id", "--format", "csv", "t.csv"])
        self.assertEqual(got.status, 0, got.stderr)
        self.assertEqual(got.doc()["data"]["out"]["path"], "sub/x.csv")
        self.assertEqual(self.read("sub/x.csv"), self.plain(["--select", "id", "--format", "csv", "t.csv"]).stdout)

    def test_a_larger_answer_than_one_block_and_the_sorted_and_the_grouped_forms_of_it(self):
        n = 30000
        self.s.write("big.csv", encode(["id", "k", "v"], [[str(i), "k%d" % (i % 211), str((i * 7919) % 10007)] for i in range(n)]))
        self.s.write("bigb.csv", encode(["k", "label"], [["k%d" % i, "L%d" % (i % 7)] for i in range(0, 250, 2)]))
        self.s.write("big.jsonl", "".join(json.dumps({"id": i, "k": "k%d" % (i % 211), "v": (i * 7919) % 10007}) + "\n" for i in range(n)))
        for label, args in [
            ("rows", ["--select", "id,k,v", "--format", "csv", "big.csv"]),
            ("sorted", ["--order-by", "-v:int,id:int", "--format", "csv", "big.csv"]),
            ("grouped", ["--group", "k", "--agg", "count,sum:v", "--sort", "-sum:v", "--format", "csv", "big.csv"]),
            ("jsonl", ["--select", "id,k", "--format", "jsonl", "big.csv"]),
            ("threads", ["--select", "id,k", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "65536", "--format", "csv", "big.csv"]),
            ("sorted threads", ["--order-by", "-v:int,id:int", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "65536", "--format", "csv", "big.csv"]),
            ("grouped threads", ["--group", "k", "--agg", "count,sum:v", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "65536", "--format", "csv", "big.csv"]),
            ("join", ["--join", "bigb.csv", "--on", "k", "--join-prefix", "r_", "--select", "id,k,r_label", "--format", "csv", "big.csv"]),
            ("join left", ["--join", "bigb.csv", "--on", "k", "--join-prefix", "r_", "--join-type", "left", "--select", "id,k,r_label", "--format", "csv", "big.csv"]),
            ("join full", ["--join", "bigb.csv", "--on", "k", "--join-prefix", "r_", "--join-type", "full", "--select", "id,k,r_label", "--format", "csv", "big.csv"]),
            ("join sorted rows", ["--join", "bigb.csv", "--on", "k", "--join-prefix", "r_", "--order-by", "r_label,id:int", "--select", "id,k,r_label", "--format", "csv", "big.csv"]),
            ("join grouped", ["--join", "bigb.csv", "--on", "k", "--join-prefix", "r_", "--group", "id", "--agg", "count", "--format", "csv", "big.csv"]),
            ("jsonl input", ["--input-format", "jsonl", "--fields", "id,k,v", "--select", "id,k,v", "--format", "csv", "big.jsonl"]),
            ("jsonl input threads", ["--input-format", "jsonl", "--fields", "id,k,v", "--select", "id,k", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "65536", "--format", "csv", "big.jsonl"]),
            ("jsonl input sorted", ["--input-format", "jsonl", "--fields", "id,k,v", "--order-by", "-v:int,id:int", "--format", "csv", "big.jsonl"]),
            ("jsonl input sorted threads", ["--input-format", "jsonl", "--fields", "id,k,v", "--order-by", "-v:int,id:int", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "65536", "--format", "csv", "big.jsonl"]),
            ("jsonl input grouped threads", ["--input-format", "jsonl", "--fields", "id,k,v", "--group", "id", "--agg", "count", "--threads", "4", "--parallel-min-bytes", "1", "--chunk-bytes", "65536", "--format", "csv", "big.jsonl"]),
            ("jsonl input grouped", ["--input-format", "jsonl", "--fields", "id,k,v", "--group", "id", "--agg", "count", "--format", "csv", "big.jsonl"]),
        ]:
            with self.subTest(label):
                want = self.plain(args)
                got = self.out("big_" + label, args)
                self.assertEqual(got.status, 0, got.stderr)
                if label not in ("grouped", "grouped threads"):
                    self.assertGreater(len(want.stdout), 65536, "the answer must pass a block, or no flush is tested: " + label)
                self.assertEqual(self.read("big_" + label), want.stdout)
        self.assertGreater(len(self.read("big_rows")), 65536 * 3)


class Overwrite(Base):
    ARGS = ["--select", "id", "--format", "csv", "t.csv"]

    def test_a_taken_name_is_out_exists_with_two_repairs(self):
        self.s.write("x.csv", "old\n")
        before = listing(self.dir)
        got = self.out("x.csv", self.ARGS)
        self.assertEqual(got.status, 2)
        e = got.stderr.decode()
        self.assertIn("out.exists", e)
        self.assertEqual(self.read("x.csv"), b"old\n")
        self.assertEqual(listing(self.dir), before)
        j = self.out("x.csv", ["--select", "id", "t.csv"])
        self.assertEqual(j.status, 2)
        err = j.error()
        self.assertEqual(err["rule"], "out.exists")
        self.assertEqual(err["code"], "INVALID_ARGS")
        self.assertEqual(err["repair"]["kind"], "choose")
        opts = [o["argv"] for o in err["repair"]["options"]]
        self.assertEqual(len(opts), 2)
        self.assertEqual(opts[0][-1], "--overwrite")
        self.assertEqual(opts[1][opts[1].index("--out") + 1], "x-1.csv")
        self.assertEqual(err["detail"], {"path": "x.csv", "flag": "--out"})
        for argv in opts:
            r = run_argv(argv)
            self.assertEqual(r.status, 0, (argv, r.stderr))

    def test_the_second_name_is_the_first_free_one(self):
        self.s.write("x.csv", "old\n")
        self.s.write("x-1.csv", "old\n")
        self.s.write("x-2.csv", "old\n")
        err = self.out("x.csv", ["--select", "id", "t.csv"]).error()
        opts = [o["argv"] for o in err["repair"]["options"]]
        self.assertEqual(opts[1][opts[1].index("--out") + 1], "x-3.csv")

    def test_the_equals_spelling_of_out(self):
        got = run(*self.root, "--out=eq.csv", *self.ARGS)
        self.assertEqual(got.status, 0, got.stderr)
        self.assertEqual(got.doc()["data"]["out"]["path"], "eq.csv")
        self.assertTrue((self.dir / "eq.csv").exists())
        err = run(*self.root, "--out=eq.csv", "--select", "id", "t.csv").error()
        opts = [o["argv"] for o in err["repair"]["options"]]
        self.assertIn("--out=eq-1.csv", opts[1])
        self.assertEqual(run_argv(opts[1]).status, 0)
        self.assertTrue((self.dir / "eq-1.csv").exists())
        bad = run(*self.root, "--out=../x.csv", *self.ARGS)
        self.assertIn("path.dotdot", bad.stderr.decode())

    def test_no_free_name_among_the_first_nine_is_one_option(self):
        self.s.write("x.csv", "old\n")
        for n in range(1, 10):
            self.s.write("x-%d.csv" % n, "old\n")
        err = self.out("x.csv", ["--select", "id", "t.csv"]).error()
        self.assertEqual(len(err["repair"]["options"]), 1)
        self.assertEqual(err["repair"]["options"][0]["argv"][-1], "--overwrite")
        os.remove(self.dir / "x-9.csv")
        err = self.out("x.csv", ["--select", "id", "t.csv"]).error()
        opts = [o["argv"] for o in err["repair"]["options"]]
        self.assertEqual(opts[1][opts[1].index("--out") + 1], "x-9.csv")

    def test_no_extension_and_a_dotfile(self):
        for name, want in [("plain", "plain-1"), (".hidden", ".hidden-1"), ("a.b.c", "a.b-1.c")]:
            self.s.write(name, "old\n")
            err = self.out(name, ["--select", "id", "t.csv"]).error()
            opts = [o["argv"] for o in err["repair"]["options"]]
            self.assertEqual(opts[1][opts[1].index("--out") + 1], want, name)

    def test_overwrite_replaces_whole_and_leaves_no_temporary(self):
        self.s.write("x.csv", "old\n")
        got = self.out("x.csv", self.ARGS, "--overwrite")
        self.assertEqual(got.status, 0, got.stderr)
        self.assertEqual(self.read("x.csv"), self.plain(self.ARGS).stdout)
        self.assertEqual(listing(self.dir), sorted(["t.csv", "b.csv", "j.jsonl", "x.csv"]))

    def test_overwrite_of_a_name_that_is_free_is_a_write(self):
        got = self.out("fresh.csv", self.ARGS, "--overwrite")
        self.assertEqual(got.status, 0, got.stderr)
        self.assertTrue((self.dir / "fresh.csv").exists())

    def test_overwrite_of_a_directory_is_a_write_failure_and_leaves_the_directory(self):
        (self.dir / "d").mkdir()
        got = self.out("d", self.ARGS, "--overwrite")
        self.assertEqual(got.status, 1, got.stderr)
        self.assertIn("out.write-failed", got.stderr.decode())
        self.assertIn('"step":"rename"', got.raw_stderr.decode())
        self.assertIn('"errno":21', got.raw_stderr.decode())
        self.assertTrue((self.dir / "d").is_dir())
        self.assertEqual(listing(self.dir), sorted(["t.csv", "b.csv", "j.jsonl", "d"]))

    def test_overwrite_without_out_is_required_flag(self):
        got = self.plain(["--overwrite", "--select", "id", "t.csv"])
        self.assertEqual(got.status, 2)
        self.assertEqual(got.error()["rule"], "args.required-flag")
        self.assertEqual(got.error()["detail"]["flags"], "--overwrite --out")


class Litter(Base):
    def test_a_refused_run_leaves_the_directory_as_it_was(self):
        before = listing(self.dir)
        for label, args in [
            ("unknown column", ["--select", "nope", "--format", "csv", "t.csv"]),
            ("bad value", ["--where", "name:int > 1", "--format", "csv", "t.csv"]),
            ("missing file", ["--select", "id", "--format", "csv", "absent.csv"]),
            ("late refusal", ["--where", "note:int > 1", "--select", "id", "--format", "csv", "t.csv"]),
            ("json", ["--select", "nope", "t.csv"]),
            ("sort", ["--order-by", "note:int", "--format", "csv", "t.csv"]),
            ("group", ["--group", "name", "--agg", "sum:note", "--format", "csv", "t.csv"]),
        ]:
            with self.subTest(label):
                plain = self.plain(args)
                got = self.out("never.csv", args)
                self.assertNotEqual(got.status, 0, label)
                self.assertEqual(got.status, plain.status, label)
                self.assertEqual(listing(self.dir), before, label)

    def test_a_refusal_is_the_refusal_it_was_without_out(self):
        args = ["--select", "nope", "t.csv"]
        a = self.plain(args)
        b = self.out("never.json", args)
        self.assertEqual(a.stdout, b.stdout)

    def test_a_flag_conflict_and_the_read_flags_that_do_not_run(self):
        for flag, args in (("--explain", ["--select", "id", "t.csv"]), ("--discover", ["--input-format", "jsonl", "j.jsonl"])):
            got = self.out("e.json", args, flag)
            self.assertEqual(got.status, 2, flag)
            self.assertEqual(got.error()["rule"], "args.conflict")
        self.assertEqual(listing(self.dir), sorted(["t.csv", "b.csv", "j.jsonl"]))


class Confinement(Base):
    ARGS = ["--select", "id", "--format", "csv", "t.csv"]

    def rule_of(self, got):
        text = got.stderr.decode() + got.stdout.decode()
        return text

    def test_no_root_is_required_flag(self):
        got = run("--out", "x.csv", *self.ARGS, cwd=str(self.dir))
        self.assertEqual(got.status, 2)
        self.assertIn("args.required-flag", got.stderr.decode())
        self.assertEqual(listing(self.dir), sorted(["t.csv", "b.csv", "j.jsonl"]))

    def test_dotdot_absolute_and_outside(self):
        sub = self.dir / "sub"
        sub.mkdir()
        outside = self.dir.parent / ("escaped-%d.csv" % os.getpid())
        for name, rule in [("../x.csv", "path.dotdot"), ("sub/../x.csv", "path.dotdot"), (str(outside), "path.outside-root"), ("", "args.bad-value")]:
            with self.subTest(name):
                got = run("--root", str(sub), "--out", name, "--select", "id", "--format", "csv", stdin=self.read("t.csv"))
                self.assertEqual(got.status, 4 if rule in ("path.outside-root", "path.symlink") else 2, (name, got.stderr))
                self.assertIn(rule, got.stderr.decode(), name)
        self.assertFalse(outside.exists())
        self.assertEqual(listing(sub), [])

    def test_a_link_in_the_way(self):
        target = self.dir / "target"
        target.mkdir()
        (self.dir / "linkdir").symlink_to(target)
        (self.dir / "linkfile").symlink_to(self.dir / "t.csv")
        for name in ("linkdir/x.csv", "linkfile"):
            with self.subTest(name):
                before = listing(target)
                got = self.out(name, self.ARGS, "--overwrite")
                self.assertEqual(got.status, 4, (name, got.stderr))
                self.assertIn("path.symlink", got.stderr.decode(), name)
                self.assertEqual(listing(target), before)
        self.assertEqual(self.read("t.csv"), encode_expected(self))

    def test_a_directory_that_is_not_there(self):
        got = self.out("nodir/x.csv", self.ARGS)
        self.assertEqual(got.status, 3)
        self.assertIn("io.not-found", got.stderr.decode())
        self.assertFalse((self.dir / "nodir").exists())

    def test_a_directory_is_not_a_file_name(self):
        for name in (".", "sub/"):
            (self.dir / "sub").mkdir(exist_ok=True)
            with self.subTest(name):
                got = self.out(name, self.ARGS, "--overwrite")
                self.assertNotEqual(got.status, 0, name)
        self.assertEqual(listing(self.dir), sorted(["t.csv", "b.csv", "j.jsonl", "sub"]))

    def test_a_read_only_directory_is_out_write_failed(self):
        ro = self.dir / "ro"
        ro.mkdir()
        os.chmod(ro, 0o555)
        os.chmod(self.dir, 0o755)
        os.chmod(self.dir / "t.csv", 0o644)

        def as_nobody():
            if os.geteuid() == 0:
                os.setgid(65534)
                os.setuid(65534)

        try:
            p = subprocess.run([str(BIN / "table"), "--root", str(self.dir), "--out", "ro/x.csv", *self.ARGS], capture_output=True, preexec_fn=as_nobody)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertIn(b"out.write-failed", p.stderr)
            self.assertEqual(listing(ro), [])
            j = subprocess.run([str(BIN / "table"), "--root", str(self.dir), "--out", "ro/x.csv", "--select", "id", "t.csv"], capture_output=True, preexec_fn=as_nobody)
            self.assertEqual(j.returncode, 1, j.stderr)
            detail = json.loads(j.stdout)["error"]["detail"]
            self.assertEqual((detail["path"], detail["errno"], detail["step"]), ("ro/x.csv", 13, "create-temporary"))
        finally:
            os.chmod(ro, 0o755)


def encode_expected(case):
    return case.read("t.csv")


class Order(Base):
    """The name is looked at before the run (docs/out.md section 11): a call that cannot succeed does no work, and says what is wrong with the output first."""

    def test_a_missing_directory_is_said_before_a_refusal_of_the_run(self):
        got = self.out("nodir/x.csv", ["--select", "nope", "--format", "csv", "t.csv"])
        self.assertEqual(got.status, 3)
        self.assertIn("io.not-found", got.stderr.decode())
        self.assertNotIn("select.unknown-column", got.stderr.decode())

    def test_a_directory_name_is_said_before_a_refusal_of_the_run(self):
        (self.dir / "sub").mkdir(exist_ok=True)
        for name, rule in ((".", "io.is-a-directory"), ("sub/", "out.exists"), ("sub", "out.exists")):
            with self.subTest(name):
                got = self.out(name, ["--select", "nope", "--format", "csv", "t.csv"])
                self.assertIn(rule, got.stderr.decode(), name)
                self.assertNotIn("select.unknown-column", got.stderr.decode(), name)

    def test_a_taken_name_is_said_before_a_refusal_of_the_run(self):
        self.s.write("x.csv", "old\n")
        got = self.out("x.csv", ["--select", "nope", "--format", "csv", "t.csv"])
        self.assertIn("out.exists", got.stderr.decode())
        self.assertNotIn("select.unknown-column", got.stderr.decode())

    def test_an_input_that_is_not_there_is_the_inputs_refusal(self):
        got = self.out("fine.csv", ["--select", "id", "--format", "csv", "absent.csv"])
        self.assertEqual(got.status, 3)
        self.assertIn("io.not-found", got.stderr.decode())
        self.assertFalse((self.dir / "fine.csv").exists())


class Bound(Base):
    def test_the_held_answer_is_counted_to_the_byte(self):
        args = ["--select", "id,name", "--format", "csv", "t.csv"]
        size = len(self.plain(args).stdout)
        ok = self.out("fits.csv", args, "--max-state-bytes", str(size))
        self.assertEqual(ok.status, 0, ok.stderr)
        self.assertEqual(self.read("fits.csv"), self.plain(args).stdout)
        before = listing(self.dir)
        over = self.out("over.csv", args, "--max-state-bytes", str(size - 1))
        self.assertEqual(over.status, 8, over.stderr)
        self.assertIn("limit.state-too-large", over.stderr.decode())
        self.assertIn('"holder":"output"', over.raw_stderr.decode())
        self.assertEqual(listing(self.dir), before)

    def test_the_bound_in_json_form_and_when_the_answer_is_large(self):
        n = 40000
        self.s.write("big.csv", encode(["id", "k"], [[str(i), "k%d" % (i % 97)] for i in range(n)]))
        args = ["--select", "id,k", "--format", "csv", "big.csv"]
        size = len(self.plain(args).stdout)
        self.assertGreater(size, 65536 * 2)
        before = listing(self.dir)
        for cap in (size - 1, size // 2, 70000, 65536, 1):
            with self.subTest(cap):
                over = self.out("over.csv", args, "--max-state-bytes", str(cap))
                self.assertEqual(over.status, 8, (cap, over.stderr[:300]))
                self.assertEqual(listing(self.dir), before)
        fits = self.out("fits.csv", args, "--max-state-bytes", str(size))
        self.assertEqual(fits.status, 0, fits.stderr)
        j = self.out("over.csv", ["--select", "id,k", "--format", "csv", "big.csv"], "--max-state-bytes", "1000")
        self.assertEqual(j.status, 8)

    def test_a_json_answer_is_bounded_by_the_page(self):
        got = self.out("page.json", ["--select", "id,name", "--max-bytes", "2000", "t.csv"])
        self.assertEqual(got.status, 0, got.stderr)
        self.assertLessEqual(len(self.read("page.json")), 4000)
        self.assertTrue(json.loads(self.read("page.json"))["data"]["truncated"])


class Race(Base):
    def test_two_runs_with_one_name_exactly_one_wins(self):
        n = 20000
        self.s.write("race.csv", encode(["id", "k"], [[str(i), "k%d" % (i % 97)] for i in range(n)]))
        for trial in range(6):
            name = "r%d.csv" % trial
            argv = [str(BIN / "table"), "--root", str(self.dir), "--select", "id,k", "--format", "csv", "--out", name, "race.csv"]
            results = []

            def go():
                p = subprocess.run(argv, capture_output=True)
                results.append(p.returncode)

            threads = [threading.Thread(target=go) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(results), [0, 2, 2], (trial, results))
            self.assertEqual(len(self.read(name)), len(self.plain(["--select", "id,k", "--format", "csv", "race.csv"]).stdout))
        left = [p for p in listing(self.dir) if p.startswith(".")]
        self.assertEqual(left, [], left)


class Memory(unittest.TestCase):
    """The held answer is the memory of the call and no more: an answer past the bound is refused as it grows, not when it is whole (docs/out.md section 5)."""

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        with open(cls.s.dir / "large.csv", "w", newline="") as f:
            f.write("id,name,v\n")
            for i in range(900_000):
                f.write("%d,name%d,%d\n" % (i, i % 977, (i * 7919) % 100003))
        cls.size = os.path.getsize(cls.s.dir / "large.csv")

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def peak(self, *args):
        from test_memory import helper
        import procs
        p = procs.run([helper(), str(BIN / "table"), "--root", str(self.s.dir), *args], capture_output=True, text=True, check=True)
        rss, status = p.stdout.split()
        return int(status), int(rss) * (1 if sys.platform == "darwin" else 1024)

    def test_an_answer_past_the_bound_is_refused_as_it_grows(self):
        self.assertGreater(self.size, 15 << 20)
        status, rss = self.peak("--select", "id,name,v", "--format", "csv", "--out", "never.csv", "--max-state-bytes", "1000000", "large.csv")
        self.assertEqual(status, 8)
        self.assertLess(rss, 8 << 20, rss)
        self.assertFalse((self.s.dir / "never.csv").exists())

    def test_an_answer_within_the_bound_is_held_and_not_more(self):
        status, rss = self.peak("--select", "id", "--where", "id:int < 200000", "--format", "csv", "--out", "held.csv", "--max-state-bytes", "4000000", "large.csv")
        self.assertEqual(status, 0)
        written = os.path.getsize(self.s.dir / "held.csv")
        self.assertGreater(written, 1_000_000)
        self.assertLess(written, 4_000_000)
        self.assertLess(rss, (8 << 20) + 3 * written, rss)


class Authority(unittest.TestCase):
    def test_the_ceiling_gained_the_two_labels_and_the_tool_uses_them(self):
        eff = introspect()["authority"]["effects"]
        self.assertIn("dir_write", eff)
        self.assertIn("file_write", eff)
        for forbidden in ("net_out", "net_in", "ffi", "clock", "exec"):
            self.assertFalse([e for e in eff if e.startswith(forbidden)], forbidden)

    def test_the_flags_are_in_the_tool_description(self):
        names = {f["name"]: f for f in introspect()["flags"]}
        self.assertEqual(names["--out"]["role"], "path-write")
        self.assertEqual(names["--overwrite"]["kind"], "bool")

    def test_the_rules_are_catalogued(self):
        rules = {r["rule"]: r for r in introspect()["rules"]}
        self.assertEqual(rules["out.exists"]["repairable"], "sometimes")
        self.assertEqual(rules["out.exists"]["exit"], 2)
        self.assertEqual(rules["out.write-failed"]["repairable"], "never")
        self.assertEqual(rules["out.write-failed"]["exit"], 1)
        self.assertIn("output", rules["limit.state-too-large"]["summary"])


if __name__ == "__main__":
    unittest.main()
