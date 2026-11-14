#!/usr/bin/env python3
"""G-ORACLE of docs/computed-columns.md for the compiler and evaluator of tools/table/cexpr.cho, against Python's integers (no code shared with the tool).

    python3 scripts/cexpr_golden.py [--cases N] [--seed S]     # random expressions over random typed rows, and the hand-written refusals
    python3 scripts/cexpr_golden.py --check                    # every mutant applies exactly once (no compiler)
    python3 scripts/cexpr_golden.py --mutants                  # every mutant of cexpr.cho must make the golden fail

The probe (tests/conformance/cexpr_probe/main.cho) prints a value as NAME=text at its scale, `C status offset` for definitions refused before a row is read and
`E status column definition` for a row refused. The random cases compare the value text; the refusals compare the status.
"""
import argparse, pathlib, random, re, shutil, sys, tempfile
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import mutlib  # the shared half of the mutation scripts: `check` says every pattern occurs exactly once

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIM = 2**127
HEADER = "a,b,c,d,e,f"


def text(v, scale):
    s = str(abs(v)).rjust(scale + 1, "0")
    return ("-" if v < 0 else "") + (s if scale == 0 else s[:-scale] + "." + s[-scale:])


# ---- the reference: a tree of Python integers ------------------------------------------------------------------------------------------

class Overflow(Exception):
    pass


class DivZero(Exception):
    pass


class BadScale(Exception):
    pass


def ev(node, row):
    """(value, scale) of a node; raises Overflow, DivZero, BadScale"""
    k = node[0]
    if k == "col":
        _, c, kind, s = node
        return row[c][0] * 10 ** (s - row[c][1]), s
    if k == "lit":
        return node[1], node[2]
    if k == "neg":
        v, s = ev(node[1], row)
        return -v, s
    (a, sa), (b, sb) = ev(node[1], row), ev(node[2], row)
    if k in "+-":
        sc = max(sa, sb)
        x, y = a * 10 ** (sc - sa), b * 10 ** (sc - sb)
        r = x + y if k == "+" else x - y
    elif k == "*":
        if sa + sb > 18:
            raise BadScale()
        sc, r = sa + sb, a * b
    else:
        n = node[3]
        if b == 0:
            raise DivZero()
        e = n + sb - sa
        num, den = (abs(a) * 10 ** e, abs(b)) if e >= 0 else (abs(a), abs(b) * 10 ** -e)
        q, rem = divmod(num, den)
        if 2 * rem > den or (2 * rem == den and q % 2 == 1):
            q += 1
        r, sc = (-q if (a < 0) != (b < 0) else q), n
    if abs(r) >= LIM:
        raise Overflow()
    return r, sc


def static_scale(node):
    k = node[0]
    if k == "col":
        return node[3]
    if k == "lit":
        return node[2]
    if k == "neg":
        return static_scale(node[1])
    a, b = static_scale(node[1]), static_scale(node[2])
    if k == "*":
        if a + b > 18:
            raise BadScale()
        return a + b
    return node[3] if k == "/" else max(a, b)


PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def sp(rng):
    return rng.choice(["", " ", "  "])


def atom(node):
    return node[0] in ("col", "lit")


def render(node, rng):
    k = node[0]
    if k == "col":
        s = f"{'abcdef'[node[1]]}:int" if node[2] == "int" else f"{'abcdef'[node[1]]}:dec({node[3]})"
        return f"#{node[1] + 1}" + s[1:] if rng.random() < 0.15 else s
    if k == "lit":
        return text(node[1], node[2])
    if k == "neg":
        inner = render(node[1], rng)
        return "-" + sp(rng) + (inner if atom(node[1]) or node[1][0] == "neg" else f"({inner})")
    l, r = render(node[1], rng), render(node[2], rng)
    lc, rc = node[1], node[2]
    if k == "/":
        r = r if atom(rc) or rc[0] == "neg" else f"({r})"
        l = f"({l})" if lc[0] in "+-" else l
        return f"{l}{sp(rng)}/{sp(rng)}{r}{sp(rng)}@{node[3]}"
    l = f"({l})" if lc[0] in "+-*/" and PREC[lc[0]] < PREC[k] else l
    r = f"({r})" if rc[0] in "+-*/" and PREC[rc[0]] <= PREC[k] else r
    return f"{l}{sp(rng)}{k}{sp(rng)}{r}"


def paren_depth(s):
    d = m = 0
    for ch in s:
        d += ch == "("
        d -= ch == ")"
        m = max(m, d)
    return m


