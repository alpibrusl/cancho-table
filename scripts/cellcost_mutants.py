#!/usr/bin/env python3
"""Mutation check of the cell-cost paths (docs/history.md, "cell cost"): the walk of short fields, the one-pass
quoting test, the add-in-place path and its cache of hot groups, the integer read without checks, the next line
taken in place. Each mutant is one source file of tools/table with one deliberate defect, rebuilt, and run against
test_cellcost, then the filter, select, parallel, limit and differential tests, stopping at the first failure. A mutant is killed when a test
fails or the build refuses it. The files are restored after every mutant, whatever
happens.

    python3 scripts/cellcost_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_cellcost", "test_filter", "test_select", "test_parallel", "test_limits"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
# Three mutants are not here, because they are equivalent, and were run to see:
#  * "an unquoted field is quoted for a delimiter it cannot hold" (writer.must_quote tests the delimiter and
#    LF for an unquoted field too): an unquoted field never holds a delimiter or an LF, or it would be two
#    fields or two lines; the answer is the same.
#  * "the length of a long key loses its second byte" (agg.add_fast writes `n >> 9` for `n >> 8`): the key
#    built there is only looked for, never stored; a wrong one is not found, and the row takes the way that
#    builds the right one. The answer is the same, and only the time shows it.
#  * "a distinct count is added in place" (the check in agg.add_fast removed, or engine.fast_ok answering true):
#    a distinct aggregate is kept from the fast way twice, by `fast_ok` in the callers and by the same test in
#    `add_fast`; either alone is enough, so removing one changes nothing a test can see.
MUTANTS = [
    # reader.seek: the walk and the hand over to memchr
    ("seek answers the stopping place without testing its byte", "reader.cho", "    if q < stop {\n        return q;\n    }", "    if q <= stop {\n        return q;\n    }"),
    ("seek forgets how far the walk got", "reader.cho", "    return q + more;", "    return q + more + 1;"),
    ("seek hands over to memchr one byte early", "reader.cho", "    let more = index_of_byte(line[q..end], byte_of(b));", "    let more = index_of_byte(line[q + 1..end], byte_of(b));"),
    ("seek finds a quote where it was asked for a delimiter", "reader.cho", "            let q = seek(line, p, end, delim);", "            let q = seek(line, p, end, 34);"),
    ("the closing quote is looked for from the opening one", "reader.cho", "                let at = seek(line, q, end, 34);", "                let at = seek(line, p, end, 34);"),
    # writer.must_quote
    ("a CR does not make a short field quoted", "writer.cho", "            if c == 34 || c == 13 {\n                return true;", "            if c == 34 {\n                return true;"),
    ("a quote does not make a short field quoted", "writer.cho", "            if c == 34 || c == 13 {\n                return true;", "            if c == 13 {\n                return true;"),
    ("an LF in a short quoted field is not quoted again", "writer.cho", "            if all == 1 && (c == delim || c == 10) {", "            if all == 1 && c == delim {"),
    ("a delimiter in a short quoted field is not quoted again", "writer.cho", "            if all == 1 && (c == delim || c == 10) {", "            if all == 1 && c == 10 {"),
    ("a long field is not tested for an LF", "writer.cho", "    return all == 1 && (has(data, delim) || has(data, 10));", "    return all == 1 && has(data, delim);"),
    ("a long field is not tested for a delimiter", "writer.cho", "    return all == 1 && (has(data, delim) || has(data, 10));", "    return all == 1 && has(data, 10);"),
    ("a long field is not tested for a CR", "writer.cho", "    if has(data, 34) || has(data, 13) {\n        return true;\n    }\n    return all", "    if has(data, 34) {\n        return true;\n    }\n    return all"),
    # agg.add_fast
    ("a cached group is believed without comparing its key", "agg.cho", "if length_at(key, p) != n || !bytes.equal(key[p + 4..p + 4 + n], record[first..first + n]) {", "if false && length_at(key, p) != n || !bytes.equal(key[p + 4..p + 4 + n], record[first..first + n]) {"),
    ("a cached group is believed whatever its bytes", "agg.cho", "if length_at(key, p) != n || !bytes.equal(key[p + 4..p + 4 + n], record[first..first + n]) {", "if length_at(key, p) != n {"),
    ("a cached key is compared for one column only", "agg.cho", "            while j < ng && entry >= 0 {", "            while j < 1 && entry >= 0 {"),
    ("a quoted key with a quote is keyed as written", "agg.cho", "        if cells[3 * column + 2] == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {\n            return (0 - 1, 0);", "        if false && cells[3 * column + 2] == 1 && index_of_byte(record[first..last], byte_of(34)) >= 0 {\n            return (0 - 1, 0);"),
    ("a key longer than the room is written anyway", "agg.cho", "        if len(buffer.room(g.keyb)) < need {\n            return (0 - 1, 0);", "        if false && len(buffer.room(g.keyb)) < need {\n            return (0 - 1, 0);"),
    ("a group that is not there is counted anyway", "agg.cho", "        entry = map.find(g.index, buffer.bytes(g.keyb));\n        if entry < 0 {", "        entry = map.find(g.index, buffer.bytes(g.keyb));\n        if false && entry < 0 {"),
    ("the key is written one byte short", "agg.cho", "            copy_into(room[at + 4..at + 4 + n], record[first..first + n]);", "            copy_into(room[at + 4..at + 3 + n], record[first..first + n - 1]);"),
    ("a row counts twice in place", "agg.cho", "    vec.set(g.acc, slot, before + 1);", "    vec.set(g.acc, slot, before + 2);"),
    ("an in-place integer past 64 bits is a text", "agg.cho", "                        bad = 3 + bad2;", "                        bad = 4;"),
    ("an integer past 64 bits is a text, by value", "query.cho", "    return (0, 3 + bad);\n}", "    return (0, 4);\n}"),
    ("an in-place text is a number", "agg.cho", "            if bad != 0 {\n                return (bad, k);", "            if false && bad != 0 {\n                return (bad, k);"),
    ("an in-place minimum is a maximum", "agg.cho", "            } else if function == 2 && v < now {\n                vec.set(g.acc, slot + 1 + k, v);", "            } else if function == 2 && v > now {\n                vec.set(g.acc, slot + 1 + k, v);"),
    ("the first value of an in-place group is not its minimum", "agg.cho", "            } else if before == 0 {\n                vec.set(g.acc, slot + 1 + k, v);", "            } else if before == 1 {\n                vec.set(g.acc, slot + 1 + k, v);"),
    ("the second aggregate is the first in place", "agg.cho", "            let column = cols[query.agg_at(tree, k, 1)];\n            let now = vec.get(g.acc, slot + 1 + k);", "            let column = cols[query.agg_at(tree, 0, 1)];\n            let now = vec.get(g.acc, slot + 1 + k);"),
    ("a built key is not committed before `add` takes it", "agg.cho", "    buffer.filled(g.keyb, need);", "    buffer.filled(g.keyb, 0);"),
    ("the rows that wait for the fast way are dropped", "agg.cho", "        contents(g.memo)[rest_at] = waiting - 1;\n        return (0 - 1, 0);", "        contents(g.memo)[rest_at] = waiting - 1;\n        return (0, 0);"),
    ("a new group is taken for one that is there", "agg.cho", "            return (0 - 2, 0);\n        }\n        if contents", "            return (0, 0);\n        }\n        if contents"),
    # engine
    ("a refusal in place is numbered one low", "engine.cho", "    return 12 + status;\n}", "    return 11 + status;\n}"),
    ("the aggregate that refused is lost in the answer", "engine.cho", "    return 16 + 16 * k + status;", "    return 16 + status;"),
    ("a refusal is decoded with the wrong radix", "engine.cho", "    let status = (answer - 16) % 16;", "    let status = (answer - 16) % 15;"),
    # scan.next_fast
    ("a line is taken before looking for its end", "scan.cho", "    if found < 0 || found > r.cap {\n        return 9;\n    }", "    if found > r.cap {\n        return 9;\n    }"),
    ("the rest of a line held over is a line", "scan.cho", "    if r.over > 0 || buffer.size(r.held) > 0 {\n        return 9;\n    }", "    if r.over > 0 {\n        return 9;\n    }"),
    ("a line that is too long is a line", "scan.cho", "    if r.over > 0 || buffer.size(r.held) > 0 {\n        return 9;\n    }", "    if buffer.size(r.held) > 0 {\n        return 9;\n    }"),
    ("a line longer than the cap is a line", "scan.cho", "    if found < 0 || found > r.cap {", "    if found < 0 || found > r.cap + 1000000 {"),
    ("a line does not move the offset by its newline", "scan.cho", "    r.seen = r.seen + (k + 1 - at);", "    r.seen = r.seen + (k - at);"),
    ("a line is counted twice", "scan.cho", "    r.count = r.count + 1;", "    r.count = r.count + 2;"),
    ("the next line starts at the newline", "scan.cho", "    r.pos = k + 1;", "    r.pos = k;"),
    ("the line starts one byte late", "scan.cho", "    r.view_from = at;", "    r.view_from = at + 1;"),
    # query.parse_int
    ("an integer of 19 digits is read without the check", "query.cho", "    if len(data) - at <= 18 {", "    if len(data) - at <= 19 {"),
    ("a negative integer is positive on the short way", "query.cho", "        if negative {\n            return (0 - plain, 0);", "        if false && negative {\n            return (0 - plain, 0);"),
    ("a digit is read one low on the short way", "query.cho", "            plain = plain * 10 + c - '0';", "            plain = plain * 10 + c - '1';"),
    ("a letter is a digit on the short way", "query.cho", "            if c < '0' || c > '9' {\n                return (0, 1);\n            }\n            plain", "            if c < '0' || c > 'z' {\n                return (0, 1);\n            }\n            plain"),
]


if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
