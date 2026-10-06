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

* Only the group-count answers were compared (they agree); the other three outputs were not diffed.
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

## Ideas, roughly in order

1. **Parallel scan** (in progress: `--threads N`, byte-identical to the sequential engine). Needs only today's
   `spawn`/`join` and `fork_heap`. The hard part is the quoted-newline boundary, not atomics. Compare against
   DuckDB's default threads, which is the figure that actually beats us today.
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

Found while building; some are closed (lexsys-tools#28 `extra_rules`, #29 `fail.choose_*`, `fail.detail_*`,
`toolbox.sort`). Still open:

* `describe.Tool` allows one `schema` string, so a tool with several documents shares one `oneOf`.
* A flag value cannot be empty, and a flag table cannot hold `;` or `|` (not even in help text).
* No `Buffer` truncate/undo (a lex-sys `std` gap), so bounded JSON paging builds each row in a scratch buffer.
* A large `res` struct is copied when passed to a per-row call, and `buffer.append` consumes and returns, which pushes
  tools toward that pattern (it cost one measured regression, `docs/history.md`).
* The sort comparator cannot be generic (`vcs publish` refuses generics at the pinned compiler) and cannot capture, so
  `agg.ls` has not moved to `toolbox.sort`.
* Atomics and channels do not exist in lex-sys yet (design: lex-sys `docs/atomics.md`); until they do, a work queue
  between threads is not available and workers are shared-nothing, merged in order.