def gen(rng, depth, cols):
    if depth == 0 or rng.random() < 0.25:
        if rng.random() < 0.2:
            sc = rng.randint(0, 4)
            return ("lit", rng.randint(0, 10 ** rng.randint(1, 6)), sc)
        c = rng.randrange(6)
        kind, s = cols[c]
        return ("col", c, kind, 0 if kind == "int" else s)
    k = rng.choice(["+", "-", "*", "*", "/", "neg"])
    if k == "neg":
        return ("neg", gen(rng, depth - 1, cols))
    if k == "/":
        return ("/", gen(rng, depth - 1, cols), gen(rng, depth - 1, cols), rng.randint(0, 18))
    return (k, gen(rng, depth - 1, cols), gen(rng, depth - 1, cols))


def make_row(rng):
    cols, cells, row = [], [], []
    for _ in range(6):
        if rng.random() < 0.4:
            v = rng.choice([0, 1, -1, rng.randint(-10**rng.randint(1, 18) + 1, 10**rng.randint(1, 18) - 1), 2**62])
            cols.append(("int", 0))
            cells.append(str(v))
            row.append((v, 0))
        else:
            s = rng.randint(1, 8)
            digits = rng.randint(1, 17)
            v = rng.randint(-10**digits + 1, 10**digits - 1)
            cols.append(("dec", s))
            cells.append(text(v, s))
            row.append((v, s))
    return cols, ",".join(cells), row


def lines_of(rng, n):
    out = []
    for _ in range(n):
        cols, cells, row = make_row(rng)
        defs, exp = [], []
        for d in range(rng.randint(1, 3)):
            tree = gen(rng, rng.randint(1, 4), cols)
            try:
                static_scale(tree)
                v, s = ev(tree, row)
                exp.append(("ok", f"r{d}={text(v, s)}"))
            except BadScale:
                exp.append(("C", 7))
            except Overflow:
                exp.append(("E", 12))
            except DivZero:
                exp.append(("E", 11))
            rendered = render(tree, rng)
            if paren_depth(rendered) > 4:
                exp[-1] = ("C", 9)
            defs.append(f"r{d}=" + rendered)
        # the first definition that is refused decides; a compile refusal comes before any row error
        if any(e[0] == "C" for e in exp):
            want = "C %d" % [e[1] for e in exp if e[0] == "C"][0]
        else:
            bad = [i for i, e in enumerate(exp) if e[0] == "E"]
            want = f"E {exp[bad[0]][1]} 0 {bad[0]}" if bad else ";".join(e[1] for e in exp)
        # the order the tool evaluates in is the definition order: values of earlier definitions are not printed on a failure
        out.append((HEADER, cells, ";".join(defs), want))
    return out


HAND = [
    ("a,b,c,d,e,f", "r=z:int+1", "C 2"),
    ("a,b,c,d,e,f", "z:int+1", "C 1"),
    ("a,a,c,d,e,f", "r=a:int+1", "C 3"),
    ("a,b,c,d,e,f", "a=b:int+1", "C 4"),
    ("a,b,c,d,e,f", "r=b:int+1;r=c:int", "C 4"),
    ("a,b,c,d,e,f", "r=a+1", "C 5"),
    ("a,b,c,d,e,f", "r=a:float+1", "C 6"),
    ("a,b,c,d,e,f", "r=a:int/b:int", "C 8"),
    ("a,b,c,d,e,f", "r=a:int/b:int@19", "C 1"),
    ("a,b,c,d,e,f", "r=a:dec(10)*b:dec(10)", "C 7"),
    ("a,b,c,d,e,f", "r=a:int+", "C 1"),
    ("a,b,c,d,e,f", "r=(a:int", "C 1"),
    ("a,b,c,d,e,f", "r=a:int)", "C 1"),
    ("a,b,c,d,e,f", "r=a:int b:int", "C 1"),
    ("a,b,c,d,e,f", "", "C 1"),
    ("a,b,c,d,e,f", "r", "C 1"),
    ("a,b,c,d,e,f", "r=1234567890123456789", "C 1"),
    ("a,b,c,d,e,f", "r=((((((a:int))))))", "C 9"),
    ("a,b,c,d,e,f", "r=a:int/b:int@2*c:int", "r=15.00"),
    ("a,b,c,d,e,f", "r=a:int@2", "C 1"),
    ("a,b,c,d,e,f", "r=#7:int", "C 2"),
    ("a,b,c,d,e,f", "r=#0:int", "C 2"),
]


