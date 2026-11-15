#!/usr/bin/env python3
"""Mutation check of the text functions of `--compute` (docs/functions.md section 3.3, slice K6): the evaluator (`ctext.cho`), the compiler's text half and `len` (`cexpr.cho`), the row's use
of a text (`compute.cho`) and the explain (`computex.cho`), broken against tests/conformance/test_compute_text.py (the reference of Python's `bytes` through the binary).

    python3 scripts/ctext_mutants.py [name-substring ...]
    python3 scripts/ctext_mutants.py --check        # every site still matches (no build)

A mutant is killed when a test fails or the build refuses it. See mutlib.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_compute_text"]

MUTANTS = [
    ("lower leaves A as it is", "ctext.cho", "if id == 11 && b >= 65 && b <= 90 {", "if id == 11 && b >= 66 && b <= 90 {"),
    ("lower changes bytes that are not ASCII letters", "ctext.cho", "if id == 11 && b >= 65 && b <= 90 {", "if id == 11 && b >= 65 && b <= 222 {"),
    ("upper leaves a as it is", "ctext.cho", "} else if id == 12 && b >= 97 && b <= 122 {", "} else if id == 12 && b >= 98 && b <= 122 {"),
    ("trim keeps a tab", "ctext.cho", "return c == 32 || c == 9;", "return c == 32;"),
    ("trim removes a line feed", "ctext.cho", "return c == 32 || c == 9;", "return c == 32 || c == 9 || c == 10;"),
    ("rtrim trims the left too", "ctext.cho", "                    if id != 15 {", "                    if id != 99 {"),
    ("ltrim trims the right too", "ctext.cho", "                    if id != 14 {", "                    if id != 99 {"),
    ("substr counts from zero", "ctext.cho", "let i = start - 1;", "let i = start;"),
    ("substr takes one byte too many", "ctext.cho", "var stop = i + count;", "var stop = i + count + 1;"),
    ("substr is not clipped to the end", "ctext.cho", "                    if stop > len {\n                        stop = len;\n                    }", "                    if stop > len + 1 {\n                        stop = len;\n                    }"),
    ("substr may start inside a character", "ctext.cho", "if i > 0 && continuation(int_of(data[s0 + i])) {", "if i > 99999 && continuation(int_of(data[s0 + i])) {"),
    ("substr may end inside a character", "ctext.cho", "if stop > 0 && stop < len && continuation(int_of(data[s0 + stop])) {", "if stop > 99999 && stop < len && continuation(int_of(data[s0 + stop])) {"),
    ("substr refuses a text that starts with a continuation byte", "ctext.cho", "if i > 0 && continuation(int_of(data[s0 + i])) {", "if i >= 0 && continuation(int_of(data[s0 + i])) {"),
    ("a start past the end reads past the text", "ctext.cho", "                if i >= len {\n                    rs = s0;", "                if i > len {\n                    rs = s0;"),
    ("concat drops its last operand", "ctext.cho", "                    var j = 0;\n                    while j < n {\n                        var js = 0;", "                    var j = 0;\n                    while j < n - 1 {\n                        var js = 0;"),
    ("replace skips a byte after a match", "ctext.cho", "                            tmp = buffer.append(heap, tmp, data[ts..te]);\n                            k = k + flen;", "                            tmp = buffer.append(heap, tmp, data[ts..te]);\n                            k = k + flen + 1;"),
    ("replace lets the matches overlap", "ctext.cho", "                            tmp = buffer.append(heap, tmp, data[ts..te]);\n                            k = k + flen;", "                            tmp = buffer.append(heap, tmp, data[ts..te]);\n                            k = k + 1;"),
    ("a loaded text past the limit is accepted", "ctext.cho", "            if e0 - s0 > cap {\n                status = 17;", "            if e0 - s0 > cap + 1000000000 {\n                status = 17;"),
    ("a made text past the limit is accepted", "ctext.cho", "                if size > cap {\n                    status = 17;", "                if size > cap + 1000000000 {\n                    status = 17;"),
    ("a quoted cell keeps its doubled quotes", "ctext.cho", "    if doubled == 0 {\n        return buffer.append(heap, arena, record[first..last]);", "    if true {\n        return buffer.append(heap, arena, record[first..last]);"),
    ("len counts the doubled quotes of a cell", "ctext.cho", "    if doubled == 0 {\n        return last - first;", "    if true {\n        return last - first;"),
    ("a text result is not a quoted cell", "compute.cho", "contents(cw3)[3 * (ncols + d) + 2] = 1;", "contents(cw3)[3 * (ncols + d) + 2] = 0;"),
    ("the quotes of a text result are not doubled", "compute.cho", "                        if int_of(data[k]) == 34 {\n                            copy2 = buffer.push(heap, copy2, data[k]);", "                        if int_of(data[k]) == 99999 {\n                            copy2 = buffer.push(heap, copy2, data[k]);"),
    ("the line limit is not checked for a text result", "compute.cho", "                if tend > cap {\n                    st = 17;", "                if tend > cap + 1000000000 {\n                    st = 17;"),
    ("a text definition is evaluated as a number", "compute.cho", "        if cexpr.scale_of(prog, d) < 0 {\n            // a text-valued definition", "        if false {\n            // a text-valued definition"),
    ("len counts a literal's doubled quotes twice", "cexpr.cho", "            bytes_in = bytes_in + 1;\n            if int_of(t[k]) == 34 {\n                k = k + 2;", "            bytes_in = bytes_in + 1;\n            if int_of(t[k]) == 34 {\n                k = k + 1;"),
    ("a typed column is accepted in a text position", "cexpr.cho", "                } else if e2 < len(t) && int_of(t[e2]) == ':' {\n                    status = 16;", "                } else if e2 < len(t) && int_of(t[e2]) == 99999 {\n                    status = 16;"),
    ("arithmetic after a text is a syntax error", "cexpr.cho", "int_of(t[i]) == '/' {\n            status = 16;", "int_of(t[i]) == '/' {\n            status = 1;"),
    ("the start and the count of substr are one value", "cexpr.cho", "work[224 + top] = v1 * 2147483648 + v2;", "work[224 + top] = v1 * 2147483648 + v1;"),
    ("replace takes an empty text to look for", "cexpr.cho", "                        if s1 != 0 || t1 == f1 {", "                        if s1 != 0 {"),
    ("len takes a typed column", "cexpr.cho", "} else if e2 < len(t) && (int_of(t[e2]) == ':' || int_of(t[e2]) == '(') {\n            status = 16;", "} else if e2 < len(t) && (int_of(t[e2]) == 99999 || int_of(t[e2]) == '(') {\n            status = 16;"),
    ("a number definition copies a text definition", "cexpr.cho", "                    if dk >= 0 && prog[1 + 5 * dk + 2] < 0 {\n                        // an earlier definition that is a text", "                    if false {\n                        // an earlier definition that is a text"),
    # the bound of concat is held twice (at each comma and at the closing parenthesis): both are broken
    ("concat takes nine operands", "cexpr.cho", "                } else if fid == 17 {\n                    lo = 2;\n                    hi = 8;", "                } else if fid == 17 {\n                    lo = 2;\n                    hi = 9;", [("cexpr.cho", "                    if commas + 2 > 8 {", "                    if commas + 2 > 9 {")]),
    ("a function of one argument takes two", "cexpr.cho", "                    if q < len(t) && int_of(t[q]) == ')' {\n                        status = 1;\n                    } else {\n                        status = 14;", "                    if q < len(t) && int_of(t[q]) == ')' {\n                        status = 1;\n                    } else {\n                        status = 1;"),
    ("the explain does not say a text is a text", "computex.cho", '                    stage = buffer.append(heap, stage, ",\\"kind\\":\\"text\\"");\n', ""),
    ("the explain never lists the limit of a text", "computex.cho", "        if texts {\n            rules = put_rule(heap, rules, \"compute.result-too-long\");", "        if false {\n            rules = put_rule(heap, rules, \"compute.result-too-long\");"),
    ("the explain lists the cut of every text", "computex.cho", "        if cuts {\n            rules = put_rule(heap, rules, \"compute.not-utf8-boundary\");", "        if texts {\n            rules = put_rule(heap, rules, \"compute.not-utf8-boundary\");"),
]

# Survivors that are explained (none yet): an entry is (name, file, old, new, reason) and its site must still exist.
EQUIVALENT = []

if __name__ == "__main__":
    stale = mutlib.check([e[:4] for e in EQUIVALENT])
    for p in stale:
        print("!! (equivalent) " + p)
    if stale:
        sys.exit(1)
    sys.exit(mutlib.main(MUTANTS, TESTS))
