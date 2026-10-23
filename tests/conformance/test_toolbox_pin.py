"""The adoption of cancho-tools' main (#42: a hint on every refusal; then cancho-tools#53: a hint on the operating-system refusals), docs/architecture.md "The pin".

The nine `[dependencies.*]` revs moved from 919e7ba to af73b1b, and from af73b1b to d3fed0c. What the first move changed in the bytes of `table` is four hints that were null and are sentences now (`toolbox.cli`:
`args.missing-value`, `args.bad-value`; `toolbox.path`: `path.empty`, `path.too-long`); what the second changed is three more (`toolbox.fail`: `io.not-a-directory`, `io.permission-denied`, `io.read-failed`), seven
in all, and `io.write-failed` has one in the package that `table` cannot deliver (its refusal is written when standard output is what failed, so it goes to standard error as `table: RULE: message`, which has no hint).
This file states the new contract: the exact text of each, on every way to reach it; that nothing else of these refusals moved (status, code, message, repair, detail); that the sentences are the installed
package's and the same as `toolbox_pin.HINTS`; that every refusal the fixtures of `test_rules.py` reach in json carries a hint; and, against the binary of the earlier pin (MAIN_TABLE), that the hints are the only
difference in a wide set of calls.
"""

import json
import os
import unittest

from harness import Scratch, run, run_argv, binary, BIN
import toolbox_pin as T
import test_rules

MAIN_TABLE = os.environ.get("MAIN_TABLE")

BAD = "give the flag a value of the kind detail.expected names"
MISSING = "put the value after the flag, or write the two as --flag=value"
BAD_MESSAGE = "this flag's value is not of the kind it takes"
MISSING_MESSAGE = "this flag takes a value and none followed it"

# (the flags, the flag the refusal names, its value)
BAD_VALUES = [
    (["--where", ""], "--where", ""), (["--select", ""], "--select", ""), (["--group", ""], "--group", ""), (["--agg", ""], "--agg", ""), (["--sort", ""], "--sort", ""),
    (["--order-by", ""], "--order-by", ""), (["--report", ""], "--report", ""), (["--format", ""], "--format", ""), (["--format", "xml"], "--format", "xml"),
    (["--threads", "x"], "--threads", "x"), (["--limit", "x"], "--limit", "x"), (["--top", "-1"], "--top", "-1"), (["--max-bytes", "abc"], "--max-bytes", "abc"),
    (["--max-rows", "many"], "--max-rows", "many"), (["--from", "x"], "--from", "x"), (["--delimiter", ""], "--delimiter", ""), (["--root", ""], "--root", ""),
    (["--join", ""], "--join", ""), (["--join", "b.csv", "--on", ""], "--on", ""), (["--join", "b.csv", "--on", "a=k", "--join-type", "x"], "--join-type", "x"),
    (["--query", ""], "--query", ""), (["--rename", ""], "--rename", ""), (["--input-format", "xml"], "--input-format", "xml"), (["--fields", ""], "--fields", ""),
    (["--explain", "--where", ""], "--where", ""),
]
MISSING_VALUES = ["--where", "--select", "--group", "--agg", "--sort", "--order-by", "--report", "--format", "--threads", "--limit", "--max-rows", "--root", "--join"]


def errors_of(r):
    doc = json.loads(r.stdout)
    assert doc["ok"] is False
    return doc["error"], doc["errors"]


