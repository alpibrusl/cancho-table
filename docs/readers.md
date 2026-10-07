# Readers, standard input, JSON lines and more outputs: a design

> **Status: a design. Nothing in `tools/` changes with this document.** It is written before the code, as `docs/numbers.md`
> was: the decisions, the numbers that decided them, the gates that must be able to fail, and the questions for the
> maintainer. [`docs/query.md`](query.md) is its twin (the `--query` front end, `--explain`, the surface). The throwaway spikes behind the
> numbers are in `scripts/spikes/` (`jsonl_scan.cho`, `jsonl_scan_x.cho` + `make_jsonx.py`, `stdin_read.cho`, `jsonl_gen.py`, `jsonl_time.py`, `jsonl_check.py`,
> `bench_jsonl.py`); nothing in them is part of the tool.
>
> **How things were measured.** Mac: Apple silicon, 16 cores, macOS, the compiler the tool is pinned to
> (`a4572ea`), load average 2.5 to 7 from other work on the machine (the *shapes* below are stable; absolute nanoseconds are good to
> about 20 percent, and where two runs differed both are given). Linux: x86-64 `gram`, cores 0 to 5 (three physical cores
> and their hyper-threads), niced, load average 5.6 to 6.7 from a shared box, scratch directory removed afterwards.
> The file is `scripts/bench.py`'s, 1,000,000 rows, 31,667,311 bytes as CSV and 71,667,285 bytes as JSON lines (one object per
> line, `{"id":0,"status":200,"bytes":74606,"path":"/p/867","note":"a,b 0"}`; `jsonl_gen.py` writes both, and its CSV is byte for byte the benchmark's: md5 checked). A spike's per-record cost is the difference of a run of
> 11 to 101 rounds and a run of one (the load of the input drops out); every spike prints an answer that is compared with Python's before its time is believed.
> **Claims that are projections, not measurements of a built tool, say so.**

## 0. The short version

