"""SPIKE: ns per element of the sum loops of superacc.cho (plain f64, checked i64, pair, superacc), by the difference between 41 rounds and 1.
Needs S (a directory holding the built `superacc` and prices.json / wide.json: 1,000,000 JSON numbers) in the environment."""
import subprocess,sys,time,os
S=os.environ['S']
def run(mode,rounds,f):
    best=9e9
    for _ in range(3):
        t=time.time(); subprocess.run([S+'/superacc',mode,str(rounds)],stdin=open(S+'/'+f),capture_output=True); best=min(best,time.time()-t)
    return best
for f in ('prices.json','wide.json'):
    base=run('plain',1,f)
    for mode in ('plain','int','acc'):
        a=run(mode,1,f); b=run(mode,41,f)
        print(f, mode, 'ns per element', round((b-a)/40/1e6*1e9,2))
