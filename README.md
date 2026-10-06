# table

A command-line tool for AI agents that reads a CSV or TSV file and answers
with a document: the file's **shape**, or some of its **columns**. Written in
[lex-sys](https://github.com/alpibrusl/lex-sys), as one of the
[lexsys-tools](https://github.com/alpibrusl/lexsys-tools) family (JSON out
against a schema, every error a named rule, a self-description from
`introspect`, an authority the compiler derived), and the first of them built
outside that repository, on its `contract/` taken as a package.

```console
$ table --root data orders.csv                       # the shape
{"ok":true,"command":"table","schema":"table.v2","data":{"headers":["id","status","bytes","path","note"],"column_count":5,"row_count":1000000,"truncated":false},"meta":{"version":"0.2.0"}}
$ table --root data --select status,bytes --limit 2 orders.csv
{"ok":true,"command":"table","schema":"table.v2","data":{"columns":["status","bytes"],"rows":[["200","74606"],["200","33432"]],"row_count":2,"truncated":true,"next":{"from":2}},"meta":{"version":"0.2.0"}}
$ table --root data --select note,id --format csv orders.csv | head -3
note,id
"a,b 0",0
"a,b 1",1
```

## What it is, and is not

**Is:**

* **the shape**: `table [--root DIR] [--delimiter ,|tab|;] [--max-rows N]
  [--max-line-bytes N] [--format json|text] FILE` prints the header names, the
  number of data rows, the number of columns, and `truncated` when `--max-rows`
  stopped the count;
* **select columns**: `table --select NAMES [--limit N] [--from N] [--max-bytes N]
  [--format json|csv] FILE`. NAMES are header names, exact, in the order given, a
  name repeated repeats the column; `\,` and `\\` write a comma and a backslash in a
  name, `#3` is the third column. As JSON: a page of rows (`--limit`, default 1000,
  within `--max-bytes`) with `truncated` and a `next` cursor that `--from` resumes
  (the file is re-read from the start: see [`docs/select.md`](docs/select.md)). As
  CSV: the columns, header first, minimal quoting, LF, streamed. An unknown name is
  `select.unknown-column`, listing the header, with a `choose` repair when only the
  case differs. The choices and what each rests on are in
  [`docs/select.md`](docs/select.md);
* an RFC 4180 reader of its own ([`tools/table/`](tools/table)): quoted fields,
  doubled quotes, delimiters and newlines (LF or CRLF) inside quotes, a leading
  UTF-8 byte order mark, blank lines between records skipped;
* streaming and bounded: the file is read in 64 KiB chunks through the contract's
  `lines` reader. Memory is the longest record kept, the header, a row of output and
  (as JSON) the page: about 1.5 to 1.9 MB on the 31 MB benchmark file, for the shape
  and for either selection;
* a rule for every refusal, with an exit code: `parse.csv-ragged-row`,
  `parse.csv-bad-quote`, `parse.csv-unterminated-quote`, `limit.line-too-long` (with a
  repair that raises `--max-line-bytes`), `limit.header-too-large`,
  `limit.record-too-large`, `limit.output-too-large`, `limit.too-many-rows`,
  `select.unknown-column`, `select.ambiguous-column`, and the `args.*`, `path.*` and
  `io.*` rules of the contract. The tool's own rules are in `extra_rules`;
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
  (section 5) plans: filter rows (`status>=400`), group and aggregate (`count`,
  `sum`, `min`, `max`, `distinct`, `mean`), sort, top-N, `describe`, and the same
  plan as one `--query` string. **None of that is built.** There is no stdin, no
  type inference, no NDJSON output, no way to rename or compute a column.
* not a validator of every CSV dialect: the delimiter is one of comma, tab or
  semicolon; a lone CR is a byte of a field, not a line break (Python's `csv`
  ends a record there; the two can differ on a file that is already damaged); a
  quote in the middle of an unquoted field is text.
* not cheap to page through: `--from N` re-reads the file from the start.

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

The contract revision is `5634a4a7` (which adds `extra_rules`). The compiler pin is
`f8ebe98e6867e1b7af3a5636b180b56eb3dd3cc1`.

## Gates

```sh
lex-sys fmt --check tools generated
lex-sys build
python3 scripts/schemas.py --check      # the schema is generated, not edited
python3 scripts/manifest.py --check     # authority = the compiler's, embedded, within tools.toml
python3 -W ignore -m unittest discover -s tests/conformance -v   # needs jsonschema, cc; strace on Linux
python3 scripts/select_mutants.py       # mutation check of --select (slow: one build and a test run each)
```

The conformance tests, in [`tests/conformance`](tests/conformance):

* **differential** against Python's `csv` module (strict mode), for the shape
  (`test_differential.py`) and for `--select` (`test_select.py`): hand-picked
  fixtures (quoted LF and CRLF newlines, BOM, CRLF, empty fields, doubled quotes,
  header only, empty file, unterminated quote, ragged rows, text after a closing
  quote), thousands of random tables written by `csv.writer` and then damaged, and
  fields that straddle the 64 KiB chunk boundary. A selection must equal Python's
  fields, and its CSV must be the bytes `csv.writer` writes and read back to the same
  fields;
* **select**: duplicate and reordered columns, unknown and ambiguous names, escapes
  and positions, pages (`--limit`, `--from`, `next`) equal to slicing, the byte
  budget, `--max-rows`, record and line limits, binary names and fields;
* **rules**: every rule the tool declares has a fixture that reaches it, with its
  exit code and, for a `retry` repair, a re-run that succeeds;
* **memory**: peak resident set is flat on a 2 MB and a 37 MB file, for the shape
  and for each way of selecting, and on a record of 20 MB over 200,000 lines;
* **fuzz**: no input reaches a trap;
* **authority**: the binary's report is the committed one, within `tools.toml`,
  and under `strace` (Linux) there is no socket and no write on disk.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) builds the pinned
