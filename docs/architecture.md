# Architecture and the capability model

How `table` is put together, and what its authority row says. Short on purpose: the designs are in [select.md](select.md), [filter.md](filter.md) and [parallel.md](parallel.md).

## The pieces

```
   argv                                                       standard output
    |                                                                ^
    v                                                                |
 [ contract package ]  cli: flags         out: output ------------> [ writer.cho ]
   (cancho-tools,      describe: introspect, skill                   csv and json fields
    pinned in          fail, limit: rules and limits                      ^
    cancho.toml)      path, place: confinement under --root              |
    |                  lines: the 64 KiB line reader                      |
    v                                                                     |
 [ table.cho ]  flags -> [ query.cho / plan.cho / expr.cho ] -> the plan      |
    |                    one Query, whichever flags filled it             |
    |   header read, then the plan resolved against it ([ frame.cho ])     |
    v                                                                     |
 [ reader.cho ]  RFC 4180: records, fields   --->   [ engine.cho ] ---------+
    ^                                              per row: ragged? --where? emit or add
    |                                                   |
    +-- sequential read (table.cho)                      v
    +-- parallel read (par.cho, scan.cho):          [ agg.cho ]  groups, sums, limits
        ranges of the file, one thread each,
        the parent takes them in file order
```

| piece | what it does |
|---|---|
| contract package (`cancho-tools`, modules `cli`, `describe`, `fail`, `limit`, `lines`, `out`, `path`, `place`, `text`) | the parts every tool of the family shares: the flag table, `introspect` and `skill`, the `table.v2` envelope, rules and repairs, limits, the line reader, paths confined beneath `--root`. Pinned by commit in `cancho.toml` (the pin and what moving it changed: the section "The pin" below), installed by `cancho build` |
| `tools/table/table.cho` | the flags, the sequential read loop, the answers. `body` reads the plan only through one `Inputs` value (`inputs.cho`): `body` gives `plan_body` the flags' values, `query_body` a `--query`'s translation |
| `tools/table/inputs.cho` | the `Inputs` value: what the plan is made from (the columns, the condition, the grouping, the aggregates, the order, the cut and the page, the rows to read, the source), filled once from the flags, or from a `--query`'s translation (`inputs.for_query`); a standard-input source fills the same value (`docs/query.md` Q0, Q1) |
| `qarena.cho`, `qlex.cho`, `qparse.cho`, `qtrans.cho` | the front end of `--query`: the query's bytes to tokens, the tokens to records, the records to the flags the query stands for (one function per flag for how a name is written in it); the existing parsers of the flags then fill the one `Query`. A hand-written recursive descent over one block of integers (docs/query.md section 14) |
| `qtext.cho`, `qrefuse.cho`, `qrepair.cho`, `qpath.cho` | what its refusals say (`query.syntax`, `query.unsupported`, `query.group-mismatch`, `query.too-long`: offset, expectation or feature), the repair that offers the corrected invocation, and the check of a query's `from 'path'` rule for rule as an operand's |
| `explain.cho` | `--explain`: reads the header of a file with the reader the run uses and nothing after it, resolves the plan with `frame.prepare`, refuses with `finish.explain` as the run does, and otherwise writes the plan as one document (`docs/query.md` section 15). The read loops of `table.cho` are not touched |
| `tools/table/cellnum.cho` | the exact readers of one cell: `parse_int` (64 bits), `parse_dec` (at a scale, below 10^18 once scaled), `pow10` and `int_min`. It imports nothing and knows nothing of the plan, so it is published as a store another repository pins (`docs/numeric-package.md`); `query.cho`'s `parse_typed`, `expr.cho`, `agg.cho`, `sorter.cho` and `report.cho` call it. Taken out of `query.cho`, which holds the plan and is not published |
| `query.cho`, `plan.cho`, `expr.cho`, `frame.cho` | the plan: `--select`, `--where`, `--group` and `--agg` fill one `Query`; it is resolved against the header before any row is read, so a wrong column is refused at the cost of one line |
| `reader.cho` | the RFC 4180 reader of its own: quotes, doubled quotes, newlines inside quotes, a byte order mark |
| `jout.cho` | JSON lines out (`--format jsonl`, docs/readers.md section 13): the keys of the objects, made once from the answer's columns and carried to every writer and thread in the tail of `sel`; one selected row or one group as an object |
| `jfields.cho`, `jrec.cho`, `jscan.cho`, `jsonl.cho`, `jflags.cho`, `jdisc.cho`, `dispatch.cho` | JSON lines as a second reader (`--input-format jsonl`, docs/readers.md section 12): the declared columns (`--fields`: paths, parsed once into the header and a table of segments), one record's cells found in the tape `std.json.parse_with` fills (state and tape made once per reader, per thread), the sequential loop for a file or standard input (`jsonl.read`, a loop of its own beside `read_file`), the range scan a worker runs (`jscan.scan_range`, chosen per range by `dispatch.range`, so `scan.scan_range` is not edited), the checks of the flags (`jflags.check`) and `--discover` (`jdisc.run`). The engine sees the same record, cells and line as for CSV |
| `jtab.cho`, `jnflags.cho`, `jnhead.cho`, `jnkey.cho`, `jnfail.cho`, `jnread.cho`, `jnexplain.cho` | the join (`--join`, docs/joins.md, slices J1 and J2): the build table (`jtab.cho`: the distinct keys in a `gmap`, the rows of a key as a chain in file order, or the keys alone for a semi or anti join, the accounted bytes counted as rows are added), the checks of the flags and the reading of `--on` (`jnflags.cho`: up to 8 pairs, a type at the end of a pair, the shared-name form), the key of a row when it is not one pair of text (`jnkey.cho`: the 8 bytes of a typed value, a length before each component of several), the two headers, the key columns, a name both files have and the header of the joined row (`jnhead.cho`), the refusals only a join raises (`jnfail.cho`), the read loops (`jnread.cho`: the build file whole, then the probe streamed, each joined row handed to `engine.process_rows` as one CSV record; a loop of its own beside `read_file`, which does not know a join exists) and `--explain` of a join (`jnexplain.cho`) |
| `engine.cho` | what is done with one row: counted, ragged (never used), kept by `--where`, then written or added to its group. Both reads call the same functions |
| `agg.cho`, `gmap.cho`, `gsort.cho` | groups and their sums, in bounded memory (`gmap.cho` is the hash table from key bytes to group); the output order is the key order or `--sort`; `gsort.cho` is the key order, by radix, whose depth is not a recursion (`docs/gap-groups.md` section 9) |
| `dec.cho`, `flt.cho`, `facc.cho`, `pow5.cho` | the numbers: `:dec(S)` as scaled integers and the exact mean, `:float` read as the nearest double and written as the shortest decimal, the exact float sum (a 72-limb accumulator), and the generated table of powers of five for the float reader |
| `dtparse.cho`, `dtfail.cho`, `dtrepair.cho` | the dates: `:date`, `:timestamp` and `:datetime` read as days and microseconds (a strict subset of RFC 3339, calendar-validated, no zone database), written back in the normal form, and the seven refusals with their repairs and the day-boundary repair of a date written where an instant is read (`docs/datetime.md`) |
| `report.cho` | `--report types`: what each column's cells are, counted, and the declaration that would read them; it dispatches `--report dates` to `reportdt.cho` |
| `reportdt.cho`, `reportflags.cho` | `--report dates`: the cells counted as date, timestamp, datetime or other, and the declaration that would read them; the flags `--report` does not go with |
| `dtbucket.cho`, `dtjoin.cho` | the period of a group key (`--group ts:timestamp@day`); the refusal of a date type key cell of a join |
| `scan.cho`, `par.cho` | `--threads`: ranges of the file read by threads, each assuming it starts at a record; the parent takes them in file order and reads again any it cannot trust, so the answer is the one-core answer |
| `sorter.cho` | `--order-by`: the rows held, their keys and an index sorted by a stable merge; with `--top` or a page it keeps only the best rows, and past `--max-sort-rows` it refuses. Its state is the grouping's value, used for rows; on its own it reads on one core (`psort.cho` below) |
| `psort.cho` | `--order-by` with `--threads`, for a whole sort written as CSV (no `--top`, `--limit` or `--from`): each range sorted into a run by a `sorter.cho` state, the runs merged in partitions by as many threads, the sequential bytes. A page, `--top` and JSON output stay on `sorter.cho` alone |
| `writer.cho` | the fields as CSV (minimal quoting) or JSON |
| `generated/table/built.cho` | the authority, the schema and the compiler pin, embedded in the binary; written by `scripts/manifest.py` |

