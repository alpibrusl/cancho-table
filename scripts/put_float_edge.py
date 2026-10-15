#!/usr/bin/env python3
"""`flt.put_float` on its own, from the IN-TREE sources, against Python (docs/numbers.md 1.1 and 4.6; issue #59).

    python3 scripts/put_float_edge.py [--seed 1] [--random 1000] [--control reference]

`put_float(heap, out, key)` writes the key of a double (`flt.key_of`) as text. The finite contract is the one the differential tests define and this script
does not change: the layout of `numbers_ref.float_text` (shortest decimal that reads back, positional from 1e-6 to 1e21, always with a point, scientific
outside), compared exactly. The one thing added by #59 is the non-finite doubles: before it `std.fmt.float_into`'s `inf` was split as digits and exponent and
came out as `i.nf`; now `+inf` and `-inf` are written as `inf` and `-inf`, and every NaN (either sign, any payload) as `NaN`: the spelling of `std.fmt.float_into`, which has no sign for a NaN.

The cells: +inf, -inf, NaN, -NaN and a NaN with a payload (the NON-FINITE family), 0.0, -0.0 (both the key 0), 5e-324, 1e21, 1e-7, 123456789012345678 (18 digits), the tie of the issue
(`0xc309a9757963b552`, exactly -902899553826474.25: two equally short decimals, the compiler's std.fmt picks the even one since cancho#361), the boundaries of
the positional range, and --random finite doubles of random bit patterns (deterministic from --seed). The probe is tests/conformance/put_float_probe/main.cho,
built with the compiler cancho.toml pins ($CANCHO, or `cancho` on PATH) from tools/table/{flt,pow5}.cho. Exit 1 on any difference; the output names each.
"""
import argparse
import os
import pathlib
import random
import re
import shutil
import struct
import procs as subprocess  # every child in a group that is reaped: tests/conformance/procs.py
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
import numbers_ref as ref  # noqa: E402

INF = float("inf")
NON_FINITE = [("+inf", INF, "inf"), ("-inf", -INF, "-inf"), ("nan", float("nan"), "NaN"),
              ("-nan", struct.unpack(">d", struct.pack(">Q", 0xfff8000000000000))[0], "NaN"),
              ("nan payload", struct.unpack(">d", struct.pack(">Q", 0x7ff0000000000001))[0], "NaN")]
NAMED = [
    ("0.0", 0.0), ("-0.0", -0.0), ("5e-324", 5e-324), ("1e21", 1e21), ("1e-7", 1e-7), ("123456789012345678", float(123456789012345678)),
    ("tie -902899553826474.25", struct.unpack(">d", struct.pack(">Q", 0xc309a9757963b552))[0]),
    ("1e20", 1e20), ("9.999999999999999e20", 9.999999999999999e20), ("1e-6", 1e-6), ("9.99e-7", 9.99e-7), ("1.7976931348623157e308", 1.7976931348623157e308),
    ("2.2250738585072014e-308", 2.2250738585072014e-308), ("0.1", 0.1), ("100.0", 100.0), ("12345.67", 12345.67), ("-1.5e-7", -1.5e-7),
    ("2^53", 2.0 ** 53), ("2^53+2", 2.0 ** 53 + 2), ("1e15", 1e15), ("-1e22", -1e22),
]


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


def key_of(x):
    """flt.key_of for a double; a zero (either sign) is the key 0, which is how the table keeps it"""
    b = struct.unpack(">q", struct.pack(">d", 0.0 if x == 0 else x))[0]
    return b ^ 0x7FFFFFFFFFFFFFFF if b < 0 else b


def build(scratch, exe, pin):
    proj = pathlib.Path(scratch)
    (proj / "src").mkdir()
    shutil.copy(ROOT / "tests" / "conformance" / "put_float_probe" / "main.cho", proj / "src" / "main.cho")
    for m in ("flt", "pow5"):
        shutil.copy(ROOT / "tools" / "table" / (m + ".cho"), proj / "src")
    (proj / "cancho.toml").write_text(f'[package]\nname = "putfloatprobe"\ncancho = "{pin}"\n\n[[bin]]\nname = "probe"\nsources = ["src"]\nstd = true\nout = "build/probe"\n')
    r = run([exe, "build"], cwd=proj)
    if r.returncode != 0:
        raise SystemExit(f"cancho build failed:\n{r.stdout}{r.stderr}")
    return str(proj / "build" / "probe")


def texts(probe, keys):
    out = []
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        r = run([probe] + [str(k) for k in chunk])
        if r.returncode != 0:
            raise SystemExit("the probe failed:\n" + r.stdout + r.stderr)
        lines = r.stdout.split("\n")[:-1]
        if len(lines) != len(chunk):
            raise SystemExit(f"the probe answered {len(lines)} lines to {len(chunk)} keys")
        out += lines
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--random", type=int, default=1000)
    ap.add_argument("--control", choices=["reference"], help="a leg that cannot fail proves nothing: with `reference` the expected text of +inf is wrong, and the run must differ")
    args = ap.parse_args()
    exe, pin = compiler()
    rng = random.Random(args.seed)
    cells = [("non-finite " + n, key_of(x), want) for n, x, want in NON_FINITE]
    if args.control == "reference":
        cells[0] = (cells[0][0], cells[0][1], "infinity")
    cells += [("named " + n, key_of(x), ref.float_text(x)) for n, x in NAMED]
    while len(cells) < len(NON_FINITE) + len(NAMED) + args.random:
        bits = rng.getrandbits(64)
        x = struct.unpack(">d", struct.pack(">Q", bits))[0]
        if x != x or x in (INF, -INF):
            continue
        cells.append(("random %016x" % bits, key_of(x), ref.float_text(x)))
    with tempfile.TemporaryDirectory() as scratch:
        probe = build(scratch, exe, pin)
        got = texts(probe, [k for _, k, _ in cells])
    bad = 0
    for (name, key, want), g in zip(cells, got):
        if g != want:
            bad += 1
            print("DIFFERENT", name, "key", key, "put_float", repr(g), "want", repr(want))
    print(f"{len(cells)} cells ({len(NON_FINITE)} non-finite, {len(NAMED)} named, {args.random} random), {bad} differences")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
