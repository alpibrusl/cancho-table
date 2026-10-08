#!/bin/sh
# Builds fbench with the Mac compiler: the new reader is tools/table/flt.cho, the reader of origin/main is generated here as module flt0.
#     [BASE=DIR_WITH_origin_main_checkout] scripts/spikes/float/build.sh OUTDIR     (needs `cancho` on PATH; writes OUTDIR/fbench; BASE when there is no git repository to read origin/main from)
set -e
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
out=${1:?outdir}
mkdir -p "$out"
if [ -n "$BASE" ]; then cat "$BASE/tools/table/flt.cho"; else git -C "$root" show origin/main:tools/table/flt.cho; fi | sed 's/^module flt;/module flt0;/' > "$out/flt0.cho"
cancho build "$here/fbench.cho" "$out/flt0.cho" "$root/tools/table/flt.cho" "$root/tools/table/dec.cho" "$root/tools/table/pow5.cho" --std ${BACKEND:+--backend $BACKEND} -o "$out/fbench"
