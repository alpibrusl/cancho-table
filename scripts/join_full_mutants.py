#!/usr/bin/env python3
"""Mutation check of slice F1a (docs/join-redesign.md section 9; tests/conformance/test_join_full.py): `--join-type full`, rows out.

Each mutant is one defect in the matched marks (never set, always set, set for the first row of a key only, the wrong row, the wrong bit), the second phase (when it runs, in which order, which rows, the padding of the
probe cells, the cells of the build columns), the pushdown that a full join does not have, the refusals of what a full join does not take, and `--explain`; the tool is rebuilt from it and run against the tests. A mutant is
killed when the build refuses it or a test fails.

    python3 scripts/join_full_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

TESTS = ["test_join_full.Refusals", "test_join_full.Where", "test_join_full.Oracle", "test_join_full.Pages", "test_join_j2.Flags", "test_rules"]

MUTANTS = [
    ("a probe row matching a build row does not mark it", "jnread.cho", "        if st[s_full()] == 1 {\n            mark(st, x);\n        }", "        if st[s_full()] == 7 {\n            mark(st, x);\n        }"),
    ("only the first build row of a key is marked", "jnread.cho", "        if st[s_full()] == 1 {\n            mark(st, x);\n        }\n        x = jtab.next_row(jt, x);", "        if st[s_full()] == 1 && matches == 1 {\n            mark(st, x);\n        }\n        x = jtab.next_row(jt, x);"),
    ("the mark is of the next build row", "jnread.cho", "            mark(st, x);", "            mark(st, x + 1);"),
    ("the mark is bit 0 of its slot whatever the row", "jnread.cho", "    let bit = row % 62;\n    st[slot] = st[slot] | 1 << bit;", "    let bit = 0;\n    st[slot] = st[slot] | 1 << bit;"),
    ("a mark is read from the slot of the next 62 rows", "jnread.cho", "    let slot = s_size() + row / 62;\n    let bit = row % 62;\n    let word = st[slot] >> bit & 1;", "    let slot = s_size() + row / 62 + 1;\n    let bit = row % 62;\n    let word = st[slot] >> bit & 1;"),
    ("every build row is taken for matched", "jnread.cho", "        if !marked(st, r) {", "        if marked(st, r) || !marked(st, r) {"),
    ("every build row is taken for unmatched", "jnread.cho", "        if !marked(st, r) {", "        if true {"),
    ("the unmatched build rows come in reverse order", "jnread.cho", "            let brec = jtab.record(jt, r);", "            let brec = jtab.record(jt, total - 1 - r);"),
    ("the unmatched build rows stop after the first", "jnread.cho", "    while r < total && a[engine.k_stop()] == 0 {", "    while r < 1 && a[engine.k_stop()] == 0 {"),
    ("the second phase ignores a full page", "jnread.cho", "    while r < total && a[engine.k_stop()] == 0 {", "    while r < total {"),
    ("the second phase runs after a refusal", "jnread.cho", "            if full && a[engine.k_abort()] == 0 && a[engine.k_stop()] == 0 && a[engine.k_capped()] == 0 && a[engine.k_ragged()] == 0 {", "            if full && a[engine.k_stop()] == 0 {"),
    ("the second phase is not run", "jnread.cho", "            if full && a[engine.k_abort()] == 0 && a[engine.k_stop()] == 0 && a[engine.k_capped()] == 0 && a[engine.k_ragged()] == 0 {", "            if full && a[engine.k_abort()] == 7 {"),
    ("the probe part of a second-phase record has one cell too few", "jnread.cho", "            while c + 1 < np {\n                jrec2 = buffer.push(heap, jrec2, byte_of(delim));", "            while c + 2 < np {\n                jrec2 = buffer.push(heap, jrec2, byte_of(delim));"),
    ("the build cells of a second-phase record are found one byte early", "jnread.cho", "            let keep = np;\n            let brec = jtab.record(jt, r);", "            let keep = np - 1;\n            let brec = jtab.record(jt, r);"),
    ("the probe cells of a second-phase record are not empty", "jnread.cho", "                cells[3 * p + 1] = p;\n                cells[3 * p + 2] = 0;", "                cells[3 * p + 1] = p + 1;\n                cells[3 * p + 2] = 0;"),
    ("a padded probe cell is not said to be padding", "jnread.cho", "            if a[engine.k_abort()] != 0 && a[engine.k_err_fn()] == 0 - 1 && a[engine.k_err_col()] < np {\n                a[engine.k_err_fn()] = 98 + 1;", "            if a[engine.k_abort()] != 0 && a[engine.k_err_fn()] == 0 - 1 && a[engine.k_err_col()] < 0 {\n                a[engine.k_err_fn()] = 98 + 1;"),
    ("a full join does not mark: the flag is not given to the pass", "jnread.cho", "            if full {\n                st[s_full()] = 1;\n            }", "            if full && np < 0 {\n                st[s_full()] = 1;\n            }"),
    ("the marks have no room", "jnread.cho", "        marks = jtab.rows_of(jt) / 62 + 1;", "        marks = 0;"),
    ("a full join pushes the leading conditions of --where", "jnwhere.cho", "    if jtype == 4 {", "    if jtype == 9 {"),
    ("a full join is classified as an inner join", "jnrun.cho", "                if full {\n                    ctype = 4;\n                }", "                if full {\n                    ctype = 0;\n                }"),
    ("the word full is not a left join", "jnrun.cho", "        } else if bytes.equal(word, \"full\") {\n            kind = 1;", "        } else if bytes.equal(word, \"full-never\") {\n            kind = 1;"),
    ("a --join-where is taken by a full join", "jnflags.cho", "        if cli.has(parsed, table, \"join-where\") {\n            e = flags.problem(heap, e, \"args.conflict\", \"--join-where drops", "        if cli.has(parsed, table, \"join-where\") && !full {\n            e = flags.problem(heap, e, \"args.conflict\", \"--join-where drops"),
    ("a --join-sorted is taken by a full join", "jnflags.cho", "        if cli.has(parsed, table, \"join-sorted\") {\n            e = flags.problem(heap, e, \"args.conflict\", \"a full join is not built over --join-sorted", "        if cli.has(parsed, table, \"join-sorted\") && !full {\n            e = flags.problem(heap, e, \"args.conflict\", \"a full join is not built over --join-sorted"),
    ("a --group over a full join is taken", "jnflags.cho", "        if inp.has_group || inp.has_agg || inp.has_order || cli.has(parsed, table, \"sort\") || cli.has(parsed, table, \"top\") {", "        if inp.has_agg || inp.has_order || cli.has(parsed, table, \"sort\") || cli.has(parsed, table, \"top\") {"),
    ("a shared-name key beside a full join is taken", "jnflags.cho", "                if shared {\n                    e = flags.problem(heap, e, \"args.conflict\", \"a full join shows", "                if shared && !full {\n                    e = flags.problem(heap, e, \"args.conflict\", \"a full join shows"),
    ("an --explain says the join is a left join", "jnexplain.cho", "    if full {\n        o = buffer.append(heap, o, \"\\\"full\\\"\");", "    if false {\n        o = buffer.append(heap, o, \"\\\"full\\\"\");"),
    ("an --explain moves the conditions of a full join before the join", "jnexplain.cho", "                            if full {\n                                ctype = 4;\n                            }", "                            if false {\n                                ctype = 4;\n                            }"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
