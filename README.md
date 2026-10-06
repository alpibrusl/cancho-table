# table

A command-line tool for AI agents that reads a CSV or TSV file and answers
with a document: the file's **shape**, some of its **columns**, the **rows** that satisfy
a condition, or **counts and sums by group**. Written in
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
$ table --root data --where 'status=404 and bytes:int>50000' --select id,bytes --limit 2 orders.csv
{"ok":true,"command":"table","schema":"table.v2","data":{"columns":["id","bytes"],"rows":[["6","99913"],["21","84186"]],"row_count":2,"truncated":true,"next":{"from":21}},"meta":{"version":"0.3.0"}}
$ table --root data --group status --agg count,sum:bytes --sort -count --top 2 --format csv orders.csv
status,count,sum:bytes
200,500269,25023279627
404,166893,8343911780
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
* **filter rows**: `--where EXPR`, conditions joined by `and`: `= != < <= > >=`,
  `contains`, `in (...)`. Text compares bytewise; **`COLUMN:int` compares as an exact
  64-bit integer** (a cell that is not one, an empty cell included, is a refusal naming
  its row and column, never a coercion or a float); conditions stop at the first false,
  so `x != '' and x:int > 5` guards an empty cell. A malformed expression is
  `where.syntax` with the byte offset of the error. It composes with `--select` and
  paging, and is applied before grouping;
* **group and aggregate**: `--group NAMES --agg count,sum:COL,min:COL,max:COL,distinct:COL
  [--sort [-]COLUMN] [--top N]`, as json (`table.v2` rows plus `group_count`) or csv.
  Sums, minima and maxima are exact integers and a sum past 64 bits is
  `agg.sum-overflow`, never a wrap. Groups come out in key order (bytewise, field by
  field), or `--sort` order with ties by key, so the same rows give the same bytes
  in any order. Bounded by `--max-groups`, `--max-distinct` and `--max-state-bytes`,
  each its own rule. All of it is one plan (`tools/table/query.ls`); design and
  deviations from the design document in [`docs/filter.md`](docs/filter.md);
* **threads**: `--threads N` (1 to 64, default 1) reads the file with N threads, for rows and
  groups, and the answer is **the sequential answer byte for byte**: the same output, the same
  refusal naming the same row, line and column (the first in file order), the same ragged-row
  report, the same page. A range of the file whose first line is not where the previous record
  ended (a quoted newline) is read again by the parent, and so is anything that depends on order
  (a page, a bound, an integer sum that could leave 64 bits). Memory is O(threads x range), not the
  file. 4 to 7x faster on a 16-core Mac, 2 to 2.4x on three physical cores; the design, what the
  language did and did not allow, and the measurements are in [`docs/parallel.md`](docs/parallel.md);
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
* an authority of `args, conc, dir_read, err_write, file_read, fs_read(""), heap,
  io_write` (`conc` is `--threads`): no network, no foreign code, no clock, nothing written to disk,
  enforced by the ceiling in [`tools.toml`](tools.toml) and
  `python3 scripts/manifest.py --check`;
* `table introspect` and `table skill`, generated from the tables the parser
  runs on, as in the other tools.

**Is not:**

* **not pandas.** Arbitrary pandas is arbitrary code, and giving that up is what
  keeps these tools bounded.