def hand_cases():
    out = []
    for header, defs, want in HAND:
        cells = "10,20,30,40,50,60"
        out.append((header, cells, defs, want))
    out += [
        (HEADER, "10,0,3,4,5,6", "r=a:int/b:int@2", "E 11 0 0"),
        (HEADER, "x,2,3,4,5,6", "r=a:int+1", "E 101 0 0"),
        (HEADER, "1.5,2,3,4,5,6", "r=a:dec(1)+1", "r=2.5"),
        (HEADER, "1.55,2,3,4,5,6", "r=a:dec(1)+1", "E 203 0 0"),
        (HEADER, "9223372036854775808,2,3,4,5,6", "r=a:int+1", "E 102 0 0"),
        (HEADER, "1,2,3,4,5,6", "r=1+2*3", "r=7"),
        (HEADER, "1,2,3,4,5,6", "r=(1+2)*3", "r=9"),
        (HEADER, "1,2,3,4,5,6", "r=10-2-3", "r=5"),
        (HEADER, "1,2,3,4,5,6", "r=-a:int*-b:int", "r=2"),
        (HEADER, "1,2,3,4,5,6", "r=-(a:int+b:int)", "r=-3"),
        (HEADER, "1,2,3,4,5,6", "r=7/2@0", "r=4"),
        (HEADER, "1,2,3,4,5,6", "r=5/2@0", "r=2"),
        (HEADER, "1,2,3,4,5,6", "r=1/3@4", "r=0.3333"),
        (HEADER, "1,2,3,4,5,6", "r=1.25*2", "r=2.50"),
        (HEADER, "1,2,3,4,5,6", "x=a:int+1;y=b:int*2", "x=2;y=4"),
        (HEADER, '"1",2,3,4,5,6', "r=a:int+1", "r=2"),
        ("a b,b,c,d,e,f", "7,2,3,4,5,6", "r='a b':int+1", "r=8"),
        ("a b,b,c,d,e,f", "7,2,3,4,5,6", "'out one'=\\a\\ b:int+1", "out one=8"),
        ("a,b,c,d,e,f", "7,2,3,4,5,6", "r=#1:int+#2:int", "r=9"),
    ]
    return out


# ---- the probe -------------------------------------------------------------------------------------------------------------------------

def closure(module):
    seen, todo = [], [module]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.append(m)
        src = (ROOT / "tools" / "table" / (m + ".cho")).read_text()
        todo += [i for i in re.findall(r"^import\s+([\w.]+)\s*;", src, re.M) if not i.startswith("std.")]
    return seen


def build(work, mutant=None):
    proj = pathlib.Path(work)
    (proj / "src").mkdir()
    for m in closure("cexpr") + ["cmpv"]:
        p = ROOT / "tools" / "table" / (m + ".cho")
        t = p.read_text()
        if mutant and mutant[0] == m:
            t = t.replace(mutant[1], mutant[2])
        (proj / "src" / (m + ".cho")).write_text(t)
    (proj / "src/main.cho").write_text((ROOT / "tests/conformance/cexpr_probe/main.cho").read_text().replace("@MODULE@", "cexpr"))
    (proj / "cancho.toml").write_text('[package]\nname = "cexprprobe"\ncancho = "119bcfe9b985ef5ebd9dafc3ff3b2bbd05f22653"\n\n'
                                      '[[bin]]\nname = "probe"\nsources = ["src"]\nstd = true\nout = "build/probe"\n')
    r = subprocess.run(["cancho", "build", "--ignore-compiler-rev"], cwd=proj, capture_output=True, text=True)
    return (proj / "build/probe", "") if r.returncode == 0 else (None, r.stdout + r.stderr)


def run(probe, cs):
    got = []
    for i in range(0, len(cs), 100):
        chunk = cs[i:i + 100]
        argv = [str(probe)] + [x for c in chunk for x in c[:3]]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 0:
            return None, f"rc {r.returncode}: {r.stderr[:200]}"
        got += r.stdout.split("\n")[:len(chunk)]
    return got, ""


def same(want, got):
    if want.startswith("C "):
        return got.startswith(want)  # the status; the offset is the tool's
    return got == want


def check(cs, probe):
    got, err = run(probe, cs)
    if got is None:
        return [("probe failed", err)]
    return [(c, g) for c, g in zip(cs, got) if not same(c[3], g)]


