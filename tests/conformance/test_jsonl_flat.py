"""R2b (docs/readers.md section 12.9) -- the flat-object scanner is an accelerator and never a judge: for a line it answers for, the cells are the ones the tape gives; for any other
line it says nothing and std.json decides, with the same refusal as without it.

The scanner has no switch to turn it off, so the same lines are run twice: as they are, and each with `,"zz":[]` before its closing brace, a nested value that no flat scanner
answers for (every line of the second file goes to the tape, whatever the first does). A line that does not end in a closing brace is left as it is in both. The two answers
must be the same bytes when the file is accepted, and the same refusal (rule, line, row, column, reason, kind, key) when it is not; the offset of a syntax error is the parser's and the
second file's lines are longer, so it is not compared."""

import json
import random
import unittest

from harness import Scratch, run, validate

KEYS = ["a", "b", "c", "id", "status", "bytes", "x y", "k1", "ab", "A"]
GOOD_NUMBERS = ["0", "1", "-1", "42", "-0", "1.50", "-0.5", "1e3", "1E3", "1e-7", "2.5E+10", "123456789012345678901234567890", "9223372036854775807", "-9223372036854775808",
                "0.1234567890123456789", "0.0", "10", "100"]
BAD_NUMBERS = ["007", "1.", ".5", "+1", "1e", "-", "01", "1.e3", "1ee3", "--1", "0x10", "1_000", "NaN", "Infinity", "1 2"]
STRINGS = ["", "a", "hello world", "x,y", "it's", "q\\\"z", "l1\\nl2", "\\u00e9", "é", "\\\\", "/p/12", "a b", "\t", "#h", "and", "ÿ", "end\\"]
GOOD_LITERALS = ["true", "false", "null"]
BAD_LITERALS = ["True", "nul", "nullx", "tru"]
BLANKS = ["", "", "", " ", "  ", "\t", " \t "]


def value(rng, dirty, scalar=False):
    r = rng.random()
    if scalar and r >= 0.93:
        r = 0.5
    if r < 0.40:
        return '"' + rng.choice(STRINGS if dirty else [x for x in STRINGS if x != "end\\"]) + '"'
    if r < 0.75:
        return rng.choice(GOOD_NUMBERS + (BAD_NUMBERS if dirty else []))
    if r < 0.93:
        return rng.choice(GOOD_LITERALS + (BAD_LITERALS if dirty else []))
    if not dirty:
        return rng.choice(["[]", "[1]", "{}", '{"n":1}'])
    return rng.choice(["[]", "[1]", "{}", '{"n":1}', '"unterminated', "[", "{", "}", ",", '{"a":'])


def line(rng, declared, dirty):
    pairs = []
    if dirty:
        for _ in range(rng.randint(0, 6)):
            pairs.append(rng.choice(KEYS if rng.random() < 0.8 else declared))
    else:
        pairs = rng.sample(KEYS, rng.randint(0, 6))
    # a key twice now and then; a declared key missing now and then
    body = []
    for k in pairs:
        key = k if rng.random() < 0.97 else k.replace("a", "\\u0061")
        body.append('%s"%s"%s:%s%s' % (rng.choice(BLANKS), key, rng.choice(BLANKS), rng.choice(BLANKS), value(rng, dirty, scalar=not dirty and k in declared)))
    sep = rng.choice([",", ",", ",", ", "])
    text = "{" + rng.choice(BLANKS) + sep.join(body) + rng.choice(BLANKS) + "}"
    r = rng.random() if dirty else 1.0
    if r < 0.02:
        text += ","
    elif r < 0.04:
        text = text[:-1]
    elif r < 0.06:
        text += " x"
    elif r < 0.08:
        text = text.replace(":", "=", 1)
    elif r < 0.10:
        text = "[" + text + "]"
    if rng.random() < 0.05:
        text += "\r"
    return text


def nested(text):
    """The line with a nested value in it, which the scanner does not answer for: before the closing brace, after a comma unless the object is empty."""
    cr = text.endswith("}\r")
    body = text[:-2] if cr else text[:-1] if text.endswith("}") else None
    if body is None:
        return text
    comma = "" if body.strip(" \t{") == "" and body.startswith("{") else ","
    return body + comma + '"zz":[]}' + ("\r" if cr else "")


FIELDS = [["a"], ["a", "b"], ["id", "status", "bytes"], ["c", "a", "x y"], ["status"], ["ab", "A", "k1", "a", "b"]]


