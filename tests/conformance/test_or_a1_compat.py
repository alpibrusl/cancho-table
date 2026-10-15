"""G-COMPAT of slice A1 of docs/or-and-columns.md: every call that is valid today means the same, and every refusal of today is the same bytes except where the grammar is new.

Three things are held here, against `main` as it was when the slice started (62d60ad, slice A0 merged):

* the golden of main (or_a1_main_golden.json: its answer, refusal or not, to each of the 2,892 cases of or_a0.golden_cases(): the hand lists, the pinned refusals of query_cases.py,
  seeded soups of tokens, and the other forms an expression arrives in) and the golden of slice A0 (or_a0_golden.json, the 2,798 refusals of the rules `where.*` and `query.*`): after the
  change every answer is the same bytes, EXCEPT the cases whose expression is not read the same by the old grammar and the new one. That is decided by two references written in Python, one
  for the grammar before (`or_a1.parse(legacy=True)`, and query_ref_a0.py, main's reference front end) and one for the grammar now (or_a1.parse, query_ref.py): a case whose two readings are
  the same must give main's bytes, and a case whose two readings differ must give the answer the new reference says. The cases that changed are written down in
  or_a1_compat_changes.json (regenerate it with OR_A1_WRITE=1) so that the list is explicit and a change to it is a change of this file;
* near strings: a column called `or`, `not`, `and`, `in` or `contains`, bare, quoted and escaped, at every place of a condition, parse as they did (the near strings are in the goldens
  above, and here they are checked a second time against the binary of main when OR_A1_BASE names it);
* when OR_A1_BASE is a directory with main's build, every case is run on both binaries and the answers are compared by the same rule, whatever the goldens say.
"""

import json
import os
import pathlib
import unittest

import or_a0
import or_a1 as O
import query_ref
import query_ref_a0
from harness import Scratch, binary, run, run_argv

CHANGES = pathlib.Path(__file__).resolve().parent / "or_a1_compat_changes.json"
BASE = os.environ.get("OR_A1_BASE")


def expression_of(argv):
    """("where" | "query", the text) of a case, or None."""
    for k, a in enumerate(argv):
        if a == "--where" and k + 1 < len(argv):
            return "where", argv[k + 1]
        if a.startswith("--where="):
            return "where", a[len("--where="):]
        if a == "--query" and k + 1 < len(argv):
            return "query", argv[k + 1]
        if a.startswith("--query="):
            return "query", a[len("--query="):]
    return None


def reading_where(text, legacy):
    r = O.parse_result(text, legacy=legacy)
    return r if r[0] != "ok" else ("ok",)


def reading_query(text, module):
    try:
        module.translate(text)
        return ("ok",)
    except module.QueryError as e:
        if e.kind == "mixed":
            return ("mixed", e.offset)
        if e.kind == "too-long":
            return ("too-long",)
        what = e.expected if e.kind in ("syntax", "group-mismatch") else e.feature
        return (e.kind, e.offset, what)


def tool_where(text, doc):
    """The reading of the tool: what the new reference should say, from the document the tool wrote (None for a form that has no document)."""
    if doc is None:
        return None
    if doc.get("ok"):
        return ("ok",)
    e = doc["error"]
    if e["rule"] == "where.syntax":
        return ("syntax", e["detail"]["offset"], e["detail"]["expected"])
    if e["rule"] == "where.mixed-and-or":
        return ("mixed", e["detail"]["offset"])
    return ("ok",)          # refused for another reason, after the expression was read


def tool_query(text, doc):
    if doc is None:
        return None
    if doc.get("ok"):
        return ("ok",)
    e = doc["error"]
    if e["rule"] == "query.mixed-and-or":
        return ("mixed", e["detail"]["offset"])
    if e["rule"] == "query.too-long":
        return ("too-long",)
    if e["rule"].startswith("query."):
        kind = e["rule"][6:]
        d = e["detail"]
        if kind == "syntax" and "column" in d:
            return ("literal",)             # the engine's literal checks, mapped back to the query: not the grammar's
        return (kind, d["offset"], d.get("expected", d.get("feature")))
    return ("ok",)


def as_doc(r):
    try:
        lines = r.stdout.decode("utf-8").splitlines()
        return json.loads(lines[0]) if len(lines) == 1 else None
    except (ValueError, IndexError):
        return None


