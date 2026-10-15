#!/usr/bin/env python3
"""Mutation check of the join (docs/joins.md section 10, gate M; tests/conformance/test_join.py and test_join_j2.py): slices J1 (`--join FILE --on PROBE=BUILD`, text keys, inner) and J2 (left, semi and anti; typed keys; composite keys; USING; padding).

Each mutant is one defect in how a key is read or compared, how the chain of a key is kept or walked, how the joined row is made, what is counted against the bound, which file is
read first, or how a flag or a header is judged; the tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/join_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). See mutlib.py.

Not here: the `2 *` of the explain's upper bound (it is provable and, with `rows_at_most` as loose as it is, no file can tell the two forms apart); the byte between the probe record and the build record of the joined record (it is never read: the cells are offsets, so a mutant of it is equivalent); the bias of the 8 bytes of a typed key (the value plus 2^63: only the order of keys depends on it, and a join compares for equality); the 4-byte length written before a typed component of a composite key (any length that is the same on both sides gives the same equality); a one pair of text taken for a composite key (the general key of one text pair is the cell's bytes, as the fast path reads them: the two cannot be told apart by any answer, only by the instructions it takes); a key that is already held put in the table again by a semi or anti join (the first entry is the one a lookup finds, so no answer changes; the accounted bytes do not count it either way); the unquoting of a quoted typed cell (the reader's cell excludes the quotes, and a cell with a doubled quote left in it is no number either way, so the status is the same); the sign of a negative zero (`-0` is read as the integer 0, and the float reader makes `-0` the key of 0.0, so there is no code to break).
Not here either, because there is no such code (the slice that adds it adds its mutants, docs/joins.md section 11): a semi join that duplicates (a semi join holds the keys alone, and there is no chain to walk), the pushdown of a build condition for `left` and of a typed condition (J3), `--join-where` applied to the probe (J3), the swap repair offered for `left` (J7), the shared budget with the groups not subtracted (J3).
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

# cheap classes first: a killed mutant stops at the first failure (mutlib runs `unittest -f`)
TESTS = ["test_join.Keys", "test_join.Columns", "test_join.Flags", "test_join.DataRefusals", "test_join.Order", "test_join.MaxJoinRows", "test_join.Stdin", "test_join.Explain",
         "test_join_j2.Flags", "test_join_j2.Semantics", "test_join_j2.TypedRefusals", "test_join_j2.Explain", "test_join_j2.Threads", "test_join_j2.Oracle", "test_join.Oracle",
         "test_join_j2.Accounting", "test_join.Accounting", "test_join.Introspect", "test_rules", "test_authority", "test_skill"]

MUTANTS = [
    # how a key is read and compared
    ("a quoted build key keeps its quotes", "jnread.cho", "        if cells[3 * key + 2] == 0 {\n            let (t2, ok) = put(", "        if true {\n            let (t2, ok) = put("),
    ("a quoted probe key keeps its quotes", "jnread.cho", "        if cells[3 * pkey + 2] == 0 {\n            if jtype <= 1 {\n                chain = jtab.first(jt, prec[first..last]);", "        if true {\n            if jtype <= 1 {\n                chain = jtab.first(jt, prec[first..last]);"),
    ("an empty build key is not held", "jnread.cho", "        let last = cells[3 * key + 1];\n        if cells[3 * key + 2] == 0 {", "        let last = cells[3 * key + 1];\n        if last == first {\n            return (t, scratch, kbuf, 0);\n        }\n        if cells[3 * key + 2] == 0 {"),
    ("an empty probe key never matches", "jnread.cho", "        let last = cells[3 * pkey + 1];\n        if cells[3 * pkey + 2] == 0 {", "        let last = cells[3 * pkey + 1];\n        if last == first && cells[3 * pkey + 2] == 0 {\n            return (rows2, scratch2, escr2, kept2, jrec2, kscr2, kbuf2);\n        }\n        if cells[3 * pkey + 2] == 0 {"),
    ("the key is compared without its length", "jtab.cho", "    var cost = len(record) + 16;\n    if e < 0 {", "    var cost = len(record) + 16;\n    if e < 0 && len(key) > 0 {"),
    # the chain of a key
    ("a key's rows are walked from the last", "jtab.cho", "    return contents(t.head)[e];", "    return contents(t.tail)[e];"),
    ("a new row is linked at the head, not the tail", "jtab.cho", "        borrow mut nx as &!nw in {\n            contents(nw)[last] = row;\n        }", "        borrow mut nx as &!nw in {\n            contents(nw)[row] = last;\n        }"),
    ("only the first row of a chain is joined", "jnread.cho", "        x = jtab.next_row(jt, x);\n    }\n    return (rows2, scratch2, escr2, kept2, jrec2, kscr2, kbuf2);\n}", "        x = 0 - 1;\n    }\n    return (rows2, scratch2, escr2, kept2, jrec2, kscr2, kbuf2);\n}"),
    ("the chain's length is counted twice", "jnread.cho", "        matches = matches + 1;", "        matches = matches + 2;"),
    # the joined row
    ("the build cells are shifted one byte short", "jnread.cho", "cells[3 * (np + k)] = bcells[3 * k] + keep;", "cells[3 * (np + k)] = bcells[3 * k] + keep - 1;"),
    ("the build record is not cut back between matches", "jnread.cho", "            jw.used = keep;", "            jw.used = jw.used;"),
    ("an inner join pads its unmatched probe row as a left join does", "jnread.cho", "    if chain < 0 {\n        if jtype == 0 {", "    if chain < 0 {\n        if jtype == 9 {"),
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
    ("the build bound is off by one", "jtab.cho", "    if held + cost > limit {\n        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held }, false);\n    }\n    var ks = keys;\n    var hd = head;", "    if held + cost >= limit {\n        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held }, false);\n    }\n    var ks = keys;\n    var hd = head;"),
    ("a row's cost forgets its 16", "jtab.cho", "    var cost = len(record) + 16;", "    var cost = len(record) + 15;"),
    ("a key's cost forgets its 60", "jtab.cho", "        cost = cost + len(key) + 60;", "        cost = cost + len(key) + 59;"),
    ("a key's bytes are counted twice", "jtab.cho", "        cost = cost + len(key) + 60;", "        cost = cost + len(key) + len(key) + 60;"),
    ("a repeated key is counted as new", "jtab.cho", "    if e < 0 {\n        cost = cost + len(key) + 60;\n    }", "    if e < 1000000000 {\n        cost = cost + len(key) + 60;\n    }"),
    ("a refused row is held anyway", "jtab.cho", "        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held }, false);\n    }\n    var ks = keys;\n    var hd = head;", "        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held + cost }, false);\n    }\n    var ks = keys;\n    var hd = head;"),
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
    ("a name in both files is not refused", "jnhead.cho", "jnfail.collisions(pnames, pends, bnames, bends, prefix, contents(dr)) > 0", "jnfail.collisions(pnames, pends, bnames, bends, prefix, contents(dr)) > 100000"),
    ("the prefix is not put before the build's names", "jnhead.cho", "        names = buffer.append(heap, names, prefix);\n        names = buffer.append(heap, names, buffer.bytes(bnames)[from..vec.get(bends, col)]);", "        names = buffer.append(heap, names, buffer.bytes(bnames)[from..vec.get(bends, col)]);"),
    ("the prefix is not considered by the collision check", "jnfail.cho", "        if len(other) == len(prefix) + len(name) && bytes.starts_with(other, prefix) && bytes.equal(other[len(prefix)..len(other)], name) {", "        if bytes.equal(other, name) {"),
    ("the repair is offered when a prefix was given", "jnfail.cho", "        good = !given && collisions(pnames, pends, bnames, bends, buffer.bytes(sr), dropped) == 0;", "        good = collisions(pnames, pends, bnames, bends, buffer.bytes(sr), dropped) == 0;"),
    ("the repair is offered when it would collide", "jnfail.cho", "        good = !given && collisions(pnames, pends, bnames, bends, buffer.bytes(sr), dropped) == 0;", "        good = !given;"),
    ("the repair's prefix is not the file's stem", "jnfail.cho", "    return buffer.push(heap, out, byte_of('_'));", "    return buffer.push(heap, out, byte_of('-'));"),
    ("a position names the column before it", "jnhead.cho", "            return (0, kind - 1);", "            return (0, kind);"),
    ("a position past the last column is a column", "jnhead.cho", "        if kind <= vec.size(ends) {", "        if kind <= vec.size(ends) + 1 {"),
    ("a name twice is the first", "jnhead.cho", "    if found > 1 {\n        return (2, 0);\n    }", "    if found > 100 {\n        return (2, 0);\n    }"),
    ("the build key is column one", "jnhead.cho", "                    contents(kw)[3 * j + 1] = c2;", "                    contents(kw)[3 * j + 1] = 0;"),
    ("the probe key is column one", "jnhead.cho", "                        contents(kw)[3 * j] = c2;", "                        contents(kw)[3 * j] = 0;"),
    # the flags
    ("a colon is part of the name", "jnflags.cho", "            } else if c == ':' {\n                if insuffix {", "            } else if c == ':' && false {\n                if insuffix {"),
    ("a comma is part of the name", "jnflags.cho", "            } else if c == ',' {\n                cut = true;", "            } else if c == ',' {\n                literal = c;"),
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
    ("the explain says the file will pass", "jnexplain.cho", "    if most > budget {\n        o = buffer.append(heap, o, \"true\");", "    if most > budget + 1000000000 {\n        o = buffer.append(heap, o, \"true\");"),
    # J2: typed keys and composite keys (jnkey.cho)
    ("a typed key is compared as text", "jnhead.cho", "                    contents(kw)[3 * j + 2] = vec.get(onr.types, j);", "                    contents(kw)[3 * j + 2] = 0;"),
    ("a typed key keeps the bytes of the cell", "jnkey.cho", "                o = put_value_key(heap, o, v);", "                o = buffer.append(heap, o, record[first..last]);"),
    ("a decimal key is read as an integer", "jnkey.cho", "                let (x, y) = query.parse_typed(record[first..last], kind);", "                let (x, y) = query.parse_typed(record[first..last], 1);"),
    ("an empty typed cell is a zero", "jnkey.cho", "            if bad != 0 {\n                status = bad;", "            if bad != 0 && last > first {\n                status = bad;"),
    ("a refused typed cell names the first pair", "jnkey.cho", "                which = j;", "                which = 0;"),
    ("a composite text key has no length before a component", "jnkey.cho", "                if pairs > 1 {\n                    o = put_length(heap, o, last - first);\n                }", "                if pairs > 1 && false {\n                    o = put_length(heap, o, last - first);\n                }"),
    ("a composite key is its first pair", "jnkey.cho", "    while j < pairs && status == 0 {", "    while j < 1 && status == 0 {"),
    ("the second pair is read from the column of the first", "jnkey.cho", "        let column = keys[3 * j + side];", "        let column = keys[side];"),
    ("the probe's pairs are read from the build's columns", "jnread.cho", "jnkey.make(heap, kbuf2, kscr2, prec, cells, keys, st[s_pairs()], 0)", "jnkey.make(heap, kbuf2, kscr2, prec, cells, keys, st[s_pairs()], 1)"),
    ("a typed single pair is taken for text", "jnread.cho", "        simple = pairs == 1 && contents(kr0)[2] == 0;", "        simple = pairs == 1;"),
    # the refusal of a typed cell
    ("the build's refusal is not stopping the build", "jnread.cho", "                                    kept = note_typed(heap, kept, line, cells, keys, answer, 1, row, number, a);\n                                    going = false;", "                                    kept = note_typed(heap, kept, line, cells, keys, answer, 1, row, number, a);"),
    ("the probe's refusal is not stopping the probe", "jnread.cho", "            kept2 = note_typed(heap, kept2, prec, cells, keys, 16 + 16 * which + status, 0, row, number, a);\n            a[engine.k_stop()] = 1;", "            kept2 = note_typed(heap, kept2, prec, cells, keys, 16 + 16 * which + status, 0, row, number, a);"),
    ("a refused key names the row after", "jnread.cho", "    a[engine.k_err_row()] = row;\n    a[engine.k_err_line()] = line;", "    a[engine.k_err_row()] = row + 1;\n    a[engine.k_err_line()] = line;"),
    ("a refused key names the other file's column", "jnread.cho", "    let column = keys[3 * which + side];\n    a[engine.k_abort()] = 72;", "    let column = keys[3 * which + 1 - side];\n    a[engine.k_abort()] = 72;"),
    ("a refused probe key says build", "jnread.cho", 'bkr in {\n                        e = typed_refusal(heap, e, contents(kr2), bc, bkr, shown_b, "build", hbr.names, hbr.ends);', 'bkr in {\n                        e = typed_refusal(heap, e, contents(kr2), bc, bkr, shown_b, "probe", hbr.names, hbr.ends);'),
    ("a refused probe key names the build header", "jnread.cho", 'borrow hp as &hpr in {\n                    borrow kept as &vr in {\n                        e = typed_refusal(heap, e, contents(kr3), c, vr, shown, "probe", hpr.names, hpr.ends);', 'borrow hb as &hpr in {\n                    borrow kept as &vr in {\n                        e = typed_refusal(heap, e, contents(kr3), c, vr, shown, "probe", hpr.names, hpr.ends);'),
    ("the digits of a decimal that is too fine are not counted", "jnread.cho", "        a[engine.k_err_digits()] = jnkey.digits_after_point(record, cells[3 * column], cells[3 * column + 1]);", "        a[engine.k_err_digits()] = 0;"),
    ("the pair of a refusal is counted from zero", "jnread.cho", "c.err_cond + 1, keys[3 * c.err_cond + 2] - 2, c.err_digits);", "c.err_cond, keys[3 * c.err_cond + 2] - 2, c.err_digits);"),
    ("the scale of a refusal is the type's code", "jnread.cho", "c.err_cond + 1, keys[3 * c.err_cond + 2] - 2, c.err_digits);", "c.err_cond + 1, keys[3 * c.err_cond + 2], c.err_digits);"),
    ("a not-decimal key is refused as an integer", "jnfail.cho", "    } else if status == 6 {\n        rule = \"value.not-decimal\";", "    } else if status == 66 {\n        rule = \"value.not-decimal\";"),
    ("a too-fine decimal key is refused as an integer", "jnfail.cho", "    } else if status == 7 {\n        rule = \"value.decimal-scale\";", "    } else if status == 77 {\n        rule = \"value.decimal-scale\";"),
    ("a too-wide decimal key is refused as an integer", "jnfail.cho", "    } else if status == 8 {\n        rule = \"value.decimal-too-wide\";", "    } else if status == 88 {\n        rule = \"value.decimal-too-wide\";"),
    ("a non-finite float key is refused as not a float", "jnfail.cho", "    } else if status == 10 {\n        rule = \"value.not-finite\";", "    } else if status == 100 {\n        rule = \"value.not-finite\";"),
    ("an integer overflow key is refused as not an integer", "jnfail.cho", "    if status == 5 {\n        rule = \"value.integer-overflow\";", "    if status == 55 {\n        rule = \"value.integer-overflow\";"),
    ("a float range refusal has no direction", "jnfail.cho", "    if status == 11 {\n        o = fail.detail_str(heap, o, \"direction\", \"overflow\");", "    if status == 111 {\n        o = fail.detail_str(heap, o, \"direction\", \"overflow\");"),
    ("a too-long float key is refused as not a float", "jnfail.cho", "    } else if status == 13 {\n        rule = \"limit.number-too-long\";", "    } else if status == 133 {\n        rule = \"limit.number-too-long\";"),
    # left, semi, anti
    ("a left join drops its unmatched probe row", "jnread.cho", "    if chain < 0 {\n        if jtype == 0 {", "    if chain < 0 {\n        if jtype <= 1 {"),
    ("a left join pads one cell short", "jnread.cho", "        var k = 0;\n        while k < nk {\n            jrec2 = buffer.push(heap, jrec2, byte_of(delim));", "        var k = 0;\n        while k + 1 < nk {\n            jrec2 = buffer.push(heap, jrec2, byte_of(delim));"),
    ("a padded cell is a copy of the key", "jnread.cho", "            cells[3 * (np + k)] = len(prec) + 1 + k;\n            cells[3 * (np + k) + 1] = len(prec) + 1 + k;", "            cells[3 * (np + k)] = cells[0];\n            cells[3 * (np + k) + 1] = cells[1];"),
    ("an anti join is inverted", "jnread.cho", "        if jtype == 2 && present || jtype == 3 && !present {", "        if jtype == 2 && present || jtype == 3 && present {"),
    ("a semi join keeps the rows with no match", "jnread.cho", "        if jtype == 2 && present || jtype == 3 && !present {", "        if jtype == 2 && !present || jtype == 3 && !present {"),
    ("the facts of a semi row bound say the wrong matches", "jnread.cho", "max_join, 3 - jtype, false, np);", "max_join, 0, false, np);"),
    ("a padded row is not counted against --max-join-rows", "jnread.cho", "    st[s_produced()] = st[s_produced()] + 1;\n    a[engine.k_records()] = st[s_produced()];\n    if st[s_produced()] <= from {", "    a[engine.k_records()] = st[s_produced()];\n    if st[s_produced()] <= from {"),
    ("--from counts one padded row too few", "jnread.cho", "    if st[s_produced()] <= from {", "    if st[s_produced()] < from {"),
    ("--max-join-rows is off by one for padded and semi rows", "jnread.cho", "    if st[s_produced()] >= max_join {\n        a[engine.k_abort()] = 71;\n        a[engine.k_stop()] = 1;\n        a[engine.k_err_row()] = st[s_probe()];\n        a[engine.k_err_line()] = number;\n        a[engine.k_err_digits()] = matches;", "    if st[s_produced()] > max_join {\n        a[engine.k_abort()] = 71;\n        a[engine.k_stop()] = 1;\n        a[engine.k_err_row()] = st[s_probe()];\n        a[engine.k_err_line()] = number;\n        a[engine.k_err_digits()] = matches;"),
    ("a refused padded read is not flagged", "jnread.cho", "            a[engine.k_err_fn()] = 77;", "            a[engine.k_err_fn()] = 0 - 1;"),
    ("a refused read of the probe cell of a padded row is flagged", "jnread.cho", "a[engine.k_err_fn()] == 0 - 1 && a[engine.k_err_col()] >= np {", "a[engine.k_err_fn()] == 0 - 1 && a[engine.k_err_col()] >= 0 {"),
    ("a refused read of a matched row is flagged padded", "jnread.cho", "max_join, matches, false, np);", "max_join, matches, true, np);"),
    ("an integer refusal does not say padded", "finish.cho", "        w = fail.detail_bool(heap, w, \"value_truncated\", buffer.size(kept) >= 64);\n        if c.err_fn == 77 {", "        w = fail.detail_bool(heap, w, \"value_truncated\", buffer.size(kept) >= 64);\n        if c.err_fn == 78 {"),
    ("a float refusal does not say padded", "finish.cho", "            w = fail.detail_str(heap, w, \"direction\", \"underflow\");\n        }\n        if c.err_fn == 77 {", "            w = fail.detail_str(heap, w, \"direction\", \"underflow\");\n        }\n        if c.err_fn == 78 {"),
    ("a decimal refusal does not say padded", "finish.cho", "            w = fail.detail_int(heap, w, \"digits\", digits);\n        }\n        if c.err_fn == 77 {", "            w = fail.detail_int(heap, w, \"digits\", digits);\n        }\n        if c.err_fn == 78 {"),
    # the build of a semi or anti join: keys only (jtab.add_key)
    ("a semi build holds the rows", "jnread.cho", "simple, jtype >= 2, delim, cap, most, max_state);", "simple, false, delim, cap, most, max_state);"),
    ("a key alone costs its 60", "jtab.cho", "        cost = len(key) + 44;", "        cost = len(key) + 60;"),
    ("a key alone is counted again when it repeats", "jtab.cho", "    var cost = 0;\n    if e < 0 {\n        cost = len(key) + 44;\n    }", "    var cost = 0;\n    if e < 1000000000 {\n        cost = len(key) + 44;\n    }"),
    ("the keys-only bound is off by one", "jtab.cho", "    if held + cost > limit {\n        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held }, false);\n    }\n    var ks = keys;\n    if e < 0 {", "    if held + cost >= limit {\n        return (JTab { keys: keys, head: head, tail: tail, rows: rows, start: start, next: next, nrows: nrows, held: held }, false);\n    }\n    var ks = keys;\n    if e < 0 {"),
    ("has answers true for a key that is not there", "jtab.cho", "    return gmap.find_h(t.keys, key, gmap.hash(key)) >= 0;", "    return gmap.find_h(t.keys, key, gmap.hash(key)) >= -1;"),
    # USING and the columns of the answer
    ("USING drops the last column", "jnhead.cho", "                        contents(dw)[c2] = 1;", "                        contents(dw)[nb - 1] = 1;"),
    ("USING drops nothing", "jnhead.cho", "                if vec.get(onr.using, j) == 1 {\n                    borrow mut dropped", "                if vec.get(onr.using, j) == 7 {\n                    borrow mut dropped"),
    ("a dropped column still collides", "jnfail.cho", "        if dropped[j] == 0 && collides(pnames, pends, bnames, bends, j, prefix) {\n            n = n + 1;", "        if collides(pnames, pends, bnames, bends, j, prefix) {\n            n = n + 1;"),
    ("a dropped column is listed in the collision", "jnfail.cho", "        if dropped[j] == 0 && collides(pnames, pends, bnames, bends, j, prefix) {\n            found = found + 1;", "        if collides(pnames, pends, bnames, bends, j, prefix) {\n            found = found + 1;"),
    ("the cells of the build are the columns in order when some are dropped", "jnread.cho", "            cells[3 * (np + k)] = bcells[3 * kmap[k]] + keep;", "            cells[3 * (np + k)] = bcells[3 * k] + keep;"),
    ("the fast probe is taken when a column is left out", "jnread.cho", "    let fast = jtype == 0 && simple && nk == nb;", "    let fast = jtype == 0 && simple;"),
    ("a semi join checks the build's names for a collision", "jnhead.cho", "    if ok && probe_known && jtype <= 1 {", "    if ok && probe_known && jtype <= 3 {"),
    ("a semi join shows the build's names", "jnhead.cho", "    if jtype <= 1 {\n        j = 0;\n        borrow dropped as &dr in {", "    if jtype <= 3 {\n        j = 0;\n        borrow dropped as &dr in {"),
    ("a shared name that is a position is accepted", "jnflags.cho", "                    if lkind >= 1 {\n                        status = 9;", "                    if lkind >= 1000000000 {\n                        status = 9;"),
    # the grammar of --on
    ("a type before the equals sign is accepted", "jnflags.cho", "                if insuffix {\n                    status = 3;", "                if insuffix && false {\n                    status = 3;"),
    ("two types are accepted", "jnflags.cho", "                if insuffix {\n                    status = 8;", "                if insuffix && false {\n                    status = 8;"),
    ("a scale of 19 is accepted", "jnflags.cho", "        if n <= 18 {\n            return 2 + n;", "        if n <= 99 {\n            return 2 + n;"),
    ("a scale with a leading zero is accepted", "jnflags.cho", "        if len(digits) == 2 && int_of(digits[0]) == '0' {\n            return 0;\n        }", ""),
    ("an unknown type is a text key", "jnflags.cho", "                if t == 0 {\n                    status = 7;\n                }", ""),
    ("nine pairs are accepted", "jnflags.cho", "            if status == 0 && pairs == 8 {", "            if status == 0 && pairs == 80 {"),
    ("an empty pair is accepted", "jnflags.cho", "            if lsize == 0 || side == 1 && rsize == 0 {\n                status = 6;", "            if lsize == 0 && false || side == 1 && rsize == 0 {\n                status = 6;"),
    ("a prefix with a semi join is accepted", "jnflags.cho", '        if bytes.equal(kind, "semi") || bytes.equal(kind, "anti") {', '        if bytes.equal(kind, "semi-never") || bytes.equal(kind, "anti") {'),
    ("an anti join is read as inner", "jnread.cho", '    if bytes.equal(word, "anti") {\n        return 3;\n    }', '    if bytes.equal(word, "anti-never") {\n        return 3;\n    }'),
    ("a left join is read as inner", "jnread.cho", '    if bytes.equal(word, "left") {\n        return 1;\n    }', '    if bytes.equal(word, "left-never") {\n        return 1;\n    }'),
    ("explain reads a left join as inner", "jnexplain.cho", '    if bytes.equal(word, "left") {\n        jtype = 1;', '    if bytes.equal(word, "left-never") {\n        jtype = 1;'),
    # explain
    ("explain says text for a typed key", "jnexplain.cho", "            o = type_name(heap, o, keys[3 * j + 2]);", "            o = type_name(heap, o, 0);"),
    ("explain names the wrong build column", "jnexplain.cho", "            o = buffer.push_nat(heap, o, keys[3 * j + 1] + 1);", "            o = buffer.push_nat(heap, o, keys[3 * j] + 1);"),
    ("explain forgets the keys-only bound", "jnexplain.cho", "    if keys_only {\n        return body + (44 + extra) * rows;", "    if false {\n        return body + (44 + extra) * rows;"),
    ("explain forgets that a typed key is longer than its cell", "jnexplain.cho", "        extra = 12 * pairs;", "        extra = 0;"),
    ("explain lists the shared names of a semi join", "jnexplain.cho", "            if vec.get(onr.using, j) == 1 && jtype <= 1 {", "            if vec.get(onr.using, j) == 1 {"),
    ("explain counts the dropped columns", "jnexplain.cho", "        o = buffer.push_nat(heap, o, np + nk);", "        o = buffer.push_nat(heap, o, np + nb);"),
    ("explain says the bound is the inner one for a keys-only build", "jnexplain.cho", "    if jtype >= 2 {\n        o = buffer.append(heap, o, \",\\\"counted\\\":\\\"key bytes + 44", "    if jtype >= 20 {\n        o = buffer.append(heap, o, \",\\\"counted\\\":\\\"key bytes + 44"),
    # the build and the probe of J1 (hold_text, probe_inner), which are kept as J1 had them: the same mutants, against the code that runs for an inner join of one pair of text
    ("a quoted build key keeps its quotes (the inner build)", "jnread.cho", "    if cells[3 * key + 2] == 0 {\n        let (t2, ok) = jtab.add(", "    if true {\n        let (t2, ok) = jtab.add("),
    ("an empty build key is not held (the inner build)", "jnread.cho", "    let last = cells[3 * key + 1];\n    if cells[3 * key + 2] == 0 {", "    let last = cells[3 * key + 1];\n    if last == first {\n        return (t, scratch, true);\n    }\n    if cells[3 * key + 2] == 0 {"),
    ("a quoted probe key keeps its quotes (the inner probe)", "jnread.cho", "    if cells[3 * pkey + 2] == 0 {\n        chain = jtab.first(", "    if true {\n        chain = jtab.first("),
    ("an empty probe key never matches (the inner probe)", "jnread.cho", "    var chain = 0 - 1;\n    if cells[3 * pkey + 2] == 0 {", "    var chain = 0 - 1;\n    if last == first && cells[3 * pkey + 2] == 0 {\n        return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n    }\n    if cells[3 * pkey + 2] == 0 {"),
    ("only the first row of a chain is joined (the inner probe)", "jnread.cho", "        x = jtab.next_row(jt, x);\n    }\n    return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n}", "        x = 0 - 1;\n    }\n    return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n}"),
    ("an unmatched probe row is joined to the first build row (the inner probe)", "jnread.cho", "    if chain < 0 {\n        return (rows2, scratch2, escr2, kept2, jrec2, kscr2);\n    }\n    var matches = 0;", "    if chain < 0 {\n        chain = 0;\n    }\n    var matches = 0;"),
    ("the inner build is not taken for one pair of text", "jnread.cho", "    let bfast = simple && !keys_only;", "    let bfast = simple && !keys_only && false;"),
    ("the inner probe is taken for a left join", "jnread.cho", "    let fast = jtype == 0 && simple && nk == nb;", "    let fast = jtype <= 1 && simple && nk == nb;"),
    # the rules
    ("the collision has the wrong exit code", "finish.cho", "join.column-collision|2|sometimes|", "join.column-collision|8|sometimes|"),
    ("the join rows rule has the wrong exit code", "finish.cho", "limit.too-many-join-rows|8|never|", "limit.too-many-join-rows|2|never|"),
]

if __name__ == "__main__":
    sys.exit(mutlib.main(MUTANTS, TESTS))
