# table

A command-line tool for AI agents that reads a CSV or TSV file and answers
with a document: today, the file's **shape**. Written in
[lex-sys](https://github.com/alpibrusl/lex-sys), as one of the
[lexsys-tools](https://github.com/alpibrusl/lexsys-tools) family (JSON out
against a schema, every error a named rule, a self-description from
`introspect`, an authority the compiler derived), and the first of them built
outside that repository, on its `contract/` taken as a package.

```console
$ table --root data orders.csv
{"ok":true,"command":"table","schema":"table.v1","data":{"headers":["id","status","bytes","path","note"],"columns":5,"rows":1000000,"truncated":false},"meta":{"version":"0.1.0"}}
$ table --root data --max-rows 10 --format text orders.csv
rows	10
columns	5
truncated	true
header	id	status	bytes	path	note
```

## What it is, and is not

**Is** (this is a skeleton, deliberately small):

* one operation: `table [--root DIR] [--delimiter ,|tab|;] [--max-rows N]
  [--max-line-bytes N] [--format json|text] FILE` prints the header names, the
  number of data rows, the number of columns, and `truncated` when `--max-rows`
  stopped the count (schema [`table.v1`](schemas/table.v1.json));
* an RFC 4180 reader of its own ([`tools/table/table.ls`](tools/table/table.ls)):
  quoted fields, doubled quotes, delimiters and newlines (LF or CRLF) inside
  quotes, a leading UTF-8 byte order mark, blank lines between records skipped;
* streaming and bounded: the file is read in 64 KiB chunks through the contract's
  `lines` reader, nothing of a row is kept, and the header and the longest line
  are all that is held, so memory does not depend on the file's size (about
  1.5 MB for 40 MB below). A quoted field may span any number of lines;
* a rule for every refusal. `parse.csv-ragged-row` (a row with another number of
  fields than the header, naming the first and counting the rest),
  `parse.csv-bad-quote` (a closing quote followed by something but the
  delimiter), `parse.csv-unterminated-quote`, `limit.line-too-long` (with a repair
  that raises `--max-line-bytes` to what was seen), `limit.header-too-large`, and
  the `args.*`, `path.*` and `io.*` rules of the contract. `--max-rows` is a bound
  on work, not memory, and is a `truncated` answer rather than an error;
* an authority of `args, dir_read, err_write, file_read, fs_read(""), heap,
  io_write`: no network, no foreign code, no clock, nothing written to disk,
  enforced by the ceiling in [`tools.toml`](tools.toml) and
  `python3 scripts/manifest.py --check`;
* `table introspect` and `table skill`, generated from the tables the parser
  runs on, as in the other tools.

**Is not:**

* **not pandas.** Arbitrary pandas is arbitrary code, and giving that up is what
  keeps these tools bounded.
