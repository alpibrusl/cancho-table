# `table --order-by` on several threads: the gap, the design, two spikes, and where Amdahl stops it

`--order-by` reads and sorts on one core: `--threads N` was accepted and gave the same bytes sequentially (`docs/sort.md`).
Against DuckDB on 16 cores that is the one loss of the sort benchmark (`docs/benchmarks.html`, "Sorting rows"): 1M rows by a
text key in 0.483 s against 0.143 s, an integer key 0.413 against 0.123, an integer key with ties 0.487 against 0.125; and
on Linux `sort` is 1.04x to 1.45x faster. This document is the design that closes most of it, and two spikes that measure it:

* `scripts/spikes/sort/sortpar.cho`, a stand-alone program with the whole algorithm and nothing else (no quoted fields, no refusals, one or
  two keys, the output row is the input row), to find what the design costs and where it stops;
* an integration of the same algorithm into a scratch copy of `tools/table` (`psort.cho`, and edits to `par.cho`, `sorter.cho`,
  `table.cho`, `engine.cho`), so that quoted fields, refusals, the three bounds, `--where`, `--select`, typed keys and the rest of the tool are the real ones.
  **It gives the sequential answer, byte for byte, for every thread count and every range size** on the tests below. It is a spike: it takes a
  whole sort written as csv, and leaves a page, `--top` and json to the sequential sort (section 3.4).

Everything is measured, with its conditions next to the number. The Mac was shared with other work (load average 10 to 22), so
every time is the minimum of 9 (7 for the longest), and ratios are between numbers of the same run.

## 0. The answer first

* **The sort can be parallel and exact.** Each range of the file is sorted by its own thread into a *run* (rows already formatted as the
  answer's csv rows, in order, with the words that order them); the parent keeps the runs in file order; when the file is read the runs are cut at the
  same keys into one *partition* for each thread, each thread merges its partition (a stable k-way merge: by the keys, then by the run, that is by the
  order of the file), and the parent writes the partitions in order. The speculation of the scan (`docs/parallel.md`), the typed refusals, the bounds and
  the output are the existing ones: a range that cannot be taken as it is, is read again by the parent with the sequential code.
* **Measured, Mac, 16 cores, 1M rows of 40 bytes, seconds:**

  | | `table` today | `table --threads 16` (integrated) | spike, 16 threads | DuckDB 1 thread / default | `sort` |
  |---|---:|---:|---:|---:|---:|
  | A1 text key | 0.499 | **0.065** | 0.045 | 0.501 / 0.136 | 1.60 |
  | A2 integer key | 0.471 | **0.062** | 0.042 | 0.424 / 0.127 | 2.17 |
  | A4 integer key, ties | 0.504 | **0.063** | 0.043 | 0.419 / 0.126 | 2.13 |
  | B1 two keys `s,v:int` | 0.931 | **0.076** | 0.054 | 0.522 / 0.149 | 2.45 |
  | A3 first 1000, descending | 0.063 | 0.062 (sequential by the gate) | 0.015 | 0.191 / 0.081 | 1.84 |

  **The integrated sort is 7.5x to 8x faster than today's and 2.0x to 2.2x faster than DuckDB's default; the spike, which moves less data, is 11x
  and 3.0x.** The loss of `docs/sort.md` (3.4x to 3.9x slower than DuckDB's default) becomes a lead of 2x to 3x.
* **Linux, 6 threads on the box's cores 0 to 5 (three physical cores):** (quiet box: `table` 0.63 and 0.62, `table --threads 6` 0.28 and 0.25, DuckDB on the same cores 0.27 and 0.23, section 6.2); in the busier session below `table` 0.72 (A1) and 0.77 (A2); integrated `--threads 6` 0.38 and 0.37 (**1.9x and 2.1x**);
  spike 0.26 and 0.27 (2.8x); GNU `sort` 0.47 and 0.84. It is 1.2x (A1) and 2.3x (A2) faster than GNU `sort`, where today it is 1.5x slower (A1) and level (A2). Three physical cores cap the gain near 3x.
* **Per core the new way is cheaper, not only parallel (Mac):** the algorithm on one thread (the spike's single run) is 0.21 s for the integer key against 0.47 to 0.50 for the
  sequential sort; the integrated build's CPU time at 2 to 4 threads is 0.25 to 0.37 s for one key (0.44 to 0.46 for two) for what takes 0.47 to 0.50 s of CPU sequentially (0.93 for two keys). **On the x86-64 box the per-core gain is small** (one core, two threads
  on it: 0.68 against 0.84 s for the integer key, 0.82 against 0.87 for the text key).
* **Where Amdahl stops it: not at the sort, at the parent's copies.** At 16 threads the sort itself is under 10 ms of work per thread; what is left is the parent *moving bytes* because no
  thread can read what another made (no shared memory, no atomics): the runs up from the threads, the slices of the runs into the threads that merge, the merged rows up, and out. Measured in the
  spike (1M rows): of 44 to 49 ms the parent alone spends 8 to 11 in phase A and 12 to 18 in phase C, an effective serial fraction of 15%; on 10M rows (425 MB) 260 to 295 ms of 556 to 607. **The floor of this design is
  about 35 to 45 ms for this file on this Mac**: the four copies are 256 MB, 32 ms at the 8 GB/s the parent's copies run at (1.3 GB in 170 ms in the 10M run), overlapped in part with the threads' 20 ms; the spike is at 42 to 45
  (the integrated version, with two more copies and a bigger entry, is at 62 to 76), and **at 10M rows DuckDB's default (0.61 s) is ahead of both (0.68 s the spike, 0.81 s
  integrated; `table` today 7.3 s)**: the memory traffic, not the compute, is what is divided by too few cores. Section 7 has the shares and what moves the floor.
* **What it costs:** memory, 2.5x to 3x the sequential sort's (about 12x the size of the file in the integrated version, up to 589 MB for 40 MB; an estimate of 6 to 7x for the lean entry of section 7.3); the work is more code (`psort.cho` is
  about 1,200 lines) and more cases to keep equal (section 4); and it helps a *whole sort written as csv* only. A page, `--top` and json (the default format, with its default limit of 1000) stay sequential, which is
  already fast for them; section 3.6 designs their parallel form and the spike measures it (A3: 0.063 s to 0.015 s) but it is not integrated.

