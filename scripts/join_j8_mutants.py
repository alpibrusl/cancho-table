#!/usr/bin/env python3
"""Mutation check of slice J8 (docs/joins.md section 24; tests/conformance/test_join_j8.py): the partitioned build of a hash join.

Each mutant is one defect in when the build is partitioned (the rule, the minimum, the size of a range, where a range begins), in how a range reads its rows (what makes it give up, the filters, the key, the pruned record, the
partition a row goes to, the frames and their sizes), in the tables of the partitions (the offsets of entries, rows and bytes, the bound each is held to), in how the parent puts them together and when it gives up (the sum
against the bound, `--max-rows`, a partition that failed), in how the build is hooked into the join, and in what `--explain` says; the tool is rebuilt from it and run against the tests. A mutant is killed when the build
refuses it or a test fails.

    python3 scripts/join_j8_mutants.py [name-substring ...]

`--check` only verifies that every site still matches (no build; CI runs it). MUT_SHARD=K/N runs the K-th of N interleaved parts of the list (each part needs a checkout of its own: the runner edits
tools/table and builds into build/). See mutlib.py.

Not here (equivalent mutants, each argued):

* the partition of a key (`% parts` for `% 1`, another function of the key's hash or length): which thread builds a key changes nothing but the load, because equal keys go to one partition whatever the function is and the
  table the parent puts together has the same chains. What must not be allowed is a partition that is not a function of the key: that mutant is here (the record's length).
* the alignment of a range (`at = p - 1` for `at = p`, `found = at + q` for `found = at + q + 1`, the window or the number of its rounds, `pl[ranges] = size - 1`, the guard `edge < pl[j - 1]`): every other boundary at the
  start of a line (or at the line feed before it, which begins a blank line that is skipped) cuts the file into ranges whose rows are the same rows, and `align` never goes back; a boundary that is NOT at a line start
  is the mutant above (`+ 2`), and a window too short to find a line feed makes the ranges after it empty and the last one the rest, which only a range larger than its slot (a megabyte of rows) shows.
* the check of the payload against the slot after the frames are counted (`payload > room`): the same test is made, row by row, as the frames are written (`held > room`), so it is never the first to be true.
* `next[row] < 0` for `<= 0` in the partition's table: a row is never the next of row 0.
* the lower bound of the accounted bytes for a semi or anti join (`cost = 0`): it only gives up earlier, and a semi or anti join that is refused is refused by the sequential build.
* the records counted on success (`empty_counts(nb, records)`): `Counts.records` of a build that was done is read by nothing (it is read for a refusal, which the sequential build gives).
* the check of the least bytes of the frames that a range makes for itself (`least > limit` in `jnprange.cho`): the parent makes the same check on the sum after the wave, so the build is declined either way and by
  the same wave; the worker's check only stops its own range earlier, which only time and the memory of one range show.
* the hook's `c_threads() > 0` for `> 1`: the partitions are 0 or at least 2.
* the tails of the chains: the table the probe reads has none (a tail is read only while a table is built), so what is put in it is not read.
* the bound handed to a partition (`limit + 1`): a partition that holds one more byte than the bound is found by the parent's sum, which is the sequential build's refusal either way.
"""
import sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import mutlib  # noqa: E402

# cheap classes first: a killed mutant stops at the first failure (mutlib runs `unittest -f`)
TESTS = ["test_join_j8.Explain", "test_join_j8.Used", "test_join_j8.Refusals", "test_join_j8.Edges", "test_join_j8.Bound", "test_join_j8.Threads", "test_join_j8.Identity"]

R = "jnprange.cho"
B = "jnpbuild.cho"
P = "jnpart.cho"

