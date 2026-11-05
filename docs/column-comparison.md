# A condition between two columns: a design

> **Status: C0 is built** (the flag form for rows and groups, one core or many; the sections below say where the build differs from the design that came first, and each correction is made where the claim was). It follows [`docs/or-and-columns.md`](or-and-columns.md), whose section 8.3 refused computed columns and wrote the condition for reopening them, and [`docs/numbers.md`](numbers.md). Tags: **[measured]** printed by a command run for this document, **[read]** read in the code or the docs, **[choice]** a decision you can overturn, **[open]** not decided or not checked. The use that prompts it is a real program, the daily report of `alpibrusl/cancho-consulting` (`metasearch-bidding/src/daily`).

## 0. The short version

| question | decision (proposed) | why, in one line |
|---|---|---|
| what is asked | **`--where` may compare two columns of the same row**: `bid:int > (t_bid:int)` | the one thing the recorded use needs from `table` and cannot get from the rows it already writes |
| what is not asked | **computed columns, a ratio or a difference as an output or as a sort key, `abs`, division** | the use computes those in 1,048 rows; section 8.3 of `or-and-columns.md` stands |
| output | **unchanged: the cells of the file** | the oracle of every selection test (`docs/select.md`) keeps holding |
| types | **both sides the same type**, exact; mixed types refused | no coercion anywhere in the tool |
| the hot path | **must not get slower: instructions retired, bound 1.01** | the gate that exists (`scripts/gate_regress.py --counter`) |
| first slice | **C0: the flag form, rows and groups, one core** | `--query`, threads and JSON lines follow, each with its own gate |

## 1. The recorded use

The report proposes tomorrow's bids of a group of hotels and says how they differ from today's. For each channel it needs: how many bids, the average bid today and tomorrow, **how many went up, down or stayed**, the sums of the forecast clicks and cost, and the eight largest relative changes.

**What `table` does for it today [measured]** (a 55-day run, day 45, bids of day 46 against day 45; `bids.csv` has 152,030 rows in the larger run):

```
table --where "day:int = 46 and policy:int in (1, 2)" \
      --join bids.csv --join-where "day:int = 45" --on hotel_id:int,channel:int,device:int --join-prefix t_ \
      --join-type left --format csv bids.csv \
 | table --join hotel_arms.csv --join-where "arm = 1" --on hotel_id:int --join-type semi \
         --select hotel_id,channel,device,bid,t_bid,forecast_clicks_milli,forecast_cost_m --format csv -
```

writes 1,048 rows in 0.05 s. Counted outside, per channel, the rows give 262 bids a channel, the averages 637 to 647, 725 to 727, 675 to 672 and 609 to 605, and up/down/same of 172/89/1, 86/94/82, 89/88/85 and 86/88/88: **the same as the report that the program writes** (`tests/golden/daily_45.md`). Two joins through a pipe, a semi join to the group, a left join for the bid that has no yesterday.

**What it cannot do** [measured, run]: `--where "bid:int > t_bid:int"` is `where.syntax` (`docs/or-and-columns.md` line 12 says it is out of the grammar on purpose); and nothing orders by `|bid - t_bid| / t_bid`.

## 2. The options

| | what | for the use | against |
|---|---|---|---|
| **D** | nothing: the client reads the joined rows and counts | works today, shown above | the up/down count is done in the client, over rows it must read |
| **A** | **a condition between two columns** | the up/down/same counts become `table --agg count` with three `--where`s, or one `--group`-less call each | a new kind of condition in the filter's loop |
| **B** | a computed column, one operator (`or-and-columns.md` 8.3) | would give `bid - t_bid`; not `abs`, not the ratio | ends "the output is the input's bytes"; the second operator is the next request |
| **C** | an order key that is a ratio of two columns | the top eight, done in `table` | an expression in a sort key, and 128-bit products (there is no routine, 8.3) |

## 3. Decision [choice]

**Build A, refuse B and C again, and say why in this document.** The top eight by relative change, and the ratio, stay in the client: it has the rows (1,048 in the use, 0.05 s), the work is a presentation of eight lines, and the two exact things `table` is for (the join and the sums) it already does. A is the smallest thing that moves a count the client now does into the tool, and it changes nothing about what the tool writes. B and C are not asked by the use once the rows are available, which was the condition `or-and-columns.md` 8.3 set ("cannot be an aggregate"; here it can be a row).

