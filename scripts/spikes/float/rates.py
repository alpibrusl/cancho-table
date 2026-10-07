#!/usr/bin/env python3
"""Which tier reads each cell of a file (docs/gap-float.md section 6): the share Clinger's fast path decides, the share Eisel-Lemire decides, and the share that goes to the exact
way (more than 19 significant digits whose window and window + 1 round to different doubles), by the Python model of flt.cho (el_model.py).

    rates.py FILE...      one line per file: cells, % clinger, % eisel-lemire, % exact way"""
import re
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from el_model import el

G = re.compile(r"([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?)(\d+))?")


def tier(s):
    m = G.fullmatch(s)
    if m is None or len(s) > 1100 or not (m.group(2) or m.group(3)):
        return "bad"
    ip, fp = m.group(2) or "", m.group(3) or ""
    digs = (ip + fp)
    if not digs.strip("0"):
        return "zero"
    ex = int(m.group(5) or 0) * (-1 if m.group(4) == "-" else 1)
    ex = max(-100000, min(100000, ex))
    sig = (ip + fp).lstrip("0")
    q = ex - len(fp) + max(0, len(sig) - 19)   # dropped digits raise the power of ten of the window's last digit
    w = int(sig[:19])
    dropped = len(sig) > 19 and sig[19:].strip("0") != ""
    if not dropped and 0 <= w <= 2 ** 53 and -22 <= q <= 22:
        return "clinger"
    if dropped and el(q, w) != el(q, w + 1):
        return "exact"
    return "el"


for f in sys.argv[1:]:
    n = {"clinger": 0, "el": 0, "exact": 0, "zero": 0, "bad": 0}
    for line in open(f):
        n[tier(line.strip())] += 1
    t = sum(n.values())
    print(f"{f.rsplit('/', 1)[-1]:14s} {t:9d}  clinger {100*n['clinger']/t:6.2f}%  el {100*n['el']/t:6.2f}%  exact {100*n['exact']/t:8.4f}%  ({n['exact']} cells)  zero/bad {n['zero']}/{n['bad']}")
