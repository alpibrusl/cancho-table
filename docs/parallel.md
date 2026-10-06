# `table --threads N`: a parallel scan that answers as the sequential one does

`--threads N` reads the file with N threads. **The answer is the sequential answer, byte for
byte**: the same standard output, the same standard error, the same exit status, for every plan
(rows as csv and as json, filters, groups, aggregates, sort, top, pages) and for every refusal
(the same row, line and column, the first in *file order*, not the first a thread met). The
sequential read (`--threads 1`, the default) is not touched by any of it and is the oracle.

This is built on today's threads: `spawn`/`join` are real pthreads, there are no atomics, no
channels and no shared mutable state, and a thread can be handed one pointer-width leaf (an
owned `File`, a `Heap`, or a unique reference to a struct). What that allows, what had to be
worked around, and what is missing are in section 1; the design is section 2 and 3; what it
cost and gained, measured, is section 6.

## 1. What the language gave, and what it did not

| need | what exists | verdict |
|---|---|---|
| read a byte range through a second handle | `file_pread(file, at, into)` (`pread(2)`, no cursor) and `file_size`; `open` again for a handle of its own | **enough.** No `seek` is needed, and there is none; a `File` is unique, so each thread opens its own |
| `lines` from an offset | none: `lines.fill_file` reads from the handle's cursor | not a language gap: `scan.fill_at` is a copy of it that calls `file_pread`, written against the contract's `Lines` fields (so it depends on their layout, at the pinned rev) |
| hand a thread its work and its data | `spawn(payload, body)`: a unique reference to a struct crosses (`docs/parallelism.md` §3.4), and the struct can own a `File`, a `Heap`, `Box` slices and the plan | **works**, with one function value per spawn |
| a number of threads fixed at run time | a spawn needs a function value of its own and a reference that lives until its `join`: **the number of threads is the depth of a call, not the length of a loop** | worked around by recursion: one frame makes its job, spawns it, makes the rest, joins it (`par.fan`); 64 deep at most |
| the thread's own memory | `fork_heap(&!Heap) -> Heap`; a box a forked heap made may be freed by the parent | works (T1) |
| get a result back | `join` returns one leaf; a `Box` result is refused; **a `res` field of a struct reached by `&!` cannot be replaced** (so `buffer.append(heap, w.out, ...)`, which consumes and returns a buffer, cannot be called on a field) | **the real gap.** A thread builds its output in buffers of its own, and ends by copying it, with `copy_into`, into a fixed-size byte slot the parent allocated and passed in the struct; the parent copies the slot up (a frame per thread, so in reverse) into one array of slots, in order |
| grow what the thread was handed | none (same reason) | a slot has a size fixed up front; a thread whose answer does not fit says so, and the parent reads that range itself |
| share the plan between threads | one `&!` payload per thread, so nothing else can cross with it | each thread gets its own copy of the plan (`query.duplicate`) and of the column arrays; they are small |
| tell the authority report | `spawn` is labelled `conc` | `tools.toml` allows `conc` for `table`, and says why; nothing else changes (no net, no ffi, no clock, no write) |
| wait or hand work around | no atomics, no queue | static partition, in waves (below); a thread that finishes early waits for its wave |

