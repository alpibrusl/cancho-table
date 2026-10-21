"""NB3 of docs/named-benchmarks.md: the join generator, its reference and the gates, before a clock is read.

(a) the generator is deterministic (same seed, same bytes, whatever the block; pinned digests) and its tables have exactly the documented sizes, key sets (90 percent common, unique right
keys, every left key at least once) and formats; (b) the reference is a fingerprint of the joined rows (the owner's chk plus a row digest that ties v2 to the row it was joined to), computed
by a streaming pass and again by SQLite; (c) a contender's joined rows are held to it row by row (a value off, a row missing, doubled or swapped with another's v2, a trailing byte, a wrong
header or column count each fail); (d) `table`'s five questions, flag form, `--query` form and the probe on standard input, equal the reference; (e) the contenders installed give the
reference's rows (a missing one is skipped with its reason printed).

No timing anywhere, no network, no table above 20,000 rows.
"""

import copy
import hashlib
import json
import os
import pathlib
import random
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import procs as subprocess  # noqa: E402  every child in a group that is reaped
import bench_named_gen as gen  # noqa: E402


def mods():
    for name in ("bench_named_join_gen.py", "bench_named_join_ref.py", "bench_named_join_questions.json"):
        if not (ROOT / "scripts" / name).exists():
            raise AssertionError("scripts/%s does not exist: NB3 is not built" % name)
    import bench_named_join_gen as jgen
    import bench_named_join_ref as jref
    return jgen, jref


TABLE = os.environ.get("TOOLBOX_BIN")
TABLE = str(pathlib.Path(TABLE) / "table") if TABLE else str(ROOT / "build" / "table")

# pinned: the generator specified in scripts/bench_named_join_gen.py gives these bytes at N = 2000 (K irrelevant: the join layout has none)
SHA_2000 = {
    "x": "53e91d612ff2bcab38fa1c94d866823a144bf7e48458ac78e3fcfa57957b812d",
    "small": "b2fd061d7235c804a4cbd68438d3e54b217f370f915051c0285a278baaa5e4ca",
    "medium": "a64d340620519da62aa574728e1f467657604d4e216bd87e6ceac951eab248e7",
    "big": "d010ab900dc4c1b859b4a611e6dad87c9d479e73d8c106a9e87198e9e5a9a0c4",
}
# pinned: the reference at N = 2000 (chk, matched rows, digest)
REF_2000 = {
    "q1": ("1808;89927.350025;73080.153265", 1808, 0x3117a7fdb1565ed4),
    "q2": ("1787;89242.792247;86864.344408", 1787, 0x9684a83c950d5c03),
    "q3": ("2000;99969.545467;86864.344408", 1787, 0x4f5f76986515fdc8),
    "q4": ("1787;89242.792247;86864.344408", 1787, 0x9684a83c950d5c03),
    "q5": ("1800;90337.991612;91274.020717", 1800, 0xf704760d6e312519),
}


def digests(jgen, n, seed=108, block=50_000):
    out = {}
    for name, blocks in jgen.tables(n, seed, block):
        h = hashlib.sha256()
        for t in blocks:
            h.update(t.encode("ascii"))
        out[name] = h.hexdigest()
    return out