class Hints(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()
        self.s.write("f.csv", "a,b,c\n1,2,3\n4,5,6\n")
        self.s.write("b.csv", "k,v\n1,x\n")

    def tearDown(self):
        self.s.cleanup()

    def go(self, *args):
        return run(*args) if "--root" in args else run("--root", self.s.dir, *args)

    def test_a_bad_value_says_what_to_do(self):
        for argv, flag, value in BAD_VALUES:
            with self.subTest(argv):
                r = self.go(*argv, "f.csv")
                first, every = errors_of(r)
                self.assertEqual(r.status, 2)
                self.assertEqual(first["rule"], "args.bad-value")
                self.assertEqual(first["hint"], BAD)
                self.assertEqual((first["code"], first["message"], first["repair"]), ("INVALID_ARGS", BAD_MESSAGE, None))
                self.assertEqual(first["detail"]["flag"], flag)
                self.assertEqual(first["detail"]["value"], value)
                self.assertIn("expected", first["detail"])
                self.assertEqual(first, every[0])
                for e in every:
                    if e["rule"] == "args.bad-value":
                        self.assertEqual(e["hint"], BAD)

    def test_a_flag_with_no_value_says_where_it_goes(self):
        for flag in MISSING_VALUES:
            with self.subTest(flag):
                r = self.go("f.csv", flag)
                first, every = errors_of(r)
                self.assertEqual(r.status, 2)
                self.assertEqual(first["rule"], "args.missing-value")
                self.assertEqual(first["hint"], MISSING)
                self.assertEqual((first["code"], first["message"], first["repair"]), ("INVALID_ARGS", MISSING_MESSAGE, None))
                self.assertEqual(first["detail"]["flag"], flag)
                self.assertEqual(first, every[0])

    def test_an_empty_or_a_long_path(self):
        r = self.go("")
        first, every = errors_of(r)
        self.assertEqual((r.status, first["rule"], first["hint"], first["message"]), (2, "path.empty", "name a path; an empty argument names nothing", "the path is empty"))
        self.assertEqual((first["code"], first["repair"]), ("INVALID_ARGS", None))
        self.assertEqual(first, every[0])
        r = self.go("a" * 5000)
        first, every = errors_of(r)
        self.assertEqual((r.status, first["rule"], first["hint"], first["message"]), (2, "path.too-long", "use a path of at most 4096 bytes", "the path is longer than 4096 bytes"))
        self.assertEqual((first["code"], first["repair"]), ("INVALID_ARGS", None))
        r = run("--root", "a" * 5000, "f.csv")
        first, every = errors_of(r)
        self.assertEqual((r.status, first["rule"], first["hint"], first["message"]), (2, "path.too-long", "name a root of at most 4096 bytes", "--root is longer than 4096 bytes"))

    def test_each_error_of_a_list_carries_its_own_hint(self):
        # several refusals in one answer: the hints of the four are in `errors` as well as in `error`
        r = self.go("--select", "a", "--select", "b", "--where", "", "f.csv")
        first, every = errors_of(r)
        rules = [e["rule"] for e in every]
        self.assertIn("args.bad-value", rules)
        self.assertIn("args.duplicate-flag", rules)
        for e in every:
            if e["rule"] == "args.bad-value":
                self.assertEqual(e["hint"], BAD)
            if e["rule"] == "args.duplicate-flag":
                self.assertEqual(e["hint"], "give each flag once")

    def test_csv_and_text_do_not_carry_hints(self):
        for fmt in ("csv", "text"):
            r = self.go("--where", "", "--format", fmt, "f.csv")
            self.assertEqual(r.status, 2)
            self.assertEqual(r.stdout, b"")
            self.assertIn(b"args.bad-value", r.stderr)
            self.assertNotIn(BAD.encode(), r.stderr)

    def test_the_hints_are_the_ones_of_the_helper_of_the_goldens(self):
        # toolbox_pin.before(answer) is the answer of the earlier pin: the seven hints null again, every other byte alone
        for argv in (["--where", ""], ["f.csv", "--limit"], [""], ["a" * 5000]):
            r = self.go(*argv) if argv != ["f.csv", "--limit"] else self.go("--limit")
            self.assertNotEqual(T.before(r.stdout), r.stdout, argv)
            self.assertNotIn(b'"hint":"', T.before(r.stdout).split(b'"errors"')[0])
            self.assertEqual(T.before(T.before(r.stdout)), T.before(r.stdout))
        ok = self.go("f.csv")
        self.assertEqual(T.before(ok.stdout), ok.stdout)
        other = self.go("--select", "zz", "f.csv")
        self.assertEqual(T.before(other.stdout), other.stdout)


class EveryRule(unittest.TestCase):
    # the refusals whose hint the package writes empty: none since cancho-tools#53 (toolbox.fail's reading of an operating-system error gave the last three a sentence); a pin that takes one away fails here
    NO_HINT = set()

    def test_every_rule_a_fixture_reaches_has_a_hint(self):
        s = Scratch()
        without, with_hint = set(), set()
        try:
            for rule, argv, preexec in test_rules.Rules().fixtures(s):
                import procs as subprocess
                p = subprocess.run([binary(), *[str(a) for a in argv]], capture_output=True, preexec_fn=preexec, input=b"")
                try:
                    first = json.loads(p.stdout)["errors"][0]
                except ValueError:
                    continue            # csv and text answer on standard error, which has no hint
                (with_hint if first.get("hint") else without).add(rule)
        finally:
            try:
                os.chmod(s.dir / "denied.csv", 0o644)
            except OSError:
                pass
            s.cleanup()
        self.assertGreater(len(with_hint), 60)
        self.assertEqual(without, self.NO_HINT)
        for rule in ("args.missing-value", "args.bad-value", "path.empty", "path.too-long", "io.not-a-directory", "io.permission-denied", "io.read-failed"):
            self.assertIn(rule, with_hint)


class OperatingSystemRefusals(unittest.TestCase):
    """cancho-tools#53: the refusals that carry an operating-system error (`toolbox.fail.io_error`) say a hint. The sentences are read from the installed package and compared with `toolbox_pin.HINTS` (which
    `before` uses to put them back to null for the frozen goldens), then each rule `table` reaches is made, end to end, in the way `test_rules.py` makes it."""

    NOT_A_DIRECTORY = "a name in the path that has to be a directory is a file; name a path whose directories are all directories"
    PERMISSION_DENIED = "the user running the tool lacks permission for this path; check the permissions of the path and of the directories above it"
    OS_REFUSED = "the operating system refused the operation (detail.errno is its code); check the path and the storage, then try again"

    @staticmethod
    def package_sentences():
        """(default, errno 20, errno 13/1) from the installed toolbox.fail: the ladder of io_error."""
        import glob
        import re
        texts = [t for t in (open(f).read() for f in sorted(glob.glob(str(BIN / "deps" / "*.cho")))) if "pub fn io_error[" in t]
        assert len(texts) == 1, "the installed toolbox.fail"
        body = texts[0][texts[0].index("pub fn io_error["):]
        def sentence(pattern, what):
            m = re.search(pattern, body)
            assert m, "the installed toolbox.fail has no hint for %s" % what
            return m.group(1)
        default = sentence(r'var hint: &static \[byte\] = "([^"]*)";', "an operating-system error")
        not_a_directory = sentence(r'errno == 20 \{\s*message = "[^"]*";\s*hint = "([^"]*)";', "errno 20")
        permission = sentence(r'errno == 13 \|\| errno == 1 \{\s*message = "[^"]*";\s*hint = "([^"]*)";', "errno 13")
        return default, not_a_directory, permission

    def test_the_sentences_are_the_installed_packages_and_the_helpers(self):
        default, not_a_directory, permission = self.package_sentences()
        self.assertEqual((default, not_a_directory, permission), (self.OS_REFUSED, self.NOT_A_DIRECTORY, self.PERMISSION_DENIED))
        self.assertEqual(T.HINTS["io.not-a-directory"], [not_a_directory])
        self.assertEqual(T.HINTS["io.permission-denied"], [permission])
        self.assertEqual(T.HINTS["io.read-failed"], [default])
        self.assertEqual(T.HINTS["io.write-failed"], [default])

    def reached(self, rule):
        s = Scratch()
        try:
            for r, argv, preexec in test_rules.Rules().fixtures(s):
                if r == rule:
                    import procs as subprocess
                    return subprocess.run([binary(), *[str(a) for a in argv]], capture_output=True, preexec_fn=preexec, input=b"")
        finally:
            try:
                os.chmod(s.dir / "denied.csv", 0o644)
            except OSError:
                pass
            s.cleanup()
        self.fail("no fixture reaches %s" % rule)

    def first(self, rule):
        p = self.reached(rule)
        doc = json.loads(p.stdout)
        self.assertEqual(doc["errors"][0], doc["error"])
        self.assertEqual(doc["error"]["rule"], rule)
        return p.returncode, doc["error"]

    def test_a_path_through_a_file(self):
        status, e = self.first("io.not-a-directory")
        self.assertEqual((status, e["code"], e["message"], e["hint"], e["repair"]), (3, "NOT_FOUND", "a component of the path is not a directory", self.NOT_A_DIRECTORY, None))
        self.assertEqual(e["detail"], {"path": "ok.csv/x", "errno": 20})

    def test_a_file_the_user_may_not_read(self):
        status, e = self.first("io.permission-denied")
        self.assertEqual((status, e["code"], e["message"], e["hint"], e["repair"]), (4, "PERMISSION_DENIED", "permission denied", self.PERMISSION_DENIED, None))
        self.assertEqual(e["detail"], {"path": "denied.csv", "errno": 13})

    @unittest.skipUnless(os.path.exists("/proc/self/mem"), "Linux")
    def test_a_read_the_system_fails(self):
        status, e = self.first("io.read-failed")
        self.assertEqual((status, e["code"], e["message"], e["hint"], e["repair"]), (1, "GENERAL_ERROR", "the operating system failed the operation", self.OS_REFUSED, None))
        self.assertEqual(e["detail"], {"path": "/proc/self/mem", "errno": 5})

    @unittest.skipUnless(os.path.exists("/dev/full"), "Linux")
    def test_io_write_failed_has_no_hint_to_carry(self):
        # NOT REACHED with a hint: the refusal is for a write to standard output that failed, so it is written on standard error as text, which has no hint (docs/architecture.md, "The pin")
        p = self.reached("io.write-failed")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout, b"")
        self.assertEqual(p.stderr, b"table: io.write-failed: standard output took fewer bytes than were written\n")


