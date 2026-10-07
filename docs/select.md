# `table --select`: the first real operation

`table --select NAMES FILE` returns some columns of a CSV/TSV file, as a page of
JSON rows or as CSV. This is the design, the choices made and the claims the code
relies on, each with how it was checked. Section 5 of cancho-tools'
`docs/next-tools.md` is the plan this is a step of; it was not edited.

## The operation

```
table --select NAMES [--limit N] [--from N] [--max-bytes N] [--format json|csv]
      [--root DIR] [--delimiter ,|tab|;] [--max-rows N] [--max-line-bytes N] FILE
```

One pass over the file, streaming. The header is read first and `NAMES` is
resolved against it **before any row is read**, so a refusal costs one line.

## Choices

**NAMES.** Comma-separated header names, exact and case-sensitive, returned in the
order given; a repeated name repeats the column. A header name with a comma (or a
backslash) is written with a backslash before it (`--select 'a\,b'`); a backslash
before anything but a comma, a backslash or a `#` is `args.bad-value`. Escaping was
chosen over refusing such names because it is unambiguous (the escape character is
itself escaped) and costs 25 lines; header names with commas are not rare
(`"Last, First"`).

**Positions, `#3`.** Included: a name that is `#` and digits is the 1-based column at
that position. It is the only way to select a column whose name is empty (the
contract's parser refuses an empty flag value) or one of several columns of the same
name, and it is cheap. It is unambiguous because a position is exactly `#` and
digits; a header that really is `#3` is written `\#3`, and `#id` or `a#1` need no
escape. `#0` and `#N` past the last column are `select.unknown-column`.

**A name that is several columns.** `select.ambiguous-column` (exit 8), naming the
name; select by position. Picking the first silently was rejected: a tool for agents
that guesses which `id` is meant is wrong the day the file gets a second one.

**An unknown name.** `select.unknown-column` (exit 3, "a named input does not
exist"), with `detail.available` (the first 50 header names, and the count) and, when
the name differs from a header only in case, a repair of kind `choose` whose options
are whole invocations (the same argv with that one name corrected; at most 5). The
repair is built by hand in `plan.cho`: the contract's `fail` has `retry` and `none`
helpers and no `choose` one. With no near name the repair is `none` and says to read
`detail.available`.

**JSON, `table.v2`.** `{columns, rows, row_count, truncated, next}`. A field is a
JSON string, or `{"b64": ...}` when its bytes are not UTF-8 (`toolbox.text`, as the
other tools). `rows` is a page: `--limit N` (default 1000, 0 refused, ceiling
1,000,000) and also a byte budget `--max-bytes` (default 1 MiB, ceiling 64 MiB) on
the rows' text, because a limit of rows does not bound memory when a row can be a
megabyte. A page ends at whichever comes first. `truncated` is true when the page
ended with rows left, and `next` is then `{"from": N}`, the 0-based index of the
first row not returned. A first row that alone is past the budget is
`limit.output-too-large`, not an empty page that could never advance.

`table.v2` also replaces v1 for the shape (`headers, column_count, row_count,
truncated`): the tool's description has one schema, and `rows` meant a number in v1
and means the rows in v2, so the shape's keys were renamed rather than overloaded.
v1 had one release as a skeleton and no consumer.

**Resuming re-reads the file from the start.** `--from N` is an offset into the
rows, and a line-oriented CSV with quoted newlines has no way to jump to the Nth
record, so a resumed call parses (and does not keep) the first N records again. This
is honest cost, not a cursor into the file: page k costs k pages of parsing, bounded
by `--max-rows`. Skipped records are only scanned: a page at row 900,000 of the benchmark file costs
0.030 s, the cost of the shape count (`docs/history.md`). But a loop over a 10-million-row file in pages of 1000 is quadratic.
Row indexes count ragged rows (which are never returned), so a `next` is stable
whether or not the file has any.

**CSV.** `--format csv` writes the selected columns, header first, to standard
output as RFC 4180: LF line ends (not the input's: one rule is simpler to rely on,
and a CRLF file's rows are LF-terminated after this), the **input's delimiter** (a TSV
selects to a TSV), minimal quoting (a field is quoted only if it holds the delimiter,
a quote, a CR or an LF; a quote is doubled; the quotes of a field that did not need
them are dropped), and a row of one empty field as `""` (a blank line is not a
record). It streams: output is written in 64 KiB blocks as rows are read, so memory
does not depend on the file. No row cap by default; `--limit` and `--from` apply if
given.

**How a CSV stream says it is whole.** A CSV file has no `end` record, and the
stream tools' NDJSON convention (`seek`, `hash` end with an `end` record whose
`complete` says it was not cut short) cannot be written into CSV. So: **standard
output is only CSV; standard error holds the verdict.** Success writes nothing to
standard error and exits 0. Anything that went wrong is the rule's sentence
(`table: rule: message`, the contract's text-mode form) on standard error and the
rule's exit code, and the rows written before it stand: a reader must treat a
non-zero exit as "this is a prefix". `--max-rows` reached with rows left is
`limit.too-many-rows` (exit 8) here, because a silently shorter CSV is the failure
that matters; as JSON the same bound is `truncated: true`, which is a field the
document has. `--format ndjson` was not built.

**Ragged rows are never selected.** A row with another number of fields than the
header is not in the output (a selected field of it could be the wrong column) and is
reported, once, as `parse.csv-ragged-row` after everything else is read: the JSON
document is then `ok: false` with `data` holding the rows that were whole; the CSV
exits 8 with the sentence on standard error.

**Records are kept while they are used.** To select from a record that spans lines
(a quoted field with a newline in it) the record is assembled in a buffer, so a
record, unlike in the shape, has a bound: `limit.record-too-large` when it holds more
than `--max-line-bytes` bytes. The shape still counts records of any size, since it
keeps none.

## The reader

`reader.fields` finds the fields of a record in one pass with one `memchr` per field
(for the delimiter, or for the quote that closes a quoted field) and no step per
byte. `reader.scan` answers only whether a line ends its record, for the shape and
for the continuation lines of a record that spans lines. Both are checked against
Python's `csv` module (strict) on the same fixtures, and the differential test of the
shape and of `--select` run the same generated tables.

## What is claimed, and where it is checked

| claim | check |
|---|---|
| the selected fields are exactly Python's `csv.reader`'s, in order, repeated for a repeated name | `tests/conformance/test_select.py`: 18 hand-picked tables (quoted newlines LF and CRLF, BOM, doubled quotes, an embedded delimiter, empty fields, a single empty column), 1,200 generated tables over the three delimiters |
| the CSV output is byte for byte `csv.writer(lineterminator="\n")`'s and reads back to the same fields | the same tests |
| `csv` output on the benchmark file is byte for byte `csvtk cut -f status,bytes`'s and `mlr --icsv --ocsv cut -f status,bytes`'s | md5 of the three, Linux (and `csvtk` on the Mac) |
| pages concatenate to the whole table, for any limit, with ragged rows counted | `test_paging_equals_slicing`, `test_paging_counts_ragged_rows_as_rows`, `test_the_budget_ends_a_page` |
| memory does not depend on the file | `test_memory.py`: peak RSS 1.5 to 1.9 MB for `--select` as csv and as json on a 2 MB and a 37 MB file, and for a page at row 900,000 |
| no input reaches a trap | 300 fuzzed inputs through four option sets (`test_deterministic_and_never_a_trap`) |
| every refusal has a rule, the exit code the table says, and is reached by a fixture | `test_rules.py` |
| each piece of the new logic matters | `scripts/select_mutants.py`: 29 defects (a quote not recognised, a doubled quote, a page one row off, a `next` one past, the budget, a position counted from 0, the escape of a comma in a repair, a record not bounded ...), every one killed; a thirtieth, a flag that only a dead variable carried, was removed with the variable |

## Measurements

The file is the design's (1,000,000 rows, 31,667,311 bytes), minimum of 5 interleaved
runs, output to /dev/null; `python3 scripts/bench.py`. Numbers are in the README and
`docs/history.md`. The shape of what was measured: `table --select status,bytes
--format csv` took 0.057 s on the Mac (csvtk -j 1 cut 0.202 s, 3.5x) and 0.137 s on
Linux x86-64 (csvtk 0.628 s, 4.6x; Miller 0.906 s), at 1.7 and 1.9 MB of memory against
csvtk's 23 and Miller's 329. This is the first benchmark in which `table` and the
incumbents do the same work (read, parse, select, quote, write), which the shape
benchmark was not.

## Not done

* `--format ndjson`; selecting by a range or a pattern; renaming a column in the
  output; non-UTF-8 header names in `--select` (a name is bytes on the command line, so
  they work on a platform whose arguments are bytes, but no test covers it).
* Pages are not cheap to resume (above). An index would need a second file or a
  seekable record format, and neither belongs in a tool that holds no state.
* The filter, sort, group and `--query` of the design.
