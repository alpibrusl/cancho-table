# Functions in `--compute`: a design (slices K5 to K8)

> **Status: design; K5a (`--explain` for the stage, section 3.1) and K5 (the numeric functions, section 3.2) are built; K6 to K8 are not.** Every claim is a **choice**, a **read** (file), **measured** (with the command), or **open**. A measurement replaces its line, in place, when it exists.
> Written 2026-11-15 after the maintainer's direction (relayed in the working session): *unblock functions without affecting performance or the first principles.*
> It amends item 14 of `docs/backlog.md` (the decision of 2026-11-09) and reopens two lines of `docs/computed-columns.md` section 10 ("Rounding functions: out", "Dates: out"); both are corrected in place and point here.

## 0. The decision, and what it keeps

**Amended rule (choice, the maintainer's).** A *derived column* is in `table`; an *in-place rewrite* is out. `--compute` already only appends columns (`compute.duplicate-name` refuses a name that is an input header, `docs/computed-columns.md` section 3), so a function that makes a new column from old ones is a derivation. `--lower COL`, `--clip`, `--sentinel` as a value rewrite and `--dedupe` stay out, as item 14 says.

Item 14 gave three reasons for putting transforms in another tool. Each is answered here, not argued away:

| reason of 2026-11-09 | answer |
|---|---|
| `--query` has no expressions or functions | it still has none. A function is a flag of the stage; a `--query` that wants one is `query.unsupported`, naming the flag form (as `--compute` is refused beside `--query` today, `docs/computed-columns.md` section 4) |
| `--explain` and the equivalence gates assume one plan from flags or query | the stage has **one plan form**, and it gets an `--explain` (K5a below) and an oracle gate (G-FN-ORACLE). **Measured gap:** `table --compute 't=a:int*b:int' --explain f.csv` is today `compute.unsupported-combination` (one run, `table` built from this repository's `main` at `a553449` with the pinned compiler, 15 Nov 2026; the refusal's `detail.flags` lists `--compute --select --where --group --agg --order-by --report --rows --join --query --sort --top --rename`), so the stage has no explain yet; closing it comes first |
| a transform changes the data the plan is about | a derivation does not: the input columns are written as they were read, byte for byte, then the new ones |

## 1. Invariants every function keeps (the first principles, as gates)

1. **Typed, no coercion.** A function has a signature (argument kinds, result kind). A mismatch is `compute.type-mismatch` with a repair. Nothing is converted silently: a text is never read as a number inside a function, as a column is never read as one outside `:int`/`:dec(S)`.
2. **Bounded.** Every function declares its output bound. A result that would pass the record limit (`--max-line-bytes`) is `compute.result-too-long`, never truncated. At most 8 definitions, 4 levels of nesting, 8 arguments (the first two are the existing limits' neighbours: **choice**).
3. **Deterministic and local.** No locale, no `now`, no zone database, no random, no I/O. Case mapping is explicit (section 3). The same bytes on any number of cores: the stage is one loop on one core (as `--compute` is), so there is nothing to merge.
4. **A refusal names the definition, the column and the row, never the cell** (the rule of `--compute`).
5. **The existing paths do not change.** A function lives in the stage's loop and in no other. `--where`, `--query`, `--group`, `--agg`, `--join` take no function (**choice**: their docs say "no expressions, no functions", and a function there is the select path's 1.01 budget, `docs/or-and-columns.md` 8.3). The idiom is two stages.

## 2. Two value domains, so the exact machine stays as it is (choice)

`tools/table/cexpr.cho` (775 lines) is an integer stack machine over the 180-bit core of `tools/table/cmpv.cho` (**read**): opcodes cell, const, add, sub, mul, div, neg. A text has no place in it. So:

* A definition has **one result kind**: a number (`int` or `dec(S)`, the existing machine) or a text (a new, small evaluator over byte slices of the row copy and one scratch arena).
* **Numbers keep their machine, untouched.** A number-valued function that takes numbers (`abs`, `round`) is an opcode (`op_fn`) with the function's id in `b`. A number-valued function of a text or a date (`len(s)`, `year(d)`) is a **leaf**: it produces an integer that is pushed as an operand, so the arithmetic around it is unchanged.
* **Texts have their own grammar**: `fn(text_operand, literal ...)` nested to 4 levels, no arithmetic. A column is a text operand as itself (`name`); a date operand is `d:date` as in `--where`.
* **No automatic conversion between the domains** (invariant 1). Whether a number may be written as text (`text(x)`, exact, at its scale) is **open** (section 7).

## 3. The functions, by slice (each a row of the signature table)

The signature table is **one static table** that the parser, `introspect`, the skill text and the MCP schema all read (as the lexical tables of `docs/query.md` section 10 are), so the surface is generated, not written twice. **Names must not be a reserved word of `--query`** (`count sum min max mean`, `docs/query.md`), hence `greatest`/`least`.

| slice | functions | note |
|---|---|---|
| **K5a** (built) | `--explain` for the stage | the definitions, the scale of each, the program size, the possible refusals; no data read beyond the header, as `--explain` is. A prerequisite, not a function. Section 3.1 |
| **K5** (built) numbers | `abs(x)`, `sign(x)`, `greatest(a, b ...)`, `least(a, b ...)`, `round(x, n)`, `floor(x, n)`, `ceil(x, n)`, `trunc(x, n)`, `coalesce(col, literal)` | `round` is half to even, the rule `/ @N` already has (**read**: `docs/computed-columns.md` section 6); `n` is a literal scale `0..18`; the result is `dec(n)`. `coalesce` is the answer to the empty cell (`value.not-decimal`, `docs/numbers.md`): an empty cell, and only an empty one, becomes the literal; any other bad cell is still refused. A result is exact or refused (`compute.overflow`), never a double |
| **K6** text | `lower(s)`, `upper(s)`, `trim(s)`, `ltrim(s)`, `rtrim(s)`, `substr(s, start, count)`, `concat(a, b ...)`, `len(s)`, `replace(s, from, to)` | **ASCII only** (choice, recommended and accepted): `lower` and `upper` change the 26 ASCII letters and leave every other byte as it is, and the signature says so; no Unicode table, no locale. `trim` removes ASCII space and tab. `substr` counts **bytes** and refuses a cut inside a multi-byte character (`compute.not-utf8-boundary`), because a half character is not a text. `replace` and `concat` can grow: bounded by invariant 2. `len` counts bytes (**open**: also a character count under another name) |
| **K7** conditions | `if(cond, a, b)`, comparisons in a definition | **open and last of the numbers and texts**: it needs the `--where` condition grammar inside an expression (so its 4-level and 64-condition limits), and a boolean kind. It is designed after K5 and K6 have measured what a function costs |
| **K8** dates | `year(d)`, `month(d)`, `day(d)`, `weekday(d)`, `days_between(a, b)`, `add_days(d, n)` | on `:date` only, the real calendar already in `dtparse` (**read**: `docs/datetime.md`): integer arithmetic on days, no zone, no time of day. **Open, to confirm before K8:** whether day-count arithmetic reopens the non-goal "no arithmetic" of `docs/datetime.md` section 15. `strftime`, zones, `now` and epoch integers stay non-goals |

### 3.1 K5a: the explain document of a stage (built 2026-11-15)

> **Status: built.** The text below is the design as first written, **corrected in place** where the build differs: the stage is an **additive member** (`stage`) of the one explain document, and `plan` stays the plan of the read, so every consumer of the existing keys keeps working and the explain of every other plan is the earlier bytes.

**Read** (`table --rows --explain f.csv`, `table --group s --agg count --explain f.csv`, 15 Nov 2026, `table` built from `main` at `a553449`): an explained plan is one document `{"explain": {...}}` with the keys `source`, `plan`, `output`, `work`, `refusals`, `notes`, `sql`, `sql_flags`. **The refusal that was there** was one name (`inp.has_explain`) in the list that `--compute` refuses in `tools/table/table.cho`; it is removed.

| key | for `--compute 'NAME=EXPR;...'` (as built) |
|---|---|
| `source` | as for any plan (the header is read, nothing else) |
| `plan` | **unchanged**: the plan of the read (`form: "rows"`, every column, no condition, `limit`, `from`) |
| **`stage`** (new, only for a stage whose header is known) | `{"definitions": [{"name": "t", "scale": 2, "instructions": 3}, ...], "max_definitions": 8}`: the names, scales and program sizes are the compiler's (`cexpr.compile`, `cexpr.scale_of`, `cexpr.size_of`), not a guess. **Corrected:** the first design also had a `kind` (`int` or `dec`); the machine has no such distinction in a result (a scale of 0 prints as an integer), so there is none |
| `output.columns` | every input column, then every name: the header of the real answer |
| `work` | the read's; `threads.effective` is **1** and `reason` is `the --compute stage runs on one core`, whatever `--threads` says (found while building: the first version said `effective: 4` on a large file, which the stage never does; `test_the_stage_runs_on_one_core_whatever_threads_say`) |
| `refusals.possible` | the I/O and reader rules of any plan, then exactly those that this program can give: `value.not-integer` and `value.integer-overflow` if a cell is read as `:int`, `value.not-decimal`, `value.decimal-scale` and `value.decimal-too-wide` if a cell is read as `:dec(S)`, `compute.overflow` if any definition has an operator, `compute.divide-by-zero` only if one has a `/` |
| `sql`, `sql_flags` | `null` and `[]`, with the note `sql.compute: no query is shown for this plan: --compute adds columns that a query has no word for` (a new reason, `qsql` code 11) |

**A definition that does not compile is the explain's refusal**, with the rule, message and detail the run gives (`compute.syntax`, `compute.unknown-column`, `compute.too-long`, ...). A constant zero divisor (`t/0@2`) **does** compile: it is a refusal of the data, so it is in `refusals.possible` and not a certain refusal. The explain reads the header and runs no row. **Not resolved:** for standard input the header is unknown, so there is no `stage` member and the existing note says so (`test_standard_input_is_not_read`).

**Gates** (`tests/conformance/test_compute_explain.py`, 11 tests; `scripts/compute_explain_mutants.py`, 17 mutants):

* *G-EXPLAIN-CERTAIN*: 20 definitions that do not compile; the explain's `error`, `errors`, message and exit status equal the run's.
* *G-EXPLAIN-PLAN*: six valid stages (a product, a division at a scale, a chain, a quoted name, unary minus); `stage.definitions` equal the names written by the run after the input columns and the fractional digits of its values, and `output.columns` is the run's CSV header.
* *G-EXPLAIN-POSSIBLE*: eight hostile files (a bad integer, an integer past 64 bits, a bad decimal, too many fractional digits, too wide a decimal, an overflow, a zero divisor, a ragged row): the rule the run gives is in the list; and a rule that the definitions cannot reach is not in it.
* *G-EXPLAIN-NOREAD*: a 3 MB file whose rows are all garbage explains, and `bytes_read` is the header chunk's, under 70,000.
* *G-COMPAT*: the explain of every other plan is the earlier one: `test_explain.py` (42 tests) and `test_sql_explain.py` (10) pass unchanged beside the new file.

### 3.2 K5: the numeric functions, exactly (built 2026-11-15)

> **Status: built.** The text is the design as written before the code, **corrected in place** where the build differs (marked *Corrected*); the gates are listed at the end of the section.

**Grammar.** A call is `name(` argument { `,` argument } `)` with the `(` **immediately** after the name (no space). A word followed by `(` is a call; a word followed by `:` or by anything else is a column or an earlier definition, as today, so a column called `abs` is still `abs:int`. A call is an operand: it can be anywhere an operand can, inside an operator expression and inside another call. **Choice:** a call's parenthesis counts as one of the 4 levels of `compute.too-long`, its arguments are pending operands (the limit of 10), and a call has at most 8 arguments.

| function | arguments | result scale | meaning (exact; Python integers are the reference) |
|---|---|---|---|
| `abs(x)` | 1 | scale of `x` | the magnitude |
| `sign(x)` | 1 | 0 | -1, 0 or 1 |
| `greatest(a, b ...)`, `least(a, b ...)` | 2 to 8 | the largest scale among the arguments | each argument is rescaled **exactly** to the result scale, then compared; a rescale that leaves 127 bits is `compute.overflow` |
| `round(x, n)` | 2: `x`, and `n` an **integer literal 0 to 18** | `n` | `x` at scale `n`, ties **to even** (the rule of `/ @N`) |
| `floor(x, n)` | the same | `n` | toward minus infinity |
| `ceil(x, n)` | the same | `n` | toward plus infinity |
| `trunc(x, n)` | the same | `n` | toward zero |
| `coalesce(c, lit)` | 2: `c` a **plain typed column** (`price:dec(2)`, `qty:int`), `lit` a number with an optional `-` | the scale of `c` | `lit` (scaled to the scale of `c`) when the cell of `c` is **empty**, else the cell exactly as `c:type` reads it; any other bad cell is still its `value.*` refusal |

* If `n` is not below the scale of `x`, `round` and its kin only add zeros: `x * 10^(n - scale)`, `compute.overflow` past 127 bits. Below it, the digits dropped are decided by the rule of the function.
* **An empty cell** is a cell of zero bytes (a quoted `""` too). A cell of spaces is not empty; it is refused as it is today.
* `coalesce` takes a *column*, not an expression, because the empty cell is a fact of a cell and not of a value: `coalesce(price:dec(2) * 2, 0)` is `compute.syntax` at the first argument. A literal with more fractional digits than the column's scale is `compute.bad-literal`.
* **No float anywhere**; a `:float` operand is still `compute.float-operand`.

**New refusals** (exit 2, as the other `compute.*` compile refusals; no payload): `compute.unknown-function` (status 13: the name is not in the table; *Corrected:* the hint lists the names, there is no `detail.available`), `compute.arity` (status 14: too few or too many arguments, at the name), `compute.bad-literal` (status 15: the scale of `round` and its kin is not an integer literal 0 to 18, or the literal of `coalesce` is not a number or has more fractional digits than the column). `compute.syntax` stays for a comma outside a call, an empty argument, or `coalesce` of a non-column.

**Encoding (choice, nothing at run time beyond the machine).** A call is postfix: its arguments' instructions, then `op_fn` with `a` the function id, `b` the number of **operands it takes off the stack** (for the rounding family one: the scale is not an operand; *Corrected:* the first build counted it, and the first run trapped) and `c` the literal `n` of the rounding family. A `coalesce` is the cell instruction with its kind raised by 2 (`2` an integer, `3` a decimal with a default) followed by an `op_const` that the evaluator reads as the default and skips; so a cell instruction is the same four ints it is. `shape` (scale and pending depth) reads `op_fn` as "pop `b`, push one".

**The reference** is `scripts/cexpr_golden.py`: the tree of Python integers gains a `fn` node and a `coal` node (the code is the table above, and the rounding family is also checked against Python's `decimal` module, which shares no code with either: `scripts/cexpr_golden.py --selftest`). **Gates:** G-FN-ORACLE for the numbers (random calls, nested, with the operators, at all scales, with ties and values near 2^127), G-FN-MUT (below), `tests/conformance/test_compute.py` through the binary, and the explain's `refusals.possible` (a call that can overflow lists `compute.overflow`).

**Gates, as run** (compiler `119bcfe`, 15 Nov 2026):

* *G-FN-ORACLE* (`scripts/cexpr_golden.py`): the reference of Python integers gains the calls; **5,256 cases, 0 differences** at two seeds (about 3,000 random expressions with calls nested up to the four levels, 600 `coalesce` cases with the cell empty or not, the 1,500 chains of K4, and 133 hand cases for every value and every refusal of the table), and **3,000 fuzz cases with no trap**. The rounding family of the reference is itself checked against Python's `decimal` module (`--selftest`: 200,000 cases with ties on purpose, four modes, 0 differences). The probe also says, for every definition, whether the scale it was compiled with is the scale of the value it computed (`S definition compiled computed`): that check found nothing in the older features and is what kills the mutant that corrupts the compile-time scale of a `coalesce`.
* *G-FN-MUT* (`python3 scripts/cexpr_golden.py --mutants`): 29 mutants of the calls and of the exact core's new functions beside the 25 earlier ones; the first run killed 53 of 54, and the survivor (the compile-time scale of a `coalesce`, which only the explain showed) is what the probe's scale check was added for.
* *Through the binary* (`tests/conformance/test_compute.py`, `Functions`, 8 tests, and the random trees of `Compute`, which now draw calls): the values in CSV, `greatest` and `least` across scales, `coalesce` of an empty cell and of a bad one, JSON lines with `--absent empty`, the three new rules with their exit status and offset, overflow as a refusal, and a function name that is still a column name.
* *Explain* (`test_compute_explain.py`): a call that can overflow lists `compute.overflow` and `abs` and `sign` do not; `coalesce` adds no rule beyond its cell's; the scale of a call is in `stage.definitions`; a call that does not compile is refused as the run refuses it.

**Not measured:** the cost of a call (G-COST). No speed is claimed for the functions; the baseline of section 5 stands.

## 4. Rules (every refusal has a tag)

New: `compute.unknown-function` (the name, the table of names in `detail`) · `compute.arity` · `compute.type-mismatch` (the argument, the kind it wanted and the kind it got) · `compute.result-too-long` · `compute.not-utf8-boundary` (K6) · `compute.bad-literal` (a scale outside 0..18, a negative count). Existing `compute.*` and `value.*` unchanged. **No payload in any message.**

## 5. Gates (written before the code, each able to fail)

* **G-FN-ORACLE.** For each function, `tests/conformance/compute_fn_ref.py` (Python, sharing no code with the tool) generates random definitions over random typed tables, with hostile cells (the empty cell, NUL, `"`, `\`, U+2028, a 4-byte character, a half character, digits past the scale, 18-digit numbers, ties of half-even for every sign) and compares **stdout, stderr and the exit status**, refusals included.
* **G-FN-MUT.** `scripts/compute_fn_mutants.py`: per function, mutants the oracle must kill (half-even to half-up, `trim` of one side only, a cut inside a character allowed, an off-by-one `substr`, `lower` of a non-letter, `coalesce` of a non-empty bad cell, the result bound off by one, an argument swapped). A survivor is explained or the gate is extended.
* **G-COMPAT.** The corpus md5 of `scripts/corpus.py` and the instruction counts of the select loops (callgrind, budget 1.01) equal those of `main`: no byte of the existing paths changes (`docs/computed-columns.md` section 7).
* **G-FLAT.** Peak resident memory of one definition of each function class is flat in the rows (the repository's `maxrss` launcher), as `test_compute.test_memory_is_flat_in_the_rows` holds for the arithmetic.
* **G-COST.** Per function, wall time per row on the repository's 1,000,000-row file, next to the numbers of the stage as it is. **No speed is claimed before this table exists.** Baseline, **measured** (`docs/computed-columns.md` section 7, 4-core container): `--select id,price,qty` 93 ns a row; `--compute 't=price:dec(2)*qty:int'` 279 ns; a product and a division 555 ns.
* **G-EXPLAIN.** The explain of a stage lists exactly the definitions that run, with their kinds; a corpus of stages is run with and without `--explain`, and every refusal that the explain calls certain is the one the run gives.
* **G-SURFACE.** `introspect`, `skill`, the MCP schema and AGENTS.md are regenerated from the signature table, and every example in them is run.
* **G-NOPANIC.** A fuzz of function names, arities, literals and cell bytes: no input reaches a panic.

## 6. What stays out

In-place rewrites (`--lower COL`, `--clip`, `--sentinel`, `--dedupe`); a function in `--where`, `--query`, `--group`, `--agg`, `--join` or `--order-by`; `:float` operands and float functions (`sqrt`, `exp`: a double is rounded by every operation, `docs/computed-columns.md` section 3.1); regular expressions; user-defined functions; locale, `now`, zones, `strftime`; Unicode case mapping.

## 7. Open

1. **Dates (K8):** day-count arithmetic only, no zones: **to be confirmed by the maintainer before K8** (the direction in the session was a recommendation, not yet a yes for this one).
2. **`text(x)`**: a number written as text, exact at its scale. Needed by `concat(name, '-', id)`; without it `concat` takes texts only and a number column is not a text operand.
3. **Unicode case mapping** and a **character-count `len`**: out for v1; the names are not taken (`upper`, `lower` and `len` are the ASCII and byte ones, and say so).
4. **K7** (conditions): the boolean kind and how much of the `--where` grammar it takes.
5. **Window functions** are a different stage (a materialising one) and have their own document.
