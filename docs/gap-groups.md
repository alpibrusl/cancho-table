# High-cardinality grouping: the gap, the cheaper sequential path, and a partitioned parallel read

Branch `gap-groups-design` (a spike on `origin/main` 7d04b9f, not merged). The gap is the one `docs/adversarial.md` registered and
`docs/backlog.md` left open: a grouping with many distinct keys (cells B1 to B4, I1) is 3x behind DuckDB on one core, `--threads` does
not help it, and on 16 cores it is 8 to 11x behind. This file says where the time went, what was changed, what the parallel design is and
why it returns the sequential answer, what each part measured, and what is still not done. Everything below was run on this branch; the
conditions are next to the numbers and the raw outputs are in `scripts/spikes/groups/results/`.

**Summary.** With the sequential changes alone B3/B4 go from 0.79/1.40 s to 0.26/0.30 s on one core (DuckDB: 0.23/0.25 s unsorted,
0.35/0.38 s ordered like `table`'s output). With the partitioned read, 16 threads give 0.039/0.042 s, where the old threaded read gave
0.87/1.52 s and DuckDB's default 0.08/0.09 s (0.11 s ordered). Output is byte-identical to the one-thread answer of the base binary in the
134-plan corpus, in ~3,000 random plans at 4 thread counts and 4 chunk sizes each, and in the conformance suite (all tests but the 5 that check that the old mutation scripts still match the changed source); 24 of 25 mutants of the new
code are killed (the 25th is a guard that cannot be reached).

## 1. The gap, reproduced

`python3 scripts/adversarial.py --cells B1,B2,B3,B4,I1 --threads 16` ran first (Mac, Apple silicon, 16 cores, with a load of 34 from other
work: B3 1.07 s, B4 1.50 s at one thread, `--threads 16` no faster). The numbers below are a quieter re-run (load 1.6 to 2.2, minimum of 9,
interleaved, `scripts/spikes/groups/cells.py`, `f2.csv` of the adversarial round, seconds), the base being `origin/main` built with the pinned
compiler. DuckDB 1.5.6 is run twice, as `docs/adversarial.md` does it but also with the `ORDER BY` that `table`'s output has by contract
(its default `GROUP BY` output is in no order, so the unsorted figure answers a cheaper question).

| cell | base -t1 | base -t16 | DuckDB -t1 unsorted / ordered | DuckDB default unsorted / ordered | csvtk -j1 |
|---|---|---|---|---|---|
| B1 count, 100k keys | 0.121 | 0.072 | 0.164 / 0.172 | 0.076 / 0.085 | 0.302 |
| B2 sum, 100k keys | 0.164 | 0.115 | 0.175 / 0.183 | 0.078 / 0.088 | 4.00 (16 GB) |
| B3 count, 1M keys | **0.810** | **0.875** | 0.226 / 0.348 | 0.081 / 0.108 | 0.911 |
| B4 sum, 1M keys | **1.476** | **1.518** | 0.254 / 0.380 | 0.087 / 0.114 | 48.8 (17.8 GB) |
| I1 distinct, 4 groups | 0.271 | 0.175 | 0.201 / 0.202 | 0.083 / 0.084 | |

So: B3/B4 are 3.6/5.8x DuckDB at one thread (unsorted), 10.8/17x its default; `--threads 16` is 3 to 8 percent *slower* than one
thread and uses 336 MB against 179; B1 level with DuckDB's unsorted default and B2 1.5x behind it (at 16 threads); I1 1.35x at one thread, 2.1x at 16. B4 is 0.67 s worse than B3 for the same keys,
which the registered causes do not explain (below).

### Where the time goes (B3, one thread, `sample` at 1 ms, three runs)

| share of samples | what |
|---|---|
| 56 to 60% | the end-phase sort: `compare_keys`, `sort_into`, `before` (the merge sort of 1M keys; the 7-byte prefix of round 4 settles little because `key0000001`... share 6 bytes) |
| 21 to 23% | writing the rows: `csv_value`, `must_quote`, `memmove`, one `buffer.push` per delimiter and per digit |
| ~14% | the table: `add` (moves the 40-word `Groups` by value for a new group), `find_slot`, `put`, `rebuild`, `vec.reserve` |
| ~4% | the scan |

The registered "each new group hashes its key twice" is three hashes: `add` calls `find`, `map.put` calls `find` again and then hashes to
place. And B4's extra 0.67 s is not the sum but its text: a sum's row goes through `put_value` and `dec.put_sum` (arbitrary width, a scale) and a `buffer.push` per delimiter and per digit: about 0.6
microseconds a group. Removing the fast row writer from the final build puts B4 back to 0.935 s (section 2).

## 2. The sequential changes (b), each measured

Mac, one thread, minimum of 9, seconds (`results/mac_sequential_ablation.txt`; every build's output md5 equals the base's on every cell).
Left to right the changes are added; the last three columns are the final build with one change taken out.

| cell | base | + radix order | + one-hash table | + row writer | + in-place new group (final) | final minus radix | final minus rows | final minus in-place |
|---|---|---|---|---|---|---|---|---|
| B1 | 0.118 | 0.110 | 0.094 | 0.092 | 0.087 | 0.100 | 0.089 | 0.094 |
| B2 | 0.161 | 0.148 | 0.137 | 0.099 | 0.094 | 0.105 | 0.130 | 0.100 |
| B3 | 0.791 | 0.418 | 0.347 | 0.316 | 0.251 | 0.607 | 0.297 | 0.318 |
| B4 | 1.402 | 1.074 | 0.954 | 0.314 | 0.271 | 0.640 | 0.935 | 0.358 |
| I1 | 0.226 | 0.222 | 0.154 | 0.154 | 0.153 | 0.154 | 0.154 | 0.157 |

1. **Radix order of the keys** (`agg.sort_keys`). A key is cut into chunks of six bytes of its current field; a chunk is one integer whose
   order is the bytes' order, with a low byte that says whether the field ends in it (0 to 5), ends exactly at its end (6) or goes on (7), so
   a shorter field sorts first and equal chunks that go on are exactly the groups that share everything so far. The groups are sorted by
   chunk with a least-significant-digit radix sort over the seven bytes (a byte every group shares is skipped, which is what makes
   `key0000001`... cost two passes, not seven); each run of equal chunks is sorted again at the next chunk, a key that ends in the chunk
   going on at the next field. It orders exactly as `compare_keys` (bytewise per field, a shorter value first), which `--sort` by an
   aggregate and the typed keys still use through `sort_into`. B3 loses 0.37 s (47%); taken out of the final build it costs 0.34 s. Storing the first chunk in the table when
   the key is put, so that the first pass does not gather the keys, was built and measured: no gain (the runs of equal first chunks are
   large for this file), removed.
2. **One hash, tags in the slots** (`gmap.cho`, replacing `std.map` in the groups). The hash is taken once by the caller and handed to `find_h` and
   `put_new`; it reads eight bytes at a time (one multiply each, a murmur finalizer) instead of FNV per byte; a slot holds a 31-bit tag of
   the hash beside the entry number, so a probe that does not match is refused without reading the entry; an entry is three integers (the
   map's four, the values array and the live flags gone), so 1M groups hold ~20 MB less (peak resident 192 to 172 MB for B3). B3 -0.07 s, I1 -0.07 s (the pair set of `distinct`
   is the same table), B2 -0.01.
3. **A row writer** (`agg.put_row_csv`). For a plan whose aggregates are all integers (count, distinct count, integer sum, min, max) and a
   key with nothing csv would quote, the row is written into the output buffer's room in one pass, with no `buffer.push` per byte. B3 -0.03
   (count), **B4 -0.64** (the sum no longer goes through `dec.put_sum`), B2 -0.04. Rows that do not qualify take the old route
   (`csv_value`, `put_value`) per row, so quoting is not touched.
4. **A new group is put in place** (`gmap.put_inplace`, `add_fast`). `add_fast` already built the key and looked it up; on a miss it used to
   return, and `add` moved the whole `Groups` by value and hashed again. Now, when the table, the key bytes and the accumulator vector have room
   and the bounds (left in the memo by `add`) allow it, the group is put where it is, with the same refusals as `add`; when anything is full
   the row takes `add`, which grows the storage by value as before. (Fields of a unique reference can be assigned, a `res` field cannot be
   replaced; `vec.used`, `Tab.entries` and `Buffer.used` are integers.) B3 -0.065, B4 -0.04.

Together: B3 0.79 to 0.26 (3.0x), B4 1.40 to 0.30 (4.7x), B2 1.7x, B1 1.3x, I1 1.4x; B3 and B4 are now **1.2 to 1.3x DuckDB's unsorted time
at one thread and 0.8x its ordered time**; B1, B2, I1 are ahead of both. The profile that is left (B3, 250 samples): `put_row_csv` 44% (the
reads: in key order the three arrays, meta, key bytes and accumulators, are three cache misses a group), `word_at` 15% (the second chunk of
the sort, the same gather), `find_h` and `add` the rest. A record that holds hash, accumulators and key together would cut those; not built.

## 3. The parallel read (a): groups cut at sampled splitters, merged and written by threads

### 3.1 Why not "hash partitions and a k-way merge"

The design asked for was: each worker partitions by hash of the key, partitions are merged in parallel, sorted, and the sorted partitions
merged k ways. The first three steps are in the design below (a worker cuts its *groups*, not its rows, after it has counted its range: a
row-level partition would send every cell of the file through the partitions; the groups of a range are ~22 bytes a distinct key). The last is
the problem, because the groups of a hash partition are not a range of the key order, so the output needs a merge, and in the parent. Measured
(`scripts/spikes/groups/kway.cho`, 1M keys of 10 bytes in 16 sorted runs, appending each key and a newline to an output buffer, one thread):
**0.05 s for a binary heap of the heads, 0.11 s for taking the least of 16 heads**, which is more than the whole of B3 at 16 threads
now. A merge in parallel needs splitters anyway (sample the sorted runs, cut each run, merge slice j of every run in thread j: one more round and one
more copy of all the data). So the cut is made by key range from the start: **each range's groups are cut at P - 1 splitter keys into P
blobs**; blob k holds the keys from splitter k - 1 up to splitter k, whichever range they came from, so thread k merges blob k of every range, sorts and
writes its rows, and the rows of thread 0, 1, ... end to end are the answer. There is no merge and no second sort. What it costs is the
need for splitters before the ranges are read, and a risk of skew; both below.

### 3.2 The flow (`par.run_groups`, `agg.serialize_parts`, `par.work2`)

```
file ──16 windows of 16 KiB──> sample groups ──sorted, quantiles──> P-1 splitters                      (parent; below what the timings can see)
wave of ranges ─ thread w: scan range, local groups, cut at the splitters ─> table w = P blobs         (T threads, as `run`)
parent: takes the tables in file order (a wrong guess at a boundary: re-read here, into groups of its own)
round (the last wave, or the kept tables passed 32 MiB or 64 tables):
        thread k: input = blob k of every kept table and of this wave's  (copied by the parent, 1 copy)
                  merge them (`agg.merge`, the old function) into its own groups, with the bounds of the read
                  last round: settle the sums, sort the keys (radix), write the rows (or the first `want` groups in the order asked for)
                  other rounds: write its groups as one blob
parent: appends the rows of thread 0, 1, ..., P-1 to the output (a page, a sort or a top: merges the candidates, below)
```

P is `--threads`. Between waves nothing is merged while the kept tables are few and small (this is what makes a file of two or three waves
cost one round): they wait in a buffer. The splitters come from the distinct keys of the sample (not its rows), so a heavy hitter does not skew
them. An empty or filtered sample gives no splitter and everything lands in blob 0: correct, and as slow as the sequential merge.

**Pages, sorts, tops, json.** When the answer is not "all rows as csv in key order" (`--format json`, whose default is a page of 1000; `--limit`,
`--from`, `--top`; `--sort` by an aggregate), each thread of the last round sorts its groups in the order asked for (`sort_into` as today,
the radix for the default) and returns its first `want` (`want` = `--top`, or `--from + --limit + 1`, at most 100,000), the parent merges the
candidates of all threads into a fresh set of groups and the existing `settle_groups` and `finish_groups` finish that. The first `want` of the union are among the
first `want` of the parts, because every order here ends in the key, which is unique. `group_count` is the real total (`k_total`). Over 100,000
the read is declined (a full output in another order than key order needs the k-way merge, above).

### 3.3 Why the answer is the sequential answer

* **Values.** A group's value is a function of the multiset of its rows: counts add; a sum is a pair `(hi, lo)` added in any order and carried
  once at the end (`docs/numbers.md` N0p), so there is no overflow to order; min and max are commutative; `distinct` is a union of
  `(group, value)` pairs; a float sum or mean is the exact superaccumulator, added limb by limb (`agg.merge` normalises) and rounded once in
  `settle`, whose result is the same for every order (`docs/numbers.md`). So blob k of range 1 and blob k of range 2 add to what one thread
  reading both would have. A key is in exactly one blob (the cut is a function of the key and the splitters), and so is every pair of a
  `distinct`, renumbered to its group's place in the blob (`serialize_parts`); **the pair set is per partition**, 1/P of the one set, with no merge of sets
  between threads.
* **Order.** Blob k holds the keys in `[s(k-1), s(k))` in `compare_keys` order; inside a blob `sort_keys` gives that order (differential
  tests against `sort_into` on every plan below); unique keys make the order total. So the concatenation is the global order **whatever the
  splitters, P, the ranges or the waves are**: a different thread count changes P and the sample, never the bytes. The same holds for
  candidates, whose order ends in the key.
* **Bounds and refusals, "the first in file order".** The sequential read refuses at the first row after which groups, pairs or bytes held pass their bound, or at a bad
  cell, or stops at `--max-rows`. All three counts only grow, so *if the totals of the whole read pass no bound, no prefix did*. The partitioned read
  therefore does not decide any refusal: **it declines** (nothing written, `a`, the buffers and the groups as they were, the old read from the start of the data) when
  (i) a worker's range stopped with its guess right (a refusal, a bound, `--max-rows`, any abort), (ii) the parent's own re-read of a range stops, (iii) a merge thread
  passes a bound alone (a part of the groups past a bound means the whole is), (iv) the merged totals of groups, pairs or held bytes pass a bound (the same
  accounting as `add`: key bytes, 584 bytes a float aggregate, pair bytes), (v) a float sum is beyond a double (the old read names the smallest key), (vi) anything does not fit its
  slot. The old read is exact by construction (`docs/parallel.md`), so the refusal that comes out is the first in file order, with its row, line and
  column. Declining costs the wasted work (a refusal costs ~8 ms more than before: the sample and one wave; a refusal that every worker would
  have found in its first records now stops them at once, see (i)) and the old read's own time.
* **Boundaries.** A range whose first line is not where the last record ended is read again by the parent with the sequential reader into groups of
  its own (so a quoted newline across a boundary costs one range, as before), which must itself end clean. The tally (records, ragged, the
  first ragged row and line) is applied in file order with the old code.
* **Nothing is written before it is known.** The first byte of rows is written after every merge thread has finished and the totals were checked;
  a failing write sets the same abort (9) as the sequential flush.
* **Evidence**, section 6.

### 3.4 Memory: the peak, stated

A range's worker holds its local groups (the table of ~170 bytes a group, as in the sequential read, for the groups of its range) and its
range's buffers; the parent holds the slots (one per worker; on Linux a zero-filled box is `calloc` and the pages a worker does not write are not resident
(`docs/zeroed-slices.md` of the compiler); the Mac figures show them resident), the kept tables, and for a round one
copy of each thread's input, the thread's merged groups (hint-sized, so no regrowth), its output (a blob or the rows), the copy of the output in the
parent, and, last, the rows in the output buffer, flushed per thread. Counting in bytes per distinct key of 10 bytes with a sum (the blob record is 4 + key + 8 per integer):

