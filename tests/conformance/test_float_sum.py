"""The exact `sum` and `mean` of a `:float` column (docs/numbers.md, stage N4).

The oracle is `numbers_ref.fsum_ref` and `fmean_ref`: Python `Fraction`s summed exactly and rounded ONCE to the nearest double (`float(Fraction)` is a correctly
rounded true division), with no negative zero; `math.fsum` is checked against it where it does not raise. The tool must give that answer for every order of the
rows, for every thread count (the answer of one thread, byte for byte), and must refuse, naming the group, when the exact sum is beyond the largest double.
Plain `+` of doubles gets many of these wrong, and the tests below say which (so that a plain-double implementation cannot pass them).
"""

import csv
import io
import math
import random
import struct
import unittest
from fractions import Fraction

import numbers_ref as ref
import test_float as tf
import test_parallel as par
from harness import Scratch, TRAPS, validate
from test_numbers import enc


def bits_double(rng):
    """A random finite double of a random exponent, from random bits (any exponent, subnormals included)."""
    while True:
        x = struct.unpack(">d", struct.pack(">Q", rng.getrandbits(64)))[0]
        if math.isfinite(x):
            return x


def cell(x):
    return repr(x)


def plain_sum(xs):
    s = 0.0
    for x in xs:
        s += x
    return s


def want_pair(cells):
    xs = [ref.flt(c)[1] for c in cells]
    return ref.fsum_ref(xs), ref.fmean_ref(xs)


def text_of(r):
    return ref.float_text(r[1])


def spike_cases(r, big):
    """The generator of scripts/spikes/superacc_check.py, as it was (seed 1 gives its 502 cases)."""
    dbl_max = 1.7976931348623157e308

    def rand_double():
        while True:
            x = struct.unpack("<d", struct.pack("<Q", r.getrandbits(64)))[0]
            if math.isfinite(x):
                return x

    def frombits(b):
        return struct.unpack("<d", struct.pack("<Q", b & (2 ** 64 - 1)))[0]

    c = []
    c += [[0.0], [-0.0], [1.0, -1.0], [0.1] * 10, [1e16, 1.0, -1e16, 1.0], [1.0, 2 ** -53], [1.0, 2 ** -53, 2 ** -200],
          [1.0, 2 ** -53, -2 ** -200], [1.0 + 2 ** -52, 2 ** -53], [1.0 + 2 ** -52, 2 ** -53, 2 ** -200],
          [dbl_max, dbl_max, -dbl_max], [dbl_max, -dbl_max], [dbl_max], [-dbl_max, -dbl_max],
          [5e-324], [5e-324] * 3, [-5e-324, 5e-324 * 2], [2.2250738585072014e-308, -5e-324], [2.2250738585072009e-308] * 7,
          [2 ** -1074 * 3, 2 ** -1074 * 5], [1e308, 1e308, -1e308], [1e308, 1e-308, -1e308], [2 ** 1023, 2 ** 1023, -2 ** 1023, 2 ** -1074],
          [0.1, 0.2, 0.3], [1e100, 1.0, -1e100], [3.0] * 1000, [float(i) for i in range(1, 2001)]]
    c.append([2.0 ** 53, 1.0, 1.0])
    c.append([2.0 ** 53, 1.0])
    c.append([2.0 ** 53, 3.0])
    c.append([2.0 ** 53 + 2, 1.0])
    for n in (1, 2, 3, 5, 10, 100, 1000):
        for _ in range(30):
            c.append([rand_double() for _ in range(n)])
    for _ in range(60):
        xs = [rand_double() * 2.0 ** -r.randint(0, 80) for _ in range(r.randint(2, 200))]
        ys = xs + [-x for x in xs] + [rand_double() * 2.0 ** -1000 if r.random() < .5 else 0.0]
        r.shuffle(ys)
        c.append(ys)
    for _ in range(60):
        c.append([frombits(r.getrandbits(52) | (r.getrandbits(1) << 63)) for _ in range(r.randint(1, 300))])
    for _ in range(60):
        c.append([r.choice([1, -1]) * r.choice([2.0 ** r.randint(-1074, 1023), 1e308 * r.random(), 1e-300 * r.random()]) for _ in range(r.randint(2, 400))])
    for _ in range(40):
        c.append([round(r.uniform(-1000, 1000), 2) for _ in range(r.randint(1, 5000))])
    for _ in range(40):
        b = rand_double()
        u = math.ulp(b) if abs(b) < dbl_max / 2 else 1.0
        c.append([b, u / 2, r.choice([-1, 1]) * 2.0 ** -1074 * r.randint(0, 3)])
    if big:
        c.append([rand_double() * 2.0 ** -r.randint(0, 900) for _ in range(big)])
    return c


