"""G-COMPAT of slice A2 of docs/or-and-columns.md (`not`): every call that is valid today means the same, and every refusal of today is the same bytes except where the grammar is new.

Held against `main` as it was when the slice started (a3af5ec, slice A1 merged), by the golden of its answers to 5,585 cases (not_a2_base_golden.json, made before a line of A2 was written;
not_a2_cases.py): the 2,892 cases of the earlier slices (whose own goldens, or_a0_golden.json and or_a1_main_golden.json, are unchanged, and are still held by test_or_a1_compat.py for every
case that A2 does not read differently) and the cases made for `not`. After the change every answer is the same bytes, EXCEPT the cases whose expression is read differently by the grammar of
A1 and the grammar of A2. That is decided by two references written in Python, one for each (or_a1.parse and query_ref_a1.py, frozen, and not_a2.parse and query_ref.py): a case whose two
readings are the same must give the bytes of the golden (a mix that is refused in both may differ in its repair, which now can say a `not`, and in nothing else); a case whose two readings
differ must give the answer the new reference says. The cases that changed are written down in not_a2_compat_changes.json (regenerate it with NOT_A2_WRITE=1) so that the list is explicit and a
change to it is a change of this file.

When NOT_A2_BASE names the directory of main's build, every case is also run on both binaries and the answers compared by the same rule, whatever the golden says.
"""

import json
import os
import pathlib
import unittest

import not_a2 as N
import not_a2_cases as K
import or_a0
import or_a1 as O
import query_ref_a1
import query_ref_a2 as query_ref          # the grammar of A2, frozen: slice B1 (`as`) came after this file, and test_rename_b1_compat.py holds the cases it moved
import rename_b1
import test_or_a1_compat as C1
from harness import Scratch, binary, run, run_argv

CHANGES = pathlib.Path(__file__).resolve().parent / "not_a2_compat_changes.json"
BASE = os.environ.get("NOT_A2_BASE")


def lat(text):
    return text.encode("utf-8").decode("latin-1")


def readings(expr):
    """(the reading of slice A1, the reading of slice A2) of an expression (kind, text)."""
    if expr is None:
        return None, None
    kind, text = expr
    if kind == "where":
        a = O.parse_result(lat(text))
        b = N.parse_result(lat(text))
        return (a if a[0] != "ok" else ("ok",)), (b if b[0] != "ok" else ("ok",))
    return C1.reading_query(lat(text), query_ref_a1), C1.reading_query(lat(text), query_ref)


