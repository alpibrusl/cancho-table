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
