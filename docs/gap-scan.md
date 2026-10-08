# The scan cost and the thread-scaling gaps of the CSV reader

Two trees were measured, and **every number in this document says which**:

* **tree A**, sections 1 to 10: the maintainer's old local clone of `lexsys-table` (`main` at 435ba27, sources `.ls`, built with the
  `lex-sys-static-sig` compiler, db7d5bc), branch `gap-scan-design` of that clone. The design study: the reproduction, the profile, the
  kernels, the block scan, the speculation first written, the rejected variants. Those numbers are valid for that tree and compiler.
* **tree B**, section 11: `alpibrusl/cancho-table` `origin/main` at 7d04b9f (sources `.cho`, compiler cancho a4572ea, the pin of
  `cancho.toml`) with this PR on top. The speculation and the production-safe range sizing were **ported** to it and gated there; the
  numbers of the PR are the ones of section 11. The block scan (section 4.4) is **not** in this PR: it needs two compiler builtins that do
  not exist (the patches stay in `scripts/spikes/scan/`, written against tree A, and do not apply to tree B as they are).

Machines: the Mac (Apple silicon, 12 performance and 4 efficiency cores, page cache warm) and `gram` (Intel i7-1260P, cores 0-5 = 3
physical performance cores with two threads each, other work on the box). Every timing is the minimum of 5 to 9 interleaved runs and the
load average is stated next to it; instruction counts do not depend on the load and are the figures to trust where the box was busy.

## 0. Summary (tree A; the port to tree B is section 11)

| gap | what was found | what was built | result |
|---|---|---|---|
| (2) long fields with newlines do not scale | the parent's guess "a range starts outside quotes" is wrong at 99-100 percent of the boundaries of such a file, so every range is read twice | the worker looks 64 KiB ahead and guesses *inside* when that reading gives two or more whole records of the right width and the *outside* reading does not (`scan.guess`); the parent's check is untouched | E1 at 8 threads 0.18 s to 0.031 s (Mac), 0.186 s to 0.057 s at 6 threads (gram). Same bytes on 134 corpus plans, 432 differential runs on each machine, all 111 conformance tests. A file built so that the wrong reading also looks right gets no speed-up and no slow-down |
| (1) the scan is 40-48 percent of every question | 37 to 46 instructions per byte, 1100 to 1450 per row; `reader.fields` is 37-45 percent of them. Making the scan **free** (memchr for the line, fixed cells) leaves 0.84x to 0.52x of today's time | a block-wise structural scan needs two primitives the language lacks (`byte_mask64`, `trailing_zeros`); with them (a compiler patch, LLVM only) the reader is 5 percent fewer instructions and 12-15 percent fewer cycles on the 31.7 MB file, and 38 percent faster on a 200-column file. Without them (SWAR, one pass, rewritten loops) it is **not faster**: 6 variants measured, all within noise or worse | 0.84x-0.91x of the time on the four everyday questions (both machines), G2 1 GB at one thread 1.72 s to 1.48 s (Mac), 2.37 to 2.03 s (gram) |
| (3) scaling stops | the scan is *instruction bound* (IPC 5.3 on one thread of a performance core), so the second thread of a core adds nothing (6 threads on 3 cores: 5.5 CPU-seconds for 2.35 s of work); a wave is as slow as its slowest thread; 4 MiB ranges mean 16 waves of 16 slots for 1 GB | in group mode the range is the thread's share of the file and the slot holds groups, not rows | G2 at 16 threads 0.234 s to 0.172 s (Mac); gram at 4 threads 1.35 s to 0.97 s. The ceiling on gram is 3 physical cores, whatever is done |
| the ceiling | per core `table` is **already faster than DuckDB's single thread: 1.9x on G2 on the Mac, 1.2x on gram (2.2x on G1), 2-3x on the 31.7 MB file**; DuckDB's 3.2 GB/s is 10 of its threads. A free scan would be 1.2x to 1.9x on the whole row (gram); C with SIMD structural indexing cuts the scan kernel 2.3x (Mac) to 2.6x (gram) | | see section 8 |

What to take from the branch: the speculation (section 5) and the range sizing in group mode (section 7) are plain lex-sys
(pinned compiler) and are committed. The block scan is kept as `scripts/spikes/scan/block-scan.patch` with the compiler
patch it needs (`compiler-byte-mask64.patch`); it should wait for the two primitives upstream, and then be written for
whole records with embedded newlines too (section 4.5).

## 1. The gaps, reproduced (before) (tree A)

`build/table.base` is `main` of `lexsys-table` at 435ba27 built with the compiler of `lex-sys-static-sig` (db7d5bc; the one
that accepts the pinned `toolbox` store), `--threads N`, output to `/dev/null`, minimum of the runs.

| cell | Mac t1 | t4 | t8 | t16 | DuckDB t1 / t16 (Mac) | gram t1 | t3 | t6 | DuckDB t1 / t6 (gram) |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| G2 1 GB group-count | 1.73 | 0.58 | 0.34 | 0.32 | 3.35 / 0.34 | 1.99 | 0.86 | 0.96 | 2.9 / 1.28 |
| G1 1 GB filter | 1.47 | | 0.36 | | 4.8 / 0.50 | 2.24 | | 0.93 | 5.2 / 2.2 |
| E1 long fields select (114 MB) | 0.182 | | 0.187 | | 0.38 / 0.31 | 0.158 | | 0.165 | 0.40 / 0.36 |
| E2 long fields count | 0.180 | | 0.188 | | | | | | |
| the same file, no newline in the fields | 0.0193 | | 0.0106 | | | 0.0208 | | 0.0144 | |
| the same, newline every ~17 bytes (`longq_nl.csv`) | 0.168 | | 0.171 | | | 0.18 | | 0.18 | |