| decision | choice | why, in one line |
|---|---|---|
| the record reader | **no trait, no hot-loop refactor**: the interface is the engine's existing boundary (a record's bytes + 3 ints per field + its line), named and documented; each format keeps its own read loop, the engine is shared | the repo has measured that a case added to the shared loop costs the others 15 percent (`docs/backlog.md`); the cancho language has no traits or closures; `table.cho` and `scan.cho` are already two loops over one engine |
| standard input | `table -` or no `FILE` (the family's convention: `tally`, `jsonq`); streaming, sequential, `--threads` degrades to one core with the same bytes, `--from` skips inside the one pass; the authority row gains `io_read` | the answer must be the file's answer; a pipe has no `pread` |
| stdin speed | **`getchar` is the floor and it is slow: 71 MB/s on the Mac, 264 MB/s on the Linux box, against 2.9 GB/s through a file** (40x and 12x). Ship it, say it, and ask cancho for a bulk read | measured, section 3.4; not hidden behind `/dev/stdin` |
| second reader | **JSON lines** (`--input-format jsonl`, also spelled `ndjson`): one object per line, strict RFC 8259 through `std.json` | one parser, already in `std`, strict, tested |
| its columns | **declared**: `--fields a,b.c,d.0` (a flat projection by dotted path); `--discover` is a bounded *report* of what the first lines hold, never a silent plan | "no guessing" is the tool's rule; a header is not free in JSON |
| what a cell is | string -> its decoded text; number -> its exact token text; `true`/`false` -> that word; `null` and a missing key -> `--absent refuse` (default) or `empty`; object/array -> refused; a duplicate key on a declared path -> refused | every case a rule or a defined mapping, no silent stringify |
| the cost of JSON | `std.json` per line is **about 650 ns on the Mac (24x a CSV record)**, 283 ns on Linux (3.9x). **All of the Mac's cost is one `region` per `parse` call: the same parser with the two ints of state passed in costs 63 to 106 ns (2.3 to 3.6x CSV) and scales with threads like CSV.** Ask upstream for `parse_with`; do not write a second scanner first | measured, section 6 |
| a scanner of my own | **not in R2.** A flat-object scanner with `std.json` fallback was built as a spike: 59 to 69 ns on the Mac (between equal to and 1.6x faster than the fixed `std.json`), 170 ns on Linux (1.5x faster than `std.json`'s 255); it agrees with `std.json` and with Python on 25,070 lines. Kept as plan B if the Linux gate fails | section 6.3 |
| parallel | JSON lines splits at newlines with **no speculation** (a raw LF cannot be inside a JSON text); stdin cannot be split | section 2 |
| other outputs | **JSON lines out (`--format jsonl`)**: yes, in R3. **Markdown table (`--format md`)**: not before someone asks (optional R3b). TSV is `--delimiter tab`, already there | section 7 |

## 1. What there is today, and what that means

* **One reader, written into two loops.** `table.cho`'s sequential read and `scan.cho`'s range read each do: frame (the 64 KiB `lines`
  reader and `reader.scan`'s quote state), split (`reader.fields`: three ints per field, `first`, `last`, `quoted`), then call the
  **same** functions in `engine.cho` with `record: &[byte]`, `cells: &[int]` and the line the record began on. The header is two
  buffers (names, where each ends); `plan.resolve` turns names into column numbers; **nothing after the header
  knows what a CSV is** except the `quoted` flag in a cell (a doubled quote still to be undone) and `--delimiter` in the writer.
* **One input**, a path, opened by `place.open_operand` under `--root`; `FILE` is a required operand (`args.missing-operand`). The authority row has
  no `io_read` (`tools.toml`: "no standard input").
* **A `.jsonl` file given to `table` is accepted silently as a one-column CSV** (measured: `table j1.jsonl` for the file `{"a":1}`
  answers `headers: ["{\"a\":1}"]`, `column_count: 1`, success). Whatever is built must at least say so in a refusal.
* **`--from` re-reads from the start**, and `--threads` needs `pread` on a seekable file.

## 2. The reader interface

cancho has no traits, no closures that capture, no generics over modules; a function value is a captureless `fn`. There are three ways
to say "a reader", and what each costs here:

| way | what it is | cost | verdict |
|---|---|---|---|
| (A) one shared loop, `if fmt == ...` per record | one `read_file`, the format a loop-invariant integer | **measured against us in this repo**: a branch that was never taken at the place a row is counted made `--select` and `--where` 15 percent slower (`docs/backlog.md`, "A third case in the read's loop") | no |
| (B) a function value per format called per record | `let split = reader.fields_csv;` | no inlining, an indirect call per record; the engine's calls take 8 to 12 arguments already | no |
| (C) **one loop per format over the same engine** | what `table.cho` and `scan.cho` already are | duplicated framing (a few dozen lines per loop); no cost to the other format; the CSV loops are untouched | **yes** |

So **the interface is a contract, not a type**, and it is the one the engine already consumes. A *reader* is whatever turns bytes into this,
and it is documented in `engine.cho`'s header and tested at its boundary:

```
header      names: a Buffer + where each name ends (what frame.cho / plan.resolve take today)
            CSV: the first record.  JSON lines: the declared --fields (nothing is read for it).
record      bytes   &[byte]   the record, whole (CSV: its lines joined by LF, the last CR dropped; JSON lines: the line, CR dropped)
            cells   &[int]    3 ints per field of the header, in header order: first, last, flag
                              into `bytes`; flag 0: the cell is bytes[first..last] as it is,
                              flag 1: CSV-quoted, a doubled quote in it is one quote of text.
                              (A JSON string with an escape is DECODED into the record buffer after the line, flag 0: the engine never sees JSON.)
            line    int       the 1-based physical line the record began on (error reports)
            (the record number, 1-based among data records, is the engine's count: engine.count_row)
status      RECORD | NEED more bytes | DONE | LONG (a line past --max-line-bytes) | REFUSED (a rule, its line, its facts)
```

What the existing pieces are, in these words:

* **CSV's quote state** (`quoted`, `seps`, a record that spans lines) is the CSV *framer's* private state. A JSON-lines framer has none: a record is a line.
* **The parallel scan's range speculation** (`docs/parallel.md` section 2) is a property of a *framer*: "a range assumes its first line starts a record". CSV
  needs the parent's check (`first line == cur`) because a quoted newline can make the guess wrong. **JSON lines can never be wrong**: RFC 8259 allows no unescaped control
  character inside a string, so an LF is either whitespace between two tokens (never inside a one-line record) or the end of a record. Every range starts at a line
  start (`par.align`), the check `first line == cur` is kept (it is an equality; it costs nothing and never fails), and **there is no re-read for a wrong guess**.
* **`--from`** is already format-independent: `engine.count_row(a, found, opened, from)` counts a record and says whether it is at or past `from`; a skipped record is framed and split
  (`found` fields are needed for the ragged check) and dropped. JSON lines skips the same way. (A skipped JSON line is still *parsed* by `std.json`, because strictness is per line and an
  error in a skipped line is still the file's error, as a bad quote in a skipped CSV record is.)
* **The header** is the other half: `frame.cho` resolves names against `(names, ends)`. JSON lines hands it the declared fields.

### 2.1 Which sources can be what

| source | how bytes arrive | ranges (`--threads`) | `--from` | the header | notes |
|---|---|---|---|---|---|
| CSV/TSV file | `file_read` / `file_pread` | yes, speculative (the parent verifies each range's first line) | skip-scan | the first record | today |
| **JSON lines file** | the same | **yes, never wrong** | skip-scan | declared `--fields` | R2 |
| **standard input**, either format | `getchar` into the same 64 KiB chunk | **no** (no `pread`, no size) | skip-scan *in the one pass* | as the format says | R1 |
| compressed input, a JSON array, pretty-printed JSON | not read | | | | `.gz` is not in `std`; a JSON array or a pretty-printed document is not JSON lines and is refused with a pointer to `jsonq` / `jq -c` (section 5.1) |

## 3. Standard input

### 3.1 Naming it

`table [flags] -` and `table [flags]` (no operand) read standard input. This is the family's convention, in the words of `tally` ("none reads standard input") and `jsonq`
("`-` or nothing"), and `introspect`'s operand table says it with `min: 0` and the tool description's `stdin: "when FILE is - or absent"` field, which
`cancho-tools`' MCP server already turns into a `stdin` argument. A file called `-` is `./-`. `--root` does not apply to standard input (the MCP server always passes it, so it
cannot be a refusal) and `introspect` says so. In the query form it is `from stdin` ([docs/query.md](query.md)).

*Trade-off considered:* requiring `-` (so a forgotten `FILE` stays `args.missing-operand`) is safer for a person at a terminal, where `table --where x` would otherwise wait
for the keyboard. It was rejected because every other reader in the family takes no operand, because an agent's subprocess has a closed or empty standard input (the forgotten
`FILE` is then an immediate, ordinary empty-input answer, below) and because MCP's schema generator wants one rule per operand. Open question 1 asks for the maintainer's call.

### 3.2 What it does, and the refusals and degradations

| situation | what happens | why |
|---|---|---|
| the same bytes as a file | **the same answer, byte for byte**, for every plan, as JSON and CSV, refusals included except `detail.path`, which is `"-"` | the gate of R1: every case of the conformance suite run twice |
| empty standard input | the same as an empty file: `headers: []`, `column_count: 0`, success | a file of no bytes does that today; one rule |
| `--threads N` (N > 1) | **one core, same bytes, no refusal** (`--threads` has always meant "the same bytes on any number of cores"; `--order-by` already runs on one core under it). `--explain` says `threads_effective: 1, reason: "standard input has no ranges"` | a refusal would make every agent that sets `--threads` for files fail on a pipe |
| `--chunk-bytes`, `--parallel-min-bytes` | ignored (they size ranges) | |
| `--from N` | allowed: the read skips N records in the one pass. **What cannot be done is resuming**: a JSON page that ends with rows left carries `next: {"from": N}`, and running `table --from N -` needs the same bytes again, which only the producer can give | the cursor is an offset into the stream; the skill says so. *Not* a refusal: `cat f \| table --from 1000` is a fine, deterministic call |
| `--max-rows`, `--max-line-bytes`, the group and sort bounds | as for files. A producer that never ends is bounded by `--max-rows` (default 10,000,000 records) and a producer with no newline by `--max-line-bytes` | no new limit is needed |
| a terminal | not detected (cancho cannot ask) | the operator's problem; see the lacks list |
| `--root` | ignored | MCP |
| `-` given twice, or `-` with other operands | `args.too-many-operands` | one input |
| `--query ... from 'x'` together with an operand | `args.conflict` | [docs/query.md](query.md) |

### 3.3 Streaming and memory

The same `lines` reader (64 KiB chunk, line capped at `--max-line-bytes`), fed by the contract's `lines.fill_stdin`, which already exists. Memory is what it is for a file
(the spike, which reads standard input in 64 KiB chunks and keeps nothing, peaks at 1.4 MB).

### 3.4 What it costs: the finding

`stdin_read.cho` counts the bytes and newlines of the 31.7 MB CSV in 64 KiB chunks and keeps nothing; best of 5.

| way of reading | Mac (arm64) | Linux x86-64 (gram, loaded) |
|---|---:|---:|
| `getchar` per byte into the chunk (`lines.fill_stdin`) | **0.444 s = 71 MB/s** (redirect), 0.439 s through a pipe | 0.12 s = 264 MB/s |
| the same with `getchar` straight into the chunk's room (no `buffer.push` per byte) | 0.42 to 0.43 s | |
| open the path `/dev/stdin` through `Fs` and `file_read` it | **0.011 s = 2.9 GB/s** (redirect), 0.013 s through a pipe | 0.01 s |
| an ordinary file, `file_read` | 0.011 s | 0.01 s |
| `cat` to `/dev/null`, for scale | 0.015 s | |

So **a 1,000,000-row CSV through a pipe costs the reader 0.44 s on the Mac, where the whole question costs 0.046 s from a file**: standard input is **9.6 times slower than the
entire query on a file**, and the cause is `getchar` (13 ns a byte: a libc call per byte) and nothing in `table`. Options:

1. **Ship `getchar` (R1), say the number in the docs and in `--explain`'s `notes`** (`"source": "standard input", "ceiling": "about 70 MB/s on macOS, 260 on Linux"`). Honest authority row (`io_read`).
2. **Open `/dev/stdin` as a file** (0.011 s): 40x faster, **and not proposed**: it reaches standard input through the *file system*, so the authority row would say `fs_read("")` (which it already does for every path) and *not* `io_read`: the program
   would read the one stream a reader of the row believes it does not, which defeats the row. It is also a path the `--root` confinement cannot reason about, and it is not portable.
3. **Ask cancho for a bulk read** (`std.io.read_into(io, &![byte]) -> [io_read] Read`, with the same three-way `Got/End/Failed` as `file_read`, which cancho's `docs/bulk-io.md` section 3.3 deferred "until the short read, end and error question is answered"; `file_read` already answers it).
   This is the right fix and is what the goal "fix gaps upstream" asks for. R1 ships with (1) and the gate "within 1.1x of the `getchar` floor"; when (3) lands the stdin gate becomes "within 1.5x of the file's time".

A person who has a large file should name it; the skill says so.

### 3.5 The authority row

`io_read` is a new label in `manifests/table.authority.json` and in `tools.toml`'s ceiling for `table` ("no standard input" is in its comment today): **a change somebody has to approve**, and the
conformance test that runs the binary under `strace` and requires no socket and no write on disk must still pass. `scripts/manifest.py --check` and the page's authority row move with it.

## 4. What the engine sees of a format: no change

The plan (`--select`, `--where`, `--group`, `--agg`, `--order-by`, `--sort`, `--top`, `--limit`, `--from`) resolves against the header; for JSON lines the header is the declared fields, so
**`--select user.name`, `--where "status:int >= 500"`, `--group user.name`, `--agg sum:bytes:dec(2)` work unchanged**, as does every type, every bound and every refusal of the engine (`value.not-integer` names
the row, the line and the column, the column being the field's path). The columns of a JSON page and the header of CSV output are the paths as written in `--fields`.

## 5. JSON lines

### 5.1 The input format, exactly

One JSON text per line, RFC 8259, parsed by `std.json` (strict: no comments, no trailing commas, no leading zeros, no `NaN`, no single quotes, strings valid UTF-8 with no unescaped control character and no lone
surrogate escape; nesting at most 128 deep, `std.json`'s own limit).

| question | decision | why |
|---|---|---|
| **JSON lines or NDJSON?** | one format, two spellings of `--input-format` (`jsonl`, `ndjson`). Both specifications say: UTF-8, one JSON value per line, `\n` ends it, `\r\n` allowed. We read **objects only** | `jsonlines.org` allows any value per line; a table needs objects. A line that is another JSON value is `parse.jsonl-not-object` |
| **a blank line** (nothing, or only JSON whitespace) | **skipped**, and counted in the line number | as a blank CSV line is skipped (`docs/select.md`); NDJSON says a parser may ignore them |
| **a missing final newline** | the last line is a line | as CSV |
| **CRLF** | the CR before the LF is dropped (it is JSON whitespace, so `std.json` would take it anyway; dropped so that `offset` counts from the text) | |
| **a byte order mark** | dropped at the start of the input, as CSV does (RFC 8259 lets a parser ignore it) | |
| **a pretty-printed document, or an array of objects** | not JSON lines: line 1 is `{` or `[`, so `parse.jsonl-syntax` at its end; the hint says `jq -c '.[]'` or `jsonq`. `detail.looks_like: "json-document"` when line 1 is `[`, a lone `{`, or `[{` | the `jsonq` boundary of `docs/backlog.md` item 2 |
| **a line longer than `--max-line-bytes`** | `limit.line-too-long`, as a CSV line | the bound exists; reused |
| **a very long key, deep nesting, a huge array** | no separate key bound: a key is a slice of its line, so the line's bound is its bound (a declared path is at most 8 segments of a flag value). Nesting is `std.json`'s 128 (`parse.jsonl-syntax`, code `depth`); a line with more JSON nodes than `--max-json-nodes` is `limit.json-too-many-nodes` | each is a bound that already exists or one number |
| **invalid UTF-8 outside a string, a NUL, any other control byte** | `parse.jsonl-syntax` with `std.json`'s code and position | |
| **a line that parses but is not an object** | `parse.jsonl-not-object` (exit 8, `detail.kind`) | |
| **duplicate keys** | a key that occurs twice **on a declared path** is `parse.jsonl-duplicate-key`; keys the plan never looks up may repeat freely | parsers disagree (`std.json` keeps the first, Python and `jq` the last): a silent choice is where two programs differ about what a line says |

### 5.2 Columns: the declared flat projection

**Header discovery for a JSON stream is not free.** Three candidates:

| way | what it does | verdict |
|---|---|---|
| first object's keys | read line 1, its keys are the header | **no**: the schema of a stream is not its first line; a key that first appears on line 2 is silently not a column; a stream whose producer reorders keys changes the header between runs; it also needs a nesting rule (flatten?) |
| union of keys over the first N lines | bounded read, header = first-appearance order | **as a report, not a plan**: the same silent loss for a key first seen on line N+1, and the header is data-dependent (a column's position changes with the file), so `#3` means different things for two files |
| **an explicit declaration** | `--fields a,b.c,d.0` | **yes**: the plan names what it asks; a field absent from a record is a *rule*, not a surprise; the header is stable; `--explain` and the engine know every column before a byte is read |

So **`--fields LIST` is required for any plan on JSON lines** (`input.fields-required`, exit 2, repair `retry` with `--discover`), and **`--discover` is a bounded report**
(`--discover-lines N`, default 1000, ceiling 1,000,000; `--max-line-bytes` and the node bound apply) that answers, as a `table.v2` document:

```
{"fields": [{"path": "id", "kinds": {"int": 1000}, "first_line": 1}, {"path": "user.name", "kinds": {"string": 998, "missing": 2}, ...}],
 "lines_examined": 1000, "complete": false, "paths_truncated": false, "arrays": ["tags"]}
```

`kinds` are `string`, `int` (a JSON number token that is an integer in the tool's grammar), `number` (a decimal point or exponent), `bool`, `null`, `missing`, `object`, `array`: the
vocabulary for choosing `:int`, `:dec(S)` or `:float` (the type *report* that `docs/numbers.md` section 5.2 promised: a report, never a coercion). Paths are flattened through objects
to depth 8 and counted up to `--max-fields` (default 256, ceiling 4096, `plan.most_names`' ceiling); an array is reported as `array` and not expanded (the agent writes `tags.0` if it wants it). `complete: false` is said whenever the input was not read to the end.
Nothing from `--discover` reaches a plan: an agent copies the paths it wants into `--fields`.

**Path syntax.** `--fields` is a list like `--select`'s (commas; a backslash escapes the next byte, here a comma, a backslash, a `#` or **a dot**). A name is a **path**: segments separated by `.`;
the column's name in the plan, in the output header and in refusals **is the path as written** (`user.name`). A segment is an object key or, when the value at that point is an array, an
index (`0`, or a number without a leading zero: `items.0.sku`; `01` is only ever a key). One lookup rule, decided by the runtime kind of the value, so no path is ambiguous: an object looks up keys, an array
looks up indices. A key that contains a dot is `a\.b`. An empty segment, a path longer than 8 segments, a repeated path, or more than 4096 paths is `input.bad-fields` (exit 2). `#3` and names work in the plan as for CSV.

### 5.3 What a cell is: every JSON value, defined

| JSON value at the declared path | the cell | typed reads (`:int`, `:dec(S)`, `:float`) |
|---|---|---|
| string | its **decoded** text (escapes resolved, UTF-8, may be empty, may hold a NUL) | the text is read by the table's grammar: `"12"` is the integer 12, as a CSV cell `12` is. The declaration is the user's |
| number | the **exact token text as written**: `-0`, `1.50`, `1E3`, `123456789012345678901234567890`. **Never through a double unless the column is `:float`** | `:int` of `1E3` or `1.50` is `value.not-integer`; `:dec(2)` of `1.50` is fine, of `1E3` is `value.not-decimal`; a number past 64 bits is `value.integer-overflow`; `:float` reads any of them; a token over 1,100 bytes is `limit.number-too-long` |
| `true`, `false` | the word `true` or `false` | refused by any typed read (`value.not-integer`...), as the text `true` is |
| `null` | **absent** (below) | |
| **absent** (no such key, an index past an array's end, a path through something that is not an object or array) | per `--absent`: **`refuse` (default)**: `parse.jsonl-missing-field` (exit 8, `detail`: `line`, `row`, `path`, `reason`: `missing`, `null` or `not-container`; repair `retry` with `--absent empty`); **`empty`**: the empty cell | an empty cell is what the engine already refuses in a typed read (`value.not-integer`: "an empty cell is not an integer"), so `--absent empty --where "bytes != '' and bytes:int > 5"` is the CSV idiom, unchanged |
| object, array | **refused**: `parse.jsonl-not-scalar` (exit 8, `detail.kind`, `path`; the hint says to declare a deeper path) | no stringified JSON in a cell, ever |

`--absent` is one flag for `null` and a missing key on purpose: both mean "this record has no value", and a flag per case would be two ways to get the same cell. The default is `refuse` because the alternative
conflates a missing key, `null` and `""` in one empty cell *silently*, and an answer about "the rows that have a value" would be about fewer rows than the file has (the argument of `docs/filter.md`, "Empty cells"). Sparse data is
common; the cost is one refusal with a repair that runs.

### 5.4 Errors: where they point

Every rule names the **line** (1-based, physical, blank lines counted, a BOM not counted), the **row** (1-based among non-blank lines, as CSV), the **path** (the declared field, for cell rules) and, for `parse.jsonl-syntax`, the **offset** (0-based byte in the line, CR dropped) with `std.json`'s `code`
(`syntax`, `end`, `control`, `escape`, `number`, `utf8`, `depth`, `full`, `trailing`), its message and the 32 bytes around it (`near`, escaped as the other details are).

| rule | exit | repair | refuses |
|---|---:|---|---|
| `input.fields-required` | 2 | `retry` with `--discover` | a plan on JSON lines with no `--fields` |
| `input.bad-fields` | 2 | never | a `--fields` that is empty, has an empty segment, a path past 8 segments, a repeated path, or more than 4096 paths; names the item |
| `parse.jsonl-syntax` | 8 | never | a line that is not strict JSON: the offset and `std.json`'s reason |
| `parse.jsonl-not-object` | 8 | never | a line that is JSON but not an object |
| `parse.jsonl-missing-field` | 8 | sometimes (`--absent empty`) | a declared path with no value (missing, or `null`) |
| `parse.jsonl-not-scalar` | 8 | never | a declared path whose value is an object or an array |
| `parse.jsonl-duplicate-key` | 8 | never | a key that occurs twice at a step of a declared path |
| `limit.json-too-many-nodes` | 8 | sometimes (raises `--max-json-nodes`) | a line whose JSON has more nodes than `--max-json-nodes` (default 4096; ceiling 1,048,576): `std.json`'s tape is 24 bytes a node, allocated once |
| the engine's `value.*`, `limit.*`, `column.*`, `select.*`, `agg.*` | | | unchanged; `detail.column` is the path |

**The first refusal in file order** is the answer, as for CSV: `--threads` ranges report in file order (the first range with one wins), and the gate runs every JSON-lines case at every thread count.

### 5.5 A JSON-lines file pointed at as CSV

If the CSV header's first byte is `{` and its last is `}`, any refusal (`select.unknown-column`, `column.unknown`, `parse.csv-*`) adds `detail.looks_like: "jsonl"` and a `choose` repair with `--input-format jsonl --discover`. The *success* path (a shape of a one-column file) is not changed (a CSV whose header
starts with a brace is legal). Open question 4.

### 5.6 Flags (all go in the flag table, so `introspect`, `skill` and MCP's schema are generated)

| flag | kind | default | |
|---|---|---|---|
| `--input-format` | choice `csv/jsonl/ndjson` | `csv` | **never guessed from a name or from content** |
| `--fields` | text | none | required with `jsonl` for a plan; `args.conflict` with `csv` |
| `--discover` | bool | off | the report; conflicts with plan flags |
| `--discover-lines` | nat | 1000 | ceiling 1,000,000 |
| `--absent` | choice `refuse/empty` | `refuse` | |
| `--max-json-nodes` | nat | 4096 | ceiling 1,048,576 |
| `--max-fields` | nat | 256 | `--discover` only |
| `--delimiter` | (existing) | `,` | with JSON lines input it only sets the delimiter of CSV *output* |

## 6. Cost

### 6.1 One record, five ways (spike `jsonl_scan.cho`)

The task is the same in every mode: of each record take `status` and `bytes`; count the records whose status is the text `404`; sum `bytes` as an exact integer (the tool's `parse_int`). The answer, `166893 49991975303`, is Python's in every mode before it is timed.
`csv` is the tool's own `reader.fields`, copied. `json` is `std.json.parse` into one reused tape plus two `json.get`. `jsona` allocates the tape in a `region` per line (what a first implementation writes). **`jsonx` is a copy of `std.json` whose
`parse` takes the two ints of state from the caller instead of making a `region` for them** (`make_jsonx.py` makes it from the compiler's `std/json.cho`: about ten lines moved). `flat` is the hand-written scanner of 6.3.

| per record, ns | Mac (16 cores, load 2.5 to 7) | Linux x86-64 (gram, cores 0-5, load about 6) |
|---|---:|---:|
| `lines`: find the line ends (the floor) | 8 to 10 | 13 |
| `csv`: `reader.fields` + the task | **27 to 31** | **72** |
| `json`: `std.json`, tape reused | **636 to 651** (22 to 24x csv) | **283** (3.9x) |
| `jsona`: `std.json`, tape in a region per line | 1,101 | 317 |
| **`jsonx`: `std.json` with caller-owned state (no region)** | **63 to 106** (2.3 to 3.6x csv; two sessions an hour apart, 63 three times and 103 to 106 four times, same binaries) | **255** (3.5x) |
| `flat`: the scanner (std.json for the odd line) | 59 to 69 (2.2 to 2.6x) | 170 (2.4x) |
| `shuffled` (the key order differs per line): `jsonx` / `flat` / `json` | 127 / 82 / 668 | |
| `nested` (a nested object and an array before the two keys): `jsonx` / `flat` (falls back) / `json` | 146 to 151 / 716 / 687 | |
| `unicode` (non-ASCII and an escape in every line): `jsonx` / `flat` (falls back) / `json` | 121 / 686 / 668 | |

**Why the Mac's `std.json` is about 10 times slower than the same parser:** `parse` does `region a { let st = alloc_slice[a](2, 0); ... }` on every call. On the Mac a `region` costs about 590 ns, on Linux about 30 ns
(283 against 255). Passing the state in (`parse_with(src, tape, st)`) takes the Mac from 651 to 63 ns. The change is about ten lines and every user of `std.json` that parses per request (the MCP server of `cancho-tools`, which parses a request with it) would collect it.
**This is the "negative scaling on macOS" of `docs/numbers.md` (N3a notes) again, and the same cause: a region per call.**

### 6.2 With threads (the 200,000-line prefix; per-record wall time, ns; lower is better)

Two sweeps on the Mac, the second at load 5 to 11 (other work on the machine); the second is the table, the first run's `json` and `jsona` are in the text.

| threads | Mac `csv` | Mac `flat` | Mac `jsonx` | **Mac `json` (std.json as it is)** | **Mac `jsona`** | Linux `csv` | Linux `json` | Linux `jsonx` |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 28.9 | 64.0 | 105.9 | **646** | 1,210 | 71 | 279 | 258 |
| 2 | 15.3 | 35.4 | 53.6 | 1,201 | 2,545 | 36 | 150 | 130 |
| 4 | 8.0 | 19.9 | 25.0 | **1,435** | 2,655 | 32 | 117 | 95 |
| 6 | | | | | | 22 | 124 | 83 |
| 8 | 4.4 | 7.1 | 14.0 | **1,553** | 3,384 | | | |
| 16 | 2.6 | 7.2 | 9.4 | **1,657** | 2,703 | | | |

On the Mac **`std.json` as it is gets slower with threads: 2.6x slower per record at 16 threads than at 1 in this sweep (1.7x in the first sweep, whose one-thread time was 908 ns under a load of 7: 868, 1,260, 1,326 and 1,549 at 2, 4, 8 and 16); the region-per-line version is 2.2x slower at 16 (2.9x in the first sweep)**.
**`jsonx` and `flat` scale like CSV** (11x and 8.9x at 16 threads; CSV 11x; the first sweep gave 10.2x, 9.9x and 8.5x), because neither touches the allocator per record.
On Linux all of them scale as far as that box does (three physical cores; its known ceiling is 2.4x at 6 threads, `docs/parallel.md` section 5): `json` 2.2x at 6, `jsonx` 3.1x, CSV 3.2x.

### 6.3 Is a scanner of my own worth it?

**Not first.** The measured facts: after the ten-line upstream change `std.json` is within 1.0 to 1.6x of the scanner on the Mac (63 to 106 ns against 59 to 69: between equal and 1.6x slower) and 1.5x slower on Linux (255 against 170). A second parser means a second grammar to get wrong: a scanner that validates by itself
must agree with `std.json` on every line it accepts. The spike was built to see whether that can be made safe, and it can: `flat` answers only for one object of scalar values, written as RFC 8259 writes it, with no backslash, no byte >= 0x80 and no control byte inside a string, and *any* oddity (a nested value, a number out of the grammar,
a duplicate of a wanted key, trailing bytes) sends the line to `std.json`, which alone refuses. `jsonl_check.py` feeds it 25,070 lines (70 edge cases, every single-byte mutation of valid documents, random documents; Python's `json` as the oracle, with `NaN` and `Infinity` refused) and **there are 0 disagreements between `flat`, `std.json` and Python**
(5,636 lines answered by `flat`, 19,434 sent to `std.json`); the one class where `std.json` and Python differ (a lone surrogate escape, which Python accepts and `std.json` refuses) is 35 lines and is listed, not hidden.
The price of a fallback is the whole `std.json` cost: a file whose lines all have a nested value or an escape runs at `std.json`'s speed (716 and 686 ns on the two files that make it fall back, against 687 and 668 for `std.json` alone: the failed scan adds about 4 percent).

So the decision rule is: **R2 is built on `std.json` with one reused tape and, as soon as it exists, `parse_with`; if the Linux gate (J4) fails, the scanner is added behind the same differential test (R2b)**; it never decides a refusal.

### 6.4 Memory

The tape is `3 * nodes` ints: 24 bytes a node; with `--max-json-nodes` 4096 that is **96 KiB**, allocated once (once per thread with `--threads`). `std.json.tape_len(src)` (24 bytes per input byte: 24 MiB for a 1 MiB line) is **not used**: a line with more nodes than the tape
holds makes `parse` answer its `full` code (measured: a document of about 4,100 nodes in a tape of 4,096 answers code 8; nesting of 129 answers code 7, and 128 is accepted), which is `limit.json-too-many-nodes`. The rest is as for CSV: the 64 KiB chunk and the longest line.

### 6.5 The comparison (same 1,000,000 rows, same questions as `scripts/bench.py`; DuckDB 1.5.6 `read_ndjson`, jq 1.7.1, Miller 6.22)

Every contender's answer is compared with Python's before it is timed (`scripts/spikes/bench_jsonl.py`; its output is thrown away); seconds, best of 3 (DuckDB) or 2 (jq, Miller), and peak resident set. Miller is not installed on the Mac, so it is a Linux number only.

| question | DuckDB 1 thread (read_ndjson / read_json_auto / read_csv) | DuckDB 16 threads (read_ndjson) | jq (Mac) | jq (Linux) | Miller (Linux, default threads) |
|---|---|---|---:|---:|---:|
| filter `status = 404 and bytes > 50000` | **0.119** / 0.121 / 0.151 s; 70 MB | 0.069 s; 130 MB | 1.36 s; 2.4 MB | 3.26 s | 11.5 s; 101 MB |
| group-count by status | 0.123 / 0.106 / 0.132 s; 59 MB | 0.061 s | 3.33 s | 8.55 s | 10.1 s |
| group-sum of bytes by status | 0.108 / 0.106 / 0.135 s | 0.061 s | 3.42 s | 9.92 s | 12.2 s |
| cut two columns | 0.251 / 0.237 / 0.276 s; 90 MB | 0.254 s | 1.96 s | 5.01 s | 10.3 s; 105 MB |

DuckDB's JSON reader is nearly as fast as its CSV reader. **`table` with JSON lines does not exist yet, so there is no number for it, only a projection**: `table`'s CSV filter on the Mac is 0.046 s; replacing the per-record read (27 to 29 ns) by `jsonx`'s (63 to 106 ns) adds 0.036 to 0.077 s, **about 0.08 to 0.12 s, which is 0.7 to 1.0x DuckDB's 0.119 s at one thread**; with `std.json` as it is (650 ns) it would be about 0.67 s, 5.6x
slower than DuckDB. On Linux `table`'s CSV filter is 0.124 s; `jsonx` adds about 0.18 s (**about 0.31 s, 2.5x its CSV**), `flat` about 0.10 s (0.22 s, 1.8x). These are the gate lines below, to be replaced by measurements at R2.

## 7. Outputs

`--format` today: `json` (pages), `text` (the shape), `csv`. Candidates, with the decision:

| format | decision | why |
|---|---|---|
| **`jsonl`**: one JSON object per row, `{"col": "value", ...}` | **yes (R3)** | composes (`table ... --format jsonl \| table --input-format jsonl --fields ...`), streams like CSV (written as read, no page, no envelope; a refusal after rows is the stderr verdict and a non-zero exit, exactly as for CSV), and carries a cell that holds newlines, a NUL or non-UTF-8 (`{"b64": "..."}`, as pages do) through a pipe without CSV quoting. **Keys are the output columns in order; two columns of one name (`--select a,a`) are `output.duplicate-column` (exit 2, before any row)**, because an object with a repeated key is the ambiguity section 5.1 refuses on input. **Values:** every cell is a JSON *string*, **except the columns of aggregates (`count`, `sum`, `min`, `max`, `mean`, `distinct`), which are JSON numbers written as the exact text the table writes** (`166893`, `49991975303`, `7.850`), so a count is a count. Open question 2 (the alternative is all strings, for parity with pages) |
| `md`: an aligned markdown table | **not now** (R3b, only on request) | it is for a person reading a terminal or a chat; an agent reads JSON. It also needs a rule for a cell it cannot hold (a newline, `\|`), a buffered page to align, and its own tests and mutants. If built: pipe table, `\|` escaped, a cell with a newline or a control byte refused (`output.not-representable`), the page bounds (`--limit`, `--max-bytes`), a last line saying whether rows remain |
| TSV | **already there**: `--delimiter tab` writes the input's delimiter | |
| a JSON array of objects | no | unbounded in one document; a page is the bounded form |
| NDJSON with a header line, or arrays | no | `jsonl` of objects is what `--fields` reads back |

The reading side of a round trip is a gate: `csv -> jsonl -> csv` is byte-identical to the CSV's normal form (minimal quoting, LF), and `jsonl -> csv -> jsonl` gives, for scalar fields, the same objects with string values.

## 8. Stages and gates

Fixed before the build. Each stage ends with every earlier gate green. **G6** is `docs/numbers.md`'s regression gate for the CSV paths (its third revision is being built on `numbers-n4`; this document uses it as it ends and does not restate it: the requirement is that no CSV cell is slower than the build-to-build noise it measures); a stage that touches nothing
in `reader.cho`, `scan.cho`, `par.cho`, `engine.cho`'s hot functions and the read loops of `table.cho` passes it by construction and says so (the proof is `git diff --stat` on those files, which the stage's PR carries).

| stage | what | gates (pass lines, each able to fail) | fail line |
|---|---|---|---|
| **R0** | the reader contract of section 2 written into `engine.cho`'s header and `docs/architecture.md`; the CSV loops untouched | `scripts/corpus.py` md5 of all plans identical to `main`'s; `git diff --stat` shows no change to the loops; G6 by construction | any md5 differs |
| **U1** (upstream, cancho, not this repo) | `std.json.parse_with` (caller-owned state) | spike's `jsonx` on the benchmark file: <= 130 ns a record on the Mac, and >= 8x at 16 threads | not met |
| **U2** (upstream) | `std.io.read_into` (bulk read of standard input) | `stdin_read`: >= 1 GB/s | not met: R1 ships on `getchar` |
| **R1** | standard input for CSV: `-` / no operand, `io_read` in the authority row and `tools.toml`, `introspect` operand `min: 0` and the `stdin` field, skill text | **S1** every test of `test_select`, `test_filter`, `test_sort`, `test_numbers`, `test_float`, `test_plan` (the 1,800 generated tables) re-run through stdin: the same bytes as the file, refusals equal but for `detail.path: "-"`; **S2** `--threads 1..64` with stdin: the same bytes as `--threads 1`; **S3** `strace`: no socket, no write on disk; **S4** `scripts/manifest.py --check` with `io_read`; **S5** stdin time <= 1.1x the `getchar` floor of `stdin_read` on the same bytes (0.44 s Mac, 0.12 s Linux for the 31.7 MB file); **S6** peak memory flat from 2 MB to 37 MB of stdin; **S7** one 40,000-line record and an unterminated quote at the end through stdin: `parse.csv-unterminated-quote`; **S8** G6 (the CSV file paths untouched: the diff is in `body` only) | any |
| **R2** | JSON lines read: `reader_jsonl.cho` (the line loop, the path lookup, the cell mapping), a range scan for JSON lines, the rules and flags of section 5, `--discover`, the `looks_like` detail | **J1 differential vs Python's `json`**: 1,800 generated JSON-lines files (every kind of value, escapes, non-ASCII, big numbers, nested, duplicates, blank lines, CRLF, BOM, ragged key sets) and generated plans, the engine's reference implementation (`refimpl.py`) fed the *Python-decoded* cells: same rows, same groups, same refusals (rule, line, row, path); **J2 the same plan gives the same bytes as the CSV of the same data**, for every plan the existing generator makes (the rows rendered as JSON lines with `--fields` = the header); **J3 `--threads` 1..16 and `--chunk-bytes` 1, 7, 64, 1000** give the sequential bytes, refusals first in file order; **J4 speed on the 1,000,000-row file, filter question: <= 3.0x the CSV time on the same rows on the same machine (projected 1.8 to 2.7x Mac, 2.5x Linux), and <= 1.2x DuckDB 1 thread `read_ndjson` on the Mac**; **J5 threads: `--threads 4` is >= 0.6x the speed-up of CSV at 4 on the same file, and `--threads 16` is never slower than `--threads 1`** (this fails today on the Mac with `std.json` as it is: 1.7x to 2.6x slower; it needs U1); **J6 memory flat** 2 MB to 37 MB, tape 96 KiB; **J7 300 fuzzed lines per plan: no trap**; **J8 mutants** (below) all killed or proved equivalent; **J9 G6** | any. If J4 fails on Linux with U1 in: R2b |
| **R2b** (conditional) | the flat scanner | `jsonl_check.py` with 1,000,000 generated lines: 0 disagreements with `std.json` and Python; J4 re-measured | |
| **R3** | `--format jsonl` | **O1** the plan generator renders every plan as `jsonl` and the cells equal the CSV output's cells (parsed by Python's `json` and `csv`); **O2** the round trips of section 7; **O3** a cell with a newline, a NUL, a quote, a backslash, non-UTF-8 survives `csv -> jsonl -> csv`; **O4** `output.duplicate-column`; **O5** a refusal after rows: the rows stand, stderr has the verdict, the exit is non-zero (as CSV); **O6** `--select` as jsonl <= 1.3x `--format csv` in time | any |
| **R3b** | `--format md`, if asked | its own gate list then | |

### 8.1 Mutants (`scripts/jsonl_mutants.py`, not run in CI; `test_mutants_apply.py` checks that every one applies)

Each is one defect and each must be killed: a number read through a double (`1.50` becomes `1.5`); a leading `+` or a leading zero accepted; `null` as the text `null`; a missing key as empty under `refuse`; `true` as `1`; the second duplicate wins; a duplicate on an undeclared key refused; an array index off by one; `01` as an index; a dot escape ignored;
an escape not decoded in a cell; the BOM kept; a CR kept in the last cell; a blank line counted as a record; a blank line not counted in `line`; the row of a refusal one low; the offset one off; `--absent empty` ignored; the node bound off by one; depth 129 accepted; a range that starts mid-line (the align missing); a skipped line not parsed (an error in a `--from` skipped line missed); `--from` counting blank lines; standard input spawning threads under `--threads`; a `-` file name read as stdin when it is `./-`; the end of `getchar` read as a NUL byte. (The range check `first line == cur` dropped is an *equivalent* mutant for JSON lines, and is documented as one.)

### 8.2 Differential tests

* `json`: Python's `json.loads` with `parse_constant` refusing `NaN`/`Infinity` and a duplicate-key hook, as the oracle for the verdict and the cells of every generated line (the known class, a lone surrogate, is listed in the test and asserted to be that class only).
* `csv`: Python's `csv` module stays the oracle for CSV, through stdin too.
* the **engine's own flag form**: J2 above (CSV and JSON lines of the same rows give the same bytes), and the `--threads` gates.

### 8.3 Benchmarks

`scripts/bench_jsonl.py` (the spike's, promoted): the four questions on the 1,000,000-row JSON-lines file against DuckDB `read_ndjson`, jq and Miller, answers checked first, best of 5, peak resident set; reported in `docs/benchmarks.html` with the conditions (and, as for every number there, the Linux line from the shared box with its load).

## 9. What cancho lacks (found while designing this)

Language and `std` (cancho): **(1)** no bulk read of standard input (`getchar` only): 40x on the Mac for this tool (U2). **(2)** `std.json.parse` makes a `region` per call: 10x on the Mac (U1). **(3)** no `isatty`, `fstat` or size on `Io`: a terminal cannot be told from a pipe, and a redirected regular file cannot be given `pread` ranges.
**(4)** no way to open fd 0 as a `File` under a label that names it. **(5)** no traits, no closures that capture, no generics over modules: a record reader cannot be a type, which is why each format has its own loop. **(6)** no gzip, zstd or snappy in `std` (`.jsonl.gz` is the common form; `zcat | table -` runs at the `getchar` rate). **(7)** no atomics or channels (a work queue for the ranges). **(8)** `std.json` has a fixed nesting limit (128) and a tape sized by the caller with no way to ask "how many nodes did you need".

The contract package (cancho-tools): `describe.Tool` has no grammar section (so [docs/query.md](query.md) puts the query grammar in the flag's help, where `;` and `|` cannot appear), allows one `schema` string (so the `explain` and `discover` documents join `table.v2`'s `oneOf` by hand in `scripts/schemas.py`), and has no per-subcommand description (so `explain` is a flag, not a subcommand).

`table`: joins; `or`; a computed or renamed column; Parquet; compressed input; typed output beyond the aggregate columns of `jsonl`; grouping and sorting by `:dec` and `:float` (N5); JSON arrays and nested paths past a declared dotted path.

## 10. Open questions for the maintainer, with a recommendation each

1. **No operand = standard input, or require `-`?** Recommendation: **no operand reads standard input** (the family's rule, `tally`/`jsonq`), because MCP's generated schema and the agents' habit want one rule, and a forgotten `FILE` from an agent's subprocess is an immediate empty-input answer.
2. **`--format jsonl`: numbers for aggregate columns, or everything a string like the pages?** Recommendation: **numbers for aggregates** (a count is a count; it is what every consumer of a pipe wants); the pages keep their strings (`table.v2` is shipped).
3. **`--absent` default `refuse` or `empty`?** Recommendation: **`refuse`**, with the repair that runs: one extra call, no silent conflation.
4. **A `looks_like: "jsonl"` hint on the refusals of a CSV whose header starts with `{`: yes, or leave the silent one-column success?** Recommendation: **the hint in refusals only**; the success path does not change.
5. **Ask upstream for `std.json.parse_with` and `std.io.read_into` before R1/R2, or build on what exists?** Recommendation: **ask for both now (they are small and measured), build R1 on `getchar` (honest and correct) and hold R2's Mac gates J4/J5 for U1.** If U1 is refused, R2b (the scanner) becomes mandatory on the Mac.
6. **`ndjson` as an alias of `jsonl`, or `jsonl` only?** Recommendation: **both spellings**, one behaviour (agents will type either).
7. **Ship `--format md`?** Recommendation: **no, until a user asks.**
8. **`--discover`: part of R2 or later?** Recommendation: **part of R2**: without it the required `--fields` has no repair and an agent facing an unknown file has nothing to start from.