class OwnPathRefusals(unittest.TestCase):
    """The refusals `qpath.cho` writes for a path that is not an operand (a query's `from 'path'` or `join 'path'`, the file of `--join`): `table`'s own copy of two rules of `toolbox.path`
    (`path.empty`, `path.too-long`), written with no hint while the package's carries one. They say the package's sentence now, read here from the installed package and not typed twice."""

    PATH_EMPTY = "name a path; an empty argument names nothing"
    PATH_TOO_LONG = "use a path of at most 4096 bytes"

    def setUp(self):
        self.s = Scratch()
        self.s.write("f.csv", "a,b,c\n1,2,3\n4,5,6\n")
        self.s.write("b.csv", "k,v\n1,x\n")

    def tearDown(self):
        self.s.cleanup()

    def go(self, *args):
        return run("--root", self.s.dir, *args)

    def check(self, r, rule, hint, detail):
        first, every = errors_of(r)
        self.assertEqual(r.status, 2)
        self.assertEqual((first["code"], first["rule"], first["hint"], first["repair"]), ("INVALID_ARGS", rule, hint, None))
        self.assertEqual(first["detail"], detail)
        self.assertEqual(first, every[0])
        return first

    def test_the_package_wording_is_the_one_pinned_here(self):
        # the operand's refusals (the package's own) say what this file says the query's say
        first = self.check(self.go(""), "path.empty", self.PATH_EMPTY, {"path": "", "position": 3})
        self.assertEqual(first["message"], "the path is empty")
        first = self.check(self.go("a" * 5000), "path.too-long", self.PATH_TOO_LONG, {"path": "a" * 5000, "position": 3})
        self.assertEqual(first["message"], "the path is longer than 4096 bytes")

    def test_a_query_from_an_empty_path(self):
        for query in ("select a from ''", "select a from ''  ", "select a, b from '' where a > 1"):
            with self.subTest(query):
                r = self.go("--query", query)
                first = self.check(r, "path.empty", self.PATH_EMPTY, {"path": ""})
                self.assertEqual(first["message"], "the path is empty")
        # the same words as the operand's, byte for byte, except the position the operand has and a path in a query has not
        operand = errors_of(self.go(""))[0]
        query = errors_of(self.go("--query", "select a from ''"))[0]
        self.assertEqual({k: v for k, v in operand.items() if k != "detail"}, {k: v for k, v in query.items() if k != "detail"})

    def test_a_query_join_of_an_empty_path(self):
        q = "select a.a from 'f.csv' as a join '' as b on a.a = b.a"
        for extra in ([], ["--explain"]):
            with self.subTest(extra):
                self.check(self.go(*extra, "--query", q), "path.empty", self.PATH_EMPTY, {"path": ""})

    def test_a_join_file_that_is_too_long(self):
        long = "a" * 5000
        for extra in ([], ["--explain"]):
            with self.subTest(extra):
                r = self.go(*extra, "--join", long, "--on", "a=a", "f.csv")
                first, every = errors_of(r)
                self.assertEqual((r.status, first["rule"], first["hint"], first["repair"]), (2, "path.too-long", self.PATH_TOO_LONG, None))
                self.assertEqual(first["detail"], {"path": long})
                self.assertEqual(first["message"], "the path is longer than 4096 bytes")

    def test_the_hints_in_the_source_are_the_packages(self):
        import glob
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[2]
        package = [t for t in (open(f).read() for f in sorted(glob.glob(str(BIN / "deps" / "*.cho")))) if 'path_error(heap, e, args, "path.empty", "the path is empty"' in t]
        self.assertEqual(len(package), 1, "the installed toolbox.path")
        own = open(root / "tools" / "table" / "qpath.cho").read()
        for rule, message in (("path.empty", "the path is empty"), ("path.too-long", "the path is longer than 4096 bytes")):
            theirs = re.findall(r'path_error\(heap, e, args, "%s", "%s", ("[^"]*")' % (re.escape(rule), re.escape(message)), package[0])
            mine = re.findall(r'refuse\(heap, e, "%s", "%s", ("[^"]*")' % (re.escape(rule), re.escape(message)), own)
            self.assertEqual(len(theirs), 1, rule)
            self.assertEqual(mine, theirs, rule)
            self.assertNotEqual(mine, ['""'], rule)


