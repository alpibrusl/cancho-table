# `table --where`, `--group`, `--agg`, `--sort`, `--top`: filter and group-count

Design, the choices made, where it deviates from section 5 of cancho-tools'
`docs/next-tools.md`, and what each claim rests on. `docs/select.md` covers the
reader, `--select` and paging, which all of this composes with.

## One plan

The flags do not each do something; they fill one `Query` (`tools/table/query.cho`):
the column references (select's, then where's, then group's, then the aggregates'),
the conditions with their literals, and the aggregates. After the header is read,
every column the plan names is resolved together (`plan.resolve`: one name, one
column, a repeated header name is `column.ambiguous`), and the read is a loop over
rows that asks the plan three things in order: is it ragged (counted, never
used), does `--where` want it, and then either emit it or add it to its group.
A later `--query` string would compile to the same `Query`; none is built, and
there is nothing in the engine that knows which front end made the plan. The design
asked for a gate that a typed form and a `--query` form give the same bytes; with one
front end there is nothing yet to compare, and the gate is not written.

Modes: no `--select`, `--where` or `--group`: the shape. `--select` and/or `--where`:
rows (a `--where` alone returns every column). `--group` and/or `--agg`: groups
(`--agg` alone is one group of all the rows). `--select` with `--group` or `--agg` is
`args.conflict`: in a grouping the columns are the groups and the aggregates.

## The `--where` grammar

```
EXPR   := COND { "and" COND }
COND   := COLUMN OP VALUE | COLUMN "contains" VALUE | COLUMN "in" "(" VALUE { "," VALUE } ")"
OP     := "=" | "!=" | "<" | "<=" | ">" | ">="
COLUMN := WORD [":int" | ":dec(" N ")"]     a header name, or #N the Nth column; N is a scale, 0 to 18 (1-based)
VALUE  := WORD
WORD   := bare | 'quoted'
```

A bare word runs to whitespace or one of `' ( ) , = ! < >`; a backslash makes the next
byte part of it, whatever it is. A quoted word is between single quotes with `''` for a
quote. `and`, `in` and `contains` are lower case and are keywords only where the
grammar expects them (a column may be called `and`: `and = x` is a condition). No
`or`, no grouping parentheses, no expressions, no functions, as the design says.

**Names and positions.** The same rules as `--select`, in the notation of this
grammar: a bare word `#3` is the third column, a header that really is `#3` is `'#3'`
or `\#3`, `#id` needs nothing. **A deviation from `--select`**: `--select` writes a
comma in a name as `\,`; here a comma ends a bare word, so a name with a comma is
quoted (`'Last, First'`) or escaped the same way (`Last\,\ First`). Quoting was added
because names with spaces and operators are common in files an agent meets, and a
list where `\ ` is the only way to write a space is a trap. A quoted word is never a
position and never a keyword.

**Comparison.** By default **text, bytewise**: the cell's bytes (a quoted field with
its doubled quotes undone) against the literal's, as unsigned bytes, a shorter value
first when one is a prefix of the other; no locale, no case folding. So `"10" < "9"`,
`'Z' < 'a'`, and a non-ASCII value sorts by its UTF-8 bytes. `contains` is a byte
substring (an empty literal is contained in everything); `in` is equality with any of
the literals.

**Numeric comparison is opt-in per column**: `bytes:int >= 50000`. The cell must be an
exact integer: an optional `+` or `-`, then at least one digit, nothing else, within
64 bits. The literals of an `:int` condition must be integers too (an error of the
expression at the literal's offset, not of a row). A cell that is not one (a space, a
decimal point, an exponent, a letter, a sign alone, a digit that is not ASCII), or one
that does not fit, is **never coerced and never a float**: the whole query is refused
with `value.not-integer` or `value.integer-overflow`, naming the **row** (1-based among
data records), the **line**, the **column** and the cell (its first 64 bytes). A
deviation from section 5: it says "a column of decimals is read as an exact scaled
integer or as a string" and leaves open how decimals are declared; **they are declared
`:dec(S)`**, below, and an `:int` column that meets `1.5` is still a refusal (its hint says to
declare the column `:dec(1)`).

