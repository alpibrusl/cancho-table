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