## The capability model

cancho programs get their capabilities (files, directories, standard streams, threads, memory) as values, and a program that does not hold one cannot use it. The compiler can derive from the source what a program can reach: `cancho authority`, described in cancho's `docs/authority.md`. The result is a list of labels, whether the program is `bounded`, what it is `unbounded_by`, and any foreign symbols. `table` embeds its own, and `table introspect` prints it.

`table`'s row (`manifests/table.authority.json`):

| label | why it is there |
|---|---|
| `args` | the flags and the file name |
| `file_read` | reading the input file |
| `io_read` | standard input, when the input is `-` or there is no file: read in blocks with `read_bytes` (`bulk.cho`), in one pass on one core. Approved for the standard-input stage (R1, `docs/readers.md` section 10) and in no other place; the ceiling in `tools.toml` names it |
| `fs_read("")` | the path is only known at run time, so the row cannot name it; `table introspect` lists this under `not_narrowable` |
| `dir_read` | read through a directory handle: a label that names no path, because the handle is the authority (cancho `docs/directory-handles.md`); a row with `dir_read` and not `fs_read` can reach only what handles it was given |
| `heap` | memory for the record, the page and the groups |
| `io_write` | standard output |
| `err_write` | standard error |
| `conc` | `--threads`: spawn and join. The threads hold nothing the process did not already have: a file opened by the same path and a heap forked from the parent's |

