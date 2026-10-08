#!/bin/bash
# tstat.sh BIN... : instructions/B, cycles/B (user, cpu_core) of one thread over the four everyday questions on the 31.7 MB file, on core $CORE.
CORE=${CORE:-2}
F=build/bench/data.csv
SZ=$(stat -c %s $F)
declare -A Q
Q[select]="--select status,bytes"
Q[filter]="--where status=404_and_bytes:int>50000"
Q[count]="--group status"
Q[sum]="--group status --agg sum:bytes"
for b in "$@"; do
 for name in select filter count sum; do
  if [ $name = filter ]; then args=(--where "status=404 and bytes:int>50000"); else read -ra args <<< "${Q[$name]}"; fi
  taskset -c $CORE perf stat -x, -e instructions:u,cycles:u,branch-misses:u $b --root build/bench "${args[@]}" --format csv --threads 1 data.csv 2>&1 >/dev/null | awk -F, -v n="$name" -v b="$b" -v sz=$SZ '/cpu_core\/instructions/ {i=$1} /cpu_core\/cycles/ {c=$1} /cpu_core\/branch-misses/ {m=$1} END{printf "%-18s %-7s instr/B %6.2f  cycles/B %5.2f  instr/row %6.0f  brmiss/KB %.3f\n", b, n, i/sz, c/sz, i/1000000, m/sz*1024}'
 done
done