Nothing needed a change to the language, so nothing was worked around unsoundly. The three
costs are the slot (a copy of each thread's output), the per-thread copy of the plan, and the
absence of a queue (so no stealing): all bounded by the number of threads times the range size.

## 2. The problem: a range that starts in the middle of a record

A thread that starts at an arbitrary offset cannot know whether it is inside a quoted field.
Two ways were weighed.

**(b) A first pass that counts quote parity per range** and gives each thread its start state.
Rejected. It is a pass over every byte before any work begins (cheap with `memchr`, but not
free, and serial in the parent unless it is itself parallel, which is the same problem again),
and parity alone is *wrong* for this reader: a quote in the middle of an unquoted field is
text, not an opening quote (`a"b,"c,d"`), so parity of quotes does not give the state; a
correct version needs the grammar, i.e. the parse it is trying to avoid. Verified at each
boundary, with the sequential read as the fallback, it is (a) with extra steps.

**(a) Speculation, chosen, in its cheapest form.** Every range *assumes* that the line it
starts on begins a record (the state "outside quotes", which is nearly always true) and reads
its records on that assumption. It is the parent, which knows where the sequential read
actually is, that decides whether the assumption held: the range before it reports where its
*last record ended* (a record that starts in a range may run past its end), and the next range
is right **exactly when that is where it began**. A quoted newline just before a boundary
makes the next range wrong; the parent then reads that range itself, from where the sequential
read is, with the sequential rules. The other hypothesis ("inside quotes") is *not*
speculated, because it is the rare one and the parent's re-read costs one range, once. The
cost of being wrong is that range's time again, and a file whose every boundary is wrong runs
at about the sequential speed.

Why this is exact and not just likely: a newline either ends a record (outside quotes) or is
inside one, so the byte after a newline is a record start or it is not, and the reader's own
state machine decides which: the range before ends at a record start `X`, and `X` is the next
range's start iff the state was "outside". Equality of two offsets is the whole check.

## 3. The design

**Cutting.** After the header, which the sequential code reads, the rest is cut into ranges of
at most `--chunk-bytes` (4 MiB; smaller when the file is small, so that every thread gets one),
each beginning at a line start (`align`: the first offset at or after the nominal one whose
previous byte is a newline). A wave of up to N ranges is read at once, each by a thread with a
`File` of its own opened by the same path, and a heap forked from the parent's.

**A range** (`scan.scan_range`) is the data-record half of the sequential read, statement for
statement, over `file_pread` instead of a cursor, and calling the same functions (`engine.*`,
`reader.*`, `agg.*`) for what is done with a record. It reads the records that *start* before
its end, stops at the first record that starts at or after it, and answers where that is, how
many lines came before it, and its tally (rows, ragged, rows emitted) and output.

**The parent takes the ranges in file order**, holding the position `cur` where the sequential
read would be. A range is **taken as it is** only if all of these hold:

1. its thread finished and nothing in it would have stopped the sequential read (no refusal, no
   full page, no `--max-rows`, no overlong line or record, no output or state that did not
   fit its slot);
2. its first line is `cur` (the speculation held);
3. adding it to the answer needs no reading in order: see below.

Anything else, the parent **reads again itself** from `cur` to the end of that range, with the
sequential rules and its own tally, output and groups. So a refusal is the sequential refusal,
because it is raised by the same code in the same order; the first one in file order, because
nothing after a range that stopped is looked at.

**What "adding it" means, effect by effect**, because this is where exactness could fail:

- *Rows and the count.* The range's rows are appended in order; row numbers are offset by the
  rows before it; the first ragged row is the first range's that has one, its row and line
  numbers offset by the rows and lines before it. The lines of a range are counted by the thread
  and added by the parent in order.
- *Pages and bounds on rows.* `--limit`, `--max-rows` and the JSON byte budget are decisions
  that depend on how much came before, and they are taken by the parent: a range is taken only
  if the page would still not be full after all of it, the row bound not passed, and the budget
  respected for its last row; otherwise it is read again and the sequential code stops where it
  stops. (`--from` on a selection starts the page late and the read is sequential; on a grouping
  it pages the groups and does not touch the rows.)
- *Groups.* Each thread counts its range into groups of its own and writes them out as bytes.
  Counts add, minima and maxima take the smaller and the larger, distinct values are a union
  (the pairs are remapped to the parent's group numbers), and the groups are a set: the output is
  sorted by key, so no order of merging shows. **Sums are a pair of integers** (the low 32 bits of
  every cell in one, the rest in the other), added in any order: the merge of a range is a plain add and
  the sum is exact whatever the width. (The first version refused a sum that left 64 bits, at the first row
  where a *running* sum did, which a range's own sum from zero cannot tell: each thread kept the largest running
  sum (`peak`), and a range was merged only if `|S| + peak` fit; otherwise the parent read it again. Both the refusal
  and the `peak` are gone, `docs/numbers.md` stage N0p; a range of this read is now re-read for a bound, a refusal or a
  page, never for a sum.)
- *Group bounds* (`--max-groups`, `--max-distinct`, `--max-state-bytes`). The sequential read
  refuses at the first row that passes one, and which of several refusals comes first depends on
  the row. A range whose merge would pass any bound is not merged, it is read again by the parent,
  which then raises whichever the sequential read would.
- *What no thread decides:* the header (read first, sequentially), the plan (resolved against the
  header, then copied), the sort and top and page of the groups (done once, by the parent, after
  everything is merged, by the code the sequential read uses).

**Output and memory.** Rows are written by the parent in file order: csv is flushed in 64 KiB
blocks as ranges are taken; JSON is the page. A thread's output is bounded by its slot (the range
size plus 64 KiB): one that does not fit makes the parent read that range itself, in streaming,
so memory is **O(threads x range)** and never the file. A grouping also holds its groups, and
each thread its own: bounded by the same three limits, so up to N times what the sequential
read holds in the worst case.

**When it is used.** `--threads N` with N > 1, on rows (a selection or a filter) or groups, on data of at
least `--parallel-min-bytes` after the header (the threshold below). The shape (a row count,
30 ms for the benchmark file) is not parallelised: there is nothing to gain. A selection
with `--from` is sequential.

## 4. Tests

`tests/conformance/test_parallel.py` runs every case with N in {2, 3, 4, 8, 16} and ranges of
{1, 7, 64, 1000} bytes (`--chunk-bytes`: a boundary falls inside almost every record) and
requires the sequential bytes:

- quoted fields holding newlines and doubled quotes, so that a boundary falls inside a quoted
  field at every chunk size; stray quotes in unquoted fields; ragged rows at boundaries;
  blank lines before late errors (the lines named are the same); an empty file, a header only, a
  file smaller than the ranges, a BOM, CRLF, tab and semicolon delimiters, non-UTF-8 bytes;
- errors in two different ranges (the first in file order wins), an unterminated quote, a bad
  quote, a single 40,000-line record;
- sums at and past the edge of 64 bits, in every order, equal to Python's `int`;
- every limit: `--limit`, `--from`, `--max-rows`, `--max-bytes`, `--max-groups`, `--max-distinct`,
  `--max-state-bytes`, as csv and as JSON;
- 350 generated tables and plans (the generators of the sequential tests), 4 configurations each;
- a file of 120,000 rows (several default-size ranges) with quoted newlines, five plans, every N;
- 300 fuzz inputs through six plans, no trap, the sequential answer;
- the same run 200 times: the same bytes (nothing in the answer depends on which thread finished
  first);
- memory: peak resident set stays within the threads times the range, not the file.

`scripts/parallel_mutants.py` has 33 mutants of the new logic (stitching, the speculation check,
page and budget decisions, the merge of every aggregate, the peak of a sum, the signed encoding):
the result is in the pull request.

## 5. Measurements

The file is the design's (1,000,000 rows, 31,667,311 bytes), minimum of 5 interleaved runs,
output to /dev/null, `python3 scripts/bench_parallel.py`. Before any timing every contender's
answer is checked against one computed independently in Python (DuckDB's and csvtk's as well), and a
disagreement stops the run. `table` is built here by the Mac compiler (`db7d5bc`, the pinned one's
fix) and, for Linux, by the pinned compiler.

### The scaling curve, Apple silicon Mac (16 cores), seconds

| question | `table` 1 | 2 | 4 | 8 | 16 | speed-up at 4 / 8 / 16 |
|---|---:|---:|---:|---:|---:|---:|
| filter `status=404 and bytes>50000`, csv | 0.0514 | 0.0351 | 0.0200 | 0.0122 | 0.0120 | 2.6x / 4.2x / 4.3x |
| cut two columns, csv | 0.0577 | 0.0374 | 0.0210 | 0.0129 | 0.0108 | 2.8x / 4.5x / 5.3x |
| group-count by status | 0.1102 | 0.0612 | 0.0330 | 0.0172 | 0.0150 | 3.3x / 6.4x / 7.3x |
| group sum of `bytes` by status | 0.1214 | 0.0689 | 0.0366 | 0.0194 | 0.0177 | 3.3x / 6.3x / 6.9x |

The grouping scales best (its threads send back a few bytes), the filter and the cut worst (their
threads send back rows, which the parent copies and writes one range at a time). At 4 threads
every question is above the 1.5x the work was to reach; the sequential read is unchanged in the
first column, and agrees with every other column byte for byte.

### The same, on Linux x86-64, cores 0 to 5 of a shared box (niced, a soak running), seconds

| question | 1 | 2 | 4 | 6 | speed-up at 2 / 4 / 6 |
|---|---:|---:|---:|---:|---:|
| filter | 0.124 | 0.081 | 0.071 | 0.064 | 1.5x / 1.8x / 1.9x |
| cut | 0.112 | 0.082 | 0.070 | 0.067 | 1.4x / 1.6x / 1.7x |
| group-count | 0.210 | 0.107 | 0.095 | 0.087 | 2.0x / 2.2x / 2.4x |
| group sum | 0.236 | 0.121 | 0.115 | 0.098 | 1.9x / 2.1x / 2.4x |

**Why this flattens, and what was checked.** The i7-1260P's logical CPUs 0 to 5 are three physical
cores (`lscpu -e`: 0/1, 2/3 and 4/5 share a core each), so six threads are three cores and their
hyper-threads. Two checks that it is the machine and not the design:
(1) on cores 0, 2 and 4, one thread to a physical core, three threads give 2.3x on both groupings
and 1.9x on the filter; (2) six *independent* sequential `table` processes, which share nothing at
all, each took 3.8x as long as one alone (0.629 s against 0.164 s), an aggregate speed-up of
1.56x on six logical cores, against the 2.4x the six threads of one process get. **Memory
bandwidth is not the limit** either: one thread reads the file at 150 to 270 MB/s (compute bound:
parsing and counting), far below what the memory can give, and the Mac, which has the bandwidth,
reaches 7x. `perf` is not permitted on that machine (`perf_event_paranoid`), so there is no profile; the
two experiments above are what there is.

### `table` against DuckDB and csvtk (Mac), seconds

| question | `table` 1 thread | `table` 4 | `table` 16 | DuckDB 1 thread | DuckDB 4 | DuckDB default (16) | `csvtk` -j 1 | `csvtk` best -j |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| filter | 0.051 | 0.020 | **0.012** | 0.176 | 0.074 | 0.075 | 0.308 | 0.262 (-j 2) |
| cut | 0.058 | 0.021 | **0.011** | 0.187 | 0.079 | 0.078 | 0.185 | 0.185 (-j 1) |
| group-count | 0.110 | 0.033 | **0.015** | 0.129 | 0.062 | 0.062 | 0.186 | 0.186 (-j 1) |
| group sum | 0.121 | 0.037 | **0.018** | 0.135 | 0.064 | 0.063 | 0.403 | 0.293 (-j 8) |

Peak resident set: `table` 1.6 to 2 MB at one thread, 3 to 41 MB at 4 to 16 threads
(threads x range); DuckDB 56 to 80 MB; csvtk 23 to 28 MB (122 MB for the sum). **`csvtk -j N` does
not scale on any of these** (0.8x to 1.4x at best). What this does and does not say: DuckDB is
a SQL engine with type detection, a planner and a spill path, run here the way one runs it
(`read_csv` with detection, `COPY ... TO '/dev/null'`), and its time is the whole process's, as
`table`'s is; it stops improving at 4 threads on this file (its own parallelism is by
row group and the file is 31 MB). `table` does a small fraction of what it does. The comparison is
of the same four questions answered with the same bytes, not of the tools.