class Compat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        cls.s.write("t.csv", or_a0.DATA)
        cls.dir = str(cls.s.dir)
        cls.cases = {}
        for kind, argv in K.cases():
            cls.cases[or_a0.case_id(kind, argv)] = (kind, argv)
        cls.now = {row[0]: row for row in or_a0.run_cases(list(cls.cases.values()), cls.dir, run)}

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def judge(self, cid, kind, argv, row, old):
        """Class of the case: "same" (the golden's bytes), or ("repair", ...) / ("reading", ...) for a change that is listed. `row` and `old` are (status, rule, sha, sha without the repair)."""
        expr = C1.expression_of(argv)
        if C1.expr_b1(argv):
            return None                    # slice B1 reads it differently from slice A2: held by test_rename_b1_compat.py
        if C1.hint_moved(argv):
            self.assertEqual(row[:2], old[:2], cid)       # slice J3b rewrote the hint of the features join and subquery: held by test_join_j3b.py
            return None
        r1, r2 = readings(expr)
        if row[:3] == old[:3]:
            # (a refusal in the formats that carry no detail -- csv, JSON lines -- is one line of text that does not say where or what was expected: the same bytes for two readings)
            if kind not in ("csv", "jsonl", "query-csv") and not (r1 != r2 and r1[0] == r2[0] == "syntax" and "stderr" in kind):
                self.assertEqual(r1, r2, "%s %r: the references say the grammars differ, and the tool answered the bytes of main" % (cid, expr))
            return None
        if r1 == r2:
            # the same reading, so the same refusal at the same place with the same words: only the repair may differ. It can: a repair is the corrected query or the two readings of a mix,
            # offered only if the rest of the expression is whole, and the rest may now hold a `not` (a `"x"` made a value in a query that also has `not b < 1`)
            self.assertEqual(row[3], old[3], "%s %r: read the same by both grammars, and answered differently in more than the repair" % (cid, expr))
            return [cid, kind, expr[1], list(r1), list(r2), "repair"]
        args = [a.replace("{DIR}", self.dir) for a in argv]
        doc = None if kind in ("csv", "jsonl", "query-csv") else C1.as_doc(run(*args))
        tool = C1.tool_where(expr[1], doc) if expr[0] == "where" else C1.tool_query(expr[1], doc)
        if tool == ("literal",):
            tool = ("ok",)
        if tool is not None:
            self.assertEqual(tool, r2, "%s %r" % (cid, expr))
        return [cid, kind, expr[1], list(r1), list(r2), "reading"]

    def classify(self):
        golden = json.loads(K.GOLDEN.read_text())["cases"]
        changes = []
        same = 0
        for cid, (kind, argv) in self.cases.items():
            g = golden[cid]
            row = self.now[cid]
            c = self.judge(cid, kind, argv, (row[1], row[2], row[3], row[4]), (g[0], g[1], g[2], g[3]))
            if c is None:
                same += 1
            else:
                changes.append(c)
        return changes, same

    def test_golden(self):
        changes, same = self.classify()
        self.assertGreater(same, 2500)
        self.assertGreater(len(changes), 700)
        for c in changes:
            # nothing changes unless the expression has the word `not` in it (an escaped n\ot is the word, as an escaped \and is the connective)
            self.assertRegex(c[2].lower().replace("\\", ""), r"\bnot\b", c)
        if os.environ.get("NOT_A2_WRITE"):
            CHANGES.write_text(json.dumps({"count": len(changes), "same": same, "changes": changes}, sort_keys=True, indent=0, separators=(",", ":")) + "\n")
        recorded = json.loads(CHANGES.read_text())
        self.assertEqual((recorded["count"], recorded["same"]), (len(changes), same))
        self.assertEqual(recorded["changes"], json.loads(json.dumps(changes)), "the list of changed cases moved: regenerate it with NOT_A2_WRITE=1 and read the diff")

    def test_every_case_that_slice_a1_pinned_and_a2_does_not_read_differently_is_unchanged(self):
        # the cases of the earlier goldens: those that A2 reads as A1 did are held by test_or_a1_compat.py to the bytes of the older goldens; here, that the ones it defers are listed
        c1 = C1.Compat
        c1.setUpClass()
        try:
            t = c1("test_golden")
            t.classify()
            deferred = set(t.deferred)
        finally:
            c1.tearDownClass()
        listed = {c[0] for c in json.loads(CHANGES.read_text())["changes"]}
        self.assertGreater(len(deferred), 20)
        self.assertEqual(deferred - listed, set(), "a case that test_or_a1_compat.py defers is not in the list of A2's changes")

    def test_the_new_forms_are_the_only_new_acceptances(self):
        # every case that was refused by A1 and is answered now is an expression with a `not` that is the keyword of the new grammar (or a query with one)
        golden = json.loads(K.GOLDEN.read_text())["cases"]
        became = []
        for cid, (kind, argv) in self.cases.items():
            g, row = golden[cid], self.now[cid]
            if g[0] != 0 and row[1] == 0 and not C1.expr_b1(argv):
                expr = C1.expression_of(argv)
                became.append(expr[1] if expr else None)
        self.assertGreater(len(became), 100)
        for text in became:
            self.assertIsNotNone(text)
            self.assertRegex(text.lower().replace("\\", ""), r"\bnot\b", text)         # an escaped n\ot is the word not, as an escaped \and is the word and

    @unittest.skipUnless(BASE, "NOT_A2_BASE names the directory of main's build")
    def test_against_the_binary_of_main(self):
        base = str(pathlib.Path(BASE) / "table")
        changes = 0
        for cid, (kind, argv) in self.cases.items():
            args = [a.replace("{DIR}", self.dir) for a in argv]
            old = run_argv([base, *args])
            new = run(*args)
            norm = lambda r, b: (r.stdout.replace(b.encode(), b"{BIN}"), r.stderr.replace(b.encode(), b"{BIN}"))
            rows = []
            for r, b in ((old, base), (new, binary())):
                text = or_a0.normalise(r.stdout, r.stderr, self.dir, b)
                try:
                    doc = json.loads(r.stdout.decode("utf-8").splitlines()[0])
                    rule = None if doc.get("ok") else doc["error"]["rule"]
                except (ValueError, IndexError, KeyError):
                    rule = None
                rows.append((r.status, rule, or_a0.digest(text), or_a0.digest(or_a0.blank_repair(text))))
            if self.judge(cid, kind, argv, rows[1], rows[0]) is not None:
                changes += 1
        self.assertGreater(changes, 700)


if __name__ == "__main__":
    unittest.main()
