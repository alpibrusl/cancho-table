# An MCP server for `table`: the design

> **Status: built** (`server/mcp.cho`, `scripts/mcp.py`, `generated/mcp/tools.cho`,
> `tests/conformance/test_mcp.py`, `scripts/mcp_mutants.py`), on the compiler `cancho.toml` pins
> (`a4572ea`, edition 7 with `std.process`). The server is cancho-tools' MCP server (its
> `docs/mcp.md`) with one tool in place of eight, and what that tool needed that the eight did not.
> Every claim here was measured on macOS (arm64) and on Linux (x86-64); §9 is what was found.

An agent runtime adopts a tool most easily over MCP. `table` already describes itself
(`table introspect`, `table skill`), so the server derives its one tool definition from that and
has no description of its own to drift. The server is a cancho program, so what it may do is what
the compiler derives, as for the tool: **start the one binary, under a fixed `--root`, and nothing
else.**

## 1. What a call becomes

```
tools/call {"name": "table", "arguments": {"file": "data.csv", "select": "id,bytes", "where": "bytes:int>99000", "limit": 5}}
    -> <bin>/table --root=<ROOT> --select=id,bytes --where=bytes:int>99000 --limit=5 -- data.csv
```

* **The binary is one, by name.** The name is matched against the generated table and anything else is a
  JSON-RPC error (`-32602`, `mcp.unknown-tool`). No path from the model reaches `exec_spawn`.
* **No shell.** The call is an argv, built from the schema's tables, and passed to `exec_spawn`:
  a `;`, `$(...)`, a backquote or a newline is one byte of one argument, and the tests send them
  (`test_no_shell_a_metacharacter_is_text`, `test_the_argv_is_fixed_in_form`, which records the argv a
  stand-in binary receives).
* **`--root` is the server's, first, and once.** It comes from the server's command line
  (`mcp --root DIR`) and is not in the input schema; a `root` property is refused
  (`mcp.unknown-argument`). The tool refuses a second `--root` (`args.duplicate-flag`), so a flag the
  server failed to filter could not replace it either.
* **A value is bound or fenced.** A flag's value is one argument, `--name=value`, so it cannot become a
  flag of its own; the operand comes after `--`, after which the tool takes everything as a file name:
  `file: "--root=/"` is a file called `--root=/` (`io.not-found`), and `where: "--root=/"` is a condition.
* **A NUL byte is refused** (`mcp.nul-in-argument`): it would arrive as two arguments.
* **Every path is confined by the tool, under the server's root.** The tool resolves FILE beneath
  `--root` one component at a time, following no link, and refuses `..` (`path.dotdot`), an absolute
  path outside the root (`path.outside-root`), a link anywhere on the path (`path.symlink`), an empty
  or over-long path. The server adds no check of its own on a path, because two checkers can disagree
  and the tool's is the one with a test suite (M8): what the server guarantees is that the root it
  passes is its own and that the path reaches the tool as one argument. `test_dotdot_absolute_and_links_are_refused_with_their_rules`
  sends 14 forms and checks the answer is the CLI's and that the secret file's content is nowhere in it.
* **Standard input is the call's `stdin`, or empty** (§12). With the `stdin` property the server writes its bytes to the pipe that is the
  child's standard input; without it the child gets an empty input, as it always did. `table` reads standard input when its operand is `-` or
  absent (docs/readers.md section 3), so its authority has `io_read`; the generator accepts that for a tool whose one operand is optional, and
  the server requires the call to give **exactly one** of `file` and `stdin` (`mcp.input-conflict`, `mcp.input-missing`). A call that names the
  file `-` still reaches an empty standard input and gets the empty table's answer (`headers: []`): `file` is always a file. A generator still
  refuses a tool that reads standard input and has no optional operand.

## 2. What comes back

Per the 2025-06-18 specification (`server/tools`), a tool's failure is a result with `isError`, and a
protocol failure is a JSON-RPC error.

* `content[0]`: one `text` item, the tool's **standard output exactly**, as a JSON string
  (`_meta.stream: "stdout"`).
* `content[1]`, only when the tool wrote any: its **standard error exactly** (`_meta.stream: "stderr"`). In
  `--format csv` and `--format text` the verdict of a refusal is on standard error and standard output
  is empty or a prefix (docs/refusals.md), so the server returns both: a client that read only the first
  item would see an empty answer and no reason. The two are separate channels, so their interleaving is
  not kept (the tool writes the document to one and the reason to the other, never both for one answer).
* `structuredContent`: the same JSON as an object, **only when the answer is the JSON document**: the
  default `--format json`, `ok` or not (a refusal's `error` with its `rule`, `hint`, `repair` and `detail`
  is there to be matched on). With `format: "csv"` or `"text"` the answer is text only, and there is no
  `outputSchema` (the specification would then require structured content for every successful call).
  The document's schema is `table.v2` (`schemas/table.v2.json`, `table introspect`); the tests
  validate every JSON answer of the corpus against it.
* `isError`: true when the exit code is not 0. `table` has no non-zero success (no `--dry-run`, so no
  exit 9 as in cancho-tools).
