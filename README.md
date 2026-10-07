# table

[![ci](https://github.com/alpibrusl/cancho-table/actions/workflows/ci.yml/badge.svg)](https://github.com/alpibrusl/cancho-table/actions/workflows/ci.yml)

**A deterministic data primitive for AI agents.** `table` queries CSV and TSV data with explicit semantics, bounded resources, structured refusals and machine-verifiable capabilities. It is a command line, not a chat box: you (or an agent) give it flags, it gives back exact answers or a named refusal with a repair. For a person with a CSV and a question: ask it, get an exact answer. One small binary, written in [cancho](https://github.com/alpibrusl/cancho). The [project page](https://alpibrusl.github.io/cancho-table/) has the same in pages.

**Status: early.** It works and is tested; what it cannot do yet is listed below.

## One complete flow

Ask, get refused with a repair, run the repair. The repair is the corrected command, and the output below is what the built program prints.

<!-- gen:flow -->
```console
# Ask: the customer and bytes of the orders with status 200
$ table --where "status = 200" --select Customer,bytes orders.csv
{"rule": "select.unknown-column", "hint": "pick from detail.available", "repair": {"kind": "choose", "options": [{"argv": ["table", "--where", "status = 200", "--select", "customer,bytes", "orders.csv"]}]}}
# exit status 3; the rule, hint, repair of the JSON line it prints
# The tool offered the corrected command. Run it:
$ table --where 'status = 200' --select customer,bytes orders.csv
{"ok":true,"command":"table","schema":"table.v2","data":{"columns":["customer","bytes"],"rows":[["Doe, Jane","512"],["Doe, Jane","2048"],["Zed","64"]],"row_count":3,"truncated":false,"next":null},"meta":{"version":"0.3.0"}}
```
<!-- /gen:flow -->

## Why table?

Most data tools optimise for flexibility. `table` optimises for predictability.

* **Explicit types.** A column is text unless you write `:int` (an exact 64-bit integer) or `:dec(S)` (an exact decimal with S fractional digits, in `--where`); a cell that is not one is refused. [docs/filter.md](docs/filter.md)
* **Explicit operations.** One plan from flags, no expressions or functions. [docs/filter.md](docs/filter.md)
* **Bounded resource use.** Rows, line and record size, groups, distinct values and group state each have a limit with its own rule. Peak memory is about 2 MB on a 31.7 MB file (Linux) and 1.6 to 1.8 MB on a 1 GB file (Mac, one core). `table introspect` lists the limits; [benchmarks](https://alpibrusl.github.io/cancho-table/benchmarks.html)
* **No implicit network access.** The authority row, derived by `cancho authority`, lists what the program can reach: no `net_out`, `net_in`, `ffi` or `clock`, and nothing written to disk. CI fails if the binary differs from the committed [`manifests/table.authority.json`](manifests/table.authority.json). [docs/architecture.md](docs/architecture.md)
* **Machine-readable errors.** One JSON line against a schema (`table.v2`); a refusal is `{code, rule, message, hint, repair, detail}`. [docs/refusals.md](docs/refusals.md)
* **Repair hints.** A refusal says how to fix the request: a command to run, a choice of commands, or why there is none. [docs/refusals.md](docs/refusals.md)
* **Capability introspection.** `table introspect` prints the flags, limits, rules and authority; `table skill` prints a guide for an agent. Both come from the tables the parser runs on.
* **The same bytes on any number of cores.** `--threads N` gives the one-core answer, refusals included. [docs/parallel.md](docs/parallel.md)

## Two-minute quick start

You need `git`, Rust, `clang` and `python3`. On a 16-core Mac with Rust installed and its dependencies already downloaded, the compiler built in 19 seconds and `table` in 3; the clones and a first-time dependency download are extra, so allow a few minutes the first time.

```sh
git clone https://github.com/alpibrusl/cancho                           # the compiler
git clone https://github.com/alpibrusl/cancho-table && cd cancho-table
REV=$(sed -n 's/^cancho *= *"\([0-9a-f]*\)".*/\1/p' cancho.toml)        # the compiler these sources need
(cd ../cancho && git fetch -q origin && git checkout "$REV" && cargo build --release -p cancho)
export PATH=$PWD/../cancho/target/release:$PWD/build:$PATH
cancho build                                                            # builds build/table

cat > orders.csv <<'EOF'
id,customer,status,bytes
1,"Doe, Jane",200,512
2,Acme,404,
3,"Doe, Jane",200,2048
4,Acme,500,128
5,Zed,200,64
EOF
```

Three commands, then a refusal:

<!-- gen:qs -->
```console
$ table orders.csv
{"ok":true,"command":"table","schema":"table.v2","data":{"headers":["id","customer","status","bytes"],"column_count":4,"row_count":5,"truncated":false},"meta":{"version":"0.3.0"}}
$ table --select customer,bytes --format csv orders.csv
customer,bytes
"Doe, Jane",512
Acme,
"Doe, Jane",2048
Acme,128
Zed,64
$ table --where "bytes != ''" --group customer --agg count,sum:bytes --sort -sum:bytes --format csv orders.csv
customer,count,sum:bytes
"Doe, Jane",2,2560
Acme,1,128
Zed,1,64
$ table --where "status = 200" --select Customer,bytes orders.csv
{"rule": "select.unknown-column", "hint": "pick from detail.available", "repair": {"kind": "choose", "options": [{"argv": ["table", "--where", "status = 200", "--select", "customer,bytes", "orders.csv"]}]}}
# exit status 3; the rule, hint, repair of the JSON line it prints
```
<!-- /gen:qs -->

## What you can do now

Every example runs on the five-line file above, and the output is what the program prints.

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

### Filter on decimals

`price:dec(2)` reads the column as exact decimals with two fractional digits: `12.5` and `12.50` are one value, and the literal is read at the same scale. A cell with more digits than the scale is refused, never rounded, and the repair is the command with the scale the column needs. This second example uses a small `prices.csv`.

<!-- gen:t-decimal -->
```console
$ table --where "price:dec(2) >= 12.50" --format csv prices.csv
item,price
book,12.50
lamp,12.5
$ table --where "price:dec(1) >= 12.5" prices.csv
{"rule": "value.decimal-scale", "hint": "declare a larger scale, :dec(N) with N the most fractional digits the column holds", "repair": {"kind": "choose", "options": [{"argv": ["table", "--where", "price:dec(2) >= 12.5", "prices.csv"]}]}}
# exit status 8; the rule, hint, repair of the JSON line it prints
```
<!-- /gen:t-decimal -->

### Sort rows

`--order-by` takes keys: `-` before a name is descending, `:int` compares whole numbers, and rows that tie keep their order in the file. With `--limit` or `--top` it keeps only the best rows, so memory stays flat. A full sort holds every row, up to `--max-sort-rows` (1,000,000 by default); past that it refuses and suggests `--top`, a smaller `--limit` or a `--where`. There is no sort that spills to disk.

<!-- gen:t-sort -->
```console
$ table --where "bytes != ''" --order-by -bytes:int,status --limit 3 --select id,customer,bytes --format csv orders.csv
id,customer,bytes
3,"Doe, Jane",2048
1,"Doe, Jane",512
4,Acme,128
```
<!-- /gen:t-sort -->

### Count and sum by a key

`count`, `sum`, `min`, `max` and `distinct`. Sums are exact at any width: a sum past 64 bits is printed in full (up to 28 digits), and it is the same on any number of cores.

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

Every refusal has a fixed rule name, an exit status and, where one exists, a repair. A cell that is not a whole number, an empty one too, is refused with its row, line and column; there is no repair here, because what the cell was meant to be is not known.

<!-- gen:t-refuse -->
```console
$ table --where "bytes:int > 100" --select id orders.csv
{"rule": "value.not-integer", "hint": "keep out the rows with such a cell with --where, or do not ask for an integer of this column (a cell with a point is a decimal: declare the column :dec(N))", "detail": {"path": "orders.csv", "context": "where", "column": "bytes", "row": 2, "line": 3, "value": "", "value_truncated": false}}
# exit status 8; the rule, hint, detail of the JSON line it prints
```
<!-- /gen:t-refuse -->

## Built to be read by an agent

`table introspect` and `table skill` are generated from the same tables the code parses its flags with, so they cannot disagree with the program. See [docs/refusals.md](docs/refusals.md) for how an agent should act on each kind of refusal, and [the project page](https://alpibrusl.github.io/cancho-table/#agent) for excerpts of both.

## What it cannot do yet

* **Decimals are only half built.** `:dec(S)` works in `--where`. Summing, min, max, mean, grouping by and `distinct` of a decimal column, printing decimals, and floats are decided and in progress ([docs/numbers.md](docs/numbers.md)); until then those are refused. In `sum`, `min` and `max`, cells must be whole numbers.
* **One core for a sort.** `--threads` is accepted with `--order-by`, and gives the same bytes, but the sort runs on one core. There is no sort that spills to disk.
* **No joins**, and **one input file** at a time, named on the command line (no standard input).
* **No JSON lines**, no Parquet. Only CSV and TSV (comma, tab or semicolon).
* **No guessing.** Every column is text unless you say `:int`. No mean, no computed or renamed column, no `or` in a filter.

JSON lines, joins, `mean`, decimals and standard input are on the [backlog](docs/backlog.md), in rough order. Nothing there is promised.

## How fast

Supporting evidence, not the point: the predictability costs little speed. A *core* is one of the independent workers inside a processor. By default `table` uses one; `--threads 16` uses sixteen. [DuckDB](https://duckdb.org) is a full database engine and a popular, fast way to query a CSV, so it is a fair yardstick. On these four questions one core of `table` is about as fast as, or faster than, DuckDB on all sixteen. With many distinct keys in a group-by, DuckDB on all cores wins.

Time to answer on the same 1,000,000-row, 32 MB CSV, in seconds (lower is better):

| question | table, 1 core | table, 16 cores | csvtk | DuckDB, 1 core | DuckDB, 16 cores |
|---|---:|---:|---:|---:|---:|
| filter rows | 0.046 | **0.0087** | 0.293 | 0.170 | 0.075 |
| count by a key | 0.057 | **0.0099** | 0.181 | 0.132 | 0.065 |
| sum by a key | 0.062 | **0.011** | 0.395 | 0.141 | 0.067 |
| pick 2 columns | 0.050 | **0.011** | 0.188 | 0.190 | 0.078 |

Apple-silicon Mac, 16 cores. Best of five runs, output thrown away, every answer checked against a Python answer first. csvtk 0.38.0 on one core (its filter is `filter` piped to `grep`); DuckDB 1.5.6; DuckDB's 16-core times for filter and pick-2-columns are from an earlier run on the same machine. Miller is not installed on the Mac; on a different machine (Linux, 6 shared cores) it took 0.300 s to filter, 0.438 s to count and 0.649 s to sum, against `table`'s 0.074, 0.128 and 0.140 s there.

**Where it is slower:** a group-by with a million distinct keys is 3.4 times slower than DuckDB on one core and 9.4 times slower than DuckDB on 16 (Mac); `distinct` over a column of unique values is 1.4 times slower on one core; and sorting 1,000,000 rows takes 0.41 s on one core (about the same as DuckDB on one core, 0.43 s) against DuckDB's 0.12 s on all 16, because the sort runs on one core. All the tables, the harder cases and how to rerun them: [docs/benchmarks.html](https://alpibrusl.github.io/cancho-table/benchmarks.html).

## Learn more

| | |
|---|---|
| [project page](https://alpibrusl.github.io/cancho-table/) | the flow, the agent view and the examples, in pages |
| [docs/refusals.md](docs/refusals.md) | the refusal and repair protocol: `{code, rule, message, hint, repair, detail}`, what each repair kind means, every rule |
| [docs/architecture.md](docs/architecture.md) | the pieces, and the capability model: what the authority row says and why each label is there |
| [docs/select.md](docs/select.md), [docs/filter.md](docs/filter.md) | how `--select`, `--where`, `--group` and `--agg` behave |
| [docs/parallel.md](docs/parallel.md) | `--threads`: how it gives the one-core answer |
| [benchmarks](https://alpibrusl.github.io/cancho-table/benchmarks.html) | the latest timings, with conditions, and how to rerun them |
| [docs/backlog.md](docs/backlog.md) | what is not done yet |
| [docs/reference.md](docs/reference.md) | the long description of every flag and of the layout |
| [docs/history.md](docs/history.md), [docs/adversarial.md](docs/adversarial.md) | for contributors: what was measured and changed, and the harder cases |

## Contributing

Every change goes through what CI runs: `cancho fmt --check tools generated`, `cancho build`, `scripts/schemas.py --check`, `scripts/manifest.py --check`, the conformance tests, and `scripts/site.py --check`. Design before code, in `docs/`; the gates are fixed before the build; new code gets mutants (`scripts/*_mutants.py`, not run in CI); claims are measured, with the conditions next to the number, and a false claim is corrected in place. After a change to the program, `python3 scripts/site.py` rewrites the generated parts of this file and of the pages.

## Licence

[EUPL-1.2](LICENSE).
