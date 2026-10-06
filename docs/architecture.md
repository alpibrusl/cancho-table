# Architecture and the capability model

How `table` is put together, and what its authority row says. Short on purpose: the designs are in [select.md](select.md), [filter.md](filter.md) and [parallel.md](parallel.md).

## The pieces

```
   argv                                                       standard output
    |                                                                ^
    v                                                                |
 [ contract package ]  cli: flags         out: output ------------> [ writer.ls ]
   (lexsys-tools,      describe: introspect, skill                   csv and json fields
    pinned in          fail, limit: rules and limits                      ^
    lex-sys.toml)      path, place: confinement under --root              |
    |                  lines: the 64 KiB line reader                      |
    v                                                                     |
 [ table.ls ]  flags -> [ query.ls / plan.ls / expr.ls ] -> the plan      |
    |                    one Query, whichever flags filled it             |
    |   header read, then the plan resolved against it ([ frame.ls ])     |
    v                                                                     |
 [ reader.ls ]  RFC 4180: records, fields   --->   [ engine.ls ] ---------+
    ^                                              per row: ragged? --where? emit or add
    |                                                   |
    +-- sequential read (table.ls)                      v
    +-- parallel read (par.ls, scan.ls):          [ agg.ls ]  groups, sums, limits
        ranges of the file, one thread each,
        the parent takes them in file order
```

| piece | what it does |
|---|---|
| contract package (`lexsys-tools`, modules `cli`, `describe`, `fail`, `limit`, `lines`, `out`, `path`, `place`, `text`) | the parts every tool of the family shares: the flag table, `introspect` and `skill`, the `table.v2` envelope, rules and repairs, limits, the line reader, paths confined beneath `--root`. Pinned by commit in `lex-sys.toml`, installed by `lex-sys build` |
| `tools/table/table.ls` | the flags, the sequential read loop, the answers |
| `query.ls`, `plan.ls`, `expr.ls`, `frame.ls` | the plan: `--select`, `--where`, `--group` and `--agg` fill one `Query`; it is resolved against the header before any row is read, so a wrong column is refused at the cost of one line |
| `reader.ls` | the RFC 4180 reader of its own: quotes, doubled quotes, newlines inside quotes, a byte order mark |
| `engine.ls` | what is done with one row: counted, ragged (never used), kept by `--where`, then written or added to its group. Both reads call the same functions |
| `agg.ls` | groups and their sums, in bounded memory; the output order is the key order or `--sort` |
| `scan.ls`, `par.ls` | `--threads`: ranges of the file read by threads, each assuming it starts at a record; the parent takes them in file order and reads again any it cannot trust, so the answer is the one-core answer |
| `sorter.ls` | `--order-by`: the rows held, their keys and an index sorted by a stable merge; with `--top` or a page it keeps only the best rows, and past `--max-sort-rows` it refuses. Its state is the grouping's value, used for rows; it reads on one core |
| `writer.ls` | the fields as CSV (minimal quoting) or JSON |
| `generated/table/built.ls` | the authority, the schema and the compiler pin, embedded in the binary; written by `scripts/manifest.py` |

## The capability model

lex-sys programs get their capabilities (files, directories, standard streams, threads, memory) as values, and a program that does not hold one cannot use it. The compiler can derive from the source what a program can reach: `lex-sys authority`, described in lex-sys's `docs/authority.md`. The result is a list of labels, whether the program is `bounded`, what it is `unbounded_by`, and any foreign symbols. `table` embeds its own, and `table introspect` prints it.

`table`'s row (`manifests/table.authority.json`):

| label | why it is there |
|---|---|
| `args` | the flags and the file name |
| `file_read` | reading the input file |
| `fs_read("")` | the path is only known at run time, so the row cannot name it; `table introspect` lists this under `not_narrowable` |
| `dir_read` | directory access used to open the path (`--root` confinement) |
| `heap` | memory for the record, the page and the groups |
| `io_write` | standard output |
| `err_write` | standard error |
| `conc` | `--threads`: spawn and join. The threads hold nothing the process did not already have: a file opened by the same path and a heap forked from the parent's |

Not in the row, and so not possible for this program: `net_out`, `net_in`, `ffi` (foreign code), `clock`, writing a file or directory (`file_write`, `dir_write`), reading standard input (`io_read`). Not that the program has no bugs: the row says what it may reach, not that it is correct. The list of what a row cannot say is also in `introspect`: the extent of a path, what a symbolic link reaches, what standard output carries, and resource use beyond the tool's caps.

### What CI checks

* `tools.toml` is the ceiling a person writes and reviews: the labels `table` is allowed. No tool may hold `ffi`, `net_out`, `net_in` or `clock`, and `bounded` must be true.
* `python3 scripts/manifest.py --check` derives the authority over the sources and the installed contract package and requires that it is within the ceiling, equal to the committed manifest, and equal to the one embedded in the binary (and to what the binary prints).
* `python3 scripts/manifest.py --check --against ../lexsys-tools/contract` requires that the installed package says the same thing as the sources it was published from.
* On Linux, the conformance tests run the binary under `strace` and require no socket and no write on disk.
* `python3 scripts/site.py --check` requires that the authority row on the project page is the committed one.

A new capability is therefore a change to `tools.toml` that somebody has to approve.
