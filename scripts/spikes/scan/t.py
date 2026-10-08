#!/usr/bin/env python3
"""t.py [-n RUNS] [-b bin1,bin2] cell... : min wall time, interleaved. cells: name:file:threads:args
   usage: t.py -n 5 -b build/table.base,build/table big:count:1,4,8"""
import argparse, subprocess, time, pathlib, shlex, os
R = pathlib.Path(__file__).resolve().parents[3]
A, B = R/"build/adv", R/"build/bench"
BIG = ["--max-groups","1000000","--max-state-bytes","1073741824"]
CELLS = {
 "select": (B,"data.csv",["--select","status,bytes"]),
 "filter": (B,"data.csv",["--where","status=404 and bytes:int>50000"]),
 "count": (B,"data.csv",["--group","status"]),
 "sum": (B,"data.csv",["--group","status","--agg","sum:bytes"]),
 "G2": (A,"big.csv",["--group","status","--max-rows","1000000000"]),
 "G1": (A,"big.csv",["--where","status=404 and bytes:int>50000","--max-rows","1000000000"]),
 "E1": (A,"long.csv",["--select","id,g"]),
 "E2": (A,"long.csv",["--group","g"]),
 "Enl": (A,"longq_nl.csv",["--select","id,g"]),
 "Eq": (A,"longq.csv",["--select","id,g"]),
 "Ep": (A,"longplain.csv",["--select","id,g"]),
 "wide": (A,"wide.csv",["--select","c5,c100,c199"]),
 "quoted": (A,"quoted.csv",["--select","status,note"]),
 "b3": (A,"f2.csv",["--group","k1m"]+BIG),
}
ap=argparse.ArgumentParser(); ap.add_argument("-n",type=int,default=5); ap.add_argument("-b",default=str(R/"build/table"))
ap.add_argument("-p",default="", help="prefix command e.g. 'taskset -c 0-5'")
ap.add_argument("cells",nargs="+"); a=ap.parse_args()
bins=a.b.split(","); pre=shlex.split(a.p)
for spec in a.cells:
    name,_,ts=spec.partition(":"); root,f,args=CELLS[name]; mb=(root/f).stat().st_size/1e6
    for t in (ts or "1").split(","):
        best={b:1e9 for b in bins}
        for _ in range(a.n):
            for b in bins:
                s=time.perf_counter()
                subprocess.run(pre+[b,"--root",str(root),*args,"--format","csv","--threads",t,f],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                best[b]=min(best[b],time.perf_counter()-s)
        ref=best[bins[0]]
        print("%-7s t%-2s %s"%(name,t,"  ".join("%7.4fs %5.0fMB/s %.2fx"%(best[b],mb/best[b],best[b]/ref) for b in bins)),flush=True)
