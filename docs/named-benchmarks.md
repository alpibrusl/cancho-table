# Named benchmarks: a design for externally comparable measurements

> **Status (2026-10-21): accepted, NB1, NB2 and NB3 built.** NB3 (section 19) is the join suite: `scripts/bench_named_join_gen.py`, `bench_named_join_ref.py`, `bench_named_join.py`, the join contenders (table, `table --join-sorted`, DuckDB, csvtk, Miller, pandas) validated against the reference, a 1e6 smoke step on one shared VM (recorded, not publishable) and the report of what `table` says where it refuses; Mac and gram, 1e7 and 1e8 are not run.
> *(Status of 2026-10-20 follows.)* **Status (2026-10-20): accepted, NB1 and NB2 built.** NB2 (section 18) is the timing driver `scripts/bench_named.py` with the answer gate, the groupby forms of DuckDB, csvtk, Miller and
> pandas validated against NB1's reference, and a 1e6 smoke step on one shared cloud VM whose numbers are recorded and **not publishable**; Mac and gram steps, 1e7 and 1e8 are not run.
> *(Status of 2026-10-15 follows.)* The maintainer accepted every recommendation of section 15 on 2026-10-15: Q1 groupby q1 to q5
> and join q1 to q5 first; Q2 our own seeded Python generator, labelled as such, no R install; Q3 R only on gram and only as a separate yes (so not now); Q4 no
> 12 GB 1BRC file for now; Q5 a measured, capped ClickBench/TLC download is allowed later (not in NB1); Q6 no `--no-header` for the benchmark's sake; Q7 no
> rounding-mode flag for `mean`; Q8 the taxi set deferred; Q9 and Q10 as recommended; Q11 gram cores 0-6 only after confirming no soak is running (`pgrep`,
> `docker ps`, `uptime` first, else cores 0-5 as before); Q12 Mac runs are not publishable while loaded. **NB1 (section 17) is built: the groupby generator,
> the reference answers and the gates, with no clock. Where building it showed this document wrong, the text is corrected in place, marked
> "[corrected in NB1]", and listed in section 17.** The paragraphs below describe the document as it was when it was a design.
>
> **Original status: design only. No tool code, no script and no data was added by the pull request that added this file (#64), and nothing was run to
> measure speed.** This document follows the rules of `docs/numbers.md`, `docs/joins.md` and `docs/or-and-columns.md`: every decision has
> its argument, every fact about a benchmark that is not ours is cited with a URL, and what could not be cited is marked **unverified**.
> **It makes no claim about how fast `table` is on any of these benchmarks.** The only runs made while writing it were five-row syntax probes of
> the flag forms (section 2.3), with no clock.
>
> All sources were read on **2026-10-15** (the "access date" of every URL below). The maintainer's answer to "should `table` adopt externally
> comparable named benchmarks" was **yes**; the answers to the open questions of section 15 are not given yet.
>
> References to code are to `origin/main` at `5f4f509`. Two slices this design depends on are **open pull requests**, not merged:
> J2 of `docs/joins.md` (#62: `left`, `semi`, `anti`, typed keys) and A1 of `docs/or-and-columns.md` (#63: `or`, parentheses). Where a mapping needs
> one of them it says so.

## 0. The short version

| question | decision | why, in one line |
|---|---|---|
| what "comparable" means | **same questions, same data layout, same answer check, run side by side on our machines**. **Never** a comparison with the timings published by the benchmark's owner | their numbers come from other hardware and, for the H2O suite, from tables already in memory with the load left out (section 1) |
| which to adopt | **db-benchmark groupby (q1 to q5 now) and join (q1 to q5, after J2 and a pipe check)**, the **1BRC** task on the generator's files, **ClickBench's 16 expressible queries** plus the 16 that are expressible only below a cardinality bound, on a prefix of its file. **The taxi set only for its first two queries**, **simdjson's files as a correctness corpus, not a timing benchmark**, **TPC-H and TPC-DS never** | sections 4 to 10 |
| what is not adopted, because `table` refuses it | 4 of 10 groupby questions (median and sd, a difference of two aggregates, per-group top two, correlation), and the six-key grouping (q10) above 1,000,000 rows (**[corrected twice]** NB1 found that it trapped at any size; the fix of issue 66 made it a grouping like any other, bounded by the group ceiling; see section 17), the NA variants (no null), ClickBench's 11 queries with functions, regex, text `min`, `having` or `not like`, taxi queries 3 and 4 (a year and a rounded number are computed columns) | each is listed with the rule or the feature that blocks it, and **a refusal is a result of the suite, not a hole in it** (section 3.4) |
| the gate | **an answer is checked against a reference computed independently of every contender, before any time is printed; a contender that disagrees is reported and not timed** | the rule of `scripts/bench.py` and `scripts/bench_parallel.py`, unchanged |
| scale ladder | **1e6, 1e7, 1e8 rows** (groupby and join: about 51 MB, 0.5 GB, 5 GB for the left table, computed); **1e9 rows is out of budget on both machines** (50 GB) | the Mac has 43 GiB free, gram 67 GB (section 3.5) |
| data in git | **none.** Generators and checkers in git, data under `$NAMED_DATA` or `--out` (a directory outside the worktree; **[corrected in NB1]** the default is `<system temporary directory>/cancho-named-data`, not `~/named-data`, and the generator refuses a directory inside the repository), deleted after the run | the repository stays small and the data reproducible from a seed |
| the generator | **our own Python generator, same schema and same distributions, a fixed seed, not the same bytes as the owner's file** for the H2O suite (their generator is R, `set.seed(108)`, and R is installed on neither machine with `data.table`); **the owner's generator unchanged for 1BRC (Java) and the owner's file for ClickBench** | the bytes of an R `sample()` cannot be reproduced without R; the answer check does not need them (section 3.3) |
| scripts | `scripts/bench_named.py` (driver), `scripts/bench_named_gen.py`, `scripts/bench_named_ref.py`, `scripts/bench_named_questions.json` (**[corrected in NB1]** flat files in `scripts/`, not a `scripts/named/` directory: the existing scripts are flat, and a JSON question file is read by the driver and by the tests alike), all with `import procs as subprocess` (the reaper of #55) | no child outlives its runner |
| publishing | a section per suite in `docs/benchmarks.html`, **each number next to its conditions** (machine, cores, load, N, tool versions, the ladder step), **only numbers that were measured, with the claim worded to the question and the conditions** | the memory rule "claims only measured" |
| staging | NB1 to NB8, each a small pull request with its gates written first (section 13) | as J0 to J9 |

## 1. What "externally comparable" does and does not mean

Four ways in which a number from a named benchmark is **not** comparable with ours, each found in the sources:

1. **The H2O/DuckDB-Labs suite times a table that is already in memory.** The DuckDB script loads the CSV into a table, then times
   `CREATE TEMP TABLE ans AS SELECT ...` for each question, twice (`run=1`, `run=2`); the check sum is computed after the clock stops
   (`duckdb/groupby-duckdb.py` lines 81 to 100, read 2026-10-15). `table` reads and parses the CSV **inside** every run. A
   `table` time and a published DuckDB time are therefore not the same quantity. **The comparison we make is `table` against the contenders we run
   ourselves on the same file, whole process, output to `/dev/null`**, which is what `docs/benchmarks.html` already does.
2. **1BRC's own FAQ** says wall-clock results are comparable only on the same machine and asks for the baseline's time on the same hardware
   (README, "My solution runs in 2 sec on my machine"). We therefore never compare with the leaderboard (a Hetzner AX161, 8 cores, from a RAM disk),
   we run the Java baseline where we run `table` if we want a yardstick (section 6.6).
3. **ClickBench's published numbers are for the whole 100-million-row file on a c6a.4xlarge** (README: "All tests were originally run on AWS
   c6a.4xlarge EC2 VMs"). We run **a prefix** of the file (section 8.4), which is a different workload with the same queries. The result is labelled
   "ClickBench queries on a prefix of N rows", never "ClickBench".
4. **The taxi benchmark's data is the author's own dump** (Mark Litwintschik's post: "a data dump I've produced"), whose public availability is
   **unverified**; the public source, the TLC, replaced its CSV files with Parquet in May 2022 (section 7).

What is comparable, and is the point of adopting them: **the questions** (somebody else chose them, so we did not pick the ones `table` is good
at), **the data layout** (somebody else chose the columns, cardinalities and types) and **the answer check** (the owner's own check sums or expected
output files). A named benchmark also forces the list of what `table` cannot express to be written down by a third party's questions rather than ours.

## 2. Sources checked

### 2.1 The sources (access date of all: 2026-10-15)

| id | what | URL |
|---|---|---|
| S1 | db-benchmark, DuckDB Labs fork: README | <https://github.com/duckdblabs/db-benchmark> (raw: `.../master/README.md`) |
| S2 | groupby generator, R | `_data/groupby-datagen.R` of S1 |
| S3 | join generator, R | `_data/join-datagen.R` of S1 |
| S4 | data sets run (the fork) | `_control/data.csv` of S1; the original's is <https://github.com/h2oai/db-benchmark/blob/master/_control/data.csv> |
| S5 | the questions' names | `_control/questions.csv` of S1 |
| S6 | the questions as SQL, the clock, the check sums | `duckdb/groupby-duckdb.py`, `duckdb/join-duckdb.py`, `_helpers/helpers.py`, `_helpers/helpers.R`, `_report/report.R` of S1 |
| S7 | the report | <https://duckdblabs.github.io/db-benchmark/> and the original's <https://h2oai.github.io/db-benchmark/> |
| S8 | repository state | `gh api repos/duckdblabs/db-benchmark` and `repos/h2oai/db-benchmark` (field `pushed_at`) |
| S9 | 1BRC README (task, rules, evaluation, FAQ, results) | <https://github.com/gunnarmorling/1brc> |
| S10 | 1BRC generator (authoritative) | `src/main/java/dev/morling/onebrc/CreateMeasurements.java` of S9; the non-authoritative `src/main/python/create_measurements.py` |
| S11 | 1BRC baseline and expected outputs | `CalculateAverage_baseline.java`, `src/test/resources/samples/*.txt` and `*.out` of S9 |
| S12 | taxi: DuckDB post | <https://tech.marksblogg.com/duckdb-1b-taxi-rides.html> |
| S13 | taxi: the benchmark index | <https://tech.marksblogg.com/benchmarks.html> |
| S14 | taxi: how the data was built | <https://tech.marksblogg.com/billion-nyc-taxi-rides-redshift.html> and <https://github.com/toddwschneider/nyc-taxi-data> (README) |
| S15 | ClickBench README, queries, schema | <https://github.com/ClickHouse/ClickBench> (`README.md`, `clickhouse/queries.sql`, `clickhouse/create.sql`) |
| S16 | ClickBench data files | `https://datasets.clickhouse.com/hits_compatible/hits.{csv.gz,tsv.gz,json.gz,parquet}` (HTTP HEAD requests, and a 60 KB range of `hits.csv.gz`) |
| S17 | simdjson | <https://github.com/simdjson/simdjson> (`jsonexamples/`, `benchmark/`) |
| S18 | `canada.json` | <https://github.com/miloyip/nativejson-benchmark> (`data/`) |
| S19 | TPC-H, TPC-DS, fair use | <https://www.tpc.org/tpch/default5.asp>, <https://www.tpc.org/tpcds/default5.asp>, TPC Fair Use Quick Reference v1.0.0 (<https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC_Fair_Use_Quick_Reference_v1.0.0.pdf>), <https://duckdb.org/docs/current/core_extensions/tpch.html> |

Downloads made while checking, all small: the generator and driver scripts (text), `twitter.json` (631,515 bytes), `citm_catalog.json` (1,727,204),
`canada.json` (2,251,051), eleven 1BRC sample files (the largest, `measurements-rounding.txt`, 178,764 bytes), the first 60 KB of `hits.csv.gz`. No
benchmark data set was downloaded or generated.

### 2.2 The verified facts

Each row was read in the source named; "how" says whether it was read in a file, on a page, or computed here from what was read.

| # | fact | source / how |
|---|---|---|
| F1 | The DuckDB-Labs `db-benchmark` is a fork of `h2oai/db-benchmark` maintained by the DuckDB Labs team; its README says the benchmark is re-run routinely and is now updated on request (a pull request with new timings from a c6id.metal and a solution change). The fork's last push is 2026-09-23 and the original's 2023-06-29; neither is archived (GitHub API) | S1, S8 |
| F2 | The last report of the fork: generated 2026-08-17 13:10 CEST, the run took about 71.9 hours; machines c6id.4xlarge (16 cores, 32 GB) and c6id.metal (128 cores, 256 GB, Xeon Platinum 8375C) | S7 |
| F3 | The original's last report: generated 2021-07-02, 163.3 hours, Xeon E5-2630 v4, 40 cores, 125.78 GB | S7 |
| F4 | Sizes: "0.5 GB, 5 GB, 50 GB" are the tabs of the report, **1e7, 1e8, 1e9 rows**. For join the size is the left table; the right tables are small (left/1e6), medium (left/1e3), big (left) | S7, S4 |
| F5 | The fork's `_control/data.csv` activates **only five groupby sets, all 1e9 rows** (`G1_1e9_1e2_0_0`, `_1e1_0_0`, `_2e0_0_0`, `_1e2_0_1`, `_1e2_5_0`). The original's lists 1e7, 1e8 and 1e9 for groupby (the same five variants each) and join (`J1_*_NA_0_0`, `_NA_5_0`, `_NA_0_1`) and groupby2014 | S4 |
| F6 | The groupby generator: `Rscript groupby-datagen.R N K nas sort`, `set.seed(108)`, columns `id1` (`sprintf("id%03d", 1:K)`, text), `id2` (the same, drawn independently), `id3` (`sprintf("id%010d", 1:(N/K))`, text), `id4` (1..K, integer), `id5` (1..K, independently), `id6` (1..N/K, integer), `v1` (1..5), `v2` (1..15), `v3` (`round(runif(N, max=100), 6)`); every draw `sample(..., N, TRUE)`; file `G1_<N>_<K>_<nas>_<sort>.csv` written with `fwrite` (header line, no quoting needed) | S2 |
| F7 | NAs (`nas` > 0): for each `id`, whole **value groups** become NA (`nas` percent of the distinct values); for each `v`, `nas` percent of the rows. `sort=1` sorts by `id1`..`id6` | S2 |
| F8 | The ten groupby questions, in order: `sum v1 by id1`, `sum v1 by id1:id2`, `sum v1 mean v3 by id3`, `mean v1:v3 by id4`, `sum v1:v3 by id6` (the five "basic"), `median v3 sd v3 by id4 id5`, `max v1 - min v2 by id3`, `largest two v3 by id6`, `regression v1 v2 by id2 id4`, `sum v3 count by id1:id6` (the five "advanced"). The five join questions: `small inner on int`, `medium inner on int`, `medium outer on int`, `medium inner on factor`, `big inner on int` | S5; the SQL of each in S6 (quoted in sections 4 and 5) |
| F9 | The join generator: `Rscript join-datagen.R N K nas sort`, `set.seed(108)`, **N must be at least 1e7**, K is parsed and unused. Keys: a permutation of 1.1n integers split 90% common / 10% left-only / 10% right-only, for n = N/1e6, N/1e3, N. Left table `J1_<N>_NA_<nas>_<sort>`: `id1` `id2` `id3` (integers), `id4` `id5` `id6` (`"id%.0f"` of the integer, text), `v1` (`round(runif, 6)`); right tables: small `id1,id4,v2` (N/1e6 rows), medium `id1,id2,id4,id5,v2` (N/1e3), big `id1,id2,id3,id4,id5,id6,v2` (N). Every key appears at least once in its table (`sample_all`); the right tables' keys are unique per join column. N = 1e9 is written in 10 batches | S3 |
| F10 | Measured per run: `time_sec`, `mem_gb`, `chk`, `chk_time_sec`, `out_rows`, `out_cols`, `cache`, `on_disk`, `machine_type`, `run` (1 or 2). `chk` is a text of the sums of the output columns; `mem_gb` is the process RSS in the Python script (psutil) and is `NA` in the R helper ("disabled because during #110 system() kills the scripts"). The report keeps `time_sec_1` and `time_sec_2`; **which of the two the charts plot is unverified** | S6 |
| F11 | Solutions: 16 listed (dask, data.table, collapse, dplyr, pandas, (py)datatable, Spark, ClickHouse, Polars, Arrow, DuckDB, DuckDB-latest, DataFrames.jl, In Memory DataSets, Datafusion, (haskell)dataframe); tasks groupby, join, groupby2014. only open-source tools' results are merged | S1 |
| F12 | The DuckDB script runs on all cores (`PRAGMA THREADS=os.cpu_count()`; joins at 1e7 use at most 40), keeps tables in memory below 1e9 rows and on disk at 1e9 | S6 |
| F13 | 1BRC: each row is `<station name>;<measurement>` with exactly one fractional digit; the task is min, mean and max per station, printed on one line as `{Abha=-23.0/18.0/59.2, Abidjan=-16.2/26.0/67.3, ...}`, sorted by station name, each value rounded to one fractional digit | S9 |
| F14 | 1BRC rules: station name UTF-8, 1 to 100 bytes, no `;` or `\n`; temperature between -99.9 and 99.9, one fractional digit; at most **10,000 unique stations**; lines end in `\n`; no reliance on the data set; output rounding must use the IEEE 754 rounding direction roundTowardPositive (the rule's wording). Java only (non-JVM solutions cannot be formally submitted, per the FAQ); the 1e9-row file is about 12 GB (the README's figure) | S9 |
| F15 | 1BRC evaluation: Hetzner AX161 (32-core AMD EPYC 7502P, 128 GB), **8 cores, from a RAM disk**, `hyperfine`, five runs, slowest and fastest dropped, mean of three; the baseline takes "~2 min" there; the leaderboard's first entry is 1.535 s | S9 |
| F16 | 1BRC generator (Java): 413 stations hard-coded with a mean temperature each (computed here: 413 names, mean name length 7.95 bytes, none contains `"` or `;`, two contain `,`); each row picks a station with `ThreadLocalRandom` and writes `Math.round(nextGaussian(mean, 10) * 10.0) / 10.0` after `;`. **There is no seed**: two runs give two files. The Python script is "non-authoritative" and draws `random.uniform` over a global range, also unseeded | S10 |
| F17 | 1BRC baseline: accumulates in `double`, rounds with `Math.round(value * 10.0) / 10.0`. **The project's own sample `measurements-rounding` (4 rows for `ham`, 20,124 for `jel`) has the expected output `{ham=14.6/25.5/33.6, jel=-9.0/18.0/46.5}`; the exact mean of `ham` is 25.45** (computed here from the sample with exact fractions), so the expected answer rounds a tie **up**; half-to-even would give 25.4 | S11, computed |
| F18 | Taxi: 1.1 billion NYC taxi trips, 2009 to 2015, as 56 gzip-compressed CSV files, 104 GB compressed and 500 GB raw (the post's figures), 51 columns (the index page). **The four queries** (verbatim in section 7.1). The times are the fastest seen after running each query "multiple times"; **the number of runs is not stated**. The index page (updated 19 March 2024 by its footer) lists the same four queries as columns and DuckDB 0.10.0 on an Intel i9-14900K among its rows | S12, S13 |
| F19 | Taxi data source: Todd W. Schneider's repository builds the set from the NYC Taxi & Limousine Commission's data. Its README says the TLC changed the raw format from CSV to Apache Parquet in May 2022, replacing all historical files, and that the old CSV files are no longer downloadable from the TLC. Last push 2026-09-25, MIT | S14 |
| F20 | ClickBench: 43 queries in `queries.sql`; table `hits`; "Each of the 43 queries is run three times"; first run cold, runs two and three hot; the hot figure is the smaller of runs 2 and 3; summary is a geometric mean of ratios; all runs on c6a.4xlarge (500 GB gp2) originally; the repository was pushed to on 2026-10-15 | S15 |
| F21 | ClickBench data: CSV, TSV, JSON lines and Parquet. HEAD requests (Content-Length): `hits.csv.gz` 16,608,960,810 bytes, `hits.tsv.gz` 16,298,506,510, `hits.json.gz` 23,728,268,670, `hits.parquet` 14,779,976,446; last-modified 2022-06-25 to 06-27. **`hits.csv` has no header line** (the first 60 KB of the gzip, decompressed, begin with a data row) and quotes text with `"`. Row count and uncompressed size: **unverified** (not in the README) | S16 |
| F22 | simdjson `jsonexamples/` holds `twitter.json` (631,515 bytes), `citm_catalog.json` (1,727,204), `recipe_book.json`, `example_config.json` and `amazon_cellphones.ndjson` (277,673); **`canada.json` is not in simdjson's `jsonexamples`**: it is in nativejson-benchmark `data/` (2,251,051 bytes). Read here: `twitter.json` is one object `{statuses: [100 tweets], search_metadata}`; `citm_catalog.json` one object (`areaNames`, `events`, `performances`, ...); `canada.json` one GeoJSON `FeatureCollection` with one feature, a Polygon of 55,563 coordinate pairs. `amazon_cellphones.ndjson` is one **array** per line (its first line is the header array) | S17, S18, read here |
| F23 | simdjson's `benchmark/` has the tasks `find_tweet`, `top_tweet`, `distinct_user_id`, `partial_tweets` and others; they are **in-process library benchmarks** (a parser object, a padded in-memory string, a Google Benchmark loop) | S17 (the sources of `top_tweet`, `distinct_user_id`, `find_tweet`) |
| F24 | TPC fair use: TPC results may be named as such only when audited and published on the TPC site; anything else is a "non-TPC benchmark" and must be labelled as a derivation. DuckDB's TPC-H page: "all 22 queries", generated data has "small differences from the official TPC-H specification". The TPC-DS query count is **unverified** (not on the pages read) | S19 |
| F25 | This repository's DuckDB-comparison page (`docs/benchmarks.html`) already runs 4 groups of questions with: best of five, interleaved, `/dev/null`, answers first, peak RSS from `/usr/bin/time`; csvtk 0.38.0, Miller 6.22.0 (Linux only), DuckDB 1.5.6 | the file in this repository |

### 2.3 The machines (read-only checks made on 2026-10-15; nothing installed)

| | Mac (this one) | gram (`ssh gram`, one command at a time under `nice`) |
|---|---|---|
| CPU, RAM | Apple M4 Max, 16 cores, 64 GB | 12th Gen Intel i7-1260P, 16 logical CPUs, 31 GB |
| disk free | **43 GiB of 926** (96% used) | 67 GB of 733 (91% used); `/tmp` is a 16 GB tmpfs |
| load when read | **12.8, 16.4, 17.7** (other work running) | 1.0 |
| DuckDB | CLI 1.5.6 (Homebrew); the Python package is **not** installed | `~/bin/duckdb` 1.5.6 |
| csvtk | 0.38.0 | `~/bin/csvtk` 0.38.0 |
| Miller | **not on PATH** | `~/bin/mlr` 6.22.0 |
| pandas, pyarrow, numpy | pandas 3.0.3, pyarrow 24.0.0, numpy 2.4.6 (Python 3.14.5) | none installed (Python 3.14.4) |
| polars | **not installed** | **not installed** |
| jq, sqlite3 | jq 1.7.1, sqlite 3.51.0 | jq 1.8.1, sqlite 3.46.1 |
| Java | OpenJDK 26.0.1 | OpenJDK 25.0.4 |
| R | **not installed** | Rscript 4.5.2, **`data.table` not installed** |
| other | no `hyperfine`, `clickhouse`, `xsv`, `qsv`, `datamash` | none of those either; Docker present |

Consequences: polars is **skipped** (not installed; installing is not part of this design). Miller runs on gram only, as on the existing page. The authoritative H2O
generator (R with `data.table`) cannot run on either machine without an install, which is open question Q3. The 1BRC baseline (Java) can run on both.

The five-row probes of the flag forms used the build of the J2 branch (`cancho-table-j2/build/table`, version 0.3.0), because J2 is where the join
flags in section 5 live. They confirmed the forms printed in sections 4 to 8, and four behaviours that matter below: `min:COL` of a **text** column is
refused (`value.not-integer`); `--limit N --from M` pages **groups** (so `limit 10 offset 1000` of a grouped query is one command; `--top N --from M` did
not); a file with no header loses its first row to the header; `--group` and `--agg` with `--join` are refused (`args.conflict`, "a join gives rows in this version").

## 3. The method, common to every suite

### 3.1 What is recorded for every number

Machine (model, cores, RAM), the **number of cores each contender used** (`table --threads`, `SET threads` for DuckDB, `-j` for csvtk), the one-minute load
average before and after, tool versions (`table introspect` version and the commit of the build, `duckdb --version`, `csvtk version`, `mlr --version`,
Python and pandas), the data file's path class (internal SSD) and byte size and **sha256**, the ladder step, the output sink, the number of runs and the
statistic. A run whose one-minute load average at start exceeds a quarter of the cores is marked **loaded** and is not published as a
comparison (the Mac was at 12.8 to 17.7 when read, section 2.3, and the Linux line of `docs/benchmarks.html` already says "with other work running"); it may still be recorded.

### 3.2 The metrics

* **Wall time** of the whole process, from `fork` to exit, **minimum of 5** after one untimed warm-up, the contenders **interleaved** (each once per round,
  the order rotating), as `bench_parallel.py` does. All runs are kept in the JSON result, the **median** is reported next to the minimum, and the first
  run is listed separately (db-benchmark reports run 1 and run 2; ClickBench reports one cold and the best of two hot). 1BRC's own statistic (five runs,
  drop the slowest and the fastest, mean of three) is also computed for 1BRC, as a second column.
* **Peak resident set** of one extra run (`/usr/bin/time -l` on macOS, `-v` on Linux), as today. **[corrected in NB2]** The driver reads it from `wait4` through a small launcher of its own (section 18, reading 7), because
  `/usr/bin/time` is not present on the Linux VM NB2 ran on; the launcher's own resident set is a floor under every figure and is recorded with the result.
* **CPU time** (user plus system) of the same run, so that a multi-threaded contender's wall time is not read without its cost.
* **Output** to `/dev/null` for the timed runs; a separate untimed run gives the answer for the gate. **[corrected in NB2]** The answer is read from a pipe, not written to a file (reading 6).
* **Cache**: **warm only.** Dropping the page cache needs `sudo purge` (macOS) or a write to `/proc/sys/vm/drop_caches` (Linux), which this work does not do
  (the gram instruction is "no kernel settings"). The data is read once before the first timed run, and the result says "warm page cache". ClickBench's
  "cold" and "hot" columns are therefore not reproduced; only a hot-like figure is.
* **Thread counts**: `table` at 1 and at the machine's chosen number (6 on gram, as the page does; 4 and 16 on the Mac); DuckDB at 1 and at its default; csvtk at 1
  and Miller at 1 (their own parallel flags are reported if used). **[corrected in NB2]** Miller 6 has no thread flag: its "1" is the environment variable `GOMAXPROCS=1` of its Go runtime (reading 8); pandas has no setting and is
  recorded as "default".

### 3.3 The gate: the answer before the time

For every (suite, size, question, contender), **the contender's answer is compared with a reference before any time is printed**; a contender that disagrees
is reported with the first difference and **not timed**; a contender that refuses (a bound, an unsupported feature) is reported as "refused: RULE" with its
exit status and **not timed**. This is the rule of `scripts/bench.py`.

* **The reference is computed by the generator process from the arrays it is about to write**, with exact integer arithmetic (the `v` columns as integers;
  `v3` as an integer count of millionths, whose sums stay below 2^63 for 1e8 rows: 1e8 x 1e8 = 1e16), and **recomputed from the written file** by a
  second path (**[corrected in NB1]** by SQLite, which is in Python's standard library: pandas is installed on the Mac only, so a gate that needs it cannot run on gram or in CI; the SQLite path groups and sums in SQL and shares with the first path only the last rounding of a mean), the two must be equal before the file is used. A contender is never the
  reference.
* **Means.** `table` gives an exact mean rounded half to even at the scale asked (`mean:v3:dec(6)@6`); a double-based engine gives a double. The check
  compares every mean with the reference's exact rational: a `table` or exact-decimal answer must equal the reference rounded half to even at its scale; a double
  must be within 1e-9 relative. The tolerance is recorded with the result, and **an answer that passes only by tolerance is marked as such**.
* **Order.** Questions whose SQL has no `ORDER BY` (ClickBench q18, the groupby questions) are compared as sets of rows. A `LIMIT` with ties
  (ClickBench q24, q25) is checked as "the right number of rows, each a true row of the full answer, the sort keys equal to the reference's top-k keys".
* **The ordinary checks of the suites are kept as well**: db-benchmark's `chk` (the sums of the output columns), 1BRC's expected output files for the
  eleven sample files in the repository (the gate runs `table` on those first).

### 3.4 What the suite reports when `table` cannot answer

The result table has a row per question for **every** question of the owner's set, adopted or not. A question `table` cannot express is "not expressible:
<feature>", with the link to the document that says whether the feature is planned (`docs/backlog.md`, `docs/joins.md`, `docs/or-and-columns.md`); a question it
refuses at a size is "refused: <rule>". These are results, and they are published with the timings. No question is dropped to make a column look better.

### 3.5 Scales, disk and time

All sizes are **computed** from the formulas of the generators (not measured); the generator step of NB1 prints the real sizes, which replace these.

| suite | 1e6 rows | 1e7 rows (a named size) | 1e8 rows (a named size) | 1e9 rows |
|---|---|---|---|---|
| groupby `G1_*_1e2_0_0` | about 51 MB (non-named) | about 0.5 GB (the report's "0.5 GB" tab) | about 5 GB | about 50 GB: **no** |
| join left table `J1_*` | about 52 MB (non-named: the owner's generator needs N >= 1e7) | about 0.5 GB; right tables: small 10 rows, medium 10,000 rows, big about 0.6 GB | about 5 GB; big about 6 GB | **no** |
| 1BRC | about 12 MB | about 120 MB | about 1.2 GB | about 12 GB (the README's figure): gram only, if at all |
| ClickBench prefix | the first 1e6 lines of `hits.csv` | the first 1e7 lines | **no** (see 8.4) | **no** |
| taxi | only if a CSV exists (section 7) | | | |

How the estimate for the groupby row is made: per row, `id1` and `id2` are 5 bytes, `id3` 12, `id4` and `id5` about 2 digits, `id6` about 4.9 digits (K = 100),
`v1` 1, `v2` 1.7, `v3` about 8.9, plus 8 separators and a newline: about 51.5 bytes, which is consistent with the report's 0.5 GB label for 1e7 rows.

**The disk plan on the Mac (43 GiB free, 96% used):** one data set family at a time, deleted at the end of its run. The largest planned is 1e8 groupby
(5 GB) or 1e8 join (11 GB for the left and the big right table), never both together, and never with the 1e8 ClickBench prefix. If free space is below three
times the family's size when a run starts, `bench_named.py` refuses to generate (a gate of its own). `gram` has 67 GB free and 31 GB of RAM and takes
1e8 and, if the maintainer agrees (Q4), the 12 GB 1BRC file.

**Time.** The unit of budget is a process launch: (questions) x (contenders) x (1 warm-up + 5 runs) + 1 for peak memory + 1 for the answer. Each launch has a hard deadline
from `procs.py` (default 600 s); **a contender that exceeds 120 s on a question at a step is recorded "over 120 s" and dropped from the larger steps of that question**, so a
slow contender (the sources show csvtk and Miller reaching minutes on a 1 GB file in `docs/benchmarks.html`) cannot take a night. For example, 1e7 groupby: 5 questions x 5
contenders x 8 launches = 200 launches. The wall time of a ladder step is **not estimated here** (nothing was measured); NB2 prints it after the 1e6 step and the maintainer
decides whether 1e7 and 1e8 are run.

### 3.6 The contenders

| tool | where | used for |
|---|---|---|
| `table` | both | all, flag form and, where it exists, `--query` form (the gate also requires the two forms to give the same bytes, which `docs/query.md` already holds) |
| DuckDB 1.5.6 CLI | both | all; `read_csv` with type detection, `COPY (...) TO '/dev/null' (FORMAT csv)` as in `bench_parallel.py` |
| csvtk 0.38.0 | both | groupby, join, 1BRC |
| Miller 6.22.0 | gram | groupby, join, 1BRC (the `stats1` and `join` verbs) |
| pandas 3.0.3 | Mac | groupby, join, 1BRC; `pd.read_csv(...).groupby(...)`, the process wall time including import. pyarrow's reader is the engine as installed |
| polars | neither | **skipped** (not installed); revisit if the maintainer installs it |
| the 1BRC Java baseline | both | 1BRC only, as the yardstick the 1BRC FAQ asks for |
| `awk`, `sort | uniq -c` | both | floors where they apply, "do not understand CSV quoting", as on the page |

The exact command line of every contender is a function in `scripts/named/` and **each is validated against the reference before it is used**; the syntax
of the csvtk, Miller and pandas forms is **unverified** at the time of writing (only `table` and DuckDB forms were read in the owner's scripts or probed).
**[corrected in NB2]** The forms are functions of `scripts/bench_named.py` (not `scripts/named/`, section 0), and the groupby forms of all five are validated against the reference at 20,000 rows for q1 to q5 and q10 (section 18); the join forms (5.3) stay unverified.

## 4. db-benchmark, groupby

### 4.1 Layout (F6, F7)

`G1_<N>_<K>_<nas>_<sort>.csv`, header `id1,id2,id3,id4,id5,id6,v1,v2,v3`. The default case of the report is `K = 100`, no NAs, random order. Cardinalities
with K = 100: `id1`, `id2`, `id4`, `id5` have 100 values; `id3` and `id6` have N/K values (1e5 at 1e7 rows, 1e6 at 1e8). `v1` in 1..5, `v2` in 1..15, `v3` in [0, 100)
with up to 6 decimals. Variants (F5): K = 1e1 and 2e0 (few, large groups), sorted by the keys, and 5% NAs. **Adopted:** K = 1e2 (default), then K = 1e1 and 2e0 and the sorted file as
cheap follow-ups (the same questions, other file). **Not adopted: the NA variants**, because `table` has no null: an empty `v1` is `value.not-integer`, never skipped; adding
`v1 != ''` to the plan changes what the other aggregates see. A refusal is the result, and it is published as such (3.4).

### 4.2 Generation

The owner's is `groupby-datagen.R` (R with `data.table`, seed 108). It **cannot be reproduced byte for byte without R** (the draws are R's `sample`), and `data.table` is installed on neither
machine. So: **`scripts/named/gen_groupby.py`** (**[corrected in NB1]** pure Python with its own SplitMix64, specified in the script's docstring; numpy is not installed on gram and neither numpy's nor `random`'s streams are promised across versions, so the bytes are pinned by a digest that was checked on both machines) writes a file with the **same header, the same value sets, the same
formats** (`id%03d`, `id%010d`, integer ids, `round(v3, 6)` written with trailing zeros dropped as `fwrite` does, which the reference must mirror) and the same draw
shape (independent uniform draws with replacement), and records the seed, `N`, `K`, the sha256 and the exact answer. The result is labelled **"H2O-groupby layout, own generator, seed 108"**. If the maintainer prefers
the owner's bytes, Q3 is the route (install R and `data.table` on gram, run their script unchanged, still check the answers with our reference).

### 4.3 The questions and their `table` commands

SQL as the DuckDB script of the owner runs it (S6), the flag form, the `--query` form. `FILE` is `G1_<N>_1e2_0_0.csv`. Output is CSV to `/dev/null` for timing.

| q | SQL (owner's) | flag form | `--query` form | status |
|---|---|---|---|---|
| 1 | `SELECT id1, sum(v1) AS v1 FROM x GROUP BY id1` | `table --group id1 --agg sum:v1 --format csv FILE` | `select id1, sum(v1) from 'FILE' group by id1` | expressible |
| 2 | `SELECT id1, id2, sum(v1) AS v1 FROM x GROUP BY id1, id2` | `--group id1,id2 --agg sum:v1` | `... group by id1, id2` | expressible |
| 3 | `SELECT id3, sum(v1) AS v1, avg(v3) AS v3 FROM x GROUP BY id3` | `--group id3 --agg 'sum:v1,mean:v3:dec(6)@6' --max-groups 1000000` | `select id3, sum(v1), mean(v3::dec(6), 6) from 'FILE' group by id3` | expressible; **N/K groups: 1e5 at 1e7 rows is at the default `--max-groups` (100,000); 1e6 at 1e8 rows is the ceiling (1,000,000)** |
| 4 | `SELECT id4, avg(v1), avg(v2), avg(v3) FROM x GROUP BY id4` | `--group id4 --agg 'mean:v1@6,mean:v2@6,mean:v3:dec(6)@6'` | `select id4, mean(v1, 6), mean(v2, 6), mean(v3::dec(6), 6) ...` | expressible |
| 5 | `SELECT id6, sum(v1), sum(v2), sum(v3) FROM x GROUP BY id6` | `--group id6 --agg 'sum:v1,sum:v2,sum:v3:dec(6)' --max-groups 1000000` | `select id6, sum(v1), sum(v2), sum(v3::dec(6)) ...` | expressible; N/K groups as q3 |
| 6 | `quantile_cont(v3, 0.5) AS median_v3, stddev(v3) ... GROUP BY id4, id5` | none | none | **not expressible**: no median and no standard deviation among `count, sum, min, max, mean, distinct` (`describe`, backlog item 4, is not built and lists neither) |
| 7 | `max(v1) - min(v2) AS range_v1_v2 ... GROUP BY id3` | `--agg max:v1,min:v2` gives the two operands | none | **not expressible**: the difference is a computed column ("no, not now", `docs/or-and-columns.md` section 8) |
| 8 | `unnest(max(v3, 2)) ... WHERE v3 IS NOT NULL GROUP BY id6` | none | none | **not expressible**: no per-group top-k (`--top` takes the first groups, not the first values of each group) |
| 9 | `pow(corr(v1, v2), 2) ... GROUP BY id2, id4` | none | none | **not expressible**: no correlation |
| 10 | `SELECT id1..id6, sum(v3), count(*) ... GROUP BY id1..id6` | `--group id1,id2,id3,id4,id5,id6 --agg 'sum:v3:dec(6),count' --max-groups 1000000` | `... group by id1, id2, id3, id4, id5, id6` | **bounded (class B)**: about N groups against `--max-groups`' **ceiling of 1,000,000** (`limit.too-many-groups` above it, tagged, exit 8). Measured: at 1e6 rows it answers, 1,000,000 groups, equal to the reference in both forms; at 1.2e6 rows it is the refusal, so at 1e7 and 1e8 too. **[corrected in NB1, and again by the fix of issue 66: NB1 measured a trap (exit 132, no output) with five or more group columns at any size, which made the question impossible; the cause was an integer overflow in a cache of `agg.add_fast`, not a limit, and the fix leaves the bound this row first described. Section 17.]** The ceiling is a constant of the tool, not a flag |

`mean:COL@N` and `sum:COL:dec(6)` are the exact forms of `docs/numbers.md`; `:float` would also work and is cheaper to write but its sum is the exact sum of doubles,
a different quantity from a double engine's. The owner's questions 1 to 5 are therefore **adopted now** (5 of 10), 6 to 9 are **listed as not expressible** and 10 as **bounded**. The
mean's scale (`@6`) is a choice of this design (an average of values with 6 decimals), recorded with the result.

### 4.4 The bounds that apply

`--max-groups` (default 100,000, ceiling 1,000,000), `--max-state-bytes` (default 64 MiB, ceiling 1 GiB; a float `sum` costs 584 bytes a group, `docs/numbers.md`, which is why the plan above uses `:dec(6)`
for `v3`), `--max-rows` (default 10,000,000, ceiling 1,000,000,000: it bounds **CSV rows out**, `limit.too-many-rows`, and does not apply to a grouped answer). Whether a plain scan of
1e8 rows is accepted without raising a flag is **unverified** (not run; NB2 starts with it) and is the first thing the 1e8 step reports.

### 4.5 Competitors' forms (to be validated in NB2 against the reference)

DuckDB: the owner's SQL, over `read_csv('FILE')` instead of a loaded table, `COPY (...) TO '/dev/null' (FORMAT csv)`. csvtk: `csvtk summary -g id1 -f v1:sum` (unverified syntax). Miller:
`mlr --icsv --ocsv stats1 -a sum -f v1 -g id1` (unverified). pandas: `pd.read_csv(FILE).groupby('id1', as_index=False)['v1'].sum()` timed as a process.
**[corrected in NB2]** All four are validated (section 18, reading 8): csvtk's `summary` prints floats to **2 decimals by default** (`3128.00`, and a mean rounded at 2 decimals fails any check), so the form passes `-w 12`; Miller's `stats1` applies every
accumulator to every field, so a question that asks `sum v1` and `mean v3` is `stats1 -a sum,mean -f v1,v3 then cut -o -f id3,v1_sum,v3_mean`; pandas groups with `sort=False` and writes with `to_csv`.

## 5. db-benchmark, join

### 5.1 Layout (F9)

Left table `x` = `J1_<N>_NA_0_0.csv`: `id1,id2,id3,id4,id5,id6,v1` (`id1` in about N/1e6, `id2` in about N/1e3, `id3` in about N distinct values; `id4..id6` the text `"id" + the integer`). Right tables: `small`
(`id1,id4,v2`, N/1e6 rows), `medium` (`id1,id2,id4,id5,v2`, N/1e3), `big` (`id1,id2,id3,id4,id5,id6,v2`, N). 90% of the left keys have a match; each right table's join key is unique. The owner's generator has
a floor of N = 1e7, so the **ladder for join is 1e7 and 1e8** (named) and, for development only, **a scaled-down 1e6 that is not a named size** (own generator, same structure). Generator: `scripts/named/gen_join.py`, with the reference
(row count, `sum(v1)`, `sum(v2)`, as the owner's `chk`) computed from the key arrays. **[corrected in NB3]** The files are `scripts/bench_named_join_gen.py`, `bench_named_join_ref.py` and `bench_named_join_questions.json` (flat, as NB1's); the reference is read from the written files and is a fingerprint of the joined rows, the owner's `chk` plus a row digest (section 19); the small table has `max(N/1e6, 10)` rows and the medium `max(N/1e3, 10)`, so the development size 1e6 keeps a small table with a miss.

### 5.2 The questions (S6)

| q | owner's SQL | `table` (flag form; the probe is the operand, the build side is `--join`) | status |
|---|---|---|---|
| 1 small inner on int | `x JOIN small USING (id1)` | `table --join small.csv --on id1 --join-prefix small_ --max-join-rows 1000000000 --max-rows 1000000000 --format csv x.csv` | expressible after J1 (merged); the build side is 10 rows at 1e7 |
| 2 medium inner on int | `x JOIN medium USING (id2)` | `--join medium.csv --on id2 --join-prefix medium_` | expressible; 10,000 build rows at 1e7 |
| 3 medium outer on int | `x LEFT JOIN medium USING (id2)` | `--join medium.csv --on id2 --join-type left --join-prefix medium_` | **needs J2 (#62, open)**: unmatched left rows get empty cells where DuckDB gives NULL; the reference's `sum(v2)` skips them in both. **[corrected in NB3]** J2 is merged and q3 is built: the unmatched rows come out with the build side's cells empty, the reference counts them as rows with v2 0 and `matched` 0, and the fingerprint of table's answer equals it, as does every contender's |
| 4 medium inner on factor | `x JOIN medium USING (id5)` | `--join medium.csv --on id5 --join-prefix medium_` | expressible (text key, as the key is text in the file) |
| 5 big inner on int | `x JOIN big USING (id3)` | `--join big.csv --on id3 --join-prefix big_ --max-state-bytes 1073741824` | **bounded**: the build side has N rows; at 1e8 rows it exceeds the 1 GiB ceiling of `--max-state-bytes` unless a row accounts for under 10.7 bytes (arithmetic, 1e8 x 10.7 = 1.07e9), so a refusal `limit.state-too-large` is expected at 1e8; at 1e7 it is **unverified** (the accounted bytes are the formula of `docs/joins.md` section 6.3, applied to a 7-column row; to be computed in NB3 before running) |

`--on NAME` (the shared-name key) is the form of `USING`; with `--join-prefix P` every build column is written `P`+name, and the key once. Column **names** therefore differ from the owner's output
(`small_id4`, `small_v2` against `small_id4`, `v2`); the gate compares **values**. The joined rows are the answer: `--agg` and `--group` with `--join` are `args.conflict` today (J3 is not built). **[corrected in NB3: J3 is merged and `--group`/`--agg` work over a join; NB3 does not use them for the check, because the check should read the joined rows themselves: a sum cannot see a wrong pairing, a row digest can (section 19).]** So **the answer check
is a separate, untimed pipe**: the joined CSV is written to a file, then `table --agg 'sum:v1:dec(6),sum:v2:dec(6),count' FILE` (standard input is built, R1) gives the owner's `chk` (the sums of `v1` and `v2`) and
the row count. The timed run writes the joined rows to `/dev/null`; DuckDB's timed run is `COPY (SELECT x.*, ... FROM x JOIN ...) TO '/dev/null'`. A joined row is wide (13 cells), so the output volume is the cost of
the question as much as the join; it is reported (bytes written) in the result.

`--query` has no join form yet (J3b of `docs/joins.md`), so the join questions have a flag form only. **[corrected in NB3]** J3b is merged: each question has a `--query` form (`select a.id1, ..., b.v2 from 'x.csv' as a [left] join 'R.csv' as b on a.k = b.k`), held to the same fingerprint (section 19, reading 10).

### 5.3 Competitors

DuckDB (the owner's SQL), csvtk (`csvtk join`, loads whole files: unverified, may be refused by memory at 1e8), Miller (`mlr join`, sorted-input and unsorted modes: unverified), pandas (`merge`). Each is gated. **[corrected in NB3]** All four forms are validated against the reference at 2,000 and 20,000 rows and ran at 1e6 rows (section 19.2, reading 12); Miller's unsorted mode is the one used, and csvtk did not run out of memory at 1e6.

## 6. The 1 Billion Row Challenge

### 6.1 The task (F13, F14)

Input: `<station>;<temperature>` per line, one decimal, `\n`. Output: one line, `{station=min/mean/max, ...}` sorted by station, values rounded to one decimal, `, ` between entries. Rules: up to 10,000 distinct stations
of 1 to 100 bytes, not containing `;` or `\n`; temperatures -99.9 to 99.9 with one decimal; the program must not depend on the data set.

### 6.2 The data: the owner's generator, with its two properties

`CreateMeasurements.java` (`./create_measurements.sh N`, or `java CreateMeasurements.java N` as a single source file: **unverified**, the README uses the Maven wrapper) writes `measurements.txt` in the current directory.
It is **authoritative** (README), **unseeded** (F16: `ThreadLocalRandom`), 413 stations. So the data is not reproducible from a seed: **the sha256 of the file is the identity**, recorded with every result, and the reference answer is
computed from the file itself by an independent program (a streaming Python or Java reader with exact integer tenths), never from the generator. A seeded Python port (`scripts/named/gen_1brc.py`, own seed, the 413 stations and means
copied from the Java source with the licence noted) is provided for reproducible small steps and is labelled "own generator".

### 6.3 The mapping, and where `table` does not fit

`table` has **no headerless input**: the first line is the header (probed: `x;1.5` as the first line gave the header `x`). So the file given to `table` (and, for fairness, to every contender) has the line `station;temp` prepended: a
copy of the file, **declared as a deviation**. The delimiter `;` exists (`--delimiter ';'`).

| piece | `table` | note |
|---|---|---|
| read, group by station | `table --delimiter ';' --group station --agg 'count,min:temp:dec(1),sum:temp:dec(1),max:temp:dec(1)' --format csv m.csv` | `:dec(1)` is exact; the groups are 413 (at most 10,000 by the rules), far under `--max-groups` |
| the mean | **not computed by `table` for this task** | `mean:temp:dec(1)@1` rounds **half to even** (`docs/numbers.md` 3.6) and the owner's own sample expects ties **up** (F17: `ham`, exact mean 25.45, expected 25.5, half-to-even gives 25.4). So `table`'s mean cannot reproduce the expected output. The suite takes `count`, `min`, `sum`, `max` (all exact) and a single formatter outside the tool computes the mean with ties up |
| the order | groups come out in bytewise key order | equal to Java's `TreeMap` order for names in the Basic Multilingual Plane (UTF-8 byte order equals code point order, UTF-16 order differs only for supplementary characters); the 413 names are all in the BMP (**unverified for every name; the gate checks the order on the output**) |
| the output line | `{Abha=-23.0/18.0/59.2, ...}` | `table` writes CSV or JSON; the formatter (about ten lines) writes the owner's line from `count,min,sum,max`. **The time of `table` alone is reported, and the formatter's time is reported separately**; the answer that is gated is the formatter's line, identical for every contender (3.3) |
| a station name that starts with `"` | **not 1BRC-conformant** | the rules allow it, and an RFC 4180 reader reads it as a quoted field. The generator's 413 names have none, so the suite passes on the generator's files; it is **stated in the result** that `table` is not a conforming 1BRC solution for arbitrary names |

DuckDB: `select station, count(*), min(temp), sum(temp), max(temp) from read_csv('m.csv', delim=';', header=true, columns={'station':'VARCHAR','temp':'DECIMAL(4,1)'}) group by station order by station` (**unverified**
as to type options; validated in NB5), so that its ingredients are exact too; the native `avg` form is run as a second, ungated column and its ties-differences are reported. csvtk, Miller, pandas, the Java baseline:
the same four ingredients where possible; the baseline prints the owner's line itself and is compared as the line.

### 6.4 Scales (3.5)

1e6, 1e7, 1e8 on the Mac (12 MB, 120 MB, 1.2 GB); 1e9 (about 12 GB) is on gram only and only on a yes to Q4. The unmodified owner's program, `CalculateAverage_baseline.java`, is the yardstick; its time at 1e8 is
**unmeasured here**.

### 6.5 Conditions specific to 1BRC

The owner evaluates from a RAM disk with 8 cores; we run from the internal SSD with a warm cache and report the cores used by each contender, and we say so. We compute the owner's statistic (drop the slowest and the fastest of five, mean of three) as a second column.

### 6.6 The expected outputs

The eleven sample pairs in the repository (`measurements-*.txt` and `.out`, F17) are run first through every contender: **a contender that does not reproduce an `.out` file exactly is not timed on the large file**. `table` plus the formatter
is expected to pass them; this is to be confirmed in NB5, not claimed here. Whether the official 1e9 file's expected output is published: **unverified**.

## 7. The NYC taxi ("billion taxi rides")

### 7.1 The query set, verbatim from the post (S12)

```sql
SELECT   cab_type, COUNT(*) FROM trips GROUP BY cab_type;                                        -- Q1
SELECT   passenger_count, AVG(total_amount) FROM trips GROUP BY passenger_count;                 -- Q2
SELECT   passenger_count, DATE_PART('year', pickup_datetime) AS year, COUNT(*)
FROM     trips GROUP BY passenger_count, year;                                                   -- Q3
SELECT   passenger_count, DATE_PART('year', pickup_datetime) AS year, ROUND(trip_distance) AS distance, COUNT(*)
FROM     trips GROUP BY passenger_count, year, distance ORDER BY year, count(*) DESC;            -- Q4
```

(formatted onto fewer lines here; the post writes each as a multi-line statement over `trips`, or over `READ_PARQUET('trips_x*.pq')`).

### 7.2 Mapping

| q | `table` | status |
|---|---|---|
| Q1 | `table --group cab_type --agg count --format csv trips.csv` | expressible (if the CSV has a `cab_type` column: it does in Mark's schema; not in the TLC's raw files, where Todd's repository adds it) |
| Q2 | `--group passenger_count --agg 'mean:total_amount:dec(6)@6'` (`:dec(6)` in his DECIMAL(18,6) schema; `:float` for the Parquet-derived DOUBLE schema) | expressible |
| Q3 | none | **not expressible**: `year(pickup_datetime)` is a function of a column, a computed column (`docs/or-and-columns.md` section 8). With a **prepared `pickup_year` column** (added once, outside the timed run, and declared), `--group passenger_count,pickup_year --agg count` |
| Q4 | none | **not expressible**: the year and `ROUND(trip_distance)`. With prepared `pickup_year` and `distance_rounded` columns and `--sort`: the `ORDER BY year, count(*) DESC` is two keys, one of which is an aggregate, and `--sort` takes one output column; **not expressible even with the prepared columns** (a second `--sort` key is not built) |

### 7.3 The data, and why the taxi set is staged last

The CSV of the post is the author's own 56-file, 104 GB gzipped, 500 GB raw dump (F18); **its public location is not given in the post (unverified)**. The public sources are the TLC's files, which are Parquet since May 2022 (F19), and `table` reads
no Parquet (backlog item 10). A 500 GB raw set does not fit either machine. The only adoptable form is **a CSV made from a handful of TLC months** with a declared conversion (DuckDB, outside the timed region), at 1e6 to 1e7 rows, running Q1 and Q2 as written and Q3 as the prepared-column variant. It is labelled
"taxi-style queries on N rows of TLC data converted to CSV", it is **not** "the billion taxi rides benchmark", and it is NB7, behind the others, because it measures the same two shapes (a low-cardinality count and a mean) that groupby q1 and q4 already measure. Recommendation in Q8.

## 8. ClickBench

### 8.1 The data and the table (F20, F21)

`hits`: 105 columns (`create.sql`), 43 queries, three runs each. The CSV has **no header** (so the same one-line deviation as 1BRC: a header line with the 105 names from `create.sql`, prepended) and quotes text with `"`. The file is **16.6 GB gzipped**; its
uncompressed size and row count are **unverified**. `--max-fields` is 256 by default and 4096 at most, and 105 columns fit.

### 8.2 The 43 queries against the surface

"Surface" = `--where` (and, or after A1, `or` and parentheses; `contains`, `in`, comparisons, typed `:int :dec(S) :float`), `--group`, `--agg` of `count sum min max mean distinct` (min and max of **integers, decimals, floats, not text**: probed), `--order-by`, `--sort`, `--limit --from`, `--top`, `--join` (J1/J2), JSONL.
**E** = expressible with today's surface. **B** = expressible, but a bound of the tool (`--max-groups` ceiling 1,000,000; `--max-distinct` ceiling 10,000,000; `--max-state-bytes` ceiling 1 GiB) can refuse it as the number of groups or distinct values grows; whether it does at a given prefix depends on cardinalities that are **unverified** (the README gives no
distinct counts). **X** = not expressible.

| q | query (shortened) | class | `table` command or the blocker |
|---:|---|---|---|
| 1 | `COUNT(*)` | E | `--agg count` |
| 2 | `COUNT(*) WHERE AdvEngineID <> 0` | E | `--where "AdvEngineID:int != 0" --agg count` |
| 3 | `SUM(AdvEngineID), COUNT(*), AVG(ResolutionWidth)` | E | `--agg 'sum:AdvEngineID,count,mean:ResolutionWidth@6'` |
| 4 | `AVG(UserID)` | E | `--agg mean:UserID@6` (sums are pairs of integers and never wrap) |
| 5 | `COUNT(DISTINCT UserID)` | B | `--agg distinct:UserID --max-distinct 10000000` |
| 6 | `COUNT(DISTINCT SearchPhrase)` | B | `--agg distinct:SearchPhrase` |
| 7 | `MIN(EventDate), MAX(EventDate)` | X | **min/max of text**: `min:EventDate` is `value.not-integer`. Two commands (`--order-by EventDate --limit 1` and `-EventDate`) give the rows, not one query; not counted. No backlog item |
| 8 | `AdvEngineID, COUNT(*) WHERE <> 0 GROUP BY ... ORDER BY COUNT(*) DESC` | E | `--where "AdvEngineID:int != 0" --group AdvEngineID --agg count --sort -count` |
| 9 | `RegionID, COUNT(DISTINCT UserID) ... ORDER BY u DESC LIMIT 10` | B | `--group RegionID --agg distinct:UserID --sort -distinct:UserID --limit 10` (the pairs are (RegionID, UserID)) |
| 10 | `RegionID, SUM, COUNT, AVG, COUNT(DISTINCT UserID) ... ORDER BY c DESC LIMIT 10` | B | `--group RegionID --agg 'sum:AdvEngineID,count,mean:ResolutionWidth@6,distinct:UserID' --sort -count --limit 10` |
| 11 | `MobilePhoneModel, COUNT(DISTINCT UserID) WHERE <> '' ...` | B | `--where "MobilePhoneModel != ''" --group MobilePhoneModel --agg distinct:UserID --sort -distinct:UserID --limit 10` |
| 12 | `MobilePhone, MobilePhoneModel, COUNT(DISTINCT UserID) ...` | B | as 11 with two group columns |
| 13 | `SearchPhrase, COUNT(*) WHERE <> '' ... LIMIT 10` | B | `--where "SearchPhrase != ''" --group SearchPhrase --agg count --sort -count --limit 10` (groups = distinct phrases) |
| 14 | `SearchPhrase, COUNT(DISTINCT UserID) ...` | B | groups and distinct pairs both bounded |
| 15 | `SearchEngineID, SearchPhrase, COUNT(*) ...` | B | two group columns |
| 16 | `UserID, COUNT(*) GROUP BY UserID ORDER BY COUNT(*) DESC LIMIT 10` | B | groups = distinct users: the **ceiling of 1,000,000 groups** refuses any prefix with more distinct users |
| 17 | `UserID, SearchPhrase, COUNT(*) ... ORDER BY COUNT(*) DESC LIMIT 10` | B | as 16, more groups |
| 18 | `UserID, SearchPhrase, COUNT(*) GROUP BY ... LIMIT 10` (no `ORDER BY`) | B | any 10 groups are a correct answer; gated as "10 true groups" (3.3) |
| 19 | `extract(minute FROM EventTime)` in the grouping | X | a function of a column: computed columns, not proposed (`docs/or-and-columns.md` section 8) |
| 20 | `SELECT UserID WHERE UserID = 435090932899640449` | E | `--select UserID --where "UserID = '435090932899640449'"` |
| 21 | `COUNT(*) WHERE URL LIKE '%google%'` | E | `--where "URL contains 'google'" --agg count` (bytewise, case-sensitive, as `LIKE` in the engines run; `ILIKE` is not used) |
| 22 | `SearchPhrase, MIN(URL), COUNT(*) WHERE URL LIKE ... ` | X | **min of text** (as 7) |
| 23 | `... MIN(URL), MIN(Title) ... Title LIKE '%Google%' AND URL NOT LIKE '%.google.%'` | X | min of text, and **`not contains`**: `not` is designed (`docs/or-and-columns.md` section 3) and not built (A2) |
| 24 | `SELECT * WHERE URL LIKE '%google%' ORDER BY EventTime LIMIT 10` | E | `--where "URL contains 'google'" --order-by EventTime --limit 10` (ties on `EventTime`: gated by key, 3.3) |
| 25 | `SearchPhrase WHERE <> '' ORDER BY EventTime LIMIT 10` | E | `--select SearchPhrase --where "SearchPhrase != ''" --order-by EventTime --limit 10` (sorting by an unselected column was probed to work) |
| 26 | `SearchPhrase WHERE <> '' ORDER BY SearchPhrase LIMIT 10` | E | `--select SearchPhrase --where ... --order-by SearchPhrase --limit 10` |
| 27 | `... ORDER BY EventTime, SearchPhrase LIMIT 10` | E | `--order-by EventTime,SearchPhrase` |
| 28 | `CounterID, AVG(length(URL)) ... HAVING COUNT(*) > 100000 ...` | X | `length()` and `HAVING` (`query.unsupported`, `docs/query.md`) |
| 29 | `REGEXP_REPLACE(Referer, ...)`, `length`, `HAVING` | X | regular expressions, `length()`, `HAVING` |
| 30 | `SUM(ResolutionWidth)`, `SUM(ResolutionWidth + 1)` ... `+ 89` (90 sums) | X | arithmetic in an aggregate argument: computed columns |
| 31 | `SearchEngineID, ClientIP, COUNT(*), SUM(IsRefresh), AVG(ResolutionWidth) WHERE SearchPhrase <> '' GROUP BY ... ORDER BY c DESC LIMIT 10` | B | two group columns |
| 32 | `WatchID, ClientIP ... WHERE SearchPhrase <> ''` | B | groups about as many as rows kept |
| 33 | `WatchID, ClientIP ... GROUP BY` (no filter) | B | groups about as many as rows: **refused above 1,000,000 rows** by the group ceiling, so it runs on the 1e6 prefix at most |
| 34 | `URL, COUNT(*) GROUP BY URL ORDER BY c DESC LIMIT 10` | B | groups = distinct URLs; keys are long, `--max-state-bytes` counts them |
| 35 | `SELECT 1, URL, COUNT(*) ... GROUP BY 1, URL` | X | a constant in the select list: computed column |
| 36 | `ClientIP, ClientIP - 1, ClientIP - 2, ClientIP - 3, COUNT(*) ...` | X | arithmetic |
| 37 | `URL, COUNT(*) WHERE CounterID = 62 AND EventDate >= '2013-07-01' AND EventDate <= '2013-07-31' AND DontCountHits = 0 AND IsRefresh = 0 AND URL <> '' ... LIMIT 10` | E | `--where "CounterID:int = 62 and EventDate >= '2013-07-01' and EventDate <= '2013-07-31' and DontCountHits:int = 0 and IsRefresh:int = 0 and URL != ''" --group URL --agg count --sort -count --limit 10` (the dates are text, which sorts as dates) |
| 38 | the same over `Title` | E | as 37 |
| 39 | `... IsLink <> 0 AND IsDownload = 0 ... LIMIT 10 OFFSET 1000` | E | as 37 with `--limit 10 --from 1000` (paging groups probed on a five-row file) |
| 40 | `CASE WHEN (SearchEngineID = 0 AND AdvEngineID = 0) THEN Referer ELSE '' END AS Src` | X | `CASE`, a computed column |
| 41 | `URLHash, EventDate ... TraficSourceID IN (-1, 6) AND RefererHash = 3594120000172545465 ... OFFSET 100` | E | `--where "CounterID:int = 62 and EventDate >= '2013-07-01' and EventDate <= '2013-07-31' and IsRefresh:int = 0 and TraficSourceID in (-1, 6) and RefererHash = '3594120000172545465'" --group URLHash,EventDate --agg count --sort -count --limit 10 --from 100` |
| 42 | `WindowClientWidth, WindowClientHeight ... URLHash = 2868770270353813622 ... OFFSET 10000` | E | `--limit 10 --from 10000` |
| 43 | `DATE_TRUNC('minute', EventTime) ... OFFSET 1000` | X | a function of a column |

**Count: 16 E (1 to 4, 8, 20, 21, 24 to 27, 37 to 39, 41, 42), 16 B (5, 6, 9 to 18, 31 to 34), 11 X (7, 19, 22, 23, 28 to 30, 35, 36, 40, 43).** The classification was done by reading `queries.sql` against the flags and the probes of 2.3; it has **not** been run on the real file, so each B is a prediction until NB6. After A1 (#63) and `--where` with `or` nothing in the table changes: no query needs `or`.
The X reasons that would be cheapest to lift are the text `min`/`max` (7, 22, 23: three queries; not on any backlog) and `not contains` (23: A2).

### 8.3 What follows

Adoptable now: the 16 E queries. The 16 B queries are run with the bounds raised to their ceilings and **each refusal is a result**. The 11 X are published as "not expressible: reason".

### 8.4 The scale: a prefix, taken with a range request

The whole file is 16.6 GB gzipped (F21) and its CSV is far larger: it does not fit the Mac. The prefix of N lines is made with `curl -r 0-<bytes>` of `hits.csv.gz` into `gzip -dc` and `head -n N` (a gzip prefix decompresses up to its last whole block; the first 60 KB did), so a 1e7-line prefix needs
the download of its compressed bytes only (an estimate from the file's 16.6 GB over its unknown row count: **unverified**; NB6 starts by measuring the compressed bytes of the first 1e6 lines and decides what is affordable). The first rows are from one counter and day (the first three rows read have `EventDate` 2013-07-15 and `CounterID` 17), which
changes the cardinalities (q37 to q42 filter on `CounterID = 62` and July 2013 and may select **no** row from a prefix: such a question is reported "empty answer at this prefix", not timed as a comparison). A prefix is therefore a different workload from the full file, and the page says "the first N lines of `hits.csv`".

## 9. simdjson's `twitter.json`, `citm_catalog.json`, `canada.json`

### 9.1 What they are for

They are the JSON-parsing corpus of a family of libraries (simdjson, RapidJSON, yyjson; `canada.json` is from nativejson-benchmark): small files (0.6, 1.7 and 2.3 MB) parsed **in process, many times**, reported as throughput (F22, F23). The simdjson tasks (`find_tweet`, `top_tweet`, `distinct_user_id`, `partial_tweets`) read named fields out of the parsed document.

### 9.2 Is a comparison fair? No, for timing; yes, for coverage

* **`table` does not parse a JSON document.** It reads JSON **lines** with declared `--fields` (`docs/readers.md` section 5): a line that opens a document is refused (`parse.jsonl-syntax`, with `looks_like: json-document` and the hint to use `jq -c '.[]'`); a line that is an array is `parse.jsonl-not-object`, which is what `amazon_cellphones.ndjson` (one array per line) would meet; a declared path that reaches an object or an array is `parse.jsonl-not-scalar`.
* **`canada.json` has no JSON-lines form at all**: one feature, one polygon, coordinates nested three deep. There is nothing to declare as fields. It is **not comparable**.
* **`twitter.json`** has a natural lines form: `jq -c '.statuses[]' twitter.json` gives 100 lines, one per tweet, and `table --input-format jsonl --fields id,user.screen_name,retweet_count,user.id --format csv FILE` can run `top_tweet` and `distinct_user_id` (the
  `retweeted_status.user.id` branch is `--absent` handling, `docs/readers.md` section 5). The conversion is `jq`'s, outside `table`, and **the file is 631 KB: a run is dominated by process start-up** (`docs/backlog.md` records 0.013 s for DuckDB's start-up alone), so a timing against simdjson's in-process figures is meaningless.
* **`citm_catalog.json`**: `events` is an object keyed by id, not an array; its lines form needs a `jq` program that is itself a choice. Same conclusion.

So the simdjson files are adopted as a **correctness corpus** (NB8): `jq -c` makes the lines, `table` reads them with declared fields, and the cells are compared with Python's `json` (a differential test as `tests/conformance` does for CSV), including the refusals above, with the **expected refusal as the pass condition** for `canada.json` and for the array-per-line file. They are **not** a timing benchmark. If timing against JSON tools is wanted, the right data is a large generated JSON-lines file (what `scripts/bench_jsonl.py` already does), against `jq` and DuckDB `read_json`, which is a comparison of tools and not of a named benchmark.

## 10. TPC-H and TPC-DS: out of scope

1. **They need what `table` is designed not to have.** TPC-H is 22 queries (F24). From general knowledge, **not read from the TPC pages and so unverified here**, they use multi-way joins, subqueries, `HAVING`, date arithmetic, `CASE` and arithmetic over decimals, and TPC-DS is larger. `docs/query.md` lists a subquery, `union`, `having` and `select distinct` as `query.unsupported`
   by decision, and `docs/joins.md` allows **one** join. The two single-table TPC-H queries a reader might hope for (the pricing summary and the forecasting-revenue query) use products of columns (`l_extendedprice * (1 - l_discount)`), which are computed columns (also unverified); whether even those fit is not worth a column.
2. **The name is policed.** The TPC allows its name only for audited, published results; a derived run must say it is a non-TPC derivation (F24). A run of two or three queries would be "a fair-use derivation of TPC-H", on data DuckDB itself says differs slightly from the specification.
3. **The data is generated by tools we would have to run** (`dbgen`, DuckDB's extension), and the comparison would be, again, between engines for questions `table` refuses.

Recorded here so that the question is not asked twice: if the maintainer reopens it, the starting point is a list of which TPC-H queries survive the feature list above, not a benchmark.

## 11. Scripts, and the gates they carry

All scripts start a process only through `import procs as subprocess` (`scripts/procs.py` locating `tests/conformance/procs.py`): own session, hard deadline, group killed in `finally` and on SIGTERM/SIGINT/SIGHUP, a watchdog for a SIGKILLed runner; `tests/conformance/test_reaper.py` already fails any file that starts a process another way, so these files are covered by an existing gate.

| file | what | gate |
|---|---|---|
| `scripts/bench_named.py` | the driver: `--suite groupby|join|1brc|clickbench|taxi|json` `--size 1e6` `--runs 5` `--contenders ...` `--out result.json`; checks disk and load first; **[corrected in NB2]** the flags are `--rows` (not `--size`), `--out` (the data directory) and `--results` (the JSON); the disk is checked before generating and the load is read and recorded at the start, not a refusal (a loaded run is flagged, Q12); **prints no time before the answer gate has passed** | G-answer: a deliberately wrong contender (a stub that drops one group) must be reported and not timed; G-refusal: a contender that refuses must be "refused", not "slow"; G-disk, G-load as above |
| `scripts/named/gen_groupby.py`, `gen_join.py`, `gen_1brc.py` | seeded generators (4.2, 5.1, 6.2); each writes `<name>.meta.json` with seed, N, K, sha256, the reference | G-repro: the same seed twice gives the same sha256; G-ref: the reference equals a second computation from the written file; both at 1e5 rows in the unit tests |
| `scripts/named/questions_*.py` | per suite: the owner's question text, the `table` flag form and `--query` form, each contender's command, and the classification (E, B, X) with the blocker and its link | G-forms: the flag form and the `--query` form give the same bytes on every question that has both; G-class: a question marked X must be refused by `table` with a tagged rule (so the table cannot go stale when a feature lands: an X that starts working fails the gate and must be re-classified) |
| `scripts/named/reference.py` | the independent reference (exact integers; `Fraction` for means) | mutants in the style of `scripts/mutlib.py`: a reference with an off-by-one or a swapped rounding mode must be caught by the 1BRC sample files |
| `tests/conformance/test_named_*.py` | the unit tests above at tiny sizes (5 to 1e5 rows), the eleven 1BRC sample pairs as fixtures, **no network and no large file** | in CI |

The suites' results are JSON files (`results/named-<suite>-<machine>-<date>.json`, small, committed with the page that cites them, as the soak's numbers are), data is never committed.

## 12. How results are published

1. A section per suite in `docs/benchmarks.html`, after the existing ones, with the **conditions next to the table** (the machine, the cores per tool, the load before and after, the tool versions, the file's sha256, the ladder step, "warm cache", "output to /dev/null", "minimum of 5, interleaved") and the list of rows for questions `table` does not answer.
2. The wording follows the existing page: "`table` was faster/slower than X by N on this question, this file, this machine"; never "faster than DuckDB". No number is copied from an owner's report. A figure that passed the gate only by tolerance says so.
3. `README.md` and `docs/index.html` say, in their "what it cannot do" sentences, **which named questions are refused** (the 11 ClickBench queries, the groupby questions 6 to 10), and `scripts/site.py` keeps the generated regions consistent.
4. Each suite's result is dated and carries the commit of `table` it ran. A result is rerun when `table`'s plan code changes (the gate of `docs/numbers.md`: a performance claim has a guard), and old results are kept, not overwritten.
5. A suite whose results are only recorded and not published (a loaded Mac run) is marked "recorded, not published" in the JSON.

## 13. Staging

| slice | what | proves | depends on |
|---|---|---|---|
| **NB0** | this document | the questions, the mapping, the blockers are written down with sources | |
| **NB1 (built, section 17)** | `scripts/named/` generators and references for **groupby** (own generator, seed 108), `scripts/named/questions_groupby.py`, the unit tests and gates G-repro, G-ref, G-forms, G-class. **No timing driver yet** | the layout and the reference are right before any clock; the `table` forms of q1 to q5 equal their `--query` forms q6 to q9 are refused as classified and q10 equals the reference up to the group ceiling | nothing |
| **NB2 (built, section 18)** | `scripts/bench_named.py` with the answer gate, the contender forms for groupby (DuckDB, csvtk, Miller, pandas), the 1e6 step on the Mac and gram, the real sizes and the real time of each step; the maintainer decides 1e7 and 1e8. **[corrected in NB2]** the 1e6 step ran on one shared VM only, recorded and not publishable; the Mac and gram steps are not run | the driver does not print a time for a wrong or refused answer (G-answer, G-refusal) | NB1 |
| **NB3 (built, section 19)** | **join**: `gen_join.py`, the `--join` forms, the pipe check; q3 only once J2 merges. **[corrected in NB3]** J2 and J3b are merged, so all five questions and the `--query` forms are built; `table --join-sorted` (J9) is an extra contender | the join answers equal the owner's `chk`; the build-side bound is reported where it refuses | NB2; J2 (#62) for q3; J1 for the rest |
| **NB4** | publish groupby (and join) sections in `docs/benchmarks.html` from measured results | claims only measured | NB2, NB3 and unloaded runs |
| **NB5** | **1BRC**: `gen_1brc.py`, the formatter, the eleven sample pairs as a gate, the Java baseline as yardstick, 1e6 to 1e8 | `table` plus the formatter reproduces the owner's `.out` files, or the report says exactly where it does not | NB2 |
| **NB6** | **ClickBench prefix**: header file from `create.sql`, the range-request prefix tool, the 16 E queries, then the 16 B | the classification (E, B, X) is confirmed or corrected by running | NB2 |
| **NB7** | **taxi-style** queries on TLC months converted to CSV (Q1, Q2, Q3 with a prepared column) | | NB2; maintainer's yes (Q8) |
| **NB8** | **JSON corpus**: simdjson/nativejson files through `jq -c` and `table`, the differential test, the expected refusals | the JSON-lines reader on third-party documents | readers R2 (built) |

Each slice is a pull request with its gates written first and failing against `main`, its mutants where it has logic, and, when it changes what the page says, the page. A slice that needs a download or a machine asks first (Q4, Q5).

## 14. Where each question would come from, if it were built (the links)

* `or`, parentheses: `docs/or-and-columns.md` (A1 open as #63; `not`, A2, not built; rename, B1, not built).
* Computed columns (a year, a difference, `+ 1`, a constant): `docs/or-and-columns.md` section 8 ("no, not now", with the conditions to reopen). Nine of the named questions wait on this decision: groupby q7, ClickBench q19, q30, q35, q36, q40 and q43, and taxi Q3 and Q4.
* Text `min` and `max`, `median`, `stddev`, per-group top-k, `corr`: **no backlog item**; `describe` (backlog item 4) is the nearest, and lists none of them. This design adds a line to `docs/backlog.md` pointing here, not an item.
* Group ceiling 1,000,000: a constant (`docs/filter.md`); raising it is a memory decision (`docs/numbers.md` 584 bytes a float sum, `docs/joins.md` section 6.3 for a join).
* Joins: `docs/joins.md` (J2 open #62: `left`, typed keys; J3: `--group`/`--agg` over a join; J3b: the `--query` join form).
* Parquet (the TLC files): backlog item 10; a headerless CSV (1BRC, ClickBench): no item (Q6).

## 15. Open questions for the maintainer, with recommendations

| # | question | recommendation |
|---|---|---|
| Q1 | Adopt **groupby q1 to q5 and join q1 to q5** first, and do NB1 and NB2 next? | **Yes**: they use only built features (q3 of join waits for J2), and they are the only named set with a published data generator and per-question check sums |
| Q2 | For the H2O suite use **our own seeded Python generator** (same layout, not the same bytes), or install R and `data.table` on gram to run the owner's script unchanged? | **Own generator first**, labelled as such. Installing R packages is a machine change the instructions for this work forbid; revisit only if a reviewer insists on the owner's bytes. The answer check does not need them |
| Q3 | If yes to installing R: on gram only, in a user library? | gram only, never on the Mac, and as a separate yes |
| Q4 | May a 12 GB 1BRC file (1e9 rows) be generated on gram (67 GB free)? | **No for now**: the ladder stops at 1e8 on both machines. Reopen once 1e8 has results |
| Q5 | May NB6 download the compressed prefix of ClickBench's `hits.csv.gz` (the size of the first 1e6 lines is to be measured first; the whole file is 16.6 GB) and the TLC months for NB7? | **Yes for a measured prefix up to a stated size** (say 2 GB compressed), asked again with the figure |
| Q6 | Add `--no-header` / `--headers a,b,c` so that headerless CSV (1BRC, ClickBench) is read without a copy that prepends a line? | **Not for the benchmark's sake.** Prepend the line and declare it. A headerless input is a real feature (logs, exports) and has its own design if it is wanted |
| Q7 | Add a **rounding mode** to `mean` (ties up) so `table` can reproduce 1BRC's expected output on its own? | **No.** The suite takes exact `sum` and `count` and rounds once, identically for every contender; the owner's rule text ("roundTowardPositive") and its sample (ties up) are not the project's half-to-even and the project's choice stands |
| Q8 | Run the taxi-style set at all (NB7)? | **Defer**: only two of four queries are expressible, the real data is Parquet, and its shapes are already covered by groupby q1 and q4 |
| Q9 | Publish the lists of refused questions (3.4) on the front page, or only in `docs/benchmarks.html`? | On `benchmarks.html`, and one sentence in the README's "cannot do" list with a link |
| Q10 | Text `min`/`max` (ClickBench q7, q22, q23) as a feature? | **Not from this work**; ask once NB6 shows what it costs the 43-query column |
| Q11 | Which cores for gram? The earlier page used 6 (a taskset); the maintainer's gram notes say to keep a soak's cores free | confirm that no soak is running when a gram run starts, and use `taskset -c 0-6`; record the cores |
| Q12 | Is the Mac fit for a published number while other work is running (load 12 to 17 when read)? | **No**: Mac runs are for development and the gate; published numbers come from a run with load under a quarter of the cores, or from gram |

## 16. What was not measured, and what is unverified

* **Nothing was timed.** No contender was run on any benchmark data; no `table` speed on any named question is claimed. **[corrected in NB2]** True of this document as a design and of NB1; NB2 timed the groupby questions once at 1e6 rows on a shared VM (section 18.3), recorded and not publishable, and still claims no speed.
* **Unverified** (not in the sources read, or not checked): which of run 1 and run 2 the db-benchmark charts plot (F10); the number of runs in Mark Litwintschik's "fastest" (F18); the public availability of his CSV dump; the row count and uncompressed size of ClickBench's `hits.csv` (F21); the distinct counts of its columns, so every B in section 8.2; the TPC-DS query count; whether the official 1e9 file's 1BRC expected output is published; whether `java CreateMeasurements.java N` runs without the Maven build; the syntax of the csvtk, Miller and pandas forms (3.6, 4.5, 5.3) **[verified for groupby in NB2, section 18; the join forms remain]**; DuckDB's `read_csv` typing options for the 1BRC form (6.3); that every one of the 413 stations is in the Basic Multilingual Plane (6.3); whether a plain scan of 1e8 rows needs `--max-rows` raised (4.4) **[still unverified after NB2: the probe exists and ran at 1e6 only, section 18.3]**; whether the build side of join q5 fits at 1e7 rows (5.2) **[still unverified after NB3: `--explain` gives 541,874,598 bytes at most for q5 at 1e6 and the refusal at the tool's default bound held 123 bytes a row, which puts the 1 GiB ceiling at about 8.7 million rows, so 1e7 is expected to be refused; computed from the 1e6 figures, not run; section 19.3]**; the compressed bytes of a ClickBench prefix (8.4).
* **Computed, not measured:** the file sizes of section 3.5 (formula in the text), the group counts of section 4.3, the 10.7-byte arithmetic in 5.2, the exact mean 25.45 and the rounding rule read from the 1BRC sample (F17).
* **Probed on five-row files with the J2 branch's build, no clock**: the flag forms in sections 4 to 8 that say "probed"; the J1/J2 forms in 5.2 are from `docs/joins.md` and the probe of `--join ... --on id1 --join-prefix s_` and `--join-type left`.
* **Open pull requests this design leans on**: #62 (J2), #63 (A1). If either changes before merge, sections 5.2 and 8.2 are re-read.

## 17. NB1: what was built, what it measured, and what this document had wrong

Built (all outside any clock; nothing here is a speed claim): `scripts/bench_named_gen.py` (the generator), `scripts/bench_named_ref.py` (the reference answers, the
layout check and the answer gate), `scripts/bench_named_questions.json` (the ten questions as commands, for the later driver) and
`tests/conformance/test_named_groupby.py` (26 tests, in CI).

**The generator.** Pure Python, the generator specified in its docstring (SplitMix64, nine draws a row, `draw mod m`), streaming in blocks of 50,000 rows, writing
`G1_<N>_<K>_0_0.csv` and `.meta.json` (seed, N, K, bytes, sha256, the label "NOT the h2oai R generator: same layout, different bytes, so published numbers are not
comparable") outside the repository. **Measured, deterministic on both machines:** the sha256 of the 100,000-row file is `ca52ff7e...7c3a1` and of the 1,000,000-row
file `16f80eb8ced039012aa9bbfdde73ecf1468e57ae28c6e40611d9736d79a73d6a`, identical on the Mac (Python 3.14.5, arm64) and on gram (3.14.4, x86-64); the 10,000,000-row file
is 509,174,428 bytes, sha256 `d0c061a103432514c1cd7e0456f764256070d311924259da0afe63ca2a94dba4`. **Real sizes, replacing the estimates of section 3.5:** 1e6 rows
49,916,813 bytes (the estimate said about 51 MB), 1e7 rows 509,174,428 bytes. Generation takes about 3 s at 1e6 and 33 s at 1e7 on the Mac, one core, loaded: recorded, not published.

**The reference.** A streaming pass over the written file, standard library only, exact integers (`v3` as millionths, means as exact rationals rounded half to even), and
a second path by SQLite; the two are equal for q1 to q5 (and q10) at 1e5 and 1e6 rows. `chk` follows `make_chk` of the owner's helpers (ints as they are, floats rounded
to 3 decimals, `;`); for the means it is the sum of the group means rounded at scale 6, which is not the owner's sum of double averages, so it is recorded and never compared
across engines.

**The gates, all passing; each can fail.** (a) the layout check holds the file to the documented header, formats, ranges and distinct counts (14 file mutants are each caught);
(b) `table`'s q1 to q5, flag form and `--query` form, equal the reference and each other byte for byte, at 1e5 rows in CI and at 1e6 and 1e7 rows by hand (`python3 scripts/bench_named_ref.py FILE --layout --answers --gate`);
mutated references (7), mutated commands (9) and a reference with truncating or ties-up rounding are each caught; (c) q6 to q9 carry a probe that must be refused with a stated rule
(`agg.bad-spec` or `query.unsupported`: a probe is the nearest spelling, so a feature that lands changes the rule and fails the gate until the entry is re-classified); (d) the class D of the questions file (a defect: the command must still trap, and the gate fails when it stops) has no entry now: q10 was its only one and is class B (item 1 below).

**What this document had wrong, found by building it:**

1. **q10 was a defect, then a bound.** Section 4.3 said the six-key grouping is "refused above 1e6 rows" with `limit.too-many-groups`. NB1 measured on `origin/main` `2f24508` built with the
   pinned compiler (`e47d647`) and on the older build of `fb4098d`: `--group` with five or more columns exits 132 (SIGILL, a trap), with no output and no tagged refusal, on any input,
   including a file of one data row, and classed the question D. **The cause** (issue 66): `agg.add_fast` chooses a place in a cache of groups from a hash of each cell's length, first and last
   byte, written `look * 31 + ...` three times a column, and `*` traps on overflow in this language; 31^14 is more than 2^63, so the fifth column overflowed for any cell (and the fourth for a
   first cell of about 370 bytes). It now wraps, and the grouping has no limit on its columns of its own. **q10 is class B again**, with the bound the first reading gave: it answers at 1e6 rows
   (1,000,000 groups; the gate passes in both forms, the reference agreeing with SQLite) and is `limit.too-many-groups` at 1.2e6, so at 1e7 and 1e8.
2. **Pandas cannot be the second path** (section 3.3): it is on the Mac only. SQLite replaces it.
3. **numpy cannot be the generator** (section 4.2): not installed on gram. The own-generator decision (Q2) stands; only its engine changed.
4. **File names and the default directory** (sections 0, 11, 13): flat files in `scripts/`, and a default under the temporary directory, as asked.
5. **Sizes** (section 3.5): 49.9 MB and 509 MB, measured.
6. **Distinct counts** (section 4.1): with draws with replacement a column of m values holds all m only with many draws a value. At K=100 every column is complete from 1e5 rows up
   except the pair `id1:id2` (q2's groups: 9,998 of 10,000 in the 1e5-row file); at K=2 or K=10 `id3` and `id6` miss some of their N/K values, as the owner's `sample` does. The layout check
   therefore requires every value when there are 20 draws a value or more, and the expected count (m times 1 - e^-(N/m)) otherwise.
7. **q3 and q5 at 1e7 rows** have 100,000 groups, exactly the default `--max-groups`; the commands pass `--max-groups 1000000` anyway, and `table` answers them at 1e7 (checked by the gate).

**Not done in NB1, by design:** no timing, no contender other than `table` and SQLite-as-reference, no driver (NB2), no join (NB3), no R, no download, no data in git.

## 18. NB2: what was built, what it measured, and what this document had wrong

Built: `scripts/bench_named.py` (the timing driver, 1,305 lines), `scripts/bench_named_mutants.py` (89 mutants of its logic, `--check` in CI) and
`tests/conformance/test_bench_named.py` (116 tests, in CI). `scripts/bench_named_ref.py`, `bench_named_gen.py` and `bench_named_questions.json` of NB1 are used unchanged. The tool is untouched, so is
its authority row. Everything that starts a process does it through `import procs as subprocess`. The CI changes are two: `bench_named` in the loop of `--check` of the mutants, and `pandas` in the line that installs
what the gates validate with (so that pandas' form is held to the reference in CI as well; csvtk and Miller are not installed there and their tests are skipped with a printed reason).

### 18.1 What the driver does

`python3 scripts/bench_named.py --rows 1e6 [--runs 5] [--questions q1,q2,q3,q4,q5] [--contenders table,duckdb,csvtk,mlr,pandas] [--threads N] [--out DIR] [--results FILE]`, one step at a time:

1. **Before it generates anything** it reads the conditions of 3.1 (a `Snapshot` of the machine: one-minute load, uptime, `docker ps`, `pgrep -af soak`, the virtual-machine witness), so that its own work is not in the load it records.
2. It writes NB1's file (or reuses it when its sha256 is the one in its metadata), holds it to NB1's layout, computes NB1's reference twice (streaming and SQLite) and refuses to go on if they differ.
3. It runs the **scan probe** (4.4): `table --agg count` and `table --select id1` over the file, recording the exit status, the rule tag if refused and the rows written, no clock. At `--rows 1e8 --accept-large --scan-probe-only` this
   is the first thing the 1e8 step reports.
4. For each question and each contender it runs the form once and judges the answer (`compare_answer`, below). A **wrong answer**, a **refusal** (`refused`, with `table`'s rule tag), a **crash** (a signal or a trap status) and a **timeout**
   (the limit, 120 s by default) are recorded as that, with the reason and **no time**. A contender that timed out is dropped from the larger steps of that question (`--drop-from RESULT.json`).
5. Only when every contender of the question has been judged: one untimed warm-up each, then N rounds in which each contender runs once and the order rotates (`a b c`, `b c a`, `c a b`, ...), output to /dev/null; then one more run each for the peak resident set and the CPU time. The
   figure is the **minimum** of N, with the median and the first run beside it and all runs kept. A contender that fails during any of these loses its times and is recorded `failed-while-timing`.
6. `q6` to `q9` are not run: their probe (the nearest spelling, from NB1) must still be refused with the stated rule, now, and the record says "not-expressible" with the blocker; a probe that is accepted or refused with another rule is a `class-mismatch` and fails the run.
7. It writes a JSON result (`schema bench-named.v1`) with the conditions, the records and the step's real sizes and wall time, and prints a line per answer (`answer  q3  duckdb@1  ok (only by tolerance ...)`) and, after the answers of the question, a line per time (`time  q3  table@1  min ... of 5 ...`).

Exit status: 0 the step ran (a contender's refusal or timeout is a result), 3 the driver refused (a rule tag: `bench.large-size`, `bench.inside-repo`, `bench.disk`, `bench.not-publishable`, `bench.conditions-missing`, `bench.result-invalid`, `bench.suite-not-built`,
`bench.args` and others), 4 a gate failed (a wrong answer, a stale classification, a file that is not NB1's layout, a reference the two paths disagree on).

**The comparison of an answer** (`compare_answer`; G-answer). The output must be UTF-8, `\n`-terminated, with a final newline, no carriage return, no NUL, no byte order mark, and a rectangle of cells. Then, by profile:
`exact` (`table`): the header is the question's columns and every cell is the reference's text. `double` (every other engine): keys are the reference's text; an integer column (`sum` of `v1`, `v2`, `count`) is the same integer in whatever spelling (`3128.00` is
`3128`, level *numeric*); a decimal sum (`sum:v3`) and a mean are the same number or within **1e-9 relative** of the *exact* value (level *tolerance*, marked "only by tolerance" in the output and the result); the header only has to have the right number of columns.
The exact value of a mean is the rational of a second pass over the file (`exact_means`), which must round to NB1's reference text or the run stops. Rows are a set (3.3; an ordered comparison is there for the later suites and is tested). An integer is never taken by tolerance.

### 18.2 The readings NB2 had to choose (the design is silent or ambiguous)

1. **Loaded** is: the one-minute load at the **start** exceeds a quarter of the machine's logical CPUs (`os.cpu_count()`, not the CPUs the process may use), strictly (equal is not loaded). The load after the run is recorded, not judged (the run's own work is in it).
2. **Shared VM** is any guest of a hypervisor: the `hypervisor` flag of `/proc/cpuinfo` (Linux x86), `sysctl kern.hv_vmm_present` (macOS), `systemd-detect-virt` (otherwise). A machine that cannot be told is treated as shared. There is **no override flag**: a dedicated machine is one where the witness says so.
3. **Soak and containers** (Q11): `pgrep -af soak` finding a process (the driver's own line and `pgrep` excluded), or `docker ps` listing a container or failing while docker is installed, is a reason. No docker installed is not.
4. **Publishable** is false, with the reasons listed, when any of: loaded, shared VM (or unknown), soak, container (or docker unreadable), **fewer than 5 runs**, or the conditions of 3.1 are incomplete. The writer of a published table (`--write-table`, `write_published_table`) recomputes this from the recorded conditions and refuses
   (`bench.not-publishable`, `bench.conditions-missing`, `bench.result-invalid`) whatever the file's own flag says; it also refuses a result in which a contender that did not pass carries a time.
5. **Pins** (3.6) are recorded with the version read (`pin`, `pinned`, a note); a version that differs from the pin does not by itself make a run unpublishable (the numbers are of the version read).
6. **The answer** for the gate is read from a pipe at the untimed run, not written to a file (3.2 said a file).
7. **Peak resident set and CPU time** come from `wait4` through a small launcher run with `python -S -E`, because `/usr/bin/time` is not on the VM; a launcher's own resident set is a floor under every figure, so the floor (the reading for `true`) is recorded in the result as `rss_floor_kb`.
8. **Forms.** table: the flag form of NB1 with `--threads N`, timed; the `--query` form is run once per question and must give the same bytes (`forms_equal`). DuckDB: the owner's SQL over `read_csv('FILE')`, `COPY (...) TO '/dev/null' (FORMAT csv)`, `SET threads=1` or its default; the **CLI when there is one, else the Python module**
   (the same statements; the record says which). csvtk: `csvtk -j 1 summary -w 12 -g KEYS -f col:op,...` (`-w 12` because the default is 2 decimals). Miller: `mlr --icsv --ocsv stats1 -a OPS -f COLS -g KEYS [then cut -o -f ...]` with `GOMAXPROCS=1` for "1 thread" (`stats1` applies each accumulator to each field, so the
   extra ones are computed and cut). pandas: `pd.read_csv(FILE).groupby(KEYS, as_index=False, sort=False).agg(...).to_csv(...)` as a process (the import is in the time). Threads: table at 1 and at `--threads` (default the usable CPUs), DuckDB at 1 and default, csvtk 1, Miller 1, pandas default.
9. **q6 to q9** are probes of `table` only. The other contenders are not asked them: there is no reference answer for a median, a difference of aggregates, a per-group top-two or a correlation, and NB1 did not build one. **q10** is opt-in (`--questions ...,q10`): its reference holds a group per row.
10. **1e7 and 1e8** need `--accept-large`; the estimate (bytes, the free space needed, the process launches) is printed first, and without the flag the driver refuses with `bench.large-size` before touching the disk. The estimate has **no wall time**: nothing measured would support one. `--estimate` prints it and stops.
11. `--skip QID:CONTENDER[=REASON]` records a contender as not-run with the operator's reason, and `--taskset LIST|gram` runs every contender under `taskset -c` (`gram`: cores 0-6 if no soak and no container, else 0-5, Q11), recording the cores. Both exist for the maintainer's runs and are tested; `--taskset` was not run here.
12. Only the **groupby** suite is built; `--suite join` and the others are refused with `bench.suite-not-built`.
13. **NB1's `gate()` is not called.** It treats a refusal of `table` as a problem, and a refusal (q10 above the group ceiling) is a result here; the driver does its two checks itself: the `--query` form equal to the flag form in bytes (`forms_equal`) and the probe of each not-expressible question (`probe_not_expressible`). Its reference (`reference`, `reference_sqlite`, `validate_layout`) is used unchanged.
14. A **result's data directory** is `--out` (default as NB1's); a path inside the repository is refused (`bench.inside-repo`), a disk with less than three times the file's estimated size free is refused before generating (`bench.disk`).

### 18.3 What was measured, with its conditions

All of it on **one machine, a shared cloud VM**, so the run is **recorded and not published** (`publishable: false`, reasons below), and **no speed of any contender is claimed or compared here**: the times in the result file are there to prove that the driver records them, with their
conditions, only for answers that passed.

| condition | value (from the result file `named-groupby-vm-2026-10-20-1e6.json`, kept outside the repository) |
|---|---|
| machine | `vm`, Linux x86-64, Intel(R) Xeon(R) Processor @ 2.10GHz, 4 logical CPUs (4 usable), 16,874,930,176 bytes of RAM, Python 3.13.16; the `hypervisor` flag is set |
| load before / after the step (1, 5, 15 min) | 1.60, 1.58, 0.94 / 1.38, 1.50, 1.21; the line for "loaded" is 1.00. The four mutant shards of section 18.4 had ended minutes before, and during the step the same session ran light checks (the mutants' `--check`, `cancho fmt --check`, the manifest, schema and site checks, two unit tests), which the load figures include and nothing else records |
| uptime at the start | `12:43:28 up  5:48,  0 user,  load average: 1.60, 1.58, 0.94` |
| docker / soak | `docker ps` failed (the daemon is not reachable); `pgrep -af soak` found nothing |
| `publishable` | **false**: loaded (1.60 over 1.00); shared virtual machine; docker unreadable so no soak container can be ruled out. (The five runs were made, so that is not a reason.) |
| file | `G1_1e6_1e2_0_0.csv`, 49,916,813 bytes, sha256 `16f80eb8ced039012aa9bbfdde73ecf1468e57ae28c6e40611d9736d79a73d6a`, seed 108, K 100, data directory on the VM's disk, warm page cache, output to /dev/null |
| statistic | minimum of 5 after one untimed warm-up, interleaved, the order rotating; median and first run kept; `table` at 1 and 4 threads, DuckDB at 1 and its default, csvtk `-j 1`, Miller `GOMAXPROCS=1`, pandas as it is |
| contenders | `table` 0.3.0 (compiler `e47d647`, built from the `tools/` of `origin/main` `beab759`, which NB2 does not touch); DuckDB CLI v1.5.6 (`069cc9f9b5`); csvtk v0.38.0; Miller 6.22.0; pandas **3.0.5** (the design pins 3.0.3: recorded as `pinned: false`). The three binaries are the projects' release downloads (`duckdb_cli-linux-amd64.zip` v1.5.6, `csvtk_linux_amd64.tar.gz` v0.38.0, `miller-6.22.0-linux-amd64.tar.gz`), extracted binaries' sha256 `61238cfb...`, `3904d683...`, `9044e7e9...`, outside the repository |
| launcher floor under every peak RSS | 7,316 KiB |

What the step showed (the numbers are counts and bytes, not speeds):

* **The file.** The generator's sha256 of the 1,000,000-row file is **the digest NB1 recorded on the Mac and on gram**, now also on a third machine (x86-64 Linux, Python 3.13.16): G-repro holds across three. Real size **49,916,813 bytes**; the driver's estimate (the formula of `bench_named_gen.estimate_bytes`) is 53,600,001, 7.4% over, so
  the estimate printed before a 1e7 or 1e8 step is on the safe side by about that much (section 3.5 had "about 51 MB", NB1 corrected it to 49.9 MB; the estimate is the formula, not the design's sentence).
* **The wall time of the step**, which the design asks NB2 to print: **599.4 s** for the whole step of 6 questions x 7 contenders (336 launches planned, 41 pairs run, see below), of which 6.3 s generating the file, 32.5 s for the layout check, the streaming reference and the SQLite cross-check, and 560.0 s for the answers, warm-ups, rounds and measured runs. The 1e7 and 1e8 steps are not extrapolated from it (this VM is shared, and the contenders do not scale alike).
* **The gates.** Of the 42 (question, contender) pairs asked, 41 ran and **all 41 answers passed the gate**; the 42nd is csvtk on q10, skipped by the operator (below). By the level at which the answers matched the reference: **20 exact** (`table` at both thread counts on all six questions; DuckDB, Miller and pandas on q1 and q2), **6 value-equal** in another spelling (csvtk's `3128.00` for `3128` on q1 and q2;
  DuckDB, Miller and pandas on q10, where `sum:v3` is printed `5.5` for `5.500000`) and **15 only by tolerance** (the five other contenders on q3, q4 and q5: a double against the exact decimal or mean, within 1e-9 relative). No answer was wrong, refused, crashed or timed out at this size. `table`'s `--query` form gave the bytes of its flag form on all 12 of its records (`forms_equal`).
* **q6 to q9** were probed on the same file and each was refused with the rule NB1 classified (`agg.bad-spec` for q6, q8, q9; `query.unsupported` for q7): the four records are `not-expressible`, with their blockers.
* **The scan probe (4.4)** at 1,000,000 rows: `table --agg count` exits 0 and answers 1000000; `table --select id1` exits 0 and writes 1,000,000 rows (6,000,004 bytes). That says nothing about 1e8 rows (a million rows is far under the default `--max-rows` of 10,000,000): the question stays open until the maintainer runs the 1e8 probe, which the driver can now do alone (`--rows 1e8 --accept-large --scan-probe-only`).
* **csvtk on q10.** In a trial at 1e5 rows (not in the result file) `csvtk summary` of the six keys with `v3:sum,v3:count` was **killed by the kernel's OOM killer** at 13,771,076 kB resident on this 16 GB VM (the kernel log says `Memory cgroup out of memory`); the driver recorded it as `crashed: signal 9` and no time. It was not repeated at 1e6: the step ran with `--skip q10:csvtk=...`, which records the operator's reason. A refusal is a result (3.4):
  csvtk's six-key `summary` is memory-bound far below the group ceiling of `table`, on this data.
* **The tests that start processes** (the classes `Program` and `Forms`) ran here with all five contenders installed (csvtk, Miller and DuckDB's CLI from `/home/user/named-tools`) and, without that directory on PATH (CI's situation), with the Python module of DuckDB, pandas and `table` only, csvtk and Miller skipped with their reason printed.


### 18.4 Mutants of the driver

`scripts/bench_named_mutants.py` has **89 mutants** of the logic that decides whether a number is recorded and published, none of them touching the tool: the load line and the divisor, the cores counted, every reason of `publishable` (loaded, virtual machine, unknown machine, docker, container, soak, the minimum of N and its boundary), the completeness of the conditions, the
writer of a published table (the flag believed, the conditions not required, a time on a failed contender, the minimum that is not the minimum), the launch, disk and opt-in arithmetic and their boundaries, the figure (maximum, last run as first, mean as median), every refusal of the parsing of an answer (final newline, carriage return, NUL, byte order mark, header spaces, the header of `table`, the number of columns, more rows,
the order), the tolerance (its size, relative or absolute, integers, keys, a decimal sum, the level kept, an unknown exact mean), the classification of a launch (timeout first, signal, trap status, `table`'s rule tag), the order of the work (a wrong answer timed, forms of `table` that may differ, a timeout not dropped, a dropped pair or a skip launched, N+1 rounds, no rotation, no warm-up, a failure while timing kept, a failed measured run kept), the probes of the classification, the machine probes
(the hypervisor flag, a word containing it, the Mac witness, pgrep finding the driver, docker's list, the clock, the output sink, a missing program, the snapshot), and six program-level rules (opt-in, inside the repository, a reused file without its sha256, disk, an unbuilt suite, the scan probe's row count). Unlike the mutants of the tool, it edits a copy of `scripts/` and `tests/conformance/test_bench_named.py` in a temporary
directory, so shards run at once and the working tree is never touched; the unit classes (86 tests, a few seconds) kill all but the six program-level ones, which name the test that must.

**Run for real on this machine** (`MUT_SHARD=i/4`, four shards on the four cores, `TOOLBOX_BIN` the built `table`): the first full list had **92 mutants: 85 killed, 7 survived**. The seven: *a number may have a huge exponent* (the bound 40 raised to 4000) is **equivalent** (it only bounds the cost of `Fraction(Decimal('1e999999999'))`; both bounds refuse the cell, and every reference value is a text of at most 12
digits) and was removed with its proof in the script; *an integer may be off by a tolerance*, *a blank line is fine* and *the times of a contender that failed stay* were mutants of **dead code** (the integer guard was shadowed by `truth is None`; the explicit blank-line refusal by the rectangle check; the lines that removed times removed nothing, as they are set after every run), so the code was removed and the first re-pointed to give integers a truth; *a decimal sum is compared with a
mean's exact value*, *a NUL byte is fine* (in the header a double engine prints) and *a probe that works is a confirmed classification* (exit 0 with the right rule) **had no test**; a test was added for each (`test_a_decimal_sum_is_held_to_its_exact_value_by_tolerance`, `test_a_nul_byte_in_a_header_a_double_engine_prints_is_refused`, `test_an_exit_zero_is_never_a_refusal_even_with_the_right_rule`). The list is 89 and the second full run killed **all 89** (shards of 23, 22, 22 and 22; about three minutes a shard).


### 18.5 What this document had wrong, found by building NB2

**[corrected in NB3]** One more, found by NB3: a refusal of `table` in a csv answer is one line on **standard error** (`table: RULE: message`), not a JSON line on standard output, so NB2's `classify` read no rule tag from it (it would have recorded `refused: no rule tag` for q10 above the group ceiling, a case no NB2 run met). It now reads the tag from standard error (`rule_of_stderr`), with a test and two mutants.

1. **Peak resident set** (3.2): `/usr/bin/time` is not on every machine (it is not on this VM); the driver measures with `wait4` through a launcher of its own and records the launcher's floor (7,316 KiB here), so a figure near the floor reads "at most the floor". CPU time comes from the same call.
2. **The answer file** (3.2): the answer is read from a pipe at the untimed run.
3. **Thread counts** (3.2): Miller 6 has no thread flag; "1" is `GOMAXPROCS=1`. pandas has none and is recorded "default".
4. **Unverified syntax of csvtk, Miller and pandas** (3.6, 4.5): verified for the groupby questions and corrected: csvtk prints to **2 decimals by default**, so `-w 12`; Miller's `stats1` needs `then cut -o -f ...` when the accumulators differ by field; both forms equal the reference at 20,000 rows for q1 to q5 and q10. The join forms (5.3) stay unverified.
5. **"a slow contender" (3.5)** assumed csvtk and Miller fail by minutes; on q10 csvtk fails by **memory** (killed at 13.8 GB resident at 1e5 rows). The skip with a reason (`--skip`) is what the driver offers, and the OOM is a recorded result.
6. **The step on "the Mac and gram"** (13, NB2): this environment is neither; the step ran on one shared VM and is not publishable. The Mac and gram steps need the maintainer's machines and are not run.
7. **The scripts' flags** (11): `--rows`, `--out`, `--results`; the load is recorded and flagged, never a refusal.
8. **Section 16**: "Nothing was timed" was true of NB0 and NB1; NB2 timed the groupby questions at 1e6 on one shared VM, publishes nothing, and claims no speed. "The syntax of the csvtk, Miller and pandas forms" is verified for groupby (4 above); "whether a plain scan of 1e8 rows needs --max-rows raised" stays unverified (the 1e6 probe says nothing about 1e8).


### 18.6 Not run

* **Mac and gram** steps (the driver supports them: `--threads`, `--taskset gram`, `--path-class`; `--taskset` itself was not exercised on a real run here). Q11's soak confirmation on gram and the Mac's `kern.hv_vmm_present` witness were not run (the latter is covered by a fake in the tests).
* **1e7 and 1e8** (explicit opt-in, the maintainer decides) and therefore **the 1e8 scan probe** (4.4): not run; the driver can report it alone.
* **csvtk on q10 at 1e6** (skipped with its recorded reason after the OOM at 1e5); **q10 at 1e7 and above** (a refusal of `table` by the group ceiling, and a reference of a group a row).
* **Joins** (NB3), 1BRC (NB5), ClickBench (NB6), the taxi set (NB7), the JSON corpus (NB8).
* **`scripts/manifest.py --check --against ../cancho-tools/contract`**: no checkout of cancho-tools at the pinned revision here; `tools/`, `tools.toml` and the manifests are untouched by NB2, so the step has nothing new to check.
* **Published tables:** nothing is published (NB4); `--write-table` was exercised only on fabricated conditions in the tests and on this VM's run, where it refuses.

## 19. NB3: what was built, what it measured, and what this document had wrong

Built: `scripts/bench_named_join_gen.py` (the generator, 258 lines), `scripts/bench_named_join_ref.py` (the reference and the checks, 460), `scripts/bench_named_join_questions.json` (the five questions as commands),
`scripts/bench_named_join.py` (the join suite of the driver, 481), `scripts/bench_named_join_mutants.py` (61 mutants, `--check` in CI), and the tests `tests/conformance/test_named_join.py` (44 tests) and
`test_bench_named_join.py` (45 tests). `scripts/bench_named.py` of NB2 gained `--suite join` (a branch that hands the step to the new module), `time_live` (the timing of NB2's `run_question` moved into a function both suites call, with the
same text), `rule_of_stderr` (below) and `--refusal-bound`. Nothing else of NB1 or NB2 changed; the tool, `tools.toml` and the manifests are untouched. Every process is started through `import procs as subprocess`. No data is in git.
The first commit of the pull request is the tests alone (`135a9ac`, pushed alone): against `main`'s binary the two new files, `test_mutants_apply` and the new `classify` test run 89 tests and **85 do not pass**, each because a script of NB3 does not exist (one because
`main`'s `classify` reads no rule tag from standard error); the 4 that pass are the two controls of `test_mutants_apply` and two older `classify` tests. (The generator, the reference and the driver existed as a working prototype
when the tests were committed; what makes the gates honest is that each can fail: the 61 mutants below.)

### 19.1 What the suite does

`python3 scripts/bench_named.py --suite join --rows 1e6 [--runs 5] [--questions q1,..,q5] [--contenders table,table-sorted,duckdb,csvtk,mlr,pandas] [--refusal-bound BYTES] ...` is NB2's step with these differences:

1. **The files.** Four tables from the SplitMix64 stream of NB1 (`bench_named_join_gen.py`; its docstring is the specification): x (`id1 id2 id3 id4 id5 id6 v1`), small, medium, big, named as the owner names them (`J1_1e7_NA_0_0.csv`, `J1_1e7_1e1_0_0.csv`, ...), with `<x>.meta.json` holding the seed, the sizes and the sha256 of every table.
   The key sets follow F9: for n keys, 1.1 n integers are shuffled, the left set is the first n and the right set the n from index n/10, so 90 percent are common, n/10 left-only and n/10 right-only; a right table has each key once; x draws each key column from its left set with every key at least
   once (`sample_all`). A set already in the data directory is reused only if the sha256 of all four tables is the one in its metadata.
2. **The reference** (`bench_named_join_ref.py`): for each question a **fingerprint of the set of joined rows**: the row count, `sum(v1)` and `sum(v2)` in integer millionths (the owner's `chk`, exact), the number of matched rows, and a **row digest**, the sum modulo 2^64 of a mixing function of (id1, id2, id3, v1, v2, matched).
   The digest ties each v2 to the left row it was joined to, which the sums cannot (a gate swaps the v2 of two rows: the totals are unchanged and the fingerprint is not). Two paths share only the digest function and the rounding to millionths: a streaming pass over the written files with the right table in a dict, and SQLite (the join in SQL, the digest over the rows it returns); they must be equal before the files are used. The layout of the files is checked first.
3. **The answer gate.** The contender's joined rows are written to a **file** (they are wide), and read in one pass (`check_output`): the canonical number of cells, id1 to id3 whole numbers, id4 to id6 equal to "id" + id1 to id3, the right table's own copies of its ids consistent with its key, v1 and v2 numbers of the layout in whatever spelling
   (`5.5`, `5.500000`, `5.5e0`) but exact, a v2 empty only on an unmatched row of the left join, then the fingerprint against the reference. A contender whose rows fail is `wrong-answer` with the first difference and **no time**; a refusal, a crash and a timeout are what NB2 records, with the rule tag of `table` now read from standard error too. Everything after the answers (warm-up,
   rounds, the measured run, the conditions, `publishable`, the writer of a published table) is NB2's, unchanged.
4. **The forms** (`table` is asked for the same canonical row as the others): `table --join R --on KEY --join-prefix R_ [--join-type left] --max-join-rows 1000000000 --max-rows 1000000000 [--max-state-bytes 1073741824 for q5] --format csv X` (the design's flags); the `--query` form; the probe on standard input; and `table-sorted`, the same call with `--join-sorted` over copies of the two tables sorted bytewise by the key text (made by the driver
   in memory). DuckDB: `COPY (SELECT x.*, r.<non-key columns> AS R_<...> FROM read_csv(x) x [LEFT] JOIN read_csv(R) r ON x.k = r.k) TO ... (FORMAT csv)`, `SET threads=1` or its default; csvtk: `csvtk -j 1 join -f KEY [-L] x R`; Miller: `mlr --icsv --ocsv join -j KEY --lp R_ [--ur] -f R [then unsparsify -f ...] then cut -o -f ... x` with `GOMAXPROCS=1`;
   pandas: `x.merge(r, on=KEY, how=inner|left, suffixes=('', '_r'))` as a process. Which of them was run is in the result (`not_run` lists what is missing, with the reason).
5. **The second forms.** For `table`, after its answer passes: the `--query` form must pass the same fingerprint, and the call with the probe on standard input must give **the same bytes** as the call with the probe as a file; for `table-sorted`, the call without `--join-sorted` over the same sorted files must give the same bytes as the merge. A failure is `wrong-answer`, and `forms_equal` is recorded.
6. **The refusal report** (`refusal_report`, in the result as `refusal_report` and printed as `report  q5 ...`): each question's flag form with the tool's own bounds (or `--refusal-bound BYTES`) and the JSON answer: the exit status, and if it is refused the rule, the facts (`holder`, `limit`, `held`, `rows_held`, `build_row`, `build_file_bytes`) and the repair (`kind`, `reason` or the option); and `--explain`'s `build_state` (`accounted_at_most`, `budget`, `may_pass_bound`, `swap`). It runs nothing but the call and its explain and
   times nothing; it answers "what does `table` say here".

### 19.2 The readings NB3 had to choose (the design is silent or ambiguous)

1. **Flat files** `scripts/bench_named_join_*.py` (section 0 says `scripts/named/`; NB1 corrected that for the groupby).
2. **Sizes.** The owner's n are N/1e6, N/1e3 and N; here `max(N/1e6, 10)` and `max(N/1e3, 10)` and N, equal to the owner's at 1e7 and 1e8 (10 and 10,000 at 1e7; 100 and 100,000 at 1e8) and different at the development size 1e6 (10 for small instead of 1; medium is 1,000 either way), because a small table of one key has nothing to miss. Below N = 2e4 the small and the medium table both have 10 rows and the owner's names would be one file, so the medium's gets `_medium`. N below 100 is refused.
3. **v1 and v2** are `draw mod 10^8` millionths, written with the trailing zeros dropped, exactly as NB1's v3 (F9 says `round(runif, 6)` and does not give the range).
4. **id4, id5, id6** are "id" + id1, id2, id3 (F9 says "`id%.0f` of the integer" without saying which); in a right table, "id" + the right table's id1, id2, id3.
5. **The right tables' other key columns** (medium's id1; big's id1 and id2) draw uniformly from the right key set of the small (and medium) table, so a joined row's right-side ids are keys that exist.
6. **The check is of the rows, not of a sum.** J3 is merged, so `table --join ... --agg ...` could give the owner's `chk`; the check reads the joined rows themselves because a sum cannot tell a wrong pairing from a right one. The owner's `chk` (count; sum v1; sum v2) is kept, exact, as `answer.chk` and in `reference`.
7. **The canonical row** is x's seven cells, then the right table's other cells in its order (v2 last); `table`'s key is written once (the shared-name `--on`), as the others' forms are made to write it. Only `table`'s header is held exact (x's names, then `R_` + the right table's); the others' names are their own and only their width is held. An engine that gives a column NULLs writes its integers as floats (pandas, `5.0`, in a left join): the right table's id cells may be so spelled; every other cell must be exact.
8. **Typed keys** are not used: the design's forms join on the text of the key (ids have no leading zeros, so text and integer equality are the same relation). A `--on id2:int` form is not added.
9. **Threads.** `--threads` is accepted and the join is read on one core with the same bytes (`docs/joins.md` section 7): `table` is one contender, `table@1`, not two. DuckDB at 1 and its default, csvtk 1, Miller `GOMAXPROCS=1`, pandas as it is.
10. **The `--query` forms** select the canonical columns by name (`select a.id1, ..., a.v1, b.id4, b.v2 from ...`): `select *` would write the build side's key too and prefix its columns `b.`. Their headers differ from the flag form's (`b.id4`), so they are held to the fingerprint and not to the flag form's bytes.
11. **`table-sorted`** exists only where the output is the same rows: it is gated by the fingerprint (the reference is a set) and by the bytes of the hash join over the same sorted copies. The sorted copies are made in memory by the driver (a development-size tool: they are an extra pass over the files that is not timed).
12. **The other engines.** SQLite is not a contender (section 3.6 does not list it; it is the reference's second path). DuckDB uses the CLI when there is one and the Python module otherwise (the same statements); csvtk `join` loads its files in memory and Miller's `join -f` holds the right table (unsorted mode); both ran at 1e6 here.
13. **`--external`** for the join suite is `PROGRAM QID X RIGHT KEY KIND`, printing the canonical rows (for tests and for a tool the maintainer adds).
14. **The refusal report is of the flag form with the tool's own bounds**, in the JSON answer: a csv answer writes the refusal as a line on standard error without its facts. `--refusal-bound` shows a refusal where the default bound does not refuse.
15. **Memory of the driver:** the layout check holds every column of the four tables as lists of strings; at 1e6 that is a few hundred megabytes, at 1e7 several gigabytes. The maintainer who runs 1e7 and 1e8 decides whether it is run there (it is part of the step, not optional); the estimate printed first covers the files, not this.

### 19.3 What was measured, with its conditions

All of it on **one machine, the shared cloud VM of section 18.3** (Linux x86-64, Intel Xeon 2.1 GHz, 4 logical CPUs, 15.7 GiB, Python 3.13.16, the `hypervisor` flag set, a docker daemon that cannot be reached), so the step is **recorded and not published**: `publishable: false` for the reasons *shared virtual machine* and *docker unreadable*
(the load at the start, 0.80, was under the line of 1.00 and is not a reason this time); **no speed of any contender is claimed or compared**. Contenders: `table` 0.3.0 (compiler `e47d647`, built from the `tools/` of `origin/main` `9f425d8`), DuckDB CLI v1.5.6, csvtk v0.38.0, Miller 6.22.0 (the release binaries of section 18, still in place: nothing was downloaded again), pandas 3.0.5 (the design pins 3.0.3).
Minimum of 5 after one untimed warm-up, interleaved, warm cache, output to /dev/null. The result is `named-join-vm-2026-10-21-1e6.json`, kept outside the repository.

* **The files at 1e6** (seed 108): x 42,151,482 bytes, small 174, medium 26,192, big 42,145,824, **84,323,672 bytes in all**; the driver's estimate (arithmetic from the widths of the cells) is 95,830,080, 13.6 percent over. Generated in 7.2 s; the layout check, the streaming reference and the SQLite cross-check took 49.3 s (this is Python on a shared VM, not a claim about any contender). The sha256 of x is `2d18aaf2bcc10b80648520614e5aecc3668ab4ac947ae10d00ae66218fe09567`.
  The tables have 10, 1,000 and 1,000,000 right rows (small 10, medium 1,000, big 1e6), as 19.2 reading 2 says.
* **The reference** (rows; owner's chk `count;sum v1;sum v2`): q1 900,212 rows, `900212;44979542.143897;62359074.632354`; q2 and q4 899,767 rows, `899767;44964046.557756;44821932.055158` (the same answer: id5 is "id" + id2, so the join on the text key is the join on the integer); q3 1,000,000 rows of which 899,767 matched, `1000000;49975581.664645;44821932.055158`; q5 (the big table has each id3 once, 90 percent of them in x) 900,000 rows, `900000;44992493.212420;44981670.034073`. The streaming pass and SQLite give the same fingerprint for all five.
* **The gates.** Of the 35 (question, contender) pairs, **all 35 answers passed the gate at the exact level** (the fingerprint, equal): `table`, `table-sorted`, DuckDB at 1 thread and at its default, csvtk, Miller and pandas on q1 to q5. No answer was wrong, refused, crashed or timed out at this size; csvtk did not run out of memory on any join (it did on the groupby q10 of section 18.3). The ten `table` records (`table@1` and `table-sorted@1`
  on five questions) have `forms_equal: true`: the `--query` form gave the same fingerprint, the probe on standard input gave the same bytes as the probe as a file, and the merge gave the bytes of the hash join over the same sorted files. The wall time of the step was 1,119.0 s (1,062.0 s for the answers, rounds and reports). Peak resident set and CPU time are in the result file with the launcher's floor of 7,364 KiB.
* **What `table` says where it refuses, without a clock** (the refusal report at the tool's own bounds; the design's flags raise `--max-state-bytes` to the ceiling for q5):
  * q1, q2, q3, q4 answer at the default bound: `--explain` says the build holds at most 4,506 (q1), 450,206 (q2, q3, q4) accounted bytes of the 67,108,864 allowed.
  * **q5 is refused at the default bound**: `limit.state-too-large`, exit 8, `holder` `join-build`, `limit` 67,108,864, `held` 67,108,783 after `rows_held` 544,997 rows, refused at build row 544,998 (line 544,999) of the 42,145,824-byte file. `--explain` says at most 541,874,598 bytes (12.86 times the file) and `may_pass_bound` true. **The repair is `none`**: "the probe file is not smaller on disk than the `--join` file" (x is 5,658 bytes larger than big; the design's `--join-prefix` would also forbid the exchange, J7 reading 5, and so does the left join of q3: "only an inner join gives the same rows either way"). So at this size the only way to the answer is a larger `--max-state-bytes`, which is what the design's form does (1 GiB), and it answers (35 of 35 above).
  * **Where the ceiling is** (computed from the figures above, **not run**): the refusal held 123.1 bytes a row, so the 1 GiB ceiling of `--max-state-bytes` is at about 8.7 million rows of the big table; **q5 at 1e7 is expected to be refused at the ceiling** (and at 1e8 by an order of magnitude), with the repair none for the reasons above. `--explain`'s upper bound (12.86 accounted bytes per byte of the file, so 1 GiB at a file of 83.5 MB) would put it at about 2 million rows; it is an upper bound, and the measured 123 bytes a row is the tighter figure (8.7 million). Section 5.2's arithmetic (10.7 bytes a row) was far too small; the number to expect is of the order of 100.
  The maintainer's 1e7 run will show which.

### 19.4 Mutants

`scripts/bench_named_join_mutants.py` has **61 mutants**: the generator (the floors of the sizes, a fifth of the keys common, right keys equal to the left, no left-only keys, `sample_all` without its keys or its shuffle, the shuffle, the names of two tables of ten rows, the order of a sorted copy and its column, the disk rule and its factor, the estimate), the reference (the digest made linear, without the match, the sums of the wrong column, a left join that drops its unmatched rows, the range and the decimals of a v, spaces around a v, each field of the comparison), the check of the rows (no final newline,
a header's width, table's exact header, the id copies, the right table's copies, an unmatched row with right cells, the float spelling), the judge and the order of the work (a joined answer not compared, table's header not exact, a timeout not dropped, a dropped pair or a skip launched, the second forms not asked, the merge allowed other bytes, the pipe unchecked, the `--query` form unfingerprinted, the clock, stderr unclipped), the forms (DuckDB's left join and its right key, csvtk's `-L`, Miller's `--ur`, the merge flag, the sorted column), and the program's rules (the opt-in, the repository, the sha256 of a reused set, the disk, the question list, the refusal report and its bound). It edits a copy in a temporary directory, so shards run at once.
**Run for real** (`MUT_SHARD=i/4`, four shards, `TOOLBOX_BIN` the built `table`, DuckDB, csvtk and Miller on PATH): the first list of **64 had 7 survivors** (57 killed): the *right table's key unique* check of the layout and the *empty v2 in an inner join* refusal are **equivalent** (the first is implied by the row count checked just above it; the second makes the fingerprint refuse the row instead of the check, same verdict: both in the docstring and removed);
*the merge may give other bytes*, *the pipe is not checked* and *the `--query` form is not fingerprinted* had **no test that could see them** (the real `table` never differs), so the second-form logic got its own tests with a fake whose second forms can differ (`SecondForms`), and the pipe was made a launch the driver's context can intercept; *a float spelling is taken for any id* got a test; *DuckDB's right key* was a string test in a class the unit run did not include. The second run had **5 survivors of 62**: the same three (the new tests were not in the list of classes it ran), *DuckDB's left join* (its test is in `Forms`) and *the layout need not have a fifth of the keys on each side* (**equivalent**: the left and right sets have n distinct values each, checked just above, and the common count is checked, so the differences are n/10 by arithmetic; removed with its proof in the script). The list is 61 and the third run killed **all 61** (shards of 16, 15, 15 and 15). NB2's 91 mutants were run again after the changes to `bench_named.py` (23, 23, 23 and 22): **all killed**.

### 19.5 What this document had wrong, found by building NB3

1. **Section 5.1** named `scripts/named/gen_join.py`; the files are flat (reading 1), and the owner's n do not give a small table at 1e6 (reading 2). **Section 5.2**: q3 no longer needs an open J2; `--agg` and `--group` with `--join` work (J3); the join has a `--query` form (J3b); the check does not use them (reading 6). **5.3**: csvtk and Miller forms are validated, and csvtk did not run out of memory at 1e6.
2. **The q5 arithmetic** (5.2, "1e8 x 10.7 = 1.07e9"): the accounted bytes of a held row are about **123 bytes** (measured at the refusal), not 10.7; the ceiling is at about 8.7 million rows, not 100 million, so q5 is expected to be refused at 1e7 as well as 1e8, not only at 1e8 (19.3). Still not run there.
3. **A refusal in a csv answer is on standard error** (NB2's `classify` read only standard output): corrected in NB2's code and section 18.5.
4. **The swap repair** (J7, `docs/joins.md` 22) is never the way out of q5 here: the two big files are within a few kilobytes of each other, the design's `--join-prefix` forbids it, and q3 is a left join.
5. **Names** (5.1): below N = 2e4 the owner's naming gives the small and the medium table one name.

### 19.6 Not run

* **The Mac and gram steps.** `--taskset gram`, `--threads` and `--path-class` support them; nothing was run on either.
* **1e7 and 1e8** (the maintainer decides; `--accept-large` after `--estimate`), and so q5 at those sizes (19.3), the layout check's memory there (reading 15), and csvtk's and Miller's behaviour on tables of that size.
* **A published table**: nothing is published (NB4); `--write-table` refuses this run.
* **SQLite as a timed contender** (not in section 3.6).
* **The 1BRC, ClickBench, taxi and JSON suites** (NB5 to NB8).
* **`scripts/manifest.py --check --against ../cancho-tools/contract`**: no checkout of cancho-tools here; the tool and the manifests are untouched.
