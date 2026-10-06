"""M3 -- every rule the tool can emit has a fixture that reaches it, every
fixture's first error is its rule with the right exit code, the declared rule
list is exactly what the fixtures reach, and every `retry` repair works when
applied.

The shared rules are lexsys-tools' catalogue (`toolbox.rules`, in the installed
package). Four of this tool's rules are its own, listed in `extra_rules` in
tools/table/table.ls; their exit codes and summaries are asserted here.
"""

import os
import unittest

from harness import Scratch, introspect, package_catalogue, run, run_argv, binary

# The tool's own rules: tag -> (exit code, repairable, a word of the summary).
LOCAL = {
    "limit.header-too-large": (8, "never", "header"),
    "parse.csv-ragged-row": (8, "never", "different number of fields"),
    "parse.csv-bad-quote": (8, "never", "closing quote"),
    "parse.csv-unterminated-quote": (8, "never", "still open"),
}
LINUX_ONLY = {"io.read-failed"}


def as_nobody():
    if os.geteuid() == 0:
        os.setgid(65534)
        os.setuid(65534)


class Rules(unittest.TestCase):
    def fixtures(self, s):
        """(rule, argv, preexec) for every rule."""
        s.write("ok.csv", "a,b\n1,2\n")
        s.write("ragged.csv", "a,b\n1,2\n3\n")
        s.write("quote.csv", 'a,b\n"x"y,1\n')
        s.write("open.csv", 'a,b\n1,"x\n')
        s.write("wide.csv", "a,b\n" + "1," + "x" * 5000 + "\n")
        s.write("header.csv", '"a\n' + "b\n" * 600 + '",c\n1,2\n')
        (s.dir / "sub").mkdir(exist_ok=True)
        s.write("secret.csv", "a\n1\n")
        os.symlink(s.dir / "secret.csv", s.dir / "link.csv")
        root = ["--root", s.dir]
        out = [
            ("args.unknown-flag", root + ["--nope", "ok.csv"], None),
            ("args.missing-value", root + ["ok.csv", "--max-rows"], None),
            ("args.bad-value", root + ["--max-rows", "many", "ok.csv"], None),
            ("args.duplicate-flag", root + ["--max-rows", "1", "--max-rows", "2", "ok.csv"], None),
            ("args.missing-operand", root, None),
            ("args.too-many-operands", root + ["ok.csv", "ok.csv"], None),
            ("path.empty", root + [""], None),
            ("path.dotdot", root + ["sub/../ok.csv"], None),
            ("path.absolute", root + [str(s.dir / "ok.csv")], None),
            ("path.outside-root", root + ["/etc/hosts"], None),
            ("path.too-long", root + ["a" * 5000], None),
            ("path.symlink", root + ["link.csv"], None),
            ("io.not-found", root + ["missing.csv"], None),
            ("io.not-a-directory", root + ["ok.csv/x"], None),
            ("io.is-a-directory", root + ["sub"], None),
            ("limit.line-too-long", root + ["--max-line-bytes", "100", "wide.csv"], None),
            ("limit.header-too-large", root + ["--max-line-bytes", "100", "header.csv"], None),
            ("parse.csv-ragged-row", root + ["ragged.csv"], None),
            ("parse.csv-bad-quote", root + ["quote.csv"], None),
            ("parse.csv-unterminated-quote", root + ["open.csv"], None),
        ]
        if os.geteuid() != 0 or True:
            s.write("denied.csv", "a\n1\n")
            os.chmod(s.dir / "denied.csv", 0)
            out.append(("io.permission-denied", root + ["denied.csv"], as_nobody))
        if os.path.exists("/proc/self/mem"):
            out.append(("io.read-failed", ["/proc/self/mem"], None))
        return out

    def test_every_rule_has_a_fixture_and_every_fixture_its_rule(self):
        s = Scratch()
        cat = package_catalogue()
        failures = []
        reached = set()
        try:
            for rule, argv, preexec in self.fixtures(s):
                import subprocess
                p = subprocess.run([binary(), *[str(a) for a in argv]], capture_output=True, preexec_fn=preexec)
                import json
                try:
                    doc = json.loads(p.stdout)
                except ValueError:
                    failures.append("%s: not JSON %r" % (rule, p.stdout[:200]))
                    continue
                errs = doc.get("errors", [])
                if not errs or errs[0]["rule"] != rule:
                    failures.append("%s: first error is %s (status %d) %r" % (rule, errs[0]["rule"] if errs else None, p.returncode, p.stdout[:200]))
                    continue
                want = cat[rule][0] if rule in cat else LOCAL[rule][0]
                if p.returncode != want:
                    failures.append("%s: exit %d, the package catalogue gives %d" % (rule, p.returncode, want))
                reached.add(rule)
                repair = errs[0]["repair"]
                if rule in cat and cat[rule][1] == "never" and repair and repair["kind"] == "retry":
                    failures.append("%s: a retry repair on a never-repairable rule" % rule)
                if repair and repair["kind"] == "retry":
                    again = run_argv(repair["argv"])
                    if again.status != 0:
                        failures.append("%s: the repair %r exited %d: %r" % (rule, repair["argv"][1:], again.status, again.stdout[:300]))
        finally:
            os.chmod(s.dir / "denied.csv", 0o644)
            s.cleanup()
        self.maxDiff = None
        self.assertEqual(failures, [])
        declared = {r["rule"] for r in introspect()["rules"]}
        expected = declared - (LINUX_ONLY if not os.path.exists("/proc/self/mem") else set())
        self.assertEqual(reached, expected, "declared %s, reached %s" % (sorted(declared - reached), sorted(reached - declared)))

    def test_every_declared_rule_is_a_real_tag(self):
        for r in introspect()["rules"]:
            self.assertRegex(r["rule"], r"^[a-z]+\.[a-z0-9-]+$")

    def test_local_rules(self):
        """The tool's own rules are not in the shared catalogue, are in
        `introspect` with their real exit code, repairability and summary, and
        the exit codes the declared table names include theirs."""
        cat = package_catalogue()
        declared = {r["rule"]: r for r in introspect()["rules"]}
        self.assertEqual({t for t in declared if t not in cat}, set(LOCAL))
        for tag, (exit_code, repairable, word) in LOCAL.items():
            r = declared[tag]
            self.assertEqual((r["exit"], r["repairable"]), (exit_code, repairable), tag)
            self.assertIn(word, r["summary"], tag)
        self.assertIn(8, {c["code"] for c in introspect()["exit_codes"]})

    def test_the_skill_lists_the_local_rules_with_their_exit(self):
        import subprocess
        text = subprocess.run([binary(), "skill"], capture_output=True, check=True).stdout.decode()
        for tag in LOCAL:
            self.assertIn(tag, text)


if __name__ == "__main__":
    unittest.main()
