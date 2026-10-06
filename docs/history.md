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
