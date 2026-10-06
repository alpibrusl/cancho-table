# The adversarial round: where does `table` lose?

Registered **before any of it was run** (this file's first commit is the registration; the results are added in
later commits, below the line). The rule: find the shapes of file and question that are *not* kind to `table`, run
them against the incumbents, report every loss with its ratio and its cause, and either fix it (a cheap, safe win,
outputs byte-identical, the round recorded in `docs/history.md`) or put it in `docs/backlog.md`. Nothing is tuned
to avoid a loss, and a cell is not dropped because it is bad.

## The incumbents and how each is run

| tool | how | threads |
|---|---|---|
| `table` | `--threads 1`, and `--threads 8` on the Mac / `--threads 6` on Linux (the number of cores allowed) | 1, N |
| `csvtk` 0.38.0 | `-j 1` (and `-j N` where it differs) | 1 |
| Miller (`mlr`) 6.22.0 | Linux only (not installed on the Mac) | 1 |
| DuckDB 1.5.6 | Mac only: `SET threads=1` and its default; `COPY (...) TO '/dev/null' (FORMAT csv)`, `read_csv` with detection | 1, default |
| `sort`/`uniq`/`cut`/`awk` | where the question is that shell pipeline (`LC_ALL=C`), a floor and not CSV-aware | 1 |

Output goes to /dev/null. Minimum of 5 interleaved runs (3 for the 1 GB file). Peak resident set from
`/usr/bin/time` on one more run. **Before any timing every contender's answer is compared**: the output is parsed as
CSV, numbers that are integers are normalised (`25.00` and `25` are one number), rows are put in a sorted order where
the question does not define one, and the result must equal the Python-computed or `table`-computed answer; a
contender that disagrees is reported, not timed. Machines: Apple-silicon Mac (16 cores, a load of 3 to 6 from other
work) and the Linux box, cores 0 to 5 (three physical cores) with a soak running.

## What counts as a loss

For a question that `table` answers:

* **a loss** is `table`'s time above the incumbent's time at the same thread count (1 against 1), or `table` at the
  machine's thread count above the incumbent's *default* (DuckDB's default, or csvtk/Miller, which are what they are);
* **a material loss** is a ratio above 1.25;
* **a memory loss** is a peak resident set above 4x the incumbent's *and* above 64 MB (the claim "bounded" is the
  thing under test, so `table`'s memory is judged against its own limits too: it must stay within the documented
  bound for the shape, whatever the incumbent does);
* **a cliff** is `table`'s time per megabyte rising more than 3x from the benchmark file's to this shape's, with no
  more work to account for it. (A cliff is reported even when an incumbent has one too.)

## The cells (what, the file, the incumbents, my prediction)

Files are generated, seeded, outside the repository (`build/adv/`). `F1` is the design's 1,000,000-row, 31.7 MB file.
`F2` is 1,000,000 rows of `id,s,k100k,k1m,u,v`: `s` one of 4 values, `k100k` 100,000 distinct text keys (`k17`...),
`k1m` 1,000,000 distinct text keys in random order, `u` a random permutation of 0..999,999, `v` a random integer
0..99,999.

