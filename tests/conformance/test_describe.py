"""D11 -- the tool describes itself from the tables it runs on."""

import subprocess
import unittest

from harness import binary, introspect


class Describe(unittest.TestCase):
    def test_introspect(self):
        d = introspect()
        self.assertEqual(d["tool"], "table")
        flags = {f["name"] for f in d["flags"]}
        self.assertEqual(flags, {"--root", "--delimiter", "--max-rows", "--max-line-bytes", "--format"})
        self.assertEqual({l["name"]: (l["default"], l["ceiling"]) for l in d["limits"]},
                         {"max-rows": (10000000, 1000000000), "max-line-bytes": (1048576, 16777216)})
        self.assertTrue(d["guarantees"]["bounded_memory"])
        self.assertTrue(d["guarantees"]["deterministic"])
        self.assertFalse(d["guarantees"]["atomic"])

    def test_skill(self):
        p = subprocess.run([binary(), "skill"], capture_output=True, check=True)
        text = p.stdout.decode()
        self.assertIn("table", text)
        self.assertIn("parse.csv-ragged-row", text)
        self.assertIn("--max-line-bytes", text)


if __name__ == "__main__":
    unittest.main()
