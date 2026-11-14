# Computed columns: `--compute`, a design (slice K0)

> **Status: built for a file and for standard input (slices K0 to K3); JSON-lines input is still refused (section 10).** What was measured is in section 7.1; the lines below that say "not yet" or "nothing built" were true when they were written and are corrected where they stand.
> **(Written as: design, nothing built.)** This reopens the decision of `docs/or-and-columns.md` section 8.3 ("B2 and B3: do not build"). The maintainer's direction
> (14 Nov 2026, relayed by the cancho-sheets work) is that *column operations are a basic feature of a table tool*. Section 8.3 recorded its own reopening
> condition (a recorded use that needs the value in the same call, and the select path measured with the instruction budget 1.01). The use is recorded
> in section 1; the measurement is a gate of this design (section 7), not a claim. Every claim below is a **choice**, a **read** (file and line), or **open**;
> none is measured yet, and a measurement will replace its line, in place, when it exists.

## 1. The use

cancho-sheets (`alpibrusl/cancho-sheets`) derives a column from other columns row by row (`rev_per_employee = revenue / employees`); a spreadsheet's most
basic act is `=B2*C2`. Today `table` returns the input's bytes only (`docs/or-and-columns.md` 8.3: "the output is the input's bytes"), so that act is done
by a program outside, losing the exactness `table` gives (decimals never pass through doubles, `docs/numbers.md`).

## 2. What is built, in one sentence

`--compute 'NAME=EXPR[;NAME=EXPR...]'` reads rows (a file or standard input, CSV/TSV or JSON lines as today), and writes **every input column followed by one
column per definition**, header first, as CSV or JSON, streaming, with flat memory. It is a **stage**: its output is what `table` writes, so the next
call composes it (`... | table --stdin --where 'total:dec(2) > 100'`, `--agg sum:total:dec(2)`), and `sum(price * qty)` — the use 8.3 said a single
operator could not answer — is two stages, exact. *Choice.*

**Why a stage and not a clause of `--select`/`--where`/`--agg`.** The row loops of `table` are four generated copies (`scripts/stream.py` derives the
standard-input loops from the file loops) plus the parallel, sorted, grouped and join paths, with `docs/backlog.md:75-81` on record that a third case in the
select loop costs 15 percent against a budget of 1.01 (`docs/or-and-columns.md` 8.3, item 3). A computed value inside them would change the hottest path
the tool has and every sink (`engine.flush`, `engine.flush_last`, `par`, `psort`, `jn*`: 13 write sites read with `grep` on 14 Nov 2026). A stage is its own
loop: **no byte of the existing paths changes**, which is measurable (gate G-COMPAT: the corpus md5 and the instruction counts of the select loops are
identical before and after) instead of argued. The price is a second pass through a pipe when a selection or a filter is also wanted; `table`'s
agent-first contract is already "standard input in, the answer out" (`README.md`), so that is the idiom, not a workaround. *Choice; the cost is open
until measured (G-COST).*

## 3. The expression

```ebnf
defs   = def { ";" def } ;
def    = name "=" expr ;
expr   = term { ( "+" | "-" ) term } ;
term   = factor { ( "*" | "/" "@" nat ) factor } ;    (* "/" is only ever written  a / b @ N  : see 3.3 *)
factor = "-" factor | "(" expr ")" | operand ;
operand= column [ ":" type ] | number ;
type   = "int" | "dec(" nat ")" ;
```

* **Names and positions** as `--where` has them (`docs/select.md`): a bare word, `'quoted'` with `''`, `#3` a position, a backslash escape. *Read.*
* **`+ - *`, unary minus, parentheses, the usual precedence** (a product binds tighter than a sum, left to right). *Choice:* the earlier design allowed one
  operator only to avoid "the first expression"; a language with three operators and parentheses is what a spreadsheet user means by "operations", and
  the grammar above is the whole of it: **no functions, no comparisons, no strings, no conditionals.**
* **`NAME`** is the new header; it must not equal an input header or another definition (`compute.duplicate-name`, repair: choose another name).
* **At most 8 definitions, 256 bytes each, 4 levels of parentheses and 10 operands pending at once** (`compute.too-long`). *Choice, small enough to bound the work per row; the first draft said 16 levels of parentheses, and the build (`tools/table/cexpr.cho`) holds 4, which a gate (`C 9` on five levels) pins.*
* **`a / b @ N` takes a factor** on its right (a column, a number, a negation, a parenthesised group) and `@N` closes it, so `a / b @ 2 * c` is `(a / b @ 2) * c`; `a / b * c @ 2` is `compute.division-needs-scale` (the first `/` has none). *Read from the build's gates.*

### 3.1 Types

An operand is **`:int`** or **`:dec(S)`** (S in 0..18), read by the existing readers (`cellnum.parse_int`, `cellnum.parse_dec`, `tools/table/cellnum.cho`),
so `1.5` and `1.50` are one value and a cell with more than S fractional digits is `value.decimal-scale`, as in `--where`. An untyped column is
`compute.untyped-operand` (no guessing: `README.md`'s rule); a number written in the expression is typed by what it says (`12` is an int, `1.25` a
`dec(2)`, up to 18 fractional digits). **`:float` is refused** (`compute.float-operand`): a double is rounded by every operation, and the tool's float
is "the exact sum, rounded once" (`docs/filter.md`), which a product of doubles is not. *Read from 8.3; kept.*

