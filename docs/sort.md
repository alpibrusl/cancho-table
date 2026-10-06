# `table --order-by`: sorting rows

The adversarial round (`docs/adversarial.md`, cells A1 and A2) found that `table` could not sort rows: DuckDB does
1M rows in 0.13 s (all its threads), csvtk in 0.9 to 2.3 s. This is the design of the feature, written before the code.
What was built, and what it measured, is below the line at the end.

## The flag

```
table [--select NAMES] [--where EXPR] --order-by KEYS [--top N] [--limit N] [--from N] [--max-sort-rows N] [--max-state-bytes N] FILE
```

`KEYS` is a list separated by commas, as `--select`'s names are, each key `[-]NAME[:int]`:

| | |
|---|---|
| `NAME` | a header name, or `#N`, the Nth column, as in `--select` and `--where` (a comma, a backslash or a leading `#` in a name is escaped with a backslash) |
| `-NAME` | descending. The sign of `--sort` for groups. A name that starts with a minus is written `\-NAME` |
| `NAME:int` | compare as exact integers, as `--where`'s `COLUMN:int` does. Without it the key is the cell's bytes, compared bytewise (`a` before `b`, `Z` before `a`, `10` before `9`), the same as `--where` text comparisons and the group order. A name that ends in `:int` is written `NAME\:int` |

`--order-by -bytes:int,status` sorts by `bytes` as a number, largest first, and ties by `status` as text.
The name `--order-by` and not `--sort`: `--sort` already means "the output column the groups are ordered by", with
different keys (output labels, not header names) and no `:int`; two meanings for one flag would be a trap.

## Semantics

* **Stable.** Rows whose keys are all equal come out in the order they were in the file, whether the keys are
  ascending or descending (a descending sort reverses the comparison, not the order of ties). The oracle is Python:
  `sorted` by the last key first, each pass `sorted(rows, key=..., reverse=desc)`, each stable and `reverse=True`
  also keeping equal elements in order.
* **Text keys** are the cell's bytes with a quoted cell's doubled quotes undone (the same bytes `--where` compares),
  compared bytewise, a shorter value first. An empty cell is the empty text: it sorts first ascending, last descending.
* **`:int` keys** are exact 64-bit integers, parsed as `--where` parses them: an optional sign and digits. A cell that is
  not one is a refusal naming the row, the column and the value, with `context` `order-by`: `value.not-integer` (an
  empty cell is not an integer), or `value.integer-overflow` (more than 64 bits). The read stops at the first one, as
  `--where` does; keep such rows out with `--where` first.
* **Composes with `--where`** (filter, then sort what is left) and with `--select` (the columns returned; the keys need
  not be among them). The sort is over the *rows*, so with `--group` or `--agg` it is `args.conflict`: a grouping is
  ordered with `--sort` and `--top`, which are untouched. With nothing else the whole row is returned, as with
  `--where` alone.
* **Paging** is `--limit` and `--from` as for any rows: the page is the rows `from` to `from + limit` of the *sorted*
  answer, and `next` is the `from` of the following one. Each call is a fresh read: nothing is kept between pages.
* **`--top N`** with `--order-by` is the first N rows of the sorted answer, no more (as it is for groups); `--limit` and
  `--from` page inside it.
* **`--max-rows`** bounds the rows read, and sorting a prefix of the file is not sorting the file: a read that stops at
  `--max-rows` with rows left is `limit.too-many-rows`, as csv already is, in json too.
* **`--format csv`** writes the sorted rows, header first, after the read is complete (a sort cannot start writing before
  it has seen the last row); json is a page, as for `--select`.
* **`--threads`**: see "Threads" below.

## Bounds

A sort has state; every bound is a rule with a repair that suggests the way out.

* **`--max-sort-rows N`** (default 1,000,000; ceiling 20,000,000): the most rows *held*. One more is
  `limit.too-many-sort-rows`, whose hint is `--top N`, `--limit`, or `--where`.
* **`--max-state-bytes N`** (the existing flag: default 64 MiB, ceiling 1 GiB): the bytes held, which for a sort are the
  records, the keys that had to be unquoted, and the index (24 bytes plus 16 for each key). Past it, the rule
  that exists, `limit.state-too-large`, whose message now covers sorting.
* **Top-N is bounded memory.** With `--top N`, or with a json page (`--limit` is 1000 by default), `table` keeps
  the `K = from + limit + 1` best rows (`N` for `--top`), not the file. A row that does not beat the current K-th is
  dropped at once, its key compared where it lies in the record; every `K` rows or so the held rows are sorted and
  cut back to `K`. Memory is `O(K)`, the file can be any size, and a million-row file sorts for its first 1000 in a
  megabyte or two. At most `2K + 1` rows are held between cuts, and that is what `--max-sort-rows` bounds: a page
  past it is refused (`--from` too deep) with the same rule and a hint to narrow with `--where`.
