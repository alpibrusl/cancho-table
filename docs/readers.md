# Readers, standard input, JSON lines and more outputs: a design

> **Status: a design, and stage R1 (standard input, section 11) is built.** Sections 0 to 10 were written before the code, as `docs/numbers.md`
> was: the decisions, the numbers that decided them, the gates that must be able to fail, and the questions for the
> maintainer. [`docs/query.md`](query.md) is its twin (the `--query` front end, `--explain`, the surface). The throwaway spikes behind the
> numbers are in `scripts/spikes/` (`jsonl_scan.cho`, `jsonl_scan_x.cho` + `make_jsonx.py`, `stdin_read.cho`, `jsonl_gen.py`, `jsonl_time.py`, `jsonl_check.py`,
> `bench_jsonl.py`); nothing in them is part of the tool.
>
> **How things were measured.** Mac: Apple silicon, 16 cores, macOS, the compiler the tool is pinned to
> (`a4572ea`; since the bulk read of section 11.7 the tool is pinned to `e47d647`, the merge of cancho #363), load average 2.5 to 7 from other work on the machine (the *shapes* below are stable; absolute nanoseconds are good to
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
| stdin speed | **`getchar` was the floor and it was slow: 71 MB/s on the Mac, 264 MB/s on the Linux box, against 2.9 GB/s through a file** (40x and 12x). R1 shipped on it and asked cancho for a bulk read; cancho #363 added `read_bytes` and the tool uses it (11.7): a pipe now costs what the file does, within the noise | measured, section 3.4 (the finding) and 11.5 (after); not hidden behind `/dev/stdin` |
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
`FILE` is then an immediate, ordinary empty-input answer, below) and because MCP's schema generator wants one rule per operand. Decision 1 of section 10 (decided) settles for the maintainer's call.

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

The same `lines` reader (64 KiB chunk, line capped at `--max-line-bytes`), fed by the contract's `lines.fill_stdin`, which already exists (R1 as first built; the built tool refills from `bulk.fill_stdin` instead, section 11.7). Memory is what it is for a file
(the spike, which reads standard input in 64 KiB chunks and keeps nothing, peaks at 1.4 MB).

### 3.4 What it costs: the finding

*(Measured before the bulk read existed. What it costs now is in 11.5.)*

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
starts with a brace is legal). Decision 4 of section 10 (decided).

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
| **`jsonl`**: one JSON object per row, `{"col": "value", ...}` | **yes (R3)** | composes (`table ... --format jsonl \| table --input-format jsonl --fields ...`), streams like CSV (written as read, no page, no envelope; a refusal after rows is the stderr verdict and a non-zero exit, exactly as for CSV), and carries a cell that holds newlines, a NUL or non-UTF-8 (`{"b64": "..."}`, as pages do) through a pipe without CSV quoting. **Keys are the output columns in order; two columns of one name (`--select a,a`) are `output.duplicate-column` (exit 2, before any row)**, because an object with a repeated key is the ambiguity section 5.1 refuses on input. **Values:** every cell is a JSON *string*, **except the columns of aggregates (`count`, `sum`, `min`, `max`, `mean`, `distinct`), which are JSON numbers written as the exact text the table writes** (`166893`, `49991975303`, `7.850`), so a count is a count. Decision 2 of section 10 (decided; the alternative, all strings, was not taken) |
| `md`: an aligned markdown table | **not now** (R3b, only on request) | it is for a person reading a terminal or a chat; an agent reads JSON. It also needs a rule for a cell it cannot hold (a newline, `\|`), a buffered page to align, and its own tests and mutants. If built: pipe table, `\|` escaped, a cell with a newline or a control byte refused (`output.not-representable`), the page bounds (`--limit`, `--max-bytes`), a last line saying whether rows remain |
| TSV | **already there**: `--delimiter tab` writes the input's delimiter | |
| a JSON array of objects | no | unbounded in one document; a page is the bounded form |
| NDJSON with a header line, or arrays | no | `jsonl` of objects is what `--fields` reads back |

The reading side of a round trip is a gate: `csv -> jsonl -> csv` is byte-identical to the CSV's normal form (minimal quoting, LF), and `jsonl -> csv -> jsonl` gives, for scalar fields, the same objects with string values.

## 8. Stages and gates

Fixed before the build. Each stage ends with every earlier gate green. **G6** is `docs/numbers.md`'s regression gate for the CSV paths (its third revision, with its instruction-count form `gate_regress.py --counter`, bound 1.01, on both machines; this document does not restate it: the requirement is that no CSV cell executes more instructions than `main`'s build beyond that bound); a stage that touches nothing
in `reader.cho`, `scan.cho`, `par.cho`, `engine.cho`'s hot functions and the read loops of `table.cho` passes it by construction and says so (the proof is `git diff --stat` on those files, which the stage's PR carries).

