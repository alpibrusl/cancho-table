# The numeric modules as `.cancho-vcs` stores (design, gates first)

`table` is a binary repository: no commit of it holds a package, so another repository cannot pin its exact readers. Six modules of
`tools/table` are published as stores, one per module, at `.cancho-vcs/<module>` (the way cancho-tools commits `.cancho-vcs/toolbox.*`):
`pow5`, `dec`, `flt`, `cellnum`, `facc`, `reader`: the cell parsers (`pow5`, `dec`, `flt`, `cellnum`), the exact float sum (`facc`) and RFC 4180 records (`reader`). A consumer writes `[dependencies.flt] git = ..., rev = ..., path = ".cancho-vcs/flt"`
and `cancho install` fetches it; the stores a module imports are reached through its `requires/` (so `flt` brings `dec` and `pow5`).

The stores are a publication of `tools/table/*.cho` as they are.

**`query` was published in #44 and is withdrawn.** #44 published `query` as "the cell parsers", but `tools/table/query.cho` is the table's own plan
(`Query`: names, conditions, aggregates, meta; ten internal modules import it), and it held `parse_int`, `pow10` and `parse_dec` beside it. A consumer
that pins it pins an internal interface. The three parsers, and `int_min`, which `parse_int` accumulates towards, now live in `tools/table/cellnum.cho`
(`module cellnum;`: no imports at all; `query.cho` keeps the plan and `parse_typed`, and imports `cellnum`), and `cellnum` is published instead of `query`. The name
is not `cells`, which was the provisional one: `cells` is already the name of the slice of field offsets in 14 modules of this program (238 uses), and
`cells.parse_int(record[cells[3 * column]..])` compiles but reads as one object. A pin at the revision of #44 keeps working, because that revision still holds the
`query` store; a pin at a later revision moves to `cellnum` (`query.parse_int` is `cellnum.parse_int`, `query.parse_dec` is `cellnum.parse_dec`, `query.pow10` is
`cellnum.pow10`; the signatures and the behaviour are the same, see the golden test below).

## What is checked (fixed before `scripts/publish_numeric.py` is trusted)

| Gate | Fails when |
|---|---|
| `python3 scripts/publish_numeric.py --check` | a fresh publish of the six sources, into a temporary directory, differs byte for byte from the committed `.cancho-vcs`: a changed source, a hand-edited store file, a store file missing, a store file or a whole store extra, or the directory missing |
| the compiler is the pinned one | `cancho --version` does not report the `cancho = "<rev>"` of `cancho.toml` (the bytes of a store depend on the compiler; the check refuses another one and says so) |
| the module list | a source does not declare the module of its file name, or imports a non-`std` module that is not published |
| a published module is a stable interface | a published module declares `struct Query` (the table's plan, `pub res struct Query`, or not `pub`): it is internal whatever else it holds. Together with the line above: a published module imports `std.*` and other published modules only. `--check` and the publish both refuse, naming the module (it failed on #44's set: `query declares the table's plan struct`); `tests/conformance/test_publish_numeric.py` runs the rule on texts |
| the parsers do what the arithmetic says | `scripts/cellnum_golden.py`: 3013 cases (684 `parse_int`, 2310 `parse_dec`, 19 `pow10`: empty, sign alone, leading zeros, 17 to 25 digits, 2^63-1, -2^63, 2^63, overflow, scale 0 to 18, more fractional digits than the scale, trailing junk, a second point, non-ASCII digits, 600 seeded random cells) against Python's integers, never the code's own answers, through (a) the built `table` binary, (b) the module's sources, (c) the published store (`--leg store --rev`, a project that depends on the store by git, rev and path; by hand, the revision has to be pushed). (a) and (b) run in `tests/conformance/test_cellnum.py`, with controls that must fail (a reference that takes 2^63 as a value; a reference that accepts trailing junk; a source that does not refuse the extra fractional digits) |
| consumption (run by hand, not in CI) | a project outside this repository that depends on the stores by `git`, `rev` and `path` cannot `cancho install`, `cancho build --bin`, or prints a wrong answer |

The gate has to be able to fail: it was shown failing after (a) a one-byte change to a source and (b) a hand edit of a store, and passing
again after the republish. The results are in the pull request text, with the compiler that published the stores.

A store published by the pinned compiler is read by other compilers (a consumer's compiler need not be the pin).

## Commands

    python3 scripts/publish_numeric.py            # rewrite .cancho-vcs/{pow5,dec,flt,cellnum,facc,reader}
    python3 scripts/publish_numeric.py --check    # change nothing; exit 1 on drift

The compiler is `$CANCHO`, or `cancho` on `PATH`. After any change to one of the six sources, run the first command and commit the
stores with it: CI runs the second.
