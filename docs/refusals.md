# Refusals: the protocol for an agent

Every way `table` can say no is a **rule**. A refusal is never a sentence to be parsed: it is a stable tag, an exit status, the facts, and, where one exists, a repair. This page is for the program (or person) that reads them. The tables are generated from `table introspect` by `scripts/site.py`, and CI checks they are current.

## The shape

With the default `--format json`, standard output is one JSON line against the schema `table.v2` (`schemas/table.v2.json`, also in `table introspect`). `ok` is false exactly when `errors` is present, and `error` is its first element:

```
{"ok":false, "command":"table", "schema":"table.v2", "error":{...}, "errors":[{...}], "meta":{...}}
```

An error has six members, all always present:

| member | what it is |
|---|---|
| `code` | the class, one of `GENERAL_ERROR`, `INVALID_ARGS`, `NOT_FOUND`, `PERMISSION_DENIED`, `CONFLICT`, `PRECONDITION_FAILED`; it fixes the exit status (below) |
| `rule` | the tag, `area.name`: **match on this**, it does not change |
| `message` | a sentence for a person; may be reworded |
| `hint` | what to do about it, in a sentence, or null |
| `repair` | `null`, or one of the three kinds below |
| `detail` | an object of the facts the rule names: the flag, the column, the row, the line, the limit, the value |

With `--format csv` standard output is only CSV and the verdict is standard error (`table: RULE: message`) and the exit status; rows written before a refusal stand, so a non-zero exit means the output is a prefix.

## Exit status

<!-- gen:exit-codes -->
| exit | `code` | meaning |
|---:|---|---|
| 0 | `SUCCESS` | what was asked was done; zero matches is success |
| 1 | `GENERAL_ERROR` | the operating system failed mid-operation, or the tool caught a bug in itself |
| 2 | `INVALID_ARGS` | the invocation is malformed or names something of the wrong kind; nothing was read |
| 3 | `NOT_FOUND` | a named input does not exist |
| 4 | `PERMISSION_DENIED` | the operating system refused, or the tool's own confinement did |
| 8 | `PRECONDITION_FAILED` | the answer is no, or a limit was reached |
<!-- /gen:exit-codes -->

## Repair kinds

| `repair.kind` | what it holds | what to do |
|---|---|---|
| `retry` | `argv`: a whole command | run it. It is the same request with the one thing that was wrong changed (a limit raised, a path made relative) |
| `choose` | `options`: a list of `{argv}` | the request named something that is not there, and these are the nearest valid requests; pick one, or ask whoever asked, then run its `argv` |
| `none` | `reason` | there is no safe automatic fix: read `hint` and `detail`, change the request or the data |

The `argv` starts with the name the tool was invoked as. In the column "a repair?" below, `always` means the rule always carries a repair, `sometimes` that it does when the tool can name one, and `never` that it never does.

Three real refusals, one of each kind (the output is generated from the built binary):

<!-- gen:examples -->
```console
$ table --max-line-bytes 10 orders.csv          # exit status 8
{"code": "PRECONDITION_FAILED", "rule": "limit.line-too-long", "message": "a line is longer than --max-line-bytes; the file was not read past it", "hint": "raise --max-line-bytes, up to the ceiling introspect names", "repair": {"kind": "retry", "argv": ["table", "--max-line-bytes", "24", "orders.csv"]}, "detail": {"path": "orders.csv", "lines": 1, "first_line": 1, "longest": 24, "limit": 10}}
```

```console
$ table --where "status = 200" --select Customer,bytes orders.csv          # exit status 3
{"code": "NOT_FOUND", "rule": "select.unknown-column", "message": "--select names a column the header does not have", "hint": "pick from detail.available", "repair": {"kind": "choose", "options": [{"argv": ["table", "--where", "status = 200", "--select", "customer,bytes", "orders.csv"]}]}, "detail": {"path": "orders.csv", "flag": "--select", "name": "Customer", "columns": 4, "available": ["id", "customer", "status", "bytes"], "available_truncated": false}}
```

```console
$ table --where "bytes:int > 100" --select id orders.csv          # exit status 8
{"code": "PRECONDITION_FAILED", "rule": "value.not-integer", "message": "a cell is not an exact integer: an optional sign and digits, nothing else (an empty cell is not one)", "hint": "keep out the rows with such a cell with --where, or do not ask for an integer of this column (a cell with a point is a decimal: declare the column :dec(N))", "repair": {"kind": "none", "reason": "what the cell was meant to be is not known"}, "detail": {"path": "orders.csv", "context": "where", "column": "bytes", "row": 2, "line": 3, "value": "", "value_truncated": false}}
```
<!-- /gen:examples -->