@unittest.skipUnless(MAIN_TABLE and os.path.exists(MAIN_TABLE), "MAIN_TABLE names the binary of the earlier pin")
class AgainstTheEarlierPin(unittest.TestCase):
    def test_the_hints_are_the_only_difference(self):
        s = Scratch()
        s.write("f.csv", "a,b,c\n1,2,3\n4,5,6\n")
        s.write("b.csv", "k,v\n1,x\n")
        changed = 0
        calls = [[], ["--select", "a"], ["--where", "a > 1"], ["--group", "a", "--agg", "count"], ["--report", "types"], ["--explain", "--select", "b"], ["--select", "zz"], ["--where", "zz = 1"],
                 ["--bogus"], ["--selct", "a"], ["--limit", "x"], ["--limit"], ["--format", "xml"], ["--where", ""], [""], ["a" * 5000], ["--join", "b.csv", "--on", "a=k"], ["--max-rows", "1"],
                 ["--join", "b.csv", "--on", "a=k", "--join-type", "x"], ["--query", "select a from 'f.csv'"], ["--query", ""], ["--introspect"], ["../f.csv"], ["/etc/passwd"], ["nofile.csv"]]
        try:
            for extra in calls:
                for fmt in ([], ["--format", "csv"], ["--format", "jsonl"]):
                    if "--format" in extra and fmt:
                        continue
                    argv = ["--root", str(s.dir), *extra, *fmt, "f.csv"] if extra[:1] != [""] and not (extra and len(extra[0]) > 4000) else ["--root", str(s.dir), *extra, *fmt]
                    new = run_argv([binary(), *argv[0:]])
                    old = run_argv([MAIN_TABLE, *argv])
                    norm = lambda r, b: (r.status, r.stdout.replace(b.encode(), b"{BIN}"), r.stderr.replace(b.encode(), b"{BIN}"))
                    n, o = norm(new, binary()), norm(old, MAIN_TABLE)
                    if n != o:
                        changed += 1
                        self.assertEqual((n[0], T.before(n[1]), n[2]), o, argv[2:])
            # the operating-system refusals (cancho-tools#53): an operand through a file, a file nobody may read, a read the system fails; the hint is the only difference.
            # At this pin these are the only calls that differ from the pin before it (MAIN_TABLE is that pin's binary): each differs in json, and not in csv or jsonl (standard error, no hint)
            s.write("denied.csv", "a\n1\n")
            os.chmod(s.dir / "denied.csv", 0)
            io_calls = [(["--root", str(s.dir), "f.csv/x"], None), (["--root", str(s.dir), "denied.csv"], test_rules.as_nobody)]
            if os.path.exists("/proc/self/mem"):
                io_calls.append((["/proc/self/mem"], None))
            io_changed = 0
            for operand, preexec in io_calls:
                for fmt in ([], ["--format", "csv"], ["--format", "jsonl"]):
                    import procs as subprocess
                    runs = []
                    for b in (binary(), MAIN_TABLE):
                        argv = [b, *operand[:-1], *fmt, operand[-1]]
                        p = subprocess.run(argv, capture_output=True, preexec_fn=preexec, input=b"")
                        runs.append((p.returncode, p.stdout.replace(b.encode(), b"{BIN}"), p.stderr.replace(b.encode(), b"{BIN}")))
                    n, o = runs
                    if n != o:
                        io_changed += 1
                        self.assertEqual(fmt, [], "only the json answer carries a hint")
                        self.assertEqual((n[0], T.before(n[1]), n[2]), o, (operand, fmt))
            self.assertEqual(io_changed, len(io_calls))
            os.chmod(s.dir / "denied.csv", 0o644)
        finally:
            try:
                os.chmod(s.dir / "denied.csv", 0o644)
            except OSError:
                pass
            s.cleanup()
        self.assertEqual(changed, 0, "the calls above differ from the pin before only in the operating-system refusals, which the calls of the loop after them cover")


if __name__ == "__main__":
    unittest.main()
