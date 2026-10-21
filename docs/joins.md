# Joins: a design (backlog item 9)

> **Status: design only. No tool code is written or changed by the pull request that adds this file.** It follows the rules of
> `docs/numbers.md`, `docs/readers.md` and `docs/query.md`: every decision has its argument, every number is marked as
> *measured* (with where and how), *computed* (arithmetic on a stated formula) or *estimate/choice* (a judgement), the gates are
> fixed before the build, and what was not measured is listed (section 14). The baseline of section 12 was taken on
> 2026-10-14 on one Mac, **loaded by other work** (load average 10 to 14 on 16 cores, from other sessions), which makes every
> time in it an upper-ish figure for each contender alike. The Linux box (`gram`) was down and was not used: every gram
> measurement is listed as NOT RUN.
>
> **Update 2026-10-15 (the pull request that adds section 15): the spikes S0 and S1 were run; their results are section 15, and where they correct
> this document the sentence says "Corrected by section 15" in place.** Still no tool code: the spikes are throwaway programs under
> `scripts/spikes/join/`.
>
> **Update 2026-10-15 (the pull request that adds section 16): slice J1 is built** (`--join`, `--on`, `--join-type inner`, `--join-prefix`, `--max-join-rows`, rows out, one core), and section 16 says what it is, where it differs from this document, its gates and its numbers.
>
> **Update 2026-10-15 (the pull request that adds section 17): slice J2 is built** (`--join-type left|semi|anti`, typed keys `:int`, `:dec(S)`, `:float`, a list of up to 8 pairs, the shared-name key `--on NAME`, the padding of a left join), and section 17 says what it is, where it differs from this document, its gates and its numbers. Where it corrects an earlier sentence the sentence says "Corrected by section 17" in place.
>
> **Update 2026-10-15 (the pull request that adds section 18): slice J3 is built** (`--join-where`, the split of `--where` into probe, build and after, `--group`, `--agg`, `--sort`, `--order-by`, `--top`, `--limit`, `--from` and `--rename` over the joined row with one `--max-state-bytes` for the build and the groups or the sorted rows, the guard of an empty typed key, `--explain`), and section 18 says what it is, where it differs from this document (every narrow reading, numbered), its gates and its numbers. Where it corrects an earlier sentence the sentence says "Corrected by section 18" in place.
>
> **Update 2026-10-16 (the pull request that adds sections 19 and 20): section 19 is the staging table of section 11 against what `main` does, measured by running the binary; slice J3b is built** (the join in `--query`: `from 'a.csv' as a [inner|left|semi|anti] join 'b.csv' as b on a.k = b.k [and b.x > 1]`, every column written `alias.column`, translated to the flags of J1 to J3 and to nothing else), and section 20 says what it is, where it differs from this document (every narrow reading, numbered), its gates and its numbers. Where it corrects an earlier sentence the sentence says "Corrected by section 20" in place.
>
> **Update 2026-10-16 (the pull request that adds section 21): slice J5 is built** (JSON lines on the probe, on the build or on both: `--input-format jsonl --fields PATHS` for FILE, `--join-input-format jsonl --join-fields PATHS` for the `--join` file, each defaulting to FILE's; and `--join-delimiter` for a csv `--join` file with a delimiter of its own), and section 21 says what it is, where it differs from this document (every narrow reading, numbered), its gates and its numbers. Where it corrects an earlier sentence the sentence says "Corrected by section 21" in place.
>
> **Update 2026-10-20 (the pull request that adds section 23): slice J9 is built** (`--join-sorted`, the merge join over two files the user declares sorted by the key: the state is the largest group of equal build keys, the output is key order, the first row out of order is `join.not-sorted`), and section 23 says what it is, where it differs from this document (every narrow reading, numbered), its gates and its numbers. Where it corrects an earlier sentence the sentence says "Corrected by section 23" in place.
>
> **Update 2026-10-19 (the pull request that adds section 22): slice J7 is built** (the swap repair of `limit.state-too-large`, and the build that keeps only the columns the plan names), and section 22 says what it is, where it differs from this document (every narrow reading, numbered), its gates and its numbers. Where it corrects an earlier sentence the sentence says "Corrected by section 22" in place.
>
> References to code are to `origin/main` at `072ce15` (`file:line`). The branch that adds columns renaming
> (`docs/or-and-columns.md` in the request) **does not exist**: there is no such file and `gh pr list` shows no open pull
> request on 2026-10-14, so section 4 defines the minimal prefixing rule itself and says what a rename feature would replace.

## 0. The short version

| question | decision | why, in one line |
|---|---|---|
| flags | `--join FILE --on KEYS [--join-type inner\|left\|semi\|anti] [--join-prefix P] [--join-where EXPR] [--max-join-rows N]`; later `--join-delimiter`, `--join-input-format`, `--join-fields` | explicit words, no inference; each is one thing (section 2) |
| `--query` | `select ... from 'a.csv' as a [inner\|left\|semi\|anti] join 'b.csv' as b on a.k = b.k [and b.x > 1] ...`, translated to the flags; every column of a join query is qualified `alias.column` | the translator stays a pure text-to-flags step that knows no header (section 2.3) |
| types of join | **inner, left, semi, anti** in v1; no right, full, cross | `table` has no `null`, so "left rows with no match" can only be asked with `anti`; right/full need match-tracking on the build side (section 2.2) |
| how many joins | **one**; a second is `query.unsupported`, and the answer is a pipe: the first join's CSV is the second's standard input | standard input as the probe side makes chains bounded and byte-exact (section 2.4) |
| inputs | two inputs, **the build side is always the `--join` file**; the probe side is the operand (file) **or standard input**; `--join -` is refused | the user chooses the build side by choosing which file to name in `--join`; stdin can only be streamed (section 3) |
| formats | csv/tsv on both sides in the first slices; jsonl per side in a later slice; the delimiter and format of the build side default to the probe side's | one loop per format over the same engine (section 3.2) |
| the output row | the probe row's cells **then** the build row's cells; **no column is ever dropped or renamed silently**: equal names are `join.column-collision` unless `--join-prefix` is given or `--on` is the shared-name form (USING) | no guessing, no hidden rename (section 4) |
| key equality | text: **the exact bytes** of the cell; typed (`:int`, `:dec(S)`, `:float`): **the value**, as `--group` already reads it (`007` = `7`, `-0` = `0`, `1.5` = `1.50`); **an empty cell is a key like any other** for text (it matches an empty cell) and **a refusal** for a typed key | `table` has no null (`docs/query.md:81`); typed reads already refuse empty cells (`docs/filter.md:88`); the decision is an open question (section 13, Q1) |
| the algorithm | hash join: **build the `--join` file whole in memory, stream the probe side** | the only shape that streams one input and keeps the bound on the other (section 6) |
| the bound | the **accounted** bytes of the build, counted by a stated formula, against `--max-state-bytes`; **one budget** shared with the groups or the sort rows that the same call holds; past it `limit.state-too-large` with the build's facts and, when the files are both on disk and the join is inner, a `choose` repair that swaps the sides | the flag keeps its meaning (accounted bytes, **not** resident memory) (section 6.3) |
| duplicates | all matches are output; the output is bounded by `--max-join-rows` (default 10,000,000) and refused past it with `limit.too-many-join-rows`; **no per-key fan-out limit** | a hot key that no probe row has costs nothing; the output is what costs (section 6.5) |
| output order | **probe order, then build order** within a probe row; unmatched probe rows (left join) in their place; the same bytes at any thread count | one definition, checkable against a SQL oracle with explicit row numbers (section 7) |
| threads | the build is single-threaded; the probe is sequential in the first slices, then split by the existing speculative range scan **if and only if** a spike shows a worker can read the parent's build table (section 7.2) **(Corrected by section 15: a worker can read it, but cannot also hold its own output; the parallel probe waits for one change in cancho, section 15.3)**; until then `--threads` is accepted and reads on one core with the same bytes, as `--order-by` and standard input do | the build table cannot be copied to every thread within a stated bound; the language question is open (section 7.2) |
| `--where` | evaluated on the joined row, left to right, stopping at the first false, as everywhere; **the leading run of text-only conditions** that name one side is applied to that side first (probe side always; build side only for inner and semi); `--join-where` filters the build side before it is built (the SQL `ON` clause) | defined, visible in `--explain`, and equal to the unpushed evaluation except for which cells a typed key read can refuse (section 8.1) |
| `--group`, `--agg`, `--order-by`, `--top`, `--limit`, `--from`, `--format` | work on the joined row unchanged; the build state and the groups/sort rows **share** `--max-state-bytes` | the engine sees one more kind of record, not a new plan (section 8.2) |
| `--report types` | refused with a join in v1 (`args.conflict`); `table ... --join ... --format csv \| table --report types` does it | a report over padded rows needs its own design (section 8.5) |
| spill | **not in v1.** Past the bound: the refusal. The designed route past memory is a **merge join over two sorted inputs**, a different contract (section 9; **built by section 23**) | bounded memory and no hidden disk; no write authority is added |
| authority | **unchanged**: the build file is opened with the labels the operand already uses; the gate checks the manifest (section 10) | a join must not widen what `table` may reach |
| new rules | two: `join.column-collision`, `limit.too-many-join-rows`; every other failure is an existing rule with a `detail.input` saying `probe` or `build` | the refusal table stays small (section 5.6) |

## 1. What exists, and what a join must not disturb

* **One input, one plan.** The flags and a `--query` both fill one `Inputs` value (`tools/table/inputs.cho:13`, filled by
  `from_flags` at `:58` and `for_query` at `:113`); `body` reads the plan only through it (`docs/architecture.md:33-34`).
  A join adds fields to `Inputs` and nothing else is a second route to a plan.
* **The engine consumes a record, not a format.** A reader gives the engine the record's bytes, three integers per field (`first`,
  `last`, a quoted flag) and a line number (`docs/readers.md:62-70`). `engine.screen` (`engine.cho:408`) tests `--where`, and rows go
  to `process_rows` (`:464`) or `process_groups` (`:485`). **A joined row can be handed to it as a CSV record**: the probe record,
  the delimiter, the build record, with the build cells' offsets moved by the probe record's length plus one. That is what the
  sorter already relies on (it holds a record and splits it again, `docs/readers.md:620`, N16, `jrec.csv_record` at `jrec.cho:548`).
* **One loop per format; nothing is added to the shared loop.** `docs/backlog.md:75-81` and `docs/readers.md:48-56` measured that a
  case that is never taken, added where a row is counted, made `--select` and `--where` 15 percent slower. The JSON-lines reader
  therefore has loops of its own (`jsonl.cho`, `jscan.cho`, chosen per range by `dispatch.range`, `dispatch.cho:14-19`), and so must the join: a
  `join.cho` read loop beside `read_file` (`table.cho:123`) and `read_stream` (`table.cho:661`, a generated copy of `read_file`,
  `scripts/stream.py`), and a range scan beside `scan.scan_range` (`scan.cho:226`).
* **`gmap.cho` is a hash table from bytes to an entry number, nothing else.** It stores no values (`gmap.cho:5-6`), is open addressing with
  linear probing, at most half full (`:12`), a slot holds a 31-bit tag beside the entry number (`:18`), an entry is three integers: where
  its key starts, its length and its hash (`:20-21`), and entries are numbered in the order they were put (`:12`). The hash is taken
  once, by the caller (`hash` at `:67-89`, `find_h` at `:92-119`, `put_new` at `:189-214`), and **it is not seeded** (constants in
  the source), so the same bytes hash the same everywhere. A join build can use it as is: the entry number is the index of a side
  array of `(head, tail)` per distinct key. It cannot hold two rows with the same key, which is what the side arrays are for.
* **`--max-state-bytes` counts accounted bytes, not resident memory.** `agg.add` refuses a new group when `bytes_held + key + float_state > max_state`
  (`agg.cho:321`), where `bytes_held` is only the key bytes and the float accumulators; the sorter refuses when the bytes it holds, the record
  and 8 integers a key would pass it (`sorter.cho:205`, `docs/gap-sort.md:139`). The flag is 1 to 1,073,741,824 and defaults to 67,108,864
  (`table.cho:96`, checked at `:1358-1360`). **The statement "resident is at most k times the flag" was withdrawn** (`docs/gap-groups.md:191-195`,
  `docs/backlog.md:133-135`): `B3` accounts 14,000,000 bytes for about 170 MB resident, and a threaded refusal peaks at up to 2.0x
  the sequential resident memory. This design makes no claim of that kind either (section 6.3).
* **Threads are shared-nothing.** `spawn` takes one payload (a unique reference to a struct, a `Heap`, a `File`), there are no atomics
  or channels, a thread's answer comes back through a fixed-size slot, and each thread gets its own copy of the plan
  (`docs/parallel.md:15-32`). A range is read by a thread, the parent takes ranges **in file order**, and rereads any it cannot trust
  (`docs/parallel.md:78-90`). A shared `&` reference to one struct may cross a `spawn` in the language (`lex-sys/docs/threads.md`, section 3, "Why this needs no new soundness rule"),
  but a thread gets one payload and the job's own state is a `&!` reference (`docs/gap-sort.md:381`): whether both can be had is open (section 7.2).
* **A qualified name is free.** `docs/query.md:205` and `:210-217` reserved `join inner left right full cross on using` and the dot (a bare `a.b`
  is a syntax error on purpose), and committed to nothing else. `semi` and `anti` are **not** reserved; this design makes them
  contextual (section 2.3) so that no query valid today changes.
* **Today's answer to a join.** `table --join x.csv a.csv` is `args.unknown-flag`; a `--query` with `join` is `query.unsupported`, feature `join`
  (checked on the build of `072ce15` on 2026-10-14).

## 2. The surface

### 2.1 The flags

```
table [flags] --join FILE --on KEYS [--join-type TYPE] [--join-prefix P] [--join-where EXPR] [--max-join-rows N] [FILE|-]
```

| flag | meaning |
|---|---|
| `--join FILE` | the **build side**: a path beneath `--root`, resolved exactly as the operand is (`path.*`, `io.*`; the rule of `qpath.cho:1-8` already does this for a query's `from 'path'`). `-` is `args.bad-value` ("the build side is read whole before the first probe row, so it cannot be standard input; name the file here and pipe the other one") |
| `--on KEYS` | a comma list of key pairs, 1 to 8. `LEFT=RIGHT` names a column of the probe side and a column of the build side (`#N` is the Nth column **of that side's own header**); an optional single `:int`, `:dec(S)` or `:float` suffix at the end of the pair reads **both** columns as that type. `NAME` alone is `NAME=NAME` with the USING behaviour of section 4.2. Escapes are the flags' own: a backslash before a comma, a backslash, a leading `#`, and (new, for this flag) a `=` |
| `--join-type` | `inner` (default), `left`, `semi`, `anti` |
| `--join-prefix P` | text put before **every** build-side column name in the output (section 4) |
| `--join-where EXPR` | a `--where` expression over the build side's own columns, applied to build rows before they are held (the SQL `ON b.x > 1`; section 8.1) |
| `--max-join-rows N` | the most rows the join may produce, 1 to 1,000,000,000, default 10,000,000 (section 6.5) |
| `--join-delimiter`, `--join-input-format`, `--join-fields` | the build side's reader; defaults are the probe side's `--delimiter`, `--input-format`, `--fields`. **Built in J5: section 21** |

A call with `--join` and no other plan flag is the **rows** form, every column of the joined row, as `--rows` is for one file
(the `all:` line of the usage string, `table.cho:96`). `--select`, `--where`, `--order-by`, `--top`, `--limit`, `--from`, `--group`, `--agg`, `--sort`, `--format` and the
bounds mean what they mean for one file. Refusals of the flag set, all before any read:
`args.required-flag` (`--on` or `--join-type`/`--join-prefix`/`--join-where` without `--join`; `--join` without `--on`), `args.conflict`
(`--join` with `--report` or `--discover`), `args.bad-value` (a key list of 0 or more than 8 pairs, a type on one side only, an unknown
`--join-type`, `--join -`). A `--join` file equal to the operand is allowed (a self-join: two opens of one path), with the one caveat
that the file is read twice at two times (section 13, R6).

### 2.2 Which joins in v1

| type | rows out | why in or out |
|---|---|---|
| `inner` | each probe row, once per matching build row | the core |
| `left` | `inner`, plus each probe row that matched nothing, once, with every build cell **empty** | the question "all of these, with the extra column where there is one". The padding is the empty cell because there is no null; section 8.1 says what that means for a condition on a padded column |
| `semi` | each probe row that has **at least one** match, once, **probe columns only** | "which of mine appear in that list". Costs no fan-out; the build holds only keys (section 6.2) |
| `anti` | each probe row that has **no** match, once, probe columns only | **the only way to ask "which rows have no counterpart"**: SQL asks it with `left join ... where b.k is null` and `table` has no `is null` and no null (`docs/query.md:81`, the `null` row of the refusal table). This is why `semi` and `anti` are in v1 and not "later" |
| `right`, `full` | out | they need a bit per build row set when matched and a second output phase after the probe; a `right join` is a `left join` with the files swapped, whose output order is different (the probe side is the other file). Not hidden: `query.unsupported`, feature `right join`, hint "swap the two files and write `left join`" |
| `cross` | out | no key, no bound that is not the output's; `args.required-flag` for `--on` already says so |

### 2.3 The query form

```ebnf
statement = "select" items "from" source [ [ jtype ] "join" source "on" onconds ] [ "where" conds ] ... ;   (* the rest as docs/query.md section 2 *)
source    = ( string | "stdin" ) [ "as" alias ] ;       (* "as" is required on both sources of a join query *)
jtype     = "inner" | "left" | "semi" | "anti" ;        (* "semi" and "anti" are keywords only here, between a source and "join" *)
onconds   = eq { "and" eq } { "and" buildcond } ;
eq        = qcol "=" qcol | typed-qcol "=" typed-qcol ;  (* one column of each alias, in either order; both with the same ::type, or none *)
qcol      = alias "." ( ident | quoted ) ;
```

* **Every column of a join query is qualified** (`a.id`, `b."order id"`). A bare `id` is `query.syntax`: "a column of a join is written
  alias.column" **(Corrected by section 20: the `as` name of a select item is the one bare name, in `order by`)**. Reason: the translator knows no header (`docs/query.md` section 1: it writes flags and the existing parsers do the
  rest); to resolve a bare name it would need both headers, which is a second plan. Qualifying is the explicit form.
* **Aliases need `as`.** The grammar has `[ [ "as" ] alias ]` (`docs/query.md:213`); in a join query the optional `as` is required, so `from 'a' semi join` cannot be read
  as an alias called `semi`. `semi` and `anti` stay bare identifiers everywhere else, so **no query that is valid today changes**
  (the gate: the corpus of queries of `query_corpus.py` translates to the same flags).
* **`left outer join`** is `query.unsupported` (`outer` is not reserved and is not made so): "write `left join`".
* **`on`** may hold, after the key equalities, conditions that name **only the build alias**; they become `--join-where`. A condition on the probe
  alias in `on` is `query.unsupported`, feature `a condition on the left table in on`, hint "put it in `where`, which filters the left rows"
  (SQL gives a left join's `on a.x > 5` a meaning that does not filter the left rows; refusing is better than choosing).
* **Names in the output.** The translator writes `--join-prefix 'b.'` (the alias and a dot), so the build columns are `b.name` in the answer
  and the probe columns keep their names; `select a.id, b.name` is `--select id,b.name`. The dotted header is visible in the answer; there are no
  aliases for columns (`as` stays refused, `docs/query.md:82`), and the flag form is the oracle (gate Q-EQ, section 10).
* **`from stdin join 'b.csv' ...`** is the probe on standard input. `from 'a.csv' as a join stdin as b` is `query.unsupported`, feature
  `standard input as the build side`.

### 2.4 One join

A second `join` is `query.unsupported`, feature `more than one join`, hint "pipe the first join's `--format csv` into the second: `table ... | table --join c.csv ...`".
This is not a workaround: the probe side on standard input is streamed, so each stage is bounded by its own build side, and each stage's output is the
defined order, so the chain is deterministic. The cost of the pipe is one parse of the intermediate (about the time of a file on one core,
`docs/readers.md:466-486`). Two joins in one process would need a plan with a tree, which is the second plan this tool does not have.

## 3. The inputs

### 3.1 Roles and standard input

| role | what | may be |
|---|---|---|
| probe (left) | the operand; the rows are streamed | a file, or standard input (`-` or no operand) |
| build (right) | `--join FILE`; read whole, first | a file under `--root` |

* **Why the user chooses and the tool does not guess.** The tool could pick the smaller file by `stat`, but the output order is the
  probe's, and a tool that changed the order of its answer with the sizes of the files would give two different byte sequences for one
  command as the files grow; the order must be a function of the command. So: the build side is the file named in `--join`, always,
  and the refusal of an oversize build (section 6.3) tells the user which swap is possible.
* **Standard input is the probe only.** It has no size and no ranges (`docs/readers.md:107-120`): it is read in one pass on one core. It is
  allowed exactly where it is useful, streaming through a build that is already held. `--threads` on a stdin probe is "one core, same bytes,
  no refusal", as for every plan (`docs/readers.md:113`). The build side is read before the first byte of standard input is consumed,
  so a build refusal leaves the pipe unread (the producer sees a closed pipe, as it does for any flag refusal). **Corrected by section 16.2:** the first line of the pipe, its header, is read before the build's rows (the collision of names is refused before any row of either file is read); the rows of the pipe are not.
* **Both inputs read the same file twice** if they are the same path; nothing is claimed about a file that changes between the two opens
  (`docs/query.md:564` makes the same statement for `--explain`).
* **MCP.** The server passes `--join=VALUE` as one argument and the tool confines the path under the server's `--root`, one component at a time,
  refusing `..`, absolute paths outside the root, and links (`docs/mcp.md:15-48`). The server adds no check of its own on a path. A call with
  `file` and `join` is two files under the root; a call with `stdin` and `join` is the stdin text as the probe side and a file as the
  build side (`docs/mcp.md` section 12 gives `stdin` as the call's standard input and refuses both `file` and `stdin`, which stays true:
  `join` is a second *file*, not a second input text). `join: "-"` is the tool's `args.bad-value` and cannot reach the server's own standard
  input (that is the JSON-RPC channel, `docs/mcp.md:392`). The generator builds the `join` property from the flag table (kinds `text`, as the
  other flags); the gate repeats the 14 path forms of `test_dotdot_absolute_and_links_are_refused_with_their_rules` for `join`.

### 3.2 Formats

* **First slices: csv or tsv on both sides**, one `--delimiter`. A join of a csv and a tsv is not possible until `--join-delimiter` exists; the
  tool cannot detect a wrong delimiter (it does not guess), so a wrong one shows as a `column.unknown` for the key or a ragged row.
* **Later slice: jsonl on either side**, `--join-input-format jsonl --join-fields PATHS` (`docs/readers.md` section 12 gives the reader; the
  declared fields are the header). `--absent` and `--max-json-nodes` apply to both sides. `--discover` conflicts with `--join`. **(Built by J5, section 21, as written here.)**
* **The record is the joined CSV record whatever the formats** (section 1): for a jsonl side the engine already gets a record and cells
  (`jrec.cho:202`), and `jrec.csv_record` (`:548`) is the existing conversion to CSV text. The held build row is stored as that CSV record.
* **BOM, CRLF, blank lines, quoted newlines** are each side's reader's, unchanged. A one-column file's empty cell is a blank line and is skipped by the
  reader (measured on `072ce15`: a one-column file `k` with an empty row groups as if the row were absent), so an empty key can only occur
  in a file of two or more columns; the oracle includes the one-column case (section 10).

## 4. Columns: the header of a join, collisions, prefix

### 4.1 The rule

The header of the joined row is **the probe header, then the build header** (semi and anti: the probe header only). `--select`, `--where`, `--group`,
`--agg`, `--order-by`, `--sort` all name columns against this header, by name or `#N` (position in the joined header), by the rules they have today
(`plan.resolve`, `plan.cho:111`; `frame.prepare`, `frame.cho:91`, takes the header as a buffer and the end of each name, so it is given the
concatenation and **needs no change**). With `--join-prefix P`, every build name in the joined header is `P` followed by its name.

* A name that is on both sides of the joined header is `join.column-collision`, **exit 2, before any read but the two headers** (nothing is renamed on the way): `detail.columns`
  (the names, at most 32), `detail.prefix` (what was given, or `""`). Its repair is `choose` with one `retry` option, `--join-prefix` set to
  the stem of the build file's name and an underscore, **only if the result has no collision**; otherwise `none`. The offered prefix is a
  suggestion the reader picks; the tool applies nothing.
* A name twice inside one side's header is the ambiguity the tool already knows (`select.ambiguous-column`, `column.ambiguous`, "name it by
  position", `finish.cho:709-716`): `#3` of the joined header. A key named `#N` in `--on` is read against its own side's header.
* **Why a refusal and not a default prefix.** A default such as `right_` makes `select id` mean the left `id` in one file and an error in another. The collision
  is the thing the reader must see. The cost is one extra flag in the join of two files that share a column name; that is the whole of the minimal rule.
  **If the rename feature of `docs/or-and-columns.md` is built** (it is, since slice B1: `--rename TARGET=NEW`, which names the columns of the joined row and
  changes only the labels of the answer), `--join-prefix` could become sugar for renaming every build column and `join.column-collision` could offer a rename in
  its repair. Slice B1 took the narrow reading and changed neither: a collision is refused before any label is written, so a rename cannot repair it by itself (the
  duplicate names have to be told apart first, by position or by a prefix), and a repair that wrote the rename would have to invent the new label, which no repair of
  this tool does (`docs/or-and-columns.md` section 8.2: "no name is invented"). `--rename` works on the joined row once the collision is resolved
  (`--join-prefix r_ --rename r_w=who`).

### 4.2 USING: the shared-name key

`--on id` is the pair `id=id` **and** the build column `id` is left out of the joined header ("USING"). The probe's `id` is the key. This is the one place a
column is omitted, and the omission is the written form (`--on id`, not `--on id=id`). For a text key the two cells are the same bytes. **For a typed key
they are the same value and may be spelled differently** (`007` and `7`): the build's spelling is not shown, and the doc of the flag says so. A user who
wants both spellings writes `--on id=id:int --join-prefix r_`. In a `semi` or `anti` join the build side is not in the output at all.

### 4.3 Padding and the `--format jsonl` names

A left join's padding cells are the empty cell. `--format jsonl` needs unique names (`output.duplicate-column`, `finish.cho:922`, `jout.cho:19`): the
collision rule makes them unique before that rule can fire. The type of a key in `--format jsonl` stays what `docs/readers.md` section 13 says for a group key.

## 5. Keys: exactly when two cells are the same

### 5.1 Text keys

The key is the cell's bytes after the CSV quotes are undone (a doubled quote is one quote), compared as bytes: no case folding, no trimming, no
locale, no normalisation (`docs/filter.md`, "Comparison"). `7` and `07` differ, `a` and `A` differ, a space differs. The bytes may be anything the reader
accepts, including bytes that are not UTF-8.

### 5.2 Typed keys

`--on a=b:int`, `a=b:dec(S)`, `a=b:float`, and (slice T3 of [`datetime.md`](datetime.md), section 20) `a=b:date`, `a=b:timestamp`, `a=b:datetime` read **both** columns as the type, and two cells match when they have the **same value** as `--group` would put them in one group (`docs/numbers.md`, "What was built: N5"): an integer
`07` is `7` and `-0` is `0` (measured on `072ce15`: `--group k:int` on the cells `007, 7, -0, 0, +7` gives the groups `0` (2) and `7` (3)); at `:dec(2)` `1.5`, `1.50` and `01.5`
are one value; at `:float` the cell is the nearest double, `-0` is `0`, and NaN and infinities are refused (`value.not-finite`). The key's bytes are
the 8-byte order-preserving value (`agg.put_value_key`, 12 bytes with the length in the group state), so hashing and equality are the bytes' (`gmap`).
A cell that is not of the type is the existing refusal (`value.not-integer`, `value.integer-overflow`, `value.not-decimal`, `value.decimal-scale`, `value.decimal-too-wide`,
`value.not-float`, `value.float-range`, `limit.number-too-long`) with `detail.context` **`join`** and `detail.input` **`build`** or **`probe`**, the row, line, column and value of the
cell in its own file. **There is no cross-type join**: one suffix reads both columns, so `int` against `dec` is a refusal of the flag (`args.bad-value`), and the user
chooses the type that both can be read as (`:dec(1)` reads `1` and `1.0`; that is a statement of the user's, not a guess of the tool's).

### 5.3 Empty cells (decided, and the decision is open: section 13 Q1)

* **A typed key with an empty cell is a refusal**, as it is everywhere (`docs/filter.md:88-98`; measured: `--group k:int` on a file with an empty cell is `value.not-integer`). The
  repair that the tool already teaches is a guard: for the build side `--join-where "k != ''"`; for the probe side `--where "k != '' and ..."` (section 8.1 makes the leading text
  condition run before the key is read, so the guard works). **Corrected by section 17.2:** J2 has neither `--join-where` nor the split of `--where` (both are J3), so in J2 `--where` runs after the join and the key is read first; the refusal of J2 teaches the repairs it has (read the pair as text, or correct the file) and not this guard. **Corrected by section 18.2 (J3):** both exist now, and the refusal of an empty typed key offers this guard as its repair (reading 10).
* **A text key with an empty cell is a key: it matches an empty cell.** Argument: (1) `table` says it has no null and that an empty cell is the empty text
  (`docs/query.md:81`), `name = ''` works, and `--group` makes `''` a group (measured: `--group k` over `,a` and `1,b` gives the groups `` and `1`); the join's equality is the
  `where`'s equality; (2) the alternative, "an empty key never matches" (SQL's behaviour for null), makes an inner join answer about fewer rows than the file has, which is
  the failure `docs/filter.md:88-92` rejects for numeric reads; (3) a blank key that matches too much is **visible** (rows appear) and **bounded** (`--max-join-rows`), while a
  key that silently does not match is invisible. (4) The tools disagree, so there is nothing to be compatible with. **Measured, 2026-10-14, keys `""`, `7`, `007`, `-0`, `0`, `1.0`, `1` against `""`, `""`, `7`, `0`, `1`:**

  | tool | empty = empty | `007` = `7` | `-0` = `0` | `1.0` = `1` | note |
  |---|---|---|---|---|---|
  | DuckDB 1.5.6 `read_csv` defaults | no (empty is NULL, also with `all_varchar`) | yes | yes | yes | the probe column is inferred VARCHAR (it holds `1.0`) and the build column BIGINT, so the join casts: the answer depends on inference |
  | DuckDB `all_varchar=true` | no | no | no | no | exact bytes, empty still NULL |
  | csvtk 0.38.0 `join -f k` | **yes** | no | no | no | exact bytes, empty matches empty |
  | SQLite 3.53.4, `text` columns | **yes** | no | no | no | |
  | SQLite, `integer` columns | yes (stays text `''`) | yes | yes | yes (`1.0` becomes 1) | affinity converts silently |

  The one who wants SQL's behaviour writes it: `--join-where "k != ''"` (the build rows with an empty key are not held, so no probe row matches an empty key; for a `left` join the probe rows with an empty
  key are kept and padded, as in SQL).