* `_meta.exit_code`: the tool's exit code.
* **Not byte for byte, in one case.** The text is a JSON string, so bytes that are not UTF-8 cannot be
  carried as they are: with `format: "csv"` a cell of invalid UTF-8 arrives as U+FFFD, and the
  differential test pins this down. The default JSON format carries such bytes as `{"b64": ...}`
  (the tool's own `text_or_bytes`) and loses nothing: use it for data that may not be text.

When the **server** ends the child itself (`capture_both` answered `TimedOut`, `TooMuch`, a signal or a
failure to start), the result is `isError` with one record in the shape of the tool's own errors
(`code`, `rule`, `message`, `hint`, `repair: {"kind": "none", ...}`, `detail`), rule `mcp.timeout`,
`mcp.output-too-large`, `mcp.signaled` or `mcp.spawn`, and `command: "mcp"`. Its `hint` says what to ask for
instead (§4).

## 3. The tool definition is generated, not written

`table introspect` gives the flags (name, kind, role, default, help), the operand (`FILE`, role, `min`,
`max`, help), the limits with their ceilings, the guarantees and the schema. `scripts/mcp.py` turns that
into `generated/mcp/tools.cho`: the `tools/list` result as one literal and the table each call is
checked against; `--check` (CI) fails when it differs from a fresh derivation, as `manifest.py --check` does
for the authority.

| `introspect` | input schema |
|---|---|
| `kind: bool` | `{"type": "boolean"}`, passed as `--name` when true (`table` has one, `--rows`, and the generator and the server handle it; a test also builds a scratch copy of the tool with another) |
| `kind: nat` | `{"type": "integer", "minimum": 0}`, and `maximum` from the flag's row in `limits` (its ceiling) |
| `kind: text`, `any`, `path` | `{"type": "string"}` (`text` with `minLength: 1`); a `path` flag with role `root` is the server's and is not offered |
| `kind: choice:a/b` | `{"enum": ["a", "b"]}` |
| the operand `FILE` (min 1, max 1) | `file`, a string, in `required` |
| `help`, `default` | `description`, `default` |
| `guarantees.idempotent`, `authority.effects` | `annotations`: `title` (the tool's name), `readOnlyHint` (no write or network effect), `destructiveHint` (its negation), `idempotentHint`, `openWorldHint: false` |

The strings a model has to write, `--where`, `--select`, `--agg`, `--order-by`, `--sort`, `--group`,
are the flags' `help` from `introspect`, in the property's `description`, so the schema documents them
from the one place they are written. The tool's description is its `summary`, then a fixed paragraph
the generator adds (§4: the root, the bound, the refusals).

`additionalProperties` is false, and the server refuses a property it does not know (`-32602`,
`mcp.unknown-argument`, with the list of the real ones), so the schema is the whole of what a model can
pass. A property of the wrong type is refused with **all** the faults at once, what the schema wants, what
arrived, and for the mistake small models make most (a number sent as a string) what to send:

```
`limit` must be a non-negative integer, not a string; send a JSON number such as 1, not a string such as "1"
{"rule": "mcp.wrong-type", "problems": [{"property": "limit", "expected": "a non-negative integer", "got": "string"}]}
```

Strictness is unchanged from cancho-tools: the server does not coerce, because then the schema would
be a suggestion. It validates types (a boolean, a non-negative integer, a string, an array of strings),
not an `enum` or a `maximum`: those are the tool's, which refuses `--limit=99999999` or `--delimiter=ab`
with its own rule (`args.bad-value`) and its own repair, and the differential corpus covers both.

### What the schema cannot say, and what `introspect` would need to

Whether flags go together is not in `introspect`, so it is not in the schema. The tool has three forms
(its `usage` line): the shape, rows (`--select`/`--where`/`--order-by`, paged with `--limit`/`--from`)
and groups (`--group`, `--agg`, `--sort`, `--top`), and refuses a mix with `args.conflict`
(`--select` with `--group`, `--order-by` with `--group`, `--format text` with rows) or
`args.required-flag` (`--limit`/`--from` without `--select`, `--where` or `--group`; `--sort`/`--top` without
`--group` or `--order-by`; `--format csv` without any of them). A model learns these from the
refusal and its hint. They are in the code of `table.cho`, not in a table, so this generator cannot read them
(§9, gaps).

## 4. The bounds, and the refusal when one is met

An MCP response goes into a model's context. The server bounds it and says what to ask for instead:

| bound | default | flag | refusal |
|---|---|---|---|
| standard output of one call | 2 MiB | `--max-output` | `mcp.output-too-large` |
| standard error of one call | 64 KiB | fixed | `mcp.output-too-large` |
| time of one call | 30 s | `--timeout-ms` | `mcp.timeout` |
| a request line | 4 MiB | fixed | `mcp.line-too-long` (JSON-RPC `-32600`, `id: null`) |

* **The 2 MiB is not arbitrary.** The tool's own page bound is `--max-bytes`, 1 MiB of rows by default, so a
  default answer, its envelope included, is always within the server's. A call that raises `max-bytes`, or asks
  for `format: "csv"` of a whole file, can pass it: the tool is then killed and the result is
  `mcp.output-too-large`, `detail.limit` the bound, and the hint:
  *ask for less: `limit` caps the rows, `top` the groups (or the first rows of an order-by), `select` the columns,
  `where` the rows; do not raise `max-bytes` past the server's bound*. A client that caps a tool result smaller
  still (Claude Code warns at 10,000 tokens and refuses at 25,000 unless told otherwise) is served by a lower
  `--max-output`: the bound is the operator's, and the refusal is the same.
* **The deadline** ends the call with `mcp.timeout` (the child is killed and reaped; the tests check the pid
  is gone), `detail.limit` the milliseconds, and the hint *ask for less work: add `where`, `limit` or `top`,
  name fewer columns with `select`, or read a large file with more `threads`; the deadline is the server's
  --timeout-ms*. The server keeps serving.
* Both hints are generated by `scripts/mcp.py`, which exits if a property they name is no longer in
  `introspect`, rather than say something false.
* **The bound is exact**: a call whose output is `N` bytes passes with `--max-output N` and is refused with
  `N - 1` (`test_the_output_bound_is_exact_and_refuses_with_what_to_ask_for`).

## 5. The protocol, as much as it needs

* **stdio**, newline-delimited JSON-RPC 2.0. A line is read with `getchar` up to `\n`; a line over 4 MiB is
  read to its end, dropped and answered `mcp.line-too-long`, and nothing but responses goes to standard output.
* **`initialize`** answers `protocolVersion` `2025-06-18` with `capabilities: {"tools": {"listChanged": false}}`
  and `serverInfo` `cancho-table`. **`notifications/*`** and any request without an `id` are not answered.
  **`ping`**: `{}`. **`tools/list`**: the generated result. **Anything else**: `-32601`, `mcp.unknown-method`.
  A line that is not JSON: `-32700`, `mcp.parse-error`, `id: null`. Not a JSON-RPC 2.0 request: `-32600`,
  `mcp.invalid-request`.
* Every protocol refusal carries `error.data.rule`, a stable tag to match on, as the tool's errors do.
* One request at a time: a call blocks the next line until `capture_both` returns, which a stdio client
  already expects.

## 6. Its authority, its ceiling, and how it describes itself

The server holds `Exec` narrowed to the directory of the binary, and no `Fs`, `Net` or `Ffi`. It reads nothing
itself. `cancho authority` says (measured, `manifests/mcp.authority.json`): bounded, no foreign symbols, and
`args`, `child_signal`, `clock`, `err_write`, `exec("<bin>")`, `heap`, `io_read`, `io_write`, `pipe_read`,
`pipe_write`, `poll`. (`pipe_*` and `child_signal` are what it does with the child and channels it owns, `pipe_write` among them the child's standard input, which carries the call's `stdin` text (§12) and nothing else; `clock`
and `poll` are the deadline.)

* **The ceiling** is `server/ceiling.toml`, as `tools.toml` is the ceiling of the tool: a person writes and
  reviews it; the authority is whatever the compiler derives. `scripts/mcp.py --check` (CI) fails when a derived
  label is not allowed there, when a label in `forbid` appears even if `allow` names it, when `bounded` is false,
  when a foreign symbol appears, or when the `exec` label names a directory other than the one the binary is
  baked with. Widening the server is therefore an edit of that file: a reviewable diff. (cancho-tools gates its
  server only in a test; this is the same row as a file, in the form `tools.toml` has.)
* **The bound is baked in at build time.** `narrow` takes a literal, so the directory is a source substitution:
  `scripts/mcp.py build --bin DIR --out FILE` writes `DIR` into the server and into the generated module and
  builds that; the committed ones say `/opt/cancho-table/bin`. The authority of the built binary says `DIR`.
* **`mcp introspect`** prints the server's description of itself, as `table introspect` does for the tool: one
  JSON line with `tool`, `version`, `summary`, `usage`, `protocol`, `serves` (the tool, its version, the
  compiler it was built with and the directory), `flags` (`--root`, `--timeout-ms`, `--max-output`), `limits`,
  every `rules` entry (and the test checks the list is **exactly** the rules the server can say: each one is
  provoked and found, and nothing else is), `confinement`, and the **authority**, the compiler's report as embedded
  at build time. Embedding must not change the report: `generated()` derives it with the module that holds it and
  fails unless the second report equals the first (the fixed point of `manifest.py`).
* The bounds and the sentences are written once in `scripts/mcp.py` and read by the server from the generated
  module, so `introspect`, the schema and the code cannot disagree.

## 7. Shared with cancho-tools, and copied

**Copied**, with its source revision recorded: `server/mcp.cho` is cancho-tools' `server/mcp.cho`
at `74411ee43c74a2741ed1bb37404844fcbb031753` (the head of `main` when this was written; the contract
package this repository consumes is pinned at `af73b1b`, and the server is not in it; that revision's `server/mcp.cho` differs from the copy in two comments and the text of one refusal, and its `scripts/mcp.py` (#42) adds a `title` to the annotations and derives the four hints from `reversibility` and the effects, where this copy derives them from the effects and writes `openWorldHint: false`; the four values are the same for `table`, and `title`, the tool's name, is written by this generator as that one does: `docs/architecture.md`, "The pin"), and `scripts/mcp.py`
is that revision's generator, both adapted. **Why not shared:** the contract package is a set of library
modules (`toolbox.cli`, `.describe`, `.fail`, ...), and a library cannot carry the server: its `main` must hold
`Exec` narrowed to a *literal* directory (`narrow` takes a literal, cancho `processes.md` §7.1), which is
the point of the design, so the server is a program and is baked per deployment. What a package could carry
is the JSON-RPC and argv-building half (the line reader, the problem messages, the argv builder): about 700
of the 1,000 lines, generic in the tables. That needs `mcp.cho` split into a module plus a `main`, in
cancho-tools (its server and this one then import the same module and differ in a `main` of 60 lines and a
generated table); it is the right next step and it is a change to cancho-tools' package, which this
repository only consumes. Until then the drift risk is real and bounded: the generic half is covered by this
repository's own protocol, hostile-input and mutation tests, and the header of `server/mcp.cho` names the
revision it was copied from.

**What changed from the copy** (all generic, so they could go back upstream):

* the binary directory and the server name (`cancho-table`);
* `capture_both` in place of `capture`: standard error is returned beside standard output (cancho-tools'
  server sends the child's standard error to `/dev/null`, which loses the reason when `--format csv` fails);
* `isError` is `exit != 0` (no exit 9 here);
* `structuredContent` only when the call's format is JSON (a `structured` property in the generated table)
  and no `outputSchema`;
* every refusal of the server carries `data.rule`; the results of calls the server ended are in the tool's own
  error shape, with `hint` and `repair`, and `detail.limit`;
* `mcp introspect`, the ceiling file, the fixed-point authority embedded in the generated module;
* the output bound defaults to 2 MiB and names `limit`/`top`/`select` in the refusal (cancho-tools: 16 MiB).

## 8. How it is checked

`tests/conformance/test_mcp.py` (43 tests, 10 s with the server built):

* **Differential.** A corpus of 71 plans over the benchmark generator's file, quoted and multi-line fields,
  a TSV, a `;` file, numbers (`:int`, `:dec(2)`, `:float`), an empty file, a header-only file, a file with a space
  and one with `"`, `;` and `$(id)` in its name, and 24 refusals (ragged, bad quote, unterminated quote, duplicate
  header, unknown column, bad `--where`, bad `--agg`, too many groups, flag conflicts, a directory, a missing
  file): for each, `content[0].text` is the CLI's standard output byte for byte, `content[1]` its standard error,
  `_meta.exit_code` its exit code, `isError` is `exit != 0`, `structuredContent` is the parsed document (and
  validates against `table.v2`), and its `error` equals the tool's, `rule`, `hint` and `repair` included.
* **Schema = introspect.** An independent derivation (not through `mcp.py`) of every property by kind, with its
  help, default and ceiling; the operand; `required`; the annotations. And a scratch copy of the tool's sources
  whose flag table is edited (a `nat` made a boolean, another a `choice`, a help changed, a boolean added) is
  built and the server built from *its* `introspect` lists the edited schema and takes what it says.
* **Confinement.** `..`, `sub/../x`, absolute paths outside the root, links (absolute, relative, through a
  directory, inside the root), an over-long path; `root` is not a property; `--root=/` as a file, a value or a
  delimiter; the argv the child receives (a stand-in that records it), shell metacharacters, NUL.
* **Limits.** The exact output bound, the 2 MiB default and a default page within it, a whole file as csv refused,
  a 1 ms deadline on a 400,000-row file, the child killed and gone, floods on standard output and error, a tool
  ended by a signal, a tool that cannot start; the server serves after each.
* **Protocol.** `initialize`, notifications unanswered, `ping`, unknown methods, 30 malformed lines by name, an
  oversize frame (4 MiB + 1, 8 MiB + 7) and the exact bound, end of input, an unterminated last line, an answer
  while the input is still open (the flush), the server's own command line.
* **Hostile.** 300 seeded damaged requests (bytes, truncation, junk in a valid frame, deep nesting): each is
  answered with a rule or ignored, no trap, and the server answers the `ping` after them.
* **The server itself.** `mcp introspect`, its rules exactly the ones it says, its authority equal to the committed
  one and to a fresh derivation, no `fs_*`/`net_*`/`ffi`, the gate failing for each label it must refuse.

`scripts/mcp_mutants.py` has 65 mutants, one defect each, in the server (wire, call, answer, limits, command line) and
in the generator (schema, gate); `--check` (CI) verifies each still applies exactly once.

**Results.** 65 of 65 killed on macOS. The first run killed 61: four survived and each was a test that did not look,
and each now has its test: *a line one byte past the bound is read* and *a line past the bound is taken for a request*
(the tests sent lines far past the bound and one exactly at it, never one byte past it, nor an oversize last line ending
the input without a newline); *csv is structured content* (no csv answer was ever a JSON object: a file whose header is
`{}` makes one, `test_text_that_is_a_json_object_is_still_text`); *the gate lets exec be anywhere* (the label was also
outside the allow list, so the second check was redundant until a test gave a ceiling that names the wrong directory).
All four are killed after. Four others (no flush, the root, the output bound one byte more, a deadline a thousand times longer) again
on Linux x86-64. The whole suite is green with the new file: 245 tests (one skipped on macOS, as before), 175 s on a
laptop; `test_mcp.py` alone is 43 tests, 10 s on macOS and 34 s on `gram` (Linux 6.x x86-64, niced on six cores).
One mutant needed the test file to know it was being mutated: `test_the_mutants_still_apply` checks the patterns match
the sources, which a mutant by definition breaks, so it is skipped when `MCP_MUTANT_RUN` is set and `--check` (CI) does
the job.

`scripts/mcp_client.py` is a minimal client, used for the transcripts below.

## 9. Tried for real

`scripts/mcp_client.py` (initialize, `notifications/initialized`, `tools/list`, then calls) against the server built with
`build/` baked in and the benchmark generator's file (20,000 rows; `big.csv` is 1,000,000):

```
initialize -> {"name": "cancho-table", "version": "0.1.0"} 2025-06-18
tools/list -> ['table']
call {"file": "data.csv"} -> exit 0 isError False structured 187 bytes
    {"ok":true,"command":"table","schema":"table.v2","data":{"headers":["id","status","bytes","path","note"],"column_count":5,"row_count":20000,"truncated":false},"meta":{"version":"0.3.0"}}
call {"file": "data.csv", "select": "id,status", "limit": 3} -> exit 0 isError False structured 206 bytes
    {...,"data":{"columns":["id","status"],"rows":[["0","200"],["1","200"],["2","301"]],"row_count":3,"truncated":true,"next":{"from":3}},...}
call {"file": "data.csv", "where": "status=404 and bytes:int>99000", "select": "id,bytes", "limit": 2} -> exit 0
    {...,"rows":[["6","99913"],["254","99276"]],"row_count":2,"truncated":true,"next":{"from":767}},...}
call {"file": "data.csv", "group": "status", "agg": "count,sum:bytes", "sort": "-count"} -> exit 0
    {...,"columns":["status","count","sum:bytes"],"rows":[["200","9992","493850992"],["301","3375","167290607"],...],"group_count":4},...}
call {"file": "data.csv", "order-by": "-bytes:int", "select": "id,bytes", "limit": 2} -> exit 0
    {...,"rows":[["12727","99989"],["13317","99989"]],"truncated":true,"next":{"from":2}},...}
call {"file": "data.csv", "group": "status", "format": "csv"} -> exit 0 isError False text-only 49 bytes
    status,count / 200,9992 / 301,3375 / 404,3338 / 500,3295
call {"file": "../x.csv"} -> exit 2 isError True structured      (error.rule "path.dotdot", hint, repair, detail intact)
call {"file": "data.csv", "where": "nope=1", "format": "csv"} -> exit 3 isError True text-only 0 bytes
    [stderr] table: column.unknown: the plan names a column the header does not have
call {"file": "data.csv", "limit": "3"} -> JSON-RPC error -32602, data.rule "mcp.wrong-type", "send a JSON number such as 1"
call big.csv, group by path, sum:bytes, --timeout-ms 2  -> isError, rule mcp.timeout, hint "add `where`, `limit` or `top` ..."
call big.csv, select id,note, format csv                  -> isError, rule mcp.output-too-large, hint "`limit` caps the rows ..."
```

**Claude Code** (2.1.201): `claude mcp add --scope user table -- build/mcp --root DIR` in a scratch `HOME` (so not the
global configuration) and `claude mcp list` reports `table: ... - Connected`: its client completed `initialize` and
`tools/list` against the server. **A tool call from Claude Code was not made:** `claude -p` in the session this was written
in answered "Not logged in", so there was no model to choose the call. The same client-side steps are what
`scripts/mcp_client.py` does, and what cancho-tools' docs/mcp.md §9 measured with Claude Code 2.1.289 for its server (which this one
is a copy of, with a different tool table).

## 10. Gaps found in `table skill` and `table introspect`

> **Status on `main` (after #24, the skill-text change).** This section is the record of what was found when the server was
> written, against 22 flags and 48 rules. `table introspect` now has 23 flags, 11 limits and 50 rules. The first two gaps below are
> closed (`usage` names every flag, and `tests/conformance/test_skill.py` checks it; `--agg`'s help names `mean` and the `:int`,
> `:dec(S)` and `@N` modifiers); the third is half closed (the summary now says in prose which flags exclude or require each other, and
> there is still no machine-readable form); `io.write-failed` is in the rules; `skill` has examples, and the tool's own rules carry their hint and repair kind
> (the contract's `args.*`, `path.*` and `io.*` do not, and there is no operand section). Still open: `args.unexpected-value`, a row for `top` and `from` in `limits`,
> `output: "document"` for the csv and text modes, and a version for the flag table.

Both exist and are complete as far as they go: `skill` is `introspect`'s tables printed as a `SKILL.md` (the same
`describe.Tool`), the flags are the parser's own table (`flag_table()` in `tools/table/table.cho`, the one the
parser reads), and every one of the 22 flags, the operand, the 11 limits, the 48 rules with exit and repairability, the exit codes, the
guarantees and the authority is there. What was checked by script against the sources (not a test in the suite: a
one-off, and what it found is below):

* **`usage` is hand-written and stale.** It omits five flags the parser takes: `--order-by`, `--max-sort-rows`,
  `--threads`, `--chunk-bytes`, `--parallel-min-bytes`; and gives `--format` as `json|text` for one form and `json|csv` for
  the others while the flag is `choice:json/text/csv`. `skill` prints it, so a model that reads the usage line does not
  learn that `table` can sort rows or read in parallel (it learns it two paragraphs later from the flags list).
* **`--agg`'s help omits `mean`, and the modifiers.** The rule `agg.bad-spec` names `mean:COL` and says `:int`, `:dec(S)` and
  `@N` are accepted on an item; the flag's help lists `count, sum:COL, min:COL, max:COL, distinct:COL` only. (`mean:bytes`
  is refused here, `agg.bad-spec`: the help does not say what a valid `mean` is.) The strings a model must write
  are the whole of what this server's schema can document, so this is the most expensive gap for it.
* **Which flags go together is not in the tables.** The three forms are in `usage` as prose; `args.conflict` and
  `args.required-flag` are raised from code with a message naming the flags (`--select` with `--group`, `--order-by` with
  `--group`, `--limit`/`--from` without `--select`, `--where` or `--group`, `--sort`/`--top` without `--group` or
  `--order-by`, `--format text` with rows, `--format csv` with none of them). Neither `introspect` nor `skill` lists them, so
  no generator can emit a `oneOf`, and a model finds out by being refused. A `forms` (or `requires`/`excludes`) member in
  `introspect`, read by the parser, would close it.
* **`limits` has no row for `top` and `from`** (they have no ceiling) and its `default` for `--limit` (1000) is the
  default for JSON only: the flag's own `default` is null and the help says "no limit as csv". The schema's `default` for `limit`
  is therefore absent, correctly, and the number is only in the help.
* **Two rules the code can say are not in the catalogue:** `io.write-failed` (standard output took fewer bytes than
  written; in the contract's `out` module, on standard error) and `args.unexpected-value` (a flag that takes no value was
  given one; reachable since `--rows`, a boolean flag: `table --rows=1 FILE` is `args.unexpected-value`, exit 2, checked on the build of `5a439ee`). `introspect`'s `rules` is the tool's list, not the
  package's, and `test_rules.py` asserts it equals what the fixtures reach, so the first is a rule with no fixture.
* **`skill` has no operand section, no example, no hint:** FILE appears only in the usage line and in `--root`'s help;
  there is not one example invocation; each rule has its summary but not its hint or repair kind, which is what an
  agent branches on. `introspect` has the operand (`min`, `max`, `role`) and, with the tools' own
  `operands.min`/`max`, is complete for a schema; `skill` is the thing with the omissions.
* **`--format csv` and `text` write refusals to standard error** and rows written before a refusal stand
  (docs/refusals.md): `introspect` says `output: "document"` and one schema, and does not say that the output is
  not a document in those modes. This server needed to know (§2) and reads it from the choice list, not from `introspect`.
* **Versions:** `introspect.version` is the tool's (`0.3.0`) and `compiler` the pin; nothing says the version of a
  *flag table*, so an agent that cached a schema cannot tell it is stale other than by `version`.

## 11. What the readers/query design needs from the server

* **A table the server serves is under one root, read-only.** The server has `file_read` nowhere: a
  reader that must resolve a name (`table://...`, a set of files, a glob) cannot be added to the server without giving
  it `fs_read` or running another tool. Names stay the tool's: `file` is the tool's FILE under `--root`.
* **Structured paging is already there** (`truncated`, `next: {"from": N}`), and the tool's own `max-bytes` page is
  1 MiB: a client wanting smaller answers than the server's 2 MiB bound sets `limit` (and `max-bytes`) per call, or the
  operator lowers `--max-output`. A reader that returns bigger pages than the server's bound needs the bound
  raised for it, or pages sized to it: `mcp.output-too-large` is the refusal and `detail.limit` the number.
* **The schema is generated, so a new flag is in it on the next `scripts/mcp.py`** with its help, kind and ceiling,
  and a flag of a *new kind* is a `sys.exit` in the generator (it lists the kinds it knows) rather than a silent hole.
  The query design's additions (`--order-by` keys, `:dec`, `:float`, anything `introspect` gains) arrive as help
  text: they need an accurate `--agg`/`--where` help (§10) because that text is the model's grammar.
  **Built (Q1):** `query` (a string, with the grammar in words as its description) and `rows` (a boolean) are two more properties of the one tool, from the flag table; a call
  `{"file": ..., "query": ...}` is the CLI's `--query=TEXT -- FILE`, and `tests/conformance/test_mcp.py`'s corpus holds it to the CLI's bytes, a refusal of the query
  front end and its repair (the invocation offered back, with the `--query=TEXT` argument replaced) included. The query's own `from 'file'` goes through the same confinement as `file`.
* **Several tools or a second tool** (a reader as its own binary): the generator and the server are written over a
  table of tools (`count()`, `name(i)`, `path(i)`), and this one fills it with one row; a second row is a second
  `[[bin]]` and a second directory entry, but `Exec` is narrowed to one directory, so both binaries live in it.
* **Constraints between flags** (§3) would let the server refuse a bad combination before the process starts and
  document the forms in the schema; they need `introspect` to carry them, and the parser to read the same table.
* **A `stdin` property for a tool that reads standard input.** cancho-tools' server handles `stdin` as a property (and
  `--stdin`); the code is there in the copy. `table` reads standard input (docs/readers.md, R1), and the server offered it a file
  only until §12, which designs and records the property (built there).
