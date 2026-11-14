#!/usr/bin/env python3
"""G-CORE of docs/computed-columns.md: the 128-bit core of tools/table/cmpv.cho against Python's integers, which share no code with it.

    python3 scripts/cmpv_golden.py [--cases N] [--seed S]        # build the probe from the sources, run N random cases and the edges
    python3 scripts/cmpv_golden.py --mutants                     # every mutant of cmpv.cho must make the golden fail (exit 0 only if all are killed)

The expected text is computed here: add and sub at the larger scale, mul at the sum of scales, div rounded half to even at scale n; a result of
2^127 or more is `!`, a zero divisor is `/0`. A value is written with exactly its scale's fractional digits and `-` only when it is not zero.
"""
import argparse, pathlib, random, shutil, sys, tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import mutlib  # the shared half of the mutation scripts: `check` says every pattern occurs exactly once

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIM = 2**127


def text(v, scale):
    s = str(abs(v)).rjust(scale + 1, "0")
    body = s if scale == 0 else s[:-scale] + "." + s[-scale:]
    return ("-" if v < 0 else "") + body


def expect(op, a, sa, b, sb, n):
    a = int(str(a).lstrip("="))
    b = int(str(b).lstrip("="))
    if op in "AS":
        sc = max(sa, sb)
        x, y = a * 10 ** (sc - sa), b * 10 ** (sc - sb)
        r = x + y if op == "A" else x - y
        return "!" if abs(r) >= LIM or abs(x) >= 2**250 else text(r, sc)
    if op == "M":
        r = a * b
        return "!" if abs(r) >= LIM else text(r, sa + sb)
    if b == 0:
        return "/0"
    e = n + sb - sa
    num, den = (abs(a) * 10 ** e, abs(b)) if e >= 0 else (abs(a), abs(b) * 10 ** -e)
    q, r = divmod(num, den)
    if 2 * r > den or (2 * r == den and q % 2 == 1):
        q += 1
    if q >= LIM:
        return "!"
    return text(-q if (a < 0) != (b < 0) and q else q, n)


def operand(rng):
    k = rng.random()
    if k < 0.15:
        return rng.choice([0, 1, -1, 10**18, -(10**18), 2**63 - 1, -(2**63), LIM - 1, -(LIM - 1), 10**18 - 1, 5, -5, 2**64])
    d = rng.choice([1, 3, 9, 18, 19, 30, 38])
    return rng.randint(-(10**d) + 1, 10**d - 1)


# Pairs (a, b) for which Knuth's algorithm D (base 2^30) must add back: the estimate of a quotient limb is one too large after the two-limb test and the subtraction goes negative.
# Random operands reach that about once in 2^29 digits, so a division with no add-back would pass a million of them; these were found by running a model of the algorithm over operands
# whose limbs are 0, 1, 2, 3, 2^28, 2^29 - 1, 2^29, 2^29 + 1, 2^30 - 3, 2^30 - 2 and 2^30 - 1: `python3 scripts/cmpv_addback.py --check` re-runs that search and fails if the list is not what it finds.
ADD_BACK = [[2475880078570760548724506626, 2305843011361177603], [618970020219150890289856511, 618970020219150890826727421], [1329227993928005815128658153075376127, 1237940038708919523669442558], [664613996654517900625287766914105345, 618970019642690140939223040], [1329227993928005815128658153612247040, 1329227993928005815705118905915670529], [664613998511427955518132916360052734, 618970020795611643130150909], [664613999749367995379973944636342273, 664613999749367995956434695866023934], [664613997892457937028364282712031232, 618970020219150891363598336], [664613997892457934146060522000220157, 664613997892457934722521272156160003], [3458764517041766400, 1729382258520883201], [1329227993309035796638889519695790082, 664613996654517898319444761458507776], [618970020795611640982667265, 618970020795611640982667266], [1329227992380580765445472057684918274, 1237940036114846138304036862], [1329227995784915870597964048382296065, 1152921506754330627], [1329227994546975831889044528470949889, 1237940039285380273825382397], [1329227993928005815128658153612247040, 1329227993928005815128658154685988866], [664613997892457939910668045034455039, 618970019642690140670787585], [664613998511427957823975925573746691, 332306999255713979200218336254230530], [1329227993928005813975736645784174592, 618970019930920514675015678], [2658455990331891706522233843514081277, 2305843010287435777], [332307000184169004629028280599576574, 618970018489768633916456959], [1329227994546975831312583780462493695, 2305843011361177602], [4652297985247205555163324712591818751, 664613997892457937028364281638289408], [1329227992690065775843277876565639171, 1237940037555998017988853757], [664613997892457935298982027680808962, 1237940039285380273825382397], [3987683989211657677639491593189720065, 1856910060657452670869569536], [664613999130397974007901548665896961, 618970020795611640445796353], [1329227995784915872327346306903179266, 1237940039285380274899124223], [618970020795611639908925442, 618970020795611640445796352], [664613999130397972278519292292497408, 664613999130397972854980043522179073], [1329227995165945854414038426363887614, 618970019642690137986433024], [664613999130397974007901548129026048, 332306999565198987003950775138254845], [664613996654517897166523258462273538, 618970019066229385146138621], [1329227993309035794333046512629579775, 1237940038132458769218535427], [1329227993309035798368271777679802365, 618970019642690139597045759], [332306998946228969378873269676933123, 618970019642690139597045761], [1329227995165945853261116925515137025, 1329227995165945853261116927125749759], [1237940037555998019599466495, 1237940037555998020136337406], [618970021372072395433574402, 618970021372072395702009856], [1329227995165945854414038429048242177, 1237940038708919524206313473]]


