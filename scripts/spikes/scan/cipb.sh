#!/bin/bash
# cipb.sh BIN FILE V : instr/B cycles/B for one round of the C ceiling kernels (rounds 6 minus 1)
CORE=${CORE:-2}
SZ=$(stat -c %s $2)
for r in 1 6; do
  taskset -c $CORE perf stat -x, -e instructions:u,cycles:u $1 $2 $3 $r 2>&1 >/dev/null | awk -F, -v r=$r '/cpu_core/ {printf "%s ", $1} END{print r}'
done | awk -v sz=$SZ '{ if (NR==1){i1=$1;c1=$2} else {printf "instr/B %.2f  cycles/B %.2f\n", ($1-i1)/5/sz, ($2-c1)/5/sz} }'