## 1. The gap, reproduced

`scripts/adversarial.py --cells A1,A2,A3,A4` on the Mac (16 cores: 12 performance and 4 efficiency), the 1M-row file `F2` the script generates. The harness times DuckDB through `COPY (...) TO '/dev/null'`;
in its first run on the loaded Mac it timed `duckdb -t1` of A1 and A2 at 0.059 and 0.055 s, against 0.50 and 0.42 s by hand. **The by-hand numbers (and the 0.522 and 0.429 of `docs/sort.md`) are right; the script's were runs that failed.**
DuckDB takes a lock on the file it writes, and `COPY ... TO '/dev/null'` finds `/dev/null` locked by any other DuckDB on the machine (another session's benchmark, on this shared Mac): `IO Error: Could not set lock on file "/dev/null": Conflicting lock is held in ... duckdb`, exit status 1, after 50 ms.
The timing loop of `adversarial.py` did not look at the status, so the minimum of five was a failed run (reproduced: 24 of the same command started 8 at a time, 23 failed with that message after 0.048 to 0.12 s, the one that did not took 0.60 s). The answer is checked before the timing, with `/dev/stdout`, which is why the check passed. The same script run again, alone, gives 0.52 and 0.42 s (`duckdb -t1`) and
0.13 to 0.17 and 0.12 (default). `scripts/bench.py` and `scripts/bench_parallel.py` stop on a non-zero status and were not affected. The fix is a separate commit, `timed_run` in `adversarial.py` with `tests/conformance/test_adversarial_harness.py` (4 tests; they fail on the old script).
`scripts/spikes/sort/bench_sort.py` times the same questions, checks every contender's whole output first (the sequential `table` is the oracle; the others "same rows, in key order") and has the rest of this document.

| | `docs/sort.md` | this run, min of 9 |
|---|---:|---:|
| A1 `table` / DuckDB 1 thread / default | 0.483 / 0.522 / 0.143 | 0.499 / 0.501 / 0.136 |
| A2 | 0.413 / 0.429 / 0.123 | 0.471 / 0.424 / 0.127 |
| A3 (first 1000) | 0.063 / 0.196 / 0.082 | 0.063 / 0.191 / 0.081 |
| A4 | 0.487 / 0.432 / 0.125 | 0.504 / 0.419 / 0.126 |

Linux (i7-1260P, `taskset -c 0-5`, another session's jobs came and went, GNU `sort` is `/usr/bin/gnusort`, the `sort` there being uutils): `table` 0.68 to 0.74 s (A1), 0.69 to 0.86 (A2), 0.75 to 0.90 (A4);
GNU `sort` 0.47 to 0.50, 0.70 to 0.84, 0.68 to 0.80 across three sessions. `docs/sort.md` had 0.912, 0.958, 1.080 under the soak. DuckDB 1.5.6 is in `~/bin` there (the gram session of section 6.2 used it).

## 2. Where the sequential sort spends its time

A scratch build of `table` that stops after a stage (`--from 999999998` skips the sort, `999999999` skips the write), 1M rows, Mac: **reading and holding the rows 0.074 s (13%), ordering
their places 0.185 s (32%), writing them out 0.31 s (55%)** for the text key; 0.087, 0.16, 0.35 for the integer key. The write is the biggest piece, and it is two things: the same file already in order (sort by
`id`) writes in 0.076 s, so reading each record's cells again and writing them costs 0.076 s, and the order of a random sort costs another **0.23 s, 230 ns for each of the 1M rows**, two dependent cache misses
(the index entry, then the record in the 40 MB that holds them). `docs/sort.md` calls this "random reads"; they are reads of memory, not of the file.

Both are removed by the design, and neither needs threads: a range of a few thousand rows fits the caches (the spike's materialisation of a run costs 20 ms for 1M rows of sorted input and 75 to 140 ms for random
input in one run of 1M rows, 40 to 135 ms in all when the 1M are 16 runs of 62,500), and a row is formatted once, in its thread. The spike on **one** thread (one run, one partition) is 0.21 to 0.23 s for the
integer key, 0.30 to 0.39 for the text key.

## 3. The design

### 3.1 The phases

```
file ──A──▶ run 0 .. run R-1 ──B──▶ cut points ──C──▶ partition 0 .. P-1 ──D──▶ standard output
      R threads                      the parent        P threads               the parent
      (waves of T)                   (sparse samples)  (k-way merge)
```

**A. The runs.** The file after the header is cut into ranges at line starts, speculating that a range starts a record, exactly as `par.run` does for groups (`docs/parallel.md` section 3). A thread
reads its range with `pread`, holds the rows it keeps in a sorter of its own (`sorter.cho`: the sequential bounds and `--where`, the sequential code), sorts them, and writes the *run* into its slot: for each
row in order an entry with the words that order it and the row **formatted as the csv answer writes it** (`engine.emit_buf`, the sequential write's own function, so `--select`, quoting and one-field rows are the sequential ones).
The parent takes the ranges in file order (waves of `T` ranges: `--chunk-bytes` of them to a thread).

**B. The cuts.** A run has a sparse index (every 64th entry: offset and words). The parent takes `P - 1` quantiles of all the samples as splitters and finds in each run where each falls (a binary search
in the sparse index and a short scan): microseconds (1 ms for 10M rows).

**C. The partitions.** Partition `q` is, from every run, the entries between splitter `q - 1` and splitter `q`. The parent copies those slices into the input of thread `q`, which merges them with a binary heap of the
`R` run heads ordered by the words, by the full keys when the words are equal and a key is longer than its words, and then by the run: a tie goes to the earlier run. The thread writes only the rows.

**D. The write.** The parent writes the outputs in partition order. A refusal can only happen in A, before anything is written, as in the sequential sort; a failed write is `io.write-failed` as always.

### 3.2 A run

All integers are 8 bytes. A header of 64 bytes: rows, bytes of entries, the bytes the sorter held (what `--max-state-bytes` counts), the unquoted-key bytes of the last row, the flags of all rows ORed, the number of
samples, the keys. The samples (32 bytes: offset, three words). The entries:

```
row length | keys' text length | flags | word 1 | word 2 | one word for each later key | for each text key: length, bytes | the row, as written
```

An integer key (`:int`, `:dec(S)`, `:float`: the sorter already turns them into a number that orders as the value) is its number. A text key is its first 7 bytes as a big-endian number, zero-padded,
which orders as the bytes do (the first key has a second word, bytes 7 to 14). A flag says that a text key may be longer than its words (more than 14 bytes for the first key, 7 for the others, or a NUL):
only then do equal words need the bytes. This is the sorter's two-prefix-words comparison (`docs/sort.md` round S1) moved from "at each comparison" to "once for each row".
An entry is 80 bytes for a 40-byte row and an integer key, 98 with a text key, 88 in the spike: more than twice the row, and the reason for most of the memory (section 3.7).

### 3.3 The cuts are exact

A partition is an interval of a *coarsening* of the order of the sort: the first key's two words and, **only when no row of any run has a flagged key**, the second key's word as well. A coarser order cannot put a
row of a later partition before a row of an earlier: if the words of `a` are below those of `b`, the key of `a` is below the key of `b` (descending reverses both). Rows with equal words are in one partition,
so a tie never crosses a cut, and inside a partition the merge sees every run's rows of that interval. Any values of the splitters are correct (each is a threshold in a preorder); the quantiles only decide the balance.
The balance has one flaw: a first key with 4 values gives 4 partitions unless the second key's word cuts them (it does, when every key is exact: B1 goes from 42 ms of merge to 18); an all-equal first key
with a flagged second one gives one partition, which is the sequential merge. The fix, cuts made with the full key and `(run, position)` to split equal keys (the total order is `(keys, run, position)`), is allowed
by the order and is not built.

### 3.4 Why the answer is the sequential one, and what stays sequential

* **Stability and ties across ranges.** The sequential sort is a stable merge sort of the rows in file order, so its order is `(keys, file position)`. A run holds the rows of consecutive records of the file in `(keys, position)` order;
  the runs are in file order (a range is taken in order, and what the parent reads itself is closed into a run before the next range is taken); the merge orders by `(keys, run)`, and inside a run the order already is
  `(keys, position)`. So the merged order is `(keys, position)`: the same. Descending reverses the comparison of keys, never of positions; Python's `sorted(..., reverse=True)` also keeps equal elements in order, and the
  tests compare with it (`test_sort.py`).
* **The speculation.** A range is taken only if its first line is where the previous range's last record ended, its thread finished with nothing the sequential read would have stopped at, and the bounds below hold
  (`par.run`, the existing rule). A quoted newline at a boundary makes the next range wrong; the parent reads it again, into its own sorter, and that sorter becomes the next run. With `--chunk-bytes 1` every record is a boundary and the answer is the same.
* **Refusals.** A cell that is not an integer (`:int`, `:dec`, `:float`) or too long, a bad quote, a ragged row, a long line: the thread's range stops with a status, the parent does not take it and reads it again with
  the sequential rules, which raise the sequential refusal at the sequential row, line and column. The ranges are looked at in file order, so the first refusal in file order wins; a refusal in a later range, found first by a faster thread,
  is not looked at (as with groups).
* **The bounds are exact.** `--max-sort-rows` and `--max-state-bytes` are refusals at the first row that passes them. The sorter refuses a row when it holds `max_rows` already, or when the bytes it holds, the record of that row and 8 integers of
  index for each row, pass `max_state`. Both only grow with the rows, so a range whose last row passes them has passed them at every row: the run header carries the sorter's size and the unquoted-key bytes of its last row (which the test
  at that row does not yet count) and the parent adds them to what the runs before it hold. A range that does not pass is read again with the bounds lowered by what the runs hold (`max_rows - rows`, `max_state - bytes - 8 * width * rows`), and the sequential code
  refuses at the row where the whole sequence would have. `--max-rows` is the scan's own. Tested at every value in a window of 80 around the threshold, with doubled-quote keys.
* **What is sequential.** json (its byte budget is counted in the order of the answer), `--top`, `--limit`, `--from`: the gate is `psortable` in `table.cho`. The rows they want are few and the sequential sort keeps them in bounded memory.
  `--threads 1` is untouched, as `docs/parallel.md` says.

### 3.5 The output phase, options (b)

| | what | measured |
|---|---|---|
| today | records in one buffer, an index, written in sorted order: read the cells again, write them, two cache misses a row | 0.31 s of 0.57 (section 2) |
| **runs of formatted rows** (built) | the rows are copied once into the run in the thread, in order; the merge copies bytes | 20 ms (sorted input) to 75 to 140 ms (random input) of CPU for the materialisation of 1M rows on one thread; 40 to 135 ms in all at 16 threads (runs of 62,500 rows) |
| keys and records streamed | the run is `(key, raw record)` and the merge threads format the rows | not built or measured: the same as the row above with the formatting moved from phase A to phase C, which is the phase the parent already bounds; no better |
| re-read by `(offset, length)` | a run holds the words and the file offset (48 bytes a row, not 88); a merge thread `pread`s each row in sorted order | **0.60 s on one thread (against 0.23), 0.73 s on 4 and 0.88 s on 16** (CPU 2.7 s and 8.5 s): a `pread` is 0.4 us and the threads contend on the one file. Memory 231 to 265 MB against 414 to 434. Rejected |
| today's, with the cells not read again | a record with no quote is its own csv row | would save the 0.076 s of the write (the sorted-input control of section 2); sequential only; not built |

The sorted-in-memory bound, stated: the integrated build holds, at the peak, the runs (2.5 times the rows), the partitions' inputs and outputs (the same, twice) and the parent's copy of the runs: section 3.7.

### 3.6 The first N, and pages

For `--top N`, or a page (`wanted = from + limit + 1`), every thread keeps its own bounded selection (the sorter's: collect `2N + 1`, sort, cut to `N`, drop the rows that are not strictly before the last kept one),
writes at most `N` entries, and the parent merges the runs (one partition, `N * R` entries at most). The first `N` of the merge are the first `N` of the file, because a row among the first `N` of the file is among the first `N` of its
range (what precedes it in its range precedes it in the file). The spike does this and the answers equal Python's (`sort_check.py`: top of 1, 3 and 100): **A3 0.063 s sequential, 0.057 s on one thread, 0.015 s on 16**
(`sort | head` 1.84 s, DuckDB 0.19 s and 0.081 s).

One exactness problem is real: the sequential top-N cuts back as it goes, so the bytes it holds at a row depend on its cut-backs, and a thread's cut-backs are not the same, so the state bound could refuse in the one and not in the other.
The rule: the parent takes the threads' answers if `max_state` is at least `(2N + 1)` rows of the longest record seen plus their index (a bound that no sequence can reach), and otherwise runs the sequential sort. It is a check after the fact, it
costs a second read only for a file whose bound is near its data, and it is not built. For json the entries hold the raw record and the parent formats the `from + limit` rows in order with the sequential budget logic, which is cheap.

### 3.7 Memory

Peak of the integrated version, 1M rows of 40 bytes (a 40 MB file): 254 to 589 MB at 2 to 16 threads across the cells (A1 534 at 16, A2 477, B1 589), against 173 to 245 MB sequential (B1 has two keys); the spike 410 to 435 MB. At 16 threads, 10 to 15 times the file.
The pieces: the threads' sorters (twice the bytes of their range), the slots (three ranges each, allocated and zeroed by the parent), the runs (2.5 times the rows) and the parent's copy of them, the partitions' inputs and
outputs (the same again, twice). At 10M rows (425 MB): 2.9 GB integrated, 3.9 GB spike, 2.0 GB sequential, 1.9 GB DuckDB. The bound on the rows held is the same flag (`--max-state-bytes`), and the memory is about ten times it
in this version: a 1 GB bound can use 10 GB. A lean entry (section 7.3) would be an estimated 6 to 7 times; `parallel-min-bytes` and `--threads 1` are the user's way out. The parent reserves address space for the runs (`min(3 x file, 4 x max-state)`) and writes only what it uses.

## 4. Tests, and what they killed

`tests/conformance/test_psort.py` runs a plan sequentially and then at `(threads, bytes of a range)` in `(2, 1) (3, 7) (5, 64) (16, 1000) (64, 3) (4, 4 MiB)` and requires the same status, standard output and standard error:
plain sorts (text, integer, descending, two and three keys, `#N`, `--select`, `--where`); all-equal and two-valued keys; text keys that agree for 7 and 14 bytes, are empty or hold a NUL; quoted newlines and doubled quotes at every boundary;
typed keys (`:dec(2)`, `:float`); the integer edges, ranges too wide to offset included; 60 files with one to three bad cells, for the first refusal in file order; a ragged row and two bad quotes at four places;
`--max-sort-rows` at 1, 99, 100, 399, 400, 401, 2000; `--max-state-bytes` at every value from 40 below to 40 above the threshold found by bisection, and six others; `--max-rows`; a bound with a refusal after it; empty and one-row files, no newline at the end, CRLF, blank
lines, tab and `;` delimiters and a byte order mark; 60,000 rows in many ranges and waves; pages, `--top`, `--from` and json in both formats (sequential by the gate). And the **1,600 random plans of `test_sort.py`, each at five settings in csv and json** (16,000 comparisons with the sequential bytes).

Three bugs the tests found in the first version of the radix sort, the first two the language's checked arithmetic (a trap, not a wrong answer): `hi - lo`, the range of an integer key, overflows when the keys span more than 63 bits (found by `:float`,
whose order-preserving integers span the whole range) and again for `-2^63` with `0` (found by the integer edges); and a thread whose range holds no row asked for the least of nothing (an index out of range). All fixed, and `test_integer_edges`
and the spike's `sort_check.py` (`INTS` now has `+-(2^63 - 1)`) keep them. The whole conformance suite (370 tests, 2 skipped) passes with the change on the Mac, and `test_psort.PSort` with `test_sort.py` (31 tests) on the Linux box.

**The spike** is checked by `scripts/spikes/sort/sort_check.py` against Python's stable sort on random files (shapes: awkward text keys, short ranges of integers, 7-digit keys, a long shared prefix with outliers; one or two keys, text or integer, either
direction; top 0, 1, 3, 100; threads 1, 2, 3, 5, 8, 16; both sorts; rows in the run or read back): 15,840 runs, 0 differences on the final code (30 files, nine key specs, four tops, six thread counts, both sorts, rows or offsets) The 10M-row file, the URL file and F2 give the same md5 from `table`, the spike with
either sort and the integrated build at the thread counts tried (1 to 16, 64).

## 5. Cheaper comparisons (c)

All in the spike, 1M rows of F2; "sort" is the part of phase A that orders the rows, CPU milliseconds summed over the threads.

### 5.1 Radix on the key words

A stable least-significant-digit radix sort of the key words (an integer key: its number minus the least, 11-bit digits; a text key: the bytes of its words, 8-bit digits), skipping the digits that are the same in every row, replaces the
merge sort of row numbers (the sorter's, with a function call and random reads of the prefix arrays at each of the n log n comparisons).

| sort, CPU ms | merge sort | radix | |
|---|---:|---:|---|
| A2, integer key, one run of 1M rows | 175 to 310 | 21 to 47 | 6x to 8x |
| A2 at 16 threads (runs of 62,500) | 176 | 22 | 8x |
| A1, text key `key0000001`, one run | 179 to 306 | 81 to 167 | 2x (three live bytes in each word: six passes) |
| A1 at 16 threads | 127 to 173 | 28 | 5x |
| B1, two keys (4 values, then `v:int`), one run | 332 | 108 | 3x (the second key's digits are two more passes) |

(The ranges are quiet and loaded runs.) Whole spike, one thread: A2 0.31 s with the merge sort, 0.16 to 0.21 with the radix sort; A1 0.29 to 0.46, 0.21 to 0.32. The integrated build uses the radix sort in the threads
(`psort.order_radix`, which also covers `:dec`, `:float`, descending, any number of keys), and leaves `sorter.order` to the sequential sort.

### 5.2 Equal words, and the full compare

Only rows whose words are equal and that may differ in their keys (a flag: a text key longer than its words, or with a NUL) are compared by their bytes, as a stable merge sort of the group. Groups are looked at level by level: equal first key's
words, then equal second key's, and so on, so the order the radix gave by the later words (which a longer first key can contradict) is restored by the full comparison, and ties keep the order of the file. For the benchmark's keys (10 bytes) no row
is flagged: the two words (14 bytes) are the key, and the full comparison is never made.

### 5.3 A better prefix for text keys

Keys that share a long prefix, as URLs and paths do, make every row's words equal: one group, and the full comparison for all. 1M keys `https://www.example.com/products/item-0906631` (33 shared bytes), seconds:

| | 1 thread | 16 threads |
|---|---:|---:|
| `table` today | 1.81 | |
| spike, words of the first 14 bytes | 1.96 | 0.41 (all the cuts equal: one partition) |
| integrated build (the same words) | | 0.33 |
| **spike, words after the bytes the file's keys share** | **0.40** | **0.065** |
| DuckDB 1 thread / default | 0.65 | 0.169 |

The fix, built in the spike: the parent samples 32 keys spread over the file and takes the bytes they all start with (at most 200); the words are the 14 bytes *after* them; a key that does not start with them is
*below* all the others (word 0) or *above* (a word past the largest), flagged, and ordered by its bytes among its kind. It is exact for any file (a sample that is wrong only makes more outliers). The words of all runs must agree on the skipped bytes, so it is the parent that
chooses them once; `sort_check.py` has keys with and without the prefix, shorter than it, equal to it, with a NUL after it. Not in the integrated build.

### 5.4 Two keys

`docs/sort.md`: two keys cost twice one (`--order-by s,v:int`, 0.87 s: four values then a number, so most pairs tie on the first key's words and take the full comparison, which finds the cells again and compares them). The design keeps one word for every key,
so a key that ties on the first does not go back to the bytes. B1: `table` 0.93 s, the spike 0.29 s on one thread and 0.054 on 16, the integrated build 0.076 on 16 (the cuts use the second key's word when every key is exact).

## 6. Results

### 6.1 Mac, 16 cores (12 performance, 4 efficiency), 1M rows of 40 bytes, minimum of 9, seconds

`table --threads N` is the integrated build, "spike" the stand-alone program with the radix sort, N threads and N partitions.

**A1, text key `k1m`**

| threads | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| `table --threads N` | 0.499 | 0.190 | 0.133 | 0.106 | 0.069 | 0.065 |
| spike | 0.299 | 0.157 | 0.089 | 0.057 | 0.048 | 0.045 |

DuckDB 0.501 (1 thread) and 0.136 (default); csvtk 0.807; `sort` 1.60.

**A2, integer key `u`**

| threads | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| `table --threads N` | 0.471 | 0.144 | 0.104 | 0.084 | 0.067 | 0.062 |
| spike | 0.210 | 0.125 | 0.074 | 0.051 | 0.050 | 0.042 |

DuckDB 0.424 and 0.127; csvtk 1.91; `sort` 2.17.

**A4, integer key `v`, about 10 rows to a value**

| threads | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| `table --threads N` | 0.504 | 0.166 | 0.108 | 0.093 | 0.070 | 0.063 |
| spike | 0.219 | 0.115 | 0.074 | 0.050 | 0.045 | 0.043 |

DuckDB 0.419 and 0.126; csvtk 1.88; `sort -s -n` 2.13.

**B1, two keys `s,v:int`**

| threads | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| `table --threads N` | 0.931 | 0.259 | 0.164 | 0.130 | 0.084 | 0.076 |
| spike | 0.292 | 0.160 | 0.090 | 0.064 | 0.060 | 0.054 |

DuckDB 0.522 and 0.149; csvtk 2.06; `sort -s` 2.45.

**A3, the first 1000 by `u`, descending** (`table --threads N` is the sequential sort: 0.062 to 0.064 at every N)

| threads | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| spike | 0.057 | 0.032 | 0.021 | 0.016 | 0.015 | 0.015 |

DuckDB 0.191 and 0.081; `sort | head` 1.84.

From 12 to 16 threads the gain is a few ms: four of the 16 cores are efficiency cores, and the ranges are equal. The CPU time of the integrated build rises from 0.25 s (2 threads) to 0.36 (16) for the integer key: the copies.

### 6.2 Linux x86-64, i7-1260P, `taskset -c 0-5` (three physical cores, two threads each), minimum of 15 for the focused run, seconds

| | `table` today | `table --threads 6` | spike, 6 threads | GNU `sort` (6 threads) |
|---|---:|---:|---:|---:|
| A1 text | 0.724 | **0.381** | 0.263 | 0.470 |
| A2 integer | 0.767 | **0.371** | 0.268 | 0.842 |

On the quiet box (load 0.3, the same cores, DuckDB `SET threads=1` and with the affinity mask of 6 cpus, its output compared by md5 with the sequential `table` first: equal), minimum of 7: A1 `table` 0.634, `table --threads 6` **0.277**, DuckDB 0.625 (1 thread) and **0.268**
(6 cpus); A2 `table` 0.620, `table --threads 6` **0.246**, DuckDB 0.520 and **0.230**. On Linux the integrated sort is level with DuckDB at the same cores, and today's is level with DuckDB's one thread.

The full session of nine runs, same machine: A4 0.899 / 0.421 / 0.284 / 0.799; B1 1.717 / 0.597 / 0.320 / 2.682; A3 0.086 / 0.088 (sequential) / 0.040 / 0.486; one thread of the spike 0.60, 0.57, 0.56, 0.68 (A1, A2, A4, B1), two threads 0.36, 0.35, 0.35, 0.43. The gain at 6 threads is 2.0x to 2.3x
over the spike's own one thread, which is what three cores and their second threads give (`docs/parallel.md` measured 2.4x for the groups there). The first quiet session had `table` at 0.68 (A1) and 0.69 (A2); the box was not quiet in the later ones.
**Per core**, one core (cpu 0), the integrated build with 2 threads on it: 0.82 s (A1) and 0.68 (A2) against 0.87 and 0.84 sequential; the spike's single run 0.70 and 0.52. On the x86-64 box an 8-byte integer read from a byte slice
costs 4.5 ns and on the Mac 2.0 (`scripts/spikes/sort/i64cost.cho`; the language has no 8-byte load or store of a slice, `agg.get_i64` is eight byte loads), which is part of it.

### 6.3 Larger: 10M rows (425 MB), Mac, minimum of 3, seconds

| | seconds | peak RSS |
|---|---:|---:|
| `table` today | 7.30 | 2.0 GB |
| `table --threads 16` | **0.81** | 2.9 GB |
| spike, 16 threads | 0.68 | 3.9 GB |
| DuckDB 1 thread / default | 4.17 / **0.61** | 1.9 GB (default) |

`table --threads 16` is 9x its own sequential sort, and 1.3x slower than DuckDB's default: the file's bytes pass through the parent about five times, 2 GB of copies. The same three outputs have the same md5.

### 6.4 The text file with a long shared prefix: section 5.3.

## 7. Where Amdahl stops it

### 7.1 Measured shares

The spike with a timer in each frame of the parent that counts only the parent's own work (allocate the job, copy a run up / copy slices in and the output up; `clock_ms`, so 1 ms), 16 threads, A2:

| | A: runs | of it the parent alone | B: cuts | C: partitions | of it the parent alone | D: write | whole |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1M rows (40 MB), ms | 26 to 31 | 8 to 11 | 1 | 16 to 22 | 12 to 18 | 5 (to /dev/null) | 44 to 49 |
| 10M rows (425 MB), ms | 335 to 401 | 82 to 129 | 8 | 198 to 213 | 165 to 179 | | 556 to 607 |

The threads' own work, summed (ms): 10M rows read 76 to 119, find the keys 554 to 702, sort 1,048 to 1,241, materialise 1,445 to 1,672, so 3.3 s; over 16 threads that is 0.21 s. The parent alone is 250 to 310 ms: **half of the 10M run is the parent copying**, and in
phase C it is the whole phase (the threads merge while the parent is still building the inputs of the later ones, and the last merge ends right after the last copy).

For the integrated build (A2, minimum of 15, loaded): 4 threads, phase A 101 ms, the cuts +10, the merge +17, the write +0: 126; 16 threads A 63, cuts +0, merge +19, write +0: 77 to 82.

### 7.2 The model

Speed-up from one thread to 16 in the spike, A2: 0.210 / 0.042 = 5.0, an effective serial fraction `s = (1/S - 1/P) / (1 - 1/P)` of **15%**, 31 ms of the 210; the parent's copies are 20 to 29 ms of them. In bytes, for 1M rows of 40 bytes: the runs up from the
threads (88 MB), the slices into the merge threads (88 MB), the merged rows up (40 MB), out (40 MB); at 8 GB/s that is 31 ms. Where the data is larger the copies grow with it and the threads' work grows with it too, so the share of the parent
stays near a half (10M rows: 0.26 to 0.30 s of 0.56 to 0.61), and the speed-up near `P / (1 + (P - 1) s)` = 4.9 at 16 threads for `s` = 0.15. On the Linux box the spike's 6 threads give 2.0 to 2.3 over its own one, which three cores explain.

### 7.3 What moves the floor

| change | saves | status |
|---|---|---|
| entries of 16 to 24 bytes of header, not 48 (one word for an integer key; the length of the row in 3 bytes; no key text unless flagged) | a third of every copy: the integrated entry is 80 to 98 bytes for a 40-byte row, a lean one 49 to 57 (estimate) | designed |
| runs and outputs in memory that is not zeroed (`buffer.empty(heap, n)` and `buffer.room`/`filled` in a job instead of `box_slice`) | the parent zeroes 3 to 4 times the file before it writes anything; measured **0.093 to 0.082 s** (12%) in the integrated build | built (the partitions' buffers), and `slots` not yet |
| the runs kept where the threads' slots are, not copied again (`psort.take` is a copy of the whole run) | one copy of the file | designed |
| partition frames in reverse order, so that the first partition is the first to join and the parent writes it while the others merge, without the copy up | one copy, and the write overlaps | designed |
| a thread that can read, not write, memory the parent owns (`docs/threads.md` lets a non-slice reference cross a `spawn` if the thread is joined first, but a thread gets one payload, and `docs/parallel.md` found that a job's plan has to be copied for that reason) | the slices copy, and the copy up | needs a language change, or a proof that a shared `&` to a struct will do |
| an 8-byte load and store on a byte slice | an integer read costs 2.0 ns on the Mac and 4.5 on x86-64 (eight byte loads), and the merge reads about six for each entry | needs a language change |
| more ranges than threads, handed out as threads finish (no queue, no atomics) | the efficiency cores of the Mac, probably (a static range is as slow as its core; 12 to 16 threads gain only 3 ms) | needs a language change |

With the first four the floor of the Mac sort is two copies of 50 MB and the write: an estimate of **12 ms plus the threads' 20 ms, overlapped in part: 30 to 40 ms for this file at 16 threads, which the spike already nearly has (42)** because it has the smaller entry and no `psort.take`.

### 7.4 A realistic target

* Mac, 16 cores, 1M rows: **0.04 to 0.065 s**, 2x to 3x faster than DuckDB's default today. The integrated version is at the top of that range.
* Linux, 6 threads on three cores: **0.25 to 0.4 s** for 1M rows, which is level with or ahead of GNU `sort` (1.2x to 2.3x ahead for A1 and A2 in this run, and 4.5x ahead for two keys). Three cores cannot give more than 3x over the sequential sort, which is 0.7 to 0.9 s there.
* Beyond a few million rows the parent's memory traffic is the cost: **10M rows: 0.7 to 0.8 s, 9x faster than today but level with DuckDB's default**; what would change that is the shared memory above.
* Small files: below `--parallel-min-bytes` (1 MiB of data) the sort is sequential; with threads and a file near it the 64-thread default of the tests is wasteful (a partition of a few rows); the partition count should be `min(threads, rows / 16,384)`.

## 8. Mutants

`scripts/psort_mutants.py` has 26 mutants of the runs, their merge, the bounds that cut a read short and the gate (a tie to the later run, a descending key merged ascending, a key longer than its words compared by words, the
flags not gathered, the keys' text length not written, a run taken whatever the bounds, the bounds of the sorter that goes on not lowered, `--max-sort-rows` allowing one more, the bytes of the last row not counted, a page sorted by threads
and not cut, and so on), run against `test_psort.PSort`: **26 of 26 killed**. The ones that survived the first version were all equivalent (a splitter is a threshold wherever it lies; a run refused too early is read again; `emitted` is for json),
or were gaps of the tests (csv with `--limit`, `--top` and `--from` through the gate), which `test_a_page_a_top_or_json_is_the_sequential_sort` now has. The equivalent ones are listed in the script with the reason.
The scripts of the older mutants that matched the lines this change edits (`sort_mutants.py`, `parallel_mutants.py`, `report_mutants.py`) are updated and check clean.

## 9. What it would take to ship

1. **Decide the form of the gate.** The integrated `psortable` is "csv, no page, no `--top`, `--threads > 1`". json with a large `--limit` (above the bound that makes a page a full sort) is a full sort too and could take the parallel path with the
   raw rows in the runs (section 3.6); the default json page is the sequential one.
2. **The lean entry and the fewer copies** of 7.3: the first four rows are a few hundred lines and no new idea; an estimate of 62 to 66 ms down to about 45 on the Mac, and 6 to 7 times the file in memory.
3. **The prefix after the shared bytes** (5.3) in the integrated build: needs the parent to sample the keys through `reader.fields` (quoted keys: skip the sampling), and the words to be those after the bytes; the fixup groups are the existing ones.
4. **The parallel top-N and pages** (3.6) with the after-the-fact bound check.
5. **Radix for the sequential sort too.** `order_radix` does not depend on threads: it is a drop-in for `sorter.order` in `finish_sort` and `cut_back` (A2's order 0.185 s to about 0.03). The 1,600-plan test and `sort_mutants.py` are its gate.
6. **Memory flags**: say in the help of `--threads` and `--max-state-bytes` that a parallel sort holds about ten times the bound until 2. is done; and the partition count rule of 7.4.
7. **Language asks** (upstream, `lex-sys` first): an 8-byte load and store on a byte slice; a way to let a thread read, not write, a box the parent owns for the thread's life; and the work queue (atomics, `docs/atomics.md`) for ranges handed to whoever is free.
8. **Docs**: `docs/sort.md` "Threads" (it still says the sort is sequential; this branch edits it), `docs/benchmarks.html` "Sorting rows" and the README table are regenerated by `scripts/site.py` after the numbers are final. Risks: the memory above; a box with fewer cores than threads (the partitions
   then cost without return: use `--threads` no larger than the cores); the efficiency cores; the file that is not on the page cache (the threads' `pread`s are then the cost, as for the groups).

## 10. Reproducing

```
cancho build                                                     # build/table, with psort.cho
python3 scripts/adversarial.py --cells A1,A2,A3,A4 --threads 16   # the cells of section 1 (generates build/adv/f2.csv)
cancho build scripts/spikes/sort/sortpar.cho --std -o sortpar     # the spike
python3 scripts/spikes/sort/sort_check.py ./sortpar               # against Python's stable sort, 15,840 runs
python3 scripts/spikes/sort/bench_sort.py --table build/table --sortpar ./sortpar --file build/adv/f2.csv \
    --cells A1,A2,A4,A3,B1 --threads 1,2,4,8,12,16 --tthreads 2,4,8,12,16 --sorts r            # section 6
./sortpar FILE 16 t r 4:i:a                                       # phase times; KEYS COL:KIND:DIR[,...], then TOP and p (re-read) after SORT
cd tests/conformance && python3 -W ignore -m unittest test_psort # the exactness tests
python3 scripts/psort_mutants.py                                  # the mutants (--check: no build)
```

The spike's options are in its header. The stage numbers of 7.1 come from a scratch build that stops after a stage (`--max-bytes 7`, `9`, `11` in a copy; not kept); the phase split of section 2 from `--from 999999998` and `999999999` in another.