* **A result that is not UTF-8** is lossy in `csv`/`text` mode (§2). If the query design adds raw output modes,
  the answer for MCP is base64 in the JSON document or a refusal, not text.

## 12. The `stdin` property (design, recorded before the code)

> **Status: built on branch `mcp-stdin`** (the numbers are in §12.6). The design below was written first and the gates of §12.5 were written
> to fail against `main` before any code.

An MCP client that holds a table in its context (a model that was handed rows, a query result, a pasted CSV) has no way to give `table` those bytes:
`file` names a file under the server's `--root`, and the server can write none (§6: no `fs_*`). `table -` reads standard input, and the server cannot
use its own: **its standard input is the JSON-RPC channel**, so it must never read it for the tool, and a call cannot be allowed to pass it through.
What it can do is what it already does: it owns a pipe to the child's standard input (`process.channels_with_errors`, written by `capture_both`
on the same poller as the child's output and error), and today writes nothing to it.

### 12.1 The decision

A new property of the one tool, **`stdin`** (a string): *the table as text, instead of `file`; give exactly one of the two.*

* **The mechanism is the pipe the server already owns.** The decoded string is `capture_both`'s `input`. No file is made, no temporary directory,
  no new label: **the authority row is unchanged** (`pipe_write` and `poll` are in it for this very pipe): every label, `bounded` and the foreign symbols of
  `manifests/mcp.authority.json` are `main`'s, and a gate says so (§12.5). The *file* differs from `main`'s only in its list of the functions the server
  has (`default_input`, `hint_input` are two new pure names; 184 functions, 182 before). Had building this changed a label, the work would have stopped
  there and been reported for review.
