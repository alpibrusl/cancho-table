#!/bin/bash
# prof.sh BIN : instructions by function (perf record, user) for the four questions, one thread, core $CORE.
CORE=${CORE:-2}
for name in select filter count sum; do
  case $name in select) args=(--select status,bytes);; filter) args=(--where "status=404 and bytes:int>50000");; count) args=(--group status);; sum) args=(--group status --agg sum:bytes);; esac
  taskset -c $CORE perf record -q -e instructions:u -c 100000 -o /tmp/gs-$name.perf $1 --root build/bench "${args[@]}" --format csv --threads 1 data.csv >/dev/null 2>&1
  echo "== $name"
  perf report -i /tmp/gs-$name.perf --stdio --no-children 2>/dev/null | grep -v "^#" | grep -v "^$" | head -${TOP:-9}
done
