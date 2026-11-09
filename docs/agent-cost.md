# Making the common refusals fixable by an agent: #107, and what it leaves for #100

> **Status: three parts are built: the empty-cell repair (section 1), the `path.dotdot` hint through the contract package (section 2) and the compact skill (section 4).** Written 2026-11-07 for cancho-table#107, a slice of #100. It follows the rules of `docs/numbers.md`: what is measured is said as measured, the rest is a reading of the code or a judgement. **Nothing here measures an agent**: the evidence for the cost of the refusals is the audit quoted in #100 (5 of 27 refusals were an empty cell in a `:dec` column, n = 3 runs a cell, Haiku 4.5), which is not repeated and not re-run.

## 1. An empty cell in a typed column: the repair is the guard, written

**Before.** `value.not-integer`, `value.not-float` and `value.not-decimal` on an empty cell answered `repair: none` ("what the cell was meant to be is not known"), and the hint taught the guard in words: put a condition that is false for such a cell before the typed term in `--where`. An agent that read the hint had to write the expression, and the audit says it often did not.

**Now.** When the refused cell is **empty**, the repair is `choose` with one option: the same invocation with `--where` holding `COLUMN != ''` and, if there was an expression, that one after it in parentheses (so that a top-level `or` is not mixed with the `and`, `docs/or-and-columns.md`). It reuses `jnguard.guarded`, which built the same repair for the key of a join (`docs/joins.md` 5.3); the column is written as its name when that is one plain word no other column shares, quoted otherwise, and `#N` when two columns share the name.

| call | the repair |
|---|---|
| `--agg sum:amount:dec(2) t.csv` | `--agg sum:amount:dec(2) t.csv --where "amount != ''"` |
| `--where "n:int > 1" t.csv` | `--where "n != '' and (n:int > 1)"` |
| `--where "host = 'a' or n:int > 3" t.csv` | `--where "n != '' and (host = 'a' or n:int > 3)"` |

**It does what the hint says and nothing else:** the rows with an empty cell in that column are left out (`tests/conformance/test_empty_guard.py` runs the repair and compares the answer with Python's, for a decimal sum, an integer sum by group, an integer and a float condition, an order key, an expression with `or`, a quoted name and a repeated name). A second column with an empty cell is the next refusal, with its own guard in front of this one.

**Where it is not offered** (`repair: none`, as before, with the same reason):

* a cell that is **not empty** (`abc` in an `:int` column): nothing says what it was meant to be;
* a **`--query`**: the query says its own `where`, and a repair of a query refusal is the flag form (`docs/query.md` section 1); translating the guard into the flags of a query is not built;
* a **`--join-where`** (`detail.context` is `join-where`): the guard would go in the `--join-where`, which the join code builds for keys (`jnguard`) and not here;
* a **join's padded cell** (`detail.padded`): the cell is empty because the join padded it, and the guard in `--where` changes the join into an inner join on that column, which is a decision for the person.

**Declared in the catalogue.** The three rules were `never` repaired; they are `sometimes` now, in `finish.extra()`, `introspect` and `docs/refusals.md`. `test_rules` holds their fixtures to an empty cell, which has the repair, and `test_empty_guard` holds the cases without one.

## 2. `path.dotdot`: done through the package (2026-11-08)

The hint of `path.dotdot` is the **contract package's** (cancho-tools, `toolbox.path`), and `tests/conformance/test_toolbox_pin.py` holds the tool to the package's wording, so the change was made there: cancho-tools#62 asked for it and cancho-tools#63 made it ("name the file without `..`: run the tool in the directory that holds it, or give --root DIR and name the file relative to DIR (the tool does not resolve `..`, ...)"). `cancho.toml` now pins `c7131fd`, the merge of #63, and `docs/architecture.md` has the paragraph for the move, which also brought in cancho-tools#60 (a text or csv refusal keeps its hint, repair and detail). Not measured: whether an agent saves a turn with it.

## 3. What was not measured

* Whether an agent finishes more tasks with the repair: no agent was run. The change makes the refusal carry the fixing call, which is what the audit said was missing; whether that moves the numbers of #100 is for the held-out re-test that issue asks for.
* The cost of the repair: building it reads the arguments again (`jnguard.guarded`) and happens once, on a refusal; no timing was taken.

## 4. The compact skill (#105, 2026-11-09)

**Measured before the change.** `table skill` printed **88,627 bytes** (sha256 `3624c137...`), of which the 8,846-byte `description` line is printed twice (in the front matter and as the first paragraph), the rest being the usage forms, the flags with their help, the rules and the authority report. At four bytes a token that is on the order of 22,000 tokens read before a first call. (The 43 KB of `tools/list` over MCP is #106, a different text.)

**What it is now.** `table skill` prints a **compact profile** of 1,770 bytes, written in `tools/table/skillc.cho`, and `table skill --full` prints the exhaustive text exactly as `table skill` printed it: the contract package's generator, the same `Tool` literal, **byte for byte** (measured by building the commit before the change and comparing the two outputs: the same sha256). The profile says what the tool returns, leads with `--query` and the flags that mean the same, lists the five forms with a command each (the shape, rows, groups, a join, the type report) and `--explain`, the `WHERE` grammar in two lines, the types and why `10` sorts before `9` as text, the empty-cell guard, the page size and where the next page starts, the three kinds of `repair`, and where the rest is. It does not list flags or limits; it says where they are.

**A decision, and why it differs from the issue.** The issue asked for the profile to be *generated* from the tables of `introspect`. It is not: the lines worth reading are prose, and a generator that picks lines out of an 8.8 KB summary and 40 flag helps cannot make them (the summary alone is 8.8 KB). It is a literal, and the tests hold it to the tool so that it cannot drift unseen (`tests/conformance/test_skill.py`, `Compact` and `Full`): the size is at most 2,048 bytes; every `--flag` it names is in `introspect`; every rule tag it names is a rule of the tool; every command in it is run on a fixture and answers `ok`; the guard and the sentence on text order are run; the types it lists are accepted by `--where`; the page size is `introspect`'s limit. The sentences that README.md said about the skill ("generated from the same tables, so it cannot disagree with the program") are true of `--full` and `introspect`, and corrected for the default.

**Where it is done.** Not in the contract package: `toolbox.cli.subcommand` answers `skill` only when it is the whole command line, and the package's generator is left as it is, so `--full` is that generator unchanged. `table.run` answers `skill` itself and sends `skill --full` to the package. `table skill` followed by anything else is still a file operand, as before; `--fully` and the like are refused.

**What moved with it.** The tests that read the skill for a claim (`test_skill`, `test_describe`, `test_rules`, `test_stdin`, `test_jsonl_out_keys`, `test_jsonl_out_more`) read `skill --full`; the project page's excerpt (`scripts/site.py`) shows the profile.

**Not measured.** Whether the profile lowers the cost of an agent: the numbers in #100 were taken on tasks that the draft of this text was written after seeing fail, with three runs a cell. The held-out re-test #100 asks for is the evidence, and nothing here replaces it. Not measured either: whether an agent that wants a flag the profile does not list asks for `--full` or `introspect`, or guesses.
