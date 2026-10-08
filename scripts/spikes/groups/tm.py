#!/usr/bin/env python3
"""tm.py: time commands, interleaved, N rounds. Prints min wall, min cpu (user+sys), peak RSS MB.
usage: tm.py [-n N] [--out] 'label=cmd args' ...   (cmd is split with shlex; output goes to /dev/null)
"""
import os, sys, shlex, subprocess, time, resource, platform
args = sys.argv[1:]
n = 5
if args and args[0] == "-n":
    n = int(args[1]); args = args[2:]
cmds = []
for a in args:
    label, cmd = a.split("=", 1)
    cmds.append((label, shlex.split(cmd)))
res = {l: [] for l, _ in cmds}
for r in range(n):
    order = cmds[r % len(cmds):] + cmds[:r % len(cmds)]
    for l, c in order:
        t = time.perf_counter()
        p = subprocess.Popen(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _, st, ru = os.wait4(p.pid, 0)
        w = time.perf_counter() - t
        rss = ru.ru_maxrss / (1 if platform.system() == "Linux" else 1) / (1024 if platform.system() == "Linux" else 1024 * 1024)
        res[l].append((w, ru.ru_utime + ru.ru_stime, rss, os.WEXITSTATUS(st)))
for l, _ in cmds:
    v = res[l]
    print("%-34s wall %.4f  cpu %.4f  rss %6.1f MB  rc %d" % (l, min(x[0] for x in v), min(x[1] for x in v), max(x[2] for x in v), v[0][3]))
