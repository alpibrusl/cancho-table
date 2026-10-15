# `or` in a filter, and renamed or computed output columns: a design

> **Status: a design; its recommendations are accepted by the maintainer as written; slices A0 and A1 (section 11) are built, nothing else is (section 15 has the notes of A0, section 16 those of A1).** It follows the rules of [`docs/numbers.md`](numbers.md) and [`docs/query.md`](query.md): decisions with their reasons, the gates written before the code, every claim about the program checked against the code (file and line, at `origin/main` `072ce15`) and every number either measured (with its conditions) or marked *estimate* or *choice*. The two features are the ones the README lists under "What it cannot do yet" (`README.md:353-354`): **(A)** `or` (and the parentheses and `not` that make it usable) in `--where` and in `--query`, and **(B)** renamed and computed output columns. The questions for the maintainer are in section 13, each with a recommendation.
>
> The two features are independent and are designed so that they can be built, or refused, one without the other. (A) is the larger, and it touches the hot path of every filter. (B) is small if it stops at renaming, and the section on it argues that it should.

## 0. The short version

| question | decision (proposed) | why, in one line |
|---|---|---|
| what `or` is for | **several alternatives for rows to be kept**, on one column or on several, without leaving the tool | `a in (...)` already says "one column, several values"; nothing says "this or that column" |
| what it is not | **not a boolean expression language**: conditions, `and`, `or`, `not`, parentheses, nothing else; no `xor`, no `true`/`false`, no `case`, no condition between two columns, no `like`, no `between`, no `null` | each of those is a different feature with its own rule (sections 2, 8) |
| `and` mixed with `or` | **refused unless parenthesised** (`where.mixed-and-or`, with a repair that offers both readings) | the one place where an agent and a person read the same text differently; a refusal can be relaxed later, an answer cannot be taken back |
| plan representation | **the conditions stay as they are (7 integers each); a second vector, `flow`, gives each condition the next condition to run when it is true and when it is false** (a forward-only branch program). Empty for a plan with only `and`, which then runs the code it runs today | exact short-circuit with no distribution; `not` costs nothing; no bound on the *shape*, only on the 64 conditions that are already the ceiling |
| DNF? a tree? | **neither** (section 4): DNF changes which cells are read when there are parentheses, and can square the size; a tree needs a stack or recursion in the per-row loop | measured on a model: DNF refused differently from the source in 1,730 of 6,000 random expressions |
| evaluation | **left to right, stop at the first condition that decides the group it is in**, the one rule of today (`docs/filter.md:90-95`). A cell is read as its type only when the evaluation reaches the condition; a bad cell that is not reached is not an error, exactly as in `bytes != '' and bytes:int > 5` | the alternative (read every typed cell of the row) breaks the guard that the docs teach |
| new rules | `where.mixed-and-or` and `query.mixed-and-or` (2, repair `choose`); `where.syntax` and `query.syntax` get new `expected` words; the `or`, `parentheses` and `not` features of `query.unsupported` go away | section 7 |
| new bounds | **none that is a flag.** The 64 conditions and the 1,024 values of an `in` are the ceilings of today; one new constant, **8 levels of parentheses** (a choice) | the shape of the expression is bounded by its 64 leaves |
| the AND-only path | **must not get slower: instructions retired, bound 1.01** (the gate that exists, `scripts/gate_regress.py --counter`) | section 10 |
| (B) rename | **yes**: `--rename TARGET=NEW,...` and, in a query, `select a as b`. Output labels only; one place in the code (`frame.prepare`) | it closes a real gap: the repair of `output.duplicate-column` has nowhere to go today |
| (B) computed columns | **no, not now.** Not literals, not concatenation; arithmetic only as an appendix of exact rules and the conditions under which to reopen | it is the first output that is not bytes of the input; it does not answer the question people ask (`sum(price * qty)`); the select loop is the hot path |
| first slice | **A0: the repair `a = 1 or a = 2` to `a in (1, 2)`**, front end only, no engine change | the commonest `or`, and the item the backlog already lists |

## 1. What is built today (checked against the code)