The 290 MB/s per core of the brief is the *loaded* gram (my first run, other agents on cores 0-5: 0.110 s for the 31.7 MB
file); with the box quiet one performance-core thread reads 560-590 MB/s (select 0.054 s) and the Mac 650-700 MB/s. Per core,
then, `table` is not slower than DuckDB; it is 1.9x faster on G2 on the Mac (1.73 s against 3.35 s), 1.2x on gram (2.37 s
against 2.90 s; DuckDB's single thread there was 4.4-6.0 s while the box was loaded), 2.2x on G1 on gram, and 2-3x on the
31.7 MB file. The loss in the brief, "4 cores against 16", is a difference in the number of cores.

The standard 31.7 MB file, one thread, seconds: select 0.049 / 0.055 (Mac / gram), filter 0.044 / 0.055, count 0.054 / 0.059,
sum 0.060 / 0.068.

## 2. Where the time goes (gram, `perf`, user mode, instructions)

`scripts/spikes/scan/tstat.sh`, `prof.sh`. One thread, the 31.7 MB file, 1,000,000 rows.

| question | instructions / byte | per row | cycles / byte | `reader.fields` | read loop (`read_file`, which holds `next_fast`, `count_row`) | rest |
|---|---:|---:|---:|---:|---:|---|
| select status,bytes | 37.2 | 1177 | 7.0 | 42% (493) | 20% (240) | csv_cell 13%, emit_buf 10%, memchr 6%, screen 4% |
| filter | 35.4 | 1121 | 7.0 | 45% | 17% | expr.eval 11%, holds 7%, memchr 5% |
| group-count | 40.1 | 1269 | 7.5 | 43% | 24% | add_fast 17%, group_plain 11% |
| group-sum | 46.1 | 1461 | 8.5 | 37% | 19% | add_fast 20%, group_plain 11%, parse_int 8% |

IPC is 5.3: a performance core retires five instructions a cycle on this code, so time is the instruction count and a
branch miss (0.22 a row) is noise. `fields` is 90 instructions a field for fields of 6 bytes: the byte loop is 6 to 9
instructions a byte (compare against the end, the byte load, two compares, increment, and an overflow `jo` that never
fires), and about 45 per field is fixed (three bounds-checked stores of the cell, checked increments, the quote test).

**The floor experiment.** `reader.record` replaced by "memchr for the newline, five fixed one-byte cells"
(`build/table.floor`, wrong answers, right work): the scan made free except for the line split.

| question | Mac base | scan free | ratio | gram base | scan free | ratio | gram instr / row base | scan free |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| select | 0.049 | 0.031 | 0.63x | 0.056 | 0.036 | 0.65x | 1177 | 769 |
| filter | 0.044 | 0.026 | 0.58x | 0.059 | 0.031 | 0.52x | 1121 | 640 |
| group-count | 0.054 | 0.039 | 0.73x | 0.063 | 0.053 | 0.84x | 1269 | 896 |
| group-sum | 0.060 | 0.044 | 0.73x | 0.072 | 0.058 | 0.80x | 1461 | 1029 |

That is Amdahl: the best any scanner can do for the four questions is 1.2x (a group by) to 1.9x (a filter) on gram, 1.4x to 1.7x on the Mac. The rest is the
read loop (240-390 instructions a row), the engine, and writing.

## 3. Reading (c): the kernel copy is not the problem

* Pure read of the 1 GB file, warm cache, `dd` into `/dev/null`: Mac 13-15 GB/s (0.075 s) for blocks of 256 KiB to 16 MiB;
  gram 8.4-9.3 GB/s (0.12 s) for 64 KiB to 4 MiB. That is 4% of `table` at one thread (1.7 s) and 6% on gram. It is
  `cat`'s `copy_file_range` that reaches 50 GB/s on gram, which reads nothing; `dd` copies.
* Cold cache (`posix_fadvise(DONTNEED)` on the file only), gram: `dd` 0.55 s (1.9 GB/s, the NVMe); `table` G2 at one thread
  2.40 s cold against 2.34 s warm, at 3 threads 0.99 cold against 0.94, at 6 threads 1.03 against 0.98: the kernel's
  readahead overlaps the read with the scan and **the scan is slower than the disk until about 3.5 times today's speed**. A
  scan three times faster on gram would meet a 0.55 s floor for the cold 1 GB file.
* The read chunk (the contract's 64 KiB), varied in `scan_range` (`chunk-size.snippet.ls`), G2 on the Mac: 16 KiB +6% at 8
  threads, 32 KiB +4%, 64 KiB the reference, 128 KiB +1%, 256 KiB +1%, 1 MiB +3%, 4 MiB +21% (the chunk leaves the L2).
  Round 7 of `history.md` found the same at one thread. One `pread` buffer per worker already is what the code does (the
  `Lines` of a range). Overlapping the read with the parse has nothing to hide: warm, the copy is 4-6%.
* So (c) closes with nothing to change in the reading itself. What the ranges cost is in section 7.

## 4. A block-wise structural scan (a)

### 4.1 What the language can say

* `index_of_byte` is a call to `memchr`. For a field of 6 bytes the call costs more than the walk it saves (round 3 of
  `history.md`), and for a field of 5,000 bytes the walk costs 5-7 instructions a byte against `memchr`'s 0.1. That is why a
  pure walk was 2.5x slower on long fields (4.8x measured here on `longplain.csv`: 0.078 s against 0.016 s) and why the
  hybrid (24 bytes by hand, then `memchr`) was kept.
* A 64-bit word cannot be loaded. It can be built from eight indexed byte loads, and clang folds those into one `ldr`/`mov`
  **only when the code is written inline in a loop guarded by `i + 8 <= len`**: put in a function (`w8.ls`: 15 instructions,
  eight checks and eight `ldrb`) it is not inlined and not merged, and `text[i..i+8]` followed by `w[0]..w[7]` does not fold
  either (the length is `(i+8)-i`, with a trap on the add). `wrapping_add` for the index keeps the optimiser from proving what
  it needs.
* There is no count of trailing zeros and no population count. The lowest flagged byte of a SWAR word is found by isolating
  the bit and multiplying by `0x0001020304050607` (8 instructions).
* A bit-gather of 16 byte compares (`((c ^ 44) - 1) >> 63 & (1 << k)`, written out) is half-vectorised by SLP into a
  mess of widened 64-bit lanes (`gather.ls`): no `movemask`. The vectoriser does not see the idiom.
* The 64-bit `+` is checked. Every index and counter pays a `jo`; `wrapping_add` removes it when the value is provably
  bounded. LLVM also if-converts a short chain of tests into `setcc`/`cmov` and gives up the fast path one wrote.

### 4.2 The kernels (`scripts/spikes/scan/kern.ls`, `c/ceil.c`)

The whole 31.7 MB file in memory, each record cut into fields (offsets in a cell array) and a checksum of the lengths; every
variant answers the same checksum (31,667,312). Milliseconds, minimum of 5-7 rounds; instructions / byte and cycles / byte
on gram from `perf stat` (`ipb.sh`).

| variant | Mac ms | gram ms | gram instr/B | gram cycles/B |
|---|---:|---:|---:|---:|
| lex 9: memchr for the newline only (floor of the line split) | 7 | 5.5 | 1.6 | 0.8 |
| lex 0: production (memchr for the line, `reader.fields`) | 25 | 34 | 21.8 | 4.3 |
| lex 1: one pass, byte loop, no memchr | 21 | 27 | 16.3 | 3.5 |
| lex 2: one pass, SWAR words (inline 8-load, multiply for the position) | 40 | 28 | 16.1 | 3.8 |
| lex 3: stage 1 SWAR token index per 32 KiB, stage 2 token walk | 24 | 35 | 25.3 | 4.2 |
| lex 4: stage 1 with the **builtins** (below), stage 2 as 3 | 17.5 | 26 | 17.3 | 3.0 |
| C 0: byte loop | 14.6 | 20 | 10.7 | 2.5 |
| C 1: SWAR, `ctz` | 23.5 | 27 | 7.7 | 3.8 |
| C 2: 16-byte vectors (SSE2 `movemask` / NEON), position array, token walk | 11.5 | 15.7 | 9.4 | 1.9 |
| C 3: the same, four vectors combined into a 64-bit mask | 11.0 | 13.3 | 8.6 | 1.6 |

* SWAR is **slower** than the byte loop on the Mac (the byte loop is what that core does best) and equal on gram, in lex-sys
  *and* in C. 6-byte fields do not amortise a 25-instruction word step, and a record has a field every 6 bytes. The idea pays
  for fields of 16 bytes and more, which `memchr` already does.
* The byte loop needs half the instructions in C (10.7 per byte against 16.3 for the same loop in lex-sys, 21.8 with the
  `memchr` and `fields` split) and runs 1.35x faster: the checked adds, the bounds checks and the unfused cell stores.
* Structural indexing with real vectors is the only variant that moves it: 2.3x against production lex-sys on the Mac, 2.6x
  on gram, 1.3-1.5x against the C byte loop. The shape of that: 4 vector loads, 12 compares, 4 `movemask`s a block of 64 bytes
  (about 45 instructions, 0.7 a byte), then a bit loop of `ctz`, store, `x & (x-1)`, and a walk over the tokens by the
  grammar that is 8-9 instructions a byte on this file because it has a structural byte every 3.5 bytes (a quoted `note`
  column with a comma in it).

### 4.3 The primitives, prototyped (`compiler-byte-mask64.patch`, LLVM backend only)

Two builtins in a copy of the compiler (`gap-scan-compiler`, not committed; the patch is 216 lines):

```
byte_mask64(text: &t [byte], at: int, a: byte, b: byte, c: byte) -> int   // bit k set iff text[at+k] is a, b or c; traps unless 0 <= at <= len-64
trailing_zeros(x: int) -> int                                              // cttz, 64 for 0
```

Four `load <16 x i8>`, `icmp eq` against splats, `or`, `bitcast <16 x i1> to i16` (LLVM turns that into `pmovmskb` or the
NEON narrow-and-extract), `shl`/`or` into one `i64`. Cranelift is left `unreachable!` (a spike). With them the kernel (lex 4)
is 1.4x better than production on the Mac and 1.3x on gram, in 12 lines of lex-sys.

### 4.4 In the tool (`block-scan.patch`: `reader.record`, `scan.next_rec`, two call sites)

`reader.record` finds one record from its first byte with the mask: it walks the tokens by the grammar (a quote at the start of
a field opens a quoted field; inside, a doubled quote is text and a single one closes it and must be followed by a
delimiter, a newline or CR LF; a stray quote in an unquoted field is text), fills the same cells as `fields`, and answers
the newline's index. It takes **only** the common record: whole on one line inside the chunk, at least 64 bytes before the
end of the chunk, well formed, not longer than the cap. Anything else (a quote open at the newline, a bad quote, the chunk's
tail, a long line) returns "no" and the line goes the way it always did, through `next_fast` or `lines.next` and `fields`, which
raise every refusal: **the refusals, their rows and lines, are the old code's**. `scan.next_rec` is `next_fast` plus the cells. It
is used for data records (`header` true, `mode != 0`, no record open) in the sequential loop of `table.ls` and in `scan_range`.

Gate: the 134 plans of `scripts/corpus.py` give the same md5, status and error as `main`, on both machines, sequentially
and with threads and tiny ranges; the conformance suite (111 tests) passes; `specfiles.py` (432 runs of hostile files)
shows no difference.

| cell (one thread) | Mac before | after | | gram before | after | | gram instr/row before | after |
|---|---:|---:|---|---:|---:|---|---:|---:|
| select | 0.049 | 0.041 | 0.84x | 0.060 | 0.055 | 0.91x | 1177 | 1099 |
| filter | 0.044 | 0.038 | 0.87x | 0.064 | 0.055 | 0.86x | 1121 | 1044 |
| group-count | 0.054 | 0.046 | 0.86x | 0.068 | 0.060 | 0.87x | 1269 | 1189 |
| group-sum | 0.060 | 0.055 | 0.91x | 0.076 | 0.070 | 0.91x | 1461 | 1380 |
| 200 columns, select 3 (196 MB) | 0.183 | 0.114 | 0.62x | 0.350 | 0.228 | 0.65x | | |
| all fields quoted (47 MB) | 0.062 | 0.063 | 1.0x | | | | | |
| long unquoted fields (110 MB) | 0.0161 | 0.0162 | 1.0x | | | | | |
| 1 GB group-count | 1.715 | 1.475 | 0.86x | 2.372 | 2.030 | 0.86x | | |

The instruction count falls only 5-7 percent (the token walk is still ~45 instructions a token: the state variables do not
fit in registers and LLVM turns the branches into `cmov` chains) but cycles fall 13 percent because the branch misses fall from
7.4 to 3.6 a kilobyte (0.27 for group-count). The 200-column file profits most (a record is many blocks, and a delimiter
every 5 bytes costs a token walk step instead of a byte loop). The long-field file keeps the speed of the `memchr`
hybrid, which is the answer to "why was the pure walk 2.5x slower": a block of 64 bytes with no structural byte is
about 30 instructions, 0.4 a byte, against 6-7, and a field of 5,000 bytes is then 80 blocks.

### 4.5 What was tried and did not work (all gated by the corpus, all measured)

| try | result |
|---|---|
| SWAR 8-byte steps inside `reader.seek` (the existing structure) | **+7% instructions** (37.2 to 39.8 per byte on select), cycles +2%; branch misses fall 7.4 to 0.5-3.9 a KB |
| one pass over the line, no `memchr` for the newline (`reader.record`, byte loop, `c > hi` shortcut) | instructions -1.5%, time 0.95x-1.03x (noise); LLVM if-converts the shortcut away (17 instructions a byte on the Mac) |
| the same with the inner loop restructured (a tight `while data[i] > hi` skip) and `wrapping_add` | the loop is 6 instructions a byte, the total the same |
| the same with `wrapping_add` everywhere and one guard for the three cell stores (`record` rec3) | **+4% instructions** |
| `reader.fields` itself rewritten with wrapping indices and one guarded store | +1.5% instructions, cycles -2% (noise) |
| a larger or smaller read chunk | section 3 |
| unrolling and the pure byte walk | `history.md` rounds 3, 11 |

The honest reading: in-language, the scalar scan is at its floor for this compiler. The time is in how many instructions the
checked, bounds-checked, if-converted code costs, and only a primitive that does 64 bytes in a handful of instructions gets
under it.

### 4.6 What the primitives would still not give, and what would

* Whole-row 5-7 percent fewer instructions and 13 percent fewer cycles, **not** the 2.5x of the kernel, because the token walk is
  as long as the byte walk it replaces on this file and because the scan is 35 percent of the row (section 2).
* A second use is larger: **records with newlines inside quoted fields** (E1/E2). `record` stops at a newline inside quotes
  and the line goes to `lines.next`, `reader.scan` and `rec` (330 lines per 5.7 KB record, 28 ns each). A mask scan that
  crosses the newlines, with the quote-only mask for the quoted part, delivers the record as one slice
  (`data[start..end]` is exactly what `rec` builds, CRs included). The kernel does the whole 114 MB file in 51 ms (Mac) and
  49 ms (gram) with tokens inside quotes *not even skipped*, against 170-190 ms for the whole of E1 at one thread today.
  Not built: it needs the line counter to jump by the lines the record held, and the places where `number` is read (refusals,
  `consumed` at a range end and at a flush) must see the first line of the record while the counter ends at its last.
* Upstream, in order of value: `byte_mask64` (or any 64-byte "which of these three bytes" mask), `trailing_zeros`, a checked
  `load_le64(text, at)` (one bounds check, one load: it makes SWAR 25 percent cheaper), and an attribute that lets a small
  function be inlined (`w8.ls`).

## 5. The speculation (b)

### 5.1 Why E1/E2 do not scale

Ranges are cut at line starts and each thread assumes its first line starts a record. In a file whose records are 5.7 KB of
quoted text with a newline every 17 bytes, **112 of 113 boundaries (99%) of `long.csv` and 110 of 110 of `longq_nl.csv` are
inside a quoted field** (`guessrate.py`, ranges of 1 MB). Each guess is wrong, the parent re-reads each range, and the
time is the sequential time plus the wasted threads: 0.18 s at 1 thread and at 16. On an ordinary file (`data.csv`, `f2.csv`,
`quoted.csv`, `wide.csv`, `longq.csv`) the guess is wrong 0 times in 31 to 195 boundaries. Everything is in the table:

| file | boundaries | inside quotes (truth) | "outside" wrong (today) | lookahead wrong | lookahead says inside |
|---|---:|---:|---:|---:|---:|
| data.csv, f2.csv, quoted.csv, wide.csv, longq.csv | 31-195 each | 0 | 0 | 0 | 0 |
| long.csv (E1/E2) | 113 | 112 | 112 | **0** | 112 |
| longq_nl.csv | 110 | 110 | 110 | **0** | 110 |
| few short multi-line fields (1 in 10 rows) | 118 | 6 | 6 | 0 | 6 |
| stray quotes in unquoted fields + multi-line | 19 | 9 | 9 | 0 | 9 |
| ragged rows near multi-line fields | 14 | 10 | 10 | 0 | 10 |
| bad quote late in the file | 11 | 9 | 9 | 0 | 9 |
| **lookalike**: the quoted lines are themselves valid 3-column records | 161 | 151 | 151 | **151** | 0 |
| lookalike with CR LF | 176 | 174 | 174 | 174 | 0 |
| lookalike, three-field lines in 2000-9000-line fields | 394 | 392 | 392 | 392 | 0 |

### 5.2 The design (committed: `scan.ls`, `par.ls`, `engine.ls`)

1. A thread whose range is not the first of its wave reads the first 64 KiB of its range and asks `scan.guess(buf, delim,
   columns)`. It reads up to four records by lines with the reader's own rules (`reader.scan`), from two states: *outside* (the
   line is the start of a record) and *inside* (the first line is the tail of a quoted field; read to the close of the
   quote, drop that tail, then records). A state is *plausible* when two or more whole records come out with exactly
   `columns` fields and no bad quote. It answers "inside" only when *outside* is not plausible and *inside* is; otherwise
   "outside", which is today's behaviour. The cost: one 64 KiB `pread` and the parsing of a few records per range.
2. `scan_range` takes `hint`. With `hint = 1` it starts with a quoted field open and `skipping`: lines are read with
   `reader.scan` and dropped (not kept in `rec`, not counted as rows) until the quote closes; the first record of the range
   is the next line. The skipped lines are subtracted from the line count it reports, and from `base_line`, so a refusal inside
   the range names the same line as the sequential read. A bad quote or the end of the input while skipping stops the range
   unclean, and the parent reads it again.
3. The worker reports where its first record begins in the tally (`engine.k_first_at`, a slot that was unused): the range
   start with `hint = 0`, the line after the dropped tail with `hint = 1`.
4. **The parent's check is the same equality, on a different number**: it takes a range when the *first record's start*
   equals `cur`, the place where the previous range's last record ended. Nothing else about acceptance changed. A wrong guess is
   a re-read, exactly as before; the re-read itself (the parent's `scan_range`, called with `hint = 0`) is the sequential code.

**Why this stays exact although parity is not sound.** A stray quote in an unquoted field is text (`a"b,"c,d"`), so the number
of quotes before a boundary does not give the state, and a guess from parity would be a guess too. This design does not rely on
being right: the guess only chooses which of two readings the thread performs, and the *verification* is positional: the parent
accepts a reading only if its first record starts exactly where the sequential read stands, and from a record start the
state machine of the reader is deterministic, so what follows is what the sequential read produces. The lookahead being a
heuristic is a matter of speed, not of correctness, and the conformance and differential suites check that.

### 5.3 Results

Same bytes on all of: the 134 plans of `corpus.py` (Mac and gram), the 22 parallel conformance tests and the full 111
(ranges from 1 byte to 4 MiB, 2 to 64 threads), 432 runs of `specfiles.py` on each machine (9 hostile files x 4 plans x 3
thread counts x 4 range sizes, including the lookalike, CR LF, stray quote, bad quote, ragged and unterminated cases),
and 27 default-range runs on the 1 GB file.

E1 (select `id,g`), seconds, `table.base` -> `table.spec`:

| threads | 1 | 2 | 3 | 4 | 6 | 8 | 16 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mac | 0.178 -> 0.177 | 0.179 -> 0.096 | | 0.179 -> 0.050 | | 0.180 -> **0.033** | 0.174 -> **0.029** |
| gram | 0.145 -> 0.148 | 0.147 -> 0.083 | 0.153 -> 0.059 | 0.156 -> 0.064 | 0.163 -> **0.051** | | |

E2 and `longq_nl.csv` the same (Mac t8: 0.178 -> 0.037, 0.180 -> 0.040; gram t6: 0.162 -> 0.062, 0.181 -> 0.051). Ordinary files
are unchanged: G2 and G1 at 2 to 16 threads, `longq.csv` and the standard file are within +-5 percent (the Mac was noisy).
On the hostile files at 8 threads (generated at 25x, `specbig_*`): ragged 6.0x, stray quotes 5.9x, bad quote 3.2x, unterminated
3.2x, short multi-line fields 5.6x (was 4.4x); **lookalike, CR LF and "huge" 1.0-1.1x, exactly as before**.

### 5.4 The alternatives, measured or reasoned

* **Speculate both states per range** (the brief's suggestion), emulated: the worker also reads the range with the other
  reading and throws the answer away. E1 at 8 threads: guess 0.036 s, both states 0.056 s (1.6x worse), re-read 0.185 s;
  at 16 threads 0.027 / 0.036 / 0.175. Both is a good second line (it makes the lookalike file scale at about half speed)
  but it **must be bounded**: on a file without quotes the "inside" reading never ends and reads to the end of the file for
  every range. On `big.csv` the other reading aborted at the first quote of the `note` column, which hid this in the first
  timing. A bounded version would give "inside" a budget of a few record widths and stop. Not built.
* **A quote-parity pre-pass** (a parallel count of quotes per range, a prefix xor): exact for files without stray quotes, one
  extra pass over the whole file (about 5-10 percent of a scan) and a second wave of threads, for every file, to help a
  minority. Rejected as a default; as a *fallback* when the guess is wrong twice in a row it would fix the lookalike files.
* **Local parity** (look back from the boundary to the nearest quote and decide by its neighbours): not sound with doubled
  quotes, and the lookahead already decides almost every real case from the text *after* the boundary.
* **Accept the suffix of a wrong reading**: after a record start both readings are the same state machine, so a wrong reading
  whose records include one that starts at `cur` is right from there. Needs per-record tallies instead of per-range, which
  the slots do not have. Not built.
* **Sampling**: what the lookahead is, with a sample of one window.

## 6. Thread scaling (3)

### 6.1 gram: three cores, not six

`/usr/bin/time` on G2, `taskset -c 0-5`:

| threads | wall s | user s | CPU % |
|---|---:|---:|---:|
| 1 | 2.43 | 2.30 | 99 |
| 2 | 1.35 | 2.54 | 196 |
| 3 | 0.98 | 2.74 | 290 |
| 4 | 1.34 | 3.58 | 286 |
| 6 | 1.02 | 5.51 | 560 |

Six threads burn 5.5 CPU-seconds to do the 2.3 s of work: the second thread of a core adds no throughput because one thread
already retires 5.3 instructions a cycle. Four threads are *slower* than three: with static ranges in waves two threads share a
core and the wave waits for them (a work queue would fix that; the language has no atomics, `docs/parallel.md`). On this
machine `--threads` should be the number of physical cores, and the way to go beyond 2.4-2.5x is fewer instructions. The
cores 8-15 are efficiency cores and were not used.

### 6.2 The ranges (committed: `par.ls`)

Every wave allocates and zero-fills a slot of `range + 64 KiB` for each thread, forks a heap, opens the file and joins; the
parent merges in order between waves. With 4 MiB ranges, 1 GB is 16 waves at 16 threads. In group mode a range's answer is
its groups, so the slot can be small (8 MiB cap) and the range can be the thread's share. Effect of the range size alone
(`--chunk-bytes`, `table.spec`, G2, Mac, seconds):

| chunk | t4 | t8 | t12 | t16 |
|---|---:|---:|---:|---:|
| 256 KiB | 0.630 | 0.492 | 0.398 | 0.337 |
| 1 MiB | 0.528 | 0.317 | 0.283 | 0.260 |
| **4 MiB (default)** | 0.504 | 0.271 | 0.264 | 0.234 |
| 16 MiB | 0.492 | 0.257 | 0.241 | 0.216 |
| 64 MiB | 0.464 | 0.261 | 0.271 | 0.183 |

and with the default replaced in group mode by the share (`table.big` against `table.blk2`, both with the block scan): t4 0.467
-> 0.418, t8 0.260 -> 0.223, t16 0.225 -> 0.172 (Mac); gram t4 1.18 -> 0.97, t6 0.92 -> 0.90 (`table.base` there: 1.35 and
1.08). A 1 GB file with 100,000 keys at 8 threads: 2.15 s -> 1.19 s (the merge between
waves was the cost). Caveats: the slot must be sized from the group limits when they are raised (a range whose groups do not
fit is re-read by the parent: with 64 MiB ranges that is a big re-read), `--chunk-bytes` given as exactly 4 MiB is
indistinguishable from the default, and one range per thread is exposed to the slowest core (the Mac's efficiency cores
run at half speed): an adaptive scheme (waves of decreasing range size) would be better than this experiment.

## 7. Against DuckDB, csvtk and Miller (`final.py`)

`new` is `table.final` = the speculation + the range sizing + the block scan (with the compiler patch). Seconds, minimum of 5
(DuckDB 3, the 1 GB csvtk 2); DuckDB gets the same thread count; "-" is not measured (Miller is not on the Mac).

Mac:

| cell | threads | old | new | DuckDB | csvtk (1 thread) |
|---|---:|---:|---:|---:|---:|
| select status,bytes | 1 | 0.049 | 0.041 | 0.190 | 0.189 |
| filter | 1 | 0.044 | 0.038 | 0.175 | 0.298 |
| group-count | 1 | 0.054 | 0.046 | 0.132 | 0.187 |
| group-sum | 1 | 0.060 | 0.055 | 0.139 | 0.404 |
| G2 | 1 / 4 / 8 / 16 | 1.715 / 0.478 / 0.281 / 0.209 | 1.475 / 0.385 / 0.266 / **0.155** | 3.28 / 0.91 / 0.49 / 0.34 | 6.21 |
| G1 | 1 / 4 / 8 / 16 | 1.429 / 0.469 / 0.265 / 0.220 | 1.230 / 0.420 / 0.235 / 0.195 | 4.82 / 1.25 / 0.65 / 0.50 | 10.0 |
| E1 | 1 / 4 / 8 / 16 | 0.170 / 0.175 / 0.179 / 0.170 | 0.170 / 0.050 / 0.031 / **0.024** | 0.38 / 0.33 / 0.32 / 0.31 | 0.182 |
| E2 | 1 / 4 / 8 / 16 | 0.176 / 0.176 / 0.178 / 0.169 | 0.172 / 0.046 / 0.025 / **0.020** | 0.39 / 0.33 / 0.32 / 0.31 | 0.178 |

gram (cores 0-5):

| cell | threads | old | new | DuckDB | csvtk | Miller |
|---|---:|---:|---:|---:|---:|---:|
| select | 1 | 0.060 | 0.055 | 0.195 | 0.297 | 0.372 |
| filter | 1 | 0.064 | 0.055 | 0.182 | 0.482 | 0.252 |
| group-count | 1 | 0.068 | 0.060 | 0.117 | 0.301 | 0.236 |
| group-sum | 1 | 0.076 | 0.070 | 0.131 | 0.715 | 0.340 |
| G2 | 1 / 2 / 3 / 6 | 2.37 / 1.39 / 1.02 / 1.08 | 2.03 / 1.20 / 0.99 / 0.89 | 2.90 / 1.71 / 1.27 / 1.28 | 10.1 | 7.61 |
| G1 | 1 / 2 / 3 / 6 | 2.33 / 1.40 / 1.07 / 1.12 | 1.97 / 1.24 / 0.97 / 0.98 | 5.16 / 2.89 / 2.22 / 2.17 | 17.0 | |
| E1 | 1 / 2 / 3 / 6 | 0.187 / 0.189 / 0.185 / 0.186 | 0.167 / 0.103 / 0.071 / **0.057** | 0.40 / 0.39 / 0.38 / 0.36 | 0.216 | 0.212 |
| E2 | 1 / 2 / 3 / 6 | 0.174 / 0.180 / 0.183 / 0.181 | 0.166 / 0.092 / 0.066 / **0.053** | 0.39 / 0.38 / 0.42 / 0.38 | 0.202 | 0.227 |

The brief's G2 loss, "4 cores against 16 DuckDB cores": old 0.478 s against 0.339 s (1.41x slower); now 0.385 s against 0.339 s
(**1.14x slower**), and at 8 threads 0.266 s (**1.27x faster than DuckDB with 16**). At equal threads `table` is 2.2x to 2.4x
faster than DuckDB on G2 on the Mac, 1.3x to 1.4x on gram (2.2x on G1), and 6x to 13x on E1/E2 once the speculation holds.

## 8. The ceiling

* **Per core**, today (final binary): 730 MB/s (Mac, G2), 530 MB/s (gram); DuckDB 330 and 370 MB/s. DuckDB's 3.2 GB/s is
  about 10 of its threads' worth.
* **What a perfect scan buys**, measured as the floor of section 2 (gram; the Mac 1.4x to 1.7x): group-count 1.2x, sum 1.25x,
  select 1.5x, filter 1.9x. The block scan of section 4.4 already takes a third of that.
* **What SIMD structural indexing buys on the scan alone**: 2.3x-2.6x of the kernel (C: 11.0 ms against lex-sys production 25 ms
  on the Mac, 13.3 against 34 on gram), 1.3-1.5x against C's own byte loop. Applied to the row that is the 1.2x-1.9x above at the
  very most, and 1.1x-1.4x in practice (the row would still be 700-900 instructions).
* **So**, at 4 cores G2 stays at about 0.35 s on the Mac (2.8 GB/s); parity with DuckDB's 16 cores (0.34 s) needs ~4.4
  cores, and the Mac gets there at 5-6 (0.27-0.3 s). On gram the cap is 3 physical cores: 0.9-1.0 s for G2, and cold, a floor
  of 0.55 s for the disk.
* **Beyond that** the work is in the rest of the row: the read loop (240-390 instructions a row: 17 `Lines` fields moved by
  reference, `count_row`, a call with 20 arguments per row), `add_fast` + `group_plain` (28% of group-count), and the output
  (`csv_cell`, `emit_buf`: 23% of select). They are the next lever, and each is as big as the whole scan gain above.

## 9. Recommendations

1. **Merge the speculation** (tree A commit 5e71d4d; ported to tree B in this PR, section 11): plain lex-sys, exact by construction, measured on both machines, E1/E2 5-6x at
   8 threads, no cost on other files. Add a bounded "both readings" for files where both readings look right (section 5.4),
   and test the lookalike files in `tests/conformance/test_parallel.py` (`specfiles.py` has the generators).
2. **Size the ranges by mode** (tree A commit 7504327 is the experiment; the production-safe form in this PR is section 11.2): in group mode the slot holds groups, so a range can
   be a share of the file; in row mode keep range-sized slots. Derive the slot from `--max-state-bytes`/`--max-groups`, keep
   `--chunk-bytes` as an explicit override, and shrink the range for the next wave when a range does not fit.
3. **Ask upstream for `byte_mask64`-class primitives** (a 64-byte compare mask for up to three bytes, `trailing_zeros`, a
   checked `load_le64`), and then do the block scan for whole records including the newlines inside quoted fields (section
   4.6). Do **not** spend more on in-language SWAR or byte-loop rewrites: six variants, none better.
4. Do not size `--threads` above the number of physical cores in the documentation or the default advice; on gram 3 is the
   number, and 4 is worse than 3 until there is a work queue.
5. The next lever for the whole row is the loop around the scan (the `Lines` reader and the engine calls), not the scan.

## 10. Files

* `tools/table/{scan,par,engine,table}.cho`: the speculation and the range growth of tree B (this PR). The tree A versions (`.ls`) were on
  the old clone's branch `gap-scan-design` and are not part of this repository.
* `scripts/spikes/scan/`:
  * `kern.ls` the kernels, `c/ceil.c` the C ceiling, `w8.ls`, `gather.ls` the codegen checks, `clang-keep.sh` (`CLANG=`
    wrapper that keeps the `.ll`; compile it with `clang -S -O2 -target ...` to read the assembly);
  * `block-scan.patch`, `compiler-byte-mask64.patch`, `onepass-rec3.patch`, `chunk-size.snippet.ls`: the spikes that are not in
    the tree;
  * `t.py` (timing of the table cells, `-b` binaries interleaved), `final.py` (the comparison with DuckDB/csvtk/Miller),
    `tstat.sh`, `prof.sh`, `ipb.sh`, `cipb.sh` (instructions, cycles and profile on gram), `gbuild.sh` (build on gram),
    `specfiles.py` (hostile files and a differential run), `guessrate.py` (how often the guess is right).
* Rerun: `lex-sys build --ignore-compiler-rev` with the `lex-sys-static-sig` compiler (db7d5bc; newer ones refuse the pinned
  `toolbox` store); `python3 scripts/corpus.py > a; ...; diff` against `main`'s; `python3 scripts/spikes/scan/specfiles.py
  build/table.base build/table`; for the block scan, apply the compiler patch and `git apply block-scan.patch`.

## 11. The port to tree B (`cancho-table` origin/main 7d04b9f, compiler cancho a4572ea) and its gates

What is in the PR: the speculation of section 5 as written for tree A (`scan.guess`, `scan_range`'s `hint`, `engine.k_first_at` = tally slot 27,
the worker's look-ahead in `par.work`, the parent's check on the range's first record), and the range growth of section 6.2 in a production-safe form (11.2).
Nothing else of section 4 or 6: the block scan needs `byte_mask64` and `trailing_zeros`, which the compiler does not have.

### 11.1 The speculation, tests, mutants

* `tests/conformance/test_parallel.py`: `Speculation` (nine files built to make the look-ahead right, wrong or undecided: E1-like long multi-line fields,
  lookalike records inside quoted fields, the same with CR LF, stray quotes, short multi-line fields, ragged rows, a bad quote, an open quote, one column; five plans
  each, among them the type report) at N in {2, 3, 4, 8, 16, 64}, with tiny ranges (1, 7, 64, 1000 bytes) and with ranges of 4000, 30000 bytes and the default;
  `test_e1_e2_files_of_several_ranges` (1500 rows of 0.4-2.5 KB fields, ranges of 64 KiB, 1 MiB and the default); `test_random_quoting_with_ragged_rows`
  (25 random files of pieces that are records, ragged rows, quoted newlines and stray quotes, N in {2, 3, 8}, ranges 1 to 64 bytes: the one that makes the look-ahead say
  "inside" where the truth is "outside" and the other way round); and `test_ranges_that_grow_for_groups_when_chunk_bytes_is_not_given` (a 16 MB file whose keys are few in the first half
  and nearly all different in the second, five plans including the type report, `--agg` with `distinct`, a float sum, N in {2, 3, 4, 8}, with and without `--chunk-bytes`).
* Mutants (`scripts/parallel_mutants.py`, five new, `--check` 32 mutants apply exactly once): the parent comparing the range's start instead of its first record;
  a range reporting its start as its first record; the skipped tail's lines counted; the skipped tail shifting a refusal's line; the tail read as a row.
  **All five killed on the Mac and on gram** (real runs; two of them are killed only by the random-quoting test, which is why it exists: the first three tests left
  three of the five alive). Not mutated, because the output is the same whatever they do (the parent having checked the range), only the time: `plausible`/`guess`, the
  growth (`growing`, `wave_peak`, `wave_bad`, the `explicit` marker). Their effect is what the timings below measure.
* Suites: `tests/conformance` 361 tests OK on the Mac (332 s, load 3-4) and on gram (niced, cores 0-5, 234 s, load below 2) at 1bffeb6, including the parallel,
  numbers, float, float_sum, typed_keys, report, skill, mcp, limits, memory and differential tests; `scripts/corpus.py` 134 plans, md5, status and error identical to
  `origin/main` on both machines.

### 11.2 The range growth, production-safe

Tree A's experiment replaced `--chunk-bytes`' default in group mode by the thread's share and capped the slot at 8 MiB. That is not safe (a slot that is too small makes
the parent read a 64 MiB range again; `--chunk-bytes 4194304` is the default). What is in the PR:

* the **slot is not touched**: `payload_cap` is still `first range + 64 KiB + 64 float states`, so the memory bound (threads x slot) is the one of today. Peak resident set
  of G2: Mac 4 threads 20.3 MB and 16 threads 73.8 MB before and after (73.7); gram 3 threads 7.2 MB and 6 threads 28.1 MB, before and after;
* only **later waves** and only for **groups and the type report** (mode 2) grow the range, and never when `--chunk-bytes` is given: `table.cho` passes
  `cli.has(parsed, table, "chunk-bytes")` (the explicit marker), so an explicit 4194304 is respected;
* the growth is a function of what the first wave's answers measured: the range becomes `first * room`, `room = (slot / 2) / largest answer` (so an answer that grows in
  proportion to the range stays inside half the slot), at most 1024x, at most **16 MiB** (64 MiB made a wave wait for one slow core on gram: G2 at 3 threads 0.82 s old,
  0.91 s with 64 MiB, 0.85 s with 16 MiB, load 4), and at most the thread's share of what is left;
* it **stops for good** and goes back to the first size as soon as a wave had an answer that did not fit, was not clean, or a range that the parent had to read
  again (a wrong guess, a bound): so the worst case of a bad prediction is one re-read of one range of at most 16 MiB;
* nothing about exactness depends on it: a range is still taken only by the parent's checks.

### 11.3 Gates (tree B)

`scripts/gate_regress.py --counter` (instructions retired, bound 1.01, 3 builds per side, the standard cells at 1 and at N threads, N = 4 on the Mac and 3 on gram):

* **sequential cells (13, thread count 1)**: 1.000 to 1.002 on the Mac and on gram (+1 to +4 M instructions of 1500-2400 M: the tally slot, the extra parameter); none moved.
* **parallel cells**: gram **PASS**, 0.993 to 0.997 for the 11 cells that group or filter (and 1.002 for the two `--order-by` cells, which are sequential); the Mac
  0.992 to 0.995 for those, **one cell over the bound: `float min/max by status` at 4 threads, 1.048 (spread 0.7% / 1.9%)**. The cell is a pathology of `main`
  on the Mac that this PR does not cause (the same cell on gram is 12.6 G instructions at 1 and at 3 threads, 0.999): at 4 threads it takes 37 G instructions against 19 G
  at one thread, 1.86 s against 1.24 s of wall time, and 3 s of system time, with `--chunk-bytes 4194304` given (growth off) the new binary is the old one (36-37 G); the
  instruction count of that cell varies by 2-4% from run to run and with the range size on `main` itself (explicit `--chunk-bytes` 8 MiB: 35 G, 16 MiB: 38.6 G, 32 MiB: 38 G).
  In wall time the growth is not slower there (1.67-1.83 s, against 1.87-1.96 s with the explicit size, the Mac loaded). It is reported as it is: the gate does not pass on the Mac
  for that cell, and the cell needs its own look (a follow-up).
  Instruction counts do not measure what the growth is for (fewer waves): the time does.

### 11.4 Timings (tree B), `final.py`, interleaved, minimum of 7 (Mac) and 9 (gram); `old` = `origin/main`, `new` = this PR at 1bffeb6

Mac (load average 3.8 at the start, 4.3 at the end, i.e. not quiet; DuckDB 1.5.x, csvtk, no Miller), seconds:

| cell | threads | old | new | new/old | DuckDB | csvtk |
|---|---:|---:|---:|---:|---:|---:|
| select / filter / group-count / group-sum, 31.7 MB | 1 | 0.043 / 0.040 / 0.049 / 0.054 | 0.042 / 0.041 / 0.048 / 0.055 | 0.98-1.02 | 0.171 / 0.160 / 0.121 / 0.126 | 0.167 / 0.267 / 0.172 / 0.364 |
| G2 1 GB group-count | 1 | 1.568 | 1.616 | 1.03 | 3.139 | 5.767 |
| | 4 | 0.443 | 0.433 | 0.98 | 0.865 | |
| | 8 | 0.260 | 0.236 | 0.91 | (a run that failed: 0.038, discarded; 0.49-0.52 in the earlier run) | |
| | 16 | 0.217 | 0.183 | **0.85** | 0.316 | |
| G1 1 GB filter (not touched) | 1 / 4 / 8 / 16 | 1.360 / 0.423 / 0.242 / 0.186 | 1.306 / 0.411 / 0.238 / 0.186 | 0.96-1.00 | 4.42 / 1.20 / 0.63 / 0.40 | 8.95 |
| E1 long fields select, 114 MB | 1 | 0.154 | 0.152 | 0.99 | 0.348 | 0.157 |
| | 4 | 0.155 | 0.049 | **0.32** | 0.291 | |
| | 8 | 0.157 | 0.031 | **0.20** | 0.283 | |
| | 16 | 0.171 | 0.023 | **0.13** | 0.282 | |
| E2 long fields count | 1 / 4 / 8 / 16 | 0.154 / 0.158 / 0.159 / 0.156 | 0.156 / 0.059 / 0.027 / 0.022 | 1.02 / 0.37 / 0.17 / 0.14 | 0.355 / 0.304 / 0.292 / 0.291 | 0.163 |

An earlier run on the same binaries (load 7-12, the Mac busier): G2 4 / 8 / 16 threads 0.91 / 0.85 / 0.77 of old; E1 4 / 8 / 16 0.31 / 0.24 / 0.16.

gram (cores 0-5, load average 0.2 at the start and 3.5 at the end), seconds:

| cell | threads | old | new | new/old | DuckDB | csvtk | Miller |
|---|---:|---:|---:|---:|---:|---:|---:|
| G2 1 GB group-count | 1 | 1.974 | 2.146 | 1.09 | 2.666 | 10.12 | 7.21 |
| | 3 | 0.995 | 0.996 | 1.00 | 1.272 | | |
| | 6 | 1.168 | 1.040 | 0.89 | 1.266 | | |
| G1 1 GB filter (not touched) | 1 / 3 / 6 | 2.232 / 1.014 / 1.139 | 2.185 / 1.053 / 1.111 | 0.98 / 1.04 / 0.98 | 4.89 / 2.19 / 2.16 | 17.0 |
| E1 long fields select | 1 | 0.167 | 0.165 | 0.99 | 0.379 | 0.207 | 0.210 |
| | 3 | 0.181 | 0.072 | **0.40** | 0.358 | | |
| | 6 | 0.182 | 0.056 | **0.31** | 0.357 | | |
| E2 long fields count | 1 / 3 / 6 | 0.171 / 0.182 / 0.182 | 0.165 / 0.074 / 0.053 | 0.97 / 0.41 / 0.29 | 0.380 / 0.358 / 0.353 | 0.202 | 0.210 |

**What these numbers support, and what they do not.** E1/E2 scale: 2.5x to 3.4x at 3-6 threads on gram (cores 0-5), 3x to 8x at 4-16 on the Mac, and `table`
at the same thread count is 5x to 12x faster than DuckDB on them. The sequential cells do not move (instruction counts 1.000-1.002; the times are within the noise).
The G2 growth is **modest and not established on gram**: on the Mac 0.85x to 0.91x at 8 and 16 threads and no change at 4; on gram 0.89x at 6 threads and 1.00x at 3,
while the controls that this PR does not touch moved by -2% to +4% (G1) and +9% (G2 at one thread, whose code is identical): the box's noise is +-9%. A 3-way run under load 4
(old / 64 MiB cap / 16 MiB cap, 11 runs) was 0.822 / 0.910 / 0.854 s at 3 threads and 1.173 / 1.065 / 1.045 s at 6. Nothing here says G2 at 4 threads is
within reach of DuckDB with 16: on the Mac G2 at 4 threads is 0.43 s, DuckDB at 16 threads 0.32-0.34 s.

### 11.5 What the page must say (for the agent that writes `README.md`, `docs/index.html`, `docs/benchmarks.html`; this PR does not touch them)

* "Long fields with newlines inside (E1/E2: 114 MB, 1-10 KB fields, a newline every ~17 bytes) now scale with `--threads`": Mac 0.154 s at one thread, 0.049 / 0.031 / 0.023 s at
  4 / 8 / 16; gram 0.167 s at one thread, 0.072 s at 3 and 0.056 s at 6 (cores 0-5, 3 physical cores); minimum of 7-9 interleaved runs, the Mac at load average ~4, gram at load 0.2-3.5.
  Say "from 1.0x to 3x-8x", not more. The "Harder cases" note that E1/E2 do not scale is to be replaced.
* "A file built so that the quoted lines are themselves valid records of the same width (and CR LF versions) gets no speed-up and is not slower": that is the stated limit.
* The group-by on the 1 GB file at 16 threads is 0.85x of the time on the Mac (0.217 s to 0.183 s); on gram not shown. Do not write a G2 claim on gram, and do not write that the loss to DuckDB at 4 threads is closed.
* Unchanged: every sequential cell; the standard four questions; G1.
