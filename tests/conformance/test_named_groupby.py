"""NB1 of docs/named-benchmarks.md: the groupby generator, its reference answers and the gates, before any clock exists.

(a) the generator is deterministic (same seed, same bytes, whatever the block size; pinned digests, the same on the Mac and on gram) and its columns have exactly the
    documented cardinalities, types and ranges; (b) `table`'s q1 to q5, in the flag form and the --query form, equal the reference, which is computed by a streaming
    pass over the written file and again by SQLite; (c) the questions are a machine-readable file, the ones `table` cannot answer carry a probe that must be refused
    with a stated rule; (d) the gates can fail: a mutated reference, a mutated command and a mutated file are each caught.

No timing anywhere, no network, no file larger than 100,000 rows.
"""

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import bench_named_gen as gen  # noqa: E402
import bench_named_ref as ref  # noqa: E402

# pinned: the generator specified in scripts/bench_named_gen.py gives these bytes (checked on the Mac, Python 3.14.5, and on gram, 3.14.4, x86-64)
SHA_1E5_K100 = "ca52ff7e93184b7f84511e7a1e66027ed106a5e48ef43e75b7f52fd124a7c3a1"
SHA_2E4_K10 = "62314e2f2fe25aaa2147dd23677f8a5e4747a09b28e0863155ede8242ef1b6a1"


def sha(n, k, seed=gen.DEFAULT_SEED, block=gen.BLOCK):
    h = hashlib.sha256()
    for text in gen.blocks(n, k, seed, block):
        h.update(text.encode("ascii"))
    return h.hexdigest()


