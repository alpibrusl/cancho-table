# table, for an agent

`table` answers a question about a CSV, TSV or JSON lines file with one JSON document, or refuses with a rule and, where one exists, a repair. Ask the tool, not this file:

```console
$ table skill        # a compact guide (about 2 KB); `table skill --full` is the exhaustive one
$ table introspect   # one JSON document: the flags, the limits, the refusal rules and the authority
```

* **Answers and refusals are one JSON line**, schema `table.v2`: `{ok, command, schema, data, error, errors, meta}`. Match on `error.rule`, never on `message`. The `hint` says how to fix the request; a `repair` of kind `retry` is an argv to run, and one of kind `none` says why there is none.
* **Types are explicit.** A column is text unless you write `:int`, `:dec(S)` (an exact decimal with S fractional digits; a cell with more digits is refused, never rounded), `:float`, `:date`, `:timestamp` or `:datetime`. When a refusal names a column or a scale, take the repair it gives.
* **It reads under `--root` and writes standard output, or one file with `--out`.** `--out FILE --root DIR` writes the answer (the bytes standard output would have had, for any `--format`) to `FILE` beneath `DIR`, whole and by a rename, so a reader never sees half of it and a refused run leaves nothing; standard output then carries one JSON line, `data.out` with the path, the bytes and the rows. A file that is there is `out.exists` (take its repair: `--overwrite`, or the next free name). The answer is held in memory until written, counted in `--max-state-bytes`; the MCP server does not offer `--out`. No network, no clock. A task that needs one of those is not a task for this tool.
* **The same bytes on any number of cores:** `--threads N` gives the one-core answer, refusals included.
* **Over MCP:** the server in `docs/mcp.md` exposes one tool, `table`, derived from `table introspect`.
* The stories in `docs/stories.html` are worked cases with data; `docs/refusals.md` lists every rule.