* **`--where` is a conjunction.** The grammar is `COND { "and" COND }` (`docs/filter.md:27-34`; `tools/table/expr.cho:5-21`). The parser is one loop: a condition, then `and` or the end (`expr.cho:442-461`); any other word there is `where.syntax` with the expectation "and, or the end of the expression" (`expr.cho:113-114`, `expr.cho:458-459`). Run on the Mac with the build of `fb4098d`: `--where "a = 1 or a = 2"` answers `where.syntax`, `detail.offset` 6, exit 2, repair `none`.
* **At most 64 conditions and 1,024 values in one `in`**, both as `where.syntax` with the words of expectation 11 (`expr.cho:381`, `expr.cho:423-426`, `expr.cho:135`).
* **`(` and `)` end a bare word** (`expr.cho:37-39`), and a `(` where a column should begin is `where.syntax` ("a column", `expr.cho:203-204`). So every string with `or`, a parenthesis or a `not` after a condition is an error today, and a column called `or` or `not` is an ordinary column at the start of a condition (measured on the same build with a header `or,not,in`: `or = 1`, `not in (x)`, `not = x` and `or = 1 and not in (x)` are valid and answer; `(or = 1)`, `not (or = 1)` and `or not in (x)` are `where.syntax`): *any change that only gives a meaning to what is an error today cannot change a valid call* (section 3.2).
* **The plan.** `Query` has ten fields (`query.cho:36-49`); `conds` holds **7 integers a condition**: kind (0 compare, 1 contains, 2 in), type (0 text, 1 `:int`, 2 + S `:dec(S)`, 21 `:float`), operator, the index of the column name, the first literal and how many, and the offset in the expression (`query.cho:17-20`, `query.cho:340-352`, stride in `cond_at`, `query.cho:362-364`). All the conditions are conjunctive; nothing in the structure says how they combine. `meta[1]` is their number (`query.cho:92-100`).
* **Evaluation.** `expr.eval` walks `k = 0 .. n-1` and returns at the first condition whose verdict is not 1 (`expr.cho:601-627`; the return is `expr.cho:621-623`). A verdict is 1 yes, 0 no, 2 to 11 a typed cell that is not of its type (`expr.cho:519-523`). Conditions to the right of a false one are never looked at, so their cells are never read as numbers. That is documented as a feature: "`bytes != '' and bytes:int > 5` never reads an empty cell as an integer" (`docs/filter.md:90-95`).
* **One function asked.** The rows are asked through `engine.screen` (`engine.cho:408-412`), called from three places: `process_rows_buf` (a selection, `engine.cho:448`), `process_groups` (a grouping, `engine.cho:490`) and `group_fast` (the groupings' in-place path, `engine.cho:513`, which also screens the rows of `--order-by` and `--report types`: `order_row` says so at `engine.cho:575`). `screen` returns at once when there is no condition (`engine.cho:409`).
* **A refusal in a condition is the first in file order**, at every `--threads`: a range in which anything aborted is not taken from the thread but read again by the parent, sequentially (`par.cho:168`, `par.cho:1305`; `docs/parallel.md` lines 4-8). A range that began on a wrong guess about quotes is also read again (`docs/parallel.md` section 2). Nothing in either depends on what a condition is.
* **The query front end** reads a `where` as a flat list of condition records (`qparse.cho:432-531`). `or` and `(` after a condition are `query.unsupported` with the features `or` and `parentheses` (`qparse.cho:521-524`, `qparse.cho:688-690`, codes 71 and 72 in `qtext.cho:150` and `153`), `not` (before `in`) is the feature `not` (`qparse.cho:514-515`, `qtext.cho:165`), and `as` after a select item is the feature `alias` (`qparse.cho:368-370`, `qtext.cho:132`). The translator writes the conditions as one `--where` text joined by `" and "` (`qtrans.cho:409-423`), and a query's text is at most 4,096 bytes. The words `or`, `not` and `as` are already reserved (`docs/query.md:72`), so a column of that name is `"or"` in a query.
* **Output labels are made in one place.** `frame.prepare` writes the header of a selection from the header's names (`frame.cho:150-160`) and the labels of a grouping from the group columns' names and `count`, `sum:NAME`... (`frame.cho:161-190`), quoting for CSV in `put_label` (`frame.cho:77`). `--sort` names one of those labels (`finish.cho:872-880`), and `--format jsonl` refuses two equal labels (`output.duplicate-column`, `finish.cho:915-935`, with repair `none`). In CSV a repeated label is allowed: `--select a,a` answers `a,a` and `--group b,b --sort b` answers (measured on the same build).
* **`--explain`** prints the conditions as `"where": [{"column", "type", "op", "values"}]` in condition order (`explain.cho:539-565`), and notes a number compared as text (`explain.cho:795-808`). The schema of that object is generated (`scripts/schemas.py:130-134`).
* **What the flag help says** is `tools/table/spec.cho:10` ("conditions joined by and (no or, no parentheses)").

## 2. (A) What `or` is for, and what it is not

**For.** A filter whose alternatives are not values of one column: "errors or slow requests" (`status >= 500 or ms:int > 2000`), "either field is empty" (`email = '' or phone = ''`), "not these two statuses and not that path" (`not (status in (200, 304) or path contains '/health')`). Today the only way is two calls and a merge by the caller, which loses the file order the tool guarantees and, for a grouping, the counts. For several values of *one* column `in` already exists and stays the better spelling (it reads a typed cell once; section 5.3).

**Parentheses and `not` come with it** because without parentheses `or` is a language of "or of ands" only, and `(a or b) and c` is the first thing a person types. `not` is justified on its own by one gap: there is **no way today to say "does not contain"**, and "not in" is only `!=` repeated. With a branch program (section 4) negation costs nothing in the engine.

**Not, each with the rule that says so:**

| not in scope | what happens | why |
|---|---|---|
| `xor`, `true`, `false`, `case`, `if` | `where.syntax` (a word that is not a column or an operator) | a condition is a comparison of a column with literals |
| a condition between two columns (`a = b`) | stays refused (`where.syntax`; `query.unsupported: column-comparison`) | the literal of a typed condition is read once at parse time (`expr.cho:314-366`); two cells would need the type of each |
| `like`, `between`, `is null` | stay refused | `contains`, two conditions, and "an empty cell is the empty text" (`docs/query.md:75-87`) |
| a value computed from a condition, a score, a weight | never | a filter keeps or drops a row |
| different precedence from SQL's | not offered: the mix is refused (section 3.3) | explicit over conventional |
| `or` between `--where` and another flag (`--where` twice) | `args.*` as today | one expression per call |
| an `or` that reorders conditions for speed | never | the order of evaluation is the order of the text (section 5) |

## 3. (A) The grammar

### 3.1 The flag form, `--where`

```
EXPR   := ALL | ANY | UNIT
ALL    := UNIT "and" UNIT { "and" UNIT }
ANY    := UNIT "or"  UNIT { "or"  UNIT }
UNIT   := COND | "(" EXPR ")" | "not" "(" EXPR ")"
COND   := COLUMN OP VALUE | COLUMN "contains" VALUE | COLUMN "in" "(" VALUE { "," VALUE } ")"
        | COLUMN "not" "contains" VALUE | COLUMN "not" "in" "(" VALUE { "," VALUE } ")"
OP, COLUMN, VALUE, WORD   as today (docs/filter.md:27-34)
```

* **A level is all `and` or all `or`.** `a and b or c` is not in the grammar: it is `where.mixed-and-or` (section 3.3). Today's expressions (`COND { "and" COND }`) are `ALL` or `UNIT`, so **every call that is valid today is valid and means the same**.
* **`not` before a condition is not offered in the flag form** (`not a = 1`): it would need a second token of lookahead to tell it from a column called `not` (`not in (1)` is that column today, and stays so). The three spellings that are offered cannot be mistaken for a column: `not (` (a `(` after a word that is not an operator is an error today), and `COLUMN not in`, `COLUMN not contains` (a word that is not an operator after a column is an error today). `not a = 1` is `where.syntax` at the `a` with the expectation "( after not", and the repair is the rewritten expression when it has an exact one (`a != 1`; `not a < 1` to `a >= 1`; `not a in (...)` to `a not in (...)`; `not a contains 'x'` to `a not contains 'x'`). The complements are exact for text (a total order, bytewise) and for the typed comparisons (the cell is read the same way whichever operator follows it, so a bad cell is refused in both).
* **`not in` and `not contains` on a typed column**: `contains` stays a syntax error for a typed column (`expr.cho:300-302`); `not in` is allowed (it is `in`, swapped).
* **`or` and `not` are keywords only where the grammar expects them**, as `and`, `in` and `contains` are (`expr.cho:17-19`): after a complete unit, `or` is the connective; at the start of a unit, `or` is a column; a quoted `'or'` is never a keyword.
* **Depth.** At most **8** nested parentheses (a *choice*: the parser recurses once per level, and 8 is more than a person writes; `par.fan` already recurses 64 deep, `docs/parallel.md` section 1, so the language does not forbid it). A ninth is `where.syntax` with the expectation "fewer nested parentheses: the ceiling is 8".
* **Leaves.** The ceiling of 64 conditions is the number of leaves, wherever they are (`expr.cho:381`). `not (a or b)` is two.

### 3.2 Why the grammar changes nothing that is valid today

Every string the new rules accept and the old ones do not fails today at one of: an `or` after a complete condition (`expr.cho:458`), a `(` where a column starts (`expr.cho:203`), a word that is not an operator after a column (`expr.cho:309`). The new meanings are given to exactly those places. The gate in section 9 (**G-COMPAT**) checks it, rather than leaving it as an argument: the corpus of valid expressions (the 41 plans of `scripts/corpus.py`, the generated plans of `test_plan.py`) must give the same bytes, and a generated set of *near* strings (a column called `or`, `not`, `and`, `in`, `contains`, each at each position, quoted and not) must parse as before.

### 3.3 `and` and `or` at one level: refused

`a = 1 or b = 2 and c = 3` is `where.mixed-and-or`: exit 2, `detail.offset` at the connective that changes the level (the `and`), `detail.expression`, and a repair of kind `choose` with whole invocations (the same `argv`, `fail.choose_option_replacing`, as `select.unknown-column` offers, `docs/select.md`) for the two readings:

1. by precedence, `and` before `or`, as SQL does: `a = 1 or (b = 2 and c = 3)`;
2. left to right: `(a = 1 or b = 2) and c = 3`.

This is the one place where the text can be read two ways by two careful readers, and the tool's rule is to refuse where it would otherwise guess. The cost is one retry for an agent that writes SQL's precedence, and the retry is a repair it can run unchanged. The benefit is permanent: **a refusal can later be turned into an answer (precedence chosen) without breaking a call; an answer can never be turned back into a refusal.** The query form says the same with `query.mixed-and-or`. (Open question 1: this is the least obvious recommendation of the document.)

### 3.4 The query form

The grammar of `docs/query.md:42-67` changes in `conds` only. The query accepts the SQL surface, and the translator writes the flag grammar:

```
conds   = orlist
orlist  = andlist { "or" andlist }            (* a mix at one level is query.mixed-and-or, as above *)
andlist = unit { "and" unit }
unit    = "not" unit | "(" orlist ")" | cond
cond    = typed cmp value | column "contains" value | typed "in" "(" value { "," value } ")"
        | column "not" "contains" value | typed "not" "in" "(" value { "," value } ")"
```

* The words `or`, `not`, `and` are keywords in any case and are already reserved (`docs/query.md:72`); a column of that name is `"or"`, so **there is no ambiguity that the flag form has**, and `not a = 1` (valid SQL, `not` binds looser than a comparison) is accepted and translated to `not (a = 1)`. The translator does not simplify (`not (a = 1)` stays, not `a != 1`): it writes what was said, so that the gate compares text with text.
* The translation renders the tree with the fewest parentheses the flag grammar allows, an `or` or `and` inside the other always in parentheses. It is deterministic, and `--explain` prints the plan, not the text (section 6.3).
* **The offsets and the literals.** The front end checks the literals of a typed condition itself and maps an engine refusal back to the query (`qtrans.cho:110`, `qtrans.cho:452`). Because the flag text is built by concatenation, the map keeps working; the pinned cases `query_cases.py:53`, `:62` and `:127` (the features `or`, `not`) become answers, and `parentheses` is dropped from the features.
* **Where a query's `order by` is concerned** (`order by a or b`, `query_cases.py:127`) nothing changes: it stays a syntax error at `or`.
* `limit`, `offset`, `group by` after a `where` are not affected: `or` ends nothing.

## 4. (A) The plan: how `conds` generalises

### 4.1 The proposal: a branch program

Keep `conds` exactly as it is (7 integers a condition, in the order the leaves are written). Add **one field to `Query`: `flow`**, a vector of **2 integers per condition**: the condition to run when this one is true, and the one to run when it is false. A target is either a condition number *greater than this one's*, or `-1` (the row is kept) or `-2` (the row is dropped).

```
EXPR                            conds (in text order)   flow (if true, if false)
a = 1 and b = 2                 0:a 1:b                 -  (empty: the plan is a conjunction)
(a = 1 or b = 2) and c = 3      0:a 1:b 2:c             0: (2, 1)   1: (2, -2)   2: (-1, -2)
not (a = 1 or b = 2)            0:a 1:b                 0: (-2, 1)  1: (-2, -1)
x not in (1, 2) and y = 3       0:x 1:y                 0: (-2, 1)  1: (-1, -2)
```

* **A plan with only `and` has an empty `flow`** and goes through the code that exists. Nothing about it changes except the one test for "is `flow` empty" (section 10).
* **The evaluator** is a loop with a program counter: `pc = 0`; evaluate condition `pc`; a verdict of 2 or more is the refusal (the same `holds`, the same codes, `expr.cho:523-594`); otherwise `pc` becomes the true or the false target; stop at `-1` or `-2`. Targets only go forward, so it ends in at most as many steps as there are conditions.
* **Compiling** is one recursive pass over the parsed tree with two "where next" arguments: a leaf gets (on true, on false); `A and B` compiles `A` with (entry of `B`, on false) and `B` with (on true, on false); `A or B` the dual; `not X` compiles `X` with the two exits swapped. **A leaf appears once**, so the program has exactly as many steps as the expression has leaves (the 64 ceiling), whatever the nesting.
* **`not` is a swap**, not a flag on the condition and not a new operator code: `holds` never sees it, so the typed refusals, the `in` list and the `contains` byte search are untouched.
* **The 7 integers are not widened**, so `typed_mention` and `type_conflict` (`query.cho:247-317`), which scan every condition without regard to how they combine, work as they are: `column.type-conflict` is checked across all the leaves, as the docs state ("one numeric type per column in one plan"). `explain.cho:399-420` (the refusals a plan can raise) and `finish.cho:815-835` (`err_cond`, the offset a `:dec` repair rewrites) index conditions by number, which a program keeps.
* **The ripple.** A new field means every place that builds or takes a `Query` apart changes: 39 lines contain `Query {` (35 in `query.cho`, 3 in `jout.cho:50-52`, 1 in `plan.cho:300`), plus `duplicate` (`query.cho:432`) which each thread calls. All are mechanical. The alternative, keeping `flow` in the tail of `meta` as `with_page` keeps `--top` (`query.cho:160-176`), touches none of them and is the pattern the file already uses, at the price of a position that is only known by counting. *Choice:* the field, because `with_page` was itself a workaround that cost an explanation in a comment (`query.cho:160-161`).

### 4.2 Against DNF

A DNF is a list of conjunctions, one `conds` run per disjunct, with a bound on both numbers (the shape the question suggested). It is the right answer if the grammar has no parentheses (`ANY` of `ALL`s), and it is **not** if it has them:

* **Distribution changes what is read.** `(a or b) and c` distributes to `a and c or b and c`. On a row where `a` is true and `c` is false, the source stops at `c`; the DNF goes on to `b`, and if `b` is a typed condition with a bad cell, the DNF refuses a row the source answered. The model of the appendix (not the tool: a Python model of leaf statuses 0 false, 1 true, 2 bad cell) generated 6,000 random expressions of 3 to 7 leaves and every status of their leaves: **the distributed form refused differently from the source in 1,730**, and gave a different boolean in 0 when no cell was bad. For the 1,104 of the 6,000 that are already a DNF as written, it never differed. So a DNF is exact only for the grammar without parentheses. To keep it with them, the *semantics* would have to be defined as the DNF's, and a person's reading of `(a or b) and c` would stop being what the tool does.
* **Distribution multiplies the size.** `(a1 or .. a4) and (b1 or .. b4) and (c1 or .. c4)` is 12 leaves and 64 conjunctions of 3, **192 leaves**. A bound on the expanded size would refuse an expression of 12 conditions for a reason the person cannot see in the text.
* **It evaluates repeated conditions repeatedly**, once per conjunction they were copied into.
* **What a DNF would have as a bound**, for the record: a number of disjuncts (say 16) and a total of leaves (64, the same ceiling) with the tags `where.or-too-many` and the existing 64-condition words. The branch program needs neither tag.

### 4.3 Against an expression tree

A tree (a node vector with a kind and two children, evaluated by recursion or by a stack) is the general answer and is as exact as the program: it visits the same leaves in the same order. It is rejected for three reasons that are about this tool and not about trees: (1) the per-row loop would carry a stack, or recurse, in the one function every filtered row passes through, and `docs/backlog.md:75-81` records what an extra case in that loop cost ("15 percent") and the instruction-count budget is 1.01; (2) the generality is not used, since a condition has no value but true or false and the only connectives are the three; (3) the program *is* the tree flattened: the model of the appendix compiled 4,000 random trees to programs and ran every status of their leaves, **5,030,376 cases, 0 differences in the answer or in the sequence of leaves visited**. If a value-carrying tree is ever needed (section 8.3, arithmetic), it is a different structure for a different job.

### 4.4 What the program costs in memory

`flow` is 2 integers a condition: 128 integers (1 KiB) at the ceiling, copied once per thread by `duplicate` like the rest of the plan (`query.cho:432`). The `lits` are as before: at most 64 conditions of at most 1,024 values.

## 5. (A) Evaluation, refusals and the typed columns

### 5.1 The rule

**The conditions are tried in the order they are written; a condition is tried only when the run reaches it; a typed cell is read as its type only when its condition is tried.** For `and` that is today's rule. For `or` it is the same rule seen from the other side: the first alternative that is true ends the row.

Consequences, stated so that they can be tested:

| expression | a row where | result |
|---|---|---|
| `c != '' and c:int > 5` | `c` is empty | dropped (the guard stops the evaluation); as today |
| `c = '' or c:int > 5` | `c` is empty | **kept**; the second condition is not tried. The guard of an `or` is the positive test |
| `c:int > 5 or c = ''` | `c` is empty | **refused**, `value.not-integer`: the typed condition is tried first |
| `a = 'x' or c:int > 5` | `a` is `x`, `c` is `abc` | kept: `c` is never read |
| `a = 'y' or c:int > 5` | `a` is `x`, `c` is `abc` | refused at `c`, the row and the column named as ever |
| `(a = 'x' or b = 'x') and c:int > 5` | `a` is `x` | `b` is never read; `c` is read |

### 5.2 Why a skipped bad cell is not an error

The alternative is "every typed cell the expression names must be of its type, whether or not it is reached". It is simpler to state and it is wrong for this tool, for three reasons. (1) It breaks the guard that `docs/filter.md:90-95` teaches and that the hints of the typed refusals repeat (the hints of the typed refusals say to keep such rows out with `--where` first, `finish.cho:735`, `770`, `778`, `819`): the only way to filter a column that has empty cells and compare it as a number. (2) It would make `and` and `or` different, or change `and`: the first is a surprise, the second a change of valid calls. (3) It would not be cheaper for the engine: it must still read cells it does not need. **What keeps it honest** is that the rule is a function of the row and the expression and nothing else: the same row is refused or kept in the same way at every `--threads`, in every order of the file, and in a range that is read again by the parent (`par.cho:168`). *Nothing is skipped silently in the sense that matters for the rule "the same bytes at any thread count"*: a cell that is not read cannot be refused by any run.

What can still surprise is `c:int > 5 or c = ''` (the last row of the table). The refusal's hint says what to write (section 7), `--explain` makes the order visible (section 6.3), and the guard idiom is in the help text.

### 5.3 Typed columns, `in`, empty cells

* **A typed condition is a leaf like any other.** `holds` is unchanged (`expr.cho:523-594`): the same `:int`, `:dec(S)` and `:float` reads and the same eleven verdicts. `column.type-conflict` is the plan's (`query.cho:296-317`): one numeric type for a column in the whole expression, an untyped mention is text and mixes (`a:int > 5 or a = 'x'` is one `:int` and one text mention, and is valid).
* **`in` stays one leaf.** `a:int in (1, 2)` reads the cell once. `a:int = 1 or a:int = 2` reads it up to twice, and parses it twice. They answer the same rows with the same refusals when both conditions are reached: a bad cell is refused at the first leaf either way. The advice is therefore a *note*, not a rule: `--explain` reports "the conditions at 0 and 1 compare `a` with `=` and could be `a in (1, 2)`" (section 6.3). The **repair** of the same shape (section 7, A0) exists for the stage in which `or` is not yet built.
* **Empty cells.** In a text comparison an empty cell is the empty string, in a typed one it is a refusal when reached (`docs/filter.md:88-95`). Both are unchanged; the table of 5.1 is the whole of what `or` adds.
* **`not` and a bad cell.** `not (c:int > 5)` and `c:int <= 5` refuse the same cells: the refusal is made by the read, before the comparison.

### 5.4 The first refusal of a row

If a row reaches two leaves with bad cells, the first in the order of evaluation is the one named. With the program that is the first in the path taken. The oracle (`tests/conformance/refimpl.py:78-` walks `for cond in where` and returns on the first non-true verdict) is extended with the same walk over a tree, and the harness compares the *row, line and column* named (as `test_plan.py` does).

## 6. (A) The other parts of the tool

### 6.1 The parallel scan and the speculative ranges

Nothing in the parallel read looks at a condition. A worker evaluates the same `screen` on the records of its range (the three call sites of section 1); a range in which the plan aborted (`t[engine.k_abort()] != 0`) is not accepted and the parent reads it again from where the sequential read would be (`par.cho:168`, `par.cho:1305`). A range that began on a wrong guess about quotes is read again for the same reason (`docs/parallel.md` section 2). So `or` neither adds a state a thread can leave nor a way for a speculative range to hand back a refusal that the sequential read would not give. **The first refusal is the first in file order, evaluated by the sequential rules, as it is today.** The gate is the existing one (`test_parallel.py`, N in {2, 3, 4, 8, 16, 64} by chunks {1, 7, 64, 1000}, `docs/numbers.md` G4) with generated `or` plans added (section 9, G-PAR).

### 6.2 `--group`, `--agg`, `--order-by`, `--top`, `--report types`

The filter runs before the row is counted, grouped, sorted or classified (`screen` is the first thing the three call sites do, `engine.cho:448`, `490`, `513`). A grouping counts the rows the expression keeps; `--order-by` and `--top` sort and cut the kept rows; `--report types --where EXPR` counts the cells of the kept rows (`docs/numbers.md` 5.2). None reads the structure of the expression. What changes is a hint and a doc line: `value.not-integer` and its three siblings say "a condition that is false stops the ones after it" (`finish.cho:770`, `819`); the hint becomes "a condition that decides the row stops the ones after it; for `and` put a test that is false for such a cell before it (`c != '' and c:int > 5`), for `or` one that is true (`c = '' or c:int > 5`)".

### 6.3 `--explain`

For a plan with no `flow` the document is **byte-identical to today's** (gate G-EXPLAIN-SAME). With a `flow`, each object of `"where"` gains two members in the order they are tried:

```json
"where": [
  {"column": ..., "type": ..., "op": "=",  "values": ["x"], "if_true": 2, "if_false": 1},
  {"column": ..., "type": "int", "op": ">", "values": ["5"], "if_true": 2, "if_false": "reject"},
  {"column": ..., "type": ..., "op": "=",  "values": ["3"], "if_true": "accept", "if_false": "reject"}
]
```

A target is the index (0-based, into this array) or `"accept"` or `"reject"`. The schema (`scripts/schemas.py:130-134`) gains two optional members. Notes (advice, not refusals; `explain.cho:795-808` is the model):

* a chain of `=` on one column with the same type that could be `in` (the advice of 5.3);
* a typed condition that is tried before any condition that could guard it, only when it is the first condition on that column and it is a `:int`, `:dec` or `:float` (so that "refused at the first empty cell" is a sentence the document can say). *Marked as optional: the exact wording is for the build; the rule is that a note is never a refusal.*

The text form of `--explain` is not built (`docs/query.md` section 15); nothing here needs it.

### 6.4 JSON lines in and out

`--input-format jsonl` filters through the same `screen` (`jscan.cho:95`, `jsonl.cho:65` only decide whether a filter exists). A cell is the exact text of the number or the string, as ever, and the typed rules apply to it. `--format jsonl` writes the kept rows; nothing about the output depends on the expression. The rows of a refusal are numbered as the records are.

### 6.5 MCP, `introspect`, the skill

`where` stays a `text` property of the tool; its description (`spec.cho:10`) is the flag's help, so it changes with the help, and `scripts/site.py --check`, `scripts/schemas.py --check`, `scripts/mcp.py --check` regenerate. The explain document's `outputSchema` gains the two optional members above. The skill's "Query" section and `docs/refusals.md` are generated from the flag table and the rule table (`docs/query.md` section 10); the examples in the help are run by the tests. The help must stay one line without `;` or `|` (the contract's limits, `docs/backlog.md` friction list): the grammar is written in words, as `--query`'s is.

