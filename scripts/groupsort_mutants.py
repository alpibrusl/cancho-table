#!/usr/bin/env python3
"""Mutation check of the default order of the groups (tools/table/gsort.cho, docs/gap-groups.md section 9, issue #79): each mutant is one deliberate defect in the
radix order of the keys or in the chain of pending runs that replaced its recursion, rebuilt, and run against tests/conformance/test_group_deep.py. A mutant is
killed when a test fails or the build refuses it.

    python3 scripts/groupsort_mutants.py [name-substring ...]

`--check` only verifies that every site still matches exactly once (no build; CI runs it). See mutlib.py. MUT_SHARD=I/N runs the I-th of N parts of the list (each
in a checkout of its own: a run rebuilds tools/table in place).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_group_deep"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # the boundary of a run of equal chunks
    ("a run ends one group early", "gsort.cho", "        while b < hi && word[b] == word[a] {", "        while b < hi - 1 && word[b] == word[a] {"),
    ("a run is the groups whose chunks differ", "gsort.cho", "        while b < hi && word[b] == word[a] {", "        while b < hi && word[b] != word[a] {"),
    ("a pair of groups is not a run", "gsort.cho", "        if b - a > 1 {\n            var at = 0 - 1;", "        if b - a > 2 {\n            var at = 0 - 1;"),
    # where the next chunk is
    ("the next chunk of a field skips a byte", "gsort.cho", "                go = off + 6;", "                go = off + 7;"),
    ("a field that goes on is told by a chunk that ends exactly", "gsort.cho", "            if word[a] & 255 == 7 {", "            if word[a] & 255 == 6 {"),
    ("a chunk that goes on carries the code of one that ends exactly", "gsort.cho", "        take = 6;\n        code = 7;", "        take = 6;\n        code = 6;"),
    ("the code is not in the chunk", "gsort.cho", "    return w * 256 + code;", "    return w * 256;"),
    ("the radix leaves the code byte out", "gsort.cho", "    while p < 7 {\n        if hist", "    while p < 6 {\n        if hist"),
    ("a chunk is read from the key's start, not the field's", "gsort.cho", "    let b = posf + 4 + off;", "    let b = 4 + off;"),
    ("the length of the field is the whole key's", "gsort.cho", "    let rem = length_at(key, posf) - off;", "    let rem = length_at(key, 0) - off;"),
    # the next field
    ("the next field starts without its length", "gsort.cho", "                at = posf + 4 + length_at(gmap.key_at(index, order[a]), posf);", "                at = posf + length_at(gmap.key_at(index, order[a]), posf);"),
    ("the next field starts inside the length", "gsort.cho", "                at = posf + 4 + length_at(gmap.key_at(index, order[a]), posf);", "                at = posf + 3 + length_at(gmap.key_at(index, order[a]), posf);"),
    ("the last field is not the last", "gsort.cho", "            } else if j + 1 < fields {", "            } else if j + 2 < fields {"),
    ("the second field is not sorted", "gsort.cho", "            } else if j + 1 < fields {", "            } else if j + 1 < 1 {"),
    # the chain of pending runs
    ("a pending run forgets where it ends", "gsort.cho", "                word[a] = b;\n", "                word[a] = hi;\n"),
    ("a pending run keeps the parent's field, not its own", "gsort.cho", "                word[a + 1] = at;", "                word[a + 1] = posf;"),
    ("a pending run keeps the parent's offset", "gsort.cho", "                word2[a] = go;", "                word2[a] = off;"),
    ("a pending run does not point at the one before it", "gsort.cho", "                spare[a] = top;", "                spare[a] = head;"),
    ("a pending run is not pushed", "gsort.cho", "                top = a;", "                top = top;"),
    ("a run of a field that begins at 0 is not pending", "gsort.cho", "            if at >= 0 {", "            if at > 0 {"),
    ("the chain is cut after its first run", "gsort.cho", "lo, hi, posf, off, j, fields, spare[lo]);", "lo, hi, posf, off, j, fields, 0 - 1);"),
    ("a popped run ends where its field begins", "gsort.cho", "            let hi = word[lo];", "            let hi = word[lo + 1];"),
    ("a popped run is in the wrong field", "gsort.cho", "            let posf = word[lo + 1];", "            let posf = word[lo];"),
    ("a popped run is at the wrong offset", "gsort.cho", "            let off = word2[lo];", "            let off = word2[lo + 1];"),
    ("a popped run is of the wrong field number", "gsort.cho", "            let j = word2[lo + 1];", "            let j = word2[lo];"),
    ("the first run is not sorted when there are two groups", "gsort.cho", "    if fields >= 1 && n > 1 {", "    if fields >= 1 && n > 2 {"),
    ("no group column is sorted", "gsort.cho", "    if fields >= 1 && n > 1 {", "    if fields >= 2 && n > 1 {"),
    ("the insertion sort moves the order and not the chunks", "gsort.cho", "            w[j] = w[j - 1];\n            o[j] = o[j - 1];", "            w[j] = w[j - 1];\n            o[j] = o[j];"),
]

# Not here, and why: mutants that change nothing a test can see (the answer is the same; only the time, or nothing, differs).
#  - survivors of the run of 2026-10-19 (4 of 32, each proved equivalent, so removed from the list; the other 28 were killed, by the tests named in the log of that run):
#      * the field's number does not advance (`jj = j`), and a pending run keeps its parent's field number (`word2[a + 1] = j`): the number only guards `j + 1 < fields`, a
#        run of equal chunks that ended in a field and has no field after it is a run of equal keys, and the keys of a map are unique, so the guard is never false
#        where it is asked; it is kept so that no key can send the sort past its last field. (A test cannot make two equal keys; `fields` lower than the key's fields is
#        the mutant "the last field is not the last", which is killed.)
#      * the field of a run is read from its last group's key (`order[b - 1]` for `order[a]`): the groups of a run have equal chunks, and a chunk that ends the field
#        holds its length, so every group of the run has the same field length and `posf + 4 + length` is the same;
#      * the insertion sort is used for no run (`hi - lo <= 1` for `<= 32`): the radix sort gives the same order, equal chunks stay together and are ordered by the next
#        chunk; only the time differs.
#  - the threshold of the insertion sort (32 for 31 or 33), a `>=` for the `>` in it, a digit of the radix that is scattered when every group shares it (`!= n` for
#    `!= n + 1`): a run is sorted either way, and equal chunks stay together; ties between equal chunks are settled by the next chunk, so their order here is never seen;
#  - `rem > 6` for `rem >= 6` or `rem > 7` in `word_at`, and a window that moves 5 bytes, not 6 (`off + 5`): a field that ends exactly at the chunk's end then goes on
#    to a chunk of zero bytes with the code 0, a window that overlaps by one byte repeats a byte every chunk; the same order, one more or one less level.

if __name__ == "__main__":
    shard = os.environ.get("MUT_SHARD")
    chosen = MUTANTS
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        chosen = [m for k, m in enumerate(MUTANTS) if k % n == i]
    sys.exit(mutlib.main(chosen, TESTS))
