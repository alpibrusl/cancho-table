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

(Added below, after the run.)
