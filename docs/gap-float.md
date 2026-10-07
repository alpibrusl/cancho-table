# `table` and the 15 to 17 digit float column: an exact reader that does not go through a bignum (design, spike and patch; stage N3b)

**Status: designed, spiked, and built as a patch on this branch (`tools/table/flt.cho`, `tools/table/pow5.cho`); not merged.** The gap is `docs/numbers.md` section 4.2 and
the "Floats" table of `docs/benchmarks.html`: a column of 17-digit doubles took 1.09 s to read with `min`/`max` (DuckDB: 0.208 s), and on macOS it got *slower* with threads.
Everything here that is called *measured* was measured by the files in `scripts/spikes/float/` (listed in section 11) with the compiler the tool is pinned to
(`a4572ea`, no change to `cancho` was needed or made). Numbers are the Apple M4 Max (16 cores, a Mac that other work was running on: load 6 to 15, so read ratios, not the last digit)
and `gram` (Linux x86-64, 16 cores, `taskset -c 0-6`, load under 2).

## 0. The result in one table

| | before (`origin/main`) | after (this branch) | DuckDB 1.5.6 DOUBLE |
|---|---:|---:|---:|
| one 17-digit cell, ns (Mac; the 8 ns of the line scan included) | 1,151 | **29.6** | |
| one 17-digit cell, ns (gram, the 6 ns of the line scan included) | 693 | **41.9** | |
| `min` + `max` + `sum` + `count` of `ratio` by `status`, 1M rows, 1 thread (Mac) | 1.227 s | **0.114 s** | 0.230 s |
| the same at 16 threads (Mac) | 2.093 s (*slower* with threads) | **0.016 s** | 0.085 s |
| the same, gram, 1 thread / 4 threads | 0.859 s / 0.286 s | **0.156 s / 0.054 s** | 0.202 s / 0.108 s |
| differential against Python `float()` | | **0 differences in 201,767,328 generated and adversarial cells on the Mac, 51,194,656 on gram, and 20,867,328 more with the exact tier forced**; the 640 cells of `test_float.py` | |

* **Cost of a cell.** 17-digit cells: 1,151 ns to 29.6 ns (39x); the money-like column of 16 to 17 digits, 2,898 to 32.6 ns (89x); random doubles at any exponent, 4,451 to 42.6 ns (104x); the
  two-decimal column (Clinger decides) is unchanged, 13.5 to 13.8 ns. The line scan alone is 8 ns, so the reader itself is **about 22 ns** on a 17-digit cell: 45 million cells a second on one core of the Mac.
* **The realistic ratio against DuckDB.** The float column is no longer the slow column. At one thread the whole question on the 17-digit column is **0.48 to 0.50 of DuckDB's time**
  (2.0x faster), at 16 threads 0.18 to 0.20 (5x faster). The old "5.7x to 24x slower" of `docs/numbers.md` section 10 (N4) is gone: **0.16x to 0.77x of DuckDB's time on every question measured, on both machines** (sections 5 and 7).
* **What it costs in the program.** One new 10 kB table (`pow5.cho`, generated), 330 lines in `flt.cho`, and `std.json` is no longer imported by the tool (the binary is 624 bytes smaller,
  the build is no slower). A cell that is not decided by the two fast tiers (adversarial only: section 6) costs 1.5 to 10 microseconds, 4 to 7 times *less* than today.
* **The threads.** No region and no allocation per cell on the paths that matter, so the macOS scaling is the Linux scaling: 0.114 s (1 thread) to 0.016 s (16) on the Mac,
  7.1x; before, 1.23 s to 2.09 s.

## 1. The gap, reproduced first

`python3 scripts/spikes/float/e2e.py` (the file of `scripts/bench_numbers.py`: 1,000,000 rows, `ratio` = `repr(uniform(0, 1000))`, 15 to 17 digits). `origin/main`, best of 5:

| question (by `status`) | threads | Mac `table` | Mac DuckDB | gram `table` | gram DuckDB |
|---|---:|---:|---:|---:|---:|
| min, max, sum, count | 1 | 1.227 | 0.230 | 0.859 | 0.202 |
| | 4 | 1.748 | 0.108 | 0.286 | 0.108 |
| | 16 (gram: 6) | 2.093 | 0.085 | 0.294 | 0.102 |
| min, max | 1 / 16 (gram: 1 / 6) | 1.221 / 2.115 | 0.219 / 0.084 | 0.860 / 0.284 | 0.200 / 0.099 |
| count where `ratio >= 500` | 1 | 0.268 | 0.213 | 0.240 | 0.195 |

