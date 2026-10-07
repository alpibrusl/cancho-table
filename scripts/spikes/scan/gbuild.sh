#!/bin/sh
# gbuild.sh NAME : sync the tree to gram, build it there as build/table.NAME (the pinned-compatible compiler built from lex-sys-static-sig)
cd "$(dirname "$0")/../../.." || exit 1
rsync -a --exclude build --exclude .git ./ gram:Workspace/gap-scan/ || exit 1
ssh gram "cd ~/Workspace/gap-scan && nice taskset -c 6-7 ../gap-scan-compiler/target/release/lex-sys build --ignore-compiler-rev 2>&1 | tail -1 && cp build/table build/table.$1"