* **unique-ish keys** (each key in about one range, B3, uuid): peak ~1.9x the sequential read's (B3: 317 MB at 16 threads against 170; Linux 6 threads 239 against 117);
* **keys repeated in every range** (B1, 100k keys): the workers hold 16 local copies of the table, and the old read held them too: 183 MB against 171 for the old read at 16 threads (21 MB sequential);
* **a file of several waves** keeps the kept tables (≤ 32 MiB) and then a round's state and copies: the 98 MB, 4M-row, 1M-key file: 785 MB at 16 threads against 571 for the old read
  and 170 sequential (the allocator keeps what the earlier phase freed; reusing one table per thread across rounds, which needs threads that live longer than a wave
  or a way to give a thread a `Groups`, would take this to ~2x);
* **few groups** (a 1 GB file, 6 groups): 136 MB against 71 on the Mac, the slots being twice the chunk (a blob can be larger than the rows it came from).

The bound the documentation should state: **about (2T + 3) x `--max-state-bytes`** at worst, T being the threads: each worker's groups are bounded by the
bound (as in the old read), each merge thread's part is bounded by it too (it declines past it) and a read that is not declined has a merged state within it, the
copies add the blobs (smaller than the tables), and the allocator may keep the workers' pages while the round runs (the second T). The old read's bound was T x
the bound for the workers plus the whole merged state, and the documentation says only the per-state figure. A float aggregate makes a group 584 bytes more in every copy; see 3.6.

