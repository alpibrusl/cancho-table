#!/usr/bin/env python3
"""Mutation check of the text `if` of `--compute` through the binary (docs/functions.md section 3.4, slice K7b2): what only the row of `compute.cho` and the compiler's end decide, broken against
tests/conformance/test_compute_if.py (a leaf as a cell of the CSV and the JSON, the explain, the line limit). The evaluator's own mutants are in scripts/cexpr_golden.py.

    python3 scripts/cif_mutants.py [name-substring ...]
    python3 scripts/cif_mutants.py --check        # every site still matches (no build)

A mutant is killed when a test fails or the build refuses it. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_compute_if"]

MUTANTS = [
    ("a text if is run as a text of the text machine", "cexpr.cho", "        prog[1 + 5 * d + 2] = -2;", "        prog[1 + 5 * d + 2] = -1;"),
    ("a literal leaf is read from the row", "compute.cho", "                        b = int_of(text[k]);", "                        b = int_of(line[k]);"),
    ("a cell leaf is read from the definitions", "compute.cho", "                    if sel_src == 0 {\n                        b = int_of(line[k]);", "                    if sel_src == 7 {\n                        b = int_of(line[k]);"),
    ("the line limit is not checked for a leaf", "compute.cho", "                    k = k + 1;\n                }\n                var tend = 0;\n                borrow copy2 as &kr in {\n                    tend = buffer.size(kr);\n                }\n                if tend > cap {", "                    k = k + 1;\n                }\n                var tend = 0;\n                borrow copy2 as &kr in {\n                    tend = buffer.size(kr);\n                }\n                if tend > cap + 1000000000 {"),
    ("a leaf is not written as a quoted cell", "compute.cho", "                        contents(cw3)[3 * (ncols + d) + 2] = 1;\n                    }\n                }\n            }\n        } else if cexpr.scale_of(prog, d) < 0 {", "                        contents(cw3)[3 * (ncols + d) + 2] = 0;\n                    }\n                }\n            }\n        } else if cexpr.scale_of(prog, d) < 0 {"),
    ("the column of a refused cell is lost", "compute.cho", "                let (s1, c1, ts1, tf1, tt1, td1) = cexpr.eval(prog, d, text, line, contents(cr), ws);\n                st = s1;\n                cc = c1;\n                sel_src = ts1;", "                let (s1, c1, ts1, tf1, tt1, td1) = cexpr.eval(prog, d, text, line, contents(cr), ws);\n                st = s1;\n                cc = 0;\n                sel_src = ts1;"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS, sys.argv[1:]))
