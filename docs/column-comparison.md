# A condition between two columns: a design

> **Status: a design, nothing is built.** It follows [`docs/or-and-columns.md`](or-and-columns.md), whose section 8.3 refused computed columns and wrote the condition for reopening them, and [`docs/numbers.md`](numbers.md). Tags: **[measured]** printed by a command run for this document, **[read]** read in the code or the docs, **[choice]** a decision you can overturn, **[open]** not decided or not checked. The use that prompts it is a real program, the daily report of `alpibrusl/cancho-consulting` (`metasearch-bidding/src/daily`).

## 0. The short version

| question | decision (proposed) | why, in one line |
|---|---|---|
| what is asked | **`--where` may compare two columns of the same row**: `bid:int > @t_bid:int` | the one thing the recorded use needs from `table` and cannot get from the rows it already writes |
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

`COLUMN[:type] OP @COLUMN[:type]`, with `OP` any of `= != < <= > >=` (not `contains`, not `in`). A **bare value stays a literal**, so no existing expression changes meaning (the argument of `or-and-columns.md` 3.2). The right side is marked because a header can be called anything, including `5` or `x`: `a = x` compares with the text `x`; `a = @x` with the column `x`. `@` is the mark because `#N` (the Nth column) is already taken on the left and a `$` is the shell's; a value that starts with `@` is written quoted (`'@x'`), as one with a space already is. `@#3` is the third column. **[open]** the mark itself; any single character the grammar does not use would do.

## 5. Semantics

* **Types.** Both sides are read as the same type: text (bytewise), `:int`, `:dec(S)`, `:float`, `:date`, `:timestamp`, `:datetime`. A suffix on one side and none on the other, or two different types, is refused (`where.compare-types`, with the repair that writes both sides with the type of the left). Two decimals of different scales are not mixed: write the same `S`.
* **Exactness.** Integers, decimals and dates compare as the values they are; floats as doubles, as `--where` does today (`docs/filter.md`). No arithmetic is done, so nothing can overflow.
* **Cells.** Each side is read as its type when the condition is reached, left to right; a bad cell is the refusal of its type with `context: "where"`, an empty cell is refused (no null), exactly as for a literal.
* **Evaluation.** The one rule of `or-and-columns.md` section 5: left to right, stop at the first condition that decides the group.
* **Where it stands.** `--where` and `--join-where` (there, both columns must be the build file's own). After a join, a column of either file, by its name or its prefix. `--query` in a later slice.
* **A column against itself** (`a = @a`) is allowed and means "the cell is a valid value of its type"; it is not a refusal.

## 6. Cost, and the gate written first

A condition is today **7 integers** (kind, type, operator, the column, ...), and kind 0 is "compare with a literal" (`docs/or-and-columns.md` section 1). A is a new kind with a second column index in place of the literal. The loop has two cases and the backlog records that "a third case in the read's loop costs the other two 15 percent" (`docs/backlog.md`). So the gate is the existing one, on the three existing paths: **instructions retired of the AND-only filter, the select and the group-count, at most 1.01 of `main`**, and if a third case costs more, the evaluation goes in a callee behind a case the loop already has, as the sort was. **[open]** not measured: this document is written before any code.

## 7. Rules and bounds

New: `where.compare-types` (exit 2, repair `retry` with both sides typed alike). Changed: `where.syntax` learns the `@`. No new bound: the 64 conditions of an expression are the ceiling, and a column on the right counts as part of its condition.

## 8. Gates, written before any code

1. Every expression valid today gives the same bytes, rows and refusals (the corpus of `docs/or-and-columns.md` section 9).
2. A differential oracle: for random files and random `a OP @b` of each type, the rows kept equal those of a Python filter that reads the same cells; refusals equal the refusal of the same condition with a literal in place of the column.
3. `--threads N` gives the one-core bytes.
4. The use: the three counts of section 1 equal the report's.
5. The instruction gate of section 6.

## 9. Staging

C0 the flag form for rows and groups, one core (the use needs only this); C1 `--query` (`where a > @b` is a grammar change of `docs/query.md`); C2 JSON lines and threads; C3 `--explain` says the plan. Each is a separate pull request with its gate.

## 10. Open questions for the maintainer

1. Is "the count belongs in the query" a requirement (section 3)? If not, close this as not built.
2. The mark for a column on the right (`@`)?
3. Should `--join-where` accept it (a build file's two columns), or only `--where`?
4. Is the instruction budget 1.01 right for a feature the AND-only path does not use?

## 11. What this did not measure

The cost of A in the loop (no code exists); the counts on a 330-day run (the use was measured on a 55-day run and on a 120-day run's scorecard); the MCP schema of `introspect` for the new rule; a build file wider than the memory bound.