**`--max-state-bytes` and the other bounds: what a user observes.** What the flags *refuse* is unchanged: the accounting is the old one (key bytes, 584 bytes a float
aggregate, pair bytes; `agg.merge`'s and `add`'s are the same sums), a read that passes a bound is refused with the same rule, row, line and column, because the old
read raises it (declines, 3.3), and the sequential read is untouched in what it holds. What changes is the memory a threaded grouping may use on the way: the bound is
still a bound on one set of groups, and the memory of a threaded read is up to **(2T + 3) x `--max-state-bytes`** (T threads) where the old read's was T x the bound for
the workers plus the merged set (documented as "the per-state figure" only). Measured peaks are far below that (3.4); the figure is what the bound and the design allow,
not what a file did. A read that is declined has done the parallel work first, so a refusal costs ~8 ms (Mac) more than before and the old read's own time.

### 3.5 What it measured

Mac, 16 cores, minimum of 9, seconds (`results/mac_threads_vs_duckdb.txt`, `mac_scaling.txt`). `base` is `origin/main`, `final` this branch; the md5 of each
`final` output was equal to the base's at every thread count.

| cell | base -t1 | final -t1 | base -t16 | final -t16 | DuckDB default unsorted / ordered | final -t16 over DuckDB | peak RSS MB final -t16 (-t1) |
|---|---|---|---|---|---|---|---|
| B1 | 0.121 | 0.089 | 0.072 | **0.024** | 0.076 / 0.085 | 0.32x / 0.29x | 183 (21) |
| B2 | 0.164 | 0.099 | 0.115 | **0.027** | 0.078 / 0.088 | 0.34x / 0.31x | 184 (20) |
| B3 | 0.810 | 0.277 | 0.875 | **0.039** | 0.081 / 0.108 | 0.48x / 0.36x | 317 (170) |
| B4 | 1.476 | 0.314 | 1.518 | **0.042** | 0.087 / 0.114 | 0.48x / 0.37x | 318 (172) |
| I1 | 0.271 | 0.190 | 0.175 | **0.039** | 0.083 / 0.084 | 0.47x / 0.47x | 180 (109) |

