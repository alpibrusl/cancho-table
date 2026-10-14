#!/usr/bin/env python3
"""The golden test of the three exact cell parsers (parse_int, parse_dec, pow10) of tools/table, against Python's integer arithmetic.

    python3 scripts/cellnum_golden.py --leg binary                         # the built table binary (build/table, or --table): `--agg min:v:int|dec(S)` over a one-cell file
    python3 scripts/cellnum_golden.py --leg source                         # the sources of the module, built with the pinned compiler into a scratch probe
    python3 scripts/cellnum_golden.py --leg store --rev REV                # the PUBLISHED store: a scratch project that depends on .cancho-vcs/<module> by git, rev and path
    python3 scripts/cellnum_golden.py --leg source --control reference     # negative controls: the leg must fail (exit 1)
    python3 scripts/cellnum_golden.py --leg source --control source

--module is the module the parsers are in (default: cellnum; query before they were moved out of it). The expected answers are computed here, from the grammar
of docs/numbers.md section 3 and Python's int, never from the code under test: (value, status) with status 0 it is, 1 it is not (empty, sign or point alone,
a space, an exponent, a letter, trailing junk, a second point), 2 it does not fit (parse_int: 64 bits; parse_dec: 10^18 once scaled), 3 (parse_dec only)
more fractional digits than the scale. The order of parse_dec's checks is the design's: grammar, then scale, then width.

The binary leg sees the statuses as refusals (value.not-integer, value.integer-overflow, value.not-decimal, value.decimal-too-wide, value.decimal-scale) and
cannot see an empty cell (an aggregate skips it: the expected answer is no value), nor pow10. The source and store legs see all of them, through
tests/conformance/cellnum_probe/main.cho. The store leg needs the revision to be pushed (it is fetched from GitHub); it is not run in CI.
"""
import argparse
import csv
import io
import os
import pathlib
import random
import re
import shutil
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
INT_MAX = 2**63 - 1
INT_MIN = -(2**63)
DEC_LIMIT = 10**18          # a scaled decimal is below this, so it is an int
REPO = "https://github.com/alpibrusl/cancho-table"


# ---- the reference: grammar and arithmetic, nothing of the code under test -------------------------------------------------------------

def ref_int(cell):
    if re.fullmatch(r"[+-]?[0-9]+", cell, re.ASCII) is None:
        return 0, 1
    v = int(cell)
    if v < INT_MIN or v > INT_MAX:
        return 0, 2
    return v, 0


def ref_dec(cell, scale, accept_junk=False):
    m = re.fullmatch(r"([+-]?)([0-9]*)(?:\.([0-9]*))?", cell, re.ASCII)
    if accept_junk and m is None:
        m = re.match(r"([+-]?)([0-9]*)(?:\.([0-9]*))?", cell, re.ASCII)
    if m is None:
        return 0, 1
    sign, whole, frac = m.group(1), m.group(2), m.group(3) or ""
    if len(whole) + len(frac) == 0:
        return 0, 1
    if len(frac) > scale:
        return 0, 3
    v = int(whole + frac) * 10 ** (scale - len(frac))
    if v >= DEC_LIMIT:
        return 0, 2
    return (-v if sign == "-" else v), 0


def ref_pow10(k):
    return 10 ** k, 0


# ---- the cases -------------------------------------------------------------------------------------------------------------------------