| cell | question | file | incumbents | prediction |
|---|---|---|---|---|
| **A1** | sort 1M rows by a **text** column | `F2`, `k1m` | csvtk `sort -k`, Miller `sort -f`, DuckDB `ORDER BY`, `sort -t, -k` | `table` has **no row sort**: a missing feature. Measured proxy: `--group k1m` (1M distinct keys come out in key order, bytewise) with the group bounds raised. Not the same question; the proxy is reported with that said |
| **A2** | sort 1M rows by an **integer** column | `F2`, `u` | the same | no row sort, and no proxy that orders numerically (a group's keys are bytes): reported as missing, incumbents' times for scale only |
| **B1** | group-count, 100,000 distinct keys | `F2`, `k100k` | csvtk `freq`, Miller `count-distinct`, DuckDB, `cut\|sort\|uniq -c` | **loss likely**: every row is a map probe into 100k entries instead of 4 |
| **B2** | group-sum of `v`, 100,000 keys | `F2` | csvtk `summary`, Miller `stats1`, DuckDB | loss likely |
| **B3** | group-count, 1,000,000 distinct keys | `F2`, `k1m`, `--max-groups 1000000 --max-state-bytes 1073741824` | the same | **loss likely, large**: a million keys is a million map entries and the output sort |
| **B4** | group-sum, 1,000,000 keys | `F2` | the same | loss likely |
| **C1** | select 3 columns of **200** | 200 columns x 200,000 rows, small integers (~210 MB) | csvtk `cut`, Miller `cut`, DuckDB | **loss possible**: the field finder stores up to 200 cells per row |
| **D1** | cut 2 columns, every field quoted, doubled quotes inside | 1M rows x 5 columns, all quoted | csvtk, Miller, DuckDB | **loss likely**: the fast scan is by delimiter, and a quoted field is found by quote |
| **D2** | filter `status=404 and bytes>50000`, all quoted | the same | csvtk (`filter\|grep`), Miller, DuckDB | loss likely |
| **D3** | group-count by `status`, all quoted | the same | csvtk `freq`, Miller, DuckDB | loss possible |
| **E1** | select 2 columns, **fields of 1 to 10 KB** | 20,000 rows x `id`, `g` (10 values), a 1-10 KB text field (~110 MB) | csvtk, Miller, DuckDB | **loss possible**: the 64 KiB reader and a line longer than a chunk |
| **E2** | group-count by `g`, long fields | the same | csvtk, Miller, DuckDB | loss possible |
| **G1** | **1 GB** file, filter | `F1` body repeated to ~1.08 GB (33.8M rows) | DuckDB default, csvtk `-j 1`; `table` at 1, 4 and N threads; peak memory | `table` wins on time at 4+ threads, memory flat; the page cache holds the file on the Mac |
| **G2** | 1 GB file, group-count | the same | the same | the same |
| **H1** | filter keeping **~90%** of the rows (output-bound) | `F1`, `bytes>10000` | csvtk, Miller, DuckDB | loss possible: the output copy and write is the work |
| **I1** | `distinct:id` of a unique column, per `s` | `F2`, `--group s --agg distinct:id --max-distinct 10000000 --max-state-bytes 1073741824` | DuckDB `count(DISTINCT)`, Miller `count-distinct`, csvtk `uniq\|freq`, `sort -u` | **loss likely, large** (a million-entry pair set) |

Not run: many tiny files (`table` takes one file), joins, sorts of non-key columns (not features), Parquet.

## Results

Ran after the registration above. Mac: Apple silicon, 16 cores, a load of 3 to 6; `--threads 8`. Linux: i7-1260P,
`taskset -c 0-5` (three physical cores), niced, a soak running on other cores, **`--threads 8` (not the registered 6:
a deviation, it oversubscribes the six logical cores a little; the G cells also ran 4)**; Miller there, DuckDB not
installed. Minimum of 5 (3 for 1 GB), seconds; RSS in MB. Every contender's output was checked against the Python
answer first. `table` of this branch (with the prefix sort, see `docs/history.md` round 4); B3, B4, A1 on the Mac were
measured again after it (before: 0.940, 0.895, 0.914 s).

### Mac (seconds; RSS in MB in brackets for table -t1 / best incumbent)

| cell | table -t1 | table -t8 | csvtk -j1 | DuckDB -t1 | DuckDB default | verdict |
|---|---|---|---|---|---|---|
| A1 sort by text (proxy) | 0.778 [179] | | 0.942 [259] | 0.534 | 0.141 | **loss 1.46x** vs DuckDB t1 (5.5x vs default); wins csvtk 1.2x. Not the same question (no row sort) |
| A2 sort by int | none | | 2.26 | 0.443 | 0.128 | **missing feature** |
| B1 count, 100k keys | 0.184 [20] | 0.092 | 0.299 | 0.167 | 0.079 | **loss 1.10x** t1, **1.16x** t8 vs default; csvtk 1.6x slower |
| B2 sum, 100k keys | 0.193 [20] | 0.097 | 3.58 [12,926] | 0.180 | 0.083 | **loss 1.07x** t1, **1.17x** t8 |
| B3 count, 1M keys | 0.790 [183] | 0.874 | 1.086 | 0.257 | 0.091 | **loss 3.1x** t1, **9.7x** t8 vs default; t8 slower than t1; csvtk 1.4x slower |
| B4 sum, 1M keys | 0.811 [179] | 0.837 | 40.1 [14,842] | 0.281 | 0.092 | **loss 2.9x** t1, **9.1x** t8; csvtk 49x slower and 14.8 GB |
| I1 distinct:id | 0.308 [124] | 0.190 | 0.493 | 0.200 | 0.082 | **loss 1.54x** t1, **2.3x** t8 |
| C1 select 3 of 200 cols | 0.364 [1.8] | 0.061 | 0.853 | 1.407 | 0.828 | win (2.3x csvtk, 3.9x DuckDB t1) |
| D1 cut, all quoted | 0.095 | 0.031 | 0.293 | 0.290 | 0.091 | win |
| D2 filter, all quoted | 0.088 | 0.029 | 0.379 | 0.250 | 0.079 | win |
| D3 group, all quoted | 0.143 | 0.038 | 0.239 | 0.166 | 0.064 | win |
| E1 select, 1-10 KB fields | 0.208 | 0.205 | 0.186 | 0.399 | 0.323 | **loss 1.11x** vs csvtk, no thread scaling |
| E2 group, 1-10 KB fields | 0.212 | 0.208 | 0.195 | 0.402 | 0.328 | **loss 1.09x** vs csvtk, no thread scaling |
| H1 filter keeping 90% | 0.106 | 0.032 | 0.348 | 0.300 | 0.118 | win |

1 GB (33.8M rows, 1,077 MB; page cache holds it), minimum of 3, `--max-rows 1000000000`:

| | table -t1 | -t4 | -t8 | csvtk -j1 | DuckDB default | RSS table t1 / t8 / DuckDB |
|---|---|---|---|---|---|---|
| G1 filter | 1.655 | 0.556 | 0.306 | 10.0 | 0.518 | 1.8 / 50 / 385 MB |
| G2 group-count | 3.690 | 1.003 | 0.532 | 6.28 | 0.334 | 1.6 / 36 / 285 MB |

G1: `table` -t8 is 1.7x faster than DuckDB, t4 is 1.07x slower. **G2: loss 1.59x vs DuckDB at t8**; 6.0x faster than csvtk.

### Linux (cores 0-5, soak running)

| cell | table -t1 | table -t8 | csvtk -j1 | Miller | shell | verdict |
|---|---|---|---|---|---|---|
| A1 sort by text (proxy) | 1.814 [135] | | 2.281 | 5.603 [1,425] | `sort` 1.055 [84] | **loss 1.72x** vs `sort` (multi-threaded GNU sort, not CSV-aware); wins csvtk and Miller |
| A2 sort by int | none | | 4.28 | 4.11 | | **missing feature** |
| B1 count, 100k | 0.404 [16] | 0.313 | 0.697 | 0.810 [554] | `cut\|sort\|uniq -c` 0.249 | **loss 1.62x** vs shell; wins csvtk 1.7x, Miller 2.0x |
| B2 sum, 100k | 0.482 [16] | 0.408 | 13.4 [14,474] | 2.13 [854] | | win |
| B3 count, 1M | 1.649 [135] | 1.867 | 1.948 | 2.915 [1,526] | 0.352 | **loss 4.7x** vs shell; csvtk 1.18x slower; t8 slower than t1 |
| B4 sum, 1M | 1.869 [135] | 2.221 | killed (rc -9, ~14 GB) | 4.48 [2,645] | | win vs Miller 2.4x; csvtk OOM-killed |
| I1 distinct | 0.627 [89] | 0.693 | 1.524 | 3.84 [1,701] | `cut\|sort -u\|uniq -c` 0.353 | **loss 1.78x** vs shell; t8 slower than t1; wins csvtk 2.4x, Miller 6.1x |
| C1 select 3 of 200 | 0.450 [2.2] | 0.125 | 1.442 | 6.19 [3,889] | | win |
| D1 cut, quoted | 0.437 | 0.251 | 1.775 | 2.355 | | win |
| D2 filter, quoted | 0.438 | 0.177 | 3.221 | 1.449 | | win |
| D3 group, quoted | 0.651 | 0.279 | 1.522 | 1.383 | | win |
| E1 select, long fields | 0.698 | 0.733 | 0.844 | 0.693 | | tie with Miller (1.01x), wins csvtk; no thread scaling |
| E2 group, long fields | 0.717 | 0.661 | 0.788 | 0.816 | | win, no thread scaling |
| H1 filter 90% | 0.589 | 0.245 | 2.524 | 2.061 | | win |

1 GB on Linux: G1 filter t1 2.850, t4 2.452, t8 1.939 (csvtk 29.7 s: 15x slower); G2 group-count t1 6.512, t4 4.582,
t8 4.137 (csvtk 16.7 s). RSS 1.8 to 36 MB. DuckDB is not installed there. The Linux scaling on 1 GB (1.5x filter, 1.6x
group at 8 threads) is the flattening of `docs/history.md` round 3, in a worse form, with the soak running.

### Every loss, and why

(The profile is `sample` on the Mac; `perf` is not permitted on the Linux box, so Linux causes are by timing
experiment or inference, and said so.)

* **B3, B4 (1M keys), 3x at 1 thread, 9x against DuckDB's default.** `sample` of B3 before the fix: the end phase,
  sorting the million keys (`compare_keys` and `sort_into` over 50 percent of the samples); a million map probes
  per run and `memchr` for the field search are the rest. After the prefix sort (round 4) the same samples are
  `compare_keys` 152 of about 370, `sort_into` 102, `memchr` 81: the sort is still two thirds of it, because the
  generated keys `key0000001`... share 4 or 5 bytes, so a 7-byte prefix does not separate most neighbours. The
  threads do not help and make it 10 percent worse: the groups are merged by the parent one thread at a time, and
  eight 1M-key maps are eight times the state (327 MB against 183). DuckDB's default is parallel in the hash
  *and* the sort.
* **A1 (the sort proxy), 1.46x vs DuckDB t1, 1.72x vs GNU sort on Linux.** The same sort. `table` has no row
  sort: only the proxy is measured.
* **B1, B2 (100k keys), 1.07 to 1.17x.** A probe into a 100,000-entry map (cache-missing) per row; nothing else
  stands out in the profile (the field search, `memchr`, is the same as for 4 groups). Not looked at further.
* **I1 (distinct), 1.54x t1, 2.3x t8 vs DuckDB; 1.78x vs shell on Linux.** A million-entry pair set with a
  per-row probe, then a sort of the output; the threads' pair sets are merged by the parent.
* **E1, E2 (1-10 KB fields), 1.1x vs csvtk on the Mac, no thread scaling.** The time per megabyte is *not* a cliff
  (1.70 ms/MB against 1.82 for the same select on the benchmark file), so it is a plain tie with csvtk, whose
  inner loop is a Go `bufio` scan: with long fields each 64 KiB read holds few records and the per-row work is
  small, so the memory copy dominates. The threads do not scale because the speculation "this boundary is outside
  quotes" is wrong for the first record boundary in a range far more often for fields that hold newlines and
  long text; a wrong guess is a re-read by the parent. (Inferred from the shape of the data and from the flat
  curve; not profiled.)
* **G2, 1.59x vs DuckDB default at 8 threads on the Mac.** DuckDB at 0.33 s reads 3.2 GB/s; `table`'s sequential
  read is 290 MB/s and it scales 6.9x at 8 threads, not 16. The lever is the per-cell cost: `csv_value` calls
  `memchr` four times per cell (seen in the `sample`), and the group-count of 4 keys has little else to do.
* **Linux B1, B3, I1 against `cut|sort|uniq -c`: 1.6x, 4.7x, 1.8x.** That is not CSV-aware, not one thread (GNU
  `sort` uses all the cores it is given), and it cannot quote; it is a floor and `table` is not meant to beat it.
  Reported because the registration said it would be. Against csvtk and Miller, `table` wins B1 to I1 on Linux
  except B3 (csvtk 1.18x slower, a tie in effect).
* **A2: no row sort.** Missing, not slow. DuckDB t1 0.44 s, csvtk 2.3 s (Mac).
* **Memory.** `table` stays within its bounds in every cell: 135 to 183 MB for a million groups (limit 1 GiB), 2 MB
  or less for a scan, 36 to 50 MB at 8 threads on the 1 GB file. No memory loss against any incumbent by the
  registered definition (4x and above 64 MB). csvtk's `summary` uses 11.8 to 14.8 GB on B2/B4 and was killed on
  Linux B4; Miller 0.5 to 3.9 GB.
* **Cliffs.** None by the registered definition (time per MB 3x up): the longest, E1, is at 0.93x.

### Where `table` wins

Wide files (C1: 2.3x csvtk on the Mac, 3.2x on Linux, 13.8x Miller), all-quoted (D: 1.7 to 4.3x csvtk at one
thread), the 90 percent filter (H1: 3.3x csvtk), the 1 GB filter (6x csvtk on the Mac and 10x on Linux at one thread;
1.7x faster than DuckDB's default on the Mac at 8 threads), with memory of one or two megabytes for a scan.

## After the cell-cost round (branch `cell-cost`, `docs/history.md` "Cell cost")

The same cells, run again with the binary of `main` (`base`) and the binary of this branch in one run (the
contenders of a round are run one after another, `scripts/adversarial.py --base`), the incumbents re-measured at
the same time. Minimum of 5 (3 for 1 GB), seconds. Mac: a quiet machine this time (no other work, the Linux box doing
the benchmarking elsewhere); Linux: cores 0 to 5, niced, the soak still running, `--threads 8` (not the registered 6,
as before). The outputs were checked first, as before.

### Mac: `table` before and after, and against the incumbents

| cell | base t1 | new t1 | change | new t8 | against DuckDB t1 / default | against csvtk |
|---|---:|---:|---:|---:|---|---|
| B1 count, 100k keys | 0.177 | 0.124 | -30% | 0.087 | **win 1.37x** at t1; t8 1.09x **loss** to default (was 1.16x) | 2.4x faster |
| B2 sum, 100k keys | 0.180 | 0.127 | -29% | 0.087 | **win 1.39x**; t8 1.11x **loss** (was 1.17x) | 36x faster, 16 GB less |
| I1 `distinct:id` | 0.265 | 0.287 | +8% (see below) | 0.208 | **loss 1.40x** (was 1.54x); t8 2.5x loss (was 2.3x) | 2.0x faster |
| C1 select 3 of 200 | 0.352 | 0.184 | -48% | 0.040 | win 7.2x; t8 20x vs default | 4.3x faster |
| D1 cut, all quoted | 0.090 | 0.065 | -27% | 0.025 | win 4.3x; t8 3.3x | 4.2x faster |
| D2 filter, all quoted | 0.084 | 0.067 | -21% | 0.026 | win 3.7x; t8 3.1x | 5.5x faster |
| D3 group, all quoted | 0.145 | 0.076 | -48% | 0.025 | win 2.2x; t8 2.7x | 3.1x faster |
| E1 select, long fields | 0.210 | 0.184 | -12% | 0.182 | win 2.2x | **tie** (1.01x faster; was a 1.11x loss) |
| E2 group, long fields | 0.215 | 0.192 | -11% | 0.190 | win 2.1x | **tie** (1.02x faster; was a 1.09x loss) |
| H1 filter keeping 90% | 0.101 | 0.087 | -13% | 0.027 | win 3.3x; t8 4.1x | 3.7x faster |
| G1 1 GB filter | 1.682 | 1.460 | -13% | 0.264 | t8 1.8x faster than default | 6.8x faster at t1 |
| G2 1 GB group-count | 3.706 | 1.746 | -53% | 0.282 | t8 **win 1.23x** (was a 1.59x loss); t4 0.473 against 0.348 is a 1.36x loss | 3.5x faster at t1, 22x at t8 |
| B3 count, 1M keys | 0.725 | 0.792 | within noise (a repeated interleaved run: 0.95x to 1.02x) | 0.790 | **loss 3.4x**, t8 9.4x | 1.08x faster |
| B4 sum, 1M keys | 0.781 | 0.840 | within noise | 0.814 | **loss 3.1x**, t8 9.2x | 49x faster |
| A1 sort proxy | 0.722 | 0.763 | within noise | | **loss 1.59x** | 1.04x faster |

I1 is the one slowdown that was real in the adversarial run (+6 to +8 percent on the Mac, +7 percent on Linux in
interleaved runs): a `distinct` aggregate is not added in place, and the way that decides so was costing a call and
two loops on every row. Round 15 of `docs/history.md` decides it once, before the loop: interleaved, 15 runs, I1 is
0.293 s on `main` and 0.268 s now on the Mac (-9%), and 0.587 and 0.596 s on Linux (even). The tables here were
measured before that round.

### Linux: `table` before and after, and against the incumbents

| cell | base t1 | new t1 | change | new t8 | against csvtk / Miller / the shell pipeline |
|---|---:|---:|---:|---:|---|
| B1 count, 100k keys | 0.494 | 0.378 | -23% | 0.368 | csvtk 2.3x, Miller 2.7x faster; `cut\|sort\|uniq -c` 0.319: **loss 1.19x** (was 1.62x) |
| B2 sum, 100k keys | 0.504 | 0.261 | -48% | 0.335 | csvtk 43x, Miller 7.9x faster |
| I1 `distinct:id` | 0.635 | 0.648 | no change (before round 15) | 0.710 | csvtk 2.5x, Miller 5.9x faster; shell 0.360: **loss 1.8x** |
| C1 select 3 of 200 | 0.453 | 0.298 | -34% | 0.140 | csvtk 4.5x, Miller 18.6x faster |
| D1 cut, all quoted | 0.154 | 0.130 | -16% | 0.069 | csvtk 4.5x, Miller 5.8x faster |
| D2 filter, all quoted | 0.142 | 0.131 | -8% | 0.056 | csvtk 7.3x, Miller 3.6x faster |
| D3 group, all quoted | 0.243 | 0.130 | -46% | 0.059 | csvtk 4.0x, Miller 3.5x faster |
| E1 select, long fields | 0.264 | 0.239 | -10% | 0.244 | csvtk 1.24x, Miller 1.15x faster (was a tie with Miller) |
| E2 group, long fields | 0.267 | 0.250 | -6% | 0.272 | csvtk 1.18x, Miller 1.11x faster |
| H1 filter keeping 90% | 0.180 | 0.172 | -4% | 0.109 | csvtk 5.0x, Miller 3.2x faster |
| G1 1 GB filter | 3.662 | 2.677 | -27% | 2.181 (t4 1.972) | csvtk 29.8: 11x faster at t1 |
| G2 1 GB group-count | 5.420 | 3.481 | -36% | 2.167 (t4 2.167) | csvtk 16.2: 4.6x at t1, 7.5x at t4 |
| B3 count, 1M keys | 1.764 | 1.792 | no change | 1.818 | csvtk 1.16x, Miller 1.6x faster; shell 0.324: **loss 5.5x** |
| B4 sum, 1M keys | 1.892 | 1.725 | -9% | 2.013 | Miller 2.6x faster; csvtk killed at 14 GB |
| A1 sort proxy | 1.820 | 1.866 | no change | | `sort` 1.027: **loss 1.8x**; csvtk 1.24x, Miller 2.9x faster |

The Linux box is not what it was in the first round (its load changes by the hour, and these runs were faster than
the earlier ones for every contender), so the Linux base column is the thing to compare with, not the first round's
column. On Linux `table` now beats Miller and csvtk on every cell, including the two (E1, E2) where the first round
had it at a tie or behind; what is left against the shell's `sort|uniq` is the high-cardinality shapes (B3, I1, A1).

### What the round did not change

* **B3, B4, I1, A1 (a million keys, or `distinct` over a unique column): the same 3x to 3.4x loss to DuckDB at one
  thread and 9x at its default.** These rows each create a group; nothing in this round touches the cost of that
  (the groups are still moved by value to be added, the map still hashes the key twice).
* **`--threads` does not help them** (B3 t8 0.790 against t1 0.792): the serial merge, unchanged.
* **E1, E2 do not scale with threads** (t8 equals t1). Checked this time: the same file with *no* newline inside the
  quoted fields (`build/adv/longq.csv`, 20,000 rows, 1-10 KB) is 0.017 s at one thread and 0.0086 s at eight, and with
  newlines every 17 bytes (`longq_nl.csv`) 0.172 s and 0.170 s. So the cause is the one named in the first round (the
  speculation that a range starts outside quotes is wrong most of the time when quoted fields hold newlines), and the
  cost of the lines themselves: that file is six million short lines in 114 MB.
* **Memory** is the same, a few MB for a scan and 135 to 180 MB for a million groups; the cache of hot groups is 8 KB.

## After the row sort (branch `row-sort`, `docs/sort.md`)

A1 and A2 were "missing feature": `table` could not sort rows. It can now, and the cells measure it for real (A1 with a
text key, A2 an integer key, plus A3, the first 1000 by an integer, and A4, an integer key with ties; the whole output of
every contender is compared with Python's before timing). Seconds, minimum of 7 (Mac) and 5 (Linux, cores 0 to 5, niced,
the soak running, no DuckDB there):

| cell | `table` | csvtk | Miller | DuckDB 1 thread / default | `sort` |
|---|---:|---:|---:|---:|---:|
| A1 text, Mac | 0.483 | 0.789 | | 0.522 / 0.143 | 1.56 |
| A1 text, Linux | 0.912 | 2.014 | 4.936 | | 0.881 |
| A2 integer, Mac | 0.413 | 1.882 | | 0.429 / 0.123 | 1.87 |
| A2 integer, Linux | 0.958 | 4.420 | 4.377 | | 0.926 |
| A3 top 1000, Mac | 0.063 | 1.898 | | 0.196 / 0.082 | 1.60 |
| A3 top 1000, Linux | 0.115 | 4.324 | 3.958 | | 0.909 |
| A4 ties, Mac | 0.487 | 2.246 | | 0.432 / 0.125 | 2.00 |
| A4 ties, Linux | 1.080 | 4.714 | 2.626 | | 0.743 |

Losses, by the registered definition: against DuckDB's default **3.4x (A1, A2), 3.9x (A4)** (the sort is sequential; its
default uses 16 cores), **1.13x against DuckDB at one thread with ties**, and on Linux **1.45x against `sort -s -n` with ties**
and 1.04x on a text key (`sort` uses the cores it is given). Wins: 1.6x to 4.6x against csvtk, 2.4x to 5.4x against Miller,
1.04x to 1.08x against DuckDB at one thread on A1 and A2, and on the top 1000 3.1x against DuckDB at one thread and 1.3x against
its default, in 2 MB. The text cell was 1.7x behind DuckDB at one thread before round S1 of `docs/sort.md`.
