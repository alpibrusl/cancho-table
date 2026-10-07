"""The cell-cost rounds (docs/history.md, "cell cost"): what changed in how a cell is found, quoted, parsed and grouped.

Every test here is aimed at a seam of the fast paths: the short-field walk against `memchr` (24 bytes), the one-pass
decision to quote an output field (32 bytes), the cache of hot groups (collisions, giving up, coming back), the
add-in-place path against the one that moves the groups (a new group, a quoted key with a quote in it), the
integers read without overflow checks (18 digits), and the next line taken in place (a line at the cap, a line that
ends a chunk). The oracle is Python: the csv module's rows, a dict of counts, `int`. Each plan runs sequentially and
with threads and tiny ranges, and the answers must be equal as well as right.
"""

import csv
import io
import random
import unittest

from harness import Scratch, binary, run_argv

csv.field_size_limit(1 << 30)

VARIANTS = ((), ("--threads", "4", "--chunk-bytes", "300", "--parallel-min-bytes", "0"),
            ("--threads", "16", "--chunk-bytes", "64", "--parallel-min-bytes", "0"),
            ("--threads", "64", "--chunk-bytes", "7", "--parallel-min-bytes", "0"),
            ("--threads", "3", "--chunk-bytes", "4096", "--parallel-min-bytes", "0"))


