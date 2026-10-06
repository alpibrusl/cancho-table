#!/usr/bin/env python3
"""Mutation check of `--select` (docs/select.md): each mutant is one source file of
tools/table with one deliberate defect, rebuilt, and run against the select,
differential, limit and rule tests. A mutant is killed when a test fails or the
build refuses it. The files are restored after every mutant, whatever happens.

    python3 scripts/select_mutants.py [name-substring ...]

Run where a compiler is (`lex-sys` on PATH, or LEX_SYS; LEX_SYS_ARGS="--ignore-compiler-rev"
for a compiler of another revision). Exit status 1 if one survives. Do not edit
tools/table while it runs.
"""
import os
import pathlib
import signal
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "table"
COMPILER = os.environ.get("LEX_SYS", "lex-sys")
EXTRA = os.environ.get("LEX_SYS_ARGS", "").split()
TESTS = ["test_select", "test_differential", "test_limits", "test_rules"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # reader.fields: where the fields of a record are
    ("a field is never quoted", "reader.ls", "if p < end && int_of(line[p]) == 34 {", "if p < end && int_of(line[p]) == 35 {"),
    ("a doubled quote closes a quoted field", "reader.ls", "                    if at + 1 < end && int_of(line[at + 1]) == 34 {\n                        q = at + 2;", "                    if false && at + 1 < end && int_of(line[at + 1]) == 34 {\n                        q = at + 2;"),
    ("a delimiter after a closing quote is bad", "reader.ls", "} else if int_of(line[at + 1]) == delim {\n                            next = at + 2;", "} else if int_of(line[at + 1]) == 0 - 5 {\n                            next = at + 2;"),
    ("a quoted field is marked unquoted", "reader.ls", "                        quoted = 1;", "                        quoted = 0;"),
    ("an unquoted field swallows the next delimiter", "reader.ls", "                next = last + 1;", "                next = last + 2;"),
    ("the last stored field is dropped", "reader.ls", "            if n < room {", "            if n + 1 < room {"),
    # plan: NAMES
    ("a # cannot be escaped", "plan.ls", " || int_of(given[i + 1]) == '#') {", ") {"),
    ("a position needs one character, not two", "plan.ls", "if hash && len(name) >= 2 && len(name) <= 10 {", "if hash && len(name) >= 1 && len(name) <= 10 {"),
    ("a repeated header names one column", "plan.ls", "map.set_value_at(w, found, 0 - 2);", "map.set_value_at(w, found, 0 - 1);"),
    ("a position past the last column is a column", "plan.ls", "position >= 1 && position <= width", "position >= 1 && position <= width + 1"),
    ("a position counts from 0", "plan.ls", "column = position - 1;", "column = position;"),
    ("case is ignored in a name's repair", "plan.ls", "if bytes.to_lower(int_of(a[i])) != bytes.to_lower(int_of(b[i])) {", "if int_of(a[i]) != int_of(b[i]) {"),
    ("a comma in a repaired name is not escaped", "plan.ls", "if c == ',' || c == '\\\\' || c == '#' && j == 0 {", "if c == '\\\\' || c == '#' && j == 0 {"),
    # writer
    ("an unquoted field with a CR is not quoted", "writer.ls", "        if !(has(data, 34) || has(data, 13)) {", "        if !has(data, 34) {"),
    ("a quoted field with a delimiter loses its quotes", "writer.ls", "    if !(has(data, delim) || has(data, 34) || has(data, 13) || has(data, 10)) {\n        return buffer.append(heap, out, data);\n    }\n    var o = buffer.push(heap, out, byte_of(34));\n    o = buffer.append(heap, o, data);", "    if !(has(data, 34) || has(data, 13) || has(data, 10)) {\n        return buffer.append(heap, out, data);\n    }\n    var o = buffer.push(heap, out, byte_of(34));\n    o = buffer.append(heap, o, data);"),
    ("a doubled quote is read as two in json", "writer.ls", "            p = p + found + 2;", "            p = p + found + 1;"),
    # table: paging, bounds, refusals
    ("a page starts one row late", "table.ls", "    return index >= from;", "    return index > from;"),
    ("a page holds one row more", "table.ls", "a[k_records()] >= from && a[k_emitted()] >= limit {", "a[k_records()] >= from && a[k_emitted()] > limit {"),
    ("next is one past", "table.ls", "                        a[k_next()] = a[k_records()];", "                        a[k_next()] = a[k_records()] + 1;"),
    ("the budget is not kept", "table.ls", "    if have + need + 1 > budget {", "    if have + need + 1 > budget + 1000000 {"),
    ("the next of a budget stop is one past", "table.ls", "a[k_next()] = a[k_records()] - 1;", "a[k_next()] = a[k_records()];"),
    ("a row of one empty field is a blank line", "table.ls", "        if picked == 1 && held == begun {", "        if false && picked == 1 && held == begun {"),
    ("--max-rows allows one more", "table.ls", "header && !quoted && a[k_records()] >= most {", "header && !quoted && a[k_records()] > most {"),
    ("a record is not bounded", "table.ls", "                            if held + 1 > cap {", "                            if held + 1 > cap + 100000000 {"),
    ("csv hides that --max-rows stopped it", "table.ls", "    } else if as_csv && c.capped {", "    } else if false && as_csv && c.capped {"),
    ("a record of several lines is one field short", "table.ls", "                                    var found = n + 1;", "                                    var found = n;"),
    ("a record of several lines is not kept for select", "table.ls", "                                    if want {\n                                        borrow mut cells", "                                    if false {\n                                        borrow mut cells"),
    ("the number of names is not capped", "table.ls", "    if named > plan.most_names() {", "    if named > 99999999 {"),
    ("the header is kept as a name list of the wrong width", "table.ls", "                                    picked = vec.size(tends);", "                                    picked = vec.size(tends) - 1;"),
]

