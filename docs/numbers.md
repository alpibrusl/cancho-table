# `table` and numbers: exact decimals and floats (design)

**Status: design, accepted by the maintainer (the six open questions are answered, section 11); built stage by stage.** It decides what `:dec(S)` and `:float` mean
in `--where`, `--group`, `--agg`, the row sort and a type report; what they refuse and how; how they stay
byte-identical for every `--threads`; and which gates are fixed before any code is written. The spikes behind it are in
`scripts/spikes/` (throwaway; `README.md` there). Every number in this document that is called *measured* was measured
by one of them, on the Apple-silicon Mac (16 cores), by the LLVM backend of the Mac compiler (`db7d5bc`); anything else
is a judgement or an assumption, and is listed as one (section 11).

The starting point is `docs/filter.md` (`:int` is the only number the tool has, it refuses what is not an exact
integer, and a sum never wraps), `docs/parallel.md` (every `--threads` gives the sequential bytes, and the sum is
the one aggregate whose merge needs a rule, the `peak`), and `docs/backlog.md` item 5, which said "decimals, exact scaled
integers, never floats". The maintainer now wants floats too. The rule that makes both fit is the one the rest of the
tool already follows: **a number is a declared reading of a column, never inferred, never coerced, and a cell that is
not one is refused, naming the row, the line and the column.**

## 0. The decisions in one table