## 7. (A) Rules, repairs and bounds

| rule | exit | repair | when |
|---|---|---|---|
| `where.mixed-and-or` (new) | 2 | `choose`: the invocation with the expression parenthesised by precedence, and left to right (section 3.3) | `a or b and c` |
| `query.mixed-and-or` (new) | 2 | `choose`: the same, as the flags the corrected query stands for (`docs/query.md` 14.3) | the same in a query |
| `where.syntax` (existing) | 2 | none, except `not COND`: the rewritten expression (3.1) | a missing `)`, an unclosed group, `or` with nothing after it, `not` not followed by `(`, `()`, more than 8 levels, more than 64 conditions |
| `query.syntax` (existing) | 2 | as today (a typo, `==`, a double-quoted value) | the same, at the offset in the query |
| `query.unsupported` (existing) | 2 | the features `or`, `parentheses` and `not` disappear from it; `like`, `between`, `is-null`, `column-comparison` stay | |
| `value.not-integer`, `value.not-decimal`, `value.decimal-scale`, `value.decimal-too-wide`, `value.not-float`, ... (existing) | 8 | as today; **the hint changes** (6.2). The `:dec` scale repair rewrites the condition at its offset (`finish.cho:833-835`) and still does | |

**No other tag.** The question of a rule for "too many alternatives" (`where.or-too-many`) has the answer no: the number of alternatives is bounded by the number of conditions, which already has its words (expectation 11, `expr.cho:135`), and a second tag for the same ceiling would let a caller tell apart two refusals that it repairs in the same way (by writing fewer conditions). The depth ceiling is one more `expected` string, not a tag, for the same reason.

