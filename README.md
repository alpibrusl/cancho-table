# table

[![ci](https://github.com/alpibrusl/lexsys-table/actions/workflows/ci.yml/badge.svg)](https://github.com/alpibrusl/lexsys-table/actions/workflows/ci.yml)

**Ask a CSV a question. Get an exact answer.** `table` picks columns, filters rows, and counts or sums by a key, in one pass over a CSV or TSV file. Memory stays small however big the file is, whole-number sums are exact, and a bad request is refused with a named rule and, where there is one, a repair. One small binary, written in [lex-sys](https://github.com/alpibrusl/lex-sys). The [project page](https://alpibrusl.github.io/lexsys-table/) has the same in pages.

**Status: early.** It works and is tested; what it cannot do yet is listed below.

## What you can do now

Every example runs on this five-line file, saved as `orders.csv`, and the output is what the program prints.

```csv
id,customer,status,bytes
1,"Doe, Jane",200,512
2,Acme,404,
3,"Doe, Jane",200,2048
4,Acme,500,128
5,Zed,200,64
```

### Pick some columns

They come out in the order you name them. A comma inside a quoted field stays inside it.

<!-- gen:t-select -->
```console
$ table --select customer,bytes --format csv orders.csv
customer,bytes
"Doe, Jane",512
Acme,
"Doe, Jane",2048
Acme,128
Zed,64
```
<!-- /gen:t-select -->

### Keep the rows that match

Text compares byte by byte; `:int` compares whole numbers exactly. Conditions run left to right and stop at the first false one, so the first keeps the empty cell away from the number test.

<!-- gen:t-filter -->
```console
$ table --where "bytes != '' and bytes:int > 100" --select id,customer --format csv orders.csv
id,customer
1,"Doe, Jane"
3,"Doe, Jane"
4,Acme
```
<!-- /gen:t-filter -->

### Count and sum by a key

`count`, `sum`, `min`, `max` and `distinct`. Sums are exact whole numbers; one that would not fit in 64 bits is refused, not wrapped.

<!-- gen:t-group -->
```console
$ table --where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --format csv orders.csv
customer,count,sum:bytes
"Doe, Jane",2,2560
Acme,1,128
Zed,1,64
```
<!-- /gen:t-group -->

### Page through the results

JSON comes in pages; `next` says where the next starts. Resuming re-reads the file from the start, so paging deep into a huge file gets slow.

<!-- gen:t-page -->
```console
$ table --select id,customer --limit 2 orders.csv
{"ok":true,"command":"table","schema":"table.v2","data":{"columns":["id","customer"],"rows":[["1","Doe, Jane"],["2","Acme"]],"row_count":2,"truncated":true,"next":{"from":2}},"meta":{"version":"0.3.0"}}
$ table --select id,customer --limit 2 --from 2 orders.csv
{"ok":true,"command":"table","schema":"table.v2","data":{"columns":["id","customer"],"rows":[["3","Doe, Jane"],["4","Acme"]],"row_count":2,"truncated":true,"next":{"from":4}},"meta":{"version":"0.3.0"}}
```
<!-- /gen:t-page -->

### Get CSV back

`--format csv` writes rows as they are read, whatever the file size. A non-zero exit means the output is only the start of the answer.

<!-- gen:t-csv -->
```console
$ table --where "status = 200" --format csv orders.csv
id,customer,status,bytes
1,"Doe, Jane",200,512
3,"Doe, Jane",200,2048
5,Zed,200,64
```
<!-- /gen:t-csv -->

### Use several cores

`--threads N` (1 to 64) gives the same bytes as one core, refusals included. Files under 1 MiB are read on one core whatever you ask, so this tiny example only shows the same answer.

<!-- gen:t-cores -->
```console
$ table --threads 4 --group status --agg count --format csv orders.csv
status,count
200,3
404,1
500,1
```
<!-- /gen:t-cores -->

### See what a bad request looks like

Every refusal has a fixed rule name, an exit status and, where one exists, a repair: here, the command with the right spelling. A cell that is not a whole number, an empty one too, is refused with its row, line and column.

<!-- gen:t-refuse -->
```console
$ table --select Status orders.csv
{"rule": "select.unknown-column", "hint": "pick from detail.available", "repair": {"kind": "choose", "options": [{"argv": ["table", "--select", "status", "orders.csv"]}]}}
# exit status 3; the rule, the hint and the repair from the JSON line it prints
```
<!-- /gen:t-refuse -->

## What it cannot do yet

* **Whole numbers only.** A decimal in a number column is refused. Decimals, as exact numbers and never as floats, are on the [backlog](docs/backlog.md).
* **No row sort yet.** You can sort the groups of a count or a sum, not the rows. A row sort is in progress.
* **No joins**, and **one input file** at a time, named on the command line (no standard input).
* **No JSON lines**, no Parquet. Only CSV and TSV (comma, tab or semicolon).
* **No guessing.** Every column is text unless you say `:int`. No mean, no computed or renamed column, no `or` in a filter.

JSON lines, joins, `mean`, decimals and standard input are on the [backlog](docs/backlog.md), in rough order. Nothing there is promised.

## How fast

A *core* is one of the independent workers inside a processor. By default `table` uses one; `--threads 16` uses sixteen. [DuckDB](https://duckdb.org) is a full database engine and a popular, fast way to query a CSV, so it is a fair yardstick. On these four questions one core of `table` is about as fast as, or faster than, DuckDB on all sixteen. With many distinct keys in a group-by, DuckDB on all cores wins.

Time to answer on the same 1,000,000-row, 32 MB CSV, in seconds (lower is better):

| question | table, 1 core | table, 16 cores | csvtk | DuckDB, 1 core | DuckDB, 16 cores |
|---|---:|---:|---:|---:|---:|
| filter rows | 0.046 | **0.0087** | 0.293 | 0.170 | 0.075 |
| count by a key | 0.057 | **0.0099** | 0.181 | 0.132 | 0.065 |
| sum by a key | 0.062 | **0.011** | 0.395 | 0.141 | 0.067 |
| pick 2 columns | 0.050 | **0.011** | 0.188 | 0.190 | 0.078 |

Apple-silicon Mac, 16 cores. Best of five runs, output thrown away, every answer checked against a Python answer first. csvtk 0.38.0 on one core (its filter is `filter` piped to `grep`); DuckDB 1.5.6; DuckDB's 16-core times for filter and pick-2-columns are from an earlier run on the same machine. Miller is not installed on the Mac; on a different machine (Linux, 6 shared cores) it took 0.300 s to filter, 0.438 s to count and 0.649 s to sum, against `table`'s 0.074, 0.128 and 0.140 s there.

**Where it is slower:** a group-by with a million distinct keys is 3.4 times slower than DuckDB on one core and 9.4 times slower than DuckDB on 16 (Mac); `distinct` over a column of unique values is 1.4 times slower on one core; and there is no row sort yet. All the tables, the harder cases and how to rerun them: [docs/benchmarks.html](https://alpibrusl.github.io/lexsys-table/benchmarks.html).

## Install

You need `git`, Rust, `clang` and `python3`. The compiler is the commit `lex-sys.toml` pins:

```sh
git clone https://github.com/alpibrusl/lex-sys                           # the compiler
git clone https://github.com/alpibrusl/lexsys-table && cd lexsys-table
REV=$(sed -n 's/^lex-sys *= *"\([0-9a-f]*\)".*/\1/p' lex-sys.toml)        # the compiler these sources need
(cd ../lex-sys && git fetch -q origin && git checkout "$REV" && cargo build --release -p lex-sys)
export PATH=$PWD/../lex-sys/target/release:$PWD/build:$PATH
lex-sys build                                                            # builds build/table
```

`table introspect` lists every flag, rule and limit; `table skill` prints a short guide for an agent.

## Learn more

| | |
|---|---|
| [project page](https://alpibrusl.github.io/lexsys-table/) | what you can do, in pages, with the refusal rules and the limits |
| [docs/select.md](docs/select.md), [docs/filter.md](docs/filter.md) | how `--select`, `--where`, `--group` and `--agg` behave, and why |
| [docs/parallel.md](docs/parallel.md) | `--threads`: how it gives the one-core answer |
| [docs/benchmarks.html](https://alpibrusl.github.io/lexsys-table/benchmarks.html) | the latest timings, with conditions, and how to rerun them |
| [docs/backlog.md](docs/backlog.md) | what is not done yet |
| [docs/reference.md](docs/reference.md) | the long description of every flag and of the layout |
| [docs/history.md](docs/history.md), [docs/adversarial.md](docs/adversarial.md) | for contributors: what was measured and changed, and the harder cases |

## Contributing

Every change goes through what CI runs: `lex-sys fmt --check tools generated`, `lex-sys build`, `scripts/schemas.py --check`, `scripts/manifest.py --check`, the conformance tests, and `scripts/site.py --check`. Design before code, in `docs/`; the gates are fixed before the build; new code gets mutants (`scripts/*_mutants.py`, not run in CI); claims are measured, with the conditions next to the number, and a false claim is corrected in place. After a change to the program, `python3 scripts/site.py` rewrites the generated parts of this file and of the pages.

## Licence

[EUPL-1.2](LICENSE).
