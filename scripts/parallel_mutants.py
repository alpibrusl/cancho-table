#!/usr/bin/env python3
"""Mutation check of `--threads` (docs/parallel.md): each mutant is one source file of
tools/table with one deliberate defect in the parallel path, rebuilt, and run against
test_parallel (every plan, N threads against the sequential read) and the plan tests. A
mutant is killed when a test fails or the build refuses it. The files are restored after
every mutant, whatever happens.

    python3 scripts/parallel_mutants.py [name-substring ...]

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
TESTS = ["test_parallel"]

# (name, file, the text replaced, its replacement): each `old` occurs exactly once in its file.
MUTANTS = [
    # stitching: the parent takes ranges in file order, and only a range whose guess was right
    ("a range whose first line is not where the last record ended is taken", "par.ls", "if agg.get_i64(sl, at) == 0 && first == cur {", "if agg.get_i64(sl, at) == 0 {"),
    ("a range whose thread says it needs the parent is taken", "par.ls", "if agg.get_i64(sl, at) == 0 && first == cur {", "if first == cur {"),
    ("the lines a range read are not counted", "par.ls", "lines_before = lines_before + l_lines(sl, at);", "lines_before = lines_before + 0;"),
    ("the first ragged row of a range is its own number", "par.ls", "a[engine.k_first_row()] = before + agg.get_i64(sl, at + 32 + 8 * engine.k_first_row());", "a[engine.k_first_row()] = agg.get_i64(sl, at + 32 + 8 * engine.k_first_row());"),
    ("the first ragged line of a range is its own number", "par.ls", "a[engine.k_first_line()] = lines_before + agg.get_i64(sl, at + 32 + 8 * engine.k_first_line());", "a[engine.k_first_line()] = agg.get_i64(sl, at + 32 + 8 * engine.k_first_line());"),
    ("a later ragged row can be the first", "par.ls", "if a[engine.k_ragged()] == 0 && g > 0 {", "if g > 0 {"),
    ("the rows a range read are not counted", "par.ls", "a[engine.k_records()] = before + r;", "a[engine.k_records()] = before;"),
    ("a range that passes --max-rows is taken", "par.ls", "var fits = before < most && before + r <= most;", "var fits = true;"),
    ("a range that would fill the page is taken", "par.ls", "if a[engine.k_emitted()] + e > limit {", "if false {"),
    ("a range that reaches the limit is taken, and the ragged rows after its last row with it", "par.ls", "if !filtering && a[engine.k_emitted()] + e >= limit && r > 0 {", "if false {"),
    ("a range that passes the byte budget is taken", "par.ls", "if have + payload_len + 2 > budget {", "if have + payload_len + 2 > budget + 100000 {"),
    ("json rows of two ranges are not separated", "par.ls", "if !as_csv && a[engine.k_emitted()] > 0 {", "if false {"),
    ("the rows of a range are not added", "par.ls", "rows2 = buffer.append(heap, rows2, body);", "rows2 = buffer.append(heap, rows2, \"\");"),
    ("a reread does not know the lines before it", "par.ls", "                        pp[scan.p_base_line()] = lines_before;\n                        let (x2, l2, st,", "                        pp[scan.p_base_line()] = 0;\n                        let (x2, l2, st,"),
    ("groups that do not fit a slot are trusted", "par.ls", "                    if n < 0 {\n                        clean = 0;", "                    if false {\n                        clean = 0;"),
    # a range
    ("a range reads one record too few", "scan.ls", "} else if !quoted && at >= until {", "} else if !quoted && at + 1 >= until {"),
    ("a range forgets its lines when it stops", "scan.ls", "                    consumed = number - 1;\n                    next_at = at;", "                    consumed = number;\n                    next_at = at;"),
    ("a range of a quoted record is one field short", "scan.ls", "                            let (m, open, bad_quote) = reader.fields(record, delim, cells, a[engine.k_columns()]);\n                            found = m;", "                            let (m, open, bad_quote) = reader.fields(record, delim, cells, a[engine.k_columns()]);\n                            found = m - 1;"),
    # groups across ranges
    ("a sum that can overflow in order is merged", "agg.ls", "if get_i64(blob, acc_at + 8 * (1 + na + k)) > query.int_max() - int_max_of(total) {", "if get_i64(blob, acc_at + 8 * (1 + na + k)) > query.int_max() {"),
    ("the largest running sum is not kept", "agg.ls", "                                if magnitude > seen_peak {", "                                if magnitude < seen_peak {"),
    ("a bound that would be passed is not noticed", "agg.ls", "if size + new_groups > max_groups || pairs + new_pairs > max_distinct || held + new_bytes > max_state {", "if false {"),
    ("distinct values of a range are not added", "agg.ls", "a = set_at(a, entry * stride + 1 + k, mine + fr[i * na + k]);", "a = set_at(a, entry * stride + 1 + k, mine);"),
    ("a minimum is merged as a maximum", "agg.ls", "} else if function == 2 && theirs < mine {", "} else if function == 2 && theirs > mine {"),
    ("the count of a range is not added", "agg.ls", "a = set_at(a, entry * stride, before + get_i64(blob, acc_at));", "a = set_at(a, entry * stride, before);"),
    ("a negative integer is read as a large one", "agg.ls", "    if top >= 128 {\n        top = top - 256;\n    }", ""),
    ("a new group is kept under an empty key", "agg.ls", "                        m = map.put(heap, m, key, 0);", "                        m = map.put(heap, m, key[0..0], 0);"),
    # handing over
    ("a selection that starts later is read by threads", "table.ls", "threads > 1 && (mode == 2 || row_from == 0)", "threads > 1"),
]

# Not here, and why: mutants that change nothing a test can see.
#  - a payload of rows that does not fit is trusted: a range that makes `room` bytes of rows has already
#    yielded (status 1, so `clean` is 0) before the copy is reached, and one that has not fits;
#  - a thread that met an error is trusted: scan_range answers status 2 for every abort, stop,
#    full page and row bound, so the extra conditions of `clean` repeat what `s == 0` says;
#  - the ranges are not aligned to lines, or a range reads one record too many: where a range
#    begins and ends is a guess the parent checks by `first == cur`, so a bad guess costs a re-read
#    and not an answer (the output is the same; only the time is not);
#  - a pair already known is added again: `map.put` of a present key replaces its value, which is 0;
#  - one thread is not enough to be sequential: `--threads 1` through the parallel code (one range at
#    a time) answers the same;
#  - a re-read does not write a full block of csv: it keeps the block until the next range is taken
#    instead of writing it, which changes the memory the parent holds and not one byte written.

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