FILES = {n: (SRC / n).read_text() for n in {m[1] for m in MUTANTS}}


def restore(*_):
    for n, text in FILES.items():
        (SRC / n).write_text(text)


def build_and_test():
    b = subprocess.run([COMPILER, "build", "--bin", "table", *EXTRA], cwd=ROOT, capture_output=True, text=True)
    if b.returncode:
        return "does not build", b.stderr.strip()[-140:]
    p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", *TESTS], cwd=ROOT / "tests" / "conformance",
                       capture_output=True, text=True, timeout=1800)
    failed = sorted({l.split(" ")[1] for l in (p.stdout + p.stderr).splitlines() if l.startswith(("FAIL:", "ERROR:"))})
    return ("killed" if p.returncode else "SURVIVED"), ", ".join(failed[:3])


def main():
    signal.signal(signal.SIGTERM, lambda *a: (restore(), sys.exit(143)))
    wanted = sys.argv[1:]
    chosen = [m for m in MUTANTS if not wanted or any(w in m[0] for w in wanted)]
    verdict, _ = build_and_test()
    print("unmutated:", "pass" if verdict == "SURVIVED" else verdict, flush=True)
    if verdict != "SURVIVED":
        return 1
    survivors = []
    for name, file, old, new in chosen:
        if FILES[file].count(old) != 1:
            print("!! %s: the site occurs %d times in %s" % (name, FILES[file].count(old), file))
            return 1
        try:
            (SRC / file).write_text(FILES[file].replace(old, new, 1))
            verdict, why = build_and_test()
        finally:
            restore()
        print("%-14s %s  [%s]" % (verdict, name, why), flush=True)
        if verdict == "SURVIVED":
            survivors.append(name)
    for n, text in FILES.items():
        assert (SRC / n).read_text() == text
    print("%d of %d killed" % (len(chosen) - len(survivors), len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
