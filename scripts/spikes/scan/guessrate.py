#!/usr/bin/env python3
"""How often is a range's start inside a quoted field (the truth, from a sequential parse with the reader's rules),
and how often does the present guess ('outside') and the lookahead guess (scan.guess) get it right?

    python3 scripts/spikes/scan/guessrate.py FILE... [--chunk 4194304]

The ranges are the ones `par.ls` cuts: the first line start at or after k * chunk. The lookahead is a port of
`scan.plausible` / `scan.guess` (up to four records, two good ones, a 64 KiB window).
"""
import argparse, os, sys, mmap

def scan_line(line, delim, inside, seps):
    # reader.scan: (inside, seps, bad)
    end = len(line); p = 0; n = seps; bad = False
    while p < end and not bad:
        q = line.find(b'"', p)
        if not inside:
            qq = end if q < 0 else q
            n += line.count(bytes([delim]), p, qq)
            if qq == end:
                p = end
            elif qq == 0 or line[qq-1] == delim:
                inside = True; p = qq + 1
            else:
                p = qq + 1
        elif q < 0:
            p = end
        else:
            if q + 1 < end and line[q+1] == 34:
                p = q + 2
            else:
                inside = False
                if q + 1 == end: p = end
                elif line[q+1] == delim: n += 1; p = q + 2
                else: bad = True
    return inside, n, bad

def plausible(buf, delim, columns, inside0):
    at = 0; inside = inside0; skip = inside0; seps = 0; good = 0; failed = False
    n = len(buf)
    while not failed and good < 4 and at < n:
        k = buf.find(b'\n', at)
        if k < 0: at = n; continue
        line = buf[at:k]; at = k + 1
        if line.endswith(b'\r'): line = line[:-1]
        if inside or len(line) > 0:
            ins, m, bad = scan_line(line, delim, inside, seps)
            if bad: failed = True
            else:
                inside, seps = ins, m
                if not inside:
                    if skip: skip = False
                    elif seps + 1 != columns: failed = True
                    else: good += 1
                    seps = 0
    return (not failed) and good >= 2

def guess(buf, delim, columns):
    if plausible(buf, delim, columns, False): return 0
    if plausible(buf, delim, columns, True): return 1
    return 0

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("files", nargs="+"); ap.add_argument("--chunk", type=int, default=4194304)
    a = ap.parse_args()
    print("%-24s %8s %8s %8s %8s %8s" % ("file", "ranges", "inside", "old-wrong", "new-wrong", "new-in"))
    for f in a.files:
        size = os.path.getsize(f)
        with open(f, "rb") as fh:
            data = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            nl = data.find(b'\n'); header = data[:nl]
            columns = header.count(b',') + 1   # the headers here have no quotes
            # sequential parse: record state at every line start
            starts = []  # offsets of line starts that begin a record are implied; we need 'inside' at the edges
            edges = []
            k = 1
            while True:
                e = (k * a.chunk)
                if e >= size: break
                # first line start at or after e
                if data[e-1:e] == b'\n': ls = e
                else:
                    j = data.find(b'\n', e)
                    if j < 0: break
                    ls = j + 1
                edges.append(ls); k += 1
            edges_set = set(edges); inside_at = {}
            pos = nl + 1; inside = False; seps = 0
            ei = 0
            # walk lines, record the state at each edge line start
            edges_sorted = sorted(edges_set)
            ptr = 0
            while pos < size and ptr < len(edges_sorted):
                if pos >= edges_sorted[ptr]:
                    if pos == edges_sorted[ptr]: inside_at[pos] = inside
                    while ptr < len(edges_sorted) and edges_sorted[ptr] <= pos: ptr += 1
                    if ptr >= len(edges_sorted): break
                j = data.find(b'\n', pos)
                if j < 0: break
                line = data[pos:j]
                if line.endswith(b'\r'): line = line[:-1]
                if inside or len(line) > 0:
                    inside, seps, bad = scan_line(line, 44, inside, seps)
                    if bad: break
                    if not inside: seps = 0
                pos = j + 1
            n_in = n_old = n_new = n_newin = 0
            for ed in edges_sorted:
                truth = inside_at.get(ed)
                if truth is None: continue
                g = guess(data[ed:ed+65536], 44, columns)
                n_in += truth
                n_old += 1 if truth else 0
                n_new += 1 if (g == 1) != truth else 0
                n_newin += g
            print("%-24s %8d %8d %8d %8d %8d" % (os.path.basename(f), len(edges_sorted), n_in, n_old, n_new, n_newin))
main()
