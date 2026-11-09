# `--out FILE`: write the answer to a file, atomically (issue #108): a design

> **Status: accepted by the maintainer on 2026-11-09 (section 9, decisions 1 to 5, all as proposed); built, with the differences of section 11.** Written 2026-11-09 for cancho-table#108. The text below is the design as proposed, kept; where the build differs the difference is said in section 10 when there is one. (Original status: a design, nothing built.) Written 2026-11-09 for cancho-table#108, the first slice of #101. It follows the rules of `docs/numbers.md`: what is measured is said as measured, the rest is a reading of the code or a judgement, and what only a person can decide is listed in section 9. **This design widens the tool's authority ceiling** (`tools.toml`), which until now says "nothing written to disk"; that is the one decision here that cannot be left to the tests, and it is first in section 9.

## 1. What it is

`table ... --out FILE` writes the answer to `FILE` and not to standard output. The file is written whole and put in place by a rename, so a reader never sees half an answer, and a run that is refused or fails leaves no file. The bytes in the file are the bytes standard output would have had, for every `--format` (`json`, `csv`, `jsonl`, `text`). It exists so that a pipeline step needs no shell redirect, and `--format csv --out clean.csv` is the last step of the cleaning pipeline of #101.

## 2. The authority: what the tool may do after this, and nothing more

Today the ceiling (`tools.toml`, `[table]`) is `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`: it reads files and writes standard output and standard error. `--out` needs the write side of `toolbox.atomic` in the contract package (cancho-tools `contract/atomic.cho`): a sibling temporary file created with `O_EXCL` in the directory that holds the destination, written, synced, then renamed to the name (`create`: the kernel refuses a name that is taken, `replace`: over an existing one), all through a directory handle opened with `toolbox.place.parent`, so no step resolves a path string and none follows a link. Read from the signatures of `atomic.replace` and `atomic.create` (`[dir_write, file_write]`), the labels to add are **`dir_write` and `file_write`**, and `scripts/manifest.py` derives the real set when the code exists; nothing else is added (`net_*`, `ffi`, `clock`, `exec` stay forbidden).

What that lets a call do, in a sentence for the person who reviews the ceiling: **write one file, whose name the caller gives, in a directory beneath `--root`; never outside `--root` (the rules `path.outside-root`, `path.dotdot`, `path.symlink` of the input paths apply to the output path), never over an existing file unless `--overwrite` is given, and never anything but the answer of the call.** `--out` without `--root` is refused (`args.required-flag`): an output path with no root to be beneath is the unconfined write the design does not offer.

## 3. The flags and the rules

| | |
|---|---|
| `--out FILE` | the answer goes to `FILE`, relative to `--root`, whose parent directory must exist (the tool creates no directory) |
| `--overwrite` | with `--out`: replace a file that is there (`atomic.replace`, under the sidecar lock that `toolbox.atomic` takes); without `--out`, `args.required-flag` |
| existing `FILE`, no `--overwrite` | `out.exists`, exit 2 (the caller can fix it), `repair` of kind `choose`: the call with `--overwrite`, and the call with another name (the stem plus a counter), as `join.column-collision` offers its prefix |
| a path outside the root, `..`, a link | the existing `path.*` rules (the path is in `detail.path`, section 11) |
| a refusal of the run | the run's own, as today; **no file is created**, because nothing is written to the destination before the answer is whole and the status is 0 |
| the write fails (no space, no permission, a rename the file system refuses) | `out.write-failed`, exit 1 (it is the environment's, not the caller's), `repair: none` with the errno in `detail`; the temporary is removed |
| the answer is larger than the bound (section 5) | `limit.state-too-large` with `detail.holder` `output`: the rule and the repairs that already exist for a bound met |

Every refusal has a rule tag and a hint, as the project requires; `docs/refusals.md` lists the three new ones (`out.exists`, `out.write-failed`, and `args.required-flag` for the two missing companions) when they are built.

## 4. What standard output says

With `--out` standard output is not the answer, so it is free for a **receipt**, one JSON line of schema `table.v2` whatever the `--format`: `{"ok":true,...,"data":{"out":{"path":"clean.csv","bytes":1234,"rows":99}}}`. An agent that wrote a file wants to see that it did and how big the file is, and a tool that says nothing on success is the tool whose success is a guess. `rows` is the number of rows or groups written, `bytes` the size of the file. A refusal is the same refusal as without `--out`, on the stream it had.

## 5. Bounded, and where the bytes are held

