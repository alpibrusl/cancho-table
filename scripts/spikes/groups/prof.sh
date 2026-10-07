#!/bin/bash
# prof.sh OUTFILE cmd... : sample (1 ms) the process named like cmd's program from its launch, run cmd once with stdout to /dev/null
out=$1; shift
name=$(basename "$1")
sample "$name" 3 1 -wait -file $out > /dev/null 2>&1 &
sp=$!
sleep 0.5
"$@" > /dev/null
wait $sp