def cases(n, seed):
    rng = random.Random(seed)
    out = []
    for a, b in ADD_BACK:
        out.append(("D", a, 0, b, 0, 0))
        out.append(("D", -a, 0, b, 0, 0))
        out.append(("D", a, 0, b, 0, 5))    # the same quotient at 5 fractional digits: the dividend is scaled by 10^5 first
        out.append(("D", a, 3, b, 0, 2))
    # operands that go through set_int as an int (written `=v`), the smallest and largest included
    for v in (-(2**63), 2**63 - 1, -(2**63) + 1, 0, 1, -1):
        for op in "AMS":
            out.append((op, "=%d" % v, 0, "=%d" % v, 3, 0))
    for _ in range(n):
        op = rng.choice("ASMDD")
        a, b = operand(rng), operand(rng)
        sa, sb, nn = rng.randint(0, 18), rng.randint(0, 18), rng.randint(0, 18)
        if op == "D" and rng.random() < 0.3:  # ties: a / b with a = (2k+1) * b / 2 style
            b = rng.choice([2, 4, 8, 10, 1000, 6]) * rng.choice([1, -1])
            a = (2 * rng.randint(-50, 50) + 1) * (abs(b) // 2 or 1) * rng.choice([1, -1])
            sa = sb = rng.randint(0, 3)
            nn = rng.randint(0, 2)
        if op in "AS" and rng.random() < 0.3:
            a = rng.choice([LIM - 1, -(LIM - 1)])
            b = rng.choice([1, -1, 10 ** rng.randint(0, 17)])
            sa = sb = rng.randint(0, 2)
        if op == "M" and rng.random() < 0.3:
            a = rng.randint(2**60, 2**64)
            b = rng.randint(2**60, 2**64)
        out.append((op, a, sa, b, sb, nn))
    return out


def build(src_dir, work, extra=None):
    proj = pathlib.Path(work)
    (proj / "src").mkdir()
    cmpv = (ROOT / "tools/table/cmpv.cho").read_text()
    if extra:
        old, new = extra
        cmpv = cmpv.replace(old, new)
    (proj / "src/cmpv.cho").write_text(cmpv)
    (proj / "src/main.cho").write_text((ROOT / "tests/conformance/cmpv_probe/main.cho").read_text().replace("@MODULE@", "cmpv"))
    (proj / "cancho.toml").write_text('[package]\nname = "cmpvprobe"\ncancho = "119bcfe9b985ef5ebd9dafc3ff3b2bbd05f22653"\n\n'
                                      '[[bin]]\nname = "probe"\nsources = ["src"]\nstd = true\nout = "build/probe"\n')
    r = subprocess.run(["cancho", "build", "--ignore-compiler-rev"], cwd=proj, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stdout + r.stderr
    return proj / "build/probe", ""


def run(probe, cs):
    got = []
    for i in range(0, len(cs), 300):
        chunk = cs[i:i + 300]
        argv = [str(probe)]
        for op, a, sa, b, sb, n in chunk:
            argv += [op, str(a), str(sa), str(b), str(sb), str(n)]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 0:
            return None, r.stderr[:300]
        got += r.stdout.split("\n")[:len(chunk)]
    return got, ""


# Divisions whose first quotient-limb estimate is about 2^29 too large unless the divisor was normalized (its top limb is 1, so the estimate is the whole two-limb dividend): a division
# without the normalization still gets the right answer, after about 2^29 corrections, which is a second and not a millisecond. Each must answer, correctly, inside SLOW_LIMIT seconds.
SLOW_LIMIT = 0.3    # measured 1 to 2 ms with the normalization and 0.9 to 1.0 s without it (2^29 corrections), one run each
B30 = 1 << 30
SLOW = [((B30 - 1) * B30, 2 * B30 - 1), ((B30 - 1) * B30 * B30, 2 * B30 * B30 - 1), ((B30 - 1) * B30 + 12345, 2 * B30 - 1), (10**30, B30 + 5), ((B30 - 1) * B30 ** 3, 2 * B30 ** 3 - 1)]


def check_speed(probe):
    bad = []
    for a, b in SLOW:
        if a >= LIM or b >= LIM:
            continue
        try:
            r = subprocess.run([str(probe), "D", str(a), "0", str(b), "0", "0"], capture_output=True, text=True, timeout=SLOW_LIMIT)
        except subprocess.TimeoutExpired:
            bad.append((("D", a, 0, b, 0, 0), "answered in under %s s" % SLOW_LIMIT, "TIMEOUT"))
            continue
        want = expect("D", a, 0, b, 0, 0)
        if r.stdout.strip() != want:
            bad.append((("D", a, 0, b, 0, 0), want, r.stdout.strip()))
    return bad


def check(cs, probe):
    bad_speed = check_speed(probe)
    got, err = run(probe, cs)
    if got is None:
        return [("probe failed", err)]
    bad = list(bad_speed)
    for c, g in zip(cs, got):
        e = expect(*c)
        if e != g:
            bad.append((c, e, g))
    return bad


MUTANTS = [
    ("carry of the limb sum dropped", "        w[at(d, i)] = t & 1073741823;\n        carry = t >> 30;\n        i = i + 1;\n    }\n    return carry;\n}\n\n// d = |a| - |b|",
     "        w[at(d, i)] = t & 1073741823;\n        carry = 0;\n        i = i + 1;\n    }\n    return carry;\n}\n\n// d = |a| - |b|"),
    ("borrow forgotten", "            t = t + 1073741824;\n            owed = 1;", "            t = t + 1073741824;\n            owed = 0;"),
    ("sub does not flip the sign", "        sign_b = 1 - sign_b;", "        sign_b = sign_b;"),
    ("larger magnitude chosen the wrong way", "        if c >= 0 {\n            mag_sub(w, tmp2(), tmp0(), tmp1());", "        if c < 0 {\n            mag_sub(w, tmp2(), tmp0(), tmp1());"),
    ("scales not aligned (b)", "over = over + mul_pow10(w, tmp1(), scale - sb);", "over = over + 0;"),
    ("the 2^127 bound off by one", "return w[at(s, 4)] < 128;", "return w[at(s, 4)] <= 128;"),
    ("the 2^127 bound dropped", "return w[at(s, 4)] < 128;", "return true;"),
    ("product sign", "    if w[a * 12] != w[b * 12] {\n        sign = 1;\n    }\n    clear(w, tmp2(), scale);", "    clear(w, tmp2(), scale);"),
    ("product scale", "    let scale = w[a * 12 + 1] + w[b * 12 + 1];\n    var sign = 0;", "    let scale = w[a * 12 + 1];\n    var sign = 0;"),
    ("product carry dropped", "                    w[at(tmp2(), k)] = t & 1073741823;\n                    carry = t >> 30;", "                    w[at(tmp2(), k)] = t & 1073741823;\n                    carry = 0;"),
    ("half-even tie rounds up", "        up = w[at(tmp2(), 0)] & 1 == 1;", "        up = true;"),
    ("half-even tie rounds down", "        up = w[at(tmp2(), 0)] & 1 == 1;", "        up = false;"),
    ("remainder above half does not round", "    if c > 0 {\n        up = true;", "    if c > 100 {\n        up = true;"),
    ("zero divisor not seen", "    if mag_is_zero(w, b) {\n        return 1;\n    }", "    if false {\n        return 1;\n    }"),
    ("division exponent on the wrong side", "    if e >= 0 {\n        mul_pow10(w, tmp0(), e);", "    if e > 100 {\n        mul_pow10(w, tmp0(), e);"),
    ("quotient sign", "    var sign = 0;\n    if w[a * 12] != w[b * 12] {\n        sign = 1;\n    }\n    // quotient", "    var sign = 0;\n    // quotient"),
    ("negative zero allowed (put)", "    if w[s * 12] == 1 {\n        o = buffer.push(heap, o, byte_of('-'));", "    if w[s * 12] == 1 || w[at(s, 0)] == 0 {\n        o = buffer.push(heap, o, byte_of('-'));"),
    ("fraction digits not padded", "    while count < scale + 1 {\n        w[base + count] = 0;", "    while count < 1 {\n        w[base + count] = 0;"),
    ("printer drops the point", "    if scale > 0 {\n        o = buffer.push(heap, o, byte_of('.'));", "    if scale > 100 {\n        o = buffer.push(heap, o, byte_of('.'));"),
    ("division: no add-back", "        if negative {\n", "        if false {\n"),
    ("division: the quotient limb is not corrected by the second limb", "            if qhat >= 1073741824 || qhat * w[v0 + nd - 2] > rhat * 1073741824 + w[u0 + j + nd - 2] {", "            if qhat >= 1073741824 {"),
    ("division: the divisor is not normalized", "    while top < 536870912 {", "    while false {"),
    ("division: a one-limb divisor loses its remainder", "            rem = cur - q * dv;", "            rem = 0;"),
    ("division: a dividend below the divisor has no remainder", "            w[at(12, k)] = w[at(tmp0(), k)];", "            w[at(12, k)] = 0;"),
    ("division: the remainder is not shifted back", "(w[u0 + i] >> shift) + (w[u0 + i + 1] & low)", "w[u0 + i] + (w[u0 + i + 1] & low)"),
    ("set_int of the smallest int", "        m = -(v + 1);\n        plus_one = 1;", "        m = -v;\n        plus_one = 0;"),
    ("mul_pow10 overflow flag ignored", "        if carry != 0 {\n            over = 1;\n        }\n        left = left - step;", "        left = left - step;"),
]

# Survivors that are explained and not hidden: the flag of mul_pow10 can only be set by a magnitude of 2^270, and every caller passes one below 2^127 times at
# most 10^36 (2^247), so the flag is a guard that no input reaches; the mutant is equivalent on every reachable input.
EQUIVALENT = {"mul_pow10 overflow flag ignored"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--check", action="store_true", help="only verify that every mutant applies exactly once (no compiler)")
    a = ap.parse_args()
    problems = mutlib.check([(n, "cmpv.cho", o, w) for n, o, w in MUTANTS])
    if problems or a.check:
        print("\n".join(problems) or f"{len(MUTANTS)} mutants: every pattern occurs exactly once")
        sys.exit(1 if problems else 0)
    cs = cases(a.cases if not a.mutants else 6000, a.seed)
    if not a.mutants:
        with tempfile.TemporaryDirectory() as w:
            probe, err = build(ROOT, w)
            if not probe:
                sys.exit("build failed:\n" + err)
            bad = check(cs, probe)
        print(f"{len(cs)} cases, {len(bad)} differences")
        for b in bad[:10]:
            print(" ", b)
        sys.exit(1 if bad else 0)
    survivors = 0
    for name, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as w:
            probe, err = build(ROOT, w, (old, new))
            if not probe:
                print(f"SKIP (does not build) {name}: {err[:200]}")
                survivors += 1
                continue
            bad = check(cs, probe)
        eq = name in EQUIVALENT and not bad
        print(("killed   " if bad else ("equivalent (explained) " if eq else "SURVIVED ")) + name)
        survivors += 0 if (bad or eq) else 1
    print(f"{len(MUTANTS) - survivors}/{len(MUTANTS)} killed")
    sys.exit(1 if survivors else 0)


if __name__ == "__main__":
    main()