MUTANTS = [
    # when the build is partitioned
    ("two threads are not partitioned", B, "    if threads < 2 || data < 1 || data < min_bytes {", "    if threads < 3 || data < 1 || data < min_bytes {"),
    ("an empty build is partitioned", B, "    if threads < 2 || data < 1 || data < min_bytes {", "    if threads < 2 || data < 0 || data < min_bytes {"),
    ("a build of exactly --parallel-min-bytes is not partitioned", B, "    if threads < 2 || data < 1 || data < min_bytes {", "    if threads < 2 || data < 1 || data <= min_bytes {"),
    ("the range is the larger of the chunk and a thread's share", B, "    if share < rb {", "    if share > rb {"),
    ("the ranges may be more than 16,384", B, "    if rb < least {", "    if rb > least {"),
    ("a thread's share is rounded down", B, "    let share = (data + threads - 1) / threads;", "    let share = data / threads;"),
    ("the minimum counts the header", P, "        parts = jnpbuild.partitions(threads, size - consumed, cli.nat(args, parsed, table, \"parallel-min-bytes\"));", "        parts = jnpbuild.partitions(threads, size, cli.nat(args, parsed, table, \"parallel-min-bytes\"));"),
    ("the hook partitions every build", "jnrun.cho", "                if contents(pcr)[jnpbuild.c_threads()] > 0 {", "                if contents(pcr)[jnpbuild.c_threads()] >= 0 {"),
    ("the hook partitions a build that is not csv with the answer's delimiter", "jnrun.cho", "        if !foreign {\n            var hstart = 0;", "        if true {\n            var hstart = 0;"),
    ("the rows begin at the start of the file", "jnrun.cho", "                hstart = hbr.consumed;", "                hstart = 0;"),
    ("the table is made and the build is made again", "jnrun.cho", "                                                pdone = true;", "                                                pdone = false;"),
    ("the pruning of the build is not told", "jnrun.cho", "                pruned = contents(pmr0)[0] == 1;", "                pruned = false;"),
    # the plan of ranges
    ("a range begins in the middle of a line", B, "                found = at + q + 1;", "                found = at + q + 2;"),
    ("the first range begins at the start of the file", B, "                    pl[0] = start;", "                    pl[0] = 0;"),
    # a range
    ("a row with a quoted line break is taken", R, "                                if wrong || open || n != nb {", "                                if wrong || n != nb {"),
    ("a ragged row is taken", R, "                                if wrong || open || n != nb {", "                                if wrong || open {"),
    ("a bad quote is taken", R, "                                if wrong || open || n != nb {", "                                if open || n != nb {"),
    ("a row at the --max-rows count makes the build be read again", R, "                                if records > most {", "                                if records >= most {"),
    ("a row that a filter leaves out makes the build be read again", R, "                                        if verdict < 0 {", "                                        if verdict <= 0 {"),
    ("a row that a filter leaves out is held", R, "                                        } else if verdict == 1 {", "                                        } else if verdict >= 0 {"),
    ("the key of a quoted cell is the cell with its quotes", R, "                                                if cells[3 * key + 2] == 0 {", "                                                if cells[3 * key + 2] != 0 {"),
    ("a typed key that is not of its type is taken", R, "                                                if st != 0 {", "                                                if st != 99 {"),
    ("a frame that does not fit the slot is written", R, "                                                if held > room {", "                                                if held > room + 100000000 {"),
    ("the next range's first row is read too", R, "                            } else if start + lines.offset(rr) >= until {", "                            } else if start + lines.offset(rr) > until {"),
    ("a row goes to the partition its record's length says", R, "    let pid = part_of(key, parts);", "    let pid = len(line) % parts;"),
    ("a pruned record loses the open quote of its first cell", R, "        rw = buffer.append(heap, rw, record[cells[3 * c1] - cells[3 * c1 + 2]..cells[3 * c2 + 1] + cells[3 * c2 + 2]]);", "        rw = buffer.append(heap, rw, record[cells[3 * c1]..cells[3 * c2 + 1] + cells[3 * c2 + 2]]);"),
    ("a pruned record has one delimiter less before a run", R, "            rw = buffer.append(heap, rw, dl[0..lead]);", "            rw = buffer.append(heap, rw, dl[0..lead - 1]);"),
    ("a pruned record loses its trailing delimiters", R, "    if trailing == 1 {\n        rw = buffer.push(heap, rw, dl[0]);\n    } else if trailing > 0 {\n        rw = buffer.append(heap, rw, dl[0..trailing]);\n    }\n    return rw;", "    return rw;"),
    ("a semi join's frames carry the record", R, "        return (put_frame(heap, stage, pid, key, line[0..0]), prec, 0);", "        return (put_frame(heap, stage, pid, key, line), prec, len(line) + 16);"),
    ("a range's least bytes are not told", R, "    inf[2] = least;", "    inf[2] = 0;"),
    ("a range's payload is not told", R, "    inf[3] = payload;", "    inf[3] = payload - 1;"),
    ("a blob starts where the one before began", R, "                base = base + 8 * inf[4 + 4 * p] + inf[4 + 4 * p + 1] + inf[4 + 4 * p + 2];", "                base = base + inf[4 + 4 * p + 1] + inf[4 + 4 * p + 2];"),
    # the parent
    ("the rows of the ranges are not added up against --max-rows", B, "                        records = records + inf[at + 1];", "                        records = records;"),
    ("the sum of the rows may pass --max-rows by one", B, "                        if records > cfg[c_most()] || least > limit {", "                        if records > cfg[c_most()] + 1 || least > limit {"),
    ("the least bytes of the ranges are not checked", B, "                        if records > cfg[c_most()] || least > limit {", "                        if records > cfg[c_most()] {"),
    ("the store may grow without a bound", B, "                                if buffer.size(sr) > 8 * limit + 65536 {", "                                if buffer.size(sr) > 800 * limit + 65536 {"),
    ("the first range of a wave is the only one taken", B, "                        records = records + inf[at + 1];", "                        records = records + inf[at + 1];\n                        q = n;"),
    ("a blob of a range is taken from where the range's payload began", B, "                                        contents(tw)[tab + 3] = base + inf[at + 4 + 4 * p + 3];", "                                        contents(tw)[tab + 3] = inf[at + 4 + 4 * p + 3];"),
    ("the key bytes of a partition are not added up", B, "                                        contents(ow)[4 * p + 1] = contents(ow)[4 * p + 1] + inf[at + 4 + 4 * p + 1];", "                                        contents(ow)[4 * p + 1] = contents(ow)[4 * p + 1];"),
    ("the record bytes of a partition are not added up", B, "                                        contents(ow)[4 * p + 2] = contents(ow)[4 * p + 2] + inf[at + 4 + 4 * p + 2];", "                                        contents(ow)[4 * p + 2] = contents(ow)[4 * p + 2];"),
    ("the entries of the partitions begin one early", B, "                total_rows = total_rows + contents(tr)[4 * p];", "                total_rows = total_rows + contents(tr)[4 * p] + 1;"),
    ("the key bytes of every partition begin at the start", B, "                contents(bw)[6 * p + 1] = total_keys;", "                contents(bw)[6 * p + 1] = 0;"),
    ("the record bytes of every partition begin at the start", B, "                contents(bw)[6 * p + 2] = total_recs;", "                contents(bw)[6 * p + 2] = 0;"),
    ("a partition that failed is taken", B, "            pr[8 * k] = o[0];", "            pr[8 * k] = 0;"),
    ("the sum of the held bytes may equal the bound plus one", B, "        if held > limit {", "        if held > limit + 1 {"),
    ("the sum of the held bytes may not equal the bound", B, "        if held > limit {", "        if held >= limit {"),
    ("the last entry of a partition is not put in the slots", B, "                            place_all(contents(mr), contents(sw), from, from + contents(rr)[8 * p + 1]);", "                            place_all(contents(mr), contents(sw), from, from + contents(rr)[8 * p + 1] - 1);"),
    ("the entries of a partition are put in the slots from its first row's number", B, "                            let from = contents(br)[6 * p];", "                            let from = contents(br)[6 * p + 1];"),
    # a partition's table
    ("a partition's entries point at its own key bytes", B, "                oi[8 + 3 * e] = km[3 * e] + kb;", "                oi[8 + 3 * e] = km[3 * e];"),
    ("a partition's heads point at its own rows", B, "                    oi[8 + 3 * entries + e] = hd[e] + rb;", "                    oi[8 + 3 * entries + e] = hd[e];"),
    ("a partition's rows start in its own record bytes", B, "                    oi[8 + 4 * entries + row] = st[row] + cb;", "                    oi[8 + 4 * entries + row] = st[row];"),
    ("a partition's chains link its own rows", B, "                        oi[8 + 4 * entries + nrows + row] = nx[row] + rb;", "                        oi[8 + 4 * entries + nrows + row] = nx[row];"),
    ("a semi join's partition holds rows", B, "        if keys_only {\n            let (t2, ok2) = jtab.add_key(w.heap, t, key, limit);", "        if false {\n            let (t2, ok2) = jtab.add_key(w.heap, t, key, limit);"),
    ("the last meta number of a partition is not copied", B, "                while i < 3 * entries {", "                while i < 3 * entries - 1 {"),
    ("the last head of a partition is not copied", B, "                    while i < entries {", "                    while i < entries - 1 {"),
    ("the last next of a partition is not copied", B, "                    while i < nrows {", "                    while i < nrows - 1 {"),
    ("the record bytes of a partition are put where its key bytes are", B, "                copy_into(contents(uni.u_rbytes)[cb..cb + o[5]], b[o[4]..o[4] + o[5]]);", "                copy_into(contents(uni.u_rbytes)[0..o[5]], b[o[4]..o[4] + o[5]]);"),
    # the settings
    ("the delimiter is not told", P, "        k[jnpbuild.c_delim()] = delim;", "        k[jnpbuild.c_delim()] = 0;"),
    ("the line cap is not told", P, "        k[jnpbuild.c_cap()] = cap;", "        k[jnpbuild.c_cap()] = 0;"),
    ("--max-rows is not told", P, "        k[jnpbuild.c_most()] = most;", "        k[jnpbuild.c_most()] = 0;"),
    ("the bound is not told", P, "        k[jnpbuild.c_limit()] = limit;", "        k[jnpbuild.c_limit()] = 0;"),
    ("the number of pairs is not told", P, "        k[jnpbuild.c_pairs()] = pairs;", "        k[jnpbuild.c_pairs()] = 1;"),
    ("a semi or anti join is not told", P, "            k[jnpbuild.c_keys_only()] = 1;", "            k[jnpbuild.c_keys_only()] = 0;"),
    ("--join-where is not told", P, "            k[jnpbuild.c_jw_on()] = 1;", "            k[jnpbuild.c_jw_on()] = 0;"),
    ("the conditions on the build are not told", P, "            k[jnpbuild.c_push_b()] = 1;", "            k[jnpbuild.c_push_b()] = 0;"),
    ("the pruning is not told", P, "            k[jnpbuild.c_prune()] = 1;", "            k[jnpbuild.c_prune()] = 0;"),
    ("the chunk is not told", P, "        k[jnpbuild.c_chunk()] = cli.nat(args, parsed, table, \"chunk-bytes\");", "        k[jnpbuild.c_chunk()] = 1000000000;"),
    ("the start of the rows is not told", P, "        k[jnpbuild.c_start()] = consumed;", "        k[jnpbuild.c_start()] = 0;"),
    ("the size is not told", P, "        k[jnpbuild.c_size()] = size;", "        k[jnpbuild.c_size()] = size + 1;"),
    # --explain
    ("the explain of a foreign build has partitions", "jnexplain.cho", "    if !merging && !foreign && bsize >= 0 {", "    if !merging && bsize >= 0 {"),
    ("the explain of a merge has partitions", "jnexplain.cho", "    if !merging && !foreign && bsize >= 0 {", "    if !foreign && bsize >= 0 {"),
    ("the explain counts the header in the data", "jnexplain.cho", "        partitions = jnpbuild.partitions(requested, bsize - bheader, cli.nat(args, parsed, table, \"parallel-min-bytes\"));", "        partitions = jnpbuild.partitions(requested, bsize, cli.nat(args, parsed, table, \"parallel-min-bytes\"));"),
    ("the explain gives the whole file as the data", "jnexplain.cho", "        let pdoc = jnpart.partitions_doc(heap, partitions, bsize - bheader, cli.nat(args, parsed, table, \"chunk-bytes\"));", "        let pdoc = jnpart.partitions_doc(heap, partitions, bsize, cli.nat(args, parsed, table, \"chunk-bytes\"));"),
    ("the explain gives the chunk as the range", P, "    let rb = jnpbuild.range_bytes(parts, data, chunk);", "    let rb = chunk;"),
    ("the explain counts one range too few", P, "    o = buffer.push_nat(heap, o, (data + rb - 1) / rb);", "    o = buffer.push_nat(heap, o, data / rb);"),
    ("the top level of the explain says the join is read on one core", "explain.cho", "                why = jnpwords.top_reason();", "                why = \"the join is read on one core\";"),
    ("the note of the explain says --threads has no effect", "explain.cho", "                o = note(heap, o, first_note, jnpwords.note());", "                o = note(heap, o, first_note, \"--threads has no effect on a join: it is read on one core, with the same bytes\");"),
]

if __name__ == "__main__":
    # MUT_SHARD=K/N runs the K-th (from 1) of N interleaved parts of the list; each part needs a checkout of its own, as the runner edits tools/table and builds into build/
    shard = __import__('os').environ.get("MUT_SHARD")
    chosen = MUTANTS
    if shard and "--check" not in sys.argv:
        k, n = (int(x) for x in shard.split("/"))
        chosen = [m for i, m in enumerate(MUTANTS) if i % n == k - 1]
    sys.exit(mutlib.main(chosen, TESTS))