That is `docs/benchmarks.html`'s 1.089 / 2.154 on the Mac (the page was measured on a quieter machine) and 1.069 / 0.505 on Linux (here 0.860 / 0.284 at 6 threads on a quieter box than the page's):
the same behaviour, both signs. The per-cell cost behind it, `fbench o` (`origin/main`'s `flt.cho` on a file of cells, the difference between 11 rounds and 1 so that reading the file drops out; best of 3):
1,151 ns for 17-digit cells, 2,898 for 16-17 digit money, 4,451 for random doubles; on gram 693, 1,539 and (not run). Clinger's fast path decides a cell of 15 digits or fewer (the two-decimal column: 13.5 ns); everything
else was the tape and the region of `std.json` (1.0 to 4.5 microseconds).

## 2. Design: three tiers, none of them allocates

```
cell ──scan──► (negative, m, nd, q, dropped, digits, point, exponent)      m: the first 19 significant digits, as an unsigned 64-bit pattern in an `int`
                │                                                          q: the power of ten of m's last digit; dropped: a non-zero digit after the 19th
                ├─ grammar / length / word refusals (9, 10, 13) ─────────  as today, in the same order
                ├─ all digits zero ──► key 0, status 0                     as today (-0 is 0)
                │
   tier 1  Clinger   m <= 2^53, not dropped, |q| <= 22:  float_of(m) * or / 10^|q|         one rounding, bits_of(x)                      (two-decimal cells, 15-16 digits)
   tier 2  Eisel-Lemire  eisel_lemire(q, m): the bits of the nearest double, no float built;  with dropped digits also eisel_lemire(q, m + 1):
                         the same bits both times ──► done                                   (17-19 digits, any exponent; 20+ digits mostly)
   tier 3  exact     exact_bits(cell): bignum, only when m and m + 1 round differently        (adversarial: 20+ digits within a rounding step of a halfway point)
                │
bits (the magnitude's exponent field and fraction) ──► status 11 past the range, 12 reads as zero, else the key: bits, or -1 - bits when negative
```

### 2.1 The window of 19 digits and the truncation rule

`m` takes the first 19 significant digits (leading zeros do not count: `0.000123` has three) with `wrapping_mul`/`wrapping_add`, so a 19-digit number up to 9,999,999,999,999,999,999 is the bit pattern of an
unsigned 64-bit number in an `int` (the checked `*` would trap above 2^63). Digits after the 19th are not accumulated: a digit before the point raises `q` by one (it is a factor of ten the window did not take), a digit after the
point changes nothing, and `dropped` records whether any of them was not `0` (`1.5000000000000000000000` is 1.5, not a truncated number: it never needs the `m + 1` test). `q` is the exponent, minus the fraction digits taken into `m`, plus the integer digits not taken,
and the exponent field is clipped at 100,000 as before, so `q` is at most 101,100 in magnitude and is only a number to compare with the table's range.

Why 19 digits: 10^19 < 2^64 < 10^20, so any 19-digit number is exact in 64 bits, which is what Eisel-Lemire needs; and the paper that closed the algorithm
(Mushtak and Lemire, *Fast number parsing without fallback*, 2023) proves that **with at most 19 digits the algorithm never fails to decide**: the multiplication by the 128-bit approximation of 5^q is always accurate enough
(the second, refining multiplication runs only when the 9 bits below the 55 kept are all ones), and the one case it cannot see through, a product that is *exactly* halfway between two doubles, is recognised
by `lo <= 1` for `q` in -4..23 (there 5^|q| fits and the product is exact) and rounded to even. For a cell with more than 19 significant digits a non-zero tail is a number strictly between `m` and `m + 1` in units of
10^q; rounding is monotone, so **if `m` and `m + 1` give the same double, the cell does too** (the older fast_float rule); if not, the tail decides and tier 3 does.
Nothing is ever "approximately" right: a cell is read by the tier that can prove its answer, and the differential (section 6) found no cell where the proof was wrong.

### 2.2 The multiply-high, from `wrapping_mul` and 32-bit limbs

cancho has no 64x64 to 128 multiply. `mul128(a, b)` (`flt.cho`) splits both into 32-bit halves, makes the four partial products with `wrapping_mul` (each below 2^64, so exact as a bit
pattern) and adds them with the carries by hand (`wrapping_add`, and a logical shift made of `>> 32` and a mask because `>>` is arithmetic):

```
p00 = a0*b0   u = a0*b1 + (p00 >> 32)   v = a1*b0 + (u & M)   hi = a1*b1 + (u >> 32) + (v >> 32)   lo = (v << 32) | (p00 & M)
```

Four multiplications and about a dozen other operations. Measured (`fbench M`, a chain in which each product feeds the next, so it is the latency): **2.35 ns on the Mac, 2.15 ns on gram**. Inside the
reader it is nothing like that: the multiplications overlap with the scan of the digits (`fbench m`, the same helper next to the line scan: 8.04 ns a cell against 7.92 for the scan alone), and the
second multiplication runs for one cell in 512. Unsigned compares (the carry test after the second product) are done by flipping the top bit of both sides. The count of leading zeros, which the algorithm needs to
normalise `m`, is a 6-step binary search (`clz64`: 5.8 ns of latency on the Mac, 4.8 on gram, branchy; a version through the exponent field of `float_of(m)` measured 0.2 to 0.9 ns *less* in the whole
reader, inside the noise, and was not kept because it is harder to see that it is right). **The language does not need `wide_mul` for this to be fast**; it would save lines, not nanoseconds
(`docs/numbers.md` section 11 lists it as a possible ask: this is the measurement that says it is not urgent).

### 2.3 The table of powers of five

Eisel-Lemire multiplies by a 128-bit number that stands for 5^q, q from -342 to 308: **651 entries, 1,302 words, 10,416 bytes of read-only data** (`static pow5_128`, built into the binary,
not at run time). Entry q is the pair (high word, low word) at index 2(q+342), 2(q+342)+1 of a number with its top bit set:

* q >= 0: 5^q shifted until it has 128 bits (exact up to q = 55, truncated, i.e. rounded *down*, beyond);
* q < 0: floor(2^b / 5^-q) + 1 with b = z + 127 (z the least number with 2^z >= 5^-q) for q >= -27, b = 2z + 128 below, shifted right to 128 bits: always an *upper* bound of the true value, by less than one unit of the last place.

It is Lemire's table (`fast_float`'s `script/table_generation.py`), regenerated here from Python integers: no floating point and no platform in the generation, so it is deterministic by construction.
`scripts/spikes/float/pow5_table.py` writes `tools/table/pow5.cho` and `--check` regenerates in memory and compares byte for byte (exit 1 when they differ); I spot-checked the entries I know by heart from fast_float's table (q = -342, -1, 28, 308), and the table is validated where it counts, by the 250 million cells of section 6, which cover every q from -342 to 308 (`bits`, `p10`, `exp` and `long` are made to).
**A first version of the generator got q in -27..-1 wrong** (it used the `b = 2z + 128` rule everywhere, which truncates away the "+1" of the upper bound): the differential caught it on the first exactly-halfway cells
(`6.6816965524445895e15` read one unit low), which is the most useful thing a table check can do.

