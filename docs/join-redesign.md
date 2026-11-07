# Redesigning the join: what the pin `119bcfe` changes, and what new join features cost

> **Status: design, two spikes run (S9, section 2, which rests on a gap of the default compiler and is corrected there; S10, section 8, which decided against R1). Nothing is built except F0a (section 7.2).** Every claim is marked as measured, read from a document, or assumed. It follows the rules of `docs/numbers.md`: decisions with their reasons, gates fixed before the build, measured claims separated from assumptions. It extends `docs/joins.md` (sections 7.2, 15, 24). **[corrected 2026-11-07]** The first version of this document said a worker can hand a table back through a job on `119bcfe`; that was wrong (section 1).

## 0. The short version

| decision | choice | why, in one line |
|---|---|---|
| J8 (partitioned build) as it is | **keep it** | it is built, gated and byte-identical, and its rationale (24.2, point 1) stands: a table cannot be handed back |
| the threaded probe (J6) | **still blocked on the compiler**: `119bcfe` refuses all eight S0 negative programs | measured, section 1 |
| a new J6 route, "R1": one job per partition that owns its table and its output | **not kept**: S10 measured 1.42 and 1.46 times at 4 partitions (two runs) on a 1,000,000 x 4,000,000 join, under the 1.5 fixed in advance (section 8) | measured, section 8 |
| `right` and `full` | **`full` yes (F1), `right` stays a refusal with the swap repair** | `full` needs a bit per build row and a second phase; `right` is a left join with the files swapped |
| a join of more than two files | **no**; pipe, with `--out` (cancho-table#108) | one plan per call is the rule |
| `--report` over a join | **keep the refusal, make its repair the two-step command** | section 4 |

## 1. What the new compiler changes, measured (2026-11-07, `119bcfe` against `e47d647`)

The eight negative programs of `scripts/spikes/join/s0/neg/` were built with `119bcfe`. **All eight are still refused**:

| program | what it tries | `119bcfe` says (rule) |
|---|---|---|
| `n1_ref_field` | a job struct with a region parameter, `Job[&f]` | `region-mismatch` at the declaration (the text was: a type declaration has no region parameters) |
| `n2_ref_field_no_param` | the same without declaring the region | `region-not-in-scope` |
| `n3_tuple_payload` | `spawn((sh, job), f)` | `thread-payload-type` |
| `n4_write_through_shared` | a write through a shared reference | `shared-reference-written` |
| `n5`, `n8` | moving or copying a `res` table out of a reference | `type-mismatch` |
| `n6_slice_payload` | a slice as the payload | `thread-payload-type` |
| `n7_two_unique_one_table` | two workers on one `&!` `borrow-conflict` |

`docs/thread-payloads.md` in the compiler checkout still says T-P1 (tuple payloads) is a design. So **J6 as option (b) of section 7.2 (a shared table and a worker's own output in one payload) is not buildable on `119bcfe`**, and the request drafted in `docs/joins.md` 15.3 (never filed) is still what unblocks it. The first line of `n1`'s message changed ; whether `Job[&f]` is nearer to being accepted is a question for cancho, not something this document measures.

**[corrected] What did not change, and a gap of the default compiler.** The S8 negative program `n1_table_into_job` (a worker builds a table and puts it in a job lent by `&!`) compiles with the **default** compiler of `119bcfe`, the one written in cancho (`docs/bootstrap.md` of the compiler checkout). The **Rust compiler** (`CANCHO_COMPILER=rust`, the reference checks) refuses it with the message it always gave, `the value overwritten here is res (JTab), so it cannot be discarded`, and so does it refuse the S9 program of section 2. So the default compiler lets through a program the language refuses: that is a gap of that compiler, not a new capability, and this document's first version (and `docs/joins.md` 24.2, as first corrected in cancho-table#112) took it for one. The eight S0 negative programs are refused by both compilers. `n2_table_as_result` is refused by both (the sentence differs). The gate `test_join_j8.Premise` now asks the Rust compiler.

## 2. S9: is handing a table back through a job usable? (run, 2026-11-07; **its premise is false, see section 1**)

`scripts/spikes/join/s9/handback.cho`: the parent forks a heap, makes a `Job { heap, tab, n }`, lends it by `&!` to one worker that builds a `JTab` of `n` rows with the job's heap and assigns it to `job.tab`; after the join the parent reads the table (`has`, `rows_of`) and drops it with the heap it was built from.

* **Compiles** with the default compiler of `119bcfe` only. **The Rust compiler refuses it** (`the value overwritten here is res (JTab), so it cannot be discarded`), so the program is not legal and nothing below is a basis for a design.
* **Correct:** the exit status is `rows_of(tab) - n`, and it is 0 at `n` = 200 and `n` = 1,000,000.
* **valgrind** (`-q`, errors fail the run) reported nothing at `n` = 200. It was not run at 1,000,000. It says what the default compiler's code does with the overwrite, not that the overwrite is allowed.
* **Not measured:** time, resident memory, whether the overwritten empty table is released or leaked by the assignment (valgrind's leak summary was not read at a size that would show a leak), more than one worker, a table with the pruned columns of J7. None of this says the hand-back is *faster* than J8's copy; it says it is possible.

## 3. What that opens, and what it does not

**It does not remove J8's assembly by itself.** The probe loops take one `JTab` (24.2, point 1). If each partition thread hands back its own table, the parent holds `P` tables, and probing `P` tables means either copies of the probe loops (about 850 lines, rejected in 24.2) or building one table from the parts, which is what J8 does with integers and bytes. The hand-back would replace the *encoding* of the copy, not the copy. Whether it is cheaper is an assumption.

**Option (c) of 7.2 (a partitioned join) was expressible before the pin and is now measured (section 8).** Worker `k` is lent a `Job_k` that owns its share of the keys and its own output box, builds the table of its partition in a **local variable**, probes it and drops it; only the build row each probe key found leaves the worker, in the box. That is the shape `s0_copy.cho` showed works, and it needs no hand-back of a table (S9 is not needed and is not legal).

### R1, the design to test

1. Each worker builds the table of its partition, from the keys the parent routed to it, in a local variable (where J8's build threads hand back integers and bytes to be put together into one table).
2. Worker `k` probes its own table with the probe keys the parent routed to it and writes the build row each found into its output box. (A worker cannot read every probe key itself: a shared reference and the worker's own state do not fit in one payload, `docs/joins.md` 15.2. So the parent routes the probe keys, as it routes the build keys, with a counting sort by the hash of the key.)
3. The parent puts the `P` outputs back in probe order. The output must be the sequential output: same bytes at every thread count, refusals included (the J6 gates of `docs/joins.md` section 10, TH).

The cost to measure: **the parent's routing of both key sets** (sequential: it is not shared by the workers), the copy of each share to its worker, and the merge.

### S10, the spike, and its kill criterion

Build R1 as a spike on the 1,000,000-row build and 4,000,000-key probe of S0, at 2, 4 and 8 threads, checking every answer against the sequential probe (as `s0.py` does). **Run: section 8.**

* **Keep R1** only if at 4 threads it is at least 1.5 times faster than the sequential probe, with a resident set no more than 2 times J8's at the same size (24.4 measured 1.8 to 2.7 times the sequential build).
* **Kill it** otherwise, record the numbers in `docs/joins.md` 24 (corrected in place), and let J6 wait for the compiler (the request of 15.3).

The 1.5 and 2 are choices, not measurements. S0 measured the *shared* read at 2.9 times at 4 threads on this probe; R1 gives that up for the routing, which is why it may not pay.

## 4. New join features

| feature | design | cost and risk | slice |
|---|---|---|---|
| **`full` join** | `--join-type full`: the left join's rows in probe order, then each build row that matched nothing, in build order, probe cells empty. A bit per build row, set when matched; a second pass after the probe | one bit per held build row, added to the formula of 6.2 and so to `--max-state-bytes`; the bytes of every other type must not change (the corpus md5 of J1 to J9); `--join-sorted` first refuses it (the merge could do it, as a later slice); `--query` gets `full join` with its two aliases | F1 |
| **`right` join** | stays `query.unsupported`/`args.bad-value`; its repair is the swapped `left` join, **with a note that the output order and the column order differ** | none; check that the repair is offered for flags as well as for a query | F0 |
| **more than two files** | no. `--out FILE` (cancho-table#108) makes the two-step pipeline a single command, which keeps one plan per call | a plan over three files needs a join order and a bound per step | |
| **`--report` over a join** | the refusal stays (`args.conflict`, 8.5); its repair offers `table <join flags> --format csv --out T` then `table --report types T` | a repair that writes a file needs `--out` | F2 |
| **a threaded probe (J6)** | R1 if S10 keeps it, else waits for the compiler | section 3 | after S10 |
| **a grace (external-memory) join** | not now. `table` has no write capability beyond the planned `--out`, and 9 keeps `--join-sorted` as the way past memory | a spill is a different tool | |
| **the SQL view (cancho-table#104)** | the `sql` field of `--explain` must write the join as the `--query` join form it already has | none, but it is a gate of that issue | |

## 5. Order of work

1. **F0** (done as F0a, section 7.2), **S10** (done, section 8: R1 not kept). Independent.
2. **F1** (`full`), after a one-page design in this file's style: the formula, the order, the gates (every type's existing answers unchanged; `full` equals a `left` join followed by the unmatched build rows, computed independently in Python; the thread counts of J8).
3. **F2** after `--out` exists.
4. ~~R1 as J6b~~: S10 did not pass its criterion (section 8). The threaded probe waits for the compiler.

## 6. What was measured, and what was not

* **Measured:** the eight S0 refusals (by both compilers), the S8/S9 results (by both compilers: section 1) and S10 (section 8), on `119bcfe` (x86_64 Linux, 4 cores). The earlier numbers quoted (24.4, 15.2) are from their sections and machines.
* **Not measured:** R1 past 4 cores or with a real csv read; `full`'s cost. The 1.5 and 2 of section 3 were thresholds chosen before the spike, so that the spike could not move them.
* **Not decided here:** whether to file the request of 15.3 (it needs the maintainer's yes), and whether `full` is wanted at all; nothing in the issues asks for it.

## 7. F0 in detail: the repair for `right` (written 2026-11-07, before any code)

**What happens today (measured on the built tool, pin `119bcfe`).** `--join-type right` is `args.bad-value` (`detail.expected`: `inner/left/semi/anti`, `repair: null`); `full` is refused the same way. A `--query` with `right join` is `query.unsupported`, feature `right join`, hint "swap the two files and write left join", and `repair: {"kind":"none"}`. So the swap is told in words and never offered as a call. Section 4 called F0 small; it is not quite: the swap is only the same question when the call has none of the features below, which is the list the existing swap repair of the build bound (`tools/table/jnswap.cho`, `code_of`) already keeps. F0 reuses its pieces; it does not copy them.

**What "the right join" means here.** The rows of `A right join B on k` are the rows of `B left join A on k` with the columns of `A` first again: for each row of `B`, in `B`'s order, the matching rows of `A` in `A`'s order, and a row of `B` with no match once, with the cells of `A` empty. This is the definition of docs/joins.md 2.2, whose row order is `B`'s. The repair is exactly that call: the operand and the `--join` file exchange places, each pair of `--on` is read the other way, the reader flags of the two files go with their files, and a `--select` with the original joined header keeps the columns in the original order (`jnswap.exchanged` does all of this for the inner swap).

**When the repair is offered, and when nothing is** (each "nothing" has a code and a sentence in the hint, as `jnswap.reason_of` has):

| condition of the call | offered? | why |
|---|---|---|
| `--join-type right` or `right join` in a `--query` | yes, if the rest holds | the case |
| `--join-prefix` | no | it names the `--join` file's columns, which would then be the other file's |
| `--join-where` | no | in a right join it would be a condition on the preserved side; in the swapped left join it filters the probe: a different question |
| a column named by position (`#N`) | no | the positions move |
| a typed shared-name key | no | it shows the probe's spelling of the key |
| a name twice or none in the joined header | no | the columns cannot be kept in order by name |
| a csv answer whose delimiter would change | no | as for the inner swap |
| standard input as the probe | no | it cannot be held as the build side |
| the probe file larger than `--max-state-bytes` allows as a build | no | an offer the next run refuses for the bound does not repair |
| `--limit`, `--top`, `--from` | yes, with the order in the hint | there is no earlier right-join order to keep: the order is `B`'s by definition |
| a `--query` | no argv is rewritten, but the hint carries the swapped query; the `repair` of a query refusal stays the flag form, as docs/query.md section 1 says | |

Not required, unlike the inner swap: that the probe be the smaller file. The inner swap is offered only to relieve the bound; here the swap is the whole point, and the bound check above covers the cost.

**Gates (to be written before the code).** (1) For random tables with duplicate, empty and unmatched keys, the rows of the repair's argv, run, equal the right join computed independently in Python (`rows of B left join A`, columns of `A` first), for text and typed keys. (2) Each "no" row of the table has a test that the refusal still has `repair: null` and a hint that names the reason. (3) The refusal of the flag form and of the query form keep their rule tags and exit status 2. (4) `docs/refusals.md` and `docs/joins.md` 2.2 say what the refusal now offers. (5) Mutants: key pair not reversed, reader flags not exchanged, header not restored, `--join-where` not refused.

**Not decided, and cheap to decide wrongly:** whether `full` should get the same treatment (it cannot: a full join is not a swap of anything; F1 builds it).

### 7.1 What reading the code for the build found (2026-11-07) — F0 is not a small change, and the design above is not yet buildable as one slice

**[corrected]** Section 7 treated the repair as the exchange of `jnswap` offered one step earlier. Reading the code (nothing was built or changed) says otherwise:

1. **The flag form's refusal is not table's.** `--join-type right` is `args.bad-value` raised by the generic check of a word flag (the kind is in `tools/table/spec.cho`; the hint "give the flag a value of the kind detail.expected names" is the contract package's, cancho-tools'), before any file is opened. Tests pin its `detail.expected` (`inner/left/semi/anti`: `test_join.py`, `test_join_j2.py`). A different hint or a repair for one value needs either an interception in `table` before that check, or a change in cancho-tools.
2. **The repair needs both headers.** `jnswap.exchanged` restores the original column order with a `--select` of the joined header, so it needs the header of the `--join` file as well as the operand's. Those are read inside the 1,059-line run loop of `tools/table/jnrun.cho` (the existing offer is built at its `bc.abort == 70` branch, with about twenty values in scope), after the flags are validated. A right-join refusal that offers the call would therefore be raised *after* opening and reading two headers, and it would have to carry a rule: `args.bad-value` is raised before, so the tag would change or the check would move. Either breaks the claim of gate (3) that the rule tags stay.
3. **The same for a `--query`.** `query.unsupported` is raised by the parser at the byte of `right`, before the files are known; the repair would again need the headers.

**What this changes.** F0 is two slices, not one: **F0a**, decide where the refusal is raised (a pre-check in `table` that opens the headers only when the word is `right`, with a new rule, say `join.right-unsupported`, and a hint that is the prose of this section; the old tags `args.bad-value` and `query.unsupported` stay for every other value), and **F0b**, the repair argv on top of it, with the gates of section 7. F0a is also the home of the `full` refusal ("no full join yet", issue #113). Neither is built; the cost of F0b was not estimated beyond "a new module the size of `jnswap` plus a call site in `jnrun`", and I have not decided that it is worth that: a person who reads "swap the two files and write left join" (the hint a query already gets) can do it, which is the case for stopping at F0a.

### 7.2 F0a, built (2026-11-07)

`--join-type right` and `--join-type full` (and the `=` spelling) are refused as **`join.type-unsupported`** (exit 2, `repair: none`, `detail.flag`, `detail.value`, `detail.expected`), with one hint that says how to write a right join (a left join with the two files exchanged, the rows in the other file's order and its columns first, `--select` to keep the old column order) and that a full join is not built (issue #113). Any other word (`outer`, `cross`, `LEFT`, empty) keeps `args.bad-value`; a `--query` keeps `query.unsupported`; `introspect` still says the kind is `choice:inner/left/semi/anti`.

**How it is raised.** The generic check of a word flag runs first and does not keep a flag whose value it refused, so `run` (tools/table/table.cho) reads the arguments again, up to `--`, but only when the parse already refused something, and replaces that parse's errors with this one. **A consequence, decided here:** when `--join-type right` comes with another refused flag, only this refusal is reported; the other is reported by the next call. The rule is in `finish.extra()`, in the tool's `rules:` list, in `docs/refusals.md` (generated), and has a fixture in `test_rules.py` and a test in `test_join_j2.Flags`.

**Not done:** F0b, the repair argv, and the same hint for a `--query` (its `query.unsupported` hint already says to swap the files).

## 8. S10, run (2026-11-07): R1 is not kept

`scripts/spikes/join/s10/partition.cho`, driver `scripts/spikes/join/s10.py`, results `scripts/spikes/join/results/s10.txt` and `.json`. Built with the **Rust compiler** (`CANCHO_COMPILER=rust`; the default compiler also builds it and gives the same answers). Machine: `Linux-6.18.44-fc-v310-x86_64`, **4 cores**, load average 0.05 before and 1.1 after (a quiet machine, but a small one). Keys of 16 bytes, a record that is the key; the probe asks for `(p * 7 + 3) mod 2n`, so a bit under half of the keys are in the build. Each cell is the minimum of 3 processes of the minimum of 5 repetitions; milliseconds; resident set in MB from `wait4`, of the sequential join alone and of R1 alone. Every answer of R1 equalled the sequential one (the `ok` column: all repetitions).

| build x probe | parts | sequential build + probe | route | par | merge | R1 total | speed-up | rss sequential | rss R1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100,000 x 400,000 | 1 | 11 + 12 = 23 | 6 | 36 | 0 | 44 | 0.52 | 23 | 41 |
|  | 2 | 11 + 12 = 23 | 5 | 21 | 0 | 27 | 0.85 | 23 | 40 |
|  | 4 | 10 + 11 = 21 | 5 | 12 | 0 | 19 | 1.11 | 23 | 41 |
|  | 8 | 12 + 12 = 24 | 5 | 10 | 0 | 16 | 1.50 | 23 | 32 |
| 1,000,000 x 4,000,000 | 1 | 233 + 227 = 460 | 87 | 588 | 5 | 695 | 0.66 | 219 | 375 |
|  | 2 | 229 + 247 = 476 | 85 | 311 | 5 | 420 | 1.13 | 219 | 368 |
|  | **4** | 219 + 242 = 461 | 81 | 221 | 6 | **316** | **1.46** | 219 | 348 |
|  | 8 | 211 + 209 = 420 | 76 | 182 | 5 | 264 | 1.59 | 219 | 317 |

**Verdict against the criterion fixed in section 3: not met, by a margin the runs cannot settle.** At 4 partitions on the large join R1 is **1.46 times** the sequential join (build and probe together; against the probe alone it is slower: 316 ms against 242), under the 1.5 chosen in advance. An earlier run of the same program and the same machine, with the child processes started another way, gave 1.42 at 4 partitions (and 1.39 at 8); this run gives 1.59 at 8 partitions, which on 4 cores is oversubscribed and is not the case the criterion names. So the two runs differ by about 0.04 at 4 partitions and by 0.2 at 8: the distance to 1.5 (0.04 to 0.08) is within what the measurement varies by. Its resident set is 348 MB against 219 MB for the sequential join, **1.59 times**, within the 2 times; that comparison is with the sequential one-thread join of the same spike, since J8's resident set was not measured at this size (24.4 measured another shape). The small join gives 1.11 at 4 partitions and misses too. So R1 is **not kept** on the rule fixed in advance, which is what a pre-registered threshold is for; it is a close call, not a clear failure, and the threaded probe waits for the compiler (the request of `docs/joins.md` 15.3, unfiled, needs the maintainer's yes).

**Why, as far as the numbers say.** One partition costs 695 ms against 460: the routing (about 80 to 90 ms, sequential, and it grows with the keys), the copy of each share to its worker and the second table's construction are paid whatever the number of partitions. The parallel part scales (588, 311, 221, 182 ms at 1, 2, 4, 8) but not by the number of workers, and on 4 cores 8 partitions cannot do better than 4 (the gain at 8 is the noise of the runs, or the extra workers hiding stalls: not told apart here). What the spike did not model makes R1 look better than it would be in the tool: the keys are generated, not read and split out of csv, and the record is the key; a real join also reads quoted fields and writes the joined rows, which R1 would have to put back in probe order (the merge here puts back integers). A 16-core machine was not available, so how R1 scales past 4 cores is not measured. The resident set was taken with the small launcher of `tests/conformance/maxrss.c`, as the other scripts do; a first version of the driver forked from Python and so started from Python's own mark, which only the small cells could show.

**What this leaves.** J8 stays as it is. `docs/joins.md` 15.2 stays the account of why a threaded probe needs the compiler: a shared read of the table gave 2.9 times at 4 threads on the probe alone (S0, another machine, an older `jtab`); R1 gives that up for the routing and does not reach the bar. F0a, F1 and F2 do not depend on it.

## 9. F1: the `full` join, designed before any code (2026-11-07)

Issue #113. This section is the design the issue asks for; nothing in it is built. Every rule below is taken from `docs/joins.md` (sections cited) and from SQL's meaning of `FULL JOIN`, and each place where `full` differs from `left` is a decision with its reason. **No cost of `full` has been measured**; the one number that follows from the formulas is the byte per held row of 9.3.

### 9.1 What it answers

`--join-type full` (and, in a query, `from 'a.csv' as a full join 'b.csv' as b on a.k = b.k`): the rows of the `left` join, in probe order, **then** each held build row that no probe row matched, in build file order, with every probe cell empty. The header is the `left` join's (the probe columns, then the build columns). Matching is the existing one (text or typed keys, up to eight pairs, 5.1 to 5.4 of `docs/joins.md`).

### 9.2 Where `full` differs from `left` (each a decision)

| point | decision | why |
|---|---|---|
| `--where` pushdown (8.1, refinement 1) | **none for `full`**: the whole `--where` is evaluated on the joined row, probe-side leading conditions included | a probe row removed before its key is read would never mark its build rows as matched, and they would come back in the second phase as unmatched rows: a wrong answer, not a slower one. `--explain` says `where.probe` and `where.build` are empty for `full` |
| `--join-where` | **refused** with `full` (a rule and a hint: write the condition in `--where`, or use `left`) | in SQL the `ON` condition of a full join keeps the build rows it rejects, as unmatched, padded; `--join-where` drops them before they are held (8.1, refinement 2). Offering the second meaning under the first name is the trap this tool exists to refuse |
| the shared-name key (`--on k`) | the one key column of the answer is `coalesce(probe key, build key)`: the probe's spelling for a matched row, the build's for an unmatched build row | the column cannot be empty for a row that has a key; for a typed shared-name key the spelling shown for a build-only row is the build file's (5.2 shows the probe's, which is empty here) |
| padded cells | **both sides can be padded**: the build part of a probe-only row and the probe part of a build-only row. A typed condition or aggregate over a padded cell refuses as before (`value.not-integer` and the others) with `detail.padded: true`, and for a row of the second phase `detail.input: "build"` and the build row's number and line (6.4) | no null, so the padded cell is the empty text; the guard `col != '' and ...` is the repair a person is taught |
| `--max-join-rows` | counts the rows of both phases | it bounds the rows produced (6.5) |
| `--limit`, `--from`, `--top` | the page is a window over the sequence "phase 1 rows, then phase 2 rows"; `next.from` counts joined rows over both phases | one meaning of `--from` (8.2) |
| phase 2 is lazy | when the page is full before the probe ends, **phase 2 never runs** and the matched marks are never completed | nothing in the page depends on them; this keeps a paged `full` as cheap as a paged `left` |
| `--join-sorted` | refused with `full` (`args.conflict`) at first | the merge produces key order, and the unmatched rows of both files interleave in it: a different contract, a later slice |
| standard input as the probe | allowed | phase 2 starts at its end |
| threads | one core, as every join (7.2); `--explain` says why | nothing new |
| `semi` and `anti` | not combined (they are types) | |

### 9.3 State and the bound

`full` holds the build rows (as `inner` and `left` do, 6.2) and **one byte per held row** for the matched mark, allocated as the rows are held:

```
accounted = sum over held rows (record_bytes + 17)  +  sum over distinct keys (key_bytes + 60)       (full only; inner and left keep 16)
```

A byte rather than a bit is a choice for a growth rule that is the existing one (a box that doubles) and for a gate that is one line; a bit would cost 1/8 of that and was not weighed further. The bound is checked at the first held row that would pass it, as 6.3 says, so the threshold stays one byte at every thread count. `--explain`'s upper bound on the build gains the same 1 per possible row. **The refusal `limit.state-too-large` for a `full` build offers no swap** (the swap repair is for `inner` only, 6.3).

### 9.4 The surface that changes

* `--join-type`'s kind becomes `choice:inner/left/semi/anti/full`: `introspect`, the MCP schema and the skill (all generated) change, and so do the tests that pin the four words (`test_join.py` and `test_join_j2.py`, 12 lines in all).
* `join.type-unsupported` (F0a) keeps `right` and loses `full`; its hint loses the sentence about `full`; the rule's summary in `finish.extra()`, `docs/refusals.md` and the fixture of `test_rules.py` follow. A `--query` with `full join` stops being `query.unsupported` (feature `full join`); `full outer join` keeps being refused (the feature `outer join`), as `left outer join` is.
* One new rule, say `join.where-with-full` (exit 2, no repair, hint as in 9.2), for `--join-where` or a query `on` condition on the second table beside `full`; and `args.conflict` for `--join-sorted`.

### 9.5 Staging, each slice with its gates

| slice | what | gates beyond the common ones |
|---|---|---|
| F1a | flag form, rows out, text keys, no `--group`/`--order-by`, the refusals of 9.2 | the oracle below; the matched mark under mutants |
| F1b | `--group`, `--agg`, `--sort`, `--order-by`, `--top`, paging | the same bytes as the same question on the csv of the oracle's rows, piped into a second call |
| F1c | typed keys, composite keys, the shared-name coalesce | the oracle with typed and empty keys; `1.0` against `1` as keys |
| F1d | the query form | flags and query give the same bytes; `--explain` agrees (X1 of `docs/query.md`) |

**Common gates (written before the code).** (1) Every existing join type's answers are unchanged: the corpus md5 of J1 to J9 and `scripts/query_corpus.py --require f1`. (2) **The oracle**: `full` equals the `left` join followed by the build rows that matched nothing, computed independently in Python (dict of lists, no use of the tool), on random tables with duplicate, empty, unmatched and quoted-newline keys, for text and typed keys, and each probe row's matches in build order. (3) The same bytes at every `--threads` and `--chunk-bytes` already gated for joins. (4) The bound is exact to one byte (accepted at N, refused at N - 1) with the extra byte per row. (5) A page that ends inside phase 1 never reads the rest of the build file past what the build needs, and a page that reaches phase 2 equals the corresponding window of the unpaged answer. (6) Every refusal has a rule and a hint; no input reaches a panic.

**Mutants (each one defect, each must be killed).** The mark never set; always set; set only for the first row of a chain; set for a build row of the wrong key; cleared when a refusal is retried; phase 2 in reverse order; phase 2 omitted when the probe is empty; the probe-side pushdown left on; `--join-where` accepted; the shared key not coalesced; padded cells counted in the wrong side; the extra byte not counted; `--max-join-rows` not counting phase 2.

### 9.6 What this design does not settle

* Whether `full` is worth its second pass over the held rows for the people who ask: it was asked for by the maintainer (issue #113) and by nothing in the evaluation of #100/#101.
* How long the build of `F1a` is: the closest measured thing is J7's build change (22.6), a few hundred lines in `jnrun.cho` and `jnread.cho`; that is a comparison, not an estimate.
* A bit instead of a byte (9.3), and `full outer join` as a spelling.
