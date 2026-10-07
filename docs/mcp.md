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
* **Standard input is empty.** `table` reads a file; the server gives the child an empty input, so a
  tool that read standard input unasked would see its end at once. (The input schema has no `stdin`:
  the generator refuses a tool whose authority has `io_read`.)

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
| `kind: bool` | `{"type": "boolean"}`, passed as `--name` when true (the tool has none today; the generator and the server handle it, and a test builds a scratch copy of the tool with one) |
| `kind: nat` | `{"type": "integer", "minimum": 0}`, and `maximum` from the flag's row in `limits` (its ceiling) |
| `kind: text`, `any`, `path` | `{"type": "string"}` (`text` with `minLength: 1`); a `path` flag with role `root` is the server's and is not offered |
| `kind: choice:a/b` | `{"enum": ["a", "b"]}` |
| the operand `FILE` (min 1, max 1) | `file`, a string, in `required` |
| `help`, `default` | `description`, `default` |
| `guarantees.idempotent`, `authority.effects` | `annotations`: `readOnlyHint` (no write or network effect), `idempotentHint`, `openWorldHint: false` |

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
`pipe_write`, `poll`. (`pipe_*` and `child_signal` are what it does with the child and channels it owns; `clock`
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
package this repository consumes is pinned at `8dab9176`, and the server is not in it), and `scripts/mcp.py`
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

`tests/conformance/test_mcp.py` (42 tests, 10 s with the server built):

* **Differential.** A corpus of 70 plans over the benchmark generator's file, quoted and multi-line fields,
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
in the generator (schema, gate); `--check` (CI) verifies each still applies exactly once. RESULTS_PLACEHOLDER

`scripts/mcp_client.py` is a minimal client, used for the transcripts below.

## 9. Tried for real

TRIED_PLACEHOLDER
