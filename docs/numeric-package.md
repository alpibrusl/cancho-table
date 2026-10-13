# The numeric modules as `.cancho-vcs` stores (design, gates first)

`table` is a binary repository: no commit of it holds a package, so another repository cannot pin its exact readers. Five modules of
`tools/table` are published as stores, one per module, at `.cancho-vcs/<module>` (the way cancho-tools commits `.cancho-vcs/toolbox.*`):
the cell parsers `pow5`, `dec`, `flt`, the exact float sum `facc` and the RFC 4180 records `reader`. A consumer writes `[dependencies.flt] git = ..., rev = ..., path = ".cancho-vcs/flt"`
and `cancho install` fetches it; the stores a module imports are reached through its `requires/` (so `flt` brings `dec` and `pow5`).

No source changes: the stores are a publication of `tools/table/*.cho` as they are.

**`query` is not published.** #44 published a sixth store, `.cancho-vcs/query`, and described it as a cell parser. It is not one: `query` is
the table's own plan (`Query`: names, conditions, aggregates, meta), it imports `flt`, and ten internal modules import it, so publishing it
handed a consumer the table's internal interface. It is withdrawn here. A consumer that pinned `.cancho-vcs/query` from the #44 merge must stop
(none is known: a search of the alpibrusl repositories found none). The other five stores are unchanged byte for byte by the withdrawal
(none of them imports `query`), and the gate refuses a `query` store committed again (it is an extra store).

## What is checked (fixed before `scripts/publish_numeric.py` is trusted)

| Gate | Fails when |
|---|---|
| `python3 scripts/publish_numeric.py --check` | a fresh publish of the five sources, into a temporary directory, differs byte for byte from the committed `.cancho-vcs`: a changed source, a hand-edited store file, a store file missing, a store file or a whole store extra, or the directory missing |
| the compiler is the pinned one | `cancho --version` does not report the `cancho = "<rev>"` of `cancho.toml` (the bytes of a store depend on the compiler; the check refuses another one and says so) |
| the module list | a source does not declare the module of its file name, or imports a non-`std` module that is not published |
| consumption (run by hand, not in CI) | a project outside this repository that depends on the stores by `git`, `rev` and `path` cannot `cancho install`, `cancho build --bin`, or prints a wrong answer |

The gate has to be able to fail: it was shown failing after (a) a one-byte change to a source and (b) a hand edit of a store, and passing
again after the republish. The results are in the pull request text, with the compiler that published the stores.

A store published by the pinned compiler is read by other compilers (a consumer's compiler need not be the pin).

## Commands

    python3 scripts/publish_numeric.py            # rewrite .cancho-vcs/{pow5,dec,flt,facc,reader}
    python3 scripts/publish_numeric.py --check    # change nothing; exit 1 on drift

The compiler is `$CANCHO`, or `cancho` on `PATH`. After any change to one of the five sources, run the first command and commit the
stores with it: CI runs the second.
