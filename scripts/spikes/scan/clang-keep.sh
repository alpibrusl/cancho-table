#!/bin/sh
# CLANG wrapper: keep the .ll the lex-sys LLVM backend hands to clang (in $KEEP_LL, default /tmp/keep-ll), then run clang.
mkdir -p "${KEEP_LL:-/tmp/keep-ll}"
for a in "$@"; do case "$a" in *.ll) cp "$a" "${KEEP_LL:-/tmp/keep-ll}/last.ll";; esac; done
exec clang $CLANG_EXTRA "$@"
