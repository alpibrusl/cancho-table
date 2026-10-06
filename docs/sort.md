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
* The order is an index sorted by a stable merge sort that compares a 7-byte prefix (or the integer itself, for a first
  `:int` key) before the full keys, and breaks every tie by the row's number in the file: the sort is stable by
  construction, and does not depend on the algorithm being stable. This is the group sort's merge sort (`agg.sort_into`)
  adapted to the row index; the contract's `toolbox.sort` has no comparison that can see two keys of one record, so it
  is not used (its limits do not allow it).
* A new module, `sorter.ls`, holds the state and the sort; `engine.order_row` is what the read does with a row, in
  place of `process_rows`; the sorted answer is written with `engine.emit_buf`, the same writer as `--select`, so
  the cells, the quoting and the page budget are the ones that are already tested.

## Threads

The sort reads sequentially for now: `--threads N` with `--order-by` is accepted and runs the sequential read, and the
answer is the same bytes (it is the same code). The design that would parallelise it is per-range sorted runs
merged in order, with the stable tie-break by file position making the merge deterministic; it needs the groups'
serialise-and-merge machinery for rows, and the benchmark below says whether it is worth it. It is not in this branch.

## Rules

New: `limit.too-many-sort-rows`, `order.syntax`? No: a bad key is `args.bad-value` (as `--select`'s list is), an unknown
column is `column.unknown`, ambiguous `column.ambiguous`, as for `--where`. Reused: `value.not-integer`,
`value.integer-overflow` (context `order-by`), `limit.state-too-large`, `limit.too-many-rows`, `args.conflict`.

---

(The results are added below, after the code is built and measured.)
