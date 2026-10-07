Throwaway spikes behind `docs/numbers.md` (a design; nothing here is part of the tool).

| file | what it measures or checks |
|---|---|
| `superacc.cho`, `superacc_check.py`, `rep_check.py`, `timeit.py` | the exact accumulator for doubles: correctness against Python integer arithmetic and `math.fsum` (sum, merge in other orders, mean), the carry threshold, per-value cost against plain f64 and the checked i64 sum |
| `numparse.cho`, `numparse_check.py`, `timecells.py` | reading a decimal cell and a float cell (Clinger's fast path inline, `std.json` as the exact fallback): differential against `numbers_ref.py`, cost per cell against `parse_int` |
| `numbers_ref.py` | the reference semantics of the design for one cell and for the aggregates, and the table of edge cells (`--compare` prints what DuckDB, csvtk, pandas and Python make of the same cells) |
| `bench_engines.py` | the same questions on a numeric column through `table`, DuckDB (DECIMAL and DOUBLE), csvtk and pandas, answers checked before timing |

Build a `.cho` with the Mac compiler: `cancho build FILE.cho --std -o OUT --ignore-compiler-rev`.
