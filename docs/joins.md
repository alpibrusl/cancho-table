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
| spill | **not in v1.** Past the bound: the refusal. The designed route past memory is a **merge join over two sorted inputs**, a different contract (section 9) | bounded memory and no hidden disk; no write authority is added |
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
| `--join-delimiter`, `--join-input-format`, `--join-fields` | the build side's reader, **later slice**; defaults are the probe side's `--delimiter`, `--input-format`, `--fields` |

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
  alias.column". Reason: the translator knows no header (`docs/query.md` section 1: it writes flags and the existing parsers do the
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
  declared fields are the header). `--absent` and `--max-json-nodes` apply to both sides. `--discover` conflicts with `--join`.
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
  **If the rename feature of `docs/or-and-columns.md` is built** (it does not exist today), `--join-prefix` becomes sugar for renaming every build column and
  `join.column-collision` offers a rename in its repair; nothing else in this design changes.

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

`--on a=b:int`, `a=b:dec(S)`, `a=b:float` read **both** columns as the type, and two cells match when they have the **same value** as `--group` would put them in one group (`docs/numbers.md`, "What was built: N5"): an integer
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
  condition run before the key is read, so the guard works).
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

**Accounted bytes (choice, a formula; the gate A of section 10 holds the allocation to it):**

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
  at the refusal (the rest of the build is unread). A `left`, `semi` or `anti` join has no swap: its meaning depends on which side is which.
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
`detail.holder: "join-build"`, whose hint reads: "the build side does not fit in memory: filter it with `--join-where`, give it fewer columns by selecting fewer (not yet), make the smaller file the build side (the repair offers it for an inner
join), or raise `--max-state-bytes`, up to the ceiling `introspect` names (1,073,741,824). There is no join on disk." The same stance as the sort (`docs/backlog.md:96-98`).

**The route that stays inside the philosophy, designed later (J9), is a merge join over inputs the user declares sorted**: `--join-sorted` asserts that both files are in ascending order of the key (bytewise for text, numeric for a typed
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
| **J2** | `left`, `semi`, `anti`; typed keys; composite keys; USING; padding | the null-free semantics: padded cells and typed refusals (O-shapes), `anti` as the answer to "no match" |
| **J3** | `--where` split (probe/build/after), `--join-where`; `--group`, `--agg`, `--order-by`, `--top`, `--limit`, `--from` over joined rows with the shared budget; `--explain` | the pushdown definition equals the unpushed one except for the key read (a test pair for each), the shared budget (A), the plan document (EXPL) |
| **J3b** | the query form, `semi`/`anti` contextual, the `query.unsupported` features | Q-EQ, and that no query valid before changes |
| **J4** | standard input as the probe (the generated copy of the loop, `scripts/stream.py --check`), MCP `join` and `stdin` | ST, MCP |
| **J5** | jsonl on either side, `--join-delimiter` | the reader contract holds for a join (O with jsonl sides) |
| **J6** | threaded probe, if S0 passes. **Corrected by section 15:** S0 passes for reading and fails for a worker's own output; J6 waits for the cancho change of 15.3, or is built as option (a) under a cap | TH |
| **J7** | the swap repair; the build keeps only the columns the plan names | the repair is mechanical and tested; the pruned build is byte-identical (a pair of runs per plan) and measures the memory saved |
| **J8** (optional) | partitioned build | the same bytes, a faster 1M x 1M |
| **J9** (later) | `--join-sorted` merge join | the route past memory, a different order, its own gates |

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
| **Q11** | **The rename feature does not exist** (no `docs/or-and-columns.md`, no open pull request on 2026-10-14). | The prefix rule of 4.1 is enough for v1 and is replaced, not extended, if renaming lands. |
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