MUTANTS = [
    ("a product binds as a sum", "    if code == 3 || code == 4 {\n        return 2;", "    if code == 3 || code == 4 {\n        return 1;"),
    ("a negation binds loosely", "    if code == 5 {\n        return 3;", "    if code == 5 {\n        return 0;"),
    ("a right-associative subtraction", "    while status == 0 && work[64] > 0 && prec(work[work[64] - 1]) >= prec(code) {", "    while status == 0 && work[64] > 0 && prec(work[work[64] - 1]) > prec(code) {"),
    ("the scale of a division is ignored", "        return emit(prog, at, op_div(), n, 0, 0);", "        return emit(prog, at, op_div(), 0, 0, 0);"),
    ("a division without a scale is accepted", "        if n < 0 {\n            return -1;\n        }", "        if n < -5 {\n            return -1;\n        }"),
    ("a decimal read at scale 0", "            return (from + 1 + k + 1, n, 1, 0);", "            return (from + 1 + k + 1, 0, 1, 0);"),
    ("a float is read as an int", "        return (from + 6, 0, 0, 6);", "        return (from + 6, 0, 0, 0);"),
    ("no type is accepted", "        return (from, 0, 0, 5);", "        return (from, 0, 0, 0);"),
    ("a position is one off", "                return (n - 1, 0);", "                return (n, 0);"),
    ("an ambiguous name is accepted", "    if count > 1 {\n        return (0, 3);", "    if count > 100 {\n        return (0, 3);"),
    ("a duplicate of a column is accepted", "            if cs == 0 {\n                status = 4;", "            if cs == 100 {\n                status = 4;"),
    ("a duplicate of a definition is accepted", "            if same {\n                status = 4;", "            if same && false {\n                status = 4;"),
    ("a product past scale 18 is accepted", "                if scale > 18 {\n                    return (0, 0, s_scale());", "                if scale > 99 {\n                    return (0, 0, s_scale());"),
    ("a zero divisor is a value", "                if st == 1 {\n                    return (11, 0);\n                }", "                if st == 11 {\n                    return (11, 0);\n                }"),
    ("an overflow is a value", "            if !ok {\n                return (12, 0);", "            if false {\n                return (12, 0);"),
    ("the literal's scale is dropped", "            cmpv.set_int(w, sp, prog[at + 1], prog[at + 2]);", "            cmpv.set_int(w, sp, prog[at + 1], 0);"),
    ("negation does nothing", "            cmpv.negate(w, sp - 1);", "            sp = sp;"),
    ("parentheses are unlimited", "                if work[65] > 4 || work[64] > 28 {", "                if work[65] > 400 || work[64] > 28 {"),
    ("an empty expression is accepted", "    if status == 0 && (expect || work[65] != 0) {", "    if status == 0 && (work[65] != 0) {"),
]


def fuzz_cases(rng, n):
    """G-NOPANIC: damaged and random definitions; whatever the answer, it is an answer (no trap), a listed status, and an offset inside the text."""
    base = [d for _, _, d, _ in lines_of(rng, 200)]
    out = []
    alphabet = "abcdef#:()+-*/@;=' \\.0123456789\"intdec,"
    for _ in range(n):
        d = rng.choice(base)
        k = rng.random()
        if k < 0.4 and d:
            i = rng.randrange(len(d))
            d = d[:i] + d[i + rng.randint(1, 4):]
        elif k < 0.7:
            i = rng.randrange(len(d) + 1)
            d = d[:i] + "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 4))) + d[i:]
        elif k < 0.85:
            d = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        else:
            d = d * rng.randint(2, 40)
        out.append((HEADER, "1,2.5,3,4,5,6", d, ""))
    return out


def fuzz_ok(cs, probe):
    got, err = run(probe, cs)
    if got is None:
        return [("probe failed", err)]
    shape = re.compile(r"^(C ([1-9]) (\d+)|E (1\d\d|2\d\d|11|12) \d+ \d+|[^=;]*=.*)$")
    bad = []
    for c, g in zip(cs, got):
        m = shape.match(g)
        if not m or (g.startswith("C ") and int(g.split()[2]) > len(c[2])):
            bad.append((c, g))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    problems = mutlib.check([(n, "cexpr.cho", o, w) for n, o, w in MUTANTS])
    if problems or a.check:
        print("\n".join(problems) or f"{len(MUTANTS)} mutants: every pattern occurs exactly once")
        sys.exit(1 if problems else 0)
    rng = random.Random(a.seed)
    cs = hand_cases() + lines_of(rng, a.cases if not a.mutants else 1500)
    if not a.mutants:
        with tempfile.TemporaryDirectory() as w:
            probe, err = build(w)
            if not probe:
                sys.exit("build failed:\n" + err)
            bad = check(cs, probe)
            fz = fuzz_cases(rng, 3000)
            fbad = fuzz_ok(fz, probe)
        print(f"{len(fz)} fuzz cases, {len(fbad)} traps or malformed answers")
        for b in fbad[:5]:
            print(" ", b)
        bad += fbad
        print(f"{len(cs)} cases, {len(bad)} differences")
        for b in bad[:10]:
            print(" ", b)
        sys.exit(1 if bad else 0)
    survivors = 0
    for name, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as w:
            probe, err = build(w, ("cexpr", old, new))
            if not probe:
                print(f"SKIP (does not build) {name}: {err[:200]}")
                survivors += 1
                continue
            bad = check(cs, probe)
        print(("killed   " if bad else "SURVIVED ") + name)
        survivors += 0 if bad else 1
    print(f"{len(MUTANTS) - survivors}/{len(MUTANTS)} killed")
    sys.exit(1 if survivors else 0)


if __name__ == "__main__":
    main()