compiler and runs all of the above but the mutants. It was written without being
run on Actions; it was run step by step on a Mac and a Linux x86-64 machine with the
compiler it pins.

## Benchmark

`python3 scripts/bench.py` generates the file of the design's benchmark (1,000,000
rows of `id,status,bytes,path,note`, the last quoted; seeded; 31,667,311 bytes) and
times each tool answering the same question, with output to `/dev/null` and the
output checked first. Minimum of 5 interleaved runs; peak RSS from `/usr/bin/time`.
Three questions: the shape (a row count), the selection of two columns as CSV (the
first benchmark in which the incumbents do the same work as `table`: they read, parse,
select, quote and write), and the first 1000 rows of two columns.

**Linux x86-64** (the pinned compiler; 6 cores of a shared box, niced, with a soak
running; csvtk 0.38.0, Miller 6.22.0):

| question | `table` | `csvtk -j 1` | `mlr` |
|---|---:|---:|---:|
| shape: `table FILE` / `csvtk nrow` / `mlr count` | 0.061 s, 1.8 MB | 0.507 s, 21.4 MB | 0.328 s, 110 MB |
| cut: `--select status,bytes --format csv` / `csvtk cut -f` / `mlr --icsv --ocsv cut -f` | **0.137 s, 1.9 MB** | 0.628 s, 21.7 MB | 0.906 s, 329 MB |
| first 1000 rows of two columns (json) / `csvtk head` / `mlr head then cut` | 0.0016 s, 1.8 MB | 0.0114 s, 17.0 MB | 0.0299 s, 31.2 MB |

`table`'s CSV is byte for byte `csvtk`'s and `mlr`'s (md5 of the three). `wc -l`
reads the file in 0.007 s.

**Apple silicon Mac** (16 cores, load average 5 to 8 from other work; csvtk 0.38.0;
no `mlr` there). `table` was built by the compiler at `db7d5bc` (the pinned one's
fix, another revision):

| question | `table` | `csvtk -j 1` |
|---|---:|---:|
| shape | 0.032 s, 1.5 MB | 0.203 s, 24.1 MB |
| cut as CSV | **0.057 s, 1.7 MB** | 0.202 s, 23.8 MB |
| first 1000 rows | 0.0025 s, 1.5 MB | 0.0078 s, 19.1 MB |

So on the cut, `table` is 4.6x faster than `csvtk -j 1` on Linux and 3.5x on the
Mac, and uses a tenth of the memory. This is for two columns of five that are not
the quoted one, on one file; it is not a claim about every question, and `table`
does far less (no types, no filter, no sort). The rounds, and why none was needed to
close a gap, are in [`docs/history.md`](docs/history.md).

## Layout

```
tools/table/        the program: table.ls (flags, the read, the answers), reader.ls
                    (RFC 4180), writer.ls (csv and json fields), plan.ls (--select)
docs/               select.md (the design), history.md (the measurements)
generated/table/    the embedded manifest (written by scripts/manifest.py)
manifests/          the authority, as the compiler reports it
schemas/            table.v2.json (written by scripts/schemas.py)
tools.toml          the authority ceiling a person writes and reviews
tests/conformance/  the gates, run against build/table
scripts/            manifest.py, schemas.py, bench.py, select_mutants.py
```

Licence: EUPL-1.2, as lexsys-tools.