* **The argv is `table --root=ROOT FLAGS -- -`**: the operand is `-`, the tool's own name for standard input. A call with `stdin` is therefore the
  command `printf %s TEXT | table --root=ROOT FLAGS -- -`, and the same bytes of standard output, standard error and exit code. `--root` is ignored by
  the tool for standard input (docs/readers.md section 3.2) and is still passed first and once.
* **Exactly one of `file` and `stdin`.** Both: `mcp.input-conflict`. Neither: `mcp.input-missing`. Both are JSON-RPC `-32602` with
  `data.rule` and `data.properties`, before any process is started. (`main` did not enforce `required`: `{}` reached the tool and got the empty table's
  answer. That was the generated `required: ["file"]` promising what the server did not check; now the schema says neither is required and the
  server checks the pair.) An **empty string is an input** (the empty table's answer, as `table - < /dev/null`), not a missing one.
* **A bound: `--max-input N`, default 1 MiB (1,048,576 bytes), of the decoded text.** Past it the call is refused before the tool starts, as
  `mcp.input-too-large`: an `isError` result in the shape of the tool's own errors (like `mcp.timeout`), `detail.limit` the bound, and a `hint` that says
  what to send instead (the header and the rows needed, not the whole table). The bound is exact: N bytes pass, N + 1 do not. The request line
  (4 MiB, `mcp.line-too-long`) bounds it from above whatever the flag says. *1 MiB* is the tool's own page size (`--max-bytes`) and half of the
  output bound: a judgement, an operator's knob, not a measurement of any client.
* **Text only, and it is the JSON parser's rule.** A JSON string is Unicode. Bytes that are not UTF-8 in the request line, and a lone surrogate
  escape (`\ud800`), do not parse: the whole line is `mcp.parse-error` (`-32700`, `id: null`) and **no tool is started** (measured, §12.6). So what
  `table` receives is always valid UTF-8; a NUL (`\u0000`) is allowed, because a pipe carries it and the tool treats its input as bytes, as `table -` does
  (the argv refuses NUL, `mcp.nul-in-argument`, only because an argument cannot hold one). Data that is not text cannot be sent in a call; there is no
  `stdin_b64` (below).
* **`--query` with `from`.** The operand is `-`, so `--query` text that names a source (`from stdin` or `from 'x'`) with a `stdin` call is the
  tool's `args.conflict` (docs/query.md section 5: a source and an operand), exactly as `table --query ... -`. A query with no `from` reads the
  input. The property's description says so. This is a refusal rather than a silent choice: with no operand a `from 'x'` would read a file and
  ignore the bytes the caller sent.
* **A tool that fails before it reads** (a bad flag is refused before the first byte of input is read) must not hang or kill the server: the
  server writes into a pipe the child has closed. This is the case most likely to be wrong, and has a gate of its own (§12.5).

### 12.2 What was considered

| alternative | verdict |
|---|---|
| **Keep MCP file-only, and say so** (the property stays out; the description tells the caller to put the data in a file under the root) | Rejected. The server cannot write a file and the caller (a model) usually cannot either, so the sentence would be a dead end for the case the property exists for. It stays the answer for data that is not text or exceeds the bound. |
| **The server passes its own standard input through** | Impossible and unsafe: it is the JSON-RPC channel. The server reads nothing for the tool and the tests check the tool cannot read the channel. |
| **Write the text to a temporary file under `--root`, give `table` its name** | Rejected, and the stop condition of this work: it needs `fs_write`/`dir_write` and a place to write, a widening of the authority row (and a cleanup, a race, a quota). Reviewed as authority growth, not built. |
| **Inline text through the child's pipe** | **Chosen.** Zero authority growth, no copy to disk, and the equivalence with `table -` is the argv. |
| `stdin_b64` (base64) for data that is not text | Not built: a second property and a decoder in the server for a case no one asked for; the JSON format already carries cells that are not text (§2), and a client that has bytes has a file. |
| No operand (`table FLAGS --`) instead of `-` | Rejected: `--query ... from 'x'` would read a file and drop the caller's text silently. `-` makes every mismatch a refusal of the tool. |
| Both `file` and `stdin` given: prefer one | Rejected: choosing silently drops data the caller meant to be read. |
| `file: "-"` to mean the call's `stdin` | Rejected: it overloads a name that is a file called `-` for every other reader, and `main`'s answer to `file: "-"` (the empty table) stays what it was. |
| The bound as the request line only (4 MiB) | Rejected: no operator knob, and a bound that is a framing limit is an accident. `--max-input` is the operator's, like `--max-output`. |

### 12.3 Where it lands

`scripts/mcp.py` (the generator: the `stdin` property from the tool's optional operand, `file` leaves `required`, the rules and `--max-input` in
`mcp introspect`, `default_input`), `generated/mcp/tools.cho` and `server/mcp.cho` (the pair check, the `-` operand, the bound, the flag),
`tests/conformance/test_mcp_stdin.py` (new), `scripts/mcp_mutants.py` (new mutants), and docs. `tools/table` and `generated/table` are untouched:
the binary is the same bytes as `main`'s.

### 12.4 What stays true

The tool's refusals, rules, hints and repairs are the tool's (the server adds three rules of its own: `mcp.input-conflict`, `mcp.input-missing`, `mcp.input-too-large`);
a call with `file` is the call of `main`, argv for argv; the server's authority and ceiling are `main`'s.

### 12.5 The gates, written before the code

`tests/conformance/test_mcp_stdin.py` (26 tests) was written and run against `main`'s server before any server code; 23 of the 26 failed there (the other
three hold what `main` already did and the design keeps: that bytes that are not UTF-8 or a lone surrogate do not parse, that a call with `file` gives the
tool an empty input, and a fuzz that is vacuous until the property exists, so it is also held by mutants, §12.6).

