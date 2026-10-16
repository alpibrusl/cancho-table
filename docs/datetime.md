# Dates and timestamps: `:date`, `:timestamp` and `:datetime`

> **Status: accepted (PR #76, all twelve questions of section 16 answered by their recommendations); slices T0 and T1 are built, T2 and T3 are not.** Sections 0 to 17 are the design as it was accepted, written in the future tense of a design: they say what is decided and why, with every statement about the program at `origin/main` `338a119` and every number either **measured** (with the command), **cited** (with its source and access date) or marked *estimate* or *choice*. **Section 18 says what T0 and T1 built, each narrow reading of a gap in the design, and what was measured (with its conditions).** What exists is also described, in the present tense, in [`docs/filter.md`](filter.md), [`docs/refusals.md`](refusals.md) and the README. It follows the rules of [`docs/numbers.md`](numbers.md), [`docs/or-and-columns.md`](or-and-columns.md) and [`docs/joins.md`](joins.md): decisions with their reasons, gates written before the code, and the existing paths held to instructions retired, bound 1.01.
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
| group by period | **`--group ts:timestamp@day`**, units `hour`, `day`, `month`, `year` (and `week`, Monday); the UTC day; the key is the first instant of the bucket, a value of the same type | `@` is the precedent of `mean:price:dec(2)@4`; a bucket is a property of a key, not a second reading, so it must not collide with `column.type-conflict` (section 7) |
| the missing offset | **refused** on `:timestamp` (`value.timezone-missing`), with the repair "read it as `:datetime`"; no `--assume-utc` flag | a flag would change what every unmarked name means (`docs/numbers.md` 5.1 rejected the same shape) |
| zone names | `Europe/Madrid` is refused (`value.timezone-unsupported`); no zone database | a zone database is data that changes; the tool is deterministic |
| first slice | **T1**: the three types in `--where`, `--group`, `--order-by`, `min`, `max`, `count`, `distinct`, the refusals | T2 is the bucket, T3 is JSON lines out, the query, joins, `--report types` |
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

`--query` already has `cast(c as type)` and `c::type` in a condition, a sort and an aggregate (`qparse.cho:224-253`; the words of `type_code` are `int`, `float`, `dec(N)`, `qparse.cho:197-219`, and `qtext.cho:56` is its `expected` text). T3 adds `date`, `datetime` and `timestamp` to `type_code` and to that text; the translator writes the same suffix. So `select id from 'e.csv' where ts::timestamp >= '2026-01-02T00:00:00Z' order by ts::timestamp` is the flag plan above. **A typed `group by` is `query.unsupported` today** for every type (measured: `group by p::dec(2)`; the hint is "a type is part of how a column is read in a condition, a sort or an aggregate; selecting a column returns its text"), so **the period bucket has no query form** and stays a flag; the refusal's hint names the flag. *The design does not add `date_trunc(...)`:* `table` has no functions and the query subset is a translator of the flags (`docs/query.md` section 1). (Open question 7.)

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

* **Units.** For `:date`: `month`, `year`, `week`. For `:timestamp` and `:datetime`: `hour`, `day`, `month`, `year`, `week`. A unit finer than the type (`:date@hour`) or `minute`/`second` (use the text or the type) is `agg.bad-spec`-like: `group.bad-bucket` is **not** a new tag; the existing `where.syntax`/`agg.bad-spec` shape is reused, a choice to avoid a tag for a spelling error (the exact tag is a T2 decision recorded then).
* **The day of a `:timestamp` is the UTC day** of the instant. A cell `2026-01-02T01:00:00+02:00` is in the bucket of 2026-01-01. *Reason:* the bucket must be a function of the **instant**, or two cells that are equal as values would fall into different groups (the property of 6.2). The user who wants local days has two true readings: `:datetime` (the wall clock, offset refused) when the file's convention is the zone he wants, or a fixed-offset shift, which is arithmetic and a non-goal here. For `:datetime` and `:date` the day is the written day. (Open question 6.)
* **The key of a bucket** is the **first value of the bucket**, a value of the same type, printed by the output rule: `2026-01-01T00:00:00Z` for `@day` of the example, `2026-01-01` for `d:date@month`, `2026-01-01` for `@year`. *Not* `2026-01` or `2026`: a key of the same type sorts, groups, joins and feeds back into `--where` unchanged, and a text key of another shape would not. *Choice.*
* **Week.** `@week` starts on **Monday** (ISO 8601's week, F5) and the key is the Monday's date or midnight. No parameter for the start day. A week-year or a week number is not a bucket key (it would need a different key type). Open question 6 asks whether `week` is in T2 at all.
* **Floor, not truncation toward zero.** Before 1970 the day of `-1` microsecond is 1969-12-31; the bucket uses floor division. A test row for it is named in section 12.
* **Cost.** The bucket is computed from the already parsed value, on the keys of rows that pass the filter; it is an addition per group key and does not touch a plan without `@`. *Estimate*, not measured.
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
* **Joins** (`docs/joins.md` 5.2): `--on a=b:timestamp` reads both columns as one type and matches on **the instant**; `07`/`7` style equality is replaced by `+02:00`/`Z` equality. `:date` against `:timestamp` is `args.bad-value` (no cross-type join, as `int` against `dec`); a user who wants to join a day to an instant has no bucket on a key in v1 (non-goal: a bucket on a join key, an open question for T3). The refusals are the `value.*` set with `detail.context: "join"` and `detail.input` (`jnkey.cho:11`).
* **`--report types`** (`report.cho:57` `classify`, `report.cho:36` `width`; `docs/numbers.md` 5.2): today a date column is `other` with `suggest` `none` (section 1, command 8). T3 counts, per column, the cells that are valid as `date`, `timestamp` (with offset) and `datetime` (without), and `suggest` becomes `:date`, `:timestamp` or `:datetime` **when every non-empty cell satisfies it** (the smallest declaration, the rule of N6), with `first_not_date_row`, ... as the other `first_not_*` columns do. It is **bounded** (a few counters per column; the cell is classified once, by the length and the separators first so that a non-date cell costs a few comparisons) and **never applies** (no auto type). The columns are appended after the existing ones, which changes the report's bytes and so the report's golden files and the `table.v2` schema: the **one place where an existing output changes**, flagged in section 15, risk 2.
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
| `where.syntax` (exists) | 2 | a literal that is not of the type: a date against a timestamp, a timestamp against a date, a bare `:timestamp` word with a bucket, `contains` on a typed column | `offset`, `expected` | the midnight rewriting for a date literal |
| `agg.bad-spec` (exists) | 2 | `sum:`/`mean:` of the types; a bucket finer than the type | | none |
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
| **T2** | `@UNIT` on a group key: `day`, `month`, `year`, `hour`, and `week` if the maintainer wants it | the bucket as a function of the instant, floor division, the week start, G-ROUND with buckets, mutants of the bucket | tool 150 to 250, tests 400 to 600 |
| **T3** | JSON lines out for the types, `--query` (`type_code`), joins on the types, `--report types`, MCP schema, the docs | G-CORPUS-EQ, G-JOIN, G-GEN, G-DOC, the report's changed bytes (risk 2) | tool 350 to 500, tests 800 to 1,200; **about J2's size** |

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
2. **`--report types` changes its bytes** in T3 (new columns): the schema, the goldens and every consumer that indexes the columns by position. Mitigation: append at the end, keep the old columns and their order, and let the report schema be regenerated; a flag to ask for the old shape is not proposed. If the maintainer wants the report byte-identical, the date suggestion can live in `--report dates`, a new report with its own columns (open question 8).
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
