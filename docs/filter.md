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
A `--query` string compiles to the same `Query` (it is translated to these flags, `docs/query.md`), and
there is nothing in the engine that knows which front end made the plan. The gate that a typed form and a
`--query` form give the same bytes is written and run (`tests/conformance/test_query.py`, `scripts/query_corpus.py`).

Modes: no `--select`, `--where` or `--group`: the shape. `--select` and/or `--where`:
rows (a `--where` alone returns every column). `--group` and/or `--agg`: groups
(`--agg` alone is one group of all the rows). `--select` with `--group` or `--agg` is
`args.conflict`: in a grouping the columns are the groups and the aggregates.

## The `--where` grammar

```
EXPR   := ALL | ANY | UNIT
ALL    := UNIT "and" UNIT { "and" UNIT }
ANY    := UNIT "or" UNIT { "or" UNIT }
UNIT   := COND | "(" EXPR ")" | "not" "(" EXPR ")"
COND   := COLUMN OP VALUE | COLUMN "contains" VALUE | COLUMN "in" "(" VALUE { "," VALUE } ")"
        | COLUMN "not" "contains" VALUE | COLUMN "not" "in" "(" VALUE { "," VALUE } ")"
OP     := "=" | "!=" | "<" | "<=" | ">" | ">="
COLUMN := WORD [":int" | ":dec(" N ")" | ":float" | ":date" | ":timestamp" | ":datetime"]     a header name, or #N the Nth column (1-based); N is a scale, 0 to 18
VALUE  := WORD
WORD   := bare | 'quoted'
```

A bare word runs to whitespace or one of `' ( ) , = ! < >`; a backslash makes the next
byte part of it, whatever it is. A quoted word is between single quotes with `''` for a
quote. `and`, `or`, `not`, `in` and `contains` are lower case and are keywords only where the
grammar expects them: after a complete unit `and` and `or` are the connectives, at the start
of a unit they are a column (a column may be called `and` or `or`: `or = x` is a condition,
`a = 1 or or = 2` has both), and a quoted `'or'` is never a keyword. `not` is a keyword in three
places only: before a `(` at the start of a unit (`not (a = 1)`), and between a column and `in` or
`contains` (`a not in (1, 2)`, `a not contains x`). A column called `not` is a column everywhere else
(`not = 1`, `not in (1)`, `not contains x`, `not not in (1)`, `a = 1 and not != 2`). No expressions, no
functions.

**`not`** (slice A2 of [`or-and-columns.md`](or-and-columns.md)). `not (EXPR)` is the opposite of
what is in the parentheses, `COLUMN not in (...)` the opposite of `in`, `COLUMN not contains VALUE`
the opposite of `contains` (there was no way to say "does not contain"; a typed column is `not in` as it is `in`,
and `contains` on one is refused as it is, `not contains` too). A `not` changes the answer of a unit
and nothing else: **which conditions are tried, and so which typed cells are read, is the same with or
without it** (`not (c:int > 5)` and `c:int <= 5` refuse the same cells, and `not (a = x or c:int > 5)`
reads `c` for the rows where `a` is not `x`, as `a = x or c:int > 5` does). In the plan it is a swap of the
two exits of the condition or of the group (`--explain` shows `if_true` and `if_false` of `a not in (1)` as
`reject` and `accept`), so a plan with a `not` has a branch program even when it has no `or`. A `not` in front of a
bare condition is not offered (`not a = 1` would need a second look ahead to tell it from a column called `not`): it is
`where.syntax` at the `a`, "( after not", and the repair is the same command with each such condition
rewritten as its exact opposite (`a != 1`; `not a < 1` is `a >= 1`, `not a in (1, 2)` is `a not in (1, 2)`,
`not a contains x` is `a not contains x`), offered only if the grammar then takes the whole expression.
After a column, `not` must be followed by `in` or `contains`: anything else is "in or contains after not". A
`not (` is one of the 8 levels of parentheses.