### 3.2 Results are exact and printed in full

A value is a signed integer with a scale (an `:int` has scale 0). `+` and `-` give scale `max(S1, S2)`, `*` gives `S1 + S2`; a scale above **18** is
`compute.scale-too-large` (the caller's decision to round; this tool does not round except in 3.3). The intermediate and final magnitude is held in
**128 bits and must stay below 2^127**; leaving it is `compute.overflow`, naming the definition and the row (it is never wrapped or rounded). A result is
printed as a plain decimal, no exponent, with exactly its scale's fractional digits (`12.50`, not `12.5`): the scale is a property of the *types*, not of
the row, so a column has one shape. *Choice.*

### 3.3 Division

`a / b @ N` divides and rounds **half to even** to scale `N` (0..18) — the same rule as `mean:COL@N` (`docs/numbers.md` 3.6), the one place the tool already
rounds, and the scale is explicit because a quotient has no finite decimal. Without `@N` it is `compute.division-needs-scale` (repair: add `@N`). A zero
divisor is `compute.divide-by-zero` for that row (no `null`, no `inf`). *Choice; reading `@N` from the mean is the only reuse.*

### 3.4 Cells

An empty cell is a refusal of its type (`value.not-integer` / `value.not-decimal`, the codes `--where` uses), with `detail.row` and `detail.column`.
There is no `null`, no propagation: one bad cell stops the run with the first row that has one (`--na`, if given, is the reader's, as today). *Read.*

## 4. Surface

| flag | meaning |
|---|---|
| `--compute DEFS` | the definitions of section 3; **repeatable** only through `;` |
| `--format csv\|json` | as today (`table.v2` JSON: `columns` has the new names last) |
| input | a FILE or `--stdin`, CSV/TSV or `--json-lines` as today |

`--compute` is **refused** (standard input is *not* among them: it works) beside any flag that shapes rows or groups — `--select`, `--where`, `--group`, `--agg`, `--order-by`, `--join`, `--rename`,
`--query` — as `compute.unsupported-combination`, with the repair "run the operation in a second stage": an argv pair joined by `|`. Not built because it
cannot be done without editing the loops of section 2; **a decision to be revisited only with G-COST in hand.** `--limit/--from/--max-rows/--max-bytes`
apply (they are the row page, one meaning). Introspection (`table describe`), the skill text and the MCP tool list are generated from the flag and rule
tables, so the flag and the rules are added there and the generators re-run (`scripts/manifest.py`, `scripts/schemas.py`, `publish_numeric --check`).

### 4.1 Where it plugs in (read on 14 Nov 2026, not yet built)

`read_any` (`tools/table/table.cho`) already dispatches to a reader per input kind (`jnrun.read`, `jsonl.read`, `read_stream`, `read_file`, the `_opt` variants), each returning the
same tuple (`Counts`, header, rows, names, ends, errors). The stage is **one more reader**, `compute.read`, selected when `--compute` is given, and it has no effect on the
others. The plan it runs is `--rows` (all columns, no condition), which `inputs.from_flags` sets when `--compute` is present. Its trick, so that **no emit code changes**:

1. The header's names and ends are **extended with the computed names** before `frame.prepare`, so the frame (selection, labels, the CSV header line, the JSON `columns`) already
   has `n + k` columns; `a[k_columns()]` stays the real `n`, so `reader.fields` and the ragged-row check are the file's.
2. Per row, after `reader.fields`, each definition is evaluated (`cexpr.eval`) and its text is appended to a copy of the record; the extra cells point into the copy.
   `engine.process_rows` then writes the row exactly as for `--rows`: pages, budget, `--limit/--from`, CSV and JSON are the existing code.
3. A refusal of a cell or of the arithmetic is recorded in the tally and turned into its rule by `finish.explain`'s neighbour, with the definition, the column and the row, never the cell.

Cost: a copy of the line and a few appends per row, on the stage's own loop. The existing loops are not edited, which G-COMPAT measures.

## 5. Rules (every refusal has a tag)

`compute.syntax` (offset, what was expected) · `compute.unknown-column` · `compute.ambiguous-column` · `compute.duplicate-name` · `compute.untyped-operand` ·
`compute.float-operand` · `compute.scale-too-large` · `compute.division-needs-scale` · `compute.divide-by-zero` · `compute.overflow` ·
`compute.too-long` · `compute.unsupported-combination`. Existing: `value.*` for cells. No payload (no cell) is in any message: a refusal names the
definition, the column and the row number only.

## 6. The arithmetic core (the first piece, as 8.3 said)

`tools/table/cmpv.cho`: a value is a sign and a magnitude in **six limbs of 30 bits** (180 bits of room; the 2^127 bound is checked after each operation).
`add`, `sub`, `mul` (schoolbook; a limb product is below 2^60 and a carry below 2^31, so the checked 64-bit arithmetic of the language never traps),
`div_round_even(a, b, shift)` (long division by a limb vector, rounded half to even), and `put` (divides by 10^9 in place, limb remainder times 2^30 below
2^60). *Reading:* `dec.mul_small` (`dec.cho:126`) multiplies limbs by a small number and `dec.put_sum` prints a pair accumulator documented for sums below
2^93, so neither covers a 126-bit product; this module is new and does not borrow their limits.

## 7. Gates (written before the code, each able to fail)

* **G-ORACLE.** `tests/conformance/compute_ref.py`, Python integers (sharing no code with the tool), generates random expressions over random typed tables
  (negative values, scale 0..18, 18-digit cells, products reaching the bound, divisions at ties) and compares **stdout, stderr and exit status** with
  the tool's, refusals included.
* **G-CORE.** The 128-bit core alone against Python for 1,000,000 random operand pairs per operation and the edges (0, ±1, 2^127-1, 10^18, ties of
  half-even for every sign), through a small test program of `cmpv`.
* **G-MUT.** `scripts/compute_mutants.py`: mutants of the program (carry dropped, sign of a difference, half-even replaced by half-up and by truncation,
  precedence of `*` and `+`, scale of a product, the 2^127 bound off by one, a missing zero-divisor check, unary minus, the header order, the JSON `columns`)
  that **the gates must kill**; a survivor is explained or the gate is extended.
* **G-COMPAT.** The corpus md5 of `scripts/corpus.py` on this branch equals the one on `main`, and the select path's instruction count (callgrind, budget
  1.01) is unchanged: the claim of section 2, measured.
* **G-FLAT.** Peak resident memory of `--compute 't=price:dec(2)*qty:int;h=price:dec(2)/qty:int@4' --format csv`, measured by the repo's launcher (`tests/conformance/maxrss.c`, as `test_memory.py`; `wait4`'s `ru_maxrss` of a small forked child): **3,500 KB at 100,000 rows and 3,524 KB at 1,000,000 for a file; 3,656 and 3,652 KB for a pipe.** `test_compute.test_memory_is_flat_in_the_rows` holds the growth between 20,000 and 200,000 rows under 1 MB (the launcher's noise is about 0.5 MB either way, and a copy of the rows kept would add about 6 MB). *Correction in place:* the first figure written here, 8.9 MB, was taken from a forked Python script and is that script's own high-water mark (which `maxrss.c` exists to avoid), not the tool's.
* **Speed of the stage: not measured.** Nothing is claimed about it, nor against another tool.

## 8. Slices

| slice | content | done when |
|---|---|---|
| **K0** | this document; the corrections in place (section 9) | merged first |
| **K1** | `cmpv` and G-CORE with its mutants (no grammar) | G-CORE 0 differences, mutants killed |
| **K2a** (built) | `tools/table/cexpr.cho`: parser and evaluator, with `scripts/cexpr_golden.py` (3,041 cases, 0 differences; 3,000 fuzz cases, no trap; 19 mutants, all killed) | G-ORACLE, G-MUT, G-NOPANIC for the module |
| **K2b** | the flag, the rule tags, the stage's read loop, CSV and JSON out | G-ORACLE through the binary |
| **K2b** (built) | the flag, 12 rules, `compute.read` beside `read_file`, usage form, MCP text, docs; `tests/conformance/test_compute.py` and `scripts/compute_mutants.py` (14 of 14 killed) | G-ORACLE through the binary |
| **K3** (built for standard input) | standard input (`bulk.fill_stdin` in the same reader), G-COMPAT and G-FLAT measured (7.1), surface regenerated, AGENTS.md, README, `docs/index.html` | G-COMPAT, G-FLAT, G-SURFACE |
| **K3b** (open) | JSON-lines input: `jsonl.read` has its own record builder; the stage would need its own seam there | not started |

## 9. Corrections made in place

* `docs/or-and-columns.md` 8.3: a pointer at its head says the decision is reopened and that this document is the design; the text under it is **kept** as
  the record of the earlier reasoning (its objections 1 and 3 are what the stage design answers: `sum(price * qty)` becomes two stages; the select loop is
  not edited).
* `README.md` line 38 and line 469 ("No computed column"): rewritten when K3 lands, not before, so that the README never states a flag that does not run.

## 10. Open

* **Whether `--compute` should later be allowed beside `--select`/`--where`** (one pass instead of two): only with G-COST, the select path measured with a
  callee at the 1.01 budget.
* **JSON-lines input** (K3b): refused as `compute.unsupported-combination`. `jsonl.read` holds each object as the CSV record of its cells and has its own loop; the stage's trick (extend the header, append the values to a copy of the record) would be a second copy there, to be designed with its own mutants.
* **Dates** (`ts + 1d`, differences): out; `docs/datetime.md` section 15 says why.
* **Rounding functions** (`round`, `floor`): out; `/ @N` is the only rounding.
