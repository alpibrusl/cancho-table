# A matrix benchmark: a design for testing "analytics over files" as a thesis

> **Status (2026-10-22): designed, and the harness built and ready to run; no matrix has been published and no clock was run.** The pull request adds this document, the harness
> (`scripts/bench_matrix.py`, `scripts/bench_matrix_forms.py`, `scripts/bench_matrix_tools.json`, `scripts/bench_matrix_mutants.py`, `tests/conformance/test_bench_matrix.py`) and links; it does
> not touch the tool, `docs/benchmarks.html` or the numbers of the README. **It makes no claim about how fast or how correct `table` or any other tool is on any task of the matrix.**
> The kinds a shared virtual machine can settle (expressible, correct, deterministic, at a limit, instructions, error contract) were run once on one cloud VM, for the contenders installed
> there, and are **recorded, not published** (section 15). **Every clock kind (peak resident set, time) is NOT RUN**: on the Mac and on gram (no access from here), and on every contender that is not installed.
> Every number quoted from this repository is given with its source file and the conditions under which it was measured; a number whose conditions could not be found is not quoted. Every
> fact about a tool that is not ours carries a URL and an access date (2026-10-22), or is marked **unverified**.
>
> The base is `origin/main` at `6c0291c`, plus the correction of the open pull request #90 (`docs/backlog.md`, "Corrected (2026-10-22)"), which is **not merged** and which this document does not
> edit. One sentence of that correction is itself contradicted by `docs/joins.md`; see section 14, item 1. *(The first version of this file, of the same day, was design only; section 15 is what the
> harness added.)*

## 0. The short version

| question | decision | why, in one line |
|---|---|---|
| what is being tested | the **hypothesis** H: "`table`'s moat is analytics over files (query CSV/TSV/JSONL directly: filter, group, aggregate, sort, join, dates, exact numbers, bounded memory, usable by an agent)". It is **not** a claim of this document | the matrix may refute it, and section 6 says in advance which cells would |
| shape | **rows are tasks, columns are contenders, and each cell has a kind** (eight kinds, section 4): expressible, correct, deterministic, behaviour at a memory limit, peak memory, time, instructions, error contract | speed is one kind of eight; a matrix of times only would repeat `docs/named-benchmarks.md` |
| what the cloud VM can settle | expressibility, correctness against an independent reference, byte-identity across threads and repeats, refusal behaviour, and **instruction counts by callgrind** for the tools that run under valgrind | none of these depends on the load of a shared machine |
| what it cannot settle | **clock time and, with it, any "faster than"**; hardware counters | the VM is shared and has no counter (`docs/named-benchmarks.md` 18.3; `docs/joins.md` section 22 (J7) header). Those cells are **blank: NOT RUN until the quiet Mac or gram** |
| presentation | **no composite score, no ranking, no colour for "winner"**; each cell shows its kind and conditions; a cell not measured is blank with the reason; where `table` loses the cell says so | section 5 |
| the thesis test | a **falsification table** (section 6) fixed before any cell is measured: the cells where `table` must win or tie for H to stand, and the cells where one loss refutes it | section 6 |
| honest prior | on the repository's own recorded numbers DuckDB is ahead on high-cardinality group-by on Linux (section 2.1, E1) and on breadth (SQL, windows, Parquet, spill, per the backlog) | H is open, not established |
| staging | MX0 to MX9; **MX0 to MX5 and the machinery of MX7 are built** (section 8, 15), their gates written first and shown failing; the clock matrix itself and every contender that is not installed are parked | section 8, 15 |
| output | this document, `scripts/bench_matrix.py` (the driver), `scripts/bench_matrix_forms.py` (tasks, references, the judge, the commands), `scripts/bench_matrix_tools.json` (contenders, pins, install commands); a committed JSON `docs/assets/matrix-benchmark.json` and a section of `docs/benchmarks.html` only when a matrix is published | section 10 |
| run it | `python3 scripts/bench_matrix.py --check-tools`, then one command on the cloud VM (no clock) or on a quiet machine (`--clock`) | section 15.2 |

## 1. Why a matrix, and what it can and cannot do for the thesis

`docs/named-benchmarks.md` compares `table` with four contenders on someone else's questions and, for the clock, only on speed. Two things follow from that
design that a matrix is meant to repair, and one it cannot repair.

* **Speed against one engine is a narrow test of a broad claim.** The thesis H is about a *kind of work* (analytics over files) done *under constraints* (exact,
  bounded, deterministic, agent-usable). A time on one question against DuckDB says nothing about the constraints. The matrix puts the constraints in cells of their
  own (kinds c, d, h of section 4).
* **A refusal is a result** (`docs/named-benchmarks.md` 3.4). A task a tool cannot express, or refuses at a limit, is a cell, not a hole. The matrix is where
  H gets its honest negative space: the tasks `table` does not do (median, window, computed column, regex) are rows, so a table of wins cannot be read as a table of the
  whole.
* **What it cannot do:** a matrix cannot establish a *moat*. A moat is a claim about what others cannot easily do, and about users' needs; the matrix measures
  what the contenders do on a stated set of tasks, on stated machines, at a stated version. The most a cell can do is *support* or *fail to refute* H on that task.
  Section 6 words it that way, and section 13 repeats it.

## 2. Sources, and the status of every number quoted

### 2.1 From this repository (read 2026-10-22; conditions as the source states them)

