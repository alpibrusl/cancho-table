#!/usr/bin/env python3
"""Mutation check of the join (docs/joins.md section 10, gate M; tests/conformance/test_join.py): slice J1, `--join FILE --on PROBE=BUILD`, text keys, inner.

Each mutant is one defect in how a key is read or compared, how the chain of a key is kept or walked, how the joined row is made, what is counted against the bound, which file is
read first, or how a flag or a header is judged; the tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/join_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.

Not here, because J1 has no such code (the slice that adds it adds its mutants, docs/joins.md section 11): the padding width of a left join (J2), a semi join that duplicates and an anti
join that is inverted (J2), the typed sign of `-0` and the typed read of a key (J2), USING dropping the wrong column (J2), the pushdown of a build condition for `left` and of a typed
condition (J3), `--join-where` applied to the probe (J3), the swap repair offered for `left` (J7), the shared budget with the groups not subtracted (J3).
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

# cheap classes first: a killed mutant stops at the first failure (mutlib runs `unittest -f`)
TESTS = ["test_join.Keys", "test_join.Columns", "test_join.Flags", "test_join.DataRefusals", "test_join.Order", "test_join.MaxJoinRows", "test_join.Stdin", "test_join.Explain",
         "test_join.Oracle", "test_join.Accounting", "test_join.Introspect", "test_rules", "test_authority", "test_skill"]

MUTANTS = [
    # how a key is read and compared
    ("a quoted build key keeps its quotes", "jnread.cho", "    if cells[3 * key + 2] == 0 {\n        let (t2, ok) = jtab.add(", "    if true {\n        let (t2, ok) = jtab.add("),
    ("a quoted probe key keeps its quotes", "jnread.cho", "    if cells[3 * pkey + 2] == 0 {\n        chain = jtab.first(", "    if true {\n        chain = jtab.first("),
    ("an empty build key is not held", "jnread.cho", "    let last = cells[3 * key + 1];\n    if cells[3 * key + 2] == 0 {", "    let last = cells[3 * key + 1];\n    if last == first {\n        return (t, scratch, true);\n    }\n    if cells[3 * key + 2] == 0 {"),
    ("an empty probe key never matches", "jnread.cho", "    var chain = 0 - 1;\n    if cells[3 * pkey + 2] == 0 {", "    var chain = 0 - 1;\n    if last == first && cells[3 * pkey + 2] == 0 {\n        return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n    }\n    if cells[3 * pkey + 2] == 0 {"),
    ("the key is compared without its length", "jtab.cho", "    var cost = len(record) + 16;\n    if e < 0 {", "    var cost = len(record) + 16;\n    if e < 0 && len(key) > 0 {"),
    # the chain of a key
    ("a key's rows are walked from the last", "jtab.cho", "    return contents(t.head)[e];", "    return contents(t.tail)[e];"),
    ("a new row is linked at the head, not the tail", "jtab.cho", "        borrow mut nx as &!nw in {\n            contents(nw)[last] = row;\n        }", "        borrow mut nx as &!nw in {\n            contents(nw)[row] = last;\n        }"),
    ("only the first row of a chain is joined", "jnread.cho", "        x = jtab.next_row(jt, x);\n    }\n    return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n}", "        x = 0 - 1;\n    }\n    return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n}"),
    ("the chain's length is counted twice", "jnread.cho", "        matches = matches + 1;", "        matches = matches + 2;"),
    # the joined row
    ("the build cells are shifted one byte short", "jnread.cho", "cells[3 * (np + k)] = bcells[3 * k] + keep;", "cells[3 * (np + k)] = bcells[3 * k] + keep - 1;"),
    ("the build record is not cut back between matches", "jnread.cho", "            jw.used = keep;", "            jw.used = jw.used;"),
    ("the delimiter between the records is the probe's last byte", "jnread.cho", "    jrec2 = buffer.push(heap, jrec2, byte_of(delim));\n    let keep = len(prec) + 1;", "    jrec2 = buffer.push(heap, jrec2, byte_of(44));\n    let keep = len(prec) + 1;"),
    ("a probe row with no match is joined to the first build row", "jnread.cho", "    if chain < 0 {\n        return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n    }\n    var matches = 0;", "    if chain < 0 {\n        chain = 0;\n    }\n    var matches = 0;"),
    # --from, the page and the rows counted
    ("--from skips one joined row too few", "jnread.cho", "        if st[s_produced()] > from {", "        if st[s_produced()] >= from {"),
    ("--from skips one joined row too many", "jnread.cho", "        if st[s_produced()] > from {", "        if st[s_produced()] > from + 1 {"),
    ("the joined rows are not numbered for the page", "jnread.cho", "        a[engine.k_records()] = st[s_produced()];", "        a[engine.k_records()] = st[s_probe()];"),
    ("a refusal names the joined row, not the probe row", "jnread.cho", "            if a[engine.k_abort()] != 0 {\n                a[engine.k_err_row()] = st[s_probe()];\n            }", "            if a[engine.k_abort()] != 0 {\n                a[engine.k_err_row()] = st[s_produced()];\n            }"),
    # the bound on the rows produced
    ("--max-join-rows is off by one", "jnread.cho", "        if st[s_produced()] >= max_join {", "        if st[s_produced()] > max_join {"),
    ("--max-join-rows counts after --where", "jnread.cho", "        st[s_produced()] = st[s_produced()] + 1;\n        let brec", "        let brec"),
    ("the most matches so far is not kept", "jnread.cho", "    if matches > st[s_max_matches()] {\n        st[s_max_matches()] = matches;\n    }", "    st[s_max_matches()] = matches;"),
    # the bound on the build, and what is counted
    ("the build bound is off by one", "jtab.cho", "    if held + cost > limit {", "    if held + cost >= limit {"),
    ("a row's cost forgets its 16", "jtab.cho", "    var cost = len(record) + 16;", "    var cost = len(record) + 15;"),
    ("a key's cost forgets its 60", "jtab.cho", "        cost = cost + len(key) + 60;", "        cost = cost + len(key) + 59;"),
    ("a key's bytes are counted twice", "jtab.cho", "        cost = cost + len(key) + 60;", "        cost = cost + len(key) + len(key) + 60;"),
    ("a repeated key is counted as new", "jtab.cho", "    if e < 0 {\n        cost = cost + len(key) + 60;\n    }", "    if e < 1000000000 {\n        cost = cost + len(key) + 60;\n    }"),
    ("a refused row is held anyway", "jtab.cho", "        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held }, false);", "        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held + cost }, false);"),
    ("the refusal names no row", "jnread.cho", "bc.records, bc.abort_line, consumed, size);", "0, bc.abort_line, consumed, size);"),
    ("the bound is the default and not the flag", "jnread.cho", '    let max_state = cli.nat(args, parsed, table, "max-state-bytes");\n    let max_join', '    let max_state = 67108864;\n    let max_join'),
    # which file is read first, and what refuses a build
    ("a ragged build row is ignored", "jnread.cho", "bc.abort != 0 || bc.ragged > 0 || bc.capped || berrno != 0", "bc.abort != 0 || bc.capped || berrno != 0"),
    ("a build truncated by --max-rows is not refused", "jnread.cho", "bc.abort != 0 || bc.ragged > 0 || bc.capped || berrno != 0", "bc.abort != 0 || bc.ragged > 0 || berrno != 0"),
    ("a ragged probe row is joined", "jnread.cho", "                                if n != np {\n                                    if a[engine.k_ragged()] == 0 {\n                                        a[engine.k_first_row()] = st[s_probe()];\n                                        a[engine.k_first_line()] = number;", "                                if n != np && n == 0 {\n                                    if a[engine.k_ragged()] == 0 {\n                                        a[engine.k_first_row()] = st[s_probe()];\n                                        a[engine.k_first_line()] = number;"),
    ("the build is read one row past --max-rows", "jnread.cho", "                        } else if !quoted && a[engine.k_records()] >= most {", "                        } else if !quoted && a[engine.k_records()] >= most + 1 {"),
    ("the probe is not bounded by --max-rows", "jnread.cho", "                        } else if !quoted && st[s_probe()] >= most {", "                        } else if !quoted && st[s_probe()] >= most + 1000000000 {"),
    ("the probe header is read before the build's", "jnread.cho", "    let (rb1, hb) = jnhead.head_of(heap, rb, bfile, io, false, cap, delim);\n    rb = rb1;", "    let (rb1, hb) = jnhead.head_of(heap, rb, bfile, io, true, cap, delim);\n    rb = rb1;"),
    # the headers and the keys
    ("a name in both files is not refused", "jnhead.cho", "jnfail.collisions(pnames, pends, bnames, bends, prefix) > 0", "jnfail.collisions(pnames, pends, bnames, bends, prefix) > 100000"),
    ("the prefix is not put before the build's names", "jnhead.cho", "        names = buffer.append(heap, names, prefix);\n        names = buffer.append(heap, names, buffer.bytes(bnames)[from..to]);", "        names = buffer.append(heap, names, buffer.bytes(bnames)[from..to]);"),
    ("the prefix is not considered by the collision check", "jnfail.cho", "        if len(other) == len(prefix) + len(name) && bytes.starts_with(other, prefix) && bytes.equal(other[len(prefix)..len(other)], name) {", "        if bytes.equal(other, name) {"),
    ("the repair is offered when a prefix was given", "jnfail.cho", "        good = !given && collisions(pnames, pends, bnames, bends, buffer.bytes(sr)) == 0;", "        good = collisions(pnames, pends, bnames, bends, buffer.bytes(sr)) == 0;"),
    ("the repair is offered when it would collide", "jnfail.cho", "        good = !given && collisions(pnames, pends, bnames, bends, buffer.bytes(sr)) == 0;", "        good = !given;"),
    ("the repair's prefix is not the file's stem", "jnfail.cho", "    return buffer.push(heap, out, byte_of('_'));", "    return buffer.push(heap, out, byte_of('-'));"),
    ("a position names the column before it", "jnhead.cho", "            return (0, kind - 1);", "            return (0, kind);"),
    ("a position past the last column is a column", "jnhead.cho", "        if kind <= vec.size(ends) {", "        if kind <= vec.size(ends) + 1 {"),
    ("a name twice is the first", "jnhead.cho", "    if found > 1 {\n        return (2, 0);\n    }", "    if found > 100 {\n        return (2, 0);\n    }"),
    ("the build key is the probe's column", "jnhead.cho", "    bkey = bc;\n", "    bkey = 0;\n"),
    ("the probe key is the build's column", "jnhead.cho", "        pkey = pc;\n", "        pkey = 0;\n"),
    # the flags
    ("a type suffix is read as part of the name", "jnflags.cho", "        } else if c == ':' {\n            status = 3;", "        } else if c == ':' {\n            literal = c;"),
    ("a second pair is ignored", "jnflags.cho", "        } else if c == ',' {\n            status = 2;", "        } else if c == ',' {\n            literal = c;"),
    ("an escaped equals sign splits the pair", "jnflags.cho", "                if d == ',' || d == 92 || d == '=' || d == ':' || d == '#' {", "                if d == ',' || d == 92 || d == ':' || d == '#' {"),
    ("--on without --join is accepted", "jnflags.cho", '        if cli.has(parsed, table, "on") || cli.has(parsed, table, "join-prefix")', '        if cli.has(parsed, table, "on-never") || cli.has(parsed, table, "join-prefix")'),
    ("--join - is accepted", "jnflags.cho", '    if bytes.equal(cli.text(args, parsed, table, "join"), "-") {', '    if bytes.equal(cli.text(args, parsed, table, "join"), "--") {'),
    ("a grouping over a join is accepted", "jnflags.cho", "    if grouped || inp.has_order || inp.has_sort || inp.has_top || inp.has_report {", "    if inp.has_order || inp.has_sort || inp.has_top || inp.has_report {"),
    ("--max-join-rows has no ceiling", "jnflags.cho", "        if n < 1 || n > 1000000000 {", "        if n < 1 {"),
    ("--max-join-rows is ignored", "jnread.cho", '    let max_join = cli.nat(args, parsed, table, "max-join-rows");', "    let max_join = 10000000;"),
    ("a query beside a join is accepted", "table.cho", '    if inp.has_join {\n        clash = buffer.append(heap, clash, " --join");\n        clashed = true;\n    }', '    if inp.has_join && false {\n        clash = buffer.append(heap, clash, " --join");\n        clashed = true;\n    }'),
    # standard input, the root and the threads
    ("the probe on standard input is read as the file", "jnread.cho", "                if status == lines.need() {\n                    if stdin {\n                        var length = 0;", "                if status == lines.need() {\n                    if false {\n                        var length = 0;"),
    ("the header of a pipe is read as the file", "jnhead.cho", "        if status == lines.need() {\n            if stdin {", "        if status == lines.need() {\n            if false {"),
    ("a pipe is not given the root for the build file", "table.cho", 'buffer.bytes(rr), "-", io, fs, delim, mode, as_csv, from, limit, budget, top, tree, names_at, e, payload, plain);\n                        tree = t2;\n                        e = e2;\n                        payload = d2;\n                        plain = l2;\n                        streamed = s2;\n                    }\n                }', '"", "-", io, fs, delim, mode, as_csv, from, limit, budget, top, tree, names_at, e, payload, plain);\n                        tree = t2;\n                        e = e2;\n                        payload = d2;\n                        plain = l2;\n                        streamed = s2;\n                    }\n                }'),
    ("the join is not chosen", "table.cho", "    if joining {\n        return jnread.read(", "    if false {\n        return jnread.read("),
    ("a join is read by several threads as a file is", "explain.cho", '        if joining {\n            why = "the join is read on one core";\n        } else if stdin {', '        if stdin {'),
    ("a join is explained as a file", "table.cho", "        if joining {\n            let (t4, e4, d4) = jnexplain.settle(", "        if false {\n            let (t4, e4, d4) = jnexplain.settle("),
    ("explain forgets that a join reads the whole build first", "explain.cho", '        reads = "the whole build file, then the probe until the page is full";\n        if unbounded {', '        reads = "until the page is full";\n        if unbounded {'),
    ("the explain bound counts the key bytes once", "jnexplain.cho", "    return 2 * body + 76 * (body / least + 1);", "    return body + 76 * (body / least + 1);"),
    ("the explain says the file will pass", "jnexplain.cho", "    if most > budget {\n        o = buffer.append(heap, o, \"true\");", "    if most > budget + 1000000000 {\n        o = buffer.append(heap, o, \"true\");"),
    # the rules
    ("the collision has the wrong exit code", "finish.cho", "join.column-collision|2|sometimes|", "join.column-collision|8|sometimes|"),
    ("the join rows rule has the wrong exit code", "finish.cho", "limit.too-many-join-rows|8|never|", "limit.too-many-join-rows|2|never|"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
