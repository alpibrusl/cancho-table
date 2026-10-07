#!/usr/bin/env python3
"""Mutants of the reader (docs/gap-float.md section 7.3): each one edits tools/table/flt.cho (or pow5_table.py's rule), builds fbench, and runs the checksum over a fixed set of cell files;
a mutant is killed when one checksum differs from reference.py's. Not part of any gate; the proof that the differential can fail.

    mutants.py CELLDIR [--only NAME]      CELLDIR holds c_f17.txt c_bits.txt c_d19.txt c_d25.txt c_exp.txt c_lead.txt c_money.txt c_p10.txt c_near.txt c_mid.txt (gen_cells.py kinds, seed 1)"""
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import reference  # noqa: E402

FILES = ["edge", "f17", "bits", "d19", "d25", "exp", "lead", "money", "p10", "near", "mid"]
M = [
    ("tie rule off", "mant & 3 == 1 {", "mant & 3 == 7 {"),
    ("tie window -4 -> -3", "q >= 0 - 4 && q <= 23", "q >= 0 - 3 && q <= 23"),
    ("tie window 23 -> 22", "q >= 0 - 4 && q <= 23", "q >= 0 - 4 && q <= 22"),
    ("tie: lo <= 1 -> lo <= 0", "lo >= 0 && lo <= 1 &&", "lo >= 0 && lo <= 0 &&"),
    ("second product never", "if hi & 511 == 511 {", "if hi & 511 == 512 {"),
    ("second product at 8 bits", "if hi & 511 == 511 {", "if hi & 255 == 255 {"),
    ("carry > -> >=", "if sh ^ 1 << 63 > nl ^ 1 << 63 {", "if sh ^ 1 << 63 >= nl ^ 1 << 63 {"),
    ("no carry", "hi = wrapping_add(hi, 1);", "hi = hi;"),
    ("power of 2: 217706 -> 217705", "(217706 * q >> 16)", "(217705 * q >> 16)"),
    ("subnormal cutoff 64 -> 63", "if 1 - power2 >= 64 {", "if 1 - power2 >= 63 {"),
    ("subnormal: no round up", "        mant = mant + (mant & 1);\n        return mant >> 1;", "        return mant >> 1;"),
    ("mantissa carry past 2^53", "if mant >= 9007199254740992 {", "if mant >= 9007199254740993 {"),
    ("clz off by one", "    if x >= 0 {\n        n = n + 1;\n    }\n    return n;", "    return n;"),
    ("window + 1 check off", "if dropped && eisel_lemire(", "if false && eisel_lemire("),
    ("dropped integer digit does not raise q", "                if point_at < 0 {\n                    q = q + 1;\n                }", "                if point_at < 0 {\n                    q = q + 0;\n                }"),
    ("Clinger past 2^53", "m <= 9007199254740992 &&", "m <= 9007199254740993 &&"),
    ("Clinger takes 19-digit patterns", "!dropped && m >= 0 && m <=", "!dropped && m <="),
    ("overflow at 2047 -> 2048", "    if power2 >= 2047 {\n        return 9218868437227405312;", "    if power2 >= 2048 {\n        return 9218868437227405312;"),
    ("exact: sticky from division lost", "                    if dec.div_small(a, nl, p) != 0 {\n                        sticky = true;\n                    }", "                    dec.div_small(a, nl, p);"),
    ("exact: sticky of low limbs lost", "                    if a[j] != 0 {\n                        sticky = true;\n                    }", "                    j = j;"),
    ("exact: round half up", "if round_bit == 1 && (low || kept & 1 == 1) {", "if round_bit == 1 {"),
    ("exact: field 2047 -> 2048", "            if field >= 2047 {", "            if field >= 2048 {"),
    ("exact: t one smaller", "k10 * 33220 / 10000 + 66 - bit_length(a, nl)", "k10 * 33220 / 10000 + 60 - bit_length(a, nl)"),
    ("window of 18 digits", "if nd < 19 {", "if nd < 18 {"),
]


# The mutants that survive, and why each is accepted (checked by reading the code and, where a number is given, by running it):
NOTES = {
    "tie: lo <= 1 -> lo <= 0": "equivalent on every input tried: lo_search.py found lo == 1 in none of 2.8 million draws of (q in -4..23, w) at the point where the rule is consulted, and all 31,979 exact halfways among them have lo == 0. "
                              "Argument (see lo_search.py): for an exact halfway the product's dropped part cannot carry into bit 64, so lo is exactly 0. No test can kill it, because no input differs.",
    "second product at 8 bits": "equivalent: the condition is made weaker-or-equal to the real one (every product with 9 low ones has 8), so the second multiplication runs more often and never less; the result is the same, only slower.",
    "subnormal cutoff 64 -> 63": "equivalent: mant has at most 55 bits, so mant >> 63 is 0 and the rounding of 0 is 0, the same as the early return.",
    "Clinger takes 19-digit patterns": "equivalent in effect: a 19-digit m >= 2^63 is negative, float_of(m) is negative, bits_of(x) is negative, and the cell falls to the exact tier (slower, same answer).",
    "overflow at 2047 -> 2048": "equivalent: a result with power2 == 2047 has bits >= 0x7ff0000000000000, which parse_float turns into status 11 exactly as for the early return.",
    "exact: field 2047 -> 2048": "equivalent for the same reason (the clamp after the rounding, and parse_float's test).",
    "exact: t one smaller": "equivalent: the quotient still has at least 58 bits and rounding needs 55 (53 + round + sticky from the remainder flag).",
    "window of 18 digits": "equivalent: a 19-digit cell then has a dropped digit and goes through the m / m + 1 test or the exact tier: correct, slower.",
}


def build(src, out):
    flt0 = out / "flt0.cho"
    flt0.write_text(subprocess.run(["git", "-C", str(ROOT), "show", "origin/main:tools/table/flt.cho"], capture_output=True, check=True).stdout.decode().replace("module flt;", "module flt0;", 1))
    r = subprocess.run(["cancho", "build", str(HERE / "fbench.cho"), str(flt0), str(src), str(ROOT / "tools/table/dec.cho"), str(ROOT / "tools/table/pow5.cho"), "--std", "-o", str(out / "fbench")], capture_output=True)
    return r.returncode == 0, r.stderr.decode()[:300]


def main():
    celldir = pathlib.Path(sys.argv[1])
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    want = {f: reference.checksum(reference.lines_of(celldir / f"c_{f}.txt")) for f in FILES}
    want = {f: v - (1 << 64) if v >> 63 else v for f, v in want.items()}
    text = (ROOT / "tools/table/flt.cho").read_text()
    killed = 0
    total = 0
    for name, old, new in M:
        if only and only != name:
            continue
        total += 1
        if text.count(old) != 1:
            print(f"CANNOT APPLY ({text.count(old)} sites): {name}")
            continue
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "flt.cho").write_text(text.replace(old, new))
            ok, err = build(d / "flt.cho", d)
            if not ok:
                print(f"DOES NOT BUILD: {name}: {err}")
                continue
            bad = []
            for f in FILES:
                got = subprocess.run([str(d / "fbench"), "C", "1"], stdin=open(celldir / f"c_{f}.txt"), capture_output=True)
                if got.returncode != 0 or int(got.stdout.decode().strip() or 0) != want[f]:
                    bad.append(f)
            killed += bool(bad)
            print(f"{'killed  ' if bad else 'SURVIVED'} {name}   ({', '.join(bad)})", flush=True)
            if not bad:
                print("         " + NOTES.get(name, "NO NOTE: unexplained survivor"), flush=True)
    print(f"{killed} of {total} killed")


main()
