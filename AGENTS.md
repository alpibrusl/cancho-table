# table, for an agent

`table` answers a question about a CSV, TSV or JSON lines file with one JSON document, or refuses with a rule and, where one exists, a repair. Ask the tool, not this file:

```console
$ table skill        # a compact guide (about 2 KB); `table skill --full` is the exhaustive one
$ table introspect   # one JSON document: the flags, the limits, the refusal rules and the authority
```

* **Answers and refusals are one JSON line**, schema `table.v2`: `{ok, command, schema, data, error, errors, meta}`. Match on `error.rule`, never on `message`. The `hint` says how to fix the request; a `repair` of kind `retry` is an argv to run, and one of kind `none` says why there is none.
* **Types are explicit.** A column is text unless you write `:int`, `:dec(S)` (an exact decimal with S fractional digits; a cell with more digits is refused, never rounded), `:float`, `:date`, `:timestamp` or `:datetime`. When a refusal names a column or a scale, take the repair it gives.
* **It reads under `--root` and writes standard output, or one file with `--out`.** `--out FILE --root DIR` writes the answer (the bytes standard output would have had, for any `--format`) to `FILE` beneath `DIR`, whole and by a rename, so a reader never sees half of it and a refused run leaves nothing; standard output then carries one JSON line, `data.out` with the path, the bytes and the rows. A file that is there is `out.exists` (take its repair: `--overwrite`, or the next free name). The answer is held in memory until written, counted in `--max-state-bytes`; the MCP server does not offer `--out`. No network, no clock. A task that needs one of those is not a task for this tool.
* **A file with a title block above its header:** `--skip-lines N` drops the first N lines, `--skip-prefix '#'` the comment lines before the header (after the header a line that starts with it is a row); both also apply to the `--join` file. The answer says how many lines were dropped (`data.skipped.lines`, `join_lines` for the `--join` file), and line numbers in errors and in `--report` are the file's, preamble included.
* **A European or marked export:** `--decimal , --thousands .` reads a cell of a column the plan types `:dec(S)` or `:float` (in `--where`, `--agg`, `--group`, `--order-by`) written `1.234,5` as `1234.5`, and shows it so; a cell that fits neither that syntax nor the plain one (a plain `12.34` beside `--decimal ,`, or `1.23` with `--thousands .`) is refused as `value.not-decimal` or `value.not-float`, and `detail.syntax` says which syntax was in force. `--na '?,NA'` reads those markers as the empty cell everywhere, the output included. Only for a CSV FILE (`args.conflict` with `--join` and JSON lines), on one core; `--thousands` needs `--decimal`. Docs in `docs/reader-options.md`.
* **The same bytes on any number of cores:** `--threads N` gives the one-core answer, refusals included.
* **Over MCP:** the server in `docs/mcp.md` exposes one tool, `table`, derived from `table introspect`.
* The stories in `docs/stories.html` are worked cases with data; `docs/refusals.md` lists every rule.
