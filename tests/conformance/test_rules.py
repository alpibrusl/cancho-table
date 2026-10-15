"""M3 -- every rule the tool can emit has a fixture that reaches it, every
fixture's first error is its rule with the right exit code, the declared rule
list is exactly what the fixtures reach, and every `retry` repair works when
applied.

The shared rules are cancho-tools' catalogue (`toolbox.rules`, in the installed
package). Four of this tool's rules are its own, listed in `extra_rules` in
tools/table/table.cho; their exit codes and summaries are asserted here.
"""

import os
import re
import unittest

from harness import Scratch, introspect, package_catalogue, run, run_argv, binary

# The tool's own rules: tag -> (exit code, repairable, a word of the summary).
LOCAL = {
    "limit.header-too-large": (8, "never", "header"),
    "parse.csv-ragged-row": (8, "never", "different number of fields"),
    "parse.csv-bad-quote": (8, "never", "closing quote"),
    "parse.csv-unterminated-quote": (8, "never", "still open"),
    "limit.record-too-large": (8, "never", "record"),
    "limit.output-too-large": (8, "never", "first row"),
    "limit.too-many-rows": (8, "never", "--max-rows"),
    "select.unknown-column": (3, "sometimes", "not a column"),
    "select.ambiguous-column": (8, "never", "more than one column"),
    "limit.too-many-sort-rows": (8, "never", "--max-sort-rows"),
    "limit.too-many-groups": (8, "never", "--max-groups"),
    "limit.too-many-distinct": (8, "never", "--max-distinct"),
    "limit.state-too-large": (8, "never", "--max-state-bytes"),
    "column.unknown": (3, "never", "--where, --group, --agg or --on"),
    "column.ambiguous": (8, "never", "more than one column"),
    "where.syntax": (2, "sometimes", "offset"),
    "where.mixed-and-or": (2, "sometimes", "parentheses"),
    "agg.bad-spec": (2, "never", "count, sum:COL"),
    "sort.unknown-key": (2, "never", "no output column"),
    "value.not-integer": (8, "never", "exact integer"),
    "value.integer-overflow": (8, "never", "64 bits"),
    "value.not-decimal": (8, "never", "decimal"),
    "value.decimal-scale": (8, "sometimes", "fractional digits"),
    "value.decimal-too-wide": (8, "never", "18"),
    "column.type-conflict": (2, "never", "numeric type"),
    "value.not-float": (8, "never", "float"),
    "value.not-finite": (8, "never", "inf or nan"),
    "value.float-range": (8, "never", "double"),
    "limit.number-too-long": (8, "never", "1,100"),
    "agg.float-overflow": (8, "never", "largest double"),
    "query.syntax": (2, "sometimes", "byte offset"),
    "query.mixed-and-or": (2, "sometimes", "parentheses"),
    "query.unsupported": (2, "sometimes", "leaves out"),
    "query.group-mismatch": (2, "sometimes", "group by columns"),
    "query.too-long": (2, "never", "4096"),
    "input.fields-required": (2, "sometimes", "no header"),
    "input.bad-fields": (2, "never", "list of paths"),
    "parse.jsonl-syntax": (8, "never", "strict JSON"),
    "parse.jsonl-not-object": (8, "never", "not an object"),
    "parse.jsonl-missing-field": (8, "sometimes", "has no value"),
    "parse.jsonl-not-scalar": (8, "never", "object or an array"),
    "parse.jsonl-duplicate-key": (8, "never", "occurs twice"),
    "limit.json-too-many-nodes": (8, "never", "--max-json-nodes"),
    "output.duplicate-column": (2, "never", "same name"),
    "output.not-representable": (2, "never", "UTF-8"),
    "join.column-collision": (2, "sometimes", "both files"),
    "limit.too-many-join-rows": (8, "never", "--max-join-rows"),
    "rename.syntax": (2, "never", "offset"),
    "rename.unknown-target": (3, "sometimes", "output column"),
    "rename.ambiguous-target": (8, "never", "more than one output column"),
    "rename.repeated-target": (2, "never", "twice"),
}
# rules whose hint is the feature's, so it varies with the detail: the summary says so and does not repeat one
VARIABLE_HINT = {"query.unsupported"}
LINUX_ONLY = {"io.read-failed", "io.write-failed"}


