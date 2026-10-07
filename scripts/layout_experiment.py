#!/usr/bin/env python3
"""The layout-stability experiment (docs/numbers.md, "What was built: R0 and Q0"): do unrelated edits to the code that parses the flags and renders the answer move the instruction count of the
read loop?

    python3 scripts/layout_experiment.py [--tree DIR] [--file CSV] [--spread 0.002]

For each variant the tree's tools/table/table.cho gets a block of dead code (locals, a counted loop, a branch that never runs) at the top of `body` or of `read_file`, is
built, and the instructions retired by the one-thread read loop (two cells: a cut, and a count by group) are counted, the minimum of 3 runs, as `gate_regress.py --counter`
counts them. On the tree of the R0 stage the loop is in `readloop.cho` and the variants edit another function: the counts must agree within --spread (default 0.2%, ten times
the run-to-run wander of the count). Run it on a tree where the loop is inside `read_file` (main before R0) and the same edits are inside the loop's own function.
The tree's sources are restored whatever happens, and rebuilt. Needs the compiler (`cancho` on PATH).
"""
import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_regress as g  # noqa: E402

CELLS = {"cut status,bytes": g.CELLS["cut status,bytes"], "group-count status": g.CELLS["group-count status"]}
LOCALS = "".join("    var pad%d = %d;\n" % (i, i + 3) for i in range(12))
VARIANTS = {
    "base": "",
    "a loop and a branch": "    var junk = 0;\n    var kk = 0;\n    while kk < 5 {\n        junk = junk + kk;\n        kk = kk + 1;\n    }\n    if junk == 1000 {\n        kk = 1;\n    }\n",
    "twelve locals": LOCALS + "    if pad0 + pad1 + pad2 + pad3 + pad4 + pad5 + pad6 + pad7 + pad8 + pad9 + pad10 + pad11 == 1000 {\n        pad0 = 0;\n    }\n",
}
FUNCTIONS = ("body", "read_file")


def edit(text, fn, block):
    i = text.index("fn %s[" % fn)
    j = text.index("{\n", text.index(") -> ", i)) + 2
    return text[:j] + block + text[j:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(g.ROOT))
    ap.add_argument("--file", default=None)
    ap.add_argument("--spread", type=float, default=0.002)
    a = ap.parse_args()
    tree = pathlib.Path(a.tree)
    data = pathlib.Path(a.file) if a.file else tree / "build" / "num" / "data.csv"
    path = tree / "tools" / "table" / "table.cho"
    orig = path.read_text()
    results = {}
    try:
        for fn in FUNCTIONS:
            for name, block in VARIANTS.items():
                path.write_text(edit(orig, fn, block))
                b = subprocess.run(["cancho", "build"], cwd=tree, capture_output=True, text=True)
                if b.returncode:
                    print("!! %s / %s does not build: %s" % (fn, name, b.stderr.strip()[-200:]))
                    return 1
                for cell, flags in CELLS.items():
                    argv = g.argv_of(tree / "build" / "table", data, flags, 1)
                    counts = [g.counted(argv)[0] for _ in range(3)]
                    if min(counts) == 0:
                        print("!! the counter cannot be read here")
                        return 3
                    results.setdefault(cell, []).append(("%s: %s" % (fn, name), min(counts)))
    finally:
        path.write_text(orig)
        subprocess.run(["cancho", "build"], cwd=tree, capture_output=True)
    worst = 0.0
    for cell, rows in results.items():
        lo, hi = min(c for _, c in rows), max(c for _, c in rows)
        worst = max(worst, (hi - lo) / lo)
        print("%s: spread %.4f%%" % (cell, 100 * (hi - lo) / lo))
        for label, c in rows:
            print("    %-34s %d  (%+.4f%% of the first)" % (label, c, 100 * (c - rows[0][1]) / rows[0][1]))
    print("worst spread %.4f%%, bound %.2f%%: %s" % (100 * worst, 100 * a.spread, "stable" if worst <= a.spread else "MOVED"))
    return 0 if worst <= a.spread else 1


if __name__ == "__main__":
    sys.exit(main())