**Exact decimals are opt-in per column too**: `price:dec(2) >= 12.50` (docs/numbers.md, stage N1).
`S`, 0 to 18, is the number of fractional digits the column may hold, and is written, never inferred.
The cell is `[+-]? digits? [. digits?]` with at least one digit, ASCII, nothing else (no exponent, no
space, no thousands separator; `.5` and `5.` are fine), as an exact integer of 10^-S: `3` is 300 and `1.5`
is 150 at scale 2. **It is never rounded**: a cell with more than `S` fractional digits is refused
(`value.decimal-scale`, with the repair that declares the larger scale), one that is 18 or more
significant digits once scaled is `value.decimal-too-wide`, anything else `value.not-decimal` (an empty
cell is not one); each names the row, the line, the column and the cell, as `:int` does, and the checks run
in that order (the whole cell against the grammar, then the scale, then the width). The literals of the
condition are read the same way at the same scale, so `12.505` against `:dec(2)` is an error of the
expression at the literal, not of a row. **Equal as numbers**: `1.5` and `1.50` are one value
(`price:dec(2) = 1.5` matches both), while the same column without a suffix is text and they differ.
`contains` on a typed column is a syntax error. **One numeric type per column in one plan**:
`x:dec(2)` beside `x:int` or `x:dec(3)`, or beside `sum:x`, is `column.type-conflict` (exit 2) before a
row is read; an unmarked mention is text and mixes freely. Not built yet (the stages after N1): typed
aggregates, group keys and sort keys of a decimal, `:float`, the type report.

**Empty cells.** In a text comparison an empty cell is the empty string (`name = ''`
works). In an `:int` condition an empty cell **is not an integer: it is refused**, not
skipped and not false. This is deliberate: a query that quietly ignored the rows with
no value would give an answer about fewer rows than the file has. The way to say "only
the rows that have one" is to say it: **conditions are evaluated left to right and stop
at the first that is false**, so `bytes != '' and bytes:int > 5` never reads an empty
cell as an integer. (The same column may be tested as text and as an integer in one
expression.)

**Errors in the expression** are `where.syntax` (exit 2) with `detail.offset` (a byte
offset into the expression), `detail.expected` (what would have been right there, in
words) and `detail.expression`: an empty expression, a missing operator or value, a
`!` without `=`, `==`, `<>`, a missing `and`, `in` without `(` or with `()`, an unclosed
quote, a trailing backslash, an `:int` literal that is not an integer or does not fit,
`contains` on an `:int` column, more than 64 conditions or 1024 values. The tests pin
the offset of each.

## Groups

`--group NAMES` (a list like `--select`'s) and `--agg LIST`, items `count`, `sum:COL`,
`min:COL`, `max:COL`, `distinct:COL` separated by commas (default `count`). `COL` is a
name or `#N`; a name with a comma is written `\,` as in a list. `sum`, `min` and `max`
read their cell as an exact integer (same refusals, with `context` naming the
function). **A sum is exact whatever its width: it is a pair of integers** (the low 32 bits of every cell in
one, the rest in the other), so it cannot wrap or refuse, and one past 64 bits is printed in full (up to 28
digits; `--max-rows`' ceiling of 10^9 keeps the pair below 2^93). The first versions refused it
(`agg.sum-overflow`); the rule is gone, `docs/numbers.md` stage N0p;
`distinct` counts distinct byte strings, empty included. **Typed items (docs/numbers.md, stage N2)**: an item
may end in `:int` or `:dec(S)` (`sum:price:dec(2)`, `min:price:dec(2)`, `distinct:price:dec(2)`), which reads the
cells as exact decimals of at most `S` fractional digits and writes a sum, minimum or maximum at exactly `S`;
`distinct:price:dec(2)` counts by value (`1.5` and `1.50` are one). `mean:COL[:dec(S)][@N]` is the exact sum over the
group's count, rounded half to even at `N` fractional digits (the column's own scale when it is `:dec(S)`; an
integer column has none, so `mean:n@2` must say). The refusals are `--where`'s (`value.not-decimal`,
`value.decimal-scale`, `value.decimal-too-wide`) with `context` naming the function, `scale` and `digits`.
A suffix is a suffix only when a column name is left before it (`sum:int` sums a column called `int`);
a column really called `x:dec(2)`, `x:int` or `x@2` is written `sum:x\:dec(2)`, `sum:x\:int`, `mean:x\@2@3`.
`describe` is not built.