Scaling of B3 (final): 1 thread 0.300, 2: 0.221, 4: 0.122, 8: 0.078, 16: 0.037 s (the old read: flat or worse). It is not linear because the
parent still copies the data (the blobs, the rows) and starts threads; at 16 threads the CPU time is 0.30 s against 0.30 for one thread, so there is little
inflation. Other shapes of file (`results/mac_other_shapes.txt`, 1M rows each): unique 32-hex keys 1.01 to 0.31 s at one thread and 1.06 to 0.050 at 16; unique
shuffled keys with a 9-byte common prefix 1.34 to 0.30 and 1.40 to 0.045; a file already in key order 0.89 to 0.15 and 0.91 to 0.039; a Zipf file of 200k keys 0.135 to 0.074 and 0.084 to
0.017; two group columns (~900k pairs) 0.51 to 0.19 and 0.53 to 0.036. DuckDB's ordered default is 0.09 to 0.12 s on the same files. A single group with a million
distinct values (`--agg distinct:id`): 0.235 to 0.161 s at one thread, 0.167 to 0.085 at 16 against DuckDB 0.075: that one merge is in a single thread (3.6).

Linux, i7-1260P, `taskset -c 0-6` (three physical cores, six threads used), other work running on the box so the minimum of 9 over a quiet minute is given
(`results/gram_t1_t6.txt`):