| # | decision | why in one line |
|---|---|---|
| A1 | `:dec(S)`, `S` in 0..18, declared; a cell with more than `S` fractional digits is **refused**, never rounded | a silent rounding is the wrong answer presented as an answer; DuckDB does it (section 2) |
| A2 | cell grammar `[+-]? digits? [. digits?]`, at least one digit; no exponent, no spaces, no separators; at most 18 significant digits once scaled | the same strictness as `:int`, plus `.5` and `5.` which every engine measured accepts |
| A3 | value = scaled `int` (64-bit), \|v\| < 10^18; the **sum** is exact in a pair of integers (`hi*2^32 + lo`) and cannot refuse | the cell path stays the `int` path; widening is needed only for the sum, and 2^30 rows of 10^18 are below 2^90 |
| A4 | numeric equality: in a `:dec(2)` column `1.5` and `1.50` are one value, one group, one distinct; as text (no suffix) they stay different | what the declaration means |
| A5 | `mean:COL[@N]`: exact sum / count, rounded **half to even** to scale `N` (default: the column's scale; `:int` has none, so `@N` is required) | the rule the design already named; no float anywhere |
| A6 | output: always exactly `S` fractional digits, no exponent | `1.50`, not `1.5` |
| B1 | `:float` reads decimal and exponent forms, the **nearest double** (ties to even), refuses `inf`, `nan`, overflow, and a non-zero cell that underflows to zero | NaN breaks equality, order and grouping; the repair is a filter |
| B2 | no negative zero: `-0` reads as `0` | one value, one key, one text |
| B3 | `min`, `max`, compare, `count`, `distinct`, group key: exact on the double values; float order = numeric order, by an order-preserving 8-byte key | one machinery for text, dec, float keys |
| B4 | `sum` and `mean` of floats: an exact accumulator (72 limbs of 32 bits), rounded **once**; merge of partials is a limbwise add | `--threads` cannot change a digit; DuckDB's double sum does (measured) |
| B5 | printing: the shortest decimal that reads back to the same double; positional from 1e-6 to 1e21, scientific outside; always a point or an `e` (`3.0`) | `std.json`'s writer's rule, which exists |
| C1 | suffix per column reference: `x:int`, `x:float`, `x:dec(2)`; one numeric type per column in one plan; no global `--types` | consistent with `--where` and `--select`; an unmarked name stays text |
| C2 | `--report types FILE`: per column counts of what each cell would be and a suggested declaration; never changes an answer | inference as a report |
| D | each new refusal is a rule with a tag, exit code 8 (data) or 2 (usage), a position, and a repair | section 6 |
| E | exact accumulators merge in any order: no `peak` for dec or float, and (decided, stage N0p) none for `:int`, whose sum becomes the pair sum too; `agg.sum-overflow` is removed | section 7 |

## 1. What exists today

**In `table`** (`docs/filter.md`): `:int` in `--where` and implicit in `sum`, `min`, `max`; an exact integer or a
refusal (`value.not-integer`, `value.integer-overflow`, `agg.sum-overflow`); empty cell refused; text keys and text
group order, bytewise; no `mean`; `:int` sum refuses past 64 bits and so needs the `peak` rule when threads merge.

**In cancho** (read, not edited; `docs/floating-point.md`, `docs/float-printing.md`, `docs/json.md`, `std/*.cho`):

| need | what the language has | what it lacks |
|---|---|---|
| a real number type | `float`, binary64, IEEE arithmetic, round to nearest even, no `-ffast-math`, the optimiser may not reassociate; `float_of(int)`, `truncate` (traps on NaN/inf/range), `bits_of` (every NaN reads as one pattern), `is_nan`, `sqrt` | `float_of_bits` (missing on purpose: "a builtin with no caller") |
| integers | `int` is 64-bit signed and **traps** on overflow; `wrapping_add/sub/mul`; `<<`, `>>` (arithmetic), `&`, `\|`, `^` | no 128-bit, no unsigned, no multiply-high, no carry flag |
| bignum | `std.bignum`: fixed-length base-2^32 limbs, add, subtract, compare, shift, multiply by a small number; **no division** | a divide (the spikes write 16-bit-digit division in 20 lines) |
| text to float | **only** `std.json.to_float`: correctly rounded (Clinger's fast path, then exact bignum arithmetic with the remainder as the sticky bit; 13,000 numbers bit for bit against Rust's `parse::<f64>`), but it reads a **tape** of a strict JSON document (no `+`, no `.5`, no `5.`) | a non-tape, non-JSON entry point |
| float to text | `std.fmt.float_into`: shortest round-trip digits as `d.ddde-k` (`0.1` is `1e-1`); `std.json` Writer `put_float`: the same digits, positional for 1e-6 to 1e21, `3.0` keeps its point (verified here: `0.1`, `100.0`, `12345.67`, `1e21`, `1.5e-7`) | a public `float_text` without the Writer |
| `2^k` scaling | `std.math.ldexp`, exact where the answer is representable (its comment says it is what a correctly rounded decimal parse needs to place a subnormal) | |
| threads | `spawn`/`join`, no atomics, no channels, shared-nothing (`docs/parallel.md` section 1) | |

Two consequences drive the design. There is no 128-bit integer, so everything wide is limbs in `int` slices written
here. And the only correctly rounded reader costs about a microsecond per call through the tape (measured, section 9),
so the float path needs its own scanner with Clinger's fast path inline, and the exact slow path is the one
cost that has to be bought back (stage N3b).

## 2. What the other tools do (measured, Mac; Python 3.14.5, pandas 3.0.3, DuckDB v1.5.6, csvtk v0.38.0)

Miller is not installed on the Mac or on the Linux box, so nothing is claimed about it. `scripts/spikes/numbers_ref.py
--compare` regenerates the cell table; the rest was typed into each tool and is quoted from its output.

**One cell at a time** (the design's answer is the first two columns; `-` is a refusal):

| cell | design `:dec(2)` | design `:float` | DuckDB DECIMAL(18,2) / DOUBLE | csvtk sum | Python `float()` / `Decimal()` |
|---|---|---|---|---|---|
| `1.500` | refused (scale) | 1.5 | **1.50** (rounded) / 1.5 | 1.5 | 1.5 / 1.500 |
| `1e3` | refused (repair: `:float`) | 1000.0 | 1000.00 / 1000.0 | 1000 | 1000.0 / 1E+3 |
| empty | refused | refused | NULL (dropped from the sum) / NULL | skipped | error |
| `NaN` | refused | refused (`not-finite`) | NULL / **nan** | error | nan |
| `inf` | refused | refused | NULL / **inf** | error | inf |
| `-0` | 0 | 0 | 0.00 / **-0.0** | 0 | -0.0 / -0 |
| ` 3 ` | refused | refused | **3.00 / 3.0** | error | 3.0 / 3 |
| `+5` | 500 | 5.0 | 5.00 / 5.0 | 5 | 5.0 / 5 |
| `1,000` | refused | refused | NULL / NULL | (a field break) | error |
| `1_000` | refused | refused | **1000.00 / 1000.0** | error | **1000.0** / 1000 |
| `.5`, `5.` | 50, 500 | 0.5, 5.0 | accepted | accepted | accepted |
| `1e999` | refused | refused (`float-range`) | NULL / **inf** | error | inf |
| `1e-999` | refused | refused (`float-range`) | 0.00 / **0.0** | 0 | 0.0 |
| `0x10` | refused | refused | NULL / NULL (BIGINT: 16) | error | error |
| fullwidth `１` | refused | refused | NULL / NULL | error | **1.0** / 1 |
| `9007199254740993` | 900719925474099300 (exact) | 9007199254740992.0 | 9007199254740993.00 / 9007199254740992.0 | 9007199254740992 | exact only in `Decimal` |

Python's own `float()` and `Decimal()` accept spaces, underscores and non-ASCII digits, so neither is the oracle
for the grammar; the reference (`numbers_ref.py`) matches ASCII-only and then uses Python's correctly rounded `float()` and
exact `int` arithmetic.

**Sums and means:**

| question | DuckDB | pandas | csvtk | design |
|---|---|---|---|---|
| ten times `0.1` | DOUBLE `0.9999999999999999`; DECIMAL(18,3) `1.000` | `1.0` (pairwise luck: 1,000,000 times `0.1` is `100000.00000000003`, `fsum` says `100000.0`) | `0.99999999999999989` | `:dec(1)`: `1.0` exactly; `:float`: the exact sum of ten doubles rounded once, `1.0` |
| `1e16, 1, -1e16, 1` | `1.0` (its Kahan `fsum` too) | `1.0` (`np.sum`) | n/a | `2.0` (the exact sum) |
| 2,000,000 doubles, `sum` | **different results by thread count and by run**: 1 thread -16827404804365.12; 2 threads -16827404804364.06 or -16827404804374.16; 4 threads three different values; 16 threads two; the exact sum is -16827404804369.316 | n/a | single thread, fixed | one value, every `--threads` |
| `AVG` of DECIMAL(18,3) `1.000,2.000,2.000` | **DOUBLE** `1.6666666666666667` | float | float | `1.667` at scale 3, half to even |
| `AVG` of `0.001, 0.002` | DOUBLE `0.0015` | | | `0.002` (a tie, to the even digit) at scale 3; `0.0015` at scale 4 |
| `int64` sum past 2^63 | HUGEINT, exact (`9223372036854775808`) | **wraps silently** (`-9223372036854775808`) | converts to float (`9223372036854775808.00`) | `:int`: `agg.sum-overflow`, as today |
| DECIMAL sum past the precision | DECIMAL(38,0): two 10^38-1 **wrap silently** to `-140282366920938463463374607431768211458` | | | cannot happen (A3) |
| a cell past DECIMAL(18,3) | cast error (`try_cast`: NULL) | | | refused, named |
| `0.1235` into DECIMAL(18,3) | `0.124`, and `0.1245` into `0.125`: rounds **half away from zero**, silently | | | refused (A1) |

What this says. DuckDB is exact where the type is exact (DECIMAL sums), and wraps silently at the top of DECIMAL(38);
it rounds on the way in; its `AVG` of a decimal is a double; its DOUBLE sum is a function of the thread schedule. pandas
infers (`' 3 '`, `'+5'` make an `int64` column), drops `NaN` and empty cells from sums, and wraps `int64`. csvtk reads
everything as float64 and refuses `NaN`, spaces and out-of-range, but skips empty cells silently. None of them refuses
a cell that has more digits than the column was declared to hold, and none gives a float sum that is the same for
every thread count. The design's whole point is that last pair of sentences.

**Speed on the same numeric column** (`scripts/spikes/bench_engines.py`: 1,000,000 rows, 56 MB, `price` with two decimals,
`ratio` a double in shortest form, 15-17 digits; every answer checked against Python before timing; minimum of 5
interleaved runs; seconds; the `table` binary is today's `:int` one):

| question | `table` (int) | DuckDB 1 thread | DuckDB 16 threads | csvtk `-j 1` | pandas |
|---|---:|---:|---:|---:|---:|
| group-sum of `bytes` (bigint) | **0.076** | 0.179 | 0.073 | | |
| group-sum of `price`, DECIMAL(18,2) | (the target) | 0.186 | 0.074 | | |
| group-sum of `price`, DOUBLE | | 0.183 | 0.075 | 0.448 | 0.137 (with the read) |
| group-sum of `ratio`, DOUBLE (17 digits) | | 0.187 | 0.076 | | |
| filter `status=404 and bytes>50000`, count | **0.070** | 0.173 | | | |
| filter `status=404 and price>=500.00` | | 0.178 | 0.075 | 2.782 | |
| mean of `price` by status | | 0.178 | 0.075 | | |

DuckDB's time does not depend on the type: its CSV read dominates, and it reads a 17-digit double as fast as an integer
(it has a fast exact reader). That is the bar for the float column: **a 17-digit float cell must cost about what an
integer cell costs, and `std.json`'s reader does not (section 9).**

## 3. A. DECIMAL: `:dec(S)`

### 3.1 The choice: a declared scale that refuses, not inference, not per-cell scales

Three ways were weighed on one example: a `price` column whose cells are `12.5`, `12.50`, `3`, and one `3.999`.

| way | `sum` | cost |
|---|---|---|
| **declared `:dec(2)`** | refuses at the `3.999` row, naming it, with the repair `:dec(3)` as a whole invocation; with `:dec(3)` the sum is exact and printed with three digits | the user states a scale (the type report says which) |
| inferred scale (the maximum number of fractional digits seen) | needs the whole file first (two passes, or a scale that changes while reading), and the output's shape (`28.499` against `28.50`) depends on **one cell**; threads would each infer a scale and have to realign | silent: a typo in one row changes every printed digit |
| exact values with their own scales (`12.5`, `12.50` are different `(mantissa, scale)`) | exact, but comparison needs an alignment `10^k` that can exceed 64 bits, a sum needs a common scale anyway, a key needs a canonical form, and the i64 fast path is lost | two code paths for a case the declaration makes unnecessary |

**Chosen: declared, refusing.** `S` is written in the plan, `0 <= S <= 18`, and a cell with more than `S` fractional digits is
`value.decimal-scale`, never rounded, never truncated (what DuckDB does silently, section 2). Fewer digits are padded: in
`:dec(2)` the cell `3` is 300 and `1.5` is 150. A bare `:dec` is a syntax error (`expected "(" scale ")"`): there is no default
scale to guess. `:dec(0)` is an integer with a decimal grammar: `5.` is accepted (no fractional digits), `5.0` is refused (one fractional digit exceeds scale 0).

### 3.2 The cell grammar and its order of checks

```
DEC   := [ "+" | "-" ] INT_PART? [ "." FRAC? ]      at least one digit in all; ASCII digits only
```

* `+5`, `-5`, `.5`, `5.`, `-.5`, `00012.30` are accepted (`12.30` at scale 2). `.5` and `5.` were weighed: every engine
  measured accepts them, a file that writes `.5` is not malformed, and the cell is unambiguous. `1e3`, `1,000`, `1_000`, ` 3 `,
  `0x10`, `NaN`, `inf`, an empty cell, a lone sign or point, a second point, a non-ASCII digit are refused. There is no exponent
  because `:float` exists, and the refusal says so (the repair).
* **Whitespace is not trimmed**, as `:int` today: a column of padded numbers is a data problem the report shows
  (`n cells with spaces`), not something to hide inside every comparison.
* **Order of checks** (fixed because the spike's first draft differed from the reference exactly here, and the differential
  test found it): the **whole cell** is checked against the grammar first (`value.not-decimal`), then the number of
  fractional digits against `S` (`value.decimal-scale`), then the width (`value.decimal-too-wide`).
* **Width**: after padding to scale `S` the value must be below 10^18 (`9999999999999999.99` is the largest `:dec(2)`; leading zeros do
  not count; `-0`, `-0.00` are 0, and there is no negative zero).

### 3.3 Representation, and why there is no "widened path"

A cell is a scaled **`int`**: below 10^18 in absolute value, so it fits 64 bits with 3 bits to spare, parses with the
`parse_int` loop (no overflow check per digit), compares as an integer, and is the key of a group as 8 bytes. The ask
was "widen to a bignum on overflow so sums do not refuse where they need not; the fast path must stay i64". The measured
answer is that nothing has to widen *per value*, only the **sum** keeps two integers, and it never needs to refuse:

```
lo += v;  if lo >= 2^62 or lo <= -2^62 { hi += lo >> 32;  lo &= 0xffffffff }          value = hi * 2^32 + lo
```

`|v| < 2^60` so `lo` cannot overflow between two checks. `--max-rows` has a ceiling of 10^9 (< 2^30), so `|sum| < 2^90`, and `hi`
(an `int`) holds 2^95: **the sum of a `:dec` column cannot overflow and `agg.sum-overflow` does not exist for it.** The ceiling is
what makes the claim a proof and not a hope; a test builds a table whose every value is 10^18-1 so the carry runs every fifth row.

*Measured (`superacc.cho` mode `pair`, 1M prices, ns per value, minimum of 5, difference of 101 rounds and 1):*
plain f64 add 0.48, today's checked i64 add 0.82, **the pair add 0.44**. The widened sum is not slower than the checked one it
replaces (it has no data-dependent overflow branch to mispredict). Whole cell (`numparse.cho`, 8-digit cells, ns per cell above the
7.2 ns the line scan costs): `parse_int` + checked add **+2.2**, `parse_dec` at scale 0 + pair add **+3.6**: the decimal reader is
**+1.4 ns a cell** over the integer one, about 2% of the 62-76 ns a row costs today.

*Output of a sum* has to print up to 28 digits: a limb division by 10^9 on three limbs (about 20 lines; `std.bignum` has no
divide, the spike's 16-bit-digit division is the model).

### 3.4 Compare, min, max, count, distinct, group keys

* All on the scaled `int`: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in (...)` are integer operations. `contains` on a typed column is
  a `where.syntax` error, as `:int` today.
* **Equal as numbers, not as text.** In `price:dec(2)`, `1.5` and `1.50` are one value: one group (`--group price:dec(2)`), one
  `distinct:price:dec(2)`. Read as text (no suffix: today's behaviour, unchanged) they are two (`distinct:price` counts bytes). The
  spike's DuckDB check: `count(distinct)` of `1.5, 1.50, 1.500` is 3 as varchar, 1 as decimal. A test pins both.
* A group key or `min`/`max` of a `:dec(S)` column is **printed at scale `S`** (`1.50`), not as the first cell that had that value.
  That is what makes the output independent of which row came first and of the thread count.
* Group order of a typed key is numeric (the key is the 8-byte order-preserving form, section 4.5).

### 3.5 Literals in `--where`, and mixing

```
price:dec(2) >= 12.50        12.5 and 12 are also fine (12.5 -> 1250, 12 -> 1200)
price:dec(2) in (1, 2.5, 3.25)
price:dec(2) > 12.505        where.syntax: the literal has 3 fractional digits, the column's scale is 2; write :dec(3)
price:dec(2) > 1e3           where.syntax: an exponent is not a decimal; write 1000
bytes:int > 12.5             where.syntax: an :int column takes an integer; write :dec(1) to compare tenths
x:float > 12                 fine: an integer literal is also a float
```

A literal is read by the cell's own reader at the column's scale, so the literal and the cell obey one grammar, and a
literal that is finer than the column is an error of the expression at the literal's offset (`detail.offset`, `detail.expected`),
the same shape as an `:int` literal today. It is **not** rounded and not compared "as if": `12.505` against two-digit cells has
a well-defined exact answer, but writing it almost always means the declaration was wrong, and the refusal is the cheap way to find out.
Mixing a `:dec` and an `:int` column in one comparison is not a thing (a condition has one column and a literal).

### 3.6 `mean`

`mean:price:dec(2)` is **the exact sum divided by the count, rounded half to even to scale `N`**, with `N` written `@N` (default:
the column's scale `S`; allowed 0..18), no float anywhere: the numerator `sum * 10^(N-S)` is a few limbs, divided by the count in
16-bit digits (the count is below 2^30, so the remainder times 2^16 fits an `int`), and the remainder against half the count decides
the last digit. For `:int` (and an untyped `mean:COL`, which means `:int` like `sum`) there is no scale to inherit, so `@N` is **required**
(`agg.bad-spec` with the repair that adds `@2`): a default would be a guess.

| values (scale 3) | `mean` at `@3` | `@4` | DuckDB `AVG` |
|---|---|---|---|
| `1.000, 2.000, 2.000` | `1.667` | `1.6667` | `1.6666666666666667` (DOUBLE) |
| `0.001, 0.002` | `0.002` (1.5 thousandths: tie, to even) | `0.0015` | `0.0015` |
| `0.001, 0.001, 0.002` | `0.001` | `0.0013` | `0.0013333333333333333` |

Half to even was chosen over half away from zero (DuckDB's cast): it is the rule the design (`cancho-tools` `docs/next-tools.md`
section 5) already named for this tool, and it has no drift when many means are averaged. The reference (`numbers_ref.dec_mean`,
`Fraction` arithmetic) agrees with Python's `Decimal.quantize(ROUND_HALF_EVEN)` on 3,000 random cases.

### 3.7 Output format

`1.50` always: exactly `S` fractional digits, no exponent, no thousands separator, `-` only for a non-zero value (`-0.05`, never `-0.00`), no
point at scale 0. As JSON a decimal is a **string** (like every field of `table.v2`): a JSON number would be read as a double by
nearly every consumer, which is the loss this type exists to avoid.

## 4. B. FLOAT: `:float`

### 4.1 Grammar and policy

```
FLOAT := [ "+" | "-" ] INT_PART? [ "." FRAC? ] [ ("e"|"E") [ "+" | "-" ] DIGITS ]      at least one mantissa digit
```

* The decimal grammar of 3.2 plus an exponent. Whitespace, separators, `0x`, non-ASCII digits: refused as for `:dec`. A cell
  longer than 1,100 bytes is `limit.number-too-long` (a decimal exactly halfway between two doubles has at most 767 significant digits,
  `docs/json.md` 4.1; the bound is the longest cell that is decidable, not a tuning). An exponent is read saturating (past 100,000 it
  cannot change the answer: `std.json` does the same), so `1e99999999999999999999` is a range refusal, not an `int` trap.
* **`inf`, `infinity`, `nan` (any case, any sign) are refused** (`value.not-finite`). Options: refuse (chosen); skip them
  (csvtk's `-i`, pandas: silently fewer rows than the file has, the thing `docs/filter.md` already refuses for empty cells); accept with a
  total order (DuckDB: NaN equals NaN and is the greatest, `sum` becomes `nan` and stays). The first is the only one that cannot give a
  quiet wrong answer, and the repair is the existing idiom: `x != 'NaN' and x:float > 5` never reads the NaN cell as a number
  (conditions stop at the first false).
* **Overflow and underflow**: `1e999` is `value.float-range` (`detail.direction: overflow`); a cell with a non-zero digit that rounds to
  zero (`1e-999`, `2.4703282292062327e-324`) is `value.float-range` (`underflow`) and not a silent `0`; subnormals (`4.9e-324` is
  `5e-324`) are read, correctly rounded, like any other. A cell of zero digits with any exponent (`0e999`) is `0`.
* **No negative zero.** `-0`, `-0.0`, `-0e5` read as `0`. A double has two zeros that `==` cannot tell apart and `1/x` can; `table`
  has no division, so keeping the sign would add a distinct value to every group, distinct and key for no one's benefit (DuckDB groups them
  together and prints `-0.0` for the cell: measured). The price is that `min` of `-0` cells prints `0`.

### 4.2 Correctly rounded reading: what the language gives, and the path

Does cancho give a correctly rounded text-to-double? **Yes, but only through `std.json.to_float`** (a tape of a JSON document), and
**through that door it costs about a microsecond a cell (measured: 1,017 ns for `12345.67`, 1,557 ns for a 17-digit double,
1,038 ns for an exponent form**, by the difference of 21 rounds and 1 over a million cells; most of it is the tape and the region the
call allocates, not the arithmetic). So the design is a scanner of its own, three tiers:

1. **The scanner** (`numparse.cho` `scan_float`, about 60 lines): sign, digits into a mantissa `m` (up to 15 digits kept exactly), the decimal
   exponent `e10`, the grammar check of the whole cell. Measured +3.7 ns a cell over the line scan on `12345.67`-shaped cells.
2. **Clinger's fast path, inline**: if `m <= 2^53` and `abs(e10) <= 22`, `m` and `10^|e10|` are doubles and **one** division or multiplication is
   correctly rounded (a `static [float]` table of the 23 powers). Measured **bit-exact on 84,917 decided cells** against Python's
   `float()` (`numparse_check.py`), +3.7 ns for two-decimal prices, +9.6 for `1.5e3`-shaped cells.
3. **The exact slow path** for what Clinger cannot decide (more than 15-16 digits: random doubles printed in shortest form; or `abs(e10) > 22`):
   the cell is rewritten into JSON grammar in a scratch buffer (`+5` to `5`, `.5` to `0.5`, `5.` to `5`) and read by `std.json`. Correct, and
   **1.0-1.6 microseconds a cell**: on the 17-digit column above, 1,000,000 cells cost 1.0-1.6 s, **against 0.19 s for DuckDB at one thread
   and 0.076 s for today's integer sum**. That is a fail of the speed gate for that column shape, and it is the honest state of the language today.

What buys the third tier back, in order of cost (none built; none measured beyond the baseline above):

* **N3a** (the gate is correctness only, speed is reported): tier 3 as above. Needs nothing from cancho.
* **N3b**: Eisel-Lemire (what DuckDB's reader does): one 64x64 to 128-bit multiply by an entry of a table of 650 128-bit powers of five, and a
  rare fallback. cancho has no multiply-high, so the multiply is four 32x32 partial products with `wrapping_mul` and carries by hand; the table is generated
  (as `std.math`'s 2/pi table was). The gate is in section 8. **Unknown**: its real cost here; the design records only that the spike was not written.
* **Upstream** (`cancho`, section 11): a text-free entry `std.json.decimal_to_float(m, e10, digits, sticky...)` that skips the tape. If
  the tape and region are most of the 1 microsecond, this alone could be the cheaper fix. Not measured separately.

### 4.3 Compare, min, max, count, distinct

Ordinary `<` and `==` on doubles; there is no NaN to break them and no `-0` to split equal values. **A comparison is of the nearest doubles, not of the
decimal text**: `x:float = 0.1` matches the cells `0.1`, `0.10`, `1e-1` and `0.10000000000000001` (all the same double). For an exact
decimal comparison use `:dec`. A test pins this with the 17-digit neighbours. `min` and `max` keep the double's **order key** (4.5), an
`int`, so they are integer compares in the engine and print by the 4.7 rule, not as the winning cell's text (`1.50` and `1.5` tie; the output must
not depend on which came first).

### 4.4 `sum` and `mean`: an exact accumulator

**The problem.** Floating-point addition is not associative, and `--threads N` adds in a different order for each `N`. Measured: DuckDB's
`sum(double)` over 2,000,000 values gave a different answer for 2 threads on two runs, three different for 4, two for 16, and none equals the exact sum.
The parallel guarantee of this tool (the sequential bytes for every `N`) cannot hold with `+`.

**The accumulator** (`scripts/spikes/superacc.cho`): the sum of doubles kept as an exact integer multiple of 2^-1074 (every finite double is one),
in **72 limbs of 32 bits** (limb `i` has weight 2^(32i - 1074)) plus a counter. Adding a double is: split the sign, the biased exponent and the 53-bit mantissa
from `bits_of` (a subnormal has no hidden bit and exponent 1), shift the mantissa by `p mod 32` and add three pieces of at most 2^33 into limbs `p/32`, `+1`, `+2`,
**signed** (a negative double subtracts its pieces), with no carry. Every 2^27 additions a carry pass makes every limb but the top a value in `[0, 2^32)` and the top the sign.
**Nothing rounds until the end.**

* **Final rounding, once.** Find the top bit; keep 53 bits; the next bit is the guard, any lower non-zero bit the sticky; round half to even; build the
  double as `float_of(m) * 2^k` with `std.math.ldexp` (exact for `m <= 2^53`; a result below `2^-1022` has at most 53 significant bits and is exact, so there is
  no double rounding). The only rounding in the whole computation is the one the type forces.
* **Merge** of two accumulators: carry both, add limb by limb, carry. **Exact, so order-independent**: the merged state is the same integer however the rows were divided.
* **Refusal**: the exact sum can exceed the largest double: `agg.float-overflow` (the group is named; there is no row to name, because the exact sum is not
  a function of the order). Intermediate overflow does not exist: Python's `math.fsum` raises `OverflowError` in 15-30 of the 502 spike cases whose exact sum is finite; this does not.
* **`mean`** of floats is the exact sum divided by the count, rounded **once** (single rounding, unlike `fl(fl(sum) / n)`). The division takes `|sum| * 2^64` in 16-bit digits (the count
  is below 2^30); with 64 fractional bits the remainder can never decide a tie, because a tie differs from the quotient by at least 1/(2n) of the last place of the unit (2^-1074)
  and 2^-41 is far above 2^-64 (the spike's "sticky" mutant is therefore **equivalent**, and the mean needs no sticky bit for any count below 2^62).

**Measured** (`superacc_check.py`, five seeds, 502 cases each: random bit patterns, cancelling runs, subnormals, sums at a tie, `DBL_MAX` sums, 40 two-decimal price lists,
and one list of 1,000,000 random doubles of random exponents), against **Python integer arithmetic** (exact sum as a multiple of 2^-1074 rounded by true division, which is
correctly rounded) and `math.fsum`: **0 mismatches in sum, in the three-part merge (merged in order c, a, b), in the five-part interleaved merge (merged in reverse), and in the mean.**
Eight mutants of the accumulator: five caught by the check (ties rounded up, sticky ignored, subnormal as normal, a lost carry, a negation that forgets the top limb); one (the carry threshold set so high it never carries) caught only by a **second check**
(`rep_check.py` adds one all-ones-mantissa double 2.4 billion times and compares with `R * x` exactly: the real build passes, that mutant **traps**; with the threshold lowered to 3 the 502 cases pass too, so the carry itself is right); two survive and are
**equivalent**: the mean's sticky bit (above), and a merge that does not carry its inputs first (the counter bounds a limb at 2^27 * 2^33 = 2^60, so the sum of two is below 2^63 and the carry that follows is all that is needed).

**Cost** (`timeit.py`, 1,000,000 values, ns per value, minimum of 3, difference of 41 rounds and 1; and the one-off costs in 1,000-value rounds):

| | two-decimal prices | random doubles, random exponents and signs |
|---|---:|---:|
| plain f64 add (**not** thread-stable) | 0.48 | 1.1 |
| today's checked i64 add | 0.73-0.82 | 1.4 |
| the decimal pair add | 0.44 | |
| **the exact accumulator** | **2.1** | **4.9** (sign mispredicts) |

Per cell, with the scanner: **+5.9 ns** over the line scan on prices (decimal pair path +3.3), against the 62-76 ns the tool spends on a row today. `merge`: 0.25 microseconds; the end
(`finalize` and `mean`, bit by bit): 2.5 microseconds (100,000 groups: 0.25 s, to be done word by word, not bit by bit, if it ever matters).
**State: 73 `int`s = 584 bytes per group per float `sum` or `mean`** (100,000 groups: 58 MB, which is the whole default `--max-state-bytes` of 64 MiB), counted in `limit.state-too-large`. A
two-tier accumulator (a few limbs around the first value's exponent, spilling to the full 72 only when a value falls outside them) would cut a typical group to 6 limbs; it is listed
(section 11) and not designed, because the fixed one is already correct and the bound is stated.

### 4.5 Float and decimal keys: group, sort, distinct

**What a float group key means: the double's value, `-0` merged with `0`, as the order-preserving 8 bytes of its IEEE image**: the 64 bits of the (canonicalised) double
with the sign bit flipped for a non-negative value and every bit flipped for a negative one, big-endian. Compared bytewise, the keys are in **numeric order**; equal keys are the
same double. A `:dec(S)` key is its scaled `int` plus 2^63, big-endian: the same property. (`numbers_ref.float_key` and `dec_key` are checked against Python's sort on 2,000
random values.) The consequences are the reason for choosing it:

* the **group machinery is unchanged** (`agg.cho` keys are length-prefixed bytes in a `std.map`, sorted bytewise; a typed key is `8` plus 8 bytes, so a typed group sorts numerically for free);
* **distinct** is the same key in the `seen` map;
* a **row sort** (the other agent's `--sort-rows`) compares opaque key bytes: a text key is the cell, a typed key is these 8 bytes, descending is the complement, ties keep file order. Nothing in
  the sorter depends on the type; the single function it needs is `key(type, cell) -> 8 bytes or a refusal`. An empty or malformed cell in a typed sort key is the same refusal as everywhere (not "sorted first").
* a float `min`/`max` accumulator is that key as an `int`, so the engine's integer compare and its parallel merge (smaller, larger) apply unchanged.

### 4.6 Printing

The shortest decimal that reads back to the same double (`std.fmt.float_into`'s digits; `docs/float-printing.md`), **positional from 1e-6 up to 1e21 and scientific outside, always with a point or an `e`**
(`0.1`, `100.0`, `12345.67`, `1e21`, `1.5e-7`: what `std.json`'s writer does, verified here), so a float never looks like an integer in a column. It is the one formatting rule in the language
that already has a test corpus (1,500 floats written and read back to the same bits). The table needs it without the JSON writer around it: upstream ask (section 11). As JSON a float is a string, like every field.

## 5. C. Where each type fits

| surface | `:dec(S)` | `:float` | note |
|---|---|---|---|
| `--where` | `= != < <= > >= in`, literals of 3.5 | same, literals as 4.1 | one numeric type per column per plan (`column.type-conflict`, exit 2); an untyped mention is text and may mix freely, as `bytes != '' and bytes:int > 5` does today |
| `--agg` | `count`, `sum`, `min`, `max`, `mean[@N]`, `distinct` | `count`, `sum`, `min`, `max`, `mean`, `distinct` | `sum:price:dec(2)`; untyped `sum:COL`, `min`, `max`, `mean` mean `:int`, as today |
| `--group` | `--group status,price:dec(2)`: numeric key, printed at scale | `--group x:float`: numeric key | text keys unchanged |
| `--sort` (of groups) | an aggregate numerically: a sum compares as `(hi, lo)`, a float sum as the double | same | the column label stays `sum:price` (no type in the label) |
| `--sort-rows` | the 8-byte key of 4.5 | the 8-byte key | plugs into the row sort as an opaque key; no other change |
| `--select`, shape | not typed: bytes out, as today | not typed | a type is a way to read a cell, and `--select` does not read |
| report | `--report types` | | 5.2 |

### 5.1 How a type is declared

**A suffix on each reference, as `:int` is**: `COLUMN := WORD [ ":int" | ":float" | ":dec(" N ")" ]`, in `--where`, in `--agg` items and in `--group` lists (so
`--agg 'sum:price:dec(2),mean:price:dec(2)@4'`). A header that really ends in `:float` is quoted or escaped, exactly as one that ends in `:int` is. A **global `--types a:int,b:dec(2)`** was weighed:
it avoids repeating the suffix and the chance of writing `dec(2)` here and `dec(3)` there. It was **not chosen** because it would make the meaning of an *unmarked* name depend on a flag elsewhere on the
command line (today an unmarked name is text, always), and because the repetition is not a risk the suffix cannot catch: one numeric type per column per plan, a second one is `column.type-conflict` with the
repair spelled out. It is listed as an open question for the maintainer.

### 5.2 The type report (inference, never coercion)

`table --report types [--where ...] FILE` reads the file once and answers one row per column (`table.v2`, rows of strings, bounded memory: a few counters per column):

```
name  cells  empty  int  dec_max_scale  dec_max_digits  float  not_finite  other  suggest       first_other_row  first_other_cell
price 1000000 0      0    2              7               0      0           0      dec(2)        -                -
ratio 1000000 0      0    0              0               1000000 0         0      float         -                -
note  1000000 12     0    0              0               0      0           999988 text          1                a,b 1
```

A cell is classified once, in this order: empty; `int` (the `:int` grammar, fits 64 bits); `dec` (a point, no exponent, up to 18 digits; its scale is the digits after the point);
`float` (an exponent, or more than 18 digits, finite, in range); `not_finite` (`nan`, `inf`); `other`. **`suggest` is the smallest declaration that every non-empty cell satisfies** (`int`, then `dec(S)` with
`S` the largest scale seen, then `float`, else `text`), and the report says, as `first_*`, the first cell that is not accepted by each rejected type, so the repair is a pointer and not a guess. It never changes what any
other flag does (there is no "auto" type); empty cells are counted apart because they are refused by every numeric reading. Parallel: counts add, maxima combine, the "first" cell is the one with the smallest
row in file order (the ranges offset their rows exactly as ragged rows are today). A `float` column of exactly `0.1`-shaped cells gets `dec(1)`, **not** `float`: the report prefers exact, and says so in the row.

## 6. D. Refusals

All carry `row` (1-based among data records), `line`, `column` (name and 1-based position), `cell` (its first 64 bytes), `context` (`--where`, `sum`, `min`, `max`, `mean`, `group key`, `sort key`), exit code **8** unless
stated, and the first in **file order** (the threads' rule, `docs/parallel.md`). Each is an `extra_rules` entry (`tag|exit|retry|summary`) and has a fixture.

| tag | exit | when | repair (`repair.kind: choose`, whole invocations, at most 5) |
|---|---|---|---|
| `value.not-decimal` | 8 | a `:dec` cell is not a decimal: empty, exponent, space, separator, sign alone, non-ASCII | an exponent: the same argv with `:float`; a space: none (clean the file; the report counts them) |
| `value.decimal-scale` | 8 | more fractional digits than `S`; `detail.digits`, `detail.scale` | the same argv with `:dec(K)`, `K` the digits of **this** cell (the report gives the file's maximum) |
| `value.decimal-too-wide` | 8 | 18 or more significant digits once scaled to `S`; `detail.max_digits: 18` | a smaller scale; or `:float` |
| `value.not-float` | 8 | a `:float` cell is not a number of the grammar | none |
| `value.not-finite` | 8 | `inf`, `infinity`, `nan` | none; the message gives the idiom `x != 'NaN' and x:float ...` |
| `value.float-range` | 8 | overflow, or a non-zero cell that rounds to zero; `detail.direction` | none |
| `limit.number-too-long` | 8 | a number cell longer than 1,100 bytes | none (the bound is the longest decidable cell, 4.1) |
| `agg.float-overflow` | 8 | the exact `sum` of a group is beyond the largest double; `detail.group` | `:dec(S)` (exact, wide), if the data is decimal |
| `column.type-conflict` | 2 | one column given two numeric types in one plan (`price:dec(2)` and `price:float`) | one of the two argvs |
| `agg.bad-spec` (exists) | 2 | `mean` of `:int` or of an untyped column without `@N`; `@N` over 18; `mean` of text | the same argv with `@2` |
| `where.syntax` (exists) | 2 | a decimal literal finer than the column's scale; a decimal or exponent literal against `:int`; a float literal that is not finite; `contains` on a typed column; a bare `:dec`; a scale over 18 | `detail.expected` says what would have been right at `detail.offset` |
| `limit.state-too-large` (exists) | 8 | the 584 bytes of a float sum per group count toward it | raise `--max-state-bytes` |
| `value.not-integer`, `value.integer-overflow`, `agg.sum-overflow` (exist) | 8 | unchanged for `:int`; `value.not-integer`'s repair gains "the cell has a point: `:dec(N)`; an exponent: `:float`" | |

`:dec` has no `agg.sum-overflow` (3.3) and `:float` has no running-sum overflow (4.4). **Limits**, all constants stated in `describe`: digits of a decimal 18; scale 0..18; a number cell 1,100 bytes; an exponent saturates at 100,000; an accumulator
is 72 limbs of 32 bits, 584 bytes, bounded by `--max-state-bytes` and by `--max-rows` (the carry every 2^27 additions keeps a limb below 2^63 for any count).

## 7. E. Parallelism

**What changes in the merge** (`docs/parallel.md` section 3): the aggregates are merged exactly as they are now (counts add; minima and maxima take the smaller and larger; distinct is a union), and **every
new sum is exact, so its merge is a plain add and needs no `peak`**:

| aggregate | partial | merge | order-dependent? |
|---|---|---|---|
| `:int` sum | `(hi, lo)` (stage N0p; before it, one `int` and a `peak`) | add the pairs | no |
| `:dec` sum, `mean` | `(hi, lo)` | add the pairs, carry | no |
| `:float` sum, `mean` | 72 limbs + counter | carry both, add limbwise, carry | no: the merged state is **the same integer** |
| `min`/`max` (typed) | the 8-byte key as an `int` | smaller / larger | no |
| `distinct` (typed) | canonical keys | union | no |
| `count` | `int` | add | no |

The `peak` rule existed because a sequential running sum can **refuse at a row**, which a range's own sum from zero cannot tell. The new sums have no row at which they refuse (`:dec` cannot; `:float` refuses
only on the exact total, which is not a function of the order), so nothing has to be re-read in order, and a range is **always** merged. **Decided (stage N0p): `:int` takes the pair sum too**, and loses the `peak`,
`agg.sum-overflow` and the re-read of a range whose sum came near the edge. The pair add measured faster than the checked add it replaces; a sum past 64 bits is now printed in full (up to 28 digits) instead of refused.
The cell is split rather than carried (`lo += v & 0xffffffff; hi += v >> 32`, no branch), so any `int` cell, not only 18 digits, can be added: with at most 10^9 rows (`--max-rows`' ceiling) `lo` stays below 2^62 and `hi` below
2^61, and `hi * 2^32 + lo` is exact to 2^93.

**Byte-identical for every thread count**: the answer to every plan with a typed column (rows, groups, aggregates, sort, top, pages, csv and json), including each refusal's row, line, column and detail. **Not promised and not
needed**: the in-memory state between merges (limbs before a carry differ with the partition; after the carry they are the canonical form, and the final text depends only on the exact value).
**Tests** (`tests/conformance/test_numbers_parallel.py`, written before the code): every plan of the differential suite for N in {1, 2, 3, 4, 8, 16, 64} and `--chunk-bytes` in {1, 7, 64, 1000}
(a boundary falls inside almost every record), on **columns built so that plain addition is order-dependent** (`1e16, 1, -1e16, 1`, repeated and shuffled; 100 groups of cancelling runs; subnormals; sums that
need the carry), and each answer compared with the sequential one **and** with Python's `math.fsum` and `decimal`; 200 repetitions give the same bytes. **The test must be able to fail**: the mutant "float sum as plain f64 addition"
must be killed by the cancellation file at N >= 2 (and it is the spike's observation about DuckDB that it would be), and so must "merge the partials in thread-finish order".

## 8. F. The gates, fixed before the build

Each is a command that can fail, with its threshold; none is relaxed after the code exists, only the maintainer can change one, in a pull request that says so.

| # | gate | pass line | how it can fail |
|---|---|---|---|
| G1 | **reference semantics**: `scripts/spikes/numbers_ref.py` and `numbers_ref.SPEC` (44 edge cells, to grow: every cell of section 2, a cell at each of 1, 17, 18, 19, 20 digits, 767 and 768 significant digits, a cell at every scale 0..18) against the tool, for `:dec(0)`, `:dec(2)`, `:dec(18)`, `:float` | every cell: the same value or the same rule, row, line, column | a refusal that differs by a tag or a row |
| G2 | **differential vs Python** (`decimal`, `fractions`, `math.fsum`): 1,800 generated tables (quoted fields with delimiters and newlines, ragged rows, empty cells, signs, leading zeros, numbers at 10^18, subnormals and `DBL_MAX` neighbours) x generated plans (up to three conditions of both types, `in`, `contains` refused, select, group by typed and untyped keys, every aggregate, sort, top, pages, json and csv): the existing `test_plan.py` generator extended | 0 mismatches in bytes; every refusal's row, line, column equal | the refimpl is the oracle and an independent Python script, not the Lex code |
| G3 | **numbers fuzz**: grammar-fuzzed cells (the alphabet of the spike: digits, signs, point, `e`, `_`, `,`, `x`, spaces, multibyte) in 300 plans x 4 scales; byte mutation of valid numeric files | no trap, no crash, no hang; deterministic bytes; the same refusal as the reference | a trap in a path nobody generated |
| G4 | **parallel differential** (section 7): N in {1, 2, 3, 4, 8, 16, 64}, `--chunk-bytes` in {1, 7, 64, 1000}, tiny ranges, adversarial cancellation files; 200 repetitions | the sequential bytes, and `fsum`'s | the plain-add mutant must be caught |
| G5 | **mutants** (`scripts/numbers_mutants.py`, at least 60, the sites listed per stage): decimal scale off by one, truncate instead of refuse, round half up, a carry that drops, `hi` shift 31 for 32, the 10^18 bound off by one, `-0` kept, the order of checks changed, `1.5` and `1.50` split in a key, the float key without the sign flip, a tie to odd, a lost sticky, a limb not carried, merge in finish order, a literal rounded, an exponent saturation removed, the Clinger bound moved, `<=` as `<`, `mean` half up, half even only for positives ... | every one killed, and every survivor is **proved equivalent in a comment** (as the mean's sticky bit above) or fixed by a new test | a survivor with no proof fails the gate. In the spikes, of 13 mutants 2 were equivalent (proved in 4.4) and the 11 others were all killed: 9 by the first check, 2 only after a check was added or made stricter (`rep_check.py`; the `+0`-bits demand) |
| G6 | **no regression of the integer paths** (`scripts/gate_regress.py`, the standard 1M-row file, group-count, group-sum, filter, `cut`, count/sum/min/max, a text filter, 1 and N threads): **second revision (after N3a)**: the noise is the MAXIMUM of ratio(base_i / base) over at least TWO extra builds of the old sources in other directories (`--base2`, `--base3`); a cell passes when ratio(new / base) <= max(1, that maximum) + 0.01 (ratios of the minimum of >= 21 interleaved runs) **and** median(new) / median(base) <= 1.01 over the same runs; the builds taking turns to go first, a warm-up discarded, outputs byte-identical first (first redefined after N2, revised after N3a: see "G6, revised" below) | every cell within its bound and its median limit | any cell over either |
| G7 | **decimal speed**, same questions on `price:dec(2)` | group-sum, filter, min/max within **1.15x** the integer cell (spike prediction: 1.02-1.06x, from +1.4 ns over `parse_int` on a 62-76 ns row) | slower than 1.15x |
| G8 | **float speed on decided cells** (at most 15 digits, `abs(e10) <= 22`): group-sum with the exact accumulator, filter | within **1.25x** the integer cell (spike prediction about 1.1x: +5.9 ns on prices) | slower than 1.25x |
| G9 | **float speed on 17-digit cells** (`ratio`): group-sum | N3a: reported, no pass line (the language today: 1.0-1.6 s on the 1M-row file); **N3b: not slower than DuckDB at one thread on the same file** (0.187 s on the Mac, section 2) | N3b above that |
| G10 | **against others**, on Mac and on Linux x86-64 (niced, cores 0-5, short, never while the soak needs them): sum, mean, filter by a numeric column; DuckDB with DECIMAL and with DOUBLE, `csvtk` for floats, Miller (**to be installed first**: absent on both machines today) | each contender's answer checked first; on a column where DuckDB's DOUBLE sum differs from the exact one, the table says by how much | an answer that differs, a missing contender |
| G11 | **rules**: every new tag in `test_rules.py` with a fixture, the exit code in the table, the summary in `extra_rules`; `introspect` lists them | all present | a rule without a fixture |
| G12 | **flat memory**: `test_memory.py` extended: a numeric filter and a decimal group stay at 2 MB from 2 MB to 37 MB of file; float group state is within 584 bytes per group per float aggregate | as stated | memory that grows with the file |

## 9. Measured spikes, in one place

All on the Mac, LLVM backend; programs in `scripts/spikes/`; "difference method" means (time at 21 or 41 rounds minus time at 1) / rounds, minimum of 3-5 runs, so the process start and the
read of the input are not in it.

| what | result |
|---|---|
| exact accumulator, correctness | 5 seeds x 502 cases (the last of 1,000,000 doubles): 0 mismatches against Python's exact integer arithmetic and `math.fsum`, in sum, 3-way merge, 5-way reverse merge, mean; 1.2 and 2.4 billion adds with the all-ones mantissa: exact |
| exact accumulator, mutants | 8: 5 killed by the first check, 1 by the second (`rep_check.py`: no carry traps), 2 equivalent (the mean's sticky bit; a merge that does not carry its inputs, 4.4) |
| accumulator add, ns per value | plain f64 0.48 (1.1 on random exponents), checked i64 0.73-0.82 (1.4), pair 0.44, exact accumulator 2.1 (4.9) |
| accumulator merge; finalize + mean | 0.25 microseconds; 2.5 microseconds (bit by bit) |
| accumulator state | 73 `int`s: 584 bytes per group per float sum or mean |
| reading a cell, ns above the line scan (7.2-8.7 ns) | `parse_int`: 5 digits +1.1, 8 digits +2.2; `parse_dec`: 8 digits at scale 0 +3.6, prices at scale 2 +3.3; float scanner + Clinger: prices +3.7, `1.5e3` shapes +9.6; 17-digit shapes +16 (scan only, undecided); + the exact accumulator +2.2 more |
| the exact slow path through `std.json` | 1,017 ns (prices), 1,038 (exponent shapes), 1,557 (17 digits) a cell |
| cell readers, differential | 200,041 cells (the 44 edge cells, grammar fuzz, decimals around 10^18, floats at the Clinger limits, 17-digit doubles) x `:dec` at scales 0, 2, 5, 18 and float: 0 mismatches with `numbers_ref.py` after the order of checks was fixed (the differential found a width-before-grammar order in the first draft); 84,917 cells decided by Clinger's path are bit-exact |
| cell readers, mutants | 5 (mantissa bound, wrong operator, no padding check, scale check removed, `-0` kept): 5 killed (the fifth only after the check was made to demand `+0` bits: **a check that allowed `-0` could not see it**) |
| `table` and the others on a numeric file | section 2 |

## 10. G. Stages

Each stage has a pass line and ends with every earlier gate still green (G6 above all). Nothing merges with a red gate; none of this is in the tool yet.

| stage | scope | pass line |
|---|---|---|
| **N0** (before code) | this document accepted; the gates committed as failing tests (the reference, the SPEC, the parallel file, the mutant list) | the tests exist and fail for the right reason |
| **N0p** (decided after the design) | `:int`'s sum becomes the pair sum; `agg.sum-overflow` and the `peak` rule are removed; docs and generated pages corrected in place | `scripts/corpus.py` md5 identical for every existing plan except those that used to refuse for a sum; G6 (no int path slower than +2%); the sum tests rewritten from refusal to the exact wide number, against Python's `int` |
| **N1** | `:dec(S)` parse, compare, `in`, literals, in `--where`; the rules `value.not-decimal`, `value.decimal-scale`, `value.decimal-too-wide`, `column.type-conflict`; `where.syntax` additions | G1 (dec), G2 (where plans), G3, G4 (filters), G6, G7 (filter), G11, G5 for those sites |
| **N2** | `sum`, `min`, `max`, `mean[@N]`, `distinct`, count of `:dec(S)`; the pair sum and its printer; group by a `:dec` key (text-equal and numeric) | G2 (groups), G4 (sums at the carry), G7, G5, 3,000-case `dec_mean` against `Decimal`; `agg.bad-spec` for `mean` without `@N` |
| **N3a** | `:float` parse (scanner, Clinger, exact slow path through `std.json`), compare, `in`, literals, `min`, `max`, count; refusals `value.not-float`, `value.not-finite`, `value.float-range`, `limit.number-too-long`; the printer | G1 (float), G2, G3, G8 (filter; sum waits for N4); G9 reported; 10,000,000 cells bit-exact against Python's `float()` |
| **N3b** | Eisel-Lemire (or the upstream tape-free reader if it is enough) | G9: not slower than DuckDB at one thread; the same 10,000,000 cells bit-exact |
| **N4** | exact `sum` and `mean` of `:float`, the merge, `agg.float-overflow`, state accounting | G4 (cancellation files, plain-add mutant killed), G8, G12, the 502-case spike check ported as a conformance test, `rep_check` as a test with the carry threshold lowerable by a test-only constant |
| **N5** | typed keys: group by `:dec` and `:float`, distinct, `--sort` on typed aggregates, and the typed sort key for `--sort-rows` (the row sort lands first; this is the one function it calls) | G2, G4; the key order checked against Python's sort on 1,000,000 values |
| **N6** | `--report types` | the report against a Python classifier over the generated tables; parallel: the same bytes for every N; the first-offender row as in G1 |
| **out** | locale formats (`1.234,56`), thousands separators, trimming, hex floats, `f32`, a typed `--select`, dates, a global `--types`, arithmetic in expressions, an `or`, per-type NULLs, rounding on input (`:dec(2,round)`) |  not designed; each would be a decision of its own |

### What was built: N0, N0p and N1 (measured)

Built in this order, one commit each: the decisions recorded; **N0p**; **N0** (the gates of N1 as tests, failing); **N1**. Mac numbers are Apple silicon, the
Mac compiler (`db7d5bc`), minimum of 7 to 21 interleaved runs; Linux numbers are x86-64 with the pinned compiler (`f8ebe98`), cores 0 to 5 niced, a soak running,
so absolute times there are noisier than the ratios.

**N0p, the pair sum for `:int`.** `agg.sum-overflow` and the `peak` rule are gone; `track` is removed from the engine, the scan and the merge. A sum is the low 32
bits of every cell in the aggregate's value slot and `v >> 32` in the slot the `peak` used (so the state did not grow); `agg.settle` carries once before the sort and
the write; `agg.put_sum` prints up to 28 digits. Pass lines: **the md5 of all 134 plans of `scripts/corpus.py` (sequential and with threads and tiny ranges) is identical
before and after** (no existing plan overflowed); the sum tests were rewritten from refusal to the exact wide number against Python's `int` (15 edge lists, 2 to 1,000
cells, `-2^63` times 999, 5*10^18 times 200 for the zeros of the nine-digit groups); G6 on the Mac, 14 cells at 1 and 4 threads: worst ratio 1.017, the sums themselves
0.98 to 0.99 (the pair add is not slower than the checked add: the spike measured 0.44 against 0.82 ns). 12 new mutants (a bit lost in either half, a missing carry, the
merge dropping the high half, the sort looking at the low half only, a negative magnitude one too large, a missing zero padding, the narrow path taken too long), all killed;
the nine-digit padding survived the first test and got the 5*10^18 lists.

**N0, the gates.** `tests/conformance/numbers_ref.py` (the decimal semantics in Python's exact `int` arithmetic, and the 44 edge cells of `SPEC`), `test_numbers.py`
(19 tests, 14 s), `refimpl.py` and `test_rules.py` extended, `test_memory.py` extended. Run before the code, they failed for the right reason (no `:dec`).

**N1, `:dec(S)` in `--where`.** `query.parse_dec` (the reader, the order of the checks), `expr` (the suffix, the literals, `holds_dec`: the decimal verdicts are a callee of the
condition test, not a case in the read loop, after `docs/sort.md`'s lesson), `engine.screen`, `frame` (the type conflict, found before a row is read), `table` (the four rules,
the repair). Pass lines:

| gate | what ran | result |
|---|---|---|
| G1 | the 44 edge cells at scales 0, 2 and 18, one process each: the value, or the rule with row 1, line 2, column, context, the cell, `scale`, and for `value.decimal-scale` the exact `digits` | 0 differences |
| G1 | every operator against every literal over 16 cells, `in` lists, equality by value (`1.5`, `1.50`, `1.500`, `01.5`), the literal grammar and its 18 syntax errors with their byte offsets, names quoted, escaped and positional | pass |
| G2 | 1,500 generated tables and plans (up to three conditions of the three kinds, `in`, select, groups, aggregates, sort, json and csv) through `refimpl`, which reads `:dec` through `numbers_ref` | 0 differences, and at least 100 of them refusals |
| G3 | 400 fuzzed files (grammar-fuzzed cells, decimals around 10^18, scales 0 to 18) and the non-UTF-8 cells | no trap; the reference's verdict, row and rule on each |
| G4 | decimal filters, refusals in two ranges (the first in file order), ragged rows and quoted newlines, N in {2, 3, 4, 8, 16, 64} x chunks {1, 7, 64, 1000} | the sequential bytes in every one |
| G5 | `scripts/numbers_mutants.py`: 31 mutants (the cell reader, the suffix, the literals, the verdicts, the conflict, what a refusal says) | all killed; one more (`if kind == 2 \|\| op == 0` as `if op == 0`) was **equivalent** (an `in` has op 0) and was replaced by a real one; `--check` passes and is in `test_mutants_apply` |
| G6 | `scripts/gate_regress.py`, 7 cells x {1, 4} threads, outputs byte-identical first | Mac: worst 1.018 (a first pass said 1.027 on a 17 ms cell, then 0.97: the script now measures a cell over the limit again, three times as long, and believes the lower ratio); Linux: worst 1.011 |
| G7 | `scripts/bench_numbers.py`: the same question on `bytes:int` and on `price:dec(2)`, count and rows | Mac dec/int: count 1.008 (1 thread), 1.055 (16); rows 1.097, 1.047. Linux: count 0.998, 1.025 (6); rows 1.078, 1.059. Limit 1.15: **pass**; the spike predicted 1.02 to 1.06 |
| G10 | the same on DuckDB DECIMAL(18,2) and DOUBLE, csvtk, Miller (below) | answers checked against Python's first |
| G11 | the four rules in `test_rules.py` with a fixture, exit code, repairability and summary | pass |
| G12 | `test_memory.py`: decimal filters at 2 MB and 37 MB of file | flat |

The whole suite is 155 tests, 100 s. **Against the others** (G10; seconds; 1,000,000 rows, 56 MB; `price` two decimals; the filter is `status=404 and price >= 500.00`):

| | `table` int | `table` dec | DuckDB DECIMAL(18,2) | DuckDB DOUBLE | csvtk `-j 1` | Miller |
|---|---:|---:|---:|---:|---:|---:|
| Mac, count, 1 thread | 0.0697 | 0.0703 | 0.186 (2.65x) | 0.189 (2.70x) | 3.00 (43x) | not installed |
| Mac, count, 16 threads | 0.0113 | 0.0120 | 0.077 (6.4x) | 0.077 (6.4x) | | |
| Mac, rows as `id,price` csv, 1 thread | 0.0581 | 0.0637 | 0.206 (3.23x) | 0.213 (3.35x) | 2.96 (46x) | not installed |
| Mac, rows, 16 threads | 0.0123 | 0.0129 | 0.082 (6.4x) | 0.083 (6.4x) | | |
| Linux, count, 1 thread | 0.1529 | 0.1526 | not installed | not installed | 8.20 (54x) | not measured |
| Linux, count, 6 threads | 0.0633 | 0.0649 | | | | |
| Linux, rows, 1 thread | 0.1130 | 0.1218 | | | 8.31 (68x) | 0.469 (3.85x) |
| Linux, rows, 6 threads | 0.0680 | 0.0720 | | | | |

(The factor is the contender's time over `table`'s decimal time at the same thread count; csvtk's `filter2` has an expression engine and is slow here, and has no thread
scaling; DuckDB reads a decimal as fast as an integer, as `docs/numbers.md` section 2 found.) DuckDB's `DOUBLE` and `DECIMAL` answers were both compared with Python's exact one
as `Decimal`: the two print `500.5` and `500.50` for the same cell, which is why the check is numeric.

**Deviations from the stage table.** (1) N0p was added by the maintainer's decision; the doc's stage list now has it. (2) N1 as written in the table (decimal parse, compare,
`in`, literals in `--where`; the four rules; the `where.syntax` additions) is what was built, and **nothing else**: there is no decimal output yet (`mean`, typed `sum`, `min`,
`max`, group keys and `distinct` by value are N2 and N5, and `--order-by x:dec(2)` is not a key yet, so it says `column.unknown` for the name `x:dec(2)`; N5 gives it meaning).
(3) Two additions the stage did not list: `column.type-conflict` counts `--order-by`'s `:int` keys too, and `value.decimal-scale` carries `digits` and a `choose` repair that is the whole invocation
with the offending condition's scale rewritten. (4) The `value.not-decimal` repair is none: it says an exponent is a float, which is stage N3. (5) `scripts/gate_regress.py`
re-measures a cell before failing it (above). (6) DuckDB is not installed on the Linux box, Miller not on the Mac: those cells are blank, not estimated. (7) The mutation script is in `scripts/` and
uses `mutlib`, as asked; it was run in a second worktree so that the Mac stayed free for the timings.

**What the README and the page should now say** (they are not edited here): `:dec(S)` in `--where` (what it is, never rounded, equal as numbers, the three rules and the conflict), the sum
is exact at any width and no longer refused past 64 bits (README line "Sums are exact whole numbers; one that would not fit in 64 bits is refused" and the same sentence in the page, and the
`agg.sum-overflow` row of the page's rule table, which is generated: `scripts/site.py` regenerates it, and `docs/refusals.md` was regenerated in this branch), the
decimal filter numbers above.

### What was built: N2, the aggregates of a decimal column (measured)

`sum`, `min`, `max`, `mean[@N]`, `distinct` and `count` over `:dec(S)` columns, written at the column's scale; branch `numbers-n2`, tests first (commit `N2 gates`:
1,217 failures against `main`), then the code.

**The grammar.** An item of `--agg` may end in `:int` or `:dec(S)` and, for `mean`, in `@N`: `sum:price:dec(2)`, `min:price:dec(2)`, `mean:price:dec(2)@4`, `mean:n@2`, `distinct:price:dec(2)`.
An untyped `sum`, `min`, `max` or `mean` reads `:int`, as before; an untyped `distinct` reads text, as before. A mean of a decimal column defaults to the column's own scale; an integer
column has none, so `mean:n` is `agg.bad-spec` (the repair is to say `@2`). **A suffix is a suffix only when a column name is left before it**: `sum:int` still sums a column called `int`, and `sum:x:int`
is the integer sum of `x`. **A column really called `x:dec(2)`, `x:int` or `x@2` is written with a backslash before the colon or the at sign**: `sum:x\:dec(2)`, `sum:x\:int`, `mean:x\@2@3` (`\:` and
`\@` were `args.bad-value` before; the other escapes of the list are unchanged). Without the backslash `sum:x:dec(2)` is the column `x` read as a decimal, and `column.unknown` if there is none.
`@N` is 0 to 18; a scale past 18 and `count:dec(2)`, `sum:x:dec(2)@2` and the like are `agg.bad-spec`. In the plan an aggregate is four numbers (function, name, type, mean); a mean is a sum that is
written divided by the group's count, so the engine adds it as one.

**How the exact sum is held and printed at scale S.** The same pair as N0p: the low 32 bits of the scaled cell (below 10^18) in the value slot and `v >> 32` in the second one, exact to 2^93, merged by threads
as a plain add, settled once (`agg.settle`) before the sort and the write. `dec.put_sum` takes the pair, builds the magnitude as three limbs of 32 bits, divides by 10^9 until nothing is left to get the digits,
pads to `S + 1` digits and puts the point `S` from the right: exactly `S` fractional digits, no exponent, `-` only for a value that is not zero (`-0.05`, never `-0.00`); a minimum or maximum is written the same way
from its `int`. As JSON it is a string.

**The mean** (`dec.put_mean`): the magnitude of the exact sum is doubled, multiplied by 10^(N - S) (when N is the larger) in steps of at most 10^9, divided by the count (below 2^30) in limbs, and, when N is the smaller,
divided by 10^(S - N) afterwards; T = floor(2 * |mean|), and a flag says whether any division left a remainder. T halved is the integer part; the low bit of T says the fraction is half or more, and then it rounds
up when something was left over (more than half) and to the even integer when nothing was (exactly half): **half to even from the exact quotient, no float, the same for any thread count and any N**. The sign is put
back, and no `-` before a zero. `--sort mean:COL` orders by the exact quotient, by cross multiplication of the sums and counts in limbs, without dividing (a mean sorted by its rounded text would be
a different, coarser order); ties are broken by the group key as always.

**Refusals.** The cell of an aggregate is refused as `--where`'s is: `value.not-decimal`, `value.decimal-scale` (detail `digits` and `scale`), `value.decimal-too-wide`, with `context` the function (`sum`, `min`, `max`,
`mean`, `distinct`), the row, line, column and cell, the first in file order for every thread count. The repair of a scale refusal in `--agg` is none (it says to write `:dec(N)` with N at least the digits shown): the
`--where` repair rewrites one condition by its offset, and an item of `--agg` has no offset to rewrite by. `column.type-conflict` now spans `--where`, `--agg` and `--order-by`: a column read as `:dec(2)` and as
`:int` (an untyped `sum:x` is `:int`), or as `:dec(2)` and `:dec(3)`, anywhere in the plan.

| gate | what ran | result |
|---|---|---|
| G1/G2 | every aggregate against Python's `Fraction` and `Decimal`: the mean at 26 hand-picked tie, sign and edge cases (also against `decimal.quantize(ROUND_HALF_EVEN)`), sums, minima and maxima past 64 bits at scales 0, 2 and 18 at the edges of the width (`999999999999999999` times 1,000, both signs, cancelling), `1.5` against `1.50` as one distinct value, sorts by sum, minimum and mean; **1,600 generated tables and plans** (up to three conditions, typed and untyped keys, five aggregates of every kind, sorts, csv and json, ragged rows, refusals) through a Python reference | 0 differences; 900+ answers and 80+ refusals in the sample |
| G3 | 300 fuzzed cells and specs (the grammar's alphabet, scales to 18, random `@N`) | no trap, a valid document every time |
| G4 | decimal aggregates (count, sum, min, max, mean at three scales, distinct, sort, top), sums that need the carry, and the first refusal in file order, N in {2, 3, 4, 8, 16, 64} x chunks {1, 7, 64, 1000} | the sequential bytes in every one |
| G5 | `scripts/numbers_mutants.py` has 67 mutants now (37 new for N2, among them each rounding rule of the mean, the sign of a negative mean and of a mean that rounds to zero, the point one digit off, the zero padding, the carry of a negative sum, the comparison of means, `@N` and the grammar of the tail, the cached cell, and what a refusal says); one was **equivalent** (a sum written `-` when it is zero: a negative high half is at most -1, so there is no zero to write; the code lost the flag) and a second was replaced by a real one | all killed; the old `filter` and `cellcost` mutants whose sites moved were re-pointed (47 and 44 mutants), the ones on the changed code re-run and killed; `--check` passes for all seven scripts and is in CI |
| G6 | `scripts/gate_regress.py`, 7 cells x {1, 4} threads, outputs byte-identical first, md5 of all 134 plans of `scripts/corpus.py` identical | Mac: **pass**, worst 1.018; `group count,sum,min,max` **0.90**. Linux: **within build-to-build noise** (below); the gate is redefined against that noise |
| G7 | `scripts/bench_numbers_agg.py`: the same grouping on `cents:int` (as many digits as the price) and on `price:dec(2)`, by `status`, 1,000,000 rows | Mac dec/int: sum 1.036 (1 thread) and 1.083 (16), mean 1.063 and 1.114, min and max 1.011 and 1.008, all five 1.010 and 1.086. Linux: 0.99 to 1.07 at one thread, 1.07 at six, worst 1.087. Limit 1.15: **pass** |
| G10 | the same against DuckDB, csvtk, Miller, below | |
| G12 | `test_memory.py`: typed aggregates at 2 MB and 37 MB of file | flat |

The whole suite is 171 tests, 125 s.

**A speed-up that came with it.** A row asks each aggregate to read its cell; `sum, min, max, mean` of one column read it four times. The in-place way now reads the cell once for the aggregates that follow on the
same column (a column has one numeric type in a plan, so the type need not be compared). `count,sum,min,max` of the integer file is **0.90x of `main`** on the Mac (0.0687 to 0.0620 s) and 0.84 to 0.89x on Linux, and
it is what keeps the decimal ratio under the limit: without it the five-aggregate cell was 1.185 to 1.227 of the integer one on Linux (a decimal cell is slower to read than an integer cell by about 8 ns, and it was read four times).

**G6, said plainly.** Mac: passes, worst 1.018. Linux: **within build-to-build noise.** A quiet box (no soak; load 0.2 to 2 from containers) was used, and four builds were made with the same compiler and flags
(`main` twice and `numbers-n2` twice, each in its own directory), 21 interleaved runs per cell, on cores 0 to 5 and on cores 0, 2, 4 (one thread per physical core). Identical sources built in different directories differ by up to
**+2.3% at one thread and by 5 to 15% with more threads** (worst cell 1.148), so a 2% bound on the minimum of 21 runs is not decidable on this box, for any pair of binaries. Main against `numbers-n2`: cells that do not touch
the aggregates (filter, `cut`, text filter) at 0.98 to 1.03 at one thread, inside that noise. **One persistent signal**: single-thread group-sum, **+1.9% to +2.6% on three main-against-n2 comparisons** (the identical-source pairs gave
0.97 to 1.01 on it, one other pair 1.015): possibly a real +2% on a one-aggregate sum, at the edge of what can be seen; it was not separated from layout. `count,sum,min,max` is **0.86 to 0.89x** in every run, a real gain. An
earlier measurement on the loaded box (a soak running) had worst ratios 1.021 to 1.047; a variant without the cached cell showed the same one-sum signal, so it is not the cache.

**G6 redefined for the stages that follow**, so that it can fail meaningfully on hardware like this: a cell passes when `ratio(new / base) <= max(1, ratio(base2 / base)) + 0.01`, where `base2` is the old sources built again in another
directory, over at least 21 interleaved runs, outputs byte-identical. `scripts/gate_regress.py --base B --base2 B2 --new N` implements it (it prints both ratios and the medians for every cell; without `--base2` it keeps the old
fixed bound for a quiet machine) and `tests/conformance/test_gate_regress.py` shows it can fail (with the clock replaced by a table of times: equal builds pass, 2% over identical builds fails, a layout effect as large as the change
passes, more than the noise plus 1% fails, faster passes, differing outputs are refused). The Mac numbers above stand as measured.

**Against the others** (G10; the grouping by `status` of 1,000,000 rows; seconds; the factor is the contender's time over `table`'s decimal time at the same thread count; every answer was compared with Python's exact decimals first, `table` and
DuckDB's DECIMAL digit for digit, the double contenders with the largest error printed):

| | `table` int | `table` dec | DuckDB DECIMAL(18,2) | DuckDB DOUBLE | csvtk `-j 1` | Miller |
|---|---:|---:|---:|---:|---:|---:|
| Mac, sum, 1 thread | 0.0824 | 0.0853 | 0.217 (2.55x) | 0.217 (2.54x) | 0.468 (5.5x) | not installed |
| Mac, sum, 16 threads | 0.0128 | 0.0139 | 0.083 (5.95x) | 0.083 (5.94x) | | |
| Mac, mean, 1 thread | 0.0801 | 0.0852 | 0.215 (2.52x) | 0.214 (2.52x) | | |
| Mac, min and max, 1 thread | 0.0861 | 0.0871 | 0.211 (2.42x) | 0.216 (2.48x) | | |
| Mac, count, sum, min, max, mean, 1 thread | 0.0917 | 0.0926 | 0.218 (2.36x) | 0.220 (2.37x) | 0.483 (5.2x) | |
| Mac, the same, 16 threads | 0.0140 | 0.0152 | 0.082 (5.39x) | 0.082 (5.42x) | | |
| Linux, sum, 1 thread | 0.1261 | 0.1250 | not installed | not installed | 0.909 (7.3x) | 0.434 (3.5x) |
| Linux, the five, 1 thread | 0.1434 | 0.1392 | | | 0.933 (6.7x) | 0.701 (5.0x) |
| Linux, the five, 6 threads | 0.0531 | 0.0577 | | | | |

What the answers said, which is the point of the exact types: DuckDB's DECIMAL sums, minima and maxima equal Python's; its DOUBLE sum was off by **0.000375** at one thread and by **0.00009** at sixteen (a different wrong
answer for a different thread count, as the design found); csvtk's was off by 0.0004 and Miller's by 0.000375. `table`'s answer is the same exact decimal at every thread count. DuckDB's `AVG` of a decimal is a
double (compared here to 1e-4); `table`'s mean is the exact quotient rounded half to even at the scale asked. (The two floats' errors are small in absolute terms on a file of 0.01-steps; they grow with the sum.)

**Deviations from the stage table.** (1) The repair of `value.decimal-scale` for an aggregate is none, not a `choose` (above). (2) `mean` over an integer column (`:int`, untyped) is in N2 too, with `@N` required:
the design put `mean:COL[@N]` in 3.6 for both, and the grammar is one. (3) `distinct` accepts `:int` as well (by value: `+5` and `5` are one), which the stage table did not list. (4) `\:` and `\@` are new escapes of the
`--agg` list. (5) The int cell the decimal one is compared with in G7 is now `cents`, not `bytes`: `bytes` has 5 digits, the price 8 characters, and half of the first ratios were the longer cell; `scripts/bench_numbers.py`
(N1) uses it too (its numbers above were with `bytes`: N1's ratios were 1.0 to 1.1 and still pass). (6) G6 on Linux is within build-to-build noise, and G6 is redefined (above). (7) Group keys of a decimal and `--order-by x:dec(2)` are still N5.

**What the README and the page should now say** (not edited here): the aggregates line (`count`, `sum`, `min`, `max`, `mean`, `distinct`; typed `price:dec(2)`; `mean@N`; exact, any width, half to even), the
`:dec(S)` bullet extended to aggregates, one table of the aggregate numbers above, and `python3 scripts/site.py` for the one generated region that moved: the rules table (the `agg.bad-spec` sentence now names
`mean` and the suffixes).

### What was built: N3a, `:float` in `--where` and min, max, count, distinct of a float column (measured)

Branch `numbers-n3a`; tests first (commit `N3a gates`: 1,113 failures and 106 errors against `main`), then the code. The sources are `.cho`, the compiler is the pinned `cancho` a4572ea (no newer one was needed).

**What it reads and writes** (`tools/table/flt.cho`). `x:float` is `[+-]? digits? [. digits?] ([eE] [+-]? digits)?` with at least one mantissa digit, ASCII only, at most 1,100 bytes; the value is the nearest double (ties to even),
and **no `inf`, no `nan`, no negative zero** (`-0`, `0e999` are `0`); a cell that overflows or that has a non-zero digit and would read as zero is refused. A comparison is of the doubles, never of the decimal text: `x:float = 0.1` matches `0.1`, `0.10`,
`1e-1`, `1E-1`, `+.1` and `0.10000000000000001` (the same double), not `0.10000000000000002`. A double is kept as an `int` whose signed order is the numeric order (its 64 bits with the low 63 flipped when the sign bit is set), so
every comparison, `min`, `max` and the distinct set are integer operations and the engine and the thread merge did not change. It is written as the shortest decimal that reads back to the same double, positional from 1e-6 up to 1e21 and
always with a point (`0.1`, `100.0`, `12345.67`, `1e21`, `1.5e-7`; zero is `0.0`). In a plan: `--where x:float >= 1.5` (and `in`, literals read the same way: a literal that is `nan`, `inf`, out of range or not a number is a
`where.syntax` error at its offset), `min:x:float`, `max:x:float`, `distinct:x:float` (by value: `1.5`, `1.50` and `15e-1` are one), `count`; `--sort` by a float minimum or maximum is numeric. `sum:x:float` and `mean:x:float` are `agg.bad-spec` until N4.
`column.type-conflict` counts `:float` too (`:float` beside `:int` or `:dec` for one column). A header really called `x:float` is written `x\:float` (as `x\:dec(2)`). Rules, all exit 8, with `context`, row, line, column and cell, the first in file order
for every thread count: `value.not-float`, `value.not-finite` (the repair is the idiom `x != 'NaN' and x:float > 5`), `value.float-range` (`detail.direction` is `overflow` or `underflow`), `limit.number-too-long`.

**The three ways to a double.** (1) Clinger's fast path, inline: at most 15 digits and a power of ten within 10^-22..10^22 are two doubles, so one multiplication or division rounds once. (2) `std.json`'s exact reader for everything else, fed the cell rewritten
in the grammar of JSON (digits without leading zeros, `e`, the exponent): correct, and about a microsecond a cell. (3) **An exact reader of its own for anything below the smallest normal double**, added because the differential tests found that
**`std.json.to_float` rounds about one in five of the cells that sit exactly on the midpoint between two subnormals, a hair above it or a hair below it, to the wrong double** (321 of 1,476 such cells; none of the 6,000 hard cells above 2^-1022 and none
of the 6,000 random ones; reported upstream below). The own reader does the integer round(value x 2^1074) from the digits in 32-bit limbs (`dec.mul_small` and `dec.div_small`, the helpers of the mean), half to even by the same floor-and-remainder
rule as the mean; it runs only when the answer from `std.json` is below 2.3e-308, so the common cost is unchanged.

| gate | what ran | result |
|---|---|---|
| G1 | the 44 edge cells of `SPEC` (floats), the cell of 1,100 and of 1,101 bytes, every operator against 25 cells x 17 literals as Python floats, equality of doubles not of text, no negative zero, the cells that are not numbers (`nan`, `-inf`, `+Infinity`, `1e999`, `1e-999`, `0x1p3`, `infx`, `nano`...) | 0 differences |
| G1 | **640 hard cells** (the exact decimal midpoint between two doubles, a hair above and a hair below it, at every exponent from 2^-1074 to 2^1023) and 6,000 cells of every shape (17-digit random doubles, two decimals, exponent forms, integers past 2^53, long mantissas with an exponent, huge and tiny): read as Python reads them and written as Python writes them | 0 differences in the value; the text is the design's layout, and **where two shortest strings are equally close (`...86.125` is `...86.12` in Python and `...86.13` in `std.fmt.float_into`) either is accepted**: both read back to the double, and the rule of 4.6 is "shortest that reads back" |
| bulk | `scripts/float_bulk.py`: **10,000,000 cells** (all those shapes, midpoints, subnormals) against Python on the count, the minimum, the maximum and the number of rows at or below 12 thresholds taken from the data and their neighbours one ulp away; and 2,000,000 on the number of distinct doubles | **0 differences** |
| G2 | **1,600 generated tables and plans** (float conditions with `in`, text conditions, select, groups of min, max, count, distinct, sorts, csv and json, ragged rows, refusals with their direction) through `refimpl`, which reads `:float` through `numbers_ref` | 0 differences; 900+ answers, 80+ refusals |
| G3 | 500 fuzzed files (the grammar's alphabet plus `i n f a`, words, huge exponents) and non-UTF-8 cells | no trap; the reference's verdict, row and rule on each |
| G4 | float filters, min/max/distinct, sorts, refusals in two ranges (the first in file order), ragged rows and quoted newlines, N in {2, 3, 4, 8, 16, 64} x chunks {1, 7, 64, 1000} | the sequential bytes in every one |
| G5 | `scripts/float_mutants.py`, **38 mutants** (the key and its inverse, the hidden bit and the bias, `nan`/case folding, the fast-path limits, the dropped digits, overflow and underflow, the subnormal reader's rounding and its power of two, the layout thresholds, the point, the suffix and its escape, the abort codes, the direction, sum refused) | all killed; one was **equivalent** (`key_of` answering 0 for a zero: it is only called for a non-zero double, a zero cell is answered as key 0 before it) and the branch was removed; two survived the first round and got a test each (the escaped suffix in `--where`, the long way for `0e999`); `--check` is in CI and `test_mutants_apply` |
| G6 | the redefined gate (`gate_regress.py --base --base2 --new`, below) | **not passed by the letter, no regression found**: see below |
| G8 | `scripts/bench_numbers_float.py`, `price:float` (two decimals: the fast path) against `cents:int` (as many digits), count and rows, 1,000,000 rows | Mac 0.94 to 1.05; Linux 0.98 to 1.06 (limit 1.25): **pass** |
| G9 | `ratio:float` (a double in shortest form, 15 to 17 digits: the exact reader) | **N3a has no pass line, and the number is this**: the filter only reads the cells of the rows that pass its first condition and is at 1.2x of DuckDB's time at one thread on the Mac (0.243 against 0.198 s); **min and max read every cell: 1.09 s against DuckDB's 0.208 (5.2x)**. This is the gap N3b exists for |
| G11/G12 | the four rules with fixtures, exit code and summary in `test_rules`; `test_memory`: float filters and aggregates at 2 MB and 37 MB | pass; flat |

The corpus md5 of the 134 existing plans is identical, and the whole suite passes (Mac and Linux).

**Against the others** (G10; 1,000,000 rows, 56 MB, the filter `status=404 and value >= 500` unless said; seconds; the factor is the contender over `table`'s `:float` time at the same thread count; every answer was checked against Python's floats first: the count,
the ids and the values of the rows, the minimum and maximum equal as doubles):

| | `table` price:float | DuckDB DOUBLE | csvtk `-j 1` | Miller |
|---|---:|---:|---:|---:|
| Mac, count, 1 thread / 16 | 0.0674 / 0.0112 | 0.195 (2.9x) / 0.075 (6.7x) | | not installed |
| Mac, rows, 1 / 16 | 0.0630 / 0.0116 | 0.221 (3.5x) / 0.079 (6.8x) | 2.70 (43x) | |
| Mac, min and max by status, 1 / 16 | 0.0804 / 0.0124 | 0.202 (2.5x) / 0.076 (6.2x) | | |
| Linux, rows, 1 thread | 0.1327 | not installed | 6.89 (52x) | 0.391 (2.95x) |
| Mac, ratio:float rows, 1 thread | 0.222 | 0.216 (0.97x) | 2.75 (12x) | |
| Mac, ratio:float min and max, 1 / 16 | 1.089 / **2.154** | 0.208 (0.19x) / 0.079 | | |
| Linux, ratio:float rows, 1 thread | 0.285 | | 6.17 (22x) | 0.368 (1.29x) |
| Linux, ratio:float min and max, 1 / 6 | 1.069 / 0.505 | | | |

**Negative scaling of the exact reader on macOS.** The 17-digit minimum and maximum is **slower with threads on the Mac** (user time grows, system time grows much faster: 1 thread 0.17 s, 16 threads 25 s of `sys`): `std.json`'s reader allocates a few regions a cell, and the Mac's `malloc` does not
scale. On Linux it does (1.07 s at one thread, 0.505 at six, and `strace -c` shows no syscall storm). The fix is a reader that does not allocate (N3b); until then a float column of 15 to 17 digits should not be read with `--threads` on macOS.

**G6 in practice.** The redefined gate (the new binary against `main` built twice from identical sources in two directories, 21 and 41 interleaved runs, outputs identical) did not pass by the letter anywhere, and **found nothing real**. Mac: two runs, two and then
one cell over its bound by 0.1 to 0.3% (a different cell each time; the medians 0.99 to 1.01). Linux on a quiet box, cores 0 to 5: three one-thread cells over by 0.2 to 1.0% that do not touch the new code (`cut`, group-count, text filter), the identical-source pair at 1.00;
cores 0, 2, 4: a handful more, the worst ratio 1.039 (its bound 1.023). **A second build of the same new sources (a different directory) gave one-thread ratios of 1.003 to 1.017 where the first gave 1.00 to 1.04**: the layout lottery of the new builds is as large as the signal, so one
base pair underestimates the noise. The gate needs the noise from at least two pairs (`max` over them) or a median criterion before it can fail only for a real regression; I propose that for the stages after this one (the script still takes one `--base2`).

**G6, revised (second revision, after N3a).** The first redefinition (one extra build of the old sources, `--base2`) judged N3a and did not pass anywhere by the letter while finding nothing real. The reason was in the numbers: **a second build of the same new sources gave one-thread ratios of 1.003 to 1.017 where the first gave 1.00 to 1.04, on identical sources.** One base pair therefore underestimates the build-to-build layout noise. The gate is now: the noise bound is the **maximum of ratio(base_i / base) over at least two extra builds** (`--base2 --base3`; one alone is refused by the script), and a cell must also have **median(new) / median(base) <= 1.01** over the >= 21 interleaved runs. `tests/conformance/test_gate_regress.py` shows it can fail on either criterion (clock replaced by a table of times) and that a single extra build is refused. `--cell TEXT` re-measures one cell with more `--runs`.

*What "no regression" can mean here, said plainly.* On this hardware (an M-series Mac and a shared 16-core Linux box, cells of 15 to 100 ms) **a regression below the layout noise of the builds cannot be proven absent**, whatever the gate; the evidence that is left is (1) the medians on both machines, (2) outputs byte-identical for all of the binaries, and (3) the **control below**: the same gate applied to builds of identical sources.

**G6 for N3a under the revised gate** (`main` = b386815 built three times, N3a built once; the pinned compiler a4572ea built on gram in a scratch clone; load checked first: 0.6 to 1.5 on gram, nothing of mine running; niced; scratch removed after). **Not passed by the letter, on either machine.** What was measured:

| machine | run | cells over | what failed |
|---|---|---|---|
| Mac, 21 runs | 1 | 3 of 14 | group-sum 1 thread (min ratio 1.027, bound 1.024, median 1.022); count,sum,min,max 1 thread (min 0.997, median 1.027); sum of everything 1 thread (min 0.995, median 1.020) |
| Mac, 41 runs | 2 | 3 of 14 | filter 1 thread (min 1.023, bound 1.013, median 1.008); cut 1 thread (median 1.012); text filter 1 thread (median 1.027) |
| Mac, group-sum only, 61 runs x 3 | | 0 of 6 | min ratios 1.003, 0.992, 0.998 (1 thread), 1.001 to 1.013 (4 threads), medians 0.996 to 1.003: **the cell of run 1 did not reproduce** |
| Linux, cores 0-5, 21 runs | | 5 of 14 | **all on the median criterion** (filter 1t 1.026, cut 1t 1.016, cut 4t 1.029, count,sum,min,max 4t 1.014, sum of everything 1t 1.012); every min ratio is within its bound (worst 1.032 against 1.045) |
| Linux, cores 0, 2, 4, 21 runs | | 5 of 14 | filter 3t (min 1.024, bound 1.010), cut 1t (1.015 / 1.014), text filter 3t (1.028 / 1.013), group-count 1t and count,sum,min,max 1t (median only, 1.037 and 1.011) |

*The control* (the same gate, identical sources: `new` is one of the three old builds, the extras are the other two and a copy): Mac 2 of 2 runs failed, 2 cells each (cut 4t and text filter 4t over their bounds; cut 4t and sum of everything 4t over the median limit); Linux 3 runs, **3, 6 and 0 cells over**, the failures of the first two on cells that run no code of the stage (group-sum, filter 4t by 5.7% on min). So the gate **fails builds of identical sources about as often and as much as it fails N3a** (N3a: 3, 3, 0, 5, 5 cells of 14; identical sources: 2, 2, 3, 6, 0), a different cell each time, and the one cell that looked like N3a's (the one-thread group-sum on the Mac) did not reproduce with 61 runs. **I found no cell that fails reproducibly**, the N3a code does not touch the integer aggregate path except in `parse_aggs` and `tail_of` (a kind test per aggregate at plan time), and the outputs are byte-identical. The honest verdict: **the gate fails by the letter, N3a is not distinguishable from identical sources, and no regression was found**; I did not loosen the criteria again. What the gate would need to decide at this resolution is cells at least ten times longer (a 10M-row file, 0.5 s a cell) or counting the instructions of the process (`perf stat` on Linux) instead of the clock; neither is implemented.

**G6 for N3a: merged although the letter of the revised G6 failed.** The maintainer accepted N3a (PR #22) on the control evidence: the same gate applied to builds of IDENTICAL sources failed as often as it failed N3a. The numbers (cells over their limits, of 14): N3a 3 (Mac, 21 runs), 3 (Mac, 41 runs), 0 (Mac, group-sum alone, 61 runs x 3), 5 (Linux cores 0-5), 5 (Linux cores 0, 2, 4); identical sources 2 and 2 (Mac, two runs), 3, 6 and 0 (Linux, cores 0-5, three runs). A different cell each time, and the one cell that looked like N3a's (one-thread group-sum on the Mac) did not reproduce. So the gate cannot discriminate at this noise. What stands behind the merge: outputs byte-identical for every binary compared, the md5 of all 134 plans of `scripts/corpus.py` identical, the medians within +-3% on both machines. The third revision of the gate (below, N4) is the answer to that.

**What was found upstream** (cancho `std`, not edited): (1) `std.json.to_float` rounds some subnormal decimals wrong (above); (2) `std.fmt.float_into` is not unique in exact ties between two shortest strings; (3) the exact reader allocates per cell
(the macOS scaling above) and goes through a tape; (4) still missing: `float_of_bits` (the key is turned back into a double through `ldexp`, two loops over the exponent, for the min and max of each group at the end only).

**Deviations from the stage table.** (1) The own subnormal reader (3 above) is not in the design; it is the one place the design's "the slow path through `std.json`" was not enough. (2) `distinct:x:float` comes for free with the key and is in; the table
listed min, max and count. (3) A tie between two shortest decimals is not specified (above). (4) G9 has no pass line in N3a by the design, and it is what it is (above). (5) The redefined G6 is not passed by the letter (above), and the gate's own noise estimate is what the
numbers criticise. (6) `column.type-conflict` and the abort codes were extended, not redesigned (29 to 33 for `--where`, 37 to 41 for an aggregate). (7) `float_bulk.py` is a new script, not in the design.

**What the README and the page should now say** (not edited here): `:float` bullet and the aggregates line (min, max, distinct of floats; sum and mean in N4); the four new rules (the generated `rules` table of `docs/index.html` and the count in the README: `site.py --check` is
red on those until they are regenerated); the float filter benchmark above, with the 17-digit column's honest ratios and the macOS thread note.

**Next: N4, the exact float sum and mean** (section 4.4). What it needs from what N3a built: (1) the accumulator is 72 limbs of 32 bits plus a counter (73 `int`s, 584 bytes) per group per float sum or mean, so the group state stops being a fixed `1 + 2 x aggregates`
integers: the stride has to be computed per aggregate (`agg.cho`: `start`, `add`, `add_fast`, `serialize`, `merge`, `settle`, the sort), counted in `--max-state-bytes`; (2) the cell comes from `flt.parse_float` as a key, and the accumulator wants the sign, the biased exponent and the
mantissa: the key turns back into the bits by the same xor (`bits = key < 0 ? key ^ 0x7fff... : key`), no double is needed; (3) the carry every 2^27 additions, the merge (carry both, add limbwise, carry) and the single rounding at the end (`dec.cho` has the limb helpers and the half-even rule; the end builds a double from 53 bits and an exponent, then `flt.key_of` and `flt.put_float` write it);
(4) the mean divides the limbs by the count (`dec.div_small`, count below 2^30) with 64 fractional bits, and sorting by a float sum or mean compares the rounded doubles (a key per group made at settle) or the exact values; (5) `agg.float-overflow` for an exact sum past the largest double,
named by group; (6) the tests: cancellation columns that make plain addition order-dependent, `math.fsum` as the oracle, every thread count, and the mutant that adds as plain `f64` (it must die at N >= 2); (7) `--sort`, `--top` and the typed group keys are N5.

## 11. Open questions, assumptions, what the language lacks

**Decided by the maintainer** (the six questions of the first version of this document, answered as recommended):

1. Suffix per reference only; **no global `--types`** (5.1).
2. **`:int`'s `sum` moves to the pair sum**, deleting `agg.sum-overflow` and the `peak` rule (sums print as wide numbers); a stage of its own, **N0p**, before N1 (7, 10).
3. **`.5` and `5.` are accepted** (3.2).
4. **`-0` reads as `0`**; losing the sign of zero is acceptable and documented (4.1).
5. **NaN and `inf` are refused**, the filter idiom is the repair (4.1).
6. **The 584-byte float group state is accepted** for now, bounded by `--max-state-bytes` (4.4).

Nothing is left open in the design itself; what remains unknown is under "Not known" below.

**Assumptions** (each checked by a gate, none by a measurement yet).

* The 16 cores' scaling: the parallel cost of the exact accumulator (each thread's state is 584 bytes per group) was not measured; only the single-thread add and the merge were.
* `--max-rows`' ceiling of 10^9 is a hard bound on rows read for every plan (`docs/reference.md`); if a path reads more, 3.3's proof is void.
* The Linux x86-64 numbers: none were taken (a soak runs there). Integer arithmetic and the superaccumulator are target-independent by construction (`bits_of` canonicalises NaN, which is refused anyway), but the **timings** are Mac-only; G10 takes them.
* The fast path `m <= 2^53` and `abs(e10) <= 22` was checked bit-exact against Python's `float()` on 84,917 cells; the claim for 10,000,000 is a gate (N3a).
* The Mac compiler (`db7d5bc`) is not the pinned one (`f8ebe98`); `std.json`, `std.fmt.float_into`, `static` data and `index_of_byte` are in the pinned compiler's `std` (the JSON commit is an ancestor of the pin), and the spikes needed `edition 5`, as the tool is.

**What the language lacks** (each a possible cancho change; none was made, `cancho` was only read).

| gap | what it costs here | the ask |
|---|---|---|
| a tape-free correctly rounded `text -> float` (or `(m, e10)` -> float) | 1.0-1.6 microseconds per cell on the exact path (N3a) | `std.json.decimal_to_float` or `std.num.parse_float` over a slice, region-free |
| multiply-high (64x64 to 128), a carry flag, unsigned compare | Eisel-Lemire (N3b) has to build them from `wrapping_mul` and 32-bit pieces | `wide_mul`, or a 128-bit `int` |
| `float_of_bits` | the accumulator builds its result as `float_of(m) * 2^k` (`ldexp`: two `pow2` loops over the bits of the exponent; not timed on its own, once per group, inside the 2.5 microseconds of `finalize`); a float key decodes the same way | `float_of_bits` (it now has callers) |
| a public positional shortest-float text | the printer of 4.6 would copy `std.json`'s private `float_text` | export it from `std.fmt` |
| bignum divide | `mean` writes 16-bit-digit division in 20 lines | `std.bignum.divide_small` |
| atomics | no work queue; unchanged by this design (`docs/parallel.md` section 7) | |

**Not known, and said so.** The real cost of Eisel-Lemire in cancho (N3b). Whether the `std.json` tape or the arithmetic is most of the microsecond. The cost of the 584-byte state at 100,000 groups at 16 threads. Miller's answers on any cell (not installed). DuckDB's
behaviour on a cell under settings other than `read_csv` defaults and `try_cast` (it was measured under those). The Linux timings.