class Sums(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def groups(self, columns, extra=(), spec="sum:x:float,mean:x:float", shuffle=None):
        """Every column is a group (key = its number); returns the rows [key, sum, mean] in the order of the keys."""
        rows = [[str(i), c] for i, col in enumerate(columns) for c in col]
        if shuffle is not None:
            random.Random(shuffle).shuffle(rows)
        data = enc([["k", "x"]] + rows)
        got = self.s.table("t.csv", data, "--group", "k", "--agg", spec, "--format", "csv", "--max-groups", "1000000", "--max-state-bytes", "1000000000", *extra)
        self.assertEqual(got.status, 0, got)
        return sorted(list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))[1:], key=lambda r: int(r[0]))

    def check_columns(self, columns, shuffle=None):
        rows = self.groups(columns, shuffle=shuffle)
        self.assertEqual(len(rows), len(columns))
        for col, (k, s, m) in zip(columns, rows):
            ws, wm = want_pair(col)
            self.assertEqual(ws[0], "ok", col[:6])
            self.assertTrue(ref.same_text(s, ws[1]), ("sum", col[:8], len(col), s, text_of(ws)))
            self.assertTrue(ref.same_text(m, wm[1]), ("mean", col[:8], len(col), m, text_of(wm)))

    # ---- the cases that plain addition gets wrong -------------------------------------------------------------------------

    def test_cases_a_plain_double_sum_gets_wrong(self):
        two53 = repr(2.0 ** -53)
        cases = [
            (["1e16", "1", "-1e16"], "1.0", "0.3333333333333333"),                       # plain: 0.0 (1e16 + 1 is 1e16)
            (["0.1"] * 10, "1.0", "0.1"),                                                # plain: 0.9999999999999999
            (["1e308", "1e308", "-1e308"], "1e308", None),                               # plain: inf on the way
            (["1", two53], "1.0", None),                                                 # an exact tie between 1 and the next double: to even, 1
            (["1", two53, "5e-324"], "1.0000000000000002", None),                        # just above the tie: up
            (["1", "-" + two53, "-5e-324"], "0.9999999999999999", None),
            (["1.0000000000000002", two53], "1.0000000000000004", None),                 # a tie between odd and even: to the even one, up
            (["5e-324"] * 3, "1.5e-323", "5e-324"),
            (["1", "-1"], "0.0", "0.0"),
            (["-1e16", "-1", "1e16"], "-1.0", "-0.3333333333333333"),
            (["1.7976931348623157e308", "-1.7976931348623157e308", "5e-324"], "5e-324", "1.7e-324"[:0] + "0.0"),   # the mean of one unit over three is below half a unit: 0
            (["5e-324", "5e-324", "-5e-324", "-5e-324", "5e-324"], "5e-324", "0.0"),    # the mean of one unit over five is below half a unit: 0
            (["-5e-324", "0", "0"], "-5e-324", "0.0"),                                  # a negative mean that rounds to zero is 0.0, there is no -0
            (["2.2250738585072014e-308", "-2.225073858507201e-308"], None, None),
        ]
        for col, want_sum, want_mean in cases:
            with self.subTest(col=col[:4]):
                ws, wm = want_pair(col)
                rows = self.groups([col])
                self.assertTrue(ref.same_text(rows[0][1], ws[1]) and ref.same_text(rows[0][2], wm[1]), (rows, ws, wm))
                if want_sum is not None:
                    self.assertEqual(rows[0][1], want_sum, (col, rows))
                if want_mean is not None:
                    self.assertEqual(rows[0][2], want_mean, (col, rows))

    def test_a_plain_double_sum_would_fail_these(self):
        """The columns above are discriminating: plain addition of the doubles gives another answer for most (a test that cannot fail is no test)."""
        wrong = 0
        for col in (["1e16", "1", "-1e16"], ["0.1"] * 10, ["1e308", "1e308", "-1e308"], ["-1e16", "-1", "1e16"]):
            xs = [ref.flt(c)[1] for c in col]
            if plain_sum(xs) != ref.fsum_ref(xs)[1]:
                wrong += 1
        self.assertEqual(wrong, 4)

    # ---- generated columns against the oracle ---------------------------------------------------------------------------

    def test_cancellation_columns_against_python_fractions(self):
        rng = random.Random(4401)
        columns = []
        for _ in range(60):
            n = rng.randint(2, 120)
            xs = [bits_double(rng) for _ in range(n)]
            xs += [-x for x in xs]                                                      # the large values cancel exactly...
            xs += [rng.uniform(-1, 1) * 10.0 ** rng.randint(-30, 30) for _ in range(rng.randint(0, 6))]       # ...and what is left is small
            rng.shuffle(xs)
            columns.append([cell(x) for x in xs])
        self.check_columns(columns, shuffle=1)
        wrong = sum(1 for col in columns if plain_sum([ref.flt(c)[1] for c in col]) != ref.fsum_ref([ref.flt(c)[1] for c in col])[1])
        self.assertGreater(wrong, 40)                                                  # plain addition gets nearly all of them wrong

    def test_the_oracle_agrees_with_math_fsum(self):
        rng = random.Random(4402)
        for _ in range(300):
            xs = [bits_double(rng) * rng.choice([1, 1e-100, 1e-300]) for _ in range(rng.randint(1, 40))]
            try:
                f = math.fsum(xs)
            except OverflowError:
                continue
            self.assertEqual(ref.fsum_ref(xs)[1], 0.0 if f == 0 else f)

    def test_subnormals_and_extremes(self):
        rng = random.Random(4403)
        columns = []
        for _ in range(60):
            kind = rng.randrange(4)
            n = rng.randint(1, 200)
            if kind == 0:
                xs = [struct.unpack(">d", struct.pack(">Q", rng.getrandbits(52)))[0] * rng.choice([1, -1]) for _ in range(n)]       # subnormals
            elif kind == 1:
                xs = [rng.choice([1.7976931348623157e308, -1.7976931348623157e308, 5e-324, -5e-324, 2.2250738585072014e-308, 1.0, -1.0]) for _ in range(n)]
                # keep the exact sum finite: pair every large value with its negation
                big = [x for x in xs if abs(x) > 1e300]
                xs = [x for x in xs if abs(x) <= 1e300] + big[: len(big) // 2] + [-x for x in big[: len(big) // 2]]
            elif kind == 2:
                xs = [rng.uniform(-1, 1) * 2.0 ** rng.randint(-1074, 1000) for _ in range(n)]
            else:
                xs = [rng.choice([1, -1]) * rng.getrandbits(53) * 2.0 ** rng.randint(-1074, 970) for _ in range(n)]
            xs = [x for x in xs if x == x and not math.isinf(x)] or [1.0]
            columns.append([cell(x) for x in xs])
        self.check_columns(columns, shuffle=2)

    def test_exact_ties_round_to_even_and_a_hair_above_or_below_does_not(self):
        rng = random.Random(4404)
        columns = []
        for _ in range(120):
            e = rng.randint(-1000, 900)
            m = rng.getrandbits(53) | (1 << 52)
            x = float(Fraction(m) * Fraction(2) ** (e - 52))
            half = 2.0 ** (e - 53)                       # half a unit in the last place of x
            sign = rng.choice([1, -1])
            col = [x * sign, half * sign]
            kind = rng.randrange(4)
            if kind == 1:
                col.append(sign * 2.0 ** (e - 53 - 60) if e - 113 > -1074 else sign * 5e-324)       # above the tie
            elif kind == 2:
                col.append(-sign * 2.0 ** (e - 53 - 60) if e - 113 > -1074 else -sign * 5e-324)     # below the tie
            elif kind == 3:
                col += [sign * half, -sign * half]                                                  # cancels back to the tie
            rng.shuffle(col)
            columns.append([cell(c) for c in col])
        self.check_columns(columns)

    def test_the_mean_is_one_rounding_not_two(self):
        rng = random.Random(4405)
        columns = []
        for _ in range(60):
            n = rng.choice([3, 7, 10, 49, 1000])
            columns.append([cell(rng.uniform(-1, 1) * 10.0 ** rng.randint(-5, 5)) for _ in range(n)])
        self.check_columns(columns)
        twice = 0
        for col in columns:
            xs = [ref.flt(c)[1] for c in col]
            if ref.fsum_ref(xs)[1] / len(xs) != ref.fmean_ref(xs)[1]:
                twice += 1
        self.assertGreater(twice, 3)                                                    # fl(fl(sum) / n) would have been wrong for some

    def test_order_of_the_rows_does_not_change_one_byte(self):
        rng = random.Random(4406)
        cols = [[cell(bits_double(rng) * 1e-5) for _ in range(rng.randint(5, 60))] for _ in range(30)]
        first = self.groups(cols)
        for seed in range(5):
            self.assertEqual(self.groups(cols, shuffle=seed), first)

    def test_a_group_of_one_and_zeros_and_mixed_signs(self):
        for col in (["0"], ["-0"], ["0.5"], ["-2.75"], ["0", "0", "0"], ["1e308"], ["-1e308"], ["5e-324"], ["-5e-324"], ["1", "-1", "1", "-1", "3"], ["123456789.123456789", "-123456789.123456789"]):
            self.check_columns([col])

    def test_the_502_cases_of_the_spike_every_one(self):
        """scripts/spikes/superacc_check.py's generator, ported: random bit patterns of 1 to 1,000 doubles, cancelling runs, subnormals, huge and tiny mixes, forty
        lists of two-decimal prices, sums that sit at or near a tie, DBL_MAX sums, and one list of 300,000 random doubles (the spike used 1,000,000). Each is one group
        of one table; the sum and the mean must be Python's exact integer arithmetic rounded once. The cases whose exact sum is beyond the largest double
        (30 of them) are the refusal, run alone and named, and their mean is a number."""
        rng = random.Random(1)
        cs = spike_cases(rng, 300000)
        self.assertEqual(len(cs), 502)
        finite = [c for c in cs if ref.fsum_ref(c)[0] == "ok"]
        over = [c for c in cs if ref.fsum_ref(c)[0] == "overflow"]
        self.assertGreater(len(over), 10)                                            # random exponents overflow the sum of a long list often
        self.assertGreater(len(finite), 400)
        rows = [[str(i), cell(x)] for i, c in enumerate(finite) for x in c]
        data = enc([["k", "x"]] + rows)
        got = self.s.table("t.csv", data, "--group", "k", "--agg", "sum:x:float,mean:x:float", "--format", "csv", "--max-groups", "1000", "--max-state-bytes", "100000000")
        self.assertEqual(got.status, 0, got)
        out = sorted(list(csv.reader(io.StringIO(got.stdout.decode("latin-1"), newline="")))[1:], key=lambda r: int(r[0]))
        self.assertEqual(len(out), len(finite))
        for c, (k, s_, m_) in zip(finite, out):
            xs = [float(cell(x)) for x in c]
            self.assertTrue(ref.same_text(s_, ref.fsum_ref(xs)[1]) and ref.same_text(m_, ref.fmean_ref(xs)[1]), (k, len(c), c[:3], s_, m_))
            try:
                f = math.fsum(xs)
            except OverflowError:
                continue
            self.assertEqual(ref.fsum_ref(xs)[1], 0.0 if f == 0 else f)           # and math.fsum agrees where it can
        rows = [[str(i), cell(x)] for i, c in enumerate(over) for x in c]
        for i, c in enumerate(over):                                                    # each alone: the refusal names one group
            got = self.s.table("t.csv", enc([["k", "x"]] + [["z", cell(x)] for x in c]), "--group", "k", "--agg", "sum:x:float,mean:x:float")
            self.assertEqual((got.first_rule(), got.error()["detail"]["group"]), ("agg.float-overflow", "z"), got)
            got = self.s.table("t.csv", enc([["k", "x"]] + [["z", cell(x)] for x in c]), "--group", "k", "--agg", "mean:x:float", "--format", "csv")
            want_mean = ref.fmean_ref([float(cell(x)) for x in c])
            self.assertTrue(got.status == 0 and ref.same_text(got.stdout.decode().split("\n")[1].split(",")[1], want_mean[1]), (got, want_mean))

    # ---- overflow: the exact sum is beyond the largest double --------------------------------------------------------------

    def test_a_sum_beyond_the_largest_double_is_refused_and_names_the_group(self):
        data = "g,x\nb,1.7976931348623157e308\na,1\nb,1.7976931348623157e308\na,2\n"
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "sum:x:float", "--format", "csv")
        self.assertEqual((got.status, got.stdout), (8, b"g,sum:x\n"), got)                  # the header went out; no group row did
        self.assertIn("agg.float-overflow", got.stderr.decode(), got)
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "sum:x:float")
        self.assertEqual((got.status, got.first_rule()), (8, "agg.float-overflow"), got)
        d = got.error()["detail"]
        self.assertEqual((d["group"], d["column"], d["context"]), ("b", "x", "sum"), got)
        self.assertEqual(validate(got), [], got)
        # the mean of the same group is a number; a filter that leaves the sum in range reads fine
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "mean:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"g,mean:x\na,1.5\nb,1.7976931348623157e308\n", got)
        got = self.s.table("t.csv", data, "--where", "x:float < 100", "--group", "g", "--agg", "sum:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"g,sum:x\na,3.0\n", got)

    def test_the_largest_double_itself_is_not_an_overflow_and_one_unit_past_it_is(self):
        top = "1.7976931348623157e308"
        for col, ok in ((["1.7976931348623157e308"], True), ([top, "-" + top, top], True), ([top, "9.979201547673598e291"], True),           # + half a unit of the last place: the tie goes to even, which is 2^1024: beyond
                        ([top, "9.979201547673599e291"], False), ([top, "1e292"], False), (["-" + top, "-1e292"], False)):
            got = self.s.table("t.csv", enc([["g", "x"]] + [["a", c] for c in col]), "--group", "g", "--agg", "sum:x:float", "--format", "csv")
            ws = ref.fsum_ref([ref.flt(c)[1] for c in col])
            with self.subTest(col=col):
                if ws[0] == "ok":
                    self.assertEqual(got.status, 0, got)
                    self.assertTrue(ref.same_text(got.stdout.decode().split("\n")[1].split(",")[1], ws[1]), got)
                else:
                    self.assertEqual(got.status, 8, got)
                    self.assertIn("agg.float-overflow", got.stderr.decode(), got)
                self.assertEqual(ws[0] == "ok", ok, (col, ws))

    def test_of_several_overflowing_groups_the_smallest_key_is_named_whatever_the_order(self):
        big = "1.7976931348623157e308"
        for order in ((("z", "m", "c"), (1, 0, 2)), (("c", "m", "z"), (0, 1, 2))):
            rows = [[g, big] for g in order[0]] + [[g, big] for g in order[0]] + [["a", "1"]]
            got = self.s.table("t.csv", enc([["g", "x"]] + rows), "--group", "g", "--agg", "sum:x:float")
            self.assertEqual(got.first_rule(), "agg.float-overflow")
            self.assertEqual(got.error()["detail"]["group"], "c", got)
        # two group columns: joined by a comma, in the order of --group
        got = self.s.table("t.csv", enc([["g", "h", "x"], ["b", "y", big], ["b", "y", big], ["b", "x", big], ["b", "x", big]]), "--group", "g,h", "--agg", "sum:x:float")
        self.assertEqual(got.error()["detail"]["group"], "b,x", got)

    def test_a_cell_that_is_not_a_float_is_refused_first_and_names_its_row(self):
        big = "1.7976931348623157e308"
        data = "g,x\na,%s\na,%s\na,oops\n" % (big, big)
        for spec, context in (("sum:x:float", "sum"), ("mean:x:float", "mean")):
            got = self.s.table("t.csv", data, "--group", "g", "--agg", spec)
            self.assertEqual((got.status, got.first_rule()), (8, "value.not-float"), got)
            d = got.error()["detail"]
            self.assertEqual((d["row"], d["context"], d["column"]), (3, context, "x"), got)

    # ---- the rest of the plan ---------------------------------------------------------------------------------------------

    def test_sum_mean_min_max_count_distinct_of_one_column_in_one_plan(self):
        cells = ["1.5", "2.5", "-4", "1.5", "1e3"]
        rows = self.groups([cells], spec="sum:x:float,min:x:float,mean:x:float,max:x:float,distinct:x:float,count")
        self.assertEqual(rows, [["0", "1001.5", "-4.0", "200.3", "1000.0", "4", "5"]])
        rows = self.groups([cells], spec="count,mean:x:float,sum:x:float")
        self.assertEqual(rows, [["0", "5", "200.3", "1001.5"]])

    def test_two_float_columns_each_have_their_own_accumulator(self):
        data = "g,x,y\na,1e16,0.5\na,1,0.25\na,-1e16,0.125\nb,3,1\n"
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "sum:x:float,sum:y:float,mean:y:float", "--format", "csv")
        self.assertEqual(got.stdout, b"g,sum:x,sum:y,mean:y\na,1.0,0.875,0.2916666666666667\nb,3.0,1.0,1.0\n", got)

    def test_a_float_sum_beside_an_integer_sum_and_a_decimal_sum_of_other_columns(self):
        data = "g,x,n,p\na,0.1,5,1.25\na,0.2,6,2.50\nb,1e16,-3,0.01\nb,1,4,0.02\nb,-1e16,0,0\n"
        got = self.s.table("t.csv", data, "--group", "g", "--agg", "sum:n,sum:x:float,sum:p:dec(2),count,mean:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"g,sum:n,sum:x,sum:p,count,mean:x\na,11,0.30000000000000004,3.75,2,0.15000000000000002\nb,1,1.0,0.03,3,0.3333333333333333\n", got)

    def test_sorting_by_a_float_sum_or_mean(self):
        rng = random.Random(4407)
        cols = [[cell(rng.uniform(-100, 100)) for _ in range(rng.randint(1, 6))] for _ in range(40)]
        sums = {i: ref.fsum_ref([ref.flt(c)[1] for c in col])[1] for i, col in enumerate(cols)}
        means = {i: ref.fmean_ref([ref.flt(c)[1] for c in col])[1] for i, col in enumerate(cols)}
        rows = [[str(i), c] for i, col in enumerate(cols) for c in col]
        data = enc([["k", "x"]] + rows)
        for label, vals, spec in (("sum:x", sums, "sum:x:float"), ("mean:x", means, "mean:x:float")):
            for desc in (False, True):
                got = self.s.table("t.csv", data, "--group", "k", "--agg", spec, "--sort", ("-" if desc else "") + label, "--limit", "1000")
                self.assertEqual(got.status, 0, got)
                got_keys = [int(r[0]) for r in got.data()["rows"]]
                want = sorted(range(len(cols)), key=lambda i: (vals[i], str(i)), reverse=False)
                if desc:
                    want = sorted(range(len(cols)), key=lambda i: (-vals[i], str(i)))
                self.assertEqual(got_keys, want, (label, desc))

    def test_quoted_group_keys_take_the_slow_way_and_give_the_same_sums(self):
        rng = random.Random(4408)
        cells = [cell(bits_double(rng) * 1e-9) for _ in range(40)]
        plain = self.s.table("t.csv", enc([["k", "x"]] + [["a", c] for c in cells]), "--group", "k", "--agg", "sum:x:float,mean:x:float", "--format", "csv").stdout.split(b"\n")[1].split(b",", 1)[1]
        quoted = self.s.table("t.csv", enc([["k", "x"]] + [['a"b', c] for c in cells]), "--group", "k", "--agg", "sum:x:float,mean:x:float", "--format", "csv").stdout.split(b"\n")[1].split(b",", 1)[1]
        self.assertEqual(plain, quoted)
        want = want_pair(cells)
        self.assertTrue(ref.same_text(plain.decode().split(",")[0], want[0][1]))

    def test_distinct_in_the_plan_sends_every_row_the_slow_way_and_the_sum_is_the_same(self):
        rng = random.Random(4409)
        cells = [cell(bits_double(rng) * 1e-9) for _ in range(50)]
        got = self.s.table("t.csv", enc([["k", "x"]] + [["a", c] for c in cells]), "--group", "k", "--agg", "sum:x:float,distinct:x:float,mean:x:float", "--format", "csv")
        row = got.stdout.decode().split("\n")[1].split(",")
        ws, wm = want_pair(cells)
        self.assertTrue(ref.same_text(row[1], ws[1]) and ref.same_text(row[3], wm[1]), (row, ws, wm))

    def test_without_a_group_the_whole_file_is_one_sum(self):
        cells = ["1e16", "1", "-1e16", "0.25"]
        got = self.s.table("t.csv", enc([["x"]] + [[c] for c in cells]), "--agg", "sum:x:float,mean:x:float", "--format", "csv")
        self.assertEqual(got.stdout, b"sum:x,mean:x\n1.25,0.3125\n", got)

    # ---- state ------------------------------------------------------------------------------------------------------------

    def test_a_float_accumulator_is_584_bytes_of_state_and_counted_against_the_ceiling(self):
        data = "g,x\na,1\na,2\n"
        # one group "a": its key is 4 + 1 bytes, and each float sum or mean 73 integers
        for spec, state in (("sum:x:float", 589), ("sum:x:float,mean:x:float", 1173), ("mean:x:float,sum:x:float,min:x:float", 1173)):
            ok = self.s.table("t.csv", data, "--group", "g", "--agg", spec, "--max-state-bytes", str(state))
            self.assertEqual(ok.status, 0, (spec, ok))
            no = self.s.table("t.csv", data, "--group", "g", "--agg", spec, "--max-state-bytes", str(state - 1))
            self.assertEqual((no.status, no.first_rule()), (8, "limit.state-too-large"), (spec, no))
        ok = self.s.table("t.csv", data, "--group", "g", "--agg", "min:x:float,max:x:float", "--max-state-bytes", "5")
        self.assertEqual(ok.status, 0, ok)                                              # a minimum and a maximum hold no accumulator

    def test_many_groups_of_a_float_sum_are_held_in_bounded_memory_and_counted(self):
        n = 3000
        data = enc([["k", "x"]] + [[str(i), "0.5"] for i in range(n)])
        got = self.s.table("t.csv", data, "--group", "k", "--agg", "sum:x:float", "--max-state-bytes", str(n * 584 + 4 * n + sum(len(str(i)) for i in range(n))), "--max-groups", "100000", "--format", "csv")
        self.assertEqual(got.status, 0, got)
        got = self.s.table("t.csv", data, "--group", "k", "--agg", "sum:x:float", "--max-state-bytes", str(n * 584), "--max-groups", "100000")
        self.assertEqual(got.first_rule(), "limit.state-too-large", got)

    # ---- fuzz -------------------------------------------------------------------------------------------------------------

    def test_fuzzed_columns_never_trap_and_agree_with_the_reference(self):
        rng = random.Random(4410)
        alpha = "0123456789012345678901234567890+-. eE_,x\t'\"\\iInNfFaA"
        for _ in range(500):
            cells = []
            for _ in range(rng.randint(1, 10)):
                k = rng.random()
                cells.append("".join(rng.choice(alpha) for _ in range(rng.randint(0, 12))) if k < 0.3 else rng.choice(tf.lex_float_cells(rng, 4) + ["inf", "nan", "1e999", "", "1.7976931348623157e308", "-1.7976931348623157e308"]))
            spec = rng.choice(["sum:x:float", "mean:x:float", "sum:x:float,mean:x:float"])
            got = self.s.table("t.csv", enc([["g", "x"]] + [["a", c] for c in cells]), "--group", "g", "--agg", spec)
            self.assertNotIn(got.status, TRAPS, (cells, got))
            refusal, xs = None, []
            for n, c in enumerate(cells, 1):
                r = ref.flt(c)
                if r[0] == "refuse":
                    refusal = (n, r[1])
                    break
                xs.append(r[1])
            if refusal:
                self.assertEqual((got.status, got.first_rule(), got.error()["detail"]["row"]), (8, refusal[1], refusal[0]), (spec, cells, got))
                continue
            want_s, want_m = ref.fsum_ref(xs), ref.fmean_ref(xs)
            if "sum" in spec and want_s[0] == "overflow":
                self.assertEqual((got.status, got.first_rule()), (8, "agg.float-overflow"), (spec, cells, got))
                continue
            self.assertEqual(got.status, 0, (spec, cells, got))
            row = got.data()["rows"][0]
            labels = got.data()["columns"]
            for lab, val in zip(labels[1:], row[1:]):
                want = want_s if lab.startswith("sum") else want_m
                self.assertTrue(ref.same_text(val, want[1]), (lab, val, want, cells))


def sum_cell(rng):
    k = rng.random()
    if k < 0.1:
        return rng.choice(["1.7976931348623157e308", "-1.7976931348623157e308", "1e308", "-1e308", "9e307"])
    if k < 0.2:
        return rng.choice(["1e16", "-1e16", "1", "-1", "5e-324", "-5e-324", "0.1", "-0.1", "1e-320"])
    return tf.float_cell(rng)


def make_sum_table(rng):
    names, kinds, rows = tf.make_float_table(rng)
    for row in rows:
        for i, kind in enumerate(kinds):
            if kind == "float" and i < len(row) and rng.random() < 0.3 and row[i] not in ("", "x", "nan", "inf", "-inf", "1e999", "1e-999", "1,5", " 1", "1e"):
                row[i] = sum_cell(rng)
    return names, kinds, rows


class SumPlans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Scratch()

    @classmethod
    def tearDownClass(cls):
        cls.s.cleanup()

    def test_random_plans_with_sum_and_mean(self):
        funcs = ("count", "min", "max", "distinct", "sum", "sum", "mean", "mean")
        tf.FloatPlans.check_plans(self, 20261101, funcs, 1700, make_sum_table, 800, 100)


class Parallel(par.Same):
    """Every thread count and chunk size gives the sequential bytes for the exact sum and mean, and the sequential answer is the oracle's."""

    def rows(self, n, seed, groups=("a", "b,c", 'q"r')):
        rng = random.Random(seed)
        xs = [bits_double(rng) * 1e-12 for _ in range(n // 2)]
        xs = xs + [-x for x in xs] + [rng.uniform(-1, 1) for _ in range(n - 2 * (n // 2))]
        rng.shuffle(xs)
        return [[str(i), cell(x), groups[i % len(groups)]] for i, x in enumerate(xs)], xs

    def table(self, rows, bad=None):
        rows = [list(r) for r in rows]
        for at, c in (bad or {}).items():
            rows[at][1] = c
        return enc([["id", "x", "t"]] + rows)

    def test_exact_sums_and_means_are_the_sequential_ones_for_every_thread_count_and_chunk(self):
        rows, xs = self.rows(150, 31)
        data = self.table(rows)
        for args in (["--agg", "sum:x:float,mean:x:float", "--format", "csv"], ["--group", "t", "--agg", "sum:x:float,mean:x:float,count"], ["--group", "t", "--agg", "sum:x:float", "--sort", "-sum:x"],
                     ["--group", "t,id", "--agg", "sum:x:float,mean:x:float", "--max-groups", "1000"], ["--where", "x:float > 0", "--group", "t", "--agg", "mean:x:float,min:x:float,distinct:x:float"]):
            self.same(data, args, "float sums")
        status, out, err = self.outcome(data, ["--agg", "sum:x:float,mean:x:float", "--format", "csv"])
        s, m = out.decode().split("\n")[1].split(",")
        self.assertTrue(ref.same_text(s, ref.fsum_ref(xs)[1]) and ref.same_text(m, ref.fmean_ref(xs)[1]), (out, ref.fsum_ref(xs), ref.fmean_ref(xs)))

    def test_plain_addition_in_a_thread_would_differ_and_the_exact_sum_does_not(self):
        """A column that cancels: every partial sum of a plain thread loses the small terms, and the merge of the partial sums is order-dependent."""
        rows = []
        for i in range(120):
            big = 1e16
            rows += [[str(3 * i), cell(big), "a"], [str(3 * i + 1), "1", "a"], [str(3 * i + 2), cell(-big), "a"]]
        data = self.table(rows)
        for args in (["--agg", "sum:x:float", "--format", "csv"], ["--group", "t", "--agg", "sum:x:float,mean:x:float", "--format", "csv"]):
            self.same(data, args, "cancelling")
        status, out, err = self.outcome(data, ["--agg", "sum:x:float", "--format", "csv"])
        self.assertEqual(out, b"sum:x\n120.0\n")
        xs = [1e16, 1.0, -1e16] * 120
        self.assertNotEqual(plain_sum(xs), 120.0)

    def test_many_groups_merge_and_a_range_whose_groups_do_not_fit_is_read_by_the_parent(self):
        rng = random.Random(33)
        rows = [[str(i), cell(rng.uniform(-1, 1) * 10.0 ** rng.randint(-5, 5)), "g%d" % (i % 200)] for i in range(1200)]
        data = self.table(rows)
        for args in (["--group", "t", "--agg", "sum:x:float,mean:x:float", "--format", "csv"], ["--group", "t", "--agg", "sum:x:float", "--sort", "-sum:x", "--top", "5"]):
            self.same(data, args, "200 groups")

    def test_state_ceiling_is_the_same_in_every_thread_count(self):
        rows = [[str(i), "0.5", "g%d" % (i % 20)] for i in range(300)]
        data = self.table(rows)
        for ceiling in (20 * (584 + 7) - 1, 20 * (584 + 7), 20 * (584 + 7) + 500):
            self.same(data, ["--group", "t", "--agg", "sum:x:float", "--max-state-bytes", str(ceiling), "--format", "csv"], "state %d" % ceiling)

    def test_the_first_refusal_in_file_order_wins_and_overflow_comes_after(self):
        rows, _ = self.rows(150, 34)
        for bad in ({120: "1.5x", 40: "abc"}, {40: "", 120: "nan"}, {120: "1e999", 121: "x"}, {149: "-"}, {0: " 1"}):
            data = self.table(rows, bad)
            for args in (["--group", "t", "--agg", "sum:x:float"], ["--group", "t", "--agg", "mean:x:float", "--format", "csv"], ["--agg", "sum:x:float,min:x:float"]):
                self.same(data, args, "refusals %r" % (bad,))
        over = [[str(i), "1.7976931348623157e308", "a" if i % 2 else "b"] for i in range(40)]
        data = self.table(over)
        for args in (["--group", "t", "--agg", "sum:x:float"], ["--group", "t", "--agg", "sum:x:float", "--format", "csv"], ["--agg", "sum:x:float,mean:x:float"]):
            self.same(data, args, "overflow")
        data = self.table(over, {39: "bad"})
        self.same(data, ["--group", "t", "--agg", "sum:x:float"], "a bad cell after the overflowing rows")

    def test_quoted_newlines_and_ragged_rows_beside_a_float_sum(self):
        rows = [[str(i), "%d.5e0" % i, 'multi\nline "%d"' % (i % 3)] for i in range(60)]
        data = self.table(rows) + b"61\n" + self.table([["62", "2.5", "z"]]).split(b"\n", 1)[1]
        for args in (["--group", "t", "--agg", "sum:x:float,mean:x:float"], ["--agg", "sum:x:float", "--format", "csv"]):
            self.same(data, args, "ragged and quoted")


if __name__ == "__main__":
    unittest.main()