**The repair that rewrites `or` into `in`** is a stage of its own (A0, section 11) and is shaped for the refusal that exists *before* `or` is built:

* offered when the expression is, at its top, `COND or COND { or COND }` (and no other connective), every `COND` is `COLUMN = VALUE` or `COLUMN in (...)`, **the same column** (the same name or position, written the same) **with the same type suffix**;
* the rewritten expression is `COLUMN[:type] in (V1, V2, ...)` with the values in order, duplicates kept. It is the same rows and the same refusals as the source would have: the cell is read once by `in` and, in the source, at the first condition (a bad cell is refused either way, the row and column named are the same; the refusal's *repair text* differs because it shows the expression, which is expected);
* **not offered** when the operators differ (`!=` makes `a != 1 or a != 2` a tautology; a repair that wrote an `in` for it would be wrong), when the types differ, when the columns differ, when a leaf is `contains`, when `or` is nested, and when the result would pass 1,024 values;
* after `or` is built (A1) the same shape is accepted and gets the note of 5.3, and the repair is kept for any other client that builds an expression (a `plan.flags`).

**Bounds and flags with ceilings.** None of the numbers is a new flag. Of the numbers: 64 conditions and 1,024 values per `in` (existing constants, `expr.cho:381`, `423`, listed by `introspect` in the help); a query of 4,096 bytes (`docs/query.md` 14.1); depth 8 (new constant). A flag (`--max-conditions`) was considered and rejected: the ceilings are not resource limits (a plan is a few KiB whatever it says), they are limits of what a person can read, and a flag would be one more thing to document, schema and test for no use. (Open question 5.)

## 8. (B) Renamed and computed output columns

### 8.1 What is asked, and the honest question

`select a as b`, and maybe a value that is not a column. The README lists it as one line with `or` (`README.md:354`). They are different in kind, so this section separates them and answers the question the task asked: **does anything beyond renaming belong in `table`?** The answer proposed is no, and the argument is in 8.3.

### 8.2 B1: renaming (proposed, build it)

**What it is for.** (1) The output of a call must use the names the next step expects (a column called `Total (USD)` that the caller wants as `total`) without a second tool in the middle; an agent with only MCP has none. (2) `--format jsonl` refuses two equal labels, `--select a,a` being the example, and the rule that refuses it has a repair of `none` for lack of anything to say (`finish.cho:915-935`). With a rename the hint can point somewhere. (3) Aggregates have labels `sum:bytes`, `mean:price` that are not names a caller wants in a JSON key (`frame.cho:161-190`).

**What it is not.** Not a column of the input (it changes nothing the engine reads: `where`, `group by`, `order by`, `agg` use header names). Not a way to give a name to a condition. Not a rename of the input header for later clauses (that is what SQL's alias is *not* either, except in `order by`).

**The flag.** `--rename LIST`, items `TARGET=NEW` separated by commas:

* `TARGET` is an **output label** exactly as the answer would print it (`customer`, `count`, `sum:bytes`), or `#N`, the Nth output column (1-based). `#N` is the way to address a repeated label, and is what the query translator writes, so the translator needs no escaping of a target.
* `NEW` is the new label: **at least one byte**, any bytes the CSV writer can quote (`put_label`, `frame.cho:77`); for `--format jsonl` the existing checks apply (`output.not-representable`, `output.duplicate-column`).
* Escapes as `--select`'s (`docs/select.md`): a backslash before a comma, a backslash or a leading `#`, and **before an `=`** (an `=` in a target is `\=`; the first unescaped `=` splits an item).
* Applied in **one place, `frame.prepare`** (`frame.cho:150-190`), where the labels are written, so the CSV header, the JSON `columns` and the JSON-lines keys all come from it. The rows, the engine and the parallel path do not see it. `--explain`'s `output.columns` is that list, so its gate (E5, `docs/query.md` section 11) keeps meaning "the columns of the real answer".
* **`--sort` names the label after the rename** (`finish.cho:872-880` resolves against the labels): `--rename sum:bytes=total --sort -total`. `sort.unknown-key` lists the renamed labels in `detail.available`. A stale name is `sort.unknown-key`, as any unknown one.
* **`--order-by` and `--where` and `--group` and `--agg` name header columns**, always. Nothing in the flag form changes what they read.

**Refusals.**

| rule | exit | when | repair |
|---|---|---|---|
| `rename.syntax` | 2 | an item with no `=`, an empty target or an empty `NEW`, a backslash at the end; `detail.offset`, `detail.expected` as `where.syntax` | none |
| `rename.unknown-target` | 3 | a label or position that is not an output column; `detail.available` (the labels, as `sort.unknown-key` and `select.unknown-column` give them) | `choose` for a case variant, as `select.unknown-column` does |
| `rename.ambiguous-target` | 8 | a label that two output columns carry; use `#N` (as `select.ambiguous-column`) | none |
| `rename.repeated-target` | 2 | one output column renamed twice | none |
| `args.conflict` (existing) | 2 | `--rename` with `--report`, or with the shape (no `--select`, `--where` or `--group`: there are no columns) | none |
| `output.duplicate-column` (existing) | 2 | the hint adds "or rename one with `--rename`" | none (which of the two was meant is not known; no name is invented) |

No rename ever produces a refusal because a new label equals another: in CSV two equal labels are allowed today (measured, section 1) and a rename does not change that; JSON lines refuses them already.

**The query.** `item = ( column | aggregate ) [ "as" name ]`, `name = ident | quoted` (an ident is not a reserved word, `docs/query.md:62-72`), `as` required (the SQL that omits it is `query.syntax`: `select a b` stays an error). `select * as x` is `query.syntax`. The translator writes `--rename '#1=b,#3=n'`: positions in the select list, which are the output positions because a grouping's select list is already its group columns then its aggregates (`docs/query.md:90-94`).

* `order by` in a query: **an alias takes priority over a header name**, as SQL does (checked: SQLite 3.53.4 and DuckDB 1.5.6 both order `select a as b, b as a from t order by b` by the first column of the select list, the alias). For a grouping the translator writes `--sort` with the label after the rename (`order by n desc` with `count(*) as n` is `--sort -n`); for rows it writes `--order-by` with the **header column the alias stands for** (every select item is a plain column, so there is exactly one). Where `where`, `group by` and an aggregate's argument are concerned a name is a header column, as in standard SQL (an alias is not visible in `where`).
* The feature `alias` of `query.unsupported` goes (`qparse.cho:368-370`).

**Cost.** One more list parse, one more pass over at most the output columns in `frame.prepare` (before any row), a handful of rules. No per-row work: the labels are made once. Estimate: about 350 lines with tests' half again. Nothing in the read loops (`git diff --stat` shows `frame.cho`, `finish.cho`, `plan.cho` or a new module, `spec.cho`, `qparse.cho`, `qtrans.cho`): the 1.01 gate holds by construction, as it did for Q0-Q3 (`docs/query.md` section 11).

### 8.3 B2 and B3: values that are not columns (proposed: do not build)

**Literals and concatenation (`select a, 'x' as src`, `a || ' ' || b`).** Not built, and not proposed as a stage. A constant column is not asked for by any question the tool answers; a concatenation is total and exact (it is bytes), which is the argument for it, and it is the argument for `upper`, `substr` and `trim` too, one at a time. The line is drawn at **the output being bytes of the input**: today a row's output is the file's cells, unchanged (`docs/select.md`'s claim, "the selected fields are exactly Python's `csv.reader`'s", `docs/select.md:118`), and every test oracle for selections rests on it. The first computed column ends that, for all later features.

**Arithmetic on typed columns (`price * qty`, `a + b`).** The rules that would make it exact are below, so that the answer "no" is not "not thought through":

* **One binary operator per computed column**: `LEAF OP LEAF`, `OP` one of `+ - *`, a leaf a typed column or one literal. No nesting, no parentheses, no precedence, no unary minus except in a literal. (An expression language is what is being refused, and a single operator is the smallest thing that is not one.)
* **Types.** Both operands `:int`, or `:dec(S)`; an `:int` is a decimal of scale 0. **`:float` is refused** (`expr.float-arithmetic`): a double result is rounded by every operation, and the tool's float is "the exact sum, rounded once" (`docs/filter.md:124-125`), which a product of two doubles is not.
* **`+` and `-`**: the result has scale `max(S1, S2)`. The operand of the smaller scale is multiplied by a power of ten, so a value below 10^18 becomes one below 10^36: it does not fit 64 bits, and the result is computed in 128.
* **`*`**: the result has scale `S1 + S2`; above 18 it is refused (`expr.scale-too-large`; the repair is a smaller scale, which is the caller's decision). The product of two 18-digit values is below 10^36.
* **No overflow rule for `:int`** is needed or wanted: two 64-bit integers' sum and product fit in 128 bits, and a result is **printed in full** (up to 39 digits), exactly as a sum already is when it passes 64 bits (`docs/filter.md:111-114`, `dec.put_sum` at `dec.cho:100`). *Not verified:* `put_sum` is documented for a pair below 2^93 (the row ceiling), and a product reaches 2^126, so the printer has to be checked or extended; the 128-bit product itself has no routine in the repository (`dec.mul_small`, `dec.cho:126`, multiplies limbs by a small number).
* **Division is refused** (`expr.division`): a quotient has no finite decimal. The mean already has the explicit form, rounding half to even to a stated scale (`mean:COL@N`, `docs/numbers.md` 3.6); a division would need that, and a zero divisor is a row-level refusal.
* **Cells.** Each operand is read as its type; a bad cell is the refusal of that type with `context: "select"`; an empty cell refuses (no `null`, no propagation). Operands are read left to right.
* **Where it could stand.** Only in the select list of rows. Not in `where`, `group by`, `order by` or an aggregate: that is where the width of the result would have to be carried (a sum of products needs the pair accumulator to take 126-bit terms; `agg` adds 64-bit cells: `docs/filter.md:111-114`).

**Why it is not proposed.**

1. *It does not answer the question people ask.* `sum(price * qty)` and `price * qty > 100` are what a computed value is wanted for, and both are excluded above. Computing it only to show it is a spreadsheet's job; `table`'s own typed `sum:price:dec(2)` already gives the exact total of a column.
2. *It ends "the output is the input's bytes"* (8.3 above), and with it the cheap oracle of every selection test.
3. *It is a third thing in the select loop*, and the cost of that is on record: "A third case in the read's loop costs the other two 15 percent" (`docs/backlog.md:75-81`). It could be put in a callee, as the sort was, but `--select` is 46 ns a row (the cell-cost round, as `docs/backlog.md` quotes it), the fastest path the tool has.
4. *It is the first expression.* The README's second bullet ("no expressions or functions") is the tool's identity. A single operator is the smallest case; the second operator is the next request, and the third is a language. The rename has no such slope: a label cannot grow.
5. *The downstream tools do it.* `mlr`, `duckdb`, `awk` read the CSV or JSON lines `table` writes; the exactness that `table` gives (decimals not through doubles) is lost in `awk` but kept in `duckdb`'s `DECIMAL`.

**Reopen if** a recorded use needs the computed value *in the same call* and cannot be an aggregate (`sum`, `mean`), and the answer to 3 is measured (the select path with and without a callee, instruction counts, 1.01). Until then the line in the README stays: "No computed column".

## 9. Gates, written before any code

Each gate has a pass line and can fail. Every stage ends with every earlier gate green. The numbers in the right column are what must be true, not what has been measured (nothing here is built).

| gate | what is held | pass line | how it can fail (mutants) |
|---|---|---|---|
| **G-COMPAT** | every call valid today means the same | the 41 plans of `scripts/corpus.py` (`PLANS`: 18 on the benchmark file, 23 on the adversarial and numeric ones; run sequentially and with tiny ranges, 166 runs in `query_corpus.py`) give the md5s of `main`; the generated plans of `test_plan.py` unchanged; **near strings** (a column named `or`, `not`, `and`, `in`, `contains`, quoted or not, at each place of an expression, `not in` and `not contains` as columns) parse as before | a mutant that makes `or` or `not` a keyword everywhere; one that makes `(` start a group after `in` |
| **G-ORACLE-SQL** | the *truth values* of `and`, `or`, `not`, parentheses, `in`, `not in`, `contains` (as `instr`), text comparison | generated tables (the generators of `test_plan.py`, `query_gen.py`: awkward names, quoted fields, empty cells, integers at the edges of 64 bits) and generated expressions (up to 12 leaves, depth up to 4) are loaded into **SQLite** (Python's `sqlite3`, 3.53.4 here) with **TEXT columns and BINARY collation** (bytewise, a shorter value first, as `table`'s) and into **DuckDB** (1.5.6 here); typed leaves only on columns that hold valid cells, as `CAST` for SQLite (`CAST` is lenient in SQLite, so the generator keeps the cells exact) and `TRY_CAST` for DuckDB; rows kept, as ids in file order, equal `table`'s in 3,000 expressions, `--format json` and `csv`, `--threads` {1, 4}, and equal across the two engines too (a disagreement between the oracles is the alarm) | `<`, `<=` swapped; `and`/`or` swapped; `not` dropped; an `in` list's last value dropped; short-circuit wrong for a *value* |
| **G-ORACLE-REF** | the *refusals*, which no SQL engine can give | `refimpl.py` extended with a tree evaluator that walks left to right and stops at a decided group, written to be read and sharing no code with the tool: the **row, line, column and rule of the refusal** (and the kept rows when there is none) equal the tool's for 3,000 generated expressions over tables with bad cells (309 of 1,800 plans of `test_plan.py` are refusals today); the generator draws typed leaves on columns with empty and non-numeric cells and puts guards both before and after | the order of evaluation changed in the compiler (a program that tries the leaves right to left; the `or` guard tried after; a `not` that swaps the wrong exits); *a skipped bad cell refused*; *a reached bad cell skipped* |
| **G-SQL-NOT-REF** | the reason for the two gates above | recorded, not run: on a two-row table (`1`, `x`), `where cast(a as integer) = 1 and a != 'x'` **answers** `1` in DuckDB 1.5.6 (it evaluated the second condition first) and **raises** in SQLite 3.53.4 with a Python function as the cast. Neither is a wrong engine: SQL gives no order. Hence an SQL engine is the oracle for truth values and **not** for refusals | |
| **G-PROGRAM** | the program equals the tree | a Python model (appendix) already agrees on 5,030,376 cases (random trees to 8 leaves, every status of every leaf); in the tool, the **plan check**: every target is greater than its leaf or negative, every leaf is reachable, the count equals `meta[1]` (a debug/`--explain` invariant, tested through the explain document on 10,000 generated expressions) | a target backwards (a loop); a leaf unreachable; two exits swapped for `not` |
| **G-PAR** | the same bytes at any thread count, with refusals | `test_parallel.py` with the matrix of `docs/numbers.md` G4 (N in {2,3,4,8,16,64} x chunks {1,7,64,1000}) over generated `or` plans: stdout, stderr and exit equal to `--threads 1`, refusals included, and the file-order first refusal named | a range that evaluates differently from the parent |
| **G-CORPUS-EQ** | flags and query give one plan | `scripts/query_corpus.py` with a query written by hand for each new corpus plan; `tests/conformance/test_query.py` `Equivalence` with `or`, parentheses, `not` in the plan generator: identical stdout, stderr, exit for 3,000 plans x {json, csv} x `--threads` {1, 4}; the `rename` plans too (B1) | the translator parenthesising wrongly; `not a = 1` mistranslated; a rename position off by one |
| **G-OFFSETS** | every place the grammar can fail has a pinned rule and offset | the cases of `query_cases.py` for the new grammar (each `mixed-and-or` position, an unclosed group, `or` with nothing after it, `not` with nothing, 8 and 9 levels, 64 and 65 leaves, an `or`-chain of 1,024 and 1,025 values), the reference front end `query_ref.py` (Python, no shared code) agrees with the cancho parser on 100,000 generated token sequences as in Q1-ORACLE (`docs/query.md` 8) | an offset one off; the 9th level accepted |
| **G-FUZZ** | no trap | 10,000 random byte strings and mutated valid expressions, flags and query: only a rule `introspect` lists, an offset inside the text | recursion without a bound |
| **G-MUT** | the logic matters | `scripts/or_mutants.py` (new, `--check` in CI as `filter_mutants.py` has, `test_mutants_apply.py`): ported from the 37 of `filter_mutants.py` those that apply (notably *no short-circuit*), plus: an `or` that is `and`; the true and false exits swapped for a leaf; `not` ignored, doubled, or swapped in the wrong subtree; precedence changed (the mix accepted); the repair of the mix offering one reading; the depth ceiling 9 or 7; a leaf evaluated twice; `in` merged across columns (the A0 repair); the A0 repair offered for `!=`; a rename off by one; a rename applied before the sort resolves; `--sort` resolved against old labels. Every one killed; one that is equivalent is explained and removed, as the 38th of `docs/filter.md:173` was | |
| **G-EXPLAIN-SAME** | an AND-only plan explains as before | `scripts/explain_corpus.py` md5s unchanged; the explain document of an `or` plan validates against the regenerated `table.v2` (`scripts/schemas.py --check`, `test_explain.py`) | a `flow` printed for a conjunction |
| **G-ANDONLY-SPEED** | **the existing plans do not get slower** | `scripts/gate_regress.py --counter`: instructions retired, bound **1.01**, the 26 cells already in it (and the `status = 404 and bytes:int > 50000` filter in particular), three builds each side, **on the Mac** (and gram when it is back; not used here). The old side is `origin/main` at the commit the stage starts from | any cell over 1.01. See section 10 for what is likely and what the remedy is |
| **G-GEN** | the surface regenerates | `scripts/site.py --check`, `scripts/schemas.py --check`, `scripts/manifest.py --check`, `scripts/mcp.py --check`; `test_skill` runs the help's examples; `test_rules` has a fixture for every new rule | |
| **G-B1** | rename | a Python reference of the labels over generated plans (every mode, `#N` and names, awkward labels, duplicates), CSV and JSON and JSON lines; `sort.unknown-key`'s `available` lists the renamed labels; `--explain`'s `output.columns` equals the real answer's (E5 of `docs/query.md`); and the mutants listed above | |

## 10. The performance budget of the AND-only path

**Baseline, measured.** Instructions retired on the Mac (`/usr/bin/time -l`, the counter `gate_regress.py --counter` uses; build of `fb4098d`, `build/table`, version 0.3.0, which differs from `origin/main` in tests, `jout.cho` and `spec.cho` only and not in a condition path; the file is the 1,000,000-row, 31.7 MB file of `scripts/bench.py`, regenerated with its generator and seed; one core; load average about 10, which the count does not depend on; the least of three runs):

| plan | instructions (M) | per row |
|---|---:|---:|
| the shape (no flag) | 591.3 | 591 |
| `--where "bytes:int < 0"`, CSV (one typed condition, every row tried, none kept) | 1,079.8 | 1,080 |
| `--where "status = 999 and bytes:int > 1 and bytes:int > 2 and bytes:int > 3"` (the first, a text one, is false: the rest never tried) | 986.5 | 987 |
| `--where` of four `:int` conditions all tried (three true, the last false), CSV | 1,794.5 | 1,794 |

So one more `:int` condition that is tried costs **about 238 instructions a row** ((1,794.5 − 1,079.8) / 3), measured on this file; a row costs 591 to read and split and about 1,080 with one typed condition. These are one machine, one file, one build; they are the scale of the budget and not a claim about any other plan.

**Where the program can cost, and the budget.**

* The new work on the AND-only path is **one test per row**: "is `flow` empty" in `screen`, placed with the test that is already there (`engine.cho:409`, `count_of(tree, 1) == 0`). *Estimate:* 3 to 6 instructions a row, 0.3 to 0.6 percent of 1,080. *Remedy if it shows:* fold the two tests into one load, a "shape" integer kept with the plan (0 no condition, 1 a conjunction, 2 a program), so that the common path executes the same number of loads and compares as today. *Not measured.*
* The program evaluator is a **separate function**, reached only from the second case of `eval`, never from the loop that exists. The reason is on record twice: a branch added in the read loop costs the others 15 percent (`docs/backlog.md:75-81`), and "the one-thread read loops' instruction count is a function of that whole function's text" (`docs/numbers.md:895`), so a change in the text of `eval` or `screen` can move the AND-only count without adding an instruction to it. This is **the main risk of the gate**, and the only defence is to measure (three builds each side, `gate_regress.py` already averages layout out: its control showed 0 to 6 of 14 cells over for *identical* sources on the time form, which is why the counter form exists, `docs/numbers.md` G6).
* *Estimate for the program itself,* for plans that use it: per tried leaf the same `holds`, plus two loads and a select for the target; about 5 to 8 instructions on 238 for an `:int` leaf (2 to 3 percent of the leaf; for a text leaf the percentage is higher, and it has no bound, because it is a new path with no old one to compare). A `not in (…)` that forces a conjunction onto the program path pays this too, and that is deliberate: the program path is the price of the feature.
* If the 1.01 bound does not hold after the fold: **do not relax the bound.** Options in order: (1) move the whole conjunction case into its own function so `screen` and `eval` have the text they have today and the new case a function of its own; (2) a second `screen` selected once per plan by the caller (the call sites are three) at the cost of a copy of the three loops' call; (3) declare the cost and ask (open question 7).

## 11. Staging

Sizes are **estimates**, by analogy to what exists: Q1's front end was 2,830 lines in eight modules (`docs/query.md` 14), the parser of `--where` is 627 lines in `expr.cho`. Each slice is one pull request that ends with every earlier gate green.

| slice | what | size (estimate) | gates | independent of |
|---|---|---|---|---|
| **A0** | the **repair**: `a = 1 or a = 2` (the shape of section 7) is refused as today, with a `choose` repair to `a in (1, 2)`, in `--where` (`where.syntax`) and in a query (`query.unsupported: or`). No engine change | 150 to 250 lines, tests 100 | G-COMPAT, G-OFFSETS (the new repair), G-MUT (the repair's mutants), G-GEN | everything |
| **A1** | `or` and parentheses in `--where` and `--query`: `ALL`/`ANY`/`UNIT`, `where.mixed-and-or` and `query.mixed-and-or`, the `flow` field, the program evaluator, `--explain`'s members | 600 lines of tool (`expr.cho` parse 120 to 300, `eval` +50, `query.cho` +50 and the 39 sites, `qparse`/`qtrans` +250, `explain` +50, `finish` hints), 1,500 of tests | all gates of section 9 except G-B1 | B |
| **A2** | `not (...)`, `not in`, `not contains`, and the query's `not COND` | 150 lines | G-ORACLE-SQL/REF, G-CORPUS-EQ, G-MUT with the `not` mutants | B |
| **B1** | `--rename` and `select a as b` | 350 lines, tests 500 | G-B1, G-CORPUS-EQ, G-GEN | A |
| (B2, B3) | not proposed | | | |

**Order:** A0, then A1 and A2, B1 in any place. A0 is worth shipping alone: the commonest `or` has a one-step answer for a client that retries, and the backlog already asks for it (`docs/backlog.md` item 3). A1 is where the risk is; it should start from the model of the appendix turned into the reference of G-PROGRAM before a line of the tool is written.

**Docs of the build, not of this PR:** `docs/filter.md` (grammar, the evaluation rule with the table of 5.1), `docs/query.md` (the grammar, the reserved words, `alias` and `or` out of the unsupported table, `query.mixed-and-or`), `docs/refusals.md` and the flag help by the generators, the README's "What it cannot do yet" (lines 353-354) and its sentence about the backlog (line 356).

## 12. Risks

1. **The 1.01 bound on the AND-only path** (section 10). Code layout moves the count. Mitigation: a separate function for the program, three builds each side, the fold of the two tests; the bound is not relaxed.
2. **`and`/`or` mixed is refused**, and agents expect SQL precedence. It costs a retry with a runnable repair; if it costs more in practice, the refusal can be dropped for precedence (the answer becomes the refusal's first reading) and nothing breaks.
3. **The guard of an `or` is the positive test**, which is the opposite of what a person writes for `and`. `c:int > 5 or c = ''` refuses at the first empty cell. The hint, the explain note and the doc table carry it; the risk is that it is still a surprise.
4. **A parser that recurses.** Depth 8 is a choice; a parser that is a loop today becomes recursive; G-FUZZ and the mutant "ceiling 9" cover it. If the language's stack bound is lower than assumed (it is not stated in the repository's docs), depth would be lowered, not the structure changed.
5. **The refusal oracle must be written by hand.** No SQL engine gives the order of evaluation (G-SQL-NOT-REF, measured on one table). The reference is Python code that states the rule, so a mistake in the *rule* is the one thing the gates cannot find; the review of the table in 5.1 is the defence.
6. **The 39-site ripple of the new `Query` field** is mechanical and a place for a slip (a field not copied by `duplicate`, so a thread evaluates a conjunction). G-PAR with an `or` plan catches it; G-MUT has the mutant.
7. **(B1) `order by` alias priority differs from the flag form** (`--order-by` names a header column). The query translator resolves it, so the two forms give the same plan for the same query; but a person who moves from the query to the flags can be surprised, and the doc says it once. The gate is G-CORPUS-EQ with aliases.
8. **Scope drift.** `not` and `or` invite `between`, `like` and null. They are refused, each by name, and the reasons are in `docs/query.md:75-87`.

## 13. Open questions for the maintainer

Each has a recommendation; none is decided.

1. **Refuse `a or b and c`, or take SQL's precedence?** *Recommend: refuse* (`where.mixed-and-or`, section 3.3), because a refusal can be relaxed and an answer cannot be taken back, and it is the tool's rule. Cost: one retry. If the answer is precedence, G-ORACLE-SQL is stronger (SQLite and DuckDB agree on precedence) and the rest of the design is unchanged.
2. **Parentheses in the first `or` slice, or `or` without them?** *Recommend: together* (A1). Without parentheses the grammar is DNF by construction and the program is unnecessary, but the feature is not usable and the second slice would redo the representation. The cost of parentheses on top of `or` is the depth ceiling and a recursive parse.
3. **Which `not`?** *Recommend: `not (...)`, `x not in`, `x not contains` in the flags; `not COND` as well in the query* (A2). The flag form cannot take `not a = 1` without a lookahead that makes `not` a different token in `not in (1)` (the column called `not`), and the repair covers it.
4. **A bad cell that is not reached: leave it unread** (section 5.2) *Recommend: yes*, as `and` does today. The alternative (every typed cell the expression names must be valid) would change valid calls.
5. **Are the ceilings flags?** *Recommend: no new flag.* 64 conditions and 1,024 values stay constants, depth 8 is a constant (a choice; the number is not measured). If the maintainer wants `--max-conditions`, it belongs to the same stage as its documentation and would need a ceiling of its own (the plan is a few KiB at any value).
6. **Ship A0 first?** *Recommend: yes.* It is front end only, it answers the commonest case and it keeps its place afterward as the explain note.
7. **If the AND-only count is over 1.01** after the remedies: *recommend: do not ship A1 until it is under;* the bound exists because the first N4 build regressed 27 instructions a row of every one-thread read and the clock called it noise (`gate_regress.py` docstring).
8. **Rename: `--rename TARGET=NEW` as a separate flag, or `--select 'a as b'`?** *Recommend: the separate flag.* `--select` names are exact bytes with spaces and operators in them, and ` as ` is a plausible header; and only a flag can address `sum:bytes`. The translator writes positions, so it never escapes a target.
9. **Rename in the query: `as` required, an alias before `order by` wins over a header, an alias is not visible in `where`/`group by`.** *Recommend: yes,* SQL's own rules, checked against SQLite and DuckDB for `order by`.
10. **Anything beyond renaming?** *Recommend: no* (section 8.3), with the reopening condition written there. If the maintainer wants arithmetic anyway, the first piece to build is the 128-bit product and its printer, with its own gate, before any grammar.
11. **Does the README line "no computed or renamed column, no `or` in a filter" change now?** *Recommend: not in this PR* (generated blocks, `scripts/site.py`); in the pull requests of the slices.

## 14. What this document did not measure

* **The speed of anything proposed.** The only performance figures are the baseline of section 10 (the existing code). Every figure for the program, the fold and the rename is an estimate or a choice and says so.
* **The depth ceiling of the recursive parse** against the language's stack, and the size of `Query` after the new field.
* **Whether `dec.put_sum` prints a 126-bit value** (8.3), and a 128-bit product has no code.
* **Linux.** The baseline is the Mac's; gram is down and was not used. The Linux counter (`perf stat -e instructions:u`) is the second half of the gate when the machine is back.
* **DuckDB's and SQLite's behaviour** is two small tables, recorded so that the point (no order of evaluation) is not an assumption; it is not a survey of either engine.
* **Agent behaviour.** That agents write SQL precedence, `not a = 1` and `(a or b) and c` is an assumption from how they write SQL elsewhere in this tool's history (`docs/query.md` 12), not a measurement.

## 15. Slice A0 as built: numbered notes

The maintainer accepted the recommendations of section 13 as written, and slice A0 (section 11) is built: `a = 1 or a = 2` and its kind are refused as before, and the refusal offers `a in (1, 2)` where that is the same question, in `--where` and in `--query`. Nothing of A1, A2 or B1 is built, and the engine (`expr.eval`, `screen`, `Query`) is untouched. What the build found where this document and the code on `main` (`9b82e53`) did not say the same thing, or said less, and the smallest reading taken:

1. **`--explain` in A0.** Sections 5.3 and 6.3 put the note "the conditions at 0 and 1 compare `a` with `=` and could be `a in (1, 2)`" in the `--explain` of a plan that has `or`; A0 has no such plan. So A0 adds nothing to the explain document (G-EXPLAIN-SAME: the 166 runs of `scripts/corpus.py` explained, `main`'s bytes and this branch's are identical). `--explain` of an `or` is the refusal it always was, and since it is one refusal it carries the same repair, with `--explain` still in the command it offers. The note is A1's.
2. **`where.syntax` is no longer "never repairable".** Section 7's table gives its repair as "none, except `not COND`" (A2). The repair of A0 makes the rule `sometimes` (`introspect`, `docs/refusals.md`, `test_rules.py`, whose fixture for the rule is now the `or`); `query.unsupported` was `sometimes` already. The hint of both refusals is unchanged, byte for byte, so that the golden of every refusal can say "the same but for the repair".
3. **"The same column (the same name or position, written the same)"** is built as the same bytes of the column as written, type suffix included: `a` and `a`, `a:int` and `a:int`. `a` and `'a'`, `a` and `#1` (even when column 1 is `a`), `a` and `A`, and in a query `i::int` and `cast(i as int)` are written differently, so they are not merged. This is the smaller reading, and an offer that is withheld costs one retry; an offer that merged two spellings would have to prove they name one column.
4. **The ceiling of 1,024 values** is `expr.parse`'s, and is applied by parsing the corrected expression before it is offered (one condition, no error). In a query it is applied to what the translation writes, and a query is at most 4,096 bytes, so it is reachable only through lists of short values (`a in (1,1,...) or a in (...)`): a chain of 1,000 `a = N` is `query.too-long` first.
5. **The literals of a typed condition in a query.** The translation of a query does not read the literals of a typed condition (the engine does, when it runs, and maps its refusal back to the query), so a corrected query always translates. For the repair of an `or` only, the front end parses the `--where` text it would offer, and offers nothing if it is refused: `i::int = 1 or i::int = 'x'` has no repair. The older repairs of `query.unsupported` and `query.syntax` do the same thing as before: the double-quoted `d::dec(2) = "y"` is repaired as `'y'`, which `--where` then refuses. That is a flaw of `main` that A0 neither causes nor changes (it is in the golden as it is); it is reported, not repaired here.
6. **`--where=EXPR` and `--query=TEXT`.** The repair is the whole invocation, so for the `=` forms it writes `--where=EXPR` and the query's flags, not a bare value. (The older repair of `value.decimal-scale` for a `--where=` call writes the value without the flag, as `main` has it; not changed here.) The argument index of `--where` comes through `Inputs` (`where_at`, `where_whole`), because `test_inputs.py` holds that `plan_body` looks up no plan flag by name.
7. **G-OFFSETS.** A0 adds no place where the grammar fails and moves no offset: the refusal is made where it was, at the `or`, and only its `repair` is new. The gate is a golden of 2,798 refusals of the rules `where.*` and `query.*` (`tests/conformance/or_a0_golden.json`, made with `main`'s binary before the repair was written): every answer is the same bytes after the change but for the repair of the 230 cases the document repairs (the other 2,568 are byte-identical whole), and for those the answer differs in nothing else.
8. **G-ORACLE-SQL, for the repair.** SQLite 3.53.4 judges the truth values (TEXT with the default BINARY collation; a typed column as the integer, the integer scaled by 100, or the double the cell is). DuckDB was not installed on the machine that built A0, so the second oracle of section 9 is not used here, and the "disagreement between the oracles is the alarm" check is not made. Typed columns hold valid cells only, as the document says, and `-0.0` is not drawn for `:float`.
9. **G-MUT.** `scripts/or_mutants.py` is created with the mutants of A0 (the document's "the A0 repair offered for `!=`" and "`in` merged across columns" among them); `--check` is in CI and in `test_mutants_apply.py`. A1 adds the rest of the list of section 9.
10. **Sizes.** Section 11 estimated 150 to 250 lines of tool and 100 of tests. Built: the tool diff is +293 and -6 lines (`expr.cho` 124, `qparse.cho` 85, `qrepair.cho` 45, `table.cho` 30, `inputs.cho` 13), more than the estimate because the repair is written twice, once for the text of `--where` and once for the tokens of a query, which share no parser; the tests are 1,198 lines (`or_a0.py` 652, `test_or_a0.py` 546) and a golden of 265 KB, far more than the estimate, because the gates (the golden, the reference, the oracle, the fuzz) are most of the work.

## 16. Slice A1 as built: numbered notes

Slice A1 (section 11) is built: `or` and parentheses in `--where` and in `--query`, the branch program (`Query.flow`), its evaluation, `where.mixed-and-or` and `query.mixed-and-or` with the two readings as the repair, the ceiling of 8 levels of parentheses, `--explain` with the program and the note of 5.3. `not` (A2) and the rename (B1) are not built. What the build found where this document and the code on `main` (`62d60ad`, A0 merged; the gates were re-run on `0105a87`, J1 merged) did not say the same thing, or said less, and the smallest reading taken:

1. **The program is evaluated from `expr.eval`, not from `engine.screen`.** Section 10 put the one new test in `screen` and estimated 3 to 6 instructions a row. Built there (`screen` testing `query.flowed` and calling a `screen_program`) it cost every filtered row **15 instructions** (Mac, `gate_regress.py --counter`, 3 builds a side: ratios 1.009 to 1.011 on the six cells that filter, `<-- over 1.01` on five of the 42), with the other cells at 1.001; the same source with the test taken out cost 1.1 to 2.0. The cause was not looked for further (a function the compiler no longer inlines, or the registers of a call with nine arguments); the remedy of section 10 ("move the whole case into its own function, so that `screen` has the text it has today") was taken: `screen` is `main`'s text, and the test is the first statement of `expr.eval`, which calls `eval_program` (a function of its own, in `expr.cho` because `holds` is private to it). Measured, Mac (arm64, macOS 25.2, compiler e47d647), `gate_regress.py --counter`, 42 cells, 3 builds a side (the compiler is deterministic: the three builds of a side are the same bytes), against `main` `0105a87`: **1.000 to 1.004**, the six filter cells 1.003 to 1.004 (+5.3 M instructions of 1,503 M), the others 1.000 to 1.002; PASS. The count of `lexs_read_file` is 18,064 bytes and of `lexs_read_stream` 17,072, main's. gram (x86-64, callgrind, 200,000 rows, 6 cores, the counter being unavailable there): the cut with no condition +13.0 instructions a row, which is a fixed cost of 2.6 M (the Mac: +2.0 a row of 1 M rows), a filter +20.2, so +7 a row for the condition.
2. **`where.syntax` keeps its words and its hint.** The expectation "and, or the end of the expression" (word 4) is where it was; `or` is now a connective, so after a condition the sentence reads as "and, or, or the end", which is the sentence it always was. The hint ("COLUMN OP VALUE joined by and") is unchanged too, so that the golden of A0 can say "the same bytes". Two words are new: 21 "fewer nested parentheses: the ceiling is 8" and 22 "and, or, or the ) that closes the group" (a group not closed, or something that is not a connective inside one). `()`, an `or` or `and` with nothing after it are word 1 ("a column"), at the byte where the column should begin, as `a = 1 and` always was; a `)` with no group to close is word 4 at the `)`. The words of `where.syntax` are not "sometimes repaired" any more: the rule is `never` in the rule table (the repair of A0 cannot be reached, note 8).
3. **A level is a level, a group is a unit.** `a and (b and c)` is valid and is a conjunction (no program, the code that exists); `a or (b or c)` is a program with the same exits as `a or b or c`. A level with one unit (`(a = 1)`, `((a = 1))`) is that unit. The depth that is counted is the nesting of the parentheses in the text, in both forms: the 9th `(` is the refusal, at its offset.
4. **The 64 conditions are counted at the start of a unit**, so the 65th unit is refused where it begins, whether it is a condition or a group (`(` of a group of one more condition): offset of the unit, words of expectation 11. The ceiling of 1,024 values is the parser's of `in`, unchanged (the offset is the byte after the `,` or `)` that follows the 1,025th value).
5. **The mix is refused at the connective that changes the level, before anything after it.** `a = 1 or b = 2 and <garbage>` is `where.mixed-and-or`, not `where.syntax`: the mix is the first error in the text, and the tool stops at the first. In a query the same text order holds, and the mix is also named before what the rest of the statement says: the statement is read to its end, and a `query.group-mismatch` or a syntax error found after the connective is not reported in its place (the repair is then `none`: the rest does not translate). A mix inside a group is the first mix in the text, wherever it is.
6. **The repair of a mix writes every level that mixes**, not only the first, so that the reading offered is whole (a second mix later in the text would be refused again): by precedence, `and` before `or`, each run of `and` in parentheses when it has two units or more (`a or (b and c) or d`); left to right, the expression so far in parentheses each time the connective changes (`((a or b) and c) or d`). The two readings are written from the leaves and connectives as the text has them, in `" and "` and `" or "`, with the parentheses the text had; a reading is offered only if `--where` takes it whole (it can pass the 8 levels), and **when the two are the same text (`a and b or c`) one option is offered**, not two. The query form offers them as the flags the query stands for (`--where` replaced; the rest of the invocation as `put_option` writes it), and nothing when the rest of the query does not translate, or when a `FILE` operand and a `from` would make two.
7. **The query keeps the parentheses it was given.** Section 3.4 says the translation renders the tree with the fewest parentheses; the smaller reading is to write what was said (`((a))` is `((a))`), so that the 8 levels are counted the same in the flag form and the query form and a refusal at the 9th points at the same `(`. The plan is the same either way (a level with one unit is that unit). The words `or` and `not` are not quoted in a `--where` the translation writes: at the start of a unit they are columns, as in the flag form.
8. **The repair of A0 is kept and cannot be reached.** Section 7 keeps it "for any other client that builds an expression"; there is none in this repository, and since `or` is accepted no refusal of the binary reaches `expr.or_in`, `qparse.or_chain` or `qrepair.or_to_in` (nor can an `a = 1 or a = 2` be refused for another reason: it is accepted). The code stays as the document asks, its mutants are listed as equivalent for that reason (`scripts/or_mutants.py`), and its tests are gone with `test_or_a0.py`; the pull request says so, and deleting it is one commit if the maintainer prefers. The feature `or` and the feature `parentheses` are gone from `query.unsupported`; an `or` where no condition can be (`order by a or b`, after the from) is `query.syntax` at the `or` ("the end of the query"), as section 3.4 says; `not` is still `query.unsupported: not`.
9. **`--explain`.** A plan with a program gives each object of `where` the members `if_true` and `if_false` after `values` (a condition's index in the array, `"accept"` or `"reject"`; the schema has them as optional members); a conjunction gives the document it always gave (G-EXPLAIN-SAME). The note of 5.3 is given for a run of conditions that are single-valued (`=` with one value, or `in`), on the same column written the same bytes, of the same type, which are alternatives of one `or` (each runs the next when false and all leave the same way when true): "the conditions at 0 and 1 test a against single values and could be one in: a in (1, 2), which reads the cell once". The second note of 6.3 (a typed condition tried before anything that could guard it) is not built: the section marked it optional, and the wording of a guard is not a rule.
10. **`value.not-float` and `value.not-decimal` say the guard of an `or`.** The hint of section 6.2 is in the two refusals that said "a condition that is false stops the ones after it" and in their rows of the rule table: "a condition that decides the row stops the ones after it: before an and put a test that is false for such a cell, before an or one that is true". The hint of `value.not-integer` never said it (it says "keep out the rows with such a cell with --where") and is unchanged.
11. **Sizes.** Section 11 estimated 600 lines of tool and 1,500 of tests. Built: the tool diff is +1,100 and -262 lines in 16 files (new modules `orparse.cho` 404, `explainor.cho` 146, `wrepair.cho` 59; `expr.cho`, `qparse.cho`, `qtrans.cho`, `query.cho`, `qrepair.cho` the rest), the tests about 1,900 (`test_or_a1.py` 800, `or_a1.py` 660, `test_or_a1_query.py` 265, `test_or_a1_compat.py` 190) beside 650 of the frozen reference of A0 (`query_ref_a0.py`) lines and two goldens of 0.3 MB and 0.35 MB: the gates are most of the work again.

### Gates, as run

All on the Mac unless said. **Written after the code, not before it** (the work began with the program and the gates were written beside it; each was then shown able to fail by the mutants below, and the new tests fail on `main`'s binary: they ask for `or`).

* **G-COMPAT.** The corpus of 166 plans (`scripts/corpus.py`, 41 plans): md5 of the md5s `fccb01b66dd7518d74611219a6c5aa29` on `main` `0105a87` and on the branch. `--explain` of the same 166 runs: byte-identical, md5 of the list `0c1ac7d875b01542d20b7d3fe9af2b28`. `scripts/query_corpus.py` and `scripts/explain_corpus.py`: 166 runs, 0 different. The goldens (`or_a1_main_golden.json`: 2,892 distinct cases, 2,935 runs; `or_a0_golden.json`, the 2,798 refusals of A0): 1,313 runs are read the same by the old and the new grammar and give main's bytes; 1,622 are read differently (490 syntax errors that move, 424 syntax errors and 224 unsupported features that become answers, 180 that become syntax errors, 130 unsupported that stay, 76 and 70 that become a mix, 28 that become a group mismatch) and each gives what the new reference says; they are listed in `or_a1_compat_changes.json`. Run against `main`'s binary as well (`OR_A1_BASE`): the same.
* **G-ORACLE-SQL** SQLite 3.53.4: 3,000 expressions of up to 12 conditions and 4 levels, json and csv, 1 and 4 threads: 0 differences. **G-ORACLE-REF** (`refimpl.eval_tree`): 3,000 expressions over tables with bad cells: 0 differences in the rows or in the row, column and rule of the first refusal. The section 5.1 table, as written, passes.
* **G-PROGRAM.** 10,000 expressions through `--explain`: the program is the model's exactly, forward-only, every condition reachable, and equal to the tree on every status of every leaf (to 6 leaves) or on 300 random ones, with the same sequence of leaves; the appendix model as it stands: 6,000 trees, 1,730 and 0 as quoted, and 5,030,376 cases with 0 differences.
* **G-PAR.** Threads 2, 3, 4, 8, 16, 64 by chunks 1, 7, 64, 1000 (24 runs against one thread) on bad cells that only some ranges reach and on 14 generated plans in 4 forms: identical stdout, stderr and status.
* **G-OFFSETS / G-FUZZ.** 22,000 generated flag expressions against the reference parser (accepted, offset, words, mix and its readings): 0 differences; 10,000 query where clauses against `query_ref.py`: 0; 10,000 random, mutated and byte strings: no trap, every refusal a listed rule, every offset inside the text, every offered repair accepted; `test_query.py`'s own fuzz and 3,000 plans flags-against-query with `or` and parentheses (about a third have an `or`), 12,000 runs, threads 1 and 4: 0 differences.
* **G-MUT** `scripts/or_mutants.py`: 69 mutants of A1, **69 killed** (one, "the mix repair is offered for a rest that does not translate", needed a test that was added for it; the quoted `or`, the `--where=` repair, the depth of a reading, the two `in` notes and the 65th group likewise). 30 mutants of A0's repair (and 2 older ones) are listed as equivalent (note 8). The other mutant scripts' patterns were brought up to the new text (`--check`, all 20 scripts).
* **G-EXPLAIN-SAME, G-GEN.** `site.py --check`, `schemas.py --check`, `manifest.py --check` (the authority is unchanged: same labels and effects, only function names), `mcp.py --check`, `seo_check.py`: pass.
* **G-ANDONLY-SPEED.** above (note 1): 1.000 to 1.004.
* **The cost of the program**, instructions a row (Mac, 1 M rows, one thread, same work and nothing kept, so only the evaluation differs): 4 text conditions tried, as a conjunction 1,448 and as an `or` 1,519 (+71); 4 `:int` conditions 1,806 and 1,892 (+86); 2 text conditions 1,144 and 1,193 (+49). So about **11 instructions for each further condition tried and 26 for the row**, about 20 a condition on `:int` against the estimate of section 10 (5 to 8) which was too low by two to three times. On gram (callgrind, 200,000 rows): +73 for 4 text, +91 for 4 `:int`. `status in (404, 500)` costs 46 above the cut and `status = 404 or status = 500` 185.


## Appendix: the model used in sections 0 and 4

A Python model of the evaluation (not the tool): leaf statuses 0 false, 1 true, 2 a bad cell; compares the source, the distributed DNF and the branch program. Run as it stands it prints the figures quoted above (seed 7, CPython 3.x, a few seconds).

```python
# A model of the evaluation, not of the tool: leaf status 0 false, 1 true, 2 a bad cell (a refusal if the leaf is visited).
import itertools, random
random.seed(7)

def gen(n):                                   # a random and/or tree of n leaves
    if n == 1:
        return ('leaf', None)
    k = random.randint(2, min(n, 3))
    cuts = sorted(random.sample(range(1, n), k - 1))
    return (random.choice(['and', 'or']), [gen(b - a) for a, b in zip([0] + cuts, cuts + [n])])

def number(t, c):                             # leaves numbered left to right
    if t[0] == 'leaf':
        c[0] += 1
        return ('leaf', c[0] - 1)
    return (t[0], [number(x, c) for x in t[1]])

def tree_eval(t, st, seen):                   # left to right, stops at the first decision; 2 = refused
    if t[0] == 'leaf':
        seen.append(t[1]); return st[t[1]]
    for x in t[1]:
        v = tree_eval(x, st, seen)
        if v == 2 or v == (1 if t[0] == 'or' else 0):
            return v
    return 0 if t[0] == 'or' else 1

def dnf(t):                                   # distribute: a list of conjunctions, leaf order kept
    if t[0] == 'leaf':
        return [[t[1]]]
    parts = [dnf(x) for x in t[1]]
    if t[0] == 'or':
        return [c for p in parts for c in p]
    out = [[]]
    for p in parts:
        out = [a + b for a in out for b in p]
    return out

def dnf_eval(d, st):
    for conj in d:
        ok = True
        for l in conj:
            if st[l] == 2:
                return 2
            if st[l] == 0:
                ok = False; break
        if ok:
            return 1
    return 0

def program(t):                               # each leaf: (next if true, next if false); 'A' accept, 'R' reject
    prog = {}
    def first(t): return t[1] if t[0] == 'leaf' else first(t[1][0])
    def emit(t, T, F):
        if t[0] == 'leaf':
            prog[t[1]] = (T, F); return
        for i, x in enumerate(t[1]):
            nxt = first(t[1][i + 1]) if i + 1 < len(t[1]) else None
            emit(x, (nxt if nxt is not None else T) if t[0] == 'and' else T,
                    F if t[0] == 'and' else (nxt if nxt is not None else F))
    emit(t, 'A', 'R')
    return prog

def run(prog, st, seen):
    pc = 0
    while True:
        seen.append(pc)
        if st[pc] == 2:
            return 2
        nxt = prog[pc][0 if st[pc] == 1 else 1]
        if isinstance(nxt, str):
            return 1 if nxt == 'A' else 0
        assert nxt > pc                       # forward only: no loop
        pc = nxt

def is_dnf(t):
    leaf = lambda x: x[0] == 'leaf'
    if leaf(t): return True
    if t[0] == 'and': return all(leaf(x) for x in t[1])
    return all(leaf(x) or (x[0] == 'and' and all(leaf(y) for y in x[1])) for x in t[1])

trees = differ = value_differ = dnf_form = dnf_form_differ = 0
for _ in range(6000):
    n = random.randint(3, 7)
    t = number(gen(n), [0]); d = dnf(t)
    trees += 1
    refusal_differs = value_differs = False
    for st in itertools.product([0, 1, 2], repeat=n):
        a, b = tree_eval(t, st, []), dnf_eval(d, st)
        refusal_differs |= (a == 2) != (b == 2)
        value_differs |= a != 2 and b != 2 and a != b
    differ += refusal_differs; value_differ += value_differs
    if is_dnf(t):
        dnf_form += 1; dnf_form_differ += refusal_differs
print(trees, 'random trees of 3 to 7 leaves')
print('distributed DNF refuses differently from the source:', differ)
print('distributed DNF answers differently when no cell is bad:', value_differ)
print('of the trees already in DNF as written:', dnf_form, ', refuse differently:', dnf_form_differ)
cases = bad = 0
for _ in range(4000):
    n = random.randint(1, 8)
    t = number(gen(n), [0]) if n > 1 else ('leaf', 0)
    p = program(t)
    for st in itertools.product([0, 1, 2], repeat=n):
        s1, s2 = [], []
        cases += 1
        bad += tree_eval(t, st, s1) != run(p, st, s2) or s1 != s2
print('branch program against the tree:', cases, 'cases (row statuses), differences in the answer or in the leaves visited:', bad)
t = ('and', [('or', [('leaf', i) for i in range(j, j + 4)]) for j in (0, 4, 8)])
print('(a1 or .. a4) and (b1 or .. b4) and (c1 or .. c4): 12 leaves, DNF', sum(map(len, dnf(t))), 'leaves in', len(dnf(t)), 'conjunctions')
```