| gate | what fails it |
|---|---|
| conformance of the property | valid (`stdin` as text, an empty string, a NUL, CRLF, a byte order mark, astral characters, a table of exactly the bound); too large (exact bound, in decoded bytes not characters nor escapes, no process started); not text (seven forms of invalid UTF-8 and six of a lone surrogate: `mcp.parse-error`, no process started, the server answers the next line); both `file` and `stdin`; neither; the wrong type of `stdin` (five types); the order of refusals |
| equivalence with `table -` | each call's `content`, `isError`, exit code and `structuredContent` against `table --root=R FLAGS -- -` run with the same bytes on standard input: a corpus of the benchmark table under 33 plans and 15 small tables under 10 plans each (refusals included), and 300 seeded random plans over seeded random tables (ragged rows, CRLF, quoted newlines, astral characters); and `stdin` against `file` over the same bytes (the documents are equal) |
| a child that does not read | exits before it reads (40 calls of 900 KiB in one session), refuses a flag before it reads (30 calls of 960 KB with the real tool), never reads (the deadline ends it, killed and gone), reads late (all 900 KB arrive) |
| no panic | 400 seeded damaged requests around the property (bytes, truncation, junk, escapes, 4,000-escape strings, wrong types): each answered with a rule or ignored, no trap, a ping answered after; and 300 calls of 8 KB in a row |
| the tests of `test_mcp.py` | 43 tests; four of them held the old surface (`file` required, no `stdin`, three flags, the rules list) and were edited for it, §12.6 |
| the tool is the same | `tools/table` and `generated/table` untouched; the binary is the same bytes as `main`'s; the corpus of 166 plans (`scripts/corpus.py`) has the same md5; the instruction-count gate (`gate_regress.py --counter`, bound 1.01) against `main` |
| authority | every label, `bounded` and the foreign symbols of the row are `main`'s (compared by a script, §12.6); `scripts/mcp.py --check` (the ceiling) passes |
| mutants | 24 new in `scripts/mcp_mutants.py` (and one removed: it pinned the old rule), real run on `gram` |
| generated files | `scripts/mcp.py --check`, `schemas.py --check`, `manifest.py --check`, `site.py --check`, `seo_check.py`, `stream.py --check`, every `*_mutants.py --check`, `cancho fmt --check` |