* **A full sort** (csv with no `--top`, or `--limit` large) holds every row, within the two bounds above. There is no
  external sort in this version: past the bound the answer is the refusal, and that is the design, not a gap to be hidden.
* `--max-line-bytes` still bounds a record.

## How it is built

* The record is kept as it was read (CR trimmed), in one buffer, and the cells are found again by `reader.fields` when a
  row is written; a row of the answer costs what a row of `--select` costs, and a row that is never written costs only
  its bytes. Nothing is parsed twice that need not be.
* A key is a `(value)` for `:int`, and for text an `(offset, length)` into the record where the cell has no doubled
  quote, or into a side buffer where it had to be unquoted.
* The order is an index sorted by a bottom-up merge sort, which is stable, over rows held in the order of the file (a cut back
  keeps the sorted order, and the rows that come after it are later in the file): equal keys keep their order. The
  comparison looks at two 7-byte words of the first text key (or the integer itself, for a first `:int` key) before the
  full keys. This is the group sort's merge sort (`agg.sort_into`) adapted to the row index; the contract's
  `toolbox.sort` has no comparison that can see two keys of one record, so it is not used (its limits do not allow it).
* A new module, `sorter.ls`, holds the state and the sort; `engine.order_row` is what the read does with a row, in
  place of `process_rows`; the sorted answer is written with `engine.emit_buf`, the same writer as `--select`, so
  the cells, the quoting and the page budget are the ones that are already tested.

## Threads

The sort reads sequentially for now: `--threads N` with `--order-by` is accepted and runs the sequential read, and the
answer is the same bytes (it is the same code). The design that would parallelise it is per-range sorted runs
merged in order, with the stable tie-break by file position making the merge deterministic; it needs the groups'
serialise-and-merge machinery for rows, and the benchmark below says whether it is worth it. It is not in this branch.

## Rules

New: `limit.too-many-sort-rows`. A bad key is `args.bad-value` (as `--select`'s list is), an unknown column is
`column.unknown` (its detail says the flag is `--order-by`), an ambiguous one `column.ambiguous`, as for `--where`.
Reused: `value.not-integer` and `value.integer-overflow` (context `order-by`), `limit.state-too-large` (its message
now covers sorting), `limit.too-many-rows`, `args.conflict`, `args.required-flag`.

---

## What was built and what it measured

`--order-by` and `--max-sort-rows` as designed, and `--top` for rows. A key's `:int`, `-`, `\-` and `\:` are as above.
Two points where the build differs from the sketch above: the sort is stable because the merge is (the rows are not
numbered), and `--threads` is accepted and sorts sequentially, with the same bytes, as said.

**Everything held is a record of the file plus ints.** A million rows of 40 bytes with one key hold about 130 MB
(Mac RSS 173 to 181 MB for A1 and A2; 98 to 101 MB on Linux); the top 1000 hold 2.1 MB, the same as `--select`.

### The benchmark

`scripts/adversarial.py --cells A1,A2,A3,A4`, minimum of 7 on the Mac and 5 on Linux (cores 0 to 5, niced, the soak running,
no DuckDB there), the 1M-row file `F2` of `docs/adversarial.md` (`k1m` a unique text key in random order, `u` a unique
integer in random order, `v` an integer with about ten rows to a value), seconds. **Every contender's whole output is
compared with Python's sort of the rows before anything is timed** (A4, whose ties the incumbents break in their own
ways, is compared as "ordered by `v`, and the same rows"; `table`'s order on ties is the stable one, and equals
`sort -s` byte for byte on the standard file).

| cell | `table` | csvtk | Miller | DuckDB 1 thread / default | `sort` |
|---|---:|---:|---:|---:|---:|
| **A1** text key, 1M rows, Mac | **0.465** | 0.806 | n/a | 0.482 / 0.134 | 1.86 |
| A1, Linux | **1.077** | 2.342 | 5.789 | n/a | 1.030 |
| **A2** integer key, Mac | **0.435** | 1.830 | n/a | 0.414 / 0.119 | 1.74 (`-n`) |
| A2, Linux | **0.895** | 4.095 | 4.682 | n/a | 0.892 (`-n`) |
| **A3** first 1000 by an integer, descending, Mac | **0.064** | 1.919 | n/a | 0.197 / 0.084 | 1.80 |
| A3, Linux | **0.152** | 4.764 | 3.903 | n/a | 0.965 |
| **A4** integer key with ties, Mac | **0.563** | 2.164 | n/a | 0.430 / 0.132 | 2.22 (`-s -n`) |
| A4, Linux | **1.117** | 4.353 | 2.265 | n/a | 0.658 (`-s -n`) |