Why a generated `static` of literals and not a table computed by the program: the compile-time evaluator has a budget of one million steps (`docs/compile-time-data.md`) and a table of 650 big numbers needs far more, so
it is written out (60 kB of source: `t[i] = 0xhhhhhhhh << 32 | 0xllllllll;`, because a 64-bit literal with the top bit set does not parse). A `static` cannot be named from another module, so `pow5.word(i)` is a
one-line function; the call costs nothing measurable. Compile time of the tool: 3.96 s against 4.21 s before.

### 2.4 The decision rules, in the order the code applies them (`eisel_lemire`)

1. `q < -342` is zero, `q > 308` is infinity (a non-zero `m` at `q = 308` can still be finite: `1.7976931348623157e308` is `17976931348623157e292`).
2. `w = m << clz(m)`; `(lo, hi) = w * T_hi(q)`; if `hi & 0x1ff == 0x1ff`, add the high word of `w * T_lo(q)` to `lo`, carrying into `hi` (the refinement).
3. `upper` = the top bit of `hi`; `mant` = `hi` shifted right by `upper + 9` (54 or 55 bits); `power2 = ((217706 q) >> 16) + 63 + upper - lz + 1023` (217706/65536 is log2(10) to the precision this range needs).
4. `power2 <= 0`: a subnormal. Shift right by `1 - power2` (zero if that is 64 or more), round the dropped bit up, and the result is `mant` itself: its bits *are* the subnormal's, and a carry into 2^52 is the smallest normal.
5. Otherwise, if `lo <= 1`, `q` in -4..23 and `mant & 3 == 1` (the kept part is even and the round bit is set) and `mant << (upper + 9) == hi` (everything below was zero: an exact halfway), clear the round bit, which is
   round to even; then round up on the round bit, shift out the round bit, and if the mantissa reached 2^53 make it 2^52 and add one to `power2`. `power2 >= 2047` is infinity.
6. The answer is `(mant & (2^52-1)) + (power2 << 52)`: the **bits**, no float. 0 means "reads as zero" (status 12), `0x7ff0000000000000` and up "past the range" (status 11) (`value.float-range`); otherwise
   the key is the bits when positive and `-1 - bits` when negative, which is `bits ^ 0x7fffffffffffffff` of the 64-bit pattern: the order key of `flt.cho`, **without ever building a `float`**.