class Shared(unittest.TestCase):
    """One 100,000-row file for the class, outside the repository, deleted after."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="named-nb1-")
        cls.path, cls.meta = gen.write(100_000, 100, cls.tmp.name)
        cls.ref = ref.reference(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Generator(Shared):
    def test_same_seed_same_bytes_whatever_the_block(self):
        self.assertEqual(sha(100_000, 100), SHA_1E5_K100)
        self.assertEqual(sha(20_000, 10), SHA_2E4_K10)
        self.assertEqual(sha(20_000, 10, block=7), SHA_2E4_K10)  # blocks are a memory bound, not part of the bytes
        self.assertEqual(sha(20_000, 10, block=19_999), SHA_2E4_K10)

    def test_the_file_written_is_the_stream(self):
        self.assertEqual(self.meta["sha256"], SHA_1E5_K100)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), SHA_1E5_K100)
        self.assertEqual(self.meta["bytes"], self.path.stat().st_size)
        self.assertEqual(self.meta["seed"], 108)
        self.assertIn("NOT the h2oai R generator", self.meta["label"])
        self.assertIn("not comparable", self.meta["label"])
        self.assertEqual(self.path.name, "G1_1e5_1e2_0_0.csv")

    def test_another_seed_other_bytes(self):
        self.assertNotEqual(sha(20_000, 10, seed=109), SHA_2E4_K10)

    def test_splitmix64_reference_values(self):
        # the first outputs of SplitMix64 from state 0, from the published reference implementation (Vigna): the generator in the file is that function
        state = 0
        want = [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]
        got = []
        for _ in want:
            state = (state + gen.GAMMA) & gen.MASK
            z = state
            z = ((z ^ (z >> 30)) * gen.M1) & gen.MASK
            z = ((z ^ (z >> 27)) * gen.M2) & gen.MASK
            got.append(z ^ (z >> 31))
        self.assertEqual(got, want)

    def test_layout_is_exactly_the_documented_spec(self):
        self.assertEqual(ref.validate_layout(self.path, 100_000, 100), [])
        s = gen.spec(100_000, 100)
        self.assertEqual({c: (v["kind"], v["count"], v["lo"], v["hi"]) for c, v in s.items()}, {
            "id1": ("text", 100, 1, 100), "id2": ("text", 100, 1, 100), "id3": ("text", 1000, 1, 1000),
            "id4": ("int", 100, 1, 100), "id5": ("int", 100, 1, 100), "id6": ("int", 1000, 1, 1000),
            "v1": ("int", 5, 1, 5), "v2": ("int", 15, 1, 15), "v3": ("millionths", 100_000_000, 0, 99_999_999)})
        lines = self.path.read_text().splitlines()
        self.assertEqual(lines[0], "id1,id2,id3,id4,id5,id6,v1,v2,v3")
        self.assertEqual(len(lines), 100_001)

    def test_layout_at_other_k(self):
        with tempfile.TemporaryDirectory(prefix="named-nb1-") as d:
            for n, k in ((20_000, 10), (20_000, 2)):
                path, _ = gen.write(n, k, d)
                self.assertEqual(ref.validate_layout(path, n, k), [], (n, k))

    def test_layout_check_can_fail(self):
        lines = self.path.read_text().splitlines(keepends=True)

        def with_edit(i, f):
            out = list(lines)
            cells = out[i].rstrip("\n").split(",")
            f(cells)
            out[i] = ",".join(cells) + "\n"
            return out

        cases = {
            "id1 outside K": lambda: with_edit(5, lambda c: c.__setitem__(0, "id101")),
            "id1 not 3 digits": lambda: with_edit(5, lambda c: c.__setitem__(0, "id01")),
            "id3 outside N/K": lambda: with_edit(5, lambda c: c.__setitem__(2, "id0000001001")),
            "id4 zero": lambda: with_edit(5, lambda c: c.__setitem__(3, "0")),
            "id6 leading zero": lambda: with_edit(5, lambda c: c.__setitem__(5, "007")),
            "v1 six": lambda: with_edit(5, lambda c: c.__setitem__(6, "6")),
            "v2 sixteen": lambda: with_edit(5, lambda c: c.__setitem__(7, "16")),
            "v3 a hundred": lambda: with_edit(5, lambda c: c.__setitem__(8, "100")),
            "v3 trailing zero": lambda: with_edit(5, lambda c: c.__setitem__(8, "5.50")),
            "v3 seven decimals": lambda: with_edit(5, lambda c: c.__setitem__(8, "5.1234567")),
            "v3 exponent": lambda: with_edit(5, lambda c: c.__setitem__(8, "1e-06")),
            "a row dropped": lambda: lines[:7] + lines[8:],
            "a value never drawn (id1 short by one)": lambda: [("id001" + l[5:]) if l.startswith("id002,") else l for l in lines],
            "header renamed": lambda: ["id1,id2,id3,id4,id5,id6,v1,v2,V3\n"] + lines[1:],
        }
        with tempfile.TemporaryDirectory(prefix="named-nb1-") as d:
            for name, make in cases.items():
                p = pathlib.Path(d) / "mut.csv"
                p.write_text("".join(make()))
                problems = ref.validate_layout(p, 100_000, 100)
                self.assertTrue(problems, "the layout check missed: " + name)

    def test_data_never_inside_the_repository(self):
        with self.assertRaises(SystemExit):
            gen.write(1000, 10, ROOT / "build" / "named-data")
        with self.assertRaises(SystemExit):
            gen.write(1000, 10, ROOT)

    def test_default_directory_is_outside_the_repository(self):
        self.assertFalse(gen.inside_repo(gen.default_dir()))

    def test_n_must_be_a_multiple_of_k(self):
        with self.assertRaises(ValueError):
            gen.spec(1001, 100)

    def test_v3_text_is_fwrites(self):
        self.assertEqual([gen.v3_text(m) for m in (0, 1, 10, 500_000, 5_000_000, 97_861_311, 99_999_999)],
                         ["0", "0.000001", "0.00001", "0.5", "5", "97.861311", "99.999999"])


class Reference(Shared):
    def test_streaming_reference_equals_sqlite(self):
        self.assertEqual(ref.reference_sqlite(self.path), self.ref)

    def test_q10_by_both_paths(self):
        with tempfile.TemporaryDirectory(prefix="named-nb1-") as d:
            path, _ = gen.write(5_000, 100, d)
            a = ref.reference(path, with_q10=True)
            self.assertEqual(ref.reference_sqlite(path, with_q10=True), a)
            self.assertEqual(sum(int(r[-1]) for r in a["q10"]["rows"]), 5_000)

    def test_shapes(self):
        r = self.ref
        # q2's pairs: 10,000 possible, 100,000 draws with replacement leave a few unseen (the file's, fixed by the seed: 9,998)
        self.assertEqual([len(r[q]["rows"]) for q in ("q1", "q2", "q3", "q4", "q5")], [100, 9998, 1000, 100, 1000])
        self.assertEqual(r["q1"]["columns"], ["id1", "sum:v1"])
        # the totals every question must agree on: the sum of v1 over all rows is the same in q1, q2 and q3
        total = sum(int(x[1]) for x in r["q1"]["rows"])
        self.assertEqual(total, sum(int(x[2]) for x in r["q2"]["rows"]))
        self.assertEqual(total, sum(int(x[1]) for x in r["q3"]["rows"]))
        self.assertEqual(total, sum(int(x[1]) for x in r["q5"]["rows"]))
        self.assertEqual(r["q1"]["chk"], str(total))
        self.assertEqual(r["q3"]["chk"].split(";")[0], str(total))

    def test_hand_computed_fixture(self):
        # exact ties: the mean of 0.000001 and 0.000002 is 0.0000015 -> half to even -> 0.000002; of 0.000002 and 0.000003 is 0.0000025 -> 0.000002
        text = ("id1,id2,id3,id4,id5,id6,v1,v2,v3\n"
                "id001,id001,id0000000001,1,1,1,1,1,0.000001\n"
                "id001,id001,id0000000001,1,1,1,2,2,0.000002\n"
                "id002,id001,id0000000002,2,1,2,1,1,0.000002\n"
                "id002,id002,id0000000002,2,1,2,2,2,0.000003\n"
                "id002,id002,id0000000002,2,1,2,2,3,99.999999\n")
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "f.csv"
            p.write_text(text)
            r = ref.reference(p)
            self.assertEqual(r["q1"]["rows"], [("id001", "3"), ("id002", "5")])
            self.assertEqual(r["q2"]["rows"], [("id001", "id001", "3"), ("id002", "id001", "1"), ("id002", "id002", "4")])
            self.assertEqual(r["q3"]["rows"], [("id0000000001", "3", "0.000002"), ("id0000000002", "5", "33.333335")])
            # q4 id4=1: v1 1.5, v2 1.5, v3 0.000002 (tie to even); id4=2: v1 5/3, v2 6/3, v3 (2+3+99999999)/3 = 33333334.67 millionths -> 33.333335
            self.assertEqual(r["q4"]["rows"], [("1", "1.500000", "1.500000", "0.000002"), ("2", "1.666667", "2.000000", "33.333335")])
            self.assertEqual(r["q5"]["rows"], [("1", "3", "3", "0.000003"), ("2", "5", "6", "100.000004")])
            self.assertEqual(ref.reference_sqlite(p), r)
            self.assertEqual(r["q5"]["chk"], "8;9;100.0")  # the owner's chk: ints as they are, a float rounded to 3 decimals
            # and the tool agrees, in both forms
            self.assertEqual(ref.gate(p, r, with_q10_defect=False), [])

    def test_mean_rounding_is_half_to_even(self):
        self.assertEqual([ref.div_half_even(n, 2) for n in (1, 3, 5, 7, 2, 4)], [0, 2, 2, 4, 1, 2])
        self.assertEqual(ref.div_half_even(5, 3), 2)
        self.assertEqual(ref.mean6(3, 2, 1), 1_500_000)

    def test_chk_text_is_make_chk(self):
        self.assertEqual(ref.chk_text([3002320, 500022.1345, 1.0]), "3002320;500022.134;1.0")


class Questions(unittest.TestCase):
    def setUp(self):
        self.q = ref.load_questions()

    def test_file_is_complete(self):
        ids = [q["id"] for q in self.q["questions"]]
        self.assertEqual(ids, ["q%d" % i for i in range(1, 11)])
        self.assertEqual({q["id"]: q["class"] for q in self.q["questions"]},
                         {"q1": "E", "q2": "E", "q3": "E", "q4": "E", "q5": "E", "q6": "X", "q7": "X", "q8": "X", "q9": "X", "q10": "D"})
        self.assertIn("not the h2oai R generator", self.q["label"])
        for q in self.q["questions"]:
            self.assertTrue(q["sql"].startswith("SELECT"), q["id"])
            if q["class"] in ("E", "B"):
                self.assertIn("{FILE}", q["flags"])
                self.assertTrue(any("{FILE}" in a for a in q["query"]))
                self.assertEqual(q["flags"][-2:], ["csv", "{FILE}"], q["id"])
                self.assertTrue(q["columns"])
            elif q["class"] == "X":
                self.assertTrue(q["blocker"] and q["link"] and q["probe"]["rule"], q["id"])
            else:
                self.assertTrue(q["blocker"] and q["defect"]["argvs"], q["id"])

    def test_no_clock_in_this_slice(self):
        for name in ("bench_named_gen.py", "bench_named_ref.py"):
            text = (ROOT / "scripts" / name).read_text()
            for word in ("import time", "perf_counter", "monotonic", "timeit", "hyperfine"):
                self.assertNotIn(word, text, name)


class Gates(Shared):
    """(b), (c) and (d): table against the reference, and the proof that the gate can fail."""

    def test_table_equals_the_reference_in_both_forms(self):
        self.assertEqual(ref.gate(self.path, self.ref), [])

    def test_a_mutated_reference_is_caught(self):
        def mutate(r, qid, f):
            r = copy.deepcopy(r)
            f(r[qid])
            return r

        def bump_sum(x):
            row = list(x["rows"][3])
            row[-1] = str(int(row[-1]) + 1)
            x["rows"][3] = tuple(row)

        def last_digit(x):
            row = list(x["rows"][7])
            row[-1] = row[-1][:-1] + ("0" if row[-1][-1] != "0" else "1")
            x["rows"][7] = tuple(row)

        mutants = {
            "q1 one sum off by one": lambda r: mutate(r, "q1", bump_sum),
            "q2 one sum off by one": lambda r: mutate(r, "q2", bump_sum),
            "q3 a mean's last digit": lambda r: mutate(r, "q3", last_digit),
            "q4 a mean's last digit": lambda r: mutate(r, "q4", last_digit),
            "q5 sum:v3's last digit": lambda r: mutate(r, "q5", last_digit),
            "q2 a group dropped": lambda r: mutate(r, "q2", lambda x: x["rows"].pop()),
            "q4 a column renamed": lambda r: mutate(r, "q4", lambda x: x["columns"].__setitem__(1, "avg:v1")),
        }
        for name, make in mutants.items():
            p = ref.gate(self.path, make(self.ref), with_q10_defect=False)
            self.assertTrue(p, "the gate missed a mutated reference: " + name)

    def test_the_reference_arithmetic_mutants_are_caught(self):
        # the reference's own arithmetic, broken in three plausible ways, must disagree with the tool (the rounding mode, the scale, the mean's divisor)
        real = ref.div_half_even
        try:
            ref.div_half_even = lambda n, d: n // d  # truncation
            p = ref.gate(self.path, ref.reference(self.path), with_q10_defect=False)
            self.assertTrue(p, "truncating means were not caught")
        finally:
            ref.div_half_even = real
        text = ("id1,id2,id3,id4,id5,id6,v1,v2,v3\n"
                "id001,id001,id0000000001,1,1,1,1,1,0.000001\n"
                "id001,id001,id0000000001,1,1,1,2,2,0.000002\n"
                "id002,id001,id0000000002,2,1,2,1,1,0.000002\n"
                "id002,id001,id0000000002,2,1,2,2,1,0.000003\n")
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "f.csv"
            p.write_text(text)
            try:
                ref.div_half_even = lambda n, d: (n + d // 2) // d  # ties up: the 1.5 -> 2 is right, the 2.5 -> 3 is wrong
                self.assertTrue(ref.gate(p, ref.reference(p), with_q10_defect=False), "ties-up rounding was not caught by the tie fixture")
            finally:
                ref.div_half_even = real
            self.assertEqual(ref.gate(p, ref.reference(p), with_q10_defect=False), [])

    def test_a_mutated_command_is_caught(self):
        def edit(qid, field, f):
            q = copy.deepcopy(ref.load_questions())
            for item in q["questions"]:
                if item["id"] == qid:
                    f(item[field])
            return q

        def sub(old, new):
            return lambda argv: argv.__setitem__(argv.index(old), new)

        mutants = {
            "q1 sums v2": edit("q1", "flags", sub("sum:v1", "sum:v2")),
            "q1 groups by id2": edit("q1", "flags", sub("id1", "id2")),
            "q2 forgets id2": edit("q2", "flags", sub("id1,id2", "id1")),
            "q3 mean at scale 5": edit("q3", "flags", sub("sum:v1,mean:v3:dec(6)@6", "sum:v1,mean:v3:dec(6)@5")),
            "q4 a mean as a sum": edit("q4", "flags", sub("mean:v1@6,mean:v2@6,mean:v3:dec(6)@6", "sum:v1,mean:v2@6,mean:v3:dec(6)@6")),
            "q5 v3 as float": edit("q5", "flags", sub("sum:v1,sum:v2,sum:v3:dec(6)", "sum:v1,sum:v2,sum:v3:float")),
            "q1 query form differs": edit("q1", "query", lambda a: a.__setitem__(-1, a[-1].replace("sum(v1)", "sum(v2)"))),
            "q5 query form a different key": edit("q5", "query", lambda a: a.__setitem__(-1, a[-1].replace("group by id6", "group by id5"))),
            "q3 query form with fewer groups allowed": edit("q3", "query", lambda a: a.__setitem__(a.index("1000000"), "10")),
        }
        self.q = ref.load_questions()
        for name, mutated in mutants.items():
            p = ref.gate(self.path, self.ref, questions=mutated, with_q10_defect=False)
            self.assertTrue(p, "the gate missed a mutated command: " + name)

    def test_an_x_that_starts_working_must_be_reclassified(self):
        q = copy.deepcopy(ref.load_questions())
        for item in q["questions"]:
            if item["id"] == "q9":
                item["probe"]["argv"] = ["--group", "id2,id4", "--agg", "sum:v1", "{FILE}"]  # a command that works: as if corr had landed
        p = ref.gate(self.path, self.ref, questions=q)
        self.assertTrue(any(x.startswith("q9") and "re-classify" in x for x in p), p)
        q = copy.deepcopy(ref.load_questions())
        for item in q["questions"]:
            if item["id"] == "q6":
                item["probe"]["rule"] = "agg.unknown-function"
        self.assertTrue(any(x.startswith("q6") for x in ref.gate(self.path, self.ref, questions=q)))

    def test_a_defect_that_is_fixed_must_be_reclassified(self):
        q = copy.deepcopy(ref.load_questions())
        for item in q["questions"]:
            if item["id"] == "q10":
                item["defect"]["argvs"] = [["--group", "id1,id2,id3,id4", "--agg", "count", "--format", "csv", "{FILE}"]]  # four keys work
        p = ref.gate(self.path, self.ref, questions=q)
        self.assertTrue(any(x.startswith("q10") and "re-classify" in x for x in p), p)

    def test_a_missing_reference_is_a_failure(self):
        r = dict(self.ref)
        del r["q3"]
        self.assertTrue(any(x.startswith("q3") for x in ref.gate(self.path, r, with_q10_defect=False)))


if __name__ == "__main__":
    unittest.main()