Not in the row, and so not possible for this program: `net_out`, `net_in`, `ffi` (foreign code), `clock`, writing a file or directory (`file_write`, `dir_write`). Not that the program has no bugs: the row says what it may reach, not that it is correct. The list of what a row cannot say is also in `introspect`: the extent of a path, what a symbolic link reaches, what standard output carries, and resource use beyond the tool's caps.

### What CI checks

* `tools.toml` is the ceiling a person writes and reviews: the labels `table` is allowed. No tool may hold `ffi`, `net_out`, `net_in` or `clock`, and `bounded` must be true.
* `python3 scripts/manifest.py --check` derives the authority over the sources and the installed contract package and requires that it is within the ceiling, equal to the committed manifest, and equal to the one embedded in the binary (and to what the binary prints).
* `python3 scripts/manifest.py --check --against ../cancho-tools/contract` requires that the installed package says the same thing as the sources it was published from.
* On Linux, the conformance tests run the binary under `strace` and require no socket and no write on disk.
* `python3 scripts/site.py --check` requires that the authority row on the project page is the committed one.

A new capability is therefore a change to `tools.toml` that somebody has to approve.

## The pin (cancho-tools)

The contract package is pinned by commit, one `[dependencies.NAME]` line for each of the nine modules `table` imports (`cancho.toml`), and never copied. Three pins have been used:

| pin | what it was |
|---|---|
| `25acde0` | the package when `table` was written |
| `919e7ba` | `25acde0` plus the one change of cancho-tools#43 (`toolbox.cli`: a flag lookup is linear in the table), taken from a side branch (`table-pin-linear-cli`) so that nothing else of the package moved |
| **`af73b1b`** | the head of cancho-tools `main` on 2026-10-21: #39 to #44, adopted as a step of its own (this section). The nine modules of `af73b1b` are those of `afc56dd` (#43 merged); #44 (`archive`) touches no module `table` uses |