| cell | base -t1 | final -t1 | base -t6 | final -t6 | DuckDB -t1 unsorted / ordered | DuckDB -t6 unsorted / ordered | csvtk | Miller |
|---|---|---|---|---|---|---|---|---|
| B1 | 0.160 | 0.128 | 0.153 | 0.099 | 0.154 / 0.155 | 0.090 / 0.102 | 0.442 | 0.460 |
| B2 | 0.245 | 0.178 | 0.221 | 0.129 | 0.211 / 0.216 | 0.120 / 0.137 | | 1.534 |
| B3 | 1.178 | 0.422 | 1.277 | 0.223 | 0.292 / 0.527 | 0.122 / 0.209 | 1.284 | 1.685 |
| B4 | 1.420 | 0.502 | 1.566 | 0.267 | 0.342 / 0.615 | 0.146 / 0.264 | | 3.250 |
| I1 | 0.387 | 0.302 | 0.403 | 0.169 | 0.276 / 0.264 | 0.124 / 0.132 | | 2.622 |

On the three cores that is 1.8x DuckDB's time (unsorted) and **1.07x its ordered time** for B3, 1.0x ordered for B4, and 1.1 / 0.97x for B1, 1.3x for I1; at one thread
0.8x DuckDB ordered. The scaling is 1.9x at 6 threads (0.402 -> 0.204 s alone on the box), 2.4x at 12 on the seven logical CPUs. Minor faults are 60k at one thread and 110k
at six: the copies, not the work, are what the extra threads cost here (sys 0.16 s of 0.74 s CPU).