class Compat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", or_a0.DATA)
        cls.dir = str(cls.s.dir)
        cls.cases = or_a0.golden_cases()
        cls.now = {row[0]: row for row in or_a0.run_cases(cls.cases, cls.dir, run)}

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def classify(self):
        """[(case id, kind, expression, class, old, new)] for every case whose expression the two grammars read differently, and the check that every other case is main's bytes."""
        main = json.loads(or_a0.MAIN_GOLDEN.read_text())["cases"]
        a0 = json.loads(or_a0.GOLDEN.read_text())["cases"]
        changes = []
        same = 0
        for kind, argv in self.cases:
            cid = or_a0.case_id(kind, argv)
            row = self.now[cid]
            expr = expression_of(argv)
            r_old = r_new = None
            if expr is not None and expr[0] == "where":
                r_old, r_new = reading_where(expr[1], True), reading_where(expr[1], False)
            elif expr is not None:
                r_old, r_new = reading_query(expr[1], query_ref_a0), reading_query(expr[1], query_ref)
            g_status, g_rule, g_sha = main[cid]
            if r_old == r_new:
                same += 1
                self.assertEqual((row[1], row[2], row[3]), (g_status, g_rule, g_sha), "%s %r: read the same by both grammars, so main's bytes" % (cid, expr))
                if cid in a0:
                    self.assertEqual((row[1], row[2], row[4]), (a0[cid][0], a0[cid][1], a0[cid][3]), cid)
                continue
            # read differently: the answer is the one the new reference gives
            self.assertNotEqual((row[1], row[2], row[3]), (g_status, g_rule, g_sha), "%s %r: the references say the grammars differ, and the tool answered main's bytes" % (cid, expr))
            args = [a.replace("{DIR}", self.dir) for a in argv]
            doc = None if kind in ("csv", "jsonl", "query-csv") else as_doc(run(*args))
            tool = tool_where(expr[1], doc) if expr[0] == "where" else tool_query(expr[1], doc)
            if tool == ("literal",):
                tool = ("ok",)
            if tool is not None:
                self.assertEqual(tool, r_new, "%s %r" % (cid, expr))
            changes.append([cid, kind, expr[1], list(r_old), list(r_new)])
        return changes, same

    def test_golden(self):
        changes, same = self.classify()
        self.assertGreater(same, 1000)
        self.assertGreater(len(changes), 300)
        if os.environ.get("OR_A1_WRITE"):
            CHANGES.write_text(json.dumps({"count": len(changes), "same": same, "changes": changes}, sort_keys=True, indent=0, separators=(",", ":")) + "\n")
        recorded = json.loads(CHANGES.read_text())
        self.assertEqual((recorded["count"], recorded["same"]), (len(changes), same))
        self.assertEqual(recorded["changes"], json.loads(json.dumps(changes)), "the list of changed cases moved: regenerate it with OR_A1_WRITE=1 and read the diff")

    @unittest.skipUnless(BASE, "OR_A1_BASE names the directory of main's build")
    def test_against_the_binary_of_main(self):
        base = str(pathlib.Path(BASE) / "table")
        sc = self.dir
        differing = []
        for kind, argv in self.cases:
            args = [a.replace("{DIR}", sc) for a in argv]
            expr = expression_of(argv)
            old = run_argv([base, *args])
            new = run(*args)
            if expr is not None:
                r_old, r_new = (reading_where(expr[1], True), reading_where(expr[1], False)) if expr[0] == "where" else (reading_query(expr[1], query_ref_a0), reading_query(expr[1], query_ref))
            else:
                r_old = r_new = None
            same = (old.status, old.stdout, old.stderr) == (new.status, new.stdout.replace(binary().encode(), base.encode()), new.stderr)
            if not same:
                normal = lambda r, b: r.stdout.replace(b.encode(), b"{BIN}") + r.stderr.replace(b.encode(), b"{BIN}")
                same = old.status == new.status and normal(old, base) == normal(new, binary())
            if r_old == r_new:
                self.assertTrue(same, "%r: read the same by both grammars and answered differently\n main: %r\n here: %r" % (argv, old.stdout[:400], new.stdout[:400]))
            elif same:
                differing.append(argv)
        self.assertEqual(differing, [], "the grammars differ and the answer is main's bytes")


if __name__ == "__main__":
    unittest.main()