Ties go to even by construction (step 5 for the halfway cases of the table's range; for `q` outside it a product cannot be exactly halfway, 5^|q| not fitting; for more than 19 digits tier 3 is exact),
and the subnormal rounding of step 4 is the same rule on a longer shift. These are the rules that the cells `edge`, `near`, `mid`, `hard` (section 6) are made to break.

### 2.5 The digit bound, the length bound and the refusals

Unchanged, in the same order: more than 1,100 bytes is `limit.number-too-long` (status 13) before anything else is looked at (a decimal exactly halfway between two doubles has at most 768 significant digits, and the
tail of zeros it can carry is bounded by the same 1,100); then the grammar of the whole cell (9, `inf`/`nan` as 10); then a mantissa of zeros is key 0; a value past the largest double is `value.float-range` (11); a non-zero cell
below the smallest subnormal is 12. The exponent is read with the same clip at 100,000. The new reader never looks at more than the cell: its work is the digits once (the scan), and tier 3 reads them again.

### 2.6 Tier 3: the exact way, and why it is not `std.json`

The old fallback wrote the cell as JSON into a `region`, built the tape, and called `std.json.to_float`; for anything below 2.3e-308 a second function, `subnormal_of`, recomputed it exactly because `to_float` was
known to misround. **The differential found a cell in the normal range that `to_float` of the pinned compiler also gets wrong**: `0.000...2522753946247637179...` (an exact midpoint a hair above, 1,077 bytes, value 3.4e-308, just above the
smallest normal) reads one unit low through `std.json` (2 cells in 30,000 of the `mid` shape), and `subnormal_of` is only called below 2.3e-308. (`alpibrusl/cancho#361` fixes a subnormal case of the same function; I did not test
whether it fixes this one.) Rather than add another special case, tier 3 is a small exact reader of its own, **`exact_bits`** (about 150 lines, `flt.cho`), valid at every exponent and with no dependence on `std.json`:

* D, the digits without leading zeros, as limbs of 32 bits (`dec.mul_small`'s representation, nine digits per pass); e = exponent - fraction digits; early exits: `digits + e <= -325` is zero, `digits + e - 1 >= 309` is infinity;
* e >= 0: Q = D * 10^e; e < 0: Q = floor(D * 2^t / 10^-e) with t chosen so that Q has at least 64 bits, dividing by 10^9 at a time and remembering whether any step left a remainder (the *sticky* bit);
* the top 63 bits of Q and the sticky bit are all that rounding needs: `field = 62 + power + 1023`; drop 10 bits (more for a subnormal), round to even on the round bit and the sticky bit, carry into the exponent for free.

It allocates one 200-limb array in a `region` per call. That is a cost only for the cells that get here. Measured: 1.7 microseconds for a cell of 17 to 40 digits (the `near` shape),
about 10 microseconds for the longest cells there are (the `mid` shape: exact halfway cells with up to 767 significant digits), where the old reader took 10 and 42. **It is also the check on tier 2: it can be switched on for every cell**
(replace the two conditions that choose tier 1 and tier 2 in `parse_float`) and it agrees with Python on 20.9 million cells that way (section 6).

### 2.7 How it fits the typed key and the exact accumulator (`docs/numbers.md` 4.4, 4.5)

Nothing outside `flt.cho` changes. `parse_float` still answers `(key, status)`; the key is still the 64 bits of the double with the low 63 flipped when negative; the accumulator still decodes the bits from the
key (`flt.float_of_key`/`agg.bits_of_key`). What changes is that the key is now made **from integers**: tier 2 and 3 produce the exponent field and fraction directly, so the reader no longer needs `float_of_bits`
(the compiler change of `alpibrusl/cancho#361`), `bits_of`, or `ldexp`. (Tier 1 still builds one `float` and calls `bits_of`, which exists.) `float_of_key`, used by the printer, still goes through `math.ldexp`; with `float_of_bits` in
the pinned compiler that function and `put_float` get simpler, and `finalize` of the accumulator saves two `pow2` loops: a follow-up that does not touch the reader.

## 3. Which tier reads which column (measured rates)

`scripts/spikes/float/rates.py` runs the Python model of the reader (`el_model.py`, a line-for-line port of `eisel_lemire`) over a file and says which tier each cell goes to. 1,000,000 cells per shape (200,000 for `near`, 640 for `hard`):

| shape (`gen_cells.py`) | example | tier 1 Clinger | tier 2 Eisel-Lemire | tier 3 exact |
|---|---|---:|---:|---:|
| `price`: two decimals | `22542.57` | 100% | 0 | 0 |
| `f17`: `repr(uniform(0, 1000))` (the benchmark's `ratio`) | `134.36424411240122` | 65.0% | 35.0% | **0** |
| `money`: 9 to 17 digits of cents, two decimals | `-367593296874477.61` | 7.9% | 92.1% | **0** |
| `bits`: `repr` of a double of random bits, every exponent | `-2.522753946247637e-223` | 3.6% | 96.4% | **0** |
| `exp`: 1 to 17 digits, exponent -330..300 | `7.669074391500080e-109` | 6.7% | 93.3% | **0** |
| `d19`: 19 random digits, point anywhere, exponent half the time | `1806360837783.533740` | 0 | 100% | **0** |
| `d25`: 25 random digits (more than the window) | `708377835.3374068124158683` | 0 | 99.84% | 0.16% |
| `lead`: leading zeros, `.5`, `5.`, signs, `+` exponents | `+00007497189151536333.5` | 50.1% | 49.4% | 0.007% |
| `p10`: powers of ten and their neighbours, the range's edges | `24703282292062327e-203` | 3.7% | 96.3% | 0 |
| `near`: the exact midpoint of two doubles cut at 17 to 40 digits, a hair above or below | `66926478731690.96484375` | 0 | 13.5% | 86.5% |
| `hard`: the 640 cells of `tests/conformance/test_float.py` | the midpoint, `+1`, `4999`, `5000000001` | 0 | 28.9% | 71.1% |

Reading it: **on random data of up to 19 digits the exact tier is never reached** (no cell in the 6,000,000 of `f17`, `money`, `bits`, `exp`, `d19` and `p10` above, and for at most 19 digits it is decided by the theorem, not by luck);
with 25 digits it is reached for 1 cell in 600 (`d25`); on money-like data never. It is reached in bulk only by cells built to sit within a rounding step of a halfway point, which a real column does not contain unless someone made it.
A file that is *all* such cells costs 1.5 to 10 microseconds a cell (`near`, `mid`), 4 to 7 times less than today's reader; a 1,100-byte cell cannot cost much more than the second figure, because the work is the digits times
the limbs and the cell is bounded (section 2.5).

## 4. Cost per cell (`fbench`, cells in memory; the difference of 11 rounds and 1, best of 3; ns)

The line scan (`index_of_byte` to the next newline) is in every figure: 8 ns on the Mac, 6 ns on gram. Mac = Apple M4 Max, load 6 to 15 from other work; gram = one core of an x86-64 box, load under 2.

| shape | Mac `origin/main` | Mac new | gram `origin/main` | gram new |
|---|---:|---:|---:|---:|
| `price` (tier 1) | 13.5 | 13.8 | 22.5 | 23.2 |
| `f17` (the benchmark's 17-digit column) | 1,151 | **29.6** | 693 | **41.9** |
| `money` | 2,898 | **32.6** | 1,539 | **49.2** |
| `bits` | 4,451 | **42.6** | not run | 64.1 |
| `exp` | not run | 37.7 | not run | 53.2 |
| `d19` | not run | 46.2 | not run | 67.1 |
| `d25` | not run | 56.4 | not run | 88.0 |
| `lead` | not run | 40.2 | not run | 67.5 |
| `p10` | not run | 32.9 | not run | 49.0 |
| `near` (87% reach tier 3) | 9,928 | 1,549 | 10,464 | 1,610 |
| `mid` (up to 767 digits, all reach tier 3) | 41,829 | 9,886 | 52,306 | 13,367 |

In cells a second on one core (line scan included), the 17-digit column goes from **0.87 million to 33.8 million** on the Mac and from 1.44 million to 23.9 million on gram. The two-decimal column, which Clinger already decided,
did not move (13.5 to 13.8 ns, 22.5 to 23.2).

**Where the nanoseconds are.** Net of the line scan: tier 1 is about 6 ns (`price`, 7 digits); tier 2 on 17 to 19 digits is 35 to 38 ns (`bits` 34.6, `d19` 38.3); tier 3 is 1.7 microseconds. I did not profile the split inside tier 2.
In isolation `mul128` is 2.35 ns of latency and `clz64` 5.8 (section 2.2), and a digit costs about 1 ns to scan (a price of 7 digits is 6 ns), so the digit loop over 17 to 19 digits is probably 15 to 20 of the 35 ns and the arithmetic the rest.
So **Eisel-Lemire in cancho costs a tier-1 reader about six times, not the 100 to 200 times of the bignum through `std.json`**, which is what `docs/numbers.md` section 11 listed as unknown.
**Clinger alone, with the old exact fallback, would not have been enough**: widened to `m <= 2^53` it decides 65% of the benchmark column and the other 35% would still cost 1.1 microseconds, an average of about 0.4 microseconds
a cell (an estimate from the two measured costs: 2.8x better than today, 14x worse than this).

## 5. Thread scaling on macOS

The old reader allocated a `region` (a 64 KiB arena chunk, over libmalloc's 32 KiB fast size) for **every** slow cell and the allocator serialised the threads. The new reader allocates nothing on tiers 1 and 2: the scan,
`mul128`, `clz64` and the rounding are on the stack and in registers, the table is read-only data. Measured (`e2e.py`, 1M rows, `ratio`, best of 5, Mac, seconds):

| question | threads | before | **after** | DuckDB | after / DuckDB |
|---|---:|---:|---:|---:|---:|
| min, max, sum, count by status | 1 | 1.227 | **0.114** | 0.230 | 0.50 |
| | 2 | 1.494 | **0.059** | 0.154 | 0.38 |
| | 4 | 1.748 | **0.037** | 0.108 | 0.34 |
| | 8 | 1.922 | **0.021** | 0.083 | 0.26 |
| | 16 | 2.093 | **0.016** | 0.085 | 0.19 |
| min, max | 1 / 16 | 1.221 / 2.115 | **0.105 / 0.015** | 0.219 / 0.084 | 0.48 / 0.18 |
| sum | 1 / 16 | 1.205 / 1.985 | **0.109 / 0.016** | 0.218 / 0.082 | 0.50 / 0.20 |
| count where `ratio >= 500` (reads `ratio` of one row in six) | 1 / 16 | 0.268 / 0.352 | **0.082 / 0.013** | 0.213 / 0.083 | 0.38 / 0.16 |

Before: slower with every thread (1.23 s to 2.09 s). After: 1 thread to 16 is **7.1x**, in line with the integer column (`bench_numbers_float.py`, same file: `cents:int` count 0.0828 s at 1 thread and 0.0147 at 16;
the 17-digit `ratio:float` count 0.0861 and 0.0149, **1.04x and 1.01x of the integer column**; gate G8 of `docs/numbers.md`, `price:float / cents:int <= 1.25`: worst 1.027). The factor below 16 is the merge, the output and the
process start, not the reader. On gram (7 cores of 16, `taskset -c 0-6`): 0.156 s (1 thread), 0.085 (2), 0.054 (4), 0.058 (6); DuckDB 0.202, 0.140, 0.108, 0.102; before 0.859, 0.459, 0.286, 0.294.

## 6. Correctness: the differential against Python `float()`

**The gate** (`docs/numbers.md` N3b): at least 100 million generated and adversarial cells, 0 differences, and the 640 hard midpoint cells already in `tests/conformance`.

`scripts/spikes/float/differential.py` generates files of cells with `gen_cells.py` (seeded; the files are byte-identical on the Mac and on gram), reads each with `fbench C` (the reader of `tools/table/flt.cho` in a loop,
folding every `(status, key)` into one 64-bit checksum, a sum of splitmix64-mixed values) and computes the same checksum from Python's `float()` (`reference.py`: the grammar, the 1,100-byte limit, the refusals, `-0` as 0, in about 40 lines).
The checksums are compared per file of up to one million cells; on a difference the file is kept and `diffcells.sh` lists the first cells that differ (no difference ever needed it after the table was fixed).

| shape | what it is | share |
|---|---|---:|
| `f17` | the benchmark's `repr(uniform(0, 1000))` | 19% |
| `bits` | `repr` of random 64-bit patterns: every exponent, subnormals, huge | 19% |
| `d19`, `d25` | 19 and 25 random digits, point anywhere, optional exponent and sign | 13%, 6% |
| `exp` | `d.dddE+-xx`, 1 to 17 digits, exponent -330..300 | 9% |
| `lead`, `money`, `price`, `p10` | `+`, `-`, `.5`, `5.`, leading zeros, `E+05`; 17-digit money; two decimals; powers of ten and neighbours (`9999999999999999e-276`, `5e-324`, `17976931348623158e292`) | 6%, 5%, 5%, 4% |
| `long` | 1 to 1,200 bytes: runs of zeros and nines, trailing zeros, exponents placed at the edge of the range | 5% |
| `near`, `mid` | the **exact midpoint of two consecutive doubles** (subnormals included), its digits cut at 17 to 40 digits and rounded up or down at the cut; the full expansion (up to 767 digits) with its last digit moved by one | 5%, 3% |
| `edge` | every mantissa within 3 of 2^52, 2^53, 2^54, 2^55, 2^62, 2^63, 2^64, 10^15..10^20, 5*10^15, 5*10^18, 9*10^18 with every exponent -30..30, as `NeX`, `N.0eX`, `N.NeX`, negative too | one file of 27,328 |
| `hard` | `hard_cells` of `tests/conformance/test_float.py` with more seeds | 2% |

Runs, all `0 files with a difference`: **the Mac, 101,400,000 cells (seed 1) and 100,367,328 (seed 7, the final code); gram (Linux x86-64), 30,327,328 (seed 2) and 20,867,328 (seed 9, the final code)**; and 20,867,328 on the Mac with tiers 1 and 2
switched off, so that every cell is read by `exact_bits` (the check that the exact tier is right on its own). The 640 cells of `test_float.py` (`rng = 41`, 160 draws) through `fbench` and the conformance suites that read floats pass with the
patched `build/table`: `test_float` 26 tests (which includes those 640 cells and a 400,000-cell run), `test_float_sum` 34, `test_numbers` 34, `test_typed_keys` 25, `test_limits` 14, `test_differential` 7, `test_parallel` 22 (all run again on the final binary after `cancho fmt` and `scripts/manifest.py`).

**What the differential found before it passed**, two things: (1) the generator's rule for the table at q in -27..-1 (section 2.3); (2) `std.json.to_float` of the pinned compiler, still the fallback at that point, misreading exact-midpoint cells
a hair above a halfway just above the smallest normal (2 of 30,000 `mid` cells), which the old `subnormal_of` did not cover because the value is above 2.3e-308: so tier 3 became its own exact reader (section 2.6).

**Can the gate fail?** `scripts/spikes/float/mutants.py` applies 24 edits to the reader, builds each and runs the checksum over the corpus. **16 are killed**: the tie rule off, the tie window narrowed at either end, the second
product never done, no carry, a wrong power-of-two estimate, no round-up of a subnormal, no carry past 2^53, `clz` off by one, the window-plus-one test off, a dropped digit not raising `q`, Clinger past 2^53 (only `edge` catches that: it needs
the mantissa 2^53 + 1), the sticky bit of the exact tier lost in the division or in the low limbs, and round-half-up in the exact tier. **8 survive**, seven of them equivalent or harmless: "the second product at 8 bits instead of 9" computes it more
often, never less; "subnormal cutoff 64 to 63" shifts by 63 where 64 would give zero too; "overflow at 2047 to 2048" in `eisel_lemire` and in `exact_bits` is caught by the caller's `>= 0x7ff0000000000000` test; "window of 18 digits" sends 19-digit cells to the
`m + 1` test (correct, slower); "`t` one smaller" in the exact tier still leaves it more than the 55 bits it needs; "Clinger takes 19-digit patterns" (the `m >= 0` test removed) makes `bits` negative, which sends the cell to tier 3 (slower, not wrong; the
test stays for clarity). The eighth is **"`lo <= 1` to `lo <= 0`"**: `lo` was 1 in none of 1.5 million random products and about 16,000 constructed exact-halfway ones that I looked at (`el_model.py`), so the corpus cannot tell the two apart; the rule is
Lemire's and I kept it.

## 7. The end-to-end cell, and the whole row

`scripts/spikes/float/e2e.py` and `scripts/bench_numbers_float.py`, `scripts/bench_numbers_fsum.py` with `--bin` for each build, the file of `scripts/bench_numbers.py` (1,000,000 rows), every answer checked first (the table's
min/max/sum equal `math.fsum` exactly; DuckDB's sum is within 1e-9, it is not exact: 17 to 125 ulp, different at 1, 4 and 16 threads, as `docs/numbers.md` 4.4 says), best of 5.

**The whole row.** One row of this file is 59 bytes; `table` finds the line, skips the columns it does not need, compares the status and reads and folds the float cell. At one thread on the Mac a
`min`+`max` of `ratio` by `status` is **105 ns a row** (0.105 s), against 1,221 ns before and 219 ns for DuckDB; in the same run (`bench_numbers_float.py`) the same question on the two-decimal `price` is 97.5 ns, and the `sum` of
`ratio` is 111 ns against 94 for `price` and 88 for the integer `cents` (`bench_numbers_fsum.py`). So **the 17-digit float cell now costs the row 1.10x of the two-decimal one for `min`/`max` and 1.26x of the integer one for `sum`**; the reader
itself is about 22 of the 105 ns. At 16 threads: 15 ns a row.

| Mac, `ratio`, DuckDB / table (higher is better for `table`) | before (1 / 4 / 16 threads) | **after** |
|---|---:|---:|
| count where `ratio >= 500` | 0.81 / . / 0.24 | **2.54 / . / 5.65** |
| rows where `ratio >= 500` | 0.94 / . / 0.28 | **3.34 / . / 6.15** |
| min, max by status | 0.18 / . / 0.04 | **2.10 / . / 4.74** |
| sum by status | 0.18 / 0.06 / 0.04 | **2.06 / 2.89 / 4.53** |
| mean by status | 0.18 / 0.06 / 0.04 | **2.05 / 2.79 / 4.56** |
| sum and mean | 0.18 / 0.05 / 0.05 | **1.91 / 2.83 / 4.91** |

(`.`: not run at 4 threads by that script.) The "5.7x to 24x slower" of `docs/numbers.md` N4 reproduces (1/0.18 = 5.6, 1/0.04 = 24 at 16 threads for `sum`) and becomes **1.9x to 6.2x faster**. Gate G9 of `docs/numbers.md`
("not slower than DuckDB at one thread on the same file") passes with 2x to spare.

**The realistic ratio.** For a column of 15 to 17 digit doubles, one thread, a CSV file in the page cache: `table` is **0.5x of DuckDB's time (Mac) and 0.6x to 0.75x (gram)**; more cores widen it on the Mac (0.2x at 16) and
narrow it on gram (0.45x to 0.6x at 4 to 6). The 2x is where the numbers are measured; I would not quote better than "about twice as fast as DuckDB's `read_csv` + `min`/`max`/`sum` on this file, and exact where it is not". What this does *not* say: DuckDB
was run with its defaults (`read_csv` sniffing off, columns declared), nothing was tuned for either side, and the file is the benchmark's, not a customer's.

gram, same file (7 cores of 16, `taskset -c 0-6`; table / DuckDB seconds):

| question | threads | before | **after** | DuckDB | after / DuckDB |
|---|---:|---:|---:|---:|---:|
| min, max, sum, count | 1 | 0.859 | **0.156** | 0.202 | 0.77 |
| | 2 | 0.459 | **0.085** | 0.140 | 0.61 |
| | 4 | 0.286 | **0.054** | 0.108 | 0.50 |
| | 6 | 0.294 | **0.058** | 0.102 | 0.56 |
| min, max | 1 / 6 | 0.860 / 0.284 | **0.142 / 0.059** | 0.200 / 0.099 | 0.71 / 0.59 |
| count where `ratio >= 500` | 1 / 6 | 0.240 / 0.084 | **0.115 / 0.044** | 0.195 / 0.097 | 0.59 / 0.45 |

## 8. What is in the patch

* `tools/table/flt.cho` (+333 / -147): `mul128`, `clz64`, `eisel_lemire`, `exact_bits` (+ `scale_add`, `bit_length`), a new `parse_float`; `slow_reader` and `subnormal_of` are gone, and so is `import std.json`. `float_of_key`, `put_float`,
  `key_of`, `pow10_float` are untouched.
* `tools/table/pow5.cho` (new, generated by `scripts/spikes/float/pow5_table.py`, 60 kB, 10 kB of data).
* `manifests/table.authority.json`, `generated/table/built.cho`: regenerated by `scripts/manifest.py`; the authority row is unchanged (`args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_write`), the `pure`
  list loses `std.json`'s helpers and gains the new functions.
* `scripts/spikes/float/`: the spike and the gate (section 11).
* Nothing in `cancho`, `std`, the CLI, the refusal codes or the output. `cancho fmt --check tools generated` passes; `scripts/manifest.py --check` passes; the binary is 624 bytes smaller (512,584 against 513,208), the build 0.25 s faster.

## 9. What is not done, and what could be wrong

* **The pages and the numbers they quote.** `docs/benchmarks.html`'s "Floats" table, `docs/numbers.md` G9/N4's "5.7x to 24x slower", sections 4.2 (tier 3 of the reader) and 11 (the "what the language lacks" row for the multiply-high, "not known": the real cost of Eisel-Lemire) are now
  out of date. This branch does not edit them: `scripts/site.py` regenerates the pages and the counts in the README from the benchmark files, which should be rerun on a quiet machine, not on this one (load 6 to 15) and not with the page's
  numbers mixed with mine. The suggested text is sections 0, 5 and 7 here.
* **Quiet-machine numbers.** Both machines were shared (Mac: other sessions; gram: another session's `taskset` builds at times). The ratios agree between the Mac and gram and between the spike (`fbench`) and the tool
  (`table`); the absolute nanoseconds are +-10% at best.
* **The `lo <= 1` rule** (section 6): not distinguishable from `lo == 0` by anything I could build. If Lemire's proof is right it does not matter; if it is wrong, only for exact halfway cells with `q` in -4..23.
* **Tier 3 allocates.** One `region` of 200 limbs per cell that gets there, so on macOS a file of *only* adversarial cells would again scale badly with threads (the cost is 1.5 to 10 microseconds a cell either way). A real column does not do this; if a
  column ever does, a per-call stack buffer (the language has none for 200 `int`s) or the compiler's per-thread chunk cache of `alpibrusl/cancho#361`'s finding 4 would fix it.
* **The 19-digit `m` as an unsigned pattern in a signed `int`** is the one place the checked arithmetic is turned off (`wrapping_mul`, `wrapping_add` in the digit loop and in `mul128`). Every other operation is the checked one; the
  `edge` shape puts a mantissa on each side of 2^63 and 2^64 to look at exactly this.
* **Not tested:** other CPUs than M4 Max and one x86-64 box (the arithmetic is integer and the table is generated, so a different result would be a compiler bug, and both backends have run it); inputs that are not ASCII (the grammar
  refuses them before any of this; `test_float`'s refusals pass); the `--threads` run of `test_float_sum` on Linux (it passed on the Mac only).
* **`float_of_bits`** (`alpibrusl/cancho#361`): not needed here. With it the printer and the accumulator's `finalize` could drop `ldexp` (section 2.7); that is a separate change.
* **A faster scan** is the next gain and is not an Eisel-Lemire matter: the digit loop is about 1.2 ns a digit; reading eight digits at a time (SWAR) needs an unaligned 8-byte load the language does not have, and the line scan is already 8 of the
  29.6 ns. Not pursued.

## 10. Reproduce

```
cancho build                                                   # the tool, with the new reader (build/table)
python3 scripts/spikes/float/pow5_table.py --check             # tools/table/pow5.cho is what the generator writes
scripts/spikes/float/build.sh OUT                              # OUT/fbench; origin/main's reader is built next to it as flt0
for k in f17 price money bits exp d19 d25 lead p10; do python3 scripts/spikes/float/gen_cells.py $k 1000000 1 > DATA/c_$k.txt; done
python3 scripts/spikes/float/time_cells.py OUT/fbench DATA 3   # ns per cell, old and new
python3 scripts/spikes/float/differential.py --bin OUT/fbench --cells 100000000 --workers 5 --tmp TMP
python3 scripts/spikes/float/mutants.py DATA                   # needs c_edge, c_mid, c_near too: gen_cells.py edge 0 0, mid 10000 1, near 200000 1
python3 scripts/spikes/float/e2e.py --old BASE/build/table --new build/table --file DATA/e2e.csv
python3 scripts/spikes/float/rates.py DATA/c_f17.txt           # which tier decides what
```

The Mac compiler is `a4572ea` (the pin); gram's was built from the same sources (`cancho --version` there says `rev unknown`, so `cancho build --ignore-compiler-rev`).

## 11. The files

| file | what it is |
|---|---|
| `fbench.cho` | the driver: modes `l` (the scan), `o` (origin/main's reader), `n` (new), `m`/`M`/`z`/`Z` (`mul128`, `clz64`: with and without the scan, as a latency chain), `C`/`O` (checksum of every cell through new/old), `D` (dump) |
| `build.sh` | builds it with origin/main's `flt.cho` renamed to module `flt0` |
| `pow5_table.py` | generates and checks `tools/table/pow5.cho` |
| `gen_cells.py` | the cell shapes of section 6, seeded |
| `reference.py` | Python's side of the checksum and of `D`; the reference semantics of the cell in 40 lines |
| `differential.py`, `diffcells.sh` | the gate; the localiser |
| `mutants.py` | the 24 mutants |
| `el_model.py`, `rates.py` | a Python port of `eisel_lemire`, and which tier reads which file |
| `time_cells.py`, `e2e.py` | ns per cell; the end-to-end cell against DuckDB |

## 12. For `docs/numbers.md` (not edited here)

Section 4.2 "Correctly rounded reading": tier 3 becomes "Eisel-Lemire on a 19-digit window, with `exact_bits` (bignum) only when the window and the window + 1 differ", the "1.0 to 1.6 microseconds a cell" and "N3b" paragraphs become "stage N3b,
built, section 6 of `gap-float.md`", the row of section 8 (G9) is met: 0.5x of DuckDB's time at one thread (Mac), the N4 paragraph "5.7x to 24x slower" is replaced by 1.9x to 2.1x faster at one thread, and section 11's "what the language lacks": the multiply-high
is *not* an urgent ask (2.35 ns of latency, hidden by the scan), `float_of_bits` is wanted only by the printer and the accumulator, and "the tape-free correctly rounded `text -> float`" is no longer needed by `table`.