class Shared(unittest.TestCase):
    """One 2,000-row data set for the class, outside the repository, deleted after, and its reference."""

    N = 2000

    missing = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.jgen, cls.jref = mods()
        except AssertionError as e:      # each test then fails by itself, with the reason (a missing script), instead of the class failing once
            cls.missing = e
            return
        cls.tmp = tempfile.TemporaryDirectory(prefix="named-nb3-")
        cls.paths, cls.meta = cls.jgen.write(cls.N, cls.tmp.name)
        cls.ref = cls.jref.reference(cls.tmp.name, cls.N)

    @classmethod
    def tearDownClass(cls):
        if cls.missing is None:
            cls.tmp.cleanup()

    def setUp(self):
        if self.missing is not None:
            raise self.missing

    def joined(self, qid):
        """The canonical joined rows of a question as lists of cells, computed here by a third, plain way (dicts): x's seven cells, then the right table's others; an unmatched row of a left join has them empty."""
        q = self.jref.JOINS[qid]
        rt = self.paths[q["right"]].read_text().splitlines()
        rh = rt[0].split(",")
        kat = rh.index(q["key"])
        right = {}
        for line in rt[1:]:
            c = line.split(",")
            right[c[kat]] = [v for i, v in enumerate(c) if i != kat]
        rows = []
        xt = self.paths["x"].read_text().splitlines()
        xat = xt[0].split(",").index(q["key"])
        for line in xt[1:]:
            c = line.split(",")
            m = right.get(c[xat])
            if m is not None:
                rows.append(c + m)
            elif q["kind"] == "left":
                rows.append(c + [""] * len(q["right_cols"]))
        return rows

    def text(self, rows, header=None, qid="q1"):
        h = header or self.jref.joined_columns(qid)
        return ("\n".join([",".join(h)] + [",".join(r) for r in rows]) + "\n").encode()

    def check(self, qid, raw, header=None, expect_header=None):
        d = pathlib.Path(self.tmp.name) / "out.csv"
        d.write_bytes(raw)
        acc, why = self.jref.check_output(d, qid, expect_header=expect_header)
        if why is not None:
            return why
        return self.jref.compare(qid, self.ref[qid], acc)