**What the move changed in the bytes of `table`: four hints.** Of the nine modules, five are byte-identical to the earlier pin's (`fail`, `lines`, `out`, `place`, `text`); `cli`, `path`, `describe` and `limit` changed, and what changed in them is, in cancho-tools' sources (`contract/cli.cho`, `contract/path.cho`, `contract/describe.cho`):

| rule | where it is written | hint before | hint now |
|---|---|---|---|
| `args.bad-value` | a flag's value is not of the kind the flag takes (`--where ''`, `--limit x`, `--format xml`, ...) | `null` | `give the flag a value of the kind detail.expected names` |
| `args.missing-value` | a flag is last, or is followed by another flag | `null` | `put the value after the flag, or write the two as --flag=value` |
| `path.empty` | the operand (the file name) is empty | `null` | `name a path; an empty argument names nothing` |
| `path.empty` | `--root` is empty (not reachable: a flag value cannot be empty, so `--root ''` is `args.bad-value`) | `null` | `name a directory, or leave --root out` |
| `path.too-long` | the operand is longer than 4096 bytes | `null` | `use a path of at most 4096 bytes` |
| `path.too-long` | `--root` is longer than 4096 bytes | `null` | `name a root of at most 4096 bytes` |

The other changes of the package are not reachable from `table`: `describe.introspect` and `describe.skill` write a different `concurrency` sentence for a tool whose reversibility is `irreversible-consequential` (`table` is not one), and the `limit` store changed only in its recorded requirement on `cli`.

**What did not change**, measured (the same status, standard output and standard error, but for those hints): the exit status and `code` of every refusal; every `message`; every `repair`; every `detail`; `introspect` (byte for byte), `skill` (byte for byte), `--help`, `--version`; `--explain`; the 166 plans of `scripts/corpus.py` (md5 `fccb01b66dd7518d74611219a6c5aa29`), `scripts/query_corpus.py` and `scripts/explain_corpus.py` (0 different); the answers of the MCP server (the refusal of a call is `table`'s own line, so it carries the new hints; `tools/list` is the same bytes); the authority row (`args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`) and the embedded manifest (`scripts/manifest.py` rewrote no file); the schemas. `csv`, `jsonl` and `text` refusals are written on standard error as `table: RULE: message` and never carried a hint, so they are the same bytes.

**How it was measured** (the earlier pin's binary is `origin/main` at `5b9dddc`; the new one is this branch's; run on 2026-10-21 to 22 on one Linux machine): (1) the whole conformance suite was run once with `tests/conformance`'s binary replaced by a program that runs both binaries on the same arguments, standard input and working directory (the same `argv[0]`, so a repair's `argv` is the same text) and records every call whose status, standard output or standard error differ: **689 calls differ**, and in every one the answer with the hints masked is byte-identical, status and standard error included; the differences are 1,368 `args.bad-value` hints, 6 `path.empty`, 6 `path.too-long` and 4 `args.missing-value` (counted per error in the answer, `error` and `errors` both); (2) a probe of 137 calls (every flag with an empty, a wrong and a missing value, the unknown-flag and typo paths, a duplicated flag, `--flag=value`, paths, the join and query errors, `--explain`, in json and csv): 101 identical, 36 differ in the hints above and nothing else; (3) the three corpora; (4) ten calls of the MCP server for each binary.

**Three refusals still carry no hint**, with this pin and with the head of cancho-tools (`4e3a8d7`, checked): `io.not-a-directory`, `io.permission-denied` and `io.read-failed`. The hint is written in the package, not in `table`: `fail.io_error` (`contract/fail.cho`, `io_error`, the `if errno == ...` ladder after `var hint: &static [byte] = "";`) gives a sentence to `io.not-found` (errno 2), `io.is-a-directory` (21) and `path.symlink` (40, 62) and leaves it empty for errno 20, 1 and 13, and for any other errno, and `fail.open` writes an empty hint as `null`. `table` only calls `fail.io_error` (`table.cho`, `finish.cho`, `jnrun.cho`, `jnexplain.cho`), so a hint here would have to be written in `table` by wrapping or copying the package's function, which this repository does not do (the package is pinned and never copied). What cancho-tools would have to change, with the sentences proposed, is an item of `docs/backlog.md`. `tests/conformance/test_toolbox_pin.py` lists the three: a pin that gives them one fails there until the list says so.

