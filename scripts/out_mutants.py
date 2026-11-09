#!/usr/bin/env python3
"""Mutation check of `--out FILE` (docs/out.md; tests/conformance/test_out.py): the file write, the name that is taken, the bound of the held answer and the flags that go with it.

Each mutant is one defect: a taken name seen as free (or the reverse), a repair that does not repair (no `--overwrite`, the name replaced at the wrong place, a counter that starts or stops at the wrong
number, an extension lost), the receipt, the flags accepted that are refused (`--overwrite` without `--out`, `--out` without `--root`, with `--explain` or `--discover`), the answer not held (so it goes to
standard output) in each reader, the bound off by one or never checked, the threads not brought to one, and the writer that does not tell a replacement from a creation. The tool is rebuilt from it and run
against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/out_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.

Not mutated, because no test of a machine that is not out of space can reach it: the names of the steps `write`, `sync` and `rename` in `out.write-failed` (the step `create-temporary` is reached by a read-only directory), the sync of
the directory after the rename (durability is not observable by a reader), and the in-flight check at one byte under the bound, which the check at the end of the read makes equal (the same answers are refused).
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_out", "test_rules"]


MUTANTS = [
    # the look at the name
    ("a directory name is a free name", "outfile.cho", "        return 21;\n    }\n    var found = 0;", "        return 0;\n    }\n    var found = 0;"),
    ("a file that is there is free", "outfile.cho", "                        file_close(file);\n                        found = 1;", "                        file_close(file);\n                        found = 0;"),
    ("a parent that is not there is a free name", "outfile.cho", "        DirOpened::Failed(reason) => {\n            found = reason;", "        DirOpened::Failed(reason) => {\n            found = 0;"),
    ("a file that is not there is an error", "outfile.cho", "                        if reason != 2 {\n                            found = reason;", "                        if reason != 3 {\n                            found = reason;"),
    # the refusal of a taken name and its repairs
    ("the repair does not say --overwrite", "outfile.cho", "    o = fail.choose_arg(heap, o, \"--overwrite\");", "    o = fail.choose_arg(heap, o, \"--overwrite-x\");"),
    ("the other name is never offered", "outfile.cho", "    if len(free) > 0 {", "    if len(free) > 99999 {"),
    ("the other name replaces another argument", "outfile.cho", "c = fail.choose_option_replacing(heap, c, args, at, free);", "c = fail.choose_option_replacing(heap, c, args, at + 1, free);"),
    ("the counter starts at 0", "outfile.cho", "    var n = 1;\n    while n < 10 {", "    var n = 0;\n    while n < 10 {"),
    ("the counter stops at 8", "outfile.cho", "    var n = 1;\n    while n < 10 {", "    var n = 1;\n    while n < 9 {"),
    ("the extension is lost", "outfile.cho", "        cand = buffer.append(heap, cand, leaf[dot..len(leaf)]);\n", ""),
    ("the first byte of a name may begin its extension", "outfile.cho", "    while k > 1 {", "    while k > 0 {"),
    ("the first dot begins the extension", "outfile.cho", "        if int_of(leaf[k]) == '.' && dot == len(leaf) {", "        if int_of(leaf[k]) == '.' {"),
    ("the other name has no dash", "outfile.cho", "        cand = buffer.push(heap, cand, byte_of('-'));", "        cand = buffer.push(heap, cand, byte_of('_'));"),
    ("the detail names no flag", "outfile.cho", "    w = fail.detail_str(heap, w, \"flag\", \"--out\");", "    w = fail.detail_str(heap, w, \"flag\", \"--outx\");"),
    # the write
    ("a replacement is made with create", "outfile.cho", "                        if overwrite {\n                            let (errno2, step2) = atomic.replace(", "                        if false {\n                            let (errno2, step2) = atomic.replace("),
    ("a creation is made with replace", "outfile.cho", "                        } else {\n                            let (errno2, step2) = atomic.create(", "                        } else if false {\n                            let (errno2, step2) = atomic.create("),
    ("a raced name is a write failure", "outfile.cho", "                } else if !overwrite && step == 4 && failed == 17 {", "                } else if !overwrite && step == 5 && failed == 17 {"),
    ("a failure after replacing is a race", "outfile.cho", "                } else if !overwrite && step == 4 && failed == 17 {", "                } else if step == 4 && failed == 17 {"),
    ("two writers of one name share a temporary", "outfile.cho", "                if errno == 0 && !big && end > 0 {", "                if false {"),
    ("the errno of a failed step is lost", "outfile.cho", "    w = fail.detail_int(heap, w, \"errno\", errno);", "    w = fail.detail_int(heap, w, \"errno\", 0);"),
    ("the first step is called another", "outfile.cho", "        w = fail.detail_str(heap, w, \"step\", \"create-temporary\");", "        w = fail.detail_str(heap, w, \"step\", \"create\");"),
    ("a directory name is writable", "outfile.cho", "    let name = place.leaf(full);\n    if place.is_directory_name(name) {\n        return fail.io_error(heap, e, 21, true, rel);", "    let name = place.leaf(full);\n    if false {\n        return fail.io_error(heap, e, 21, true, rel);"),
    # the receipt
    ("the receipt counts the rows as the bytes", "outfile.cho", "    w = json.put_int(heap, w, size);", "    w = json.put_int(heap, w, rows);"),
    ("the receipt counts the bytes as the rows", "outfile.cho", "    w = json.put_int(heap, w, rows);", "    w = json.put_int(heap, w, size);"),
    ("the receipt names no path", "outfile.cho", "    w = text.put(heap, w, shown);", "    w = text.put(heap, w, \"\");"),
    # the flags
    ("--overwrite without --out is accepted", "table.cho", "    if inp.overwrite && !inp.has_out {", "    if inp.overwrite && !inp.has_out && false {"),
    ("--out without --root is accepted", "table.cho", "        if len(inp.root_text) == 0 {\n            e = flags.problem(heap, e, \"args.required-flag\", \"--out needs", "        if len(inp.root_text) == 99999 {\n            e = flags.problem(heap, e, \"args.required-flag\", \"--out needs"),
    ("--out with --explain is accepted", "table.cho", "        if inp.has_explain || inp.discover {\n            e = flags.problem(heap, e, \"args.conflict\", \"--out writes", "        if inp.discover {\n            e = flags.problem(heap, e, \"args.conflict\", \"--out writes"),
    ("--out with --discover is accepted", "table.cho", "        if inp.has_explain || inp.discover {\n            e = flags.problem(heap, e, \"args.conflict\", \"--out writes", "        if inp.has_explain {\n            e = flags.problem(heap, e, \"args.conflict\", \"--out writes"),
    ("a taken name is written over", "table.cho", "                        if there == 1 && !inp.overwrite {", "                        if there == 99999 && !inp.overwrite {"),
    ("an error of the look is ignored", "table.cho", "                        } else if there > 1 {", "                        } else if there > 99999 {"),
    ("--overwrite is not given to the write", "table.cho", "buffer.bytes(cc2), inp.overwrite, e);", "buffer.bytes(cc2), false, e);"),
    ("the write is given another path", "table.cho", "buffer.bytes(or3), buffer.bytes(of3), inp.out_at, buffer.bytes(cc2)", "buffer.bytes(or3), buffer.bytes(or3), inp.out_at, buffer.bytes(cc2)"),
    ("a json answer is written without its newline", "table.cho", "                content = buffer.push(heap, content, byte_of(10));\n            }", "            }"),
    ("a text answer is written as the document", "table.cho", "            } else if text_mode {\n                borrow plain as &tc in {", "            } else if false {\n                borrow plain as &tc in {"),
    ("the empty answer of JSON lines holds its mark", "table.cho", "                    if !jout.is_mark(buffer.bytes(pc)) {", "                    if true {"),
    # the held answer and its bound
    ("the csv answer is not held", "table.cho", "        if outing && as_csv {\n            tree = query.with_hold(tree, state_most);", "        if outing && as_csv && false {\n            tree = query.with_hold(tree, state_most);"),
    ("the bound is one over", "table.cho", "            hold_cap = state_most;", "            hold_cap = state_most + 1;"),
    ("the bound is met at its own size", "table.cho", "if as_csv && hold_cap > 0 && nbytes > hold_cap {", "if as_csv && hold_cap > 0 && nbytes >= hold_cap {"),
    ("the bound is not looked at when the read is done", "table.cho", "if as_csv && hold_cap > 0 && nbytes > hold_cap {", "if as_csv && hold_cap > 0 && nbytes > hold_cap + 1000000000 {"),
    ("the bound is not looked at while the read goes on", "engine.cho", "    if size > a[k_hold()] - 1 {", "    if size > a[k_hold()] + 1000000000 {"),
    ("the read does not stop at the bound", "engine.cho", "        a[k_abort()] = 10;\n        a[k_stop()] = 1;", "        a[k_abort()] = 10;"),
    ("a held block is written", "engine.cho", "    if a[k_hold()] > 0 {\n        return (held_check(pending, a), true);\n    }\n    var ok = true;\n    var held = pending;", "    if a[k_hold()] > 99999999999 {\n        return (held_check(pending, a), true);\n    }\n    var ok = true;\n    var held = pending;"),
    ("the last block is written", "engine.cho", "    if hold > 0 {\n        return (pending, true);", "    if hold > 99999999999 {\n        return (pending, true);"),
    ("the file read does not hold", "table.cho", "    var lends = vec.empty(heap, 1, 0);\n    var tally = box_slice(heap, engine.k_size(), 0);\n    borrow mut tally as &!hq in {\n        contents(hq)[engine.k_hold()] = query.hold_of(tree);", "    var lends = vec.empty(heap, 1, 0);\n    var tally = box_slice(heap, engine.k_size(), 0);\n    borrow mut tally as &!hq in {\n        contents(hq)[engine.k_hold()] = 0;"),
    ("the JSON-lines read does not hold", "jsonl.cho", "        contents(hq)[engine.k_hold()] = query.hold_of(tree);", "        contents(hq)[engine.k_hold()] = 0;"),
    ("the JSON-lines read keeps its threads", "jsonl.cho", "    if query.hold_of(tree) > 0 {\n        threads = 1;", "    if query.hold_of(tree) > 99999999 {\n        threads = 1;"),
    ("the file read keeps its threads", "table.cho", "    if query.hold_of(tree) > 0 {\n        threads = 1;", "    if query.hold_of(tree) > 99999999 {\n        threads = 1;"),
    ("the probe of a join does not hold", "jnread.cho", "        contents(hq)[engine.k_hold()] = query.hold_of(tree);", "        contents(hq)[engine.k_hold()] = 0;"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