## How to act on a refusal

1. Check the exit status and `ok`. Zero with `ok: true` is an answer, and zero matches is still success.
2. Switch on `rule`, never on `message`.
3. If `repair.kind` is `retry`, run `argv`. If it is `choose`, pick an option and run it. If it is `none`, do not retry unchanged: use `hint` and `detail` to change the request, or report the data problem.
4. A refusal about data (`value.not-integer`, `parse.csv-ragged-row`) names the row, line and column in `detail`. It says what the file contains, not what it should: only the owner of the data can fix it, or the request can say which rows it means (`--where "bytes != ''"`).
5. A limit rule (`limit.*`) says which limit and its ceiling in `table introspect`. Raise it only if the larger answer is wanted.
6. A refusal of a `--query` (`query.syntax`, `query.unsupported`, `query.group-mismatch`, `query.too-long`) points at the query: `detail.offset` is a byte of `detail.query`, and `detail.expected` or `detail.feature` says what the language takes there or leaves out. Its repair, when there is one, is the invocation as it was given with the query replaced by the flags the corrected query stands for (join an `argv` into a shell string only after quoting it). A refusal of the plan the query stands for (an unknown column, a cell that is not an integer) is the flags' own refusal, naming the flag the query was translated to (`--select`, `--where`), and offers no invocation back: there is no flag in the call to rewrite. A literal that does not fit the type its column is read as is a `query.syntax` at that literal, with `detail.column`, `detail.type` and `detail.value`.

## Every rule