**`table`'s own `path.empty` and `path.too-long`** (a follow-up of this move). `tools/table/qpath.cho` checks the path of a query's `from 'path'` and `join 'path'`, and of the file of `--join`, "rule for rule" as `toolbox.path.operand` checks an operand, and wrote its first two refusals with an empty hint. They say the package's sentences now, word for word (`name a path; an empty argument names nothing`, `use a path of at most 4096 bytes`); every other byte (code, message, repair, detail) is the one it was. `test_toolbox_pin.OwnPathRefusals` reads the sentences from the installed package and requires `qpath.cho`'s literals to be them. They are reached by `--query "select a from ''"`, a query `join ''`, and, for `path.too-long`, `--join` with a name over 4096 bytes (a query is capped at 4096 bytes, so a `from` path cannot be longer), in the answer and in `--explain`. The other refusals of `tools/table/*.cho` were read for an empty hint (every `fail.open` and `fail.open_in` call and every helper that takes a hint): no other writes one, and no other duplicates a rule of the contract with a hint (`qpath.cho`'s `path.dotdot`, `path.absolute` and `path.outside-root` already carry one; its `path.dotdot` sentence is not the package's, which also carries a repair, and is not changed here).

**The MCP annotations of cancho-tools#42** are not in the contract package (the nine modules `table` imports have no MCP code); they are written in cancho-tools' own `scripts/mcp.py` (`annotations_of`) and `server/mcp.cho`, which `table` copies and adapts rather than imports (`docs/mcp.md` section 7), so the move of the pin did not touch them. `table`'s adapted generator already wrote the four hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) with #42's values for `table`; the one member it lacked was `title`. It is adopted in a follow-up: `scripts/mcp.py` writes `"title": d["tool"]` (the tool's name, from `introspect`, as `annotations_of` at `af73b1b` does), so `tools/list` carries `"annotations":{"title":"table", ...}`; that member is the whole change of the answer (`generated/mcp/tools.cho` is regenerated by `scripts/mcp.py`; the other four members and their values are the same). What stays different from #42 is how the four hints are derived (from `reversibility` and the effects there, from the effects here, the same four values for `table`).

**The frozen goldens.** Three tests held `table` to the md5 of what it answered before (`test_not_a2_compat.Compat.test_golden`, `...test_every_case_that_slice_a1_pinned_and_a2_does_not_read_differently_is_unchanged`, `test_or_a1_compat.Compat.test_golden`), and read it through `or_a0.digest` and `query_join_golden.digest`. Both digests now read the answer through `toolbox_pin.before`, which puts exactly the four hints above back to `null` (and the same two sentences of `table`'s own `path.empty` and `path.too-long` in `qpath.cho`, since the follow-up that gave them the package's wording; they were `null` at the earlier pin): **no golden file was re-recorded and no golden line changed** (`or_a0_golden.json`, `or_a1_main_golden.json`, `not_a2_base_golden.json`, `not_a2_compat_changes.json`, `query_join_main_golden.json` are the bytes they were); a golden still fails on any other change of a byte, and the new hints are pinned text by text in `test_toolbox_pin.py`. (The same way as `rename_b1.j3b_hint_moved` for the hints J3b moved, but for the bytes and not the rule.)

**Cost.** Callgrind instructions, 2-row files, valgrind 3.22.0 on this machine: a call costs 261 to 344 instructions more than before (0.03 to 0.06 per cent of a call of about 0.5 million: `shape` 514,902 to 515,177; `join inner` 920,904 to 921,216; `skill` +240; `introspect` +106); a refusal of the four rules 894 to 1,214 more (about 0.3 per cent of 0.37 million). The per-row cost of the 38 cells of `scripts/join_callgrind.py --all` is unchanged at four decimals (every ratio 1.0000, the bound is 1.01). No clock time was measured.
