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


def cases(n, seed):
    rng = random.Random(seed)
    out = []
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


def check(cs, probe):
    got, err = run(probe, cs)
    if got is None:
        return [("probe failed", err)]
    bad = []
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


main()