| id | fact | source and conditions |
|---|---|---|
| E1 | Group-by with many keys, `table` time over DuckDB's **unsorted** time, Linux, 6 threads: 1.10 (count) and 1.07 (sum) at 100,000 keys; **1.77** (count) and **1.86** (sum) at 1,000,000 keys; **1.46** for `distinct` over unique values. Over DuckDB's **ordered** time: 1.00 and 1.01 on the million-key cases, 1.39 on `distinct`. On the Mac at 16 threads `table` is ahead of DuckDB's unsorted time on all five cases (0.35 to 0.52) | `docs/benchmarks.html` section "A range-partitioned group-by (#35)" and `README.md` "Where it is slower": Linux i7-1260P, cores 0-5 (three physical cores), `--threads 6`, DuckDB `threads=6`, **load average 1.1 to 1.4, shared box**, 1,000,000 rows (cells B1 to B4, I1 of `scripts/adversarial.py`), minimum of 5 interleaved, answers checked against Python first; Mac: Apple silicon, 16 cores, `--threads 16`, load average 1.5 to 2.4 |
| E2 | The exact float sum over 2,000,000 doubles: `table --agg sum:x:float` printed the exact sum at 1, 2, 4, 8 and 16 threads in every run; DuckDB `sum` on DOUBLE printed **7 different values over 15 runs on the Mac (1 equal to the exact sum) and 8 over 15 on Linux (0 equal)**. DuckDB's `kahan_sum` was exact at 1 thread on both machines and varied at 2 threads on both (Mac: 2 of 3 runs exact at 2 threads; Linux: 0 of 3), and **DuckDB's `sum` of the same column read as `DECIMAL(38,12)` was the same value in every run at every thread count** | `docs/assets/threads-vs-duckdb.json` (Mac: Apple M4 Max, 16 cores, load average 32.27 before and 34.75 after) and `docs/assets/threads-vs-duckdb-linux.json` (i7-1260P, 16 cores of which 6 usable, load 14.1 before and 25.36 after); `table` at `db42054`; DuckDB v1.5.6; file seed 20261015, 54,304,130 bytes, sha256 `8ea33d01...fb9209`; **3 runs per thread count, no timings** |
| E3 | The four everyday scans, Linux: `table` 1.6 to 3.1 times faster than DuckDB at one thread and 2.3 to 2.8 times at six; and 1.0 GB file: peak resident 2.4 MB (`table` -t1) against 361.7 MB (DuckDB default) for the filter, 2.1 MB against 218.4 MB for the group-count | `docs/benchmarks.html` "Linux, with DuckDB" (measured on `main` at `7d04b9f`, before #32 to #35) and `docs/bench-linux-duckdb.md` ("1 GB cells": load 1.64 / 11.23 / 11.58 at the start, minimum of 3, 1,077 MB file, DuckDB 6 threads via a wrapper; `table` -t1) |
| E4 | Join, Mac, one thread of `table` against DuckDB's one thread and default: integer keys 1M x 100k, `table` 0.159 s, DuckDB 0.247 s (1 thread) and 0.133 s (default); 1M x 1M, `table` 0.473 s, DuckDB 0.353 s and 0.200 s. csvtk 0.391 and 0.951 s. Peak resident (MB) `table` 17, 21, 146, 189 against DuckDB 73, 70, 112, 163 | `docs/joins.md` 16.5: Apple M4 Max, 16 cores, **loaded by other sessions' work (load average 8.5 to 9.4)**, minimum of 7 interleaved, output to /dev/null, every output checked against a Python join first, `table` at `--max-state-bytes 1073741824`. **Not quiet-machine figures** |
| E5 | NB3 ran the five join questions of db-benchmark at 1e6 rows: 35 of 35 (question, contender) answers passed the exact fingerprint (`table`, `table --join-sorted`, DuckDB at 1 thread and default, csvtk, Miller, pandas). No time claimed | `docs/named-benchmarks.md` 19.3: one shared cloud VM, Xeon 2.1 GHz, 4 logical CPUs, `publishable: false` |
| E6 | NB2 ran the groupby questions q1 to q5 and q10 at 1e6 rows: 41 of 41 pairs passed the gate (20 exact, 6 value-equal in another spelling, 15 only by tolerance). **csvtk was killed by the kernel's OOM killer on the six-key `summary` at 1e5 rows (13,771,076 kB resident, 16 GB VM)** | `docs/named-benchmarks.md` 18.3, same VM, not publishable |
| E7 | On the harder cases csvtk's `summary` of a 100,000-key sum had a peak resident of 14,474.3 MB and csvtk was killed (rc -9) on the million-key cell B3 | `docs/bench-linux-duckdb.md` (cells B2, B3, first batch; gram, 1,000,000 rows, 40 MB file, 31 GB machine, load 4.84 / 4.61 / 4.22 at the start of the batch and a burst to 24 later, minimum of 5) |
| E8 | There is no sort that spills: past `--max-sort-rows` or `--max-state-bytes` the answer is `limit.too-many-sort-rows` / `limit.state-too-large`, by design. Defaults: `--max-groups` 100,000 (ceiling 1,000,000), `--max-state-bytes` 64 MiB | `docs/backlog.md` ("No external sort"), `docs/filter.md` ("Bounded, three ways"), `README.md` |
| E9 | A refusal is a stable tag, an exit status (2, 3, 4, 8, 1) and, where one exists, a repair of kind `retry`, `choose` or `none`; with `--format csv` the verdict is one line on standard error and the rows written before it stand | `docs/refusals.md` |
| E10 | `scripts/join_callgrind.py` counts instructions with `valgrind --tool=callgrind` (`Collected : N`) at two row counts and takes the slope, "exactly and with no regard to the load of the machine"; the same method was used on a 4-core shared Linux VM in `docs/joins.md` section 22 (J7) header ("figures are bytes, instruction counts (callgrind, exact whatever the load) and counts of cases") | `scripts/join_callgrind.py` docstring; `docs/joins.md` section 22 (J7), header: "one Linux x86-64 virtual machine ... valgrind 3.22" |
| E11 | The tool needs a build: the quick start builds the pinned compiler with `cargo` and then `cancho build`. **No release exists** (GitHub releases of `alpibrusl/cancho-table`: an empty list, read 2026-10-22) | `README.md` "Two-minute quick start"; GitHub API |
| E12 | The authority row (`manifests/table.authority.json`) lists the effects the program can reach: `args`, `conc`, `dir_read`, `err_write`, `file_read`, `fs_read`, `heap`, `io_read`, `io_write`; `bounded: true`; CI fails if the binary differs from the committed row | `manifests/table.authority.json`, `README.md` ("No implicit network access") |
| E13 | What `table` does not do: window functions, Parquet, spill, a join over more than two files in one plan, a threaded probe (J6), `--report` over a join, computed columns, `like`, `null`, subqueries, `@week`, text `min`/`max`, `median`, `stddev`, per-group top-k, `corr` | `README.md` "What it cannot do yet", `docs/or-and-columns.md` section 8, `docs/named-benchmarks.md` 14, PR #90's correction |
| E14 | On a ragged row (a record with a different number of fields from the header) the read counts it and never uses it; the tally (records, ragged, first ragged row and line) is reported | `docs/filter.md` (plan description), `docs/gap-groups.md`. **The exact reporting of ragged rows in the output is to be read in MX0 from the built binary, not assumed here** |

### 2.2 From outside (access date of every URL: **2026-10-22**)

| id | tool | fact read | URL |
|---|---|---|---|
| X1 | qsv | A toolkit of composable commands over CSV/TSV, JSONL, Parquet and spreadsheets. Prebuilt signed binaries for Linux, macOS, Windows on the releases page, also Homebrew, a Debian package and `cargo`; **the x86-64 prebuilts need AVX2, FMA and BMI1/BMI2**. Commands include `frequency`, `join` (inner, outer, right, cross, anti, semi; "a simple in-memory hash index"), `joinp` (Polars-based; "can process files larger than RAM"), `sort`, `extsort`, `sqlp` (Polars SQL over CSV, Parquet, JSONL, Arrow). An optional `index` makes `count`, `slice` near-instant and lets `frequency`, `stats` run multithreaded. "Some commands load the entire CSV into memory"; `frequency` memory grows with column cardinality. (The page was read to character 100,000 of 121,045.) | <https://github.com/dathere/qsv> |
| X2 | xsv | A Rust CSV toolkit (20 commands incl. `join`, `sort`, `stats`, `index`). **The repository was archived on 2025-04-24 and is read-only; its README says "xsv is now unmaintained" and recommends qsv or xan.** Prebuilt binaries, Homebrew, MacPorts, Nix, `cargo install` | <https://github.com/BurntSushi/xsv> |
| X3 | q | Runs SQL over delimited text through a SQLite backend; auto-detects column names and types; optional caching of the text data, off by default; GPL-3.0; version 3.1.6 | <https://github.com/harelba/q> |
| X4 | textql | Runs SQL over CSV/TSV through SQLite's in-memory database, Go; installs by Homebrew, `go get`, Docker, AUR; the page shows no archived notice and a CI badge pointing at travis-ci.org (possibly stale) | <https://github.com/dinedal/textql> |
| X5 | Polars | A query engine for DataFrames in Rust with Python, Rust, Node.js, R and SQL bindings; a streaming engine for larger-than-RAM data (`collect(engine='streaming')`); installs by `pip install polars` or build from source. **The README read does not mention a command-line tool**; a Polars "contender" is therefore a Python process, as pandas is | <https://github.com/pola-rs/polars> |
| X6 | DataFusion | The repository has a `datafusion-cli` directory; the README does not describe it or give install steps and links to an installation page that was **not reachable from this session** | <https://github.com/apache/datafusion> |
| X7 | jq | The README read says nothing about JSON-lines input, `--slurp` or number precision (it points to jqlang.org, not read) | <https://github.com/jqlang/jq> |

**Unverified** (the page was not reachable from this session, or silent): ClickHouse-local (clickhouse.com did not resolve), DataFusion CLI's install and CSV behaviour
(datafusion.apache.org did not resolve), DuckDB's spill, `memory_limit` and out-of-memory behaviour (duckdb.org did not resolve; the repository's own backlog says DuckDB
has "spill-to-disk for large sorts and aggregations" and that sentence is the repository's, not a verified source), jq's JSON-lines and big-number behaviour, SQLite's
CSV import (`.import`) behaviour and its exit codes, whether `awk` of the machine in question is `mawk`, `gawk` or `bwk`, Miller's and csvtk's behaviour at a memory
limit beyond what E6 and E7 recorded, whether any of csvtk, Miller, DuckDB, qsv, Polars or ClickHouse-local run under valgrind at all (section 4, kind g), the size of
each download of section 9.

## 3. The axes

### 3.1 Rows: tasks

A task is a question about a file with one right answer (or a stated set of right answers) that a reference computes without any contender. `table`'s expected
status is from E13 and the READMEs, **a prediction to be confirmed or corrected by MX1**, and a cell found to disagree corrects this table in place.

| id | task | what the reference is | `table` expected | already covered by |
|---|---|---|---|---|
| T01 | scan and filter (a numeric and a text condition) | `scripts/bench.py` truth, Python `csv` | expressible | `scripts/bench.py`, `bench_parallel.py` |
| T02 | projection (`cut` of 2 of 5 columns, 2 of 200) | Python `csv` | expressible | `bench.py`, `adversarial.py` C1 |
| T03 | group-by, low cardinality (count, sum) | NB1 reference (exact integers) | expressible | NB1/NB2 q1, q2 |
| T04 | group-by, high cardinality (10^5, 10^6 keys), count, sum, mean | NB1 reference | expressible up to `--max-groups`; refuses above 1,000,000 (`limit.too-many-groups`) | NB2 q3, q5, q10; `adversarial.py` B1 to B4 |
| T05 | exact decimal sum and mean (money) | `Fraction` | expressible with `:dec(S)` declared | `bench_numbers_agg.py` |
| T06 | exact float sum and mean | `Fraction` rounded once | expressible with `:float` | `threads_vs_duckdb.py` |
| T07 | sort a whole file by a text key, by an integer key, by two keys | Python `sorted` on bytes, ties stable | expressible, one core except whole CSV sorts | `adversarial.py` A1 to A4; `bench_numbers_keys.py` |
| T08 | top-k (first 1000 by an integer, descending) | Python | expressible (`--top`, bounded) | `adversarial.py` A3 |
| T09 | hash join (inner, left, anti) | NB3 fingerprint | expressible; build side bounded, refuses with a repair | NB3 q1 to q5 |
| T10 | join of two sorted files | NB3 fingerprint | expressible (`--join-sorted`) | NB3 `table-sorted` |
| T11 | date bucketing (count by day, by month of a timestamp, with offsets) | `datetime_ref.py` | expressible (`@hour`, `@day`, `@month`, `@year`; not `@week`) | `docs/datetime.md`, `bench_datetime.py` |
| T12 | JSONL input (a declared path, nested key, number kept exact) | `jsonl_ref.py` | expressible (`--input-format jsonl --fields`); a JSON array or pretty document refused | `bench_jsonl*.py` |
| T13 | standard input (the same query from a pipe) | the T01/T03 reference | expressible, one core | `bench_stdin.py` |
| T14 | distinct count (per group, and over a column of unique values) | NB1-style | expressible (`--max-distinct`) | `adversarial.py` I1 |
| T15 | quoting: quoted delimiter, doubled quote, newline inside a quoted field, CRLF | Python `csv` | expressible | `adversarial.py` D1 to D3, E1, E2; `test_differential.py` |
| T16 | ragged and malformed input (short row, long row, stray quote, unterminated quote, bad UTF-8) | **no single right answer**: the cell records the policy (error, skip, pad), whether it is silent, and whether the exit status says so | ragged rows counted and not used (E14); the rest to be read in MX0 | `docs/gap-scan.md` hostile files |
| T17 | bounded memory at a limit: the same query under an address-space or cgroup limit, and under `table`'s own `--max-*` | the T04/T07 reference | refuses with a tag and (sometimes) a repair | NB3 refusal report; `test_limits.py`, `test_memory.py` |
| T18 | start-up on a tiny file (5 rows) | the T01 answer | expressible | not measured anywhere for `table`; DuckDB's `select 1` is 0.013 s on the Mac (`docs/backlog.md`, one Mac, load about 2) |
| T19 | **breadth probes** (rows `table` is expected to lose): median and sd, a window function (running sum), a computed column (`a - b`), regex or `like`, a join of three files, a Parquet input | the SQL result | **not expressible / refused with a tag** | NB2 q6 to q9; E13 |

T19 is on purpose: a matrix whose rows were all chosen where `table` answers would be the selection the named-benchmarks design was written to avoid
(`docs/named-benchmarks.md` section 1: "somebody else chose them, so we did not pick the ones `table` is good at"). Rows T01 to T15 reuse tasks, files and references
that exist; the new work is T16 to T19 and the shared harness.

**Files.** The cells reuse the committed generators (`scripts/bench.py`, `bench_named_gen.py`, `bench_named_join_gen.py`, `threads_vs_duckdb.py`, `adversarial.py`)
and add none that carries data; data is never committed (`docs/named-benchmarks.md` section 0). A row has a stated size per machine class: **VM size** (5 to 1e6 rows,
the sizes at which a valgrind run finishes) and **quiet-machine size** (1e6 to 1e8, the NB ladder; opt-in as in NB2).

### 3.2 Columns: contenders

The maintainer's instruction (2026-10-22) was to take the contenders "because their performance is very good or because they are very popular" and to leave the harness ready to run. The
list below starts from the suggestion made (DuckDB CLI, ClickHouse local, Polars, pandas, SQLite, qsv, Miller, csvtk, DataFusion CLI, awk and jq as the JSONL baseline) and changes two things, each argued.
**Popularity** is quoted only where a page was read (GitHub stars, read 2026-10-22); **speed is not asserted for any contender here** and is what the matrix would measure. The versions are pinned in
`scripts/bench_matrix_tools.json` (the file the harness reads; the pin of a tool nobody could read a version for is `null`, with the reason).

| contender | why it is on the list | popularity evidence | pin | state on the VM that wrote this (2026-10-22) |
|---|---|---|---|---|
| `table` | the subject | | 0.3.0 | the binary of `/home/user/t-main/build/table`, commit `5a439ee`, not `origin/main` (section 15.3) |
| DuckDB CLI | the incumbent the repository measures against (E1 to E4); reads CSV, JSON lines and Parquet | 42.4k stars (<https://github.com/duckdb/duckdb>) | 1.5.6 | installed (`/home/user/named-tools`) |
| ClickHouse (`clickhouse-local`) | the other most-starred engine on the list; runs its engine over files with no server; its organisation publishes ClickBench (`docs/named-benchmarks.md` S15) | 50.5k stars (<https://github.com/ClickHouse/ClickHouse>) | none: no release list could be read; pin at first install | not installed |
| Polars (a Python process) | a Rust engine with a streaming mode for data larger than memory (<https://github.com/pola-rs/polars>); no command-line tool on the README read, so it is run as pandas is | not visible on the pages read: **unverified** | 2.0.0 (the releases page listed `py-2.0.0`, 06 Oct, as latest; the year was not shown) | not installed |
| pandas | the most common way to read a CSV in Python; already a contender of NB2 and NB3 | not measured: **unverified** | 3.0.3 (design pin) | installed, 3.0.5: **differs from the pin** |
| SQLite CLI | on every machine; SQL over an imported CSV (the load is part of the task). Also NB1's second path for the reference, so it is a contender only for tasks whose reference does not use it (Q7) | not measured: **unverified** | system's own | not installed (`sqlite3` absent) |
| qsv | the maintained toolkit that replaced xsv; `sqlp` (Polars SQL), `joinp`, an optional index, prebuilt binaries (<https://github.com/dathere/qsv>) | not visible on the pages read: **unverified** | 26.0.0 (19 Oct 2026 per the releases page) | not installed |
| Miller (`mlr`) | already a contender; reads CSV, TSV and JSON lines | 10.0k stars (<https://github.com/johnkerl/miller>) | 6.22.0 | installed |
| csvtk | already a contender | 1.2k stars (<https://github.com/shenwei356/csvtk>): kept for continuity with the existing page, not for popularity | 0.38.0 | installed |
| DataFusion CLI | the Rust engine under several other tools; its shell is `datafusion-cli` (<https://github.com/apache/datafusion>); its install and CSV behaviour could not be read: **unverified** | not visible: **unverified** | none: no release list could be read | not installed |
| awk | the floor on every machine; does not understand CSV quoting, which the quoted task shows | | system's | installed (mawk 1.3.4) |
| jq | the baseline for JSON lines only (`X07`) | <https://github.com/jqlang/jq> was read; the stars were not looked for | system's | installed (1.7) |

**Not on the list, and why.** *xsv*: the repository was archived on 2025-04-24 and its README says it is unmaintained and recommends qsv (X2, read 2026-10-22). *q and textql*: both run SQL through SQLite
(X3, X4), so the SQLite column already stands for the engine, and q's cache would have to be switched off to measure the cold path. *Pandas on its own is kept next to Polars* because a Python process is how
most people would run either. **Differences from the suggestion:** xsv is excluded (verified unmaintained); `awk` and `jq` are floors, not engines, and `jq` is asked one task only. A candidate is *listed,
not run* until it is installed: the harness records it as a blank cell with its install command, never an estimate.

## 4. The cell kinds

Every cell has one **kind**, and a cell never mixes kinds. A task x contender pair can have all eight, each in its own cell.

| kind | question | how it is measured | VM? |
|---|---|---|---|
| **a. expressible** | can the contender do the task? | the contender's form is run once on the small file; outcome one of **yes**, **workaround** (the form needs an extra step: a pre-pass, a header prepended, a typed cast, a second tool; the cell names it), **no** (not expressible; the nearest form, if any, is recorded), **refuses** (the contender ran and said no; tag and exit status recorded) | yes |
| **b. correct** | is the answer the reference's? | the answer gate of NB2 (`compare_answer`) and NB3 (`check_output`): exact / value-equal / **only by tolerance** (flagged) / **wrong** (the first difference recorded; **a wrong answer is reported and never timed**) | yes |
| **c. deterministic** | is the answer byte-identical across thread counts and repeats? | R repeats (R >= 5) at each of the contender's thread counts (1, 2, 4, 8 if it has them) on the same file; the cell is the **number of distinct sha256 of the output** (1 is the only passing value) and which settings gave the others. Order-free answers are first put in the canonical order the reference uses, and a contender whose output order is unspecified is reported as such, not failed (the NB rule: rows as a set where the question has no `ORDER BY`) | yes |
| **d. at a limit** | what happens when memory runs out? | two probes: (i) the contender's **own** bounds at a size just past them (`table`: `--max-groups`, `--max-state-bytes`, `--max-sort-rows`, the join build; others: their documented setting, if any); (ii) a cgroup or `ulimit -v` limit on the process at a fraction of the memory the unconstrained run needed (kind e), recorded as one of **spills and answers (verified correct)**, **answers wrongly**, **killed** (signal), **refuses with a message**, **refuses with a stable tag**, **refuses with a tag and a repair**. A cell never says "bounded" without the probe | yes, **if the VM allows a cgroup/ulimit; unverified** (the OOM kill of E6 happened inside a "Memory cgroup out of memory" of the VM, which says limits exist there but not that a session can set them) |
| **e. peak memory** | how much resident memory? | peak resident set from `wait4` through the launcher of NB2 (`rss_floor_kb` recorded), one extra run; the figure is a *count of bytes*, valid on the VM as a number but **not comparable across kernels** | yes (as a count; the machine is recorded) |
| **f. time** | wall and CPU time | NB2's rule: minimum of 5 after a warm-up, interleaved, warm cache, output to /dev/null, median and first run kept | **no: NOT RUN until the quiet Mac or gram.** `publishable` is recomputed by the writer (loaded, shared VM, soak, container, fewer than 5 runs: `docs/named-benchmarks.md` 18.2); a VM time is never published |
| **g. instructions** | instructions retired | `valgrind --tool=callgrind`, `Collected : N`, at two row counts, the slope per row and the start-up as the intercept (`scripts/join_callgrind.py`'s method, E10). **Valid on the VM for any contender that runs under valgrind**; a contender that does not (a Go runtime, a JIT, a Python import) gets the cell "does not run under callgrind: *reason*", **unverified for every non-`table` contender** until MX4 probes it | yes |
| **h. error contract** | is a failure machine-readable, stable and actionable? | each contender is given the same set of faults (nonexistent file, unknown column, bad number, a limit, a malformed row, a bad flag); the cell records, per fault: exit status distinct from success? a code or tag that is stable across two versions or two runs? is the message on standard error with output not mixed? is a repair offered as data? A tool's score is a *list*, never a number. `table`'s contract is E9; every other tool's is **read from its own output in MX3**, not assumed | yes |

**What a valid kind-g comparison is.** An instruction count is exact but it is not a time: it ignores memory stalls, branch misses, system calls and threads
(callgrind serialises threads). It is reported as "instructions retired by *this* process on *this* file", never as "faster". It is the one kind in which a multi-
tool comparison can be published from the VM, and it is stated that way: the claim is about instructions, not speed.

**Why the same file for every kind.** A cell is only comparable with its neighbours when the file's sha256 is the same; the file's sha256 is in the cell.

## 5. The presentation rule

1. **No composite score, no rank, no weighted sum, no "winner" column.** The reason is the one given in `docs/named-benchmarks.md` 12 ("never 'faster than DuckDB'"): any
   single number needs weights, and the weights are the argument. Kinds are not commensurable.
2. **A cell shows its kind and its conditions**: the file's name and sha256, the contender's version, the machine class, the thread count, N of runs, the date, the
   commit of `table`. A reader sees the kind before the value.
3. **A cell not measured is blank with the reason** ("NOT RUN: shared VM, no clock publishable"; "not wired"; "does not run under callgrind: *reason*"). Never an estimate,
   never a value from another machine or from another tool's published numbers, never an interpolation.
4. **Where `table` loses, the cell says so, in the cell**: "wrong answer", "refused: RULE", "killed", "N instructions per row against M for X". No footnote carries it.
5. **A cell that passed the gate only by tolerance says so** (NB2). A contender that disagrees with the reference is "wrong", and its time, if any, is not shown.
6. **Both directions in the same words.** A cell where `table` wins and a cell where it loses use the same sentence form: "on this file, on this machine, `table`
   <did X>; DuckDB <did Y>". No adjective.
7. **The falsification table (section 6) is printed above the matrix with the cells that decide it highlighted by id, and each cell's verdict is computed by the
   generator** from the JSON by the rules there, not written by hand. A verdict of "supports" or "refutes" is a function of the data; "open" is the default.
8. **Rows `table` does not do (T19) are in the matrix and are not collapsed.** Their count is printed next to the count of rows `table` answers.
9. **Old results are kept, not overwritten**, as `docs/named-benchmarks.md` 12 requires, and a result is rerun when `table`'s plan code changes.
10. **No number from an owner's published report, from another machine, or from this document's section 2.1 enters a matrix cell.** Section 2.1 is context; a cell is
    always measured by the harness under its own conditions.

## 6. The falsification table

H is "analytics over files is `table`'s moat". It is stated here as *five sub-claims*, because a moat is only as wide as its narrowest part, and each is paired with
the cells that would support it and the cells that would refute it. **The thresholds are choices, fixed before measurement; a change to one after the data is seen is
recorded as a change, with the reason, and the old verdict is kept.**

| # | sub-claim | would **support** it (all of) | would **refute** it (any of) | cells | prior from the record |
|---|---|---|---|---|---|
| H1 | **Analytics breadth over files is adequate**: the common tasks are expressible without a workaround | T01 to T15 are *yes* for `table` with the declared types (no workaround) and *correct* (kind b exact) | one of T01 to T15 is *no* or *workaround* for `table` while at least two other contenders do it *yes*; or `table`'s answer is *wrong* on any of T01 to T15 | a, b | E13: it is expected to hold for T01 to T15 and to fail on T19. **T19 is the breadth boundary, not a refutation**: H says "files", not "SQL" |
| H2 | **Exact numbers**: `table`'s sums and means are exact and others' are not, or need the user to know | T05, T06: `table` *exact* (kind b) and *1 distinct output* across threads (kind c); at least one contender is *wrong or only by tolerance*, or *not deterministic*, on T06 | a contender that is *exact and deterministic* on T05 **and** T06 **without a declaration or a workaround that `table` itself needs** (E2: DuckDB's `DECIMAL` sum was the same value in every run, but the user had to declare the type; this is a *tie with a declaration*, and the cell must say what each needed) | a, b, c, h | E2 supports the T06 half on a 2,000,000-double file, 3 runs, no timings. Whether T05 distinguishes `table` from DuckDB is **not known** |
| H3 | **Determinism**: the same bytes at any thread count and repeat | T03, T04, T05, T06, T07, T09, T11: `table` 1 distinct sha256 at every thread count; at least one contender gives more than 1 on some task | `table` gives more than 1 distinct output on any task (this would refute a claim the README makes, and is a defect); or *every* contender is also deterministic on every task (then determinism is not a differentiator, only a table-stakes property) | c | E2 for T06 only. **Unmeasured** for every other task and every other contender |
| H4 | **Bounded, with a refusal that can be acted on**: at a limit `table` refuses with a tag and (where it can) a repair, and others fail worse | T04, T07, T09, T17: `table` *refuses with a tag*, *sometimes with a repair*, never *killed* and never *wrong*; at least one contender is *killed* or *wrong* on the same probe | `table` is *killed* or *answers wrongly* at any limit; or `table` refuses where a contender *spills and answers correctly* on T07/T09 and the user's task is one where an answer is wanted more than a bound (**this refutes "bounded is the moat" for those tasks and says what would have to be built**: E8: no external sort, no spill) | d, e, h | E6, E7: csvtk was killed by OOM on a group-by at sizes `table` handles. DuckDB's behaviour at a limit is **unverified**. `table` has no spill by design (E8) |
| H5 | **Agent-usable**: the failure contract is machine-readable, stable, and carries a repair | kind h: `table` passes every probed fault; at least two contenders fail at least one (no stable code, message mixed with output, success status on a failure) | a contender passes every fault `table` passes **and** gives a repair as data on at least as many (then the contract is not unique, only well made) | h | E9 describes the contract; **no other tool's contract was read** |
| H6 | **Speed is not the moat but is not a hole**: `table` is competitive on instructions on the common scan and group tasks | kind g: on T01, T02, T03 `table` retires no more than 1.25x the instructions per row of the *lowest* contender that runs under callgrind (the "material loss" line of `docs/adversarial.md`); kind f (parked) agrees on the quiet machine | kind g: on T04 (high cardinality) `table` retires more than 1.25x the lowest contender, **and** kind f (when run) shows the same on the quiet machine at its thread count | g, f | **E1: on Linux at 6 threads, 1,000,000 keys, DuckDB's unsorted group-by was ahead of `table` by 1.77 and 1.86 times (shared box, load 1.1 to 1.4, min of 5)**; the Mac at 16 threads was the opposite. So H6 is expected to be **refuted for T04 on Linux** unless the instruction counts say otherwise; that does not refute H1 to H5, and it is why speed is not in the thesis |

**How the verdicts combine.** There is no combination. The claim "analytics over files is a moat" is reported as *a table of six rows, each supported, open, or
refuted by named cells*, and the sentence the document may then write is the conjunction as it stands ("H1 supported, H2 supported on T06 and tied on T05, H3
open ...") and no more. A single refuted row (H1, H3, H4 or H5) means the corresponding word is removed from the sentence about the moat; it does not mean the
others are. If H1 is refuted (a common task is not expressible) the thesis "analytics over files" is the wrong thesis, not a weaker one.

**Honesty about the prior.** On the repository's own numbers, DuckDB is ahead of `table` on high-cardinality group-by on the shared Linux box (E1), and DuckDB has, in the
backlog's words, SQL with a planner, window functions, spill-to-disk, vectorised execution, nested types, Parquet, a storage format with transactions and extensions
(`docs/backlog.md`, as corrected by #90). Those are statements of the repository about another tool and are **unverified here** (X-sources for DuckDB were not
reachable). The matrix does not try to make DuckDB's breadth a row to be won; T19 records it as a row `table` is expected to lose.

## 7. Where the differentiators plausibly are: all *unmeasured hypotheses* until a cell measures them

None of these is asserted. Each says which cell would measure it, and what would make it a non-differentiator.

| id | hypothesis | cell(s) that would measure it | what would make it not a differentiator | measured now? |
|---|---|---|---|---|
| D1 | **Exact sums without a declaration burden.** `table` is exact for `:float` and `:dec(S)`; DOUBLE engines are not; but a DECIMAL column in DuckDB is exact and deterministic (E2) | T05, T06 (a, b, c) | the user needs the same declaration in both tools; or a contender has a default that is exact | E2 only (T06, one file) |
| D2 | **Byte-identical answers at any thread count.** | T03 to T11 (c) | all contenders are deterministic on all tasks | E2 only (T06) |
| D3 | **A bounded refusal with a repair, instead of OOM or a silent wrong answer.** | T04, T07, T09, T17 (d, h) | a contender spills and is correct (then boundedness costs the user an answer for nothing) | csvtk's OOM only (E6, E7) |
| D4 | **The agent contract** (stable `area.name` tag, exit status classes, `repair` as data, `introspect`, `skill`, MCP). | kind h; MX3 | every contender has stable codes; or the agent cannot use the repair | none |
| D5 | **The authority row**: what the binary can reach is stated, committed and checked in CI (E12). | no cell of the eight kinds measures it. **It is a property to be listed in a "facts" strip of the matrix, with the way to check it (`manifests/table.authority.json`, `scripts/manifest.py --check`), not a measured cell** | no other contender states one | by construction, not by the matrix |
| D6 | **No install, a single binary.** The record says the opposite today: **the only route is `cargo` + `cancho build` with a pinned compiler and there is no release** (E11). The competitors have prebuilt binaries or a package manager (qsv: X1; xsv: X2; csvtk, Miller, DuckDB: release binaries downloaded in NB2, `docs/named-benchmarks.md` 18.3) | a "facts" strip: how each contender is obtained, its size, its dependencies | it is **refuted by E11 for the install path today**; the single binary is a property of a build, not of a download | E11, a negative |
| D7 | **Small resident memory on scans.** E3: 2.4 MB against 361.7 MB on a 1 GB filter | T01, T02, T03, T14 (e) | peak memory is not what a user of a 1 GB file runs out of; `table`'s peak on a sort or a join is not small (E4: 146 and 189 MB for 1M x 1M) | E3 for two tasks, one file, one machine |
| D8 | **One tool for CSV, TSV and JSONL with the same flags**. | T12 (a) | qsv `sqlp` or DuckDB do JSONL equally (X1 says qsv reads JSONL) | none |

D5 and D6 are listed because the user's list of candidate moats includes them; neither is a kind of cell. D6 is, on the evidence in hand, a **loss today**.

## 8. The slices

Each slice is one small pull request. **Its gate is written first, committed alone, and shown failing against `main`** (the NB3 precedent: the first commit
of the PR is the tests alone, `docs/named-benchmarks.md` section 19 preamble), and each slice with logic has mutants in the style of `scripts/mutlib.py`. All scripts
start processes through `import procs as subprocess`; no file exceeds 2,000 lines; every refusal has a rule tag (`matrix.*`, a new area, listed in MX0).

*Status (2026-10-22): MX0 to MX5 and the machinery of MX7 are built (section 15); MX6 (publishing) and MX7 (the clock run itself) are not run; MX8 is covered by the manifest and `--check-tools` (the tools are not installed); MX9 is not started.*

| slice | what | cloud VM? | the gate written first (must fail first) | depends on |
|---|---|---|---|---|
| **MX0** | the task catalogue: `scripts/bench_matrix_tasks.json` (T01 to T19, the reference, the sizes, `table`'s predicted status), the cell schema (`bench-matrix.v1`), the verdict functions of section 6, the rule tags of the harness. **No contender is run** | yes | a schema test; a **verdict test**: a fabricated JSON in which `table` is wrong on T01 must give H1 *refuted*, one in which a contender is exact and deterministic on T05 and T06 must give H2 *refuted*, one in which `table` gives two sha256 must give H3 *refuted*; a test that a cell with no `reason` and no value is refused | NB1 to NB3 |
| **MX1** | **expressibility and correctness matrix** (kinds a, b) for the five wired contenders on T01 to T19 at VM size, with the gate of NB2/NB3 (wrong answers reported and never timed). T19's probes are asked of every contender. The prediction column of MX0 is compared with the result and **corrected in place** where wrong | yes | G-answer from NB2 (a stub that drops a row is *wrong*); a cell for a contender that cannot express a task is *no*, not absent; `table`'s T19 probes must be refused with a *stated rule* (as NB1's class gate: a feature that lands fails the gate until it is re-classified) | MX0 |
| **MX2** | **determinism and repeat-run matrix** (kind c): R=5 repeats at 1, 2, 4, 8 threads for every contender that has a thread setting, on T03 to T11 and T14, the number of distinct sha256 per cell | yes | a stub contender whose output varies by one byte at 2 threads must give 2; a contender with no thread setting is "repeats only"; the order-free canonicalisation must not hide a real difference (a mutant that sorts bytes within a row is killed) | MX1 |
| **MX3** | **refusal-behaviour and error-contract matrix** (kinds d(i), h): the same fault set for every contender; `table`'s side is read from `table introspect` (rule tags), the others' from their own output; **nothing is assumed about a tool whose output was not read** | yes | a stub that exits 0 on a failure must be recorded as such; a message containing a path must not be taken as a stable code; the fault list is a committed file and each fault must be reproduced by `table` first (so the table cannot go stale) | MX1 |
| **MX4** | **callgrind instruction matrix** (kind g): first a **probe** that records which contenders run under valgrind at all (`table`, DuckDB CLI, csvtk, Miller, `awk`, Python-based pandas/Polars) with the exit status and the stderr tail, then the slope per row on T01 to T04, T07, T09, T11 for those that do. Slope method of `scripts/join_callgrind.py`; rows at 20,000 and 60,000 (its defaults) or smaller where the tool is slow | yes (valgrind was present on the VM when this document was written: `/usr/bin/valgrind`; the version was not read) | the count of a trivial program is positive and reproducible to the instruction on two runs; a contender that does not run is a *cell with a reason*, not an absent one; the slope of a run on 20,000 rows and on 60,000 rows of the same prefix must be linear within a stated bound or the cell is *not valid* | MX1 |
| **MX5** | **at-a-limit matrix** (kind d(ii), e): cgroup or `ulimit -v` limits at fractions of the unconstrained peak resident, for T04, T07, T09; peak resident as a count | yes **if** the session may set a limit (unverified); otherwise the cell is "NOT RUN: no limit can be set here" | a contender killed by the kernel is recorded *killed*, not *wrong*; the limit used and the unconstrained peak are in the cell; a limit set above the unconstrained peak (no pressure) must be refused | MX1, MX4's launcher |
| **MX6** | publish MX1 to MX5 (without kind f) as `docs/assets/matrix-benchmark.json` and a section of `docs/benchmarks.html`, with the falsification table printed by the generator; `scripts/site.py` and `scripts/seo_check.py` keep the page | yes | the writer refuses a cell without its kind, its file sha256, its conditions or its reason; refuses a verdict not computed from the data; refuses a rank or a score field (a mutant that adds a "total" is killed) | MX1 to MX5 |
| **MX7** | **clock matrix** (kind f): NB2's rules on the quiet Mac / gram, 1e6 to 1e8 | **no: parked.** NOT RUN until the maintainer's quiet machine (load under a quarter of the cores, no soak) | NB2's G-load and `publishable`; unchanged | MX1, the maintainer's yes |
| **MX8** | wire **qsv** (and, if the maintainer wants, Polars as a Python process) as contenders; extend MX1 to MX4 to them | yes **after a download** (section 9) | the forms validated against the reference at 20,000 rows as NB2 did; a contender that disagrees is reported | the maintainer's yes |
| **MX9** | the other candidates (ClickHouse-local, DataFusion CLI, SQLite CLI, q, textql, jq, xsv) one at a time, each its own PR | after downloads | as MX8 | MX8; the maintainer's yes per tool |

**What is never part of this:** a GPU run, a model run (`cancho-ml`'s rules do not apply here, but the instruction of the work is the same), a download without the yes of
section 9, a kernel setting, installing a package system-wide.

## 9. Installs and downloads: none was made; the list to ask about

The harness installs nothing. `python3 scripts/bench_matrix.py --check-tools` prints, for every contender, whether it is found, its version, whether that is the pin, and for the missing ones the exact install
command for the system it runs on (`scripts/bench_matrix_tools.json`). **Every install command is unverified** (no session that wrote them could run them; `install_verified` is false for all but the
subject's own build), and the sizes of the downloads are not known (unverified). Missing on the VM that wrote this: ClickHouse, Polars, SQLite CLI, qsv, DataFusion CLI. Already present there: DuckDB CLI,
csvtk and Miller (in `/home/user/named-tools`, from NB2), pandas (3.0.5, not the pin 3.0.3), mawk, jq, valgrind 3.22.0. **Each install needs the maintainer's yes, one tool at a time, with the size read first.**

## 10. Output

* **The matrix** is published as a section of `docs/benchmarks.html` (after the named-benchmark sections of NB4), generated from the JSON between `gen:` markers by
  `scripts/site.py`, not written by hand, with the conditions next to every cell, the falsification table above it and the facts strip (D5, D6) beside it.
* **The data** is a committed JSON, **`docs/assets/matrix-benchmark.json`**, in the manner of `docs/assets/threads-vs-duckdb.json`: schema `bench-matrix.v1`, the machine
  class, the versions, one record per (task, contender, kind) with the file name and sha256, the conditions, the value or the reason it is blank.
* **The generator** is **`scripts/bench_matrix.py`** with `scripts/bench_matrix_forms.py` (tasks, references, the judge, the commands), `scripts/bench_matrix_tools.json` (the manifest) and `scripts/bench_matrix_mutants.py`; none passes 2,000 lines, and the ceiling is never raised (**[as built]** the tasks are in the forms module, not a `bench_matrix_tasks.json`).
  Every process is started through `import procs as subprocess` (`scripts/procs.py` locating `tests/conformance/procs.py`; `tests/conformance/test_reaper.py` already
  fails a file that starts a process another way). The tests are `tests/conformance/test_bench_matrix*.py`, in CI, at tiny sizes, with no network, and the mutants
  `scripts/bench_matrix_mutants.py` with `--check` in the loop of the CI.
* **Rule tags.** Every refusal of the harness has a tag, `matrix.<name>` (for example `matrix.cell-without-kind`, `matrix.blank-without-reason`, `matrix.score-field`,
  `matrix.verdict-hand-written`, `matrix.not-publishable`, `matrix.limit-not-set`, `matrix.suite-not-built`), listed in MX0, and no input reaches a panic.
* **Not changed by this work:** the README and the landing page numbers.

## 11. Gates, in one place (written before the code they judge; each must be able to fail)

1. *Wrong is not timed* (NB2): a contender that disagrees with the reference has no kind-f/e value in its record.
2. *Blank has a reason*: a cell with neither a value nor a reason is refused by the writer.
3. *No score*: the writer refuses any field named total, score, rank, winner, or any cell that is a function of other cells' values.
4. *The verdicts are computed*: a test feeds a fabricated JSON per row of section 6 and the verdict must equal the one written in the table.
5. *T19 probes are classified*: a probe `table` starts to answer fails the gate until re-classified (NB1's class gate).
6. *Determinism cell counts distinct sha256*, with a stub that varies and a stub that does not.
7. *Callgrind linearity*: the slope of two sizes is checked against a third.
8. *Limit probes are not no-ops*: a limit above the unconstrained peak is refused.
9. *Machine class*: a time recorded on a VM, a loaded machine, a soak or a container is `publishable: false` and never reaches the page (NB2's writer, reused).
10. *Old results kept*: a re-run does not overwrite a result file.

## 12. Open questions for the maintainer, with a recommended answer

| # | question | recommendation | argument |
|---|---|---|---|
| Q1 | Is the thesis H to be tested in the five sub-claims of section 6, with the thresholds there? | **Yes, and fix the thresholds now, before MX1** | a threshold chosen after the data is a result in disguise |
| Q2 | Is T19 (breadth probes where `table` is expected to lose) kept in the matrix and on the page? | **Yes** | the matrix is not evidence for H if it leaves out the part H does not cover |
| Q3 | Which kinds does the page show first? | **Correctness, determinism, refusal behaviour, instructions** (the four the VM can settle), clock **blank** until MX7 | what can be published honestly now; time cells blank say why |
| Q4 | Include xsv as a column? | **No (answered by the instruction of 2026-10-22: contenders by popularity or speed; xsv is archived, X2)** | an unmaintained tool as a contender measures history |
| Q5 | Install qsv, ClickHouse, Polars, SQLite CLI and DataFusion CLI so their columns fill? | **Ask per tool, with the size read first** (`--check-tools` lists the commands). qsv with and without `index`, and `sqlp`/`joinp`, are separate columns once installed | each install is a machine change; the harness is ready and does not need them to run |
| Q6 | Polars as a Python process, as pandas is? | **Yes** (done in the manifest and the forms; not installed here); no CLI was found on the README (X5) | keeps the column honest about what is being run |
| Q7 | SQLite as a contender (it is already the reference's second path)? | **Not in the first cut.** If added, it must not also be the reference for the cell it competes in | an independence rule: a contender is never the reference (`docs/named-benchmarks.md` 3.3) |
| Q8 | May MX5 set a cgroup or `ulimit -v` limit on the VM? | **Yes if the session can without a kernel setting; else the cell is NOT RUN** | the same "no kernel settings" rule as for gram |
| Q9 | Where do the D5 (authority row) and D6 (install) facts go? | **A "facts" strip beside the matrix, not cells**; and D6 is shown as it is today (build from source, no release) | they are not measurements of the eight kinds, and D6 is currently a loss |
| Q10 | Should the matrix wait for the quiet machine? | **No: publish MX1 to MX5 without kind f** | the VM settles what it can; the clock is parked, not omitted |
| Q11 | Does the matrix replace NB4 (publishing the named sections)? | **No.** NB4 stays; the matrix is a different view of the same harness | NB4 is a time page; the matrix is a capability page |
| Q12 | The sentence of PR #90 "No speed comparison with DuckDB on joins has been run" | **Amend #90 before it merges** (section 14, item 1) | `docs/joins.md` sections 12, 16.5, 17.4 and 18.4 have loaded-Mac join timings against DuckDB |

## 13. What this document does not claim

* It does not claim `table` is faster, slower, more correct or more robust than any tool on any task of the matrix, and does not claim DuckDB or any other tool is.
* It does not claim H, nor any sub-claim of it. It says what would support it and what would refute it, and E1 and E6 to E8 are the recorded reasons it might be refuted.
* It does not claim that any D-hypothesis (section 7) holds. D6 is, on the evidence in E11, a loss today.
* It does not claim anything about a tool it did not read (section 2.2 lists what was read; the **unverified** list is the rest), including DuckDB's behaviour at a memory limit and any tool's behaviour under valgrind.
* It does not claim the predictions in section 3.1 ("`table` expected") are right; MX1 corrects them.
* It does not claim any quoted figure from section 2.1 holds beyond its conditions: E1 is a shared Linux box at load 1.1 to 1.4; E2 has 3 runs and no timings; E3 predates #32 to #35; E4 is a loaded Mac; E5 and E6 are one shared VM and `publishable: false`.
* It does not claim that instruction counts are speeds, or that a callgrind count transfers to another CPU.
* It does not claim any run: no script, no download and no clock read was part of this document.

## 14. Corrections to older documents

1. **PR #90 (`docs/backlog.md`, open, not merged, not edited here)** says "No speed comparison with DuckDB on joins has been run (NOT RUN: ...)". That is **false against
   `docs/joins.md`**: section 12 (the baseline, Mac, loaded, minimum of 5), 16.5 (J1, E4 above), 17.4 (J2) and 18.4 (J3) compare `table` with DuckDB at 1 thread and at its
   default and with csvtk on joins, on a Mac loaded by other work, with every output checked against a Python join first. What has **not** been run is a quiet-machine
   join comparison, a Linux one with DuckDB (gram had no DuckDB then), and NB3's DuckDB side at publishable conditions (NB3 ran at 1e6 on a shared VM, not publishable).
   The sentence should read "No quiet-machine or Linux speed comparison with DuckDB on joins has been run". This PR does not touch #90's branch; the correction is
   for the maintainer to apply there, and Q12 asks for it.
2. **`docs/backlog.md` item 12** said NB1 and NB2 are built and "nothing is published and no speed is claimed"; NB3 (the join suite) is also built, per
   `docs/named-benchmarks.md` 19. **Corrected in place in this PR**, with the link to this document.
3. **`docs/named-benchmarks.md` status header** did not mention this document. A one-line pointer was added; no other text of that file changed.

4. **This document's own first version** (same day) said it was design only and that the generator was "not built"; section 15 and the status header now say what was built. The text of sections 0, 3.2, 8, 9, 10
   and 12 was changed in place for that; the falsification table and the kinds were not.

No other older text was found false while writing this; nothing was re-measured.

## 15. The harness as built, how to run it, and what it recorded on one cloud VM

### 15.1 What was built

* `scripts/bench_matrix_forms.py` (tasks X01 to X11, the inputs, the references, the judge, the command of each contender for each task), `scripts/bench_matrix.py` (the driver and the decision logic),
  `scripts/bench_matrix_tools.json` (the manifest: contenders, pins, detection, install commands), `scripts/bench_matrix_mutants.py` (86 mutants of the decision logic),
  `tests/conformance/test_bench_matrix.py` (the gates). Every process is started through `import procs as subprocess`; the largest file has under 800 lines; every refusal has a `matrix.*` tag:
  `matrix.args`, `matrix.clock-switch`, `matrix.clock-not-quiet`, `matrix.clock-conditions-missing`, `matrix.clock-min-runs`, `matrix.large-size`, `matrix.inside-repo`, `matrix.tool-missing`,
  `matrix.cell-without-kind`, `matrix.blank-without-reason`, `matrix.cell-both`, `matrix.score-field`, `matrix.time-outside-clock`, `matrix.time-on-failed`, `matrix.time-invalid`,
  `matrix.no-outputs`, `matrix.slope-rows`, `matrix.limit-no-pressure`, `matrix.result-invalid`; exit status 3 for each, as the driver of the named benchmarks does.
* **The tasks** (`forms.TASKS`): X01 scan (count where v1 >= 4), X02 group-by with few keys, X03 group-by with many keys, X04 exact decimal sum, X05 sort and limit, X06 distinct, X07 JSON lines, X08 standard
  input, X09 quoted keys with a comma, a quote and a newline, and the two breadth probes X10 (median) and X11 (a computed column), where `table` is expected to refuse. They sit on NB1's seeded file
  (`bench_named_gen.py`, seed 108: the same bytes as the named benchmarks' file of that size) and two files derived from it. Not yet tasks of the harness: the joins (the NB3 suite has them), dates, ragged
  and malformed input (T16), start-up on a tiny file (T18): listed in section 3.1, not built.
* **The gates**, written first (commit `c68df38`, tests alone: 0 of the tests could run against `main`, `import bench_matrix` failed) then made to pass; later commits added tests where the mutants survived.

### 15.2 The commands

```
python3 scripts/bench_matrix.py --check-tools [--tools-dir DIR] [--table PATH]      # what is installed, what is missing, the install command for this system
# the cloud VM or any machine: the kinds a, b, c, d, g, h; no clock, no resident set; data under the temporary directory, outside the repository
python3 scripts/bench_matrix.py --rows 20000 --table build/table --tools-dir DIR --results /tmp/matrix-vm.json
# a quiet Mac: the same plus kinds e (peak resident set) and f (time), 16 cores of which 4 threads are used by the contenders that have a thread setting
python3 scripts/bench_matrix.py --clock --rows 1000000 --threads 4 --table build/table --tools-dir DIR --path-class "internal SSD" --results ~/matrix-mac.json
# gram: the same, pinned to the cores the notes allow (cores 0-6 if no soak and no container, else 0-5)
python3 scripts/bench_matrix.py --clock --rows 1000000 --threads 6 --taskset gram --table build/table --tools-dir DIR --path-class "internal SSD" --results ~/matrix-gram.json
```

`--clock` **refuses before it generates or launches anything** unless the machine is quiet: the one-minute load average at the start is at most a quarter of the cores, no hypervisor flag (or the Mac's
witness), no soak, no container, docker readable, the conditions of `docs/named-benchmarks.md` 3.1 complete (machine, cores, load, file, sha256, versions and threads of every contender found) and at least
five runs (`--runs`, default 5). The checks are NB2's (`evaluate_publishable`), reused. A time or a resident set is written only for a pair whose answer passed the judge at that size; the minimum of N
is taken after one untimed warm-up, the contenders interleaved with the order rotating, output to /dev/null; the writer re-checks all of this and refuses a result that breaks it. `--rows` above
1,000,000 needs `--accept-large`. **Run on the VM that wrote this, `--clock` was refused** with `matrix.clock-not-quiet` (a shared virtual machine, a load of 1.67 over 1.00, an unreadable docker); that is the
intended result, and CI checks it.

### 15.3 What the first run recorded (one VM, recorded, not published, no speed claimed)

**Conditions.** One shared cloud VM: Linux x86-64, Intel Xeon @ 2.10 GHz, 4 logical CPUs, 16,872,808,448 bytes of RAM, the `hypervisor` flag set, Python 3.13.16, docker not reachable; one-minute load at the
start 0.46 (kinds a and b), 0.72 (c, d, h), 1.21 (g); other work of the same session (the mutants, in four shards) ran during the runs of c, d, h and g, which is why no clock is read from them. The file:
NB1's, 20,000 rows, K 100, seed 108, 969,972 bytes, sha256 `42f9eae0a22df80ee41ae4020f964f4e7806932dd6a19a0c1087bd5cd7a7042f` (the jsonl 2,149,939 bytes, the quoted file 42,004). `table` 0.3.0 from
`/home/user/t-main/build/table`, **commit `5a439ee` (a build that was already on the VM, not `origin/main` at `6c0291c`)**; DuckDB CLI 1.5.6, Miller 6.22.0, csvtk 0.38.0 (the NB2 binaries), pandas 3.0.5
(**not the pin 3.0.3**), mawk 1.3.4, jq 1.7, valgrind 3.22.0. Not installed, so blank with their install commands: ClickHouse, Polars, SQLite CLI, qsv, DataFusion CLI.

**Kinds a and b** (the answer judged against the reference; X01 to X11, the seven installed contenders):

| contender | result |
|---|---|
| `table` | yes and **exact** on X01 to X09; X10 (median) **refused**, `agg.bad-spec`; X11 (computed column) **refused**, `column.unknown`; nothing to judge on the two |
| DuckDB, pandas, Miller | yes on all eleven tasks; **exact** except X04 (the decimal sum, by a double) **only by tolerance** (flagged) |
| csvtk | yes on ten tasks, no form written for X07; **value-equal in another spelling** (numeric) on X02, X03, X08 to X11; X04 only by tolerance |
| awk (mawk) | exact on X01 to X06, X08 and X11; **wrong on X09** (the quoted file: 58 rows where 1,805 are right), as expected of a tool that does not read quotes; no form for X07 and X10 |
| jq | X07 exact; the other ten tasks have no form (jq is asked the JSON lines task only) |

**Kind c** (thresholds 1, 2, 4 and 5 repeats each, 15 runs per cell; tools with no thread setting: 5 repeats): `table` gave **1 distinct output (bytewise) on X01 to X06**. DuckDB gave 1 on X01, X05 and X06, and
**5, 4 and 6 different byte strings on X02, X03 and X04, which are 1 after the rows are put in order**, so only the row order varied. pandas, Miller, csvtk and awk gave 1 on every task they ran. This is one file of
20,000 rows; it says nothing about larger inputs or about X07 to X11.

**Kind d.** `table`'s own bounds, set to 10 below the size of the input: `limit.too-many-groups` (X03) and `limit.too-many-sort-rows` (X05), exit 8, both with repair kind `none`. The address-space ladder (`ulimit -v`, **virtual
address space, not resident memory**, so it is not a statement about memory use), X03: `table` answered correctly at 64, 256 and 1,024 MiB (its unconstrained peak resident was 5,840 KB); DuckDB was **killed** at 64 MiB, gave a
message and exit 1 at 256 MiB and answered at 1,024; pandas, Miller and csvtk gave a message at 64 and 256 and answered at 1,024; awk answered at all three. Whether any of them spills was not probed (the probe only
sees a limit that the input is over, and a 20,000-row file is not over any of these).

**Kind h** (a missing file, an unknown column; `table` asked for its default JSON): `table` exits non-zero with a JSON error on standard output and a rule tag (`io.not-found`, `column.unknown`), repair kind `none` for the
second; DuckDB, pandas, csvtk exit non-zero with a message on standard error and no tag; Miller exits non-zero for the missing file and **exits 0 with an empty output for the unknown column**; awk exits non-zero for the
missing file and, having no column names, exits 0 on the unknown one (its form cannot name a column; this says nothing of awk's contract).

**Kind g** (instructions retired by callgrind, `--trace-children`, the slope between two prefixes of the same file, per row; one run each; **instructions, not a time**; `table` built as above):

| task | prefixes | `table` | DuckDB | pandas | Miller | csvtk | awk |
|---|---|---:|---:|---:|---:|---:|---:|
| X02 | 2,000 and 6,000 rows | 1,936 | not valid (see below) | 5,457 | 7,540 | 7,805 | 3,187 |
| X02 | 20,000 and 60,000 rows | 1,939 | 2,347 | 6,881 | does not run under callgrind | 8,225 | 3,187 |
| X04 | 20,000 and 60,000 rows | 2,158 | 2,501 | 5,514 | 169,796 (not credible: see below) | 9,967 | 3,714 |

The other tasks at 2,000 and 6,000 rows (`table`: X01 1,724, X03 1,965, X06 2,643; awk: X01 3,172, X03 3,237, X06 2,828) are in the run. **Cells that are blank or not to be trusted, with the reason the harness wrote:**
DuckDB at 2,000 and 6,000 rows is "not valid: the count did not grow with the rows" (its start-up, 2.6e8 to 3.0e8 instructions and varying by run, swamps six thousand rows: the slope needs the larger prefixes, as the second row
shows); Miller's X04 count is two orders of magnitude over its neighbours and is, by the way of a Go runtime under valgrind (threads, garbage collection), **not credible**; Miller (X04 and X06 at the smaller prefixes, X02 at the larger) and csvtk (X01 at the smaller) exit with status 1 under valgrind and are blank with the tail of the message. **Nothing here compares speed**; the kind is valid on a shared VM, one run is not a result, and the claim of H6 (section 6) is about
`table` against the lowest contender at the *quiet-machine sizes*, which was not run.

### 15.4 Mutants and gates

`scripts/bench_matrix_mutants.py`: 86 mutants of the clock gate, the blank-cell and no-score rules, the answer judge, the determinism count, the slope, the classification at a limit, the profile of a fault, the detection of
a tool, the order of the clock kinds, the references and the forms. The first full run killed 80 and left 6 alive; each was a missing assertion, not an equivalent mutant, and got a test (the first run's of a pair's *first* time,
the width of a header and of a row, a repeat count, a run that fails while timing, the length of the list of times); the full list was then run again (four shards of 22, 22, 21 and 21) and **all 86 were killed**.

### 15.5 NOT RUN

* **Every clock kind** (e peak resident set, f time): the Mac and gram were not available to this work and the cloud VM is refused by the gate. The one-command forms are in 15.2.
* **Every contender that is not installed**: ClickHouse, Polars, SQLite CLI, qsv, DataFusion CLI. Their forms were written without the tool and are **unvalidated**: the first run that has them will judge each against
  the reference and report a wrong form as a wrong answer. Their install commands are unverified.
* The tasks of section 3.1 that are not in the harness (joins, dates, malformed input, start-up on a tiny file) and the sizes above 20,000 rows for kinds a to d, g and h.
* Spilling at a limit: not probed (no input over a limit was used for the other tools).
* No GPU, no model, no dataset download, no installation.

### 15.6 Deviations from the named-benchmarks design

* The judge is a smaller one (`forms.compare`), not NB2's `compare_answer`, because the tasks are not NB1's questions and have no `chk`; the rules are the same (rows as a set unless ordered, an integer never by tolerance, a
  tolerance flagged, the first difference reported). NB2's conditions, launcher, publishable test and `classify` are reused unchanged.
* The thread levels of kind c are 1, 2 and 4, not the machine's own, so that a four-core VM can run it; the clock kinds use `--threads` (default 4).
* Contenders are chosen by the maintainer's instruction, not by `docs/named-benchmarks.md` 3.6, and two are added (awk, jq as floors) and the polars column that 3.6 skipped is wired in the manifest.
* The `table` forms are the flag form only; the `--query` form is not asked here (NB2 and NB3 hold it equal).
