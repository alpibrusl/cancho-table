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
| `args.missing-operand` | 2 | never | an operand the tool needs was not given |
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
| `limit.line-too-long` | 8 | sometimes | a line longer than --max-line-bytes |
| `limit.header-too-large` | 8 | never | the header record holds more than --max-line-bytes bytes |
| `limit.record-too-large` | 8 | never | a record read for --select, --where, --order-by or --group holds more than --max-line-bytes bytes |
| `limit.output-too-large` | 8 | never | the first row of a page is longer than --max-bytes |
| `limit.too-many-rows` | 8 | never | --format csv reached --max-rows with rows left unread |
| `limit.too-many-sort-rows` | 8 | never | --order-by would hold more than --max-sort-rows rows to sort |
| `limit.too-many-groups` | 8 | never | more groups than --max-groups |
| `limit.too-many-distinct` | 8 | never | more distinct values than --max-distinct |
| `limit.state-too-large` | 8 | never | the keys and values kept for groups, or the rows kept to sort, hold more than --max-state-bytes bytes |
| `parse.csv-ragged-row` | 8 | never | a row has a different number of fields than the header |
| `parse.csv-bad-quote` | 8 | never | a closing quote is followed by something other than the delimiter or the end of the record |
| `parse.csv-unterminated-quote` | 8 | never | a quoted field is still open at the end of the input |
| `select.unknown-column` | 3 | sometimes | a name or position in --select that is not a column of the header |
| `select.ambiguous-column` | 8 | never | a name in --select that is the name of more than one column |
| `column.unknown` | 3 | never | a name or position in --where, --group or --agg that is not a column of the header |
| `column.ambiguous` | 8 | never | a name in --where, --group or --agg that is the name of more than one column |
| `where.syntax` | 2 | never | --where is not an expression of the grammar, at the offset the detail gives |
| `agg.bad-spec` | 2 | never | an item of --agg that is not count, sum:COL, min:COL, max:COL, mean:COL or distinct:COL, or whose :int, :dec(S) or @N is not one it takes |
| `sort.unknown-key` | 2 | never | --sort names no output column of the grouping |
| `value.not-integer` | 8 | never | a cell of an :int column or of sum, min or max is not an exact integer (an empty cell is not) |
| `value.integer-overflow` | 8 | never | a cell of an :int column or of sum, min or max does not fit 64 bits |
| `value.not-decimal` | 8 | never | a cell of a :dec(S) column is not a decimal: an empty cell, an exponent, a space, a separator, a sign or a point alone |
| `value.decimal-scale` | 8 | sometimes | a cell of a :dec(S) column has more fractional digits than S (it is never rounded) |
| `value.decimal-too-wide` | 8 | never | a cell of a :dec(S) column has 18 or more significant digits once scaled to S |
| `column.type-conflict` | 2 | never | the plan reads one column as two numeric types: :int, or :dec with two scales |
<!-- /gen:rules -->

The list is what `table introspect` prints under `rules`. `args.*`, `path.*` and `io.*` are the contract's, shared by the other cancho-tools; the rest are `table`'s own.