**[open] the honest weakness.** The use works without A (option D). A is justified only if "the count belongs in the query" is a requirement, as it is for a plan of `table` calls with no program in between (`flow`). If it is not, the right outcome of this document is "do not build", and it should be closed as such.

## 4. The grammar [choice]

`COLUMN[:type] OP (COLUMN[:type])`, with `OP` any of `= != < <= > >=` (not `contains`, not `in`). A **bare value stays a literal**, so no existing expression changes meaning (the argument of `or-and-columns.md` 3.2). The right side is marked because a header can be called anything, including `5` or `x`: `a = x` compares with the text `x`; `a = (x)` with the column `x`; `(#3)` is the third column.

**Corrected on building.** The first draft of this section marked the column with `@` (`a = @x`) and said a value that starts with `@` is written quoted. That is false of the tool as it stands: `a = @x` is valid today and means the text `@x`, so the mark would have changed an expression that was valid, against gate 1. [measured] The mark is now a pair of parentheses, which cannot start a value: a `(` where a value goes has been `where.syntax` ("a value", expectation 3) since `--where` was written, and a bare word stops at one, so `a = (x)` was refused and `a = @x` still compares with the text `@x` (`tests/conformance/test_column_comparison.py`, `test_a_value_that_starts_with_a_mark_is_still_a_literal`). `in (...)` is its own operator and is untouched.

## 5. Semantics

* **Types.** Both sides are read as the same type: text (bytewise), `:int`, `:dec(S)`, `:float`, `:date`, `:timestamp`, `:datetime`. A suffix on one side and none on the other, or two different types, is refused (`where.compare-types`, with the repair that writes both sides with the type of the left). Two decimals of different scales are not mixed: write the same `S`.
* **Exactness.** Integers, decimals and dates compare as the values they are; floats as doubles, as `--where` does today (`docs/filter.md`). No arithmetic is done, so nothing can overflow.
* **Cells.** Each side is read as its type when the condition is reached, left to right; a bad cell is the refusal of its type with `context: "where"`, an empty cell is refused (no null), exactly as for a literal.
* **Evaluation.** The one rule of `or-and-columns.md` section 5: left to right, stop at the first condition that decides the group.
* **Where it stands.** `--where`, on the joined row after a join (a column of either file, by its name or its prefix), and not pushed down before the join: the leading run of text conditions of `docs/joins.md` section 18 stops at it, since its two columns may be on the two sides. **`--join-where` refuses it** (`where.syntax`, "a comparison of two columns goes in --where, after the join"), which settles question 3 of section 10 for C0. `--query` in a later slice.
* **A column against itself** (`a = (a)`) is allowed and means "the cell is a valid value of its type"; it is not a refusal.

## 6. Cost, and the gate written first

A condition is today **7 integers** (kind, type, operator, the column, ...), and kind 0 is "compare with a literal" (`docs/or-and-columns.md` section 1). A is a new kind with a second column index in place of the literal. The loop has two cases and the backlog records that "a third case in the read's loop costs the other two 15 percent" (`docs/backlog.md`). So the gate is the existing one, on the three existing paths: **instructions retired of the AND-only filter, the select and the group-count, at most 1.01 of `main`**, and if a third case costs more, the evaluation goes in a callee behind a case the loop already has, as the sort was. The paragraph below says what was measured once the code existed.