### 12.6 What was measured

Conditions: Mac = arm64, macOS (Darwin 25.2), compiler `e47d647` (the pin); gram = Linux x86-64, 16 cores shared with other jobs (load average 4 to 6 during the
runs), work niced and pinned to cores 0 to 5, `perf_event_paranoid` 4 (not changed). Head of the branch `mcp-stdin` on `c6b010e`.

| item | result |
|---|---|
| `test_mcp_stdin.py` before the code | 26 tests, 23 failed against `main`'s server |
| `test_mcp_stdin.py` after | 26 of 26 pass: Mac 10 s; gram, with `test_mcp.py`, 69 tests in 57 s |
| `test_mcp.py` (43 tests) against the new server | 38 pass untouched. 4 are edited, each because it held the old surface: `test_tools_list_is_what_introspect_says` (the property set, `file`'s description, `required` is now empty), `test_introspect` (the flag, the limit, the usage line), `test_every_rule_it_lists_is_one_it_says_and_every_one_it_says_is_listed` (the three new rules are provoked), `test_the_generator_refuses_what_would_be_false` (`file\|0\|1`, `stdin` is `text`, and a tool that does not read standard input keeps `file` required). `test_the_mutants_still_apply` passes once the mutant list is current |
| the whole suite | Mac: 539 tests OK, 10 skipped, 632 s |
| corpus of 166 plans (`scripts/corpus.py`) | md5 `fccb01b66dd7518d74611219a6c5aa29` on `main`'s binary and on this branch's: the md5 recorded in docs/readers.md section 11, and the two binaries are the same bytes on the Mac (`cmp`). The sources of the tool are untouched, so this is a check that nothing else moved |
| instruction counts (`gate_regress.py --counter`, bound 1.01, 42 cells, 3 builds a side) | Mac: **PASS**, ratios 0.998 to 1.003, spread under 0.5 percent. The six builds are the same bytes (one md5) on the Mac, so the ratios are the counter's run-to-run noise, not layout; the gate was shown to fail on the same builds when its bound was set to 0.99 (a control of the arithmetic, not of a real regression: `test_gate_regress.py` is the gate's own test) |
| clock form on gram (the counter is unavailable there) | **FAIL as a gate, and the failure is the control's.** Clock form (`gate_regress.py` without `--counter`, 21 runs, bound 1.02, 3 builds a side of `main` and of the branch, cores 0 to 5, niced, other jobs running): 8 of 42 cells over 1.02, ratios 0.934 to 1.061, spreads up to 20 percent (a build "off"). **The two sides are builds of the same tool sources** (`tools/table` and `generated/table` are byte for byte `main`'s; the six Linux builds are six different binaries, so this is layout alone), so the gate fails its own control, as docs/numbers.md G6 says it does on both machines, and says nothing about this change either way. The evidence for "the file path is not slower" is the diff (empty for the tool) and the Mac counter above; gram's counter was not available (`perf_event_paranoid` 4, left as it was) |
| mutants (`mcp_mutants.py`, real run on gram, nice, cores 0 to 5) | **90 of 90 killed** on the first run (unmutated pass first): the 66 that remain of the 67 (one, which pinned the old rule that the operand is required of the caller, is replaced) and 24 new, which are the pair check (3), the `-` operand, the text reaching the child, the bound (one more, one less, not the flag's, not enforced, no limit named, the wrong hint, the wrong rule, the flag setting the output bound, zero accepted, the default), and the generator (not offered, not a text, `minLength`, the operand required again, its description, the default). `--check` of the 16 mutant scripts passes |
| authority | every label, `bounded` and `foreign_symbols` of `manifests/mcp.authority.json` equal to `main`'s (checked by a script: `labels` equal, 11 labels); the file differs in its `pure` list (`default_input`, `hint_input`) and `functions` (182 to 184). `scripts/mcp.py --check` (ceiling) passes |
| generated files | `mcp.py`, `schemas.py`, `manifest.py`, `site.py`, `stream.py --check`, `seo_check.py`, `cancho fmt --check tools generated`: pass (Mac) |
| cost of the text (informational, Mac) | a 1 MiB table, 30 calls in a session, the minimum of 5 sessions: `agg count` 8.8 ms by `file`, 34.5 ms by `stdin`; `select a limit 1` 2.2 ms and 27.6 ms. About 25 to 30 ms a MiB more by `stdin`, of which the parse of the request line, the decoding and the pipe (8 KiB on macOS) are not separated. gram not measured |

**Found while building.** `main` did not enforce `required`: `{}` (no `file`) reached the tool and was answered with the empty table. The new pair check closes it
for this tool (§12.1). The JSON parser of the server already refuses invalid UTF-8 and lone surrogates; the property needed no check of its own for text.

**Not measured.** A client that really sends `stdin` (Claude Code or another: the transcript of §9 has no such call); the server's memory for an input of 1 MiB
(or of the 4 MiB the line allows); gram's timings of the text's cost; Windows; the cost of `--threads` with an input (the tool uses one core for a pipe,
docs/readers.md section 3.2, and that was not repeated here); an input whose decoded size is between the bound and the line limit on a slow client. The default
of 1 MiB is a judgement.