class Generator(Shared):
    def test_sizes(self):
        g = self.jgen
        self.assertEqual(g.sizes(10**7), {"small": 10, "medium": 10_000, "big": 10**7})
        self.assertEqual(g.sizes(10**8), {"small": 100, "medium": 100_000, "big": 10**8})
        self.assertEqual(g.sizes(10**6), {"small": 10, "medium": 1000, "big": 10**6})  # the floor of 10: the development size keeps a small table with a miss
        self.assertEqual(g.sizes(100), {"small": 10, "medium": 10, "big": 100})
        with self.assertRaises(ValueError):
            g.sizes(99)

    def test_same_seed_same_bytes_whatever_the_block(self):
        g = self.jgen
        self.assertEqual(digests(g, 2000), SHA_2000)
        self.assertEqual(digests(g, 2000, block=7), SHA_2000)  # a block is a memory bound, not part of the bytes
        self.assertEqual(digests(g, 2000, block=1999), SHA_2000)
        self.assertNotEqual(digests(g, 2000, seed=109)["x"], SHA_2000["x"])

    def test_the_files_written_are_the_stream(self):
        for t, p in self.paths.items():
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), SHA_2000[t])
            self.assertEqual(self.meta["tables"][t]["sha256"], SHA_2000[t])
            self.assertEqual(self.meta["tables"][t]["bytes"], p.stat().st_size)
        self.assertEqual(self.meta["seed"], 108)
        self.assertIn("NOT the h2oai R generator", self.meta["label"])
        self.assertIn("not comparable", self.meta["label"])

    def test_the_owners_names(self):
        g = self.jgen
        self.assertEqual(g.file_name("x", 10**7), "J1_1e7_NA_0_0.csv")
        self.assertEqual(g.file_name("small", 10**7), "J1_1e7_1e1_0_0.csv")
        self.assertEqual(g.file_name("medium", 10**7), "J1_1e7_1e4_0_0.csv")
        self.assertEqual(g.file_name("big", 10**7), "J1_1e7_1e7_0_0.csv")
        # below 2e4 the small and the medium table have 10 rows each: their names must still be two files
        self.assertNotEqual(g.file_name("small", 2000), g.file_name("medium", 2000))
        self.assertEqual(len({g.file_name(t, 2000) for t in g.TABLES}), 4)
        self.assertEqual(len({g.file_name(t, 10**6) for t in g.TABLES}), 4)

    def test_key_sets(self):
        g = self.jgen
        for n in (10, 11, 99, 100, 1000, 12345):
            rng = g.Rng(7)
            left, right = g.key_sets(rng, n)
            self.assertEqual((len(left), len(right)), (n, n))
            self.assertEqual((len(set(left)), len(set(right))), (n, n))
            self.assertEqual(len(set(left) & set(right)), n - n // 10, n)  # 90 percent common
            self.assertEqual(len(set(left) - set(right)), n // 10)
            self.assertEqual(len(set(right) - set(left)), n // 10)
            self.assertTrue(set(left) | set(right) <= set(range(1, n + n // 10 + 1)))
            self.assertEqual(len(set(left) | set(right)), n + n // 10)

    def test_sample_all_draws_every_key_at_least_once(self):
        g = self.jgen
        rng = g.Rng(3)
        keys = list(range(100, 130))
        col = g.sample_all(rng, keys, 500)
        self.assertEqual(len(col), 500)
        self.assertEqual(set(col), set(keys))
        col = g.sample_all(rng, keys, 30)
        self.assertEqual(sorted(col), keys)  # as many rows as keys: a permutation (id3 is unique)
        self.assertNotEqual(list(col), keys)

    def test_the_shuffle_is_a_permutation_and_moves_things(self):
        g = self.jgen
        a = list(range(50))
        g.Rng(5).shuffle(a)
        self.assertEqual(sorted(a), list(range(50)))
        self.assertNotEqual(a, list(range(50)))

    def test_splitmix64_reference_values(self):
        g = self.jgen
        rng = g.Rng(0)
        self.assertEqual([rng.next() for _ in range(3)], [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F])  # Vigna's reference outputs from state 0

    def test_the_files_are_the_documented_layout(self):
        self.assertEqual(self.jref.validate_layout(self.tmp.name, self.N), [])

    def test_the_layout_check_catches_each_defect(self):
        """Every mutation of a file is caught, so the check can fail."""
        g, r = self.jgen, self.jref

        def mutate(table, fn):
            with tempfile.TemporaryDirectory(prefix="named-nb3-mut-") as d:
                for t, p in self.paths.items():
                    (pathlib.Path(d) / p.name).write_bytes(p.read_bytes())
                target = pathlib.Path(d) / self.paths[table].name
                lines = target.read_text().splitlines(keepends=True)
                target.write_text("".join(fn(lines)))
                return r.validate_layout(d, self.N)

        def set_cell(i, col, value):
            def fn(lines):
                c = lines[i].rstrip("\n").split(",")
                c[col] = value
                lines[i] = ",".join(c) + "\n"
                return lines
            return fn

        cases = {
            "header renamed": ("x", lambda l: ["id1,id2,id3,id4,id5,id6,v\n"] + l[1:]),
            "a row dropped from x": ("x", lambda l: l[:5] + l[6:]),
            "a row added to big": ("big", lambda l: l + [l[1]]),
            "a right key twice (small)": ("small", lambda l: l[:2] + [l[1]] + l[3:]),
            "a right key twice (big)": ("big", lambda l: l[:3] + [l[2]] + l[4:]),
            "a key outside the sets": ("x", set_cell(3, 0, "99999")),
            "id4 not id + id1": ("x", set_cell(4, 3, "idX")),
            "a v1 with 7 decimals": ("x", set_cell(2, 6, "1.1234567")),
            "a v1 with a trailing zero": ("x", set_cell(2, 6, "1.50")),
            "a v2 of 100": ("big", set_cell(2, 6, "100")),
            "a ragged row": ("medium", lambda l: l[:2] + [l[2].rstrip("\n") + ",1\n"] + l[3:]),
            "the last newline missing": ("small", lambda l: l[:-1] + [l[-1].rstrip("\n")]),
            "an id1 of medium outside small's right set": ("medium", set_cell(2, 0, "88888")),
            "id3 not unique in x": ("x", set_cell(2, 2, self.paths["x"].read_text().splitlines()[3].split(",")[2])),
        }
        for name, (table, fn) in cases.items():
            with self.subTest(name):
                self.assertTrue(mutate(table, fn), "the layout check accepted: %s" % name)

    def test_the_estimate_is_arithmetic_and_a_little_over(self):
        g = self.jgen
        total = sum(p.stat().st_size for p in self.paths.values())
        e = g.estimate_bytes(self.N)
        self.assertGreaterEqual(e, total)
        self.assertLess(e, 1.4 * total)
        self.assertGreater(g.estimate_bytes(2 * self.N), 1.9 * e)

    def test_sorted_variant_is_sorted_bytewise_stable_and_the_same_rows(self):
        g = self.jgen
        with tempfile.TemporaryDirectory(prefix="named-nb3-sort-") as d:
            for col, table in (("id1", "x"), ("id5", "x"), ("id3", "big"), ("id2", "medium")):
                dest = g.sorted_variant(self.paths[table], col, d)
                src = self.paths[table].read_text().splitlines()
                got = dest.read_text().splitlines()
                at = src[0].split(",").index(col)
                self.assertEqual(got[0], src[0])
                self.assertEqual(sorted(got[1:]), sorted(src[1:]))
                keys = [l.split(",")[at] for l in got[1:]]
                self.assertEqual(keys, sorted(keys))  # bytewise on the text: "10" before "9"
                # stable: rows of one key keep their order of the source
                for k in set(keys):
                    self.assertEqual([l for l in got[1:] if l.split(",")[at] == k], [l for l in src[1:] if l.split(",")[at] == k])
            keys = [l.split(",")[0] for l in g.sorted_variant(self.paths["x"], "id1", d).read_text().splitlines()[1:]]
            self.assertTrue(any(a == "10" for a in keys) and keys.index("10") < keys.index("2") if "2" in keys and "10" in keys else True)

    def test_a_directory_inside_the_repository_is_refused(self):
        with self.assertRaises(SystemExit):
            self.jgen.write(1000, ROOT / "build" / "named-nb3-test")
        self.assertFalse((ROOT / "build" / "named-nb3-test").exists())

    def test_a_nearly_full_disk_is_refused_unless_forced(self):
        import types
        g = self.jgen
        saved = g.shutil.disk_usage
        try:
            need = g.estimate_bytes(1000)
            g.shutil.disk_usage = lambda p: types.SimpleNamespace(free=3 * need - 1)
            with tempfile.TemporaryDirectory(prefix="named-nb3-disk-") as d:
                with self.assertRaises(SystemExit):
                    g.write(1000, d)
                g.shutil.disk_usage = lambda p: types.SimpleNamespace(free=3 * need)
                paths, meta = g.write(1000, d)
                self.assertEqual(len(paths), 4)
                g.shutil.disk_usage = lambda p: types.SimpleNamespace(free=0)
                g.write(1000, d, force=True)
        finally:
            g.shutil.disk_usage = saved

    def test_the_generator_runs_as_a_script(self):
        with tempfile.TemporaryDirectory(prefix="named-nb3-cli-") as d:
            p = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench_named_join_gen.py"), "--rows", "2e3", "--out", d], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("NOT the h2oai R generator", p.stdout)
            self.assertIn(SHA_2000["x"], p.stdout)
            p = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench_named_join_gen.py"), "--rows", "50", "--out", d], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)


class Reference(Shared):
    def test_the_digest_ties_a_row_together(self):
        r = self.jref
        base = (5, 6, 7, 1000, 2000, 1)
        d = r.digest(*base)
        self.assertEqual(d, r.digest(*base))
        for i in range(6):
            other = list(base)
            other[i] += 1
            self.assertNotEqual(d, r.digest(*other), i)
        # the sum of the digests is not the sum of the parts: swapping the v2 of two rows keeps every total and changes the fingerprint
        a, b = Acc2(r), Acc2(r)
        a.add(1, 1, 1, 10, 20, 1), a.add(2, 2, 2, 30, 40, 1)
        b.add(1, 1, 1, 10, 40, 1), b.add(2, 2, 2, 30, 20, 1)
        self.assertEqual((a.acc.count, a.acc.sum_v1, a.acc.sum_v2), (b.acc.count, b.acc.sum_v1, b.acc.sum_v2))
        self.assertNotEqual(a.acc.digest, b.acc.digest)
        # and it is a sum: order-free
        c = Acc2(r)
        c.add(2, 2, 2, 30, 40, 1), c.add(1, 1, 1, 10, 20, 1)
        self.assertEqual(a.acc.digest, c.acc.digest)

    def test_to_millionths(self):
        f = self.jref.to_millionths
        self.assertEqual(f("97.861311"), 97861311)
        self.assertEqual(f("5"), 5_000_000)
        self.assertEqual(f("0"), 0)
        self.assertEqual(f("5.5"), 5_500_000)
        self.assertEqual(f("5e-06"), 5)
        self.assertEqual(f("5.500000"), 5_500_000)
        self.assertEqual(f("99.999999"), 99_999_999)
        for bad in ("100", "100.0", "-1", "1.1234567", "nan", "inf", "", "abc", "1e999999999", "9" * 50, "0x10", "1,5", " 5"):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    f(bad)

    def test_the_reference_at_2000_rows_is_pinned(self):
        for qid, (chk, matched, dig) in REF_2000.items():
            acc = self.ref[qid]
            self.assertEqual((acc.chk(), acc.matched, acc.digest), (chk, matched, dig), qid)

    def test_the_shape_of_the_answers(self):
        # q1 and q2 and q5 are inner (every row matched); q3 is the left join: all 2000 rows, 1787 matched; q2 and q4 are one answer (id5 is 'id' + id2)
        r = self.ref
        self.assertEqual([r[q].count for q in ("q1", "q2", "q3", "q4", "q5")], [1808, 1787, 2000, 1787, 1800])
        self.assertEqual(r["q2"].as_dict(), r["q4"].as_dict())
        self.assertEqual(r["q3"].matched, r["q2"].count)
        self.assertEqual(r["q3"].sum_v2, r["q2"].sum_v2)
        self.assertEqual(r["q5"].count, 1800)  # 90 percent of the 2000 distinct id3
        self.assertGreater(r["q3"].sum_v1, r["q2"].sum_v1)  # the unmatched rows add their v1

    def test_sqlite_is_a_second_path_to_the_same_answers(self):
        other = self.jref.reference_sqlite(self.tmp.name, self.N)
        for qid in self.ref:
            self.assertIsNone(self.jref.compare(qid, self.ref[qid], other[qid]), qid)

    def test_the_reference_is_a_function_of_the_files_alone(self):
        with tempfile.TemporaryDirectory(prefix="named-nb3-other-") as d:
            self.jgen.write(self.N, d, seed=109)
            other = self.jref.reference(d, self.N)
        for qid in self.ref:
            self.assertNotEqual(other[qid].digest, self.ref[qid].digest, qid)

    def test_a_tiny_set_by_hand(self):
        """3 left rows, a right table with 2 keys: the answers worked out on paper (the reference is not tested against itself)."""
        r, g = self.jref, self.jgen
        with tempfile.TemporaryDirectory(prefix="named-nb3-hand-") as d:
            n = 100
            names = {t: pathlib.Path(d) / g.file_name(t, n) for t in g.TABLES}
            names["x"].write_text("id1,id2,id3,id4,id5,id6,v1\n1,1,1,id1,id1,id1,1.5\n2,1,2,id2,id1,id2,2\n3,2,3,id3,id2,id3,0.25\n")
            names["small"].write_text("id1,id4,v2\n1,id1,10\n3,id3,20.5\n")
            names["medium"].write_text("id1,id2,id4,id5,v2\n1,1,id1,id1,4\n2,9,id2,id9,8\n")
            names["big"].write_text("id1,id2,id3,id4,id5,id6,v2\n1,1,3,id1,id1,id3,0.000001\n")
            acc = r.reference(d, n)
        self.assertEqual((acc["q1"].count, acc["q1"].sum_v1, acc["q1"].sum_v2), (2, 1_750_000, 30_500_000))   # rows 1 and 3 match
        self.assertEqual((acc["q2"].count, acc["q2"].sum_v1, acc["q2"].sum_v2), (2, 3_500_000, 8_000_000))    # id2 = 1 twice: both match medium's id2=1 row (v2 = 4)
        self.assertEqual((acc["q3"].count, acc["q3"].sum_v1, acc["q3"].sum_v2, acc["q3"].matched), (3, 3_750_000, 8_000_000, 2))
        self.assertEqual((acc["q4"].count, acc["q4"].sum_v2), (2, 8_000_000))
        self.assertEqual((acc["q5"].count, acc["q5"].sum_v1, acc["q5"].sum_v2), (1, 250_000, 1))

    def test_a_right_key_that_is_not_unique_is_refused(self):
        r, g = self.jref, self.jgen
        with tempfile.TemporaryDirectory(prefix="named-nb3-dup-") as d:
            for t, p in self.paths.items():
                (pathlib.Path(d) / p.name).write_bytes(p.read_bytes())
            small = pathlib.Path(d) / self.paths["small"].name
            lines = small.read_text().splitlines(keepends=True)
            small.write_text("".join(lines + [lines[1]]))
            with self.assertRaises(ValueError):
                r.reference(d, self.N)


class Acc2:
    def __init__(self, jref):
        self.acc = jref.Acc()

    def add(self, *a):
        self.acc.add(*a)


class Output(Shared):
    """check_output and compare: a contender's joined rows against the fingerprint, and each way to be wrong."""

    def test_the_canonical_rows_pass_every_question(self):
        for qid in self.jref.JOINS:
            with self.subTest(qid):
                raw = self.text(self.joined(qid), qid=qid)
                self.assertIsNone(self.check(qid, raw), qid)
                self.assertIsNone(self.check(qid, raw, expect_header=self.jref.joined_columns(qid)))

    def test_the_columns_of_table_are_x_then_the_right_tables_with_a_prefix(self):
        c = self.jref.joined_columns
        self.assertEqual(c("q1"), ["id1", "id2", "id3", "id4", "id5", "id6", "v1", "small_id4", "small_v2"])
        self.assertEqual(c("q3"), ["id1", "id2", "id3", "id4", "id5", "id6", "v1", "medium_id1", "medium_id4", "medium_id5", "medium_v2"])
        self.assertEqual(len(c("q5")), 13)

    def test_rows_in_another_order_are_the_same_answer(self):
        rows = self.joined("q2")
        random.Random(1).shuffle(rows)
        self.assertIsNone(self.check("q2", self.text(rows, qid="q2")))

    def test_one_value_off(self):
        rows = self.joined("q1")
        for col, value in ((-1, "1.5"), (6, "1.5"), (0, "9"), (2, "424242"), (7 - 1 + 1, "id9")):
            r = [list(x) for x in rows]
            r[10][col] = value
            with self.subTest(col=col):
                self.assertIsNotNone(self.check("q1", self.text(r, qid="q1")))

    def test_the_last_digit_of_one_v2_is_wrong(self):
        r = [list(x) for x in self.joined("q5")]
        v = r[7][-1]
        r[7][-1] = v[:-1] + str((int(v[-1]) + 1) % 10) if v[-1].isdigit() else v + "1"
        why = self.check("q5", self.text(r, qid="q5"))
        self.assertIsNotNone(why)

    def test_the_v2_of_two_rows_swapped_has_the_same_totals_and_is_caught(self):
        rows = [list(x) for x in self.joined("q5")]
        i, j = 3, 11
        self.assertNotEqual(rows[i][-1], rows[j][-1])
        rows[i][-1], rows[j][-1] = rows[j][-1], rows[i][-1]
        d = pathlib.Path(self.tmp.name) / "swap.csv"
        d.write_bytes(self.text(rows, qid="q5"))
        acc, why = self.jref.check_output(d, "q5")
        self.assertIsNone(why)
        want = self.ref["q5"]
        self.assertEqual((acc.count, acc.sum_v1, acc.sum_v2), (want.count, want.sum_v1, want.sum_v2))  # the owner's chk cannot see it
        self.assertIn("digest", self.jref.compare("q5", want, acc))                                   # the fingerprint does

    def test_a_row_missing_doubled_or_extra(self):
        rows = self.joined("q2")
        for name, variant in (("missing", rows[:-1]), ("first missing", rows[1:]), ("doubled", rows + [rows[4]]), ("replaced by a copy", rows[:3] + [rows[2]] + rows[4:]),
                              ("a row of x with no match", rows + [["9999", "1", "1", "id9999", "id1", "id1", "5", "1", "id1", "id1", "5"][:7] + rows[0][7:]])):
            with self.subTest(name):
                self.assertIsNotNone(self.check("q2", self.text(variant, qid="q2")))

    def test_one_trailing_byte(self):
        good = self.text(self.joined("q1"), qid="q1")
        for name, raw in (("newline", good + b"\n"), ("space", good + b" "), ("nul", good + b"\0"), ("letter", good + b"x"), ("no final newline", good[:-1]),
                          ("crlf", good.replace(b"\n", b"\r\n")), ("blank line inside", good.replace(b"\n", b"\n\n", 3)), ("bom", b"\xef\xbb\xbf" + good),
                          ("space in a cell", good.replace(b",", b" ,", 40)), ("empty", b""), ("only a newline", b"\n"), ("header only", good.split(b"\n")[0] + b"\n")):
            with self.subTest(name):
                self.assertIsNotNone(self.check("q1", raw))

    def test_the_header(self):
        rows = self.joined("q1")
        cols = self.jref.joined_columns("q1")
        self.assertIsNone(self.check("q1", self.text(rows, header=["a"] * 9, qid="q1")))                       # a double engine's names are its own: the width is what counts
        self.assertIsNotNone(self.check("q1", self.text(rows, header=["a"] * 9, qid="q1"), expect_header=cols))  # table's are exact
        self.assertIsNotNone(self.check("q1", self.text(rows, header=cols + ["extra"], qid="q1")))
        self.assertIsNotNone(self.check("q1", self.text([r[:-1] for r in rows], header=cols[:-1], qid="q1")))
        self.assertIsNotNone(self.check("q1", self.text(rows, header=["a", "", "c"] + ["d"] * 6, qid="q1")))
        self.assertIsNotNone(self.check("q1", self.text(rows, header=[" a"] + ["d"] * 8, qid="q1")))

    def test_an_empty_v2_is_an_unmatched_row_only_in_a_left_join(self):
        rows = self.joined("q3")
        unmatched = [i for i, r in enumerate(rows) if r[-1] == ""]
        self.assertTrue(unmatched)
        self.assertIsNone(self.check("q3", self.text(rows, qid="q3")))
        # the same answer with the unmatched rows dropped is the inner join's, and a wrong answer to the left join
        self.assertIsNotNone(self.check("q3", self.text([r for r in rows if r[-1] != ""], qid="q3")))
        inner = [list(x) for x in self.joined("q2")]
        inner[0][-1] = ""
        self.assertIsNotNone(self.check("q2", self.text(inner, qid="q2")))
        # a left join whose unmatched row has a v2 of 0 is not unmatched
        z = [list(x) for x in rows]
        z[unmatched[0]][-1] = "0"
        self.assertIsNotNone(self.check("q3", self.text(z, qid="q3")))

    def test_an_unmatched_row_has_every_right_cell_empty(self):
        rows = [list(x) for x in self.joined("q3")]
        i = next(i for i, r in enumerate(rows) if r[-1] == "")
        rows[i][7] = "5"       # a right cell filled, v2 empty
        self.assertIsNotNone(self.check("q3", self.text(rows, qid="q3")))

    def test_an_engine_that_writes_the_right_ids_of_a_left_join_as_floats_is_not_wrong(self):
        # pandas gives a column that has NULLs the type float: medium_id1 comes out 5.0. The row is the same row.
        rows = [list(x) for x in self.joined("q3")]
        for r in rows:
            if r[-1] != "":
                r[7] = r[7] + ".0"
        self.assertIsNone(self.check("q3", self.text(rows, qid="q3")))
        # but the copy must still agree with it
        bad = [list(r) for r in rows]
        j = next(i for i, r in enumerate(bad) if r[-1] != "")
        bad[j][8] = "id9999"
        self.assertIsNotNone(self.check("q3", self.text(bad, qid="q3")))

    def test_spellings_of_a_number_that_are_the_same_number(self):
        rows = [list(x) for x in self.joined("q1")]
        v = rows[5][-1]
        for spelled in (v + "0", v + "00", "%se0" % v):
            r = [list(x) for x in rows]
            r[5][-1] = spelled
            self.assertIsNone(self.check("q1", self.text(r, qid="q1")), spelled)
        # an integer id spelled 7.0, 07 or +7 is not an id
        for bad in ("7.0", "+7", "-7", "7e0", "", "x", "٣"):
            r = [list(x) for x in rows]
            r[5][0] = bad
            self.assertIsNotNone(self.check("q1", self.text(r, qid="q1")), bad)

    def test_unparseable_outputs_never_raise(self):
        rng = random.Random(1234)
        good = self.text(self.joined("q1"), qid="q1")
        for i in range(300):
            raw = bytearray(good)
            for _ in range(rng.randrange(1, 4)):
                at = rng.randrange(len(raw))
                op = rng.choice(("flip", "delete", "insert", "cut"))
                if op == "flip":
                    raw[at] = rng.randrange(256)
                elif op == "delete":
                    del raw[at]
                elif op == "insert":
                    raw.insert(at, rng.choice(b'",\n\r 0e.-+x\xff\0'))
                else:
                    raw = raw[:at]
                if not raw:
                    raw = bytearray(b"x")
            why = self.check("q1", bytes(raw))
            self.assertTrue(why is None or isinstance(why, str))
        for n in (0, 1, 5, 300, 3000):
            self.assertIsNotNone(self.check("q1", bytes(rng.randrange(256) for _ in range(n)) or b"x"))

    def test_compare_names_the_field(self):
        r = self.jref
        a, b = r.Acc(), r.Acc()
        a.add(1, 1, 1, 5, 6, 1)
        b.add(1, 1, 1, 5, 6, 1)
        self.assertIsNone(r.compare("q1", a, b))
        for field, value in (("count", 2), ("sum_v1", 9), ("sum_v2", 9), ("matched", 0), ("digest", 12345)):
            c = copy.copy(b) if False else r.Acc()
            c.add(1, 1, 1, 5, 6, 1)
            setattr(c, field, value)
            self.assertIn(field, r.compare("q1", a, c))


class TableGate(Shared):
    """`table`'s five questions, flag form, --query form and the probe on standard input, equal the reference (gate d)."""

    def test_the_gate_passes_on_the_binary(self):
        self.assertEqual(self.jref.gate(self.tmp.name, self.N, self.ref, binary=TABLE), [])

    def test_the_gate_can_fail(self):
        r = self.jref
        qs = r.load_questions()

        def with_flags(qid, fn):
            m = copy.deepcopy(qs)
            for q in m["questions"]:
                if q["id"] == qid:
                    q["flags"] = fn(q["flags"])
            return m

        # a left join asked as an inner join; the wrong key; a prefix that collides; the wrong right table; a bound that refuses
        cases = {
            "q3 as an inner join": with_flags("q3", lambda f: [a for a in f if a not in ("--join-type", "left")]),
            "q1 on the wrong key": with_flags("q1", lambda f: ["id2" if a == "id1" else a for a in f]),
            "q2 without a prefix": with_flags("q2", lambda f: [a for a in f if a not in ("--join-prefix", "medium_")]),
            "q4 against the small table": with_flags("q4", lambda f: ["{SMALL}" if a == "{MEDIUM}" else a for a in f]),
            "q5 under a bound it passes": with_flags("q5", lambda f: ["1000" if a == "1073741824" and f[f.index(a) - 1] == "--max-state-bytes" else a for a in f]),
        }
        for name, m in cases.items():
            with self.subTest(name):
                self.assertTrue(r.gate(self.tmp.name, self.N, self.ref, questions=m, binary=TABLE), "the gate accepted: %s" % name)

    def test_the_gate_catches_a_wrong_query_form(self):
        r = self.jref
        m = copy.deepcopy(r.load_questions())
        for q in m["questions"]:
            if q["id"] == "q2":
                q["query"] = [a.replace("left ", "").replace("a.id2 = b.id2", "a.id3 = b.id2") for a in q["query"]]
        problems = r.gate(self.tmp.name, self.N, self.ref, questions=m, binary=TABLE)
        self.assertTrue([p for p in problems if p.startswith("q2 query form")], problems)

    def test_join_file_on_standard_input_is_refused(self):
        p = subprocess.run([TABLE, "--join", "-", "--on", "id1", str(self.paths["x"])], capture_output=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"args.bad-value", p.stdout)

    def test_the_questions_file(self):
        qs = self.jref.load_questions()["questions"]
        self.assertEqual([q["id"] for q in qs], ["q1", "q2", "q3", "q4", "q5"])
        for q in qs:
            self.assertEqual(q["kind"], self.jref.JOINS[q["id"]]["kind"])
            self.assertIn("--join-prefix", q["flags"])
            self.assertEqual("--join-type" in q["flags"], q["kind"] == "left")
            self.assertIn("--max-join-rows", q["flags"])
            self.assertEqual("--max-state-bytes" in q["flags"], q["id"] == "q5")
            self.assertIn("--query", q["query"])
        self.assertEqual({q["id"]: q["class"] for q in qs}, {"q1": "E", "q2": "E", "q3": "E", "q4": "E", "q5": "B"})


if __name__ == "__main__":
    unittest.main()
