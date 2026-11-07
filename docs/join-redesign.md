# Redesigning the join: what the pin `119bcfe` changes, and what new join features cost

> **Status: design. Nothing in this document is built.** One spike ran (S9, section 2); every other claim is marked as measured, read from a document, or assumed. It follows the rules of `docs/numbers.md`: decisions with reasons, gates fixed before the build, measured claims separated from assumptions. It extends `docs/joins.md` (sections 7.2, 15, 24) and corrects nothing there that it does not name.

## 0. The short version

| decision | choice | why, in one line |
|---|---|---|
| J8 (partitioned build) as it is | **keep it** until S10 says otherwise | it is built, gated and byte-identical; its rationale (24.2, point 1) is weaker than written, not false |
| the threaded probe (J6) | **still blocked on the compiler**: `119bcfe` refuses all eight S0 negative programs | measured, section 1 |
| a new J6 route, "R1": one job per partition that owns its table and its output | **spike S10 first**, with a kill criterion | it is expressible now (section 2) and nobody has measured it |
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

One thing did change: the S8 negative program `n1_table_into_job` (a worker builds a table and puts it in a job lent by `&!`) **compiles** (the repair of the join spike, cancho-table#112). Its other half, `n2_table_as_result` (the table as the result of `spawn`), is still refused.

## 2. S9: is handing a table back through a job usable? (run, 2026-11-07)

`scripts/spikes/join/s9/handback.cho`: the parent forks a heap, makes a `Job { heap, tab, n }`, lends it by `&!` to one worker that builds a `JTab` of `n` rows with the job's heap and assigns it to `job.tab`; after the join the parent reads the table (`has`, `rows_of`) and drops it with the heap it was built from.

* **Compiles** with `119bcfe`.
* **Correct:** the exit status is `rows_of(tab) - n`, and it is 0 at `n` = 200 and `n` = 1,000,000.
* **valgrind** (`-q`, errors fail the run) reports nothing at `n` = 200. It was not run at 1,000,000.
* **Not measured:** time, resident memory, whether the overwritten empty table is released or leaked by the assignment (valgrind's leak summary was not read at a size that would show a leak), more than one worker, a table with the pruned columns of J7. None of this says the hand-back is *faster* than J8's copy; it says it is possible.

## 3. What that opens, and what it does not

**It does not remove J8's assembly by itself.** The probe loops take one `JTab` (24.2, point 1). If each partition thread hands back its own table, the parent holds `P` tables, and probing `P` tables means either copies of the probe loops (about 850 lines, rejected in 24.2) or building one table from the parts, which is what J8 does with integers and bytes. The hand-back would replace the *encoding* of the copy, not the copy. Whether it is cheaper is an assumption.

**It does make option (c) of 7.2 expressible** (a partitioned join): worker `k` is lent a `Job_k` that owns partition `k` of the build *and* its own output box. That is the case `s0_copy.cho` already showed works; what was missing was a way to make the partition tables without copying, and S9 is that.

### R1, the design to test

1. J8's build threads leave each partition's table in their job (S9), where today they hand back integers and bytes.
2. Worker `k` keeps its job. It reads the probe file in ranges (the speculative range scan of `docs/parallel.md`), takes **only the probe rows whose key hashes to `k`**, probes its own table and writes `(probe row number, joined record)` into its output box.
3. The parent merges the `P` boxes by probe row number. The output is the sequential output: same bytes at every thread count, refusals included (the J6 gates of `docs/joins.md` section 10, TH).

The cost nobody has measured: **every worker parses every probe row's key** (`P` times the key-reading work), and the merge is a `P`-way merge by row number.

### S10, the spike, and its kill criterion

Build R1 as a spike on the 1,000,000-row build and 4,000,000-key probe of S0, at 2, 4 and 8 threads, checking every answer against the sequential probe (as `s0.py` does).

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

1. **F0** (the swap repair for `right`, a test and a line in `docs/refusals.md`), **S10** (a spike, no change to the tool). Independent.
2. **F1** (`full`), after a one-page design in this file's style: the formula, the order, the gates (every type's existing answers unchanged; `full` equals a `left` join followed by the unmatched build rows, computed independently in Python; the thread counts of J8).
3. **F2** after `--out` exists.
4. **R1 as J6b** only if S10 passes its criterion, with the TH gates.

## 6. What was measured, and what was not

* **Measured:** the eight S0 refusals and the S8/S9 results above, on `119bcfe` (x86_64 Linux, 4 cores). The earlier numbers quoted (24.4, 15.2) are from their sections and machines.
* **Not measured:** everything about R1's speed and memory; S9's time and memory; the leak behaviour of the overwrite; `full`'s cost. The 1.5 and 2 of section 3 are thresholds chosen before the spike, so that the spike cannot move them.
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