| stage | what | gates (pass lines, each able to fail) | fail line |
|---|---|---|---|
| **R0** | the reader contract of section 2 written into `engine.cho`'s header and `docs/architecture.md`; the CSV loops untouched | `scripts/corpus.py` md5 of all plans identical to `main`'s; `git diff --stat` shows no change to the loops; G6 by construction | any md5 differs |
| **U1** (upstream, cancho, not this repo) | `std.json.parse_with` (caller-owned state) | spike's `jsonx` on the benchmark file: <= 130 ns a record on the Mac, and >= 8x at 16 threads | not met |
| **U2** (upstream) | `std.io.read_into` (bulk read of standard input) | `stdin_read`: >= 1 GB/s | met by a different name: `read_bytes`, a builtin of edition 9 (cancho #363, merged): `stdin_read bulk` 2.2 GB/s on the Mac and 3.3 GB/s on gram (11.5). R1 first shipped on `getchar` |
| **R1** (built: section 11) | standard input for CSV: `-` / no operand, `io_read` in the authority row and `tools.toml`, `introspect` operand `min: 0` and the `stdin` field, skill text | **S1** every test of `test_select`, `test_filter`, `test_sort`, `test_numbers`, `test_float`, `test_plan` (the 1,800 generated tables) re-run through stdin: the same bytes as the file, refusals equal but for `detail.path: "-"`; **S2** `--threads 1..64` with stdin: the same bytes as `--threads 1`; **S3** `strace`: no socket, no write on disk; **S4** `scripts/manifest.py --check` with `io_read`; **S5** stdin time <= 1.1x the `getchar` floor of `stdin_read` on the same bytes (0.44 s Mac, 0.12 s Linux for the 31.7 MB file; with the bulk read the gate is a pipe within 1.1x of the file, 11.7); **S6** peak memory flat from 2 MB to 37 MB of stdin; **S7** one 40,000-line record and an unterminated quote at the end through stdin: `parse.csv-unterminated-quote`; **S8** G6 (the CSV file paths untouched: the diff is in `body` only) | any |
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

Language and `std` (cancho): **(1)** ~~no bulk read of standard input (`getchar` only): 40x on the Mac for this tool (U2)~~: `read_bytes` (cancho #363, edition 9) is it; the tool uses it since 11.7. **(2)** `std.json.parse` makes a `region` per call: 10x on the Mac (U1). **(3)** no `isatty`, `fstat` or size on `Io`: a terminal cannot be told from a pipe, and a redirected regular file cannot be given `pread` ranges.
**(4)** no way to open fd 0 as a `File` under a label that names it. **(5)** no traits, no closures that capture, no generics over modules: a record reader cannot be a type, which is why each format has its own loop. **(6)** no gzip, zstd or snappy in `std` (`.jsonl.gz` is the common form; `zcat | table -` runs at the `getchar` rate). **(7)** no atomics or channels (a work queue for the ranges). **(8)** `std.json` has a fixed nesting limit (128) and a tape sized by the caller with no way to ask "how many nodes did you need".

The contract package (cancho-tools): `describe.Tool` has no grammar section (so [docs/query.md](query.md) puts the query grammar in the flag's help, where `;` and `|` cannot appear), allows one `schema` string (so the `explain` and `discover` documents join `table.v2`'s `oneOf` by hand in `scripts/schemas.py`), and has no per-subcommand description (so `explain` is a flag, not a subcommand).

`table`: joins; `or`; a computed or renamed column; Parquet; compressed input; typed output beyond the aggregate columns of `jsonl`; grouping and sorting by `:dec` and `:float` are built (N5), the type report is not (N6); JSON arrays and nested paths past a declared dotted path.

## 10. Decisions of the maintainer (2026-10-07: "go with the recommendations")

Every question that was open here is **decided**, on 2026-10-07, **accepted by the maintainer as recommended**. Nothing is open in this document. What each decision means for the build is the stage it names; the approvals that the stages needed are at the end.

1. **No operand = standard input, or require `-`?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **no operand reads standard input, and `-` is accepted too** (the family's rule, `tally`/`jsonq`), because MCP's generated schema and the agents' habit want one rule, and a forgotten `FILE` from an agent's subprocess is an immediate empty-input answer.
2. **`--format jsonl`: numbers for aggregate columns, or everything a string like the pages?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **numbers for aggregates** (a count is a count; it is what every consumer of a pipe wants); the pages keep their strings (`table.v2` is shipped).
3. **`--absent` default `refuse` or `empty`?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **`refuse`**, with the repair that runs: one extra call, no silent conflation.
4. **A `looks_like: "jsonl"` hint on the refusals of a CSV whose header starts with `{`: yes, or leave the silent one-column success?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **the hint in refusals only**; the success path does not change.
5. **Ask upstream for `std.json.parse_with` and `std.io.read_into` before R1/R2, or build on what exists?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **ask for both now (they are small and measured), build R1 on `getchar` (honest and correct) and hold R2's Mac gates J4/J5 for U1.** If U1 is refused, R2b (the scanner) becomes mandatory on the Mac.
6. **`ndjson` as an alias of `jsonl`, or `jsonl` only?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **both spellings**, one behaviour (agents will type either).
7. **Ship `--format md`?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **no, until a user asks.**
8. **`--discover`: part of R2 or later?** DECIDED 2026-10-07, accepted by the maintainer as recommended: **part of R2**: without it the required `--fields` has no repair and an agent facing an unknown file has nothing to start from.

**Approvals the stages needed, also decided on 2026-10-07 (accepted by the maintainer):** (a) **`io_read` in the `[table]` ceiling of `tools.toml`** is approved for the standard-input stage (R1); the authority row grows by `io_read` there and nowhere before. (b) The upstream pieces (U1 `std.json.parse_with`, U2 `std.io.read_into`) are being added to cancho by a separate session: R1 is shipped on `getchar` meanwhile (correct, and measured as slow in 3.4), R2's Mac gates J4 and J5 wait for U1, and if U1 is refused R2b (the scanner) becomes mandatory on the Mac. (c) `--format md` is not shipped.


## 11. R1 as built: standard input for CSV (2026-10-12)

Stage R1 of section 8, built as `table -` and `table` with no operand, with `io_read` in the authority row (the approval of section 10 (a), and nowhere else). It was first built on `getchar` (compiler `a4572ea`); the branch is now pinned to cancho `e47d647`, the merge of PR #363, and reads standard input with `read_bytes` (11.7). Where a figure below is of the `getchar` build it says so. What was not measured is in 11.6.

### 11.1 What it does

| situation | what happens |
|---|---|
| `table [flags] -` and `table [flags]` | read standard input, to the end of the input or to the first bound of the plan (`--max-rows`, the page, `--max-line-bytes`), in one pass, in reads of 512 bytes doubling to 64 KiB (11.7). Memory is the file's: peak resident set 1,936 KB on 2 MB and on 37 MB of input (`test_stdin.Bounded`, Mac). |
| the answer | the file's answer byte for byte, for every plan and every refusal, but for the one place the answer names the input: `detail.path` is `-`, and a `retry` repair repeats the invocation as it was given, so it ends with `-` or with no operand. Checked: 11.4, S1. |
| `./-` | a file called `-`. `- -`, `- FILE` and `FILE -` are `args.too-many-operands`, as two files are. |
| empty input | the empty file's answer: `headers: []`, `column_count: 0`, success. |
| `--threads N` | one core, the same bytes, no refusal (section 3.2). The loop that reads a pipe has no partitioned read in it. `--chunk-bytes` and `--parallel-min-bytes` are read and unused. |
| `--root` | not used for standard input (still checked as a value, so a malformed one is still refused as it is for a file). |
| `--from N` | skips N records in the one pass; a page that ends with rows left carries `next.from`, which needs the same bytes again. |
| a producer that never ends | bounded by `--max-rows` (default 10,000,000), by the page, and, with no newline, by the largest `--max-line-bytes` there is (16 MiB): see 11.2 (3). |
| `args.missing-operand` | no longer emitted (no operand is standard input), so it is gone from the rules `introspect` lists (50 to 49). |

### 11.2 Where it differs from sections 3 and 8

1. **`--threads` on a pipe has no tag on the wire.** Section 3.2 says the fallback is reported as `threads_effective: 1` by `--explain`; the document of `table.v2` has no member to put a note in (`meta` is `{"version"}` and the schema closes the object), so a run that falls back is silent and documented (the flag's help says it, `introspect` carries it). It is not a refusal; `test_stdin.Threads` shows the bytes are the one thread's for 1 to 64 threads and chunk sizes 1, 7 and 4096, and, on Linux, that no thread is started (`strace -e clone`). **`--explain` (Q2, `docs/query.md` section 15) is the place that says it**: `work.threads` is `{"requested": 4, "effective": 1, "reason": "standard input is read on one core"}`, and `notes` has a sentence of its own for it (`--threads has no effect on standard input: it is read on one core, in blocks`), with no rule behind it. `test_explain.Threads` holds that sentence, and on Linux that a run starts a thread exactly when its explain says `effective` is more than 1.
2. **The `File` that `read_file` is typed with is `/dev/null`.** `read_file` takes a `File`, and the language has no way to make one without a path. Standard input gets an empty file opened read-only and never read for its bytes (the one use is in 3). It adds one `open` of `/dev/null` to a run that reads standard input, and no new label (`fs_read("")` and `file_read` are already in the row). Where it cannot be opened the input is refused with the rule of the error, `io.*`, naming `-` (`test_stdin.NoTrap`, made to fail with `sandbox-exec` on macOS and `strace`'s error injection on Linux). It is not `/dev/stdin`: the bytes come from `read_bytes` (11.7), so the row says `io_read` for them.
3. **A line with no end is stopped.** `lines` answers a line that is too long only when the line ends, so a producer with no newline would be read forever (found by the first test of "a producer that never ends": the process ran at 100 percent of a core for minutes). Past the largest cap there is (16,777,216 bytes) the read stops and `lines` is told the input has ended (by reading the empty file of 2), and answers the line as `limit.line-too-long` with the length so far. **The one difference from a file:** for a line longer than 16 MiB, `detail.longest` of a file is its whole length and of a pipe is the length when the read stopped (the end of the chunk past 16 MiB, chunks of up to 64 KiB). `test_stdin.Bounded.test_a_line_past_the_ceiling_is_the_one_difference_from_a_file` states it.
4. **A read that fails is a refusal** (with `read_bytes`, 11.7). The `getchar` build answered -1 for a read error as for the end, so a directory or a write-only descriptor on descriptor 0 gave the empty table's answer, success; `read_bytes` answers `Failed(errno)` and the answer is `io.is-a-directory` (21), `io.read-failed` (9, 35 and the rest), naming `-`. **One case stays the empty input: a closed descriptor 0** (11.7).
5. **The MCP server is unchanged.** `table` now has `io_read`, and `scripts/mcp.py` refused any tool that had it. It now accepts one whose one operand is optional (the operand is standard input when absent) and keeps the operand required of a caller: `generated/mcp/tools.cho` differs from `main`'s only in the text of the tool's description and the operand's help, `file|1|1` and `stdin` are as they were, `manifests/mcp.authority.json` is byte for byte `main`'s. A call that names the file `-` reaches the tool's standard input, which the server makes empty. Giving an agent a way to send the bytes in the call (the `stdin` property) was not R1; it is built, with the design and the gates, in [docs/mcp.md](mcp.md) section 12.
6. **The read loop for a pipe is a generated copy of the one for a file** (11.3).

### 11.3 Why `read_stream` is a copy of `read_file`, and how it is kept one

The first build put the choice in the loop. It failed the gate it had to pass: **every one-thread read of a file cost more instructions** (x86-64, `gate_regress.py --counter`, `cut status,bytes`, 1,000,000 rows, main 1,192.4 M). Every formulation measured, with the loop otherwise unchanged:

| `read_file`'s loop, formulation (each a scratch build; `perf stat -e instructions:u`, core 2 of gram, `cut status,bytes --format csv`, `build/bench/data.csv`; main: 1,192.4 M) | M instr | against main |
|---|---:|---:|
| the choice inline: `if len(full) == 0 { fill_stdin } else { fill_file }` | 1,225.8 | +33.4 (+2.8 percent) |
| the first build: `stdin` a parameter, the stop of a line with no end inline | 1,211.8 | +19.4 (+1.6) |
| a function of the loop, taking `full`, answering the chunk | 1,206.9 | +14.5 (+1.2) |
| the same, answering the chunk and whether to go on | 1,205.9 | +13.5 (+1.1) |
| the same, the answer not used | 1,201.9 | +9.5 (+0.8) |
| the choice a flag of the plan, read in a function | 1,219.9 | +27.5 (+2.3) |
| the choice a slot of the tally, set before the loop | 1,220.9 | +28.5 (+2.4) |
| **nothing in the loop but `len(full) > 0` in the condition that starts the partitioned read** | 1,207.9 | +15.5 (+1.3) |
| the same, without that condition (everything else of this change in) | 1,192.8 | +0.4 (the longer help text of two flags, read at start) |

The loop of `read_file` is 26 KB of machine code (`lexs_read_file`, 0x6601 bytes on x86-64) that the compiler lays out and allocates anew at any change of its text; with the text unchanged it costs the same, to the instruction (a build of `main` with the `Inputs` of Q0, `settle` and `flags.cho` moved out, and nothing in the loop: 1,192.4 M, as `main`). The history of this repository says the same twice (`docs/backlog.md`, "A third case in the read's loop"; section R0 of `docs/numbers.md`). So the file loop is left exactly as it was and the loop for a pipe is made from it: `scripts/stream.py` copies `read_file` into `read_stream` (marked region of `tools/table/table.cho`) with three edits, and `--check` (CI, and `test_stdin.StreamLoop`, which also edits either loop alone and sees the check fail) keeps it so: the name and the row (`io_read` instead of `conc`, `fs_read`, `dir_read`); the refill of the chunk from `bulk.fill_stdin` (`lines.fill_stdin` in the `getchar` build) (and the stop of 3 above); the partitioned read cut out. A change to the loop is made in `read_file` and written here. The mutation scripts count a pattern outside that region (`scripts/mutlib.py`), so they still mutate the file loop; `scripts/stdin_mutants.py` mutates the copy (`table.cho#stream`).

Two moves were needed to keep `table.cho` under 2,000 lines (it was 2,068 on `main`, 1,551 now): the lists of names to `tools/table/flags.cho` (verbatim), and `Counts`, `explain` and the helpers the two loops finish with to `tools/table/finish.cho`, with the flag table in `tools/table/spec.cho` (a one-line `flag_table` in `table.cho` calls it). **The call of `explain` is as it was**: a first move gave it the flag table as an argument, and `read_file` then grew 52 bytes and every one-thread read 10 instructions a row on arm64 (+0.8 percent, found by the same gate on the Mac, not on gram). The sizes of `lexs_read_file` are checked equal to `main`'s on both machines: 0x6601 and 18,128 bytes. No file is over 2,000 lines but `agg.cho` (2,127, `main`'s).

### 11.4 The gates of section 8 and where they are

| gate | what | where | result |
|---|---|---|---|
| S1 | every test of the suite re-run through standard input (named `-`, and with no operand): the same bytes as the file, refusals included | `TABLE_STDIN_DIFF=1` on the whole suite (`harness.run` and `run_argv`); and `test_stdin.Differential` (17 plans on 19 texts, the bare and the redirected forms), `OneByteAtATime`, `ChunkEdge` (every cut of 7 features at 64 KiB and 128 KiB), `NoTrap.test_random_bytes` (150 inputs x 7 plans) | Mac, final tree of the `getchar` build: the whole suite, 415 tests OK (4 skipped; bulk-read build: 422, 11.8), with `TABLE_STDIN_DIFF=1`: 66,293 runs of a file also run through standard input, 132,586 comparisons, all equal. gram, final tree of the `getchar` build: 415 tests OK (1 skipped; bulk-read build: 422, 11.8), the same 66,293 / 132,586, all equal |
| S2 | `--threads 1..64`, chunks 1, 7, 4096: the same bytes | `test_stdin.Threads`, with the file's answer beside it | passes |
| S3 | `strace`: no socket, no write on disk; a pipe reads descriptor 0 and a file never does | `test_stdin.Authority`, `test_authority` | gram (Linux): `test_stdin.Authority` and `test_authority.test_strace_no_network_no_writes_to_disk` pass (no socket, no write on disk; `read(0, ` for a pipe, none for a file). On the Mac `strace` does not exist and they are skipped |
| S4 | `manifest.py --check` with `io_read`; the row grew by `io_read` and nothing else | `test_stdin.Authority.test_the_row_grew_by_io_read_and_nothing_else`; `manifests/table.authority.json`: the label, the effect, `functions` 631 to 636 on the `getchar` build, 638 with `bulk.cho` (11.8), and one more name in `pure` (`flag_table`, the wrapper and the module's) | passes. The mutant: `tools.toml` without `io_read` makes `manifest.py --check --ceiling` fail, "io_read is not within the ceiling" (`test_stdin.Authority.test_the_ceiling_refuses_the_source_without_it`; and `scripts/stdin_mutants.py`, "the ceiling forgets io_read") |
| S5 | stdin time within 1.1 of the `getchar` floor of `stdin_read.cho` (for the `getchar` build); for the bulk read, a pipe within 1.1 of the file, in instructions (`test_stdin.Bulk`) and in time | `scripts/bench_stdin.py --floor` | `getchar` build: Mac 1.095 (met), gram 1.370 (not met). Bulk read: instructions stdin/file 0.995 (Mac, 5.6 MB), pipe/file in time 1.063 (Mac) and 1.010 (gram), 11.5 and 11.7 |
| S6 | peak memory flat from 2 MB to 37 MB | `test_stdin.Bounded.test_memory_does_not_grow_with_the_input` | 1,936 KB and 1,936 KB (Mac) |
| S7 | one 40,000-line record, and an unterminated quote at the end | `test_stdin.ChunkEdge` | `parse.csv-unterminated-quote`, equal to the file's |
| S8 / G6 | the file paths are not slower (`gate_regress.py --counter`, 42 cells, 3 builds a side, bound 1.01, against `main` 134b1a7; getchar build; bulk build: 11.8, against 60f8ef1) and the corpus is `main`'s | Mac (arm64, macOS 25.2): 42 cells, ratios 0.9998 to 1.0004, counts differ from main by -1.0 to +0.6 M of 1.5 to 5.5 G. gram (x86-64, `perf stat -e instructions:u`, cores 0-5, niced, perf_event_paranoid 2): 1.0001 to 1.0004, +0.4 to +0.6 M (the longer help text of two flags, read at start). Both PASS at 1.01. Corpus (`scripts/corpus.py`, 166 plans): md5 of the 166 lines `fccb01b66dd7518d74611219a6c5aa29` for main and for the build on both machines. `lexs_read_file` is 18,128 bytes (Mac) and 0x6601 bytes (gram) in both builds |

Mutants of the `getchar` build (`scripts/stdin_mutants.py`, 18, and two in `scripts/mcp_mutants.py`): gram, then: `stdin_mutants.py` 17 of 17 killed; the two of `mcp_mutants.py` 2 of 2; `inputs_mutants.py` 11 of 11; the 68 retargeted ones 67 killed, 1 survivor ("the report help says the suggestion is applied", also a survivor on main). The results for the bulk-read build are in 11.8.

### 11.5 What it costs (measured, `scripts/bench_stdin.py`; the file is `scripts/bench.py`'s, 1,000,000 rows, 31,667,311 bytes)

Re-measured on the bulk-read build (head of PR #39, `read_bytes`, reads of 512 bytes doubling to 64 KiB). Each figure is the minimum of 7 interleaved runs, output to `/dev/null`, every answer checked against the generator's first. **Conditions:** Mac (arm64, 16 cores, macOS 25.2, shared with other sessions) load average 4.4 / 6.1 / 5.5 before and 6.8 / 6.6 / 5.7 after; `gram` (x86-64, 16 cores, scripts on cores 0 to 5, niced) 1.6 / 3.5 / 3.5 before and 2.3 / 3.3 / 3.4 after (two stray `table -` processes from an earlier session, 100 percent of a core each for six hours, were found and killed before these runs; an earlier attempt, run while they and another job's builds were running, read `table FILE` at 0.073 s and is not used). csvtk 0.38.0 and DuckDB 1.5.6, as before; DuckDB cannot sniff a pipe, so its columns are declared and the bytes `COPY`ed into a table, in its time. The floors are `scripts/spikes/stdin_read.cho` built with the pinned compiler (`cancho build --std`): `getchar` and `read_bytes` into 64 KiB chunks, counting bytes and newlines, keeping nothing.

| question, seconds | Mac `table FILE` | Mac `cat \| table -` | Mac `cat \| csvtk -j 1` | Mac `cat \| duckdb` | gram `table FILE` | gram `cat \| table -` | gram `cat \| csvtk -j 1` | gram `cat \| duckdb` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| the row count | 0.0301 | 0.0320 | 0.1897 | 0.2177 (1 thread 0.2188) | 0.0291 | 0.0294 | 0.3053 | 0.2340 (1 thread 0.2359) |
| `--select status,bytes` as csv | 0.0518 | 0.0524 | 0.1900 | | 0.0628 | 0.0645 | 0.3074 | |
| `--where 'status=404 and bytes:int>50000'` as csv | 0.0463 | 0.0491 | 2.6617 (`filter2`) | 0.2121 | 0.0679 | 0.0638 | 5.2953 (`filter2`) | 0.2434 |
| `--group status` as csv | 0.0525 | 0.0550 | 0.1900 | 0.2208 | 0.0872 | 0.0903 | 0.4128 | 0.3105 |
| the `getchar` floor | | 0.4497 | | | | 0.0679 | | |
| the `read_bytes` floor | | 0.0143 | | | | 0.0095 | | |
| `table < FILE` (a redirect), row count | | 0.0311 | | | | 0.0294 | | |

* **A pipe now costs what the file does.** Pipe over file, same question: Mac 1.063 (row count), 1.012 (cut), 1.061 (filter), 1.048 (groups); gram 1.010, 1.027, 0.940 (the pipe was faster than the file in that cell: noise, the medians are 0.0820 and 0.0945), 1.036. The row count through a pipe is 990 MB/s on the Mac and 1,076 MB/s on gram (script output); the file is 1,054 and 1,088.
* **Before and after** (the row count through a pipe): the `getchar` build measured at 0.4293 s (Mac) and 0.0944 s (gram) in the first measurement of this PR (load 2.7 to 3.3 and 1.1 to 1.8), and at 0.4499 s (Mac, load 6.8 to 7.3) and 0.1293 s (gram, load 2.4 to 3.2, a noisier run: its `table FILE` read 0.0407) in the same session as the figures above: **13 to 14 times faster on the Mac (0.0320 against 0.4293 or 0.4499), 3.2 to 4.4 times on gram** (0.0294 against 0.0944 or 0.1293).
* **Instructions** (`--select status,bytes --format csv`, the same 1,000,000 rows, standard input redirected from the file): gram (`perf stat -e instructions:u`, core 2): the file 1,192.8 M, standard input 1,197.0 M (the `getchar` build: 2,600.3 M); Mac (`/usr/bin/time -l`): the file 1,220.5 M, standard input 1,215.0 M (the `getchar` build: 10,821.2 M). On 150,000 rows (`test_stdin.Bulk`, 5.6 MB) the Mac reads 217.2 M and 216.3 M, ratio 0.995. The fill costs no instructions worth counting; the reads are syscalls (kernel time is not in the count).
* **Against csvtk and DuckDB through a pipe, `table` is now the fastest of the tools measured on every question on both machines (DuckDB was not run on the `--select` question)**: the row count 5.9 times csvtk and 6.8 times DuckDB on the Mac (0.0320 against 0.1897 and 0.2177), 10.4 and 8.0 on gram; the filter 54 times csvtk's `filter2` and 4.3 times DuckDB on the Mac, 83 and 3.8 on gram. On the Mac, with the `getchar` build, on the row count, csvtk and DuckDB had been 2.0 to 2.4 times faster (0.4293 or 0.4499 s against 0.1897 and 0.2177 s; the other questions were not measured on the `getchar` build).
* **S5 as it was written (stdin time at most 1.1 times the `getchar` floor) is moot**: `table < FILE` over the `getchar` floor is 0.069 (Mac) and 0.434 (gram), over the `read_bytes` floor 2.171 and 3.103; a floor that only counts bytes is a fraction of a query. The gate that says what S5 was for is a pipe within 1.1 times of the file: 1.063 and 1.010 above, and, in instructions, `test_stdin.Bulk` (ratio under 1.15).
* The floors themselves: `read_bytes` into 64 KiB chunks reads the file at 2.2 GB/s (Mac) and 3.3 GB/s (gram); `cat > /dev/null` is 0.0120 s and 0.0026 s (script output, `scripts/bench_stdin.py`, the `cat | cat > /dev/null` row; not in the PR #39 body).

### 11.6 Not measured, not done

* csvtk's and DuckDB's peak memory through a pipe; any machine but these two; other chunk-size schedules than the one shipped (512 doubling, against 4,096 doubling and a fixed 64 KiB: 11.7, three producers at one rate each, three trials).
* A full gram re-run of the suite at the head after rebuilding with `CANCHO_REV` was not done; only `test_mcp` and the mcp mutants were re-run.
* `--explain` is built (Q2); the `stdin` property of the MCP server is built too (docs/mcp.md section 12).
* The latency of a producer that writes a page and stops (11.7: nothing in the language can help it).
* `args.missing-operand` is gone from the rules; `--query`'s `from stdin`, the `stdin` property of the MCP server, JSON lines (R2) and the terminal case (a person at a keyboard with no file: `table --where x` waits for it; cancho cannot ask whether descriptor 0 is a terminal, section 9 (3)) are not R1.
* The failure of `open_read(fs, "/dev/null")` is tested with the sandbox on macOS and `strace`'s error injection on Linux; a system without `/dev/null` was not tried.
* The partitioned read cannot be started for a pipe because the loop for a pipe has none: the test that counts the clone calls runs on Linux only, and on the Mac the same-bytes check is all there is.
* A descriptor that fails after it has delivered bytes was made with a pipe that does not wait (`EAGAIN`, 35 on macOS, 11 on Linux) and with nothing else; a disk that fails (`EIO`) was not.
* The instruction-count test (`test_stdin.Bulk`) needs `/usr/bin/time -l` (macOS) or `perf` with `perf_event_paranoid` low enough (Linux): it is skipped on CI, where `perf` is not installed. The `strace` test of the reads runs there.

### 11.7 The bulk read (cancho PR #363, merged as `e47d647`)

**What changed.** `tools/table/bulk.cho` (edition 9, the only file of the tool that is) refills the reader with `read_bytes[&i,&b](io, into) -> [io_read] Read`, a builtin of edition 9 under the label the row already has: `Got(n)`, `End` or `Failed(errno)`, the three ways of `file_read`. The contract's `lines.fill_stdin` is a `getchar` loop and `lines` is a module of cancho-tools (edition 5), so the refill that needs the reader's fields lives here, a copy of `lines.fill_file` with the call replaced, as `scan.fill_at` is one with a read at an offset. `scripts/stream.py` writes `bulk.fill_stdin` into `read_stream` (the generator changed, the `--check` is kept, `StreamLoop` tests it); `read_file` is not touched. The compiler pin of `cancho.toml` is the merge commit `e47d647353c4e35929f312cf07b0225c88b48b7c`, which is cancho `main` at the time (the repository pins a commit; the CI builds that one). An edition-9 file is the only change the pin needed: the other files stay at edition 5 and call it (`Split` of seven fields is still edition 5's).

**What the pin alone did** (before any use of `read_bytes`; the branch with only `cancho.toml` and the regenerated files): corpus md5 `fccb01b66dd7518d74611219a6c5aa29` (166 plans), main's, both machines; `gate_regress.py --counter`, 42 cells, 3 builds a side, bound 1.01, against main `60f8ef1`: Mac 0.999 to 1.000, gram 1.000 to 1.000, PASS both; against the same sources built with `a4572ea`: 1.000 to 1.000 on both. The suite: 415 tests OK on the Mac. The generated files changed in the compiler string only and both authority manifests gained a `never_touches` list (the negative half of the report, cancho #408): labels, effects and `bounded` are as before.

**What it does to the file path.** Nothing: the same gate on the final build (3 builds a side, 42 cells, bound 1.01): Mac 0.999 to 1.001 (-3.1 to +2.8 M instructions of 1.5 to 5.5 G), gram 1.000 (+0.4 to +0.6 M), PASS both; corpus md5 the same; `lexs_read_file` 18,128 bytes on the Mac, as main's.

**Semantics, and what was decided.**

* **`Got(n)` fills the buffer unless the input ends.** A producer that writes a little and waits is waited for until the buffer fills or the input ends. The `getchar` build had the same property (it filled 64 KiB a byte at a time before the reader saw a line). What a reader sees is measured (an ad-hoc producer script, not committed; Mac; answer time after the last row the page needs was written, `--limit 5 --select a`, lines of about 40 bytes; the median of three trials, two sets of runs): a producer at 4 KB/s: fixed 64 KiB 20.0 and 23.1 s (the `getchar` build 20.0 s), reads of 4,096 doubling 1.3 and 1.5 s, **512 doubling 0.13 s**; at 40 KB/s: fixed 64 KiB 2.07 s (`getchar` 2.06 s), 4,096 doubling 0.13 s. So the first read asks for 512 bytes and each read for as many as have been read (512, 512, 1,024, ... 65,536, then 65,536): the wait is at most about what the page already read, and the whole cost is nine reads in the first 64 KiB. The gate is `test_stdin.Bulk.test_a_page_is_answered_soon_after_its_last_row_from_a_producer_that_trickles` (3 rows at 8 KB/s: 0.08 s; a fixed 64 KiB buffer fails it by waiting for the producer's end). **A producer that stops is waited for** until it writes again or ends: 3 s of stall gave 3.0 s with all three schedules and with `getchar`. Nothing in the language answers "what has arrived" (cancho #363 lists a `read_some` as not done).
* **`Failed(errno)` is a refusal.** The reader's `failed` carries it to `finish.explain`, which gives the `io.*` rule of the errno (the catalog of `introspect` and `docs/refusals.md` are unchanged: `io.read-failed`, `io.is-a-directory`, `io.permission-denied` were already there) with `detail.path` `-` and `detail.errno`. Checked: a directory (21, `io.is-a-directory`), a write-only descriptor (9, `io.read-failed`), and a pipe that does not wait, empty or after a record (`EAGAIN`, `io.read-failed`; the rows read before it are in `data`), as JSON and as csv (a sentence on standard error). The message and hint come from the contract's `fail.io_error` and say "the path is a directory / name a file inside it" for a descriptor: not changed here (it is cancho-tools').
* **A closed descriptor 0 is still the empty input**, and `read_bytes` alone would say `Failed(9)`. The empty `File` that `read_file` is typed with (`/dev/null`) is opened before standard input is read, and an open takes the lowest free descriptor, 0: `strace` on gram shows `openat(AT_FDCWD, "/dev/null", ...) = 0` and then `read(0, "", 4096) = 0`. Tested as such (`test_nothing_to_read_is_not_a_failure_to_read`, `test_strace_a_closed_descriptor_is_the_stand_in_that_is_read`). The fix is upstream (a `File` for descriptor 0, section 9 (4)) or a restructure that reads before opening; neither is here.
* **Mixed with `getchar`**: not used any more by the tool; the MCP server (`server/mcp.cho`) still reads its own standard input with `getchar`.
* **Chunk edges** are the sums of the reads: 512, 1,024, 2,048, 4,096, ... 65,536, 131,072, ...; `ChunkEdge` cuts every feature (CRLF, doubled and closing quotes, a quoted CRLF, a quote then a newline, an unterminated quote, a bad quote) at each cut inside it at 512, 1,024, 4,096, 32,768, 65,536, 131,072 and 196,608 bytes.

**Gates, bulk read** (written first; on the `getchar` build they fail: the Mac read standard input at 8.9 times the file's instructions): `test_stdin.Bulk`: the instruction ratio of standard input to the file under 1.15 (0.995), the reads of descriptor 0 under `strace` fewer than a quarter of one per 4 KiB with a request of at least 64 KiB (Linux), the trickle latency, the errno refusals, the closed descriptor, and the 5.6 MB file through a pipe, redirected and in pieces 2 ms apart that end at, before and after the edges (511, 512, 513, ...) and at 30 random places, for five plans.

**Differential** (both machines): `TABLE_STDIN_DIFF=1` over the whole suite (every test that runs a file also runs it through standard input, named `-` and bare), the pipe at once, one byte at a time, 2 ms-spaced pieces, the 64 KiB edges inside quotes and CRLF, a closed descriptor, a directory, a write-only descriptor, `/dev/null`, a pipe that does not wait.

### 11.8 The gates of the bulk-read build, and where they stand (head of PR #39)

| gate | result |
|---|---|
| corpus md5 of the 166 plans, file path | `fccb01b66dd7518d74611219a6c5aa29`, main's, Mac and gram |
| `gate_regress.py --counter`, 42 cells, 3 builds a side, bound 1.01, against main `60f8ef1` | Mac 0.999 to 1.001, gram 1.000 (+0.4 to +0.6 M instructions), PASS both |
| suite | Mac: 422 tests OK (6 skipped), plain, at the head; with `TABLE_STDIN_DIFF=1` 66,293 runs of a file also run through standard input, 132,586 comparisons, all equal (one assertion of `StreamLoop` failed in that run, which named the old call; fixed and re-run alone). gram: 422 tests with `TABLE_STDIN_DIFF=1`, the same 66,293 / 132,586, all equal; 421 passed and `test_mcp.Schema` failed because the compiler there did not know its own commit, so it refused to build a scratch copy; rebuilt with `CANCHO_REV` set, `test_mcp` 43 tests OK |
| `stdin_mutants.py` (23: 17 of before, 6 new for `bulk.cho`: the whole first read, reads that never grow, one byte past the room, a short read committed one short, a lost errno, a failed read as the end) | gram: 23 of 23 killed on the final sources. The 24th, "the refill keeps the view of the last chunk", survived and is equivalent (the refill runs only after `lines.next` answered `need`, which starts every answer with the view off), so it is not in the script |
| `mcp_mutants.py`, `inputs_mutants.py` | gram: 67 of 67 and 11 of 11 killed. The 68 retargeted mutants of other scripts were not re-run: no pattern of theirs moved |
| `--check` of the 14 mutant scripts, `stream.py --check`, `schemas.py --check`, `manifest.py --check` (also `--against` cancho-tools `8dab917`), `mcp.py --check`, `site.py --check`, `seo_check.py`, `cancho fmt --check` | pass on both machines (the `--against` check on the Mac) |
| authority | the row is main's plus `io_read`, nothing from the pin (`never_touches` is a new member of the report, not a label); `functions` 631 to 638 |



## 12. R2: JSON lines read (2026-10-13)

Stage R2 of section 8, built as `--input-format jsonl` (and its spelling `ndjson`) with `--fields`, `--absent`, `--max-json-nodes` and `--discover`, for a file and for standard input, on `std.json.parse_with` (cancho #363) with one tape and one state per reader (and per worker thread). This section is the **specification the gates were written from** (sections 5 and 8 say what was designed; this one says what is built, and **where the design was read narrowly**, N1 to N14). The measurements and the gates' results are added at the end of this section when they are in (12.9 to 12.11); a number that is not there was not measured.

### 12.1 Surface

| flag | kind | default | |
|---|---|---|---|
| `--input-format` | choice `csv/jsonl/ndjson` | `csv` | never guessed from a name or from content; `ndjson` is `jsonl` |
| `--fields LIST` | text | none | the columns, by path; required with `jsonl` (below); `args.conflict` with `csv` |
| `--absent` | choice `refuse/empty` | `refuse` | what a declared path with no value is; `args.conflict` with `csv` |
| `--max-json-nodes N` | nat | 4096 | the most JSON nodes a line may hold (the tape is 3 ints a node, once per reader); 1 to 1,048,576; `args.conflict` with `csv` |
| `--discover` | bool | off | the bounded report of 12.7; takes no plan flag |
| `--discover-lines N` | nat | 1000 | 1 to 1,000,000; needs `--discover` |
| `--max-fields N` | nat | 256 | 1 to 4096; needs `--discover` |

The flags are the last of the table, and are looked up only when an argument could be one of them, so a CSV run reads the table as far as before. `--delimiter` with JSON lines input only sets the delimiter of CSV *output*. A standard input and a file give the same bytes (but `detail.path`). `--query` works on the columns `--fields` declares (a name with a dot is `"user.name"` in a query). **A plan on JSON lines without `--fields` (and without `--discover`) is `input.fields-required`**, the shape too (N1).

### 12.2 The input, exactly

* Bytes in, lines out by the reader CSV uses (64 KiB chunk, `--max-line-bytes`, `limit.line-too-long` with the same detail). A byte order mark (`EF BB BF`) at the start of the input is dropped and not counted in an offset. A CR before the LF is dropped. A **blank line** (nothing, or only space, tab and CR) is skipped and counted in the line number. A last line without LF is a line.
* A line is a **record**; the *row* is its 1-based number among records. **line** is the physical line (blank lines counted).
* The line is parsed by `std.json.parse_with` into a tape of `--max-json-nodes` nodes: strict RFC 8259 (no comments, trailing commas, leading zeros, `NaN`, single quotes; strings valid UTF-8 with no raw control byte and no lone surrogate escape; at most 128 deep). A line that does not parse is `parse.jsonl-syntax`; one whose tape would hold more than `--max-json-nodes` nodes is `limit.json-too-many-nodes`. **Which comes first is the parser's order** (the tape fills as it goes), so a line that is both too big and malformed further on is the node limit's (N12).
* A line that parses to something other than an object is `parse.jsonl-not-object`.
* Every declared field is looked up in every record, used by the plan or not (N2); the first refusal is the first field in declaration order.

### 12.3 `--fields`

`LIST` is paths separated by commas. A path is segments separated by dots; a backslash escapes the next byte, which must be one of `,` `\` `#` `.` (anything else, or a backslash at the end, is `input.bad-fields`). An empty path or segment, a path of more than 8 segments, more than 4096 paths, or **the same path twice** (the same text) is `input.bad-fields` naming the item. **The name of the column is the item as written**, but for `\,` written as a comma (N3): the header of the output, `--select`, `--where`, `--group`, `#N` and every refusal's `column` use it. A segment is an object key or, where the value is an array, an index: `0` or digits with no leading zero (`items.0.sku`; `01` is only ever a key, so it finds no index). One rule decides, by the kind of the value at that point: an object looks up keys, an array indices.

### 12.4 What a cell is

| the value at the path | the cell |
|---|---|
| string | its decoded text (escapes, `\u0000` included, surrogate pairs; UTF-8) |
| number | the **exact token text** (`-0`, `1.50`, `1E3`, 40 digits): never through a double. The typed reads of the engine read it as they read a CSV cell (`:int` of `1E3` is `value.not-integer`, `:dec(2)` of `1.50` is fine, `:float` reads any) |
| `true`, `false` | the word |
| `null`, no such key, an index past the end or not an index, a step through a scalar | **absent**: `--absent empty` makes it the empty cell; `--absent refuse` (default) is `parse.jsonl-missing-field` with `detail.reason` `missing`, `null` or `not-container` |
| object, array | `parse.jsonl-not-scalar` (`detail.kind`), whatever `--absent` says |
| a key that occurs twice at an object step of a declared path (compared decoded, so `"a"` and `"a"` are one key) | `parse.jsonl-duplicate-key`, wherever the second is; keys the plan never looks up may repeat (N5) |

### 12.5 Refusals

Every one names `path` (the input as the others do), `line` and `row` (the record's). `column` is the field's name.

| rule | exit | detail beyond those | repair |
|---|---:|---|---|
| `input.fields-required` | 2 | | retry with `--discover` |
| `input.bad-fields` | 2 | `item` (0-based), `reason` | none |
| `parse.jsonl-syntax` | 8 | `offset` (0-based in the line, CR dropped, BOM not counted), `code` (`syntax end control escape number utf8 depth trailing`), `reason` (the parser's words), `near` (the up to 32 bytes around the offset); `looks_like: "json-document"` on row 1 when the line starts with `[` or is a lone `{` | none |
| `parse.jsonl-not-object` | 8 | `kind` (`array string number boolean null`); `looks_like` as above | none |
| `parse.jsonl-missing-field` | 8 | `column`, `reason` | retry with `--absent empty` |
| `parse.jsonl-not-scalar` | 8 | `column`, `kind` (`object array`) | none |
| `parse.jsonl-duplicate-key` | 8 | `column`, `key` (the segment) | none |
| `limit.json-too-many-nodes` | 8 | `limit` | none (raise `--max-json-nodes`) |
| `limit.line-too-long` and the engine's `value.*`, `limit.*`, `column.*`, `select.*`, `agg.*` | | | unchanged; `detail.column` is the field's name |

`args.conflict` for `--fields`, `--absent`, `--max-json-nodes` with CSV, and for `--discover` with any plan flag, `--format csv`/`text`, `--report`, `--query`, `--explain`. The first refusal **in file order** is the answer, for a file read by any number of threads and for standard input.

### 12.6 Parallel and standard input

A file is read by ranges (`--threads`, `--chunk-bytes`, `--parallel-min-bytes`) when the CSV file would be. A range begins at a line start (`par.align`) and, because no JSON text holds a raw LF, **its first line begins a record: the guess is never wrong** and nothing is read again for it; the parent still checks `first line == cur` as it does for CSV (it never fails, and the gate runs it with chunks of 1 to 64 bytes and 1 to 64 threads). Everything a range cannot hand back (a refusal, a full page, `--max-rows`) is read again by the parent from where the sequential read would be, so the answer is the sequential one. Standard input is one pass on one core, as for CSV. `--explain` says which.

### 12.7 `--discover`

`table --input-format jsonl --discover [--discover-lines N] [--max-fields N] [FILE|-]` reads the first N records (every line is parsed as above, so a malformed one is the same refusal) and answers `data.discover`:
`{"fields":[{"path":"id","kinds":{"int":1000},"first_line":1}, ...],"lines_examined":1000,"complete":false,"paths_truncated":false,"undeclarable":0,"arrays":["tags"]}`.
Paths are flattened through objects to 8 segments, in first-appearance order, written as `--fields` takes them (a dot, comma or backslash in a key escaped; a key that begins with `#` is not escaped, it is a name only a plan reads); `kinds` counts the occurrences by `string`, `int` (a number token with no point or exponent), `number`, `bool`, `null`, `object` (an empty object, or one past 8 segments), `array`, and `missing` (the examined records without the path, never negative); an array is not expanded (its path is in `arrays` too). An object at the eighth segment is the kind `object` and is not expanded (its keys are listed nowhere); a key that cannot be declared (an empty key, with its subtree) is counted in `undeclarable`, once for each occurrence, and not listed. Past `--max-fields` paths no more are listed and `paths_truncated` is true. `complete` is true when there is no record after the N examined (exactly N records and the end of the input is complete). **Nothing from it reaches a plan** (N8).

### 12.8 Where the design was read narrowly

* **N1** The shape (a FILE alone) needs `--fields` too: its headers are the fields, its `row_count` the records, and every line is parsed.
* **N2** Every declared field is extracted and checked in every record, used or not (the table is what `--fields` declares: declare what the plan uses).
* **N3** The column name is the item as written but for `\,`; to `--select` a name with a dot escape, write the backslashes the select list needs (`--select 'a\\.b'`).
* **N4** `--absent` has two values; `null` and a missing key are one cell, with the reason in the refusal.
* **N5** Duplicate keys are checked on the keys a lookup walks (the first segment in the root object, the second in that value, ...), over the whole object, and refused wherever the second occurrence is.
* **N6** The records before `--from` are parsed and extracted like the rest (an error in a skipped line is the file's), as a CSV row before `from` is split and counted.
* **N7** Offsets of `parse.jsonl-syntax` are the parser's (`std.json`'s position and code); Python's differ at places and are not compared by the differential (the verdict, rule, line and row are).
* **N8** `--discover` answers `data.discover` (the design shows the members at the top); it examines `min(N, records)`; duplicates are counted as occurrences; it is not a plan flag.
* **N9** `looks_like: "json-document"` is added to both `parse.jsonl-syntax` and `parse.jsonl-not-object` on row 1 under the rule above.
* **N10** A boolean or an escaped string makes the record copy its line (the tape holds no text for `true`/`false`, and a decoded string needs room): the cost of those lines is the copy.
* **N11** `--max-json-nodes` is the tape of each reader (per thread); the ceiling of 1,048,576 nodes is 24 MiB per reader.
* **N12** A line past the node limit and malformed later is the node limit's; the oracle's generator never makes one.
* **N13** `--format jsonl` (output, R3) is **not** built here.
* **N14** `--explain` for JSON lines reads nothing (the header is the declaration), so a file and standard input both resolve their columns; it names the format, the fields, `--absent` and `--max-json-nodes`.

* **N15** Smaller readings the gates fixed: a step through a `null` is `not-container` and a null at the end is `null`; a bad array index (not digits, a leading zero, past the end) is `missing`; a root array over the node limit is the node limit's; `looks_like` is judged after trimming blanks; the line limit counts the CR and the BOM, blank lines included, and a line of exactly `--max-line-bytes` is allowed; the record after `--max-rows` is neither parsed nor length-checked; the first bad `--fields` item is the leftmost and its own defects come before "the same path twice"; nesting of 128 (the root counts) is accepted and 129 refused; `--from` counts records and `--where` is not asked of the records before it; `kinds.missing` of a path seen twice in a record is clamped at 0; `--discover` with `--fields` is `args.conflict`; `--discover-lines` or `--max-fields` without `--discover` is `args.required-flag`; `--fields ''` is `args.bad-value` (a flag value cannot be empty); `--discover` with `--format csv` or `text` is `args.conflict`.
* **N16** **The sort holds a record and splits it again with the CSV reader** (`finish.finish_sort`, `psort.make_run`), which the reader contract of section 2 did not say. For `--order-by` the JSON-lines loops hand the engine the row as a CSV record (the cells joined by the delimiter, quoted when they must be, cut again by `reader.fields`: `jrec.csv_record`). Found by the first differential of `--order-by`, which returned the raw line.
* **N17** **A flag-0 cell may hold the delimiter or an LF in JSON lines**, which `writer.csv_cell` assumes it does not (an unquoted CSV field holds neither). For csv output a string with the delimiter or an LF is written after the record with its quotes doubled and made a flag-1 cell (`jrec.quoted_cell`).