INT_CELLS = [
    "", "+", "-", "0", "-0", "+0", "1", "-1", "+1", "7", "00012", "-00012", "+00012", "12 ", " 12", "1 2", "1.0", "1.", ".1", "1e3", "1E3", "0x10", "abc", "12abc",
    "abc12", "--1", "+-1", "-+1", "1-", "1+", "1,2", "1_000", "١٢", "½", "12 ", "\t12", "'12'", "\"12\"", "NaN", "inf", "-",
    # 18 digits and under cannot leave 64 bits (one path of the parser); 19 and over are the checked path
    "9" * 17, "9" * 18, "-" + "9" * 18, "1" + "0" * 17, "1" + "0" * 18, "-1" + "0" * 18, "9" * 19, "-" + "9" * 19, "1" + "0" * 19, "9" * 20, "9" * 25, "-" + "9" * 25,
    # the edges of 64 bits
    "9223372036854775806", "9223372036854775807", "+9223372036854775807", "9223372036854775808", "9223372036854775809", "9223372036854775810", "9223372036854775817",
    "9223372036854775818", "9223372036854775900", "9300000000000000000", "-9223372036854775807", "-9223372036854775808", "-9223372036854775809", "-9223372036854775810",
    "-9223372036854775818", "-9223372036854775900", "18446744073709551615", "18446744073709551616", "-18446744073709551616",
    # leading zeros do not count towards the width
    "0" * 30 + "9223372036854775807", "0" * 30 + "9223372036854775808", "-" + "0" * 30 + "9223372036854775808", "-" + "0" * 30 + "9223372036854775809", "0" * 40,
    # trailing and leading junk next to the edges (a bad byte is still a bad byte after an overflow)
    "9223372036854775807x", "9223372036854775808x", "x9223372036854775808", "92233720368547758079 ", "9223372036854775808.0", "-9223372036854775808.", "9" * 25 + "x",
]

DEC_CELLS = [
    "", ".", "+", "-", "+.", "-.", "0", "-0", "+0", "0.", ".0", "-.0", "+0.0", "5", ".5", "5.", "-.5", "+.5", "0.5", "1.5", "-1.5", "1.50", "1.500", "1.2.3", "1..2", "..",
    "1.5.", ".1.", "1e5", "1.5e2", "1,5", "1 .5", " 1.5", "1.5 ", "1. 5", "abc", "1.5x", "x1.5", "--1.5", "+-1.5", "1.5-", "١.٢",
    "0.1", "0.01", "0.001", "0.123456789012345678", "0.1234567890123456789", "12.345678901234567890", "12.3456789012345678901234567890",
    "00012.5", "-00012.50", "000.000", "-0.00", "00000000000000000000.5", "0" * 25, "0" * 25 + ".", "0" * 25 + "." + "0" * 25,
    # the width: below 10^18 once scaled; leading zeros do not count
    "9" * 17, "9" * 18, "-" + "9" * 18, "9" * 18 + ".", "." + "9" * 18, "-." + "9" * 18, "9" * 19, "-" + "9" * 19, "." + "9" * 19, "1" + "0" * 17, "1" + "0" * 18,
    "9" * 17 + ".9", "9" * 16 + ".99", "9" * 9 + "." + "9" * 9, "9" * 10 + "." + "9" * 9, "1" + "0" * 16 + ".5", "1" + "0" * 15 + ".55", "1" + "0" * 17 + ".0",
    "9223372036854775807", "9223372036854775808", "-9223372036854775808", "9223372036854775807.5", "922337203685477580.7", "92233720368547758.07",
    "0" * 30 + "999999999999999999", "0" * 30 + "1000000000000000000", "0" * 30 + "999999999999999999.99", "999999999999999999" + "0" * 30,
    # junk after a width or a scale problem is still a grammar error
    "9" * 25 + "x", "9" * 25 + ".5x", "1.23456x", "1.23456.", "1.5e",
]
FUZZ = 600


def fuzz_cells(rng):
    out = []
    for _ in range(FUZZ):
        digits = lambda n: "".join(rng.choice("0123456789") for _ in range(n))
        kind = rng.randrange(6)
        sign = rng.choice(["", "", "-", "+"])
        if kind == 0:
            cell = sign + digits(rng.randrange(1, 24))
        elif kind == 1:
            cell = sign + digits(rng.randrange(0, 20)) + "." + digits(rng.randrange(0, 20))
        elif kind == 2:
            cell = sign + "0" * rng.randrange(0, 20) + digits(rng.randrange(0, 20))
        elif kind == 3:
            cell = sign + digits(rng.randrange(1, 22))
            at = rng.randrange(len(cell) + 1)
            cell = cell[:at] + rng.choice([".", "x", " ", "-", "e", ",", "+"]) + cell[at:]
        elif kind == 4:
            cell = sign + str(rng.choice([INT_MAX, INT_MIN, 10**18, 10**18 - 1, 2**64])) + rng.choice(["", "", "0", ".0", ".5", "9"])
        else:
            cell = sign + digits(rng.randrange(0, 4)) + "." + digits(rng.randrange(0, 4))
        out.append(cell)
    return out


