# Making the common refusals fixable by an agent: #107, and what it leaves for #100

> **Status: both parts are built: the empty-cell repair (section 1) and the `path.dotdot` hint through the contract package (section 2).** Written 2026-11-07 for cancho-table#107, a slice of #100. It follows the rules of `docs/numbers.md`: what is measured is said as measured, the rest is a reading of the code or a judgement. **Nothing here measures an agent**: the evidence for the cost of the refusals is the audit quoted in #100 (5 of 27 refusals were an empty cell in a `:dec` column, n = 3 runs a cell, Haiku 4.5), which is not repeated and not re-run.

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
