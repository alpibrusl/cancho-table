#!/bin/sh
# diffcells.sh FILE [N]: the first N cells (default 10) where fbench D and reference.py --dump differ: line, cell, reader's, reference's
f=$1; n=${2:-10}
d=$(dirname "$0")
bin=${FBENCH:-fbench}
$bin D 1 < "$f" > "$f.new"
python3 "$d/reference.py" --dump "$f" > "$f.ref"
paste -d'|' "$f" "$f.new" "$f.ref" | awk -F'|' '$2 != $3 {print NR": "substr($1,1,120)"  new=" $2 "  ref=" $3; c++; if (c>='$n') exit}'
rm -f "$f.new" "$f.ref"