Peak memory: 173 to 181 MB (Mac) and 98 to 101 MB (Linux) for a full sort of 1M rows, against csvtk's 250 to 270 MB and
Miller's 1.1 to 1.5 GB; **2.1 to 2.3 MB for the top 1000** (DuckDB: 64 to 69 MB).

**The ratios, with the losses.** Against csvtk `table` is 1.7x (text) to 4.2x (integer) faster on the Mac and 2.2x to 4.6x on
Linux, and 30x on a top-1000. Against Miller 2x to 5.4x. Against DuckDB at **one thread** it is level on the text key
(0.97x: 0.465 against 0.482) and **1.05x slower on the integer key** (0.435 against 0.414), **1.31x slower with ties**
(0.563 against 0.430), and 3.1x *faster* on the top 1000. Against DuckDB's **default** (all its threads, on a 16-core
Mac) `table` is **3.5x (A1), 3.7x (A2) and 4.3x (A4) slower** and 1.3x faster on the top 1000. That is a loss and it is the
known one: the sort is sequential, DuckDB's is parallel (see below). Against the shell's `sort` on Linux, which uses all
the cores it is given, `table` is level on an integer key (0.895 against 0.892), 1.05x slower on text, **1.7x slower** on
the ties (`sort -s -n` 0.658 against 1.117), and 6.3x faster for the top 1000.

### Rounds

The rule is the one of the cell-cost rounds: faster beyond noise and the output the same (here the tests, 1,600 random
plans against Python, are the gate), or reverted with its number.

| # | idea | result | kept |
|---|---|---|---|
| S0 | the first version: 7-byte prefix of the first text key, `(prefix, rows)` merge sort | A1 0.807 s, A2 0.487 s on the Mac (A1 1.0x csvtk, 1.7x DuckDB t1; A2 4.6x csvtk, 1.1x DuckDB t1) | |
| S1 | **a second 7-byte word** of the first text key (bytes 7 to 13): the keys of A1 (`key0000001`) share their first four digits, so the first word settled one pair in a hundred and the rest went to a full byte compare | A1 **0.807 to 0.455 (-44%)**, descending 0.903 to 0.508; A2 unchanged | yes |
| S2 | insertion sort of runs of 8 before the merge (three fewer passes) | A1 0.464 to 0.450, A2 0.452 to 0.445 (-3%, -1.5%) | **no, reverted**: under the line, and more code |
| S3 | the prefix travels with the row index in the merge (pairs), so the merge reads it in order and not at random | A1 0.396 to 0.392, A2 0.386 to 0.372 (-1%, -3.6%) | **no, reverted** |

What is left in the integer sort: the same file already in order takes 0.197 s, the random one 0.43 to 0.50: the extra
is the random access of the merge and of writing the rows out in the order of the sort (a cache miss for each of a
million 40-byte records), which no change of arithmetic removes. Sorting by two keys whose first has few values
(`--order-by s,v:int`, four values then a number) costs 0.87 s, twice a single key: most pairs tie on the first key's
words and take the full comparison. Both are in the backlog.

### Tests and mutants

`tests/conformance/test_sort.py` (18 tests): a differential test against Python's stable sort over **1,600 random plans**
(tables with ties, empties, quoted fields with commas, quotes and newlines, the edges of 64-bit integers; one to three keys,
descending or not, `:int` or text, `#N` positions, `--where`, `--select` that leaves the keys out, `--top`, `--limit`,
`--from`, csv and json, and `--threads` on one in ten, which must give the same bytes; about one plan in twenty is a
refusal, and its row, column, value and context must be the ones the oracle names); 500 damaged files and odd keys for a
refusal or an answer and never a trap; the bounded top-N cut back about 200 times over 600 rows with ties, descending,
text and integer keys; the page chain; the byte budget; the bounds and their refusals; the names with a minus, a colon and a
comma; quoted keys with doubled quotes against unquoted keys with a quote. `test_memory.py` gains the flat top-N and the
full sort within and at its bound. `scripts/sort_mutants.py` has 44 mutants of the sorter, the plan and the way the rows are
written, with the equivalent ones said in the script. Its first run had four survivors, which were real gaps:
a quoted key's doubled quotes (the test data had only quoted keys, so raw bytes sorted the same; a key `x"#`
written without quotes sorts the other way round), and two equivalents (the rows' numbers, which stability makes unnecessary
and which were removed, and a fallback that refuses at the same row either way).
