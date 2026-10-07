"""M6 -- the authority the binary carries is the compiler's report, within the
ceiling in tools.toml, with nothing a reader of one file has no use for; and
on Linux the process does what the report says under strace."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

from harness import ROOT, binary, introspect

FORBIDDEN = {"ffi", "net_out", "net_in", "clock"}
# What a tool that reads one file and writes one document may hold; a label
# beyond these has to be argued for in tools.toml, and not by this test.
NEEDS = {"args", "conc", "dir_read", "err_write", "file_read", "fs_read", "heap", "io_write"}


def label_text(label):
    return label["name"] if label["argument"] is None else '%s("%s")' % (label["name"], label["argument"])


class Authority(unittest.TestCase):
    def test_introspect_is_the_committed_report(self):
        committed = json.loads((ROOT / "manifests" / "table.authority.json").read_text())
        self.assertEqual(introspect()["authority"], committed)

    def test_within_the_ceiling(self):
        ceiling = tomllib.loads((ROOT / "tools.toml").read_text())["table"]["allow"]
        report = introspect()["authority"]
        self.assertTrue(report["bounded"])
        self.assertEqual(report["foreign_symbols"], [])
        for label in report["labels"]:
            self.assertIn(label_text(label), ceiling)
            self.assertNotIn(label["name"], FORBIDDEN)

    def test_nothing_it_does_not_need(self):
        names = {l["name"] for l in introspect()["authority"]["labels"]}
        self.assertEqual(names, NEEDS)
        ceiling = set(tomllib.loads((ROOT / "tools.toml").read_text())["table"]["allow"])
        self.assertEqual({x.split("(")[0] for x in ceiling}, NEEDS, "the ceiling is wider than the tool needs")

    def test_the_compiler_pin_is_the_toml_one(self):
        pin = tomllib.loads((ROOT / "cancho.toml").read_text())["package"]["cancho"]
        self.assertEqual(introspect()["compiler"], pin)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("strace"), "needs Linux and strace")
    def test_strace_no_network_no_writes_to_disk(self):
        d = tempfile.mkdtemp(prefix="table-")
        try:
            with open(os.path.join(d, "f.csv"), "w") as f:
                f.write('a,b\n1,"x\ny"\n')
            log = os.path.join(d, "trace")
            subprocess.run(["strace", "-f", "-o", log, binary(), "--root", d, "f.csv"], capture_output=True, check=True)
            text = open(log).read()
            for call in ("socket(", "connect(", "bind(", "sendto(", "clock_gettime(CLOCK_REALTIME", "unlink", "rename", "mkdir", "ftruncate"):
                self.assertNotIn(call, text, call)
            for line in text.splitlines():
                if line.startswith(("openat", "open(")) or " openat(" in line:
                    self.assertNotRegex(line, r"O_(WRONLY|RDWR|CREAT)", line)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