* `--explain` carries a note for every text key: "an empty key matches an empty key (there is no null)".

### 5.4 Composite keys

Up to 8 pairs. The key bytes are the components' key bytes each preceded by a 4-byte length (as the group key is built), so `("ab","c")` and `("a","bc")` differ.
Each component has its own type suffix.

### 5.5 When a key is read

The probe row's key cells are read once per probe row, after the leading text conditions of `--where` (section 8.1) and before the rest of `--where`; the build row's after `--join-where`.

### 5.6 The rules a join uses

| situation | rule | what is new |
|---|---|---|
| a key column the header lacks | `column.unknown` (exit 3) | `detail.flag: "--on"`, `detail.input` |
| a key column twice in a header | `column.ambiguous` | `detail.input` |
| a typed cell that is not its type | `value.*`, `limit.number-too-long` | `detail.context: "join"`, `detail.input` |
| ragged row, bad quote, unterminated quote, long line, record too large in either file | `parse.csv-*`, `limit.line-too-long`, `limit.record-too-large` | `detail.input`; the **build file is read first**, so its refusals come first, then the probe's in probe file order (any thread count) |
| build file past `--max-rows` rows | `limit.too-many-rows`, always (a truncated build is a wrong answer; the probe's `truncated` of `docs/readers.md` does not exist for it) | `detail.input: "join"` |
| build bytes past the budget | `limit.state-too-large` | `detail.holder: "join-build"` and the facts of 6.3 |
| output past `--max-join-rows` | **`limit.too-many-join-rows`** (exit 8) | new |
| the same name on both sides | **`join.column-collision`** (exit 2) | new |
| `--join` with `--report`, no `--on`, a bad `--on` | `args.conflict`, `args.required-flag`, `args.bad-value` | |
| `query.*` features | `query.unsupported` features: `right join`, `full join`, `cross join`, `left outer join`, `more than one join`, `a condition on the left table in on`, `standard input as the build side`; `query.syntax` for an unqualified column in a join query | features, not rules |

## 6. The algorithm

### 6.1 Shape

```
open build, read its header; open probe (or stdin) and read its header                                   header phase: both headers, collisions, keys resolved, plan resolved against the joined header
build phase:  for each build row (file order): ragged? -> --join-where? -> key bytes -> find_h / put_new -> append row, link at the tail of the key's chain
probe phase:  for each probe row: ragged? -> leading text conditions? -> key bytes -> find_h -> for each build row of the chain in order: joined record -> engine
```

The build phase completes before the first probe row is read: **the first byte of output waits for the whole build** (`--limit` does not shorten it; the page does
shorten the probe). This is stated in `--explain`'s `work`.

### 6.2 The build table (a design on top of `gmap`)

| piece | what | bytes (accounted, per) |
|---|---|---|
| `gmap.Tab` of keys | key bytes in `keys`, 3 integers per entry, 2 to 4 slots of 8 bytes per entry | key length + 4 + 24 + 16 (at the minimum load; `gmap.cho:26-32` rounds the slots to a power of two at least twice the capacity) |
| `head`, `tail` | two integers per distinct key: the first and last build row of the key | 16 |
| rows | the held records, concatenated, in build file order, in one buffer | record length |
| `start`, `next` | per held row: where its record starts in the buffer, and the next row of the same key (or -1) | 16 |

**Accounted bytes (choice, a formula; the gate A of section 10 holds the allocation to it).** *Corrected by section 17.2 for J2: a key is the cell for one pair of text, 8 bytes for one typed pair, and several pairs carry 4 bytes of length before each component; a `semi` or `anti` join holds the keys alone, at key bytes + 44 and no row.*

```
accounted = sum over held rows (record_bytes + 16)  +  sum over distinct keys (key_bytes + 60)
```

`60` = 4 (length) + 24 (entry) + 16 (slots at minimum load) + 16 (head, tail). The record is the record the engine sees (quotes kept, so a quoted
field costs its quotes); rows kept out by `--join-where` or by a pushed text condition are not held and not counted. For `semi` and `anti` only the keys are held (no rows, no chains: a key
is present or not), so a build that is mostly duplicates is small. The formula is **a statement of what the join holds in its arrays; it is not resident memory**: `gmap`
doubles its storage when it fills (`gmap.cho:136-164` allocates the new storage before the old is freed), the allocator keeps what it freed, and the group-by's 14,000,000 accounted bytes against
170 MB resident (`docs/gap-groups.md:191-193`) is the size of the gap this kind of flag has. The first slice **measures** accounted against peak resident set on the
files of section 12 and publishes the ratio, as `docs/gap-groups.md` section 3.4 does; no multiple is claimed before then. (Measured in section 15.1: 1.65 to 2.26 times on eight build files of a spike, 1.0 to 1.3 when the table is made at its final size.)

The same formula applied to the baseline files of section 12 (**computed**, from the files' bytes, not measured allocation):

| build file | rows | record bytes | key bytes | accounted | against the default 64 MiB (67,108,864) |
|---|---:|---:|---:|---:|---|
| 100k integer keys | 100,000 | 1,177,780 | 488,890 | 9,266,670 | fits |
| 100k text keys | 100,000 | 1,878,580 | 1,189,690 | 10,668,270 | fits |
| 1M integer keys | 1,000,000 | 13,777,780 | 5,888,890 | 95,666,670 | **refused**, needs `--max-state-bytes 95666670` or more |
| 1M text keys | 1,000,000 | 19,785,790 | 11,896,900 | 107,682,690 | **refused**, needs 107682690 or more |

(The refusal at 1M rows with the default is a consequence of the formula, and the user is told the number in the refusal. Whether the default should be higher for joins is a question of section 13, Q4.)

### 6.3 The bound, and the refusal past it

* The build refuses at **the first held row that would pass the bound**, in build file order, exactly as `agg.add` (`agg.cho:321`) and the sorter do, so the threshold is one byte at every thread count
  and the gate bisects it (section 10, as `docs/gap-sort.md:181` does for the sort).
* **One budget.** The build's accounted bytes are held for the whole run. The groups of `--group` and the rows of `--order-by` (and `--report`, refused) are given
  `max_state - build_accounted` as **their** bound: `agg.add` and `sorter.add` already take `max_state` as an argument (`agg.cho:251`, `sorter.cho:186`; `sorter.set_bounds`, `sorter.cho:84`), so
  the change is the number passed and no new counter in the groups. A refusal there is `limit.state-too-large` with `detail.holder: "groups"` or `"sort"` and `detail.build_bytes`. This keeps the flag's meaning: the accounted
  bytes the call holds in tables and rows. (Alternative: a second flag `--max-join-bytes`, which would make "the memory of this call" the sum of two flags and the ceiling 2 GiB. Rejected: the
  number a user can reason about is one; Q4 asks the maintainer.)
* **The refusal says what is exact**, with no projection of the total (a projection would be a guess): `detail.holder: "join-build"`, `detail.limit`, `detail.held` (the accounted bytes
  before the row that did not fit), `detail.rows_held`, `detail.build_row` and `detail.build_line` (the row that did not fit), `detail.build_bytes_read` (the file position) and `detail.build_file_bytes`
  (the size on disk).
* **The repair** is `choose`, and its options are mechanical and come from `stat`: **swap the sides** is offered only if (1) the join is `inner` (the only type whose rows are the same multiset either way),
  (2) the probe is a file under `--root` (not standard input) and (3) the probe file is smaller on disk than the build file; its `argv` is the call with the two files and the key pairs
  exchanged. The hint says "the order of the rows and of the columns changes: the probe is now the other file". **Raising the flag is in the hint and not an option**, because the exact number needed is not known
  at the refusal (the rest of the build is unread). A `left`, `semi` or `anti` join has no swap: its meaning depends on which side is which. **Built by section 22, with these changes:** the option is offered only when the other file also fits the bound (the refusal reads it as a build side to see), and only when the exchanged call gives the same rows (the list of reading 4 of section 22); the columns of the answer do *not* change (a `--select` of the original joined header is added when the plan selects nothing), the order of the rows does, and `--explain`'s `join.build_state.swap` says whether it would be offered.
* `--explain` (section 8.4) says, before any read, what is exact about the build file: its size, and an **upper bound** on the accounted bytes that follows from the size and the header (every
  non-blank record has at least `columns - 1` delimiters and one terminator, so there are at most `(size - header) / columns + 1` rows; every record and key is at most the file;
  the bound is therefore `(size - header) + 76 * rows_at_most` and is loose for narrow files). **Changed by section 16.2:** the derivation above leaves out that the key bytes of the distinct keys are counted besides the record bytes that contain them, so `(size - header) + 76 * rows_at_most` is not proved to be an upper bound (no file of the tests violates it, because `rows_at_most` is loose, which is not a proof); J1 states the provable `2 * (size - header) + 76 * rows_at_most` with `rows_at_most = (size - header) / max(columns, 2) + 1`. It says "cannot pass the bound" when the upper bound is within it, and "may pass it" when not; it never
  says "will", because a lower bound cannot be had from the size (blank lines). It does not read rows (`docs/query.md` gate E2).

### 6.4 The probe

For each probe row, in file order: counted (the engine's `count_row`, `--max-rows`, `--from` as today), ragged rows never used, the leading text conditions of `--where` that name the probe side,
the key bytes (typed reads may refuse), `find_h`, then per join type:

| type | emits |
|---|---|
| inner | one joined record per build row of the chain, in chain order |
| left | the same; with no entry, one joined record whose build part is `columns_b - 1` delimiters (all empty) |
| semi | the probe record, once, if the entry exists |
| anti | the probe record, once, if it does not |

The joined record goes to the engine's `screen` (the rest of `--where`) and then to the row writer, the groups or the sorter as for any row. **The row number of a joined row in a refusal is the
probe row's number and line, with `detail.input: "probe"`**; a padded cell says `detail.padded: true` (so `value.not-integer` on a padded cell is not mistaken for a bad cell of the build file).

### 6.5 Duplicates, fan-out and the output bound

* A key that appears `m` times in the build and `n` times in the probe produces `m * n` rows (inner). The memory is the build's alone: the probe row is not held and the matches are streamed, so
  **fan-out costs time and output, not state**.
* `--max-join-rows` bounds the rows **produced**, before `--where` (it bounds the work of the join, as `--max-rows` bounds the rows read; `tools/table/table.cho:39`). Default 10,000,000 (the `--max-rows` default), ceiling 1,000,000,000. The refusal
  `limit.too-many-join-rows` is raised at the first row that would pass it, with `detail.limit`, `detail.probe_row`, `detail.probe_line`, `detail.matches` (the chain length of that probe row, exact) and
  `detail.max_matches_so_far`, and a hint that distinguishes the two causes without guessing which it is: "if `matches` is large the build side repeats this key: remove duplicates, filter with
  `--join-where`, or make the other file the build side; otherwise raise `--max-join-rows` or page". A page (`--limit`, JSON's default 1000) stops the probe early, so a paged answer of a huge join is cheap.
* **No per-key fan-out limit** (the alternative: `--max-fanout`, default 1000, refusing the build when one key repeats more). It would refuse a join whose repeated key never occurs in the probe, it needs a
  second flag, and it protects only what `--max-join-rows` protects. It can be added without breaking anything; Q3 asks.
* A **chain is walked in order**: the build rows of a key are linked at the tail as they are read (`tail` per key), so the walk is build file order with no sort. Memory cost: the 16 bytes per distinct key.

## 7. Order, and threads

### 7.1 The defined order

> **The output is the probe rows in the probe's order; for each probe row, the build rows of its key in the build file's order; an unmatched probe row (left) or an unmatched-only row (anti) in its
> place.** Semi and anti output probe order.

This is a function of the two files and the command, not of the machine. Everything after the join (`--where`, `--order-by`, `--group`, `--top`, `--limit`, `--from`) is applied to this sequence as
to the rows of one file: `--order-by` is stable ("ties in file order", `docs/numbers.md` N5), and its "file order" is this order; a page's `--from` counts these rows; the groups are a function of the multiset.

### 7.2 Threads (the honest state)

**Slices J1 to J5 read on one core under `--threads N`, with the same bytes, no refusal, and `--explain` says why** (`work.threads`: requested N, effective 1, reason "the join is read on one core"), the
treatment `--order-by` had (`docs/backlog.md:82-84`) and standard input has (`docs/readers.md:113`). This is a stated limit, not an omission to hide.

**The probe is a scan; the build is a shared table.** Splitting the probe by the speculative range scan (`docs/parallel.md` section 2, `scan.scan_range` at `scan.cho:226`) is exact for the same reason it is for
`--select`: a range's output is the sequence of its rows' joined records, the parent takes ranges **in file order** and appends them, and a range it cannot take (a refusal, a full page, `--max-join-rows`,
an output that does not fit its slot, a wrong guess at a quoted newline) it reads again itself. Within a probe row the order is the chain order, which is independent of the thread. So **the order of
the output is preserved by the same mechanism that preserves the order of `--select`'s rows**, and nothing about the join changes that argument.

What is not settled is how a worker **reads the build table**:

| option | memory | cost | verdict |
|---|---|---|---|
| (a) copy the table into every thread (as the plan is copied, `docs/parallel.md:26`) | `T` times the accounted build, and not within the flag (the flag would count one) | a copy of up to 1 GiB per thread per wave (every wave opens a file, forks a heap and zero-fills a slot, `docs/gap-scan.md:333`) | refused for tables beyond a small cap; at most a fallback for a build under a stated size (a choice: 1 MiB) |
| (b) a shared read-only `&` of the table crossing `spawn` | one table | none | **spike S0 decided (section 15.2): reading works, the join does not fit.** Before the spike: `lex-sys/docs/threads.md` section 3 lets a shared `&` reference cross a `spawn` if the thread is joined first, but the payload is one reference, and a worker's own state (`&!`: its buffers, its slot) is another. If one payload cannot hold both, the build table and the job go in one struct reached by `&!` and the workers must not write the table (the language would have to say so), or a cancho change is asked for (the user's standing goal is to fix such gaps upstream) |
| (c) a partitioned join: each thread owns `1/T` of the build and the probe rows are routed to the thread that owns the key's hash | one table (partitioned) | the probe is read by every thread, or by one that routes; the output is merged back to probe order by a probe row number | a different algorithm with its own proof of order; only if (b) fails |

**Decision:** no parallel probe in the first slices; J6 builds (b) **if S0 passes**, with the gates of section 10 (same bytes at 2, 3, 4, 8, 16, 64 threads and 1, 7, 64, 1000-byte ranges, a join split at every record). If S0
fails, J6 is (a) for builds under the cap and the request upstream, and the doc is updated; (c) is not designed here.

**Corrected by section 15.2 (S0 ran).** Half passes. A worker **can** read a table the parent built, concurrently with other workers and with no copy (one `&r` of a struct that holds the
table is the whole payload of each `spawn`; 5.4 to 6.2 times faster at 8 threads on a 4,000,000-key probe, resident memory unchanged). It **cannot** also be given its own output box or heap:
a payload is one reference, a struct cannot hold a reference (`a type declaration has no region parameters`), a tuple payload is refused, a `&!` that holds the table cannot go to two
workers, and the table cannot be copied out of a reference. So (b) is not buildable for a join whose workers write rows, until cancho accepts a payload of one shared and one unique reference
(the request is drafted in 15.3; it is not filed). The fallback written above applies: J6 is (a) for builds under a cap, or waits. Option (a) was measured too (15.2): it costs 107 MB of resident
memory per extra thread at 1,000,000 rows and its time stops improving after 4 threads.

**The build.** Single-threaded in v1: a parent inserts rows in file order. Measured proxy (section 12): inserting 1,000,000 distinct keys into `table`'s group map is about 0.2 s on the Mac (a proxy that also sorts and writes the groups), which is more than half of DuckDB's whole 1M x 1M join at one thread (0.35 s), so the build is a large share of a 1M x 1M join and a threaded probe alone will not make that case fast. A **partitioned build** (J8, optional) follows the
model of `docs/gap-groups.md` section 3.2: workers parse and hash their ranges of the build file, cut their rows into `P` blobs by `hash mod P`, and `P` threads each insert the blobs of their partition **in range order**
into a table of its own; chains are therefore in build file order, the bytes are unchanged, the build's accounted bytes are the sum of the partitions', and the refusal is the sequential one because a
partition passing the bound alone means the whole does (`docs/gap-groups.md:160-166`, the decline-and-reread rule). It needs (b) to share the `P` tables with probe workers. **Corrected by section 15.1:** a build of the section 6.2 table, with the reading of the file included, took 70 to 74 ms for 1,000,000 rows (min of 5, loaded Mac), not the 0.2 to 0.3 s of the group-count proxy, which sorted and wrote 1,000,000 groups; the spike's reader is simpler than the tool's (no quotes, no ragged rows), so this is a lower bound for the build and not its time, but the argument above that the build is "as large as the probe" is not supported by it, and J8 is worth less than 7.2 says until a real J1 build is timed.

## 8. Interaction with the rest

### 8.1 `--where`, `--join-where`, and left joins

**`--where` is evaluated on the joined row**, conditions left to right, stopping at the first false (`docs/filter.md:93-98`), against the joined header, so a condition names a probe column or a build
column (with the prefix, if given). That is SQL's `WHERE`. Two refinements, both written down because they are the places a join could surprise:

1. **A leading run of text-only conditions is applied before the join.** Text conditions (`=`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `in`, without a type suffix) cannot refuse and commute with each other.
   The maximal prefix of the `and` list made only of them is split by the side each names: **probe-side conditions run on the probe row before its key is read**, for every join type; **build-side conditions run on
   the build row before it is held, for `inner` and `semi` only**; a condition that names both sides, or any condition after the first typed one, stays where it is, after the join. This is what lets
   `--where "k != '' and ..."` guard a typed probe key, and it makes the state smaller, and it is **defined behaviour, not an optimisation to be proved invisible**: the only observable difference from "join first, filter
   after" is which cells a typed key read can refuse (a probe row removed by a leading text condition has its key cell never read). `--explain` prints the split (`where.probe`, `where.build`, `where.after`).
2. **Why build-side pushdown is not done for `left`, and what `--join-where` is for.** For `left`, `--where "b.x = 'y'"` is evaluated on the joined row: a padded row has `b.x` empty, so it fails (`''` is not `'y'`) and the row is
   removed: the left join has become an inner join, as in SQL. Pushing `b.x = 'y'` to the build instead would keep the probe row, padded: SQL's `ON` semantics. They are different questions, so there are two spellings:
   `--where` is `WHERE` and `--join-where` is `ON`. `--join-where` takes build-side columns by their **own** names (the prefix is not applied inside it), is a full `--where` expression (typed conditions included; a typed condition is evaluated in
   build file order, so its refusal is the first in the build file), and rows it rejects are never held. In the query form the `on b.x > 1` conditions are `--join-where` (section 2.3).
3. **Padding and typed reads.** There is no null; the padded cell is the empty text. A typed condition or aggregate over a build column of a `left` join therefore refuses at the first padded cell (`value.not-integer`
   with `detail.padded: true`), and the repair a person is taught is the guard (`b.x != '' and b.x:int > 5`, which also drops the padded rows). `count(*)` counts padded rows; `count(distinct b.x)` counts `''` once.
   These are consequences of "there is no null", written here so that they are not discovered.

### 8.2 `--group`, `--agg`, `--sort`, `--order-by`, `--top`, `--limit`, `--from`, `--format`

All take the joined row. The group map's `held` counter and the sorter's bytes are given `max_state - build_accounted` (section 6.3). A grouped join over a large probe is the same bounded pass as a grouped file: a probe row is
joined, fed to its group and dropped. `--top N` after `--order-by` keeps its bounded top-N (`docs/sort.md`), which is the cheap way to ask for "the largest 10 of the join". `--from` counts joined rows; the cursor `next.from` is
valid only for the same two files (as for standard input, `docs/readers.md:112`). `--format csv|json|jsonl` as before.

### 8.3 `--threads`, `--chunk-bytes`, `--parallel-min-bytes`

Section 7.2. `--chunk-bytes` and `--parallel-min-bytes` size the probe's ranges when J6 exists.

### 8.4 `--explain`

The plan document gains a `join` member (`data.explain.join`), built from the headers of both files and `stat` only (it still reads nothing after a header, `docs/query.md:239-292`):

```json
"join": {
  "type": "left", "build": "dim.csv", "probe": "events.csv", "role_choice": "the --join file is the build side",
  "keys": [{"probe": "user", "build": "id", "type": "int"}],
  "output_columns": 7, "prefix": "d_", "using": [],
  "build_state": {"file_bytes": 1277787, "header_bytes": 7, "accounted_at_most": 96000000, "budget": 67108864,
                  "may_pass_bound": true, "swap": {"offered": true, "reason": "inner join and the probe file is smaller on disk"}},
  "where": {"probe_before_join": ["status != ''"], "build_before_build": [], "after_join": ["d_amount:int > 5"]},
  "join_where": [], "max_join_rows": 10000000,
  "order": "probe order, then build order",
  "work": {"class": "join: build then stream", "first_output_after": "the whole build", "threads": {"requested": 4, "effective": 1, "reason": "the join is read on one core"}}
}
```

`notes` adds: the empty-key sentence for text keys; "a condition on a build column of a left join removes padded rows" when `--where` names one; "the probe file is smaller on disk than the build file: for an inner join `--join` may be the other file,
and the order of rows then changes" (advice, never an action). `refusals.possible` adds `limit.state-too-large`, `limit.too-many-join-rows`, `limit.too-many-rows` and the `value.*` of typed keys; `refusals.static` adds
`join.column-collision`, `column.unknown` (`detail.input`), `args.*`. `introspect`'s limits table gains `max-join-rows|10000000|1000000000`.

### 8.5 `--report types`, `--discover`, `--rows`

`--report types` with `--join` is `args.conflict` in v1; the report would count padded cells as empty and a key's two spellings as two, and its suggestions would be read as advice about files, not about a join. The way to get it
is the pipe: `table --join ... --format csv | table --report types`. `--discover` reads the first lines of one input and conflicts. `--rows` is implied.

### 8.6 MCP and the skill

The `join`, `on`, `join_type`, `join_prefix`, `join_where`, `max_join_rows` properties are generated from the flag table like the others (`docs/mcp.md` section 3); `introspect` and `skill` are generated from the flag table and the rule table, and the
skill's examples are run by `test_skill` (so a join example that stops working fails CI). The authority row is unchanged (section 10, gate AU).

## 9. Not in v1: spill, and the route past memory

**There is no external-memory join in v1, and no write capability is added to get one** (`table` has no `file_write`, `dir_write`; `docs/architecture.md:71`). Past `--max-state-bytes` the answer is `limit.state-too-large`,
`detail.holder: "join-build"`, whose hint reads: "the build side does not fit in memory: filter it with `--join-where`, give it fewer columns by selecting fewer (built in J7, section 22: the build holds only the columns the plan names), make the smaller file the build side (the repair offers it for an inner
join), or raise `--max-state-bytes`, up to the ceiling `introspect` names (1,073,741,824). There is no join on disk." The same stance as the sort (`docs/backlog.md:96-98`).

**The route that stays inside the philosophy (J9; built, section 23), is a merge join over inputs the user declares sorted**: `--join-sorted` asserts that both files are in ascending order of the key (bytewise for text, numeric for a typed
key); the join holds one group of equal build keys at a time (so its state is the largest duplicate group, bounded by the same flag), reads both inputs in one pass each, and **refuses at the first key out of order** (`join.not-sorted`, the
row, line and both keys; the check costs nothing). The files can be sorted by `table --order-by` (itself bounded by `--max-sort-rows`) or `sort`. Its output order is **key order, not probe order**, which is why it is a different contract
(a flag, not an automatic fallback) and why it is not in v1: a tool that answered one question in two orders depending on memory would break the same-bytes rule.

## 10. The gates, written before the code

| gate | what | fails if |
|---|---|---|
| **O: the oracle** | `tests/conformance/test_join.py`: generated tables joined by **SQLite** (Python `sqlite3`, 3.53.4) **and DuckDB** (the installed CLI, 1.5.6, driven as `duckdb -csv`; the Python module is not installed) and by a Python reference; the generator writes each file twice, once as the tool reads it and once with an added row-number column for the oracle; the oracles `ORDER BY probe_rn, build_rn`. The check compares **the multiset of rows and the defined order**, for inner, left, semi and anti, as CSV and as JSON | any difference in bytes against the order-by'd oracle after the oracle's rows are rendered as the tool renders them (a function written once in the test) |
| O-shapes | the generator draws: duplicates on both sides; no matches; all matches; empty tables (header only; a zero-byte file, whose key is `column.unknown`); empty keys in text columns (one-column files included) and in typed columns (refusal with row/line/column); "NULL", spaces and `0x10` as text keys; typed keys with `007`/`7`, `-0`/`0`, `1.5`/`1.50`/`01.5`, `2^63 - 1`, `-2^63`, 18-digit decimals at their scale, float spellings of one double; big fan-out (one key 5000 x 5000, and a `--max-join-rows` threshold bisected to the row); key columns with the same name on both sides (USING, collision, prefix); quoted keys with commas, quotes and newlines; CRLF and BOM per side; ragged rows and a bad quote in either file; non-UTF-8 key bytes; a header with a repeated name; `#N` keys | |
| O-DuckDB-honesty | DuckDB is run with `all_varchar=true` and an explicit `CAST` for typed keys and `NULLIF(k, '')` for the empty-key rule, **never with inference** (section 5.3 measured that its default depends on inference); the run records the exact SQL | the test is not a comparison with a guess |
| **EQ: non-join plans are unchanged** | `scripts/corpus.py` over the 166 plans: the md5 of every output, status and error equal to `main`'s, at 1 thread and with tiny ranges (`docs/gap-groups.md:363` is the precedent) | any line differs |
| **IB: instruction budget** | `scripts/gate_regress.py --counter` (Mac), 13 non-join cells x {1, 4} threads, build-averaged over three builds a side, bound **1.01** (`docs/numbers.md` G6; `scripts/gate_regress.py:COUNTER_LIMIT`) | any cell over 1.01. A join loop of its own in `join.cho` and `jn_scan.cho` keeps the shared loops byte-identical in what they execute |
| **A: accounting** | the held bytes the code reports equal the formula of 6.2 on every generated build; the bound is exact: accepted at `N`, refused at `N - 1`, bisected, at 1, 2, 6, 16 threads and for a stdin probe; plus the peak resident set against the accounted bytes on the files of section 12, **published as a ratio, claimed as nothing** | one byte off, or a ratio that is not published |
| **ST: standard input** | the probe from a file and from standard input give the same bytes (but `detail.path`/`input` naming `-`), for every join case, with `--threads` in {1, 4}, and `strace -e clone` shows no thread for the stdin probe (Linux) | |
| **TH: threads** (J6) | the same bytes at 1, 2, 3, 4, 8, 16, 64 threads and `--chunk-bytes` 1, 7, 64, 1000, 4 MiB; the same run 200 times; a quoted newline at every boundary; refusals the first in order (build before probe; probe in file order) | |
| **F: fuzz** | 300 inputs (the existing fuzz alphabet) through six join plans: no trap, the sequential bytes and the reference's verdict | |
| **EXPL: explain** | the gate E1 of `docs/query.md` section 9 extended: every refusal a join call raises before reading a row is in `refusals.static`; `explain` of the flag form equals the `explain` of the query form | |
| **Q-EQ** | the equivalence of `docs/query.md` section 8: a generated join plan rendered as flags and as SQL gives identical bytes, refusals included; the 26 mutants of the translator plus the new ones | |
| **AU: authority** | `scripts/manifest.py --check`: the committed manifest, the embedded one and the ceiling in `tools.toml` equal to `main`'s; on Linux the conformance suite runs under `strace` and requires no socket and no write on disk (`docs/architecture.md:73-78`) | a label appears, which would stop the work and be reported |
| **MCP** | the 14 path forms for `join` (`..`, absolute inside and outside the root, links, empty, over-long); `join: "--root=/"` is a file name; `join: "-"` is `args.bad-value`; `stdin` + `join` works; the `stdin` text never reaches the build side | |
| **M: mutants** | `scripts/join_mutants.py`, with `test_mutants_apply` checking that each applies. Sites: key equality (bytes vs value), the typed sign of `-0`, the empty rule, the chain order (head vs tail), the left padding width, semi duplicating, anti inverted, USING dropping the wrong column, the prefix, the order of columns, the bound off by one (`>` vs `>=`), the build counted twice, the shared budget not subtracted, `--join-where` applied to the probe, the pushdown of a build condition for `left`, a typed condition pushed, `max-join-rows` off by one, the build refusal before the probe, the swap repair offered for `left` | a mutant survives |
| **S: spikes** | S0: can a worker thread read a table the parent built (section 7.2). S1: accounted against resident for a `gmap` of 1M keys with side arrays. Both are scripts under `scripts/spikes/` with their numbers in this document before J6 and J1 are merged | (done: section 15) |

Gram (Linux x86-64) counterparts of EQ, IB, A, ST (with `strace`) and TH are required before merge and are **NOT RUN** here.

## 11. Staging

| slice | what | what it proves |
|---|---|---|
| **J0** | this document; spikes S0 and S1 (section 15: run, results below) | the design is falsifiable: the table can be shared, and the accounted bytes can be related to the resident |
| **J1** | `--join`, `--on` (text keys, no suffix), `--join-type inner`, `--join-prefix`, `join.column-collision`, csv x csv, rows out, `--max-join-rows`, the build bound with `limit.state-too-large` (no repair yet), `--threads` accepted and ignored | the semantics against the oracle (O), the defined order, the formula and the exact bound (A), the shared loops untouched (EQ, IB), the authority unchanged (AU) |
| **J2** | `left`, `semi`, `anti`; typed keys; composite keys; USING; padding. **Built: section 17** | the null-free semantics: padded cells and typed refusals (O-shapes), `anti` as the answer to "no match" |
| **J3** | `--where` split (probe/build/after), `--join-where`; `--group`, `--agg`, `--order-by`, `--top`, `--limit`, `--from` over joined rows with the shared budget; `--explain`. **Built: section 18** | the pushdown definition equals the unpushed one except for the key read (a test pair for each), the shared budget (A), the plan document (EXPL) |
| **J3b** | the query form, `semi`/`anti` contextual, the `query.unsupported` features. **Built: section 20** | Q-EQ, and that no query valid before changes |
| **J4** | standard input as the probe (the generated copy of the loop, `scripts/stream.py --check`), MCP `join` and `stdin`. **Built, with J1 to J3: sections 16 to 19** | ST, MCP |
| **J5** | jsonl on either side, `--join-delimiter`. **Built: section 21** | the reader contract holds for a join (O with jsonl sides) |
| **J6** | threaded probe, if S0 passes. **Corrected by section 15:** S0 passes for reading and fails for a worker's own output; J6 waits for the cancho change of 15.3, or is built as option (a) under a cap | TH |
| **J7** | the swap repair; the build keeps only the columns the plan names. **Built: section 22** | the repair is mechanical and tested; the pruned build is byte-identical (a pair of runs per plan) and measures the memory saved |
| **J8** (optional) | partitioned build | the same bytes, a faster 1M x 1M |
| **J9** | `--join-sorted` merge join. **Built: section 23** | the route past memory, a different order, its own gates |

Each slice is a pull request with its gates written first and failing against `main` (the practice of `docs/mcp.md` section 12.5), with its mutants, and each updates the "what it cannot do" sentences of the README and `docs/index.html` (`scripts/site.py` generates the rule counts).

## 12. The baseline, measured (2026-10-14, Mac, Apple M4 Max, 16 cores, 64 GB)

**Conditions.** One machine; **loaded** (load average 10.5 before and 11.0 after the benchmark run, 14.1 during the later `cut` run, from other sessions; not mine); `duckdb` 1.5.6 (Homebrew), `csvtk` 0.38.0, `table` the binary of `cancho-table-main/build/table` built from `fb4098d` (three commits behind `072ce15`, which changed
only JSON-lines typed group keys; the CSV group-by is untouched). Files from `jn/gen.py` (seed 7; in the session scratch, not committed): the probe `id,k,v` of 1,000,000 rows with `k` uniform over the build's keys, the build `k,name` with unique keys in random order; integer keys or text keys of
about 14 bytes (`u%08x-%d`); 1M x 100k: 24.7 MB (int) / 31.7 MB (text) probe and 1.3 / 2.0 MB build; 1M x 1M: 25.7 / 31.7 MB probe and 14.8 / 20.8 MB build. Every probe row matches exactly one build row, so the output is 1,000,000 rows of `id,k,v,name`.
**Before any timing, every contender's whole output was checked against a Python join** (sorted rows, md5, count), and the run stops on a difference: DuckDB at 1 thread and at its default, and csvtk, in all four cases, agreed. Timing: wall clock around the process, output to `/dev/null`
(DuckDB: `COPY (...) TO '/dev/null' (FORMAT CSV, HEADER)`; csvtk: `csvtk join -f k probe build`), the **minimum of 5** interleaved rounds, the median beside it. DuckDB reads with type detection on (its default; it does more work than `table` would); the exact SQL is
`SELECT a.id,a.k,a.v,b.name FROM read_csv(p) a JOIN read_csv(b) b ON a.k=b.k`.

| case | DuckDB 1 thread | DuckDB default | csvtk join | `table` group-count over the probe file (**proxy**, see below) | `table` group-count over the build file (**proxy**) |
|---|---:|---:|---:|---:|---:|
| integer keys, 1M x 100k | 0.264 (med 0.264) | 0.125 | 0.444 | 0.087 | 0.014 |
| text keys, 1M x 100k | 0.279 (0.281) | 0.121 | 0.461 | 0.098 | 0.015 |
| integer keys, 1M x 1M | 0.352 (0.353) | 0.170 | 0.979 | 0.190 | 0.197 |
| text keys, 1M x 1M | 0.374 (0.374) | 0.154 | 1.007 | 0.248 | 0.304 |

(seconds). Peak resident set, once per tool (`ru_maxrss`), MB: DuckDB 58 to 63 (100k) and 122 to 138 (1M), at 1 thread, and 3 to 4 MB more at its default; **csvtk 317 to 334 (100k) and 576 to 603 (1M)**; the proxy 19 to 21 (100k keys) and 115 to 173 (1M keys).

**What the proxies are.** `table` has no join. The closest thing it runs is `--group k --agg count --max-groups N --max-state-bytes 1073741824 --format csv FILE`: over the **probe** file it hashes 1,000,000 keys and finds an existing group for each (the lookups of a probe, plus the scan), over the **build** file it
inserts the keys (the inserts of a build). **They are not a join and are not a time a join will have**: a group-count also sorts and writes its groups at the end (the 1M-key cases include 1,000,000 groups sorted and written), keeps no row bytes, and a join also writes 1,000,000 rows of four columns. A scan that selects three
columns of the 1M probe and writes CSV (**measured, same conditions, the closest proxy for the probe's scan and the output's writing**) takes **0.056 s** (integer) and **0.058 s** (text) at one thread and 0.017 s at 8 threads.
**Estimate (not a measurement):** a first J1 at one thread lands between the larger proxy and the sum of the proxies plus the output, that is roughly 0.15 to 0.30 s for 1M x 100k and 0.25 to 0.50 s for 1M x 1M, which would be comparable to DuckDB at one thread (0.26 to 0.37) and
ahead of csvtk (0.44 to 1.0). The measured proxies say that **at 1M x 1M the build-side proxy (0.20 to 0.30 s) is as large as the probe-side proxy (0.19 to 0.25 s)**, which is why the parallel probe alone (J6) would not make that case fast.

**Targets (a choice, to be checked by the first slice's own measurement and stated as a result, not before):** at one thread, not slower than DuckDB's one-thread time on these four cases; resident memory under csvtk's on all four; and, after J6 on the 1M x 100k cases (a small build, a large probe), faster than DuckDB's default at 8 threads, where the probe's scan scales as
the `cut` proxy does (0.056 to 0.017 s).

## 13. Risks and open questions for the maintainer

| # | question or risk | recommendation |
|---|---|---|
| **Q1** | **Empty text keys: match (this design) or never match (SQL)?** A blank on both sides matches in csvtk and SQLite, not in DuckDB (measured, 5.3). Matching is consistent with "no null" and `--where k = ''`; not matching is what a SQL user expects and avoids a Cartesian blow-up of blank keys. | Match, with the note in `--explain`, the `limit.too-many-join-rows` bound as the guard, and `--join-where "k != ''"` as the written way to get SQL's behaviour. Revisit if the first users trip on it. |
| **Q2** | **Semi and anti in v1?** They are not SQL join keywords in every dialect (DuckDB and Spark have them), and they widen the surface by two words. | Yes. Without `is null`, `anti` is the only way to ask which rows have no counterpart, and both are cheaper than the others (no fan-out; keys only). |
| **Q3** | **A per-key fan-out limit (`--max-fanout`) as well as `--max-join-rows`?** | No in v1 (6.5); add it if a hot key in an unused build turns out to matter. It is additive. |
| **Q4** | **One budget (`--max-state-bytes` for build + groups + sort rows) or a second flag, and a higher default for joins?** The default 64 MiB refuses a 1M-row build (computed in 6.2, 91 to 103 MiB). | One budget (the flag stays one number to reason about). Keep the default; the refusal gives the number. Raise the default only after J1 publishes accounted against resident. |
| **Q5** | **Is a parallel probe worth waiting for the spike (S0)?** If a worker cannot read the parent's table, the options are a copy per thread (bounded to small builds), a cancho change, or the partitioned join. | Run S0 in J0 before J1 is reviewed. Recommend the cancho change if (b) fails: a read-only shared table is the shape of every future parallel lookup (a dictionary, a join, a `--in-file`). **Answered by section 15.2: (b) fails for the join; the change is drafted in 15.3 (a payload of one `&r` and one `&!r`).** |
| **Q6** | **A self-join, or any join where a file changes between the two opens.** The build is read, then the probe; nothing guarantees they see the same bytes. | State it in the help (done in 3.1); no mitigation, as for `--explain`. |
| **Q7** | **Unseeded hash and crafted keys.** `gmap.hash` has constants and no seed (`gmap.cho:67`), on purpose, for same bytes. A crafted build can make chains long. | Accept: the time of a pass is not a bounded resource anywhere in `table`; the group-by has the same property. Not mitigated in v1; say so in the skill. |
| **Q8** | **The probe side is the operand, so `table --join big.csv small.csv` (inner) builds the big file.** The tool never swaps; it refuses past the bound and offers the swap. | Keep; the alternative is an order that depends on file sizes. |
| **Q9** | **Left join padding is the empty text**, so a typed read of a padded cell refuses and a text condition cannot tell padding from a real empty. | Accept and document (8.1). A match indicator column (`--join-flag NAME`) is the additive remedy if it hurts; not in v1. |
| **Q10** | **Where the `anti`/`semi` words live in the query grammar** (contextual, needs `as`). | As in 2.3; the gate is that the corpus of queries translates as before. |
| **Q11** | **The rename feature does not exist** (no `docs/or-and-columns.md`, no open pull request on 2026-10-14). | The prefix rule of 4.1 is enough for v1 and is replaced, not extended, if renaming lands. *Update: slice B1 of `docs/or-and-columns.md` built `--rename`; the prefix rule of 4.1 was kept as it is (see there).* |
| **Q12** | **`semi`, `anti` and `inner` in the flag form are one flag with four values; a new join type later (`right`) changes the default's neighbours.** | Additive. |
| **R1** | The build is read whole before the first byte of output; a page of a huge join waits for the build (stated in `work`). | Accept. |
| **R2** | The estimated 0.25 to 0.50 s for 1M x 1M is an estimate from proxies; a first slice can be slower than the proxies suggest (the join writes four columns and holds rows). | The slices' gates are about bytes, not speed; speed is reported as measured. |
| **R3** | A join loop of its own adds code (the read loop, the scan, the generated stdin copy) and every added case in a shared function costs the others (the 15 percent finding). | The loops are separate and IB gates it. |

## 14. What was measured, and what was not

* **Measured (section 12 and 5.3):** DuckDB 1.5.6 at 1 thread and at its default, csvtk 0.38.0 `join`, and `table`'s group-count and cut as proxies, on four generated pairs of files, the Mac, minimum of 5 (answers of the three real joiners checked first against Python); peak resident set once; the equality behaviour of DuckDB, csvtk and SQLite on a nine-row case; `table`'s typed grouping of
  `007/7/-0/0/+7`, its refusal of an empty `:int` cell, its grouping of an empty text cell, and its skipping of a blank line in a one-column file; the current `args.unknown-flag` and `query.unsupported` answers to `--join` and `join`.
* **Computed:** the accounted bytes of section 6.2 from the files' bytes.
* **Estimate or choice:** every default (`--max-join-rows`, the 60 and 16 bytes, the cap of option (a), the 8 pairs), the 0.15 to 0.50 s expectations, the targets.
* **NOT RUN:** every Linux x86-64 measurement (gram was down): DuckDB is not installed there per `docs/bench-linux-duckdb.md`, but csvtk, the proxies, the thread scaling, the instruction counters, the `strace` gates, and the soak are all NOT RUN; no Linux claim is made. Also NOT RUN: any join in `table` (none exists; the spikes of section 15 are not a join), the oracle itself (it is specified in section 10 and
  written with J1), DuckDB's join with `all_varchar` on the timing files (the timing used type detection, as its default does), and a measurement under a quiet machine.

## 15. Spike results (S0 and S1, 2026-10-15)

> The two spikes of section 10 (row S) and 11 (J0), run before any join code. Programs, drivers and the raw output are in `scripts/spikes/join/` (`README` row in `scripts/spikes/README.md`):
> `jtab.cho` (the build table of 6.2, on `tools/table/gmap.cho`), `gen.py`, `s1_build.cho` + `s1.py` (S1), `s0/` + `s0.py` (S0), `results/s0.txt|json` and `results/s1.txt|json` (the committed runs).
> They are throwaway and are not part of the tool. Every figure below is **measured** unless it says *computed* or *reading*.

**Conditions (all of section 15).** One Mac: Apple M4 Max, 16 cores, 64 GB, macOS 26.2 (arm64). **Loaded by other sessions' work**: load average 6.8 to 8.5 on 16 cores during these runs (7.0 before S0, 6.8 before S1), so every time is an upper-ish
figure and the thread-scaling cells are noisy. Compiler `cancho` rev `e47d647` (the pin of `cancho.toml`, built from `../cancho-compiler-e47d647`), programs built with `--std`, edition 5 (as `gmap.cho`). Resident memory is the kernel's
`maximum resident set size` from `/usr/bin/time -l` (bytes), one process at a time. **Gram (Linux x86-64) was down: every Linux counterpart is NOT RUN and no Linux claim is made** (the allocator is different there; see 15.1).

### 15.1 S1: the accounted bytes of the build table against the resident set

**What was built.** `jtab.cho` is the table of 6.2 as written: a `gmap.Tab` of the distinct keys, per key `head` and `tail`, the held records concatenated in one buffer, per row `start` and `next`, every array growing by doubling
(as `gmap` does: a build does not know its row count). It counts `held` as it adds, by the formula of 6.2: for each row `record bytes + 16`, for each new key `key bytes + 60`. `s1_build.cho` streams a CSV file in 1 MiB chunks (the file is never held), takes
the key as the text before the first comma and the record as the whole line, and adds every data row. Three modes of the program were timed as separate processes: `scan` (read and split the lines, hold nothing: the **floor** of the reader), `build` (the table grown by doubling),
`presize` (the table made with room for exactly the keys, rows and record bytes of the file, so nothing grows). `verify` re-reads the file and probes every row's key: it must find its own row in its chain (all eight files: every row found).

**Files** (`gen.py`, seed 7, not committed; the same seed gives the same bytes): `k,a,b,c` with the key and three text columns of 18 to 22 bytes; unique keys in random order: `int_*` (a decimal integer, which is a *text* key at this slice: typed keys are J2), `text_*`
(`u%08x-%d`, about 14 bytes); 100,000 and 1,000,000 rows. Four more to find the worst case: `text_pow2` (2^20 rows: the row-indexed arrays have just filled), `text_pow2p1` (2^20 + 1 rows: every one of them has just doubled and is half empty), `wide_pow2` (100,000 rows
of 671 bytes whose records total 2^26 + 36 bytes, so the record buffer must double at the very end), `dup_100k` (100,000 rows over 10,000 keys: chains of 10).

**Accounted = the formula, checked.** `s1.py` computes the formula from the file in Python (sum of `record bytes + 16` over rows and `key bytes + 60` over distinct keys) and **stops if the program's own counter differs: they were equal on all eight files**, and so were the row and key counts.

**Minimum of 5 runs** per cell (processes, one thread; the spread of the five peak resident sets was under 0.13 percent on six files and about 2 percent on `text_pow2` and `dup_100k`). Reader floor (`scan`): 2.52 to 2.54 MB on every file.

| file | rows | accounted (bytes) | peak resident, grown by doubling | / accounted | peak resident, pre-sized | / accounted | build ms (incl. reading) | pre-sized ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `int_100k` | 100,000 | 15,076,063 | 26,689,536 | **1.77** | 17,809,408 | 1.18 | 6 | 4 |
| `int_1m` | 1,000,000 | 152,765,177 | 283,557,888 | **1.86** | 152,272,896 | 1.00 | 74 | 60 |
| `text_100k` | 100,000 | 16,874,484 | 27,803,648 | **1.65** | 19,611,648 | 1.16 | 6 | 5 |
| `text_1m` | 1,000,000 | 170,766,714 | 293,650,432 | **1.72** | 170,295,296 | 1.00 | 70 | 55 |
| `text_pow2` | 1,048,576 | 179,166,180 | 298,663,936 | **1.67** | 177,684,480 | 0.99 | 73 | 58 |
| `text_pow2p1` | 1,048,577 | 179,170,606 | 357,515,264 | **2.00** | 194,527,232 | 1.09 | 83 | 67 |
| `wide_pow2` | 100,000 | 76,197,790 | 146,096,128 | **1.92** | 78,954,496 | 1.04 | 16 | 11 |
| `dup_100k` | 100,000 | 9,157,215 | 20,692,992 | **2.26** | 11,812,864 | 1.29 | 5 | 4 |

(Ratios are of the resident set as it is, floor included. Net of the 2.5 MB floor: the grown tables are 1.50 to 1.98 times the accounted bytes and the pre-sized ones 0.98 to 1.07.)

**What the numbers say.**

1. **The formula describes what the arrays hold.** When nothing grows, resident memory net of the reader is 0.98 to 1.07 times the accounted bytes on all eight files (1.00 to 1.01 on the four 100,000-row files, where 2.5 MB is a large part of the total): the constants 16 and 60 of 6.2 are right
   for this layout, and gate A (the allocation held to the formula) is a gate that can be written and passed.
2. **The accounted bytes are not the resident set, and the gap is the doubling.** A build that grows its arrays by doubling peaks at **1.65 to 2.26 times** the accounted bytes (1.50 to 1.98 net of the floor), on these shapes. The excess is the half-empty arrays and the old copy
   alive while the new one is filled (resident peaks when an array doubles). The edge file shows how much one doubling boundary can cost: `text_pow2p1` has 4.4 KB more accounted bytes than `text_pow2` and 58.9 MB more resident (357.5 against 298.7 MB). The worst case measured is `dup_100k` at 2.26,
   a small, row-heavy table, where the 2.5 MB floor and the record buffer's doubling dominate. A worse case than the eight files may exist: the worst ratio was not searched for beyond these shapes.
3. **For comparison**, the group-by's gap in `docs/gap-groups.md:191-193` (14,000,000 accounted against 170 MB resident, 12 times) is much wider than this table's (at most 2.26 times here): the join build holds nearly all of its bytes in arrays the formula counts.
4. **A build is cheap.** The whole build of 1,000,000 rows, reading the file included, took 70 to 74 ms (min of 5); reading and splitting alone (`scan`) took 12 to 13 ms. This is a spike's reader (no quoted fields, no ragged rows, no `--join-where`), so it is a lower bound for a real build, not its time, but it is
   not the 0.2 to 0.3 s of the group-count proxy of section 12, which sorted and wrote 1,000,000 groups (correction noted in 7.2). Probing every row of the same file again (`verify`, a probe of all 1,000,000 keys plus the reading) took 36 to 39 ms on the 1M files.

**Verdict S1.** `--max-state-bytes` **is a usable guard and is not a bound on memory.** Usable: the count is exact (equal to an independent computation of the formula on eight files), deterministic, grows by the row, and so the refusal is at one byte at every thread count and is
the place `--explain` can name. Not a bound: on these eight builds the resident set was **1.65 to 2.26 times** the flag's number (and at the default 64 MiB, which is 67,108,864 accounted bytes, a build that stops just under it would hold something like 111 to 152 MB resident at those ratios:
*computed* from the measured ratios, not measured at that size). The flag's meaning stays what 6.3 says ("the accounted bytes the call holds in tables and rows"); **the help, the refusal's hint and the skill must say that resident memory can be about twice the number while the build grows**, and no document may
say the flag bounds resident memory. What would close the gap is a growth policy (not doubling every array; not measured here). **Not measured:** Linux (`glibc` malloc returns large blocks differently from macOS's, so the transient of a doubling may differ: NOT RUN on gram), keys longer than 16 bytes,
records over 700 bytes, builds over 1,048,577 rows, and any build through the real reader.

### 15.2 S0: can a worker thread read a table the parent built?

**Answer: yes to reading, and no to a worker that also holds its own state. The design's option (b) is not buildable for a join today.**

**The mechanism that works** (`s0/s0_shared.cho`). The parent builds the `JTab` of 15.1 (1,000,000 or 100,000 rows of a 16-byte key and a short record) and puts it in a `res struct Shared { tab, probe, n, threads }`; the probe keys (4,000,000 of 16 bytes, half of them in the build) are in a box of
the same struct. `borrow sh as &sr in { ... spawn(sr, f) ... }`: **one shared reference `&r Shared` is the entire payload of each `spawn`** (`threads.md` section 3: a shared reference may cross, and may cross to several threads at once;
`atomics.md` section 1.1 had measured the same for a struct with a box and a capability). The worker reads `sh.tab` through the reference (`contents(sh.tab.head)`, `gmap.find_h(sh.tab.keys, ...)`: the field of a shared reference is a shared reference) and answers one integer
(matches in the high bits, the sum of the build row numbers it found in the low 40). The parent adds the answers up and **every run compares them with its own sequential probe of the whole range: all equal** (7 repetitions in each of 5 processes per cell, 16 cells; the program counts the equal ones and the driver stops on any difference).
Writes through the shared reference are refused by the compiler (n4 below), so a data race on the table cannot be written. No copy is made: resident memory does not change with the number of threads.

The workers are eight functions, `w0` to `w7`, because the payload is one reference and carries no worker number: a worker's range can come only from its code (a thread count fixed in the program text). That is a limit of the mechanism, not of the measurement.

**Time and memory, 4,000,000 probe keys, minimum of 7 repetitions inside a process and then of 5 processes; ms; resident in MB (peak, build included); load average 7.0 to 8.5.**

| build rows | threads | shared `&r`: probe ms | speedup | resident | option (a), a copy in each job: total ms | speedup | resident |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100,000 | 1 | 56 | 1.00 | 81 | 72 | 1.00 | 199 |
| 100,000 | 2 | 29 | 1.93 | 81 | 45 | 1.60 | 212 |
| 100,000 | 4 | 14 | 4.00 | 82 | 32 | 2.25 | 259 |
| 100,000 | 8 | 9 | 6.22 | 82 | 25 | 2.88 | 313 |
| 1,000,000 | 1 | 81 | 1.00 | 232 | 104 | 1.00 | 422 |
| 1,000,000 | 2 | 52 | 1.56 | 232 | 78 | 1.33 | 532 |
| 1,000,000 | 4 | 28 | 2.89 | 232 | 54 | 1.93 | 753 |
| 1,000,000 | 8 | 15 | 5.40 | 232 | 61 | 1.70 | 1,174 |

The parent's own sequential probe of the same keys took 56 to 57 ms (100,000 rows) and 82 to 104 ms (1,000,000 rows) in the same runs; the build took 4 ms and 50 to 53 ms. The 2- and 4-thread cells at 1,000,000 are noisy under this load: **an earlier run of the same script, not committed, gave 42 and 22 ms** for the shared
probe there (1.98 and 3.77 times), the same 15 ms at 8 threads. Read those two cells as a range, not a point; the 8-thread cells agreed in the two runs. The probe here is a bare loop over fixed 16-byte keys, so these are the speed-ups of the table read alone, **not of a join**: a join's workers also scan, split and write rows.

**What does not work** (`s0/neg/`, each must fail to compile or `s0.py` stops; the compiler's first error, as printed):

| program | what it tries | refusal |
|---|---|---|
| `n1_ref_field` | a job struct `Job[&f] { sh: &f Shared, out: Box[[int]] }` holding the shared table beside the worker's own box | `a type declaration has no region parameters; only a function can take one` |
| `n2_ref_field_no_param` | the same field without declaring the region | `` `f` is not a region in scope; a region comes from a `[&f]` parameter or a `borrow` block `` |
| `n3_tuple_payload` | `spawn((sh, job), f)`: a payload of `&r Shared` and `&!s Job` | `` `spawn`'s payload has type `(&r Shared, &!s Job)`, which cannot cross to a real thread yet `` |
| `n4_write_through_shared` | a worker holding `&r Shared` writes into a box of the struct (to return rows) | `this is a shared slice `&`, which promises its elements will not change` |
| `n5_move_table_out_of_ref` | build a `Job` that owns the table from the reference (a second owner of the same boxes) | `expected `JTab`, found `&r JTab`` |
| `n6_slice_payload` | `spawn(contents(sh.probe)[0..16], f)`: the worker's own range as a slice payload | `` `spawn`'s payload has type `&r [byte]`, which cannot cross to a real thread yet `` |
| `n7_two_unique_one_table` | the table in a `Job` reached by `&!`, two workers on it | `the `&!` reference `j` reaches is lent to a thread that has not been joined, so it cannot be used again here; a `&!` is the only way to write its object, and two threads writing through copies of it race. Join the first thread before using it again` |
| `n8_deref_copy_of_res` | `let t = sh.tab;` as a value (a shallow copy of a `res`) | `expected `JTab`, found `&r JTab`` |

So the ways 7.2 left open for *one* payload to hold both the table and the worker's state are closed: a struct cannot hold the reference (n1, n2), a tuple cannot carry two (n3), a slice cannot be the payload (n6), the shared reference cannot carry the worker's output (n4), the
table cannot be duplicated by handle (n5, n8), and "the build table and the job in one struct reached by `&!`" gives one worker (n7). `thread-payloads.md` (compiler checkout) is the document that designs the missing piece, T-P1 (a tuple payload of leaves), and says it is not built; n3 is that
refusal.

**What does work today, and its cost.** (i) **A worker answers one integer** (the join result of a thread is one leaf): enough for a count, an existence test or a checksum of a join, not for rows. Each `spawn` and `join` costs **12.7 us** (`s0_spawn`: 100,000 in a row, minimum of 5 runs, one worker that probes one key),
so a design that returns one answer per probe row through the result is not an option (at 4,000,000 probe keys it would be 51 seconds). (ii) **Option (a), a copy of the table in each worker's job** (`s0/s0_copy.cho`, written as `par.cho` gives a worker its state: a `res struct Job` by one `&!`, one thread per level of a recursion, the job holds a deep
copy of the table, its range of the keys and its own output box, which only that worker writes): it works and every answer matched, with the columns of the table above. It costs resident memory per extra thread: (313 - 199) / 7 = **16 MB** at 100,000 rows and (1,174 - 422) / 7 = **107 MB** at 1,000,000 rows, against
none for the shared reference, and its speed-up flattens at 4 threads for the larger table (1.93 at 4, 1.70 at 8) while the shared read reaches 5.4. The 1 MiB cap of 7.2 was a choice and was not measured. (iii) **A partitioned join, option (c), is expressible today** (each worker's `Job` would own its `1/T` of the build and its output box, which `s0_copy` shows
a job can hold); it was not built or measured, and it needs the probe routed by hash and the output merged back into probe order, which 7.2 did not design.

**Verdict S0.** *Reading: PASS. Join (b) as 7.2 wrote it: FAIL.* J6 as `if S0 passes` does not pass; the fallback written in 7.2 applies (J6 is option (a) under a cap, and the request to cancho). Recommendation (unchanged from Q5, now with evidence): ask cancho for the change in 15.3, and do not build J6 until it exists or until
a real J1 build and probe say that the threaded probe is worth option (a)'s memory.

### 15.3 The request to cancho (a DRAFT; not filed upstream)

> **Title:** `spawn`: a payload of one shared and one unique reference (`thread-payloads.md` T-P1, with reference leaves)
>
> **What a program needs.** A parallel scan with a read-only lookup structure: `cancho-table` builds a hash table (a `res struct` of boxes) in the parent, then wants N workers that each (1) read that table, and (2) own a unique job of their own (a box to write their output into, a forked heap, a range).
> Today a thread's payload is one reference, so a worker gets either the shared table or its own state, never both. Measured on `e47d647` (`cancho-table` `docs/joins.md` section 15.2, `scripts/spikes/join/s0/`): one `&r Shared` as the payload reads the table from 8 threads with no copy and the
> right answers (5.4 times faster than one thread on 4,000,000 probes of a 1,000,000-row table); a job that owns a deep copy of the table works but costs 107 MB per extra thread; the combination is refused (`spawn`'s payload has type `(&r Shared, &!s Job)`, which cannot cross to a real thread yet; a type declaration has no region parameters).
>
> **Smallest change.** Let `spawn` take a **tuple of references** `(&r A, &!s B)` as its payload: at most one `&!` and any number of `&`, each region held until `join` as today (`threads.md` section 3, `aliasing.md` section 6.1). It is `thread-payloads.md` T-P1 with the reference leaves it already lists, no slices (T-P2) and no disjoint split (T-P3).
> The trampoline T-P1 describes unpacks the tuple; the result stays one leaf.
>
> ```
> fn work[&r, &s](p: (&r Shared, &!s Job)) -> [] int {
>     let (sh, job) = p;               // sh: &r Shared (read-only, many threads), job: &!s Job (only this thread)
>     contents(job.out)[0] = contents(sh.keys)[0];
>     return 0;
> }
> borrow sh as &r in {
>     borrow mut job as &!s in {
>         let f = work;
>         let t = spawn((r, s), f);    // r may be given to other spawns too; s to no other
>         ...
>         join(t);
>     }
> }
> ```
>
> **Why it is sound** (no new rule): the shared leaf is the case `spawn_thread_ids.cho` already accepts for several threads, the unique leaf is `spawn_struct_ref.cho`'s; they refer to different values, so the two lent regions are independent. **Gates to ask for:** accept: four workers share one `&r Shared` and each owns a different `&!` job box, results byte-identical to one thread; reject: a write through the `&r` (exists today: n4); reject: one `&!` in two spawns (exists: `spawn_two_copies_of_unique`); reject: a tuple with two `&!` of the same value; reject: the tuple's regions escaping `join`.
>
> **Alternatives, bigger:** region parameters on type declarations (`res struct Job[&f]`; `aliasing.md` section 3 route 2: refused today with the n1 message), which also lets one job carry the shared table; or a `frozen` read-only view of a `Box` that is `val` and can sit in any job until it is thawed after the join. Either is a larger language change than the tuple.
>
> **Not asked:** shared mutable memory, atomics, a queue (`atomics.md`).

### 15.4 What this changes in the plan, and the recommendation for J1

* **J1 is not blocked and needs no change to its design.** The flags, the formula (6.2), the exact bound (6.3), the order, the single budget (Q4) and `--threads` read on one core (7.2) stand. S1 confirms the constants 16 and 60 and gives the gap; S0 touches only J6.
* **Add to J1's gates:** gate A compares the program's counter with an independent computation of the formula on a set of files that includes the edges of the arrays' growth (`2^20` and `2^20 + 1` rows, a record buffer that doubles at the end of the file, a build of repeated keys), as `s1.py` does; and measures the peak resident set of the same
  builds and **publishes the ratio** (1.65 to 2.26 on the eight files of the spike, to be re-measured on the real build). The default (`--max-state-bytes` 64 MiB) is **kept** (Q4): the measured ratios do not argue for a different number, and a join on 1,000,000 rows of 70-byte records needs `--max-state-bytes` of about 153 to 171 million (*computed* by the formula, equal to the spike's counter).
* **Say it in the words the user reads:** the help for `--max-state-bytes`, the refusal's hint and the skill state that the number counts the bytes the join holds and that resident memory can be about twice that while the build grows.
* **Do not file the cancho request** without the maintainer's yes (the draft in 15.3 is text only). **J6** waits for it, or is built as option (a) under a cap if J1's measurements say the threaded probe is worth 16 to 107 MB per thread; **J8** (a partitioned build) is worth less than 7.2 said until a real build is timed (15.1, point 4).
* **Not changed:** the staging order, the gates, the defaults, Q1 to Q4 and Q6 to Q12.

### 15.5 What was measured, and what was not (section 15)

* **Measured:** everything marked as such above, on one loaded Mac, one compiler revision, minimum of 5 processes (S1) and of 7 repetitions in each of 5 processes (S0), one thread unless it says threads. The raw output of the committed runs is `scripts/spikes/join/results/`.
* **Computed:** the 111 to 152 MB at the default bound (the measured ratios applied to 67,108,864), the 153 to 171 million for 1,000,000 rows, the 51 seconds, the per-thread memory differences.
* **Not measured, or NOT RUN:** every Linux x86-64 measurement (gram was down); any real join, any build through the tool's reader (quotes, ragged rows, `--join-where`), typed keys; a growth policy other than doubling; a partitioned join; the option (a) cap of 1 MiB; thread counts above 8; a quiet machine; the worst ratio of resident to accounted over shapes other than the eight files.

## 16. J1 as built (2026-10-15)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions for all timings: one Mac (Apple M4 Max, 16 cores, 64 GB, macOS 26.2, arm64), **loaded by other sessions' work** (load average 8.5 to 9.4 on 16 cores during the runs), compiler `cancho` e47d647, DuckDB 1.5.6, csvtk 0.38.0. Gram (Linux x86-64) is down: **every Linux counterpart is NOT RUN** (the oracle and the accounting there, the instruction counter under `perf`, the `strace` run showing no thread for a join or a pipe, the soak) and no Linux claim is made.

### 16.1 What was built

`--join FILE --on PROBE=BUILD [--join-type inner] [--join-prefix P] [--max-join-rows N]`, csv or tsv on both sides, one key pair compared as text (the cell's bytes, its quotes undone), the inner join, rows out as csv, json or json lines, with `--select`, `--where`, `--limit`, `--from` and `--max-bytes` on the joined row. The probe is the operand or standard input; the build is the `--join` file, read whole before the first probe row. Output order is the probe's, then the build's. `--threads`, `--chunk-bytes` and `--parallel-min-bytes` are accepted and the join is read on one core (`--explain` says so). The code is in modules of its own (`jtab`, `jnflags`, `jnhead`, `jnfail`, `jnread`, `jnexplain`); `read_file` and `read_stream` are not edited, and their machine code is the same size as `main`'s (18,064 and 17,072 bytes in both binaries, `nm`).

The authority row is unchanged (`scripts/manifest.py --check`: `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`; the join only reads files). New rules: `join.column-collision` (exit 2, repair `choose` with `--join-prefix` and the stem of the build file's name when that makes names that do not collide) and `limit.too-many-join-rows` (exit 8); `column.unknown` and `column.ambiguous` also name a key of `--on` (`detail.flag "--on"`, `detail.input`); `limit.state-too-large` has `detail.holder "join-build"`.

### 16.2 Where J1 differs from sections 1 to 15 (every narrow reading)

1. **Scope.** J1 is the row of section 11: one text key pair, `inner`. `--join-type` exists with the one value (`left`, `semi`, `anti` are `args.bad-value` by the flag's `choice:inner`). `--on NAME` (USING), a type suffix (`k=id:int`), a second pair and the escapes' other uses are `args.bad-value` with the reason (J2). `--group`, `--agg`, `--order-by`, `--sort`, `--top` and `--report` with `--join` are `args.conflict` with the hint to pipe the join as csv (J3); `--input-format jsonl` with `--join` is `args.conflict` (J5); `--query` with `--join` is `args.conflict` and a `join` inside a query stays `query.unsupported` (J3b).
2. **Beyond J1's row, because the task asked:** `--explain` (section 3's row J3 has it; here `data.explain.join` and the join's `work`), standard input as the probe (J4), the MCP `join` property and the `stdin` + `join` call (J4), introspect, skill, schema, README and pages.
3. **`--where` is evaluated whole, on the joined row, after the join** (no split into probe, build and after, no `--join-where`): with text keys this is the same answer as the pushed form (section 8.1), and the split is J3.
4. **The header of a pipe is read before the build's rows** (section 3.1 said the build is read before the first byte of the pipe): a name both files have must be refused "before any read but the two headers", and the probe's header is its first line. The rows of the pipe are not read before the build is whole (tested with an endless producer of rows).
5. **`detail.input`** (`probe` or `build`) is on the rules a join raises or extends (`column.unknown`, `column.ambiguous`, `limit.state-too-large`, `limit.too-many-join-rows`); the rules of one file that a join reuses (`parse.csv-*`, `limit.line-too-long`, `limit.record-too-large`, `limit.too-many-rows` for the build, `value.*` of `--where`) name the file in `detail.path` (the build's path or the probe's, `-` for a pipe) and have no `detail.input`: `finish.explain` is shared with the loops that must not change, and a branch there changed register allocation in them before (section 1, R3).
6. **The refusal of a `--where` read names the probe row and line** as section 6.4 says, but the column is named in the joined header and there is no `detail.padded` (no left join yet).
7. **`--join` is resolved as a query's `from 'path'` is** (`qpath.resolve`, the rules of an operand, the same `path.*` and `io.*` rules), so the `retry` repair that an operand's `path.dotdot` offers is not offered for `--join` (there is no argument position in the text); the refusal is the same rule with no repair.
8. **The upper bound of `--explain` (6.3) is `2 * (size - header) + 76 * rows_at_most`, `rows_at_most = (size - header) / max(columns, 2) + 1`** (computed). The bound written in 6.3 leaves out that the keys are counted besides the records that hold them; it is not proved to be an upper bound (no tested file violates it, since `rows_at_most` is loose). `explain` says "may pass" or "cannot pass", never "will".
9. **There is no `refusals.static`** in the explain document (it has `refusals.possible` only, as before); the join's rules are listed there.
10. **The resident-to-accounted ratio is larger than the spike's** (16.3), so the help and the hint say "up to about three times", not "about twice" as section 15.4 proposed.

### 16.3 Gate A, as corrected by section 15, and the resident set of the real build

`tests/conformance/test_join.py` `Accounting`: the program's count of the accounted bytes (read from the refusal one byte below the bound, `detail.held`, which is the count before the row that did not fit) **equals the formula of 6.2 computed in Python from the file by another program** on: 2^20 and 2^20 + 1 rows of unique keys (98,566,144 accounted for the first), the spike's `wide_pow2` shape (100,000 records totalling exactly 2^26 + 36 bytes, so the record buffer doubles at the end of the file), 100,000 rows over 10,000 keys (chains of ten), quoted keys with doubled quotes, empty keys, CRLF and a BOM, every field quoted, no final newline, and a key in the second column. The bound is exact on each: accepted at the formula and refused at one byte below, naming the row, its line, the bytes read and the size of the file; and on a 5,000-row file at 1, 2, 6 and 16 threads and for a standard input probe. The eight files of the spike (`scripts/spikes/join/gen.py`, seed 7) gave **the same accounted bytes as the spike's own counter on all eight** (15,076,063 for `int_100k` and so on), through the tool's reader.

`scripts/join_rss.py` (min of 5, `/usr/bin/time -l`, the call is `table --join FILE` with a probe of one row at `--max-state-bytes` equal to the formula; the floor, the same call with a build of one row, is 2,031,616 bytes):

| file | rows | accounted | peak resident | / accounted | net of floor |
|---|---:|---:|---:|---:|---:|
| `int_100k` | 100,000 | 15,076,063 | 22,265,856 | 1.48 | 1.34 |
| `int_1m` | 1,000,000 | 152,765,177 | 280,838,144 | 1.84 | 1.83 |
| `text_100k` | 100,000 | 16,874,484 | 25,624,576 | 1.52 | 1.40 |
| `text_1m` | 1,000,000 | 170,766,714 | 291,913,728 | 1.71 | 1.70 |
| `text_pow2` | 1,048,576 | 179,166,180 | 296,894,464 | 1.66 | 1.65 |
| `text_pow2p1` | 1,048,577 | 179,170,606 | 361,267,200 | 2.02 | 2.00 |
| `wide_pow2` | 100,000 | 76,197,790 | 210,960,384 | **2.77** | 2.74 |
| `dup_100k` | 100,000 | 9,157,215 | 19,415,040 | 2.12 | 1.90 |

So **1.48 to 2.77 times** on these eight shapes, worse than the spike's 1.65 to 2.26 at the one shape that has a 64 MiB record buffer doubling at the end of the file (the old copy, 64 MiB, and the new one, 128 MiB, are both resident). The worst ratio over all shapes was not searched for. `--max-state-bytes` stays an exact guard on what the join holds, and **not a bound on memory**: the help, the refusal's hint, `--explain` and the skill say resident memory can be up to about three times the number while the build grows. A build just under the default 64 MiB would therefore be about 100 to 190 MB resident (*computed* from the ratios). The default is kept (Q4).

### 16.4 The other gates

* **O, the oracle** (`Oracle`, `Order`, `Keys`): 24 generated shapes (duplicates, no matches, all matches, big fan-out of 300 x 300, empty tables, look-alike keys `007`/`7`/`-0`/`0`/`1.5`/`1.50`, empty keys, quoted keys with commas, quotes and newlines, CRLF and BOM on either side, non-UTF-8 keys, one-column files, `#N`, same-named key columns with a prefix) and 60 random tables, each compared as rows and in order against a Python reference, **SQLite** (TEXT columns, binary collation) and **DuckDB 1.5.6** (`all_varchar=true`, an explicit `nullstr`, the column list given, no inference; skipped for the tables with non-UTF-8 bytes, which it refuses), as csv bytes, as json, from a file, from standard input, and at `--threads 4` with tiny chunks; the reference, SQLite and DuckDB agreed on every case before the tool was judged. The keys of J1 are text, so `007` and `7`, and `-0` and `0`, differ, and `--on k=id:int` is a refusal.
* **EQ**: the 166 plans of `scripts/corpus.py` on `main`'s binary (recomputed first; md5 of the list `fccb01b66dd7518d74611219a6c5aa29`) and on this branch's: the same 166 lines, byte for byte, at 1 thread and with tiny ranges.
* **IB** (`scripts/gate_regress.py --counter`, bound 1.01, 21 cells x {1, 4} threads, three builds a side; the compiler is deterministic, so the three builds of a side are the same bytes): **every cell 1.000** (the largest difference is 0.7 M instructions in 1.5 to 5.5 G). The `read_file` and `read_stream` functions are the same size as `main`'s.
* **ST**, **TH**: the probe from a pipe, byte for byte the file's, with `--threads` in {1, 4}, a pipe dripped a byte at a time and cut inside a quoted field, an endless producer of rows (the build is refused first), the refusals naming `-`; `--threads` 2 to 64 give the same bytes; `strace -e clone` is Linux only and **NOT RUN**.
* **F, fuzz**: 400 plans over random files (valid and junk flags, damaged files, files that share column names): no trap, every refusal tagged with a rule of the catalogue, every repair offered accepted by the flags (the collision's repair never collides again), 100+ accepted and 100+ refused runs.
* **EXPL**, **MCP**: `--explain` refuses what the run refuses before a row, with the same errors; the 14 path forms for `join`, `join: "--root=/"` is a file name, `join: "-"` is `args.bad-value`, `stdin` + `join` works and the text never reaches the build; the corpus of `test_mcp.py` has 16 join calls compared with the CLI byte for byte.
* **M, mutants** (`scripts/join_mutants.py`, `--check` in CI; 142 mutants now: the 63 of J1 re-anchored where the code moved, plus 79 of the typed read and its rules, the composite key, padding, semi, anti, the keys-only build and its bound, USING, the grammar of `--on`, the explain, and J1's own build and probe (`hold_text`, `probe_inner`)). **Run for real, the list of 146 of the first form: on the Mac, 137 killed; on gram, the same 137 and the same 9 survivors.** The 9: five were gaps of the tests, closed (the reasons given for a refused `--on`: a type before the equals sign and two types; a scale with a leading zero; the build position of a key in `--explain`; the counted sentence of a keys-only build) and re-run on both machines, **5 of 5 killed on each**; four are equivalent mutants, removed from the list with the argument in the script's docstring (a quoted typed cell read with its quotes: the reader's cell excludes the quotes and the status is the same; a key put in the table twice by a semi join: the first entry is the one found; the inner build taken for the general one: the same answers, held by the instruction counters; the `extra` of the explain's bound: unreachable by any file that can be written, kept because it is not proved). Two more survivors of the first run, a float key of more than 1,100 bytes and a refusal that named the other file's column, were closed by tests the same way.

### 16.5 Throughput (`scripts/join_bench.py`, min of 7 interleaved rounds, median in brackets, output to /dev/null, every contender's whole output checked against a Python join first)

| case | `table` | DuckDB, 1 thread | DuckDB, default | csvtk |
|---|---:|---:|---:|---:|
| integer keys, 1M x 100k | **0.159 s** (0.176) | 0.247 | 0.133 | 0.391 |
| text keys, 1M x 100k | **0.172 s** (0.185) | 0.286 | 0.125 | 0.404 |
| integer keys, 1M x 1M | 0.473 s (0.501) | 0.353 | 0.200 | 0.951 |
| text keys, 1M x 1M | 0.527 s (0.542) | 0.398 | 0.173 | 0.967 |

Peak resident (once per tool, MB): `table` 17, 21, 146, 189; DuckDB 73, 70, 112, 163 (1 thread); csvtk 344, 370, 623, 668. `table` selected `id,k,v,b_name` at `--max-state-bytes 1073741824` (the 1M builds are refused by the default, 6.2). **Against the targets of section 12:** not slower than DuckDB at one thread: **met on the two 100k cases, missed on the two 1M x 1M cases** (0.47 and 0.53 against 0.35 and 0.40 s); resident memory under csvtk's: met on all four; the faster-than-DuckDB-at-8-threads target belongs to J6. The 1M x 1M cases are the build's: the estimate of section 12 (0.25 to 0.50 s) held for the upper end, and section 15.1's 70 ms for the spike's build was a lower bound, as it said. Not measured: a quiet machine, Linux, build sides above 1,048,577 rows, other widths.

## 17. J2 as built (2026-10-15)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions: one Mac (Apple M4 Max, 16 cores, 64 GB, macOS 26.2, arm64), **loaded by other sessions' work** (load average 10 to 50 on 16 cores during the runs), compiler `cancho` e47d647, DuckDB 1.5.6, SQLite 3.53.4, csvtk 0.38.0 where named; and gram (Linux x86-64, 16 cores, Python 3.14.4, valgrind, `perf_event_paranoid` 4: no instruction counter, no DuckDB), where the gates say so. Where a figure is from gram it says "gram".

### 17.1 What was built

`--join-type inner|left|semi|anti`; `--on` is a comma list of 1 to 8 pairs, each `PROBE=BUILD` or one `NAME` that both files have (the shared-name form, USING), with an optional type at the end of the pair, `:int`, `:dec(S)` (S from 0 to 18) or `:float`, which reads both cells as that type. The rest is J1's: rows out as csv, json or json lines, `--select`, `--where`, `--limit`, `--from`, the probe on standard input, one core.

* **Left**: the inner join, and a probe row with no build row once, in its place, with every build cell the empty text. The padded record is the probe record and one delimiter for each build cell shown.
* **Semi** and **anti**: the probe row, once, with its own cells, if its key has a build row (semi) or has none (anti). The build holds the keys alone (`jtab.add_key`): no row, no chain.
* **Keys**: one pair of text is the cell's bytes, read in place as in J1; one typed pair is the 8 bytes of the value (the value plus 2^63, big endian: `007` and `7`, `-0` and `0`, `1.5` and `1.50` at `:dec(2)`, the spellings of one double, are one key); several pairs are each component preceded by 4 bytes of length (`jnkey.cho`), so `(ab, c)` and `(a, bc)` are two keys. A typed cell that is not of its type, an empty one included, is refused by the rule the rest of the tool uses (`value.not-integer` and the others), with `detail.context "join"`, `detail.input` (`probe` or `build`), `detail.pair` (from 1), `detail.column` (by the name its own header gives it), `detail.row` and `detail.line` of its own file, `detail.value`, and `scale`, `digits` or `direction` where the rule has them. The build file is read first (its rows in file order, the pairs of a row left to right), then the probe.
* **USING**: `--on id` is `id=id`, and the build's `id` is left out of the joined header and cells (`--on id:int` types it; the probe's spelling is shown). `--on id=id:int --join-prefix r_` shows both.
* **Padding**: a typed read of a padded cell is refused like any empty cell, and the refusal says `detail.padded: true` when the cell is a build column of a padded row (and says nothing of the kind for a real empty cell of a matched row, or for a cell of the probe).
* Code: `jnkey.cho` (new), and changes to `jnflags`, `jnhead`, `jnread`, `jtab`, `jnfail`, `jnexplain`, `explain` (the `join` member and its notes), `finish` (the rule texts and the `padded` detail) and `spec` (the help of the flags). **The authority row is unchanged** (`scripts/manifest.py --check`).

### 17.2 Where J2 differs from sections 1 to 16 (every narrow reading)

1. **A type goes at the end of a pair only.** `--on k:int=id` is `args.bad-value` (a type before the equals sign), as are two types, an unknown type, `:dec` with no scale or a scale above 18 or with a leading zero, a type in capitals, an empty pair, an empty name, and a ninth pair. The section 2.1 grammar says "an optional single suffix at the end of the pair"; the rest is this slice's reading of it.
2. **A position alone is not a shared name.** `--on '#2'` is `args.bad-value` (write `#2=#2`): `#N` is a position in the file's own header, so it names no column that two files share, and silently choosing one meaning would be a guess.
3. **`--join-prefix` with `semi` or `anti` is `args.conflict`.** Section 4 says those joins show the probe header only; a prefix would name nothing, and a flag that is accepted and ignored is the thing this tool refuses.
4. **The guard of 5.3 does not exist in J2.** `--join-where` and the split of `--where` are J3. In J2 `--where` runs after the join, and the key is read first, so the refusal of a typed key cell teaches what J2 has (write the pair without its type, or correct the file) and not the guard.
5. **USING holds the whole build record.** The column is left out of the joined header and cells, not out of the held record, so the accounted bytes are the same with and without USING (section 6.2's formula), and the memory saved by holding fewer columns is J7's. **Corrected by section 22:** J7 holds fewer columns, and the accounted bytes are those of the record held (reading 2 there), so a plan that does not name the column of a shared-name key, or any other column, holds and counts less.
6. **Accounted bytes, the formula as built.** Inner and left: `sum over held rows (record bytes + 16) + sum over distinct keys (key bytes + 60)`, as before, with `key bytes` the stored key: the cell (one pair of text), 8 (one typed pair), `sum (4 + component)` (several pairs; a typed component is 8). Semi and anti: `sum over distinct keys (key bytes + 44)` (4 + 24 + 16: a length, the entry of three integers, the slots at the minimum load; no `head` and `tail`), and a key already held costs nothing. `detail.rows_held` of the refusal is 0 for them.
7. **`--max-join-rows` counts the rows produced**, padded rows and the rows of a semi or anti join included. `detail.matches` is the build rows of the probe row's key for inner and left, 0 for a padded or an anti row, and **1 for a semi row** (the build keeps no chain, so the exact count is not known; 1 is the least it can be).
8. **The upper bound of `--explain`** gains `extra`: 12 bytes for each pair when a pair is typed or there are several (0 for one pair of text, J1's bound unchanged), and for a keys-only build `body + (44 + extra) * rows`. `data.explain.join` has `type`, one key entry per pair with `type` `text`, `int`, `float` or `dec(S)`, `using` (the shared names for inner and left; empty for semi and anti), `output_columns`; `refusals.possible` adds the `value.*` of the key types; the notes say what an empty key, a typed key and a padded cell are.
9. **The refusals of a typed key do not go through `finish.explain`'s tally of `--where`** (they are `jnfail.typed_key`), and the `padded` detail is carried into the `--where` refusals by `err_fn` 77 (the context stays `where`).
10. **The paths of J1 are kept as they were.** The first J2 probe was one function for every shape, and it cost the inner join of one text pair **+59 instructions a row (2.5 percent, gram, callgrind)**, over the bound of 1.01; J1's probe and build hold are now functions of their own (`probe_inner`, `hold_text`), chosen once per run, and the general ones (`probe_one`, `hold_row`) take every other shape.
11. **The flag table is scanned by every lookup**, and J2's longer help makes it 8.7 percent longer (18,450 to 20,055 bytes). `toolbox.cli.index_of` is 94 percent of the instructions of a join of one row (about 1.1 million instructions for each of 54 lookups), so a one-row join costs about 12 million instructions (gram, callgrind) more than before and a non-join call about 0.33 million more. This is a fixed cost, not a cost per row; the lookup is in the contract package and is not changed here.

### 17.3 The gates

Written before the code; on `main`'s binary (J1, `0105a87`) `tests/conformance/test_join_j2.py` fails (582 failures and 9 errors in the first run, when it had 54 tests) and passes on this branch (56 tests; the whole suite of 908 tests passes on the Mac after the merge of `origin/main` at `a3af5ec` (slice A1 of `docs/or-and-columns.md`, `or` and parentheses; `test_reaper`, the explain documents and the join oracle included)).

* **O, the oracle** (`join_ref2.py`, `Oracle`): 32 shapes, each for `inner`, `left`, `semi` and `anti` (128 cases), 80 random tables, and the first 6 shapes with a prefix, each compared as the multiset of rows **and** in the defined order (probe order, then build order) against a Python reference of the definition, **SQLite 3.53.4** (`ORDER BY p.rn, b.rn`, a typed key by `CAST`) and **DuckDB 1.5.6** (`all_varchar=true`, a `nullstr` no cell has, no inference, a typed key by `CAST(... AS HUGEINT | DECIMAL(18,S) | DOUBLE)`), as csv bytes, as json, from a file, from standard input, and at `--threads 4` with 7-byte ranges. The reference, SQLite and DuckDB agreed on every case before the tool was judged (DuckDB is skipped for the tables with non-UTF-8 bytes, which it refuses). Shapes: duplicates on both sides, no matches, every row matching, big fan-out (300 x 300, and `007` against `7` 200 x 200), header-only and empty tables, empty text keys, look-alike keys as text, awkward quoted keys, CRLF and a BOM, one-column files (a left join pads one cell), non-UTF-8 keys; `007`/`7`/`+7`/`-0`/`0` at `:int`, `1.5`/`1.50`/`01.5` at `:dec(2)`, nine spellings of one double at `:float`, `2^63 - 1` and `-2^63`; two pairs of text, `("ab","c")` against `("a","bc")`, text and int, int and dec(2) and float in different columns, eight pairs, the same probe column in two pairs; USING with text, typed, composite, with a prefix, with nothing of the build left to show, and with the key in another column.
* **The typed refusals** (`TypedRefusals`): every cell of the table of edge cells of `docs/numbers.md` (`numbers_ref.SPEC`, 45 cells) as a key at `:dec(2)` and `:float`, on each side, for `inner` and `anti`: the rule is the table's, with `context "join"`, the side, row 1, line 2 and the column; the integer edges (`2^63`, `-2^63 - 1`, `+0007`); an empty cell (a refusal for a typed key, a key for text); the build before the probe, rows in file order, the pairs of a row left to right and the failing pair named; `scale`, `digits` and `direction`; a padded cell read as `:int`, `:dec(2)` and `:float` says `padded`, and a real empty cell of a matched row, a cell of the probe and an inner join do not.
* **A, accounting** (`Accounting`): the program's count of the accounted bytes (`detail.held` of the refusal one byte below, with the row, line and bytes read of the row that did not fit) equals the formula computed in Python by `join_ref2.accounted` for: two typed pairs (`:int`, `:dec(2)`) on **2^20 and 2^20 + 1 rows** for `inner` and `semi`; one `:int` pair with 700 distinct keys over 5,005 rows (`007`, `7` and `+7` one key, `-0` and `0` one key) for the four types at 1, 2, 6 and 16 threads and for a pipe; `:float` and `:dec(2)` keys; two text pairs with empty components for the four types at 1, 2, 6 and 16 threads; the keys-only build over 5,000 rows of one key (`len + 44`, and the formula's `inner` against it); USING (the same bytes as without); empty builds. Accepted at `N`, refused at `N - 1`, every time. **The resident set of the real build** (`scripts/join_rss.py --shape`, min of 3, `/usr/bin/time -l`; the program's count was checked against the formula first, on every file): 

  | shape | files | resident / accounted | net of the 2.0 MB floor |
  |---|---|---|---|
  | text, inner (J1's, re-measured) | 8 files of 16.3 | 1.48 to 2.77 (`text_100k` 1.52 to 1.66 in two runs) | 1.34 to 2.74 |
  | `:int` pair | `int_100k`, `int_1m` | 1.42, 1.87 | 1.28, 1.86 |
  | two pairs of text | 7 files (`wide_pow2` has one column too few) | 1.34 to 1.84 | 1.23 to 1.83 |
  | left | 8 files | 1.48 to 2.75 | 1.34 to 2.73 |
  | semi (keys only) | 8 files | 1.65 to 6.29 | 1.61 to 2.77 |

  The semi ratios over 2.8 are builds of a few hundred kilobytes (`dup_100k`: 499,950 accounted bytes, 3.1 MB resident, of which 2.0 MB is the process); net of the floor the range is 1.61 to 2.77, `text_pow2p1` being the worst (the doubling boundary again). The help, the refusal and the skill keep saying "up to about three times".
* **EQ, non-join plans unchanged**: the 166 plans of `scripts/corpus.py`, md5 recomputed on a fresh build of the new `main` (`a3af5ec`): list md5 `fccb01b66dd7518d74611219a6c5aa29` (the same as J1 recorded); this branch gives the same 166 lines, byte for byte, at 1 thread and with tiny ranges, **on the Mac and on gram**.
* **IB, instructions** (measured on the tree of the commit `072e2bd` and after it; the one later change to code, in the group sink, is not on any path these cells run; `scripts/gate_regress.py --counter`, Mac, bound 1.01, 21 cells x {1, 4} threads, three copies a side): **every cell 1.000** (the largest ratio is 1.0004), against `main` at `a3af5ec`. The two functions J1's cost depends on were separated to keep it so (17.2, item 10).
* **Per-row cost of the inner join, before and after** (`scripts/join_perrow.py`, Mac, instructions retired, slope between probes of 250,000 and 1,000,000 rows against a build of 100,000 integer keys): **2,247.8 to 2,258.8 instructions a probe row (1.0049)**; per build row (builds of 100,000 and 400,000): **1,203.3 to 1,205.6 (1.0019)**; both within the bound (against `main` at `a3af5ec`; against `0105a87` the same script gave 1.0061 and 1.0097). **On gram, by callgrind** (`scripts/join_callgrind.py`, `perf_event_paranoid` 4: no counter; the per-row cost is the difference of the counts at 2 sizes, 20,000 and 60,000 rows): the 17 non-join cells of G6 **1.0000** (identical per-row cost), the inner join of one text pair **1.0004 to 1.0005** (+1.0 instruction a probe row of 2,087 to 2,533), a build row **0.9924** (1,224 to 1,215), against `main` at `a3af5ec` and the final tree. Before the separation of 17.2 item 10 the same measurement gave 1.0230 to 1.0276. The shapes J2 adds, per probe row (gram): left 2,749, semi 1,838, anti 1,232, `:int` pair 2,785, two text pairs 2,944, USING 2,317; per build row: semi and anti 1,222, left 1,215, `:int` pair 1,683, two text pairs 1,801.
* **ST, TH**: the probe from a pipe gives the file's bytes for every case above; `--threads` 2 to 64 with chunks of 1 to 4 MiB give the same bytes for the four types; `--explain` says `effective 1` and the note. **On gram `strace -f` (`Strace`)**: a join of any type, from a file and from a pipe, at `--threads 4` with tiny ranges, starts no thread (the control, a threaded `--select` of the same file, does), opens no socket and opens no file for writing.
* **F, fuzz**: 500 plans over random files with the new alphabet (types, pairs, USING, junk `--on`, damaged files, files that share names): no trap, every refusal tagged with a rule of the catalogue, every repair offered accepted and, for the collision, not colliding again; more than 100 accepted and 100 refused.
* **`or` and parentheses on joined rows** (A1): `--where "(v:int = 1 or w = 6) and k != 'q'"` works on the joined row of a left join, padded cells included, and on an anti join (`test_or_and_parentheses_in_where_work_on_the_joined_row`); the "cannot do" lines of the README and the page no longer say "no or" (A1 changed them) and say what a join cannot do.
* **EXPL, MCP, AU**: `--explain` refuses what the run refuses before a row, with the same errors; the MCP corpus has 9 join calls more (all four types, a typed pair, USING, two pairs, the refusals), compared with the CLI byte for byte; `scripts/manifest.py --check` and the embedded manifest are unchanged in labels (`args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`).
* **M, mutants** (`scripts/join_mutants.py`, 147 mutants: the 63 of J1 re-anchored where the code moved, and 84 of the typed read and its rules, the composite key, padding, semi, anti, the keys-only build and its bound, USING, the grammar of `--on` and the explain; `--check` in CI): 

### 17.4 Throughput (taken on the branch before the merge of A1, whose edits are to `--where` and `--query` and leave the join loops as they were: the counters of 17.3 were re-taken after it; `scripts/join_bench.py --type`, min of 7 interleaved rounds, median in brackets, output to /dev/null, every contender's whole output checked against a Python join first; Mac, load average 11 to 12 on 16 cores; `table` selects `id,k,v,b_name` at `--max-state-bytes 1073741824`; one probe key in five is in no build row for left and anti)

| case | `table` | DuckDB, 1 thread | DuckDB, default | csvtk |
|---|---:|---:|---:|---:|
| **left**, integer keys, 1M x 100k | **0.156 s** (0.179) | 0.255 | 0.140 | 0.422 |
| left, text keys, 1M x 100k | **0.191 s** (0.207) | 0.289 | 0.127 | 0.447 |
| left, integer keys, 1M x 1M | 0.476 s (0.499) | 0.355 | 0.200 | 0.965 |
| left, text keys, 1M x 1M | 0.520 s (0.526) | 0.405 | 0.178 | 0.996 |
| **anti**, integer keys, 1M x 100k | **0.084 s** (0.088) | 0.162 | 0.098 | none |
| anti, text keys, 1M x 100k | **0.100 s** (0.103) | 0.183 | 0.094 | none |
| anti, integer keys, 1M x 1M | 0.321 s (0.325) | 0.234 | 0.133 | none |
| anti, text keys, 1M x 1M | 0.350 s (0.357) | 0.270 | 0.127 | none |
| inner (J1's path, same files as 16.5), integer keys, 1M x 100k | 0.162 s (0.167) | 0.248 | 0.136 | 0.421 |
| inner, text keys, 1M x 100k | 0.188 s (0.200) | 0.291 | 0.125 | 0.445 |
| inner, integer keys, 1M x 1M | 0.543 s (0.552) | 0.357 | 0.204 | 1.027 |
| inner, text keys, 1M x 1M | 0.560 s (0.566) | 0.405 | 0.176 | 1.018 |

Peak resident (MB, once per tool): left `table` 20, 21, 146, 189 against DuckDB (1 thread) 56, 68, 113, 164 and csvtk 333 to 684; anti `table` 12, 15, 85, 116 against DuckDB 52 to 146. **Against the targets of section 12:** not slower than DuckDB at one thread: met on the two 100k cases for left and anti (0.156 against 0.255, 0.084 against 0.162), missed on the 1M x 1M cases (0.476 against 0.355, 0.321 against 0.234; as J1's inner); resident memory under csvtk's: met. The inner rows are a re-run of J1's cases on the loaded machine of today: the 1M x 1M figures are 15 percent over 16.5's (0.543 against 0.473), the instruction counts of 17.3 say the code of that path is the same (+0.6 percent a probe row, +1.0 percent a build row), and the load of the machine is the explanation I can offer and have not shown. DuckDB at its default threads is faster than `table` everywhere except anti on 100k keys at one thread count of equal order; no claim is made about it.

### 17.5 What was not measured, and what did not run

* **Linux for the oracle's DuckDB** (not installed on gram; the SQLite and Python references ran there), any quiet-machine timing, any throughput on gram (callgrind counts instructions and says nothing of the time), builds above 1,048,577 rows, other widths, other shapes than the ones listed, the worst ratio of resident to accounted over all shapes.
* The instruction counter of gram (`perf_event_paranoid` 4, not changed): replaced by callgrind, as above.
* Not built, as the design says: `--join-where`, the split of `--where`, `--group`, `--agg` and `--order-by` over a join (J3), the query form (J3b), jsonl sides (J5), the threaded probe (J6), the swap repair and the pruned build (J7).

## 18. J3 as built (2026-10-15)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions: one Mac (Apple M4 Max, 16 cores, 64 GB, macOS 26.2, arm64), **loaded by other sessions' work** (load average 43 to 82 on 16 cores during the runs: the instruction counts do not depend on it, the times do, and nothing in 18.4 is a quiet-machine figure), compiler `cancho` e47d647, DuckDB 1.5.6, SQLite 3.53.4, csvtk 0.38.0; and gram (Linux x86-64, 16 cores, Python 3.14.4, valgrind, `perf_event_paranoid` 4: no instruction counter, no DuckDB; a `lex-social` soak was running on it, so every job was `nice` and on cores 0 to 5) where a figure says "gram".

### 18.1 What was built

`--join-where EXPR`; the split of `--where` into the part applied to the probe row before its key is read, the part applied to the build row before it is held, and the rest; `--group`, `--agg`, `--sort`, `--order-by`, `--top`, `--limit`, `--from` and `--rename` over the joined row (with `--join-prefix`, `--select`, USING, the four join types), the build and the groups (or the rows held to sort) sharing the one `--max-state-bytes`; `--explain` (`join.where`, `join.join_where`, `join.build_state.shared_with`); the guard of 5.3 as the repair of an empty typed key; `detail.padded` in every context. `--report` with a join is still `args.conflict` (8.5).

Code: `jnrun.cho` (the run, moved out of `jnread`), `jn3pass.cho` (the build, the probe and the group sink of J2 **as copies** with the additions, used only by a plan that has one of them), `jnwhere.cho` (the leading run, `--join-where` read against the build header), `jnguard.cho` (the guard), and changes to `jnflags`, `jnfail`, `jnexplain`, `explain`, `finish`, `orparse` (`top_units`), `wrepair` (`refuse_for`), `spec`, `table`. `jnread` keeps the loops of J1 and J2 untouched, which is why the per-row cost of a plan without J3 is the same (18.3). **The authority row is unchanged** (`scripts/manifest.py --check`).

### 18.2 Where J3 differs from sections 1 to 17 (every narrow reading)

1. **Scope.** The row of section 11, nothing else: no query form (J3b), no jsonl side, no threaded probe, no swap repair.
2. **The leading run (8.1, point 1) is made exact.** Take the units of the top-level `and` list of `--where` (the one unit when it is a single condition, a group or an `or`). The run is the units, from the first, that are **bare conditions with no type suffix**, a negated one (`a not in (...)`, `a not contains x`, which came with slice A2 after this document) included, with the negation applied; it ends at the first unit that is typed, is a group in parentheses, is `not (...)`, or is the whole of a top-level `or` (so a plan whose top level is an `or` pushes nothing). This is the doc's "with `or`/`not` only the pure-conjunction leading text conditions are pushed". A condition names one column, so "a condition that names both sides" cannot occur.
3. **Which side, and the left join.** A condition of the run on a column of FILE is probe-side for every join type. One on a column of the `--join` file is build-side **for an inner join**; in a left join it stays after the join, and **it does not end the run** (text conditions commute, so a probe-side condition after it is still pushed). A semi or anti join has no build column in its header, so the doc's "inner and semi" is the inner join in effect.
4. **The whole `--where` is still evaluated on the joined row**, the run's conditions again (they are true there). So the numbering of a refusal of a condition after the run, `detail.row` and the `err_cond` are what they were.
5. **What a pushed condition changes: three things, not one.** The doc names the key read. The pair tests found two more: **`--from` and `--max-join-rows` count the joined rows that the run lets through** (a probe row the run rejects produces no joined row, so it is not counted; likewise a build row it rejects is not held). The cursor `next.from` is a place in that sequence. `--where "(c)"` (a parenthesised unit ends the run) is the plan without the push; the pairs of 18.3 give the bytes of both.
6. **The build phase's order:** ragged row, then `--join-where`, then the build-side conditions of the run, then the key (docs/joins.md 6.1 had the first and the last).
7. **`--join-where` names the build file's own columns** (`bg`, `#2`; the prefix is not part of them; a column a shared-name key leaves out can be named), takes the whole grammar, is read (syntax: `where.syntax` or `where.mixed-and-or`, both with `detail.flag "--join-where"`, the second offering the two readings of that argument) in the check of the flags, before a file is opened, and resolved against the build header with the keys (`column.unknown` and `column.ambiguous` with `detail.flag "--join-where"` and `detail.input "build"`, `column.type-conflict`). A build cell that a typed condition cannot read is the rule of the rest of the tool with `detail.context "join-where"`, the build file's `path`, `row` and `line`, no `detail.input`, and a hint that names `--join-where`.
8. **The shared budget, as built.** The groups (or the sorter) are given `--max-state-bytes` minus the accounted bytes of the build, which is read first. Groups: for each new group, the key cells each as 4 bytes of length and the cell's (unquoted) bytes, 12 for a typed one (8 and 4), plus `8 * 73` for each `:float` sum or mean; sorted rows: the joined record's bytes and `8 * (2 + 2 * keys)` for each row (`sorter.add`). **The joined record the sorter holds is the record the answer shows**: the build columns a shared-name key leaves out are not in it, a padded row is the probe record and one delimiter for each build cell shown, a quoted cell keeps its quotes. A refusal past it is `limit.state-too-large` with `detail.holder` `groups` or `sort`, `detail.build_bytes`, `detail.held` (the holder's own count before the item that did not fit), `detail.limit` (the flag) and `detail.path`. `--max-groups`, `--max-distinct` and `--max-sort-rows` are unchanged.
9. **Row numbers.** A refusal of the groups, the aggregates or the sort key, like one of `--where`, names the probe row and line (the joined row has no number of its own); one of `--join-where` names the build row and line.
10. **The guard of 5.3 is the repair of an empty typed key, and only of an empty one.** `choose` with one option, the invocation with `KEY != ''` added to `--join-where` (the build) or put before the rest of `--where` (the probe: the run is applied before the key is read); an expression the flag already had goes after it in parentheses (`KEY != '' and (OLD)`), and the option is offered only if the result is in the grammar. `KEY` is the name when it is one word of letters, digits and underscores that is not a keyword and that no other column of the header has, `'quoted'` otherwise, and `#N` when the header has the name twice. A cell that is not empty has no repair. For a left join the probe-side guard drops those probe rows: it is an option, not an action. The hint no longer says the version has no filter.
11. **`detail.padded`** is carried in every context: `err_fn + 100` in the tally for a padded cell (a column from the probe's width on, in a left join's padded row), read by `finish.explain` (`context_of` and the three `padded` details). J2's `77` is `99`.
12. **`--explain`.** `data.explain.join` gains `where` (`probe_before_join`, `build_before_build`, `after_join`: the units of `--where` as written, in order; `null` when the probe is standard input, whose header is not read), `join_where` (the units of `--join-where` as written) and `build_state.shared_with` (`null`, `"groups"` or `"sorted rows"`); `work.reads` says every probe row for a grouping or a sort; `refusals.possible` adds `limit.too-many-groups` and `limit.too-many-sort-rows`; the notes say what the run did before the join, that the budget is one number, and (for a left join) that `--join-where` keeps the probe row where `--where` drops it. The schema is `scripts/schemas.py`.
13. **Threads.** Unchanged: a join is read on one core and `--explain` says so (`work.threads`).
14. **Tests of J1 and J2 that were J3's absence** now say what J3 does: `--group`, `--order-by`, `--top` and `--sort` are taken; `--report` is not; the tests that counted rows "before `--where`" with a leading text condition write it in parentheses (reading 5).
15. **A cost J3 pays, found by the instruction counter.** The flag table is scanned by every lookup, so a flag added early costs every call: the first form of the help (a sentence in `--where` and one in `--max-state-bytes`) cost a trivial call **+2.5 M instructions (7 percent)**; the sentences are in `--join`'s help now (a flag late in the table), and a trivial call costs **+0.16 M** (37,231,372 to 37,389,991, Mac, `time -l`). `--join-where` itself is late in the table.

16. **The catalogue cannot say it.** `introspect.rules` and `docs/refusals.md` have one line for a rule, and say `never` for `value.not-integer`, `value.not-decimal` and `value.not-float`. The guard of reading 10 is a `choose` repair of those rules for an empty key cell of a join, and `none` for every other cell: a context the line does not hold (the test of the catalogue compares one refusal and one summary for a rule, so a `sometimes` for it would need the one refusal to be the join's and its hint the summary's). Not changed.

### 18.3 The gates

Written before the code, in their own commit (`755c5c9`), failing on `main`'s binary: `tests/conformance/test_join_j3.py` (68 tests: 653 failures and 18 errors) and the updated tests of J1 and J2 (5 and 2 failures).

* **O, the oracle** (`join_ref3.py`, `Oracle`): 16 table pairs (duplicates, no match, a left join that pads, empty probe, empty build, both empty, fan-out, the key not in the first column, empty group cells, empty cells of the int column, a typed int key with `007`/`7`/`+7`/`-0`/`0`, two pairs of text, the same names with a prefix, USING with a prefix, 300 x 300 fan-out, cells with commas, quotes and line breaks), each for **inner, left, semi and anti**, with 5 random plans each (an `--join-where`, a `--where` with `and`, `or`, `not` and parentheses, typed and guarded conditions, groups of 0 to 2 columns with `count`, `sum`, `min`, `max`, `distinct`, `--sort`, `--top`, rows with `--select`, `--order-by` of one or two keys, `--limit`, `--from`, `--rename`) and 250 more plans over all of them, compared with the Python reference as rows, as csv bytes, as json, from standard input and at 4 threads with 7-byte ranges; for the 320 first, **SQLite and DuckDB** (the join in a derived table `ORDER BY` the row numbers, `--join-where` as the `ON` clause, `--where` as `WHERE`, `COALESCE` for the padding because the tool has no null, `HAVING count(*) > 0` for an aggregate over no row) answered the same before the tool was judged. Refusals are predicted by the reference (rule, context, column, value, `padded`, row, line, path) and compared. **Before any J3 code the reference was checked** against SQLite and DuckDB (two runs, 1,057 and 530 plans with DuckDB, no difference) and against the tool's own non-join `--where`, `--group`, `--order-by` on the joined table written as a file (main's binary: **2,179 answers and 129 refusals agree**, 22 plans whose `--join-where` refuses skipped).
* **The pairs** (`Pairs`): for probe-side and build-side conditions, a left join, `or`, `not`, `not in`, `in`, `contains`: the plan with the condition in the run and the plan with it in parentheses give the same bytes (inner and left, with `or` and `not`), `--explain` shows which went where (9 shapes of what ends the run), and the one named difference is a pair of its own: an empty typed key is removed before its key is read (probe side, then build side) and refused in the twin, a left join does not push the build condition so it refuses; and reading 5 (`--from`, `--max-join-rows`).
* **The pipe** (`PipeEquivalence`, 160 plans): `join --format csv | table <the rest>` equals the one call, byte for byte, refusals included.
* **A, accounting** (`Accounting`): the program's count of the groups' bytes and of the sorted rows' bytes, with the build's, equals the reference's trace; **accepted at `build + groups` (or `+ sort`), refused one byte below with the holder, `build_bytes`, `limit` and `held` equal to the formula, and refused at the build's own bytes minus one as `join-build`**, for 15 shapes x 4 join types x 3 plans (a text key, two keys, a typed `:int` key at 12 bytes, a `--join-where`, a pushed condition), the sorted rows of the same, USING (the dropped column is not in the record) and padding, at 1, 2, 6 and 16 threads and for a pipe, and **on the edges: a build of 2^20 and of 2^20 + 1 rows under a group** (the build by `join_ref2.accounted`, the groups by hand). **Resident against accounted, re-measured on the real run** (`scripts/join_rss.py --shape group`, min of 3, Mac, load average 43 to 70, the program's own count checked against the formula first on every file): `int_100k` 2.57, `int_1m` 2.54, `text_100k` 2.25, `text_1m` 2.41, `wide_pow2` 2.91, `dup_100k` 2.14 (the build plus one group for each key; the floor is 2,260,992 bytes); the build alone was 1.48 to 2.77 (16.3). The help, the refusal and the skill keep saying "up to about three times".
* **EQ, non-join plans unchanged**: `scripts/corpus.py` (166 plans): md5 of the list **`fccb01b66dd7518d74611219a6c5aa29`** on a fresh build of the new `main` (`0a1b7dc`), and the same 166 lines, byte for byte, on this branch, **on the Mac and on gram**.
* **IB, instructions** (measured on the tree of the commit `072e2bd` and after it; the one later change to code, in the group sink, is not on any path these cells run; `scripts/gate_regress.py --counter`, Mac, bound 1.01, 21 cells x {1, 4} threads, three copies a side): **every cell between 0.999 and 1.001** (largest 1.001), against `main` at `0a1b7dc`. **Per row of the inner join** (`scripts/join_perrow.py`, Mac, instructions retired, slopes between 250,000 and 1,000,000 probe rows against a 100,000-row build, and builds of 100,000 and 400,000): probe row **2,257.37 to 2,260.02 (1.0012)**, build row **1,206.18 to 1,214.29 (1.0067)**. **`read_file` and `read_stream` are the same size** as `main`'s (18,064 and 17,072 bytes, `nm`). **Gram, callgrind** (`scripts/join_callgrind.py`, per-row cost as the difference of the counts at 20,000 and 60,000 rows, `perf_event_paranoid` 4: no counter): the 17 non-join cells of G6 **1.0000** (identical to the hundredth of an instruction, 1,508.81 to 5,769.18 a row), the inner join of one text pair **1.0000** (2,087.95 and 2,338.59 a probe row), a build row **0.9999 to 1.0000** (1,214.68 to 1,214.62), against `main` at `0a1b7dc`; a trivial call costs 15 thousand instructions more in total (the flag table). A plan with a `--where` on the probe that J3 applies before the join costs **2,533.03 to 2,771.46 a row (1.0941), for the record and not judged**: the condition is evaluated before the key is read and again on the joined row (reading 4), and this one rejects almost nothing; a condition that rejects rows saves the rest of the row's work. New, a probe row: `--join-where` 2,338.25, `--group b_name --agg count,sum:v` 2,597.51 (inner) and 2,846.85 (left), `--order-by v:int` 4,843.02.
* **ST, TH**: the probe from a pipe gives the file's bytes for every plan of the oracle; `--threads` 4 with 7-byte ranges the same; `--explain` says `effective 1`. **Gram `strace -f`** (`Strace`): a group over a join at 4 threads with 7-byte ranges starts no thread (the control, a threaded `--select` of the same file, does), opens no socket and opens no file for writing; J2's `Strace` still passes.
* **F, fuzz** (`Fuzz`, 500 plans): no trap, every refusal tagged with a rule of the catalogue, every repair offered accepted by the flags (`join.column-collision`, `where.mixed-and-or` of both flags, the guard); more than 100 accepted and 100 refused.
* **MCP** (`join-where` among the properties; 12 calls added to the corpus of `test_mcp.py`, compared with the CLI byte for byte), **EXPL** (the refusals before a row equal the run's), **AU** (`scripts/manifest.py --check`), `site.py --check`, `seo_check.py`, `manifest`, `schemas` and `mcp --check`: pass.
* **M, mutants** (`scripts/join_mutants.py`, 223 mutants (225 in the first run), 83 new: the leading run, the sides, the negation, the filter of the build, the groups' bound, the holder, the padded flag, the sorter's record of USING, the guard's name and parentheses, the explain; `--check` in CI; the 20 of J1 and J2 whose site moved to `jnrun` re-anchored). **Run for real, first on the list of 225: on the Mac the 103 that J3 adds or moved (82 killed, 21 survived); on gram all 225 (204 killed, the same 21 survived, and none of the 122 of J1 and J2 that did not move).** The 21: **19 were gaps of the tests, closed** (a quoted cell that holds a quote, a record over several lines, a shared-name key leaving a column out of a condition of the run; the mix repair of `--join-where` in both forms and its flag; the guard's name (a quote, a keyword, a digit, a space, an eight-level expression, a key not in the first column, `--where=EXPR`); the scale repair beside a `--where`; the hint, facts and scale of a refusal of `--join-where` and of a padded decimal key; the limits the explain lists; the sorter's record under a shared-name key, with nothing of the build left, and its quotes), and **2 were equivalent** (a list of the typed aborts for the padded flag: `err_col` is set by a refused cell and by nothing else on that path, so the list could not differ; removed with its two mutants). Re-run on the final tree: **Mac, 19 of 19 killed** (16, then the last 3 after their tests); gram: **19 of 19 killed** too. No mutant of the final list survives on either machine; the full list of 223 was not run again in one piece on the final tree (the 19 were re-run, and the other 204 had been killed by tests that were only added to).

### 18.4 Throughput of a group-by over a join

`scripts/join_bench.py --type group` (min of 5 interleaved rounds, median in brackets, output to /dev/null, every contender's whole output checked against a Python group-by first, `table` also in the order of the names' bytes). The files are the baseline's (section 12); the query is `SELECT b.name, count(*), sum(a.v) ... GROUP BY b.name` (about 100,000 groups for the 100k builds and 632,000 for the 1M builds); `table --join build --on k=k --join-prefix b_ --group b_name --agg count,sum:v --max-groups 1000000 --max-state-bytes 1073741824`. **Mac, load average 57 before and 53 after on 16 cores: not a quiet machine, so the times are upper figures for every contender alike and are not publishable.**

| case | `table` | DuckDB, 1 thread | DuckDB, default |
|---|---:|---:|---:|
| integer keys, 1M x 100k | 0.512 s (0.578) | 0.178 | 0.130 |
| text keys, 1M x 100k | 0.489 s (0.597) | 0.232 | 0.137 |
| integer keys, 1M x 1M | 1.041 s (1.068) | 0.332 | 0.191 |
| text keys, 1M x 1M | 1.002 s (1.015) | 0.369 | 0.190 |

Peak resident (once, MB): `table` 31, 34, 256, 266; DuckDB (1 thread) 86, 78, 163, 213. **`table` is 2.7 to 3.1 times DuckDB's one-thread time on these four cases**. The group sink is the one of `read_file` (`add_fast` with a memo) fed from `jn3pass`; no tuning was done. **Gram: NOT RUN** (no DuckDB there, and a `table`-only figure would compare with nothing).

### 18.5 What was not measured, and what did not run

* Any quiet-machine timing; the sort over a join in a time or a resident figure; a build above 1,048,577 rows other than the pow2 edge test; other widths and key shapes than the ones listed; the worst resident-to-accounted ratio over all shapes; DuckDB on Linux (not installed on gram).
* Not built, as the design says: the query form (J3b), jsonl sides (J5), the threaded probe (J6), the swap repair and the pruned build (J7).

## 19. The staging table against what `main` does (2026-10-16, `origin/main` at `ca33910`)

> **Measured, not read.** The binary was built from `ca33910` with the pinned compiler (`cancho` e47d647, `nice cancho build`, 9 s) and every row below was checked by running it. The fixture is two tiny files in a scratch directory (the commands are given as they were typed, in that directory):
>
> ```
> a.csv  = id,name,v / 1,ann,10 / 2,bob,20 / 2,bo2,25 / 3,cy,30        b.csv = id,city / 1,x / 2,y / 2,yy / 4,z        b.jsonl = {"id":"1","city":"x"} / {"id":"2","city":"y"}
> ```
>
> "built" means the row of section 11 is done as written; "partly" says what is there and what is not; "not built" means the command shows the refusal. The MCP rows were run through `scripts/mcp_client.py` against a server built with `scripts/mcp.py build` and through `tests/conformance/test_mcp_stdin.Join` and `test_mcp.py -k oin` (5 tests, OK). The gram counterparts that J1 to J3 report (the `strace` runs, the oracle's counters) are in sections 16 to 18 and were not repeated here.

| slice | status | what `main` does (the command that shows it) |
|---|---|---|
| **J0** the document and the spikes | **built** | this file (sections 0 to 14); `scripts/spikes/join/` holds `s0.py`, `s1.py`, `gen.py` and their results (`results/s0.txt`, `s1.txt`), reported in section 15 |
| **J1** `--join`, `--on` (text), `inner`, `--join-prefix`, `join.column-collision`, csv x csv, rows out, `--max-join-rows`, the build bound `limit.state-too-large` (no repair), `--threads` accepted | **built** | `table --join b.csv --on id=id --join-prefix b. --format csv a.csv` gives `id,name,v,b.id,b.city` and the 5 inner rows in probe order then build order. `table --join b.csv --on id=id,v=id --format csv a.csv` is `join.column-collision`. `table --join b.csv --on id --max-state-bytes 10 a.csv` is `limit.state-too-large` (`detail.holder` `join-build`, `input` `build`, `held` 0, `build_row` 1) with `repair.kind` `none`. `--max-join-rows` is in `introspect` (`rules`: `limit.too-many-join-rows`; `limits`). `table --join - --on id a.csv` is `args.bad-value`. |
| **J2** `left`, `semi`, `anti`; typed keys; composite keys; USING; padding | **built** | `--join-type left` pads the row of `3,cy,30` with an empty `city` (`3,cy,30,`); `semi` gives `1,ann,10 / 2,bob,20 / 2,bo2,25` (probe columns only); `anti` gives `3,cy,30`; `--on id:int` and `--on id=id,v=id:int` are read; `--on id` is the shared-name form (the header is `id,name,v,city`, `id` once). The bisected bound of the build (`--max-state-bytes`, accepted/refused) is 260 bytes for the inner join and 135 for `semi` and `anti` on this fixture, which is the keys-only build of J2 |
| **J3** `--where` split, `--join-where`; `--group`, `--agg`, `--order-by`, `--top`, `--limit`, `--from` over joined rows with the shared budget; `--explain` | **built** | `table --join b.csv --on id --group name --agg count --join-where "city != 'z'" a.csv` gives the groups `ann 1`, `bo2 2`, `bob 2`; `table --join b.csv --on id --threads 4 --explain a.csv` has `data.explain.join` with `keys`, `build_state`, `where` (`probe_before_join`, `build_before_build`, `after_join`), `join_where`, `order` and `work` |
| **J3b** the query form, `semi`/`anti` contextual, the `query.unsupported` features | **not built** | `table --query "select a.id from 'a.csv' as a join 'b.csv' as b on a.id = b.id"` is `query.syntax` at offset 8 (the dot); `... from 'a.csv' join 'b.csv' on id = id` is `query.unsupported`, feature `join`, at 23; `... from 'a.csv' semi join 'b.csv'` is `query.syntax` at 23 (`semi` is not a word of the grammar); `table --query "select id from 'a.csv'" --join b.csv --on id` is `args.conflict` (`--query --join`). **This pull request builds it (section 20).** |
| **J4** standard input as the probe, MCP `join` and `stdin` | **built**: nothing remains of the row | `cat a.csv \| table --join b.csv --on id --format csv -` gives the file's bytes; `scripts/stream.py --check` says `read_stream is what read_file makes`; the MCP tool schema has `join`, `on`, `join-type`, `join-prefix`, `join-where`, `max-join-rows` and `stdin`, and a call `{"stdin": "id,name,v\n1,ann,10\n3,cy,30\n", "join": "b.csv", "on": "id", "join-type": "left", "format": "csv"}` gives `id,name,v,city / 1,ann,10,x / 3,cy,30,`; `{"file": "a.csv", "join": "b.csv", "on": "id", "join-type": "semi", "format": "csv"}` works; `stdin` with `join` makes the text the probe and the file under `--root` the build (`test_mcp_stdin.Join`: 2 tests); the paths of `join` (`..`, absolute, links, `-`) are the forms of `file` (`test_mcp.py`, class `Join`: 3 tests); `join: "-"` is `args.bad-value`; `{"file": ..., "stdin": ..., "join": ...}` is `mcp.input-conflict`; `{"stdin": ..., "join": ..., "explain": true}` works and names the source `-`. The `strace -e clone` run is Linux only and was reported on gram in sections 17 and 18; it is not repeated here. What the row never claimed: a query with `join` (J3b) and a jsonl probe (J5) |
| **J5** jsonl on either side, `--join-delimiter` | **not built** (**this pull request builds it: section 21**) | `table --join b.csv --on id --join-input-format jsonl a.csv`, `--join-delimiter ';'` are `args.unknown-flag`; `table --join b.jsonl --on id --input-format jsonl --fields id,name a.csv` and the same with a jsonl probe are `args.conflict` (`--join --input-format`: "a join reads csv or tsv on both sides in this version"). `--delimiter` (and `tab`) applies to both sides |
| **J6** threaded probe | **not built** | `--threads 4` is accepted and `--explain` says `work.threads` `{"requested":4,"effective":1,"reason":"the join is read on one core"}`; waits for the change of cancho in 15.3 |
| **J7** the swap repair; the build keeps only the columns the plan names | **not built** (one thing the row does not ask for is there; **corrected by section 22: built**) | `limit.state-too-large` has `repair.kind` `none` and `data.explain.join.build_state.swap` is `{"offered":false,"reason":"the swap repair arrives with a later slice"}`; the held bytes of an inner or left join do not depend on `--select` (the bound is 260 bytes with no `--select`, with `--select id` and with `--select city`), so the build holds the whole record. The keys-only build of `semi` and `anti` (135 bytes) is J2's, not J7's |
| **J8** (optional) partitioned build | **not built** | no flag, no code; `--explain` has no `partitions` |
| **J9** `--join-sorted` merge join | **built (section 23)** | `table --join-sorted --join b.csv --on id a.csv` joins two files sorted by `id`; a file out of order is `join.not-sorted` |

What remains of each row, after this pull request builds J3b: J5 (the build side's own reader flags and a jsonl probe or build), J6 (the threaded probe, blocked on cancho), J7 (the swap repair as a `choose` option of `limit.state-too-large`, and the pruned build; **corrected by section 22: built**), J8 (J9 is built, section 23). **J4 has nothing left in its row**; what the design says of it in 7.2 (the threads) is J6's.


## 20. J3b as built (2026-10-16)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions: one Mac (Apple M4 Max, 16 cores, 64 GB, macOS 26.2, arm64), **loaded by other sessions' work** (load average 12 to 22 on 16 cores during the runs: the instruction counts do not depend on it, the times do, and nothing here is a quiet-machine time), compiler `cancho` e47d647, SQLite 3.53.4 (Python `sqlite3`); and gram (Linux x86-64, 16 cores, Python 3.14.4, valgrind, `perf_event_paranoid` 4: no instruction counter; a `lex-social` soak was running on it, so every job was `nice` and on cores 0 to 5, one job at a time) where a figure says "gram". The base of every comparison is the binary of `origin/main` built from the same compiler (`ca33910`, J3 merged; the pull request `page-video` that merged after it changed README, `docs/index.html` and the site scripts, not the program).

### 20.1 What was built

The join in `--query`:

```
select a.id, b.city from 'p.csv' as a [inner|left|semi|anti] join 'b.csv' as b on a.id = b.id [and a.j = b.j] [and b.x > 1]
  [where ...] [group by ...] [order by ...] [limit N] [offset M]
```

It is translated to the flags of J1 to J3 and to nothing else: `--join 'b.csv' --on id=id[,j=j][:type] --join-type T [--join-prefix 'b.'] [--join-where 'x > 1']`, then the select, where, group, agg, sort, order-by, rename, limit and from of any query. The translation is kept in the plan (`query.jx`, six texts: `--join`, `--on`, `--join-type`, `--join-prefix`, `--join-where`, `--where`), and every place that read one of those six flags reads them through `jxsrc.cho`, which answers the flag's own value when the call is not a query (so a call with flags reads exactly what it read). The query front end: the lexer reads a `.` as a token (`qlex`); `qparse` scans the head of the statement before it reads the select list (`scan_head`), reads the second table, the key equalities and the conditions of the `on` (`join_head`, `on_clause`, `key_pair`), qualifies every column (`column`, `after_dot`), and keeps the first refusal that does not stop the parse (`note`, `settle_pending`); `qtrans` writes the joined row's names (`decode`: the second table's alias, a dot, the name) and the six sections 8 to 13 of the translation; `qtext` holds the words of the new refusals. The reference front end (`tests/conformance/query_ref.py`) was extended in the same way and is the oracle of every gate below.

**Features** (`query.unsupported`, `detail.feature`): `right join`, `full join`, `cross join`, `left outer join`, `more than one join`, `a condition on the left table in on`, `standard input as the build side` (the seven of 2.3), and four the doc did not list: `or in on`, `column-position-in-join`, `table-star` (`a.*`) and `a column of the right table of a semi or anti join`. The feature `join` stays for a join that is not in the new form (no `as`), with a hint that says the form.

Code: new `jxsrc.cho` (the six flags, from the flags or from a query); changes to `qlex`, `qarena`, `qparse` (1,351 lines), `qtrans`, `qtext`, `qrepair`, `query` (one field), `inputs`, `jout` (the field), `jnflags`, `jnhead`, `jnfail`, `jnrun`, `jnexplain`, `spec` (the help), `finish` (two rule summaries), `table` (`plan_body` takes the join text; the flags of a join beside a query are `args.conflict`). Every file is under 2,000 lines (`table.cho` 1,902). `read_file` and `read_stream` are not edited and their machine code is the size it was (18,064 and 17,072 bytes in both binaries, `nm`); the binary is 36,368 bytes larger (1,188,648 to 1,225,016). **The authority row is unchanged** (`scripts/manifest.py --check`: `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`).

### 20.2 Where J3b differs from sections 1 to 19 (every narrow reading)

1. **Scope.** The row of section 11, nothing else: the query form, `semi` and `anti` contextual, the features. No jsonl side (J5), no threaded probe (J6), no swap repair or pruned build (J7).
2. **What is a join statement.** The tokens after the first `from` are a source (`'path'` or `stdin`), `as`, a name, and a join word (`join`, `inner`, `left`, `right`, `full`, `cross`, or `semi` or `anti` when `join` follows). Anything else is read as it always was: `select a from 'f' join 'g' on a = a` is still `query.unsupported` `join` at the word, `select a from 'f' as x` is still `query.syntax` at the `as`. This is why no query that was valid changes: a join statement has an `as` after its source, which was a syntax error. Only the **hint** of the features `join` and `subquery` changed (it said there were no joins); `test_or_a1_compat`, `test_not_a2_compat` hold the status and the rule of those cases and `test_join_j3b.Compat` the new hint.
3. **The dot.** `.` is a token only when the query has one of `join inner left right full cross` as a bare word outside its quotes (it is worked out once, `qlex.joined`); otherwise the lexer refuses it at the dot as it always did. In a statement that is not a join statement a dot is refused at the first dot, with the old words (so `select a.b from 'f' join 'g'` is `query.syntax` at the dot, as before, and the three pinned `join` refusals keep their offsets). A lexical error after an accepted dot is told after it: in a query that has a join word, `select a.b from 'f' join 'g' where c = 'x` is refused at the unterminated string, not at the dot (the one change of order, in queries that were refused).
4. **`semi` and `anti`** are join words only between the alias and `join`; they are not reserved, and stay names, aliases and `as` names everywhere else (`select semi from 'f'`, `from 'f' as semi`, `select a as anti ... order by anti`). `Compat` holds 18 such queries, among 385 answers of `main`.
5. **Every column is `alias.column`.** A bare name is `query.syntax` (`expected`: "a column of a join is written alias.column") at the name; **the one exception is the `as` name of a select item in `order by`** (`select a.name as n ... order by n`), because an answer's own label is the only bare name a join query can use, and the translation of it is `--sort`. A typed bare alias (`order by n::int`) or `n.x` is not that exception. `#N` is `query.unsupported` (`column-position-in-join`, also after a dot): a position of the joined row is not a name of one table. `a.*` is `table-star`. A name after the dot is a name (bare and no keyword, or quoted): `a.order` is refused as a keyword is.
6. **Aliases.** A bare word that is no keyword or a "quoted" name; the two must differ (compared as names: a doubled quote is one); they are case-sensitive; `as` is required on both tables (no alias without `as`). An alias that is no table's, in any clause, is `query.syntax` at the alias ("the name of one of the two tables of the join"), told when the second alias is known from the head; with a head that is malformed the head's own refusal is told.
7. **The order refusals are told in.** A refusal that does not stop the parse (a column not written `alias.column`, an alias that is no table's, a position, a condition on the first table in the `on`, a column of the second table of a semi or anti join outside the `on`) is **noted**; the parse goes on; at the end the **earliest of the noted one and of the one that stopped the parse is told, a tie going to the noted one**. So `select id, c.id ...` is told at `id`, and `select c.id from 'p.csv' as a left outer join ...` at `c.id`, not at `outer`. `query.mixed-and-or` still comes before everything, as before; a mix inside the conditions of an `on` is told with no repair (the two readings are of a where clause).
8. **The on clause.** Key equalities joined by `and`, then conditions joined by `and`. An equality is `column = column`, one of each table, in either order (`b.id = a.id`), both with the same `::type` or `cast(... as ...)`, or none; at most 8. A condition is a unit of the where grammar (`b.x > 1`, `b.x in (...)`, `not b.x = 1`, a group in parentheses that may hold `or`), over the second table alone; typed conditions are allowed (`b.n::int > 5` is `--join-where 'n:int > 5'`). Refusals, all `query.syntax` at the start of the offending unit: the first unit is not an equality ("a key equality first"), an equality with both columns of one table, two types, more than 8, an equality after a condition. A top-level `or` is `query.unsupported` `or in on` (parenthesise the alternatives after `and`); a condition on the first table is `a condition on the left table in on` (at the alias). The columns of `--join-where` are the second table's **own** names (no prefix), as the flag takes them.
9. **The translation.** `--join-type` is always written (`inner` for a bare `join`); `--join-prefix` is the second alias and a dot for `inner` and `left`, and **not given** for `semi` and `anti` (the flag refuses it there: they have no second-table column), so **a column of the second table outside the `on` of a semi or anti join is `query.unsupported`** (`a column of the right table of a semi or anti join`: it could only be `column.unknown`, and the query says why). `select *` is `--rows`. The names of the joined row are the first table's own and `b.name` for the second's. In `--where` a second-table column is written bare (`b.city`) when the alias and the name are plain words that are not `and`, `in` or `contains`, else `'b t.city'`; in `--select`, `--group`, `--agg`, `--order-by`, `--sort` as every name is written for its flag.
10. **Literals.** A literal that does not fit the type of its condition is refused by `--where`'s or `--join-where`'s own parser and told as `query.syntax` at the literal of the query, with `detail.column` the column's **own** name (`n`, not `b.n`), `type` and `value`. The literals of the `on` are mapped at an offset of 1,000,000 in the translation's map (`qtrans.jw_base`), so that the one mapping serves both clauses.
11. **The flags beside a query.** `--on`, `--join-type`, `--join-prefix`, `--join-where` and `--join` with `--query` are `args.conflict` (`detail.flags` lists them); `--max-join-rows` and `--max-state-bytes` are bounds and stay flags, as `--max-state-bytes` does for a query. `--max-join-rows` with a query that has no join is `args.required-flag`, as with no `--join`.
12. **The first table from `stdin`.** `from stdin as a join 'b.csv' as b on ...` is the probe from standard input. `from ... join stdin as b` is `standard input as the build side`. A `from` beside an operand is `args.conflict`, as ever.
13. **MCP.** The server maps `file` and `stdin` to the operand and always requires one of them, so a query that names its source with `from` (a join query always does) has two: `args.conflict`, or `mcp.input-missing` with neither. This is the rule of every query with a `from` there, not new; the server's way to a join is its properties `join`, `on`, `join-type`, `join-prefix`, `join-where` (J4).
14. **Repairs.** A refusal that has a correction (a `like`, the readings of a mix in a `where`) offers the invocation with the flags of the whole join (`--join`, `--on`, `--join-type`, `--join-prefix`, `--join-where` written in); the refusals of the new grammar offer none. The guard that the typed-key refusal teaches (5.3) is a `choose` of invocations with a flag added, so in a query it is `none` (the reason says so); `join.column-collision` in a query has a prefix given and offers none.
15. **`--explain` is the explain of the flags**, byte for byte: the document of a query and of its flags are the same text (`test_join_j3b.Refusals.test_the_explain_of_every_pinned_translation_is_the_explain_of_its_flags`, and for generated plans `Equivalence.test_the_explain_documents_are_the_same`). No stated difference was needed.
16. **A cost J3b pays, found by the instruction counter.** The flag table is scanned by every lookup, so text added to a flag early in it costs every call: the first form of the `--query` help (1,210 bytes about the join) cost a trivial call **+95,270 instructions and a trivial join call +5.4 M (6.5 percent)**; the sentences are in `--join`'s help now (late in the table), the `--query` help has one short sentence, and the join code makes **no lookup it did not make before** (the lookup of `--join-prefix` for a collision is made only when one happens). Now a trivial flag call is **-16,227** instructions (35,343,235 to 35,327,008, Mac, `time -l`, minimum of 3), a trivial non-join query **+79,510 to +94,575** (0.20 to 0.24 percent: the head scan, the dot check) and a trivial join call **+395,363 to +531,491** (0.48 to 0.55 percent: the longer help of `--join`).
17. **An empty file name.** `join ''` is `path.empty` (the rule `from ''` has), where the flag `--join ''` is `args.bad-value` at the parse of the flags (a text flag takes a non-empty value): the one refusal that the two forms word differently, and it is not a join plan, so the equivalence gate does not draw it.
18. **The hints of two refusals that were not queries.** `join` (a join that is not in the new form) and `subquery` said "there are no joins": they say the form now (`test_join_j3b.Compat`).


### 20.3 The gates

The gates were written first, in the first commit of the pull request (`tests/conformance/test_join_j3b.py` and its helpers), and **failed on the binary of `origin/main` (`ca33910`)**: 1,689 sub-tests failed and 5 errored (the MCP server needs the compiler on `PATH`), every one of them a join query; the reference tests, the compatibility golden of main and the unchanged flag form of a join passed. Against this branch all pass.

* **Q-EQ, the equivalence** (`Equivalence`, `Refusals`). A join plan is written as flags and as the query that stands for them and the two give the same status, standard output and standard error, a refusal compared without its `repair` (an invocation offered back is the flags' own) and with the literal that does not fit its column as one class (`where.syntax` in the flag form, `query.syntax` in the query form, as for any query). **Plans:** the 16 table pairs of the J3 reference (`join_ref3.shapes`: duplicates, no matches, empty sides, fan-out, a key in another column, empty cells, typed int keys in eight spellings, two pairs, the same names with a prefix, quoted records) x the 4 join types x 3 plans each drawn by `join_ref3.random_plan` among those the query can say (`--join-where`, `--where` with `or`, `not` and parentheses, select lists, groupings with `count`, `sum`, `min`, `max`, `distinct`, sorts, `--order-by` with types, `--top`, `--limit`, `--from`, `--rename` as `as`), each in 6 forms of the call (csv, json, json lines; the probe from a file and from standard input; 1 thread and 4 threads with 7-byte ranges): **1,152 pairs of runs over 192 plans, 1,116 answers and 36 refusals**; and a run 6.7 times larger (`TABLE_J3B_CASES=20`: **7,680 pairs of runs over 1,280 plans, 7,188 answers and 492 refusals**). **The 44 table pairs of the J2 shapes** (`join_ref2.shapes` and 12 random: text keys in every awkward form, typed int, dec(2) and float in every spelling, composite keys up to eight pairs, one-column files, a CRLF and a BOM, bytes that are not UTF-8) with `select *` of the 4 join types as csv and json: **352 pairs of runs, all equal**. **The refusals of the run** (`Refusals`, 19 queries x csv and json): the build file `-`, absent, outside the root; a key column that is not there, on either side; a typed key that is not of its type; a typed read of a padded cell; a `--join-where` literal that does not fit; the bounds `--max-state-bytes`, `--max-join-rows`, `--max-rows`; a column present in both files; `--delimiter`, `--input-format jsonl`: the same. **`--explain`**: the document of the flags and of the query is the same bytes for 32 generated plans and for the 35 pinned translations (no stated difference was needed).
* **O, the oracle** (`Oracle`). The very text of a query, `from 'p.csv' as a join 'b.csv' as b on ...`, is run by SQLite on the same two tables (a quoted name is a table name there; `cast(x as int)` is spelt alike; a semi and an anti join as `exists` and `not exists`): the rows are compared, in order when the `order by` names every column of the answer (all of them do), over the shapes whose cells SQLite and the tool read alike (no empty key or int cell, no quoted record): **840 statements, 107,845 rows compared, 339 of them with no row** (join types x 14 shapes x 5 statements: rows with `where` of `and`, `or`, `not`, `in`, `limit` and `offset`, conditions in the `on`, groups with `count`, `sum`, `min`, `max`, `count(distinct)`, one and two key pairs, typed keys); a run 6.7 times larger (`TABLE_J3B_CASES=20`): **5,600 statements, 1,626,602 rows compared, 2,173 with no row**.
* **Q-REF, the pinned cases** (`Pinned`, `Reference`). 113 refusals of the grammar of a join, each at its byte offset, kind and words, written by hand from the design (`query_join_cases.py`) and held against the reference front end and against the tool: every bare name (select, where, group by, aggregate, order by, `on`, a cast, a quoted name); every unknown alias; a position, a star, what is after a dot; every head (the first table without `as`, the second without `as`, a keyword for a name, the same name twice, no `on`, `using`, the second table not a string or `stdin`, `right`, `full`, `cross`, `left outer`, a second join with each join word); every equality (first unit, both columns of one table, two types, nine pairs, an equality after a condition); an `or` at the top of the `on`, a condition on the first table, a mix inside the `on`; a column of the second table of a semi and of an anti join (select, where, group by, order by); the earliest of several; a dot outside a join. 35 translations, hand-derived, to the flags they stand for (the second table's columns, `cast`, quoted names with a comma, a colon, an equals sign, a quote, a backslash, a leading `#` or `-`, `stdin`, every join type, the page). 6 literals.
* **SQL syntax error offsets by the reference parser** (`query_ref.py`, which shares no code with the tool): the fuzz below compares every refusal of the tool with the reference's kind, byte offset and words.
* **Compatibility** (`Compat`). (a) The golden of main (`query_join_main_golden.json`, 385 answers of the binary of `ca33910`: every accepted query and worked example of the query tests, 80 seeded random plans of the equivalence generator as queries with their tables (stored in the golden, so that what is replayed does not depend on the random streams of the Python that runs it), 18 queries that use `semi`, `anti`, `left`, `join`, `on`, `using`, `a`, `b` and `as` as names, aliases and quoted words, and the refusals of the grammar before the join): the same md5 of status, standard output and standard error. (b) **The goldens of A0, A1 and A2 recomputed on the binary of `main`** (`J3B_BASE`): the 5,585 cases (the hand lists, the pinned refusals, the seeded soups, the other forms an expression arrives in) through the binary of `main` and through this one: the same status, rule and bytes, **4 cases differ, all in the hint of `join` or `subquery`** (status and rule equal). (c) The 166-plan corpus (`scripts/corpus.py`): md5 of the list **`fccb01b66dd7518d74611219a6c5aa29`** from a fresh build of the new `main`, and the same 166 lines, byte for byte, on this branch, at 1 thread and with tiny ranges, **on the Mac and on gram**. (d) The reference front end reads 30,000 mutated and random strings of the old grammar as the one before did (one string differs: a lexical error after a dot, reading 3). (e) The flag form of a join is the bytes it was (`test_the_flags_of_a_join_are_unchanged` and the tests of J1 to J3, which all pass).
* **Fuzz** (`Fuzz`). 1,200 damaged and random join queries (the seeds are the 113 pinned refusals and the 35 translations, mutated, mixed with a soup of the words of the grammar): no trap, no stderr in JSON mode, every refusal a rule of `introspect`, every `query.*` at an offset inside the query and equal, with its kind and words, to the reference's, nothing the tool accepts that the reference refuses and nothing the reference accepts that the query layer refuses, **every offered repair accepted** (run: not refused as the query was). Counts: 70 accepted, 1,118 refused by the query layer, 12 by the engine, 13 repairs offered and run; and a run 12.5 times larger (`TABLE_J3B_FUZZ=15000`: 920 accepted, 13,895 refused by the query layer, 185 by the engine, 97 repairs offered and run).
* **MCP** (`Mcp`). The server's properties `join`, `on`, ... do what the query does; a join query over the server is a query that names its source (reading 13).
* **M, mutants** (`scripts/query_join_mutants.py`: 222 mutants of the new code and of the places that read the six flags, in the lexer, the head, the qualified column and its notes, the earliest-refusal rule, the look-ahead, the key equalities, the `on`, `order by`, the writing of the names and of the six texts, the plan, the surfaces of `jxsrc`, the repairs; `--check` in CI, and `test_mutants_apply` too). **Run for real**, one at a time, the sources restored after each, a mutant killed by the first test that fails (the quick pinned classes first): the Mac ran the 131 of the lexer, head, column, name, refusal, look-ahead, key, `on` and `order` groups and gram the 92 of the writing, the plan, the flags and the repairs (nice, cores 0 to 5). **First run: 186 killed, 17 survived on gram (of 92) and 20 on the Mac (of 131)**: 20 of the 37 are **equivalent** (one of them, an escaped `#` in the middle of a name, only after the second run showed that the reader of `--on` takes it as the `#`) and are listed in the script with the reason each changes nothing a call can see (for instance the kind of a `right join` is read nowhere, since the join is refused before any column is; `settle_pending` writes the refusal into the arena whichever way it answers; the first column of a key pair is the first table's, whose joined name is its own); 1 was a mutant that changed nothing and was removed; the other 16 were holes in the gates, and each got a test (a quote in a file name, `a` and `ab` as aliases, `inner outer join`, a second join of each kind, a bare name beside an `as` name, a name in the `on` with a comma, a backslash and a `#` inside, a literal in the `on` beside one in the `where`, the reason of a mix in the `on` that has a `where`, a collision in a query). **Second run of the 17 that were not yet judged: 8 of 8 killed on the Mac and 8 of 9 on gram, the ninth being the `#` one above.** The mutants of the earlier slices whose site moved (`query_mutants.py`, `join_mutants.py`, `or_mutants.py`, `rename_mutants.py`) were re-anchored to the new text, and 20 of them were run again for real on the Mac: **20 of 20 killed** (4, 8, 6 and 2; one more name I gave, `--join - is accepted`, matched no mutant of `join_mutants.py`, so it did not run).
* **EQ, non-join plans are unchanged** (`scripts/corpus.py`, the md5 above); `scripts/query_corpus.py --require f1` (41 plans, 166 runs, flags and query: 0 different) and `scripts/explain_corpus.py --require f1` (166 runs explained as flags and as a query: 0 different).
* **IB, instructions** (`scripts/gate_regress.py --counter`, Mac, bound 1.01, 21 cells x {1, 4} threads, three copies a side of the new `main` and of this branch): **every cell between 0.999 and 1.000**. **Per row of a join, non-query** (`scripts/join_perrow.py --all`, probes of 250,000 and 1,000,000 rows against a build of 100,000; builds of 100,000 and 400,000 against a probe of one row): probe row **0.9941 to 1.0024** over the seven shapes, build row **0.9807 to 1.0010**. **Callgrind on gram** (`scripts/join_callgrind.py --all`, valgrind instructions, old and new binary on all 38 cells, the join cells being new-only before J3b and now run on `main` too; `nice taskset -c 0-5`; perf_event_paranoid is 4, so no hardware counters were used): the ratio of the per-row cost is **between 0.99999 and 1.00000** on every cell, bound 1.01 PASS; the total of a call is 100 to 560 thousand instructions higher on the join cells (a fixed cost per call of the larger program, at most 1.0050 of the total on the smallest, the build-row cells). **A cost, disclosed:** an earlier build of this branch counted 1.0104 on a group over a semi join and 1.0087 on an inner join on two int pairs (total instructions, a probe of 1,000,000 rows, Mac) where the final build counts 0.9997 and 0.9999: the sources of those loops are the same in the two builds; I could not explain the 1 percent, and the bound is held by the final build, not by the draw.
* **Sizes.** `lexs_read_file` 18,064 bytes and `lexs_read_stream` 17,072 bytes in both binaries (`nm`); the binary is 36,368 bytes larger (1,188,648 to 1,225,016).
* **CI steps run locally**: `cancho fmt --check tools generated`, `publish_numeric.py --check`, `stream.py --check`, `schemas.py --check`, `manifest.py --check` (the compiler's report; the run against `cancho-tools` sources is CI's), `mcp.py --check`, `site.py --check`, `seo_check.py`, the 22 `--check` of the mutants, `query_corpus.py`, `explain_corpus.py`. **The whole conformance suite** (`python3 -W ignore -m unittest discover -s tests/conformance`) ran once on gram on the final tree, `nice taskset -c 0-5`: **1,204 tests, OK, 10 skipped, 2,678 s**. The Mac (load 40 to 80 from other sessions) ran the targeted classes only (the J3b file, the query, or/not/rename compatibility, the join tests of J1 to J3, the mutant checks); CI runs the whole suite.

### 20.4 What the front end costs

Instructions of a whole process on a 3-row file (`/usr/bin/time -l`, minimum of 3; most of a trivial call is the start-up, the flag table scanned for each lookup, 35 million): a flag call **-16,227** (35,343,235 to 35,327,008), a non-join query **+79,510** (`select id from 'orders.csv'`, 39,147,145 to 39,226,655) and **+94,575** (a grouping with three conditions, 39,311,382 to 39,405,957), a join call by flags **+395,363** (82,870,786 to 83,266,149) and with `--join-where` and a grouping **+531,491** (96,279,627 to 96,811,118). The increase of a join call is the longer help of `--join` (a flag late in the table) read by its lookups; a call with a file of a million rows is unaffected (the gate above). Reading 16 says how it was brought down from +5.4 M.

### 20.5 What was not measured, and what did not run

* Any quiet-machine timing, and any throughput of a join query (it is the join of J1 to J3: its throughput is theirs, section 18.4). The query front end's own cost per query is the instructions above, not a time.
* The oracle against DuckDB (not installed on gram; the Mac's DuckDB was not asked for: the oracle of this slice is SQLite, whose syntax the text shares).
* A join query over the MCP server (reading 13), a join with a jsonl side (J5), the threaded probe (J6), the swap repair (J7).
* The run of `scripts/manifest.py --check --against` the sources of `cancho-tools` (CI does it).
* The B1 rename test's `RENAME_B1_BASE`, and the `OR_A1_BASE` and `NOT_A2_BASE` tests: their base is the `main` of their own slice (they fail for any later `main`, which already has the grammar they test); the goldens of those slices are recomputed on the new `main` by `Compat` instead.

## 21. J5 as built (2026-10-16)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions: one Mac (Apple M4 Max, 16 cores, 64 GB, macOS 26.2, arm64), **loaded by other sessions' work** (load average 30 to 90 on 16 cores during the runs: the instruction counts do not depend on it, the times do, and nothing here is a quiet-machine time), compiler `cancho` e47d647, Python 3.14.5, SQLite 3.53.4 (Python's `sqlite3`), DuckDB 1.5.6; and gram (Linux x86-64, 16 cores, valgrind, `perf_event_paranoid` 4: no instruction counter, no DuckDB; a `lex-social` soak and other sessions' jobs were running on it, so every job was `nice -n 5` on cores 0 to 5) where a figure says "gram". The base of every comparison is the binary of `origin/main` at `a97e965` (J3b merged) built from the same compiler.

### 21.1 What was built

JSON lines on the probe, on the build or on both, and a csv build with a delimiter of its own. Three flags, the names section 2.1 gives them, last in the flag table:

| flag | meaning | default |
|---|---|---|
| `--join-input-format csv\|jsonl\|ndjson` | how the `--join` file is read | the format of FILE (`--input-format`) |
| `--join-fields PATHS` | the columns of a jsonl `--join` file, by path, as `--fields` declares them | `--fields`, when FILE is jsonl too |
| `--join-delimiter` | the separator of a csv `--join` file: a comma, `tab` or a semicolon | `--delimiter` |

`--input-format jsonl --fields PATHS` for FILE (the operand or standard input) is the flag of any one file and now goes with `--join`. `--absent` and `--max-json-nodes` are one flag for both files (section 3.2 says so). The answer keeps the delimiter of `--delimiter`.

**The contract: a row is a csv record.** Each side is read by the reader it has (a JSON line by `jrec.extract`, as `jsonl.read` reads one file; a csv line by `toolbox.lines` and `reader`, as `jnread` does) and every row is then handed to the join as the csv record of its cells in the delimiter of the answer: the cells joined by the delimiter, a cell quoted (its quotes doubled) when it holds a quote, the delimiter, LF or CR, and a record of one empty cell written as two quotes (`jrec.csv_record`, which the sorter already uses to hold a row of JSON lines), its cells found again by `reader.fields`. The reader of #47 hands the engine the JSON line itself (or a copy of it with the decoded strings appended) and three integers a cell that point into it; a join cannot hold that (the line is not csv, and the joined record is built from csv bytes), so it takes the conversion the sorter already takes. So `jtab`, `jnkey`, `jnwhere`, `jn3pass.probe_g`, `jnread.probe_one` and the engine are given what they were always given, and **the accounted bytes of a row are those of the csv row with the same cells** (the formula of 6.2 is unchanged: `record bytes + 16` for a row, `key bytes + 60` for a key, `+ 44` for a keys-only key). A JSON number is the cell its exact text is (`1E5`, `-0`, `0.30000000000000004` and an integer of 30 digits stay what they were written), a string its decoded text, `true` and `false` the words; `null`, a missing key and a step through a scalar are the absent cell (a refusal, or the empty cell with `--absent empty`).

Code: `jnfmt.cho` (new: the formats from the flags, their defaults, the checks), `jnjson.cho` (new: the build and the probe loops for a jsonl side or a csv build with its own delimiter, **copies** of `jn3pass.build_pass3` and `jn3pass.probe_pass3` with the reading of a row replaced, which take every plan), and changes to `jnrun` (which loop reads which file, the headers, the refusals), `jnexplain` (the readers), `jnflags` (the formats are checked by `jnfmt`), `finish` (`jsonl_refusal` says `input`), `spec` (the help), `table` (the summary and usage; one condition: the checks of one JSON-lines file are not run for a join), `jn3pass` (three functions made `pub`: their code is the same). `jnread` and `jn3pass`, the loops of J1 to J3b, are not edited, nor are `read_file` and `read_stream`: a join of two csv files with one delimiter runs the code it ran. **The authority row is unchanged** (`scripts/manifest.py --check`: `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`). No new rule: the refusals of a line are the reader's, and the flags use `args.*` and `input.*`.

### 21.2 Where J5 differs from sections 1 to 20 (every narrow reading)

1. **Scope.** The row of section 11, nothing else: JSON lines on either side, `--join-delimiter`. Not built: the threaded probe (J6), the swap repair and the pruned build (J7). `--discover`, `--discover-lines` and `--max-fields` are `args.conflict` with a join (section 3.2 said so).
2. **The names are the design's** (2.1 and 3.2): `--join-input-format`, `--join-fields`, `--join-delimiter`. The design gives them no values: `--join-input-format` takes the values of `--input-format` (`csv`, `jsonl`, `ndjson`; a tsv build is `csv` with `--join-delimiter tab`), `--join-delimiter` the values of `--delimiter`. No other flag is added: one `--absent` and one `--max-json-nodes` for both files (the design's words), and no `--join-absent`.
3. **Each defaults to the probe's, as the design says (2.1, 3.2), and this is a trap that is stated here.** The `--join` file is read as FILE is: with `--input-format jsonl --fields a,b` and no `--join-input-format`, the build is JSON lines with the fields `a,b`. So **a jsonl probe with a csv build needs `--join-input-format csv`**; without it the build's names are the probe's declaration (a `join.column-collision` at the header, or `parse.jsonl-syntax` naming the build file at its first line when a prefix avoids the collision), and `--explain` shows the build's reader in `join.build_reader` before a row is read. A csv probe with a jsonl build needs `--join-input-format jsonl` and `--join-fields`. Nothing is guessed from a file's name or content.
4. **A csv build with a delimiter of its own is re-delimited, not translated.** Its record is held in the delimiter of the answer: a quoted field keeps its quotes (the record as the file wrote it, so the accounted bytes are that record's), an unquoted field that holds the answer's delimiter gets quotes, a field that spans lines stays one record. `--join-delimiter` equal to `--delimiter`, said or not, is the old csv build (`jnread`'s loop, untouched).
5. **The header of a jsonl side is its declaration; nothing is read for it.** Section 16.2 item 4 (the header of a pipe is read before the build's rows) is about a csv pipe; a jsonl probe, from a file or from a pipe, has its names before the build is opened, so `join.column-collision` and the unknown key columns are told before any row of either file is read, and `--explain` of a pipe knows the joined header (`join.output_columns`, the split of `--where`).
6. **The flags that name nothing are refused, before any read** (`jnfmt.check`; the checks of `jflags` for one file are not run for a join, `table.cho`): `--join-fields` beside a csv build, `--join-delimiter` beside a jsonl build or naming no delimiter (`args.bad-value`), `--fields` beside a csv probe (the hint names `--join-fields`), `--absent` and `--max-json-nodes` when neither file is jsonl, `--max-json-nodes` out of 1 to 1,048,576, the three flags without `--join` (`args.required-flag`, as `--on`). A jsonl file with no declaration is `input.fields-required` and a declaration that is not a list of paths `input.bad-fields`, each with `detail.flag` (`--fields` or `--join-fields`).
7. **The repair of `input.fields-required` is none in a join.** The one-file refusal offers the same call with `--discover`, which a join refuses; the reason says to run `--discover` on the file alone.
8. **A refusal of a line is the reader's, with the file named.** `parse.jsonl-syntax`, `-not-object`, `-missing-field`, `-not-scalar`, `-duplicate-key` and `limit.json-too-many-nodes` come with `detail.path` the file (`-` for a pipe), `detail.input` (`build` or `probe`, new for these rules; the csv rules of a join have none, 16.2 item 5), `detail.line` and `detail.row` as the reader gives them. The row of a refusal of the probe is the probe's own count (the tally counts joined rows), and `detail.looks_like` (a document, not lines) is told only for the first probe line. The build file is read first. The repair of a missing field (`--absent empty`) is offered, the call written again with it, and is accepted: the flag applies to both files.
9. **Typed keys and padding over jsonl cells need nothing new.** A cell is the csv cell, so `k:int` over `1.5` is `value.not-integer` with `detail.input`, row and line of the JSON line, and a padded cell of a left join with a jsonl build says `padded`. The guard (`KEY != ''` in `--join-where` or before the rest of `--where`) is the same, and works for a jsonl probe whose key is absent under `--absent empty`.
10. **`--query`: the format of a file is a flag, not part of the query.** The query names the files and the flags say how each is read: `--input-format`, `--fields` for the first table; `--join-input-format`, `--join-fields`, `--join-delimiter` for the second (which default to the first's). The flags of the join itself (`--join`, `--on`, `--join-type`, `--join-prefix`, `--join-where`) are still `args.conflict` beside a query, the three format flags are not, a column of a jsonl table is its declared path (`a."user.name"`), and `--explain` of the query is the explain of the flags (`Query`).
11. **`--explain`.** `data.explain.join` gains `probe_reader` (a jsonl probe: `format`, `fields`, `absent`, `max_json_nodes`) and `build_reader` (a jsonl build the same; a csv build with a delimiter that is not the probe's: `format`, `delimiter`), and **only then**: the document of a join of two csv files with one delimiter is the bytes it was. `data.explain.source` describes the row the plan runs over, as it did (its `fields` and `columns` are the joined row's). The upper bound of the build for jsonl (`accounted_at_most`) is `2 * (size + columns * rows) + (76 + extra) * rows` with `rows = size / 3 + 1` (a keys-only build `(size + columns * rows) + (44 + extra) * rows`), **computed**: a record is at least `{}` and a line feed, its csv record is at most its line and `columns` more (a record of `{}` with every path absent is `columns - 1` delimiters), and the keys are inside the records. `header_bytes` is 0.
12. **One thread.** A join reads on one core whatever `--threads` says, for a jsonl side as for a csv one (J6).
13. **MCP.** The three flags are properties of the tool (generated from the flag table), a call with `file` or `stdin` and `join` takes them, and the text of `stdin` can be JSON lines (`input-format`, `fields`); the paths of `join` are the forms of `file`, as before.
14. **Disclosed changes of code that other plans run, none in a loop.** `jn3pass.filter3`, `probe_g` and `sorted_held` are `pub` (their code is the same: every function of `jnread` and `jn3pass` has the size it had, `nm`); `finish.jsonl_refusal` has one more argument, `""` for the one-file reader; `jnrun.run` reads the formats once (a scan of the arguments for five prefixes, and no lookup of a flag unless one is mentioned: a join of two csv files costs no flag lookup more).

### 21.3 The gates

Written before the code, in their own commit (`27a3218`), failing on `main`'s binary: `tests/conformance/test_join_j5.py` (61 tests; on the binary of `a97e965`, **970 failures and 3 errors**: the errors are the MCP server, which needs the compiler on `PATH`) and the one test of J1 that said a jsonl probe is `args.conflict` (`test_join.py`, now: it is a join). CI ran that commit and failed. The same file passes on this branch.

* **O, the oracle** (`Oracle`). Tables are generated and written as csv (with the delimiter of the side) or as JSON lines, and **read again by Python's csv and json modules** (`join_ref5.py`: `json.loads` with `parse_int` and `parse_float` keeping the text, duplicate keys kept as pairs, a path walked by hand); that is what the Python reference, **SQLite** (`ORDER BY p.rn, b.rn`, TEXT columns, typed keys by `CAST`) and **DuckDB 1.5.6** (`all_varchar=true`, a `nullstr` no cell has, no inference, typed keys by `CAST`) join, and the tool is compared with it as the multiset of rows **and in the defined order**, as csv bytes, as json, from a file, from standard input, and at 4 threads with 7-byte ranges. The writer varies what a JSON-lines file may vary: CRLF, a BOM, no final newline, blank lines (empty, spaces, a tab), undeclared keys, keys in any order, spaces around the object, a cell written as a bare number, `true` or `false` when it reads back as that text, strings with `\\u` escapes or UTF-8; and a csv file CRLF, BOM, every field quoted. Cases: the 30 UTF-8 shapes of `join_ref2.shapes` (duplicates, no match, every row matching, fan-out of 300 x 300, header-only and empty sides, empty keys, look-alike keys, quoted keys with commas, quotes and line breaks, CRLF and BOM, one-column files, `007` and `7`, `-0` and `0`, `1.5` and `1.50`, the spellings of one double, composite keys up to eight pairs, USING with a prefix, the key in another column) x csv x jsonl, jsonl x csv and jsonl x jsonl x the 4 join types (360 cases; the reference, SQLite and DuckDB agreed on the 120 that use them); the same shapes with a build delimiter of its own (comma, semicolon, tab in each place: 240); 70 random tables in random formats and delimiters; cells that JSON escapes (a quote, a backslash, a tab, a line feed, a carriage return, a control character, a comma), that are not ASCII (precomposed and combining accents, CJK, an emoji as a surrogate pair of `\\u` escapes), numbers written every way (30-digit integers, `2^63`, `-0`, `1E5`, `0.30000000000000004`, `5e-324`) and the words `true`, `false` and `NaN` as text keys (5 pools x 3 formats x 4 types), typed keys `:int`, `:dec(2)` and `:float` over the spellings (36), declared paths into objects and arrays (`u.name`, `items.0.sku`: 12), null and missing keys read as empty cells under `--absent empty` (12).
* **The plans of J3 over jsonl sides** (`Plans`): 160 plans drawn by `join_ref3.random_plan` over its 16 table pairs, each with a random pair of formats, compared with the J3 reference as csv or json and from a pipe; the refusals of the reference are predicted and compared by rule. `SharedBudget`: the groups and the sorted rows of a jsonl probe have the threshold and the facts (`holder`, `build_bytes`, `held`, `limit`) of the csv probe's, bisected, for three plans; the guard of an empty typed key works for a jsonl probe whose key is absent.
* **The chunk edges** (`ChunkEdges`): a jsonl build of 6,000 records with the record length swept (pad 0 to 45 in steps of 3, for inner and left) so that the 64 KiB blocks of the reader cut every position of a record, a jsonl probe through a pipe in blocks, a record of 200,000 bytes, and `limit.line-too-long` for the build and for the probe.
* **The refusals** (`Refusals`): each of the six reader refusals for the build and for the probe with the rule, the exit status, `detail.path`, `detail.input`, `detail.line`, `detail.row`, `detail.column`, the offset, the code and the bytes near it of a syntax error; the row of a probe refusal is the probe's own (a probe whose first rows join nothing); a document that is not JSON lines is said so on its first line only; the build before the probe; the repair `--absent empty` offered, accepted and equal to the explicit call; the row bounds (`--max-rows 5` accepts 5 and refuses 6 build rows; the probe stops at it); `--join-where` reading a jsonl cell as a number; a BOM only on the first line; ragged, unterminated and badly quoted records of a csv build with its own delimiter, and a record over lines.
* **The flags** (`Flags`): 22 flag combinations that name nothing, each with the rule the design gives, the exit status and a schema-valid document; the defaults of the build; ndjson; the delimiter of the build and of the answer; `--join-where` and the collision rule over declared names (and the repair of the collision accepted).
* **ST, TH** (`Identity`, and every `Oracle` case): the file, a pipe and `--threads` 2, 3, 8, 16, 64 with `--chunk-bytes` 1, 7, 64, 1000, 4 MiB give one answer for the four join types, a refusal from a file and from a pipe differ in `detail.path` only. **Gram `strace -f`** (`Strace`): a jsonl x jsonl join at 4 threads with 7-byte ranges starts no thread, opens no socket and opens no file for writing.
* **A, accounting** (`Accounting`): the program's count (`detail.held` of the refusal one byte below the bound) equals the formula computed in Python from the file read again (`join_ref5.accounted_cells`: the csv record of the cells, `+ 16` a row and `+ 60` a key, `+ 44` for a keys-only build), with the row, the line and the bytes read of the row that did not fit, on **2^20 and 2^20 + 1 rows** of a jsonl build for `inner` and `semi` with two typed keys, on cells that need quotes (a comma, a quote, a line feed), unicode, numbers and the words `true` and `false`, for the four types at 1, 2, 6 and 16 threads and for a pipe, on a record of one empty cell (two quotes), on blank lines, CRLF, a BOM and no final newline, and on 5,000 rows of one key; **accepted at N, refused at N - 1** every time. And the same cells as a csv file and as a jsonl file hold **the same accounted bytes to the byte** (bisected on the bound, four types).
* **`--explain`** (`Explain`): the readers (`probe_reader`, `build_reader`) for a jsonl build, a jsonl probe, a jsonl probe on a pipe (its columns are known) and a build delimiter (tab as `\\t`), no such member for two csv files; the upper bound of the build is the stated formula, to the byte, for `inner` and `semi`; the refusals before a row are the run's. **The query form** (`Query`): the query and the flags it stands for give the same bytes with jsonl sides, the format flags go beside a query and the flags of the join do not, the explain of the query is the explain of the flags. **MCP** (`Mcp`): the three properties are in the tool, and calls with a jsonl build, a tsv build, a missing declaration, and the text of a jsonl probe give the CLI's bytes.
* **F, fuzz** (`Fuzz`, 400 plans over random tables in random formats and delimiters, damaged files, the new flags and flags that name nothing): no trap, every refusal tagged with a rule of the catalogue, every repair offered accepted by the flags; more than 80 accepted and 80 refused runs.
* **EQ, non-join plans unchanged**: `scripts/corpus.py` (166 plans): md5 of the list **`fccb01b66dd7518d74611219a6c5aa29`** recomputed on a fresh build of `a97e965`, and the same 166 lines, byte for byte, on this branch, **on the Mac** and (gram was down for the final runs: the corpus there is NOT RUN; the whole suite ran once on gram on an earlier tree, below); `scripts/query_corpus.py --require f1` (41 plans, 166 runs: 0 different) and `scripts/explain_corpus.py --require f1` (166 runs: 0 different).
* **IB, instructions** (`scripts/gate_regress.py --counter`, Mac, bound 1.01, 21 cells x {1, 4} threads, three copies a side of the new `main` and of this branch): **every cell between 1.000 and 1.001** (largest 1.001: `group-count id (1M keys)`, 2,793.7 to 2,795.4 M). **Per row of a csv join** (`scripts/join_perrow.py --all`, now the minimum of three runs of each size, probes of 250,000 and 1,000,000 rows against a build of 100,000; builds of 100,000 and 400,000 against a probe of one row): probe row **0.9995 to 1.0010** over the seven shapes (2,259.47 to 2,259.42 instructions for the inner join of one text pair), build row **0.9959 to 1.0017** (1,205.73 to 1,206.39). A first run that took one count of each size gave 0.989 to 1.0085 for the probe and 0.9922 to **1.0100** for the build (the anti join): the code of those loops is the same (below), and the minimum of three is the reading I rely on; the single counts are in the record because the bound is 1.01. **The code of the loops is the same**: every function of `jnread` and `jn3pass` and the engine has the size it had (`nm`), `lexs_read_file` and `lexs_read_stream` are 18,064 and 17,072 bytes in both binaries, and the binary is 50,640 bytes larger (1,225,016 to 1,275,656). **Callgrind on gram: NOT RUN** (gram, the only machine with valgrind, went down at about 13:20 and did not come back; the Mac counter above is the evidence for the per-row cost).
* **EXPL, MCP, AU**: `scripts/manifest.py --check` and the embedded manifest are unchanged in labels (`args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`); `schemas.py`, `mcp.py`, `site.py`, `seo_check.py` and `publish_numeric.py --check` pass; the explain schema gains `probe_reader` and `build_reader` as optional members.
* **M, mutants** (`scripts/join_jsonl_mutants.py`, 68 mutants of the formats, the reading of a row, the loops, the refusals and the explain; `--check` in CI, and `test_mutants_apply` too). **Run for real on the Mac (the final tree, two copies of it, one half of the list each): 68 of 68 killed.** The first pass over the 69 of that day killed 59 and let 10 survive: 9 were gaps of the tests (`--max-json-nodes` out of range, a bad declaration of the probe, the same build delimiter said again, a CRLF line, the facts and the first-line hint of a probe refusal, a second bad typed cell and a second bad `--join-where` cell, a ragged record over lines) and each got a test (re-run: 8 of 8 killed; the ragged one verified by hand, the mutant traps), and 1 was equivalent and was left out (the tally's `groups` on abort 71, which nothing reads; in the script's docstring). **On gram: NOT RUN** (down). The whole conformance suite ran once on gram on an earlier tree (1,255 tests, 53 minutes: 5 failures and 1 error, each a test expectation this change moved (the flag lists of `test_describe` and `test_skill`, the J1 test that read a csv build as jsonl, six stale anchors of `join_mutants.py`), all fixed since) and on CI (Linux x86-64) on the later commits: green, including `test_join_j5.Strace`. The 223 mutants of `join_mutants.py` whose site moved (six: the build and probe header, the pushed conditions, the explain's bound, the flags without `--join`, the split of the explain) were re-anchored to the new text.

### 21.4 What a JSON-lines side costs

**Per row, in instructions retired** (`scripts/join_perrow.py --all --jsonl`, Mac, `/usr/bin/time -l`, minimum of three runs of each size, the slope between probes of 250,000 and 1,000,000 rows against a build of 100,000 rows of integer keys, and between builds of 100,000 and 400,000 rows against a probe of one row; one in five probe keys in no build row):

| the inner join of one text pair | per probe row | per build row |
|---|---:|---:|
| csv probe, csv build (J1's path, the reference) | 2,258.5 | 1,204.8 |
| jsonl probe, csv build | 4,482.3 (+2,224) | 1,205.4 |
| csv probe, jsonl build | 2,258.1 | 3,101.6 (+1,897) |
| jsonl probe, jsonl build | 4,483.2 | 3,104.3 |

A JSON-lines row costs the parse of the line (`jrec.extract`, a scan of the line for the declared keys and the cells found in it), its csv record (`jrec.csv_record`) and the split of that record again (`reader.fields`): about 2,200 instructions a probe row and 1,900 a build row more than the csv row, for lines of 41 and 31 bytes with three and two columns. The per-row cost of a csv x csv join against `main` is in 21.3 (probe row 0.9995 to 1.0010, build row 0.9959 to 1.0017).

**Time, for the record and not as a measurement of speed** (`min` of 5 interleaved rounds, output to a pipe and compared by md5 first, the four outputs are the same bytes; the Mac was at load 35 to 48 on 16 cores, so these are upper figures for every contender alike and the 1M x 100k csv x csv join, 0.17 s in section 16.5 at load 9, is 0.99 s here): the probe of 1,000,000 rows (19.9 MB as csv, 40.9 MB as JSON lines) and the build of 100,000 rows (1.4 MB, 3.1 MB):

| | csv x csv | jsonl probe | jsonl build | jsonl x jsonl |
|---|---:|---:|---:|---:|
| `min` (median), s | 0.992 (1.112) | 1.327 (1.422) | 1.082 (1.298) | 1.209 (1.463) |

**Memory** (`scripts/join_rss.py --build-format jsonl`, the eight files of the spike of 15.1 written as JSON lines, min of 3, `/usr/bin/time -l`, the call is `table --join FILE.jsonl` with a probe of one row at `--max-state-bytes` equal to the formula; **the program's count was checked equal to the formula of the csv file first, on every file**, since the record held is the csv record of the same cells): peak resident / accounted **1.56 to 2.80** (`int_100k` 1.56, `int_1m` 1.85, `text_100k` 1.71, `text_1m` 1.73, `text_pow2` 1.65, `text_pow2p1` 2.01, `wide_pow2` 2.80, `dup_100k` 2.16); the same binary on the csv files, same run: **1.49 to 2.77** (1.49, 1.84, 1.53, 1.71, 1.66, 2.00, 2.77, 2.15). So the help, the refusal's hint and the skill keep saying "up to about three times". A build through the reader of JSON lines takes about twice the time of the csv build in the same call (the `ms` column of those runs, whole process: 49 to 453 ms against 28 to 225).

### 21.5 What was not measured, and what did not run

* Any quiet-machine timing (the Mac was at load 30 to 90 for the whole of this slice), the speed of a jsonl join against DuckDB or csvtk (neither reads this form), a build above 1,048,577 rows other than the pow2 edge test, builds of other widths than the ones listed, the worst resident-to-accounted ratio over all shapes.
* The oracle's DuckDB on gram (not installed there): the SQLite and Python references ran there; DuckDB ran on the Mac.
* The instruction counter on gram (`perf_event_paranoid` 4, not changed): replaced by callgrind, as in sections 17 to 20.
* `manifest.py --check --against` the sources of `cancho-tools` (CI does it).
* Not built, as the design says: the threaded probe (J6: a join reads a jsonl side on one core), the swap repair and the pruned build (J7; **built by section 22**), the partitioned build (J8), the merge join (J9; **built by section 23**); `--report` over a join; a per-side `--absent`.

## 22. J7 as built (2026-10-19)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions: one Linux x86-64 virtual machine (`Intel(R) Xeon(R) Processor @ 2.10GHz`, 4 cores shared with other work, 16 GB, kernel 6.18, Python 3.13, valgrind 3.22, DuckDB 1.5.6), compiler `cancho` e47d647. **No clock time is claimed anywhere in this section**: the machine is shared and has no instruction counter, so the figures are bytes (accounted, and resident from `wait4`), instruction counts (callgrind, exact whatever the load) and counts of cases. The Mac of the earlier sections was not used; every figure of this section is from this machine.

### 22.1 What was built

* **The pruned build.** A join holds, for each row of the `--join` file, the record with the cells of the columns the plan does not name left empty (`jtab.add_pruned`, `jnprune`): the delimiters stay, so every cell keeps its place and the probe cuts the held record with the reader it always used. The columns the plan names are those the answer shows (`--select`, or every column of the joined row when the plan selects nothing and does not group) and those `--where`, `--group`, `--agg` and `--order-by` read. A build that names few of its columns holds, and counts in `--max-state-bytes`, few of its bytes.
* **The swap repair.** `limit.state-too-large` of the build (`detail.holder` `join-build`) has a `choose` repair with one option, the same call with the two files exchanged, when that call gives the same rows (reading 5) and the probe file, held as the build side, fits within the same bound (reading 6); otherwise `repair.kind` is `none` and its `reason` says which condition failed. `--explain`'s `join.build_state.swap` is `{"offered": true|false, "reason": "..."}`: it says whether the exchange would give the same rows, from what is known before any row is read.
* Code: `jnprune.cho` and `jnswap.cho` (new), `jtab.add_pruned`, one branch in each of the three build passes (`jnread.build_pass`, `jn3pass.build_pass3`, `jnjson.build_passj` and its `hold3`), the mask and the refusal in `jnrun`, `jnfail.build_too_large`, the swap object of `jnexplain`, the rule row of `finish.extra` (`limit.state-too-large` is `sometimes` repairable, and says so), the help of `--join` and `--max-state-bytes`. No new rule, no new flag. **The authority row is unchanged** (`scripts/manifest.py --check`: `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`; the join only reads files, and the check of the other file is a read of a file the call already has open).

### 22.2 Where J7 differs from sections 1 to 21 (every narrow reading)

1. **The held record keeps its width.** The record is not re-indexed to the named columns: the cells of the other columns are empty and their delimiters kept (`a,,,d`). Section 6.2 says the record is "the record the engine sees", and the engine, the probe and the sorter all cut it with `reader.fields` at its place; a narrower record would need a second numbering of the columns in all of them. The price is `columns - 1` delimiter bytes a row, which is counted. A row is made from the named columns that are next to each other as one stretch of the record each (a *run*), so its cost is a step for each run and not for each column, and a plan that names nothing of the build holds a row of delimiters.
2. **The accounted bytes are those of the record held (the design check of the accounting).** Section 6.2: `accounted = sum over held rows (record_bytes + 16) + sum over distinct keys (key_bytes + 60)`, "a statement of what the join holds in its arrays", the record "the record the engine sees". Read as written, with a pruned build the record held is the pruned one, and J2's reading 5 (USING holds the whole record, "the memory saved by holding fewer columns is J7's") says as much. So the count of an inner or left join falls by the bytes of the cells it no longer holds; the key's bytes, the 16, the 60 and the semi and anti join (keys only, `key bytes + 44`) are as they were. **An input accepted before is accepted now**: a held record is never longer than the whole one (it is the same raw cells, a subset of them, with the same delimiters), so the sum is never larger, and the groups and the sorted rows, which are given `max_state - build_accounted`, are given at least what they were (the joined record a sort holds is shorter too). What changes is that a call refused before can be accepted now: `table --join wide.csv --on k --select a.id,b.name` with `--max-state-bytes` below the whole record's count. No call that was valid refuses (`Accounting`: every plan is accepted at the whole-record count of `main`'s formula, and `Identity` runs 556 plans against `main`'s bytes). Corrected in place: section 17.2 item 5, and the sentence of section 19 that said the held bytes did not depend on `--select`.
3. **Pruning is on when some column that is not a key is not named.** A plan that leaves out nothing but a key column holds the whole record, as it did: the key is the one cell that is held twice (in the record and in the table of keys), and dropping it alone saves its bytes and costs the work of a pruned record for every row. Measured by `scripts/join_callgrind.py` before this rule and the runs of reading 1 (a build of two columns, `--select id`): 1,386 instructions a build row against `main`'s 1,269, a ratio of **1.0926** over the bound of 1.01; after them 1,263, **0.9956** (22.5). The rule is a choice, argued by that measurement.
4. **A column is named by what the plan reads or shows**, and the whole of `--where` counts, including the conditions of the leading run that were applied before the join (they are evaluated again on the joined row, section 18.2, so the cell must be there). `--join-where` does not name a column (it reads the build row before the row is held); a key does not name its column (the key is held beside the record, in the table of keys, and was read from the whole record); the column a shared-name key leaves out of the joined row is named by nothing. A `--rename` or `--sort` names output labels, not columns.
5. **When the exchange gives the same rows** (the codes of `jnswap.code_of`, in the order they are tried; the first that holds is the reason): (1) the join is inner; (2) the probe is a file, and not standard input (an operand written like a flag, `-x.csv`, is refused too); (3) the call is not a `--query` (the text of a query is not rewritten); (4) the probe file is smaller on disk than the `--join` file (design 6.3, condition 3; a size that is not known is refused); (5) no `--join-prefix` (it names the build file's columns, and the exchange would give it to the other file's, so every name of the plan would move); (6) no `--join-where` (it filters the file that would become the probe; carrying it over as a `--where` would change which cells are read); (7) no column named by position, `#N` (the positions move); (8) no typed shared-name key (`--on id:int`: the answer shows the probe's spelling of the key and the exchange would show the other's, `007` for `7`); (9) the answer is not a page of rows, that is `--limit`, `--top` or `--from` is given and the plan does not group (the exchange changes the order of the rows, and so which rows a page holds; the default page of `--format json` is a cap and not a window the caller asked for, so it is not refused, and a grouping lists its groups by key, so a page of groups is the same page); (10) when the call has to say the columns (it selects nothing and does not group) no name is twice or empty in the joined header; (11) a csv answer keeps its delimiter (the delimiter of FILE is the answer's, and the exchange would give it the other file's). Left, semi and anti are never exchanged (design 6.3): `left` pads the probe rows, `semi` and `anti` answer about the probe. **Narrower than the design:** it offers the exchange for an inner join with the probe smaller on disk, as 6.3 says, and refuses in the cases above that 6.3 did not name, because in each of them the exchanged call would not give the same rows.
6. **The other file must fit; the refusal reads it.** Section 6.3 said the option comes from `stat` alone. An option whose next run is refused for the same bound is not a repair, so the refusal reads the probe file with the build pass of the run, with the keys the other way round and the columns the exchanged plan names, up to `--max-state-bytes`, and offers the exchange only if that build is whole. It is the same pass over the same bytes the exchanged call will read first, so the answer is exact for the build (accepted at N, refused at N - 1: gate B). **It does not apply `--where`'s pushdown**, so it can only err towards offering nothing; `--join-where` is refused (5, code 6). A read that the exchanged call would refuse (a ragged row, a key cell that is not of its type, more rows than `--max-rows`, a line the reader refuses) is `none` with a reason that says so. The cost is a read of the smaller file, at most, and the read of the rest of the `--join` file of reading 10 when its remaining bytes could hold more than `--max-rows` rows; a refusal that offers nothing for the other reasons reads nothing. **What it does not check:** the groups and the sorted rows share the flag with the build, and the exchanged call gives the build what it needs (the other file) and the groups what is left; a call whose groups need more than that is refused again, by the groups (`detail.holder` `groups`), which has its own message. The fuzz allows exactly that, and nothing else, as an answer to an offered option.
7. **The columns do not change, the order of the rows does.** Section 6.3 says the hint tells that "the order of the rows and of the columns changes". The columns need not: when the plan selects nothing and does not group, the option adds `--select` with the joined header of the original call, in its order, so the answer has the columns it had. The order of the rows cannot be kept (the probe is now the other file: its order, then this file's within a key), and the hint says so; a grouping is the same bytes either way (gate S). `--explain` has `"order": "probe order, then build order"` as before, which is the order of the call it explains.
8. **The reader flags go with their files.** The option takes `--input-format`, `--fields`, `--delimiter` and the three `--join-` ones off the call and puts them back the other way round: the probe is read as the `--join` file was (JSON lines with its fields, or csv with its delimiter), the `--join` file as the probe was. `--delimiter` is also the delimiter of a csv answer: with two csv files of different delimiters the exchange is refused for a csv answer (code 11), and offered for the json answers a refusal is written in (a csv refusal is a line on standard error, with no repair, so the case is only reachable through `--explain --format csv`).
9. **`--explain` says what the exchange is, not whether the refusal will write it.** `offered` is true when the call is one whose exchange gives the same rows (the codes above but the sizes of the build it has not read); the refusal may still offer nothing because the other file does not fit (reading 6), and for a `--query` it offers nothing because it does not rewrite the text, while `--explain` of a flag call and of its query form are the same document (gate X1: `test_join_j3b`, `test_join_j5`). The words of `reason` for an offered exchange are the design's: "inner join and the probe file is smaller on disk".
10. **`--max-rows`.** The `--join` file of the original call becomes the probe of the exchanged one. A build is refused past `--max-rows` rows (`limit.too-many-rows`, always: a truncated build is a wrong answer), a probe is cut and says `truncated`. Without a check a `--join` file past `--max-rows` rows would, under the exchange, be a truncated answer instead of the refusal the call gets under a larger `--max-state-bytes` (found by the follow-up of this slice: a `--join` file of `--max-rows` + 1 rows was offered the exchange, shown by `test_join_j7.MaxRows` on the previous head). **Now checked:** at the refusal the rest of the `--join` file, which the build did not read, is counted (`jnswap.count_rest`: the lines after the row the build stopped at, a csv record over several lines as one row, a JSON-lines blank line not at all, every other row, a ragged one included, so that a count errs high), unless the bytes that remain cannot hold enough rows to matter (at least as many bytes as the file has columns for each row). The exchange is offered only if the rows read plus the rows that remain are at most `--max-rows`, and then the answer is the raised-bound call's rows (gate `MaxRows`). Otherwise the repair is `none` and says either that the file has more rows than `--max-rows`, or that the rows cannot be counted (a line past `--max-line-bytes`, a quote that never closes, a row that is not csv). The cost is one more read of the part of the `--join` file the build did not reach, at a refusal that is going to offer the exchange; `--explain` still says `offered` from what is known before a row is read (reading 9). The probe file past `--max-rows` rows was already refused (`fits_as_build` is `capped`, reading 6).
11. **`limit.state-too-large` is "sometimes" repairable.** The row of the rule is the tool's own (`finish.extra`, which overrides the package's, as for the other `limit.*` rows), `repairable` says `sometimes` and the summary, which `test_rules` holds to the refusal of the groups, says where the choose repair is. `docs/refusals.md` and the project page are generated from it. The hint of the join build's refusal now starts with the columns ("name fewer columns of the --join file") and says what the repair is.
12. **The first option of the design, "raising the flag", is still only in the hint**, for the reason section 6.3 gives (the number needed is not known at the refusal).

### 22.3 The gates

Written before the code (the first commit of the pull request is the tests alone, with the counts below against `main`'s binary), each able to fail:

* **S, the repair is mechanical** (`tests/conformance/test_join_j7.py` `Mechanical`): 13 scenes (text key; `:int` key with `007`, `+7`, `-0` spellings; USING; two pairs; a JSON-lines probe; a JSON-lines build; both JSON lines; both with a semicolon; both with a tab; a semicolon probe with a JSON-lines build; a build with a delimiter of its own; a probe with one; the key in the third column of the probe and the second of the build) and 10 or more random plans on each (rows, `--select`, `--where`, `--rename`, `--group`, `--agg`, `--sort`, `--top`, a page of groups): the call is refused at a bound the other file fits, the refusal has one option, and the option, run as it stands, exits 0 and gives (a) for a grouping, **the bytes of the original call under a larger bound**, and (b) otherwise, **the rows of the Python reference of the exchanged definition, in its order, with the columns of the original call**, whose multiset of rows and whose columns equal the original's under a larger bound. **130 options were run and accepted (73 of them groupings), 84 giving the original's bytes.** The forms `--join=FILE`, `--on=PAIRS` and `--delimiter=;` are written back as they were, and names with a comma, a backslash, a hash and an equals sign are escaped (`--on` and `--select`).
* **B, the boundary** (`Boundary`): the other file's accounted bytes as a build, N, computed by the reference: accepted at N (the option is offered and runs), refused at N - 1 (`none`, "does not fit", and the probe named); on all 13 scenes and four plans each. A bound that hits both sides offers nothing. The exchanged call refused at N - 1 offers nothing again (the calls do not go back and forth: its probe is not smaller).
* **N, nothing is offered when the exchange would not give the same rows** (`NotOffered`): left, semi, anti; standard input (no operand, and `-`); `--join-prefix`; `--join-where`; a position in `--select` and in `--where`; a typed shared-name key; `--limit`, `--from`, `--top` (and a grouping paged, which is offered); a probe not smaller, and the same size; a `--query`; a header with a name twice; a csv answer with the other file's delimiter; and, in `MaxRows` (csv, JSON lines and other delimiters on both sides, `--threads` 1 and 4, a `--join` file of `--max-rows` - 1, `--max-rows`, + 1 and + 60 rows, a probe of `--max-rows` and + 1 rows, rows that cannot be counted, blank lines of a JSON-lines file), the file past `--max-rows` rows; each with its reason.
* **The repair is well formed** (`Mechanical`): the refusal validates against the schema of the contract, `repair.kind` is `choose` with one option whose only key is `argv`, strings all; the option is the call's arguments with the files, the pairs (and their types), the reader flags exchanged and nothing else changed (`test_the_option_is_the_call_with_the_two_files...`).
* **`--explain`** (`Explain`): the object, its words, for each reason; the old sentence is gone; the explain agrees with the refusal on 20 or more plans.
* **A, the accounting** (`test_join_j7_prune.py` `Accounting`): the program's count of the accounted bytes (the refusal at one byte below: `detail.held`, `detail.rows_held`) is the formula computed by the Python reference from the plan and the files, with the columns the plan names, and the bound is exact (accepted at N, refused at N - 1), on **75 random plans** over five shapes (text, `:int`, two pairs, USING, a prefix) and three pairs of formats (csv x csv, csv x JSON lines, JSON lines x semicolon csv), `--join-where` and the pushdown included, **36 of which hold fewer bytes than the whole record**; on raw files with quoted keys, cells with a comma, a quote and a line break (kept ones counted with their quotes); and a plan that leaves out only the key holds the whole record. Every plan is also accepted at the whole record's count (what was accepted before is accepted now). The existing gates that pinned the old count say the new one (`test_join_j3` through `join_ref3.execute`, which counts the record held; `test_join_j2`'s USING test, which said J2's reading 5).
* **I, identity, the pair of runs** (`Identity`): **556 plans** (the 16 shapes of the J3 generator for the four join types, and wide builds in seven combinations of formats, with typed keys, two pairs, USING, a prefix, `--join-where`, `--where`, `--group`, `--agg`, `--sort`, `--order-by`, `--top`, `--limit`, `--from`, `--rename`), each as json, as csv, at four threads with 7-byte ranges, and from standard input, equal to the Python reference; and, with `MAIN_TABLE` naming `main`'s binary (which holds the whole record), **3,336 runs whose status, standard output and standard error are the same bytes as `main`'s** (530 plans that answer, 26 that refuse), plus quoted cells and records over several lines in a column that is dropped, a one-column build and an empty one.
* **EQ**: the 166 plans of `scripts/corpus.py` hash to `fccb01b66dd7518d74611219a6c5aa29` on `main`'s binary and on this branch's (`Corpus`, run where the corpus files are).
* **F, no input reaches a panic** (`Fuzz`): **320 plans** over the 13 scenes, the four join types, random bounds (from 1 to the large one, around both files' counts) and junk flags (a prefix, `--join-where`, `--limit`, `--select '#1'`, `--max-rows`, `--max-join-rows`, `--absent`, `--join-delimiter`, `--max-line-bytes`, csv and json): no trap, every refusal tagged with a rule of the catalogue, every document valid, every offered option accepted by the flags and succeeding (or refused only by what the larger bound would also refuse, or by the groups' share of the flag): 263 refused, 57 answered, **30 options offered and run, 51 refusals with `none`**.
* **M, mutants** (`scripts/join_j7_mutants.py`, `--check` in CI): 106 mutants of the mask, the pruned record and its count, which builds prune, when the exchange is offered, how the exchanged call is written, whether the other file is read, the count of the rows that remain in the `--join` file, and the explain. **Run for real on this machine on the final tree (4 shards on the 4 cores, each in a checkout of its own, commit `acdffff`, whose sources are the ones of the head): 103 killed, 3 survived, and the 3 are equivalent** (argued in the docstring of the script: the delimiters after the last named column of a held record, dropped or held as one, and the refusal's check of the delimiter of a csv answer). **Every mutant of the earlier slices that this slice's code had made stop applying (`--check` caught them) was re-pointed and run again: `scripts/join_mutants.py` 223 of 223 killed, `scripts/join_jsonl_mutants.py` 68 of 68 killed** (`acdffff` for the first, `5e6bf01`, whose only other change is the regenerated authority files, for the second; the same 4 shards; the re-pointing adds the lines of `jtab.add` that come before a pattern, or the argument the build calls now take, and replaces the same text with the same text, so each mutates the semantics it did). History, so the numbers are not rounded: the first pass ran 89 of the 96 mutants of the time and killed 75; the 14 survivors were 11 gaps of the tests (a quoted key of a pruned build, a record over several lines under a composite or typed key, a probe that is a named pipe, a probe cut by `--max-rows`, an empty `--join-prefix`, a position in `--on`, a header with an empty name, the keys read the wrong way round, a ragged row, a refused read taken for a fit, `--join-where` over several lines), each of which got a test and was then killed, and 3 equivalent. 7 mutants had names that began with `--`, which `mutlib` reads as a flag, so the first pass never ran them: renamed (with a guard in the script) and run, 7 of 7 killed. The 10 mutants of the `--max-rows` check (reading 10) were added with it and are among the 106. One run of the earlier lists failed at its baseline for a stale build left by the mutant before it (`mutlib` restores the sources, not the build): that is a fault of running the lists one after the other, not of the tree; they were run again after `cancho build`.

**Against `main`'s binary** (`TOOLBOX_BIN` the `build` directory of `origin/main` at `8fde71b`; each test counted once whatever its sub-tests; this machine, re-measured by the follow-up of this slice, which **replaces an earlier figure of 49 tests and 32 failures that could not be reproduced**): **at the first commit of the pull request** (`ac27f4e`, the tests alone) the two new modules ran 36 tests and **30 did not pass** (365 failing sub-tests; 5 passed, 1 skipped for want of the corpus files in that checkout), and of the older join modules that commit changed (`test_join_j2`, `test_join_j3`, `test_rules`) **9 did not pass** (225 tests ran in `test_join_j2`, `test_join_j3`, `test_join`, `test_rules`; 3 more, the `Authority` `manifest.py --check` tests, failed in that scratch checkout for want of its `build/` and are not counted). **With the final test files** the two new modules run 49 tests on `main`'s binary and **42 do not pass** (364 failing sub-tests, 38 tests failed as a whole, 1 error), each because `main`'s refusal has `repair.kind` `none`, because its build holds the whole record, or because its `--explain` says "the swap repair arrives with a later slice"; the 7 that pass are the five identity gates (a pair of runs passes on both, that is its point), the corpus md5 and the plan that names every column.

### 22.4 What the pruned build saves

`scripts/join_prune_rss.py` (this machine, 4 shared cores, one thread: the join is read on one core; the minimum of 5 runs; peak resident set from `wait4` of a small launcher, in bytes; the call is `table --root D --join b.csv --on pk=bk --select ... --format csv --max-state-bytes 1073741824 p.csv` with a probe of 5 rows; the build file has unique keys in random order and cells of 10 bytes; the accounted bytes are the formula of 6.2 over the records held, computed by the script from the file and **checked against the program** (accepted at N, refused at N - 1) for both binaries):

| build file (rows x columns, bytes) | the plan names | accounted, `main` | accounted, J7 | resident, `main` (MiB) | resident, J7 (MiB) | resident J7 / `main` |
|---|---|---:|---:|---:|---:|---:|
| 8000 x 152, 13,263,536 | names 2 columns | 13,901,780 | 1,862,890 | 19.0 | 5.3 | 0.279 |
| 8000 x 152, 13,263,536 | names half | 13,901,780 | 7,862,890 | 19.1 | 11.4 | 0.599 |
| 8000 x 152, 13,263,536 | names every column | 13,901,780 | 13,901,780 | 18.9 | 19.4 | 1.027 |
| 100000 x 22, 22,888,966 | names 2 columns | 30,977,780 | 10,388,890 | 44.6 | 16.8 | 0.377 |
| 100000 x 22, 22,888,966 | names half | 30,977,780 | 20,388,890 | 44.6 | 28.9 | 0.650 |
| 100000 x 22, 22,888,966 | names every column | 30,977,780 | 30,977,780 | 44.7 | 45.0 | 1.008 |
| 300000 x 8, 22,688,914 | names 2 columns | 47,177,780 | 27,188,890 | 65.1 | 41.7 | 0.640 |
| 300000 x 8, 22,688,914 | names half | 47,177,780 | 36,188,890 | 64.9 | 49.3 | 0.760 |
| 300000 x 8, 22,688,914 | names every column | 47,177,780 | 47,177,780 | 65.1 | 65.2 | 1.002 |

Measured on Linux-6.18.44-fc-v139-x86_64-with-glibc2.39, 4 cores shared with other work (the mutant runs of this slice were running), load average 5.41 before and 5.72 after (one-minute), minimum of 5 runs, `main` being the build of `8fde71b`. The accounted figures are equal to the formula for both binaries (`formula_holds_*` true in all nine cells: accepted at N, refused at N - 1). The resident set falls to 28%, 38% and 64% of `main`'s for a plan that names two columns of the three shapes, and to 60%, 65% and 76% for a plan that names half; a plan that names every column is 102.7%, 100.8% and 100.2% of `main`'s (that is, **not saved, and up to 2.7% more**: the cause was not investigated).

The accounted bytes fall by the bytes of the cells not held; the resident set falls by about the same amount, as the earlier slices measured the arrays to hold nearly all of the accounted bytes (sections 15.1 and 16.3: 1.5 to 2.8 times). A plan that names every column is the whole record on both binaries. No pass line was given by the design, and none is claimed.

### 22.5 The instruction budget

`scripts/join_callgrind.py --old main --new build/table --all` (callgrind, 38 cells: the 17 non-join cells of gate G6 and the cells of the joins and of the build, a probe of 20,000 and of 60,000 rows against a build of 5,000, the per-row cost the difference of the two counts over the difference of the rows; this machine; bound 1.01): **PASS, the worst ratio 1.0016** (the build row of a semi or an anti join, 1,276 to 1,278 instructions; the build row of `:int` and of two text pairs 1.0012 and 1.0011), the 17 non-join cells **1.0000** (identical per-row cost), every probe cell of the joins **1.0000**, the build row of J1's inner join **0.9968** and **the build row of a plan that names nothing of the build (`--select id`, the cell that takes the pruned path) 0.9956** (1,268.68 to 1,263.08). The first version of the pruned path, a generic loop over the columns, cost 1,386 instructions on that cell (**1.0926**, over the bound); the rule of reading 3 and the runs of reading 1 are what the measurement asked for. The totals of the large run differ by +0.106 million instructions on the non-join cells and by +0.4 to +0.55 million on the join cells (measured: the difference of the `new total` and `old total` columns); this is a cost of a call and not of a row (the per-row ratios above are of differences), and is put down to the table of flags being scanned by every lookup (section 17.2 item 11) and to the longer help of `--join` and `--max-state-bytes`, an attribution that was not measured separately.

### 22.6 What was not measured, and what did not run

* Any clock time (the machine is shared and has no instruction counter); any Mac figure; any figure on a quiet machine; the cost of the other file's read at the refusal in seconds (it is one pass of the build over the smaller file, bounded by `--max-state-bytes`; the instruction cost of a build row is in 22.5).
* Builds above 300,000 rows in the memory table, other widths than the three, the worst ratio of resident to accounted over all shapes (as in section 16.3), `--threads` above 4 (the join is read on one core).
* A `--query` repair: the text of a query is not rewritten (reading 5, code 3); the option for a query is `none`.
* The exchange under `--join-where`, `--join-prefix`, `#N` names or a typed shared-name key: refused with a reason (reading 5); no attempt was made to rewrite them.
* Not built, as the design says: the threaded probe (J6), the partitioned build (J8), the merge join (J9; **built by section 23**); `--report` over a join.
* Still open: the groups' and the sorted rows' share of the flag is not checked by the exchange (reading 6).
* The Mac, and `gram` (Linux x86-64, 16 cores) of the earlier sections: every figure of this section is from the machine named at its head; the conformance suite was run once here before the last push and by CI.

## 23. J9 as built (2026-10-20)

> Everything below is **measured** unless it says *computed* or *reading*. Conditions: one Linux x86-64 virtual machine (4 cores shared with other work, kernel 6.18, Python 3.13, valgrind 3.22), compiler `cancho` e47d647, the build of `origin/main` at `5a439ee` as `main`. **No clock time is claimed anywhere in this section**: the machine is shared and has no instruction counter, so the figures are bytes (accounted, and resident from `wait4`), instruction counts (callgrind, exact whatever the load) and counts of cases.

### 23.1 What was built

* **`--join-sorted`** (a flag, never a fallback): it says that FILE and the `--join` file are both in ascending order of the key, and the join is a merge of the two. Each file is read once, in step; the rows of **one key of the `--join` file** are held at a time (the *window*: a table of its own, the table the hash join looks keys up in, made again when the key of the probe row moves on). The state is the largest group of equal keys that some probe row meets, counted in `--max-state-bytes` by the formula of section 6.2 (a held row costs its record + 16, the key + 60; a semi or anti join holds the key alone, at key bytes + 44). A `--join` file of any size runs when no such group passes the bound. The `--join` file's rows are not pruned (section 22): the window holds whole records.
* **The answer is the answer of the same call without the flag**, byte for byte, for every plan (reading 1). The emission is the hash join's own (`jnread.probe_one`, `jn3pass.probe_g`): the joined records, the padding of a left join, the semi and anti rows, `--max-join-rows`, `--where` and its split, `--join-where`, the groups and the sorter. For a FILE in order of the key that is **key order**: each row of FILE, then the rows of the `--join` file with its key, in their order.
* **`join.not-sorted`** (new rule, exit 8, repair `none`): the first row of either file whose key is below the key of the row before it. `detail.input` (`probe` or `build`), `detail.path`, `detail.row`, `detail.line`, `detail.previous_row`, `detail.previous_line`, `detail.key` and `detail.previous_key` (the key cells as written, quotes undone, at most 64 bytes each) and `detail.key_truncated`. The claim is checked and never trusted.
* **`limit.state-too-large`** with `detail.holder` **`join-group`** (no new tag): the rows of one key of the `--join` file that the window holds pass the bound alone. `detail.limit`, `held` and `rows_held` (before the row that did not fit), `build_row`, `build_line`, `group_key` (the cells of the key) and `group_key_truncated`. No swap is offered (`repair.kind` `none`): the window is not the file.
* **The flag set**: `--join-sorted` without `--join` is `args.required-flag`, with `--query` is `args.conflict` (a query has no spelling for it), with `--report` is the `args.conflict` a join already has. `--explain` says `join.sorted: true`, the order (`key order ...`), the work (`join: merge of two sorted files`, `first_output_after` the first probe row and the group of its key) and `join.not-sorted` among the refusals the data could still give; `build_state.accounted_at_most` is `null` and `may_pass_bound` true (what a group costs cannot be had from the size), `swap` is not offered. A call without the flag has the explain it had.
* Code: `jnmerge.cho` (new: `next_row`, the reader of a record of either file in csv or JSON lines; `advance_build`, the build cursor; `sync_window`; `merge_pass`; `cmp_keys`), a block of `jnrun.run` (the set-up of the groups and the refusals of the build are repeated there, between `// BEGIN merge` and `// END merge`, a region that `scripts/mutlib.py` does not count a pattern of the hash join in), `jnfail.not_sorted` and `jnfail.group_too_large`, `jtab.small`, the rule row, the usage and help, `jnexplain` and `explain`. **The authority row is unchanged** (`scripts/manifest.py --check`: `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`; the merge only reads the two files it already opens). No loop of the hash join is edited.

### 23.2 Where J9 differs from sections 1 to 22 (every narrow reading)

1. **The output is the hash join's, and for a sorted FILE that is key order.** Section 9 says the output order of the merge is "key order, not probe order", a different contract from the hash join's. Because FILE must itself be in order of the key, its order *is* key order, and the two definitions agree: the merge gives each row of FILE, in file order, and for it the rows of the `--join` file of its key in theirs (the cross product of a group is probe-major). The different contract is the one of the *inputs* (declared, checked, sorted) and of the *state* (one group), not another order of rows. **The consequence is a gate that can fail**: the bytes of the merge equal the bytes of the call without the flag (`Merge`, `Plans`, `Threads`), checked on every run; and the rows equal a sorted-merge written as two cursors with no index (`join_ref9.merge_ref`). A design that wanted another order within a key (build-major) would be a different flag.
2. **The order of a key.** A text key is compared bytewise, as unsigned bytes (the cell with its quotes undone; a shorter cell first when it is a prefix of the other; an empty cell is the smallest; `B` before `a`). A typed key is compared by its value: its 8 bytes are the value plus 2^63, big endian (section 5.2), so `9` comes before `10` for `:int`, `007` and `7` are one key, and two spellings of one instant are one key. Several pairs are compared pair by pair, left to right, each by its own rule (`("a", 10)` before `("ab", 1)`). A key may repeat (the check is *not below*, not *strictly above*).
3. **What is checked, and when.** The key of a row is checked when it is read, against the key of the last row of the same file whose key was read. A row that `--join-where` rejects, or that a leading condition of `--where` removes before the join (section 8.1), has its key unread and so is not checked: the order is the order of the keys the join reads. The two files are read in step, so the refusal is the first met in that order, not "the build before the probe" of section 5.6; a file with a typed key cell that is not of its type, a ragged row, a bad quote or a long line is refused by its own rule with the same facts as without the flag (`Refusals`), at the point the merge reaches it, after the rows of the keys before it. A ragged row of the `--join` file ends the read at once (without the flag the build is read whole first, so no row is written).
4. **The `--join` file is read to its end** when the run ends because FILE does: a disorder, a bad cell or a row past `--max-rows` after the last key of FILE is found, and refused. A run that stops early does not read the rest: a full page (`--limit`, JSON's default page) or `--max-join-rows` ends the read where it is, and a disorder past that point is not found (`test_a_full_page_ends_the_read...`).
5. **The window is the group a probe row meets, and nothing else.** A group of the `--join` file that no probe row has is read and left, and costs nothing: the bound is *the largest group some probe row meets*. The window is exact (accepted at N, refused at N - 1: `Bounded`), computed from the files by `test_join_j9.window_need`.
6. **One flag shared by the window and the groups (a choice).** A plan that groups or sorts gives the groups `floor(--max-state-bytes / 2)` and the window the rest: the window is not known when the first group is made (it is not for the hash join either, but there the build is held first). `detail.build_bytes` of a refusal of the groups is what the window was given. A plan that does neither gives the window the whole flag.
7. **Standard input and threads.** Standard input can be FILE (it is streamed, as always); the `--join` file is a file. `--threads` is read on one core with the same bytes, as for every join (`work.threads` says so). Each file is read once, so there is nothing for a thread to split.
8. **JSON lines and delimiters work**: either file may be JSON lines or csv with a delimiter of its own; the key is read from the same csv record the hash join reads (`jrec.csv_record`).
9. **`--query` has no spelling for it** (reading: `args.conflict`). A later slice may give the query form a word; it would be a pure translation to the flag.
10. **Pruning is not applied** (section 22): the window holds whole records, and the bound is the formula over them. A group of narrow records is cheap whatever the plan.
11. **Early version of the key display.** `detail.key` is the cells as the file writes them (quotes undone), not the key bytes, so a typed key shows `007` and not `7`; at most 64 bytes of a cell, with `key_truncated`.

### 23.3 The gates

Written before the code (`30c0c73`, tests alone, pushed alone), each able to fail:

* **A, the differential** (`Merge`, `Plans`): sorted tables of every kind of key (text with empty cells, unicode, quoted and awkward cells; `:int`, `:dec(2)`, `:float`, `:date`, `:timestamp` and `:datetime` with several spellings of a value; two pairs; USING; the key in other columns than the first; a prefix), duplicate groups of 1 to 6 rows on either side, a group of 150 x 160 rows, empty files and empty keys, CRLF, BOM, `quote_all`, a missing final newline, csv with `,` `;` and tab, JSON lines on either side: for every join type the rows are those of the sorted-merge reference, in key order, **and the bytes of the call without the flag**; and **191 random plans** of J3's generator (`--where` and its split, `--join-where`, `--select`, `--group`, `--agg`, `--sort`, `--order-by`, `--top`, `--limit`, `--from`, `--rename`) over six shapes and four combinations of formats equal the hash join's Python reference as json, as csv, from standard input and at four threads with 7-byte ranges.
* **B, the order** (`Unsorted`, `Refusals`): for every kind of key, either file, every join type: refused at the first row below its predecessor with the row, line, previous row and line, and both keys right (88 runs and more), in csv, JSON lines, `;` and tab, at 1 to 64 threads, from a pipe, past the last probe key, with a tie that is not a disorder, `9` before `10` as an integer and not as text, bytes order and the empty key smallest, two pairs component by component, a row the join does not read, a full page; and the refusals of the readers of either file (a typed cell, a ragged row, a bad quote, an unterminated quote, a long line, `--max-rows`, a refused `--join-where` cell, a JSON-lines line) equal to the call without the flag.
* **C, bounded memory** (`Bounded`): a build of 600 keys and 3,200 rows (15 times the bound) runs where the hash join is refused (`limit.state-too-large`, `join-build`); the state is the formula over the largest group a probe row meets, accepted at N and refused at N - 1 for the four join types, text, `:int`, composite, `:dec` and USING keys; the refusal names the row, line and key; a group no probe row has costs nothing; the groups share the flag; and the peak resident set of a build of 160,000 rows is no more than 1.5 times that of 20,000 plus 2 MiB and under half of the hash join's.
* **D, the same bytes** (`Threads`): 7 thread counts x 5 chunk sizes x 4 join types x text and `:int`, file and standard input, csv and json; a page and `--max-join-rows` and `--max-rows` as without the flag.
* **E, the surface** (`Surface`): the flag, its help and the usage; the rule (exit 8, `never`) and the fixture of `test_rules`; the refusals of the flag set; `--explain`; the MCP definition; **330 fuzzed calls** over 18 cases, sorted and broken, with random plans and junk flags: no trap, every refusal tagged with a rule of the table (225 refused, 132 of them `join.not-sorted`, 105 answered).
* **F, a call without the flag is what it was** (`Unchanged`): the 166 plans of `scripts/corpus.py` hash to **`fccb01b66dd7518d74611219a6c5aa29`** on `main`'s binary and on this branch's; and with `MAIN_TABLE` naming `main`'s binary, 16 cases x 4 join types x json and csv x three argument sets (`--explain`, `--threads 4`) give **the same status, standard output and standard error** (384 calls compared).
* **M, mutants** (`scripts/join_sorted_mutants.py`, `--check` in CI): **55 mutants, run for real in 4 shards on the final tree (14, 14, 14 and 13): 55 killed, 0 survived.** The first run left two: a refused cell of `--join-where` in the build and a full page were each given a test, and the walk of several pairs (`iy = iy + lx` for `iy = iy + ly`) is argued equivalent in the docstring of the script (the advance is reached only after two components compared equal, and equal components have equal lengths) and is not in the list.
* **EXPL, MCP, AU**: `scripts/manifest.py --check` and the embedded manifest are unchanged in labels; `schemas.py`, `mcp.py`, `site.py`, `seo_check.py` and `publish_numeric.py --check` pass; the explain schema gains `join.sorted` and three values of `work.reads` and one of `work.class`.

**Against `main`'s binary** (`TOOLBOX_BIN` the `build` directory of `origin/main` at `5a439ee`; each test counted once whatever its sub-tests): at the first commit of the pull request (the tests alone) the two new files ran as follows (re-counted on the final test files with `main`'s binary): `test_join_j9` 51 tests, **4 passed and 47 not passed** (529 failing sub-tests; 39 whole-test failures and 1 error), `test_rules` 5 tests, **2 passed and 3 not passed**; on this branch's binary 51 of 51 and 5 of 5 pass. The 4 that pass on `main` are three of `Unchanged` (the md5 of the 166 plans, the bytes of the plain joins against `main`'s binary, a lookalike flag still unknown) and the MCP definition test, which reads the generated file of the tree and not the binary.

### 23.4 What the merge holds

`scripts/join_sorted_rss.py` (this machine, 4 shared cores, one thread, minimum of 5 runs; peak resident set from `wait4` of a small launcher; the call is `table --root D --join b.csv --on pk=bk --select pk,pt,bk,bg,bv,bt --format csv --max-state-bytes N [--join-sorted] p.csv` with a probe of 2,000 rows in order; the build file has keys `k%09d` in order, in groups of G equal keys, cells of 12 bytes; the accounted bytes are the formula of 6.2 computed by the script from the files and **checked against the program** for both: accepted at N, refused at N - 1):

| build (rows x group) | build file bytes | accounted, merge | accounted, hash join | peak RSS, merge | peak RSS, hash join | call without the flag at the merge's bound |
|---|---|---|---|---|---|---|
| 100000 x 1 | 5,000,012 | 135 | 13,500,000 | 3.35 MB | 21.52 MB | exit 8 (`limit.state-too-large`) |
| 100000 x 100 | 5,000,012 | 6,570 | 6,570,000 | 3.61 MB | 13.97 MB | exit 8 (`limit.state-too-large`) |
| 300000 x 1 | 15,000,012 | 135 | 40,500,000 | 3.55 MB | 58.47 MB | exit 8 (`limit.state-too-large`) |
| 300000 x 1000 | 15,000,012 | 65,070 | 19,521,000 | 3.58 MB | 27.19 MB | exit 8 (`limit.state-too-large`) |

Machine `Linux-6.18.44-fc-v140-x86_64` glibc 2.39, 4 cores, load average 1.7 before and after, minimum of 5 runs, one thread. The formula holds for both (accepted at N, refused at N - 1) in all four builds.

**What it shows**: the bound the merge needs is the largest group a probe row meets (135 bytes for a build of unique keys, 65,070 for groups of 1,000), where the hash join needs the whole file (13.5 and 40.5 million). The resident set of the merge is the same 3.4 to 3.6 MB for all four builds: it does not grow with the rows of the build. The hash join's grows with them (and the merge is below it in every cell). The call without the flag at the bound of the merge is `limit.state-too-large` in all four (exit 8), as the gate `Bounded` also shows.

### 23.5 The instruction budget

`scripts/join_callgrind.py --old main --new build/table --all` (callgrind, the cells of section 22.5 and the others; this machine; bound 1.01): **PASS: 38 cells, every ratio of the per-row cost is 1.0000 at four decimals (the worst is 1.0000)**; the total of the join cells is about 7.0 million instructions higher in the large run, a constant per call that cancels in the difference of the two runs (the cause was not investigated further).

**The merge against the hash join on the same sorted files, for the record** (`scripts/join_sorted_callgrind.py`; a probe of N rows and a build of N / 4 rows, a key repeated 4 times in the build, one probe row in five with a key the build lacks; the cost of a row the difference of the counts at 20,000 and 60,000 rows over the difference of the rows; **instructions, not time, and no claim that one is faster**; the window starts at 16 entries and grows):

| cell | hash Ir/row | merge Ir/row | merge / hash |
|---|---|---|---|
| text, inner join | 6433.56 | 9143.69 | 1.421 |
| text, left join | 7055.51 | 9400.29 | 1.332 |
| text, semi join | 1990.56 | 4332.51 | 2.177 |
| text, anti join | 1479.87 | 3791.09 | 2.562 |
| int, inner join | 6786.91 | 9237.51 | 1.361 |
| int, left join | 7037.40 | 9487.99 | 1.348 |
| int, semi join | 2273.91 | 4814.07 | 2.117 |
| int, anti join | 1788.37 | 4239.46 | 2.371 |

The cost of the merge per row is the cost of making the window again for each key of FILE (a table of 16 entries, and a read of the key of the build row); a first version that made it with `jtab.empty` (1,024 entries, 64 KiB of rows) cost 35,000 to 40,000 instructions a row in all eight cells, which is why the window starts small.

### 23.6 What was not measured, and what did not run

* Any clock time (the machine is shared and has no instruction counter); any Mac figure; any figure on a quiet machine; the speed of the merge against another library (neither DuckDB nor csvtk was run on a sorted pair here).
* A build of more than 300,000 rows in the memory table, other widths than 4 columns of 12 bytes, groups of more than 1,000 rows, `--threads` above 4 (the join is read on one core), a probe of other than 2,000 rows.
* The quoted text of a typed key is compared after its value is read: the cost of the date types is not separately counted.
* A threaded merge (a thread would need the reading of the other file at the place its key reaches: a design of its own), a merge that reads FILE unsorted (a different algorithm), a `--query` spelling, and pruning of the window's records.
* Still open: a disorder past a page that is full (reading 4); the groups' and the window's split of the flag is a choice, not a measurement (reading 6).