def cases():
    """[(op, number, cell)]: I = parse_int, D = parse_dec at the scale `number`, P = pow10 of `number`."""
    rng = random.Random(20261013)
    out = [("I", 0, c) for c in INT_CELLS]
    out += [("D", s, c) for c in DEC_CELLS for s in range(19)]
    fz = fuzz_cells(rng)
    out += [("I", 0, c) for c in fz]
    out += [("D", rng.randrange(19), c) for c in fz]
    out += [("P", k, "") for k in range(19)]
    # no newline or carriage return (and no NUL: an argument cannot hold one): the legs carry a cell as an argument and the binary leg in a CSV file
    return [c for c in out if not re.search(r"[\n\r]", c[2])]


def expected(case, control=None):
    op, num, cell = case
    if op == "I":
        if control == "reference" and cell == "9223372036854775808":
            return INT_MAX, 0          # the control: 2^63 taken as a value
        return ref_int(cell)
    if op == "D":
        return ref_dec(cell, num, accept_junk=control == "reference-junk")
    return ref_pow10(num)


# ---- the legs --------------------------------------------------------------------------------------------------------------------------

def run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def compiler():
    exe = os.environ.get("CANCHO") or shutil.which("cancho")
    if not exe:
        raise SystemExit("no compiler: set CANCHO, or put the cancho named in cancho.toml on PATH")
    pin = re.search(r'^cancho\s*=\s*"([0-9a-f]+)"', (ROOT / "cancho.toml").read_text(), re.M).group(1)
    got = re.search(r"rev ([0-9a-f]+)", run([exe, "--version"]).stdout)
    if not got or got.group(1) != pin:
        raise SystemExit(f"{exe} is not the compiler cancho.toml pins ({pin})")
    return exe, pin


def probe_source(module):
    return (ROOT / "tests" / "conformance" / "cellnum_probe" / "main.cho").read_text().replace("@MODULE@", module)


def sources_closure(module):
    """the module and the non-std modules it imports, transitively: the files of tools/table"""
    seen, todo = [], [module]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.append(m)
        text = (ROOT / "tools" / "table" / (m + ".cho")).read_text()
        todo += [i for i in re.findall(r"^import\s+([\w.]+)\s*;", text, re.M) if not i.startswith("std.")]
    return seen


def mutate_source(path):
    """the source control: a decimal with more fractional digits than the scale is not refused (it is read as if it had fewer)"""
    text = path.read_text()
    old = "    if frac > scale {\n        return (0, 3);"
    if text.count(old) != 1:
        raise SystemExit(f"{path}: the text of the source control is not there exactly once")
    path.write_text(text.replace(old, "    if false && frac > scale {\n        return (0, 3);"))


def probe_leg(args, scratch, exe, pin, cs):
    proj = pathlib.Path(scratch)
    (proj / "src").mkdir()
    (proj / "src" / "main.cho").write_text(probe_source(args.module))
    toml = f'[package]\nname = "cellnumprobe"\ncancho = "{pin}"\n\n'
    if args.leg == "source":
        for m in sources_closure(args.module):
            shutil.copy(ROOT / "tools" / "table" / (m + ".cho"), proj / "src")
        if args.control == "source":
            mutate_source(proj / "src" / (args.module + ".cho"))
    else:
        if not args.rev:
            raise SystemExit("--leg store needs --rev: the pushed revision whose .cancho-vcs/" + args.module + " is read")
        toml += f'[dependencies.{args.module}]\ngit = "{REPO}"\nrev = "{args.rev}"\npath = ".cancho-vcs/{args.module}"\n\n'
    toml += '[[bin]]\nname = "probe"\nsources = ["src"]\nstd = true\nout = "build/probe"\n'
    (proj / "cancho.toml").write_text(toml)
    for step in (["install"] if args.leg == "store" else []) + ["build"]:
        r = run([exe, step], cwd=proj)
        if r.returncode != 0:
            raise SystemExit(f"cancho {step} failed:\n{r.stdout}{r.stderr}")
    got = []
    for i in range(0, len(cs), 400):
        chunk = cs[i:i + 400]
        argv = [str(proj / "build" / "probe")]
        for op, num, cell in chunk:
            argv += [op, str(num), cell]
        r = run(argv)
        if r.returncode != 0:
            raise SystemExit("the probe failed:\n" + r.stdout + r.stderr)
        lines = r.stdout.split("\n")[:-1]
        if len(lines) != len(chunk):
            raise SystemExit(f"the probe answered {len(lines)} lines to {len(chunk)} cases")
        got += [tuple(int(x) for x in ln.split(" ")) for ln in lines]
    return got