* **not yet all of the declarative core** the
  [design](https://github.com/alpibrusl/lexsys-tools/blob/next-tools-design/docs/next-tools.md)
  (section 5) plans. **Not built:** `mean` (an exact fixed-point mean needs a stated
  rounding rule and a scale), `describe`, a second sort key, `or`, expressions and
  functions in `--where` (the design excludes them from v1), decimals (a decimal in an
  `:int` column is a refusal), and the same plan as one `--query` string. There is no
  stdin, no type inference (a column is text unless `:int` says otherwise), no NDJSON
  output, no join, no way to rename or compute a column.
* not a validator of every CSV dialect: the delimiter is one of comma, tab or
  semicolon; a lone CR is a byte of a field, not a line break (Python's `csv`
  ends a record there; the two can differ on a file that is already damaged); a
  quote in the middle of an unquoted field is text.
* not cheap to page through: `--from N` re-reads the file from the start.
* not threaded for the shape or for a selection that starts late (`--from`); no work stealing
  (no atomics yet), so a wave waits for its slowest range.

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

The contract revision is `a636daa7` (which adds `extra_rules`). The compiler pin is
`f8ebe98e6867e1b7af3a5636b180b56eb3dd3cc1`.

## Gates

```sh
lex-sys fmt --check tools generated
lex-sys build
python3 scripts/schemas.py --check      # the schema is generated, not edited
python3 scripts/manifest.py --check     # authority = the compiler's, embedded, within tools.toml
python3 -W ignore -m unittest discover -s tests/conformance -v   # needs jsonschema, cc; strace on Linux
python3 scripts/select_mutants.py       # mutation check of --select (slow: one build and a test run each)
python3 scripts/filter_mutants.py       # the same for --where, --group, --agg
python3 scripts/parallel_mutants.py     # the same for --threads
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
times each tool answering the same question, with output to `/dev/null`. **Before any
timing every contender's answer is checked** against one computed independently in
Python (the ids of the filtered rows; the count and the sum of `bytes` per status), and
a contender that disagrees stops the run. Minimum of 5 interleaved runs; peak RSS from
`/usr/bin/time`. Six questions; the first three are the earlier ones (`docs/history.md`).

**Linux x86-64** (the pinned compiler; 6 cores of a shared box, niced, a soak running;
csvtk 0.38.0, Miller 6.22.0). Seconds, peak RSS:

| question | `table` | `csvtk -j 1` | `mlr` | reference floor |
|---|---:|---:|---:|---:|
| shape | 0.055 s, 1.9 MB | 0.395 s, 21.7 MB | 0.237 s, 122 MB | `wc -l` 0.006 s |
| cut two columns, csv | 0.112 s, 2.0 MB | 0.489 s, 21.3 MB | 0.716 s, 342 MB | |
| first 1000 rows | 0.0015 s | 0.0088 s | 0.0229 s | |
| **filter** `status=404 and bytes>50000`, csv | **0.100 s, 2.2 MB** | 0.797 s (`filter` then `grep`, two processes) / 7.99 s (`filter2`, one) | 0.397 s, 127 MB | `awk -F,` 0.611 s |
| **group-count** by status | **0.177 s, 1.9 MB** | 0.397 s (`freq`) | 0.371 s (`count-distinct`), 140 MB | `cut\|sort\|uniq -c` 0.176 s |
| **group sum** of bytes by status | **0.220 s, 1.9 MB** | 1.103 s (`summary`), 118 MB | 0.549 s (`stats1`), 301 MB | |

**Apple silicon Mac** (16 cores, load 4 to 5 from other work; csvtk 0.38.0; no `mlr`
there; `table` built by the compiler at `db7d5bc`, the pinned one's fix):

| question | `table` | `csvtk -j 1` | reference floor |
|---|---:|---:|---:|
| shape | 0.029 s | 0.172 s | `wc -l` 0.025 s |
| cut two columns, csv | 0.054 s | 0.174 s | |
| first 1000 rows | 0.0019 s | 0.0059 s | |
| **filter** | **0.047 s** | 0.288 s (`filter`\|`grep`) / 2.63 s (`filter2`) | `awk -F,` 0.577 s |
| **group-count** | **0.104 s** | 0.179 s | `cut\|sort\|uniq -c` 0.293 s |
| **group sum** | **0.113 s** | 0.398 s (`summary`) | |

Ratios against the best `csvtk -j 1` invocation: filter 6.1x (Mac) and 8.0x (Linux)
faster, group-count 1.7x and 2.2x, group sum 3.5x and 5.0x. Against Miller on Linux:
filter 4.0x, group-count 2.1x, group sum 2.5x, at a hundredth of the memory. `table`
was not worse than `csvtk -j 1` anywhere, so no tuning round was needed for the new
operations; one round was needed for a regression it introduced in the old ones, and is
in [`docs/history.md`](docs/history.md).

What these numbers are not: a claim about every question. Everything here is one
file, two integer columns and one low-cardinality group column; `table` does less than
the incumbents (no types, one sort key, integers only: `csvtk summary` computes floats
and `mlr` is a whole language). The `awk` and `uniq` rows are floors where CSV quoting
does not matter and are not equivalent on the quoted column; the `uniq` pipeline is
three processes running in parallel on the Linux box's other cores, which is why it
matches `table` there.

### Threads (`--threads N`, `python3 scripts/bench_parallel.py`)

Seconds, the same file; DuckDB is `COPY (...) TO '/dev/null'` with `SET threads=N`, csvtk is `-j N`.
Every answer is checked first. Apple silicon Mac, 16 cores:

| question | `table` 1 / 2 / 4 / 8 / 16 threads | DuckDB 1 / 4 / default | csvtk -j 1 |
|---|---|---|---:|
| filter | 0.051 / 0.035 / 0.020 / 0.012 / 0.012 | 0.176 / 0.074 / 0.075 | 0.308 |
| cut two columns | 0.058 / 0.037 / 0.021 / 0.013 / 0.011 | 0.187 / 0.079 / 0.078 | 0.185 |
| group-count | 0.110 / 0.061 / 0.033 / 0.017 / 0.015 | 0.129 / 0.062 / 0.062 | 0.186 |
| group sum | 0.121 / 0.069 / 0.037 / 0.019 / 0.018 | 0.135 / 0.064 / 0.063 | 0.403 |

Linux x86-64, cores 0 to 5 (three physical cores) of a shared box, `table` 1 / 2 / 4 / 6 threads:
filter 0.124 / 0.081 / 0.071 / 0.064, group-count 0.210 / 0.107 / 0.095 / 0.087; `csvtk -j N` does
not scale (0.9 to 1.2x). The scaling there is limited by the machine, which was checked
([`docs/parallel.md`](docs/parallel.md) section 5): six independent sequential processes get 1.56x
the throughput of one. The default threshold below which `--threads` is ignored, 1 MiB of data, is
measured in the same document.

## Layout

```
tools/table/        the program: table.ls (flags, the read, the answers), engine.ls (what is done
                    with a row), scan.ls (a byte range), par.ls (threads), reader.ls
                    (RFC 4180), writer.ls (csv and json fields), plan.ls (name lists),
                    query.ls (the plan), expr.ls (--where), agg.ls (groups), frame.ls
                    (the plan against the header)
docs/               select.md, filter.md, parallel.md (the designs), history.md (the measurements)
generated/table/    the embedded manifest (written by scripts/manifest.py)
manifests/          the authority, as the compiler reports it
schemas/            table.v2.json (written by scripts/schemas.py)
tools.toml          the authority ceiling a person writes and reviews
tests/conformance/  the gates, run against build/table
scripts/            manifest.py, schemas.py, bench.py, bench_parallel.py, select_mutants.py,
                    filter_mutants.py, parallel_mutants.py
```

Licence: EUPL-1.2, as lexsys-tools.