def to_full():
    """Standard output is /dev/full: every write fails (Linux)."""
    os.dup2(os.open("/dev/full", os.O_WRONLY), 1)


def summary_says(rule, summary, error):
    """What a rule's summary says it hints and repairs with is what the refusal says: the hint
    verbatim (a `;` is a `,`, the catalogue has no other separator) and the repair kind
    (null is `none`). Problems, as a list of strings."""
    m = re.search(r"\. Hint: (.*)\. Repair: (none|retry|choose)\.$", summary)
    if not m:
        return ["%s: the summary has no `Hint: ... Repair: ...` ending: %r" % (rule, summary)]
    out = []
    hint = (error["hint"] or "").replace(";", ",")
    kind = error["repair"]["kind"] if error["repair"] else "none"
    if m.group(1) != hint:
        out.append("%s: the summary hints %r, the refusal %r" % (rule, m.group(1), hint))
    if m.group(2) != kind:
        out.append("%s: the summary repairs with %s, the refusal with %s" % (rule, m.group(2), kind))
    return out


def as_nobody():
    if os.geteuid() == 0:
        os.setgid(65534)
        os.setuid(65534)


class Rules(unittest.TestCase):
    def fixtures(self, s):
        """(rule, argv, preexec) for every rule."""
        s.write("ok.csv", "a,b\n1,2\n")
        s.write("rec.csv", 'a,b\n1,"' + "x\n" * 600 + '"\n')
        s.write("many.csv", "a\n1\n2\n3\n")
        s.write("text.csv", "a,b\n1,x\n")
        s.write("big.csv", "a\n99999999999999999999\n")
        s.write("ovf.csv", "a\n1e999\n")
        s.write("ovfsum.csv", "a\n1.7976931348623157e308\n1.7976931348623157e308\n")
        s.write("scale.csv", "a\n1.25\n")
        s.write("nan.csv", "a\nnan\n")
        s.write("long.csv", "a\n" + "1" * 1101 + "\n")
        s.write("wide18.csv", "a\n99999999999999999.99\n")
        s.write("dup.csv", "a,a\n1,2\n")
        s.write("ragged.csv", "a,b\n1,2\n3\n")
        s.write("quote.csv", 'a,b\n"x"y,1\n')
        s.write("open.csv", 'a,b\n1,"x\n')
        s.write("wide.csv", "a,b\n" + "1," + "x" * 5000 + "\n")
        s.write("header.csv", '"a\n' + "b\n" * 600 + '",c\n1,2\n')
        s.write("jp.csv", "k,v\na,1\na,2\n")
        s.write("ok.jsonl", '{"a":1}\n')
        s.write("badhead.csv", b"\xff\n1\n")
        s.write("bad.jsonl", '{"a":1,}\n')
        s.write("arr.jsonl", '[1]\n')
        s.write("miss.jsonl", '{"b":1}\n')
        s.write("nested.jsonl", '{"a":{}}\n')
        s.write("twice.jsonl", '{"a":1,"a":2}\n')
        (s.dir / "sub").mkdir(exist_ok=True)
        s.write("secret.csv", "a\n1\n")
        os.symlink(s.dir / "secret.csv", s.dir / "link.csv")
        root = ["--root", s.dir]
        out = [
            ("args.unknown-flag", root + ["--max-rowz", "5", "ok.csv"], None),
            ("args.missing-value", root + ["ok.csv", "--max-rows"], None),
            ("args.bad-value", root + ["--max-rows", "many", "ok.csv"], None),
            ("args.duplicate-flag", root + ["--max-rows", "1", "--max-rows", "2", "ok.csv"], None),
            ("args.conflict", root + ["--select", "a", "--format", "text", "ok.csv"], None),
            ("args.required-flag", root + ["--limit", "3", "ok.csv"], None),
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
            ("limit.record-too-large", root + ["--select", "a", "--max-line-bytes", "100", "rec.csv"], None),
            ("limit.output-too-large", root + ["--select", "b", "--max-bytes", "10", "wide.csv"], None),
            ("limit.too-many-rows", root + ["--select", "a", "--format", "csv", "--max-rows", "1", "ragged.csv"], None),
            ("select.unknown-column", root + ["--select", "A", "ok.csv"], None),
            ("select.ambiguous-column", root + ["--select", "a", "dup.csv"], None),
            ("limit.too-many-sort-rows", root + ["--order-by", "a", "--format", "csv", "--max-sort-rows", "2", "many.csv"], None),
            ("limit.too-many-groups", root + ["--group", "a", "--max-groups", "1", "many.csv"], None),
            ("limit.too-many-distinct", root + ["--agg", "distinct:a", "--max-distinct", "1", "many.csv"], None),
            ("limit.state-too-large", root + ["--group", "a", "--max-state-bytes", "1", "many.csv"], None),
            ("column.unknown", root + ["--where", "zz = 1", "ok.csv"], None),
            ("column.ambiguous", root + ["--where", "a = 1", "dup.csv"], None),
            ("where.syntax", root + ["--where", "not a = 1", "ok.csv"], None),
            ("where.mixed-and-or", root + ["--where", "a = 1 or b = 2 and a = 3", "ok.csv"], None),     # the one whose repair is a choose (slice A1)
            ("agg.bad-spec", root + ["--group", "a", "--agg", "avg:a", "ok.csv"], None),
            ("sort.unknown-key", root + ["--group", "a", "--sort", "zz", "ok.csv"], None),
            ("value.not-integer", root + ["--where", "b:int > 0", "text.csv"], None),
            ("value.integer-overflow", root + ["--where", "a:int > 0", "big.csv"], None),
            ("value.not-decimal", root + ["--where", "b:dec(2) > 0", "text.csv"], None),
            ("value.decimal-scale", root + ["--where", "a:dec(1) > 0", "scale.csv"], None),
            ("value.decimal-too-wide", root + ["--where", "a:dec(2) > 0", "wide18.csv"], None),
            ("column.type-conflict", root + ["--where", "a:dec(2) > 0 and a:int > 0", "ok.csv"], None),
            ("value.not-float", root + ["--where", "b:float > 0", "text.csv"], None),
            ("value.not-finite", root + ["--where", "a:float > 0", "nan.csv"], None),
            ("value.float-range", root + ["--where", "a:float > 0", "ovf.csv"], None),
            ("limit.number-too-long", root + ["--where", "a:float > 0", "long.csv"], None),
            ("agg.float-overflow", root + ["--agg", "sum:a:float", "ovfsum.csv"], None),
            ("query.syntax", root + ["--query", "selct a from 'ok.csv'"], None),
            ("query.unsupported", root + ["--query", "select a from 'ok.csv' where b like '%x%'"], None),
            ("query.mixed-and-or", root + ["--query", "select a from 'ok.csv' where a = 1 or b = 2 and a = 3"], None),
            ("query.group-mismatch", root + ["--query", "select a from 'ok.csv' group by a"], None),
            ("query.too-long", root + ["--query", "select a from 'ok.csv' where b = '" + "x" * 4100 + "'"], None),
            ("input.fields-required", root + ["--input-format", "jsonl", "ok.jsonl"], None),
            ("input.bad-fields", root + ["--input-format", "jsonl", "--fields", "a..b", "ok.jsonl"], None),
            ("parse.jsonl-syntax", root + ["--input-format", "jsonl", "--fields", "a", "bad.jsonl"], None),
            ("parse.jsonl-not-object", root + ["--input-format", "jsonl", "--fields", "a", "arr.jsonl"], None),
            ("parse.jsonl-missing-field", root + ["--input-format", "jsonl", "--fields", "a", "miss.jsonl"], None),
            ("parse.jsonl-not-scalar", root + ["--input-format", "jsonl", "--fields", "a", "nested.jsonl"], None),
            ("parse.jsonl-duplicate-key", root + ["--input-format", "jsonl", "--fields", "a", "twice.jsonl"], None),
            ("output.duplicate-column", root + ["--select", "a,a", "--format", "jsonl", "ok.csv"], None),
            ("output.not-representable", root + ["--rows", "--format", "jsonl", "badhead.csv"], None),
            ("limit.json-too-many-nodes", root + ["--input-format", "jsonl", "--fields", "a", "--max-json-nodes", "1", "ok.jsonl"], None),
            ("join.column-collision", root + ["--join", "jp.csv", "--on", "k=k", "jp.csv"], None),
            ("rename.syntax", root + ["--select", "a", "--rename", "a", "ok.csv"], None),
            ("rename.unknown-target", root + ["--select", "a", "--rename", "A=x", "ok.csv"], None),
            ("rename.ambiguous-target", root + ["--select", "a,a", "--rename", "a=x", "ok.csv"], None),
            ("rename.repeated-target", root + ["--select", "a", "--rename", "a=x,#1=y", "ok.csv"], None),
            ("limit.too-many-join-rows", root + ["--join", "jp.csv", "--on", "k=k", "--join-prefix", "r_", "--max-join-rows", "1", "jp.csv"], None),
        ]
        if os.geteuid() != 0 or True:
            s.write("denied.csv", "a\n1\n")
            os.chmod(s.dir / "denied.csv", 0)
            out.append(("io.permission-denied", root + ["denied.csv"], as_nobody))
        if os.path.exists("/proc/self/mem"):
            out.append(("io.read-failed", ["/proc/self/mem"], None))
        if os.path.exists("/dev/full"):
            out.append(("io.write-failed", root + ["ok.csv"], to_full))
        return out

    def test_every_rule_has_a_fixture_and_every_fixture_its_rule(self):
        s = Scratch()
        cat = package_catalogue()
        failures = []
        reached = set()
        summaries = {r["rule"]: r["summary"] for r in introspect()["rules"]}
        try:
            for rule, argv, preexec in self.fixtures(s):
                import procs as subprocess
                p = subprocess.run([binary(), *[str(a) for a in argv]], capture_output=True, preexec_fn=preexec, input=b"")
                import json
                try:
                    errs = json.loads(p.stdout).get("errors", [])
                except ValueError:
                    # csv and text answer on standard error: `table: rule: message`.
                    errs = [{"rule": l.split(": ")[1], "repair": None} for l in p.stderr.decode().splitlines() if l.startswith("table: ")]
                if not errs or errs[0]["rule"] != rule:
                    failures.append("%s: first error is %s (status %d) %r %r" % (rule, errs[0]["rule"] if errs else None, p.returncode, p.stdout[:200], p.stderr[:200]))
                    continue
                want = cat[rule][0] if rule in cat else LOCAL[rule][0]
                if p.returncode != want:
                    failures.append("%s: exit %d, expected %d" % (rule, p.returncode, want))
                reached.add(rule)
                if rule in LOCAL and "hint" in errs[0] and rule not in VARIABLE_HINT:
                    failures += summary_says(rule, summaries[rule], errs[0])
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

    def test_the_summaries_of_the_rules_that_answer_on_standard_error_in_csv(self):
        """limit.too-many-rows and limit.too-many-sort-rows are reached above as csv, which has no hint to
        compare; in json they have one."""
        s = Scratch()
        try:
            s.write("many.csv", "a\n1\n2\n3\n")
            summaries = {r["rule"]: r["summary"] for r in introspect()["rules"]}
            for rule, argv in (("limit.too-many-rows", ["--order-by", "a", "--max-rows", "1"]),
                               ("limit.too-many-sort-rows", ["--order-by", "a", "--max-sort-rows", "2"])):
                r = run("--root", s.dir, *argv, "many.csv")
                self.assertEqual(r.first_rule(), rule)
                self.assertEqual(summary_says(rule, summaries[rule], r.error()), [])
        finally:
            s.cleanup()

    def test_the_skill_lists_the_local_rules_with_their_exit(self):
        import procs as subprocess
        text = subprocess.run([binary(), "skill"], capture_output=True, check=True).stdout.decode()
        for tag in LOCAL:
            self.assertIn(tag, text)


if __name__ == "__main__":
    unittest.main()
