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
