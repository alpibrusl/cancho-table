<p align="center"><img src="docs/assets/cancho-table-logo-256.png" alt="cancho-table" width="200"></p>

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

* **Explicit types.** A column is text unless you write `:int` (an exact 64-bit integer), `:dec(S)` (an exact decimal with S fractional digits) or `:float` (the nearest double, in `--where` and in every aggregate); a cell that is not one is refused. [docs/filter.md](docs/filter.md)
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
item,category,price
book,office,12.50
lamp,home,12.5
$ table --where "price:dec(1) >= 12.5" prices.csv
{"rule": "value.decimal-scale", "hint": "declare a larger scale, :dec(N) with N the most fractional digits the column holds", "repair": {"kind": "choose", "options": [{"argv": ["table", "--where", "price:dec(2) >= 12.5", "prices.csv"]}]}}
# exit status 8; the rule, hint, repair of the JSON line it prints
```
<!-- /gen:t-decimal -->

### Filter and find the extremes of floats

`temp:float` reads the column as the nearest double (ties to even), and compares doubles, not text: `21.5`, `21.50` and `2.15e1` are one value. `min` and `max` print as the shortest decimal that reads back to the same double; rows are copied as they are. `NaN`, `inf`, an out-of-range number and a cell that is not a number are refused, never skipped, with the row, line and column; for `NaN` the hint is the idiom that keeps those rows out (`temp != 'NaN' and temp:float >= 21.5`), and there is no repair, because what the cell was meant to be is not known. `min`, `max`, `distinct`, `count`, `sum` and `mean` work on floats. A column really named `x:float` is written `x\:float`.

<!-- gen:t-float -->
```console
$ table --where "temp != 'NaN' and temp:float >= 21.5" --format csv readings.csv
sensor,temp
a,21.5
c,21.50
d,2.15e1
$ table --where "temp != 'NaN'" --agg "min:temp:float,max:temp:float,distinct:temp:float" --format csv readings.csv
min:temp,max:temp,distinct:temp
-3.4,21.5,3
$ table --where "temp:float >= 21.5" readings.csv
{"rule": "value.not-finite", "hint": "keep them out with --where first: x != 'NaN' and x:float > 5 never reads the NaN cell as a number", "repair": {"kind": "none", "reason": "what the cell was meant to be is not known"}, "detail": {"path": "readings.csv", "context": "where", "column": "temp", "row": 6, "line": 7, "value": "NaN", "value_truncated": false}}
# exit status 8; the rule, hint, repair, detail of the JSON line it prints
```
<!-- /gen:t-float -->

### Sum and average floats, exactly

`sum:x:float` and `mean:x:float` are exact: every cell is added without rounding and the total is rounded once to the nearest double (ties to even), so the answer does not depend on the order of the rows or on the number of cores. Adding the same numbers left to right in double precision gives `0.0` for group `a` and `0.9999999999999999` for group `h`. A `mean` takes no `@N` (it is a double; `mean:x:float@2` is refused). A sum beyond the largest double, about 1.8e308, is `agg.float-overflow`, which names the group; the mean of that group still exists. On a million rows DuckDB's DOUBLE sum gave a different answer at 1, 4 and 16 threads, none equal to the exact one: see the [benchmarks](https://alpibrusl.github.io/cancho-table/benchmarks.html#floats).

<!-- gen:t-floatsum -->
```console
$ table --group g --agg "sum:x:float,mean:x:float" --format csv drift.csv
g,sum:x,mean:x
a,2.0,0.5
h,1.0,0.1
$ table --group g --agg sum:x:float big.csv
{"rule": "agg.float-overflow", "hint": "sum fewer rows with --where, group by more columns, or sum in a smaller unit (the mean of the same group is a number: mean:COL:float)", "repair": {"kind": "none", "reason": "which rows to leave out is not known"}, "detail": {"path": "big.csv", "context": "sum", "column": "x", "group": "a", "group_truncated": false}}
# exit status 8; the rule, hint, repair, detail of the JSON line it prints
```
<!-- /gen:t-floatsum -->

### Sum and average decimals

`sum:price:dec(2)` is exact and printed at the column's scale. `mean:price:dec(2)@3` is the exact quotient rounded half to even at three digits (`@N` is required for a whole-number column), the same on any number of cores. `distinct` counts by value, so `12.5` and `12.50` are one. Sums are exact at any width. A column whose name really ends in `:int`, `:dec(2)` or `@2` is written with a backslash: `sum:x\:dec(2)`.

<!-- gen:t-decagg -->
```console
$ table --group category --agg "sum:price:dec(2),mean:price:dec(2)@3" --format csv prices.csv
category,sum:price,mean:price
home,15.70,7.850
office,14.00,7.000
$ table --agg "min:price:dec(2),max:price:dec(2),distinct:price:dec(2)" --format csv prices.csv
min:price,max:price,distinct:price
1.50,12.50,3
```
<!-- /gen:t-decagg -->

### Group and sort by numbers

`--group` and `--order-by` take the same suffixes as `--where`: `:int`, `:dec(S)` and `:float`. The key is the **value** of the cell, not its text. Here `1.5`, `1.50`, `01.5` and `+1.5` are one group at `:dec(2)`, written back at the column's scale, where grouping by text gives four groups for them. Groups come out in numeric order, and `--order-by -price:dec(2)` puts `12.50` before `3.2`, where a text sort puts `3.2` first; rows that tie keep their order in the file. On a million rows with a number written in two spellings (20,001 values), csvtk and Miller grouped by text and reported 22,002 groups: see the [benchmarks](https://alpibrusl.github.io/cancho-table/benchmarks.html#typed).

<!-- gen:t-typedkeys -->
```console
$ table --group level --agg count --format csv spell.csv
level,count
+1.5,1
01.5,1
1.5,1
1.50,1
2.25,1
$ table --group "level:dec(2)" --agg count --format csv spell.csv
level,count
1.50,4
2.25,1
$ table --order-by -price --limit 3 --select item,price --format csv prices.csv
item,price
cup,3.2
book,12.50
lamp,12.5
$ table --order-by "-price:dec(2)" --limit 3 --select item,price --format csv prices.csv
item,price
book,12.50
lamp,12.5
cup,3.2
```
<!-- /gen:t-typedkeys -->

### Check what is in a column

`--report types` reads the file once and prints, for each column, how many cells are empty, whole numbers, decimals, floats or something else, the widest decimal scale, the first row and line that each declaration would refuse, and a **suggested declaration**: `:int`, `:dec(S)`, `:float`, or `none` when a cell is not a number. It prefers the exact reading: a column of `0.1`-shaped cells is `:dec(1)`, not `:float`. It never changes anything; no other flag reads a column as a type unless you write the suffix, and the report is the same on any number of cores. Here `price` has an `n/a` in its last row (suggestion `none`, first offender row 4, line 5), `bytes` has a `1e3` (`:float`, not `:int`) and `cents` has an empty cell (still `:int`; the empty cell is counted apart and refused by any numeric reading). On a million rows with three late wrong cells, DuckDB's type sniffing, which looks at a sample of 20,480 rows, still said `DOUBLE` and `BIGINT`, and its `SUMMARIZE` failed at line 900,001; Miller's `summary` showed the mixtures without counts or rows ([benchmarks](https://alpibrusl.github.io/cancho-table/benchmarks.html#report)).

<!-- gen:t-report -->
```console
$ table --report types --select price,bytes,cents --format csv dirty.csv
name,cells,empty,int,dec,float,not_finite,other,dec_max_scale,int_digits_max,widest,suggest,first_empty_row,first_empty_line,first_not_int_row,first_not_int_line,first_not_dec_row,first_not_dec_line,first_not_float_row,first_not_float_line,first_other_row,first_other_line,first_other_cell,first_other_cell_truncated
price,4,0,0,3,0,0,1,2,2,5,none,,,1,2,4,5,4,5,4,5,n/a,false
bytes,4,0,3,0,1,0,0,0,2,3,:float,,,4,5,4,5,,,,,,
cents,4,1,3,0,0,0,0,0,3,3,:int,4,5,,,,,,,,,,
```
<!-- /gen:t-report -->

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

`count`, `sum`, `min`, `max`, `mean` and `distinct`, of whole numbers and of `:dec(S)` columns. Sums are exact at any width: a sum past 64 bits is printed in full (up to 28 digits), and it is the same on any number of cores.

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

* **Numbers are mostly built.** `:int`, `:dec(S)` and `:float` work in `--where`, `--agg`, `--group` and `--order-by`. Columns of 15- to 17-digit numbers are read by an exact reader that allocates nothing on the paths a real column takes (Clinger, then Eisel-Lemire, then an exact fallback); it matched Python's `float()` on every cell of each differential run, the largest of 101,400,000 cells, and costs 29.6 ns a cell against 1,151 ns before (one Mac core, load average 6.7 to 13.5; [conditions](https://alpibrusl.github.io/cancho-table/benchmarks.html#float-reader)).
* **A sort uses more cores only for a whole sort written as CSV.** With `--order-by`, `--format csv` and no `--top`, `--limit` or `--from`, `--threads` above 1 sorts in parallel and gives the same bytes; pages, `--top` and JSON output run on one core. A threaded sort peaks at 11.8 to 14.8 times the size of a 39.55 MB file at 16 threads on the Mac, against 4.4 to 6.2 times for the sequential sort. There is no sort that spills to disk.
* **No joins**, and **one input file** at a time, named on the command line (no standard input).
* **No JSON lines**, no Parquet, no `--query` string. Only CSV and TSV (comma, tab or semicolon). Readers and a query form are designed in [docs/readers.md](docs/readers.md) and [docs/query.md](docs/query.md), not built.
* **No guessing.** Every column is text unless you say `:int`. No mean, no computed or renamed column, no `or` in a filter.

JSON lines, joins, `mean`, decimals and standard input are on the [backlog](docs/backlog.md), in rough order. Nothing there is promised.

## How fast

Supporting evidence, not the point: the predictability costs little speed. A *core* is one of the independent workers inside a processor. By default `table` uses one; `--threads 16` uses sixteen. [DuckDB](https://duckdb.org) is a full database engine and a popular, fast way to query a CSV, so it is a fair yardstick. On these four questions one core of `table` is about as fast as, or faster than, DuckDB on all sixteen. With many distinct keys in a group-by the answer depends on which DuckDB time you compare with: see "Where it is slower" below.

Time to answer on the same 1,000,000-row, 32 MB CSV, in seconds (lower is better):

| question | table, 1 core | table, 16 cores | csvtk | DuckDB, 1 core | DuckDB, 16 cores |
|---|---:|---:|---:|---:|---:|
| filter rows | 0.046 | **0.0087** | 0.293 | 0.170 | 0.075 |
| count by a key | 0.057 | **0.0099** | 0.181 | 0.132 | 0.065 |
| sum by a key | 0.062 | **0.011** | 0.395 | 0.141 | 0.067 |
| pick 2 columns | 0.050 | **0.011** | 0.188 | 0.190 | 0.078 |

Apple-silicon Mac, 16 cores. Best of five runs, output thrown away, every answer checked against a Python answer first. csvtk 0.38.0 on one core (its filter is `filter` piped to `grep`); DuckDB 1.5.6; DuckDB's 16-core times for filter and pick-2-columns are from an earlier run on the same machine. Miller is not installed on the Mac; on a different machine (Linux, 6 shared cores) it took 0.300 s to filter, 0.438 s to count and 0.649 s to sum, against `table`'s 0.074, 0.128 and 0.140 s there.

**Where it is slower, and what it costs.** Four changes were merged after the table above was measured; each number below has its conditions in the [benchmarks](https://alpibrusl.github.io/cancho-table/benchmarks.html#changes).

* **Group-by with many distinct keys.** Against DuckDB's *unsorted* time on Linux at 6 threads (three physical cores, load average 1.1 to 1.4, minimum of 5, 1,000,000 rows) `table` takes 1.77 and 1.86 times as long for a count and a sum over 1,000,000 keys and 1.46 times for `distinct` over unique values; at 100,000 keys, 1.10 and 1.07 times. Against DuckDB's *ordered* time (its groups sorted by the key, which is what `table` returns) it is level on the million-key count and sum (1.00 and 1.01 times). On the Mac at 16 threads it is ahead of DuckDB's unsorted time on all five cases (0.35 to 0.52 of its time). Before this change it was 3.4 times slower than DuckDB on one Mac core on the million-key count and 9.4 times slower than DuckDB on 16 ([table](https://alpibrusl.github.io/cancho-table/benchmarks.html#groups)).
* **Sorting.** A whole sort written as CSV with `--threads 16` takes 0.064 s on a text key and 0.053 s on a whole number (1,000,000 rows of 40 bytes, Mac, load average 6.6 to 8.4, minimum of 5), against DuckDB's default at 0.139 s and 0.120 s. On Linux at 6 threads it is level with DuckDB on one key (0.287 s against 0.293 s) and behind it on two keys (0.459 s against 0.385 s). At 10,000,000 rows (an earlier Mac run) DuckDB's default, 0.61 s, was ahead of the integrated sort, 0.81 s. Pages, `--top` and JSON output still sort on one core ([table](https://alpibrusl.github.io/cancho-table/benchmarks.html#psort)).
* **Quoted fields with newlines.** On a 114 MB file of 1 to 10 KB fields with newlines inside, `--threads` now helps: 0.183 s on one thread and 0.051, 0.032 and 0.024 s on 4, 8 and 16 (Mac, load average 3.8 to 6.1, minimum of 5); 0.174 s, 0.069 s and 0.060 s on 1, 3 and 6 threads on Linux. A file whose quoted lines are themselves valid records of the same width gets no speed-up. The 1 GB group-by gains little: 0.88 of the old time at 4 threads and 0.84 at 16 on the Mac, and no gain is established on Linux ([table](https://alpibrusl.github.io/cancho-table/benchmarks.html#speculation)).
* **Memory.** A refused threaded grouping peaks higher in resident memory than before: 325 to 471 MB at one byte short of the limit and 181 to 360 MB with `--max-state-bytes` at 2,000,000 (million-key count, Mac, 16 threads). `--max-state-bytes` counts accounted bytes, not resident memory, and no multiple of it is claimed as a bound on resident memory.
* **Instructions.** Cells that do not group retire 0.8 to 0.9 percent more instructions at 4 threads on the Mac (+14 million of 1,629 million); the two-decimal float filter retires 0.3 percent more on Linux.

All the tables, the harder cases and how to rerun them: [docs/benchmarks.html](https://alpibrusl.github.io/cancho-table/benchmarks.html).

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
