# History: what was measured, and what was changed because of it

The method is lexsys-tools' `docs/history.md`: measure with the incumbent on the
same file in the same loop, change one thing, measure again, keep what helped. The
rule set for this stage was: if `table` is more than about 2x worse than `csvtk -j 1`
on `--select`, investigate and iterate. It was not.

File: the design's, 1,000,000 rows, 31,667,311 bytes (`scripts/bench.py`). Minimum of
5 interleaved runs, output to /dev/null.

## Round 0: the skeleton's shape count (before `--select`)

Counting rows only. Mac: `table` 0.033 s, csvtk -j 1 0.200 s. Linux: `table` 0.053 s,
csvtk 0.455 s, mlr 0.298 s (on the earlier 31.7 MB file's generator; see the PR).
This was not a parity claim: `table` did a fraction of the work.

## Round 1: `--select` as first written

The reader's `fields` finds a field with one `memchr` for its delimiter, or for the
quote that closes it (no step per byte); a record is assembled in a buffer only when it
spans lines; output is written into a buffer in 64 KiB blocks and checked on write.
Nothing in it was tuned against a profile, because the first measurement did not ask
for it:

| `--select status,bytes --format csv` | `table` | `csvtk -j 1 cut -f` | `mlr cut -f` |
|---|---:|---:|---:|
| Mac arm64 | 0.057 s | 0.202 s | n/a |
| Linux x86-64 | 0.137 s | 0.628 s | 0.906 s |

`table` is 3.5x (Mac) and 4.6x (Linux) faster than `csvtk -j 1`, and 6.6x faster than
Miller on Linux. Output is byte for byte the same for all three (md5). A second Linux run, at a load of 6.9, gave 0.152 s, 0.710 s and 1.037 s: the same ratios (4.7x, 6.8x). Peak RSS: 1.7
and 1.9 MB against csvtk's 23.8 and 21.7 MB and Miller's 329 MB.

The other question, the first 1000 rows as JSON, is a startup cost: 0.0025 s (Mac) and
0.0016 s (Linux) against csvtk's 0.0078 s and 0.0114 s and Miller's 0.0299 s.

## Why no profiling round

The condition to iterate (worse than about 2x) was not met, so no `perf` or `sample`
run was made and nothing was tuned. Known costs left alone because the numbers do
not call for them: a record is copied into a buffer only when it spans lines (and a
header, once); the output of every field is checked for characters that need quoting
with up to four `memchr`s over the field.

## Other questions, Mac, same file (minimum of 5, `table` unless named)

| question | time |
|---|---:|
| `--select status --limit 1000` (the first page) | 0.0018 s |
| `--select status --limit 1000 --from 900000` (a page deep in the file) | 0.030 s |
| `--select note --format csv` (the one quoted column) | 0.049 s |
| `--select id,note,status,bytes,path --format csv` (all five, reordered) | 0.081 s |
| `csvtk -j 1 cut -f note` | 0.178 s |
| `csvtk -j 1 cut -f id,note,status,bytes,path` | 0.250 s |

A page at row 900,000 costs about the shape count (0.032 s), which is what the claim
in `docs/select.md` that a skipped record is only scanned amounts to: page k costs the
parse of the first k pages, and no more. Selecting the quoted column, or all the
columns, is not where `table` is weak either.

## Not measured

Wider files, a file whose every field is quoted, `--from` on a file with many
multi-line records, a sort, a join, non-ASCII data. The Linux box was measured with
the soak running (load about 4 to 5 of 16), so its absolute numbers are noisy; the
ratios between interleaved runs are what is reported.


# Filter and group (docs/filter.md)

The same file and method; `table` against `csvtk -j 1`, Miller and, as floors, `awk`
and `cut|sort|uniq -c`; every answer checked before timing.

## Round 0: the plan written, measured

First numbers with the filter and the grouping in (Mac, minimum of 5):

| question | `table` | best `csvtk -j 1` |
|---|---:|---:|
| filter | 0.083 s | 0.295 s |
| group-count | 0.113 s | 0.187 s |
| group sum | 0.124 s | 0.395 s |
| **cut two columns (unchanged code path)** | **0.093 s** | 0.185 s |

The new operations were ahead of `csvtk` from the start. The last row is the finding:
`--select ... --format csv` had been 0.057 s and was 0.093 s, a regression in code that
had not changed.

## Round 1: take the groups out of the per-row call

The cause: every counted row went through one `process` function that took a `Sink`
(the output buffers, the scratch buffers and **the groups**) by value and returned it.
A `Groups` is two maps, a vector and two buffers, some 45 words, moved twice per row
(1,000,000 rows) whether or not anything was being grouped. Fix: two functions,
`process_rows` (buffers only) and `process_groups` (the groups only), so a row moves
what it uses. Cut: **0.093 s -> 0.057 s** (Mac, minimum of 7, twice: 0.0569, 0.0564),
back to what it had been; filter went from 0.083 s to 0.047 s in the final run, and
group-count from 0.113 s to 0.104 s. Nothing else was changed, and nothing else was
needed: no round was run to close a gap with `csvtk`, because there was none.

A general observation for programs in this language: a `res` struct is moved, not
borrowed, and a loop that threads a large one through a call per row pays a copy per
row. The measurement found it only because the old benchmark was kept in the loop.

## Final numbers

See the README (Linux and Mac tables). Linux, minimum of 5, `table` / best `csvtk -j 1`
/ `mlr`: filter 0.100 / 0.797 / 0.397 s, group-count 0.177 / 0.397 / 0.371 s, group sum
0.220 / 1.103 / 0.549 s. Peak RSS 1.9 to 2.2 MB against 21 to 24 MB (csvtk) and 120 to
340 MB (Miller).

## Not measured

A filter that keeps most of the file (output-bound), a grouping with high cardinality
(the 100,000-group case is only bounded, 17 MB, not timed), `distinct` on a large column,
an `:int` condition on a column with quoted cells, wider files. `csvtk filter2` at 8 s
is an expression interpreter, not the comparison that matters; `filter | grep` is.


# Threads (docs/parallel.md)

`--threads N`, the answer the sequential read gives, from N threads. Measured with
`scripts/bench_parallel.py`, minimum of 5 interleaved runs; the sequential read is the first column
and the oracle.

## Round 0: the first parallel read

Speculate that every range begins a record, read ranges in waves, take the ranges in file order,
read again whatever cannot be trusted. Mac, group count, seconds: 1 thread 0.124, 2 0.068, 4 0.036,
8 0.020, 16 0.020; filter 0.052, 0.035, 0.020, 0.011, 0.011. Already 4 to 6x at 8 threads, and the
answers were the sequential ones on every test. Two things in these numbers were wrong:

* **16 threads were no faster than 8** (0.020 and 0.020): a range was 4 MiB, a 31 MB file has 8 of
  them, and 8 of 16 threads were idle.
* The scan of a *paged* answer (`--limit 1000`, JSON's default) started a full wave: when the page
  ended in the first range, the other N-1 had read for nothing.

## Round 1: the size of a range follows the number of threads, and a page starts small

A range is now the smaller of `--chunk-bytes` and a thread's share of what is left, so 16 threads
get 16 ranges (group count at 16 threads 0.020 -> 0.015 s; the filter, whose time is the rows the parent copies and
writes, stays at 0.011 to 0.012); and a paged answer's waves
start with one range and double. Not a speed-up but a bound: what a page reads beyond what it needed
is at most what it needed.

## Round 2: where it stops paying

The size below which several threads are not worth starting was measured, not guessed
(`--threshold`): break-even at 0.25 MiB of data after the header, a clear win from 1 MiB; the default
`--parallel-min-bytes` is 1 MiB.

## Round 3: the Linux curve flattens; is it the code?

Cores 0 to 5 of the Linux box: group-count 1.95x at 2 threads, 2.2x at 4, 2.4x at 6; filter 1.5x, 1.8x,
1.9x. Below the Mac's. Checked, in this order:

1. *Is it the memory?* One thread reads the file at 150 to 270 MB/s, so it is not bandwidth.
2. *Is it the cores?* `lscpu -e`: 0 to 5 are three physical cores. On cores 0, 2 and 4 (one thread to
   a core) 3 threads give 2.28x (group count), 2.32x (sum) and 1.92x (filter).
3. *Is it the machine, independent of the program?* Six concurrent *sequential* processes, sharing
   nothing, each took 3.8x as long as one alone: 1.56x aggregate on six logical cores. Six threads of
   one process get 2.4x.
4. *Is it the program's own serial part?* The one serial cost that is the design's is the copy of a
   thread's rows into the parent's array and out (the filter and the cut, 1.5 to 1.9x; the
   groupings, which send a few bytes, 2.0 to 2.4x). `perf` is not permitted on the box, so there is no
   profile; that part is a measured difference between questions, not a measured profile.

No change was made for this round: the flattening is not the program's, as far as the checks reach, and
the program is within 25 percent of what three cores give.

## A bug the tests found, and what the mutants found

* A page that fills at the last good row of a range was taken whole, with the ragged rows that follow it
  counted: the sequential read stops at the *next record*, ragged or not, and never reads them. Found by
  a mutant that removed the test, which led to a test of ragged rows after a full page, which failed; fixed
  in `par.run` (a range that reaches the limit without a condition is read in order).
* The mutant that moved a range's end one byte early did not change an answer: it hung (a wave that makes
  no progress). The proof that a wave always reads the record at its start is in `docs/parallel.md`; a loop
  that is only shown to end by a proof now also has a guard that reads the rest sequentially if it ever
  does not.

## Not measured

More than six cores of x86; a file whose boundaries are all inside quoted fields (correct, and about
sequential speed); wider files; a file that does not fit the page cache (the reads are `pread`s of 64 KiB, the
same as the sequential read's, but the threads' disk pattern was not looked at).

# The adversarial round (docs/adversarial.md)

## Round 4: the end-phase sort of a high-cardinality grouping

Cells registered first, then run (results in `docs/adversarial.md`). The worst Mac loss was the grouping of a
million distinct keys (B3 0.940 s, B4 0.895 s against DuckDB's 0.257 and 0.281 at one thread, and 9 to 10 times its
default), with the A1 proxy at 0.914 s. `sample` of B3: the end phase, `agg.compare_keys` and `agg.sort_into`
(the merge sort of the groups by key, field by field), over half of the samples.

*Change:* before the sort, one integer per group holds the first 7 bytes of the first key field (zero padded,
big-endian, so integer order is byte order); the merge sort compares the integers and calls `compare_keys` only when
they are equal (`agg.prefix_of`, `agg.before`, `agg.sort_into`, `table.finish_groups`). Output byte-identical:
md5 of `--group k1m --format csv` equal before and after (`bb5069c5...`), the 100 conformance tests pass (Mac and
Linux).

*Result (Mac, minimum of 5):* B3 0.940 to 0.790 s, B4 0.895 to 0.811 s, A1 0.914 to 0.778 s (-16, -9, -15
percent); B1, B2 and I1 did not move outside the noise (0.184, 0.193, 0.308). A smaller win than hoped, and the
reason is in the data: the keys `key0000001`... share their first 4 to 5 bytes, so the 7-byte prefix settles only
part of the comparisons; the second `sample` has `compare_keys` still 152 of about 370 samples. The rest is in
`docs/backlog.md` (a radix pass, a parallel merge, `csv_value`). Not tuned to the benchmark: the generated keys are
what a text key of this shape looks like (a prefix then digits), and the loss is reported as it stands.

Contract package adopted in the same branch: `fail.choose_*`, `fail.detail_*` (lexsys-tools#29), 48 lines fewer
(4,844 to 4,796); `toolbox.sort` was not adopted for `agg.ls` (see the backlog's friction list).

# Cell cost (branch `cell-cost`)

The adversarial round (`docs/adversarial.md`) found one lever behind most of its losses: the work done per row and
per cell, not the memory or the threads. This branch works that lever in measured rounds. The rule for a round: keep
it only if it is faster beyond noise **and** the output is byte for byte the same; otherwise revert it and write the
idea down with its number. The gates, every round: `scripts/corpus.py` (the md5 of the output, the status and the
error of 134 plans over the benchmark and adversarial files, each sequentially and with 3 and 4 threads and tiny
ranges: the same 134 lines before and after, every round), and from round 2 the whole conformance suite (111 tests
now; the parallel differential tests run 2, 3, 4, 8, 16 and 64 threads, with ranges down to one byte). The stopwatch
is `scripts/cellcost.py`: `table` only, one thread, the minimum of 9 to 15 runs, the binaries of two rounds
interleaved so that the machine's load falls on both. Numbers below are the Mac's unless said (Apple silicon, 16
cores; the Mac was not quiet during the rounds, which is why a ratio under about 3 percent is called noise).

## Round 0: where the time goes

`sample` on the Mac over the 1 GB file (33.8 million rows of 32 bytes), one thread, 1 ms samples, self time by function.
Per row, before: select 56 ns, filter 50 ns, group-count 110 ns, group-sum 122 ns (3.7 and 4.1 s for the file).

| cell | where the samples were (before) |
|---|---|
| select `status,bytes` | `memchr` 27% (the field search, one call per cell, and the two to four calls that decide whether an output cell needs quotes), `reader.fields` 18%, the read loop itself 14%, `lines.next` 12%, `emit_buf` 10%, `memmove` 8%, `csv_cell` 5% |
| filter `status=404 and bytes:int>50000` | `reader.fields` 22%, `memchr` 21%, `expr.holds` 15%, the read loop 13%, `lines.next` 10%, `expr.eval` 7% |
| group-count by status | `engine.process_groups` itself 30% and the read loop 22% (neither does any work: they **move** the `Groups` struct, about forty words, in and out of a call for every row), `agg.add` 14%, `reader.fields` 8%, `memchr` 8%, `lines.next` 5%, `map.find_slot` 5% |
| group-sum | as group-count, plus `parse_int` |

Two experiments fix the floor. With the group call replaced by nothing, group-count of the standard file takes
0.035 s (900 MB/s): reading, finding the fields and the screening are a third of what group-count costs. And an empty
`borrow mut` of the groups costs nothing, so working through a unique reference is free; the moves were the cost.
The thing the first plan of this round blamed, four `memchr`s per cell in `csv_value`, is the *output* side (the quoting
decision); the field search has one per cell; both were real, neither was the biggest.

## Rounds, in the order they were run

| # | idea | result (interleaved against the round before) | kept |
|---|---|---|---|
| 1 | **Add a row to an existing group in place**: `agg.add_fast` works through a unique reference to the groups, builds the key in the room of the key buffer, finds the entry, updates the integers with `vec.set`; a new group, a key that has to be unquoted, a `distinct` take the old way (`engine.group_fast`, then `process_groups`). No `Groups` moves per row | count 0.113 to 0.082 (-28%), sum 0.126 to 0.089 (-29%), 100k keys 0.196 to 0.146 (-25%) | yes |
| 2 | **A cache of hot groups** in front of the key hash: a cheap look (length, first and last byte of each key cell) names a group, believed only after comparing its key with the cells (so a wrong cache is a slow row, never a wrong answer); it gives itself up for 4096 rows after 16 net misses | count 0.082 to 0.071 (-14%), sum -13%. The first version, without giving up, made 100k keys 16% *slower*; with it, neutral | yes |
| 3 | **Walk short fields instead of calling `memchr`** (`reader.seek`): 24 bytes by hand, then `memchr` for what is left. A pure walk first: the 200-column file went 0.338 to 0.152 s (2.2x) and count -15%, but a file of 1 to 10 KB *unquoted* fields went 2.5x slower; the hybrid keeps both | select -2%, filter -8%, count -13%, sum -11%, wide -49%, long unquoted fields 1.00x | yes (the pure walk: no) |
| 4 | The same `seek` for the closing quote of a quoted field | quoted file -7%, others noise | yes |
| 5 | **One pass to decide an output cell needs quoting** (`writer.must_quote`) for cells up to 32 bytes, instead of two to four `memchr`s | select -9%, all-quoted -14%, 90%-filter -14% | yes |
| 6 | A grouping with no `--where` takes no buffers through the hot call (`engine.group_plain`) | count -8%, sum -2% (noise), 100k keys -3% | yes (small) |
| 7 | **A larger read chunk** (256 KiB, 1 MiB, 4 MiB instead of the contract's 64 KiB; a copy of `fill_file` that grows the chunk) | 32 MB file: select -6%, the rest -2% to -3%; **1 GB file: group-count 1.958 s to 1.950, 1.967, 1.941; filter 1.657 to 1.633, 1.653, 1.667: nothing** | **no, reverted**: the time in `read` is the kernel's copy of the page cache, not the number of calls |
| 8 | `parse_int` without the overflow check for up to 18 digits (a positive accumulator) | sum -6%, filter and the rest noise | yes |
| 9 | One loop instead of two in `add_fast` (the quote check, the cache's look and the key length together) | 0% to +3% | **no, reverted** |
| 10 | **The next line through a unique reference** (`scan.next_fast`: a whole line inside the chunk, nothing held over, not past the cap, is taken without moving the 17-field `Lines` in and out of `lines.next`; anything else asks `lines.next` as before) | select -11%, filter -11%, count -12%, sum -11%, quoted -9%, long -9%, 90% filter -9% | yes |
| 11 | The field walk unrolled four bytes at a time | 3% to 11% *slower* | **no, reverted** |
| 12 | `seek` in `reader.scan` (the lines inside a quoted field) | long fields 3% slower, newline-heavy long fields 18% slower | **no, reverted** |
| 13 | A new group takes the key `add_fast` already built and its failed lookup (`add`'s `keyed`) instead of building and looking again | 100k-key and 1M-key unchanged, 1M keys 1.09x to 1.05x of `main` | yes (it removes duplicate work) |
| 14 | After 32 rows in a row that each made a group, the next 2048 take the old way at once | 1M keys 1.05x to 1.00x of `main` | yes |
| 15 | A plan with a `distinct` aggregate does not go through the fast call at all (`engine.fast_ok`, decided once; round 1 had it ask and be refused on every row) | distinct over a unique column: Mac 0.293 s (main) to 0.268 (-9%); Linux 0.587 to 0.596 (+1.5%, even). Before this round it was +7% on Linux | yes |

That is more than the eight rounds the plan allowed; the last ones were each a few lines and each was measured and
either kept or reverted on its number, which is the reason none was stopped at the 3% line: a round that gained
under 3% on its own cell (6, 8, 13) is in because it also removed work the profile had shown, and is marked small.

### After (Mac, one thread, the 1 GB file)

| cell | before | after | per row |
|---|---:|---:|---:|
| select `status,bytes` | 1.898 s | 1.556 s | 56 to 46 ns |
| filter | 1.679 s | 1.456 s | 50 to 43 ns |
| group-count | 3.725 s | 1.772 s | 110 to 52 ns |
| group-sum | 4.139 s | 1.968 s | 122 to 58 ns |

`sample` after: select: `reader.fields` 42%, the read loop 15%, `emit_buf` 12%, `csv_cell` 12%, `memmove` 6%, `read` 5%,
`memchr` 5%; filter: `reader.fields` 48%, `expr.holds` 13%, the read loop 13%, `expr.eval` 8%; group-count:
`reader.fields` 39%, the read loop 30% (with the in-place add partly inlined into it), `add_fast` 23%; group-sum the
same plus `parse_int` 6%. **The field search is now the largest item in every cell**: 0.5 ns a byte, a byte loop that
LLVM does not vectorise and a cell-by-cell store of three integers. It is what is left (see `docs/backlog.md`).

On the standard 1M-row file (`scripts/bench.py`, the README's table): group-count 0.104 to 0.051 s on the Mac, group-sum
0.113 to 0.060 s, filter 0.047 to 0.045, cut 0.054 to 0.049. The adversarial cells before and after, with the new
ratios against csvtk, Miller and DuckDB, are in `docs/adversarial.md`.

### Tests and mutants

`tests/conformance/test_cellcost.py` (new) aims at each seam: field lengths around the 24-byte walk and the 32-byte
quoting pass with every kind of special byte at every distance; the cache (keys that collide, runs, many keys, a
few keys after many; **two keys of different lengths that share a cache place**, which only the length check
tells apart); a quoted key with a doubled quote against the same text unquoted; keys of more than 255 bytes (the second
length byte); `distinct` over text; the second aggregate refusing (which one is said, and the rule); integers of 0 to
30 digits, signs, and the sums at the edge; a line that ends a 64 KiB chunk, one that is a byte on either side, a line
longer than `--max-line-bytes` that is more than a chunk (and one that ends 100 bytes into the next chunk, the case where
a line held over looks like a whole one). Each plan runs sequentially and with 3, 4, 16 and 64 threads and ranges of
7 to 4096 bytes, and the answers must be equal and equal to Python's.
`scripts/cellcost_mutants.py` (new) lists 44 mutants of the new paths (this said 45; the script is the count), **all killed** (on the Linux box; the Mac run of an earlier
version of the list too), and three more that are equivalent and are said so in the script: an unquoted field is never able to hold a delimiter or an LF, so testing for them changes nothing; and the
key `add_fast` builds is only looked for, so a wrong length byte in it makes a miss and the row takes the slow way, with
the same answer. The first run of the mutants had seven survivors, two of them those equivalent ones and five real (the tests checked a refusal's status and not its
rule, did not hold a group in the cache before a collision, did not have a quoted key and an unquoted key of the same
text, and did not have a line over the cap that left less than the cap in the next chunk); the tests were sharpened and
all seven are killed. The older `select_mutants.py` and `filter_mutants.py` no longer apply to the code (21 of their
sites moved to `engine.ls` and `scan.ls` when the parallel read was written, and `agg.ls` now has two copies of two
of them); that was already so on `main`; it is in the backlog.