def encode(rows, delimiter=","):
    out = io.StringIO(newline="")
    csv.writer(out, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return out.getvalue().encode("latin-1")


class CellCost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def table(self, data, args, variant=()):
        self.s.write("t.csv", data)
        return run_argv([binary(), "--root", str(self.s.dir), *args, "--format", "csv", *variant, "t.csv"])

    def every_way(self, data, args):
        """The answer of the sequential read, which must equal the answer of every other way."""
        want = self.table(data, args)
        for v in VARIANTS[1:]:
            got = self.table(data, args, v)
            self.assertEqual((got.status, got.stdout, got.stderr), (want.status, want.stdout, want.stderr), (args, v, data[:200]))
        return want

    def test_fields_around_the_walk_and_the_search(self):
        rng = random.Random(24)
        for length in (0, 1, 2, 22, 23, 24, 25, 26, 47, 48, 49, 50, 100, 300):
            for style in ("plain", "quoted", "delim_in_quotes", "doubled", "cr", "lf", "end_special"):
                rows = [["id", "v", "w"]]
                for i in range(6):
                    body = "".join(rng.choice("abcdefgh ") for _ in range(length))
                    if style == "delim_in_quotes" and length > 2:
                        k = rng.randrange(length)
                        body = body[:k] + "," + body[k + 1:]
                    elif style == "doubled" and length > 2:
                        k = rng.randrange(length)
                        body = body[:k] + '"' + body[k + 1:]
                    elif style == "cr" and length > 1:
                        k = rng.randrange(length)
                        body = body[:k] + "\r" + body[k + 1:]
                    elif style == "lf" and length > 1:
                        k = rng.randrange(length)
                        body = body[:k] + "\n" + body[k + 1:]
                    elif style == "end_special" and length > 1:
                        body = body[:-1] + rng.choice(',"\r')
                    rows.append([str(i), body, "z" + body[:3]])
                data = encode(rows)
                want = list(csv.reader(io.StringIO(data.decode("latin-1"), newline=""), strict=True))
                got = self.every_way(data, ["--select", "w,v,id"])
                self.assertEqual(got.status, 0, (length, style, got.stderr))
                back = list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""), strict=True))
                self.assertEqual(back, [[r[2], r[1], r[0]] for r in want], (length, style))
                self.assertEqual(got.stdout, encode([[r[2], r[1], r[0]] for r in want]), (length, style))

    def test_a_quote_in_the_middle_of_a_field_is_text_at_every_distance(self):
        for before in (0, 1, 22, 23, 24, 25, 40):
            row = "x" * before + 'q"r' if before else 'q"r'
            data = ("a,b\n" + row + ",2\n").encode()
            got = self.every_way(data, ["--select", "a,b"])
            self.assertEqual(got.status, 0, (before, got.stderr))
            self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode()), strict=True))[1], [row, "2"], before)

    def group_oracle(self, rows, keys, agg=None):
        """Counts (and sums, minima, maxima) by key, as table prints them: keys sorted bytewise, field by field."""
        counts, vals = {}, {}
        for r in rows:
            k = tuple(r[i] for i in keys)
            counts[k] = counts.get(k, 0) + 1
            if agg:
                vals.setdefault(k, []).append(int(r[agg[1]]))
        out = []
        for k in sorted(counts, key=lambda k: tuple(x.encode("latin-1") for x in k)):
            row = list(k) + [str(counts[k])]
            if agg:
                f = {"sum": sum, "min": min, "max": max}[agg[0]]
                row.append(str(f(vals[k])))
            out.append(row)
        return out

    def test_the_cache_of_groups_collides_gives_up_and_comes_back(self):
        rng = random.Random(5)
        # keys with one length, one first and one last byte: the cache's look cannot tell them apart
        near = ["a%db" % i for i in range(10)] + ["a%d%db" % (i, j) for i in range(4) for j in range(4)]
        pools = {
            "near": near,
            "few": ["x", "y", "z"],
            "many": ["k%d" % i for i in range(3000)],
            "then_few": None,
        }
        for name, pool in pools.items():
            rows = [["k", "n", "g"]]
            for i in range(9000):
                if name == "then_few":
                    key = "k%d" % i if i < 5000 else "k%d" % (i % 4)   # new keys for a long time, then the same few
                elif name == "near":
                    key = pool[(i // 7) % len(pool)] if i % 3 else rng.choice(pool)  # runs, and random
                else:
                    key = rng.choice(pool)
                rows.append([key, str(rng.randrange(-50, 1000)), "g%d" % rng.randrange(3)])
            data = encode(rows)
            body = rows[1:]
            got = self.every_way(data, ["--group", "k"])
            self.assertEqual(got.status, 0, (name, got.stderr))
            self.assertEqual(got.stdout, encode([["k", "count"]] + self.group_oracle(body, [0])), name)
            got = self.every_way(data, ["--group", "k,g", "--agg", "count,sum:n"])
            self.assertEqual(got.stdout, encode([["k", "g", "count", "sum:n"]] + self.group_oracle(body, [0, 2], ("sum", 1))), name)
            for f in ("min", "max"):
                got = self.every_way(data, ["--group", "g,k", "--agg", "count,%s:n" % f])
                self.assertEqual(got.stdout, encode([["g", "k", "count", "%s:n" % f]] + self.group_oracle(body, [2, 0], (f, 1))), (name, f))

    def test_long_keys_and_distinct_text_and_the_second_aggregate_refusing(self):
        # a key of more than 255 bytes (the second byte of its length), several of them, in two columns
        rows = [["a", "b", "n", "t"]]
        for i in range(400):
            rows.append(["k" * (250 + i % 9) + str(i % 5), "b" * 300, str(i), "t%d" % (i % 7)])
        data = encode(rows)
        got = self.every_way(data, ["--group", "a,b", "--agg", "count,sum:n"])
        self.assertEqual(got.stdout, encode([["a", "b", "count", "sum:n"]] + self.group_oracle(rows[1:], [0, 1], ("sum", 2))))
        # distinct over text is not an integer read
        got = self.every_way(data, ["--group", "a", "--agg", "distinct:t", "--max-distinct", "1000"])
        self.assertEqual(got.status, 0, got.stderr)
        want = {}
        for r in rows[1:]:
            want.setdefault(r[0], set()).add(r[3])
        back = list(csv.reader(io.StringIO(got.stdout.decode()), strict=True))[1:]
        self.assertEqual({r[0]: int(r[1]) for r in back}, {k: len(v) for k, v in want.items()})
        # the second aggregate refuses: which one is said
        bad = [["g", "m", "n"]] + [["a", str(i), str(i)] for i in range(50)]
        bad[30] = ["a", "5", "oops"]
        got = self.every_way(encode(bad), ["--group", "g", "--agg", "sum:m,sum:n"])
        self.assertEqual(got.status, 8)
        for v in ((), ("--threads", "4", "--chunk-bytes", "100", "--parallel-min-bytes", "0")):
            doc = self.s.table("t.csv", encode(bad), "--group", "g", "--agg", "sum:m,sum:n", *v).error()
            self.assertEqual((doc["rule"], doc["detail"]["column"], doc["detail"]["row"], doc["detail"]["value"]), ("value.not-integer", "n", 30, "oops"))

    def test_keys_the_cache_cannot_tell_apart_by_their_length(self):
        # `look` = (length * 31 + first) * 31 + last: "zmb" (3) and "[mmb" (4) have the same one, so they
        # share a place in the cache; the key of one must not be taken for the other, or read past its end
        rows = [["k", "n"]]
        for i in range(200):
            rows.append([("zmb", "[mmb")[i % 2 if i > 20 else 0], str(i)])
        for i in range(60):
            rows.append([("[mmb", "zmb")[i % 2], str(i)])
        got = self.every_way(encode(rows), ["--group", "k", "--agg", "count,sum:n"])
        want = {}
        for k, n in rows[1:]:
            c, t = want.get(k, (0, 0))
            want[k] = (c + 1, t + int(n))
        back = list(csv.reader(io.StringIO(got.stdout.decode()), strict=True))[1:]
        self.assertEqual({r[0]: (int(r[1]), int(r[2])) for r in back}, want)

    def test_a_sum_past_64_bits_is_exact_on_every_way_of_adding(self):
        # in place, by value (a quoted key, a distinct beside it), and by threads
        big = "9223372036854775807"
        for key, aggs in (('"a""b"', "sum:n"), ("a", "sum:n,distinct:n")):
            data = ("g,n\n%s,%s\n%s,1\n" % (key, big, key)).encode()
            for v in ((), ("--threads", "4", "--chunk-bytes", "10", "--parallel-min-bytes", "0")):
                got = self.s.table("t.csv", data, "--group", "g", "--agg", aggs, "--format", "csv", *v)
                self.assertEqual(got.status, 0, (key, aggs, v, got))
                self.assertEqual(list(csv.reader(io.StringIO(got.stdout.decode())))[1][1], "9223372036854775808", (key, aggs, v))

    def test_the_byte_budget_ends_a_page_of_groups(self):
        rows = [["k", "n"]] + [["key%05d" % i, str(i)] for i in range(300)]
        data = encode(rows)
        got = self.s.table("t.csv", data, "--group", "k", "--max-bytes", "400")
        d = got.data()
        self.assertTrue(d["truncated"])
        self.assertLess(d["row_count"], 300)
        self.assertEqual(d["next"], {"from": d["row_count"]})
        self.assertLessEqual(len(__import__("json").dumps(d["rows"], separators=(",", ":"))), 400)
        seen, at = [], 0
        while True:
            got = self.s.table("t.csv", data, "--group", "k", "--max-bytes", "400", "--from", at)
            seen += got.data()["rows"]
            if got.data()["next"] is None:
                break
            at = got.data()["next"]["from"]
        self.assertEqual(seen, [["key%05d" % i, "1"] for i in range(300)])
        got = self.s.table("t.csv", data, "--group", "k", "--max-bytes", "5")
        self.assertEqual((got.status, got.first_rule()), (8, "limit.output-too-large"))

    def test_a_quoted_key_with_a_quote_is_not_the_text_it_is_written_as(self):
        # `a""b` unquoted is the text a""b; `"a""b"` is the text a"b: two groups, whatever the cache holds
        lines = ["k,n"]
        for i in range(120):
            lines.append('a""b,1' if i % 2 else '"a""b",10')
        data = ("\n".join(lines) + "\n").encode()
        got = self.every_way(data, ["--group", "k", "--agg", "count,sum:n"])
        back = list(csv.reader(io.StringIO(got.stdout.decode()), strict=True))[1:]
        self.assertEqual(sorted(back), sorted([['a""b', "60", "60"], ['a"b', "60", "600"]]))

    def test_a_line_longer_than_the_cap_that_fills_more_than_a_chunk(self):
        # the first chunk holds only the start of it: the rest, and the end of it, are not a line
        for length in (70000, 65626, 131162, 150000, 400000):   # 65626 and 131162 end 100 bytes into the next chunk
            data = ("a,b\n1,2\n3," + "x" * length + "\n5,6\n7,8\n").encode()
            want = self.table(data, ["--group", "a", "--max-line-bytes", "1000"])
            self.assertEqual(want.status, 8, length)
            for v in VARIANTS[1:]:
                got = self.table(data, ["--group", "a", "--max-line-bytes", "1000"], v)
                self.assertEqual((got.status, got.stdout, got.stderr), (want.status, want.stdout, want.stderr), (length, v))
            doc = self.s.table("t.csv", data, "--group", "a", "--max-line-bytes", "1000").error()
            self.assertEqual((doc["rule"], doc["detail"]["first_line"], doc["detail"]["lines"]), ("limit.line-too-long", 3, 1), length)
            self.assertEqual(doc["detail"]["longest"], length + 2, length)

    def test_a_quoted_key_with_a_quote_and_a_new_group_take_the_other_way(self):
        rows = [["k", "n"]]
        rng = random.Random(8)
        for i in range(600):
            rows.append([rng.choice(['p"q', "plain", 'a,b', "", 'x\ny', "plain2"]), str(i)])
        data = encode(rows)
        got = self.every_way(data, ["--group", "k", "--agg", "count,sum:n"])
        # the same counts as the oracle, key by key
        body = {}
        for k, n in rows[1:]:
            c, s = body.get(k, (0, 0))
            body[k] = (c + 1, s + int(n))
        back = list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline=""), strict=True))[1:]
        self.assertEqual({r[0]: (int(r[1]), int(r[2])) for r in back}, body)

    def test_integers_up_to_18_digits_are_read_without_the_check_and_19_with_it(self):
        cases = ["0", "-0", "+0", "7", "-7", "+7", "000000000000000007", "999999999999999999", "-999999999999999999",
                 "1000000000000000000", "9223372036854775807", "-9223372036854775808", "9223372036854775808",
                 "-9223372036854775809", "00000000000000000000000001", "-00000000000000000000000001", "1" + "0" * 30]
        for cell in cases:
            for function in ("sum", "min", "max"):
                data = ("g,n\na,1\na,%s\n" % cell).encode()   # the group exists when the cell comes
                got = self.every_way(data, ["--group", "g", "--agg", "%s:n" % function])  # one aggregate: no count column
                v = int(cell)
                if -(1 << 63) <= v < (1 << 63):
                    f = {"sum": sum, "min": min, "max": max}[function]
                    total = f([v, 1])
                    self.assertEqual(got.stdout, encode([["g", "%s:n" % function], ["a", str(total)]]), (cell, function))   # a sum past 64 bits is printed in full
                else:
                    self.assertEqual(got.status, 8, (cell, function, got.stdout))
                    rule = self.s.table("t.csv", data, "--group", "g", "--agg", "%s:n" % function).first_rule()
                    self.assertEqual(rule, "value.integer-overflow", (cell, function))
        for bad in ("", "-", "+", " 1", "1 ", "1.0", "1e3", "0x10", "1_0", "١"):
            got = self.every_way(("g,n\na,%s\n" % bad).encode(), ["--group", "g", "--agg", "sum:n"])
            self.assertEqual(got.status, 8, bad)

    def test_a_refusal_in_the_middle_of_a_run_of_hot_groups_says_the_same(self):
        rows = [["g", "n"]] + [["a", str(i)] for i in range(300)]
        rows[177] = ["a", "oops"]
        data = encode(rows)
        got = self.every_way(data, ["--group", "g", "--agg", "sum:n"])
        self.assertEqual(got.status, 8)
        rows[177] = ["a", "9223372036854775807"]
        got = self.every_way(encode(rows), ["--group", "g", "--agg", "sum:n"])
        self.assertEqual(got.status, 0)
        self.assertEqual(got.stdout, encode([["g", "sum:n"], ["a", str(sum(int(r[1]) for r in rows[1:]))]]))

    def test_the_slow_way_of_adding_refuses_as_the_fast_way_does(self):
        # a quoted key with a quote in it is added by value (agg.add), and a `distinct` beside the sum forces it too
        for key in ('"a""b"', "a"):
            for agg, rows, rule in (("sum:n", ["1", "9223372036854775808"], "value.integer-overflow"), ("min:n", ["x"], "value.not-integer"), ("max:n:int", ["1", ""], "value.not-integer"),
                                    ("sum:n,distinct:n", ["1", "-9223372036854775809"], "value.integer-overflow"), ("sum:p:dec(1)", ["1.55"], "value.decimal-scale"),
                                    ("mean:p:dec(1)", ["1e2"], "value.not-decimal"), ("distinct:p:dec(0)", ["1000000000000000000"], "value.decimal-too-wide")):
                with self.subTest(key=key, agg=agg):
                    col = "p" if ":dec" in agg else "n"
                    data = "g,n,p\n" + "".join("%s,%s,%s\n" % (key, c if col == "n" else "0", c if col == "p" else "0") for c in rows)
                    got = self.s.table("t.csv", data, "--group", "g", "--agg", agg)
                    self.assertEqual((got.status, got.first_rule()), (8, rule), got)
                    self.assertEqual(got.error()["detail"]["row"], len(rows), got)

    def test_lines_at_the_cap_and_at_the_end_of_a_chunk(self):
        # a line that is exactly the end of a 64 KiB chunk, one byte short of it, one past it
        for pad in (65536 - 20, 65536 - 19, 65536 - 18, 65536 - 17, 2 * 65536 - 25, 2 * 65536 - 24, 2 * 65536 - 23):
            head = "id,t\n"
            first = "1," + "x" * (pad - len(head) - 3) + "\n"
            data = (head + first + "2,yy\n3,zz\n").encode()
            got = self.every_way(data, ["--select", "id,t"])
            self.assertEqual(got.status, 0, (pad, got.stderr))
            rows = list(csv.reader(io.StringIO(got.stdout.decode()), strict=True))
            self.assertEqual(rows[2:], [["2", "yy"], ["3", "zz"]], pad)
            self.assertEqual(len(rows[1][1]), pad - len(head) - 3, pad)
        # no newline at the end, a CRLF file, a blank line, a last line with a CR only
        for data in (b"a,b\n1,2\n3,4", b"a,b\r\n1,2\r\n3,4\r\n", b"a,b\n\n1,2\n\n3,4\n", b"a,b\n1,2\n3,4\r"):
            got = self.every_way(data, ["--group", "a"])
            self.assertEqual(got.status, 0, data)
            self.assertEqual(got.stdout.count(b"\n"), 3, data)


if __name__ == "__main__":
    unittest.main()