### Where threads start to pay (data after the header; `--parallel-min-bytes 0`, group sum)

| size | sequential | 2 threads | 4 | 8 |
|---|---:|---:|---:|---:|
| 0.25 MiB | 2.8 ms | 2.4 | 2.1 | 2.2 |
| 0.5 MiB | 3.8 | 3.1 | 2.5 | 2.4 |
| 1 MiB | 5.8 | 4.2 | 3.1 | 2.8 |
| 4 MiB | 18.2 | 10.9 | 6.6 | 4.9 |
| 16 MiB | 64.0 | 34.9 | 19.2 | 11.1 |

(Mac. On Linux, 0.25 MiB: 3.2 ms against 2.8, 2.9 and 2.9; 1 MiB: 9.0 against 6.9, 6.2 and 5.2.)
Threads already break even at a quarter of a megabyte (starting a thread costs about 100
microseconds) and win clearly from a megabyte; below that the process's own start-up is the
answer. The default `--parallel-min-bytes` is **1 MiB**: smaller than that is read by one thread,
whatever `--threads` says.

## 6. A page that ends early

A page (`--limit`, JSON's default is 1000) may be complete after a few rows, and a wave of N
ranges read before that is found out is work thrown away. So the waves of a *paged* answer start
with one range and double (1, 2, 4, ...), which bounds what is read beyond the page to what was needed;
a grouping, or csv without a limit, needs the whole file and starts at N. The answer is the same
either way (the limit tests run both).

## 7. What is left

* No work stealing: the ranges of a wave are equal and the wave waits for its slowest. A queue needs
  atomics, which are being designed and are not used here.
* A thread's output is copied three times (its buffer, its slot, the parent's array) before it is
  written; a `buffer.append` through a `&!` reference would remove two.
* A range whose guess is wrong is read twice. A file whose every boundary is inside a quoted
  field (a text column with newlines, in every row) is read at about the sequential speed plus
  the wasted work; it is correct, and not faster.
* Threads for the shape (a row count) and for a selection that starts late (`--from`) were left out:
  the first is 30 ms, the second reads sequentially up to its page.
* The test of the slot overflow and of a re-read's block of csv checks the answer, and memory only loosely.
* `--threads` takes a number. An `auto` (the number of cores) was not added: the language cannot ask
  how many there are, and a guess that is too high costs memory.
