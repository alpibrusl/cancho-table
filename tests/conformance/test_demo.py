"""The demo video cannot drift from the tool (scripts/demo/).

storyboard.py is the one list of commands the video plays. This gate runs every one of them for real against the current build (build/table, or TOOLBOX_BIN), and

* **D1** fails if a command ends with another exit status than the storyboard says: a refusal that was not expected (a flag renamed, a rule changed) or one that stopped happening;
* **D2** fails if the transcript (each command, its output, its exit status) differs from scripts/demo/expected.txt, byte for byte. A change that is meant is taken in with
  `python3 scripts/demo/storyboard.py --write` and the video is recorded again (scripts/demo/README.md);
* **D3** is able to fail: a mutated expectation, a mutated storyboard exit status and a mutated command are each noticed by the same comparison.

It needs jq and shasum (the storyboard pipes into them), as the video does.
"""

import os
import pathlib
import shutil
import sys
import unittest

from harness import ROOT, binary

sys.path.insert(0, str(ROOT / "scripts" / "demo"))
import storyboard  # noqa: E402

EXPECTED = ROOT / "scripts" / "demo" / "expected.txt"


def compare(results, expected):
    """The problems of a run against an expectation, as a list of strings (empty: the demo says what the tool says)."""
    problems = []
    for cmd, want, got, _ in results:
        if want != got:
            problems.append("refused unexpectedly: %r exited %d, the storyboard says %d" % (cmd, got, want))
    if storyboard.transcript(results) != expected:
        problems.append("the transcript differs from expected.txt")
    return problems


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ("jq", "shasum", "bash", "python3"):
            assert shutil.which(tool), "the demo's commands need " + tool
        cls.results = storyboard.run_all(binary())
        cls.expected = EXPECTED.read_text()

    def test_every_command_ends_as_the_storyboard_says(self):
        bad = [(c, w, g) for c, w, g, _ in self.results if w != g]
        self.assertEqual(bad, [], "a command of the demo exited with another status")

    def test_the_transcript_is_the_golden_file(self):
        self.assertEqual(storyboard.transcript(self.results), self.expected)

    def test_nothing_wraps(self):
        # the terminal is storyboard.COLS wide: a caption, a typed line or a line of output at COLS or more would wrap (mid-word); none does
        long = []
        for scene, caps, cmds in storyboard.SCENES:
            long += ["caption: " + c for c in caps if len("# " + c) >= storyboard.COLS]
        for cmd, _, _, out in self.results:
            long += [line for i, line in enumerate(cmd.split("\n")) if len(line) + (2 if i == 0 else 0) >= storyboard.COLS]
            long += [line for line in out.split("\n") if len(line) >= storyboard.COLS]
        long += [text for text, _ in storyboard.CLOSING if len(text) >= storyboard.COLS]
        self.assertEqual(long, [])
        self.assertTrue(max(len(c["cmd"].split("\n")) for c in storyboard.commands()) <= 4)

    def test_the_storyboard_is_not_empty_and_has_the_refusal(self):
        self.assertGreaterEqual(len(self.results), 12)
        self.assertTrue(any(w == 3 for _, w, _, _ in self.results))

    def test_the_gate_can_fail(self):
        self.assertEqual(compare(self.results, self.expected), [])
        # a changed expectation
        self.assertTrue(compare(self.results, self.expected.replace("2,2560", "2,2561", 1)))
        # an exit status the storyboard no longer expects
        mutated = [(c, 0 if w == 3 else w, g, o) for c, w, g, o in self.results]
        self.assertTrue(compare(mutated, self.expected))
        # a changed output (the tool said something else)
        mutated = [(c, w, g, o.replace("0.60", "0.61")) for c, w, g, o in self.results]
        self.assertTrue(compare(mutated, self.expected))


if __name__ == "__main__":
    unittest.main()