`toolbox.atomic.create` and `replace` take the whole content as one byte slice: the answer is held in memory until it is written. That is a bound to state and to enforce, not to hide: **the held answer is counted in `--max-state-bytes`**, with the groups and the rows to sort that the call also holds (docs/joins.md section 22 gives the formula for the others; the output counts its length plus the buffer's slack), so there is still one number for the memory of a call. A run that would pass it is `limit.state-too-large` with `detail.holder: "output"`, and the repair that rule already offers (a larger `--max-state-bytes`, up to the ceiling; or `--limit`/`--top` for a smaller answer). A `--format csv` answer that is the whole of a large file therefore needs `--max-state-bytes` raised, and the hint says so; the alternative, streaming into the temporary file, needs a write primitive that `toolbox.atomic` does not have (it would be a change to the contract package, and is the right follow-up if the bound proves too tight).

## 6. How the code changes: the measured cost

All output goes through `io` (`write_bytes`), and cancho has no way to point standard output at a file without foreign code, which the ceiling forbids. So the answer is written to a **sink** the call owns, a buffer, and the sink is written to the file at the end. Measured on `main` (`29e2d59`): 41 functions take an `io: &!i Io` in 14 modules, 20 call sites flush rows or finish an answer (`engine.flush`, `engine.flush_last`, `psort.finish`, the document writers) in 9 modules. The change is a `Sink` value (a `res` struct: standard output, or a buffer) that those functions take beside `io`, and a flush that writes to the one or the other; the threaded readers (`par`) hand their rows back to the main thread, which already does the writing, so no thread writes a file. About 40 signatures gain a parameter and no behaviour of the tool without `--out` changes: a gate (section 8) holds that.

## 7. MCP

`--out` and `--overwrite` are **withheld from the MCP server** (`WITHHELD_FLAGS` in `scripts/mcp.py`): the server's authority stays what it is, a program that starts the one binary and returns its output, and a client that wants a file asks for the csv and keeps it. `tools/list` has no `out` property, and a test says so.

## 8. The gates, to be written before the code

1. **Bytes.** For every `--format` and the whole corpus of the equivalence gates (`test_query`, the join corpus), the file written by `--out` equals standard output of the same call, byte for byte; and without `--out` the answers of `main` are unchanged (the corpus md5 of docs/joins.md EQ).
2. **No file, no litter.** A refused run leaves no file and no temporary in the directory; so does `out.exists`; the directory listing before and after is equal. A kill (`SIGKILL`) during the write leaves at most one sibling temporary whose name `toolbox.atomic` derives from the destination and the content's hash, and never the destination itself (the test kills the process in the window by writing to a full file system or a read-only directory, and checks the destination).
3. **Confinement.** The `path.*` cases of the input gates (`..`, absolute, a link in the directory, a link as the file) for `--out`, and `--out` without `--root`.
4. **The ceiling.** `scripts/manifest.py --check` derives exactly the old set plus `dir_write` and `file_write`; a mutant ceiling without them is refused.
5. **The bound.** An answer over `--max-state-bytes` is `limit.state-too-large` with `holder` `output`, to the byte; nothing is written.
6. **Concurrency.** Two runs with the same `--out` and no `--overwrite`: exactly one wins, the other is `out.exists` (the kernel refuses a taken name).
7. **Mutants** for each rule above, and no input reaches a panic.

## 9. What is not decided, and is a person's

1. **Whether the tool's ceiling widens at all** (section 2). It is the reason this is a design before code. If the answer is no, `--out` does not belong in `table`, and #101's pipeline step stays a shell redirect.
2. **The receipt** on standard output (section 4): one JSON line with the path, the bytes and the rows, or silence.
3. **The memory bound** (section 5): counted in `--max-state-bytes` (proposed), or a number of its own, or streaming (needs the contract package).
4. **`--overwrite` as a flag**, or no way to replace at all (the caller removes the file first, which the tool cannot do).
5. **Whether `--out` is for the MCP server** (proposed: no).

**Not measured:** the cost of holding the answer (no code yet), the size of answers people ask for as csv, whether agents prefer the receipt.

## 10. Decided, 2026-11-09

The maintainer answered "yes" to the ceiling and took the defaults for the four smaller choices: (1) `table` may write one named file beneath `--root` (`dir_write`, `file_write` added to the ceiling in `tools.toml`); (2) the receipt on standard output; (3) the held answer counted in `--max-state-bytes`; (4) `--overwrite` as a flag; (5) `--out` and `--overwrite` withheld from the MCP server. Issue #111 was decided the same day (option 2, `docs/backlog.md` item 14): `--out` is the glue between `table` and a separate cleaning tool.



## 11. Built, and where the build differs from the design

Written when the code landed (cancho-table#108). What was measured is said as measured; the rest is read from the code.

**The sink was not needed.** Section 6 proposed a `Sink` threaded through about 40 signatures. The build holds the answer where it already is: the csv and JSON-lines readers collect rows in a buffer that `engine.flush` writes every 64 KiB, and in *hold* mode `flush` and `flush_last` leave the bytes in that buffer, which grows to the whole answer and is handed back to `plan_body`. The mode is one slot of the tally (`engine.k_hold`, slot 29, which was unused: the slots are the layout of a worker's result, `par.header_bytes`, so none can be added), set by each reader from the plan (`Query.hold`). The change is ten call sites of `flush` gaining the tally they already had, one field of `Query`, four tally set-ups and the function `outfile.cho`, and `table.cho`'s `plan_body`; no function that takes `io` changed its parameters. A JSON or text answer is already a document in a buffer (`--max-bytes` bounds it), so it is written from there.

**One core.** A parallel read hands the main thread partitions of the answer, and `psort.finish` writes them as it merges; neither was changed. A call with `--out` therefore reads on one core, as a join does (`--threads` does not apply). Measured on this container (4 CPUs, a 67 MB file of 3,000,000 rows, `--select id,name --where 'v:int > 500000' --format csv`, a 23 MB answer, best of 4): 0.317 s to standard output with the default threads, 0.342 s with `--threads 1`, 0.545 s with `--out` (the held buffer growing, the SHA-256 of the content for the temporary's name, the write and the rename). The 1.6 times is what the file costs and not a lost parallelism.

**The bound.** The held csv or JSON-lines answer is counted against `--max-state-bytes` **on its own**, not summed with the groups or the rows to sort that the call also holds: a call can hold up to twice the number (a grouped answer is made after its groups are, and is usually much smaller than they). The refusal is exact in both ways: an answer of exactly `--max-state-bytes` bytes is written, one byte more is `limit.state-too-large` with `detail.holder` `output` and nothing written (`test_out.Bound`); inside a read it is checked each time the buffer passes a 64 KiB block, so a refused call does not read to the end: peak resident set 8 MB or less on a 18.6 MB file with `--max-state-bytes 1000000` (`test_out.Memory`). `detail.path` is the input's (as in the other state refusals); the file's path is in the call.

**No lock, a unique temporary.** `toolbox.atomic` makes the temporary's name from the destination and the content's hash and, finding it there, takes it for a stale one and removes it, because its caller holds the sidecar lock. `--out` takes no lock (the sidecar `NAME.lexsys-lock` would be a file left beside every answer, and a replacement has no precondition to keep between a look and a rename). So two writers of one name and one content must not share a temporary: the name is derived from the content's hash and, where there is a `/proc` (Linux), the process number (`outfile.temp_key`). Measured before the number was added: in each of 8 trials of 3 simultaneous runs of one call at least one run exited 1 (12 of the 24 runs: the loser removed the winner's temporary, and the rename failed with ENOENT); with it, `test_out.Race` (6 trials of 3 runs) gives exactly one 0 and two `out.exists`. **Where there is no `/proc` (macOS) the number is not added and that race is not excluded: not measured on such a system.** A kill leaves at most the writer's temporary `.NAME.HASH.lexsys-tmp`, never the destination.

**The refusals.** `out.exists` (exit 2, `repair` `choose`: the call with `--overwrite`, and the call with the first free `NAME-1.EXT` to `NAME-9.EXT`, none when all nine are taken), `out.write-failed` (exit 1, `repair` `none`, `detail.errno` and `detail.step`), `args.required-flag` for `--out` without `--root` and `--overwrite` without `--out`, `args.conflict` for `--out` with `--explain` or `--discover`. The `path.*` and `io.*` rules of `--out` are the rules of the input path and carry the path in `detail.path`, not `detail.flag` (section 3 said they would). The name is looked at **before** the read, so a call that cannot succeed does no work, and again by the kernel at the rename. A directory where the file goes, with `--overwrite`, is `out.write-failed` at the step `rename` (errno 21), whoever runs it; that is how the rule is exercised in `test_rules`.

**The receipt** is `{"out":{"path":…,"bytes":N,"rows":N}}` in the `data` of a `table.v2` document, for every `--format`; `rows` is the number of rows or groups of the answer (`Counts.emitted`, which `test_out.Bytes` checks against the `row_count` of the json answer).

**The MCP server.** `--out` and `--overwrite` are not properties of the tool there (`WITHHELD_FLAGS`), a call that names them is refused as an unknown property, and the tool's annotations stay `readOnlyHint: true`, because a call through the server writes nothing (`mcp.py` takes the write labels off when every `path-write` flag is withheld).

**The ceiling** gained `dir_write` and `file_write` and nothing else (`tools.toml`, `manifests/table.authority.json`; the four tests that listed the old row say the new one).

**Not measured, and not tested:** the steps `write` and `sync` of a failed write (they need a full file system; the step `create-temporary` is reached by a read-only directory and `rename` by a directory), the directory sync after the rename, a kill during the write (the window is the length of one `write`), a network or exotic file system (`atomic.create` answers `EOPNOTSUPP` where the rename cannot refuse a taken name atomically; the tool then says `out.write-failed`, `errno` 95, and not that the name is taken), and any system but Linux.
