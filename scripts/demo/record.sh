#!/bin/sh
# Record the demo: demo.py (simulated typing, real runs of build/table) under asciinema, at a fixed 100x28 terminal.
#
#   sh scripts/demo/record.sh [OUT.cast]       default: docs/assets/demo/table-demo.cast
#
# Needs a built binary (build/table; `cancho build`, see the README quick start) and asciinema, python3, jq, shasum.
# The commit of the checkout is written into the cast (its title and the TABLE_COMMIT variable of its env header) and is the commit the
# README of this directory says the video was recorded at. Idle time between events is capped at 1.2 s.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out=${1:-$root/docs/assets/demo/table-demo.cast}
[ -x "$root/build/table" ] || { echo "no $root/build/table: build it first (cancho build)" >&2; exit 2; }
for t in asciinema python3 jq shasum; do command -v "$t" >/dev/null || { echo "missing: $t" >&2; exit 2; }; done
commit=$(git -C "$root" rev-parse HEAD)
git -C "$root" diff --quiet HEAD -- tools generated cancho.toml 2>/dev/null || echo "warning: the sources differ from $commit" >&2
mkdir -p "$(dirname "$out")"
TABLE_COMMIT=$commit TABLE_BIN="$root/build/table" TERM=xterm-256color LC_ALL=${LC_ALL:-en_US.UTF-8} \
  asciinema rec --overwrite --cols 100 --rows 28 -i 1.2 \
    -t "table: exact answers, or a refusal that tells you how to fix the question (commit $commit)" \
    -e SHELL,TERM,TABLE_COMMIT \
    -c "python3 $here/demo.py" "$out"
echo "recorded $out at $commit"
