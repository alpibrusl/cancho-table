"""D11 -- the tool describes itself from the tables it runs on."""

import subprocess
import unittest

from harness import binary, introspect


class Describe(unittest.TestCase):
    def test_introspect(self):
        d = introspect()
        self.assertEqual(d["tool"], "table")
        flags = {f["name"] for f in d["flags"]}
        self.assertEqual(flags, {"--root", "--delimiter", "--report", "--select", "--where", "--group", "--agg", "--sort", "--top", "--limit", "--from", "--max-bytes", "--max-groups", "--max-distinct", "--max-state-bytes", "--order-by", "--max-sort-rows", "--max-rows", "--max-line-bytes", "--format", "--threads", "--chunk-bytes", "--parallel-min-bytes"})
        self.assertEqual({l["name"]: (l["default"], l["ceiling"]) for l in d["limits"]},
                         {"threads": (1, 64), "chunk-bytes": (4194304, 1073741824), "parallel-min-bytes": (1048576, 1073741824), "limit": (1000, 1000000), "max-bytes": (1048576, 67108864), "max-groups": (100000, 1000000), "max-distinct": (100000, 10000000), "max-state-bytes": (67108864, 1073741824), "max-sort-rows": (1000000, 20000000), "max-rows": (10000000, 1000000000), "max-line-bytes": (1048576, 16777216)})
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