**Output** is `table.v2` rows like a selection: `columns` (the group columns, then
`count`, `sum:NAME`, ... spelled with the header's own name), `rows` of strings (numbers
as decimal strings, like every other field), `row_count`, `truncated`, `next` and
`group_count` (all the groups, before `--top` and the page). As CSV the same, header
first. A grouping of no rows is no groups, including the grouping of everything.

**Order is defined and byte-stable.** `std.map` iterates in insertion order, which is
the order of first appearance in the file; that order is **not** the output order, so
that the answer does not depend on how the rows are arranged. The default is the group
keys compared **field by field, each bytewise, a shorter value first**; `--sort KEY`
(`KEY` an output column, `-` before it for descending, ties broken by the same key
order, the sort stable) orders by that column instead: an aggregate numerically, a
group column bytewise. One key only. `--top N` keeps the first N groups of that order;
`--limit` and `--from` then page them, with `next`. The test shuffles the rows and
requires the same bytes.

**Bounded, three ways, each with its own rule**: `--max-groups` (default 100,000,
ceiling 1,000,000: `limit.too-many-groups`, the name `tally` calls `--max-keys`),
`--max-distinct` (distinct (group, value) pairs over all groups, default 100,000:
`limit.too-many-distinct`) and `--max-state-bytes` (the bytes of every group key and
distinct value kept, default 64 MiB: `limit.state-too-large`), because a count of keys
bounds memory only if the keys are short. A grouping that exceeds a bound is refused;
there is no partial grouping, which would be a wrong count presented as a count. As CSV
the groups are written after the whole file has been read, so a refusal writes none.

## What the plan costs

A filter or a grouping keeps one record at a time (not ragged ones), so memory is the
reader's plus the groups. Peak RSS on the benchmark file is 1.6 to 2.0 MB for a filter
and for a grouping by `status` (`test_memory.py` requires it flat from 2 MB to 37 MB),
and 17 MB when 100,000 groups are kept (`--group id`, stopped by `--max-groups`).

## Claims and where they are checked

| claim | check |
|---|---|
| filtered rows and groups are what a reference implementation of the stated semantics computes, in the same order | `test_plan.py` + `refimpl.py`: 1,800 generated tables (quoted fields, embedded delimiters and newlines, ragged rows, empties, integers at the edges of 64 bits) and generated plans (up to three conditions, `in`, `contains`, `:int`, select, group, aggregates, sort, top), rendered with quoting and escapes, as json and as csv |
| a cell that has to be an integer and is not is refused naming the same row and column as the reference | the same, 309 of the 1,800 are refusals |
| the grammar's errors are at the stated offsets | `test_filter.py` (24 expressions, offset and wording) |
| integers are exact, sums are exact and never wrap or refuse | `test_filter.py`: the edges of 64 bits, signs, 14 non-integers, sums past 64 bits against Python's `int` |
| group order does not depend on the rows' order | `test_group_order_is_the_same_whatever_the_order_of_rows` |
| paging a filter or a grouping equals slicing | `test_filter_pages`, `test_group_pages_and_csv` |
| every rule has a fixture, exit code and summary | `test_rules.py` |
| memory is flat and the group bounds hold | `test_memory.py` |
| no input reaches a trap | 300 fuzzed inputs through 6 plans |
| the logic matters | `scripts/filter_mutants.py`: 37 defects (an integer past 64 bits, 2^63 as positive, a sum at the edge, `<=` as `<`, no short-circuit, a keyword that is quoted, the 64-condition and 1024-value ceilings, min as max, a distinct counted twice, the bounds off by one, sorting descending by key, a page one row off, a `next` one past, `--top` ignored, the row of a refusal one low, the flag a refusal names ...), every one killed; a 38th, an unstable merge, is equivalent -- the order is total, because ties are broken by distinct keys -- and was removed |
| speed | `scripts/bench.py`, `docs/history.md` |