* **not yet the declarative core** the
  [design](https://github.com/alpibrusl/lexsys-tools/blob/next-tools-design/docs/next-tools.md)
  (section 5, lexsys-tools PR #23) plans: select columns, filter rows
  (`status>=400`), group and aggregate (`count`, `sum`, `min`, `max`, `distinct`,
  `mean`), sort, top-N with a `next` cursor, `describe`, and the same plan as one
  `--query` string. **None of that is built.** There is no TSV/JSON-lines
  sniffing, no stdin, no output of rows, no types.
* not a validator of every CSV dialect: the delimiter is one of comma, tab or
  semicolon; a lone CR is a byte of a field, not a line break (Python's `csv`
  ends a record there; the two can differ on a file that is already damaged); a
  quote in the middle of an unquoted field is text.

## Build

You need Rust (for the compiler), `clang`, and network access once, for the
dependencies.

```sh
git clone https://github.com/alpibrusl/lex-sys
git clone https://github.com/alpibrusl/lexsys-table
cd lex-sys
git checkout "$(sed -n 's/^lex-sys *= *"\([0-9a-f]*\)".*/\1/p' ../lexsys-table/lex-sys.toml)"
cargo build --release -p lex-sys
export PATH="$PWD/target/release:$PATH"
cd ../lexsys-table
lex-sys build             # installs the contract modules into build/deps, then builds build/table
```

### The contract, as a package

`contract/` of lexsys-tools is not copied here. [`lex-sys.toml`](lex-sys.toml)
pins its commit and names each module `table` imports directly, one
`[dependencies.NAME]` line each (`cli`, `describe`, `fail`, `limit`, `lines`,
`out`, `path`, `place`, `text`); `lex-sys install` also brings the modules those
need (`rules`, `sha`). `toolbox.built` (the authority, the schema and the
compiler pin, embedded in the binary) is not in the package: it is
[`generated/table/built.ls`](generated/table/built.ls), written by
[`scripts/manifest.py`](scripts/manifest.py), which derives the authority over the
sources **and the installed dependency sources** in `build/deps`, and with
`--against DIR` checks that it is the same report the package's origin sources
(`lexsys-tools/contract`) give.

The compiler pin is `f8ebe98e6867e1b7af3a5636b180b56eb3dd3cc1`.

## Gates

```sh
lex-sys fmt --check tools generated
lex-sys build
python3 scripts/schemas.py --check      # the schema is generated, not edited
python3 scripts/manifest.py --check     # authority = the compiler's, embedded, within tools.toml
python3 -W ignore -m unittest discover -s tests/conformance -v   # needs jsonschema, cc; strace on Linux
```

The conformance tests, in [`tests/conformance`](tests/conformance):

* **differential** against Python's `csv` module (strict mode): hand-picked
  fixtures (quoted LF and CRLF newlines, BOM, CRLF, empty fields, doubled quotes,
  header only, empty file, unterminated quote, ragged rows, text after a closing
  quote), 1,500 random tables written by `csv.writer` and then damaged (cut,
  stray quote, ragged), and fields that straddle the 64 KiB chunk boundary: same
  header names, same row and column counts, and an error exactly when the
  reference raises or finds a ragged row;
* **rules**: every rule the tool declares has a fixture that reaches it, with its
  exit code and, for a `retry` repair, a re-run that succeeds;
* **limits**: a 1 MiB field hits the line limit; `--max-rows` truncates, and
  stops reading; the header is bounded; no fuzzed input traps;
* **memory**: peak resident set on a 2 MB and a 37 MB file and on a record of
  20 MB over 200,000 lines is flat;
* **authority**: the binary's report is the committed one, within `tools.toml`,
  and under `strace` (Linux) there is no socket and no write on disk.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) builds the pinned
compiler and runs all of the above, plus the `--against` check. It was written
without being run on Actions; it was run step by step on a Mac and a Linux
x86-64 machine with the compiler it pins.

## Benchmark

`python3 scripts/bench.py` generates the file of design section 5's benchmark
(1,000,000 rows of `id,status,bytes,path,note`, the last quoted; `status` one of
200 200 200 301 404 500, `bytes` 0..99999, path `/p/N`, note `"a,b N"`; seeded)
and times each tool answering the same question, the file's shape, with output to
`/dev/null`; the row count each reports is checked first. Minimum of 5 interleaved
runs. The file this generator writes is **39.6 MB**, not the 31.7 MB the design
section states for its file; the design gives the columns and ranges but not the
generator, so this is a reconstruction.

Apple silicon Mac, 16 cores, load average 5 to 8 from other work, csvtk 0.38.0
(Homebrew), `mlr` not installed. `table` was built by the compiler at
`db7d5bc` (the pinned one's fix, another revision):

| tool | min | median | peak RSS |
|---|---:|---:|---:|
| `table` | 0.033 s | 0.033 s | 1.5 MB |
| `csvtk -j 1 nrow` | 0.200 s | 0.203 s | 24.4 MB |
| `wc -l` (the floor: bytes read, newlines found) | 0.036 s | 0.037 s | 2.4 MB |

On Linux x86-64 (the pinned compiler, 6 cores of a shared box; no `csvtk` or
`mlr` there): `table` 0.070 s and 1.8 MB, `wc -l` 0.008 s. So on the Mac `table` is at the floor of reading the file, and on Linux it is about nine times it: this was measured, not explained. Not measured: other questions than the shape (nothing else is
built), wider files, a file where every field is quoted.

## Layout

```
tools/table/        the program: the RFC 4180 reader and the one operation
generated/table/    the embedded manifest (written by scripts/manifest.py)
manifests/          the authority, as the compiler reports it
schemas/            table.v1.json (written by scripts/schemas.py)
tools.toml          the authority ceiling a person writes and reviews
tests/conformance/  the gates, run against build/table
scripts/            manifest.py, schemas.py, bench.py
```

Licence: EUPL-1.2, as lexsys-tools.