<!-- gen:rules -->
| `rule` | exit | repair | what it refuses |
|---|---:|---|---|
| `args.unknown-flag` | 2 | sometimes | a flag that is not in the tool's table |
| `args.missing-value` | 2 | never | a flag that takes a value was given none |
| `args.bad-value` | 2 | never | a flag's value is not of the flag's kind |
| `args.duplicate-flag` | 2 | never | the same flag was given twice |
| `args.conflict` | 2 | never | two flags that exclude each other were both given |
| `args.required-flag` | 2 | never | a flag the tool needs was not given |
| `args.too-many-operands` | 2 | never | more operands than the tool takes |
| `path.empty` | 2 | never | an empty path |
| `path.dotdot` | 2 | sometimes | a path with a `..` component |
| `path.absolute` | 2 | always | an absolute path where one relative to --root is wanted |
| `path.outside-root` | 4 | never | an absolute path that is not under --root |
| `path.too-long` | 2 | never | a path longer than 4096 bytes |
| `path.symlink` | 4 | never | a path that runs through a symbolic link, which the tool does not follow |
| `io.not-found` | 3 | never | a named input does not exist |
| `io.not-a-directory` | 3 | never | a path runs through something that is not a directory |
| `io.is-a-directory` | 2 | never | a directory where a file is wanted |
| `io.permission-denied` | 4 | never | the operating system refused access |
| `io.read-failed` | 1 | never | the operating system failed a read |
| `io.write-failed` | 1 | never | the operating system failed a write, or a write was short |
| `limit.line-too-long` | 8 | sometimes | a line longer than --max-line-bytes |
| `limit.header-too-large` | 8 | never | the header record holds more than --max-line-bytes bytes. Hint: raise --max-line-bytes, up to the ceiling introspect names. Repair: none. |
| `limit.record-too-large` | 8 | never | a record read for --select, --where, --order-by or --group holds more than --max-line-bytes bytes. Hint: raise --max-line-bytes, up to the ceiling introspect names. Repair: none. |
| `limit.output-too-large` | 8 | never | the first row of a page is longer than --max-bytes. Hint: raise --max-bytes, up to the ceiling introspect names, or select fewer columns. Repair: none. |
| `limit.too-many-rows` | 8 | never | --format csv reached --max-rows with rows left unread. Hint: raise --max-rows, up to the ceiling introspect names, or page with --limit and --from. Repair: none. |
| `limit.too-many-sort-rows` | 8 | never | --order-by would hold more than --max-sort-rows rows to sort. Hint: ask for the first rows only with --top N or a smaller --limit (they are kept in bounded memory), keep fewer rows with --where, or raise --max-sort-rows, up to the ceiling introspect names. Repair: none. |
| `limit.too-many-groups` | 8 | never | more groups than --max-groups. Hint: raise --max-groups, up to the ceiling introspect names, or group by fewer or coarser columns. Repair: none. |
| `limit.too-many-distinct` | 8 | never | more distinct values than --max-distinct. Hint: raise --max-distinct, up to the ceiling introspect names. Repair: none. |
| `limit.state-too-large` | 8 | never | the keys and values kept for groups, or the rows kept to sort, hold more than --max-state-bytes bytes. Hint: raise --max-state-bytes, up to the ceiling introspect names, or group by shorter values. Repair: none. |
| `parse.csv-ragged-row` | 8 | never | a row has a different number of fields than the header. Hint: make every row as wide as the header, quoting fields that hold the delimiter. Repair: none. |
| `parse.csv-bad-quote` | 8 | never | a closing quote is followed by something other than the delimiter or the end of the record. Hint: double a quote that is text, or put the delimiter after the closing quote. Repair: none. |
| `parse.csv-unterminated-quote` | 8 | never | a quoted field is still open at the end of the input. Hint: close the quote, or double the quotes that are text. Repair: none. |
| `select.unknown-column` | 3 | sometimes | a name or position in --select that is not a column of the header. Hint: pick from detail.available. Repair: choose. |
| `select.ambiguous-column` | 8 | never | a name in --select that is the name of more than one column. Hint: name it by position, as #3 for the third column. Repair: none. |
| `column.unknown` | 3 | never | a name or position in --where, --group or --agg that is not a column of the header. Hint: pick from detail.available. Repair: none. |
| `column.ambiguous` | 8 | never | a name in --where, --group or --agg that is the name of more than one column. Hint: name it by position, as #3 for the third column. Repair: none. |
| `where.syntax` | 2 | sometimes | --where is not an expression of the grammar, at the offset the detail gives (an or of = and in on one column, a = 1 or a = 2, is repaired as the in it stands for, a in (1, 2)). Hint: the grammar is in the help of --where: COLUMN OP VALUE joined by and. Repair: choose. |
| `agg.bad-spec` | 2 | never | an item of --agg that is not count, sum:COL, min:COL, max:COL, mean:COL or distinct:COL, or whose :int, :dec(S) or @N is not one it takes. Hint: --agg count,sum:bytes,max:bytes,distinct:status, a mean of an integer column needs its scale, mean:bytes@2, a decimal column is declared sum:price:dec(2). Repair: none. |
| `sort.unknown-key` | 2 | never | --sort names no output column of the grouping. Hint: pick from detail.available, with a - before it to sort descending. Repair: none. |
| `value.not-integer` | 8 | never | a cell of an :int column or of sum, min or max is not an exact integer (an empty cell is not). Hint: keep out the rows with such a cell with --where, or do not ask for an integer of this column (a cell with a point is a decimal: declare the column :dec(N)). Repair: none. |
| `value.integer-overflow` | 8 | never | a cell of an :int column or of sum, min or max does not fit 64 bits. Hint: keep out the rows with such a cell with --where, or do not ask for an integer of this column (a cell with a point is a decimal: declare the column :dec(N)). Repair: none. |
| `value.not-decimal` | 8 | never | a cell of a :dec(S) column is not a decimal: an empty cell, an exponent, a space, a separator, a sign or a point alone. Hint: keep out the rows with such a cell with --where first (a condition that is false stops the ones after it), or do not ask for a decimal of this column. Repair: none. |
| `value.decimal-scale` | 8 | sometimes | a cell of a :dec(S) column has more fractional digits than S (it is never rounded). Hint: declare a larger scale, :dec(N) with N the most fractional digits the column holds. Repair: choose. |
| `value.decimal-too-wide` | 8 | never | a cell of a :dec(S) column has 18 or more significant digits once scaled to S. Hint: declare a smaller scale, or compare the column as text. Repair: none. |
| `column.type-conflict` | 2 | never | the plan reads one column as two numeric types: :int, :float, or :dec with two scales, in --where, --group, --agg or --order-by. Hint: give the column one type: the same :int, :float or :dec(N), in every place it is named, --where, --group, --agg and --order-by. Repair: none. |
| `value.not-float` | 8 | never | a cell of a :float column is not a number: an empty cell, a space, a separator, a hex form, a sign or a point alone. Hint: keep out the rows with such a cell with --where first (a condition that is false stops the ones after it), or do not ask for a float of this column. Repair: none. |
| `value.not-finite` | 8 | never | a cell of a :float column is inf or nan (the repair is a condition that is false first: x != 'NaN' and x:float > 5). Hint: keep them out with --where first: x != 'NaN' and x:float > 5 never reads the NaN cell as a number. Repair: none. |
| `value.float-range` | 8 | never | a cell of a :float column is outside the range of a double, or a non-zero number that would read as zero (detail.direction says which). Hint: keep out the rows with such a cell with --where first, or compare the column as text. Repair: none. |
| `limit.number-too-long` | 8 | never | a cell of a :float column is longer than 1,100 bytes, the longest cell that can be a double. Hint: a number of this length is not a double, compare the column as text. Repair: none. |
| `agg.float-overflow` | 8 | never | the exact sum of a :float column in a group is beyond the largest double, about 1.8e308 (detail.group names the group). Hint: sum fewer rows with --where, group by more columns, or sum in a smaller unit (the mean of the same group is a number: mean:COL:float). Repair: none. |
| `query.syntax` | 2 | sometimes | a --query that is not a statement of the grammar, or a literal that does not fit the type its column is read as, at the byte offset the detail gives. Hint: detail.expected says what was expected at detail.offset, and the grammar is in the help of --query. Repair: choose. |
| `query.unsupported` | 2 | sometimes | a --query that uses something the language leaves out: an or, a like, a null, an alias, an expression, a join, a typed group by column, an order the flags cannot say. Hint: detail.feature names it, and the hint says what to write instead. Repair: choose. |
| `query.group-mismatch` | 2 | sometimes | a --query whose select list is not its group by columns, in the same order, then at least one aggregate, or whose order by names something the select list does not hold. Hint: a grouping selects its group by columns first, in the same order, then aggregates, and has at least one: select a, count(*) from 'f.csv' group by a. Repair: choose. |
| `query.too-long` | 2 | never | a --query of more than 4096 bytes. Hint: ask the question in fewer bytes, the limit is detail.limit. Repair: none. |
| `input.fields-required` | 2 | sometimes | a plan on JSON lines (--input-format jsonl) with no --fields: JSON lines have no header, so the columns are declared. Hint: --fields id,status,user.name declares them, --discover reports what the first lines hold. Repair: retry. |
| `input.bad-fields` | 2 | never | --fields is not a list of paths: an empty item or segment, a backslash that escapes anything but a comma, a backslash, a # or a dot, a path of more than 8 segments, the same path twice, or more than 4096 paths (detail.item and detail.reason). Hint: paths are keys separated by dots, items are separated by commas, and a backslash escapes only a comma, a backslash, a # or a dot: --fields id,user.name,items.0.sku. Repair: none. |
| `parse.jsonl-syntax` | 8 | never | a line is not strict JSON (RFC 8259): detail.code and detail.reason say what the parser met, detail.offset where in the line and detail.near the bytes around it, and detail.looks_like is json-document when the first line begins like a document that is not JSON lines. Hint: one JSON object per line, and jq -c '.[]' makes lines of an array. Repair: none. |
| `parse.jsonl-not-object` | 8 | never | a line is JSON that is not an object (detail.kind). Hint: one JSON object per line, and jq -c '.[]' makes lines of an array. Repair: none. |
| `parse.jsonl-missing-field` | 8 | sometimes | a declared path has no value in a line: the key or index is not there, the value is null, or a step goes through something that is not an object or an array (detail.reason). Hint: --absent empty reads such a path as the empty cell. Repair: retry. |
| `parse.jsonl-not-scalar` | 8 | never | a declared path holds an object or an array (detail.kind). Hint: declare a deeper path: user.name, tags.0. Repair: none. |
| `parse.jsonl-duplicate-key` | 8 | never | a key occurs twice at a step of a declared path (detail.key), which parsers read differently. Hint: make the keys of the line unique. Repair: none. |
| `limit.json-too-many-nodes` | 8 | never | a line holds more JSON nodes than --max-json-nodes. Hint: raise --max-json-nodes, up to the ceiling introspect names. Repair: none. |
| `output.duplicate-column` | 2 | never | two columns of the answer of --format jsonl have the same name, and an object whose key occurs twice is read differently by different parsers (detail.column, and detail.first and detail.second, the 1-based places in the answer). Hint: name each column once: select it once, and each aggregate once. Repair: none. |
| `output.not-representable` | 2 | never | the name of a column of the answer of --format jsonl is not valid UTF-8, which cannot be the key of a JSON object (detail.column holds it as text_or_bytes does). Hint: write the answer as --format csv, which carries any bytes. Repair: none. |
<!-- /gen:rules -->

The list is what `table introspect` prints under `rules`. `args.*`, `path.*` and `io.*` are the contract's, shared by the other cancho-tools; the rest are `table`'s own.
