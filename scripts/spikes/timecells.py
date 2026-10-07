"""SPIKE: ns per cell of the readers of numparse.cho, by the difference between 21 rounds and 1, minimum of 3.
Needs S (a directory holding the built `numparse` and the cell files c_int.txt, c_price.txt, c_f17.txt, c_exp.txt: one cell per line, 1,000,000 lines)."""
import subprocess,time,os,sys
S=os.environ['S']
def run(mode,rounds,f):
    best=9e9
    for _ in range(3):
        t=time.time(); out=subprocess.run([S+'/numparse',mode,str(rounds)],stdin=open(S+'/'+f),capture_output=True); best=min(best,time.time()-t)
    return best,out.stdout.strip()
plan=[('c_int.txt',['line','int']),('c_price.txt',['line','int','dec2','fast','json','acc']),('c_f17.txt',['line','fast','json','acc']),('c_exp.txt',['line','fast','json','acc'])]
for f,modes in plan:
    for m in modes:
        if m=='int' and f!='c_int.txt': continue
        a,_=run(m,1,f); b,o=run(m,21,f)
        print(f"{f:14s} {m:5s} ns/cell {1e9*(b-a)/20/1e6:7.2f}   ({o.decode()})")
