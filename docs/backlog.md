# Backlog and ideas

What `table` does not do yet, in rough order of how soon it would be taken, and the reasoning that should survive
between sessions. Nothing here is promised; each item says what would have to be true to build it. Measured claims
are marked with where they were measured; everything else is a judgement.

## Where we stand against DuckDB (measured once, on one machine)

DuckDB 1.5.6 (Homebrew), Apple-silicon Mac with 16 cores at load about 2, the 1,000,000-row 31.7 MB generated file
(`scripts/bench.py`'s generator), minimum of 5 interleaved runs, CSV output to `/dev/null`, `read_csv` with type
detection. Start-up of `duckdb -c "select 1"` is 0.013 s, so it does not explain the gaps.

| question | `table` (1 thread) | `duckdb` 1 thread | `duckdb` default (16 threads) |
|---|---:|---:|---:|
| filter `status=404 and bytes>50000` | **0.048 s** | 0.166 s | 0.070 s |
| group-count by status | 0.105 s | 0.122 s | **0.059 s** |
| group sum of bytes by status | 0.115 s | 0.131 s | **0.061 s** |
| `cut` status,bytes as CSV | **0.054 s** | 0.178 s | 0.074 s |

* That first table compared only the group-count answers. The later one below checks every contender's answer
  against one computed in Python before timing.
* DuckDB infers column types and `table` does not, so it does more work. One file shape, one machine, no Linux
  run (a soak was running there).
* So per core `table` is level with or ahead of DuckDB on these CSV questions, and DuckDB's threads win the
  groupings by about 1.8x. Neither says anything about the rest of what DuckDB is.
* lex-sys's own scan primitives were measured level with DuckDB's single thread on 50 million `int64`
  (lex-sys `docs/parallelism.md` §3.3).

**What DuckDB has that `table` is far from:** SQL and a planner, joins (hash and merge), window functions,
spill-to-disk for large sorts and aggregations, vectorised execution, typed columns (decimal, date, timestamp,
nested), CSV type inference, Parquet and JSON readers, a storage format with transactions, extensions.

**Why that is not the target.** `table` is bounded, deterministic and refuses instead of guessing; every limit has a
rule and a repair. An engine that is those things and still SQL-complete is a different, much larger project. The use
of DuckDB here is as a yardstick for the per-core speed of the questions `table` does answer, and as a source of
ideas (below).

**With `--threads` (`docs/parallel.md`, one machine: the same Mac, minimum of 5 interleaved, every answer checked
first), seconds:**

| question | `table` 1 / 4 / 16 threads | `duckdb` 1 / 4 / default |
|---|---|---|
| filter | 0.051 / 0.020 / **0.012** | 0.176 / 0.074 / 0.075 |
| `cut` | 0.058 / 0.021 / **0.011** | 0.187 / 0.079 / 0.078 |
| group-count | 0.110 / 0.033 / **0.015** | 0.129 / 0.062 / 0.062 |
| group sum | 0.121 / 0.037 / **0.018** | 0.135 / 0.064 / 0.063 |

So with threads `table` is ahead of DuckDB's default on all four, **on this file and these questions only**
(low-cardinality group key, 5 columns, 31 MB). DuckDB stops improving at 4 threads here; it does type inference,
planning and everything in the list below, and `table` does none of it. The adversarial round
(`docs/adversarial.md`) is where this claim is tested against shapes that are not kind to `table`.

**After the cell-cost round** (`docs/history.md`; the same Mac, `scripts/bench_parallel.py`, the same file and questions,
minimum of 5 interleaved, answers checked first), seconds:

| question | `table` 1 / 4 / 16 threads | `duckdb` 1 / default |
|---|---|---|
| filter | 0.046 / 0.017 / **0.0087** | 0.170 / (not taken) |
| `cut` | 0.050 / 0.020 / **0.011** | 0.190 / (not taken) |
| group-count | 0.057 / 0.019 / **0.0099** | 0.132 / 0.065 |
| group sum | 0.062 / 0.021 / **0.011** | 0.141 / 0.067 |

Group-count and group-sum were level with DuckDB per core (0.105 against 0.122, 0.115 against 0.131); they are now
2.3 times faster at one thread. On the shapes that are not kind to `table` the picture is in `docs/adversarial.md`:
the groupings with many keys are still 3x behind DuckDB at one thread.

## From the adversarial round (`docs/adversarial.md`), with the cause of each

Each is a measured loss or a missing thing; the cause is from a profile where one was possible (`sample`, Mac) and
otherwise said to be inferred.

* ~~**A row sort (text and integer).**~~ (done: `--order-by`, `docs/sort.md`. 1M rows by a text key 0.47 s on the Mac against csvtk's
  0.81, DuckDB's 0.48 at one thread and 0.13 at its default; by an integer 0.44 against 1.8, 0.41 and 0.12; the first 1000
  in 0.064 s and 2 MB.)
* **A third case in the read's loop costs the other two 15 percent** (found while building the row sort; `docs/sort.md`): a
  branch that is never taken, at the place where a counted row is written or grouped, made `--select` and `--where` that much
  slower, and the same branch elsewhere did nothing. Until it is understood (a look at the code the compiler writes for the
  loop would settle whether it is the specialisation of the loop on the mode), a new thing to do with a row goes in a
  callee behind a case the loop already has, as the sort does. The cost of the by-value call for the groups is the other
  half of the same fact: the in-place paths (`group_plain`, `group_fast`) exist because moving the 40-word `Groups` per row
  cost a quarter of a group-count.
* **The row sort is sequential.** `--threads` with `--order-by` runs the sequential read and gives the same bytes. DuckDB's
  default is 3.4x to 3.9x ahead of it on 1M rows (the shell's `sort` on Linux uses its cores too). The design that fits:
  each range keeps its rows sorted (the bounded top-N already does that for a page), the parent merges the runs in file
  order (equal keys: the earlier range first, which is the stable order), the way the groups are merged now. It needs a
  serialised form of a held row, which the groups' blob has the pieces of. Worth it only with the hash-partitioned merge
  of the groupings, which needs the same plumbing.
* **A sort by two keys whose first has few values is twice a sort by one** (`--order-by s,v:int`: 0.87 s, against 0.44):
  most comparisons tie on the first key's prefix words and take the full comparison, through `vec.get` and `key_bytes`.
  A prefix of the *pair* (the second key's integer, when the first has settled to ties) would make them integer compares.
* **Writing the rows out in sorted order reads the file's records at random**: the same file already in order sorts in 0.20 s,
  the random one in 0.44 to 0.50. An external-memory layout (the keys and a pointer, the records read again in order) is
  no better; storing the records in sorted blocks is what a column store does. Not tried.
* **No external sort**: past `--max-sort-rows` or `--max-state-bytes` the answer is the refusal (`limit.too-many-sort-rows`,
  `limit.state-too-large`), by design (`docs/sort.md`). A merge of sorted spill files would lift it; it is not worth
  building before someone has a file that does not fit.
* **The end-phase sort of a million groups: `compare_keys` is still two thirds of B3's profile** after the prefix
  sort of round 4, because keys share their first bytes. Next steps in order of cost: an MSD radix or a longer prefix
  (skip the shared leading bytes of the whole key set, which is one pass); store the key hash-map in insertion order
  and sort only the output when `--sort`/the key order is wanted (it is always wanted today, as bytewise order is the
  documented output order, so this is a change of contract, not an optimisation).
* **High cardinality does not parallelise: B3/B4/I1 at `--threads 8` are 0 to 14 percent slower than at 1** and use
  8 times the state (327 MB against 183). Cause (read from the code and the 8-thread RSS; not profiled): each thread
  builds its own map of up to a million keys and the parent merges them one by one. Options: a hash-partitioned
  merge (threads own key ranges); or a pre-sample that declines threads when the first wave's groups are more than
  a fraction of its rows. Until then `--threads` should not be used for a high-cardinality grouping.
* ~~**`csv_value` does four `memchr`s per cell**~~ (done, `docs/history.md` "Cell cost": the per-row and per-cell work
  was profiled and cut in rounds: group-count 110 to 52 ns a row, select 56 to 46, filter 50 to 43; the 1 GB
  group-count on the Mac is now 1.23x *faster* than DuckDB's default at 8 threads, where it was 1.59x slower.)
* **The field search is the largest item left, 40 to 48% of every scan** (`reader.fields`, after the cell-cost round):
  a byte loop at about 0.5 ns a byte that LLVM does not vectorise, three integers stored per cell. Ideas, none tried:
  stop at the last column the plan needs and count the delimiters of the rest with `count_byte` when the rest has
  no quote (one `memchr` for the quote decides); skip storing the cells nobody reads; a SWAR word-at-a-time delimiter
  test needs a way to load eight bytes as an int, which the language has no way to say yet. Unrolling by hand was
  tried and was slower (round 11).
* **A new group costs what it cost**: the groups are moved by value to be added (the map grows by value), and the key is
  hashed twice (`find`, then `put`). A 1M-key grouping is 3x DuckDB at one thread and the cell-cost round did not touch
  it (B3 1.0x of `main`). A `std.map` with an `insert` that reports whether the key was new, working through `&!`, would
  take both; that is lex-sys's, not this tool's.
* **Long fields (1-10 KB) do not scale with threads, and the cause is now known (E1/E2).** With no newline inside the
  quoted fields the same file scales 2x at eight threads (0.017 to 0.0086 s); with newlines every 17 bytes it does not
  (0.172 to 0.170 s), because the speculation "this range starts outside quotes" is wrong about as often as a boundary
  is inside a quoted field, and a wrong guess is a re-read by the parent; and each embedded newline is a line the
  reader handles (six million of them in that file). To do: let a worker that finds its guess wrong say where the first
  record start after an *even* count of quotes is, which needs the range's whole quote parity (a pass of `count_byte`).
  Real files with 1-10 KB fields and few newlines already scale. The single-thread time ties csvtk and Miller.
* **100k-key grouping is 1.09-1.11x DuckDB's default at 8 threads (B1/B2), and wins at one thread (1.37-1.39x faster)**
  after the in-place add and the cache of hot groups (it was 1.07-1.17x slower).
* ~~**`select_mutants.py` and `filter_mutants.py` are stale**~~ (done, `docs/history.md` "The mutation scripts"): 24 sites
  re-pointed, a mutant that cannot apply now fails the script, and CI checks that every mutant still applies.
* **Peak memory of grouping is N threads x groups.** Bounded by `--max-state-bytes` per state, so the true bound is
  N times it; the documentation says the per-state bound, and should say this.

## Ideas, roughly in order

1. ~~**Parallel scan**~~ (done: `--threads N`, byte-identical to the sequential engine, `docs/parallel.md`).
2. **JSON lines** as a second reader behind a record-reader interface (design: lexsys-tools `docs/next-tools.md`
   §5, "Formats"): declared flat projection with dotted paths; a non-scalar in a cell is a tagged refusal; one big
   JSON array is refused with a pointer to `jsonq`.
3. **`--query`**, the string front end to the same `Query` plan (the typed-flags form must equal it byte for byte:
   the gate is written in the design, not yet in code).
4. **`mean` and `describe`** with the design's exact fixed-point rule (half to even, integers only), per-column
   count/empty/distinct/min/max.
5. **Decimals** declared per column, read as exact scaled integers, never floats.
6. **A second sort key**, and `or` in `--where` (the grammar is the thing to keep unambiguous).
7. **Type inference** as a *report* (`describe`), never as a silent coercion.
8. **stdin** as an input.
9. **Joins**, a hash join with the build side bounded by `--max-state-bytes` and a refusal past it. This is the first
   feature where DuckDB's shape is the model; it needs its own design, the memory bound is the whole difficulty.
10. **Parquet, a read-only subset**: plain, dictionary and run-length encodings, uncompressed and snappy only; every
    other encoding or codec a tagged refusal. Not before a user has a file they need: it needs a Thrift decoder, none
    of snappy/zstd/gzip is in lex-sys's `std`, and its gain over CSV (column pruning) implies a column-wise engine.
11. **Cheap paging**: `--from N` re-reads the file from the start, so paging a huge file is quadratic. An index file
    or a seekable cursor would fix it and is out of scope until someone pages a large file.

## Friction in the contract package (lexsys-tools), from building this

Found while building. **Closed and adopted:** `extra_rules` (lexsys-tools#28); `fail.choose_*` and `fail.detail_*`
(#29: the hand-built `choose` repair of `plan.ls` and the key/value ladders of every error are one call each,
48 lines fewer, the conformance suite unchanged). Still open:

* `describe.Tool` allows one `schema` string, so a tool with several documents shares one `oneOf`.
* A flag value cannot be empty, and a flag table cannot hold `;` or `|` (not even in help text).
* No `Buffer` truncate/undo (a lex-sys `std` gap), so bounded JSON paging builds each row in a scratch buffer.
* A large `res` struct is copied when passed to a per-row call, and `buffer.append` consumes and returns, which pushes
  tools toward that pattern (it cost one measured regression, `docs/history.md`).
* `toolbox.sort` (#29) was not adopted in `agg.ls`: its comparator is a captureless function over one of three
  concrete contexts (a `[int]`, or a `Map[int]`), and the groups' order needs the map **and** the accumulator
  array **and** the stride, the number of key fields, the column sorted by and its direction. Copying all of that
  into one context per mode would be more code than the merge sort it replaces.
* Atomics and channels do not exist in lex-sys yet (design: lex-sys `docs/atomics.md`); until they do, a work queue
  between threads is not available and workers are shared-nothing, merged in order.