**Built, and measured** (the instruction gate of `scripts/gate_regress.py --counter`, three builds of `main`'s sources against three of these, instructions retired, the 1,000,000-row benchmark file, one thread and four): **all 42 cells between 0.998 and 1.004 of `main`, bound 1.01, G6 pass.** That is the AND-only filter, the select and the group-count among them. It holds because the loop is not touched: a plan with a comparison of two columns always gets the branch program of `or` and `not` (`orparse.parse`, "flowed"), and `expr.eval`'s one test of `flowed` sends it to `eval_program_columns`, a copy of `eval_program` that knows the kind; the first draft's alternative, a test of the kind of every condition inside `eval_program`, cost the `or` and `not` paths 0.8 to 1.9 percent (measured, 1,835.7 to 1,827.8, 2,309.0 to 2,353.4 and 1,850.8 to 1,878.8 million instructions), and the split gives them -1.3, +0.4 and +0.4 percent (1,841.5 to 1,818.0, 2,309.1 to 2,318.5, 1,850.8 to 1,858.8; the cost is the parity of the length of the program, which `query.has_columns` reads: a plan with a comparison of two columns has one element more than two for each condition). The new condition itself, over the same file [measured, one run each]: `status:int > 100 and bytes:int > 200` 1,990.6 million instructions, `status:int > 100 and bytes:int > (status:int)` 2,243.6, `bytes:int > (status:int)` 2,010.3: a right cell read is about 250 instructions a row, which is a cell read as an integer.

## 7. Rules and bounds

**No new rule tag.** The first draft proposed `where.compare-types`; the build uses `where.syntax` with the sentence "the same type on both columns of a comparison, written alike" (`detail.expected`), because the mismatch is a fault of the expression's text, as an unknown suffix is, and a rule carries a fixture, a schema, an MCP row and a line of `docs/refusals.md`. It can be split out if a repair is wanted (writing both sides with the type of the left); none is offered today. A bad cell is the refusal of its type with `context: "where"` and `detail.column` the column of the cell that failed, the right one when it is the right; the repair that rewrites a `:dec(S)` to a larger scale is not offered for this condition (it would change one side only). No new bound: the 64 conditions of an expression are the ceiling, and a column on the right counts as part of its condition.

## 8. Gates, written before any code

1. Every expression valid today gives the same bytes, rows and refusals (the corpus of `docs/or-and-columns.md` section 9).
2. A differential oracle: for random files and random `a OP (b)` of each type, the rows kept equal those of a Python filter that reads the same cells; refusals equal the refusal of the same condition with a literal in place of the column.
3. `--threads N` gives the one-core bytes.
4. The use: the three counts of section 1 equal the report's. **Met** [measured]: with `--where "bid:int > (t_bid:int)"`, `< (...)` and `= (...)` on the joined rows of the pipe of section 1, per channel 0 to 3, the counts are 172/89/1, 86/94/82, 89/88/85 and 86/88/88, the report's; the two joins and a group count of the same pipe take 0.02 s.
5. The instruction gate of section 6. **Met** (section 6).

What was built against them: `tests/conformance/test_column_comparison.py` (15 tests: the oracle over the seven types and six operators on 400 random rows each, threads 2, 4 and 16 with chunks of 1, 7 and 64 bytes, a bad cell on either side for four rules, quoted cells, the forms that are refused, `@x` still a literal, `not`, `or`, groups, a join with the comparison on the joined row and not pushed down, `--join-where` refusing it, standard input, `--explain`, and the cases of the earlier corpus that read differently), `scripts/column_mutants.py` (25 mutants, all killed; the first run killed 22, and the three survivors were a hole in the tests: a bad float cell, a quoted right cell and a join that pushed the condition down), and the references of the earlier slices kept as they were (`not_a2.a2_grammar()` is the reading without the form). Of the 5,674 cases of the corpus of the earlier slices, 21 read differently, and every one has a `(` after an operator; the other 5,653 are held to the bytes of `main` as before. Six of the 21 were in the list of A2's changes, which lost exactly those six (`tests/conformance/not_a2_compat_changes.json`). The conformance suite otherwise passes; `test_toolbox_pin.test_every_rule_a_fixture_reaches_has_a_hint` fails on `main`'s binary too (3 failures there, 1 here) and is not touched.

## 9. Staging

C0 the flag form for rows and groups (**built**, and with threads too: every path goes through the one evaluator); C1 `--query` (`where a > (b)` is a grammar change of `docs/query.md`); C2 JSON lines; C3 `--explain` says the plan. Each is a separate pull request with its gate.

## 10. Open questions for the maintainer

1. Is "the count belongs in the query" a requirement (section 3)? If not, close this as not built.
2. ~~The mark for a column on the right (`@`)?~~ Answered by the build: `( )`, because `@x` was a valid value (section 4).
3. ~~Should `--join-where` accept it?~~ Refused in C0 (section 5); it can be opened later for two columns of the build file.
4. ~~Is the instruction budget 1.01 right?~~ It was met with room: the gated cells moved by at most 0.4 percent (section 6).

## 11. What this did not measure

The counts on a 330-day run (the use was measured on a 55-day run and on a 120-day run's scorecard); a comparison on a file wider than the memory bound; `--query` (C1) and JSON lines (C2), which are not built; the time of the new condition as a clock (instructions retired only); and a Linux run of the instruction gate (the figures are from macOS).