### 3.6 What it does not do (yet)

* **Float aggregates with many groups.** A group with a float sum or mean carries 73 limbs, 584 bytes (and 2 KB resident in the sequential read: 1M such groups are 2.1 GB, 2.5 s).
  A range whose answer holds more than 512 such groups does not fit its slot and the read is declined (the old read's speed). The remedy is a sparse form (the non-zero limbs and their
  indices: usually two to four of 73) for the blob, and ideally for the table; the exactness argument does not change, the limbs add the same. Correctness with floats is in
  the fuzz plans and the full-size checks.
* **One group, or a few, with a huge `distinct`.** The cut is by group key, so the pair set of one group is one thread's. I1 (4 groups) parallelises 4 ways; `--agg distinct:id`
  alone is 0.085 s at 16 threads (the scan and the workers' sets are parallel, the one merge is not). A second cut of the pairs by the hash of (group, value), counting per
  thread and adding the counts to the owners, is the design for it; not built.
* **Skew.** Splitters come from 16 places of the file. A file whose distinct keys are almost all in a part of it that the windows miss puts most groups into one blob, and the
  merge/sort/write of that blob is sequential (about the old end phase, now radix and one hash). A second pass (splitters from the workers' tables, the parent has all of them) would
  repair it; the cost is another round.
* **Full output in an order that is not key order** (`--sort -count` without `--top`/`--limit`), over 100,000 candidates: declined.
* **`--report types`, `--order-by` rows**: not touched.
* The sequential end of the read (sample, copies, thread starts) is ~10 ms on the Mac; below ~4 MB of data it does not pay (the old threshold, 1 MiB, is unchanged).

## 4. Distinct (c)

`distinct` kept one `Map` of `(group, aggregate, value bytes)` pairs and hashed each pair three times; the pair table is now `gmap` (one hash): I1 -0.07 s at one thread (0.226 to 0.154). In
the partitioned read each blob carries the pairs of its own groups, renumbered, and each merge thread holds the pair set of its groups only; the parent merges no sets. I1 at 16 threads: 0.175
(old) to 0.039 s. Single group: 3.6.

## 5. The realistic target

* **One core, Mac:** 1.2 to 1.3x DuckDB unsorted, 0.8x DuckDB ordered, for 1M keys; ahead of both for 100k keys and `distinct`. Not further without a record layout (one miss a group
  instead of three) and the second chunk of the sort stored: perhaps 0.20 s for B3, i.e. level with DuckDB unsorted.
* **16 cores, Mac:** 0.04 s for 1M keys, a third to half of DuckDB's default; the floor is the scan (0.011 s at 16 threads for a 4-group file) plus copies.
* **Six threads on three physical cores (Linux):** level with DuckDB's *ordered* time, 1.8x its unsorted; the machine, not the design, is the limit (DuckDB's own speed-up is 2.4x).
* **Not reachable by this design:** many-group float sums (needs the sparse limbs), a single huge `distinct` without the pair cut, a hostile key distribution without the second splitter pass.

## 6. Evidence that the answers are the sequential answers

* `scripts/corpus.py`: the md5, status and error of 134 plans over the benchmark and adversarial files, each at 1 thread and with 3 and 4 threads and 64 KiB ranges, are unchanged
  against the base binary at every step of this branch.
* `scripts/spikes/groups/fuzz.py`: random files (text keys with commas, quotes, newlines, empties, accents and long runs; integers at the edges of 64 bits and bad cells; ragged rows) and random
  plans (1 to 2 group columns, typed `:int` and `:dec(2)` keys, count, sum, min, max, mean, distinct, float sum, mean, min; `--where`, bounds, `--sort`, `--top`, `--limit`, `--from`, json and csv): the
  threaded answer (2, 3, 5, 16 threads x ranges of 1, 64, 1000 and 100,000 bytes) equals the base binary's one-thread answer in stdout, stderr and status, ~3,000 files, and a debugging build
  that marks the partitioned path shows it taken in a third to a half of the threaded runs (the rest are declines and runs with nothing to group). A set of hand-made cases (a bound passed by a few
  groups, sums beyond a double, keys that are prefixes of each other and that contain NUL, a group at a splitter) runs first.
* `bigcheck`-style full-size comparisons on `f2.csv`: ten plans (distinct, floats, mean, typed keys, `--where`, `--sort -count --top 5`, pages) at 1, 2, 6, 16 and 64 threads, chunks from 100 KB to 4 MiB.
* The conformance suite (`python3 -m unittest discover -s tests/conformance`, run from that directory): 351 of 356 pass, 2 are skipped, **5 fail, all `test_every_script_applies`**: the mutation scripts of
  the base (`filter`, `parallel`, `cellcost`, `float_sum`, `typed_keys`) name 12 pieces of text that this branch changed (`map.put`, the unique spelling of `if agg.get_i64(sl, at) == 0 && first == cur`, and
  indentation after `cancho fmt`). They have to be re-pointed in the change that merges this; no functional test fails. `test_memory` caught a first version (a float slot sized to the state bound was resident
  on the Mac) and passes.
* `scripts/spikes/groups/partition_mutants.py`: 25 mutants of the new code (bounds not checked on the totals, a wrong-guess range taken, tables not merged, blobs cut the wrong way, pairs not renumbered,
  keys not sorted, ties in a chunk not refined, a field of exactly six bytes sorting with a longer one, a place made in place past a bound, a key that needs quotes written plain, ...): 24 killed (one
  by not building), 1 survives: removing the guard "a wave that read nothing is declined", which cannot be reached (a wave always reads the record at its start, `docs/parallel.md`). Two survived
  until the hand-made cases were added (a bound passed inside the room of the first table).

## 7. What a change that merges this needs

1. The sequential parts first, as their own change (outputs identical; mutants re-pointed; the documents that describe the end phase and the cost of a new group updated): `agg.sort_keys`, `gmap`, `put_row_csv`, the in-place group.
2. The partitioned read as a second change, with the documentation sentence about memory (3.4) and `docs/parallel.md` section 3 ("groups") rewritten: the old per-range merge into the parent is now only the
   fallback.
3. The sparse float blob, the pair cut and the second splitter pass if they are wanted.
4. What the language could give and would make this smaller: an insert-if-absent on a `&!` map (the groups are moved by value for every growth), a way to send a `Box` out of a thread (every result is
   copied through a slot; the merge threads' input is copied in), threads that live across rounds (the tables would not be rebuilt each round), a word load of eight bytes (the hash and the scan assemble
   them from bytes), and `box_slice` of zeros being lazy on macOS as it is on Linux.

## 8. Reproducing

```sh
git worktree add -b gap-groups-design ../gap-groups origin/main    # then this branch's commits
cancho build                                                       # build/table; the base: a build of origin/main
python3 scripts/spikes/groups/cells.py -n 9 --cells B1,B2,B3,B4,I1 --threads 1,16 --bins base=PATH,final=build/table --duck --check
python3 scripts/spikes/groups/cells.py -n 9 --cells B1,B3 --threads 1,6 --bins ... --adv DIR --taskset 0-6 --duck --duck-threads 6 --csvtk --mlr   # Linux
python3 scripts/spikes/groups/extra_files.py build/extra           # the other shapes
python3 scripts/spikes/groups/fuzz.py --bin build/table --base PATH --cases 200 --seed 1
python3 scripts/spikes/groups/partition_mutants.py --base PATH     # rebuilds build/table; rebuild after
cancho build scripts/spikes/groups/kway.cho --std -o kway && ./kway heap 1000000 16
```

`cells.py` times with `wait4` (wall, CPU, peak RSS), interleaves the contenders in a rotating order and takes the minimum; a run that fails is excluded from the minimum and counted. `prof.sh` samples with
`sample`. The Linux box is shared: numbers there are from a quiet minute and vary by a quarter between minutes; the Mac was at a load of 1.6 to 2.2.
