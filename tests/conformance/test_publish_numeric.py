"""The rule of what scripts/publish_numeric.py may publish: no module that is not a stable interface (docs/numeric-package.md).

A published module imports `std.*` and other published modules only, and does not declare the table's plan struct `Query`. The script's own check is run on the
tree (it passes only when the committed set obeys the rule), and the rule on texts, so that it is known to fail.
"""

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import publish_numeric as pn  # noqa: E402


class StableInterface(unittest.TestCase):
    def test_the_plan_struct_is_refused(self):
        for decl in ("pub res struct Query {", "res struct Query {", "pub struct Query {", "struct Query {", "    pub res struct Query {"):
            with self.subTest(decl):
                self.assertEqual(len(pn.interface_problems("module m;\n" + decl + "\n}\n", "m", [])), 1)

    def test_an_internal_import_is_refused(self):
        bad = pn.interface_problems("module m;\nimport plan;\n", "m", ["plan"])
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("not published", bad[0])

    def test_a_name_that_only_looks_like_it_is_not(self):
        text = "module m;\n// a res struct Query is the plan\npub struct QueryLike {\n}\npub fn f() -> [] int { return 0; }\n"
        self.assertEqual(pn.interface_problems(text, "m", []), [])

    def test_the_published_modules_obey_it(self):
        for m in pn.MODULES:
            text = (ROOT / "tools" / "table" / (m + ".cho")).read_text()
            imports = [i for i in __import__("re").findall(r"^import\s+([\w.]+)\s*;", text, __import__("re").M) if not i.startswith("std.")]
            self.assertEqual(pn.interface_problems(text, m, imports), [], m)


if __name__ == "__main__":
    unittest.main()
