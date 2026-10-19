# Dates and timestamps: `:date`, `:timestamp` and `:datetime`

> **Status: accepted (PR #76, all twelve questions of section 16 answered by their recommendations); slices T0, T1, T2 and T3 are built.** Sections 0 to 17 are the design as it was accepted, written in the future tense of a design: they say what is decided and why, with every statement about the program at `origin/main` `338a119` and every number either **measured** (with the command), **cited** (with its source and access date) or marked *estimate* or *choice*. **Section 18 says what T0 and T1 built, section 19 what T2 built and section 20 what T3 built, each with its narrow readings of a gap in the design and what was measured (with its conditions).** What exists is also described, in the present tense, in [`docs/filter.md`](filter.md), [`docs/refusals.md`](refusals.md) and the README. It follows the rules of [`docs/numbers.md`](numbers.md), [`docs/or-and-columns.md`](or-and-columns.md) and [`docs/joins.md`](joins.md): decisions with their reasons, gates written before the code, and the existing paths held to instructions retired, bound 1.01.
>
> **Nothing in sections 1 to 17 was timed when it was written.** Section 11 estimated the cost of a feature that did not exist from the costs of `:dec(S)`, said so on every figure and fixed the budget the existing paths must keep; section 18.3 has the measured cost beside that estimate.

## 0. The short version

| question | decision (proposed) | why, in one line |
|---|---|---|
| what is added | **three declared readings of a column**: `:date`, `:timestamp` (an instant: an offset is required), `:datetime` (a wall-clock time: an offset is refused) | a reading is declared, never inferred, as `:int`, `:dec(S)` and `:float` are (`docs/numbers.md` lines 14-16) |
| why not one type | a cell with an offset and a cell without one are **different facts**; one type must guess a zone for one of them | the tool's rule is to refuse where it would guess; the guess is the whole difficulty of dates |
| why not a precision parameter, as `:dec(S)`'s scale | **microseconds, fixed**; a cell with more than 6 fractional digits is refused, never rounded | the same refusal as `value.decimal-scale` (`docs/numbers.md` 3.1); int64 holds years 0001..9999 at microseconds and **not** at nanoseconds (measured, section 6.1) |
| grammar | the strict subset of RFC 3339: `YYYY-MM-DD`; `YYYY-MM-DD(T\|t\| )hh:mm:ss[.f{1,6}](Z\|z\|+hh:mm\|-hh:mm)`; extended format only; years 0001..9999; calendar-validated | everything accepted has one meaning and no zone is guessed (section 5) |
| refused, each with its own tag | impossible dates (`2026-02-30`), `24:00`, leap second `:60`, no offset on a `:timestamp`, `-00:00`, named zones, basic format, week and ordinal dates, reduced precision, whitespace, a JSON number | section 10 |
| representation | an **i64**: days since 1970-01-01 for `:date`, microseconds since 1970-01-01T00:00:00Z for `:timestamp` and `:datetime`; days from civil by Hinnant's algorithm, checked here against Python's calendar for all 3,652,059 days of 0001..9999: 0 mismatches | exact, 8 bytes, the key machinery of `:dec(S)` (`agg.cho:228-237`) works unchanged |
| equality and order | **by instant**: `2026-01-02T01:00:00+02:00` equals `2026-01-01T23:00:00Z`; one group, one distinct, one join match | what the declaration means; text gets it wrong today (section 1) |
| `sum`, `mean` | **refused** (`agg.bad-spec`, no new tag) | the sum of instants is not a date |
| output of a key, `min`, `max` | the **normalised form**: `YYYY-MM-DD`; `YYYY-MM-DDThh:mm:ss[.fff[fff]]Z` in UTC; a `:datetime` without the `Z` | independent of the first row seen and of the thread count (the rule of `docs/numbers.md` 3.4) |
| rows (`--select`, `--order-by`) | **the input bytes, unchanged**; only keys and aggregates are normalised | `--select` does not read (`docs/numbers.md` section 5) |
| group by period | **`--group ts:timestamp@day`**, units `hour`, `day`, `month`, `year` (`week`, Monday, was proposed and is not built: section 19, reading 1); the UTC day; the key is the first instant of the bucket, a value of the same type | `@` is the precedent of `mean:price:dec(2)@4`; a bucket is a property of a key, not a second reading, so it must not collide with `column.type-conflict` (section 7) |
| the missing offset | **refused** on `:timestamp` (`value.timezone-missing`), with the repair "read it as `:datetime`"; no `--assume-utc` flag | a flag would change what every unmarked name means (`docs/numbers.md` 5.1 rejected the same shape) |
| zone names | `Europe/Madrid` is refused (`value.timezone-unsupported`); no zone database | a zone database is data that changes; the tool is deterministic |
| first slice | **T1**: the three types in `--where`, `--group`, `--order-by`, `min`, `max`, `count`, `distinct`, the refusals | T2 is the bucket (built, section 19), T3 is JSON lines out, the query, joins, `--report dates` (built, section 20) |
| existing paths | **must not get slower: instructions retired, bound 1.01** (`scripts/gate_regress.py --counter`) | section 11, section 12 |

## 1. What text does today (re-run on current main, 2026-10-16)

**How it was run.** The binary is `cancho-table-j3/build/table` (`meta.version` 0.3.0, compiler `e47d647`, built from `cancho-table-j3` at `f33897c`). `git diff f33897c origin/main -- tools tests schemas` lists one file, `tests/conformance/test_query.py` (no program file): the program text is `origin/main`'s (`338a119`). I could not rebuild here (no `cancho` compiler on the PATH of this session), so **the binary is the one that was already built; it was not rebuilt from the worktree of this branch**. The data is six rows (`t.csv`):

```
id,ts
1,2026-01-01T23:59:59Z
2,2026-01-02T01:00:00+02:00
3,2026-01-02
4,2026-02-30
5,2026-01-01T23:00:00Z
6,2026-01-02T00:00:00Z
```

Row 2 is the instant 2026-01-01T23:00:00Z, **the same instant as row 5**. Row 4 is not a date. Row 3 is a date, not an instant.

| # | command | output (the rows) | what is wrong |
|---|---|---|---|
| 1 | `table --order-by ts t.csv` | ids `5, 1, 3, 6, 2, 4` | by bytes: row 2 (23:00Z) sorts after row 6 (00:00Z of the next day) and after row 1 (23:59:59Z); rows 2 and 5 are one instant and are 4 places apart; row 4 sorts last as if it were a date; row 3 (a date) sits among instants |
| 2 | `table --where "ts >= '2026-01-02'" --select id,ts --format csv t.csv` | ids `2, 3, 4, 6` | row 2 is **kept** (its instant is 2026-01-01T23:00Z, before the day) and row 4 is kept; correct for byte order only |
| 3 | `table --where "ts >= '2026-01-02T00:00:00Z'" ...` | ids `2, 4, 6` | row 2 is still kept; row 3 (`2026-01-02`) is dropped: a date is shorter, so it sorts before its own midnight |
| 4 | `table --group ts t.csv` | six groups, rows 2 and 5 in two groups of one | equal instants are two groups; there is no way to say "by day" (no function in `table`) |
| 5 | `table --where "ts:date >= '2026-01-02'" t.csv` (and `ts:ts`) | `column.unknown`, exit 3, `detail.name` `ts:date` | `:date` is not a type; the whole word is read as a column name (`expr.cho:224-250` takes only `:int`, `:dec(`, `:float` as suffixes) |
| 6 | `table --agg min:ts,max:ts t.csv` | `value.not-integer`, exit 8, row 1, line 2, `2026-01-01T23:59:59Z` | an untyped `min` is `:int` (`query.cho:24-25`); there is no text `min` |
| 7 | `table --agg count,distinct:ts t.csv` | `count` 6, `distinct:ts` 6 | distinct counts bytes: the instant of rows 2 and 5 is counted twice |
| 8 | `table --report types t.csv` (the `ts` row) | `other` 6, `suggest` `none`, `first_other_cell` `2026-01-01T23:59:59Z` | the report does not know dates |
| 9 | `table --query "select cast(ts as date) from 't.csv'"` (and `ts::date`) | `query.syntax`, `detail.expected` `a type: int, dec(N) or float`, offset 18 | the query has `cast` and `::` (`qparse.cho:224-253`, `type_code` at `qparse.cho:197-219`) but three types (`qtext.cho:56`) |
| 10 | `table --where "ts in ('2026-01-02','2026-02-30')" --select id --format csv t.csv` | ids `3, 4` | `2026-02-30` is never refused anywhere |

The mixed-shape files (`m.csv`, five rows) show the other byte-order failures:

```
$ table --order-by ts --select id,ts --format csv m.csv       # ids 1: 2026-01-02T00:00:00Z, 2: 2026-01-02 12:00:00Z (a space),
id,ts                                                         #     3: 2026-01-02T12:00:00Z, 4: 2026-01-03T00:00:00Z, 5: 2026-01-02
5,2026-01-02
2,2026-01-02 12:00:00Z        <- noon, sorted BEFORE midnight (a space is 0x20, T is 0x54)
1,2026-01-02T00:00:00Z
3,2026-01-02T12:00:00Z
4,2026-01-03T00:00:00Z
```

**What does work, and where it ends.** For values of **one uniform shape in UTC** (`YYYY-MM-DDThh:mm:ssZ`, all valid) byte order is time order, and a comparison against a literal of the same shape is exact. The workaround for "count per day" is one call per day, with two conditions (measured on `m.csv`):

```
$ for d in 2026-01-02 2026-01-03; do table --where "ts >= '$d' and ts < '<next day>'" --agg count --format csv m.csv; done
2026-01-02: 4          # the date-only row 5 is included because it begins with the date
2026-01-03: 1
$ table --where "ts contains '2026-01'" --agg count --format csv t.csv     # a month by prefix: 5 (row 4, 2026-02-30, has another month)
```

That is a loop in the caller, a calendar computed by the caller (the next day, month ends), no output grouped by period and no guarantee about shape. It breaks at: any offset other than `Z`; a space for `T`; lowercase `t`/`z`; a date-only cell mixed with full ones; a fraction (measured, `f.csv`: `table --order-by ts` puts `2026-01-02T00:00:00.5Z` **before** `2026-01-02T00:00:00Z`, because `.` is 0x2E and `Z` is 0x5A, and the instant is half a second later); impossible dates, which are accepted without a word.

**JSON lines.** A JSON string is the cell, so the text rules above apply to it; **a JSON number is the number's token** (`docs/readers.md` 5.3, lines 214-217): `{"id":2,"ts":1767308400}` sorts by bytes before `2026-...` (measured: ids `2, 1`). The tool does not know that `1767308400` is 2026-01-02T01:00:00+02:00's epoch second, and the design below does not teach it.

## 2. Sources checked

Access date of all: **2026-10-16**. Where a page is a redirect or does not say, the row says so.

| id | what | URL |
|---|---|---|
| D1 | RFC 3339, "Date and Time on the Internet: Timestamps" (July 2002; updated by RFC 9557, per the RFC Editor page) | <https://www.rfc-editor.org/rfc/rfc3339> |
| D2 | ISO 8601, summary (the standard itself is not free: **the primary text was not read**) | <https://en.wikipedia.org/wiki/ISO_8601> |
| D3 | Howard Hinnant, "chrono-Compatible Low-Level Date Algorithms" | <https://howardhinnant.github.io/date_algorithms.html> |
| D4 | DuckDB, TIMESTAMP types | <https://duckdb.org/docs/current/sql/data_types/timestamp.html> (the `/stable/` URL is a redirect page) |
| D5 | DuckDB, DATE | <https://duckdb.org/docs/current/sql/data_types/date.html> |
| D6 | SQLite, date and time functions | <https://www.sqlite.org/lang_datefunc.html> |
| D7 | DuckDB v1.5.6 (the CLI installed on the Mac, `/opt/homebrew/bin/duckdb`) and Python's `sqlite3` with SQLite 3.53.4, run here | measured, section 3 |

### 2.1 The verified facts

| # | fact | source / how |
|---|---|---|
| F1 | RFC 3339 grammar: `date-time = full-date "T" full-time`; `full-time = partial-time time-offset`; `time-offset = "Z" / time-numoffset`; `time-secfrac = "." 1*DIGIT` (any number of digits) | D1 section 5.6 |
| F2 | The `T` and `Z` "may alternatively be lower case"; an application "may choose, for the sake of readability, to specify a full-date and full-time separated by (say) a space" | D1 section 5.6 (a note) |
| F3 | An offset of `-00:00` means "unknown local offset"; it "differs semantically from" `Z` and `+00:00`, which say UTC is the preferred reference | D1 section 4.3 |
| F4 | `time-second` "may have the value 60 at the end of months in which a leap second occurs"; "in time zones other than Z, the leap second point is shifted by the zone offset"; applications "should not generate" inserted leap seconds before they are announced | D1 section 5.7 and Appendix D |
| F5 | ISO 8601: years are four digits 0000 to 9999 (0000 = 1 BC); the calendar is Gregorian from 1582-10-15 and proleptic only by agreement; **basic format** has no separators ("should be avoided in plain text"); **week dates** `YYYY-Www-D` and **ordinal dates** `YYYY-DDD` exist; offsets may be `Z`, `+hh:mm`, `+hhmm`, `+hh`; a **negative zero offset is not permitted**; a time with no designator is **local time** (ambiguous) | D2 (secondary source) |
| F6 | ISO 8601: seconds run to 60 only for a leap second; **`24:00:00` was removed in ISO 8601-1:2019 and reinstated by an amendment of October 2022** as the end of a day | D2 (secondary source; **unverified against the standard**) |
| F7 | Hinnant's `days_from_civil(y, m, d)` and `civil_from_days(z)`: eras of 400 years = 146,097 days, epoch 1970-01-01 as day 0, internal shift 719,468 days (from 0000-03-01), exact over the proleptic Gregorian calendar "until integer overflow", **no validity checking of the input** | D3, code read verbatim |
| F8 | DuckDB `TIMESTAMP`: naive, microsecond precision, "extra decimal places are dropped" (`.123456789` shows as `.123456`); an offset in a string passed to a `WITHOUT TIME ZONE` type is **ignored**; a zone name other than UTC is an error | D4 |
| F9 | DuckDB `TIMESTAMPTZ`: an INT64 of microseconds since the epoch (an absolute instant); input with an offset is converted; input without one is read in the **configured session time zone**, which defaults to the system's | D4 |
| F10 | DuckDB `DATE`: `YYYY-MM-DD`, Gregorian even before the calendar existed; `infinity`, `-infinity`, `epoch` as special strings; the page gives **no numeric range and no error text** | D5 |
| F11 | SQLite accepts a list of 12 forms (`YYYY-MM-DD`, with `T` or a space, `HH:MM[:SS[.SSS]]`, `Z` or `[+-]HH:MM`, a Julian day number); valid range 0000-01-01 to 9999-12-31 23:59:59; only the first three fractional digits count; the page does not say what happens with `24:00` or `February 30` | D6 |
| F12 | The measured behaviour of both engines on the edge values of this design | section 3 |

## 3. What the other tools do (measured on the Mac, 2026-10-16: DuckDB v1.5.6, SQLite 3.53.4, Python 3.14 `datetime`)

DuckDB was run as `duckdb -c "select try_cast('<v>' as timestamp), try_cast('<v>' as timestamptz), try_cast('<v>' as date)"`; the session zone of this Mac is `+01` (Europe/Madrid, winter), which is why `timestamptz` shows `+01`. SQLite as `select julianday(?), datetime(?), strftime('%s',?)`. `-` is NULL (a refusal).

| value | DuckDB `TIMESTAMP` | DuckDB `TIMESTAMPTZ` | DuckDB `DATE` | SQLite `datetime()` | the design |
|---|---|---|---|---|---|
| `2026-02-30` | (error: `Conversion Error: date field value out of range: "2026-02-30"`) | | same | **`2026-03-02`** (normalised, silently) | refused (`value.not-date`, reason `day`) |
| `2026-04-31`, `1900-02-29` | `1900-02-29`: NULL | NULL | NULL | `2026-05-01`, `1900-03-01` (silently) | refused |
| `2000-02-29T00:00:00` | accepted | accepted | accepted | accepted | `:datetime` ok; `:timestamp` refused (no offset) |
| `2026-01-02T01:00:00+02:00` | `2026-01-02 01:00:00` (**offset ignored**) | `2026-01-02 00:00:00+01` (= 23:00Z) | `2026-01-02` | `2026-01-01 23:00:00` | `:timestamp`: 2026-01-01T23:00:00Z; `:datetime`: refused |
| `2026-01-02 01:00:00` (space) | accepted | read in the session zone (+01) | | accepted | accepted (`:datetime`) |
| `2026-01-02t01:00:00z` (lowercase) | **NULL** | NULL | `2026-01-02` | NULL | accepted (F2) |
| `2026-01-02T24:00:00` | `2026-01-03 00:00:00` (rolled) | rolled | | `2026-01-02 24:00:00` (kept as written) | refused |
| `2026-01-02T23:59:60` | NULL | NULL | | NULL | refused (reason `leap-second`) |
| `0001-01-01T00:00:00` | accepted | accepted (`+00:53`: a local mean time of Madrid) | | accepted | accepted |
| `9999-12-31T23:59:59.999999` | accepted | accepted | | `9999-12-31 23:59:59` (fraction cut) | accepted |
| `10000-01-01T00:00:00` | **accepted** | accepted | accepted | | refused (`value.not-timestamp`, reason `year`) |
| `2026-01-02T01:00:00.123456789` | `.123456` (**cut**) | `.123456` | | `01:00:00` (cut to ms, shown without) | refused (`value.timestamp-precision`) |
| `20260102T010000` (basic) | NULL | NULL | NULL | NULL | refused |
| ` 2026-01-02T01:00:00` (leading space) | accepted (**trimmed**) | accepted | | NULL | refused |
| `2026-1-2T1:0:0` | accepted | accepted | | | refused |
| `2026-01-02T01:00` (no seconds) | accepted | accepted | | accepted | refused (RFC 3339 requires seconds) |
| `2026-01-02T01:00:00+0200`, `+02` | accepted (ignored) | accepted, **applied** | | | refused (`+hh:mm` only) |
| `2026-01-02T01:00:00-00:00` | accepted | read as UTC (`02:00+01`) | | | refused (F3; reason `unknown-offset`) |
| `2026-01-02T01:00:00 Europe/Madrid` | NULL | accepted (zone applied) | | | refused (`value.timezone-unsupported`) |
| `2026-W01-1`, `2026-002` | NULL | NULL | NULL | NULL | refused |

What this says. (1) **No engine here refuses everything that is not a date**: SQLite normalises `2026-02-30` to March and `1900-02-29` to 1 March without a word; DuckDB rolls `24:00:00` to the next day, cuts the fraction, trims spaces, ignores the offset in the naive type and reads a missing one in a session zone that depends on the machine. (2) **They do not agree with each other**, so neither is an oracle for the refusals: they are one for the instant of a valid, offset-bearing value (section 12). (3) DuckDB's `TIMESTAMPTZ` from `0001-01-01` shows `+00:53`, a zone database at work; an answer that depends on the system zone is what `table` refuses to give. (4) Python's `datetime(…, tzinfo=timezone(timedelta))` is exact with fixed offsets and rejects `second=60`, `hour=24` and `1900-02-29`: it is the oracle of the grammar with a fixed offset and **no zone database** (section 12, G2).

## 4. The surface

### 4.1 The types

`COLUMN := WORD [ ":int" | ":float" | ":dec(" N ")" | ":date" | ":datetime" | ":timestamp" ]` (`expr.cho:11` is the grammar today; the suffix is read at `expr.cho:224-250`), in `--where`, `--group`, `--agg` (for `min`, `max`, `count`, `distinct`) and `--order-by`, and as a join key. An unmarked name is text, always (`docs/numbers.md` 5.1). A header that really ends in `:date` is quoted or escaped as one that ends in `:int` is (`expr.cho:225-250`): a **near-string** the compatibility gate checks (section 12, G-COMPAT).

| type | a cell is | the value | for |
|---|---|---|---|
| `:date` | `YYYY-MM-DD`, nothing else | days since 1970-01-01 | birthdays, due dates, daily files |
| `:timestamp` | a date, a time **and an offset** | microseconds since 1970-01-01T00:00:00Z, **UTC** | logs, API events, anything with `Z` or `+hh:mm` |
| `:datetime` | a date and a time, **no offset** | microseconds since 1970-01-01T00:00:00 **as a wall-clock count**: it is not an instant | database exports without a zone |

**One type or two, and then three.** The question was `:date` and `:timestamp` (one type or two). Two, because a date is not a midnight: `2026-01-02` against a timestamp column has no exact meaning (midnight where?), and the tool compares each column as one thing. **And a third, `:datetime`,** because the alternative is a guess. Half of the timestamps in the files this tool reads have no offset (`2026-01-02 13:45:00` is what a database export writes), and for them every other choice is wrong for somebody: refuse them (the tool is useless on them), read them as UTC (wrong silently for every file that is local time), or read them in a flag-declared zone (section 4.5). `:datetime` says what is true: a **wall-clock reading**, ordered and grouped by its digits, never compared with an instant. The cost is one more suffix; the code cost is one flag in the same parser (an offset is required, or refused). *Choice.* (Open question 1.)

**No precision parameter** (the model, `:dec(S)`'s scale, is `docs/numbers.md` 3.1): a timestamp is **microseconds**, fixed. See 6.1 for why nanoseconds cannot be had at the full range. A parameter `:timestamp(P)` for "at most P fractional digits" was weighed and not proposed: it would change nothing about the value, only add a refusal the user can already have with a `--where` on the text, and every extra spelling is a column to test. (Open question 2.)

**One spelling.** `:timestamp`, not `:ts`: `ts` is a common column name, and the suffix would then be read next to a column called `ts` (`ts:ts`), which is legal and unreadable. *Choice.*

### 4.2 Literals in `--where`

A literal is read by the **cell's own reader** for the column's type, so cell and literal obey one grammar (`docs/numbers.md` 3.5):

```
ts:timestamp >= '2026-01-02T00:00:00Z'          an instant
ts:timestamp in ('2026-01-02T00:00:00Z', '2026-01-02T01:00:00+02:00')
d:date >= '2026-01-02'
w:datetime < '2026-01-02 12:00:00'
ts:timestamp >= '2026-01-02'          where.syntax at the literal: expected "a timestamp with an offset: 2026-01-02T00:00:00Z"
d:date >= '2026-01-02T00:00:00Z'      where.syntax: expected "a date: YYYY-MM-DD"
ts:timestamp contains 'x'             where.syntax (as for :int, :dec, :float: expr.cho:119)
```

A **date literal against a timestamp column is refused**, not read as midnight UTC: the day boundary is the user's statement, and writing it costs one more condition (`ts:timestamp >= '2026-01-02T00:00:00Z' and ts:timestamp < '2026-01-03T00:00:00Z'`). The refusal's repair offers that exact rewriting as a whole invocation (`choose`), as the `:dec` scale repair does (`finish.cho:841-849`): the agent runs it, the tool does not assume it. *Choice.* Whether a literal is quoted follows the rule of every literal (a word, or in quotes); a bare `2026-01-02T00:00:00Z` contains `:` and the quoting rule for that is **not measured here** and is pinned in the T1 tests (today's `--where "ts >= 2026-01-02"` with a bare date is accepted as text: measured, section 1).

The literal is checked **at parse time of the expression**, as every typed literal is (`expr.cho:314-366`, cited in `docs/or-and-columns.md:49`): an impossible literal is `where.syntax` with `detail.offset`, `detail.expected`, and exit 2, before any row is read.

### 4.3 The flag forms

```
--where "ts:timestamp >= '2026-01-02T00:00:00Z'"
--group  ts:timestamp@day                      (T2)
--agg    min:ts:timestamp,max:ts:timestamp,count,distinct:ts:timestamp
--order-by -ts:timestamp                        (rows in instant order; ties in file order: docs/numbers.md 4.5)
--on     a=b:timestamp                          (a join key, both columns read as one type)
```

`min`, `max`, `count`, `distinct` take the types; `sum:` and `mean:` take them only to be refused (10). The mention rule of the plan is unchanged: **one type per column per plan** (`column.type-conflict`, exit 2), checked by `query.typed_mention` (`query.cho:280`) across conditions, aggregates and keys; an untyped mention is text and mixes (`ts != '' and ts:timestamp > '...'` is the guard idiom of `docs/filter.md:90-95`).

### 4.4 The query forms

`--query` already has `cast(c as type)` and `c::type` in a condition, a sort and an aggregate (`qparse.cho:224-253`; the words of `type_code` were `int`, `float`, `dec(N)`, `qparse.cho:197-219`, and `qtext.cho:56` was its `expected` text). T3 added `date`, `datetime` and `timestamp` to `type_code` and to that text; the translator writes the same suffix. So `select id from 'e.csv' where ts::timestamp >= '2026-01-02T00:00:00Z' order by ts::timestamp` is the flag plan above. **A typed `group by` is `query.unsupported` today** for every type (measured: `group by p::dec(2)`; the hint is "a type is part of how a column is read in a condition, a sort or an aggregate; selecting a column returns its text"), so **the period bucket has no query form** and stays a flag; the refusal's hint names the flag (T2 built that: the hint of `group by x::dec(2)` is now "the grammar groups by the text of a column; a grouping by value is the flag --group x:int, x:dec(N) or x:float; a period of a date, a timestamp or a datetime is --group ts:timestamp@day (or @hour, @month, @year)", and the hint measured here as "a type is part of how a column is read ..." belongs to a different feature, `cast` in a select list, not to the group by). *The design does not add `date_trunc(...)`:* `table` has no functions and the query subset is a translator of the flags (`docs/query.md` section 1). (Open question 7.)

### 4.5 No `--assume-utc`, no `--zone`

A flag that says "cells without an offset are UTC" (or `--zone +01:00`) was weighed:

| | `:datetime` (proposed) | `--assume-utc` / `--zone` |
|---|---|---|
| meaning of an unmarked name | unchanged (text) | unchanged, but the meaning of a **marked** one now depends on a flag elsewhere on the line |
| a file that mixes offsets and none | the user declares the column as the one reading that is true of **every** cell, or is refused with the row | the flag silently reads some cells in a zone they were not written in |
| `docs/numbers.md` 5.1 | the same shape as the suffix | the "global `--types`" that section rejected |
| comparison of a wall clock with an instant | cannot be written (one type per column, one literal type) | possible, and wrong in DST-ambiguous hours of a real zone |

Cost of the proposal: a user who **knows** the data is UTC writes `:datetime` and gets wall-clock semantics, which equal UTC semantics for that column; the printed key has no `Z`, so the file's own convention is kept. *Choice.* (Open question 3.)

## 5. The grammar

```
DATE      := YYYY "-" MM "-" DD
TIME      := hh ":" mm ":" ss [ "." DIGIT{1,6} ]
OFFSET    := "Z" | "z" | ("+" | "-") hh ":" mm
SEP       := "T" | "t" | " "                         (one byte)
:date       cell  := DATE
:datetime   cell  := DATE SEP TIME
:timestamp  cell  := DATE SEP TIME OFFSET
```

ASCII digits only. Nothing is trimmed (as `:int`, `docs/numbers.md` 3.2). The grammar is a **strict subset of RFC 3339 section 5.6** (F1, F2): every accepted cell is a valid RFC 3339 `date-time` (with `T`, `t` or space, `Z` or `z`), except the fraction, which RFC 3339 allows in any length and the design caps at 6 digits.

| input | decision | reason (and tag, section 10) |
|---|---|---|
| `YYYY` four digits, `0001`..`9999` | accepted | `0000` and a sign or more digits (expanded years) are refused: `value.not-date` / `not-timestamp`, reason `year`. Years 0001..9999 are what int64 holds at microseconds (6.1) and what SQLite's documented range (F11) and ISO's four digits (F5) share |
| proleptic Gregorian | **yes**, for the whole range | ISO says Gregorian from 1582 and proleptic only by agreement (F5); a table that stores `1066-10-14` writes the proleptic form that Hinnant's algorithm computes (F7), so declaring the column is the agreement. *Choice* |
| `MM` 01..12, `DD` valid for the month and year | validated | leap year: divisible by 4, except centuries not divisible by 400 (1900 no, 2000 yes); reason `month` / `day`. `2026-02-30`, `2026-04-31`, `1900-02-29` are refused |
| `hh` 00..23 | accepted | **`24:00:00` refused**, reason `hour-24`. ISO's position changed twice (F6, secondary source); RFC 3339 does not have it (`time-hour` is 00-23, F1); DuckDB rolls it to the next day and SQLite keeps it (section 3). A refusal is the only answer that neither rolls nor keeps. |
| `mm` 00..59, `ss` 00..59 | accepted | **`ss` = 60 refused**, reason `leap-second`. An i64 count of seconds has no slot for a second that is not counted (F4: RFC 3339 allows `60` only on a leap-second day, shifted by the offset, and an exact check needs the table of announced leap seconds, which changes). Mapping 60 to 59 or to the next 00 is a silent change of the instant. Repair: none (the message says to correct the file) |
| fraction `.d` to `.dddddd` | accepted, microseconds (`.5` = 500000) | **7 or more digits refused**, `value.timestamp-precision`, even when the extra digits are zero (`.1234560`): the rule of `:dec(S)`, which counts digits and not value (`docs/numbers.md` section 2 table, `1.500` refused at scale 2). No rounding, no truncation (DuckDB cuts, F8) |
| a `.` with no digit (`01:00:00.`), a `,` fraction | refused | reason `shape` |
| `SEP` `T`, `t`, or one space | accepted | F2; no semantic difference, so no guess. The space is what SQL exports write |
| `Z`, `z`, `+hh:mm`, `-hh:mm` | accepted | `hh` 00..23, `mm` 00..59 (RFC 3339's `time-numoffset`); the tool does **not** police real-world offsets (the real range is -12:00..+14:00, which no grammar states). The instant, not the digits, is range-checked (6.1) |
| `+hhmm`, `+hh`, no colon | refused | reason `offset-shape`. ISO allows them (F5), RFC 3339 does not; DuckDB accepts them. Open question 4 |
| `-00:00` | refused as `value.timezone-missing` (reason `unknown-offset`) | F3: RFC 3339 gives it the meaning "the offset is not known". Reading it as UTC would guess; it is also the form ISO forbids (F5) |
| `+00:00`, `Z`, `z` | the same instant | |
| a named zone (`Europe/Madrid`, `CET`, a bracket suffix as RFC 9557's `[Europe/Madrid]`) | refused, `value.timezone-unsupported` | no zone database (non-goal, 15) |
| no offset on `:timestamp` | refused, `value.timezone-missing`, reason `missing` | repair: the same invocation with `:datetime` |
| an offset on `:datetime` | refused, `value.timestamp-offset-present` | repair: the same invocation with `:timestamp` |
| `DATE` alone in `:timestamp` / `:datetime` | refused, `value.not-timestamp`, reason `date-only` | repair: the same invocation with `:date`. No midnight is invented |
| a time in `:date` | refused, `value.not-date`, reason `time-part` | repair: `:timestamp` or `:datetime` as the cell has an offset or not. Truncating to the day is the bucket (7), asked for, not guessed |
| reduced precision (`2026-01`, `01:00`), basic format (`20260102T010000`), week dates (`2026-W01-1`), ordinal dates (`2026-002`), `infinity`, `epoch` | refused, reason `shape` | F5 lists them; none is needed by a file this tool is asked to read, and each is a different grammar to test. They can be added later; a refusal can be relaxed and an answer cannot be taken back (`docs/or-and-columns.md` 3.3) |
| a leading or trailing space, an empty cell | refused | `value.not-date` / `value.not-timestamp`, reason `shape`; the empty cell is refused by every typed read (`docs/filter.md:88-95`) and the guard idiom works as for numbers |
| a JSON number (`1767308400`) in a JSON-lines column | refused (it is the token text, which has no `-`) | the tool does not guess seconds, milliseconds or microseconds (non-goal) |

**The order of checks** is fixed, as `docs/numbers.md` 3.2 fixed its own because a differential test had found two orders: (1) the **whole cell against the shape** (positions of digits and separators, offset form); (2) year, month, day against the calendar; (3) hour, minute, second (24 and 60 have their own reasons); (4) the fraction's digit count; (5) the offset's presence against the type; (6) the instant against the range (6.1). The tag follows the first failing step.

## 6. The exact representation

### 6.1 The integers, and why microseconds

Day number `z = days_from_civil(y, m, d)` (F7): `y -= m <= 2; era = floor(y / 400); yoe = y - era*400; doy = (153*(m>2 ? m-3 : m+9)+2)/5 + d-1; doe = yoe*365 + yoe/4 - yoe/100 + doy; return era*146097 + doe - 719468`. Validity is checked **before** it is called (F7: the algorithm does not check). A timestamp value is `((z * 86400) + hh*3600 + mm*60 + ss) * 1000000 + fraction_in_microseconds - offset_seconds * 1000000`. The printer is `civil_from_days` and a `div`/`mod` by 86,400,000,000 with a **floor** division (before 1970 the value is negative).

**Measured here** (a throwaway script, not kept: `python3 -I`, the full range of Python's `datetime.date` against Hinnant's `days_from_civil` as written in D3):

| check | result |
|---|---|
| days 0001-01-01 .. 9999-12-31 (3,652,059 days), `days_from_civil` against `date.toordinal()` shifted to the 1970 epoch | **0 mismatches** |
| `days_from_civil(1,1,1)`, `days_from_civil(9999,12,31)` | **-719,162** and **2,932,896** |
| seconds: -62,135,596,800 .. 253,402,300,799 | fits int64 |
| **microseconds**: -62,135,596,800,000,000 .. 253,402,300,799,999,999 | fits int64 (2^63 = 9,223,372,036,854,775,808) |
| milliseconds | fits |
| **nanoseconds**: -62,135,596,800,000,000,000 .. 253,402,300,799,999,999,999 | **overflows int64** (the range is about 2.5e20 against 9.2e18) |
| the microsecond maximum shifted by an offset of 14 hours | still fits |

So **microseconds are the finest unit that covers 0001..9999 in the 8 bytes the key machinery uses**; nanoseconds would limit the range to about 1677..2262 (an estimate from 2^63 ns, not computed above) and would need a second representation or a narrower grammar. A cell with nanoseconds is the common case in Go/Rust logs; it is refused with `value.timestamp-precision` and the message says that the finest unit is the microsecond. (Open question 2.)

**The instant is range-checked.** After the offset is applied the value must lie in **[0001-01-01T00:00:00Z, 9999-12-31T23:59:59.999999Z]**; `0001-01-01T00:00:00+01:00` is 0000-12-31T23:00Z and is refused, `value.timestamp-range`, with `detail.direction` (`before` / `after`). The reason: the printed key is always a four-digit UTC year. The integer cannot overflow in the arithmetic (the maximum with a 23:59 offset shift is far below 2^63; the table's last row).

### 6.2 Comparison, order, equality, min, max, distinct, group keys

* **Instant equality.** `2026-01-02T01:00:00+02:00` and `2026-01-01T23:00:00Z` are one value: `=`, `!=`, `in`, one group, one `distinct`, one join match. `Z`, `z`, `+00:00` are one. The cell `2026-01-02T01:00:00.50Z` and `...01:00:00.500000Z` are one value (digits are counted, not compared, only for the limit).
* **Order** is the order of the integer; `<`, `<=`, `>`, `>=` are integer comparisons (`expr.cho:745-762` is the shape for `:dec`; T1 adds branches). Dates sort as days, so `2026-01-02` and `2026-01-10` order as dates (as bytes they do too; the point is that `:date` is validated and that equal dates written once are one value).
* **Keys.** A typed key is `put_value_key` (`agg.cho:228-237`): the value with its sign bit flipped, 8 bytes big-endian, **bytewise order = numeric order**, equal keys = equal values; `value_of_key` (`agg.cho:1225-1231`) undoes it. The group machinery (`gmap`, the sort, `docs/numbers.md` 4.5) is unchanged. Joins use the same bytes (`jnkey.cho:25`, `jnkey.cho:75`).
* **`min`/`max`** keep the integer and print by the output rule (section 8); the partial values merge by smaller/larger as `:dec` does (`docs/numbers.md` 7).
* **`count`** counts rows; **`distinct:COL:type`** counts distinct instants (or dates).
* **`sum` and `mean` of any of the three are refused**: `agg.bad-spec` (exit 2, existing), `detail` names the type; the hint says what the types are for. A sum of instants has no unit; `mean` of dates is a defined rational but its rounding (to the day? to the microsecond?) is a choice nobody has asked for; it can be added later and cannot be taken back (`docs/or-and-columns.md` 3.3). *Choice.* (Open question 5.)
* **The `:float`, `:dec`, `:int` of the same column** in one plan is `column.type-conflict`, as today, by base type: `:date` against `:timestamp` is a conflict too.

### 6.3 What is not stored

The original offset is **not** kept in the value. `min`, `max` and group keys print the instant in UTC (8); the original text is available in rows (`--select`, and `--order-by ts:timestamp --top 1` gives the row of the earliest instant with its own text). A user who needs the original offsets groups by `--select`-free text, which is the text group of today (unchanged). *Cost of the choice, stated:* the key `2026-01-02T01:00:00+02:00` prints as `2026-01-01T23:00:00Z`.

## 7. Grouping by period

The request is "count per day / per month". The tool has no functions (`docs/query.md` section 1; `docs/numbers.md` section 5 "not typed" for `--select`).

| option | what it is | for | against |
|---|---|---|---|
| **A. a truncating type parameter**: `ts:timestamp(day)` | the type's reading truncates | one syntax, as `:dec(S)` | it **collides with `column.type-conflict`**: a plan that filters `ts:timestamp >= ...` (an instant) and groups `ts:timestamp(day)` reads one column as two types, so the most common plan is refused; relaxing the rule for a parameter that changes the value would let `ts:dec(2)` and `ts:dec(3)` mix, which `docs/numbers.md` section 6 forbids on purpose |
| **B. a separate flag** `--bucket ts=day` | a flag naming a column and a unit | the type stays one reading | a flag that refers to a column of `--group` by name: a second list to keep in step, a new rule for a name that is not a key, a new item in `--explain`, in the skill, in the MCP schema; the query has no spelling for it either |
| **C. leave it out** | the text workaround | no work | a loop in the caller with a calendar in it (section 1) |
| **D (recommended). a modifier on the key, `@UNIT`** | `--group ts:timestamp@day`, `--group d:date@month` | the precedent exists: `mean:price:dec(2)@4` is a **shape of the output of a reading**, not a second reading (`docs/numbers.md` 3.6); the conflict check compares the **base type** and ignores `@`; one flag; `--explain` prints it | a new modifier on a group key (parser, explain, `introspect`) |

**Recommended: D**, in its own slice (T2), after the types. Details:

* **Units.** For `:date`: `month`, `year`, `week`. For `:timestamp` and `:datetime`: `hour`, `day`, `month`, `year`, `week`. A unit finer than the type (`:date@hour`) or `minute`/`second` (use the text or the type) is refused with an existing tag, not a new one: `group.bad-bucket` is **not** a new tag, a choice to avoid a tag for a spelling error. T2 chose `args.bad-value` for the unit of a `--group` item (the flag's value is not of the flag's kind, the tag `:dec(19)` has in the same flag) and `agg.bad-spec` for a unit in `--agg`; `where.syntax` is for expressions and is not used (section 19, reading 2).
* **The day of a `:timestamp` is the UTC day** of the instant. A cell `2026-01-02T01:00:00+02:00` is in the bucket of 2026-01-01. *Reason:* the bucket must be a function of the **instant**, or two cells that are equal as values would fall into different groups (the property of 6.2). The user who wants local days has two true readings: `:datetime` (the wall clock, offset refused) when the file's convention is the zone he wants, or a fixed-offset shift, which is arithmetic and a non-goal here. For `:datetime` and `:date` the day is the written day. (Open question 6.)
* **The key of a bucket** is the **first value of the bucket**, a value of the same type, printed by the output rule: `2026-01-01T00:00:00Z` for `@day` of the example, `2026-01-01` for `d:date@month`, `2026-01-01` for `@year`. *Not* `2026-01` or `2026`: a key of the same type sorts, groups, joins and feeds back into `--where` unchanged, and a text key of another shape would not. *Choice.*
* **Week.** `@week` would start on **Monday** (ISO 8601's week, F5) and the key would be the Monday's date or midnight. No parameter for the start day. A week-year or a week number is not a bucket key (it would need a different key type). Open question 6 asked whether `week` is in T2 at all; **it is not** (answered by its recommendation, deferred), and `@week` is refused with a tag that says so (section 19, reading 1).
* **Floor, not truncation toward zero.** Before 1970 the day of `-1` microsecond is 1969-12-31; the bucket uses floor division. A test row for it is named in section 12.
* **Cost.** The bucket is computed from the already parsed value, on the keys of rows that pass the filter; it is an addition per group key and does not touch a plan without `@`. *Estimate*; section 19.3 has it measured (it is: every plan of the speed gate costs the same instructions a row) and has the cost of a bucketed key.
* **In `--agg`:** no bucket (`min:ts:timestamp@day` is refused): a bucket belongs to a key.

## 8. Output

| where | what is printed |
|---|---|
| a **row** (`--select`, `--order-by`, `--rows`, `--where` alone) | the **input bytes** of the cell, unchanged. `--select` does not read a cell (`docs/numbers.md` section 5) |
| a **group key**, `min`, `max` | the **normalised form**, a function of the value only: `:date` `YYYY-MM-DD`; `:timestamp` `YYYY-MM-DDThh:mm:ss[.fff[fff]]Z` in **UTC**; `:datetime` the same without `Z`. The fraction: none when it is zero, three digits when the value is a whole number of milliseconds, otherwise six. *Choice:* `:dec(S)` prints exactly `S` digits always (`docs/numbers.md` 3.7), which here would be six digits on every key; the 0/3/6 rule is a function of the value, so it is independent of the first row and of `--threads`, and it round-trips (`parse(print(v)) = v`) |
| JSON | `table.v2` rows are strings (`docs/numbers.md` 3.7) |
| `--format csv` | the same text; no quoting needed (no delimiter in it) |
| `--format jsonl` | a key or `min`/`max` of these types is a **JSON string**. **Measured today:** a `:dec(2)` key and `min:p:dec(2)` are written as bare JSON numbers (`{"p":1.50,"count":2}`, `{"g":"a","min:p":1.50,"count":2}`, `d.csv` of three rows), so `jsonl` is not "strings except counts" for decimals; for the date types it must be a string (`2026-01-01T23:00:00Z` is not a JSON number). `jout.cho` writes the exception per type; T3 pins it with a test |
| `--explain` | `"type": "date"`, `"timestamp"`, `"datetime"` (today `"dec(2)"`, measured, `plan.where[].type`); a bucket as `"bucket": "day"` |

**Round trip.** For every valid cell, `print(parse(cell))` is the canonical form and `parse(print(v)) = v`. A cell already in the canonical form prints as itself (the common case for a file in UTC with `Z`). A test pins it over the generated cells (section 12, G2).

## 9. The other parts of the tool

* **CSV, TSV.** Nothing in the reader changes: a cell is bytes until a typed condition reads it (`agg.cho:205-226`, `typed_cell`: unquote when a quote appears, then `query.parse_typed`).
* **JSON lines in** (`docs/readers.md` 5.3, 12.4): the string's decoded text is the cell, read by the same grammar. A JSON number is its token text, which fails the grammar (`value.not-timestamp`, reason `shape`): measured today that such a cell is plain text that sorts before `2026-` (section 1). Epoch seconds are not read (non-goal).
* **Standard input.** Unchanged (the type is a read of the cell).
* **Threads.** A typed read refuses at the first row in file order at every `--threads` (`docs/parallel.md` lines 4-8; `par.cho:168`); the new reads are pure functions of the cell, so the argument of `docs/or-and-columns.md` 6.1 holds as it does for `:dec`. A `min`/`max`/`distinct`/group merge is the integer's (`docs/numbers.md` 7). Gate G-PAR (section 12).
* **Joins** (`docs/joins.md` 5.2): `--on a=b:timestamp` reads both columns as one type and matches on **the instant**; `07`/`7` style equality is replaced by `+02:00`/`Z` equality. There is no cross-type join, as there is none of `int` against `dec`: a pair has one type, written once at the end of the pair, so `--on a:date=b` (the type after the probe column) and `--on a=b:date:timestamp` (two types) are `args.bad-value`, and a `:date` cell in a `:timestamp` pair is `value.not-timestamp` (T3 built it, section 20). A user who wants to join a day to an instant has no bucket on a key in v1 (non-goal: a bucket on a join key, not built in T3 either). The refusals are the `value.*` set with `detail.context: "join"` and `detail.input` (`jnkey.cho:11`).
* **`--report types`** (`report.cho:57` `classify`, `report.cho:36` `width`; `docs/numbers.md` 5.2): today a date column is `other` with `suggest` `none` (section 1, command 8). T3 counts, per column, the cells that are valid as `date`, `timestamp` (with offset) and `datetime` (without), and `suggest` becomes `:date`, `:timestamp` or `:datetime` **when every non-empty cell satisfies it** (the smallest declaration, the rule of N6), with `first_not_date_row`, ... as the other `first_not_*` columns do. It is **bounded** (a few counters per column; the cell is classified once, by the length and the separators first so that a non-date cell costs a few comparisons) and **never applies** (no auto type). *Corrected (open question 8 was answered by its recommendation, and T3 built it so):* the columns are **not** appended to `--report types`; they are a new report, **`--report dates`**, so that `--report types` keeps its bytes, its goldens and the `table.v2` columns it has, and risk 2 does not arise (section 20, reading 12).
* **`--explain`** (`docs/query.md` section 15): the plan lists the type, the bucket, and the refusals a plan can raise (`explain.cho:399-420` is where the numeric ones are listed, per `docs/or-and-columns.md:127`).
* **MCP, `introspect`, the skill.** The flag help (`spec.cho`) gains the three suffixes and `@UNIT`; `scripts/site.py --check`, `scripts/schemas.py --check`, `scripts/manifest.py --check`, `scripts/mcp.py --check` regenerate; the help examples are run by the tests. The help is one line without `;` or `|` (the contract's limit, `docs/backlog.md` friction list).

## 10. Refusals

All carry `row` (1-based among data records), `line`, `column` (name and 1-based position), `value` (the cell, first 64 bytes), `context` (`where`, `min`, `max`, `distinct`, `group key`, `sort key`, `join`); the first in **file order** at every `--threads`; the exit code is the table of `docs/refusals.md` ("Exit status", generated from `table introspect`): **8** for data (`PRECONDITION_FAILED`), **2** for usage (`INVALID_ARGS`). *(The task referred to "the D4 table of the contract"; that name does not occur in this repository and the table above is the one this tool publishes: unverified that they are the same.)*

| tag | exit | when | `detail` | repair (`choose`, whole invocations) |
|---|---|---|---|---|
| `value.not-date` | 8 | a `:date` cell is not `YYYY-MM-DD`, or is not a real day | `reason`: `shape`, `year`, `month`, `day`, `time-part`; `offset` (byte in the cell) | `time-part`: the same argv with `:timestamp` (offset present) or `:datetime` (absent); else none |
| `value.not-timestamp` | 8 | a `:timestamp` / `:datetime` cell is not a date and time | `reason`: `shape`, `year`, `month`, `day`, `hour`, `hour-24`, `minute`, `second`, `leap-second`, `offset-shape`, `date-only`; `offset` | `date-only`: the same argv with `:date`; else none |
| `value.timestamp-precision` | 8 | more than 6 fractional digits | `digits` | none (the message says the finest unit is the microsecond) |
| `value.timestamp-range` | 8 | the instant is outside 0001..9999 UTC | `direction` (`before`, `after`) | none |
| `value.timezone-missing` | 8 | a `:timestamp` cell with no offset, or `-00:00` | `reason`: `missing`, `unknown-offset` | the same argv with `:datetime` (for `missing`) |
| `value.timestamp-offset-present` | 8 | a `:datetime` cell with an offset | | the same argv with `:timestamp` |
| `value.timezone-unsupported` | 8 | a named zone, a bracketed suffix | | none (no zone database) |
| `column.type-conflict` (exists) | 2 | one column given two base types | | the argvs |
| `where.syntax` (exists) | 2 | a literal that is not of the type: a date against a timestamp, a timestamp against a date, `contains` on a typed column | `offset`, `expected` | the midnight rewriting for a date literal |
| `agg.bad-spec` (exists) | 2 | `sum:`/`mean:` of the types; a bucket in `--agg` (`min:ts:timestamp@day`: a bucket belongs to a group key) | | none |
| `args.bad-value` (exists) | 2 | the unit of a `--group` bucket (T2): `week` (not built), `hour` or `day` of a `:date` (finer than the type), anything else (`minute`, `second`, `DAY`, empty, a second `@`) | `flags`, `item`, `unit`, `reason` (`week-not-built`, `finer-than-type`, `unknown-unit`) | none |
| `limit.*`, `parse.*` (exist) | | unchanged | | |

**Why seven new tags and not one**: the repairs differ (`:datetime`, `:timestamp`, `:date`, none), and a caller matches on the tag (`docs/refusals.md`: "match on this"). Reasons live in `detail.reason`, not in more tags (the same split as `value.float-range`'s `detail.direction`, `docs/numbers.md` section 6). The tags are `extra_rules` entries (`tag|exit|retry|summary`, `finish.cho:76`) with a fixture each (gate G-RULES).

The status codes that `query.parse_typed` returns today are 0, 3-8, and 9-13 for floats (`query.cho:249-251`); T1 takes the next free numbers (14 and up). The mapping of status to rule is in `finish.cho` (`value.not-integer` at 745, `value.not-float` at 786, `value.not-decimal` at 841) and gets one more block per tag.

## 11. Cost (estimates; nothing here was timed)

**The unit.** `docs/numbers.md` measured the cost of reading one cell **above the 7.2-8.7 ns of the line scan**, on the Mac: `parse_int` **+1.1 ns** (5 digits) to **+2.2 ns** (8 digits); `parse_dec` at scale 0 **+3.6 ns**, prices at scale 2 **+3.3 ns**; the float scanner with Clinger's path **+3.7 ns** on prices and +9.6 ns on `1.5e3`-shaped cells; against **62-76 ns** a row (`docs/numbers.md` section 9 table and 3.3). `docs/or-and-columns.md` section 10 measured instructions retired on the 1M-row file: a row costs about **591** instructions to read and split and about **1,080** with one typed `:int` condition, so one more tried `:int` condition costs about **238** instructions a row (build of `fb4098d`).

**The estimate, for a feature that does not exist.** A text comparison of a cell with a literal today is one byte compare. A `:timestamp` read validates 20 to 33 bytes at known positions (shape, digit ranges, month length), then computes the day number (the algorithm has a few multiplications and shifts, no data-dependent loop), applies the offset, and checks the range. *Estimate:* **three to eight times the `parse_dec` figure above, about 10 to 30 ns above the line scan, 100 to 400 more instructions than a tried `:int` condition**; that is, a tried typed date condition may cost 1.2x to 2x the row cost of a tried `:int` condition, and a `:date` cell (10 bytes, no offset) less. This is an estimate **by analogy**; no prototype exists and none is written here. The gate G-SPEED fixes what is measured after T1 and what happens if it is above a bound (section 12).

**Why the text path is not comparable.** For a file of uniform UTC values, text comparison is exact and the cheapest thing; a typed read is a **correctness feature**, not a speed one. The design does not claim to be faster than text, or than DuckDB's date parser, for anything.

**The existing paths must not move.** Budget: **instructions retired, bound 1.01**, `scripts/gate_regress.py --counter`, every cell already in it, three builds each side, the old side `origin/main` at the commit T1 starts from (the method and its history are `docs/numbers.md` "G6, third revision" and `docs/or-and-columns.md` section 10). The risk is the one those two documents record: a new case in `holds` or `parse_typed` changes the text of a function in the read loop and moves the count of plans that do not use it (the `typed >= 2` branch at `expr.cho:781-783` sends every `:dec` and `:float` to `holds_dec`; `parse_typed` at `query.cho:252` (comment 249-251) tests `kind == 21`, then `kind >= 2`). Placement: the new types get their own codes above the float's (22 `:date`, 23 `:timestamp`, 24 `:datetime`; today `1` is `:int`, `2 + S` is `:dec(S)` for S 0..18 so 2..20, `21` is `:float`: `query.cho:17-20`, `query.cho:249-251`, `expr.cho:11`), their own function, and a test that sits **after** the existing branches; if the count moves, the options are those of `docs/or-and-columns.md` section 10 (a function of its own, a dispatch chosen once per plan). The bound is not relaxed.

## 12. Gates, written before any code

Each gate has a pass line and a way to fail. Every stage ends with every earlier gate green.

| gate | what is held | pass line | how it can fail (mutants) |
|---|---|---|---|
| **G0** | the facts of this document | the checks of sections 1, 3 and 6.1 re-run on the build the stage starts from, the outputs diffed against this text | a drift of the existing text behaviour |
| **G-COMPAT** | every valid call of today means the same | the md5 of the **166 outputs** of `scripts/corpus.py` (41 plans, `docs/query.md:430-431`) unchanged; the generated plans of `test_plan.py` unchanged; **near strings**: a header ending in `:date`, `:datetime`, `:timestamp`, quoted and not, in `--where`, `--group`, `--agg`, `--order-by`, `--select`; a column called `date` or `timestamp`; `--report types` unchanged for files without date columns (T1, T2) | a suffix read where a name was meant; `:date` taken for the beginning of `:dec` (`expr.cho:228` tests `:dec` by four bytes) |
| **G-ORACLE-GRAMMAR** | the grammar and the instants | **Python with fixed-offset arithmetic and no `zoneinfo`**: a reference (`tests/conformance/datetime_ref.py`, new, sharing no code with the tool) that parses the grammar of section 5 by hand and computes the instant with `datetime`/`timedelta`; generated cells: every leap-day case (1900 no, 2000 yes, 2100 no, 2400 yes, 2024, 2026), every month end, offsets `+14:00`, `-12:00`, `+00:00`, `-00:00`, `+23:59`, `-23:59`, `24:00`, `60`, years 1 and 9999 and their offset overflows (`0001-01-01T00:00:00+01:00`, `9999-12-31T23:59:59.999999-01:00`), fractional digits 0 to 9, whitespace (leading, trailing, inside), lowercase `t`/`z`, a space for `T`, the basic and week and ordinal forms, empty, a lone sign, multibyte digits | tag, `detail.reason`, row, line, column equal; for a valid cell, the instant equal; 100,000 generated cells | an order of checks that differs (the reference decides it, as N1's did) |
| **G-ORACLE-SQL** | the instants, for **valid** cells only | **DuckDB 1.5.6** (`TIMESTAMPTZ` with the session set to UTC, `DATE`) and **SQLite 3.53.4** (`julianday`, `strftime('%s')`) agree with the tool on the order and equality of the valid cells, 3,000 generated tables x conditions {`<`, `<=`, `=`, `in`} x `--threads` {1, 4}; the generator draws only valid, microsecond-or-coarser, offset-bearing values; **a disagreement between the two engines is the alarm** | an SQL engine is **not** the oracle for refusals: section 3 measured that SQLite normalises `2026-02-30` and DuckDB rolls `24:00:00` |
| **G-ALGORITHM** | the day number | `days_from_civil` and `civil_from_days` against `datetime.date` for every day of 0001..9999 (3,652,059: **0 mismatches measured here for the algorithm in Python**; the gate runs the tool's version, through a probe like `tests/conformance/cellnum_probe`) | an era boundary wrong; negative `era`; `floor` as truncation |
| **G-ROUND** | print and parse | `parse(print(v)) = v` and `print(parse(print(v))) = print(v)` for 100,000 generated values; the bucket of values before 1970 and at the year 1 and 9999 ends | a printer that drops the fraction; truncation for floor |
| **G-DIFF** | the plans | the plan generator of `test_plan.py` extended with the three types (conditions, `in`, group keys, `min`/`max`, `distinct`, `--order-by`, sort, top, csv/json/jsonl): the reference `refimpl.py` extended by an independent Python reading, rows and refusals equal | the instant compared as text; a group split by offset |
| **G-PAR** | the same bytes at any thread count | the matrix of `docs/numbers.md` G4 (N in {2,3,4,8,16,64} x chunks {1, 7, 64, 1000}) over files with refusals in the middle and mixed offsets; the sequential bytes | a range that evaluates differently |
| **G-RULES** | the tags | each tag of section 10 with a fixture and `introspect` lists them; the exit codes of the table | a rule without a fixture |
| **G-CORPUS-EQ** | flags and query give one plan | `scripts/query_corpus.py` and `Equivalence` in `test_query.py` with `cast(ts as timestamp)` and `ts::date` (T3) | the translator writing the wrong suffix |
| **G-JOIN** | typed join keys | `test_join_j2.py`'s typed-key cases repeated for the three types; instants in two offsets match; `:date` against `:timestamp` is `args.bad-value` | a join on text |
| **G-FUZZ** | no trap | grammar-fuzzed cells (the alphabet: digits, `-`, `:`, `T`, `t`, `Z`, `z`, `+`, `.`, space, `,`, multibyte) and byte-mutated valid files, 300 plans: no trap, no hang, the reference's tag | a trap on a length-0 cell, on a cell shorter than 10 bytes, on a 1 MiB cell |
| **G-MUT** | the logic matters | `scripts/datetime_mutants.py` (new, `--check` in CI as `float_mutants.py` has): at least 60 mutants: leap rule `% 100` removed, `% 400` removed; month lengths off by one; `24` accepted; `60` mapped to 59; the offset sign flipped; the offset applied twice or not at all for `Z`; minutes of the offset dropped; `-00:00` as UTC; a fraction rounded or cut; 7 digits accepted; the year range 0000 or 10000; the range checked before the offset; `floor` as truncation; era boundary off by one; the key without the sign flip; seconds in a `:date`; the order of checks changed; the bucket UTC day replaced by the cell's local day; the week start Sunday; the month bucket not 1; a `contains` accepted on a type; `sum` accepted. Every one killed; an equivalent one is proved in a comment | a survivor without a proof |
| **G-SPEED** | the existing paths | `gate_regress.py --counter`: instructions retired, bound **1.01**, every cell already in it, three builds each side, on the Mac, and on gram (`perf stat -e instructions:u`; niced, one job, never while the soak needs the cores) when the stage is built | any cell over 1.01 (section 11) |
| **G-NEW-SPEED** | the new path, reported | the per-cell cost of the three types against `:int` and `:dec(2)` on the same 1M-row file, `scripts/cellcost.py` style; **reported, with no pass line until it is measured once** (the way N3a was) | a number nobody wrote down |
| **G-GEN** | the surface regenerates | `scripts/site.py --check`, `scripts/schemas.py --check`, `scripts/manifest.py --check`, `scripts/mcp.py --check`, `scripts/seo_check.py`; the help examples run by `test_skill` | a stale generated block |
| **G-DOC** | present tense | `docs/filter.md`, `docs/query.md`, `docs/refusals.md` and the README say what exists after each stage, not what is planned | a doc that says "will" |

## 13. Staging

Sizes are **estimates by analogy**; the `:dec(S)` sources are `dec.cho` 311 lines, `flt.cho` 466, `cellnum.cho` 143 (`wc -l`, section 0's tree); `or-and-columns.md` section 11 sized its slices the same way (A1: 600 lines of tool, 1,500 of tests). Each slice is one pull request that ends with every earlier gate green.

| slice | what | proves | size (estimate) |
|---|---|---|---|
| **T0** | this document accepted; G-ORACLE-GRAMMAR's reference and its generated corpus, the mutants list and the golden refusals committed as failing tests | the gates exist and fail for the right reason | tests 600 lines, no tool code |
| **T1** | `:date`, `:timestamp`, `:datetime` (a new module `tm.cho` for the parse, day number and printer, **about 300 to 450 lines**; the suffix in `expr.cho`, the type codes in `query.cho`, `agg.cho` keys and `min`/`max`/`distinct`, `finish.cho` rules, `explain`), in `--where`, `--group`, `--agg`, `--order-by`, the seven tags, the refusals | G-COMPAT, G-ORACLE-*, G-ALGORITHM, G-ROUND, G-DIFF, G-PAR, G-RULES, G-FUZZ, G-MUT, G-SPEED | tool 600 to 900 lines in all, tests 1,500 to 2,500. **Comparable to N1 + N2 together** (the decimal reader and its aggregates), **smaller than J1** (a new form with a build table), by estimate |
| **T2** (built: section 19; `week` not built) | `@UNIT` on a group key: `day`, `month`, `year`, `hour`, and `week` if the maintainer wants it | the bucket as a function of the instant, floor division, the week start, G-ROUND with buckets, mutants of the bucket | tool 150 to 250, tests 400 to 600 |
| **T3** (built: section 20) | JSON lines out for the types, `--query` (`type_code`), joins on the types, `--report dates` (a new report; `--report types` unchanged), MCP schema, the docs | G-CORPUS-EQ, G-JOIN, G-GEN, G-DOC | tool 350 to 500, tests 800 to 1,200; **about J2's size** |

**Order:** T0, T1, T2, T3. T1 alone answers "sort, compare and count distinct dates and timestamps correctly" and refuses what text accepts; T2 answers "per day"; T3 is the surface around it. T2 may be built before T3.

## 14. Alternatives considered

1. **Do nothing; document the text workaround.** Honest, and cheap: the section 1 loop works for **uniform UTC** files and the docs could say so. It does not refuse `2026-02-30`, cannot order mixed offsets, cannot group by period and cannot be fixed by the caller without a date library, which an agent with only `table` through MCP does not have. *Not recommended as the end state; recommended as the text of `docs/filter.md` until T1, and it is the section 1 commands.*
2. **A sort-key-only normaliser** (`--order-by ts:timestamp` only, nothing else typed). Smaller (no equality, no group, no min/max), and it would fix the worst of the ordering failures. It would leave `distinct`, `group` and `where` byte-wise, so the same column would have two equalities in one call (the plan would order by instants and count distinct bytes). The cost of the rest, once the parse exists, is small: the key machinery is the one the decimals use. *Rejected: it makes the inconsistency the feature.*
3. **Delegate to DuckDB** (`duckdb` for any file with dates). Right for a user with DuckDB; wrong for the tool's niche: no implicit processes, no network, deterministic bytes, and DuckDB's own answer depends on the session zone, rolls `24:00:00`, cuts digits and ignores offsets in the naive type (section 3). *The design is the answer for an agent that has this tool and not that one; the comparison with DuckDB would be a gate of exactness (G-ORACLE-SQL), not of speed.*
4. **One `:ts` type, a missing offset read as UTC, `--assume-utc` for the rest.** Smaller and what most tools do. Rejected in 4.1 and 4.5.
5. **Epoch integers** (`ts:epoch_s`). A different feature: it declares a unit, and the cell is a number the `:int` reader already reads; a bucket or a printer on it would be the way in. Not designed; a possible T4 if someone has such a file.
6. **Calendar arithmetic** (`ts + 1d`, differences). Out (15).

## 15. Risks and non-goals

**Risks.**

1. **A near string in a header** (`created:date`) changes meaning: a column that was addressable by that exact name now needs quoting. The same happened for `:int` and is the documented escape (`expr.cho:225-250`). G-COMPAT covers it; the cost is that a user of such a header must quote.
2. **`--report types` changes its bytes** in T3 (new columns; *it does not: T3 built `--report dates` beside it, section 20*): the schema, the goldens and every consumer that indexes the columns by position. Mitigation: append at the end, keep the old columns and their order, and let the report schema be regenerated; a flag to ask for the old shape is not proposed. If the maintainer wants the report byte-identical, the date suggestion can live in `--report dates`, a new report with its own columns (open question 8).
3. **The 1.01 bound** (section 11): a new type branch in `holds` or `parse_typed` can move the count of plans that do not use it. The remedy is written; the bound is not relaxed.
4. **An unmeasured cost.** Section 11 is an estimate by analogy. If the typed read costs several times a `:dec` read, a feature that is a correctness feature remains one, but the documentation must not call it fast.
5. **The grammar is stricter than the data.** Files with `+0200`, 7 to 9 fractional digits, no seconds, or `24:00` are common. The refusals say exactly where and why, and `detail.reason` is machine-readable, but a user pays one retry per class of defect; relaxing a refusal later is possible, taking an answer back is not. (Open questions 2 and 4.)
6. **ISO 8601 facts come from a secondary source** (F5, F6): the standard was not read. The design does not depend on them for anything but the list of what is refused; RFC 3339 and the engines' measured behaviour carry the accepted grammar.
7. **`:datetime` can be misread as an instant** by a person who puts a UTC file in it and then mixes it with a `:timestamp` column in a join. The join refuses the cross-type pair (`args.bad-value`); the documentation says that a `:datetime` is a wall clock.
8. **Leap seconds** are refused at `:60`, which makes a leap-second log line unreadable until the user filters it. The repair is a filter that is false first (`ts != '2016-12-31T23:59:60Z' and ts:timestamp ...`), the idiom for NaN.

**Non-goals.** No zone database or any local-time conversion (a `Europe/Madrid` cell is refused); no arithmetic (no `+ 1d`, no difference of two columns); no formatting functions (`strftime`, `to_char`) and no output in another zone; no parsing of locale formats (`02/01/2026`, `Jan 2`); no epoch integers (14.5); no leap-second table; no `now`; no `infinity`/`epoch` strings; no `sum`/`mean`; no reduced-precision, basic, week or ordinal forms in v1; no bucket on a join key in v1.

## 16. Open questions for the maintainer

Each has a recommendation; none is decided.

1. **Three types (`:date`, `:timestamp`, `:datetime`) or two?** *Recommend: three* (4.1). The third is the same parser with an offset rule; the alternative is a guess about every file that has no offset.
2. **Microsecond only, refusing 7+ digits, or a precision parameter, or a nanosecond type?** *Recommend: microseconds, refuse, no parameter* (6.1: nanoseconds do not fit the full range in 8 bytes). Revisit with a real nanosecond file; a nanosecond type would be a narrower range (about 1677..2262, an estimate) and a separate type, not a parameter of this one.
3. **No flag for a default zone** (`--assume-utc`)? *Recommend: no*, `:datetime` instead (4.5). If the maintainer wants the flag, it should be per-reference (`ts:timestamp(utc)`) and not global.
4. **`+hhmm` and `+hh`: refuse or accept?** *Recommend: refuse in v1* (RFC 3339 does not have them; DuckDB accepts them; ISO allows them). They can be added without breaking a call.
5. **`sum`, `mean`, differences: refuse.** *Recommend: refuse*, with the reopening condition "a user asks for `mean` of dates with a stated rounding".
6. **Bucket: UTC day for an instant, Monday for the week, `week` in T2 or not?** *Recommend: the UTC day, Monday, and `week` deferred to a later slice* unless somebody needs it (a week-based key is where year boundaries are the traps).
7. **A query form for the bucket?** *Recommend: none now.* A typed `group by` is unsupported in `--query` for every type today (section 4.4); the bucket follows the same rule and the refusal names the flag.
8. **`--report types`: add columns to the existing report or a new `--report dates`?** *Recommend: a new report `--report dates`*, to keep the existing report byte-identical (risk 2), unless the maintainer prefers one report; the cost is one more report form.
9. **Whether a date literal on a timestamp column is refused (4.2).** *Recommend: refuse, with the midnight rewriting as the repair.*
10. **Lowercase `t`/`z` and a space as the separator.** *Recommend: accept all three* (RFC 3339 says so, F2), because nothing is guessed; the strict alternative is a refusal that fires on every SQL export.
11. **The fraction of a printed key: 0/3/6 digits, or always 6?** *Recommend: 0/3/6* (8); always 6 is also exact, and louder.
12. **Is the original offset worth keeping** (a second column of output with the offset of the first cell)? *Recommend: no*: the rows keep the original text (6.3).

## 17. What this document did not measure, and what is unverified

* **Nothing proposed was timed or built.** Every cost is an estimate by analogy (section 11). The numbers quoted from `docs/numbers.md` and `docs/or-and-columns.md` are theirs, measured on other builds (`fb4098d`, `db7d5bc`), not re-measured here.
* **The binary of section 1 was not rebuilt** from this branch's worktree; it is the `f33897c` build whose program text equals `origin/main`'s (checked with `git diff`, which lists one test file).
* **gram (Linux x86-64) was not used.** The outputs of sections 1 and 3 are the Mac's; DuckDB's session zone on this Mac is `+01`. Behaviour on Linux was not checked; the design does not depend on a zone.
* **ISO 8601** facts (F5, F6) come from a summary page, not from the standard; the October 2022 amendment on `24:00:00` is a claim of that page. The DuckDB pages (F8 to F10) do not state a numeric range or error texts; the table in section 3 is measured on 1.5.6 and nothing in it is a claim about other versions.
* **The quoting of a bare timestamp literal** (a word with `:`) in `--where` was not tested (4.2).
* **The mapping of an exit code to "D4 of the contract"** (10): the name is not in this repository.
* **The nanosecond range** (about 1677..2262) is `2^63` nanoseconds, computed by hand and not checked.
* **Agent behaviour.** That agents write `ts::date`, `date_trunc` and `group by day` is an assumption from how they write SQL elsewhere in this tool's history (`docs/query.md` section 12), not a measurement.

## 18. T1 as built

**Status.** Sections 1 to 17 are the design as accepted (PR #76, all twelve questions of section 16 answered by their recommendations). This section is what slices **T0 and T1** built, in one pull request of two commits (T0 the gates, written first and failing on `main`'s binary; T1 the types), the **narrow readings** where the design left a gap, and the **measurements**, each with its conditions. T2 (`@UNIT`) is section 19 and T3 (`--query`, joins, `--report dates`, the help) is section 20. When this section was written they were not built: `--on a=b:date` was `args.bad-value`, `cast(x as date)` was `query.syntax`, and `--report types` is, still, byte for byte what it was.

### 18.1 What is built

`:date`, `:timestamp` and `:datetime` are suffixes of a column in `--where` (conditions, `in`, typed literals), `--group`, `--agg` (`min`, `max`, `distinct`; `count` counts rows) and `--order-by`, and in `--join-where` and in the `--where` and `--group` of a joined row, which read them through the same code (nothing was added to the join; the key of a pair is T3). The new modules are `dtparse.cho` (the reader, the day number and its inverse, the printer), `dtfail.cho` (the seven refusals and their repairs), `dtrepair.cho` (the day-boundary repair of a date written where an instant is read) and `dtjson.cho` (a JSON-lines group with a date type); the type codes are 22, 23 and 24, and a refusal is a status of 14 and more (`14 + reason + 64 * the byte of the cell`, with the abort `1000 + status`, which `engine.cho` and `finish.cho` read). Hooks in the existing files: `expr.cho` (the suffix, the literal, `holds_date`), `agg.cho` (the tails, `read_typed`, the key and the minimum written), `sorter.cho`, `engine.cho`, `finish.cho` (the rules), `explain.cho`, `explainor.cho`, `table.cho` (the rule list, the hint of `agg.bad-spec` for a sum of dates and the repair of a date literal), `jout.cho` (one call), `spec.cho` (the help). `scripts/datetime_mutants.py`, `scripts/bench_datetime.py`, `tests/conformance/datetime_ref.py`, `test_datetime.py` and `dtparse_probe/` are new.

### 18.2 The narrow readings

Each of these is a gap or an ambiguity of sections 5 to 10 and the smallest reading that closes it; each is a line of `datetime_ref.py` and a case of `test_datetime.py`.

1. **`detail.offset`** (section 10 names it and does not define it) is the 0-based byte of the cell where the fault is: for a fault of shape the first byte that does not fit the pattern (the length of the cell when it ends too soon, `20` for a point with no digit), `0` for a year, `5` a month, `8` a day, `11` an hour (`hour` and `hour-24`), `14` a minute, `17` a second (`second` and `leap-second`), `10` for `date-only` and `time-part`, and the first byte of the offset for `offset-shape` (`19` in a cell of whole seconds).
2. **The shape comes first and says what the tail is.** After `hh:mm:ss[.f+]` the tail is: empty; `Z` or `z`; or exactly a sign and `dd:dd` (an offset). Anything else is, in this order: a bracketed suffix (a tail that begins with `[`, or `Z`, `z` or an offset followed by `[`) or a name (`^ ?[A-Za-z][A-Za-z0-9_/+:-]*$`: `Europe/Madrid`, ` CET`, `UTC`, and also `Zx`), both `value.timezone-unsupported`; a tail that begins with a sign, `value.not-timestamp` reason `offset-shape` at its first byte; one that begins with `Z` or `z`, `shape` at the byte after it; else `shape` at its first byte. Section 5 lists "a named zone, a bracketed suffix" and not their form.
3. **An offset whose digits are out of range** (`+24:00`, `+00:60`: section 5 gives `hh` 00..23 and `mm` 00..59 and no tag) is `value.not-timestamp` reason `offset-shape`, checked with the clock (step 3), before the fraction's digits.
4. **A cell of exactly ten bytes with the shape of a date is `date-only` in a `:timestamp` or `:datetime` even when it is no real day** (`2026-02-30`): the shape step comes first.
5. **`time-part`** (a time in a `:date`) is a cell longer than a date, whose date has the shape, that is a **valid** `:timestamp` or a valid `:datetime`; any other cell longer than a date is `shape` at the byte of its fault (10 when its time part is well formed and its values are not). The repair `:timestamp` (offset present) or `:datetime` (absent) is then always accepted by the grammar.
6. **`-00:00`** is `value.timezone-missing` reason `unknown-offset` in a `:timestamp`, and `value.timestamp-offset-present` in a `:datetime` (it has an offset).
7. **Contexts** are the strings `finish.context_of` already writes: `where`, `min`, `max`, `distinct`, `group`, `order-by` (section 10 says "group key" and "sort key"), and `detail.column` is the header's name, as for `value.not-decimal` (section 10 says "name and 1-based position"). No `type` key is added: the tag and the reason say it.
8. **The repair** (section 10: "the same argv with `:datetime`") is the same invocation with the type written where the flags name it, offered only when it can be written whole: the cell was read by `--group`, `--agg` (`min`, `max`, `distinct`) or `--order-by`; no condition of `--where` reads that column with the old type (it has literals of the old type, which the new one would refuse: the case of a `--where` that was not reached before the group read the cell, and of a column called `#N`, are refusals without a repair); no other column has the old type in those flags (the flags are rewritten as text); and the plan is not a `--query`. Otherwise `repair.kind` is `none` with the reason. For `time-part` the type is `:timestamp` if the cell is a valid timestamp, else `:datetime`; `date-only` gives `:date`; `missing` gives `:datetime`; `offset-present` gives `:timestamp`; `unknown-offset`, `precision`, `range` and `unsupported` have none.
9. **A date literal against a timestamp column** (section 4.2: "the refusal's repair offers that exact rewriting as a whole invocation") is `where.syntax` at the literal's first byte (the quote, when it is quoted) with `expected` `a timestamp with an offset: 2026-01-02T00:00:00Z`, and a `choose` repair when the literal is a real date: `>=` and `<` replace the literal by midnight UTC (wherever the condition is); `>`, `<=`, `=` and `!=` need the column written again, so they are only offered when `--where` is that one condition, as `COLUMN >= next`, `COLUMN < next`, `COLUMN >= day and COLUMN < next` and `(COLUMN < day or COLUMN >= next)`; the last day, 9999-12-31, has no next one. The text is read again by the grammar before it is offered. The other `expected` texts: `a date: YYYY-MM-DD` and `a date and a time without an offset: 2026-01-02 12:00:00`.
10. **`contains`** (and `not contains`) on the three types is `where.syntax` with `expected` `= != < <= > >= or in: contains compares text, and this column is :date, :timestamp or :datetime`.
11. **`column.type-conflict`** lists the two types in the order the plan's mentions are checked (conditions, aggregates, group keys, sort keys), as it does for the numbers: `--group ts:date --agg min:ts:timestamp` gives `[":timestamp", ":date"]`.
12. **`sum` and `mean`** of the types are `agg.bad-spec` with the usual `detail` (`item`, `agg`: the text of `--agg`, which names the type); the **hint** says what the types are for, for those items only.
13. **JSON lines**: a key, a `min` and a `max` of these types are JSON strings; a `distinct` and a `count` are numbers. The writer is `dtjson.cho`, used when the plan has a date key or a date minimum or maximum; the writer of every other plan is the code it was.
14. **`--explain`** names the types `date`, `timestamp`, `datetime` and lists, in `refusals.possible`, `value.not-date` for a `:date`; `value.not-timestamp`, `value.timestamp-precision`, `value.timezone-unsupported` for a `:timestamp` or a `:datetime`, with `value.timestamp-range` and `value.timezone-missing` for the first and `value.timestamp-offset-present` for the second.
15. **A column called `:date`** (a name that is only the suffix) stays a column; `created\:date` and `'created:date'` are names, as `created\:int` is.
16. **The fast reading.** `parse` reads a well-formed cell by `fast_date` and `fast_time`, which add the digits' sign tests and know no reasons; any other cell is read again by the step-by-step reader that says why (the order of checks of section 5 is its order). A defect in the fast reading can only accept, and the differential tests and mutants hold it to the reference.
17. **A plan with a date type in an aggregate does not take the fast way of the hot loop** (`engine.fast_ok`, a function that runs once per plan): `query.parse_typed` is not touched. Its first form (a case for 22 and up in `parse_typed`) moved `dec group-sum cents` by 1.014 (instructions retired, three builds each side), the failure section 11 described; the dispatch moved to `agg.read_typed` and to `sorter.key_value`, which the cells of the gate do not use for decimals.

### 18.3 Measurements

Conditions of every run below: the Mac (Apple M4 Max, 16 cores, macOS 26.2, Python 3.14.5) at a **load average of 76 to 118**, shared with other jobs; `gram` (the Linux x86-64 box) was **down** (`Host is down`, then a timeout) for the whole of this work, so nothing here was run on it; the Linux x86-64 run is the pull request's CI. Compiler `e47d647`, the pin. Instruction counts (`/usr/bin/time -l`, "instructions retired") do not depend on the load; times do, and a time below is labelled.

| gate | result |
|---|---|
| **T0** (the gates, committed alone, before any code) | `test_datetime.py` and `datetime_ref.py` on `main`'s binary (`373d01a`): 25 of 35 fail and 5 error; the 5 that pass are the reference against the hand-written expectations, the day-number check in Python, the compatibility test and the two probe tests (which read `dtparse.cho` from the tree) |
| **G-ORACLE-GRAMMAR** | the reference (Python `datetime`/`calendar`, fixed offsets, no `zoneinfo`) against the tool: 2,550 edge cells (leap days of 20 years, month ends, offsets `+14:00`/`-12:00`/`+23:59`/`-23:59`, `24:00`, `:60`, years 1 and 9999 and the ends of the range at 14 offsets of hours and minutes, fractional digits 0 to 10, whitespace, lower case, a space for the `T`, basic/week/ordinal forms, named zones, multibyte digits, 400 cells with two faults at once for the order of the checks), each as each of the three types, one process per refused cell; 300 generated refused cells and 3,000 valid cells a type in the suite; **a sweep of 20,000 generated refused cells and 100,000 valid cells a type: all equal** (tag, `detail.reason`, `offset`, `digits`, `direction`, row, line, column, context, value, instant) |
| **G-ORACLE-SQL** | DuckDB 1.5.6 (`TIMESTAMPTZ` with the session in UTC, and `DATE`) and SQLite 3.53.4 (`strftime('%s')`, whole seconds): the instants of cells both accept equal the tool's, 2,000 cells a type in the suite, **30,000 a type in a sweep**; the reference agrees with DuckDB on 2,521 cells in a first check; the two engines are not an oracle for refusals (section 3) |
| **G-ALGORITHM** | the tool's own `days_from_civil` and its inverse, built into a probe from `dtparse.cho`, against Python's calendar for **all 3,652,059 days of 0001..9999: 0 mismatches**; the control with the era one day short fails (`Algorithm` in `test_datetime.py`) |
| **G-ROUND** | `parse(print(v)) = v` and `print` of it again is the same text, 100,000 valid cells a type: all |
| **G-DIFF** | 40 random tables x 8 plans x one and four threads in the suite (80 tables in a run of 80 tables): conditions, `in`, order keys, group keys, `min`, `max`, `distinct`, `--top`, all equal to the Python evaluation |
| **G-PAR** | `--threads` 2, 3, 4, 8, 16, 64 x `--chunk-bytes` 1, 7, 64, 1000 over files of 800 rows with refusals in the middle: the same bytes (a repair's own `--threads` aside), the first refusal in file order; standard input and the file say the same |
| **G-RULES** | the seven tags are in `introspect` with their exit (8) and repairability, each has a fixture in `test_rules.py` that reaches it, whose hint and repair kind are the summary's |
| **G-COMPAT** | the md5 of the **166 outputs** of `scripts/corpus.py` on `373d01a`'s binary and on the branch's: **identical** (`diff` of the two lists empty); near strings (`created:date`, a column called `date`, `x:timestamp`, `:date`, quoted and escaped) in the suite; `--report types` unchanged (`report types` is a cell of the speed gate) |
| **G-FUZZ** | 1,500 random and mutated cells a run (the alphabet of the grammar plus multibyte, `[`, a NUL, a newline): no trap, the reference's tag and reason, every offered repair understood (exit 0 or 8, never an argument error); cells of 40 to 5,000 bytes; a trap this found (an `int` overflow in the fraction's accumulator on a fraction of 10 or more digits) is fixed and is a case |
| **G-MUT** | `scripts/datetime_mutants.py`, 165 mutants of `dtparse`, `dtfail`, `dtrepair`, `dtjson`, `expr`, `agg`, `sorter`, `engine`, `finish`, `explain`, `query`: run on the Mac in three shards, 169 in the first list: every one is killed except the ones proved equivalent in comments of the script (the step-by-step reader's fraction and range edges, the offset applied twice there, `in` without `kind == 2`, the stride of `group_plain`, `context_ok`, a sort key's `status >= 5`); mutants that survived a first run led to new edge cells (two faults at once, the ends of the range at offsets of minutes, year 0 with no offset, other delimiters), a bare suffix, a quoted name with a suffix and a JSON-lines plan of one date key, and were killed on a second run. The final list is 161. `gram` could not run them, the Mac did (load 76 to 118, about 4 hours) |
| **G-SPEED** | `scripts/gate_regress.py --counter`, instructions retired, three builds each side (`373d01a`, and the branch's code), 21 plans x 1 and 4 threads = **42 cells: every ratio between 0.999 and 1.003, `G6: PASS`** (bound 1.01); `lexs_read_file` and `lexs_read_stream` are 18,064 and 17,072 bytes in all six builds; the binary grows from 1,225,016 to 1,277,944 bytes. The first form of the change (a case for 22 and up in `query.parse_typed`) failed it: `dec group-sum cents` at 1.014 (two cells over; the other 40 within 1.005); a second form (a test in `engine.fast_ok`) moved every one-thread read by 30 instructions a row (1.011 to 1.024 on 21 of 42 cells, every one-thread cell); the third, in 18.2 (17), is the one built |
| **G-NEW-SPEED** | see below |
| **G-GEN** | `scripts/site.py --check`, `seo_check.py`, `schemas.py --check`, `manifest.py --check` (the authority row is the same labels, `args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`; only the list of function names and the counts moved), `mcp.py --check`, `stream.py --check`, `publish_numeric.py --check`, every `*_mutants.py --check`: pass; the help examples run by `test_skill` |
| **CI** | green on the pushed head 7d51450 (ubuntu, the whole suite, the generated checks); the final head's is in the pull request |

**G-NEW-SPEED, the cost of a cell** (`scripts/bench_datetime.py`, 1,000,000 rows, one thread, 8 columns, each question asked with the column as text and as the type; **instructions retired a row above the text question**, which do not depend on the load; the time column of the script, taken at a load average of 95 to 118, is **not publishable** and is not quoted, several of its differences are negative):

| the typed read | instructions a row above the text read | the doc's estimate (section 11) |
|---|---:|---|
| `bytes:int >= 50000` (for scale) | 75 | |
| `price:dec(2) >= 500` | 285 | |
| `ts:timestamp` (`2024-03-05T01:02:03Z`) | **290** | 100 to 400 more than a tried `:int` condition: here 215 more |
| `ts2:timestamp` (`2024-03-05T01:02:03.250000+02:00`) | **475** | 400 more: here 400 more (the top of the band) |
| `w:datetime` (`2024-03-05 01:02:03`) | **255** | 180 more |
| `d:date` (`2024-03-05`) | **96** | "a `:date` cell less": 20 more than `:int` |
| `--order-by ts:timestamp --top 100` | 267 | |
| `--group d:date` (about 1,500 groups) | 874 | |
| `--agg max:price:dec(2)` (the fast way) | 304 | |
| `--agg max:ts:timestamp` | **1,145** | |

The estimate "three to eight times the `parse_dec` figure" was too high for the instruction count: a UTC timestamp costs what a `:dec(2)` does (290 and 285). **What is above the estimate is the aggregate**: a plan with a date type in an aggregate does not take `add_fast` (18.2, 17), so a `min` or a `max` of dates pays the general way of adding a row (about 850 instructions) that a decimal's does not. The first version of the reader (a loop over the shape, `two()` for every field) cost 391, 653, 187 and 355 for the first four rows; the sign-test form (`fast_time`) is the one measured above. A fast way for these aggregates, held to the same gate, is the follow-up this suggests.


### 18.4 What was not done

`gram` was down, so no run on Linux x86-64 beyond CI, no `perf`, no callgrind; the doc's G-ORACLE-SQL matrix (3,000 tables x `<`, `<=`, `=`, `in` x threads against DuckDB and SQLite) was not run: the engines are the oracle of the instant of the cells, and the conditions are held to the Python evaluation (G-DIFF); T2 (section 19) and T3.

## 19. T2 as built

**Status.** This section is what slice **T2** built: `@UNIT` on a group key. It was built in the order the maintainer's rule says: the gates first (`tests/conformance/datetime_ref.py` gained an independent reference of the bucket, `test_datetime_t2.py` and `scripts/datetime_bucket_mutants.py` are new), run on `main`'s binary where they fail, and pushed alone; the code after. T3 (`--query`, joins on the types, `--report dates`, the MCP examples) is not built, and `@week` is not built either (reading 1). Section 7 is the design; where T2 shows a sentence of sections 0 to 18 to be false, the sentence is corrected in the section that made it (sections 0, 4.4, 7, 10, 13, 18), and nothing else of them changed.

### 19.1 What is built

`--group` takes `NAME:TYPE@UNIT` for the three types: `--group ts:timestamp@day`, `--group d:date@month`, `--group w:datetime@hour`. The units are `hour`, `day`, `month`, `year` for a `:timestamp` and a `:datetime`, and `month`, `year` for a `:date`. The key of a row is the **first value of the bucket its value falls in, a value of the same type**, written by the output rule of section 8: `2026-01-01T00:00:00Z` for the day of `2026-01-02T01:00:00+02:00` (the bucket is a function of the instant: the UTC day), `2026-01-01` for `d:date@month` and `@year` of `2026-01-15`, `2026-01-02T00:00:00` for the day of a datetime. It works with `--threads` (the same bytes at any thread count), standard input and a file, `--input-format jsonl`, a joined row (`--join` with `--group` on a column of either file), csv, json and jsonl output, `--agg count,min,max,distinct` on other columns and on the same one, `--where` on the same column, `--sort`, `--top`, `--limit`, `--from`, and `--explain`.

The code is `dtbucket.cho` (225 lines: the unit's text and its refusal, the first value of a bucket, `read`, the `--agg` check) and hooks of a few lines in `flags.cho` (`split_types` reads the unit of a `--group` item), `query.cho` (the plan), `agg.cho` (`read_typed`, `add`), `dtfail.cho` (the repair, the hint of a bucket in `--agg`), `explain.cho`, `qtext.cho` (the hint of a typed `group by`), `spec.cho` (the help) and `table.cho` (one line). The plan holds the unit above the type in the group column's entry: `query.gkinds` has `kind + 256 * unit`, and `query.gkind_at` answers the type alone, so everything that asks what type a key is (the conflict check, `--explain`, the printer, the repairs, the writers of JSON lines) is blind to the unit, and a plan without a unit holds the entries it always held. `agg.cho` is 2,167 lines (it was 2,169: a paragraph of its header comment is wrapped wider; the file was over 2,000 before this slice and is not raised), and no other file is over 2,000.

**The arithmetic.** A value is shifted to 0001-01-01T00:00:00 (a whole number of days before the epoch: 62,135,596,800,000,000 microseconds), where no operand is negative, so the integer division is a floor. An hour or a day is the value less its remainder; a month or a year is the civil date of the day (`civil_from_shifted`) with the day set to 1 or the month and the day set to 1, back through `days_from_civil`. A date is the same on days. Before 1970 the bucket is the one that contains the value (the day of -1 microsecond is 1969-12-31). **The ends of the range need no clamp:** every valid value is at or after 0001-01-01T00:00:00 (a `:timestamp` is range-checked after its offset is applied, section 6.1) and the first value of its bucket is at or before it and at or after the first instant of its year, so the key is a valid value of the type; the bucket of `0001-01-01T14:00:00+14:00` is `0001-01-01T00:00:00Z` and that of `9999-12-31T23:59:59.999999Z` is `9999-12-31T00:00:00Z`, `9999-12-01T...` and `9999-01-01T...`. Tests pin both ends for every unit of every type (`test_a_bucket_of_the_ends_of_the_range_is_inside_the_range`, and the golden cells).

### 19.2 The narrow readings

1. **`week` is not built** (open question 6, answered by its recommendation). `--group ts:timestamp@week` is refused before a row is read, with the rule `args.bad-value` (exit 2), `detail.reason` `week-not-built`, and a hint that says `@week is not built` and names the units that are. The design does not decide the tag; this is the choice, with the others of reading 2.
2. **The tags: no new tag.** A unit of a `--group` item that is refused is `args.bad-value` (the same rule, in the same flag, as `--group 'price:dec(19)'`): `detail` is `flags` (`--group`), `item` (the whole item as written), `unit` (the text after the `@`) and `reason`, which is `week-not-built`, `finer-than-type` (`@hour` or `@day` of a `:date`: its cells have no hour, and its key is the day) or `unknown-unit` (`minute`, `second`, `DAY`, `Day`, `days`, a number, a space, an empty word, `day@month`). The repair is none (which unit was meant is not known; the hint lists them). `where.syntax` is not used (it is the rule of expressions). A unit in `--agg` (`min:ts:timestamp@day`, with or without a function, and with any word after the `@`) is `agg.bad-spec` (exit 2) with `detail.item` and `detail.agg` as for any bad item, and a hint that says a bucket belongs to a group key and gives the flag. No rule is added, so `introspect`, `docs/refusals.md` and the rule fixtures hold the same tags; the T2 tests are the fixtures of the new `detail.reason` values.
3. **Where the unit begins.** In a `--group` item the unit begins at the first unescaped `@` that has a date type right before it (`:date`, `:timestamp` or `:datetime`, its colon not escaped, with a name before it), and runs to the end of the item. So `a@b:timestamp@day` is the column `a@b` by day; `ts:timestamp@day@month` is the unit `day@month`, refused as `unknown-unit`; `ts@day` is a column called `ts@day` (no type before the `@`); `n:int@day`, `n:dec(2)@day` and `n:float@day` are columns called that (an integer, a decimal or a float is not bucketed); `:timestamp@day` (no name) is a column called that; and a header that really ends in `:timestamp@day` is written with its colon escaped, `--group 'x\:timestamp@day'`, as one that ends in `:int` is. The last is the one change of meaning of a plan that was valid: a column whose header ends in `:date@month`, `:timestamp@day` or the like, written unescaped in `--group`, is now a bucket (it was a name). `--agg` reads the same `@` for its refusal only (`min:ts:timestamp@day`), except that `@` and one or two digits after a date type is the scale of a mean, as in T1 (`mean:ts:timestamp@2` is the refusal of a mean of instants, with its own hint), and `min:ts@day` is a column called `ts@day` as it was.
4. **Only `--group` reads a unit.** `--order-by ts:timestamp@day` is a column name that is not there (`column.unknown`, as it was: `--order-by` is not a group key), `--where "ts:timestamp@day > 'x'"` is as it was, and `--select`, `--join`/`--on` read none. A `--query` has no spelling of a period (open question 7): `group by x::dec(2)` is `query.unsupported`, `cast-in-group`, and its hint now names the flag (`a period of a date, a timestamp or a datetime is --group ts:timestamp@day (or @hour, @month, @year)`). `cast(ts as timestamp)` is still `query.syntax` (T3). The hint is the one refusal of a plan that was refused on `main` whose bytes change (only its hint): the compat tests of the earlier slices hold it as they hold the hints that J3b rewrote (`rename_b1.j3b_hint_moved` also answers for `cast-in-group`, so they compare the status and the rule; `query_join_golden.py` leaves the case out and `query_join_main_golden.json` loses its line, `refusal 90`), and `test_datetime_t2.py` pins the new text; `query_ref.py`, the current reference, has it.
5. **The same column.** The conflict check (`column.type-conflict`) compares the base type: `--where ts:timestamp >= ... --group ts:timestamp@day --agg min:ts:timestamp` is one column read as one type, and `ts:timestamp@day` beside `ts:date`, `ts:datetime`, `ts:int` or `ts:dec(2)` is a conflict, with the base types in `detail.types`. **Two keys on one column are two independent keys**, as two keys on one column are today (`--group ts,ts` prints `ts,ts,count`): `ts:timestamp@month,ts:timestamp@day` is the pair (month, day), `ts:timestamp@day,ts:timestamp` the pair (day, instant), `ts:timestamp@day,ts` the pair (day, text), and the same item twice repeats the key. They are well defined (each key is a function of the cell) and sort field by field; the labels are the column's name as before. Nothing is refused for it.
6. **The refusal of a cell is the plain key's.** A cell that is not of the type is refused in a bucketed key with the same rule, `detail.reason`, `offset`, `digits`, `direction`, row, line, column and value as in the plain key, and `context` `group`, at the first row in file order at every `--threads`; the order of the checks of section 5 is unchanged (the range of an instant is checked before it is bucketed: `0001-01-01T00:00:00+01:00` is `value.timestamp-range` with `@year`).
7. **The repair keeps the unit when the new type has it.** `--group ts:timestamp@day` over a cell with no offset offers the same invocation with `ts:datetime@day`; `d:timestamp@month` over `2026-01-02` offers `d:date@month`; `d:timestamp@day` over it offers none (a `:date` has no day: the reason says so), and an invocation whose other items would be rewritten and one of them cannot is not offered either. The invocations of the repair tests are run, and accepted.
8. **`--explain`** prints `"bucket":"day"` after `"type"` for a key that has a unit, and nothing for a key that has none: the bytes of the explain of every plan without an `@` are the bytes they were (tested against `main`'s binary with `MAIN_TABLE`, and the 166 plans of `scripts/corpus.py` by their md5, below).
9. **`--max-groups` and the other bounds count buckets**, not instants: ten instants in two days are two groups of `--group ts:timestamp@day`, and `limit.too-many-groups` fires on the buckets.
10. **JSON lines out:** a bucketed key is a JSON string, as a key of a date type is (reading 13 of section 18); nothing changed in the writers.
11. **Floor, not truncation, and the year 0001.** Reading of the ends of the range above. A key can be `0001-01-01T00:00:00Z` (a first bucket) and `9999-12-31T00:00:00Z`; no bucket starts before year 1, because no value does.

### 19.3 Measurements

**Conditions of every run below.** This session's machine: Intel Xeon @ 2.10 GHz, 4 cores, 15 GiB, Linux 6.18.44 x86-64 (a virtual machine, shared; load average about 2 when the instruction counts were taken, up to 4 while tests ran), Python 3.13.16, valgrind 3.22.0, DuckDB 1.5.6 (the Python module) and SQLite of Python's `sqlite3`, compiler `e47d647` (the pin). **No clock time is quoted and none is claimed**: a count of instructions by `valgrind --tool=callgrind` does not depend on the load; a time does, and the machine has no instruction counter (`perf stat` is not available here), so the gate of section 12 was run as `scripts/join_callgrind.py`, as the join slices ran it.

| gate | result |
|---|---|
| **the gates before the code** | `test_datetime_t2.py` has 50 tests; on `main`'s binary (`e5fd5e2`, built in a worktree of its own) **44 fail** and 6 pass (the reference against its hand-written buckets, the units by type, a fuzz of group items that must not trap, `--explain` without a bucket, `--order-by` with an `@`, and the bytes of 24 plans against `main`'s: the tests of what does not change). The failures are the right ones: a bucketed `--group` is `column.unknown` (exit 3, the word with the `@` is read as a column name), the hints and the docs do not say the bucket, and the repair is not offered. No test errors on an import; the first commit of the pull request (`b0542e7`, the gates) was pushed alone. |
| **G-ORACLE-GRAMMAR (bucket)** | the reference (`datetime.replace`, fixed offsets, no `zoneinfo`) against the tool: 56 hand-written buckets (offsets of +14:00 to -12:00 crossing the day, the month and the year, 1900, 2000, 2100 and 2400, 1969-12-31 and -1 microsecond, 0001 and 9999 at both ends, a fraction of 1 to 6 digits); the instants before, at and after each of 141 boundaries written in twelve offsets (11,390 timestamp cells, 1,714 datetimes and 321 dates with the seed of the first count) and the edge cells of section 18 (every valid one), each for every unit of every type; 1,500 random valid cells a type and unit; the refusals of 60 refused cells a type in a bucketed key against the same cell in the plain key: all equal |
| **G-ROUND** | `parse(print(key)) = key` and the bucket of a key is the key, for every key of every unit and type over 2,300 cells each; the printed key fed to `--where c:type >= KEY and c:type < NEXT` selects exactly the rows of its bucket (count equal), and `c:type = KEY` the rows whose value is the key |
| **G-DIFF** | 40 random tables x 8 plans (one to three keys, with and without a unit, text keys, `min`, `max`, `distinct`, `count`, `--where` on a grouped column, `--sort`) x one and four threads x csv, json, jsonl: all equal to the Python evaluation |
| **G-PAR** | `--threads` 2, 3, 4, 8, 16, 64 x `--chunk-bytes` 1, 7, 64, 1000 over 800 rows, six bucketed plans, then the file against standard input; the first refusal in file order (row 301, 701) at every cell of the matrix: the same bytes |
| **G-ORACLE-SQL** | DuckDB 1.5.6 (`date_trunc` of `TIMESTAMPTZ` with the session in UTC, of `TIMESTAMP` and of `DATE`: 3,000 cells a type, every unit) and SQLite (`strftime`, whole seconds, 3,000 cells): the key of every valid cell equal. Neither is an oracle for refusals (section 3). CI installs `duckdb` and `pytz` for it. **It ran here** (the Python module, DuckDB 1.5.6, `PYTHONPATH` to a local install) and in CI; it is skipped when the module is missing |
| **G-FUZZ** | 600 random group items made of the pieces of the grammar and of the near strings (`@`, `@@`, `\`, `\:`, `#2`, a quote, a multibyte byte, `@week`, `@DAY`): no trap, every refusal has a listed rule and a hint; odd cells (empty, 3,000 fraction digits, multibyte) and a 5,000-byte unit: no trap |
| **G-COMPAT** | the md5 of the **166 outputs** of `scripts/corpus.py` (`fccb01b66dd7518d74611219a6c5aa29`) on `main`'s binary (`e5fd5e2`) and on the branch's: **identical** (`diff` of the two lists empty, md5 `fccb01b66dd7518d74611219a6c5aa29` for both; `main`'s list recomputed first); `scripts/query_corpus.py` and `scripts/explain_corpus.py`: `query_corpus.py --require f1` (41 plans, 166 runs, flags against query) 0 different and `explain_corpus.py --require f1` (166 runs) 0 different, on the branch; these two scripts compare the flags with the query, not the branch with `main`, so they were not run on `main`'s binary; the explain, the answer and the refusal of 24 plans without an `@` (near strings included) at one and four threads and with `--explain`, byte for byte against `main`'s |
| **G-MUT** | `scripts/datetime_bucket_mutants.py`, 69 mutants (the arithmetic of the bucket, the UTC day, the unit's reading and refusals, the key, the conflict check, `--agg`, `--order-by`, the repair, `--explain`, the help and the hint), run on this machine in four shards (`MUT_SHARD=i/4`, one worktree each, load average about 4): **67 of 70 killed in the full run, 3 survived**: `a week is a day` is equivalent (proved in the script's docstring: `unit_class` refuses the word `week` before `unit_code` is asked, so no run reaches the changed answer); `a repair with a blocked item is offered` and `a repair rewrites an item with a unit of another type` had no test, tests were added (`Repairs`) and **both are killed** on a second run of those two. So 69 killed and 1 proved equivalent. The first attempt at the full run was interrupted by a restart after about four mutants a shard and is not counted |
| **G-SPEED** | `scripts/join_callgrind.py --old <main's binary> --new <the branch's> --all` (callgrind, the cost of a row from the difference of a 20,000-row and a 60,000-row prefix, so the start-up cancels; the numeric benchmark file; one thread): 38 cells (the non-join cells of the speed gate, the join cells of J1 to J5): **the ratio of the per-row instructions of every cell is between 0.9999989 and 1.0000000** (the worst is 1.0000000; 31 cells are equal to the hundredth of an instruction, the other 7 are cheaper on the branch by less than one part in a million), bound 1.01: PASS. The start-up differs (the help text and the code are larger: `main`'s run totals are 2.0 to 4.3 million instructions below the branch's, of 118 to 410 million) and is not a per-row cost. `join_callgrind.py` has no cell of a date type, so a plan with a date type and no `@` is not in this gate; measured separately, with the same method and file as the table below (`main`'s binary against the branch's): **a cell of a date type read by `read_typed`, which a group key, `min`, `max` and `distinct` do, costs 6.0 instructions more on the branch** (a `ts:timestamp` key 2,955.8 to 2,961.8 a row, `d:date` 2,697.5 to 2,703.5, `w:datetime` 2,854.4 to 2,860.4, `max:ts:timestamp` 2,611.8 to 2,617.8, `distinct:ts:timestamp` 2,941.2 to 2,947.2, `min:d:date,max:w:datetime` 2,833.4 to 2,845.4 for the two cells), 0.2 percent of the row: under the bound and not zero, and it is the one comparison (`kind >= 256`) that `read_typed` makes before it reads a date type. A condition (`ts:timestamp >= ...` 2,057.9 both, `d:date =` 1,699.6 both), `--order-by ts:timestamp --top 100` (1,798.1 both) and the text paths (a text key 1,551.1, a text filter 1,584.1) cost the same instructions on both |
| **G-NEW-SPEED** | below, reported with no pass line |
| **G-GEN** | `scripts/manifest.py`, `mcp.py`, `site.py` run and their `--check` forms; the authority row of the manifest is the same labels (`args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`; only the list of function names moved); the help example is run by the skill's tests |
| **CI** | green (the job `gate`, ubuntu, the whole suite with DuckDB installed, the generated checks and the mutants' `--check`) on `30fdc38`, the head that first had the code; the result for the head that added the two repair tests and this text is in the pull request |

**G-NEW-SPEED, the cost of a bucketed key** (callgrind, instructions a row; the same file of 60,000 rows with a `:timestamp` column of 1,500 distinct instants in 700 days and five offsets, a `:date` of 600 days, a `:datetime` of 1,500, a text column of five values; the cost of a row is the difference of the counts at 20,000 and 60,000 rows, each count the minimum of three runs, `--max-groups 1000000`, one thread; a bucketed key has fewer groups than the plain one, which is part of what is measured). **Reported, with no pass line.**

| the key | instructions a row | above the same key without the unit |
|---|---:|---:|
| `ts:timestamp` | 2,961.8 | |
| `ts:timestamp@hour` | 3,003.0 | 41.2 |
| `ts:timestamp@day` | 3,003.0 | 41.2 |
| `ts:timestamp@month` | 3,142.3 | 180.5 |
| `ts:timestamp@year` | 3,106.8 | 145.0 |
| `d:date` | 2,703.5 | |
| `d:date@month` | 2,858.7 | 155.2 |
| `d:date@year` | 2,831.3 | 127.8 |
| `w:datetime` | 2,860.4 | |
| `w:datetime@day` | 2,901.5 | 41.1 |
| `k` (text, for scale) | 1,551.1 | |
| `ts:timestamp,k` | 3,201.3 | |
| `ts:timestamp@day,k` | 3,229.7 | 28.4 |

A month or a year pays the calendar (`civil_from_shifted` and `days_from_civil`, about 100 to 140 instructions); an hour or a day is one division. The bucket adds 1.4 percent (an hour or a day) to 6.1 percent (a month) of the row cost of a `:timestamp` key in this plan, and 4.7 to 5.7 percent to that of a `:date` key.

### 19.4 What was not done and not run

* **`@week`** (reading 1), a period in `--query`, a period in `--order-by` and a period of a join key (`--on`): not built, not in T3 either. (`--report dates`, the query's types and the types of a join key were T3, section 20.) A timezone-aware day (a fixed-offset shift) is a non-goal (section 7).
* **No clock time** was measured or is claimed, here or anywhere in this section; `perf` is not available on this machine, so G-SPEED is callgrind's (instructions executed), which is exact but is not instructions retired by the CPU.
* **`gram` and the Mac** were not used; the Linux x86-64 run that counts is the pull request's CI.
* **The matrix of section 12's G-ORACLE-SQL** (3,000 tables x four operators x threads) was not run for the bucket; the SQL engines are the oracle of the key of valid cells (above) and the conditions are held to the Python evaluation (G-DIFF).
* **G-ALGORITHM** (the day number over every day) is T1's and unchanged; the bucket reuses `days_from_civil` and its inverse, and the bucket's own tests cover every month end and leap day of the edge cells but are not an exhaustive sweep of 3.6 million days. A sweep of every day of 0001..9999 as `@month` and `@year` against the reference was not run.
* **`--threads` above 64, and files of more than 800 rows in the thread matrix**, were not run for the bucket; the group machinery is shared with every typed key, and its merge is on bytes.

## 20. T3 as built

**Status.** This section is what slice **T3** built, "the surface around the types": `--query` with the three types, the types as join keys, `--report dates`, and the JSON-lines output of all of them. It was built gates first, as T0 and T2 were: `tests/conformance/test_datetime_t3.py` (and the references `join_rows` and `report_row` in `datetime_ref.py`) were pushed alone, failing on `main`'s binary, before any code. Section 9's sentence that `--report types` would change its bytes is corrected in place (reading 12): it does not.

### 20.1 What is built

* **`--query` with `date`, `datetime` and `timestamp`** (`qparse.cho` `type_code` 22, 23, 24; `qtext.cho` the expected text and the type names; `qtrans.cho` writes `:date`, `:timestamp`, `:datetime`). `cast(ts as timestamp)` and `ts::date` work in a condition, a sort (`order by ts::timestamp desc`) and an aggregate (`min`, `max`, `count(distinct ...)`; `sum` and `mean` are `agg.bad-spec` as in the flags), and are translated to the flag form: the query and the flags give the same bytes. A literal that is not of the type is `query.syntax` at the literal with `detail.column`, `detail.type` and `detail.value`; `contains` on a typed column and a cast in the select list are refused as they are for the numbers. A typed `group by` stays `query.unsupported` (`cast-in-group`, open question 7) and its hint names `--group ts:timestamp@day`.
* **The types as join keys** (`jnflags.cho` `type_of`, `jnkey.cho`, `jnfail.cho`, `dtjoin.cho`): `--on k=kk:timestamp`, `:date`, `:datetime`, the shared-name form (`--on k:timestamp`), several pairs of different types. The key of a typed pair is the value (days or microseconds), 8 bytes, as for the numbers, so `2026-01-02T01:00:00+02:00` and `2026-01-01T23:00:00Z` are one key. Inner, left, semi and anti joins, standard input, JSON-lines sides, `--join-where` and `--where` on the types beside a typed key, and a group with a bucket of a joined column all go through the code they went through. A key cell that is not of the type is refused with the rule of the date types (`value.not-date`, `value.not-timestamp`, `value.timestamp-precision`, `value.timestamp-range`, `value.timezone-missing`, `value.timestamp-offset-present`, `value.timezone-unsupported`) with `context` `join`, `input` (`probe` or `build`), `pair` (from 1), `column`, `row`, `line`, `value`, and the reason, offset, digits or direction of the type. `--explain` names the pair's type (`date`, `timestamp`, `datetime`).
* **`--report dates`** (`reportdt.cho`, `reportflags.cho`; `report.cho` keeps `--report types` and dispatches): one row per column, the columns `name, cells, empty, date, timestamp, datetime, other, widest, suggest, first_empty_row, first_empty_line, first_not_date_row, first_not_date_line, first_not_timestamp_row, first_not_timestamp_line, first_not_datetime_row, first_not_datetime_line, first_other_row, first_other_line, first_other_cell, first_other_cell_truncated`, as `--report types` has its own; it takes `--select`, `--where`, `--limit`, `--from`, `--format json|csv`, `--threads` and standard input as `--report types` does, and the same refusals for what it does not go with.
* **JSON lines out** is T1's `dtjson.cho` (a key, a `min` and a `max` of the types are JSON strings, a count and a `distinct` are numbers); T3 adds no code to it and pins it for the paths T3 opened (a query, a join with a typed key group, rows of a join). `--report dates --format jsonl` is `args.conflict`, as `--report types` is.
* **The help** (`--report`, `--on`, `--query`), the MCP definition, the README and `docs/filter.md`, `query.md`, `joins.md`, `refusals.md`, `reference.md`, `architecture.md`, `backlog.md` say what exists.

### 20.2 The narrow readings

1. **`--report dates` is a new report, not columns appended to `--report types`** (open question 8, answered by its recommendation). The design text of section 9 said the date columns would be appended to the one report and that its bytes would change; the open question recommended `--report dates` "to keep the existing report byte-identical (risk 2)". T3 builds the second: `--report types` is the bytes it was (tested against `main`'s binary), and risk 2 does not arise. Section 9 and risk 2 are corrected in place.
2. **The columns of `--report dates`** are those listed in 20.1, with the types report's own shape: a name, nine counters and a suggestion, then five pairs of row and line (the first empty cell, the first cell that `:date` would refuse, `:timestamp`, `:datetime`, and the first `other` cell) and the first 64 bytes of that last cell with whether it was longer. The design named `first_not_date_row` "and the other `first_not_*` columns" and nothing else; the `widest` counter, the `other` class and the pair of the first `other` cell are the types report's own columns carried over (a reading). `dec_max_scale`, `int_digits_max` and the `first_not_float_*` pair have no meaning here and are not there.
3. **A cell is exactly one class.** `empty` (no bytes, or a quoted pair of quotes), `date`, `timestamp`, `datetime`, or `other`: the three grammars exclude one another (ten bytes; an offset; none), so the counts add up to `cells`. A cell that fails a date grammar for any reason (`2026-02-30`, `24:00:00`, a seventh fractional digit, a zone name) is `other`; the report does not say why (the refusal of `--agg min:c:timestamp` does). A date of ten bytes is tried as a date only, and a cell of 19 bytes or more as a timestamp and then a datetime (a reading of "classified once, by the length first").
4. **`suggest` is `:date`, `:timestamp` or `:datetime` when every non-empty cell is of that one class, else `none`.** A column that mixes dates and timestamps has no one declaration, and a column with no non-empty cell has none (the rule of N6). The report applies nothing.
5. **The row and the line of a first cell are those of the file**, as for `--report types`: with `--where` the rows that are kept are counted, and a first cell is placed by its place in the file. The thread merge takes the earlier range's first cell and offsets the later range's by the records and lines before it, as the types report's does (G-PAR: 2 to 64 threads x chunks of 1 to 1,000 bytes, and standard input, give the sequential bytes).
6. **The state of a dates report is 28 integers a column, 224 bytes,** counted in `--max-state-bytes` (the types report's is 248). The bound's refusal is `limit.state-too-large` with its own message (`the date report keeps 224 bytes ...`); the abort code is 44, beside the types report's 43.
7. **`--report WORD` takes `types` and `dates`.** Any other word is `args.bad-value`, and the message now says `--report is types or dates` (it said `types: the only report there is`). The flags `--report dates` does not go with are those of `--report types`, with the same rules (`args.conflict`) and messages that name `dates`; `--format jsonl`, a grouping, a sort, `--top`, `--rename`, `--rows` and a join are refused. The refusals of `--report types` are word for word what they were, and `--explain` calls a dates report `date report`.
8. **A type at the end of a join pair** (`--on k=kk:timestamp`) is one of `date`, `datetime` and `timestamp`, in lower case, as the three numeric types are; a word that is not a type (`Date`, `time`), a type after the probe column (`k:date=kk`), two types (`kk:date:date`) and an empty type are `args.bad-value`. There is **no cross-type join**: a pair has one type, and a `:date` cell in a `:timestamp` pair is `value.not-timestamp` (reason `date-only`), the other way round `value.not-date` (reason `time-part`). The text of the refusal of an unknown type now lists the six types.
9. **The key of a typed pair is the value** (days or microseconds, plus 2^63, big endian: 8 bytes, as for the numbers), so two spellings of one instant are one key; for a `:datetime` the wall-clock digits are the key and for a `:date` the day; a `:timestamp` pair matches `2026-01-02T01:00:00+02:00` with `2026-01-01 23:00:00.000+00:00` and `2026-01-01T23:00:00z`. Nothing about the rows changes: a joined row is the input bytes of both rows. A bucket on a join key (`--on k=kk:timestamp@day`) is not built.
10. **The refusal of a key cell of a date type** has the rule of the type (`value.not-date` and the six others of section 10), `detail.context` `join`, `detail.input` (`probe` or `build`), `detail.pair` (from 1), `detail.column` (the name its own header gives it), `row`, `line` (of its own file), `value` and `value_truncated`, and the `reason`, `offset`, `digits` or `direction` of the type. **There is no repair** (the type is written once at the end of the pair; reading the key as text is the user's choice), where the numeric key refusals offer the guard of an empty cell. The first cell in file order is refused at every `--threads`; the build file is read first, so a bad build cell is named before a bad probe cell.
11. **The status of a refused date key does not fit the answer of a read**, which holds the status in four bits (4 to 13 for the numbers); a date status is `14 + reason + 64 * the byte of the fault`. The read notes 15 and the refusal reads the cell again (`dtjoin.status_of`, quotes undone) to have it. Not visible except as a defect it would be (a quoted key cell is a test).
12. **The query's types** are the lower-case words `date`, `datetime` and `timestamp` after `::` and `as`, read as `int` is; the translator writes `:date`, `:datetime`, `:timestamp`. The `expected` text of a type word is `a type: int, dec(N), float, date, datetime or timestamp` (the bytes of the refusal of `cast(b as integer)` and the like change; the older goldens hold them as the reference of slice B1 moved them: `query_ref.py` has the new text, the frozen references of A0 to A2 have the old, and the J3b golden leaves those three refusals out). A literal that is not of the type is `query.syntax` at the literal (the repair of the flags, the midnight rewriting of a date against a timestamp, is not offered in a query), with `column`, `type` and `value`.
13. **JSON lines out** needed no code (reading 13 of section 18): the paths T3 opened (a query, a join with a typed key and a group, rows of a join) write what T1's writer writes, and are pinned. `--report dates --format jsonl` is `args.conflict`.
14. **The help.** `--report` lists `dates` and its columns, `--on` the three types and that instants in two offsets are equal, `--query` the six types; `--explain` of a join names the pair's type.
15. **Older tests changed on purpose:** `test_datetime.py` pinned T1's refusal of a date type in a join key (`args.bad-value`) and of `cast(d as date)` (`query.syntax`); it now pins their replacement. The mutants of `report`, `explain`, `jsonl_out`, `join` and `rename` named code that moved (`reportflags.cho`, `jnkey.read`, the abort code of the report) and name the new sites. Three refusals of plans that `main` refused change their bytes, and nothing else of an existing output: the message and hint of an unknown type in `--on` (it lists the six types), the `expected` of an unknown type word in `--query` (reading 12), and the message of an unknown `--report` word (reading 7).

### 20.3 Measurements

Conditions of every number: this session's machine, Intel Xeon @ 2.10 GHz, 4 cores, Linux 6.18 x86-64 (a virtual machine, shared; the mutants ran four at a time), Python 3.13.16, valgrind 3.22.0, DuckDB 1.5.6 (the Python module). **No clock time is quoted or claimed.** Instruction counts are callgrind's (executed instructions, exact, independent of the load); `perf` is not available.

| gate | result |
|---|---|
| **the gates before the code** | `test_datetime_t3.py` (39 tests when it was pushed alone, commit `5fc0a69`): on `main`'s binary (`2c35b0f`, built in a worktree of its own) **35 fail and 4 pass** (the generator calibrated over integers, the bytes of `--report types`, the explain and the fuzz of words that must not trap are what does not change); the failures are the right ones: a typed `--on` pair is `args.bad-value`, `cast(ts as timestamp)` is `query.syntax`, `--report dates` is `args.bad-value`, the docs and the help do not say it. The one test T1 pinned (`test_a_date_type_in_a_join_key_is_refused_with_a_tag`) was changed in the same commit and fails there too. Tests were added or fixed after the code, each for a defect or a surviving mutant it found: a quoted key cell, the bound of a dates report, the join-test fixture (a column collision of its own) |
| **G-CORPUS-EQ** | 60 random plans (a condition, a sort, an aggregate over a timestamp, a date and a datetime column, `cast` and `::`), flags against query, csv and json, one and four threads: the same bytes; and `--explain` the same bytes; the same generator over integer columns is the control (it refuses nothing); the refusals of a literal, a type word, `contains`, a cast in the select list and a typed `group by` |
| **G-JOIN** | for each of the three types, four tables of 14 probe rows and 10 build rows (the edge cells of the doc, spelled in other offsets), inner, left, semi, anti, `k=kk:T` and the shared-name form: the rows equal `datetime_ref.join_rows` (a match on the value of the keys, never of the text); 25 refused cells a type in the probe and in the build, each with the reference's rule, reason, offset, digits, direction, row, line, column and value; DuckDB 1.5.6 (`TIMESTAMPTZ` with the session in UTC, `TIMESTAMP`, `DATE`): the matches of 30 x 20 rows equal; threads 2 to 16, standard input, JSON-lines sides, several pairs, `--join-where` and `--where` beside a typed key, a group with a bucket of a joined column, quoted key cells |
| **`--report dates`** | the reference `datetime_ref.report_row` against eight columns of 120 cells (all of one class, mixed, with empties, a long `other` cell): json and csv equal; `--select`, `--where` (the first cells placed by the file's rows), `--limit`, `--from`; threads 2, 3, 4, 8, 16, 64 x chunks 1, 7, 64, 1000 and standard input: the sequential bytes; the bounds; 150 fuzzed cells |
| **G-FUZZ** | 400 random `--on` lists, 200 `--report` words and 200 `--query` conditions made of the pieces of the grammar: no trap, every refusal has a listed rule |
| **G-COMPAT** | the md5 of the **166 outputs** of `scripts/corpus.py` on `main`'s binary and on the branch's: **identical** (`fccb01b66dd7518d74611219a6c5aa29`); `--report types` on a file with date columns, at 1 and 4 threads and as csv, is `main`'s bytes (the `Report` test with `MAIN_TABLE` set) |
| **G-SPEED** | `scripts/join_callgrind.py --old <main's binary> --new <the branch's> --all` (callgrind, the cost of a row from a 20,000-row and a 60,000-row prefix, so the start-up cancels), 38 cells: **PASS, bound 1.01; the worst ratios are 1.003501 (`build row, :int pair`, +6.0 instructions a row: the `jnkey.read` that decides between the number and the date readers), 1.001387 (`report types`, 5,767.2 to 5,775.2 instructions a row, +8.0: the one decision of the engine between the two reports), 1.002147 (`join inner, :int pair`, +6.0), 1.001958 (`join left, :int pair`, +6.0)**, and every other cell is within one part in a million of 1.000000. `--report dates` over a five-column file of 60,000 rows (one thread) costs 3,382.2 instructions a row (reported, no pass line; `--report types` over it was measured before the last change to the engine at 4,149.6, and is not compared here) |
| **G-MUT** | `scripts/datetime_surface_mutants.py`, 45 mutants, four shards, run on this machine: 43 killed and 2 survived on the first full run; one of the two had no test (the bound of a dates report on a file with no row) and a test was added, **it is killed**; the other (`dtjoin.status_of` reading a quoted key cell with its quotes) is **proved equivalent** in the script's docstring (no position of the grammar accepts a quote). So 44 killed and 1 equivalent. The mutants of `report`, `explain`, `jsonl_out`, `join` and `rename` whose sites moved were re-pointed at the new sites; the two that named `engine.cho` were run and are killed |
| **G-GEN** | `manifest.py`, `mcp.py`, `site.py` run, and their `--check` forms, `schemas.py --check`, `seo_check.py`, every `*_mutants.py --check`: pass; the authority row of the manifest is the same labels (`args, conc, dir_read, err_write, file_read, fs_read(""), heap, io_read, io_write`; only the list of function names moved) |
| **CI** | @@CI@@ |

### 20.4 What was not done and not run

* **A period in `--query`, a period of a join key and `@week`**: not built (as section 19).
* **`--report types` for dates**, and the nanosecond, epoch and zone-name items of section 15: not built.
* **No clock time** was measured or is claimed; `perf` is not available here, so G-SPEED is callgrind's. `gram` and the Mac were not used.
* **A join on more than one core, a join with a period bucket on a key, and a `--report dates` over JSON lines** with a type on the flags: JSON lines in goes through the same classification (the cell is the string), and was tested for the join (both sides) and not for the report.
* **DuckDB or SQLite as an oracle of `--report dates` and of `--query`**: neither has the report; the query is held to the flag form, not to an SQL engine. SQLite was not used for the join.
* **The matrix of the doc's G-ORACLE-SQL** (3,000 tables x operators x threads) for the join: not run; the join is held to the Python join and to DuckDB on 30 x 20 rows a type.
* **A sweep of more than 800 rows in the thread matrix of the report**, and of threads above 64.