RULES_INT = {"value.not-integer": 1, "value.integer-overflow": 2}
RULES_DEC = {"value.not-decimal": 1, "value.decimal-too-wide": 2, "value.decimal-scale": 3}


def dec_text(v, scale):
    s = str(abs(v)).rjust(scale + 1, "0")
    body = s if scale == 0 else s[:-scale] + "." + s[-scale:]
    return ("-" if v < 0 else "") + body


def binary_leg(args, scratch, cs):
    table = args.table or str(ROOT / "build" / "table")
    got = []
    path = os.path.join(scratch, "one.csv")
    for op, num, cell in cs:
        if op == "P":
            got.append(None)
            continue
        if cell == "":
            got.append("empty")
            continue
        buf = io.StringIO(newline="")
        csv.writer(buf, lineterminator="\n").writerows([["v"], [cell]])
        pathlib.Path(path).write_bytes(buf.getvalue().encode("utf-8", "surrogateescape"))
        kind = "int" if op == "I" else f"dec({num})"
        r = run([table, "--agg", f"min:v:{kind}", "--format", "csv", path])
        if r.returncode == 0:
            rows = r.stdout.split("\n")
            got.append(("value", rows[1]))
            continue
        m = re.match(r"table: ([\w.-]+):", r.stderr)
        rules = RULES_INT if op == "I" else RULES_DEC
        got.append(("status", rules.get(m.group(1) if m else "", -1)))
    return got


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leg", choices=["binary", "source", "store"], required=True)
    ap.add_argument("--module", default="cellnum")
    ap.add_argument("--rev")
    ap.add_argument("--table")
    ap.add_argument("--control", choices=["reference", "reference-junk", "source"])
    args = ap.parse_args()
    cs = cases()
    bad = []
    with tempfile.TemporaryDirectory() as scratch:
        if args.leg == "binary":
            got = binary_leg(args, scratch, cs)
            for case, g in zip(cs, got):
                if g is None:
                    continue
                v, st = expected(case, args.control)
                if g == "empty":
                    ok = True       # an aggregate skips an empty cell: nothing to compare
                    want = "(skipped)"
                elif st == 0:
                    want = dec_text(v, case[1]) if case[0] == "D" else str(v)
                    ok = g == ("value", want)
                else:
                    want = ("status", st)
                    ok = g == want
                if not ok:
                    bad.append((case, want if st == 0 or g == "empty" else want, g))
        else:
            exe, pin = compiler()
            got = probe_leg(args, scratch, exe, pin, cs)
            for case, g in zip(cs, got):
                want = expected(case, args.control)
                if g != want:
                    bad.append((case, want, g))
    skipped = sum(1 for c in cs if c[0] == "P" or c[2] == "") if args.leg == "binary" else 0
    print(f"{args.leg} leg, module {args.module}: {len(cs)} cases ({sum(1 for c in cs if c[0] == 'I')} parse_int, {sum(1 for c in cs if c[0] == 'D')} parse_dec, "
          f"{sum(1 for c in cs if c[0] == 'P')} pow10){'; ' + str(skipped) + ' not observable here' if skipped else ''}; {len(bad)} differ")
    for case, want, g in bad[:15]:
        print(f"  {case[0]} {case[1]} {case[2]!r}: expected {want}, got {g}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
