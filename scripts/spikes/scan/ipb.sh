#!/bin/sh
# ipb.sh BIN FILE V : instructions, cycles and branch misses per byte of one round of a kern variant
# (rounds 6 minus rounds 1, so the read and the start are subtracted), on core $CORE of an Intel hybrid (cpu_core PMU).
CORE=${CORE:-2}
SZ=$(stat -c %s $2)
for r in 1 6; do
  taskset -c $CORE perf stat -x, -e instructions:u,cycles:u,branch-misses:u $1 $2 $3 $r 2>&1 >/dev/null | awk -F, -v r=$r '/cpu_core/ {printf "%s ", $1} END{print r}'
done | awk -v sz=$SZ '{ if (NR==1){i1=$1;c1=$2;b1=$3} else {printf "instr/B %.2f  cycles/B %.2f  brmiss/KB %.3f\n", ($1-i1)/5/sz, ($2-c1)/5/sz, ($3-b1)/5/sz*1024} }'