**`or` and parentheses** (slice A1 of [`or-and-columns.md`](or-and-columns.md)). A level of an
expression is all `and` or all `or`: `a = 1 and b = 2 or c = 3` is **not read either way**, it is
`where.mixed-and-or` (exit 2), with `detail.offset` at the connective that changes the level, and a
`choose` repair that offers the two readings as whole commands: by precedence, `and` before `or`, as SQL
does (`a = 1 or b = 2 and c = 3` is `a = 1 or (b = 2 and c = 3)`), and left to right
(`(a = 1 or b = 2) and c = 3`; for `a or b and c or d` they are `a or (b and c) or d` and `((a or b) and c) or d`). When the two
readings are the same text (`a = 1 and b = 2 or c = 3`) one is offered. A refusal can be
turned into an answer later without breaking a call; an answer cannot be taken back. A reading is offered
only if this grammar takes it whole (it can pass the 8 levels of parentheses). Today's expressions,
`COND { "and" COND }`, are `ALL`, so **every call that was valid is valid and means the same**.
At most **8 levels of parentheses** (a ninth is `where.syntax`, `detail.expected` "fewer nested
parentheses: the ceiling is 8"), 64 conditions wherever they are, and 1,024 values in one `in`.
A missing `)` is `where.syntax` ("and, or, or the ) that closes the group"), `()` and an `or` with
nothing after it are "a column", a `)` with no group to close is "and, or the end of the expression".

**Evaluation.** The conditions are tried **in the order they are written**; a condition is tried
only when the run reaches it, and **a typed cell is read as its type only when its condition is tried**.
A level stops at the first unit that decides it: the first false unit of an `and`, the first true unit of an
`or`. The plan is a conjunction when it has no `or` and no `not` (and then it is the loop it always was), and
otherwise a branch program (`--explain` shows it: `if_true` and `if_false` on each condition, the index of
the condition run next, or `accept` or `reject`). The consequences, as the tests have them
(`c` has empty cells, `a` is `x` on row 1):

| expression | a row where | result |
|---|---|---|
| `c != '' and c:int > 5` | `c` is empty | dropped: the guard stops the evaluation (as always) |
| `c = '' or c:int > 5` | `c` is empty | **kept**: the second condition is not tried. The guard of an `or` is the positive test |
| `c:int > 5 or c = ''` | `c` is empty | **refused**, `value.not-integer`: the typed condition is tried first |
| `a = 'x' or c:int > 5` | `a` is `x`, `c` is `abc` | kept: `c` is never read |
| `a = 'y' or c:int > 5` | `a` is `x`, `c` is `abc` | refused at `c`, naming the row and the column |
| `(a = 'x' or b = 'x') and c:int > 5` | `a` is `x` | `b` is never read; `c` is |
| `not (c:int > 5)` | `c` is empty | **refused**, as `c:int <= 5` is: the read is made before the comparison |
| `not (a = 'x') and c:int > 5` | `a` is `x`, `c` is `abc` | dropped: the `not` is false, `c` is never read |
| `not (c = '' or c:int > 5)` | `c` is empty | dropped: the guard of the `or` decides, `c:int` is not tried |

A cell that no path reaches is not an error, as the cell to the right of a false `and` never was; the
rule is a function of the row and the expression alone, so the same row is refused or kept in the same way
at every `--threads`, and a range read again by the parent gives the same answer. `a:int in (1, 2)` reads
the cell once where `a:int = 1 or a:int = 2` reads it up to twice, and answers the same rows with the same
refusals: `--explain` notes a run of single-value conditions on one column that could be one `in`.

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
row is read; an unmarked mention is text and mixes freely. The stages after N1 are built: typed
aggregates, group keys and sort keys of a decimal, `:float` and the type report (below, and `docs/numbers.md`).

**Dates and timestamps (slice T1 of [`datetime.md`](datetime.md)).** `COLUMN:date`, `COLUMN:timestamp` and `COLUMN:datetime` read the cell as a day (`2026-01-02`), as an instant
(`2026-01-02T01:00:00+02:00`: an offset, `Z`, `z`, `+hh:mm` or `-hh:mm`, is required) or as a wall-clock time (`2026-01-02 12:00:00`: an offset is refused). The grammar is a strict subset of
RFC 3339 and nothing is guessed: years 0001 to 9999, a real calendar (`2026-02-30` and `1900-02-29` are refused), `T`, `t` or one space between the date and the time, hours 00 to 23 (`24:00:00` is
refused), seconds 00 to 59 (a leap second `:60` is refused), a fraction of 1 to 6 digits (more is refused even when the extra digits are zeros: the unit is the microsecond, never rounded or cut), no
whitespace, no basic, week or ordinal form, no zone names and no `-00:00`. A timestamp is the instant, so `2026-01-02T01:00:00+02:00`, `2026-01-01T23:00:00Z` and `2026-01-01 23:00:00.000+00:00` are one
value: they are equal, they are one group, one `distinct` and one place in a sort. A literal is read by the same reader as the cells (`ts:timestamp >= '2026-01-02T00:00:00Z'`); one that is not a value of the
type is `where.syntax` at the literal, and a date against a timestamp is refused too (the day's boundary is the user's statement), with the repair that writes the day out as the instants that bound it.
`contains` is refused on all three. Comparisons are of the number (days, or microseconds), the order is the order of the instants, and ties keep the order of the file. **One type per column per plan**
as for the numbers: `:date` beside `:timestamp` is `column.type-conflict`. The refusals have a tag each (`value.not-date`, `value.not-timestamp`, `value.timestamp-precision`, `value.timestamp-range`,
`value.timezone-missing`, `value.timestamp-offset-present`, `value.timezone-unsupported`), name the row, the line, the column and the cell, and say in `detail.reason` and `detail.offset` what is wrong
and where; the repair is the same invocation with the type the cell shows, when it can be written whole (`docs/refusals.md`). **Output:** a row is the input bytes, unchanged; a group key, a `min` and
a `max` are written in the normal form, which is a function of the value (`2026-01-02`, `2026-01-01T23:00:00Z` in UTC, `2026-01-02T12:00:00.250` for a datetime, a fraction of none, three or six digits),
a JSON string in `--format jsonl`. `sum` and `mean` of them are `agg.bad-spec`.

**A period of a group key (slice T2 of [`datetime.md`](datetime.md)).** A `--group` item that ends in `:date`, `:timestamp` or `:datetime` may end in `@UNIT`: `--group ts:timestamp@day`, `--group d:date@month`,
`--group w:datetime@hour`. The units are `hour`, `day`, `month` and `year` for a `:timestamp` or a `:datetime`, and `month` and `year` for a `:date`, in lower case. The key of a row is **the first value of
the period its value falls in, a value of the same type**, written as any other value of the type: `2026-01-01T00:00:00Z` for the day of `2026-01-02T01:00:00+02:00`, `2026-01-01` for the month or the year of
`2026-01-15` (not `2026-01` or `2026`), `2026-01-02T00:00:00` for the day of a datetime. So a key sorts, groups, and feeds `--where` back (`ts:timestamp >= '2026-01-01T00:00:00Z'`) as a value does. **The period
of a `:timestamp` is the UTC one of its instant**, whatever offset the cell is written in (`2026-01-02T01:00:00+02:00` is in the day of `2026-01-01`); a `:datetime` and a `:date` are bucketed by what is
written. Before 1970 the period is the one that contains the value (the day of `1969-12-31T23:59:59.999999Z` is `1969-12-31`). The type of the column is the same with or without the unit (`--where
ts:timestamp >= ... --group ts:timestamp@day --agg min:ts:timestamp` is one column read as one type), two items on one column are two keys (`ts:timestamp@month,ts:timestamp@day`, or `ts:timestamp@day,ts`), and
the cell of a bucketed key is refused exactly as the cell of the plain key (`value.not-timestamp` and the rest, `context` `group`). A unit that is not one the type has is `args.bad-value` with `detail.item`,
`detail.unit` and `detail.reason` (`week-not-built`, `finer-than-type`, `unknown-unit`) before a row is read: `@week` is not built, `@hour` and `@day` of a `:date` are finer than its cells, and `@minute`, `@second`,
`@DAY` and a second `@` are not units. A unit in `--agg` (`min:ts:timestamp@day`) is `agg.bad-spec`: a period belongs to a key. `--order-by` and `--where` read no unit, and `--query` has no form of it (a typed
`group by` there is `query.unsupported`, whose hint names `--group ts:timestamp@day`). An `@` after any other name is part of the name: `ts@day`, `n:int@day` and `a@b` are columns called that, and a column whose
name ends in `:timestamp@day` is written with its colon escaped, `--group 'x\:timestamp@day'`, as one that ends in `:int` is.

**Empty cells.** In a text comparison an empty cell is the empty string (`name = ''`
works). In an `:int` condition an empty cell **is not an integer: it is refused**, not
skipped and not false. This is deliberate: a query that quietly ignored the rows with
no value would give an answer about fewer rows than the file has. The way to say "only
the rows that have one" is to say it: **conditions are evaluated left to right and stop
at the first that decides the level they are in** (the first that is false in an `and`, the first that is true in an
`or`), so `bytes != '' and bytes:int > 5` never reads an empty cell as an integer, and
`bytes = '' or bytes:int > 5` keeps the rows that have none. (The same column may be tested as text and as an integer in one
expression.)

**Errors in the expression** are `where.syntax` (exit 2) with `detail.offset` (a byte
offset into the expression), `detail.expected` (what would have been right there, in
words) and `detail.expression`: an empty expression, a missing operator or value, a
`!` without `=`, `==`, `<>`, a missing `and`, `in` without `(` or with `()`, an unclosed
quote, a trailing backslash, an `:int` literal that is not an integer or does not fit,
`contains` or `not contains` on an `:int` column, `not` before a condition or after a column where it does not belong, more than 64 conditions, 1024 values or 8 levels of parentheses. The tests pin
the offset of each. `and` mixed with `or` at one level is `where.mixed-and-or`, above.

## Groups

`--group NAMES` (a list like `--select`'s, of any number of columns: the grouping has no limit of its own on them, and a column may be named more than once; the bounds are the group, state and line limits below) and `--agg LIST`, items `count`, `sum:COL`,
`min:COL`, `max:COL`, `mean:COL[@N]`, `distinct:COL` separated by commas (default `count`; `min`, `max` and `distinct` take `:date`, `:timestamp` and `:datetime` too, above). `COL` is a
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
**`:float`** (stage N3a) reads the cell as the nearest double (`x:float >= 1.5`, `min:x:float`, `max:x:float`, `distinct:x:float`, and from stage N4 the **exact** `sum:x:float` and `mean:x:float`: the exact sum of the doubles rounded once, the same bytes for every row order and `--threads`, 584 bytes of state a group counted in `--max-state-bytes`; a sum beyond the largest double is `agg.float-overflow`, naming the group): no `inf`, no `nan`, no negative zero, a comparison of doubles and not of text, a double written as the shortest
decimal that reads back to it; `value.not-float`, `value.not-finite`, `value.float-range` and `limit.number-too-long` are the refusals, `docs/numbers.md` section 4.
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