def facts(r):
    """The comparable part of an answer."""
    if r.stdout.startswith(b"{"):
        doc = r.doc()
        if doc["ok"]:
            return ("ok", r.stdout)
        e = doc["error"]
        d = e["detail"]
        keep = {k: d.get(k) for k in ("line", "row", "column", "reason", "kind", "key", "context", "value")}
        return ("refused", e["rule"], keep, r.status)
    return ("raw", r.stdout, r.status)


class Flat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()
        (cls.s.dir / "plain").mkdir()
        (cls.s.dir / "tape").mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def both(self, lines, fields, *flags):
        plain = ("\n".join(lines) + "\n").encode("utf-8")
        tape = ("\n".join(nested(l) for l in lines) + "\n").encode("utf-8")
        (self.s.dir / "plain" / "f.jsonl").write_bytes(plain)
        (self.s.dir / "tape" / "f.jsonl").write_bytes(tape)
        common = ["--input-format", "jsonl", "--fields", ",".join(f.replace("\\", "\\\\") for f in fields), *flags, "f.jsonl"]
        a = run("--root", self.s.dir / "plain", *common)
        b = run("--root", self.s.dir / "tape", *common)
        return a, b

    def test_the_scanner_and_the_tape_agree(self):
        rng = random.Random(20261013)
        answered = refused = 0
        for case in range(600):
            fields = rng.choice(FIELDS)
            dirty = case % 4 == 3
            lines = [line(rng, fields, dirty) for _ in range(rng.randint(1, 12))]
            flags = rng.choice([["--absent", "empty"], ["--absent", "empty"], ["--absent", "empty", "--select", fields[0]], [], ["--select", fields[0]], ["--format", "csv", "--select", ",".join(fields)], ["--absent", "empty", "--select", ",".join(fields)],
                                ["--absent", "empty", "--format", "csv", "--where", "%s != x" % fields[0], "--select", ",".join(fields)], ["--absent", "empty", "--group", fields[0]]])
            a, b = self.both(lines, fields, *flags)
            with self.subTest(case=case, lines=lines, fields=fields, flags=flags):
                fa, fb = facts(a), facts(b)
                if fa[0] == "refused" and fa[1] == "parse.jsonl-syntax":
                    # the offset is the parser's and the lines of the second file are longer: the rule, the line and the row are compared
                    self.assertEqual(fb[:2], fa[:2])
                    self.assertEqual({k: fa[2][k] for k in ("line", "row")}, {k: fb[2][k] for k in ("line", "row")})
                    refused += 1
                    continue
                if fa[0] == "refused" and fa[1] == "parse.jsonl-not-object":
                    self.assertEqual(fb[:2], fa[:2])
                    refused += 1
                    continue
                self.assertEqual(fa, fb)
                if fa[0] == "ok":
                    answered += 1
                else:
                    refused += 1
        self.assertGreater(answered, 80)
        self.assertGreater(refused, 80)

    def test_a_flat_file_goes_through_the_scanner_and_a_nested_one_through_the_tape_and_both_agree_on_big_numbers_and_literals(self):
        lines = ['{"a":123456789012345678901234567890,"b":-0,"c":true,"d":false,"e":null,"f":"x,y"}'] * 3
        a, b = self.both(lines, ["a", "b", "c", "d", "e", "f"], "--absent", "empty", "--format", "csv", "--select", "a,b,c,d,e,f")
        self.assertEqual(a.stdout, b"a,b,c,d,e,f\n" + b"123456789012345678901234567890,-0,true,false,,\"x,y\"\n" * 3)
        self.assertEqual(a.stdout, b.stdout)

    def test_the_node_limit_is_the_tapes_whether_the_scanner_would_have_answered_or_not(self):
        # 3 nodes for one pair: with --max-json-nodes 2 the scanner declines and the tape refuses; with 3 both read it
        for nodes, rule in (("2", "limit.json-too-many-nodes"), ("3", None)):
            r = run("--input-format", "jsonl", "--fields", "a", "--max-json-nodes", nodes, "-", stdin=b'{"a":1}\n')
            self.assertEqual(r.first_rule(), rule, r)
        for nodes, rule in (("4", "limit.json-too-many-nodes"), ("5", None)):
            r = run("--input-format", "jsonl", "--fields", "a", "--max-json-nodes", nodes, "-", stdin=b'{"a":1,"b":2}\n')
            self.assertEqual(r.first_rule(), rule, (nodes, r))


if __name__ == "__main__":
    unittest.main()
