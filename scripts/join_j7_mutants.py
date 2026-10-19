#!/usr/bin/env python3
"""Mutation check of slice J7 (docs/joins.md section 22; tests/conformance/test_join_j7.py and test_join_j7_prune.py): the swap repair of `limit.state-too-large` and the build that keeps only the columns the plan names.

Each mutant is one defect in how the columns a plan names are found (the mask), how a pruned record is made and counted, which build passes prune, when the exchange of the two files is offered
(the join type, the probe, the sizes, the flags that stop it), how the exchanged call is written (the files, the pairs and their types, the reader flags, the header kept by `--select`), whether
the other file is read to see that it fits, and what `--explain` says; the tool is rebuilt from it and run against the tests. A mutant is killed when the build refuses it or a test fails.

    python3 scripts/join_j7_mutants.py [--shard K/N] [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). `--shard K/N` runs the K-th of N interleaved parts of the list (each part needs a checkout of its own: the runner edits
tools/table and builds into build/). See mutlib.py.

Not here (equivalent mutants, each argued):

* the mask's last entry forced to 1 (pruning "on" for a plan that names every column): the pruned record of a plan that names every column is the whole record byte for byte (every raw field,
  a delimiter between every two), so the held bytes, the count and the answer are the same; only the work differs.
* `append_pruned` and `add_pruned` agree on what is kept by the same mask, so a defect in one alone shows as a wrong answer or wrong count; a mutant that makes BOTH keep every column is a build that
  does not prune: it is the memory test's, not the answer's (`M` below kills it).
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

# cheap classes first: a killed mutant stops at the first failure (mutlib runs `unittest -f`)
TESTS = ["test_join_j7.NotOffered", "test_join_j7.Explain", "test_join_j7.Boundary", "test_join_j7.Mechanical", "test_join_j7_prune.Accounting", "test_join_j7_prune.Memory", "test_join_j7.Fuzz",
         "test_join_j7_prune.Identity", "test_join.Accounting", "test_join_j2.Accounting", "test_join_j3.Accounting", "test_join_j5.Accounting", "test_join_j5.Oracle"]

JNPRUNE = "jnprune.cho"
JTAB = "jtab.cho"
JNSWAP = "jnswap.cho"
JNRUN = "jnrun.cho"

MUTANTS = [
    # which columns the plan names
    ("a column a condition, group or order reads is not named", JNPRUNE, "            if cols[i] >= np {\n                m[kmap[cols[i] - np]] = 1;", "            if cols[i] >= np + 1000000 {\n                m[kmap[cols[i] - np]] = 1;"),
    ("a column the answer shows is not named", JNPRUNE, "            if sel[i] >= np {\n                m[kmap[sel[i] - np]] = 1;", "            if sel[i] >= np + 1000000 {\n                m[kmap[sel[i] - np]] = 1;"),
    ("the first build column is never named by a name of the plan", JNPRUNE, "            if cols[i] >= np {\n", "            if cols[i] > np {\n"),
    ("the first build column is never named by the answer", JNPRUNE, "            if sel[i] >= np {\n", "            if sel[i] > np {\n"),
    ("a named build column is looked up without the columns a shared-name key leaves out", JNPRUNE, "m[kmap[cols[i] - np]] = 1;", "m[cols[i] - np] = 1;"),
    ("a shown build column is looked up without the columns a shared-name key leaves out", JNPRUNE, "m[kmap[sel[i] - np]] = 1;", "m[sel[i] - np] = 1;"),
    ("pruning is never on", JNPRUNE, "        m[nb] = unnamed;", "        m[nb] = 0;"),
    ("a semi or anti join is pruned as if it held rows", JNRUN, "    if go && jtype <= 1 {\n        unbox_slice(heap, pmask);", "    if go {\n        unbox_slice(heap, pmask);"),
    # the pruned record and what it costs
    ("the delimiters of a pruned record are one short", JTAB, "    var n = nb - 1;\n    var j = 0;", "    var n = nb - 2;\n    var j = 0;"),
    ("a quoted cell is counted without its quotes", JTAB, "            n = n + (cells[3 * j + 1] + q) - (cells[3 * j] - q);", "            n = n + cells[3 * j + 1] - cells[3 * j];"),
    ("a pruned record is held without its delimiters", JTAB, "        if j > 0 {\n            out = buffer.push(heap, out, byte_of(delim));\n        }\n        if mask[j] == 1 {", "        if mask[j] == 1 {"),
    ("a quoted cell is held without its quotes", JTAB, "            out = buffer.append(heap, out, record[cells[3 * j] - q..cells[3 * j + 1] + q]);", "            out = buffer.append(heap, out, record[cells[3 * j]..cells[3 * j + 1]]);"),
    ("a pruned row costs 15 and not 16 beside its record", JTAB, "    var cost = pruned_len(record, cells, mask) + 16;", "    var cost = pruned_len(record, cells, mask) + 15;"),
    ("a new key of a pruned build costs 59 and not 60", JTAB, "    var cost = pruned_len(record, cells, mask) + 16;\n    if e < 0 {\n        cost = cost + len(key) + 60;", "    var cost = pruned_len(record, cells, mask) + 16;\n    if e < 0 {\n        cost = cost + len(key) + 59;"),
    ("the bound of a pruned build is off by one", JTAB, "    var cost = pruned_len(record, cells, mask) + 16;\n    if e < 0 {\n        cost = cost + len(key) + 60;\n    }\n    if held + cost > limit {", "    var cost = pruned_len(record, cells, mask) + 16;\n    if e < 0 {\n        cost = cost + len(key) + 60;\n    }\n    if held + cost >= limit {"),
    ("a pruned build holds the whole record (and counts the pruned one)", JTAB, "    rw = append_pruned(heap, rw, record, cells, mask, delim);", "    rw = buffer.append(heap, rw, record);"),
    ("a pruned build counts the whole record", JTAB, "    var cost = pruned_len(record, cells, mask) + 16;", "    var cost = len(record) + 16;"),
    ("a quoted key of a pruned build keeps its quotes", JNPRUNE, "        if cells[3 * key + 2] == 0 {\n            let (t2, ok) = jtab.add_pruned(", "        if true {\n            let (t2, ok) = jtab.add_pruned("),
    ("a pruned build of several pairs reads the key from the pruned record", JNPRUNE, "    let (kb, sc, status, which) = jnkey.make(heap, kbuf, scratch, record, cells, keys, pairs, 1);", "    let (kb, sc, status, which) = jnkey.make(heap, kbuf, scratch, record, cells, keys, pairs, 2);"),
    # which build passes prune
    ("the build of a plain join does not prune", "jnread.cho", "                                if prune {\n                                    let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, line,", "                                if false {\n                                    let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, line,"),
    ("a build of a multi-line record does not prune", "jnread.cho", "                                        if prune {\n                                            let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, record,", "                                        if false {\n                                            let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, record,"),
    ("the build with a --join-where does not prune", "jn3pass.cho", "                                } else if prune {\n                                    let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, line,", "                                } else if false {\n                                    let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, line,"),
    ("a multi-line record of the build with a --join-where is not pruned", "jn3pass.cho", "                                        } else if prune {\n                                            let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, record,", "                                        } else if false {\n                                            let (t2, k2, b2, answer2) = jnprune.hold_row(heap, jt, kscr, kbuf, record,"),
    ("the build of a JSON-lines or foreign side does not prune", "jnjson.cho", "    if mask[len(mask) - 1] == 1 {", "    if mask[len(mask) - 1] == 9 {"),
    ("the pruning is inverted in the plain build", "jnread.cho", "    let prune = mask[nb] == 1;", "    let prune = mask[nb] == 0;"),
    # the exchange: when it is offered
    ("an exchange is offered for a left join", JNSWAP, "    if f.jtype != 0 {\n        return 1;", "    if f.jtype > 1 {\n        return 1;"),
    ("an exchange is offered for a semi join", JNSWAP, "    if f.jtype != 0 {\n        return 1;", "    if f.jtype == 1 || f.jtype == 3 {\n        return 1;"),
    ("an exchange is offered for an anti join", JNSWAP, "    if f.jtype != 0 {\n        return 1;", "    if f.jtype == 1 || f.jtype == 2 {\n        return 1;"),
    ("an exchange is offered for a probe on standard input", JNSWAP, "    if f.stdin {\n        return 2;", "    if false {\n        return 2;"),
    ("an exchange is offered for a --query", JNSWAP, "    if f.queried {\n        return 3;", "    if false {\n        return 3;"),
    ("an exchange is offered for a probe of the same size", JNSWAP, "f.psize >= f.bsize {", "f.psize > f.bsize {"),
    ("an exchange is offered for a probe that is larger", JNSWAP, "f.psize >= f.bsize {", "f.psize >= f.bsize + 1000000000 {"),
    ("an exchange is offered when a size is not known", JNSWAP, "    if f.psize < 0 || f.bsize < 0 || f.psize >= f.bsize {", "    if f.psize >= f.bsize {"),
    ("an exchange is offered with a --join-prefix", JNSWAP, "    if f.prefixed {\n        return 5;", "    if false {\n        return 5;"),
    ("an exchange is offered with a --join-where", JNSWAP, "    if f.join_where {\n        return 6;", "    if false {\n        return 6;"),
    ("an exchange is offered when a column is named by position", JNSWAP, "    if f.positions {\n        return 7;", "    if false {\n        return 7;"),
    ("an exchange is offered for a typed shared-name key", JNSWAP, "    if f.typed_using {\n        return 8;", "    if false {\n        return 8;"),
    ("an exchange is offered for a page of rows", JNSWAP, "    if f.paged {\n        return 9;", "    if false {\n        return 9;"),
    ("an exchange is offered for a header with a name twice", JNSWAP, "    if f.dup {\n        return 10;", "    if false {\n        return 10;"),
    ("an exchange is offered when the delimiter of a csv answer would change", JNSWAP, "    if f.dchange {\n        return 11;", "    if false {\n        return 11;"),
    ("a grouping is taken for a page of rows", JNSWAP, "    if mode != 1 {\n        return false;\n    }", "    if mode > 2 {\n        return false;\n    }"),
    ("--top does not make a page", JNSWAP, '    return cli.has(parsed, table, "limit") || cli.has(parsed, table, "top") || cli.has(parsed, table, "from");', '    return cli.has(parsed, table, "limit") || cli.has(parsed, table, "from");'),
    ("--from does not make a page", JNSWAP, '    return cli.has(parsed, table, "limit") || cli.has(parsed, table, "top") || cli.has(parsed, table, "from");', '    return cli.has(parsed, table, "limit") || cli.has(parsed, table, "top");'),
    ("--limit does not make a page", JNSWAP, '    return cli.has(parsed, table, "limit") || cli.has(parsed, table, "top") || cli.has(parsed, table, "from");', '    return cli.has(parsed, table, "top") || cli.has(parsed, table, "from");'),
    ("an empty name is not a name twice", JNSWAP, "        if to == from {\n            return true;\n        }", "        if to < from {\n            return true;\n        }"),
    # the other file must fit
    ("the other file is not read to see that it fits", JNRUN, "                    if fit.abort == 70 {", "                    if fit.abort == 7000 {"),
    ("the other file is held within one byte more than the flag", JNRUN, "cap, most, max_state, jq3, contents(jcr3), tree, contents(clr3), contents(bcr3), contents(m2r));", "cap, most, max_state + 1, jq3, contents(jcr3), tree, contents(clr3), contents(bcr3), contents(m2r));"),
    ("the other file is held with its whole record", JNSWAP, "    var mask = box_slice(heap, np + 1, 0);\n    borrow mut mask as &!mw in {\n        let m = contents(mw);\n        var i = 0;\n        while i < names {\n            if cols[i] < np {", "    var mask = box_slice(heap, np + 1, 0);\n    borrow mut mask as &!mw in {\n        let m = contents(mw);\n        var i = 0;\n        while i < names {\n            if cols[i] < np + 1000000 {"),
    ("the other file's key column is held when a shared-name key leaves it out", JNSWAP, "            if using[j] == 1 {\n                m[keys[3 * j]] = 0;", "            if using[j] == 9 {\n                m[keys[3 * j]] = 0;"),
    ("the other file is read with the keys the same way round", JNSWAP, "            o[3 * j] = keys[3 * j + 1];\n            o[3 * j + 1] = keys[3 * j];", "            o[3 * j] = keys[3 * j];\n            o[3 * j + 1] = keys[3 * j + 1];"),
    ("the other file's columns the answer shows are not named", JNSWAP, "            if sel[i] < np {\n                m[sel[i]] = 1;", "            if sel[i] < 0 {\n                m[sel[i]] = 1;"),
    ("a read of the other file that is refused is taken for a fit", JNRUN, "                    } else if fit.abort != 0 || fit.ragged > 0 || fit.capped {", "                    } else if fit.abort == 7000 {"),
    ("a ragged row of the other file does not stop the exchange", JNRUN, "fit.abort != 0 || fit.ragged > 0 || fit.capped {", "fit.abort != 0 || fit.capped {"),
    ("a probe cut by --max-rows does not stop the exchange", JNRUN, "fit.abort != 0 || fit.ragged > 0 || fit.capped {", "fit.abort != 0 || fit.ragged > 0 {"),
    # the exchanged call
    ("the files are not exchanged: the operand stays", JNSWAP, "                contents(kw2)[oi] = 2;", "                contents(kw2)[oi] = 0;"),
    ("the pairs of --on are not read the other way", JNSWAP, "                out = put_key_name(heap, out, onr, 2 * j + 1);\n                out = buffer.push(heap, out, byte_of('='));\n                out = put_key_name(heap, out, onr, 2 * j);", "                out = put_key_name(heap, out, onr, 2 * j);\n                out = buffer.push(heap, out, byte_of('='));\n                out = put_key_name(heap, out, onr, 2 * j + 1);"),
    ("the type of a pair does not go with it", JNSWAP, "        out = put_type(heap, out, vec.get(onr.types, j));", "        j = j + 0;"),
    ("a shared-name pair is read as two names", JNSWAP, "            if vec.get(onr.using, j) == 1 {\n                out = put_name(heap, out, jnflags.name_at(onr, 2 * j), true);", "            if vec.get(onr.using, j) == 9 {\n                out = put_name(heap, out, jnflags.name_at(onr, 2 * j), true);"),
    ("a position in --on is written as its name", JNSWAP, "    if kind >= 0 {\n        var b = buffer.push(heap, out, byte_of('#'));", "    if kind >= 1000000 {\n        var b = buffer.push(heap, out, byte_of('#'));"),
    ("a = in a name of --on is not escaped", JNSWAP, "        if c == ',' || c == '\\\\' || c == '#' && i == 0 || eq && c == '=' {", "        if c == ',' || c == '\\\\' || c == '#' && i == 0 {"),
    ("a leading # in a name is not escaped", JNSWAP, "        if c == ',' || c == '\\\\' || c == '#' && i == 0 || eq && c == '=' {", "        if c == ',' || c == '\\\\' || eq && c == '=' {"),
    ("the original header is not said by --select", JNSWAP, "    if say_header {\n        let list = select_list(heap, jnames, jends);", "    if false {\n        let list = select_list(heap, jnames, jends);"),
    ("the original header is said by --select even when the plan selects", JNRUN, "            if mode == 1 && query.count_of(tree, 0) == 0 {\n                say_header = true;", "            if mode == 1 {\n                say_header = true;"),
    ("a comma in a name of --select is not escaped", JNSWAP, "        if c == ',' || c == '\\\\' || c == '#' && i == 0 || eq && c == '=' {", "        if c == '\\\\' || c == '#' && i == 0 || eq && c == '=' {"),
    ("the reader flags of the call are kept", JNSWAP, '        drop_flag(parsed, "input-format", kd);', "        kd[0] = kd[0];"),
    ("the --join-fields of the call are kept", JNSWAP, '        drop_flag(parsed, "join-fields", kd);', "        kd[0] = kd[0];"),
    ("the --join-delimiter of the call is kept", JNSWAP, '        drop_flag(parsed, "join-delimiter", kd);', "        kd[0] = kd[0];"),
    ("the --delimiter of the call is kept", JNSWAP, '        drop_flag(parsed, "delimiter", kd);', "        kd[0] = kd[0];"),
    ("the build's format is not given to the probe", JNSWAP, '    if nj {\n        extra = put_flag(heap, extra, "--input-format", "jsonl");', '    if false {\n        extra = put_flag(heap, extra, "--input-format", "jsonl");'),
    ("the build's fields are not given to the probe", JNSWAP, '        extra = put_flag(heap, extra, "--fields", fmt.b_fields);', '        extra = put_flag(heap, extra, "--fields", fmt.p_fields);'),
    ("the probe's format is not given to the build", JNSWAP, "    if mj != nj {\n        if mj {", "    if false {\n        if mj {"),
    ("the probe's fields are not given to the build", JNSWAP, "    if mj && (!nj || !bytes.equal(fmt.p_fields, fmt.b_fields)) {", "    if false {"),
    ("the build's delimiter is not given to the probe", JNSWAP, "    if !fmt.b_jsonl {\n        nd = bdelim;\n    }\n    let mj", "    let mj"),
    ("the probe's delimiter is not given to the build", JNSWAP, "    if !mj && delim != nd {", "    if false {"),
    ("a delimiter that is not the default is not said", JNSWAP, '    if nd != 44 || cli.has(parsed, table, "delimiter") {', '    if cli.has(parsed, table, "delimiter") {'),
    ("--join=VALUE is written as --join VALUE", JNSWAP, '    if !whole_join {\n        rep = buffer.append(heap, rep, "--join=");', '    if false {\n        rep = buffer.append(heap, rep, "--join=");'),
    ("--on=VALUE is written as --on VALUE", JNSWAP, '    if !whole_on {\n        rep = buffer.append(heap, rep, "--on=");', '    if false {\n        rep = buffer.append(heap, rep, "--on=");'),
    # what is said
    ("an offer that is made is not written", "jnfail.cho", "    if offer.offered {\n        var c = fail.choose_open(heap, o);", "    if false {\n        var c = fail.choose_open(heap, o);"),
    ("an exchange is checked and then not offered", JNRUN, "                                if wrote {\n                                    said = jnswap.Offer { offered: true,", "                                if false {\n                                    said = jnswap.Offer { offered: true,"),
    ("--explain offers the exchange whatever the plan", "jnexplain.cho", "    if swap_code == 0 {\n        swap_json = buffer.append(heap, swap_json, \"true\");", "    if true {\n        swap_json = buffer.append(heap, swap_json, \"true\");"),
    ("--explain does not offer the exchange", "jnexplain.cho", "    if swap_code == 0 {\n        swap_json = buffer.append(heap, swap_json, \"true\");", "    if swap_code == 99 {\n        swap_json = buffer.append(heap, swap_json, \"true\");"),
    ("the refusal does not know the join is a --query", JNRUN, "queried: jxsrc.queried(jxs), prefixed: prefixed, join_where: jw_on, positions: jnswap.has_positions(tree)", "queried: false, prefixed: prefixed, join_where: jw_on, positions: jnswap.has_positions(tree)"),
    ("the refusal reads the size of the wrong file", JNRUN, "                psize = jnswap.size_of(pfile);", "                psize = size;"),
    ("the refusal does not look at the delimiter of a csv answer", JNRUN, "dchange: as_csv && !fmt.b_jsonl && bdelim != delim };", "dchange: false };"),
    ("the refusal takes a --join-prefix of the empty text for a prefix", JNRUN, "                    prefixed = buffer.size(pxr) > 0;", "                    prefixed = true;"),
]

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--shard" in argv:
        at = argv.index("--shard")
        k, n = (int(x) for x in argv[at + 1].split("/"))
        names = [m[0] for i, m in enumerate(MUTANTS) if i % n == k - 1]
        argv = [a for i, a in enumerate(argv) if i not in (at, at + 1)] + names
        # (a name is matched as a substring: the part's names are matched whole by listing the whole name)
    sys.exit(mutlib.main(MUTANTS, TESTS, argv))
